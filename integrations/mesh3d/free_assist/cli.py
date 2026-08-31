"""Command-line interface for zero-spend generative reference assistance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from .model import AssistFailure, Decision, GenerationRequest, json_record
from .pipeline import default_metadata_probe, generate, preflight, resume
from .registry import PROVIDERS
from .security import redact


def _add_request_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "images", nargs="+", type=Path, help="local reference image files"
    )
    parser.add_argument(
        "--provider",
        required=True,
        choices=tuple(PROVIDERS),
        help="reviewed zero-spend provider",
    )
    parser.add_argument("--out-dir", required=True, type=Path, help="artifact root")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--mesh-simplify", type=float, default=0.95)
    parser.add_argument("--texture-size", type=int, default=None)
    parser.add_argument("--foreground-ratio", type=float, default=0.85)
    parser.add_argument(
        "--force-cpu",
        action="store_true",
        help="force CPU for an already-installed local SF3D",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m integrations.mesh3d.free_assist",
        description="Zero-spend, approval-gated reference-mesh generation. No automatic retry or fallback.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    preflight_parser = commands.add_parser(
        "preflight", help="read-only capability, cost, and cache check"
    )
    _add_request_arguments(preflight_parser)
    generate_parser = commands.add_parser(
        "generate", help="perform one explicitly approved provider call, then normalize"
    )
    _add_request_arguments(generate_parser)
    generate_parser.add_argument(
        "--approve-upload",
        action="store_true",
        help="approve these exact files for this hosted provider",
    )
    generate_parser.add_argument(
        "--approve-local-run",
        action="store_true",
        help="approve one run of an already-installed local SF3D",
    )
    resume_parser = commands.add_parser(
        "resume", help="normalize an already persisted raw GLB without generation"
    )
    resume_parser.add_argument(
        "--run", required=True, type=Path, help="existing runs/<run-id> directory"
    )
    return parser


def _request(args: argparse.Namespace) -> GenerationRequest:
    parameters: dict[str, Any] = {
        "seed": args.seed,
        "meshSimplify": args.mesh_simplify,
        "foregroundRatio": args.foreground_ratio,
        "forceCpu": args.force_cpu,
    }
    if args.texture_size is not None:
        parameters["textureSize"] = args.texture_size
    return GenerationRequest(
        tuple(args.images), args.provider, args.out_dir, parameters=parameters
    )


def _print(value: Any, *, stream: Any | None = None) -> None:
    if stream is None:
        stream = sys.stdout
    print(
        json.dumps(
            redact(json_record(value)), indent=2, sort_keys=True, ensure_ascii=False
        ),
        file=stream,
    )


def main(
    argv: list[str] | None = None,
    *,
    metadata_probe: Callable[[str], dict[str, Any]] = default_metadata_probe,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preflight":
            report = preflight(_request(args), metadata_probe=metadata_probe)
            _print(report)
            return 0 if report.decision == Decision.ALLOW else 3
        if args.command == "generate":
            request = _request(args)
            run = generate(
                request,
                approve_upload=args.approve_upload,
                approve_local_run=args.approve_local_run,
                metadata_probe=metadata_probe,
            )
            status = resume(run)
            _print({"run": str(run), **status})
            return 0 if status.get("status") == "complete" else 2
        status = resume(args.run)
        _print({"run": str(args.run.resolve()), **status})
        return 0 if status.get("status") == "complete" else 2
    except AssistFailure as exc:
        _print(
            {
                "status": "blocked",
                "failureCategory": exc.category,
                "message": str(exc),
                "resumable": exc.resumable,
                "lastDurableArtifact": exc.last_artifact,
            },
            stream=sys.stderr,
        )
        if exc.category in {
            "upload_not_approved",
            "free_status_unverified",
            "authentication_required",
            "local_capability_missing",
        }:
            return 3
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
