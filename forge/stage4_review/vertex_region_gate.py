#!/usr/bin/env python3
"""Gate colour-region boundaries on executed geometry, before any browser render.

WHAT THIS IS FOR. When a subject's identity is carried by flat colour regions with hard boundaries
-- a tuxedo cat's blaze, bib and socks; a livery stripe; a painted marking -- the position of those
boundaries is an identity feature, and identity features are gated, not eyeballed. Neither existing
instrument can do it:

- `divine_eye.py` scores a 64x64 luma grid over the whole frame. A bib boundary a few pixels out
  moves a handful of cells and is lost in the global score.
- `interior_difference.py` measures inside the silhouette, but against a rendered image, so it
  cannot run until a browser has produced pixels -- and by then the spec is already built.

This gate runs on exported geometry: world positions plus the vertex colours the model will
actually be shaded with. It projects those vertices to a review azimuth, computes each colour
region's 2D bounding box normalised to the model's own projected bounding box, and compares that
against the box measured on the reference at the same azimuth. Same units on both sides, and no
renderer involved.

WHAT IT DOES NOT SEE, stated because a clean verdict here is not a clean model:

- It measures a bounding box, not a shape. A region of the right extent and the wrong outline
  passes. Shape is the visual review's job.
- It classifies a vertex by matching its colour to the declared palette. Vertices inside a soft
  boundary fringe match nothing and are reported as `unclassifiedFraction` rather than being
  silently assigned. Read that number before believing the verdict.
- Vertex density is not uniform over a mesh, so `areaFraction` here is a vertex-count fraction and
  is a weaker signal than the box. It is reported, never gated on.
- A region hidden behind another surface at this azimuth is still measured: these are vertices, not
  pixels, and there is no occlusion test.

Exit codes: 0 clean, 1 gate failure, 2 error.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

DEFAULT_COLOR_TOLERANCE = 0.06


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"{label} error: {error}")


def _hex_to_rgb(value: str) -> tuple[float, float, float]:
    number = int(value.lstrip("#"), 16)
    return (((number >> 16) & 255) / 255.0, ((number >> 8) & 255) / 255.0, (number & 255) / 255.0)


def collect_vertices(geometry: Any) -> list[dict[str, Any]]:
    """Flatten an exported geometry payload into per-mesh position/colour arrays.

    Accepts the shape `runtime/scripts/export_mesh_geometry.mjs` emits (a list of meshes, each with
    a flat `positions` array) extended with a matching flat `colors` array.
    """
    meshes = geometry.get("meshes") if isinstance(geometry, dict) else geometry
    if not isinstance(meshes, list):
        raise SystemExit("geometry error: expected a list of meshes or {\"meshes\": [...]}")
    out = []
    for index, mesh in enumerate(meshes):
        if not isinstance(mesh, dict):
            continue
        positions = mesh.get("positions") or mesh.get("worldPositions")
        colors = mesh.get("colors")
        if not isinstance(positions, list) or not positions:
            continue
        if len(positions) % 3:
            raise SystemExit(f"geometry error: mesh {index} positions length is not a multiple of 3")
        if colors is not None:
            if not isinstance(colors, list) or len(colors) != len(positions):
                raise SystemExit(
                    f"geometry error: mesh {index} colors must be the same length as positions"
                )
        out.append(
            {
                "id": mesh.get("id") or mesh.get("name") or f"mesh-{index}",
                "positions": positions,
                "colors": colors,
            }
        )
    if not out:
        raise SystemExit("geometry error: no mesh carried positions")
    return out


def project(positions: list[float], azimuth_degrees: float) -> list[tuple[float, float]]:
    """Rotate about world Y by -azimuth, then drop Z. Azimuth 0 looks along -Z at the model's front."""
    angle = math.radians(-azimuth_degrees)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    points = []
    for index in range(0, len(positions), 3):
        x, y, z = positions[index], positions[index + 1], positions[index + 2]
        points.append((x * cos_a + z * sin_a, y))
    return points


def classify(color: tuple[float, float, float], palette: dict[str, tuple[float, float, float]],
             tolerance: float) -> str | None:
    best_id = None
    best_distance = tolerance
    for region_id, target in palette.items():
        distance = math.sqrt(
            (color[0] - target[0]) ** 2 + (color[1] - target[1]) ** 2 + (color[2] - target[2]) ** 2
        )
        if distance <= best_distance:
            best_distance = distance
            best_id = region_id
    return best_id


def measure(
    meshes: list[dict[str, Any]],
    palette: dict[str, tuple[float, float, float]],
    azimuth: float,
    tolerance: float,
    scope: set[str] | None = None,
) -> dict[str, Any]:
    """Measure colour regions at one azimuth.

    `scope` restricts which MESHES contribute region buckets, while the model's projected bounding
    box is always computed from every mesh. Both halves of that matter: a subject can carry the
    same colour in several unrelated places — a tuxedo cat's white is its bib, its four socks and
    its muzzle at once — so colour alone cannot separate them, and the part they sit on can. And
    normalising to the whole model keeps a scoped measurement in the same coordinates as the
    reference box it is compared against, instead of in the scoped part's own frame.
    """
    all_points: list[tuple[float, float]] = []
    labelled: list[tuple[str | None, tuple[float, float]]] = []
    for mesh in meshes:
        points = project(mesh["positions"], azimuth)
        all_points.extend(points)
        if scope is not None and mesh["id"] not in scope and mesh.get("name") not in scope:
            continue
        colors = mesh["colors"]
        for index, point in enumerate(points):
            if colors is None:
                labelled.append((None, point))
                continue
            color = (colors[index * 3], colors[index * 3 + 1], colors[index * 3 + 2])
            labelled.append((classify(color, palette, tolerance), point))
    if not labelled:
        raise SystemExit(
            "geometry error: the requested mesh scope selected no vertices; check the mesh ids"
        )

    xs = [p[0] for p in all_points]
    ys = [p[1] for p in all_points]
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    if span_x <= 0 or span_y <= 0:
        raise SystemExit("geometry error: projected model has zero extent at this azimuth")
    origin = (min(xs), min(ys))

    buckets: dict[str, list[tuple[float, float]]] = {}
    unclassified = 0
    for label, point in labelled:
        if label is None:
            unclassified += 1
            continue
        buckets.setdefault(label, []).append(point)

    regions = {}
    for region_id, points in buckets.items():
        blobs = projected_blobs(points, origin, span_x, span_y)
        rxs = [(p[0] - origin[0]) / span_x for p in points]
        # Image space runs downward while world Y runs up, so the reference's y0 is the model's
        # TOP. Flipping here keeps both sides of every comparison in reference coordinates.
        rys = [1.0 - (p[1] - origin[1]) / span_y for p in points]
        regions[region_id] = {
            "x0": round(min(rxs), 4),
            "x1": round(max(rxs), 4),
            "y0": round(min(rys), 4),
            "y1": round(max(rys), 4),
            "vertexCount": len(points),
            "areaFraction": round(len(points) / len(labelled), 5),
            "blobs": [
                {
                    "vertexCount": len(members),
                    "centroidX": round(
                        sum((points[i][0] - origin[0]) / span_x for i in members) / len(members), 4
                    ),
                    "centroidY": round(
                        sum(1.0 - (points[i][1] - origin[1]) / span_y for i in members)
                        / len(members), 4
                    ),
                    "x0": round(min((points[i][0] - origin[0]) / span_x for i in members), 4),
                    "x1": round(max((points[i][0] - origin[0]) / span_x for i in members), 4),
                    "y0": round(min(1.0 - (points[i][1] - origin[1]) / span_y for i in members), 4),
                    "y1": round(max(1.0 - (points[i][1] - origin[1]) / span_y for i in members), 4),
                }
                for members in blobs
            ],
        }
    return {
        "azimuth": azimuth,
        "projectedExtent": {"width": span_x, "height": span_y},
        "sampledVertexCount": len(labelled),
        "unclassifiedFraction": round(unclassified / len(labelled), 5) if labelled else 1.0,
        "regions": regions,
    }


# The clustering grid is derived from the point count, not fixed.
#
# A fixed fine grid does not cluster: neighbouring vertices land in non-adjacent cells and every
# vertex becomes its own blob — a 288-point fixture split into 24. A fixed coarse grid merges
# regions that are genuinely apart. Sizing it as sqrt(count)/2 keeps roughly a handful of vertices
# per occupied cell at any mesh density, which is what connectivity needs.
MIN_BLOB_GRID = 6
MAX_BLOB_GRID = 96


def projected_blobs(points: list[tuple[float, float]], origin, span_x, span_y):
    """Split a region's projected points into connected blobs, largest first.

    WHY. The reference side of every comparison is a connected blob found in the image; once the
    model's parts are FUSED into one mesh, colour alone cannot tell the model's equivalent blobs
    apart. A tuxedo cat's white is its bib, its four socks and its muzzle at once, all one colour
    on one mesh, so the measured "sock" box ran from the muzzle to the ground and the comparison
    was meaningless. Clustering the projection puts both sides on the same instrument.

    The grid is coarse on purpose: it is a connectivity test, not a shape measurement, and the box
    is still computed from the real point coordinates rather than from grid cells.
    """
    if not points:
        return []
    grid = max(MIN_BLOB_GRID, min(MAX_BLOB_GRID, int(len(points) ** 0.5 / 2)))
    cells: dict[tuple[int, int], list[int]] = {}
    for index, (x, y) in enumerate(points):
        column = min(grid - 1, max(0, int((x - origin[0]) / span_x * grid)))
        row = min(grid - 1, max(0, int((y - origin[1]) / span_y * grid)))
        cells.setdefault((column, row), []).append(index)

    seen: set[tuple[int, int]] = set()
    blobs = []
    for start in cells:
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        members: list[int] = []
        while stack:
            cell = stack.pop()
            members.extend(cells[cell])
            for step in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                neighbour = (cell[0] + step[0], cell[1] + step[1])
                if neighbour in cells and neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        blobs.append(members)
    blobs.sort(key=len, reverse=True)
    return blobs


def union_box(regions: dict[str, Any], ids: list[str]) -> dict[str, float] | None:
    present = [regions[region_id] for region_id in ids if region_id in regions]
    if not present:
        return None
    return {
        "x0": min(box["x0"] for box in present),
        "x1": max(box["x1"] for box in present),
        "y0": min(box["y0"] for box in present),
        "y1": max(box["y1"] for box in present),
    }


def evaluate(measured: dict[str, Any], expectations: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate expectations against a per-expectation measurement.

    `measured` may be a single measurement (every expectation reads the same regions) or a mapping
    of expectation id to measurement, which is what a scoped run produces.
    """
    results = []
    failures = 0
    for expectation in expectations:
        ids = expectation.get("regions") or [expectation["id"]]
        tolerance = float(expectation.get("tolerance", 0.04))
        expected = expectation["expected"]
        source = measured.get("byExpectation", {}).get(expectation["id"], measured)
        wanted = expectation.get("blobs")
        spatial = expectation.get("blobFilter")
        if spatial is not None:
            # Selection by a DECLARED spatial fact, not by area rank.
            #
            # Rank is not stable between the two sides: on the reference the bib is the largest
            # white blob, but once the model's legs and paws are fused the four socks merge into
            # one band that is larger still, so rank 0 means the bib on one side and the socks on
            # the other. Measured: every sock comparison was off by -0.45 in y0 and every bib
            # comparison by +0.56 or more, all in the same direction — the signature of a swapped
            # correspondence rather than a misplaced boundary.
            #
            # "the socks are the white below the belly line" is a fact about the subject, stated
            # once, and it is independent of the box being tested — so using it to pick the blob
            # does not make the comparison circular.
            kept = []
            for region_id in ids:
                for blob in source["regions"].get(region_id, {}).get("blobs", []):
                    low = spatial.get("centroidYMin", 0.0)
                    high = spatial.get("centroidYMax", 1.0)
                    if low <= blob["centroidY"] <= high:
                        kept.append(blob)
            if not kept:
                results.append({
                    "id": expectation["id"], "status": "missing",
                    "detail": f"no blob of {ids} has its centroid in {spatial}",
                    "availableCentroids": [
                        blob["centroidY"]
                        for region_id in ids
                        for blob in source["regions"].get(region_id, {}).get("blobs", [])
                    ],
                })
                failures += 1
                continue
            actual = {
                "x0": min(box["x0"] for box in kept), "x1": max(box["x1"] for box in kept),
                "y0": min(box["y0"] for box in kept), "y1": max(box["y1"] for box in kept),
            }
        elif wanted is None:
            actual = union_box(source["regions"], ids)
        else:
            # Blob selection mirrors how the reference blob was isolated: by connectivity, then by
            # size rank. Naming a blob that does not exist is a failure, not a silent fallback to
            # the whole region.
            boxes = []
            missing = []
            for region_id in ids:
                blobs = source["regions"].get(region_id, {}).get("blobs", [])
                for rank in wanted:
                    if rank < len(blobs):
                        boxes.append(blobs[rank])
                    else:
                        missing.append(f"{region_id}[{rank}]")
            if missing or not boxes:
                results.append({
                    "id": expectation["id"], "status": "missing",
                    "detail": f"requested blobs not present: {missing or ids}",
                    "availableBlobCounts": {
                        region_id: len(source["regions"].get(region_id, {}).get("blobs", []))
                        for region_id in ids
                    },
                })
                failures += 1
                continue
            actual = {
                "x0": min(box["x0"] for box in boxes), "x1": max(box["x1"] for box in boxes),
                "y0": min(box["y0"] for box in boxes), "y1": max(box["y1"] for box in boxes),
            }
        if actual is None:
            results.append(
                {
                    "id": expectation["id"],
                    "status": "missing",
                    "detail": f"no vertex carried any of the colours for {ids}",
                }
            )
            failures += 1
            continue
        deltas = {edge: round(actual[edge] - expected[edge], 4) for edge in ("x0", "x1", "y0", "y1")}
        worst = max(abs(value) for value in deltas.values())
        status = "pass" if worst <= tolerance else "fail"
        if status == "fail":
            failures += 1
        results.append(
            {
                "id": expectation["id"],
                "status": status,
                "regions": ids,
                "expected": expected,
                "actual": {edge: round(actual[edge], 4) for edge in ("x0", "x1", "y0", "y1")},
                "deltas": deltas,
                "worstDelta": round(worst, 4),
                "tolerance": tolerance,
            }
        )
    return {"failures": failures, "results": results}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--geometry", type=Path, required=True, help="exported mesh geometry JSON")
    parser.add_argument("--palette", type=Path, required=True,
                        help="{regionId: '#rrggbb'} for every region to measure")
    parser.add_argument("--expect", type=Path,
                        help="expectation list; without it the gate only reports measurements")
    parser.add_argument("--azimuth", type=float, default=0.0,
                        help="review azimuth in degrees; 0 looks at the model's front")
    parser.add_argument("--color-tolerance", type=float, default=DEFAULT_COLOR_TOLERANCE)
    parser.add_argument("--max-unclassified", type=float, default=0.25,
                        help="fail if more than this fraction of vertices match no palette colour")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    geometry = _load_json(args.geometry, "geometry")
    palette_raw = _load_json(args.palette, "palette")
    if not isinstance(palette_raw, dict) or not palette_raw:
        raise SystemExit("palette error: expected a non-empty {regionId: '#rrggbb'} object")
    palette = {key: _hex_to_rgb(value) for key, value in palette_raw.items()}

    meshes = collect_vertices(geometry)
    measured = measure(meshes, palette, args.azimuth, args.color_tolerance)

    report: dict[str, Any] = {"measured": measured, "palette": palette_raw}
    exit_code = 0
    if measured["unclassifiedFraction"] > args.max_unclassified:
        report["unclassifiedVerdict"] = (
            f"{measured['unclassifiedFraction']:.3f} of vertices matched no palette colour, above "
            f"the {args.max_unclassified} limit — the measurement below covers less of the model "
            "than it appears to"
        )
        exit_code = 1

    if args.expect is not None:
        expectations = _load_json(args.expect, "expectations")
        if not isinstance(expectations, list):
            raise SystemExit("expectations error: expected a list")
        # An expectation may name the meshes it applies to. Each such expectation gets its own
        # scoped measurement, so a white bib is never accidentally satisfied by a white sock.
        by_expectation: dict[str, Any] = {}
        for expectation in expectations:
            scope = expectation.get("meshes")
            if isinstance(scope, list) and scope:
                by_expectation[expectation["id"]] = measure(
                    meshes, palette, args.azimuth, args.color_tolerance, scope=set(scope)
                )
        measured["byExpectation"] = by_expectation
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
