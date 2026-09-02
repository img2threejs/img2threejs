#!/usr/bin/env python3
"""Resolve and invoke an emission target (design D4), or no-op with none selected.

This is a NEW CLI. `generate_threejs_factory.py` is not modified by this module and carries no
target-related edits at all -- the terminal export step is layered on top of it, never inside it.

With no `--target`, this is a successful no-op: it writes nothing, changes no byte, and returns
before `forge/_shared/targets.py` reads anything (D5) -- a malformed `plugins.json` therefore cannot
break the default path, matching PR #106's finding about `registered_domains()` running at
argparse-construction time.

With `--target <kind>`, the socket: checks the terminal (`action-ready`) precondition (D3) -> resolves
the target (D5, refusing ambiguity or a missing kind, never falling back) -> re-checks the artifact
path statically (D6) -> invokes it as a bounded subprocess with an injected `IMG2_HOME` (D6, round-3
H1) -> verifies the artifact to the honest limit (D10) -> verifies determinism if declared (D7) ->
records provenance (D5's manifest data plus the spec content hash).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_FORGE_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_FORGE_ROOT), str(_FORGE_ROOT / "_shared")]

from glb_container import parse_glb  # noqa: E402
from targets import (  # noqa: E402
    Target,
    TargetResolutionError,
    _harness_version,
    path_escapes,
    resolve_target,
)
from workflow_state import WorkflowStateError, load_state, mark_steps, save_state, status_payload  # noqa: E402

DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 1800
MAX_STDOUT_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL")
ARTIFACT_OF_RECORD = "src/createObjectModel.ts"
DETERMINISM_CACHE_NAME = ".determinism-verified.json"


class EmitTargetError(RuntimeError):
    """A target run was refused or failed. Callers print `str(exc)` and exit non-zero -- never
    fall back to the no-target default (D6): fallback only ever applies to "nothing selected".

    `classification` distinguishes what kind of failure this is -- see `_classify_reference_
    failure` for the one case (the reference target) where the socket bothers to tell them apart
    instead of reporting a flat "error" for everything."""

    def __init__(self, message: str, *, classification: str = "error") -> None:
        super().__init__(message)
        self.classification = classification


def _img2_home() -> Path:
    return Path(os.environ.get("IMG2_HOME") or Path.home() / ".img2")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------- D3: terminal-only precondition

def assert_action_ready(workspace: Path) -> None:
    """Refuse unless workspace state records `action-ready`, naming the unmet step (D3).

    Reads state via `status_payload` only, per design's stated limit: this does not re-verify
    `action-ready`'s recorded evidence content (free text a human or agent supplied) -- it trusts
    the checklist's own `done` marking, the same way `mark_steps`'s out-of-order guard does.
    """
    state_path = workspace / ".img2threejs" / "state.json"
    if not state_path.is_file():
        raise EmitTargetError(
            f"target run refused: no workflow state at {state_path}; this workspace has not "
            f"reached action-ready"
        )
    try:
        state = load_state(state_path)
    except WorkflowStateError as exc:
        raise EmitTargetError(f"target run refused: {exc}") from exc
    by_id = {entry["id"]: entry for entry in state["checklist"]}
    action_ready = by_id.get("action-ready")
    if action_ready is None or action_ready.get("status") != "done":
        payload = status_payload(state)
        unmet = payload["currentStep"]
        raise EmitTargetError(
            f"target run refused: action-ready is not recorded yet (next required step: {unmet!r})"
        )


def record_target_selection(workspace: Path, target: Target, *, artifact_path: Path) -> None:
    """After a successful target run, record WHICH plugin ran (task 4.2's own precondition: gate
    execution needs to know whether a plugin's step actually ran in THIS workspace, and nothing
    else in workflow state carries that attribution for a target step). Additive only -- a new
    top-level `targetSelection` key, never `reviewHistory`/`buildPasses`/any pass-state field (D3).

    Marks the `emission-target` checklist entry `done` when it exists. It will not exist in a
    workspace created before this change (round-3 H6: `new_state()` materialises `FINAL_STEPS`
    once, at init) -- that is not an error, the workspace simply has no such step to mark, and
    `targetSelection` is still recorded so gate participation can still be determined for it.
    """
    state_path = workspace / ".img2threejs" / "state.json"
    state = load_state(state_path)
    by_id = {entry["id"]: entry for entry in state["checklist"]}
    if "emission-target" in by_id and by_id["emission-target"]["status"] != "done":
        mark_steps(state, ["emission-target"], status="done", evidence=[str(artifact_path)])
    state["targetSelection"] = {"pluginId": target.plugin_id, "kind": target.kind}
    save_state(state_path, state)


# ---------------------------------------------------------------- D6: the bounded runner

def _bounded_env(declared: list[str]) -> dict[str, str]:
    """Construct the child environment. `IMG2_HOME` is INJECTED from the base's own resolution,
    never merely allowed to pass through (round-3 H1: an allowlist forwards a variable only when
    the parent already has it, and the base itself commonly runs with IMG2_HOME unset)."""
    env = {name: os.environ[name] for name in ENV_ALLOWLIST if name in os.environ}
    for name in declared:
        if name in os.environ:
            env[name] = os.environ[name]
    env["IMG2_HOME"] = str(_img2_home())
    return env


def _resolve_timeout(target: Target) -> int:
    timeout = target.timeout_seconds if target.timeout_seconds is not None else DEFAULT_TIMEOUT_SECONDS
    if timeout > MAX_TIMEOUT_SECONDS:
        raise EmitTargetError(
            f"target {target.kind!r}: declared timeoutSeconds {timeout} exceeds the base-owned "
            f"ceiling of {MAX_TIMEOUT_SECONDS}s"
        )
    return timeout


def run_bounded(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    declared_env: list[str],
    target_kind: str,
) -> subprocess.CompletedProcess:
    env = _bounded_env(declared_env)
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.TimeoutExpired as exc:
        raise EmitTargetError(f"target {target_kind!r} timed out after {timeout}s") from exc
    except (FileNotFoundError, PermissionError) as exc:
        raise EmitTargetError(f"target {target_kind!r}: could not spawn its command: {exc}") from exc
    # subprocess.CompletedProcess is an ordinary mutable object (not a namedtuple); capping its
    # captured output in place is the bound (D6: 1 MiB stdout / 64 KiB stderr).
    if len(proc.stdout) > MAX_STDOUT_BYTES:
        proc.stdout = proc.stdout[:MAX_STDOUT_BYTES]
    if len(proc.stderr) > MAX_STDERR_BYTES:
        proc.stderr = proc.stderr[:MAX_STDERR_BYTES]
    return proc


def _substitute(command: str, *, workspace: Path, spec: Path, plugin_dir: Path) -> list[str]:
    """{plugin_dir}/{workspace}/{spec} replaced by value -- steps.json's closed placeholder set
    (img2.mjs:670). {image} is not substituted: no installed target step should need it (a target's
    `from` is always sculpt-spec, never image), and leaving it untouched matches the harness's own
    "replace by value" discipline rather than inventing a new rule for this file."""
    return [
        token.replace("{plugin_dir}", str(plugin_dir)).replace("{workspace}", str(workspace)).replace("{spec}", str(spec))
        for token in shlex.split(command)
    ]


# ---------------------------------------------------------------- D10: tier-1 verification

def _verify_glb(path: Path) -> None:
    try:
        parse_glb(path)
    except ValueError as exc:
        raise EmitTargetError(f"GLB container check failed for {path}: {exc}") from exc


_PROBERS = {"glb": _verify_glb}


def verify_artifact(path: Path, declared_kind: str) -> dict[str, Any]:
    """Verify to the honest limit (D10): container check where a prober exists, existence/size/
    location always. Raises EmitTargetError naming the declared/actual kinds on a wrong-kind
    artifact; returns the recorded verification level otherwise."""
    if not path.is_file():
        raise EmitTargetError(f"target produced no artifact at {path}")
    size = path.stat().st_size
    if size > MAX_ARTIFACT_BYTES:
        raise EmitTargetError(
            f"target {declared_kind!r} artifact at {path} is {size} bytes, exceeding the "
            f"{MAX_ARTIFACT_BYTES}-byte bound"
        )
    prober = _PROBERS.get(declared_kind)
    if prober is None:
        return {
            "kind": declared_kind,
            "level": "existence+size+location",
            "note": "no container prober for this kind; no further base quality warranty applies",
        }
    try:
        prober(path)
    except EmitTargetError as exc:
        raise EmitTargetError(
            f"target declared kind {declared_kind!r} but its artifact failed that kind's container "
            f"check: {exc}"
        ) from exc
    return {"kind": declared_kind, "level": "container+existence+size+location"}


# ---------------------------------------------------------------- D7: determinism

def _determinism_cache_path(workspace: Path, plugin_id: str) -> Path:
    return workspace / ".img2" / "artifacts" / plugin_id / DETERMINISM_CACHE_NAME


def _determinism_cache_key(resolved_sha: str | None, spec_hash: str) -> str:
    return f"{resolved_sha or '<none>'}:{spec_hash}"


def verify_determinism(
    target: Target,
    *,
    argv: list[str],
    cwd: Path,
    timeout: int,
    artifact_path: Path,
    spec_hash: str,
) -> bool:
    """Verify a declared-deterministic target by double-invocation, cached once per
    (resolvedSha, spec-content-hash) so repeat builds cost zero (D7). Returns True if this run
    performed (and passed) verification, False if it was served from the cache. Raises
    EmitTargetError naming the target on a mismatch. There is no skip flag anywhere."""
    cache_path = _determinism_cache_path(cwd, target.plugin_id or "threejs-ts")
    key = _determinism_cache_key(target.resolved_sha, spec_hash)
    cache: dict[str, bool] = {}
    if cache_path.is_file():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache = {}
    if cache.get(key):
        return False

    first_bytes = artifact_path.read_bytes()
    proc = run_bounded(argv, cwd=cwd, timeout=timeout, declared_env=[], target_kind=target.kind)
    if proc.returncode != 0:
        raise EmitTargetError(
            f"target {target.kind!r}: determinism re-invocation exited {proc.returncode}: "
            f"{proc.stderr.decode('utf-8', errors='replace')}"
        )
    second_bytes = artifact_path.read_bytes()
    if first_bytes != second_bytes:
        raise EmitTargetError(
            f"target {target.kind!r} declared itself deterministic but two invocations against the "
            f"same (resolvedSha, spec) produced different bytes"
        )
    cache[key] = True
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    return True


# ---------------------------------------------------------------- provenance

def build_provenance(
    target: Target,
    *,
    spec_hash: str,
    harness_version: str,
    verification: dict[str, Any],
    determinism_verified: bool | None,
) -> dict[str, Any]:
    return {
        "kind": "img2.target-artifact",
        "version": 1,
        "target": target.kind,
        "plugin": {
            "id": target.plugin_id,
            "version": target.plugin_version,
            "resolvedSha": target.resolved_sha,
        },
        "specContentHash": spec_hash,
        "harnessVersion": harness_version,
        "deterministic": target.deterministic,
        "determinismVerified": determinism_verified,
        "verification": verification,
        # Never reported under a gated TypeScript build's completion vocabulary (D10's own scenario):
        # this section exists precisely so nothing downstream can conflate the two.
        "baseQualityWarranty": "container/existence/size/location only; no visual warranty",
    }


def _write_provenance(artifact_path: Path, provenance: dict[str, Any]) -> None:
    provenance_path = artifact_path.with_suffix(artifact_path.suffix + ".provenance.json")
    handle, tmp_name = tempfile.mkstemp(dir=provenance_path.parent, prefix=".provenance.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(provenance, stream, indent=2)
            stream.write("\n")
    except BaseException:
        os.unlink(tmp_name)
        raise
    os.replace(tmp_name, provenance_path)


# ---------------------------------------------------------------- no-target path (D2)

def run_no_target() -> int:
    """The default: no target selected. Writes nothing, changes no byte, succeeds -- and returns
    before this function is even reached, since main() checks `args.target is None` before calling
    anything in this module that touches the registry (D5)."""
    print(f"no target selected; the artifact of record is {ARTIFACT_OF_RECORD}")
    return 0


# ---------------------------------------------------------------- target invocation

def run_target(
    kind: str,
    *,
    plugin: str | None,
    spec_path: Path,
    workspace: Path,
    harness_version_reader=None,
) -> int:
    assert_action_ready(workspace)
    try:
        target = resolve_target(kind, plugin=plugin)
    except TargetResolutionError as exc:
        raise EmitTargetError(str(exc)) from exc

    if target.plugin_id is None:
        return _run_reference_target(target, spec_path=spec_path, workspace=workspace)
    return _run_plugin_target(target, spec_path=spec_path, workspace=workspace)


def _run_plugin_target(target: Target, *, spec_path: Path, workspace: Path) -> int:
    assert target.plugin_dir is not None and target.artifact_path is not None and target.command is not None
    # Runtime re-check (D6): the same static rule doctor enforces, re-checked here before the
    # subprocess starts, since the registry can drift after doctor last ran.
    if path_escapes(target.artifact_path, target.plugin_id):
        raise EmitTargetError(
            f"target {target.kind!r}: declared artifact path {target.artifact_path!r} does not "
            f"resolve under .img2/artifacts/{target.plugin_id}/"
        )
    # target.artifact_path is already workspace-relative and already carries the full
    # ".img2/artifacts/<plugin-id>/" prefix (that is exactly what path_escapes just re-checked) --
    # it is not a bare filename to be joined under that prefix a second time.
    artifact_path = (workspace / target.artifact_path).resolve()
    argv = _substitute(target.command, workspace=workspace, spec=spec_path, plugin_dir=target.plugin_dir)
    timeout = _resolve_timeout(target)
    spec_hash = _sha256_file(spec_path)

    # Delete-on-failure wraps the whole invocation, not just a non-zero exit: the plugin writes to
    # its own declared path directly (there is no base-owned temp location to rename from for a
    # third-party target -- only the reference target, which the base invokes with its own --out,
    # gets true temp-write+rename below), so ANY failure here -- crash, non-zero exit, timeout,
    # wrong-kind, oversized -- must not leave a right-named wrong-length file behind. A timeout
    # raises out of run_bounded() before a CompletedProcess even exists, so the cleanup cannot live
    # behind a "check proc.returncode" gate; it has to wrap the call itself.
    try:
        proc = run_bounded(argv, cwd=workspace, timeout=timeout, declared_env=[], target_kind=target.kind)
        if proc.returncode != 0:
            raise EmitTargetError(
                f"target {target.kind!r} (plugin {target.plugin_id!r}) exited {proc.returncode}: "
                f"{proc.stderr.decode('utf-8', errors='replace')}"
            )
        verification = verify_artifact(artifact_path, target.artifact_kind)
        determinism_verified = None
        if target.deterministic:
            determinism_verified = verify_determinism(
                target,
                argv=argv,
                cwd=workspace,
                timeout=timeout,
                artifact_path=artifact_path,
                spec_hash=spec_hash,
            )
    except EmitTargetError:
        artifact_path.unlink(missing_ok=True)
        raise

    provenance = build_provenance(
        target,
        spec_hash=spec_hash,
        harness_version=_harness_version_for_provenance(),
        verification=verification,
        determinism_verified=determinism_verified,
    )
    _write_provenance(artifact_path, provenance)
    record_target_selection(workspace, target, artifact_path=artifact_path)
    print(json.dumps({"artifact": str(artifact_path), "provenance": provenance}, indent=2))
    return 0


def _classify_reference_failure(returncode: int, stderr: bytes) -> EmitTargetError:
    """Reconciliation (task 3.10's envelope-parity gap, resolved by the lead): `generate_threejs_
    factory.py main()` returns exit 2 for both a strict-quality BLOCKED report (a structured JSON
    envelope printed to stderr by `emit_blocked`) and a plain `argparse.error()` usage mistake
    (free-text prose, also on stderr, also exit 2) -- one exit code, two unrelated shapes.

    The socket classifies by envelope, not by exit code, and this makes ZERO changes to
    `generate_threejs_factory.py` -- the emitter's exit-code overload is its own pre-existing
    surface, left for whoever next revises `main()`; only the socket's own parsing changes here. A
    stderr that parses as JSON with `status == "BLOCKED"` is a quality block, surfaced with its own
    `cause`/`nextAction` verbatim -- that prose is actionable and must not be flattened into an
    opaque string. Anything else -- prose, JSON that isn't a BLOCKED envelope, or JSON that only
    half-parses -- is classified `error`, never treated as a pass: the same "a malformed envelope
    is an error, not a pass" discipline `gate_runner.parse_verdict` applies to a gate's verdict.
    """
    text = stderr.decode("utf-8", errors="replace")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        doc = None
    if isinstance(doc, dict) and doc.get("status") == "BLOCKED":
        cause = doc.get("cause") if isinstance(doc.get("cause"), list) else []
        message = f"reference target refused: strict-quality blocked ({len(cause)} failure(s)): " + "; ".join(
            str(item) for item in cause
        )
        next_action = doc.get("nextAction")
        if next_action:
            message += f" -- {next_action}"
        return EmitTargetError(message, classification="quality-block")
    return EmitTargetError(f"reference target exited {returncode}: {text}", classification="error")


def _run_reference_target(target: Target, *, spec_path: Path, workspace: Path) -> int:
    """`--target threejs-ts`: the base fully controls invocation, so this is the one case where
    D6's temp-write+rename is literally implementable rather than a best-effort delete-on-failure
    (see `_run_plugin_target`'s note). Runs `generate_threejs_factory.py` as a subprocess -- proving
    the byte-equality/envelope/bounds/kind parity D8 requires -- against the last completed build
    pass, since action-ready implies the pass loop is done and its TypeScript already exists
    in-process (D2)."""
    from orchestrate_passes import completed_passes, pass_order

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    ids = pass_order(spec)
    completed = completed_passes(spec, ids)
    if not completed:
        raise EmitTargetError(
            "target 'threejs-ts' requires at least one completed build pass; none recorded in the spec"
        )
    pass_id = completed[-1]

    plugin_id = "threejs-ts"
    artifact_rel = "model.ts"
    artifact_path = (workspace / ".img2" / "artifacts" / plugin_id / artifact_rel).resolve()
    timeout = _resolve_timeout(target)
    spec_hash = _sha256_file(spec_path)
    emitter = str(_FORGE_ROOT / "stage3_build" / "generate_threejs_factory.py")

    def invoke(out_path: Path) -> subprocess.CompletedProcess:
        # --force is required: mkstemp below pre-creates out_path, and the emitter refuses an
        # existing --out without it -- omitting the flag makes every reference-target run fail.
        argv = [sys.executable, emitter, str(spec_path), "--out", str(out_path), "--force", "--pass-id", pass_id]
        return run_bounded(argv, cwd=workspace, timeout=timeout, declared_env=[], target_kind=target.kind)

    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=artifact_path.parent, prefix=".model.", suffix=".ts.tmp")
    os.close(handle)
    tmp_path = Path(tmp_name)
    try:
        proc = invoke(tmp_path)
        if proc.returncode != 0:
            raise _classify_reference_failure(proc.returncode, proc.stderr)
        determinism_verified = None
        if target.deterministic:
            cache_path = _determinism_cache_path(workspace, plugin_id)
            key = _determinism_cache_key(None, spec_hash)
            cache: dict[str, bool] = {}
            if cache_path.is_file():
                try:
                    cache = json.loads(cache_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    cache = {}
            if not cache.get(key):
                handle2, tmp2_name = tempfile.mkstemp(dir=artifact_path.parent, prefix=".model2.", suffix=".ts.tmp")
                os.close(handle2)
                tmp2_path = Path(tmp2_name)
                try:
                    proc2 = invoke(tmp2_path)
                    if proc2.returncode != 0 or tmp2_path.read_bytes() != tmp_path.read_bytes():
                        raise EmitTargetError(
                            "reference target declared itself deterministic but two invocations "
                            "against the same (spec, pass_id) produced different bytes"
                        )
                finally:
                    tmp2_path.unlink(missing_ok=True)
                cache[key] = True
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(cache), encoding="utf-8")
                determinism_verified = True
            else:
                determinism_verified = False
        verification = verify_artifact(tmp_path, target.artifact_kind)
        os.replace(tmp_path, artifact_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    provenance = build_provenance(
        target,
        spec_hash=spec_hash,
        harness_version=_harness_version_for_provenance(),
        verification=verification,
        determinism_verified=determinism_verified,
    )
    _write_provenance(artifact_path, provenance)
    record_target_selection(workspace, target, artifact_path=artifact_path)
    print(json.dumps({"artifact": str(artifact_path), "provenance": provenance}, indent=2))
    return 0


def _harness_version_for_provenance() -> str:
    return _harness_version(_img2_home())


# ---------------------------------------------------------------- CLI

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--target", default=None, help="target kind, e.g. threejs-ts; omit for the no-op default")
    parser.add_argument("--plugin", default=None, help="disambiguate when more than one plugin provides --target")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    if args.target is None:
        # Returns before any registry read (D5): a malformed plugins.json must not break this path.
        return run_no_target()

    workspace = args.workspace.expanduser().resolve()
    spec_path = args.spec.expanduser().resolve()
    try:
        return run_target(args.target, plugin=args.plugin, spec_path=spec_path, workspace=workspace)
    except EmitTargetError as exc:
        print(f"emit-target [{exc.classification}]: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
