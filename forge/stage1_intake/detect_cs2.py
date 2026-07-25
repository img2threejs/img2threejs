#!/usr/bin/env python3
"""Detect whether a reference image depicts a CS2 weapon/item.

This is a pre-analysis guard that runs BEFORE visual analysis. It uses
heuristic signals (aspect ratio, color distribution, known CS2 weapon
silhouette ratios) to route the pipeline to the CS2-specific track.

This script does NOT do visual analysis — it only reads file metadata
and performs basic image statistics. The agent's vision layer does the
real CS2 identification later in the pipeline.

Usage:
    python3 detect_cs2.py <image_path> [--threshold 0.3]
    python3 detect_cs2.py <image_path> --json
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Image format readers (pure stdlib, no PIL)
# ---------------------------------------------------------------------------

def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    return None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    idx = 2
    while idx + 9 < len(data):
        if data[idx] != 0xFF:
            idx += 1
            continue
        marker = data[idx + 1]
        idx += 2
        if marker in {0xD8, 0xD9}:
            continue
        if idx + 2 > len(data):
            return None
        length = struct.unpack(">H", data[idx : idx + 2])[0]
        if length < 2 or idx + length > len(data):
            return None
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if length >= 7:
                height, width = struct.unpack(">HH", data[idx + 3 : idx + 7])
                return width, height
        idx += length
    return None


def _gif_dimensions(data: bytes) -> tuple[int, int] | None:
    if data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    return None


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if chunk == b"VP8 " and len(data) >= 30:
        start = data.find(b"\x9d\x01\x2a")
        if start != -1 and start + 7 <= len(data):
            width, height = struct.unpack("<HH", data[start + 3 : start + 7])
            return width, height
    return None


def _read_dimensions(data: bytes) -> tuple[int, int] | None:
    return (
        _png_dimensions(data)
        or _jpeg_dimensions(data)
        or _gif_dimensions(data)
        or _webp_dimensions(data)
    )


# ---------------------------------------------------------------------------
# CS2 detection heuristics
# ---------------------------------------------------------------------------

# Known CS2 weapon aspect ratios (width / height) from inventory icons and
# standard workshop reference poses.  These are *approximate* — a real CS2
# detection needs the agent vision layer, but these ratios help the guard
# route early.
CS2_WEAPON_ASPECT_RATIOS: dict[str, tuple[float, float]] = {
    # knives — typically shown at ~15-45° angle
    "knife_long": (1.8, 4.0),     # Bowie, M9, Bayonet, Falchion
    "knife_curved": (0.8, 1.5),   # Karambit, Talon
    "knife_folder": (1.2, 2.5),   # Flip, Gut, Navaja
    "knife_butterfly": (0.6, 1.2),# Butterfly (open)
    # rifles — shown horizontally or at slight angle
    "rifle_horizontal": (2.5, 5.0),  # AK-47, M4A4, AWP
    # pistols — shown at ~20-45° angle
    "pistol_angle": (1.0, 2.0),   # Glock, USP, Deagle, AK pistol
    # gloves — shown as pair or single
    "gloves": (0.8, 1.5),
}


def _aspect_in_cs2_ranges(aspect: float) -> list[str]:
    """Return list of CS2 weapon categories whose aspect ratio matches."""
    matches = []
    for category, (lo, hi) in CS2_WEAPON_ASPECT_RATIOS.items():
        if lo <= aspect <= hi:
            matches.append(category)
    return matches


def _color_energy(data: bytes, sample_points: int = 200) -> dict[str, float]:
    """Sample raw byte energy at evenly-spaced points to detect dominant color ranges.

    This is a VERY rough heuristic — it reads raw file bytes, not decoded
    pixels.  For real detection, the agent vision layer handles this.
    """
    if len(data) < sample_points * 4:
        return {"dominant_range": "unknown", "energy": 0.0}

    step = len(data) // sample_points
    r_sum = g_sum = b_sum = 0
    for i in range(sample_points):
        offset = i * step
        if offset + 3 < len(data):
            r_sum += data[offset]
            g_sum += data[offset + 1]
            b_sum += data[offset + 2]

    r_avg = r_sum / sample_points
    g_avg = g_sum / sample_points
    b_avg = b_sum / sample_points

    # Detect common CS2 backgrounds: white/grey workshop renders
    if r_avg > 200 and g_avg > 200 and b_avg > 200:
        dominant = "light_background"
    elif r_avg < 50 and g_avg < 50 and b_avg < 50:
        dominant = "dark_background"
    elif abs(r_avg - g_avg) < 20 and abs(g_avg - b_avg) < 20:
        dominant = "neutral_grey"
    else:
        dominant = "colored"

    energy = math.sqrt(r_avg**2 + g_avg**2 + b_avg**2)
    return {"dominant_range": dominant, "energy": round(energy, 2)}


def detect_cs2_signals(path: Path) -> dict:
    """Analyze a reference image for CS2 weapon/item detection signals.

    Returns a dict with:
        - is_cs2_candidate: bool
        - confidence: 0.0-1.0
        - signals: list of detected signal descriptions
        - weapon_categories: possible CS2 weapon categories
        - dimensions: {width, height, aspectRatio}
        - color_profile: dominant color range info
        - note: explanation of detection limits
    """
    data = path.read_bytes()
    dims = _read_dimensions(data)
    signals: list[str] = []
    score = 0.0

    # Signal 1: Aspect ratio matches known CS2 weapon profiles
    if dims:
        width, height = dims
        aspect = width / height if height else 1.0
        categories = _aspect_in_cs2_ranges(aspect)
        if categories:
            score += 0.3
            signals.append(f"aspect_ratio_matches:{','.join(categories)}")
    else:
        aspect = None
        categories = []

    # Signal 2: Image dimensions suggest inventory/workshop render
    if dims:
        w, h = dims
        # CS2 inventory icons are typically 512×512 or 256×256
        if w == h and w in {256, 512, 1024}:
            score += 0.2
            signals.append("square_format_inventory_icon")
        # Workshop renders are often wide format
        elif w > h and w >= 512:
            score += 0.1
            signals.append("wide_format_workshop_render")

    # Signal 3: Color profile suggests workshop render
    color = _color_energy(data)
    if color["dominant_range"] == "light_background":
        score += 0.15
        signals.append("light_background_workshop_style")
    elif color["dominant_range"] == "neutral_grey":
        score += 0.1
        signals.append("neutral_background_steam_style")

    # Signal 4: File size suggests rendered image (not photo)
    file_size = len(data)
    if 50_000 < file_size < 5_000_000:
        score += 0.05
        signals.append("typical_render_file_size")

    is_cs2_candidate = score >= 0.3

    return {
        "path": str(path),
        "is_cs2_candidate": is_cs2_candidate,
        "confidence": min(round(score, 2), 1.0),
        "signals": signals,
        "weapon_categories": categories,
        "dimensions": {
            "width": dims[0] if dims else None,
            "height": dims[1] if dims else None,
            "aspectRatio": round(aspect, 3) if aspect else None,
        },
        "color_profile": color,
        "note": (
            "This is a heuristic pre-analysis guard using file metadata and "
            "basic byte statistics. It does NOT perform visual analysis. "
            "The agent's vision layer (Step 1) performs definitive CS2 "
            "identification using the M.C.M.T framework in "
            "grimoire/intake/cs2_technical_analysis.md"
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", type=Path, help="Path to reference image")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--threshold", type=float, default=0.3, help="CS2 candidate threshold (default: 0.3)")
    args = parser.parse_args(argv)

    if not args.image.exists():
        print(json.dumps({"error": f"File not found: {args.image}"}))
        return 1

    result = detect_cs2_signals(args.image)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        status = "CS2_CANDIDATE" if result["is_cs2_candidate"] else "NON_CS2"
        print(f"Status: {status}")
        print(f"Confidence: {result['confidence']}")
        print(f"Signals: {', '.join(result['signals']) or 'none'}")
        if result["weapon_categories"]:
            print(f"Weapon categories: {', '.join(result['weapon_categories'])}")
        dims = result["dimensions"]
        if dims["width"]:
            print(f"Dimensions: {dims['width']}×{dims['height']} (aspect {dims['aspectRatio']})")
        print(f"\n{result['note']}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
