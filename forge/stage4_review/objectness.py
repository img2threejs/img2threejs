#!/usr/bin/env python3
"""Stdlib objectness proxy (OSIM-lite) for the Divine Eye — Plan 1.3 task #18, Pillar-1 safe.

A pure-Python gradient-orientation-histogram (HOG-like) descriptor compared by cosine
similarity. The object's foreground is masked, cropped to its bbox, and resampled to a
canonical square grid, so the descriptor is invariant to:
  - background      (foreground mask drops it)
  - position        (bbox crop)
  - scale           (resample to a fixed grid)
  - absolute brightness / colour  (gradient *orientations*, per-cell L2-normalised)

Those are exactly the photo-vs-procedural axes where silhouette-IoU, SSIM and edge-overlap
collapse (a faithful reconstruction on a dark background vs a white-bg studio photo). This
is the graceful *stdlib floor* objectness signal — no numpy, no weights, no download. A
learned CNN-OSIM remains an optional V2 upgrade behind the same signal name.

CLI:  objectness.py --reference REF --render RENDER [--json]
API:  objectness_similarity(ref_path, render_path) -> float in [0,1]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stage1_intake"))
from extract_pbr_evidence import build_foreground_mask, load_image  # noqa: E402

GRID = 96   # canonical square the object bbox is resampled to
CELLS = 8   # CELLS x CELLS spatial cells
BINS = 9    # orientation bins over 0..180 (unsigned gradient)


def _to_gray(pixels: list[tuple[int, int, int, int]]) -> list[int]:
    return [(p[0] * 30 + p[1] * 59 + p[2] * 11) // 100 for p in pixels]


def _bbox(mask: list[bool], w: int, h: int) -> tuple[int, int, int, int]:
    minx = miny = 1 << 30
    maxx = maxy = -1
    for y in range(h):
        row = y * w
        for x in range(w):
            if mask[row + x]:
                if x < minx:
                    minx = x
                if x > maxx:
                    maxx = x
                if y < miny:
                    miny = y
                if y > maxy:
                    maxy = y
    if maxx < 0:  # empty mask -> whole frame
        return (0, 0, w, h)
    return (minx, miny, maxx + 1, maxy + 1)


def _resample_bbox(gray: list[int], w: int, h: int, box: tuple[int, int, int, int], n: int) -> list[float]:
    x0, y0, x1, y1 = box
    bw = max(1, x1 - x0)
    bh = max(1, y1 - y0)
    out = [0.0] * (n * n)
    for j in range(n):
        sy = min(h - 1, y0 + (j * bh) // n)
        base = sy * w
        for i in range(n):
            sx = min(w - 1, x0 + (i * bw) // n)
            out[j * n + i] = float(gray[base + sx])
    return out


def descriptor(path: str | Path) -> list[float]:
    """HOG-like descriptor of the object's foreground, canonicalised for pose/scale/bg."""
    w, h, pixels, _ = load_image(Path(path))
    gray = _to_gray(pixels)
    mask, _diag, _warn = build_foreground_mask(w, h, pixels)
    x0, y0, x1, y1 = _bbox(mask, w, h)
    # pad the bbox so the object's silhouette contour (its strongest shape cue) lands
    # INSIDE the resampled grid — a solid object cropped flush would otherwise put its
    # only edges on the border (skipped by the gradient loop) and yield a zero descriptor.
    px = int((x1 - x0) * 0.10) + 2
    py = int((y1 - y0) * 0.10) + 2
    box = (max(0, x0 - px), max(0, y0 - py), min(w, x1 + px), min(h, y1 + py))
    g = _resample_bbox(gray, w, h, box, GRID)

    hist = [0.0] * (CELLS * CELLS * BINS)
    cell = GRID // CELLS
    for y in range(1, GRID - 1):
        row = y * GRID
        cy = min(CELLS - 1, y // cell)
        for x in range(1, GRID - 1):
            gx = g[row + x + 1] - g[row + x - 1]
            gy = g[(y + 1) * GRID + x] - g[(y - 1) * GRID + x]
            mag = math.hypot(gx, gy)
            if mag < 1e-6:
                continue
            ang = math.degrees(math.atan2(gy, gx)) % 180.0
            b = int(ang / 180.0 * BINS) % BINS
            cx = min(CELLS - 1, x // cell)
            hist[(cy * CELLS + cx) * BINS + b] += mag
    # per-cell L2 block normalisation -> contrast/brightness invariance
    for c in range(CELLS * CELLS):
        base = c * BINS
        norm = math.sqrt(sum(hist[base + k] ** 2 for k in range(BINS))) + 1e-6
        for k in range(BINS):
            hist[base + k] /= norm
    return hist


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


def objectness_similarity(ref_path: str | Path, render_path: str | Path) -> float:
    """Cosine similarity of the two objectness descriptors, in [0,1]. 1 = same structure."""
    return cosine(descriptor(ref_path), descriptor(render_path))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stdlib objectness (OSIM-lite) similarity")
    ap.add_argument("--reference", required=True)
    ap.add_argument("--render", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    score = objectness_similarity(args.reference, args.render)
    out: dict[str, Any] = {"objectness": round(score, 4), "grid": GRID, "cells": CELLS, "bins": BINS}
    if args.json:
        print(json.dumps(out))
    else:
        print(f"objectness similarity: {score:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
