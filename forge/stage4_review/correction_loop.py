#!/usr/bin/env python3
"""Bounded stop-policy state machine for the Eye-driven correction loop.

Plan 1.3 Phase 4, §3.6 — bounded correction-loop stop policy.

This is a PURE-LOGIC module: no images, no repo imports, stdlib only. It decides,
after each iteration of an expensive VLM-driven correction loop, whether to stop
and what action the caller should take next.

TERMINATION GUARANTEE
---------------------
A caller that loops::

    while not decide(history)["stop"]:
        history.append(one_more_iteration())

can NEVER run more than ``max_iter`` iterations. The HARD_CEILING condition
(priority 6) fires purely on ``len(history) >= max_iter`` and cannot be bypassed
by any other state — not even a monotonically-improving-but-never-reaching-target
loop. This is the single most important property of this module; do not weaken it.
"""

from __future__ import annotations

import argparse
import json
import sys


def decide(history, target_fidelity=0.85, max_iter=6, min_delta=0.02):
    """Decide whether to stop the correction loop and what to do next.

    Args:
        history: list of per-iteration dicts in chronological order, each
            ``{"fidelity": float in [0,1], "defectTags": list[str], "reverted": bool}``.
            ``reverted`` means that iteration's correction lowered the score and
            was auto-reverted.
        target_fidelity: fidelity at/above which the model is considered good enough.
        max_iter: non-bypassable ceiling on iterations — guarantees termination.
        min_delta: minimum per-iteration fidelity gain below which progress has
            plateaued.

    Returns:
        dict ``{"stop": bool, "action": str, "reason": str}``.

    Stop conditions are evaluated in strict priority order; the first match wins.
    """
    # 1. EMPTY — nothing has happened yet.
    if not history:
        return {
            "stop": False,
            "action": "continue-iterating",
            "reason": "no iterations yet",
        }

    last = history[-1]
    prev = history[-2] if len(history) >= 2 else None

    # 2. SUCCESS — target met and no open defects.
    if last["fidelity"] >= target_fidelity and not last["defectTags"]:
        return {
            "stop": True,
            "action": "continue",
            "reason": "fidelity target met, no open defects",
        }

    # 3. REPEATED_DEFECT — a defect tag survived two consecutive iterations.
    if prev is not None:
        shared = set(last["defectTags"]) & set(prev["defectTags"])
        if shared:
            tag = sorted(shared)[0]
            return {
                "stop": True,
                "action": "refine-spec",
                "reason": f"same defect survived 2 consecutive fixes: {tag}",
            }

    # 4. OSCILLATION — repeated reverts, or a direction flip over the last 3 scores.
    reverts = sum(1 for h in history if h.get("reverted"))
    oscillating = reverts >= 2
    if not oscillating and len(history) >= 3:
        f0, f1, f2 = (h["fidelity"] for h in history[-3:])
        d1 = f1 - f0
        d2 = f2 - f1
        # strictly down-then-up or up-then-down (both deltas non-zero, opposite signs)
        if (d1 < 0 and d2 > 0) or (d1 > 0 and d2 < 0):
            oscillating = True
    if oscillating:
        return {
            "stop": True,
            "action": "refine-spec",
            "reason": "oscillating/thrashing",
        }

    # 5. PLATEAU — progress stalled below target.
    if (
        prev is not None
        and (last["fidelity"] - prev["fidelity"]) < min_delta
        and last["fidelity"] < target_fidelity
    ):
        return {
            "stop": True,
            "action": "request-input",
            "reason": "progress plateaued below target (Δ<min_delta)",
        }

    # 6. HARD_CEILING — non-bypassable termination guarantee.
    if len(history) >= max_iter:
        return {
            "stop": True,
            "action": "request-input",
            "reason": "hit MAX_ITER ceiling",
        }

    # 7. Otherwise — keep going.
    return {
        "stop": False,
        "action": "continue-iterating",
        "reason": "still improving toward target",
    }


def budget_exceeded(spent_tokens, budget):
    """§3.6 budget circuit-breaker.

    Returns True when the token budget is spent. The caller uses this to
    HALT-and-ask-the-user; it never silently continues.
    """
    return spent_tokens >= budget


def main(argv):
    parser = argparse.ArgumentParser(
        description="Bounded correction-loop stop policy (§3.6)."
    )
    parser.add_argument("--history", required=True, help="path to JSON list of iterations")
    parser.add_argument("--target", type=float, default=0.85, help="target fidelity")
    parser.add_argument("--max-iter", type=int, default=6, help="hard iteration ceiling")
    parser.add_argument("--min-delta", type=float, default=0.02, help="plateau threshold")
    parser.add_argument("--json", action="store_true", help="emit decision as JSON")

    try:
        args = parser.parse_args(argv)
        with open(args.history, "r", encoding="utf-8") as fh:
            history = json.load(fh)
        if not isinstance(history, list):
            raise ValueError("history JSON must be a list")

        decision = decide(
            history,
            target_fidelity=args.target,
            max_iter=args.max_iter,
            min_delta=args.min_delta,
        )

        if args.json:
            print(json.dumps(decision))
        else:
            print(
                f"stop={decision['stop']} action={decision['action']} "
                f"reason={decision['reason']}"
            )
        return 0 if not decision["stop"] else 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary, surface any failure
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
