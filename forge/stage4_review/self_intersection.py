#!/usr/bin/env python3
"""Detect a surface that has folded through its own volume, on a mesh whose topology is valid.

`geometry_integrity.mesh_edge_counts` counts `boundaryEdges` (an edge used by one triangle) and
`nonManifoldEdges` (an edge used by more than two). Those are the only geometry facts any gate in
this repository checks, and they are both topological. A real production defect walked straight
through them: a character's eye-socket recess was pushed deeper than the skull's local half-depth,
so the front surface passed THROUGH the back of the skull and emerged out the occiput. From behind
there was a clean hole through the head. The mesh was still perfectly closed -- boundaryEdges 0,
nonManifoldEdges 0 -- because pushing existing vertices around changes no connectivity at all. Every
existing gate passed it.

Self-intersection is a GEOMETRIC defect on a TOPOLOGICALLY VALID mesh. Edge counts cannot see it,
by construction. This module is the check that can.

Method: ray parity. Take a point on the surface, step a small epsilon along its outward direction,
and ask whether that point -- which by definition should be just OUTSIDE the object -- is in fact
inside the object's own volume. On a closed surface the answer is decided by counting how many of
the mesh's own triangles a ray from that point crosses: odd means inside. A point that is supposed
to be outside its own surface but measures inside can only mean the surface folded through itself
there.

Everything here is deterministic. No `random`, no sampling jitter, no wall-clock or hash-order
dependence: the same mesh yields the same verdict and the same reported positions every run, which
is what makes a gate's output reviewable.

Pure Python 3.10+ stdlib. No numpy, no pip installs.
"""

from __future__ import annotations

import argparse
import json
import sys
from math import sqrt
from pathlib import Path
from typing import Any

# A gate that quietly inspects a tenth of a mesh and then reports "clean" is worse than no gate, so
# this budget is a cap on WORK, never on what the result claims: every result carries
# sampledVertexCount / totalVertexCount / samplingStride so the coverage is on the face of it.
MAX_SAMPLED_VERTICES = 4000

# The offset distance is a fraction of the mesh's bounding-box diagonal rather than an absolute
# number, because an absolute epsilon is wrong at every scale but one: 0.001 units is a huge step
# across a 0.01-wide gem and invisible on a 1000-unit terrain. Tied to the diagonal it means the
# same thing on both. 1e-3 of the diagonal clears floating-point noise on the surface the point came
# from while staying far below any modelled feature.
EPSILON_BBOX_FRACTION = 1e-3

# Hits below this |dot(ray direction, face normal)| are edge-on: the intersection maths is dividing
# by a near-zero determinant and the answer is numerically meaningless. Such a sample is reported
# undecided rather than guessed at.
GRAZING_COS_THRESHOLD = 1e-4

# Same idea one step earlier: if the ray direction is this close to tangent to the surface AT the
# sample, the ray skims its own neighbourhood and the parity it returns is not trustworthy.
TANGENT_COS_THRESHOLD = 1e-3

# A hit this close to a triangle's edge is shared with the neighbouring triangle, so it is either
# double-counted or missed depending on rounding. Deliberately tight -- it should fire on genuine
# degeneracy, not on ordinary hits.
BARYCENTRIC_EDGE_TOLERANCE = 1e-9

# Fixed, mutually non-parallel, and deliberately not axis-aligned: axis-aligned rays lie exactly in
# the plane of the axis-aligned quads that box-derived geometry is full of, which is the single most
# common way a parity count goes wrong. Three directions give a majority; one bad ray cannot decide
# a sample on its own.
_RAW_RAY_DIRECTIONS: tuple[tuple[float, float, float], ...] = (
    (1.0, 0.37, 0.19),
    (0.23, 1.0, -0.41),
    (-0.47, 0.29, 1.0),
)

WORST_REGION_LIMIT = 8

# Grid resolution per axis. Above this the bucket dictionary costs more to build than the triangle
# tests it saves.
GRID_MAX_RESOLUTION = 256

# A triangle whose footprint covers more cells than this is filed once in an "everywhere" list and
# tested against every sample, instead of being copied into thousands of buckets.
GRID_OVERSIZED_CELL_LIMIT = 1024

# Vertices are welded by rounded position at this precision, matching `geometry_integrity`'s own
# vertex identity, so that seam-duplicated vertices sharing a position are treated as one point.
WELD_PRECISION = 6

Vector3 = tuple[float, float, float]


def _normalize(vector: Vector3) -> Vector3 | None:
    length = sqrt(vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2])
    if length <= 1e-15:
        return None
    return (vector[0] / length, vector[1] / length, vector[2] / length)


RAY_DIRECTIONS: tuple[Vector3, ...] = tuple(
    direction for direction in (_normalize(raw) for raw in _RAW_RAY_DIRECTIONS) if direction is not None
)


def _as_point(value: Any) -> Vector3:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        raise ValueError("vertices must contain 3D positions")
    return (float(value[0]), float(value[1]), float(value[2]))


def _triangles(indices: Any) -> list[tuple[int, int, int]]:
    """Accept both index encodings `geometry_integrity` accepts: flat, or grouped in threes."""
    if not isinstance(indices, list) or not indices:
        raise ValueError("indices must be a non-empty array")
    if isinstance(indices[0], (list, tuple)):
        groups = indices
    else:
        if len(indices) % 3 != 0:
            raise ValueError("flat triangle indices must be a multiple of three")
        groups = [indices[i:i + 3] for i in range(0, len(indices), 3)]
    triangles: list[tuple[int, int, int]] = []
    for group in groups:
        if not isinstance(group, (list, tuple)) or len(group) != 3:
            raise ValueError("triangle indices must contain groups of three")
        triangles.append((int(group[0]), int(group[1]), int(group[2])))
    return triangles


class _DirectionGrid:
    """Triangles bucketed by their footprint in the plane perpendicular to one ray direction.

    Testing every sample against every triangle is ~1.5e9 operations on a 30k-vertex / 50k-triangle
    mesh, which is not a check anyone will run. All rays of a given pass share one direction, so the
    problem collapses to two dimensions: project every triangle onto the plane perpendicular to that
    direction, bucket it by the cells its 2D bounding box covers, and a sample only ever tests the
    triangles filed in its own cell. The grid is built once per direction and reused for every
    sample, so the build cost is amortised across the whole mesh.
    """

    def __init__(self, direction: Vector3, vertices: list[Vector3], triangles: list[tuple[int, int, int]]) -> None:
        helper: Vector3 = (0.0, 0.0, 1.0) if abs(direction[2]) < 0.9 else (1.0, 0.0, 0.0)
        axis_u = _normalize((
            helper[1] * direction[2] - helper[2] * direction[1],
            helper[2] * direction[0] - helper[0] * direction[2],
            helper[0] * direction[1] - helper[1] * direction[0],
        ))
        if axis_u is None:  # unreachable for a unit direction, but a None basis would be silent nonsense
            raise ValueError("cannot build a basis for the ray direction")
        axis_v = (
            direction[1] * axis_u[2] - direction[2] * axis_u[1],
            direction[2] * axis_u[0] - direction[0] * axis_u[2],
            direction[0] * axis_u[1] - direction[1] * axis_u[0],
        )
        self._u = axis_u
        self._v = axis_v
        self.oversized: list[int] = []
        self._cells: dict[tuple[int, int], list[int]] = {}

        ux, uy, uz = axis_u
        vx, vy, vz = axis_v
        boxes: list[tuple[float, float, float, float]] = []
        min_u = min_v = float("inf")
        max_u = max_v = float("-inf")
        for a, b, c in triangles:
            pa, pb, pc = vertices[a], vertices[b], vertices[c]
            au = pa[0] * ux + pa[1] * uy + pa[2] * uz
            bu = pb[0] * ux + pb[1] * uy + pb[2] * uz
            cu = pc[0] * ux + pc[1] * uy + pc[2] * uz
            av = pa[0] * vx + pa[1] * vy + pa[2] * vz
            bv = pb[0] * vx + pb[1] * vy + pb[2] * vz
            cv = pc[0] * vx + pc[1] * vy + pc[2] * vz
            lo_u, hi_u = min(au, bu, cu), max(au, bu, cu)
            lo_v, hi_v = min(av, bv, cv), max(av, bv, cv)
            boxes.append((lo_u, lo_v, hi_u, hi_v))
            min_u = lo_u if lo_u < min_u else min_u
            max_u = hi_u if hi_u > max_u else max_u
            min_v = lo_v if lo_v < min_v else min_v
            max_v = hi_v if hi_v > max_v else max_v

        resolution = max(1, min(GRID_MAX_RESOLUTION, int(sqrt(len(triangles))) or 1))
        span_u = max_u - min_u
        span_v = max_v - min_v
        self._resolution = resolution
        self._min_u = min_u
        self._min_v = min_v
        # A zero span means the whole mesh projects to a line or a point along that axis; one cell
        # wide is then the correct answer rather than a division by zero.
        self._cell_u = span_u / resolution if span_u > 0.0 else 1.0
        self._cell_v = span_v / resolution if span_v > 0.0 else 1.0

        cells = self._cells
        for index, (lo_u, lo_v, hi_u, hi_v) in enumerate(boxes):
            cu0 = self._cell_index(lo_u, self._min_u, self._cell_u)
            cu1 = self._cell_index(hi_u, self._min_u, self._cell_u)
            cv0 = self._cell_index(lo_v, self._min_v, self._cell_v)
            cv1 = self._cell_index(hi_v, self._min_v, self._cell_v)
            if (cu1 - cu0 + 1) * (cv1 - cv0 + 1) > GRID_OVERSIZED_CELL_LIMIT:
                self.oversized.append(index)
                continue
            for cu in range(cu0, cu1 + 1):
                for cv in range(cv0, cv1 + 1):
                    cells.setdefault((cu, cv), []).append(index)

    def _cell_index(self, value: float, origin: float, size: float) -> int:
        cell = int((value - origin) / size)
        if cell < 0:
            return 0
        if cell >= self._resolution:
            return self._resolution - 1
        return cell

    def candidates(self, point: Vector3) -> list[int]:
        """Triangles whose footprint covers this point's cell. Everything else cannot be hit."""
        ux, uy, uz = self._u
        vx, vy, vz = self._v
        pu = point[0] * ux + point[1] * uy + point[2] * uz
        pv = point[0] * vx + point[1] * vy + point[2] * vz
        # Clamped exactly as insertion clamps, and for the same reason. A sample projecting onto the
        # grid's outer edge -- the vertex that defines the maximum, plus its epsilon offset -- lands
        # one cell past the end; treating that as "off the grid, nothing to test" silently drops
        # every triangle in the boundary cell and returns a confident "outside". Clamping costs a
        # few extra triangle tests for points that really are past the footprint, which is the right
        # side to be wrong on.
        cu = self._cell_index(pu, self._min_u, self._cell_u)
        cv = self._cell_index(pv, self._min_v, self._cell_v)
        cell = self._cells.get((cu, cv))
        if cell is None:
            return self.oversized
        if not self.oversized:
            return cell
        return cell + self.oversized


def _count_crossings(
    origin: Vector3,
    direction: Vector3,
    grid: _DirectionGrid,
    prepared: list[tuple[float, ...]],
    excluded: set[int],
    t_min: float,
) -> tuple[int, bool]:
    """Möller-Trumbore parity count. Returns (crossings, reliable).

    `reliable` goes false the moment a candidate triangle is near enough to edge-on that the
    determinant cannot be trusted, or a hit lands on a triangle edge where it belongs equally to two
    triangles. Those cases are handed back to the caller as undecided; they are never rounded into a
    verdict.
    """
    ox, oy, oz = origin
    dx, dy, dz = direction
    crossings = 0
    for index in grid.candidates(origin):
        if index in excluded:
            continue
        (ax, ay, az, e1x, e1y, e1z, e2x, e2y, e2z, normal_length) = prepared[index]
        px = dy * e2z - dz * e2y
        py = dz * e2x - dx * e2z
        pz = dx * e2y - dy * e2x
        det = e1x * px + e1y * py + e1z * pz
        # det == dot(direction, e1 x e2), so |det| / |e1 x e2| is exactly the cosine between the ray
        # and the face normal -- the edge-on test, for free, with no square root.
        limit = GRAZING_COS_THRESHOLD * normal_length
        if -limit < det < limit:
            return 0, False
        inv_det = 1.0 / det
        tx = ox - ax
        ty = oy - ay
        tz = oz - az
        u = (tx * px + ty * py + tz * pz) * inv_det
        if u < -BARYCENTRIC_EDGE_TOLERANCE or u > 1.0 + BARYCENTRIC_EDGE_TOLERANCE:
            continue
        qx = ty * e1z - tz * e1y
        qy = tz * e1x - tx * e1z
        qz = tx * e1y - ty * e1x
        v = (dx * qx + dy * qy + dz * qz) * inv_det
        if v < -BARYCENTRIC_EDGE_TOLERANCE or u + v > 1.0 + BARYCENTRIC_EDGE_TOLERANCE:
            continue
        t = (e2x * qx + e2y * qy + e2z * qz) * inv_det
        if t < t_min:
            continue
        if (
            u < BARYCENTRIC_EDGE_TOLERANCE
            or v < BARYCENTRIC_EDGE_TOLERANCE
            or u + v > 1.0 - BARYCENTRIC_EDGE_TOLERANCE
        ):
            return 0, False
        crossings += 1
    return crossings, True


def _error_result(name: str, message: str, total_vertices: int = 0) -> dict[str, Any]:
    """Degenerate input is reported, not raised: one broken mesh must not abort a whole batch."""
    return {
        "name": name,
        "analyzed": False,
        "error": message,
        "selfIntersecting": False,
        "insideVertexCount": 0,
        "undecidedVertexCount": 0,
        "outsideVertexCount": 0,
        "sampledVertexCount": 0,
        "totalVertexCount": total_vertices,
        "samplingStride": 0,
        "triangleCount": 0,
        "degenerateTriangleCount": 0,
        "worstRegions": [],
        "normalSource": None,
        "normalFallbackCount": 0,
        "epsilon": None,
    }


def analyze_mesh(
    mesh: dict[str, Any],
    *,
    max_samples: int = MAX_SAMPLED_VERTICES,
    epsilon: float | None = None,
) -> dict[str, Any]:
    """Report whether a mesh's surface passes through its own volume.

    `mesh` takes the same shape `geometry_integrity` uses:
    `{"vertices": [[x, y, z], ...], "indices": [...] or [[a, b, c], ...],
      "normals": [[x, y, z], ...] (optional), "name": str (optional)}`.

    `epsilon` is the distance the sample point is stepped along its outward direction. It defaults
    to `EPSILON_BBOX_FRACTION` of the bounding-box diagonal so it scales with the model instead of
    being an absolute number that is correct at exactly one scale.
    """
    name = str(mesh.get("name") or mesh.get("id") or "mesh")
    raw_vertices = mesh.get("vertices")
    if not isinstance(raw_vertices, list) or not raw_vertices:
        return _error_result(name, "mesh has no vertices")
    try:
        vertices = [_as_point(vertex) for vertex in raw_vertices]
    except (TypeError, ValueError) as exc:
        return _error_result(name, f"invalid vertex data: {exc}", len(raw_vertices))
    try:
        triangles = _triangles(mesh.get("indices"))
    except (TypeError, ValueError) as exc:
        return _error_result(name, str(exc), len(vertices))
    vertex_count = len(vertices)
    if any(index < 0 or index >= vertex_count for triangle in triangles for index in triangle):
        return _error_result(name, "triangle index out of range", vertex_count)

    lo = [min(vertex[axis] for vertex in vertices) for axis in range(3)]
    hi = [max(vertex[axis] for vertex in vertices) for axis in range(3)]
    diagonal = sqrt(sum((hi[axis] - lo[axis]) ** 2 for axis in range(3)))
    if diagonal <= 0.0:
        return _error_result(name, "mesh has zero extent", vertex_count)
    offset = float(epsilon) if epsilon is not None else EPSILON_BBOX_FRACTION * diagonal
    if offset <= 0.0:
        return _error_result(name, "epsilon must be positive", vertex_count)
    # Hits closer than this are the surface the sample point is standing on, reached through
    # floating-point noise rather than through a real second surface.
    t_min = offset * 1e-3

    prepared: list[tuple[float, ...]] = []
    live: list[tuple[int, int, int]] = []
    degenerate = 0
    for a, b, c in triangles:
        pa, pb, pc = vertices[a], vertices[b], vertices[c]
        e1 = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
        e2 = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
        cross = (
            e1[1] * e2[2] - e1[2] * e2[1],
            e1[2] * e2[0] - e1[0] * e2[2],
            e1[0] * e2[1] - e1[1] * e2[0],
        )
        normal_length = sqrt(cross[0] * cross[0] + cross[1] * cross[1] + cross[2] * cross[2])
        if normal_length <= 0.0:
            # A zero-area triangle bounds no volume and its plane is undefined; counting it would
            # only corrupt the parity.
            degenerate += 1
            continue
        prepared.append((pa[0], pa[1], pa[2], *e1, *e2, normal_length))
        live.append((a, b, c))
    if not live:
        return _error_result(name, "mesh has no non-degenerate triangles", vertex_count)

    # Fixed stride, never random: the same mesh must produce the same sample set every run.
    stride = max(1, -(-vertex_count // max(1, int(max_samples))))
    sampled = list(range(0, vertex_count, stride))

    normals = mesh.get("normals")
    use_supplied_normals = isinstance(normals, list) and len(normals) == vertex_count
    normal_source = "vertexNormals" if use_supplied_normals else "centroid"
    centroid = (
        sum(vertex[0] for vertex in vertices) / vertex_count,
        sum(vertex[1] for vertex in vertices) / vertex_count,
        sum(vertex[2] for vertex in vertices) / vertex_count,
    )

    # Weld by rounded position rather than by index so that a seam-duplicated vertex sharing a
    # position still masks the triangles that meet there. Without this, the surface the sample sits
    # on gets counted as a crossing and every seam vertex reads as a false positive.
    keys = [tuple(round(value, WELD_PRECISION) for value in vertex) for vertex in vertices]
    sampled_keys = {keys[index] for index in sampled}
    incident: dict[tuple[float, ...], set[int]] = {key: set() for key in sampled_keys}
    for triangle_index, triangle in enumerate(live):
        for corner in triangle:
            bucket = incident.get(keys[corner])
            if bucket is not None:
                bucket.add(triangle_index)

    grids = [_DirectionGrid(direction, vertices, live) for direction in RAY_DIRECTIONS]

    inside_indices: list[int] = []
    undecided = 0
    normal_fallbacks = 0
    for vertex_index in sampled:
        vertex = vertices[vertex_index]
        outward: Vector3 | None = None
        if use_supplied_normals:
            try:
                outward = _normalize(_as_point(normals[vertex_index]))
            except (TypeError, ValueError):
                outward = None
        if outward is None:
            if use_supplied_normals:
                # A zero-length or malformed normal on one vertex is not a reason to abandon the
                # good normals on every other vertex; fall back for this vertex and say how often.
                normal_fallbacks += 1
            outward = _normalize((
                vertex[0] - centroid[0],
                vertex[1] - centroid[1],
                vertex[2] - centroid[2],
            ))
        if outward is None:
            # A vertex sitting exactly on the centroid has no outward direction to step along.
            undecided += 1
            continue
        origin = (
            vertex[0] + outward[0] * offset,
            vertex[1] + outward[1] * offset,
            vertex[2] + outward[2] * offset,
        )
        excluded = incident[keys[vertex_index]]
        votes: list[bool] = []
        reliable = True
        for direction, grid in zip(RAY_DIRECTIONS, grids):
            alignment = direction[0] * outward[0] + direction[1] * outward[1] + direction[2] * outward[2]
            if -TANGENT_COS_THRESHOLD < alignment < TANGENT_COS_THRESHOLD:
                reliable = False
                break
            # Cast into the outward hemisphere. An inward ray from a point that is legitimately
            # just outside the surface re-crosses that same surface immediately, on triangles this
            # pass has masked out, and the missing crossing flips the parity to a false positive.
            ray = direction if alignment > 0.0 else (-direction[0], -direction[1], -direction[2])
            crossings, ok = _count_crossings(origin, ray, grid, prepared, excluded, t_min)
            if not ok:
                reliable = False
                break
            votes.append(crossings % 2 == 1)
        if not reliable:
            undecided += 1
            continue
        inside_votes = sum(votes)
        if inside_votes * 2 == len(votes):
            # A tie has no majority, so there is no verdict to report.
            undecided += 1
        elif inside_votes * 2 > len(votes):
            inside_indices.append(vertex_index)

    # Spread the reported positions across the offending set instead of returning the first eight,
    # which on a large defect would all come from one corner of one region. There is no severity
    # ranking to apply to a boolean inside/outside verdict, so these are representative locations a
    # human can navigate to, not a ranking.
    region_stride = max(1, len(inside_indices) // WORST_REGION_LIMIT)
    worst = [
        {"vertexIndex": index, "position": list(vertices[index])}
        for index in inside_indices[::region_stride][:WORST_REGION_LIMIT]
    ]

    return {
        "name": name,
        "analyzed": True,
        "error": None,
        "selfIntersecting": bool(inside_indices),
        "insideVertexCount": len(inside_indices),
        "undecidedVertexCount": undecided,
        "outsideVertexCount": len(sampled) - len(inside_indices) - undecided,
        "sampledVertexCount": len(sampled),
        "totalVertexCount": vertex_count,
        "samplingStride": stride,
        "triangleCount": len(live),
        "degenerateTriangleCount": degenerate,
        "worstRegions": worst,
        "normalSource": normal_source,
        "normalFallbackCount": normal_fallbacks,
        "epsilon": offset,
        "rayDirectionCount": len(RAY_DIRECTIONS),
    }


def analyze_meshes(
    meshes: list[dict[str, Any]],
    *,
    max_samples: int = MAX_SAMPLED_VERTICES,
    epsilon: float | None = None,
) -> dict[str, Any]:
    """Run `analyze_mesh` over a list of meshes and aggregate the verdicts."""
    reports: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, mesh in enumerate(meshes):
        if not isinstance(mesh, dict):
            report = _error_result(f"mesh-{index}", "mesh entry is not an object")
        else:
            report = analyze_mesh(mesh, max_samples=max_samples, epsilon=epsilon)
        if report["error"]:
            errors.append(f"{report['name']}: {report['error']}")
        reports.append(report)
    return {
        "selfIntersecting": any(report["selfIntersecting"] for report in reports),
        "meshCount": len(reports),
        "insideVertexCount": sum(report["insideVertexCount"] for report in reports),
        "undecidedVertexCount": sum(report["undecidedVertexCount"] for report in reports),
        "sampledVertexCount": sum(report["sampledVertexCount"] for report in reports),
        "totalVertexCount": sum(report["totalVertexCount"] for report in reports),
        "meshes": reports,
        "errors": errors,
    }


def _load_meshes(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text())
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        meshes = payload.get("meshes")
        if meshes is None:
            meshes = payload.get("components")
        if isinstance(meshes, dict):
            return [dict(value, id=key) if isinstance(value, dict) else {"id": key} for key, value in meshes.items()]
        if isinstance(meshes, list):
            return meshes
        if isinstance(payload.get("vertices"), list):
            return [payload]
    raise ValueError("input must be a mesh, an array of meshes, or an object with meshes/components")


def _format_summary(result: dict[str, Any]) -> str:
    lines: list[str] = []
    for report in result["meshes"]:
        if report["error"]:
            lines.append(f"  [ERROR] {report['name']}: {report['error']}")
            continue
        flag = "SELF-INTERSECTING" if report["selfIntersecting"] else "ok"
        lines.append(
            f"  [{flag}] {report['name']} "
            f"sampled={report['sampledVertexCount']}/{report['totalVertexCount']} "
            f"(stride {report['samplingStride']}) "
            f"inside={report['insideVertexCount']} undecided={report['undecidedVertexCount']} "
            f"normals={report['normalSource']} epsilon={report['epsilon']:.6g}"
        )
        for region in report["worstRegions"]:
            position = ", ".join(f"{value:.4f}" for value in region["position"])
            lines.append(f"      vertex {region['vertexIndex']} at ({position})")
    if result["errors"]:
        verdict = "ERROR"
    elif result["selfIntersecting"]:
        verdict = "SELF-INTERSECTION DETECTED"
    else:
        verdict = "no self-intersection"
    lines.append(f"verdict: {verdict}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("meshes", type=Path, help="JSON file: a mesh, an array of meshes, or {meshes: [...]}")
    parser.add_argument("--max-samples", type=int, default=MAX_SAMPLED_VERTICES)
    parser.add_argument("--epsilon", type=float, default=None)
    parser.add_argument("--json", action="store_true", help="print JSON instead of a summary")
    args = parser.parse_args(argv)

    try:
        meshes = _load_meshes(args.meshes)
        result = analyze_meshes(meshes, max_samples=args.max_samples, epsilon=args.epsilon)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(_format_summary(result))
        # An unreadable mesh is an error, not a clean bill of health, so it outranks the verdict.
        if result["errors"]:
            return 2
        return 1 if result["selfIntersecting"] else 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


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
