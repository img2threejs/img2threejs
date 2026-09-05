from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parent))

from domains import DomainRegistryError, domain_profile  # noqa: E402


SCHEMA_VERSION: Final = 1
STEP_STATUSES: Final = {"pending", "done", "skipped"}
REFINE_ACTIONS: Final = {"refine-spec", "refine-code"}


SETUP_STEPS: Final = (
    ("image-analysis", "Read grimoire/intake/image_analysis.md and analyze {reference}"),
    (
        "reference-suitability",
        "Read grimoire/intake/validation_rubric.md and record a pass, conditional, or reject verdict for {reference}",
    ),
    ("reference-admission",
     "python3 forge/stage1_intake/check_reference_admission.py {reference}"
     " --out admission.json --probe-out probe.json"),
    ("local-spec-search", "Run the local evidence search before authoring the assessment"),
    ("pre-spec-assessment",
     "python3 forge/stage2_spec/new_pre_spec_assessment.py \"<name>\" --image {reference}"
     " --domain {profile} --out assessment.json"),
    ("detail-inventory", "python3 forge/stage1_intake/build_detail_inventory.py {reference} --mode grid-3x3 --out-dir detail-inventory --out di.json"),
    (
        "projection-route",
        "Record whether projection is required; if required run solve_camera_pose.py, delight_albedo.py, and bake_projected_texture.py, otherwise skip with a reason",
    ),
    ("spec-authoring",
     "python3 forge/stage2_spec/new_sculpt_spec.py \"<name>\" --image {reference} --assessment assessment.json"
     " --augmentation spec-augmentation.json --domain {profile} --out object-sculpt-spec.json"),
    (
        "material-evidence",
        "python3 forge/stage1_intake/material_region_analysis.py --manifest material-regions.json --out-dir material-evidence --out material-analysis.json"
        " (single-crop route: analyze_texture.py + extract_pbr_evidence.py per verified crop; otherwise skip with a reason)",
    ),
    ("material-spec-wiring", "python3 forge/stage2_spec/apply_material_analysis.py {spec} material-analysis.json --in-place"),
    ("strict-validation", "python3 forge/stage2_spec/validate_sculpt_spec.py {spec} --strict-quality"),
)

PASS_STEPS: Final = (
    ("build-current-pass", "python3 forge/stage3_build/generate_threejs_factory.py {spec} --out src/createObjectModel.ts --pass-id {pass_id}"),
    ("render-capture", "Render {pass_id} and capture the fixed review view plus meaningful orbit views"),
    ("review-contract-read", "Read grimoire/review/gates_reference.md and grimoire/review/self_correction.md completely"),
    ("tier1-diagnostics", "python3 forge/stage4_review/diagnose_render.py --reference {reference} --render <shot> --spec {spec} --pass-id {pass_id} --in-place"),
    ("multi-angle-review", "python3 forge/stage4_review/diagnose_render_multi_angle.py --reference <fixed-shot> --orbit <orbit-shot> --orbit <orbit-shot>"),
    ("pass-gate-check", "python3 forge/stage3_build/orchestrate_passes.py check {spec} --pass-id {pass_id}"),
    ("ai-review-recorded", "Create the comparison sheet, inspect it with agent vision, and append exactly one review action"),
    ("pipeline-sync", "python3 forge/stage3_build/orchestrate_passes.py sync {spec} --in-place"),
)

FINAL_STEPS: Final = (
    ("part-coverage", "python3 forge/stage4_review/check_part_coverage.py --spec {spec} --manifest parts.json"),
    ("action-ready", "Verify explodable/clickable hierarchy, pivots, sockets, and root.userData.sculptRuntime"),
    # D4/task 3.8: base-owned, appended unconditionally -- no plugin ever splices this in (there is
    # no `finalSteps` key in domains/__init__.py's _ALLOWED, deliberately: a plugin must never be
    # able to change what runs at the terminal phase behind the user's back). An explicit
    # `--target <kind>` is the only thing that ever populates this step: on a successful run,
    # `emit_target.record_target_selection` marks it `done` and records which plugin ran (or
    # `None` for the reference target) in the new `targetSelection` state field, which slice 4's
    # gate-participation rule reads. With no `--target`, emit_target.py's no-op path (D2) never
    # touches this file at all -- correcting an earlier claim in this comment that the no-op path
    # marks the row "done"; it does not, and this row is left `pending` on that path (a stated,
    # not-yet-resolved rough edge: forge/next.py will keep naming it as the next required step
    # even though there is nothing further to do when no target will ever be selected). Round-3 H6:
    # workspaces created before this change do not carry this row in their persisted checklist
    # (new_state() materialises FINAL_STEPS once, at init) -- not breakage, since nothing crashes and
    # SCHEMA_VERSION stays 1, but emit_target.py's own action-ready precondition still enforces
    # terminal-only there directly, independent of whether this row exists in a given state file.
    (
        "emission-target",
        "python3 forge/stage3_build/emit_target.py --spec {spec} --workspace . "
        "[--target <kind> [--plugin <id>]]",
    ),
    # task 4.2's proposed hook point (see run_gates.py's module docstring for the full rationale):
    # a distinct checklist step, not auto-chained inside emit_target.py, following the same
    # discipline every other FINAL_STEPS/PASS_STEPS row already has -- the agent invokes it, it is
    # not triggered by the step before it. Harmless with no plugin involved: the two-clause rule
    # naturally yields zero gates to run, and this exits 0 having done nothing.
    ("plugin-gates", "python3 forge/stage3_build/run_gates.py --workspace ."),
)

class WorkflowStateError(ValueError):
    pass


def _anchor_index(rows: list[Any], anchor: str, profile: str, key) -> int:
    for index, row in enumerate(rows):
        if key(row) == anchor:
            return index
    # An unknown anchor is a contribution the base cannot place. Failing loud beats appending at the
    # end, which would put a domain's setup step after the steps that depend on it.
    raise WorkflowStateError(f"profile {profile!r} anchors a step before unknown base step {anchor!r}")


def _splice(rows: list[dict[str, Any]], steps, anchor: str | None, profile: str, *, scope: str) -> list[dict[str, Any]]:
    if not steps:
        return rows
    at = _anchor_index(rows, anchor, profile, lambda r: r["id"])
    return rows[:at] + [_step(*item, scope=scope) for item in steps] + rows[at:]


def _splice_raw(rows: list[Any], steps, anchor: str | None, profile: str) -> list[Any]:
    if not steps:
        return rows
    at = _anchor_index(rows, anchor, profile, lambda r: r[0])
    return rows[:at] + list(steps) + rows[at:]


def _step(step_id: str, command: str, *, scope: str) -> dict[str, Any]:
    return {
        "id": step_id,
        "scope": scope,
        "status": "pending",
        "evidence": [],
        "reason": "",
        "command": command,
    }


def new_state(
    reference: str,
    *,
    profile: str = "generic",
    spec: str = "",
    max_per_pass: int = 3,
    max_total: int = 6,
) -> dict[str, Any]:
    try:
        domain = domain_profile(profile)
    except DomainRegistryError as exc:
        raise WorkflowStateError(str(exc)) from exc
    if max_per_pass < 1 or max_total < 1 or max_per_pass > max_total:
        raise WorkflowStateError("loop limits require 1 <= max-per-pass <= max-total")

    setup = [_step(sid, cmd.replace("{profile}", profile), scope="setup") for sid, cmd in SETUP_STEPS]
    pass_steps = list(PASS_STEPS)
    if domain is not None:
        setup = _splice(setup, domain.get("setupSteps"), domain.get("setupAnchorBefore"), profile, scope="setup")
        pass_steps = _splice_raw(pass_steps, domain.get("passSteps"), domain.get("passAnchorBefore"), profile)
    state = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "active",
        "profile": profile,
        "currentStep": setup[0]["id"],
        "currentPass": "",
        "checklist": setup
        + [_step(*item, scope="pass") for item in pass_steps]
        + [_step(*item, scope="final") for item in FINAL_STEPS]
        + [_step(*item, scope="rig") for item in ((domain.get("rigSteps") or ()) if domain else ())],
        "loops": {
            "perPass": {},
            "total": 0,
            "maxPerPass": max_per_pass,
            "maxTotal": max_total,
        },
        "artifacts": {"reference": reference, "spec": spec},
        "passHistory": [],
        "reviewCursor": 0,
        "iterationAction": "initial",
        "stopReason": "",
    }
    recompute(state)
    return state


def validate_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise WorkflowStateError("state must be a JSON object")
    if state.get("schemaVersion") != SCHEMA_VERSION:
        raise WorkflowStateError(f"unsupported state schemaVersion: {state.get('schemaVersion')!r}")
    # A state file written before its domain moved out must fail by naming the missing provider,
    # never by silently downgrading the run to generic.
    try:
        domain_profile(state.get("profile"))
    except DomainRegistryError as exc:
        raise WorkflowStateError(str(exc)) from exc
    checklist = state.get("checklist")
    if not isinstance(checklist, list) or not checklist:
        raise WorkflowStateError("state checklist must be a non-empty list")
    seen: set[str] = set()
    for entry in checklist:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise WorkflowStateError("every checklist entry needs a string id")
        if entry["id"] in seen:
            raise WorkflowStateError(f"duplicate checklist step: {entry['id']}")
        seen.add(entry["id"])
        if entry.get("scope") not in {"setup", "pass", "final", "rig"}:
            raise WorkflowStateError(f"invalid checklist scope for {entry['id']}")
        if entry.get("status") not in STEP_STATUSES:
            raise WorkflowStateError(f"invalid checklist status for {entry['id']}")
    loops = state.get("loops")
    if not isinstance(loops, dict):
        raise WorkflowStateError("state loops must be an object")
    max_per_pass = loops.get("maxPerPass")
    max_total = loops.get("maxTotal")
    if not isinstance(max_per_pass, int) or not isinstance(max_total, int):
        raise WorkflowStateError("loop limits must be integers")
    if max_per_pass < 1 or max_total < 1 or max_per_pass > max_total:
        raise WorkflowStateError("loop limits require 1 <= maxPerPass <= maxTotal")
    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts.get("reference"):
        raise WorkflowStateError("state artifacts.reference is required")
    review_cursor = state.get("reviewCursor", 0)
    if not isinstance(review_cursor, int) or review_cursor < 0:
        raise WorkflowStateError("state reviewCursor must be a non-negative integer")
    return state


def load_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise WorkflowStateError(f"state file does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise WorkflowStateError(f"state file is not valid JSON: {path}") from error
    return validate_state(state)


def save_state(path: Path, state: dict[str, Any]) -> None:
    validate_state(state)
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _entries(state: dict[str, Any], scope: str) -> list[dict[str, Any]]:
    return [entry for entry in state["checklist"] if entry["scope"] == scope]


def _pending(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in entries if entry["status"] == "pending"]


def _format_command(state: dict[str, Any], entry: dict[str, Any]) -> str:
    artifacts = state.get("artifacts", {})
    command = str(entry["command"])
    if entry["id"] == "build-current-pass":
        action = state.get("iterationAction")
        if action == "refine-code":
            return "Refine the existing src/createObjectModel.ts from the latest review; do not regenerate it"
        if action in {"new-pass", "refine-spec"}:
            command += " --force"
    return command.format(
        reference=shlex.quote(str(artifacts.get("reference") or "<reference>")),
        spec=shlex.quote(str(artifacts.get("spec") or "<spec>")),
        pass_id=shlex.quote(str(state.get("currentPass") or "<pass>")),
    )


def next_entry(state: dict[str, Any]) -> dict[str, Any] | None:
    setup_pending = _pending(_entries(state, "setup"))
    if setup_pending:
        return setup_pending[0]
    if state.get("currentPass") != "complete":
        pass_pending = _pending(_entries(state, "pass"))
        if pass_pending:
            return pass_pending[0]
        return {
            "id": "await-pass-transition",
            "scope": "pass",
            "status": "pending",
            "command": "python3 forge/next.py --state .img2threejs/state.json {spec}",
        }
    final_pending = _pending(_entries(state, "final"))
    if final_pending:
        return final_pending[0]
    # Stage R runs LAST, after the static model is complete and its coverage gates have passed:
    # rigging is additive to a finished mesh, and binding a model whose parts are still moving
    # would freeze geometry that has not settled. Dispatching it here is what makes the rig steps
    # reachable at all -- a checklist entry no dispatcher returns is a step `next.py` never asks
    # for, which is the same defect as a gate nothing invokes.
    rig_pending = _pending(_entries(state, "rig"))
    return rig_pending[0] if rig_pending else None


def recompute(state: dict[str, Any]) -> None:
    entry = next_entry(state)
    if state.get("status") == "stopped":
        state["currentStep"] = "stopped"
    elif entry is None:
        state["status"] = "complete"
        state["currentStep"] = "complete"
        state["stopReason"] = ""
    else:
        state["status"] = "active"
        state["currentStep"] = entry["id"]
        state["stopReason"] = ""


def mark_steps(
    state: dict[str, Any],
    step_ids: list[str],
    *,
    status: str,
    evidence: list[str] | None = None,
    reason: str = "",
) -> None:
    if state.get("status") == "stopped":
        raise WorkflowStateError("state is hard-stopped; do not mark more work complete")
    if status not in {"done", "skipped", "pending"}:
        raise WorkflowStateError("mark status must be done, skipped, or pending")
    if status == "done" and not evidence:
        raise WorkflowStateError("completing a mandatory step requires at least one --evidence value")
    if status == "skipped" and not reason.strip():
        raise WorkflowStateError("skipping a mandatory step requires --reason")
    by_id = {entry["id"]: entry for entry in state["checklist"]}
    missing = [step_id for step_id in step_ids if step_id not in by_id]
    if missing:
        raise WorkflowStateError(f"unknown checklist step(s): {', '.join(missing)}")
    if status in {"done", "skipped"}:
        for step_id in step_ids:
            expected = next_entry(state)
            if expected is None or expected["id"] != step_id:
                expected_id = expected["id"] if expected else "complete"
                raise WorkflowStateError(
                    f"out-of-order checklist update: expected {expected_id}, received {step_id}"
                )
            entry = by_id[step_id]
            entry["status"] = status
            entry["evidence"] = list(evidence or [])
            entry["reason"] = reason.strip()
            recompute(state)
        return
    for step_id in step_ids:
        entry = by_id[step_id]
        entry["status"] = status
        entry["evidence"] = list(evidence or [])
        entry["reason"] = reason.strip()
    recompute(state)


def set_current_pass(state: dict[str, Any], pass_id: str) -> None:
    normalized = pass_id.strip()
    if not normalized:
        raise WorkflowStateError("current pass cannot be empty")
    previous = str(state.get("currentPass") or "")
    if previous and previous != normalized:
        state.setdefault("passHistory", []).append(
            {
                "passId": previous,
                "checklist": deepcopy(_entries(state, "pass")),
            }
        )
    if previous != normalized:
        for entry in _entries(state, "pass"):
            entry["status"] = "pending"
            entry["evidence"] = []
            entry["reason"] = ""
        state["iterationAction"] = "new-pass" if previous else "initial"
    state["currentPass"] = normalized
    recompute(state)


def sync_from_spec(state: dict[str, Any], spec: dict[str, Any], current_pass: str) -> None:
    set_current_pass(state, current_pass)
    history = spec.get("reviewHistory", [])
    if not isinstance(history, list):
        history = []
    review_cursor = min(int(state.get("reviewCursor", 0)), len(history))
    new_reviews = history[review_cursor:]
    refinements = [
        entry
        for entry in new_reviews
        if isinstance(entry, dict)
        and entry.get("passId") == current_pass
        and entry.get("action") in REFINE_ACTIONS
    ]
    if current_pass != "complete" and refinements:
        state.setdefault("passHistory", []).append(
            {
                "passId": current_pass,
                "iteration": "refine",
                "checklist": deepcopy(_entries(state, "pass")),
            }
        )
        for checklist_entry in _entries(state, "pass"):
            checklist_entry["status"] = "pending"
            checklist_entry["evidence"] = []
            checklist_entry["reason"] = ""
        state["iterationAction"] = refinements[-1]["action"]
    state["reviewCursor"] = len(history)
    per_pass: dict[str, int] = {}
    total = 0
    for entry in history:
        if not isinstance(entry, dict) or entry.get("action") not in REFINE_ACTIONS:
            continue
        pass_id = str(entry.get("passId") or "unknown")
        per_pass[pass_id] = per_pass.get(pass_id, 0) + 1
        total += 1
    loops = state["loops"]
    loops["perPass"] = per_pass
    loops["total"] = total
    pass_count = per_pass.get(current_pass, 0)
    if pass_count >= loops["maxPerPass"]:
        state["status"] = "stopped"
        state["currentStep"] = "stopped"
        state["stopReason"] = f"max-correction-loops-reached:{current_pass}:{pass_count}/{loops['maxPerPass']}"
    elif total >= loops["maxTotal"]:
        state["status"] = "stopped"
        state["currentStep"] = "stopped"
        state["stopReason"] = f"max-total-correction-loops-reached:{total}/{loops['maxTotal']}"
    else:
        recompute(state)


def status_payload(state: dict[str, Any]) -> dict[str, Any]:
    entry = next_entry(state)
    current_pass = str(state.get("currentPass") or "")
    loops = state["loops"]
    visible_scopes = {"setup", "final", "rig"}
    if current_pass != "complete":
        visible_scopes.add("pass")
    return {
        "status": state["status"],
        "currentStep": state["currentStep"],
        "currentPass": current_pass,
        "loop": {
            "passCount": loops.get("perPass", {}).get(current_pass, 0),
            "maxPerPass": loops["maxPerPass"],
            "totalCount": loops["total"],
            "maxTotal": loops["maxTotal"],
        },
        "nextCommand": None if state["status"] != "active" or entry is None else _format_command(state, entry),
        "stopReason": state.get("stopReason") or None,
        "pending": [
            entry["id"]
            for entry in state["checklist"]
            if entry["scope"] in visible_scopes and entry["status"] == "pending"
        ],
    }
