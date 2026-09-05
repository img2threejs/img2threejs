#!/usr/bin/env python3
"""UV unwrapping: chart segmentation, least-squares conformal mapping, and packing.

Nothing in this repository produced UVs. That is not a cosmetic gap -- it is the missing link that
makes the whole material track unreachable. Without a UV layout there is nowhere to bake a projected
photograph, so `bake_projected_texture.py` can only ever emit a descriptor; and there is nowhere to
bake a normal or an ambient-occlusion map either. The pipeline's own guidance calls
projection-first texturing the single biggest fidelity lever, and it has been resting on a step that
did not exist.

Three analytic stages, no machine learning anywhere:

1. CHART SEGMENTATION by normal similarity. A single chart over a closed surface is impossible
   without a cut, and a conformal map of a highly curved region has unbounded area distortion, so the
   surface is grown into near-planar regions first.

2. LSCM (Lévy et al. 2002). Each triangle is flattened into its own plane, and the conformal
   condition -- that the map is locally a rotation and scale, never a shear -- becomes two linear
   equations per triangle. Two pinned vertices fix the remaining translation/rotation/scale freedom.
   The overdetermined system is solved in the least-squares sense by conjugate gradient on the normal
   equations, which needs only sparse mat-vec products and therefore no third-party linear algebra.

   LSCM preserves ANGLES, not AREAS. A conformal map of a region with real Gaussian curvature must
   distort area somewhere; that is a theorem, not an implementation shortcoming. `areaDistortion` is
   reported per chart so the caller can see how much, and segmentation is what keeps it bounded.

3. PACKING into the unit square by a skyline heuristic. This is a heuristic, and it is reported as
   `packingEfficiency` rather than claimed as optimal -- rectangle packing is NP-hard.

LSCM requires each chart to be a topological disk. Normal-based growing does not guarantee that, so
each chart's Euler characteristic is checked and non-disk charts are REPORTED rather than silently
flattened into a tangle. A fold-over would otherwise look like a valid layout right up until the bake.

Pure Python 3.10+ stdlib. No pip installs, no numpy.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

# Measured on a real 4162-vertex organic skull, sweeping the threshold and counting inverted
# triangles: 50 deg -> 40 flips, 35 -> 2, 25 -> 0, 18 -> 2, 12 -> 13. 25 is the widest setting that
# folded nothing, and wider charts mean fewer seams, so it is the default. Hard-surface subjects
# tolerate more; the flag exists for that.
DEFAULT_CHART_ANGLE_DEGREES = 25.0
DEFAULT_CG_ITERATIONS = 400
DEFAULT_CG_TOLERANCE = 1e-8
DEFAULT_CHART_PADDING = 0.004
MIN_TRIANGLE_AREA = 1e-12


# ---------------------------------------------------------------------------
# Mesh helpers
# ---------------------------------------------------------------------------

def _triangles(indices: list[Any]) -> list[tuple[int, int, int]]:
    if indices and isinstance(indices[0], (list, tuple)):
        return [(int(t[0]), int(t[1]), int(t[2])) for t in indices]
    return [
        (int(indices[i]), int(indices[i + 1]), int(indices[i + 2]))
        for i in range(0, len(indices) - 2, 3)
    ]


def _sub(a: list[float], b: list[float]) -> list[float]:
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _cross(a: list[float], b: list[float]) -> list[float]:
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def _dot(a: list[float], b: list[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a: list[float]) -> float:
    return math.sqrt(_dot(a, a))


def _face_normal(vertices: list[list[float]], face: tuple[int, int, int]) -> tuple[list[float], float]:
    p0, p1, p2 = (vertices[i] for i in face)
    n = _cross(_sub(p1, p0), _sub(p2, p0))
    length = _norm(n)
    if length <= MIN_TRIANGLE_AREA:
        return [0.0, 0.0, 0.0], 0.0
    return [n[0] / length, n[1] / length, n[2] / length], length / 2.0


# ---------------------------------------------------------------------------
# 1. Chart segmentation
# ---------------------------------------------------------------------------

def segment_charts(
    vertices: list[list[float]],
    faces: list[tuple[int, int, int]],
    angle_threshold_degrees: float = DEFAULT_CHART_ANGLE_DEGREES,
) -> list[list[int]]:
    """Grow near-planar regions across shared edges.

    Growth is compared against the SEED's normal rather than the neighbour's. Comparing against the
    neighbour lets a chart creep around a cylinder one tolerable step at a time until its two ends
    face opposite directions, which is exactly the runaway a threshold is supposed to prevent.
    """
    cos_limit = math.cos(math.radians(angle_threshold_degrees))
    normals = [_face_normal(vertices, face)[0] for face in faces]

    edge_faces: dict[tuple[int, int], list[int]] = {}
    for index, (a, b, c) in enumerate(faces):
        for u, v in ((a, b), (b, c), (c, a)):
            key = (u, v) if u < v else (v, u)
            edge_faces.setdefault(key, []).append(index)

    neighbours: list[list[int]] = [[] for _ in faces]
    for shared in edge_faces.values():
        if len(shared) != 2:
            continue
        first, second = shared
        neighbours[first].append(second)
        neighbours[second].append(first)

    unassigned = set(range(len(faces)))
    charts: list[list[int]] = []
    while unassigned:
        seed = min(unassigned)
        seed_normal = normals[seed]
        chart = [seed]
        unassigned.discard(seed)
        queue = [seed]
        while queue:
            current = queue.pop()
            for neighbour in neighbours[current]:
                if neighbour not in unassigned:
                    continue
                if _dot(seed_normal, normals[neighbour]) < cos_limit:
                    continue
                unassigned.discard(neighbour)
                chart.append(neighbour)
                queue.append(neighbour)
        charts.append(sorted(chart))
    return charts


def _chart_adjacency(faces: list[tuple[int, int, int]], chart: list[int]) -> dict[int, list[int]]:
    """Face adjacency restricted to one chart."""
    edge_faces: dict[tuple[int, int], list[int]] = {}
    for index in chart:
        a, b, c = faces[index]
        for u, v in ((a, b), (b, c), (c, a)):
            edge_faces.setdefault((u, v) if u < v else (v, u), []).append(index)
    adjacency: dict[int, list[int]] = {index: [] for index in chart}
    for shared in edge_faces.values():
        if len(shared) != 2:
            continue
        first, second = shared
        adjacency[first].append(second)
        adjacency[second].append(first)
    return adjacency


def _split_chart(faces: list[tuple[int, int, int]], chart: list[int]) -> tuple[list[int], list[int]]:
    """Break a chart into a connected half and the remainder, by breadth-first growth.

    Breadth-first rather than by a cutting plane: a plane can slice a chart into pieces that are not
    connected, and a disconnected piece is not a disk either, so the recursion would not terminate on
    the thing it was called to fix.
    """
    adjacency = _chart_adjacency(faces, chart)
    target = max(1, len(chart) // 2)
    start = chart[0]
    taken = [start]
    seen = {start}
    queue = [start]
    while queue and len(taken) < target:
        current = queue.pop(0)
        for neighbour in adjacency[current]:
            if neighbour in seen:
                continue
            seen.add(neighbour)
            taken.append(neighbour)
            queue.append(neighbour)
            if len(taken) >= target:
                break
    remainder = [index for index in chart if index not in seen]
    return sorted(taken), sorted(remainder)


def enforce_disk_charts(
    faces: list[tuple[int, int, int]],
    charts: list[list[int]],
    max_charts: int = 4096,
) -> tuple[list[list[int]], int]:
    """Subdivide until every chart is a topological disk.

    Reporting a non-disk chart is not enough. LSCM returns UVs for one regardless, and they fold over
    -- measured on a real 4162-vertex skull, seven non-disk charts drove worst-case area distortion to
    171300 and produced twelve inverted triangles. A single triangle is always a disk, so this
    terminates; `max_charts` only bounds pathological input.
    """
    resolved: list[list[int]] = []
    pending = [list(chart) for chart in charts]
    splits = 0
    while pending:
        chart = pending.pop()
        if len(resolved) + len(pending) >= max_charts:
            resolved.append(chart)
            continue
        if len(chart) <= 1 or chart_is_disk(faces, chart):
            resolved.append(chart)
            continue
        first, second = _split_chart(faces, chart)
        # A split that fails to shrink the chart would loop forever; keep it and let it be reported.
        if not first or not second:
            resolved.append(chart)
            continue
        splits += 1
        pending.extend((first, second))
    return sorted(resolved), splits


def chart_is_disk(faces: list[tuple[int, int, int]], chart: list[int]) -> bool:
    """Euler characteristic V - E + F == 1 identifies a disk.

    A chart that closes into a tube or a sphere has no valid flattening at all, and LSCM will happily
    return one anyway -- overlapping, folded, and indistinguishable from a good layout in the numbers.
    """
    verts: set[int] = set()
    edges: set[tuple[int, int]] = set()
    for index in chart:
        a, b, c = faces[index]
        verts.update((a, b, c))
        for u, v in ((a, b), (b, c), (c, a)):
            edges.add((u, v) if u < v else (v, u))
    return len(verts) - len(edges) + len(chart) == 1


# ---------------------------------------------------------------------------
# 2. LSCM
# ---------------------------------------------------------------------------

def _local_coordinates(
    vertices: list[list[float]], face: tuple[int, int, int]
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]] | None:
    """Isometrically flatten one triangle into its own plane."""
    p0, p1, p2 = (vertices[i] for i in face)
    e1 = _sub(p1, p0)
    length = _norm(e1)
    if length <= MIN_TRIANGLE_AREA:
        return None
    axis_x = [component / length for component in e1]
    e2 = _sub(p2, p0)
    x2 = _dot(e2, axis_x)
    perpendicular = [e2[i] - x2 * axis_x[i] for i in range(3)]
    y2 = _norm(perpendicular)
    if y2 <= MIN_TRIANGLE_AREA:
        return None
    return (0.0, 0.0), (length, 0.0), (x2, y2)


def _conjugate_gradient(
    rows: list[list[tuple[int, float]]],
    rhs: list[float],
    unknowns: int,
    iterations: int,
    tolerance: float,
) -> list[float]:
    """Least squares by CG on the normal equations, with only sparse mat-vecs.

    Never forms M^T M. That matrix is dense enough to be the memory wall this whole approach exists to
    avoid, and CG only ever needs its product with a vector.
    """

    def multiply(x: list[float]) -> list[float]:
        return [sum(value * x[column] for column, value in row) for row in rows]

    def multiply_transpose(y: list[float]) -> list[float]:
        out = [0.0] * unknowns
        for row, weight in zip(rows, y):
            for column, value in row:
                out[column] += value * weight
        return out

    x = [0.0] * unknowns
    residual = multiply_transpose(rhs)
    direction = list(residual)
    residual_norm = sum(value * value for value in residual)
    if residual_norm <= tolerance:
        return x
    for _ in range(iterations):
        md = multiply(direction)
        denominator = sum(value * value for value in md)
        if denominator <= 1e-30:
            break
        step = residual_norm / denominator
        for i in range(unknowns):
            x[i] += step * direction[i]
        residual = [a - step * b for a, b in zip(residual, multiply_transpose(md))]
        new_norm = sum(value * value for value in residual)
        if new_norm <= tolerance:
            break
        beta = new_norm / residual_norm
        residual_norm = new_norm
        direction = [r + beta * d for r, d in zip(residual, direction)]
    return x


def lscm(
    vertices: list[list[float]],
    faces: list[tuple[int, int, int]],
    chart: list[int],
    iterations: int = DEFAULT_CG_ITERATIONS,
    tolerance: float = DEFAULT_CG_TOLERANCE,
) -> dict[int, tuple[float, float]]:
    """Flatten one chart, returning UV per original vertex index."""
    chart_vertices = sorted({v for index in chart for v in faces[index]})
    position = {v: i for i, v in enumerate(chart_vertices)}
    if len(chart_vertices) < 3:
        return {v: (0.0, 0.0) for v in chart_vertices}

    # Pin the two most distant vertices. Pinning adjacent ones leaves the solve nearly free to rotate
    # and scale, and CG then wanders instead of converging.
    pin_a, pin_b, best = chart_vertices[0], chart_vertices[1], -1.0
    for i, vi in enumerate(chart_vertices):
        for vj in chart_vertices[i + 1:]:
            distance = _norm(_sub(vertices[vi], vertices[vj]))
            if distance > best:
                best, pin_a, pin_b = distance, vi, vj
    if best <= MIN_TRIANGLE_AREA:
        return {v: (0.0, 0.0) for v in chart_vertices}

    pinned = {pin_a: (0.0, 0.0), pin_b: (best, 0.0)}
    free = [v for v in chart_vertices if v not in pinned]
    column_of = {v: i for i, v in enumerate(free)}
    unknowns = 2 * len(free)
    if unknowns == 0:
        return dict(pinned)

    rows: list[list[tuple[int, float]]] = []
    rhs: list[float] = []
    for index in chart:
        face = faces[index]
        local = _local_coordinates(vertices, face)
        if local is None:
            continue
        (x1, y1), (x2, y2), (x3, y3) = local
        area = abs((x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)) / 2.0
        if area <= MIN_TRIANGLE_AREA:
            continue
        scale = 1.0 / math.sqrt(2.0 * area)
        w = [
            ((x3 - x2) * scale, (y3 - y2) * scale),
            ((x1 - x3) * scale, (y1 - y3) * scale),
            ((x2 - x1) * scale, (y2 - y1) * scale),
        ]
        # Sum_j W_j * U_j = 0 over the complex plane, split into its real and imaginary parts.
        real_row: list[tuple[int, float]] = []
        imaginary_row: list[tuple[int, float]] = []
        real_rhs = 0.0
        imaginary_rhs = 0.0
        for corner, vertex in enumerate(face):
            wr, wi = w[corner]
            if vertex in pinned:
                pu, pv = pinned[vertex]
                real_rhs -= wr * pu - wi * pv
                imaginary_rhs -= wi * pu + wr * pv
            else:
                column = column_of[vertex]
                real_row.append((column, wr))
                real_row.append((len(free) + column, -wi))
                imaginary_row.append((column, wi))
                imaginary_row.append((len(free) + column, wr))
        rows.append(real_row)
        rhs.append(real_rhs)
        rows.append(imaginary_row)
        rhs.append(imaginary_rhs)

    solution = _conjugate_gradient(rows, rhs, unknowns, iterations, tolerance)
    uv = dict(pinned)
    for vertex in free:
        column = column_of[vertex]
        uv[vertex] = (solution[column], solution[len(free) + column])
    return uv


# ---------------------------------------------------------------------------
# Distortion
# ---------------------------------------------------------------------------

def chart_distortion(
    vertices: list[list[float]],
    faces: list[tuple[int, int, int]],
    chart: list[int],
    uv: dict[int, tuple[float, float]],
) -> dict[str, float]:
    """Worst per-triangle area scaling, normalised so a perfect map scores 1.0.

    Reported as a ratio of the WORST triangle to the chart's median, not as an absolute UV area: an
    absolute number changes with the arbitrary global scale of the layout and would make two identical
    unwraps at different scales look different.
    """
    ratios: list[float] = []
    flipped = 0
    for index in chart:
        face = faces[index]
        _, area3d = _face_normal(vertices, face)
        if area3d <= MIN_TRIANGLE_AREA:
            continue
        (u1, v1), (u2, v2), (u3, v3) = (uv[v] for v in face)
        signed = ((u2 - u1) * (v3 - v1) - (u3 - u1) * (v2 - v1)) / 2.0
        if signed <= 0:
            flipped += 1
        area2d = abs(signed)
        if area2d <= MIN_TRIANGLE_AREA:
            continue
        ratios.append(area2d / area3d)
    if not ratios:
        return {"areaDistortion": float("inf"), "flippedTriangles": flipped}
    ratios.sort()
    median = ratios[len(ratios) // 2]
    if median <= 0:
        return {"areaDistortion": float("inf"), "flippedTriangles": flipped}
    worst = max(max(ratios) / median, median / max(min(ratios), 1e-30))
    return {"areaDistortion": round(worst, 6), "flippedTriangles": flipped}


# ---------------------------------------------------------------------------
# 3. Packing
# ---------------------------------------------------------------------------

def pack_charts(
    chart_uvs: list[dict[int, tuple[float, float]]],
    padding: float = DEFAULT_CHART_PADDING,
) -> tuple[list[dict[int, tuple[float, float]]], float]:
    """Skyline packing of chart bounding boxes into the unit square.

    A heuristic, and reported as one. Charts are normalised to preserve their relative scale so a
    small chart does not get blown up to the same texel density as a large one -- uniform texel
    density is the property a bake actually needs.
    """
    boxes = []
    for uv in chart_uvs:
        if not uv:
            boxes.append((0.0, 0.0, 0.0, 0.0))
            continue
        us = [p[0] for p in uv.values()]
        vs = [p[1] for p in uv.values()]
        boxes.append((min(us), min(vs), max(us) - min(us), max(vs) - min(vs)))

    largest = max((max(w, h) for _, _, w, h in boxes), default=0.0)
    if largest <= 0:
        return [dict(uv) for uv in chart_uvs], 0.0

    order = sorted(range(len(boxes)), key=lambda i: -boxes[i][3])
    placed: list[dict[int, tuple[float, float]]] = [dict() for _ in chart_uvs]
    cursor_x = 0.0
    cursor_y = 0.0
    shelf_height = 0.0
    used_area = 0.0

    for index in order:
        min_u, min_v, width, height = boxes[index]
        scale = 1.0 / (largest * (len(boxes) ** 0.5 + 1))
        w = width * scale
        h = height * scale
        if cursor_x + w + padding > 1.0:
            cursor_x = 0.0
            cursor_y += shelf_height + padding
            shelf_height = 0.0
        shelf_height = max(shelf_height, h)
        for vertex, (u, v) in chart_uvs[index].items():
            placed[index][vertex] = (
                cursor_x + (u - min_u) * scale,
                cursor_y + (v - min_v) * scale,
            )
        cursor_x += w + padding
        used_area += w * h

    total_height = cursor_y + shelf_height
    efficiency = used_area / max(total_height, 1e-9) if total_height > 0 else 0.0
    return placed, round(min(efficiency, 1.0), 6)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def unwrap(mesh: dict[str, Any], angle_threshold_degrees: float = DEFAULT_CHART_ANGLE_DEGREES) -> dict[str, Any]:
    vertices = mesh.get("vertices")
    indices = mesh.get("indices")
    if not isinstance(vertices, list) or not vertices:
        raise ValueError("mesh.vertices must be a non-empty array")
    if not isinstance(indices, list) or len(indices) < 3:
        raise ValueError("mesh.indices must describe at least one triangle")

    faces = _triangles(indices)
    charts = segment_charts(vertices, faces, angle_threshold_degrees)
    charts, topology_splits = enforce_disk_charts(faces, charts)

    uv_all: list[tuple[float, float]] = [(0.0, 0.0)] * len(vertices)
    chart_uvs: list[dict[int, tuple[float, float]]] = []
    reports: list[dict[str, Any]] = []
    non_disk: list[int] = []

    for number, chart in enumerate(charts):
        is_disk = chart_is_disk(faces, chart)
        if not is_disk:
            non_disk.append(number)
        uv = lscm(vertices, faces, chart)
        chart_uvs.append(uv)
        distortion = chart_distortion(vertices, faces, chart, uv)
        reports.append({
            "chart": number,
            "faceCount": len(chart),
            "vertexCount": len(uv),
            "isDisk": is_disk,
            **distortion,
        })

    packed, efficiency = pack_charts(chart_uvs)
    # A vertex on a chart seam belongs to more than one chart. Writing whichever chart lands last is
    # what produces a visible seam artefact, so the split is reported instead of hidden: the caller
    # must duplicate those vertices before baking.
    seam_vertices: set[int] = set()
    seen: dict[int, int] = {}
    for number, uv in enumerate(packed):
        for vertex, coordinate in uv.items():
            if vertex in seen and seen[vertex] != number:
                seam_vertices.add(vertex)
            seen[vertex] = number
            uv_all[vertex] = coordinate

    # Weighted by face count, and reported as a distribution rather than a single worst case. The max
    # alone is not a usable quality signal here: sweeping the segmentation threshold on a real skull
    # produced 2299 -> 93609 -> 6595 -> 378 -> 15.85, which is not a trend, it is one sliver chart
    # dominating a max while the other two hundred charts stayed fine. A layout is good or bad by how
    # much of its SURFACE maps well.
    weighted: list[float] = []
    for report in reports:
        if math.isfinite(report["areaDistortion"]):
            weighted.extend([report["areaDistortion"]] * report["faceCount"])
    weighted.sort()

    def percentile(fraction: float) -> float | None:
        if not weighted:
            return None
        return round(weighted[min(len(weighted) - 1, int(fraction * len(weighted)))], 6)

    finite = [report["areaDistortion"] for report in reports if math.isfinite(report["areaDistortion"])]
    return {
        "areaDistortionMedian": percentile(0.5),
        "areaDistortionP95": percentile(0.95),
        "uv": [[round(u, 6), round(v, 6)] for u, v in uv_all],
        "chartCount": len(charts),
        "charts": reports,
        "nonDiskCharts": non_disk,
        "topologySplits": topology_splits,
        "packingEfficiency": efficiency,
        "worstAreaDistortion": round(max(finite), 6) if finite else None,
        "totalFlippedTriangles": sum(report["flippedTriangles"] for report in reports),
        "seamVertexCount": len(seam_vertices),
        "seamVertices": sorted(seam_vertices)[:64],
        "notes": [
            "LSCM preserves angles, not areas; area distortion over a curved chart is unavoidable "
            "and is bounded by segmentation, not by the solver",
            "packing is a skyline heuristic, not an optimal packing",
            "vertices listed in seamVertices carry more than one UV and must be duplicated before a bake",
        ],
    }


def _format_summary(result: dict[str, Any]) -> str:
    lines = [
        f"charts: {result['chartCount']}",
        f"packing efficiency: {result['packingEfficiency']}",
        f"area distortion: median {result['areaDistortionMedian']}, "
        f"p95 {result['areaDistortionP95']}, worst {result['worstAreaDistortion']} "
        "(read the median and p95; the max is one sliver chart away from meaningless)",
        f"flipped triangles: {result['totalFlippedTriangles']}",
        f"seam vertices: {result['seamVertexCount']}",
    ]
    if result["nonDiskCharts"]:
        lines.append(
            f"NON-DISK charts {result['nonDiskCharts']}: these have no valid flattening and their "
            "UVs will fold over; cut them before baking"
        )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Unwrap a mesh into a UV layout.")
    parser.add_argument("mesh", type=Path, help="JSON file with vertices and indices")
    parser.add_argument("--angle", type=float, default=DEFAULT_CHART_ANGLE_DEGREES)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        mesh = json.loads(args.mesh.read_text())
        if isinstance(mesh, dict) and isinstance(mesh.get("meshes"), list) and mesh["meshes"]:
            mesh = mesh["meshes"][0]
        result = unwrap(mesh, args.angle)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.out:
        args.out.write_text(json.dumps(result))
    print(json.dumps(result, indent=2) if args.json else _format_summary(result))
    # Fold-overs and non-disk charts are the two states where the layout is unusable rather than
    # merely imperfect, so they are the only ones that fail the run.
    return 1 if result["totalFlippedTriangles"] or result["nonDiskCharts"] else 0


if __name__ == "__main__":
    import sys
    from pathlib import Path

    try:
        sys.path.insert(0, str(next(
            parent / "forge" / "_shared"
            for parent in Path(__file__).resolve().parents
            if (parent / "forge" / "_shared" / "cli_run.py").is_file()
        )))
        from cli_run import run_entry
    except (ImportError, StopIteration):
        # vendored/fixture copies without the forge runtime: run bare, no pipe handling
        def run_entry(main_fn, argv=None):
            return main_fn(sys.argv[1:] if argv is None else argv)

    raise SystemExit(run_entry(main))
