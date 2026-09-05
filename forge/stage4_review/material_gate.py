#!/usr/bin/env python3
"""Blocking acceptance gate for the wired material pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "forge"))
from materials.compatibility import check_compatibility  # noqa: E402


def run_material_gate(
    spec: dict[str, Any],
    *,
    analysis: dict[str, Any] | None = None,
    comparisons: list[dict[str, Any]] | None = None,
    view_plan: dict[str, Any] | None = None,
    require_visual_evidence: bool = True,
) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    pipeline = spec.get("materialPipeline")
    if not isinstance(pipeline, dict):
        failures.append("spec is not wired to materialPipeline")
    elif pipeline.get("status") != "proceed":
        failures.append(f"materialPipeline status is {pipeline.get('status')!r}, not proceed")
    if isinstance(pipeline, dict):
        for item in pipeline.get("unresolvedNotObservedMaterials", []):
            if isinstance(item, dict):
                failures.append(
                    f"material {item.get('materialId')!r} is not observed: "
                    f"{item.get('reason', 'additional reference required')}"
                )
    if analysis is not None and analysis.get("status") != "proceed":
        failures.append(f"material analysis status is {analysis.get('status')!r}, not proceed")
    regions = pipeline.get("regions", []) if isinstance(pipeline, dict) else []
    if not isinstance(regions, list) or not regions:
        failures.append("no material regions are available for gating")
    else:
        for region in regions:
            if not isinstance(region, dict):
                failures.append("materialPipeline contains a malformed region")
                continue
            if region.get("status") != "proceed":
                failures.append(f"region {region.get('regionId')!r} is not proceed")
    comparison_records = comparisons or []
    if require_visual_evidence and not comparison_records:
        failures.append("no material crop comparison evidence was supplied")
    for comparison in comparison_records:
        if not comparison.get("passed"):
            failures.append(
                f"material comparison failed for {comparison.get('materialId')!r}: "
                + ", ".join(comparison.get("mismatches", []))
            )
    if view_plan is not None:
        views = view_plan.get("views", [])
        if not isinstance(views, list) or len(views) < 4:
            failures.append("material view plan must contain at least four camera azimuths/views")
        captures = view_plan.get("captureValidation", [])
        if require_visual_evidence and captures and any(not item.get("passed") for item in captures if isinstance(item, dict)):
            failures.append("one or more saved material captures failed readback/coverage validation")
        environment = view_plan.get("environment")
        if not isinstance(environment, dict) or environment.get("required") is not True:
            failures.append("controlled material views must require a stable environment")
    compatibility = check_compatibility(spec)
    failures.extend(f"compatibility: {item}" for item in compatibility["failures"])
    warnings.extend(compatibility["warnings"])
    return {
        "schemaVersion": 1,
        "kind": "img2threejs.material-gate",
        "passed": not failures,
        "failures": failures,
        "warnings": warnings,
        "comparisonCount": len(comparison_records),
        "compatibility": compatibility,
        "requiredEvidence": [
            "materialPipeline.status=proceed",
            "all material regions status=proceed",
            "per-region crop comparison passed",
            "multi-angle controlled view plan and screenshot readback",
            "geometry/UV/skinning/collision/LOD material ID compatibility passed",
        ],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--analysis", type=Path)
    parser.add_argument("--comparison", type=Path, action="append", default=[])
    parser.add_argument("--view-plan", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--in-place", action="store_true", help="record materialGate on the spec")
    parser.add_argument("--allow-no-visual-evidence", action="store_true")
    args = parser.parse_args(argv)
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        analysis = json.loads(args.analysis.read_text(encoding="utf-8")) if args.analysis else None
        comparisons = [json.loads(path.read_text(encoding="utf-8")) for path in args.comparison]
        view_plan = json.loads(args.view_plan.read_text(encoding="utf-8")) if args.view_plan else None
        result = run_material_gate(
            spec,
            analysis=analysis,
            comparisons=comparisons,
            view_plan=view_plan,
            require_visual_evidence=not args.allow_no_visual_evidence,
        )
        if args.in_place:
            spec["materialGate"] = result
            args.spec.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"passed": result["passed"], "failures": result["failures"], "out": str(args.out.resolve())}, indent=2))
        return 0 if result["passed"] else 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
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
