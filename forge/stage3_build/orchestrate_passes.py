#!/usr/bin/env python3
"""Gate procedural sculpt generation through ordered build passes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
from feature_acceptance_policy import feature_gate_failures


DEFAULT_PASS_ORDER = [
    "blockout",
    "structural-pass",
    "form-refinement",
    "material-pass",
    "surface-pass",
    "lighting-pass",
    "interaction-pass",
    "optimization-pass",
]
VISUAL_PASS_IDS = set(DEFAULT_PASS_ORDER) - {"optimization-pass"}
ATTACHMENT_ROLES = {
    "appendage",
    "branch",
    "limb",
    "arm",
    "leg",
    "handle",
    "connector",
    "tube",
    "cable",
    "horn",
    "wing",
    "tail",
    "root",
    "fork",
    "rib",
    "support",
    "hinge",
    "socket",
    "pipe",
}
ATTACHMENT_PRIMITIVES = {"cylinder", "cone", "capsule", "tube", "curve-sweep"}


def load_spec(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("spec must be a JSON object")
    return payload


def write_spec(path: Path, spec: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def pass_order(spec: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in spec.get("buildPasses", []):
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
            ids.append(item["id"])
    return ids or DEFAULT_PASS_ORDER.copy()


def pass_acceptance(spec: dict[str, Any], pass_id: str) -> list[str]:
    for item in spec.get("buildPasses", []):
        if isinstance(item, dict) and item.get("id") == pass_id:
            acceptance = item.get("acceptance", [])
            if isinstance(acceptance, list):
                return [str(value) for value in acceptance if str(value).strip()]
    return []


def visual_evidence(entry: dict[str, Any]) -> dict[str, Any]:
    visual = entry.get("visualEvidence")
    return visual if isinstance(visual, dict) else {}


def review_completes_pass(spec: dict[str, Any], entry: dict[str, Any], pass_id: str) -> bool:
    if entry.get("passId") != pass_id or entry.get("action") != "continue":
        return False
    if pass_id in VISUAL_PASS_IDS:
        visual = visual_evidence(entry)
        if not visual.get("renderScreenshot") or not visual.get("comparisonImage"):
            return False
        score = entry.get("aiVisionScore")
        threshold = entry.get("visualAcceptanceThreshold", 0.7)
        if not has_number(score) or not has_number(threshold) or float(score) < float(threshold):
            return False
        if feature_gate_failures(spec, entry, pass_id):
            return False
    return True


def completed_passes(spec: dict[str, Any], ids: list[str]) -> list[str]:
    history = spec.get("reviewHistory", [])
    if not isinstance(history, list):
        return []
    completed: list[str] = []
    for pass_id in ids:
        if any(
            isinstance(entry, dict) and review_completes_pass(spec, entry, pass_id)
            for entry in history
        ):
            completed.append(pass_id)
        else:
            break
    return completed


def current_pass(ids: list[str], completed: list[str]) -> str:
    if len(completed) >= len(ids):
        return "complete"
    return ids[len(completed)]


def next_required_evidence(spec: dict[str, Any], pass_id: str) -> list[str]:
    if pass_id == "complete":
        return []
    evidence = pass_acceptance(spec, pass_id)
    evidence.extend(pass_specific_evidence(pass_id))
    if pass_id in VISUAL_PASS_IDS:
        evidence.append("browser render screenshot from your agent's browser/screenshot tool")
        evidence.append("side-by-side reference/render comparison sheet for AI vision review")
        evidence.append("AI vision score at or above the visual acceptance threshold")
        evidence.append("all critical semantic feature scores from the shared image pair at or above their thresholds")
        evidence.append("self-correction review appended with action=continue before the next pass")
    return evidence


def has_non_empty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() not in {"none", "unassessed", "n/a"}
    if isinstance(value, list):
        return any(has_non_empty(item) for item in value)
    if isinstance(value, dict):
        return any(has_non_empty(item) for item in value.values())
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return abs(float(value)) > 0
    return False


def number_from_layer(value: Any, keys: tuple[str, ...]) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                return float(item)
    return 0.0


def is_vector3(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 3
        and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
    )


def has_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def component_requires_attachment(component: dict[str, Any]) -> bool:
    if not component.get("parent"):
        return False
    role = str(component.get("role") or "").lower()
    name = str(component.get("name") or component.get("id") or "").lower()
    primitive = str(component.get("primitive") or "").lower()
    action = component.get("actionProfile") if isinstance(component.get("actionProfile"), dict) else {}
    animation_role = str(action.get("animationRole") or "").lower()
    tokens = {role, animation_role} | set(re.findall(r"[a-z0-9]+", name))
    return bool(tokens & ATTACHMENT_ROLES) or primitive in ATTACHMENT_PRIMITIVES


def attachment_complete(component: dict[str, Any]) -> bool:
    attachment = component.get("attachment")
    if not isinstance(attachment, dict):
        return False
    has_endpoint = is_vector3(attachment.get("localStart")) and is_vector3(attachment.get("localEnd"))
    has_socket = has_non_empty(attachment.get("parentSocket")) or has_non_empty(attachment.get("parentId"))
    has_contact = has_non_empty(attachment.get("contactType"))
    has_overlap = (
        number_from_layer(attachment.get("embedDepth"), ("base", "amount", "value")) > 0
        or number_from_layer(attachment.get("overlap"), ("base", "amount", "value")) > 0
    )
    has_tolerance = has_number(attachment.get("gapTolerance"))
    return has_endpoint and has_socket and has_contact and has_overlap and has_tolerance


def attachment_gaps(spec: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    for component in spec.get("componentTree", []):
        if not isinstance(component, dict) or not component_requires_attachment(component):
            continue
        if attachment_complete(component):
            continue
        component_id = str(component.get("id") or component.get("name") or "(unnamed)")
        gaps.append(
            f"component {component_id!r} requires attachment.parentSocket/localStart/localEnd/"
            "contactType/embedDepth(or overlap)/gapTolerance"
        )
    return gaps


def material_has_palette(material: dict[str, Any]) -> bool:
    color_variation = material.get("colorVariation")
    if isinstance(color_variation, dict) and len(color_variation.get("palette", [])) >= 2:
        return True
    albedo = material.get("albedo")
    if isinstance(albedo, dict) and has_non_empty(albedo.get("secondary")):
        return True
    return has_non_empty(material.get("baseColor") or material.get("color"))


def material_has_response(material: dict[str, Any]) -> bool:
    if number_from_layer(material.get("roughness"), ("variation", "base")) > 0:
        return True
    if number_from_layer(material.get("normal"), ("strength", "amplitude")) > 0:
        return True
    if number_from_layer(material.get("bump"), ("amplitude", "strength")) > 0:
        return True
    if number_from_layer(material.get("displacement"), ("amplitude", "strength")) > 0:
        return True
    return False


def material_has_locality(material: dict[str, Any]) -> bool:
    if has_non_empty(material.get("localOverrides")):
        return True
    wear = material.get("wear")
    if isinstance(wear, dict) and (
        number_from_layer(wear.get("edgeWear"), ("base", "amount")) > 0
        or has_non_empty(wear.get("scratches"))
        or has_non_empty(wear.get("chips"))
    ):
        return True
    dirt = material.get("dirt")
    if isinstance(dirt, dict) and (
        number_from_layer(dirt.get("amount"), ("base", "amount")) > 0
        or number_from_layer(dirt.get("cavityBias"), ("base", "amount")) > 0
    ):
        return True
    for field in ("moss", "stains", "scratches", "chips", "wetness", "patina", "soot"):
        if has_non_empty(material.get(field)):
            return True
    return False


def quality_first_enabled(spec: dict[str, Any]) -> bool:
    targets = spec.get("lookDevTargets")
    return isinstance(targets, dict) and targets.get("qualityPriority") == "reference-fidelity"


def reference_pbr_usable(material: dict[str, Any], threshold: float) -> tuple[bool, str]:
    material_id = str(material.get("id") or "(unnamed)")
    reference = material.get("referencePbr")
    if not isinstance(reference, dict):
        return False, f"material {material_id!r} needs usable referencePbr extracted from source pixels"
    if reference.get("usable") is not True:
        return False, f"material {material_id!r} referencePbr.usable must be true"
    confidence = reference.get("confidence", reference.get("estimatedFidelity"))
    if not has_number(confidence) or float(confidence) < threshold:
        return False, f"material {material_id!r} referencePbr confidence must be >= {threshold}"
    maps = reference.get("maps")
    if not isinstance(maps, dict):
        return False, f"material {material_id!r} referencePbr needs maps"
    for channel in ("albedo", "roughness", "height", "normal", "ao"):
        entry = maps.get(channel)
        if not isinstance(entry, dict) or not has_non_empty(entry.get("url") or entry.get("path")):
            return False, f"material {material_id!r} referencePbr missing {channel} map path/url"
    return True, ""


def quality_first_material_gaps(spec: dict[str, Any], material: dict[str, Any]) -> list[str]:
    material_id = str(material.get("id") or "(unnamed)")
    gaps: list[str] = []
    targets = spec.get("lookDevTargets")
    material_targets = targets.get("materialPass", {}) if isinstance(targets, dict) else {}
    minimum_resolution = material_targets.get("minimumTextureResolution", 1024)
    if not isinstance(minimum_resolution, int) or isinstance(minimum_resolution, bool):
        minimum_resolution = 1024
    extraction_targets = material_targets.get("referencePbrExtraction", {})
    if not isinstance(extraction_targets, dict):
        extraction_targets = {}
    pbr_required = (
        extraction_targets.get("requiredWhenSourceImagePresent") is True
        and has_non_empty(spec.get("sourceImage"))
    )
    pbr_threshold = extraction_targets.get("targetThreshold", 0.7)
    if not has_number(pbr_threshold):
        pbr_threshold = 0.7
    resolution = material.get("textureResolution")
    if not isinstance(resolution, int) or isinstance(resolution, bool) or resolution < minimum_resolution:
        gaps.append(f"material {material_id!r} textureResolution must be >= {minimum_resolution}")

    projection = material.get("textureProjection")
    if not isinstance(projection, dict) or not has_non_empty(projection.get("mode")):
        gaps.append(f"material {material_id!r} needs textureProjection.mode and texel-density intent")

    bands = material.get("surfaceFrequencyBands")
    band_ids = {
        str(item.get("id")).lower()
        for item in bands
        if isinstance(item, dict) and has_non_empty(item.get("id"))
    } if isinstance(bands, list) else set()
    missing_bands = {"macro", "meso", "micro"} - band_ids
    if missing_bands:
        gaps.append(
            f"material {material_id!r} missing surface frequency bands: "
            + ", ".join(sorted(missing_bands))
        )

    roughness = material.get("roughness")
    roughness_map = roughness.get("map") if isinstance(roughness, dict) else None
    if not has_non_empty(roughness_map) or "albedo" in str(roughness_map).lower():
        gaps.append(f"material {material_id!r} needs an independent roughness map")
    if not has_non_empty(material.get("normal")) and not has_non_empty(material.get("bump")):
        gaps.append(f"material {material_id!r} needs an independent height/normal response")
    if not has_non_empty(material.get("ambientOcclusion")):
        gaps.append(f"material {material_id!r} needs an independent ambient-occlusion response")
    if pbr_required:
        ok, message = reference_pbr_usable(material, float(pbr_threshold))
        if not ok:
            gaps.append(message)
    return gaps


def material_pass_gaps(spec: dict[str, Any]) -> list[str]:
    materials = [item for item in spec.get("materials", []) if isinstance(item, dict)]
    if not materials:
        return ["materials array is empty"]
    if not any(material_has_palette(item) for item in materials):
        return ["no material has a reference-derived albedo palette or secondary color zones"]
    gaps: list[str] = []
    if not any(material_has_response(item) for item in materials):
        gaps.append("no material defines roughness variation or normal/bump/displacement response")
    if not any(material_has_locality(item) for item in materials):
        gaps.append("no material defines local overrides, AO, dirt, wear, stains, moss, chips, or scratches")
    if quality_first_enabled(spec):
        for material in materials:
            if material.get("qualityTier") == "utility":
                continue
            gaps.extend(quality_first_material_gaps(spec, material))
    return gaps


def surface_pass_gaps(spec: dict[str, Any]) -> list[str]:
    components = [item for item in spec.get("componentTree", []) if isinstance(item, dict)]
    has_surface_detail = any(has_non_empty(item.get("surfaceDetail")) for item in components)
    if not has_surface_detail:
        return ["componentTree has no meaningful surfaceDetail for normal/bump/displacement/AO locality"]
    return []


def lighting_pass_gaps(spec: dict[str, Any]) -> list[str]:
    lighting = spec.get("lightingFromPhoto", [])
    if not isinstance(lighting, list) or len([item for item in lighting if has_non_empty(item)]) < 3:
        return ["lightingFromPhoto needs at least three concrete entries for key/fill/rim or environment lighting"]
    text = " ".join(str(item).lower() for item in lighting)
    required_groups = {
        "key light": ("key", "sun", "main light"),
        "fill light": ("fill", "ambient", "hemisphere"),
        "rim/environment light": ("rim", "back light", "environment", "hdr", "reflection"),
        "exposure/tone mapping": ("exposure", "tone", "aces", "filmic"),
        "contact shadow": ("contact shadow", "ground shadow", "ambient occlusion", "ao"),
    }
    gaps = [
        f"lightingFromPhoto missing {label}"
        for label, terms in required_groups.items()
        if not any(term in text for term in terms)
    ]
    return gaps


def pass_specific_evidence(pass_id: str) -> list[str]:
    if pass_id in {"structural-pass", "form-refinement"}:
        return [
            "attachment contracts for child appendages/connectors",
            "no floating child roots/joints in the browser screenshot",
        ]
    if pass_id == "material-pass":
        return [
            "reference-derived albedo palette with dominant, secondary, and accent colors",
            "independent albedo, roughness, height/normal, and AO maps",
            "macro, meso, and micro surface-frequency response at 1024px or higher",
            "local material masks: AO, dirt, wear, stains, moss, chips, scratches, wetness, or equivalent",
            "neutral, grazing-light close-up, and reference-matched browser screenshots",
            "AI vision comparison sheet score meeting the visual acceptance threshold",
        ]
    if pass_id == "surface-pass":
        return [
            "component surfaceDetail for tactile normal/bump/displacement and locality",
        ]
    if pass_id == "lighting-pass":
        return [
            "lightingFromPhoto with key/fill/rim or environment light",
            "exposure, tone mapping, background, shadow softness, and contact shadow behavior",
        ]
    return []


def pass_specific_gaps(spec: dict[str, Any], pass_id: str) -> list[str]:
    if pass_id in {"structural-pass", "form-refinement"}:
        return attachment_gaps(spec)
    if pass_id == "material-pass":
        return material_pass_gaps(spec)
    if pass_id == "surface-pass":
        return surface_pass_gaps(spec)
    if pass_id == "lighting-pass":
        return lighting_pass_gaps(spec)
    return []


def sync_pipeline(spec: dict[str, Any]) -> dict[str, Any]:
    ids = pass_order(spec)
    completed = completed_passes(spec, ids)
    current = current_pass(ids, completed)
    pipeline = spec.setdefault("sculptPipeline", {})
    if not isinstance(pipeline, dict):
        pipeline = {}
        spec["sculptPipeline"] = pipeline
    pipeline.update(
        {
            "passGateMode": "locked-sequential",
            "passOrder": ids,
            "currentPass": current,
            "completedPasses": completed,
            "lastCompletedPass": completed[-1] if completed else "",
            "blockedReason": "" if current != "complete" else "all build passes completed",
            "nextRequiredEvidence": next_required_evidence(spec, current),
        }
    )
    return pipeline


def check_pass(spec: dict[str, Any], requested_pass: str) -> tuple[bool, str, dict[str, Any]]:
    pipeline = sync_pipeline(spec)
    ids = list(pipeline["passOrder"])
    if requested_pass not in ids:
        return False, f"unknown build pass {requested_pass!r}", pipeline
    current = str(pipeline["currentPass"])
    completed = list(pipeline.get("completedPasses", []))
    if requested_pass in completed or current == "complete":
        gaps = pass_specific_gaps(spec, requested_pass)
        if gaps:
            return False, f"pass {requested_pass!r} needs spec refinement: {'; '.join(gaps)}", pipeline
        return True, f"pass {requested_pass!r} is already completed and can be regenerated", pipeline
    if requested_pass == current:
        gaps = pass_specific_gaps(spec, requested_pass)
        if gaps:
            return False, f"pass {requested_pass!r} needs spec refinement: {'; '.join(gaps)}", pipeline
        return True, f"pass {requested_pass!r} is the current unlocked pass", pipeline
    previous_index = ids.index(requested_pass) - 1
    previous = ids[previous_index] if previous_index >= 0 else ""
    return (
        False,
        f"pass {requested_pass!r} is locked; complete {previous!r} with reviewHistory.action=continue and screenshot evidence first",
        pipeline,
    )


def status_payload(spec: dict[str, Any]) -> dict[str, Any]:
    pipeline = sync_pipeline(spec)
    return {
        "targetName": spec.get("targetName"),
        "passGateMode": pipeline.get("passGateMode"),
        "currentPass": pipeline.get("currentPass"),
        "completedPasses": pipeline.get("completedPasses", []),
        "nextRequiredEvidence": pipeline.get("nextRequiredEvidence", []),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Print current sculpt pipeline state")
    status_parser.add_argument("spec", type=Path)
    status_parser.add_argument("--json", action="store_true")

    check_parser = subparsers.add_parser("check", help="Fail unless a build pass is unlocked")
    check_parser.add_argument("spec", type=Path)
    check_parser.add_argument("--pass-id", required=True)
    check_parser.add_argument("--json", action="store_true")

    sync_parser = subparsers.add_parser("sync", help="Refresh sculptPipeline from reviewHistory")
    sync_parser.add_argument("spec", type=Path)
    sync_parser.add_argument("--in-place", action="store_true")
    sync_parser.add_argument("--out", type=Path)
    sync_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    spec_path = args.spec.expanduser().resolve()
    spec = load_spec(spec_path)

    if args.command == "status":
        payload = status_payload(spec)
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(f"currentPass: {payload['currentPass']}")
            print(f"completedPasses: {', '.join(payload['completedPasses']) or '(none)'}")
            for item in payload["nextRequiredEvidence"]:
                print(f"required: {item}")
        return 0

    if args.command == "check":
        ok, message, pipeline = check_pass(spec, args.pass_id)
        payload = {"ok": ok, "message": message, "pipeline": pipeline}
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print("PASS" if ok else "FAIL")
            print(message)
        return 0 if ok else 1

    if args.command == "sync":
        payload = status_payload(spec)
        output = spec_path if args.in_place else (args.out.expanduser().resolve() if args.out else None)
        if output:
            write_spec(output, spec)
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(output or json.dumps(spec, indent=2, ensure_ascii=False))
        return 0

    parser.error("unreachable command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
