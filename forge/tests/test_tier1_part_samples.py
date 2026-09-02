#!/usr/bin/env python3
"""Tier-1 per-part colour check with viewer sample points.

Why this exists: `per_part_color_delta` originally compared every component's authored albedo
against the FIVE dominant Lab clusters of the whole render. A part covering a fraction of a
percent of the frame (a brass neck collar at 0.3%, a printed 6 px label border) can never
form one of those clusters, so its delta-E was measured against some other part's colour and
failed the material-pass gate no matter how right it was. Measured on the fire-extinguisher
reconstruction: collar dE 58.7 and frame dE 48.5 against a 20.0 threshold, while every large
part sat between 4 and 18.

The viewer can publish, per part, the render-pixel positions of that part's own visible
vertices (`samplePoints` in the part manifest). With those, the check reads the part's own
pixels. Without them it must keep the old behaviour, and it must say which method produced
each number.

Run: python3 forge/tests/test_tier1_part_samples.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stage4_review"))
sys.path.insert(0, str(ROOT / "stage1_intake"))

from diagnose_render import MIN_PART_SAMPLES, load_part_samples, per_part_color_delta  # noqa: E402
from extract_pbr_evidence import write_png_rgb  # noqa: E402

BACKGROUND = (235, 235, 235)
RED = (160, 20, 25)
DARK_RED = (90, 8, 12)
BLACK = (24, 24, 26)
GREY = (150, 152, 156)
WHITE = (200, 198, 204)
BLUE = (40, 80, 160)
BRASS = (200, 160, 60)
WIDTH = HEIGHT = 48


def write_render(path: Path) -> None:
    """Six large colour regions (the clusters `k=5` has to spend itself on) plus a 3x3 brass
    patch: the patch is 0.4% of the image and cannot claim a dominant cluster of its own."""
    rows = bytearray()
    for y in range(HEIGHT):
        for x in range(WIDTH):
            if not (8 <= x < 40 and 4 <= y < 44):
                colour = BACKGROUND
            elif 20 <= x < 23 and 6 <= y < 9:
                colour = BRASS
            elif y < 12:
                colour = GREY
            elif y < 16:
                colour = BLACK
            elif y < 30:
                colour = WHITE if 12 <= x < 36 else BLUE
            elif y < 38:
                colour = RED
            else:
                colour = DARK_RED
            rows.extend(colour)
    write_png_rgb(path, WIDTH, HEIGHT, bytes(rows))


def rgba(colour: tuple[int, int, int]) -> str:
    return f"rgba({colour[0]}, {colour[1]}, {colour[2]}, 1.0)"


class PartSampleColourTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.render = Path(self.tmp.name) / "render.png"
        write_render(self.render)
        self.recipes = [
            {"componentId": "body", "dominantAlbedo": rgba(RED)},
            {"componentId": "band", "dominantAlbedo": rgba(BLACK)},
            {"componentId": "valve", "dominantAlbedo": rgba(GREY)},
            {"componentId": "label", "dominantAlbedo": rgba(WHITE)},
            {"componentId": "collar", "dominantAlbedo": rgba(BRASS)},
        ]
        # Every brass pixel plus a few red ones, more than MIN_PART_SAMPLES in total: the
        # median must still land on brass, which is what makes the median the right statistic
        # for a footprint whose edge pixels blend into the neighbour.
        brass_points = [[x, y] for y in range(6, 9) for x in range(20, 23)]
        grey_points = [[x, y] for y in range(9, 11) for x in range(24, 28)]
        self.collar_points = brass_points * 3 + grey_points[:4]
        self.body_points = [[x, y] for y in range(30, 38, 2) for x in range(10, 38, 4)]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_tiny_part_fails_against_global_clusters(self) -> None:
        report = per_part_color_delta(self.recipes, self.render)
        by_id = {entry["componentId"]: entry for entry in report["perComponent"]}
        self.assertEqual(by_id["collar"]["method"], "global-cluster")
        self.assertGreater(by_id["collar"]["deltaE"], 20.0)
        # The large parts still pass the 20.0 gate through the cluster path (the red body's
        # cluster is pulled toward the dark-red band, so it is not near zero).
        self.assertLess(by_id["body"]["deltaE"], 20.0)
        self.assertEqual(report["sampledComponents"], 0)

    def test_sample_points_measure_the_part_itself(self) -> None:
        samples = {"collar": [tuple(p) for p in self.collar_points], "body": [tuple(p) for p in self.body_points]}
        report = per_part_color_delta(self.recipes, self.render, samples)
        by_id = {entry["componentId"]: entry for entry in report["perComponent"]}
        # Lab round-trips through 8-bit sRGB, so an exact colour still lands a hair above zero.
        self.assertEqual(by_id["collar"]["method"], "part-samples")
        self.assertLess(by_id["collar"]["deltaE"], 3.0)
        self.assertEqual(by_id["collar"]["sampleCount"], len(self.collar_points))
        self.assertEqual(by_id["body"]["method"], "part-samples")
        self.assertLess(by_id["body"]["deltaE"], 3.0)
        self.assertEqual(report["sampledComponents"], 2)
        # The three recipes without samples keep the cluster path and say so.
        for key in ("band", "valve", "label"):
            self.assertEqual(by_id[key]["method"], "global-cluster")
            self.assertEqual(by_id[key]["sampleCount"], 0)

    def test_too_few_samples_fall_back_and_say_so(self) -> None:
        samples = {"collar": [(21, 7)] * (MIN_PART_SAMPLES - 1)}
        report = per_part_color_delta(self.recipes, self.render, samples)
        by_id = {entry["componentId"]: entry for entry in report["perComponent"]}
        self.assertEqual(by_id["collar"]["method"], "global-cluster")
        self.assertEqual(by_id["collar"]["sampleCount"], MIN_PART_SAMPLES - 1)

    def test_off_image_points_are_ignored(self) -> None:
        samples = {"collar": [(-5, 7), (21, 999)] * 20 + [(21, 7)] * MIN_PART_SAMPLES}
        report = per_part_color_delta(self.recipes, self.render, samples)
        by_id = {entry["componentId"]: entry for entry in report["perComponent"]}
        self.assertEqual(by_id["collar"]["sampleCount"], MIN_PART_SAMPLES)
        self.assertEqual(by_id["collar"]["method"], "part-samples")

    def test_manifest_loader_keys_by_id_and_name(self) -> None:
        manifest = Path(self.tmp.name) / "parts.json"
        manifest.write_text(json.dumps({
            "model": "x",
            "parts": [
                {"name": "Brass collar", "id": "collar", "samplePoints": self.collar_points},
                {"name": "No samples", "id": "plain"},
                {"name": "Bad points", "id": "bad", "samplePoints": [[1, "a"], "nope", [2, 3]]},
            ],
        }), encoding="utf-8")
        samples = load_part_samples(manifest)
        self.assertEqual(samples["collar"], [tuple(p) for p in self.collar_points])
        self.assertEqual(samples["Brass collar"], samples["collar"])
        self.assertNotIn("plain", samples)
        self.assertEqual(samples["bad"], [(2, 3)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
