"""Tests for content-based opposing view detection."""

from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from forge.stage1b_multi_view.content_detector import (
    ContentMatch,
    _bhattacharyya_distance,
    _compute_iou,
    _extract_color_histogram,
    _extract_foreground_mask,
    _flip_mask_horizontally,
    _otsu_threshold,
    detect_opposing_by_content,
    find_opposing_pairs_in_group,
)


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def write_rgb_png(path: Path, width: int, height: int, pixel_fn) -> None:
    """Write a minimal RGB PNG file."""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind)
        checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        for x in range(width):
            scanlines.extend(pixel_fn(x, y))
    path.write_bytes(
        PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(scanlines), 9))
        + chunk(b"IEND", b"")
    )


class OtsuThresholdTests(unittest.TestCase):
    def test_bimodal_distribution_separates_fg_bg(self) -> None:
        """Otsu should separate white background from dark foreground."""
        # Pure white image
        white = [255] * (10 * 10)
        threshold = _otsu_threshold(white, 10, 10)
        # All same value, threshold can be anything
        self.assertIn(threshold, range(256))

    def test_bimodal_image_finds_separation(self) -> None:
        """Image with clear foreground/background split."""
        pixels = [255] * 50 + [30] * 50  # 50 white, 50 dark
        threshold = _otsu_threshold(pixels, 10, 10)
        # Should separate around the gap
        self.assertGreaterEqual(threshold, 30)
        self.assertLess(threshold, 255)


class ForegroundMaskTests(unittest.TestCase):
    def test_white_background_dark_foreground(self) -> None:
        """Dark pixels should be foreground, white should be background."""
        # 4x4: top-left 2x2 is dark, rest is white
        pixels = []
        for y in range(4):
            for x in range(4):
                if x < 2 and y < 2:
                    pixels.append(10)  # dark foreground
                else:
                    pixels.append(255)  # white background

        mask = _extract_foreground_mask(pixels, 4, 4)
        # Foreground pixels should be True
        self.assertTrue(mask[0])  # (0,0)
        self.assertTrue(mask[1])  # (1,0)
        self.assertTrue(mask[4])  # (0,1)
        self.assertTrue(mask[5])  # (1,1)
        # Background pixels should be False
        self.assertFalse(mask[2])  # (2,0)
        self.assertFalse(mask[15])  # (3,3)


class IoUTests(unittest.TestCase):
    def test_identical_masks_have_iou_1(self) -> None:
        mask = [True, False, True, False]
        self.assertAlmostEqual(_compute_iou(mask, mask), 1.0)

    def test_disjoint_masks_have_iou_0(self) -> None:
        mask_a = [True, False, True, False]
        mask_b = [False, True, False, True]
        self.assertAlmostEqual(_compute_iou(mask_a, mask_b), 0.0)

    def test_partial_overlap(self) -> None:
        mask_a = [True, True, False, False]
        mask_b = [True, False, True, False]
        # intersection=1 (index 0), union=3 (indices 0,1,2)
        self.assertAlmostEqual(_compute_iou(mask_a, mask_b), 1.0 / 3.0)

    def test_empty_masks_return_0(self) -> None:
        mask_a = [False, False]
        mask_b = [False, False]
        self.assertAlmostEqual(_compute_iou(mask_a, mask_b), 0.0)


class FlipMaskTests(unittest.TestCase):
    def test_horizontal_flip(self) -> None:
        mask = [True, False, False, False]  # 2x2: top-left only
        flipped = _flip_mask_horizontally(mask, 2, 2)
        # Flipped: top-right should be True
        self.assertFalse(flipped[0])
        self.assertTrue(flipped[1])


class BhattacharyyaTests(unittest.TestCase):
    def test_identical_histograms_have_distance_0(self) -> None:
        hist = [0.25, 0.25, 0.25, 0.25]
        self.assertAlmostEqual(_bhattacharyya_distance(hist, hist), 0.0)

    def test_disjoint_histograms_have_distance_1(self) -> None:
        hist_a = [1.0, 0.0, 0.0, 0.0]
        hist_b = [0.0, 0.0, 0.0, 1.0]
        self.assertAlmostEqual(_bhattacharyya_distance(hist_a, hist_b), 1.0)

    def test_similar_histograms_have_small_distance(self) -> None:
        hist_a = [0.5, 0.3, 0.1, 0.1]
        hist_b = [0.5, 0.25, 0.15, 0.1]
        dist = _bhattacharyya_distance(hist_a, hist_b)
        self.assertLess(dist, 0.1)


class ColorHistogramTests(unittest.TestCase):
    def test_masked_histogram_ignores_background(self) -> None:
        """Histogram should only count pixels where mask is True."""
        rgb = [
            (255, 255, 255),  # white - ignored
            (200, 0, 0),      # red - counted
            (255, 255, 255),  # white - ignored
            (0, 0, 200),      # blue - counted
        ]
        mask = [False, True, False, True]
        hist = _extract_color_histogram(rgb, mask, 2, 2, bins=4)
        # Non-zero entries should sum to 1.0 (normalized)
        self.assertAlmostEqual(sum(hist), 1.0)
        # Should have non-zero entries
        self.assertGreater(sum(hist), 0)


class ContentDetectorTests(unittest.TestCase):
    def test_same_shape_different_colors_detected_as_opposing(self) -> None:
        """Two images with same rectangle shape but different colors should be opposing."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Red rectangle on white background
            red_rect = root / "red.png"
            write_rgb_png(red_rect, 64, 64, lambda x, y: (200, 0, 0)
                          if 12 <= x < 52 and 12 <= y < 52
                          else (255, 255, 255))

            # Blue rectangle on white background (same shape)
            blue_rect = root / "blue.png"
            write_rgb_png(blue_rect, 64, 64, lambda x, y: (0, 0, 200)
                          if 12 <= x < 52 and 12 <= y < 52
                          else (255, 255, 255))

            result = detect_opposing_by_content(red_rect, blue_rect)

        self.assertIsInstance(result, ContentMatch)
        self.assertGreater(result.iou, 0.75, "Same shape should have high IoU")
        self.assertGreater(result.bhattacharyya, 0.3, "Different colors should have high Bhattacharyya")
        self.assertTrue(result.is_opposing)

    def test_different_shapes_not_opposing(self) -> None:
        """Two images with different shapes should not be opposing."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Small rectangle
            small = root / "small.png"
            write_rgb_png(small, 64, 64, lambda x, y: (200, 40, 40)
                          if 20 <= x < 44 and 20 <= y < 44
                          else (255, 255, 255))

            # Large rectangle (different shape)
            large = root / "large.png"
            write_rgb_png(large, 64, 64, lambda x, y: (200, 40, 40)
                          if 4 <= x < 60 and 4 <= y < 60
                          else (255, 255, 255))

            result = detect_opposing_by_content(small, large)

        # Different shapes should have low IoU
        self.assertLess(result.iou, 0.75)
        self.assertFalse(result.is_opposing)

    def test_identical_images_not_opposing(self) -> None:
        """Identical images should not be opposing (same color = low Bhattacharyya)."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            img_a = root / "a.png"
            img_b = root / "b.png"
            write_rgb_png(img_a, 64, 64, lambda x, y: (200, 40, 40)
                          if 12 <= x < 52 and 12 <= y < 52
                          else (255, 255, 255))
            write_rgb_png(img_b, 64, 64, lambda x, y: (200, 40, 40)
                          if 12 <= x < 52 and 12 <= y < 52
                          else (255, 255, 255))

            result = detect_opposing_by_content(img_a, img_b)

        self.assertGreater(result.iou, 0.95, "Identical shapes should have very high IoU")
        self.assertLess(result.bhattacharyya, 0.3, "Identical colors should have low Bhattacharyya")
        self.assertFalse(result.is_opposing)


class FindPairsTests(unittest.TestCase):
    def test_finds_opposing_pair_in_group(self) -> None:
        """find_opposing_pairs_in_group should find the red/blue pair."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Red rectangle (fills most of the image)
            red = root / "red.png"
            write_rgb_png(red, 64, 64, lambda x, y: (200, 0, 0)
                          if 4 <= x < 60 and 4 <= y < 60
                          else (255, 255, 255))

            # Blue rectangle (same shape, different color)
            blue = root / "blue.png"
            write_rgb_png(blue, 64, 64, lambda x, y: (0, 0, 200)
                          if 4 <= x < 60 and 4 <= y < 60
                          else (255, 255, 255))

            # Small green square (very different shape/size)
            green = root / "green.png"
            write_rgb_png(green, 64, 64, lambda x, y: (0, 200, 0)
                          if 28 <= x < 36 and 28 <= y < 36
                          else (255, 255, 255))

            paths = [red, blue, green]
            pairs = find_opposing_pairs_in_group(paths)

        # Should find only red/blue as opposing (green is too different in size)
        red_blue_found = any(
            {paths[i], paths[j]} == {red, blue}
            for i, j, _ in pairs
        )
        self.assertTrue(red_blue_found, "Red/blue pair should be detected")

    def test_no_opposing_pairs_in_group_of_circles(self) -> None:
        """Group of different-sized circles should have no opposing pairs."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for i, radius in enumerate([10, 15, 20]):
                path = root / f"circle_{i}.png"
                write_rgb_png(path, 64, 64, lambda x, y, r=radius: (100, 100, 100)
                              if ((x - 32) ** 2 + (y - 32) ** 2) < r ** 2
                              else (255, 255, 255))
                paths.append(path)

            pairs = find_opposing_pairs_in_group(paths)

        # Different shapes should not be opposing
        self.assertEqual(len(pairs), 0)


if __name__ == "__main__":
    unittest.main()
