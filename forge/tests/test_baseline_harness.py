#!/usr/bin/env python3
"""Tests for the quality baseline harness.

These assert that SCORING is deterministic and that a result is attributable.
They deliberately do NOT assert that re-running the pipeline reproduces a render:
a real reconstruction is agent-driven and will vary, which is exactly why the
manifest records a spread instead of a single number.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from baseline import (  # noqa: E402
    BaselineError,
    aggregate,
    assert_comparable,
    environment_record,
    hard_gate_key,
    run_sweep,
    score_pair,
    sha256_file,
    variance,
)
from test_divine_eye import block, write_rgb_png  # noqa: E402


class ScoringDeterminismTest(unittest.TestCase):
    def test_same_pair_scores_identically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference.png"
            render = root / "render.png"
            write_rgb_png(reference, 64, 64, block(16, 16, 48, 48))
            write_rgb_png(render, 64, 64, block(17, 16, 48, 48))
            first = json.dumps(score_pair(reference, render), sort_keys=True)
            second = json.dumps(score_pair(reference, render), sort_keys=True)
            self.assertEqual(first, second)

    def test_missing_image_fails_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference.png"
            write_rgb_png(reference, 32, 32, block(8, 8, 24, 24))
            with self.assertRaises(BaselineError):
                score_pair(reference, root / "absent.png")


class EnvironmentRecordTest(unittest.TestCase):
    def test_unresolved_fields_are_named_not_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = environment_record(skill_root=Path(tmp))
            self.assertIsNone(record["skillVersion"])
            self.assertIn("skillVersion", record["unresolved"])
            self.assertIn("threeVersion", record["unresolved"])

    def test_skill_version_read_from_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SKILL.md").write_text(
                "---\nname: img2threejs\nversion: 2.0.0\n---\n# x\n", encoding="utf-8"
            )
            record = environment_record(skill_root=root, three_version="0.169.0",
                                        browser_build="chromium-1148")
            self.assertEqual(record["skillVersion"], "2.0.0")
            self.assertNotIn("skillVersion", record["unresolved"])

    def test_unresolved_baseline_refuses_comparison(self):
        left = {"unresolved": ["threeVersion"]}
        with self.assertRaises(BaselineError):
            assert_comparable(left, left)

    def test_two_unknown_environments_are_not_comparable(self):
        """{} vs {} returned silently before the review: two baselines with no
        provenance compared clean, so toolchain drift read as a quality delta."""
        with self.assertRaises(BaselineError):
            assert_comparable({}, {})
        with self.assertRaises(BaselineError):
            assert_comparable({"unresolved": []}, {"unresolved": []})

    def test_toolchain_change_reports_as_toolchain_not_regression(self):
        left = {"skillVersion": "2.0.0", "threeVersion": "0.169.0",
                "browserBuild": "a", "lockfileSha256": "x", "unresolved": []}
        right = {**left, "threeVersion": "0.185.1"}
        with self.assertRaises(BaselineError) as caught:
            assert_comparable(left, right)
        self.assertIn("toolchain changed", str(caught.exception))


class AggregateAndVarianceTest(unittest.TestCase):
    def test_empty_aggregate_refuses_rather_than_reporting_zero(self):
        with self.assertRaises(BaselineError):
            aggregate([])

    def test_aggregate_groups_real_divine_eye_failure_strings(self):
        """Uses strings PRODUCED BY divine_eye, not a shape invented to fit this code.

        The first version of this test asserted against "silhouette IoU: 0.60 < 0.85".
        divine_eye emits no colon (stage4_review/divine_eye.py:343), so the test passed
        while the grouping was broken and every run produced a unique key.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference.png"
            write_rgb_png(reference, 64, 64, block(4, 4, 60, 60))
            scores = []
            for offset in (10, 18):
                render = root / f"render{offset}.png"
                write_rgb_png(render, 64, 64, block(offset, offset, offset + 6, offset + 6))
                scores.append(score_pair(reference, render))
            produced = [f for score in scores for f in score["hardGateFailures"]]
            self.assertTrue(produced, "fixtures did not trip a hard gate")
            self.assertFalse(any(":" in f.split(";")[0] for f in produced),
                             "divine_eye's gate names gained a colon; update HARD_GATE_PREFIXES")
            summary = aggregate(scores)
            self.assertEqual(summary["runCount"], 2)
            # The point: distinct measured values collapse onto one gate name.
            self.assertNotIn("other", summary["hardGateFailures"])
            for key, count in summary["hardGateFailures"].items():
                self.assertIn(key, ("silhouette IoU", "scale delta", "foreground mask"))
            self.assertTrue(any(c >= 2 for c in summary["hardGateFailures"].values()),
                            f"no gate grouped across runs: {summary['hardGateFailures']}")

    def test_hard_gate_key_groups_measured_values(self):
        self.assertEqual(hard_gate_key("silhouette IoU 0.012 < 0.85"), "silhouette IoU")
        self.assertEqual(hard_gate_key("silhouette IoU 0.071 < 0.85"), "silhouette IoU")
        self.assertEqual(hard_gate_key("scale delta 0.684 > 0.08"), "scale delta")
        self.assertEqual(hard_gate_key("something new"), "other")

    def test_aggregate_counts_passes(self):
        scores = [{"verdict": "pass", "fidelity": 0.9, "hardGateFailures": []},
                  {"verdict": "reject", "fidelity": 0.5, "hardGateFailures": []}]
        summary = aggregate(scores)
        self.assertEqual(summary["passCount"], 1)
        self.assertEqual(summary["fidelityMean"], 0.7)

    def test_variance_unmeasured_when_every_reference_ran_once(self):
        runs = [{"referenceId": "a", "score": {"fidelity": 0.9}},
                {"referenceId": "b", "score": {"fidelity": 0.8}}]
        self.assertFalse(variance(runs)["measured"])

    def test_variance_reports_spread_for_repeated_reference(self):
        runs = [{"referenceId": "a", "score": {"fidelity": 0.90}},
                {"referenceId": "a", "score": {"fidelity": 0.82}},
                {"referenceId": "a", "score": {"fidelity": 0.86}}]
        spread = variance(runs)
        self.assertTrue(spread["measured"])
        self.assertEqual(spread["perReference"]["a"]["runs"], 3)
        self.assertEqual(spread["perReference"]["a"]["spread"], 0.08)


class SweepTest(unittest.TestCase):
    def test_empty_manifest_fails_loud_naming_the_cause(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "m.json"
            manifest.write_text(json.dumps({"schemaVersion": 1, "references": [],
                                            "runs": []}), encoding="utf-8")
            with self.assertRaises(BaselineError) as caught:
                run_sweep(manifest)
            self.assertIn("no references", str(caught.exception))

    def test_shipped_manifest_is_empty_and_says_so(self):
        """The committed fixture must stay empty until real references exist."""
        manifest = ROOT / "tests" / "fixtures" / "baseline-manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(data["references"], [])
        self.assertEqual(data["runs"], [])
        with self.assertRaises(BaselineError):
            run_sweep(manifest)

    def test_changed_reference_is_caught_not_silently_rescored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "ref.png"
            render = root / "render.png"
            write_rgb_png(reference, 32, 32, block(8, 8, 24, 24))
            write_rgb_png(render, 32, 32, block(8, 8, 24, 24))
            manifest = root / "m.json"
            manifest.write_text(json.dumps({
                "schemaVersion": 1,
                "references": [{"id": "r", "path": "ref.png", "sha256": "0" * 64,
                                "subjectClass": "object", "licence": "test"}],
                "runs": [{"referenceId": "r", "runIndex": 0, "renderPath": "render.png"}],
            }), encoding="utf-8")
            with self.assertRaises(BaselineError) as caught:
                run_sweep(manifest)
            self.assertIn("changed on disk", str(caught.exception))

    def test_sweep_scores_declared_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "ref.png"
            render = root / "render.png"
            write_rgb_png(reference, 64, 64, block(16, 16, 48, 48))
            write_rgb_png(render, 64, 64, block(16, 16, 48, 48))
            manifest = root / "m.json"
            manifest.write_text(json.dumps({
                "schemaVersion": 1,
                "environment": {"unresolved": []},
                "references": [{"id": "r", "path": "ref.png",
                                "sha256": sha256_file(reference),
                                "subjectClass": "object", "licence": "test"}],
                "runs": [{"referenceId": "r", "runIndex": 0, "renderPath": "render.png"},
                         {"referenceId": "r", "runIndex": 1, "renderPath": "render.png"}],
            }), encoding="utf-8")
            result = run_sweep(manifest)
            self.assertEqual(result["aggregate"]["runCount"], 2)
            self.assertTrue(result["variance"]["measured"])

    def test_duplicate_reference_id_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref = root / "ref.png"
            write_rgb_png(ref, 32, 32, block(8, 8, 24, 24))
            manifest = root / "m.json"
            entry = {"id": "r", "path": "ref.png", "sha256": sha256_file(ref),
                     "subjectClass": "object", "licence": "test"}
            manifest.write_text(json.dumps({"schemaVersion": 1,
                                            "references": [entry, dict(entry)],
                                            "runs": []}), encoding="utf-8")
            with self.assertRaises(BaselineError) as caught:
                run_sweep(manifest)
            self.assertIn("duplicate reference id", str(caught.exception))

    def test_unlicensed_reference_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref = root / "ref.png"
            write_rgb_png(ref, 32, 32, block(8, 8, 24, 24))
            manifest = root / "m.json"
            manifest.write_text(json.dumps({
                "schemaVersion": 1,
                "references": [{"id": "r", "path": "ref.png",
                                "sha256": sha256_file(ref), "subjectClass": "object"}],
                "runs": []}), encoding="utf-8")
            with self.assertRaises(BaselineError) as caught:
                run_sweep(manifest)
            self.assertIn("licence", str(caught.exception))

    def test_artifact_records_relative_paths_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref, render = root / "ref.png", root / "render.png"
            write_rgb_png(ref, 64, 64, block(16, 16, 48, 48))
            write_rgb_png(render, 64, 64, block(16, 16, 48, 48))
            manifest = root / "m.json"
            manifest.write_text(json.dumps({
                "schemaVersion": 1, "environment": {"unresolved": []},
                "references": [{"id": "r", "path": "ref.png", "sha256": sha256_file(ref),
                                "subjectClass": "object", "licence": "test"}],
                "runs": [{"referenceId": "r", "runIndex": 0, "renderPath": "render.png"}],
            }), encoding="utf-8")
            result = run_sweep(manifest)
            blob = json.dumps(result)
            self.assertNotIn(tmp, blob, "artifact embeds an absolute machine path")
            self.assertEqual(result["runs"][0]["score"]["reference"], "ref.png")

    def test_variance_reports_whether_three_runs_were_reached(self):
        two = [{"referenceId": "a", "score": {"fidelity": 0.9}},
               {"referenceId": "a", "score": {"fidelity": 0.8}}]
        self.assertTrue(variance(two)["measured"])
        self.assertFalse(variance(two)["sufficient"])
        three = two + [{"referenceId": "a", "score": {"fidelity": 0.85}}]
        self.assertTrue(variance(three)["sufficient"])

    def test_run_naming_unknown_reference_fails_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "ref.png"
            write_rgb_png(reference, 32, 32, block(8, 8, 24, 24))
            manifest = root / "m.json"
            manifest.write_text(json.dumps({
                "schemaVersion": 1,
                "references": [{"id": "r", "path": "ref.png",
                                "sha256": sha256_file(reference),
                                "subjectClass": "object", "licence": "test"}],
                "runs": [{"referenceId": "ghost", "runIndex": 0, "renderPath": "ref.png"}],
            }), encoding="utf-8")
            with self.assertRaises(BaselineError) as caught:
                run_sweep(manifest)
            self.assertIn("unknown reference", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
