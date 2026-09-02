"""Offline-only CLI for dense mesh evidence extraction."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import EXTRACTOR_VERSION
from .alignment import ALIGNMENT_PROFILE_VERSION, validate_alignment
from .cache import ExtractionCacheInput, base_extraction_cache_key
from .extract import ExtractionConfig, extract_run
from .model import (
    DenseEvidenceError,
    canonical_sha256,
    load_json_object,
    validate_provider_run,
    write_json_atomic,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract bounded geometric evidence from an existing reviewed mesh run"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("extract", "propose-scope", "verify-cache"):
        command = commands.add_parser(name)
        command.add_argument("--run", type=Path, required=True)
        command.add_argument("--source-image", type=Path, action="append", required=True)
        command.add_argument("--alignment", type=Path, required=True)
        if name != "propose-scope":
            command.add_argument("--out-dir", type=Path, required=True)
            command.add_argument("--resolution", type=int, default=24)
            command.add_argument("--sections", type=int, default=16)
            command.add_argument("--max-section-points", type=int, default=64)
    return parser


def _configuration(args: argparse.Namespace) -> ExtractionConfig:
    return ExtractionConfig(args.resolution, args.sections, args.max_section_points)


def _cache_identity(
    run: Path,
    source_images: tuple[Path, ...],
    alignment_payload: dict[str, Any],
    config: ExtractionConfig,
) -> tuple[str, Any, Any]:
    config.validate()
    provider_run = validate_provider_run(run, source_images)
    alignment = validate_alignment(alignment_payload, provider_run.semantic_status)
    measurement_hash = canonical_sha256(
        {
            "resolution": config.resolution,
            "sections": config.sections,
            "maxSectionPoints": config.max_section_points,
            "alignmentSha256": canonical_sha256(alignment_payload),
        }
    )
    cache_input = ExtractionCacheInput(
        provider_run.glb_sha256,
        provider_run.obj_sha256,
        provider_run.source_image_sha256,
        EXTRACTOR_VERSION,
        ALIGNMENT_PROFILE_VERSION,
        None,
        measurement_hash,
    )
    return base_extraction_cache_key(cache_input), provider_run, alignment


def _verify_cache(
    run: Path,
    sources: tuple[Path, ...],
    alignment_payload: dict[str, Any],
    out_dir: Path,
    config: ExtractionConfig,
) -> dict[str, Any]:
    expected_key, provider_run, _ = _cache_identity(
        run, sources, alignment_payload, config
    )
    evidence_path = out_dir.expanduser().resolve() / "dense-evidence.v1.json"
    if not evidence_path.is_file():
        return {"cacheHit": False, "reason": "evidence_missing", "expectedKey": expected_key}
    evidence = load_json_object(evidence_path, "cache_invalid")
    provenance = evidence.get("provenance")
    cache = evidence.get("cache")
    hit = (
        isinstance(provenance, dict)
        and isinstance(cache, dict)
        and cache.get("baseExtractionKey") == expected_key
        and provenance.get("glbSha256") == provider_run.glb_sha256
        and provenance.get("objSha256") == provider_run.obj_sha256
        and provenance.get("sourceImageSha256") == list(provider_run.source_image_sha256)
        and provenance.get("alignmentSha256") == canonical_sha256(alignment_payload)
    )
    return {
        "cacheHit": hit,
        "reason": "complete" if hit else "authoritative_hash_mismatch",
        "expectedKey": expected_key,
        "evidence": str(evidence_path),
    }


def _write_status(out_dir: Path, **values: object) -> None:
    write_json_atomic(
        out_dir.expanduser().resolve() / "status.json",
        {"schemaVersion": 1, "updatedAt": datetime.now(UTC).isoformat(), **values},
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sources = tuple(args.source_image)
    out_dir = getattr(args, "out_dir", None)
    try:
        alignment_payload = load_json_object(args.alignment, "malformed_input")
        if args.command == "propose-scope":
            provider_run = validate_provider_run(args.run, sources)
            alignment = validate_alignment(alignment_payload, provider_run.semantic_status)
            print(
                json.dumps(
                    {
                        "decision": "ALLOW",
                        "maximumInfluenceScope": alignment.maximum_scope.value,
                        "approvedInfluenceScope": "none",
                        "semanticStatus": provider_run.semantic_status,
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        config = _configuration(args)
        cache_report = _verify_cache(
            args.run, sources, alignment_payload, out_dir, config
        )
        if args.command == "verify-cache":
            print(json.dumps(cache_report, ensure_ascii=False))
            return 0 if cache_report["cacheHit"] else 1
        if cache_report["cacheHit"]:
            _write_status(
                out_dir,
                status="complete",
                cacheHit=True,
                lastDurableArtifact="dense-evidence.v1.json",
                resumable=True,
            )
            print(json.dumps(cache_report, ensure_ascii=False))
            return 0
        request = {
            "schemaVersion": 1,
            "run": str(args.run.expanduser().resolve()),
            "sourceImages": [str(item.expanduser().resolve()) for item in sources],
            "alignmentSha256": canonical_sha256(alignment_payload),
            "measurementConfig": {
                "resolution": config.resolution,
                "sections": config.sections,
                "maxSectionPoints": config.max_section_points,
            },
        }
        write_json_atomic(out_dir / "extraction-request.json", request)
        write_json_atomic(out_dir / "alignment.json", alignment_payload)
        evidence = extract_run(args.run, sources, alignment_payload, out_dir, config)
        # Use the CLI identity, which binds the exact alignment as well as numeric caps.
        expected_key, _, _ = _cache_identity(args.run, sources, alignment_payload, config)
        evidence["cache"]["baseExtractionKey"] = expected_key
        evidence["cache"]["measurementConfigSha256"] = canonical_sha256(
            {**request["measurementConfig"], "alignmentSha256": request["alignmentSha256"]}
        )
        write_json_atomic(out_dir / "dense-evidence.v1.json", evidence)
        _write_status(
            out_dir,
            status="complete",
            cacheHit=False,
            lastDurableArtifact="dense-evidence.v1.json",
            resumable=True,
        )
        print(
            json.dumps(
                {
                    "status": "complete",
                    "cacheHit": False,
                    "evidence": str((out_dir / "dense-evidence.v1.json").resolve()),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except DenseEvidenceError as error:
        if out_dir is not None:
            _write_status(
                out_dir,
                status="failed",
                failureCategory=error.category,
                message=str(error),
                lastDurableArtifact="normalized/reference.glb",
                resumable=True,
            )
        print(str(error), file=sys.stderr)
        return 1 if error.category in {"evidence_hash_mismatch", "cache_invalid"} else 2
    except (OSError, ValueError) as error:
        if out_dir is not None:
            _write_status(
                out_dir,
                status="failed",
                failureCategory="local_execution_error",
                message=str(error),
                lastDurableArtifact="normalized/reference.glb",
                resumable=True,
            )
        print(f"local_execution_error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
