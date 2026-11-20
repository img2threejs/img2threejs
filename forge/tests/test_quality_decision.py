"""Offline adapter tests with synthetic files, not real visual qualification.

Callbacks are test doubles. No render, image-readback, provider or paid call is
performed; these examples prove arbitration and binding behavior only.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

_FORGE = str(Path(__file__).resolve().parents[1])
if _FORGE not in sys.path:
    sys.path.insert(0, _FORGE)

from _shared.decision_control import DecisionController, canonical_digest
from stage4_review.quality_decision import review_quality


LEGEND = {
    "0": "Absent or wrong relative to the declared requirement.",
    "1": "Major mismatch with the declared requirement.",
    "2": "Partially meets the declared requirement, with visible deficiencies.",
    "3": "Meets the declared requirement.",
    "4": "Exceeds the declared requirement without changing it.",
}
DIMENSIONS = ["silhouette-proportions", "part-relationships", "geometry",
              "material-appearance", "identity-features"]
PAIR_ROLES = ("reference", "render", "camera", "spec", "source", "quality-contract")


class QualityDecisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.now = 1000.0
        self.controller = DecisionController(self.workspace, "run-1", clock=lambda: self.now)
        self.controller.consume = Mock(wraps=self.controller.consume)
        self.subject = {"runId": "run-1", "objectId": "cup", "revision": 2, "passId": "geometry"}
        self.requirements = {
            "rubricVersion": "quality-review-rubric-v1", "dimensions": list(DIMENSIONS),
            "criticalFeatures": [{"id": "handle", "threshold": 3, "requiredViews": [0, 90],
                                  "evidenceRoles": ["reference-crop-handle", "render-crop-handle"]}],
            "requiredViews": [0, 90, 180, 270],
        }
        self.candidate = {
            "id": "cup-v2", "outputKind": "3d-object", "flatness": "volumetric",
            "gateResults": [{"id": "mesh", "producer": "mesh-check-v1", "status": "pass"}],
            "measurements": {"width": 2, "height": 1, "depth": 0.4, "frontArea": 2,
                             "sideArea": 0.6, "selfIntersectionCount": 0, "partCoveragePassed": True},
            "features": [{"id": "handle", "tier": "critical", "status": "pass", "score": 3, "threshold": 3}],
            "metrics": {"silhouetteIoU": 0.95, "interiorDifference": 0.02, "orbitConsistency": 0.98},
            "cost": 2, "timeMs": 100,
        }
        self.view_ids = [0, 90, 180, 270]
        self.crop_ids = ["handle"]
        self.vision_allowed, self.hard_failures = True, []
        self.transforms = {}
        self.bindings = {}
        self.refresh()
        self.grant = {"granted": True, "purposes": ["routing", "build-strategy", "quality-review", "repair"],
                      "sha256": "b" * 64, "tokenBudget": 100000}
        self.consent = Mock(side_effect=lambda: deepcopy(self.grant))
        self.availability = Mock(return_value={"status": "present"})
        self.provider = Mock(side_effect=self.advisory)
        self.fallback = Mock(side_effect=lambda context: self.advisory(context, model="host-model"))

    def put(self, role, value):
        if role in self.transforms:
            value = self.transforms[role](deepcopy(value))
        data = value if type(value) is bytes else json.dumps(value, sort_keys=True).encode()
        relative = role + ".fixture"
        (self.workspace / relative).write_bytes(data)
        self.bindings[role] = {"role": role, "path": relative, "sha256": hashlib.sha256(data).hexdigest()}

    def inputs(self, roles):
        return {role: self.bindings[role]["sha256"] for role in roles}

    def refresh(self):
        """Produce independent synthetic reports with actual on-disk digests."""
        self.bindings = {}
        for role in ("reference", "render", "camera", "spec", "source"):
            self.put(role, ("synthetic-" + role).encode())
        self.put("quality-contract", self.requirements)
        views, features, image_roles = [], [], ["reference", "render"]
        for angle in self.view_ids:
            role = "view-" + str(angle)
            self.put(role, ("synthetic-view-" + str(angle)).encode())
            views.append({"id": angle, "role": role, "sha256": self.bindings[role]["sha256"]})
            image_roles.append(role)
        for identifier in self.crop_ids:
            crop = {"id": identifier}
            for prefix in ("reference", "render"):
                role = prefix + "-crop-" + identifier
                self.put(role, ("synthetic-crop-" + role).encode())
                crop[prefix + "Role"] = role
                crop[prefix + "Sha256"] = self.bindings[role]["sha256"]
                image_roles.append(role)
            features.append(crop)
        self.put("view-set", {"subject": self.subject, "inputs": self.inputs(PAIR_ROLES),
                              "views": views, "features": features})
        self.put("gate-report", {"subject": self.subject, "inputs": self.inputs((*PAIR_ROLES, "view-set")),
                                 "visionAllowed": self.vision_allowed, "hardGateFailures": self.hard_failures,
                                 "gateResults": self.candidate["gateResults"]})
        candidate = deepcopy(self.candidate)
        candidate["views"] = list(self.view_ids)
        candidate["evidence"] = {"referenceSha256": self.bindings["reference"]["sha256"],
                                 "cameraSha256": self.bindings["camera"]["sha256"],
                                 "viewSetSha256": self.bindings["view-set"]["sha256"]}
        self.put("quality-report", {"subject": self.subject,
                                    "inputs": self.inputs((*PAIR_ROLES, "view-set", "gate-report")),
                                    "candidate": candidate})
        self.context = {
            "kind": "img2.quality-review-context", "version": 1, "group": "quality-review",
            "subject": deepcopy(self.subject), "bindings": list(self.bindings.values()),
            "candidates": [{"id": "cup-v2"}],
            "observations": {"availableViews": list(self.view_ids),
                             "images": [{"role": role, "asset": "asset-" + str(index)}
                                        for index, role in enumerate(image_roles)]},
            "requirements": deepcopy(self.requirements), "policyVersion": "route-quality-policy-v1",
            "questionSetVersion": "quality-review-questions-v1", "requestedModel": "jev-1.14.0",
        }

    @staticmethod
    def advisory(context, model="jev-1.14.0", feature="pass", score=4):
        answers = {}
        for dimension in context["requirements"]["dimensions"]:
            identifier = dimension if type(dimension) is str else dimension["id"]
            answers["dimension:" + identifier] = {
                "type": "score", "score": score, "confidence": 0.1, "legend": deepcopy(LEGEND),
                "probabilities": {str(number): int(number == score) for number in range(5)},
            }
        for item in context["requirements"]["criticalFeatures"]:
            answers["critical:" + item["id"]] = {
                "type": "choice", "choice": feature, "confidence": 0.1,
                "probabilities": {choice: int(choice == feature) for choice in ("pass", "fail", "uncertain")},
            }
        return {"kind": "img2.decision-advisory", "version": 1, "group": "quality-review",
                "subject": deepcopy(context["subject"]), "contextDigest": canonical_digest(context),
                "model": model, "answers": answers, "usage": {"input_tokens": 10, "output_tokens": 5}}

    def review(self, **overrides):
        arguments = {"provider": self.provider, "fallback": self.fallback, "availability": self.availability,
                     "consent": self.consent, "request_token_limit": 128}
        arguments.update(overrides)
        return review_quality(self.controller, self.context, **arguments)

    def assert_no_visual_calls(self, result, reason):
        self.assertEqual(result["action"], "request-input")
        self.assertFalse(result["deliverable"])
        self.assertEqual(result["reasonCode"], reason)
        self.provider.assert_not_called()
        self.fallback.assert_not_called()
        self.availability.assert_not_called()
        self.consent.assert_not_called()
        self.controller.consume.assert_not_called()

    def test_matched_complete_independent_and_semantic_pass_continues(self):
        original = deepcopy(self.context)
        result = self.review()
        self.assertEqual(result["action"], "continue")
        self.assertTrue(result["deliverable"])
        self.assertTrue(result["assessment"]["passed"])
        self.assertEqual(result["decision"]["reasonCode"], "review-evidence-only")
        self.assertEqual(result["decision"]["decider"], "jev:jev-1.14.0")
        self.provider.assert_called_once()
        self.fallback.assert_not_called()
        self.controller.consume.assert_called_once()
        self.assertEqual(self.context, original)
        projected = self.provider.call_args.args[0]
        self.assertTrue(all(set(binding) == {"role", "sha256"} for binding in projected["bindings"]))
        self.assertEqual(result["assessment"]["report"]["features"][0]["score"], 3)

    def test_forbidden_admission_never_calls_jev_fallback_or_runtime_callbacks(self):
        self.vision_allowed = False
        self.refresh()
        self.assert_no_visual_calls(self.review(), "vision-forbidden")

    def test_deterministic_hard_failure_forbids_even_with_allowed_flag(self):
        self.hard_failures = ["mesh-integrity"]
        self.refresh()
        self.assert_no_visual_calls(self.review(), "vision-forbidden")

    def test_absent_or_untyped_admission_is_not_permission(self):
        self.transforms["gate-report"] = lambda document: dict(document, visionAllowed=1)
        self.refresh()
        self.assert_no_visual_calls(self.review(), "admission-missing")

    def test_stale_reference_render_source_camera_or_gate_stops_before_calls(self):
        for role in ("reference", "render", "camera", "source", "spec", "gate-report", "quality-report",
                     "quality-contract", "view-set", "render-crop-handle", "view-180"):
            with self.subTest(role=role):
                self.refresh()
                (self.workspace / self.bindings[role]["path"]).write_bytes(b"outside-edit")
                self.assert_no_visual_calls(self.review(), "stale-evidence")

    def test_new_file_digest_does_not_make_old_render_manifest_current(self):
        (self.workspace / self.bindings["render"]["path"]).write_bytes(b"new-render")
        for binding in self.context["bindings"]:
            if binding["role"] == "render":
                binding["sha256"] = hashlib.sha256(b"new-render").hexdigest()
        self.assert_no_visual_calls(self.review(), "evidence-mismatch")

    def test_foreign_subject_is_not_admitted(self):
        self.transforms["gate-report"] = lambda document: dict(document, subject=dict(self.subject, objectId="other"))
        self.refresh()
        self.assert_no_visual_calls(self.review(), "subject-mismatch")

    def test_camera_manifest_mismatch_cannot_be_semantically_rescored(self):
        def mismatch(document):
            document["inputs"]["camera"] = "c" * 64
            return document
        self.transforms["view-set"] = mismatch
        self.refresh()
        self.assert_no_visual_calls(self.review(), "evidence-mismatch")

    def test_report_cannot_claim_a_different_reference_or_camera(self):
        def mismatch(document):
            document["candidate"]["evidence"]["cameraSha256"] = "c" * 64
            return document
        self.transforms["quality-report"] = mismatch
        self.refresh()
        self.assert_no_visual_calls(self.review(), "evidence-mismatch")

    def test_report_cannot_replace_the_deterministic_gate_results(self):
        def mismatch(document):
            document["candidate"]["gateResults"][0]["producer"] = "provider-claimed"
            return document
        self.transforms["quality-report"] = mismatch
        self.refresh()
        self.assert_no_visual_calls(self.review(), "gate-mismatch")

    def test_missing_rear_view_is_unevaluated_not_inferred_from_front(self):
        self.view_ids.remove(180)
        self.refresh()
        result = self.review()
        self.assert_no_visual_calls(result, "missing-views")
        self.assertEqual(result["status"], "unevaluated")
        self.assertTrue(result["unevaluated"])

    def test_available_view_claim_without_a_capture_is_rejected(self):
        self.view_ids.remove(180)
        self.refresh()
        self.context["observations"]["availableViews"].append(180)
        self.assert_no_visual_calls(self.review(), "view-mismatch")

    def test_feature_specific_missing_view_is_not_inferred(self):
        self.requirements["criticalFeatures"][0]["requiredViews"].append(45)
        self.refresh()
        self.assert_no_visual_calls(self.review(), "missing-views")

    def test_crop_pair_must_be_named_in_the_frozen_feature_contract(self):
        self.requirements["criticalFeatures"][0]["evidenceRoles"] = []
        self.refresh()
        self.assert_no_visual_calls(self.review(), "missing-critical-evidence")

    def test_declared_images_cannot_omit_a_bound_feature_crop(self):
        self.context["observations"]["images"] = [
            image for image in self.context["observations"]["images"] if image["role"] != "render-crop-handle"]
        self.assert_no_visual_calls(self.review(), "missing-evidence")

    def test_missing_crop_or_wrong_crop_identity_blocks_review(self):
        for identifiers in ([], ["wrong-handle"]):
            with self.subTest(identifiers=identifiers):
                self.crop_ids = identifiers
                self.refresh()
                self.assert_no_visual_calls(self.review(), "missing-critical-evidence")

    def test_crop_digest_must_match_the_current_bound_crop_file(self):
        def mismatch(document):
            document["features"][0]["renderSha256"] = "d" * 64
            return document
        self.transforms["view-set"] = mismatch
        self.refresh()
        self.assert_no_visual_calls(self.review(), "evidence-mismatch")

    def test_distinct_angles_cannot_reuse_one_capture_role(self):
        def mismatch(document):
            document["views"][1].update(role=document["views"][0]["role"], sha256=document["views"][0]["sha256"])
            return document
        self.transforms["view-set"] = mismatch
        self.refresh()
        self.assert_no_visual_calls(self.review(), "evidence-mismatch")

    def test_missing_handle_in_independent_report_cannot_be_replaced_by_high_scores(self):
        self.candidate["features"] = []
        self.refresh()
        self.assert_no_visual_calls(self.review(), "missing-critical-evidence")

    def test_failed_handle_is_not_averaged_away_by_perfect_semantic_dimensions(self):
        self.candidate["features"][0].update(status="fail", score=0)
        self.refresh()
        result = self.review()
        self.assertEqual(result["action"], "refine")
        self.assertEqual(result["reasonCode"], "independent-quality-failed")
        self.assertFalse(result["deliverable"])
        self.assertEqual(result["decision"]["answers"]["dimension:geometry"]["score"], 4)
        self.assertEqual(result["assessment"]["report"]["features"][0]["status"], "fail")
        self.provider.assert_called_once()
        self.fallback.assert_not_called()

    def test_feature_score_below_frozen_threshold_still_blocks_with_semantic_pass(self):
        self.candidate["features"][0]["score"] = 2.9
        self.refresh()
        result = self.review()
        self.assertEqual(result["action"], "refine")
        self.assertFalse(result["assessment"]["passed"])
        self.provider.assert_called_once()

    def test_uncertain_independent_critical_score_requests_input_without_override(self):
        self.candidate["features"][0]["status"] = "uncertain"
        self.refresh()
        self.assert_no_visual_calls(self.review(), "missing-measurements")

    def test_jev_four_of_four_cannot_clear_hard_measurement_failure(self):
        self.candidate["measurements"]["selfIntersectionCount"] = 1
        self.refresh()
        result = self.review()
        self.assertEqual(result["action"], "refine")
        self.assertEqual(result["assessment"]["hardStatus"], "fail")
        self.assertFalse(result["deliverable"])
        self.provider.assert_called_once()
        self.fallback.assert_not_called()

    def test_jev_critical_fail_blocks_an_independent_numeric_pass(self):
        self.provider.side_effect = lambda context: self.advisory(context, feature="fail")
        result = self.review()
        self.assertTrue(result["assessment"]["passed"])
        self.assertEqual(result["action"], "refine")
        self.assertEqual(result["reasonCode"], "semantic-quality-failed")
        self.fallback.assert_not_called()

    def test_jev_uncertainty_never_asks_host_model_for_a_replacement_pass(self):
        self.provider.side_effect = lambda context: self.advisory(context, feature="uncertain")
        result = self.review()
        self.assertEqual(result["action"], "request-input")
        self.assertEqual(result["reasonCode"], "review-uncertain")
        self.assertEqual(result["unevaluated"], ["critical:handle"])
        self.provider.assert_called_once()
        self.fallback.assert_not_called()
        self.controller.consume.assert_called_once()

    def test_unevaluated_dimension_requests_input_without_model_override(self):
        def unevaluated(context):
            answer = self.advisory(context)
            answer["answers"]["dimension:identity-features"] = {"type": "unevaluated"}
            return answer
        self.provider.side_effect = unevaluated
        result = self.review()
        self.assertEqual(result["action"], "request-input")
        self.assertIn("dimension:identity-features", result["unevaluated"])
        self.fallback.assert_not_called()

    def test_dimension_failure_is_not_hidden_by_other_four_scores(self):
        def missing_identity(context):
            answer = self.advisory(context)
            dimension = answer["answers"]["dimension:identity-features"]
            dimension.update(score=0, probabilities={str(number): int(number == 0) for number in range(5)})
            return answer
        self.provider.side_effect = missing_identity
        result = self.review()
        self.assertEqual(result["action"], "refine")
        self.assertFalse(result["deliverable"])
        self.fallback.assert_not_called()

    def test_missing_credentials_request_login_not_model_fallback(self):
        self.availability.return_value = {"status": "credential-missing"}
        result = self.review()
        self.assertEqual(result["action"], "request-input")
        self.assertEqual(result["loginCommand"], "img2-jev login")
        self.provider.assert_not_called()
        self.fallback.assert_not_called()

    def test_rejected_credentials_request_login_not_model_fallback(self):
        self.availability.return_value = {"status": "credential-rejected"}
        result = self.review()
        self.assertEqual(result["action"], "request-input")
        self.assertEqual(result["reasonCode"], "credential-rejected")
        self.assertEqual(result["loginCommand"], "img2-jev login")
        self.provider.assert_not_called()
        self.fallback.assert_not_called()

    def test_proven_unavailable_group_uses_identical_bound_model_evidence(self):
        self.availability.return_value = {"status": "capability-unavailable"}
        result = self.review()
        self.assertEqual(result["action"], "continue")
        self.assertEqual(result["decision"]["decider"], "model:host-model")
        self.assertEqual(result["decision"]["unavailableReason"], "capability-unavailable")
        self.provider.assert_not_called()
        self.fallback.assert_called_once()
        self.assertEqual(self.fallback.call_args.args[0]["requirements"], self.requirements)

    def test_fallback_four_of_four_cannot_clear_independent_failure(self):
        self.availability.return_value = {"status": "capability-unavailable"}
        self.candidate["gateResults"][0]["status"] = "fail"
        self.refresh()
        result = self.review()
        self.assertEqual(result["action"], "refine")
        self.assertFalse(result["deliverable"])
        self.provider.assert_not_called()
        self.fallback.assert_called_once()

    def test_forbidden_admission_prevents_fallback_even_if_runtime_is_unavailable(self):
        self.availability.return_value = {"status": "not-installed"}
        self.vision_allowed = False
        self.refresh()
        self.assert_no_visual_calls(self.review(), "vision-forbidden")

    def test_mutation_during_provider_call_cannot_be_consumed(self):
        def stale(context):
            answer = self.advisory(context)
            (self.workspace / self.bindings["source"]["path"]).write_bytes(b"new-source")
            return answer
        self.provider.side_effect = stale
        result = self.review()
        self.assertEqual(result["action"], "request-input")
        self.assertEqual(result["reasonCode"], "stale-evidence")
        self.provider.assert_called_once()
        self.fallback.assert_not_called()

    def test_unchanged_resume_reuses_record_and_reconsumes_without_new_visual_call(self):
        first = self.review()
        second = self.review()
        self.assertEqual(first["decision"]["recordId"], second["decision"]["recordId"])
        self.assertEqual(second["action"], "continue")
        self.provider.assert_called_once()
        self.assertEqual(self.controller.consume.call_count, 2)

    def test_quality_contract_cannot_be_lowered_only_in_context(self):
        self.context["requirements"]["criticalFeatures"][0]["threshold"] = 0
        self.assert_no_visual_calls(self.review(), "contract-mismatch")

    def test_bound_report_cannot_lower_critical_threshold(self):
        self.candidate["features"][0]["threshold"] = 0
        self.refresh()
        self.assert_no_visual_calls(self.review(), "contract-mismatch")

    def test_admission_does_not_allow_policy_or_rubric_replacement(self):
        self.context["policyVersion"] = "provider-policy"
        self.assert_no_visual_calls(self.review(), "policy-mismatch")

    def test_object_deadline_is_shared_across_review_calls(self):
        self.review()
        self.subject["revision"] = 3
        self.refresh()
        self.now += 3600
        result = self.review()
        self.assertEqual(result["action"], "request-input")
        self.assertEqual(result["reasonCode"], "deadline-exhausted")
        self.provider.assert_called_once()
        self.fallback.assert_not_called()

    def test_missing_numeric_feature_score_is_never_fabricated_from_a_choice(self):
        del self.candidate["features"][0]["score"]
        self.refresh()
        self.assert_no_visual_calls(self.review(), "missing-measurements")

    def test_missing_numeric_measurement_requests_input(self):
        del self.candidate["measurements"]["sideArea"]
        self.refresh()
        self.assert_no_visual_calls(self.review(), "missing-measurements")

    def test_bound_feature_threshold_above_initial_floor_is_preserved(self):
        self.requirements["criticalFeatures"][0]["threshold"] = 4
        self.candidate["features"][0].update(threshold=4, score=3.9)
        self.refresh()
        result = self.review()
        self.assertEqual(result["action"], "refine")
        self.assertEqual(result["assessment"]["report"]["features"][0]["threshold"], 4)

    def test_missing_view_names_and_critical_identity_are_withheld_explicitly(self):
        self.view_ids.remove(180)
        self.refresh()
        result = self.review()
        self.assert_no_visual_calls(result, "missing-views")
        self.assertIn("view:180", result["unevaluated"])
        self.assertIn("critical:handle", result["unevaluated"])

    def test_aliased_bindings_cannot_choose_between_different_reports(self):
        self.context["bindings"].append(dict(self.context["bindings"][0], path="source.fixture"))
        self.assert_no_visual_calls(self.review(), "invalid-binding")

    def test_subject_revision_uses_exact_json_identity_not_numeric_coercion(self):
        self.transforms["gate-report"] = lambda document: dict(document, subject=dict(self.subject, revision=2.0))
        self.refresh()
        self.assert_no_visual_calls(self.review(), "subject-mismatch")


if __name__ == "__main__":
    unittest.main()
