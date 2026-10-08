"""Contracts for the reconstruct-reference-motion companion skill."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT / "skills" / "reconstruct-reference-motion"
VALIDATOR_PATH = SKILL_ROOT / "scripts" / "validate_motion_manifest.py"
EXAMPLE_PATH = SKILL_ROOT / "references" / "example-motion-manifest.json"
SCHEMA_PATH = SKILL_ROOT / "references" / "reference-motion-manifest.schema.json"


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_motion_manifest", VALIDATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load motion-manifest validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALIDATOR = load_validator()


class ReferenceMotionSkillTest(unittest.TestCase):
    def setUp(self) -> None:
        self.example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))

    def error_codes(self, manifest: dict) -> set[str]:
        return {error["code"] for error in VALIDATOR.validate_manifest(manifest)}

    def test_example_manifest_is_valid(self) -> None:
        self.assertEqual(VALIDATOR.validate_manifest(self.example), [])

        result = subprocess.run(
            [sys.executable, str(VALIDATOR_PATH), str(EXAMPLE_PATH), "--json"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["preImplementationDecision"], "ready-for-implementation")
        self.assertEqual(payload["rootReviewDecision"], "refine-spec")

    def test_every_frame_request_rejects_sampled_analysis(self) -> None:
        manifest = copy.deepcopy(self.example)
        manifest["source"]["analysisCoverage"] = "sampled"
        manifest["source"]["samplingRule"] = "every tenth frame"
        self.assertIn("COVERAGE", self.error_codes(manifest))

    def test_frame_source_points_use_original_frame_bounds(self) -> None:
        manifest = copy.deepcopy(self.example)
        manifest["frames"][0]["observed"]["features"][0]["sourcePoints"] = [[641, 12]]
        self.assertIn("BOUNDS", self.error_codes(manifest))

    def test_non_equivalent_features_must_be_proxy_metrics(self) -> None:
        manifest = copy.deepcopy(self.example)
        manifest["comparison"]["classification"] = "like-for-like"
        self.assertIn("FEATURE_IDENTITY", self.error_codes(manifest))

    def test_first_implementation_can_start_before_a_render_exists(self) -> None:
        manifest = copy.deepcopy(self.example)
        manifest["comparison"] = {"status": "not-run"}
        manifest["rootReviewDecision"] = {"status": "not-run"}
        self.assertEqual(VALIDATOR.validate_manifest(manifest), [])

    def test_not_run_phase_rejects_stale_comparison_and_review_fields(self) -> None:
        manifest = copy.deepcopy(self.example)
        manifest["comparison"] = {"status": "not-run", "metrics": []}
        manifest["rootReviewDecision"] = {"status": "not-run", "action": "continue"}
        codes = self.error_codes(manifest)
        self.assertIn("STALE_COMPARISON", codes)
        self.assertIn("STALE_REVIEW", codes)

    def test_comparison_and_root_review_phase_must_match(self) -> None:
        manifest = copy.deepcopy(self.example)
        manifest["rootReviewDecision"] = {"status": "not-run"}
        self.assertIn("PHASE_MISMATCH", self.error_codes(manifest))

    def test_stable_bootstrap_frame_must_lie_in_stable_interval(self) -> None:
        manifest = copy.deepcopy(self.example)
        manifest["source"]["bootstrapReferenceFrame"]["selectionRule"] = "stable-hold-midpoint"
        manifest["source"]["bootstrapReferenceFrame"]["timestampSeconds"] = 1.0
        self.assertIn("BOOTSTRAP_SELECTION", self.error_codes(manifest))

    def test_interval_bootstrap_frame_uses_nearest_midpoint_with_earlier_tie(self) -> None:
        manifest = copy.deepcopy(self.example)
        manifest["frames"] = [manifest["frames"][0], manifest["frames"][2]]
        manifest["source"]["frameCount"] = 2
        manifest["source"]["bootstrapReferenceFrame"]["timestampSeconds"] = 1.0666666667
        self.assertIn("BOOTSTRAP_SELECTION", self.error_codes(manifest))

        manifest["source"]["bootstrapReferenceFrame"]["timestampSeconds"] = 1.0
        self.assertNotIn("BOOTSTRAP_SELECTION", self.error_codes(manifest))

    def test_completed_root_review_requires_prior_implementation_readiness(self) -> None:
        manifest = copy.deepcopy(self.example)
        manifest["preImplementationDecision"]["action"] = "stop"
        self.assertIn("PHASE_MISMATCH", self.error_codes(manifest))

    def test_completed_comparison_must_be_synchronized_and_camera_matched(self) -> None:
        manifest = copy.deepcopy(self.example)
        manifest["comparison"]["synchronizedTimes"] = False
        manifest["comparison"]["cameraMatched"] = False
        self.assertIn("COMPARISON_ALIGNMENT", self.error_codes(manifest))

    def test_single_view_high_confidence_requires_calibration(self) -> None:
        manifest = copy.deepcopy(self.example)
        inferred = manifest["frames"][0]["inferred"]["properties"][0]
        inferred["confidence"] = 0.9
        self.assertIn("SINGLE_VIEW_CONFIDENCE", self.error_codes(manifest))

        inferred["calibrationEvidence"] = ["known chrome-ball calibration target"]
        self.assertNotIn("SINGLE_VIEW_CONFIDENCE", self.error_codes(manifest))

    def test_ready_decision_requires_stable_interval_and_visual_evidence(self) -> None:
        manifest = copy.deepcopy(self.example)
        manifest["intervals"][0]["classification"] = "transition"
        del manifest["evidence"]["annotatedKeyframes"]
        codes = self.error_codes(manifest)
        self.assertIn("READINESS", codes)

    def test_timestamps_must_be_strictly_increasing(self) -> None:
        manifest = copy.deepcopy(self.example)
        manifest["frames"][1]["timestampSeconds"] = manifest["frames"][0]["timestampSeconds"]
        self.assertIn("ORDER", self.error_codes(manifest))

    def test_skill_and_schema_are_reachable_and_machine_readable(self) -> None:
        skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        companion_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        scripts_text = (ROOT / "grimoire" / "scripts.md").read_text(encoding="utf-8")
        relative_skill = "skills/reconstruct-reference-motion/SKILL.md"
        relative_validator = "skills/reconstruct-reference-motion/scripts/validate_motion_manifest.py"
        self.assertIn(relative_skill, skill_text)
        self.assertIn(relative_validator, scripts_text)
        self.assertTrue(companion_text.startswith("---\nname: reconstruct-reference-motion\ndescription: "))
        self.assertEqual(companion_text.count("\n---\n"), 1)
        frontmatter = companion_text.split("\n---\n", 1)[0]
        self.assertNotIn("TODO", companion_text)
        self.assertNotIn("metadata:", frontmatter)
        self.assertEqual(json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["title"], "Reference motion manifest")


if __name__ == "__main__":
    unittest.main()
