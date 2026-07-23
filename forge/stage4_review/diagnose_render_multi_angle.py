#!/usr/bin/env python3
"""Deterministic Phase-3 §3.2 multi-angle "degenerate-view" check.

A flat plane faking a 3D volume looks convincing from the single reference
camera angle, but its silhouette AREA collapses when the camera orbits: a
billboard seen edge-on nearly vanishes. This module exploits that. Given a set
of already-rendered PNGs of the SAME object from different camera angles, it
measures each frame's foreground silhouette area (as a fraction of the frame)
and flags "degenerate-view" when a non-reference angle's area collapses far
below the reference angle's area.

This check is deterministic and costs zero tokens: it is pure pixel arithmetic
over foreground masks, reusing the stage1 intake loader/segmenter.

Scope: this module only ANALYZES already-captured PNGs. Driving an actual
browser / renderer to PRODUCE the orbit PNGs is a separate concern handled
elsewhere in the pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stage1_intake"))
from extract_pbr_evidence import load_image, build_foreground_mask, mask_bbox  # noqa: E402


def silhouette_area_fraction(png_path: Path) -> float:
    """Return the fraction of the frame occupied by the object's silhouette.

    Loads the image, segments the foreground from the background, and returns
    (foreground pixel count / total pixels). ``mask_bbox`` is imported and kept
    available for callers/tests that want the tight silhouette box, but the area
    fraction itself is computed from the full mask so a small-but-wide sliver
    is scored purely by how much of the frame it actually fills.
    """
    path = Path(png_path)
    width, height, pixels, _warnings = load_image(path)
    mask, _diag, mask_warnings = build_foreground_mask(width, height, pixels)
    total = len(mask)
    if total <= 0:
        return 0.0
    # HARDENING (lead, from the multiangle worker's finding): build_foreground_mask has
    # a <3.5%-coverage safety fallback that INVERTS a tiny mask to "all opaque pixels"
    # (coverage → ~1.0). For degenerate-view detection that is exactly backwards — a plane
    # orbited edge-on yields a near-zero silhouette that MUST read as collapsed, not full.
    # So when the fallback fired, report near-zero (the true, pre-inversion silhouette).
    if any("tiny" in str(w).lower() for w in mask_warnings):
        return 0.0
    foreground = sum(1 for value in mask if value)
    return foreground / total


def analyze_angles(
    reference_png: Path,
    orbit_pngs: list[Path],
    collapse_ratio: float = 0.15,
) -> dict[str, Any]:
    """Compare each orbit angle's silhouette area against the reference angle.

    For every orbit angle, ``ratio = orbit_area / reference_area``. When the
    reference area is 0 the ratio is treated as 0.0 (divide-by-zero guard). Any
    orbit angle whose ratio is below ``collapse_ratio`` is flagged degenerate:
    the object nearly vanished from that viewpoint, which is what a flat plane
    faking a volume does when orbited.
    """
    reference_area = silhouette_area_fraction(Path(reference_png))
    angles: list[dict[str, Any]] = []
    any_degenerate = False
    for orbit in orbit_pngs:
        orbit_area = silhouette_area_fraction(Path(orbit))
        ratio = 0.0 if reference_area <= 0.0 else orbit_area / reference_area
        degenerate = ratio < collapse_ratio
        if degenerate:
            any_degenerate = True
        angles.append(
            {
                "path": str(orbit),
                "areaFraction": orbit_area,
                "ratio": ratio,
                "degenerate": degenerate,
            }
        )
    return {
        "referenceAreaFraction": reference_area,
        "angles": angles,
        "degenerate": any_degenerate,
        "collapseRatio": collapse_ratio,
        "note": (
            "deterministic; zero token; a flat-plane-faking-a-volume collapses "
            "in silhouette area when orbited"
        ),
    }


def _format_summary(result: dict[str, Any]) -> str:
    lines = [
        f"reference area fraction: {result['referenceAreaFraction']:.4f}",
        f"collapse ratio threshold: {result['collapseRatio']}",
    ]
    for angle in result["angles"]:
        flag = "DEGENERATE" if angle["degenerate"] else "ok"
        lines.append(
            f"  [{flag}] {angle['path']} "
            f"area={angle['areaFraction']:.4f} ratio={angle['ratio']:.4f}"
        )
    verdict = "DEGENERATE-VIEW DETECTED" if result["degenerate"] else "no degenerate view"
    lines.append(f"verdict: {verdict}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True, help="reference-angle PNG")
    parser.add_argument(
        "--orbit",
        type=Path,
        action="append",
        default=[],
        help="orbit-angle PNG (repeat for each angle)",
    )
    parser.add_argument("--collapse-ratio", type=float, default=0.15)
    parser.add_argument("--json", action="store_true", help="print JSON instead of a summary")
    args = parser.parse_args(argv)

    try:
        result = analyze_angles(args.reference, args.orbit, args.collapse_ratio)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(_format_summary(result))
        return 1 if result["degenerate"] else 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
