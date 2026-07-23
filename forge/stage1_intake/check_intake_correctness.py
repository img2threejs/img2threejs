#!/usr/bin/env python3
"""Intake-correctness cross-check (Plan 1.3 §4.6) — no semantic garbage-in.

Reference admission (§4.5) proves the image is a valid target; this proves the
intake actually UNDERSTOOD it. A vague prompt can make intake confidently
mis-classify (a knife specced as a "spoon"); if that wrong guess drives spec
authoring, everything downstream is wrong. So before spec authoring proceeds, the
intake's object-class/material guess is (a) exposed as `assumptionsExposed`, and
(b) cross-checked against an objectness verdict. A contradiction HALTS to
request-input rather than building a wrong spec.

Scaffold note (§4.6 + build order §8.5): the objectness verdict ideally comes from
OSIM (Phase 3, deterministic, zero-token) or, failing that, one cheap VLM objectness
call. This module owns the DECISION/control-flow now; the objectness source is
pluggable and wired in Phase 3. With no verdict available, it proceeds but records
that confirmation is deferred — never silently claims confirmation it didn't get.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# A contradiction only halts when the objectness signal is confident enough to be trusted.
CONTRADICTION_CONFIDENCE_MIN = 0.6


def expose_assumptions(assessment: dict[str, Any]) -> dict[str, Any]:
    """Extract the intake's confident guesses so a wrong default is visible, not buried."""
    psa = assessment.get("preSpecAssessment", assessment)
    obj = psa.get("objectClass", {}) if isinstance(psa.get("objectClass"), dict) else {}
    return {
        "primaryType": obj.get("primaryType"),
        "primaryDomain": obj.get("primaryDomain"),
        "materialFamilies": obj.get("materialFamilies", []),
    }


def decide(
    assessment: dict[str, Any],
    objectness_verdict: dict[str, Any] | None,
) -> dict[str, Any]:
    """Decide whether spec authoring may proceed.

    objectness_verdict (pluggable; from OSIM in Phase 3 or a VLM objectness call):
      {"matchesDeclaredClass": bool, "confidence": float, "detectedClass": str}
      or None when no objectness source is available yet.

    Returns {action: 'proceed'|'halt', confirmed: bool, assumptionsExposed, reason}.
    """
    assumptions = expose_assumptions(assessment)

    if objectness_verdict is None:
        return {
            "action": "proceed",
            "confirmed": False,
            "assumptionsExposed": assumptions,
            "reason": (
                "no objectness source available yet — confirmation DEFERRED to Phase 3 (OSIM). "
                "Proceeding on exposed assumptions; they remain falsifiable at Divine-Eye review."
            ),
        }

    matches = bool(objectness_verdict.get("matchesDeclaredClass"))
    confidence = float(objectness_verdict.get("confidence", 0.0))
    detected = objectness_verdict.get("detectedClass")

    if not matches and confidence >= CONTRADICTION_CONFIDENCE_MIN:
        return {
            "action": "halt",
            "confirmed": False,
            "assumptionsExposed": assumptions,
            "reason": (
                f"intake declared object class {assumptions.get('primaryType')!r} but the objectness "
                f"check detected {detected!r} (confidence {confidence:.2f} ≥ {CONTRADICTION_CONFIDENCE_MIN}). "
                "Halting to request-input rather than building a wrong spec (§4.6)."
            ),
        }

    return {
        "action": "proceed",
        "confirmed": matches,
        "assumptionsExposed": assumptions,
        "reason": (
            f"objectness check {'confirms' if matches else 'does not contradict'} the declared class "
            f"(confidence {confidence:.2f})."
        ),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assessment", type=Path, help="assessment/spec JSON with preSpecAssessment.objectClass")
    parser.add_argument("--objectness", type=Path, help="optional objectness verdict JSON (matchesDeclaredClass/confidence/detectedClass)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    assessment = json.loads(args.assessment.read_text(encoding="utf-8"))
    verdict = json.loads(args.objectness.read_text(encoding="utf-8")) if args.objectness else None
    result = decide(assessment, verdict)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"action: {result['action'].upper()}  confirmed={result['confirmed']}")
        print(f"  assumptions: {result['assumptionsExposed']}")
        print(f"  reason: {result['reason']}")
    # Exit 1 on halt so a pipeline runner can gate on it; 0 on proceed.
    return 1 if result["action"] == "halt" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
