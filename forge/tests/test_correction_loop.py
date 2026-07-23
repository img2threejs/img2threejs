#!/usr/bin/env python3
"""Tests for the bounded correction-loop stop policy (§3.6).

Pure stdlib unittest. The termination guarantee (test_hard_ceiling_always_terminates
and test_loop_cannot_exceed_max_iter) is the load-bearing property of this suite.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "stage4_review"))

from correction_loop import budget_exceeded, decide  # noqa: E402


def _it(fidelity, defect_tags=None, reverted=False):
    """Build one iteration record."""
    return {
        "fidelity": fidelity,
        "defectTags": list(defect_tags or []),
        "reverted": reverted,
    }


class CorrectionLoopTest(unittest.TestCase):
    def test_empty_history_continues(self):
        d = decide([])
        self.assertFalse(d["stop"])
        self.assertEqual(d["action"], "continue-iterating")
        self.assertEqual(d["reason"], "no iterations yet")

    def test_success_when_target_met_and_no_defects(self):
        d = decide([_it(0.90, [])])
        self.assertTrue(d["stop"])
        self.assertEqual(d["action"], "continue")

    def test_success_requires_no_defects(self):
        # Fidelity above target BUT open defects remain -> NOT success.
        d = decide([_it(0.95, ["visible-seam"])])
        self.assertNotEqual(d["action"], "continue")

    def test_repeated_defect_stops_refine_spec(self):
        history = [
            _it(0.60, ["seam"]),
            _it(0.72, ["seam", "gap"]),
        ]
        d = decide(history)
        self.assertTrue(d["stop"])
        self.assertEqual(d["action"], "refine-spec")
        self.assertIn("seam", d["reason"])

    def test_plateau_stops_request_input(self):
        # Second iteration improves by < min_delta and stays below target.
        history = [
            _it(0.60, []),
            _it(0.61, []),
        ]
        d = decide(history)
        self.assertTrue(d["stop"])
        self.assertEqual(d["action"], "request-input")

    def test_hard_ceiling_always_terminates(self):
        # Steadily improving (delta 0.05 > min_delta so PLATEAU cannot fire),
        # never reaching target 0.85, no shared defect tags, no reverts.
        history = [
            _it(0.50, []),
            _it(0.55, []),
            _it(0.60, []),
            _it(0.65, []),
            _it(0.70, []),
            _it(0.75, []),
        ]
        self.assertEqual(len(history), 6)  # == default max_iter
        d = decide(history)
        self.assertTrue(d["stop"])
        self.assertIn("ceiling", d["reason"].lower())

    def test_oscillation_two_reverts_stops(self):
        history = [
            _it(0.50, [], reverted=True),
            _it(0.50, [], reverted=False),
            _it(0.50, [], reverted=True),
        ]
        d = decide(history)
        self.assertTrue(d["stop"])
        self.assertEqual(d["action"], "refine-spec")
        self.assertEqual(d["reason"], "oscillating/thrashing")

    def test_loop_cannot_exceed_max_iter(self):
        # Simulate a real caller loop. Even with monotonic tiny improvements,
        # the body MUST execute at most max_iter times. A hard safety counter
        # fails the test if the loop somehow refuses to terminate.
        max_iter = 6
        history = []
        iterations = 0
        safety = 0
        while not decide(history, max_iter=max_iter)["stop"]:
            safety += 1
            if safety > 100:
                self.fail("did not terminate")
            history.append(_it(0.001 * (len(history) + 1), []))
            iterations += 1
        self.assertLessEqual(iterations, max_iter)

    def test_budget_exceeded_helper(self):
        self.assertTrue(budget_exceeded(100, 100))
        self.assertTrue(budget_exceeded(101, 100))
        self.assertFalse(budget_exceeded(99, 100))


if __name__ == "__main__":
    unittest.main(verbosity=2)
