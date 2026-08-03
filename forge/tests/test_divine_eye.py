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
from diagnose_render import mask_is_inverted, silhouette_iou  # noqa: E402

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
        # SAME-SIZE square translated to a corner (100x100 → 100x100, area & aspect identical) →
        # only the IoU hard gate trips (scale/aspect pass), and objectness (bg/pose/scale-invariant)
        # recognises the same shape → reconstruction-mode rescue. Soft fidelity is well below target
        # (misaligned pixels tank ssim/edge), so it routes to probe, never an auto-pass.
        ren = self.dir / "shifted.png"
        write_rgb_png(ren, 200, 200, block(0, 0, 100, 100))
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


class DegenerateEvidenceTest(unittest.TestCase):
    """Absence of evidence must never score as agreement.

    build_foreground_mask falls back to whole-frame coverage when the keyed subject is
    under 3.5% of the frame. Two unrelated small subjects therefore both invert to a full
    frame and compare as an exact match; an empty union scored 1.0 compounded it.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_silhouette_iou_empty_union_is_zero(self):
        # two failed captures are not a perfect match
        self.assertEqual(silhouette_iou([False] * 16, [False] * 16), 0.0)

    def test_silhouette_iou_still_correct_when_populated(self):
        self.assertEqual(silhouette_iou([True, True, False, False], [True, True, False, False]), 1.0)
        self.assertEqual(silhouette_iou([True, False, False, False], [False, True, False, False]), 0.0)

    def test_edge_overlap_no_edges_is_zero(self):
        # a flat image pair has no edges anywhere; that is missing evidence, not agreement,
        # and it used to feed a free 1.0 into the weighted fidelity ensemble.
        flat = [0.5] * 64
        self.assertEqual(edge_overlap(flat, flat, 8), 0.0)

    def test_mask_is_inverted_detects_the_tiny_coverage_fallback(self):
        self.assertTrue(mask_is_inverted(["foreground mask is tiny; material extraction is unreliable"]))
        self.assertFalse(mask_is_inverted([]))
        self.assertFalse(mask_is_inverted(["image is not clearly isolated from background"]))

    def test_disjoint_tiny_objects_are_not_a_perfect_match(self):
        # THE regression: 30x30 subjects at opposite corners of a 200x200 frame share zero
        # pixels. Each is 2.25% coverage, so both masks invert to the full frame and the
        # silhouette compares as identical. This must be a hard failure, not a pass.
        ref = self.dir / "corner_a.png"
        ren = self.dir / "corner_b.png"
        write_rgb_png(ref, 200, 200, block(10, 10, 40, 40, fg=(220, 30, 30)))
        write_rgb_png(ren, 200, 200, block(160, 160, 190, 190, fg=(30, 30, 220)))
        r = evaluate(ref, ren)
        self.assertTrue(r["hardGateFailures"], "disjoint subjects must trip a hard gate")
        self.assertNotEqual(r["verdict"], "pass")
        self.assertTrue(r["maskWarnings"], "the mask inversion must be reported, not discarded")

    def test_same_shape_at_different_positions_still_fails(self):
        # Identical 28x28 subjects at opposite corners: shape, colour and size all match, so
        # every non-silhouette signal agrees. Only the inversion gate separates this from a
        # genuine match, which is why it must be a hard failure rather than a soft penalty.
        ref = self.dir / "tiny_a.png"
        ren = self.dir / "tiny_b.png"
        write_rgb_png(ref, 200, 200, block(20, 20, 48, 48, fg=(200, 40, 40)))
        write_rgb_png(ren, 200, 200, block(150, 150, 178, 178, fg=(200, 40, 40)))
        r = evaluate(ref, ren)
        self.assertTrue(r["hardGateFailures"])
        self.assertEqual(r["action"], "refine-code")


if __name__ == "__main__":
    unittest.main(verbosity=2)
