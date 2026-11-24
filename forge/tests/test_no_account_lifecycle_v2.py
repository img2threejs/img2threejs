"""Live v2 grants and shared group contracts, entirely offline and fixture-owned."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parent / "_shared")]
import test_route_no_account as fixtures
import test_route_authority as route_fixtures
from decision_control import DecisionController, DecisionError, GROUPS, canonical_digest
from stage0_route.model_decider import ModelDecider
import route_no_account as authority


class NoAccountV2Tests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.NoAccountRoutingTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.workspace = self.case.workspace

    def test_legacy_grant_requires_reattest_and_is_preserved_with_its_journal(self):
        legacy = self.case.mint()
        legacy.update(version=1, policyVersion="routing-no-jev-account-v1")
        legacy.pop("purposes", None)
        legacy.pop("attestationId")
        legacy["attestationId"] = canonical_digest(legacy)
        path = authority._path(self.workspace, "run-1")
        authority._store(path, legacy)
        before = path.read_bytes()
        with self.assertRaises(DecisionError) as caught:
            self.case.grant()
        self.assertEqual(caught.exception.reason_code, "no-jev-account-reattest-required")
        self.assertEqual(path.read_bytes(), before)
        entries_before = (self.workspace / ".img2threejs/route/journal.jsonl").read_bytes()
        current = self.case.mint(purposes=list(GROUPS))
        self.assertEqual(current["version"], 2)
        self.assertEqual(set(self.case.grant()()["purposes"]), set(GROUPS))
        history = list(path.parent.glob(path.name + ".*.history"))
        self.assertTrue(any(item.read_bytes() == before for item in history))
        self.assertTrue((self.workspace / ".img2threejs/route/journal.jsonl").read_bytes().startswith(entries_before))

    def test_each_purpose_must_be_explicit_and_insufficient_scope_does_not_dispatch(self):
        with patch.object(authority, "_confirm", side_effect=AssertionError("no implicit purpose consent")):
            with self.assertRaises(DecisionError) as caught:
                authority.attest(self.workspace, "run-1", model_id=fixtures.MODEL, images=["ref.png"], token_budget=64000)
        self.assertEqual(caught.exception.reason_code, "invalid-attestation")
        self.case.mint(purposes=["routing"])
        grant = self.case.grant()
        controller = DecisionController(self.workspace, "run-1", routing_authority=grant)
        called = []
        result = controller.consult(self.context("repair"), consent=grant, provider=lambda _: called.append(True))
        self.assertEqual(result["status"], "request-input")
        self.assertEqual(result["reasonCode"], "consent-required")
        self.assertEqual(called, [])

    def context(self, group):
        kind, questions = GROUPS[group]
        sha = hashlib.sha256(self.case.image.read_bytes()).hexdigest()
        return {"kind": kind, "version": 1, "group": group,
                "subject": {"runId": "run-1", "objectId": "object-1", "revision": 1, "passId": group},
                "bindings": [{"role": role, "path": "ref.png", "sha256": sha} for role in ("reference", "render")],
                "candidates": [] if group == "quality-review" else [{"id": "a"}, {"id": "b"}],
                "observations": {"images": [{"role": role, "asset": role} for role in ("reference", "render")],
                                 "availableViews": []},
                "requirements": {"criticalFeatures": [], "requiredViews": [], "dimensions": [
                    "silhouette-proportions", "part-relationships", "geometry", "material-appearance", "identity-features"]},
                "policyVersion": "fixture-v1", "questionSetVersion": questions, "requestedModel": fixtures.MODEL}

    def test_all_four_groups_resume_and_consume_under_the_same_live_model_grant(self):
        self.case.mint(purposes=list(GROUPS))
        grant = self.case.grant()
        controller = DecisionController(self.workspace, "run-1", routing_authority=grant)
        recorded = []
        for group in GROUPS:
            context = self.context(group)
            factory = ModelDecider(controller, grant)
            with self.assertRaises(DecisionError) as caught:
                factory.provider(context)
            self.assertEqual(caught.exception.reason_code, "model-answers-required")
            question = json.loads((self.workspace / factory.pending["path"]).read_text())
            self.assertEqual(question["answerFormat"]["group"], group)
            self.assertFalse(question["presentedAsJev"])
            projected = controller._context(context)
            answer = (route_fixtures.RouteAuthorityTests.quality_answer(projected) if group == "quality-review" else
                      route_fixtures.RouteAuthorityTests.answer(projected))
            answer.update(model=fixtures.MODEL, group=group)
            answers = {"kind": "img2.route-model-answers", "version": 1,
                       "attestationId": grant.metadata()["attestationId"], "answers": [answer]}
            (self.workspace / "answers.json").write_text(json.dumps(answers))
            factory = ModelDecider(controller, grant, "answers.json")
            record = controller.consult(context, consent=grant, provider=factory.provider(context))
            self.assertEqual(record["status"], "answered")
            self.assertEqual(record["decider"], "model:" + fixtures.MODEL)
            self.assertFalse(record["presentedAsJev"])
            reopened = DecisionController(self.workspace, "run-1", routing_authority=self.case.grant())
            self.assertEqual(reopened.consume(record, context, consent=reopened.routing_authority), record)
            recorded.append((record, context))
        self.case.mint(revoke=True)
        with self.assertRaises(DecisionError) as caught:
            controller.consume(*recorded[-1], consent=grant)
        self.assertEqual(caught.exception.reason_code, "attestation-revoked")

    def test_workflow_cli_questions_selection_publication_and_revocation_share_authority(self):
        from contextlib import redirect_stdout
        import io
        from types import SimpleNamespace
        import test_workflow_decisions as workflow_fixtures
        from forge import decision
        case = workflow_fixtures.WorkflowDecisionTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        reference = case.request["bindings"][0]["path"]
        self.case.typed("attest workflow-fixture " + fixtures.MODEL + "\n")
        with redirect_stdout(io.StringIO()):
            grant = authority.attest(case.workspace, "workflow-fixture", model_id=fixtures.MODEL,
                images=[reference], token_budget=256000, purposes=list(GROUPS))
        discovery = SimpleNamespace(jev=None, enumerate=lambda: None,
            query=lambda _: {"providers": []}, availability=lambda: self.fail("no Jev probe in primary model mode"))
        harness = SimpleNamespace(root=self.case.root / "harness", argv=("fixture-img2",), runner=lambda *a, **k: None)
        args = ["strategy-spec", "--workspace", str(case.workspace), "--run-id", "workflow-fixture",
            "--child-state", case.state_path, "--request", "request.json", "--policy-module", "/host/policy.py",
            "--policy-sha256", "a" * 64, "--harness-root", str(harness.root), "--harness-sha", "b" * 40,
            "--harness-executable", "/host/harness/bin/img2.mjs", "--harness-sha256", "c" * 64,
            "--no-jev-account", "--image", reference]
        def invoke(request, answers=False):
            (case.workspace / "request.json").write_text(json.dumps(request))
            output = io.StringIO()
            with patch.object(decision, "PinnedDiscovery", return_value=discovery), \
                    patch.object(decision, "HarnessPin", return_value=harness), \
                    patch.object(decision, "load_policy", return_value=case.policy), \
                    patch.object(decision, "_approved_lock", side_effect=AssertionError("no Jev lock")), \
                    patch.object(decision, "JevProviderFactory", side_effect=AssertionError("no Jev provider")), \
                    redirect_stdout(output):
                code = decision.main([*args, *(["--model-answers", "answers.json"] if answers else [])])
            return code, json.loads(output.getvalue())
        before = (case.workspace / case.state_path).read_bytes()
        code, pending = invoke(case.request)
        self.assertNotIn("decider", pending)
        self.assertNotIn("presentedAsJev", pending)
        self.assertEqual((code, pending["status"]), (0, "questions-ready"), pending)
        self.assertEqual((case.workspace / case.state_path).read_bytes(), before)
        question = json.loads((case.workspace / pending["questionsRef"]["path"]).read_text())
        advisory = case.factory.provider(question["state"])(question["state"])
        advisory["model"] = fixtures.MODEL
        (case.workspace / "answers.json").write_text(json.dumps({
            "kind": "img2.route-model-answers", "version": 1, "attestationId": grant["attestationId"],
            "answers": [advisory]}))
        code, selected = invoke(case.request, answers=True)
        self.assertEqual((code, selected["result"]["action"]), (0, "selected"), selected)
        begun = dict(case.request, operation="begin", trialRevision=2)
        code, started = invoke(begun, answers=True)
        self.assertEqual(code, 0, started)
        output_binding = case.bind("spec", "candidates/rev-2/spec.json", started["result"]["spec"])
        code, finished = invoke(dict(begun, operation="finish", outputBinding=output_binding), answers=True)
        self.assertEqual(code, 0, finished)
        self.assertEqual(case.state()["decisionLifecycle"]["candidateRevision"], 2)
        self.assertEqual(finished["decider"], "model:" + fixtures.MODEL)
        self.assertFalse(finished["presentedAsJev"])
        controller = DecisionController(case.workspace, "workflow-fixture",
            routing_authority=authority.RoutingNoAccount(case.workspace, "workflow-fixture", reference))
        with controller._locked():
            _, state = controller._load()
        self.assertEqual(state["runs"]["workflow-fixture"]["tokensReserved"], 0)
        self.case.typed("revoke workflow-fixture\n")
        with redirect_stdout(io.StringIO()):
            authority.attest(case.workspace, "workflow-fixture", model_id=None, images=[], token_budget=None, revoke=True)
        code, refused = invoke(case.request, answers=True)
        self.assertEqual((code, refused["reasonCode"]), (3, "attestation-revoked"))

    def test_terminal_model_review_and_completion_keep_independent_native_gate_closed(self):
        from contextlib import redirect_stdout
        import io
        import terminal
        from route_parent import RouteParent, ParentError
        case = route_fixtures.RouteAuthorityTests()
        self.addCleanup(case.doCleanups)
        router_class = route_fixtures.Router
        def primary_router(workspace, run_id, **kwargs):
            (workspace / "reference.png").write_bytes(route_fixtures.png(4, 4, 3, bytes(range(48))))
            self.case.typed(f"attest {run_id} {fixtures.MODEL}\n")
            with redirect_stdout(io.StringIO()):
                authority.attest(workspace, run_id, model_id=fixtures.MODEL, images=["reference.png"],
                    token_budget=256000, purposes=list(GROUPS))
            grant = authority.RoutingNoAccount(workspace, run_id, "reference.png")
            case.controller = DecisionController(workspace, run_id, clock=lambda: 1000, routing_authority=grant)
            case.consent = grant
            def answer(context):
                result = case.answer(context)
                result["model"] = fixtures.MODEL
                return result
            case.provider.side_effect = answer
            kwargs.update(controller=case.controller, consent=grant, requested_model=fixtures.MODEL)
            return router_class(workspace, run_id, **kwargs)
        with patch.object(route_fixtures, "Router", side_effect=primary_router):
            case.setUp()
        fixture, aggregate = case.quality_fixture()
        child = fixture[1]
        parent = RouteParent(case.controller, verify_aggregate=aggregate, consent=case.consent)
        factory = ModelDecider(case.controller, case.consent)
        reviews = terminal.TerminalReviews(parent, provider_factory=factory,
            availability=lambda: self.fail("no Jev availability probe"), fallback=None)
        parent_before = (case.workspace / ".img2threejs/state.json").read_bytes()
        child_before = (child / "state.json").read_bytes()
        # Synthetic capture transport is NOT native authority or visual qualification.
        with patch.object(aggregate._pixi_capture_authority, "verify_capture", return_value=None):
            pending = reviews.consult("whole-image")
            self.assertEqual(pending["reasonCode"], "model-answers-required")
            question = json.loads((case.workspace / factory.pending["path"]).read_text())
            answer = case.quality_answer(question["state"])
            answer["model"] = fixtures.MODEL
            (case.workspace / "model-answers.json").write_text(json.dumps({
                "kind": "img2.route-model-answers", "version": 1,
                "attestationId": case.consent.metadata()["attestationId"], "answers": [answer]}))
            reviews.provider_factory = ModelDecider(case.controller, case.consent, "model-answers.json")
            result = reviews.consult("whole-image")
        self.assertEqual(result["status"], "decision-recorded")
        record = json.loads((case.workspace / result["record"]["path"]).read_text())
        self.assertEqual(record["decider"], "model:" + fixtures.MODEL)
        self.assertFalse(record["presentedAsJev"])
        with self.assertRaises(ParentError):
            parent.complete_child("whole-image")
        self.assertEqual((case.workspace / ".img2threejs/state.json").read_bytes(), parent_before)
        self.assertEqual((child / "state.json").read_bytes(), child_before)
        from types import SimpleNamespace
        from test_route_cli import route_cli
        harness = SimpleNamespace(root=case.runtime, argv=("fixture-img2",), runner=case.command)
        argv = ["--workspace", str(case.workspace), "--run-id", "route-run",
            "--complete-child", "whole-image", "--decision-ref", case.routed["decisionRef"],
            "--no-jev-account", "--image", "reference.png",
            "--harness-root", str(case.runtime), "--harness-sha", "a" * 40,
            "--harness-executable", str(case.runtime / "bin/img2.mjs"), "--harness-sha256", "b" * 64]
        output = io.StringIO()
        with patch.object(route_cli, "HarnessPin", return_value=harness), \
                patch.object(route_cli, "PinnedDiscovery", return_value=case.discovery), \
                patch.object(route_cli, "_route_parent", return_value=parent), \
                patch.object(route_cli, "_approved_lock", side_effect=AssertionError("no Jev lock")), \
                patch.object(route_cli, "JevProviderFactory", side_effect=AssertionError("no Jev provider")), \
                redirect_stdout(output):
            code = route_cli.main(argv)
        value = json.loads(output.getvalue())
        self.assertEqual((code, value["reasonCode"]), (3, "terminal-native-capture-authority-unavailable"), value)
        self.assertFalse(value["terminalAcceptance"])
        self.assertFalse(value["deliverable"])
        self.assertNotIn("decider", value)
        self.assertEqual((case.workspace / ".img2threejs/state.json").read_bytes(), parent_before)
        self.assertEqual((child / "state.json").read_bytes(), child_before)


if __name__ == "__main__":
    unittest.main()
