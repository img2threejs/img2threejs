#!/usr/bin/env python3
"""Contract tests for the scene camera solver and its honesty gates.

The fixture is a synthetic camera in the same pixel convention as the solver
(x right, y down, z forward), so every expected value is exact by construction.
Two regression cases are lifted from the reconstruction that motivated this
profile (a film-still interior, 1536x691): the 90px uniform reprojection shift
caused by an off-centre principal point, and the 3-VP focal collapse under
sub-pixel noise.
"""

from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from forge.stage1_intake import scene_camera as sc  # noqa: E402
from forge.stage1_intake import scene_backproject as sb  # noqa: E402
from forge.stage1_intake import scene_unit_gate as su  # noqa: E402

W, H = 1536, 691
F_TRUE, P_TRUE, H_TRUE = 772.0, (760.0, 430.0), 1.25


def camera_looking(yaw_deg: float, pitch_deg: float) -> sc.SceneCamera:
    """Ground-truth camera in the OpenCV pixel convention: rows = [right; down; look]."""
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    look = (math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch))

    def unit(v):
        n = math.sqrt(sum(x * x for x in v))
        return tuple(x / n for x in v)

    def cross(a, b):
        return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

    right = unit(cross(look, (0, 1, 0)))
    down = cross(look, right)
    return sc.SceneCamera(F_TRUE, P_TRUE, (right, down, look), H_TRUE, (W, H))


def synthetic_measurements(ct: sc.SceneCamera, *, noise: float = 0.0,
                           verticals: int = 0, seed: int = 7) -> dict:
    rng = random.Random(seed)
    jit = lambda v: v + rng.uniform(-noise, noise)  # noqa: E731

    def clipped(fn):
        inside = []
        for i in range(500):
            t = -16 + 32 * i / 499
            q = ct.project(fn(t))
            if q and 0 <= q[0] < W and 0 <= q[1] < H:
                inside.append(q)
        if len(inside) < 40:
            return None
        a, b = inside[0], inside[-1]
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 60:
            return None
        return [jit(a[0]), jit(a[1]), jit(b[0]), jit(b[1])]

    fam_a = [s for k in range(-16, 17) if (s := clipped(lambda t, k=k: (k * 1.0, 0.0, t)))]
    fam_b = [s for k in range(-16, 17) if (s := clipped(lambda t, k=k: (t, 0.0, k * 1.0)))]
    m = {"image": {"width": W, "height": H},
         "floorFamilies": [{"name": "A", "segments": fam_a},
                           {"name": "B", "segments": fam_b}],
         "pitchRows": [H * 0.80, H * 0.94]}
    if verticals:
        v = []
        for x in range(-8, 9):
            for z in (3, 5, 7, 9, 12):
                a = ct.project((x, 0.1, z))
                b = ct.project((x, 2.6, z))
                if a and b and all(0 <= q[0] < W and 0 <= q[1] < H for q in (a, b)):
                    v.append([jit(a[0]), jit(a[1]), jit(b[0]), jit(b[1])])
            if len(v) >= verticals:
                break
        m["verticalSegments"] = v[:verticals]
    return m


def floor_error(ct: sc.SceneCamera, cam: sc.SceneCamera) -> float:
    errs = []
    for v in range(470, H, 10):
        for u in range(30, W, 50):
            t = ct.floor(u, v)
            b = cam.floor(u, v)
            if t and b:
                errs.append(math.hypot(b[0] - t[0], b[1] - t[1]) / max(1.0, math.hypot(*t)))
    return max(errs)


class TwoVanishingPointRoute(unittest.TestCase):
    def test_recovers_camera_when_horizontal(self):
        ct = camera_looking(38, 0.0)
        cam = sc.solve(synthetic_measurements(ct))
        d = cam.to_dict()
        self.assertLess(abs(d["focalPx"] - F_TRUE) / F_TRUE, 0.01)
        self.assertLess(abs(d["cameraHeightInUnits"] - H_TRUE) / H_TRUE, 0.01)
        self.assertLess(floor_error(ct, cam), 0.01)
        self.assertEqual(d["calibration"]["verdict"], "pass")

    def test_pitch_absorbed_into_principal_point_stays_usable(self):
        # With verticals unavailable the camera's pitch folds into the principal
        # point; the back-projection must still be metrically close.
        ct = camera_looking(38, -3.0)
        cam = sc.solve(synthetic_measurements(ct))
        self.assertLess(floor_error(ct, cam), 0.03)

    def test_non_orthogonal_families_are_refused(self):
        ct = camera_looking(38, 0.0)
        m = synthetic_measurements(ct)
        m["floorFamilies"][1] = m["floorFamilies"][0]  # same direction twice
        with self.assertRaises(ValueError):
            sc.solve(m)


class ThreeVanishingPointRoute(unittest.TestCase):
    def test_exact_with_clean_verticals(self):
        ct = camera_looking(38, -3.0)
        cam = sc.solve(synthetic_measurements(ct, verticals=8))
        d = cam.to_dict()
        self.assertEqual(d["route"], "three-vanishing-point")
        self.assertLess(abs(d["focalPx"] - F_TRUE), 0.5)
        self.assertLess(abs(d["principalPoint"][0] - P_TRUE[0]), 0.5)
        self.assertLess(floor_error(ct, cam), 1e-6)

    def test_focal_collapse_under_noise_is_gated(self):
        # Regression: before the leave-one-out gate on f, 0.8px of endpoint noise
        # at 3 degrees of pitch collapsed f from 772 to 259. Whatever route is
        # chosen now, the back-projection must stay metrically sane.
        ct = camera_looking(38, -3.0)
        cam = sc.solve(synthetic_measurements(ct, verticals=8, noise=0.8))
        self.assertLess(floor_error(ct, cam), 0.05)

    def test_two_verticals_cannot_claim_stability(self):
        ct = camera_looking(38, -3.0)
        m = synthetic_measurements(ct, verticals=8)
        m["verticalSegments"] = m["verticalSegments"][:2]
        cam = sc.solve(m)
        self.assertTrue(cam.to_dict()["route"].startswith("two-vanishing-point"))


class PitchAgreementGate(unittest.TestCase):
    def test_square_grid_agrees(self):
        cam = sc.solve(synthetic_measurements(camera_looking(38, 0.0)))
        base = cam.to_dict()["calibration"]["pitchDisagreement"]
        self.assertIsNotNone(base)
        self.assertLess(base, sc.PITCH_DISAGREEMENT_WARN)

    def test_disagreeing_families_fail_the_verdict(self):
        # Keep only every second line of one family: its measured repeat doubles,
        # so the two families disagree by ~2x and the verdict must be "fail".
        # (The first version of this test built the modified measurements and
        # then solved the CLEAN ones — a dead assertion. Kept as a reminder that
        # a gate without a failing-input test is not known to fire.)
        m = synthetic_measurements(camera_looking(38, 0.0))
        m["floorFamilies"][0]["segments"] = m["floorFamilies"][0]["segments"][::2]
        cam = sc.solve(m)
        cal = cam.to_dict()["calibration"]
        self.assertGreater(cal["pitchDisagreement"], sc.PITCH_DISAGREEMENT_FAIL)
        self.assertEqual(cal["verdict"], "fail")

    def test_shallow_family_still_measured(self):
        # Near-one-point perspective: at 8 deg of yaw one family is almost
        # horizontal in the image. Sampling lines only by image row used to drop
        # that entire family (pitch None -> unit silently degraded).
        cam = sc.solve(synthetic_measurements(camera_looking(8, 0.0)))
        fams = cam.to_dict()["calibration"]["floorPitchPerFamily"]
        self.assertTrue(all(f["pitch"] is not None for f in fams), fams)


class BackprojectionGates(unittest.TestCase):
    def setUp(self):
        self.ct = camera_looking(38, 0.0)
        self.cam = sc.solve(synthetic_measurements(self.ct))

    def test_horizon_y_matches_ground_truth_camera(self):
        # horizon_y is derived from K and R, so it must work on the hand-built
        # fixture too, and agree with where floor() stops returning points.
        y_h = self.ct.horizon_y(W / 2)
        self.assertIsNone(self.ct.floor(W / 2, y_h - 2))
        self.assertIsNotNone(self.ct.floor(W / 2, y_h + 8))

    def test_threejs_view_offset_recentres_principal_point(self):
        d = sc.solve(synthetic_measurements(camera_looking(38, -3.0))).to_dict()
        t = d["threejsViewOffset"]
        # cutting (offsetX, offsetY, w, h) out of the full frame puts the
        # principal point back at the stored pixel position
        self.assertAlmostEqual(t["fullWidth"] / 2 - t["offsetX"], d["principalPoint"][0], places=1)
        self.assertAlmostEqual(t["fullHeight"] / 2 - t["offsetY"], d["principalPoint"][1], places=1)

    def test_depth_sensitivity_grows_toward_horizon(self):
        low = self.cam.depth_sensitivity(W / 2, H - 20)
        high = self.cam.depth_sensitivity(W / 2, self.cam.horizon_y(W / 2) + 25)
        self.assertLess(low, high / 10)

    def test_uniform_shift_is_named_as_one_cause(self):
        # Regression: an off-centre principal point produced a 90px uniform shift
        # that looked like every piece of furniture being misplaced at once.
        contacts, expected = {}, {}
        for i, (x, z) in enumerate([(-2, 4), (1, 5), (-4, 7), (3, 8), (0, 3)]):
            q = self.ct.project((x, 0.0, z))
            contacts[f"p{i}"] = [q[0], q[1] + 90.0]     # reading shifted uniformly
            expected[f"p{i}"] = [x, z]
        landmarks = {"contacts": contacts, "expected": expected}
        result = sb.backproject(self.cam, landmarks)
        check = sb.reprojection_check(self.cam, landmarks, result)
        self.assertGreater(check["uniformShiftMagnitudePx"], 60)
        self.assertTrue(any("principal point" in d for d in check["diagnosis"]))

    def test_local_residual_names_the_landmark(self):
        contacts, expected = {}, {}
        for i, (x, z) in enumerate([(-2, 4), (1, 5), (-4, 7), (3, 8)]):
            q = self.ct.project((x, 0.0, z))
            contacts[f"p{i}"] = [q[0], q[1]]
            expected[f"p{i}"] = [x, z]
        expected["p2"] = [-5.5, 8.5]                     # one wrong placement
        landmarks = {"contacts": contacts, "expected": expected}
        result = sb.backproject(self.cam, landmarks)
        check = sb.reprojection_check(self.cam, landmarks, result)
        self.assertEqual(check["residuals"][0]["landmark"], "p2")
        self.assertTrue(any("'p2'" in d for d in check["diagnosis"]))

    def test_horizon_contact_is_flagged_not_trusted(self):
        y_h = self.cam.horizon_y(W / 2)
        result = sb.backproject(self.cam, {"contacts": {"far": [W / 2, y_h + 18]}})
        entry = result["floorPlan"]["far"]
        self.assertEqual(entry["confidence"], "horizon-limited")


class UnitSanityGate(unittest.TestCase):
    # Values measured from the motivating interior (in floor-pattern units).
    SAMPLES = {
        "doorway": {"kind": "doorHead", "units": 2.98},
        "commodeTop": {"kind": "worktop", "units": 1.15},
        "cornice": {"kind": "cornice", "units": 3.50},
        "camera": {"kind": "cameraEyeLevel", "units": 1.21},
    }

    def test_one_metre_per_tile_is_plausible(self):
        self.assertEqual(su.evaluate(1.0, self.SAMPLES)["verdict"], "plausible")

    def test_half_metre_tile_fails_conjunctively(self):
        out = su.evaluate(0.5, self.SAMPLES)
        self.assertEqual(out["verdict"], "implausible")
        self.assertTrue(out["failures"])

    def test_two_samples_cannot_support_a_verdict(self):
        few = {k: self.SAMPLES[k] for k in ("commodeTop", "camera")}   # both in-band
        self.assertIn("insufficient", su.evaluate(1.0, few)["verdict"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
