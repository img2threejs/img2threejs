#!/usr/bin/env python3
"""Tests for Plan 1.3 §4 Blum-medial-axis lathe-profile derivation (Phase 2)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "stage2_spec"))

from derive_geometry import derive_lathe_profile  # noqa: E402


def circle_mask(size: int, r: float):
    c = (size - 1) / 2.0
    return [((x - c) ** 2 + (y - c) ** 2) <= r * r for y in range(size) for x in range(size)]


def rect_mask(w: int, h: int, mx: int):
    # a vertical bar of constant half-width mx, centered
    cx = w // 2
    return [abs(x - cx) <= mx for y in range(h) for x in range(w)]


def triangle_mask(w: int, h: int):
    # point at top (y=0), widening to full at bottom (y=h-1)
    cx = w // 2
    mask = []
    for y in range(h):
        half = int((y / (h - 1)) * (cx - 1))
        for x in range(w):
            mask.append(abs(x - cx) <= half)
    return mask


class LatheProfileTest(unittest.TestCase):
    def test_circle_radius_peaks_in_middle(self):
        size = 64
        mask = circle_mask(size, 28)
        prof = derive_lathe_profile(mask, size, size, samples=15)
        radii = [p[0] for p in prof["points"]]
        mid = radii[len(radii) // 2]
        self.assertGreater(mid, radii[0])
        self.assertGreater(mid, radii[-1])
        self.assertLess(radii[0], 0.15)  # near the poles the radius is ~0
        self.assertLess(radii[-1], 0.15)

    def test_rectangle_radius_roughly_constant(self):
        w, h = 40, 80
        mask = rect_mask(w, h, 8)
        prof = derive_lathe_profile(mask, w, h, samples=15)
        # drop first/last (endpoints can clip) and check the middle band is stable
        mids = [p[0] for p in prof["points"][3:-3]]
        self.assertTrue(mids)
        self.assertLess(max(mids) - min(mids), 0.03, mids)

    def test_triangle_radius_increases_top_to_bottom(self):
        w, h = 60, 80
        mask = triangle_mask(w, h)
        prof = derive_lathe_profile(mask, w, h, samples=15)
        radii = [p[0] for p in prof["points"]]
        # top (axisPos -0.5) narrow → bottom (+0.5) wide
        self.assertLess(radii[0], radii[-1])
        self.assertEqual(prof["axis"], "vertical")

    def test_axis_picks_longer_dimension(self):
        # a subject whose bbox is WIDER than tall → horizontal revolve axis.
        w, h = 80, 40
        cy = h // 2
        mask = [abs(y - cy) <= 6 for y in range(h) for x in range(w)]  # wide horizontal bar
        prof = derive_lathe_profile(mask, w, h, samples=10)
        self.assertEqual(prof["axis"], "horizontal")

    def test_axis_positions_span_normalized_range(self):
        size = 40
        mask = circle_mask(size, 16)
        prof = derive_lathe_profile(mask, size, size, samples=9)
        positions = [p[1] for p in prof["points"]]
        self.assertAlmostEqual(positions[0], -0.5, delta=0.05)
        self.assertAlmostEqual(positions[-1], 0.5, delta=0.05)


if __name__ == "__main__":
    unittest.main(verbosity=2)
