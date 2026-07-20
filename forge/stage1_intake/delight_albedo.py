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
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path
from typing import Any


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


def paeth_predictor(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def read_png(path: Path) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG file")
    cursor = len(PNG_SIGNATURE)
    width = height = bit_depth = color_type = None
    idat = bytearray()
    interlace = 0
    while cursor + 8 <= len(data):
        length = struct.unpack(">I", data[cursor : cursor + 4])[0]
        chunk_type = data[cursor + 4 : cursor + 8]
        chunk_data = data[cursor + 8 : cursor + 8 + length]
        cursor += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", chunk_data)
        elif chunk_type == b"IDAT":
            idat.extend(chunk_data)
        elif chunk_type == b"IEND":
            break
    if width is None or height is None or bit_depth != 8 or interlace != 0:
        raise ValueError("unsupported PNG; expected 8-bit non-interlaced image")
    channels_by_type = {0: 1, 2: 3, 4: 2, 6: 4}
    if color_type not in channels_by_type:
        raise ValueError("unsupported PNG color type; convert to RGB/RGBA first")
    channels = channels_by_type[color_type]
    row_bytes = width * channels
    raw = zlib.decompress(bytes(idat))
    rows: list[bytearray] = []
    offset = 0
    previous = bytearray(row_bytes)
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        row = bytearray(raw[offset : offset + row_bytes])
        offset += row_bytes
        for index in range(row_bytes):
            left = row[index - channels] if index >= channels else 0
            up = previous[index]
            up_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                row[index] = (row[index] + left) & 0xFF
            elif filter_type == 2:
                row[index] = (row[index] + up) & 0xFF
            elif filter_type == 3:
                row[index] = (row[index] + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                predictor = paeth_predictor(left, up, up_left)
                row[index] = (row[index] + predictor) & 0xFF
            elif filter_type != 0:
                raise ValueError(f"unsupported PNG filter {filter_type}")
        rows.append(row)
        previous = row
    pixels: list[tuple[int, int, int, int]] = []
    for row in rows:
        for x in range(width):
            base = x * channels
            if color_type == 0:
                gray = row[base]
                pixels.append((gray, gray, gray, 255))
            elif color_type == 2:
                pixels.append((row[base], row[base + 1], row[base + 2], 255))
            elif color_type == 4:
                gray = row[base]
                pixels.append((gray, gray, gray, row[base + 1]))
            elif color_type == 6:
                pixels.append((row[base], row[base + 1], row[base + 2], row[base + 3]))
    return width, height, pixels


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


def load_image(path: Path) -> tuple[int, int, list[tuple[int, int, int, int]], list[str]]:
    warnings: list[str] = []
    try:
        return (*read_png(path), warnings)
    except Exception as direct_error:
        sips = shutil.which("sips")
        if not sips:
            raise ValueError(
                f"could not decode {path.name} as PNG and macOS sips is unavailable: {direct_error}"
            ) from direct_error
        with tempfile.TemporaryDirectory() as tmpdir:
            converted = Path(tmpdir) / "converted.png"
            command = [sips, "-s", "format", "png", str(path), "--out", str(converted)]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise ValueError(result.stderr.strip() or result.stdout.strip() or "sips conversion failed")
            warnings.append("source image was converted to PNG with macOS sips before pixel extraction")
            width, height, pixels = read_png(converted)
            return width, height, pixels, warnings


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
    raise SystemExit(main(sys.argv[1:]))
