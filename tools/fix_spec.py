#!/usr/bin/env python3
"""fix_spec: 修复和裁剪 ObjectSculptSpec JSON

用法:
  python3 tools/fix_spec.py <spec.json> --auto                          # 自动修正常见验证错误
  python3 tools/fix_spec.py <spec.json> --prune <prefix>                # 删除名字以 prefix 开头的组件
  python3 tools/fix_spec.py <spec.json> --keep <id1,id2>                # 只保留指定的组件 ID
  python3 tools/fix_spec.py <spec.json> --auto --prune skin- --keep iris,pupil,cornea,sclera
  python3 tools/fix_spec.py <spec.json> --scope eye                     # 快捷: 只保留眼球相关组件
  python3 tools/fix_spec.py <spec.json> --scope face                    # 快捷: 只保留面部组件

范围预设:
  --scope eye    只保留: eye-root, sclera, iris, pupil, cornea (删除皮肤、眼睑、睫毛等)
  --scope face   保留: eye-root, eyelid-*, sclera, iris, pupil, cornea, skin-* (删除身体部分)
"""

import argparse
import json
import re
import sys
from pathlib import Path


SCOPES = {
    "eye": {
        "keep_patterns": [r"^eye-root$", r"^sclera$", r"^iris$", r"^pupil$", r"^cornea$"],
        "description": "Only eyeball components (no skin, eyelids, lashes)",
    },
    "face": {
        "keep_patterns": [r"^eye-root$", r"^eyelid", r"^sclera$", r"^iris$", r"^pupil$",
                          r"^cornea$", r"^eyelash", r"^skin-", r"^nose", r"^mouth", r"^ear"],
        "description": "Face and eye components only",
    },
}


def load_spec(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_spec(spec: dict, path: str) -> None:
    # Backup
    backup = path + ".bak"
    Path(path).rename(backup)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2, ensure_ascii=False)
    print(f"✅ Saved to {path}")
    print(f"📦 Backup at {backup}")


def fix_auto(spec: dict) -> int:
    """Apply common validation fixes. Returns number of fixes applied."""
    fixes = 0

    # 1. Fix material albedo: string → object
    for m in spec.get("materials", []):
        albedo = m.get("albedo")
        if isinstance(albedo, str) and albedo.startswith("#"):
            m["albedo"] = {"hex": albedo, "type": "sRGB"}
            fixes += 1
        # Fix ambientOcclusion: bool → object
        ao = m.get("ambientOcclusion")
        if ao is True or ao is False:
            m["ambientOcclusion"] = {"map": {"type": "procedural"}, "intensity": 0.5 if ao else 0.0}
            fixes += 1
        # Add surfaceFrequencyBands if missing
        if "surfaceFrequencyBands" not in m or not isinstance(m.get("surfaceFrequencyBands"), list):
            m["surfaceFrequencyBands"] = [
                {"id": "macro", "frequency": 1.0, "amplitude": 0.5},
                {"id": "meso", "frequency": 0.3, "amplitude": 0.3},
                {"id": "micro", "frequency": 0.1, "amplitude": 0.2},
            ]
            fixes += 1
        # Add textureProjection if missing
        m.setdefault("textureProjection", {"mode": "uv", "texelDensity": 256})
        # Remove referencePbr if present but empty (causes validation errors)
        if "referencePbr" in m and (not isinstance(m["referencePbr"], dict) or m["referencePbr"].get("usable") is not True):
            del m["referencePbr"]
            fixes += 1

    # 2. Fix component colorMaterialRecipe: string → object
    for c in spec.get("componentTree", []):
        cmr = c.get("colorMaterialRecipe")
        if isinstance(cmr, str):
            # Parse "meshStandardMaterial color=#HEX roughness=0.x metalness=0.x"
            match = re.search(r"color=#([0-9a-fA-F]{6})", cmr)
            hex_col = match.group(1) if match else "888888"
            r, g, b = int(hex_col[0:2], 16), int(hex_col[2:4], 16), int(hex_col[4:6], 16)
            c["colorMaterialRecipe"] = {
                "dominantAlbedo": f"rgba({r}, {g}, {b}, 1.0)",
                "secondaryAlbedo": f"rgba({r}, {g}, {b}, 1.0)",
                "materialClass": "skin",
                "materialClassConfidence": 0.9,
            }
            fixes += 1
        elif isinstance(cmr, dict):
            # Add secondaryAlbedo if missing
            if "secondaryAlbedo" not in cmr and "dominantAlbedo" in cmr:
                cmr["secondaryAlbedo"] = cmr["dominantAlbedo"]
                fixes += 1
        # Add localFeatures if missing
        c.setdefault("localFeatures", [])

    # 3. Fix detailInventory kinds
    valid_kinds = {"gloss", "bevel", "fastener", "linework", "contour", "seam",
                   "stitch", "stain", "scratch", "chip", "decal", "emissive",
                   "hole", "groove", "ridge"}
    detail_mapping = {
        "pupil": "hole", "iris": "groove", "limbal": "ridge", "crease": "seam",
        "tarsal": "ridge", "follicle": "groove", "vessel": "stain",
        "tear": "gloss", "pore": "stain", "reflect": "gloss", "fold": "seam",
        "skin": "stain", "lid": "seam",
    }
    di = spec.get("preSpecAssessment", {}).get("detailInventory", {})
    for d in di.get("details", []):
        kind = d.get("kind", "")
        if kind not in valid_kinds:
            # Try to guess kind from id/description
            for keyword, new_kind in detail_mapping.items():
                if keyword in d.get("id", "").lower() or keyword in d.get("description", "").lower():
                    d["kind"] = new_kind
                    fixes += 1
                    break

    # 4. Add lightingFromPhoto if missing
    if not spec.get("lightingFromPhoto"):
        spec["lightingFromPhoto"] = [
            {"role": "key", "direction": [1, -1, 2], "intensity": 1.0, "color": "#FFFFFF", "exposure": 1.0},
            {"role": "fill", "direction": [-1, 0, 1], "intensity": 0.5, "color": "#FFFFFF", "toneMapping": "ACESFilmicToneMapping"},
            {"role": "rim", "direction": [0, 1, -1], "intensity": 0.3, "color": "#FFFFFF"},
        ]
        fixes += 1

    # 5. Clear unknowns
    spec.get("preSpecAssessment", {}).pop("unknownsToResolveBeforeImplementation", None)

    return fixes


def prune_components(spec: dict, prefix: str) -> int:
    """Delete components whose id starts with prefix. Returns count removed."""
    original = list(spec.get("componentTree", []))
    kept = [c for c in original if not c["id"].startswith(prefix)]
    removed = len(original) - len(kept)
    spec["componentTree"] = kept

    # Also remove related materials
    # (find which materials were used by removed components)
    used_materials = set()
    for c in kept:
        mat = c.get("material", c.get("colorMaterialRecipe", {}).get("materialClass"))
        if mat:
            used_materials.add(mat)
    # Keep all materials that are still referenced
    orig_mats = list(spec.get("materials", []))
    spec["materials"] = [m for m in orig_mats if m["id"] in used_materials or len(orig_mats) <= 3]

    # Clean up detailInventory entries that reference removed components
    di = spec.get("preSpecAssessment", {}).get("detailInventory", {})
    di["details"] = [d for d in di.get("details", [])
                     if not any(d.get("mapsTo", {}).get("ref", "").startswith(prefix)
                                or d.get("id", "").startswith(prefix))]

    return removed


def keep_components(spec: dict, ids: list[str]) -> int:
    """Keep only components whose id is in the list. Returns count removed."""
    original = list(spec.get("componentTree", []))
    kept = [c for c in original if c["id"] in ids]
    removed = len(original) - len(kept)
    spec["componentTree"] = kept

    # Clean up details
    di = spec.get("preSpecAssessment", {}).get("detailInventory", {})
    di["details"] = [d for d in di.get("details", [])
                     if d.get("mapsTo", {}).get("ref") in ids
                     and d.get("mapsTo", {}).get("type") == "component"]

    return removed


def apply_scope(spec: dict, scope: str) -> int:
    """Apply a named scope preset."""
    if scope not in SCOPES:
        print(f"❌ Unknown scope: {scope}. Available: {', '.join(SCOPES.keys())}")
        sys.exit(1)
    sc = SCOPES[scope]
    patterns = [re.compile(p) for p in sc["keep_patterns"]]
    original = list(spec.get("componentTree", []))
    kept = [c for c in original if any(p.match(c["id"]) for p in patterns)]
    removed = len(original) - len(kept)
    spec["componentTree"] = kept

    # Update estimated counts
    estimates = spec.get("preSpecAssessment", {}).get("complexity", {}).get("estimatedCounts", {})
    levels = {"macro": 0, "meso": 0, "micro": 0}
    for c in kept:
        levels[c.get("level", "meso")] += 1
    if "macroComponents" in estimates:
        estimates["macroComponents"] = levels["macro"]
    if "mesoComponents" in estimates:
        estimates["mesoComponents"] = levels["meso"]
    if "microFeatureGroups" in estimates:
        estimates["microFeatureGroups"] = levels["micro"]

    print(f"🔍 Scope '{scope}': {sc['description']}")
    print(f"   Removed {removed} components, kept {len(kept)}: {[c['id'] for c in kept]}")
    return removed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix and prune ObjectSculptSpec JSON")
    parser.add_argument("spec", help="Path to sculpt spec JSON")
    parser.add_argument("--auto", action="store_true", help="Auto-fix common validation errors")
    parser.add_argument("--prune", type=str, help="Delete components with id prefix")
    parser.add_argument("--keep", type=str, help="Comma-separated list of component ids to keep")
    parser.add_argument("--scope", type=str, choices=list(SCOPES.keys()),
                        help=f"Scope preset: {', '.join(SCOPES.keys())}")

    args = parser.parse_args()
    spec = load_spec(args.spec)

    total = 0
    if args.auto:
        n = fix_auto(spec)
        total += n
        print(f"🔧 Auto-fix: {n} changes applied")

    if args.scope:
        n = apply_scope(spec, args.scope)
        total += n

    if args.prune:
        n = prune_components(spec, args.prune)
        total += n
        print(f"✂️  Pruned {n} components with prefix '{args.prune}'")

    if args.keep:
        ids = [x.strip() for x in args.keep.split(",")]
        n = keep_components(spec, ids)
        total += n
        print(f"✂️  Kept only {len(ids)} components, removed {n}")

    if total > 0:
        save_spec(spec, args.spec)
        print(f"\n💡 Validate again: python3 forge/stage2_spec/validate_sculpt_spec.py {args.spec} --strict-quality")
    else:
        print("No changes made. Use --auto, --prune, --keep, or --scope.")
