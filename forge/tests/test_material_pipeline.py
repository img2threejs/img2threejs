#!/usr/bin/env python3
"""End-to-end contracts for the nine-phase material pipeline."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "stage1_intake"))
sys.path.insert(0, str(ROOT / "stage2_spec"))
sys.path.insert(0, str(ROOT / "stage4_review"))

from materials.compatibility import check_compatibility  # noqa: E402
from materials.reference import build_assignment, load_reference, resolve_material  # noqa: E402
from material_region_analysis import analyze_manifest  # noqa: E402
from apply_material_analysis import apply_material_analysis  # noqa: E402
from material_comparator import compare_material_crops  # noqa: E402
from material_feedback import apply_material_feedback  # noqa: E402
from material_gate import run_material_gate  # noqa: E402
from material_views import build_view_plan, crop_visible_footprint, validate_capture  # noqa: E402


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def write_png(path: Path, width: int, height: int, fn) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return len(data).to_bytes(4, "big") + kind + data + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend(bytes(fn(x, y)))
    header = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes((8, 2, 0, 0, 0))
    path.write_bytes(PNG_SIGNATURE + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b""))


class MaterialPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="img2threejs-material-pipeline-"))
        self.registry = load_reference()
        self.reference = self.tmp / "armor.png"
        write_png(
            self.reference,
            512,
            512,
            lambda x, y: (246, 246, 246) if x < 64 or x >= 448 or y < 64 or y >= 448 else (
                24 + (x * 5 + y * 3) % 190,
                42 + (x * 7 + y * 11) % 150,
                80 + (x * 13 + y * 17) % 150,
            ),
        )

    def test_phase_one_resolver_preserves_authority_and_ambiguity(self) -> None:
        exact = resolve_material({"materialId": "coating.painted-metal", "confidence": 0.2}, self.registry)
        self.assertEqual(exact["status"], "proceed")
        self.assertEqual(exact["method"], "explicit-material-id")
        assignment = build_assignment({"family": "coating", "subtype": "paint-over-metal", "finish": "gloss-or-satin", "confidence": 0.9}, {}, self.registry)
        self.assertEqual(assignment["profileId"], "coating.painted-metal")
        self.assertIn("roughness", assignment["renderPrior"])
        ambiguous = resolve_material({"family": "metal", "confidence": 0.4}, self.registry)
        self.assertIn(ambiguous["status"], {"request-input", "probe"})

    def test_phases_two_to_nine_vertical_slice(self) -> None:
        manifest = self.tmp / "regions.json"
        manifest.write_text(json.dumps({
            "referenceId": "armor-fixture",
            "regions": [{
                "componentId": "torso-armor",
                "regionId": "paint",
                "sourceImage": str(self.reference),
                "bbox": {"x": 64, "y": 64, "width": 384, "height": 384},
                "family": "coating",
                "subtype": "paint-over-metal",
                "finish": "gloss-or-satin",
                "materialSpecId": "armor-paint",
            }],
        }, indent=2), encoding="utf-8")
        analysis = analyze_manifest(manifest, self.tmp / "analysis-data")
        self.assertEqual(analysis["status"], "proceed")
        region = analysis["regions"][0]
        self.assertEqual(region["assignment"]["profileId"], "coating.painted-metal")
        self.assertTrue(Path(region["crop"]["path"]).exists())
        self.assertTrue(region["referencePbr"]["maps"]["roughness"]["path"])

        spec = {
            "materials": [{"id": "armor-paint", "baseColor": "#20344A", "textureProjection": {"mode": "uv", "repeat": [1, 1]}}],
            "componentTree": [{"id": "torso-armor", "material": "armor-paint", "uvContract": {"status": "unwrapped"}}],
            "lodPlan": [{"id": "lod0", "materialIds": ["armor-paint"]}],
            "rig": {"bones": [{"id": "torso", "component": "torso-armor"}]},
            "collisionGroups": {"torso-armor": "box"},
        }
        spec = apply_material_analysis(spec, analysis)
        self.assertEqual(spec["materialPipeline"]["status"], "proceed")
        self.assertEqual(spec["materials"][0]["referenceMaterialId"], "coating.painted-metal")
        compatibility = check_compatibility(spec)
        self.assertTrue(compatibility["passed"], compatibility)

        crop = Path(region["crop"]["path"])
        render = self.tmp / "render.png"
        render.write_bytes(crop.read_bytes())
        comparison = compare_material_crops(crop, render, material_id="armor-paint", expected=region["assignment"])
        self.assertTrue(comparison["passed"], comparison)
        plan = build_view_plan("torso-armor", "paint", validation_views=["neutral-studio", "grazing", "environment-reflection"])
        self.assertGreaterEqual(len(plan["views"]), 12)
        crop_meta = crop_visible_footprint(crop, {"x": 0, "y": 0, "width": 384, "height": 384}, self.tmp / "microscope.png")
        self.assertTrue(validate_capture(Path(crop_meta["path"]))["passed"])

        feedback = apply_material_feedback(spec, "armor-paint", comparison)
        self.assertEqual(feedback["decision"]["action"], "continue")
        gate = run_material_gate(spec, analysis=analysis, comparisons=[comparison], view_plan=plan)
        self.assertTrue(gate["passed"], gate)

    def test_low_confidence_analysis_cannot_enter_without_override(self) -> None:
        analysis = {
            "status": "probe",
            "registry": str(ROOT / ".." / "docs/materials/material-reference.json"),
            "regions": [{"regionId": "paint", "status": "probe"}],
        }
        spec = {"materials": [], "componentTree": []}
        with self.assertRaises(ValueError):
            apply_material_analysis(spec, analysis)

    def test_unobserved_material_keeps_pipeline_probe_even_when_visible_regions_pass(self) -> None:
        manifest = self.tmp / "regions-with-unobserved.json"
        manifest.write_text(json.dumps({
            "referenceId": "armor-fixture-with-hidden-material",
            "notObservedMaterials": [{"materialId": "hair.human", "status": "request-input"}],
            "regions": [{
                "componentId": "torso-armor",
                "regionId": "paint",
                "sourceImage": str(self.reference),
                "bbox": {"x": 64, "y": 64, "width": 384, "height": 384},
                "materialId": "coating.painted-metal",
                "materialSpecId": "armor-paint",
                "family": "coating",
                "subtype": "paint-over-metal",
                "finish": "gloss-or-satin",
            }],
        }, indent=2), encoding="utf-8")
        analysis = analyze_manifest(manifest, self.tmp / "analysis-with-unobserved")
        self.assertEqual(analysis["status"], "probe")
        self.assertEqual(len(analysis["unresolvedNotObservedMaterials"]), 1)
        spec = {"materials": [{"id": "armor-paint", "baseColor": "#20344A"}], "componentTree": []}
        spec = apply_material_analysis(spec, analysis, allow_probe=True)
        self.assertEqual(spec["materialPipeline"]["status"], "probe")

    def test_sanitize_region_id_prevents_traversal(self) -> None:
        from material_region_analysis import sanitize_region_id
        self.assertEqual(sanitize_region_id("paint"), "paint")
        self.assertEqual(sanitize_region_id("../../../evil"), "evil")
        self.assertEqual(sanitize_region_id("..\\..\\evil"), "evil")
        self.assertEqual(sanitize_region_id("arm/forearm-part"), "arm-forearm-part")


if __name__ == "__main__":
    unittest.main()
