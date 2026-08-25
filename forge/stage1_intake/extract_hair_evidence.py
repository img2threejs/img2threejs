#!/usr/bin/env python3
"""Measure what a reference image actually says about its hair.

WHAT WAS MISSING. `faceLandmarks.hairline` has existed as a slot since v1.2 and no generator has
ever read it, because nothing ever filled it. Banded dark coverage -- the measurement that finally
localised a hair deficit to the mid band -- was run by hand, four times, and never became a script.
And nothing at all measured the SHADING, which is where the deficit turned out mostly to live.

WHY SHADING IS IN HERE AT ALL. The reference this pipeline calibrates against is a 570,400-vertex
merged scan whose hair surface roughness is 0.00338 against a torso control of 0.00312. Its hair is
a smooth shell; every strand it appears to have is in the diffuse and normal textures. A pipeline
that emits code and no textures cannot copy that with geometry, so the numbers worth extracting are
the ones a material can act on: where the highlight band sits, and how much darker the roots are
than the tips.

WHAT THIS DELIBERATELY DOES NOT DO. It does not guess lock geometry. A single image does not carry
it, the reference does not contain it, and inventing it is how eleven hair locks ended up sharing a
tip radius of 0.0327.

Pure Python 3.10+ standard library.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_pbr_evidence import build_foreground_mask, load_image  # noqa: E402

# Hair is dark against skin at every skin tone the reference set contains, but "dark" has to be
# relative or a blonde subject reports no hair at all.
#
# The threshold is the between-class variance maximum (Otsu) over the head's own luminance
# histogram. A fixed PERCENTILE was tried first and is a trap: taking the 38th percentile as the
# cut makes "38% of head pixels are hair" true by construction. It measured 0.380, 0.384 and 0.382
# on three different views of the same subject, which looks like agreement and is arithmetic.
# Otsu finds where the distribution actually splits, so a bald head reports little hair and a
# hooded one reports a lot.
OTSU_BINS = 64
# If the two classes Otsu finds are barely separated there is no hair/skin split to speak of -- a
# bald head, or a subject cropped so tightly that only hair is in frame. Reported rather than
# silently returning a threshold through the middle of one population.
MIN_CLASS_SEPARATION = 12.0
# ... and the two classes must be far apart RELATIVE TO THEIR OWN SPREAD. Separation alone does not
# catch a single broad population: an ordinarily lit BALD head has a wide luminance spread from
# shading and measures a separation of 38.6, three times the floor above, while reporting a quarter
# of its scalp as hair.
#
# This bound is one of the few in the hair subsystem with real endpoints under it, because the
# archived BALD calibration capture is genuine ground truth for "no hair":
#
#   unimodal, theoretical      1.732   any uniform spread cut at its own middle, sqrt(3)
#   bald, synthetic key light  1.735   and identical at every gradient width, as predicted
#   BALD capture, real render  1.119   <-- the measured floor
#   reference front view       2.450   <-- the measured ceiling, and the tightest real case
#   reference profile          3.603
#   reference rear             4.177
#
# 2.0 sits between the two measured neighbours, 15% above the theoretical unimodal value and 22%
# below the tightest genuine hair view. A first attempt at 3.0 was invented rather than measured and
# rejected the reference's own front view.
MIN_SEPARABILITY = 2.0
# Reported in place of infinity when both classes are exactly constant. Finite because this value is
# serialised, and `json.dumps(float("inf"))` writes a bare `Infinity` that RFC 8259 forbids.
SEPARABILITY_MAX = 1.0e6
# Bands across the head, top to bottom. Three is what the by-hand measurement used, and it was
# enough to localise the deficit to the middle one.
BAND_NAMES = ("crown", "mid", "jaw")
# Fraction of the figure's height taken to be head. Anchored on the reference's own proportion --
# a 1.75m figure whose head spans y 1.52 to 1.75 -- which is 0.13, rounded up for framing slack.
HEAD_FRACTION = 0.15
# Views that see the back of the head. Anything not in here leaves the rear unobserved, and the
# report has to say so rather than let a generator invent a nape.
#
# A profile sees the nape and the rear mass in outline, which is weaker evidence than a true rear
# view but is evidence. `right-profile` was missing purely because the capture set this was written
# against names its two side views `profile` and `left-profile`; a set that spells both sides out
# would have had one of them silently ignored.
REAR_VIEWS = {
    "rear", "back", "orbit-plus-145", "orbit-minus-145",
    "profile", "left-profile", "right-profile",
}


def _luma(pixel: tuple[int, int, int, int]) -> float:
    return 0.2126 * pixel[0] + 0.7152 * pixel[1] + 0.0722 * pixel[2]


def otsu_threshold(values: Sequence[float], bins: int = OTSU_BINS) -> tuple[float, float, float]:
    """Split a luminance population in two where the between-class variance is greatest.

    Returns `(threshold, separation, separability)`.

    `separation` is the gap between the two class means. `separability` is that gap divided by the
    two classes' combined standard deviation -- large when the classes are genuinely distinct, and
    around 1.7 for ANY single broad population cut down the middle.

    SEPARABILITY IS NOT OPTIONAL. Otsu always returns a split, even from one population, and
    separation alone does not catch that: an ordinarily lit BALD head has a wide luminance spread
    from shading and measures a separation of 38.6, three times over any sane floor, while
    reporting a quarter of its scalp as hair.
    """
    if not values:
        return (0.0, 0.0, 0.0)
    low = min(values)
    high = max(values)
    if high - low < 1e-9:
        return (low, 0.0, 0.0)

    histogram = [0] * bins
    width = (high - low) / bins
    for value in values:
        index = min(bins - 1, int((value - low) / width))
        histogram[index] += 1

    total = len(values)
    sum_all = sum((low + (i + 0.5) * width) * count for i, count in enumerate(histogram))
    best_variance = -1.0
    best_index = 0
    weight_below = 0
    sum_below = 0.0
    for index, count in enumerate(histogram[:-1]):
        weight_below += count
        if weight_below == 0:
            continue
        weight_above = total - weight_below
        if weight_above == 0:
            break
        sum_below += (low + (index + 0.5) * width) * count
        mean_below = sum_below / weight_below
        mean_above = (sum_all - sum_below) / weight_above
        variance = weight_below * weight_above * (mean_below - mean_above) ** 2
        if variance > best_variance:
            best_variance = variance
            best_index = index

    threshold = low + (best_index + 1) * width
    below = [v for v in values if v <= threshold]
    above = [v for v in values if v > threshold]
    if not below or not above:
        return (threshold, 0.0, 0.0)
    mean_below = sum(below) / len(below)
    mean_above = sum(above) / len(above)
    separation = abs(mean_above - mean_below)

    # SEPARABILITY, not histogram shape. Two shape-based attempts failed here, each on a different
    # edge case, and both are recorded because the failures are instructive:
    #
    #   valley = min bin between the peaks    Broke on QUANTISATION. An 8-bit gradient lands on far
    #                                         fewer distinct values than there are bins, so empty
    #                                         bins scatter through a perfectly unimodal spread and
    #                                         read as a bottomless valley. A key-lit bald head
    #                                         scored 0.000 -- maximally bimodal -- and passed as 50%
    #                                         hair.
    #   valley = the bin at the cut           Broke when the cut lands beside a spike, which is what
    #                                         a two-tone synthetic image always does. Every haired
    #                                         fixture scored 1.0 and was thrown out.
    #
    # This measure asks the question directly: are the two classes far apart RELATIVE TO THEIR OWN
    # spread? Two tight clusters give a large ratio. One broad population cut down the middle gives
    # about 1.7 whatever its width, because widening it grows the gap and the spreads together.
    # No bins, no smoothing, no peak finding, nothing to tune.
    spread_below = (sum((v - mean_below) ** 2 for v in below) / len(below)) ** 0.5
    spread_above = (sum((v - mean_above) ** 2 for v in above) / len(above)) ** 0.5
    total_spread = spread_below + spread_above
    if total_spread < 1e-9:
        # Two exactly-constant classes -- as separated as it is possible to be, and the arithmetic
        # answer is infinity. A FINITE sentinel goes out instead, because this value is serialised:
        # `json.dumps(float("inf"))` emits a bare `Infinity`, which RFC 8259 does not allow. Python's
        # own loader accepts it, so every test here parsed it happily while `jq`, `JSON.parse` and
        # any strict validator would reject the file the CLI had just written. And this is not a
        # rare branch: a two-tone synthetic image hits it every time.
        return (threshold, separation, SEPARABILITY_MAX)
    return (threshold, separation, min(separation / total_spread, SEPARABILITY_MAX))


def _bbox(mask: Sequence[bool], width: int) -> tuple[int, int, int, int] | None:
    rows = [i // width for i, on in enumerate(mask) if on]
    if not rows:
        return None
    columns = [i % width for i, on in enumerate(mask) if on]
    return (min(columns), min(rows), max(columns), max(rows))


def analyse_view(path: Path, view: str) -> dict[str, Any]:
    """Everything one image can honestly say about the hair in it."""
    width, height, pixels, warnings = load_image(path)
    mask, _mask_info, mask_warnings = build_foreground_mask(width, height, pixels)
    warnings = list(warnings) + list(mask_warnings)

    box = _bbox(mask, width)
    if box is None:
        return {"view": view, "status": "no-foreground", "warnings": warnings + ["empty mask"]}
    x0, y0, x1, y1 = box
    figure_height = y1 - y0 + 1
    head_bottom = y0 + max(1, int(figure_height * HEAD_FRACTION))

    head_pixels: list[tuple[int, int, float]] = []
    for y in range(y0, head_bottom + 1):
        for x in range(x0, x1 + 1):
            index = y * width + x
            if mask[index]:
                head_pixels.append((x, y, _luma(pixels[index])))
    if not head_pixels:
        return {"view": view, "status": "no-head-region", "warnings": warnings}

    threshold, separation, separability = otsu_threshold([luma for _, _, luma in head_pixels])
    if separation < MIN_CLASS_SEPARATION or separability < MIN_SEPARABILITY:
        reason = (
            f"class separation {separation:.1f} < {MIN_CLASS_SEPARATION}"
            if separation < MIN_CLASS_SEPARATION
            else f"separability {separability:.2f} < {MIN_SEPARABILITY}, so the spread is one broad "
                 f"population cut down the middle -- shading on bare skin looks exactly like this"
        )
        return {
            "view": view,
            "status": "no-hair-skin-split",
            "darkThreshold": round(threshold, 2),
            "classSeparation": round(separation, 2),
            "separability": round(separability, 3),
            "warnings": warnings + [
                f"the head region has one luminance population, not two ({reason}); this view "
                f"cannot tell hair from skin"
            ],
        }
    hair = [(x, y, luma) for x, y, luma in head_pixels if luma <= threshold]

    # ---- banded coverage: the by-hand measurement, made repeatable ----
    head_height = head_bottom - y0 + 1
    bands: dict[str, dict[str, float]] = {}
    for band_index, band_name in enumerate(BAND_NAMES):
        top = y0 + int(head_height * band_index / len(BAND_NAMES))
        bottom = y0 + int(head_height * (band_index + 1) / len(BAND_NAMES))
        in_band = [(x, y, luma) for x, y, luma in head_pixels if top <= y < max(bottom, top + 1)]
        dark = [p for p in in_band if p[2] <= threshold]
        bands[band_name] = {
            "coverage": round(len(dark) / len(in_band), 4) if in_band else 0.0,
            "meanLuma": round(sum(p[2] for p in dark) / len(dark), 2) if dark else 0.0,
            "pixelCount": len(in_band),
        }

    # ---- hairline: where the hair stops and the face starts ----
    # Per column, walk down from the top of the head and record the first non-hair row. Reported as
    # a fraction of figure height, which is the unit `faceLandmarks` already uses.
    hair_rows_by_column: dict[int, list[int]] = {}
    for x, y, _ in hair:
        hair_rows_by_column.setdefault(x, []).append(y)
    crossings: list[float] = []
    for x, rows in hair_rows_by_column.items():
        rows.sort()
        run_end = rows[0]
        for row in rows[1:]:
            if row - run_end > 2:
                break
            run_end = row
        crossings.append((run_end - y0) / figure_height)
    crossings.sort()
    hairline = round(crossings[len(crossings) // 2], 4) if crossings else None

    # ---- shading: where the highlight band sits, and how much darker the roots are ----
    #
    # Measured over rows the hair OWNS, not over the sub-threshold pixels alone. A specular band on
    # dark hair is bright by definition, so it lands above the hair/skin threshold and a naive pass
    # discards exactly the thing it is looking for -- measured on a fixture whose planted highlight
    # had luminance 132.8: the reported band luminance came back as 31.8, the base hair colour, and
    # the highlight was invisible to it. A row that is majority hair is a hair row, and every head
    # pixel in it counts.
    hair_by_row: dict[int, int] = {}
    total_by_row: dict[int, list[float]] = {}
    for _, y, _ in hair:
        hair_by_row[y] = hair_by_row.get(y, 0) + 1
    for _, y, luma in head_pixels:
        total_by_row.setdefault(y, []).append(luma)
    owned = {
        y for y, values in total_by_row.items()
        if len(values) >= 3 and hair_by_row.get(y, 0) >= len(values) * 0.5
    }
    # A row can be blown out end to end -- glossy hair under a key light does exactly this -- and
    # then it holds no sub-threshold pixels at all and the majority test drops it. Since it is the
    # brightest row, dropping it removes the highlight the moment the highlight is strong enough to
    # matter. Any row ENCLOSED by owned rows is owned: hair does not stop for one row and resume.
    if owned:
        for y in range(min(owned), max(owned) + 1):
            if y in total_by_row and len(total_by_row[y]) >= 3:
                owned.add(y)
    row_means = {y: sum(total_by_row[y]) / len(total_by_row[y]) for y in owned}
    specular_row = max(row_means, key=lambda y: row_means[y]) if row_means else None
    ordered_rows = sorted(row_means)
    if len(ordered_rows) >= 6:
        third = max(1, len(ordered_rows) // 3)
        top_mean = sum(row_means[y] for y in ordered_rows[:third]) / third
        bottom_mean = sum(row_means[y] for y in ordered_rows[-third:]) / third
        root_tip_delta = round(bottom_mean - top_mean, 2)
    else:
        root_tip_delta = None

    return {
        "view": view,
        "status": "measured",
        "figureBox": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},
        "darkThreshold": round(threshold, 2),
        "classSeparation": round(separation, 2),
        "separability": round(separability, 3),
        "hairFraction": round(len(hair) / len(head_pixels), 4),
        "bands": bands,
        "hairline": hairline,
        "shading": {
            "specularBandV": (
                round((specular_row - y0) / max(1, head_height), 4) if specular_row is not None else None
            ),
            "specularBandLuma": round(row_means[specular_row], 2) if specular_row is not None else None,
            "rootToTipLumaDelta": root_tip_delta,
            "note": (
                "rootToTipLumaDelta is lower-row mean minus upper-row mean, so a positive value "
                "means the hair gets LIGHTER downward. Which end is the root depends on the "
                "hairstyle and is not inferred here."
            ),
        },
        "warnings": warnings,
    }


def extract_hair_evidence(views: dict[str, Path]) -> dict[str, Any]:
    """Combine per-view measurements and state plainly what was never seen."""
    per_view = {name: analyse_view(path, name) for name, path in sorted(views.items())}
    measured = {name: data for name, data in per_view.items() if data.get("status") == "measured"}

    hairlines = [d["hairline"] for d in measured.values() if d.get("hairline") is not None]
    consensus = round(sorted(hairlines)[len(hairlines) // 2], 4) if hairlines else None

    not_observed: list[str] = []
    if not any(name in REAR_VIEWS for name in measured):
        not_observed.append(
            "rear: no view in this set sees the back of the head, so the nape and rear mass are "
            "unobserved. Do not author them as if measured."
        )
    if len(measured) < 2:
        not_observed.append(
            "depth: a single view cannot separate hair thickness from hair silhouette."
        )
    if not hairlines:
        not_observed.append("hairline: no view produced a usable hair/skin boundary.")

    return {
        "schemaVersion": 1,
        "kind": "hair-evidence",
        "views": per_view,
        "faceLandmarks": {"hairline": consensus} if consensus is not None else {},
        "notObserved": not_observed,
        "confidence": round(min(1.0, len(measured) / 4.0), 3),
        "calibrationNote": (
            "Envelope and shading numbers are measurable from an image. Lock geometry is not, and "
            "is not reported: the reference this pipeline calibrates against is a merged scan whose "
            "hair is a smooth shell with all strand detail in textures."
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("images", nargs="+", help="view=path pairs, e.g. front=ref.front.png")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    views: dict[str, Path] = {}
    for entry in args.images:
        if "=" not in entry:
            parser.error(f"expected view=path, got {entry!r}")
        name, _, raw = entry.partition("=")
        views[name] = Path(raw)

    report = extract_hair_evidence(views)
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
