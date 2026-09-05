#!/usr/bin/env python3
"""Apply region-level material analysis to an ObjectSculptSpec.

This is the Phase 7 hand-off: analyzer output is copied with provenance into
the spec, profile priors become explicit candidate scalars, and components are
bound to the resolved material region. Existing specs remain valid when this
field is absent.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


SCALAR_FIELDS = (
    "metalness", "roughness", "clearcoat", "clearcoatRoughness", "ior",
    "anisotropy", "sheen", "sheenRoughness", "transmission", "dispersion",
)


def _layer(existing: Any, value: Any) -> dict[str, Any]:
    result = dict(existing) if isinstance(existing, dict) else {}
    result["base"] = value
    result.setdefault("variation", 0.0)
    return result


def apply_material_analysis(
    spec: dict[str, Any],
    analysis: dict[str, Any],
    *,
    allow_probe: bool = False,
) -> dict[str, Any]:
    if not isinstance(spec, dict) or not isinstance(analysis, dict):
        raise ValueError("spec and analysis must be JSON objects")
    regions = analysis.get("regions")
    if not isinstance(regions, list) or not regions:
        raise ValueError("analysis.regions must be a non-empty array")
    statuses = {str(item.get("assignment", {}).get("status")) for item in regions if isinstance(item, dict)}
    analysis_status = str(analysis.get("status", "probe"))
    unresolved_not_observed = analysis.get("unresolvedNotObservedMaterials", [])
    if not isinstance(unresolved_not_observed, list):
        raise ValueError("analysis.unresolvedNotObservedMaterials must be an array")
    if analysis_status != "proceed" and not allow_probe:
        raise ValueError("material analysis is not proceed; review unresolved material regions before wiring")
    if not allow_probe and statuses - {"proceed"}:
        raise ValueError("material analysis contains probe/unknown regions; pass --allow-probe after review")
    materials = spec.setdefault("materials", [])
    if not isinstance(materials, list):
        raise ValueError("spec.materials must be an array")
    components = spec.setdefault("componentTree", [])
    if not isinstance(components, list):
        raise ValueError("spec.componentTree must be an array")
    by_material = {str(item.get("id")): item for item in materials if isinstance(item, dict) and item.get("id")}
    by_component = {str(item.get("id")): item for item in components if isinstance(item, dict) and item.get("id")}
    applied: list[dict[str, Any]] = []
    for region in regions:
        if not isinstance(region, dict):
            raise ValueError("analysis region must be an object")
        assignment = region.get("assignment")
        if not isinstance(assignment, dict) or not assignment.get("profileId"):
            raise ValueError(f"region {region.get('regionId')!r} has no resolved profile")
        spec_material_id = str(region.get("materialSpecId") or region.get("regionId"))
        material = by_material.get(spec_material_id)
        if material is None:
            material = {"id": spec_material_id, "baseColor": "#FFFFFF"}
            materials.append(material)
            by_material[spec_material_id] = material
        profile_id = str(assignment["profileId"])
        material["referenceMaterialId"] = profile_id
        material["materialFamily"] = assignment.get("family")
        material["materialSubtype"] = assignment.get("subtype")
        material["materialFinish"] = assignment.get("finish")
        material["materialReference"] = {
            "registry": analysis.get("registry"),
            "profileId": profile_id,
            "method": assignment.get("method"),
            "confidence": assignment.get("confidence"),
            "sourceRefs": copy.deepcopy(assignment.get("sourceRefs", [])),
            "requiredMaps": copy.deepcopy(assignment.get("requiredMaps", [])),
            "optionalMaps": copy.deepcopy(assignment.get("optionalMaps", [])),
            "validationViews": copy.deepcopy(assignment.get("validationViews", [])),
        }
        for field, value in assignment.get("renderPrior", {}).items():
            if field in SCALAR_FIELDS and isinstance(value, (int, float)):
                material[field] = _layer(material.get(field), value)
        material.setdefault("textureResolution", 1024)
        material.setdefault("textureProjection", {
            "mode": "uv",
            "repeat": [1, 1],
            "anisotropy": 8,
            "colorSpace": "SRGBColorSpace for albedo; NoColorSpace for scalar/normal maps",
            "mapBindings": copy.deepcopy(assignment.get("requiredMaps", [])),
        })
        pbr = region.get("referencePbr")
        if isinstance(pbr, dict):
            material["referencePbr"] = copy.deepcopy(pbr)
        texture_analysis = region.get("textureAnalysis")
        if isinstance(texture_analysis, dict):
            material["textureAnalysis"] = copy.deepcopy(texture_analysis)
        material["materialEvidence"] = {
            "componentId": region.get("componentId"),
            "regionId": region.get("regionId"),
            "crop": copy.deepcopy(region.get("crop")),
            "observations": copy.deepcopy(region.get("observations", [])),
            "hypothesis": copy.deepcopy(region.get("hypothesis", {})),
            "alternatives": copy.deepcopy(assignment.get("alternatives", [])),
        }
        if assignment.get("family") in {"metal", "glass", "gemstone"}:
            material["needsEnvironment"] = True
        component = by_component.get(str(region.get("componentId")))
        if component is not None:
            component["material"] = spec_material_id
            component.setdefault("uvContract", {
                "status": "unwrapped",
                "strategy": "generated procedural coordinates",
                "materialId": spec_material_id,
            })
            component.setdefault("materialRegions", []).append({
                "regionId": region.get("regionId"),
                "materialId": spec_material_id,
                "profileId": profile_id,
                "crop": copy.deepcopy(region.get("crop")),
            })
        applied.append({
            "componentId": region.get("componentId"),
            "regionId": region.get("regionId"),
            "specMaterialId": spec_material_id,
            "profileId": profile_id,
            "status": assignment.get("status"),
        })
    spec["materialPipeline"] = {
        "schemaVersion": 1,
        "status": (
            "proceed"
            if analysis_status == "proceed"
            and not statuses - {"proceed"}
            and not unresolved_not_observed
            else "probe"
        ),
        "registry": analysis.get("registry"),
        "analysisArtifact": analysis.get("artifact", analysis.get("manifest")),
        "targetThreshold": analysis.get("targetThreshold", 0.7),
        "unresolvedNotObservedMaterials": copy.deepcopy(unresolved_not_observed),
        "regions": applied,
        "controlledViewsRequired": sorted({
            view
            for region in regions
            if isinstance(region.get("assignment"), dict)
            for view in region["assignment"].get("validationViews", [])
        }),
    }
    spec.setdefault("materialAnalysisHistory", []).append({
        "analysisArtifact": analysis.get("artifact", analysis.get("manifest")),
        "status": spec["materialPipeline"]["status"],
        "unresolvedNotObservedMaterials": copy.deepcopy(unresolved_not_observed),
        "regions": len(applied),
    })
    return spec


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--allow-probe", action="store_true")
    args = parser.parse_args(argv)
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
        result = apply_material_analysis(spec, analysis, allow_probe=args.allow_probe)
        output = args.spec if args.in_place else args.out
        if output is None:
            raise ValueError("choose --in-place or --out")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"status": result["materialPipeline"]["status"], "out": str(output.resolve())}, indent=2))
        return 0
    except Exception as exc:
        print(f"error: {exc}")
        return 1


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
