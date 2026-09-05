#!/usr/bin/env python3
"""Create a side-by-side visual acceptance sheet for AI vision review.

The sheet is only evidence packaging. It does not score the images. the agent or
another AI vision reviewer should inspect the generated sheet and write the
score back with stage4_review/append_review.py.
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




def composite_over_checker(pixel: tuple[int, int, int, int], x: int, y: int) -> tuple[int, int, int]:
    red, green, blue, alpha = pixel
    background = 238 if ((x // 12 + y // 12) % 2 == 0) else 210
    mix = alpha / 255.0
    return (
        round(red * mix + background * (1 - mix)),
        round(green * mix + background * (1 - mix)),
        round(blue * mix + background * (1 - mix)),
    )


def resize_cover(
    width: int,
    height: int,
    pixels: list[tuple[int, int, int, int]],
    target_w: int,
    target_h: int,
) -> list[tuple[int, int, int]]:
    scale = max(target_w / width, target_h / height)
    scaled_w = max(1, round(width * scale))
    scaled_h = max(1, round(height * scale))
    offset_x = max(0, (scaled_w - target_w) // 2)
    offset_y = max(0, (scaled_h - target_h) // 2)
    output: list[tuple[int, int, int]] = []
    for y in range(target_h):
        source_y = min(height - 1, max(0, int((y + offset_y) / scale)))
        for x in range(target_w):
            source_x = min(width - 1, max(0, int((x + offset_x) / scale)))
            output.append(composite_over_checker(pixels[source_y * width + source_x], x, y))
    return output


def fill_rect(
    canvas: list[tuple[int, int, int]],
    width: int,
    x0: int,
    y0: int,
    rect_w: int,
    rect_h: int,
    color: tuple[int, int, int],
) -> None:
    height = len(canvas) // width
    for y in range(max(0, y0), min(height, y0 + rect_h)):
        row = y * width
        for x in range(max(0, x0), min(width, x0 + rect_w)):
            canvas[row + x] = color


def blit(
    canvas: list[tuple[int, int, int]],
    width: int,
    image: list[tuple[int, int, int]],
    image_w: int,
    x0: int,
    y0: int,
) -> None:
    image_h = len(image) // image_w
    height = len(canvas) // width
    for y in range(image_h):
        target_y = y0 + y
        if target_y < 0 or target_y >= height:
            continue
        for x in range(image_w):
            target_x = x0 + x
            if 0 <= target_x < width:
                canvas[target_y * width + target_x] = image[y * image_w + x]


# WHOLE-IMAGE ONLY. This composites the full reference beside the full render, at which scale a
# few-pixel tear is invisible to a human reviewer and to a VLM alike -- so a sheet that "looks right"
# is not evidence about small features. Feature crops belong here eventually; until then treat this
# as a macro-tier artifact. See grimoire/review/divine_eye_microscope.md.
def create_sheet(
    reference: Path,
    render: Path,
    out: Path,
    width: int,
    height: int,
    gutter: int,
) -> dict:
    ref_w, ref_h, ref_pixels = load_image(reference)
    ren_w, ren_h, ren_pixels = load_image(render)
    panel_w = width
    panel_h = height
    canvas_w = panel_w * 2 + gutter * 3
    header_h = 28
    canvas_h = panel_h + gutter * 2 + header_h
    canvas = [(246, 242, 236)] * (canvas_w * canvas_h)
    fill_rect(canvas, canvas_w, gutter, gutter, panel_w, header_h, (40, 45, 48))
    fill_rect(canvas, canvas_w, gutter * 2 + panel_w, gutter, panel_w, header_h, (40, 45, 48))
    fill_rect(canvas, canvas_w, gutter, gutter + header_h, panel_w, panel_h, (230, 230, 230))
    fill_rect(canvas, canvas_w, gutter * 2 + panel_w, gutter + header_h, panel_w, panel_h, (230, 230, 230))
    ref_panel = resize_cover(ref_w, ref_h, ref_pixels, panel_w, panel_h)
    ren_panel = resize_cover(ren_w, ren_h, ren_pixels, panel_w, panel_h)
    blit(canvas, canvas_w, ref_panel, panel_w, gutter, gutter + header_h)
    blit(canvas, canvas_w, ren_panel, panel_w, gutter * 2 + panel_w, gutter + header_h)
    fill_rect(canvas, canvas_w, panel_w + gutter + gutter // 2, gutter, max(2, gutter // 5), canvas_h - gutter * 2, (170, 146, 92))
    write_png_rgb(out, canvas_w, canvas_h, canvas)
    return {
        "comparisonImage": str(out.resolve()),
        "referenceImage": str(reference.resolve()),
        "renderScreenshot": str(render.resolve()),
        "layout": "left=reference,right=render",
        "panelWidth": panel_w,
        "panelHeight": panel_h,
        "note": "Send this image to AI vision for global, layer, and semantic feature scores; this script does not score.",
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--render", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--panel-width", type=int, default=720)
    parser.add_argument("--panel-height", type=int, default=720)
    parser.add_argument("--gutter", type=int, default=24)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = create_sheet(
            args.reference.expanduser().resolve(),
            args.render.expanduser().resolve(),
            args.out.expanduser().resolve(),
            max(128, args.panel_width),
            max(128, args.panel_height),
            max(6, args.gutter),
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else payload["comparisonImage"])
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
