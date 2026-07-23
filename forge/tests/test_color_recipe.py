#!/usr/bin/env python3
"""Unit tests for the Plan 1.3 Workstream C color/material math: srgb_to_lab,
lab_to_rgba round-trip, lab_kmeans_palette, estimate_roughness_from_hotspot,
and detect_color_gradient (including the monotonicity gate). Independent of
forge/tests/test_pipeline.py per the plan's acceptance criteria — these test
pure functions directly, not the CLI subprocess surface.

Run: python3 forge/tests/test_color_recipe.py
  or: python3 -m unittest discover -s forge/tests
"""
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL / "forge" / "stage1_intake"))

from extract_part_color_recipe import (  # noqa: E402
    build_recipe,
    detect_color_gradient,
    lab_distance,
    lab_kmeans_palette,
    lab_to_rgba,
    srgb_to_lab,
    estimate_roughness_from_hotspot,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def write_rgb_png(path: Path, w: int, h: int, pixel_fn) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            raw += bytes(pixel_fn(x, y, w, h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (
        PNG_SIGNATURE
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


class SrgbToLabTest(unittest.TestCase):
    def test_white_is_l100_a0_b0(self):
        l_star, a_star, b_star = srgb_to_lab((255, 255, 255))
        self.assertAlmostEqual(l_star, 100.0, delta=0.5)
        self.assertAlmostEqual(a_star, 0.0, delta=0.5)
        self.assertAlmostEqual(b_star, 0.0, delta=0.5)

    def test_pure_red_matches_reference_values(self):
        l_star, a_star, b_star = srgb_to_lab((255, 0, 0))
        self.assertAlmostEqual(l_star, 53.24, delta=0.5)
        self.assertAlmostEqual(a_star, 80.09, delta=0.5)
        self.assertAlmostEqual(b_star, 67.20, delta=0.5)


class LabToRgbaRoundTripTest(unittest.TestCase):
    def test_round_trip_within_one_unit(self):
        for rgb in [(138, 109, 75), (12, 200, 44), (0, 0, 0), (255, 255, 255)]:
            with self.subTest(rgb=rgb):
                lab = srgb_to_lab(rgb)
                rgba = lab_to_rgba(lab, alpha=0.5)
                self.assertTrue(rgba.startswith("rgba("))
                r, g, b, a = rgba[5:-1].split(",")
                self.assertAlmostEqual(int(r), rgb[0], delta=1)
                self.assertAlmostEqual(int(g), rgb[1], delta=1)
                self.assertAlmostEqual(int(b), rgb[2], delta=1)
                self.assertEqual(float(a), 0.5)


class LabKmeansPaletteTest(unittest.TestCase):
    def test_two_known_colors_in_known_split(self):
        color_a = srgb_to_lab((200, 60, 40))
        color_b = srgb_to_lab((40, 60, 200))
        samples = [color_a] * 70 + [color_b] * 30
        clusters = lab_kmeans_palette(samples, k=2)
        self.assertEqual(len(clusters), 2)
        shares = sorted((c["share_pct"] for c in clusters), reverse=True)
        self.assertAlmostEqual(shares[0], 0.7, delta=0.02)
        self.assertAlmostEqual(shares[1], 0.3, delta=0.02)


class RoughnessFromHotspotTest(unittest.TestCase):
    def test_tight_hotspot_is_lower_roughness_than_broad_hotspot(self):
        # Tight: only 2% of foreground pixels are near-max brightness.
        tight_lumas = [0.95] * 2 + [0.2] * 98
        tight_mask = [True] * 100
        tight_roughness, _ = estimate_roughness_from_hotspot(tight_lumas, tight_mask)

        # Broad: half the foreground is near-max brightness (gradual falloff).
        broad_lumas = [0.95] * 50 + [0.3] * 50
        broad_mask = [True] * 100
        broad_roughness, _ = estimate_roughness_from_hotspot(broad_lumas, broad_mask)

        self.assertLess(tight_roughness, broad_roughness)


class ColorGradientDetectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_flat_color_yields_no_gradient(self):
        write_rgb_png(self.tmp / "flat.png", 64, 64, lambda x, y, w, h: (120, 90, 60))
        recipe = build_recipe("flat-part", self.tmp / "flat.png")
        self.assertNotIn("colorGradient", recipe)

    def test_linear_gradient_is_detected_with_correct_axis(self):
        def grad_pixel(x, y, w, h):
            t = x / (w - 1)
            return (round(60 + t * 140), round(45 + t * 110), round(30 + t * 90))

        write_rgb_png(self.tmp / "gradient.png", 64, 64, grad_pixel)
        recipe = build_recipe("gradient-part", self.tmp / "gradient.png")
        self.assertIn("colorGradient", recipe)
        gradient = recipe["colorGradient"]
        self.assertEqual(gradient["type"], "linear")
        # horizontal gradient -> axis should point predominantly along x
        self.assertGreater(abs(gradient["axis"][0]), abs(gradient["axis"][1]))
        self.assertEqual(len(gradient["stops"]), 3)
        offsets = [stop["offset"] for stop in gradient["stops"]]
        self.assertEqual(offsets, sorted(offsets))

    def test_checkerboard_pattern_yields_no_gradient(self):
        # Real color variance (two colors), but zero monotonic directional trend —
        # proves the monotonicity gate rejects textured/patterned regions, not just
        # the magnitude gate.
        def checker_pixel(x, y, w, h):
            on = ((x // 8) + (y // 8)) % 2 == 0
            return (60, 45, 30) if on else (200, 155, 100)

        write_rgb_png(self.tmp / "checker.png", 64, 64, checker_pixel)
        recipe = build_recipe("checker-part", self.tmp / "checker.png")
        self.assertNotIn("colorGradient", recipe)


if __name__ == "__main__":
    unittest.main()
