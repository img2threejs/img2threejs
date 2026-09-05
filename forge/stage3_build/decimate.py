#!/usr/bin/env python3
"""Mesh decimation by quadric error metric, so an lodPlan can be produced rather than only declared.

`lodPlan` is validated in three places in this repository and generated in none. A validated
declaration is a promise about tiers that nothing ever built; `measure_geometry_integrity` reports
`lodPlanValid` and the tiers behind it do not exist.

Quadric error metrics (Garland & Heckbert 1997): every vertex accumulates the squared-distance-to-plane
form of the faces around it, as a symmetric 4x4. Collapsing an edge costs the quadric sum evaluated at
the merged position, so the cheapest collapse is the one that moves the surface least. Collapses are
taken in cost order, and each one refreshes the costs of the edges it touched.

Two collapses are refused outright, because both produce a mesh that still passes a triangle count but
is no longer a surface:

* a collapse that would FLIP a face -- the neighbouring normal reversing means the surface folded
  through itself, exactly the defect `self_intersection.py` exists to catch, and creating it here
  while reporting a successful decimation would be the pipeline sabotaging its own gate;
* a collapse on a BOUNDARY edge -- an open border erodes inward and the silhouette visibly retreats,
  which is the classic "the LOD is smaller than the original" artefact.

Pure Python 3.10+ stdlib. No pip installs, no numpy.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import sys
from pathlib import Path
from typing import Any

DEFAULT_TARGET_RATIO = 0.5
MIN_TRIANGLES = 4


def _triangles(indices: Any) -> list[tuple[int, int, int]]:
    if indices and isinstance(indices[0], (list, tuple)):
        return [(int(t[0]), int(t[1]), int(t[2])) for t in indices]
    return [
        (int(indices[i]), int(indices[i + 1]), int(indices[i + 2]))
        for i in range(0, len(indices) - 2, 3)
    ]


def _plane(p0, p1, p2) -> tuple[float, float, float, float] | None:
    e1 = [p1[i] - p0[i] for i in range(3)]
    e2 = [p2[i] - p0[i] for i in range(3)]
    n = [
        e1[1] * e2[2] - e1[2] * e2[1],
        e1[2] * e2[0] - e1[0] * e2[2],
        e1[0] * e2[1] - e1[1] * e2[0],
    ]
    length = math.sqrt(sum(component * component for component in n))
    if length <= 1e-12:
        return None
    n = [component / length for component in n]
    return n[0], n[1], n[2], -(n[0] * p0[0] + n[1] * p0[1] + n[2] * p0[2])


def _quadric(plane: tuple[float, float, float, float]) -> list[float]:
    a, b, c, d = plane
    # Upper triangle of the symmetric 4x4, in row-major order.
    return [a * a, a * b, a * c, a * d, b * b, b * c, b * d, c * c, c * d, d * d]


def _add(q1: list[float], q2: list[float]) -> list[float]:
    return [x + y for x, y in zip(q1, q2)]


def _error(q: list[float], v: list[float]) -> float:
    x, y, z = v
    return (
        q[0] * x * x + 2 * q[1] * x * y + 2 * q[2] * x * z + 2 * q[3] * x
        + q[4] * y * y + 2 * q[5] * y * z + 2 * q[6] * y
        + q[7] * z * z + 2 * q[8] * z
        + q[9]
    )


def decimate(
    mesh: dict[str, Any],
    target_ratio: float = DEFAULT_TARGET_RATIO,
) -> dict[str, Any]:
    vertices = [list(v) for v in mesh["vertices"]]
    faces = _triangles(mesh["indices"])
    if not faces:
        raise ValueError("mesh has no triangles")
    if not 0.0 < target_ratio <= 1.0:
        raise ValueError("target_ratio must be in (0, 1]")

    original_count = len(faces)
    target_count = max(MIN_TRIANGLES, int(original_count * target_ratio))

    alive = [True] * len(faces)
    vertex_faces: list[set[int]] = [set() for _ in vertices]
    for index, face in enumerate(faces):
        for vertex in face:
            vertex_faces[vertex].add(index)

    quadrics = [[0.0] * 10 for _ in vertices]
    for index, face in enumerate(faces):
        plane = _plane(*(vertices[i] for i in face))
        if plane is None:
            alive[index] = False
            continue
        q = _quadric(plane)
        for vertex in face:
            quadrics[vertex] = _add(quadrics[vertex], q)

    edge_faces: dict[tuple[int, int], int] = {}
    for index, face in enumerate(faces):
        a, b, c = face
        for u, v in ((a, b), (b, c), (c, a)):
            key = (u, v) if u < v else (v, u)
            edge_faces[key] = edge_faces.get(key, 0) + 1
    boundary = {edge for edge, count in edge_faces.items() if count == 1}

    def cost(u: int, v: int) -> tuple[float, list[float]]:
        # Midpoint rather than the quadric-optimal position: solving the 3x3 needs an inverse that is
        # singular on flat regions, and the midpoint keeps the collapse on the original surface, which
        # matters more here than the last few percent of accuracy.
        merged = [(vertices[u][i] + vertices[v][i]) / 2 for i in range(3)]
        return _error(_add(quadrics[u], quadrics[v]), merged), merged

    heap: list[tuple[float, int, int]] = []
    for (u, v) in edge_faces:
        if (u, v) in boundary:
            continue
        heapq.heappush(heap, (cost(u, v)[0], u, v))

    removed = 0
    refused_flip = 0
    refused_boundary = len(boundary)
    live_faces = sum(1 for flag in alive if flag)

    while heap and live_faces > target_count:
        _, u, v = heapq.heappop(heap)
        if not vertex_faces[u] or not vertex_faces[v]:
            continue
        if u == v:
            continue
        shared = vertex_faces[u] & vertex_faces[v]
        if not any(alive[index] for index in shared):
            continue
        _, merged = cost(u, v)

        # Would any surviving face flip? Compare each affected face's normal before and after.
        flips = False
        for index in (vertex_faces[u] | vertex_faces[v]) - shared:
            if not alive[index]:
                continue
            face = faces[index]
            before = _plane(*(vertices[i] for i in face))
            after_points = [merged if i in (u, v) else vertices[i] for i in face]
            after = _plane(*after_points)
            if before is None or after is None:
                flips = True
                break
            if before[0] * after[0] + before[1] * after[1] + before[2] * after[2] < 0.0:
                flips = True
                break
        if flips:
            refused_flip += 1
            continue

        vertices[u] = merged
        quadrics[u] = _add(quadrics[u], quadrics[v])
        for index in shared:
            if alive[index]:
                alive[index] = False
                live_faces -= 1
                removed += 1
        for index in list(vertex_faces[v]):
            if not alive[index]:
                continue
            faces[index] = tuple(u if i == v else i for i in faces[index])  # type: ignore[assignment]
            vertex_faces[u].add(index)
        vertex_faces[v] = set()

        for index in list(vertex_faces[u]):
            if not alive[index]:
                continue
            for w in faces[index]:
                if w != u:
                    heapq.heappush(heap, (cost(u, w)[0], u, w))

    kept = [faces[index] for index in range(len(faces)) if alive[index]]
    used = sorted({vertex for face in kept for vertex in face})
    remap = {old: new for new, old in enumerate(used)}
    out_vertices = [vertices[old] for old in used]
    out_indices: list[int] = []
    for face in kept:
        out_indices.extend(remap[vertex] for vertex in face)

    return {
        "vertices": out_vertices,
        "indices": out_indices,
        "originalTriangleCount": original_count,
        "triangleCount": len(kept),
        "targetTriangleCount": target_count,
        "achievedRatio": round(len(kept) / original_count, 6),
        "collapsesRefusedForFlip": refused_flip,
        "boundaryEdgesProtected": refused_boundary,
        # Reported because a decimation that stops short is not a failure but IS something the caller
        # must know: on a mesh whose collapses would all flip a face, the honest outcome is fewer
        # triangles removed, not a folded mesh that hits the number.
        "reachedTarget": len(kept) <= target_count,
    }


def build_lod_plan(
    mesh: dict[str, Any],
    ratios: list[float],
    distances: list[float],
) -> dict[str, Any]:
    """Generate the tiers `lodPlan` has only ever declared.

    Tiers must have strictly increasing distance and strictly decreasing triangle count -- that is what
    `_has_reduced_lod_tiers` in geometry_integrity.py already enforces, so a plan generated here has to
    satisfy the validator that until now had nothing real to validate.
    """
    if len(ratios) != len(distances):
        raise ValueError("ratios and distances must be the same length")
    tiers = []
    meshes = []
    for ratio, distance in sorted(zip(ratios, distances), key=lambda pair: pair[1]):
        result = decimate(mesh, ratio)
        tiers.append({"distance": distance, "triangleCount": result["triangleCount"]})
        meshes.append(result)
    for earlier, later in zip(tiers, tiers[1:]):
        if later["triangleCount"] >= earlier["triangleCount"]:
            raise ValueError(
                f"tier at distance {later['distance']} has {later['triangleCount']} triangles, not "
                f"fewer than {earlier['triangleCount']} at {earlier['distance']}; the requested "
                "ratios do not produce a monotonic plan"
            )
    return {"lodPlan": tiers, "meshes": meshes}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Decimate a mesh by quadric error metric.")
    parser.add_argument("mesh", type=Path)
    parser.add_argument("--ratio", type=float, default=DEFAULT_TARGET_RATIO)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        mesh = json.loads(args.mesh.read_text())
        if isinstance(mesh, dict) and isinstance(mesh.get("meshes"), list) and mesh["meshes"]:
            mesh = mesh["meshes"][0]
        result = decimate(mesh, args.ratio)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.out:
        args.out.write_text(json.dumps(result))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"{result['originalTriangleCount']} -> {result['triangleCount']} triangles "
            f"(ratio {result['achievedRatio']}), {result['collapsesRefusedForFlip']} collapses "
            f"refused to avoid folding the surface"
        )
    return 0


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
