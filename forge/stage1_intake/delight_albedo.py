#!/usr/bin/env python3
"""Approximate a neutral (de-lit) albedo from a single reference photo.

This is an approximation, not true inverse rendering. A single photo bakes
together albedo, direct light, ambient occlusion, and specular response
into one signal, and there is no way to fully separate those from pixels
alone. This script applies a per-pixel normalization against a low-frequency
luminance estimate (a box-blur "lighting" proxy): pixels darker than their
local neighborhood get brightened, pixels brighter than their neighborhood
get darkened, pulling the image toward flat, even lighting. Strong specular
hotspots, deep occlusion shadows, and directional cues that vary faster than
the blur radius will not be fully removed. Always review the output next to
the source image before treating it as a projection albedo.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
from image_codec import load_image  # noqa: E402


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def clamp01(value: float) -> float:
    return clamp(value, 0.0, 1.0)


def srgb_luma(rgb: tuple[int, int, int]) -> float:
    red, green, blue = rgb
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255.0


def percentile(values: list[float], fraction: float, fallback: float = 0.0) -> float:
    if not values:
        return fallback
    ordered = sorted(values)
    index = int(round(clamp01(fraction) * (len(ordered) - 1)))
    return ordered[index]






def write_png_rgba(path: Path, width: int, height: int, rgba: bytes) -> None:
    if len(rgba) != width * height * 4:
        raise ValueError("RGBA payload has the wrong size")
    path.parent.mkdir(parents=True, exist_ok=True)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind)
        checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    scanlines = bytearray()
    stride = width * 4
    for y in range(height):
        scanlines.append(0)
        scanlines.extend(rgba[y * stride : (y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(
        PNG_SIGNATURE
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(scanlines), level=6))
        + chunk(b"IEND", b"")
    )




def blur_scalar(values: list[float], width: int, height: int, radius: int) -> list[float]:
    if radius <= 0:
        return values[:]
    horizontal = [0.0] * (width * height)
    for y in range(height):
        row_offset = y * width
        running = 0.0
        count = 0
        for x in range(-radius, width + radius):
            if 0 <= x < width:
                running += values[row_offset + x]
                count += 1
            remove = x - radius * 2 - 1
            if 0 <= remove < width:
                running -= values[row_offset + remove]
                count -= 1
            write_x = x - radius
            if 0 <= write_x < width:
                horizontal[row_offset + write_x] = running / max(1, count)
    vertical = [0.0] * (width * height)
    for x in range(width):
        running = 0.0
        count = 0
        for y in range(-radius, height + radius):
            if 0 <= y < height:
                running += horizontal[y * width + x]
                count += 1
            remove = y - radius * 2 - 1
            if 0 <= remove < height:
                running -= horizontal[remove * width + x]
                count -= 1
            write_y = y - radius
            if 0 <= write_y < height:
                vertical[write_y * width + x] = running / max(1, count)
    return vertical


def delight(
    width: int,
    height: int,
    pixels: list[tuple[int, int, int, int]],
    strength: float,
    blur_radius: int,
) -> tuple[bytes, dict[str, Any]]:
    lumas = [srgb_luma(pixel[:3]) for pixel in pixels]
    target = percentile(lumas, 0.5, 0.5)
    low_frequency = blur_scalar(lumas, width, height, blur_radius)
    out = bytearray()
    corrections: list[float] = []
    for (red, green, blue, alpha), low in zip(pixels, low_frequency):
        shade = clamp(low, 0.05, 1.0)
        raw_scale = target / shade
        # strength blends between no correction (1.0) and the full normalization
        scale = 1.0 + (raw_scale - 1.0) * clamp01(strength)
        scale = clamp(scale, 0.35, 2.6)
        corrections.append(scale)
        out.extend(
            (
                round(clamp(red * scale, 0, 255)),
                round(clamp(green * scale, 0, 255)),
                round(clamp(blue * scale, 0, 255)),
                alpha,
            )
        )
    luma_before_range = percentile(lumas, 0.95, 0.8) - percentile(lumas, 0.05, 0.2)
    stats = {
        "targetLuma": round(target, 4),
        "blurRadius": blur_radius,
        "lumaRangeBefore": round(luma_before_range, 4),
        "meanCorrectionScale": round(sum(corrections) / max(1, len(corrections)), 4),
        "maxCorrectionScale": round(max(corrections, default=1.0), 4),
        "minCorrectionScale": round(min(corrections, default=1.0), 4),
    }
    return bytes(out), stats


def estimate_confidence(stats: dict[str, Any], strength: float, warnings: list[str]) -> tuple[float, list[str]]:
    notes: list[str] = []
    luma_range = float(stats.get("lumaRangeBefore", 0.4))
    # a very large baked lighting range means more got corrected but also more
    # residual error is likely, since the box blur is only a crude lighting proxy
    range_penalty = clamp01((luma_range - 0.35) * 0.6)
    strength_bonus = clamp01(strength) * 0.15
    confidence = clamp01(0.55 - range_penalty * 0.25 + strength_bonus - min(0.1, len(warnings) * 0.04))
    confidence = min(0.72, confidence)  # single-image de-lighting is always capped
    notes.append("single-image de-lighting cannot separate true albedo from baked light/AO/specular; confidence is capped")
    if luma_range > 0.5:
        notes.append("wide baked lighting range detected; expect visible residual shading after correction")
    return round(confidence, 3), notes


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="Output de-lit PNG path")
    parser.add_argument("--report", type=Path, help="Write the JSON report to this path (also printed to stdout)")
    parser.add_argument(
        "--strength",
        type=float,
        default=0.6,
        help="0.0 = no correction (passthrough), 1.0 = full normalization against the blurred luminance proxy (default 0.6)",
    )
    parser.add_argument(
        "--blur-radius",
        type=int,
        default=0,
        help="Box-blur radius in pixels for the low-frequency lighting estimate; 0 = auto from image size",
    )
    args = parser.parse_args(argv)

    image = args.image.expanduser().resolve()
    if not image.exists():
        parser.error(f"{image} does not exist")
    out_path = args.out.expanduser().resolve()

    try:
        width, height, pixels, load_warnings = load_image(image)
        blur_radius = args.blur_radius if args.blur_radius > 0 else max(6, min(48, min(width, height) // 20))
        strength = clamp01(args.strength)
        delit_rgba, stats = delight(width, height, pixels, strength, blur_radius)
        write_png_rgba(out_path, width, height, delit_rgba)

        confidence, confidence_notes = estimate_confidence(stats, strength, load_warnings)
        report = {
            "delightReference": {
                "version": "1.0",
                "sourceImage": str(image),
                "outputImage": str(out_path),
                "method": (
                    "per-pixel normalization against a box-blurred luminance proxy; an approximation of "
                    "de-lighting, not physically based inverse rendering or true light/albedo separation"
                ),
                "strength": strength,
                "confidence": confidence,
                "stats": stats,
                "limitations": [
                    "this is an approximation, not true inverse rendering; it cannot recover ground-truth albedo",
                    "sharp specular highlights and hard shadow edges narrower than the blur radius will remain baked in",
                    "deep occlusion shadows (creases, undercuts) are only partially lifted",
                    "must be reviewed visually next to the source image before use as a projection albedo",
                ]
                + confidence_notes
                + load_warnings,
                "note": (
                    "If shadows or highlights are still visible in the output, try a larger --strength or a "
                    "smaller --blur-radius so the correction responds to tighter lighting gradients, then "
                    "re-review; this script does not know when the correction is visually sufficient."
                ),
            }
        }
        text = json.dumps(report, indent=2, ensure_ascii=False)
        if args.report:
            report_path = args.report.expanduser().resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    import sys
    from pathlib import Path

    try:
        sys.path.insert(0, str(next(
            parent / "forge" / "_shared"
            for parent in Path(__file__).resolve().parents
            if (parent / "forge" / "_shared" / "cli_run.py").is_file()
        )))
        from cli_run import run_entry
    except (ImportError, StopIteration):
        # vendored/fixture copies without the forge runtime: run bare, no pipe handling
        def run_entry(main_fn, argv=None):
            return main_fn(sys.argv[1:] if argv is None else argv)

    raise SystemExit(run_entry(main))
