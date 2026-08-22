#!/usr/bin/env python3
"""Self-calibration from a box-dominant OBJECT's own edges (img2threejsScene spillover).

`solve_camera_pose.py` is honest that a lone object cannot fix the camera from
one image — but a BOX-DOMINANT object (tank hull, cabinet, container, building)
is the exception: its edges form two or three mutually orthogonal parallel
families, which is the same calibration target a scene's architecture provides.
This module reuses the scene solver's mathematics with the object's OWN axes as
the world frame, and its deliverable is aimed at the object pipeline's weakest
point: instead of eyeballing proportions into an ObjectSculptSpec, it returns
**measured dimension ratios** with per-corner reprojection residuals.

What it can and cannot do, stated up front:
- Needs >= 2 clean parallel edges per family on >= 2 axes. A 2001-monolith-like
  slab seen mostly face-on has one usable family and CANNOT self-calibrate —
  that case stays with the floor-grid scene route or the eyeball route.
- Absolute scale is not recoverable. The gauge is "the x dimension = 1"; pass
  `scale` with a known dimension (e.g. a tank's published hull length) or a
  known repeat (track-link pitch) to get metres.
- With only 2 families, the third axis is ASSUMED parallel to the image plane
  (same constraint as the scene solver's horizon route) and the output says so.

Input JSON:
    {"image": {"width": W, "height": H},
     "edgeFamilies": {"x": [[x1,y1,x2,y2], ...], "y": [...], "z": [...]},
     "corners": {"name": {"px": [u, v], "at": [i, j, k]}, ...},   # lattice coords, 0..1 (fractions ok)
     "scale": {"axis": "x", "metres": 6.3}}                        # optional

Axis convention: "y" is the object's up. Pure Python stdlib.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scene_camera import (  # noqa: E402
    Segment, Vec2, Vec3, _cross, _dot, _orthocentre, _unit,
    vanishing_point, VP_RESIDUAL_WARN_PX, VP_RESIDUAL_FAIL_PX,
)

F_PAIRWISE_SPREAD_WARN = 0.06   # the three pairwise f estimates of a 3-VP solve must agree
F_PAIRWISE_SPREAD_FAIL = 0.20
CORNER_RESIDUAL_WARN_PX = 6.0
GN_ITERATIONS = 120
GN_INIT_DISTANCES = (3.0, 6.0, 12.0, 24.0)   # in gauge units; multi-start, deterministic


# --- calibration ------------------------------------------------------------
def calibrate(families: dict[str, list[Segment]], image: tuple[int, int]
              ) -> dict[str, Any]:
    """f, principal point and the box axes (camera frame) from 2-3 edge families."""
    w, h = image
    vps: dict[str, tuple[Vec2, float, int]] = {}
    for axis, segs in families.items():
        if axis not in ("x", "y", "z"):
            raise ValueError(f"unknown axis '{axis}' (use x, y, z; y = object's up)")
        if len(segs) >= 2:
            vps[axis] = vanishing_point([tuple(map(float, s)) for s in segs])
    if len(vps) < 2:
        raise ValueError("need >= 2 edge families with >= 2 segments each; "
                         "a single-family subject cannot self-calibrate — "
                         "use the scene route (floor grid) or solve_camera_pose.py")

    notes: list[str] = []
    axes = sorted(vps)
    if len(vps) == 3:
        v1, v2, v3 = (vps[a][0] for a in axes)
        p = _orthocentre(v1, v2, v3)
        f2s = [-((a[0] - p[0]) * (b[0] - p[0]) + (a[1] - p[1]) * (b[1] - p[1]))
               for a, b in ((v1, v2), (v1, v3), (v2, v3))]
        if min(f2s) <= 0:
            raise ValueError("edge families are not mutually orthogonal in this view "
                             "(a pairwise f^2 <= 0) — check the axis tagging")
        fs = [math.sqrt(v) for v in f2s]
        spread = (max(fs) - min(fs)) / (sum(fs) / 3)
        f = sum(fs) / 3
        route = "three-family orthocentre"
    else:
        (a1, a2) = axes
        v1, v2 = vps[a1][0], vps[a2][0]
        px = w / 2.0
        if abs(v2[0] - v1[0]) < 1e-9:
            py = (v1[1] + v2[1]) / 2
        else:
            py = v1[1] + (v2[1] - v1[1]) * (px - v1[0]) / (v2[0] - v1[0])
        p = (px, py)
        f2 = -((v1[0] - p[0]) * (v2[0] - p[0]) + (v1[1] - p[1]) * (v2[1] - p[1]))
        if f2 <= 0:
            raise ValueError("the two edge families are not orthogonal in this view — "
                             "check the axis tagging")
        f = math.sqrt(f2)
        spread = None
        missing = ({"x", "y", "z"} - set(axes)).pop()
        route = "two-family (principal point on the vanishing line)"
        notes.append(f"axis '{missing}' unobserved and ASSUMED parallel to the image "
                     f"plane; principal point x assumed at the frame centre")

    # box axes in the camera frame (each VP direction points forward: z > 0)
    e: dict[str, Vec3] = {}
    for axis in axes:
        v = vps[axis][0]
        e[axis] = _unit(((v[0] - p[0]) / f, (v[1] - p[1]) / f, 1.0))  # type: ignore[assignment]
    missing = {"x", "y", "z"} - set(axes)
    if missing:
        m = missing.pop()
        others = [a for a in ("x", "y", "z") if a != m]
        d = _unit(_cross(e[others[0]], e[others[1]]))
        e[m] = d  # type: ignore[assignment]
    if e["y"][1] > 0:            # camera y points down; the object's up must not
        e["y"] = tuple(-c for c in e["y"])  # type: ignore[assignment]
    # right-handed x, y, z; re-orthogonalise around the two measured axes
    if _dot(_cross(e["x"], e["y"]), e["z"]) < 0:
        e["x"] = tuple(-c for c in e["x"])  # type: ignore[assignment]
    e["z"] = _unit(_cross(e["x"], e["y"]))          # type: ignore[assignment]
    e["x"] = _unit(_cross(e["y"], e["z"]))          # type: ignore[assignment]

    resid = {a: round(vps[a][1], 2) for a in axes}
    worst = max(resid.values())
    verdict = "pass"
    if worst > VP_RESIDUAL_FAIL_PX or (spread is not None and spread > F_PAIRWISE_SPREAD_FAIL):
        verdict = "fail"
    elif worst > VP_RESIDUAL_WARN_PX or (spread is not None and spread > F_PAIRWISE_SPREAD_WARN):
        verdict = "warn"
    return {"focalPx": f, "principalPoint": p, "axesCameraFrame": e,
            "route": route, "vpResidualPx": resid,
            "fPairwiseSpread": round(spread, 4) if spread is not None else None,
            "verdict": verdict, "notes": notes}


# --- box fit (Gauss-Newton, stdlib) -----------------------------------------
def _solve_lin(A: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-12:
            raise ValueError("singular normal equations (corners underconstrain the box)")
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c:
                k = M[r][c] / M[c][c]
                for cc in range(c, n + 1):
                    M[r][cc] -= k * M[c][cc]
    return [M[r][n] / M[r][r] for r in range(n)]


def fit_box(cal: dict[str, Any], corners: dict[str, Any]) -> dict[str, Any]:
    """Fit origin (camera frame) + dimensions (gauge: dim_x = 1) to corner pixels."""
    f, (px, py) = cal["focalPx"], cal["principalPoint"]
    e = cal["axesCameraFrame"]
    names = sorted(corners)
    obs = [(corners[n]["px"], corners[n]["at"]) for n in names]
    if len(obs) < 4:
        raise ValueError("need >= 4 tagged corners to constrain the box")

    def residuals(th: Sequence[float]) -> list[float]:
        ox, oy, oz, dy, dz = th
        r = []
        for (u, v), (i, j, k) in obs:
            c = [ox + i * e["x"][0] + j * dy * e["y"][0] + k * dz * e["z"][0],
                 oy + i * e["x"][1] + j * dy * e["y"][1] + k * dz * e["z"][1],
                 oz + i * e["x"][2] + j * dy * e["y"][2] + k * dz * e["z"][2]]
            if c[2] < 1e-6:
                return [1e4] * (2 * len(obs))
            r += [px + f * c[0] / c[2] - u, py + f * c[1] / c[2] - v]
        return r

    best: tuple[float, list[float]] | None = None
    mean_u = sum(o[0][0] for o in obs) / len(obs)
    mean_v = sum(o[0][1] for o in obs) / len(obs)
    look = _unit(((mean_u - px) / f, (mean_v - py) / f, 1.0))
    for dist in GN_INIT_DISTANCES:
        th = [look[0] * dist - 0.5, look[1] * dist - 0.5, look[2] * dist, 1.0, 1.0]
        for _ in range(GN_ITERATIONS):
            r0 = residuals(th)
            J = []
            for pi in range(5):
                tp = th[:]
                step = 1e-5 * max(1.0, abs(th[pi]))
                tp[pi] += step
                rp = residuals(tp)
                J.append([(a - b) / step for a, b in zip(rp, r0)])
            A = [[sum(J[a][k] * J[b][k] for k in range(len(r0))) + (1e-9 if a == b else 0)
                  for b in range(5)] for a in range(5)]
            g = [-sum(J[a][k] * r0[k] for k in range(len(r0))) for a in range(5)]
            try:
                delta = _solve_lin(A, g)
            except ValueError:
                break
            cost0 = sum(x * x for x in r0)
            scale = 1.0
            for _ in range(12):                     # step-halving line search
                cand = [t + scale * d for t, d in zip(th, delta)]
                if sum(x * x for x in residuals(cand)) < cost0:
                    th = cand
                    break
                scale *= 0.5
            else:
                break
            if max(abs(d) * scale for d in delta) < 1e-10:
                break
        cost = sum(x * x for x in residuals(th))
        if best is None or cost < best[0]:
            best = (cost, th)
    assert best is not None
    th = best[1]
    r = residuals(th)
    per = [math.hypot(r[2 * i], r[2 * i + 1]) for i in range(len(obs))]
    mdx = sum(r[2 * i] for i in range(len(obs))) / len(obs)
    mdy = sum(r[2 * i + 1] for i in range(len(obs))) / len(obs)
    local = [{"corner": n, "residualPx": round(math.hypot(r[2 * i] - mdx, r[2 * i + 1] - mdy), 2)}
             for i, n in enumerate(names)]
    dims = {"x": 1.0, "y": abs(th[3]), "z": abs(th[4])}
    flipped = [a for a, v in (("y", th[3]), ("z", th[4])) if v < 0]
    worst = max(local, key=lambda d: d["residualPx"])
    diagnosis = []
    if math.hypot(mdx, mdy) > CORNER_RESIDUAL_WARN_PX:
        diagnosis.append(f"uniform shift {math.hypot(mdx, mdy):.1f}px — suspect the "
                         f"calibration (principal point), not the corner readings")
    if worst["residualPx"] > CORNER_RESIDUAL_WARN_PX:
        diagnosis.append(f"corner '{worst['corner']}' is off by {worst['residualPx']}px — "
                         f"re-read that pixel or its lattice tag")
    return {"gauge": "dimension along x = 1",
            "dimensions": {a: round(v, 4) for a, v in dims.items()},
            "originCameraFrame": [round(v, 4) for v in th[:3]],
            "axesFlippedDuringFit": flipped,
            "meanCornerResidualPx": round(sum(per) / len(per), 2),
            "uniformShiftPx": round(math.hypot(mdx, mdy), 2),
            "perCorner": sorted(local, key=lambda d: -d["residualPx"]),
            "diagnosis": diagnosis or ["corner reprojection is clean"]}


def apply_scale(box: dict[str, Any], scale: dict[str, Any]) -> dict[str, Any]:
    axis, metres = scale["axis"], float(scale["metres"])
    per_unit = metres / box["dimensions"][axis]
    return {"metresPerGaugeUnit": round(per_unit, 4),
            "dimensionsMetres": {a: round(v * per_unit, 3)
                                 for a, v in box["dimensions"].items()},
            "source": f"user-supplied: dimension '{axis}' = {metres} m"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("measurements")
    ap.add_argument("--out", default="box-calibration.json")
    args = ap.parse_args(argv)
    m = json.loads(Path(args.measurements).read_text())
    img = (int(m["image"]["width"]), int(m["image"]["height"]))
    cal = calibrate({a: s for a, s in m["edgeFamilies"].items()}, img)
    out: dict[str, Any] = {
        "calibration": {k: (round(v, 2) if isinstance(v, float) else v)
                        for k, v in cal.items() if k != "axesCameraFrame"},
        "axesCameraFrame": {a: [round(c, 6) for c in v]
                            for a, v in cal["axesCameraFrame"].items()},
    }
    if m.get("corners"):
        out["box"] = fit_box(cal, m["corners"])
        if m.get("scale"):
            out["scale"] = apply_scale(out["box"], m["scale"])
        else:
            out["calibration"]["notes"] = cal["notes"] + [
                "no scale reference: dimensions are ratios (x = 1), not lengths"]
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n")
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if cal["verdict"] != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
