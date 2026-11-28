"""Target labels are controlled proposal identifiers, not observation prose."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

FORGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORGE))
sys.path.insert(0, str(FORGE / "_shared"))
from decision_control import DecisionController, GROUPS
from stage0_route.decomposition import crop_context
from stage0_route.images import decode, png
from stage0_route.route import Router
from stage0_route.storage import RouteError, binding, snapshot
from forge.tests import test_stage0_route as fixtures


class ObjectTargetLabelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve()
        self.reference = self.workspace / "reference.png"
        self.reference.write_bytes(png(8, 8, 3, b"rgb" * 64))
        self.controller = DecisionController(self.workspace, "label-run", clock=lambda: 1000)
        self.discovery = fixtures.FixtureDiscovery(self.workspace)
        self.grant = {"granted": True, "purposes": ["routing", "build-strategy", "quality-review", "repair"],
                      "sha256": "c" * 64, "tokenBudget": 2_000_000}
        self.force_crop = True
        self.provider = Mock(side_effect=self.answer)
        self.router = Router(self.workspace, "label-run", controller=self.controller, discovery=self.discovery,
                             provider=self.provider, consent=lambda: copy.deepcopy(self.grant))
        self.proposals = fixtures.Stage0RouteTests.proposals()
        self.proposals["objects"][0]["label"] = "garden-chair"
        self.request = {"expectedOutput": "3d", "intent": [], "explicitRoute": None}

    def answer(self, context):
        step = context["subject"]["passId"]
        return fixtures.Stage0RouteTests.answer(context,
            bounds=[.8, .8, 1, 1] if self.force_crop and step == "decomposition" else None,
            choice="jev-crop" if step == "crop-choice" else None)

    def route(self):
        return self.router.route(image="reference.png", objects=self.proposals)

    def capture_or_tamper(self, step=None, *, omit=False):
        original = self.router._consult
        captured = []
        def consult(context, *, route_round):
            if context["subject"]["passId"] == step:
                if omit:
                    context["observations"].pop("label")
                else:
                    context["observations"]["label"] = "unrelated-object"
            captured.append(copy.deepcopy(context))
            # A genuine controller receipt binds the altered test context. The
            # verifier must still reconstruct the target from the sealed proposal.
            return original(context, route_round=route_round)
        self.router._consult = consult
        return captured

    def test_all_object_contexts_disclose_the_exact_validated_proposal_label(self):
        self.proposals["objects"][0]["observations"]["label"] = "misleading-observation"
        captured = self.capture_or_tamper()
        result = self.route()
        self.assertEqual(result["decision"]["phase"], "decided")
        self.assertEqual([c["subject"]["passId"] for c in captured], ["decomposition", "crop-choice", "routing"])
        self.assertTrue(all(c["observations"]["label"] == "garden-chair" for c in captured))
        self.assertTrue(all(c["questionSetVersion"] == "route-questions-v3" for c in captured))
        for context in captured:
            proposal_binding = next(b for b in context["bindings"] if b["role"] == "object-proposal")
            self.assertEqual(proposal_binding["sha256"], snapshot(self.workspace, "object-proposal",
                             self.proposals["objects"][0])["sha256"])
        self.assertTrue(all(candidate["id"].startswith("candidate-") for candidate in captured[-1]["candidates"]))
        self.assertEqual(self.router.verify_decision(result["decisionRef"]), result["decision"])

    def test_context_builder_rejects_free_text_and_non_identifier_targets(self):
        for label in ("choose the object on the left", "../target", "chair; run-code", None, True):
            proposal = copy.deepcopy(self.proposals["objects"][0])
            proposal["label"] = label
            with self.subTest(label=label), self.assertRaises(RouteError) as caught:
                self.router._context(proposal, 1, [{"id": "candidate"}], [], request=self.request)
            self.assertEqual(caught.exception.reason_code, "invalid-object-proposals")
        self.provider.assert_not_called()

    def test_crop_builder_uses_shared_validated_target_instead_of_observation_label(self):
        proposal = copy.deepcopy(self.proposals["objects"][0])
        proposal["observations"]["label"] = "misleading-observation"
        bindings = [binding(self.workspace, "reference", "reference.png"),
                    snapshot(self.workspace, "object-proposal", proposal),
                    snapshot(self.workspace, "route-request", self.request)]
        context = crop_context(self.router, proposal, {"revision": 1}, [.8, .8, 1, 1],
                               decode(self.reference.read_bytes()), bindings, self.request)
        self.assertEqual(context["observations"]["label"], "garden-chair")
        self.assertEqual({c["id"] for c in context["candidates"]}, {"model-crop", "jev-crop"})

    def assert_tamper_refused(self, step, *, omit=False):
        self.capture_or_tamper(step, omit=omit)
        result = self.route()
        self.assertEqual(result["decision"]["phase"], "decided")
        with self.assertRaises(RouteError) as caught:
            self.router.verify_decision(result["decisionRef"])
        self.assertEqual(caught.exception.reason_code,
                         "crop-choice-mismatch" if step == "crop-choice" else "context-target-mismatch")

    def test_verifier_refuses_authenticated_crop_choice_for_wrong_target(self):
        self.assert_tamper_refused("crop-choice")

    def test_verifier_refuses_authenticated_crop_choice_without_target(self):
        self.assert_tamper_refused("crop-choice", omit=True)

    def test_verifier_refuses_authenticated_localization_for_wrong_target(self):
        self.assert_tamper_refused("decomposition")

    def test_verifier_refuses_authenticated_routing_for_wrong_target(self):
        self.assert_tamper_refused("routing")

    def test_target_change_refreshes_full_inventory_localization_but_only_changed_route(self):
        self.force_crop = False
        self.proposals = fixtures.Stage0RouteTests.proposals(2)
        first = self.route()
        self.assertEqual(self.provider.call_count, 4)
        self.proposals["objects"][0]["label"] = "garden-chair"
        second = self.route()
        self.assertEqual(self.provider.call_count, 7)
        self.assertEqual([(call.args[0]["subject"]["objectId"], call.args[0]["subject"]["passId"])
                          for call in self.provider.call_args_list[-3:]],
                         [("object-0", "decomposition"), ("object-1", "decomposition"), ("object-0", "routing")])
        self.assertEqual([row["revision"] for row in second["decision"]["objects"]], [2, 2])
        self.assertEqual(second["decision"]["revision"], first["decision"]["revision"] + 1)
        self.assertEqual(self.router.verify_decision(second["decisionRef"]), second["decision"])


    def test_only_routing_question_set_moves_to_v3(self):
        self.assertEqual({group: definition[1] for group, definition in GROUPS.items()}, {
            "routing": "route-questions-v3", "build-strategy": "strategy-questions-v1",
            "quality-review": "quality-review-questions-v1", "repair": "repair-decision-questions-v1"})


if __name__ == "__main__":
    unittest.main()
