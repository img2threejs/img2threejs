from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .model import EXIT_CODES, JsonObject, StateProblem, error_payload
from .service import StateService


def parser() -> argparse.ArgumentParser:
    json_option = argparse.ArgumentParser(add_help=False)
    json_option.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    root = argparse.ArgumentParser(add_help=False)
    root.add_argument("--root", type=Path, default=Path.cwd())
    command = argparse.ArgumentParser(parents=[root, json_option], description="Portable reconstruction state CLI")
    subcommands = command.add_subparsers(dest="command", required=True)
    initialized = subcommands.add_parser("init", parents=[json_option])
    initialized.add_argument("--item-id", required=True)
    initialized.add_argument("--primary-reference", required=True)
    initialized.add_argument("--from-artifacts", action="store_true")
    listed = subcommands.add_parser("list", parents=[json_option])
    listed.add_argument("--status")
    for name in ("read", "validate", "resume"):
        child = subcommands.add_parser(name, parents=[json_option])
        child.add_argument("item_id")
    for name in ("claim", "release"):
        child = subcommands.add_parser(name, parents=[json_option])
        child.add_argument("item_id")
        child.add_argument("--actor", required=True)
    heartbeat = subcommands.add_parser("heartbeat", parents=[json_option])
    heartbeat.add_argument("item_id")
    heartbeat.add_argument("--actor", required=True)
    heartbeat.add_argument("--lease-token", required=True)
    acknowledged = subcommands.add_parser("acknowledge", parents=[json_option])
    acknowledged.add_argument("item_id")
    acknowledged.add_argument("revision", type=int)
    acknowledged.add_argument("--actor", required=True)
    preflight = subcommands.add_parser("preflight", parents=[json_option])
    preflight.add_argument("item_id")
    preflight.add_argument("action")
    preflight.add_argument("--actor", default="anonymous")
    committed = subcommands.add_parser("commit", parents=[json_option])
    committed.add_argument("item_id")
    committed.add_argument("--operation-id", required=True)
    committed.add_argument("--actor", required=True)
    committed.add_argument("--next-action", required=True)
    handoff = subcommands.add_parser("handoff", parents=[json_option])
    handoff.add_argument("item_id")
    handoff.add_argument("--actor", required=True)
    handoff.add_argument("--recipient", required=True)
    handoff.add_argument("--summary", required=True)
    recover = subcommands.add_parser("recover", parents=[json_option])
    recover.add_argument("item_id")
    recover.add_argument("--operation-id", required=True)
    recover.add_argument("--actor", required=True)
    return command


def run(arguments: argparse.Namespace) -> JsonObject:
    service = StateService(arguments.root)
    match arguments.command:
        case "init":
            return service.initialize(arguments.item_id, arguments.primary_reference, arguments.from_artifacts)
        case "list":
            return service.list_items()
        case "read":
            return service.read(arguments.item_id)
        case "validate":
            return service.validate(arguments.item_id)
        case "resume":
            return service.resume(arguments.item_id)
        case "claim":
            return service.claim(arguments.item_id, arguments.actor)
        case "heartbeat":
            return service.heartbeat(arguments.item_id, arguments.actor, arguments.lease_token)
        case "acknowledge":
            return service.acknowledge(arguments.item_id, arguments.revision, arguments.actor)
        case "preflight":
            return service.preflight(arguments.item_id, arguments.action, arguments.actor)
        case "commit":
            return service.commit(arguments.item_id, arguments.operation_id, arguments.actor, arguments.next_action)
        case "handoff":
            return service.handoff(arguments.item_id, arguments.actor, arguments.recipient, arguments.summary)
        case "recover":
            return service.recover(arguments.item_id, arguments.operation_id, arguments.actor)
        case "release":
            return service.release(arguments.item_id, arguments.actor)
        case unmatched:
            raise StateProblem("USAGE", f"unsupported command: {unmatched}")


def main(argv: list[str]) -> int:
    arguments = parser().parse_args(argv)
    try:
        result = run(arguments)
    except StateProblem as problem:
        print(json.dumps(error_payload(problem), ensure_ascii=False) if arguments.json else str(problem))
        return EXIT_CODES[problem.code]
    print(json.dumps({"ok": True, **result}, ensure_ascii=False) if arguments.json else result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
