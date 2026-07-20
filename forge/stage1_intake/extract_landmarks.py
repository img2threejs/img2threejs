#!/usr/bin/env python3
"""Overlay a labelled proportion/landmark guide grid on a reference image and scaffold anatomy.

Draws head-unit ticks, a rule-of-thirds grid, a center symmetry axis, default face-line
guides (hairline/eye/nose/mouth), and default shoulder/hip lines onto a copy of the
reference (see docs/UPGRADE_PLAN.md 5.3-5.4 and grimoire/character/reconstruction.md),
then emits an anatomy skeleton JSON for the agent to fill from what the overlay reveals.
The drawn lines are generic starting positions, not measurements - the agent's vision
supplies the actual proportions, pose, and landmark coordinates.
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


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

MARGIN = 44

COLOR_THIRDS = (150, 150, 150)
COLOR_HEAD_UNIT = (60, 120, 220)
COLOR_HAIRLINE = (210, 80, 210)
COLOR_EYELINE = (230, 60, 60)
COLOR_NOSEBASE = (240, 150, 30)
COLOR_MOUTHLINE = (40, 170, 90)
COLOR_SHOULDER = (30, 140, 200)
COLOR_HIP = (170, 110, 40)
COLOR_CENTER = (20, 20, 20)

FONT_3X5 = {
    "0": ["###", "#.#", "#.#", "#.#", "###"],
    "1": [".#.", "##.", ".#.", ".#.", "###"],
    "2": ["###", "..#", "###", "#..", "###"],
    "3": ["###", "..#", "###", "..#", "###"],
    "4": ["#.#", "#.#", "###", "..#", "..#"],
    "5": ["###", "#..", "###", "..#", "###"],
    "6": ["###", "#..", "###", "#.#", "###"],
    "7": ["###", "..#", "..#", "..#", "..#"],
    "8": ["###", "#.#", "###", "#.#", "###"],
    "9": ["###", "#.#", "###", "..#", "###"],
    "H": ["#.#", "#.#", "###", "#.#", "#.#"],
    "E": ["###", "#..", "##.", "#..", "###"],
    "N": ["#.#", "##.", "#.#", ".##", "#.#"],
    "M": ["#.#", "###", "###", "#.#", "#.#"],
    "S": [".##", "#..", ".#.", "..#", "##."],
    "P": ["##.", "#.#", "##.", "#..", "#.."],
    "C": [".##", "#..", "#..", "#..", ".##"],
}


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
    width = height = bit_depth = color_type = interlace = None
    idat = bytearray()
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
                row[index] = (row[index] + paeth_predictor(left, up, up_left)) & 0xFF
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


def write_png_rgb(path: Path, width: int, height: int, pixels: list[tuple[int, int, int]]) -> None:
    if len(pixels) != width * height:
        raise ValueError("pixel payload has the wrong size")

    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind)
        checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        for red, green, blue in pixels[y * width : (y + 1) * width]:
            scanlines.extend((red, green, blue))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(scanlines), level=6))
        + chunk(b"IEND", b"")
    )


def load_image(path: Path) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    try:
        return read_png(path)
    except Exception as direct_error:
        sips = shutil.which("sips")
        if not sips:
            raise ValueError(f"could not decode {path.name} as PNG and sips is unavailable: {direct_error}") from direct_error
        with tempfile.TemporaryDirectory() as tmpdir:
            converted = Path(tmpdir) / "converted.png"
            result = subprocess.run(
                [sips, "-s", "format", "png", str(path), "--out", str(converted)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise ValueError(result.stderr.strip() or result.stdout.strip() or "sips conversion failed")
            return read_png(converted)


def composite_over_white(pixel: tuple[int, int, int, int]) -> tuple[int, int, int]:
    red, green, blue, alpha = pixel
    mix = alpha / 255.0
    return (
        round(red * mix + 255 * (1 - mix)),
        round(green * mix + 255 * (1 - mix)),
        round(blue * mix + 255 * (1 - mix)),
    )


def set_pixel(canvas: list[tuple[int, int, int]], width: int, height: int, x: int, y: int, color: tuple[int, int, int]) -> None:
    if 0 <= x < width and 0 <= y < height:
        canvas[y * width + x] = color


def draw_glyph(
    canvas: list[tuple[int, int, int]],
    width: int,
    height: int,
    x0: int,
    y0: int,
    glyph: list[str],
    color: tuple[int, int, int],
    scale: int,
) -> None:
    for row_index, row in enumerate(glyph):
        for col_index, mark in enumerate(row):
            if mark != "#":
                continue
            for dy in range(scale):
                for dx in range(scale):
                    set_pixel(canvas, width, height, x0 + col_index * scale + dx, y0 + row_index * scale + dy, color)


def draw_text(
    canvas: list[tuple[int, int, int]],
    width: int,
    height: int,
    x0: int,
    y0: int,
    text: str,
    color: tuple[int, int, int],
    scale: int = 2,
) -> None:
    cursor_x = x0
    for character in text:
        glyph = FONT_3X5.get(character)
        if glyph:
            draw_glyph(canvas, width, height, cursor_x, y0, glyph, color, scale)
        cursor_x += 3 * scale + scale


def draw_hline(
    canvas: list[tuple[int, int, int]],
    width: int,
    height: int,
    y: int,
    x_start: int,
    x_end: int,
    color: tuple[int, int, int],
    dash: int = 0,
) -> None:
    if not (0 <= y < height):
        return
    row = y * width
    for x in range(max(0, x_start), min(width, x_end)):
        if dash and (x // dash) % 2 == 1:
            continue
        canvas[row + x] = color


def draw_vline(
    canvas: list[tuple[int, int, int]],
    width: int,
    height: int,
    x: int,
    y_start: int,
    y_end: int,
    color: tuple[int, int, int],
    dash: int = 0,
) -> None:
    if not (0 <= x < width):
        return
    for y in range(max(0, y_start), min(height, y_end)):
        if dash and (y // dash) % 2 == 1:
            continue
        canvas[y * width + x] = color


def build_overlay(image: Path, overlay_path: Path, heads: int) -> dict:
    width, height, pixels = load_image(image)
    base = [composite_over_white(pixel) for pixel in pixels]
    canvas_w = width + MARGIN
    canvas_h = height
    canvas: list[tuple[int, int, int]] = [(255, 255, 255)] * (canvas_w * canvas_h)
    for y in range(height):
        source_row = y * width
        dest_row = y * canvas_w + MARGIN
        canvas[dest_row : dest_row + width] = base[source_row : source_row + width]

    for fraction in (1.0 / 3, 2.0 / 3):
        y = round(fraction * height)
        draw_hline(canvas, canvas_w, canvas_h, y, MARGIN, canvas_w, COLOR_THIRDS, dash=6)
    for fraction in (1.0 / 3, 2.0 / 3):
        x = MARGIN + round(fraction * width)
        draw_vline(canvas, canvas_w, canvas_h, x, 0, height, COLOR_THIRDS, dash=6)

    center_x = MARGIN + width // 2
    draw_vline(canvas, canvas_w, canvas_h, center_x, 0, height, COLOR_CENTER)
    draw_text(canvas, canvas_w, canvas_h, 4, max(0, min(height - 6, height // 2 - 3)), "C", COLOR_CENTER)

    step = height / heads
    for i in range(1, heads):
        y = round(i * step)
        draw_hline(canvas, canvas_w, canvas_h, y, MARGIN, canvas_w, COLOR_HEAD_UNIT, dash=10)
        draw_text(canvas, canvas_w, canvas_h, 4, max(0, y - 3), str(i), COLOR_HEAD_UNIT)

    band = step
    face_lines = [
        ("H", COLOR_HAIRLINE, 0.05),
        ("E", COLOR_EYELINE, 0.50),
        ("N", COLOR_NOSEBASE, 0.65),
        ("M", COLOR_MOUTHLINE, 0.80),
    ]
    for label, color, fraction in face_lines:
        y = round(fraction * band)
        draw_hline(canvas, canvas_w, canvas_h, y, MARGIN, MARGIN + width, color, dash=4)
        draw_text(canvas, canvas_w, canvas_h, 4, max(0, y - 3), label, color)

    shoulder_y = round(0.28 * height)
    hip_y = round(0.55 * height)
    draw_hline(canvas, canvas_w, canvas_h, shoulder_y, MARGIN, canvas_w, COLOR_SHOULDER, dash=14)
    draw_text(canvas, canvas_w, canvas_h, 4, max(0, shoulder_y - 3), "S", COLOR_SHOULDER)
    draw_hline(canvas, canvas_w, canvas_h, hip_y, MARGIN, canvas_w, COLOR_HIP, dash=14)
    draw_text(canvas, canvas_w, canvas_h, 4, max(0, hip_y - 3), "P", COLOR_HIP)

    write_png_rgb(overlay_path, canvas_w, canvas_h, canvas)
    return {
        "overlayImage": str(overlay_path),
        "imageWidth": width,
        "imageHeight": height,
        "headUnitCount": heads,
        "legend": {
            "C": "center symmetry axis",
            "1..N": "head-unit horizontal ticks (blue, dashed)",
            "H": "hairline guide (default fraction of the first head-unit band)",
            "E": "eye line guide",
            "N": "nose base guide",
            "M": "mouth line guide",
            "S": "shoulder line guide (default fraction, adjust to observed pose)",
            "P": "hip line guide (default fraction, adjust to observed pose)",
            "grayDashed": "rule-of-thirds compositional grid",
        },
        "note": "Guide lines are generic starting positions, not measurements. Read the overlay "
        "against the actual reference and fill anatomy with observed normalized values.",
    }


def make_anatomy_skeleton(style_heads: float) -> dict:
    joint_names = [
        "neck",
        "leftShoulder",
        "rightShoulder",
        "leftElbow",
        "rightElbow",
        "leftWrist",
        "rightWrist",
        "leftHip",
        "rightHip",
        "leftKnee",
        "rightKnee",
        "leftAnkle",
        "rightAnkle",
    ]
    return {
        "styleHeads": style_heads,
        "proportions": {
            "headUnit": None,
            "torso": None,
            "legs": None,
            "shoulderWidth": None,
            "hipWidth": None,
        },
        "pose": {
            "type": "",
            "jointAngles": {name: [0, 0, 0] for name in joint_names},
        },
        "faceLandmarks": {
            "hairline": None,
            "eyeLine": None,
            "eyeSpacing": None,
            "noseBase": None,
            "mouthLine": None,
            "earTop": None,
            "earBottom": None,
        },
        "features": [],
        "confidence": 0.0,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        help="Output anatomy skeleton JSON path (default: <image-stem>-anatomy.json next to the image)",
    )
    parser.add_argument(
        "--overlay",
        type=Path,
        help="Output overlay PNG path (default: <image-stem>-landmarks.png next to the image)",
    )
    parser.add_argument(
        "--style-heads",
        type=float,
        default=6.0,
        help="Initial head-unit estimate driving the overlay grid "
        "(realistic ~7.5, stylized ~5-6, chibi/figurine ~2-3); refine after visual inspection",
    )
    parser.add_argument(
        "--heads",
        type=int,
        help="Override number of head-unit tick lines drawn (default: round(--style-heads))",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing outputs")
    args = parser.parse_args(argv)

    image = args.image.expanduser().resolve()
    if not image.exists():
        parser.error(f"{image} does not exist")
    overlay_path = (args.overlay or image.with_name(f"{image.stem}-landmarks.png")).expanduser().resolve()
    out_path = (args.out or image.with_name(f"{image.stem}-anatomy.json")).expanduser().resolve()
    if not args.force:
        for existing in (overlay_path, out_path):
            if existing.exists():
                parser.error(f"{existing} already exists; use --force to overwrite")
    heads = args.heads or max(1, round(args.style_heads))

    try:
        overlay_meta = build_overlay(image, overlay_path, heads)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    payload = {
        "sourceImage": str(image),
        "overlayImage": str(overlay_path),
        "overlayLegend": overlay_meta["legend"],
        "anatomy": make_anatomy_skeleton(args.style_heads),
        "authoringInstruction": (
            "Open overlayImage and read the reference against its head-unit ticks, thirds grid, "
            "face-line guides, shoulder/hip lines, and center axis. Replace every null/placeholder "
            "value in anatomy with normalized coordinates or joint angles actually observed; the "
            "drawn guide lines are generic starting positions, not measurements."
        ),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
