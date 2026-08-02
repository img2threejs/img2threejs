#!/usr/bin/env python3
"""Validate the subject adapter synthesized for a novel image reconstruction.

This validator is intentionally domain-agnostic. It checks the reconstruction contract, not
whether the agent made the right visual judgment; visual correctness remains a review-gate concern.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


VALID_DOMAINS = {"object", "character", "hybrid"}
VALID_MODES = {"llm-synthesized", "domain-template"}
VALID_EVIDENCE_KINDS = {"observed", "researched", "inferred", "unknown"}
VALID_PROJECTION_BINDINGS = {"none", "uv-on-authored-mesh", "conforming-decal"}


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any, label: str, errors: list[str], *, allow_empty: bool = True) -> None:
    if not isinstance(value, list):
        errors.append(f"{label} must be an array of strings")
        return
    if not allow_empty and not value:
        errors.append(f"{label} must not be empty")
    for index, item in enumerate(value):
        if not _is_nonempty_string(item):
            errors.append(f"{label}[{index}] must be a non-empty string")


def _confidence(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
        errors.append(f"{label} must be a number from 0 to 1")


def _evidence_refs(value: Any, label: str, errors: list[str]) -> None:
    _string_list(value, label, errors, allow_empty=False)


def validate_adapter_contract(adapter: Any) -> list[str]:
    """Return actionable schema errors for an embedded subject adapter contract."""
    errors: list[str] = []
    if not isinstance(adapter, dict):
        return ["subjectAdapter/adapterContract must be an object"]

    for field in ("id", "mode", "domain", "subjectClass"):
        if not _is_nonempty_string(adapter.get(field)):
            errors.append(f"subjectAdapter.{field} is required")
    if adapter.get("mode") not in VALID_MODES:
        errors.append("subjectAdapter.mode must be llm-synthesized or domain-template")
    if adapter.get("domain") not in VALID_DOMAINS:
        errors.append("subjectAdapter.domain must be object, character, or hybrid")

    for field in ("evidenceRefs", "researchRefs", "reviewViewpoints"):
        _string_list(adapter.get(field), f"subjectAdapter.{field}", errors, allow_empty=field == "researchRefs")

    components = adapter.get("components")
    if not isinstance(components, list) or not components:
        errors.append("subjectAdapter.components must contain at least one component")
    else:
        component_ids: set[str] = set()
        for index, component in enumerate(components):
            label = f"subjectAdapter.components[{index}]"
            if not isinstance(component, dict):
                errors.append(f"{label} must be an object")
                continue
            component_id = component.get("id")
            if not _is_nonempty_string(component_id):
                errors.append(f"{label}.id is required")
            elif component_id in component_ids:
                errors.append(f"duplicate subjectAdapter component id {component_id!r}")
            else:
                component_ids.add(component_id)
            for field in ("name", "topologyClass", "geometryRecipe"):
                if not _is_nonempty_string(component.get(field)):
                    errors.append(f"{label}.{field} is required")
            _evidence_refs(component.get("evidenceRefs"), f"{label}.evidenceRefs", errors)
            _confidence(component.get("confidence"), f"{label}.confidence", errors)

    attachments = adapter.get("attachmentRules")
    if not isinstance(attachments, list):
        errors.append("subjectAdapter.attachmentRules must be an array")
    else:
        for index, rule in enumerate(attachments):
            label = f"subjectAdapter.attachmentRules[{index}]"
            if not isinstance(rule, dict):
                errors.append(f"{label} must be an object")
                continue
            for field in ("parent", "child", "parentSocket", "contactType"):
                if not _is_nonempty_string(rule.get(field)):
                    errors.append(f"{label}.{field} is required")
            if not isinstance(rule.get("gapTolerance"), (int, float)) or isinstance(rule.get("gapTolerance"), bool) or rule.get("gapTolerance") < 0:
                errors.append(f"{label}.gapTolerance must be a non-negative number")
            _evidence_refs(rule.get("evidenceRefs"), f"{label}.evidenceRefs", errors)

    features = adapter.get("criticalFeatures")
    if not isinstance(features, list) or not features:
        errors.append("subjectAdapter.criticalFeatures must contain at least one feature")
    else:
        for index, feature in enumerate(features):
            label = f"subjectAdapter.criticalFeatures[{index}]"
            if not isinstance(feature, dict):
                errors.append(f"{label} must be an object")
                continue
            if not _is_nonempty_string(feature.get("id")):
                errors.append(f"{label}.id is required")
            _string_list(feature.get("componentRefs"), f"{label}.componentRefs", errors, allow_empty=False)
            _evidence_refs(feature.get("evidenceRefs"), f"{label}.evidenceRefs", errors)
            if not _is_nonempty_string(feature.get("acceptance")):
                errors.append(f"{label}.acceptance is required")

    confidence = adapter.get("confidence")
    if not isinstance(confidence, dict) or not confidence:
        errors.append("subjectAdapter.confidence must be a non-empty object")
    else:
        for region, value in confidence.items():
            _confidence(value, f"subjectAdapter.confidence[{region!r}]", errors)

    policy = adapter.get("geometryPolicy")
    if not isinstance(policy, dict):
        errors.append("subjectAdapter.geometryPolicy is required")
    else:
        if policy.get("realMeshRequired") is not True:
            errors.append("subjectAdapter.geometryPolicy.realMeshRequired must be true")
        if policy.get("cameraOnlyGeometry") is not False:
            errors.append("subjectAdapter.geometryPolicy.cameraOnlyGeometry must be false")
        if policy.get("projectionBinding") not in VALID_PROJECTION_BINDINGS:
            errors.append(
                "subjectAdapter.geometryPolicy.projectionBinding must be none, "
                "uv-on-authored-mesh, or conforming-decal"
            )
        forbidden = policy.get("forbidden")
        _string_list(forbidden, "subjectAdapter.geometryPolicy.forbidden", errors, allow_empty=False)
        if isinstance(forbidden, list):
            forbidden_tokens = {str(item).lower() for item in forbidden}
            if {"depth-map-extrusion", "camera-only-shell"} - forbidden_tokens:
                errors.append(
                    "subjectAdapter.geometryPolicy.forbidden must include depth-map-extrusion and camera-only-shell"
                )

    return errors


def validate_spec(spec: dict[str, Any]) -> list[str]:
    if not isinstance(spec, dict):
        return ["spec must be an object"]
    adapter = spec.get("subjectAdapter", spec.get("adapterContract"))
    if adapter is None:
        return ["spec must contain subjectAdapter or adapterContract before code generation"]
    return validate_adapter_contract(adapter)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True, help="ObjectSculptSpec JSON containing subjectAdapter")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.spec.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"adaptive-adapter: cannot read spec: {exc}")
        return 2
    errors = validate_spec(payload)
    if errors:
        for error in errors:
            print(f"adaptive-adapter: {error}")
        return 1
    print("adaptive-adapter: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
