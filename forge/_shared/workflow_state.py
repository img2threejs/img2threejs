from __future__ import annotations

import json
import os
import shlex
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Final


SCHEMA_VERSION: Final = 1
STEP_STATUSES: Final = {"pending", "done", "skipped"}
REFINE_ACTIONS: Final = {"refine-spec", "refine-code"}


SETUP_STEPS: Final = (
    ("image-analysis", "Read grimoire/intake/image_analysis.md and analyze {reference}"),
    (
        "reference-suitability",
        "Read grimoire/intake/validation_rubric.md and record a pass, conditional, or reject verdict for {reference}",
    ),
    ("reference-admission", "python3 forge/stage1_intake/check_reference_admission.py {reference}"),
    ("local-spec-search", "Run the local evidence search before authoring the assessment"),
    ("pre-spec-assessment", "python3 forge/stage2_spec/new_pre_spec_assessment.py \"<name>\" --image {reference} --out assessment.json"),
    ("detail-inventory", "python3 forge/stage1_intake/build_detail_inventory.py {reference} --mode grid-3x3 --out-dir detail-inventory --out di.json"),
    (
        "projection-route",
        "Record whether projection is required; if required run solve_camera_pose.py, delight_albedo.py, and bake_projected_texture.py, otherwise skip with a reason",
    ),
    ("spec-authoring", "python3 forge/stage2_spec/new_sculpt_spec.py \"<name>\" --image {reference} --assessment assessment.json --out object-sculpt-spec.json"),
    (
        "material-evidence",
        "python3 forge/stage1_intake/material_region_analysis.py --manifest material-regions.json --out-dir material-evidence --out material-analysis.json"
        " (single-crop route: analyze_texture.py + extract_pbr_evidence.py per verified crop; otherwise skip with a reason)",
    ),
    ("material-spec-wiring", "python3 forge/stage2_spec/apply_material_analysis.py {spec} material-analysis.json --in-place"),
    ("strict-validation", "python3 forge/stage2_spec/validate_sculpt_spec.py {spec} --strict-quality"),
)

CHARACTER_STEPS: Final = (
    (
        "character-contract-read",
        "Read grimoire/character/reconstruction.md and grimoire/character/likeness_maximization.md completely",
    ),
    (
        "character-landmarks",
        "python3 forge/stage1_intake/extract_landmarks.py {reference} --out anatomy.json --overlay landmarks.png",
    ),
)

CS2_STEPS: Final = (
    ("cs2-contract-read", "Read grimoire/intake/cs2_intake_contract.md completely"),
    ("cs2-authoritative-classification", "Obtain an authoritative CS2 family/subtype classification record"),
    ("cs2-manifest", "python3 forge/stage1_intake/cs2_manifest.py {reference} --classification classification.json --out cs2-intake.json"),
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

CS2_PASS_STEPS: Final = (
    (
        "cs2-review",
        "python3 forge/stage4_review/cs2_review.py --manifest cs2-intake.json --metrics cs2-review-inputs.json --scene forge/tests/fixtures/knife_review_scene.json --out cs2-review.json",
    ),
)

FINAL_STEPS: Final = (
    ("part-coverage", "python3 forge/stage4_review/check_part_coverage.py --spec {spec} --manifest parts.json"),
    ("action-ready", "Verify explodable/clickable hierarchy, pivots, sockets, and root.userData.sculptRuntime"),
)


class WorkflowStateError(ValueError):
    pass


def _has_external_versioned_ledger(state: dict[str, Any]) -> bool:
    """Return whether a versioned rebuild owns its loop ledger outside the generic spec history.

    V1 states derive loop counts from the spec reviewHistory. A versioned rebuild can carry a
    separate, compacted ledger (for example state-v2.json) whose review history intentionally does
    not have the same cursor or pass semantics as the original spec. The local state remains the
    authority in that case; next.py must validate and report it without rewriting it from V1 data.
    """
    active = state.get("activeVersion")
    baseline = state.get("baselineVersion")
    return (
        isinstance(active, str)
        and bool(active.strip())
        and isinstance(baseline, str)
        and bool(baseline.strip())
        and active != baseline
    )


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
    if profile not in {"generic", "cs2", "character"}:
        raise WorkflowStateError("profile must be generic, cs2, or character")
    if max_per_pass < 1 or max_total < 1 or max_per_pass > max_total:
        raise WorkflowStateError("loop limits require 1 <= max-per-pass <= max-total")
    setup = [_step(*item, scope="setup") for item in SETUP_STEPS]
    insertion = next(index for index, item in enumerate(setup) if item["id"] == "local-spec-search")
    if profile == "cs2":
        setup[insertion:insertion] = [_step(*item, scope="setup") for item in CS2_STEPS]
    elif profile == "character":
        setup[insertion:insertion] = [_step(*item, scope="setup") for item in CHARACTER_STEPS]
    pass_steps = list(PASS_STEPS)
    if profile == "cs2":
        review_index = next(index for index, item in enumerate(pass_steps) if item[0] == "ai-review-recorded")
        pass_steps[review_index:review_index] = list(CS2_PASS_STEPS)
    state = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "active",
        "profile": profile,
        "currentStep": setup[0]["id"],
        "currentPass": "",
        "checklist": setup
        + [_step(*item, scope="pass") for item in pass_steps]
        + [_step(*item, scope="final") for item in FINAL_STEPS],
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
    if state.get("profile") not in {"generic", "cs2", "character"}:
        raise WorkflowStateError("state profile is invalid")
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
        if entry.get("scope") not in {"setup", "pass", "final"}:
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
            "command": f"python3 forge/next.py --state {state.get('statePath') or '.img2threejs/state.json'} {{spec}}",
        }
    final_pending = _pending(_entries(state, "final"))
    return final_pending[0] if final_pending else None


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
    if _has_external_versioned_ledger(state):
        # A versioned reconstruction may deliberately use a separate review/loop ledger and a
        # compact resume contract. Validate/report the state, but never derive its counters or
        # current step from the baseline spec's reviewHistory.
        return
    # A user-authorized loop reset starts a new correction epoch without
    # deleting the prior review ledger. The old ledger remains authoritative
    # evidence; only reviews at/after loopEpochStart count toward the new cap.
    if state.get("loopEpochStart") is not None and state.get("status") == "stopped":
        state["status"] = "active"
        state["stopReason"] = ""
    set_current_pass(state, current_pass)
    history = spec.get("reviewHistory", [])
    if not isinstance(history, list):
        history = []
    epoch_start = int(state.get("loopEpochStart", 0) or 0)
    epoch_start = max(0, min(epoch_start, len(history)))
    review_cursor = max(epoch_start, min(int(state.get("reviewCursor", 0)), len(history)))
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
    for entry in history[epoch_start:]:
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
    visible_scopes = {"setup", "final"}
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

# --- compaction resume snapshot -------------------------------------------------
# Ported from the ~/.claude and ~/.codex host copies, which carried it while this repo
# never did: `git log -S "def cmd_compact"` returns 0 commits here, so it was developed
# outside git rather than deliberately removed. Kept additive -- org's own
# _has_external_versioned_ledger is absent from those copies, so neither file is a
# superset and overwriting either direction would drop real work.
COMPACTION_VERSION: Final = 1

def resume_snapshot_path(state_path: Path, state: dict[str, Any] | None = None) -> Path:
    """Return the small, human-facing resume snapshot for a state file.

    Versioned states conventionally live at `.img2threejs/state-v2.json` and
    therefore write to `.img2threejs/v2/compaction/resume.md`. A state may
    override this with `compaction.resumePath`; relative overrides are
    resolved from the current project directory, like the rest of the forge
    command paths.
    """
    configured = (state or {}).get("compaction", {}).get("resumePath")
    if isinstance(configured, str) and configured.strip():
        configured_path = Path(configured).expanduser()
        return configured_path if configured_path.is_absolute() else Path.cwd() / configured_path

    target = state_path.expanduser().resolve()
    stem = target.stem
    if stem.startswith("state-") and len(stem) > len("state-"):
        return target.parent / stem.removeprefix("state-") / "compaction" / "resume.md"
    return target.parent / "compaction" / "resume.md"

def _resume_value(value: Any, *, fallback: str = "unknown") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)

def build_resume_snapshot(state_path: Path, state: dict[str, Any]) -> str:
    """Build a bounded markdown checkpoint without embedding state history."""
    validate_state(state)
    payload = status_payload(state)
    loops = payload["loop"]
    artifacts = state.get("artifacts", {})
    last_loop = state.get("lastCompletedLoop") or {}
    locked = state.get("lockedFamilies") or []
    phase_gates = state.get("phaseGates") or {}
    foundation_epoch = state.get("foundationEpoch") or {}

    lines = [
        "# img2threejs compact resume checkpoint",
        "",
        "This file is generated from the authoritative state. Read it for context; use `state.py` and",
        "`next.py` as the authority for ordering and gate decisions. Do not reconstruct progress from chat memory.",
        "",
        f"- state: `{state_path}`",
        f"- compaction version: `{COMPACTION_VERSION}`",
        f"- status: `{_resume_value(payload['status'])}`",
        f"- current step: `{_resume_value(payload['currentStep'])}`",
        f"- current pass: `{_resume_value(payload['currentPass'], fallback='none')}`",
        f"- workflow version: `{_resume_value(state.get('workflowVersion'))}`",
        f"- phase: `{_resume_value(state.get('currentPhase'))}`",
        f"- active family: `{_resume_value(state.get('activeFamily'))}`",
        f"- baseline pass: `{_resume_value(state.get('baselinePass'))}`",
        f"- loop budget: `{loops['passCount']}/{loops['maxPerPass']} current, {loops['totalCount']}/{loops['maxTotal']} total`",
        f"- locked families: `{', '.join(map(str, locked)) if locked else 'none recorded'}`",
        "",
        "## Next command",
        "",
        f"```sh\n{payload['nextCommand'] or 'No command: state is stopped or complete.'}\n```",
        "",
        "## Gate summary",
        "",
        f"- acceptance target: `{_resume_value(state.get('acceptanceTarget'))}`",
        f"- macro gate: `{_resume_value(phase_gates.get('macroBlockout'))}`",
        f"- assembly gate: `{_resume_value(phase_gates.get('componentAssembly'))}`",
        f"- projection gate: `{_resume_value(phase_gates.get('projection'))}`",
        f"- stop reason: `{_resume_value(payload.get('stopReason'), fallback='none')}`",
        "",
        "## Latest checkpoint",
        "",
        f"- decision: `{_resume_value(last_loop.get('decision'))}`",
        f"- scope: `{_resume_value(last_loop.get('scope'))}`",
        f"- next family: `{_resume_value(last_loop.get('nextFamily'))}`",
        f"- evidence: `{_resume_value(last_loop.get('evidence'))}`",
        "",
    ]
    if isinstance(foundation_epoch, dict) and foundation_epoch:
        lines.extend(
            [
                "## Current foundation epoch",
                "",
                f"- id: `{_resume_value(foundation_epoch.get('id'))}`",
                f"- status: `{_resume_value(foundation_epoch.get('status'))}`",
                f"- baseline: `{_resume_value(foundation_epoch.get('baselinePass'))}`",
                f"- measurement contract: `{_resume_value(foundation_epoch.get('measurementContract'))}`",
                f"- region matrix: `{_resume_value(foundation_epoch.get('regionMatrix'))}`",
                f"- camera packet: `{_resume_value(foundation_epoch.get('cameraRuntime'))}`",
                f"- geometry changed: `{_resume_value(foundation_epoch.get('geometryChanged'))}`",
                "",
            ]
        )
    lines.extend(
        [
        "## Read-on-demand evidence",
        "",
        ]
    )
    evidence_keys = (
        "spec",
        "latestReview",
        "latestRender",
        "latestDiagnostics",
        "latestMultiAngle",
        "latestRuntimeManifest",
        "latestFeatureReview",
        "latestReviewContract",
        "latestCs2Review",
        "latestPartCoverage",
        "latestNotebookAudit",
    )
    for key in evidence_keys:
        if key in artifacts:
            lines.append(f"- {key}: `{_resume_value(artifacts[key])}`")
    lines.extend(
        [
            "",
            "## Pending mandatory steps",
            "",
        ]
    )
    pending = payload.get("pending", [])
    if pending:
        lines.extend(f"- `{step_id}`" for step_id in pending[:20])
        if len(pending) > 20:
            lines.append(f"- ... {len(pending) - 20} more; query `forge/state.py status --json` when needed")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Resume rules",
            "",
            "- Load this snapshot first; do not paste the full state or pass history into context.",
            "- Run `forge/state.py status --state <active-state.json>` and `forge/next.py --state <active-state.json> <spec>` before edits.",
            "- Read full history only for a retrospective, regression investigation, or the active family evidence review.",
            "- Keep the state file authoritative and regenerate this snapshot after every state save.",
        ]
    )
    return "\n".join(lines) + "\n"

def write_resume_snapshot(
    state_path: Path,
    state: dict[str, Any],
    *,
    output: Path | None = None,
) -> Path:
    """Write the bounded resume snapshot atomically and return its path."""
    target = (output.expanduser() if output else resume_snapshot_path(state_path, state)).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(build_resume_snapshot(state_path, state))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target
