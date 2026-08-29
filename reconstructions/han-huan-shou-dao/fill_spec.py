#!/usr/bin/env python3
"""Fill the starter ObjectSculptSpec from 汉代环首刀三视图.jpg."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]
sys.path.insert(0, str(REPO_ROOT))

from forge.stage2_spec.dao_adapter import (
    DaoDimensions,
    assemble_dao_dimensions,
    get_dao_family_adapter,
    validate_dao_component_tree,
)

STARTER = ROOT / "object-sculpt-spec.starter.json"
OUT = ROOT / "object-sculpt-spec.json"
ASSESSMENT = ROOT / "assessment.json"

SOURCE = "/home/nyb/img2threejs/references/chinese-swords/汉代环首刀三视图.jpg"

# Measured from the orthographic sheet (px → world).
# Face view ink: tip x=56, ring end x=1752, span=1696.
# World length 2.30 so the long axis stays readable in the factory units.
PX0 = 56.0
SPAN_PX = 1696.0
WORLD_LEN = 2.30
S = WORLD_LEN / SPAN_PX  # 0.001356

def px(x: float) -> float:
    return (x - PX0) * S


# Guard / blade heel. Face ink at the junction is the collar; the disk stands in YZ.
BLADE_HEEL_X = px(1394.0)  # 1.814
BLADE_LEN = BLADE_HEEL_X
# Face-view midline of the wide blade (y=165..239 → 202).
MID_Y = 202.0


def y_from_px(pixel_y: float) -> float:
    return (MID_Y - pixel_y) * S


def local_from_px(pixel_x: float) -> float:
    """Heel → tip local X for the Y-rotated ground-blade."""
    return (1394.0 - pixel_x) * S


# Heel → tip local X. Spine is +Y, edge is −Y. Tip sits on the spine, not the midline.
# Mid-blade edge is a few px below the ink box so the belly reads; distal climb is dense.
BLADE_STATIONS = [
    [local_from_px(1394), y_from_px(164), y_from_px(238)],
    [local_from_px(1250), y_from_px(165), y_from_px(240)],
    [local_from_px(1100), y_from_px(166), y_from_px(242)],
    [local_from_px(960), y_from_px(166), y_from_px(243)],
    [local_from_px(820), y_from_px(167), y_from_px(243)],
    [local_from_px(700), y_from_px(167), y_from_px(242)],
    [local_from_px(600), y_from_px(168), y_from_px(241)],
    [local_from_px(520), y_from_px(168), y_from_px(240)],
    [local_from_px(460), y_from_px(169), y_from_px(239)],
    [local_from_px(400), y_from_px(169), y_from_px(237)],
    [local_from_px(360), y_from_px(170), y_from_px(236)],
    [local_from_px(320), y_from_px(170), y_from_px(234)],
    [local_from_px(280), y_from_px(171), y_from_px(232)],
    [local_from_px(250), y_from_px(172), y_from_px(229)],
    [local_from_px(220), y_from_px(172), y_from_px(226)],
    [local_from_px(190), y_from_px(173), y_from_px(222)],
    [local_from_px(160), y_from_px(174), y_from_px(216)],
    [local_from_px(140), y_from_px(174), y_from_px(211)],
    [local_from_px(120), y_from_px(175), y_from_px(204)],
    [local_from_px(100), y_from_px(175), y_from_px(197)],
    [local_from_px(88), y_from_px(176), y_from_px(192)],
    [local_from_px(76), y_from_px(176), y_from_px(186)],
    [local_from_px(66), y_from_px(176), y_from_px(181)],
    [local_from_px(56), y_from_px(176), y_from_px(176)],
]
BLADE_THICKNESS = 37.0 * S  # mid side-view height
# Side-view thickness traced heel → tip. The final collapsed station is a true point.
BLADE_THICKNESSES_PX = [
    37, 36, 35, 34, 33, 32, 31, 29, 27, 25, 23, 21,
    19, 17, 15, 13, 11, 9, 7, 5.5, 4, 2.5, 1, 0,
]
BLADE_THICKNESSES = [round(value * S, 4) for value in BLADE_THICKNESSES_PX]
HAMON_FRONT_IDS = tuple(f"hamon-{index}" for index in range(1, 4))
HAMON_BACK_IDS = tuple(f"hamon-back-{index}" for index in range(1, 4))
HAMON_IDS = (*HAMON_FRONT_IDS, *HAMON_BACK_IDS)
# Disk axis along X (rot_x): circle lives in YZ. Face-on it is a thin gilt edge.
GUARD_THICK = 3.0 * S
GUARD_DIAM = 88.0 * S
WRAP_DIAM = 67.0 * S
COLLAR_DIAM = 58.0 * S
FERRULE_DIAM = 56.0 * S
HANDLE_LEN = 215.0 * S
COLLAR_LEN = 14.0 * S
FERRULE_LEN = 10.0 * S
# Slightly horizontal oval pommel (W/H ≈ 1.06), matching the scoring reference.
RING_WIDTH = 0.148
RING_HEIGHT = 0.139
RING_DEPTH = 0.012
INLAY_COUNT = 6

DAO_ADAPTER = get_dao_family_adapter("han-huan-shou")
DAO_DIMENSIONS = DaoDimensions(
    blade_length=BLADE_LEN,
    blade_thickness=BLADE_THICKNESS,
    guard_kind="disk",
    guard_diameter=GUARD_DIAM,
    guard_thickness=GUARD_THICK,
    front_ferrule_length=COLLAR_LEN,
    front_ferrule_diameter=COLLAR_DIAM,
    handle_kind="cord-wrap",
    handle_length=HANDLE_LEN,
    handle_diameter=WRAP_DIAM,
    rear_ferrule_length=FERRULE_LEN,
    rear_ferrule_diameter=FERRULE_DIAM,
    pommel_kind="ring",
    pommel_length=RING_WIDTH,
    inlay_count=INLAY_COUNT,
    front_overlap=0.006,
    handle_overlap=0.010,
    rear_overlap=0.006,
    pommel_overlap=-0.007,
)
DAO_LAYOUT = assemble_dao_dimensions(DAO_DIMENSIONS)
GUARD_X = DAO_LAYOUT["guard"]["x"]
COLLAR_X = DAO_LAYOUT["frontFerrule"]["x"]
HANDLE_X = DAO_LAYOUT["handle"]["x"]
FERRULE_X = DAO_LAYOUT["rearFerrule"]["x"]
RING_X = DAO_LAYOUT["pommel"]["x"]
RING_NECK_LEN = 0.018
RING_NECK_DIAM = 0.045
RING_NECK_X = (FERRULE_X + FERRULE_LEN * 0.5 + RING_X - RING_WIDTH * 0.5) * 0.5
STUD_SEAT_Z = WRAP_DIAM * 0.5 + 0.00015
STUD_Z = WRAP_DIAM * 0.5 + 0.00055
STUD_XS = DAO_LAYOUT["inlayXs"]
STUD_IDS = tuple(f"stud-{chr(ord('a') + index)}" for index in range(INLAY_COUNT))
STUD_SEAT_IDS = tuple(f"stud-seat-{chr(ord('a') + index)}" for index in range(INLAY_COUNT))
STUD_BACK_IDS = tuple(f"stud-back-{chr(ord('a') + index)}" for index in range(INLAY_COUNT))
STUD_SEAT_BACK_IDS = tuple(f"stud-seat-back-{chr(ord('a') + index)}" for index in range(INLAY_COUNT))
ALL_STUD_IDS = (*STUD_IDS, *STUD_BACK_IDS)
ALL_STUD_SEAT_IDS = (*STUD_SEAT_IDS, *STUD_SEAT_BACK_IDS)
RING_ENGRAVING_KINDS = ("outer", "middle", "inner")
RING_ENGRAVING_FRONT_IDS = tuple(f"ring-engraving-{kind}" for kind in RING_ENGRAVING_KINDS)
RING_ENGRAVING_BACK_IDS = tuple(f"ring-engraving-back-{kind}" for kind in RING_ENGRAVING_KINDS)
RING_ENGRAVING_IDS = (*RING_ENGRAVING_FRONT_IDS, *RING_ENGRAVING_BACK_IDS)
ASSEMBLY_SOCKETS = {
    "blade-heel": BLADE_HEEL_X,
    "guard-back": COLLAR_X - COLLAR_LEN * 0.5,
    "front-ferrule-back": HANDLE_X - HANDLE_LEN * 0.5,
    "handle-back": FERRULE_X - FERRULE_LEN * 0.5,
    "rear-ferrule-back": RING_NECK_X - RING_NECK_LEN * 0.5,
    "pommel-anchor": RING_X,
}

def _rounded_square_points(count: int = 32) -> list[list[float]]:
    """Smooth superellipse matching the reference's rounded-square gilt frame."""
    return [
        [
            round(0.5 * math.copysign(abs(math.cos(index / count * math.tau)) ** 0.5, math.cos(index / count * math.tau)), 5),
            round(0.5 * math.copysign(abs(math.sin(index / count * math.tau)) ** 0.5, math.sin(index / count * math.tau)), 5),
        ]
        for index in range(count)
    ]


# Normalized ring silhouette. Short neck is a separate cylinder; the hole stays a real cutout.
# The reference aperture is a tall oval inside the rounded-square outer fitting.
RING_PROFILE = {
    "points": _rounded_square_points(32),
    "depth": 1.0,
    "ovalHoles": [{"cx": 0.04, "cy": 0.0, "rx": 0.28, "ry": 0.42}],
}

# Primary hamon is index 1 (hamon-2 / hamon-back-2). Flanking lines stay thinner, darker, quieter.
HAMON_LINE_SPECS = (
    (-0.0076, 0.18, 0.52, 0.00042, "hamon-steel-secondary"),
    (0.0000, 0.82, 1.00, 0.00092, "hamon-steel"),
    (0.0088, 1.92, 0.40, 0.00036, "hamon-steel-secondary"),
)


def _blade_envelope(world_x: float) -> tuple[float, float, float]:
    samples = [
        (BLADE_HEEL_X - station[0], station[1], station[2], thickness)
        for station, thickness in zip(BLADE_STATIONS, BLADE_THICKNESSES)
    ]
    samples.sort()
    if world_x <= samples[0][0]:
        return samples[0][1], samples[0][2], samples[0][3]
    if world_x >= samples[-1][0]:
        return samples[-1][1], samples[-1][2], samples[-1][3]
    for prev, nxt in zip(samples, samples[1:]):
        if prev[0] <= world_x <= nxt[0]:
            span = nxt[0] - prev[0] or 1.0
            u = (world_x - prev[0]) / span
            return (
                prev[1] + (nxt[1] - prev[1]) * u,
                prev[2] + (nxt[2] - prev[2]) * u,
                prev[3] + (nxt[3] - prev[3]) * u,
            )
    return samples[-1][1], samples[-1][2], samples[-1][3]


def hamon_path(
    vertical_offset: float,
    phase: float,
    face_sign: float = 1.0,
    amplitude: float = 1.0,
) -> list[list[float]]:
    """One etched line with end taper and low-frequency wander — not three parallel rails."""
    points = []
    start_x, end_x = 0.28, 1.72
    samples = 28
    for index in range(samples):
        t = index / (samples - 1)
        world_x = start_x + (end_x - start_x) * t
        spine_y, edge_y, thickness = _blade_envelope(world_x)
        envelope = math.sin(math.pi * t) ** 2
        wave = envelope * amplitude * (
            math.sin(world_x * 6.8 + phase) * 0.0050
            + math.sin(world_x * 3.05 + phase * 0.42) * 0.0034
            + math.sin(world_x * 14.2 + 0.9 - phase * 0.28) * 0.0010
        )
        y = edge_y + (spine_y - edge_y) * 0.68 + vertical_offset + wave
        z = face_sign * (thickness * 0.5 + 0.0008)
        points.append([round(world_x, 5), round(y, 5), round(z, 5)])
    return points


def ring_engraving_path(kind: str, face_sign: float = 1.0) -> list[list[float]]:
    z = face_sign * (RING_DEPTH * 0.5 + 0.0007)
    scale = {"outer": 0.88, "middle": 0.72, "inner": 0.58}[kind]
    hole_cx = 0.04 * RING_WIDTH
    return [
        [
            round(RING_X + hole_cx * (1.0 - scale) + math.cos(index / 32 * math.tau) * 0.5 * scale * RING_WIDTH, 5),
            round(math.sin(index / 32 * math.tau) * 0.5 * scale * RING_HEIGHT, 5),
            z,
        ]
        for index in range(32)
    ]


def wrap_seam_path(direction: int, phase: float) -> list[list[float]]:
    """Trace one shallow cord seam around the X-axis grip."""
    start = HANDLE_X - HANDLE_LEN * 0.5 + 0.006
    end = HANDLE_X + HANDLE_LEN * 0.5 - 0.006
    radius = WRAP_DIAM * 0.493
    turns = 3.25
    points = []
    for index in range(97):
        t = index / 96
        angle = direction * turns * math.tau * t + phase
        points.append(
            [
                round(start + (end - start) * t, 5),
                round(math.sin(angle) * radius, 5),
                round(math.cos(angle) * radius, 5),
            ]
        )
    return points


def rgba(r: int, g: int, b: int, a: float = 1.0) -> str:
    return f"rgba({r}, {g}, {b}, {a})"


def action_profile(role: str, fracture: str, material: str, pivot_mode: str = "center", **extra):
    return {
        "animationRole": role,
        "pivot": {
            "mode": pivot_mode,
            "localPosition": [0, 0, 0],
            "axis": [0, 1, 0],
            "confidence": 0.7,
        },
        "transformChannels": {
            "translate": True,
            "rotate": True,
            "scale": True,
            "bend": False,
            "twist": False,
            "detach": role == "detachable",
            "visibility": True,
            "materialState": True,
        },
        "sockets": extra.pop("sockets", []),
        "collider": extra.pop(
            "collider",
            {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": False, "notes": "box proxy"},
        ),
        "constraints": [],
        "destruction": {
            "breakable": False,
            "fractureGroup": fracture,
            "seamRefs": [],
            "detachableFragments": [],
            "breakImpulse": 0.0,
            "debrisMaterial": material,
        },
    }


def geom(intent: str, **extra):
    descriptor = {
        "topologyIntent": intent,
        "edgeTreatment": extra.pop("edgeTreatment", {"type": "none", "bevelRadius": 0.0, "segments": 1}),
        "deformationStack": extra.pop("deformationStack", []),
        "uvStrategy": extra.pop("uvStrategy", "generated procedural coordinates"),
        "normalStrategy": extra.pop("normalStrategy", "vertex normals from generated geometry"),
    }
    descriptor.update(extra)
    return descriptor


def recipe(dominant, secondary, material_class, confidence, stops=None):
    payload = {
        "dominantAlbedo": dominant,
        "secondaryAlbedo": secondary,
        "materialClass": material_class,
        "materialClassConfidence": confidence,
    }
    if stops:
        payload["colorGradient"] = {"type": "linear", "stops": stops}
    return payload


def contact_attachment(parent_id: str, socket: str, *, overlap: float = 0.02) -> dict:
    """Complete attachment metadata without replacing the authored primitive.

    A non-degenerate localStart/localEnd pair would swap the mesh for a connector
    cylinder. Keep the endpoints coincident so the factory uses transform + scale.
    """
    return {
        "parentId": parent_id,
        "parentSocket": socket,
        "localStart": [0.0, 0.0, 0.0],
        "localEnd": [0.0, 0.0, 0.0],
        "contactType": "sleeve",
        "overlap": overlap,
        "gapTolerance": 0.004,
        "embedDepth": 0.0,
    }


def component(
    cid,
    name,
    *,
    level,
    role,
    primitive,
    parent,
    position,
    scale,
    material,
    rationale,
    topology="assembled-solid",
    rotation=(0.0, 0.0, 0.0),
    importance=0.8,
    confidence=0.72,
    attachment=None,
    sockets=None,
    local_features=None,
    extra_geom=None,
    anim_role="static",
    color=None,
    evidence=None,
    collider=None,
    surface=None,
    explode_with_parent=None,
    fracture_group=None,
    owner_module=None,
    face=None,
    merge_policy=None,
):
    node = {
        "id": cid,
        "name": name,
        "level": level,
        "role": role,
        "importance": importance,
        "confidence": confidence,
        "primitive": primitive,
        "topologyClass": topology,
        "topologyRationale": rationale,
        "geometryDescriptor": geom(f"{name} reconstruction", **(extra_geom or {})),
        "parent": parent,
        "attachment": attachment,
        "dimensions": {
            "width": float(scale[0]),
            "height": float(scale[1]),
            "depth": float(scale[2]),
            "units": "relative",
            "confidence": confidence,
        },
        "transform": {
            "position": list(position),
            "rotation": list(rotation),
            "scale": list(scale),
        },
        "actionProfile": action_profile(anim_role, fracture_group or cid, material, sockets=sockets or [], collider=collider),
        "material": material,
        "materialLayers": [material],
        "deformations": [],
        "joints": [],
        "seams": [],
        "localFeatures": local_features or [],
        "surfaceDetail": surface
        or {
            "macroRoughness": 0.28,
            "microRoughness": 0.18,
            "bumpAmplitude": 0.004,
            "normalPattern": "fine-grind-lines",
            "displacementPattern": "none",
            "occlusionPattern": "contact at fittings",
            "edgeWearPattern": "brighter steel at the cutting edge",
            "notes": "Orthographic illustration: polished steel, not excavated rust.",
        },
        "evidenceRefs": evidence or ["full-object"],
        "details": [],
        "fidelityTier": "blockout",
        "colorMaterialRecipe": color,
    }
    if explode_with_parent:
        node["explodeWithParent"] = explode_with_parent
    if owner_module:
        node["ownerModule"] = owner_module
    if face:
        node["face"] = face
    if merge_policy:
        node["mergePolicy"] = merge_policy
    return node


def steel_material():
    return {
        "id": "polished-steel",
        "name": "Polished blade steel",
        "type": "standard",
        "shaderModel": "MeshStandardMaterial / PBR approximation",
        "baseColor": "#AEB4BA",
        "color": "#AEB4BA",
        "albedo": {
            "dominant": "#AEB4BA",
            "secondary": ["#747B82", "#C8CDD1"],
            "samplingNotes": "Cool grey steel from the three-view plate, not the rusted photo.",
        },
        "colorVariation": {
            "palette": ["#747B82", "#AEB4BA", "#C8CDD1"],
            "pattern": "longitudinal-grind",
            "amplitude": 0.12,
            "heightCorrelation": 0.15,
        },
        "textureResolution": 1024,
        "textureProjection": {
            "mode": "triplanar",
            "repeat": [4.0, 1.0],
            "anisotropy": 8,
            "texelDensityIntent": "Keep grind lines running heel-to-tip.",
        },
        "surfaceFrequencyBands": [
            {"id": "macro", "frequency": 1.2, "amplitude": 0.12, "role": "broad value along the hamon zone"},
            {"id": "meso", "frequency": 8.0, "amplitude": 0.08, "role": "longitudinal grind"},
            {"id": "micro", "frequency": 36.0, "amplitude": 0.04, "role": "fine polish grit"},
        ],
        "roughness": {
            "base": 0.44,
            "variation": 0.08,
            "map": "independent-procedural-field",
            "localResponse": "slightly higher below the hamon, lower on the polished shinogi",
        },
        "metalness": {"base": 0.82, "variation": 0.06},
        "envMapIntensity": 0.45,
        "vertexColors": True,
        "vertexToneFinal": True,
        "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.14, "scale": 22.0, "space": "tangent"},
        "bump": {"pattern": "longitudinal-grind", "amplitude": 0.002, "scale": 20.0},
        "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": False},
        "ambientOcclusion": {
            "cavityStrength": 0.18,
            "contactShadowBias": 0.2,
            "notes": "Keep the blade bright; only darken the guard and wrap contacts.",
        },
        "wear": {"edgeWear": 0.16, "scratches": ["faint heel-to-tip grind"], "chips": []},
        "dirt": {"amount": 0.04, "cavityBias": 0.2, "color": "#6A6864"},
        "patina": {"amount": 0.0, "color": "#AEB4BA", "notes": "Illustrated as clean steel."},
        "localOverrides": [
            {
                "id": "hamon-band",
                "region": "wavy band along the edge, distal two-thirds",
                "albedo": "#D8DCE0",
                "roughness": 0.34,
                "notes": "Hamon is drawn, not a geometry bevel. Blockout keeps it as a value/roughness stain.",
                "evidenceRefs": ["blade-face"],
            },
            {
                "id": "edge-brightening",
                "region": "cutting edge",
                "albedo": "#E8EBEE",
                "roughness": 0.2,
                "evidenceRefs": ["blade-face"],
            },
        ],
        "shaderNotes": [
            "Polished steel: high metalness, mid-low roughness. Not rust, not chrome mirror.",
            "Do not reuse albedo as roughness.",
        ],
        "notes": "Reconstruct the three-view illustration, not the excavated relic photo.",
    }


def gilt_material():
    return {
        "id": "gilt-bronze",
        "name": "Gilt bronze fittings",
        "type": "standard",
        "shaderModel": "MeshStandardMaterial / PBR approximation",
        "baseColor": "#C4A46A",
        "color": "#C4A46A",
        "albedo": {
            "dominant": "#C4A46A",
            "secondary": ["#8A7040", "#E0C890"],
            "samplingNotes": "Guard, collars, and ring share this yellow-metal family.",
        },
        "colorVariation": {
            "palette": ["#C4A46A", "#8A7040", "#E0C890"],
            "pattern": "soft-cast-mottle",
            "amplitude": 0.14,
            "heightCorrelation": 0.2,
        },
        "textureResolution": 1024,
        "textureProjection": {
            "mode": "triplanar",
            "repeat": [1.0, 1.0],
            "anisotropy": 4,
            "texelDensityIntent": "Cast-metal scale on small fittings; do not stretch around the ring.",
        },
        "surfaceFrequencyBands": [
            {"id": "macro", "frequency": 2.0, "amplitude": 0.14, "role": "cast value shift"},
            {"id": "meso", "frequency": 10.0, "amplitude": 0.08, "role": "engraving suggestion"},
            {"id": "micro", "frequency": 32.0, "amplitude": 0.04, "role": "fine grit"},
        ],
        "roughness": {
            "base": 0.38,
            "variation": 0.1,
            "map": "independent-procedural-field",
            "localResponse": "duller in engraved recesses on the ring",
        },
        "metalness": {"base": 0.74, "variation": 0.08},
        "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.18, "scale": 10.0, "space": "tangent"},
        "bump": {"pattern": "cast-engraving", "amplitude": 0.005, "scale": 10.0},
        "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": False},
        "ambientOcclusion": {
            "cavityStrength": 0.28,
            "contactShadowBias": 0.22,
            "notes": "Darken the ring inner diameter and guard/handle seams.",
        },
        "wear": {"edgeWear": 0.18, "scratches": [], "chips": []},
        "dirt": {"amount": 0.08, "cavityBias": 0.3, "color": "#5A4030"},
        "localOverrides": [
            {
                "id": "ring-recess",
                "region": "inner and engraved face of the ring",
                "albedo": "#8A7040",
                "roughness": 0.48,
                "evidenceRefs": ["pommel-ring"],
            }
        ],
        "shaderNotes": [
            "Yellow metal fittings, not painted plastic.",
            "Keep hue from shifting blue under ACES.",
        ],
        "notes": "Illustration gilt; alloy is not specified.",
    }


def wrap_material():
    return {
        "id": "cord-wrap",
        "name": "Dark cord-wrapped grip",
        "type": "standard",
        "shaderModel": "MeshStandardMaterial / PBR approximation",
        "baseColor": "#3A2418",
        "color": "#3A2418",
        "albedo": {
            "dominant": "#3A2418",
            "secondary": ["#241610", "#5A3A28"],
            "samplingNotes": "Dark brown wrap with diamond gilt studs sitting on top.",
        },
        "colorVariation": {
            "palette": ["#241610", "#3A2418", "#5A3A28"],
            "pattern": "helical-wrap",
            "amplitude": 0.16,
            "heightCorrelation": 0.35,
        },
        "textureResolution": 1024,
        "textureProjection": {
            "mode": "cylindrical",
            "repeat": [1.0, 8.0],
            "anisotropy": 4,
            "texelDensityIntent": "Wrap turns run around the grip, not along the blade.",
        },
        "surfaceFrequencyBands": [
            {"id": "macro", "frequency": 3.0, "amplitude": 0.22, "role": "wrap-turn ridges"},
            {"id": "meso", "frequency": 12.0, "amplitude": 0.12, "role": "cord twist"},
            {"id": "micro", "frequency": 40.0, "amplitude": 0.05, "role": "fiber grit"},
        ],
        "roughness": {
            "base": 0.78,
            "variation": 0.1,
            "map": "independent-procedural-field",
            "localResponse": "higher in wrap valleys",
        },
        "metalness": {"base": 0.02, "variation": 0.02},
        "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.35, "scale": 14.0, "space": "tangent"},
        "bump": {"pattern": "cord-wrap", "amplitude": 0.012, "scale": 12.0},
        "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": False},
        "ambientOcclusion": {
            "cavityStrength": 0.4,
            "contactShadowBias": 0.25,
            "notes": "Darken wrap valleys and the stud sockets.",
        },
        "wear": {"edgeWear": 0.08, "scratches": [], "chips": []},
        "dirt": {"amount": 0.12, "cavityBias": 0.4, "color": "#1A100C"},
        "localOverrides": [
            {
                "id": "wrap-valley",
                "region": "helical recesses between cord turns",
                "albedo": "#241610",
                "roughness": 0.86,
                "evidenceRefs": ["handle"],
            }
        ],
        "shaderNotes": [
            "Dielectric wrap, not metal.",
            "Do not reuse albedo as roughness.",
        ],
        "notes": "Dark cord or leather wrap as drawn; blockout is a cylinder plus six inlays.",
    }


def wrap_seam_material():
    material = wrap_material()
    material.update(
        {
            "id": "wrap-seam",
            "name": "Recessed cord-wrap seam",
            "baseColor": "#21140E",
            "color": "#21140E",
        }
    )
    material["albedo"] = {
        "dominant": "#21140E",
        "secondary": ["#160D09", "#322018"],
        "samplingNotes": "Dark crossing valleys between the illustrated wrap turns.",
    }
    material["notes"] = "Thin procedural seam geometry set into the grip surface."
    return material


def hamon_material():
    material = steel_material()
    material.update(
        {
            "id": "hamon-steel",
            "name": "Polished hamon line",
            "baseColor": "#B8BFC6",
            "color": "#B8BFC6",
            "vertexColors": False,
            "vertexToneFinal": False,
            "envMapIntensity": 0.22,
        }
    )
    material["albedo"] = {
        "dominant": "#B8BFC6",
        "secondary": ["#9AA2A9", "#C9CED3"],
        "samplingNotes": "Primary etched hamon: cooler than chrome, not a white highlight rail.",
    }
    material["colorVariation"] = {
        "palette": ["#9AA2A9", "#B8BFC6", "#C9CED3"],
        "pattern": "fine-etched-line",
        "amplitude": 0.05,
        "heightCorrelation": 0.0,
    }
    material["roughness"] = {"base": 0.48, "variation": 0.06, "map": "independent-procedural-field"}
    material["metalness"] = {"base": 0.70, "variation": 0.05}
    return material


def hamon_secondary_material():
    material = hamon_material()
    material.update(
        {
            "id": "hamon-steel-secondary",
            "name": "Quiet hamon companion line",
            "baseColor": "#8E959C",
            "color": "#8E959C",
            "envMapIntensity": 0.12,
        }
    )
    material["albedo"] = {
        "dominant": "#8E959C",
        "secondary": ["#7A8188", "#A4ABB1"],
        "samplingNotes": "Thinner, darker companion etch; must not compete with the primary hamon.",
    }
    material["colorVariation"] = {
        "palette": ["#7A8188", "#8E959C", "#A4ABB1"],
        "pattern": "fine-etched-line",
        "amplitude": 0.04,
        "heightCorrelation": 0.0,
    }
    material["roughness"] = {"base": 0.58, "variation": 0.05, "map": "independent-procedural-field"}
    material["metalness"] = {"base": 0.62, "variation": 0.04}
    return material


def engraving_material():
    material = gilt_material()
    material.update(
        {
            "id": "gilt-engraving",
            "name": "Dark gilt engraving recess",
            "baseColor": "#6F5427",
            "color": "#6F5427",
            "envMapIntensity": 0.25,
        }
    )
    material["albedo"] = {
        "dominant": "#6F5427",
        "secondary": ["#4F391A", "#8A7040"],
        "samplingNotes": "Dark recess tone sampled conceptually from the ring engraving.",
    }
    material["colorVariation"] = {
        "palette": ["#4F391A", "#6F5427", "#8A7040"],
        "pattern": "engraved-recess",
        "amplitude": 0.06,
        "heightCorrelation": 0.0,
    }
    material["roughness"] = {"base": 0.55, "variation": 0.08, "map": "independent-procedural-field"}
    return material


WRAP_SURFACE = {
    "macroRoughness": 0.78,
    "microRoughness": 0.86,
    "bumpAmplitude": 0.012,
    "normalPattern": "cord-wrap",
    "displacementPattern": "wrap-turn ridges",
    "occlusionPattern": "valleys between turns",
    "edgeWearPattern": "none",
    "notes": "Dark wrapped grip from the three-view plate.",
}

GILT_SURFACE = {
    "macroRoughness": 0.4,
    "microRoughness": 0.5,
    "bumpAmplitude": 0.005,
    "normalPattern": "cast-engraving",
    "displacementPattern": "none",
    "occlusionPattern": "inner ring and seams",
    "edgeWearPattern": "brighter gilt on outer rim",
    "notes": "Gilt fittings from the three-view plate.",
}


def main() -> None:
    preserved = {}
    if OUT.exists():
        previous = json.loads(OUT.read_text(encoding="utf-8"))
        for key in ("reviewHistory", "visualEvidence", "tier1Results", "sculptPipeline"):
            if key in previous:
                preserved[key] = previous[key]
    spec = json.loads(STARTER.read_text(encoding="utf-8"))

    spec["sourceImage"] = SOURCE
    spec["suitability"] = "conditional"
    spec["scores"] = {
        "object_isolation": 3,
        "silhouette_readability": 3,
        "depth_inference": 3,
        "primitive_decomposition": 3,
        "material_procedurality": 2,
        "occlusion_risk": 1,
        "interaction_fit": 2,
    }
    spec["assumptions"] = [
        "Authoritative reference is the orthographic three-view plate, not the rusted floor photo.",
        "Subject is a Han-style huan-shou dao as drawn: single edge, disk guard, wrapped grip, gilt ring.",
        "Hamon and ring engraving use shallow procedural tube relief rather than copied source pixels.",
        "Diamond handle inlays are six gilt plaques per face, not a full menuki set.",
        "First-look fidelity is stylized / approximate.",
    ]
    spec["risks"] = [
        "The earlier rusted photo contradicts this plate (no wrap, no guard, excavated oxide). Mixing them will fail identity.",
        "Disk guard is edge-on in the face view and easy to miss if only the top row is used.",
        "Ring engraving and hamon will look painted if treated as albedo-only later.",
        "Constant-thickness slab would still read as a toy; grind stays in the ground-blade stations.",
    ]
    spec["coordinateFrame"] = {
        "front": "blade face matching the top row of the three-view",
        "up": "spine (+Y)",
        "right": "toward ring pommel (+X)",
        "scaleReference": "overall length 2.30 world units from the 1696 px face-view span",
    }
    spec["silhouette"] = {
        "boundingShape": "long single-edged bar, circular disk guard, cylindrical wrap, shallow gilt ring pommel",
        "aspectRatios": ["length:width ~ 22:1", "width:thickness ~ 2:1 at mid-blade"],
        "symmetry": "bilateral about the long axis in plan; not rotational",
        "dominantCurves": [
            "straight spine",
            "edge rising to meet the spine at the tip",
            "closed circular pommel hole",
            "disk guard read as a circle in the side view",
        ],
        "negativeSpaces": ["ring aperture"],
        "landmarks": [
            "clip-point tip",
            "hamon along the edge",
            "disk guard",
            "wrapped grip with six diamond inlays",
            "gilt ring",
        ],
    }
    spec["viewEvidence"] = [
        {
            "id": "full-object",
            "view": "orthographic-face",
            "imageRegion": {"x": 0.03, "y": 0.12, "width": 0.94, "height": 0.16, "units": "normalized"},
            "observations": ["Face view: polished blade, gilt collar, dark wrap, gilt ring."],
            "confidence": 0.9,
        },
        {
            "id": "blade-face",
            "view": "orthographic-face",
            "imageRegion": {"x": 0.03, "y": 0.12, "width": 0.74, "height": 0.16, "units": "normalized"},
            "observations": ["Single edge, hamon, distal taper to a point near the spine."],
            "confidence": 0.88,
        },
        {
            "id": "handle",
            "view": "orthographic-face",
            "imageRegion": {"x": 0.78, "y": 0.12, "width": 0.12, "height": 0.16, "units": "normalized"},
            "observations": ["Dark helical wrap with six diamond gilt inlays."],
            "confidence": 0.84,
        },
        {
            "id": "pommel-ring",
            "view": "orthographic-face",
            "imageRegion": {"x": 0.90, "y": 0.10, "width": 0.08, "height": 0.18, "units": "normalized"},
            "observations": ["Closed gilt ring with a real aperture and engraved face."],
            "confidence": 0.9,
        },
        {
            "id": "side-guard",
            "view": "orthographic-side",
            "imageRegion": {"x": 0.76, "y": 0.42, "width": 0.08, "height": 0.12, "units": "normalized"},
            "observations": ["Side view reveals a circular disk guard hidden edge-on in the face view."],
            "confidence": 0.86,
        },
    ]

    pre = spec["preSpecAssessment"]
    pre["objectClass"] = {
        "primaryType": "single-edged ring-pommel dao",
        "primaryDomain": "object",
        "formLanguage": ["hard-surface", "illustrated-metal"],
        "structureKind": ["compound object", "linear hierarchy"],
        "motionPotential": ["static prop", "whole-object transform"],
        "materialFamilies": ["metal", "fabric"],
        "notes": (
            "Orthographic reconstruction of a Han-style huan-shou dao: polished steel blade, "
            "disk guard, cord wrap, gilt ring. Not the rusted floor relic."
        ),
    }
    pre["complexity"] = {
        "tier": "moderate",
        "scores": {
            "silhouetteComplexity": 2,
            "componentCount": 3,
            "hierarchyDepth": 2,
            "repetitionDensity": 1,
            "materialLayerCount": 3,
            "localDetailDensity": 2,
            "occlusionRisk": 1,
            "actionReadinessNeed": 1,
        },
        "estimatedCounts": {
            "macroComponents": 3,
            "mesoComponents": 5,
            "microFeatureGroups": 3,
            "materialLayers": 3,
            "repetitionSystems": 1,
        },
        "reasoning": [
            "Blade, wrap, and ring are the identity macros; guard and ferrules are meso.",
            "Six diamond inlays are a small repeated set, not a fastener system.",
            "Three-view plate removes the hidden-thickness problem of the rust photo.",
        ],
    }
    pre["specDepthDecision"] = {
        "requiredDepth": "moderate",
        "minimumComponentLevels": ["macro", "meso"],
        "needsRepetitionSystems": False,
        "needsMaterialLocalOverrides": True,
        "needsMultipleReviewViews": True,
        "needsActionReadyHierarchy": True,
        "rationale": "Moderate: three materials, distinct fittings, ring hole must stay real geometry.",
    }
    pre["unknownsToResolveBeforeImplementation"] = []
    pre["detailInventory"] = {
        "scanMethod": "component-zones",
        "targetMinDetails": 6,
        "note": "Identity details from the three-view plate.",
        "details": [
            {
                "id": "ring-aperture",
                "kind": "hole",
                "region": "proximal gilt ring",
                "affects": ["silhouette", "topology"],
                "scale": 0.12,
                "confidence": 0.92,
                "evidenceRef": "pommel-ring",
                "mapsTo": {"type": "feature", "ref": "ring-aperture"},
                "realization": "geometry",
            },
            {
                "id": "disk-guard",
                "kind": "contour",
                "region": "blade/handle junction",
                "affects": ["silhouette", "hierarchy"],
                "scale": 0.08,
                "confidence": 0.86,
                "evidenceRef": "side-guard",
                "mapsTo": {"type": "feature", "ref": "disk-guard"},
                "realization": "geometry",
            },
            {
                "id": "distal-taper",
                "kind": "bevel",
                "region": "distal third",
                "affects": ["silhouette", "geometry"],
                "scale": 0.28,
                "confidence": 0.88,
                "evidenceRef": "blade-face",
                "mapsTo": {"type": "feature", "ref": "distal-taper"},
                "realization": "geometry",
            },
            {
                "id": "edge-grind",
                "kind": "bevel",
                "region": "cutting edge along -Y",
                "affects": ["thickness", "lighting"],
                "scale": 0.08,
                "confidence": 0.7,
                "evidenceRef": "blade-face",
                "mapsTo": {"type": "feature", "ref": "edge-grind"},
                "realization": "geometry",
            },
            {
                "id": "wrapped-grip",
                "kind": "seam",
                "region": "handle",
                "affects": ["material", "hierarchy"],
                "scale": 0.18,
                "confidence": 0.84,
                "evidenceRef": "handle",
                "mapsTo": {"type": "feature", "ref": "wrapped-grip"},
                "realization": "geometry",
            },
            {
                "id": "diamond-inlays",
                "kind": "fastener",
                "region": "handle face",
                "affects": ["material", "silhouette"],
                "scale": 0.03,
                "confidence": 0.78,
                "evidenceRef": "handle",
                "mapsTo": {"type": "feature", "ref": "diamond-inlays"},
                "realization": "geometry",
            },
            {
                "id": "hamon-line",
                "kind": "stain",
                "region": "blade edge band",
                "affects": ["albedo", "roughness"],
                "scale": 0.35,
                "confidence": 0.7,
                "evidenceRef": "blade-face",
                "mapsTo": {"type": "component", "ref": "hamon-2"},
                "realization": "geometry",
            },
            {
                "id": "ring-engraving",
                "kind": "stain",
                "region": "gilt ring face",
                "affects": ["albedo", "roughness"],
                "scale": 0.06,
                "confidence": 0.6,
                "evidenceRef": "pommel-ring",
                "mapsTo": {"type": "component", "ref": "ring-engraving-inner"},
                "realization": "geometry",
            },
        ],
    }

    contract = spec["qualityContract"]
    contract["qualityBar"] = "stylized-approximate"
    contract["definitionOfDone"] = [
        "A viewer recognises a Han ring-pommel dao from the three-view: gilt ring with a real hole, disk guard, wrapped grip, single-edged polished blade.",
        "Ring aperture is a real hole, not a dark decal.",
        "Blade has a grind wedge and a tip that meets near the spine; it must not read as a constant-thickness card.",
        "Guard, wrap, and gilt fittings are separate parts and materials from the steel.",
        "Do not reconstruct the rusted floor relic when reviewing against this plate.",
    ]
    contract["featureGroups"] = [
        group
        for group in contract.get("featureGroups", [])
        if group.get("id") not in {"huan-shou-identity"}
    ]
    contract["featureGroups"].extend(
        [
            {
                "id": "huan-shou-identity",
                "name": "Ring pommel dao from the three-view plate",
                "required": True,
                "qualityCriteria": [
                    "Closed gilt profile pommel sits in the blade-face plane.",
                    "A circular disk guard is present (visible in the side view).",
                    "Handle is a dark wrap with six visible diamond gilt inlays per face, not a bare tang.",
                ],
                "evidenceRefs": ["pommel-ring", "handle", "side-guard", "full-object"],
                "failureModes": [
                    "Rusted unguarded relic from the floor photo",
                    "Missing ring or filled-in hole",
                    "Double-edged jian section",
                ],
            }
        ]
    )
    extra_must_not = [
        "Do not rebuild the rusted unguarded floor relic against this three-view plate.",
        "Do not omit the disk guard that the side view shows.",
        "Do not build a double-edged diamond jian section.",
        "Do not leave the ring as a painted disk.",
        "Blade components must vary in thickness from spine to edge and taper from heel toward the tip (source: grimoire/build/geometry_patterns.md).",
    ]
    contract["mustNotDo"] = list(dict.fromkeys(list(contract.get("mustNotDo") or []) + extra_must_not))

    spec["qualityTargets"]["targetFidelity"] = 0.85
    spec["qualityTargets"]["mustMatch"] = [
        "single-edged polished blade with distal taper",
        "closed gilt ring pommel",
        "disk guard",
        "dark wrapped grip",
    ]
    spec["qualityTargets"]["niceToHave"] = [
        "readable hamon",
        "ring engraving",
        "exact wrap-turn count",
    ]

    spec["featureReviewTargets"] = [
        {
            "id": "dao-silhouette",
            "name": "Single-edged dao silhouette from the plate",
            "tier": "critical",
            "passIds": ["blockout"],
            "minimumScore": 0.8,
            "mustPass": True,
            "componentRefs": ["blade"],
            "evidenceRefs": ["full-object", "blade-face"],
        },
        {
            "id": "huan-shou-ring",
            "name": "Closed gilt ring pommel",
            "tier": "critical",
            "passIds": ["blockout", "structural-pass"],
            "minimumScore": 0.8,
            "mustPass": True,
            "componentRefs": ["ring"],
            "evidenceRefs": ["pommel-ring"],
        },
        {
            "id": "disk-guard",
            "name": "Circular disk guard",
            "tier": "critical",
            "passIds": ["blockout", "structural-pass"],
            "minimumScore": 0.75,
            "mustPass": True,
            "componentRefs": ["guard"],
            "evidenceRefs": ["side-guard"],
        },
        {
            "id": "wrapped-handle",
            "name": "Dark wrapped grip with diamond inlays",
            "tier": "important",
            "passIds": ["structural-pass", "material-pass"],
            "minimumScore": 0.7,
            "mustPass": False,
            "componentRefs": ["handle", *ALL_STUD_IDS],
            "evidenceRefs": ["handle"],
        },
        {
            "id": "steel-material",
            "name": "Polished steel with restrained grind response",
            "tier": "critical",
            "passIds": ["material-pass"],
            "minimumScore": 0.8,
            "mustPass": True,
            "componentRefs": ["blade", *HAMON_IDS],
            "evidenceRefs": ["full-object", "blade-face"],
        },
        {
            "id": "gilt-material",
            "name": "Gilt fittings with cast-metal variation",
            "tier": "important",
            "passIds": ["material-pass"],
            "minimumScore": 0.7,
            "mustPass": False,
            "componentRefs": ["guard", "collar", *ALL_STUD_IDS, "ferrule", "ring-neck", "ring"],
            "evidenceRefs": ["side-guard", "handle", "pommel-ring"],
        },
        {
            "id": "surface-locality",
            "name": "Cavity dirt and high-point wear survive relighting",
            "tier": "critical",
            "passIds": ["surface-pass"],
            "minimumScore": 0.8,
            "mustPass": True,
            "componentRefs": ["blade", "guard", "handle", "ring"],
            "evidenceRefs": ["blade-face", "handle", "pommel-ring"],
        },
        {
            "id": "lighting-readability",
            "name": "Key/fill/rim lighting preserves form and material readability",
            "tier": "critical",
            "passIds": ["lighting-pass"],
            "minimumScore": 0.8,
            "mustPass": True,
            "componentRefs": ["blade", "guard", "handle", "ring"],
            "evidenceRefs": ["full-object", "blade-face", "handle", "pommel-ring"],
        },
        {
            "id": "interaction-readiness",
            "name": "Stable pivots and integral module ownership survive runtime actions",
            "tier": "critical",
            "passIds": ["interaction-pass"],
            "minimumScore": 0.8,
            "mustPass": True,
            "componentRefs": ["root", "blade", "guard", "collar", "handle", "ferrule", "ring"],
            "evidenceRefs": ["full-object", "handle", "pommel-ring"],
        },
        {
            "id": "optimization-budget",
            "name": "Runtime budgets hold without removing selectable identity details",
            "tier": "critical",
            "passIds": ["optimization-pass"],
            "minimumScore": 0.8,
            "mustPass": True,
            "componentRefs": ["root", "blade", "handle", "ring"],
            "evidenceRefs": ["full-object", "handle", "pommel-ring"],
        },
        {
            "id": "refined-inlays",
            "name": "Recessed gilt grip inlays",
            "tier": "important",
            "passIds": ["form-refinement"],
            "minimumScore": 0.7,
            "mustPass": False,
            "componentRefs": [*ALL_STUD_SEAT_IDS, *ALL_STUD_IDS],
            "evidenceRefs": ["handle"],
        },
        {
            "id": "hamon-character",
            "name": "Controlled irregular hamon band",
            "tier": "important",
            "passIds": ["form-refinement"],
            "minimumScore": 0.7,
            "mustPass": False,
            "componentRefs": [*HAMON_IDS],
            "evidenceRefs": ["blade-face"],
        },
        {
            "id": "ring-ornament",
            "name": "Layered ring engraving",
            "tier": "important",
            "passIds": ["form-refinement"],
            "minimumScore": 0.7,
            "mustPass": False,
            "componentRefs": [*RING_ENGRAVING_IDS],
            "evidenceRefs": ["pommel-ring"],
        },
    ]

    steel = rgba(196, 200, 204)
    steel_dark = rgba(154, 160, 166)
    steel_light = rgba(228, 231, 234)
    gilt = rgba(196, 164, 106)
    gilt_dark = rgba(138, 112, 64)
    wrap = rgba(58, 36, 24)
    wrap_dark = rgba(36, 22, 16)
    rot_x = (0.0, 0.0, 1.5707963267948966)

    spec["familyAdapter"] = DAO_ADAPTER.component_tree_contract()
    inlay_seat_components = [
        component(
            component_id,
            f"Diamond inlay seat {index + 1}" if face_name == "front" else f"Diamond back inlay seat {index + 1}",
            level="micro",
            role="detail",
            primitive="box",
            parent="root",
            position=(STUD_XS[index], 0.0, face_sign * STUD_SEAT_Z),
            scale=(0.029, 0.021, 0.0010),
            rotation=(0.0, 0.0, 0.7853981633974483),
            material="wrap-seam",
            rationale=f"Dark shallow socket leaves a narrow wrap-colored border around the {face_name} gilt lozenge.",
            importance=0.48,
            confidence=0.78,
            color=recipe(wrap_dark, rgba(22, 13, 9), "fabric", 0.72),
            evidence=["handle"],
            surface=WRAP_SURFACE,
            explode_with_parent="handle",
            fracture_group="handle",
            owner_module="handle",
            face=face_name,
            merge_policy="bake",
        )
        for face_name, face_sign, component_ids in (
            ("front", 1.0, STUD_SEAT_IDS),
            ("back", -1.0, STUD_SEAT_BACK_IDS),
        )
        for index, component_id in enumerate(component_ids)
    ]
    inlay_components = [
        component(
            component_id,
            f"Diamond inlay {index + 1}" if face_name == "front" else f"Diamond back inlay {index + 1}",
            level="micro",
            role="detail",
            primitive="box",
            parent="root",
            position=(STUD_XS[index], 0.0, face_sign * STUD_Z),
            scale=(0.022, 0.013, 0.0010),
            rotation=(0.0, 0.0, 0.7853981633974483),
            material="gilt-bronze",
            rationale=f"Small gilt lozenge sits nearly flush inside a larger dark {face_name} wrap socket.",
            importance=0.55,
            confidence=0.82,
            color=recipe(gilt, gilt_dark, "metal", 0.7),
            evidence=["handle"],
            surface=GILT_SURFACE,
            explode_with_parent="handle",
            fracture_group="handle",
            owner_module="handle",
            face=face_name,
            merge_policy="keep",
        )
        for face_name, face_sign, component_ids in (
            ("front", 1.0, STUD_IDS),
            ("back", -1.0, STUD_BACK_IDS),
        )
        for index, component_id in enumerate(component_ids)
    ]
    hamon_components = [
        component(
            component_id,
            f"Hamon {face_name} line {index + 1}",
            level="micro",
            role="detail",
            primitive="tube",
            parent="root",
            attachment=contact_attachment("root", "blade-heel", overlap=0.001),
            position=(0.0, 0.0, 0.0),
            scale=(1.0, 1.0, 1.0),
            material=material_id,
            rationale=(
                f"Primary hamon plus quieter companion etches on the {face_name} face; "
                "low-frequency wander with tapered ends, not three equal highlight rails."
            ),
            topology="continuous-sculpt",
            importance=0.78 if index == 1 else 0.58,
            confidence=0.78 if face_sign > 0 else 0.68,
            extra_geom={
                "tubePath": {
                    "points": hamon_path(offset, phase + phase_shift, face_sign, amplitude),
                    "radius": radius,
                    "radialSegments": 5,
                    "closed": False,
                }
            },
            color=recipe(
                rgba(184, 191, 198) if index == 1 else rgba(142, 149, 156),
                rgba(154, 162, 169) if index == 1 else rgba(122, 129, 136),
                "metal",
                0.72 if index == 1 else 0.6,
            ),
            evidence=["blade-face"],
            explode_with_parent="blade",
            fracture_group="blade",
            owner_module="blade",
            face=face_name,
            merge_policy="bake",
        )
        for face_name, face_sign, phase_shift, component_ids in (
            ("front", 1.0, 0.0, HAMON_FRONT_IDS),
            ("back", -1.0, 0.55, HAMON_BACK_IDS),
        )
        for index, (component_id, (offset, phase, amplitude, radius, material_id)) in enumerate(
            zip(component_ids, HAMON_LINE_SPECS)
        )
    ]
    ring_engraving_components = [
        component(
            component_id,
            f"Ring engraving {kind}" if face_name == "front" else f"Ring back engraving {kind}",
            level="micro",
            role="detail",
            primitive="tube",
            parent="root",
            attachment=contact_attachment("root", "pommel-anchor", overlap=0.001),
            position=(0.0, 0.0, 0.0),
            scale=(1.0, 1.0, 1.0),
            material="gilt-engraving",
            rationale=f"Closed dark line follows the ring {face_name} face as shallow engraved relief.",
            topology="continuous-sculpt",
            importance=0.68,
            confidence=0.74,
            extra_geom={
                "tubePath": {
                    "points": ring_engraving_path(kind, face_sign),
                    "radius": 0.0011,
                    "radialSegments": 5,
                    "closed": True,
                }
            },
            color=recipe(rgba(111, 84, 39), rgba(79, 57, 26), "metal", 0.7),
            evidence=["pommel-ring"],
            explode_with_parent="ring",
            fracture_group="ring",
            owner_module="ring",
            face=face_name,
            merge_policy="bake",
        )
        for face_name, face_sign, component_ids in (
            ("front", 1.0, RING_ENGRAVING_FRONT_IDS),
            ("back", -1.0, RING_ENGRAVING_BACK_IDS),
        )
        for kind, component_id in zip(RING_ENGRAVING_KINDS, component_ids)
    ]
    wrap_seam_components = [
        component(
            f"wrap-seam-{index + 1}",
            f"Cord wrap seam {index + 1}",
            level="micro",
            role="detail",
            primitive="tube",
            parent="root",
            attachment=contact_attachment("root", "blade-heel", overlap=0.001),
            position=(0.0, 0.0, 0.0),
            scale=(1.0, 1.0, 1.0),
            material="wrap-seam",
            rationale="A shallow helical valley makes the wrapped grip read in face and orbit views.",
            topology="continuous-sculpt",
            importance=0.7,
            confidence=0.8,
            extra_geom={
                "tubePath": {
                    "points": wrap_seam_path(direction, phase),
                    "radius": 0.00115,
                    "radialSegments": 6,
                    "closed": False,
                }
            },
            color=recipe(rgba(33, 20, 14), rgba(22, 13, 9), "fabric", 0.78),
            evidence=["handle"],
            surface=WRAP_SURFACE,
            explode_with_parent="handle",
            fracture_group="handle",
            owner_module="handle",
            face="wrap",
            merge_policy="bake",
        )
        for index, (direction, phase) in enumerate(((1, 0.35), (-1, -0.35)))
    ]

    spec["componentTree"] = [
        component(
            "root",
            "Han Huan-Shou Dao assembly",
            level="macro",
            role="assembly",
            primitive="box",
            parent=None,
            position=(0.0, 0.0, 0.0),
            scale=(0.001, 0.001, 0.001),
            material="polished-steel",
            rationale="Assembly pivot only; keep the cube below visibility so it cannot sit on the blade.",
            importance=1.0,
            confidence=0.9,
            anim_role="root",
            color=recipe(steel, steel_dark, "metal", 0.8),
            evidence=["full-object"],
            collider={
                "type": "box",
                "offset": [1.15, 0, 0],
                "scale": [2.4, 0.2, 0.08],
                "isTrigger": False,
                "notes": "whole-weapon proxy",
            },
            sockets=[
                {"id": socket_id, "localPosition": [socket_x, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}
                for socket_id, socket_x in ASSEMBLY_SOCKETS.items()
            ],
        ),
        component(
            "blade",
            "Dao blade",
            level="macro",
            role="blade",
            primitive="ground-blade",
            parent="root",
            position=(BLADE_HEEL_X, 0.0, 0.0),
            scale=(1.0, 1.0, 1.0),
            rotation=(0.0, 3.141592653589793, 0.0),
            material="polished-steel",
            rationale="Single-edged bar lofted from the face-view stations; tip climbs to the spine. Not a rusted card and not a jian diamond.",
            importance=1.0,
            confidence=0.88,
            extra_geom={
                "bladeSpec": {
                    "stations": BLADE_STATIONS,
                    "thickness": round(BLADE_THICKNESS, 4),
                    "thicknesses": BLADE_THICKNESSES,
                    "grindFrac": 0.42,
                    "swedgeFromTipFrac": 0.0,
                    "edgeTone": 0.58,
                },
                "edgeTreatment": {"type": "chamfer", "bevelRadius": 0.003, "segments": 2},
            },
            local_features=[
                {"id": "distal-taper", "kind": "bevel", "notes": "Edge rises to meet the spine; last stations collapse to a point."},
                {"id": "edge-grind", "kind": "bevel", "notes": "Primary bevel on -Y from the side view thickness."},
            ],
            color=recipe(
                steel,
                steel_light,
                "metal",
                0.84,
                stops=[
                    {"offset": 0.0, "color": steel_dark},
                    {"offset": 0.55, "color": steel},
                    {"offset": 1.0, "color": steel_light},
                ],
            ),
            evidence=["full-object", "blade-face"],
            collider={
                "type": "box",
                "offset": [BLADE_LEN * 0.5, 0, 0],
                "scale": [BLADE_LEN, 0.11, 0.05],
                "isTrigger": False,
                "notes": "blade proxy in local heel-to-tip X",
            },
        ),
        *hamon_components,
        component(
            "guard",
            "Disk guard",
            level="meso",
            role="body",
            primitive="cylinder",
            parent="root",
            position=(GUARD_X, 0.0, 0.0),
            scale=(GUARD_DIAM, GUARD_THICK, GUARD_DIAM),
            rotation=rot_x,
            material="gilt-bronze",
            attachment=contact_attachment("root", "blade-heel", overlap=0.012),
            rationale="Disk axis along the blade. Face-on it is a thin gilt edge; from the tip or a 3/4 it reads as a circle.",
            importance=0.95,
            confidence=0.86,
            extra_geom={"edgeTreatment": {"type": "chamfer", "bevelRadius": 0.0015, "segments": 2}},
            local_features=[{"id": "disk-guard", "kind": "contour", "notes": "Circle in side/top; thin gilt line in the face view."}],
            color=recipe(gilt, gilt_dark, "metal", 0.8),
            evidence=["side-guard", "full-object"],
            surface=GILT_SURFACE,
            collider={"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": False, "notes": "disk guard proxy"},
        ),
        component(
            "collar",
            "Gilt front ferrule",
            level="meso",
            role="body",
            primitive="cylinder",
            parent="root",
            position=(COLLAR_X, 0.0, 0.0),
            scale=(COLLAR_DIAM, COLLAR_LEN, COLLAR_DIAM),
            rotation=rot_x,
            material="gilt-bronze",
            attachment=contact_attachment("root", "guard-back", overlap=0.012),
            rationale="Short gilt sleeve between the disk and the wrap. Thinner than the wrap so it reads as a band, not a cap.",
            importance=0.8,
            confidence=0.8,
            extra_geom={"edgeTreatment": {"type": "chamfer", "bevelRadius": 0.0015, "segments": 2}},
            local_features=[{"id": "front-ferrule", "kind": "seam", "notes": "Visually distinct from wrap and steel."}],
            color=recipe(gilt, gilt_dark, "metal", 0.78),
            evidence=["full-object", "handle"],
            surface=GILT_SURFACE,
            collider={"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": False, "notes": "front ferrule proxy"},
        ),
        component(
            "handle",
            "Cord-wrapped grip",
            level="macro",
            role="handle",
            primitive="cylinder",
            parent="root",
            position=(HANDLE_X, 0.0, 0.0),
            scale=(WRAP_DIAM, HANDLE_LEN, WRAP_DIAM),
            rotation=rot_x,
            material="cord-wrap",
            attachment=contact_attachment("root", "front-ferrule-back", overlap=0.02),
            rationale="Dark cylindrical wrap. Degenerate attachment keeps the authored cylinder.",
            importance=0.95,
            confidence=0.84,
            extra_geom={"edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}},
            local_features=[
                {"id": "wrapped-grip", "kind": "seam", "notes": "Two counter-wound helical seam tubes define the crossed wrap."},
                {"id": "diamond-inlays", "kind": "fastener", "notes": "Six thin gilt lozenges per face sit nearly flush in paired dark seats."},
            ],
            color=recipe(wrap, wrap_dark, "fabric", 0.78),
            evidence=["handle"],
            surface=WRAP_SURFACE,
            collider={"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": False, "notes": "grip proxy"},
        ),
        *wrap_seam_components,
        *inlay_seat_components,
        *inlay_components,
        component(
            "ferrule",
            "Gilt rear ferrule",
            level="meso",
            role="body",
            primitive="cylinder",
            parent="root",
            position=(FERRULE_X, 0.0, 0.0),
            scale=(FERRULE_DIAM, FERRULE_LEN, FERRULE_DIAM),
            rotation=rot_x,
            material="gilt-bronze",
            attachment=contact_attachment("root", "handle-back", overlap=0.01),
            rationale="Thin gilt band between wrap and ring. Stops short of the ring so it does not fill the aperture.",
            importance=0.8,
            confidence=0.8,
            extra_geom={"edgeTreatment": {"type": "chamfer", "bevelRadius": 0.0015, "segments": 2}},
            color=recipe(gilt, gilt_dark, "metal", 0.78),
            evidence=["pommel-ring", "handle"],
            surface=GILT_SURFACE,
            collider={"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": False, "notes": "rear ferrule proxy"},
        ),
        component(
            "ring-neck",
            "Huan-shou neck",
            level="meso",
            role="body",
            primitive="cylinder",
            parent="root",
            position=(RING_NECK_X, 0.0, 0.0),
            scale=(RING_NECK_DIAM, RING_NECK_LEN, RING_NECK_DIAM),
            rotation=rot_x,
            material="gilt-bronze",
            attachment=contact_attachment("root", "rear-ferrule-back", overlap=0.01),
            rationale="Short gilt neck bridges the rear ferrule to the offset ring profile.",
            importance=0.75,
            confidence=0.82,
            color=recipe(gilt, gilt_dark, "metal", 0.78),
            evidence=["pommel-ring", "handle"],
            surface=GILT_SURFACE,
            explode_with_parent="ring",
            fracture_group="ring",
        ),
        component(
            "ring",
            "Huan-shou ring",
            level="macro",
            role="pommel",
            primitive="extrude",
            parent="root",
            position=(RING_X, 0.0, -RING_DEPTH * 0.5),
            scale=(RING_WIDTH, RING_HEIGHT, RING_DEPTH),
            material="gilt-bronze",
            attachment=contact_attachment("root", "rear-ferrule-back", overlap=0.007),
            rationale="Shallow gilt profile in the blade-face plane; an extruded oval hole preserves the aperture without the inflated look of a torus.",
            importance=1.0,
            confidence=0.9,
            extra_geom={
                "profile2D": RING_PROFILE,
                "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1},
            },
            local_features=[{"id": "ring-aperture", "kind": "hole", "notes": "Negative space must remain open in every orbit view."}],
            color=recipe(gilt, gilt_dark, "metal", 0.84),
            evidence=["pommel-ring"],
            surface=GILT_SURFACE,
            collider={"type": "sphere", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": False, "notes": "ring bounds"},
        ),
        *ring_engraving_components,
    ]

    adapter_failures = validate_dao_component_tree(DAO_ADAPTER, spec["componentTree"])
    if adapter_failures:
        raise ValueError("invalid dao component tree: " + "; ".join(adapter_failures))

    spec["materials"] = [
        steel_material(),
        gilt_material(),
        wrap_material(),
        wrap_seam_material(),
        hamon_material(),
        hamon_secondary_material(),
        engraving_material(),
    ]
    spec["lookDevTargets"]["qualityPriority"] = "stylized-approximate"
    spec["lookDevTargets"]["materialPass"]["referencePbrExtraction"]["requiredWhenSourceImagePresent"] = False
    spec["lookDevTargets"]["materialPass"]["referencePbrExtraction"]["acceptedLimitation"] = (
        "Source is an illustration plate, not a photographed material. "
        "Procedural steel / gilt / wrap is the honest path; extracted maps would copy ink, not PBR."
    )
    spec["performanceBudget"]["qualityPriority"] = "stylized-approximate"
    spec["optimizationPlan"] = {
        "policy": "Stay below the authored runtime budgets without removing accepted silhouette, material, or interaction evidence.",
        "runtimeAudit": ["triangles", "draw-calls", "measured-fps", "unique-geometries", "shared-materials", "texture-memory"],
        "benchmarkPolicy": "FPS is a hard gate on hardware-accelerated WebGL; SwiftShader or llvmpipe measurements are retained as report-only environment diagnostics.",
        "repetitionDecisions": [
            {
                "family": "handle-inlays",
                "count": 12,
                "strategy": "retain-selectable-components",
                "reason": "Each front/back stud and recessed seat is a named component; shared materials already avoid duplicate texture sets.",
            },
            {
                "family": "hamon-lines",
                "count": 6,
                "strategy": "retain-distinct-curves",
                "reason": "The six front/back curves have distinct paths and cannot share one instance transform.",
            },
            {
                "family": "ring-engravings",
                "count": 6,
                "strategy": "retain-distinct-profiles",
                "reason": "Front/back concentric profiles differ by face and size and remain integral ring details.",
            },
        ],
        "lodStrategy": {
            "near": "Full component tree, procedural PBR maps, and interaction metadata.",
            "far": "At 30 relative units, a host application may hide micro integral details while preserving blade, guard, handle, and ring silhouettes.",
            "implementation": "Documented host integration contract; no automatic LOD swap in the review viewer because the fixed evidence camera is always near-tier.",
        },
    }

    spec["lightingFromPhoto"] = [
        "Key: even orthographic plate lighting, slightly above-front, no hard indoor bounce.",
        "Fill: white page surround, high value, keeps steel from falling to mid-grey.",
        "Rim / environment: weak studio rim so the disk guard and ring read as volumes.",
        "Exposure: 1.0; protect steel highlights with ACES filmic tone mapping.",
        "Background: pure white plate field with no gradient or floor plane.",
        "Contact shadow: optional; the plate is on white, so review shots may omit the floor.",
    ]
    spec["proceduralStrategy"] = [
        "Block out blade + disk guard + wrap + ring first.",
        "Use ground-blade stations traced from the face view.",
        "Cylinder fittings rotate onto X; the shallow profile ring stays in the blade-face plane.",
        "Six gilt inlays seated nearly flush inside slightly larger dark wrap sockets.",
        "Procedural materials; do not project the illustration.",
        "Review face-on against the top row, side-on against the middle row.",
    ]
    spec["animationAnchors"] = [
        "root pivot for whole-weapon posing",
        "blade-heel socket at the guard",
        "pommel-anchor socket at the ring center",
    ]

    ids = [c["id"] for c in spec["componentTree"]]
    for build_pass in spec["buildPasses"]:
        build_pass["componentRefs"] = ids

    spec.update(preserved)

    spec["qualityContract"]["antiShallowSpecRules"] = spec["qualityContract"].get("mustNotDo", [])

    if ASSESSMENT.exists():
        existing = json.loads(ASSESSMENT.read_text(encoding="utf-8"))
        if isinstance(existing.get("localSpecSearch"), dict):
            spec["localSpecSearch"] = existing["localSpecSearch"]

    ASSESSMENT.write_text(
        json.dumps(
            {
                "targetName": spec["targetName"],
                "sourceImage": spec["sourceImage"],
                "preSpecAssessment": spec["preSpecAssessment"],
                "qualityContract": spec["qualityContract"],
                "authoringInstruction": "Assessment filled from the Han huan-shou dao three-view plate.",
                "localSpecSearch": spec.get("localSpecSearch", {}),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    OUT.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"BLADE_HEEL_X={BLADE_HEEL_X:.3f} RING_X={RING_X:.3f} HANDLE_X={HANDLE_X:.3f} thickness={BLADE_THICKNESS:.4f} inlays={INLAY_COUNT}")


if __name__ == "__main__":
    main()
