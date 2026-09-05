"""forge/stage3_build/run_gates.py tests (task 4.6, `establish-the-emission-target-contract`).

Covers: domain plugin's gates run post-domain-steps; target plugin's gates run post-target-step;
uninvolved plugin runs none; blocking stop naming gate and plugin; malformed-envelope error;
hang trips the bound and names the gate; the runner works with IMG2_HOME unset in the parent env.
Every test pins IMG2_HOME to a temp/empty directory populated directly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS_DIR = ROOT.parent / "img2-harness"
sys.path.insert(0, str(ROOT / "forge"))
sys.path.insert(0, str(ROOT / "forge" / "_shared"))
sys.path.insert(0, str(ROOT / "forge" / "stage3_build"))

import run_gates  # noqa: E402
from workflow_state import new_state, save_state  # noqa: E402


def _action_ready_state(workspace: Path, spec_path: Path, *, profile: str = "generic") -> dict:
    state = new_state("ref.png", profile=profile, spec=str(spec_path))
    for entry in state["checklist"]:
        entry["status"] = "done"
        entry["evidence"] = ["fixture"]
    state["currentPass"] = "complete"
    state["status"] = "complete"
    state["currentStep"] = "complete"
    save_state(workspace / ".img2threejs" / "state.json", state)
    return state


def _write_registry_row(home: Path, plugin_id: str) -> None:
    registry_path = home / "plugins.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else {"version": 1, "plugins": []}
    registry["plugins"].append({"id": plugin_id, "repo": "x", "ref": "v1", "resolvedSha": f"sha-{plugin_id}", "addedAt": "2026-01-01"})
    registry_path.write_text(json.dumps(registry), encoding="utf-8")


PASS_GATE = """import json, sys
print(json.dumps({{"kind": "img2.gate-verdict", "version": 1, "gate": "{gate_id}", "plugin": "{plugin_id}",
                  "status": "pass", "reasons": [], "evidence": {{}}}}))
sys.exit(0)
"""

FAIL_GATE = """import json, sys
print(json.dumps({{"kind": "img2.gate-verdict", "version": 1, "gate": "{gate_id}", "plugin": "{plugin_id}",
                  "status": "fail", "reasons": ["deliberate failure"], "evidence": {{}}}}))
sys.exit(1)
"""

HANG_GATE = """import time
time.sleep(30)
"""


def _write_gated_plugin(home: Path, plugin_id: str, *, gate_body: str, blocking: bool = True) -> None:
    plugin_dir = home / "plugins" / plugin_id
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "gates.json").write_text(json.dumps([{
        "id": f"{plugin_id}-gate", "command": f"{sys.executable} {plugin_dir}/gate.py",
        "blocking": blocking, "after": [],
    }]), encoding="utf-8")
    (plugin_dir / "gate.py").write_text(gate_body, encoding="utf-8")
    _write_registry_row(home, plugin_id)


@unittest.skipUnless((ROOT.parent / "img2-harness").is_dir(), f"sibling checkout not found: {ROOT.parent / 'img2-harness'}")
class RunGatesTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.home = self.root / "img2home-empty"
        self.home.mkdir()
        (self.home / "plugins").mkdir()
        (self.home / "plugins.json").write_text(json.dumps({"version": 1, "plugins": []}), encoding="utf-8")
        # gate_runner_argv points at <home>/harness/img2_core/gate_runner.py -- a real copy, not
        # installed, so every test here actually exercises it rather than a stand-in.
        import shutil
        shutil.copytree(HARNESS_DIR / "img2_core", self.home / "harness" / "img2_core",
                         ignore=shutil.ignore_patterns("__pycache__"))
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        self.spec_path = self.workspace / "spec.json"
        self.spec_path.write_text(json.dumps({"buildPasses": [{"id": "blockout"}]}), encoding="utf-8")
        # new_state(profile=...) reads the domain registry in-process (domains.domain_profile),
        # which reads IMG2_HOME fresh from the environment -- pinned here so a domain-profile test
        # resolves against THIS scratch home, never whatever the developer happens to have
        # installed for real (the test_search_specs.py:243 pattern).
        self._old_img2_home = os.environ.get("IMG2_HOME")
        os.environ["IMG2_HOME"] = str(self.home)
        self.addCleanup(self._restore_img2_home)

    def _restore_img2_home(self):
        if self._old_img2_home is None:
            os.environ.pop("IMG2_HOME", None)
        else:
            os.environ["IMG2_HOME"] = self._old_img2_home

class TargetPluginGatesRunPostTargetStep(RunGatesTestBase):
    def test_a_plugin_that_ran_the_target_step_has_its_gates_run(self):
        state = _action_ready_state(self.workspace, self.spec_path)
        state["targetSelection"] = {"pluginId": "echo-plugin", "kind": "echo"}
        save_state(self.workspace / ".img2threejs" / "state.json", state)
        _write_gated_plugin(
            self.home, "echo-plugin",
            gate_body=PASS_GATE.format(gate_id="echo-plugin-gate", plugin_id="echo-plugin"),
        )
        results = run_gates.run_gates_for_workspace(self.workspace, home=self.home)
        self.assertIn("echo-plugin", results)
        self.assertEqual(results["echo-plugin"]["results"][0]["status"], "pass")

    def test_a_different_plugins_target_selection_does_not_involve_this_one(self):
        state = _action_ready_state(self.workspace, self.spec_path)
        state["targetSelection"] = {"pluginId": "other-plugin", "kind": "other"}
        save_state(self.workspace / ".img2threejs" / "state.json", state)
        _write_gated_plugin(
            self.home, "echo-plugin",
            gate_body=FAIL_GATE.format(gate_id="echo-plugin-gate", plugin_id="echo-plugin"),
        )
        results = run_gates.run_gates_for_workspace(self.workspace, home=self.home)
        self.assertEqual(results, {})


class DomainPluginGatesRunPostDomainSteps(RunGatesTestBase):
    def _install_domain_plugin(
        self, plugin_id: str, *, gate_body: str, blocking: bool = True, domain_id: str | None = None,
    ) -> None:
        domain_id = domain_id or plugin_id
        plugin_dir = self.home / "plugins" / plugin_id
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "domain.json").write_text(json.dumps({
            "id": domain_id,
            "setupSteps": [[f"{domain_id}-setup", "echo hi"]],
            "setupAnchorBefore": "local-spec-search",
        }), encoding="utf-8")
        (plugin_dir / "gates.json").write_text(json.dumps([{
            "id": f"{plugin_id}-gate", "command": f"{sys.executable} {plugin_dir}/gate.py",
            "blocking": blocking, "after": [],
        }]), encoding="utf-8")
        (plugin_dir / "gate.py").write_text(gate_body, encoding="utf-8")
        _write_registry_row(self.home, plugin_id)

    def test_a_domain_plugin_whose_setup_step_ran_has_its_gates_run(self):
        self._install_domain_plugin(
            "cs2-like", gate_body=PASS_GATE.format(gate_id="cs2-like-gate", plugin_id="cs2-like"),
        )
        _action_ready_state(self.workspace, self.spec_path, profile="cs2-like")
        results = run_gates.run_gates_for_workspace(self.workspace, home=self.home)
        self.assertIn("cs2-like", results)
        self.assertEqual(results["cs2-like"]["results"][0]["status"], "pass")

    def test_a_domain_plugin_installed_but_not_this_workspaces_profile_is_uninvolved(self):
        self._install_domain_plugin(
            "cs2-like", gate_body=FAIL_GATE.format(gate_id="cs2-like-gate", plugin_id="cs2-like"),
        )
        _action_ready_state(self.workspace, self.spec_path, profile="generic")
        results = run_gates.run_gates_for_workspace(self.workspace, home=self.home)
        self.assertEqual(results, {})

    def test_a_plugin_whose_package_name_differs_from_its_domain_id_is_still_correctly_attributed(self):
        # The exact case the name-match assumption would have misattributed: the registry plugin id
        # ("my-interiors-plugin") is not the domain id its domain.json declares ("interiors").
        self._install_domain_plugin(
            "my-interiors-plugin", domain_id="interiors",
            gate_body=PASS_GATE.format(gate_id="my-interiors-plugin-gate", plugin_id="my-interiors-plugin"),
        )
        _action_ready_state(self.workspace, self.spec_path, profile="interiors")
        results = run_gates.run_gates_for_workspace(self.workspace, home=self.home)
        self.assertIn("my-interiors-plugin", results)
        self.assertEqual(results["my-interiors-plugin"]["results"][0]["status"], "pass")

    def test_an_in_repo_domain_has_no_owner_plugin_and_gates_no_installed_plugin(self):
        # 'character' resolves from forge/_shared/domains/character.py, not an installed plugin.
        # An installed plugin's domain.json ALSO declaring "character" would collide with the
        # in-repo module (registered_domains()'s own "declared twice" refusal) -- so the honest
        # version of this scenario installs an unrelated plugin (its own, different domain) and
        # confirms it is not mistaken for "character"'s owner.
        self._install_domain_plugin(
            "unrelated-plugin", domain_id="unrelated-domain",
            gate_body=FAIL_GATE.format(gate_id="unrelated-plugin-gate", plugin_id="unrelated-plugin"),
        )
        self.assertIsNone(run_gates.domain_owner_plugin("character", self.home))
        _action_ready_state(self.workspace, self.spec_path, profile="character")
        results = run_gates.run_gates_for_workspace(self.workspace, home=self.home)
        self.assertEqual(results, {})


class UninvolvedPluginRunsNone(RunGatesTestBase):
    def test_an_installed_plugin_with_no_domain_and_no_target_selection_runs_no_gates(self):
        _action_ready_state(self.workspace, self.spec_path)
        _write_gated_plugin(
            self.home, "bystander",
            gate_body=FAIL_GATE.format(gate_id="bystander-gate", plugin_id="bystander"),
        )
        results = run_gates.run_gates_for_workspace(self.workspace, home=self.home)
        self.assertEqual(results, {})

    def test_no_installed_plugins_at_all_is_a_harmless_empty_run(self):
        _action_ready_state(self.workspace, self.spec_path)
        results = run_gates.run_gates_for_workspace(self.workspace, home=self.home)
        self.assertEqual(results, {})


class RigDomainsAreNotDueUntilTheRigTrackBegins(RunGatesTestBase):
    """extract-animated-character D1: the gate sweep at plugin-gates (FINAL scope) runs before the
    rig scope, and a rig domain's gate inputs are produced by rig steps -- participation on the
    strength of done SETUP steps fired the blocking gate one whole phase early, against a payload
    nothing had produced yet (machine-proven in review)."""

    def _write_rig_domain(self, plugin_id: str) -> None:
        (self.home / "plugins" / plugin_id / "domain.json").write_text(json.dumps({
            "id": "rig-dom",
            "setupSteps": [["rig-dom-setup", "Read {plugin_dir}/contract.md completely"]],
            "setupAnchorBefore": "local-spec-search",
            "rigSteps": [["rig-dom-track", "Run python3 {plugin_dir}/tools/track.py --payload p.json"]],
        }), encoding="utf-8")

    def _state_with(self, done_ids: list[str]) -> dict:
        state = _action_ready_state(self.workspace, self.spec_path, profile="rig-dom")
        for entry in state["checklist"]:
            entry["status"] = "done" if entry["id"] in done_ids else "pending"
        from workflow_state import save_state
        save_state(self.workspace / ".img2threejs" / "state.json", state)
        return state

    def test_setup_steps_alone_do_not_involve_a_rig_domain(self):
        _write_gated_plugin(
            self.home, "rigged",
            gate_body=PASS_GATE.format(gate_id="rigged-gate", plugin_id="rigged"),
        )
        self._write_rig_domain("rigged")
        state = self._state_with(["rig-dom-setup", "action-ready"])
        self.assertFalse(run_gates.plugin_contributed_a_step(state, "rigged", self.home))

    def test_a_done_rig_step_makes_the_domain_due(self):
        _write_gated_plugin(
            self.home, "rigged",
            gate_body=PASS_GATE.format(gate_id="rigged-gate", plugin_id="rigged"),
        )
        self._write_rig_domain("rigged")
        state = self._state_with(["rig-dom-setup", "rig-dom-track", "action-ready"])
        self.assertTrue(run_gates.plugin_contributed_a_step(state, "rigged", self.home))

    def test_a_domain_without_rig_steps_keeps_the_old_rule(self):
        _write_gated_plugin(
            self.home, "plain",
            gate_body=PASS_GATE.format(gate_id="plain-gate", plugin_id="plain"),
        )
        (self.home / "plugins" / "plain" / "domain.json").write_text(json.dumps({
            "id": "plain-dom",
            "setupSteps": [["plain-setup", "Read {plugin_dir}/contract.md completely"]],
            "setupAnchorBefore": "local-spec-search",
        }), encoding="utf-8")
        state = _action_ready_state(self.workspace, self.spec_path, profile="plain-dom")
        for entry in state["checklist"]:
            entry["status"] = "done" if entry["id"] == "plain-setup" else "pending"
        from workflow_state import save_state
        save_state(self.workspace / ".img2threejs" / "state.json", state)
        self.assertTrue(run_gates.plugin_contributed_a_step(state, "plain", self.home))


class ParticipationLookupFailureFailsLoudNotOpen(RunGatesTestBase):
    """A DomainRegistryError during the participation check used to be swallowed as "uninvolved",
    which silently skipped a blocking gate whose domain steps had actually run -- fail-open in the
    enforcement layer. It must stop the run naming the plugin instead."""

    def test_a_registry_error_stops_the_run_instead_of_skipping_gates(self):
        from unittest import mock

        from domains import DomainRegistryError

        _write_gated_plugin(
            self.home, "gated",
            gate_body=PASS_GATE.format(gate_id="gated-gate", plugin_id="gated"),
        )
        _action_ready_state(self.workspace, self.spec_path)
        with mock.patch.object(
            run_gates, "plugin_contributed_a_step",
            side_effect=DomainRegistryError("domain id 'x' is declared twice"),
        ):
            with self.assertRaises(run_gates.GateExecutionError) as ctx:
                run_gates.run_gates_for_workspace(self.workspace, home=self.home)
        self.assertIn("participation", str(ctx.exception))
        self.assertIn("gated", str(ctx.exception))


class RunnerStderrSurfacesOnEnvelopeFailure(RunGatesTestBase):
    """A runner that never printed an envelope said why on stderr — e.g. an installed harness too
    old for a forwarded --gate-timeout rejects it with argparse's "unrecognized arguments" and no
    stdout at all. That cause must reach the error, not be flattened into "no parseable envelope"."""

    def test_the_runners_stderr_is_named_when_no_envelope_was_printed(self):
        _write_gated_plugin(
            self.home, "gated",
            gate_body=PASS_GATE.format(gate_id="gated-gate", plugin_id="gated"),
        )
        # Replace the copied runner with a stub that mimics a pre-0.2.2 harness: argparse rejects
        # the forwarded flag on stderr and exits 2 without printing an envelope.
        (self.home / "harness" / "img2_core" / "gate_runner.py").write_text(
            "import sys\n"
            "sys.stderr.write(\"gate_runner.py: error: unrecognized arguments: --gate-timeout 60\\n\")\n"
            "sys.exit(2)\n",
            encoding="utf-8",
        )
        with self.assertRaises(run_gates.GateExecutionError) as ctx:
            run_gates.run_plugin_gates(
                "gated", self.home / "plugins" / "gated",
                workspace=self.workspace, home=self.home, gate_timeout=60,
            )
        self.assertIn("unrecognized arguments: --gate-timeout", str(ctx.exception))


class BlockingStopNamesGateAndPlugin(RunGatesTestBase):
    def test_a_blocking_failure_stops_the_run_naming_gate_and_plugin(self):
        state = _action_ready_state(self.workspace, self.spec_path)
        state["targetSelection"] = {"pluginId": "echo-plugin", "kind": "echo"}
        save_state(self.workspace / ".img2threejs" / "state.json", state)
        _write_gated_plugin(
            self.home, "echo-plugin",
            gate_body=FAIL_GATE.format(gate_id="echo-plugin-gate", plugin_id="echo-plugin"),
        )
        with self.assertRaises(run_gates.GateExecutionError) as ctx:
            run_gates.run_gates_for_workspace(self.workspace, home=self.home)
        self.assertEqual(ctx.exception.classification, "blocking-stop")
        self.assertIn("echo-plugin", str(ctx.exception))
        self.assertIn("echo-plugin-gate", str(ctx.exception))

    def test_a_non_blocking_failure_does_not_stop_the_run(self):
        state = _action_ready_state(self.workspace, self.spec_path)
        state["targetSelection"] = {"pluginId": "echo-plugin", "kind": "echo"}
        save_state(self.workspace / ".img2threejs" / "state.json", state)
        _write_gated_plugin(
            self.home, "echo-plugin",
            gate_body=FAIL_GATE.format(gate_id="echo-plugin-gate", plugin_id="echo-plugin"),
            blocking=False,
        )
        results = run_gates.run_gates_for_workspace(self.workspace, home=self.home)
        self.assertEqual(results["echo-plugin"]["results"][0]["status"], "fail")
        self.assertFalse(results["echo-plugin"]["stopped"])


class MalformedEnvelopeIsAnError(unittest.TestCase):
    def test_non_json_stdout_is_an_error(self):
        with self.assertRaises(run_gates.GateExecutionError) as ctx:
            run_gates.parse_gate_run_envelope(b"not json at all")
        self.assertEqual(ctx.exception.classification, "error")

    def test_json_that_is_not_an_img2_gate_run_envelope_is_an_error(self):
        with self.assertRaises(run_gates.GateExecutionError):
            run_gates.parse_gate_run_envelope(json.dumps({"kind": "something-else"}).encode())

    def test_wrong_version_is_an_error(self):
        with self.assertRaises(run_gates.GateExecutionError):
            run_gates.parse_gate_run_envelope(
                json.dumps({"kind": "img2.gate-run", "version": 2, "results": [], "stopped": False}).encode()
            )

    def test_exit_code_disagreeing_with_stopped_is_an_error(self):
        doc = {"kind": "img2.gate-run", "version": 1, "results": [], "stopped": False}
        with self.assertRaises(run_gates.GateExecutionError) as ctx:
            run_gates._check_exit_agreement(doc, 1)
        self.assertIn("disagrees", str(ctx.exception))

    def test_a_config_error_from_the_runner_is_an_error_not_a_pass(self):
        doc = {"kind": "img2.gate-run", "version": 1, "results": [], "stopped": True, "error": "no gates.json"}
        with self.assertRaises(run_gates.GateExecutionError) as ctx:
            run_gates._check_exit_agreement(doc, 2)
        self.assertIn("config error", str(ctx.exception))


class HangTripsTheBoundAndNamesTheGate(RunGatesTestBase):
    def test_a_hanging_non_blocking_gate_is_named_in_the_aggregate(self):
        # Non-blocking here specifically so the per-gate result is inspectable directly, rather
        # than only reachable through the raised exception's message (which the blocking case,
        # tested below, already covers).
        state = _action_ready_state(self.workspace, self.spec_path)
        state["targetSelection"] = {"pluginId": "hang-plugin", "kind": "hang"}
        save_state(self.workspace / ".img2threejs" / "state.json", state)
        _write_gated_plugin(self.home, "hang-plugin", gate_body=HANG_GATE, blocking=False)
        results = run_gates.run_gates_for_workspace(self.workspace, home=self.home, gate_timeout=1)
        result = results["hang-plugin"]["results"][0]
        self.assertEqual(result["status"], "error")
        self.assertIn("timed out after 1s", result["reasons"][0])

    def test_a_hanging_blocking_gate_stops_the_run_naming_it(self):
        state = _action_ready_state(self.workspace, self.spec_path)
        state["targetSelection"] = {"pluginId": "hang-plugin", "kind": "hang"}
        save_state(self.workspace / ".img2threejs" / "state.json", state)
        _write_gated_plugin(self.home, "hang-plugin", gate_body=HANG_GATE, blocking=True)
        with self.assertRaises(run_gates.GateExecutionError) as ctx:
            run_gates.run_gates_for_workspace(self.workspace, home=self.home, gate_timeout=1)
        self.assertEqual(ctx.exception.classification, "blocking-stop")
        self.assertIn("hang-plugin", str(ctx.exception))
        self.assertIn("hang-plugin-gate", str(ctx.exception))


class RunnerWorksWithImg2HomeUnsetInTheParentEnv(unittest.TestCase):
    """Task 4.6's literal scenario: the leaf gate process sees IMG2_HOME even though `run_gates.py`
    itself is invoked with IMG2_HOME genuinely absent from ITS OWN process env -- relying entirely
    on the `~/.img2` fallback every reader in this ecosystem shares, via a fake $HOME whose `.img2`
    is symlinked to the scratch harness (the same trick used to reproduce round-3's H1 by hand: a
    real unset-env repro needs a real fallback location to resolve to, or it just proves nothing).
    `run_gates.py`'s own bounded runner still injects IMG2_HOME into `gate_runner.py`'s env
    (D6/round-3 H1), and `gate_runner.py`'s own fix (task 4.4) re-injects/derives it again for the
    leaf gate -- this test proves the WHOLE chain ends with IMG2_HOME present, rather than trusting
    each layer's injection in isolation."""

    def setUp(self):
        if not HARNESS_DIR.is_dir():
            self.skipTest(f"sibling checkout not found: {HARNESS_DIR}")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        real_home = self.root / "real-img2-home"
        import shutil
        shutil.copytree(HARNESS_DIR / "img2_core", real_home / "harness" / "img2_core",
                         ignore=shutil.ignore_patterns("__pycache__"))
        self.fake_home_dir = self.root / "fake-home"
        self.fake_home_dir.mkdir()
        (self.fake_home_dir / ".img2").symlink_to(real_home)
        self.home = real_home
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        self.spec_path = self.workspace / "spec.json"
        self.spec_path.write_text(json.dumps({"buildPasses": [{"id": "blockout"}]}), encoding="utf-8")
        _action_ready_state(self.workspace, self.spec_path)
        state = json.loads((self.workspace / ".img2threejs" / "state.json").read_text(encoding="utf-8"))
        state["targetSelection"] = {"pluginId": "echo-plugin", "kind": "echo"}
        (self.workspace / ".img2threejs" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (self.home / "plugins.json").write_text(json.dumps({"version": 1, "plugins": [
            {"id": "echo-plugin", "repo": "x", "ref": "v1", "resolvedSha": "x", "addedAt": "2026-01-01"}
        ]}), encoding="utf-8")
        plugin_dir = self.home / "plugins" / "echo-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "gates.json").write_text(json.dumps([{
            "id": "env-probe", "command": f"{sys.executable} {plugin_dir}/gate.py", "blocking": True, "after": [],
        }]), encoding="utf-8")
        (plugin_dir / "gate.py").write_text(textwrap.dedent("""
            import json, os, sys
            ok = bool(os.environ.get("IMG2_HOME"))
            print(json.dumps({"kind": "img2.gate-verdict", "version": 1, "gate": "env-probe",
                              "plugin": "echo-plugin", "status": "pass" if ok else "error",
                              "reasons": [] if ok else ["IMG2_HOME missing"], "evidence": {}}))
            sys.exit(0 if ok else 2)
        """), encoding="utf-8")

    def test_run_gates_cli_works_with_img2_home_unset_in_the_parent_env(self):
        env = {k: v for k, v in os.environ.items() if k not in ("IMG2_HOME", "IMG2THREEJS_HOME")}
        env["HOME"] = str(self.fake_home_dir)
        proc = subprocess.run(
            [sys.executable, str(ROOT / "forge" / "stage3_build" / "run_gates.py"), "--workspace", str(self.workspace)],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["echo-plugin"]["results"][0]["status"], "pass")


@unittest.skipUnless(__import__("shutil").which("node"), "node is not on PATH")
@unittest.skipUnless(HARNESS_DIR.is_dir(), f"sibling checkout not found: {HARNESS_DIR}")
class ArgvDriftGuard(unittest.TestCase):
    """Task 4.1: the constructed argv must equal the harness's own `gateRunnerArgv` output
    (`bin/img2.mjs:807-809`) -- §13:308 documents the MODULE form while the code emits the
    FILE-PATH form, exactly the kind of drift this test pins against the code's actual behavior,
    not the doc. `gateRunnerArgv` itself is not exported, so this drives it the only way a caller
    can: through `img2 capabilities`, which surfaces it verbatim in each provider's `gateRunner`
    field whenever that plugin ships a `gates.json`."""

    def test_gate_runner_argv_matches_the_harness_capabilities_output(self):
        import shutil

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "img2home"
            shutil.copytree(HARNESS_DIR, home / "harness",
                             ignore=shutil.ignore_patterns("node_modules", ".git", "__pycache__"))
            plugin_dir = home / "plugins" / "gated-plugin"
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "gates.json").write_text(json.dumps([
                {"id": "g", "command": "python3 {plugin_dir}/gate.py", "blocking": True, "after": []}
            ]), encoding="utf-8")
            (plugin_dir / "gate.py").write_text("", encoding="utf-8")
            (plugin_dir / "plugin.json").write_text(json.dumps({
                "schema": 1, "name": "gated-plugin", "version": "0.1.0", "description": "fixture",
                "capabilities": [{"from": "sculpt-spec", "to": "whatever"}],
                "requires": {"harness": ">=0.1.0", "coreApi": 1},
            }), encoding="utf-8")
            (plugin_dir / "steps.json").write_text("[]", encoding="utf-8")
            (plugin_dir / "SKILL.md").write_text("---\nname: gated-plugin\ndescription: fixture\n---\nfixture\n", encoding="utf-8")
            (home / "plugins.json").write_text(json.dumps({"version": 1, "plugins": [
                {"id": "gated-plugin", "repo": "local", "ref": "local", "resolvedSha": "local", "addedAt": "2026-01-01"}
            ]}), encoding="utf-8")

            proc = subprocess.run(
                ["node", str(home / "harness" / "bin" / "img2.mjs"), "capabilities",
                 "--from-kind", "sculpt-spec", "--to-kind", "whatever", "--json"],
                env={**os.environ, "IMG2_HOME": str(home)}, capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            answer = json.loads(proc.stdout)
            js_argv = answer["providers"][0]["gateRunner"]["argv"]

            python_argv = run_gates.gate_runner_argv(home, plugin_dir)
            self.assertEqual(python_argv, js_argv)


if __name__ == "__main__":
    unittest.main()
