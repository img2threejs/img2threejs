from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "stage4_review"))

from cs2_review import evaluate_knife_review, load_review_scene  # noqa: E402
from append_review import main as append_review_main  # noqa: E402


class Cs2ReviewGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scene = load_review_scene(ROOT / "tests" / "fixtures" / "knife_review_scene.json")
        self.manifest = {
            "schemaVersion": 1,
            "state": "proceed",
            "itemFamily": "knife",
            "subtype": "karambit",
            "componentAdapter": "cs2-knife-v1",
            "route": "reference-projection",
            "exactnessTier": "image-only",
            "confidence": {"overall": 0.86, "hiddenRegions": 0.25},
            "assumptions": {"hiddenRegions": "inferred"},
        }

    def passing_inputs(self) -> dict:
        return {
            "silhouetteIoU": 0.91,
            "aspectRatioDelta": 0.02,
            "scaleDelta": 0.04,
            "finishMaterialResponse": 0.88,
            "identityDetail": 0.9,
            "paintedRegions": [{"id": "blade_finish", "score": 0.9, "confidence": 0.8}],
            "projection": {"coverage": 0.94, "required": True},
            "multiAngle": {"degenerate": False, "angles": [{"id": "orbit-a"}, {"id": "orbit-b"}]},
            "criticalFeatures": [{"id": "karambit_ring", "score": 0.9, "threshold": 0.8}],
            "approximationNotes": ["hidden blade side inferred from single view"],
        }

    def test_passing_knife_review_returns_machine_readable_report(self) -> None:
        report = evaluate_knife_review(self.manifest, self.passing_inputs(), self.scene)

        self.assertEqual(report["verdict"], "pass")
        self.assertEqual(report["action"], "continue")
        self.assertEqual(report["family"], "knife")
        self.assertEqual(report["exactnessTier"], "image-only")
        self.assertEqual(report["reviewScene"]["version"], 1)
        self.assertEqual(report["failedGates"], [])
        self.assertEqual(report["approximationNotes"], ["hidden blade side inferred from single view"])

    def test_wrong_family_is_blocking_even_when_visual_metrics_pass(self) -> None:
        manifest = {**self.manifest, "itemFamily": "rifle"}

        report = evaluate_knife_review(manifest, self.passing_inputs(), self.scene)

        self.assertEqual(report["verdict"], "reject")
        self.assertEqual(report["action"], "request-input")
        self.assertIn("unsupported-family:rifle", report["failedGates"])

    def test_projection_coverage_and_identity_detail_are_blocking(self) -> None:
        inputs = self.passing_inputs()
        inputs["projection"] = {"coverage": 0.42, "required": True}
        inputs["criticalFeatures"] = [{"id": "karambit_ring", "score": 0.4, "threshold": 0.8}]

        report = evaluate_knife_review(self.manifest, inputs, self.scene)

        self.assertEqual(report["verdict"], "reject")
        self.assertEqual(report["action"], "refine-code")
        self.assertIn("projection-coverage", report["failedGates"])
        self.assertIn("critical-feature:karambit_ring", report["failedGates"])

    def test_degenerate_orbit_is_blocking_and_missing_scene_metadata_is_error(self) -> None:
        inputs = self.passing_inputs()
        inputs["multiAngle"] = {"degenerate": True, "angles": [{"id": "orbit-a", "degenerate": True}]}
        report = evaluate_knife_review(self.manifest, inputs, self.scene)
        self.assertEqual(report["verdict"], "reject")
        self.assertIn("degenerate-orbit", report["failedGates"])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps({"version": 1}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_review_scene(path)

    def test_append_review_persists_cs2_report_and_rejects_failed_report(self) -> None:
        report = evaluate_knife_review(self.manifest, self.passing_inputs(), self.scene)
        failed = evaluate_knife_review(
            self.manifest,
            {**self.passing_inputs(), "identityDetail": 0.2},
            self.scene,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = root / "spec.json"
            report_path = root / "report.json"
            failed_path = root / "failed.json"
            spec_path.write_text(json.dumps({"sourceImage": "ref.png"}), encoding="utf-8")
            report_path.write_text(json.dumps(report), encoding="utf-8")
            failed_path.write_text(json.dumps(failed), encoding="utf-8")
            append_review_main([
                str(spec_path), "--pass-id", "optimization-pass", "--fidelity", "0.9",
                "--action", "continue", "--summary", "ok", "--cs2-review-json", str(report_path),
                "--review-scene-json", str(ROOT / "tests" / "fixtures" / "knife_review_scene.json"),
                "--in-place",
            ])
            persisted = json.loads(spec_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["reviewHistory"][0]["cs2Review"]["verdict"], "pass")
            with self.assertRaises(ValueError):
                append_review_main([
                    str(spec_path), "--pass-id", "optimization-pass", "--fidelity", "0.9",
                    "--action", "continue", "--summary", "blocked", "--cs2-review-json", str(failed_path),
                ])


if __name__ == "__main__":
    unittest.main(verbosity=2)
