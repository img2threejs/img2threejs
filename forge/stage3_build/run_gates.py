#!/usr/bin/env python3
"""Gate execution (D9, tasks 4.1-4.3, `establish-the-emission-target-contract`).

**Hook point (proposed, per the lead's invitation to propose rather than guess silently):** a new
`FINAL_STEPS` row, `"plugin-gates"`, appended after `"emission-target"` -- not auto-chained inside
`emit_target.py` itself. Every other checklist step in this pipeline (`build-current-pass`,
`render-capture`, ...) is a distinct command the agent invokes one at a time, never auto-triggered
by the step before it; gate execution follows that same discipline. This still satisfies "gates run
after domain steps complete" (a FINAL step runs only once every setup/pass step is done) and "as
part of the emission-target step's post-run" in the sense that it is the very next thing the
checklist asks for, immediately following target selection -- just not chained in-process.

The runner argv is constructed here in Python (task 4.1) -- reusing `_img2_home()`, no `img2`
binary at runtime -- and mirrors `img2.mjs`'s `gateRunnerArgv` (`bin/img2.mjs:807-809`) exactly: the
file-path form, not the module form §13:308 documents (that drift is recorded, not touched here --
it is task 5.5's). A drift-guard test asserts the two never disagree.

The two-clause participation rule (task 4.2, D9): (i) a plugin's gates run when any step it
contributed to THIS workspace's checklist has run -- via `domain.json` or via a selected target
step; (ii) a plugin that contributed no step runs no gates. Clause (i)'s owner lookup
(`domain_owner_plugin`) is derived from the registry layout (`$IMG2_HOME/plugins/<id>/domain.json`)
rather than assumed from a name match -- see its docstring for the earlier, wrong version this
replaced.

Invocation is through the bounded runner from 3.4 (`emit_target.run_bounded`) -- same bounds as
targets, per D9's amendment. The `img2.gate-run` aggregate envelope is parsed per task 4.3:
malformed -> error, never a pass; status/exit disagreement -> error; a blocking fail stops the
WHOLE run (across every involved plugin, not just that plugin's own gates.json), naming the gate
and the plugin.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_FORGE_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_FORGE_ROOT), str(_FORGE_ROOT / "_shared")]

from domains import DomainRegistryError, domain_profile  # noqa: E402
from emit_target import EmitTargetError, assert_action_ready, run_bounded  # noqa: E402
from workflow_state import WorkflowStateError, load_state  # noqa: E402

KIND_RUN = "img2.gate-run"
GATE_TIMEOUT_SECONDS = 300  # same default as gate_runner.py's own DEFAULT_GATE_TIMEOUT_SECONDS


class GateExecutionError(RuntimeError):
    """A gate-execution run was refused or failed. `classification` distinguishes a structural
    problem (malformed envelope, exit disagreement -- `"error"`) from a real blocking gate failure
    (`"blocking-stop"`), so a caller can tell "something is broken" from "a plugin's gate said no"."""

    def __init__(self, message: str, *, classification: str = "error") -> None:
        super().__init__(message)
        self.classification = classification


def _img2_home() -> Path:
    return Path(os.environ.get("IMG2_HOME") or Path.home() / ".img2")


# ---------------------------------------------------------------- 4.1: argv construction + drift guard

def gate_runner_argv(home: Path, plugin_dir: Path) -> list[str]:
    """Mirrors img2.mjs's `gateRunnerArgv` (`bin/img2.mjs:807-809`) exactly. `{workspace}` is left
    as its own argv element for the caller to replace by value -- same discipline as `stepArgv`."""
    return [
        "python3", str(home / "harness" / "img2_core" / "gate_runner.py"),
        "--plugin-dir", str(plugin_dir), "--workspace", "{workspace}",
    ]


# ---------------------------------------------------------------- registry enumeration

def _installed_plugins_with_gates(home: Path) -> list[tuple[str, Path]]:
    registry = home / "plugins.json"
    if not registry.is_file():
        return []
    try:
        rows = json.loads(registry.read_text(encoding="utf-8")).get("plugins") or []
    except (OSError, json.JSONDecodeError) as exc:
        raise GateExecutionError(f"cannot read the plugin registry at {registry}: {exc}") from exc
    out: list[tuple[str, Path]] = []
    for row in rows:
        plugin_id = (row or {}).get("id")
        if not plugin_id:
            continue
        plugin_dir = home / "plugins" / plugin_id
        if (plugin_dir / "gates.json").is_file():
            out.append((plugin_id, plugin_dir))
    return sorted(out, key=lambda pair: pair[0])


# ---------------------------------------------------------------- 4.2: the two-clause participation rule

def domain_step_ids(profile: str, home: Path) -> tuple[str, ...]:
    """Every step id the resolved domain profile contributes (setupSteps + passSteps ids), or ()
    for 'generic'. Propagates `DomainRegistryError` for an unregistered profile -- the caller
    decides what "unknown" means for participation, this does not silently swallow it."""
    domain = domain_profile(profile)
    if domain is None:
        return ()
    setup = tuple(step_id for step_id, _ in (domain.get("setupSteps") or ()))
    passes = tuple(step_id for step_id, _ in (domain.get("passSteps") or ()))
    return setup + passes


def domain_owner_plugin(profile: str, home: Path) -> str | None:
    """The registry plugin id that owns this domain profile, derived from the REGISTRY LAYOUT
    itself rather than assumed. Each installed plugin's domain declaration lives at
    `$IMG2_HOME/plugins/<plugin_id>/domain.json` -- the path carries the registry id, so this reads
    every installed plugin's own `domain.json` (if it has one) and returns the id of whichever
    plugin's declared `"id"` equals `profile`.

    Returns `None` when the profile resolves from an in-repo domain module
    (`forge/_shared/domains/*.py`, e.g. `character` today) instead of an installed plugin -- an
    in-repo domain has no owner plugin, so clause (i) correctly yields "no plugin participated" for
    it rather than raising or guessing. Two plugins declaring the same domain id is already refused
    at the registry level (`domains/__init__.py`'s "declared twice" check, `:82-85`), so this lookup
    is unambiguous by construction -- it does not need to detect or refuse a duplicate itself, only
    the first (and, by that same refusal, only) match is possible.

    This replaces an earlier, WRONG version of this rule that assumed `domain.json`'s own `"id"`
    equals the contributing plugin's registry `"name"` -- true for the one installed domain plugin
    today (cs2: `plugin.json` name `"cs2"`, `domain.json` id `"cs2"`) but unenforced anywhere, so a
    plugin named e.g. `"my-interiors-plugin"` shipping domain id `"interiors"` would have been
    misattributed. Reported as a gap rather than shipped silently; this is the lead's fix.
    """
    registry = home / "plugins.json"
    if not registry.is_file():
        return None
    try:
        rows = json.loads(registry.read_text(encoding="utf-8")).get("plugins") or []
    except (OSError, json.JSONDecodeError):
        return None
    for row in rows:
        plugin_id = (row or {}).get("id")
        if not plugin_id:
            continue
        declaration = home / "plugins" / plugin_id / "domain.json"
        if not declaration.is_file():
            continue
        try:
            entry = json.loads(declaration.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(entry, dict) and entry.get("id") == profile:
            return plugin_id
    return None


def plugin_contributed_a_step(state: dict[str, Any], plugin_id: str, home: Path) -> bool:
    """The two-clause rule (D9, task 4.2).

    Clause (i), via `domain.json`: true iff `plugin_id` is the REGISTRY-DERIVED owner of this
    workspace's resolved domain profile (`domain_owner_plugin`, not an assumed name match) AND at
    least one of that domain's contributed step ids is marked `done` in the checklist.

    Clause (ii), via a selected target step: true iff `state["targetSelection"]["pluginId"]`
    (written by `emit_target.record_target_selection`, added in this same slice) equals
    `plugin_id`.
    """
    profile = state.get("profile")
    if profile and domain_owner_plugin(profile, home) == plugin_id:
        by_id = {entry["id"]: entry for entry in state.get("checklist", [])}
        if any(
            by_id.get(step_id, {}).get("status") == "done"
            for step_id in domain_step_ids(profile, home)
        ):
            return True
    selection = state.get("targetSelection")
    if isinstance(selection, dict) and selection.get("pluginId") == plugin_id:
        return True
    return False


# ---------------------------------------------------------------- 4.3: envelope parsing

def parse_gate_run_envelope(stdout: bytes) -> dict[str, Any]:
    text = stdout.decode("utf-8", errors="replace")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GateExecutionError(f"gate runner produced no parseable {KIND_RUN} envelope: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("kind") != KIND_RUN:
        raise GateExecutionError(f"gate runner stdout is not an {KIND_RUN} envelope")
    if doc.get("version") != 1:
        raise GateExecutionError(f"{KIND_RUN} envelope version {doc.get('version')!r} is not 1")
    if not isinstance(doc.get("results"), list):
        raise GateExecutionError(f"{KIND_RUN} envelope is missing a results array")
    return doc


def _check_exit_agreement(doc: dict[str, Any], returncode: int) -> None:
    if doc.get("error"):
        raise GateExecutionError(f"gate runner reported a config error: {doc['error']}")
    expected = 1 if doc.get("stopped") else 0
    if returncode != expected:
        raise GateExecutionError(
            f"gate runner exit code {returncode} disagrees with its own stopped={doc.get('stopped')!r} "
            f"(expected {expected})"
        )


def run_plugin_gates(
    plugin_id: str, plugin_dir: Path, *, workspace: Path, home: Path, gate_timeout: int | None = None
) -> dict[str, Any]:
    argv = [token.replace("{workspace}", str(workspace)) for token in gate_runner_argv(home, plugin_dir)]
    # `--gate-timeout` is appended AFTER the parity-checked argv, never folded into
    # `gate_runner_argv` itself -- the harness's own `gateRunnerArgv` has no such flag, and the
    # drift-guard test asserts the two match exactly.
    if gate_timeout is not None:
        argv += ["--gate-timeout", str(gate_timeout)]
    # The runner applies its timeout PER GATE, so the outer bound must cover every declared gate --
    # a flat bound killed a legitimately-configured multi-gate run mid-flight, and could never
    # honour a forwarded --gate-timeout larger than itself. The margin is runner overhead.
    per_gate = gate_timeout if gate_timeout is not None else GATE_TIMEOUT_SECONDS
    try:
        rows = json.loads((plugin_dir / "gates.json").read_text(encoding="utf-8"))
        gate_count = len(rows) if isinstance(rows, list) else 1
    except (OSError, json.JSONDecodeError):
        gate_count = 1
    outer_timeout = per_gate * max(gate_count, 1) + 30
    try:
        proc = run_bounded(
            argv, cwd=workspace, timeout=outer_timeout, declared_env=[],
            target_kind=f"gates:{plugin_id}",
        )
    except EmitTargetError as exc:
        raise GateExecutionError(f"plugin {plugin_id!r} gates: {exc}") from exc
    try:
        doc = parse_gate_run_envelope(proc.stdout)
    except GateExecutionError as exc:
        # A runner that never printed an envelope said why on stderr (e.g. an installed harness too
        # old for a forwarded --gate-timeout: "unrecognized arguments"). Flattening that into "no
        # parseable envelope" hides the actual cause from the user.
        stderr_text = proc.stderr.decode("utf-8", errors="replace").strip()
        if stderr_text:
            raise GateExecutionError(f"{exc}; gate runner stderr: {stderr_text[:500]}") from exc
        raise
    _check_exit_agreement(doc, proc.returncode)
    return doc


def run_gates_for_workspace(
    workspace: Path, *, home: Path | None = None, gate_timeout: int | None = None
) -> dict[str, Any]:
    """Refuses the same `action-ready` precondition `emit_target.py` enforces (nothing stops an
    agent invoking this out of checklist order either), then runs every INVOLVED installed
    plugin's gates in registry order, stopping the whole run at the first blocking failure."""
    home = home or _img2_home()
    assert_action_ready(workspace)
    state_path = workspace / ".img2threejs" / "state.json"
    try:
        state = load_state(state_path)
    except WorkflowStateError as exc:
        raise GateExecutionError(str(exc)) from exc

    per_plugin: dict[str, Any] = {}
    for plugin_id, plugin_dir in _installed_plugins_with_gates(home):
        try:
            involved = plugin_contributed_a_step(state, plugin_id, home)
        except DomainRegistryError as exc:
            # Fail loud, not open: gates are the enforcement layer, so "cannot determine
            # participation" must stop the run -- treating it as uninvolved silently skipped a
            # blocking gate whose domain steps had actually run in this workspace.
            raise GateExecutionError(
                f"cannot determine gate participation for plugin {plugin_id!r}: {exc}"
            ) from exc
        if not involved:
            continue
        doc = run_plugin_gates(plugin_id, plugin_dir, workspace=workspace, home=home, gate_timeout=gate_timeout)
        per_plugin[plugin_id] = doc
        if doc.get("stopped"):
            blocking = next(
                (result for result in doc["results"] if result.get("blocking") and result.get("status") != "pass"),
                None,
            )
            gate_name = blocking["gate"] if blocking else "<unknown>"
            raise GateExecutionError(
                f"plugin {plugin_id!r} gate {gate_name!r} blocked the run", classification="blocking-stop"
            )
    return per_plugin


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--gate-timeout", type=int, default=None,
                         help="forwarded to gate_runner.py's --gate-timeout; its own default applies if omitted")
    args = parser.parse_args(argv)
    workspace = args.workspace.expanduser().resolve()
    try:
        results = run_gates_for_workspace(workspace, gate_timeout=args.gate_timeout)
    except (GateExecutionError, EmitTargetError) as exc:
        classification = getattr(exc, "classification", "error")
        print(f"run-gates [{classification}]: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
