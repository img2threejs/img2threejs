"""A scaffold's identity `transform.scale` must not silently delete authored `dimensions`.

WHAT THIS PINS. `dimensions` and `transform.scale` can both express a part's size, and
`scale_vector()` used to prefer `transform.scale` merely because the key existed. Since
`new_sculpt_spec.py` writes `"scale": [1, 1, 1]` into every component it scaffolds, every spec
authored on top of that scaffold emitted `geometry.scale(1, 1, 1)` -- unit-sized parts -- while
`validate_sculpt_spec.py --strict-quality` still returned PASS, because no gate compares the two
fields. A wrong model with a green gate is the failure mode this repo's own
`GeometryNotImplementedError` docstring calls out: never silently substitute geometry.

The fix is narrow on purpose: an identity scale multiplies by nothing, so it cannot be the
authored answer when `dimensions` says otherwise. A real non-uniform scale still wins.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "forge" / "_shared"))
sys.path.insert(0, str(ROOT / "forge" / "stage2_spec"))
sys.path.insert(0, str(ROOT / "forge" / "stage3_build"))

import generate_threejs_factory as generator  # noqa: E402

NEW_SCULPT_SPEC = ROOT / "forge/stage2_spec/new_sculpt_spec.py"


def spec_with(transform: dict, dimensions: dict) -> dict:
    return {
        "targetName": "Dimension Probe",
        "targetId": "dimension-probe",
        "schemaVersion": "2.1",
        "suitability": "pass",
        "coordinateFrame": {"front": "+Z", "up": "+Y", "scaleReference": "unit"},
        "silhouette": {"boundingShape": "test", "symmetry": "bilateral"},
        "proceduralStrategy": ["blockout"],
        "materials": [{"id": "base", "name": "Base", "baseColor": "#808080"}],
        "buildPasses": [{"id": "blockout", "acceptance": []}],
        "componentTree": [
            {
                "id": "part",
                "name": "Part",
                "level": "macro",
                "role": "body",
                "primitive": "box",
                "topologyClass": "assembled-solid",
                "topologyRationale": "test",
                "parent": None,
                "material": "base",
                "transform": copy.deepcopy(transform),
                "dimensions": copy.deepcopy(dimensions),
            }
        ],
    }


SCAFFOLD_TRANSFORM = {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}
REAL_DIMENSIONS = {"width": 0.52, "height": 0.22, "depth": 0.2, "units": "relative"}


class ComponentDimensionsTest(unittest.TestCase):
    def emit(self, transform: dict, dimensions: dict) -> str:
        return generator.generate(spec_with(transform, dimensions), "blockout")

    def test_authored_dimensions_survive_a_scaffold_identity_scale(self):
        """The regression itself: scaffold transform plus real dimensions."""
        source = self.emit(SCAFFOLD_TRANSFORM, REAL_DIMENSIONS)
        self.assertIn("Geometry.scale(0.52, 0.22, 0.2);", source)
        self.assertNotIn("Geometry.scale(1.0, 1.0, 1.0);", source)

    def test_an_authored_non_uniform_scale_still_wins(self):
        """Backward compatibility. `test_hierarchy_scale.py` builds a rig exactly this way."""
        transform = {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [3.0, 1.0, 0.2]}
        source = self.emit(transform, REAL_DIMENSIONS)
        self.assertIn("Geometry.scale(3.0, 1.0, 0.2);", source)

    def test_identity_scale_with_no_dimensions_is_still_identity(self):
        source = self.emit(SCAFFOLD_TRANSFORM, {})
        self.assertIn("Geometry.scale(1.0, 1.0, 1.0);", source)

    def test_a_missing_scale_key_still_reads_dimensions(self):
        source = self.emit({"position": [0, 0, 0], "rotation": [0, 0, 0]}, REAL_DIMENSIONS)
        self.assertIn("Geometry.scale(0.52, 0.22, 0.2);", source)

    def test_only_an_exact_unit_triple_counts_as_identity(self):
        """Float ones are identity too; anything that actually scales is left alone."""
        identity = self.emit(
            {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1.0, 1.0, 1.0]},
            REAL_DIMENSIONS,
        )
        self.assertIn("Geometry.scale(0.52, 0.22, 0.2);", identity)

        near_identity = self.emit(
            {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 2]},
            REAL_DIMENSIONS,
        )
        self.assertIn("Geometry.scale(1.0, 1.0, 2.0);", near_identity)

    def test_the_scaffold_really_does_emit_an_identity_scale(self):
        """Guards the premise: if the scaffold stops writing `scale`, this trap is gone with it.

        Kept as a live check rather than a comment, because the fix above is only load-bearing
        for as long as the scaffold keeps seeding the field.
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            out = Path(temporary_directory) / "spec.json"
            result = subprocess.run(
                [sys.executable, str(NEW_SCULPT_SPEC), "Scale Probe", "--out", str(out)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            spec = json.loads(out.read_text(encoding="utf-8"))

        components = spec["componentTree"]
        self.assertTrue(components)
        for component in components:
            self.assertEqual(component["transform"].get("scale"), [1, 1, 1], component["id"])


if __name__ == "__main__":
    unittest.main()
