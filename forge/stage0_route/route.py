#!/usr/bin/env python3
"""Decision-only stage zero. No child initialisation, scene execution or acceptance claims.

Production hosts inject trusted provider/fallback/consent adapters. The standalone
CLI discovers real providers but deliberately requests input without those adapters.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys

FORGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORGE / "_shared"))
if __package__ in {None, ""}:
    sys.path.insert(0, str(FORGE))
    __package__ = "stage0_route"

from decision_control import DecisionController, DecisionError
from route_eligibility import evaluate_candidate
from .discovery import Discovery, read_only_probes
from .decomposition import decompose, distinct_count, route_key
from .images import admit_reference, box
from .storage import RouteError, atomic_write, binding, digest, encode, read_bytes, safe_path, snapshot

_POLICY = "route-quality-policy-v1"
_QUESTIONS = "route-questions-v3"
_ID = re.compile(r"[a-z][a-z0-9-]{0,95}\Z")
_ROLES = {"scene-container", "subject", "prop", "set-dressing"}
_GENERIC_CARD = {"kind": "img2.route-card", "version": 1, "outputKind": "3d-object",
                 "costClass": "local", "requires": {}, "notFor": {}, "deps": [],
                 "display": {"appliesTo": "Base procedural Three.js object workflow.", "notFor": "Explicit 2D."}}


def route_identity(candidate):
    if candidate["provider"] == "base" and candidate["id"] == "base:generic":
        return "base-generic"
    if candidate["provider"] in {"interior", "character", "cs2", "pixi2d"}:
        return candidate["provider"]
    raise RouteError("unapproved-route-identity")


class Router:
    def __init__(self, workspace, run_id, *, discovery=None, provider=None, fallback=None,
                 consent=None, controller=None, requested_model="jev-1.14.0", second_proposals=None,
                 second_proposal_binding=None, provider_factory=None):
        self.workspace = Path(workspace).resolve(strict=True)
        self.controller = controller or DecisionController(self.workspace, run_id)
        if self.controller.workspace != self.workspace or self.controller.run_id != run_id:
            raise RouteError("controller-subject-mismatch")
        self.run_id = run_id
        self.provider_factory = provider_factory
        self.discovery = discovery or Discovery(self.controller)
        self.provider = provider
        self.fallback = fallback
        self.consent = consent
        self.second_proposals = second_proposals
        self.second_proposal_binding = copy.deepcopy(second_proposal_binding)
        self._second_proposal_source()
        self.requested_model = requested_model
        self.latest = f".img2threejs/route/latest-{run_id}.json"
        safe_path(self.workspace, self.latest)

    def _second_proposal_source(self):
        source = self.second_proposal_binding
        if source is None:
            return None
        if (type(source) is not dict or set(source) != {"role", "path", "sha256"}
                or source["role"] != "second-proposal-source"
                or type(source["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"])):
            raise RouteError("invalid-second-proposal-binding")
        if binding(self.workspace, "second-proposal-source", source["path"]) != source:
            raise RouteError("stale-second-proposal-source")
        return copy.deepcopy(source)

    def _previous(self):
        path = safe_path(self.workspace, self.latest)
        if not path.exists():
            return None
        pointer = json.loads(read_bytes(self.workspace, self.latest))
        data = read_bytes(self.workspace, pointer["path"])
        if hashlib.sha256(data).hexdigest() != pointer["sha256"]:
            raise RouteError("decision-integrity-failed")
        value = json.loads(data)
        if (value.get("kind") != "img2.route-decision" or value.get("version") != 1
                or value.get("runId") != self.run_id):
            raise RouteError("decision-subject-mismatch")
        return value

    def _persist(self, decision):
        # Atomic, content-addressed progress only, NOT execution authority. Final
        # state promotion must use verify_decision inside workspace_transaction.
        if self.controller.routing_authority is not None:
            decision.update(self.controller.routing_authority.metadata())
            decision["jevSession"] = "not-used:no-account"
        data = encode(decision)
        sha = hashlib.sha256(data).hexdigest()
        relative = f".img2threejs/route/decision-{sha}.json"
        atomic_write(self.workspace, relative, data, immutable=True)
        pointer = {"path": relative, "sha256": sha}
        atomic_write(self.workspace, self.latest, encode(pointer))
        return {"decision": copy.deepcopy(decision), "decisionRef": relative, "sha256": sha}

    @staticmethod
    def _objects(value, *, allow_empty=False):
        if (type(value) is not dict or set(value) != {"kind", "version", "objects"}
                or value.get("kind") != "img2.object-proposals" or value.get("version") != 1):
            raise RouteError("object-proposals-required")
        objects = value.get("objects")
        if type(objects) is not list or (not objects and not allow_empty):
            raise RouteError("objects-required")
        seen = set()
        clean = []
        for obj in objects:
            if (type(obj) is not dict or set(obj) - {"objectId", "label", "role", "approxBox", "observations", "instanceOf", "classId"}
                    or not {"objectId", "label", "role", "approxBox"} <= set(obj)
                    or type(obj["objectId"]) is not str or not _ID.fullmatch(obj["objectId"])
                    or type(obj["label"]) is not str or not _ID.fullmatch(obj["label"])
                    or obj["objectId"] in seen or type(obj["role"]) is not str or obj["role"] not in _ROLES
                    or type(obj.get("observations", {})) is not dict):
                raise RouteError("invalid-object-proposals")
            if (obj.get("instanceOf") is not None and (type(obj["instanceOf"]) is not str
                    or not _ID.fullmatch(obj["instanceOf"]))):
                raise RouteError("invalid-instance-group")
            if "classId" in obj and (type(obj["classId"]) is not str or not _ID.fullmatch(obj["classId"])):
                raise RouteError("invalid-object-class")
            seen.add(obj["objectId"])
            clean.append(dict(obj, approxBox=box(obj["approxBox"])))
        if distinct_count(clean) > 12:
            raise RouteError("object-prioritisation-required")
        return clean

    def _context(self, obj, revision, candidates, bindings, *, request, decomposition=False, inventory=None):
        label = obj.get("label")
        if type(label) is not str or not _ID.fullmatch(label):
            raise RouteError("invalid-object-proposals")
        requirements = {"criticalFeatures": [], "requiredViews": [], "request": copy.deepcopy(request)}
        observations = copy.deepcopy(obj.get("observations", {}))
        observations["role"] = obj["role"]
        observations["box"] = obj["approxBox"]
        observations["label"] = label
        if decomposition:
            observations["images"] = [{"role": "upright-reference", "asset": "reference-image"}]
            requirements["decomposition"] = {"image": "reference-image", "objects": [
                {"id": obj["objectId"], "label": obj["label"], "approxBox": obj["approxBox"]}],
                "inventory": [{"id": item["objectId"], "label": item["label"], "role": item["role"],
                               "approxBox": item["approxBox"]} for item in inventory]}
        return {"kind": "img2.route-context", "version": 1, "group": "routing",
                "subject": {"runId": self.run_id, "objectId": obj["objectId"], "revision": revision,
                            "passId": "decomposition" if decomposition else "routing"},
                "bindings": bindings, "candidates": candidates, "observations": observations,
                "requirements": requirements, "policyVersion": _POLICY,
                "questionSetVersion": _QUESTIONS, "requestedModel": self.requested_model}

    def _consult(self, context, *, route_round):
        provider = self.provider_factory.provider(context) if self.provider_factory is not None else self.provider
        with read_only_probes(self.discovery):
            record = self.controller.consult(context, provider=provider, fallback=self.fallback,
                availability=self.discovery.availability, consent=self.consent, route_round=route_round)
            # consume also invokes the live consent callback under the same
            # non-reentrant lock, after consult has released its own acquisition.
            if record.get("model") is not None:
                self.controller.consume(record, context, consent=self.consent)
        return record

    def _consultation(self, record, context, handles=None):
        selection = record.get("answers", {}).get("selection")
        fits = record.get("answers", {}).get("anyFits")
        answer = None
        if selection is not None:
            answer = {"choice": selection["choice"], "probabilities": selection["probabilities"],
                      "confidence": selection["confidence"]}
            if fits is not None:
                answer["anyFitsNoul"] = fits["noul"]
            if handles:
                answer["choice"] = handles[answer["choice"]]
                answer["probabilities"] = {handles[k]: v for k, v in answer["probabilities"].items()}
        metadata = ({key: record[key] for key in ("decisionMode", "attestationId", "presentedAsJev")}
                    if record.get("decisionMode") == "no-account" else {})
        return {**metadata, "requestDigest": record["contextDigest"], "decider": record["decider"],
                "outcome": record["outcome"], "reasonCode": record["reasonCode"],
                "questionSetVersion": record["questionSetVersion"], "answer": answer,
                "receiptSha256": record["recordId"], "status": "completed" if record.get("model") else "pending",
                "contextRef": snapshot(self.workspace, "decision-context", context),
                "decisionRef": f".img2threejs/decisions/routing/{record['recordId']}.json"}

    def _generic(self):
        # Intrinsic base route, not a fictitious installed provider. Its actual local
        # generator and gate runners must exist before it can enter the eligible set.
        producers = [FORGE / "stage3_build" / "generate_threejs_factory.py",
                     FORGE / "stage3_build" / "run_gates.py",
                     FORGE / "stage4_review" / "geometry_integrity.py"]
        present = all(path.is_file() and not path.is_symlink() for path in producers)
        pinned = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in producers if path.is_file()}
        evidence = snapshot(self.workspace, "route-card", {"provider": "base", "card": _GENERIC_CARD,
                                                           "producers": pinned})
        return {"id": "base:generic", "provider": "base", "resolvedSha": evidence["sha256"],
                "card": _GENERIC_CARD, "binding": evidence, "providerStatus": "answered",
                "dependencies": {}, "blockingGateProduced": present}

    def route(self, *, image, objects=None, expected_output="3d", intent=None, explicit_route=None):
        intent = [] if intent is None else intent
        if (expected_output not in {"2d", "3d"} or type(intent) is not list
                or any(type(item) is not str or not _ID.fullmatch(item) for item in intent)
                or (explicit_route is not None and type(explicit_route) is not str)):
            raise RouteError("invalid-request")
        # Discovery/consultations verify the shared lease; progress is not authority.
        previous = self._previous()
        second_source = self._second_proposal_source()
        image_binding = binding(self.workspace, "reference", image)
        candidates, rejected = self.discovery.enumerate()
        candidates.append(self._generic())
        request = {"expectedOutput": expected_output, "intent": intent, "explicitRoute": explicit_route}
        reason = None
        normalization = None
        try:
            decoded, normalization = admit_reference(self.workspace, image_binding, read_bytes(self.workspace, image))
            if expected_output == "2d":
                proposals = [{"objectId": "whole-image", "label": "whole-image", "role": "subject",
                              "approxBox": [0, 0, 1, 1], "observations": {}}]
            else:
                proposals = self._objects(objects)
        except RouteError as exc:
            proposals, decoded, reason = [], None, exc.reason_code
        inventory = {"kind": "img2.object-inventory", "version": 1,
                     "imageSha256": image_binding["sha256"], "objects": []}
        try:
            consent_snapshot = self.consent() if callable(self.consent) else None
            consent_snapshot_digest = digest(consent_snapshot)
        except Exception:
            consent_snapshot_digest = digest(None)
        inputs = {"reference": image_binding["sha256"], "request": request, "proposals": proposals,
                  "referenceNormalization": normalization,
                  "consentSnapshotSha256": consent_snapshot_digest,
                  "candidates": [{k: c[k] for k in ("id", "resolvedSha", "providerStatus", "dependencies", "blockingGateProduced")}
                                 for c in candidates], "rejected": rejected, "policyVersion": _POLICY,
                  "questionSetVersion": _QUESTIONS, "requestedModel": self.requested_model}
        if second_source is not None:
            inputs["secondProposalBinding"] = second_source
        inputs_digest = digest(inputs)
        inputs_binding = snapshot(self.workspace, "routing-inputs", inputs)
        proposal_binding = snapshot(self.workspace, "object-proposals",
            objects if expected_output == "3d" else {"kind": "img2.object-proposals", "version": 1, "objects": proposals})
        base_inputs_digest = inputs_digest
        revision = (previous["revision"] + (previous.get("baseInputsDigest", previous["inputsDigest"]) != inputs_digest)) if previous else 1
        decision = {"kind": "img2.route-decision", "version": 1, "runId": self.run_id,
                    "revision": revision, "phase": "routing", "suspendedAt": None,
                    "imageSha256": image_binding["sha256"], "request": request, "inputsDigest": inputs_digest,
                    "inputsRef": inputs_binding["path"], "proposalRef": proposal_binding["path"],
                    "proposalSha256": proposal_binding["sha256"],
                    "referenceNormalization": normalization,
                    "baseInputsDigest": base_inputs_digest,
                    "revisionIndex": copy.deepcopy(previous.get("revisionIndex", {})) if previous else {},
                    "proposalRounds": [{"round": 1, "proposal": proposal_binding, "parentRoundSha256": None,
                                        "triggerDecisionIds": []}],
                    "inventorySha256": None, "objects": [], "jevSession": "healthy",
                    "probes": list(self.discovery.probes), "policyVersion": _POLICY,
                    "versions": {"harness": self.discovery.version, "baseSkill": "2.0.1",
                                 "cards": {c["id"]: c["binding"]["sha256"] for c in candidates}},
                    "consentSha256": None, "enumeration": "enumeration-degraded" if self.discovery.degraded else "answered",
                    "skipComposition": expected_output == "2d", "executionAuthorized": False}
        if second_source is not None:
            decision["secondProposalBinding"] = second_source
        if reason:
            decision["phase"] = "suspended"
            decision["suspendedAt"] = {"step": "decomposition", "reasonCode": reason}
            return self._persist(decision)
        request_binding = snapshot(self.workspace, "route-request", request)
        image_evidence = [image_binding, normalization["uprightReference"],
                          snapshot(self.workspace, "reference-normalization", normalization)]
        if second_source is not None:
            image_evidence.append(second_source)
        common = {"reference": image_binding["sha256"], "request": request, "candidates": inputs["candidates"],
                  "policyVersion": _POLICY, "questionSetVersion": _QUESTIONS, "requestedModel": self.requested_model}
        if second_source is not None:
            common["secondProposalBinding"] = second_source
        prepared, retained = decompose(self, decision, proposals, decoded, image_evidence, request_binding,
                                      common, previous, explicit_2d=expected_output == "2d")
        all_proposals = [proposal for proposal, _ in prepared]
        inputs["proposals"] = all_proposals
        inputs["proposalRounds"] = decision["proposalRounds"]
        inputs_binding = snapshot(self.workspace, "routing-inputs", inputs)
        proposal_binding = snapshot(self.workspace, "object-proposals",
            {"kind": "img2.object-proposals", "version": 1, "objects": all_proposals})
        decision.update(inputsDigest=inputs_binding["sha256"], inputsRef=inputs_binding["path"],
                        proposalRef=proposal_binding["path"], proposalSha256=proposal_binding["sha256"])
        original = {proposal["objectId"]: proposal for proposal, _ in prepared}
        entries, groups = {}, {}
        for obj, row in retained:
            row["rejected"] = copy.deepcopy(rejected)
            entry = {"objectId": obj["objectId"], "label": obj["label"], "role": obj["role"],
                     "box": obj["approxBox"], "reference": row["reference"], "cropTransform": row["cropTransform"],
                     "decomposer": obj["decomposer"], "confidence": obj["confidence"],
                     "lowConfidence": obj["lowConfidence"], "instanceOf": obj.get("instanceOf"), "ownerRoute": None}
            if "classId" in obj:
                entry["classId"] = obj["classId"]
            entries[obj["objectId"]] = entry
            inventory["objects"].append(entry)
            groups.setdefault(route_key(obj), []).append((obj, row))
            eligible_ids = []
            for candidate in candidates:
                result = evaluate_candidate(candidate["card"], expected_output=expected_output,
                    roles=[obj["role"]], intent=intent, observations=obj.get("observations", {}),
                    provider_status=candidate["providerStatus"], dependencies=candidate["dependencies"],
                    blocking_gate_produced=candidate["blockingGateProduced"])
                if expected_output == "2d" and candidate["provider"] != "pixi2d":
                    result = {"eligible": False, "reasons": ["output-kind-mismatch"]}
                if explicit_route is not None and explicit_route not in {candidate["id"], candidate["provider"]}:
                    result = {"eligible": False, "reasons": ["explicit-route-mismatch"]}
                if result["eligible"]:
                    eligible_ids.append(candidate["id"])
                else:
                    row["rejected"].extend({"id": candidate["id"], "reasonCode": r} for r in result["reasons"])
            row["eligible"] = eligible_ids
        prior_rows = dict(previous.get("revisionIndex", {})) if previous else {}
        if previous:
            for old in previous["objects"]:
                prior_rows[old["objectId"]] = {**prior_rows.get(old["objectId"], {}), **old}
        for members in groups.values():
            obj, leader = members[0]
            route_object_id = obj["objectId"]
            eligible = [c for c in candidates if all(c["id"] in row["eligible"] for _, row in members)]
            for member, row in members:
                row["routeObjectId"] = route_object_id
                row["groupEligible"] = [c["id"] for c in eligible]
                entries[member["objectId"]]["routeObjectId"] = route_object_id
                # The composer names the child whose artifact owns this row. The group
                # leader is the only member that is actually built; the rest are
                # placementOnly instances of it, so they share its artifact.
                entries[member["objectId"]]["artifactObjectId"] = route_object_id
            if decision.get("decompositionBlocker") or any(row["status"] == "request-input" for _, row in members):
                for _, row in members:
                    if row["status"] == "pending":
                        row.update(status="request-input", reasonCode=decision.get("decompositionBlocker", "instance-reconciliation-unresolved"))
                continue
            if not eligible:
                for _, row in members:
                    row.update(status="request-input", reasonCode="explicit-2d-unavailable" if expected_output == "2d" else "no-common-instance-route")
                continue
            handles = {"candidate-" + hashlib.sha256(c["id"].encode()).hexdigest(): c["id"] for c in eligible}
            inverse_handles = {v: k for k, v in handles.items()}
            member_data = [{"objectId": m["objectId"], "proposal": original[m["objectId"]],
                            "box": m["approxBox"], "reference": row["reference"]} for m, row in members]
            member_binding = snapshot(self.workspace, "instance-members", member_data)
            route_semantic = digest({"members": member_data, "request": request, "candidates": inputs["candidates"],
                                     "policyVersion": _POLICY, "questionSetVersion": _QUESTIONS})
            if second_source is not None:
                route_semantic = digest({"routeInputsDigest": route_semantic, "secondProposalBinding": second_source})
            old = prior_rows.get(route_object_id)
            route_revision = (old.get("routingRevision", old["revision"]) +
                              (old.get("routeInputsDigest") != route_semantic)) if old else 1
            bindings = image_evidence + [request_binding, member_binding,
                        snapshot(self.workspace, "candidate-handles", handles)]
            bindings.append(dict(leader["reference"], role="object-crop"))
            for member, row in members:
                bindings.extend([snapshot(self.workspace, "object-proposal", original[member["objectId"]]), row["reference"]])
                row.update(candidateHandles=handles, routingRevision=route_revision, routeInputsDigest=route_semantic)
                decision["revisionIndex"][member["objectId"]].update(
                    routingRevision=route_revision, routeInputsDigest=route_semantic)
            bindings.extend(c["binding"] for c in eligible)
            unique_bindings = list({(b["role"], b["path"]): b for b in bindings}.values())
            context = self._context(obj, route_revision,
                [{"id": inverse_handles[c["id"]], "routeId": route_identity(c),
                  "outputKind": c["card"]["outputKind"], "display": c["card"].get("display", {})}
                 for c in eligible], unique_bindings, request=request)
            context["observations"]["instances"] = [{"id": m["objectId"], "role": m["role"], "box": m["approxBox"],
                "observations": m.get("observations", {}), "instanceOf": m.get("instanceOf")} for m, _ in members]
            context["observations"]["images"] = [{"role": "object-crop", "asset": "object-crop"}]
            routing_round = max(row["round"] for _, row in members)
            for _, row in members:
                row["routingRound"] = routing_round
            self._persist(decision)
            record = self._consult(context, route_round={"routeRevision": decision["revision"], "roundNumber": routing_round})
            leader["consultations"].append(self._consultation(record, context, handles))
            grant = record.get("consent")
            decision["consentSha256"] = grant["sha256"] if grant else None
            if record.get("unavailableReason") in {"not-installed", "login-declined", "consent-declined"}:
                decision["jevSession"] = "unavailable:" + record["unavailableReason"]
            selected, status, reason_code = [], "request-input", record["reasonCode"]
            if record["status"] in {"answered", "uncertain"}:
                selection = record["answers"]["selection"]
                ranking = sorted(selection["probabilities"], key=lambda key: (-selection["probabilities"][key], key))
                margin = selection["probabilities"][ranking[0]] - selection["probabilities"][ranking[1]] if len(ranking) > 1 else 1
                primary = record["decider"].startswith("jev:") or record.get("decisionMode") == "no-account"
                best_of_n = primary and (record["status"] == "uncertain" or margin < .25)
                selected = [handles[k] for k in (ranking[:2] if best_of_n else [selection["choice"]])]
                status = "best-of-n" if best_of_n else "resolved"
                if any(c["id"] in selected and c["card"]["costClass"] in {"paid", "licensed"} for c in eligible):
                    status, reason_code = "request-input", "route-action-consent-required"
            for member, row in members:
                row.update(decider=record["decider"], reasonCode=reason_code, selected=selected, status=status,
                    routeReceiptSha256=record["recordId"],
                    execution={"mode": "best-of-n" if status == "best-of-n" else "single", "n": max(1, len(selected)),
                               "placementOnly": member["objectId"] != route_object_id})
                if "loginCommand" in record:
                    row["loginCommand"] = record["loginCommand"]
                if status == "resolved":
                    entries[member["objectId"]]["ownerRoute"] = selected[0]
        inventory_binding = snapshot(self.workspace, "object-inventory", inventory)
        decision.update(inventorySha256=inventory_binding["sha256"], inventoryRef=inventory_binding["path"],
                        probes=list(self.discovery.probes))
        active = [o for o in decision["objects"] if o["status"] not in {"dropped", "merged"}]
        pending = next((o for o in active if o["status"] not in {"resolved", "best-of-n"}), None)
        blocker = decision.get("decompositionBlocker")
        if blocker or pending or not active:
            decision["phase"] = "suspended"
            decision["suspendedAt"] = {"step": pending["objectId"] if pending else "decomposition",
                "reasonCode": blocker or (pending["reasonCode"] if pending else "no-visible-objects")}
        else:
            decision["phase"] = "decided"
        return self._persist(decision)

    def verification_snapshot(self):
        """Run real discovery OUTSIDE a workspace transaction (probes lock the journal)."""
        candidates, rejected = self.discovery.enumerate()
        return {"candidates": candidates + [self._generic()], "rejected": rejected}

    def verify_decision(self, decision_ref, *, verify=None, discovered=None):
        """Verify without consulting. Caller may supply its active transaction verifier.

        Preflight via verification_snapshot outside the transaction; then pass
        discovered plus verify while publishing parent state under the same lease.
        """
        from .verification import verify_route
        if verify is None:
            discovered = self.verification_snapshot()
            with self.controller.workspace_transaction(consent=self.consent) as verifier:
                return verify_route(self, decision_ref, verifier, discovered)
        if discovered is None:
            raise RouteError("verification-preflight-required")
        return verify_route(self, decision_ref, verify, discovered)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--run", required=True)
    parser.add_argument("--image", required=True, help="Workspace-relative admitted PNG or baseline JPEG reference")
    parser.add_argument("--objects", help="Workspace-relative img2.object-proposals v1 JSON")
    parser.add_argument("--expected-output", choices=("2d", "3d"), default="3d")
    parser.add_argument("--intent", action="append", default=[])
    parser.add_argument("--explicit-route")
    parser.add_argument("--harness", default="img2", help="Trusted executable path, not a shell string")
    parser.add_argument("--auth-cli", default="img2-jev", help="Trusted executable supporting auth-status")
    args = parser.parse_args(argv)
    try:
        controller = DecisionController(args.workspace, args.run)
        discovery = Discovery(controller, harness=(args.harness,), auth=(args.auth_cli, "auth-status"))
        router = Router(args.workspace, args.run, controller=controller, discovery=discovery)
        objects = json.loads(read_bytes(router.workspace, args.objects)) if args.objects else None
        result = router.route(image=args.image, objects=objects, expected_output=args.expected_output,
                              intent=args.intent, explicit_route=args.explicit_route)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["decision"]["phase"] == "decided" else 3
    except (DecisionError, RouteError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "request-input", "reasonCode": getattr(exc, "reason_code", "invalid-routing-input")}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
