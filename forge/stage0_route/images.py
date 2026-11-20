"""Lossless upright PNG crops and stdlib baseline JPEG/EXIF normalization."""
from __future__ import annotations

import hashlib
import math
import struct
import zlib

from .storage import RouteError, atomic_write

_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def box(value):
    if (type(value) is not list or len(value) != 4
            or any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1 for x in value)
            or value[0] >= value[2] or value[1] >= value[3]):
        raise RouteError("invalid-object-box")
    return list(value)


def iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return intersection / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection)


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)


def png(width, height, channels, pixels):
    color = {1: 0, 2: 4, 3: 2, 4: 6}[channels]
    stride = width * channels
    filtered = b"".join(b"\0" + pixels[y * stride:(y + 1) * stride] for y in range(height))
    return (_SIGNATURE + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(filtered)) + _chunk(b"IEND", b""))


def _decode_png(data):
    if not data.startswith(_SIGNATURE) or len(data) > 32 * 1024 * 1024:
        raise RouteError("reference-conversion-required")
    offset, compressed, header, ended = 8, [], None, False
    while offset + 12 <= len(data):
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + size]
        end = offset + 12 + size
        if end > len(data) or zlib.crc32(kind + payload) & 0xffffffff != struct.unpack(">I", data[end - 4:end])[0]:
            raise RouteError("invalid-reference-png")
        if kind == b"IHDR":
            if header is not None or offset != 8 or size != 13:
                raise RouteError("invalid-reference-png")
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            compressed.append(payload)
        elif kind == b"IEND":
            ended = True
            if size != 0 or end != len(data):
                raise RouteError("invalid-reference-png")
            break
        elif kind == b"eXIf" or (kind[0] & 32 == 0 and kind != b"PLTE"):
            raise RouteError("reference-conversion-required")
        offset = end
    if header is None or not ended:
        raise RouteError("invalid-reference-png")
    width, height, depth, color, compression, filtering, interlace = header
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
    if (not width or not height or width * height > 16_000_000 or depth != 8
            or channels is None or compression or filtering or interlace):
        raise RouteError("reference-conversion-required")
    stride = width * channels
    expected = height * (stride + 1)
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(b"".join(compressed), expected + 1)
        if len(raw) != expected or not decoder.eof or decoder.unused_data:
            raise RouteError("invalid-reference-png")
    except zlib.error as exc:
        raise RouteError("invalid-reference-png") from exc
    pixels = bytearray(width * height * channels)
    for y in range(height):
        mode = raw[y * (stride + 1)]
        if mode > 4:
            raise RouteError("invalid-reference-png")
        for x in range(stride):
            left = pixels[y * stride + x - channels] if x >= channels else 0
            up = pixels[(y - 1) * stride + x] if y else 0
            upper_left = pixels[(y - 1) * stride + x - channels] if y and x >= channels else 0
            if mode == 0:
                predictor = 0
            elif mode == 1:
                predictor = left
            elif mode == 2:
                predictor = up
            elif mode == 3:
                predictor = (left + up) // 2
            else:
                p = left + up - upper_left
                distances = [abs(p - left), abs(p - up), abs(p - upper_left)]
                predictor = (left, up, upper_left)[distances.index(min(distances))]
            pixels[y * stride + x] = (raw[y * (stride + 1) + 1 + x] + predictor) & 255
    return width, height, channels, pixels


def crop(workspace, decoded, bounds):
    width, height, channels, pixels = decoded
    left, top, right, bottom = box(bounds)
    margin_x, margin_y = (right - left) * .1, (bottom - top) * .1
    x0, y0 = max(0, math.floor((left - margin_x) * width)), max(0, math.floor((top - margin_y) * height))
    x1, y1 = min(width, math.ceil((right + margin_x) * width)), min(height, math.ceil((bottom + margin_y) * height))
    rows = b"".join(pixels[(y * width + x0) * channels:(y * width + x1) * channels] for y in range(y0, y1))
    data = png(x1 - x0, y1 - y0, channels, rows)
    sha = hashlib.sha256(data).hexdigest()
    relative = f".img2threejs/route-inputs/crops/{sha}.png"
    atomic_write(workspace, relative, data, immutable=True)
    return {"role": "reference-crop", "path": relative, "sha256": sha}, {
        "origin": [x0, y0], "size": [x1 - x0, y1 - y0], "referenceSize": [width, height],
        "scale": 1, "padding": [0, 0], "contextMargin": .1, "coordinateSpace": "upright-pixels"}


def _exif_orientation(payload):
    tiff = payload[6:]
    if len(tiff) < 8 or tiff[:2] not in {b"II", b"MM"}:
        raise RouteError("invalid-reference-exif")
    endian = "<" if tiff[:2] == b"II" else ">"
    def number(offset, kind):
        size = struct.calcsize(kind)
        if offset < 0 or offset + size > len(tiff):
            raise RouteError("invalid-reference-exif")
        return struct.unpack(endian + kind, tiff[offset:offset + size])[0]
    if number(2, "H") != 42:
        raise RouteError("invalid-reference-exif")
    offset = number(4, "I")
    count = number(offset, "H")
    if offset < 8 or offset + 2 + 12 * count + 4 > len(tiff):
        raise RouteError("invalid-reference-exif")
    orientation = None
    for index in range(count):
        entry = offset + 2 + 12 * index
        if number(entry, "H") == 274:
            if orientation is not None or number(entry + 2, "H") != 3 or number(entry + 4, "I") != 1:
                raise RouteError("invalid-reference-exif")
            orientation = number(entry + 8, "H")
            if orientation not in range(1, 9):
                raise RouteError("unsupported-reference-orientation")
    return orientation or 1


def _jpeg_header(data):
    if len(data) > 32 * 1024 * 1024 or not data.endswith(b"\xff\xd9"):
        raise RouteError("invalid-reference-jpeg")
    offset, orientation, size, seen_exif = 2, 1, None, False
    while offset < len(data):
        if data[offset] != 255:
            raise RouteError("invalid-reference-jpeg")
        while offset < len(data) and data[offset] == 255:
            offset += 1
        if offset >= len(data):
            raise RouteError("invalid-reference-jpeg")
        marker = data[offset]
        offset += 1
        if marker == 0xDA:
            if size is None:
                raise RouteError("invalid-reference-jpeg")
            return size, orientation
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            raise RouteError("invalid-reference-jpeg")
        if offset + 2 > len(data):
            raise RouteError("invalid-reference-jpeg")
        length = struct.unpack(">H", data[offset:offset + 2])[0]
        if length < 2 or offset + length > len(data):
            raise RouteError("invalid-reference-jpeg")
        segment = data[offset + 2:offset + length]
        if marker == 0xE1 and segment.startswith(b"Exif\0\0"):
            if seen_exif:
                raise RouteError("ambiguous-reference-orientation")
            orientation, seen_exif = _exif_orientation(segment), True
        if marker == 0xEE and segment.startswith(b"Adobe") and (len(segment) < 12 or segment[11] != 1):
            raise RouteError("jpeg-conversion-required")
        if marker in {0xC0, 0xC1}:
            if size is not None or len(segment) < 6:
                raise RouteError("invalid-reference-jpeg")
            precision, height, width, components = struct.unpack(">BHHB", segment[:6])
            if (precision != 8 or components not in {1, 3} or len(segment) != 6 + 3 * components
                    or not width or not height or width * height > 16_000_000):
                raise RouteError("jpeg-conversion-required")
            component_ids = [segment[6 + 3 * index] for index in range(components)]
            if component_ids != list(range(1, components + 1)):
                raise RouteError("jpeg-conversion-required")
            for index in range(components):
                sampling = segment[7 + 3 * index]
                if sampling >> 4 not in range(1, 5) or sampling & 15 not in range(1, 5):
                    raise RouteError("jpeg-conversion-required")
            size = (width, height)
        elif marker in {0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            raise RouteError("jpeg-conversion-required")
        offset += length
    raise RouteError("invalid-reference-jpeg")


def prepare_reference(data):
    """Return upright pixels and an explicit original-to-upright pixel-centre transform."""
    if data.startswith(_SIGNATURE):
        decoded, orientation = _decode_png(data), 1
    elif data.startswith(b"\xff\xd8"):
        from jpeg import decode_jpeg, UnsupportedJpeg
        size, orientation = _jpeg_header(data)
        try:
            width, height, pixels = decode_jpeg(data)
        except UnsupportedJpeg as exc:
            raise RouteError("jpeg-conversion-required") from exc
        except (ValueError, IndexError, KeyError, ZeroDivisionError, struct.error) as exc:
            raise RouteError("invalid-reference-jpeg") from exc
        if (width, height) != size:
            raise RouteError("invalid-reference-jpeg")
        decoded = (width, height, 4, bytearray(channel for pixel in pixels for channel in pixel))
    else:
        raise RouteError("reference-conversion-required")
    width, height, channels, pixels = decoded
    transforms = {
        1: [[1, 0, 0], [0, 1, 0]], 2: [[-1, 0, width - 1], [0, 1, 0]],
        3: [[-1, 0, width - 1], [0, -1, height - 1]], 4: [[1, 0, 0], [0, -1, height - 1]],
        5: [[0, 1, 0], [1, 0, 0]], 6: [[0, -1, height - 1], [1, 0, 0]],
        7: [[0, -1, height - 1], [-1, 0, width - 1]], 8: [[0, 1, 0], [-1, 0, width - 1]]}
    upright_width, upright_height = (height, width) if orientation >= 5 else (width, height)
    if orientation != 1:
        output = bytearray(len(pixels))
        matrix = transforms[orientation]
        for y in range(height):
            for x in range(width):
                nx = matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]
                ny = matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]
                source, target = (y * width + x) * channels, (ny * upright_width + nx) * channels
                output[target:target + channels] = pixels[source:source + channels]
        decoded = upright_width, upright_height, channels, output
    return decoded, {"sourceSize": [width, height], "uprightSize": [upright_width, upright_height],
        "exifOrientation": orientation, "sourceToUpright": transforms[orientation],
        "transformCoordinates": "pixel-centres", "boxCoordinates": "upright-normalized"}


def decode(data):
    return prepare_reference(data)[0]


def admit_reference(workspace, source, data):
    if hashlib.sha256(data).hexdigest() != source["sha256"]:
        raise RouteError("stale-evidence")
    decoded, transform = prepare_reference(data)
    if data.startswith(_SIGNATURE):
        upright = dict(source, role="upright-reference")
    else:
        normalized = png(*decoded)
        sha = hashlib.sha256(normalized).hexdigest()
        relative = f".img2threejs/route-inputs/upright/{sha}.png"
        atomic_write(workspace, relative, normalized, immutable=True)
        upright = {"role": "upright-reference", "path": relative, "sha256": sha}
    return decoded, {"sourceSha256": source["sha256"], "uprightReference": upright, "transform": transform}
