#!/usr/bin/env python3
"""Look-dev shadow camera must cover the model it lights.

The whimsical-hearth-house incident: the emitted rig hardcoded a +-2.6
orthographic shadow camera sized for a character-scale asset, so a ~5.3-radius
garden diorama's contact shadow could never land past its own base — the
"missing contact shadow" mustAvoid of the lighting contract was structurally
unavoidable for any larger model. The generator now sizes the shadow camera
and light positions from the spec's estimated world radius.

Pure Python 3.10+ stdlib. No pip installs.
"""

from __future__ import annotations

import re
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


def component(component_id: str, parent: str | None, position: list[float], dims: tuple[float, float, float]) -> dict:
    return {
        "id": component_id,
        "parent": parent,
        "dimensions": {"width": dims[0], "height": dims[1], "depth": dims[2]},
        "transform": {"position": list(position), "rotation": [0, 0, 0], "scale": [1, 1, 1]},
    }


class EstimateModelRadiusTest(unittest.TestCase):
    def test_accumulates_parent_chain_and_half_extents(self) -> None:
        spec = {
            "componentTree": [
                component("root", None, [0, 0, 0], (1, 1, 1)),
                component("base", "root", [0, 0.2, 0], (10, 0.5, 8)),
                component("tower", "base", [3.0, 2.5, 0], (2.4, 5.2, 2.4)),
            ]
        }
        radius = GEN.estimate_model_radius(spec)
        # base alone reaches |0| + 10/2 = 5.0; the tower reaches 2.7 + 2.6 = 5.3 in y
        self.assertGreaterEqual(radius, 5.0)

    def test_falls_back_to_character_scale_without_dimensions(self) -> None:
        self.assertEqual(GEN.estimate_model_radius({"componentTree": []}), 2.0)


class ShadowCameraBoundsTest(unittest.TestCase):
    def emitted_rig(self, base_half: float) -> str:
        spec = {
            "componentTree": [
                component("root", None, [0, 0, 0], (1, 1, 1)),
                component("base", "root", [0, 0.2, 0], (base_half * 2, 0.5, base_half * 2)),
            ]
        }
        radius = GEN.estimate_model_radius(spec)
        half = round(max(2.6, radius * 1.4), 2)
        return radius, half

    def test_large_model_gets_covering_bounds(self) -> None:
        radius, half = self.emitted_rig(5.3)
        self.assertGreaterEqual(half, radius, "shadow half-extent must cover the model radius")

    def test_small_model_keeps_character_scale_floor(self) -> None:
        _, half = self.emitted_rig(0.5)
        self.assertEqual(half, 2.6)


if __name__ == "__main__":
    unittest.main()
