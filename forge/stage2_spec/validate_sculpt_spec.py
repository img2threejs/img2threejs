#!/usr/bin/env python3
"""Validate an ObjectSculptSpec JSON file for procedural Three.js generation."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
from feature_acceptance_policy import feature_gate_failures, feature_review_policy


REQUIRED_TOP_LEVEL = {
    "targetName": str,
    "suitability": str,
    "coordinateFrame": dict,
    "silhouette": dict,
    "componentTree": list,
    "materials": list,
    "proceduralStrategy": list,
}
VALID_SUITABILITY = {"pass", "conditional", "reject"}
VALID_PRIMITIVES = {
    "box",
    "sphere",
    "ellipsoid",
    "cylinder",
    "cone",
    "capsule",
    "torus",
    "tube",
    "lathe",
    "extrude",
    "curve-sweep",
    "plane-card",
    "instanced-cluster",
}
VALID_COMPONENT_LEVELS = {"macro", "meso", "micro"}
VALID_COMPLEXITY_TIERS = {"unassessed", "simple", "moderate", "complex", "ultra-complex"}
TERMINOLOGY_LIST_FIELDS = {"geometryTerms", "materialTerms", "lightingTerms"}
VALID_REVIEW_ACTIONS = {"continue", "refine-spec", "refine-code", "request-input", "stop"}
VISUAL_PASS_IDS = {
    "blockout",
    "structural-pass",
    "form-refinement",
    "material-pass",
    "surface-pass",
    "lighting-pass",
    "interaction-pass",
}
VALID_PIPELINE_PASS_IDS = VISUAL_PASS_IDS | {"optimization-pass"}
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


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_unit_interval(value: Any, label: str, errors: list[str]) -> None:
    if not is_number(value) or value < 0 or value > 1:
        errors.append(f"{label} must be a number from 0 to 1")


def load_spec(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("spec must be a JSON object")
    return payload


def as_number_list(value: Any, length: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == length
        and all(isinstance(item, (int, float)) for item in value)
    )


def validate_score_block(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    scores = spec.get("scores")
    if scores is None:
        warnings.append("missing scores block; image validation evidence will be weaker")
        return
    if not isinstance(scores, dict):
        errors.append("scores must be an object")
        return
    for key, value in scores.items():
        if not isinstance(value, int) or value < 0 or value > 3:
            errors.append(f"score {key!r} must be an integer from 0 to 3")


def validate_nonnegative_int(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        errors.append(f"{label} must be a non-negative integer")


def validate_pre_spec_assessment(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    assessment = spec.get("preSpecAssessment")
    if assessment is None:
        warnings.append("quality: missing preSpecAssessment; spec may be shallow because complexity was not assessed first")
        return
    if not isinstance(assessment, dict):
        errors.append("preSpecAssessment must be an object")
        return
    object_class = assessment.get("objectClass")
    if not isinstance(object_class, dict):
        errors.append("preSpecAssessment.objectClass must be an object")
    else:
        primary_type = object_class.get("primaryType")
        if primary_type is not None and not isinstance(primary_type, str):
            errors.append("preSpecAssessment.objectClass.primaryType must be a string")
        if primary_type in {None, "", "unassessed"}:
            warnings.append("quality: preSpecAssessment.objectClass.primaryType is unassessed")
        for field in ("formLanguage", "structureKind", "motionPotential", "materialFamilies"):
            validate_string_array(object_class.get(field), f"preSpecAssessment.objectClass.{field}", errors)
            if isinstance(object_class.get(field), list) and not object_class[field]:
                warnings.append(f"quality: preSpecAssessment.objectClass.{field} is empty")
    complexity = assessment.get("complexity")
    if not isinstance(complexity, dict):
        errors.append("preSpecAssessment.complexity must be an object")
    else:
        tier = complexity.get("tier")
        if tier not in VALID_COMPLEXITY_TIERS:
            errors.append(f"preSpecAssessment.complexity.tier must be one of: {', '.join(sorted(VALID_COMPLEXITY_TIERS))}")
        if tier == "unassessed":
            warnings.append("quality: preSpecAssessment.complexity.tier is unassessed")
        scores = complexity.get("scores")
        if not isinstance(scores, dict):
            errors.append("preSpecAssessment.complexity.scores must be an object")
        else:
            for key, value in scores.items():
                if not isinstance(value, int) or value < 0 or value > 3:
                    errors.append(f"preSpecAssessment.complexity.scores.{key} must be an integer from 0 to 3")
        estimated = complexity.get("estimatedCounts")
        if not isinstance(estimated, dict):
            errors.append("preSpecAssessment.complexity.estimatedCounts must be an object")
        else:
            for field in ("macroComponents", "mesoComponents", "microFeatureGroups", "materialLayers", "repetitionSystems"):
                if field in estimated:
                    validate_nonnegative_int(estimated[field], f"preSpecAssessment.complexity.estimatedCounts.{field}", errors)
        validate_string_array(complexity.get("reasoning"), "preSpecAssessment.complexity.reasoning", errors)
    decision = assessment.get("specDepthDecision")
    if not isinstance(decision, dict):
        errors.append("preSpecAssessment.specDepthDecision must be an object")
    else:
        required_depth = decision.get("requiredDepth")
        if required_depth not in VALID_COMPLEXITY_TIERS:
            errors.append("preSpecAssessment.specDepthDecision.requiredDepth must be a valid complexity tier")
        if required_depth == "unassessed":
            warnings.append("quality: preSpecAssessment.specDepthDecision.requiredDepth is unassessed")
        validate_string_array(decision.get("minimumComponentLevels"), "preSpecAssessment.specDepthDecision.minimumComponentLevels", errors)
        for field in (
            "needsRepetitionSystems",
            "needsMaterialLocalOverrides",
            "needsMultipleReviewViews",
            "needsActionReadyHierarchy",
        ):
            if field in decision and not isinstance(decision[field], bool):
                errors.append(f"preSpecAssessment.specDepthDecision.{field} must be boolean")
    unknowns = assessment.get("unknownsToResolveBeforeImplementation")
    validate_string_array(unknowns, "preSpecAssessment.unknownsToResolveBeforeImplementation", errors)
    if isinstance(unknowns, list) and unknowns:
        warnings.append("quality: preSpecAssessment has unresolved unknowns before implementation")


def validate_terminology_profile(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    profile = spec.get("terminologyProfile")
    if profile is None:
        warnings.append("missing terminologyProfile; descriptions may drift into vague non-3D language")
        return
    if not isinstance(profile, dict):
        errors.append("terminologyProfile must be an object")
        return
    for field in TERMINOLOGY_LIST_FIELDS:
        value = profile.get(field)
        if value is None:
            warnings.append(f"terminologyProfile.{field} is missing")
            continue
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            errors.append(f"terminologyProfile.{field} must be an array of non-empty strings")
    rule = profile.get("descriptionRule")
    if rule is not None and not isinstance(rule, str):
        errors.append("terminologyProfile.descriptionRule must be a string")


def validate_evidence(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> set[str]:
    refs: set[str] = set()
    evidence = spec.get("viewEvidence", [])
    if evidence is None:
        return refs
    if not isinstance(evidence, list):
        errors.append("viewEvidence must be an array when present")
        return refs
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            errors.append(f"viewEvidence[{index}] must be an object")
            continue
        evidence_id = item.get("id")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            errors.append(f"viewEvidence[{index}].id is required")
            continue
        if evidence_id in refs:
            errors.append(f"duplicate viewEvidence id {evidence_id!r}")
        refs.add(evidence_id)
        confidence = item.get("confidence")
        if confidence is not None:
            validate_unit_interval(confidence, f"viewEvidence {evidence_id!r} confidence", errors)
        region = item.get("imageRegion")
        if region is not None:
            if not isinstance(region, dict):
                errors.append(f"viewEvidence {evidence_id!r} imageRegion must be an object")
            else:
                for key in ("x", "y", "width", "height"):
                    if key in region and not is_number(region[key]):
                        errors.append(f"viewEvidence {evidence_id!r} imageRegion.{key} must be numeric")
    if not refs:
        warnings.append("missing viewEvidence; local visual claims cannot be traced back to image regions")
    return refs


def validate_material_scalar_or_layer(value: Any, label: str, errors: list[str]) -> None:
    if value is None:
        return
    if is_number(value):
        return
    if not isinstance(value, dict):
        errors.append(f"{label} must be a number or object")
        return
    base = value.get("base")
    if base is not None and not is_number(base):
        errors.append(f"{label}.base must be numeric")
    variation = value.get("variation")
    if variation is not None and not is_number(variation):
        errors.append(f"{label}.variation must be numeric")


def validate_reference_pbr_map(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return
    has_locator = False
    for field in ("path", "url"):
        item = value.get(field)
        if item is not None:
            if not isinstance(item, str) or not item.strip():
                errors.append(f"{label}.{field} must be a non-empty string when present")
            else:
                has_locator = True
    if not has_locator:
        errors.append(f"{label} needs a path or url")
    channel = value.get("channel")
    if channel is not None and not isinstance(channel, str):
        errors.append(f"{label}.channel must be a string")


def validate_reference_pbr(material_id: str, value: Any, errors: list[str], warnings: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append(f"material {material_id!r} referencePbr must be an object")
        return
    for field in ("version", "sourceImage", "extractor", "method", "verdict", "hardLimit"):
        item = value.get(field)
        if item is not None and not isinstance(item, str):
            errors.append(f"material {material_id!r} referencePbr.{field} must be a string")
    usable = value.get("usable")
    if usable is not None and not isinstance(usable, bool):
        errors.append(f"material {material_id!r} referencePbr.usable must be boolean")
    for field in ("confidence", "estimatedFidelity", "targetThreshold"):
        item = value.get(field)
        if item is not None:
            validate_unit_interval(item, f"material {material_id!r} referencePbr.{field}", errors)
    maps = value.get("maps")
    if maps is None:
        warnings.append(f"quality: material {material_id!r} referencePbr is missing maps")
        return
    if not isinstance(maps, dict):
        errors.append(f"material {material_id!r} referencePbr.maps must be an object")
        return
    required = ("albedo", "roughness", "height", "normal", "ao")
    for channel in required:
        if channel not in maps:
            warnings.append(f"quality: material {material_id!r} referencePbr.maps missing {channel}")
        else:
            validate_reference_pbr_map(maps[channel], f"material {material_id!r} referencePbr.maps.{channel}", errors)


def validate_materials(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> set[str]:
    material_ids: set[str] = set()
    for index, material in enumerate(spec.get("materials", [])):
        if not isinstance(material, dict):
            errors.append(f"materials[{index}] must be an object")
            continue
        material_id = material.get("id")
        if not isinstance(material_id, str) or not material_id.strip():
            errors.append(f"materials[{index}].id is required")
            continue
        if material_id in material_ids:
            errors.append(f"duplicate material id {material_id!r}")
        material_ids.add(material_id)
        color = material.get("baseColor", material.get("color"))
        if color is not None and not (isinstance(color, str) and color.startswith("#") and len(color) in {4, 7}):
            errors.append(f"material {material_id!r} baseColor/color should be #RGB or #RRGGBB")
        for field in ("shaderModel", "type"):
            value = material.get(field)
            if value is not None and not isinstance(value, str):
                errors.append(f"material {material_id!r} {field} must be a string")
        for field in ("albedo", "ambientOcclusion"):
            value = material.get(field)
            if value is not None and not isinstance(value, dict):
                errors.append(f"material {material_id!r} {field} must be an object")
        validate_material_scalar_or_layer(material.get("roughness"), f"material {material_id!r} roughness", errors)
        validate_material_scalar_or_layer(material.get("metalness"), f"material {material_id!r} metalness", errors)
        for field in ("normal", "bump", "displacement", "wear", "dirt"):
            value = material.get(field)
            if value is not None and not isinstance(value, dict):
                errors.append(f"material {material_id!r} {field} must be an object")
        texture_resolution = material.get("textureResolution")
        if texture_resolution is not None and (
            not isinstance(texture_resolution, int)
            or isinstance(texture_resolution, bool)
            or texture_resolution < 64
            or texture_resolution > 4096
        ):
            errors.append(f"material {material_id!r} textureResolution must be an integer from 64 to 4096")
        projection = material.get("textureProjection")
        if projection is not None:
            if not isinstance(projection, dict):
                errors.append(f"material {material_id!r} textureProjection must be an object")
            else:
                mode = projection.get("mode")
                if mode is not None and not isinstance(mode, str):
                    errors.append(f"material {material_id!r} textureProjection.mode must be a string")
                repeat = projection.get("repeat")
                if repeat is not None and not (
                    isinstance(repeat, list)
                    and len(repeat) == 2
                    and all(is_number(item) and item > 0 for item in repeat)
                ):
                    errors.append(f"material {material_id!r} textureProjection.repeat must contain two positive numbers")
                anisotropy = projection.get("anisotropy")
                if anisotropy is not None and (not is_number(anisotropy) or anisotropy < 1):
                    errors.append(f"material {material_id!r} textureProjection.anisotropy must be >= 1")
        frequency_bands = material.get("surfaceFrequencyBands")
        if frequency_bands is not None:
            if not isinstance(frequency_bands, list):
                errors.append(f"material {material_id!r} surfaceFrequencyBands must be an array")
            else:
                seen_band_ids: set[str] = set()
                for band_index, band in enumerate(frequency_bands):
                    if not isinstance(band, dict):
                        errors.append(
                            f"material {material_id!r} surfaceFrequencyBands[{band_index}] must be an object"
                        )
                        continue
                    band_id = band.get("id")
                    if not isinstance(band_id, str) or not band_id.strip():
                        errors.append(
                            f"material {material_id!r} surfaceFrequencyBands[{band_index}].id is required"
                        )
                    elif band_id in seen_band_ids:
                        errors.append(f"material {material_id!r} has duplicate surface band {band_id!r}")
                    else:
                        seen_band_ids.add(band_id)
                    for field in ("frequency", "amplitude"):
                        value = band.get(field)
                        if not is_number(value) or value <= 0:
                            errors.append(
                                f"material {material_id!r} surfaceFrequencyBands[{band_index}].{field} "
                                "must be a positive number"
                            )
        local_overrides = material.get("localOverrides", [])
        if local_overrides is not None and not isinstance(local_overrides, list):
            errors.append(f"material {material_id!r} localOverrides must be an array")
        shader_notes = material.get("shaderNotes")
        if shader_notes is not None:
            validate_string_array(shader_notes, f"material {material_id!r} shaderNotes", errors)
        validate_reference_pbr(material_id, material.get("referencePbr"), errors, warnings)
    if not material_ids:
        errors.append("at least one material is required")
    return material_ids


def validate_dimensions(component_id: str, dimensions: Any, errors: list[str]) -> None:
    if dimensions is None:
        return
    if not isinstance(dimensions, dict):
        errors.append(f"component {component_id!r} dimensions must be an object")
        return
    for field in ("width", "height", "depth", "radius", "length"):
        if field in dimensions and not is_number(dimensions[field]):
            errors.append(f"component {component_id!r} dimensions.{field} must be numeric")
    confidence = dimensions.get("confidence")
    if confidence is not None:
        validate_unit_interval(confidence, f"component {component_id!r} dimensions.confidence", errors)


def validate_geometry_descriptor(component_id: str, descriptor: Any, errors: list[str]) -> None:
    if descriptor is None:
        return
    if not isinstance(descriptor, dict):
        errors.append(f"component {component_id!r} geometryDescriptor must be an object")
        return
    for field in ("topologyIntent", "uvStrategy", "normalStrategy"):
        value = descriptor.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"component {component_id!r} geometryDescriptor.{field} must be a string")
    edge = descriptor.get("edgeTreatment")
    if edge is not None:
        if not isinstance(edge, dict):
            errors.append(f"component {component_id!r} geometryDescriptor.edgeTreatment must be an object")
        else:
            if "bevelRadius" in edge and not is_number(edge["bevelRadius"]):
                errors.append(f"component {component_id!r} edgeTreatment.bevelRadius must be numeric")
            if "segments" in edge and not isinstance(edge["segments"], int):
                errors.append(f"component {component_id!r} edgeTreatment.segments must be an integer")
    stack = descriptor.get("deformationStack")
    if stack is not None and not isinstance(stack, list):
        errors.append(f"component {component_id!r} geometryDescriptor.deformationStack must be an array")


def validate_bool_object(value: Any, label: str, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return
    for key, item in value.items():
        if not isinstance(key, str):
            errors.append(f"{label} keys must be strings")
        if not isinstance(item, bool):
            errors.append(f"{label}.{key} must be boolean")


def validate_action_profile(component_id: str, profile: Any, errors: list[str], warnings: list[str]) -> None:
    if profile is None:
        warnings.append(f"component {component_id!r} is missing actionProfile; future animation/destruction may require refactor")
        return
    if not isinstance(profile, dict):
        errors.append(f"component {component_id!r} actionProfile must be an object")
        return
    role = profile.get("animationRole")
    if role is not None and not isinstance(role, str):
        errors.append(f"component {component_id!r} actionProfile.animationRole must be a string")
    pivot = profile.get("pivot")
    if pivot is not None:
        if not isinstance(pivot, dict):
            errors.append(f"component {component_id!r} actionProfile.pivot must be an object")
        else:
            mode = pivot.get("mode")
            if mode is not None and not isinstance(mode, str):
                errors.append(f"component {component_id!r} actionProfile.pivot.mode must be a string")
            for field in ("localPosition", "axis"):
                if field in pivot and not as_number_list(pivot[field], 3):
                    errors.append(f"component {component_id!r} actionProfile.pivot.{field} must be [number, number, number]")
            confidence = pivot.get("confidence")
            if confidence is not None:
                validate_unit_interval(confidence, f"component {component_id!r} actionProfile.pivot.confidence", errors)
    validate_bool_object(profile.get("transformChannels"), f"component {component_id!r} actionProfile.transformChannels", errors)
    sockets = profile.get("sockets")
    if sockets is not None:
        if not isinstance(sockets, list):
            errors.append(f"component {component_id!r} actionProfile.sockets must be an array")
        else:
            for socket_index, socket in enumerate(sockets):
                if not isinstance(socket, dict):
                    errors.append(f"component {component_id!r} actionProfile.sockets[{socket_index}] must be an object")
                    continue
                socket_id = socket.get("id")
                if socket_id is not None and not isinstance(socket_id, str):
                    errors.append(f"component {component_id!r} actionProfile.sockets[{socket_index}].id must be a string")
                for field in ("localPosition", "position", "localRotation", "rotation"):
                    if field in socket and not as_number_list(socket[field], 3):
                        errors.append(
                            f"component {component_id!r} actionProfile.sockets[{socket_index}].{field} must be [number, number, number]"
                        )
    collider = profile.get("collider")
    if collider is not None:
        if not isinstance(collider, dict):
            errors.append(f"component {component_id!r} actionProfile.collider must be an object")
        else:
            collider_type = collider.get("type")
            if collider_type is not None and not isinstance(collider_type, str):
                errors.append(f"component {component_id!r} actionProfile.collider.type must be a string")
            for field in ("offset", "scale"):
                if field in collider and not as_number_list(collider[field], 3):
                    errors.append(f"component {component_id!r} actionProfile.collider.{field} must be [number, number, number]")
            if "isTrigger" in collider and not isinstance(collider["isTrigger"], bool):
                errors.append(f"component {component_id!r} actionProfile.collider.isTrigger must be boolean")
    constraints = profile.get("constraints")
    if constraints is not None and not isinstance(constraints, list):
        errors.append(f"component {component_id!r} actionProfile.constraints must be an array")
    destruction = profile.get("destruction")
    if destruction is not None:
        if not isinstance(destruction, dict):
            errors.append(f"component {component_id!r} actionProfile.destruction must be an object")
        else:
            if "breakable" in destruction and not isinstance(destruction["breakable"], bool):
                errors.append(f"component {component_id!r} actionProfile.destruction.breakable must be boolean")
            if "breakImpulse" in destruction and not is_number(destruction["breakImpulse"]):
                errors.append(f"component {component_id!r} actionProfile.destruction.breakImpulse must be numeric")
            for field in ("fractureGroup", "debrisMaterial"):
                value = destruction.get(field)
                if value is not None and not isinstance(value, str):
                    errors.append(f"component {component_id!r} actionProfile.destruction.{field} must be a string")
            for field in ("seamRefs", "detachableFragments"):
                validate_string_array(destruction.get(field), f"component {component_id!r} actionProfile.destruction.{field}", errors)


def component_requires_attachment(component: dict[str, Any]) -> bool:
    if not component.get("parent"):
        return False
    role = str(component.get("role") or "").lower()
    name = str(component.get("name") or component.get("id") or "").lower()
    primitive = str(component.get("primitive") or "").lower()
    profile = component.get("actionProfile") if isinstance(component.get("actionProfile"), dict) else {}
    animation_role = str(profile.get("animationRole") or "").lower()
    tokens = {role, animation_role} | set(re.findall(r"[a-z0-9]+", name))
    return bool(tokens & ATTACHMENT_ROLES) or primitive in ATTACHMENT_PRIMITIVES


def has_attachment_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def attachment_is_complete(attachment: dict[str, Any]) -> bool:
    has_endpoint = as_number_list(attachment.get("localStart"), 3) and as_number_list(attachment.get("localEnd"), 3)
    has_socket = isinstance(attachment.get("parentSocket"), str) and bool(attachment["parentSocket"].strip())
    has_parent_id = isinstance(attachment.get("parentId"), str) and bool(attachment["parentId"].strip())
    has_contact = isinstance(attachment.get("contactType"), str) and bool(attachment["contactType"].strip())
    has_overlap = (
        has_attachment_number(attachment.get("embedDepth"))
        and float(attachment["embedDepth"]) > 0
    ) or (
        has_attachment_number(attachment.get("overlap"))
        and float(attachment["overlap"]) > 0
    )
    has_tolerance = has_attachment_number(attachment.get("gapTolerance"))
    return has_endpoint and (has_socket or has_parent_id) and has_contact and has_overlap and has_tolerance


def validate_attachment(
    component_id: str,
    parent: str | None,
    attachment: Any,
    required: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    if attachment is None:
        if required:
            warnings.append(
                f"quality: component {component_id!r} requires attachment.parentSocket, localStart/localEnd, "
                "contactType, embedDepth or overlap, and gapTolerance"
            )
        return
    if not isinstance(attachment, dict):
        errors.append(f"component {component_id!r} attachment must be an object")
        return
    for field in ("parentId", "parentSocket", "contactType"):
        value = attachment.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"component {component_id!r} attachment.{field} must be a string")
    if parent and isinstance(attachment.get("parentId"), str) and attachment["parentId"] != parent:
        warnings.append(
            f"quality: component {component_id!r} attachment.parentId {attachment['parentId']!r} "
            f"does not match parent {parent!r}"
        )
    for field in ("localStart", "localEnd", "contactNormal"):
        value = attachment.get(field)
        if value is not None and not as_number_list(value, 3):
            errors.append(f"component {component_id!r} attachment.{field} must be [number, number, number]")
    for field in ("embedDepth", "overlap", "gapTolerance", "baseRadius", "endRadius"):
        value = attachment.get(field)
        if value is not None and (not has_attachment_number(value) or float(value) < 0):
            errors.append(f"component {component_id!r} attachment.{field} must be a non-negative number")
    validate_string_array(attachment.get("evidenceRefs"), f"component {component_id!r} attachment.evidenceRefs", errors)
    if required and not attachment_is_complete(attachment):
        warnings.append(
            f"quality: component {component_id!r} requires attachment.parentSocket, localStart/localEnd, "
            "contactType, embedDepth or overlap, and gapTolerance"
        )


def validate_string_array(value: Any, label: str, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append(f"{label} must be an array of strings")


def validate_components(
    spec: dict[str, Any],
    material_ids: set[str],
    evidence_ids: set[str],
    errors: list[str],
    warnings: list[str],
) -> None:
    components = spec.get("componentTree", [])
    ids: set[str] = set()
    parent_refs: list[tuple[str, str]] = []
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            errors.append(f"componentTree[{index}] must be an object")
            continue
        component_id = component.get("id")
        if not isinstance(component_id, str) or not component_id.strip():
            errors.append(f"componentTree[{index}].id is required")
            continue
        if component_id in ids:
            errors.append(f"duplicate component id {component_id!r}")
        ids.add(component_id)
        primitive = component.get("primitive")
        if primitive not in VALID_PRIMITIVES:
            errors.append(
                f"component {component_id!r} primitive must be one of: {', '.join(sorted(VALID_PRIMITIVES))}"
            )
        level = component.get("level")
        if level is not None and level not in VALID_COMPONENT_LEVELS:
            errors.append(f"component {component_id!r} level must be macro, meso, or micro")
        for field in ("importance", "confidence"):
            value = component.get(field)
            if value is not None:
                validate_unit_interval(value, f"component {component_id!r} {field}", errors)
        parent = component.get("parent")
        if parent:
            if not isinstance(parent, str):
                errors.append(f"component {component_id!r} parent must be a string or null")
            else:
                parent_refs.append((component_id, parent))
        material = component.get("material")
        if material and material not in material_ids:
            errors.append(f"component {component_id!r} references unknown material {material!r}")
        validate_geometry_descriptor(component_id, component.get("geometryDescriptor"), errors)
        material_layers = component.get("materialLayers")
        if material_layers is not None:
            validate_string_array(material_layers, f"component {component_id!r} materialLayers", errors)
            if isinstance(material_layers, list):
                for material_layer in material_layers:
                    if material_layer not in material_ids:
                        errors.append(
                            f"component {component_id!r} materialLayers references unknown material {material_layer!r}"
                        )
        validate_dimensions(component_id, component.get("dimensions"), errors)
        transform = component.get("transform", {})
        if transform is not None and not isinstance(transform, dict):
            errors.append(f"component {component_id!r} transform must be an object")
        elif isinstance(transform, dict):
            for field in ("position", "rotation", "scale"):
                if field in transform and not as_number_list(transform[field], 3):
                    errors.append(f"component {component_id!r} transform.{field} must be [number, number, number]")
        validate_action_profile(component_id, component.get("actionProfile"), errors, warnings)
        validate_attachment(
            component_id,
            parent if isinstance(parent, str) else None,
            component.get("attachment"),
            component_requires_attachment(component),
            errors,
            warnings,
        )
        for field in ("deformations", "joints", "seams", "localFeatures"):
            value = component.get(field)
            if value is not None and not isinstance(value, list):
                errors.append(f"component {component_id!r} {field} must be an array")
        surface = component.get("surfaceDetail")
        if surface is not None:
            if not isinstance(surface, dict):
                errors.append(f"component {component_id!r} surfaceDetail must be an object")
            else:
                for field in ("macroRoughness", "microRoughness", "bumpAmplitude"):
                    if field in surface and not is_number(surface[field]):
                        errors.append(f"component {component_id!r} surfaceDetail.{field} must be numeric")
        evidence_refs = component.get("evidenceRefs")
        if evidence_refs is not None:
            validate_string_array(evidence_refs, f"component {component_id!r} evidenceRefs", errors)
            if isinstance(evidence_refs, list):
                for evidence_ref in evidence_refs:
                    if evidence_ids and evidence_ref not in evidence_ids:
                        errors.append(f"component {component_id!r} references missing evidence {evidence_ref!r}")
    for component_id, parent in parent_refs:
        if parent not in ids:
            errors.append(f"component {component_id!r} references missing parent {parent!r}")
    if not ids:
        errors.append("at least one component is required")
    if len(ids) == 1:
        warnings.append("only one component found; this is likely still blockout quality")


def validate_quality_targets(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    targets = spec.get("qualityTargets")
    if targets is None:
        warnings.append("missing qualityTargets; self-correction loop has no explicit fidelity bar")
        return
    if not isinstance(targets, dict):
        errors.append("qualityTargets must be an object")
        return
    target_fidelity = targets.get("targetFidelity")
    if target_fidelity is not None:
        validate_unit_interval(target_fidelity, "qualityTargets.targetFidelity", errors)
    for field in ("mustMatch", "niceToHave", "reviewViewpoints"):
        validate_string_array(targets.get(field), f"qualityTargets.{field}", errors)


def validate_quality_contract(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    contract = spec.get("qualityContract")
    if contract is None:
        warnings.append("quality: missing qualityContract; no explicit definition of done prevents shallow specs")
        return
    if not isinstance(contract, dict):
        errors.append("qualityContract must be an object")
        return
    quality_bar = contract.get("qualityBar")
    if quality_bar is not None and not isinstance(quality_bar, str):
        errors.append("qualityContract.qualityBar must be a string")
    if quality_bar in {None, "", "unassessed"}:
        warnings.append("quality: qualityContract.qualityBar is unassessed")
    validate_string_array(contract.get("definitionOfDone"), "qualityContract.definitionOfDone", errors)
    if isinstance(contract.get("definitionOfDone"), list) and not contract["definitionOfDone"]:
        warnings.append("quality: qualityContract.definitionOfDone is empty")
    minimums = contract.get("minimumSpecDepth")
    if not isinstance(minimums, dict):
        errors.append("qualityContract.minimumSpecDepth must be an object")
    else:
        for field in (
            "macroComponents",
            "mesoComponents",
            "microFeatureGroups",
            "materialLayers",
            "repetitionSystems",
            "reviewViewpoints",
        ):
            if field in minimums:
                validate_nonnegative_int(minimums[field], f"qualityContract.minimumSpecDepth.{field}", errors)
    feature_groups = contract.get("featureGroups")
    if not isinstance(feature_groups, list):
        errors.append("qualityContract.featureGroups must be an array")
    else:
        if len(feature_groups) < 3:
            warnings.append("quality: qualityContract.featureGroups has fewer than 3 groups; spec may miss important visual layers")
        for index, group in enumerate(feature_groups):
            if not isinstance(group, dict):
                errors.append(f"qualityContract.featureGroups[{index}] must be an object")
                continue
            for field in ("id", "name"):
                value = group.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"qualityContract.featureGroups[{index}].{field} is required")
            if "required" in group and not isinstance(group["required"], bool):
                errors.append(f"qualityContract.featureGroups[{index}].required must be boolean")
            validate_string_array(group.get("qualityCriteria"), f"qualityContract.featureGroups[{index}].qualityCriteria", errors)
            validate_string_array(group.get("evidenceRefs"), f"qualityContract.featureGroups[{index}].evidenceRefs", errors)
            validate_string_array(group.get("failureModes"), f"qualityContract.featureGroups[{index}].failureModes", errors)
            if group.get("required") is True and not group.get("qualityCriteria"):
                warnings.append(f"quality: required feature group {group.get('id', index)!r} has no qualityCriteria")
    for field in ("visualDeltaChecks", "antiShallowSpecRules"):
        validate_string_array(contract.get(field), f"qualityContract.{field}", errors)
        if isinstance(contract.get(field), list) and not contract[field]:
            warnings.append(f"quality: qualityContract.{field} is empty")


def validate_quality_depth(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    contract = spec.get("qualityContract")
    if not isinstance(contract, dict) or not isinstance(contract.get("minimumSpecDepth"), dict):
        return
    minimums = contract["minimumSpecDepth"]
    components = [item for item in spec.get("componentTree", []) if isinstance(item, dict)]
    level_counts = {
        "macroComponents": sum(1 for item in components if item.get("level") == "macro"),
        "mesoComponents": sum(1 for item in components if item.get("level") == "meso"),
        "microFeatureGroups": sum(
            len(item.get("localFeatures", []))
            for item in components
            if isinstance(item.get("localFeatures", []), list)
        ),
        "materialLayers": len([item for item in spec.get("materials", []) if isinstance(item, dict)]),
        "repetitionSystems": len([item for item in spec.get("repetitionSystems", []) if isinstance(item, dict)]),
        "reviewViewpoints": len(spec.get("qualityTargets", {}).get("reviewViewpoints", []))
        if isinstance(spec.get("qualityTargets"), dict)
        and isinstance(spec.get("qualityTargets", {}).get("reviewViewpoints"), list)
        else 0,
    }
    for field, actual in level_counts.items():
        required = minimums.get(field)
        if isinstance(required, int) and actual < required:
            warnings.append(f"quality: {field} below qualityContract minimum ({actual} < {required})")


def validate_action_readiness(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    readiness = spec.get("actionReadiness")
    if readiness is None:
        warnings.append("missing actionReadiness; generated model may not be ready for animation/transformation/destruction")
        return
    if not isinstance(readiness, dict):
        errors.append("actionReadiness must be an object")
        return
    for field in ("contract", "defaultRigType", "rootMotionNode"):
        value = readiness.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"actionReadiness.{field} must be a string")
    for field in ("requiredComponentFields", "transformChannels", "authoringRules"):
        validate_string_array(readiness.get(field), f"actionReadiness.{field}", errors)
    policy = readiness.get("destructionPolicy")
    if policy is not None and not isinstance(policy, dict):
        errors.append("actionReadiness.destructionPolicy must be an object")


def validate_self_correct_loop(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    loop = spec.get("selfCorrectLoop")
    if loop is None:
        warnings.append("missing selfCorrectLoop; construction may not review/refine after each pass")
        return
    if not isinstance(loop, dict):
        errors.append("selfCorrectLoop must be an object")
        return
    enabled = loop.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        errors.append("selfCorrectLoop.enabled must be boolean")
    for field in ("reviewAfterPasses", "allowedActions", "specRefineTriggers", "codeRefineTriggers", "stopCriteria"):
        validate_string_array(loop.get(field), f"selfCorrectLoop.{field}", errors)
    actions = loop.get("allowedActions", [])
    if isinstance(actions, list):
        for action in actions:
            if action not in VALID_REVIEW_ACTIONS:
                errors.append(f"selfCorrectLoop.allowedActions contains invalid action {action!r}")
    visual_acceptance = loop.get("visualAcceptance")
    if visual_acceptance is None:
        warnings.append("quality: selfCorrectLoop.visualAcceptance is missing; AI vision cannot enforce visual fidelity")
    elif not isinstance(visual_acceptance, dict):
        errors.append("selfCorrectLoop.visualAcceptance must be an object")
    else:
        reviewer = visual_acceptance.get("reviewer")
        if reviewer is not None and not isinstance(reviewer, str):
            errors.append("selfCorrectLoop.visualAcceptance.reviewer must be a string")
        threshold = visual_acceptance.get("threshold")
        if threshold is None:
            warnings.append("quality: selfCorrectLoop.visualAcceptance.threshold is missing")
        else:
            validate_unit_interval(threshold, "selfCorrectLoop.visualAcceptance.threshold", errors)
        for field in (
            "comparisonArtifactRequired",
            "layerScoresRequired",
            "codePixelDiffIsAcceptanceAuthority",
        ):
            value = visual_acceptance.get(field)
            if value is not None and not isinstance(value, bool):
                errors.append(f"selfCorrectLoop.visualAcceptance.{field} must be boolean")
        scoring_rule = visual_acceptance.get("scoringRule")
        if scoring_rule is not None and not isinstance(scoring_rule, str):
            errors.append("selfCorrectLoop.visualAcceptance.scoringRule must be a string")
        validate_string_array(
            visual_acceptance.get("requiredLayerScores"),
            "selfCorrectLoop.visualAcceptance.requiredLayerScores",
            errors,
        )
        feature_policy = visual_acceptance.get("featureReviewPolicy")
        if feature_policy is None:
            warnings.append("quality: visualAcceptance.featureReviewPolicy is missing")
        elif not isinstance(feature_policy, dict):
            errors.append("selfCorrectLoop.visualAcceptance.featureReviewPolicy must be an object")
        else:
            for field in (
                "enabled",
                "adaptiveEscalation",
                "singleImagePairOnly",
            ):
                value = feature_policy.get(field)
                if value is not None and not isinstance(value, bool):
                    errors.append(
                        f"selfCorrectLoop.visualAcceptance.featureReviewPolicy.{field} must be boolean"
                    )
            for field in ("maxCriticalFeaturesPerPass", "maxImportantFeaturesPerPass"):
                value = feature_policy.get(field)
                if value is not None:
                    validate_nonnegative_int(
                        value,
                        f"selfCorrectLoop.visualAcceptance.featureReviewPolicy.{field}",
                        errors,
                    )
            for field in ("criticalDefaultThreshold", "importantAverageThreshold"):
                value = feature_policy.get(field)
                if value is not None:
                    validate_unit_interval(
                        value,
                        f"selfCorrectLoop.visualAcceptance.featureReviewPolicy.{field}",
                        errors,
                    )
            for field in ("reviewUnit", "selectionRule"):
                value = feature_policy.get(field)
                if value is not None and not isinstance(value, str):
                    errors.append(
                        f"selfCorrectLoop.visualAcceptance.featureReviewPolicy.{field} must be a string"
                    )
    policy = loop.get("screenshotPolicy")
    if policy is None:
        warnings.append("selfCorrectLoop.screenshotPolicy is missing; visual review may drift without screenshots")
    elif not isinstance(policy, dict):
        errors.append("selfCorrectLoop.screenshotPolicy must be an object")
    else:
        validate_string_array(policy.get("requiredForPasses"), "selfCorrectLoop.screenshotPolicy.requiredForPasses", errors)
        for field in (
            "preferredCapture",
            "fallbackCapture",
            "minimumEvidence",
            "reviewPairRule",
            "acceptanceAuthority",
        ):
            value = policy.get(field)
            if value is not None and not isinstance(value, str):
                errors.append(f"selfCorrectLoop.screenshotPolicy.{field} must be a string")


def validate_visual_evidence_item(item: Any, label: str, errors: list[str]) -> None:
    if not isinstance(item, dict):
        errors.append(f"{label} must be an object")
        return
    for field in (
        "passId",
        "referenceScreenshot",
        "renderScreenshot",
        "comparisonImage",
        "cameraView",
        "notes",
        "aiVisionNotes",
    ):
        value = item.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"{label}.{field} must be a string")
    fidelity = item.get("estimatedFidelity")
    if fidelity is not None:
        validate_unit_interval(fidelity, f"{label}.estimatedFidelity", errors)
    score = item.get("aiVisionScore")
    if score is not None:
        validate_unit_interval(score, f"{label}.aiVisionScore", errors)
    threshold = item.get("visualAcceptanceThreshold")
    if threshold is not None:
        validate_unit_interval(threshold, f"{label}.visualAcceptanceThreshold", errors)
    layer_scores = item.get("layerScores")
    if layer_scores is not None:
        if not isinstance(layer_scores, dict):
            errors.append(f"{label}.layerScores must be an object")
        else:
            for key, value in layer_scores.items():
                if not isinstance(key, str):
                    errors.append(f"{label}.layerScores keys must be strings")
                if not is_number(value) or value < 0 or value > 1:
                    errors.append(f"{label}.layerScores.{key} must be a number from 0 to 1")


def validate_feature_review_targets(
    spec: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> None:
    targets = spec.get("featureReviewTargets")
    policy = feature_review_policy(spec)
    if targets is None:
        if policy.get("enabled") is True:
            errors.append("featureReviewTargets must be an array when feature review is enabled")
        else:
            warnings.append("quality: featureReviewTargets is missing; feature-level visual gating is disabled")
        return
    if not isinstance(targets, list):
        errors.append("featureReviewTargets must be an array")
        return
    if not targets:
        warnings.append("quality: featureReviewTargets is empty; component-level visual gaps can hide in the overall score")
        return
    ids: set[str] = set()
    critical_by_pass: dict[str, int] = {}
    important_by_pass: dict[str, int] = {}
    for index, target in enumerate(targets):
        label = f"featureReviewTargets[{index}]"
        if not isinstance(target, dict):
            errors.append(f"{label} must be an object")
            continue
        target_id = target.get("id")
        if not isinstance(target_id, str) or not target_id.strip():
            errors.append(f"{label}.id is required")
        elif target_id in ids:
            errors.append(f"duplicate feature review target id {target_id!r}")
        else:
            ids.add(target_id)
        if not isinstance(target.get("name"), str) or not target["name"].strip():
            errors.append(f"{label}.name is required")
        tier = target.get("tier")
        if tier not in {"critical", "important", "detail"}:
            errors.append(f"{label}.tier must be critical, important, or detail")
        validate_string_array(target.get("passIds"), f"{label}.passIds", errors)
        validate_string_array(target.get("componentRefs"), f"{label}.componentRefs", errors)
        validate_string_array(target.get("evidenceRefs"), f"{label}.evidenceRefs", errors)
        minimum = target.get("minimumScore")
        if minimum is not None:
            validate_unit_interval(minimum, f"{label}.minimumScore", errors)
        for field in ("mustPass",):
            value = target.get(field)
            if value is not None and not isinstance(value, bool):
                errors.append(f"{label}.{field} must be boolean")
        if tier == "critical" or target.get("mustPass") is True:
            pass_ids = target.get("passIds", [])
            if isinstance(pass_ids, list):
                for pass_id in pass_ids:
                    if isinstance(pass_id, str):
                        critical_by_pass[pass_id] = critical_by_pass.get(pass_id, 0) + 1
        elif tier == "important":
            pass_ids = target.get("passIds", [])
            if isinstance(pass_ids, list):
                for pass_id in pass_ids:
                    if isinstance(pass_id, str):
                        important_by_pass[pass_id] = important_by_pass.get(pass_id, 0) + 1
    maximum = policy.get("maxCriticalFeaturesPerPass", 5)
    if is_number(maximum):
        for pass_id, count in critical_by_pass.items():
            if count > int(maximum):
                errors.append(
                    f"pass {pass_id!r} has {count} critical feature targets; "
                    f"maximum is {int(maximum)}"
                )
    important_maximum = policy.get("maxImportantFeaturesPerPass", 3)
    if is_number(important_maximum):
        for pass_id, count in important_by_pass.items():
            if count > int(important_maximum):
                errors.append(
                    f"pass {pass_id!r} has {count} important feature targets; "
                    f"maximum is {int(important_maximum)}"
                )
    assessment = spec.get("preSpecAssessment")
    complexity = (
        assessment.get("complexity", {}).get("tier")
        if isinstance(assessment, dict) and isinstance(assessment.get("complexity"), dict)
        else None
    )
    starter_ids = {
        "overall-silhouette",
        "primary-structure",
        "reference-material-system",
    }
    if complexity in {"moderate", "complex", "ultra-complex"} and ids.issubset(starter_ids):
        warnings.append(
            "quality: replace generic starter featureReviewTargets with object-specific "
            "identity-defining semantic systems before strict validation"
        )


def validate_feature_reviews(
    entry: dict[str, Any],
    label: str,
    errors: list[str],
) -> None:
    reviews = entry.get("featureReviews")
    if reviews is None:
        return
    if not isinstance(reviews, list):
        errors.append(f"{label}.featureReviews must be an array")
        return
    ids: set[str] = set()
    for index, review in enumerate(reviews):
        item_label = f"{label}.featureReviews[{index}]"
        if not isinstance(review, dict):
            errors.append(f"{item_label} must be an object")
            continue
        feature_id = review.get("id")
        if not isinstance(feature_id, str) or not feature_id.strip():
            errors.append(f"{item_label}.id is required")
        elif feature_id in ids:
            errors.append(f"{label}.featureReviews has duplicate id {feature_id!r}")
        else:
            ids.add(feature_id)
        score = review.get("score")
        if score is not None:
            validate_unit_interval(score, f"{item_label}.score", errors)
        for field in ("notes",):
            value = review.get(field)
            if value is not None and not isinstance(value, str):
                errors.append(f"{item_label}.{field} must be a string")
        visible = review.get("visible")
        if visible is not None and not isinstance(visible, bool):
            errors.append(f"{item_label}.visible must be boolean")


def validate_review_history(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    history = spec.get("reviewHistory", [])
    if history is None:
        return
    if not isinstance(history, list):
        errors.append("reviewHistory must be an array")
        return
    for index, entry in enumerate(history):
        if not isinstance(entry, dict):
            errors.append(f"reviewHistory[{index}] must be an object")
            continue
        action = entry.get("action")
        if action is not None and action not in VALID_REVIEW_ACTIONS:
            errors.append(f"reviewHistory[{index}].action is invalid")
        fidelity = entry.get("estimatedFidelity")
        if fidelity is not None:
            validate_unit_interval(fidelity, f"reviewHistory[{index}].estimatedFidelity", errors)
        for field in ("matched", "mismatches", "specFixes", "codeFixes", "evidence"):
            validate_string_array(entry.get(field), f"reviewHistory[{index}].{field}", errors)
        visual = entry.get("visualEvidence")
        if visual is not None:
            validate_visual_evidence_item(visual, f"reviewHistory[{index}].visualEvidence", errors)
        validate_feature_reviews(entry, f"reviewHistory[{index}]", errors)
        pass_id = entry.get("passId")
        if (
            pass_id in VISUAL_PASS_IDS
            and action == "continue"
            and not (isinstance(visual, dict) and visual.get("renderScreenshot"))
        ):
            warnings.append(
                f"reviewHistory[{index}] continues visual pass {pass_id!r} without a render screenshot"
            )
        if pass_id in VISUAL_PASS_IDS and action == "continue":
            if not isinstance(visual, dict) or not visual.get("comparisonImage"):
                warnings.append(
                    f"quality: reviewHistory[{index}] continues visual pass {pass_id!r} without an AI vision comparison image"
                )
            score = entry.get("aiVisionScore")
            threshold = entry.get("visualAcceptanceThreshold", 0.7)
            if not is_number(score):
                warnings.append(
                    f"quality: reviewHistory[{index}] continues visual pass {pass_id!r} without aiVisionScore"
                )
            elif is_number(threshold) and float(score) < float(threshold):
                warnings.append(
                    f"quality: reviewHistory[{index}] aiVisionScore {score} is below threshold {threshold}"
                )
            loop = spec.get("selfCorrectLoop")
            acceptance = loop.get("visualAcceptance", {}) if isinstance(loop, dict) else {}
            if isinstance(acceptance, dict) and acceptance.get("layerScoresRequired") is True:
                layer_scores = entry.get("layerScores")
                if not isinstance(layer_scores, dict) or not layer_scores:
                    warnings.append(
                        f"quality: reviewHistory[{index}] continues visual pass {pass_id!r} without layerScores"
                    )
                else:
                    required_layers = acceptance.get("requiredLayerScores", [])
                    if isinstance(required_layers, list):
                        missing_layers = [
                            layer
                            for layer in required_layers
                            if isinstance(layer, str) and layer not in layer_scores
                        ]
                        if missing_layers:
                            warnings.append(
                                f"quality: reviewHistory[{index}] layerScores missing: "
                                + ", ".join(missing_layers)
                            )
            failures = feature_gate_failures(spec, entry, str(pass_id))
            for failure in failures:
                warnings.append(
                    f"quality: reviewHistory[{index}] feature gate failed: {failure}"
                )


def validate_visual_evidence_history(spec: dict[str, Any], errors: list[str]) -> None:
    visual_history = spec.get("visualEvidence", [])
    if visual_history is None:
        return
    if not isinstance(visual_history, list):
        errors.append("visualEvidence must be an array")
        return
    for index, item in enumerate(visual_history):
        validate_visual_evidence_item(item, f"visualEvidence[{index}]", errors)


def validate_build_passes(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> list[str]:
    build_passes = spec.get("buildPasses")
    if build_passes is None:
        warnings.append("quality: missing buildPasses; model construction can skip blockout/structural/material gates")
        return []
    if not isinstance(build_passes, list):
        errors.append("buildPasses must be an array")
        return []
    ids: list[str] = []
    for index, item in enumerate(build_passes):
        if not isinstance(item, dict):
            errors.append(f"buildPasses[{index}] must be an object")
            continue
        pass_id = item.get("id")
        if not isinstance(pass_id, str) or not pass_id.strip():
            errors.append(f"buildPasses[{index}].id is required")
            continue
        if pass_id in ids:
            errors.append(f"duplicate buildPasses id {pass_id!r}")
        ids.append(pass_id)
        for field in ("goal",):
            value = item.get(field)
            if value is not None and not isinstance(value, str):
                errors.append(f"buildPasses[{index}].{field} must be a string")
        validate_string_array(item.get("componentRefs"), f"buildPasses[{index}].componentRefs", errors)
        validate_string_array(item.get("acceptance"), f"buildPasses[{index}].acceptance", errors)
    if ids:
        if ids[0] != "blockout":
            warnings.append("quality: first build pass should be blockout")
        if "structural-pass" not in ids:
            warnings.append("quality: missing structural-pass; component hierarchy may be skipped")
        if not ({"material-pass", "surface-pass"} & set(ids)):
            warnings.append("quality: missing material/surface pass; model may stay as flat geometry")
    return ids


def review_completes_pass(
    spec: dict[str, Any],
    entry: dict[str, Any],
    pass_id: str,
) -> bool:
    if entry.get("passId") != pass_id or entry.get("action") != "continue":
        return False
    visual = entry.get("visualEvidence")
    if pass_id in VISUAL_PASS_IDS:
        if not (
            isinstance(visual, dict)
            and visual.get("renderScreenshot")
            and visual.get("comparisonImage")
        ):
            return False
        score = entry.get("aiVisionScore")
        threshold = entry.get("visualAcceptanceThreshold", 0.7)
        if not is_number(score) or not is_number(threshold) or float(score) < float(threshold):
            return False
        if feature_gate_failures(spec, entry, pass_id):
            return False
    return True


def completed_passes_from_history(spec: dict[str, Any], pass_ids: list[str]) -> list[str]:
    history = spec.get("reviewHistory", [])
    if not isinstance(history, list):
        return []
    completed: list[str] = []
    for pass_id in pass_ids:
        if any(
            isinstance(entry, dict) and review_completes_pass(spec, entry, pass_id)
            for entry in history
        ):
            completed.append(pass_id)
        else:
            break
    return completed


def validate_sculpt_pipeline(
    spec: dict[str, Any],
    build_pass_ids: list[str],
    errors: list[str],
    warnings: list[str],
) -> None:
    pipeline = spec.get("sculptPipeline")
    if pipeline is None:
        warnings.append("quality: missing sculptPipeline; pass order is not locked and generation can skip build passes")
        return
    if not isinstance(pipeline, dict):
        errors.append("sculptPipeline must be an object")
        return
    pass_order = pipeline.get("passOrder")
    if pass_order is None:
        warnings.append("quality: sculptPipeline.passOrder is missing")
        pass_order_ids = build_pass_ids
    else:
        validate_string_array(pass_order, "sculptPipeline.passOrder", errors)
        pass_order_ids = [str(value) for value in pass_order] if isinstance(pass_order, list) else build_pass_ids
    if build_pass_ids and pass_order_ids and pass_order_ids != build_pass_ids:
        warnings.append("sculptPipeline.passOrder differs from buildPasses order; sync the pipeline before generation")
    current = pipeline.get("currentPass")
    if current is not None and current != "complete" and current not in (pass_order_ids or build_pass_ids):
        errors.append("sculptPipeline.currentPass must be a known build pass or complete")
    completed = pipeline.get("completedPasses", [])
    validate_string_array(completed, "sculptPipeline.completedPasses", errors)
    if isinstance(completed, list):
        expected = completed_passes_from_history(spec, pass_order_ids or build_pass_ids)
        if list(completed) != expected:
            warnings.append("sculptPipeline.completedPasses is out of sync with reviewHistory; run stage3_build/orchestrate_passes.py sync")
        for pass_id in completed:
            if pass_id not in (pass_order_ids or build_pass_ids):
                errors.append(f"sculptPipeline.completedPasses contains unknown pass {pass_id!r}")
    gate_mode = pipeline.get("passGateMode")
    if gate_mode != "locked-sequential":
        warnings.append("quality: sculptPipeline.passGateMode should be locked-sequential")
    validate_string_array(pipeline.get("nextRequiredEvidence"), "sculptPipeline.nextRequiredEvidence", errors)


def has_non_empty_detail(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() not in {"none", "unassessed", "n/a"}
    if isinstance(value, list):
        return any(has_non_empty_detail(item) for item in value)
    if isinstance(value, dict):
        return any(has_non_empty_detail(item) for item in value.values())
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return abs(float(value)) > 0
    return False


def layer_number(value: Any, keys: tuple[str, ...]) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                return float(item)
    return 0.0


def reference_pbr_usable(material: dict[str, Any], threshold: float) -> tuple[bool, str]:
    reference = material.get("referencePbr")
    material_id = str(material.get("id") or "(unnamed)")
    if not isinstance(reference, dict):
        return False, f"material {material_id!r} needs usable referencePbr extracted from source pixels"
    if reference.get("usable") is not True:
        return False, f"material {material_id!r} referencePbr.usable must be true"
    confidence = reference.get("confidence", reference.get("estimatedFidelity"))
    if not is_number(confidence) or float(confidence) < threshold:
        return False, f"material {material_id!r} referencePbr confidence must be >= {threshold}"
    maps = reference.get("maps")
    if not isinstance(maps, dict):
        return False, f"material {material_id!r} referencePbr needs maps"
    for channel in ("albedo", "roughness", "height", "normal", "ao"):
        entry = maps.get(channel)
        if not isinstance(entry, dict) or not has_non_empty_detail(entry.get("url") or entry.get("path")):
            return False, f"material {material_id!r} referencePbr missing {channel} map path/url"
    return True, ""


def validate_look_dev_targets(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    targets = spec.get("lookDevTargets")
    if targets is None:
        warnings.append("quality: missing lookDevTargets; material/color/lighting passes may stay flat")
    elif not isinstance(targets, dict):
        errors.append("lookDevTargets must be an object")
    materials = [item for item in spec.get("materials", []) if isinstance(item, dict)]
    if materials:
        has_palette = any(
            has_non_empty_detail(item.get("colorVariation"))
            or has_non_empty_detail(item.get("albedo", {}).get("secondary") if isinstance(item.get("albedo"), dict) else None)
            for item in materials
        )
        has_response = any(
            layer_number(item.get("roughness"), ("variation", "base")) > 0
            or layer_number(item.get("normal"), ("strength", "amplitude")) > 0
            or layer_number(item.get("bump"), ("amplitude", "strength")) > 0
            or layer_number(item.get("displacement"), ("amplitude", "strength")) > 0
            for item in materials
        )
        has_locality = any(
            has_non_empty_detail(item.get("localOverrides"))
            or (
                isinstance(item.get("wear"), dict)
                and (
                    layer_number(item["wear"].get("edgeWear"), ("base", "amount")) > 0
                    or has_non_empty_detail(item["wear"].get("scratches"))
                    or has_non_empty_detail(item["wear"].get("chips"))
                )
            )
            or (
                isinstance(item.get("dirt"), dict)
                and (
                    layer_number(item["dirt"].get("amount"), ("base", "amount")) > 0
                    or layer_number(item["dirt"].get("cavityBias"), ("base", "amount")) > 0
                )
            )
            or has_non_empty_detail(item.get("moss"))
            or has_non_empty_detail(item.get("stains"))
            or has_non_empty_detail(item.get("scratches"))
            or has_non_empty_detail(item.get("chips"))
            or has_non_empty_detail(item.get("wetness"))
            or has_non_empty_detail(item.get("patina"))
            for item in materials
        )
        if not has_palette:
            warnings.append("quality: material-pass needs reference-derived albedo palette or secondary/accent color zones")
        if not has_response:
            warnings.append("quality: material-pass needs roughness variation or normal/bump/displacement response")
        if not has_locality:
            warnings.append("quality: material-pass needs local overrides, AO, dirt, wear, stains, moss, chips, scratches, or equivalent masks")
        quality_first = isinstance(targets, dict) and targets.get("qualityPriority") == "reference-fidelity"
        if quality_first:
            material_targets = targets.get("materialPass", {})
            if not isinstance(material_targets, dict):
                warnings.append("quality: quality-first lookDevTargets.materialPass must be an object")
                material_targets = {}
            minimum_resolution = material_targets.get("minimumTextureResolution", 1024)
            if not isinstance(minimum_resolution, int) or isinstance(minimum_resolution, bool):
                warnings.append("quality: minimumTextureResolution must be an integer")
                minimum_resolution = 1024
            required_channels = {
                str(item).lower()
                for item in material_targets.get("independentMapChannels", [])
                if isinstance(item, str)
            }
            extraction_targets = material_targets.get("referencePbrExtraction", {})
            if not isinstance(extraction_targets, dict):
                extraction_targets = {}
            pbr_required = (
                extraction_targets.get("requiredWhenSourceImagePresent") is True
                and has_non_empty_detail(spec.get("sourceImage"))
            )
            pbr_threshold = extraction_targets.get("targetThreshold", 0.7)
            if not is_number(pbr_threshold):
                pbr_threshold = 0.7
            expected_channels = {"albedo", "roughness", "height", "normal", "ambient-occlusion"}
            if not expected_channels.issubset(required_channels):
                warnings.append(
                    "quality: quality-first materialPass must require independent albedo, roughness, "
                    "height, normal, and ambient-occlusion channels"
                )
            for material in materials:
                if material.get("qualityTier") == "utility":
                    continue
                material_id = str(material.get("id") or "(unnamed)")
                resolution = material.get("textureResolution")
                if not isinstance(resolution, int) or isinstance(resolution, bool) or resolution < minimum_resolution:
                    warnings.append(
                        f"quality: material {material_id!r} textureResolution must be >= {minimum_resolution}"
                    )
                projection = material.get("textureProjection")
                if not isinstance(projection, dict) or not has_non_empty_detail(projection.get("mode")):
                    warnings.append(
                        f"quality: material {material_id!r} needs textureProjection.mode and texel-density intent"
                    )
                bands = material.get("surfaceFrequencyBands")
                band_ids = {
                    str(item.get("id")).lower()
                    for item in bands
                    if isinstance(item, dict) and has_non_empty_detail(item.get("id"))
                } if isinstance(bands, list) else set()
                missing_bands = {"macro", "meso", "micro"} - band_ids
                if missing_bands:
                    warnings.append(
                        f"quality: material {material_id!r} missing surface frequency bands: "
                        + ", ".join(sorted(missing_bands))
                    )
                roughness = material.get("roughness")
                roughness_map = roughness.get("map") if isinstance(roughness, dict) else None
                if not has_non_empty_detail(roughness_map) or "albedo" in str(roughness_map).lower():
                    warnings.append(f"quality: material {material_id!r} needs an independent roughness map")
                if not has_non_empty_detail(material.get("ambientOcclusion")):
                    warnings.append(
                        f"quality: material {material_id!r} needs an independent ambient-occlusion response"
                    )
                if pbr_required:
                    ok, message = reference_pbr_usable(material, float(pbr_threshold))
                    if not ok:
                        warnings.append(f"quality: {message}")
    lighting = spec.get("lightingFromPhoto", [])
    if not isinstance(lighting, list):
        errors.append("lightingFromPhoto must be an array")
    else:
        meaningful = [item for item in lighting if has_non_empty_detail(item)]
        if len(meaningful) < 3:
            warnings.append("quality: lighting-pass needs concrete key/fill/rim or environment light entries")
        lighting_text = " ".join(str(item).lower() for item in meaningful)
        if meaningful and not any(term in lighting_text for term in ("exposure", "tone", "aces", "filmic")):
            warnings.append("quality: lighting-pass needs exposure and tone mapping intent")
        if meaningful and not any(term in lighting_text for term in ("contact shadow", "ground shadow", "ambient occlusion", "ao")):
            warnings.append("quality: lighting-pass needs contact shadow or ground shadow behavior")


VALID_DETAIL_KINDS = {
    "gloss", "bevel", "fastener", "linework", "contour", "seam", "stitch",
    "stain", "scratch", "chip", "decal", "emissive", "hole", "groove", "ridge",
}


def _detail_link_keys(spec: dict[str, Any]) -> set[str]:
    """Collect keys a detailInventory item may map to: component ids, local feature ids,
    material ids, and material localOverride ids (with and without owner prefix)."""
    keys: set[str] = set()
    for comp in spec.get("componentTree", []):
        if not isinstance(comp, dict):
            continue
        cid = comp.get("id")
        if isinstance(cid, str):
            keys.add(cid)
        for feat in comp.get("localFeatures", []) or []:
            if isinstance(feat, str):
                keys.add(feat)
                if isinstance(cid, str):
                    keys.add(f"{cid}/{feat}")
            elif isinstance(feat, dict) and isinstance(feat.get("id"), str):
                keys.add(feat["id"])
                if isinstance(cid, str):
                    keys.add(f"{cid}/{feat['id']}")
    for mat in spec.get("materials", []):
        if not isinstance(mat, dict):
            continue
        mid = mat.get("id")
        if isinstance(mid, str):
            keys.add(mid)
        for over in mat.get("localOverrides", []) or []:
            if isinstance(over, dict) and isinstance(over.get("id"), str):
                keys.add(over["id"])
                if isinstance(mid, str):
                    keys.add(f"{mid}/{over['id']}")
    return keys


def _has_gloss_response(spec: dict[str, Any]) -> bool:
    for mat in spec.get("materials", []):
        if not isinstance(mat, dict):
            continue
        rough = mat.get("roughness")
        base = rough.get("base") if isinstance(rough, dict) else rough
        if is_number(base) and float(base) < 0.35:
            return True
        if is_number(mat.get("clearcoat")) or isinstance(mat.get("clearcoat"), dict):
            return True
        for over in mat.get("localOverrides", []) or []:
            if isinstance(over, dict) and is_number(over.get("roughness")) and float(over["roughness"]) < 0.3:
                return True
    return False


def _has_repetition_or_small_parts(spec: dict[str, Any]) -> bool:
    if [r for r in spec.get("repetitionSystems", []) if isinstance(r, dict)]:
        return True
    return any(
        isinstance(c, dict) and c.get("level") == "micro"
        for c in spec.get("componentTree", [])
    )


def validate_detail_inventory(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    """Gate the detail inventory. Backward compatible: only enforced when a detailInventory
    block with a positive targetMinDetails is present (new-pipeline specs)."""
    assessment = spec.get("preSpecAssessment")
    if not isinstance(assessment, dict):
        return
    inv = assessment.get("detailInventory")
    if not isinstance(inv, dict):
        return
    details = inv.get("details", [])
    if not isinstance(details, list):
        errors.append("preSpecAssessment.detailInventory.details must be an array")
        return
    target = inv.get("targetMinDetails", 0)
    if not (isinstance(target, int) and not isinstance(target, bool) and target > 0):
        return  # not a new-pipeline spec; skip enforcement
    if len(details) < target:
        warnings.append(
            f"quality: detailInventory has {len(details)} details but targetMinDetails is {target}; "
            "enumerate identity-defining details (gloss, bevel, fasteners, linework, stains) before code generation"
        )
    link_keys = _detail_link_keys(spec)
    has_gloss = has_fastener = False
    for index, detail in enumerate(details):
        if not isinstance(detail, dict):
            errors.append(f"detailInventory.details[{index}] must be an object")
            continue
        did = detail.get("id", index)
        kind = detail.get("kind")
        if kind not in VALID_DETAIL_KINDS:
            warnings.append(f"quality: detailInventory detail {did!r} has unknown kind {kind!r}")
        maps = detail.get("mapsTo")
        ref = maps.get("ref") if isinstance(maps, dict) else None
        if not (isinstance(ref, str) and ref in link_keys):
            warnings.append(
                f"quality: detailInventory detail {did!r} does not map to a component.localFeatures "
                "or material.localOverrides entry (no prose-only details)"
            )
        if kind == "gloss":
            has_gloss = True
        elif kind == "fastener":
            has_fastener = True
    if has_gloss and not _has_gloss_response(spec):
        warnings.append(
            "quality: detailInventory lists a gloss detail but no material provides low roughness or clearcoat response"
        )
    if has_fastener and not _has_repetition_or_small_parts(spec):
        warnings.append(
            "quality: detailInventory lists fastener details but no repetitionSystem/instancing or micro parts represent them"
        )


def validate_character_track(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    """Gate the character track. Backward compatible: only enforced when primaryDomain is
    character or hybrid."""
    assessment = spec.get("preSpecAssessment")
    if not isinstance(assessment, dict):
        return
    object_class = assessment.get("objectClass")
    domain = object_class.get("primaryDomain") if isinstance(object_class, dict) else None
    if domain not in {"character", "hybrid"}:
        return
    anatomy = assessment.get("anatomy")
    if not isinstance(anatomy, dict) or anatomy.get("applies") is not True:
        warnings.append(
            "quality: primaryDomain is character/hybrid but anatomy.applies is not true; "
            "fill anatomy (styleHeads, proportions, pose, faceLandmarks) from the reference"
        )
        return
    if not (is_number(anatomy.get("styleHeads")) and float(anatomy["styleHeads"]) > 0):
        warnings.append("quality: character anatomy.styleHeads must be greater than 0 (head-unit proportion)")
    proportions = anatomy.get("proportions")
    if not (isinstance(proportions, dict) and any(
        is_number(proportions.get(k)) and float(proportions[k]) > 0 for k in ("torso", "legs")
    )):
        warnings.append("quality: character anatomy.proportions must set torso/legs head-unit ratios")
    landmarks = anatomy.get("faceLandmarks")
    if not (isinstance(landmarks, dict) and any(
        is_number(landmarks.get(k)) and float(landmarks[k]) > 0 for k in ("eyeLine", "noseBase", "mouthLine")
    )):
        warnings.append("quality: character anatomy.faceLandmarks must set eyeLine/noseBase/mouthLine from the reference")
    targets = spec.get("featureReviewTargets", [])
    character_ids = {"anatomy-proportion", "face-landmark-placement", "pose-silhouette", "outfit-and-palette"}
    if not any(isinstance(t, dict) and t.get("id") in character_ids for t in targets):
        warnings.append(
            "quality: character track needs featureReviewTargets covering anatomy/face/pose/outfit "
            "(add anatomy-proportion, face-landmark-placement, pose-silhouette, outfit-and-palette)"
        )


def validate_spec(spec: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for key, expected_type in REQUIRED_TOP_LEVEL.items():
        if key not in spec:
            errors.append(f"missing top-level field {key!r}")
        elif not isinstance(spec[key], expected_type):
            errors.append(f"field {key!r} must be {expected_type.__name__}")
    suitability = spec.get("suitability")
    if suitability not in VALID_SUITABILITY:
        errors.append("suitability must be pass, conditional, or reject")
    validate_pre_spec_assessment(spec, errors, warnings)
    validate_terminology_profile(spec, errors, warnings)
    validate_score_block(spec, errors, warnings)
    validate_quality_targets(spec, errors, warnings)
    validate_quality_contract(spec, errors, warnings)
    validate_action_readiness(spec, errors, warnings)
    validate_self_correct_loop(spec, errors, warnings)
    validate_feature_review_targets(spec, errors, warnings)
    validate_review_history(spec, errors, warnings)
    validate_visual_evidence_history(spec, errors)
    build_pass_ids = validate_build_passes(spec, errors, warnings)
    validate_sculpt_pipeline(spec, build_pass_ids, errors, warnings)
    validate_look_dev_targets(spec, errors, warnings)
    evidence_ids = validate_evidence(spec, errors, warnings)
    material_ids = validate_materials(spec, errors, warnings)
    validate_components(spec, material_ids, evidence_ids, errors, warnings)
    lod_plan = spec.get("lodPlan")
    if lod_plan is not None and not isinstance(lod_plan, list):
        errors.append("lodPlan must be an array")
    performance = spec.get("performanceBudget")
    if performance is not None and not isinstance(performance, dict):
        errors.append("performanceBudget must be an object")
    validate_quality_depth(spec, errors, warnings)
    validate_detail_inventory(spec, errors, warnings)
    validate_character_track(spec, errors, warnings)
    if suitability == "pass" and spec.get("risks"):
        warnings.append("suitability is pass but risks are present; confirm they are acceptable")
    return errors, warnings


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--json", action="store_true", help="Print machine-readable result")
    parser.add_argument(
        "--strict-quality",
        action="store_true",
        help="Treat quality warnings as validation errors before implementation/generation",
    )
    args = parser.parse_args(argv)

    try:
        spec = load_spec(args.spec)
        errors, warnings = validate_spec(spec)
    except ValueError as exc:
        errors, warnings = [str(exc)], []

    if args.strict_quality:
        errors.extend(
            f"strict quality failure: {warning.removeprefix('quality: ').strip()}"
            for warning in warnings
            if warning.startswith("quality:")
        )

    ok = not errors
    result = {
        "ok": ok,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "targetName": spec.get("targetName") if "spec" in locals() else None,
            "suitability": spec.get("suitability") if "spec" in locals() else None,
            "components": len(spec.get("componentTree", [])) if "spec" in locals() else 0,
            "materials": len(spec.get("materials", [])) if "spec" in locals() else 0,
        },
    }
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("PASS" if ok else "FAIL")
        for warning in warnings:
            print(f"warning: {warning}")
        for error in errors:
            print(f"error: {error}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
