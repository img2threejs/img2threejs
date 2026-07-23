#!/usr/bin/env python3
"""Tests for the stdlib objectness proxy (OSIM-lite), Plan 1.3 task #18.

Proves the property the Divine Eye needed: structural similarity that is invariant to
background colour and absolute brightness (the photo-vs-procedural axes), while still
separating different shapes. Pure stdlib, zero token.

Run: python3 forge/tests/test_objectness.py
"""
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage4_review"))

from objectness import cosine, descriptor, objectness_similarity  # noqa: E402

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def write_png(path, w, h, pixel_fn):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            raw += bytes(pixel_fn(x, y))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    path.write_bytes(PNG_SIG + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def diag_bar(fg, bg):
    # a thick diagonal bar (same shape/orientation regardless of colours)
    def fn(x, y):
        return fg if abs((x - y)) < 40 else bg
    return fn


def horiz_bar(fg, bg):
    def fn(x, y):
        return fg if 90 <= y < 150 else bg
    return fn


class ObjectnessTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.W = self.H = 240
        # same diagonal shape, but opposite brightness AND opposite background
        self.dark_on_white = self.d / "dark_on_white.png"
        write_png(self.dark_on_white, self.W, self.H, diag_bar((40, 40, 44), (250, 250, 250)))
        self.bright_on_dark = self.d / "bright_on_dark.png"
        write_png(self.bright_on_dark, self.W, self.H, diag_bar((205, 205, 210), (18, 20, 26)))
        # a different shape (horizontal bar)
        self.horiz = self.d / "horiz.png"
        write_png(self.horiz, self.W, self.H, horiz_bar((40, 40, 44), (250, 250, 250)))

    def test_identical_descriptor_cosine_is_one(self):
        a = descriptor(self.dark_on_white)
        self.assertAlmostEqual(cosine(a, a), 1.0, places=6)

    def test_invariant_to_background_and_brightness(self):
        # SAME shape, opposite bg + opposite brightness -> must still score high
        # (this is exactly where SSIM/IoU/edge collapse for photo-vs-procedural)
        s = objectness_similarity(self.dark_on_white, self.bright_on_dark)
        self.assertGreater(s, 0.9, f"expected high objectness for same shape, got {s}")

    def test_separates_different_shapes(self):
        diag_vs_horiz = objectness_similarity(self.dark_on_white, self.horiz)
        same = objectness_similarity(self.dark_on_white, self.bright_on_dark)
        self.assertLess(diag_vs_horiz, same, "different shapes must score below same-shape")
        self.assertLess(diag_vs_horiz, 0.8, f"diagonal vs horizontal should be clearly lower, got {diag_vs_horiz}")

    def test_score_bounded(self):
        s = objectness_similarity(self.dark_on_white, self.horiz)
        self.assertGreaterEqual(s, 0.0)
        self.assertLessEqual(s, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
