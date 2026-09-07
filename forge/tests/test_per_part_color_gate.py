#!/usr/bin/env python3
"""Tests for the material-pass per-part colour gate.

The contract under test: a component whose material-region analysis mapped a reference crop
onto it is scored on that region (reference pixels vs the render's pixels in the same place),
a component without that evidence keeps the whole-frame cluster comparison, the verdict records
how many components were scored each way, and broken region evidence stops the run.

The sanity criterion is the first test: the reference scored against itself PASSES on the region
basis and FAILS on the cluster basis, at the same 20.0 threshold and on the same pixels — the
fix changes the basis, not the bar.
"""

from __future__ import annotations

import contextlib
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
sys.path.insert(0, str(ROOT / "stage1_intake"))

import diagnose_render  # noqa: E402
from diagnose_render import (  # noqa: E402
    COLOR_DELTA_E_THRESHOLD,
    current_region_crops,
    per_part_color_delta,
    run_tier1,
)
from extract_part_color_recipe import lab_distance, lab_kmeans_palette, srgb_to_lab  # noqa: E402
from extract_pbr_evidence import build_foreground_mask, load_image  # noqa: E402

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

SIZE = 96
SUBJECT = (16, 16, 80, 80)          # the subject block; everything outside is backdrop
STRIPES = 8                          # one material band per component
BAND = (SUBJECT[3] - SUBJECT[1]) // STRIPES
# Eight widely separated materials: more distinct colours than the five whole-frame clusters the
# fallback basis can hold, which is the condition the cluster comparison cannot represent.
MATERIALS = [
    (198, 82, 44), (60, 110, 190), (40, 150, 90), (215, 190, 70),
    (150, 60, 160), (90, 90, 95), (230, 140, 170), (35, 40, 45),
]


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


def stripe_index(x: int, y: int) -> int | None:
    x0, y0, x1, y1 = SUBJECT
    if not (x0 <= x < x1 and y0 <= y < y1):
        return None
    return min(STRIPES - 1, (y - y0) // BAND)


def scene(recolour: dict[int, tuple[int, int, int]] | None = None):
    recolour = recolour or {}
    def pixel(x: int, y: int) -> tuple[int, int, int]:
        index = stripe_index(x, y)
        if index is None:
            return (250, 250, 250)
        return recolour.get(index, MATERIALS[index])
    return pixel


def stripe_bbox(index: int) -> dict[str, int]:
    """A crop wholly inside one band, the way a picked material region is."""
    x0, y0, x1, _y1 = SUBJECT
    return {"x": x0 + 4, "y": y0 + index * BAND + 1, "width": (x1 - x0) - 8, "height": BAND - 2}


def build_spec(*, with_regions: bool) -> dict:
    components = []
    regions = []
    for index, (red, green, blue) in enumerate(MATERIALS):
        component = {
            "id": f"part-{index}",
            "colorMaterialRecipe": {"dominantAlbedo": f"rgba({red}, {green}, {blue}, 1.0)"},
        }
        if with_regions:
            component["materialRegions"] = [{
                "regionId": f"material-{index}",
                "materialId": f"material-{index}",
                "crop": {
                    "bbox": stripe_bbox(index),
                    "sourceWidth": SIZE,
                    "sourceHeight": SIZE,
                },
            }]
            regions.append({
                "componentId": f"part-{index}",
                "regionId": f"material-{index}",
                "specMaterialId": f"material-{index}",
                "status": "proceed",
            })
        components.append(component)
    spec: dict = {"componentTree": components}
    if with_regions:
        spec["materialPipeline"] = {"schemaVersion": 1, "status": "proceed", "regions": regions}
    return spec


class PerPartColorGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        self.reference = self.workspace / "reference.png"
        self.render = self.workspace / "render.png"
        write_rgb_png(self.reference, SIZE, SIZE, scene())
        write_rgb_png(self.render, SIZE, SIZE, scene())
        self.addCleanup(self._tmp.cleanup)

    def write_spec(self, spec: dict) -> Path:
        path = self.workspace / "spec.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        return path

    def cluster_deltas(self, spec: dict) -> list[float]:
        """The whole-frame cluster comparison, computed here independently of the gate."""
        width, height, pixels, _warnings = load_image(self.render)
        mask, _diag, _warn = build_foreground_mask(width, height, pixels)
        foreground = [srgb_to_lab((r, g, b)) for (r, g, b, _a), keep in zip(pixels, mask) if keep]
        components = spec["componentTree"]
        clusters = lab_kmeans_palette(foreground, k=min(5, max(1, len(components))))
        deltas = []
        for component in components:
            text = component["colorMaterialRecipe"]["dominantAlbedo"]
            red, green, blue = (int(float(p)) for p in text[text.index("(") + 1:text.index(")")].split(",")[:3])
            expected = srgb_to_lab((red, green, blue))
            deltas.append(round(min(lab_distance(expected, c["center"]) for c in clusters), 2))
        return deltas

    # --- the sanity criterion --------------------------------------------------------------
    def test_reference_against_itself_passes_on_region_basis_and_fails_on_cluster_basis(self):
        region = per_part_color_delta(build_spec(with_regions=True), self.render, self.reference)
        cluster = per_part_color_delta(build_spec(with_regions=False), self.render, self.reference)
        self.assertEqual(region["maxDeltaE"], 0.0)
        self.assertLessEqual(region["maxDeltaE"], COLOR_DELTA_E_THRESHOLD)
        self.assertGreater(cluster["maxDeltaE"], COLOR_DELTA_E_THRESHOLD)
        self.assertEqual((region["regionBasis"], region["clusterBasis"]), (STRIPES, 0))
        self.assertEqual((cluster["regionBasis"], cluster["clusterBasis"]), (0, STRIPES))

    def test_the_gate_itself_is_reachable_for_a_faithful_render(self):
        spec_path = self.write_spec(build_spec(with_regions=True))
        verdict = run_tier1(self.reference, self.render, spec_path, "material-pass")
        self.assertTrue(verdict["passed"], verdict["failures"])
        self.assertEqual(verdict["checks"]["colorDelta"]["maxDeltaE"], 0.0)
        self.assertTrue(verdict["checks"]["colorDelta"]["gated"])

    def test_the_same_render_without_region_evidence_still_fails_the_gate(self):
        spec_path = self.write_spec(build_spec(with_regions=False))
        verdict = run_tier1(self.reference, self.render, spec_path, "material-pass")
        self.assertFalse(verdict["passed"])
        self.assertTrue(any("per-part color delta-E" in f for f in verdict["failures"]), verdict["failures"])

    # --- the basis is recorded, and the fallback is unchanged -------------------------------
    def test_component_without_region_evidence_keeps_the_cluster_comparison(self):
        spec = build_spec(with_regions=True)
        # one part loses its evidence; the current analysis stops mapping it
        spec["componentTree"][3].pop("materialRegions")
        spec["materialPipeline"]["regions"] = [
            entry for entry in spec["materialPipeline"]["regions"] if entry["componentId"] != "part-3"
        ]
        report = per_part_color_delta(spec, self.render, self.reference)
        self.assertEqual((report["regionBasis"], report["clusterBasis"]), (STRIPES - 1, 1))
        fallback = [e for e in report["perComponent"] if e["basis"] == "cluster"]
        self.assertEqual([e["componentId"] for e in fallback], ["part-3"])
        self.assertEqual(fallback[0]["deltaE"], self.cluster_deltas(build_spec(with_regions=False))[3])

    def test_region_entries_name_their_region_and_matched_share(self):
        report = per_part_color_delta(build_spec(with_regions=True), self.render, self.reference)
        entry = report["perComponent"][0]
        self.assertEqual(entry["componentId"], "part-0")
        self.assertEqual(entry["regionId"], "material-0")
        self.assertEqual(entry["basis"], "region")
        self.assertGreater(entry["matchedClusterShare"], 0.0)

    def test_a_spec_without_any_region_analysis_scores_exactly_as_the_cluster_comparison(self):
        spec = build_spec(with_regions=False)
        report = per_part_color_delta(spec, self.render, self.reference)
        self.assertEqual([e["deltaE"] for e in report["perComponent"]], self.cluster_deltas(spec))
        self.assertEqual(report["checked"], STRIPES)
        self.assertTrue(all(e["basis"] == "cluster" for e in report["perComponent"]))

    def test_superseded_and_relabelled_region_entries_are_ignored(self):
        # apply_material_analysis appends on every run; the pipeline block is replaced, so it is
        # the mapping of record and the last appended crop is the current one.
        spec = build_spec(with_regions=True)
        stale = {"regionId": "material-0", "materialId": "material-0",
                 "crop": {"bbox": stripe_bbox(5), "sourceWidth": SIZE, "sourceHeight": SIZE}}
        spec["componentTree"][0]["materialRegions"].insert(0, stale)
        spec["componentTree"][1]["materialRegions"].append({
            "regionId": "material-9", "materialId": "material-9",
            "crop": {"bbox": stripe_bbox(7), "sourceWidth": SIZE, "sourceHeight": SIZE},
        })
        crops = current_region_crops(spec)
        self.assertEqual(crops["part-0"]["material-0"]["bbox"], stripe_bbox(0))
        self.assertEqual(sorted(crops["part-1"]), ["material-1"])

    # --- a real per-part miss is what fails now ---------------------------------------------
    def test_a_recoloured_part_fails_and_the_verdict_names_it(self):
        write_rgb_png(self.render, SIZE, SIZE, scene(recolour={5: (250, 250, 60)}))
        spec_path = self.write_spec(build_spec(with_regions=True))
        verdict = run_tier1(self.reference, self.render, spec_path, "material-pass")
        report = verdict["checks"]["colorDelta"]
        over = [e for e in report["perComponent"] if e["deltaE"] > COLOR_DELTA_E_THRESHOLD]
        self.assertEqual([e["componentId"] for e in over], ["part-5"])
        self.assertEqual(over[0]["regionId"], "material-5")
        self.assertFalse(verdict["passed"])
        self.assertTrue(any("worst part-5 on the region basis" in f for f in verdict["failures"]),
                        verdict["failures"])

    def test_color_is_not_gated_before_material_pass(self):
        write_rgb_png(self.render, SIZE, SIZE, scene(recolour={5: (250, 250, 60)}))
        spec_path = self.write_spec(build_spec(with_regions=True))
        verdict = run_tier1(self.reference, self.render, spec_path, "blockout")
        self.assertFalse(verdict["checks"]["colorDelta"]["gated"])
        self.assertFalse(any("color delta-E" in f for f in verdict["failures"]), verdict["failures"])

    # --- broken evidence stops the run ------------------------------------------------------
    def run_cli(self, spec_path: Path) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = diagnose_render.main([
                "--reference", str(self.reference), "--render", str(self.render),
                "--spec", str(spec_path), "--pass-id", "material-pass", "--json",
            ])
        return code, out.getvalue(), err.getvalue()

    def test_region_evidence_cut_from_another_image_is_a_hard_error(self):
        spec = build_spec(with_regions=True)
        spec["componentTree"][2]["materialRegions"][0]["crop"]["sourceWidth"] = SIZE * 2
        code, out, err = self.run_cli(self.write_spec(spec))
        self.assertEqual(code, 1)
        self.assertIn("material region 'material-2' on component 'part-2'", err)
        self.assertIn("192x96", err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn('"passed"', out)

    def test_region_bbox_outside_the_reference_is_a_hard_error(self):
        spec = build_spec(with_regions=True)
        spec["componentTree"][2]["materialRegions"][0]["crop"]["bbox"] = {
            "x": SIZE - 4, "y": 0, "width": 40, "height": 10,
        }
        code, _out, err = self.run_cli(self.write_spec(spec))
        self.assertEqual(code, 1)
        self.assertIn("outside the 96x96 reference", err)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
