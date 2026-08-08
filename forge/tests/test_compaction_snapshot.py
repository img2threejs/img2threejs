#!/usr/bin/env python3
"""Unit tests for the compaction resume snapshot.

The feature was ported into this repo from the ~/.claude and ~/.codex host skill copies, which
carried it while this repo never did (`git log -S "def cmd_compact"` returns 0 commits here).
It arrived with no tests, so these are new: the port added ~210 lines of behaviour that nothing
covered, and the repo's own rule is that a behaviour change ships focused tests.

What matters about a resume snapshot is narrow and checkable: it must carry the facts an agent
resuming after compaction needs (which state file, what step, what pass, what status), it must
stay bounded rather than growing with history, and it must never be mistaken for the authority
-- the state JSON remains that.

Run: python3 forge/tests/test_compaction_snapshot.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))

from workflow_state import (  # noqa: E402
    COMPACTION_VERSION,
    build_resume_snapshot,
    resume_snapshot_path,
    write_resume_snapshot,
)


def make_state(**over):
    state = {
        "schemaVersion": 1,
        "status": "active",
        "profile": "cs2",
        "currentStep": "render-capture",
        "currentPass": "blockout",
        "checklist": [
            {"id": "image-analysis", "scope": "setup", "status": "done",
             "evidence": ["a.md"], "reason": "", "command": "analyze {reference}"},
            {"id": "render-capture", "scope": "pass", "status": "pending",
             "evidence": [], "reason": "", "command": "capture a render"},
        ],
        "loops": {"perPass": {}, "total": 0, "maxPerPass": 3, "maxTotal": 6},
        "artifacts": {"reference": "refs/front.png"},
        "passHistory": [],
        "reviewCursor": 0,
        "iterationAction": "",
        "stopReason": "",
    }
    state.update(over)
    return state


class ResumeSnapshotPathTest(unittest.TestCase):
    def test_snapshot_sits_beside_the_state_it_describes(self):
        path = resume_snapshot_path(Path(".img2threejs/state.json"), make_state())
        self.assertTrue(str(path).endswith(".md"), path)
        self.assertIn(".img2threejs", str(path))

    def test_a_versioned_state_gets_its_own_snapshot(self):
        """A versioned rebuild keeps a separate state file, and its snapshot must not overwrite
        the baseline's -- the repo's rule is that prior version evidence stays immutable."""
        base = resume_snapshot_path(Path(".img2threejs/state.json"), make_state())
        v2 = resume_snapshot_path(Path(".img2threejs/state-v2.json"), make_state())
        self.assertNotEqual(base, v2)


class BuildResumeSnapshotTest(unittest.TestCase):
    def test_snapshot_carries_the_facts_a_resuming_agent_needs(self):
        text = build_resume_snapshot(Path(".img2threejs/state.json"), make_state())
        for fact in ("render-capture", "blockout", "active", str(COMPACTION_VERSION)):
            self.assertIn(fact, text, f"missing {fact!r}")

    def test_snapshot_names_the_state_file_as_the_authority(self):
        """The snapshot is a bounded view, not the source of truth. If it ever reads as the
        authority, an agent will resume from a stale summary instead of the state JSON."""
        text = build_resume_snapshot(Path(".img2threejs/state.json"), make_state())
        self.assertIn("state.json", text)
        self.assertRegex(text.lower(), r"authorit|do not reconstruct")

    def test_snapshot_stays_bounded_as_history_grows(self):
        """The whole point is surviving compaction, so the snapshot must not scale with the
        history it summarises."""
        small = build_resume_snapshot(Path("s.json"), make_state())
        big = make_state(checklist=[
            {"id": f"step-{i}", "scope": "pass", "status": "done",
             "evidence": [f"e{i}.md"], "reason": "", "command": f"cmd {i}"}
            for i in range(400)
        ])
        self.assertLess(len(build_resume_snapshot(Path("s.json"), big)), len(small) * 4)


class WriteResumeSnapshotTest(unittest.TestCase):
    def test_write_creates_a_readable_file_and_returns_its_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state = make_state()
            state_path.write_text(json.dumps(state))
            out = write_resume_snapshot(state_path, state)
            self.assertTrue(out.exists(), out)
            self.assertGreater(out.stat().st_size, 0)
            self.assertIn("render-capture", out.read_text())

    def test_explicit_output_path_is_honoured(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state = make_state()
            state_path.write_text(json.dumps(state))
            target = Path(tmp) / "nested" / "resume.md"
            out = write_resume_snapshot(state_path, state, output=target)
            # resolve() both sides: on macOS tempfile hands back /var/... while the writer
            # resolves it to the real /private/var/..., and that is not a behaviour difference.
            self.assertEqual(out.resolve(), target.resolve())
            self.assertTrue(target.exists())

    def test_rewriting_replaces_rather_than_appends(self):
        """`init`/`mark` refresh the snapshot after every state write, so an appending writer
        would grow without bound and defeat the bounded-view guarantee."""
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state = make_state()
            state_path.write_text(json.dumps(state))
            first = write_resume_snapshot(state_path, state).read_text()
            second = write_resume_snapshot(state_path, state).read_text()
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
