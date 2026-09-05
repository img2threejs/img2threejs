#!/usr/bin/env python3
"""Check material bindings against geometry, UV, rig, collision and LOD contracts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def check_compatibility(spec: dict[str, Any]) -> dict[str, Any]:
    materials = {str(item.get("id")): item for item in spec.get("materials", []) if isinstance(item, dict) and item.get("id")}
    components = [item for item in spec.get("componentTree", []) if isinstance(item, dict)]
    failures: list[str] = []
    warnings: list[str] = []
    component_ids = {str(item.get("id")) for item in components if item.get("id")}
    referenced: set[str] = set()
    for component in components:
        component_id = str(component.get("id") or component.get("name") or "(unnamed)")
        material_id = component.get("material")
        if material_id:
            referenced.add(str(material_id))
            if str(material_id) not in materials:
                failures.append(f"component {component_id!r} references missing material {material_id!r}")
        if component.get("materialRegions") and not material_id:
            failures.append(f"component {component_id!r} has materialRegions but no material binding")
    for material_id, material in materials.items():
        reference = material.get("materialReference")
        required_maps = reference.get("requiredMaps", []) if isinstance(reference, dict) else []
        pbr = material.get("referencePbr") if isinstance(material.get("referencePbr"), dict) else {}
        maps = pbr.get("maps", {}) if isinstance(pbr.get("maps"), dict) else {}
        projection = material.get("textureProjection")
        has_uv_contract = isinstance(projection, dict) or any(
            isinstance(component.get("uv"), dict) or isinstance(component.get("uvContract"), dict)
            for component in components if component.get("material") == material_id
        )
        if required_maps and not has_uv_contract:
            failures.append(f"material {material_id!r} requires maps but no UV/textureProjection contract exists")
        missing_maps = [channel for channel in required_maps if channel.endswith("Map") and channel not in maps and channel not in {"map", "normalMap"}]
        if missing_maps and pbr:
            warnings.append(f"material {material_id!r} profile requires map slots not present in extracted PBR maps: {missing_maps}")
    unbound = sorted(set(materials) - referenced)
    if unbound:
        warnings.append(f"materials not referenced by componentTree: {unbound}")
    rig = spec.get("rig")
    rigged_components = {
        str(item.get("component")) for item in (rig.get("bones", []) if isinstance(rig, dict) else [])
        if isinstance(item, dict) and item.get("component")
    }
    for component_id in sorted(rigged_components & component_ids):
        component = next(item for item in components if str(item.get("id")) == component_id)
        if not component.get("material"):
            failures.append(f"rigged component {component_id!r} lost its material binding")
    lod = spec.get("lodPlan")
    if lod is not None:
        if not isinstance(lod, list):
            failures.append("lodPlan must remain an array")
        else:
            for index, level in enumerate(lod):
                if isinstance(level, dict):
                    ids = level.get("materialIds", level.get("materials", []))
                    if isinstance(ids, list):
                        missing = sorted({str(value) for value in ids} - set(materials))
                        if missing:
                            failures.append(f"lodPlan[{index}] references missing material IDs: {missing}")
    collision = spec.get("collisionGroups", spec.get("colliders"))
    if collision is not None and not isinstance(collision, (list, dict)):
        failures.append("collisionGroups/colliders must remain an object or array")
    return {
        "schemaVersion": 1,
        "passed": not failures,
        "failures": failures,
        "warnings": warnings,
        "checked": {
            "materialCount": len(materials),
            "componentCount": len(components),
            "referencedMaterialCount": len(referenced),
            "riggedComponentCount": len(rigged_components),
            "lodPresent": lod is not None,
            "collisionPresent": collision is not None,
        },
        "invariant": "material IDs and UV/map contracts survive geometry, skinning, collision and LOD changes",
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        result = check_compatibility(json.loads(args.spec.read_text(encoding="utf-8")))
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 2
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
