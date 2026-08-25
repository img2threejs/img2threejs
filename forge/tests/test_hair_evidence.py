#!/usr/bin/env python3
"""Tests for stage 1 hair evidence extraction.

Three things this fills that were previously absent: `faceLandmarks.hairline`, a slot that has
existed since v1.2 with nothing ever writing to it; banded dark coverage, which was run by hand four
times in one session and never became a script; and shading evidence, which nothing measured at all
and which turned out to be where the deficit mostly lived.

`ThresholdIsNotATautology` is the load-bearing class. The first implementation cut at a fixed
percentile, which makes the reported hair fraction true by construction -- it read 0.380, 0.384 and
0.382 across three different views of the same subject, which looks like agreement and is
arithmetic.

Run: python3 forge/tests/test_hair_evidence.py
"""
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage1_intake"))

from extract_hair_evidence import (  # noqa: E402
    MIN_CLASS_SEPARATION,
    MIN_SEPARABILITY,
    analyse_view,
    extract_hair_evidence,
    otsu_threshold,
)


def write_png(path: Path, width: int, height: int, pixel) -> None:
    """Minimal RGBA PNG writer. `pixel(x, y)` returns (r, g, b, a)."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type: none
        for x in range(width):
            raw.extend(bytes(pixel(x, y)))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )


BACKGROUND = (255, 255, 255, 0)
SKIN = (200, 160, 140, 255)
HAIR = (40, 30, 25, 255)
HIGHLIGHT = (150, 130, 110, 255)


FIGURE_TOP = 5
FIGURE_BOTTOM = 395
# The module takes the head to be the top HEAD_FRACTION (0.15) of the figure, anchored on the
# reference's own proportion. A fixture must therefore be a whole FIGURE, not a head: a 90-row
# head-only image makes the analysed band 13 rows of pure hair, one luminance population, and the
# module correctly refuses to split it. Getting that wrong was the first attempt here.
HEAD_ROWS = int((FIGURE_BOTTOM - FIGURE_TOP) * 0.15)


def head_image(path: Path, hair_rows: int = 6, highlight_row: int | None = None,
               width: int = 120, height: int = 400) -> None:
    """A full figure whose head band is `hair_rows` of hair over skin.

    `hair_rows` counts rows inside the head band, which is what the module actually analyses.
    """
    def pixel(x: int, y: int):
        if not (20 <= x < 100 and FIGURE_TOP <= y < FIGURE_BOTTOM):
            return BACKGROUND
        if y < FIGURE_TOP + hair_rows:
            if highlight_row is not None and y == highlight_row:
                return HIGHLIGHT
            return HAIR
        return SKIN

    write_png(path, width, height, pixel)


class OtsuBehaviour(unittest.TestCase):
    def test_two_clear_populations_split_between_them(self) -> None:
        threshold, separation, _ = otsu_threshold([10.0] * 50 + [200.0] * 50)
        self.assertGreater(threshold, 10.0)
        self.assertLess(threshold, 200.0)
        self.assertGreater(separation, 150.0)

    def test_one_population_reports_no_separation(self) -> None:
        _, separation, _ = otsu_threshold([120.0] * 100)
        self.assertEqual(separation, 0.0)

    def test_an_empty_population_does_not_raise(self) -> None:
        self.assertEqual(otsu_threshold([]), (0.0, 0.0, 0.0))

    def test_the_split_moves_with_the_data_not_with_the_count(self) -> None:
        """The property a percentile cut does not have."""
        mostly_dark, _, _ = otsu_threshold([10.0] * 90 + [200.0] * 10)
        mostly_light, _, _ = otsu_threshold([10.0] * 10 + [200.0] * 90)
        self.assertAlmostEqual(mostly_dark, mostly_light, delta=25.0)

    def test_a_uniform_spread_scores_the_theoretical_unimodal_value(self) -> None:
        """sqrt(3) for any uniform population cut at its own middle -- and INDEPENDENT of width,
        which is the whole reason separability catches what raw separation cannot."""
        for width in (20.0, 80.0, 200.0):
            with self.subTest(width=width):
                spread = [i * width / 400 for i in range(400)]
                _, separation, separability = otsu_threshold(spread)
                self.assertAlmostEqual(separability, 1.732, delta=0.05)
                self.assertGreater(separation, width * 0.4)

    def test_two_tight_clusters_score_far_above_the_floor(self) -> None:
        _, _, separability = otsu_threshold([30.0] * 100 + [200.0] * 100)
        self.assertGreater(separability, MIN_SEPARABILITY * 2)


class ThresholdIsNotATautology(unittest.TestCase):
    def test_more_hair_in_the_image_reports_more_hair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fractions = []
            for hair_rows in (8, 24, 48):
                path = Path(directory) / f"h{hair_rows}.png"
                head_image(path, hair_rows=hair_rows)
                fractions.append(analyse_view(path, "front")["hairFraction"])
        self.assertLess(fractions[0], fractions[1])
        self.assertLess(fractions[1], fractions[2])

    def test_a_head_with_no_hair_is_reported_as_having_no_split(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bald.png"
            head_image(path, hair_rows=0)
            result = analyse_view(path, "front")
        self.assertEqual(result["status"], "no-hair-skin-split")

    def test_a_bald_head_under_a_key_light_is_still_rejected(self) -> None:
        """The case raw separation cannot see. Shading alone gives a bald scalp a wide luminance
        spread -- measured separation 38.6, three times any sane floor -- and it reported a quarter
        of itself as hair. Separability stays at the unimodal value however hard the light is.
        """
        def shaded(path: Path, spread: int) -> None:
            def pixel(x: int, y: int):
                if not (20 <= x < 100 and FIGURE_TOP <= y < FIGURE_BOTTOM):
                    return BACKGROUND
                value = max(0, min(255, int(160 + spread * ((x - 20) / 80.0 - 0.5))))
                return (value, int(value * 0.8), int(value * 0.7), 255)
            write_png(path, 120, 400, pixel)

        with tempfile.TemporaryDirectory() as directory:
            for spread in (20, 80, 200):
                path = Path(directory) / f"bald{spread}.png"
                shaded(path, spread)
                result = analyse_view(path, "front")
                with self.subTest(spread=spread):
                    self.assertEqual(result["status"], "no-hair-skin-split")
                    self.assertLess(result["separability"], MIN_SEPARABILITY)
                    # A faint gradient is caught by the raw separation floor and never reaches the
                    # separability test; a strong one passes that floor easily and is caught only
                    # here. Both are correct rejections, and asserting one message for both would
                    # be asserting a detail of which rule fired first.
                    if result["classSeparation"] >= MIN_CLASS_SEPARATION:
                        self.assertIn("one broad", " ".join(result["warnings"]))

    def test_the_no_split_case_says_why(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bald.png"
            head_image(path, hair_rows=0)
            result = analyse_view(path, "front")
        self.assertTrue(any("cannot tell hair from skin" in w for w in result["warnings"]))


class BandedCoverage(unittest.TestCase):
    def test_hair_confined_to_the_top_shows_in_the_crown_band_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "top.png"
            head_image(path, hair_rows=HEAD_ROWS // 3)
            bands = analyse_view(path, "front")["bands"]
        self.assertGreater(bands["crown"]["coverage"], 0.85)
        self.assertLess(bands["jaw"]["coverage"], 0.15)

    def test_every_band_is_reported_even_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "top.png"
            head_image(path, hair_rows=12)
            bands = analyse_view(path, "front")["bands"]
        self.assertEqual(set(bands), {"crown", "mid", "jaw"})
        for band in bands.values():
            self.assertIn("coverage", band)
            self.assertIn("pixelCount", band)


class Shading(unittest.TestCase):
    def test_the_highlight_row_is_found(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.png"
            head_image(path, hair_rows=30, highlight_row=FIGURE_TOP + 10)
            shading = analyse_view(path, "front")["shading"]
        self.assertIsNotNone(shading["specularBandV"])
        # The highlight sits 10 rows into a 58-row head band, so in its upper quarter.
        self.assertLess(shading["specularBandV"], 0.5)
        self.assertGreater(shading["specularBandLuma"], 100.0)

    def test_a_flat_mass_reports_no_root_to_tip_delta_worth_having(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flat.png"
            head_image(path, hair_rows=30)
            shading = analyse_view(path, "front")["shading"]
        self.assertIsNotNone(shading["rootToTipLumaDelta"])
        self.assertAlmostEqual(shading["rootToTipLumaDelta"], 0.0, delta=1.0)

    def test_the_delta_sign_convention_is_documented_not_assumed(self) -> None:
        """Which end is the root depends on the hairstyle, and this module does not guess."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flat.png"
            head_image(path, hair_rows=30)
            note = analyse_view(path, "front")["shading"]["note"]
        self.assertIn("not inferred here", note)


class Hairline(unittest.TestCase):
    def test_the_hairline_tracks_where_the_hair_actually_stops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shallow = Path(directory) / "a.png"
            deep = Path(directory) / "b.png"
            head_image(shallow, hair_rows=12)
            head_image(deep, hair_rows=45)
            a = analyse_view(shallow, "front")["hairline"]
            b = analyse_view(deep, "front")["hairline"]
        self.assertLess(a, b)

    def test_the_consensus_hairline_reaches_faceLandmarks(self) -> None:
        """The slot that has existed since v1.2 with nothing writing to it."""
        with tempfile.TemporaryDirectory() as directory:
            front = Path(directory) / "front.png"
            profile = Path(directory) / "profile.png"
            head_image(front, hair_rows=20)
            head_image(profile, hair_rows=20)
            report = extract_hair_evidence({"front": front, "profile": profile})
        self.assertIn("hairline", report["faceLandmarks"])
        self.assertGreater(report["faceLandmarks"]["hairline"], 0.0)


class HonestyAboutWhatWasNotSeen(unittest.TestCase):
    def test_a_frontal_only_set_reports_the_rear_as_unobserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            front = Path(directory) / "front.png"
            head_image(front, hair_rows=20)
            report = extract_hair_evidence({"front": front})
        joined = " ".join(report["notObserved"])
        self.assertIn("rear", joined)
        self.assertIn("Do not author them as if measured", joined)

    def test_a_set_that_includes_a_rear_view_does_not_claim_it_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = {}
            for name in ("front", "rear"):
                path = Path(directory) / f"{name}.png"
                head_image(path, hair_rows=20)
                paths[name] = path
            report = extract_hair_evidence(paths)
        self.assertFalse([n for n in report["notObserved"] if n.startswith("rear")])

    def test_a_single_view_reports_that_depth_is_unobservable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            front = Path(directory) / "front.png"
            head_image(front, hair_rows=20)
            report = extract_hair_evidence({"front": front})
        self.assertTrue(any(n.startswith("depth") for n in report["notObserved"]))

    def test_confidence_rises_with_the_number_of_usable_views(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = {}
            confidences = []
            for name in ("front", "rear", "profile", "left-profile"):
                path = Path(directory) / f"{name}.png"
                head_image(path, hair_rows=20)
                paths[name] = path
                confidences.append(extract_hair_evidence(dict(paths))["confidence"])
        self.assertEqual(confidences, sorted(confidences))
        self.assertEqual(confidences[-1], 1.0)

    def test_lock_geometry_is_explicitly_not_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            front = Path(directory) / "front.png"
            head_image(front, hair_rows=20)
            report = extract_hair_evidence({"front": front})
        self.assertIn("Lock geometry is not", report["calibrationNote"])
        self.assertNotIn("locks", report["views"]["front"])


class RealReferenceViews(unittest.TestCase):
    """The synthetic cases above prove the arithmetic; this proves it survives a real render."""

    def test_the_reference_views_disagree_with_each_other_in_the_right_direction(self) -> None:
        from showcase_test_support import showcase_root  # noqa: PLC0415

        captures = showcase_root() / "artifacts" / "low-poly-humanoid-glb" / "TRY1"
        if not captures.is_dir():
            raise unittest.SkipTest("archived reference captures are not in this checkout")

        views = {}
        for name in ("front", "rear"):
            path = captures / f"glb-baseline.{name}.png"
            if path.is_file():
                views[name] = path
        if len(views) < 2:
            raise unittest.SkipTest("need both a front and a rear baseline capture")

        report = extract_hair_evidence(views)
        front = report["views"]["front"]["hairFraction"]
        rear = report["views"]["rear"]["hairFraction"]
        # The back of a head is nearly all hair; the front is mostly face. A tautological threshold
        # reported these as equal to three decimal places.
        self.assertGreater(rear, front + 0.15, f"front={front} rear={rear}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
