#!/usr/bin/env python3
"""Tests for stage1_intake/detect_reference_effects.py (pure stdlib, unittest).

Synthetic RGB PNGs are written with the verbatim helper below. Images are kept
comfortably above the shared segmenter's tiny-mask floor: build_foreground_mask
INVERTS any mask covering < 3.5% of the frame to "all foreground", so every
subject here is ~5-25% of the frame.
"""

import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "stage1_intake"))
from detect_reference_effects import (  # noqa: E402
    detect_background_blur,
    detect_highlight_glow,
    recommend_effects,
)


# --- verbatim PNG helper (copied as instructed) --------------------------- #
import struct, zlib
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
def write_rgb_png(path, w, h, pixel_fn):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            raw += bytes(pixel_fn(x, y, w, h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    path.write_bytes(PNG_SIGNATURE + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
# -------------------------------------------------------------------------- #


class ReferenceEffectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _sharp_scene(self, x, y, w, h):
        # Colored (red) checkerboard subject block on a sharp grayscale
        # checkerboard background. The red block is caught by the saturation
        # path of the mask (coverage ~25%); the background stays highly
        # textured so its gradient energy is NOT far below the subject.
        checker = (x + y) % 2 == 0
        in_subject = 30 <= x < 90 and 30 <= y < 90
        if in_subject:
            return (210, 50, 50) if checker else (110, 20, 20)
        return (200, 200, 200) if checker else (60, 60, 60)

    def _shallow_dof(self, x, y, w, h):
        # High-contrast grayscale checkerboard subject block (~25% coverage)
        # on a perfectly FLAT uniform background (zero gradient energy).
        in_subject = 30 <= x < 90 and 30 <= y < 90
        if in_subject:
            return (200, 200, 200) if (x + y) % 2 == 0 else (20, 20, 20)
        return (120, 120, 120)

    def _flat_gray(self, x, y, w, h):
        return (120, 120, 120)

    def _glow(self, x, y, w, h):
        # Concentric brightness: small white core (luma > 0.92) surrounded by a
        # gradual gray halo (~0.7 luma) fading to a dark background.
        dx = x - w // 2
        dy = y - h // 2
        dd = dx * dx + dy * dy
        if dd <= 9:            # core radius 3 -> ~0.8% hot pixels on a 60x60 frame
            return (250, 250, 250)
        if dd <= 81:           # halo radius 4..9
            return (180, 180, 180)
        return (30, 30, 30)

    def test_sharp_scene_no_blur(self):
        path = self.tmp / "sharp.png"
        write_rgb_png(path, 120, 120, self._sharp_scene)
        result = detect_background_blur(path)
        self.assertFalse(result["blurred"], result["reason"])

    def test_shallow_dof_flagged(self):
        path = self.tmp / "dof.png"
        write_rgb_png(path, 120, 120, self._shallow_dof)
        result = detect_background_blur(path)
        self.assertTrue(result["blurred"], result["reason"])
        self.assertLess(result["ratio"], 0.5)

    def test_no_glow_on_flat_image(self):
        path = self.tmp / "flat.png"
        write_rgb_png(path, 60, 60, self._flat_gray)
        result = detect_highlight_glow(path)
        self.assertFalse(result["glow"], result["reason"])

    def test_glow_flagged(self):
        path = self.tmp / "glow.png"
        write_rgb_png(path, 60, 60, self._glow)
        result = detect_highlight_glow(path)
        self.assertTrue(result["glow"], result["reason"])
        self.assertGreater(result["hotFraction"], 0.005)
        self.assertGreater(result["haloFraction"], 0.5)

    def test_recommend_effects_shape(self):
        path = self.tmp / "shape.png"
        write_rgb_png(path, 120, 120, self._sharp_scene)
        result = recommend_effects(path)
        for key in ("dof", "bloom", "blur", "glow"):
            self.assertIn(key, result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
