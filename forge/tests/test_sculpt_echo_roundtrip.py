"""The fixture round-trip, wired as a hard gate (task 3.14, `establish-the-emission-target-contract`).

install -> doctor -> resolve -> --target echo -> artifact produced -> the base repo's working tree
is unchanged. This is the mechanically falsifiable form of "zero base changes" (D8) -- the only
non-base evidence for the ecosystem claim.

No `.github/workflows/*.yml` exists in either repo today, so there is no separate CI pipeline file
to add a step to; this test module IS the enforcement mechanism, in the same sense
`test_suite_integrity.py`'s COLLECTED_FLOOR is -- it runs every time the base suite runs.

This test genuinely invokes `img2 add --link` and `img2 doctor` -- `doctor` requires the on-disk
state `add`/`sync` produces (host skill symlinks under `~/.claude/skills/` etc., `_img2_local.py`
sync stubs inside the plugin checkout) to pass at all; there is no hermetic shortcut that bypasses
those checks and still exercises real `doctor`. Those host-linking side effects are real and are
NOT scoped to the scratch `$IMG2_HOME` this test builds, so this snapshots every location `add`
touches before running and restores it exactly in `addCleanup` (registered before any mutation, so
it runs even on assertion failure) -- a developer's actual `~/.claude/skills/`, `~/.codex/skills/`,
`~/.config/opencode/skills/` and `~/.claude/settings.json` must read identically before and after.

Deliberately does NOT call `img2 install`: it provisions the harness via `git clone` from the local
checkout, which captures only the last COMMIT -- any uncommitted harness work (there is some, from
other lanes of this same change) would silently be missing from the scratch home, and this test
would then be exercising a stale harness instead of the one actually on disk. `$IMG2_HOME/harness`
is populated with a plain recursive copy of the live working tree instead, so uncommitted changes
are exercised the same way a developer running this by hand would see them.

Skips (does not fail) when `node` is not on PATH, or when the sibling `img2-harness` /
`plugin-sculpt-echo` checkouts are not present -- this repository does not vendor either.
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
HARNESS_DIR = ORG_ROOT / "img2-harness"
PLUGIN_DIR = ORG_ROOT / "plugin-sculpt-echo"
EMIT_TARGET = ROOT / "forge" / "stage3_build" / "emit_target.py"

SKILL_LINKS = (
    Path.home() / ".claude" / "skills" / "img2-sculpt-echo",
    Path.home() / ".codex" / "skills" / "img2-sculpt-echo",
    Path.home() / ".config" / "opencode" / "skills" / "img2-sculpt-echo",
)
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
PLUGIN_SYNC_STUBS = (PLUGIN_DIR / "_img2_local.py", PLUGIN_DIR / "tools" / "_img2_local.py")

sys.path.insert(0, str(ROOT / "forge" / "_shared"))


def _node_available() -> bool:
    return shutil.which("node") is not None


def _git_status(repo: Path) -> str:
    proc = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else ""


def _snapshot_link(path: Path) -> str | None:
    return os.readlink(path) if path.is_symlink() else None


def _restore_link(path: Path, previous_target: str | None) -> None:
    if path.is_symlink() or path.exists():
        path.unlink()
    if previous_target is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(previous_target)


def _snapshot_additional_directories() -> list | None:
    if not CLAUDE_SETTINGS.is_file():
        return None
    try:
        data = json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return list(data.get("permissions", {}).get("additionalDirectories", []))


def _restore_additional_directories(previous: list | None) -> None:
    if previous is None or not CLAUDE_SETTINGS.is_file():
        return
    data = json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))
    data.setdefault("permissions", {})["additionalDirectories"] = previous
    CLAUDE_SETTINGS.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@unittest.skipUnless(_node_available(), "node is not on PATH")
@unittest.skipUnless(HARNESS_DIR.is_dir(), f"sibling checkout not found: {HARNESS_DIR}")
@unittest.skipUnless(PLUGIN_DIR.is_dir(), f"sibling checkout not found: {PLUGIN_DIR}")
class SculptEchoRoundTrip(unittest.TestCase):
    def setUp(self):
        # Registered before any mutating command runs, so a failed assertion mid-test still
        # restores every host location `add --link` / `sync` can touch.
        for link in SKILL_LINKS:
            self.addCleanup(_restore_link, link, _snapshot_link(link))
        self.addCleanup(_restore_additional_directories, _snapshot_additional_directories())
        for stub in PLUGIN_SYNC_STUBS:
            self.addCleanup(lambda p=stub: p.unlink(missing_ok=True))

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.home = root / "img2home"
        # Plain copy, not `img2 install` (see module docstring): captures the live working tree,
        # including any uncommitted harness changes, instead of only the last git commit.
        shutil.copytree(
            HARNESS_DIR, self.home / "harness",
            ignore=shutil.ignore_patterns("node_modules", ".git", "__pycache__"),
        )
        for name in ("plugins", "generated", "backups"):
            (self.home / name).mkdir(parents=True, exist_ok=True)
        (self.home / "plugins.json").write_text(json.dumps({"version": 1, "plugins": []}), encoding="utf-8")
        (self.home / "receipts.json").write_text("[]", encoding="utf-8")
        self.workspace = root / "ws"
        self.workspace.mkdir()
        self.env = {**os.environ, "IMG2_HOME": str(self.home)}

    def run_node(self, *args):
        return subprocess.run(
            ["node", str(self.home / "harness" / "bin" / "img2.mjs"), *args],
            env=self.env, capture_output=True, text=True,
        )

    def test_install_doctor_resolve_target_round_trips_with_a_clean_base_tree(self):
        before = _git_status(ROOT)

        added = self.run_node("add", "--link", str(PLUGIN_DIR))
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)

        doctor = self.run_node("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        self.assertIn("ok (1 plugin", doctor.stdout)

        capabilities = self.run_node("capabilities", "--from-kind", "sculpt-spec", "--to-kind", "echo", "--json")
        self.assertEqual(capabilities.returncode, 0, capabilities.stderr)
        answer = json.loads(capabilities.stdout)
        self.assertEqual(len(answer["providers"]), 1)
        self.assertEqual(answer["providers"][0]["plugin"], "sculpt-echo")

        from workflow_state import new_state, save_state  # noqa: E402 (sys.path set at module scope)

        spec_path = self.workspace / "spec.json"
        spec_path.write_text(
            json.dumps({"targetId": "ci-roundtrip", "buildPasses": [{"id": "blockout"}], "componentTree": [{"id": "body"}]}),
            encoding="utf-8",
        )
        state = new_state("ref.png", spec=str(spec_path))
        for entry in state["checklist"]:
            entry["status"] = "done"
            entry["evidence"] = ["fixture"]
        state["currentPass"] = "complete"
        state["status"] = "complete"
        state["currentStep"] = "complete"
        save_state(self.workspace / ".img2threejs" / "state.json", state)

        proc = subprocess.run(
            [sys.executable, str(EMIT_TARGET), "--spec", str(spec_path), "--workspace", str(self.workspace), "--target", "echo"],
            env=self.env, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        artifact = self.workspace / ".img2" / "artifacts" / "sculpt-echo" / "echo.json"
        self.assertTrue(artifact.is_file())
        self.assertEqual(json.loads(artifact.read_text(encoding="utf-8"))["passCount"], 1)

        after = _git_status(ROOT)
        self.assertEqual(before, after, "the round-trip modified the base repository's working tree")


if __name__ == "__main__":
    unittest.main()
