"""The failure matrix against `plugin-sculpt-echo` (task 3.15,
`establish-the-emission-target-contract`).

Uninstall / crash / hang / wrong-kind, run against the real fixture plugin rather than a synthetic
stand-in -- `test_emit_target.py` already covers this matrix against throwaway fixture plugins;
this module exists so the one plugin authored from the wiki (task 3.13) is also exercised by it.

Every test pins IMG2_HOME to a temp/empty directory populated directly (no `img2 add`/`sync`, so no
host skill-symlink side effects here -- see `test_sculpt_echo_roundtrip.py` for the one test that
legitimately needs those).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORG_ROOT = ROOT.parent
PLUGIN_DIR = ORG_ROOT / "plugin-sculpt-echo"
HARNESS_DIR = ORG_ROOT / "img2-harness"
EMIT_TARGET = ROOT / "forge" / "stage3_build" / "emit_target.py"

sys.path.insert(0, str(ROOT / "forge" / "_shared"))


def _action_ready_state_path(workspace: Path, spec_path: Path) -> Path:
    from workflow_state import new_state, save_state

    state = new_state("ref.png", spec=str(spec_path))
    for entry in state["checklist"]:
        entry["status"] = "done"
        entry["evidence"] = ["fixture"]
    state["currentPass"] = "complete"
    state["status"] = "complete"
    state["currentStep"] = "complete"
    path = workspace / ".img2threejs" / "state.json"
    save_state(path, state)
    return path


@unittest.skipUnless(PLUGIN_DIR.is_dir(), f"sibling checkout not found: {PLUGIN_DIR}")
@unittest.skipUnless(HARNESS_DIR.is_dir(), f"sibling checkout not found: {HARNESS_DIR}")
class SculptEchoFailureMatrixBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.home = self.root / "img2home"
        self.home.mkdir()
        (self.home / "plugins").mkdir()
        # A real img2_core, copied rather than installed, so the plugin's bootstrap stanza
        # (`sys.path.insert(0, IMG2_HOME/harness)`) resolves it exactly as it would for real.
        shutil.copytree(HARNESS_DIR / "img2_core", self.home / "harness" / "img2_core",
                         ignore=shutil.ignore_patterns("__pycache__"))
        (self.home / "harness" / "package.json").write_text(json.dumps({"version": "0.5.0"}), encoding="utf-8")
        self.workspace = self.root / "ws"
        self.workspace.mkdir()
        self.spec_path = self.workspace / "spec.json"
        self.spec_path.write_text(
            json.dumps({"targetId": "matrix", "buildPasses": [{"id": "blockout"}], "componentTree": [{"id": "body"}]}),
            encoding="utf-8",
        )
        self.env = {**os.environ, "IMG2_HOME": str(self.home)}

    def _install_real_plugin(self):
        (self.home / "plugins" / "sculpt-echo").symlink_to(PLUGIN_DIR)
        (self.home / "plugins.json").write_text(
            json.dumps({"version": 1, "plugins": [
                {"id": "sculpt-echo", "repo": "local", "ref": "local", "resolvedSha": "local", "addedAt": "2026-01-01"}
            ]}),
            encoding="utf-8",
        )

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(EMIT_TARGET), "--spec", str(self.spec_path), "--workspace", str(self.workspace), *args],
            env=self.env, capture_output=True, text=True,
        )


class Uninstalled(SculptEchoFailureMatrixBase):
    def test_selecting_echo_with_no_plugin_installed_fails_naming_it(self):
        (self.home / "plugins.json").write_text(json.dumps({"version": 1, "plugins": []}), encoding="utf-8")
        _action_ready_state_path(self.workspace, self.spec_path)
        proc = self.run_cli("--target", "echo")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("echo", proc.stderr)
        self.assertIn("no installed plugin provides", proc.stderr)


class Crashes(SculptEchoFailureMatrixBase):
    def test_a_crashing_echo_emitter_surfaces_its_exit_code(self):
        self._install_real_plugin()
        _action_ready_state_path(self.workspace, self.spec_path)
        # Corrupt the spec so emit_echo.py's own JSON parse fails and it exits non-zero -- exactly
        # the shape "the plugin's own tool crashed" takes here, without needing a second fixture.
        self.spec_path.write_text("not valid json {{{", encoding="utf-8")
        proc = self.run_cli("--target", "echo")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("exited 1", proc.stderr)
        self.assertFalse((self.workspace / ".img2" / "artifacts" / "sculpt-echo" / "echo.json").exists())


class Hangs(SculptEchoFailureMatrixBase):
    def test_a_hung_echo_emitter_is_killed_at_the_declared_bound(self):
        # A copy of the real plugin with its emit tool swapped for one that hangs -- the fixture
        # plugin itself has no reason to ship a hanging code path, so the matrix supplies one.
        hanging_plugin = self.root / "sculpt-echo-hangs"
        shutil.copytree(PLUGIN_DIR, hanging_plugin, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        (hanging_plugin / "tools" / "emit_echo.py").write_text(
            "import time\n"
            "import sys\n"
            "sys.path.insert(0, __file__.rsplit('/', 1)[0])\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        steps = json.loads((hanging_plugin / "steps.json").read_text(encoding="utf-8"))
        steps[0]["timeoutSeconds"] = 1
        (hanging_plugin / "steps.json").write_text(json.dumps(steps), encoding="utf-8")

        (self.home / "plugins" / "sculpt-echo").symlink_to(hanging_plugin)
        (self.home / "plugins.json").write_text(
            json.dumps({"version": 1, "plugins": [
                {"id": "sculpt-echo", "repo": "local", "ref": "local", "resolvedSha": "local", "addedAt": "2026-01-01"}
            ]}),
            encoding="utf-8",
        )
        _action_ready_state_path(self.workspace, self.spec_path)
        proc = self.run_cli("--target", "echo")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("timed out after 1s", proc.stderr)
        self.assertFalse((self.workspace / ".img2" / "artifacts" / "sculpt-echo" / "echo.json").exists())


class WrongKind(SculptEchoFailureMatrixBase):
    def test_an_echo_artifact_that_is_not_json_would_still_pass_since_echo_has_no_prober(self):
        # Honest limit, not a defect: "echo" has no container prober (only "glb" does today), so a
        # wrong-shaped echo.json is accepted on existence/size/location alone -- recorded here so
        # the matrix documents the limit rather than silently skipping this case.
        self._install_real_plugin()
        _action_ready_state_path(self.workspace, self.spec_path)
        proc = self.run_cli("--target", "echo")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        provenance = json.loads(
            (self.workspace / ".img2" / "artifacts" / "sculpt-echo" / "echo.json.provenance.json").read_text(encoding="utf-8")
        )
        self.assertEqual(provenance["verification"]["level"], "existence+size+location")
        self.assertIn("no further base quality warranty applies", provenance["verification"]["note"])


if __name__ == "__main__":
    unittest.main()
