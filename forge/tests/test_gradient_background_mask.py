#!/usr/bin/env python3
"""Tests for the per-corner background model behind build_foreground_mask.

A lit scene rarely has one background colour: the corners of an interior
reference sit at different points of a lighting gradient. Modelling them as one
pooled sample set turns that gradient into "noise", and since the mask threshold
is a multiple of the noise, the gradient buys the background a tolerance wide
enough to swallow the subject. Each scene here is built directly as RGBA pixels
(no PNG round-trip) and keeps its subject at ~16% of the frame, clear of the
segmenter's 3.5% tiny-mask floor.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "stage1_intake"))
from extract_pbr_evidence import (  # noqa: E402
    build_foreground_mask,
    color_distance,
    median_color,
    percentile,
    sample_corner_background,
    saturation,
    srgb_luma,
)


SIZE = 120
SUBJECT_BOX = (36, 36, 84, 84)


def build_scene(background_at, subject):
    """A SIZE x SIZE RGBA scene: one subject block, clear of every corner window."""
    x0, y0, x1, y1 = SUBJECT_BOX
    pixels = []
    for y in range(SIZE):
        for x in range(SIZE):
            pixels.append(subject if x0 <= x < x1 and y0 <= y < y1 else background_at(x, y))
    return SIZE, SIZE, pixels


def subject_indices():
    x0, y0, x1, y1 = SUBJECT_BOX
    return [y * SIZE + x for y in range(y0, y1) for x in range(x0, x1)]


def background_indices():
    subject = set(subject_indices())
    return [index for index in range(SIZE * SIZE) if index not in subject]


def pooled_background(width, height, pixels):
    """Restate the superseded pooled-corner model: one median, one noise, one threshold.

    This lives in the test as the yardstick the fix is measured against, so the
    erosion can be shown by construction rather than by keeping dead code in the
    extractor. It mirrors the opaque colour path only.
    """
    radius = max(3, min(width, height) // 40)
    corner_ranges = [
        (0, radius, 0, radius),
        (width - radius, width, 0, radius),
        (0, radius, height - radius, height),
        (width - radius, width, height - radius, height),
    ]
    samples = []
    for x0, x1, y0, y1 in corner_ranges:
        for y in range(max(0, y0), min(height, y1)):
            for x in range(max(0, x0), min(width, x1)):
                red, green, blue, alpha = pixels[y * width + x]
                if alpha > 16:
                    samples.append((red, green, blue))
    background = median_color(samples)
    noise = percentile([color_distance(sample, background) for sample in samples], 0.75, 0.0)
    return background, max(24.0, noise * 2.4)


def pooled_mask(width, height, pixels):
    background, threshold = pooled_background(width, height, pixels)
    mask = []
    for red, green, blue, alpha in pixels:
        rgb = (red, green, blue)
        mask.append(
            alpha > 16
            and (
                color_distance(rgb, background) > threshold
                or (saturation(rgb) > 0.16 and srgb_luma(rgb) < 0.94)
            )
        )
    return mask


def gradient_scene():
    """Neutral subject on a corner-to-corner luminance ramp (~76 colour distance).

    The subject is grey, so the saturation/luma escape cannot rescue it — only the
    distance test decides, which is exactly what the per-corner model changes.
    """

    def background_at(x, y):
        value = 120 + round(44 * (x + y) / (2 * SIZE - 2))
        return (value, value, value, 255)

    return build_scene(background_at, (98, 98, 98, 255))


def uniform_scene():
    return build_scene(lambda x, y: (235, 235, 235, 255), (60, 60, 60, 255))


class GradientBackgroundTests(unittest.TestCase):
    def test_subject_survives_the_gradient(self):
        width, height, pixels = gradient_scene()
        mask, diagnostics, warnings = build_foreground_mask(width, height, pixels)
        self.assertTrue(all(mask[index] for index in subject_indices()))
        self.assertFalse(any(mask[index] for index in background_indices()))
        self.assertAlmostEqual(diagnostics["foregroundCoverage"], 0.16, places=4)
        self.assertEqual(warnings, [])

    def test_every_corner_threshold_stays_at_the_floor(self):
        width, height, pixels = gradient_scene()
        corners = sample_corner_background(width, height, pixels)
        self.assertEqual(len(corners), 4)
        for _median, noise in corners:
            self.assertEqual(max(24.0, noise * 2.4), 24.0)
        # The gradient lives BETWEEN the corners, so it must not appear within any
        # of them: the reported noise is the widest single corner's spread.
        _mask, diagnostics, _warnings = build_foreground_mask(width, height, pixels)
        self.assertLess(diagnostics["backgroundNoise"], 10.0)

    def test_the_pooled_model_would_have_erased_this_subject(self):
        width, height, pixels = gradient_scene()
        background, threshold = pooled_background(width, height, pixels)
        subject = pixels[subject_indices()[0]][:3]
        self.assertGreater(threshold, 24.0)
        self.assertLess(color_distance(subject, background), threshold)
        eroded = pooled_mask(width, height, pixels)
        self.assertFalse(any(eroded[index] for index in subject_indices()))


class UniformBackgroundTests(unittest.TestCase):
    def test_mask_matches_the_pooled_model(self):
        width, height, pixels = uniform_scene()
        mask, _diagnostics, warnings = build_foreground_mask(width, height, pixels)
        self.assertEqual(mask, pooled_mask(width, height, pixels))
        self.assertEqual(warnings, [])

    def test_subject_and_background_stay_separated(self):
        width, height, pixels = uniform_scene()
        mask, diagnostics, _warnings = build_foreground_mask(width, height, pixels)
        self.assertTrue(all(mask[index] for index in subject_indices()))
        self.assertFalse(any(mask[index] for index in background_indices()))
        self.assertAlmostEqual(diagnostics["foregroundCoverage"], 0.16, places=4)
        self.assertEqual(diagnostics["backgroundColor"], "#EBEBEB")
        self.assertEqual(diagnostics["backgroundNoise"], 0.0)

    def test_the_four_corner_medians_coincide(self):
        width, height, pixels = uniform_scene()
        corners = sample_corner_background(width, height, pixels)
        self.assertEqual([median for median, _noise in corners], [(235, 235, 235)] * 4)


class EmptyCornerTests(unittest.TestCase):
    def test_a_fully_transparent_image_reports_neutral_white(self):
        pixels = [(0, 0, 0, 0)] * (SIZE * SIZE)
        self.assertEqual(sample_corner_background(SIZE, SIZE, pixels), [((255, 255, 255), 0.0)])
        _mask, diagnostics, _warnings = build_foreground_mask(SIZE, SIZE, pixels)
        self.assertEqual(diagnostics["backgroundColor"], "#FFFFFF")
        self.assertEqual(diagnostics["backgroundNoise"], 0.0)

    def test_a_transparent_corner_is_skipped_not_read_as_white(self):
        # A near-white subject only survives if the empty corner contributes no
        # background at all; a neutral-white stand-in for it would swallow the subject.
        def background_at(x, y):
            if x < 4 and y < 4:
                return (0, 0, 0, 0)
            return (200, 200, 200, 255)

        width, height, pixels = build_scene(background_at, (250, 250, 250, 255))
        corners = sample_corner_background(width, height, pixels)
        self.assertEqual(corners, [((200, 200, 200), 0.0)] * 3)
        mask, diagnostics, _warnings = build_foreground_mask(width, height, pixels)
        self.assertLess(diagnostics["transparentPixelFraction"], 0.03)
        self.assertTrue(all(mask[index] for index in subject_indices()))
        self.assertFalse(any(mask[y * width + x] for y in range(4) for x in range(4)))


if __name__ == "__main__":
    unittest.main()
