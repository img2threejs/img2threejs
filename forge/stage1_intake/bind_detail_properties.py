#!/usr/bin/env python3
"""Detail → PBR/geometry property auto-binding (Plan 1.3 Workstream J).

Standardized intake: turn a plain-language detail/material observation into concrete
Three.js material properties + geometry/primitive hints, so a vague one-line prompt
still produces a rigorous quality contract instead of "make it shiny". Deterministic
keyword mapping — no model, no token. The bound properties are exactly the ones the
full property surface (Workstream H) can emit.

Usage:
  bind("glossy jade blade with internal smoke") ->
    {materialProperties, primitiveHint, note}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

# Each rule: (name, keyword-regex, material-properties dict, primitive hint or None, note).
# Ordered most-specific first; all matching rules contribute (union of properties).
_RULES: list[tuple[str, str, dict[str, Any], str | None, str]] = [
    ("gemstone/glass", r"jade|gem|glass|crystal|translucent|transparent|amber|resin",
     {"transmission": 0.6, "ior": 1.6, "thickness": 0.5, "roughness": 0.1, "requiresEnvMap": True},
     None, "translucent → transmission+ior+thickness; needs an environment map to read correctly"),
    ("brushed-metal", r"brushed|anisotropic|satin metal|machined",
     {"metalness": 1.0, "roughness": 0.35, "anisotropy": 0.6, "anisotropyRotation": 0.0, "requiresEnvMap": True},
     None, "brushed metal → anisotropy for the directional highlight streak"),
    ("metal", r"metal|steel|chrome|gold|silver|brass|copper|iron|alumin",
     {"metalness": 1.0, "roughness": 0.3, "requiresEnvMap": True},
     None, "metal → metalness 1 + low roughness + env map"),
    ("glossy-coat", r"gloss|glossy|lacquer|wet look|polished paint|clear ?coat|enamel|varnish",
     {"clearcoat": 1.0, "clearcoatRoughness": 0.1, "requiresEnvMap": True},
     None, "glossy paint → clearcoat 1.0 + low clearcoatRoughness"),
    ("iridescent", r"iridescen|oil ?slick|soap|pearlescent|nacre|holograph",
     {"iridescence": 1.0, "iridescenceIOR": 1.3, "requiresEnvMap": True},
     None, "iridescent → iridescence + iridescenceIOR"),
    ("fabric", r"fabric|cloth|velvet|suede|felt|textile|woven|denim|cotton",
     {"sheen": 1.0, "sheenColor": "#ffffff", "sheenRoughness": 0.5, "metalness": 0.0, "roughness": 0.85},
     None, "fabric → sheen for the soft grazing-angle retroreflection"),
    ("emissive", r"emissive|glow|glowing|led|neon|luminous|backlit|screen|display",
     {"emissive": "#ffffff", "emissiveIntensity": 1.0},
     None, "emissive/glowing → emissive + emissiveIntensity"),
    ("rubber/matte", r"rubber|matte plastic|matte|grip|polymer|abs plastic",
     {"metalness": 0.0, "roughness": 0.8},
     None, "matte polymer → high roughness, non-metal"),
    ("fastener", r"screw|rivet|bolt|stud|fastener|nut|nail|grommet|eyelet",
     {},
     "instanced-cluster", "repeated fasteners → instancing (InstancedMesh), not N meshes"),
    ("decal", r"logo|emblem|stamp|decal|marking|engrav|wordmark|serial|inscription|label",
     {},
     "decal", "flat marking → DecalGeometry stamped onto the surface"),
]


def bind(description: str) -> dict[str, Any]:
    """Bind a free-text detail description to concrete properties + a primitive hint."""
    text = (description or "").lower()
    props: dict[str, Any] = {}
    notes: list[str] = []
    primitive_hint: str | None = None
    matched: list[str] = []
    for name, pattern, rule_props, hint, note in _RULES:
        if re.search(pattern, text):
            matched.append(name)
            for k, v in rule_props.items():
                props.setdefault(k, v)  # first (most-specific) rule wins on conflict
            if hint and primitive_hint is None:
                primitive_hint = hint
            notes.append(note)
    return {
        "matchedRules": matched,
        "materialProperties": props,
        "primitiveHint": primitive_hint,
        "notes": notes,
        "bound": bool(matched),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("description", help="free-text detail/material description")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = bind(args.description)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"matched: {', '.join(result['matchedRules']) or '(none)'}")
        print(f"properties: {result['materialProperties']}")
        print(f"primitiveHint: {result['primitiveHint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
