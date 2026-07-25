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
    preSpecAssessment: dict[str, JsonValue]
    qualityContract: dict[str, JsonValue]
    authoringInstruction: str


class PreSpecPayload(PreSpecPayloadRequired, total=False):
    localSpecSearch: LocalSpecSearchPayload


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
    image: str | None,
    complexity: str,
    is_cs2: bool = False,
    manifest: dict | None = None,
) -> PreSpecPayload:
    assessment = make_pre_spec_assessment(target_name)
    contract = make_quality_contract()
    is_cs2 = is_cs2 or detect_cs2_intent(target_name)
    if is_cs2:
        assessment["objectClass"]["cs2"] = True
    assessment["sourceImage"] = image or ""
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
        "sourceImage": image or "",
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
        payload["preSpecAssessment"]["cs2Intake"] = intake
        payload["preSpecAssessment"]["objectClass"]["itemFamily"] = manifest.get("itemFamily")
        payload["preSpecAssessment"]["objectClass"]["subtype"] = manifest.get("subtype")
        payload["preSpecAssessment"]["objectClass"]["route"] = manifest.get("route")
        payload["preSpecAssessment"]["objectClass"]["exactnessTier"] = manifest.get("exactnessTier")
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_name", help="Human-readable object name")
    parser.add_argument("--image", help="Reference image path or URL")
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
    payload_object = make_payload(args.target_name, args.image, complexity, is_cs2, manifest)
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
