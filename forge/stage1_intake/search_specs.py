#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias, TypedDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from forge._shared.spec_search import (  # noqa: E402
    CacheReadError,
    CacheValidationError,
    CacheWriteError,
    IndexBuildError,
    IndexLoadResult,
    IndexRequest,
    ProfileValidationError,
    ProfileCachePathError,
    SearchOutputRequest,
    SerializedSearchMatch,
    SourceIngestionError,
    SpecRecordValidationError,
    UnknownCollectionError,
    load_or_build_index,
    load_profile,
    serialize_search_results,
)

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
MIN_SNIPPET_CHARS: Final = 5


class OutputIndex(TypedDict):
    status: str
    reason: str
    fingerprint: str
    cache_path: str


class SuccessPayload(TypedDict):
    query: str
    collection: str
    index: OutputIndex
    matches: list[SerializedSearchMatch]


@dataclass(frozen=True, slots=True)
class CliOptions:
    query: str
    collection: str
    limit: int
    snippet_chars: int
    reindex: bool
    json_output: bool


@dataclass(frozen=True, slots=True)
class ErrorContext:
    query: str
    collection: str
    json_output: bool


@dataclass(frozen=True, slots=True)
class CliFailure:
    code: str
    message: str
    exit_code: int


class CliArgumentError(ValueError):
    pass


class CliNamespace(argparse.Namespace):
    def __init__(self) -> None:
        super().__init__()
        self.query: list[str] = []
        self.collection: str = "cs2"
        self.limit: int = 3
        self.snippet_chars: int = 250
        self.reindex: bool = False
        self.json_output: bool = False


def _parse_options(argv: Sequence[str]) -> CliOptions:
    parser = argparse.ArgumentParser(
        description="Search registered specification collections with local BM25."
    )
    _ = parser.add_argument("query", nargs="*", help="Search terms")
    _ = parser.add_argument("--collection", default="cs2")
    _ = parser.add_argument("--limit", type=int, default=3)
    _ = parser.add_argument(
        "--snippet-chars",
        type=int,
        default=250,
        help=f"Maximum snippet length; must be at least {MIN_SNIPPET_CHARS}.",
    )
    _ = parser.add_argument("--reindex", action="store_true")
    _ = parser.add_argument("--json", action="store_true", dest="json_output")
    namespace = CliNamespace()
    diagnostics = io.StringIO()
    try:
        with contextlib.redirect_stderr(diagnostics):
            _ = parser.parse_args(argv, namespace=namespace)
    except SystemExit as error:
        if error.code == 0:
            raise
        raise CliArgumentError(diagnostics.getvalue().strip()) from None
    return CliOptions(
        query=" ".join(namespace.query).strip(),
        collection=namespace.collection,
        limit=namespace.limit,
        snippet_chars=namespace.snippet_chars,
        reindex=namespace.reindex,
        json_output=namespace.json_output,
    )


def _index_payload(result: IndexLoadResult) -> OutputIndex:
    return {
        "status": result.status,
        "reason": result.reason,
        "fingerprint": result.fingerprint,
        "cache_path": str(result.cache_path),
    }


def _print_json(payload: SuccessPayload | dict[str, JsonValue]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _print_human(payload: SuccessPayload) -> None:
    print(f"Query: {payload['query']}")
    print(f"Collection: {payload['collection']}")
    index = payload["index"]
    print(f"Index: {index['status']} ({index['reason']}) {index['fingerprint']}")
    if not payload["matches"]:
        print("No matches.")
        return
    for position, match in enumerate(payload["matches"], start=1):
        location = match["file_path"]
        if match["heading"] is not None:
            location += f" :: {match['heading']}"
        if match["key_path"] is not None:
            location += f" :: {match['key_path']}"
        print(f"{position}. {match['record_id']}  score={match['score']:.6f}")
        print(f"   source: {location}")
        print(f"   {match['snippets'][0]}")


def _emit_error(context: ErrorContext, failure: CliFailure) -> int:
    print(f"error: {failure.message}", file=sys.stderr)
    if context.json_output:
        _print_json(
            {
                "query": context.query,
                "collection": context.collection,
                "index": None,
                "matches": [],
                "error": {"code": failure.code, "message": failure.message},
            }
        )
    return failure.exit_code


def main(argv: Sequence[str]) -> int:
    json_requested = "--json" in argv
    try:
        options = _parse_options(argv)
    except CliArgumentError as error:
        return _emit_error(
            ErrorContext("", "cs2", json_requested),
            CliFailure("invalid_arguments", str(error), 2),
        )

    context = ErrorContext(options.query, options.collection, options.json_output)
    if not options.query:
        return _emit_error(context, CliFailure("empty_query", "query must not be empty", 2))
    if options.limit <= 0:
        return _emit_error(
            context,
            CliFailure("invalid_limit", "--limit must be greater than zero", 2),
        )
    if options.snippet_chars < MIN_SNIPPET_CHARS:
        return _emit_error(
            context,
            CliFailure(
                "invalid_snippet_chars",
                f"--snippet-chars must be at least {MIN_SNIPPET_CHARS} to include source text",
                2,
            ),
        )

    try:
        profile = load_profile(options.collection)
        loaded = load_or_build_index(
            IndexRequest(PROJECT_ROOT, options.collection, profile, options.reindex)
        )
        matches = serialize_search_results(
            loaded.index,
            SearchOutputRequest(options.query, options.limit, options.snippet_chars),
        )
    except UnknownCollectionError as error:
        return _emit_error(
            context,
            CliFailure("unknown_collection", str(error), 2),
        )
    except ProfileCachePathError as error:
        return _emit_error(
            context,
            CliFailure("cache_failure", str(error), 3),
        )
    except ProfileValidationError as error:
        return _emit_error(
            context,
            CliFailure("profile_failure", str(error), 3),
        )
    except (SourceIngestionError, SpecRecordValidationError) as error:
        return _emit_error(
            context,
            CliFailure("source_failure", str(error), 3),
        )
    except IndexBuildError as error:
        return _emit_error(
            context,
            CliFailure("index_failure", str(error), 3),
        )
    except (CacheReadError, CacheValidationError, CacheWriteError) as error:
        return _emit_error(
            context,
            CliFailure("cache_failure", str(error), 3),
        )

    payload: SuccessPayload = {
        "query": options.query,
        "collection": options.collection,
        "index": _index_payload(loaded),
        "matches": matches,
    }
    if options.json_output:
        _print_json(payload)
    else:
        _print_human(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
