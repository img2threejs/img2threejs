from __future__ import annotations

import unittest

from forge.stage2_spec.adaptive_adapter import validate_adapter_contract, validate_spec


def valid_adapter() -> dict:
    return {
        "id": "generated:ceramic-mug:v1",
        "mode": "llm-synthesized",
        "domain": "object",
        "subjectClass": "ceramic vessel",
        "evidenceRefs": ["view-front"],
        "researchRefs": [],
        "components": [
            {
                "id": "body",
                "name": "Mug body",
                "topologyClass": "assembled-solid",
                "geometryRecipe": "surface-of-revolution",
                "evidenceRefs": ["view-front"],
                "confidence": 0.92,
            },
            {
                "id": "handle",
                "name": "Handle",
                "topologyClass": "continuous-sculpt",
                "geometryRecipe": "curve-sweep",
                "evidenceRefs": ["view-front"],
                "confidence": 0.81,
            },
        ],
        "attachmentRules": [
            {
                "parent": "body",
                "child": "handle",
                "parentSocket": "handle-root",
                "contactType": "overlap",
                "gapTolerance": 0.002,
                "evidenceRefs": ["view-front"],
            }
        ],
        "criticalFeatures": [
            {
                "id": "rim-and-handle",
                "componentRefs": ["body", "handle"],
                "evidenceRefs": ["view-front"],
                "acceptance": "Rim ellipse and handle attachment hold in front and orbit views.",
            }
        ],
        "reviewViewpoints": ["reference", "orbit-right"],
        "confidence": {"body": 0.92, "handle-hidden-side": 0.58},
        "geometryPolicy": {
            "realMeshRequired": True,
            "cameraOnlyGeometry": False,
            "projectionBinding": "none",
            "forbidden": ["depth-map-extrusion", "camera-only-shell", "image-plane-substitute"],
        },
    }


class AdaptiveAdapterTests(unittest.TestCase):
    def test_new_domain_adapter_is_valid_without_registry_entry(self) -> None:
        self.assertEqual(validate_adapter_contract(valid_adapter()), [])

    def test_missing_adapter_is_actionable(self) -> None:
        self.assertEqual(validate_spec({}), ["spec must contain subjectAdapter or adapterContract before code generation"])

    def test_camera_shell_and_depth_extrusion_are_rejected(self) -> None:
        adapter = valid_adapter()
        adapter["geometryPolicy"]["cameraOnlyGeometry"] = True
        adapter["geometryPolicy"]["forbidden"] = ["image-plane-substitute"]
        errors = validate_adapter_contract(adapter)
        self.assertTrue(any("cameraOnlyGeometry" in error for error in errors))
        self.assertTrue(any("must include depth-map-extrusion" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
