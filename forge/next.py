from __future__ import annotations

import argparse
import json
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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Report the exact next sculpt pipeline command")
    parser.add_argument("spec", type=Path, nargs="?")
    parser.add_argument("--state", type=Path, help="local checklist state created by forge/state.py init")
    args = parser.parse_args(argv)
    local_state = None
    spec_path = args.spec
    if args.state:
        try:
            local_state = load_state(args.state)
        except WorkflowStateError as error:
            print(f"state error: {error}", file=sys.stderr)
            return 2
        if spec_path is None:
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
