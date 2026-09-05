#!/usr/bin/env python3
"""Analyze admitted material regions and connect them to the registry.

Input manifest example::

  {"referenceId":"armor-front", "regions":[
    {"componentId":"torso-armor", "regionId":"paint", "sourceImage":"front.png",
     "bbox":{"x":120,"y":80,"width":420,"height":300},
     "family":"coating", "subtype":"paint-over-metal", "finish":"gloss-or-satin",
     "materialSpecId":"armor-paint"}
  ]}

The command creates verified crops, runs the existing pixel/PBR evidence
extractor, resolves the canonical material profile, and emits one durable
``material-analysis.json`` artifact.  It never silently promotes a low
confidence inference to ``proceed``.
"""

from __future__ import annotations

import argparse
import json
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "forge" / "stage1_intake"))
from analyze_texture import analyze  # noqa: E402
from extract_pbr_evidence import extract, load_image, write_png_rgb  # noqa: E402

sys.path.insert(0, str(ROOT / "forge"))
from materials.reference import build_assignment, load_reference  # noqa: E402


def _bounded_bbox(raw: Any, width: int, height: int) -> tuple[int, int, int, int]:
    if not isinstance(raw, dict):
        raise ValueError("region bbox must be an object")
    values = [raw.get(key) for key in ("x", "y", "width", "height")]
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
        raise ValueError("region bbox requires numeric x, y, width and height")
    x, y, box_width, box_height = (float(value) for value in values)
    if raw.get("normalized") is True:
        x, box_width = x * width, box_width * width
        y, box_height = y * height, box_height * height
    x0 = max(0, min(width - 1, round(x)))
    y0 = max(0, min(height - 1, round(y)))
    x1 = max(x0 + 1, min(width, round(x + box_width)))
    y1 = max(y0 + 1, min(height, round(y + box_height)))
    if (x1 - x0) < 8 or (y1 - y0) < 8:
        raise ValueError("material crop is too small; minimum crop dimension is 8 pixels")
    return x0, y0, x1 - x0, y1 - y0


def crop_region(source: Path, bbox: dict[str, Any], output: Path) -> dict[str, Any]:
    width, height, pixels, warnings = load_image(source)
    x0, y0, crop_width, crop_height = _bounded_bbox(bbox, width, height)
    rgb = bytearray()
    for y in range(y0, y0 + crop_height):
        for x in range(x0, x0 + crop_width):
            red, green, blue, alpha = pixels[y * width + x]
            if alpha < 16:
                red = green = blue = 0
            rgb.extend((red, green, blue))
    write_png_rgb(output, crop_width, crop_height, bytes(rgb))
    return {
        "path": str(output.resolve()),
        "bbox": {"x": x0, "y": y0, "width": crop_width, "height": crop_height},
        "sourceWidth": width,
        "sourceHeight": height,
        "loaderWarnings": warnings,
        "coverage": round((crop_width * crop_height) / max(1, width * height), 4),
    }


def _observations(texture_report: dict[str, Any]) -> list[str]:
    stats = texture_report.get("stats", {})
    observations: list[str] = []
    if float(stats.get("meanSaturation", 0.0)) < 0.18:
        observations.append("near-neutral colour response")
    else:
        observations.append("chromatic base-colour response")
    if float(stats.get("streakRatio", 1.0)) > 1.9 or float(stats.get("streakRatio", 1.0)) < 0.53:
        observations.append("directional surface frequency")
    if float(stats.get("mottle", 0.0)) > 0.038:
        observations.append("visible meso/micro variation")
    if float(stats.get("gradientStrength", 0.0)) > 0.18:
        observations.append("strong image-space gradient; verify it is material pattern, not lighting")
    observations.append("single-image PBR inference requires controlled render validation")
    return observations


def analyze_manifest(
    manifest_path: Path,
    out_dir: Path,
    reference_path: Path | None = None,
    registry_path: Path | None = None,
    target_threshold: float = 0.7,
    multi_view_reference: bool = False,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("regions"), list):
        raise ValueError("material region manifest must contain a regions array")
    registry = load_reference(registry_path) if registry_path else load_reference()
    out_dir.mkdir(parents=True, exist_ok=True)
    regions: list[dict[str, Any]] = []
    for index, region in enumerate(manifest["regions"]):
        if not isinstance(region, dict):
            raise ValueError(f"regions[{index}] must be an object")
        component_id = str(region.get("componentId") or "").strip()
        region_id = str(region.get("regionId") or "").strip()
        if not component_id or not region_id:
            raise ValueError(f"regions[{index}] requires componentId and regionId")
        source = Path(region.get("sourceImage") or reference_path or "")
        if not source.is_absolute():
            source = (manifest_path.parent / source).resolve()
        if not source.exists():
            raise ValueError(f"region {region_id!r} source image does not exist: {source}")
        crop_path = out_dir / f"{index:02d}-{region_id.replace('/', '-').replace(' ', '-')}.png"
        crop_info = crop_region(source, region.get("bbox"), crop_path)
        texture_report = analyze(crop_path)
        pbr_dir = out_dir / f"pbr-{index:02d}-{region_id.replace('/', '-').replace(' ', '-')}"
        args = Namespace(
            image=crop_path,
            out_dir=pbr_dir,
            material_id=str(region.get("materialSpecId") or region_id),
            size=512,
            palette_size=5,
            target_threshold=target_threshold,
            url_prefix="",
            multi_view_reference=multi_view_reference,
        )
        pbr_report, pbr_patch = extract(args)
        hypothesis = {
            "componentId": component_id,
            "regionId": region_id,
            "materialId": region.get("materialId"),
            "family": region.get("family"),
            "subtype": region.get("subtype"),
            "finish": region.get("finish"),
            "aliases": region.get("aliases", []),
            "confidence": min(float(region.get("confidence", 1.0)), float(pbr_report["confidence"])),
            "source": region.get("source", "vision"),
        }
        assignment = build_assignment(hypothesis, {
            "crop": crop_info,
            "pbr": pbr_report,
            "texture": texture_report,
        }, registry)
        if pbr_report["confidence"] < target_threshold and assignment["status"] == "proceed":
            assignment["status"] = "probe"
        regions.append({
            "componentId": component_id,
            "regionId": region_id,
            "materialSpecId": region.get("materialSpecId") or region_id,
            "hypothesis": hypothesis,
            "assignment": assignment,
            "observations": _observations(texture_report),
            "crop": crop_info,
            "textureAnalysis": texture_report,
            "pbrReport": pbr_report,
            "referencePbr": pbr_patch["referencePbr"],
            "maps": pbr_report.get("maps", {}),
        })
    not_observed = manifest.get("notObservedMaterials", [])
    if not isinstance(not_observed, list):
        raise ValueError("notObservedMaterials must be an array when present")
    unresolved_not_observed = [
        item for item in not_observed
        if isinstance(item, dict) and item.get("status") in {"probe", "request-input", "unknown"}
    ]
    return {
        "schemaVersion": 1,
        "kind": "img2threejs.material-analysis",
        "referenceId": manifest.get("referenceId", manifest_path.stem),
        "manifest": str(manifest_path.resolve()),
        "registry": str((registry_path or ROOT / "docs/materials/material-reference.json").resolve()),
        "targetThreshold": target_threshold,
        "notObservedMaterials": not_observed,
        "unresolvedNotObservedMaterials": unresolved_not_observed,
        "regions": regions,
        "status": (
            "proceed"
            if regions
            and all(item["assignment"]["status"] == "proceed" for item in regions)
            and not unresolved_not_observed
            else "probe"
        ),
        "limitations": [
            "PBR extraction is reference-derived inference, not inverse rendering.",
            "A crop must be checked against the component visible footprint before accepting material scores.",
        ],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--target-threshold", type=float, default=0.7)
    parser.add_argument("--multi-view-reference", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = analyze_manifest(
            args.manifest,
            args.out_dir,
            args.reference,
            args.registry,
            max(0.0, min(1.0, args.target_threshold)),
            args.multi_view_reference,
        )
        result["artifact"] = str(args.out.resolve())
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"status": result["status"], "regions": len(result["regions"]), "out": str(args.out.resolve())}, indent=2))
        return 0 if result["status"] == "proceed" else 2
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
