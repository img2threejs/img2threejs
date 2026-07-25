from __future__ import annotations

import unittest
from pathlib import Path

from forge.stage1_intake.cs2_foundation import (
    classify_map_path,
    enrich_manifest_with_metadata,
    normalize_cs2_metadata,
    map_assets_to_reference_pbr,
    resolve_identity,
    validate_route_requirements,
)
from forge.stage1_intake.cs2_review_contract import (
    GOLDEN_THRESHOLDS,
    build_review_scene,
    validate_review_scene,
)
from forge.stage2_spec.cs2_adapters import get_family_adapter
from forge.stage2_spec.new_pre_spec_assessment import make_payload
from forge.stage2_spec.new_sculpt_spec import apply_cs2_manifest_evidence, apply_cs2_template, make_spec
from forge.stage2_spec.validate_sculpt_spec import validate_cs2_contract


class IdentityFoundationTests(unittest.TestCase):
    def test_explicit_metadata_wins_over_resolved_and_classification(self) -> None:
        result = resolve_identity(
            {"itemName": "Karambit | Fade", "source": "user"},
            {"itemName": "Karambit | Doppler", "source": "index"},
            {"itemFamily": "knife", "subtype": "karambit", "confidence": 0.9},
        )
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["identity"]["itemName"], "Karambit | Fade")
        self.assertEqual(result["provenance"], "explicit-user-metadata")

    def test_ambiguous_metadata_preserves_candidates(self) -> None:
        result = resolve_identity(
            None,
            {"status": "ambiguous", "candidates": [{"name": "A"}, {"name": "B"}]},
            {"itemFamily": "knife", "confidence": 0.9},
        )
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(len(result["candidates"]), 2)

    def test_missing_float_seed_and_hidden_view_are_explicit_approximations(self) -> None:
        normalized = normalize_cs2_metadata({"name": "Karambit | Fade"})
        self.assertTrue(normalized["approximations"]["float"]["approximated"])
        self.assertTrue(normalized["approximations"]["paintSeed"]["approximated"])
        self.assertEqual(normalized["approximations"]["hiddenRegions"]["confidence"], 0.25)

    def test_manifest_enrichment_keeps_image_only_tier(self) -> None:
        manifest = {"exactnessTier": "image-only", "provenance": {}, "warnings": []}
        enriched = enrich_manifest_with_metadata(manifest, {"status": "no-match"})
        self.assertEqual(enriched["exactnessTier"], "image-only")
        self.assertEqual(enriched["metadataResolution"]["status"], "no-match")
        self.assertIn("metadata-unavailable", enriched["warnings"])


class TextureFoundationTests(unittest.TestCase):
    def test_map_classifier_maps_independent_channels(self) -> None:
        self.assertEqual(classify_map_path(Path("knife_color.png"))["mapClass"], "albedo")
        self.assertEqual(classify_map_path(Path("knife_normal.png"))["mapClass"], "normal")
        packed = classify_map_path(Path("knife_orm.png"))
        self.assertEqual(packed["mapClass"], "packed")
        self.assertEqual(packed["packedChannels"], {"roughness": "r", "metalness": "g", "ao": "b"})

    def test_packed_and_direct_assets_map_to_independent_pbr_channels(self) -> None:
        result = map_assets_to_reference_pbr([
            {"mapClass": "albedo", "path": "a.png"},
            {"mapClass": "normal", "path": "n.png"},
            {"mapClass": "packed", "path": "orm.png", "packedChannels": {"roughness": "r", "metalness": "g", "ao": "b"}},
        ])
        self.assertTrue(result["complete"])
        self.assertTrue(result["maps"]["roughness"]["derived"])
        self.assertEqual(result["maps"]["metalness"]["packedChannel"], "g")

    def test_projection_requires_camera_and_delit_unless_fallback(self) -> None:
        missing = validate_route_requirements(
            {"route": "reference-projection", "exactnessTier": "image-only"}
        )
        self.assertEqual(missing["state"], "request-input")
        fallback = validate_route_requirements(
            {"route": "reference-projection", "exactnessTier": "image-only"},
            allow_fallback=True,
        )
        self.assertEqual(fallback["route"], "procedural-finish")
        self.assertEqual(fallback["exactnessTier"], "image-only")


class AdapterAndReviewTests(unittest.TestCase):
    def test_only_knife_adapter_is_registered(self) -> None:
        self.assertEqual(get_family_adapter("knife", "karambit").family, "knife")
        with self.assertRaises(ValueError):
            get_family_adapter("rifle", "ak47")

    def test_review_scene_is_versioned_and_thresholds_are_frozen(self) -> None:
        scene = build_review_scene("fixture-sha256")
        self.assertEqual(scene["version"], "cs2-knife-review-v1")
        self.assertEqual(scene["environmentHash"], "fixture-sha256")
        self.assertEqual(GOLDEN_THRESHOLDS["silhouetteIoU"], 0.85)
        self.assertEqual(validate_review_scene(scene), [])

    def test_manifest_evidence_reaches_assessment_and_spec(self) -> None:
        manifest = {
            "schemaVersion": 1,
            "state": "proceed",
            "itemFamily": "knife",
            "subtype": "karambit",
            "route": "procedural-finish",
            "exactnessTier": "metadata-assisted",
            "confidence": {"overall": 0.9},
            "provenance": {"reference": "fixture"},
            "warnings": ["paintSeed-approximated"],
        }
        assessment = make_payload("Karambit | Fade", None, "ultra-complex", True, manifest)
        self.assertEqual(assessment["preSpecAssessment"]["objectClass"]["itemFamily"], "knife")
        spec = make_spec("Karambit | Fade", None)
        apply_cs2_template(spec)
        apply_cs2_manifest_evidence(spec, manifest)
        self.assertEqual(spec["cs2Intake"]["exactnessTier"], "metadata-assisted")
        self.assertEqual(spec["materials"][0]["route"], "procedural-finish")

    def test_validator_rejects_invalid_exactness_route_pair(self) -> None:
        errors: list[str] = []
        validate_cs2_contract(
            {"cs2Intake": {"itemFamily": "knife", "route": "procedural-finish", "exactnessTier": "exact-texture"}},
            errors,
            [],
        )
        self.assertTrue(any("cannot claim exact-texture" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
