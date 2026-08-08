#!/usr/bin/env python3
"""Rank a look-dev parameter sweep against the reference, so the choice is measured not reasoned.

WHY THIS EXISTS. On one reconstruction three consecutive correction loops moved the finish in the
WRONG direction. Each loop reasoned about PBR physics -- metalness suppresses albedo, so lower it;
the clearcoat adds a neutral specular, so lower that; the rig is hot, so cut it -- and each was
measured only AFTER the edit. Saturation error went -24, -62, -79, -79. The actual culprit was the
tone-mapping operator, a variable that had been fixed by ASSUMPTION at the very start on the
authority of a doc comment and was never questioned, so three loops were spent compensating for it
with other knobs. Enumerating the space instead (4 operators x 4 exposures) settled it in ONE run,
and the winner was the operator the doc had ruled out. The sweep was cheaper than any single
reasoning loop it replaced.

The discipline this encodes: when a parameter's effect is non-obvious and the space is small and
enumerable, SWEEP IT. And put the variables you "already know" inside the sweep -- the one that is
wrong is disproportionately likely to be the one nobody re-examined.

SCOPE. Rendering a grid is project-specific (which knob, set how), so this ranks rather than
renders. Produce one PNG per combination, name each by its combination, then:

    rank_lookdev_sweep.py --reference plate.png \\
        --candidate neutral@0.70=sweep/neutral-0.7.png \\
        --candidate agx@1.15=sweep/agx-1.15.png ... --json

A Three.js grid is a dozen lines in the browser driver you already use for capture: set
`renderer.toneMapping` / `toneMappingExposure` (or whatever knob is under test), render, screenshot.

METRIC. Value and saturation deltas over the subject foreground, plus CIEDE2000 for reporting.
Rank is |dV| + |dS| because that is what actually discriminated on real data: the failing
candidates were not far off in hue, they were washed out -- bright and desaturated -- and a
lightness-weighted distance alone under-punishes exactly that.

Exit 0 on a ranked result, 2 on an unreadable input. This never picks FOR you: it reports the
ordering and the numbers behind it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage1_intake"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))

from check_reference_admission import build_foreground_mask, load_image  # noqa: E402
from color_metrics import ciede2000, srgb_to_lab  # noqa: E402


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _hsv_value_saturation(rgb: tuple[int, int, int]) -> tuple[float, float]:
    r, g, b = rgb
    high, low = max(r, g, b), min(r, g, b)
    value = float(high)
    saturation = 0.0 if high == 0 else (high - low) / high * 255.0
    return value, saturation


def summarise(path: Path) -> dict[str, Any]:
    """Median RGB / value / saturation over the subject foreground only.

    Background must be excluded or the metric measures the backdrop: a studio plate is a large
    flat area and would dominate any whole-frame statistic.
    """
    width, height, pixels, warnings = load_image(path)
    mask, _stats, mask_warnings = build_foreground_mask(width, height, pixels)
    reds: list[float] = []
    greens: list[float] = []
    blues: list[float] = []
    values: list[float] = []
    saturations: list[float] = []
    for index, is_subject in enumerate(mask):
        if not is_subject:
            continue
        r, g, b, _a = pixels[index]
        reds.append(r)
        greens.append(g)
        blues.append(b)
        value, saturation = _hsv_value_saturation((r, g, b))
        values.append(value)
        saturations.append(saturation)
    if not values:
        raise ValueError(f"{path}: no subject foreground found; cannot rank a blank capture")
    return {
        "path": str(path),
        "subjectPixels": len(values),
        "medianRGB": [round(_median(reds)), round(_median(greens)), round(_median(blues))],
        "medianValue": round(_median(values), 2),
        "medianSaturation": round(_median(saturations), 2),
        "warnings": list(warnings) + list(mask_warnings),
    }


def score(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    d_value = candidate["medianValue"] - reference["medianValue"]
    d_saturation = candidate["medianSaturation"] - reference["medianSaturation"]
    ref_lab = srgb_to_lab(tuple(int(c) for c in reference["medianRGB"]))
    cand_lab = srgb_to_lab(tuple(int(c) for c in candidate["medianRGB"]))
    return {
        "deltaValue": round(d_value, 2),
        "deltaSaturation": round(d_saturation, 2),
        "error": round(abs(d_value) + abs(d_saturation), 2),
        "deltaE2000": round(ciede2000(ref_lab, cand_lab), 3),
        "medianRGB": candidate["medianRGB"],
    }


def rank(reference: dict[str, Any], candidates: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [{"label": label, **score(reference, item)} for label, item in candidates.items()]
    rows.sort(key=lambda row: row["error"])
    return rows


def parse_candidate(token: str) -> tuple[str, Path]:
    if "=" not in token:
        raise argparse.ArgumentTypeError(
            f"--candidate expects LABEL=path.png (the label is the parameter combination), got {token!r}"
        )
    label, _, raw = token.partition("=")
    if not label.strip():
        raise argparse.ArgumentTypeError(f"--candidate needs a non-empty label: {token!r}")
    return label.strip(), Path(raw)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--candidate", action="append", required=True, type=parse_candidate,
                        metavar="LABEL=PATH", help="one rendered combination; repeatable")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        reference = summarise(args.reference)
        candidates = {label: summarise(path) for label, path in args.candidate}
    except Exception as exc:  # noqa: BLE001 - an unreadable sweep frame is an error, not a ranking
        print(f"rank_lookdev_sweep: {exc}", file=sys.stderr)
        return 2

    rows = rank(reference, candidates)
    report = {
        "reference": reference,
        "ranked": rows,
        "best": rows[0]["label"] if rows else None,
        "spread": round(rows[-1]["error"] - rows[0]["error"], 2) if len(rows) > 1 else 0.0,
        "metric": "|deltaValue| + |deltaSaturation| over the subject foreground; deltaE2000 reported",
        "note": "Ranking only. A small spread means the parameter barely matters and the choice "
                "should be made on other grounds; a large spread means it dominates and reasoning "
                "about the other knobs first would be wasted effort.",
        "limitation": "Scored over the WHOLE subject foreground, so a large pale region outweighs "
                      "a small saturated one. A region-specific objective can rank differently and "
                      "on real data it did: whole-subject put neutral@0.85 first while a "
                      "ruby-region-only objective put neutral@0.70 first. Both agreed on the "
                      "OPERATOR, which was the variable worth sweeping; they differed only on "
                      "exposure. Re-rank on a crop when one region carries the identity.",
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"reference: medianRGB={reference['medianRGB']} "
              f"V={reference['medianValue']} S={reference['medianSaturation']}")
        print(f"{'rank':<5}{'label':<18}{'error':>8}{'dV':>8}{'dS':>8}{'dE2000':>9}  medianRGB")
        for position, row in enumerate(rows, 1):
            print(f"{position:<5}{row['label']:<18}{row['error']:>8}{row['deltaValue']:>8}"
                  f"{row['deltaSaturation']:>8}{row['deltaE2000']:>9}  {row['medianRGB']}")
        print(f"\nbest: {report['best']}   spread: {report['spread']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
