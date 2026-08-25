"""Slice a GLB node's point cloud into horizontal cross-sections.

WHY THIS REPLACES THE SILHOUETTE EXTRUSIONS. Those matched the reference's front outline almost
exactly -- 11 of 11 regions above 9/10 -- by extruding a traced 2D outline. From any other angle the
model was a stack of flat, mutually detached slabs, because an extrusion has no cross-section to speak
of. That is a one-view fit, not a model.

The GLB is a real 3D asset and was never used as one here: only its per-node bounding boxes, then its
per-height widths. Node 0 alone carries 371083 vertices. Reading the actual point cloud and taking a
convex hull per height band gives a solid that is correct from every direction, which is what the
front-outline approach could never be.

WHAT A PER-SLICE CONVEX HULL CAN AND CANNOT DO. It captures how a part's footprint changes with
height -- a boot's toe, a head narrowing to a face, trousers tapering at the waist -- so silhouettes
from any azimuth are plausible. It CANNOT represent a concavity in the horizontal plane: a cupped
palm, the gap between two fingers, the hollow inside a sleeve. Those get filled. The alternative,
tracing each slice's true outline, needs a sensible 2D boundary from a sparse and noisy point set;
hulls are robust where that is not, so this starts with hulls and says so.

Character-agnostic: takes a GLB path and node index as arguments, no character-specific data baked in.

Usage:  python3 slice_node.py <glb> <nodeIndex> [slices]
"""
from __future__ import annotations

import array
import json
import struct
import sys
from pathlib import Path


def read_node_positions(glb: Path, node_index: int) -> tuple[array.array, int]:
    """POSITION accessor of the node's first primitive, straight from the binary chunk."""
    with glb.open("rb") as handle:
        struct.unpack("<III", handle.read(12))
        json_length, _ = struct.unpack("<II", handle.read(8))
        gltf = json.loads(handle.read(json_length))
        struct.unpack("<II", handle.read(8))
        binary_start = handle.tell()

        node = gltf["nodes"][node_index]
        primitive = gltf["meshes"][node["mesh"]]["primitives"][0]
        accessor = gltf["accessors"][primitive["attributes"]["POSITION"]]
        view = gltf["bufferViews"][accessor["bufferView"]]
        handle.seek(binary_start + view.get("byteOffset", 0) + accessor.get("byteOffset", 0))
        raw = handle.read(accessor["count"] * 12)

    positions = array.array("f")
    positions.frombytes(raw)
    return positions, accessor["count"]


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain, counter-clockwise, no collinear points retained.

    Deduplicated first: these clouds hold tens of thousands of points per slice and many coincide
    once projected to the horizontal plane, which makes the cross-product test degenerate.
    """
    unique = sorted(set(points))
    if len(unique) < 3:
        return unique

    def cross(o, a, b) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def resample_ring(ring: list[tuple[float, float]], spokes: int) -> list[tuple[float, float]]:
    """Resample a hull to exactly `spokes` points, evenly spaced in angle about its centroid.

    Lofting needs every ring to have the same point count in the same angular order; hulls of
    different slices do not. Casting a fixed set of rays and taking where each crosses the hull gives
    that correspondence, so ring i point k always connects to ring i+1 point k and the side walls come
    out untwisted.
    """
    import math

    cx = sum(x for x, _ in ring) / len(ring)
    cy = sum(y for _, y in ring) / len(ring)
    out: list[tuple[float, float]] = []
    for index in range(spokes):
        angle = 2 * math.pi * index / spokes
        dx, dy = math.cos(angle), math.sin(angle)
        best = 0.0
        # Intersect the ray with every hull edge; the hull is convex so at most one crossing counts.
        for i in range(len(ring)):
            (x0, y0), (x1, y1) = ring[i], ring[(i + 1) % len(ring)]
            ex, ey = x1 - x0, y1 - y0
            denominator = dx * ey - dy * ex
            if abs(denominator) < 1e-12:
                continue
            t = ((x0 - cx) * ey - (y0 - cy) * ex) / denominator
            u = ((x0 - cx) * dy - (y0 - cy) * dx) / denominator
            if t > 0 and -1e-9 <= u <= 1 + 1e-9:
                best = max(best, t)
        out.append((round(cx + dx * best, 4), round(cy + dy * best, 4)))
    return out


def radial_outline(points: list[tuple[float, float]], spokes: int,
                  percentile: float = 0.92) -> list[tuple[float, float]]:
    """Concave outline of a cluster: the real radius in each angular bin, not a hull around it.

    WHY THIS REPLACES convex_hull + resample_ring. A hull is the tightest CONVEX shape containing the
    cluster, so it erases every inward curve -- the waist above the hips, the arch of a boot, the dip
    between a knee pad and the shin. That is exactly the detail the silhouette needs, and no amount of
    extra slices recovers it, because the loss is within each slice rather than between them.

    Taking a high percentile of the radii per bin rather than the maximum keeps a single stray vertex
    from throwing a spike; `percentile` is the one tuning knob. Empty bins are filled by interpolating
    their nearest occupied neighbours, so a sparse cluster degrades to a smooth ring instead of a
    collapsed one.
    """
    import math

    cx = sum(x for x, _ in points) / len(points)
    cy = sum(y for _, y in points) / len(points)
    bins: list[list[float]] = [[] for _ in range(spokes)]
    for x, y in points:
        dx, dy = x - cx, y - cy
        radius = math.hypot(dx, dy)
        if radius <= 0:
            continue
        index = int((math.atan2(dy, dx) % (2 * math.pi)) / (2 * math.pi) * spokes) % spokes
        bins[index].append(radius)

    radii: list[float | None] = []
    for values in bins:
        if not values:
            radii.append(None)
            continue
        values.sort()
        radii.append(values[min(len(values) - 1, int(len(values) * percentile))])

    if all(value is None for value in radii):
        return []
    for index in range(spokes):
        if radii[index] is not None:
            continue
        before = next(radii[(index - step) % spokes] for step in range(1, spokes + 1)
                      if radii[(index - step) % spokes] is not None)
        after = next(radii[(index + step) % spokes] for step in range(1, spokes + 1)
                     if radii[(index + step) % spokes] is not None)
        radii[index] = (before + after) / 2

    out = []
    for index in range(spokes):
        angle = 2 * math.pi * (index + 0.5) / spokes
        out.append((round(cx + math.cos(angle) * radii[index], 4),
                    round(cy + math.sin(angle) * radii[index], 4)))
    return out


def decimate_ring(ring: list[tuple[float, float]], limit: int) -> list[tuple[float, float]]:
    """Keep at most `limit` points, dropping the ones that change direction least.

    A hull of a dense cloud can run to hundreds of points that are visually identical; every one
    becomes vertices in the lofted mesh. Dropping by turn angle keeps corners and sheds the rest.
    """
    if len(ring) <= limit:
        return ring
    while len(ring) > limit:
        worst_index, worst_turn = 1, float("inf")
        for i in range(len(ring)):
            previous, current, following = ring[i - 1], ring[i], ring[(i + 1) % len(ring)]
            turn = abs((current[0] - previous[0]) * (following[1] - current[1])
                       - (current[1] - previous[1]) * (following[0] - current[0]))
            if turn < worst_turn:
                worst_index, worst_turn = i, turn
        ring.pop(worst_index)
    return ring


def cluster_slice(points: list[tuple[float, float]],
                 cell: float = 0.012) -> list[list[tuple[float, float]]]:
    """Split one slice's points into spatially separate groups, largest first.

    A single hull per slice is wrong whenever a slice cuts through two disconnected pieces of the same
    node. Grid-based union of occupied cells: cheap, and `cell` is the only tuning knob -- it is the
    widest gap that still counts as connected.
    """
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for x, z in points:
        buckets.setdefault((int(x // cell), int(z // cell)), []).append((x, z))

    seen: set[tuple[int, int]] = set()
    groups: list[list[tuple[float, float]]] = []
    for key in buckets:
        if key in seen:
            continue
        stack, group = [key], []
        seen.add(key)
        while stack:
            cx, cz = stack.pop()
            group.extend(buckets[(cx, cz)])
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    neighbour = (cx + dx, cz + dz)
                    if neighbour in buckets and neighbour not in seen:
                        seen.add(neighbour)
                        stack.append(neighbour)
        groups.append(group)
    return sorted(groups, key=len, reverse=True)


def slice_node(glb: Path, node_index: int, slices: int = 12,
               max_points: int = 24) -> dict[str, object]:
    positions, count = read_node_positions(glb, node_index)
    xs, ys, zs = positions[0::3], positions[1::3], positions[2::3]
    low, high = min(ys), max(ys)
    span = high - low

    buckets: list[list[tuple[float, float]]] = [[] for _ in range(slices)]
    for i in range(count):
        index = min(slices - 1, int((ys[i] - low) / span * slices)) if span else 0
        # Quantise to 0.1 mm before hulling: raw floats make thousands of near-duplicate points that
        # the hull has to sort through for no gain in shape.
        buckets[index].append((round(xs[i], 4), round(zs[i], 4)))

    rings = []
    for index, bucket in enumerate(buckets):
        if len(bucket) < 3:
            continue
        y = round(low + span * (index + 0.5) / slices, 4)
        for group in cluster_slice(bucket):
            # Drop specks: a handful of stray points make a degenerate ring that adds a spike to the
            # loft without describing anything.
            if len(group) < max(12, len(bucket) // 100):
                continue
            ring = radial_outline(group, max_points)
            if len(ring) < 3:
                continue
            cx = sum(x for x, _ in group) / len(group)
            cz = sum(z for _, z in group) / len(group)
            rings.append({
                "y": y,
                "centroid": [round(cx, 4), round(cz, 4)],
                "points": [[round(x, 4), round(z, 4)] for x, z in ring],
            })

    return {
        "nodeIndex": node_index,
        "vertexCount": count,
        "yRange": [round(low, 4), round(high, 4)],
        "sliceCount": len(rings),
        "rings": rings,
        "limitations": [
            "Each ring is a radial outline of one CLUSTER within its slice, so inward curves survive. "
            "What it still cannot represent is a shape that is not star-convex about its own centroid: "
            "a spiral, a deep hook, or a C-section whose opening faces away from the centre.",
            "Rings are sampled at slice centres, so a feature thinner than one slice is averaged away.",
            "Rings are resampled on rays from each slice's own centroid, so a part whose slices are "
            "offset from one another has its correspondence set by centroid, not by world position.",
        ],
    }


def main(argv: list[str]) -> int:
    glb = Path(argv[0])
    node_index = int(argv[1])
    slices = int(argv[2]) if len(argv) > 2 else 12
    print(json.dumps(slice_node(glb, node_index, slices), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
