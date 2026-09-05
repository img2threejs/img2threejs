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
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
from image_codec import decode_image as load_image  # noqa: E402


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
