#!/usr/bin/env python3
"""Does one part pass through another?

`self_intersection.py` catches a surface folded through ITS OWN volume. It says nothing about two
separate meshes occupying the same space, and that is a different, equally common defect: an arm
inside a torso, a staff through a hat, a strap sunk into a shoulder. Each mesh is individually
flawless, so every per-mesh gate passes, and the model is still broken.

Method: ray parity again, but across meshes. Sample vertices of A, count crossings of B's triangles
along a fixed direction; odd means the vertex is inside B. As in the sibling module, a grazing hit is
reported as UNDECIDED rather than rounded into a verdict, and the sampling budget is reported so a
clean result over a strided sample is never mistaken for a clean result over the whole mesh.

Touching is not penetrating. A hand gripping a staff shares a surface, and a gate that flagged that
would be useless on exactly the models it is meant to check, so `allowedPairs` exists for parts that
are supposed to be in contact and `penetrationDepth` distinguishes a skim from a part buried inside
another.

Pure Python 3.10+ stdlib.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

MAX_SAMPLED_VERTICES = 2000
EPSILON = 1e-9
# Three fixed, mutually non-parallel directions. Deterministic on purpose: a `random` direction makes
# a gate that disagrees with itself between runs, and an intermittent gate gets switched off.
_DIRECTIONS = (
    (0.5773502691896258, 0.5773502691896258, 0.5773502691896258),
    (-0.8017837257372732, 0.5345224838248488, 0.2672612419124244),
    (0.2672612419124244, -0.8017837257372732, 0.5345224838248488),
)
GRAZING_LIMIT = 1e-7


def _triangles(indices: Any) -> list[tuple[int, int, int]]:
    if indices and isinstance(indices[0], (list, tuple)):
        return [(int(t[0]), int(t[1]), int(t[2])) for t in indices]
    return [
        (int(indices[i]), int(indices[i + 1]), int(indices[i + 2]))
        for i in range(0, len(indices) - 2, 3)
    ]


def _bounds(vertices: list[list[float]]) -> tuple[list[float], list[float]]:
    low = [min(v[axis] for v in vertices) for axis in range(3)]
    high = [max(v[axis] for v in vertices) for axis in range(3)]
    return low, high


def _boxes_overlap(a: tuple[list[float], list[float]], b: tuple[list[float], list[float]]) -> bool:
    return all(a[0][axis] <= b[1][axis] and b[0][axis] <= a[1][axis] for axis in range(3))


GRID_CELLS = 48


class _DirectionIndex:
    """Triangles bucketed by their footprint on the plane perpendicular to one ray direction.

    Without this the test is every sample against every triangle. That is fine on a twelve-triangle
    fixture and hopeless on a real model: 2000 samples x 8300 triangles x 3 directions is 50 million
    ray-triangle tests for ONE ordered pair, and an eight-mesh audit has fifty-six of them. Measured
    on the actual character, the brute-force version did not finish in ten minutes. Because every
    sample shares the same direction, the projection basis and the buckets are built once per pair and
    reused, so each sample only tests the triangles whose footprint covers it.
    """

    def __init__(self, direction, vertices, faces):
        dx, dy, dz = direction
        # Any vector not parallel to the direction gives a usable basis; picking the axis the
        # direction is *least* aligned with keeps the cross product well conditioned.
        smallest = min(range(3), key=lambda axis: abs(direction[axis]))
        helper = [0.0, 0.0, 0.0]
        helper[smallest] = 1.0
        ux = helper[1] * dz - helper[2] * dy
        uy = helper[2] * dx - helper[0] * dz
        uz = helper[0] * dy - helper[1] * dx
        length = math.sqrt(ux * ux + uy * uy + uz * uz)
        self.u = (ux / length, uy / length, uz / length)
        self.v = (
            dy * self.u[2] - dz * self.u[1],
            dz * self.u[0] - dx * self.u[2],
            dx * self.u[1] - dy * self.u[0],
        )
        self.faces = faces
        self.vertices = vertices

        boxes = []
        min_a = min_b = float("inf")
        max_a = max_b = float("-inf")
        for face in faces:
            aa = [self._project(vertices[i]) for i in face]
            lo_a = min(p[0] for p in aa)
            hi_a = max(p[0] for p in aa)
            lo_b = min(p[1] for p in aa)
            hi_b = max(p[1] for p in aa)
            boxes.append((lo_a, hi_a, lo_b, hi_b))
            min_a, max_a = min(min_a, lo_a), max(max_a, hi_a)
            min_b, max_b = min(min_b, lo_b), max(max_b, hi_b)

        self.min_a, self.min_b = min_a, min_b
        self.span_a = max(max_a - min_a, 1e-9)
        self.span_b = max(max_b - min_b, 1e-9)
        self.buckets: dict[tuple[int, int], list[int]] = {}
        for index, (lo_a, hi_a, lo_b, hi_b) in enumerate(boxes):
            for cell_a in range(self._cell(lo_a, min_a, self.span_a), self._cell(hi_a, min_a, self.span_a) + 1):
                for cell_b in range(self._cell(lo_b, min_b, self.span_b), self._cell(hi_b, min_b, self.span_b) + 1):
                    self.buckets.setdefault((cell_a, cell_b), []).append(index)

    def _project(self, point) -> tuple[float, float]:
        return (
            point[0] * self.u[0] + point[1] * self.u[1] + point[2] * self.u[2],
            point[0] * self.v[0] + point[1] * self.v[1] + point[2] * self.v[2],
        )

    @staticmethod
    def _cell(value: float, low: float, span: float) -> int:
        # Clamped, never rejected. A point landing one cell past the end -- which is exactly where the
        # vertex defining the projection's maximum sits -- would otherwise return no candidates and a
        # confident "outside".
        return max(0, min(GRID_CELLS - 1, int((value - low) / span * GRID_CELLS)))

    def candidates(self, point) -> list[int]:
        a, b = self._project(point)
        return self.buckets.get(
            (self._cell(a, self.min_a, self.span_a), self._cell(b, self.min_b, self.span_b)), []
        )


def _crossings(
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    vertices: list[list[float]],
    faces: list[tuple[int, int, int]],
    index: "_DirectionIndex | None" = None,
) -> tuple[int, bool]:
    """Möller-Trumbore parity. Returns (crossings, reliable)."""
    ox, oy, oz = origin
    dx, dy, dz = direction
    crossings = 0
    reliable = True
    candidate_faces = faces if index is None else [faces[i] for i in index.candidates(origin)]
    for a, b, c in candidate_faces:
        p0, p1, p2 = vertices[a], vertices[b], vertices[c]
        e1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
        e2 = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
        px = dy * e2[2] - dz * e2[1]
        py = dz * e2[0] - dx * e2[2]
        pz = dx * e2[1] - dy * e2[0]
        determinant = e1[0] * px + e1[1] * py + e1[2] * pz
        if abs(determinant) < GRAZING_LIMIT:
            # Edge-on: the intersection is numerically meaningless, so the whole ray is untrusted.
            reliable = False
            continue
        inverse = 1.0 / determinant
        tx, ty, tz = ox - p0[0], oy - p0[1], oz - p0[2]
        u = (tx * px + ty * py + tz * pz) * inverse
        if u < -EPSILON or u > 1.0 + EPSILON:
            continue
        qx = ty * e1[2] - tz * e1[1]
        qy = tz * e1[0] - tx * e1[2]
        qz = tx * e1[1] - ty * e1[0]
        v = (dx * qx + dy * qy + dz * qz) * inverse
        if v < -EPSILON or u + v > 1.0 + EPSILON:
            continue
        distance = (e2[0] * qx + e2[1] * qy + e2[2] * qz) * inverse
        if distance <= EPSILON:
            continue
        # A hit exactly on a shared edge belongs to two triangles and would be counted twice.
        if abs(u) < 1e-7 or abs(v) < 1e-7 or abs(u + v - 1.0) < 1e-7:
            reliable = False
        crossings += 1
    return crossings, reliable


def build_indices(vertices, faces) -> list[_DirectionIndex]:
    """One acceleration structure per ray direction, built once and reused across every sample."""
    return [_DirectionIndex(direction, vertices, faces) for direction in _DIRECTIONS]


def point_inside(
    point: tuple[float, float, float],
    vertices: list[list[float]],
    faces: list[tuple[int, int, int]],
    indices: list[_DirectionIndex] | None = None,
) -> str:
    """'inside', 'outside', or 'undecided' by majority over three fixed ray directions."""
    votes = []
    for position, direction in enumerate(_DIRECTIONS):
        crossings, reliable = _crossings(
            point, direction, vertices, faces, indices[position] if indices else None
        )
        if reliable:
            votes.append(crossings % 2 == 1)
    if len(votes) < 2:
        return "undecided"
    inside_votes = sum(1 for vote in votes if vote)
    if inside_votes * 2 == len(votes):
        return "undecided"
    return "inside" if inside_votes * 2 > len(votes) else "outside"


def _sample_points(vertices: list[list[float]], faces: list[tuple[int, int, int]]) -> list[list[float]]:
    """Vertices, plus edge midpoints and face centroids.

    Vertices alone are not enough, and the failure is not exotic. A bar driven through a block has
    every one of its corners OUTSIDE the block and every corner of the block outside the bar -- the
    two surfaces interpenetrate entirely across faces, and a vertex-only test reports a clean pass on
    a model with a pole through its chest. Midpoints and centroids put samples on the parts of the
    surface that are actually inside the other mesh.

    This is still SAMPLING, not exact surface intersection: a part thin enough to thread between the
    samples goes unseen. Detecting that needs an edge-versus-triangle test on every pair of faces,
    which is a different cost class. The limit is stated in the result rather than left implied.
    """
    points = [list(vertex) for vertex in vertices]
    seen_edges: set[tuple[int, int]] = set()
    for a, b, c in faces:
        for u, v in ((a, b), (b, c), (c, a)):
            key = (u, v) if u < v else (v, u)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            points.append([(vertices[u][i] + vertices[v][i]) / 2 for i in range(3)])
        points.append([(vertices[a][i] + vertices[b][i] + vertices[c][i]) / 3 for i in range(3)])
    return points


def analyze_pair(
    a: dict[str, Any],
    b: dict[str, Any],
    max_samples: int = MAX_SAMPLED_VERTICES,
) -> dict[str, Any]:
    a_vertices, b_vertices = a["vertices"], b["vertices"]
    b_faces = _triangles(b["indices"])
    a_samples = _sample_points(a_vertices, _triangles(a["indices"]))
    a_box, b_box = _bounds(a_vertices), _bounds(b_vertices)
    name_a, name_b = str(a.get("name", "a")), str(b.get("name", "b"))
    if not _boxes_overlap(a_box, b_box):
        # Cheap rejection, and reported as such: "0 inside" from a test that never ran and "0 inside"
        # from a test that ran are different claims.
        return {
            "a": name_a, "b": name_b, "boundsOverlap": False, "penetrating": False,
            "insideVertexCount": 0, "undecidedVertexCount": 0,
            "sampledVertexCount": 0, "totalVertexCount": len(a_samples), "samplingStride": 0,
            "penetrationDepth": 0.0,
        }

    b_indices = build_indices(b_vertices, b_faces)
    stride = max(1, math.ceil(len(a_samples) / max_samples))
    inside = 0
    undecided = 0
    sampled = 0
    deepest = 0.0
    worst_points: list[list[float]] = []
    for index in range(0, len(a_samples), stride):
        vertex = a_samples[index]
        sampled += 1
        verdict = point_inside((vertex[0], vertex[1], vertex[2]), b_vertices, b_faces, b_indices)
        if verdict == "undecided":
            undecided += 1
            continue
        if verdict != "inside":
            continue
        inside += 1
        # Depth measured to B's bounding box rather than to its surface: a surface distance needs a
        # full closest-point query per vertex, and the box distance already separates a skimming
        # contact from a part sitting well inside another, which is the decision being made.
        depth = min(
            min(vertex[axis] - b_box[0][axis], b_box[1][axis] - vertex[axis])
            for axis in range(3)
        )
        if depth > deepest:
            deepest = depth
        if len(worst_points) < 8:
            worst_points.append([round(component, 4) for component in vertex])

    return {
        "a": name_a, "b": name_b, "boundsOverlap": True,
        "penetrating": inside > 0,
        "insideVertexCount": inside,
        "undecidedVertexCount": undecided,
        "sampledVertexCount": sampled,
        "totalVertexCount": len(a_samples),
        "samplingStride": stride,
        "penetrationDepth": round(deepest, 6),
        "samplePoints": worst_points,
        "samplingLimitation": (
            "samples are vertices, edge midpoints and face centroids; a part thin enough to thread "
            "between them is not detected, which needs an edge-versus-triangle test"
        ),
    }


def analyze_meshes(
    meshes: list[dict[str, Any]],
    allowed_pairs: list[list[str]] | None = None,
    max_samples: int = MAX_SAMPLED_VERTICES,
) -> dict[str, Any]:
    """Every ordered pair, minus the pairs declared as legitimately in contact."""
    allowed = {frozenset(pair) for pair in (allowed_pairs or [])}
    usable = [m for m in meshes if isinstance(m.get("vertices"), list) and m.get("vertices")
              and isinstance(m.get("indices"), list) and len(m["indices"]) >= 3]
    skipped = [str(m.get("name", "?")) for m in meshes if m not in usable]

    pairs: list[dict[str, Any]] = []
    failures: list[str] = []
    for i, first in enumerate(usable):
        for second in usable[i + 1:]:
            names = frozenset((str(first.get("name", "a")), str(second.get("name", "b"))))
            if names in allowed:
                continue
            # Both directions: A's vertices can all sit outside B while B's sit inside A, whenever one
            # part is much smaller than the other.
            for source, target in ((first, second), (second, first)):
                result = analyze_pair(source, target, max_samples)
                if result["penetrating"]:
                    failures.append(
                        f"{result['a']} penetrates {result['b']}: {result['insideVertexCount']} of "
                        f"{result['sampledVertexCount']} sampled vertices are inside it, depth "
                        f"{result['penetrationDepth']}"
                    )
                if result["boundsOverlap"]:
                    pairs.append(result)

    return {
        "pairs": pairs,
        "failures": failures,
        "penetrating": bool(failures),
        "meshCount": len(usable),
        "skippedMeshes": skipped,
        "allowedPairs": [sorted(pair) for pair in allowed],
        "passed": not failures,
    }


def _format_summary(result: dict[str, Any]) -> str:
    lines = [f"meshes: {result['meshCount']}, pairs with overlapping bounds: {len(result['pairs'])}"]
    if result["skippedMeshes"]:
        lines.append(f"skipped (no usable geometry): {result['skippedMeshes']}")
    for failure in result["failures"]:
        lines.append(f"  [PENETRATION] {failure}")
    lines.append("verdict: PASS" if result["passed"] else "verdict: GATE FAILURE")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Detect one part passing through another.")
    parser.add_argument("meshes", type=Path)
    parser.add_argument("--allow", action="append", default=[],
                        help="a pair permitted to touch, as 'nameA,nameB' (repeatable)")
    parser.add_argument("--max-samples", type=int, default=MAX_SAMPLED_VERTICES)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.meshes.read_text())
        meshes = payload["meshes"] if isinstance(payload, dict) and isinstance(payload.get("meshes"), list) else [payload]
        allowed = [pair.split(",") for pair in args.allow]
        result = analyze_meshes(meshes, allowed, args.max_samples)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else _format_summary(result))
    return 0 if result["passed"] else 1


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
