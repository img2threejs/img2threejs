#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Final

from forge.stage1_intake.check_reference_admission import check_admission
from forge.stage1_intake.cs2_foundation import enrich_manifest_with_metadata, normalize_cs2_metadata, resolve_identity
from forge.stage1_intake.cs2_review_contract import build_review_scene
from forge.stage1_intake.detect_cs2 import detect_cs2_signals
from forge.stage1_intake.probe_image import probe

SCHEMA_VERSION: Final[int] = 1
SUPPORTED_FAMILIES: Final[frozenset[str]] = frozenset({"knife"})
UNSUPPORTED_FAMILIES: Final[frozenset[str]] = frozenset(
    {"pistol", "rifle", "smg", "sniper", "heavy", "glove"}
)
# Knife subtypes that have a dedicated geometry adapter. A subtype absent here is
# `unsupported-subtype`, never silently routed through another subtype's tree.
KNIFE_SUBTYPES: Final[frozenset[str]] = frozenset(
    {"karambit", "butterfly", "bayonet", "m9", "flip", "gut", "falchion", "bowie", "navaja",
     "talon", "classic"}
)
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
    elif subtype and subtype not in KNIFE_SUBTYPES:
        manifest["state"] = "unsupported-subtype"
        manifest["unsupportedReason"] = f"no knife adapter fixture for {subtype}"
    else:
        manifest["state"] = "proceed"
        manifest["componentAdapter"] = "cs2-knife-v1"
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
    if manifest["state"] == "proceed" and manifest.get("itemFamily") != "knife":
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
