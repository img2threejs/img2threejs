#!/usr/bin/env python3
"""Build deterministic material camera/crop/microscope review plans.

The forge cannot invent a browser screenshot, but it can make the required
camera contract explicit and validate every crop before a renderer or vision
provider consumes it.  A showcase/browser adapter uses the emitted plan to
capture the same views across iterations.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "forge" / "stage1_intake"))
from extract_pbr_evidence import build_foreground_mask, load_image, write_png_rgb  # noqa: E402


DEFAULT_AZIMUTHS = (0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0)
DEFAULT_VALIDATION_VIEWS = (
    "albedo-unlit", "neutral-studio", "grazing", "environment-reflection", "reference-beauty"
)


def _norm(value: float) -> float:
    return round(float(value) % 360.0, 3)


def build_view_plan(
    component_id: str,
    region_id: str,
    *,
    validation_views: list[str] | None = None,
    azimuths: tuple[float, ...] = DEFAULT_AZIMUTHS,
    elevation: float = 8.0,
    zoom: float = 2.5,
    target: tuple[float, float, float] = (0.0, 0.0, 0.0),
    required_transmission: bool = False,
) -> dict[str, Any]:
    views = list(validation_views or DEFAULT_VALIDATION_VIEWS)
    if required_transmission and "backlight-transmission" not in views:
        views.append("backlight-transmission")
    captures: list[dict[str, Any]] = []
    for view_name in views:
        for azimuth in azimuths:
            captures.append({
                "id": f"{region_id}-{view_name}-{int(_norm(azimuth)):03d}",
                "componentId": component_id,
                "regionId": region_id,
                "view": view_name,
                "camera": {
                    "azimuthDegrees": _norm(azimuth),
                    "elevationDegrees": elevation,
                    "rollDegrees": 0.0,
                    "target": list(target),
                    "zoom": zoom,
                },
                "cropPolicy": {
                    "mode": "visible-footprint",
                    "paddingFraction": 0.12,
                    "minCoverage": 0.03,
                    "maxCoverage": 0.92,
                    "microscope": view_name in {"grazing", "environment-reflection", "backlight-transmission"},
                },
            })
    return {
        "schemaVersion": 1,
        "kind": "img2threejs.material-view-plan",
        "componentId": component_id,
        "regionId": region_id,
        "environment": {
            "required": True,
            "prefilter": "PMREMGenerator",
            "workingColorSpace": "LinearSRGBColorSpace",
            "displayColorSpace": "SRGBColorSpace",
            "toneMapping": "ACESFilmicToneMapping",
            "exposure": 1.0,
            "stableHash": "img2threejs-material-neutral-studio-v1",
        },
        "views": captures,
        "captureRequirements": [
            "save PNG/JPEG to workspace",
            "read the saved image back before scoring",
            "record renderer version, environment hash and camera transform",
            "compare the full visible component footprint, not an isolated hidden surface",
        ],
    }


def crop_visible_footprint(source: Path, bbox: dict[str, Any], output: Path, padding_fraction: float = 0.12) -> dict[str, Any]:
    width, height, pixels, warnings = load_image(source)
    required = ("x", "y", "width", "height")
    if not isinstance(bbox, dict) or not all(isinstance(bbox.get(key), (int, float)) for key in required):
        raise ValueError("visible-footprint bbox requires x, y, width and height")
    x, y, box_width, box_height = (float(bbox[key]) for key in required)
    if bbox.get("normalized") is True:
        x, box_width = x * width, box_width * width
        y, box_height = y * height, box_height * height
    pad_x = box_width * max(0.0, padding_fraction)
    pad_y = box_height * max(0.0, padding_fraction)
    x0 = max(0, math.floor(x - pad_x))
    y0 = max(0, math.floor(y - pad_y))
    x1 = min(width, math.ceil(x + box_width + pad_x))
    y1 = min(height, math.ceil(y + box_height + pad_y))
    crop_width, crop_height = x1 - x0, y1 - y0
    if crop_width < 8 or crop_height < 8:
        raise ValueError("visible footprint crop is too small")
    rgb = bytearray()
    for row in range(y0, y1):
        for column in range(x0, x1):
            red, green, blue, alpha = pixels[row * width + column]
            rgb.extend((red, green, blue) if alpha >= 16 else (0, 0, 0))
    write_png_rgb(output, crop_width, crop_height, bytes(rgb))
    mask, diagnostics, mask_warnings = build_foreground_mask(crop_width, crop_height, [(*rgb[i:i + 3], 255) for i in range(0, len(rgb), 3)])
    coverage = sum(mask) / max(1, len(mask))
    if coverage < 0.03:
        raise ValueError("visible footprint crop contains too little foreground")
    return {
        "path": str(output.resolve()),
        "bbox": {"x": x0, "y": y0, "width": crop_width, "height": crop_height},
        "coverage": round(coverage, 4),
        "diagnostics": diagnostics,
        "warnings": warnings + mask_warnings,
        "readback": {"width": crop_width, "height": crop_height, "pixels": crop_width * crop_height},
    }


def validate_capture(path: Path, *, min_width: int = 64, min_height: int = 64) -> dict[str, Any]:
    width, height, pixels, warnings = load_image(path)
    mask, diagnostics, mask_warnings = build_foreground_mask(width, height, pixels)
    coverage = sum(mask) / max(1, len(mask))
    failures: list[str] = []
    if width < min_width or height < min_height:
        failures.append(f"capture is {width}x{height}; minimum is {min_width}x{min_height}")
    if coverage < 0.03:
        failures.append("capture foreground coverage is below 3%")
    return {
        "path": str(path.resolve()),
        "readable": True,
        "width": width,
        "height": height,
        "foregroundCoverage": round(coverage, 4),
        "diagnostics": diagnostics,
        "warnings": warnings + mask_warnings,
        "passed": not failures,
        "failures": failures,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component-id", required=True)
    parser.add_argument("--region-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--validation-view", action="append", dest="validation_views")
    parser.add_argument("--transmission", action="store_true")
    parser.add_argument("--capture", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    try:
        result = build_view_plan(
            args.component_id,
            args.region_id,
            validation_views=args.validation_views,
            required_transmission=args.transmission,
        )
        if args.capture:
            result["captureValidation"] = [validate_capture(path) for path in args.capture]
            result["capturePassed"] = all(item["passed"] for item in result["captureValidation"])
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"out": str(args.out.resolve()), "views": len(result["views"]), "capturePassed": result.get("capturePassed", True)}, indent=2))
        return 0 if result.get("capturePassed", True) else 2
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
