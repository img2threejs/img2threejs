#!/usr/bin/env python3
"""Verify CIEDE2000 against Sharma et al.'s canonical published test pairs (tolerance 1e-3).
Pure stdlib, zero token.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))
from color_metrics import ciede2000, delta_e_rgb  # noqa: E402

# (lab1, lab2, expected ΔE00) — Sharma/Wu/Dalal CIEDE2000 verification data.
SHARMA = [
    ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
    ((50.0000, 3.1571, -77.2803), (50.0000, 0.0000, -82.7485), 2.8615),
    ((50.0000, 2.8361, -74.0200), (50.0000, 0.0000, -82.7485), 3.4412),
    ((50.0000, -1.3802, -84.2814), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, -1.1848, -84.8006), (50.0000, 0.0000, -82.7485), 1.0000),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((63.0109, -31.0961, -5.8663), (62.8187, -29.7946, -4.0864), 1.2630),
    ((35.0831, -44.1164, 3.7933), (35.0232, -40.0716, 1.5901), 1.8645),
    ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
]


class ColorMetricsTest(unittest.TestCase):
    def test_ciede2000_matches_sharma_reference(self):
        for lab1, lab2, expected in SHARMA:
            got = ciede2000(lab1, lab2)
            self.assertAlmostEqual(got, expected, places=3,
                                   msg=f"ΔE00{lab1}->{lab2}: got {got:.4f}, expected {expected}")

    def test_symmetry_and_identity(self):
        a = (50.0, 2.6772, -79.7751)
        b = (50.0, 0.0, -82.7485)
        self.assertAlmostEqual(ciede2000(a, a), 0.0, places=6)
        self.assertAlmostEqual(ciede2000(a, b), ciede2000(b, a), places=6)

    def test_delta_e_rgb_same_colour_zero(self):
        self.assertAlmostEqual(delta_e_rgb((120, 40, 200), (120, 40, 200)), 0.0, places=6)

    def test_same_hue_zone_threshold(self):
        # two close blues within the ~2.3 "same hue zone"; a blue vs orange far outside it
        near = delta_e_rgb((46, 108, 236), (50, 112, 232))
        far = delta_e_rgb((46, 108, 236), (236, 140, 40))
        self.assertLess(near, 3.0)
        self.assertGreater(far, 20.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
