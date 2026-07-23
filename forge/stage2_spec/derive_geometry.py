#!/usr/bin/env python3
"""Deterministic geometry derivation from a reference mask (Plan 1.3 §4, Phase 2).

Blum-Medial-Axis-style lathe-profile derivation: instead of hand-guessing a
LatheGeometry profile, COMPUTE it from the object's silhouette. For a roughly
axisymmetric subject (vase, bottle, handle, hilt), sweeping the local inscribed
half-width along the major axis yields a revolve profile that matches the
reference — stopping the "eyeballed profile" failure mode.

Pure Python stdlib (reuses extract_pbr_evidence's mask primitives). No PIL/numpy.

Output profile is a list of [radius, axisPos] pairs in object-relative units
(axisPos in [-0.5, 0.5], radius normalized by the object's long dimension),
directly consumable as LatheGeometry points (x = radius ≥ 0, revolved around Y).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stage1_intake"))
from extract_pbr_evidence import build_foreground_mask, load_image, mask_bbox  # noqa: E402


def _row_span(mask: list[bool], width: int, y: int, x0: int, x1: int) -> tuple[int, int] | None:
    left = right = None
    for x in range(x0, x1 + 1):
        if mask[y * width + x]:
            if left is None:
                left = x
            right = x
    if left is None:
        return None
    return (left, right)


def _col_span(mask: list[bool], width: int, height: int, x: int, y0: int, y1: int) -> tuple[int, int] | None:
    top = bottom = None
    for y in range(y0, y1 + 1):
        if mask[y * width + x]:
            if top is None:
                top = y
            bottom = y
    if top is None:
        return None
    return (top, bottom)


def derive_lathe_profile(
    mask: list[bool],
    width: int,
    height: int,
    samples: int = 24,
) -> dict[str, Any]:
    """Derive a revolve profile from the mask by sweeping inscribed half-width along
    the object's major axis. Returns {axis, points:[[radius, axisPos],...], samples}."""
    x0, y0, bw, bh = mask_bbox(width, height, mask)
    x1, y1 = x0 + bw - 1, y0 + bh - 1
    vertical = bh >= bw  # revolve around the LONGER dimension's axis
    long_len = bh if vertical else bw
    if long_len <= 1:
        return {"axis": "vertical" if vertical else "horizontal", "points": [], "samples": 0}

    raw: list[tuple[float, float]] = []  # (axisPos_pixels_from_start, radius_pixels)
    for i in range(samples):
        t = i / (samples - 1)
        if vertical:
            y = int(round(y0 + t * (y1 - y0)))
            span = _row_span(mask, width, y, x0, x1)
            pos = y - y0
        else:
            x = int(round(x0 + t * (x1 - x0)))
            span = _col_span(mask, width, height, x, y0, y1)
            pos = x - x0
        radius = 0.0 if span is None else (span[1] - span[0]) / 2.0
        raw.append((float(pos), radius))

    # Normalize: axisPos to [-0.5, 0.5] over the long dimension, radius by long dimension.
    norm = float(long_len)
    points = [[round(r / norm, 4), round(pos / norm - 0.5, 4)] for pos, r in raw]
    return {
        "axis": "vertical" if vertical else "horizontal",
        "points": points,
        "samples": samples,
        "note": "Blum-medial-axis-derived revolve profile (radius along major axis); LatheGeometry-ready.",
    }


def derive_from_image(crop_path: Path, samples: int = 24) -> dict[str, Any]:
    width, height, pixels, _warn = load_image(crop_path)
    mask, _diag, _mw = build_foreground_mask(width, height, pixels)
    result = derive_lathe_profile(mask, width, height, samples)
    result["sourcePath"] = str(crop_path.resolve())
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--samples", type=int, default=24)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = derive_from_image(args.image.expanduser().resolve(), args.samples)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else
          f"axis={result['axis']} points={len(result['points'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
