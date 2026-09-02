#!/usr/bin/env python3
"""Instanced repetition-system geometry must use the LOW tessellation tier.

An instanced micro-part (curb stone, eave tile, fastener) never deforms and is
never subdivided, so hero-tier segment counts are pure waste multiplied by the
instance count: measured on the whimsical-hearth-house, a 26-stone curb ring of
hero-tier boxes cost 44,928 triangles — half the diorama's 90k target budget
spent on cubes.

Pure Python 3.10+ stdlib. No pip installs.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def import_generator():
    module_names = ("generate_threejs_factory", "validate_sculpt_spec")
    original_modules = {name: sys.modules.pop(name, None) for name in module_names}
    original_path = sys.path[:]
    sys.path[:0] = [str(ROOT / "stage2_spec"), str(ROOT / "stage3_build")]
    try:
        import generate_threejs_factory
    finally:
        sys.path[:] = original_path
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return generate_threejs_factory


GEN = import_generator()

CLUSTER_SPEC = {
    "targetName": "Cluster Tessellation Fixture",
    "schemaVersion": "2.1",
    "suitability": "pass",
    "coordinateFrame": {},
    "silhouette": {},
    "proceduralStrategy": [],
    "materials": [{"id": "clay"}],
    "componentTree": [
        {
            "id": "base",
            "name": "Base",
            "level": "macro",
            "role": "body",
            "primitive": "box",
            "parent": None,
            "material": "clay",
            "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [4, 1, 4]},
        },
    ],
    "repetitionSystems": [
        {
            "id": "stud-ring",
            "level": "meso",
            "parent": "base",
            "count": 12,
            "primitive": "box",
            "material": "clay",
            "instanceScale": [0.2, 0.2, 0.2],
            "placement": {"mode": "radial", "axis": [0, 1, 0], "radius": 4.0},
        }
    ],
}


class InstancedClusterTessellationTest(unittest.TestCase):
    def test_cluster_geometry_uses_low_tier(self) -> None:
        generated = GEN.generate(CLUSTER_SPEC, "form-refinement")
        cluster_block = generated[generated.index("repetition system: stud-ring"):]
        geo_line = next(
            line for line in cluster_block.splitlines() if "const geo =" in line
        )
        low_n = GEN.TESSELLATION_TIERS["low"]["BOX_SEGMENTS"]
        self.assertIn(
            f"new THREE.BoxGeometry(1, 1, 1, {low_n}, {low_n}, {low_n})",
            geo_line,
            f"instanced cluster must use the low tier, got: {geo_line.strip()}",
        )

    def test_component_geometry_keeps_the_model_tier(self) -> None:
        generated = GEN.generate(CLUSTER_SPEC, "form-refinement")
        hero_n = GEN.TESSELLATION_TIERS[GEN.DEFAULT_TESSELLATION_TIER]["BOX_SEGMENTS"]
        self.assertIn(
            f"new THREE.BoxGeometry(1, 1, 1, {hero_n}, {hero_n}, {hero_n})",
            generated,
            "component geometry must still use the model's own tier",
        )


if __name__ == "__main__":
    unittest.main()
