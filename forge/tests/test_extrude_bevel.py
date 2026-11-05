"""`profile2D.bevel` gives an extrude rounded edges inside its authored envelope.

`buildExtrudeGeometry` hard-coded `bevelEnabled: false`, so every extruded part of a hard-surface
subject had razor edges. The optional bevel keeps the profile points as the outer silhouette and the
caps at 0..depth; without it the emitted geometry is unchanged.

Run: python3 -m unittest forge.tests.test_extrude_bevel
"""
import copy
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "_shared"))
sys.path.insert(0, str(HERE.parent / "stage2_spec"))
sys.path.insert(0, str(HERE.parent / "stage3_build"))

import generate_threejs_factory  # noqa: E402

FIXTURE = HERE / "fixtures" / "reference_target_conformance_spec.json"


def spec_with(component: dict) -> dict:
    spec = json.loads(FIXTURE.read_text(encoding="utf-8"))
    root = spec["componentTree"][0]
    part = copy.deepcopy(root)
    part.update(component)
    part["parent"] = root["id"]
    part["level"] = root.get("level", "macro")
    spec["componentTree"].append(part)
    return spec


def first_pass(spec: dict) -> str:
    passes = spec.get("buildPasses") or []
    return passes[0]["id"] if passes and isinstance(passes[0], dict) and passes[0].get("id") else "blockout"


class ExtrudeBevel(unittest.TestCase):
    def test_builder_supports_an_optional_inward_bevel(self):
        spec = spec_with({"id": "plate", "name": "Plate", "primitive": "extrude",
                          "geometryDescriptor": {"profile2D": {"points": [[0, 0], [1, 0], [1, 1], [0, 1]],
                                                               "depth": 0.2, "bevel": {"size": 0.02}}},
                          "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}})
        source = generate_threejs_factory.generate(spec, first_pass(spec))
        self.assertIn("bevelOffset: -bevel.size", source)
        self.assertIn('"bevel": {"size": 0.02}', source.replace(", ", ", "))

    def test_without_bevel_the_extrude_is_unchanged(self):
        geometry = generate_threejs_factory.geometry_for(
            "extrude", {"geometryDescriptor": {"profile2D": {"points": [[0, 0], [1, 0], [0, 1]], "depth": 0.1}}})
        self.assertNotIn("bevel", geometry)

if __name__ == "__main__":
    unittest.main()
