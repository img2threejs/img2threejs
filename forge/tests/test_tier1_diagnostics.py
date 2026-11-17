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
    bbox_of,
    bilateral_symmetry_error,
    color_is_gated,
    proportion_delta,
    silhouette_iou,
)


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



class MaskIsRobustToStrayForeground(unittest.TestCase):
    """A bounding box is an extremal statistic, so one stray cell ruins it.

    Measured on a real review plate: a subject filling 24% of the grid reported a bbox of the whole
    224x224 grid, because the viewer's background gradient registered as foreground. Every
    proportion derived from that bbox was describing the background, and none of them moved when
    the camera did.
    """

    SIZE = 8

    def _mask(self, cells):
        mask = [False] * (self.SIZE * self.SIZE)
        for x, y in cells:
            mask[y * self.SIZE + x] = True
        return mask

    def test_a_stray_cell_is_dropped_from_the_bounding_box(self):
        from diagnose_render import bbox_of, largest_component

        blob = [(x, y) for x in range(2, 5) for y in range(2, 5)]
        with_stray = self._mask(blob + [(7, 7)])
        self.assertEqual(bbox_of(with_stray, self.SIZE), (2, 2, 6, 6))
        filtered, discarded = largest_component(with_stray, self.SIZE)
        self.assertEqual(bbox_of(filtered, self.SIZE), (2, 2, 3, 3))
        self.assertAlmostEqual(discarded, 1 / 10, places=6)

    def test_a_clean_mask_is_returned_unchanged(self):
        """Negative control: the filter must not alter a subject that is already one blob."""
        from diagnose_render import largest_component

        blob = self._mask([(x, y) for x in range(2, 5) for y in range(2, 5)])
        filtered, discarded = largest_component(blob, self.SIZE)
        self.assertEqual(filtered, blob)
        self.assertEqual(discarded, 0.0)

    def test_discarded_geometry_is_reported_rather_than_swallowed(self):
        from diagnose_render import largest_component

        big = [(x, y) for x in range(0, 4) for y in range(0, 4)]
        small = [(x, y) for x in range(6, 8) for y in range(6, 8)]
        _filtered, discarded = largest_component(self._mask(big + small), self.SIZE)
        self.assertAlmostEqual(discarded, 4 / 20, places=6)

    def test_an_empty_mask_does_not_divide_by_zero(self):
        from diagnose_render import largest_component

        filtered, discarded = largest_component([False] * (self.SIZE * self.SIZE), self.SIZE)
        self.assertEqual(sum(filtered), 0)
        self.assertEqual(discarded, 0.0)


class PerPartColorPresenceTest(unittest.TestCase):
    """A small coloured fitting can never own one of five global clusters. The cluster-only test
    failed every multi-material object with one (measured: navigation lights at delta-E 80+ while
    every real material sat at <= 2). Presence and 'unmeasured' fix that without weakening a real
    mismatch on a large part."""

    @staticmethod
    def _write_png(path, w, h, fn):
        import struct
        import zlib

        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        raw = bytearray()
        for y in range(h):
            raw.append(0)
            for x in range(w):
                raw.extend(fn(x, y))
        ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                         + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))

    def _render(self, tmp):
        def px(x, y):
            if 18 <= x < 21 and 18 <= y < 21:
                return (40, 90, 230)  # 9 px blue fitting -> present
            if 25 <= x < 27 and 25 <= y < 27:
                return (210, 30, 30)  # 4 px red fitting -> below PRESENCE_MIN_PIXELS
            if 5 <= x < 35 and 5 <= y < 35:
                # five large materials take every global cluster, as on a real multi-material render
                bands = [(60, 62, 66), (120, 80, 40), (200, 200, 90), (90, 40, 120), (30, 120, 120)]
                return bands[min(4, (y - 5) // 6)]
            return (255, 255, 255)

        path = Path(tmp) / "render.png"
        self._write_png(path, 40, 40, px)
        return path

    def test_statuses(self):
        import tempfile

        from diagnose_render import per_part_color_delta

        with tempfile.TemporaryDirectory() as tmp:
            render = self._render(tmp)
            recipes = [
                {"componentId": "hull", "dominantAlbedo": "rgba(58, 62, 66, 1.0)", "extent": 10.0},
                {"componentId": "light-bar", "dominantAlbedo": "rgba(40, 90, 230, 1.0)", "extent": 1.2},
                {"componentId": "nav-light", "dominantAlbedo": "rgba(210, 30, 30, 1.0)", "extent": 0.1},
                {"componentId": "big-wrong", "dominantAlbedo": "rgba(30, 190, 70, 1.0)", "extent": 5.0},
            ]
            report = per_part_color_delta(recipes, render)
        status = {e["componentId"]: e["status"] for e in report["perComponent"]}
        self.assertEqual(status["hull"], "matched")
        self.assertEqual(status["light-bar"], "present")
        self.assertEqual(status["nav-light"], "unmeasured")
        self.assertEqual(status["big-wrong"], "mismatch")
        self.assertEqual(report["unmeasured"], ["nav-light"])
        # a large part with an absent colour still fails the gate
        self.assertGreater(report["maxDeltaE"], 20.0)

    def test_unmeasured_is_excluded_from_max_but_listed(self):
        import tempfile

        from diagnose_render import per_part_color_delta

        with tempfile.TemporaryDirectory() as tmp:
            render = self._render(tmp)
            report = per_part_color_delta([
                {"componentId": "hull", "dominantAlbedo": "rgba(58, 62, 66, 1.0)", "extent": 10.0},
                {"componentId": "nav-light", "dominantAlbedo": "rgba(210, 30, 30, 1.0)", "extent": 0.1},
            ], render)
        self.assertLessEqual(report["maxDeltaE"], 20.0)
        self.assertEqual(report["unmeasured"], ["nav-light"])
        self.assertGreater(report["maxClusterDeltaE"], 20.0)

    def test_without_extent_an_absent_colour_is_a_mismatch(self):
        import tempfile

        from diagnose_render import per_part_color_delta

        with tempfile.TemporaryDirectory() as tmp:
            render = self._render(tmp)
            report = per_part_color_delta([
                {"componentId": "hull", "dominantAlbedo": "rgba(58, 62, 66, 1.0)"},
                {"componentId": "nav-light", "dominantAlbedo": "rgba(210, 30, 30, 1.0)"},
            ], render)
        statuses = [e["status"] for e in report["perComponent"]]
        self.assertIn(statuses[0], {"matched", "present"})  # k=2 may merge the grey with a neighbour band
        self.assertEqual(statuses[1], "mismatch")  # no extent -> never excused as 'unmeasured'


if __name__ == "__main__":
    unittest.main()
