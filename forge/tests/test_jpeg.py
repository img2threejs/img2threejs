#!/usr/bin/env python3
"""Tests for the pure-stdlib baseline JPEG decoder.

The fixture is embedded rather than generated so these run identically on Linux
CI, where the macOS `sips` converter this decoder replaces does not exist.
"""

from __future__ import annotations

import base64
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "_shared"))
sys.path.insert(0, str(ROOT / "stage1_intake"))
sys.path.insert(0, str(ROOT / "stage4_review"))

from jpeg import UnsupportedJpeg, decode_jpeg, is_jpeg  # noqa: E402

# 32x32 baseline JPEG: left half red (200,30,40), right half blue (30,60,200).
FIXTURE_B64 = (
    "/9j/4AAQSkZJRgABAQAASABIAAD/4QBMRXhpZgAATU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAA"
    "A6ABAAMAAAABAAEAAKACAAQAAAABAAAAIKADAAQAAAABAAAAIAAAAAD/7QA4UGhvdG9zaG9wIDMu"
    "MAA4QklNBAQAAAAAAAA4QklNBCUAAAAAABDUHYzZjwCyBOmACZjs+EJ+/8AAEQgAIAAgAwEiAAIR"
    "AQMRAf/EAB8AAAEFAQEBAQEBAAAAAAAAAAABAgMEBQYHCAkKC//EALUQAAIBAwMCBAMFBQQEAAAB"
    "fQECAwAEEQUSITFBBhNRYQcicRQygZGhCCNCscEVUtHwJDNicoIJChYXGBkaJSYnKCkqNDU2Nzg5"
    "OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6g4SFhoeIiYqSk5SVlpeYmZqio6Slpqeo"
    "qaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk5ebn6Onq8fLz9PX29/j5+v/EAB8BAAMB"
    "AQEBAQEBAQEAAAAAAAABAgMEBQYHCAkKC//EALURAAIBAgQEAwQHBQQEAAECdwABAgMRBAUhMQYS"
    "QVEHYXETIjKBCBRCkaGxwQkjM1LwFWJy0QoWJDThJfEXGBkaJicoKSo1Njc4OTpDREVGR0hJSlNU"
    "VVZXWFlaY2RlZmdoaWpzdHV2d3h5eoKDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5"
    "usLDxMXGx8jJytLT1NXW19jZ2uLj5OXm5+jp6vLz9PX29/j5+v/bAEMAAgICAgICAwICAwUDAwMF"
    "BgUFBQUGCAYGBgYGCAoICAgICAgKCgoKCgoKCgwMDAwMDA4ODg4ODw8PDw8PDw8PD//bAEMBAgIC"
    "BAQEBwQEBxALCQsQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ"
    "EBAQEP/dAAQAAv/aAAwDAQACEQMRAD8A+R6KKK/Hz/VA4Oiiiv8AYw/xHP/Q+R6KKK/Hz/VA4Oii"
    "iv8AYw/xHP/Z"
)
FIXTURE = base64.b64decode("".join(FIXTURE_B64.split()))


class JpegDecoderTest(unittest.TestCase):
    def test_is_jpeg_detects_signature(self):
        self.assertTrue(is_jpeg(FIXTURE))
        self.assertFalse(is_jpeg(b"\x89PNG\r\n\x1a\n"))
        self.assertFalse(is_jpeg(b""))

    def test_decodes_expected_dimensions(self):
        width, height, pixels = decode_jpeg(FIXTURE)
        self.assertEqual((width, height), (32, 32))
        self.assertEqual(len(pixels), 32 * 32)
        self.assertTrue(all(len(p) == 4 and p[3] == 255 for p in pixels))

    def test_decodes_correct_colours(self):
        # JPEG is lossy, so assert the hue is right rather than exact bytes.
        width, _height, pixels = decode_jpeg(FIXTURE)
        left = pixels[16 * width + 6]
        right = pixels[16 * width + 26]
        self.assertGreater(left[0], 150, f"left half should read red, got {left}")
        self.assertLess(left[2], 100, f"left half should read red, got {left}")
        self.assertGreater(right[2], 150, f"right half should read blue, got {right}")
        self.assertLess(right[0], 100, f"right half should read blue, got {right}")

    def test_chroma_upsampling_places_the_colour_boundary_correctly(self):
        # The fixture is red|blue split exactly down the middle. Chroma is stored at
        # half resolution, so an incorrect upsampling scale still yields "red on the
        # left, blue on the right" while silently moving the boundary — sampling a
        # point well inside each half cannot detect that. Assert the position.
        width, _height, pixels = decode_jpeg(FIXTURE)
        row = [pixels[16 * width + x] for x in range(width)]
        red_majority = sum(1 for p in row if p[0] > p[2])
        self.assertEqual(
            red_majority, width // 2,
            f"colour boundary is misplaced; {red_majority} red px of {width}",
        )

    def test_rejects_non_jpeg(self):
        with self.assertRaises(ValueError):
            decode_jpeg(b"\x89PNG\r\n\x1a\nnot a jpeg")

    def test_progressive_raises_unsupported_not_garbage(self):
        # Flip the SOF0 marker to SOF2 so the decoder must refuse explicitly rather
        # than mis-decode; callers rely on UnsupportedJpeg to fall back cleanly.
        index = FIXTURE.find(b"\xff\xc0")
        self.assertGreater(index, 0, "fixture should contain a baseline SOF0 marker")
        progressive = FIXTURE[:index] + b"\xff\xc2" + FIXTURE[index + 2:]
        with self.assertRaises(UnsupportedJpeg):
            decode_jpeg(progressive)


class JpegPortabilityTest(unittest.TestCase):
    """The reason this decoder exists: every load_image() shelled out to macOS
    `sips` for non-PNG input, so a JPEG reference failed hard on Linux/Windows."""

    MODULES = [
        "extract_pbr_evidence",
        "extract_landmarks",
        "build_detail_inventory",
        "delight_albedo",
        "make_comparison_sheet",
    ]

    def test_every_load_image_reads_jpeg_without_sips(self):
        directory = Path(tempfile.mkdtemp())
        path = directory / "reference.jpg"
        path.write_bytes(FIXTURE)

        real_which = shutil.which
        shutil.which = lambda *a, **k: None  # simulate a machine with no sips
        try:
            for name in self.MODULES:
                module = __import__(name)
                with self.subTest(module=name):
                    result = module.load_image(path)
                    self.assertEqual(result[0], 32)
                    self.assertEqual(result[1], 32)
                    self.assertEqual(len(result[2]), 32 * 32)
        finally:
            shutil.which = real_which


if __name__ == "__main__":
    unittest.main(verbosity=2)
