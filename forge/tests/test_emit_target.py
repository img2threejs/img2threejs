"""emit_target.py socket matrix (task 3.12, `establish-the-emission-target-contract`).

Covers: null default (no file written, mtime unchanged); installed target produces a verified
artifact; missing target fails naming it; crash surfaces exit code; hang trips the first timeout;
wrong-kind refused naming both kinds; mid-pass invocation refused naming the unmet step; every test
pins IMG2_HOME to a temp/empty directory (`test_search_specs.py:243` pattern); one test runs
against a deliberately malformed plugins.json asserting the no-target path still succeeds.
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
sys.path.insert(0, str(ROOT / "forge"))
sys.path.insert(0, str(ROOT / "forge" / "_shared"))
sys.path.insert(0, str(ROOT / "forge" / "stage3_build"))

import emit_target  # noqa: E402
from workflow_state import new_state, save_state  # noqa: E402

EMIT_TARGET = ROOT / "forge" / "stage3_build" / "emit_target.py"


def _action_ready_state_path(workspace: Path) -> Path:
    """A workspace whose state has reached action-ready -- built directly rather than driven
    through the real sequential mark_steps() API, which is out of scope for this module (it is
    covered by test_workflow_state.py); this file only needs a state.json where the precondition
    check reads "done"."""
    state = new_state("ref.png", spec=str(workspace / "spec.json"))
    for entry in state["checklist"]:
        entry["status"] = "done"
        entry["evidence"] = ["fixture"]
    state["currentPass"] = "complete"
    state["status"] = "complete"
    state["currentStep"] = "complete"
    path = workspace / ".img2threejs" / "state.json"
    save_state(path, state)
    return path


def _write_plugin_target(
    home: Path,
    plugin_id: str,
    kind: str,
    *,
    tool_source: str,
    artifact_rel: str,
    deterministic: bool = False,
    timeout_seconds: int | None = None,
    harness_version: str = "0.5.0",
) -> None:
    (home / "harness").mkdir(parents=True, exist_ok=True)
    (home / "harness" / "package.json").write_text(json.dumps({"version": harness_version}), encoding="utf-8")
    plugin_dir = home / "plugins" / plugin_id
    (plugin_dir / "tools").mkdir(parents=True, exist_ok=True)
    (plugin_dir / "tools" / "emit.py").write_text(textwrap.dedent(tool_source), encoding="utf-8")
    manifest = {
        "schema": 1, "name": plugin_id, "version": "0.1.0", "description": "fixture",
        "capabilities": [{"from": "sculpt-spec", "to": kind}],
        "requires": {"harness": ">=0.1.0", "coreApi": 1},
    }
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    step = {
        "id": "emit", "after": [],
        "command": "python3 {plugin_dir}/tools/emit.py --spec {spec} --out {workspace}/" + artifact_rel,
        "provides": {"version": 1, "from": "sculpt-spec", "to": kind,
                     "artifact": {"kind": kind, "path": artifact_rel}},
        "deterministic": deterministic,
    }
    if timeout_seconds is not None:
        step["timeoutSeconds"] = timeout_seconds
    (plugin_dir / "steps.json").write_text(json.dumps([step]), encoding="utf-8")
    registry_path = home / "plugins.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else {"plugins": []}
    registry["plugins"].append({"id": plugin_id, "repo": "x", "ref": "v1", "resolvedSha": f"sha-{plugin_id}", "addedAt": "2026-01-01"})
    registry_path.write_text(json.dumps(registry), encoding="utf-8")


WRITES_JSON_TOOL = """
    import argparse, json, os
    p = argparse.ArgumentParser(); p.add_argument("--spec", required=True); p.add_argument("--out", required=True)
    a = p.parse_args()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump({"ok": True}, f)
"""

CRASHES_TOOL = """
    import argparse, os, sys
    p = argparse.ArgumentParser(); p.add_argument("--spec", required=True); p.add_argument("--out", required=True)
    a = p.parse_args()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w").write("partial")
    sys.exit(1)
"""

HANGS_TOOL = """
    import argparse, os, time
    p = argparse.ArgumentParser(); p.add_argument("--spec", required=True); p.add_argument("--out", required=True)
    a = p.parse_args()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w").write("x")
    time.sleep(30)
"""

WRONG_KIND_GLB_TOOL = """
    import argparse, os
    p = argparse.ArgumentParser(); p.add_argument("--spec", required=True); p.add_argument("--out", required=True)
    a = p.parse_args()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "wb").write(b"not a real glb")
"""


class EmitTargetTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.home = self.root / "img2home-empty"
        self.home.mkdir()
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        self.spec_path = self.workspace / "spec.json"
        self.spec_path.write_text(json.dumps({"buildPasses": [{"id": "blockout"}]}), encoding="utf-8")
        self.env = {**os.environ, "IMG2_HOME": str(self.home)}

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(EMIT_TARGET), "--spec", str(self.spec_path), "--workspace", str(self.workspace), *args],
            env=self.env, capture_output=True, text=True,
        )


class NullDefault(EmitTargetTestBase):
    def test_no_target_writes_nothing_and_succeeds(self):
        candidate = self.workspace / "src" / "createObjectModel.ts"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("// pre-existing", encoding="utf-8")
        before_mtime = candidate.stat().st_mtime_ns
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("src/createObjectModel.ts", proc.stdout)
        self.assertEqual(candidate.stat().st_mtime_ns, before_mtime)
        self.assertEqual(candidate.read_text(encoding="utf-8"), "// pre-existing")

    def test_no_target_never_reads_the_registry_even_when_it_is_malformed(self):
        (self.home / "plugins.json").write_text("not valid json {{{", encoding="utf-8")
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_help_does_not_touch_the_registry_either(self):
        (self.home / "plugins.json").write_text("not valid json {{{", encoding="utf-8")
        proc = subprocess.run([sys.executable, str(EMIT_TARGET), "--help"], env=self.env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class ActionReadyPrecondition(EmitTargetTestBase):
    def test_no_state_file_refuses_naming_the_gap(self):
        proc = self.run_cli("--target", "threejs-ts")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("action-ready", proc.stderr)
        self.assertIn("has not reached action-ready", proc.stderr)

    def test_mid_pass_state_refuses_naming_the_unmet_step(self):
        state = new_state("ref.png", spec=str(self.spec_path))
        # Leave everything pending -- the state exists but nothing is action-ready yet.
        save_state(self.workspace / ".img2threejs" / "state.json", state)
        proc = self.run_cli("--target", "threejs-ts")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("next required step", proc.stderr)
        self.assertIn(state["currentStep"], proc.stderr)
        self.assertNotIn("no target subprocess", proc.stdout)

    def test_action_ready_state_passes_the_precondition(self):
        _action_ready_state_path(self.workspace)
        # No plugin installed, so resolution itself fails -- but that proves the precondition was
        # cleared: the message is about resolution, not about action-ready.
        proc = self.run_cli("--target", "nope")
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("action-ready", proc.stderr)
        self.assertIn("nope", proc.stderr)


class InstalledTargetProducesAVerifiedArtifact(EmitTargetTestBase):
    def test_success_writes_artifact_and_provenance(self):
        _action_ready_state_path(self.workspace)
        _write_plugin_target(self.home, "echo-plugin", "echo", tool_source=WRITES_JSON_TOOL,
                              artifact_rel=".img2/artifacts/echo-plugin/echo.json")
        proc = self.run_cli("--target", "echo")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        artifact = self.workspace / ".img2" / "artifacts" / "echo-plugin" / "echo.json"
        self.assertTrue(artifact.is_file())
        provenance = json.loads(Path(str(artifact) + ".provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["plugin"]["id"], "echo-plugin")
        self.assertEqual(provenance["plugin"]["resolvedSha"], "sha-echo-plugin")
        self.assertIn("specContentHash", provenance)


class MissingTargetFailsNamingIt(EmitTargetTestBase):
    def test_unresolved_kind_fails_naming_it(self):
        _action_ready_state_path(self.workspace)
        proc = self.run_cli("--target", "does-not-exist")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("does-not-exist", proc.stderr)


class CrashSurfacesExitCode(EmitTargetTestBase):
    def test_a_crashing_target_fails_and_deletes_the_partial_artifact(self):
        _action_ready_state_path(self.workspace)
        _write_plugin_target(self.home, "crash-plugin", "crash-kind", tool_source=CRASHES_TOOL,
                              artifact_rel=".img2/artifacts/crash-plugin/out.json")
        proc = self.run_cli("--target", "crash-kind")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("exited 1", proc.stderr)
        self.assertFalse((self.workspace / ".img2" / "artifacts" / "crash-plugin" / "out.json").exists())


class HangTripsTheFirstTimeout(EmitTargetTestBase):
    def test_a_hanging_target_is_killed_and_names_the_bound(self):
        _action_ready_state_path(self.workspace)
        _write_plugin_target(self.home, "hang-plugin", "hang-kind", tool_source=HANGS_TOOL,
                              artifact_rel=".img2/artifacts/hang-plugin/out.json", timeout_seconds=1)
        proc = self.run_cli("--target", "hang-kind")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("timed out after 1s", proc.stderr)
        self.assertFalse((self.workspace / ".img2" / "artifacts" / "hang-plugin" / "out.json").exists())

    def test_a_declared_timeout_over_the_ceiling_is_refused(self):
        _action_ready_state_path(self.workspace)
        _write_plugin_target(self.home, "greedy-plugin", "greedy-kind", tool_source=WRITES_JSON_TOOL,
                              artifact_rel=".img2/artifacts/greedy-plugin/out.json", timeout_seconds=999999)
        proc = self.run_cli("--target", "greedy-kind")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("exceeds the base-owned ceiling", proc.stderr)


class WrongKindRefusedNamingBothKinds(EmitTargetTestBase):
    def test_a_declared_glb_that_is_not_a_glb_is_refused(self):
        _action_ready_state_path(self.workspace)
        _write_plugin_target(self.home, "fake-glb-plugin", "glb", tool_source=WRONG_KIND_GLB_TOOL,
                              artifact_rel=".img2/artifacts/fake-glb-plugin/out.glb")
        proc = self.run_cli("--target", "glb")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("glb", proc.stderr)
        self.assertIn("container check failed", proc.stderr)
        self.assertFalse((self.workspace / ".img2" / "artifacts" / "fake-glb-plugin" / "out.glb").exists())


class DeterminismMismatchFailsNamingTheTarget(EmitTargetTestBase):
    def test_a_target_that_lies_about_determinism_is_refused(self):
        _action_ready_state_path(self.workspace)
        nondeterministic_tool = textwrap.dedent("""
            import argparse, os, random, json
            p = argparse.ArgumentParser(); p.add_argument("--spec", required=True); p.add_argument("--out", required=True)
            a = p.parse_args()
            os.makedirs(os.path.dirname(a.out), exist_ok=True)
            with open(a.out, "w") as f:
                json.dump({"nonce": random.random()}, f)
        """)
        _write_plugin_target(self.home, "flaky-plugin", "flaky-kind", tool_source=nondeterministic_tool,
                              artifact_rel=".img2/artifacts/flaky-plugin/out.json", deterministic=True)
        proc = self.run_cli("--target", "flaky-kind")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("flaky-kind", proc.stderr)
        self.assertIn("different bytes", proc.stderr)
        self.assertFalse((self.workspace / ".img2" / "artifacts" / "flaky-plugin" / "out.json").exists())


if __name__ == "__main__":
    unittest.main()
