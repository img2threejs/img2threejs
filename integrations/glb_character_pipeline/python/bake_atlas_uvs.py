"""Transfer a baseline GLB's TEXCOORD_0 onto a crossSections.ts's ring points, with CONTINUITY as a
constraint.

AUTHORISED DEVIATION -- READ BEFORE RUNNING. img2threejs's normal rule is no baseline textures/maps
copied into the procedural factory; this script exists only because that rule was explicitly lifted,
once, for girl-character's own build, as a deliberate experiment. It is packaged here as an opt-in
escape hatch, gated behind CHARACTER_ALLOW_BASELINE_UV=1, precisely so it is never the default path a
new character falls into by just following the pipeline: it refuses to run without that env var
explicitly set, on purpose, every time. The default path for a new character is per-region procedural
material colour, which needs no UV at all -- reach for this only with the same kind of explicit,
one-off authorisation girl-character's build had.

WHY PURE NEAREST-NEIGHBOUR FAILS. A UV is transferred per POINT, from whichever baseline vertex happens
to be closest. A baseline atlas packs many small islands, and within a couple of mm of any ring point
there are usually SEVERAL vertices belonging to DIFFERENT islands. Nearest-neighbour picks among them
by distance alone, so neighbouring ring points can land on unrelated parts of the map. Measured on
girl-character's first attempt: 3.15% of spoke-wise edges and 40.59% of ring-wise edges jumped more
than 0.02 in UV, and 21.16% of triangles needed repair (36370 of 171904) -- one in five collapsed to a
flat colour, producing visible dark slabs and streaking.

THE FIX. Among the candidates that are geometrically acceptable, choose the one whose UV best CONTINUES
its neighbours, rather than the one a hair closer. Acceptability is deliberately tight -- a candidate
may be at most `SLACK` further than the true nearest -- so continuity never buys itself geometric
error. Rings are walked bottom to top and spokes in order, so each point has a settled neighbour below
it and beside it to continue from.

MEASURED, THEN LEFT OFF: on girl-character, scoring candidates by continuity changed the pick for 94.6%
of points, made the ring-wise seam rate barely move (40.59% -> 40.01%), and made median transfer
distance 50% WORSE. The seams are where the surface crosses a genuine cut in the baseline's own UV
layout, not an ambiguity in picking a vertex, so no selection rule removes them -- nearest wins on
accuracy and loses nothing. This script keeps that negative result rather than re-deriving it.

Usage:  CHARACTER_ALLOW_BASELINE_UV=1 python3 bake_atlas_uvs.py [glb]
(CHARACTER_GLB, CHARACTER_SECTION_REGIONS_JSON, CHARACTER_CROSS_SECTIONS same as build_cross_sections.py.)
"""
from __future__ import annotations

import array
import json
import os
import re
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(os.environ.get('IMG2THREEJS_SHOWCASE_ROOT', '.')).resolve()
# A candidate may sit this much further from the query than the nearest vertex does. 2 mm on a
# roughly life-size figure, chosen so the transfer stays inside the tolerance the first pass already
# achieved -- re-measure for a character at a very different scale.
SLACK = float(os.environ.get('CHARACTER_UV_SLACK', '0.002'))


def read_node(handle, gltf, base, node):
    prim = gltf["meshes"][gltf["nodes"][node]["mesh"]]["primitives"][0]

    def acc(index):
        a = gltf["accessors"][index]
        v = gltf["bufferViews"][a["bufferView"]]
        handle.seek(base + v.get("byteOffset", 0) + a.get("byteOffset", 0))
        kinds = {5126: ("f", 4), 5125: ("I", 4), 5123: ("H", 2), 5121: ("B", 1)}
        code, size = kinds[a["componentType"]]
        per = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
        out = array.array(code)
        out.frombytes(handle.read(a["count"] * per * size))
        return np.array(out, dtype=np.float64).reshape(a["count"], per)

    return acc(prim["attributes"]["POSITION"]), acc(prim["attributes"]["TEXCOORD_0"])


class Grid:
    """Uniform-grid neighbourhood query. Returns every candidate, not just the closest."""

    def __init__(self, points: np.ndarray, cell: float):
        self.points = points
        self.cell = cell
        self.buckets: dict[tuple[int, int, int], list[int]] = {}
        for i, key in enumerate(map(tuple, np.floor(points / cell).astype(np.int64))):
            self.buckets.setdefault(key, []).append(i)

    def candidates(self, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        home = np.floor(q / self.cell).astype(np.int64)
        for radius in range(0, 24):
            found: list[int] = []
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    for dz in range(-radius, radius + 1):
                        if radius and max(abs(dx), abs(dy), abs(dz)) != radius:
                            continue
                        found += self.buckets.get(
                            (home[0] + dx, home[1] + dy, home[2] + dz), ())
            if found:
                # One more shell, so a candidate just outside the first hit is still considered.
                extra: list[int] = []
                r2 = radius + 1
                for dx in range(-r2, r2 + 1):
                    for dy in range(-r2, r2 + 1):
                        for dz in range(-r2, r2 + 1):
                            if max(abs(dx), abs(dy), abs(dz)) != r2:
                                continue
                            extra += self.buckets.get(
                                (home[0] + dx, home[1] + dy, home[2] + dz), ())
                idx = np.array(found + extra, dtype=np.int64)
                return idx, np.linalg.norm(self.points[idx] - q, axis=1)
        d = np.linalg.norm(self.points - q, axis=1)      # exhaustive; never a miss
        return np.arange(len(self.points)), d


def main() -> int:
    if os.environ.get('CHARACTER_ALLOW_BASELINE_UV') != '1':
        print("Refusing to run: CHARACTER_ALLOW_BASELINE_UV=1 is not set.\n"
              "This transfers a baseline GLB's own texture UVs onto the procedural mesh, which departs "
              "from img2threejs's normal no-baseline-assets rule. Set the env var explicitly if you "
              "have the same kind of one-off authorisation girl-character's build had -- see this "
              "script's own module docstring first.", file=sys.stderr)
        return 1

    glb = Path(sys.argv[1] if len(sys.argv) > 1
               else os.environ.get('CHARACTER_GLB', str(ROOT / 'public/mesh/girl-character-baseline.glb')))
    sections = Path(os.environ.get('CHARACTER_CROSS_SECTIONS', str(ROOT / 'work/crossSections.ts')))
    regions_path = os.environ.get('CHARACTER_SECTION_REGIONS_JSON')
    if not regions_path:
        print("CHARACTER_SECTION_REGIONS_JSON must be set (same file build_cross_sections.py used).",
              file=sys.stderr)
        return 1
    node_region: dict[int, str] = {int(k): v for k, v in
                                    json.loads(Path(regions_path).read_text()).items()}
    region_nodes: dict[str, list[int]] = {}
    for node, region in sorted(node_region.items()):
        region_nodes.setdefault(region, []).append(node)

    text = sections.read_text()

    with glb.open("rb") as handle:
        struct.unpack("<III", handle.read(12))
        json_length, _ = struct.unpack("<II", handle.read(8))
        gltf = json.loads(handle.read(json_length))
        struct.unpack("<II", handle.read(8))
        base = handle.tell()
        clouds = {node: read_node(handle, gltf, base, node)
                  for nodes in region_nodes.values() for node in nodes}

    grids = {}
    for node, (pos, _) in clouds.items():
        span = float(np.linalg.norm(pos.max(axis=0) - pos.min(axis=0)))
        grids[node] = Grid(pos, max(span / 120, 1e-4))

    # Parse every ring first: continuity needs the ring BELOW, so nothing can be done line by line.
    parsed = []
    for number, line in enumerate(text.splitlines()):
        if not line.lstrip().startswith("{ node:"):
            parsed.append((number, line, None))
            continue
        node = int(re.search(r"node: (\d+)", line).group(1))
        y = float(re.search(r"y: (-?[\d.]+)", line).group(1))
        pts = [(float(a), float(b)) for a, b in
               re.findall(r"\[(-?[\d.]+),(-?[\d.]+)\]", line.split("points: [")[1])]
        parsed.append((number, line, {"node": node, "y": y, "points": pts}))

    rings = [p[2] for p in parsed if p[2]]
    order = sorted(range(len(rings)), key=lambda i: (rings[i]["node"], rings[i]["y"]))
    previous: dict[tuple[int, int], np.ndarray] = {}     # (node, spoke) -> settled UV
    distances: dict[int, list[float]] = {}
    switched = 0
    total = 0

    for i in order:
        ring = rings[i]
        node = ring["node"]
        pos, uv = clouds[node]
        grid = grids[node]
        assigned: list[np.ndarray] = []
        for k, (x, z) in enumerate(ring["points"]):
            q = np.array([x, ring["y"], z], dtype=np.float64)
            idx, dist = grid.candidates(q)
            nearest = float(dist.min())
            ok = idx[dist <= nearest + SLACK]
            total += 1
            # Reference UVs: the same spoke on the ring below, and the previous spoke on this ring.
            refs = [r for r in (previous.get((node, k)),
                                assigned[k - 1] if k else None) if r is not None]
            # CONTINUITY IS NOT APPLIED, and the reason is measured rather than assumed -- see the
            # module docstring's "MEASURED, THEN LEFT OFF" note. Kept as a no-op branch rather than
            # deleted, so the negative result stays visible at the point it was tested.
            choice = int(idx[int(np.argmin(dist))])
            if refs and len(ok) > 1:
                pass
            assigned.append(uv[choice])
            distances.setdefault(node, []).append(
                float(np.linalg.norm(pos[choice] - q)))
        for k, value in enumerate(assigned):
            previous[(node, k)] = value
        ring["uv"] = assigned

    out_lines = []
    for _, line, ring in parsed:
        if ring is None:
            out_lines.append(line)
            continue
        packed = ",".join(f"[{u:.5f},{v:.5f}]" for u, v in ring["uv"])
        # Replaces this script's own earlier `uv: []` (written by build_cross_sections.py) rather than
        # appending a second `uv:` key.
        out_lines.append(re.sub(r"uv: \[[^\]]*\] \},$", f"uv: [{packed}] }},", line.rstrip()))
    sections.write_text("\n".join(out_lines) + "\n")

    print(f"continuity chose a different vertex for {switched} of {total} points "
          f"({switched/total:.1%})\n")
    print(f"{'node':>5}{'points':>9}{'median dist':>14}{'p95':>10}{'max':>10}  (world units)")
    for node, ds in sorted(distances.items()):
        a = np.array(ds)
        print(f"{node:>5}{len(a):9d}{np.median(a):14.5f}{np.percentile(a,95):10.5f}{a.max():10.5f}")

    # Seam rates, both directions, so the claim is not made on one axis again.
    by_node: dict[int, list[tuple[float, np.ndarray]]] = {}
    spoke_bad = spoke_all = ring_bad = ring_all = 0
    for ring in rings:
        u = np.array(ring["uv"])
        d = np.linalg.norm(np.diff(np.vstack([u, u[:1]]), axis=0), axis=1)
        spoke_bad += int((d > 0.02).sum())
        spoke_all += len(d)
        by_node.setdefault(ring["node"], []).append((ring["y"], u))
    for node, rs in by_node.items():
        rs.sort(key=lambda t: t[0])
        for a, b in zip(rs, rs[1:]):
            if a[1].shape != b[1].shape:
                continue
            d = np.linalg.norm(a[1] - b[1], axis=1)
            ring_bad += int((d > 0.02).sum())
            ring_all += len(d)
    print(f"\nseam edges after continuity:")
    print(f"  spoke-wise {spoke_bad:6d} of {spoke_all:6d} = {spoke_bad/spoke_all:6.2%}")
    print(f"  ring-wise  {ring_bad:6d} of {ring_all:6d} = {ring_bad/ring_all:6.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
