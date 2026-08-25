#!/usr/bin/env python3
"""Tests for the scalp exposure gate.

The gate exists to catch one recorded failure at build time. Widening the hair side masses by hand
took closure from 42.2% to 40.9%, worse on all six views, with dark coverage DOWN -- the widened
mass had slid off the skull and the render grew a bare strip. Nothing in the review path saw it;
only the eye did, three times running.

The decisive test here is `test_hair_that_sank_into_the_skull_is_not_coverage`: a gate that merely
asked "is there a hair vertex near this patch" would have passed that build, because the vertices
were still nearby -- they had sunk below the surface.

Run: python3 forge/tests/test_scalp_exposure.py
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage4_review"))

from scalp_field import ScalpField  # noqa: E402
from scalp_exposure import largest_exposed_run, scalp_exposure  # noqa: E402

HUMANOID_HEAD_RINGS = [
    [5.10, 0.145, 0.249], [5.28, 0.190, 0.311], [5.36, 0.356, 0.375], [5.44, 0.416, 0.400],
    [5.60, 0.398, 0.480], [5.76, 0.398, 0.515], [5.90, 0.356, 0.498], [6.04, 0.327, 0.440],
]

# The band above the hairline. The face is scalp by geometry and not by anatomy, so a full sweep
# would report the forehead and chin as bald.
CROWN_BAND = (0.55, 1.0)


def shell(field: ScalpField, offset: float, u_count: int = 96, v_count: int = 48,
          v_range: tuple[float, float] = (0.0, 1.0),
          skip: object = None) -> list[tuple[float, float, float]]:
    """A dense layer of points sitting `offset` outside the skull.

    `skip` takes (u, v) and returns True where the layer should have a hole, which is how the bald
    strip cases below are built.
    """
    low, high = v_range
    points = []
    for j in range(v_count):
        v = low + (high - low) * (j + 0.5) / v_count
        for i in range(u_count):
            u = i / u_count
            if skip is not None and skip(u, v):
                continue
            px, py, pz = field.sample(u, v)
            nx, ny, nz = field.normal(u, v)
            points.append((px + nx * offset, py + ny * offset, pz + nz * offset))

    # The cap disc closing the top of the ring stack has no (u, v) and so is never produced by the
    # loop above. Real hair over a crown does cover it, and leaving it out of this helper is what
    # let a version of the gate that ignored the cap entirely look correct in these tests.
    if high >= 1.0 - 1e-9:
        top_rx, top_rz, top_zc = field.section(field.y_max)
        for ring_index in range(max(1, v_count // 4)):
            radius_fraction = (ring_index + 0.5) / max(1, v_count // 4)
            for i in range(u_count):
                u = i / u_count
                if skip is not None and skip(u, 1.0):
                    continue
                angle = 2.0 * math.pi * u
                points.append((
                    top_rx * radius_fraction * math.cos(angle),
                    field.y_max + offset,
                    top_zc + top_rz * radius_fraction * math.sin(angle),
                ))
    return points


class FullCoverage(unittest.TestCase):
    def test_a_skull_fully_wrapped_reports_nothing_exposed(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        result = scalp_exposure(field, shell(field, 0.02), v_range=CROWN_BAND)
        self.assertEqual(result["exposedFraction"], 0.0)
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["exposedSamples"], [])

    def test_no_hair_at_all_reports_everything_exposed(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        result = scalp_exposure(field, [], v_range=CROWN_BAND)
        self.assertEqual(result["exposedFraction"], 1.0)
        self.assertEqual(result["verdict"], "fail")

    def test_hair_beyond_reach_does_not_count_as_coverage(self) -> None:
        """A mass floating clear of the head is a separate object, not the layer that covers it."""
        field = ScalpField(HUMANOID_HEAD_RINGS)
        height = field.y_max - field.y_min
        result = scalp_exposure(field, shell(field, height * 0.9), v_range=CROWN_BAND)
        self.assertGreater(result["exposedFraction"], 0.9)


class TheRecordedFailure(unittest.TestCase):
    def test_hair_that_sank_into_the_skull_is_not_coverage(self) -> None:
        """The TRY2 failure, encoded.

        Every point is close to the scalp -- a nearest-neighbour test would call this covered. Every
        point is also INSIDE the skull, which is exactly what a widened straight-spine loft does
        against a convex skull, and what renders as bald.
        """
        field = ScalpField(HUMANOID_HEAD_RINGS)
        sunk = shell(field, -0.012)
        result = scalp_exposure(field, sunk, v_range=CROWN_BAND)
        self.assertEqual(result["exposedFraction"], 1.0)
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["hairPointsInsideSkull"], len(sunk))

    def test_a_partial_sink_exposes_only_the_sunk_region(self) -> None:
        """Half the hair proud, half sunk: the gate must localise, not just alarm."""
        field = ScalpField(HUMANOID_HEAD_RINGS)
        points = (
            shell(field, 0.02, skip=lambda u, v: u < 0.5)
            + shell(field, -0.012, skip=lambda u, v: u >= 0.5)
        )
        result = scalp_exposure(field, points, v_range=CROWN_BAND)
        self.assertGreater(result["exposedFraction"], 0.35)
        self.assertLess(result["exposedFraction"], 0.65)
        for sample in result["exposedSamples"]:
            self.assertLess(sample["u"], 0.55)


class BaldStrips(unittest.TestCase):
    def test_a_strip_is_found_and_its_area_is_about_right(self) -> None:
        """A quarter of the azimuth left bare must read as about a quarter of the area."""
        field = ScalpField(HUMANOID_HEAD_RINGS)
        points = shell(field, 0.02, skip=lambda u, v: 0.25 <= u < 0.5)
        result = scalp_exposure(field, points, v_range=CROWN_BAND)
        self.assertAlmostEqual(result["exposedFraction"], 0.25, delta=0.05)
        self.assertEqual(result["verdict"], "fail")

    def test_the_strip_is_reported_where_it_actually_is(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        points = shell(field, 0.02, skip=lambda u, v: 0.25 <= u < 0.5)
        result = scalp_exposure(field, points, v_range=CROWN_BAND)
        self.assertTrue(result["exposedSamples"])
        for sample in result["exposedSamples"]:
            self.assertTrue(0.20 <= sample["u"] <= 0.55, f"unexpected exposure at u={sample['u']}")

    def test_a_strip_reads_as_a_run_and_scattered_holes_do_not(self) -> None:
        """A run is a parting a viewer sees; a scatter is sampling noise."""
        field = ScalpField(HUMANOID_HEAD_RINGS)
        strip = scalp_exposure(field, shell(field, 0.02, skip=lambda u, v: 0.25 <= u < 0.5),
                               v_range=CROWN_BAND)
        self.assertGreaterEqual(largest_exposed_run(strip), 6)

        scatter = scalp_exposure(
            field,
            shell(field, 0.02, skip=lambda u, v: int(u * 96) % 24 == 0),
            v_range=CROWN_BAND,
        )
        self.assertLessEqual(largest_exposed_run(scatter), 2)

    def test_a_bare_crown_cap_is_found(self) -> None:
        """The exact shape of the recorded failure: hair present at the sides, gone off the top."""
        field = ScalpField(HUMANOID_HEAD_RINGS)
        points = shell(field, 0.02, skip=lambda u, v: v > 0.88)
        result = scalp_exposure(field, points, v_range=CROWN_BAND)
        self.assertGreater(result["exposedFraction"], 0.10)
        for sample in result["exposedSamples"]:
            self.assertGreater(sample["v"], 0.80)


class TheCapDisc(unittest.TestCase):
    """The flat disc closing the top of the ring stack, which has no (u, v) of its own.

    The parametrisation walks v from the bottom ring to the top and samples the ELLIPSE at each
    height, so nothing ever visits the disc that closes the stack. On a skull whose top ring still
    has real radius that disc IS the crown -- and a completely bare crown scored exposedFraction 0.0
    and passed. The one place a bald patch shows most was the one place the gate could not look.
    """

    def test_a_bare_cap_is_now_found(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        result = scalp_exposure(field, shell(field, 0.02, skip=lambda u, v: v > 0.97),
                                v_range=CROWN_BAND)
        self.assertGreater(result["exposedFraction"], 0.15)
        self.assertEqual(result["verdict"], "fail")

    def test_the_cap_samples_are_labelled_so_a_caller_can_tell_where(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        result = scalp_exposure(field, shell(field, 0.02, skip=lambda u, v: v > 0.97),
                                v_range=CROWN_BAND)
        cap = [s for s in result["exposedSamples"] if s.get("cap")]
        self.assertTrue(cap)
        for sample in cap:
            self.assertAlmostEqual(sample["y"], field.y_max, places=6)

    def test_a_covered_cap_reports_nothing(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        result = scalp_exposure(field, shell(field, 0.02), v_range=CROWN_BAND)
        self.assertEqual(result["exposedFraction"], 0.0)

    def test_a_band_that_stops_below_the_crown_does_not_sample_the_cap(self) -> None:
        """Asking about the temples should not report the crown."""
        field = ScalpField(HUMANOID_HEAD_RINGS)
        result = scalp_exposure(field, [], v_range=(0.4, 0.7))
        self.assertFalse([s for s in result["exposedSamples"] if s.get("cap")])


class ExposedRuns(unittest.TestCase):
    def test_a_fully_exposed_ring_reports_the_whole_ring(self) -> None:
        """Every column's predecessor is also exposed, so the run-start scan finds no start and
        fell through reporting zero -- the worst case reporting as the best."""
        field = ScalpField(HUMANOID_HEAD_RINGS)
        result = scalp_exposure(field, [], v_range=CROWN_BAND)
        self.assertEqual(largest_exposed_run(result), 32)

    def test_a_partial_run_is_still_measured_as_a_run(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        result = scalp_exposure(field, shell(field, 0.02, skip=lambda u, v: 0.25 <= u < 0.5),
                                v_range=CROWN_BAND)
        run = largest_exposed_run(result)
        self.assertGreaterEqual(run, 6)
        self.assertLess(run, 32)


class AreaWeighting(unittest.TestCase):
    def test_the_fraction_is_area_weighted_not_sample_counted(self) -> None:
        """A small bare crown must not hide behind a large well-covered band.

        On a strongly tapered skull the top ring holds the same NUMBER of samples as the bottom but
        a small fraction of the area, so the two weightings give visibly different answers.
        """
        field = ScalpField([[0.0, 1.0, 1.0], [1.0, 0.1, 0.1]])
        points = shell(field, 0.02, skip=lambda u, v: v > 0.5)
        result = scalp_exposure(field, points)
        by_count = len(result["exposedSamples"]) / result["sampleCount"]

        # Roughly the upper half of the samples are bare either way -- the exact row count depends
        # on how far the reach march lets the topmost covered row extend upward, which is a sampling
        # detail and not the property under test.
        self.assertGreater(by_count, 0.3)
        # The property under test: on a skull that tapers 10:1, that same bare region is a far
        # smaller share of the AREA, and the gate must report the smaller, truer number.
        self.assertLess(result["exposedFraction"], by_count * 0.6)


class TheFailureIsRealInPixels(unittest.TestCase):
    """The geometric gate above is only worth having if the failure it models actually happened.

    This measures scalp showing through the crown band of the saved renders: light pixels inside the
    top 9% of the figure, where the hair mass should be. It is a PROXY, not a bald-fraction -- lit
    hair also reads light, which is why the absolute numbers sit near 50% rather than near zero. The
    signal is the CHANGE, and it is unambiguous. Measured 2026-08-07 on the archived captures:

        view             TRY1     TRY2       delta
        orbit-plus-35    54.0%    68.9%     +14.9
        profile          48.8%    55.1%      +6.3
        front            53.3%    57.6%      +4.3
        orbit-minus-35   43.3%    44.3%      +1.0

    Widening the side masses raised scalp exposure on every view, worst on the exact view where the
    bare crown was visible by eye. That is the mechanism this module encodes: the mass slid off the
    skull instead of growing on it.
    """

    BAND = 0.09
    LIGHT = 110.0

    def crown_light_fraction(self, png: Path) -> float:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage1_intake"))
        from extract_pbr_evidence import build_foreground_mask, load_image  # noqa: PLC0415

        width, height, pixels, _ = load_image(png)
        mask, _, _ = build_foreground_mask(width, height, pixels)
        rows = [i // width for i, on in enumerate(mask) if on]
        columns = [i % width for i, on in enumerate(mask) if on]
        if not rows:
            self.fail(f"{png.name} has an empty foreground mask")
        top, bottom = min(rows), max(rows)
        band_bottom = top + int((bottom - top + 1) * self.BAND)

        light = total = 0
        for y in range(top, band_bottom + 1):
            for x in range(min(columns), max(columns) + 1):
                index = y * width + x
                if not mask[index]:
                    continue
                r, g, b, _ = pixels[index]
                total += 1
                if 0.2126 * r + 0.7152 * g + 0.0722 * b > self.LIGHT:
                    light += 1
        return light / total if total else 0.0

    def test_widening_the_side_masses_raised_scalp_exposure_on_every_view(self) -> None:
        from showcase_test_support import showcase_root  # noqa: PLC0415

        captures = showcase_root() / "artifacts" / "low-poly-humanoid-glb"
        if not (captures / "TRY1").is_dir() or not (captures / "TRY2").is_dir():
            raise unittest.SkipTest("archived TRY1/TRY2 captures are not in this checkout")

        for view in ("orbit-plus-35", "orbit-minus-35", "front", "profile"):
            before = captures / "TRY1" / f"procedural-factory.{view}.png"
            after = captures / "TRY2" / f"procedural-factory.{view}.png"
            if not before.is_file() or not after.is_file():
                continue
            with self.subTest(view=view):
                self.assertGreater(
                    self.crown_light_fraction(after),
                    self.crown_light_fraction(before),
                    f"{view}: widening the masses should have raised scalp exposure",
                )


class Contract(unittest.TestCase):
    def test_the_uncalibrated_threshold_is_declared_as_such(self) -> None:
        """No multipart hair reference exists yet, so this bound is chosen, not measured."""
        field = ScalpField(HUMANOID_HEAD_RINGS)
        result = scalp_exposure(field, shell(field, 0.02), v_range=CROWN_BAND)
        self.assertTrue(result["hardMaxUncalibrated"])

    def test_a_degenerate_band_is_rejected(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        for bad in ((0.5, 0.5), (0.8, 0.2), (-0.1, 0.5), (0.5, 1.4)):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    scalp_exposure(field, [], v_range=bad)

    def test_non_positive_reach_is_rejected(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        with self.assertRaises(ValueError):
            scalp_exposure(field, [], reach=0.0)

    def test_the_report_counts_what_it_discarded(self) -> None:
        field = ScalpField(HUMANOID_HEAD_RINGS)
        proud = shell(field, 0.02, u_count=24, v_count=12)
        sunk = shell(field, -0.02, u_count=24, v_count=12)
        result = scalp_exposure(field, proud + sunk, v_range=CROWN_BAND)
        self.assertEqual(result["hairPointCount"], len(proud) + len(sunk))
        self.assertEqual(result["hairPointsInsideSkull"], len(sunk))


if __name__ == "__main__":
    unittest.main(verbosity=2)
