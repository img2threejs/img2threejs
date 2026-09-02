#!/usr/bin/env python3
"""Create reversible, bounded ObjectSculptSpec proposals from admitted evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.stage1_intake.check_dense_evidence import validate_dense_evidence


GLOBAL_NUMERIC_FIELDS = frozenset(
    {
        "dimensions.width",
        "dimensions.height",
        "dimensions.depth",
        "dimensions.radius",
        "dimensions.length",
        "transform.position.0",
        "transform.position.1",
        "transform.position.2",
    }
)
COMPONENT_NUMERIC_FIELDS = frozenset(
    {
        "dimensions.width",
        "dimensions.height",
        "dimensions.depth",
        "dimensions.radius",
        "dimensions.length",
    }
)


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def bounded_ratio(measured: float, authored: float, maximum_delta: float) -> float:
    if not math.isfinite(measured) or not math.isfinite(authored) or authored <= 0:
        raise ValueError("invalid_geometry_measurement: sizes must be finite and positive")
    raw = measured / authored
    return min(1.0 + maximum_delta, max(1.0 - maximum_delta, raw))


def _components(spec: dict[str, object]) -> list[tuple[dict[str, Any], list[object], tuple[float, float, float]]]:
    result: list[tuple[dict[str, Any], list[object], tuple[float, float, float]]] = []

    def visit(items: object, path: list[object], parent_position: tuple[float, float, float]) -> None:
        if not isinstance(items, list):
            return
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            transform = item.get("transform")
            position = transform.get("position") if isinstance(transform, dict) else None
            local = (
                tuple(float(value) for value in position)
                if isinstance(position, list)
                and len(position) == 3
                and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in position)
                else (0.0, 0.0, 0.0)
            )
            world = tuple(parent_position[axis] + local[axis] for axis in range(3))
            item_path = [*path, index]
            result.append((item, item_path, world))
            visit(item.get("children"), [*item_path, "children"], world)

    visit(spec.get("componentTree"), ["componentTree"], (0.0, 0.0, 0.0))
    return result


def _authored_size(spec: dict[str, object]) -> tuple[float, float, float]:
    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    seen = False
    for component, _path, world in _components(spec):
        dimensions = component.get("dimensions")
        if not isinstance(dimensions, dict):
            continue
        values = [dimensions.get("width"), dimensions.get("height"), dimensions.get("depth")]
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) > 0
            for value in values
        ):
            continue
        seen = True
        for axis, value in enumerate(values):
            half = float(value) / 2.0
            minimum[axis] = min(minimum[axis], world[axis] - half)
            maximum[axis] = max(maximum[axis], world[axis] + half)
    if not seen:
        raise ValueError("invalid_geometry_measurement: no component xyz dimensions")
    return tuple(maximum[axis] - minimum[axis] for axis in range(3))


def _max_delta(spec: dict[str, object]) -> float:
    quality = spec.get("qualityContract")
    dense = quality.get("denseEvidence") if isinstance(quality, dict) else None
    value = dense.get("maxNumericDeltaFraction") if isinstance(dense, dict) else 0.20
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0 < float(value) <= 0.5
    ):
        raise ValueError("invalid_dense_evidence_policy: maxNumericDeltaFraction must be in (0, 0.5]")
    return float(value)


def _set_change(
    component: dict[str, Any],
    path: list[object],
    field: str,
    new_value: float,
    changes: list[dict[str, object]],
    *,
    scope: str,
    measured: float,
    confidence: float,
    source_region: str,
) -> None:
    tokens: list[object] = field.split(".")
    resolved_tokens: list[object] = []
    target: Any = component
    for token in tokens[:-1]:
        if isinstance(target, dict):
            target = target.get(token)
            resolved_tokens.append(token)
        elif isinstance(target, list):
            index = int(token)
            target = target[index]
            resolved_tokens.append(index)
        else:
            return
    final = tokens[-1]
    old: object
    if isinstance(target, dict):
        old = target.get(final)
        if not isinstance(old, (int, float)) or isinstance(old, bool):
            return
        target[final] = new_value
    elif isinstance(target, list):
        index = int(final)
        if index >= len(target) or not isinstance(target[index], (int, float)) or isinstance(target[index], bool):
            return
        old = target[index]
        target[index] = new_value
        resolved_final: object = index
    else:
        return
    if isinstance(target, dict):
        resolved_final = final
    if math.isclose(float(old), new_value, rel_tol=1e-12, abs_tol=1e-12):
        if isinstance(target, dict):
            target[final] = old
        else:
            target[int(final)] = old
        return
    changes.append(
        {
            "path": [*path, *resolved_tokens, resolved_final],
            "componentId": component.get("id"),
            "field": field,
            "old": float(old),
            "new": float(new_value),
            "measured": float(measured),
            "confidence": float(confidence),
            "sourceRegion": source_region,
            "scope": scope,
            "reason": "bounded dense-evidence measurement proposal",
        }
    )


def _validate_binding(
    spec: dict[str, object],
    evidence: dict[str, object],
    admission: dict[str, object],
    component_map: dict[str, object] | None,
) -> str:
    if admission.get("decision") != "ALLOW" or not isinstance(admission.get("binding"), dict):
        raise ValueError("influence_not_approved: ALLOW admission is required")
    binding = admission["binding"]
    scope = admission.get("approvedInfluenceScope")
    provenance = evidence.get("provenance")
    if scope not in {"global-massing", "component-measurements"}:
        raise ValueError("influence_scope_exceeded: unsupported approval scope")
    expected = {
        "targetSpecSha256": _canonical_sha256(spec),
        "evidenceSha256": _canonical_sha256(evidence),
        "glbSha256": provenance.get("glbSha256") if isinstance(provenance, dict) else None,
        "scope": scope,
    }
    if component_map is not None:
        expected["componentMapSha256"] = _canonical_sha256(component_map)
    if any(binding.get(field) != value for field, value in expected.items()):
        raise ValueError("admission_hash_mismatch: approval tuple no longer matches inputs")
    return str(scope)


def _global_proposal(
    proposal: dict[str, object], evidence: dict[str, object], changes: list[dict[str, object]]
) -> tuple[float, float, float]:
    geometry = evidence.get("globalGeometry")
    bounds = geometry.get("bounds") if isinstance(geometry, dict) else None
    measured = bounds.get("size") if isinstance(bounds, dict) else None
    if not isinstance(measured, list) or len(measured) != 3:
        raise ValueError("invalid_geometry_measurement: evidence bounds size is missing")
    authored = _authored_size(proposal)
    maximum_delta = _max_delta(proposal)
    measured_values = tuple(float(value) for value in measured)
    if not all(math.isfinite(value) and value > 0 for value in measured_values):
        raise ValueError("invalid_geometry_measurement: evidence bounds must be finite and positive")
    authored_anchor = math.prod(authored) ** (1.0 / 3.0)
    measured_anchor = math.prod(measured_values) ** (1.0 / 3.0)
    scale = tuple(
        bounded_ratio(
            measured_values[axis] / measured_anchor,
            authored[axis] / authored_anchor,
            maximum_delta,
        )
        for axis in range(3)
    )
    for component, path, _world in _components(proposal):
        dimensions = component.get("dimensions")
        if isinstance(dimensions, dict):
            for field, factor, measurement in (
                ("dimensions.width", scale[0], float(measured[0])),
                ("dimensions.height", scale[1], float(measured[1])),
                ("dimensions.depth", scale[2], float(measured[2])),
            ):
                value = dimensions.get(field.split(".")[-1])
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    _set_change(component, path, field, float(value) * factor, changes, scope="global-massing", measured=measurement, confidence=0.8, source_region="global")
            radius = dimensions.get("radius")
            if isinstance(radius, (int, float)) and not isinstance(radius, bool):
                _set_change(component, path, "dimensions.radius", float(radius) * ((scale[0] + scale[2]) / 2.0), changes, scope="global-massing", measured=(float(measured[0]) + float(measured[2])) / 2.0, confidence=0.75, source_region="global")
            length = dimensions.get("length")
            dominant = component.get("dominantAxis")
            if isinstance(length, (int, float)) and not isinstance(length, bool) and dominant in {"x", "y", "z"}:
                axis = {"x": 0, "y": 1, "z": 2}[str(dominant)]
                _set_change(component, path, "dimensions.length", float(length) * scale[axis], changes, scope="global-massing", measured=float(measured[axis]), confidence=0.75, source_region="global")
        transform = component.get("transform")
        position = transform.get("position") if isinstance(transform, dict) else None
        if isinstance(position, list) and len(position) == 3:
            for axis in range(3):
                if isinstance(position[axis], (int, float)) and not isinstance(position[axis], bool):
                    _set_change(component, path, f"transform.position.{axis}", float(position[axis]) * scale[axis], changes, scope="global-massing", measured=float(measured[axis]), confidence=0.8, source_region="global")
    return scale


def _component_proposal(
    proposal: dict[str, object],
    evidence: dict[str, object],
    component_map: dict[str, object] | None,
    changes: list[dict[str, object]],
) -> None:
    if component_map is not None:
        mappings = component_map.get("mappings")
        if isinstance(mappings, list):
            for mapping in mappings:
                fields = mapping.get("permittedFields") if isinstance(mapping, dict) else None
                if isinstance(fields, list) and any(
                    field not in COMPONENT_NUMERIC_FIELDS for field in fields
                ):
                    raise ValueError(
                        "influence_scope_exceeded: component field is forbidden"
                    )
    validation = validate_dense_evidence(evidence, proposal, component_map)
    if not validation["passed"]:
        category = validation["failureCategories"][0] if validation["failureCategories"] else "component_mapping_invalid"
        raise ValueError(f"{category}: {'; '.join(validation['errors'])}")
    if component_map is None:
        raise ValueError("component_mapping_invalid: component map is required")
    components = {str(item.get("id")): (item, path) for item, path, _world in _components(proposal)}
    regions = {
        str(item.get("regionId")): item
        for item in evidence.get("regions", [])
        if isinstance(item, dict)
    }
    for mapping in component_map.get("mappings", []):
        fields = mapping.get("permittedFields", [])
        if any(field not in COMPONENT_NUMERIC_FIELDS for field in fields):
            raise ValueError("influence_scope_exceeded: component field is forbidden")
        component, path = components[str(mapping["componentId"])]
        region = regions[str(mapping["selectors"][0]["regionId"])]
        size = region["bounds"]["size"]
        dimensions = component.get("dimensions", {})
        for field in fields:
            key = field.split(".")[-1]
            if key not in {"width", "height", "depth"}:
                continue
            axis = {"width": 0, "height": 1, "depth": 2}[key]
            old = dimensions.get(key)
            if not isinstance(old, (int, float)) or isinstance(old, bool):
                continue
            factor = bounded_ratio(float(size[axis]), float(old), _max_delta(proposal))
            _set_change(component, path, field, float(old) * factor, changes, scope="component-measurements", measured=float(size[axis]), confidence=float(mapping["confidence"]), source_region=str(region["regionId"]))


def build_proposal(
    accepted_spec: dict[str, object],
    evidence: dict[str, object],
    admission: dict[str, object],
    component_map: dict[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    scope = _validate_binding(accepted_spec, evidence, admission, component_map)
    proposal = copy.deepcopy(accepted_spec)
    changes: list[dict[str, object]] = []
    if scope == "global-massing":
        scale = _global_proposal(proposal, evidence, changes)
    else:
        _component_proposal(proposal, evidence, component_map, changes)
        scale = (1.0, 1.0, 1.0)
    delta = {
        "schemaVersion": 1,
        "kind": "dense-evidence-spec-delta",
        "acceptedSpecSha256": _canonical_sha256(accepted_spec),
        "proposedSpecSha256": _canonical_sha256(proposal),
        "evidenceSha256": _canonical_sha256(evidence),
        "approvedScope": scope,
        "changes": changes,
    }
    fit_plan = {
        "schemaVersion": 1,
        "kind": "dense-evidence-fit-plan",
        "acceptedSpecSha256": delta["acceptedSpecSha256"],
        "proposedSpecSha256": delta["proposedSpecSha256"],
        "evidenceSha256": delta["evidenceSha256"],
        "correctionGroup": "silhouette",
        "parameterVector": list(scale),
        "requiredBrowserViews": ["source", "front", "right", "rear", "left"],
        "minimumEvidenceImprovement": 0.01,
        "maximumSourceSilhouetteRegression": 0.02,
        "correctionLoopBudget": {"maxPerPass": 3, "maxTotal": 6},
    }
    return proposal, delta, fit_plan


def _set_path(root: object, path: list[object], value: object) -> None:
    target: Any = root
    for token in path[:-1]:
        target = target[token]
    target[path[-1]] = value


def apply_reverse_delta(
    proposed_spec: dict[str, object], delta: dict[str, object]
) -> dict[str, object]:
    restored = copy.deepcopy(proposed_spec)
    changes = delta.get("changes", [])
    if not isinstance(changes, list):
        raise ValueError("delta changes must be a list")
    for change in reversed(changes):
        if not isinstance(change, dict) or not isinstance(change.get("path"), list):
            raise ValueError("delta change path is invalid")
        _set_path(restored, change["path"], change.get("old"))
    return restored


def _write_atomic(path: Path, value: object) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--component-map", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--delta-out", type=Path, required=True)
    parser.add_argument("--fit-plan-out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        admission = json.loads(args.admission.read_text(encoding="utf-8"))
        component_map = json.loads(args.component_map.read_text(encoding="utf-8")) if args.component_map else None
        # Admissions created by the CLI bind exact file bytes. Rebind only after those exact
        # hashes have been checked here, then use the same pure proposal implementation.
        binding = admission.get("binding", {})
        exact = {
            "targetSpecSha256": hashlib.sha256(args.spec.read_bytes()).hexdigest(),
            "evidenceSha256": hashlib.sha256(args.evidence.read_bytes()).hexdigest(),
        }
        if args.component_map:
            exact["componentMapSha256"] = hashlib.sha256(args.component_map.read_bytes()).hexdigest()
        if not isinstance(binding, dict) or any(binding.get(key) != value for key, value in exact.items()):
            raise ValueError("admission_hash_mismatch: exact input bytes changed")
        normalized = copy.deepcopy(admission)
        normalized["binding"]["targetSpecSha256"] = _canonical_sha256(spec)
        normalized["binding"]["evidenceSha256"] = _canonical_sha256(evidence)
        if component_map is not None:
            normalized["binding"]["componentMapSha256"] = _canonical_sha256(component_map)
        proposal, delta, fit_plan = build_proposal(spec, evidence, normalized, component_map)
        _write_atomic(args.out, proposal)
        _write_atomic(args.delta_out, delta)
        _write_atomic(args.fit_plan_out, fit_plan)
        validator = ROOT / "forge" / "stage2_spec" / "validate_sculpt_spec.py"
        normal = subprocess.run([sys.executable, str(validator), str(args.out)], capture_output=True, text=True, check=False)
        strict = subprocess.run([sys.executable, str(validator), str(args.out), "--strict-quality"], capture_output=True, text=True, check=False)
        if normal.returncode or strict.returncode:
            print(json.dumps({"status": "strict_quality_failed", "normal": normal.stdout + normal.stderr, "strict": strict.stdout + strict.stderr}, ensure_ascii=False))
            return 1
        print(json.dumps({"status": "complete", "proposal": str(args.out), "changes": len(delta["changes"])}, ensure_ascii=False))
        return 0
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
