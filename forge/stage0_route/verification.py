"""Revalidate all proposal rounds and reconciliations, without provider calls.

Aggregate journal attestation is still required before execution authorization.
"""
from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
import json

from route_eligibility import evaluate_candidate
from .decomposition import bounds_answer, distinct_count, reconcile_box, route_key
from .images import admit_reference, crop, decode, iou
from .storage import RouteError, digest, encode, read_bytes
from .route import route_identity


def _read(router, ref):
    raw = read_bytes(router.workspace, ref["path"])
    if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise RouteError("stale-routing-binding")
    return json.loads(raw)


def _bindings(context, role):
    return [b for b in context["bindings"] if b["role"] == role]


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if type(value) in {list, tuple}:
        return [_plain(item) for item in value]
    return value


def _consultations(router, row, verify):
    result = {}
    for consultation in row["consultations"]:
        context = _read(router, consultation["contextRef"])
        record = json.loads(read_bytes(router.workspace, consultation["decisionRef"]))
        step = context["subject"]["passId"]
        if step not in {"decomposition", "crop-choice", "routing"} or step in result:
            raise RouteError("unexpected-consultation")
        revision = row["routingRevision"] if step == "routing" else row["revision"]
        if (context["group"] != "routing" or context["subject"] != {
                "runId": router.run_id, "objectId": row["objectId"], "revision": revision, "passId": step}
                or consultation["receiptSha256"] != record["recordId"]):
            raise RouteError("decision-subject-mismatch")
        record = verify(record, context)
        handles = row.get("candidateHandles") if step == "routing" else None
        if router._consultation(record, context, handles) != consultation:
            raise RouteError("consultation-mismatch")
        result[step] = (record, context)
    return result


def verify_route(router, decision_ref, verify, discovered):
    raw = read_bytes(router.workspace, decision_ref)
    if decision_ref != f".img2threejs/route/decision-{hashlib.sha256(raw).hexdigest()}.json":
        raise RouteError("decision-integrity-failed")
    decision = json.loads(raw)
    if (encode(decision) != raw or decision.get("kind") != "img2.route-decision" or decision.get("version") != 1
            or decision.get("runId") != router.run_id or decision.get("phase") != "decided"
            or decision.get("executionAuthorized") is not False or decision.get("decompositionBlocker")):
        raise RouteError("decision-not-decided")
    inputs = _read(router, {"path": decision["inputsRef"], "sha256": decision["inputsDigest"]})
    proposals = router._objects(_read(router, {"path": decision["proposalRef"], "sha256": decision["proposalSha256"]}))
    available = {c["id"]: c for c in discovered["candidates"]}
    facts = [{k: c[k] for k in ("id", "resolvedSha", "providerStatus", "dependencies", "blockingGateProduced")}
             for c in available.values()]
    if (inputs["proposals"] != proposals or inputs["request"] != decision["request"]
            or inputs["reference"] != decision["imageSha256"] or inputs["candidates"] != facts
            or decision["versions"]["cards"] != {k: c["binding"]["sha256"] for k, c in available.items()}
            or inputs["policyVersion"] != decision["policyVersion"]):
        raise RouteError("stale-routing-inputs")
    rounds = decision["proposalRounds"]
    if (len(rounds) not in {1, 2} or inputs["proposalRounds"] != rounds
            or [r["round"] for r in rounds] != list(range(1, len(rounds) + 1))
            or rounds[0]["parentRoundSha256"] is not None):
        raise RouteError("invalid-proposal-rounds")
    round_objects = [router._objects(_read(router, r["proposal"])) for r in rounds]
    if [p for group in round_objects for p in group] != proposals or distinct_count(proposals) > 12:
        raise RouteError("inventory-completeness-unverified")
    initial_inputs = dict(inputs, proposals=round_objects[0])
    initial_inputs.pop("proposalRounds")
    if digest(initial_inputs) != decision["baseInputsDigest"]:
        raise RouteError("stale-initial-inventory")
    if (len(rounds) == 2 and rounds[1]["parentRoundSha256"] != rounds[0]["proposal"]["sha256"]):
        raise RouteError("invalid-proposal-rounds")
    seals = _plain(verify.route_rounds(decision["revision"]))
    if len(seals) != len(rounds):
        raise RouteError("proposal-round-seal-mismatch")
    previous_sequence = -1
    for index, round_record in enumerate(rounds):
        sequence = seals[index].get("sealedAtSequence")
        expected_seal = {"runId": router.run_id, "routeRevision": decision["revision"],
            "roundNumber": round_record["round"], "proposal": round_record["proposal"],
            "initialInputsDigest": decision["baseInputsDigest"], "parentRoundSha256": round_record["parentRoundSha256"],
            "triggerDecisionIds": round_record["triggerDecisionIds"],
            "proposalValue": _read(router, round_record["proposal"]), "initialInputs": initial_inputs,
            "sealedAtSequence": sequence}
        if (type(sequence) is not int or sequence <= previous_sequence or seals[index] != expected_seal):
            raise RouteError("proposal-round-seal-mismatch")
        previous_sequence = sequence
    rows = decision["objects"]
    if [r["objectId"] for r in rows] != [p["objectId"] for p in proposals]:
        raise RouteError("inventory-completeness-unverified")
    row_by_id = {r["objectId"]: r for r in rows}
    if len(row_by_id) != len(rows):
        raise RouteError("duplicate-object")
    records = {r["objectId"]: _consultations(router, r, verify) for r in rows}
    round_of = {p["objectId"]: n for n, objects in enumerate(round_objects, 1) for p in objects}
    links = _plain(verify.route_round_links(decision["revision"]))
    for row in rows:
        for step, (record, context) in records[row["objectId"]].items():
            bound = _bindings(context, "object-proposal")
            expected_link = {"runId": router.run_id, "routeRevision": decision["revision"],
                "roundNumber": row["routingRound"] if step == "routing" else row["round"],
                "recordId": record["recordId"], "contextDigest": record["contextDigest"],
                "objectIds": [_read(router, b)["objectId"] for b in bound],
                "proposalShas": [b["sha256"] for b in bound]}
            if expected_link not in links:
                raise RouteError("unlinked-routing-consultation")
    expected_output = decision["request"]["expectedOutput"]
    if decision["skipComposition"] != (expected_output == "2d"):
        raise RouteError("output-branch-mismatch")
    for number, round_record in enumerate(rounds, 1):
        triggers = []
        for proposal in round_objects[number - 1]:
            localization = records[proposal["objectId"]].get("decomposition")
            if localization and localization[0]["answers"]["missingObjects"]["noul"] >= .7:
                triggers.append(localization[0]["recordId"])
        if triggers != round_record["missingObjectDecisionIds"]:
            raise RouteError("missing-object-trigger-mismatch")
        if (number == 1 and bool(triggers) != (len(rounds) == 2)) or (number == 2 and triggers):
            raise RouteError("incomplete-decomposition-rounds")
    if len(rounds) == 2:
        request = _read(router, rounds[1]["request"])
        expected_request = {"kind": "img2.decomposition-proposal-request", "version": 1, "runId": router.run_id,
            "round": 2, "baseInputsDigest": decision["baseInputsDigest"], "imageSha256": decision["imageSha256"],
            "firstRoundSha256": rounds[0]["proposal"]["sha256"], "triggerDecisionIds": rounds[0]["missingObjectDecisionIds"],
            "objects": round_objects[0]}
        if request != expected_request:
            raise RouteError("proposal-request-mismatch")
    inventory = _read(router, {"path": decision["inventoryRef"], "sha256": decision["inventorySha256"]})
    entries = {o["objectId"]: o for o in inventory["objects"]}
    if len(entries) != len(inventory["objects"]) or inventory["imageSha256"] != decision["imageSha256"]:
        raise RouteError("inventory-object-mismatch")
    retained, groups, eligible_by_id = [], {}, {}
    decoded = None
    common_reference = None
    for proposal, row in zip(proposals, rows):
        object_id = proposal["objectId"]
        consultations = records[object_id]
        if row["round"] != round_of[object_id]:
            raise RouteError("proposal-round-mismatch")
        for step, (_, context) in consultations.items():
            references = _bindings(context, "reference")
            requests = _bindings(context, "route-request")
            if (len(references) != 1 or references[0]["sha256"] != decision["imageSha256"]
                    or len(requests) != 1 or _read(router, requests[0]) != decision["request"]
                    or context["requirements"]["request"] != decision["request"]
                    or context["policyVersion"] != inputs["policyVersion"]
                    or context["questionSetVersion"] != inputs["questionSetVersion"]
                    or context["requestedModel"] != inputs["requestedModel"]):
                raise RouteError("context-request-mismatch")
            if context["observations"].get("label") != proposal["label"]:
                raise RouteError("crop-choice-mismatch" if step == "crop-choice" else "context-target-mismatch")
            common_reference = references[0] if common_reference is None else common_reference
            if digest(consultations[step][0]["consent"]) != inputs["consentSnapshotSha256"]:
                raise RouteError("consent-changed")
            if references[0] != common_reference:
                raise RouteError("reference-mismatch")
            if decoded is None:
                decoded, normalized = admit_reference(router.workspace, common_reference,
                    read_bytes(router.workspace, common_reference["path"]))
                if normalized != decision["referenceNormalization"] or inputs["referenceNormalization"] != normalized:
                    raise RouteError("reference-normalization-mismatch")
            normalization_bindings = _bindings(context, "reference-normalization")
            if (_bindings(context, "upright-reference") != [decision["referenceNormalization"]["uprightReference"]]
                    or len(normalization_bindings) != 1
                    or _read(router, normalization_bindings[0]) != decision["referenceNormalization"]):
                raise RouteError("reference-normalization-mismatch")
            if step != "routing":
                bound_proposals = _bindings(context, "object-proposal")
                if len(bound_proposals) != 1 or _read(router, bound_proposals[0]) != proposal:
                    raise RouteError("proposal-mismatch")
        if expected_output == "3d":
            if "decomposition" not in consultations:
                raise RouteError("decomposition-not-verified")
            localization, context = consultations["decomposition"]
            expected_decomposition = {"image": "reference-image", "objects": [{
                "id": object_id, "label": proposal["label"], "approxBox": proposal["approxBox"]}]}
            if context["requirements"].get("decomposition") != expected_decomposition:
                raise RouteError("proposal-mismatch")
            choice = consultations.get("crop-choice")
            selected_box, low_confidence, reason = reconcile_box(proposal, localization, choice[0] if choice else None)
            if reason == "object-not-visible":
                if row["status"] != "dropped" or set(consultations) != {"decomposition"}:
                    raise RouteError("decomposition-mismatch")
                continue
            if reason or low_confidence:
                raise RouteError("decomposition-low-confidence")
            reconciliation = {"modelBox": proposal["approxBox"], "jevBox": bounds_answer(localization, object_id),
                              "selectedBox": selected_box, "lowConfidence": False}
            if row.get("reconciliation") != reconciliation:
                raise RouteError("decomposition-mismatch")
            if choice:
                choice_context = choice[1]
                expected_choices = [{"id": "model-crop", "box": proposal["approxBox"]},
                                    {"id": "jev-crop", "box": bounds_answer(localization, object_id)}]
                if choice_context["candidates"] != expected_choices:
                    raise RouteError("crop-choice-mismatch")
                if decoded is None:
                    decoded = decode(read_bytes(router.workspace, common_reference["path"]))
                expected_options = {}
                for candidate in expected_choices:
                    actual, transform = crop(router.workspace, decoded, candidate["box"])
                    actual["role"] = candidate["id"]
                    if _bindings(choice_context, candidate["id"]) != [actual]:
                        raise RouteError("crop-choice-mismatch")
                    expected_options[candidate["id"]] = {"box": candidate["box"], "transform": transform}
                if choice_context["requirements"].get("cropChoice") != expected_options:
                    raise RouteError("crop-choice-mismatch")
            duplicate = next((p for p, b in retained if not proposal.get("instanceOf") and not p.get("instanceOf")
                and p["label"] == proposal["label"] and iou(b, selected_box) > .6), None)
            if duplicate is not None:
                if row["status"] != "merged" or row.get("mergedInto") != duplicate["objectId"] or "routing" in consultations:
                    raise RouteError("decomposition-merge-mismatch")
                continue
            decomposer, confidence = localization["decider"], localization["answers"][f"object:{object_id}:box"]["confidence"]
        else:
            if len(proposals) != 1 or object_id != "whole-image" or set(consultations) != {"routing"}:
                raise RouteError("explicit-2d-mismatch")
            selected_box, decomposer, confidence = [0, 0, 1, 1], "explicit-2d", None
        retained.append((proposal, selected_box))
        entry = entries.get(object_id)
        if (entry is None or row["status"] != "resolved" or entry["box"] != selected_box
                or entry["role"] != proposal["role"] or entry["label"] != proposal["label"]
                or entry.get("instanceOf") != proposal.get("instanceOf") or entry.get("lowConfidence") is not False
                or entry["decomposer"] != decomposer or entry["confidence"] != confidence):
            raise RouteError("inventory-object-mismatch")
        if decoded is None:
            decoded = decode(read_bytes(router.workspace, common_reference["path"]))
        actual_crop, transform = crop(router.workspace, decoded, selected_box)
        if (row["reference"] != actual_crop or entry["reference"] != actual_crop or row["boxSha256"] != digest(selected_box)
                or row["cropTransform"] != transform or entry["cropTransform"] != transform):
            raise RouteError("reference-crop-mismatch")
        eligible = []
        for candidate in available.values():
            result = evaluate_candidate(candidate["card"], expected_output=expected_output,
                roles=[proposal["role"]], intent=decision["request"]["intent"], observations=proposal.get("observations", {}),
                provider_status=candidate["providerStatus"], dependencies=candidate["dependencies"],
                blocking_gate_produced=candidate["blockingGateProduced"])
            explicit = decision["request"]["explicitRoute"]
            if (result["eligible"] and (explicit is None or explicit in {candidate["id"], candidate["provider"]})
                    and (expected_output != "2d" or candidate["provider"] == "pixi2d")):
                eligible.append(candidate["id"])
        if row["eligible"] != eligible:
            raise RouteError("eligibility-mismatch")
        eligible_by_id[object_id] = eligible
        groups.setdefault(route_key(proposal), []).append(proposal)
    if set(entries) != {p["objectId"] for p, _ in retained} or not retained:
        raise RouteError("inventory-completeness-unverified")
    for members in groups.values():
        leader_id = members[0]["objectId"]
        leader = row_by_id[leader_id]
        record, context = records[leader_id].get("routing", (None, None))
        if record is None or record["status"] != "answered":
            raise RouteError("route-execution-unresolved")
        eligible = [k for k in available if all(k in eligible_by_id[m["objectId"]] for m in members)]
        handles = {"candidate-" + hashlib.sha256(k.encode()).hexdigest(): k for k in eligible}
        candidates = [{"id": h, "routeId": route_identity(available[k]), "outputKind": available[k]["card"]["outputKind"],
                       "display": available[k]["card"].get("display", {})} for h, k in handles.items()]
        member_data = [{"objectId": m["objectId"], "proposal": m, "box": entries[m["objectId"]]["box"],
                        "reference": entries[m["objectId"]]["reference"]} for m in members]
        member_bindings = _bindings(context, "instance-members")
        expected_instances = [{"id": m["objectId"], "role": m["role"], "box": entries[m["objectId"]]["box"],
            "observations": m.get("observations", {}), "instanceOf": m.get("instanceOf")} for m in members]
        if (context["candidates"] != candidates or context["observations"].get("instances") != expected_instances
                or len(member_bindings) != 1 or _read(router, member_bindings[0]) != member_data
                or [_read(router, b) for b in _bindings(context, "object-proposal")] != members):
            raise RouteError("instance-group-mismatch")
        expected_crops = list({(b["role"], b["path"]): b for b in
                               (entries[m["objectId"]]["reference"] for m in members)}.values())
        expected_observations = copy.deepcopy(members[0].get("observations", {}))
        expected_observations.update(role=members[0]["role"], label=members[0]["label"],
                                     box=entries[leader_id]["box"], instances=expected_instances,
                                     images=[{"role": "object-crop", "asset": "object-crop"}])
        if _bindings(context, "object-crop") != [dict(entries[leader_id]["reference"], role="object-crop")]:
            raise RouteError("routing-image-mismatch")
        if _bindings(context, "reference-crop") != expected_crops or context["observations"] != expected_observations:
            raise RouteError("instance-group-mismatch")
        card_bindings = _bindings(context, "route-card")
        if card_bindings != [available[k]["binding"] for k in eligible]:
            raise RouteError("stale-route-candidates")
        hb = _bindings(context, "candidate-handles")
        if len(hb) != 1 or _read(router, hb[0]) != handles:
            raise RouteError("candidate-mapping-mismatch")
        selection = record["answers"]["selection"]
        selected = handles[selection["choice"]]
        probabilities = sorted(selection["probabilities"].values(), reverse=True)
        if (available[selected]["card"]["costClass"] in {"paid", "licensed"}
                or (record["decider"].startswith("jev:") and len(probabilities) > 1 and probabilities[0] - probabilities[1] < .25)):
            raise RouteError("route-execution-unresolved")
        for member in members:
            object_id = member["objectId"]
            row, entry = row_by_id[object_id], entries[object_id]
            if (row.get("routeObjectId") != leader_id or entry.get("routeObjectId") != leader_id
                    or entry.get("artifactObjectId") != leader_id
                    or row.get("candidateHandles") != handles or row.get("groupEligible") != eligible
                    or row["selected"] != [selected] or entry["ownerRoute"] != selected
                    or row.get("routeReceiptSha256") != record["recordId"]
                    or row["routingRevision"] != leader["routingRevision"]
                    or row["routingRound"] != max(row_by_id[m["objectId"]]["round"] for m in members)
                    or row["execution"].get("placementOnly") != (object_id != leader_id)
                    or (object_id != leader_id and "routing" in records[object_id])):
                raise RouteError("route-selection-mismatch")
    return copy.deepcopy(decision)
