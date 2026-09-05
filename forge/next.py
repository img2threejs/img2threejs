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
            # Bare invocation must still be runnable guidance, not an argparse usage
            # dead end: the spec is an OUTPUT of the earlier stages, not an input, so
            # say where the pipeline actually starts.
            print("next.py reports the next command for an existing workflow; you gave neither a spec nor --state.")
            print("resume an existing checklist:  python3 forge/next.py --state .img2threejs/state.json")
            print("start a new workflow:           python3 forge/state.py init --reference <image> --profile <generic|character|animated-character|cs2> --spec <path>")
            print("then run:                       python3 forge/next.py --state .img2threejs/state.json")
            return 2
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
    if not isinstance(spec.get("sculptPipeline"), dict):
        # A spec without sculptPipeline is a placeholder, not an authored spec. Reporting a
        # "current pass" here contradicted the blocked banner above it: one line said the
        # pipeline is missing, the next said the pass is blockout. The spec is an OUTPUT of
        # intake/assessment/refine-spec — say that, and point at the actual first step.
        print("spec has no sculptPipeline: this file is a placeholder, not an authored spec.")
        print("next command: python3 forge/state.py init --reference <image> --profile <generic|character|animated-character|cs2> --spec <path>")
        return 2
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
    import sys
    from pathlib import Path

    try:
        sys.path.insert(0, str(next(
            parent / "forge" / "_shared"
            for parent in Path(__file__).resolve().parents
            if (parent / "forge" / "_shared" / "cli_run.py").is_file()
        )))
        from cli_run import run_entry
    except (ImportError, StopIteration):
        # vendored/fixture copies without the forge runtime: run bare, no pipe handling
        def run_entry(main_fn, argv=None):
            return main_fn(sys.argv[1:] if argv is None else argv)

    raise SystemExit(run_entry(main))
