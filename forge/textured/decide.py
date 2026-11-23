#!/usr/bin/env python3
"""The `decide` row: SAME accepts; DIFFERENT regenerates with a changed input (up to the generation
cap); otherwise (similar) the loop continues while generations remain, and the best generation is
accepted when the cap is reached.

The verdict is read from the generation's likeness.json, never from chat. Rules, in order:

  1. verdict `same`                      -> accept this generation
  2. verdict `different`                 -> regenerate (cap not reached) / accept best (cap reached)
  3. verdict `similar`                   -> accept when the mean score reaches --accept-mean (default 2.4)
                                            and no defect is flagged; otherwise regenerate while generations remain
  4. cap reached                         -> accept the best generation (highest mean; Jev scores outrank
                                            model-answered ones; a `pick.json` winner among tied/close
                                            candidates breaks the tie)

Deciders are never mixed up: a `model:<id>` likeness is journalled as such and cannot be compared with
a Jev one -- the best generation is chosen among likenesses of one decider kind (Jev if any exists).
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _HERE]
sys.path.append(_HERE)

import argparse  # noqa: E402
from pathlib import Path  # noqa: E402

import _common as C  # noqa: E402

sys.path.append(os.path.join(os.path.dirname(_HERE), "_shared"))
from workflow_state import (  # noqa: E402
    WorkflowStateError, load_state, save_state, status_payload, textured_apply_decision,
)

DEFAULT_ACCEPT_MEAN = 2.4
TIE_MARGIN = 0.15  # Jev run-to-run noise measured on the vesper run


def load_likeness(workspace, generation):
    path = C.gen_dir(workspace, generation) / "likeness.json"
    return C.read_json(path) if path.is_file() else None


def best_generation(workspace, generations, pick=None):
    """(generation, basis) among those with a likeness; Jev-decided rows outrank model-decided ones."""
    rows = []
    for generation in generations:
        record = load_likeness(workspace, generation)
        if record is None:
            continue
        rows.append((generation, record["summary"]["meanScore"] or 0.0, record["presentedAsJev"], record["summary"]))
    if not rows:
        return None, "no-likeness"
    pool = [row for row in rows if row[2]] or rows
    top = max(row[1] for row in pool)
    close = [row for row in pool if top - row[1] <= TIE_MARGIN]
    if len(close) > 1 and pick and pick.get("winner") in {row[0] for row in close}:
        return pick["winner"], "jev-pick"
    close.sort(key=lambda row: (-row[1], row[3]["flaggedDefects"].__len__(), row[0]))
    return close[0][0], ("jev-mean" if pool[0][2] else "model-mean")


def decide(workspace, state, accept_mean=DEFAULT_ACCEPT_MEAN):
    block = state["textured"]
    generation = block["generation"]
    record = load_likeness(workspace, generation)
    if record is None:
        raise C.TexturedError(f"gen-{generation}/likeness.json is missing; run the likeness step first")
    summary = record["summary"]
    verdict = summary["overall"]["verdict"]
    flagged = summary["flaggedDefects"]
    mean = summary["meanScore"]
    remaining = generation < block["maxGenerations"]
    evidence = [C.workspace_relative(workspace, C.gen_dir(workspace, generation) / "likeness.json")]
    decider = record["decider"]
    if verdict == "same":
        return "accept", generation, f"verdict same by {decider}", evidence
    if verdict == "similar" and mean is not None and mean >= accept_mean and not flagged:
        return "accept", generation, f"verdict similar, mean {mean} >= {accept_mean}, no flagged defect ({decider})", evidence
    if remaining:
        why = f"verdict {verdict}, mean {mean}" + (f", flagged {flagged}" if flagged else "")
        return "regenerate", generation, f"{why} ({decider}); change model, scenario, prompt or input before generation {generation + 1}", evidence
    pick_path = C.textured_root(workspace) / "pick.json"
    pick = C.read_json(pick_path) if pick_path.is_file() else None
    best, basis = best_generation(workspace, range(1, generation + 1), pick)
    best = best or generation
    evidence.append(C.workspace_relative(workspace, C.gen_dir(workspace, best) / "likeness.json"))
    return "accept", best, f"generation cap reached; best generation {best} by {basis}", evidence


def build_parser():
    parser = argparse.ArgumentParser(description="Record the textured accept/regenerate decision")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--state", default=None)
    parser.add_argument("--accept-mean", type=float, default=DEFAULT_ACCEPT_MEAN)
    parser.add_argument("--dry-run", action="store_true", help="print the decision without recording it")
    return parser


def main(argv):
    args = build_parser().parse_args(argv)
    workspace = C.resolve_workspace(args.workspace)
    state_path = Path(args.state) if args.state else workspace / ".img2threejs" / "state.json"
    try:
        state = load_state(state_path)
        outcome, chosen, reason, evidence = decide(workspace, state, args.accept_mean)
        print(f"decide: {outcome} (generation {chosen}): {reason}")
        if args.dry_run:
            return 0
        textured_apply_decision(state, outcome=outcome, generation=state["textured"]["generation"], evidence=evidence,
                                reason=reason, accepted_generation=chosen if outcome == "accept" else None)
        save_state(state_path, state)
    except WorkflowStateError as error:
        raise C.TexturedError(str(error)) from None
    payload = status_payload(state)
    print(f"next: {payload['nextCommand'] or payload['status']}")
    return 0


if __name__ == "__main__":
    C.run_cli(main)
