#!/usr/bin/env python3
"""Baseline JPEG decoder in pure Python 3.10+ standard library.

Why this exists: every image-consuming script fell back to macOS `sips` for any
format that is not PNG, so a JPEG reference — the most common kind of reference
photo there is — failed hard on Linux and Windows, and in CI. This decodes
baseline JPEG directly so the pipeline behaves the same on every platform.

Scope is deliberately narrow and honest about it:
  * baseline sequential DCT (SOF0) and extended sequential (SOF1), 8-bit
  * grayscale (1 component) and YCbCr (3 components), any sampling factors
  * restart intervals
  * progressive (SOF2), arithmetic coding, 12-bit and CMYK raise a clear
    UnsupportedJpeg so callers can fall back rather than silently mis-decode

Returns the same shape as read_png(): (width, height, [(r, g, b, a), ...]).
"""
from __future__ import annotations

import struct
from math import cos, pi

__all__ = ["decode_jpeg", "is_jpeg", "UnsupportedJpeg"]


class UnsupportedJpeg(ValueError):
    """A JPEG this decoder deliberately does not handle (e.g. progressive)."""


SOI, EOI, SOS, DQT, DHT, DRI = 0xD8, 0xD9, 0xDA, 0xDB, 0xC4, 0xDD
SOF_BASELINE, SOF_EXTENDED, SOF_PROGRESSIVE = 0xC0, 0xC1, 0xC2

ZIGZAG = (
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63,
)

# IDCT[u][x] = C(u)/2 * cos((2x+1) u pi / 16); applying it on rows then columns
# yields the 1/4 scaling of the 2-D inverse DCT.
_IDCT = [
    [((0.353553390593273762 if u == 0 else 0.5) * cos((2 * x + 1) * u * pi / 16))
     for x in range(8)]
    for u in range(8)
]


def is_jpeg(data: bytes) -> bool:
    return len(data) >= 2 and data[0] == 0xFF and data[1] == SOI


class _BitReader:
    """MSB-first bit reader over entropy-coded data, unstuffing 0xFF 0x00."""

    __slots__ = ("data", "pos", "bits", "nbits")

    def __init__(self, data: bytes, pos: int) -> None:
        self.data = data
        self.pos = pos
        self.bits = 0
        self.nbits = 0

    def _fill(self) -> None:
        data = self.data
        while self.nbits <= 24:
            if self.pos >= len(data):
                self.bits = (self.bits << 8) | 0
                self.nbits += 8
                continue
            byte = data[self.pos]
            self.pos += 1
            if byte == 0xFF:
                nxt = data[self.pos] if self.pos < len(data) else 0
                if nxt == 0x00:
                    self.pos += 1
                elif 0xD0 <= nxt <= 0xD7:
                    # restart marker: stop feeding, sync() consumes it
                    self.pos -= 1
                    self.bits = (self.bits << 8) | 0
                    self.nbits += 8
                    continue
                else:
                    self.pos -= 1
                    self.bits = (self.bits << 8) | 0
                    self.nbits += 8
                    continue
            self.bits = (self.bits << 8) | byte
            self.nbits += 8

    def receive(self, length: int) -> int:
        if length == 0:
            return 0
        if self.nbits < length:
            self._fill()
        self.nbits -= length
        value = (self.bits >> self.nbits) & ((1 << length) - 1)
        self.bits &= (1 << self.nbits) - 1
        return value

    def bit(self) -> int:
        return self.receive(1)

    def sync_restart(self) -> None:
        """Consume an RSTn marker and drop partial bits."""
        self.bits = 0
        self.nbits = 0
        data = self.data
        while self.pos + 1 < len(data):
            if data[self.pos] == 0xFF and 0xD0 <= data[self.pos + 1] <= 0xD7:
                self.pos += 2
                return
            self.pos += 1


def _build_huffman(counts: bytes, symbols: bytes) -> dict[tuple[int, int], int]:
    """Map (bit-length, code) -> symbol. Canonical JPEG Huffman assignment."""
    table: dict[tuple[int, int], int] = {}
    code = 0
    index = 0
    for length in range(1, 17):
        for _ in range(counts[length - 1]):
            table[(length, code)] = symbols[index]
            index += 1
            code += 1
        code <<= 1
    return table


def _decode_huffman(reader: _BitReader, table: dict[tuple[int, int], int]) -> int:
    code = 0
    for length in range(1, 17):
        code = (code << 1) | reader.bit()
        symbol = table.get((length, code))
        if symbol is not None:
            return symbol
    raise ValueError("invalid Huffman code in entropy-coded data")


def _extend(value: int, length: int) -> int:
    """Convert an unsigned magnitude to its signed JPEG value."""
    if length == 0:
        return 0
    return value if value >= (1 << (length - 1)) else value - (1 << length) + 1


def _idct_2d(block: list[float]) -> list[float]:
    """Separable 8x8 inverse DCT. DC-only blocks short-circuit."""
    if not any(block[1:]):
        flat = block[0] * 0.125
        return [flat] * 64

    tmp = [0.0] * 64
    for y in range(8):
        row = y * 8
        coeffs = block[row:row + 8]
        if not any(coeffs[1:]):
            value = coeffs[0] * 0.353553390593273762
            for x in range(8):
                tmp[row + x] = value
            continue
        for x in range(8):
            total = 0.0
            for u in range(8):
                c = coeffs[u]
                if c:
                    total += c * _IDCT[u][x]
            tmp[row + x] = total

    out = [0.0] * 64
    for x in range(8):
        column = [tmp[y * 8 + x] for y in range(8)]
        if not any(column[1:]):
            value = column[0] * 0.353553390593273762
            for y in range(8):
                out[y * 8 + x] = value
            continue
        for y in range(8):
            total = 0.0
            for v in range(8):
                c = column[v]
                if c:
                    total += c * _IDCT[v][y]
            out[y * 8 + x] = total
    return out


def _axis_map(out_len: int, samp: int, samp_max: int, plane_len: int) -> list[tuple[int, int, float]]:
    """Per-output-pixel (low, high, fraction) for triangular chroma upsampling.

    Subsampled chroma samples sit at the centre of the luma pixels they cover, so
    output pixel x maps to chroma coordinate (x + 0.5) * samp/samp_max - 0.5.
    Nearest-neighbour replication instead of this interpolation is visible as
    banding on sharp colour edges (~30/255 against a libjpeg reference)."""
    scale = samp / samp_max
    mapping: list[tuple[int, int, float]] = []
    last = max(0, plane_len - 1)
    for x in range(out_len):
        coord = (x + 0.5) * scale - 0.5
        low = int(coord // 1)
        frac = coord - low
        if low < 0:
            low, frac = 0, 0.0
        high = low + 1
        if low > last:
            low = last
        if high > last:
            high = last
        mapping.append((low, high, frac))
    return mapping


def _bilinear(plane: list[float], row0: int, row1: int, yfrac: float,
              xmap: tuple[int, int, float]) -> float:
    x0, x1, xfrac = xmap
    top = plane[row0 + x0] + (plane[row0 + x1] - plane[row0 + x0]) * xfrac
    if yfrac == 0.0 and row0 == row1:
        return top
    bottom = plane[row1 + x0] + (plane[row1 + x1] - plane[row1 + x0]) * xfrac
    return top + (bottom - top) * yfrac


def _clamp(value: float) -> int:
    if value <= 0.0:
        return 0
    if value >= 255.0:
        return 255
    return int(value + 0.5)


def decode_jpeg(data: bytes) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    """Decode baseline JPEG bytes to (width, height, RGBA pixel list)."""
    if not is_jpeg(data):
        raise ValueError("not a JPEG file")

    quant: dict[int, list[int]] = {}
    huff_dc: dict[int, dict[tuple[int, int], int]] = {}
    huff_ac: dict[int, dict[tuple[int, int], int]] = {}
    components: list[dict] = []
    width = height = 0
    restart_interval = 0
    scan_start = -1
    scan_components: list[dict] = []

    pos = 2
    while pos + 3 < len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        pos += 2
        if marker in (0x01, EOI) or 0xD0 <= marker <= 0xD7:
            continue
        if pos + 2 > len(data):
            break
        seg_len = struct.unpack(">H", data[pos:pos + 2])[0]
        segment = data[pos + 2:pos + seg_len]

        if marker == SOF_PROGRESSIVE:
            raise UnsupportedJpeg("progressive JPEG is not supported by this decoder")
        if marker in (0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            raise UnsupportedJpeg(f"unsupported JPEG coding mode (SOF marker 0x{marker:02X})")

        if marker in (SOF_BASELINE, SOF_EXTENDED):
            precision = segment[0]
            if precision != 8:
                raise UnsupportedJpeg(f"only 8-bit JPEG is supported, got {precision}-bit")
            height, width = struct.unpack(">HH", segment[1:5])
            count = segment[5]
            if count not in (1, 3):
                raise UnsupportedJpeg(
                    f"only grayscale and YCbCr JPEG are supported, got {count} components"
                )
            components = []
            for i in range(count):
                cid, sampling, tq = segment[6 + i * 3:9 + i * 3]
                components.append({
                    "id": cid,
                    "h": sampling >> 4,
                    "v": sampling & 15,
                    "tq": tq,
                })
        elif marker == DQT:
            cursor = 0
            while cursor < len(segment):
                pq_tq = segment[cursor]
                cursor += 1
                precision, table_id = pq_tq >> 4, pq_tq & 15
                values = [0] * 64
                for i in range(64):
                    if precision:
                        values[ZIGZAG[i]] = struct.unpack(">H", segment[cursor:cursor + 2])[0]
                        cursor += 2
                    else:
                        values[ZIGZAG[i]] = segment[cursor]
                        cursor += 1
                quant[table_id] = values
        elif marker == DHT:
            cursor = 0
            while cursor < len(segment):
                tc_th = segment[cursor]
                cursor += 1
                counts = segment[cursor:cursor + 16]
                cursor += 16
                total = sum(counts)
                symbols = segment[cursor:cursor + total]
                cursor += total
                table = _build_huffman(counts, symbols)
                if tc_th >> 4:
                    huff_ac[tc_th & 15] = table
                else:
                    huff_dc[tc_th & 15] = table
        elif marker == DRI:
            restart_interval = struct.unpack(">H", segment[0:2])[0]
        elif marker == SOS:
            count = segment[0]
            scan_components = []
            for i in range(count):
                cid, tables = segment[1 + i * 2:3 + i * 2]
                for comp in components:
                    if comp["id"] == cid:
                        comp["dc"] = tables >> 4
                        comp["ac"] = tables & 15
                        scan_components.append(comp)
                        break
            scan_start = pos + seg_len
            break
        pos += seg_len

    if not components or width == 0 or height == 0:
        raise ValueError("JPEG is missing a frame header")
    if scan_start < 0:
        raise ValueError("JPEG is missing scan data")

    h_max = max(c["h"] for c in components)
    v_max = max(c["v"] for c in components)
    mcu_w, mcu_h = 8 * h_max, 8 * v_max
    mcus_x = (width + mcu_w - 1) // mcu_w
    mcus_y = (height + mcu_h - 1) // mcu_h

    for comp in components:
        comp["bw"] = mcus_x * comp["h"]
        comp["bh"] = mcus_y * comp["v"]
        comp["plane"] = [0] * (comp["bw"] * 8 * comp["bh"] * 8)
        comp["stride"] = comp["bw"] * 8
        comp["pred"] = 0

    reader = _BitReader(data, scan_start)
    block = [0.0] * 64
    mcu_index = 0

    for my in range(mcus_y):
        for mx in range(mcus_x):
            if restart_interval and mcu_index and mcu_index % restart_interval == 0:
                reader.sync_restart()
                for comp in components:
                    comp["pred"] = 0
            mcu_index += 1

            for comp in scan_components:
                qt = quant.get(comp["tq"])
                if qt is None:
                    raise ValueError("JPEG references an undefined quantization table")
                dc_table = huff_dc.get(comp["dc"])
                ac_table = huff_ac.get(comp["ac"])
                if dc_table is None or ac_table is None:
                    raise ValueError("JPEG references an undefined Huffman table")

                for by in range(comp["v"]):
                    for bx in range(comp["h"]):
                        for i in range(64):
                            block[i] = 0.0

                        t = _decode_huffman(reader, dc_table)
                        diff = _extend(reader.receive(t), t) if t else 0
                        comp["pred"] += diff
                        block[0] = float(comp["pred"] * qt[0])

                        k = 1
                        while k < 64:
                            rs = _decode_huffman(reader, ac_table)
                            run, size = rs >> 4, rs & 15
                            if size == 0:
                                if run == 15:
                                    k += 16
                                    continue
                                break
                            k += run
                            if k > 63:
                                break
                            zz = ZIGZAG[k]
                            block[zz] = float(_extend(reader.receive(size), size) * qt[zz])
                            k += 1

                        pixels = _idct_2d(block)
                        px0 = (mx * comp["h"] + bx) * 8
                        py0 = (my * comp["v"] + by) * 8
                        stride = comp["stride"]
                        plane = comp["plane"]
                        for y in range(8):
                            row = (py0 + y) * stride + px0
                            src = y * 8
                            for x in range(8):
                                plane[row + x] = pixels[src + x] + 128.0

    out: list[tuple[int, int, int, int]] = []
    if len(components) == 1:
        comp = components[0]
        plane, stride = comp["plane"], comp["stride"]
        for y in range(height):
            base = y * stride
            for x in range(width):
                grey = _clamp(plane[base + x])
                out.append((grey, grey, grey, 255))
        return width, height, out

    y_c, cb_c, cr_c = components[0], components[1], components[2]
    for comp in (y_c, cb_c, cr_c):
        comp["w"] = -(-width * comp["h"] // h_max)
        comp["h_px"] = -(-height * comp["v"] // v_max)
        comp["xmap"] = _axis_map(width, comp["h"], h_max, comp["w"])
        comp["ymap"] = _axis_map(height, comp["v"], v_max, comp["h_px"])

    y_plane, y_stride, y_xmap, y_ymap = (
        y_c["plane"], y_c["stride"], y_c["xmap"], y_c["ymap"])
    cb_plane, cb_stride, cb_xmap, cb_ymap = (
        cb_c["plane"], cb_c["stride"], cb_c["xmap"], cb_c["ymap"])
    cr_plane, cr_stride, cr_xmap, cr_ymap = (
        cr_c["plane"], cr_c["stride"], cr_c["xmap"], cr_c["ymap"])

    for y in range(height):
        yy0, yy1, yfr = y_ymap[y]
        cby0, cby1, cbyfr = cb_ymap[y]
        cry0, cry1, cryfr = cr_ymap[y]
        y_r0, y_r1 = yy0 * y_stride, yy1 * y_stride
        cb_r0, cb_r1 = cby0 * cb_stride, cby1 * cb_stride
        cr_r0, cr_r1 = cry0 * cr_stride, cry1 * cr_stride
        for x in range(width):
            luma = _bilinear(y_plane, y_r0, y_r1, yfr, y_xmap[x])
            cb = _bilinear(cb_plane, cb_r0, cb_r1, cbyfr, cb_xmap[x]) - 128.0
            cr = _bilinear(cr_plane, cr_r0, cr_r1, cryfr, cr_xmap[x]) - 128.0
            out.append((
                _clamp(luma + 1.402 * cr),
                _clamp(luma - 0.344136 * cb - 0.714136 * cr),
                _clamp(luma + 1.772 * cb),
                255,
            ))
    return width, height, out
