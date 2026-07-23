#!/usr/bin/env python3
"""VLM gating layer (Plan 1.3 §3.4) — the model as gated, calibrated, cross-checked lubricant.

The VLM is the ONLY place a model opinion enters the Divine Eye, and it is deliberately
subordinate to the deterministic ensemble (divine_eye.py). This module encodes the RULES
around the model so a wrong/overconfident model verdict cannot mislead the generator:

  1. The VLM NEVER runs on a render that failed a deterministic HARD gate. A geometrically
     broken render is rejected by math; asking the model about it only invites a confident-
     but-wrong "looks fine". (Reliability property, not a token optimization.)
  2. Multi-sample self-consistency voting: sample the VLM N times, aggregate (median), and
     treat high spread across samples as an uncertainty signal → probe, never a coin-flip.
  3. Post-hoc score calibration: raw VLM scores are remapped through a monotonic calibration
     fitted on the §5 corpus (true temperature scaling is impossible without logits). Default
     is identity until calibration data exists.
  4. Evidence cross-check: the VLM's claimed object class is checked against the deterministic
     geometric descriptor; a contradiction flags the verdict uncertain (the model's semantics
     must not override measured geometry).
  5. Hard vs soft: the VLM can NEVER grant past a hard geometric failure (it doesn't even run
     then), but it CAN rescue a SOFT near-threshold reject when its criteria + evidence agree.

The actual VLM call is INJECTED as `vlm_sampler` (a callable), so this whole layer is
deterministic + testable with a stub — no real model, no token, in tests. Pure stdlib.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

CRITERIA = ("objectness", "semantic", "structural", "specular")
DEFAULT_CRITERIA_MIN = 0.80
VARIANCE_SPREAD_MAX = 0.20  # spread across samples above this ⇒ uncertain → probe


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else 0.5 * (s[m - 1] + s[m])


def aggregate_samples(samples: list[dict]) -> dict[str, Any]:
    """Median per criterion + the worst (max) per-criterion spread across samples."""
    agg: dict[str, float] = {}
    max_spread = 0.0
    for crit in CRITERIA:
        vals = [float(s.get(crit, 0.0)) for s in samples]
        agg[crit] = _median(vals)
        if vals:
            max_spread = max(max_spread, max(vals) - min(vals))
    # majority claimed class (ties → first seen)
    classes = [s.get("claimedClass") for s in samples if s.get("claimedClass")]
    claimed = None
    if classes:
        counts: dict[str, int] = {}
        for c in classes:
            counts[c] = counts.get(c, 0) + 1
        claimed = max(counts, key=lambda k: counts[k])
    return {"criteria": agg, "maxSpread": max_spread, "claimedClass": claimed}


def calibrate(score: float, calibration: list[list[float]] | None = None) -> float:
    """Monotonic piecewise-linear remap fitted on the §5 corpus. `calibration` is a sorted
    list of [raw, calibrated] control points. Default (None) is identity."""
    if not calibration:
        return score
    pts = sorted(calibration, key=lambda p: p[0])
    if score <= pts[0][0]:
        return pts[0][1]
    if score >= pts[-1][0]:
        return pts[-1][1]
    for i in range(1, len(pts)):
        x0, y0 = pts[i - 1]
        x1, y1 = pts[i]
        if x0 <= score <= x1:
            t = 0.0 if x1 == x0 else (score - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return score


def evidence_consistent(vlm_class: str | None, geometry_class: str | None) -> bool:
    """True if the VLM's claimed class does not contradict the deterministic geometry
    descriptor. Unknown geometry (None) cannot contradict → consistent."""
    if not vlm_class or not geometry_class:
        return True
    return vlm_class.strip().lower() == geometry_class.strip().lower()


def gate(
    eye_result: dict[str, Any],
    vlm_sampler: Callable[[int], dict] | None,
    n_samples: int = 3,
    criteria_min: float = DEFAULT_CRITERIA_MIN,
    calibration: list[list[float]] | None = None,
    geometry_class: str | None = None,
) -> dict[str, Any]:
    """Decide the final verdict given the deterministic Eye result + an (injected) VLM sampler."""
    hard_failures = eye_result.get("hardGateFailures") or []
    if hard_failures:
        # RULE 1: never consult the VLM on a hard-gate failure.
        return {
            "verdict": "reject",
            "action": "refine-code",
            "ranVlm": False,
            "reason": f"deterministic hard gate failed ({'; '.join(hard_failures)}); VLM not consulted",
        }

    if vlm_sampler is None:
        # No VLM available: fall back to the deterministic verdict unchanged.
        return {
            "verdict": eye_result.get("verdict", "reject"),
            "action": eye_result.get("action", "refine-code"),
            "ranVlm": False,
            "reason": "no VLM sampler provided; deterministic verdict stands",
        }

    samples = [vlm_sampler(i) for i in range(max(1, n_samples))]
    agg = aggregate_samples(samples)
    calibrated = {c: calibrate(agg["criteria"].get(c, 0.0), calibration) for c in CRITERIA}

    # RULE 2: high spread across samples ⇒ uncertain → probe.
    if agg["maxSpread"] > VARIANCE_SPREAD_MAX:
        return {
            "verdict": "uncertain", "action": "probe", "ranVlm": True,
            "criteria": calibrated, "maxSpread": agg["maxSpread"],
            "reason": f"VLM sample spread {agg['maxSpread']:.2f} > {VARIANCE_SPREAD_MAX} (unstable opinion)",
        }
    # RULE 4: evidence cross-check against geometry.
    if not evidence_consistent(agg.get("claimedClass"), geometry_class):
        return {
            "verdict": "uncertain", "action": "probe", "ranVlm": True,
            "criteria": calibrated, "claimedClass": agg.get("claimedClass"),
            "reason": f"VLM claimed {agg.get('claimedClass')!r} contradicts geometry {geometry_class!r}",
        }

    below = {c: v for c, v in calibrated.items() if v < criteria_min}
    if not below:
        # All criteria satisfied. Confirm a pass, or RESCUE a soft near-threshold reject.
        eye_verdict = eye_result.get("verdict")
        rescued = eye_verdict in ("low-confidence", "reject")  # soft (no hard failure — checked above)
        return {
            "verdict": "pass", "action": "continue", "ranVlm": True,
            "criteria": calibrated,
            "reason": ("VLM criteria all ≥ min; soft-reject rescued (deterministic hard gates already passed)"
                       if rescued else "VLM criteria all ≥ min; pass confirmed"),
        }

    # RULE 5: a criterion is below min → withhold. Route by which criterion.
    conceptual = any(c in below for c in ("objectness", "semantic"))
    return {
        "verdict": "withhold",
        "action": "refine-spec" if conceptual else "refine-code",
        "ranVlm": True,
        "criteria": calibrated,
        "below": below,
        "reason": ("VLM objectness/semantic below min (conceptual — spec wrong)"
                   if conceptual else "VLM structural/specular below min (execution — code wrong)"),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eye", required=True, type=Path, help="divine_eye result JSON")
    parser.add_argument("--samples", type=Path, help="JSON list of VLM sample dicts (offline/testing)")
    parser.add_argument("--geometry-class", default=None)
    parser.add_argument("--criteria-min", type=float, default=DEFAULT_CRITERIA_MIN)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        eye = json.loads(args.eye.read_text(encoding="utf-8"))
        sampler = None
        if args.samples:
            preloaded = json.loads(args.samples.read_text(encoding="utf-8"))
            sampler = lambda i: preloaded[i % len(preloaded)]  # noqa: E731
        result = gate(eye, sampler, n_samples=len(json.loads(args.samples.read_text())) if args.samples else 3,
                      criteria_min=args.criteria_min, geometry_class=args.geometry_class)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False) if args.json
          else f"{result['verdict'].upper()} → {result['action']}  (ranVlm={result['ranVlm']})")
    return 0 if result["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
