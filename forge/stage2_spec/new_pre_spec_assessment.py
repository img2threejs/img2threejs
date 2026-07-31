#!/usr/bin/env python3
"""Create a pre-spec assessment and quality contract skeleton before ObjectSculptSpec authoring."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Final, TypedDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from forge._shared.spec_search import (  # noqa: E402
    CacheReadError,
    CacheValidationError,
    CacheWriteError,
    IndexBuildError,
    IndexRequest,
    ProfileCachePathError,
    ProfileValidationError,
    JsonValue,
    SearchOutputRequest,
    SerializedSearchMatch,
    SourceIngestionError,
    SpecRecordValidationError,
    UnknownCollectionError,
    load_or_build_index,
    load_profile,
    serialize_search_results,
)
from forge.stage2_spec.new_sculpt_spec import (  # noqa: E402
    make_pre_spec_assessment,
    make_quality_contract,
)



COMPLEXITY_MINIMUMS = {
    "simple": {
        "macroComponents": 1,
        "mesoComponents": 0,
        "microFeatureGroups": 0,
        "materialLayers": 1,
        "repetitionSystems": 0,
        "reviewViewpoints": 2,
    },
    "moderate": {
        "macroComponents": 2,
        "mesoComponents": 3,
        "microFeatureGroups": 2,
        "materialLayers": 2,
        "repetitionSystems": 0,
        "reviewViewpoints": 3,
    },
    "complex": {
        "macroComponents": 3,
        "mesoComponents": 8,
        "microFeatureGroups": 5,
        "materialLayers": 3,
        "repetitionSystems": 1,
        "reviewViewpoints": 4,
    },
    "ultra-complex": {
        "macroComponents": 5,
        "mesoComponents": 16,
        "microFeatureGroups": 8,
        "materialLayers": 4,
        "repetitionSystems": 2,
        "reviewViewpoints": 5,
    },
}


DETAIL_MINIMUMS = {
    "simple": 3,
    "moderate": 6,
    "complex": 10,
    "ultra-complex": 16,
}

# CS2 weapon/knife/glove skins always carry more identity-defining detail (finish pattern,
# wear layer, hardware, stitching/fasteners) than a generic object at the same structural
# complexity tier -- so --cs2 defaults to the ultra-complex tier (targetMinDetails 16), and the
# detail-count floor never drops below this even if --complexity is explicitly set lower.
CS2_DETAIL_MINIMUM = 9

# Lightweight keyword heuristic, not exhaustive: catches the common phrasing ("CS2 skin",
# "AK-47 | Redline", "Karambit Doppler") so --cs2 doesn't have to be typed by hand for the
# obvious case. A miss here just means the agent (or the user) sets --cs2 explicitly --
# vision/prompt-based detection beyond this heuristic is inherently the agent's judgment call.
CS2_INTENT_KEYWORDS = (
    "cs2", "csgo", "counter-strike", "counter strike", "weapon skin", "knife skin", "glove skin",
    "doppler", "gamma doppler", "marble fade", "case hardened", "fade",
    "karambit", "butterfly knife", "bayonet", "gut knife", "falchion", "bowie knife",
    "classic knife",
)

DEFAULT_SPEC_SEARCH_LIMIT: Final = 3
DEFAULT_SPEC_SEARCH_SNIPPET_CHARS: Final = 250


class LocalSpecSearchIndex(TypedDict):
    status: str
    reason: str
    fingerprint: str


class LocalSpecSearchPayload(TypedDict):
    collection: str
    query: str
    index: LocalSpecSearchIndex
    matches: list[SerializedSearchMatch]


class PreSpecPayloadRequired(TypedDict):
    targetName: str
    sourceImage: str
    sourceImages: list[str]
    multiViewSynthesis: dict[str, JsonValue]
    preSpecAssessment: dict[str, JsonValue]
    qualityContract: dict[str, JsonValue]
    authoringInstruction: str


class PreSpecPayload(PreSpecPayloadRequired, total=False):
    localSpecSearch: LocalSpecSearchPayload
    cs2Intake: dict[str, JsonValue]


def normalize_source_images(image: str | list[str] | None) -> list[str]:
    if isinstance(image, str):
        return [image] if image else []
    if image is None:
        return []
    return [value for value in image if value]


def synthesize_source_images(
    target_name: str,
    source_images: list[str],
    multi_view_analysis: dict[str, JsonValue] | None = None,
    calibration: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    """Treat each image as an independent evidence source, NOT a fused multi-view set.
    
    Each image provides:
    - Front image → front-facing geometry + front texture
    - Back image → back-facing geometry + back texture
    - Depth (Z) comes from procedural parameters, NOT from image analysis
    
    Do NOT attempt feature matching or pose estimation between views.
    """
    readable_images = [Path(value) for value in source_images if Path(value).is_file()]
    if len(source_images) > 1 and len(readable_images) != len(source_images):
        return {
            "status": "request-input",
            "reason": "all image paths must be readable local files",
            "viewCount": len(source_images),
            "synthesisMode": "independent-evidence",
            "confidence": 0.0,
        }
    if not readable_images:
        return {
            "status": "skipped",
            "reason": "no-local-image",
            "viewCount": len(source_images),
            "synthesisMode": "single-view" if len(source_images) == 1 else "unavailable",
            "confidence": 0.0,
        }
    
    # Each image is an independent evidence source — do NOT fuse them
    named_views = {}
    for i, img_path in enumerate(readable_images):
        name = f"view-{i}"
        # Try to detect view name from filename
        lower_name = img_path.stem.lower()
        if "front" in lower_name:
            name = "front"
        elif "back" in lower_name:
            name = "back"
        elif "top" in lower_name:
            name = "top"
        elif "bottom" in lower_name:
            name = "bottom"
        elif "left" in lower_name:
            name = "left"
        elif "right" in lower_name:
            name = "right"
        named_views[name] = str(img_path)
    
    result = {
        "status": "proceed",
        "viewCount": len(readable_images),
        "synthesisMode": "independent-evidence",
        "confidence": 1.0,  # Each image is independently valid
        "namedViews": named_views,
        "notes": "Each image is an independent evidence source. Front image → front faces, back image → back faces. Depth (Z) comes from procedural parameters, NOT from image analysis. Do NOT fuse images into a single multi-view representation.",
    }
    
    # Integrate multi-view analysis if provided (e.g., from agent calibration)
    if multi_view_analysis is not None:
        result["brief"] = multi_view_analysis
        # If calibrated data has components, use it for status
        if multi_view_analysis.get("calibrated") and multi_view_analysis.get("components"):
            result["status"] = "evidence-only"
    
    return result


def detect_cs2_intent(target_name: str) -> bool:
    lowered = target_name.lower()
    return " | " in target_name or any(keyword in lowered for keyword in CS2_INTENT_KEYWORDS)


def select_spec_collection(target_name: str, requested_collection: str | None) -> str:
    """Choose the local specification collection for a pipeline target."""
    if requested_collection:
        return requested_collection
    return "cs2" if detect_cs2_intent(target_name) else "core_3d"


def search_local_specs(
    target_name: str,
    collection: str,
    extra_terms: list[str],
    force_reindex: bool,
) -> LocalSpecSearchPayload:
    """Search local specs and return a portable evidence bundle for the assessment."""
    query = " ".join([target_name, *extra_terms]).strip()
    profile = load_profile(collection)
    loaded = load_or_build_index(
        IndexRequest(PROJECT_ROOT, collection, profile, force_reindex)
    )
    matches = serialize_search_results(
        loaded.index,
        SearchOutputRequest(query, DEFAULT_SPEC_SEARCH_LIMIT, DEFAULT_SPEC_SEARCH_SNIPPET_CHARS),
    )
    return {
        "collection": collection,
        "query": query,
        "index": {
            "status": loaded.status,
            "reason": loaded.reason,
            "fingerprint": loaded.fingerprint,
        },
        "matches": matches,
    }


def make_payload(
    target_name: str,
    image: str | list[str] | None,
    complexity: str,
    is_cs2: bool = False,
    manifest: dict[str, JsonValue] | None = None,
    multi_view_analysis: dict[str, JsonValue] | None = None,
    calibration: dict[str, JsonValue] | None = None,
) -> PreSpecPayload:
    source_images = normalize_source_images(image)
    source_image = source_images[0] if source_images else ""
    multi_view_synthesis = synthesize_source_images(
        target_name,
        source_images,
        multi_view_analysis,
        calibration,
    )
    assessment = make_pre_spec_assessment(target_name)
    contract = make_quality_contract()
    is_cs2 = is_cs2 or detect_cs2_intent(target_name)
    if is_cs2:
        assessment["objectClass"]["cs2"] = True
    assessment["sourceImage"] = source_image
    assessment["sourceImages"] = source_images
    assessment["multiViewSynthesis"] = multi_view_synthesis
    assessment["complexity"]["tier"] = complexity
    assessment["specDepthDecision"]["requiredDepth"] = complexity
    target_min_details = DETAIL_MINIMUMS[complexity]
    if is_cs2:
        target_min_details = max(target_min_details, CS2_DETAIL_MINIMUM)
    assessment["detailInventory"]["targetMinDetails"] = target_min_details
    if complexity in {"complex", "ultra-complex"}:
        assessment["specDepthDecision"]["needsRepetitionSystems"] = True
        assessment["specDepthDecision"]["needsMaterialLocalOverrides"] = True
        assessment["specDepthDecision"]["minimumComponentLevels"] = ["macro", "meso", "micro"]
    elif complexity == "moderate":
        assessment["specDepthDecision"]["minimumComponentLevels"] = ["macro", "meso"]
    contract["qualityBar"] = complexity
    contract["minimumSpecDepth"] = COMPLEXITY_MINIMUMS[complexity]
    payload: PreSpecPayload = {
        "targetName": target_name,
        "sourceImage": source_image,
        "sourceImages": source_images,
        "multiViewSynthesis": multi_view_synthesis,
        "preSpecAssessment": assessment,
        "qualityContract": contract,
        "authoringInstruction": (
            "Fill observed object class, complexity reasoning, featureGroups, visualDeltaChecks, "
            "and unknowns before generating or implementing ObjectSculptSpec."
        ),
    }
    if manifest is not None:
        intake = {
            "schemaVersion": manifest.get("schemaVersion"),
            "itemFamily": manifest.get("itemFamily"),
            "subtype": manifest.get("subtype"),
            "route": manifest.get("route"),
            "exactnessTier": manifest.get("exactnessTier"),
            "confidence": manifest.get("confidence", {}),
            "provenance": manifest.get("provenance", {}),
            "warnings": manifest.get("warnings", []),
        }
        payload["cs2Intake"] = intake
        assessment["cs2Intake"] = intake
        assessment["objectClass"]["itemFamily"] = manifest.get("itemFamily")
        assessment["objectClass"]["subtype"] = manifest.get("subtype")
        assessment["objectClass"]["route"] = manifest.get("route")
        assessment["objectClass"]["exactnessTier"] = manifest.get("exactnessTier")
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_name", help="Human-readable object name")
    parser.add_argument(
        "--image",
        action="append",
        default=[],
        help="Reference image path or URL; repeat for each distinct view.",
    )
    parser.add_argument(
        "--complexity",
        choices=sorted(COMPLEXITY_MINIMUMS),
        default=None,
        help="Initial complexity estimate. Refine after visual inspection. "
             "Default: ultra-complex for CS2 skins, moderate otherwise.",
    )
    parser.add_argument("--out", type=Path, help="Output JSON path")
    parser.add_argument("--force", action="store_true", help="Overwrite output file")
    parser.add_argument(
        "--cs2",
        action="store_true",
        help=f"CS2 weapon/knife/glove skin -- defaults complexity to ultra-complex (targetMinDetails 16); "
             f"never below the {CS2_DETAIL_MINIMUM} floor even if --complexity is set lower.",
    )
    parser.add_argument("--manifest", type=Path, help="CS2 intake manifest")
    parser.add_argument(
        "--multi-view-analysis",
        type=Path,
        help="Calibrated agent analysis JSON to merge with deterministic multi-view evidence.",
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        help="Camera calibration JSON for metric multi-view depth estimation.",
    )
    parser.add_argument(
        "--collection",
        help="Spec-search collection; defaults to cs2 for CS2 targets and core_3d otherwise.",
    )
    parser.add_argument(
        "--spec-query",
        action="append",
        default=[],
        help="Additional observed terms to add to the automatic local-spec query; repeatable.",
    )
    parser.add_argument(
        "--reindex-specs",
        action="store_true",
        help="Force rebuilding the local BM25 index before creating the assessment.",
    )
    args = parser.parse_args(argv)

    is_cs2 = args.cs2 or detect_cs2_intent(args.target_name)
    complexity = args.complexity or ("ultra-complex" if is_cs2 else "moderate")
    manifest = None
    if args.manifest:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            parser.error("CS2 intake manifest must be a JSON object")
        if manifest.get("state") not in {"proceed", "fallback"}:
            parser.error(f"CS2 intake is not ready for assessment: {manifest.get('state', 'unknown')}")
        is_cs2 = True
    multi_view_analysis = None
    if args.multi_view_analysis:
        multi_view_analysis = json.loads(args.multi_view_analysis.read_text(encoding="utf-8"))
        if not isinstance(multi_view_analysis, dict):
            parser.error("multi-view analysis must be a JSON object")
    calibration = None
    if args.calibration:
        calibration = json.loads(args.calibration.read_text(encoding="utf-8"))
        if not isinstance(calibration, dict):
            parser.error("calibration must be a JSON object")
    payload_object = make_payload(
        args.target_name,
        args.image,
        complexity,
        is_cs2,
        manifest,
        multi_view_analysis,
        calibration,
    )
    collection = select_spec_collection(args.target_name, args.collection)
    try:
        payload_object["localSpecSearch"] = search_local_specs(
            args.target_name,
            collection,
            args.spec_query,
            args.reindex_specs,
        )
    except (
        CacheReadError,
        CacheValidationError,
        CacheWriteError,
        IndexBuildError,
        ProfileCachePathError,
        ProfileValidationError,
        SourceIngestionError,
        SpecRecordValidationError,
        UnknownCollectionError,
    ) as error:
        parser.error(f"local spec search failed: {error}")
    payload = json.dumps(payload_object, indent=2, ensure_ascii=False) + "\n"
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
