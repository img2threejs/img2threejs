"""Defensive runtime-boundary fixtures; synthetic bpy metadata is not native qualification."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

FORGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(FORGE), str(FORGE / "_shared")]
from decision_control import DecisionController
from route_parent import ParentError
from stage0_route.discovery import Discovery
import test_route_authority as authority_fixtures
from test_route_cli import route_cli


class InteriorBpyRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.executable = self.root / "venv" / "bin" / "python"
        self.executable.parent.mkdir(parents=True)
        self.write_probe()
        self.environment = patch.dict(os.environ, {
            "IMG2_BLENDER_PYTHON": str(self.executable),
            "PATH": str(self.root), "TYPESAFE_API_KEY": "benign-canary",
            "GITHUB_TOKEN": "benign-canary", "NODE_OPTIONS": "benign-canary",
            "PYTHONPATH": "benign-canary"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.controller = DecisionController(self.root, "interior-runtime", clock=lambda: 1000)
        self.calls = []

        def run(argv, **kwargs):
            self.calls.append((list(argv), kwargs))
            return subprocess.run(argv, **kwargs)
        self.discovery = Discovery(self.controller, runner=run)

    def write_probe(self, version="4.3.0", executable=None):
        value = {"kind": "img2.interior-bpy-probe", "version": 1,
                 "executable": executable or str(self.executable),
                 "bpyVersion": version, "bpyModule": "/synthetic/bpy/__init__.py"}
        self.executable.write_text("#!" + sys.executable + "\nimport json\nprint(json.dumps(" + repr(value) + "))\n")
        self.executable.chmod(0o755)

    def authority(self):
        case = authority_fixtures.RouteAuthorityTests()
        with patch.dict(os.environ, {"PATH": os.defpath}):
            case.setUp()
        self.addCleanup(case.doCleanups)
        discovered = copy.deepcopy(case.discovered)
        discovered["candidates"].append({"id": "interior-fixture", "provider": "interior",
            "card": {"outputKind": "3d-scene", "deps": [{"probe": "interior-bpy>=4.3"}]}})
        return case.authority_for(discovered=discovered, runner=self.discovery.runner)

    def test_bpy_only_runtime_is_admitted_by_discovery_and_authority(self):
        self.assertTrue(self.discovery.dependency("interior-bpy>=4.3"))
        self.assertTrue(self.authority()._dependency("interior-bpy>=4.3"))
        self.assertTrue(self.calls)
        for argv, kwargs in self.calls:
            self.assertEqual(argv[0], str(self.executable))
            self.assertEqual(argv[1:4], ["-I", "-B", "-c"])
            for key in ("TYPESAFE_API_KEY", "GITHUB_TOKEN", "NODE_OPTIONS", "PYTHONPATH"):
                self.assertNotIn(key, kwargs["env"])

    def test_missing_invalid_and_old_runtime_do_not_fall_back_to_blender(self):
        for setting in ("", "relative/python", str(self.root / "missing-python")):
            with self.subTest(setting=setting), patch.dict(os.environ, {"IMG2_BLENDER_PYTHON": setting}):
                self.assertFalse(self.discovery.dependency("interior-bpy>=4.3"))
                self.assertFalse(self.authority()._dependency("interior-bpy>=4.3"))
        self.write_probe("4.2.9")
        self.assertFalse(self.discovery.dependency("interior-bpy>=4.3"))
        self.write_probe(executable="/different/python")
        self.assertFalse(self.discovery.dependency("interior-bpy>=4.3"))

    def test_runtime_selector_and_interpreter_bytes_are_fresh_at_authority(self):
        authority = self.authority()
        self.assertTrue(authority._dependency("interior-bpy>=4.3"))
        self.write_probe("4.5.0")
        with self.assertRaises(ParentError) as caught:
            authority._dependency("interior-bpy>=4.3")
        self.assertEqual(caught.exception.reason_code, "interior-host-binding-changed")
        authority = self.authority()
        with patch.dict(os.environ, {"IMG2_BLENDER_PYTHON": ""}):
            with self.assertRaises(ParentError) as caught:
                authority._dependency("interior-bpy>=4.3")
        self.assertEqual(caught.exception.reason_code, "interior-host-binding-changed")

    def interior_strategy(self):
        import test_native_strategy_decision as strategy_fixtures
        from workflow_decisions import WorkflowDecisions, WorkflowPolicy, NativePolicy
        case = strategy_fixtures.NativeStrategyDecisionTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.scope["available_dependencies"] = {"blender"}
        for candidate in case.context["candidates"]:
            candidate["dependencies"] = ["blender"]
        case.bind_authority()
        policy = WorkflowPolicy(
            scope={key: value for key, value in case.scope.items() if key not in {"pass_id", "pass_unlocked"}},
            requirements=case.context["requirements"], authorize=lambda *args: True, draft_spec="scene.json",
            native=NativePolicy(validate_spec=case.validator, current_pass=lambda *args: "camera",
                authorize_native=case.authority, rules=tuple(case.rules),
                frozen_paths=frozenset(case.frozen), forbidden_components=frozenset(case.forbidden)))
        workflow = WorkflowDecisions(case.controller, ".img2threejs/objects/room/rev-1/state.json",
            policy, consent=case.consent, availability=case.options["availability"], provider_factory=None)
        return case, workflow

    def test_symbolic_blender_plan_requires_the_trusted_bpy_probe_not_host_claims(self):
        from decision_control import DecisionError
        case, workflow = self.interior_strategy()
        with patch.dict(os.environ, {"IMG2_BLENDER_PYTHON": ""}):
            with self.assertRaises(DecisionError) as caught:
                workflow._strategy(case.context, {"proposals": case.proposals}, profile="interior")
        self.assertEqual(caught.exception.reason_code, "interior-runtime-unavailable")
        case.provider.assert_not_called()

    def test_bpy_only_native_strategy_is_bound_through_begin_to_venv_configuration(self):
        from forge._shared.decision_control import DecisionError
        configuration = self.executable.parent.parent / "pyvenv.cfg"
        configuration.write_text("home = owned fixture runtime\n")
        case, workflow = self.interior_strategy()
        stage = workflow._strategy(case.context, {"proposals": case.proposals}, profile="interior")
        result = stage.consult(**case.options)
        self.assertEqual(result["action"], "selected")
        configuration.write_text("home = changed owned fixture runtime\n")
        with self.assertRaises(DecisionError) as caught:
            stage.begin_application(result["record"], 2, consent=case.consent)
        self.assertEqual(caught.exception.reason_code, "interior-host-binding-changed")

    def test_selected_venv_symlink_keeps_its_invocation_identity(self):
        target = self.root / "base-python"
        self.executable.rename(target)
        self.executable.symlink_to(target)
        self.assertTrue(self.discovery.dependency("interior-bpy>=4.3"))
        self.assertEqual(self.authority()._interior_host["path"], str(self.executable))

    def test_native_producer_policy_cannot_select_an_unrelated_bpy_python(self):
        from dataclasses import replace
        import test_native_terminal_authority as native_fixtures
        case = native_fixtures.NativeTerminalAuthorityTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        authority = self.authority()
        policy = replace(case.policy, provider="interior", parent_root=str(authority.workspace))
        authority._native_terminal_authority = native_fixtures.NativeTerminalAuthority(
            authority.workspace, {policy.object_id: policy})
        with self.assertRaises(ParentError) as caught:
            authority._native_runtime(policy.object_id, "interior")
        self.assertEqual(caught.exception.reason_code, "interior-host-binding-changed")


class AuthorityProbeBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.pin = object.__new__(route_cli.HarnessPin)
        self.pin.root = self.root
        self.pin.argv = (str(self.root / "unused-harness"),)
        self.pin.verify = Mock()

    def test_missing_optional_probe_is_an_unavailable_fact_not_a_global_error(self):
        with patch.dict(os.environ, {"PATH": str(self.root)}):
            result = self.pin.authority_runner(["blender", "--version"], cwd=str(self.root),
                capture_output=True, timeout=1, check=False, shell=False, env=None)
        self.assertEqual(result.returncode, 127)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(self.pin.verify.call_count, 2)

    def test_reviewed_gate_keeps_failure_but_does_not_inherit_secrets(self):
        canaries = {"TYPESAFE_API_KEY": "benign-canary", "GITHUB_TOKEN": "benign-canary",
                    "NODE_OPTIONS": "benign-canary", "PYTHONPATH": "benign-canary"}
        with patch.dict(os.environ, canaries), patch.object(route_cli.subprocess, "run",
                return_value=subprocess.CompletedProcess(["reviewed-gate"], 7, b"", b"")) as run:
            result = self.pin.authority_runner(["reviewed-gate"], env=None)
        self.assertEqual(result.returncode, 7)
        for key in canaries:
            self.assertNotIn(key, run.call_args.kwargs["env"])
        self.assertEqual(self.pin.verify.call_count, 2)

    def test_missing_legacy_blender_dependency_does_not_block_real_generic_child_creation(self):
        import test_stage0_route as route_fixtures
        from stage0_route.route import Router
        from route_authority import OwnerProfile, RouteAuthority
        from route_parent import RouteParent
        case = authority_fixtures.RouteAuthorityTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        plugin = case.workspace / "optional-interior"
        provider = {"plugin": "interior", "version": "1.0.0", "resolvedSha": authority_fixtures.PIN, "dir": str(plugin)}
        case.pin(plugin, "plugin.json", authority_fixtures.encode({
            "name": "interior", "version": "1.0.0",
            "capabilities": [{"from": "image", "to": "interior-scene-candidate"}]}))
        case.pin(plugin, "route.json", authority_fixtures.encode({
            "kind": "img2.route-card", "version": 1, "outputKind": "3d-scene", "costClass": "local",
            "requires": {}, "notFor": {}, "deps": [{"probe": "blender>=4.3"}], "display": {}}))
        case.pin(plugin, "gates.json", b"[]")
        def run(argv, **kwargs):
            if argv[0] == "fixture-img2" and "interior-scene-candidate" in argv:
                return subprocess.CompletedProcess(argv, 0, authority_fixtures.encode({
                    "version": 1, "query": {"from": "image", "to": "interior-scene-candidate"},
                    "status": "answered", "providers": [provider]}), b"")
            if argv == ["blender", "--version"] or tuple(argv) == ("blender", "--version"):
                return self.pin.authority_runner(argv, **kwargs)
            return case.command(list(argv), **kwargs)
        discovery = Discovery(case.controller, harness=("fixture-img2",), runner=run)
        discovery.availability = Mock(return_value={"status": "present"})
        router = Router(case.workspace, "route-run", controller=case.controller,
                        discovery=discovery, provider=case.answer, consent=case.consent)
        with patch.dict(os.environ, {"PATH": str(self.root), "IMG2_BLENDER_PYTHON": ""}):
            routed = router.route(image="reference.png", objects=route_fixtures.Stage0RouteTests.proposals(), expected_output="3d")
            self.assertEqual(routed["decision"]["phase"], "decided")
            discovered = router.verification_snapshot()
            interior = next(candidate for candidate in discovered["candidates"] if candidate["provider"] == "interior")
            self.assertIs(interior["dependencies"]["blender>=4.3"], False)
            base = next(candidate for candidate in discovered["candidates"] if candidate["provider"] == "base")
            authority = RouteAuthority(router, routed["decisionRef"], discovered=discovered,
                owners={base["id"]: OwnerProfile("generic", "3d-object")}, harness=("fixture-img2",),
                runtime_provider=case.runtime_row, runner=run)
            parent = RouteParent(case.controller, verify_aggregate=authority, consent=case.consent).create()
        self.assertTrue(parent["objects"])
        self.assertTrue(all(row["ownerRoute"] == base["id"] and row["profile"] == "generic" for row in parent["objects"]))
        for row in parent["objects"]:
            self.assertTrue((case.workspace / row["statePath"]).is_file())


if __name__ == "__main__":
    unittest.main()
