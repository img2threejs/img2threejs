#!/usr/bin/env python3
"""Divine Eye calibration harness (Plan 1.3 §5) — fit/validate the Eye on a labeled corpus.

Before the Eye is allowed to HARD-GATE anything, it runs in report-only mode over a
corpus of labeled (reference, render) pairs — known-good (the render should pass) and
known-bad (it must be rejected). This harness runs divine_eye.evaluate on each pair,
tabulates every signal per label class, and checks that the fidelity score cleanly
SEPARATES good from bad. A corpus needs BOTH classes or it only measures false-rejects.

Acceptance (absolute, on the corpus): every known-good pair passes AND every known-bad
pair is rejected. Only then may report-only be flipped to hard-gate. Percentage targets
(Q1/Q4/Q5) apply to a rolling window of ≥20 real builds, not this small corpus.

Note: producing real RENDERS of the showcase demos needs a browser (deferred to the
end-to-end infra). This harness is the deterministic, render-agnostic scaffold: give it
whatever (reference, render) PNG pairs exist and it reports the separation + suggests a
threshold. Pure stdlib + reuses divine_eye. Zero token.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from divine_eye import evaluate  # noqa: E402


def run_corpus(pairs: list[dict]) -> dict[str, Any]:
    """pairs: [{"reference": path, "render": path, "label": "good"|"bad"}, ...].
    Runs the Eye on each and aggregates per-signal stats per label class."""
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        result = evaluate(Path(pair["reference"]), Path(pair["render"]))
        rows.append({
            "label": pair.get("label", "unlabeled"),
            "fidelity": result["fidelity"],
            "verdict": result["verdict"],
            "hardGateFailures": result["hardGateFailures"],
            "signals": result["signals"],
            "reference": pair["reference"],
            "render": pair["render"],
        })
    return {"rows": rows, "signalStats": _signal_stats(rows)}


def _signal_stats(rows: list[dict]) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for label in ("good", "bad"):
        labelled = [r for r in rows if r["label"] == label]
        if not labelled:
            continue
        fids = [r["fidelity"] for r in labelled]
        stats[label] = {
            "count": len(labelled),
            "fidelityMin": round(min(fids), 4),
            "fidelityMax": round(max(fids), 4),
            "fidelityMean": round(sum(fids) / len(fids), 4),
        }
    return stats


def separation(rows: list[dict]) -> dict[str, Any]:
    """Does fidelity cleanly separate good from bad? A clean split exists when the
    worst good fidelity is strictly above the best bad fidelity; the suggested
    threshold sits between them."""
    good = [r["fidelity"] for r in rows if r["label"] == "good"]
    bad = [r["fidelity"] for r in rows if r["label"] == "bad"]
    result: dict[str, Any] = {
        "hasGood": bool(good),
        "hasBad": bool(bad),
        "minGoodFidelity": round(min(good), 4) if good else None,
        "maxBadFidelity": round(max(bad), 4) if bad else None,
    }
    if good and bad:
        min_good, max_bad = min(good), max(bad)
        clean = min_good > max_bad
        result["cleanSeparation"] = clean
        result["suggestedThreshold"] = round((min_good + max_bad) / 2, 4) if clean else None
        # absolute acceptance: every good passes at the current target, every bad rejected
        result["allGoodPass"] = all(
            r["verdict"] == "pass" for r in rows if r["label"] == "good"
        )
        result["allBadRejected"] = all(
            r["verdict"] != "pass" for r in rows if r["label"] == "bad"
        )
        result["corpusAcceptable"] = result["allGoodPass"] and result["allBadRejected"]
    else:
        result["cleanSeparation"] = None
        result["corpusAcceptable"] = False
        result["warning"] = "corpus needs BOTH good and bad pairs (else it only measures false-rejects)"
    return result


def calibrate(pairs: list[dict]) -> dict[str, Any]:
    corpus = run_corpus(pairs)
    corpus["separation"] = separation(corpus["rows"])
    corpus["reportOnly"] = True
    corpus["note"] = ("report-only — flip to hard-gate ONLY when corpusAcceptable is true "
                      "(all good pass, all bad rejected)")
    return corpus


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path,
                        help="JSON list of {reference, render, label} pairs")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        pairs = json.loads(args.corpus.read_text(encoding="utf-8"))
        result = calibrate(pairs)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        sep = result["separation"]
        print(f"corpus: {result['signalStats']}")
        print(f"separation: clean={sep.get('cleanSeparation')} acceptable={sep.get('corpusAcceptable')} "
              f"suggestedThreshold={sep.get('suggestedThreshold')}")
    # exit 0 only when the corpus cleanly validates the Eye
    return 0 if result["separation"].get("corpusAcceptable") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
