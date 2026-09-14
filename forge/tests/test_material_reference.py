#!/usr/bin/env python3
"""Contract tests for the machine-readable Three.js material reference library."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "docs" / "materials" / "material-reference.json"


class MaterialReferenceContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(REFERENCE.read_text(encoding="utf-8"))
        cls.materials = cls.payload["materials"]
        cls.by_id = {item["id"]: item for item in cls.materials}
        cls.source_records = cls.payload["sources"]
        cls.sources = {item["id"] for item in cls.source_records}

    def test_library_is_versioned_and_targets_threejs(self) -> None:
        self.assertEqual(self.payload["schemaVersion"], 1)
        self.assertEqual(self.payload["renderer"]["engine"], "three.js")
        self.assertEqual(self.payload["renderer"]["workflow"], "metallic-roughness")

    def test_material_ids_are_unique(self) -> None:
        self.assertEqual(len(self.by_id), len(self.materials))

    def test_core_material_families_are_covered(self) -> None:
        families = {item["family"] for item in self.materials}
        required = {
            "skin", "hair", "fur", "fabric", "leather", "metal", "coating",
            "plastic", "rubber", "wood", "stone", "ceramic", "glass", "gemstone",
        }
        self.assertFalse(required - families, f"missing material families: {sorted(required - families)}")

    def test_every_recipe_is_explicitly_a_prior_with_image_cues(self) -> None:
        for item in self.materials:
            with self.subTest(material=item["id"]):
                self.assertEqual(item["recipeBasis"], "inferred-starting-prior")
                self.assertTrue(item["aliases"])
                self.assertTrue(item["visualCues"])
                self.assertTrue(item["confusableWith"])
                self.assertTrue(item["validationViews"])
                self.assertTrue(item["sourceRefs"])

    def test_priors_stay_inside_documented_threejs_ranges(self) -> None:
        legal = self.payload["legalRanges"]
        for item in self.materials:
            for property_name, prior in item["renderPrior"].items():
                with self.subTest(material=item["id"], property=property_name):
                    self.assertIn(property_name, legal)
                    minimum, maximum = prior["range"]
                    default = prior["default"]
                    legal_min, legal_max = legal[property_name]
                    self.assertLessEqual(minimum, default)
                    self.assertLessEqual(default, maximum)
                    self.assertGreaterEqual(minimum, legal_min)
                    self.assertLessEqual(maximum, legal_max)

    def test_source_references_resolve(self) -> None:
        for item in self.materials:
            with self.subTest(material=item["id"]):
                missing = set(item["sourceRefs"]) - self.sources
                self.assertFalse(missing, f"unresolved source refs: {sorted(missing)}")

    def test_source_registry_is_traceable_and_unique(self) -> None:
        self.assertEqual(len(self.sources), len(self.source_records))
        for source in self.source_records:
            with self.subTest(source=source["id"]):
                self.assertTrue(source["url"].startswith("https://"))
                self.assertTrue(source["notebookSourceId"])
                self.assertTrue(source["authority"])

    def test_deep_research_provenance_is_recorded(self) -> None:
        research = self.payload["research"]
        runs = research["deepResearchRuns"]
        self.assertEqual({run["taskId"] for run in runs}, set(research["researchTaskIds"]))
        for run in runs:
            with self.subTest(task=run["taskId"]):
                self.assertGreaterEqual(run["sourcesFound"], run["citedSourcesImported"])
                self.assertGreater(run["citedSourcesImported"], 0)
        self.assertEqual(research["sourceHierarchy"][0], "Three.js official documentation and source contracts")

    def test_specialist_materials_carry_specialist_sources(self) -> None:
        expectations = {
            "skin.human": {"nvidia.faceworks", "activision.separable-sss"},
            "hair.human": {"pbrt.hair"},
            "fabric.woven-matte": {"khronos.sheen"},
            "glass.clear": {"khronos.transmission", "khronos.volume"},
        }
        for material_id, required_sources in expectations.items():
            with self.subTest(material=material_id):
                self.assertTrue(required_sources.issubset(self.by_id[material_id]["sourceRefs"]))

    def test_texture_contract_separates_colour_and_data_maps(self) -> None:
        contract = self.payload["textureContract"]
        colour = set(contract["colorMaps"])
        data = set(contract["dataMaps"])
        self.assertFalse(colour & data)
        self.assertEqual(contract["colorMapColorSpace"], "SRGBColorSpace")
        self.assertEqual(contract["dataMapColorSpace"], "NoColorSpace")
        self.assertIn("roughnessMap", data)
        self.assertIn("metalnessMap", data)
        self.assertIn("map", colour)

    def test_critical_model_invariants(self) -> None:
        for material_id in ("metal.steel-polished", "metal.steel-brushed", "metal.copper", "metal.gold"):
            self.assertEqual(self.by_id[material_id]["renderPrior"]["metalness"]["default"], 1.0)
        for material_id in ("skin.human", "fabric.woven-matte", "plastic.glossy", "glass.clear", "coating.metallic-paint"):
            self.assertEqual(self.by_id[material_id]["renderPrior"]["metalness"]["default"], 0.0)
        self.assertEqual(self.by_id["coating.painted-metal"]["renderPrior"]["metalness"]["default"], 0.0)
        self.assertEqual(self.by_id["glass.clear"]["renderPrior"]["transmission"]["default"], 1.0)

    def test_metallic_paint_recipe_resolves_dangling_metal_confusions(self) -> None:
        # Five metal records (and the lookup contract in docs/materials/README.md)
        # already named coating.metallic-paint before the recipe existed.
        self.assertIn("coating.metallic-paint", self.by_id)
        referrers = sorted(
            item["id"] for item in self.materials if "coating.metallic-paint" in item["confusableWith"]
        )
        self.assertTrue(referrers, "expected existing confusableWith refs to coating.metallic-paint")
        recipe = self.by_id["coating.metallic-paint"]
        self.assertEqual(recipe["renderPrior"]["clearcoat"]["default"], 1.0)
        self.assertIn("normalMap", recipe["requiredMaps"])


if __name__ == "__main__":
    unittest.main()
