#!/usr/bin/env python3
"""Tests for the adapter-provided reference mask in the Divine Eye's tier-1 scoring.

The contract under test: a workspace that publishes `.img2threejs/reference-mask.png` plus its
provenance sidecar scores the REFERENCE silhouette with that artifact; a workspace that publishes
nothing scores exactly as before; and an artifact that cannot be authenticated stops the run with
a named cause instead of quietly reverting to the heuristic.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "stage4_review"))

import diagnose_render  # noqa: E402
import divine_eye  # noqa: E402
from divine_eye import _foreground_hsv_stats, evaluate  # noqa: E402
from diagnose_render import condition_mask, load_mask, run_tier1, silhouette_iou  # noqa: E402
from reference_mask_artifact import (  # noqa: E402
    MASK_SOURCE_ARTIFACT,
    MASK_SOURCE_HEURISTIC,
    ReferenceMaskError,
    load_reference_mask_artifact,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

SIZE = 64
SUBJECT = (16, 12, 48, 52)          # x0, y0, x1, y1 of the reference subject
ARTIFACT_REGION = (16, 12, 48, 32)  # deliberately only the subject's upper half


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def write_rgb_png(path: Path, width: int, height: int, pixel_fn) -> None:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw += bytes(pixel_fn(x, y))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(PNG_SIGNATURE + _chunk(b"IHDR", ihdr)
                     + _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _chunk(b"IEND", b""))


def write_gray_png(path: Path, width: int, height: int, value_fn) -> None:
    """Grayscale 8-bit PNG — the exact shape `run_vision_adapter.py segment` writes."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.append(value_fn(x, y))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    path.write_bytes(PNG_SIGNATURE + _chunk(b"IHDR", ihdr)
                     + _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _chunk(b"IEND", b""))


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def in_rect(x: int, y: int, rect: tuple[int, int, int, int]) -> bool:
    x0, y0, x1, y1 = rect
    return x0 <= x < x1 and y0 <= y < y1


def reference_pixels(x: int, y: int) -> tuple[int, int, int]:
    """Subject on a banded backdrop — the image class the corner heuristic cannot segment."""
    if in_rect(x, y, SUBJECT):
        return (200, 40, 40)
    return (150 + (y // 8) * 12, 150 + (y // 8) * 10, 160 + (y // 8) * 8)


def render_pixels(x: int, y: int) -> tuple[int, int, int]:
    """Same subject on the uniform backdrop a render always has."""
    return (200, 40, 40) if in_rect(x, y, SUBJECT) else (255, 255, 255)


class ReferenceMaskArtifactTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        self.state = self.workspace / ".img2threejs"
        self.state.mkdir()
        self.reference = self.workspace / "reference.png"
        self.render = self.workspace / "renders" / "render.png"
        self.render.parent.mkdir()
        write_rgb_png(self.reference, SIZE, SIZE, reference_pixels)
        write_rgb_png(self.render, SIZE, SIZE, render_pixels)
        self.addCleanup(self._tmp.cleanup)

    # --- helpers ---------------------------------------------------------------------------
    def write_artifact(self, *, mask_size: int = SIZE, sidecar_name: str = "reference-mask.json",
                       source_sha: str | None = None, kind: str = "segmentation-mask",
                       write_sidecar: bool = True, image_size: list[int] | None = None,
                       corrupt: bool = False) -> Path:
        mask_path = self.state / "reference-mask.png"
        if corrupt:
            ihdr = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 0, 0, 0, 0)
            mask_path.write_bytes(PNG_SIGNATURE + _chunk(b"IHDR", ihdr)
                                  + _chunk(b"IDAT", b"not-deflate-data") + _chunk(b"IEND", b""))
        else:
            scale = mask_size / SIZE
            write_gray_png(
                mask_path, mask_size, mask_size,
                lambda x, y: 255 if in_rect(int(x / scale), int(y / scale), ARTIFACT_REGION) else 0,
            )
        if write_sidecar:
            payload = {
                "kind": kind,
                "model": "facebook/sam2.1-hiera-large",
                "sourceImage": str(self.reference),
                "sourceSha256": source_sha if source_sha is not None else sha256_of(self.reference),
                "output": str(mask_path),
                "outputSha256": sha256_of(mask_path),
                "imageSize": image_size if image_size is not None else [SIZE, SIZE],
                "boundary": "mask evidence only; agent must confirm the selected subject/component",
            }
            (self.state / sidecar_name).write_text(json.dumps(payload), encoding="utf-8")
        return mask_path

    def artifact_iou(self) -> float:
        """IoU the artifact mask implies, computed independently of divine_eye.evaluate."""
        full = [in_rect(x, y, ARTIFACT_REGION) for y in range(SIZE) for x in range(SIZE)]
        grid, _warnings = condition_mask(full, SIZE, SIZE)
        return silhouette_iou(grid, load_mask(self.render)[0])

    def heuristic_iou(self) -> float:
        return silhouette_iou(load_mask(self.reference)[0], load_mask(self.render)[0])

    def run_cli(self) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = divine_eye.main(["--reference", str(self.reference),
                                    "--render", str(self.render), "--json"])
        return code, out.getvalue(), err.getvalue()

    # --- artifact honored ------------------------------------------------------------------
    def test_artifact_mask_scores_the_reference_silhouette(self):
        self.write_artifact()
        result = evaluate(self.reference, self.render)
        self.assertEqual(result["referenceMaskSource"], MASK_SOURCE_ARTIFACT)
        self.assertEqual(result["signals"]["silhouetteIoU"], round(self.artifact_iou(), 4))
        self.assertNotAlmostEqual(self.artifact_iou(), self.heuristic_iou(), places=3,
                                  msg="fixture must make the two rulers disagree")

    def test_adapter_default_sidecar_name_is_accepted(self):
        self.write_artifact(sidecar_name="reference-mask.png.json")
        result = evaluate(self.reference, self.render)
        self.assertEqual(result["referenceMaskSource"], MASK_SOURCE_ARTIFACT)

    def test_artifact_found_from_a_reference_nested_in_the_workspace(self):
        nested = self.workspace / "intake" / "reference.png"
        nested.parent.mkdir()
        write_rgb_png(nested, SIZE, SIZE, reference_pixels)
        self.write_artifact()  # sha of the identical bytes matches either path
        result = evaluate(nested, self.render)
        self.assertEqual(result["referenceMaskSource"], MASK_SOURCE_ARTIFACT)

    def test_artifact_mask_drives_the_report_only_colour_signals_too(self):
        # One run, one ruler: the reference-side colour helpers must read the same mask the
        # silhouette did, or the evidence field would describe only half the measurement.
        self.write_artifact()
        artifact_full = [in_rect(x, y, ARTIFACT_REGION) for y in range(SIZE) for x in range(SIZE)]
        _hue, artifact_sat = _foreground_hsv_stats(self.reference, artifact_full)
        _hue, heuristic_sat = _foreground_hsv_stats(self.reference)
        _hue, render_sat = _foreground_hsv_stats(self.render)
        self.assertNotAlmostEqual(artifact_sat, heuristic_sat, places=3,
                                  msg="fixture must make the two rulers disagree on colour too")
        result = evaluate(self.reference, self.render)
        self.assertEqual(result["specularWash"]["satRatio"], round(render_sat / artifact_sat, 3))

    # --- absent artifact: today's behavior -------------------------------------------------
    def test_absent_artifact_keeps_the_heuristic_untouched(self):
        result = evaluate(self.reference, self.render)
        self.assertEqual(result["referenceMaskSource"], MASK_SOURCE_HEURISTIC)
        self.assertIsNone(result["referenceMaskArtifact"])
        self.assertEqual(result["signals"]["silhouetteIoU"], round(self.heuristic_iou(), 4))

    def test_reference_outside_any_workspace_keeps_the_heuristic(self):
        with tempfile.TemporaryDirectory() as outside:
            lonely = Path(outside) / "reference.png"
            write_rgb_png(lonely, SIZE, SIZE, reference_pixels)
            self.assertIsNone(load_reference_mask_artifact(lonely))
            result = evaluate(lonely, self.render)
        self.assertEqual(result["referenceMaskSource"], MASK_SOURCE_HEURISTIC)

    def test_evidence_records_the_artifact_path_when_one_scored(self):
        mask_path = self.write_artifact()
        result = evaluate(self.reference, self.render)
        self.assertEqual(result["referenceMaskArtifact"], str(mask_path.resolve()))

    # --- the official tier-1 verdict (diagnose_render) ------------------------------------
    def run_tier1_cli(self) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = diagnose_render.main(["--reference", str(self.reference),
                                         "--render", str(self.render), "--json"])
        return code, out.getvalue(), err.getvalue()

    def test_tier1_verdict_scores_with_the_artifact(self):
        self.write_artifact()
        verdict = run_tier1(self.reference, self.render)
        self.assertEqual(verdict["referenceMaskSource"], MASK_SOURCE_ARTIFACT)
        self.assertEqual(verdict["checks"]["silhouetteIoU"], round(self.artifact_iou(), 4))

    def test_tier1_verdict_and_divine_eye_measure_the_same_silhouette(self):
        # The gap this closes: two scorers, two rulers, same bytes.
        self.write_artifact()
        verdict = run_tier1(self.reference, self.render)
        eye = evaluate(self.reference, self.render)
        self.assertEqual(verdict["checks"]["silhouetteIoU"], eye["signals"]["silhouetteIoU"])
        self.assertEqual(verdict["referenceMaskSource"], eye["referenceMaskSource"])

    def test_tier1_verdict_without_an_artifact_is_unchanged(self):
        verdict = run_tier1(self.reference, self.render)
        self.assertEqual(verdict["referenceMaskSource"], MASK_SOURCE_HEURISTIC)
        self.assertEqual(verdict["checks"]["silhouetteIoU"], round(self.heuristic_iou(), 4))
        self.assertEqual(verdict["maskWarnings"],
                         [f"reference: {w}" for w in load_mask(self.reference)[1]]
                         + [f"render: {w}" for w in load_mask(self.render)[1]])

    def test_tier1_cli_fails_loud_on_an_unauthenticated_artifact(self):
        self.write_artifact(source_sha="0" * 64)
        code, out, err = self.run_tier1_cli()
        self.assertEqual(code, 1)
        self.assertIn("cut from a different image", err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn('"passed"', out)

    # --- fail loud -------------------------------------------------------------------------
    def assert_cli_fails_naming(self, *fragments: str) -> str:
        code, _out, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)
        for fragment in fragments:
            self.assertIn(fragment, err)
        return err

    def test_wrong_dimensions_is_a_hard_error(self):
        self.write_artifact(mask_size=32)
        with self.assertRaises(ReferenceMaskError) as caught:
            evaluate(self.reference, self.render)
        self.assertIn("32x32", str(caught.exception))
        self.assert_cli_fails_naming("reference mask", "32x32", "64x64")

    def test_missing_sidecar_is_a_hard_error(self):
        self.write_artifact(write_sidecar=False)
        self.assert_cli_fails_naming("no provenance sidecar")

    def test_sha_mismatch_is_a_hard_error(self):
        self.write_artifact(source_sha="0" * 64)
        self.assert_cli_fails_naming("cut from a different image")

    def test_corrupt_mask_png_is_a_hard_error(self):
        self.write_artifact(corrupt=True)
        self.assert_cli_fails_naming("could not be decoded")

    def test_unreadable_sidecar_is_a_hard_error(self):
        self.write_artifact()
        (self.state / "reference-mask.json").write_text("{not json", encoding="utf-8")
        self.assert_cli_fails_naming("is unreadable")

    def test_wrong_sidecar_kind_is_a_hard_error(self):
        self.write_artifact(kind="depth-map")
        self.assert_cli_fails_naming("declares kind 'depth-map'")

    def test_sidecar_declaring_another_image_size_is_a_hard_error(self):
        self.write_artifact(image_size=[128, 128])
        self.assert_cli_fails_naming("declares imageSize [128, 128]")

    def test_mask_edited_after_its_sidecar_is_a_hard_error(self):
        self.write_artifact()
        write_gray_png(self.state / "reference-mask.png", SIZE, SIZE,
                       lambda x, y: 255 if in_rect(x, y, SUBJECT) else 0)
        self.assert_cli_fails_naming("changed after its sidecar was written")

    def test_empty_mask_is_a_hard_error(self):
        write_gray_png(self.state / "reference-mask.png", SIZE, SIZE, lambda x, y: 0)
        mask_path = self.state / "reference-mask.png"
        (self.state / "reference-mask.json").write_text(json.dumps({
            "kind": "segmentation-mask",
            "sourceSha256": sha256_of(self.reference),
            "outputSha256": sha256_of(mask_path),
            "imageSize": [SIZE, SIZE],
        }), encoding="utf-8")
        self.assert_cli_fails_naming("marks no foreground pixels")


if __name__ == "__main__":
    unittest.main()
