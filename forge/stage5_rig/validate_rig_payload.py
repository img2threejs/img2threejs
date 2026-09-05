#!/usr/bin/env python3
"""Validate the code-only alpha skeleton/skinning payload.

This is a structural gate, not a rigging model. It intentionally uses only Python's
standard library and never downloads or executes UniRig checkpoints.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


EPSILON = 1e-6
WEIGHT_TOLERANCE = 1e-4
MAX_INFLUENCES = 4


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def vector3(value: Any, label: str, errors: list[str]) -> tuple[float, float, float] | None:
    if not isinstance(value, list) or len(value) != 3 or not all(finite_number(item) for item in value):
        errors.append(f"{label} must be a finite length-3 vector")
        return None
    return float(value[0]), float(value[1]), float(value[2])


def matrix16(value: Any, label: str, errors: list[str]) -> list[float] | None:
    if isinstance(value, list) and len(value) == 16 and all(finite_number(item) for item in value):
        flat = [float(item) for item in value]
    elif isinstance(value, list) and len(value) == 4 and all(isinstance(row, list) and len(row) == 4 for row in value):
        if not all(finite_number(item) for row in value for item in row):
            errors.append(f"{label} must contain only finite numbers")
            return None
        flat = [float(item) for row in value for item in row]
    else:
        errors.append(f"{label} must be a finite 4x4 matrix or flat length-16 matrix")
        return None
    last_row = flat[12:16]
    if any(abs(actual - expected) > WEIGHT_TOLERANCE for actual, expected in zip(last_row, (0.0, 0.0, 0.0, 1.0))):
        errors.append(f"{label} must have affine last row [0, 0, 0, 1]")
    column_scales = [math.sqrt(sum(flat[row * 4 + column] ** 2 for row in range(3))) for column in range(3)]
    if any(scale <= EPSILON for scale in column_scales):
        errors.append(f"{label} contains a zero-scale basis column")
    return flat


def distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(a, b)))


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if payload.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1")

    coordinate = payload.get("coordinateSystem")
    if not isinstance(coordinate, dict):
        errors.append("coordinateSystem is required")
    else:
        if coordinate.get("up") != "Y":
            errors.append("coordinateSystem.up must be Y")
        if coordinate.get("handedness") != "right":
            errors.append("coordinateSystem.handedness must be right")
        if not isinstance(coordinate.get("unit"), str) or not coordinate["unit"]:
            errors.append("coordinateSystem.unit must be a non-empty string")

    joints_raw = payload.get("joints")
    parents = payload.get("parents")
    names = payload.get("names")
    matrices = payload.get("matrix_local")
    joints: list[tuple[float, float, float]] = []
    if not isinstance(joints_raw, list) or not joints_raw:
        errors.append("joints must be a non-empty list")
    else:
        for index, value in enumerate(joints_raw):
            parsed = vector3(value, f"joints[{index}]", errors)
            if parsed is not None:
                joints.append(parsed)

    joint_count = len(joints_raw) if isinstance(joints_raw, list) else 0
    if not isinstance(parents, list) or len(parents) != joint_count:
        errors.append("parents must have one entry per joint")
        parents = parents if isinstance(parents, list) else []
    if not isinstance(names, list) or len(names) != joint_count:
        errors.append("names must have one entry per joint")
        names = names if isinstance(names, list) else []
    if not isinstance(matrices, list) or len(matrices) != joint_count:
        errors.append("matrix_local must have one matrix per joint")
        matrices = matrices if isinstance(matrices, list) else []

    if isinstance(names, list):
        seen: set[str] = set()
        for index, name in enumerate(names):
            if not isinstance(name, str) or not name.strip():
                errors.append(f"names[{index}] must be a non-empty string")
            elif name in seen:
                errors.append(f"duplicate joint name: {name}")
            seen.add(name)

    if parents and parents[0] is not None:
        errors.append("parents[0] must be null for the single root")
    for index, parent in enumerate(parents[1:], start=1):
        if not isinstance(parent, int) or isinstance(parent, bool):
            errors.append(f"parents[{index}] must be an integer parent index")
        elif parent < 0 or parent >= index:
            errors.append(f"parents[{index}] must satisfy 0 <= parent < child index")
        elif index < len(joints) and parent < len(joints) and distance(joints[index], joints[parent]) <= EPSILON:
            errors.append(f"joint {index} has zero-length parent bone {parent}")

    for index, matrix in enumerate(matrices):
        matrix16(matrix, f"matrix_local[{index}]", errors)

    skin_indices = payload.get("skinIndex")
    skin_weights = payload.get("skinWeight")
    if not isinstance(skin_indices, list) or not isinstance(skin_weights, list):
        errors.append("skinIndex and skinWeight are required packed arrays")
        skin_indices = skin_indices if isinstance(skin_indices, list) else []
        skin_weights = skin_weights if isinstance(skin_weights, list) else []
    if len(skin_indices) != len(skin_weights):
        errors.append("skinIndex and skinWeight must have the same vertex count")
    vertex_count = min(len(skin_indices), len(skin_weights))
    active_counts = [0] * joint_count
    for vertex in range(vertex_count):
        indices = skin_indices[vertex]
        weights = skin_weights[vertex]
        if not isinstance(indices, list) or len(indices) != MAX_INFLUENCES:
            errors.append(f"skinIndex[{vertex}] must have exactly {MAX_INFLUENCES} slots")
            continue
        if not isinstance(weights, list) or len(weights) != MAX_INFLUENCES:
            errors.append(f"skinWeight[{vertex}] must have exactly {MAX_INFLUENCES} slots")
            continue
        for slot, joint in enumerate(indices):
            if not isinstance(joint, int) or isinstance(joint, bool) or joint < 0 or joint >= joint_count:
                errors.append(f"skinIndex[{vertex}][{slot}] points outside the joint array")
            if finite_number(weights[slot]) and float(weights[slot]) > EPSILON and isinstance(joint, int) and 0 <= joint < joint_count:
                active_counts[joint] += 1
        if not all(finite_number(weight) and float(weight) >= 0 for weight in weights):
            errors.append(f"skinWeight[{vertex}] must contain finite non-negative values")
        else:
            total = sum(float(weight) for weight in weights)
            if abs(total - 1.0) > WEIGHT_TOLERANCE:
                errors.append(f"skinWeight[{vertex}] must sum to 1 (got {total:.6f})")
            if total <= EPSILON:
                errors.append(f"skinWeight[{vertex}] must influence at least one joint")

    unweighted = [names[index] if index < len(names) else str(index) for index, count in enumerate(active_counts) if count == 0]
    if unweighted:
        warnings.append("joints with no active vertex influence: " + ", ".join(unweighted))

    return {
        "schemaVersion": 1,
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "jointCount": joint_count,
            "vertexCount": vertex_count,
            "maxInfluences": MAX_INFLUENCES,
            "activeVertexCountByJoint": active_counts,
        },
        "evidenceBoundary": "structural payload only; runtime deformation and screenshot gates are separate",
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.payload.expanduser().read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("payload root must be an object")
        result = validate(payload)
        serialized = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        if args.out:
            args.out.expanduser().parent.mkdir(parents=True, exist_ok=True)
            args.out.expanduser().write_text(serialized, encoding="utf-8")
        print(serialized, end="")
        return 0 if result["passed"] else 1
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
