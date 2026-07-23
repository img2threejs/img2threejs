#!/usr/bin/env python3
"""Reference-grounded gradient-stop extractor.

Synthetic 3-zone gradient PNG (violet → blue → navy on white bg) → assert the extractor
recovers the correct hue zones, names them, and flags the blue-leaning violet (B > R) as
blue-collapse. Deterministic across runs. Pure stdlib.
"""
from __future__ import annotations

import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

INTAKE = Path(__file__).resolve().parent.parent / "stage1_intake"
sys.path.insert(0, str(INTAKE))
from extract_gradient_stops import extract_gradient_stops, hue_name  # noqa: E402


def write_rgb_png(path: Path, width: int, height: int, fn) -> None:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw += bytes(fn(x, y))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


class GradientStopsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.img = self.dir / "grad.png"
        W, H = 96, 48
        VIOLET = (150, 70, 210)   # B > R  → blue-collapse risk, hue ~274° = violet
        BLUE = (46, 108, 236)     # hue ~221° = blue
        NAVY = (8, 10, 30)        # dark blue
        WHITE = (255, 255, 255)

        def px(x, y):
            if y < 8 or y >= 40:
                return WHITE  # white border → corner-background = white, bar = foreground
            if x < 32:
                return VIOLET
            if x < 64:
                return BLUE
            return NAVY

        write_rgb_png(self.img, W, H, px)

    def test_hue_name_boundaries(self):
        self.assertEqual(hue_name(274.0), "violet")
        self.assertEqual(hue_name(221.0), "blue")
        self.assertEqual(hue_name(5.0), "red")

    def test_extracts_three_zones_in_order(self):
        result = extract_gradient_stops(self.img, axis="u", stops=6)
        names = [z["hueName"] for z in result["hueZones"]]
        # violet must appear before blue (order preserved along the axis)
        self.assertIn("violet", names)
        self.assertIn("blue", names)
        self.assertLess(names.index("violet"), names.index("blue"))

    def test_flags_blue_collapse_on_violet(self):
        result = extract_gradient_stops(self.img, axis="u", stops=6)
        violet_stops = [s for s in result["stops"] if s["hueName"] == "violet"]
        self.assertTrue(violet_stops, "expected at least one violet stop")
        v = violet_stops[0]
        self.assertEqual(v.get("hueRisk"), "blue-collapse")
        # suggested correction is magenta-leaning: R >= B
        self.assertGreaterEqual(v["suggestedRgb"][0], v["suggestedRgb"][2])

    def test_deterministic(self):
        a = extract_gradient_stops(self.img, axis="u", stops=6)
        b = extract_gradient_stops(self.img, axis="u", stops=6)
        self.assertEqual([s["rgb"] for s in a["stops"]], [s["rgb"] for s in b["stops"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
