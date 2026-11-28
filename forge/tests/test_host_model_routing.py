"""Local host routing with real controller journals; deterministic answers only in tests."""
import copy
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

FORGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORGE))
sys.path.insert(0, str(FORGE / "_shared"))
sys.path.insert(0, str(Path(__file__).parent))
import test_stage0_route as fixtures
from decision_control import DecisionController, DecisionError, GROUPS, canonical_digest
from host_model_routing import HostModelRouting
import route_no_account
from stage0_route.discovery import Discovery, JEV_EDGE
from stage0_route.images import png
from stage0_route.model_decider import ModelDecider
from stage0_route.route import Router
from route_parent import ParentError
from stage0_route.storage import encode

SPEC = importlib.util.spec_from_file_location("host_model_route_cli", FORGE / "route.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class HostModelRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name).resolve()
        (self.workspace / "ref.png").write_bytes(png(16, 16, 3, bytes(range(256)) * 3))
        self.objects = fixtures.Stage0RouteTests.proposals()
        (self.workspace / "objects.json").write_text(json.dumps(self.objects))
        self.request = {"expectedOutput": "3d", "intent": [], "explicitRoute": None}
        self.answers = []
        self.authorization_id = None
        self.candidates = []

    def authority(self, **kwargs):
        return HostModelRouting(self.workspace, "run-1", "ref.png", request=self.request,
                                sources=["objects.json"], **kwargs)

    def journal(self):
        return [json.loads(line) for line in
                (self.workspace / ".img2threejs/route/journal.jsonl").read_text().splitlines()]

    def invoke(self, *extra, envelope=None, omit_objects=False):
        argv = ["--workspace", str(self.workspace), "--run-id", "run-1", "--image", "ref.png"]
        if not omit_objects:
            argv.extend(["--objects", "objects.json"])
        if self.answers or envelope is not None:
            document = envelope if envelope is not None else {
                "kind": "img2.route-model-answers", "version": 1,
                "authorizationId": self.authorization_id, "answers": self.answers}
            (self.workspace / "answers.json").write_text(json.dumps(document))
            argv.extend(["--model-answers", "answers.json"])
        output = io.StringIO()
        discovery = fixtures.FixtureDiscovery(self.workspace, self.candidates)
        with patch.object(cli, "PinnedDiscovery", return_value=discovery), \
                patch.object(cli, "LiveConsent", side_effect=AssertionError("Jev auth")) as auth, \
                patch.object(cli, "JevProviderFactory", side_effect=AssertionError("Jev provider")) as jev, \
                patch.object(cli, "_approved_lock", side_effect=AssertionError("Jev release")) as lock, \
                patch.object(route_no_account, "credential_status", side_effect=AssertionError("credential probe")) as credential, \
                patch("builtins.input", side_effect=AssertionError("TTY prompt")), \
                patch("socket.create_connection", side_effect=AssertionError("network")), \
                redirect_stdout(output):
            code = cli.main([*argv, *extra])
        for callback in (auth, jev, lock, credential, discovery.availability):
            callback.assert_not_called()
        return code, json.loads(output.getvalue())

    def questions(self, *extra, omit_objects=False):
        code, output = self.invoke(*extra, omit_objects=omit_objects)
        self.assertEqual((code, output["status"]), (0, "questions-ready"), output)
        self.assertNotIn("decider", output)
        self.assertEqual(output["decisionMode"], "host-model-pending")
        ref = output["questionsRef"]
        raw = (self.workspace / ref["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), ref["sha256"])
        question = json.loads(raw)
        self.assertNotIn("attestationId", question)
        self.assertNotIn("decider", question)
        self.assertFalse(question["presentedAsJev"])
        self.assertEqual(question["contextDigest"], canonical_digest(question["state"]))
        self.authorization_id = question["authorizationId"]
        return question

    def answer(self, question, **kwargs):
        answer = fixtures.Stage0RouteTests.answer(
            question["state"], model=question["answerFormat"]["model"], **kwargs)
        self.answers.append(answer)
        return answer

    def complete(self, *, extra=(), **selection):
        self.answer(self.questions(*extra))
        self.answer(self.questions(*extra), **selection)
        code, output = self.invoke(*extra)
        self.assertEqual((code, output["status"]), (0, "decided"), output)
        return output, json.loads((self.workspace / output["decisionRef"]).read_bytes())

    def router(self, authority=None):
        authority = self.authority() if authority is None else authority
        controller = DecisionController(self.workspace, "run-1", routing_authority=authority)
        return Router(self.workspace, "run-1", controller=controller, consent=authority,
                      requested_model=authority.model_id,
                      discovery=fixtures.FixtureDiscovery(self.workspace, self.candidates))

    def test_default_needs_no_jev_installation_credentials_or_tty(self):
        for key in ("", "test-environment-credential"):
            with self.subTest(credential=bool(key)), \
                    patch.dict(os.environ, {"IMG2_HOME": str(self.workspace / "missing-install"),
                                            "TYPESAFE_API_KEY": key}), \
                    patch.object(sys, "stdin", io.StringIO("")), patch("os.isatty", return_value=False):
                question = self.questions()
                self.assertEqual(question["answerFormat"]["model"], "host-agent")
                self.assertEqual(question["allowedPurposes"], ["routing"])
        events = self.journal()
        self.assertEqual(sum(e["event"] == "host-model-requested" for e in events), 1)
        self.assertFalse(any(e["event"] in {"attempt-pending", "host-model-selected", "no-account-attested"}
                             for e in events))

    def test_accumulated_real_typed_path_records_model_and_verifies(self):
        output, decision = self.complete(extra=("--model-id", "test-provider:test-model"))
        for value in (output, decision):
            self.assertEqual(value["decisionMode"], "host-model")
            self.assertEqual(value["decider"], "model:test-provider:test-model")
            self.assertFalse(value["presentedAsJev"])
            self.assertEqual(value["authorizationId"], self.authorization_id)
            self.assertNotIn("attestationId", value)
        self.assertEqual(decision["objects"][0]["selected"], ["base:generic"])
        self.assertEqual(decision["jevSession"], "not-used:host-model")
        self.assertFalse(decision["executionAuthorized"])
        events = self.journal()
        self.assertEqual(sum(e["event"] == "attempt-pending" for e in events), 2)
        self.assertEqual(sum(e["event"] == "host-model-selected" for e in events), 2)
        self.assertFalse(any(e["event"] == "probe" for e in events))
        router = self.router(self.authority(model_id="test-provider:test-model"))
        self.assertEqual(router.verify_decision(output["decisionRef"]), decision)

    def test_authorization_identity_cannot_be_borrowed(self):
        self.questions()
        envelope = {"kind": "img2.route-model-answers", "version": 1,
                    "authorizationId": "0" * 64, "answers": []}
        code, output = self.invoke(envelope=envelope)
        self.assertEqual((code, output["reasonCode"]), (3, "invalid-model-answers"))
        self.assertFalse(any(e["event"] == "attempt-pending" for e in self.journal()))

    def test_typed_answer_identity_and_schema_cannot_be_forged(self):
        question = self.questions()
        valid = fixtures.Stage0RouteTests.answer(question["state"], model="host-agent")
        for field, value in (("model", "other-model"), ("subject", {}), ("group", "quality-review"),
                             ("answers", {}), ("usage", {"input_tokens": -1, "output_tokens": 0})):
            with self.subTest(field=field):
                self.answers = [dict(valid, **{field: value})]
                code, output = self.invoke()
                self.assertEqual(code, 3, output)
                self.assertNotIn("questionsRef", output)
        self.assertFalse(any(e["event"] == "attempt-pending" for e in self.journal()))

    def test_workspace_run_request_sources_model_and_budget_bind_identity(self):
        original = self.authority().metadata()["authorizationId"]
        variants = [self.authority(model_id="another-model"), self.authority(token_budget=128000),
                    HostModelRouting(self.workspace, "another-run", "ref.png", request=self.request,
                                     sources=["objects.json"]),
                    HostModelRouting(self.workspace, "run-1", "ref.png",
                                     request=dict(self.request, intent=["static"]), sources=["objects.json"])]
        for variant in variants:
            self.assertNotEqual(variant.metadata()["authorizationId"], original)
        with tempfile.TemporaryDirectory() as directory:
            other = Path(directory).resolve()
            for name in ("ref.png", "objects.json"):
                (other / name).write_bytes((self.workspace / name).read_bytes())
            variant = HostModelRouting(other, "run-1", "ref.png", request=self.request, sources=["objects.json"])
            self.assertNotEqual(variant.metadata()["authorizationId"], original)
        for name in ("ref.png", "objects.json"):
            path = self.workspace / name
            raw = path.read_bytes()
            authority = self.authority()
            path.write_bytes(raw + b" ")
            with self.assertRaisesRegex(DecisionError, "host-model-request-changed"):
                authority.metadata()
            path.write_bytes(raw)

    def test_changed_active_request_invalidates_existing_controller_consumption(self):
        _, decision = self.complete()
        router = self.router()
        consultation = decision["objects"][0]["consultations"][-1]
        record = json.loads((self.workspace / consultation["decisionRef"]).read_bytes())
        context = json.loads((self.workspace / consultation["contextRef"]["path"]).read_bytes())
        router.controller.consume(record, context, consent=router.consent)
        DecisionController(self.workspace, "run-1", routing_authority=self.authority(token_budget=128000))
        with self.assertRaisesRegex(DecisionError, "host-model-request-unproven"):
            router.controller.consume(record, context, consent=router.consent)

    def test_no_authority_or_duck_typed_authority_cannot_consume_host_receipt(self):
        _, decision = self.complete()
        consultation = decision["objects"][0]["consultations"][-1]
        record = json.loads((self.workspace / consultation["decisionRef"]).read_bytes())
        context = json.loads((self.workspace / consultation["contextRef"]["path"]).read_bytes())
        controller = DecisionController(self.workspace, "run-1")
        with self.assertRaisesRegex(DecisionError, "host-model-request-unproven"):
            controller.consume(record, context, consent=self.authority())
        forged = Mock(workspace=self.workspace, run_id="run-1")
        with self.assertRaisesRegex(DecisionError, "invalid-attestation"):
            DecisionController(self.workspace, "run-1", routing_authority=forged)

    def test_host_authority_refuses_other_groups_and_reference_contexts(self):
        self.questions()
        router = self.router()
        authority = router.consent
        question = self.questions()
        # Validate full local bindings, not the path-free provider projection.
        context = copy.deepcopy(question["state"])
        context["bindings"] = copy.deepcopy(question["imageBindings"])
        for mutate in (lambda c: c.update(group="quality-review"),
                       lambda c: c["subject"].update(runId="other-run"),
                       lambda c: c.update(bindings=[]),
                       lambda c: c["requirements"]["request"].update(intent=["static"])):
            changed = copy.deepcopy(context)
            mutate(changed)
            with self.assertRaisesRegex(DecisionError, "host-model-binding-mismatch"):
                authority.validate_context(changed)
        context["group"] = "quality-review"
        with self.assertRaisesRegex(DecisionError, "consent-required"):
            ModelDecider(router.controller, authority).provider(context)

    def test_primary_uncertainty_and_dependency_vetoes_are_unchanged(self):
        discovery = fixtures.FixtureDiscovery(self.workspace)
        self.candidates = [discovery.candidate("interior"),
                           discovery.candidate("character", deps={"character-bpy>=4.3": False})]
        output, decision = self.complete(probability=.65, confidence=.9)
        row = decision["objects"][0]
        self.assertEqual(row["status"], "best-of-n")
        self.assertEqual(len(row["selected"]), 2)
        self.assertTrue(any(r["reasonCode"].startswith("dependency-missing:") for r in row["rejected"]))
        result = {"decision": decision, "decisionRef": output["decisionRef"], "sha256": output["sha256"]}
        router, harness = self.router(), Mock()
        parent_path = self.workspace / ".img2threejs/state.json"
        children_path = self.workspace / ".img2threejs/objects"
        self.assertFalse(parent_path.exists())
        self.assertFalse(children_path.exists())
        with patch.object(router, "verification_snapshot") as snapshot:
            with self.assertRaisesRegex(DecisionError, "route-execution-unresolved"):
                cli._create_children(router, result, harness, router.consent)
        snapshot.assert_not_called()
        harness.verify.assert_not_called()
        self.assertFalse(parent_path.exists())
        self.assertFalse(children_path.exists())

    def test_host_budget_cannot_bypass_existing_reservation(self):
        extra = ("--token-budget", "64000")
        self.answer(self.questions(*extra))
        self.answer(self.questions(*extra))
        code, output = self.invoke(*extra)
        self.assertEqual((code, output["reasonCode"]), (3, "budget-exhausted"))

    def test_explicit_jev_retains_release_guard_and_conflicts_are_parser_errors(self):
        argv = ["--workspace", str(self.workspace), "--run-id", "run-1", "--image", "ref.png",
                "--objects", "objects.json", "--decider", "jev"]
        output = io.StringIO()
        with patch.object(cli, "_approved_lock", side_effect=DecisionError("provider-unpinned")) as lock, \
                patch.object(cli, "HostModelRouting") as host, redirect_stdout(output):
            code = cli.main(argv)
        self.assertEqual((code, json.loads(output.getvalue())["reasonCode"]), (3, "provider-unpinned"))
        lock.assert_called_once()
        host.assert_not_called()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            cli.main([*argv, "--no-jev-account"])
        self.assertEqual(caught.exception.code, 2)

    def test_discovery_never_queries_jev_for_host_routing(self):
        authority = self.authority()
        controller = DecisionController(self.workspace, "run-1", routing_authority=authority)
        discovery = Discovery(controller)
        with patch.object(discovery, "run", return_value=(0, json.dumps({
                "harness": "fixture", "commands": ["capabilities"]}).encode())), \
                patch.object(discovery, "query", return_value={"status": "answered", "providers": []}) as query, \
                patch("stage0_route.discovery.read_selectors", return_value=None):
            discovery.enumerate()
        self.assertNotIn(JEV_EDGE, [call.args[0] for call in query.call_args_list])
        self.assertEqual(discovery.jev_status, "not-used:host-model")


    def test_crop_choice_is_incremental_and_uses_accumulated_answers(self):
        self.answer(self.questions(), bounds=[.7, .1, .95, .9])
        question = self.questions()
        self.assertEqual(question["state"]["subject"]["passId"], "crop-choice")
        self.answer(question, choice="model-crop")
        question = self.questions()
        self.assertEqual(question["state"]["subject"]["passId"], "routing")
        self.answer(question)
        code, output = self.invoke()
        self.assertEqual((code, output["status"]), (0, "decided"), output)
        self.assertEqual(self.router().verify_decision(output["decisionRef"])["phase"], "decided")

    def test_generic_host_route_creates_only_pending_children_with_real_authority(self):
        output, decision = self.complete()
        router = self.router()
        result = {"decision": decision, "decisionRef": output["decisionRef"], "sha256": output["sha256"]}
        harness = Mock()
        harness.argv = ("fixture-img2",)
        harness.runtime_provider = None
        harness.authority_runner = Mock(side_effect=AssertionError("generic route needs no external program"))
        with patch.object(cli, "registered_domains", return_value={}), \
                patch("domains.registered_domains", return_value={}):
            parent = cli._create_children(router, result, harness, router.consent)
            self.assertNotEqual(parent["status"], "complete")
            self.assertFalse(parent["routeDecision"]["executionAuthorized"])
            child_row = parent["objects"][0]
            child = json.loads((self.workspace / child_row["statePath"]).read_bytes())
            self.assertTrue(any(step["status"] == "pending" for step in child["checklist"]))
            with self.assertRaisesRegex(Exception, "child-terminal-gates-incomplete"):
                cli._complete_child(router, result, harness, router.consent, child_row["objectId"])
        harness.authority_runner.assert_not_called()


    def separate_lifecycle_router(self, consent=None):
        if consent is None:
            consent = Mock(return_value={"granted": True, "purposes": list(GROUPS),
                                         "sha256": "f" * 64, "tokenBudget": 2_000_000})
        authority = consent if type(consent) is route_no_account.RoutingNoAccount else None
        controller = DecisionController(self.workspace, "run-1", routing_authority=authority)
        return Router(self.workspace, "run-1", controller=controller, consent=consent,
                      discovery=fixtures.FixtureDiscovery(self.workspace, self.candidates))

    def host_record(self, decision):
        consultation = decision["objects"][0]["consultations"][-1]
        record = json.loads((self.workspace / consultation["decisionRef"]).read_bytes())
        context = json.loads((self.workspace / consultation["contextRef"]["path"]).read_bytes())
        return record, context

    def create_host_child(self):
        output, decision = self.complete()
        router = self.router()
        result = {"decision": decision, "decisionRef": output["decisionRef"], "sha256": output["sha256"]}
        harness = Mock()
        harness.argv = ("fixture-img2",)
        harness.runtime_provider = None
        harness.authority_runner = Mock(side_effect=AssertionError("generic route needs no external program"))
        with patch.object(cli, "registered_domains", return_value={}), \
                patch("domains.registered_domains", return_value={}):
            parent = cli._create_children(router, result, harness, router.consent)
        return result, parent["objects"][0], harness

    def test_separate_lifecycle_controller_has_only_transaction_scoped_host_proof(self):
        output, decision = self.complete()
        record, context = self.host_record(decision)
        router = self.separate_lifecycle_router()
        journal = router.controller.journal_path.read_bytes()
        with self.assertRaisesRegex(DecisionError, "host-model-request-unproven"):
            router.controller.consume(record, context, consent=router.consent)
        with router.controller.workspace_transaction(consent=router.consent) as verify:
            with self.assertRaisesRegex(DecisionError, "host-model-request-unproven"):
                verify(record, context)
            with self.assertRaisesRegex(DecisionError, "host-model-request-unproven"):
                verify.recorded(record, context)
            proof, metadata = verify.host_routing, verify.host_routing_metadata
            self.assertEqual(proof(record, context), record)
            self.assertEqual(metadata()["authorizationId"], output["authorizationId"])
            changed = copy.deepcopy(context)
            changed["requirements"]["request"]["intent"] = ["static"]
            with self.assertRaisesRegex(DecisionError, "context-digest-mismatch"):
                proof(record, changed)
            forged = dict(record, authorizationId="0" * 64)
            with self.assertRaisesRegex(DecisionError, "record-mismatch"):
                proof(forged, context)
        for callback, args in ((proof, (record, context)), (metadata, ())):
            with self.assertRaisesRegex(DecisionError, "transaction-closed"):
                callback(*args)
        self.assertIsNone(router.controller.routing_authority)
        self.assertEqual(router.controller.journal_path.read_bytes(), journal)
        self.assertEqual(router.verify_decision(output["decisionRef"]), decision)

    def test_separate_proof_rejects_changed_sources_and_replaced_active_request(self):
        _, decision = self.complete()
        record, context = self.host_record(decision)
        router = self.separate_lifecycle_router()
        for name in ("ref.png", "objects.json"):
            path = self.workspace / name
            raw = path.read_bytes()
            path.write_bytes(raw + b" ")
            with router.controller.workspace_transaction(consent=router.consent) as verify:
                with self.assertRaises(DecisionError):
                    verify.host_routing(record, context)
                with self.assertRaises(DecisionError):
                    verify.host_routing_metadata()
            path.write_bytes(raw)
        DecisionController(self.workspace, "run-1", routing_authority=self.authority(token_budget=128000))
        journal = router.controller.journal_path.read_bytes()
        with router.controller.workspace_transaction(consent=router.consent) as verify:
            with self.assertRaisesRegex(DecisionError, "host-model-request-unproven"):
                verify.host_routing(record, context)
        self.assertEqual(router.controller.journal_path.read_bytes(), journal)

    def test_host_created_child_completion_uses_separate_jev_consent_and_keeps_pending_gate(self):
        result, descriptor, harness = self.create_host_child()
        router = self.separate_lifecycle_router()
        parent_path = self.workspace / ".img2threejs/state.json"
        child_path = self.workspace / descriptor["statePath"]
        before = (parent_path.read_bytes(), child_path.read_bytes())
        with patch.object(cli, "registered_domains", return_value={}), \
                patch("domains.registered_domains", return_value={}):
            with self.assertRaisesRegex(ParentError, "child-terminal-gates-incomplete"):
                cli._complete_child(router, result, harness, router.consent, descriptor["objectId"])
        self.assertEqual((parent_path.read_bytes(), child_path.read_bytes()), before)
        router.consent.assert_called()
        harness.authority_runner.assert_not_called()

    def test_host_created_child_accepts_separate_live_no_account_grant_not_host_lifecycle_authority(self):
        import test_route_no_account as no_account_fixtures
        result, descriptor, harness = self.create_host_child()
        case = no_account_fixtures.NoAccountRoutingTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.workspace = self.workspace
        case.image = self.workspace / "ref.png"
        case.mint(purposes=list(GROUPS))
        grant = case.grant()
        router = self.separate_lifecycle_router(grant)
        self.assertNotEqual(grant.model_id, result["decision"]["modelId"])
        self.assertEqual(router.verify_decision(result["decisionRef"]), result["decision"])
        parent_path = self.workspace / ".img2threejs/state.json"
        before = parent_path.read_bytes()
        with patch.object(cli, "registered_domains", return_value={}), \
                patch("domains.registered_domains", return_value={}):
            with self.assertRaisesRegex(ParentError, "child-terminal-gates-incomplete"):
                cli._complete_child(router, result, harness, grant, descriptor["objectId"])
        self.assertEqual(parent_path.read_bytes(), before)
        case.mint(revoke=True)
        with patch.object(cli, "registered_domains", return_value={}), \
                patch("domains.registered_domains", return_value={}):
            with self.assertRaisesRegex(DecisionError, "attestation-revoked"):
                cli._complete_child(router, result, harness, grant, descriptor["objectId"])
        self.assertEqual(parent_path.read_bytes(), before)

    def test_separate_lifecycle_receipts_still_require_real_native_terminal_authority(self):
        from route_artifact import validate_artifact
        from test_route_authority import RouteAuthorityTests
        result, descriptor, harness = self.create_host_child()
        router = self.separate_lifecycle_router()
        fixture = RouteAuthorityTests()
        self.addCleanup(fixture.doCleanups)
        fixture.router = router
        state = fixture.journal_lifecycle(router.controller, descriptor, consent=router.consent)
        child = (self.workspace / descriptor["statePath"]).parent
        source = b"export function build() { return null; } // test data, not native acceptance"
        (child / "scene.ts").write_bytes(source)
        artifact = validate_artifact(child, {
            "objectId": descriptor["objectId"], "outputKind": "3d-object",
            "files": [{"path": "scene.ts", "sha256": hashlib.sha256(source).hexdigest()}],
            "frame": {"units": "m", "up": "+Y", "forward": "+Z"},
            "pivot": "bottom-centre", "scaleEstimate": 1.0, "rig": None})
        (child / "artifact.json").write_bytes(encode(artifact))
        parent_path = self.workspace / ".img2threejs/state.json"
        before = (parent_path.read_bytes(), (child / "state.json").read_bytes())
        with patch.object(cli, "registered_domains", return_value={}), \
                patch("domains.registered_domains", return_value={}):
            parent = cli._route_parent(router, result, harness, router.consent)
            with router.controller.workspace_transaction(consent=router.consent) as verify:
                quality = state["decisionRefs"][-1]
                quality_context, quality_record = quality["context"], quality["record"]
                with self.assertRaisesRegex(DecisionError, "host-model-routing-proof-required"):
                    verify.host_routing(quality_record, quality_context)
                with self.assertRaisesRegex(ParentError, "native-policy-unavailable"):
                    parent.verify_aggregate.verify_terminal(
                        verify, object_id=descriptor["objectId"], child_workspace=child,
                        state=state, artifact=artifact)
        self.assertEqual((parent_path.read_bytes(), (child / "state.json").read_bytes()), before)
        harness.authority_runner.assert_not_called()

    def test_separate_proof_verifies_host_round_triggers_and_links(self):
        additions = fixtures.Stage0RouteTests.proposals(2)
        additions["objects"] = additions["objects"][1:]
        raw = json.dumps({"kind": "img2.recorded-second-proposals", "version": 1,
            "imageSha256": hashlib.sha256((self.workspace / "ref.png").read_bytes()).hexdigest(),
            "firstRoundProposalSha256": canonical_digest(self.objects),
            "objects": additions["objects"]}).encode()
        (self.workspace / "second.json").write_bytes(raw)
        extra = ("--second-objects", "second.json", "--second-objects-sha256",
                 hashlib.sha256(raw).hexdigest())
        self.answer(self.questions(*extra), missing=1)
        for _ in range(6):
            code, output = self.invoke(*extra)
            if output["status"] == "decided":
                break
            self.assertEqual((code, output["status"]), (0, "questions-ready"), output)
            question = json.loads((self.workspace / output["questionsRef"]["path"]).read_bytes())
            self.answer(question)
        self.assertEqual(output["status"], "decided", output)
        decision = self.separate_lifecycle_router().verify_decision(output["decisionRef"])
        self.assertEqual(len(decision["proposalRounds"]), 2)
        self.assertEqual(len(decision["proposalRounds"][1]["triggerDecisionIds"]), 1)


    def test_cli_completed_host_route_preserves_routing_model_separate_from_lifecycle_model(self):
        import test_route_no_account as no_account_fixtures
        result, descriptor, harness = self.create_host_child()
        case = no_account_fixtures.NoAccountRoutingTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.workspace, case.image = self.workspace, self.workspace / "ref.png"
        case.mint(purposes=list(GROUPS))
        grant = case.grant()
        decision = result["decision"]
        self.assertNotEqual(decision["modelId"], grant.model_id)
        jev_consent = Mock(return_value={"granted": True, "purposes": list(GROUPS),
                                        "sha256": "e" * 64, "tokenBudget": 2_000_000})
        jev_consent.executable, jev_consent.last_failure = Path(sys.executable), None
        argv = ["--workspace", str(self.workspace), "--run-id", "run-1",
                "--complete-child", descriptor["objectId"], "--decision-ref", result["decisionRef"],
                "--harness-root", str(self.workspace / "harness"), "--harness-sha", "a" * 40,
                "--harness-executable", str(self.workspace / "harness/bin/img2.mjs"),
                "--harness-sha256", "b" * 64]

        def completed(router, loaded, pinned_harness, consent, object_id, *, policies=None):
            self.assertEqual(router.verify_decision(loaded["decisionRef"]), decision)
            consent()
            # Test-owned success boundary only; no native runtime/terminal acceptance claim.
            return {"status": "in-progress"}

        for mode, flags in (("jev", ["--decider", "jev"]),
                            ("no-account", ["--no-jev-account", "--image", "ref.png"])):
            with self.subTest(lifecycle=mode):
                output = io.StringIO()
                with patch.object(cli, "PinnedDiscovery", side_effect=lambda controller:
                        fixtures.FixtureDiscovery(self.workspace)), \
                        patch.object(cli, "HarnessPin", return_value=harness), \
                        patch.object(cli, "LiveConsent", return_value=jev_consent), \
                        patch.object(cli, "_approved_lock", return_value={}), \
                        patch.object(cli, "_complete_child", side_effect=completed), redirect_stdout(output):
                    code = cli.main([*argv, *flags])
                value = json.loads(output.getvalue())
                self.assertEqual((code, value["status"]), (0, "child-completed"), value)
                for key in ("decisionMode", "authorizationId", "presentedAsJev", "modelId",
                            "routingPolicyVersion", "allowedPurposes"):
                    self.assertEqual(value[key], decision[key])
                self.assertEqual(value["decisionRef"], result["decisionRef"])
                self.assertEqual(value["decider"], "model:" + decision["modelId"])
                self.assertNotIn("attestationId", value)
                self.assertFalse(value["terminalAcceptance"])
                self.assertFalse(value["deliverable"])
                if mode == "no-account":
                    self.assertEqual(value["lifecycleAuthority"], grant.metadata())
                    self.assertEqual(value["lifecycleAuthority"]["decider"], "model:" + grant.model_id)
                else:
                    self.assertNotIn("lifecycleAuthority", value)

        def revoked_after_completion(*args, **kwargs):
            completed(*args, **kwargs)
            case.mint(revoke=True)
            return {"status": "in-progress"}

        output = io.StringIO()
        with patch.object(cli, "PinnedDiscovery", side_effect=lambda controller:
                fixtures.FixtureDiscovery(self.workspace)), \
                patch.object(cli, "HarnessPin", return_value=harness), \
                patch.object(cli, "_complete_child", side_effect=revoked_after_completion), \
                redirect_stdout(output):
            code = cli.main([*argv, "--no-jev-account", "--image", "ref.png"])
        value = json.loads(output.getvalue())
        self.assertEqual((code, value["reasonCode"]), (3, "attestation-revoked"))
        self.assertEqual(value["decisionMode"], "no-jev-account-pending")
        self.assertFalse({"decider", "authorizationId", "lifecycleAuthority"} & value.keys())


    def test_answer_envelope_refuses_malformed_shapes_before_any_debit(self):
        self.questions()
        valid = {"kind": "img2.route-model-answers", "version": 1,
                 "authorizationId": self.authorization_id, "answers": []}
        variants = [
            None, [], dict(valid, kind="other"), dict(valid, version=True),
            dict(valid, version=1.0), dict(valid, authorizationId=None),
            dict(valid, attestationId=self.authorization_id),
            {key: value for key, value in valid.items() if key != "authorizationId"},
            dict(valid, answers={}), dict(valid, answers=[None]),
            dict(valid, answers=[{"contextDigest": 1}]),
            dict(valid, answers=[{"contextDigest": "0" * 64}] * 257),
        ]
        for envelope in variants:
            with self.subTest(envelope=envelope):
                # None is the invoke helper's sentinel; write that case as raw JSON.
                if envelope is None:
                    (self.workspace / "raw.json").write_text("null")
                    code, output = self.invoke("--model-answers", "raw.json")
                else:
                    code, output = self.invoke(envelope=envelope)
                self.assertEqual((code, output["reasonCode"]), (3, "invalid-model-answers"))
                self.assertNotIn("decider", output)
        self.assertFalse(any(e["event"] == "attempt-pending" for e in self.journal()))
        self.assertFalse((self.workspace / ".img2threejs/state.json").exists())

    def test_duplicate_answer_context_cannot_override_an_earlier_answer(self):
        question = self.questions()
        answer = fixtures.Stage0RouteTests.answer(question["state"], model="host-agent")
        conflicting = copy.deepcopy(answer)
        conflicting["answers"]["missingObjects"]["noul"] = 1
        code, output = self.invoke(envelope={
            "kind": "img2.route-model-answers", "version": 1,
            "authorizationId": self.authorization_id, "answers": [answer, conflicting]})
        self.assertEqual((code, output["reasonCode"]), (3, "invalid-model-answers"))
        self.assertFalse(any(e["event"] == "attempt-pending" for e in self.journal()))

    def test_answer_json_refuses_duplicate_keys_nonfinite_and_invalid_encoding(self):
        self.questions()
        for raw in (b'{"kind":"first","kind":"second"}', b'{"value":NaN}',
                    b'{"value":Infinity}', b'\xff', b'{"answers":'):
            with self.subTest(raw=raw):
                (self.workspace / "raw.json").write_bytes(raw)
                code, output = self.invoke("--model-answers", "raw.json")
                self.assertEqual(code, 3, output)
                self.assertNotIn("questionsRef", output)
                self.assertNotIn("decider", output)
        self.assertFalse(any(e["event"] == "attempt-pending" for e in self.journal()))

    def test_oversized_answers_are_refused_without_consuming_a_request(self):
        self.questions()
        (self.workspace / "large.json").write_bytes(b" " * (2 * 1024 * 1024 + 1))
        code, output = self.invoke("--model-answers", "large.json")
        self.assertEqual(code, 3, output)
        self.assertNotIn("questionsRef", output)
        self.assertNotIn("decider", output)
        self.assertFalse(any(e["event"] == "attempt-pending" for e in self.journal()))

    def test_answer_paths_cannot_escape_or_follow_symlink_files_or_directories(self):
        self.questions()
        document = {"kind": "img2.route-model-answers", "version": 1,
                    "authorizationId": self.authorization_id, "answers": []}
        target = self.workspace / "good.json"
        target.write_text(json.dumps(document))
        (self.workspace / "link.json").symlink_to(target)
        (self.workspace / "linked-directory").symlink_to(self.workspace, target_is_directory=True)
        for path in ("../good.json", str(target), "link.json", "linked-directory/good.json"):
            with self.subTest(path=path):
                code, output = self.invoke("--model-answers", path)
                self.assertEqual((code, output["reasonCode"]), (3, "unsafe-path"))
                self.assertNotIn("decider", output)
        self.assertFalse(any(e["event"] == "attempt-pending" for e in self.journal()))

    def test_old_envelope_cannot_resume_changed_image_proposals_request_model_or_budget(self):
        question = self.questions()
        answer = fixtures.Stage0RouteTests.answer(question["state"], model="host-agent")
        envelope = {"kind": "img2.route-model-answers", "version": 1,
                    "authorizationId": self.authorization_id, "answers": [answer]}
        for name in ("ref.png", "objects.json"):
            with self.subTest(source=name):
                path = self.workspace / name
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b" ")
                    code, output = self.invoke(envelope=envelope)
                    self.assertEqual((code, output["reasonCode"]), (3, "invalid-model-answers"))
                finally:
                    path.write_bytes(original)
        for flags in (("--intent", "static"), ("--explicit-route", "base:generic"),
                      ("--expected-output", "2d"), ("--model-id", "another-model"),
                      ("--token-budget", "128000")):
            with self.subTest(flags=flags):
                code, output = self.invoke(*flags, envelope=envelope)
                self.assertEqual((code, output["reasonCode"]), (3, "invalid-model-answers"))
                self.assertNotIn("decider", output)
        self.assertFalse(any(e["event"] == "attempt-pending" for e in self.journal()))

    def test_identical_files_in_another_workspace_cannot_borrow_answer_authorization(self):
        question = self.questions()
        envelope = {"kind": "img2.route-model-answers", "version": 1,
                    "authorizationId": self.authorization_id,
                    "answers": [fixtures.Stage0RouteTests.answer(question["state"], model="host-agent")]}
        original = self.workspace
        other = original / "other-workspace"
        other.mkdir()
        for name in ("ref.png", "objects.json"):
            (other / name).write_bytes((original / name).read_bytes())
        self.workspace = other
        code, output = self.invoke(envelope=envelope)
        self.assertEqual((code, output["reasonCode"]), (3, "invalid-model-answers"))
        self.assertFalse(any(e["event"] == "attempt-pending" for e in self.journal()))

    def test_another_run_cannot_steal_the_workspace_lease_or_change_its_journal(self):
        self.questions()
        journal_path = self.workspace / ".img2threejs/route/journal.jsonl"
        before = journal_path.read_bytes()
        code, output = self.invoke("--run-id", "another-run")
        self.assertEqual((code, output["reasonCode"]), (3, "workspace-busy"))
        self.assertEqual(journal_path.read_bytes(), before)
        self.assertNotIn("decider", output)

    def test_repeated_partial_answers_wait_without_redebiting_the_completed_pass(self):
        self.answer(self.questions())
        next_question = self.questions()
        for _ in range(3):
            question = self.questions()
            self.assertEqual(question["contextDigest"], next_question["contextDigest"])
        events = self.journal()
        self.assertEqual(sum(e["event"] == "attempt-pending" for e in events), 1)
        self.assertEqual(sum(e["data"]["tokens"] for e in events if e["event"] == "attempt-result"), 30)
        self.assertFalse((self.workspace / ".img2threejs/state.json").exists())
        self.answer(next_question)
        code, output = self.invoke()
        self.assertEqual((code, output["status"]), (0, "decided"), output)

    def test_exact_reservation_and_confidence_boundaries_allow_idempotent_completed_replay(self):
        # Fixture localization debits 30; the next reservation is exactly 64,000.
        flags = ("--token-budget", "64030")
        output, decision = self.complete(extra=flags, confidence=.5, fits=.5)
        for _ in range(3):
            code, replay = self.invoke(*flags)
            self.assertEqual((code, replay["status"]), (0, "decided"), replay)
            current = json.loads((self.workspace / replay["decisionRef"]).read_bytes())
            self.assertEqual(current["objects"][0]["selected"], decision["objects"][0]["selected"])
            self.assertEqual(replay["authorizationId"], output["authorizationId"])
        events = self.journal()
        self.assertEqual(sum(e["event"] == "attempt-pending" for e in events), 2)
        self.assertEqual(sum(e["data"]["tokens"] for e in events if e["event"] == "attempt-result"), 60)

    def test_default_host_completion_cannot_implicitly_escalate_to_lifecycle_authority(self):
        output, _ = self.complete()
        journal_path = self.workspace / ".img2threejs/route/journal.jsonl"
        before = journal_path.read_bytes()
        argv = ["--workspace", str(self.workspace), "--run-id", "run-1",
                "--complete-child", "object-0", "--decision-ref", output["decisionRef"]]
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            cli.main(argv)
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(journal_path.read_bytes(), before)
        self.assertFalse((self.workspace / ".img2threejs/state.json").exists())

    def test_no_fit_cannot_authorize_the_only_available_route(self):
        self.answer(self.questions())
        self.answer(self.questions(), fits=.49)
        code, output = self.invoke()
        self.assertEqual((code, output["status"]), (3, "request-input"))
        self.assertNotIn("decider", output)
        decision = json.loads((self.workspace / output["decisionRef"]).read_bytes())
        self.assertEqual(decision["phase"], "suspended")
        self.assertEqual(decision["suspendedAt"]["reasonCode"], "none-fits")
        self.assertFalse(decision["executionAuthorized"])
        self.assertEqual(decision["objects"][0]["status"], "request-input")
        self.assertFalse((self.workspace / ".img2threejs/state.json").exists())

    def test_low_confidence_cannot_authorize_the_only_available_route(self):
        self.answer(self.questions())
        self.answer(self.questions(), confidence=.499999)
        code, output = self.invoke()
        self.assertEqual((code, output["status"]), (3, "request-input"))
        self.assertNotIn("decider", output)
        decision = json.loads((self.workspace / output["decisionRef"]).read_bytes())
        self.assertEqual(decision["phase"], "suspended")
        self.assertEqual(decision["suspendedAt"]["reasonCode"], "choice-uncertain")
        self.assertFalse(decision["executionAuthorized"])
        self.assertEqual(decision["objects"][0]["status"], "request-input")
        self.assertFalse((self.workspace / ".img2threejs/state.json").exists())

    def test_model_selection_does_not_supply_paid_route_action_consent(self):
        self.candidates = [fixtures.FixtureDiscovery(self.workspace).candidate("interior", cost="paid")]
        self.answer(self.questions())
        self.answer(self.questions())
        code, output = self.invoke()
        self.assertEqual((code, output["status"]), (3, "request-input"))
        self.assertNotIn("decider", output)
        decision = json.loads((self.workspace / output["decisionRef"]).read_bytes())
        self.assertEqual(decision["suspendedAt"]["reasonCode"], "route-action-consent-required")
        self.assertEqual(decision["objects"][0]["status"], "request-input")
        self.assertFalse(decision["executionAuthorized"])
        self.assertFalse((self.workspace / ".img2threejs/state.json").exists())

    def test_explicit_2d_without_objects_still_requires_a_real_typed_route_answer(self):
        self.candidates = [fixtures.FixtureDiscovery(self.workspace).candidate("pixi2d", output="2d-scene")]
        flags = ("--expected-output", "2d")
        question = self.questions(*flags, omit_objects=True)
        self.assertEqual(question["state"]["subject"]["passId"], "routing")
        self.answer(question)
        code, output = self.invoke(*flags, omit_objects=True)
        self.assertEqual((code, output["status"]), (0, "decided"), output)
        self.assertEqual(output["decider"], "model:host-agent")
        decision = json.loads((self.workspace / output["decisionRef"]).read_bytes())
        self.assertTrue(decision["skipComposition"])
        self.assertEqual(decision["objects"][0]["selected"], [self.candidates[0]["id"]])
        self.assertFalse(decision["executionAuthorized"])

    def test_missing_explicit_2d_provider_cannot_silently_become_3d(self):
        code, output = self.invoke("--expected-output", "2d", omit_objects=True)
        self.assertEqual((code, output["status"]), (3, "request-input"))
        self.assertNotIn("questionsRef", output)
        self.assertNotIn("decider", output)
        decision = json.loads((self.workspace / output["decisionRef"]).read_bytes())
        self.assertEqual(decision["phase"], "suspended")
        self.assertEqual(decision["suspendedAt"]["reasonCode"], "explicit-2d-unavailable")
        self.assertEqual(decision["objects"][0]["selected"], [])

    def test_unicode_and_quoted_workspace_and_image_paths_can_complete_normally(self):
        original = self.workspace
        self.workspace = original / "không gian ' có dấu"
        self.workspace.mkdir()
        for name in ("ref.png", "objects.json"):
            (self.workspace / name).write_bytes((original / name).read_bytes())
        (self.workspace / "ảnh gốc.png").write_bytes((original / "ref.png").read_bytes())
        output, decision = self.complete(extra=("--image", "ảnh gốc.png"))
        self.assertEqual(output["decisionMode"], "host-model")
        self.assertEqual(decision["objects"][0]["selected"], ["base:generic"])

    def test_corrupted_journal_cannot_be_discarded_to_return_cached_success(self):
        self.complete()
        journal_path = self.workspace / ".img2threejs/route/journal.jsonl"
        corrupt = journal_path.read_bytes() + b'{"event":'
        journal_path.write_bytes(corrupt)
        code, output = self.invoke()
        self.assertEqual(code, 3, output)
        self.assertNotIn("decider", output)
        self.assertNotIn("questionsRef", output)
        self.assertEqual(journal_path.read_bytes(), corrupt)
        self.assertFalse((self.workspace / ".img2threejs/state.json").exists())


if __name__ == "__main__":
    unittest.main()
