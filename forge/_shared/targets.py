"""Resolve emission targets (design D5, `establish-the-emission-target-contract`).

An emission target is a terminal, whole-artifact transform declared by an installed plugin's
`steps.json` `provides` row (D1) and discovered through its `plugin.json` capability edge. This
module answers exactly one question -- "which installed provider, if any, serves `--target <kind>`"
-- by reading the same `plugins.json` registry the harness's Node CLI writes, plus each candidate
plugin's own manifest and step declarations.

It is deliberately NOT an extension of `forge/_shared/domains/__init__.py`: that registry's
`_ALLOWED` key set is a closed domain-profile vocabulary, and the target axis is orthogonal to it
(D5). `_img2_home()` is duplicated here rather than imported from `domains` for the same reason --
this module is an independent reader, not a client of the domain registry.

**Fail-loud is scoped to selection time, not import time.** Nothing at module scope touches the
filesystem; `resolve_target()` is the only entry point that reads `plugins.json`, and it is called
only after the caller has already decided a target was explicitly requested. A malformed registry
therefore cannot break `emit_target.py --help` or its no-target default path (round-3 H1's PR #106
citation: `registered_domains()` running at argparse-construction time is exactly the shape this
module avoids).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Final, NamedTuple

# Mirrors img2-harness/bin/img2.mjs's MAX_PLUGIN_SCHEMA / CORE_API / MAX_PROVIDES_SCHEMA (`:11-17`).
# Both languages parse a plugin manifest independently by design (D5) -- doctor-green and
# base-ran-it must never disagree about what a manifest requires -- so both sides carry the same
# numbers. There is no shared source for them across the Node/Python boundary; keeping them in sync
# is a manual discipline, same as CONTRACT_REVISION.
MAX_PLUGIN_SCHEMA: Final = 1
CORE_API: Final = 1
MAX_PROVIDES_SCHEMA: Final = 1

SCULPT_SPEC_KIND: Final = "sculpt-spec"
REFERENCE_TARGET_KIND: Final = "threejs-ts"


class TargetResolutionError(ValueError):
    """A `--target` request could not be resolved, or a candidate's declaration is invalid.

    The message always names the kind, and for ambiguity every claimant -- callers print
    `str(exc)` and refuse; there is no fallback to the no-target default from this error (D6:
    fallback is only for "nothing selected", never for a selection that failed)."""


class Target(NamedTuple):
    kind: str
    plugin_id: str | None       # None only for the base-owned reference target
    plugin_dir: Path | None     # None only for the reference target
    plugin_version: str | None  # the manifest's own "version", for provenance
    resolved_sha: str | None    # the registry row's resolvedSha, for provenance
    command: str | None        # the providing step's raw, unsubstituted command; None for reference
    step_id: str | None
    artifact_kind: str
    artifact_path: str | None   # workspace-relative, already prefixed with .img2/artifacts/<plugin-id>/; reference: None
    deterministic: bool
    timeout_seconds: int | None  # per-step override, or None for the socket's default


def _img2_home() -> Path:
    # Deliberately duplicated from domains/__init__.py:102-103 -- see the module docstring.
    return Path(os.environ.get("IMG2_HOME") or Path.home() / ".img2")


def _harness_version(home: Path) -> str:
    package_json = home / "harness" / "package.json"
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
        version = data.get("version")
        return version if isinstance(version, str) else "0.0.0"
    except (OSError, json.JSONDecodeError):
        return "0.0.0"


_SEMVER_RE: Final = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_RANGE_RE: Final = re.compile(r"^>=\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?$")


def _parse_semver(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = _SEMVER_RE.match(value.strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def _range_satisfied(range_str: str, version: str) -> bool:
    # Same restricted grammar as img2.mjs's rangeSatisfied (":140-152"): only ">=X[.Y[.Z]]",
    # missing minor/patch defaulting to 0.
    match = _RANGE_RE.match(range_str.strip())
    if not match:
        raise TargetResolutionError(f'unsupported requires.harness range "{range_str}"')
    minimum = (int(match[1]), int(match[2] or 0), int(match[3] or 0))
    actual = _parse_semver(version)
    if actual is None:
        raise TargetResolutionError(f"unparseable harness version: {version}")
    return actual >= minimum


def _read_json(path: Path, *, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TargetResolutionError(f"{what} does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise TargetResolutionError(f"{what} is not valid JSON: {path}: {exc}") from exc


def _validate_manifest(manifest: Any, home: Path, *, plugin_id: str, where: Path) -> dict[str, Any]:
    """Re-check the manifest rules the Node CLI's `validateManifest` enforces (D5): `schema` <=
    max, `coreApi` exact equality, `requires.harness` range -- so a plugin that somehow drifted out
    of doctor-green state (edited by hand, or installed by an older harness) is refused here too,
    not silently invoked."""
    if not isinstance(manifest, dict):
        raise TargetResolutionError(f"plugin {plugin_id!r}: {where} is not a JSON object")
    schema = manifest.get("schema")
    if not isinstance(schema, int) or schema < 1:
        raise TargetResolutionError(f"plugin {plugin_id!r}: manifest \"schema\" must be an integer >= 1")
    if schema > MAX_PLUGIN_SCHEMA:
        raise TargetResolutionError(
            f"plugin {plugin_id!r}: manifest schema {schema} is newer than this base reads "
            f"(MAX_PLUGIN_SCHEMA={MAX_PLUGIN_SCHEMA})"
        )
    requires = manifest.get("requires")
    if not isinstance(requires, dict):
        raise TargetResolutionError(f"plugin {plugin_id!r}: manifest \"requires\" must be an object")
    harness_range = requires.get("harness")
    if not isinstance(harness_range, str):
        raise TargetResolutionError(f"plugin {plugin_id!r}: \"requires.harness\" must be a semver range string")
    if not _range_satisfied(harness_range, _harness_version(home)):
        raise TargetResolutionError(
            f"plugin {plugin_id!r} requires harness {harness_range!r} but the installed harness is "
            f"{_harness_version(home)!r}"
        )
    core_api = requires.get("coreApi")
    if not isinstance(core_api, int):
        raise TargetResolutionError(f"plugin {plugin_id!r}: \"requires.coreApi\" must be an integer")
    if core_api != CORE_API:
        raise TargetResolutionError(
            f"plugin {plugin_id!r} requires coreApi {core_api} but this base provides coreApi {CORE_API}"
        )
    return manifest


def path_escapes(declared_path: Any, plugin_id: str) -> bool:
    """Mirrors img2.mjs's `artifactPathEscapes` exactly: a declared artifact path must resolve
    under `.img2/artifacts/<plugin-id>/` -- no absolute path, no leading `~`, no `..` escape.
    Checked here (for the runtime re-check emit_target.py performs before invocation, D6) and at
    doctor (the harness's own static check) -- the two must never disagree."""
    if not isinstance(declared_path, str) or not declared_path:
        return True
    if os.path.isabs(declared_path) or declared_path.startswith("~"):
        return True
    prefix = f".img2/artifacts/{plugin_id}/"
    normalized = os.path.normpath(declared_path).replace(os.sep, "/")
    if normalized == ".." or normalized.startswith("../"):
        return True
    return not normalized.startswith(prefix)


def _validate_provides(provides: Any, *, plugin_id: str, step_id: str) -> dict[str, Any]:
    if not isinstance(provides, dict):
        raise TargetResolutionError(f"plugin {plugin_id!r} step {step_id!r}: \"provides\" must be an object")
    version = provides.get("version")
    if not isinstance(version, int) or version < 1:
        raise TargetResolutionError(
            f"plugin {plugin_id!r} step {step_id!r}: \"provides.version\" must be an integer >= 1"
        )
    if version > MAX_PROVIDES_SCHEMA:
        raise TargetResolutionError(
            f"plugin {plugin_id!r} step {step_id!r}: provides.version {version} is newer than this "
            f"base reads (MAX_PROVIDES_SCHEMA={MAX_PROVIDES_SCHEMA})"
        )
    artifact = provides.get("artifact")
    if not isinstance(artifact, dict):
        raise TargetResolutionError(f"plugin {plugin_id!r} step {step_id!r}: \"provides.artifact\" must be an object")
    kind = artifact.get("kind")
    path = artifact.get("path")
    if not isinstance(kind, str) or not kind:
        raise TargetResolutionError(f"plugin {plugin_id!r} step {step_id!r}: \"provides.artifact.kind\" is required")
    if not isinstance(path, str) or not path:
        raise TargetResolutionError(f"plugin {plugin_id!r} step {step_id!r}: \"provides.artifact.path\" is required")
    if path_escapes(path, plugin_id):
        raise TargetResolutionError(
            f"plugin {plugin_id!r} step {step_id!r}: provides.artifact.path {path!r} must resolve "
            f"under .img2/artifacts/{plugin_id}/"
        )
    return provides


def _plugin_targets(home: Path) -> list[Target]:
    """Every target edge declared by an installed plugin, of any kind."""
    registry = home / "plugins.json"
    if not registry.is_file():
        return []
    registry_doc = _read_json(registry, what="the plugin registry")
    rows = registry_doc.get("plugins") if isinstance(registry_doc, dict) else None
    if not isinstance(rows, list):
        raise TargetResolutionError(f"the plugin registry at {registry} has no \"plugins\" array")

    out: list[Target] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        plugin_id = row.get("id")
        if not isinstance(plugin_id, str) or not plugin_id:
            continue
        plugin_dir = home / "plugins" / plugin_id
        manifest_path = plugin_dir / "plugin.json"
        steps_path = plugin_dir / "steps.json"
        if not manifest_path.is_file() or not steps_path.is_file():
            # A plugin with no steps.json declares no targets -- it may still provide capabilities
            # this module has no reason to know about.
            continue
        manifest = _validate_manifest(
            _read_json(manifest_path, what=f"plugin {plugin_id!r}'s plugin.json"),
            home,
            plugin_id=plugin_id,
            where=manifest_path,
        )
        capability_kinds = {
            cap.get("to")
            for cap in manifest.get("capabilities", [])
            if isinstance(cap, dict) and cap.get("from") == SCULPT_SPEC_KIND
        }
        steps = _read_json(steps_path, what=f"plugin {plugin_id!r}'s steps.json")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            provides = step.get("provides")
            if not isinstance(provides, dict) or provides.get("from") != SCULPT_SPEC_KIND:
                continue
            step_id = step.get("id") if isinstance(step.get("id"), str) else "<unnamed step>"
            provides = _validate_provides(provides, plugin_id=plugin_id, step_id=step_id)
            kind = provides.get("to")
            if not isinstance(kind, str) or not kind:
                raise TargetResolutionError(f"plugin {plugin_id!r} step {step_id!r}: \"provides.to\" is required")
            if kind not in capability_kinds:
                # Doctor should already have refused this at install time (task 1.2); re-checked
                # here because the registry can drift after doctor last ran (D5's premise).
                raise TargetResolutionError(
                    f"plugin {plugin_id!r} step {step_id!r} provides {kind!r} with no matching "
                    f"manifest capability edge from {SCULPT_SPEC_KIND!r}"
                )
            deterministic = step.get("deterministic")
            if not isinstance(deterministic, bool):
                raise TargetResolutionError(
                    f"plugin {plugin_id!r} step {step_id!r}: \"deterministic\" must be declared as a boolean "
                    f"beside \"provides\" (D7)"
                )
            timeout_seconds = step.get("timeoutSeconds")
            if timeout_seconds is not None and not (isinstance(timeout_seconds, int) and timeout_seconds > 0):
                raise TargetResolutionError(
                    f"plugin {plugin_id!r} step {step_id!r}: \"timeoutSeconds\" must be a positive integer"
                )
            artifact = provides["artifact"]
            out.append(Target(
                kind=kind,
                plugin_id=plugin_id,
                plugin_dir=plugin_dir,
                plugin_version=manifest.get("version") if isinstance(manifest.get("version"), str) else None,
                resolved_sha=row.get("resolvedSha") if isinstance(row.get("resolvedSha"), str) else None,
                command=step.get("command") if isinstance(step.get("command"), str) else None,
                step_id=step_id,
                artifact_kind=artifact["kind"],
                artifact_path=artifact["path"],
                deterministic=deterministic,
                timeout_seconds=timeout_seconds,
            ))
    return out


def reference_target() -> Target:
    """`threejs-ts`: the base-owned reference target (D2, D8, M6's resolution (b)).

    Declared here, as base-owned data, rather than as a real installed plugin -- the base checkout
    ships no `plugin.json` of its own (it is registered with hosts as a skill, not a plugin). This is
    the named exception to "resolved through the same path as a plugin-declared target": the
    declaration's home differs, but resolution (via `resolve_target`) does not special-case it
    beyond that -- it is filtered, matched, and disambiguated exactly like any plugin candidate.
    """
    return Target(
        kind=REFERENCE_TARGET_KIND,
        plugin_id=None,
        plugin_dir=None,
        plugin_version=None,
        resolved_sha=None,
        command=None,
        step_id=None,
        artifact_kind=REFERENCE_TARGET_KIND,
        artifact_path=None,
        deterministic=True,
        timeout_seconds=None,
    )


def resolve_target(kind: str, *, plugin: str | None = None, home: Path | None = None) -> Target:
    """Resolve an explicit `--target <kind>` [`--plugin <id>`] request.

    Never call this for the no-target path: it reads `plugins.json` and every installed target
    plugin's manifest, and `emit_target.py` must return before any of that when no target was
    requested (D5).
    """
    home = home or _img2_home()
    all_targets = _plugin_targets(home) + [reference_target()]
    candidates = [target for target in all_targets if target.kind == kind]
    if plugin is not None:
        candidates = [target for target in candidates if target.plugin_id == plugin]
        if not candidates:
            raise TargetResolutionError(f"no installed plugin {plugin!r} provides target kind {kind!r}")
    if not candidates:
        raise TargetResolutionError(f"no installed plugin provides target kind {kind!r}")
    if len(candidates) > 1:
        claimants = sorted(target.plugin_id or f"{REFERENCE_TARGET_KIND} (base reference target)" for target in candidates)
        raise TargetResolutionError(
            f"target kind {kind!r} is provided by more than one installed plugin: "
            f"{', '.join(claimants)}; disambiguate with --plugin"
        )
    return candidates[0]
