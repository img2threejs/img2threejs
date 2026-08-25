#!/usr/bin/env python3
"""Tests for the scalp signed distance field.

The field exists because hair was held off the skull by a comment while the garment in the same file
was held off the body by a measurement. The measured failure it prevents: widening the hair side
masses by hand took closure from 42.2% to 40.9%, worse on all six views, with dark coverage going
DOWN because the widened mass slid off the skull rather than growing on it.

Run: python3 forge/tests/test_scalp_field.py
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))

from scalp_field import ScalpField, field_from_component, normalise_rings  # noqa: E402

# The humanoid demo's actual head: eight rows of [y, radiusX, radiusZ] with a parallel z-offset
# array. Carried verbatim so the tests exercise a real skull rather than a tidy synthetic one.
HUMANOID_HEAD_RINGS = [
    [5.10, 0.145, 0.249], [5.28, 0.190, 0.311], [5.36, 0.356, 0.375], [5.44, 0.416, 0.400],
    [5.60, 0.398, 0.480], [5.76, 0.398, 0.515], [5.90, 0.356, 0.498], [6.04, 0.327, 0.440],
]
HUMANOID_HEAD_OFFSETS = [0.010, 0.010, 0.02, 0.06, 0.05, 0.03, -0.01, -0.03]

CYLINDER = [[0.0, 1.0, 1.0], [2.0, 1.0, 1.0]]


class Validation(unittest.TestCase):
    def test_one_ring_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ScalpField([[0.0, 1.0, 1.0]])

    def test_zero_or_negative_radius_is_rejected(self) -> None:
        for bad in ([[0.0, 0.0, 1.0], [1.0, 1.0, 1.0]], [[0.0, 1.0, -0.5], [1.0, 1.0, 1.0]]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ScalpField(bad)

    def test_duplicate_heights_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ScalpField([[1.0, 0.5, 0.5], [1.0, 0.6, 0.6]])

    def test_non_finite_values_are_rejected(self) -> None:
        for bad in (float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ScalpField([[0.0, 1.0, 1.0], [bad, 1.0, 1.0]])

    def test_rings_are_sorted_not_assumed_sorted(self) -> None:
        field = ScalpField([[2.0, 1.0, 1.0], [0.0, 0.5, 0.5]])
        self.assertEqual(field.y_min, 0.0)
        self.assertEqual(field.y_max, 2.0)

    def test_mapping_and_tuple_forms_agree(self) -> None:
        tuples = ScalpField([[0.0, 1.0, 2.0, 0.1], [1.0, 1.5, 2.5, 0.2]])
        mappings = ScalpField([
            {"y": 0.0, "rx": 1.0, "rz": 2.0, "zc": 0.1},
            {"y": 1.0, "radiusX": 1.5, "radiusZ": 2.5, "zCentre": 0.2},
        ])
        self.assertAlmostEqual(tuples.distance(1.2, 0.5, 0.0), mappings.distance(1.2, 0.5, 0.0))

    def test_wrong_arity_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalise_rings([[0.0, 1.0], [1.0, 1.0]])


class SignIsExact(unittest.TestCase):
    """The magnitude is a first-order estimate; the sign is the part gates may rely on."""

    def test_axis_is_inside(self) -> None:
        field = ScalpField(CYLINDER)
        self.assertLess(field.distance(0.0, 1.0, 0.0), 0.0)

    def test_far_outside_is_positive(self) -> None:
        field = ScalpField(CYLINDER)
        self.assertGreater(field.distance(10.0, 1.0, 0.0), 0.0)

    def test_sign_flips_exactly_at_the_surface(self) -> None:
        field = ScalpField(CYLINDER)
        self.assertLess(field.distance(0.999, 1.0, 0.0), 0.0)
        self.assertGreater(field.distance(1.001, 1.0, 0.0), 0.0)
        self.assertAlmostEqual(field.distance(1.0, 1.0, 0.0), 0.0, places=9)

    def test_an_elliptical_section_is_not_treated_as_a_circle(self) -> None:
        field = ScalpField([[0.0, 1.0, 0.25], [1.0, 1.0, 0.25]])
        # Inside on the wide axis, outside on the narrow one, at the same radius.
        self.assertLess(field.distance(0.5, 0.5, 0.0), 0.0)
        self.assertGreater(field.distance(0.0, 0.5, 0.5), 0.0)

    def test_the_z_offset_moves_the_section(self) -> None:
        field = ScalpField([[0.0, 1.0, 1.0, 5.0], [1.0, 1.0, 1.0, 5.0]])
        self.assertLess(field.distance(0.0, 0.5, 5.0), 0.0)
        self.assertGreater(field.distance(0.0, 0.5, 0.0), 0.0)

    def test_a_surface_sample_reads_zero(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        for u in (0.0, 0.2, 0.5, 0.77):
            for v in (0.25, 0.5, 0.75):
                x, y, z = field.sample(u, v)
                with self.subTest(u=u, v=v):
                    self.assertAlmostEqual(field.distance(x, y, z), 0.0, places=6)


class Caps(unittest.TestCase):
    def test_above_the_stack_is_outside(self) -> None:
        field = ScalpField(CYLINDER)
        self.assertGreater(field.distance(0.0, 3.0, 0.0), 0.0)

    def test_below_the_stack_is_outside(self) -> None:
        field = ScalpField(CYLINDER)
        self.assertGreater(field.distance(0.0, -1.0, 0.0), 0.0)

    def test_the_axial_gap_is_the_distance_above_the_cap(self) -> None:
        field = ScalpField(CYLINDER)
        self.assertAlmostEqual(field.distance(0.0, 2.5, 0.0), 0.5, places=6)

    def test_a_corner_combines_both_gaps(self) -> None:
        field = ScalpField(CYLINDER)
        # Both gaps contribute, so the corner reads further out than either alone.
        radial_only = field.distance(2.0, 1.0, 0.0)
        axial_only = field.distance(0.0, 2.5, 0.0)
        corner = field.distance(2.0, 2.5, 0.0)
        self.assertGreater(corner, radial_only)
        self.assertGreater(corner, axial_only)
        self.assertAlmostEqual(corner, math.hypot(radial_only, axial_only), places=9)


class EstimateErrsSafely(unittest.TestCase):
    """The magnitude is `f / |grad f|`, not the true distance. Which WAY it errs decides whether a
    clearance gate built on it is safe, so the direction is pinned here rather than left to luck.

    For a circle of radius R at distance d from the axis the estimate is (d-R)(d+R)/2d against a
    true distance of |d-R|, so the ratio is (d+R)/2d: below 1 outside, above 1 inside. Outside it
    UNDERSTATES how clear a point is and inside it OVERSTATES how deep it is. Both readings make a
    `standProud` march push further than strictly necessary, which is the safe direction -- the
    failure being prevented is hair sitting inside the skull and rendering as a bald patch.
    """

    def test_outside_the_estimate_never_overstates_clearance(self) -> None:
        field = ScalpField(CYLINDER)
        for d in (1.05, 1.5, 2.0, 5.0):
            with self.subTest(d=d):
                self.assertLess(field.distance(d, 1.0, 0.0), d - 1.0)
                self.assertGreater(field.distance(d, 1.0, 0.0), 0.0)

    def test_inside_the_estimate_never_understates_depth(self) -> None:
        field = ScalpField(CYLINDER)
        for d in (0.2, 0.5, 0.95):
            with self.subTest(d=d):
                self.assertLess(field.distance(d, 1.0, 0.0), -(1.0 - d) + 1e-12)

    def test_the_estimate_converges_at_the_surface(self) -> None:
        """Where it matters most -- a vertex being marched to clearance -- the error vanishes."""
        field = ScalpField(CYLINDER)
        for d in (0.999, 1.001):
            with self.subTest(d=d):
                self.assertAlmostEqual(field.distance(d, 1.0, 0.0), d - 1.0, places=5)

    def test_nothing_returns_nan(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        for point in ((0, 0, 0), (0, 100, 0), (0, -100, 0), (1e6, 5.5, 1e6), (0.0, 5.10, 0.0)):
            with self.subTest(point=point):
                self.assertTrue(math.isfinite(field.distance(*point)))


class Normals(unittest.TestCase):
    def test_a_cylinder_normal_is_radial(self) -> None:
        field = ScalpField(CYLINDER)
        nx, ny, nz = field.normal(0.0, 0.5)
        self.assertAlmostEqual(nx, 1.0, places=5)
        self.assertAlmostEqual(ny, 0.0, places=5)
        self.assertAlmostEqual(nz, 0.0, places=5)

    def test_normals_are_unit_length(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        for u in (0.0, 0.13, 0.5, 0.9):
            for v in (0.1, 0.5, 0.95):
                with self.subTest(u=u, v=v):
                    self.assertAlmostEqual(math.dist((0, 0, 0), field.normal(u, v)), 1.0, places=6)

    def test_normals_point_outward(self) -> None:
        """Stepping along the normal must leave the skull; stepping against it must enter."""
        field = ScalpField(HUMANOID_HEAD_RINGS)
        for u in (0.0, 0.25, 0.5, 0.75):
            for v in (0.2, 0.5, 0.8):
                x, y, z = field.sample(u, v)
                nx, ny, nz = field.normal(u, v)
                with self.subTest(u=u, v=v):
                    self.assertGreater(field.distance(x + nx * 0.01, y + ny * 0.01, z + nz * 0.01), 0.0)
                    self.assertLess(field.distance(x - nx * 0.01, y - ny * 0.01, z - nz * 0.01), 0.0)

    def test_a_tapering_stack_tilts_its_normal_upward(self) -> None:
        """A radial approximation would push crown hair sideways off the top of the head."""
        field = ScalpField([[0.0, 1.0, 1.0], [1.0, 0.2, 0.2]])
        _, ny, _ = field.normal(0.0, 0.5)
        self.assertGreater(ny, 0.3)


class SurfaceSamples(unittest.TestCase):
    def test_samples_carry_area_weight_not_count(self) -> None:
        """A narrow crown ring must not weigh the same as a wide temple ring."""
        field = ScalpField([[0.0, 1.0, 1.0], [1.0, 0.1, 0.1]])
        band = [s for s in field.surface_samples(16, 8) if not s["cap"]]
        self.assertGreater(band[0]["weight"], band[-1]["weight"] * 3.0)

    def test_every_band_sample_lies_on_the_surface(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        for sample in field.surface_samples(12, 6):
            if sample["cap"]:
                continue
            self.assertAlmostEqual(field.distance(*sample["point"]), 0.0, places=6)

    def test_the_cap_disc_is_sampled(self) -> None:
        """The disc closing the stack belongs to no (u, v), so a caller iterating the band alone
        never sees it. On a skull whose top ring has real radius that disc IS the crown, and a
        completely bare crown measured as fully covered."""
        field = ScalpField(HUMANOID_HEAD_RINGS)
        cap = [s for s in field.surface_samples(16, 8) if s["cap"]]
        self.assertTrue(cap)
        for sample in cap:
            self.assertAlmostEqual(sample["point"][1], field.y_max, places=9)
            self.assertEqual(sample["normal"], (0.0, 1.0, 0.0))

    def test_the_cap_carries_the_disc_area(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        rx, rz, _ = field.section(field.y_max)
        cap_area = sum(s["weight"] for s in field.surface_samples(24, 12) if s["cap"])
        self.assertAlmostEqual(cap_area, math.pi * rx * rz, places=6)

    def test_a_band_that_stops_short_has_no_cap(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        self.assertFalse([s for s in field.surface_samples(12, 6, v_range=(0.2, 0.8)) if s["cap"]])

    def test_the_band_range_is_honoured(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        for sample in field.surface_samples(12, 6, v_range=(0.4, 0.7)):
            self.assertGreaterEqual(sample["v"], 0.4)
            self.assertLessEqual(sample["v"], 0.7)

    def test_a_degenerate_band_is_rejected(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        for bad in ((0.5, 0.5), (0.8, 0.2), (-0.1, 0.5), (0.5, 1.4)):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    field.surface_samples(12, 6, v_range=bad)

    def test_degenerate_sample_counts_are_rejected(self) -> None:
        field = ScalpField(CYLINDER)
        for u_count, v_count in ((2, 4), (8, 1)):
            with self.subTest(u_count=u_count, v_count=v_count):
                with self.assertRaises(ValueError):
                    field.surface_samples(u_count, v_count)


class FromComponent(unittest.TestCase):
    def test_the_parallel_offset_array_form_is_accepted(self) -> None:
        field = field_from_component({
            "geometryDescriptor": {
                "ringStack": {"rings": HUMANOID_HEAD_RINGS, "zOffsets": HUMANOID_HEAD_OFFSETS}
            }
        })
        self.assertEqual(len(field.rings), 8)
        # The offset must actually be applied: ring 3 sits 0.06 forward.
        _, _, zc = field.section(5.44)
        self.assertAlmostEqual(zc, 0.06, places=9)

    def test_a_mismatched_offset_array_is_an_error_not_a_silent_truncation(self) -> None:
        with self.assertRaises(ValueError):
            field_from_component({
                "geometryDescriptor": {"ringStack": {"rings": HUMANOID_HEAD_RINGS, "zOffsets": [0.0]}}
            })

    def test_missing_descriptor_shapes_raise(self) -> None:
        for component in ({}, {"geometryDescriptor": {}}, {"geometryDescriptor": {"ringStack": {}}}):
            with self.subTest(component=component):
                with self.assertRaises(ValueError):
                    field_from_component(component)


if __name__ == "__main__":
    unittest.main(verbosity=2)
