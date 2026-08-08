#!/usr/bin/env python3
"""Unit tests for the look-dev sweep ranker.

Motivation is a measured failure: three consecutive correction loops tuned PBR knobs in the
wrong direction because the real culprit -- the tone-mapping operator -- had been fixed by
assumption and left outside the search. Enumerating settled it in one run.

score()/rank() are exercised on synthetic summaries so the cases stay readable; the end-to-end
behaviour is covered by the recorded 16-render sweep in the changelog.

Run: python3 forge/tests/test_rank_lookdev_sweep.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage4_review"))

import argparse  # noqa: E402
from rank_lookdev_sweep import parse_candidate, rank, score  # noqa: E402


def summary(rgb, value, saturation):
    return {"path": "x.png", "subjectPixels": 100, "medianRGB": list(rgb),
            "medianValue": value, "medianSaturation": saturation, "warnings": []}


class ScoreTest(unittest.TestCase):
    def test_an_exact_match_scores_zero_error(self):
        ref = summary((155, 78, 77), 158.0, 66.5)
        self.assertEqual(score(ref, ref)["error"], 0.0)

    def test_error_combines_value_and_saturation_magnitudes(self):
        ref = summary((155, 78, 77), 158.0, 66.5)
        cand = summary((174, 129, 123), 175.0, 27.8)
        result = score(ref, cand)
        self.assertAlmostEqual(result["deltaValue"], 17.0, places=2)
        self.assertAlmostEqual(result["deltaSaturation"], -38.7, places=1)
        self.assertAlmostEqual(result["error"], 55.7, places=1)

    def test_a_washed_out_candidate_is_punished_even_when_value_is_close(self):
        """The real failure mode: candidates were not off in hue, they were desaturated. A
        lightness-weighted distance alone under-punishes exactly that."""
        ref = summary((155, 78, 77), 158.0, 66.5)
        close_value_washed = summary((160, 120, 118), 160.0, 26.0)
        off_value_saturated = summary((140, 61, 60), 140.0, 66.0)
        self.assertGreater(
            score(ref, close_value_washed)["error"],
            score(ref, off_value_saturated)["error"],
        )


class RankTest(unittest.TestCase):
    def test_candidates_are_ordered_by_ascending_error(self):
        ref = summary((155, 78, 77), 158.0, 66.5)
        rows = rank(ref, {
            "far": summary((196, 121, 115), 198.0, 38.4),
            "near": summary((154, 72, 71), 156.0, 58.8),
            "mid": summary((167, 82, 81), 170.0, 55.1),
        })
        self.assertEqual([row["label"] for row in rows], ["near", "mid", "far"])
        self.assertLessEqual(rows[0]["error"], rows[1]["error"])
        self.assertLessEqual(rows[1]["error"], rows[2]["error"])

    def test_identical_candidates_tie_rather_than_being_dropped(self):
        """A knob the operator ignores yields identical frames; they must all survive as
        evidence that the knob is inert, not be silently deduplicated."""
        ref = summary((155, 78, 77), 158.0, 66.5)
        same = summary((175, 97, 96), 177.0, 41.7)
        rows = rank(ref, {"a": same, "b": same, "c": same})
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({row["error"] for row in rows}), 1)


class ParseCandidateTest(unittest.TestCase):
    def test_label_and_path_are_split_on_the_first_equals(self):
        label, path = parse_candidate("neutral@0.70=sweep/neutral-0.7.png")
        self.assertEqual(label, "neutral@0.70")
        self.assertEqual(path, Path("sweep/neutral-0.7.png"))

    def test_a_missing_label_is_rejected(self):
        for bad in ("sweep/x.png", "=sweep/x.png", "  =x.png"):
            with self.assertRaises(argparse.ArgumentTypeError):
                parse_candidate(bad)


if __name__ == "__main__":
    unittest.main()
