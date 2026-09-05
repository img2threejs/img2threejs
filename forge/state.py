#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "_shared"))

from domains import DomainRegistryError, registered_domains  # noqa: E402
from workflow_state import (  # noqa: E402
    WorkflowStateError,
    load_state,
    mark_steps,
    new_state,
    save_state,
    status_payload,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal local checklist state for img2threejs")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init")
    init.add_argument("--state", type=Path, default=Path(".img2threejs/state.json"))
    init.add_argument("--reference", required=True)
    # Choices come from the registry, so installing a domain makes its profile available here without
    # editing the base CLI. new_state() re-checks and names the available set on a bad value.
    # Guarded: registered_domains() at argparse-construction time is exactly the shape targets.py
    # documents avoiding -- a broken registry (e.g. a duplicate domain id during a half-applied
    # extraction) must degrade THIS parser to generic-only choices, not kill `status`/`mark` for
    # every profile on the machine. new_state() then reports the real registry error.
    try:
        profile_choices = ("generic", *sorted(registered_domains()))
    except DomainRegistryError:
        profile_choices = ("generic",)
    init.add_argument("--profile", choices=profile_choices, default="generic")
    init.add_argument("--spec", default="")
    init.add_argument("--max-per-pass", type=int, default=3)
    init.add_argument("--max-total", type=int, default=6)

    status = commands.add_parser("status")
    status.add_argument("--state", type=Path, default=Path(".img2threejs/state.json"))
    status.add_argument("--json", action="store_true")

    mark = commands.add_parser("mark")
    mark.add_argument("step", nargs="+")
    mark.add_argument("--state", type=Path, default=Path(".img2threejs/state.json"))
    mark.add_argument("--status", choices=("done", "skipped", "pending"), default="done")
    mark.add_argument("--evidence", action="append", default=[])
    mark.add_argument("--reason", default="")

    return parser


def print_status(state: dict, *, as_json: bool = False) -> None:
    payload = status_payload(state)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
        return
    loop = payload["loop"]
    print(
        f"STATE status={payload['status']} step={payload['currentStep']} "
        f"pass={payload['currentPass'] or 'none'} "
        f"loop={loop['passCount']}/{loop['maxPerPass']} total={loop['totalCount']}/{loop['maxTotal']}"
    )
    if payload["stopReason"]:
        print(f"STOP: {payload['stopReason']}")
    elif payload["nextCommand"]:
        print(f"next command: {payload['nextCommand']}")
    print("pending mandatory steps:")
    for step_id in payload["pending"]:
        print(f"- {step_id}")


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            if args.state.expanduser().exists():
                raise WorkflowStateError(f"refusing to overwrite existing state: {args.state}")
            state = new_state(
                args.reference,
                profile=args.profile,
                spec=args.spec,
                max_per_pass=args.max_per_pass,
                max_total=args.max_total,
            )
            save_state(args.state, state)
            print_status(state)
            return 0
        state = load_state(args.state)
        if args.command == "status":
            print_status(state, as_json=args.json)
        elif args.command == "mark":
            mark_steps(state, args.step, status=args.status, evidence=args.evidence, reason=args.reason)
            save_state(args.state, state)
            print_status(state)
        return 3 if state.get("status") == "stopped" else 0
    except (OSError, WorkflowStateError) as error:
        print(f"state error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
