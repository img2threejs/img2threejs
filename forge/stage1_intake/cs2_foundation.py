#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_identity(
    explicit: dict[str, Any] | None,
    resolved: dict[str, Any] | None,
    classification: dict[str, Any] | None,
) -> dict[str, Any]:
    if explicit:
        return {"status": "resolved", "identity": dict(explicit), "provenance": "explicit-user-metadata"}
    if resolved and resolved.get("status") == "ambiguous":
        return {"status": "ambiguous", "candidates": list(resolved.get("candidates", []))}
    if resolved and resolved.get("status") == "no-match":
        return {"status": "no-match", "reason": resolved.get("reason", "metadata lookup found no match")}
    if resolved and resolved.get("status") in {None, "resolved"}:
        return {"status": "resolved", "identity": dict(resolved), "provenance": "resolved-metadata"}
    if classification and classification.get("itemFamily"):
        return {"status": "resolved", "identity": dict(classification), "provenance": "classification-record"}
    return {"status": "request-input", "reason": "authoritative identity evidence is unavailable"}


def normalize_cs2_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    float_value = metadata.get("float", metadata.get("floatValue"))
    paint_seed = metadata.get("paintSeed", metadata.get("paint_seed"))
    hidden_confidence = metadata.get("hiddenRegionConfidence")
    if not isinstance(hidden_confidence, (int, float)):
        hidden_confidence = 0.25
    return {
        "identity": {
            "name": metadata.get("name", metadata.get("itemName")),
            "paintIndex": metadata.get("paintIndex", metadata.get("paint_index")),
            "rarity": metadata.get("rarity"),
            "previewUrl": metadata.get("previewUrl", metadata.get("imageUrl")),
            "floatRange": metadata.get("floatRange", {"min": metadata.get("minFloat"), "max": metadata.get("maxFloat")}),
        },
        "float": float_value,
        "paintSeed": paint_seed,
        "finishStyle": metadata.get("finishStyle"),
        "approximations": {
            "float": {"value": float_value, "approximated": float_value is None, "confidence": 0.0 if float_value is None else 1.0},
            "paintSeed": {"value": paint_seed, "approximated": paint_seed is None, "confidence": 0.0 if paint_seed is None else 1.0},
            "hiddenRegions": {"confidence": float(hidden_confidence), "approximated": True},
            "finishStyle": {"value": metadata.get("finishStyle"), "approximated": metadata.get("finishStyle") is None},
        },
    }


def enrich_manifest_with_metadata(manifest: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
    result = dict(manifest)
    result["metadataResolution"] = dict(resolution)
    result.setdefault("warnings", [])
    warnings = list(result["warnings"])
    if resolution.get("status") in {"no-match", "ambiguous"}:
        if "metadata-unavailable" not in warnings:
            warnings.append("metadata-unavailable")
        result["exactnessTier"] = result.get("exactnessTier", "image-only")
    elif resolution.get("status") == "resolved":
        result["metadata"] = normalize_cs2_metadata(resolution.get("identity", resolution))
        if result.get("exactnessTier") == "image-only":
            result["exactnessTier"] = "metadata-assisted"
    result["warnings"] = warnings
    return result


def classify_map_path(path: Path) -> dict[str, Any]:
    name = path.name.lower()
    if any(token in name for token in ("_orm", "_mrao", "_packed")):
        return {"mapClass": "packed", "path": str(path), "colorSpace": "linear", "uvOrientation": "standard", "packedChannels": {"roughness": "r", "metalness": "g", "ao": "b"}}
    if any(token in name for token in ("_normal", "_nrm", "_bump")):
        return {"mapClass": "normal", "path": str(path), "colorSpace": "linear", "uvOrientation": "standard"}
    if any(token in name for token in ("_rough", "_roughness")):
        return {"mapClass": "roughness", "path": str(path), "colorSpace": "linear", "uvOrientation": "standard"}
    if any(token in name for token in ("_metal", "_metalness")):
        return {"mapClass": "metalness", "path": str(path), "colorSpace": "linear", "uvOrientation": "standard"}
    if any(token in name for token in ("_ao", "_occlusion")):
        return {"mapClass": "ao", "path": str(path), "colorSpace": "linear", "uvOrientation": "standard"}
    if any(token in name for token in ("_height", "_displacement")):
        return {"mapClass": "height", "path": str(path), "colorSpace": "linear", "uvOrientation": "standard"}
    if any(token in name for token in ("_mask", "_wear", "_pattern")):
        return {"mapClass": "mask", "path": str(path), "colorSpace": "linear", "uvOrientation": "standard"}
    return {"mapClass": "albedo", "path": str(path), "colorSpace": "srgb", "uvOrientation": "standard"}


def adapt_texture_result(extraction: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    if extraction.get("status") != "ok":
        return {"status": "fallback", "textureSource": "image-only", "reason": extraction.get("reason", "texture extraction unavailable")}
    records: list[dict[str, Any]] = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_file():
            record = classify_map_path(path)
            record.update({"source": "local-vpk", "ipBoundary": "local-user-install-not-redistributable"})
            records.append(record)
    return {"status": "ok", "textureSource": "local-vpk", "assets": records}


def map_assets_to_reference_pbr(records: list[dict[str, Any]]) -> dict[str, Any]:
    channels: dict[str, dict[str, Any]] = {}
    for record in records:
        map_class = record.get("mapClass")
        if map_class in {"albedo", "normal", "roughness", "metalness", "height", "ao", "mask"}:
            channels.setdefault(str(map_class), dict(record))
        elif map_class == "packed":
            for channel, packed_channel in record.get("packedChannels", {}).items():
                channels.setdefault(channel, {"path": record.get("path"), "source": record.get("source", "local-vpk"), "derived": True, "packedChannel": packed_channel, "colorSpace": "linear", "uvOrientation": record.get("uvOrientation", "standard")})
    missing = [channel for channel in ("albedo", "normal", "roughness", "metalness") if channel not in channels]
    if "ao" not in channels:
        channels["ao"] = {"derived": True, "from": "normal-or-geometry", "confidence": 0.25}
    return {"maps": channels, "missingRequired": missing, "complete": not missing}


def validate_route_requirements(manifest: dict[str, Any], *, allow_fallback: bool = False) -> dict[str, Any]:
    result = dict(manifest)
    route = result.get("route")
    tier = result.get("exactnessTier")
    if route == "reference-projection":
        camera = result.get("camera", {})
        projection = result.get("projection", {})
        has_camera = camera.get("status") == "solved" or bool(camera.get("referenceCamera"))
        has_delit = bool(result.get("deLitAlbedo") or projection.get("deLitSource"))
        if not (has_camera and has_delit):
            if allow_fallback:
                result["route"] = "procedural-finish"
                result["state"] = "fallback"
                result.setdefault("warnings", []).append("projection-evidence-unavailable; procedural fallback selected")
            else:
                result["state"] = "request-input"
            return result
    if route == "authored-texture":
        assets = result.get("assets", {}).get("records", []) if isinstance(result.get("assets"), dict) else []
        channels = {item.get("mapClass") for item in assets if isinstance(item, dict)}
        if not {"albedo", "normal"}.issubset(channels) or not ({"roughness", "packed"} & channels):
            result["state"] = "request-input" if tier == "exact-texture" else "fallback"
    if tier == "exact-texture" and route != "authored-texture":
        result["state"] = "request-input"
    return result
