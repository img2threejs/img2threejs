"""Unit tests for forge/stage4_review/per_feature.py.

Pure stdlib, no images. Exercises the per-feature gating decision logic.
"""

import os
import sys
import unittest

# Make the stage4_review package importable regardless of cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
_FORGE = os.path.dirname(_HERE)
if _FORGE not in sys.path:
    sys.path.insert(0, _FORGE)

from stage4_review import per_feature  # noqa: E402


class PerFeatureTests(unittest.TestCase):
    def test_all_features_pass(self):
        targets = [
            {"id": "recurved-hook", "tier": "critical", "minimumScore": 0.8},
            {"id": "jade-blade", "tier": "critical", "minimumScore": 0.75},
        ]
        scores = {"recurved-hook": 0.9, "jade-blade": 0.85}

        result = per_feature.evaluate_features(targets, scores)

        self.assertTrue(result["passed"])
        self.assertEqual(result["action"], "continue")
        self.assertEqual(result["defects"], [])

    def test_critical_below_fails_even_if_others_high(self):
        targets = [
            {"id": "recurved-hook", "tier": "critical", "minimumScore": 0.8},
            {"id": "jade-blade", "tier": "critical", "minimumScore": 0.75},
            {"id": "pommel", "tier": "detail", "minimumScore": 0.5},
        ]
        scores = {"recurved-hook": 0.5, "jade-blade": 1.0, "pommel": 1.0}

        result = per_feature.evaluate_features(targets, scores)

        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "refine-code")
        self.assertTrue(
            any(d.startswith("below-threshold:recurved-hook") for d in result["defects"]),
            result["defects"],
        )

    def test_missing_critical_feature_routes_refine_spec(self):
        targets = [
            {"id": "recurved-hook", "tier": "critical", "minimumScore": 0.8},
            {"id": "jade-blade", "tier": "critical", "minimumScore": 0.75},
        ]
        scores = {"recurved-hook": None, "jade-blade": 0.9}

        result = per_feature.evaluate_features(targets, scores)

        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "refine-spec")
        self.assertIn("missing-feature:recurved-hook", result["defects"])

    def test_non_gating_below_threshold_does_not_fail(self):
        targets = [
            {"id": "recurved-hook", "tier": "critical", "minimumScore": 0.8},
            {"id": "engraving", "tier": "important"},  # non-gating, default 0.65
        ]
        scores = {"recurved-hook": 0.95, "engraving": 0.4}

        result = per_feature.evaluate_features(targets, scores)

        self.assertTrue(result["passed"])
        self.assertEqual(result["action"], "continue")

        engraving = next(f for f in result["features"] if f["id"] == "engraving")
        self.assertEqual(engraving["status"], "below")
        self.assertFalse(engraving["gating"])

    def test_threshold_defaults_per_tier(self):
        self.assertEqual(per_feature.threshold_for({"tier": "critical"}), 0.8)
        self.assertEqual(per_feature.threshold_for({"tier": "important"}), 0.65)
        self.assertEqual(per_feature.threshold_for({"tier": "detail"}), 0.5)

    def test_missing_from_scores_dict_treated_as_missing(self):
        targets = [
            {"id": "recurved-hook", "tier": "critical", "minimumScore": 0.8},
        ]
        scores = {}  # id not present at all

        result = per_feature.evaluate_features(targets, scores)

        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "refine-spec")
        self.assertIn("missing-feature:recurved-hook", result["defects"])

        feature = result["features"][0]
        self.assertEqual(feature["status"], "missing")
        self.assertIsNone(feature["score"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
