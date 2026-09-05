"""Shared image decoding for every pixel-consuming forge script.

One codec, one cache. This replaces the private read_png/load_image copies that
drifted apart across delight_albedo, extract_landmarks, build_detail_inventory,
extract_pbr_evidence and make_comparison_sheet — five files, five near-identical
decoders, each falling back to macOS `sips` for anything that is not PNG.

What lives here:
  * decode_png_bytes — pure-Python PNG decoder. The 8-bit non-interlaced
    non-palette path is byte-for-byte the historical fast path. Extended on top:
    palette (PLTE + tRNS per-index alpha), 1/2/4-bit gray scaling, 16-bit
    (high byte), tRNS single-color transparency for gray/truecolor, and Adam7
    interlace. pngquant-compressed screenshots, 16-bit renders and interlaced
    PNGs decode instead of dying with "unsupported PNG".
  * decode_jpeg re-export — the pure-Python baseline JPEG decoder (jpeg.py).
  * external_convert — for formats neither decoder can read, convert via
    whatever exists on this machine, in order: ImageMagick (magick/convert),
    ffmpeg, macOS sips, then Pillow if importable. When none is available the
    error names the whole chain and the fix, so a Linux/Windows user does not
    conclude the file itself is broken.
  * decode_image / load_image — the canonical cached entry points, keyed by
    (resolved path, mtime, size): one decode per file per process, shared by
    every caller (Divine Eye alone used to decode one reference 6+ times).

Returned pixels are shared between callers — treat them as read-only; no
pipeline code mutates them. Pure stdlib (Pillow is consulted only if already
installed; nothing here installs anything).
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
from jpeg import UnsupportedJpeg, decode_jpeg, is_jpeg  # noqa: E402

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

__all__ = [
    "UnsupportedJpeg",
    "decode_cache_key",
    "decode_image",
    "decode_jpeg",
    "decode_png_bytes",
    "external_convert",
    "is_jpeg",
    "load_image",
    "read_png",
]

_PIXEL = tuple[int, int, int, int]

# (resolved path, mtime, size) -> (width, height, pixels, warnings-tuple)
_DECODE_CACHE: dict[tuple[str, int, int], tuple[int, int, list[_PIXEL], tuple[str, ...]]] = {}

_ADAM7_PASSES = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)


def decode_cache_key(path: Path) -> tuple[str, int, int]:
    resolved = path.expanduser().resolve()
    stat = resolved.stat()
    return (str(resolved), stat.st_mtime_ns, stat.st_size)


def _paeth_predictor(left: int, up: int, up_left: int) -> int:
    p = left + up - up_left
    pa = abs(p - left)
    pb = abs(p - up)
    pc = abs(p - up_left)
    if pa <= pb and pa <= pc:
        return left
    if pb <= pc:
        return up
    return up_left


def _unfilter_rows(raw: bytes, width: int, height: int, bpp: int, stride: int) -> list[bytearray]:
    rows: list[bytearray] = []
    offset = 0
    previous = bytearray(stride)
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        row = bytearray(raw[offset : offset + stride])
        offset += stride
        for index in range(stride):
            left = row[index - bpp] if index >= bpp else 0
            up = previous[index]
            up_left = previous[index - bpp] if index >= bpp else 0
            if filter_type == 1:
                row[index] = (row[index] + left) & 0xFF
            elif filter_type == 2:
                row[index] = (row[index] + up) & 0xFF
            elif filter_type == 3:
                row[index] = (row[index] + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                row[index] = (row[index] + _paeth_predictor(left, up, up_left)) & 0xFF
            elif filter_type != 0:
                raise ValueError(f"unsupported PNG filter {filter_type}")
        rows.append(row)
        previous = row
    return rows


def _sample(row: bytearray, x: int, depth: int, channels: int) -> tuple[int, ...]:
    """Raw per-channel sample values (no palette/tRNS applied) at pixel x."""
    if depth == 8:
        base = x * channels
        return tuple(row[base + c] for c in range(channels))
    if depth == 16:
        base = x * channels * 2
        return tuple((row[base + 2 * c] << 8) | row[base + 2 * c + 1] for c in range(channels))
    # 1/2/4-bit: sub-byte samples, most-significant first
    per_byte = 8 // depth
    index = x // per_byte
    shift = 8 - depth * (x % per_byte) - depth
    mask = (1 << depth) - 1
    return ((row[index] >> shift) & mask,)


def _scale_sample(value: int, depth: int) -> int:
    """Depth-normalized byte: bit replication for 1/2/4, high byte for 16."""
    if depth == 16:
        return value >> 8
    if depth == 8:
        return value
    max_value = (1 << depth) - 1
    return value * 255 // max_value


def _pixel_to_rgba(
    samples: tuple[int, ...],
    color_type: int,
    depth: int,
    palette: list[tuple[int, int, int]],
    trns: bytes | None,
) -> _PIXEL:
    if color_type == 3:  # palette
        index = samples[0]
        if index >= len(palette):
            raise ValueError("PNG palette index out of range")
        red, green, blue = palette[index]
        alpha = trns[index] if trns and index < len(trns) else 255
        return (red, green, blue, alpha)
    if color_type == 0:  # grayscale
        gray = _scale_sample(samples[0], depth)
        if trns and len(trns) >= 2:
            key = ((trns[0] << 8) | trns[1]) >> (8 if depth == 16 else 0)
            if (samples[0] >> 8 if depth == 16 else samples[0]) == key:
                return (gray, gray, gray, 0)
        return (gray, gray, gray, 255)
    if color_type == 2:  # truecolor
        red, green, blue = (_scale_sample(samples[i], depth) for i in range(3))
        if trns and len(trns) >= 6:
            keys = tuple(
                (((trns[2 * i] << 8) | trns[2 * i + 1]) >> 8 if depth == 16 else (trns[2 * i] << 8) | trns[2 * i + 1])
                for i in range(3)
            )
            raws = tuple(samples[i] >> 8 if depth == 16 else samples[i] for i in range(3))
            if raws == keys:
                return (red, green, blue, 0)
        return (red, green, blue, 255)
    if color_type == 4:  # gray + alpha
        gray = _scale_sample(samples[0], depth)
        alpha = _scale_sample(samples[1], depth)
        return (gray, gray, gray, alpha)
    # color_type == 6: RGBA
    return (
        _scale_sample(samples[0], depth),
        _scale_sample(samples[1], depth),
        _scale_sample(samples[2], depth),
        _scale_sample(samples[3], depth),
    )


def decode_png_bytes(data: bytes) -> tuple[int, int, list[_PIXEL]]:
    """Decode a PNG to (width, height, [(r, g, b, a), ...]).

    Supports color types 0/2/3/4/6 at 1/2/4/8/16-bit, tRNS transparency, and
    Adam7 interlace. The 8-bit non-interlaced path is the historical fast path
    and produces byte-identical pixels to the previous private decoders.
    """
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG file")
    cursor = len(PNG_SIGNATURE)
    width = height = bit_depth = color_type = None
    interlace = 0
    idat = bytearray()
    palette: list[tuple[int, int, int]] = []
    trns: bytes | None = None
    while cursor + 8 <= len(data):
        length = struct.unpack(">I", data[cursor : cursor + 4])[0]
        chunk_type = data[cursor + 4 : cursor + 8]
        chunk_data = data[cursor + 8 : cursor + 8 + length]
        cursor += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", chunk_data)
        elif chunk_type == b"IDAT":
            idat.extend(chunk_data)
        elif chunk_type == b"PLTE":
            palette = [
                (chunk_data[i], chunk_data[i + 1], chunk_data[i + 2])
                for i in range(0, len(chunk_data) - 2, 3)
            ]
        elif chunk_type == b"tRNS":
            trns = bytes(chunk_data)
        elif chunk_type == b"IEND":
            break
    if width is None or height is None or bit_depth is None or color_type is None:
        raise ValueError("PNG is missing IHDR")
    if interlace not in (0, 1):
        raise ValueError(f"unsupported PNG interlace method {interlace}")
    channels_by_type = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    if color_type not in channels_by_type:
        raise ValueError("unsupported PNG color type; convert to RGB/RGBA first")
    if color_type == 3 and not palette:
        raise ValueError("palette PNG is missing its PLTE chunk")
    if bit_depth not in (1, 2, 4, 8, 16):
        raise ValueError(f"unsupported PNG bit depth {bit_depth}")
    if color_type == 3 and bit_depth == 16:
        raise ValueError("unsupported PNG: palette with 16-bit indices")
    channels = channels_by_type[color_type]
    bits_per_pixel = channels * bit_depth
    bpp = max(1, bits_per_pixel // 8)
    stride = (width * bits_per_pixel + 7) // 8
    raw = zlib.decompress(bytes(idat))

    pixels: list[_PIXEL] = []
    if interlace == 0 and bit_depth == 8 and trns is None and color_type != 3:
        # Fast path for the overwhelmingly common shape (8-bit, non-interlaced,
        # no tRNS, not palette): direct expansion, no per-pixel helper calls —
        # byte-identical to the historical private decoders and just as fast.
        rows = _unfilter_rows(raw, width, height, bpp, stride)
        if color_type == 6:
            for row in rows:
                for base in range(0, width * 4, 4):
                    pixels.append((row[base], row[base + 1], row[base + 2], row[base + 3]))
        elif color_type == 2:
            for row in rows:
                for base in range(0, width * 3, 3):
                    pixels.append((row[base], row[base + 1], row[base + 2], 255))
        elif color_type == 0:
            for row in rows:
                for x in range(width):
                    gray = row[x]
                    pixels.append((gray, gray, gray, 255))
        else:  # color_type == 4 (gray + alpha)
            for row in rows:
                for base in range(0, width * 2, 2):
                    gray = row[base]
                    pixels.append((gray, gray, gray, row[base + 1]))
        return width, height, pixels

    if interlace == 0:
        rows = _unfilter_rows(raw, width, height, bpp, stride)
        for row in rows:
            for x in range(width):
                pixels.append(_pixel_to_rgba(_sample(row, x, bit_depth, channels), color_type, bit_depth, palette, trns))
        return width, height, pixels

    # Adam7: each pass is an independently filtered sub-image; unfilter it with
    # its own stride and scatter the samples back to full-resolution positions.
    grid: list[list[_PIXEL]] = [[(0, 0, 0, 0)] * width for _ in range(height)]
    offset = 0
    for x0, y0, dx, dy in _ADAM7_PASSES:
        pass_width = (width - x0 + dx - 1) // dx if width > x0 else 0
        pass_height = (height - y0 + dy - 1) // dy if height > y0 else 0
        if pass_width <= 0 or pass_height <= 0:
            continue
        pass_stride = (pass_width * bits_per_pixel + 7) // 8
        end = offset + pass_height * (1 + pass_stride)
        rows = _unfilter_rows(raw[offset:end], pass_width, pass_height, bpp, pass_stride)
        offset = end
        for py, row in enumerate(rows):
            y = y0 + py * dy
            grid_row = grid[y]
            for px in range(pass_width):
                x = x0 + px * dx
                grid_row[x] = _pixel_to_rgba(
                    _sample(row, px, bit_depth, channels), color_type, bit_depth, palette, trns
                )
    if offset != len(raw):
        raise ValueError("trailing data after Adam7 passes")
    for row in grid:
        pixels.extend(row)
    return width, height, pixels


def read_png(path: Path) -> tuple[int, int, list[_PIXEL]]:
    return decode_png_bytes(path.read_bytes())


def external_convert(
    path: Path,
    direct_error: Exception,
    warnings: list[str],
) -> tuple[int, int, list[_PIXEL]]:
    """Last resort for formats the pure-Python decoders cannot read.

    Uses whatever converter exists on this machine (ImageMagick, ffmpeg, macOS
    sips, then Pillow if importable) and fails with an error naming the whole
    chain and the fix.
    """
    tmpdir = tempfile.TemporaryDirectory()
    try:
        converted = Path(tmpdir.name) / "converted.png"
        converters: list[tuple[str, list[str]]] = []
        magick = shutil.which("magick")
        if magick:
            converters.append(("ImageMagick", [magick, str(path), str(converted)]))
        elif shutil.which("convert") and sys.platform != "win32":
            # ImageMagick 6's legacy binary name. On Windows, `convert` is the
            # FAT->NTFS filesystem tool (always on PATH) — invoking it with image
            # arguments must never happen, so only trust the name elsewhere.
            converters.append(("ImageMagick", [shutil.which("convert"), str(path), str(converted)]))
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            converters.append(("ffmpeg", [ffmpeg, "-y", "-i", str(path), str(converted)]))
        sips = shutil.which("sips")
        if sips:
            converters.append(("sips", [sips, "-s", "format", "png", str(path), "--out", str(converted)]))
        if not converters:
            try:
                from PIL import Image  # type: ignore[import-not-found]  (optional, not stdlib)
            except ImportError:
                raise ValueError(
                    f"could not decode {path.name}: it is neither a readable PNG nor a readable "
                    f"baseline JPEG ({direct_error}). No external converter is available either — "
                    f"tried ImageMagick (magick/convert), ffmpeg, macOS sips and Pillow. Convert "
                    f"the image to PNG and retry, or install one of those converters."
                ) from direct_error
            Image.open(path).convert("RGBA").save(converted, format="PNG")
            warnings.append("source image was converted to PNG with Pillow before pixel extraction")
            return read_png(converted)
        last_error = ""
        for name, command in converters:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode == 0 and converted.is_file():
                warnings.append(f"source image was converted to PNG with {name} before pixel extraction")
                return read_png(converted)
            last_error = result.stderr.strip() or result.stdout.strip() or "no output"
        raise ValueError(
            f"could not decode {path.name}: every available converter failed "
            f"({', '.join(name for name, _ in converters)}); last error: {last_error}"
        ) from direct_error
    finally:
        tmpdir.cleanup()


def decode_image(path: Path) -> tuple[int, int, list[_PIXEL]]:
    """Canonical cached decode: PNG -> baseline JPEG -> external converter chain."""
    key = decode_cache_key(path)
    cached = _DECODE_CACHE.get(key)
    if cached is not None:
        return (cached[0], cached[1], cached[2])
    width, height, pixels, _warnings = _decode_uncached(path)
    _DECODE_CACHE[key] = (width, height, pixels, tuple(_warnings))
    return width, height, pixels


def load_image(path: Path) -> tuple[int, int, list[_PIXEL], list[str]]:
    """decode_image plus the extraction warnings (fresh list per call)."""
    key = decode_cache_key(path)
    cached = _DECODE_CACHE.get(key)
    if cached is not None:
        width, height, pixels, cached_warnings = cached
        return width, height, pixels, list(cached_warnings)
    width, height, pixels, warnings = _decode_uncached(path)
    _DECODE_CACHE[key] = (width, height, pixels, tuple(warnings))
    return width, height, pixels, list(warnings)


def _decode_uncached(path: Path) -> tuple[int, int, list[_PIXEL], list[str]]:
    warnings: list[str] = []
    try:
        return (*decode_png_bytes(path.read_bytes()), warnings)
    except Exception as direct_error:
        jpeg_bytes = path.read_bytes()
        if is_jpeg(jpeg_bytes):
            try:
                return (*decode_jpeg(jpeg_bytes), warnings)
            except UnsupportedJpeg as unsupported:
                warnings.append(f"{path.name} needs an external converter: {unsupported}")
        return (*external_convert(path, direct_error, warnings), warnings)
