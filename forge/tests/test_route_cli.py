"""Offline route CLI wiring/probes. All provider execution boundaries are mocked."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import shutil
import tarfile
from types import SimpleNamespace
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import Mock, patch

FORGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORGE))
sys.path.insert(0, str(FORGE / "_shared"))
SPEC = importlib.util.spec_from_file_location("img2_route_cli", FORGE / "route.py")
route_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(route_cli)
from decision_control import DecisionController, DecisionError, GROUPS, canonical_digest
from jev_cli_bridge import REQUIRED_PROVIDER_FILES
from stage0_route.images import png
from route_eligibility import evaluate_candidate


class RouteCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.provider = self.root / "provider"
        (self.provider / "tools").mkdir(parents=True)
        for relative in REQUIRED_PROVIDER_FILES:
            (self.provider / relative).write_bytes(b"reviewed offline fixture")
        self.release = {"resolvedSha": "a" * 40, "contractVersion": "jev-v4-four-decisions", "files": {
            relative: hashlib.sha256((self.provider / relative).read_bytes()).hexdigest()
            for relative in REQUIRED_PROVIDER_FILES}}
        self.row = {"plugin": "jev", "resolvedSha": self.release["resolvedSha"], "dir": str(self.provider)}
        self.discovery = Mock()
        self.discovery.workspace = self.workspace
        self.discovery.jev = copy.deepcopy(self.row)
        self.discovery.jev_status = "present"
        self.discovery.query.return_value = {"status": "answered", "providers": [copy.deepcopy(self.row)]}
        self.discovery._pinned_file.side_effect = lambda row, relative: (self.provider / relative).read_bytes()
        self.grant = {"granted": True, "purposes": list(GROUPS), "sha256": "b" * 64, "tokenBudget": 128000}
        self.auth = {"kind": "img2.jev-command", "version": 1, "status": "present", "reasonCode": "credential-present"}
        self.status = {"kind": "img2.jev-command", "version": 1, "status": "consent-updated", **self.grant}
        self.secret = "not-real-private-api-key"
        self.argvs = []
        self.result = {"decision": {"phase": "decided", "executionAuthorized": False},
                       "decisionRef": ".img2threejs/route/decision-" + "c" * 64 + ".json", "sha256": "c" * 64}
        (self.workspace / "image.png").write_bytes(png(4, 4, 3, bytes([255]) * 48))
        self.objects = {"kind": "img2.object-proposals", "version": 1, "objects": [
            {"objectId": "object-1", "label": "cube", "role": "prop", "approxBox": [0, 0, 1, 1]}]}
        (self.workspace / "objects.json").write_text(json.dumps(self.objects))
        self.git_argvs = []
        self.git_blob_override = None
        actual_run_bytes = route_cli._run_bytes
        def git_fixture(argv, workspace, **kwargs):
            if Path(argv[0]).name == "git" and "show" in argv:
                self.git_argvs.append(list(argv))
                relative = argv[-1].split(":", 1)[1]
                data = self.git_blob_override
                return 0, (self.provider / relative).read_bytes() if data is None else data
            return actual_run_bytes(argv, workspace, **kwargs)
        self.git_patch = patch.object(route_cli, "_run_bytes", side_effect=git_fixture)
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)

    def probe(self, argv, workspace, **kwargs):
        self.argvs.append(list(argv))
        self.assertEqual(workspace, self.workspace)
        self.assertEqual(Path(argv[1]), self.provider / "tools/jev.py")
        if argv[2] == "auth-status":
            return 0, copy.deepcopy(self.auth)
        self.assertEqual(argv[2:4], ["consent", "decision-status"])
        self.assertNotIn("authorize-decisions", argv)
        return 0, copy.deepcopy(self.status)

    def live(self):
        return route_cli.LiveConsent(self.discovery, self.release, "run-1", Path(sys.executable))

    def args(self, *extra):
        return ["--workspace", str(self.workspace), "--run-id", "run-1", "--image", "image.png",
                "--objects", "objects.json", *extra]

    def assert_reason(self, reason, callback):
        with self.assertRaises(DecisionError) as caught:
            callback()
        self.assertEqual(caught.exception.reason_code, reason)
        self.assertNotIn(self.secret, str(caught.exception))

    def invoke(self, args=None, *, route_callback=None):
        stdout = io.StringIO()
        router = Mock()
        def run(**kwargs):
            # Mirrors Router's actual ordering: discover before the first consent
            # callback; provider factory construction itself must not need .jev.
            self.discovery.enumerate()
            if route_callback:
                return route_callback(self.router_kwargs["consent"])
            self.router_kwargs["consent"]()
            return copy.deepcopy(self.result)
        router.route.side_effect = run
        def construct_router(*args, **kwargs):
            self.router_kwargs = kwargs
            return router
        self.discovery.jev = None
        self.discovery.jev_status = None
        def discover():
            self.discovery.jev = copy.deepcopy(self.row)
            self.discovery.jev_status = "present"
            return [], []
        self.discovery.enumerate.side_effect = discover
        with patch.object(route_cli, "_approved_lock", return_value=copy.deepcopy(self.release)), \
                patch.object(route_cli, "PinnedDiscovery", return_value=self.discovery), \
                patch.object(route_cli, "JevProviderFactory") as factory, \
                patch.object(route_cli, "Router", side_effect=construct_router), \
                patch.object(route_cli, "_run_cli", side_effect=self.probe), redirect_stdout(stdout):
            code = route_cli.main(args or self.args())
        output = json.loads(stdout.getvalue())
        return code, output, router, factory

    def test_generic_choice_exposes_plugin_dependency_rejections(self):
        self.result["decision"]["objects"] = [
            {"objectId": "object-1", "selected": ["base:generic"], "rejected": [
                {"id": "character:" + "d" * 64,
                 "reasonCode": "dependency-missing:character-bpy>=4.3"},
                {"id": "interior", "reasonCode": "provider-unavailable"}]}]
        code, output, _, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(output["pluginRejections"], [
            {"objectId": "object-1", "id": "character:" + "d" * 64,
             "reasonCode": "dependency-missing:character-bpy>=4.3"},
            {"objectId": "object-1", "id": "interior", "reasonCode": "provider-unavailable"}])

    def test_real_argument_parsing_and_program_owned_dynamic_wiring(self):
        code, output, router, factory = self.invoke(self.args("--intent", "rig", "--intent", "static", "--explicit-route", "base:generic"))
        self.assertEqual(code, 0)
        self.assertEqual(output["mode"], "decision-only")
        self.assertFalse(output["executionAuthorized"])
        self.assertEqual(output["status"], "decided")
        self.assertNotIn("deliverable", output)
        self.assertNotIn("objects", output)
        self.assertIsNone(self.router_kwargs["fallback"])
        self.assertIs(self.router_kwargs["discovery"], self.discovery)
        self.assertIs(self.router_kwargs["provider_factory"], factory.return_value)
        self.assertEqual(factory.call_args.kwargs["approved_release"], self.release)
        self.assertEqual(router.route.call_args.kwargs, {"image": "image.png", "objects": self.objects,
                         "expected_output": "3d", "intent": ["rig", "static"], "explicit_route": "base:generic"})
        self.assertTrue(self.argvs)

    def test_2d_needs_no_objects_and_does_not_claim_composition(self):
        args = ["--workspace", str(self.workspace), "--run-id", "run-1", "--image", "image.png", "--expected-output", "2d"]
        code, output, router, _ = self.invoke(args)
        self.assertEqual(code, 0)
        self.assertIsNone(router.route.call_args.kwargs["objects"])
        self.assertEqual(router.route.call_args.kwargs["expected_output"], "2d")
        self.assertFalse(output["executionAuthorized"])

    def test_3d_requires_objects_before_controller_or_probe(self):
        stderr = io.StringIO()
        with patch.object(route_cli, "DecisionController") as controller, redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                route_cli.main(["--run-id", "run-1", "--image", "image.png"])
        self.assertEqual(caught.exception.code, 2)
        controller.assert_not_called()

    def test_workspace_relative_paths_and_symlink_sources_are_enforced(self):
        for name in ("../outside.png", str(self.workspace / "image.png")):
            with self.subTest(name=name), patch.object(route_cli, "DecisionController") as controller:
                args = self.args()
                args[args.index("--image") + 1] = name
                code, output, _, _ = self.invoke(args)
                self.assertEqual(code, 3)
                self.assertEqual(output["reasonCode"], "unsafe-path")
                controller.assert_not_called()
        (self.workspace / "link.png").symlink_to(self.workspace / "image.png")
        args = self.args()
        args[args.index("--image") + 1] = "link.png"
        code, output, _, _ = self.invoke(args)
        self.assertEqual((code, output["reasonCode"]), (3, "unsafe-path"))

    def test_live_consent_executes_only_verified_read_only_commands(self):
        consent = self.live()
        with patch.object(route_cli, "_run_cli", side_effect=self.probe):
            self.assertEqual(consent(), self.grant)
        self.assertEqual([argv[2] for argv in self.argvs], ["auth-status", "consent"])
        self.assertEqual(self.argvs[-1][-4:], ["--workspace", str(self.workspace), "--run-id", "run-1"])
        checked = {argv[-1].split(":", 1)[1] for argv in self.git_argvs}
        self.assertEqual(checked, set(REQUIRED_PROVIDER_FILES))
        self.assertNotIn(self.secret, json.dumps(self.argvs))
        self.discovery.query.assert_not_called()
        self.discovery._pinned_file.assert_not_called()

    def test_authentication_failure_is_actionable_not_implicit_decline(self):
        def fail_auth(argv, workspace, **kwargs):
            if argv[2] == "auth-status":
                return 3, {"kind": "img2.jev-command", "version": 1, "status": "missing", "reasonCode": "credential-missing"}
            self.assertEqual(argv[2:4], ["consent", "decision-status"])
            return 0, copy.deepcopy(self.status)
        self.probe = fail_auth
        code, output, _, _ = self.invoke()
        self.assertEqual((code, output["reasonCode"]), (3, "credential-missing"))
        self.assertEqual(output["requestedCommand"], "img2-jev login")
        self.assertNotIn("login-declined", json.dumps(output))
        self.assertFalse(output["executionAuthorized"])

    def test_explicit_decline_enables_only_journalled_fallback_without_credentials(self):
        from _shared.placement_decision import placement_context
        self.status.update(granted=False, purposes=[])
        def probe(argv, workspace, **kwargs):
            if argv[2] == "auth-status":
                return 3, {"kind": "img2.jev-command", "version": 1, "status": "missing", "reasonCode": "credential-missing"}
            return self.probe(argv, workspace, **kwargs)
        sha = hashlib.sha256((self.workspace / "image.png").read_bytes()).hexdigest()
        bindings = [{"role": role, "path": "image.png", "sha256": sha} for role in
                    ("reference", "inventory", "artifact", "size-prior", "camera", "depth", "support")]
        context = placement_context(
            {"runId": "run-1", "objectId": "object-1", "revision": 1, "passId": "unused"},
            bindings, question="size", options=[{"id": "size-0", "value": [1, 1, 1]}])
        provider = Mock(side_effect=AssertionError("declined Jev must never be called"))
        availability = Mock(side_effect=AssertionError("explicit decline already proves unavailability"))
        def fallback(projected):
            return {"kind": "img2.decision-advisory", "version": 1, "group": projected["group"],
                    "subject": projected["subject"], "contextDigest": canonical_digest(projected),
                    "model": "offline-consent-fixture",
                    "answers": {"selection": {"type": "choice", "choice": "size-0", "confidence": 1,
                                             "probabilities": {"size-0": 1}},
                                "anyFits": {"type": "noul", "noul": 1}},
                    "usage": {"input_tokens": 2, "output_tokens": 1}}
        controller = DecisionController(self.workspace, "run-1")
        consent = self.live()
        with patch.object(route_cli, "_run_cli", side_effect=probe):
            record = controller.consult(context, consent=consent, provider=provider,
                                        availability=availability, fallback=fallback, request_token_limit=128)
            self.assertEqual(controller.consume(record, context, consent=consent), record)
        self.assertEqual(record["status"], "answered")
        self.assertEqual(record["unavailableReason"], "consent-declined")
        self.assertEqual(record["decider"], "model:offline-consent-fixture")
        provider.assert_not_called()
        availability.assert_not_called()

    def test_missing_or_refused_consent_is_preserved_without_enrollment(self):
        for reason in ("consent-required", "consent-declined"):
            with self.subTest(reason=reason):
                def failure(argv, workspace, **kwargs):
                    self.argvs.append(list(argv))
                    return ((0, copy.deepcopy(self.auth)) if argv[2] == "auth-status" else
                            (2, {"kind": "img2.jev-command", "version": 1, "status": "request-input", "reasonCode": reason}))
                self.probe = failure
                code, output, _, _ = self.invoke()
                self.assertEqual((code, output["reasonCode"]), (3, reason))
                self.assertEqual(output["requestedCommand"], "img2-jev consent authorize-decisions")
                self.assertNotIn("authorize-decisions", [part for argv in self.argvs for part in argv])
                self.assertFalse(output["executionAuthorized"])

    def test_provider_pin_mismatch_fails_before_any_probe(self):
        consent = self.live()
        (self.provider / "tools/jev_consent.py").write_bytes(b"dirty unreviewed bytes")
        with patch.object(route_cli, "_run_cli") as run:
            self.assert_reason("stale-evidence", consent)
            run.assert_not_called()

    def test_git_and_actual_worktree_must_both_match_release_lock(self):
        consent = self.live()
        self.git_blob_override = b"foreign git object"
        with patch.object(route_cli, "_run_cli") as run:
            self.assert_reason("provider-pin-mismatch", consent)
            run.assert_not_called()

    def test_consent_is_lock_safe_inside_controller_transaction(self):
        from decision_control import DecisionController
        controller = DecisionController(self.workspace, "run-1")
        consent = self.live()
        with controller._locked(), patch.object(route_cli, "_run_cli", side_effect=self.probe):
            self.assertEqual(consent(), self.grant)
        self.discovery.query.assert_not_called()
        self.discovery._pinned_file.assert_not_called()

    def test_unproven_installation_is_not_reported_as_missing_credentials(self):
        self.discovery.jev_status = "availability-unproven"
        with patch.object(route_cli, "_run_cli") as run:
            self.assert_reason("availability-unproven", self.live())
            run.assert_not_called()

    def test_rejected_authentication_in_router_result_requests_login(self):
        result = copy.deepcopy(self.result)
        result["decision"].update(phase="suspended", suspendedAt={"reasonCode": "credential-rejected"})
        output = route_cli._output(result=result)
        self.assertEqual(output["reasonCode"], "credential-rejected")
        self.assertEqual(output["requestedCommand"], "img2-jev login")
        self.assertFalse(output["executionAuthorized"])

    def test_exact_grant_fields_reject_secrets_and_legacy_purposes(self):
        for status in ({**self.status, "secret": self.secret}, {**self.status, "purposes": ["routing"]}):
            with self.subTest(status=status):
                self.status = status
                with patch.object(route_cli, "_run_cli", side_effect=self.probe):
                    self.assert_reason("malformed", self.live())

    def test_status_failure_does_not_echo_child_secret_text(self):
        def failure(argv, workspace, **kwargs):
            if argv[2] == "auth-status":
                return 0, self.auth
            return 2, {"kind": "img2.jev-command", "version": 1, "status": "request-input",
                       "reasonCode": self.secret, "reason": self.secret}
        self.probe = failure
        code, output, _, _ = self.invoke()
        self.assertEqual((code, output["reasonCode"]), (3, "malformed"))
        self.assertNotIn(self.secret, json.dumps(output))

    def test_real_release_lock_is_fixed_base_owned_input(self):
        lock = route_cli._approved_lock()
        self.assertEqual(lock["contractVersion"], "jev-v4-four-decisions")
        self.assertTrue(REQUIRED_PROVIDER_FILES <= lock["files"].keys())
        parser = route_cli.build_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(self.args("--release-lock", "workspace-supplied.json"))

    def test_create_request_with_missing_auth_stays_decision_only(self):
        def failure(argv, workspace, **kwargs):
            return 3, {"kind": "img2.jev-command", "version": 1, "status": "missing", "reasonCode": "credential-missing"}
        self.probe = failure
        flags = ["--create-children", "--harness-root", str(self.root / "harness"),
                 "--harness-sha", "d" * 40, "--harness-executable", str(self.root / "harness/bin/img2.mjs"),
                 "--harness-sha256", "e" * 64]
        harness = SimpleNamespace(argv=("fixture-harness",), runner=Mock())
        with patch.object(route_cli, "HarnessPin", return_value=harness):
            code, value, _, _ = self.invoke(self.args(*flags))
        self.assertEqual((code, value["reasonCode"]), (3, "credential-missing"))
        self.assertEqual(value["mode"], "decision-only")
        self.assertFalse(value["childrenCreated"])
        self.assertFalse(value["terminalAcceptance"])
        self.assertFalse(value["deliverable"])
        self.assertFalse((self.workspace / ".img2threejs/state.json").exists())

    def test_pinned_decision_only_routing_never_creates_a_parent(self):
        harness = SimpleNamespace(argv=("approved-harness",), runner=Mock())
        flags = ["--harness-root", str(self.root / "harness"),
                 "--harness-sha", "d" * 40, "--harness-executable", str(self.root / "harness/bin/img2.mjs"),
                 "--harness-sha256", "e" * 64]
        with patch.object(route_cli, "HarnessPin", return_value=harness):
            code, output, unused_router, unused_factory = self.invoke(self.args(*flags))
        self.assertEqual((code, output["status"]), (0, "decided"))
        self.assertEqual(output["mode"], "decision-only")
        self.assertFalse(output["executionAuthorized"])
        self.assertEqual(self.discovery.harness, harness.argv)
        self.assertIs(self.discovery.runner, harness.runner)
        self.assertFalse((self.workspace / ".img2threejs/state.json").exists())

    def test_partial_routing_harness_approval_refuses_before_controller_creation(self):
        with patch.object(route_cli, "DecisionController") as controller, \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            route_cli.main(self.args("--harness-root", str(self.root / "harness")))
        controller.assert_not_called()

    def second_proposal_source(self, **overrides):
        value = {
            "kind": "img2.recorded-second-proposals", "version": 1,
            "imageSha256": hashlib.sha256((self.workspace / "image.png").read_bytes()).hexdigest(),
            "firstRoundProposalSha256": canonical_digest(self.objects),
            "objects": [{"objectId": "object-2", "label": "sphere", "role": "prop",
                         "approxBox": [0, 0, .5, .5], "observations": {}}],
            **overrides,
        }
        path = self.workspace / "second.json"
        path.write_text(json.dumps(value))
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        return value, path, sha

    def test_second_proposal_cannot_be_consumed_for_another_image_or_initial_inventory(self):
        value, unused_path, sha = self.second_proposal_source()
        supply, unused_binding = route_cli._recorded_second_proposals(
            self.workspace, "second.json", sha, image="image.png", objects=self.objects)
        request = {"kind": "img2.decomposition-proposal-request", "version": 1, "round": 2,
                   "imageSha256": value["imageSha256"],
                   "firstRoundSha256": value["firstRoundProposalSha256"]}
        for field in ("imageSha256", "firstRoundSha256"):
            with self.subTest(field=field):
                self.assert_reason("second-proposal-context-mismatch",
                                   lambda: supply({**request, field: "0" * 64}))
        self.assertEqual([obj["objectId"] for obj in supply(request)["objects"]], ["object-2"])

    def test_changed_second_proposal_source_is_not_consumed_after_initial_approval(self):
        value, path, sha = self.second_proposal_source()
        supply, unused_binding = route_cli._recorded_second_proposals(
            self.workspace, "second.json", sha, image="image.png", objects=self.objects)
        value["objects"][0]["label"] = "replacement"
        path.write_text(json.dumps(value))
        self.assert_reason("stale-evidence", lambda: supply({
            "kind": "img2.decomposition-proposal-request", "version": 1, "round": 2,
            "imageSha256": value["imageSha256"],
            "firstRoundSha256": value["firstRoundProposalSha256"]}))

    def test_second_source_rejects_wrong_initial_scope_and_existing_object_replacement(self):
        for changes in ({"firstRoundProposalSha256": "0" * 64},
                        {"objects": copy.deepcopy(self.objects["objects"])}):
            value, unused_path, sha = self.second_proposal_source(**changes)
            with self.subTest(changes=changes), self.assertRaises(DecisionError):
                route_cli._recorded_second_proposals(
                    self.workspace, "second.json", sha, image="image.png", objects=self.objects)

    def test_incomplete_second_source_flags_fail_before_any_decision_session(self):
        for flags in (["--second-objects", "second.json"], ["--second-objects-sha256", "0" * 64]):
            with self.subTest(flags=flags), patch.object(route_cli, "DecisionController") as controller, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                route_cli.main(self.args(*flags))
            self.assertEqual(caught.exception.code, 2)
            controller.assert_not_called()

    def test_git_verification_environment_contains_no_host_credentials(self):
        with patch.dict(route_cli.os.environ, {"TYPESAFE_API_KEY": self.secret, "GIT_CONFIG_COUNT": "99"}):
            consent = self.live()
        self.assertNotIn("TYPESAFE_API_KEY", consent.git_env)
        self.assertNotIn("GIT_CONFIG_COUNT", consent.git_env)
        self.assertEqual(consent.git_env["GIT_NO_LAZY_FETCH"], "1")

    def test_availability_never_probes_auth_after_unproven_typed_edge(self):
        self.discovery.consent_probe = Mock()
        self.discovery.query.return_value = {"status": "data-fault", "providers": []}
        self.assertEqual(route_cli.PinnedDiscovery.availability(self.discovery), {"status": "availability-unproven"})
        self.discovery.consent_probe.auth_status.assert_not_called()

    def test_probe_runner_is_bounded_no_shell_and_discards_exception_text(self):
        with patch.object(route_cli.subprocess, "Popen", side_effect=OSError(self.secret)) as spawn:
            self.assert_reason("provider-error", lambda: route_cli._run_cli(["pinned-cli", "auth-status"], self.workspace))
        self.assertFalse(spawn.call_args.kwargs["shell"])
        self.assertTrue(spawn.call_args.kwargs["start_new_session"])
        self.assertEqual(spawn.call_args.kwargs["stdin"], route_cli.subprocess.DEVNULL)


class ChildInitializationTests(unittest.TestCase):
    """Actual Router journal/RouteAuthority/RouteParent; only external providers mocked."""
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).parent))
        import test_route_authority as authority_fixtures
        self.case = authority_fixtures.RouteAuthorityTests("runTest")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.harness = SimpleNamespace(argv=("fixture-img2",), runtime_provider=self.case.runtime_row,
                                       runner=self.case.command, authority_runner=self.case.command, verify=Mock())
        self.case.pin(self.case.plugin, "domain.json", json.dumps({"id": "pixi2d"}).encode())

    def generic(self):
        proposals = {"kind": "img2.object-proposals", "version": 1, "objects": [
            {"objectId": "cube", "label": "cube", "role": "prop", "approxBox": [.1, .1, .9, .9],
             "observations": {"flatness": "volumetric"}}]}
        self.routed = self.case.router.route(image="reference.png", objects=proposals,
                                             expected_output="3d", explicit_route="base:generic")
        self.assertEqual(self.routed["decision"]["phase"], "decided")
        return proposals

    def initialize(self, result, domains):
        with patch.object(route_cli, "registered_domains", return_value=domains), \
                patch("domains.registered_domains", return_value=domains):
            return route_cli._create_children(self.case.router, result, self.harness, self.case.consent)

    def test_legitimate_generic_child_uses_real_journal_authority_not_a_boolean(self):
        self.generic()
        parent = self.initialize(self.routed, {})
        self.assertEqual(parent["objects"][0]["profile"], "generic")
        self.assertEqual(parent["objects"][0]["outputKind"], "3d-object")
        child = json.loads((self.case.workspace / parent["objects"][0]["statePath"]).read_text())
        self.assertEqual(child["profile"], "generic")
        self.assertTrue(any(step["status"] == "pending" for step in child["checklist"]))
        self.assertNotEqual(parent["status"], "complete")
        self.assertFalse(parent["routeDecision"]["executionAuthorized"])
        journal = [json.loads(line) for line in self.case.controller.journal_path.read_text().splitlines()]
        self.assertTrue(any("route-round" in entry["event"] for entry in journal))

    def test_explicit_2d_uses_registered_pixi_profile_without_generic_substitution(self):
        parent = self.initialize(self.case.routed, {"pixi2d": {"id": "pixi2d"}})
        child = json.loads((self.case.workspace / parent["objects"][0]["statePath"]).read_text())
        self.assertEqual(child["profile"], "pixi2d")
        self.assertEqual(parent["objects"][0]["outputKind"], "2d-scene")
        self.assertTrue(all(step["id"] not in {"composition", "scene-gates"} for step in parent["checklist"]))
        self.assertTrue(any(step["status"] == "pending" for step in child["checklist"]))

    def test_missing_profile_ambiguous_provider_and_dependency_fail_closed(self):
        from route_parent import ParentError
        with self.assertRaises(DecisionError) as caught:
            self.initialize(self.case.routed, {})
        self.assertEqual(caught.exception.reason_code, "owner-profile-unavailable")
        self.assertFalse((self.case.workspace / ".img2threejs/state.json").exists())
        snapshot = self.case.router.verification_snapshot()
        candidate = next(c for c in snapshot["candidates"] if c["provider"] == "pixi2d")
        candidate["dependencies"] = {"python3>=3.10": False}
        with patch.object(self.case.router, "verification_snapshot", return_value=snapshot), \
                self.assertRaises(DecisionError) as caught:
            self.initialize(self.case.routed, {"pixi2d": {"id": "pixi2d"}})
        self.assertEqual(caught.exception.reason_code, "live-dependency-mismatch")
        self.case.live_status = "ambiguous"
        with self.assertRaises((DecisionError, ParentError)):
            self.initialize(self.case.routed, {"pixi2d": {"id": "pixi2d"}})
        self.assertFalse((self.case.workspace / ".img2threejs/state.json").exists())

    def test_no_auth_and_uncertain_routes_never_initialize_or_probe_snapshot(self):
        denied = Mock(side_effect=DecisionError("credential-missing"))
        with patch.object(self.case.router, "verification_snapshot") as snapshot:
            with self.assertRaises(DecisionError) as caught:
                route_cli._create_children(self.case.router, self.case.routed, self.harness, denied)
            self.assertEqual(caught.exception.reason_code, "credential-missing")
            snapshot.assert_not_called()
        unresolved = copy.deepcopy(self.case.routed)
        unresolved["decision"]["objects"][0]["status"] = "best-of-n"
        with patch.object(self.case.router, "verification_snapshot") as snapshot:
            with self.assertRaises(DecisionError) as caught:
                route_cli._create_children(self.case.router, unresolved, self.harness, self.case.consent)
            self.assertEqual(caught.exception.reason_code, "route-execution-unresolved")
            snapshot.assert_not_called()

    def test_malicious_provider_or_profile_cannot_supply_owner_authority(self):
        snapshot = self.case.router.verification_snapshot()
        selected = self.case.routed["decision"]["objects"][0]["selected"][0]
        candidate = next(c for c in snapshot["candidates"] if c["id"] == selected)
        candidate.update(provider="unapproved", profile="generic", claimants=["unapproved"])
        with patch.object(route_cli, "registered_domains", return_value={"unapproved": {"id": "generic"}}), \
                self.assertRaises(DecisionError) as caught:
            route_cli._owner_profiles(self.case.router, self.case.routed, snapshot)
        self.assertEqual(caught.exception.reason_code, "unapproved-owner-profile")
        self.assertFalse((self.case.workspace / ".img2threejs/state.json").exists())

    def test_fixed_character_cs2_interior_and_pixi_terminal_mappings(self):
        for provider, card_kind, profile, terminal, branch in (
                ("character", "rigged-3d", "animated-character", "rigged-3d", "3d"),
                ("cs2", "spec-augmentation", "cs2", "3d-object", "3d"),
                ("interior", "3d-scene", "interior", "3d-scene", "3d"),
                ("pixi2d", "2d-scene", "pixi2d", "2d-scene", "2d")):
            with self.subTest(provider=provider):
                candidate = {"id": "selected", "provider": provider, "resolvedSha": "a" * 40,
                             "providerStatus": "answered", "claimants": [provider],
                             "blockingGateProduced": True, "dependencies": {}, "profile": "generic",
                             "card": {"costClass": "heavy" if provider == "character" else "local", "outputKind": card_kind}}
                routed = {"decision": {"phase": "decided", "request": {"expectedOutput": branch},
                                      "objects": [{"status": "resolved", "selected": ["selected"]}]}}
                domain = {"id": profile}
                row = {"plugin": provider, "resolvedSha": "a" * 40, "dir": str(self.case.plugin)}
                with patch.object(route_cli, "registered_domains", return_value={profile: domain}), \
                        patch.object(self.case.discovery, "query", return_value={"status": "answered", "providers": [row]}), \
                        patch.object(self.case.discovery, "_pinned_file", return_value=json.dumps({"id": profile}).encode()):
                    owners = route_cli._owner_profiles(self.case.router, routed, {"candidates": [candidate]})
                self.assertEqual(owners["selected"], route_cli.OwnerProfile(profile, terminal))
                self.assertNotEqual(owners["selected"].profile, "generic")

    def test_paid_licensed_and_unknown_owner_costs_remain_blocked(self):
        for cost_class in ("paid", "licensed", "unknown", None):
            with self.subTest(costClass=cost_class):
                candidate = {"id": "selected", "provider": "character", "resolvedSha": "a" * 40,
                             "providerStatus": "answered", "claimants": ["character"],
                             "blockingGateProduced": True, "dependencies": {},
                             "card": {"costClass": cost_class, "outputKind": "rigged-3d"}}
                routed = {"decision": {"phase": "decided", "request": {"expectedOutput": "3d"},
                                      "objects": [{"status": "resolved", "selected": ["selected"]}]}}
                with patch.object(route_cli, "registered_domains", return_value={"animated-character": {"id": "animated-character"}}), \
                        self.assertRaises(DecisionError) as caught:
                    route_cli._owner_profiles(self.case.router, routed, {"candidates": [candidate]})
                self.assertEqual(caught.exception.reason_code, "paid-or-licensed-owner-not-authorized")

    def test_profile_definition_must_match_pinned_provider_domain(self):
        domains = {"pixi2d": {"id": "pixi2d", "setupSteps": (("malicious", "execute unapproved code"),)}}
        with self.assertRaises(DecisionError) as caught:
            self.initialize(self.case.routed, domains)
        self.assertEqual(caught.exception.reason_code, "owner-profile-provider-mismatch")

    def test_legacy_parent_is_preserved_before_any_new_snapshot(self):
        path = self.case.workspace / ".img2threejs/state.json"
        path.write_text('{"legacy":"user state"}')
        with patch.object(self.case.router, "verification_snapshot") as snapshot, self.assertRaises(DecisionError) as caught:
            self.initialize(self.case.routed, {"pixi2d": {"id": "pixi2d"}})
        self.assertEqual(caught.exception.reason_code, "existing-parent-preserved")
        self.assertEqual(path.read_text(), '{"legacy":"user state"}')
        snapshot.assert_not_called()

    def run_child_cli(self, result, domains, *, expected_output, proposals=None):
        if proposals is not None:
            (self.case.workspace / "objects.json").write_text(json.dumps(proposals))
        host_consent = Mock(side_effect=self.case.consent)
        host_consent.executable = Path(sys.executable).resolve()
        host_consent.last_failure = None
        self.case.router.consent = host_consent
        argv = ["--workspace", str(self.case.workspace), "--run-id", "route-run", "--image", "reference.png",
                "--expected-output", expected_output, "--create-children", "--harness-root", str(self.case.runtime),
                "--harness-sha", "b" * 40, "--harness-executable", str(self.case.runtime / "bin/img2.mjs"),
                "--harness-sha256", "d" * 64]
        if proposals is not None:
            argv.extend(["--objects", "objects.json"])
        output = io.StringIO()
        with patch.object(route_cli, "DecisionController", return_value=self.case.controller), \
                patch.object(route_cli, "PinnedDiscovery", return_value=self.case.discovery), \
                patch.object(route_cli, "LiveConsent", return_value=host_consent), \
                patch.object(route_cli, "HarnessPin", return_value=self.harness), \
                patch.object(route_cli, "JevProviderFactory"), \
                patch.object(route_cli, "Router", return_value=self.case.router), \
                patch.object(self.case.router, "route", return_value=result), \
                patch.object(route_cli, "registered_domains", return_value=domains), \
                patch("domains.registered_domains", return_value=domains), redirect_stdout(output):
            status = route_cli.main(argv)
        command = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(command["status"], "children-initialized")
        self.assertEqual(command["mode"], "children-initialized")
        self.assertFalse(command["terminalAcceptance"])
        self.assertEqual(command["terminalStatus"], "incomplete")
        self.assertTrue(command["childrenCreated"])
        self.assertFalse(command["executionAuthorized"])
        self.assertFalse(command["deliverable"])
        self.assertTrue((self.case.workspace / command["parentStatePath"]).is_file())
        self.assertTrue(all((self.case.workspace / path).is_file() for path in command["childStatePaths"]))
        return command

    def test_cli_child_creation_reports_pending_terminal_state(self):
        proposals = self.generic()
        self.run_child_cli(self.routed, {}, expected_output="3d", proposals=proposals)

    def test_cli_explicit_2d_initializes_only_the_actual_pending_pixi_child(self):
        command = self.run_child_cli(self.case.routed, {"pixi2d": {"id": "pixi2d"}}, expected_output="2d")
        child = json.loads((self.case.workspace / command["childStatePaths"][0]).read_text())
        self.assertEqual(child["profile"], "pixi2d")
        self.assertEqual(child["routeChild"]["outputKind"], "2d-scene")

    def run_no_account_children(self, grant):
        """``--no-jev-account --create-children`` through main() with the real parent fixtures."""
        proposals = self.generic()
        (self.case.workspace / "objects.json").write_text(json.dumps(proposals))
        authority = Mock(side_effect=grant)
        authority.model_id, authority.last_failure = "test-provider:test-model", None
        authority.metadata = Mock(return_value={"decisionMode": "no-account", "attestationId": "a" * 64,
                                                "decider": "model:test-provider:test-model", "presentedAsJev": False})
        argv = ["--workspace", str(self.case.workspace), "--run-id", "route-run", "--image", "reference.png",
                "--objects", "objects.json", "--no-jev-account", "--create-children",
                "--harness-root", str(self.case.runtime), "--harness-sha", "b" * 40,
                "--harness-executable", str(self.case.runtime / "bin/img2.mjs"), "--harness-sha256", "d" * 64]
        output = io.StringIO()
        with patch.object(route_cli, "RoutingNoAccount", return_value=authority), \
                patch.object(route_cli, "DecisionController", return_value=self.case.controller), \
                patch.object(route_cli, "PinnedDiscovery", return_value=self.case.discovery), \
                patch.object(route_cli, "LiveConsent") as live, patch.object(route_cli, "JevProviderFactory") as jev, \
                patch.object(route_cli, "_approved_lock") as lock, \
                patch.object(route_cli, "HarnessPin", return_value=self.harness), \
                patch.object(route_cli, "Router", return_value=self.case.router), \
                patch.object(self.case.router, "route", return_value=self.routed), \
                patch.object(route_cli, "registered_domains", return_value={}), \
                patch("domains.registered_domains", return_value={}), redirect_stdout(output):
            status = route_cli.main(argv)
        for jev_path in (live, jev, lock):
            jev_path.assert_not_called()
        return status, json.loads(output.getvalue()), authority

    def test_no_account_create_children_checks_the_attestation_before_creating_anything(self):
        calls = []
        def revoked():
            calls.append(1)
            raise DecisionError("attestation-revoked")
        status, output, authority = self.run_no_account_children(revoked)
        self.assertEqual((status, output["status"], output["reasonCode"]), (3, "request-input", "attestation-revoked"))
        self.assertTrue(calls, "the attestation was never consulted on the child-creation path")
        self.assertFalse(output["childrenCreated"])
        self.assertFalse((self.case.workspace / ".img2threejs/state.json").exists())
        self.assertEqual(output["decisionMode"], "no-jev-account-pending")
        self.assertFalse({"decider", "presentedAsJev", "attestationId"} & set(output))

    def test_no_account_create_children_proceeds_only_while_the_attestation_holds(self):
        calls = []
        def holds():
            calls.append(1)
            return self.case.consent()  # the fixture's recorded grant, as the live attestation would return
        status, output, authority = self.run_no_account_children(holds)
        self.assertEqual((status, output["status"]), (0, "children-initialized"), output)
        self.assertTrue(calls)
        self.assertTrue((self.case.workspace / ".img2threejs/state.json").is_file())
        self.assertFalse(output["terminalAcceptance"])

    def test_cli_completion_revalidates_pending_child_without_rerouting(self):
        self.generic()
        parent = self.initialize(self.routed, {})
        object_id = parent["objects"][0]["objectId"]
        state_path = self.case.workspace / ".img2threejs/state.json"
        before = state_path.read_bytes()
        host_consent = Mock(side_effect=self.case.consent)
        host_consent.executable = Path(sys.executable).resolve()
        host_consent.last_failure = None
        self.case.router.consent = host_consent
        argv = ["--workspace", str(self.case.workspace), "--run-id", "route-run",
                "--complete-child", object_id, "--decision-ref", self.routed["decisionRef"],
                "--harness-root", str(self.case.runtime), "--harness-sha", "b" * 40,
                "--harness-executable", str(self.case.runtime / "bin/img2.mjs"),
                "--harness-sha256", "d" * 64]
        output = io.StringIO()
        with patch.object(route_cli, "DecisionController", return_value=self.case.controller), \
                patch.object(route_cli, "PinnedDiscovery", return_value=self.case.discovery), \
                patch.object(route_cli, "LiveConsent", return_value=host_consent), \
                patch.object(route_cli, "HarnessPin", return_value=self.harness), \
                patch.object(route_cli, "JevProviderFactory") as factory, \
                patch.object(route_cli, "Router", return_value=self.case.router), \
                patch.object(self.case.router, "route", side_effect=AssertionError("rerouted")) as route, \
                patch.object(route_cli, "registered_domains", return_value={}), \
                patch("domains.registered_domains", return_value={}), redirect_stdout(output):
            status = route_cli.main(argv)
        result = json.loads(output.getvalue())
        self.assertEqual(status, 3)
        self.assertEqual(result["reasonCode"], "child-terminal-gates-incomplete")
        self.assertFalse(result["deliverable"])
        self.assertEqual(state_path.read_bytes(), before)
        route.assert_not_called()
        factory.assert_not_called()

    def test_completion_rejects_unbound_decision_bytes(self):
        self.generic()
        reference = self.routed["decisionRef"]
        path = self.case.workspace / reference
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(DecisionError) as caught:
            route_cli._load_decision(self.case.workspace, reference, "route-run")
        self.assertEqual(caught.exception.reason_code, "invalid-routing-result")

    def test_help_and_required_harness_approval_flags(self):
        help_text = io.StringIO()
        with redirect_stdout(help_text), self.assertRaises(SystemExit) as caught:
            route_cli.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        for name in ("--create-children", "--harness-root", "--harness-sha", "--harness-executable", "--harness-sha256"):
            self.assertIn(name, help_text.getvalue())
        with patch.object(route_cli, "DecisionController") as controller, redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit):
            route_cli.main(["--run-id", "run", "--image", "reference.png", "--objects", "objects.json", "--create-children"])
        controller.assert_not_called()


@unittest.skipUnless(shutil.which("node"), "runtime closure pinning requires a host Node executable")
class HarnessPinTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "harness"
        self.sources = {"package.json": b'{"name":"@img2threejs/img2"}',
                        "bin/img2.mjs": b"import fs from 'node:fs'\n",
                        "img2_core/__init__.py": b"import json\n"}
        for relative, raw in self.sources.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.consent = SimpleNamespace(git=Path(sys.executable).resolve(), git_env={"GIT_NO_LAZY_FETCH": "1"})
        self.consent.git_sha = hashlib.sha256(self.consent.git.read_bytes()).hexdigest()
        self.calls = []
        self.runner_patch = patch.object(route_cli, "_run_bytes", side_effect=self.command)
        self.runner_patch.start()
        self.addCleanup(self.runner_patch.stop)

    def archive(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            for relative, raw in self.sources.items():
                member = tarfile.TarInfo(relative)
                member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
        return output.getvalue()

    def command(self, argv, workspace, **kwargs):
        self.calls.append((argv, kwargs))
        return (0, self.archive()) if "archive" in argv else (0, b'{"harness":"0.5.0"}')

    def pin(self):
        return route_cli.HarnessPin(self.root, "a" * 40, self.root / "bin/img2.mjs",
                                    hashlib.sha256(self.sources["bin/img2.mjs"]).hexdigest(), self.consent)

    def test_complete_runtime_inventory_and_launcher_are_pinned(self):
        pin = self.pin()
        self.assertEqual(set(pin.inventory), set(self.sources))
        (self.root / "img2_core/__init__.py").write_bytes(b"changed source")
        with self.assertRaises(DecisionError):
            pin.verify()

    def test_untracked_external_dynamic_and_dependency_imports_fail_closed(self):
        (self.root / "bin/injected.mjs").write_text("untracked")
        with self.assertRaises(DecisionError):
            self.pin()
        (self.root / "bin/injected.mjs").unlink()
        for raw in (b"import value from 'unapproved-package'\n", b"await import('./late.mjs')\n"):
            self.sources["bin/img2.mjs"] = raw
            (self.root / "bin/img2.mjs").write_bytes(raw)
            with self.assertRaises(DecisionError) as caught:
                self.pin()
            self.assertEqual(caught.exception.reason_code, "harness-import-closure-unproven")

    def test_declared_third_party_package_dependency_is_not_implicitly_trusted(self):
        self.sources["package.json"] = b'{"name":"@img2threejs/img2","dependencies":{"unapproved":"*"}}'
        (self.root / "package.json").write_bytes(self.sources["package.json"])
        with self.assertRaises(DecisionError) as caught:
            self.pin()
        self.assertEqual(caught.exception.reason_code, "harness-import-closure-unproven")

    def test_runtime_runner_strips_startup_overrides_and_forbids_installs(self):
        pin = self.pin()
        with patch.dict(route_cli.os.environ, {"NODE_OPTIONS": "--import unapproved", "TYPESAFE_API_KEY": "private"}):
            pin.runner([*pin.argv, "--version", "--json"], shell=False, capture_output=True, check=False)
        actual = next(kwargs for argv, kwargs in self.calls if argv[:len(pin.argv)] == list(pin.argv))
        self.assertNotIn("NODE_OPTIONS", actual["env"])
        self.assertNotIn("TYPESAFE_API_KEY", actual["env"])
        with self.assertRaises(DecisionError) as caught:
            pin.runner([*pin.argv, "add", "unapproved"])
        self.assertEqual(caught.exception.reason_code, "authority-command-not-authorized")


class PinnedDependencyRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.pin = object.__new__(route_cli.HarnessPin)
        self.pin.root = self.root
        self.pin.argv = (str(self.root / "missing-node"), "--no-addons",
                         str(self.root / "img2.mjs"))
        self.pin.consent = SimpleNamespace(git=self.root / "missing-git", git_env={})
        self.pin.verify = Mock()
        controller = SimpleNamespace(workspace=self.root, record_probe=lambda fact: fact)
        self.discovery = route_cli.PinnedDiscovery(controller, harness=self.pin.argv,
                                                   runner=self.pin.runner)
        self.environment = patch.dict(route_cli.os.environ, {"PATH": str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_missing_allowlisted_dependency_reports_dependency_missing(self):
        dependencies = {"blender>=4.3": self.discovery.dependency("blender>=4.3")}
        card = {"kind": "img2.route-card", "version": 1, "outputKind": "3d-scene",
                "costClass": "heavy", "requires": {"roles": ["scene-container"]},
                "deps": [{"probe": "blender>=4.3"}]}
        result = evaluate_candidate(
            card, expected_output="3d", roles=["scene-container"], observations={},
            intent=[], provider_status="answered", dependencies=dependencies,
            blocking_gate_produced=True)
        self.assertEqual(result, {"eligible": False,
                                  "reasons": ["dependency-missing:blender>=4.3"]})
        self.assertEqual(self.discovery.probes[0]["exitCode"], 127)
        self.assertEqual(self.pin.verify.call_count, 2)

    def test_non_executable_dependency_still_fails_closed(self):
        executable = self.root / "blender"
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o600)
        with self.assertRaises(DecisionError) as caught:
            self.discovery.dependency("blender>=4.3")
        self.assertEqual(caught.exception.reason_code, "provider-error")
        self.assertEqual(self.pin.verify.call_count, 2)

    def test_other_os_errors_still_fail_closed(self):
        for error in (PermissionError("denied"), OSError("io failure")):
            with self.subTest(error=type(error).__name__):
                with patch.object(route_cli.subprocess, "Popen", side_effect=error):
                    with self.assertRaises(DecisionError) as caught:
                        self.discovery.dependency("blender>=4.3")
                self.assertEqual(caught.exception.reason_code, "provider-error")

    def test_missing_harness_and_git_executables_still_fail_closed(self):
        for argv in ((*self.pin.argv, "--version", "--json"), ("git", "--version")):
            with self.subTest(argv=argv):
                with self.assertRaises(DecisionError) as caught:
                    self.discovery.run(argv)
                self.assertEqual(caught.exception.reason_code, "provider-error")

    def test_missing_working_directory_still_fails_closed(self):
        with self.assertRaises(DecisionError) as caught:
            self.pin.runner(["blender", "--version"], cwd=self.root / "missing-cwd")
        self.assertEqual(caught.exception.reason_code, "provider-error")

    def test_missing_dependency_does_not_mask_pre_or_post_verification_failure(self):
        for phase in ("before", "after"):
            with self.subTest(phase=phase):
                error = DecisionError("harness-pin-mismatch")
                self.pin.verify.side_effect = [error] if phase == "before" else [None, error]
                with self.assertRaises(DecisionError) as caught:
                    self.discovery.dependency("blender>=4.3")
                self.assertEqual(caught.exception.reason_code, "harness-pin-mismatch")

    def test_missing_verification_executable_still_fails_closed(self):
        self.pin.verify.side_effect = lambda: route_cli._run_bytes(
            [str(self.root / "missing-verifier")], self.root)
        with self.assertRaises(DecisionError) as caught:
            self.discovery.dependency("blender>=4.3")
        self.assertEqual(caught.exception.reason_code, "provider-error")


if __name__ == "__main__":
    unittest.main()
