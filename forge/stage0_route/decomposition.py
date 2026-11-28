"""D9 inventory reconciliation; every advisory remains controller-authenticated."""
from __future__ import annotations

import copy
import json

from .images import crop, iou
from .storage import RouteError, atomic_write, digest, encode, read_bytes, safe_path, snapshot


def route_key(obj):
    return ("instance", obj["instanceOf"]) if obj.get("instanceOf") else ("object", obj["objectId"])


def distinct_count(objects):
    return len({route_key(obj) for obj in objects})


def bounds_answer(record, object_id):
    b = record["answers"][f"object:{object_id}:box"]["box"]
    return [b["left"], b["top"], b["right"], b["bottom"]]


def reconcile_box(proposal, localization, choice=None):
    """Pure reconciliation, also used by the verifier on authenticated records."""
    if localization["status"] != "answered":
        return None, False, localization["reasonCode"]
    if localization["answers"][f"object:{proposal['objectId']}:visible"]["noul"] < .5:
        return None, False, "object-not-visible"
    located = bounds_answer(localization, proposal["objectId"])
    if iou(proposal["approxBox"], located) >= .3:
        if choice is not None:
            raise RouteError("unexpected-crop-choice")
        return located, False, None
    if choice is None:
        return located, True, "decomposition-crop-choice-required"
    if choice["status"] == "answered":
        selected = choice["answers"]["selection"]["choice"]
        if selected not in {"model-crop", "jev-crop"}:
            raise RouteError("invalid-crop-choice")
        return (proposal["approxBox"] if selected == "model-crop" else located), False, None
    # An uncertain Jev is never replaced by a model preference. Keep its box for
    # inspection but suspend promotion; auth/evidence failures retain their reason.
    reason = "decomposition-low-confidence" if choice["reasonCode"] in {
        "choice-uncertain", "none-fits"} else choice["reasonCode"]
    return located, True, reason


def row_for(router, obj, round_number, common, prior):
    semantic = digest({"object": obj, **common})
    old = prior.get(obj["objectId"])
    revision = old["revision"] + (old["inputsDigest"] != semantic) if old else 1
    return {"objectId": obj["objectId"], "revision": revision, "inputsDigest": semantic,
            "round": round_number, "boxSha256": digest(obj["approxBox"]), "eligible": [],
            "rejected": [], "consultations": [], "decider": None, "status": "pending",
            "selected": [], "execution": {"mode": "single", "n": 1}}


def crop_context(router, proposal, row, located, decoded, base_bindings, request):
    model_binding, model_transform = crop(router.workspace, decoded, proposal["approxBox"])
    jev_binding, jev_transform = crop(router.workspace, decoded, located)
    model_binding = dict(model_binding, role="model-crop")
    jev_binding = dict(jev_binding, role="jev-crop")
    choices = {"model-crop": {"box": proposal["approxBox"], "transform": model_transform},
               "jev-crop": {"box": located, "transform": jev_transform}}
    context = router._context(proposal, row["revision"],
        [{"id": key, "box": value["box"]} for key, value in choices.items()],
        base_bindings + [model_binding, jev_binding, snapshot(router.workspace, "crop-choices", choices)],
        request=request)
    context["subject"]["passId"] = "crop-choice"
    context["observations"]["images"] = [{"role": key, "asset": key} for key in choices]
    context["requirements"]["cropChoice"] = choices
    return context


def _next_proposals(router, decision, initial, triggers):
    """Durably publish the exact host response before admission; never replay the callback."""
    router._second_proposal_source()
    first = decision["proposalRounds"][0]
    request = {"kind": "img2.decomposition-proposal-request", "version": 1, "runId": router.run_id,
               "round": 2, "baseInputsDigest": decision["baseInputsDigest"],
               "imageSha256": decision["imageSha256"], "firstRoundSha256": first["proposal"]["sha256"],
               "triggerDecisionIds": triggers, "objects": copy.deepcopy(initial)}
    request_binding = snapshot(router.workspace, "proposal-request", request)
    request_digest = request_binding["sha256"]
    result_path = f".img2threejs/route/proposal-result-{request_digest}.json"
    attempt_path = f".img2threejs/route/proposal-attempt-{request_digest}.json"
    if safe_path(router.workspace, result_path).exists():
        result = json.loads(read_bytes(router.workspace, result_path))
        if result.get("request") != request_binding:
            raise RouteError("proposal-request-mismatch")
        proposal_binding = result["proposal"]
        raw = read_bytes(router.workspace, proposal_binding["path"])
        if digest(json.loads(raw)) != proposal_binding["sha256"]:
            raise RouteError("stale-proposal-round")
        return json.loads(raw), request_binding, proposal_binding
    if safe_path(router.workspace, attempt_path).exists():
        raise RouteError("ambiguous-proposal-attempt")
    if not callable(router.second_proposals):
        raise RouteError("second-proposal-adapter-required")
    atomic_write(router.workspace, attempt_path, encode({"status": "pending", "request": request_binding}), immutable=True)
    try:
        response = router.second_proposals(copy.deepcopy(request))
    except Exception as exc:
        raise RouteError("second-proposal-error") from exc
    router._second_proposal_source()
    proposal_binding = snapshot(router.workspace, "object-proposals", response)
    atomic_write(router.workspace, result_path, encode({"request": request_binding, "proposal": proposal_binding}), immutable=True)
    return response, request_binding, proposal_binding


def decompose(router, decision, proposals, decoded, image_binding, request_binding,
              common, previous, *, explicit_2d=False):
    """Return all proposed rows and every retained placement; never call a third round."""
    prior = dict(previous.get("revisionIndex", {})) if previous else {}
    if previous:
        for old in previous["objects"]:
            prior[old["objectId"]] = {**prior.get(old["objectId"], {}), **old}
    prepared, retained = [], []
    current = proposals
    round_number = 1
    while True:
        current_round = decision["proposalRounds"][round_number - 1]
        try:
            router.controller.seal_route_round(route_revision=decision["revision"], round_number=round_number,
                proposal=current_round["proposal"], initial_inputs_digest=decision["baseInputsDigest"],
                parent_round_sha256=current_round["parentRoundSha256"],
                trigger_decision_ids=current_round["triggerDecisionIds"])
        except ValueError as exc:
            decision["decompositionBlocker"] = getattr(exc, "reason_code", "proposal-seal-rejected")
            break
        inventory = [{"id": item["objectId"], "label": item["label"], "role": item["role"],
                      "approxBox": item["approxBox"]} for item in [p for p, _ in prepared] + current]
        round_common = dict(common, localizationInventory=inventory)
        round_rows = []
        for proposal in current:
            row = row_for(router, proposal, round_number, round_common, prior)
            decision["objects"].append(row)
            prepared.append((copy.deepcopy(proposal), row))
            round_rows.append((proposal, row))
            decision["revisionIndex"][proposal["objectId"]] = {
                **decision["revisionIndex"].get(proposal["objectId"], {}),
                "revision": row["revision"], "inputsDigest": row["inputsDigest"]}
        router._persist(decision)
        triggers, incomplete = [], False
        for proposal, row in round_rows:
            obj = copy.deepcopy(proposal)
            original_binding = snapshot(router.workspace, "object-proposal", proposal)
            base_bindings = list(image_binding) + [request_binding, original_binding]
            if not explicit_2d:
                context = router._context(proposal, row["revision"], [],
                    base_bindings, request=decision["request"], decomposition=True,
                    inventory=[item for item, _ in prepared])
                record = router._consult(context, route_round={"routeRevision": decision["revision"], "roundNumber": round_number})
                row["consultations"].append(router._consultation(record, context))
                grant = record.get("consent")
                decision["consentSha256"] = grant["sha256"] if grant else None
                if record.get("unavailableReason") in {"not-installed", "login-declined", "consent-declined"}:
                    decision["jevSession"] = "unavailable:" + record["unavailableReason"]
                if record["status"] != "answered":
                    row.update(status="request-input", reasonCode=record["reasonCode"])
                    if "loginCommand" in record:
                        row["loginCommand"] = record["loginCommand"]
                    incomplete = True
                    continue
                if record["answers"]["missingObjects"]["noul"] >= .7:
                    triggers.append(record["recordId"])
                selected_box, low_confidence, reason = reconcile_box(proposal, record)
                if reason == "object-not-visible":
                    row.update(status="dropped", reasonCode=reason)
                    continue
                choice = None
                if low_confidence:
                    choice_context = crop_context(router, proposal, row, selected_box, decoded, base_bindings, decision["request"])
                    router._persist(decision)
                    choice = router._consult(choice_context, route_round={"routeRevision": decision["revision"], "roundNumber": round_number})
                    row["consultations"].append(router._consultation(choice, choice_context))
                    selected_box, low_confidence, reason = reconcile_box(proposal, record, choice)
                    if "loginCommand" in choice:
                        row["loginCommand"] = choice["loginCommand"]
                obj.update(approxBox=selected_box, decomposer=record["decider"],
                    confidence=record["answers"][f"object:{obj['objectId']}:box"]["confidence"],
                    lowConfidence=low_confidence)
                row["reconciliation"] = {"modelBox": proposal["approxBox"],
                    "jevBox": bounds_answer(record, obj["objectId"]), "selectedBox": selected_box,
                    "lowConfidence": low_confidence}
                if reason:
                    row.update(status="request-input", reasonCode=reason)
                    incomplete = True
                # Explicit instances remain separately keyed placements, even if their boxes overlap.
                duplicate = next((other for other, _ in retained if not obj.get("instanceOf")
                    and not other.get("instanceOf") and other["label"] == obj["label"]
                    and iou(other["approxBox"], obj["approxBox"]) > .6), None)
                if duplicate is not None:
                    row.update(status="merged", mergedInto=duplicate["objectId"], reasonCode="duplicate-object")
                    continue
            else:
                obj.update(decomposer="explicit-2d", confidence=None, lowConfidence=False)
            crop_binding, transform = crop(router.workspace, decoded, obj["approxBox"])
            row.update(boxSha256=digest(obj["approxBox"]), reference=crop_binding, cropTransform=transform)
            retained.append((obj, row))
        decision["proposalRounds"][round_number - 1]["missingObjectDecisionIds"] = triggers
        if explicit_2d or not triggers:
            break
        if round_number == 2:
            decision["decompositionBlocker"] = "missing-objects-after-round-limit"
            break
        if incomplete:
            decision["decompositionBlocker"] = "decomposition-incomplete"
            break
        try:
            response, request_ref, proposal_ref = _next_proposals(router, decision, proposals, triggers)
            decision["proposalRounds"].append({"round": 2, "proposal": proposal_ref,
                "parentRoundSha256": decision["proposalRounds"][0]["proposal"]["sha256"],
                "request": request_ref, "triggerDecisionIds": list(triggers), "missingObjectDecisionIds": []})
            current = router._objects(response, allow_empty=True)
            if not current:
                raise RouteError("second-proposals-empty")
            all_objects = [p for p, _ in prepared] + current
            if len({p["objectId"] for p in all_objects}) != len(all_objects):
                raise RouteError("duplicate-round-object-id")
            if distinct_count(all_objects) > 12:
                for proposal in current:
                    row = row_for(router, proposal, 2, common, prior)
                    row.update(status="request-input", reasonCode="object-prioritisation-required")
                    decision["objects"].append(row)
                    prepared.append((copy.deepcopy(proposal), row))
                raise RouteError("object-prioritisation-required")
        except (RouteError, ValueError, TypeError) as exc:
            decision["decompositionBlocker"] = getattr(exc, "reason_code", "invalid-second-proposal-round")
            break
        round_number = 2
    return prepared, retained
