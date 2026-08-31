#!/usr/bin/env python3
"""Create a starter ObjectSculptSpec JSON file."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
from spec_augmentation import SpecAugmentationError, merge_spec_augmentation
from pipeline_routing import resolve_pipeline_routing
from status_banner import emit_status


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
            "Do not place adjacent separate-geometry parts below 0.02 world-unit seam overlap (source: grimoire/build/geometry_patterns.md).",
            "Do not satisfy raised or recessed relief, fasteners, or grip structure with a map alone when the feature affects form; use geometry or displacement (source: grimoire/build/geometry_patterns.md).",
        ],
    }


def load_assessment(path: Path | None) -> dict | None:
    if path is None:
        return None
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("assessment must be a JSON object")
    return payload


def inject_geometry_rules(spec: dict, target_name: str) -> None:
    contract = spec.setdefault("qualityContract", {})
    prohibitions = contract.setdefault("mustNotDo", contract.get("antiShallowSpecRules", []))
    if not isinstance(prohibitions, list):
        prohibitions = []
    source = "source: grimoire/build/geometry_patterns.md"
    rules = [
        f"Adjacent components must overlap by at least 0.02 world units at shared seams ({source}).",
        f"Raised or recessed relief and fasteners must be geometry, instanced micro-parts, or displacement; a map alone is an approximation ({source}).",
    ]
    object_tokens = set(re.findall(r"[a-z0-9]+", target_name.lower()))
    if object_tokens & {"blade", "knife", "sword", "dagger", "spear", "bowie"}:
        rules.append(f"Blade components must vary in thickness from spine to edge and taper from ricasso toward the tip ({source}).")
    for rule in rules:
        if rule not in prohibitions:
            prohibitions.append(rule)
    contract["mustNotDo"] = prohibitions
    spec["qualityContract"] = contract
    inventory = spec.get("preSpecAssessment", {}).get("detailInventory", {})
    details = inventory.get("details", []) if isinstance(inventory, dict) else []
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            mapping = detail.get("mapsTo")
            detail["realization"] = detail.get("realization") or ("map-only" if isinstance(mapping, dict) and mapping.get("type") == "map" else "unreported")
            if detail["realization"] == "map-only" and detail.get("kind") in {"fastener", "relief", "linework"}:
                detail["approximation"] = f"Map-only detail; geometry guidance requires physical relief ({source})."


def _cnode(cid, name, primitive, parent, position, scale,
           material="skin", role="body", level="meso", rotation=(0, 0, 0),
           importance=0.7, sockets=None, local_features=None, anim_role="static",
           pivot_mode="center", evidence=None, topology_class="assembled-solid",
           topology_rationale=None, attachment=None, sdf=None, transform_scale=None):
    """Build a full schema-valid componentTree node with humanoid-friendly defaults.

    `attachment` (optional) supplies a validate_sculpt_spec.py-complete attachment contract
    (parentSocket, localStart/localEnd, contactType, baseRadius/endRadius, embedDepth,
    gapTolerance). When localStart/localEnd form a non-degenerate segment, the factory's
    makeAttachmentEndpoint() auto-builds a tapered cylinder spanning them and IGNORES this
    node's own transform/scale for geometry -- so `position`/`scale` above still populate the
    schema's transform/dimensions fields (informational + a render fallback) but the actual
    mesh is driven by the attachment endpoints. `sdf` (optional) supplies a
    geometryDescriptor.sdf block for an implicit/concave part (e.g. a recessed eye socket via
    `subtract`); when given, topologyClass is forced to 'implicit' and transform.scale is
    forced to identity since the SDF primitives already encode real world-unit sizes.
    """
    if topology_rationale is None:
        topology_rationale = (
            f"{name} is a discrete primitive body part assembled onto the humanoid rig, "
            f"not a continuous sculpt or shell."
        )
    if sdf is not None:
        topology_class = "implicit"
        transform_scale = (1.0, 1.0, 1.0)
    node = {
        "id": cid, "name": name, "level": level, "role": role,
        "importance": importance, "confidence": 0.8, "primitive": primitive,
        "topologyClass": topology_class,
        "topologyRationale": topology_rationale,
        "geometryDescriptor": {
            "topologyIntent": "stylized character part",
            "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1},
            "deformationStack": [], "uvStrategy": "generated procedural coordinates",
            "normalStrategy": "smooth vertex normals",
        },
        "parent": parent, "attachment": attachment,
        "dimensions": {"width": float(scale[0]), "height": float(scale[1]),
                       "depth": float(scale[2]), "units": "relative", "confidence": 0.8},
        "transform": {"position": list(position), "rotation": list(rotation),
                      "scale": list(transform_scale if transform_scale is not None else scale)},
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
    if sdf is not None:
        node["geometryDescriptor"]["sdf"] = sdf
    return node


def _hu_ratio(anatomy: dict | None, key: str, default: float) -> float:
    """A head-unit proportion ratio (torso/legs/shoulderWidth/hipWidth) sourced from
    preSpecAssessment.anatomy.proportions when present and positive, else a stylized default.
    This is what lets a real reference's measured proportions drive the template instead of a
    fixed bust-shaped guess."""
    if isinstance(anatomy, dict):
        proportions = anatomy.get("proportions")
        if isinstance(proportions, dict):
            value = proportions.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                return float(value)
    return default


def _limb_attachment(start, end, base_radius, end_radius, socket,
                      contact_type="socket-joint", embed_depth=0.03, gap_tolerance=0.01,
                      evidence=None) -> dict:
    """A validate_sculpt_spec.py-complete attachment contract for a linear body segment
    (torso spine, neck, or a limb bone). `socket` fills attachment.parentSocket, a free-text
    joint-name label -- used instead of parentId because a bare parentId would just restate
    the component's own `parent` field with no more information, and validate_sculpt_spec.py's
    strict-quality mode errors if a supplied parentId ever drifts out of sync with `parent`.
    localStart/localEnd are points in the OWNING COMPONENT'S PARENT's local space (this
    template now authors a real parent chain -- see make_character_component_tree), and drive
    generate_threejs_factory.py's makeAttachmentEndpoint() to auto-build a tapered cylinder
    spanning them -- so the owning component's own primitive/scale/rotation become a
    schema-metadata fallback only."""
    return {
        "parentSocket": socket,
        "localStart": [round(float(v), 5) for v in start],
        "localEnd": [round(float(v), 5) for v in end],
        "contactType": contact_type,
        "baseRadius": round(float(base_radius), 5),
        "endRadius": round(float(end_radius), 5),
        "embedDepth": embed_depth,
        "gapTolerance": gap_tolerance,
        "evidenceRefs": evidence or ["full-object"],
    }


def _eye_socket_sdf(hu: float) -> dict:
    """A concave eye socket: a spherical shell with a smaller, forward-offset sphere carved out
    of it via a boolean subtraction -- a real cavity, not a shaded/decal patch (the recorded
    defect US-004: 'the eye reads as a patch, not a recessed socket'). See
    forge/_shared/sdf_primitives.py VALID_SDF_OPERATIONS for 'subtract'."""
    outer_radius = 0.09 * hu
    carve_radius = 0.075 * hu
    carve_offset = 0.055 * hu

    # `bounds` is REQUIRED here, not optional. polygonizeSdf falls back to a [-2,-2,-2]..[2,2,2]
    # box, and at resolution 20 that makes each sample cell 0.2 across while this shell's radius is
    # 0.0252 -- the whole sphere falls between samples and marching cubes emits NOTHING. The eye
    # sockets have therefore been EMPTY GEOMETRY in every render so far: the spec said `subtract`,
    # the validator's recessed-feature gate was satisfied by that, and 457 tests passed, while the
    # cavity did not exist in the model. Nothing checked that the emitted geometry was non-empty.
    #
    # Fit the box to the primitives instead of defaulting: the carve sphere reaches furthest, at
    # its offset plus its radius, and 1.25x leaves room for the surface without wasting samples.
    extent = max(outer_radius, carve_offset + carve_radius) * 1.25
    return {
        "primitives": [
            {"id": "shell", "type": "sphere", "center": [0.0, 0.0, 0.0], "radius": round(outer_radius, 5)},
            {"id": "carve", "type": "sphere", "center": [0.0, 0.0, round(carve_offset, 5)], "radius": round(carve_radius, 5)},
        ],
        # id only -- validate_sculpt_spec rejects setting both id and output; they are alternatives.
        "operations": [{"id": "socket", "type": "subtract", "left": "shell", "right": "carve"}],
        "bounds": {"min": [round(-extent, 5)] * 3, "max": [round(extent, 5)] * 3},
        "resolution": 24,
    }


def make_character_component_tree(
    anatomy: dict | None = None,
    include_accessories: bool = False,
) -> list:
    """A stylized GENERAL HUMANOID template: head/neck/torso/pelvis, full arms (upper arm ->
    forearm -> hand) and full legs (thigh -> shin -> foot), face features including recessed
    eye sockets, and hair. Head-unit driven; HU ~= 0.28 world units. Reads
    preSpecAssessment.anatomy.proportions (torso/legs/shoulderWidth/hipWidth, in head units)
    when supplied, else falls back to stylized full-body defaults (~7.5 heads tall).

    Glasses, headphones, and a chest decal are NOT part of the baseline -- they were traits of
    one specific reference person, not general humanoid anatomy. Pass include_accessories=True
    to restore them. Digits ARE baseline (US-003): five per hand, three phalanges each.

    REAL NESTED HIERARCHY (worker-flatten's WS-E fix has landed: a pivot Group always carries
    identity scale and a part's dimensions are baked into its own geometry, so a non-uniformly
    scaled parent can no longer distort a nested child -- see generate_threejs_factory.py). So,
    unlike the earlier revision of this function, every part below is parented on its real
    anatomical parent (neck -> torso, head -> neck, upper-arm -> torso, forearm -> upper-arm,
    hand -> forearm, thigh -> pelvis, shin -> thigh, foot -> shin, face features -> head, ...),
    not flattened onto a single hidden root.

    Layout math below is still computed as absolute joint coordinates in one shared coordinate
    space (torso_top_pt, shoulder_pt, hip_pt, ...) because that keeps the trig/anatomy formulas
    simple and easy to check by eye. Each point is then converted to the LOCAL space of the
    component's real parent with `_rel(point, parent_id)` immediately before it is authored onto
    a component -- Three.js interprets a child's position/attachment endpoints relative to its
    own parent Group, not the world, so this conversion is required once nesting is real.
    `world_anchor[parent_id]` tracks, for every already-built component, the point that becomes
    its own pivot Group's world position (its attachment localStart for limb/spine segments, or
    its own authored position for a plain part) so `_rel()` can look it up.
    """
    hu = 0.28
    torso_h = _hu_ratio(anatomy, "torso", 2.2) * hu
    legs_h = _hu_ratio(anatomy, "legs", 3.6) * hu
    shoulder_w = _hu_ratio(anatomy, "shoulderWidth", 2.3) * hu
    hip_w = _hu_ratio(anatomy, "hipWidth", 1.7) * hu

    # ---- core body-mass landmarks (world Y, root-relative -- see class docstring on _rel()) ----
    torso_center_y = 0.55 * hu
    torso_top_y = torso_center_y + torso_h * 0.5
    torso_bottom_y = torso_center_y - torso_h * 0.5

    # The head is DERIVED from the spine, bottom-up, not pinned to an absolute height.
    #
    # It used to be `head_y = 2.5 * hu`, fixed. Because `torso_h` comes from anatomy and the head
    # did not, shortening the torso did not lower the head -- it STRETCHED THE NECK to fill the
    # gap. Measured: at the default torso ratio 2.2 the neck is 15% of torso height; at the
    # regret reference's measured 1.584 it became 40%, and the torso-top-to-head-bottom gap
    # doubled from 0.081 to 0.167. That is the giraffe neck visible in the first render, and no
    # per-part check could see it because every part was individually correct.
    #
    # A skeleton has to be built in the order the bones actually stack.
    NECK_LENGTH_HU = 0.33          # a neck is about a third of a head unit
    HEAD_HEIGHT_HU = 1.12          # matches the head ellipsoid's own y scale below
    neck_bottom_y = torso_top_y - 0.08 * hu
    neck_top_y = neck_bottom_y + NECK_LENGTH_HU * hu
    head_y = neck_top_y + HEAD_HEIGHT_HU * hu * 0.5 - 0.04 * hu

    pelvis_center_y = torso_bottom_y - 0.1 * hu
    # The pelvis height co-varies with its width. It used to be a fixed 0.22 hu while the width
    # scaled with `hip_w`, so a wide-hipped anatomy produced a SLAB: at the regret reference's
    # measured hipWidth the box came out 0.455 wide x 0.123 tall x 0.360 deep, which renders as a
    # tabletop the figure appears to be sitting on. A front-on human pelvis is roughly as tall as
    # it is wide, so tie the two together and keep the old value as the floor for narrow hips.
    pelvis_half_h = max(0.22 * hu, hip_w * 0.30)
    hip_joint_y = pelvis_center_y - pelvis_half_h + 0.05 * hu

    thigh_len = legs_h * 0.53
    shin_len = legs_h * 0.47
    knee_joint_y = hip_joint_y - thigh_len
    ankle_joint_y = knee_joint_y - shin_len
    foot_y = ankle_joint_y - 0.05 * hu

    hip_x = hip_w * 0.30
    shoulder_x = shoulder_w * 0.5

    # The abdomen/chest division. A single torso cannot bend at the waist, so the spine is two
    # segments: below this line is abdomen, above it is chest. 0.42 puts the joint slightly below
    # mid-torso, which is where a human actually flexes.
    waist_y = torso_bottom_y + torso_h * 0.42

    torso_top_pt = (0.0, torso_top_y, 0.02 * hu)
    torso_bottom_pt = (0.0, torso_bottom_y, 0.0)
    waist_pt = (0.0, waist_y, 0.01 * hu)
    neck_bottom_pt = (0.0, neck_bottom_y, 0.02 * hu)
    neck_top_pt = (0.0, neck_top_y, 0.02 * hu)
    head_world = (0.0, head_y, 0.02 * hu)
    pelvis_world = (0.0, pelvis_center_y, 0.0)

    world_anchor = {"root": (0.0, 0.0, 0.0)}

    def _rel(point, parent_id):
        anchor = world_anchor[parent_id]
        return (point[0] - anchor[0], point[1] - anchor[1], point[2] - anchor[2])

    tree = [_cnode("root", "Character (root)", "box", None, (0, 0, 0), (1, 1, 1),
                   material="hidden", role="body", level="macro", importance=1.0, anim_role="root")]

    # ---- pelvis FIRST: the spine hangs off it, so it has to exist before the abdomen can be
    # parented to it. Previously the torso and pelvis were siblings under "root", which forced
    # derive_character_rig() to pick one as the bone root by hand; a real pelvis->abdomen edge
    # removes that special case entirely.
    tree.append(_cnode(
        # An ellipsoid, not a box. A box gave three consecutive profile bands at exactly the same
        # width (measured: 0.4554, 0.4554, 0.4554) because its sides are flat and vertical -- the
        # signature of a slab in a silhouette. Hip mass is rounded, so an ellipsoid both reads
        # correctly and lets the profile taper into the thighs.
        "pelvis", "Pelvis", "ellipsoid", "root", _rel(pelvis_world, "root"),
        (hip_w * 0.95, pelvis_half_h * 2, hip_w * 0.75), material="pants", role="body",
        level="macro", importance=0.9,
    ))
    world_anchor["pelvis"] = pelvis_world

    # ---- spine and neck: capsule/cylinder primitives trigger validate_sculpt_spec.py's
    # attachment requirement regardless of role (ATTACHMENT_PRIMITIVES), so each gets a real
    # spine-line attachment segment instead of a bare scale/rotation capsule. That is a genuine
    # improvement rather than a validator workaround: base/end radius gives the waist taper and
    # the neck-to-shoulder taper a uniform capsule could never express.
    #
    # Each segment is anchored at its PROXIMAL (lower) end, exactly like a limb. That is what
    # makes a component's origin its own joint, and it is why derive_character_rig() no longer
    # needs a torso-specific joint exception: summing offsets lands on a real joint every time.
    tree.append(_cnode(
        "abdomen", "Abdomen", "capsule", "pelvis", _rel(torso_bottom_pt, "pelvis"),
        (hip_w * 0.9, waist_y - torso_bottom_y, hip_w * 0.8), material="shirt", role="shell",
        level="macro", importance=0.95,
        attachment=_limb_attachment(_rel(torso_bottom_pt, "pelvis"), _rel(waist_pt, "pelvis"),
                                    hip_w * 0.40, shoulder_w * 0.34,
                                    socket="pelvis-waist", contact_type="rigid-weld"),
    ))
    world_anchor["abdomen"] = torso_bottom_pt

    tree.append(_cnode(
        "chest", "Chest", "capsule", "abdomen", _rel(waist_pt, "abdomen"),
        (shoulder_w * 0.95, torso_top_y - waist_y, hip_w * 0.85), material="shirt", role="shell",
        level="macro", importance=1.0,
        # The chest now tapers INWARD toward the neck (end radius < base) instead of being widest
        # at its flat top. Previously the chest's top radius WAS the shoulder width, so the
        # silhouette jumped from the neck straight to full torso width in a single profile band --
        # measured 0.091 -> 0.262 with no intermediate value, where the reference slopes
        # 0.116 -> 0.229 over ~8% of its height. The shoulder mass belongs to the clavicles.
        attachment=_limb_attachment(_rel(waist_pt, "abdomen"), _rel(torso_top_pt, "abdomen"),
                                    shoulder_w * 0.38, shoulder_w * 0.26,
                                    socket="abdomen-chest", contact_type="rigid-weld"),
    ))
    world_anchor["chest"] = waist_pt

    tree.append(_cnode(
        "neck", "Neck", "cylinder", "chest", _rel(neck_bottom_pt, "chest"),
        (0.55 * hu, neck_top_y - neck_bottom_y, 0.55 * hu), material="skin", role="support",
        level="meso", importance=0.6,
        attachment=_limb_attachment(_rel(neck_bottom_pt, "chest"), _rel(neck_top_pt, "chest"),
                                    0.26 * hu, 0.2 * hu,
                                    socket="chest-neck-base", contact_type="rigid-weld"),
    ))
    world_anchor["neck"] = neck_bottom_pt

    tree.append(_cnode(
        "head", "Head", "ellipsoid", "neck", _rel(head_world, "neck"),
        (0.92 * hu, 1.12 * hu, 0.98 * hu), material="skin", role="body", level="macro",
        importance=1.0,
    ))
    world_anchor["head"] = head_world

    # ---- face features and hair: parented to "head". Offsets are already authored local to
    # head (never went through world_pos/flattening even before the un-flatten), so they need
    # no _rel() conversion. ----
    # (id, name, primitive, offset, scale, material, role, level, rotation, importance, features)
    face_parts = [
        ("hair", "Hair", "ellipsoid", (0, 0.3 * hu, -0.02 * hu),
         (1.0 * hu, 0.78 * hu, 1.02 * hu), "hair", "hair", "meso", (0, 0, 0), 0.8,
         ["short, neutral stylized hairstyle"]),
        ("brow-l", "Eyebrow L", "box", (0.2 * hu, 0.12 * hu, 0.46 * hu),
         (0.22 * hu, 0.04 * hu, 0.06 * hu), "hair", "detail", "micro", (0, 0, 0), 0.4, []),
        ("brow-r", "Eyebrow R", "box", (-0.2 * hu, 0.12 * hu, 0.46 * hu),
         (0.22 * hu, 0.04 * hu, 0.06 * hu), "hair", "detail", "micro", (0, 0, 0), 0.4, []),
        # Ears. Offsets are head-unit multiples like every other feature here, so they scale with
        # the anatomy ratios instead of being pinned to one figure's dimensions. x = 0.43 hu puts
        # them on the skull surface (the head's own half-width is 0.46 hu), y at eye level, z
        # slightly aft of centre. They are detail, not bones: an ear does not articulate.
        ("ear-l", "Ear L", "ellipsoid", (0.43 * hu, 0.02 * hu, -0.02 * hu),
         (0.09 * hu, 0.26 * hu, 0.17 * hu), "skin", "detail", "micro", (0, 0, 0), 0.45,
         ["outer helix reads as a flattened shell against the skull, not a disc"]),
        ("ear-r", "Ear R", "ellipsoid", (-0.43 * hu, 0.02 * hu, -0.02 * hu),
         (0.09 * hu, 0.26 * hu, 0.17 * hu), "skin", "detail", "micro", (0, 0, 0), 0.45,
         ["outer helix reads as a flattened shell against the skull, not a disc"]),
        ("nose", "Nose", "ellipsoid", (0, -0.04 * hu, 0.5 * hu),
         (0.14 * hu, 0.28 * hu, 0.18 * hu), "skin", "detail", "micro", (1.4, 0, 0), 0.4, []),
        ("mouth", "Mouth", "box", (0, -0.34 * hu, 0.46 * hu),
         (0.24 * hu, 0.04 * hu, 0.05 * hu), "lips", "detail", "micro", (0, 0, 0), 0.4, []),
    ]
    if include_accessories:
        face_parts.extend([
            ("glasses-frame-l", "Glasses frame L", "torus", (0.21 * hu, 0.02 * hu, 0.48 * hu),
             (0.26 * hu, 0.22 * hu, 0.08 * hu), "glasses-frame", "rim", "meso", (0, 0, 0), 0.85, []),
            ("glasses-frame-r", "Glasses frame R", "torus", (-0.21 * hu, 0.02 * hu, 0.48 * hu),
             (0.26 * hu, 0.22 * hu, 0.08 * hu), "glasses-frame", "rim", "meso", (0, 0, 0), 0.85, []),
            ("glasses-bridge", "Glasses bridge", "box", (0, 0.04 * hu, 0.5 * hu),
             (0.12 * hu, 0.04 * hu, 0.04 * hu), "glasses-frame", "trim", "micro", (0, 0, 0), 0.5, []),
            ("lens-l", "Lens L", "plane-card", (0.21 * hu, 0.02 * hu, 0.485 * hu),
             (0.22 * hu, 0.18 * hu, 1.0), "glasses-lens", "panel", "micro", (0, 0, 0), 0.5, []),
            ("lens-r", "Lens R", "plane-card", (-0.21 * hu, 0.02 * hu, 0.485 * hu),
             (0.22 * hu, 0.18 * hu, 1.0), "glasses-lens", "panel", "micro", (0, 0, 0), 0.5, []),
            ("hp-band", "Headphone band", "torus", (0, -0.72 * hu, 0.05 * hu),
             (0.95 * hu, 0.62 * hu, 0.7 * hu), "headphone", "ring", "meso", (1.2, 0, 0), 0.85, []),
            ("hp-cup-l", "Ear cup L", "sphere", (0.5 * hu, -0.98 * hu, 0.35 * hu),
             (0.4 * hu, 0.4 * hu, 0.22 * hu), "headphone", "detail", "meso", (0, 0, 0), 0.7, []),
            ("hp-cup-r", "Ear cup R", "sphere", (-0.5 * hu, -0.98 * hu, 0.35 * hu),
             (0.4 * hu, 0.4 * hu, 0.22 * hu), "headphone", "detail", "meso", (0, 0, 0), 0.7, []),
        ])
    for pid, name, prim, off, scale, mat, role, level, rot, imp, feats in face_parts:
        tree.append(_cnode(pid, name, prim, "head", off, scale,
                           material=mat, role=role, level=level, rotation=rot, importance=imp,
                           local_features=feats))

    if include_accessories:
        # Chest decal is parented to torso; its offset was authored relative to torso's OLD
        # seed position (torso_center_y) back when this template flattened everything, so
        # convert it into torso's real local frame (anchored at torso_top_pt) here.
        decal_old_world = (0.0, torso_center_y + 0.1 * hu, 0.78 * hu)
        decal_local = _rel(decal_old_world, "chest")
        tree.append(_cnode(
            "shirt-decal", "Chest graphic", "plane-card", "chest", decal_local,
            (1.5 * hu, 0.9 * hu, 1.0), material="shirt-decal", role="decal", level="micro",
            importance=0.7,
        ))

    # ---- eyes: a visible eyeball sphere plus a recessed cavity carved via SDF subtract, not a
    # flat patch (US-004). Naming/roles deliberately avoid the token "socket": that token is
    # also in validate_sculpt_spec.py's ATTACHMENT_ROLES (ball-and-socket limb joints), so
    # reusing it here would additionally force an unrelated attachment contract onto a part
    # that has no start/end line to attach -- "cavity" trips the recessed-feature gate just as
    # well without that collision. Offsets are already local to head. ----
    for side, eye_x in (("l", 0.19 * hu), ("r", -0.19 * hu)):
        eye_local = (eye_x, 0.03 * hu, 0.40 * hu)
        cavity_local = (eye_x, 0.03 * hu, 0.43 * hu)
        tree.append(_cnode(
            f"eye-{side}", f"Eye {side.upper()}", "sphere", "head", eye_local,
            (0.11 * hu, 0.11 * hu, 0.11 * hu), material="eye", role="detail", level="micro",
            importance=0.5,
        ))
        tree.append(_cnode(
            f"eye-cavity-{side}", f"Eye cavity {side.upper()}", "sphere", "head", cavity_local,
            (0.18 * hu, 0.18 * hu, 0.18 * hu), material="skin", role="cavity", level="micro",
            importance=0.4, sdf=_eye_socket_sdf(hu),
            topology_rationale=(
                "The eye reads as a recessed concave cavity carved out of the head volume with a "
                "boolean subtraction (US-004), not a flat decal or shaded patch."
            ),
        ))

    # ---- arms: shoulder -> elbow -> wrist -> hand, laid out as absolute joint coordinates,
    # then each segment is parented on the PREVIOUS segment (torso -> upper-arm -> forearm ->
    # hand) and its attachment endpoints are converted into that parent's local space. ----
    arm_lean = 0.20
    arm_total = torso_h * 0.95
    upper_arm_len = arm_total * 0.55
    forearm_len = arm_total * 0.45
    shoulder_y = torso_top_y - 0.08 * hu
    for side, sign in (("l", 1), ("r", -1)):
        sx = shoulder_x * sign
        lean = arm_lean * sign
        shoulder_pt = (sx, shoulder_y, 0.05 * hu)
        elbow_pt = (
            shoulder_pt[0] + math.sin(lean) * upper_arm_len,
            shoulder_pt[1] - math.cos(lean) * upper_arm_len,
            shoulder_pt[2],
        )
        wrist_pt = (
            elbow_pt[0] + math.sin(lean * 0.6) * forearm_len,
            elbow_pt[1] - math.cos(lean * 0.6) * forearm_len,
            elbow_pt[2],
        )
        hand_len = 0.32 * hu
        hand_center = (
            wrist_pt[0] + math.sin(lean * 0.6) * hand_len * 0.5,
            wrist_pt[1] - math.cos(lean * 0.6) * hand_len * 0.5,
            wrist_pt[2],
        )
        clavicle_id = f"clavicle-{side}"
        upper_arm_id = f"upper-arm-{side}"
        forearm_id = f"forearm-{side}"
        hand_id = f"hand-{side}"

        # ---- clavicle: sternum -> shoulder joint. Without it the upper arm parents straight to
        # the chest, so a shoulder cannot shrug or roll forward and the arm's rotation origin
        # sits inside the chest volume rather than on the joint. It also gives the arm chain a
        # real root that moves with the ribcage, which is what a shoulder does.
        clavicle_root_pt = (sign * shoulder_x * 0.16, shoulder_y + 0.02 * hu, 0.03 * hu)
        tree.append(_cnode(
            clavicle_id, f"Clavicle {side.upper()}", "capsule", "chest",
            _rel(clavicle_root_pt, "chest"),
            (shoulder_x * 0.84, 0.34 * hu, 0.34 * hu), material="skin", role="support",
            level="meso", importance=0.6,
            # Thick, and tapering OUTWARD to the shoulder joint. This is the trapezius/deltoid
            # mass, and it is what produces the neck-to-shoulder slope: it starts narrow beside
            # the neck and ends wide at the joint. At the old 0.09 hu radius the clavicle was a
            # 0.05-wide stick, invisible inside the chest, and the chest's flat top had to be the
            # shoulder instead -- which is exactly what made the silhouette a cliff.
            attachment=_limb_attachment(_rel(clavicle_root_pt, "chest"), _rel(shoulder_pt, "chest"),
                                        0.11 * hu, 0.17 * hu, socket=f"chest-clavicle-{side}",
                                        contact_type="rigid-weld"),
        ))
        world_anchor[clavicle_id] = clavicle_root_pt

        tree.append(_cnode(
            upper_arm_id, f"Upper arm {side.upper()}", "capsule", clavicle_id,
            _rel(shoulder_pt, clavicle_id),
            (0.32 * hu, upper_arm_len, 0.32 * hu), material="shirt", role="arm", level="meso",
            importance=0.7,
            attachment=_limb_attachment(_rel(shoulder_pt, clavicle_id), _rel(elbow_pt, clavicle_id),
                                        0.16 * hu, 0.13 * hu, socket=f"clavicle-shoulder-{side}"),
        ))
        world_anchor[upper_arm_id] = shoulder_pt
        tree.append(_cnode(
            forearm_id, f"Forearm {side.upper()}", "capsule", upper_arm_id, _rel(elbow_pt, upper_arm_id),
            (0.26 * hu, forearm_len, 0.26 * hu), material="skin", role="arm", level="meso",
            importance=0.65,
            attachment=_limb_attachment(_rel(elbow_pt, upper_arm_id), _rel(wrist_pt, upper_arm_id),
                                        0.12 * hu, 0.09 * hu, socket=f"upper-arm-elbow-{side}",
                                        contact_type="hinge-joint"),
        ))
        world_anchor[forearm_id] = elbow_pt
        tree.append(_cnode(
            hand_id, f"Hand {side.upper()}", "box", forearm_id, _rel(hand_center, forearm_id),
            (0.22 * hu, hand_len, 0.13 * hu), material="skin", role="hand", level="meso",
            importance=0.55,
        ))
        world_anchor[hand_id] = hand_center
        # ---- digits: five per hand, three phalanges each, chained proximal -> middle -> distal.
        # This replaces the old `--fingers` flag, which added ONE box labelled "fingers" plus one
        # box labelled "thumb" -- a mitten, not a hand, and opt-in at that. A humanoid that cannot
        # curl a finger is not finished, so digits are now baseline with no flag.
        #
        # Four fingers march down off the distal palm edge; the thumb branches from near the wrist
        # and travels MEDIALLY (toward the body) rather than down, which is what opposes it. The
        # medial direction is -sign: for the left hand (sign=+1, +x side of the body) the inside
        # is -x. Getting that backwards puts both thumbs on the outside.
        knuckle_row_y = hand_center[1] - hand_len * 0.42
        digit_layout = (
            #  name,     x offset (hu),  phalanx lengths (hu),        radius (hu), medial thumb?
            ("thumb", -0.10 * sign, (0.075, 0.055, 0.040), 0.032, True),
            ("index", -0.075 * sign, (0.105, 0.072, 0.048), 0.028, False),
            ("middle", -0.025 * sign, (0.115, 0.080, 0.050), 0.029, False),
            ("ring", 0.025 * sign, (0.105, 0.072, 0.046), 0.027, False),
            ("little", 0.070 * sign, (0.080, 0.058, 0.038), 0.024, False),
        )
        for digit_name, x_offset, lengths, radius, is_thumb in digit_layout:
            # The thumb starts near the wrist and runs inward; the fingers start at the knuckle
            # row and run down the same lean the forearm already has.
            if is_thumb:
                joint = (hand_center[0] + x_offset * hu,
                         hand_center[1] - hand_len * 0.06,
                         hand_center[2] + 0.02 * hu)
                step = (-0.72 * sign, -0.62, 0.30)      # medial, down, forward
            else:
                joint = (hand_center[0] + x_offset * hu, knuckle_row_y, hand_center[2] + 0.01 * hu)
                step = (math.sin(lean * 0.6), -math.cos(lean * 0.6), 0.0)

            parent_id = hand_id
            for index, length in enumerate(lengths):
                phalanx_id = f"{digit_name}-{side}-{index + 1}"
                tip = (joint[0] + step[0] * length * hu,
                       joint[1] + step[1] * length * hu,
                       joint[2] + step[2] * length * hu)
                tree.append(_cnode(
                    phalanx_id,
                    f"{digit_name.capitalize()} {side.upper()} phalanx {index + 1}",
                    "capsule", parent_id, _rel(joint, parent_id),
                    (radius * 2 * hu, length * hu, radius * 2 * hu),
                    material="skin", role="finger", level="micro",
                    importance=0.3 if index == 0 else 0.2,
                    attachment=_limb_attachment(
                        _rel(joint, parent_id), _rel(tip, parent_id),
                        radius * hu, radius * 0.82 * hu,
                        socket=f"{parent_id}-{digit_name}-{index + 1}",
                        contact_type="hinge-joint" if index else "rigid-weld",
                    ),
                ))
                world_anchor[phalanx_id] = joint
                parent_id = phalanx_id
                joint = tip

    # ---- legs: hip -> knee -> ankle -> foot, same pattern (pelvis -> thigh -> shin -> foot). ----
    for side, sign in (("l", 1), ("r", -1)):
        lx = hip_x * sign
        hip_pt = (lx, hip_joint_y, 0.02 * hu)
        knee_pt = (lx, knee_joint_y, 0.02 * hu)
        ankle_pt = (lx, ankle_joint_y, 0.0)
        foot_center = (lx, foot_y, 0.16 * hu)
        thigh_id = f"thigh-{side}"
        shin_id = f"shin-{side}"
        tree.append(_cnode(
            thigh_id, f"Thigh {side.upper()}", "capsule", "pelvis", _rel(hip_pt, "pelvis"),
            (0.38 * hu, thigh_len, 0.38 * hu), material="pants", role="leg", level="meso",
            importance=0.75,
            attachment=_limb_attachment(_rel(hip_pt, "pelvis"), _rel(knee_pt, "pelvis"),
                                        0.2 * hu, 0.16 * hu, socket=f"pelvis-hip-{side}"),
        ))
        world_anchor[thigh_id] = hip_pt
        tree.append(_cnode(
            shin_id, f"Shin {side.upper()}", "capsule", thigh_id, _rel(knee_pt, thigh_id),
            (0.28 * hu, shin_len, 0.28 * hu), material="pants", role="leg", level="meso",
            importance=0.7,
            attachment=_limb_attachment(_rel(knee_pt, thigh_id), _rel(ankle_pt, thigh_id),
                                        0.14 * hu, 0.1 * hu, socket=f"thigh-knee-{side}",
                                        contact_type="hinge-joint"),
        ))
        world_anchor[shin_id] = knee_pt
        tree.append(_cnode(
            f"foot-{side}", f"Foot {side.upper()}", "box", shin_id, _rel(foot_center, shin_id),
            (0.24 * hu, 0.16 * hu, 0.44 * hu), material="shoes", role="foot", level="meso",
            importance=0.5,
        ))
    return tree


def _shade_hex(hex_color: str, factor: float) -> str:
    """Return a slightly darker/lighter shade of a #RRGGBB color (factor<1 darker)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (max(0, min(255, round(c * factor))) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


CHARACTER_BASE_MATERIALS = [
    {"id": "hidden", "baseColor": "#000000", "roughness": {"base": 1.0, "variation": 0.0}, "opacity": {"base": 0.0}},
    {"id": "skin", "baseColor": "#e8b98f", "roughness": {"base": 0.55, "variation": 0.08}},
    {"id": "hair", "baseColor": "#171310", "roughness": {"base": 0.42, "variation": 0.1}},
    {"id": "shirt", "baseColor": "#20202a", "roughness": {"base": 0.85, "variation": 0.12}},
    {"id": "pants", "baseColor": "#2b2d33", "roughness": {"base": 0.75, "variation": 0.1}},
    {"id": "shoes", "baseColor": "#171512", "roughness": {"base": 0.55, "variation": 0.08}},
    {"id": "eye", "baseColor": "#f2eee4", "roughness": {"base": 0.2, "variation": 0.03}},
    {"id": "lips", "baseColor": "#c98070", "roughness": {"base": 0.5, "variation": 0.05}},
]
# Accessories are opt-in (apply_character_template's include_accessories) -- a chest decal,
# glasses, and headphones are one reference person's traits, not general humanoid anatomy.
CHARACTER_ACCESSORY_MATERIALS = [
    {"id": "shirt-decal", "baseColor": "#d24a20", "roughness": {"base": 0.7, "variation": 0.05}},
    {"id": "glasses-frame", "baseColor": "#111114", "roughness": {"base": 0.35, "variation": 0.05}},
    {"id": "glasses-lens", "baseColor": "#a9c6d8", "roughness": {"base": 0.08, "variation": 0.02}},
    {"id": "headphone", "baseColor": "#0e0e10", "roughness": {"base": 0.5, "variation": 0.08}},
]


def make_character_build_passes() -> list:
    base = make_pre_spec_assessment  # noqa: reference to keep import graph obvious
    passes = [
        {"id": "blockout", "goal": "Match head-unit proportions and pose silhouette.",
         "componentRefs": ["root"], "acceptance": ["Humanoid proportions and pose read correctly without materials."]},
        {"id": "proportion-lock", "goal": "Lock head/torso/limb head-unit ratios and pose angles.",
         "componentRefs": ["root", "head", "chest"], "acceptance": ["Head-unit ratios match anatomy; silhouette matches reference."]},
        {"id": "feature-placement", "goal": "Place facial features, hair, and any enabled accessories to landmarks.",
         "componentRefs": ["head", "hair", "eye-l", "eye-r"],
         "acceptance": ["Eyeline/nose/mouth on landmark lines; eyes read as recessed, not flat patches; enabled accessories placed as in reference."]},
        {"id": "material-pass", "goal": "Match skin/hair/cloth/metal color and roughness.",
         "componentRefs": ["root"], "acceptance": ["Skin, hair, shirt, pants, shoe, and any enabled accessory materials match reference palette."]},
        {"id": "lighting-pass", "goal": "Soft key from reference direction plus rim.",
         "componentRefs": ["root"], "acceptance": ["Readable under neutral light; reference-matched lighting added."]},
        {"id": "interaction-pass", "goal": "Rig-ready pivots and sockets.",
         "componentRefs": ["root"], "acceptance": ["Head/neck/shoulder/elbow/hip/knee pivots and sockets exposed."]},
        {"id": "optimization-pass", "goal": "Protect runtime performance.",
         "componentRefs": ["root"], "acceptance": ["Triangle/draw-call budget documented."]},
    ]
    del base
    return passes


def make_character_feature_targets(include_accessories: bool = False) -> list:
    outfit_refs = ["shirt-decal", "headphone", "glasses-frame-l"] if include_accessories else ["chest", "pants", "shoes"]
    return [
        {"id": "anatomy-proportion", "name": "Head-unit proportions and pose", "tier": "critical",
         "passIds": ["blockout", "proportion-lock"], "minimumScore": 0.78, "mustPass": True,
         "componentRefs": ["root", "head", "chest"], "evidenceRefs": ["full-object"]},
        {"id": "face-landmark-placement", "name": "Face landmarks incl. recessed eye sockets", "tier": "critical",
         "passIds": ["feature-placement"], "minimumScore": 0.75, "mustPass": True,
         "componentRefs": ["head", "eye-l", "eye-cavity-l", "nose"], "evidenceRefs": ["full-object"]},
        {"id": "pose-silhouette", "name": "Pose and full-body silhouette", "tier": "critical",
         "passIds": ["blockout", "proportion-lock"], "minimumScore": 0.75, "mustPass": True,
         "componentRefs": ["root", "upper-arm-l", "thigh-l"], "evidenceRefs": ["full-object"]},
        {"id": "outfit-and-palette", "name": "Outfit + accessories + palette", "tier": "important",
         "passIds": ["material-pass"], "minimumScore": 0.7, "mustPass": False,
         "componentRefs": outfit_refs, "evidenceRefs": ["full-object"]},
    ]


# Components that are body SEGMENTS and therefore become bones, but carry no
# `attachment` block to derive a segment from (they are not capsules/cylinders).
# Everything else without an attachment block -- brows, nose, mouth, hair, eyes,
# eye cavities, lenses, decals -- rides its parent bone and is deliberately NOT a
# bone: a brow does not articulate.
_RIG_STRUCTURAL_LEAVES = {"pelvis", "head", "hand-l", "hand-r", "foot-l", "foot-r"}

_RIG_CHAINS = (
    # Digits are checked BEFORE the arm prefixes. They do not collide today, but a future
    # "hand-thumb-*" id would match "hand-" first and land in the wrong chain.
    (("thumb-", "index-", "middle-", "ring-", "little-"), "digit"),
    (("clavicle-", "upper-arm-", "forearm-", "hand-"), "arm"),
    (("thigh-", "shin-", "foot-"), "leg"),
    (("neck", "head"), "head"),
    (("chest", "abdomen", "pelvis"), "spine"),
)


def _rig_chain_of(component_id: str) -> str:
    for prefixes, chain in _RIG_CHAINS:
        for prefix in prefixes:
            if component_id == prefix or component_id.startswith(prefix):
                return chain
    return "prop"


def derive_character_rig(component_tree: list) -> dict:
    """Derive a PLAN_1.5 §6 `RigSpec` from the component tree instead of authoring one.

    Deriving rather than authoring is the point: a hand-written skeleton and a
    hand-written component tree drift the moment either is edited, and nothing
    detects the drift. Everything below comes from data the tree already carries.

    Three decisions that are choices rather than facts, recorded here because the
    next person cannot re-derive them from the code alone:

    1. **Every position is MODEL space, and a bone's tip is its child's joint.**
       `attachment.localEnd` looks like the obvious tip and is NOT used, because it
       is right for limbs and wrong for the spine: a limb component's origin IS its
       proximal joint, but the torso's origin is its centre, so reading localEnd
       there produced a spine bone running from the chest DOWN past the pelvis --
       which would have pulled leg vertices onto the torso's weight. PLAN_1.5 §6's
       "tipPos defaults to the child's jointPos" is the right rule but is
       meaningless parent-local (a parent's tip and its child's joint sit in
       different frames), so everything is accumulated into one space first.

    2. **`pelvis` is the single bone root, with `torso` parented to it.** A
       `RigSpec` requires exactly one root, and the component tree parents BOTH
       `torso` and `pelvis` to `"root"` (they are siblings there). Hips-as-root is
       the standard humanoid convention and it is the one that makes rotating the
       pelvis bend the whole figure. This makes the bone hierarchy deliberately
       differ from the pivot hierarchy at exactly one edge, which PLAN_1.5 §8
       ("bones and pivots coexist") explicitly permits.

    3. **A leaf bone continues its parent's direction** for its own `scale.y`,
       rather than being given an invented orientation. Known simplification: a
       foot therefore points down the shin rather than forward. That is wrong
       anatomically and right for weighting, and it is derived rather than
       guessed -- flagged so a real foot orientation can replace it deliberately.
    """
    by_id = {c["id"]: c for c in component_tree if isinstance(c, dict) and c.get("id")}

    def is_bone(component: dict) -> bool:
        return bool(component.get("attachment")) or component["id"] in _RIG_STRUCTURAL_LEAVES

    bone_ids = [cid for cid, c in by_id.items() if is_bone(c)]

    def bone_parent(component_id: str) -> str | None:
        # Walk up the component tree until a component that is itself a bone is
        # found, so a bone parented through a non-bone (a detail) still attaches
        # to the nearest real segment rather than falling out of the hierarchy.
        parent = by_id.get(component_id, {}).get("parent")
        while parent and parent != "root":
            if parent in bone_ids:
                return parent
            parent = by_id.get(parent, {}).get("parent")
        if component_id == "pelvis":
            return None                      # the single root
        return None

    children = {cid: [] for cid in bone_ids}
    for _cid in bone_ids:
        _parent = bone_parent(_cid)
        if _parent in children:
            children[_parent].append(_cid)

    # A branching bone's tip is its SPINE-WARD child's joint. `pelvis` has three bone children
    # (abdomen + both thighs) and `chest` has three (neck + both arms), so §6's single-child
    # default does not apply and rig_spec.resolve_tip_positions() would reject them; naming the
    # spine-ward child is an explicit choice rather than picking an arbitrary one.
    _SPINE_WARD = {
        "chest": "neck", "abdomen": "chest", "pelvis": "abdomen",
        # A hand has five bone children since US-003, so it needs a named tip too. The hand's
        # axis runs to the middle finger, so that digit's proximal phalanx is the anatomical
        # choice rather than whichever digit happens to sort first.
        "hand-l": "middle-l-1", "hand-r": "middle-r-1",
    }

    # No joint override is needed any more. It used to be, because the single `torso` component
    # was anchored at its CENTRE, so reading its own position as a joint produced a spine bone
    # running from the chest DOWN past the pelvis. Splitting the spine into abdomen + chest and
    # anchoring each at its PROXIMAL end -- exactly how limbs were already authored -- makes a
    # component's origin its own joint everywhere, so `hip_top()` and the torso exception are
    # gone. Getting the anatomy right removed the special case rather than adding one.
    def joint_of(component_id: str) -> list:
        return model_pos(component_id)

    def explicit_tip(component_id: str) -> list | None:
        return None

    def model_pos(component_id: str) -> list:
        """Accumulate parent-local offsets into MODEL space.

        Summing offsets instead of composing matrices is exact under TWO preconditions, and
        both are checked rather than assumed, because a silent violation would move every
        descendant joint without anything failing:

        1. **No cascading non-uniform scale.** Holds because pivot Groups always carry identity
           scale now (WS-E); the component's dimensions live in its geometry.
        2. **No rotation on any ancestor.** A rotated ancestor rotates its children's offsets,
           which a sum cannot represent. This holds at BIND POSE and is exactly what applying
           `anatomy.pose.jointAngles` would break — hence the fail-closed check below, and
           hence `apply_character_pose()` must run AFTER this function, never before.
        """
        total = [0.0, 0.0, 0.0]
        cursor = component_id
        while cursor and cursor != "root":
            component = by_id.get(cursor)
            if not component:
                break
            transform = component.get("transform") or {}
            rotation = transform.get("rotation") or [0.0, 0.0, 0.0]
            if any(abs(float(v)) > 1e-9 for v in rotation):
                raise ValueError(
                    f"cannot derive a rig through rotated component {cursor!r} "
                    f"(rotation={rotation}): a bone's joint is accumulated by summing "
                    f"parent-local offsets, which is only valid at bind pose. Derive the rig "
                    f"BEFORE applying pose rotations."
                )
            position = transform.get("position") or [0.0, 0.0, 0.0]
            for i in range(3):
                total[i] += float(position[i])
            cursor = component.get("parent")
        return total

    def segment(component_id: str) -> tuple[list, list]:
        component = by_id[component_id]
        joint = joint_of(component_id)
        override = explicit_tip(component_id)
        if override is not None:
            return joint, override
        kids = children.get(component_id, [])
        preferred = _SPINE_WARD.get(component_id)
        if preferred and preferred in kids:
            return joint, joint_of(preferred)
        if len(kids) == 1:
            return joint, joint_of(kids[0])           # the §6 default, now well-defined
        transform = component.get("transform") or {}
        position = joint
        length = float((transform.get("scale") or [0.0, 0.0, 0.0])[1])
        parent = bone_parent(component_id)
        direction = [0.0, -1.0, 0.0]
        if parent:                                    # decision 3
            p_start, p_end = segment(parent)
            delta = [p_end[i] - p_start[i] for i in range(3)]
            norm = sum(d * d for d in delta) ** 0.5
            if norm > 1e-9:
                direction = [d / norm for d in delta]
        return position, [round(position[i] + direction[i] * length, 5) for i in range(3)]

    bones = []
    for cid in bone_ids:
        joint, tip = segment(cid)
        bones.append({
            "id": cid,
            "parent": bone_parent(cid),
            "jointPos": [round(v, 5) for v in joint],
            "tipPos": [round(v, 5) for v in tip],
            "component": cid,
            "role": "skinned",
            "chain": _rig_chain_of(cid),
        })
    bones.sort(key=lambda b: (b["parent"] is not None, b["id"]))
    return {"version": "1.0", "bindPose": "A", "forward": "+Z", "bones": bones}


# `anatomy.pose.jointAngles` uses anatomical sides: "left" is the CHARACTER's left, which the
# component tree spells `-l`. On a front-facing reference that is the viewer's right.
_POSE_JOINT_TO_COMPONENT = {
    "neck": "neck",
    "leftShoulder": "upper-arm-l", "rightShoulder": "upper-arm-r",
    "leftElbow": "forearm-l", "rightElbow": "forearm-r",
    "leftWrist": "hand-l", "rightWrist": "hand-r",
    "leftHip": "thigh-l", "rightHip": "thigh-r",
    "leftKnee": "shin-l", "rightKnee": "shin-r",
    "leftAnkle": "foot-l", "rightAnkle": "foot-r",
}


def apply_character_pose(component_tree: list, anatomy: dict | None) -> list[str]:
    """Apply `anatomy.pose.jointAngles` as component rotations. Returns the joints applied.

    Why this is worth having even though skinning (WS-C) is unfinished: posing a hierarchy of
    RIGID segments needs no skeleton at all. It needs a real parent chain that propagates
    rotation, which the template now has and which has been measured on executed geometry.
    Skinning buys smooth deformation ACROSS a joint; it is not what makes a limb move. So a
    reference pose (a contrapposto, an arm resting on a sword) is reachable now, and treating
    pose as blocked on skinning was wrong.

    Before this, `pose.jointAngles` was declared in two places and consumed in none -- the same
    "accepted but silently unused" state `anatomy.proportions` was in before `_hu_ratio()`.

    MUST run AFTER `derive_character_rig()`. That function accumulates joints by summing
    parent-local offsets, which is only valid while every ancestor rotation is identity -- i.e.
    at bind pose. It now raises rather than silently mis-deriving if called on a posed tree, so
    the ordering is enforced, not merely documented. The rig stays bind-pose data, which is what
    a skeleton is.
    """
    pose = ((anatomy or {}).get("pose") or {})
    angles = pose.get("jointAngles") or {}
    if not angles:
        return []
    by_id = {c["id"]: c for c in component_tree if isinstance(c, dict) and c.get("id")}
    applied: list[str] = []
    for joint, component_id in _POSE_JOINT_TO_COMPONENT.items():
        value = angles.get(joint)
        if not (isinstance(value, list) and len(value) == 3):
            continue
        if not any(abs(float(v)) > 1e-9 for v in value):
            continue
        component = by_id.get(component_id)
        if component is None:
            continue
        transform = component.setdefault("transform", {})
        transform["rotation"] = [float(v) for v in value]
        applied.append(f"{joint}->{component_id}")
    return applied


def apply_character_template(
    spec: dict,
    anatomy: dict | None = None,
    include_accessories: bool = False,
) -> dict:
    """Swap in the humanoid componentTree, character materials, build passes, and feature
    targets. Object specs are untouched; only called when primaryDomain is character/hybrid."""
    spec["componentTree"] = make_character_component_tree(anatomy, include_accessories)
    # Derived, never authored -- see derive_character_rig()'s docstring. Kept in step with
    # the component tree by construction, so the skeleton cannot drift away from the parts.
    spec["rig"] = derive_character_rig(spec["componentTree"])
    # Strictly after the rig: a skeleton is bind-pose data, and derive_character_rig() now
    # raises if handed a posed tree rather than mis-deriving it quietly.
    posed = apply_character_pose(spec["componentTree"], anatomy)
    if posed:
        spec["rig"]["posedJoints"] = posed
    object_class = spec.setdefault("preSpecAssessment", {}).setdefault("objectClass", {})
    object_class["primaryDomain"] = "character"
    existing = {m.get("id"): m for m in spec.get("materials", []) if isinstance(m, dict)}
    character_materials = CHARACTER_BASE_MATERIALS + (CHARACTER_ACCESSORY_MATERIALS if include_accessories else [])
    for mat in character_materials:
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
    spec["featureReviewTargets"] = make_character_feature_targets(include_accessories)
    pipeline = spec.setdefault("sculptPipeline", {})
    pipeline["passOrder"] = [p["id"] for p in spec["buildPasses"]]
    pipeline["currentPass"] = "blockout"
    return spec


































def make_spec(target_name: str, image: str | None, assessment_payload: dict | None = None) -> dict:
    target_id = slugify(target_name)
    pre_spec_assessment = make_pre_spec_assessment(target_name)
    quality_contract = make_quality_contract()
    local_spec_search = None
    if assessment_payload:
        incoming_assessment = assessment_payload.get("preSpecAssessment")
        incoming_contract = assessment_payload.get("qualityContract")
        incoming_local_spec_search = assessment_payload.get("localSpecSearch")
        if isinstance(incoming_assessment, dict):
            pre_spec_assessment = incoming_assessment
        if isinstance(incoming_contract, dict):
            quality_contract = incoming_contract
        if isinstance(incoming_local_spec_search, dict):
            local_spec_search = incoming_local_spec_search
    spec = {
        "targetName": target_name,
        "targetId": target_id,
        "schemaVersion": "2.1",
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
            "reviewViewpoints": ["front", "three-quarter", "side", "thickness-axis", "long-axis"],
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
                "topologyClass": "assembled-solid",
                "topologyRationale": (
                    "Placeholder root blockout; reclassify per Workstream A's decision tree "
                    "(grimoire/intake/surface_topology.md) once real geometry is authored."
                ),
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
    if local_spec_search is not None:
        spec["localSpecSearch"] = local_spec_search
    if assessment_payload and isinstance(assessment_payload.get("pipelineRouting"), dict):
        spec["pipelineRouting"] = assessment_payload["pipelineRouting"]
    inject_geometry_rules(spec, target_name)
    return spec


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_name", help="Human-readable object name")
    parser.add_argument("--image", help="Reference image path or URL")
    parser.add_argument("--assessment", type=Path, help="Pre-spec assessment JSON from stage2_spec/new_pre_spec_assessment.py")
    parser.add_argument("--out", type=Path, help="Output JSON path")
    parser.add_argument("--augmentation", type=Path,
                        help="spec augmentation artifact published by an installed domain plugin")
    parser.add_argument("--domain", default=None,
                        help="resolved domain id recorded on objectClass; set by domain resolution, not by the artifact")
    parser.add_argument("--force", action="store_true", help="Overwrite output file")
    parser.add_argument("--character", action="store_true",
                        help="Use the humanoid character template (auto-enabled when the assessment primaryDomain is character/hybrid)")
    parser.add_argument("--accessories", action="store_true",
                        help="Character template only: add the opt-in accessory set (glasses, headphones, chest "
                             "decal). Off by default so the baseline humanoid carries no reference-person traits.")
    args = parser.parse_args(argv)
    emit_status(None, next_command="forge/stage2_spec/new_sculpt_spec.py")

    assessment = load_assessment(args.assessment)
    spec = make_spec(args.target_name, args.image, assessment)
    domain = None
    if isinstance(assessment, dict):
        pre = assessment.get("preSpecAssessment", {})
        oc = pre.get("objectClass", {}) if isinstance(pre, dict) else {}
        domain = oc.get("primaryDomain") if isinstance(oc, dict) else None
    incoming_routing = assessment.get("pipelineRouting") if isinstance(assessment, dict) else None
    incoming_classification = incoming_routing.get("classification") if isinstance(incoming_routing, dict) else None
    explicit_track = "character-v1.5" if args.character or domain in {"character", "hybrid"} else None
    routing = None
    if explicit_track is not None or incoming_classification is not None:
        routing = resolve_pipeline_routing(
            explicit_track=explicit_track,
            classification=incoming_classification,
        )
        spec["pipelineRouting"] = routing
        if routing["status"] != "resolved":
            parser.error("pipeline routing requires input: " + "; ".join(routing["conflicts"]))
    # A domain plugin publishes an authoritative recipe as a workspace artifact; the base pulls it
    # here. With none installed the spec keeps the skeleton this pipeline authored and the agent
    # infers the shape from the reference, which is what a run without a domain plugin has always
    # done for any other object.
    # The template and the augmentation are independent contributions, applied in that order: the
    # template authors the track's own spec content (componentTree, rig, buildPasses), and the
    # guarded merge then layers plugin sections on the completed spec. These two used to be joined
    # by an `elif`, which meant any run handed an augmentation file silently skipped the character
    # template -- the emitted spec lost `rig` entirely and still exited 0 (PR #106 review, finding
    # 4: "apply_character_template never runs. Exit 0, plausible-looking spec.").
    if routing is not None and routing["track"] == "character-v1.5":
        anatomy = None
        if isinstance(assessment, dict) and isinstance(assessment.get("preSpecAssessment"), dict):
            anatomy = assessment["preSpecAssessment"].get("anatomy")
        apply_character_template(spec, anatomy, include_accessories=args.accessories)
    if args.augmentation is not None:
        source = args.augmentation.expanduser()
        if not source.is_file():
            # A domain run whose augmentation artifact is missing means the domain's emit step
            # failed or was skipped. Writing the generic skeleton and exiting 0 here is the silent
            # downgrade 02-how-it-works.md draws as FAIL LOUD (PR #106 review, finding 4). Without
            # --domain the flag is speculative plumbing from a generic checklist, and skipping stays
            # correct.
            if args.domain:
                parser.error(
                    f"--domain {args.domain} is resolved but the augmentation artifact "
                    f"{args.augmentation} does not exist; run the domain's emit step instead of "
                    f"continuing on the generic skeleton"
                )
        else:
            try:
                artifact = json.loads(source.read_text(encoding="utf-8"))
                merge_spec_augmentation(spec, artifact, domain_id=args.domain)
            except (OSError, json.JSONDecodeError) as exc:
                parser.error(f"cannot read spec augmentation {args.augmentation}: {exc}")
            except SpecAugmentationError as exc:
                parser.error(str(exc))
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
