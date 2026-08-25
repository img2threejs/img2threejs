#!/usr/bin/env python3
"""Validate the shared GLB/procedural browser render profile.

The validator is intentionally dependency-free and stricter than the descriptive
JSON Schema: the same profile must be usable by both routes, and the six diagnostic
passes plus the one-feedback-group ordering are mandatory for v2.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PASS_IDS = (
    "beauty",
    "alpha-silhouette",
    "semantic-id",
    "depth",
    "normal",
    "roughness-material-id",
)
FEEDBACK_GROUPS = ("camera", "silhouette", "face", "clothing", "accessory", "materials", "lighting")
TONE_MAPPINGS = {"NoToneMapping", "ACESFilmicToneMapping", "AgXToneMapping", "NeutralToneMapping"}


def _is_vec(value: Any, length: int) -> bool:
    return isinstance(value, list) and len(value) == length and all(isinstance(item, (int, float)) for item in value)


def validate_profile(profile: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if profile.get("schemaVersion") != "render-profile.v2":
        errors.append("schemaVersion must be render-profile.v2")
    if profile.get("kind") != "threejs-render-profile":
        errors.append("kind must be threejs-render-profile")
    if profile.get("authority") != "browser-threejs":
        errors.append("authority must be browser-threejs")

    renderer = profile.get("renderer")
    if not isinstance(renderer, dict):
        errors.append("renderer must be an object")
    else:
        required = {
            "backend",
            "colorManagementEnabled",
            "workingColorSpace",
            "outputColorSpace",
            "toneMapping",
            "toneMappingExposure",
            "viewport",
            "devicePixelRatio",
            "antialias",
        }
        for key in sorted(required - renderer.keys()):
            errors.append(f"renderer.{key} is required")
        if renderer.get("backend") != "WebGLRenderer":
            errors.append("renderer.backend must be WebGLRenderer")
        if renderer.get("colorManagementEnabled") is not True:
            errors.append("renderer.colorManagementEnabled must be true")
        if renderer.get("workingColorSpace") != "Linear-sRGB":
            errors.append("renderer.workingColorSpace must be Linear-sRGB")
        if renderer.get("outputColorSpace") != "SRGBColorSpace":
            errors.append("renderer.outputColorSpace must be SRGBColorSpace")
        if renderer.get("toneMapping") not in TONE_MAPPINGS:
            errors.append("renderer.toneMapping is not a supported Three.js profile value")
        if not isinstance(renderer.get("toneMappingExposure"), (int, float)) or renderer.get("toneMappingExposure", 0) <= 0:
            errors.append("renderer.toneMappingExposure must be > 0")
        viewport = renderer.get("viewport")
        if not _is_vec(viewport, 2) or any(int(value) != value or value <= 0 for value in viewport):
            errors.append("renderer.viewport must be [positive integer width, positive integer height]")
        if not isinstance(renderer.get("devicePixelRatio"), (int, float)) or renderer.get("devicePixelRatio", 0) <= 0:
            errors.append("renderer.devicePixelRatio must be > 0")
        if not isinstance(renderer.get("antialias"), bool):
            errors.append("renderer.antialias must be boolean")

    environment = profile.get("environment")
    if not isinstance(environment, dict):
        errors.append("environment must be an object")
    else:
        if environment.get("kind") not in {"none", "pmrem-hdr", "pmrem-equirectangular", "pmrem-cubemap"}:
            errors.append("environment.kind must identify a PMREM or none")
        if not isinstance(environment.get("source"), str):
            errors.append("environment.source is required")
        if not isinstance(environment.get("intensity"), (int, float)) or environment.get("intensity", -1) < 0:
            errors.append("environment.intensity must be >= 0")
        if environment.get("kind", "").startswith("pmrem") and environment.get("prefilter") != "PMREMGenerator":
            warnings.append("PMREM environment should record prefilter=PMREMGenerator")

    camera = profile.get("camera")
    if not isinstance(camera, dict):
        errors.append("camera must be an object")
    else:
        if camera.get("projection") not in {"perspective", "orthographic"}:
            errors.append("camera.projection must be perspective or orthographic")
        for key in ("position", "target"):
            if not _is_vec(camera.get(key), 3):
                errors.append(f"camera.{key} must be a numeric vec3")
        if not isinstance(camera.get("near"), (int, float)) or camera.get("near", 0) <= 0:
            errors.append("camera.near must be > 0")
        if not isinstance(camera.get("far"), (int, float)) or camera.get("far", 0) <= camera.get("near", 0):
            errors.append("camera.far must be greater than camera.near")

    background = profile.get("background")
    if not isinstance(background, dict) or background.get("kind") not in {"solid", "transparent", "scene"}:
        errors.append("background.kind must be solid, transparent, or scene")

    passes = profile.get("passes")
    pass_ids = [item.get("id") for item in passes] if isinstance(passes, list) and all(isinstance(item, dict) for item in passes) else []
    if set(pass_ids) != set(PASS_IDS) or len(pass_ids) != len(PASS_IDS):
        errors.append(f"passes must contain exactly: {', '.join(PASS_IDS)}")
    else:
        for item in passes:
            if item.get("required") is not True:
                errors.append(f"pass {item.get('id')} must be required")
            if item.get("id") == "beauty" and item.get("colorSpace") != "SRGBColorSpace":
                errors.append("beauty pass must use SRGBColorSpace")
            if item.get("id") != "beauty" and item.get("colorSpace") != "NoColorSpace":
                errors.append(f"diagnostic pass {item.get('id')} must use NoColorSpace")

    regions = profile.get("regions")
    region_ids = [item.get("id") for item in regions] if isinstance(regions, list) and all(isinstance(item, dict) for item in regions) else []
    if not region_ids:
        errors.append("regions must contain at least one subject-specific semantic region")
    if any(not isinstance(region_id, str) or not region_id.strip() for region_id in region_ids):
        errors.append("every region.id must be a non-empty string")
    if len(set(region_ids)) != len(region_ids):
        errors.append("region IDs must be unique")

    extensions = profile.get("extensions")
    required_regions = extensions.get("requiredSemanticRegions") if isinstance(extensions, dict) else None
    if (
        not isinstance(required_regions, list)
        or not required_regions
        or any(not isinstance(region_id, str) or not region_id.strip() for region_id in required_regions)
    ):
        errors.append("extensions.requiredSemanticRegions must be a non-empty array of non-empty strings")
        required_regions = []
    elif len(set(required_regions)) != len(required_regions):
        errors.append("extensions.requiredSemanticRegions must contain unique IDs")
    missing_regions = [region for region in required_regions if region not in region_ids]
    if missing_regions:
        errors.append(f"regions missing declared required IDs: {', '.join(missing_regions)}")

    feedback = profile.get("feedbackGroups")
    feedback_ids = [item.get("id") for item in feedback] if isinstance(feedback, list) and all(isinstance(item, dict) for item in feedback) else []
    if feedback_ids != list(FEEDBACK_GROUPS):
        errors.append("feedbackGroups must be ordered camera, silhouette, face, clothing, accessory, materials, lighting")
    else:
        for index, item in enumerate(feedback, start=1):
            if item.get("order") != index:
                errors.append(f"feedback group {item.get('id')} must have order {index}")

    return {"passed": not errors, "errors": errors, "warnings": warnings, "passIds": pass_ids, "regionIds": region_ids, "feedbackGroups": feedback_ids}


def validate_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"passed": False, "errors": [f"cannot read JSON: {exc}"], "warnings": []}
    if not isinstance(value, dict):
        return {"passed": False, "errors": ["profile root must be an object"], "warnings": []}
    result = validate_profile(value)
    result["path"] = str(path.resolve())
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    args = parser.parse_args(argv)
    result = validate_file(args.profile.expanduser().resolve())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
