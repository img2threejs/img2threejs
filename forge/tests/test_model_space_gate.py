import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from forge.stage5_rig.model_space_gate import validate  # noqa: E402


def contract():
    return {
        "schemaVersion": 1,
        "handedness": "right",
        "authoring": {"up": "+Y", "forward": "-X"},
        "target": {"up": "+Y", "forward": "-Z"},
        "conversionOwner": "export",
        "rootTransform": {
            "position": [0, 0, 0],
            "quaternion": [0, 0, 0, 1],
            "scale": [1, 1, 1],
        },
        "forwardMarker": [0, 0, -1],
        "frontFeature": [0, 0.6, -1.23],
        "rearFeature": [0, 0.66, 0.68],
    }


class ModelSpaceGateTest(unittest.TestCase):
    def test_explicit_single_conversion_with_measured_features_passes(self):
        result = validate(contract())
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["summary"]["authoringForward"], "-X")
        self.assertEqual(result["summary"]["targetForward"], "-Z")

    def test_axis_mismatch_without_an_owner_is_a_hard_failure(self):
        value = contract()
        value["conversionOwner"] = "none"
        result = validate(value)
        self.assertFalse(result["passed"])
        self.assertTrue(any("require one conversion owner" in error for error in result["errors"]))

    def test_a_correct_label_cannot_hide_sideways_visible_geometry(self):
        value = contract()
        value["frontFeature"] = [-1.23, 0.6, 0]
        value["rearFeature"] = [0.68, 0.66, 0]
        result = validate(value)
        self.assertFalse(result["passed"])
        self.assertTrue(any("frontFeature" in error for error in result["errors"]))
        self.assertTrue(any("rearFeature" in error for error in result["errors"]))

    def test_marker_and_identity_root_are_independent_hard_gates(self):
        value = copy.deepcopy(contract())
        value["forwardMarker"] = [1, 0, 0]
        value["rootTransform"]["quaternion"] = [0, 0.7071068, 0, 0.7071068]
        result = validate(value)
        self.assertFalse(result["passed"])
        self.assertTrue(any("forwardMarker" in error for error in result["errors"]))
        self.assertTrue(any("root quaternion" in error for error in result["errors"]))

    def test_matching_frames_reject_a_redundant_adapter(self):
        value = contract()
        value["authoring"] = {"up": "+Y", "forward": "-Z"}
        result = validate(value)
        self.assertFalse(result["passed"])
        self.assertTrue(any("must not add" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
