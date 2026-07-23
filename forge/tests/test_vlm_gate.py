#!/usr/bin/env python3
"""Tests for the VLM gating layer (Plan 1.3 §3.4). Uses stub samplers — no real model, no token."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "stage4_review"))

from vlm_gate import aggregate_samples, calibrate, evidence_consistent, gate  # noqa: E402


def const_sampler(scores: dict):
    return lambda i: dict(scores)


def high_all(claimed="knife"):
    return {"objectness": 0.9, "semantic": 0.88, "structural": 0.86, "specular": 0.85, "claimedClass": claimed}


class VlmGateTest(unittest.TestCase):
    def test_hard_gate_failure_does_not_consult_vlm(self):
        eye = {"verdict": "reject", "action": "refine-code", "hardGateFailures": ["silhouette IoU 0.40 < 0.85"]}
        called = {"n": 0}
        def sampler(i):
            called["n"] += 1
            return high_all()
        r = gate(eye, sampler)
        self.assertFalse(r["ranVlm"])
        self.assertEqual(r["verdict"], "reject")
        self.assertEqual(called["n"], 0, "VLM must not be called when a hard gate failed")

    def test_pass_confirmed_when_all_criteria_high(self):
        eye = {"verdict": "pass", "action": "continue", "hardGateFailures": []}
        r = gate(eye, const_sampler(high_all()), geometry_class="knife")
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(r["action"], "continue")
        self.assertTrue(r["ranVlm"])

    def test_soft_reject_rescued_by_vlm(self):
        # deterministic ensemble was low-confidence (no HARD failure), VLM confirms → rescue
        eye = {"verdict": "low-confidence", "action": "probe", "hardGateFailures": []}
        r = gate(eye, const_sampler(high_all()), geometry_class="knife")
        self.assertEqual(r["verdict"], "pass")
        self.assertIn("rescued", r["reason"])

    def test_low_objectness_withholds_refine_spec(self):
        eye = {"verdict": "pass", "action": "continue", "hardGateFailures": []}
        s = high_all(); s["objectness"] = 0.5
        r = gate(eye, const_sampler(s), geometry_class="knife")
        self.assertEqual(r["verdict"], "withhold")
        self.assertEqual(r["action"], "refine-spec")

    def test_low_structural_withholds_refine_code(self):
        eye = {"verdict": "pass", "action": "continue", "hardGateFailures": []}
        s = high_all(); s["structural"] = 0.4
        r = gate(eye, const_sampler(s), geometry_class="knife")
        self.assertEqual(r["verdict"], "withhold")
        self.assertEqual(r["action"], "refine-code")

    def test_high_sample_spread_is_uncertain_probe(self):
        eye = {"verdict": "pass", "action": "continue", "hardGateFailures": []}
        samples = [high_all(), {"objectness": 0.4, "semantic": 0.4, "structural": 0.4, "specular": 0.4, "claimedClass": "knife"}]
        r = gate(eye, lambda i: samples[i % len(samples)], n_samples=2, geometry_class="knife")
        self.assertEqual(r["verdict"], "uncertain")
        self.assertEqual(r["action"], "probe")

    def test_evidence_contradiction_is_uncertain(self):
        eye = {"verdict": "pass", "action": "continue", "hardGateFailures": []}
        r = gate(eye, const_sampler(high_all(claimed="spoon")), geometry_class="knife")
        self.assertEqual(r["verdict"], "uncertain")
        self.assertEqual(r["action"], "probe")

    def test_no_sampler_keeps_deterministic_verdict(self):
        eye = {"verdict": "pass", "action": "continue", "hardGateFailures": []}
        r = gate(eye, None)
        self.assertFalse(r["ranVlm"])
        self.assertEqual(r["verdict"], "pass")

    def test_calibrate_identity_and_monotonic(self):
        self.assertEqual(calibrate(0.7), 0.7)  # identity by default
        # a map that pulls raw 0.9 down to 0.7 (anti-overconfidence)
        cal = [[0.0, 0.0], [0.9, 0.7], [1.0, 0.8]]
        self.assertAlmostEqual(calibrate(0.9, cal), 0.7, places=5)
        self.assertLess(calibrate(0.9, cal), 0.9)

    def test_evidence_consistent_unknown_geometry(self):
        self.assertTrue(evidence_consistent("knife", None))
        self.assertTrue(evidence_consistent("Knife", "knife"))
        self.assertFalse(evidence_consistent("spoon", "knife"))

    def test_aggregate_median(self):
        agg = aggregate_samples([{"objectness": 0.2}, {"objectness": 0.8}, {"objectness": 0.6}])
        self.assertAlmostEqual(agg["criteria"]["objectness"], 0.6, places=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
