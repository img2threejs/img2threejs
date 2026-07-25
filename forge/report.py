from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "_shared"))
sys.path.insert(0, str(ROOT / "stage3_build"))
from status_banner import emit_status
from orchestrate_passes import completed_passes, current_pass, pass_order


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate a reconstruction report from the pass ledger")
    parser.add_argument("spec", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    spec = json.loads(args.spec.expanduser().read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError("spec must be an object")
    ids = pass_order(spec)
    completed = completed_passes(spec, ids)
    current = current_pass(ids, completed)
    missing = ids[len(completed):]
    opening = "PIPELINE COMPLETE through optimization-pass." if not missing else "INCOMPLETE: uncleared passes: " + ", ".join(missing) + "."
    emit_status(spec)
    report = "\n".join([opening, f"Target: {spec.get('targetName', '(unnamed)')}", f"Completed passes: {', '.join(completed) or '(none)'}", f"Current pass: {current}", "Review history entries: " + str(len(spec.get("reviewHistory", [])))]) + "\n"
    if args.out:
        args.out.expanduser().write_text(report, encoding="utf-8")
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
