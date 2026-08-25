#!/usr/bin/env python3
"""Appearance difference INSIDE the silhouette, banded by height, against a baseline render.

Silhouette IoU is computed from the figure cells that lie on the outline -- about 11% of them at
grid 192. This reads the other 89%.

It exists because of a measured result, not a theory. A finished face and the same model with its
face deleted -- nose, mouth, eyes and both brows set invisible, hair material swapped for skin --
scored 0.8803 and 0.8803 on the outline metric, identical to four decimals. Adding an entire mouth
moved that metric by -0.0002, in the wrong direction. An outline metric cannot answer whether a face
is right, or even whether it is present, so it must not be the signal a correction loop optimises
for interior work.

Both renders are aligned by their foreground bounding box -- the same normalisation the IoU scorer
performs -- then resampled to a common lattice. Only cells that are figure in BOTH are compared, so
outline agreement cannot leak back in and inflate the result. The number is mean absolute luminance
difference as a fraction of full range: 0.000 is identical inside the mask, larger is further from
the baseline.

Restrict with --from/--to to read one region. On a standing figure the head is roughly the top 19%
of figure height, i.e. `--from 0 --to 0.19`.

BBOX CONVENTION. This module uses half-open corners `(x0, y0, x1, y1)`, matching
`objectness._bbox`. It deliberately does NOT use `diagnose_render.bbox_of`, which returns
`(x0, y0, width, height)` -- passing one where the other is expected produces confident garbage
rather than an error, and that exact substitution has already cost this project a debugging session.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stage1_intake"))
from extract_pbr_evidence import build_foreground_mask, load_image  # noqa: E402

from diagnose_render import mask_is_inverted  # noqa: E402

GRID = 192  # the lattice the audit's published numbers were computed on; changing it changes them


def _bbox_corners(mask: list[bool], width: int, height: int) -> tuple[int, int, int, int]:
    """Half-open `(x0, y0, x1, y1)`. An empty mask returns the whole frame."""
    min_x = min_y = 1 << 30
    max_x = max_y = -1
    for y in range(height):
        row = y * width
        for x in range(width):
            if mask[row + x]:
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
    if max_x < 0:
        return (0, 0, width, height)
    return (min_x, min_y, max_x + 1, max_y + 1)


def sample(png_path: Path, grid: int = GRID) -> tuple[list[float], list[bool], list[str]]:
    """Per-cell mean channel value and figure-majority flag, over the foreground bounding box.

    The cell value is an AREA MEAN, not a point sample. At grid 192 over a 1024px render each cell
    covers roughly 5x5 source pixels; point sampling would alias hard edges and make the result
    depend on where the lattice happened to land.
    """
    width, height, pixels, load_warnings = load_image(png_path)
    mask, _diagnostics, mask_warnings = build_foreground_mask(width, height, pixels)
    x0, y0, x1, y1 = _bbox_corners(mask, width, height)
    box_w = max(1, x1 - x0)
    box_h = max(1, y1 - y0)

    values = [0.0] * (grid * grid)
    solid = [False] * (grid * grid)
    for gy in range(grid):
        ys = y0 + gy * box_h // grid
        ye = max(y0 + (gy + 1) * box_h // grid, ys + 1)
        for gx in range(grid):
            xs = x0 + gx * box_w // grid
            xe = max(x0 + (gx + 1) * box_w // grid, xs + 1)
            total = 0
            counted = 0
            foreground = 0
            for y in range(ys, min(ye, height)):
                row = y * width
                for x in range(xs, min(xe, width)):
                    red, green, blue, _alpha = pixels[row + x]
                    total += red + green + blue
                    counted += 1
                    if mask[row + x]:
                        foreground += 1
            index = gy * grid + gx
            values[index] = (total / (3 * counted)) if counted else 0.0
            solid[index] = bool(counted) and foreground > counted / 2
    return values, solid, list(load_warnings) + list(mask_warnings)


def compare(
    baseline_png: Path,
    render_png: Path,
    band_from: float = 0.0,
    band_to: float = 1.0,
    grid: int = GRID,
) -> dict[str, Any]:
    """Mean absolute luminance difference over cells that are figure in both, within a height band.

    `cellsCompared` is reported alongside the score for the same reason every other gate here
    reports its sample count: a small difference over four cells is not evidence.
    """
    baseline_values, baseline_solid, baseline_warnings = sample(baseline_png, grid)
    render_values, render_solid, render_warnings = sample(render_png, grid)

    first_row = int(band_from * grid)
    last_row = max(int(band_to * grid), first_row + 1)
    shared = [
        gy * grid + gx
        for gy in range(first_row, min(last_row, grid))
        for gx in range(grid)
        if baseline_solid[gy * grid + gx] and render_solid[gy * grid + gx]
    ]

    result: dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "interior-difference",
        "baseline": str(baseline_png),
        "render": str(render_png),
        "band": {"from": band_from, "to": band_to},
        "grid": grid,
        "cellsCompared": len(shared),
        "warnings": baseline_warnings + render_warnings,
    }
    # Same hard refusal divine_eye makes. A mask that fell back to whole-frame coverage marks every
    # cell solid, so this metric would compare background against background and return a confident
    # number about nothing -- worse than the outline metric it replaces, because the number looks
    # like interior evidence.
    if mask_is_inverted(baseline_warnings) or mask_is_inverted(render_warnings):
        result["interiorDifference"] = None
        result["status"] = "foreground-mask-fell-back-to-whole-frame"
        return result
    if not shared:
        result["interiorDifference"] = None
        result["status"] = "no-overlapping-figure-cells"
        return result

    total = sum(abs(baseline_values[i] - render_values[i]) for i in shared)
    result["interiorDifference"] = total / len(shared) / 255.0
    result["status"] = "measured"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("baseline", type=Path)
    parser.add_argument("renders", nargs="+", type=Path)
    parser.add_argument("--from", dest="band_from", type=float, default=0.0)
    parser.add_argument("--to", dest="band_to", type=float, default=1.0)
    parser.add_argument("--grid", type=int, default=GRID)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    results = [
        compare(args.baseline, render, args.band_from, args.band_to, args.grid)
        for render in args.renders
    ]
    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2))
        return 0

    print(f"band y/H {args.band_from:.2f}..{args.band_to:.2f}   vs {args.baseline.name}")
    for result in results:
        name = Path(result["render"]).name
        if result["interiorDifference"] is None:
            print(f"  {name:<34} {result['status']}")
        else:
            print(
                f"  {name:<34} interior difference {result['interiorDifference']:.4f}"
                f"   ({result['cellsCompared']} cells)"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
