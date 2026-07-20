#!/usr/bin/env python3
"""Probe basic technical properties of a reference image before visual analysis."""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path


def png_size(data: bytes) -> tuple[int, int] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    return None


def gif_size(data: bytes) -> tuple[int, int] | None:
    if data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    return None


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            return None
        length = struct.unpack(">H", data[index : index + 2])[0]
        if length < 2 or index + length > len(data):
            return None
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if length >= 7:
                height, width = struct.unpack(">HH", data[index + 3 : index + 7])
                return width, height
        index += length
    return None


def webp_size(data: bytes) -> tuple[int, int] | None:
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
            return width & 0x3FFF, height & 0x3FFF
    return None


def bmp_size(data: bytes) -> tuple[int, int] | None:
    if len(data) >= 26 and data[:2] == b"BM":
        width = struct.unpack("<I", data[18:22])[0]
        height = abs(struct.unpack("<i", data[22:26])[0])
        return width, height
    return None


def tiff_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 8:
        return None
    if data[:4] == b"II*\x00":
        endian = "<"
    elif data[:4] == b"MM\x00*":
        endian = ">"
    else:
        return None
    offset = struct.unpack(f"{endian}I", data[4:8])[0]
    if offset + 2 > len(data):
        return None
    entries = struct.unpack(f"{endian}H", data[offset : offset + 2])[0]
    width = height = None
    cursor = offset + 2
    for _ in range(entries):
        if cursor + 12 > len(data):
            return None
        tag, value_type, count, raw_value = struct.unpack(f"{endian}HHII", data[cursor : cursor + 12])
        if value_type in {3, 4} and count == 1:
            value = raw_value if value_type == 4 else raw_value & 0xFFFF
            if tag == 256:
                width = value
            elif tag == 257:
                height = value
        cursor += 12
    if width and height:
        return width, height
    return None


def detect_image_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8"):
        return "jpeg"
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"BM"):
        return "bmp"
    if data[:4] in {b"II*\x00", b"MM\x00*"}:
        return "tiff"
    return None


def detect_size(data: bytes) -> tuple[int, int] | None:
    return png_size(data) or jpeg_size(data) or gif_size(data) or webp_size(data) or bmp_size(data) or tiff_size(data)


def probe(path: Path) -> dict:
    data = path.read_bytes()
    image_type = detect_image_type(data)
    size = detect_size(data)
    warnings: list[str] = []
    if not image_type:
        warnings.append("unknown image type")
    if not size:
        warnings.append("could not read image dimensions")
        width = height = None
        aspect = None
    else:
        width, height = size
        aspect = width / height if height else None
        if width < 512 or height < 512:
            warnings.append("low resolution; small geometry/material details may be unreliable")
        if aspect and (aspect > 3.0 or aspect < 0.33):
            warnings.append("extreme aspect ratio; object may be cropped or surrounded by empty space")
    return {
        "path": str(path),
        "type": image_type,
        "bytes": len(data),
        "width": width,
        "height": height,
        "aspectRatio": aspect,
        "technicalSuitability": "conditional" if warnings else "pass",
        "warnings": warnings,
        "note": "This is only technical image probing. Semantic object suitability still requires visual inspection.",
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    args = parser.parse_args(argv)
    path = args.image.expanduser().resolve()
    if not path.exists():
        parser.error(f"{path} does not exist")
    print(json.dumps(probe(path), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
