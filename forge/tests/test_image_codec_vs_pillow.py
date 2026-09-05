#!/usr/bin/env python3
"""Validate image_codec.decode_png_bytes against Pillow across every PNG shape
it newly supports: palette + tRNS, 1/2/4-bit gray, 16-bit, gray/RGB tRNS
transparency, Adam7 interlace, and all five row filters.

Pillow cannot WRITE interlaced or sub-8-bit PNGs (it silently ignores those save
options), so those files are produced by a minimal hand encoder here (filter 0
only — the filter code itself is shared with the non-interlaced path, which is
Pillow-checked) and then decoded independently by BOTH our decoder and Pillow.

Run directly: python forge/tests/test_image_codec_vs_pillow.py
"""
from __future__ import annotations

import random
import struct
import sys
import unittest
import zlib
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
from image_codec import decode_png_bytes  # noqa: E402

try:
    from PIL import Image  # noqa: E402

    PIL_AVAILABLE = True
except ImportError:  # stdlib-only machines: cross-checking needs the reference decoder
    PIL_AVAILABLE = False

RNG = random.Random(20260905)
SIZE = 23  # odd size so Adam7 pass shapes are all non-trivial
ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))

RANDOM_RGB = [(RNG.randrange(256), RNG.randrange(256), RNG.randrange(256)) for _ in range(SIZE * SIZE)]
RANDOM_RGBA = [(r, g, b, RNG.randrange(256)) for r, g, b in RANDOM_RGB]
RANDOM_GRAY = [RNG.randrange(256) for _ in range(SIZE * SIZE)]


def _chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind)
    checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def png_file(
    width: int,
    height: int,
    depth: int,
    color_type: int,
    raw_scanlines: bytes,
    extra: bytes = b"",
    interlace: int = 0,
) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, depth, color_type, 0, 0, interlace)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + extra
        + _chunk(b"IDAT", zlib.compress(raw_scanlines))
        + _chunk(b"IEND", b"")
    )


def encode_adam7(width: int, height: int, pixel: object, channels: int, filter_kind: int = 0) -> bytes:
    """Minimal Adam7 encoder (8-bit samples), every pass filtered with `filter_kind`."""
    raw = bytearray()
    for pass_index, (x0, y0, dx, dy) in enumerate(ADAM7):
        pass_width = (width - x0 + dx - 1) // dx if width > x0 else 0
        pass_height = (height - y0 + dy - 1) // dy if height > y0 else 0
        if pass_width <= 0 or pass_height <= 0:
            continue
        kind = (pass_index + filter_kind) % 5  # cycle through every filter across passes
        previous = bytearray(pass_width * channels)
        for py in range(pass_height):
            y = y0 + py * dy
            current = bytearray()
            for px in range(pass_width):
                x = x0 + px * dx
                current.extend(pixel(x, y))
            raw.append(kind)
            raw.extend(apply_filter(current, previous, channels, kind))
            previous = current
    return bytes(raw)  # caller wraps in a PNG container, which zlib-compresses


def _paeth(left: int, up: int, up_left: int) -> int:
    p = left + up - up_left
    pa, pb, pc = abs(p - left), abs(p - up), abs(p - up_left)
    if pa <= pb and pa <= pc:
        return left
    if pb <= pc:
        return up
    return up_left


def apply_filter(current: bytearray, previous: bytearray, bpp: int, kind: int) -> bytes:
    """Forward PNG filtering (the encoder side of filters 0-4)."""
    out = bytearray(len(current))
    for index, value in enumerate(current):
        left = current[index - bpp] if index >= bpp else 0
        up = previous[index]
        up_left = previous[index - bpp] if index >= bpp else 0
        if kind == 1:
            predictor = left
        elif kind == 2:
            predictor = up
        elif kind == 3:
            predictor = (left + up) // 2
        elif kind == 4:
            predictor = _paeth(left, up, up_left)
        else:
            predictor = 0
        out[index] = (value - predictor) & 0xFF
    return bytes(out)


def encode_noninterlaced_filtered(rows: list[bytearray], bpp: int, kinds: list[int]) -> bytes:
    """Non-interlaced encoder where row i is filtered with kinds[i % len(kinds)]."""
    raw = bytearray()
    previous = bytearray(len(rows[0]))
    for index, row in enumerate(rows):
        kind = kinds[index % len(kinds)]
        raw.append(kind)
        raw.extend(apply_filter(row, previous, bpp, kind))
        previous = row
    return bytes(raw)


def encode_gray_small_depth(values: list[int], width: int, height: int, depth: int) -> bytes:
    """Minimal sub-byte gray encoder: packed most-significant-first, filter 0."""
    raw = bytearray()
    mask = (1 << depth) - 1
    for y in range(height):
        row = bytearray()
        accumulator = 0
        used = 0
        for x in range(width):
            accumulator = (accumulator << depth) | (values[y * width + x] & mask)
            used += depth
            if used == 8:
                row.append(accumulator)
                accumulator = 0
                used = 0
        if used:
            accumulator <<= 8 - used
            row.append(accumulator)
        raw.append(0)
        raw.extend(row)
    return bytes(raw)  # caller wraps in a PNG container, which zlib-compresses


def pillow_pixels(data: bytes) -> list[tuple[int, int, int, int]]:
    return list(Image.open(BytesIO(data)).convert("RGBA").getdata())


@unittest.skipUnless(PIL_AVAILABLE, "Pillow (the reference decoder) is not installed")
class PillowCrossCheck(unittest.TestCase):
    def assert_both_decoders(self, data: bytes, expected: list[tuple[int, int, int, int]], note: str) -> None:
        ours = decode_png_bytes(data)[2]
        reference = pillow_pixels(data)
        self.assertEqual(ours, expected, f"ours vs source: {note}")
        self.assertEqual(reference, expected, f"Pillow vs source: {note}")

    def test_truecolor_8bit_all_filters(self):
        # Pillow writes only filters 1 and 4 (its filter_type save kwarg is ignored),
        # so filters 0-4 are exercised here with a forward-filtering hand encoder,
        # one filter per row — including cross-row cases (Up/Average/Paeth needing
        # the real previous row).
        stride_bytes = SIZE * 3
        rows = []
        flat: list[tuple[int, int, int]] = []
        for y in range(SIZE):
            row = bytearray()
            for x in range(SIZE):
                rgb = RANDOM_RGB[y * SIZE + x]
                row.extend(rgb)
                flat.append(rgb)
            rows.append(row)
        for kind_cycle in ([0, 1, 2, 3, 4], [4], [2], [3]):
            data = png_file(SIZE, SIZE, 8, 2, encode_noninterlaced_filtered(rows, 3, kind_cycle))
            expected = [(*rgb, 255) for rgb in flat]
            self.assert_both_decoders(data, expected, f"RGB filters {kind_cycle}")

    def test_rgba_8bit(self):
        img = Image.new("RGBA", (SIZE, SIZE))
        img.putdata(RANDOM_RGBA)
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        self.assertEqual(decode_png_bytes(buffer.getvalue())[2], RANDOM_RGBA, "RGBA 8-bit")

    def test_gray_and_gray_alpha_8bit(self):
        img = Image.new("L", (SIZE, SIZE))
        img.putdata(RANDOM_GRAY)
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        self.assertEqual(
            decode_png_bytes(buffer.getvalue())[2],
            [(g, g, g, 255) for g in RANDOM_GRAY],
            "gray 8-bit",
        )
        la_pairs = [(g, RNG.randrange(256)) for g in RANDOM_GRAY]
        la = Image.new("LA", (SIZE, SIZE))
        la.putdata(la_pairs)
        buffer = BytesIO()
        la.save(buffer, format="PNG")
        self.assertEqual(
            decode_png_bytes(buffer.getvalue())[2],
            [(g, g, g, a) for g, a in la_pairs],
            "gray+alpha 8-bit",
        )

    def test_16bit_gray_high_byte(self):
        values = [RNG.randrange(65536) for _ in range(SIZE * SIZE)]
        img = Image.new("I;16", (SIZE, SIZE))
        img.putdata(values)
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        data = buffer.getvalue()
        expected = [(v >> 8, v >> 8, v >> 8, 255) for v in values]
        self.assertEqual(decode_png_bytes(data)[2], expected, "16-bit gray keeps the high byte")
        # Pillow's I;16 -> RGBA conversion CLAMPS instead of scaling, so compare against
        # the raw sample values it read rather than its RGBA view of them.
        self.assertEqual(list(Image.open(BytesIO(data)).getdata()), values, "Pillow reads the same samples")

    def test_palette_with_trns(self):
        img = Image.new("RGB", (SIZE, SIZE))
        img.putdata(RANDOM_RGB)
        quantized = img.quantize(colors=64)
        trns = bytes(RNG.randrange(256) for _ in range(8))
        buffer = BytesIO()
        quantized.save(buffer, format="PNG", transparency=trns)
        data = buffer.getvalue()
        expected = pillow_pixels(data)
        self.assertTrue(expected, "Pillow decoded the palette fixture")
        self.assertEqual(decode_png_bytes(data)[2], expected, "palette + tRNS must match Pillow")

    def test_adam7_interlaced_rgb_and_gray(self):
        data = png_file(
            SIZE,
            SIZE,
            8,
            2,
            encode_adam7(SIZE, SIZE, lambda x, y: RANDOM_RGB[y * SIZE + x], 3),
            interlace=1,
        )
        expected = [(*rgb, 255) for rgb in RANDOM_RGB]
        self.assert_both_decoders(data, expected, "Adam7 RGB")

        data = png_file(
            SIZE,
            SIZE,
            8,
            0,
            encode_adam7(SIZE, SIZE, lambda x, y: bytes((RANDOM_GRAY[y * SIZE + x],)), 1),
            interlace=1,
        )
        expected = [(g, g, g, 255) for g in RANDOM_GRAY]
        self.assert_both_decoders(data, expected, "Adam7 gray")

        # every pass filtered with a different filter (1..4 cycling): each pass
        # must unfilter against its OWN previous row, not a neighbouring pass's
        for base_filter in (1, 3):
            data = png_file(
                SIZE,
                SIZE,
                8,
                2,
                encode_adam7(SIZE, SIZE, lambda x, y: RANDOM_RGB[y * SIZE + x], 3, base_filter),
                interlace=1,
            )
            self.assert_both_decoders(
                data,
                [(*rgb, 255) for rgb in RANDOM_RGB],
                f"Adam7 RGB filter cycle base {base_filter}",
            )

    def test_1_2_4_bit_gray(self):
        for depth in (1, 2, 4):
            values = [RNG.randrange(1 << depth) for _ in range(SIZE * SIZE)]
            data = png_file(SIZE, SIZE, depth, 0, encode_gray_small_depth(values, SIZE, SIZE, depth))
            top = (1 << depth) - 1
            expected = [(v * 255 // top, v * 255 // top, v * 255 // top, 255) for v in values]
            self.assert_both_decoders(data, expected, f"{depth}-bit gray")

    def test_gray_and_rgb_trns_single_color(self):
        # gray 8-bit with tRNS key 17
        data = png_file(
            SIZE,
            SIZE,
            8,
            0,
            encode_gray_small_depth([17] * (SIZE * SIZE), SIZE, SIZE, 8),
            extra=_chunk(b"tRNS", struct.pack(">H", 17)),
        )
        self.assert_both_decoders(data, [(17, 17, 17, 0)] * (SIZE * SIZE), "gray tRNS")

        # truecolor 8-bit with tRNS key (10, 20, 30)
        solid = [(10, 20, 30)] * (SIZE * SIZE)
        data = png_file(
            SIZE,
            SIZE,
            8,
            2,
            encode_adam7(SIZE, SIZE, lambda x, y: bytes(solid[y * SIZE + x]), 3),
            extra=_chunk(b"tRNS", struct.pack(">HHH", 10, 20, 30)),
            interlace=1,
        )
        self.assert_both_decoders(data, [(10, 20, 30, 0)] * (SIZE * SIZE), "RGB tRNS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
