#!/usr/bin/env python3
"""CS2 intake manifest: admission, classification-gated family/subtype routing, and identity
resolution for a CS2 weapon/glove skin reference.

Canonical taxonomy source: the community-maintained `ByMykel/CSGO-API` skins.json
(https://github.com/ByMykel/CSGO-API), reachable via forge/stage1_intake/fetch_cs2_metadata.py.
That script resolves per-skin identity (paint index, float range, rarity) from the same index
and is cheap and safe to run by default whenever a skin name is given or suspected, even with no
local CS2 install -- see grimoire/intake/cs2_texture_acquisition.md step 1. It is a different,
much smaller ask than that doc's VPK/texture-extraction steps 2-3, which stay an optional Tier-3
exactness upgrade requiring the user's own local game install.

Family/subtype support here must stay in sync with forge/stage2_spec/cs2_adapters.py (the
FamilyAdapter registered per family) -- a family/subtype supported here with no adapter there
raises at spec-authoring time instead of at intake, which is a worse failure mode.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Final

# Insert the skill root so the absolute `forge.X.Y` imports below resolve under direct script
# execution (`python3 forge/stage1_intake/cs2_manifest.py ...` from any cwd), not just when
# PYTHONPATH already includes it or the module is imported as a package (e.g. by pytest).
# Pre-existing gap: this file was the only forge.stage1_intake module doing cross-package
# `from forge.X` imports without it, so it silently only ever worked via `-m` invocation or
# under pytest's own import machinery -- never as a plain script, until a test exercised that.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from forge.stage1_intake.check_reference_admission import check_admission  # noqa: E402
from forge.stage1_intake.cs2_foundation import enrich_manifest_with_metadata, normalize_cs2_metadata, resolve_identity  # noqa: E402
from forge.stage1_intake.cs2_review_contract import build_review_scene  # noqa: E402
from forge.stage1_intake.detect_cs2 import detect_cs2_signals  # noqa: E402
from forge.stage1_intake.probe_image import probe

SCHEMA_VERSION: Final[int] = 1
SUPPORTED_FAMILIES: Final[frozenset[str]] = frozenset(
    {"knife", "pistol", "sniper", "rifle", "smg", "heavy", "glove"}
)
# Physical CS2 items with their own skins but no adapter yet (Zeus x27, C4, defuse kit, Kevlar).
# Not a weapon/knife/glove topology at all -- distinct from an unrecognized/junk classification.
UNSUPPORTED_FAMILIES: Final[frozenset[str]] = frozenset({"equipment"})
# Subtypes with a dedicated geometry adapter, per family (see forge/stage2_spec/cs2_adapters.py
# -- keep these two files' subtype sets identical; a mismatch lets intake proceed into a spec
# author call that then raises for a family/subtype cs2_adapters.py doesn't recognize).
# A subtype absent from its family's set is `unsupported-subtype`, never silently routed
# through another subtype's tree.
KNIFE_SUBTYPES: Final[frozenset[str]] = frozenset({
    "karambit", "butterfly", "bayonet", "m9", "flip", "gut", "falchion", "bowie", "navaja",
    "talon", "classic",
    "huntsman", "kukri", "nomad", "paracord", "shadow-daggers", "skeleton", "stiletto",
    "survival", "ursus",
})
PISTOL_SUBTYPES: Final[frozenset[str]] = frozenset({
    "glock-18", "usp-s", "p2000", "dual-berettas", "p250", "cz75-auto", "five-seven", "tec-9",
    "desert-eagle", "r8-revolver",
})
# CS2's "Sniper Rifles" Market category -- bolt-action AWP plus the semi-auto SSG08/G3SG1/
# SCAR-20. AWP is the only one built against a real reference so far (see
# src/demos/awp-medusa/createAwpMedusaModel.ts in the img2threejs-showcase project); the other
# three are gate-registered only, per this file's module docstring.
SNIPER_SUBTYPES: Final[frozenset[str]] = frozenset({"awp", "ssg08", "g3sg1", "scar-20"})
# CS2's "Rifles" Market category (semi/full-auto, no detachable scope) -- distinct from Sniper
# Rifles above. None built against a real reference yet.
RIFLE_SUBTYPES: Final[frozenset[str]] = frozenset(
    {"ak-47", "m4a4", "m4a1-s", "famas", "galil-ar", "sg-553", "aug"}
)
SMG_SUBTYPES: Final[frozenset[str]] = frozenset(
    {"mac-10", "mp9", "mp7", "mp5-sd", "ump-45", "p90", "pp-bizon"}
)
# CS2's "Heavy" Market category covers both shotguns and machine guns -- genuinely different
# shapes; see the _HEAVY comment in cs2_adapters.py.
HEAVY_SUBTYPES: Final[frozenset[str]] = frozenset(
    {"nova", "xm1014", "sawed-off", "mag-7", "m249", "negev"}
)
GLOVE_SUBTYPES: Final[frozenset[str]] = frozenset(
    {"bloodhound", "broken-fang", "driver", "hand-wraps", "hydra", "moto", "specialist", "sport"}
)
FAMILY_SUBTYPES: Final[dict[str, frozenset[str]]] = {
    "knife": KNIFE_SUBTYPES,
    "pistol": PISTOL_SUBTYPES,
    "sniper": SNIPER_SUBTYPES,
    "rifle": RIFLE_SUBTYPES,
    "smg": SMG_SUBTYPES,
    "heavy": HEAVY_SUBTYPES,
    "glove": GLOVE_SUBTYPES,
}
ROUTES: Final[frozenset[str]] = frozenset(
    {"reference-projection", "authored-texture", "procedural-finish"}
)
TIERS: Final[frozenset[str]] = frozenset(
    {"image-only", "metadata-assisted", "exact-texture"}
)
STATES: Final[frozenset[str]] = frozenset(
    {"proceed", "request-input", "fallback", "rejected", "unsupported-family", "unsupported-subtype"}
)


def build_classification_record(
    item_family: str,
    subtype: str | None,
    confidence: float,
    evidence_refs: list[str],
    *,
    provider: str = "offline-fixture",
    version: str = "1",
    timeout: bool = False,
) -> dict[str, Any]:
    if item_family not in SUPPORTED_FAMILIES | UNSUPPORTED_FAMILIES:
        raise ValueError(f"unsupported item family label: {item_family}")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("classification confidence must be between 0 and 1")
    return {
        "itemFamily": item_family,
        "subtype": subtype,
        "confidence": round(confidence, 4),
        "evidenceRefs": list(evidence_refs),
        "provider": provider,
        "version": version,
        "timedOut": timeout,
    }


def _classification_error(record: Any) -> str | None:
    if not isinstance(record, dict):
        return "authoritative classification record is required"
    family = record.get("itemFamily")
    confidence = record.get("confidence")
    refs = record.get("evidenceRefs")
    if not isinstance(family, str) or family not in SUPPORTED_FAMILIES | UNSUPPORTED_FAMILIES:
        return "classification itemFamily is missing or invalid"
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        return "classification confidence is missing or invalid"
    if not isinstance(refs, list) or not refs or not all(isinstance(item, str) and item for item in refs):
        return "classification evidenceRefs must contain at least one reference"
    if not isinstance(record.get("provider"), str) or not isinstance(record.get("version"), str):
        return "classification provider/version are required"
    return None


def _heuristic_signal(reference: Path) -> dict[str, Any]:
    try:
        return detect_cs2_signals(reference)
    except (OSError, ValueError) as exc:
        return {"is_cs2_candidate": False, "confidence": 0.0, "signals": [], "error": str(exc)}


def build_manifest(
    reference: Path,
    classification: dict[str, Any] | None,
    *,
    route: str = "reference-projection",
    exactness_tier: str = "image-only",
    metadata: dict[str, Any] | None = None,
    texture_source: str = "image-only",
    explicit_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = reference.expanduser().resolve()
    technical: dict[str, Any] = probe(resolved) if resolved.exists() else {"path": str(resolved), "warnings": ["file does not exist"]}
    admission: dict[str, Any] = check_admission(resolved) if resolved.exists() else {"admitted": False, "reasons": ["reference does not exist"]}
    heuristic = _heuristic_signal(resolved) if resolved.exists() else {"is_cs2_candidate": False, "confidence": 0.0, "signals": []}
    warnings: list[str] = []
    if heuristic.get("is_cs2_candidate"):
        warnings.append("heuristicSignal")
    if technical.get("warnings"):
        warnings.extend(str(item) for item in technical["warnings"])
    if route not in ROUTES:
        raise ValueError(f"unknown route {route!r}")
    if exactness_tier not in TIERS:
        raise ValueError(f"unknown exactness tier {exactness_tier!r}")
    manifest: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "state": "rejected" if not admission.get("admitted") else "request-input",
        "sourceViews": [{
            "role": "reference",
            "path": str(resolved),
            "hash": admission.get("provenance", {}).get("pHash"),
            "width": technical.get("width"),
            "height": technical.get("height"),
            "coverage": admission.get("provenance", {}).get("foregroundCoverage"),
            "duplicate": admission.get("provenance", {}).get("duplicateOfHash") is not None,
        }],
        "admission": admission,
        "probe": technical,
        "heuristicSignal": heuristic,
        "exactnessTier": exactness_tier,
        "route": route,
        "textureSource": texture_source,
        "identity": {"provenance": "unknown", "confidence": 0.0},
        "finish": {"provenance": "visual-observation", "confidence": 0.0},
        "assets": {"source": texture_source, "records": []},
        "camera": {"status": "unknown", "provenance": "not-supplied"},
        "provenance": {"reference": "user-supplied", "metadata": "not-supplied"},
        "assumptions": {"float": "unknown", "paintSeed": "unknown", "hiddenRegions": "inferred"},
        "confidence": {"overall": 0.0, "hiddenRegions": 0.25},
        "warnings": warnings,
        "extensions": {},
        "reviewScene": build_review_scene("not-supplied"),
    }
    if not admission.get("admitted"):
        manifest["rejectionReasons"] = admission.get("reasons", ["reference failed admission"])
        return manifest
    error = _classification_error(classification)
    if error:
        manifest["warnings"].append(error)
        return manifest
    assert isinstance(classification, dict)
    family = classification["itemFamily"]
    subtype = classification.get("subtype")
    manifest["classification"] = classification
    manifest["itemFamily"] = family
    manifest["subtype"] = subtype
    manifest["identity"] = {"provenance": "classification-record", "confidence": classification["confidence"]}
    manifest["confidence"] = {"overall": classification["confidence"], "hiddenRegions": 0.25}
    manifest["identity"] = resolve_identity(explicit_identity, metadata, classification)
    if family not in SUPPORTED_FAMILIES:
        manifest["state"] = "unsupported-family"
        manifest["unsupportedReason"] = f"no adapter registered for {family}"
    elif subtype and subtype not in FAMILY_SUBTYPES[family]:
        manifest["state"] = "unsupported-subtype"
        manifest["unsupportedReason"] = f"no {family} adapter fixture for {subtype}"
    else:
        manifest["state"] = "proceed"
        manifest["componentAdapter"] = f"cs2-{family}-v1"
    if metadata:
        manifest = enrich_manifest_with_metadata(manifest, {"status": "resolved", "identity": metadata})
        manifest["metadata"] = normalize_cs2_metadata(metadata)
        manifest["provenance"]["metadata"] = metadata.get("source", "provided")
    return manifest


def validate_manifest(manifest: dict[str, Any]) -> bool:
    required = {"schemaVersion", "state", "sourceViews", "admission", "exactnessTier", "route", "warnings"}
    if not required.issubset(manifest):
        return False
    if manifest["schemaVersion"] != SCHEMA_VERSION or manifest["state"] not in STATES:
        return False
    if manifest["route"] not in ROUTES or manifest["exactnessTier"] not in TIERS:
        return False
    if not isinstance(manifest["sourceViews"], list) or not isinstance(manifest["warnings"], list):
        return False
    if manifest["state"] == "proceed" and manifest.get("itemFamily") not in SUPPORTED_FAMILIES:
        return False
    return True


def persist_manifest(manifest: dict[str, Any], output: Path) -> None:
    if not validate_manifest(manifest):
        raise ValueError("refusing to persist invalid cs2-intake manifest")
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, output)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--classification", type=Path, help="offline authoritative classification JSON")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--route", choices=sorted(ROUTES), default="reference-projection")
    parser.add_argument("--exactness-tier", choices=sorted(TIERS), default="image-only")
    parser.add_argument("--cs2-pipeline", choices=("legacy", "manifest-v1"), default="manifest-v1")
    parser.add_argument("--resume", action="store_true", help="reuse a valid existing manifest at --out")
    args = parser.parse_args(argv)
    if args.resume and args.out.exists():
        existing = json.loads(args.out.read_text(encoding="utf-8"))
        if isinstance(existing, dict) and validate_manifest(existing):
            print(json.dumps({"state": existing["state"], "out": str(args.out.resolve()), "resumed": True}, ensure_ascii=False))
            return 0
    classification = json.loads(args.classification.read_text(encoding="utf-8")) if args.classification else None
    manifest = build_manifest(args.reference, classification, route=args.route, exactness_tier=args.exactness_tier)
    manifest["extensions"]["compatibilityMode"] = args.cs2_pipeline
    persist_manifest(manifest, args.out)
    print(json.dumps({"state": manifest["state"], "out": str(args.out.resolve())}, ensure_ascii=False))
    return 0 if manifest["state"] in {"proceed", "request-input", "fallback"} else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
