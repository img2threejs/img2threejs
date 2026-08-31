"""Shared byte-equality oracle-replay harness (task 3.11, `establish-the-emission-target-contract`).

Extracted from plugin-cs2's `test_cs2_oracle_replay.py` pattern (`:28-63`): replay a fixed input
through a real subprocess, assert its output is byte-identical to a recorded oracle fixture, and
run an anti-tamper guard first -- a byte comparison against a hollow or degenerate oracle proves
nothing about a working pipeline. Not named `test_*.py`: it holds no `TestCase` of its own and must
not be picked up by `unittest.TestLoader().discover(...)`, only imported by tests that use it.

Fixtures are per-change -- this change freezes its own (`forge/tests/fixtures/
reference_target_*`) -- only this harness is shared, meant to be reused by
`extract-character-into-its-own-plugin` and any future oracle-style replay test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Sequence


def assert_replay_matches_oracle(
    testcase,
    argv: Sequence[str],
    *,
    cwd: Path,
    expected_path: Path,
    out_path: Path,
    tamper_guard: Callable[[], None] | None = None,
) -> bytes:
    """Run `argv`, then assert `out_path`'s bytes equal `expected_path`'s recorded bytes.

    `tamper_guard`, if given, runs BEFORE the replay and must raise (via the testcase's own
    assertions) if the oracle fixture itself looks degenerate -- e.g. empty, or recording a failed
    run -- so a byte comparison cannot pass trivially against a fixture that no longer proves
    anything. Returns the actual bytes, for any assertion the caller wants to layer on top.
    """
    if tamper_guard is not None:
        tamper_guard()
    expected = expected_path.read_bytes()
    proc = subprocess.run(list(argv), cwd=cwd, capture_output=True, text=False)
    testcase.assertEqual(
        proc.returncode, 0, f"replay command failed:\n{proc.stderr.decode('utf-8', errors='replace')}"
    )
    actual = out_path.read_bytes()
    testcase.assertEqual(
        actual,
        expected,
        "Replay drifted from the recorded oracle. If the change was intentional, re-record the "
        "fixture in the same commit and say so -- do not relax this assertion.",
    )
    return actual
