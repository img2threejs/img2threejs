#!/usr/bin/env python3
"""Build a complete Ford Fiesta car spec with proper component tree and materials."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
PROJ_DIR = Path.home() / "Documents" / "ZCodeProjects" / "ford-fiesta"
SPEC_PATH = SKILL_DIR / "outputs" / "ford-fiesta-sculpt-spec.json"
BACKUP_PATH = SPEC_PATH.with_suffix(".json.bak2")
IMAGE_PATH = SKILL_DIR.parent / "agnes-free-image" / "outputs" / "agnes-free-image" / "4ca183cd2cad4e49928c2ed05cd3b170.png"
MODEL_OUT = PROJ_DIR / "createFordFiestaModel.ts"


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def T(x=0, y=0, z=0):
    return {"position": [x, y, z], "rotation": [0, 0, 0], "scale": [1, 1, 1]}


def A(socket, contact="continuous", embed=0.005, gap=0.001, start=(0, 0, 0), end=(0, 0, 0)):
    return {"parentSocket": socket, "contactType": contact, "embedDepth": embed,
            "gapTolerance": gap, "localStart": list(start), "localEnd": list(end)}


def AP(role="static"):
    return {"animationRole": role, "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7},
            "transformChannels": {"translate": False, "rotate": False, "scale": False, "bend": False, "twist": False, "detach": False, "visibility": True, "materialState": False},
            "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": False, "notes": "box proxy"},
            "constraints": [], "destruction": {"breakable": False, "fractureGroup": "", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hidden"}}


def GD(topology="stylized reconstruction"):
    return {"topologyIntent": topology, "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1},
            "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}


def SD():
    return {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "",
            "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "",
            "notes": "", "roughnessMap": None, "normalMap": None}


def C(cid, name, level, primitive, parent, mat, imp=1.0, conf=0.85, topo="assembled-solid",
      topoR="Procedural stylized approximation.", dims=None, xform=None, attach=None,
      kids=None, features=None, role="body", fidelity="blockout", details=None):
    c = {"id": cid, "name": name, "level": level, "role": role, "importance": imp, "confidence": conf,
         "primitive": primitive, "topologyClass": topo, "topologyRationale": topoR,
         "geometryDescriptor": GD(), "parent": parent, "attachment": attach,
         "dimensions": dims or {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": conf},
         "transform": xform or T(), "actionProfile": AP("root" if role == "root" else "static"),
         "material": mat, "materialLayers": [mat], "deformations": [], "joints": [], "seams": [],
         "localFeatures": features or [], "surfaceDetail": SD(), "evidenceRefs": ["full-object"],
         "details": details or [], "fidelityTier": fidelity,
         "colorMaterialRecipe": {"dominantAlbedo": "rgba(200,200,200,1.0)", "materialClass": "plastic",
                                 "materialClassConfidence": 0.8, "secondaryAlbedo": "rgba(200,200,200,1.0)"}}
    if kids:
        c["children"] = kids
    return c


def build_car_spec():
    print("Loading existing spec skeleton...")
    spec = load_json(SPEC_PATH)

    shutil.copy2(SPEC_PATH, BACKUP_PATH)
    print(f"Backup saved to {BACKUP_PATH}")

    L, W, H = 4.0, 1.7, 1.5

    # Build component dict
    comps = {}

    comps["root"] = C(
        "root", "Ford Fiesta (root)", "macro", "box", None, "body-paint",
        role="root", topo="assembled-solid",
        topoR="Root group for Ford Fiesta hatchback car reconstruction.",
        dims={"width": L, "height": H, "depth": W, "units": "meters", "confidence": 0.85},
        kids=["body", "wheel-fl", "wheel-fr", "wheel-rl", "wheel-rr",
              "mirror-left", "mirror-right"],
    )

    comps["body"] = C(
        "body", "Car body shell", "macro", "box", "root", "body-paint",
        imp=1.0, conf=0.9, topo="assembled-solid",
        topoR="Main body shell with compound curves, panel gaps, character lines.",
        dims={"width": L * 0.95, "height": H * 0.7, "depth": W * 0.95, "units": "meters", "confidence": 0.85},
        xform=T(0, H * 0.15, 0), attach=A("root", "continuous", 0.01, 0.0, (0, 0, 0), (0, 0, 0)),
        kids=["windshield", "side-windows", "front-grille", "headlight-left", "headlight-right", "bumper"],
        features=["body-character-line"], details=["det-body-char-line"],
    )

    comps["windshield"] = C(
        "windshield", "Front windshield", "meso", "plane-card", "body", "glass",
        dims={"width": 1.2, "height": 0.7, "depth": 0.02, "units": "meters", "confidence": 0.8},
        xform=T(1.5, 0.5, 0), attach=A("body", "recessed", 0.005, 0.002, (1.5, 0.5, 0), (1.5, 0.5, 0)),
        details=["det-windshield"],
    )

    comps["side-windows"] = C(
        "side-windows", "Side windows", "meso", "plane-card", "body", "glass",
        dims={"width": 1.8, "height": 0.4, "depth": 0.02, "units": "meters", "confidence": 0.75},
        xform=T(0.2, 0.5, 0.86), attach=A("body", "recessed", 0.005, 0.002, (0.2, 0.5, 0.86), (0.2, 0.5, 0.86)),
    )

    comps["front-grille"] = C(
        "front-grille", "Hexagonal front grille", "meso", "plane-card", "body", "chrome",
        dims={"width": 0.5, "height": 0.25, "depth": 0.05, "units": "meters", "confidence": 0.85},
        xform=T(2.0, 0.15, 0), attach=A("body", "continuous", 0.01, 0.001, (2.0, 0.15, 0), (2.0, 0.15, 0)),
        features=["grille-chrome-slats"], details=["det-grille"],
    )

    comps["headlight-left"] = C(
        "headlight-left", "Left headlight", "meso", "sphere", "body", "headlight-mat",
        dims={"width": 0.2, "height": 0.12, "depth": 0.1, "units": "meters", "confidence": 0.85},
        xform=T(1.95, 0.35, -0.6), attach=A("body", "recessed", 0.01, 0.002, (1.95, 0.35, -0.6), (1.95, 0.35, -0.6)),
    )

    comps["headlight-right"] = C(
        "headlight-right", "Right headlight", "meso", "sphere", "body", "headlight-mat",
        dims={"width": 0.2, "height": 0.12, "depth": 0.1, "units": "meters", "confidence": 0.85},
        xform=T(1.95, 0.35, 0.6), attach=A("body", "recessed", 0.01, 0.002, (1.95, 0.35, 0.6), (1.95, 0.35, 0.6)),
    )

    comps["bumper"] = C(
        "bumper", "Front bumper", "meso", "box", "body", "plastic-trim",
        dims={"width": 0.3, "height": 0.25, "depth": 0.85, "units": "meters", "confidence": 0.8},
        xform=T(2.05, -0.05, 0), attach=A("body", "continuous", 0.01, 0.001, (2.05, -0.05, 0), (2.05, -0.05, 0)),
        features=["fog-light-bezels", "black-bumper-inserts"], details=["det-foglights", "det-bumper-inserts"],
    )

    comps["wheel-fl"] = C(
        "wheel-fl", "Wheel front-left", "meso", "cylinder", "root", "alloy",
        dims={"width": 0.3, "height": 0.3, "depth": 0.25, "units": "meters", "confidence": 0.85},
        xform=T(1.2, 0.15, -1.0), attach=A("root", "continuous", 0.005, 0.005, (1.2, 0.15, -1.0), (1.2, 0.15, -1.0)),
        features=["multi-spoke-wheels"], details=["det-wheels"],
    )

    comps["wheel-fr"] = C(
        "wheel-fr", "Wheel front-right", "meso", "cylinder", "root", "alloy",
        dims={"width": 0.3, "height": 0.3, "depth": 0.25, "units": "meters", "confidence": 0.85},
        xform=T(1.2, 0.15, 1.0), attach=A("root", "continuous", 0.005, 0.005, (1.2, 0.15, 1.0), (1.2, 0.15, 1.0)),
        features=["multi-spoke-wheels"],
    )

    comps["wheel-rl"] = C(
        "wheel-rl", "Wheel rear-left", "meso", "cylinder", "root", "alloy",
        dims={"width": 0.3, "height": 0.3, "depth": 0.25, "units": "meters", "confidence": 0.85},
        xform=T(-1.3, 0.15, -1.0), attach=A("root", "continuous", 0.005, 0.005, (-1.3, 0.15, -1.0), (-1.3, 0.15, -1.0)),
    )

    comps["wheel-rr"] = C(
        "wheel-rr", "Wheel rear-right", "meso", "cylinder", "root", "alloy",
        dims={"width": 0.3, "height": 0.3, "depth": 0.25, "units": "meters", "confidence": 0.85},
        xform=T(-1.3, 0.15, 1.0), attach=A("root", "continuous", 0.005, 0.005, (-1.3, 0.15, 1.0), (-1.3, 0.15, 1.0)),
    )

    comps["mirror-left"] = C(
        "mirror-left", "Left side mirror", "micro", "box", "root", "plastic-trim",
        dims={"width": 0.15, "height": 0.1, "depth": 0.08, "units": "meters", "confidence": 0.8},
        xform=T(1.4, 0.6, -0.95), attach=A("root", "continuous", 0.005, 0.002, (1.4, 0.6, -0.95), (1.4, 0.6, -0.95)),
        features=["turn-signal-mirrors"],
    )

    comps["mirror-right"] = C(
        "mirror-right", "Right side mirror", "micro", "box", "root", "plastic-trim",
        dims={"width": 0.15, "height": 0.1, "depth": 0.08, "units": "meters", "confidence": 0.8},
        xform=T(1.4, 0.6, 0.95), attach=A("root", "continuous", 0.005, 0.002, (1.4, 0.6, 0.95), (1.4, 0.6, 0.95)),
    )

    materials = [
        {"id": "body-paint", "displayName": "White metallic paint", "type": "standard",
         "albedo": {"hex": "#F0F0F0", "type": "sRGB"},
         "roughness": {"base": 0.3, "map": {"type": "procedural", "variation": 0.1}, "variation": 0.1},
         "metalness": 0.0, "normalMap": None,
         "notes": "White metallic body paint with glossy finish", "textureResolution": 1024,
         "ambientOcclusion": {"map": {"type": "procedural"}, "intensity": 0.3}, "localOverrides": [],
         "textureProjection": {"mode": "uv", "texelDensity": 256},
         "roughnessMap": {"enabled": True, "source": "procedural"},
         "surfaceFrequencyBands": [{"id": "macro", "amplitude": 0.5, "frequency": 1.0},
                                    {"id": "meso", "amplitude": 0.3, "frequency": 0.3},
                                    {"id": "micro", "amplitude": 0.1, "frequency": 0.05}],
         "colorVariation": {"primary": "#F0F0F0", "secondary": "#FAFAFA", "accent": "#E8E8E8",
                            "notes": "Estimated from source"}},
        {"id": "glass", "displayName": "Car glass", "type": "standard",
         "albedo": {"hex": "#C8D8E8", "type": "sRGB"},
         "roughness": {"base": 0.1, "map": {"type": "procedural", "variation": 0.05}, "variation": 0.05},
         "metalness": 0.0, "transmission": 0.6, "ior": 1.5, "normalMap": None,
         "notes": "Tinted windshield and side window glass", "textureResolution": 1024,
         "ambientOcclusion": {"map": {"type": "procedural"}, "intensity": 0.2}, "localOverrides": [],
         "textureProjection": {"mode": "uv", "texelDensity": 256},
         "roughnessMap": {"enabled": True, "source": "procedural"},
         "surfaceFrequencyBands": [{"id": "macro", "amplitude": 0.3, "frequency": 1.0},
                                    {"id": "meso", "amplitude": 0.2, "frequency": 0.5},
                                    {"id": "micro", "amplitude": 0.1, "frequency": 0.1}],
         "colorVariation": {"primary": "#C8D8E8", "secondary": "#D0E0F0", "accent": "#B8C8D8",
                            "notes": "Estimated"}},
        {"id": "plastic-trim", "displayName": "Dark plastic trim", "type": "standard",
         "albedo": {"hex": "#2A2A2A", "type": "sRGB"},
         "roughness": {"base": 0.85, "map": {"type": "procedural", "variation": 0.1}, "variation": 0.1},
         "metalness": 0.0, "normalMap": None,
         "notes": "Dark plastic bumper inserts, mirror housings, trim", "textureResolution": 1024,
         "ambientOcclusion": {"map": {"type": "procedural"}, "intensity": 0.5}, "localOverrides": [],
         "textureProjection": {"mode": "uv", "texelDensity": 256},
         "roughnessMap": {"enabled": True, "source": "procedural"},
         "surfaceFrequencyBands": [{"id": "macro", "amplitude": 0.5, "frequency": 1.0},
                                    {"id": "meso", "amplitude": 0.3, "frequency": 0.3},
                                    {"id": "micro", "amplitude": 0.1, "frequency": 0.05}],
         "colorVariation": {"primary": "#2A2A2A", "secondary": "#333333", "accent": "#222222",
                            "notes": "Estimated"}},
        {"id": "chrome", "displayName": "Chrome trim", "type": "standard",
         "albedo": {"hex": "#E8E8E8", "type": "sRGB"},
         "roughness": {"base": 0.15, "map": {"type": "procedural", "variation": 0.05}, "variation": 0.05},
         "metalness": 1.0, "normalMap": None,
         "notes": "Chrome grille slats and trim", "textureResolution": 1024,
         "ambientOcclusion": {"map": {"type": "procedural"}, "intensity": 0.2}, "localOverrides": [],
         "textureProjection": {"mode": "uv", "texelDensity": 256},
         "roughnessMap": {"enabled": True, "source": "procedural"},
         "surfaceFrequencyBands": [{"id": "macro", "amplitude": 0.3, "frequency": 1.0},
                                    {"id": "meso", "amplitude": 0.2, "frequency": 0.5},
                                    {"id": "micro", "amplitude": 0.1, "frequency": 0.1}],
         "colorVariation": {"primary": "#E8E8E8", "secondary": "#F0F0F0", "accent": "#D0D0D0",
                            "notes": "Estimated"}},
        {"id": "alloy", "displayName": "Alloy wheel", "type": "standard",
         "albedo": {"hex": "#C0C0C0", "type": "sRGB"},
         "roughness": {"base": 0.4, "map": {"type": "procedural", "variation": 0.1}, "variation": 0.1},
         "metalness": 0.8, "normalMap": None,
         "notes": "Silver alloy wheel rim with tire", "textureResolution": 1024,
         "ambientOcclusion": {"map": {"type": "procedural"}, "intensity": 0.4}, "localOverrides": [],
         "textureProjection": {"mode": "uv", "texelDensity": 256},
         "roughnessMap": {"enabled": True, "source": "procedural"},
         "surfaceFrequencyBands": [{"id": "macro", "amplitude": 0.5, "frequency": 1.0},
                                    {"id": "meso", "amplitude": 0.3, "frequency": 0.3},
                                    {"id": "micro", "amplitude": 0.1, "frequency": 0.05}],
         "colorVariation": {"primary": "#C0C0C0", "secondary": "#D0D0D0", "accent": "#A0A0A0",
                            "notes": "Estimated"}},
        {"id": "headlight-mat", "displayName": "Headlight lens", "type": "standard",
         "albedo": {"hex": "#EEF4F8", "type": "sRGB"},
         "roughness": {"base": 0.2, "map": {"type": "procedural", "variation": 0.05}, "variation": 0.05},
         "metalness": 0.0, "transmission": 0.3, "ior": 1.4, "normalMap": None,
         "clearcoat": 0.5, "clearcoatRoughness": 0.2,
         "notes": "Clear headlight lens with reflective housing", "textureResolution": 1024,
         "ambientOcclusion": {"map": {"type": "procedural"}, "intensity": 0.2}, "localOverrides": [],
         "textureProjection": {"mode": "uv", "texelDensity": 256},
         "roughnessMap": {"enabled": True, "source": "procedural"},
         "surfaceFrequencyBands": [{"id": "macro", "amplitude": 0.3, "frequency": 1.0},
                                    {"id": "meso", "amplitude": 0.2, "frequency": 0.5},
                                    {"id": "micro", "amplitude": 0.1, "frequency": 0.1}],
         "colorVariation": {"primary": "#EEF4F8", "secondary": "#F4F8FC", "accent": "#E0E8F0",
                            "notes": "Estimated"}},
        {"id": "rubber", "displayName": "Tire rubber", "type": "standard",
         "albedo": {"hex": "#1A1A1A", "type": "sRGB"},
         "roughness": {"base": 0.95, "map": {"type": "procedural", "variation": 0.1}, "variation": 0.1},
         "metalness": 0.0, "normalMap": None,
         "notes": "Dark tire rubber", "textureResolution": 1024,
         "ambientOcclusion": {"map": {"type": "procedural"}, "intensity": 0.6}, "localOverrides": [],
         "textureProjection": {"mode": "uv", "texelDensity": 256},
         "roughnessMap": {"enabled": True, "source": "procedural"},
         "surfaceFrequencyBands": [{"id": "macro", "amplitude": 0.5, "frequency": 1.0},
                                    {"id": "meso", "amplitude": 0.3, "frequency": 0.3},
                                    {"id": "micro", "amplitude": 0.1, "frequency": 0.05}],
         "colorVariation": {"primary": "#1A1A1A", "secondary": "#222222", "accent": "#111111",
                            "notes": "Estimated"}},
    ]

    repetition_systems = [
        {"id": "wheel-repetition", "name": "Wheel assembly (4 corners)", "type": "patterned-layout",
         "componentRef": "wheel-fl", "distribution": "rectangular corners", "count": 4, "spacing": 0.1,
         "variation": {"positionVariation": 0.0, "rotationVariation": 0.0, "scaleVariation": 0.0},
         "instances": [{"id": "wheel-fl", "position": [1.2, 0.15, -1.0]},
                        {"id": "wheel-fr", "position": [1.2, 0.15, 1.0]},
                        {"id": "wheel-rl", "position": [-1.3, 0.15, -1.0]},
                        {"id": "wheel-rr", "position": [-1.3, 0.15, 1.0]}]},
    ]

    # Update spec
    spec["componentTree"] = [comps[cid] for cid in
                             ["root", "body", "windshield", "side-windows", "front-grille",
                              "headlight-left", "headlight-right", "bumper",
                              "wheel-fl", "wheel-fr", "wheel-rl", "wheel-rr",
                              "mirror-left", "mirror-right"]]
    spec["materials"] = materials
    spec["repetitionSystems"] = repetition_systems

    # Fix pre-spec assessment scores (must be 0-3)
    cx_scores = spec.get("preSpecAssessment", {}).get("complexity", {}).get("scores", {})
    for k in cx_scores:
        v = cx_scores[k]
        if isinstance(v, int) and v > 3:
            cx_scores[k] = min(v, 3)
    if any(v > 3 if isinstance(v, int) else False for v in cx_scores.values()):
        print("Fixed pre-spec assessment scores to 0-3 range")

    spec["lighting"] = {
        "setup": "studio-key-fill",
        "key": {"direction": [1, -1, 2], "intensity": 1.0, "color": "#FFFFFF"},
        "fill": {"direction": [-1, 0, 1], "intensity": 0.5, "color": "#FFFFFF"},
        "rim": {"direction": [0, 1, -1], "intensity": 0.3, "color": "#FFFFFF"},
        "environment": {"type": "studio", "intensity": 0.3},
    }

    spec["featureReviewTargets"] = [
        {"id": "silhouette", "name": "Car silhouette and proportions", "required": True, "tier": "critical"},
        {"id": "grille", "name": "Hexagonal grille shape and chrome slats", "required": True, "tier": "critical"},
        {"id": "headlights", "name": "Headlight shape and placement", "required": True, "tier": "critical"},
        {"id": "wheels", "name": "Wheel position, size, and proportions", "required": True, "tier": "important"},
        {"id": "body-panels", "name": "Body panel gaps and character lines", "required": True, "tier": "important"},
    ]

    SPEC_PATH.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    comp_count = len(spec["componentTree"])
    mat_count = len(spec["materials"])
    print(f"Wrote spec: {comp_count} components, {mat_count} materials")

    # Validate
    print("Validating spec...")
    subprocess.run([sys.executable, str(SKILL_DIR / "forge" / "stage2_spec" / "validate_sculpt_spec.py"),
                    str(SPEC_PATH)], cwd=SKILL_DIR)

    # Generate factory
    print("Generating Three.js factory code...")
    result = subprocess.run(
        [sys.executable, str(SKILL_DIR / "forge" / "stage3_build" / "generate_threejs_factory.py"),
         str(SPEC_PATH), "--out", str(MODEL_OUT), "--force"],
        cwd=SKILL_DIR, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout.strip()[:500])
    if result.returncode != 0:
        print(result.stderr[:1000])
        sys.exit(1)

    lines = len(MODEL_OUT.read_text().splitlines())
    size = MODEL_OUT.stat().st_size
    print(f"\nDone! Model: {MODEL_OUT}")
    print(f"  {lines} lines, {size} bytes")
    print(f"  Components: {comp_count}")
    print(f"  Preview: http://localhost:3003")


if __name__ == "__main__":
    build_car_spec()
