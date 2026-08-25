from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from forge.stage4_review.validate_render_profile import validate_file, validate_profile


PROFILE = Path(__file__).resolve().parents[2] / "docs" / "specs" / "render-profile.v2.example.json"


class RenderProfileTest(unittest.TestCase):
    def test_example_profile_passes(self) -> None:
        result = validate_file(PROFILE)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["passIds"], [
            "beauty",
            "alpha-silhouette",
            "semantic-id",
            "depth",
            "normal",
            "roughness-material-id",
        ])

    def test_profile_rejects_wrong_color_space(self) -> None:
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        profile["renderer"]["outputColorSpace"] = "LinearSRGBColorSpace"
        result = validate_profile(profile)
        self.assertFalse(result["passed"])
        self.assertTrue(any("outputColorSpace" in error for error in result["errors"]))

    def test_profile_rejects_missing_pass(self) -> None:
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        profile["passes"] = profile["passes"][:-1]
        result = validate_profile(profile)
        self.assertFalse(result["passed"])
        self.assertTrue(any("passes must contain exactly" in error for error in result["errors"]))

    def test_profile_accepts_subject_specific_regions(self) -> None:
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        profile["regions"] = [
            {"id": "skin", "criticality": "critical", "idColor": [255, 128, 0]},
            {"id": "clothing", "criticality": "critical", "idColor": [0, 255, 0]},
            {"id": "hair", "criticality": "critical", "idColor": [0, 0, 255]},
            {"id": "face", "criticality": "critical", "idColor": [255, 0, 0]},
        ]
        profile["extensions"] = {
            "requiredSemanticRegions": ["skin", "clothing", "hair", "face"],
        }
        result = validate_profile(profile)
        self.assertTrue(result["passed"], result)

    def test_profile_rejects_missing_declared_subject_region(self) -> None:
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        profile["extensions"] = {
            "requiredSemanticRegions": ["face", "missing-garment"],
        }
        result = validate_profile(profile)
        self.assertFalse(result["passed"])
        self.assertTrue(any("missing declared required IDs" in error for error in result["errors"]))

    def test_profile_rejects_duplicate_region_id(self) -> None:
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        profile["regions"].append(dict(profile["regions"][0]))
        result = validate_profile(profile)
        self.assertFalse(result["passed"])
        self.assertTrue(any("region IDs must be unique" in error for error in result["errors"]))

    def test_profile_rejects_empty_required_region_contract(self) -> None:
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        profile["extensions"]["requiredSemanticRegions"] = []
        result = validate_profile(profile)
        self.assertFalse(result["passed"])
        self.assertTrue(any("must be a non-empty array" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
