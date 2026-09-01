#!/usr/bin/env python3
"""Rebase object-frame component transforms onto the parent-local contract.

ObjectSculptSpec authors `transform.position` and `attachment.localStart` /
`attachment.localEnd` in PARENT-LOCAL coordinates (see
grimoire/readiness/joint_attachment.md). A spec authored in object-frame
absolute coordinates passes structural validation but renders with every
child displaced by its parent's own offset (double-offset floating parts).

This one-shot converter subtracts each component's parent object-frame
position from the component's position and attachment endpoints, producing a
new spec file (never in place) plus a JSON report of every changed field for
human review. It refuses to guess: any non-zero rotation on a component that
has children aborts the conversion, because the subtraction is only valid
when parent frames are axis-aligned with the object frame.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_EPSILON = 1e-9


def _iter_components(tree: object) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def visit(items: object) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("id"), str):
                result.append(item)
            visit(item.get("children"))

    visit(tree)
    return result


def _vector3(value: object, *, context: str) -> list[float]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or not all(isinstance(axis, (int, float)) and not isinstance(axis, bool) for axis in value)
    ):
        raise ValueError(f"{context} is not a numeric [x, y, z] vector")
    return [float(axis) for axis in value]


def _subtract(vector: list[float], delta: list[float]) -> list[float]:
    return [round(a - b, 9) for a, b in zip(vector, delta)]


def rebase_component_frames(spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (rebased spec, change report). The input spec is not mutated."""
    rebased = json.loads(json.dumps(spec))
    components = _iter_components(rebased.get("componentTree"))
    if not components:
        raise ValueError("spec has no componentTree components to rebase")

    by_id: dict[str, dict[str, Any]] = {}
    for component in components:
        component_id = component["id"]
        if component_id in by_id:
            raise ValueError(f"duplicate component id {component_id!r}")
        by_id[component_id] = component

    # The object-frame position of every component is its ORIGINAL authored
    # position, so all deltas are read before any component is rewritten.
    original_positions: dict[str, list[float]] = {}
    parent_ids: set[str] = set()
    for component in components:
        component_id = component["id"]
        transform = component.get("transform")
        if not isinstance(transform, dict):
            raise ValueError(f"component {component_id!r} has no transform")
        original_positions[component_id] = _vector3(
            transform.get("position"), context=f"component {component_id!r} transform.position"
        )
        parent = component.get("parent")
        if parent is not None:
            if not isinstance(parent, str) or parent not in by_id:
                raise ValueError(f"component {component_id!r} references unknown parent {parent!r}")
            parent_ids.add(parent)

    for parent_id in sorted(parent_ids):
        rotation = _vector3(
            by_id[parent_id].get("transform", {}).get("rotation", [0, 0, 0]),
            context=f"component {parent_id!r} transform.rotation",
        )
        if any(abs(axis) > _EPSILON for axis in rotation):
            raise ValueError(
                f"component {parent_id!r} has children and a non-zero rotation; "
                "the object-frame subtraction is only valid for axis-aligned parent "
                "frames — rebase this subtree manually"
            )

    changes: list[dict[str, Any]] = []
    warnings: list[str] = []

    def record(component_id: str, field: str, old: list[float], new: list[float]) -> list[float]:
        if any(abs(a - b) > _EPSILON for a, b in zip(old, new)):
            changes.append({"componentId": component_id, "field": field, "old": old, "new": new})
        return new

    for component in components:
        component_id = component["id"]
        parent = component.get("parent")
        if parent is None:
            continue
        delta = original_positions[parent]
        if all(abs(axis) <= _EPSILON for axis in delta):
            continue
        old_position = original_positions[component_id]
        new_position = _subtract(old_position, delta)
        component["transform"]["position"] = record(
            component_id, "transform.position", old_position, new_position
        )
        if _magnitude(new_position) > _magnitude(old_position) + _EPSILON:
            warnings.append(
                f"component {component_id!r} moved further from its parent origin "
                f"({old_position} -> {new_position}); it may already have been parent-local"
            )
        attachment = component.get("attachment")
        if isinstance(attachment, dict):
            for field in ("localStart", "localEnd"):
                if attachment.get(field) is None:
                    continue
                old_value = _vector3(
                    attachment[field], context=f"component {component_id!r} attachment.{field}"
                )
                attachment[field] = record(
                    component_id, f"attachment.{field}", old_value, _subtract(old_value, delta)
                )

    report = {
        "schemaVersion": 1,
        "kind": "component-frame-rebase-report",
        "targetId": spec.get("targetId"),
        "componentCount": len(components),
        "changedFieldCount": len(changes),
        "changes": changes,
        "warnings": warnings,
    }
    return rebased, report


def _magnitude(vector: list[float]) -> float:
    return sum(axis * axis for axis in vector) ** 0.5


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="object-frame spec to convert")
    parser.add_argument("--output", type=Path, required=True, help="destination for the rebased spec")
    parser.add_argument("--report", type=Path, required=True, help="destination for the change report")
    args = parser.parse_args(argv)
    if args.output.resolve() == args.input.resolve():
        print("refusing to rewrite the input spec in place; choose a new --output", file=sys.stderr)
        return 2
    try:
        spec = json.loads(args.input.read_text(encoding="utf-8"))
        rebased, report = rebase_component_frames(spec)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"rebase failed: {error}", file=sys.stderr)
        return 1
    args.output.write_text(json.dumps(rebased, indent=2) + "\n", encoding="utf-8")
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"rebased {report['changedFieldCount']} fields across {report['componentCount']} components"
        + (f"; {len(report['warnings'])} warnings" if report["warnings"] else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
