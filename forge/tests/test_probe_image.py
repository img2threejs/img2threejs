#!/usr/bin/env python3
from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "stage1_intake"))

from probe_image import tiff_size, webp_size  # noqa: E402


class ProbeImageFormatsTest(unittest.TestCase):
    def test_webp_vp8l_lossless(self):
        b0 = 0xFF
        b1 = 0x03
        b2 = 0x80
        b3 = 0x00
        header = b"RIFF" + struct.pack("<I", 26) + b"WEBPVP8L" + struct.pack("<I", 5) + b"\x2f" + bytes([b0, b1, b2, b3])
        self.assertEqual(webp_size(header), (1024, 513))

    def test_tiff_big_endian_short_tags(self):
        header = b"MM\x00*" + struct.pack(">I", 8)
        num_entries = struct.pack(">H", 2)
        tag_width = struct.pack(">HHII", 256, 3, 1, 1024 << 16)
        tag_height = struct.pack(">HHII", 257, 3, 1, 513 << 16)
        tiff_data = header + num_entries + tag_width + tag_height + b"\x00\x00\x00\x00"
        self.assertEqual(tiff_size(tiff_data), (1024, 513))

    def test_tiff_little_endian_short_tags(self):
        header = b"II*\x00" + struct.pack("<I", 8)
        num_entries = struct.pack("<H", 2)
        tag_width = struct.pack("<HHII", 256, 3, 1, 1024)
        tag_height = struct.pack("<HHII", 257, 3, 1, 513)
        tiff_data = header + num_entries + tag_width + tag_height + b"\x00\x00\x00\x00"
        self.assertEqual(tiff_size(tiff_data), (1024, 513))


if __name__ == "__main__":
    unittest.main()
