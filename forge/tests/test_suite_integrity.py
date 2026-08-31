"""The suite must not shrink silently.

A module that fails to import does not fail loudly under unittest discovery -- it is replaced by a
single synthetic `_FailedTest`, so N real tests become 1 error and the count drops without anything
naming what went missing. That is not hypothetical here: a module-scope
`from cs2_review import load_review_scene` in forge/stage4_review/append_review.py meant a missing
domain module turned test_structure_gates.py's thirteen base structural-gate tests into one loader
error. Measured: `Ran 13 tests ... OK` became `Ran 1 test ... FAILED (errors=1)`.

These two checks make that class of loss visible on its own terms, rather than as a number a human
has to notice moving.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from functools import lru_cache
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

# Raise this deliberately when tests are added; never lower it to make a red suite green. A drop
# means tests stopped being collected, which is the failure this file exists to catch.
#
# Lowered deliberately, with accounting, when the CS2 domain moved to its own plugin: 1134 -> 1109.
# Everything in the gap now runs in the plugin's own suite (24 tests, standalone) except two that
# could live on neither side and were rewritten as base-side tests in test_domain_spec_contract.py.
# The import guard above is what caught test_cs2_foundation going dark mid-move -- reported as a
# synthetic loader error rather than a quietly smaller suite.
#
# 1119 -> 1165 for establish-the-emission-target-contract slice 3 (the socket): test_targets.py
# (21), test_emit_target.py (13), test_target_reference_conformance.py (7 -- the envelope-parity
# scenario originally failed on purpose per task 3.10, then went green once the lead's exit-code
# reconciliation was implemented; 2 more classifier tests were added alongside it),
# test_sculpt_echo_roundtrip.py (1, skips without node/sibling checkouts),
# test_sculpt_echo_failure_matrix.py (4, same skip guard).
# 1165 -> 1182 for the same change's slice 4 (gate execution): test_run_gates.py (17 -- argv
# drift guard, two-clause participation for both domain and target plugins, blocking/non-blocking
# stop, malformed-envelope classification, hang-names-the-gate for both blocking and non-blocking
# gates, and the full-chain IMG2_HOME-unset-in-the-parent-env scenario).
# 1182 -> 1184: the lead's fix to clause (i)'s owner lookup (domain_owner_plugin, derived from the
# registry layout instead of assumed from a name match) added 2 tests -- a plugin whose package
# name differs from its domain id, and an in-repo domain's profile correctly attributing no
# installed plugin as its owner.
COLLECTED_FLOOR = 1192


REPO_ROOT = TESTS_DIR.parents[1]

# Discovery MUST run in a fresh interpreter. Discovering the suite from inside the suite is both
# order- and path-dependent: importing a test module runs its module-level sys.path.insert calls, so
# a second in-process discover resolves imports against a path the first one did not have. Measured:
# with a broken module shadowing a real one, the first in-process discover saw 1097 tests and 4
# unittest.loader._FailedTest entries, and a second discover in the same process saw a clean 1124
# and zero -- so whichever check happened to run second reported the suite healthy. A subprocess has
# one import world and cannot drift like that.
_PROBE = """
import json, unittest
def leaves(s):
    for x in s:
        if isinstance(x, unittest.TestSuite):
            yield from leaves(x)
        else:
            yield x
tests = list(leaves(unittest.TestLoader().discover("forge/tests", pattern="test_*.py")))
print(json.dumps({
    "collected": len(tests),
    "failed": [t.id() for t in tests if type(t).__name__ == "_FailedTest"],
}))
"""


@lru_cache(maxsize=1)
def _discover() -> dict:
    # Cached: the subprocess imports every test module, so calling it once per assertion cost ~22s
    # of the suite's wall clock for an identical answer.
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"test discovery itself failed:\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


class SuiteIntegrity(unittest.TestCase):
    def test_no_module_fails_to_import(self) -> None:
        broken = _discover()["failed"]
        self.assertEqual(
            broken,
            [],
            "a test module failed to import, so its tests were replaced by one synthetic error "
            "instead of running: " + "; ".join(broken),
        )

    def test_collected_count_has_not_dropped(self) -> None:
        collected = _discover()["collected"]
        self.assertGreaterEqual(
            collected,
            COLLECTED_FLOOR,
            f"the suite collects {collected} tests but the recorded floor is {COLLECTED_FLOOR}. "
            "Tests stopped being collected. Find out which module stopped importing before "
            "touching this number.",
        )


if __name__ == "__main__":
    unittest.main()
