#!/usr/bin/env python3
"""Solve the camera from a scene's own architecture (img2threejsScene, stage 1).

For a single OBJECT, the camera is not recoverable from one image — the existing
`solve_camera_pose.py` says so and emits agent-fill placeholders. A SCENE is the
opposite case: an interior (or a street, or any built space) contains long
straight edges in two or three mutually orthogonal directions, and that is a
calibration target. So the usual order inverts:

    object : decomposition easy, measurement impossible  -> fit by eye
    scene  : decomposition hard, measurement possible    -> fit by arithmetic

Three closures make it work, in this order:

1. **Two orthogonal floor directions** give two vanishing points v1, v2. The line
   through them is the HORIZON of the floor plane.
2. **The vertical direction** decides where the principal point p is:
   - if the verticals converge (a finite v3), p is the ORTHOCENTRE of the
     triangle v1v2v3 and f follows from any orthogonal pair;
   - if the verticals are parallel in the image (v3 at infinity — the usual case
     for an architectural shot, and testable, see `vertical_vp`), then the image
     plane contains the vertical axis, so **p lies on the horizon** and the
     classic two-point formula closes it: f = sqrt(|p-v1| * |p-v2|).
   Either way, p is generally NOT the centre of the frame. Film stills and crops
   move it. See `grimoire/scene/traps.md` — assuming p = centre is the single
   most expensive mistake in this pipeline.
3. **A repeated floor pattern** (tiles, boards, paving, joists) fixes the scale.
   The unit of length is one repeat; absolute length is not recoverable from one
   image and is not invented here. `scene_unit_gate.py` tests a proposed metre
   value against architectural priors instead.

This module does no computer vision. Line segments come from the agent, which
can see; the script does the arithmetic and the gating, which the agent cannot do
reliably. That is the same division of labour as the rest of the pipeline.

Pure Python stdlib. No numpy, no PIL, no OpenCV.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
Mat3 = tuple[Vec3, Vec3, Vec3]
Segment = tuple[float, float, float, float]

# --- gate thresholds -------------------------------------------------------
MIN_SEGMENTS_PER_FAMILY = 2
VP_RESIDUAL_WARN_PX = 4.0          # mean point-line distance at the vanishing point
VP_RESIDUAL_FAIL_PX = 12.0
PITCH_DISAGREEMENT_WARN = 0.10     # the two floor families must report the same pitch
PITCH_DISAGREEMENT_FAIL = 0.35
TRIM_FRACTION = 0.25               # drop this worst share of segments and refit once
VERTICAL_SPREAD_AT_INFINITY = 0.5  # leave-one-out relative spread of 1/|v3| above this = parallel
PRINCIPAL_POINT_MAX_OFFSET = 0.5   # reject a 3-VP principal point further than this * diagonal
F_LOO_SPREAD_MAX = 0.10            # 3-VP focal length must be stable to 10% under leave-one-out


# --- small linear algebra (stdlib only) ------------------------------------
def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(a: Sequence[float]) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a: Sequence[float]) -> tuple[float, ...]:
    n = _norm(a)
    if n < 1e-12:
        raise ValueError("cannot normalise a zero vector")
    return tuple(x / n for x in a)


def _solve2(m: tuple[float, float, float, float], rhs: Vec2) -> Vec2:
    a, b, c, d = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        raise ValueError("degenerate 2x2 system (are the lines parallel?)")
    return ((rhs[0] * d - b * rhs[1]) / det, (a * rhs[1] - rhs[0] * c) / det)


def _det3(m: Mat3) -> float:
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def _matvec(m: Mat3, v: Vec3) -> Vec3:
    return tuple(_dot(row, v) for row in m)  # type: ignore[return-value]


def _transpose(m: Mat3) -> Mat3:
    return tuple(tuple(m[r][c] for r in range(3)) for c in range(3))  # type: ignore[return-value]


# --- lines and vanishing points --------------------------------------------
def line_of(seg: Segment) -> tuple[float, float, float]:
    """Segment -> normalised line (A, B, C) with A*x + B*y + C = 0, A^2+B^2 = 1.

    The general form is used on purpose: the `x = a*y + b` form blows up on
    horizontal lines, and a scene has plenty of those.
    """
    x1, y1, x2, y2 = seg
    dx, dy = x2 - x1, y2 - y1
    n = math.hypot(dx, dy)
    if n < 1e-9:
        raise ValueError(f"degenerate segment {seg}")
    a, b = -dy / n, dx / n
    return (a, b, -(a * x1 + b * y1))


def vanishing_point(segments: Sequence[Segment], *, trim: float = TRIM_FRACTION
                    ) -> tuple[Vec2, float, int]:
    """Least-squares intersection of a bundle of lines, with one trimming refit.

    Minimises the sum of squared point-line distances, which is well conditioned
    even when the point is far outside the frame (it usually is).
    Returns (point, mean residual px, segments kept).
    """
    lines = [line_of(s) for s in segments]
    if len(lines) < MIN_SEGMENTS_PER_FAMILY:
        raise ValueError(f"need >= {MIN_SEGMENTS_PER_FAMILY} segments, got {len(lines)}")

    def fit(ls: Sequence[tuple[float, float, float]]) -> Vec2:
        saa = sum(l[0] * l[0] for l in ls)
        sab = sum(l[0] * l[1] for l in ls)
        sbb = sum(l[1] * l[1] for l in ls)
        sac = sum(l[0] * l[2] for l in ls)
        sbc = sum(l[1] * l[2] for l in ls)
        return _solve2((saa, sab, sab, sbb), (-sac, -sbc))

    point = fit(lines)
    if len(lines) >= 4 and trim > 0:
        resid = sorted(lines, key=lambda l: abs(l[0] * point[0] + l[1] * point[1] + l[2]))
        keep = resid[: max(MIN_SEGMENTS_PER_FAMILY, int(round(len(lines) * (1 - trim))))]
        point = fit(keep)
        lines = keep
    mean_res = sum(abs(l[0] * point[0] + l[1] * point[1] + l[2]) for l in lines) / len(lines)
    return point, mean_res, len(lines)


def vertical_vp(segments: Sequence[Segment], diag: float,
                centre: Vec2 = (0.0, 0.0)) -> dict[str, Any]:
    """Do the verticals converge, or are they parallel *within their own noise*?

    This is a judgement the arithmetic can make and the eye cannot. Two nearly
    vertical edges 40px apart always "converge somewhere", but if the implied
    convergence moves around when you drop any one segment, it means nothing.
    So the test is leave-one-out: refit without each segment and look at the
    spread of 1/|v3 - centre| (the reciprocal, because that is the quantity
    proportional to tan(pitch); |v3| itself is unbounded and useless as a scale).

    Getting this wrong is expensive in both directions: calling a real
    convergence "parallel" silently folds the camera's pitch into the principal
    point, and calling noise a convergence puts the principal point anywhere.
    """
    if len(segments) < MIN_SEGMENTS_PER_FAMILY:
        return {"available": False, "reason": "fewer than 2 vertical segments supplied"}
    try:
        point, res, kept = vanishing_point(segments, trim=0.0)
    except ValueError:
        # exactly parallel lines — the strongest possible "at infinity"
        return {"available": True, "point": None, "meanResidualPx": 0.0,
                "segments": len(segments), "leaveOneOutSpread": None, "atInfinity": True,
                "reason": "verticals are exactly parallel -> principal point constrained to the horizon"}
    centre = (0.0, 0.0)
    inv = []
    if len(segments) >= 3:
        for i in range(len(segments)):
            subset = [s for j, s in enumerate(segments) if j != i]
            try:
                q, _, _ = vanishing_point(subset, trim=0.0)
            except ValueError:
                continue
            d = math.hypot(q[0] - centre[0], q[1] - centre[1])
            inv.append(1.0 / d if d > 1e-9 else float("inf"))
    base = math.hypot(point[0] - centre[0], point[1] - centre[1])
    base_inv = 1.0 / base if base > 1e-9 else float("inf")
    if len(inv) >= 2 and base_inv > 0:
        mean = sum(inv) / len(inv)
        var = sum((x - mean) ** 2 for x in inv) / (len(inv) - 1)
        spread = math.sqrt(var) / abs(mean) if mean else float("inf")
    else:
        spread = None
    at_infinity = (base > 200.0 * diag) or (spread is not None and spread > VERTICAL_SPREAD_AT_INFINITY)
    return {"available": True, "point": [round(point[0], 1), round(point[1], 1)],
            "meanResidualPx": round(res, 3), "segments": kept,
            "leaveOneOutSpread": round(spread, 3) if spread is not None else None,
            "atInfinity": at_infinity,
            "reason": ("verticals are parallel within the fit noise -> the principal point is "
                       "constrained to the horizon and any real camera pitch is absorbed into it"
                       if at_infinity else
                       "verticals converge with a stable vanishing point -> full 3-VP calibration")}


# --- camera ----------------------------------------------------------------
class SceneCamera:
    """Pinhole camera for a scene, expressed in the floor grid's own frame.

    World frame: origin at the camera's foot on the floor, +Y up, +X and +Z along
    the two measured floor directions. One unit = one floor-pattern repeat.
    """

    def __init__(self, focal: float, principal: Vec2, rotation: Mat3, height: float,
                 image: tuple[int, int], meta: dict[str, Any] | None = None):
        self.f = focal
        self.p = principal
        # rotation maps world -> camera.  **The world axes go in the COLUMNS.**
        # Putting them in the rows silently mixes the two frames: near points stay
        # almost right and far points stretch, so a room comes out too long and it
        # reads as a modelling error. See grimoire/scene/traps.md.
        self.R = rotation
        self.Rt = _transpose(rotation)
        self.h = height
        self.image = image
        self.meta = meta or {}

    def ray(self, u: float, v: float) -> Vec3:
        d = ((u - self.p[0]) / self.f, (v - self.p[1]) / self.f, 1.0)
        return _unit(_matvec(self.Rt, d))  # type: ignore[return-value]

    def floor(self, u: float, v: float) -> Vec2 | None:
        """Pixel -> point on the floor plane y = 0, or None if the ray misses it."""
        d = self.ray(u, v)
        if d[1] >= -1e-9:
            return None
        t = self.h / -d[1]
        return (d[0] * t, d[2] * t)

    def height_at(self, u: float, v: float, foot: Vec2) -> float:
        """Height of the pixel (u, v) on a vertical line standing at floor point `foot`."""
        d = self.ray(u, v)
        horiz = math.hypot(d[0], d[2])
        if horiz < 1e-9:
            raise ValueError("ray is vertical; cannot resolve a height this way")
        t = math.hypot(foot[0], foot[1]) / horiz
        return self.h + d[1] * t

    def project(self, point: Vec3) -> Vec2 | None:
        c = _matvec(self.R, (point[0], point[1] - self.h, point[2]))
        if c[2] <= 1e-6:
            return None
        return (self.p[0] + self.f * c[0] / c[2], self.p[1] + self.f * c[1] / c[2])

    def horizon_y(self, x: float) -> float:
        """Image y of the floor plane's vanishing line at column x.

        Derived from K and R, not from stored vanishing points: a ray (u, v) lies
        on the horizon iff its world direction has no vertical component, i.e.
        R[0][1]*(u-px)/f + R[1][1]*(v-py)/f + R[2][1] = 0  (the middle column of
        R is the world up-axis in camera coordinates). Works on any SceneCamera,
        including hand-built fixtures with no meta.
        """
        a = self.R[0][1] * (x - self.p[0]) / self.f + self.R[2][1]
        b = self.R[1][1] / self.f
        if abs(b) < 1e-12:
            raise ValueError("degenerate orientation: horizon is not a function of x")
        return self.p[1] - a / b

    def depth_sensitivity(self, u: float, v: float) -> float:
        """Floor units of depth error per pixel of reading error, at this pixel.

        A contact point near the horizon is not a measurement. Reporting this
        number is what stops the pipeline from silently placing a cabinet three
        times too far away.
        """
        dy = abs(v - self.horizon_y(u))
        if dy < 1e-6:
            return float("inf")
        return self.h * self.f / (dy * dy)

    def to_dict(self) -> dict[str, Any]:
        w, hgt = self.image
        # Off-axis frustum for Three.js. A plain PerspectiveCamera cannot express
        # an off-centre principal point (traps.md #1), so hand over the numbers
        # for camera.setViewOffset(fullW, fullH, offX, offY, w, h) directly:
        # a virtual frame centred ON the principal point, with the real frame
        # cut out of it.
        full_w = 2 * max(self.p[0], w - self.p[0])
        full_h = 2 * max(self.p[1], hgt - self.p[1])
        three = {
            "fovFullDeg": round(math.degrees(2 * math.atan(full_h / 2 / self.f)), 3),
            "fullWidth": round(full_w, 2), "fullHeight": round(full_h, 2),
            "offsetX": round(full_w / 2 - self.p[0], 2),
            "offsetY": round(full_h / 2 - self.p[1], 2),
            "width": w, "height": hgt,
        }
        return {
            "threejsViewOffset": three,
            "focalPx": round(self.f, 2),
            "principalPoint": [round(self.p[0], 2), round(self.p[1], 2)],
            "principalPointOffsetFromCentrePx": [round(self.p[0] - w / 2, 2),
                                                 round(self.p[1] - hgt / 2, 2)],
            "fovHorizontalDeg": round(math.degrees(2 * math.atan(w / 2 / self.f)), 3),
            "fovVerticalDeg": round(math.degrees(2 * math.atan(hgt / 2 / self.f)), 3),
            "rotationWorldToCameraRowMajor": [round(x, 6) for row in self.R for x in row],
            "cameraHeightInUnits": round(self.h, 4),
            "image": {"width": w, "height": hgt},
            **self.meta,
        }


def _floor_basis(f: float, p: Vec2, v1: Vec2, v2: Vec2) -> Mat3:
    """World->camera rotation whose X and Z axes are the two measured floor directions."""
    d1 = _unit(((v1[0] - p[0]) / f, (v1[1] - p[1]) / f, 1.0))
    d2 = _unit(((v2[0] - p[0]) / f, (v2[1] - p[1]) / f, 1.0))
    up = list(_unit(_cross(d2, d1)))
    if up[1] > 0:                      # camera +Y points down, so world up has y < 0
        up = [-x for x in up]
    # re-orthogonalise: the measured directions are only approximately orthogonal
    d2o = _unit(_cross(tuple(up), d1))          # type: ignore[arg-type]
    cols = (d2o, tuple(up), d1)
    R: Mat3 = tuple(tuple(cols[c][r] for c in range(3)) for r in range(3))  # type: ignore[assignment]
    if _det3(R) < 0:
        cols = (tuple(-x for x in d2o), tuple(up), d1)
        R = tuple(tuple(cols[c][r] for c in range(3)) for r in range(3))    # type: ignore[assignment]
    return R


def _pitch_from_families(cam: SceneCamera, families: Sequence[Sequence[Segment]],
                         rows: Vec2) -> list[dict[str, Any]]:
    """Spacing of each floor family, measured on the floor plane itself.

    Every line of one family becomes a single number — its signed offset along
    the family normal — so the spacing shows up as clusters, and the gap between
    cluster centres is the repeat. Squares mean both families must agree; when
    they do not, that disagreement IS the scale uncertainty and is reported.

    Two robustness points, both learned the hard way:
    - A line is sampled along its DOMINANT image axis. Parametrising by y alone
      throws away every near-horizontal line, which is the entire second family
      of a one-point-perspective floor.
    - The clustering threshold is scale-free. The gap distribution is BIMODAL:
      several detected segments per physical grout give tiny gaps, neighbouring
      grouts give repeat-sized ones. So the split point is the largest
      multiplicative jump in the sorted gaps — not the median (dragged into the
      duplicate mode) and not a fixed absolute value (merges everything when
      the camera is high and the repeat is small in camera-height units; both
      were tried and both failed on real or synthetic data).
    """
    out: list[dict[str, Any]] = []
    y0, y1 = rows
    for fam in families:
        offsets: list[float] = []
        for seg in fam:
            a, b, c = line_of(seg)
            if abs(a) >= abs(b):                       # steep in the image: sample two rows
                if abs(a) < 1e-9:
                    continue
                px_pts = [(-(b * y + c) / a, y) for y in (y0, y1)]
            else:                                      # shallow: sample two columns
                x0, x1 = seg[0], seg[2]
                if abs(x1 - x0) < 1e-6:
                    x0, x1 = x0 - 40.0, x0 + 40.0
                px_pts = [(x, -(a * x + c) / b) for x in (x0, x1)]
            pts = [cam.floor(u, v) for u, v in px_pts]
            if any(p_ is None for p_ in pts):
                continue
            p0, p1 = pts
            d = _unit((p1[0] - p0[0], p1[1] - p0[1]))
            offsets.append(-d[1] * p0[0] + d[0] * p0[1])
        offsets.sort()
        gaps_all = sorted(g for g in (b2 - a2 for a2, b2 in zip(offsets, offsets[1:]))
                          if g > 1e-9)
        merge_below = 0.0
        if len(gaps_all) >= 2:
            jumps = [(gaps_all[i + 1] / gaps_all[i], i) for i in range(len(gaps_all) - 1)]
            ratio, at = max(jumps)
            if ratio >= 3.0:                     # clear duplicate/repeat separation
                merge_below = math.sqrt(gaps_all[at] * gaps_all[at + 1])
        clusters: list[list[float]] = []
        for o in offsets:
            if clusters and o - clusters[-1][-1] < merge_below:
                clusters[-1].append(o)
            else:
                clusters.append([o])
        centres = [sum(c) / len(c) for c in clusters]
        gaps = [b2 - a2 for a2, b2 in zip(centres, centres[1:])]
        pitch = sorted(gaps)[len(gaps) // 2] if gaps else None
        out.append({"lines": len(offsets), "clusters": len(centres),
                    "pitch": round(pitch, 4) if pitch else None})
    return out


def solve(measurements: dict[str, Any]) -> SceneCamera:
    img = measurements["image"]
    w, h = int(img["width"]), int(img["height"])
    diag = math.hypot(w, h)
    fams = measurements["floorFamilies"]
    if len(fams) != 2:
        raise ValueError("exactly two orthogonal floor families are required")
    segs = [[tuple(map(float, s)) for s in fam["segments"]] for fam in fams]

    v1, r1, n1 = vanishing_point(segs[0])
    v2, r2, n2 = vanishing_point(segs[1])
    vert = vertical_vp([tuple(map(float, s)) for s in measurements.get("verticalSegments", [])],
                       diag, centre=(w / 2, h / 2))

    notes: list[str] = []
    p = None
    route = ""
    f2 = 0.0
    if vert.get("available") and not vert.get("atInfinity"):
        # Full three-point calibration: p is the orthocentre of v1 v2 v3.
        # Guarded twice, because the triangle degenerates exactly when the
        # verticals are nearly parallel — which is the COMMON case, not an
        # exotic one. The stability test is leave-one-out **on the derived
        # focal length**, not on v3: with near-parallel verticals v3 wanders by
        # thousands of pixels under sub-pixel endpoint noise, and an
        # innocent-looking v3 can still produce f wrong by 3x. (Measured on the
        # synthetic fixture: 0.8px of endpoint noise at 3 deg of pitch collapsed
        # f from 772 to 259 before this gate existed.)
        vsegs = [tuple(map(float, s_)) for s_ in measurements.get("verticalSegments", [])]

        def f_from_verts(subset: Sequence[Segment]) -> tuple[Vec2, float] | None:
            try:
                v3s, _, _ = vanishing_point(subset, trim=0.0)
                cand = _orthocentre(v1, v2, v3s)
                cf2 = -((v1[0] - cand[0]) * (v2[0] - cand[0])
                        + (v1[1] - cand[1]) * (v2[1] - cand[1]))
                if cf2 <= 0:
                    return None
                return cand, math.sqrt(cf2)
            except ValueError:
                return None

        full = f_from_verts(vsegs)
        loo_f = [r[1] for i in range(len(vsegs))
                 if len(vsegs) > 2 and (r := f_from_verts([s_ for j, s_ in enumerate(vsegs) if j != i]))]
        stable = (full is not None and len(loo_f) >= 2
                  and (max(loo_f) - min(loo_f)) / full[1] <= F_LOO_SPREAD_MAX)
        if stable:
            cand, f_cand = full
            off = math.hypot(cand[0] - w / 2, cand[1] - h / 2)
            if off < PRINCIPAL_POINT_MAX_OFFSET * diag:
                p, f2, route = cand, f_cand * f_cand, "three-vanishing-point"
                vert["fLeaveOneOutSpread"] = round((max(loo_f) - min(loo_f)) / f_cand, 4)
            else:
                notes.append("3-VP principal point implausibly far from the frame; "
                             "fell back to the horizon constraint")
        else:
            spread_txt = (round((max(loo_f) - min(loo_f)) / full[1], 3)
                          if full and len(loo_f) >= 2 else None)
            notes.append(f"3-VP focal length unstable under leave-one-out "
                         f"(spread={spread_txt}); fell back to the horizon constraint")
    if p is None:
        px = float(measurements.get("principalPointX", w / 2))  # not measurable from one plane
        if abs(v2[0] - v1[0]) < 1e-9:
            py = (v1[1] + v2[1]) / 2
        else:
            py = v1[1] + (v2[1] - v1[1]) * (px - v1[0]) / (v2[0] - v1[0])
        p = (px, py)
        f2 = -((v1[0] - p[0]) * (v2[0] - p[0]) + (v1[1] - p[1]) * (v2[1] - p[1]))
        route = "two-vanishing-point (principal point on the horizon)"
        notes.append("principal point x assumed at the frame centre; only its y is measured")
    if f2 <= 0:
        raise ValueError("the two floor families are not orthogonal in this view "
                         "(f^2 <= 0) — check that the segments really are two "
                         "perpendicular directions of the same plane")
    f = math.sqrt(f2)

    R = _floor_basis(f, p, v1, v2)
    cam = SceneCamera(f, p, R, 1.0, (w, h))
    cam.meta = {"vp1": [round(v1[0], 1), round(v1[1], 1)],
                "vp2": [round(v2[0], 1), round(v2[1], 1)]}

    rows = measurements.get("pitchRows") or [h * 0.87, h * 0.96]
    pitches = _pitch_from_families(cam, segs, (float(rows[0]), float(rows[1])))
    vals = [p_["pitch"] for p_ in pitches if p_["pitch"]]
    if vals:
        pitch = sum(vals) / len(vals)
        disagree = (max(vals) - min(vals)) / pitch if len(vals) > 1 else 0.0
        cam.h = 1.0 / pitch
    else:
        pitch, disagree = None, None
        notes.append("no floor repeat could be measured; length unit = camera height")

    verdict = "pass"
    if max(r1, r2) > VP_RESIDUAL_FAIL_PX:
        verdict = "fail"
    elif max(r1, r2) > VP_RESIDUAL_WARN_PX:
        verdict = "warn"
    if disagree is not None:
        if disagree > PITCH_DISAGREEMENT_FAIL:
            verdict = "fail"
        elif disagree > PITCH_DISAGREEMENT_WARN and verdict == "pass":
            verdict = "warn"

    cam.meta.update({
        "route": route,
        "verticalCheck": vert,
        "calibration": {
            "vp1ResidualPx": round(r1, 2), "vp1Segments": n1,
            "vp2ResidualPx": round(r2, 2), "vp2Segments": n2,
            "floorPitchPerFamily": pitches,
            "pitchDisagreement": round(disagree, 4) if disagree is not None else None,
            "verdict": verdict,
        },
        "lengthUnit": "one floor-pattern repeat" if vals else "camera height",
        "notes": notes,
    })
    return cam


def _orthocentre(a: Vec2, b: Vec2, c: Vec2) -> Vec2:
    """Orthocentre of a triangle — the principal point of a 3-VP calibration."""
    # altitude from a is perpendicular to bc, etc.  Solve the 2x2 system.
    m = ((c[0] - b[0], c[1] - b[1]), (c[0] - a[0], c[1] - a[1]))
    rhs = (m[0][0] * a[0] + m[0][1] * a[1], m[1][0] * b[0] + m[1][1] * b[1])
    return _solve2((m[0][0], m[0][1], m[1][0], m[1][1]), rhs)


def load_camera(path: Path) -> SceneCamera:
    d = json.loads(Path(path).read_text())
    r = d["rotationWorldToCameraRowMajor"]
    R: Mat3 = ((r[0], r[1], r[2]), (r[3], r[4], r[5]), (r[6], r[7], r[8]))
    cam = SceneCamera(d["focalPx"], tuple(d["principalPoint"]), R,
                      d["cameraHeightInUnits"],
                      (d["image"]["width"], d["image"]["height"]))
    cam.meta = {k: v for k, v in d.items() if k in ("vp1", "vp2", "route", "calibration")}
    return cam


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("measurements", help="JSON with image size and two floor line families")
    ap.add_argument("--out", default="scene-camera.json")
    args = ap.parse_args(argv)
    cam = solve(json.loads(Path(args.measurements).read_text()))
    payload = cam.to_dict()
    Path(args.out).write_text(json.dumps(payload, indent=2) + "\n")
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if payload["calibration"]["verdict"] != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
