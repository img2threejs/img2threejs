#!/usr/bin/env python3
"""Gate a swept component's SHAPE — bend radius, angular span and taper — on executed geometry.

WHAT THIS IS FOR. "Curled upward into a hook; a curved spine, not a straight cone" is a claim about
a curve, and no existing gate can hold it. A silhouette IoU passes a straight cone that happens to
occupy roughly the right cells. `self_intersection.py` asks whether a mesh crosses itself, not what
shape it is. A visual review can see the difference but only after a render, and only as an
impression rather than a number.

So the shape claim is fitted directly. The component's vertices are projected onto their own
best-fit plane, an arc centre is fitted by minimising the spread of radii, and the gate reports the
bend radius, the angular span actually covered, the tube radius along the arc, and the residual of
the fit.

THE NEGATIVE CONTROL IS THE POINT. A straight tapered cone has no stable arc centre: pushing the
centre far enough away makes any bounded set look like a thin annulus, so `radiusSpread` alone can
be driven arbitrarily low and is not evidence of curvature on its own. What separates a hook from a
cone is the ANGULAR SPAN subtended at the fitted centre — a straight segment subtends a span that
shrinks as the fitted centre recedes, while a real hook holds a large span at a centre close to it.
The gate therefore requires span AND a bounded centre distance, and `forge/tests/test_swept_arc.py`
runs a straight cone through it to prove it fails.

Exit codes: 0 clean, 1 gate failure, 2 error.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"{label} error: {error}")


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def symmetric_eigenvalues(matrix: list[list[float]]) -> tuple[float, float, float]:
    """Eigenvalues of a symmetric 3x3, largest first, by the closed-form trigonometric solution."""
    p1 = matrix[0][1] ** 2 + matrix[0][2] ** 2 + matrix[1][2] ** 2
    trace = matrix[0][0] + matrix[1][1] + matrix[2][2]
    if p1 == 0.0:
        return tuple(sorted((matrix[0][0], matrix[1][1], matrix[2][2]), reverse=True))
    q = trace / 3.0
    p2 = sum((matrix[i][i] - q) ** 2 for i in range(3)) + 2.0 * p1
    p = math.sqrt(p2 / 6.0)
    b = [[(matrix[i][j] - (q if i == j else 0.0)) / p for j in range(3)] for i in range(3)]
    determinant = (
        b[0][0] * (b[1][1] * b[2][2] - b[1][2] * b[2][1])
        - b[0][1] * (b[1][0] * b[2][2] - b[1][2] * b[2][0])
        + b[0][2] * (b[1][0] * b[2][1] - b[1][1] * b[2][0])
    )
    r = determinant / 2.0
    r = -1.0 if r <= -1.0 else 1.0 if r >= 1.0 else r
    phi = math.acos(r) / 3.0
    eig1 = q + 2.0 * p * math.cos(phi)
    eig3 = q + 2.0 * p * math.cos(phi + 2.0 * math.pi / 3.0)
    eig2 = trace - eig1 - eig3
    return (eig1, eig2, eig3)


def best_fit_plane(points: list[tuple[float, float, float]]):
    """Return (origin, u, v, normal, planarity) for the plane the points lie closest to.

    `planarity` is `(lambda2 - lambda3) / lambda2` on the covariance eigenvalues. It matters
    because a SHORT sweep of a THICK tube is a rod, and a rod does not determine a plane: its two
    smaller eigenvalues are both the tube's cross-section and are nearly equal, so the smallest
    eigenvector is an arbitrary direction in that cross-section. Fitting an arc in that arbitrary
    plane produces a confident, meaningless bend radius — this measurement found exactly that on a
    40-degree fixture before the degeneracy was detected instead of ignored.
    """
    cx, cy, cz = (_mean([p[0] for p in points]), _mean([p[1] for p in points]), _mean([p[2] for p in points]))
    xx = xy = xz = yy = yz = zz = 0.0
    for x, y, z in points:
        dx, dy, dz = x - cx, y - cy, z - cz
        xx += dx * dx
        xy += dx * dy
        xz += dx * dz
        yy += dy * dy
        yz += dy * dz
        zz += dz * dz
    matrix = [[xx, xy, xz], [xy, yy, yz], [xz, yz, zz]]
    eig1, eig2, eig3 = symmetric_eigenvalues(matrix)
    planarity = (eig2 - eig3) / eig2 if eig2 > 1e-15 else 0.0

    # Power-iterate on (trace*I - M) to land on the SMALLEST eigenvector of M.
    trace = xx + yy + zz
    shifted = [[(trace if i == j else 0.0) - matrix[i][j] for j in range(3)] for i in range(3)]
    vector = [1.0, 0.37, 0.11]
    for _ in range(200):
        nxt = [sum(shifted[i][j] * vector[j] for j in range(3)) for i in range(3)]
        norm = math.sqrt(sum(v * v for v in nxt))
        if norm < 1e-15:
            break
        vector = [v / norm for v in nxt]
    normal = tuple(vector)

    seed = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
    ux = seed[1] * normal[2] - seed[2] * normal[1]
    uy = seed[2] * normal[0] - seed[0] * normal[2]
    uz = seed[0] * normal[1] - seed[1] * normal[0]
    ulen = math.sqrt(ux * ux + uy * uy + uz * uz)
    u = (ux / ulen, uy / ulen, uz / ulen)
    v = (
        normal[1] * u[2] - normal[2] * u[1],
        normal[2] * u[0] - normal[0] * u[2],
        normal[0] * u[1] - normal[1] * u[0],
    )
    return (cx, cy, cz), u, v, normal, planarity


def fit_arc_centre(points2d: list[tuple[float, float]]):
    """Coarse-to-fine search for the centre minimising the standard deviation of radii."""
    xs = [p[0] for p in points2d]
    ys = [p[1] for p in points2d]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    lo_x, hi_x = min(xs) - span, max(xs) + span
    lo_y, hi_y = min(ys) - span, max(ys) + span
    best_centre = (_mean(xs), _mean(ys))
    best_spread = float("inf")
    for _ in range(6):
        step_x = (hi_x - lo_x) / 12
        step_y = (hi_y - lo_y) / 12
        for i in range(13):
            for j in range(13):
                cx = lo_x + i * step_x
                cy = lo_y + j * step_y
                radii = [math.hypot(x - cx, y - cy) for x, y in points2d]
                mean = _mean(radii)
                spread = math.sqrt(_mean([(r - mean) ** 2 for r in radii]))
                if spread < best_spread:
                    best_spread = spread
                    best_centre = (cx, cy)
        lo_x, hi_x = best_centre[0] - step_x, best_centre[0] + step_x
        lo_y, hi_y = best_centre[1] - step_y, best_centre[1] + step_y
    return best_centre, best_spread


def angular_span(points2d: list[tuple[float, float]], centre: tuple[float, float]) -> float:
    """Degrees of arc actually occupied, found as the complement of the largest empty gap."""
    angles = sorted(math.degrees(math.atan2(y - centre[1], x - centre[0])) for x, y in points2d)
    if len(angles) < 2:
        return 0.0
    gaps = [angles[i + 1] - angles[i] for i in range(len(angles) - 1)]
    gaps.append(angles[0] + 360.0 - angles[-1])
    return round(360.0 - max(gaps), 2)


def analyse(points: list[tuple[float, float, float]], bins: int = 24) -> dict[str, Any]:
    origin, u, v, normal, planarity = best_fit_plane(points)
    planar = []
    off_plane = []
    for x, y, z in points:
        dx, dy, dz = x - origin[0], y - origin[1], z - origin[2]
        planar.append((dx * u[0] + dy * u[1] + dz * u[2], dx * v[0] + dy * v[1] + dz * v[2]))
        off_plane.append(abs(dx * normal[0] + dy * normal[1] + dz * normal[2]))

    centre, spread = fit_arc_centre(planar)
    radii = [math.hypot(x - centre[0], y - centre[1]) for x, y in planar]
    bend_radius = _mean(radii)
    span = angular_span(planar, centre)

    extent = max(
        max(p[0] for p in planar) - min(p[0] for p in planar),
        max(p[1] for p in planar) - min(p[1] for p in planar),
    )
    centre_distance = math.hypot(centre[0] - _mean([p[0] for p in planar]),
                                 centre[1] - _mean([p[1] for p in planar]))

    buckets: dict[int, list[float]] = {}
    for (x, y), radius in zip(planar, radii):
        key = int(math.degrees(math.atan2(y - centre[1], x - centre[0])) // (360 / bins))
        buckets.setdefault(key, []).append(radius)
    tube = [
        {"bin": key, "tubeRadius": round((max(values) - min(values)) / 2, 6), "samples": len(values)}
        for key, values in sorted(buckets.items())
        if len(values) >= 8
    ]
    tube_radii = [entry["tubeRadius"] for entry in tube]

    return {
        "sampledVertexCount": len(points),
        "planeNormal": [round(component, 5) for component in normal],
        "planarity": round(planarity, 5),
        "maxOffPlaneDistance": round(max(off_plane), 6),
        "bendRadius": round(bend_radius, 6),
        "radiusSpread": round(spread, 6),
        "radiusSpreadOverBendRadius": round(spread / bend_radius, 5) if bend_radius else None,
        "angularSpanDeg": span,
        "centreDistanceOverExtent": round(centre_distance / extent, 5) if extent else None,
        "planarExtent": round(extent, 6),
        "tubeRadius": {
            "min": round(min(tube_radii), 6) if tube_radii else None,
            "max": round(max(tube_radii), 6) if tube_radii else None,
            "mean": round(_mean(tube_radii), 6) if tube_radii else None,
            "bins": tube,
        },
    }


def evaluate(measured: dict[str, Any], expectations: dict[str, Any]) -> dict[str, Any]:
    checks = []

    def record(name: str, value: Any, ok: bool, detail: str) -> None:
        checks.append({"check": name, "value": value, "status": "pass" if ok else "fail", "detail": detail})

    # Checked first, and reported first, because every number after it is computed IN the fitted
    # plane. When the plane is not determined the rest of the report is arithmetic on an arbitrary
    # projection, so a "bend radius" there is uncalibrated rather than merely wrong.
    planarity_min = expectations.get("minPlanarity", 0.35)
    record(
        "planeDetermined",
        measured["planarity"],
        measured["planarity"] >= planarity_min,
        "the sweep must actually determine a plane; a short sweep of a thick tube is a rod, whose "
        "two smaller covariance eigenvalues are both its cross-section, so any plane fitted "
        "through it is arbitrary",
    )

    span_min = expectations.get("minAngularSpanDeg")
    if span_min is not None:
        record(
            "angularSpan",
            measured["angularSpanDeg"],
            measured["angularSpanDeg"] >= span_min,
            f"a hook must subtend at least {span_min} degrees at its fitted centre; a straight "
            "cone cannot",
        )

    centre_max = expectations.get("maxCentreDistanceOverExtent")
    if centre_max is not None:
        value = measured["centreDistanceOverExtent"]
        record(
            "centreDistance",
            value,
            value is not None and value <= centre_max,
            "the fitted centre must sit near the component, not be pushed far away to make a "
            "straight run look like a shallow arc",
        )

    bend = expectations.get("bendRadius")
    if bend is not None:
        tolerance = float(expectations.get("bendRadiusTolerance", 0.1 * bend))
        record(
            "bendRadius",
            measured["bendRadius"],
            abs(measured["bendRadius"] - bend) <= tolerance,
            f"measured bend radius must be within {tolerance} of the reference-derived {bend}",
        )

    tube = expectations.get("tubeRadius")
    if tube is not None and measured["tubeRadius"]["mean"] is not None:
        tolerance = float(expectations.get("tubeRadiusTolerance", 0.2 * tube))
        record(
            "tubeRadius",
            measured["tubeRadius"]["mean"],
            abs(measured["tubeRadius"]["mean"] - tube) <= tolerance,
            f"measured mean tube radius must be within {tolerance} of the reference-derived {tube}",
        )

    residual_max = expectations.get("maxRadiusSpreadOverBendRadius")
    if residual_max is not None:
        value = measured["radiusSpreadOverBendRadius"]
        record(
            "arcResidual",
            value,
            value is not None and value <= residual_max,
            "radial spread relative to the bend radius; a sweep that is not close to a circular "
            "arc will exceed this",
        )

    return {"failures": sum(1 for check in checks if check["status"] == "fail"), "checks": checks}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--geometry", type=Path, required=True)
    parser.add_argument("--component", required=True, help="mesh id or name to analyse")
    parser.add_argument("--expect", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    geometry = _load_json(args.geometry, "geometry")
    meshes = geometry.get("meshes") if isinstance(geometry, dict) else geometry
    if not isinstance(meshes, list):
        raise SystemExit("geometry error: expected a list of meshes or {\"meshes\": [...]}")
    match = None
    for mesh in meshes:
        if isinstance(mesh, dict) and args.component in {mesh.get("id"), mesh.get("name")}:
            match = mesh
            break
    if match is None:
        raise SystemExit(f"geometry error: no mesh named {args.component!r}")
    positions = match.get("positions") or match.get("worldPositions")
    if not isinstance(positions, list) or len(positions) < 30 or len(positions) % 3:
        raise SystemExit(f"geometry error: mesh {args.component!r} has no usable position array")

    points = [
        (positions[index], positions[index + 1], positions[index + 2])
        for index in range(0, len(positions), 3)
    ]
    measured = analyse(points)
    report: dict[str, Any] = {"component": args.component, "measured": measured}
    exit_code = 0
    if args.expect is not None:
        expectations = _load_json(args.expect, "expectations")
        evaluation = evaluate(measured, expectations)
        report["evaluation"] = evaluation
        if evaluation["failures"]:
            exit_code = 1
    report["exitCode"] = exit_code
    if args.out:
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.json or not args.out:
        print(json.dumps(report, indent=2))
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit as exit_error:
        if isinstance(exit_error.code, str):
            print(exit_error.code, file=sys.stderr)
            raise SystemExit(2)
        raise
