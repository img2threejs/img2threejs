#!/usr/bin/env python3
"""Slice a reference image into inspection zones and scaffold a detailInventory to fill in.

Scans the reference zone by zone (a uniform grid, or named component regions) so small
identity-defining marks are not missed by a single glance at the whole image. Writes one
crop PNG per zone plus a detailInventory skeleton JSON (see docs/UPGRADE_PLAN.md 4.1 and
grimoire/intake/detail_inventory.md) with one detail stub per zone for the agent to classify,
describe, and link to a component/material field. This script only scaffolds zones and
crops; it does not judge what is in them.
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

TARGET_MIN_DETAILS = {
    "simple": 3,
    "moderate": 6,
    "complex": 10,
    "ultra-complex": 16,
}

# These thirds are a DEFAULT, not a limit -- `component-zones` mode with --components takes any named
# normalized regions, so an arbitrary per-feature ROI is already expressible. What is genuinely absent
# is automatic detection and scoring of details: nothing here finds a tear, only describes where to
# look. See grimoire/review/divine_eye_microscope.md for the feature-descriptor schema.
DEFAULT_COMPONENT_ZONES = [
    ("upper", 0.0, 0.0, 1.0, 1.0 / 3),
    ("middle", 0.0, 1.0 / 3, 1.0, 1.0 / 3),
    ("lower", 0.0, 2.0 / 3, 1.0, 1.0 / 3),
]






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


def parse_components(spec: str) -> list[tuple[str, float, float, float, float]]:
    zones: list[tuple[str, float, float, float, float]] = []
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        name, sep, coords = part.partition(":")
        if not sep:
            raise ValueError(f"malformed --components entry (expected name:x,y,w,h): {part!r}")
        values = [float(v) for v in coords.split(",")]
        if len(values) != 4:
            raise ValueError(f"malformed --components entry (expected 4 normalized values): {part!r}")
        x, y, w, h = values
        zones.append((name.strip(), x, y, w, h))
    if not zones:
        raise ValueError("--components produced no zones")
    return zones


def build_zones(mode: str, components_spec: str | None) -> list[dict]:
    if mode == "component-zones":
        zones_spec = parse_components(components_spec) if components_spec else DEFAULT_COMPONENT_ZONES
        return [
            {"id": name, "region": {"x": x, "y": y, "width": w, "height": h, "units": "normalized"}}
            for name, x, y, w, h in zones_spec
        ]
    grid = 3 if mode == "grid-3x3" else 4
    step = 1.0 / grid
    zones = []
    for row in range(grid):
        for col in range(grid):
            zones.append(
                {
                    "id": f"zone-r{row}c{col}",
                    "region": {
                        "x": round(col * step, 4),
                        "y": round(row * step, 4),
                        "width": round(step, 4),
                        "height": round(step, 4),
                        "units": "normalized",
                    },
                }
            )
    return zones


def make_detail_stub(zone: dict, crop_path: Path) -> dict:
    return {
        "id": zone["id"],
        "kind": "",
        "description": "",
        "region": zone["region"],
        "scale": "",
        "affects": "",
        "mapsTo": {"type": "", "ref": ""},
        "evidenceRef": str(crop_path),
        "confidence": 0.0,
    }


def build_inventory(
    image: Path,
    mode: str,
    out_dir: Path,
    target_min_details: int,
    components_spec: str | None,
) -> dict:
    width, height, pixels = load_image(image)
    zones = build_zones(mode, components_spec)
    out_dir.mkdir(parents=True, exist_ok=True)
    details = []
    for zone in zones:
        region = zone["region"]
        x0 = round(region["x"] * width)
        y0 = round(region["y"] * height)
        x1 = min(width, x0 + max(1, round(region["width"] * width)))
        y1 = min(height, y0 + max(1, round(region["height"] * height)))
        crop_w = max(1, x1 - x0)
        crop_h = max(1, y1 - y0)
        crop_pixels = []
        for y in range(y0, y0 + crop_h):
            source_y = min(height - 1, y)
            for x in range(x0, x0 + crop_w):
                source_x = min(width - 1, x)
                crop_pixels.append(composite_over_white(pixels[source_y * width + source_x]))
        crop_path = out_dir / f"{zone['id']}.png"
        write_png_rgb(crop_path, crop_w, crop_h, crop_pixels)
        details.append(make_detail_stub(zone, crop_path))
    return {
        "scanMethod": mode,
        "targetMinDetails": target_min_details,
        "details": details,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument(
        "--mode",
        choices=["grid-3x3", "grid-4x4", "component-zones"],
        default="grid-3x3",
        help="Zone layout to scan (default: grid-3x3)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="Directory to write zone crop PNGs (default: <image-stem>-zones next to the image)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Output detailInventory skeleton JSON path (default: <out-dir>/detail-inventory.json)",
    )
    parser.add_argument(
        "--complexity",
        choices=sorted(TARGET_MIN_DETAILS),
        default="moderate",
        help="Sets targetMinDetails from the complexity tier; overridden by --target-min-details",
    )
    parser.add_argument("--target-min-details", type=int, help="Override targetMinDetails directly")
    parser.add_argument(
        "--components",
        help="component-zones only: 'name:x,y,w,h;name2:x,y,w,h' normalized regions "
        "(default: upper/middle/lower thirds)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing output JSON")
    args = parser.parse_args(argv)

    image = args.image.expanduser().resolve()
    if not image.exists():
        parser.error(f"{image} does not exist")
    out_dir = (args.out_dir or image.with_name(f"{image.stem}-zones")).expanduser().resolve()
    out_path = (args.out or out_dir / "detail-inventory.json").expanduser().resolve()
    if out_path.exists() and not args.force:
        parser.error(f"{out_path} already exists; use --force to overwrite")
    target_min_details = args.target_min_details or TARGET_MIN_DETAILS[args.complexity]

    try:
        inventory = build_inventory(image, args.mode, out_dir, target_min_details, args.components)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    payload = {
        "sourceImage": str(image),
        "zonesDir": str(out_dir),
        "detailInventory": inventory,
        "authoringInstruction": (
            "Open each zone crop under zonesDir and replace every detail stub's kind, description, "
            "scale, affects, mapsTo, and confidence with what is actually observed. Add more detail "
            "entries per zone if a single zone contains multiple distinct marks; do not leave stubs "
            "unfilled or unlinked (mapsTo must reference a real component.localFeatures or "
            "material.localOverrides entry)."
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
