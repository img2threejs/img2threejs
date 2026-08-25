#!/usr/bin/env python3
"""Tests for the interior appearance metric.

The property under test is the one that cost this project 35 correction loops: a model whose face
was deleted entirely scored 0.8803 on silhouette IoU against the finished face's 0.8803 -- identical
to four decimals -- because the outline never changed. Each test below asserts that the OLD metric
is blind to the same input the new one resolves, so the pair cannot silently regress into agreement.

Run: python3 forge/tests/test_interior_difference.py
"""
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage4_review"))

from diagnose_render import load_mask, silhouette_iou  # noqa: E402
from interior_difference import _bbox_corners, compare  # noqa: E402

PNG_SIG = b"\x89PNG\r\n\x1a\n"
SIZE = 256
GRID = 64

BACKGROUND = (18, 18, 20)
SKIN = (214, 190, 168)
FEATURE = (40, 30, 28)


def write_png(path: Path, pixel_fn) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = bytearray()
    for y in range(SIZE):
        raw.append(0)
        for x in range(SIZE):
            raw += bytes(pixel_fn(x, y))
    ihdr = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0)
    path.write_bytes(
        PNG_SIG + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    )


def _inside_figure(x: int, y: int) -> bool:
    """A standing blob: head disc on top, body slab below. Identical in every variant."""
    if 40 <= y < 100:
        return (x - 128) ** 2 + (y - 70) ** 2 < 30 * 30
    return 100 <= y < 220 and 100 <= x < 156


def blank_figure(x: int, y: int) -> tuple[int, int, int]:
    return SKIN if _inside_figure(x, y) else BACKGROUND


def featured_figure(x: int, y: int) -> tuple[int, int, int]:
    """Same silhouette, but with eyes and a mouth painted INSIDE the head."""
    if _inside_figure(x, y):
        if (x - 118) ** 2 + (y - 62) ** 2 < 5 * 5:
            return FEATURE
        if (x - 138) ** 2 + (y - 62) ** 2 < 5 * 5:
            return FEATURE
        if 78 <= y < 82 and 118 <= x < 138:
            return FEATURE
        return SKIN
    return BACKGROUND


class BboxConvention(unittest.TestCase):
    def test_returns_half_open_corners_not_origin_and_size(self) -> None:
        """Pins the convention. `diagnose_render.bbox_of` returns (x0, y0, w, h); this returns
        corners. Substituting one for the other produces confident garbage rather than an error."""
        width = height = 10
        mask = [False] * (width * height)
        for y in range(2, 6):
            for x in range(3, 8):
                mask[y * width + x] = True

        self.assertEqual(_bbox_corners(mask, width, height), (3, 2, 8, 6))

    def test_empty_mask_falls_back_to_whole_frame(self) -> None:
        self.assertEqual(_bbox_corners([False] * 16, 4, 4), (0, 0, 4, 4))


class InteriorDifference(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._dir = tempfile.mkdtemp(prefix="interior-difference-")
        cls.blank = Path(cls._dir) / "blank.png"
        cls.featured = Path(cls._dir) / "featured.png"
        write_png(cls.blank, blank_figure)
        write_png(cls.featured, featured_figure)

    def test_identical_images_score_zero(self) -> None:
        result = compare(self.featured, self.featured, grid=GRID)
        self.assertEqual(result["status"], "measured")
        self.assertEqual(result["interiorDifference"], 0.0)
        self.assertGreater(result["cellsCompared"], 0)

    def test_silhouette_iou_is_blind_to_the_face_this_metric_resolves(self) -> None:
        """The regression this whole module exists for.

        Adding eyes and a mouth changes no outline pixel, so IoU cannot move. If a future change
        makes IoU sensitive here the assertion below fails loudly rather than quietly making the
        interior metric redundant.
        """
        blank_mask, _ = load_mask(self.blank)
        featured_mask, _ = load_mask(self.featured)
        iou = silhouette_iou(blank_mask, featured_mask)

        result = compare(self.blank, self.featured, grid=GRID)

        self.assertEqual(iou, 1.0, "the two figures must share an identical silhouette")
        self.assertGreater(
            result["interiorDifference"],
            0.01,
            "an added face must register as interior difference even though IoU is 1.0",
        )

    def test_band_restriction_localises_the_change_to_the_head(self) -> None:
        head = compare(self.blank, self.featured, band_from=0.0, band_to=0.30, grid=GRID)
        body = compare(self.blank, self.featured, band_from=0.45, band_to=1.0, grid=GRID)

        self.assertGreater(head["interiorDifference"], body["interiorDifference"])
        self.assertAlmostEqual(body["interiorDifference"], 0.0, places=6)

    def test_reports_sample_count_so_a_thin_band_cannot_pass_as_evidence(self) -> None:
        result = compare(self.blank, self.featured, band_from=0.0, band_to=0.30, grid=GRID)
        self.assertIn("cellsCompared", result)
        self.assertGreater(result["cellsCompared"], 0)

    def test_a_figureless_render_reports_status_instead_of_a_number(self) -> None:
        """A capture that came out empty must not be scored.

        Note that two non-empty figures practically always overlap here: both are normalised to
        their own bounding box, so even a small corner blob is stretched across the same lattice.
        The reachable no-overlap case is a render with no foreground at all -- a failed capture,
        which the loop must refuse to score rather than rank.
        """
        empty = Path(self._dir) / "empty.png"
        write_png(empty, lambda x, y: BACKGROUND)

        result = compare(self.blank, empty, grid=GRID)

        self.assertIsNone(result["interiorDifference"])
        self.assertEqual(result["status"], "foreground-mask-fell-back-to-whole-frame")


class EvidenceWiring(unittest.TestCase):
    """The instrument existed for 35 loops and was never required, so it was never run.

    Building it again without wiring it into the gate would repeat exactly that.
    """

    def test_every_visual_pass_requires_banded_interior_difference(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage3_build"))
        from orchestrate_passes import VISUAL_PASS_IDS, next_required_evidence  # noqa: PLC0415

        for pass_id in sorted(VISUAL_PASS_IDS):
            with self.subTest(pass_id=pass_id):
                evidence = " ".join(next_required_evidence({}, pass_id))
                self.assertIn("interior_difference.py", evidence)

    def test_optimization_pass_is_not_asked_for_visual_evidence(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage3_build"))
        from orchestrate_passes import next_required_evidence  # noqa: PLC0415

        evidence = " ".join(next_required_evidence({}, "optimization-pass"))
        self.assertNotIn("interior_difference.py", evidence)


if __name__ == "__main__":
    unittest.main(verbosity=2)
