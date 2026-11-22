from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "_shared"))
sys.path.insert(0, str(ROOT / "stage3_build"))
from status_banner import emit_status
from orchestrate_passes import current_pass, pass_acceptance, pass_order, completed_passes
from workflow_state import (
    WorkflowStateError,
    load_state,
    save_state,
    status_payload,
    sync_from_spec,
)


def emit_local_state(payload: dict) -> None:
    loop = payload["loop"]
    print(
        f"LOCAL_STATE status={payload['status']} step={payload['currentStep']} "
        f"pass={payload['currentPass'] or 'none'} "
        f"loop={loop['passCount']}/{loop['maxPerPass']} "
        f"total={loop['totalCount']}/{loop['maxTotal']}"
    )
    if payload["stopReason"]:
        print(f"STOP: {payload['stopReason']}")
    elif payload["nextCommand"]:
        print(f"next command: {payload['nextCommand']}")
    print("pending mandatory steps:")
    for step_id in payload["pending"]:
        print(f"- {step_id}")


_NATIVE_WORKFLOW = "native-character-v1"
_NATIVE_PLAN = "native-plan.json"
_NATIVE_ROWS = {"native-plan-author", "native-build", "native-capture-deliver", "native-publish",
                "native-terminal-gate"}
_NATIVE_STATE = re.compile(r"\.img2threejs/objects/[A-Za-z0-9][A-Za-z0-9_-]{0,159}/rev-[1-9][0-9]*/state\.json\Z")


def native_binding(state_path: Path, local_state: dict) -> tuple[bool, bool]:
    """Return (native claimed by ANY source, every source agrees it is native).

    Native-ness is never read from the child marker alone: a child-state edit can delete
    or null it. The parent's descriptor (written by route_parent at creation), the child's
    plan artifact and its sibling native-plan.json are independent signals. Any signal
    makes the child native; a marker that is missing, null or altered then fails closed
    instead of downgrading to the sculpt path.
    """
    child = local_state.get("routeChild")
    child = child if type(child) is dict else {}
    claimed = ["workflow" in child, local_state.get("artifacts", {}).get("spec") == _NATIVE_PLAN,
               any(row.get("id") in _NATIVE_ROWS for row in local_state.get("checklist", ()))]
    agreed = [child.get("workflow") == _NATIVE_WORKFLOW, claimed[1], claimed[2]]
    parts = state_path.resolve().parts
    if len(parts) >= 7 and _NATIVE_STATE.fullmatch("/".join(parts[-5:])) is not None:
        relative = "/".join(parts[-5:])
        claimed.append((state_path.resolve().parent / _NATIVE_PLAN).exists())
        parent_path = Path(*parts[:-5]) / ".img2threejs/state.json"
        if parent_path.exists():
            try:
                parent = json.loads(parent_path.read_text(encoding="utf-8"))
                rows = [row for row in parent["objects"] if row.get("statePath") == relative]
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                return any(claimed), False
            if len(rows) != 1:
                return any(claimed), False
            claimed.append("workflow" in rows[0])
            agreed.append(rows[0].get("workflow") == _NATIVE_WORKFLOW)
    return any(claimed), all(agreed)


def _run_known(workspace: Path, run_id: str) -> bool:
    """Read-only: the lease is this run's and the journal already started it."""
    try:
        lease = json.loads((workspace / ".img2threejs/route/lease.json").read_text(encoding="utf-8"))
        journal = (workspace / ".img2threejs/route/journal.jsonl").read_text(encoding="utf-8")
        return type(lease) is dict and lease.get("runId") == run_id and any(
            entry.get("event") == "run-started" and entry.get("data", {}).get("runId") == run_id
            for entry in map(json.loads, journal.splitlines()))
    except (OSError, ValueError, AttributeError):
        return False


def native_main(args, local_state) -> int:
    """Resolve an authenticated native child's pass; never sculpt synchronization.

    Fails closed (exit 2) without a pinned host policy carrying native callbacks and
    live authorization. Only currentPass and its derived step fields are updated:
    loop counts and budgets are never recomputed or reset here.
    """
    if args.spec is not None:
        print("state error: native child does not accept a positional spec", file=sys.stderr)
        return 2
    if not native_binding(args.state, local_state)[1]:
        print("native error: native-selection-invalid", file=sys.stderr)
        return 2
    pending = {row["id"]: row["status"] for row in local_state["checklist"]}
    if pending.get("native-plan-author") == "pending":
        # Pre-plan phase: report the checklist only. No plan exists to validate, nothing
        # is written, and currentPass stays untouched.
        payload = status_payload(local_state)
        emit_local_state(payload)
        return 3 if payload["status"] == "stopped" else 0
    if args.policy_module is None or args.policy_sha256 is None:
        print("native error: native-authority-unavailable", file=sys.stderr)
        return 2
    try:
        parts = args.state.resolve(strict=True).parts
        if (local_state["routeChild"].get("workflow") != _NATIVE_WORKFLOW or len(parts) < 7
                or parts[-5:-3] != (".img2threejs", "objects")
                or _NATIVE_STATE.fullmatch("/".join(parts[-5:])) is None):
            print("native error: child-state-mismatch", file=sys.stderr)
            return 2
        workspace = Path(*parts[:-5])
        state_path = "/".join(parts[-5:])
        if not (workspace / ".img2threejs/route/lease.json").is_file():
            print("native error: lease-lost", file=sys.stderr)
            return 2
        # Lazy: only a native child pays for, or depends on, the decision stack.
        sys.path.insert(0, str(ROOT.parent))
        from forge.decision import load_policy
        from decision_control import DecisionController, DecisionError
        from workflow_decisions import WorkflowDecisions
        run_id = local_state["routeChild"]["runId"]
        # Host approval and run identity are proven BEFORE any write: constructing the
        # controller would otherwise append run-started to the journal for an unseen run.
        policy = load_policy(args.policy_module, args.policy_sha256, workspace=workspace,
                             state_path=state_path, excluded_roots=[Path(root) for root in args.excluded_root])
        if not _run_known(workspace, run_id):
            print("native error: lease-lost", file=sys.stderr)
            return 2
        controller = DecisionController(workspace, run_id)
        workflow = WorkflowDecisions(controller, state_path, policy,
                                     consent=lambda: None, availability=lambda: {"status": "absent"})
        resolved = workflow.resolve_native_pass()
    except (DecisionError, WorkflowStateError, OSError, ValueError, KeyError) as error:
        print(f"native error: {getattr(error, 'reason_code', error)}", file=sys.stderr)
        return 2
    payload = status_payload(resolved)
    emit_local_state(payload)
    return 3 if payload["status"] == "stopped" else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Report the exact next sculpt pipeline command")
    parser.add_argument("spec", type=Path, nargs="?")
    parser.add_argument("--state", type=Path, help="local checklist state created by forge/state.py init")
    parser.add_argument("--policy-module", type=Path,
                        help="native child only: canonical host-approved policy module")
    parser.add_argument("--policy-sha256", help="native child only: exact policy module sha256")
    parser.add_argument("--excluded-root", action="append", default=[],
                        help="native child only: provider/harness tree the policy module must not live in")
    args = parser.parse_args(argv)
    local_state = None
    spec_path = args.spec
    if args.state:
        try:
            local_state = load_state(args.state)
        except WorkflowStateError as error:
            print(f"state error: {error}", file=sys.stderr)
            return 2
        route_child = local_state.get("routeChild")
        if native_binding(args.state, local_state)[0]:
            return native_main(args, local_state)
        if local_state.get("routeDecision"):
            if spec_path is not None:
                print("state error: routed parent does not accept a positional spec", file=sys.stderr)
                return 2
        elif spec_path is None:
            stored_spec = local_state.get("artifacts", {}).get("spec")
            spec_path = Path(stored_spec) if isinstance(stored_spec, str) and stored_spec else None
        else:
            stored_spec = local_state.get("artifacts", {}).get("spec")
            if isinstance(stored_spec, str) and stored_spec:
                if Path(stored_spec).expanduser().resolve() != spec_path.expanduser().resolve():
                    print(
                        f"state error: positional spec {spec_path} does not match stored spec {stored_spec}",
                        file=sys.stderr,
                    )
                    return 2
            else:
                local_state["artifacts"]["spec"] = str(spec_path)

    if spec_path is not None and local_state is not None and not spec_path.expanduser().is_file():
        # `state.py init --spec <path>` records where the spec WILL be written, so between init
        # and spec-authoring that path does not exist yet. That is the normal pre-spec state, not
        # an error: fall back to the local checklist report so the mandatory gate stays runnable
        # for the whole pre-spec phase instead of hard-failing on its own documented first step.
        spec_path = None

    if spec_path is None:
        if local_state is None:
            parser.error("spec is required unless --state points to an initialized pre-spec workflow")
        payload = status_payload(local_state)
        if (args.state and local_state.get("routeDecision")
                and isinstance(local_state.get("objects"), list)
                and isinstance(payload["currentStep"], str)
                and payload["currentStep"].startswith("object:")):
            object_id = payload["currentStep"][len("object:"):]
            matches = [row for row in local_state["objects"]
                       if isinstance(row, dict) and row.get("objectId") == object_id]
            if len(matches) == 1:
                row = matches[0]
                revision = row.get("revision")
                expected = f".img2threejs/objects/{object_id}/rev-{revision}/state.json"
                if (re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", object_id)
                        and isinstance(revision, int) and not isinstance(revision, bool) and revision > 0
                        and row.get("statePath") == expected
                        and payload["nextCommand"] == expected):
                    child = args.state.resolve().parent.parent / expected
                    payload["nextCommand"] = (
                        f"cd {shlex.quote(str(child.parent))} && "
                        f"{shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 'next.py'))} "
                        "--state state.json")
        emit_local_state(payload)
        return 3 if payload["status"] == "stopped" else 0

    try:
        spec = json.loads(spec_path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"spec error: {error}", file=sys.stderr)
        return 2
    if not isinstance(spec, dict):
        raise ValueError("spec must be an object")
    ids = pass_order(spec)
    completed = completed_passes(spec, ids)
    current = current_pass(ids, completed)

    if local_state is not None:
        sync_from_spec(local_state, spec, current)
        save_state(args.state, local_state)
        payload = status_payload(local_state)
        emit_local_state(payload)
        if payload["status"] == "stopped":
            return 3
        return 0

    emit_status(spec)
    if current == "complete":
        print("pipeline: complete")
        return 0
    acceptance = pass_acceptance(spec, current)
    command = f"python3 forge/stage3_build/orchestrate_passes.py check {spec_path} --pass-id {current}"
    print(f"current pass: {current}")
    print(f"next command: {command}")
    print("unmet acceptance criteria:")
    for item in acceptance or ["pass-specific evidence and a reviewHistory entry with action=continue"]:
        print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
