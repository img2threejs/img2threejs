"""Lightweight hash-based artifact cache (Plan 1.3 Workstream E).

Cache key = crop file content hash + the extracting script's OWN source-file
hash, both computed automatically at every run — no manually-maintained
version string exists to forget bumping (closes Risk R3: a cache keyed on a
human-maintained version constant will eventually serve stale results after
someone edits the algorithm without remembering to bump it).

This is NOT full module/spec-hash reuse (no dependency graph, no partial-
object hashing) — just "don't redo expensive-ish work on unchanged inputs."
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cache_key(crop_path: Path, script_path: Path) -> str:
    return f"{file_sha256(crop_path)}:{file_sha256(script_path)[:12]}"


def manifest_path_for(directory: Path, name: str) -> Path:
    return directory / ".cache" / name


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def get_cached(manifest_path: Path, key: str) -> dict[str, Any] | None:
    return load_manifest(manifest_path).get(key)


def put_cached(manifest_path: Path, key: str, value: dict[str, Any]) -> None:
    manifest = load_manifest(manifest_path)
    manifest[key] = value
    save_manifest(manifest_path, manifest)
