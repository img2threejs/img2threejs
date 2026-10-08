# -*- coding: utf-8 -*-
"""Enrich ObjectSculptSpec for Langstroth Beehive from visual analysis of refs/langstroth_hive_gen.png."""
from __future__ import annotations

import json
from pathlib import Path

spec_path = Path("work/hive/object-sculpt-spec.json")
spec = json.loads(spec_path.read_text(encoding="utf-8"))

# --- Suitability / scores (agent vision of reference) ---
spec["suitability"] = "pass"
spec["scores"] = {
    "object_isolation": 0.95,
    "silhouette_readability": 0.92,
    "depth_inference": 0.78,
    "primitive_decomposition": 0.9,
    "material_procedurality": 0.85,
    "occlusion_risk": 0.25,
    "interaction_fit": 0.7,
}

# Dimensions (relative). Width ~1.0 for super exterior.
W, D, H_SUPER = 1.0, 0.82, 0.36
H_ROOF, H_BOTTOM = 0.08, 0.12
y_bottom = H_BOTTOM * 0.5
y0 = H_BOTTOM + H_SUPER * 0.5
y1 = H_BOTTOM + H_SUPER * 1.5
y2 = H_BOTTOM + H_SUPER * 2.5
y_roof = H_BOTTOM + H_SUPER * 3 + H_ROOF * 0.5
total_h = H_BOTTOM + H_SUPER * 3 + H_ROOF

# --- Pre-spec assessment ---
ps = spec["preSpecAssessment"]
ps["objectClass"] = {
    "primaryType": "hard-surface container",
    "primaryDomain": "object",
    "formLanguage": ["rectilinear", "stacked-modular", "carpentered-wood", "box-joinery"],
    "structureKind": ["assembled-stack", "segmented-box", "lid-and-base"],
    "motionPotential": ["lid-lift", "box-slide-detach", "handle-lift"],
    "materialFamilies": ["varnished softwood", "galvanized metal edge", "oxidized steel hardware"],
    "notes": (
        "Langstroth hive: three stacked rectangular supers with box-joint corners, "
        "flat roof with metal drip edge and wire handle, bottom board with entrance reducer."
    ),
}
ps["complexity"] = {
    "tier": "moderate",
    "scores": {
        "silhouetteComplexity": 0.45,
        "componentCount": 0.7,
        "hierarchyDepth": 0.55,
        "repetitionDensity": 0.75,
        "materialLayerCount": 0.4,
        "localDetailDensity": 0.65,
        "occlusionRisk": 0.25,
        "actionReadinessNeed": 0.55,
    },
    "estimatedCounts": {
        "macroComponents": 5,
        "mesoComponents": 8,
        "microFeatureGroups": 4,
        "materialLayers": 3,
        "repetitionSystems": 2,
    },
    "reasoning": [
        "Vertical stack of 3 nearly-identical supers + roof + bottom board.",
        "Identity details: finger joints, recessed hand holds, metal roof edge, entrance reducer.",
        "Hidden rear/underside inferred by bilateral symmetry of a rectangular cabinet form.",
    ],
}
ps["specDepthDecision"] = {
    "requiredDepth": "moderate",
    "minimumComponentLevels": ["macro", "meso", "micro"],
    "needsRepetitionSystems": True,
    "needsMaterialLocalOverrides": True,
    "needsMultipleReviewViews": True,
    "needsActionReadyHierarchy": True,
    "rationale": "Modular stack with repeated box geometry and joinery micro-features.",
}
ps["unknownsToResolveBeforeImplementation"] = [
    "Exact interior frame rails not visible — exterior shell only.",
    "Rear face inferred by symmetry; no rear photo.",
    "Entrance reducer teeth count approximate from foreshortened view.",
]

details = [
    {
        "id": "finger-joint-corners",
        "kind": "joinery",
        "description": "Alternating box/finger joints at all vertical corners of each super (darker end-grain fingers).",
        "region": {"x": 0.15, "y": 0.25, "width": 0.15, "height": 0.55, "units": "normalized"},
        "scale": "meso",
        "affects": "silhouette-edge-rhythm",
        "mapsTo": {"type": "component.localFeatures", "ref": "super-middle"},
        "evidenceRef": "full-object",
        "confidence": 0.92,
    },
    {
        "id": "recessed-handholds",
        "kind": "cutout",
        "description": "Shallow elongated horizontal hand-hold recesses centered on front face of each of the three supers.",
        "region": {"x": 0.35, "y": 0.3, "width": 0.3, "height": 0.45, "units": "normalized"},
        "scale": "meso",
        "affects": "form",
        "mapsTo": {"type": "component.localFeatures", "ref": "super-top"},
        "evidenceRef": "full-object",
        "confidence": 0.9,
    },
    {
        "id": "metal-roof-drip-edge",
        "kind": "trim",
        "description": "Thin galvanized metal drip edge wrapping the perimeter of the flat wooden roof lid.",
        "region": {"x": 0.25, "y": 0.08, "width": 0.5, "height": 0.12, "units": "normalized"},
        "scale": "meso",
        "affects": "material-boundary",
        "mapsTo": {"type": "component", "ref": "roof-metal-edge"},
        "evidenceRef": "full-object",
        "confidence": 0.88,
    },
    {
        "id": "roof-wire-handle",
        "kind": "fastener-hardware",
        "description": "Dark arched metal wire handle centered on roof top surface.",
        "region": {"x": 0.42, "y": 0.06, "width": 0.16, "height": 0.08, "units": "normalized"},
        "scale": "micro",
        "affects": "interaction",
        "mapsTo": {"type": "component", "ref": "roof-handle"},
        "evidenceRef": "full-object",
        "confidence": 0.9,
    },
    {
        "id": "entrance-reducer",
        "kind": "hardware",
        "description": "Bottom-front entrance board with serrated/toothed metal or wood reducer strip and dark entrance slot.",
        "region": {"x": 0.25, "y": 0.82, "width": 0.45, "height": 0.12, "units": "normalized"},
        "scale": "meso",
        "affects": "identity",
        "mapsTo": {"type": "component", "ref": "entrance-reducer"},
        "evidenceRef": "full-object",
        "confidence": 0.85,
    },
    {
        "id": "wood-grain-varnish",
        "kind": "surface-finish",
        "description": "Warm honey-blond softwood with visible vertical grain and satin varnish; darker end-grain at joints.",
        "region": {"x": 0.3, "y": 0.35, "width": 0.4, "height": 0.35, "units": "normalized"},
        "scale": "micro",
        "affects": "material",
        "mapsTo": {"type": "material.localOverrides", "ref": "varnished-pine"},
        "evidenceRef": "full-object",
        "confidence": 0.9,
    },
    {
        "id": "side-handholds",
        "kind": "cutout",
        "description": "Vertical-face recessed hand holds on right side of each super.",
        "region": {"x": 0.68, "y": 0.28, "width": 0.18, "height": 0.45, "units": "normalized"},
        "scale": "meso",
        "affects": "form",
        "mapsTo": {"type": "component.localFeatures", "ref": "super-middle"},
        "evidenceRef": "full-object",
        "confidence": 0.82,
    },
    {
        "id": "bottom-board-ledge",
        "kind": "structure",
        "description": "Extended bottom board protruding slightly beyond supers with low front landing board.",
        "region": {"x": 0.2, "y": 0.88, "width": 0.55, "height": 0.1, "units": "normalized"},
        "scale": "macro",
        "affects": "silhouette",
        "mapsTo": {"type": "component", "ref": "bottom-board"},
        "evidenceRef": "full-object",
        "confidence": 0.88,
    },
]
ps["detailInventory"] = {
    "scanMethod": "agent-vision + grid-3x3 scaffold",
    "targetMinDetails": 6,
    "details": details,
    "note": "Mapped to componentTree/materials below.",
}

spec["coordinateFrame"] = {
    "up": [0, 1, 0],
    "forward": [0, 0, 1],
    "units": "relative",
    "origin": "ground-center under bottom board",
    "notes": "Front faces +Z (toward camera in three-quarter view slightly from left).",
}
spec["silhouette"] = {
    "dominantShape": "vertical rectangular prism stack",
    "aspectRatio": {"width": 1.0, "height": round(total_h, 3), "depth": D},
    "negativeSpaces": ["entrance slot under front of bottom super", "hand-hold recesses"],
    "profileNotes": [
        "Nearly constant width/depth through stack; roof slightly larger than supers.",
        "Bottom board slightly wider/deeper with front landing overhang.",
        "Box-joint teeth create scalloped vertical edges.",
    ],
    "confidence": 0.88,
}
spec["viewEvidence"] = [
    {
        "id": "full-object",
        "view": "three-quarter front-right",
        "source": "refs/langstroth_hive_gen.png",
        "visible": ["roof", "3 supers", "bottom board", "entrance", "finger joints", "handholds", "handle"],
        "occluded": ["rear face", "interior frames", "underside"],
        "confidence": 0.9,
    }
]
spec["assumptions"] = [
    "Rear and left faces mirror visible right/front by bilateral rectangular symmetry.",
    "Interior comb frames omitted (not visible) — exterior shell reconstruction only.",
    "Entrance reducer tooth count ≈ 12 estimated under foreshortening.",
    "Wood species treated as soft pine/spruce with satin clear coat.",
]


def mat_wood():
    return {
        "id": "varnished-pine",
        "name": "Varnished softwood (pine)",
        "type": "standard",
        "shaderModel": "MeshPhysicalMaterial",
        "baseColor": "#C9A06A",
        "color": "#C9A06A",
        "albedo": {
            "dominant": "#C9A06A",
            "secondary": ["#A67C4A", "#E0C08A", "#8B6340"],
            "samplingNotes": "Honey-blond face grain; darker amber in grain lines; end-grain fingers ~#8B6340.",
        },
        "colorVariation": {
            "palette": ["#C9A06A", "#A67C4A", "#E0C08A", "#8B6340", "#D4B07A"],
            "pattern": "vertical-wood-grain",
            "amplitude": 0.22,
            "heightCorrelation": 0.35,
        },
        "textureResolution": 1024,
        "textureProjection": {
            "mode": "uv",
            "repeat": [2.0, 3.0],
            "anisotropy": 8,
            "texelDensityIntent": "Grain runs vertical on box faces; end-grain on finger joints.",
        },
        "surfaceFrequencyBands": [
            {"id": "macro", "frequency": 1.5, "amplitude": 0.12, "role": "board-to-board tone variation"},
            {"id": "meso", "frequency": 18.0, "amplitude": 0.18, "role": "wood grain ridges"},
            {"id": "micro", "frequency": 64.0, "amplitude": 0.06, "role": "pore/fiber highlight breakup under gloss"},
        ],
        "roughness": {
            "base": 0.42,
            "variation": 0.12,
            "map": "independent-procedural-field",
            "localResponse": "lower roughness on face centers (varnish), higher in recesses and end-grain",
        },
        "metalness": {"base": 0.0, "variation": 0.0},
        "normal": {
            "pattern": "anisotropic-wood-grain",
            "strength": 0.45,
            "scale": 32.0,
            "space": "tangent",
        },
        "bump": {"pattern": "wood-grain", "amplitude": 0.015, "scale": 24.0},
        "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": False},
        "ambientOcclusion": {
            "cavityStrength": 0.4,
            "contactShadowBias": 0.45,
            "notes": "Darken box joints, handhold interiors, super-to-super seams.",
        },
        "wear": {
            "edgeWear": 0.15,
            "scratches": [{"direction": "random-shallow", "density": 0.08}],
            "chips": [],
        },
        "dirt": {"amount": 0.08, "cavityBias": 0.55, "color": "#4A3A28"},
        "clearcoat": 0.35,
        "clearcoatRoughness": 0.25,
        "localOverrides": [
            {
                "id": "end-grain-fingers",
                "region": "finger-joint-corners",
                "albedo": "#8B6340",
                "roughness": 0.62,
                "notes": "Darker end-grain fingers at box joints.",
            },
            {
                "id": "handhold-cavity",
                "region": "recessed-handholds",
                "roughness": 0.7,
                "aoBoost": 0.35,
                "notes": "Matte recessed wood inside hand holds.",
            },
            {
                "id": "seam-dirt",
                "region": "super-stack-seams",
                "dirt": 0.2,
                "notes": "Slight grime line between stacked boxes.",
            },
        ],
        "referencePbr": {
            "status": "usable",
            "confidence": 0.78,
            "source": "refs/langstroth_hive_gen.png center-face crop (agent-sampled)",
            "albedoDominant": "#C9A06A",
            "roughnessEstimate": 0.42,
            "metalnessEstimate": 0.0,
            "notes": "Studio soft key from upper-left; albedo inferred after discounting highlight.",
        },
        "shaderNotes": [
            "MeshPhysicalMaterial with mild clearcoat for varnish sheen.",
            "Independent roughness/AO maps; do not reuse albedo as roughness.",
        ],
    }


def mat_metal():
    return {
        "id": "galvanized-steel",
        "name": "Galvanized / painted steel hardware",
        "type": "standard",
        "shaderModel": "MeshStandardMaterial",
        "baseColor": "#A8B0B5",
        "color": "#A8B0B5",
        "albedo": {
            "dominant": "#A8B0B5",
            "secondary": ["#7A8288", "#C5CCD0", "#3A3A3A"],
            "samplingNotes": "Roof drip edge is light zinc-gray; handle is near-black painted steel.",
        },
        "colorVariation": {
            "palette": ["#A8B0B5", "#7A8288", "#C5CCD0", "#2E2E2E"],
            "pattern": "subtle-oxidized",
            "amplitude": 0.1,
            "heightCorrelation": 0.1,
        },
        "textureResolution": 512,
        "textureProjection": {
            "mode": "uv",
            "repeat": [1.0, 1.0],
            "anisotropy": 4,
            "texelDensityIntent": "Thin trim strips; keep texel density high enough for edge glints.",
        },
        "surfaceFrequencyBands": [
            {"id": "macro", "frequency": 2.0, "amplitude": 0.05, "role": "panel tone"},
            {"id": "meso", "frequency": 20.0, "amplitude": 0.08, "role": "rolled-metal micro dents"},
            {"id": "micro", "frequency": 80.0, "amplitude": 0.04, "role": "specular breakup"},
        ],
        "roughness": {
            "base": 0.38,
            "variation": 0.15,
            "map": "independent-procedural-field",
            "localResponse": "handle darker and rougher (~0.55); drip edge smoother",
        },
        "metalness": {"base": 0.85, "variation": 0.1},
        "normal": {"pattern": "brushed-micro", "strength": 0.25, "scale": 40.0, "space": "tangent"},
        "bump": {"pattern": "none", "amplitude": 0.0, "scale": 1.0},
        "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": False},
        "ambientOcclusion": {"cavityStrength": 0.2, "contactShadowBias": 0.25, "notes": "Contact under drip edge lip."},
        "wear": {"edgeWear": 0.2, "scratches": [{"direction": "along-edge", "density": 0.1}], "chips": []},
        "dirt": {"amount": 0.05, "cavityBias": 0.3, "color": "#3A3A3A"},
        "localOverrides": [
            {
                "id": "handle-dark",
                "region": "roof-handle",
                "albedo": "#2A2A2A",
                "metalness": 0.7,
                "roughness": 0.55,
            }
        ],
        "referencePbr": {
            "status": "usable",
            "confidence": 0.72,
            "source": "refs/langstroth_hive_gen.png roof-edge crop",
            "albedoDominant": "#A8B0B5",
            "roughnessEstimate": 0.38,
            "metalnessEstimate": 0.85,
            "notes": "Handle is darker painted metal — local override.",
        },
        "shaderNotes": ["Separate dark material override for wire handle."],
    }


def mat_reducer():
    return {
        "id": "entrance-metal",
        "name": "Dark entrance reducer / landing metal",
        "type": "standard",
        "shaderModel": "MeshStandardMaterial",
        "baseColor": "#4A4A48",
        "color": "#4A4A48",
        "albedo": {
            "dominant": "#4A4A48",
            "secondary": ["#2A2A28", "#6A6A65"],
            "samplingNotes": "Dark gray metal strip with teeth at front entrance.",
        },
        "colorVariation": {
            "palette": ["#4A4A48", "#2A2A28", "#6A6A65"],
            "pattern": "oxidized",
            "amplitude": 0.12,
            "heightCorrelation": 0.15,
        },
        "textureResolution": 512,
        "textureProjection": {
            "mode": "uv",
            "repeat": [4.0, 1.0],
            "anisotropy": 4,
            "texelDensityIntent": "Teeth pattern along width.",
        },
        "surfaceFrequencyBands": [
            {"id": "macro", "frequency": 3.0, "amplitude": 0.08, "role": "strip tone"},
            {"id": "meso", "frequency": 24.0, "amplitude": 0.2, "role": "tooth serrations"},
            {"id": "micro", "frequency": 70.0, "amplitude": 0.05, "role": "wear grit"},
        ],
        "roughness": {
            "base": 0.55,
            "variation": 0.1,
            "map": "independent-procedural-field",
            "localResponse": "higher in tooth valleys",
        },
        "metalness": {"base": 0.6, "variation": 0.1},
        "normal": {"pattern": "serrated-strip", "strength": 0.5, "scale": 16.0, "space": "tangent"},
        "bump": {"pattern": "teeth", "amplitude": 0.02, "scale": 12.0},
        "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": False},
        "ambientOcclusion": {
            "cavityStrength": 0.5,
            "contactShadowBias": 0.4,
            "notes": "Deep dark entrance slot behind reducer.",
        },
        "wear": {"edgeWear": 0.25, "scratches": [], "chips": []},
        "dirt": {"amount": 0.2, "cavityBias": 0.6, "color": "#1A1A18"},
        "localOverrides": [
            {
                "id": "entrance-slot-void",
                "region": "entrance-opening",
                "albedo": "#0A0A0A",
                "roughness": 0.9,
                "metalness": 0.0,
            }
        ],
        "referencePbr": {
            "status": "usable",
            "confidence": 0.7,
            "source": "refs/langstroth_hive_gen.png bottom-front crop",
            "albedoDominant": "#4A4A48",
            "roughnessEstimate": 0.55,
            "metalnessEstimate": 0.6,
            "notes": "Foreshortened; tooth geometry approximate.",
        },
        "shaderNotes": [],
    }


spec["materials"] = [mat_wood(), mat_metal(), mat_reducer()]


def attach(parent, socket, start, end, contact="abut", embed=0.01, gap=0.005):
    return {
        "parentSocket": socket,
        "localStart": start,
        "localEnd": end,
        "contactType": contact,
        "embedDepth": embed,
        "overlap": embed,
        "gapTolerance": gap,
        "notes": f"Attached to {parent}",
    }


def recipe(dom, sec, mclass="painted-wood", conf=0.85):
    def rgba(hexcol):
        h = hexcol.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r}, {g}, {b}, 1.0)"

    return {
        "dominantAlbedo": rgba(dom),
        "secondaryAlbedo": rgba(sec),
        "materialClass": mclass,
        "materialClassConfidence": conf,
    }


def action(role, breakable=False, fracture="hive", pivot=None):
    return {
        "animationRole": role,
        "pivot": pivot
        or {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.8},
        "transformChannels": {
            "translate": True,
            "rotate": True,
            "scale": False,
            "bend": False,
            "twist": False,
            "detach": breakable,
            "visibility": True,
            "materialState": True,
        },
        "sockets": [],
        "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": False, "notes": ""},
        "constraints": [],
        "destruction": {
            "breakable": breakable,
            "fractureGroup": fracture,
            "seamRefs": [],
            "detachableFragments": [],
            "breakImpulse": 12.0 if breakable else 0.0,
            "debrisMaterial": "varnished-pine",
        },
    }


components = []

components.append(
    {
        "id": "root",
        "name": "Langstroth Beehive Assembly",
        "level": "macro",
        "role": "assembly-root",
        "importance": 1.0,
        "confidence": 0.95,
        "primitive": "box",
        "topologyClass": "assembled-solid",
        "topologyRationale": "Multi-part stacked carpentered assembly of discrete boxes and hardware.",
        "geometryDescriptor": {
            "topologyIntent": "hierarchical assembly of rectangular supers + lid + base",
            "edgeTreatment": {"type": "bevel", "bevelRadius": 0.004, "segments": 2},
            "deformationStack": [],
            "uvStrategy": "per-component box UVs",
            "normalStrategy": "vertex normals + grain bump",
        },
        "parent": None,
        "attachment": None,
        "dimensions": {"width": W, "height": total_h, "depth": D + 0.06, "units": "relative", "confidence": 0.85},
        "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
        "actionProfile": action("root"),
        "material": "varnished-pine",
        "materialLayers": ["varnished-pine", "galvanized-steel", "entrance-metal"],
        "deformations": [],
        "joints": [],
        "seams": [],
        "localFeatures": [],
        "surfaceDetail": {
            "macroRoughness": 0.4,
            "microRoughness": 0.15,
            "bumpAmplitude": 0.01,
            "normalPattern": "wood-grain",
            "displacementPattern": "",
            "occlusionPattern": "stack-seams",
            "edgeWearPattern": "corner-wear",
            "notes": "Root is structural parent only.",
        },
        "evidenceRefs": ["full-object"],
        "details": ["overall stacked rectangular silhouette"],
        "fidelityTier": "structural",
        "colorMaterialRecipe": recipe("#C9A06A", "#A67C4A", "painted-wood", 0.9),
    }
)

components.append(
    {
        "id": "bottom-board",
        "name": "Bottom board / landing",
        "level": "macro",
        "role": "base",
        "importance": 0.95,
        "confidence": 0.88,
        "primitive": "box",
        "topologyClass": "assembled-solid",
        "topologyRationale": "Flat wooden plank assembly with slight front overhang.",
        "geometryDescriptor": {
            "topologyIntent": "wide low plank with front landing ledge",
            "edgeTreatment": {"type": "bevel", "bevelRadius": 0.003, "segments": 2},
            "deformationStack": [],
            "uvStrategy": "box UV, grain along X",
            "normalStrategy": "vertex normals",
        },
        "parent": "root",
        "attachment": attach("root", "base", [0, 0, 0], [0, H_BOTTOM, 0], "abut", 0.0, 0.002),
        "dimensions": {
            "width": W + 0.08,
            "height": H_BOTTOM,
            "depth": D + 0.12,
            "units": "relative",
            "confidence": 0.85,
        },
        "transform": {"position": [0, y_bottom, 0.03], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
        "actionProfile": action("static-base", fracture="base"),
        "material": "varnished-pine",
        "materialLayers": ["varnished-pine"],
        "deformations": [],
        "joints": [],
        "seams": [{"with": "super-bottom", "type": "abut-seam"}],
        "localFeatures": [
            {
                "id": "landing-overhang",
                "kind": "ledge",
                "description": "Front plank extends beyond super stack",
                "affects": "silhouette",
            }
        ],
        "surfaceDetail": {
            "macroRoughness": 0.45,
            "microRoughness": 0.2,
            "bumpAmplitude": 0.012,
            "normalPattern": "wood-grain",
            "displacementPattern": "",
            "occlusionPattern": "under-super",
            "edgeWearPattern": "front-edge-wear",
            "notes": "",
        },
        "evidenceRefs": ["full-object"],
        "details": ["bottom-board-ledge"],
        "fidelityTier": "structural",
        "colorMaterialRecipe": recipe("#B89060", "#8B6340", "painted-wood", 0.85),
    }
)

components.append(
    {
        "id": "entrance-reducer",
        "name": "Entrance reducer strip",
        "level": "meso",
        "role": "hardware",
        "importance": 0.85,
        "confidence": 0.8,
        "primitive": "box",
        "topologyClass": "surface-relief",
        "topologyRationale": "Thin serrated strip on front of bottom board; relief is part of identity.",
        "geometryDescriptor": {
            "topologyIntent": "thin horizontal bar with toothed top edge (12 teeth approx)",
            "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1},
            "deformationStack": [],
            "uvStrategy": "planar front",
            "normalStrategy": "flat + tooth normal",
        },
        "parent": "bottom-board",
        "attachment": attach(
            "bottom-board",
            "front-edge",
            [0, H_BOTTOM * 0.35, (D + 0.12) / 2],
            [0, H_BOTTOM * 0.35, (D + 0.12) / 2 + 0.02],
            "embed",
            0.015,
            0.003,
        ),
        "dimensions": {"width": W * 0.72, "height": 0.035, "depth": 0.04, "units": "relative", "confidence": 0.75},
        "transform": {
            "position": [0, 0.02, (D + 0.12) / 2 - 0.01],
            "rotation": [0, 0, 0],
            "scale": [1, 1, 1],
        },
        "actionProfile": action("static", fracture="base"),
        "material": "entrance-metal",
        "materialLayers": ["entrance-metal"],
        "deformations": [],
        "joints": [],
        "seams": [],
        "localFeatures": [
            {
                "id": "teeth",
                "kind": "serration",
                "count": 12,
                "description": "Upward teeth along strip",
                "affects": "identity",
            },
            {
                "id": "entrance-slot",
                "kind": "void",
                "description": "Dark horizontal opening behind strip",
                "affects": "negative-space",
            },
        ],
        "surfaceDetail": {
            "macroRoughness": 0.55,
            "microRoughness": 0.3,
            "bumpAmplitude": 0.02,
            "normalPattern": "serrated",
            "displacementPattern": "",
            "occlusionPattern": "slot",
            "edgeWearPattern": "",
            "notes": "",
        },
        "evidenceRefs": ["full-object"],
        "details": ["entrance-reducer"],
        "fidelityTier": "structural",
        "colorMaterialRecipe": recipe("#4A4A48", "#2A2A28", "metal", 0.75),
    }
)

for i, (cid, y, name) in enumerate(
    [
        ("super-bottom", y0, "Bottom super (brood box)"),
        ("super-middle", y1, "Middle super"),
        ("super-top", y2, "Top super"),
    ]
):
    components.append(
        {
            "id": cid,
            "name": name,
            "level": "macro",
            "role": "body-segment",
            "importance": 1.0 - i * 0.02,
            "confidence": 0.9,
            "primitive": "box",
            "topologyClass": "assembled-solid",
            "topologyRationale": "Closed rectangular wooden box with finger-joint corners and recessed hand holds.",
            "geometryDescriptor": {
                "topologyIntent": "hollow-looking solid blockout box; wall thickness implied by joints",
                "edgeTreatment": {"type": "bevel", "bevelRadius": 0.004, "segments": 2},
                "deformationStack": [],
                "uvStrategy": "box UV vertical grain",
                "normalStrategy": "vertex normals + grain",
            },
            "parent": "root",
            "attachment": attach(
                "root",
                f"stack-{i}",
                [0, y - H_SUPER * 0.5, 0],
                [0, y + H_SUPER * 0.5, 0],
                "abut",
                0.0,
                0.003,
            ),
            "dimensions": {"width": W, "height": H_SUPER, "depth": D, "units": "relative", "confidence": 0.9},
            "transform": {"position": [0, y, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
            "actionProfile": action(
                "detachable-stack",
                breakable=True,
                fracture=f"super-{i}",
                pivot={
                    "mode": "bottom-center",
                    "localPosition": [0, -H_SUPER * 0.5, 0],
                    "axis": [0, 1, 0],
                    "confidence": 0.85,
                },
            ),
            "material": "varnished-pine",
            "materialLayers": ["varnished-pine"],
            "deformations": [],
            "joints": [{"type": "box-joint", "corners": ["all-vertical"]}],
            "seams": [{"with": "neighbor-super", "type": "horizontal-abut"}],
            "localFeatures": [
                {
                    "id": "finger-joints",
                    "kind": "joinery",
                    "description": "Alternating finger joints on vertical edges",
                    "affects": "edge-rhythm",
                },
                {
                    "id": "front-handhold",
                    "kind": "recess",
                    "description": "Elongated horizontal hand-hold on front face",
                    "affects": "form",
                    "placement": {"face": "front", "width": 0.28, "height": 0.06, "depth": 0.03},
                },
                {
                    "id": "side-handhold",
                    "kind": "recess",
                    "description": "Hand-hold on right side face",
                    "affects": "form",
                    "placement": {"face": "right", "width": 0.06, "height": 0.12, "depth": 0.03},
                },
            ],
            "surfaceDetail": {
                "macroRoughness": 0.42,
                "microRoughness": 0.15,
                "bumpAmplitude": 0.012,
                "normalPattern": "wood-grain",
                "displacementPattern": "",
                "occlusionPattern": "handhold-cavity",
                "edgeWearPattern": "corner-soft",
                "notes": "End-grain fingers darker.",
            },
            "evidenceRefs": ["full-object"],
            "details": [
                "finger-joint-corners",
                "recessed-handholds",
                "side-handholds",
                "wood-grain-varnish",
            ],
            "fidelityTier": "structural",
            "colorMaterialRecipe": recipe("#C9A06A", "#A67C4A", "painted-wood", 0.9),
            "repetitionSystemRef": "super-stack",
        }
    )

for face, hid, pos, dims in [
    ("front", "handhold-front-middle", [0, 0, D / 2 - 0.01], [0.28, 0.055, 0.035]),
    ("right", "handhold-right-middle", [W / 2 - 0.01, 0, 0], [0.035, 0.1, 0.06]),
]:
    components.append(
        {
            "id": hid,
            "name": f"Handhold recess ({face})",
            "level": "micro",
            "role": "surface-cutout",
            "importance": 0.7,
            "confidence": 0.85,
            "primitive": "box",
            "topologyClass": "surface-relief",
            "topologyRationale": "Shallow recessed cavity cut into super face.",
            "geometryDescriptor": {
                "topologyIntent": "inset box representing cavity (boolean cut intent)",
                "edgeTreatment": {"type": "bevel", "bevelRadius": 0.008, "segments": 2},
                "deformationStack": [],
                "uvStrategy": "planar",
                "normalStrategy": "inward cavity",
            },
            "parent": "super-middle",
            "attachment": attach("super-middle", f"handhold-{face}", pos, pos, "embed", 0.02, 0.002),
            "dimensions": {
                "width": dims[0],
                "height": dims[1],
                "depth": dims[2],
                "units": "relative",
                "confidence": 0.8,
            },
            "transform": {"position": [pos[0], y1, pos[2]], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
            "actionProfile": action("static", fracture="super-1"),
            "material": "varnished-pine",
            "materialLayers": ["varnished-pine"],
            "deformations": [],
            "joints": [],
            "seams": [],
            "localFeatures": [
                {"id": "cavity-ao", "kind": "occlusion", "description": "Darker interior of hand hold"}
            ],
            "surfaceDetail": {
                "macroRoughness": 0.7,
                "microRoughness": 0.3,
                "bumpAmplitude": 0.0,
                "normalPattern": "",
                "displacementPattern": "",
                "occlusionPattern": "cavity",
                "edgeWearPattern": "",
                "notes": "Render as inset darker mesh or boolean depression.",
            },
            "evidenceRefs": ["full-object"],
            "details": ["recessed-handholds"],
            "fidelityTier": "detail",
            "colorMaterialRecipe": recipe("#A67C4A", "#6B4E30", "painted-wood", 0.8),
        }
    )

components.append(
    {
        "id": "roof",
        "name": "Telescoping cover / roof",
        "level": "macro",
        "role": "lid",
        "importance": 0.95,
        "confidence": 0.9,
        "primitive": "box",
        "topologyClass": "assembled-solid",
        "topologyRationale": "Flat wooden lid slightly larger than supers with metal drip edge.",
        "geometryDescriptor": {
            "topologyIntent": "flat lid plank with slight overhang",
            "edgeTreatment": {"type": "bevel", "bevelRadius": 0.003, "segments": 2},
            "deformationStack": [],
            "uvStrategy": "box UV",
            "normalStrategy": "vertex normals",
        },
        "parent": "root",
        "attachment": attach(
            "root",
            "top-socket",
            [0, y_roof - H_ROOF * 0.5, 0],
            [0, y_roof + H_ROOF * 0.5, 0],
            "abut",
            0.0,
            0.003,
        ),
        "dimensions": {
            "width": W + 0.06,
            "height": H_ROOF,
            "depth": D + 0.06,
            "units": "relative",
            "confidence": 0.9,
        },
        "transform": {"position": [0, y_roof, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
        "actionProfile": action(
            "lid",
            breakable=True,
            fracture="roof",
            pivot={
                "mode": "back-hinge",
                "localPosition": [0, 0, -D * 0.4],
                "axis": [1, 0, 0],
                "confidence": 0.7,
            },
        ),
        "material": "varnished-pine",
        "materialLayers": ["varnished-pine", "galvanized-steel"],
        "deformations": [],
        "joints": [],
        "seams": [{"with": "super-top", "type": "abut-seam"}],
        "localFeatures": [{"id": "flat-top", "kind": "plane", "description": "Planar top surface"}],
        "surfaceDetail": {
            "macroRoughness": 0.4,
            "microRoughness": 0.15,
            "bumpAmplitude": 0.01,
            "normalPattern": "wood-grain",
            "displacementPattern": "",
            "occlusionPattern": "",
            "edgeWearPattern": "",
            "notes": "",
        },
        "evidenceRefs": ["full-object"],
        "details": ["metal-roof-drip-edge", "roof-wire-handle"],
        "fidelityTier": "structural",
        "colorMaterialRecipe": recipe("#C4A070", "#A67C4A", "painted-wood", 0.88),
    }
)

components.append(
    {
        "id": "roof-metal-edge",
        "name": "Galvanized drip edge",
        "level": "meso",
        "role": "trim",
        "importance": 0.8,
        "confidence": 0.88,
        "primitive": "box",
        "topologyClass": "conforming-shell",
        "topologyRationale": "Thin metal strip wrapping roof perimeter — shell-like trim.",
        "geometryDescriptor": {
            "topologyIntent": "thin perimeter band under roof lip",
            "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1},
            "deformationStack": [],
            "uvStrategy": "strip UV",
            "normalStrategy": "outward",
        },
        "parent": "roof",
        "attachment": attach(
            "roof", "perimeter", [0, -H_ROOF * 0.35, 0], [0, -H_ROOF * 0.35, 0], "embed", 0.01, 0.002
        ),
        "dimensions": {
            "width": W + 0.07,
            "height": 0.018,
            "depth": D + 0.07,
            "units": "relative",
            "confidence": 0.85,
        },
        "transform": {
            "position": [0, y_roof - H_ROOF * 0.35, 0],
            "rotation": [0, 0, 0],
            "scale": [1, 1, 1],
        },
        "actionProfile": action("static", fracture="roof"),
        "material": "galvanized-steel",
        "materialLayers": ["galvanized-steel"],
        "deformations": [],
        "joints": [],
        "seams": [],
        "localFeatures": [
            {"id": "zinc-band", "kind": "trim", "description": "Light metal band visible under roof rim"}
        ],
        "surfaceDetail": {
            "macroRoughness": 0.35,
            "microRoughness": 0.12,
            "bumpAmplitude": 0.0,
            "normalPattern": "brushed",
            "displacementPattern": "",
            "occlusionPattern": "",
            "edgeWearPattern": "edge-scuff",
            "notes": "",
        },
        "evidenceRefs": ["full-object"],
        "details": ["metal-roof-drip-edge"],
        "fidelityTier": "detail",
        "colorMaterialRecipe": recipe("#A8B0B5", "#7A8288", "metal", 0.85),
    }
)

components.append(
    {
        "id": "roof-handle",
        "name": "Roof wire handle",
        "level": "meso",
        "role": "handle",
        "importance": 0.75,
        "confidence": 0.9,
        "primitive": "tube",
        "topologyClass": "fiber-strand",
        "topologyRationale": "Bent metal wire is a thin tubular strand path.",
        "geometryDescriptor": {
            "topologyIntent": "U-shaped wire handle on roof top",
            "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1},
            "deformationStack": [],
            "uvStrategy": "sweep UV",
            "normalStrategy": "tube normals",
            "path3D": {
                "points": [
                    [-0.08, 0.04, 0.0],
                    [-0.06, 0.07, 0.0],
                    [0.0, 0.08, 0.0],
                    [0.06, 0.07, 0.0],
                    [0.08, 0.04, 0.0],
                ],
                "radius": 0.008,
                "radialSegments": 8,
                "closed": False,
            },
        },
        "parent": "roof",
        "attachment": attach(
            "roof",
            "top-center",
            [0, H_ROOF * 0.5, 0],
            [0, H_ROOF * 0.5 + 0.08, 0],
            "embed",
            0.01,
            0.002,
        ),
        "dimensions": {"width": 0.16, "height": 0.08, "depth": 0.02, "units": "relative", "confidence": 0.85},
        "transform": {
            "position": [0, y_roof + H_ROOF * 0.5, 0],
            "rotation": [0, 0, 0],
            "scale": [1, 1, 1],
        },
        "actionProfile": action(
            "grasp-handle",
            fracture="roof",
            pivot={"mode": "base", "localPosition": [0, 0, 0], "axis": [1, 0, 0], "confidence": 0.8},
        ),
        "material": "galvanized-steel",
        "materialLayers": ["galvanized-steel"],
        "deformations": [],
        "joints": [],
        "seams": [],
        "localFeatures": [{"id": "arch", "kind": "curve", "description": "Arched wire"}],
        "surfaceDetail": {
            "macroRoughness": 0.55,
            "microRoughness": 0.2,
            "bumpAmplitude": 0.0,
            "normalPattern": "",
            "displacementPattern": "",
            "occlusionPattern": "",
            "edgeWearPattern": "",
            "notes": "Dark painted metal local override.",
        },
        "evidenceRefs": ["full-object"],
        "details": ["roof-wire-handle"],
        "fidelityTier": "detail",
        "colorMaterialRecipe": recipe("#2A2A2A", "#3A3A3A", "metal", 0.9),
    }
)

components.append(
    {
        "id": "finger-joint-sample",
        "name": "Box joint tooth sample",
        "level": "micro",
        "role": "joinery-detail",
        "importance": 0.65,
        "confidence": 0.85,
        "primitive": "box",
        "topologyClass": "surface-relief",
        "topologyRationale": "Small rectangular teeth alternating on vertical edges.",
        "geometryDescriptor": {
            "topologyIntent": "instanced small boxes along vertical edges",
            "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1},
            "deformationStack": [],
            "uvStrategy": "end-grain UV",
            "normalStrategy": "flat",
        },
        "parent": "super-middle",
        "attachment": attach(
            "super-middle",
            "corner-edge",
            [-W / 2, 0, D / 2],
            [-W / 2 - 0.01, 0, D / 2],
            "embed",
            0.008,
            0.002,
        ),
        "dimensions": {"width": 0.028, "height": 0.04, "depth": 0.028, "units": "relative", "confidence": 0.8},
        "transform": {"position": [-W / 2, y1, D / 2], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
        "actionProfile": action("static", fracture="super-1"),
        "material": "varnished-pine",
        "materialLayers": ["varnished-pine"],
        "deformations": [],
        "joints": [],
        "seams": [],
        "localFeatures": [
            {"id": "end-grain", "kind": "material-zone", "description": "Darker end grain"}
        ],
        "surfaceDetail": {
            "macroRoughness": 0.62,
            "microRoughness": 0.25,
            "bumpAmplitude": 0.005,
            "normalPattern": "end-grain",
            "displacementPattern": "",
            "occlusionPattern": "",
            "edgeWearPattern": "",
            "notes": "",
        },
        "evidenceRefs": ["full-object"],
        "details": ["finger-joint-corners"],
        "fidelityTier": "detail",
        "colorMaterialRecipe": recipe("#8B6340", "#6B4E30", "painted-wood", 0.85),
        "repetitionSystemRef": "finger-joints",
    }
)

spec["componentTree"] = components

spec["repetitionSystems"] = [
    {
        "id": "super-stack",
        "name": "Stacked supers",
        "mode": "linear",
        "count": 3,
        "prototypeComponent": "super-middle",
        "distribution": {
            "axis": [0, 1, 0],
            "spacing": H_SUPER,
            "start": [0, y0, 0],
        },
        "variation": {"rotationJitter": 0.0, "scaleJitter": 0.0},
        "notes": "Three identical exterior boxes stacked vertically.",
    },
    {
        "id": "finger-joints",
        "name": "Box joint teeth along vertical edges",
        "mode": "linear",
        "count": 8,
        "prototypeComponent": "finger-joint-sample",
        "distribution": {
            "axis": [0, 1, 0],
            "spacing": 0.045,
            "start": [-W / 2, y1 - H_SUPER / 2 + 0.02, D / 2],
        },
        "variation": {"alternateOffset": True},
        "notes": "Alternate protruding/recessed teeth; dark end-grain material.",
    },
]

spec["featureReviewTargets"] = [
    {
        "id": "stack-silhouette",
        "name": "Vertical 3-box stack silhouette with roof and base",
        "tier": "critical",
        "passIds": ["blockout", "structural-pass"],
        "minimumScore": 0.85,
        "mustPass": True,
        "componentRefs": ["root", "super-bottom", "super-middle", "super-top", "roof", "bottom-board"],
        "evidenceRefs": ["full-object"],
    },
    {
        "id": "box-joinery-rhythm",
        "name": "Finger/box joint edge rhythm on vertical corners",
        "tier": "critical",
        "passIds": ["structural-pass", "form-refinement"],
        "minimumScore": 0.8,
        "mustPass": True,
        "componentRefs": ["super-middle", "finger-joint-sample"],
        "evidenceRefs": ["full-object"],
    },
    {
        "id": "handhold-recesses",
        "name": "Recessed hand holds on super faces",
        "tier": "critical",
        "passIds": ["form-refinement", "surface-pass"],
        "minimumScore": 0.8,
        "mustPass": True,
        "componentRefs": ["handhold-front-middle", "handhold-right-middle"],
        "evidenceRefs": ["full-object"],
    },
    {
        "id": "roof-hardware",
        "name": "Metal drip edge + arched wire handle",
        "tier": "important",
        "passIds": ["form-refinement", "material-pass"],
        "minimumScore": 0.75,
        "mustPass": True,
        "componentRefs": ["roof-metal-edge", "roof-handle"],
        "evidenceRefs": ["full-object"],
    },
    {
        "id": "entrance-identity",
        "name": "Bottom entrance reducer and landing board",
        "tier": "important",
        "passIds": ["structural-pass", "form-refinement"],
        "minimumScore": 0.75,
        "mustPass": True,
        "componentRefs": ["bottom-board", "entrance-reducer"],
        "evidenceRefs": ["full-object"],
    },
    {
        "id": "wood-pbr-response",
        "name": "Varnished pine grain and metal contrast",
        "tier": "important",
        "passIds": ["material-pass", "lighting-pass"],
        "minimumScore": 0.75,
        "mustPass": True,
        "componentRefs": ["super-middle", "roof-metal-edge"],
        "evidenceRefs": ["full-object"],
    },
]

spec["lightingFromPhoto"] = [
    {
        "id": "key",
        "type": "directional",
        "role": "key",
        "directionHint": [-0.45, 0.75, 0.45],
        "color": "#FFF5E6",
        "intensity": 1.35,
        "notes": "Soft upper-left studio key; warm.",
    },
    {
        "id": "fill",
        "type": "hemisphere",
        "role": "fill",
        "skyColor": "#F0F2F5",
        "groundColor": "#C8C0B4",
        "intensity": 0.45,
        "notes": "Neutral soft fill from white cyclorama.",
    },
    {
        "id": "rim",
        "type": "directional",
        "role": "rim",
        "directionHint": [0.55, 0.35, -0.65],
        "color": "#E8EEF5",
        "intensity": 0.35,
        "notes": "Subtle cool rim separating hive from white background.",
    },
    {
        "id": "env",
        "type": "environment",
        "role": "reflection",
        "preset": "studio-soft",
        "intensity": 0.55,
        "notes": "Low-contrast studio env for varnish reflections.",
    },
]

qc = spec["qualityContract"]
qc["qualityBar"] = "moderate"
qc["minimumSpecDepth"] = {
    "macroComponents": 5,
    "mesoComponents": 4,
    "microFeatureGroups": 3,
    "materialLayers": 3,
    "repetitionSystems": 2,
    "reviewViewpoints": 3,
}

if isinstance(spec.get("lookDevTargets"), dict):
    spec["lookDevTargets"]["primaryMaterials"] = [
        "varnished-pine",
        "galvanized-steel",
        "entrance-metal",
    ]

spec["proceduralStrategy"] = [
    "Box primitives for supers, roof, bottom board (assembled-solid).",
    "Tube path for roof wire handle.",
    "Instanced small boxes for finger-joint teeth along vertical edges.",
    "Inset darker boxes for hand-hold cavities (boolean-cut intent).",
    "Procedural wood-grain albedo/roughness/normal for pine; independent channels.",
    "No external mesh downloads — pure Three.js geometry.",
]

spec["animationAnchors"] = [
    {"id": "lid-open", "component": "roof", "channel": "rotation.x", "range": [0, -1.1], "notes": "Hinge-open lid"},
    {
        "id": "lift-top-super",
        "component": "super-top",
        "channel": "position.y",
        "range": [0, 0.5],
        "notes": "Lift off top box",
    },
    {
        "id": "explode-stack",
        "component": "root",
        "channel": "explode",
        "notes": "Scale-from-center explode of named parts",
    },
]

spec["destructionAnchors"] = [
    {
        "id": "detach-supers",
        "groups": ["super-0", "super-1", "super-2", "roof", "base"],
        "notes": "Each box is a detachable fragment.",
    }
]

spec["risks"] = [
    "Single three-quarter view: rear/left inferred by symmetry.",
    "Interior frames not reconstructed.",
    "Handholds on all three supers should match; only middle fully micro-detailed in first pass.",
    "Finger joint count approximate under foreshortening.",
]

spec["referenceCamera"] = {
    "solved": False,
    "fovDegrees": 35.0,
    "aspect": 1.0,
    "orientation": {"yaw": 28.0, "pitch": -8.0, "roll": 0.0},
    "positionHint": [1.35, 1.05, 2.4],
    "targetHint": [0.0, total_h * 0.45, 0.0],
    "note": "Approximate three-quarter studio camera matching reference framing.",
}

spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
print(f"Wrote {spec_path}")
print(
    f"components={len(spec['componentTree'])} materials={len(spec['materials'])} "
    f"details={len(ps['detailInventory']['details'])}"
)
print(
    "levels: "
    f"macro={sum(1 for c in components if c['level']=='macro')} "
    f"meso={sum(1 for c in components if c['level']=='meso')} "
    f"micro={sum(1 for c in components if c['level']=='micro')}"
)
