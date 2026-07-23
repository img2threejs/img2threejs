#!/usr/bin/env python3
"""Divine Eye specular_wash (report-only). A saturated reference rendered
desaturated + hue-drifted toward cyan must be flagged; a hue-matched render must not. Stdlib.
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
from divine_eye import specular_wash  # noqa: E402


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


WHITE = (255, 255, 255)
W, H = 96, 48


def _solid(color):
    def fn(x, y):
        return WHITE if (y < 8 or y >= 40) else color
    return fn


class SpecularWashTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ref = self.d / "ref.png"
        self.washed = self.d / "washed.png"
        self.matched = self.d / "matched.png"
        write_rgb_png(self.ref, W, H, _solid((150, 70, 210)))     # saturated purple
        write_rgb_png(self.washed, W, H, _solid((150, 180, 200)))  # desaturated, drifted toward cyan
        write_rgb_png(self.matched, W, H, _solid((150, 70, 210)))  # same purple

    def test_washed_render_flagged(self):
        r = specular_wash(self.ref, self.washed)
        self.assertTrue(r["flagged"])
        self.assertLess(r["satRatio"], 0.6)
        self.assertTrue(r["towardCyan"])
        self.assertIsNotNone(r["advice"])

    def test_matched_render_not_flagged(self):
        r = specular_wash(self.ref, self.matched)
        self.assertFalse(r["flagged"])
        self.assertGreaterEqual(r["satRatio"], 0.95)


if __name__ == "__main__":
    unittest.main(verbosity=2)
