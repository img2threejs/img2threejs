#!/usr/bin/env python3
"""Tests for the Divine Eye calibration harness (Plan 1.3 §5). Synthetic pairs, zero token."""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "stage4_review"))

from calibrate_eye import calibrate, run_corpus, separation  # noqa: E402

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
    path.write_bytes(PNG_SIGNATURE + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def block(x0, y0, x1, y1):
    return lambda x, y, w, h: (40, 40, 40) if (x0 <= x < x1 and y0 <= y < y1) else (255, 255, 255)


class CalibrateEyeTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.ref = self.dir / "ref.png"
        write_rgb_png(self.ref, 200, 200, block(50, 50, 150, 150))
        # good render == reference (fidelity 1.0, pass)
        self.good = self.dir / "good.png"
        write_rgb_png(self.good, 200, 200, block(50, 50, 150, 150))
        # bad render: subject collapsed to a small block (hard-gate reject)
        self.bad = self.dir / "bad.png"
        write_rgb_png(self.bad, 200, 200, block(92, 92, 108, 108))

    def _corpus(self):
        return [
            {"reference": str(self.ref), "render": str(self.good), "label": "good"},
            {"reference": str(self.ref), "render": str(self.bad), "label": "bad"},
        ]

    def test_run_corpus_collects_per_label(self):
        c = run_corpus(self._corpus())
        self.assertEqual(c["signalStats"]["good"]["count"], 1)
        self.assertEqual(c["signalStats"]["bad"]["count"], 1)
        self.assertGreater(c["signalStats"]["good"]["fidelityMean"],
                           c["signalStats"]["bad"]["fidelityMean"])

    def test_clean_separation_and_acceptable(self):
        result = calibrate(self._corpus())
        sep = result["separation"]
        self.assertTrue(sep["cleanSeparation"])
        self.assertTrue(sep["allGoodPass"])
        self.assertTrue(sep["allBadRejected"])
        self.assertTrue(sep["corpusAcceptable"])
        self.assertIsNotNone(sep["suggestedThreshold"])

    def test_corpus_needs_both_classes(self):
        good_only = [{"reference": str(self.ref), "render": str(self.good), "label": "good"}]
        sep = separation(run_corpus(good_only)["rows"])
        self.assertFalse(sep["corpusAcceptable"])
        self.assertIn("warning", sep)

    def test_report_only_flag_set(self):
        result = calibrate(self._corpus())
        self.assertTrue(result["reportOnly"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
