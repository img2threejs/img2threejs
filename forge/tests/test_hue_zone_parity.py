#!/usr/bin/env python3
"""Divine Eye hue_zone_parity (report-only, CIEDE2000).
Same-shape wrong-hue render must score LOWER than a right-hue render. Pure stdlib.
"""
from __future__ import annotations

import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

REVIEW = Path(__file__).resolve().parents[1] / "stage4_review"
sys.path.insert(0, str(REVIEW))
from divine_eye import hue_zone_parity  # noqa: E402


def write_rgb_png(path: Path, w: int, h: int, fn) -> None:
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            raw += bytes(fn(x, y))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


VIOLET = (150, 70, 210)
BLUE = (46, 108, 236)
WHITE = (255, 255, 255)
W, H = 96, 48


def _bar(color_left, color_right):
    def fn(x, y):
        if y < 8 or y >= 40:
            return WHITE
        return color_left if x < 48 else color_right
    return fn


class HueZoneParityTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ref = self.d / "ref.png"
        self.right = self.d / "right.png"
        self.wrong = self.d / "wrong.png"
        write_rgb_png(self.ref, W, H, _bar(VIOLET, BLUE))    # violet | blue
        write_rgb_png(self.right, W, H, _bar(VIOLET, BLUE))  # identical hues
        write_rgb_png(self.wrong, W, H, _bar(BLUE, BLUE))    # left zone collapsed to blue

    def test_right_hue_scores_higher_than_wrong(self):
        right = hue_zone_parity(self.ref, self.right)
        wrong = hue_zone_parity(self.ref, self.wrong)
        self.assertGreater(right, wrong)
        self.assertGreaterEqual(right, 0.9)   # near-perfect hue match
        self.assertLess(wrong, right)         # violet→blue zone drags it down

    def test_deterministic(self):
        self.assertEqual(hue_zone_parity(self.ref, self.right), hue_zone_parity(self.ref, self.right))


if __name__ == "__main__":
    unittest.main(verbosity=2)
