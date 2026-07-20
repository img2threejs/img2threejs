#!/usr/bin/env python3
"""Create a starter ObjectSculptSpec JSON file."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "object"


def make_pre_spec_assessment(target_name: str) -> dict:
    return {
        "objectClass": {
            "primaryType": "unassessed",
            "primaryDomain": "unassessed",
            "formLanguage": [],
            "structureKind": [],
            "motionPotential": [],
            "materialFamilies": [],
            "notes": "Fill from direct visual inspection before writing the final spec. Do not use fixed domain profiles. Set primaryDomain to object, character, or hybrid.",
        },
        "complexity": {
            "tier": "unassessed",
            "scores": {
                "silhouetteComplexity": 0,
                "componentCount": 0,
                "hierarchyDepth": 0,
                "repetitionDensity": 0,
                "materialLayerCount": 0,
                "localDetailDensity": 0,
                "occlusionRisk": 0,
                "actionReadinessNeed": 0,
            },
            "estimatedCounts": {
                "macroComponents": 1,
                "mesoComponents": 0,
                "microFeatureGroups": 0,
                "materialLayers": 1,
                "repetitionSystems": 0,
            },
            "reasoning": [
                f"Assess {target_name!r} from the image before finalizing componentTree/materials.",
            ],
        },
        "specDepthDecision": {
            "requiredDepth": "unassessed",
            "minimumComponentLevels": ["macro"],
            "needsRepetitionSystems": False,
            "needsMaterialLocalOverrides": False,
            "needsMultipleReviewViews": True,
            "needsActionReadyHierarchy": True,
            "rationale": "Choose simple/moderate/complex/ultra-complex from observed structure, not from a hardcoded domain.",
        },
        "unknownsToResolveBeforeImplementation": [],
        "detailInventory": {
            "scanMethod": "component-zones",
            "targetMinDetails": 0,
            "note": (
                "Enumerate every identity-defining small detail before authoring the spec. "
                "Each detail must map to a component.localFeatures entry or material.localOverrides entry, "
                "never prose only. Use forge/stage1_intake/build_detail_inventory.py to scan zones."
            ),
            "details": [],
        },
        "anatomy": {
            "applies": False,
            "styleHeads": 0.0,
            "proportions": {
                "headUnit": 0.0,
                "torso": 0.0,
                "legs": 0.0,
                "shoulderWidth": 0.0,
                "hipWidth": 0.0,
            },
            "pose": {"type": "unassessed", "jointAngles": {}},
            "faceLandmarks": {
                "eyeLine": 0.0,
                "eyeSpacing": 0.0,
                "noseBase": 0.0,
                "mouthLine": 0.0,
                "hairline": 0.0,
            },
            "features": [],
            "confidence": 0.0,
            "note": (
                "Only meaningful when objectClass.primaryDomain is character or hybrid. "
                "Set applies=true and fill from forge/stage1_intake/extract_landmarks.py. "
                "See grimoire/character/reconstruction.md and grimoire/character/likeness_maximization.md."
            ),
        },
    }


def make_quality_contract() -> dict:
    return {
        "qualityBar": "unassessed",
        "definitionOfDone": [
            "The rendered model matches the reference silhouette, primary proportions, visible component hierarchy, material response, and most recognizable local features for the selected fidelity tier.",
        ],
        "minimumSpecDepth": {
            "macroComponents": 1,
            "mesoComponents": 0,
            "microFeatureGroups": 0,
            "materialLayers": 1,
            "repetitionSystems": 0,
            "reviewViewpoints": 3,
        },
        "featureGroups": [
            {
                "id": "overall-silhouette",
                "name": "Overall silhouette and proportions",
                "required": True,
                "qualityCriteria": [
                    "Bounding shape, dominant curves, negative spaces, and scale relationships are explicitly described.",
                ],
                "evidenceRefs": ["full-object"],
                "failureModes": [
                    "model reads as a generic placeholder instead of the reference object",
                    "major proportions are guessed without evidence",
                ],
            },
            {
                "id": "primary-structure",
                "name": "Primary structure and hierarchy",
                "required": True,
                "qualityCriteria": [
                    "Major parts, joints, seams, contact points, and parent-child relationships are named before code generation.",
                ],
                "evidenceRefs": ["full-object"],
                "failureModes": [
                    "large visible parts are merged into one mesh",
                    "component hierarchy is too shallow for the observed complexity",
                ],
            },
            {
                "id": "attachment-joint-correctness",
                "name": "Attachment and joint correctness",
                "required": True,
                "qualityCriteria": [
                    "Every visible child appendage, branch, limb, handle, connector, tube, cable, horn, wing, leg, or hinged part has an attachment contract with parent socket, localStart/localEnd, contact type, embed/overlap, and gap tolerance.",
                ],
                "evidenceRefs": ["full-object"],
                "failureModes": [
                    "child part root floats away from the parent",
                    "branch/limb/tube is centered in space instead of pivoting from its root",
                    "parent-child transform mixes world and local coordinates",
                ],
            },
            {
                "id": "surface-material-response",
                "name": "Surface material response",
                "required": True,
                "qualityCriteria": [
                    "Albedo zones, roughness, normal/bump/displacement intent, cavity dirt, edge wear, and local overrides are specified where visible.",
                    "Important materials define independent albedo, roughness, height/normal, and AO responses instead of reusing one texture for unrelated PBR channels.",
                    "Surface response is decomposed into macro, meso, and micro frequency bands with scale and amplitude tied to object scale.",
                ],
                "evidenceRefs": ["full-object"],
                "failureModes": [
                    "surface looks like flat plastic",
                    "local material variation is missing or not tied to image evidence",
                ],
            },
            {
                "id": "reference-lookdev",
                "name": "Reference color, material, and lighting response",
                "required": True,
                "qualityCriteria": [
                    "Material-pass names the reference-derived albedo palette, roughness variation, tactile normal/bump/displacement response, and local masks.",
                    "When a source image is available, run reference PBR extraction and require confidence >= 0.7 before treating maps as implementation-ready.",
                    "Lighting-pass names key/fill/rim or environment light, exposure, tone mapping, background, and contact shadow behavior.",
                    "Neutral, grazing-angle, and reference-matched renders prove that surface relief survives relighting and is not painted into albedo.",
                ],
                "evidenceRefs": ["full-object"],
                "failureModes": [
                    "model has acceptable shape but reads as flat shaded or plastic",
                    "colors are a generic average instead of reference-observed local color zones",
                    "lighting is evenly ambient and cannot reproduce the source value range",
                ],
            },
        ],
        "visualDeltaChecks": [
            "silhouette and negative-space delta",
            "component hierarchy depth delta",
            "repetition density and distribution delta",
            "material albedo/roughness/normal response delta",
            "local feature placement and scale delta",
        ],
        "antiShallowSpecRules": [
            "Do not proceed to code if qualityContract.qualityBar is unassessed.",
            "Do not proceed to code if the spec only contains a root component for a moderate or complex object.",
            "Do not proceed to code if required featureGroups are not represented by componentTree, materials, or repetitionSystems.",
            "Do not proceed to code if visible local features are described only in prose and not attached to components/materials/evidenceRefs.",
            "Do not proceed past structural-pass if attached child parts lack attachment.parentSocket, localStart, localEnd, embedDepth/overlap, and gapTolerance.",
            "Do not pass material look-dev when albedo is reused as roughness, height, normal, or AO.",
            "Do not pass material look-dev without macro, meso, and micro surface frequency bands for close-up materials.",
            "Do not pass reference-fidelity material look-dev from a source image without usable referencePbr maps or an explicit documented limitation.",
            "Do not patch a spec with extracted PBR maps when extraction confidence is below the target threshold unless the user explicitly accepts lower fidelity.",
        ],
    }


def load_assessment(path: Path | None) -> dict | None:
    if path is None:
        return None
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("assessment must be a JSON object")
    return payload


def _cnode(cid, name, primitive, parent, position, scale,
           material="skin", role="body", level="meso", rotation=(0, 0, 0),
           importance=0.7, sockets=None, local_features=None, anim_role="static",
           pivot_mode="center", evidence=None):
    """Build a full schema-valid componentTree node with humanoid-friendly defaults."""
    return {
        "id": cid, "name": name, "level": level, "role": role,
        "importance": importance, "confidence": 0.8, "primitive": primitive,
        "geometryDescriptor": {
            "topologyIntent": "stylized character part",
            "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1},
            "deformationStack": [], "uvStrategy": "generated procedural coordinates",
            "normalStrategy": "smooth vertex normals",
        },
        "parent": parent, "attachment": None,
        "dimensions": {"width": float(scale[0]), "height": float(scale[1]),
                       "depth": float(scale[2]), "units": "relative", "confidence": 0.8},
        "transform": {"position": list(position), "rotation": list(rotation), "scale": list(scale)},
        "actionProfile": {
            "animationRole": anim_role,
            "pivot": {"mode": pivot_mode, "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7},
            "transformChannels": {"translate": True, "rotate": True, "scale": True,
                                  "bend": False, "twist": False, "detach": False,
                                  "visibility": True, "materialState": False},
            "sockets": sockets or [],
            "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1],
                         "isTrigger": False, "notes": "box proxy"},
            "constraints": [],
            "destruction": {"breakable": False, "fractureGroup": cid, "seamRefs": [],
                            "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": material},
        },
        "material": material, "materialLayers": [material], "deformations": [], "joints": [],
        "seams": [], "localFeatures": local_features or [],
        "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0,
                          "normalPattern": "", "displacementPattern": "", "occlusionPattern": "",
                          "edgeWearPattern": "", "notes": ""},
        "evidenceRefs": evidence or ["full-object"], "details": [], "fidelityTier": "blockout",
    }


def make_character_component_tree(anatomy: dict | None = None) -> list:
    """A stylized humanoid bust template (head/neck/torso/arms + hair, glasses, headphones,
    face features). Head-unit driven; HU ~= 0.28 world units.

    The generator nests children under a parent node whose transform (incl. scale) cascades,
    so non-uniform parent scale would distort descendants. To avoid that, every visible part is
    authored with a logical parent + local offset, then FLATTENED to world space and parented to
    a hidden, unit-scaled root. Parents used for offsets (torso, head) are unrotated, so summing
    offsets is exact. Parts use primitives the generator already supports."""
    hu = 0.28
    # (id, name, primitive, logical_parent, offset, scale, material, role, level, rotation, importance, features)
    parts = [
        ("torso", "Torso (shirt)", "capsule", "root", (0, 0.55 * hu, 0), (2.4 * hu, 2.2 * hu, 1.5 * hu), "shirt", "shell", "macro", (0, 0, 0), 1.0, []),
        ("shirt-decal", "Chest graphic (Orioles)", "plane-card", "torso", (0, 0.1 * hu, 0.78 * hu), (1.5 * hu, 0.9 * hu, 1.0), "shirt-decal", "decal", "micro", (0, 0, 0), 0.7, ["cursive orange team wordmark with white outline"]),
        ("neck", "Neck", "cylinder", "root", (0, 1.65 * hu, 0), (0.55 * hu, 0.7 * hu, 0.55 * hu), "skin", "support", "meso", (0, 0, 0), 0.6, []),
        ("head", "Head", "ellipsoid", "root", (0, 2.5 * hu, 0.02 * hu), (0.92 * hu, 1.12 * hu, 0.98 * hu), "skin", "body", "macro", (0, 0, 0), 1.0, []),
        ("hair", "Hair (side-swept)", "ellipsoid", "head", (0, 0.28 * hu, -0.04 * hu), (1.06 * hu, 0.82 * hu, 1.08 * hu), "hair", "hair", "meso", (0, 0, 0), 0.9, ["short sides, longer swept-back top"]),
        ("hair-front", "Hair front mass", "ellipsoid", "head", (0.12 * hu, 0.34 * hu, 0.34 * hu), (0.7 * hu, 0.5 * hu, 0.6 * hu), "hair", "hair", "micro", (0, 0, 0), 0.6, []),
        ("brow-l", "Eyebrow L", "box", "head", (0.2 * hu, 0.12 * hu, 0.46 * hu), (0.22 * hu, 0.04 * hu, 0.06 * hu), "hair", "detail", "micro", (0, 0, 0), 0.4, []),
        ("brow-r", "Eyebrow R", "box", "head", (-0.2 * hu, 0.12 * hu, 0.46 * hu), (0.22 * hu, 0.04 * hu, 0.06 * hu), "hair", "detail", "micro", (0, 0, 0), 0.4, []),
        ("nose", "Nose", "cone", "head", (0, -0.04 * hu, 0.5 * hu), (0.14 * hu, 0.28 * hu, 0.18 * hu), "skin", "detail", "micro", (1.4, 0, 0), 0.4, []),
        ("mouth", "Mouth", "box", "head", (0, -0.34 * hu, 0.46 * hu), (0.24 * hu, 0.04 * hu, 0.05 * hu), "lips", "detail", "micro", (0, 0, 0), 0.4, []),
        ("glasses-frame-l", "Glasses frame L", "torus", "head", (0.21 * hu, 0.02 * hu, 0.48 * hu), (0.26 * hu, 0.22 * hu, 0.08 * hu), "glasses-frame", "connector", "meso", (0, 0, 0), 0.85, []),
        ("glasses-frame-r", "Glasses frame R", "torus", "head", (-0.21 * hu, 0.02 * hu, 0.48 * hu), (0.26 * hu, 0.22 * hu, 0.08 * hu), "glasses-frame", "connector", "meso", (0, 0, 0), 0.85, []),
        ("glasses-bridge", "Glasses bridge", "box", "head", (0, 0.04 * hu, 0.5 * hu), (0.12 * hu, 0.04 * hu, 0.04 * hu), "glasses-frame", "connector", "micro", (0, 0, 0), 0.5, []),
        ("lens-l", "Lens L", "plane-card", "head", (0.21 * hu, 0.02 * hu, 0.485 * hu), (0.22 * hu, 0.18 * hu, 1.0), "glasses-lens", "panel", "micro", (0, 0, 0), 0.5, []),
        ("lens-r", "Lens R", "plane-card", "head", (-0.21 * hu, 0.02 * hu, 0.485 * hu), (0.22 * hu, 0.18 * hu, 1.0), "glasses-lens", "panel", "micro", (0, 0, 0), 0.5, []),
        ("hp-band", "Headphone band", "torus", "root", (0, 1.78 * hu, 0.05 * hu), (0.95 * hu, 0.62 * hu, 0.7 * hu), "headphone", "ring", "meso", (1.2, 0, 0), 0.85, []),
        ("hp-cup-l", "Ear cup L", "cylinder", "root", (0.5 * hu, 1.52 * hu, 0.35 * hu), (0.42 * hu, 0.28 * hu, 0.42 * hu), "headphone", "detail", "meso", (0, 0, 1.57), 0.7, []),
        ("hp-cup-r", "Ear cup R", "cylinder", "root", (-0.5 * hu, 1.52 * hu, 0.35 * hu), (0.42 * hu, 0.28 * hu, 0.42 * hu), "headphone", "detail", "meso", (0, 0, 1.57), 0.7, []),
        ("arm-l", "Upper arm L", "capsule", "torso", (1.15 * hu, -0.35 * hu, 0.1 * hu), (0.55 * hu, 1.5 * hu, 0.55 * hu), "shirt", "arm", "meso", (0, 0, 0.25), 0.7, []),
        ("arm-r", "Upper arm R", "capsule", "torso", (-1.15 * hu, -0.35 * hu, 0.1 * hu), (0.55 * hu, 1.5 * hu, 0.55 * hu), "shirt", "arm", "meso", (0, 0, -0.25), 0.7, []),
    ]
    offsets = {"root": (0.0, 0.0, 0.0)}
    for pid, _n, _p, parent, off, *_rest in parts:
        offsets[pid] = off  # local offset; world resolved below

    def world_pos(pid, parent, off):
        x, y, z = off
        cur = parent
        # walk up the logical parent chain (parents are unrotated), summing offsets
        while cur and cur != "root":
            po = offsets.get(cur, (0.0, 0.0, 0.0))
            x += po[0]; y += po[1]; z += po[2]
            cur = parent_of.get(cur, "root")
        return (x, y, z)

    parent_of = {p[0]: p[3] for p in parts}
    tree = [_cnode("root", "Character (root)", "box", None, (0, 0, 0), (1, 1, 1),
                   material="hidden", role="body", level="macro", importance=1.0, anim_role="root")]
    for pid, name, prim, parent, off, scale, mat, role, level, rot, imp, feats in parts:
        tree.append(_cnode(pid, name, prim, "root", world_pos(pid, parent, off), scale,
                           material=mat, role=role, level=level, rotation=rot, importance=imp,
                           local_features=feats))
    return tree


def _shade_hex(hex_color: str, factor: float) -> str:
    """Return a slightly darker/lighter shade of a #RRGGBB color (factor<1 darker)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (max(0, min(255, round(c * factor))) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


CHARACTER_MATERIALS = [
    {"id": "hidden", "baseColor": "#000000", "roughness": {"base": 1.0, "variation": 0.0}, "opacity": {"base": 0.0}},
    {"id": "skin", "baseColor": "#e8b98f", "roughness": {"base": 0.55, "variation": 0.08}},
    {"id": "hair", "baseColor": "#171310", "roughness": {"base": 0.42, "variation": 0.1}},
    {"id": "shirt", "baseColor": "#20202a", "roughness": {"base": 0.85, "variation": 0.12}},
    {"id": "shirt-decal", "baseColor": "#d24a20", "roughness": {"base": 0.7, "variation": 0.05}},
    {"id": "glasses-frame", "baseColor": "#111114", "roughness": {"base": 0.35, "variation": 0.05}},
    {"id": "glasses-lens", "baseColor": "#a9c6d8", "roughness": {"base": 0.08, "variation": 0.02}},
    {"id": "headphone", "baseColor": "#0e0e10", "roughness": {"base": 0.5, "variation": 0.08}},
    {"id": "lips", "baseColor": "#c98070", "roughness": {"base": 0.5, "variation": 0.05}},
]


def make_character_build_passes() -> list:
    base = make_pre_spec_assessment  # noqa: reference to keep import graph obvious
    passes = [
        {"id": "blockout", "goal": "Match head-unit proportions and pose silhouette.",
         "componentRefs": ["root"], "acceptance": ["Bust proportions and 3/4 pose read correctly without materials."]},
        {"id": "proportion-lock", "goal": "Lock head/torso/limb head-unit ratios and pose angles.",
         "componentRefs": ["root", "head", "torso"], "acceptance": ["Head-unit ratios match anatomy; silhouette matches reference."]},
        {"id": "feature-placement", "goal": "Place facial features, hair, glasses, headphones to landmarks.",
         "componentRefs": ["head", "hair", "glasses-frame-l", "hp-band"],
         "acceptance": ["Eyeline/nose/mouth on landmark lines; glasses and headphones placed as in reference."]},
        {"id": "material-pass", "goal": "Match skin/hair/cloth/metal color and roughness.",
         "componentRefs": ["root"], "acceptance": ["Skin, hair, shirt, decal, glasses, headphone materials match reference palette."]},
        {"id": "lighting-pass", "goal": "Soft key from reference direction plus rim.",
         "componentRefs": ["root"], "acceptance": ["Readable under neutral light; reference-matched lighting added."]},
        {"id": "interaction-pass", "goal": "Rig-ready pivots and sockets.",
         "componentRefs": ["root"], "acceptance": ["Head/neck/arm pivots and face socket exposed."]},
        {"id": "optimization-pass", "goal": "Protect runtime performance.",
         "componentRefs": ["root"], "acceptance": ["Triangle/draw-call budget documented."]},
    ]
    del base
    return passes


def make_character_feature_targets() -> list:
    return [
        {"id": "anatomy-proportion", "name": "Head-unit proportions and pose", "tier": "critical",
         "passIds": ["blockout", "proportion-lock"], "minimumScore": 0.78, "mustPass": True,
         "componentRefs": ["root", "head", "torso"], "evidenceRefs": ["full-object"]},
        {"id": "face-landmark-placement", "name": "Face landmarks + glasses placement", "tier": "critical",
         "passIds": ["feature-placement"], "minimumScore": 0.75, "mustPass": True,
         "componentRefs": ["head", "glasses-frame-l"], "evidenceRefs": ["full-object"]},
        {"id": "pose-silhouette", "name": "Pose and bust silhouette", "tier": "critical",
         "passIds": ["blockout", "proportion-lock"], "minimumScore": 0.75, "mustPass": True,
         "componentRefs": ["root", "arm-l"], "evidenceRefs": ["full-object"]},
        {"id": "outfit-and-palette", "name": "Outfit + accessories + palette", "tier": "important",
         "passIds": ["material-pass"], "minimumScore": 0.7, "mustPass": False,
         "componentRefs": ["shirt-decal", "headphone", "glasses-frame-l"], "evidenceRefs": ["full-object"]},
    ]


def apply_character_template(spec: dict, anatomy: dict | None = None) -> dict:
    """Swap in the humanoid componentTree, character materials, build passes, and feature
    targets. Object specs are untouched; only called when primaryDomain is character/hybrid."""
    spec["componentTree"] = make_character_component_tree(anatomy)
    existing = {m.get("id"): m for m in spec.get("materials", []) if isinstance(m, dict)}
    for mat in CHARACTER_MATERIALS:
        merged = dict(existing.get("base", {}))
        merged.update(mat)
        merged.setdefault("name", mat["id"])
        merged.setdefault("type", "standard")
        # the generator colours meshes from `color`/`albedo`, so keep them in sync with baseColor
        base_color = mat.get("baseColor")
        if base_color:
            shade = _shade_hex(base_color, 0.82)
            merged["color"] = base_color
            # the generator only honours a palette with >= 2 entries (else it blends in beige
            # fallback tones), so provide two near-identical shades of the intended colour.
            merged["albedo"] = {"dominant": base_color, "secondary": [shade]}
            merged["colorVariation"] = {"palette": [base_color, shade], "pattern": "flat",
                                         "amplitude": 0.05, "heightCorrelation": 0.0}
        existing[mat["id"]] = merged
    spec["materials"] = list(existing.values())
    spec["buildPasses"] = make_character_build_passes()
    # A humanoid is reviewed as a whole each pass, so every pass renders all parts
    # (unlike the object pipeline where passes add parts incrementally).
    all_ids = [c["id"] for c in spec["componentTree"] if isinstance(c, dict) and c.get("id")]
    for build_pass in spec["buildPasses"]:
        build_pass["componentRefs"] = all_ids
    spec["featureReviewTargets"] = make_character_feature_targets()
    pipeline = spec.setdefault("sculptPipeline", {})
    pipeline["passOrder"] = [p["id"] for p in spec["buildPasses"]]
    pipeline["currentPass"] = "blockout"
    return spec


def make_spec(target_name: str, image: str | None, assessment_payload: dict | None = None) -> dict:
    target_id = slugify(target_name)
    pre_spec_assessment = make_pre_spec_assessment(target_name)
    quality_contract = make_quality_contract()
    if assessment_payload:
        incoming_assessment = assessment_payload.get("preSpecAssessment")
        incoming_contract = assessment_payload.get("qualityContract")
        if isinstance(incoming_assessment, dict):
            pre_spec_assessment = incoming_assessment
        if isinstance(incoming_contract, dict):
            quality_contract = incoming_contract
    return {
        "targetName": target_name,
        "targetId": target_id,
        "schemaVersion": "2.0",
        "terminologyProfile": {
            "domain": "real-time procedural Three.js asset",
            "geometryTerms": [
                "silhouette",
                "topology",
                "primitive",
                "bevel",
                "chamfer",
                "taper",
                "bend",
                "boolean cut",
                "edge loop",
                "surface normal",
                "displacement",
            ],
            "materialTerms": [
                "albedo",
                "baseColor",
                "roughness",
                "metalness",
                "normal map",
                "bump map",
                "ambient occlusion",
                "cavity dirt",
                "edge wear",
                "clearcoat",
            ],
            "lightingTerms": [
                "key light",
                "fill light",
                "rim light",
                "HDRI/environment reflection",
                "contact shadow",
            ],
            "descriptionRule": "Use measurable 3D graphics terms. Avoid vague words unless they are paired with concrete geometry/material/shader parameters.",
        },
        "sourceImage": image or "",
        "referenceCamera": {
            "solved": False,
            "fovDegrees": 40.0,
            "aspect": 1.0,
            "orientation": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0},
            "positionHint": [0.0, 0.0, 3.0],
            "note": (
                "For likeness work, solve the reference camera (forge/stage1_intake/solve_camera_pose.py) so the "
                "review render aligns with the photo and the reference can be projected. Confirm by overlay review."
            ),
        },
        "suitability": "conditional",
        "scores": {
            "object_isolation": 0,
            "silhouette_readability": 0,
            "depth_inference": 0,
            "primitive_decomposition": 0,
            "material_procedurality": 0,
            "occlusion_risk": 0,
            "interaction_fit": 0,
        },
        "preSpecAssessment": pre_spec_assessment,
        "qualityContract": quality_contract,
        "qualityTargets": {
            "targetFidelity": 0.7,
            "mustMatch": [
                "macro silhouette and proportions",
                "primary material albedo/roughness response",
                "reference-derived PBR material response at or above 0.7 confidence when source pixels are usable",
                "most recognizable local features",
            ],
            "niceToHave": [
                "micro scratches, stains, chips, and dirt masks",
                "secondary lighting match",
            ],
            "fpsTarget": 60,
            "reviewViewpoints": ["front", "three-quarter", "side"],
        },
        "selfCorrectLoop": {
            "enabled": True,
            "visualAcceptance": {
                "reviewer": "ai-vision",
                "threshold": 0.7,
                "comparisonArtifactRequired": True,
                "layerScoresRequired": True,
                "codePixelDiffIsAcceptanceAuthority": False,
                "scoringRule": "AI vision must inspect a side-by-side reference/render sheet and score the current pass from 0 to 1. Pixel-diff code may assist diagnostics but cannot approve a pass.",
                "requiredLayerScores": [
                    "silhouetteProportion",
                    "componentStructure",
                    "formDetail",
                    "materialSurface",
                    "lightingCamera",
                ],
                "featureReviewPolicy": {
                    "enabled": True,
                    "reviewUnit": "semantic-subsystem",
                    "maxCriticalFeaturesPerPass": 5,
                    "maxImportantFeaturesPerPass": 3,
                    "criticalDefaultThreshold": 0.8,
                    "importantAverageThreshold": 0.65,
                    "adaptiveEscalation": True,
                    "singleImagePairOnly": True,
                    "selectionRule": "Choose only the most visually salient, identity-defining, user-prioritized, or high-risk semantic systems. Group repeated parts instead of reviewing every mesh. AI vision scores every selected feature from the same full reference/render pair.",
                },
            },
            "reviewAfterPasses": [
                "blockout",
                "structural-pass",
                "form-refinement",
                "material-pass",
                "surface-pass",
                "lighting-pass",
                "interaction-pass",
                "optimization-pass",
            ],
            "allowedActions": [
                "continue",
                "refine-spec",
                "refine-code",
                "request-input",
                "stop",
            ],
            "specRefineTriggers": [
                "missing component",
                "wrong primitive family",
                "wrong proportions",
                "material layer under-specified",
                "local feature not traceable to viewEvidence",
                "reference ambiguity discovered during implementation",
            ],
            "codeRefineTriggers": [
                "spec is adequate but generated geometry/material does not match",
                "browser render differs from reference",
                "performance budget exceeded",
                "lighting hides geometry or material response",
            ],
            "stopCriteria": [
                "target fidelity reached or user accepts current approximation",
                "remaining gaps require new reference images or manual art",
            ],
            "screenshotPolicy": {
                "requiredForPasses": [
                    "blockout",
                    "structural-pass",
                    "form-refinement",
                    "material-pass",
                    "surface-pass",
                    "lighting-pass",
                    "interaction-pass",
                ],
                "preferredCapture": "in-app-browser-screenshot",
                "fallbackCapture": "user-supplied-screenshot-path",
                "minimumEvidence": "Each visual pass needs a reference image, rendered screenshot, side-by-side comparison sheet, AI vision score, layer scores, and critique before choosing continue.",
                "reviewPairRule": "Compare the same camera/viewpoint whenever possible; do not judge a front reference against a random render angle.",
                "acceptanceAuthority": "AI vision review of the comparison sheet. Code-generated pixel similarity is not sufficient evidence.",
            },
        },
        "featureReviewTargets": [
            {
                "id": "overall-silhouette",
                "name": "Overall silhouette and proportion system",
                "tier": "critical",
                "passIds": ["blockout"],
                "minimumScore": 0.8,
                "mustPass": True,
                "componentRefs": ["root"],
                "evidenceRefs": ["full-object"],
            },
            {
                "id": "primary-structure",
                "name": "Primary identity-defining structure",
                "tier": "critical",
                "passIds": ["structural-pass", "form-refinement"],
                "minimumScore": 0.8,
                "mustPass": True,
                "componentRefs": ["root"],
                "evidenceRefs": ["full-object"],
            },
            {
                "id": "reference-material-system",
                "name": "Primary reference material and surface response",
                "tier": "critical",
                "passIds": ["material-pass", "surface-pass"],
                "minimumScore": 0.75,
                "mustPass": True,
                "componentRefs": ["root"],
                "evidenceRefs": ["full-object"],
            },
        ],
        "sculptPipeline": {
            "passGateMode": "locked-sequential",
            "passOrder": [
                "blockout",
                "structural-pass",
                "form-refinement",
                "material-pass",
                "surface-pass",
                "lighting-pass",
                "interaction-pass",
                "optimization-pass",
            ],
            "currentPass": "blockout",
            "completedPasses": [],
            "lastCompletedPass": "",
            "blockedReason": "blockout requires a browser screenshot and self-correction review before structural-pass unlocks",
            "nextRequiredEvidence": [
                "blockout browser render screenshot from your agent's browser/screenshot tool",
                "side-by-side reference/render comparison sheet",
                "AI vision score >= 0.7 with layer scores and mismatch critique",
                "critical semantic feature scores from the same image pair meeting their individual thresholds",
                "reviewHistory entry for blockout with action=continue",
            ],
        },
        "lookDevTargets": {
            "qualityPriority": "reference-fidelity",
            "materialPass": {
                "albedoPaletteRequired": True,
                "roughnessVariationRequired": True,
                "normalOrBumpRequired": True,
                "localOverridesRequired": True,
                "minimumTextureResolution": 1024,
                "preferredTextureResolution": 2048,
                "independentMapChannels": [
                    "albedo",
                    "roughness",
                    "height",
                    "normal",
                    "ambient-occlusion",
                ],
                "requiredSurfaceFrequencyBands": ["macro", "meso", "micro"],
                "geometryReliefRequiredWhenSilhouetteAffected": True,
                "referencePbrExtraction": {
                    "requiredWhenSourceImagePresent": True,
                    "targetThreshold": 0.7,
                    "stopOnLowConfidence": True,
                    "script": "forge/stage1_intake/extract_pbr_evidence.py",
                    "acceptedLimitation": "single-image extraction is reference-derived inference, not exact photogrammetry",
                },
                "mustAvoid": [
                    "single flat albedo per material",
                    "uniform roughness",
                    "albedo texture reused as roughness/height/normal/AO",
                    "single-frequency random noise",
                    "plastic-looking smooth bark, stone, cloth, foliage, or aged material",
                    "local color/detail described only in prose without material masks",
                    "claiming exact PBR recovery when confidence is below the target threshold",
                ],
            },
            "lightingPass": {
                "requiredTerms": [
                    "key light",
                    "fill light",
                    "rim or environment light",
                    "exposure",
                    "tone mapping",
                    "background",
                    "contact shadow",
                ],
                "mustAvoid": [
                    "ambient-only lighting",
                    "flat value range",
                    "missing contact shadow",
                    "reference lighting copied without separating material readability",
                ],
            },
            "screenshotReview": [
                "Compare albedo palette and local color zones.",
                "Compare roughness/normal/bump response under light.",
                "Compare cavity dirt, edge wear, stains, moss, scratches, or other local masks.",
                "Compare key/fill/rim structure, exposure, tone mapping, background, and contact shadows.",
                "Capture a neutral-light render to verify material readability without reference lighting.",
                "Capture a grazing-light close-up to expose flat normals, uniform roughness, tiling, and plastic highlights.",
                "Capture a reference-matched render from the same camera framing as the source.",
            ],
        },
        "actionReadiness": {
            "contract": "Every macro/meso component should be generated as a stable named Object3D pivot node with a mesh child, action metadata, optional sockets, collider proxy, and destruction metadata.",
            "defaultRigType": "action-ready-static-rig",
            "rootMotionNode": "root",
            "requiredComponentFields": [
                "id",
                "parent",
                "transform",
                "attachment for child appendages, connectors, limbs, tubes, handles, legs, horns, wings, branches, or cables",
                "actionProfile.animationRole",
                "actionProfile.pivot",
                "actionProfile.collider",
                "actionProfile.destruction",
            ],
            "transformChannels": [
                "translate",
                "rotate",
                "scale",
                "bend",
                "twist",
                "detach",
                "visibility",
                "material-state",
            ],
            "authoringRules": [
                "Do not collapse independently movable parts into one mesh.",
                "Put transforms on component pivot groups, not only on raw meshes.",
                "For attached child parts, put the pivot at the semantic root/socket and build visible geometry from localStart to localEnd.",
                "Represent hinge, socket, detachable, and breakable intent even when no animation is implemented yet.",
                "Use simplified collider proxies for runtime physics instead of visual mesh colliders by default.",
            ],
            "destructionPolicy": {
                "defaultBreakable": False,
                "fractureGroupNaming": "Use stable semantic names such as body-shell, left-hinge, glass-panel, branch-segment.",
                "debrisStrategy": "Prefer detachable component groups and a small number of procedural fragments over random mesh explosion.",
            },
        },
        "assumptions": [],
        "coordinateFrame": {
            "front": "camera-facing side in the reference image",
            "up": "image up direction",
            "scaleReference": "unit scale; adjust after first browser render",
        },
        "silhouette": {
            "boundingShape": "",
            "aspectRatios": [],
            "symmetry": "",
            "dominantCurves": [],
            "negativeSpaces": [],
            "landmarks": [],
        },
        "viewEvidence": [
            {
                "id": "full-object",
                "view": "primary",
                "imageRegion": {
                    "x": 0.0,
                    "y": 0.0,
                    "width": 1.0,
                    "height": 1.0,
                    "units": "normalized",
                },
                "observations": [],
                "confidence": 0.5,
            }
        ],
        "componentTree": [
            {
                "id": "root",
                "name": target_name,
                "level": "macro",
                "role": "body",
                "importance": 1.0,
                "confidence": 0.5,
                "primitive": "box",
                "geometryDescriptor": {
                    "topologyIntent": "low-poly blockout with bevel-ready edges",
                    "edgeTreatment": {
                        "type": "none",
                        "bevelRadius": 0.0,
                        "segments": 1,
                    },
                    "deformationStack": [],
                    "uvStrategy": "generated procedural coordinates",
                    "normalStrategy": "vertex normals from generated geometry",
                },
                "parent": None,
                "attachment": None,
                "dimensions": {
                    "width": 1.0,
                    "height": 1.0,
                    "depth": 1.0,
                    "units": "relative",
                    "confidence": 0.5,
                },
                "transform": {
                    "position": [0, 0, 0],
                    "rotation": [0, 0, 0],
                    "scale": [1, 1, 1],
                },
                "actionProfile": {
                    "animationRole": "root",
                    "pivot": {
                        "mode": "center",
                        "localPosition": [0, 0, 0],
                        "axis": [0, 1, 0],
                        "confidence": 0.5,
                    },
                    "transformChannels": {
                        "translate": True,
                        "rotate": True,
                        "scale": True,
                        "bend": False,
                        "twist": False,
                        "detach": False,
                        "visibility": True,
                        "materialState": True,
                    },
                    "sockets": [],
                    "collider": {
                        "type": "box",
                        "offset": [0, 0, 0],
                        "scale": [1, 1, 1],
                        "isTrigger": False,
                        "notes": "Replace with sphere/capsule/compound proxy when the object shape demands it.",
                    },
                    "constraints": [],
                    "destruction": {
                        "breakable": False,
                        "fractureGroup": "root",
                        "seamRefs": [],
                        "detachableFragments": [],
                        "breakImpulse": 0.0,
                        "debrisMaterial": "base",
                    },
                },
                "material": "base",
                "materialLayers": ["base"],
                "deformations": [],
                "joints": [],
                "seams": [],
                "localFeatures": [],
                "surfaceDetail": {
                    "macroRoughness": 0.0,
                    "microRoughness": 0.0,
                    "bumpAmplitude": 0.0,
                    "normalPattern": "",
                    "displacementPattern": "",
                    "occlusionPattern": "",
                    "edgeWearPattern": "",
                    "notes": "",
                },
                "evidenceRefs": ["full-object"],
                "details": [],
                "fidelityTier": "blockout",
            }
        ],
        "materials": [
            {
                "id": "base",
                "name": "Base material",
                "type": "standard",
                "shaderModel": "MeshStandardMaterial / PBR approximation",
                "baseColor": "#8A7A5F",
                "color": "#8A7A5F",
                "albedo": {
                    "dominant": "#8A7A5F",
                    "secondary": ["#6E614B", "#A08F70"],
                    "samplingNotes": "Use image-observed local color zones, not a single averaged color.",
                },
                "colorVariation": {
                    "palette": ["#8A7A5F", "#6E614B", "#A08F70"],
                    "pattern": "mottled",
                    "amplitude": 0.15,
                    "heightCorrelation": 0.3,
                },
                "textureResolution": 1024,
                "textureProjection": {
                    "mode": "uv",
                    "repeat": [2.0, 2.0],
                    "anisotropy": 8,
                    "texelDensityIntent": "Preserve stable world/object-scale detail; do not stretch micro detail with component scale.",
                },
                "surfaceFrequencyBands": [
                    {
                        "id": "macro",
                        "frequency": 2.0,
                        "amplitude": 0.42,
                        "role": "broad color and height breakup",
                    },
                    {
                        "id": "meso",
                        "frequency": 12.0,
                        "amplitude": 0.22,
                        "role": "ridges, pores, grain, dents, or equivalent visible relief",
                    },
                    {
                        "id": "micro",
                        "frequency": 56.0,
                        "amplitude": 0.08,
                        "role": "highlight breakup visible under grazing light",
                    },
                ],
                "roughness": {
                    "base": 0.75,
                    "variation": 0.15,
                    "map": "independent-procedural-field",
                    "localResponse": "higher roughness in cavities, lower roughness on worn edges",
                },
                "metalness": {
                    "base": 0.0,
                    "variation": 0.0,
                },
                "normal": {
                    "pattern": "derived-from-independent-height-field",
                    "strength": 0.35,
                    "scale": 24.0,
                    "space": "tangent",
                },
                "bump": {
                    "pattern": "none",
                    "amplitude": 0.0,
                    "scale": 1.0,
                },
                "displacement": {
                    "pattern": "none",
                    "amplitude": 0.0,
                    "scale": 1.0,
                    "silhouetteAffects": False,
                },
                "ambientOcclusion": {
                    "cavityStrength": 0.25,
                    "contactShadowBias": 0.35,
                    "notes": "Darken creases, seams, intersections, and recessed local features.",
                },
                "wear": {
                    "edgeWear": 0.0,
                    "scratches": [],
                    "chips": [],
                },
                "dirt": {
                    "amount": 0.0,
                    "cavityBias": 0.0,
                    "color": "#2F2A22",
                },
                "localOverrides": [],
                "shaderNotes": [
                    "Prefer MeshPhysicalMaterial when clearcoat, sheen, transmission, or thin-surface response is observed; otherwise use MeshStandardMaterial-compatible PBR channels.",
                    "Generate albedo, roughness, height/normal, and AO independently; never alias albedo into roughness.",
                    "Use normal/bump/displacement only when they map to observed surface relief.",
                    "Use displacement geometry when the observed relief changes the close-up silhouette; texture-only relief is insufficient there.",
                ],
                "notes": "Replace with image-derived color, roughness, noise, and edge-wear notes.",
            }
        ],
        "repetitionSystems": [],
        "buildPasses": [
            {
                "id": "blockout",
                "goal": "Match macro silhouette and proportions.",
                "componentRefs": ["root"],
                "acceptance": [
                    "Silhouette reads correctly without materials.",
                    "Quality contract has named all required macro feature groups before code generation.",
                    "AI vision comparison score meets selfCorrectLoop.visualAcceptance.threshold.",
                ],
            },
            {
                "id": "structural-pass",
                "goal": "Build the component hierarchy implied by the pre-spec complexity assessment.",
                "componentRefs": ["root"],
                "acceptance": [
                    "Macro, meso, and repeated structures meet qualityContract.minimumSpecDepth.",
                    "Parent-child relations, joints, seams, sockets, and contact points are explicit.",
                    "Every attached child appendage/connector has parentSocket, localStart/localEnd, contactType, embedDepth or overlap, and gapTolerance.",
                    "AI vision comparison score meets selfCorrectLoop.visualAcceptance.threshold.",
                ],
            },
            {
                "id": "form-refinement",
                "goal": "Refine shape, deformation, bevels, tapers, curves, asymmetry, and visible local geometry.",
                "componentRefs": ["root"],
                "acceptance": [
                    "Important visible forms are represented in component geometryDescriptor, deformations, localFeatures, or repetitionSystems.",
                    "Endpoint-based child parts are rooted at their attachment sockets and do not visibly float away from parents.",
                    "AI vision comparison score meets selfCorrectLoop.visualAcceptance.threshold.",
                ],
            },
            {
                "id": "material-pass",
                "goal": "Match material color, roughness, bump, and local variation.",
                "componentRefs": ["root"],
                "acceptance": [
                    "Reference-derived albedo palette records dominant, secondary, and accent colors per visible material.",
                    "Each important material defines roughness variation and at least one normal/bump/displacement response.",
                    "Local material overrides, dirt/wear/stains/moss/chips/scratches or equivalent masks are tied to evidenceRefs.",
                    "Thin, transparent, reflective, wet, or fibrous materials document alpha/transmission/clearcoat/metalness/fiber response when relevant.",
                    "Generated preview uses procedural albedo/roughness/bump texture or vertex color variation instead of one flat color.",
                    "Generated preview uses independent PBR maps at 1024px or higher for the quality-first tier.",
                    "If source pixels are available, referencePbr extraction passed at confidence >= 0.7 or the pass is stopped/requesting better references.",
                    "Macro, meso, and micro surface frequency bands are visible at the intended review distance without obvious tiling.",
                    "AI vision comparison score meets selfCorrectLoop.visualAcceptance.threshold.",
                ],
            },
            {
                "id": "surface-pass",
                "goal": "Add procedural surface locality such as normal/bump/displacement, AO, dirt, stains, chips, grain, moss, scratches, and wear.",
                "componentRefs": ["root"],
                "acceptance": [
                    "Every required material feature group has local overrides or surfaceDetail tied to evidenceRefs.",
                    "A grazing-angle close-up proves that normal/height detail breaks highlights naturally and does not read as smooth plastic.",
                    "AI vision comparison score meets selfCorrectLoop.visualAcceptance.threshold.",
                ],
            },
            {
                "id": "lighting-pass",
                "goal": "Make material and form readable under neutral turntable lighting plus optional reference lighting.",
                "componentRefs": ["root"],
                "acceptance": [
                    "lightingFromPhoto identifies key light direction/color/intensity, fill light, rim or environment light, and ambient color.",
                    "Exposure, tone mapping, background color/gradient, shadow softness, and contact shadow behavior are specified.",
                    "Lighting does not hide geometry/material gaps and screenshots can be compared fairly to the reference.",
                    "Neutral, grazing, and reference-matched lighting checks distinguish material errors from lighting errors.",
                    "AI vision comparison score meets selfCorrectLoop.visualAcceptance.threshold.",
                ],
            },
            {
                "id": "interaction-pass",
                "goal": "Make the model ready for future animation, transformation, physics, or destruction.",
                "componentRefs": ["root"],
                "acceptance": [
                    "Macro and movable meso components have stable pivot nodes.",
                    "Sockets, collider proxies, and destruction metadata are present for future runtime actions.",
                    "AI vision comparison score meets selfCorrectLoop.visualAcceptance.threshold.",
                ],
            },
            {
                "id": "optimization-pass",
                "goal": "Protect runtime performance after visual fidelity is accepted.",
                "componentRefs": ["root"],
                "acceptance": [
                    "Triangle count, draw calls, instancing, LOD strategy, and FPS target are documented or verified.",
                    "Repeated detail is instanced or simplified where possible without breaking silhouette/material believability.",
                ],
            },
        ],
        "visualEvidence": [],
        "reviewHistory": [],
        "lodPlan": [
            {
                "tier": "near",
                "distance": 0,
                "strategy": "full component tree and material layers",
            },
            {
                "tier": "far",
                "distance": 30,
                "strategy": "merge static components and reduce local feature geometry",
            },
        ],
        "performanceBudget": {
            "qualityPriority": "reference-fidelity",
            "targetTriangles": 250000,
            "maxDrawCalls": 160,
            "textureSize": 2048,
            "fpsTarget": 30,
            "optimizationPolicy": "Reach accepted visual fidelity first, then optimize without removing reference-critical geometry or surface layers.",
        },
        "lightingFromPhoto": [],
        "proceduralStrategy": [
            "Block out macro silhouette first.",
            "Add component hierarchy and joints.",
            "Create stable pivot groups, sockets, collider proxies, and destruction metadata before visual polish.",
            "Refine forms with bevels, tapers, bends, and procedural noise.",
            "Run reference PBR extraction for important source-image materials and stop when confidence is below the target threshold.",
            "Add material variation before adding expensive micro-geometry.",
        ],
        "animationAnchors": [
            "root pivot node supports whole-object translation, rotation, scale, and visibility changes",
            "component pivot groups support later local transforms without rebuilding geometry",
        ],
        "destructionAnchors": [
            "actionProfile.destruction.fractureGroup marks detachable or breakable component sets",
            "component seams and sockets define plausible break points instead of random explosions",
        ],
        "risks": [],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_name", help="Human-readable object name")
    parser.add_argument("--image", help="Reference image path or URL")
    parser.add_argument("--assessment", type=Path, help="Pre-spec assessment JSON from stage2_spec/new_pre_spec_assessment.py")
    parser.add_argument("--out", type=Path, help="Output JSON path")
    parser.add_argument("--force", action="store_true", help="Overwrite output file")
    parser.add_argument("--character", action="store_true",
                        help="Use the humanoid character template (auto-enabled when the assessment primaryDomain is character/hybrid)")
    args = parser.parse_args(argv)

    assessment = load_assessment(args.assessment)
    spec = make_spec(args.target_name, args.image, assessment)
    domain = None
    if isinstance(assessment, dict):
        pre = assessment.get("preSpecAssessment", {})
        oc = pre.get("objectClass", {}) if isinstance(pre, dict) else {}
        domain = oc.get("primaryDomain") if isinstance(oc, dict) else None
    if args.character or domain in {"character", "hybrid"}:
        anatomy = None
        if isinstance(assessment, dict) and isinstance(assessment.get("preSpecAssessment"), dict):
            anatomy = assessment["preSpecAssessment"].get("anatomy")
        apply_character_template(spec, anatomy)
    payload = json.dumps(spec, indent=2, ensure_ascii=False) + "\n"

    if args.out:
        output = args.out.expanduser().resolve()
        if output.exists() and not args.force:
            parser.error(f"{output} already exists; use --force to overwrite")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
        print(output)
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
