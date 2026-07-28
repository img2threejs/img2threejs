#!/usr/bin/env python3
"""auto_tree: Enrich a skeleton sculpt-spec with a full component tree from assessment data.

Usage:
  python3 tools/auto_tree.py <assessment.json> <spec.json>
  python3 tools/auto_tree.py <assessment.json> <spec.json> --out <output.json> [--force]

Templates are loaded from JSON files in the ``templates/`` directory
(sibling to this script).  Built-in template dicts in this file are fallbacks
for development but external JSON is the canonical source.
When a match is found, the skeleton spec is enriched with components, materials,
repetition systems, lighting, and feature review targets from the best template.
Otherwise the skeleton is returned unchanged (with a warning).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = TOOLS_DIR / "templates"


# ── External template loader ────────────────────────────────────────────


def load_external_templates(templates_dir: Path = TEMPLATES_DIR) -> list[dict[str, Any]]:
    """Load all JSON template files from *templates_dir*.

    Each file must contain a dict with at minimum ``categoryMatchers``.
    Files that fail to parse are skipped with a warning.
    """
    loaded: list[dict[str, Any]] = []
    if not templates_dir.is_dir():
        return loaded
    for fpath in sorted(templates_dir.iterdir()):
        if fpath.suffix.lower() not in (".json",):
            continue
        try:
            tmpl = json.loads(fpath.read_text(encoding="utf-8"))
            if not isinstance(tmpl, dict) or "categoryMatchers" not in tmpl:
                print(f"   ⚠️  Skipping {fpath.name}: missing 'categoryMatchers'")
                continue
            loaded.append(tmpl)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"   ⚠️  Skipping {fpath.name}: {exc}")
    return loaded


# ── Helpers ──────────────────────────────────────────────────────────────


def _eval(expr: str, dims: dict[str, float]) -> float:
    """Evaluate a math expression in a sandboxed namespace of dimension vars."""
    return eval(expr, {"__builtins__": {}}, dims)


def _resolve(val: Any, dims: dict[str, float]) -> Any:
    """Resolve {'$eval': 'L * 0.95'} to the computed float."""
    if isinstance(val, dict) and "$eval" in val:
        return _eval(val["$eval"], dims)
    if isinstance(val, dict):
        return {k: _resolve(v, dims) for k, v in val.items()}
    if isinstance(val, list):
        return [_resolve(v, dims) for v in val]
    return val


def pascal(s: str) -> str:
    return s.replace("-", " ").replace("_", " ").title().replace(" ", "")


# ── Component macro helper ──────────────────────────────────────────────


def C(
    cid: str,
    name: str,
    level: str,
    primitive: str,
    parent: str | None,
    material: str,
    dims: dict,
    transform: dict | None = None,
    attach: dict | None = None,
    kids: list[str] | None = None,
    details: list[str] | None = None,
    imp: float = 0.7,
    conf: float = 0.7,
    topo: str = "assembled-solid",
    topo_r: str = "",
    features: list[str] | None = None,
    geometryDescriptor: dict | None = None,
) -> dict:
    """Create a component dict matching the sculpt spec format."""
    c: dict[str, Any] = {
        "id": cid,
        "name": name,
        "level": level,
        "primitive": primitive,
        "parent": parent,
        "material": material,
        "importance": imp,
        "confidence": conf,
        "topologyClass": topo,
        "topologyRationale": topo_r,
        "dimensions": dims,
    }
    if transform:
        c["transform"] = transform
    if attach:
        c["attachment"] = attach
    if kids:
        c["children"] = kids
    if details:
        c["details"] = details
    if features:
        c["localFeatures"] = features
    if geometryDescriptor:
        c["geometryDescriptor"] = geometryDescriptor
    return c


def T(tx: float = 0, ty: float = 0, tz: float = 0) -> dict:
    return {"tx": tx, "ty": ty, "tz": tz}


def A(parent_socket: str = "root", contact: str = "continuous",
      embed: float = 0.01, overlap: float = 0.0,
      start=(0, 0, 0), end=(0, 0, 0)) -> dict:
    return {
        "parentSocket": parent_socket,
        "contactType": contact,
        "embedDepth": embed,
        "overlap": overlap,
        "localStart": list(start),
        "localEnd": list(end),
    }


# ── Templates ───────────────────────────────────────────────────────────


CAR_TEMPLATE: dict[str, Any] = {
    "categoryMatchers": [
        "automobile", "car", "hatchback", "sedan", "truck", "vehicle",
        "van", "suv", "pickup", "crossover", "wagon", "coupe", "convertible",
    ],
    "description": "Car/automobile with body, wheels, windows, lights, mirrors, bumpers",
    "defaultDimensions": {"L": 4.0, "W": 1.7, "H": 1.5},

    "components": [
        # root is always present; we add children to it. No need to re-add root.

        C("body", "Car body shell", "macro", "box", "root", "body-paint",
          {"width": {"$eval": "L * 0.95"}, "height": {"$eval": "H * 0.7"},
           "depth": {"$eval": "W * 0.95"}, "units": "meters", "confidence": 0.85},
          T(0, {"$eval": "H * 0.15"}, 0),
          A("root", "continuous", 0.01, 0.0, (0, 0, 0), (0, 0, 0)),
          imp=1.0, conf=0.9, topo="assembled-solid",
          topo_r="Main body shell with compound curves, panel gaps, character lines.",
          kids=["windshield", "side-windows", "front-grille",
                "headlight-left", "headlight-right", "bumper"],
          features=["body-character-line"]),

        C("windshield", "Front windshield", "meso", "plane-card", "body", "glass",
          {"width": 1.2, "height": 0.7, "depth": 0.02,
           "units": "meters", "confidence": 0.8},
          T(1.5, 0.5, 0),
          A("body", "recessed", 0.005, 0.002, (1.5, 0.5, 0), (1.5, 0.5, 0)),
          topo="conforming-shell", topo_r="Thin curved transparent panel."),

        C("side-windows", "Side windows", "meso", "plane-card", "body", "glass",
          {"width": 1.8, "height": 0.4, "depth": 0.02,
           "units": "meters", "confidence": 0.75},
          T(0.2, 0.5, {"$eval": "W * 0.5 + 0.01"}),
          A("body", "recessed", 0.005, 0.002,
            (0.2, 0.5, {"$eval": "W * 0.5 + 0.01"}),
            (0.2, 0.5, {"$eval": "W * 0.5 + 0.01"})),
          topo="conforming-shell", topo_r="Thin curved side window panels."),

        C("front-grille", "Hexagonal front grille", "meso", "plane-card", "body", "chrome",
          {"width": 0.5, "height": 0.25, "depth": 0.05,
           "units": "meters", "confidence": 0.85},
          T({"$eval": "L * 0.5"}, 0.15, 0),
          A("body", "continuous", 0.01, 0.001,
            ({"$eval": "L * 0.5"}, 0.15, 0),
            ({"$eval": "L * 0.5"}, 0.15, 0)),
          features=["grille-chrome-slats"],
          topo="surface-relief", topo_r="Grille with chrome slats on body surface."),

        C("headlight-left", "Left headlight", "meso", "sphere", "body", "headlight-mat",
          {"width": 0.2, "height": 0.12, "depth": 0.1,
           "units": "meters", "confidence": 0.85},
          T({"$eval": "L * 0.49"}, 0.35, {"$eval": "-W * 0.35"}),
          A("body", "recessed", 0.01, 0.002,
            ({"$eval": "L * 0.49"}, 0.35, {"$eval": "-W * 0.35"}),
            ({"$eval": "L * 0.49"}, 0.35, {"$eval": "-W * 0.35"})),
          topo="continuous-sculpt", topo_r="Smooth swept-back headlamp housing on front wing."),

        C("headlight-right", "Right headlight", "meso", "sphere", "body", "headlight-mat",
          {"width": 0.2, "height": 0.12, "depth": 0.1,
           "units": "meters", "confidence": 0.85},
          T({"$eval": "L * 0.49"}, 0.35, {"$eval": "W * 0.35"}),
          A("body", "recessed", 0.01, 0.002,
            ({"$eval": "L * 0.49"}, 0.35, {"$eval": "W * 0.35"}),
            ({"$eval": "L * 0.49"}, 0.35, {"$eval": "W * 0.35"})),
          topo="continuous-sculpt", topo_r="Smooth swept-back headlamp housing on front wing."),

        C("bumper", "Front bumper", "meso", "box", "body", "plastic-trim",
          {"width": 0.3, "height": 0.25, "depth": {"$eval": "W * 0.5"},
           "units": "meters", "confidence": 0.8},
          T({"$eval": "L * 0.51"}, -0.05, 0),
          A("body", "continuous", 0.01, 0.001,
            ({"$eval": "L * 0.51"}, -0.05, 0),
            ({"$eval": "L * 0.51"}, -0.05, 0)),
          features=["fog-light-bezels", "black-bumper-inserts"],
          topo="assembled-solid", topo_r="Boxy front bumper below grille."),

        C("wheel-fl", "Wheel front-left", "meso", "cylinder", "root", "alloy",
          {"width": 0.3, "height": 0.3, "depth": 0.25,
           "units": "meters", "confidence": 0.85},
          T({"$eval": "L * 0.3"}, 0.15, {"$eval": "-W * 0.6"}),
          A("root", "continuous", 0.005, 0.005,
            ({"$eval": "L * 0.3"}, 0.15, {"$eval": "-W * 0.6"}),
            ({"$eval": "L * 0.3"}, 0.15, {"$eval": "-W * 0.6"})),
          features=["multi-spoke-wheels"]),

        C("wheel-fr", "Wheel front-right", "meso", "cylinder", "root", "alloy",
          {"width": 0.3, "height": 0.3, "depth": 0.25,
           "units": "meters", "confidence": 0.85},
          T({"$eval": "L * 0.3"}, 0.15, {"$eval": "W * 0.6"}),
          A("root", "continuous", 0.005, 0.005,
            ({"$eval": "L * 0.3"}, 0.15, {"$eval": "W * 0.6"}),
            ({"$eval": "L * 0.3"}, 0.15, {"$eval": "W * 0.6"})),
          features=["multi-spoke-wheels"]),

        C("wheel-rl", "Wheel rear-left", "meso", "cylinder", "root", "alloy",
          {"width": 0.3, "height": 0.3, "depth": 0.25,
           "units": "meters", "confidence": 0.85},
          T({"$eval": "-L * 0.325"}, 0.15, {"$eval": "-W * 0.6"}),
          A("root", "continuous", 0.005, 0.005,
            ({"$eval": "-L * 0.325"}, 0.15, {"$eval": "-W * 0.6"}),
            ({"$eval": "-L * 0.325"}, 0.15, {"$eval": "-W * 0.6"}))),

        C("wheel-rr", "Wheel rear-right", "meso", "cylinder", "root", "alloy",
          {"width": 0.3, "height": 0.3, "depth": 0.25,
           "units": "meters", "confidence": 0.85},
          T({"$eval": "-L * 0.325"}, 0.15, {"$eval": "W * 0.6"}),
          A("root", "continuous", 0.005, 0.005,
            ({"$eval": "-L * 0.325"}, 0.15, {"$eval": "W * 0.6"}),
            ({"$eval": "-L * 0.325"}, 0.15, {"$eval": "W * 0.6"}))),

        C("mirror-left", "Left side mirror", "micro", "box", "root", "plastic-trim",
          {"width": 0.15, "height": 0.1, "depth": 0.08,
           "units": "meters", "confidence": 0.8},
          T({"$eval": "L * 0.35"}, 0.6, {"$eval": "-W * 0.56"}),
          A("root", "continuous", 0.005, 0.002,
            ({"$eval": "L * 0.35"}, 0.6, {"$eval": "-W * 0.56"}),
            ({"$eval": "L * 0.35"}, 0.6, {"$eval": "-W * 0.56"})),
          features=["turn-signal-mirrors"]),

        C("mirror-right", "Right side mirror", "micro", "box", "root", "plastic-trim",
          {"width": 0.15, "height": 0.1, "depth": 0.08,
           "units": "meters", "confidence": 0.8},
          T({"$eval": "L * 0.35"}, 0.6, {"$eval": "W * 0.56"}),
          A("root", "continuous", 0.005, 0.002,
            ({"$eval": "L * 0.35"}, 0.6, {"$eval": "W * 0.56"}),
            ({"$eval": "L * 0.35"}, 0.6, {"$eval": "W * 0.56"}))),
    ],

    "materials": [
        {"id": "body-paint", "displayName": "Body paint", "type": "standard",
         "albedo": {"hex": "#F0F0F0", "type": "sRGB"},
         "roughness": {"base": 0.3, "map": {"type": "procedural", "variation": 0.1}},
         "metalness": 0.0, "clearcoat": 0.3, "clearcoatRoughness": 0.4},
        {"id": "glass", "displayName": "Glass", "type": "standard",
         "albedo": {"hex": "#C8E8F0", "type": "sRGB"},
         "roughness": {"base": 0.05}, "metalness": 0.0,
         "transmission": 0.85, "ior": 1.5, "clearcoat": 0.0},
        {"id": "plastic-trim", "displayName": "Black plastic trim", "type": "standard",
         "albedo": {"hex": "#1A1A1A", "type": "sRGB"},
         "roughness": {"base": 0.7}, "metalness": 0.0, "clearcoat": 0.0},
        {"id": "chrome", "displayName": "Chrome trim", "type": "standard",
         "albedo": {"hex": "#E8E8E8", "type": "sRGB"},
         "roughness": {"base": 0.1}, "metalness": 1.0, "clearcoat": 0.5},
        {"id": "alloy", "displayName": "Alloy wheel", "type": "standard",
         "albedo": {"hex": "#A0A0A0", "type": "sRGB"},
         "roughness": {"base": 0.4}, "metalness": 0.8, "clearcoat": 0.2},
        {"id": "headlight-mat", "displayName": "Headlight housing", "type": "standard",
         "albedo": {"hex": "#CCCCCC", "type": "sRGB"},
         "roughness": {"base": 0.2}, "metalness": 0.0, "clearcoat": 0.8},
        {"id": "rubber", "displayName": "Rubber tire", "type": "standard",
         "albedo": {"hex": "#222222", "type": "sRGB"},
         "roughness": {"base": 0.9}, "metalness": 0.0, "clearcoat": 0.0},
    ],

    "repetitionSystems": [
        {"id": "wheel-repetition",
         "description": "Four wheels at corners, mirrored left-right and front-rear",
         "pattern": "mirror-quad",
         "components": ["wheel-fl", "wheel-fr", "wheel-rl", "wheel-rr"],
         "variations": [{"component": "wheel-rl", "transform": {"$eval": "T(-L*0.625, 0, -W*0.6)"}},
                        {"component": "wheel-rr", "transform": {"$eval": "T(-L*0.625, 0, W*0.6)"}}]},
        {"id": "mirror-repetition",
         "description": "Side mirrors, mirrored left-right",
         "pattern": "mirror-pair",
         "components": ["mirror-left", "mirror-right"]},
    ],

    "lighting": {
        "setup": "studio-key-fill",
        "key": {"direction": [1, -1, 2], "intensity": 1.0, "color": "#FFFFFF"},
        "fill": {"direction": [-1, 0, 1], "intensity": 0.5, "color": "#FFFFFF"},
        "rim": {"direction": [0, 1, -1], "intensity": 0.3, "color": "#FFFFFF"},
        "environment": {"type": "studio", "intensity": 0.3},
    },

    "featureReviewTargets": [
        {"id": "silhouette", "name": "Car silhouette and proportions", "required": True,
         "tier": "critical"},
        {"id": "body-form", "name": "Body shell compound curves and panel gaps", "required": True,
         "tier": "critical"},
        {"id": "wheel-placement", "name": "Wheel arch position and size relative to body",
         "required": True, "tier": "critical"},
        {"id": "grille-headlights", "name": "Grille and headlight shape and placement",
         "required": True, "tier": "important"},
        {"id": "materials", "name": "Material response: paint, glass, chrome, plastic, rubber",
         "required": False, "tier": "detail"},
    ],

    "detailMappings": [
        {"detailId": "grille-chrome-slats", "componentId": "front-grille",
         "featureType": "geometry"},
        {"detailId": "swept-headlamps", "componentId": "headlight-left",
         "featureType": "geometry"},
        {"detailId": "fog-light-bezels", "componentId": "bumper",
         "featureType": "geometry"},
        {"detailId": "multi-spoke-wheels", "componentId": "wheel-fl",
         "featureType": "geometry"},
        {"detailId": "body-character-line", "componentId": "body",
         "featureType": "geometry"},
        {"detailId": "turn-signal-mirrors", "componentId": "mirror-left",
         "featureType": "geometry"},
        {"detailId": "black-bumper-inserts", "componentId": "bumper",
         "featureType": "material"},
        {"detailId": "white-metallic-paint", "componentId": "body",
         "featureType": "material"},
    ],
}

HANDGUN_TEMPLATE: dict[str, Any] = {
    "categoryMatchers": [
        "pistol", "handgun", "gun", "firearm", "desert eagle", "weapon",
        "revolver", "semiautomatic", "semi-automatic",
    ],
    "description": "Handgun/pistol with slide, barrel, grip, trigger guard, trigger, magazine, sights",
    "defaultDimensions": {"L": 0.25, "W": 0.14, "H": 0.03},

    "components": [
        C("slide", "Slide / upper receiver", "macro", "extrude", "root", "gunmetal",
          {"width": {"$eval": "L * 0.65"}, "height": {"$eval": "H * 0.35"},
           "depth": {"$eval": "W * 0.85"}, "units": "meters", "confidence": 0.9},
          T({"$eval": "L * 0.12"}, {"$eval": "H * 0.35"}, 0),
          A("root", "continuous", 0.005, 0.0,
            ({"$eval": "L * 0.12"}, {"$eval": "H * 0.35"}, 0),
            ({"$eval": "L * 0.12"}, {"$eval": "H * 0.35"}, 0)),
          imp=1.0, conf=0.95, topo="assembled-solid",
          topo_r="Main upper slide body with serrations, ejection port, and sight mounts.",
          kids=["barrel", "rear-sight", "front-sight"],
          geometryDescriptor={
              "profile2D": {
                  "points": [
                      [-0.5, -0.35],
                      [-0.5, 0.3],
                      [-0.35, 0.5],
                      [-0.2, 0.45],
                      [0.0, 0.42],
                      [0.2, 0.42],
                      [0.38, 0.2],
                      [0.5, -0.05],
                      [0.5, -0.15],
                      [0.3, -0.3],
                      [-0.1, -0.35],
                      [-0.5, -0.35],
                  ],
                  "depth": 1.0,
              }
          }),

        C("barrel", "Barrel", "meso", "box", "slide", "gunmetal-dark",
          {"width": 0.012, "height": 0.012, "depth": {"$eval": "L * 0.5"},
           "units": "meters", "confidence": 0.9},
          T({"$eval": "L * 0.15"}, {"$eval": "-H * 0.05"}, 0),
          A("slide", "continuous", 0.01, 0.0,
            ({"$eval": "L * 0.15"}, {"$eval": "-H * 0.05"}, 0),
            ({"$eval": "L * 0.15"}, {"$eval": "-H * 0.05"}, 0)),
          topo="assembled-solid", topo_r="Rectangular barrel extending forward from slide.",
          features=["muzzle", "barrel-port"]),

        C("rear-sight", "Rear sight", "micro", "box", "slide", "sight",
          {"width": 0.025, "height": 0.01, "depth": 0.005,
           "units": "meters", "confidence": 0.85},
          T({"$eval": "-L * 0.05"}, {"$eval": "H * 0.55"}, 0),
          A("slide", "continuous", 0.005, 0.0,
            ({"$eval": "-L * 0.05"}, {"$eval": "H * 0.55"}, 0),
            ({"$eval": "-L * 0.05"}, {"$eval": "H * 0.55"}, 0)),
          topo="assembled-solid", topo_r="Small rear notch sight dovetailed onto slide."),

        C("front-sight", "Front sight", "micro", "box", "slide", "sight",
          {"width": 0.015, "height": 0.012, "depth": 0.005,
           "units": "meters", "confidence": 0.85},
          T({"$eval": "L * 0.4"}, {"$eval": "H * 0.55"}, 0),
          A("slide", "continuous", 0.005, 0.0,
            ({"$eval": "L * 0.4"}, {"$eval": "H * 0.55"}, 0),
            ({"$eval": "L * 0.4"}, {"$eval": "H * 0.55"}, 0)),
          topo="assembled-solid", topo_r="Small front blade sight on slide."),

        C("grip", "Grip frame / lower receiver", "macro", "extrude", "root", "polymer",
          {"width": {"$eval": "L * 0.3"}, "height": {"$eval": "H * 0.45"},
           "depth": {"$eval": "W * 0.45"}, "units": "meters", "confidence": 0.9},
          T({"$eval": "-L * 0.25"}, {"$eval": "-H * 0.4"}, 0),
          A("root", "continuous", 0.005, 0.01,
            ({"$eval": "-L * 0.25"}, {"$eval": "-H * 0.4"}, 0),
            ({"$eval": "-L * 0.25"}, {"$eval": "-H * 0.4"}, 0)),
          features=["grip-texture", "finger-grooves", "beavertail"],
          kids=["trigger-guard", "magazine"],
          geometryDescriptor={
              "profile2D": {
                  "points": [
                      [-0.4, -0.45],
                      [0.3, -0.45],
                      [0.35, -0.2],
                      [0.4, 0.1],
                      [0.35, 0.4],
                      [-0.3, 0.4],
                      [-0.4, 0.1],
                      [-0.45, -0.2],
                      [-0.4, -0.45],
                  ],
                  "depth": 1.0,
              }
          }),

        C("trigger-guard", "Trigger guard", "meso", "tube", "grip", "polymer",
          {"width": 0.035, "height": 0.03, "depth": 0.02,
           "units": "meters", "confidence": 0.85},
          T(0.02, {"$eval": "-H * 0.15"}, 0),
          A("grip", "continuous", 0.005, 0.002,
            (0.02, {"$eval": "-H * 0.15"}, 0),
            (0.02, {"$eval": "-H * 0.15"}, 0)),
          features=["trigger-guard-underside"],
          topo="continuous-sculpt", topo_r="Arched guard protecting the trigger.",
          geometryDescriptor={
              "tubePath": {
                  "points": [
                      [-0.45, 0.45, 0],
                      [-0.35, 0.0, 0],
                      [-0.15, -0.45, 0],
                      [0.15, -0.45, 0],
                      [0.35, 0.0, 0],
                      [0.45, 0.45, 0],
                  ],
                  "radius": 0.06,
                  "closed": False,
              }
          }),

        C("trigger", "Trigger", "micro", "box", "root", "gunmetal-dark",
          {"width": 0.01, "height": 0.025, "depth": 0.005,
           "units": "meters", "confidence": 0.85},
          T({"$eval": "L * 0.0"}, {"$eval": "-H * 0.05"}, {"$eval": "W * 0.25"}),
          A("root", "continuous", 0.005, 0.001,
            ({"$eval": "L * 0.0"}, {"$eval": "-H * 0.05"}, {"$eval": "W * 0.25"}),
            ({"$eval": "L * 0.0"}, {"$eval": "-H * 0.05"}, {"$eval": "W * 0.25"})),
          topo="assembled-solid", topo_r="Small curved trigger blade within the guard."),

        C("magazine", "Magazine", "meso", "box", "grip", "gunmetal-dark",
          {"width": 0.025, "height": {"$eval": "H * 0.25"}, "depth": {"$eval": "W * 0.4"},
           "units": "meters", "confidence": 0.85},
          T({"$eval": "-L * 0.25"}, {"$eval": "-H * 0.65"}, 0),
          A("grip", "continuous", 0.01, 0.005,
            ({"$eval": "-L * 0.25"}, {"$eval": "-H * 0.65"}, 0),
            ({"$eval": "-L * 0.25"}, {"$eval": "-H * 0.65"}, 0)),
          features=["magazine-floor-plate"],
          topo="assembled-solid", topo_r="Rectangular magazine inserted into grip."),

        C("safety", "Safety / decocker lever", "micro", "box", "root", "gunmetal-dark",
          {"width": 0.01, "height": 0.008, "depth": 0.02,
           "units": "meters", "confidence": 0.75},
          T({"$eval": "L * 0.0"}, {"$eval": "H * 0.25"}, {"$eval": "W * 0.5"}),
          A("root", "continuous", 0.005, 0.0,
            ({"$eval": "L * 0.0"}, {"$eval": "H * 0.25"}, {"$eval": "W * 0.5"}),
            ({"$eval": "L * 0.0"}, {"$eval": "H * 0.25"}, {"$eval": "W * 0.5"})),
          topo="assembled-solid", topo_r="Small thumb safety lever on frame."),
    ],

    "materials": [
        {"id": "gunmetal", "displayName": "Gunmetal steel", "type": "standard",
         "albedo": {"hex": "#2A2A2E", "type": "sRGB"},
         "roughness": {"base": 0.35, "map": {"type": "procedural", "variation": 0.05}},
         "metalness": 0.9, "clearcoat": 0.1, "clearcoatRoughness": 0.6},
        {"id": "gunmetal-dark", "displayName": "Dark gunmetal / blued steel", "type": "standard",
         "albedo": {"hex": "#1A1A1E", "type": "sRGB"},
         "roughness": {"base": 0.4}, "metalness": 0.95, "clearcoat": 0.05},
        {"id": "polymer", "displayName": "Polymer grip frame", "type": "standard",
         "albedo": {"hex": "#1A1A1A", "type": "sRGB"},
         "roughness": {"base": 0.8}, "metalness": 0.0, "clearcoat": 0.0},
        {"id": "sight", "displayName": "Sight (white dot)", "type": "standard",
         "albedo": {"hex": "#FFFFFF", "type": "sRGB"},
         "roughness": {"base": 0.3}, "metalness": 0.0, "clearcoat": 0.2},
        {"id": "grip-insert", "displayName": "Grip insert / texture panel", "type": "standard",
         "albedo": {"hex": "#0D0D0D", "type": "sRGB"},
         "roughness": {"base": 0.9}, "metalness": 0.0, "clearcoat": 0.0},
    ],

    "repetitionSystems": [
        {"id": "sight-pair",
         "description": "Rear and front sight aligned on slide top",
         "pattern": "linear-pair",
         "components": ["rear-sight", "front-sight"],
         "variations": [{"component": "front-sight",
                          "transform": {"$eval": "T(L*0.4, H*0.55, 0)"}}]},
    ],

    "lighting": {
        "setup": "studio-key-fill",
        "key": {"direction": [1, -0.5, 2], "intensity": 1.2, "color": "#FFFFFF"},
        "fill": {"direction": [-1, 0.5, 1], "intensity": 0.4, "color": "#E8E8FF"},
        "rim": {"direction": [0, 1, -1.5], "intensity": 0.3, "color": "#FFFFFF"},
        "environment": {"type": "studio", "intensity": 0.2},
    },

    "featureReviewTargets": [
        {"id": "silhouette", "name": "Gun silhouette and proportions", "required": True,
         "tier": "critical"},
        {"id": "slide-barrel", "name": "Slide and barrel alignment and profile",
         "required": True, "tier": "critical"},
        {"id": "grip-frame", "name": "Grip angle, texture, and trigger guard shape",
         "required": True, "tier": "important"},
        {"id": "sights", "name": "Sight alignment on slide top", "required": True,
         "tier": "important"},
        {"id": "materials", "name": "Material contrast: steel vs polymer vs sight dots",
         "required": False, "tier": "detail"},
    ],

    "detailMappings": [
        {"detailId": "muzzle", "componentId": "barrel", "featureType": "geometry"},
        {"detailId": "barrel-port", "componentId": "barrel", "featureType": "geometry"},
        {"detailId": "grip-texture", "componentId": "grip", "featureType": "material"},
        {"detailId": "finger-grooves", "componentId": "grip", "featureType": "geometry"},
        {"detailId": "beavertail", "componentId": "grip", "featureType": "geometry"},
        {"detailId": "magazine-floor-plate", "componentId": "magazine", "featureType": "geometry"},
        {"detailId": "trigger-guard-underside", "componentId": "trigger-guard", "featureType": "geometry"},
    ],
}

TEMPLATES: list[dict[str, Any]] = [
    CAR_TEMPLATE,
    HANDGUN_TEMPLATE,
    # External JSON templates from templates/ directory are appended at runtime
]

def get_all_templates(extra_dir: Path | None = None) -> list[dict[str, Any]]:
    """Return built-in + external templates.

    External JSON files from *TEMPLATES_DIR* (and optionally *extra_dir*)
    are loaded and appended after built-in templates so external files can
    override only when they appear earlier in the list.
    """
    tmpls = list(TEMPLATES)
    for d in [TEMPLATES_DIR, extra_dir].copy():
        if d is not None:
            tmpls.extend(load_external_templates(d))
    return tmpls


# ── Matching ────────────────────────────────────────────────────────────


def score_template(tmpl: dict, primary_type: str) -> int:
    """Count how many category matchers appear in the lowercased primaryType."""
    lower = primary_type.lower()
    matchers = tmpl.get("categoryMatchers", [])
    return sum(1 for m in matchers if m.lower() in lower)


def select_template(primary_type: str, templates: list[dict] | None = None) -> dict | None:
    """Find the best-matching template from *templates* (default: all templates)."""
    candidates = templates if templates is not None else get_all_templates()
    best_score = 0
    best_tmpl = None
    for tmpl in candidates:
        score = score_template(tmpl, primary_type)
        if score > best_score:
            best_score = score
            best_tmpl = tmpl
    return best_tmpl


# ── Instantiation ───────────────────────────────────────────────────────


def instantiate_components(tmpl: dict, dims: dict) -> list[dict]:
    """Resolve $eval expressions in template components to concrete values."""
    resolved = []
    for comp in tmpl.get("components", []):
        c = _resolve(comp, dims)
        # Ensure dimensions are valid
        resolved.append(c)
    return resolved


def build_component_map(components: list[dict]) -> dict[str, dict]:
    """Map component id → component dict for cross-referencing."""
    return {c["id"]: c for c in components}


def get_root_material(tmpl: dict) -> str:
    """Return first material id (used as root's material)."""
    mats = tmpl.get("materials", [])
    if mats:
        return mats[0]["id"]
    return "default"


def enrich_spec(spec: dict, assessment: dict, tmpl: dict) -> dict:
    """Enrich a skeleton spec dict with template data."""
    # Get dimensions from template (can be overridden by assessment)
    dims = dict(tmpl.get("defaultDimensions", {}))

    # Scale dimensions based on template defaults (use template's own defaults)
    base_L = tmpl.get("defaultDimensions", {}).get("L", 1.0)
    base_W = tmpl.get("defaultDimensions", {}).get("W", 1.0)
    base_H = tmpl.get("defaultDimensions", {}).get("H", 1.0)
    est_counts = assessment.get("complexity", {}).get("estimatedCounts", {})
    macro_c = est_counts.get("macroComponents", 0)
    if macro_c >= 12:
        dims["L"] = dims.get("L", base_L) * 1.15
        dims["W"] = dims.get("W", base_W) * 1.1
        dims["H"] = dims.get("H", base_H) * 1.1
    elif macro_c <= 4:
        dims["L"] = dims.get("L", base_L) * 0.85
        dims["W"] = dims.get("W", base_W) * 0.9
        dims["H"] = dims.get("H", base_H) * 0.9

    # Instantiate components
    components = instantiate_components(tmpl, dims)
    # Convert tx/ty/tz transforms to position arrays for the factory
    for c in components:
        t = c.get("transform", {})
        if isinstance(t, dict) and "position" not in t:
            tx = t.get("tx", 0)
            ty = t.get("ty", 0)
            tz = t.get("tz", 0)
            if isinstance(tx, (int, float)) and isinstance(ty, (int, float)) and isinstance(tz, (int, float)):
                t["position"] = [tx, ty, tz]
    comp_map = build_component_map(components)

    # Collect root children from template (components with parent "root")
    root_children = [c["id"] for c in components if c.get("parent") == "root"]

    # Compute overall bounding box from child components
    # so root mesh encloses all children (prevents 1x1x1 skeleton root dominating the scene)
    bb_min = {"x": 0, "y": 0, "z": 0}
    bb_max = {"x": 0, "y": 0, "z": 0}
    for c in components:
        tx = c.get("transform", {}).get("tx", 0)
        ty = c.get("transform", {}).get("ty", 0)
        tz = c.get("transform", {}).get("tz", 0)
        dw = c.get("dimensions", {}).get("width", 0)
        dh = c.get("dimensions", {}).get("height", 0)
        dd = c.get("dimensions", {}).get("depth", 0)
        # Approximate extent from center + half-dimensions
        if isinstance(tx, (int, float)) and isinstance(dw, (int, float)):
            bb_min["x"] = min(bb_min["x"], tx - dw / 2)
            bb_max["x"] = max(bb_max["x"], tx + dw / 2)
        if isinstance(ty, (int, float)) and isinstance(dh, (int, float)):
            bb_min["y"] = min(bb_min["y"], ty - dh / 2)
            bb_max["y"] = max(bb_max["y"], ty + dh / 2)
        if isinstance(tz, (int, float)) and isinstance(dd, (int, float)):
            bb_min["z"] = min(bb_min["z"], tz - dd / 2)
            bb_max["z"] = max(bb_max["z"], tz + dd / 2)

    root_w = bb_max["x"] - bb_min["x"] if bb_max["x"] != bb_min["x"] else dims.get("L", 1)
    root_h = bb_max["y"] - bb_min["y"] if bb_max["y"] != bb_min["y"] else dims.get("H", 1)
    root_d = bb_max["z"] - bb_min["z"] if bb_max["z"] != bb_min["z"] else dims.get("W", 1)

    # Update existing root component
    skeleton_tree = spec.get("componentTree", [])
    root_found = False
    for i, comp in enumerate(skeleton_tree):
        if comp.get("id") == "root" or comp.get("level") == "root":
            root_found = True
            # Keep root's basic info, update children and dimensions
            comp["children"] = root_children
            comp["topologyClass"] = "assembled-solid"
            comp["dimensions"] = {
                "width": root_w, "height": root_h, "depth": root_d,
                "units": "meters", "confidence": 0.8,
            }
            if "topologyRationale" not in comp:
                comp["topologyRationale"] = "Root group for the assembled object."
            break

    if not root_found:
        # Create a root if none exists
        root_mat = get_root_material(tmpl)
        skeleton_tree.insert(0, {
            "id": "root",
            "name": "Object root",
            "level": "root",
            "primitive": "box",
            "parent": None,
            "material": root_mat,
            "importance": 1.0,
            "confidence": 1.0,
            "topologyClass": "assembled-solid",
            "topologyRationale": "Root group for assembled object.",
            "dimensions": {"width": dims.get("L", 1), "height": dims.get("H", 1),
                           "depth": dims.get("W", 1), "units": "meters", "confidence": 0.8},
            "children": root_children,
        })

    # Append template components (skip those already in tree)
    existing_ids = {c.get("id") for c in skeleton_tree}
    for c in components:
        if c["id"] not in existing_ids:
            skeleton_tree.append(c)

    spec["componentTree"] = skeleton_tree

    # Materials
    spec["materials"] = tmpl.get("materials", spec.get("materials", []))

    # Fix root material if it was replaced
    new_mat_ids = [m.get("id", "") for m in spec["materials"]]
    for comp in skeleton_tree:
        if comp.get("id") == "root" or comp.get("level") == "root":
            if comp.get("material", "") not in new_mat_ids:
                # Use first material from ordered list
                first_mat = new_mat_ids[0] if new_mat_ids else "default"
                comp["material"] = first_mat
                print(f"   Fix: root material updated to '{first_mat}'")

    # Repetition systems
    spec["repetitionSystems"] = tmpl.get("repetitionSystems",
                                         spec.get("repetitionSystems", []))

    # Lighting
    spec["lighting"] = tmpl.get("lighting", spec.get("lighting", {}))

    # Feature review targets
    spec["featureReviewTargets"] = tmpl.get("featureReviewTargets",
                                            spec.get("featureReviewTargets", []))

    # Detail inventory mappings — link assessment details to component localFeatures
    detail_inv = assessment.get("detailInventory", {})
    details_list = detail_inv.get("details", [])
    if details_list:
        detail_map = {d["detailId"]: d for d in tmpl.get("detailMappings", [])}
        for detail in details_list:
            did = detail.get("id", "")
            mapping = detail_map.get(did, {})
            comp_id = mapping.get("componentId", "")
            if comp_id and comp_id in comp_map:
                # Ensure the component has the detail in its localFeatures
                comp_data = comp_map[comp_id]
                flist = comp_data.get("localFeatures", [])
                if did not in flist:
                    comp_data["localFeatures"] = flist + [did]

    # Detail inventory in spec
    if "detailInventory" not in spec or not spec["detailInventory"].get("details"):
        spec["detailInventory"] = {
            "scanMethod": "assessment-auto-tree",
            "targetMinDetails": len(details_list),
            "details": details_list,
        }
    else:
        # Merge existing details
        existing_dids = {d.get("id") for d in spec.get("detailInventory", {}).get("details", [])}
        for d in details_list:
            if d.get("id") not in existing_dids:
                spec.setdefault("detailInventory", {}).setdefault("details", []).append(d)

    return spec


# ── Validation ──────────────────────────────────────────────────────────


def validate_enriched(spec: dict) -> list[str]:
    """Run basic consistency checks on the enriched spec. Returns warnings list."""
    warnings: list[str] = []

    comp_list = spec.get("componentTree", [])
    comp_ids = {c.get("id", "") for c in comp_list}
    mat_ids = {m.get("id", "") for m in spec.get("materials", [])}

    # Duplicate IDs
    seen: set[str] = set()
    for c in comp_list:
        cid = c.get("id", "")
        if cid in seen:
            warnings.append(f"⚠️  Duplicate component ID: {cid}")
        seen.add(cid)

    # Children reference existing IDs
    for c in comp_list:
        for child in c.get("children", []):
            if child not in comp_ids:
                warnings.append(f"⚠️  Child '{child}' of '{c.get('id')}' not found in componentTree")

    # Material references exist
    for c in comp_list:
        mat = c.get("material", "")
        if mat and mat not in mat_ids:
            warnings.append(f"⚠️  Material '{mat}' used by '{c.get('id')}' not defined")

    # featureReviewTargets have tier
    for frt in spec.get("featureReviewTargets", []):
        if "tier" not in frt:
            warnings.append(f"⚠️  featureReviewTarget '{frt.get('id')}' missing required 'tier' field")

    # topologyClass + primitive compatibility
    DISALLOWED_TOPOLOGY_PRIMITIVE_PAIRS: dict[str, set[str]] = {
        "continuous-sculpt": {"box", "cylinder", "cone"},
    }
    for c in comp_list:
        topo = c.get("topologyClass", "")
        prim = c.get("primitive", "")
        blocked = DISALLOWED_TOPOLOGY_PRIMITIVE_PAIRS.get(topo, set())
        if prim in blocked:
            warnings.append(f"⚠️  '{c.get('id')}': topologyClass '{topo}' incompatible "
                           f"with primitive '{prim}'. Suggested: {_suggest_primitives(topo)}")

    return warnings


def _suggest_primitives(topo: str) -> str:
    suggestions = {
        "continuous-sculpt": "lathe, curve-sweep, sphere, ellipsoid",
    }
    return suggestions.get(topo, "check topology documentation")


# ── Main CLI ────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Enrich a skeleton sculpt-spec with a full component tree from assessment data.")
    parser.add_argument("assessment", help="Path to pre-spec assessment JSON")
    parser.add_argument("spec", help="Path to skeleton sculpt spec JSON (modified in-place unless --out)")
    parser.add_argument("--out", help="Output path (default: modify spec in-place)")
    parser.add_argument("--force", action="store_true", help="Overwrite output if it exists")
    parser.add_argument("--template-dir", type=Path, default=None,
                        help="Extra directory of JSON template files to load")
    args = parser.parse_args()

    assessment_path = Path(args.assessment)
    spec_path = Path(args.spec)
    out_path = Path(args.out) if args.out else spec_path

    if not assessment_path.exists():
        print(f"❌ Assessment not found: {assessment_path}")
        sys.exit(1)
    if not spec_path.exists():
        print(f"❌ Spec not found: {spec_path}")
        sys.exit(1)

    if out_path.exists() and out_path != spec_path and not args.force:
        print(f"⚠️  Output exists: {out_path}")
        print("   Use --force to overwrite, or specify a different path.")
        sys.exit(1)

    # Load input files
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    assessment = json.loads(assessment_path.read_text(encoding="utf-8"))

    # Handle nested assessment structure (file may have preSpecAssessment wrapper)
    raw_assessment = assessment.get("preSpecAssessment", assessment)

    # Clamp scores (0-3 range)
    raw_scores = raw_assessment.get("complexity", {}).get("scores", {})
    if isinstance(raw_scores, dict):
        for key in list(raw_scores.keys()):
            val = raw_scores[key]
            if isinstance(val, (int, float)) and val > 3:
                raw_scores[key] = min(int(val), 3)

    # Get primary type
    object_class = raw_assessment.get("objectClass", {})
    primary_type = object_class.get("primaryType", "")
    if not primary_type:
        print("⚠️  Assessment has no objectClass.primaryType. Cannot match template.")
        print("   Saving spec unchanged.")
        out_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        sys.exit(0)

    print(f"🔍 Object type: {primary_type}")

    # Select template
    templates = get_all_templates(args.template_dir)
    tmpl = select_template(primary_type, templates)
    if tmpl is None:
        print(f"⚠️  No template matches '{primary_type}'. Saving spec unchanged.")
        print("   To add a template, place a JSON file in tools/templates/.")
        out_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        sys.exit(0)

    print(f"   Matched template: {tmpl.get('description', '?')}")

    # Enrich
    enriched = enrich_spec(spec, raw_assessment, tmpl)

    # Validate
    warnings = validate_enriched(enriched)
    if warnings:
        print(f"\n   Validation ({len(warnings)} warnings):")
        for w in warnings:
            print(f"     {w}")
    else:
        print("\n   ✅ Validation passed")

    # Save
    out_path.write_text(json.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8")

    comp_count = len(enriched.get("componentTree", []))
    mat_count = len(enriched.get("materials", []))
    print(f"\n✅ Wrote spec: {comp_count} components, {mat_count} materials")
    print(f"   → {out_path}")


if __name__ == "__main__":
    main()
