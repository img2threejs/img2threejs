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
(priority 8) fires purely on ``len(history) >= max_iter`` and cannot be bypassed
by any other state — not even a monotonically-improving-but-never-reaching-target
loop. This is the single most important property of this module; do not weaken it.
"""

from __future__ import annotations

import argparse
import json
import math
import sys


def decide(history, target_fidelity=0.85, max_iter=6, min_delta=0.02):
    """Decide whether to stop the correction loop and what to do next.

    Args:
        history: list of per-iteration dicts in chronological order, each
             ``{"fidelity": float in [0,1], "defectTags": list[str], "reverted": bool}``,
            with optional Divine Eye ``hardGateFailures: list[str]`` and
            ``pendingReview: bool`` routing metadata.
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
    _validate_config(target_fidelity, max_iter, min_delta)
    _validate_history(history)

    # 1. EMPTY — nothing has happened yet.
    if not history:
        return {
            "stop": False,
            "action": "continue-iterating",
            "reason": "no iterations yet",
        }

    last = history[-1]
    prev = history[-2] if len(history) >= 2 else None

    hard_gates, action, verdict, pending_review, fidelity = _routing_state(last)

    # 2. HARD GATE — a Divine Eye hard failure needs code correction.
    if hard_gates:
        return {
            "stop": True,
            "action": "refine-code",
            "reason": f"hard gate failure: {hard_gates[0]}",
        }

    # 3. PENDING REVIEW — preserve the Divine Eye evaluator's non-continue routing.
    if pending_review:
        route = action if action in ("refine-code", "refine-spec") else "request-input"
        return {
            "stop": True,
            "action": route,
            "reason": f"pending Divine Eye review: {action or verdict}",
        }

    # 4. SUCCESS — target met and no open defects.
    if fidelity >= target_fidelity and not last["defectTags"]:
        return {
            "stop": True,
            "action": "continue",
            "reason": "fidelity target met, no open defects",
        }

    # 5. REPEATED_DEFECT — a defect tag survived two consecutive iterations.
    if prev is not None:
        shared = set(last["defectTags"]) & set(prev["defectTags"])
        if shared:
            tag = sorted(shared)[0]
            return {
                "stop": True,
                "action": "refine-spec",
                "reason": f"same defect survived 2 consecutive fixes: {tag}",
            }

    # 6. OSCILLATION — two trailing reverted iterations.
    reverts = 0
    for entry in reversed(history):
        if not entry.get("reverted"):
            break
        reverts += 1
    oscillating = reverts >= 2
    if oscillating:
        return {
            "stop": True,
            "action": "refine-spec",
            "reason": "oscillating/thrashing",
        }

    # 7. PLATEAU — progress stalled below target.
    if (
        prev is not None
        and (fidelity - _routing_state(prev)[4]) < min_delta
        and fidelity < target_fidelity
    ):
        return {
            "stop": True,
            "action": "request-input",
            "reason": "progress plateaued below target (Δ<min_delta)",
        }

    # 8. HARD_CEILING — non-bypassable termination guarantee.
    if len(history) >= max_iter:
        return {
            "stop": True,
            "action": "request-input",
            "reason": "hit MAX_ITER ceiling",
        }

    # 9. Otherwise — keep going.
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
    if not _is_finite_number(spent_tokens) or spent_tokens < 0:
        raise ValueError("spent_tokens must be a finite non-negative number")
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
        raise ValueError("budget must be a non-negative integer")
    return spent_tokens >= budget


def _validate_history(history):
    if not isinstance(history, list):
        raise ValueError("history must be a JSON list")
    for index, entry in enumerate(history):
        if not isinstance(entry, dict):
            raise ValueError(f"history[{index}] must be an object")
        fidelity = entry.get("fidelity")
        if isinstance(fidelity, bool) or not isinstance(fidelity, int | float) or not math.isfinite(fidelity) or not 0.0 <= fidelity <= 1.0:
            raise ValueError(f"history[{index}].fidelity must be a finite number in [0, 1]")
        _validate_tags(entry.get("defectTags"), f"history[{index}].defectTags")
        _routing_state(entry, index)
        if not isinstance(entry.get("reverted"), bool):
            raise ValueError(f"history[{index}].reverted must be a boolean")
    return history


def _validate_tags(tags, field):
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise ValueError(f"{field} must be a list of strings")


def _routing_state(entry, index=None):
    prefix = f"history[{index}]" if index is not None else "history entry"
    if "pendingReview" in entry and not isinstance(entry["pendingReview"], bool):
        raise ValueError(f"{prefix}.pendingReview must be a boolean")
    provenance = entry.get("divineEye")
    if provenance is not None and not isinstance(provenance, dict):
        raise ValueError(f"{prefix}.divineEye must be an object")
    source = provenance if provenance is not None else entry
    fidelity = source.get("fidelity") if provenance is not None else entry.get("fidelity")
    if isinstance(fidelity, bool) or not isinstance(fidelity, int | float) or not math.isfinite(fidelity) or not 0.0 <= fidelity <= 1.0:
        raise ValueError(f"{prefix}.divineEye.fidelity must be a finite number in [0, 1]")
    if provenance is not None and entry["fidelity"] != fidelity:
        raise ValueError(f"{prefix}.fidelity conflicts with divineEye provenance")
    hard_gates = source.get("hardGateFailures", [])
    _validate_tags(hard_gates, f"{prefix}.hardGateFailures")
    action = source.get("action") if provenance is not None else entry.get("divineEyeAction")
    verdict = source.get("verdict") if provenance is not None else entry.get("divineEyeVerdict")
    if action is not None and not isinstance(action, str):
        raise ValueError(f"{prefix}.divineEyeAction must be a string")
    if verdict is not None and not isinstance(verdict, str):
        raise ValueError(f"{prefix}.divineEyeVerdict must be a string")
    pending_review = bool(entry.get("pendingReview", False)) or action not in (None, "continue") or verdict not in (None, "pass") or bool(hard_gates)
    if provenance is not None:
        expected = {"hardGateFailures": hard_gates, "divineEyeAction": action, "divineEyeVerdict": verdict, "pendingReview": pending_review}
        for key, value in expected.items():
            if key in entry and entry[key] != value:
                raise ValueError(f"{prefix}.{key} conflicts with divineEye provenance")
    return hard_gates, action, verdict, pending_review, fidelity


def _validate_config(target_fidelity, max_iter, min_delta):
    if not _is_finite_number(target_fidelity) or not 0.0 <= target_fidelity <= 1.0:
        raise ValueError("target_fidelity must be a finite number in [0, 1]")
    if isinstance(max_iter, bool) or not isinstance(max_iter, int) or max_iter <= 0:
        raise ValueError("max_iter must be a positive integer")
    if not _is_finite_number(min_delta) or min_delta < 0.0:
        raise ValueError("min_delta must be finite and non-negative")


def _is_finite_number(value):
    return not isinstance(value, bool) and isinstance(value, int | float) and math.isfinite(value)


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
