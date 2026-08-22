#!/usr/bin/env python3
"""Scene unit-sanity gate: does ONE metre value make ALL the architecture plausible?

The floor pattern fixes relative scale, but no single image reveals absolute
length — a photograph of a room tiled at 0.5 m looks identical to one tiled at
1.0 m with everything doubled. What breaks the tie is that buildings are built
for bodies: door heads, worktop heights, cornices and seats live in narrow,
well-documented bands. So instead of guessing, propose a metre-per-unit value
and test EVERY independently measured height against its band at once.

The strength of the gate is the conjunction. Any one fit is weak (a door head
alone tolerates 1.9-2.5 m), but a doorway AND a worktop AND a cornice AND the
camera height all landing in-band pins the unit tightly — and a single hard
failure kills the proposal. Report which.

Bands are architectural, deliberately generous, and centre on residential /
formal interiors. They are priors, not laws: the output is `plausible` /
`implausible` with named reasons, never a silent rescale.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# (kind, low_m, high_m) — generous residential/formal-interior bands
BANDS: dict[str, tuple[float, float]] = {
    "doorHead": (1.85, 3.60),          # top of a door / archway (formal interiors run tall)
    "worktop": (0.70, 1.20),           # commode / dresser / counter top
    "seat": (0.35, 0.55),              # chair or bench seat
    "cornice": (2.30, 6.00),           # underside of the cornice / ceiling band
    "ceiling": (2.20, 7.00),
    "dado": (0.60, 1.20),              # chair rail
    "cameraEyeLevel": (0.90, 1.90),    # handheld to tripod; below 0.9 is a floor rig
    "tableTop": (0.65, 0.80),
    "wallSconce": (1.50, 2.20),
}


def evaluate(unit_m: float, samples: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows, fails = [], []
    for name, spec in samples.items():
        kind = spec["kind"]
        if kind not in BANDS:
            rows.append({"sample": name, "kind": kind, "verdict": "unknown-kind"})
            continue
        lo, hi = BANDS[kind]
        metres = float(spec["units"]) * unit_m
        ok = lo <= metres <= hi
        rows.append({"sample": name, "kind": kind, "units": spec["units"],
                     "metres": round(metres, 3), "band": [lo, hi],
                     "verdict": "in-band" if ok else "OUT-OF-BAND"})
        if not ok:
            fails.append(f"{name} ({kind}): {metres:.2f} m outside [{lo}, {hi}]")
    tested = [r for r in rows if "band" in r]
    verdict = "implausible" if fails else ("plausible" if len(tested) >= 3 else
                                          "insufficient (need >= 3 banded samples)")
    return {"metresPerUnit": unit_m, "samples": rows, "verdict": verdict,
            "failures": fails,
            "note": "conjunction gate: one hard failure kills the proposal; "
                    "fewer than 3 banded samples cannot support it"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("samples", help='JSON {"name": {"kind": "doorHead", "units": 2.98}, ...}')
    ap.add_argument("--unit", type=float, required=True, help="proposed metres per pattern unit")
    ap.add_argument("--out", default="scene-unit.json")
    args = ap.parse_args(argv)
    result = evaluate(args.unit, json.loads(Path(args.samples).read_text()))
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if result["verdict"] == "plausible" else 1


if __name__ == "__main__":
    raise SystemExit(main())
