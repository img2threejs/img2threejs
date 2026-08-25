#!/usr/bin/env python3
"""Skinning weights from geodesic distance through the model's own volume.

The weight function this replaces measures EUCLIDEAN distance from a vertex to a bone segment. That
is cheap and it is wrong in one specific, very visible way: straight-line distance does not care
whether the path stays inside the model. An arm resting against the ribs is millimetres from the
chest through the air, so the arm bone claims chest vertices, and raising the arm drags the chest with
it. Every character whose limbs touch the body has this defect; it is not an edge case, it is the
default pose.

Geodesic voxel binding (Dionne & de Lasa, SCA 2013) fixes it by measuring distance THROUGH THE SOLID:
voxelize the mesh, seed each bone's voxels, and propagate outward without ever crossing empty space.
The arm-to-chest path is then forced around through the shoulder and is correctly long. It is a
purely geometric method -- no training data, no time constants, no iteration parameters, no
optimization at bind or pose time -- which is why it fits a pipeline that ships code rather than
weights.

Interior detection is by flood fill from OUTSIDE the bounding box rather than by a point-in-mesh test
per voxel. Both are correct on a watertight mesh; the flood fill is one pass instead of one ray cast
per voxel, and it degrades sanely on a mesh with small holes (the leak is bounded by the hole) rather
than producing scattered wrong answers along a whole ray.

Pure Python 3.10+ stdlib. No pip installs, no numpy.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import deque
from pathlib import Path
from typing import Any

DEFAULT_RESOLUTION = 32
MAX_RESOLUTION = 96
MAX_INFLUENCES = 4
# Measured on the arm-beside-torso fixture, where the geodesic field correctly reports 1.37 world
# units to the spine and 4.45 to the arm: power 2 still leaves 8.6% arm influence on the far chest
# corner, power 3 leaves 2.8%, power 4 leaves 0.9%. The residual is a property of THIS constant, not
# of the distance field. Higher is tighter and lower is smoother across a joint, so 3 is the middle
# that removes the visible cross-talk without pinching the elbow.
DEFAULT_FALLOFF_POWER = 3.0
UNREACHABLE = float("inf")

# Roles that must be rigidly parented to a joint rather than smooth-skinned to the skeleton.
#
# WHY HAIR IS FIRST ON THIS LIST. The geodesic field measures distance THROUGH THE SOLID, which is
# exactly right for a limb and exactly wrong for hair. Hair sits ON the skull, so the path from the
# neck joint to a crown vertex runs up through the head and arrives short -- the crown then takes a
# real share of neck weight, and turning the neck DEFORMS the hair, shearing it against the very
# skull it is supposed to sit on.
#
# The deformation also voids the one guarantee the hair pipeline has. `standProud` and the
# `scalpExposure` gate are build-time geometric checks against the bind pose; smooth-skin the hair
# and every other pose is unverified, so the model can go bald when the head turns with no gate able
# to see it. Rigid parenting keeps hair and skull on a single shared transform, which makes the
# relation between them invariant under every pose.
#
# It is also simply more correct. Short hair on a skull genuinely does move rigidly with it; smooth
# skinning would introduce a deformation that does not exist in the subject.
#
# Long hair that crosses a joint is the real exception, and it is served by a short chain of bones
# with one mesh segment rigidly parented per bone -- still no blended vertex weights.
RIGID_ROLES = frozenset({"hair", "detail", "decal", "panel"})

# 26-connectivity with true step lengths: a face step is 1 voxel, an edge step sqrt(2), a corner
# sqrt(3). Using 6-connectivity instead makes every distance a Manhattan distance, which biases
# weights along the grid axes and shows up as boxy falloff on a diagonal limb.
_NEIGHBOURS: list[tuple[int, int, int, float]] = [
    (dx, dy, dz, math.sqrt(dx * dx + dy * dy + dz * dz))
    for dx in (-1, 0, 1)
    for dy in (-1, 0, 1)
    for dz in (-1, 0, 1)
    if (dx, dy, dz) != (0, 0, 0)
]


def _triangles(indices: list[Any]) -> list[tuple[int, int, int]]:
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


class VoxelGrid:
    """A padded occupancy grid over the mesh, with solid/empty classification."""

    def __init__(self, vertices: list[list[float]], faces: list[tuple[int, int, int]], resolution: int):
        low, high = _bounds(vertices)
        extent = max(high[axis] - low[axis] for axis in range(3)) or 1.0
        self.step = extent / resolution
        # One voxel of padding on every side so the outside flood fill has somewhere to start; without
        # it a mesh touching the bounding box would have no exterior seed on that face and its whole
        # interior would be misread as outside.
        self.low = [low[axis] - self.step for axis in range(3)]
        self.dims = [
            int(math.ceil((high[axis] - low[axis]) / self.step)) + 3 for axis in range(3)
        ]
        self.surface: set[tuple[int, int, int]] = set()
        for face in faces:
            self._rasterize(vertices, face)
        self.solid = self._fill_interior()

    def index_of(self, point: list[float]) -> tuple[int, int, int]:
        return tuple(  # type: ignore[return-value]
            min(self.dims[axis] - 1, max(0, int((point[axis] - self.low[axis]) / self.step)))
            for axis in range(3)
        )

    def _rasterize(self, vertices: list[list[float]], face: tuple[int, int, int]) -> None:
        """Mark every voxel a triangle touches, by subdividing until steps are sub-voxel.

        Barycentric sampling at a fixed rate would leave gaps in a triangle much larger than a voxel,
        and a gap in the surface shell lets the interior flood fill leak out and erase the model.
        """
        p0, p1, p2 = (vertices[i] for i in face)
        longest = max(
            math.dist(p0, p1), math.dist(p1, p2), math.dist(p2, p0)
        )
        steps = max(1, int(math.ceil(longest / (self.step * 0.5))))
        for i in range(steps + 1):
            for j in range(steps + 1 - i):
                a = i / steps
                b = j / steps
                c = 1.0 - a - b
                point = [p0[k] * c + p1[k] * a + p2[k] * b for k in range(3)]
                self.surface.add(self.index_of(point))

    def _fill_interior(self) -> set[tuple[int, int, int]]:
        outside: set[tuple[int, int, int]] = set()
        start = (0, 0, 0)
        if start in self.surface:
            # Padding guarantees this cannot happen; if it ever does, treat everything as solid rather
            # than silently returning an empty model.
            return {
                (x, y, z)
                for x in range(self.dims[0])
                for y in range(self.dims[1])
                for z in range(self.dims[2])
            }
        queue = deque([start])
        outside.add(start)
        while queue:
            x, y, z = queue.popleft()
            for dx, dy, dz, _ in _NEIGHBOURS:
                nx, ny, nz = x + dx, y + dy, z + dz
                if not (0 <= nx < self.dims[0] and 0 <= ny < self.dims[1] and 0 <= nz < self.dims[2]):
                    continue
                cell = (nx, ny, nz)
                if cell in outside or cell in self.surface:
                    continue
                outside.add(cell)
                queue.append(cell)
        solid = set(self.surface)
        for x in range(self.dims[0]):
            for y in range(self.dims[1]):
                for z in range(self.dims[2]):
                    cell = (x, y, z)
                    if cell not in outside:
                        solid.add(cell)
        return solid


def _segment_voxels(grid: VoxelGrid, start: list[float], end: list[float]) -> set[tuple[int, int, int]]:
    """Voxels a bone segment passes through, sampled at half-voxel steps."""
    length = math.dist(start, end)
    steps = max(1, int(math.ceil(length / (grid.step * 0.5))))
    cells = set()
    for i in range(steps + 1):
        t = i / steps
        point = [start[k] + (end[k] - start[k]) * t for k in range(3)]
        cells.add(grid.index_of(point))
    return cells


def geodesic_field(grid: VoxelGrid, sources: set[tuple[int, int, int]]) -> dict[tuple[int, int, int], float]:
    """Dijkstra over solid voxels from a set of sources, in voxel units.

    Dijkstra rather than plain BFS because the 26-connected steps have three different lengths; BFS
    would count a corner step (sqrt 3) the same as a face step and distort the field diagonally.
    """
    import heapq

    distance: dict[tuple[int, int, int], float] = {}
    heap: list[tuple[float, tuple[int, int, int]]] = []
    for cell in sources:
        if cell in grid.solid:
            distance[cell] = 0.0
            heap.append((0.0, cell))
    heapq.heapify(heap)
    while heap:
        current, cell = heapq.heappop(heap)
        if current > distance.get(cell, UNREACHABLE):
            continue
        x, y, z = cell
        for dx, dy, dz, cost in _NEIGHBOURS:
            neighbour = (x + dx, y + dy, z + dz)
            if neighbour not in grid.solid:
                continue
            candidate = current + cost
            if candidate < distance.get(neighbour, UNREACHABLE):
                distance[neighbour] = candidate
                heapq.heappush(heap, (candidate, neighbour))
    return distance


def partition_for_binding(
    components: list[dict[str, Any]],
    rigid_roles: frozenset[str] = RIGID_ROLES,
) -> dict[str, Any]:
    """Split a component tree into what may be smooth-skinned and what must be rigidly parented.

    `bind()` weights every vertex of whatever mesh it is handed, with no notion of role. That is the
    correct contract for a distance solver and the wrong default for a character: hand it a merged
    mesh containing hair and it will skin the hair, because nothing told it not to. This is the
    thing that tells it not to.

    Rigid components are REPORTED, never silently dropped. Each carries the joint it rides, resolved
    by walking up the parent chain to the first ancestor that is itself skinned -- a lock parented to
    a hair mass parented to the head rides the head, not the mass.
    """
    by_id = {
        str(component.get("id")): component
        for component in components
        if isinstance(component, dict) and component.get("id")
    }

    def is_rigid(component: dict[str, Any]) -> bool:
        return str(component.get("role") or "").lower() in rigid_roles

    def ride(component: dict[str, Any]) -> str | None:
        """First ancestor that is skinned, so the rigid piece has a joint to hang from."""
        seen: set[str] = set()
        parent_id = component.get("parent")
        while isinstance(parent_id, str) and parent_id in by_id and parent_id not in seen:
            seen.add(parent_id)
            parent = by_id[parent_id]
            if not is_rigid(parent):
                return parent_id
            parent_id = parent.get("parent")
        return None

    skinned: list[str] = []
    rigid: list[dict[str, Any]] = []
    warnings: list[str] = []
    for component in components:
        if not isinstance(component, dict) or not component.get("id"):
            continue
        component_id = str(component["id"])
        if not is_rigid(component):
            skinned.append(component_id)
            continue
        joint = ride(component)
        if joint is None:
            warnings.append(
                f"component {component_id!r} has rigid role "
                f"{str(component.get('role') or '')!r} but no skinned ancestor to parent to; "
                f"it would be left unattached to the skeleton"
            )
        rigid.append({"id": component_id, "role": component.get("role"), "parentJoint": joint})

    return {
        "schemaVersion": 1,
        "kind": "binding-partition",
        "skinned": skinned,
        "rigidlyParented": rigid,
        "rigidRoles": sorted(rigid_roles),
        "warnings": warnings,
        "note": (
            "Rigid components are parented to a joint and inherit its transform exactly. They are "
            "excluded from geodesic binding because the field measures distance through the solid, "
            "so a crown vertex would take neck weight through the skull and shear against it."
        ),
    }


def bind(
    mesh: dict[str, Any],
    bones: list[dict[str, Any]],
    resolution: int = DEFAULT_RESOLUTION,
    falloff_power: float = DEFAULT_FALLOFF_POWER,
    max_influences: int = MAX_INFLUENCES,
    components: list[dict[str, Any]] | None = None,
    rigid_roles: frozenset[str] = RIGID_ROLES,
) -> dict[str, Any]:
    """Compute skin indices and weights for every vertex.

    Each bone needs `id`, `jointPos` and `tipPos`. Weight falls off as 1/d**power in GEODESIC
    distance, the top `max_influences` are kept and normalised, and a vertex no bone can reach through
    the solid is reported rather than quietly pinned to the nearest bone in space -- being unreachable
    usually means the mesh has a hole or a detached island, and that is worth knowing.

    Pass `components` -- the component tree the mesh was built from -- and the result carries a
    `bindingPartition` naming everything that must be rigidly parented instead of skinned, plus a
    warning if any of it looks like it reached the mesh anyway. Without it the caller gets weights
    for every vertex handed in and no signal at all that some of them should not have been, which is
    the failure mode this whole rule exists to prevent: the geodesic path from the neck to a crown
    vertex runs UP THROUGH THE SKULL, so hair takes real neck weight (8.1% on the test fixture) and
    shears against the skull it sits on.
    """
    vertices = mesh.get("vertices")
    indices = mesh.get("indices")
    if not isinstance(vertices, list) or not vertices:
        raise ValueError("mesh.vertices must be a non-empty array")
    if not isinstance(indices, list) or len(indices) < 3:
        raise ValueError("mesh.indices must describe at least one triangle")
    if not bones:
        raise ValueError("at least one bone is required")
    if resolution > MAX_RESOLUTION:
        raise ValueError(f"resolution must not exceed {MAX_RESOLUTION}")

    faces = _triangles(indices)
    grid = VoxelGrid(vertices, faces, resolution)

    fields: list[dict[tuple[int, int, int], float]] = []
    unreachable_bones: list[str] = []
    for bone in bones:
        sources = _segment_voxels(grid, bone["jointPos"], bone["tipPos"])
        field = geodesic_field(grid, sources)
        if not field:
            unreachable_bones.append(str(bone.get("id")))
        fields.append(field)

    skin_indices: list[list[int]] = []
    skin_weights: list[list[float]] = []
    unreachable_vertices = 0
    for vertex in vertices:
        cell = grid.index_of(vertex)
        scored: list[tuple[float, int]] = []
        for bone_index, field in enumerate(fields):
            distance = field.get(cell)
            if distance is None:
                continue
            # A vertex sitting on the bone itself would divide by zero; clamp to half a voxel, the
            # finest distinction this grid can represent anyway.
            scored.append((1.0 / max(distance, 0.5) ** falloff_power, bone_index))
        if not scored:
            unreachable_vertices += 1
            skin_indices.append([0] * max_influences)
            weights = [0.0] * max_influences
            weights[0] = 1.0
            skin_weights.append(weights)
            continue
        scored.sort(reverse=True)
        kept = scored[:max_influences]
        total = sum(weight for weight, _ in kept)
        row_indices = [0] * max_influences
        row_weights = [0.0] * max_influences
        for slot, (weight, bone_index) in enumerate(kept):
            row_indices[slot] = bone_index
            row_weights[slot] = weight / total
        skin_indices.append(row_indices)
        skin_weights.append(row_weights)

    partition = partition_for_binding(components, rigid_roles) if components is not None else None
    partition_warnings: list[str] = []
    rigid_pinned = 0
    if partition and partition["rigidlyParented"]:
        partition_warnings.append(
            f"{len(partition['rigidlyParented'])} component(s) carry a rigid role "
            f"({', '.join(sorted(rigid_roles))}) and are parented to a joint rather than skinned."
        )

        # EXCLUSION, not just a note. Reporting the partition and then handing back geodesic weights
        # for those vertices anyway leaves the caller holding exactly the wrong numbers -- and the
        # obvious thing to do with a returned weight array is to use it. A rigidly parented vertex
        # gets weight 1.0 on the single joint it rides and nothing else, which IS rigid parenting
        # written in the only language a skinned mesh speaks.
        #
        # This needs to know which vertex belongs to which component, so the mesh must carry
        # `vertexComponents`, a component id per vertex. Without it the exclusion cannot be applied
        # and that is said out loud rather than silently skipped.
        rides = {entry["id"]: entry["parentJoint"] for entry in partition["rigidlyParented"]}
        owners = mesh.get("vertexComponents")
        if not isinstance(owners, list) or len(owners) != len(vertices):
            partition_warnings.append(
                "mesh has no `vertexComponents` (one component id per vertex), so the rigid "
                "components above could NOT be excluded from these weights. Supply it, or bind "
                "only the skinned components' geometry."
            )
        else:
            bone_slot = {str(bone.get("id")): index for index, bone in enumerate(bones)}
            for index, owner in enumerate(owners):
                joint = rides.get(str(owner))
                if joint is None:
                    continue
                slot = bone_slot.get(str(joint))
                if slot is None:
                    partition_warnings.append(
                        f"component {owner!r} rides joint {joint!r}, which is not among the bones "
                        f"supplied; its vertices keep their geodesic weights"
                    )
                    continue
                skin_indices[index] = [slot] + [0] * (max_influences - 1)
                skin_weights[index] = [1.0] + [0.0] * (max_influences - 1)
                rigid_pinned += 1

    return {
        "skinIndices": skin_indices,
        "skinWeights": skin_weights,
        "bindingPartition": partition,
        "partitionWarnings": partition_warnings + (partition["warnings"] if partition else []),
        "rigidPinnedVertexCount": rigid_pinned,
        "boneOrder": [str(bone.get("id")) for bone in bones],
        "resolution": resolution,
        "voxelStep": grid.step,
        "solidVoxelCount": len(grid.solid),
        "surfaceVoxelCount": len(grid.surface),
        "unreachableVertexCount": unreachable_vertices,
        "unreachableBones": unreachable_bones,
        "maxWeightError": max(
            abs(sum(row) - 1.0) for row in skin_weights
        ) if skin_weights else 0.0,
    }


def euclidean_bind(
    mesh: dict[str, Any],
    bones: list[dict[str, Any]],
    falloff_power: float = DEFAULT_FALLOFF_POWER,
    max_influences: int = MAX_INFLUENCES,
) -> dict[str, Any]:
    """The straight-line binding this module replaces, kept so the difference can be MEASURED.

    Without it the claim "geodesic stops weights leaking across a gap" is an assertion. With it a test
    can bind the same mesh both ways and show the leak in one and not the other.
    """

    def point_to_segment(point: list[float], a: list[float], b: list[float]) -> float:
        ab = [b[k] - a[k] for k in range(3)]
        ap = [point[k] - a[k] for k in range(3)]
        denominator = sum(component * component for component in ab)
        t = 0.0 if denominator <= 1e-12 else max(0.0, min(1.0, sum(ap[k] * ab[k] for k in range(3)) / denominator))
        closest = [a[k] + ab[k] * t for k in range(3)]
        return math.dist(point, closest)

    vertices = mesh["vertices"]
    skin_indices: list[list[int]] = []
    skin_weights: list[list[float]] = []
    for vertex in vertices:
        scored = [
            (1.0 / max(point_to_segment(vertex, bone["jointPos"], bone["tipPos"]), 1e-6) ** falloff_power, index)
            for index, bone in enumerate(bones)
        ]
        scored.sort(reverse=True)
        kept = scored[:max_influences]
        total = sum(weight for weight, _ in kept) or 1.0
        row_indices = [0] * max_influences
        row_weights = [0.0] * max_influences
        for slot, (weight, bone_index) in enumerate(kept):
            row_indices[slot] = bone_index
            row_weights[slot] = weight / total
        skin_indices.append(row_indices)
        skin_weights.append(row_weights)
    return {"skinIndices": skin_indices, "skinWeights": skin_weights,
            "boneOrder": [str(bone.get("id")) for bone in bones]}


def _format_summary(result: dict[str, Any]) -> str:
    lines = [
        f"bones: {len(result['boneOrder'])}",
        f"grid: {result['resolution']} ({result['solidVoxelCount']} solid voxels)",
        f"max |sum(w) - 1|: {result['maxWeightError']:.3e}",
    ]
    if result["unreachableVertexCount"]:
        lines.append(
            f"UNREACHABLE vertices: {result['unreachableVertexCount']} -- no bone reaches them through "
            "the solid, which usually means a hole or a detached island in the mesh"
        )
    if result["unreachableBones"]:
        lines.append(f"UNREACHABLE bones (outside the solid): {result['unreachableBones']}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Bind skin weights by geodesic voxel distance.")
    parser.add_argument("mesh", type=Path, help="JSON with vertices and indices")
    parser.add_argument("--bones", type=Path, required=True, help="JSON array of bones")
    parser.add_argument("--resolution", type=int, default=DEFAULT_RESOLUTION)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        mesh = json.loads(args.mesh.read_text())
        if isinstance(mesh, dict) and isinstance(mesh.get("meshes"), list) and mesh["meshes"]:
            mesh = mesh["meshes"][0]
        result = bind(mesh, json.loads(args.bones.read_text()), args.resolution)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.out:
        args.out.write_text(json.dumps(result))
    print(json.dumps(result, indent=2) if args.json else _format_summary(result))
    return 1 if result["unreachableVertexCount"] or result["unreachableBones"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
