#!/usr/bin/env python3
"""Does the mesh carry enough edge loops at each joint to survive being bent?

A skinned joint with too few rings of geometry across it collapses when it flexes: the elbow pinches
into a crease, the knee loses its volume, the shoulder creases into a fold. No amount of weight
tuning fixes it, because there is simply nothing there to deform -- the vertices needed to describe a
bent surface do not exist.

The usual answer to this in a mesh-generation pipeline is retopology, and the honest industry position
is that automatic remeshing does NOT reliably produce deformation-grade topology; people still
retopologize by hand. But that constraint belongs to pipelines that receive a finished mesh. A
procedural generator writes its own topology, so it can place loops at joints BY CONSTRUCTION and
never need to repair them afterwards. This module is the gate that makes that a requirement rather
than a hope.

Method: for each joint, take the vertices within a radius of it, project them onto the bone axis, and
count how many distinct bands along that axis they occupy. Bands, not raw vertex count: ten thousand
vertices in two rings still cannot bend, and counting vertices would call that mesh dense and pass it.

The gate deliberately does not try to identify true quad edge loops. Recovering loop structure from a
triangle soup is fragile, and the property that actually matters for deformation -- enough
independently movable rings across the joint -- is exactly what band counting measures.

Pure Python 3.10+ stdlib.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

MIN_LOOPS_PER_JOINT = 3
DEFAULT_RADIUS_SCALE = 0.35
BAND_TOLERANCE = 0.5


def _triangles(indices: list[Any]) -> list[tuple[int, int, int]]:
    if indices and isinstance(indices[0], (list, tuple)):
        return [(int(t[0]), int(t[1]), int(t[2])) for t in indices]
    return [
        (int(indices[i]), int(indices[i + 1]), int(indices[i + 2]))
        for i in range(0, len(indices) - 2, 3)
    ]


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    return math.sqrt(_dot(a, a))


def count_loops_at_joint(
    vertices: list[list[float]],
    joint: list[float],
    axis_start: list[float],
    axis_end: list[float],
    radius: float,
    band_tolerance: float = BAND_TOLERANCE,
) -> dict[str, Any]:
    """Count distinct vertex bands along the bone axis within `radius` of the joint.

    `band_tolerance` is a fraction of the mean gap between consecutive projections: two vertices
    closer together than that are the same ring. Using an absolute epsilon instead would make the
    count depend on the model's units, so the same character at two scales would score differently.
    """
    direction = _sub(axis_end, axis_start)
    length = _norm(direction)
    if length <= 1e-12:
        return {"loops": 0, "vertexCount": 0, "reason": "bone has zero length"}
    axis = [component / length for component in direction]

    # An AXIAL window, not a sphere around the joint. A sphere drops rings simply because the limb is
    # thick -- on a tube of radius 0.5 spanning a bone of length 2, the rings either side of the joint
    # sit 0.707 away and a 0.7 sphere excludes every one of them, scoring a perfectly bendable joint
    # as having a single loop. Thickness has nothing to do with whether a joint can bend; what matters
    # is how many bands sit ALONG the bone near the joint. The radial cap only keeps unrelated
    # geometry that happens to run parallel from being counted.
    radial_cap = radius * 2.0
    projections: list[float] = []
    for vertex in vertices:
        offset = _sub(vertex, joint)
        along = _dot(offset, axis)
        if abs(along) > radius:
            continue
        radial = math.sqrt(max(0.0, _dot(offset, offset) - along * along))
        if radial > radial_cap:
            continue
        projections.append(along)
    if len(projections) < 2:
        return {"loops": len(projections), "vertexCount": len(projections)}

    projections.sort()
    span = projections[-1] - projections[0]
    if span <= 1e-12:
        return {"loops": 1, "vertexCount": len(projections)}
    mean_gap = span / max(1, len(projections) - 1)
    threshold = mean_gap * band_tolerance

    loops = 1
    previous = projections[0]
    for value in projections[1:]:
        if value - previous > threshold:
            loops += 1
        previous = value
    return {"loops": loops, "vertexCount": len(projections), "axialSpan": round(span, 6)}


def analyze_joint_loops(
    meshes: list[dict[str, Any]],
    bones: list[dict[str, Any]],
    min_loops: int = MIN_LOOPS_PER_JOINT,
    radius_scale: float = DEFAULT_RADIUS_SCALE,
) -> dict[str, Any]:
    """Check every bone's joint against the pooled surface of all supplied meshes.

    Pooled rather than per mesh: a joint often sits where two components meet, and asking each mesh
    separately would let a joint pass because one half of it happens to be dense while the other has
    nothing there at all.
    """
    pooled: list[list[float]] = []
    for mesh in meshes:
        vertices = mesh.get("vertices")
        if isinstance(vertices, list):
            pooled.extend(vertices)
    if not pooled:
        raise ValueError("no vertices supplied")

    reports: list[dict[str, Any]] = []
    failures: list[str] = []
    for bone in bones:
        joint = bone.get("jointPos")
        tip = bone.get("tipPos")
        if not isinstance(joint, list) or not isinstance(tip, list):
            continue
        bone_length = _norm(_sub(tip, joint))
        radius = bone_length * radius_scale
        measured = count_loops_at_joint(pooled, joint, joint, tip, radius)
        record = {
            "bone": str(bone.get("id")),
            "radius": round(radius, 6),
            "minLoops": min_loops,
            **measured,
        }
        record["passes"] = measured["loops"] >= min_loops
        if not record["passes"]:
            failures.append(
                f"{record['bone']}: {measured['loops']} loop(s) within {record['radius']} of the "
                f"joint, needs {min_loops}; this joint will crease rather than bend"
            )
        reports.append(record)

    return {
        "joints": reports,
        "failures": failures,
        "jointCount": len(reports),
        "failingJointCount": len(failures),
        "passed": not failures,
        "note": (
            "loops are counted as distinct vertex bands along the bone axis, not as recovered quad "
            "loops; a dense joint with only two bands still cannot bend and is reported as such"
        ),
    }


def _format_summary(result: dict[str, Any]) -> str:
    lines = [f"joints checked: {result['jointCount']}"]
    for record in result["joints"]:
        flag = "ok" if record["passes"] else "TOO FEW LOOPS"
        lines.append(
            f"  [{flag}] {record['bone']} loops={record['loops']} "
            f"vertices={record['vertexCount']} radius={record['radius']}"
        )
    lines.append("verdict: PASS" if result["passed"] else "verdict: GATE FAILURE")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check edge-loop density at rig joints.")
    parser.add_argument("meshes", type=Path, help="JSON with a meshes array, or a single mesh")
    parser.add_argument("--bones", type=Path, required=True)
    parser.add_argument("--min-loops", type=int, default=MIN_LOOPS_PER_JOINT)
    parser.add_argument("--radius-scale", type=float, default=DEFAULT_RADIUS_SCALE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.meshes.read_text())
        meshes = payload["meshes"] if isinstance(payload, dict) and isinstance(payload.get("meshes"), list) else [payload]
        result = analyze_joint_loops(
            meshes, json.loads(args.bones.read_text()), args.min_loops, args.radius_scale
        )
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
