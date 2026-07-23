#!/usr/bin/env python3
"""Tests for analyze_texture.py — finish classification + recipe. Pure stdlib, zero token.
Run: python3 forge/tests/test_analyze_texture.py
"""
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage1_intake"))
from analyze_texture import RECIPES, analyze  # noqa: E402

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def write_png(path, w, h, fn):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            raw += bytes(fn(x, y))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    path.write_bytes(PNG_SIG + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def cl(v):
    return max(0, min(255, int(v)))


class AnalyzeTextureTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.S = 160

    def _mk(self, name, fn):
        p = self.d / name
        write_png(p, self.S, self.S, fn)
        return p

    def test_pigment_dominant_doppler_is_candy_coat(self):
        # doppler-like blue->purple gradient + smoky mottle, NO chrome specular (colour survives
        # into mid-tones) -> candy-coat (dielectric). This is the M9-Doppler case that previously
        # mis-classed as high-metalness gem-metal and rendered blue (env stole the hue).
        def fn(x, y):
            noise = ((x * 5 + y * 9) % 23) - 11  # smoke variance
            return (cl(20 + x * 1.2 + noise), cl(25 + noise), cl(130 + noise))
        r = analyze(self._mk("candy.png", fn))
        self.assertEqual(r["finishClass"], "candy-coat")
        self.assertEqual(r["recipe"]["procedural"], "gradient-smoke")
        self.assertLessEqual(r["recipe"]["metalness"], 0.4)   # dielectric-led → hue survives
        self.assertLessEqual(r["recipe"]["envMapIntensity"], 0.9)
        self.assertEqual(len(r["palette"]), 5)
        # blue-leaning stops (B > R) flagged for hue-survival with a magenta-lean suggestion
        self.assertTrue(r["paletteHueRisk"], "expected blue-collapse flag on blue-leaning stops")
        self.assertEqual(r["paletteHueRisk"][0]["hueRisk"], "blue-collapse")

    def test_chrome_specular_doppler_is_gem_metal(self):
        # same chromatic gradient but WITH bright chrome specular hotspots -> genuinely metallic
        # doppler (gem-metal, high metalness). Bright hotspots on ~6% of pixels (lum > 235).
        def fn(x, y):
            noise = ((x * 5 + y * 9) % 23) - 11
            if (x + y) % 17 == 0:
                return (250, 250, 255)  # chrome specular hotspot
            return (cl(20 + x * 1.2 + noise), cl(25 + noise), cl(130 + noise))
        r = analyze(self._mk("gem.png", fn))
        self.assertEqual(r["finishClass"], "gem-metal")
        self.assertGreaterEqual(r["recipe"]["metalness"], 0.6)

    def test_flat_saturated_is_painted_metal(self):
        img = self._mk("paint.png", lambda x, y: (230, 150, 50))
        r = analyze(img)
        self.assertEqual(r["finishClass"], "painted-metal")
        self.assertAlmostEqual(r["recipe"]["clearcoat"], 1.0)

    def test_mottled_grey_is_worn_composite(self):
        # dark neutral grey with isotropic mottle -> worn composite
        def fn(x, y):
            v = 55 + ((x * 7 + y * 13) % 37) - 18
            return (cl(v), cl(v), cl(v + 2))
        r = analyze(self._mk("worn.png", fn))
        self.assertEqual(r["finishClass"], "worn-composite")
        self.assertAlmostEqual(r["recipe"]["roughness"], 0.9)

    def test_directional_streaks_is_brushed_steel(self):
        # coarse bright horizontal grain (bands vary in Y, survive downsample), neutral -> brushed
        def fn(x, y):
            v = 150 + (38 if (y // 8) % 2 == 0 else -38)
            return (cl(v), cl(v), cl(v))
        r = analyze(self._mk("brushed.png", fn))
        self.assertEqual(r["finishClass"], "brushed-steel")
        self.assertAlmostEqual(r["recipe"]["metalness"], 1.0)
        self.assertAlmostEqual(r["recipe"]["anisotropy"], 1.0)

    def test_apply_to_material_writes_recipe(self):
        from analyze_texture import apply_to_material
        img = self._mk("paint2.png", lambda x, y: (230, 150, 50))
        result = analyze(img)
        mat = {"id": "frame", "roughness": {"base": 0.3, "variation": 0.1}}
        apply_to_material(mat, result)
        self.assertEqual(mat["finishClass"], "painted-metal")
        self.assertEqual(mat["roughness"]["base"], result["recipe"]["roughness"])  # layer shape kept
        self.assertEqual(mat["roughness"]["variation"], 0.1)
        self.assertIn("texturePalette", mat)
        self.assertEqual(mat["clearcoat"]["base"], result["recipe"]["clearcoat"])

    def test_all_recipes_have_required_scalars(self):
        keys = {"metalness", "roughness", "clearcoat", "clearcoatRoughness", "transmission",
                "ior", "envMapIntensity", "anisotropy", "procedural"}
        for name, rec in RECIPES.items():
            self.assertTrue(keys <= set(rec), f"{name} missing keys")


if __name__ == "__main__":
    unittest.main(verbosity=2)
