#!/usr/bin/env python3
"""Tests for the Divine Eye deterministic ensemble (Plan 1.3 Phase 3 §3.1/§3.3)."""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "stage4_review"))

from divine_eye import (  # noqa: E402
    blowout_parity,
    edge_overlap,
    evaluate,
    flat_fraction,
    global_ssim,
    tonal_parity,
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
    path.write_bytes(PNG_SIGNATURE + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def block(x0, y0, x1, y1, fg=(200, 40, 40), bg=(255, 255, 255)):
    return lambda x, y, w, h: fg if (x0 <= x < x1 and y0 <= y < y1) else bg


class SignalUnitTest(unittest.TestCase):
    def test_ssim_identical_is_one(self):
        a = [0.2, 0.8, 0.5, 0.1] * 16
        self.assertAlmostEqual(global_ssim(a, a), 1.0, places=5)

    def test_ssim_different_is_low(self):
        a = [0.0] * 64
        b = [1.0 if i % 2 else 0.0 for i in range(64)]
        self.assertLess(global_ssim(a, b), 0.5)

    def test_tonal_parity_identical_is_one(self):
        a = [i / 64 for i in range(64)]
        self.assertAlmostEqual(tonal_parity(a, a), 1.0, places=5)

    def test_tonal_parity_disjoint_is_low(self):
        dark = [0.02] * 64
        bright = [0.98] * 64
        self.assertLess(tonal_parity(dark, bright), 0.1)

    def test_blowout_parity_penalizes_extra_blown(self):
        ref = [0.5] * 64
        blown = [0.99] * 64
        self.assertLess(blowout_parity(ref, blown), 0.1)
        self.assertAlmostEqual(blowout_parity(ref, ref), 1.0, places=5)

    def test_flat_fraction_high_for_uniform(self):
        uniform = [0.5] * (16 * 16)
        self.assertGreater(flat_fraction(uniform, 16), 0.9)

    def test_edge_overlap_identical_is_one(self):
        # a vertical edge in the middle
        size = 32
        img = [0.0 if (i % size) < size // 2 else 1.0 for i in range(size * size)]
        self.assertAlmostEqual(edge_overlap(img, img, size), 1.0, places=5)


class DivineEyeIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.ref = self.dir / "ref.png"
        write_rgb_png(self.ref, 200, 200, block(50, 50, 150, 150))

    def test_identical_passes_with_full_fidelity(self):
        r = evaluate(self.ref, self.ref)
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(r["action"], "continue")
        self.assertEqual(r["fidelity"], 1.0)
        self.assertEqual(r["hardGateFailures"], [])

    def test_wrong_scale_trips_hard_gate(self):
        # a much smaller subject → scale/IoU hard gate → reject + refine-code
        ren = self.dir / "small.png"
        write_rgb_png(ren, 200, 200, block(90, 90, 110, 110))
        r = evaluate(self.ref, ren)
        self.assertEqual(r["verdict"], "reject")
        self.assertEqual(r["action"], "refine-code")
        self.assertTrue(r["hardGateFailures"])

    def test_shifted_same_shape_is_rescued_by_objectness(self):
        # subject moved to a corner → low IoU hard gate, BUT it is the same (square)
        # shape → objectness (bg/pose/scale-invariant) recognises it → reconstruction-mode
        # rescue downgrades the confident reject to a probe (still not a pass).
        ren = self.dir / "shifted.png"
        write_rgb_png(ren, 200, 200, block(0, 0, 60, 60))
        r = evaluate(self.ref, ren)
        self.assertTrue(r["hardGateFailures"])          # IoU still trips
        self.assertTrue(r["reconstructionModeSuspected"])
        self.assertEqual(r["action"], "probe")          # rescued, NOT refine-code
        self.assertNotEqual(r["verdict"], "pass")       # never auto-passes

    def test_different_shape_iou_fail_is_not_rescued(self):
        # a genuinely different shape (thin bar vs square) → low objectness → NO rescue,
        # stays a hard reject. Guards the rescue from masking real geometric failures.
        ren = self.dir / "wrongshape.png"
        write_rgb_png(ren, 200, 200, block(10, 92, 190, 108))  # thin horizontal bar
        r = evaluate(self.ref, ren)
        self.assertTrue(r["hardGateFailures"])
        self.assertFalse(r["reconstructionModeSuspected"])
        self.assertEqual(r["action"], "refine-code")

    def test_asymmetric_subject_not_penalized_when_matched(self):
        # an asymmetric L-shape compared to itself must still score fidelity 1.0
        # (symmetry is a parity signal, not an absolute one).
        def lshape(x, y, w, h):
            if (20 <= x < 60 and 20 <= y < 160) or (20 <= x < 140 and 120 <= y < 160):
                return (30, 120, 90)
            return (255, 255, 255)
        aref = self.dir / "l.png"
        write_rgb_png(aref, 180, 180, lshape)
        r = evaluate(aref, aref)
        self.assertEqual(r["fidelity"], 1.0)
        self.assertEqual(r["verdict"], "pass")


if __name__ == "__main__":
    unittest.main(verbosity=2)
