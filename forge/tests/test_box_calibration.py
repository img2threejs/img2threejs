#!/usr/bin/env python3
"""Contract tests for box-object self-calibration.

Fixture: a tank-proportioned box (hull 6.3 x 2.9 x 3.7 -> gauge ratios
1 : 0.4603 : 0.5873) rendered through a known camera in the solver's pixel
convention. Every expected value is exact by construction.
"""

from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from forge.stage1_intake import box_calibration as bc  # noqa: E402

W, H = 1536, 691
F_TRUE, P_TRUE = 900.0, (760.0, 410.0)
DIMS = (6.3, 2.9, 3.7)          # x (hull length), y (height), z (width)


def make_fixture(yaw_deg=34.0, pitch_deg=-6.0, *, noise=0.0, seed=3,
                 drop_family=None, distance=16.0):
    rng = random.Random(seed)
    jit = lambda v: v + rng.uniform(-noise, noise)  # noqa: E731
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)

    def unit(v):
        n = math.sqrt(sum(x * x for x in v))
        return tuple(x / n for x in v)

    def cross(a, b):
        return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0])

    look = (math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch))
    right = unit(cross(look, (0, 1, 0)))
    down = cross(look, right)
    R = (right, down, look)              # world -> camera (rows)

    centre_cam = (0.0, 0.0, distance)

    def corner_cam(i, j, k):
        wpt = ((i - 0.5) * DIMS[0], (j - 0.5) * DIMS[1], (k - 0.5) * DIMS[2])
        c = tuple(sum(R[r][m] * wpt[m] for m in range(3)) for r in range(3))
        return tuple(c[r] + centre_cam[r] for r in range(3))

    def project(c):
        return (P_TRUE[0] + F_TRUE * c[0] / c[2], P_TRUE[1] + F_TRUE * c[1] / c[2])

    fams = {"x": [], "y": [], "z": []}
    for axis, idx in (("x", 0), ("y", 1), ("z", 2)):
        for j in (0, 1):
            for k in (0, 1):
                lat0, lat1 = [0, j, k], [0, j, k]
                lat0[idx], lat1[idx] = 0, 1
                a = project(corner_cam(*lat0))
                b = project(corner_cam(*lat1))
                fams[axis].append([jit(a[0]), jit(a[1]), jit(b[0]), jit(b[1])])
    if drop_family:
        del fams[drop_family]

    corners = {}
    for i in (0, 1):
        for j in (0, 1):
            for k in (0, 1):
                if i + j + k == 0:          # hide one corner, like a real photo
                    continue
                q = project(corner_cam(i, j, k))
                corners[f"c{i}{j}{k}"] = {"px": [jit(q[0]), jit(q[1])], "at": [i, j, k]}
    return {"image": {"width": W, "height": H}, "edgeFamilies": fams, "corners": corners}


class ThreeFamilyCalibration(unittest.TestCase):
    def test_recovers_focal_and_principal_point(self):
        m = make_fixture()
        cal = bc.calibrate(m["edgeFamilies"], (W, H))
        self.assertEqual(cal["route"], "three-family orthocentre")
        self.assertLess(abs(cal["focalPx"] - F_TRUE) / F_TRUE, 0.01)
        self.assertLess(abs(cal["principalPoint"][0] - P_TRUE[0]), 3.0)
        self.assertLess(abs(cal["principalPoint"][1] - P_TRUE[1]), 3.0)
        self.assertEqual(cal["verdict"], "pass")

    def test_dimension_ratios_from_corners(self):
        m = make_fixture()
        cal = bc.calibrate(m["edgeFamilies"], (W, H))
        box = bc.fit_box(cal, m["corners"])
        self.assertLess(abs(box["dimensions"]["y"] - DIMS[1] / DIMS[0]), 0.01)
        self.assertLess(abs(box["dimensions"]["z"] - DIMS[2] / DIMS[0]), 0.01)
        self.assertLess(box["meanCornerResidualPx"], 1.0)

    def test_survives_pixel_noise(self):
        m = make_fixture(noise=0.7)
        cal = bc.calibrate(m["edgeFamilies"], (W, H))
        box = bc.fit_box(cal, m["corners"])
        self.assertLess(abs(box["dimensions"]["y"] - DIMS[1] / DIMS[0]), 0.05)
        self.assertLess(abs(box["dimensions"]["z"] - DIMS[2] / DIMS[0]), 0.05)

    def test_known_dimension_gives_metres(self):
        m = make_fixture()
        cal = bc.calibrate(m["edgeFamilies"], (W, H))
        box = bc.fit_box(cal, m["corners"])
        scale = bc.apply_scale(box, {"axis": "x", "metres": 6.3})
        self.assertLess(abs(scale["dimensionsMetres"]["y"] - 2.9), 0.05)
        self.assertLess(abs(scale["dimensionsMetres"]["z"] - 3.7), 0.05)


class TwoFamilyCalibration(unittest.TestCase):
    def test_level_camera_without_vertical_family(self):
        # pitch 0 -> the y family is image-parallel; drop it entirely and the
        # two-family route must still recover f and the ratios.
        m = make_fixture(pitch_deg=0.0, drop_family="y")
        cal = bc.calibrate(m["edgeFamilies"], (W, H))
        self.assertTrue(cal["route"].startswith("two-family"))
        self.assertTrue(any("ASSUMED" in n for n in cal["notes"]))
        self.assertLess(abs(cal["focalPx"] - F_TRUE) / F_TRUE, 0.02)
        box = bc.fit_box(cal, m["corners"])
        self.assertLess(abs(box["dimensions"]["y"] - DIMS[1] / DIMS[0]), 0.03)

    def test_single_family_is_refused_with_routing_advice(self):
        m = make_fixture(drop_family="y")
        del m["edgeFamilies"]["z"]
        with self.assertRaises(ValueError) as ctx:
            bc.calibrate(m["edgeFamilies"], (W, H))
        self.assertIn("cannot self-calibrate", str(ctx.exception))


class CornerGates(unittest.TestCase):
    def test_bad_corner_is_named(self):
        m = make_fixture()
        cal = bc.calibrate(m["edgeFamilies"], (W, H))
        m["corners"]["c111"]["px"][0] += 25.0
        box = bc.fit_box(cal, m["corners"])
        self.assertEqual(box["perCorner"][0]["corner"], "c111")
        self.assertTrue(any("'c111'" in d for d in box["diagnosis"]))

    def test_underconstrained_corners_are_refused(self):
        m = make_fixture()
        cal = bc.calibrate(m["edgeFamilies"], (W, H))
        few = dict(list(m["corners"].items())[:3])
        with self.assertRaises(ValueError):
            bc.fit_box(cal, few)


if __name__ == "__main__":
    unittest.main(verbosity=2)
