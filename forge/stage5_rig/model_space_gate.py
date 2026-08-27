#!/usr/bin/env python3
"""Fail closed when authored visual forward and consumer model space disagree.

The gate validates measured model-space evidence, not a label inferred from a screenshot. It uses
only Python's standard library and is intentionally independent of Three.js or a DCC.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


EPSILON = 1e-6
AXES = {
    "+X": (1.0, 0.0, 0.0),
    "-X": (-1.0, 0.0, 0.0),
    "+Y": (0.0, 1.0, 0.0),
    "-Y": (0.0, -1.0, 0.0),
    "+Z": (0.0, 0.0, 1.0),
    "-Z": (0.0, 0.0, -1.0),
}
CONVERSION_OWNERS = {"none", "export", "load-root-adapter"}


def _finite_vector(value: Any, label: str, errors: list[str]) -> tuple[float, float, float] | None:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or not all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in value
        )
    ):
        errors.append(f"{label} must be a finite length-3 vector")
        return None
    return tuple(float(item) for item in value)


def _dot(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _length(value: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(value, value))


def _normalized(value: tuple[float, float, float]) -> tuple[float, float, float] | None:
    length = _length(value)
    if length <= EPSILON:
        return None
    return tuple(item / length for item in value)


def _frame(payload: dict[str, Any], name: str, errors: list[str]) -> tuple[str, str] | None:
    frame = payload.get(name)
    if not isinstance(frame, dict):
        errors.append(f"{name} coordinate frame is required")
        return None
    up = frame.get("up")
    forward = frame.get("forward")
    if up not in AXES:
        errors.append(f"{name}.up must be one of {sorted(AXES)}")
    if forward not in AXES:
        errors.append(f"{name}.forward must be one of {sorted(AXES)}")
    if up not in AXES or forward not in AXES:
        return None
    if abs(_dot(AXES[up], AXES[forward])) > EPSILON:
        errors.append(f"{name}.up and {name}.forward must be orthogonal")
    return str(up), str(forward)


def _identity_root(payload: dict[str, Any], errors: list[str]) -> None:
    root = payload.get("rootTransform")
    if not isinstance(root, dict):
        errors.append("rootTransform is required")
        return
    position = _finite_vector(root.get("position"), "rootTransform.position", errors)
    scale = _finite_vector(root.get("scale"), "rootTransform.scale", errors)
    quaternion = root.get("quaternion")
    if (
        not isinstance(quaternion, list)
        or len(quaternion) != 4
        or not all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in quaternion
        )
    ):
        errors.append("rootTransform.quaternion must be a finite length-4 quaternion")
    elif any(
        abs(float(actual) - expected) > EPSILON
        for actual, expected in zip(quaternion, (0.0, 0.0, 0.0, 1.0))
    ):
        errors.append("semantic root quaternion must be identity")
    if position is not None and _length(position) > EPSILON:
        errors.append("semantic root position must be identity")
    if scale is not None and any(abs(actual - 1.0) > EPSILON for actual in scale):
        errors.append("semantic root scale must be identity")


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1")
    if payload.get("handedness") != "right":
        errors.append("handedness must be right")
    authoring = _frame(payload, "authoring", errors)
    target = _frame(payload, "target", errors)
    owner = payload.get("conversionOwner")
    if owner not in CONVERSION_OWNERS:
        errors.append(f"conversionOwner must be one of {sorted(CONVERSION_OWNERS)}")
    elif authoring is not None and target is not None:
        if authoring != target and owner == "none":
            errors.append("different authoring and target frames require one conversion owner")
        if authoring == target and owner != "none":
            errors.append("matching authoring and target frames must not add a conversion adapter")
    _identity_root(payload, errors)

    marker = _finite_vector(payload.get("forwardMarker"), "forwardMarker", errors)
    front = _finite_vector(payload.get("frontFeature"), "frontFeature", errors)
    rear = _finite_vector(payload.get("rearFeature"), "rearFeature", errors)
    target_forward = AXES[target[1]] if target is not None else None
    marker_direction = _normalized(marker) if marker is not None else None
    if marker is not None and marker_direction is None:
        errors.append("forwardMarker must not be at the semantic origin")
    elif marker_direction is not None and target_forward is not None:
        if _dot(marker_direction, target_forward) < 1.0 - EPSILON:
            errors.append("forwardMarker does not point along target.forward")
    if front is not None and target_forward is not None and _dot(front, target_forward) <= EPSILON:
        errors.append("frontFeature is not on the declared target.forward side")
    if rear is not None and target_forward is not None and _dot(rear, target_forward) >= -EPSILON:
        errors.append("rearFeature is not opposite the declared target.forward side")

    return {
        "schemaVersion": 1,
        "passed": not errors,
        "errors": errors,
        "summary": {
            "authoringForward": authoring[1] if authoring is not None else None,
            "targetForward": target[1] if target is not None else None,
            "conversionOwner": owner,
        },
        "evidenceBoundary": (
            "structural model-space evidence only; cardinal renders and runtime movement remain separate gates"
        ),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.contract.expanduser().read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("contract root must be an object")
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
    raise SystemExit(main(sys.argv[1:]))
