#!/usr/bin/env python3
"""Pure-stdlib validator for bounded dense-evidence records."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any


SCOPES = ("none", "global-massing", "component-measurements")
SCOPE_RANK = {value: index for index, value in enumerate(SCOPES)}
HASH_FIELDS = ("glbSha256", "objSha256", "alignmentSha256")
COMPONENT_FIELDS = {
    "dimensions.width",
    "dimensions.height",
    "dimensions.depth",
    "dimensions.radius",
    "dimensions.length",
}
TOP_LEVEL_FIELDS = {
    "schemaVersion",
    "kind",
    "extractorVersion",
    "createdAt",
    "provenance",
    "cache",
    "admission",
    "alignment",
    "globalGeometry",
    "regions",
    "uncertainty",
    "extensions",
}
ALIGNMENT_FIELDS = {
    "schemaVersion",
    "profileVersion",
    "sourceViewTransform",
    "upAxis",
    "forwardAxis",
    "handedness",
    "axisOperationAudit",
    "chiralityStatus",
    "sourceViewSilhouetteIou",
    "projectedAspectRatioError",
    "browserCaptures",
    "extensions",
}


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()


def _is_hash(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _finite(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _component_ids(spec: dict[str, object]) -> set[str]:
    found: set[str] = set()

    def visit(items: object) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("id"), str):
                found.add(item["id"])
            visit(item.get("children"))

    visit(spec.get("componentTree"))
    return found


def _failure(
    errors: list[str], categories: set[str], category: str, message: str
) -> None:
    errors.append(message)
    categories.add(category)


def _validate_global_geometry(
    geometry: object, errors: list[str], categories: set[str]
) -> None:
    if not isinstance(geometry, dict):
        _failure(errors, categories, "schema_invalid", "globalGeometry must be an object")
        return
    bounds = geometry.get("bounds")
    if not isinstance(bounds, dict):
        _failure(errors, categories, "schema_invalid", "globalGeometry.bounds is required")
    else:
        for field in ("min", "max", "size"):
            values = bounds.get(field)
            if not isinstance(values, list) or len(values) != 3 or not all(
                _finite(value) for value in values
            ):
                _failure(errors, categories, "schema_invalid", f"bounds.{field} must be finite xyz")
        size = bounds.get("size")
        if isinstance(size, list) and len(size) == 3 and all(_finite(item) for item in size):
            if any(float(item) <= 0 for item in size):
                _failure(errors, categories, "degenerate_geometry", "bounds size must be positive")
    occupancy = geometry.get("occupancyGrid")
    if not isinstance(occupancy, dict):
        _failure(errors, categories, "schema_invalid", "occupancyGrid is required")
    else:
        resolution = occupancy.get("resolution")
        if not isinstance(resolution, int) or isinstance(resolution, bool) or not 1 <= resolution <= 32:
            _failure(
                errors,
                categories,
                "measurement_limit_exceeded",
                "occupancy resolution must be in [1, 32]",
            )
        cells = occupancy.get("occupiedCells")
        if not isinstance(cells, list):
            _failure(errors, categories, "schema_invalid", "occupiedCells must be a list")
        elif isinstance(resolution, int):
            for cell in cells:
                if not isinstance(cell, list) or len(cell) != 3 or not all(
                    isinstance(value, int) and 0 <= value < resolution for value in cell
                ):
                    _failure(errors, categories, "schema_invalid", "occupied cell is out of range")
                    break
            if occupancy.get("occupiedCellCount") != len(cells):
                _failure(errors, categories, "schema_invalid", "occupiedCellCount does not match")
    sections = geometry.get("crossSections")
    if not isinstance(sections, list):
        _failure(errors, categories, "schema_invalid", "crossSections must be a list")
    elif len(sections) > 32:
        _failure(
            errors, categories, "measurement_limit_exceeded", "more than 32 cross-sections"
        )
    else:
        for section in sections:
            profile = section.get("profile") if isinstance(section, dict) else None
            if not isinstance(profile, list) or len(profile) > 64:
                _failure(
                    errors,
                    categories,
                    "measurement_limit_exceeded",
                    "cross-section profile exceeds 64 points",
                )
                continue
            if not all(
                isinstance(point, list)
                and len(point) == 2
                and all(_finite(value) for value in point)
                for point in profile
            ):
                _failure(errors, categories, "schema_invalid", "profile points must be finite xy")


def _validate_alignment(
    alignment: object,
    expected_hash: object,
    errors: list[str],
    categories: set[str],
) -> None:
    if not isinstance(alignment, dict):
        _failure(errors, categories, "schema_invalid", "alignment must be an object")
        return
    unknown = set(alignment) - ALIGNMENT_FIELDS
    required = ALIGNMENT_FIELDS - {"extensions"}
    missing = required - set(alignment)
    if unknown or missing:
        _failure(
            errors,
            categories,
            "schema_invalid",
            f"alignment fields mismatch: missing={sorted(missing)} unknown={sorted(unknown)}",
        )
    if expected_hash != _canonical_sha256(alignment):
        _failure(errors, categories, "evidence_hash_mismatch", "alignment content hash drift")
    if alignment.get("schemaVersion") != 1 or alignment.get("profileVersion") != "source-view-alignment-v1":
        _failure(errors, categories, "schema_invalid", "alignment version is invalid")
    transform = alignment.get("sourceViewTransform")
    if not isinstance(transform, list) or len(transform) != 16 or not all(
        _finite(value) for value in transform
    ):
        _failure(errors, categories, "schema_invalid", "sourceViewTransform must be 16 finite numbers")
    if (
        alignment.get("upAxis") != "+Y"
        or alignment.get("forwardAxis") != "+Z"
        or alignment.get("handedness") != "right-handed"
    ):
        _failure(errors, categories, "schema_invalid", "alignment canonical axes are invalid")
    if alignment.get("chiralityStatus") not in {"reviewed", "ambiguous"}:
        _failure(errors, categories, "schema_invalid", "alignment chiralityStatus is invalid")
    audit = alignment.get("axisOperationAudit")
    if not isinstance(audit, list) or not audit or not all(
        isinstance(item, str) and item.strip() for item in audit
    ):
        _failure(errors, categories, "schema_invalid", "alignment axis audit is required")
    iou = alignment.get("sourceViewSilhouetteIou")
    aspect = alignment.get("projectedAspectRatioError")
    if not _finite(iou) or not 0 <= float(iou) <= 1 or not _finite(aspect) or not 0 <= float(aspect) <= 1:
        _failure(errors, categories, "schema_invalid", "alignment metrics must be in [0, 1]")
    elif float(iou) < 0.65 or float(aspect) > 0.15:
        _failure(errors, categories, "alignment_failed", "alignment quality thresholds were not met")
    captures = alignment.get("browserCaptures")
    if not isinstance(captures, list) or not captures:
        _failure(errors, categories, "schema_invalid", "alignment browser captures are required")
    else:
        for capture in captures:
            if (
                not isinstance(capture, dict)
                or set(capture) != {"path", "sha256", "view"}
                or not isinstance(capture.get("path"), str)
                or not isinstance(capture.get("view"), str)
                or not _is_hash(capture.get("sha256"))
            ):
                _failure(errors, categories, "schema_invalid", "alignment browser capture is invalid")
                break


def _validate_component_map(
    evidence: dict[str, object],
    spec: dict[str, object] | None,
    component_map: dict[str, object],
    errors: list[str],
    categories: set[str],
) -> None:
    admission = evidence.get("admission", {})
    if not isinstance(admission, dict) or admission.get("maximumInfluenceScope") != "component-measurements":
        _failure(
            errors,
            categories,
            "semantic_boundary_insufficient",
            "component mapping exceeds the evidence scope ceiling",
        )
        return
    if spec is None:
        _failure(errors, categories, "component_mapping_invalid", "target spec is required")
        return
    if component_map.get("schemaVersion") != 1 or component_map.get("kind") != "component-evidence-map":
        _failure(errors, categories, "component_mapping_invalid", "component map contract is invalid")
    if component_map.get("targetSpecSha256") != _canonical_sha256(spec):
        _failure(errors, categories, "evidence_hash_mismatch", "component map target spec hash drift")
    if component_map.get("evidenceSha256") != _canonical_sha256(evidence):
        _failure(errors, categories, "evidence_hash_mismatch", "component map evidence hash drift")
    provenance = evidence.get("provenance", {})
    if not isinstance(provenance, dict) or component_map.get("glbSha256") != provenance.get("glbSha256"):
        _failure(errors, categories, "evidence_hash_mismatch", "component map GLB hash drift")
    component_ids = _component_ids(spec)
    region_ids = {
        item.get("regionId")
        for item in evidence.get("regions", [])
        if isinstance(item, dict) and isinstance(item.get("regionId"), str)
    }
    used_selectors: set[str] = set()
    mappings = component_map.get("mappings")
    if not isinstance(mappings, list):
        _failure(errors, categories, "component_mapping_invalid", "mappings must be a list")
        return
    for mapping in mappings:
        if not isinstance(mapping, dict):
            _failure(errors, categories, "component_mapping_invalid", "mapping must be an object")
            continue
        if mapping.get("componentId") not in component_ids:
            _failure(errors, categories, "component_mapping_invalid", "mapping names unknown component")
        confidence = mapping.get("confidence")
        if not _finite(confidence) or float(confidence) < 0.80 or float(confidence) > 1.0:
            _failure(errors, categories, "component_mapping_invalid", "mapping confidence must be >= 0.80")
        if mapping.get("observedSurface") is not True:
            _failure(errors, categories, "component_mapping_invalid", "hidden surfaces cannot be measured")
        if not isinstance(mapping.get("evidenceRefs"), list) or not mapping["evidenceRefs"]:
            _failure(errors, categories, "component_mapping_invalid", "mapping evidenceRefs are required")
        fields = mapping.get("permittedFields")
        if not isinstance(fields, list) or not fields or any(field not in COMPONENT_FIELDS for field in fields):
            _failure(errors, categories, "component_mapping_invalid", "mapping field is forbidden")
        selectors = mapping.get("selectors")
        if not isinstance(selectors, list) or not selectors:
            _failure(errors, categories, "component_mapping_invalid", "selectors are required")
            continue
        for selector in selectors:
            region_id = selector.get("regionId") if isinstance(selector, dict) else None
            if region_id not in region_ids or region_id in used_selectors:
                _failure(errors, categories, "component_mapping_invalid", "selector is unknown or duplicated")
            if isinstance(region_id, str):
                used_selectors.add(region_id)


def validate_dense_evidence(
    evidence: object,
    expected_spec: dict[str, object] | None = None,
    component_map: dict[str, object] | None = None,
) -> dict[str, object]:
    errors: list[str] = []
    categories: set[str] = set()
    maximum_scope = "none"
    if not isinstance(evidence, dict):
        _failure(errors, categories, "schema_invalid", "evidence must be a JSON object")
    else:
        unknown = set(evidence) - TOP_LEVEL_FIELDS
        missing = TOP_LEVEL_FIELDS - set(evidence)
        if unknown or missing:
            _failure(
                errors,
                categories,
                "schema_invalid",
                f"top-level fields mismatch: missing={sorted(missing)} unknown={sorted(unknown)}",
            )
        if evidence.get("schemaVersion") != 1 or evidence.get("kind") != "dense-evidence":
            _failure(errors, categories, "schema_invalid", "unsupported dense-evidence contract")
        provenance = evidence.get("provenance")
        if not isinstance(provenance, dict):
            _failure(errors, categories, "schema_invalid", "provenance is required")
        else:
            for field in HASH_FIELDS:
                if not _is_hash(provenance.get(field)):
                    _failure(errors, categories, "evidence_hash_mismatch", f"invalid {field}")
            sources = provenance.get("sourceImageSha256")
            if not isinstance(sources, list) or not sources or not all(_is_hash(item) for item in sources):
                _failure(errors, categories, "evidence_hash_mismatch", "invalid source image hashes")
        expected_alignment_hash = (
            provenance.get("alignmentSha256") if isinstance(provenance, dict) else None
        )
        _validate_alignment(
            evidence.get("alignment"), expected_alignment_hash, errors, categories
        )
        cache = evidence.get("cache")
        if not isinstance(cache, dict) or not all(
            _is_hash(cache.get(field))
            for field in ("baseExtractionKey", "measurementConfigSha256")
        ):
            _failure(errors, categories, "evidence_hash_mismatch", "invalid cache hashes")
        admission = evidence.get("admission")
        if not isinstance(admission, dict):
            _failure(errors, categories, "schema_invalid", "admission is required")
        else:
            maximum_scope = str(admission.get("maximumInfluenceScope", "none"))
            approved_scope = admission.get("approvedInfluenceScope")
            if maximum_scope not in SCOPES or approved_scope != "none":
                _failure(errors, categories, "schema_invalid", "evidence scope contract is invalid")
            if admission.get("semanticStatus") == "insufficient" and maximum_scope == "component-measurements":
                _failure(
                    errors,
                    categories,
                    "semantic_boundary_insufficient",
                    "insufficient semantics cannot authorize component evidence",
                )
        uncertainty = evidence.get("uncertainty")
        if not isinstance(uncertainty, dict) or uncertainty.get("hiddenSurfacePolicy") != "non-authoritative":
            _failure(errors, categories, "schema_invalid", "hidden surfaces must be non-authoritative")
        _validate_global_geometry(evidence.get("globalGeometry"), errors, categories)
        regions = evidence.get("regions")
        if not isinstance(regions, list) or any(
            not isinstance(item, dict)
            or item.get("candidateOnly") is not True
            or item.get("semanticLabel") is not None
            for item in regions if isinstance(regions, list)
        ):
            _failure(errors, categories, "schema_invalid", "regions must remain candidate-only")
        if component_map is not None:
            _validate_component_map(
                evidence, expected_spec, component_map, errors, categories
            )
    return {
        "schemaVersion": 1,
        "passed": not errors,
        "failureCategories": sorted(categories),
        "errors": errors,
        "maximumInfluenceScope": maximum_scope,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--component-map", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        spec = json.loads(args.spec.read_text(encoding="utf-8")) if args.spec else None
        mapping = (
            json.loads(args.component_map.read_text(encoding="utf-8"))
            if args.component_map
            else None
        )
        report = validate_dense_evidence(evidence, spec, mapping)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["passed"] else 1
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        print(json.dumps({"passed": False, "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
