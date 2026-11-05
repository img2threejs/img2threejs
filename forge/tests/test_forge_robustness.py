#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import struct
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "_shared"))
sys.path.insert(0, str(ROOT / "stage1_intake"))
sys.path.insert(0, str(ROOT / "stage2_spec"))
sys.path.insert(0, str(ROOT / "stage3_build"))

from image_hash import hamming, phash_from_image  # noqa: E402
from material_region_analysis import sanitize_region_id  # noqa: E402
from probe_image import tiff_size, webp_size  # noqa: E402
from generate_threejs_factory import scale_vector  # noqa: E402


class ForgeRobustnessTest(unittest.TestCase):
    def test_phash_scale_invariance_for_sub_grid_dimensions(self) -> None:
        def stepped_image(side: int) -> list[tuple[int, int, int, int]]:
            return [
                (40, 40, 40, 255) if x < side // 2 else (210, 210, 210, 255)
                for y in range(side)
                for x in range(side)
            ]

        hash_16 = phash_from_image(16, 16, stepped_image(16))
        hash_32 = phash_from_image(32, 32, stepped_image(32))
        self.assertLessEqual(hamming(hash_16, hash_32), 2)

    def test_sanitize_region_id_strips_directory_traversal(self) -> None:
        self.assertEqual(sanitize_region_id("metal_plate"), "metal_plate")
        self.assertEqual(sanitize_region_id("../../dangerous/id"), "dangerous-id")
        self.assertEqual(sanitize_region_id("..\\..\\windows_id"), "windows_id")

    def test_probe_webp_vp8l_lossless_header(self) -> None:
        header = (
            b"RIFF"
            + struct.pack("<I", 26)
            + b"WEBPVP8L"
            + struct.pack("<I", 5)
            + b"\x2f\xff\x03\x80\x00"
        )
        self.assertEqual(webp_size(header), (1024, 513))

    def test_probe_tiff_big_endian_short_dimensions(self) -> None:
        header = (
            b"MM\x00*"
            + struct.pack(">I", 8)
            + struct.pack(">H", 2)
            + struct.pack(">HHII", 256, 3, 1, 800 << 16)
            + struct.pack(">HHII", 257, 3, 1, 600 << 16)
            + b"\x00\x00\x00\x00"
        )
        self.assertEqual(tiff_size(header), (800, 600))

    def test_scale_vector_applies_authored_dimensions_when_scale_is_absent(self) -> None:
        component = {"dimensions": {"width": 0.55, "height": 0.25, "depth": 0.15}}
        vector_str = scale_vector(component, {})
        self.assertIn("0.55", vector_str)
        self.assertIn("0.25", vector_str)
        self.assertIn("0.15", vector_str)


if __name__ == "__main__":
    unittest.main()
