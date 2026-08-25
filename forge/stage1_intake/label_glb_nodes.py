#!/usr/bin/env python3
"""Label a multipart GLB's mesh nodes from MEASURED world-space bounds, never from their names.

WHY THIS REFUSES TO READ NAMES. `probe_glb.py` can report `reliableSemanticBoundary:
named-node-or-mesh` while every name is meaningless -- the reference this was written against has 16
nodes called `root.0`..`root.15` and 16 materials with the same strings. A pipeline that trusts those
names will confidently call a boot a knee pad, and every per-region comparison built on top inherits
the error silently. So the only inputs here are geometry: each node's world-space axis-aligned bounds,
normalised against the figure's own height.

WHAT THIS IS, EXACTLY. Hypothesis evidence, at the level `SKILL.md`'s GLB track allows for
connected-component/curvature segmentation -- NOT a confirmed semantic label. Every entry carries a
`confidence` and the measurement that produced it, and the report's `status` stays
`hypothesis-requires-render-confirmation` until a browser semantic-ID pass confirms it. Treating this
output as confirmed is the failure it is designed to make visible rather than prevent.

Only the glTF JSON chunk is read. Accessors carry POSITION `min`/`max`, so per-mesh bounds are exact
without decoding a single vertex -- which matters when the asset is 104 MB.

Pure Python 3.10+ standard library.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path
from typing import Any, Sequence

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A

# Height fractions of the figure's own bounding box. Derived from human proportion, not tuned against
# this asset: a boot sole is at 0, a head crown at 1. Bands overlap on purpose -- an overlap produces
# a lower confidence and a named ambiguity, which is the honest answer for a node that spans two.
BANDS: tuple[tuple[str, float, float], ...] = (
    ("footwear", 0.00, 0.12),
    ("lower-leg", 0.10, 0.30),
    ("knee", 0.28, 0.42),
    ("thigh", 0.38, 0.55),
    ("hip-waist", 0.50, 0.62),
    ("torso", 0.60, 0.80),
    ("shoulder-neck", 0.78, 0.88),
    ("head", 0.86, 1.00),
)

# Below this fraction of the figure's half-width, a node sits on the midline and has no side.
MIDLINE_FRACTION = 0.08


def _read_gltf_json(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        header = handle.read(12)
        if len(header) < 12:
            raise ValueError("file is too short to be a GLB")
        magic, _version, _length = struct.unpack("<III", header)
        if magic != GLB_MAGIC:
            raise ValueError("not a GLB container (bad magic)")
        chunk_header = handle.read(8)
        if len(chunk_header) < 8:
            raise ValueError("GLB has no chunk header")
        chunk_length, chunk_type = struct.unpack("<II", chunk_header)
        if chunk_type != CHUNK_JSON:
            raise ValueError("first GLB chunk is not JSON")
        payload = handle.read(chunk_length)
    return json.loads(payload.decode("utf-8"))


def _identity() -> list[float]:
    return [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def _compose(node: dict[str, Any]) -> list[float]:
    """Column-major 4x4 for a glTF node, from `matrix` or from TRS."""
    if isinstance(node.get("matrix"), list) and len(node["matrix"]) == 16:
        return [float(v) for v in node["matrix"]]
    tx, ty, tz = (node.get("translation") or [0.0, 0.0, 0.0])[:3]
    qx, qy, qz, qw = (node.get("rotation") or [0.0, 0.0, 0.0, 1.0])[:4]
    sx, sy, sz = (node.get("scale") or [1.0, 1.0, 1.0])[:3]
    # Rotation matrix from quaternion, then scale each basis column.
    xx, yy, zz = qx * qx, qy * qy, qz * qz
    xy, xz, yz = qx * qy, qx * qz, qy * qz
    wx, wy, wz = qw * qx, qw * qy, qw * qz
    r = [
        1 - 2 * (yy + zz), 2 * (xy + wz), 2 * (xz - wy),
        2 * (xy - wz), 1 - 2 * (xx + zz), 2 * (yz + wx),
        2 * (xz + wy), 2 * (yz - wx), 1 - 2 * (xx + yy),
    ]
    return [
        r[0] * sx, r[1] * sx, r[2] * sx, 0.0,
        r[3] * sy, r[4] * sy, r[5] * sy, 0.0,
        r[6] * sz, r[7] * sz, r[8] * sz, 0.0,
        float(tx), float(ty), float(tz), 1.0,
    ]


def _multiply(a: Sequence[float], b: Sequence[float]) -> list[float]:
    """Column-major `a * b`, applying b first."""
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
    return out


def _apply(matrix: Sequence[float], point: Sequence[float]) -> tuple[float, float, float]:
    x, y, z = point
    return (
        matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
        matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
        matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14],
    )


def _mesh_local_bounds(gltf: dict[str, Any], mesh_index: int) -> tuple[list[float], list[float]] | None:
    """Union of every primitive's POSITION accessor min/max. Exact, and reads no vertex data."""
    meshes = gltf.get("meshes") or []
    accessors = gltf.get("accessors") or []
    if mesh_index >= len(meshes):
        return None
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    found = False
    for primitive in meshes[mesh_index].get("primitives") or []:
        position = (primitive.get("attributes") or {}).get("POSITION")
        if position is None or position >= len(accessors):
            continue
        accessor = accessors[position]
        amin, amax = accessor.get("min"), accessor.get("max")
        if not (isinstance(amin, list) and isinstance(amax, list) and len(amin) >= 3):
            continue
        found = True
        for axis in range(3):
            lo[axis] = min(lo[axis], float(amin[axis]))
            hi[axis] = max(hi[axis], float(amax[axis]))
    return (lo, hi) if found else None


def _world_bounds(matrix: Sequence[float], lo: Sequence[float], hi: Sequence[float]) -> dict[str, list[float]]:
    """AABB of the transformed local AABB. All 8 corners, because a rotation makes the naive
    min/max-of-endpoints answer wrong."""
    wlo = [float("inf")] * 3
    whi = [float("-inf")] * 3
    for ix in (lo[0], hi[0]):
        for iy in (lo[1], hi[1]):
            for iz in (lo[2], hi[2]):
                for axis, value in enumerate(_apply(matrix, (ix, iy, iz))):
                    wlo[axis] = min(wlo[axis], value)
                    whi[axis] = max(whi[axis], value)
    return {
        "min": [round(v, 6) for v in wlo],
        "max": [round(v, 6) for v in whi],
        "center": [round((wlo[i] + whi[i]) / 2, 6) for i in range(3)],
        "size": [round(whi[i] - wlo[i], 6) for i in range(3)],
    }


def _collect(gltf: dict[str, Any]) -> list[dict[str, Any]]:
    """Every mesh-bearing node with its world matrix, walking the scene tree parents-first."""
    nodes = gltf.get("nodes") or []
    scenes = gltf.get("scenes") or []
    scene_index = gltf.get("scene", 0)
    roots = scenes[scene_index].get("nodes", []) if scene_index < len(scenes) else list(range(len(nodes)))
    collected: list[dict[str, Any]] = []
    stack: list[tuple[int, list[float]]] = [(int(r), _identity()) for r in roots]
    seen: set[int] = set()
    while stack:
        index, parent = stack.pop()
        if index in seen or index >= len(nodes):
            continue
        seen.add(index)
        node = nodes[index]
        world = _multiply(parent, _compose(node))
        if isinstance(node.get("mesh"), int):
            local = _mesh_local_bounds(gltf, node["mesh"])
            if local is not None:
                collected.append({
                    "nodeIndex": index,
                    "nodeName": node.get("name"),
                    "meshIndex": node["mesh"],
                    "bounds": _world_bounds(world, local[0], local[1]),
                })
        for child in node.get("children") or []:
            stack.append((int(child), world))
    return sorted(collected, key=lambda e: e["nodeIndex"])


def _classify(entry: dict[str, Any], figure: dict[str, list[float]]) -> dict[str, Any]:
    """Height band + lateral side, from measurement only. Overlap lowers confidence and is named."""
    base_y, top_y = figure["min"][1], figure["max"][1]
    height = max(top_y - base_y, 1e-9)
    centre = entry["bounds"]["center"]
    lo_f = (entry["bounds"]["min"][1] - base_y) / height
    hi_f = (entry["bounds"]["max"][1] - base_y) / height
    mid_f = (centre[1] - base_y) / height

    matches = [name for name, low, high in BANDS if not (hi_f < low or lo_f > high)]
    containing = [name for name, low, high in BANDS if low <= mid_f <= high]
    band = containing[0] if containing else (matches[0] if matches else "unbanded")

    half_width = max(abs(figure["min"][0]), abs(figure["max"][0]), 1e-9)
    lateral = centre[0] / half_width
    if abs(lateral) < MIDLINE_FRACTION:
        side = "midline"
    else:
        # With forward +Z and a right-handed frame the character's own left is +X. Same convention as
        # forge/_shared/chirality.py CHARACTER_LEFT_SIGN, which exists so this cannot silently diverge.
        side = "left" if lateral > 0 else "right"

    span_fraction = hi_f - lo_f
    # A node spanning most of the figure is a merged multi-part shell, not one body part. Saying so is
    # more useful than picking whichever band its centroid happens to land in.
    if span_fraction > 0.45:
        band, confidence, reason = "multi-region-shell", 0.20, "spans >45% of figure height"
    elif len(matches) > 2:
        confidence, reason = 0.35, f"overlaps {len(matches)} height bands: {', '.join(matches)}"
    elif len(matches) == 2:
        confidence, reason = 0.55, f"straddles {matches[0]} and {matches[1]}"
    else:
        confidence, reason = 0.70, "centroid falls inside exactly one height band"

    return {
        "partHypothesis": f"{band}-{side}" if side != "midline" else band,
        "band": band,
        "side": side,
        "confidence": confidence,
        "evidence": {
            "method": "measured-world-bounds",
            "heightFractionRange": [round(lo_f, 4), round(hi_f, 4)],
            "centroidHeightFraction": round(mid_f, 4),
            "lateralFractionOfHalfWidth": round(lateral, 4),
            "reason": reason,
        },
    }


def label(path: Path) -> dict[str, Any]:
    gltf = _read_gltf_json(path)
    entries = _collect(gltf)
    if not entries:
        raise ValueError("no mesh-bearing nodes found; this is not usable as a multipart baseline")

    lo = [min(e["bounds"]["min"][i] for e in entries) for i in range(3)]
    hi = [max(e["bounds"]["max"][i] for e in entries) for i in range(3)]
    figure = {
        "min": [round(v, 6) for v in lo],
        "max": [round(v, 6) for v in hi],
        "size": [round(hi[i] - lo[i], 6) for i in range(3)],
    }

    labelled = []
    for entry in entries:
        labelled.append({**entry, **_classify(entry, figure)})

    # A name is recorded as an observation and never used as evidence. If a caller ever wants to trust
    # it, this flag is what they have to argue past.
    names = [e.get("nodeName") for e in entries]
    return {
        "schemaVersion": 1,
        "kind": "glb-semantic-map",
        "status": "hypothesis-requires-render-confirmation",
        "source": {"path": str(path), "nodeCount": len(entries)},
        "figureBounds": figure,
        "namesUsedAsEvidence": False,
        "observedNodeNames": names,
        "nodes": labelled,
        "note": (
            "Labels are hypotheses from measured world bounds, at the confidence level SKILL.md allows "
            "for geometric segmentation. Confirm each with a browser semantic-ID pass before asserting "
            "any per-region result against this baseline."
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("glb", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.0,
        help="exit 1 if any node lands below this; use it to gate a downstream per-region claim",
    )
    args = parser.parse_args(argv)

    try:
        report = label(args.glb)
    except ValueError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 2

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    weak = [n for n in report["nodes"] if n["confidence"] < args.min_confidence]
    for node in report["nodes"]:
        print(
            f"  node {node['nodeIndex']:>3} {str(node['nodeName'])[:18]:20s} "
            f"{node['partHypothesis']:24s} conf={node['confidence']:.2f}  "
            f"y={node['evidence']['heightFractionRange']}  {node['evidence']['reason']}"
        )
    print(f"\n{len(report['nodes'])} nodes, figure size {report['figureBounds']['size']}")
    print(f"status: {report['status']}")
    if weak:
        print(
            f"\nFAILED: {len(weak)} node(s) below --min-confidence {args.min_confidence}: "
            f"{', '.join(str(n['nodeIndex']) for n in weak)}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
