#!/usr/bin/env python3
"""Unit tests for Plan 1.3 Workstream B's Tier-1 diagnostics: silhouette_iou,
bilateral_symmetry_error, proportion_delta with golden values, independent of
forge/tests/test_pipeline.py per the plan's acceptance criteria.

Run: python3 forge/tests/test_tier1_diagnostics.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage4_review"))

from diagnose_render import (  # noqa: E402
    MASK_GRID_SIZE,
    aligned_silhouette_iou,
    bbox_of,
    bilateral_symmetry_error,
    color_is_gated,
    proportion_delta,
    silhouette_iou,
)


def rect_mask(x0, y0, w, h, size=MASK_GRID_SIZE):
    """Flat boolean grid with one filled rectangle -- the smallest shape that lets a
    translation be distinguished from a deformation."""
    mask = [False] * (size * size)
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            mask[y * size + x] = True
    return mask


class ColorGateByPassTest(unittest.TestCase):
    """Per-part color is a hard criterion only from material-pass onward.
    Clay passes (blockout/structural/form) must not be failed on color they
    deliberately lack (regression: teardrop-era blockout blocked by ΔE 56)."""

    def test_pre_material_passes_are_not_color_gated(self):
        for pid in ("blockout", "structural-pass", "form-refinement"):
            self.assertFalse(color_is_gated(pid), pid)

    def test_material_pass_and_later_are_color_gated(self):
        for pid in ("material-pass", "surface-pass", "lighting-pass",
                    "interaction-pass", "optimization-pass"):
            self.assertTrue(color_is_gated(pid), pid)

    def test_unknown_or_missing_pass_is_not_gated(self):
        self.assertFalse(color_is_gated(None))
        self.assertFalse(color_is_gated("not-a-real-pass"))


def make_mask(size: int, foreground_fn) -> list[bool]:
    return [foreground_fn(x, y, size) for y in range(size) for x in range(size)]


class SilhouetteIouTest(unittest.TestCase):
    def test_identical_masks_give_iou_one(self):
        mask = make_mask(10, lambda x, y, s: x < 5)
        self.assertEqual(silhouette_iou(mask, mask), 1.0)

    def test_disjoint_masks_give_iou_zero(self):
        left = make_mask(10, lambda x, y, s: x < 5)
        right = make_mask(10, lambda x, y, s: x >= 5)
        self.assertEqual(silhouette_iou(left, right), 0.0)

    def test_known_overlap_fraction(self):
        # Two identical NxN squares offset by N/2 in both axes: analytically-known
        # overlap fraction is 1/7 (area of intersection quadrant / union of the two
        # L-shaped squares). Using two half-size (N/2 x N/2) foreground blocks placed
        # so they overlap in exactly one N/2 x N/2 quadrant out of a 7-quadrant union.
        size = 8
        half = size // 2
        # Block A: top-left half x half. Block B: offset by half in both axes.
        block_a = make_mask(size, lambda x, y, s, h=half: x < h and y < h)
        block_b = make_mask(size, lambda x, y, s, h=half: h // 2 <= x < h // 2 + h and h // 2 <= y < h // 2 + h)
        iou = silhouette_iou(block_a, block_b)
        # intersection = (h/2)^2, each block area = h^2, union = 2*h^2 - (h/2)^2 = 7/4 h^2
        # iou = (h/2)^2 / (7/4 h^2) = (1/4) / (7/4) = 1/7
        self.assertAlmostEqual(iou, 1 / 7, delta=0.01)


class SymmetryErrorTest(unittest.TestCase):
    def test_perfectly_symmetric_mask_has_zero_error(self):
        size = 16
        # bilateral_symmetry_error mirrors x -> size - 1 - x, whose axis sits at
        # (size - 1) / 2 = 7.5 for size=16 — the foreground band must be centered
        # there (not at size // 2 = 8) to be genuinely symmetric under that exact
        # transform.
        mask = make_mask(size, lambda x, y, s: abs(x - (s - 1) / 2) < 3)
        self.assertAlmostEqual(bilateral_symmetry_error(mask, size=size), 0.0, delta=0.01)

    def test_maximally_asymmetric_mask_has_error_one(self):
        size = 16
        mask = make_mask(size, lambda x, y, s: x < s // 2)
        self.assertAlmostEqual(bilateral_symmetry_error(mask, size=size), 1.0, delta=0.01)


class ProportionDeltaTest(unittest.TestCase):
    def test_identical_bboxes_have_zero_deltas(self):
        delta = proportion_delta((0, 0, 100, 50), (0, 0, 100, 50))
        self.assertEqual(delta["aspect_ratio_delta"], 0.0)
        self.assertEqual(delta["scale_delta"], 0.0)

    def test_axis_scaled_bbox_has_known_aspect_ratio_delta(self):
        # Reference is 100x50 (AR=2.0); render is 100x100 (AR=1.0) -> delta = |2-1|/2 = 0.5
        delta = proportion_delta((0, 0, 100, 50), (0, 0, 100, 100))
        self.assertAlmostEqual(delta["aspect_ratio_delta"], 0.5, delta=0.001)


class BboxOfTest(unittest.TestCase):
    def test_bbox_matches_known_foreground_region(self):
        size = 20
        mask = make_mask(size, lambda x, y, s: 4 <= x < 10 and 2 <= y < 8)
        x0, y0, w, h = bbox_of(mask, size=size)
        self.assertEqual((x0, y0, w, h), (4, 2, 6, 6))


if __name__ == "__main__":
    unittest.main()


class AlignedSilhouetteIouTest(unittest.TestCase):
    """Translation-aligned IoU: `grimoire/review/self_correction.md` says to trust IoU only
    after scale+translation alignment, and nothing computed it. The point is not to rescue the
    gate -- it stays report-only -- but to tell a FRAMING error apart from a SHAPE error, because
    the two demand opposite fixes and a bare low IoU reads as 'the shape is wrong'.

    Regression source: a Talon reconstruction reported raw IoU 0.736 against 0.965 after a pure
    26px translation. The mesh was correct; the review camera was centring a subject the
    reference had placed off-centre."""

    def test_pure_translation_is_recovered_and_flagged_as_framing(self):
        reference = rect_mask(50, 50, 60, 40)
        render = rect_mask(50, 56, 60, 40)  # identical shape, shifted +6 in y
        raw = silhouette_iou(reference, render)
        result = aligned_silhouette_iou(reference, render)
        self.assertLess(raw, 0.85, "a 6-cell shift must depress the raw IoU")
        self.assertAlmostEqual(result["alignedIoU"], 1.0, places=4)
        self.assertEqual(result["offsetCells"], [0, -6])
        self.assertGreater(result["improvement"], 0.0)

    def test_identical_masks_report_no_offset_and_no_improvement(self):
        mask = rect_mask(40, 40, 50, 50)
        result = aligned_silhouette_iou(mask, mask)
        self.assertAlmostEqual(result["alignedIoU"], 1.0, places=4)
        self.assertEqual(result["offsetCells"], [0, 0])
        self.assertAlmostEqual(result["improvement"], 0.0, places=4)

    def test_a_genuine_shape_mismatch_is_not_rescued(self):
        """The whole value depends on this: if alignment flattered a real deformation, the
        message would misdirect the fix onto the camera."""
        reference = rect_mask(50, 50, 60, 40)
        render = rect_mask(50, 50, 20, 110)  # same area, wrong proportions
        result = aligned_silhouette_iou(reference, render)
        self.assertLess(result["alignedIoU"], 0.85)

    def test_empty_mask_does_not_raise(self):
        empty = [False] * (MASK_GRID_SIZE * MASK_GRID_SIZE)
        result = aligned_silhouette_iou(empty, rect_mask(10, 10, 5, 5))
        self.assertEqual(result["alignedIoU"], 0.0)
        self.assertEqual(result["offsetCells"], [0, 0])
