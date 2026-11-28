#!/usr/bin/env python3
"""Side-by-side [reference | render] sheets that obey the Jev image limits (each side 280..1568 px).

Panels are letterboxed (aspect preserved, never stretched) onto a flat grey canvas. Two panels is the
likeness sheet; ``--panel`` may repeat for the N-way candidate pick sheet (reference first). Uses the
selected bpy python for decoding/scaling/encoding, so no Pillow dependency is introduced.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _HERE]
sys.path.append(_HERE)

import argparse  # noqa: E402
from pathlib import Path  # noqa: E402

import _common as C  # noqa: E402

JEV_MIN_SIDE = 280
JEV_MAX_SIDE = 1568
GREY = 110 / 255.0


def layout(panel_count, aspect, max_side=JEV_MAX_SIDE, min_side=JEV_MIN_SIDE):
    """(panel_w, panel_h) so the sheet is panel_count*panel_w by panel_h within [min_side, max_side].

    ``aspect`` is the target panel height/width. The largest panel that fits is chosen; a sheet that
    cannot satisfy the limits (too many panels or an extreme aspect) raises instead of silently
    shipping an image the provider would reject or downscale.
    """
    if panel_count < 2:
        raise C.TexturedError("a comparison sheet needs at least two panels (reference + render)")
    if not aspect > 0:
        raise C.TexturedError("panel aspect must be positive")
    width = max_side // panel_count
    height = round(width * aspect)
    if height > max_side:
        height = max_side
        width = round(height / aspect)
    if width * panel_count < min_side or height < min_side:
        raise C.TexturedError(f"cannot lay out {panel_count} panels of aspect {aspect:.3f} within {min_side}..{max_side} px")
    if width * panel_count > max_side or height > max_side:
        raise C.TexturedError("layout exceeds the Jev image limits")
    return width, height


def fit_box(src_w, src_h, box_w, box_h):
    """Contain-fit (w, h, x_offset, y_offset) of a source inside a box."""
    scale = min(box_w / src_w, box_h / src_h)
    w, h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    return w, h, (box_w - w) // 2, (box_h - h) // 2


def compose(panels, out, aspect=None):
    import bpy
    import numpy as np
    sizes = []
    images = []
    for path in panels:
        image = bpy.data.images.load(str(path))
        image.colorspace_settings.name = "Non-Color"  # raw pass-through: no colour management round trip
        images.append(image)
        sizes.append(tuple(image.size))
    first_w, first_h = sizes[-1]
    panel_aspect = aspect or (first_h / first_w)
    width, height = layout(len(panels), panel_aspect)
    canvas = np.full((height, width * len(panels), 4), GREY, dtype=np.float32)
    canvas[..., 3] = 1.0
    for index, image in enumerate(images):
        src_w, src_h = image.size
        w, h, dx, dy = fit_box(src_w, src_h, width, height)
        image.scale(w, h)
        pixels = np.empty(w * h * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        pixels = pixels.reshape(h, w, 4)  # bpy rows run bottom-up, matching the canvas below
        alpha = pixels[..., 3:4]
        tile = pixels[..., :3] * alpha + GREY * (1.0 - alpha)
        # canvas row 0 is the bottom row, so dy counts from the bottom of the panel
        canvas[dy:dy + h, index * width + dx:index * width + dx + w, :3] = tile
    result = bpy.data.images.new("sheet", width * len(panels), height, alpha=False, float_buffer=False)
    result.colorspace_settings.name = "Non-Color"
    result.pixels.foreach_set(canvas.reshape(-1))
    result.filepath_raw = str(out)
    result.file_format = "PNG"
    out.parent.mkdir(parents=True, exist_ok=True)
    result.save()
    return width * len(panels), height, width, height, sizes


def build_parser():
    parser = argparse.ArgumentParser(description="Compose a [reference | render] sheet within the Jev image limits")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--gen", type=int, help="gen-N: reference | gen-N/renders/<view>.png -> gen-N/sheet-<view>.png")
    parser.add_argument("--reference", help="reference image (png/jpg)")
    parser.add_argument("--view", default="front", help="render view name for --gen (default front)")
    parser.add_argument("--panel", action="append", default=[], help="explicit panel image, left to right (repeatable; "
                        "with --reference the reference is prepended)")
    parser.add_argument("--out", help="explicit sheet path")
    return parser


def main(argv):
    argv, inside = C.strip_flag(argv)
    args = build_parser().parse_args(argv)
    workspace = C.resolve_workspace(args.workspace)
    panels = []
    if args.reference:
        panels.append(Path(args.reference).expanduser().resolve())
    if args.gen is not None:
        directory = C.gen_dir(workspace, args.gen)
        C.safe_name(args.view, "--view")
        panels.append(directory / "renders" / f"{args.view}.png")
        out = Path(args.out).expanduser().resolve() if args.out else directory / f"sheet-{args.view}.png"
    else:
        out = Path(args.out).expanduser().resolve() if args.out else None
    panels.extend(Path(p).expanduser().resolve() for p in args.panel)
    if out is None:
        raise C.TexturedError("pass --out when not using --gen")
    if len(panels) < 2:
        raise C.TexturedError("need a reference and at least one render")
    for path in panels:
        if not path.is_file():
            raise C.TexturedError(f"panel image not found: {path}")
    if not inside:
        code = C.delegate_to_bpy(os.path.abspath(__file__), argv)
        if code is not None:
            return code
    sheet_w, sheet_h, panel_w, panel_h, sources = compose(panels, out)
    C.write_json(out.with_suffix(".json"), C.receipt(
        "sheet", generation=args.gen, output=C.workspace_relative(workspace, out), size=[sheet_w, sheet_h],
        panelSize=[panel_w, panel_h], jevLimits=[JEV_MIN_SIDE, JEV_MAX_SIDE],
        panels=[{"path": C.workspace_relative(workspace, p), "sourceSize": list(s), "sha256": C.sha256_file(p)}
                for p, s in zip(panels, sources)], sha256=C.sha256_file(out)))
    print(f"sheet: {len(panels)} panels, {sheet_w}x{sheet_h} -> {out}")
    return 0


if __name__ == "__main__":
    C.run_cli(main)
