#!/usr/bin/env python3
"""Morph targets: position deltas for expression, and correctives for what skinning cannot do.

Nothing in this repository emitted morph targets, which closes off two things a character needs.

The obvious one is facial expression. Bones can drive a jaw, but a blink, a sneer or a brow raise is
surface motion with no skeleton behind it, and Three.js supports it natively through
`morphAttributes.position` -- a morph target is just a second position buffer, so this needs no new
runtime machinery at all.

The less obvious one matters more for a rigged character: CORRECTIVE shapes. Linear blend skinning
always loses volume as a joint closes -- an elbow at 120 degrees pinches, a shoulder collapses -- and
no weight assignment fixes it, because the artefact is in the interpolation, not in the weights. The
repair is a shape that fades in as the joint bends and puts the volume back.

Deltas, not absolute positions. Three.js `morphAttributes` with `morphTargetsRelative` expects
offsets from the base, storing an absolute copy of every vertex per target multiplies memory by the
number of targets, and a delta array is mostly zeros and compresses to nothing.

Pure Python 3.10+ stdlib.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

DEFAULT_EPSILON = 1e-6


def make_morph_target(
    base_vertices: list[list[float]],
    target_vertices: list[list[float]],
    name: str,
    epsilon: float = DEFAULT_EPSILON,
) -> dict[str, Any]:
    """Delta between two vertex arrays that must correspond one-to-one.

    A mismatched count is refused rather than zip-truncated. Silently pairing the first N vertices of
    two different meshes produces a morph that looks like a plausible deformation and is nonsense,
    and nothing downstream can tell.
    """
    if len(base_vertices) != len(target_vertices):
        raise ValueError(
            f"morph target {name!r} has {len(target_vertices)} vertices but the base has "
            f"{len(base_vertices)}; targets must share the base's vertex order"
        )
    deltas: list[list[float]] = []
    moved = 0
    largest = 0.0
    for base, target in zip(base_vertices, target_vertices):
        delta = [target[axis] - base[axis] for axis in range(3)]
        magnitude = math.sqrt(sum(component * component for component in delta))
        if magnitude > epsilon:
            moved += 1
            largest = max(largest, magnitude)
        else:
            delta = [0.0, 0.0, 0.0]
        deltas.append([round(component, 6) for component in delta])
    return {
        "name": name,
        "deltas": deltas,
        "movedVertexCount": moved,
        "vertexCount": len(base_vertices),
        "maxDisplacement": round(largest, 6),
        # A target that moves nothing is almost always a mistake upstream -- the wrong file, or a pose
        # that failed to apply -- and it costs a full vertex buffer at runtime to achieve nothing.
        "isNoOp": moved == 0,
    }


def build_morph_set(
    base: dict[str, Any],
    targets: list[dict[str, Any]],
    epsilon: float = DEFAULT_EPSILON,
) -> dict[str, Any]:
    base_vertices = base.get("vertices")
    if not isinstance(base_vertices, list) or not base_vertices:
        raise ValueError("base mesh must have vertices")
    built = [
        make_morph_target(base_vertices, target["vertices"], str(target.get("name", f"morph{i}")), epsilon)
        for i, target in enumerate(targets)
    ]
    return {
        "base": str(base.get("name", "base")),
        "vertexCount": len(base_vertices),
        "morphTargetsRelative": True,
        "targets": built,
        "noOpTargets": [target["name"] for target in built if target["isNoOp"]],
        "totalMovedVertices": sum(target["movedVertexCount"] for target in built),
        "note": (
            "deltas are relative to the base; set mesh.morphTargetsRelative = true in Three.js or "
            "every target will be interpreted as an absolute position and the mesh will collapse "
            "toward the origin"
        ),
    }


def corrective_from_bend(
    base_vertices: list[list[float]],
    joint: list[float],
    axis: list[float],
    radius: float,
    volume_gain: float = 0.12,
) -> list[list[float]]:
    """A generated corrective: push the surface out radially near a joint.

    This is the shape that compensates for the volume linear blend skinning loses as a joint closes.
    It is a plausible default, not a simulation: real correctives are sculpted against the actual bent
    pose. It exists so a rig has something to blend in rather than nothing, and callers who have a
    real bent pose should use `make_morph_target` with it instead.
    """
    length = math.sqrt(sum(component * component for component in axis))
    if length <= 1e-12:
        raise ValueError("axis must be non-zero")
    unit = [component / length for component in axis]
    moved: list[list[float]] = []
    for vertex in base_vertices:
        offset = [vertex[i] - joint[i] for i in range(3)]
        along = sum(offset[i] * unit[i] for i in range(3))
        radial = [offset[i] - along * unit[i] for i in range(3)]
        radial_length = math.sqrt(sum(component * component for component in radial))
        distance = math.sqrt(sum(component * component for component in offset))
        if distance > radius or radial_length <= 1e-9:
            moved.append(list(vertex))
            continue
        # Cosine falloff so the corrective blends to zero at the edge of its radius instead of ending
        # in a hard ring, which would read as a crease of its own.
        falloff = 0.5 * (1.0 + math.cos(math.pi * distance / radius))
        scale = volume_gain * falloff
        moved.append([vertex[i] + radial[i] / radial_length * scale for i in range(3)])
    return moved


def _format_summary(result: dict[str, Any]) -> str:
    lines = [
        f"base: {result['base']} ({result['vertexCount']} vertices)",
        f"targets: {len(result['targets'])}, moved vertices total: {result['totalMovedVertices']}",
    ]
    for target in result["targets"]:
        lines.append(
            f"  {target['name']}: {target['movedVertexCount']} moved, "
            f"max displacement {target['maxDisplacement']}"
        )
    if result["noOpTargets"]:
        lines.append(
            f"NO-OP targets {result['noOpTargets']}: these move nothing and cost a full vertex "
            "buffer each; check the source pose actually applied"
        )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build morph-target deltas from posed meshes.")
    parser.add_argument("base", type=Path)
    parser.add_argument("--target", action="append", default=[], type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        base = json.loads(args.base.read_text())
        targets = [json.loads(path.read_text()) for path in args.target]
        result = build_morph_set(base, targets)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.out:
        args.out.write_text(json.dumps(result))
    print(json.dumps(result, indent=2) if args.json else _format_summary(result))
    return 1 if result["noOpTargets"] else 0


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
