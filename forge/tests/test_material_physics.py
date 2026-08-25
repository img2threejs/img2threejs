"""Every assertion here pins a measured property of three@0.169.0, not a policy preference.

If three changes one of these, the test should fail loudly rather than the pipeline quietly emitting
values the engine no longer honours.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))

from material_physics import (  # noqa: E402
    CLEARCOAT_ROUGHNESS_FLOOR,
    SHEEN_ENERGY_COMPENSATION_COEFFICIENT,
    check_material_physics,
    check_open_boundary_sides,
    compensated_base_luminance,
    effective_sheen_strength,
    sheen_base_darkening,
)


class SheenFoldingTests(unittest.TestCase):
    def test_sheen_and_sheen_color_are_one_degree_of_freedom(self):
        """WebGLMaterials.js:408 multiplies them, so these two must be indistinguishable."""
        self.assertAlmostEqual(
            effective_sheen_strength(1.0, "#808080"),
            effective_sheen_strength(0.50196, "#ffffff"),
            places=4,
        )

    def test_default_sheen_color_makes_sheen_a_no_op(self):
        """three's default sheenColor is black and the term is a multiply, so strength is zero."""
        self.assertEqual(effective_sheen_strength(0.65, None), 0.0)
        self.assertEqual(effective_sheen_strength(0.65, "#000000"), 0.0)

    def test_sheen_off_is_zero_strength_whatever_the_colour(self):
        self.assertEqual(effective_sheen_strength(0.0, "#ffffff"), 0.0)

    def test_short_hex_is_expanded(self):
        self.assertAlmostEqual(effective_sheen_strength(1.0, "#fff"), 1.0, places=6)

    def test_unparseable_colour_carries_no_sheen(self):
        self.assertEqual(effective_sheen_strength(1.0, "not-a-colour"), 0.0)


class SheenEnergyTests(unittest.TestCase):
    def test_full_white_sheen_darkens_base_by_the_engine_coefficient(self):
        """ShaderLib/meshphysical.glsl.js:205 -- 15.7% at full strength, exactly."""
        self.assertAlmostEqual(
            sheen_base_darkening(1.0, "#ffffff"),
            SHEEN_ENERGY_COMPENSATION_COEFFICIENT,
            places=6,
        )

    def test_darkening_scales_with_effective_strength(self):
        self.assertAlmostEqual(sheen_base_darkening(0.5, "#ffffff"), 0.0785, places=4)

    def test_compensation_inverts_the_darkening(self):
        """Authoring the compensated base must land back on the target after the engine darkens it."""
        target = 0.5
        compensated = compensated_base_luminance(target, 1.0, "#ffffff")
        self.assertAlmostEqual(compensated * (1.0 - sheen_base_darkening(1.0, "#ffffff")), target, places=6)

    def test_compensation_is_a_no_op_when_sheen_is_off(self):
        self.assertEqual(compensated_base_luminance(0.42, 0.0, "#ffffff"), 0.42)

    def test_compensation_never_exceeds_one(self):
        self.assertLessEqual(compensated_base_luminance(0.99, 1.0, "#ffffff"), 1.0)


class MaterialPhysicsGateTests(unittest.TestCase):
    def test_sheen_without_sheen_color_is_an_error_not_a_warning(self):
        errors, _ = check_material_physics("shirt", {"sheen": 0.65})
        self.assertTrue(any("no sheenColor" in e for e in errors), errors)

    def test_sheen_with_black_sheen_color_is_an_error(self):
        errors, _ = check_material_physics("shirt", {"sheen": 0.65, "sheenColor": "#000000"})
        self.assertTrue(any("evaluates to zero" in e for e in errors), errors)

    def test_valid_sheen_warns_about_its_own_base_darkening(self):
        errors, warnings = check_material_physics("shirt", {"sheen": 1.0, "sheenColor": "#ffffff"})
        self.assertEqual(errors, [])
        self.assertTrue(any("15.7%" in w for w in warnings), warnings)

    def test_clearcoat_roughness_below_the_floor_is_flagged(self):
        _, warnings = check_material_physics("skin", {"clearcoatRoughness": 0.01})
        self.assertTrue(any(str(CLEARCOAT_ROUGHNESS_FLOOR) in w for w in warnings), warnings)

    def test_clearcoat_roughness_at_or_above_the_floor_is_silent(self):
        _, warnings = check_material_physics("skin", {"clearcoatRoughness": CLEARCOAT_ROUGHNESS_FLOOR})
        self.assertEqual([w for w in warnings if "clearcoatRoughness" in w], [])

    def test_zero_clearcoat_roughness_is_not_flagged(self):
        """0.0 is the three default and means 'unset', not 'authored below the floor'."""
        _, warnings = check_material_physics("skin", {"clearcoatRoughness": 0.0})
        self.assertEqual([w for w in warnings if "clearcoatRoughness" in w], [])

    def test_ior_and_reflectivity_together_is_an_error(self):
        errors, _ = check_material_physics("skin", {"ior": 1.4, "reflectivity": 0.5})
        self.assertTrue(any("reflectivity" in e for e in errors), errors)

    def test_skin_may_not_set_transmission(self):
        errors, _ = check_material_physics("skin", {"transmission": 0.3}, family="skin")
        self.assertTrue(any("transmission" in e and "subsurface" in e for e in errors), errors)

    def test_skin_may_not_set_thickness_either(self):
        errors, _ = check_material_physics("skin", {"thickness": 0.5}, family="skin")
        self.assertTrue(any("thickness" in e for e in errors), errors)

    def test_non_skin_may_set_transmission(self):
        errors, _ = check_material_physics("glass", {"transmission": 0.9}, family="glass")
        self.assertEqual(errors, [])

    def test_family_is_read_from_the_material_when_not_passed(self):
        errors, _ = check_material_physics("skin", {"family": "skin", "transmission": 0.3})
        self.assertTrue(errors)

    def test_fabric_without_sheen_warns_it_has_no_woven_cue(self):
        _, warnings = check_material_physics("shirt", {"roughness": 0.85}, family="fabric")
        self.assertTrue(any("woven or fibre cue" in w for w in warnings), warnings)

    def test_a_clean_fabric_material_passes(self):
        errors, _ = check_material_physics(
            "shirt", {"roughness": 0.85, "sheen": 0.6, "sheenColor": "#e8e2d8"}, family="fabric"
        )
        self.assertEqual(errors, [])


class OpenBoundarySideTests(unittest.TestCase):
    def test_open_garment_boundary_with_front_side_is_an_error(self):
        failures = check_open_boundary_sides(
            "shirt-shell",
            {"garment": {"boundaries": [{"kind": "hem", "at": 0.8}]}},
        )
        self.assertTrue(any("FrontSide" in f for f in failures), failures)

    def test_double_side_clears_it(self):
        failures = check_open_boundary_sides(
            "shirt-shell",
            {"side": "DoubleSide", "garment": {"boundaries": [{"kind": "hem", "at": 0.8}]}},
        )
        self.assertEqual(failures, [])

    def test_closed_boundaries_do_not_require_double_side(self):
        failures = check_open_boundary_sides(
            "shirt-shell",
            {"garment": {"boundaries": [{"kind": "hem", "at": 0.8, "closed": True}]}},
        )
        self.assertEqual(failures, [])

    def test_a_non_garment_component_is_not_checked(self):
        self.assertEqual(check_open_boundary_sides("chest", {"primitive": "ellipsoid"}), [])

    def test_a_garment_with_no_boundaries_is_not_checked(self):
        self.assertEqual(check_open_boundary_sides("shirt", {"garment": {}}), [])


if __name__ == "__main__":
    unittest.main()
