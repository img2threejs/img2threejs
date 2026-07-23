#!/usr/bin/env python3
"""Reference-effect detectors for the img2threejs presentation stage.

Post-processing (depth-of-field, bloom) is authorized on the PRESENTATION render
ONLY, and ONLY when the REFERENCE photo actually exhibits that characteristic.
These detectors look at the reference image and decide whether each effect is
warranted so that effects serve photographic fidelity, not decoration. They are
NEVER applied to the Eye's evaluation render.

Pure Python 3.10+ stdlib only. Reuses the stage1 loader (import, do NOT edit).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stage1_intake"))
from extract_pbr_evidence import load_image, build_foreground_mask  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def luma(r: int, g: int, b: int) -> float:
    """Rec.709 luma normalized to 0..1."""
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _luma_grid(
    width: int,
    height: int,
    pixels: list[tuple[int, int, int, int]],
    step: int,
) -> tuple[list[float], int, int]:
    """Downsample the image to a manageable grid of luma values.

    Samples every ``step`` pixels. Returns (grid, grid_width, grid_height) where
    grid is row-major of length grid_width * grid_height.
    """
    grid: list[float] = []
    gw = 0
    gh = 0
    for y in range(0, height, step):
        gh += 1
        row_count = 0
        for x in range(0, width, step):
            r, g, b, _a = pixels[y * width + x]
            grid.append(luma(r, g, b))
            row_count += 1
        gw = row_count
    return grid, gw, gh


def _mask_grid(
    width: int,
    height: int,
    mask: list[bool],
    step: int,
) -> list[bool]:
    """Downsample the foreground mask on the same sampling lattice as _luma_grid."""
    out: list[bool] = []
    for y in range(0, height, step):
        for x in range(0, width, step):
            out.append(mask[y * width + x])
    return out


def _grid_step(width: int, height: int, target: int = 160) -> int:
    """Pick a sampling step so the longer side is ~<= target cells."""
    return max(1, max(width, height) // target)


def local_gradient_energy(
    grid: list[float],
    gw: int,
    gh: int,
    keep: list[bool] | None,
) -> float:
    """Mean of |L(x,y)-L(x-1,y)| + |L(x,y)-L(x,y-1)| over the selected region.

    ``keep`` selects which grid cells to include (True = include). When None,
    every cell with x>=1 and y>=1 is included.
    """
    total = 0.0
    count = 0
    for y in range(1, gh):
        for x in range(1, gw):
            idx = y * gw + x
            if keep is not None and not keep[idx]:
                continue
            here = grid[idx]
            dxe = abs(here - grid[idx - 1])
            dye = abs(here - grid[idx - gw])
            total += dxe + dye
            count += 1
    return total / count if count else 0.0


# --------------------------------------------------------------------------- #
# Detectors
# --------------------------------------------------------------------------- #
def detect_background_blur(png_path: Path) -> dict[str, Any]:
    """Detect shallow depth-of-field: background much blurrier than the subject."""
    width, height, pixels, _warnings = load_image(png_path)
    mask, diag, _mask_warnings = build_foreground_mask(width, height, pixels)
    coverage = float(diag.get("foregroundCoverage", 0.0))

    step = _grid_step(width, height)
    grid, gw, gh = _luma_grid(width, height, pixels, step)
    mgrid = _mask_grid(width, height, mask, step)

    subject_keep = mgrid
    background_keep = [not value for value in mgrid]

    subject_energy = local_gradient_energy(grid, gw, gh, subject_keep)
    background_energy = local_gradient_energy(grid, gw, gh, background_keep)
    ratio = background_energy / subject_energy if subject_energy > 0 else 1.0

    has_background = 0.05 < coverage < 0.95
    blurred = has_background and ratio < 0.5

    if not has_background:
        reason = (
            f"no separable background (foreground coverage {coverage:.3f} outside "
            f"0.05..0.95); depth-of-field not applicable"
        )
    elif blurred:
        reason = (
            f"background detail ({background_energy:.5f}) is under half the subject "
            f"detail ({subject_energy:.5f}); ratio {ratio:.3f} < 0.5 indicates shallow "
            f"depth-of-field"
        )
    else:
        reason = (
            f"background detail ({background_energy:.5f}) is comparable to subject "
            f"detail ({subject_energy:.5f}); ratio {ratio:.3f} >= 0.5, scene reads sharp"
        )

    return {
        "blurred": blurred,
        "backgroundEnergy": round(background_energy, 6),
        "subjectEnergy": round(subject_energy, 6),
        "ratio": round(ratio, 4),
        "reason": reason,
    }


def detect_highlight_glow(png_path: Path) -> dict[str, Any]:
    """Detect bloom: clipped highlights surrounded by a gradual bright halo."""
    width, height, pixels, _warnings = load_image(png_path)
    total = max(1, width * height)

    lumas = [luma(r, g, b) for r, g, b, _a in pixels]
    hot_indices = [i for i, value in enumerate(lumas) if value > 0.92]
    hot_fraction = len(hot_indices) / total

    if not hot_indices:
        return {
            "glow": False,
            "hotFraction": round(hot_fraction, 6),
            "haloFraction": 0.0,
            "reason": "no near-white highlights (luma > 0.92); nothing to bloom",
        }

    # Sample the hot pixels so large clipped regions stay fast.
    sample_limit = 200
    if len(hot_indices) > sample_limit:
        stride = len(hot_indices) // sample_limit
        sampled = hot_indices[::stride][:sample_limit]
    else:
        sampled = hot_indices

    # Ring of neighbors just outside the hot core (2-3px out, 8 compass dirs).
    ring_offsets = [
        (dx, dy)
        for radius in (2, 3)
        for dx, dy in (
            (-radius, 0),
            (radius, 0),
            (0, -radius),
            (0, radius),
            (-radius, -radius),
            (radius, -radius),
            (-radius, radius),
            (radius, radius),
        )
    ]

    haloed = 0
    for idx in sampled:
        x = idx % width
        y = idx // width
        elevated_neighbor = False
        for dx, dy in ring_offsets:
            nx = x + dx
            ny = y + dy
            if 0 <= nx < width and 0 <= ny < height:
                neighbor = lumas[ny * width + nx]
                if 0.6 <= neighbor <= 0.92:
                    elevated_neighbor = True
                    break
        if elevated_neighbor:
            haloed += 1

    halo_fraction = haloed / len(sampled)
    glow = hot_fraction > 0.005 and halo_fraction > 0.5

    if glow:
        reason = (
            f"highlights present (hotFraction {hot_fraction:.4f} > 0.005) and "
            f"{halo_fraction:.2%} of them fade through an elevated halo (0.6..0.92); "
            f"bloom is warranted"
        )
    elif hot_fraction <= 0.005:
        reason = (
            f"highlights are negligible (hotFraction {hot_fraction:.4f} <= 0.005); "
            f"no bloom"
        )
    else:
        reason = (
            f"highlights present but only {halo_fraction:.2%} bloom outward "
            f"(<= 50%); hard edges, no gradual halo, so no bloom"
        )

    return {
        "glow": glow,
        "hotFraction": round(hot_fraction, 6),
        "haloFraction": round(halo_fraction, 4),
        "reason": reason,
    }


def recommend_effects(png_path: Path) -> dict[str, Any]:
    """Run both detectors and recommend which post-fx the reference warrants."""
    blur = detect_background_blur(png_path)
    glow = detect_highlight_glow(png_path)
    return {
        "dof": blur["blurred"],
        "bloom": glow["glow"],
        "blur": blur,
        "glow": glow,
        "note": (
            "post-fx authorized only for effects the reference actually exhibits; "
            "applied on the presentation render only, never the Eye's evaluation render"
        ),
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--json", action="store_true", help="Print compact JSON")
    args = parser.parse_args(argv)

    try:
        result = recommend_effects(args.image.expanduser().resolve())
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
