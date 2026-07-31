from __future__ import annotations

import json
import unittest
from importlib.util import find_spec
from pathlib import Path

from forge.stage1b_multi_view.validate_geometry_brief import validate_geometry_brief


class GeometryBriefSchemaTest(unittest.TestCase):
    def test_complete_calibrated_brief_is_valid(self) -> None:
        errors = validate_geometry_brief(
            {
                "status": "complete",
                "viewCount": 2,
                "synthesisMode": "hybrid",
                "confidence": 0.85,
                "namedViews": {"front": "front.png", "rear": "rear.png"},
                "components": {
                    "root": {
                        "visibleIn": ["front", "rear"],
                        "dimensions": {"width": 2.0, "height": 1.0, "depth": 0.5},
                        "curvature": {"profile": "ellipsoid", "confidence": 0.8},
                        "confidence": 0.85,
                    }
                },
            }
        )

        self.assertEqual(errors, [])

    def test_component_without_view_coverage_is_rejected(self) -> None:
        errors = validate_geometry_brief(
            {
                "status": "complete",
                "viewCount": 2,
                "synthesisMode": "hybrid",
                "confidence": 0.85,
                "components": {"root": {"confidence": 0.85}},
            }
        )

        self.assertIn("components.root.visibleIn must be a non-empty array of strings", errors)

    @unittest.skipUnless(find_spec("jsonschema") is not None, "requires jsonschema")
    def test_json_schema_validates_complete_geometry_brief(self) -> None:
        from jsonschema import validate

        schema_path = Path("grimoire/multi_view/geometry_brief_schema.json")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        brief = {
            "status": "complete",
            "viewCount": 2,
            "synthesisMode": "hybrid",
            "confidence": 0.85,
            "components": {
                "root": {
                    "visibleIn": ["front", "rear"],
                    "dimensions": {"width": 2.0, "height": 1.0, "depth": 0.5},
                    "confidence": 0.85,
                }
            },
        }

        validate(brief, schema)


if __name__ == "__main__":
    unittest.main()
