"""Per-module code cache for the img2threejs factory generator (Plan 1.3
Phase 6, Workstream I).

img2threejs decomposes an object into risk-ranked semantic MODULES. Regenerating
the whole factory on every fix is wasteful and regresses already-approved parts.
This module caches generated factory code PER MODULE, keyed on BOTH the module's
spec fragment AND the generator's own source, so editing one module regenerates
only that module (and its neighbors) rather than the whole object.

It builds on the shared hash-based cache (`_shared/artifact_cache.py`) and adds:
  - a canonical (key-order-independent) hash of a module spec, and
  - §3.7 neighbor re-validation: when a module changes, cached modules ATTACHED
    to it are invalidated, because a neighbor change can introduce interaction
    defects the dependent's already-cached code no longer accounts for.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
from artifact_cache import (  # noqa: E402
    file_sha256,
    get_cached,
    load_manifest,
    manifest_path_for,  # noqa: F401  (re-exported for callers)
    put_cached,
    save_manifest,
)


def canonical_module_hash(module_spec: dict) -> str:
    """sha256 of the module spec serialized deterministically (sorted keys).

    Two specs with the same content but different key insertion order hash
    identically, so cache hits don't depend on dict ordering.
    """
    blob = json.dumps(
        module_spec, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def module_cache_key(module_spec: dict, generator_source: Path) -> str:
    """Key changes if EITHER the module's spec OR the generator source changes.

    Mirrors artifact_cache.cache_key: pairing the spec hash with the generator's
    own source hash closes the stale-cache risk (edit the generator, forget to
    bump a version -> serve stale code).
    """
    return f"{canonical_module_hash(module_spec)}:{file_sha256(generator_source)[:12]}"


def get_module(
    manifest_path: Path, module_spec: dict, generator_source: Path
) -> str | None:
    """Return the cached generated code for this module, or None on a miss."""
    entry = get_cached(manifest_path, module_cache_key(module_spec, generator_source))
    if entry is None:
        return None
    return entry.get("code")


def put_module(
    manifest_path: Path, module_spec: dict, generator_source: Path, code: str
) -> None:
    """Store generated code for this module under its (spec+generator) key."""
    put_cached(
        manifest_path,
        module_cache_key(module_spec, generator_source),
        {"code": code, "moduleId": module_spec.get("id")},
    )


def _dependents_of(changed_module_id: str, components: list[dict]) -> set[str]:
    """Ids of components attached to `changed_module_id` (parent or attachment).

    The changed module itself is never included — only its dependents.
    """
    dependents: set[str] = set()
    for comp in components:
        comp_id = comp.get("id")
        if comp_id == changed_module_id:
            continue
        parent_id = comp.get("parent")
        attachment = comp.get("attachment") or {}
        attach_parent = attachment.get("parentId")
        if parent_id == changed_module_id or attach_parent == changed_module_id:
            if comp_id is not None:
                dependents.add(comp_id)
    return dependents


def invalidate_attached(
    manifest_path: Path, changed_module_id: str, components: list[dict]
) -> list[str]:
    """§3.7 neighbor re-validation.

    Find every component attached to `changed_module_id` (its parent, or its
    attachment.parentId, points at the changed module) and drop ALL cached
    entries whose stored moduleId equals that dependent's id. The changed module
    itself is NOT invalidated here — only its dependents, whose cached code may
    now be wrong because a neighbor moved.

    Returns the sorted list of dependent module ids that were invalidated.
    """
    dependents = _dependents_of(changed_module_id, components)
    if not dependents:
        return []

    manifest = load_manifest(manifest_path)
    removed: set[str] = set()
    for key in list(manifest.keys()):
        entry = manifest[key]
        module_id = entry.get("moduleId") if isinstance(entry, dict) else None
        if module_id in dependents:
            del manifest[key]
            removed.add(module_id)
    save_manifest(manifest_path, manifest)
    return sorted(removed)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="img2threejs per-module code cache")
    parser.add_argument("--manifest", required=True, help="path to cache manifest")
    parser.add_argument("--action", choices=["stats"], default="stats")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(Path(args.manifest))
        module_ids = sorted(
            {
                entry.get("moduleId")
                for entry in manifest.values()
                if isinstance(entry, dict) and entry.get("moduleId") is not None
            }
        )
        result = {"entries": len(manifest), "moduleIds": module_ids}
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(result)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
