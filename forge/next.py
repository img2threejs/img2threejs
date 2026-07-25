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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Report the exact next sculpt pipeline command")
    parser.add_argument("spec", type=Path)
    args = parser.parse_args(argv)
    spec = json.loads(args.spec.expanduser().read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError("spec must be an object")
    ids = pass_order(spec)
    completed = completed_passes(spec, ids)
    current = current_pass(ids, completed)
    emit_status(spec)
    if current == "complete":
        print("pipeline: complete")
        return 0
    acceptance = pass_acceptance(spec, current)
    command = f"python3 forge/stage3_build/orchestrate_passes.py check {args.spec} --pass-id {current}"
    print(f"current pass: {current}")
    print(f"next command: {command}")
    print("unmet acceptance criteria:")
    for item in acceptance or ["pass-specific evidence and a reviewHistory entry with action=continue"]:
        print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
