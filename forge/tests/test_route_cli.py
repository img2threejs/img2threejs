"""Offline route CLI wiring/probes. All provider execution boundaries are mocked."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
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
from decision_control import DecisionError, GROUPS
from jev_cli_bridge import REQUIRED_PROVIDER_FILES
from stage0_route.images import png


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
            self.assertEqual(argv[2], "auth-status")
            return 3, {"kind": "img2.jev-command", "version": 1, "status": "missing", "reasonCode": "credential-missing"}
        self.probe = fail_auth
        code, output, _, _ = self.invoke()
        self.assertEqual((code, output["reasonCode"]), (3, "credential-missing"))
        self.assertEqual(output["requestedCommand"], "img2-jev login")
        self.assertNotIn("login-declined", json.dumps(output))
        self.assertFalse(output["executionAuthorized"])

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


if __name__ == "__main__":
    unittest.main()
