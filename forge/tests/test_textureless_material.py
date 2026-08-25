from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "forge" / "_shared"))
sys.path.insert(0, str(ROOT / "forge" / "stage2_spec"))

sys.path.insert(0, str(ROOT / "forge" / "stage3_build"))

from validate_sculpt_spec import (  # noqa: E402
    is_textureless,
    validate_look_dev_targets,
    validate_materials,
    validate_textureless,
)

DECLARED = {
    "declared": True,
    "evidence": [
        "view-front: no grain, print or pore is resolvable anywhere on the subject",
        "reference-measurements.json: colour regions are flat, with hard boundaries",
    ],
}


def base_material(**overrides):
    material = {
        "id": "vinyl-fur",
        "name": "Matte-to-satin vinyl",
        "type": "physical",
        "shaderModel": "MeshPhysicalMaterial",
        "baseColor": "#2e2a28",
        "roughness": {"base": 0.52, "variation": 0.04},
        "metalness": {"base": 0.0, "variation": 0.0},
    }
    material.update(overrides)
    return material


def quality_first_spec(materials):
    return {
        "sourceImage": "reference/cat.jpeg",
        "materials": materials,
        "lookDevTargets": {
            "qualityPriority": "reference-fidelity",
            "materialPass": {
                "minimumTextureResolution": 1024,
                "independentMapChannels": ["albedo", "roughness", "height", "normal",
                                           "ambient-occlusion"],
                "referencePbrExtraction": {"requiredWhenSourceImagePresent": True,
                                           "targetThreshold": 0.7},
            },
            "lightingPass": {"requiredTerms": ["key light"]},
        },
    }


class Declaration(unittest.TestCase):
    def test_a_declaration_with_evidence_is_accepted(self):
        errors: list[str] = []
        validate_textureless("m", base_material(textureless=DECLARED), errors)
        self.assertEqual(errors, [])
        self.assertTrue(is_textureless(base_material(textureless=DECLARED)))

    def test_a_declaration_without_evidence_is_rejected(self):
        errors: list[str] = []
        validate_textureless("m", base_material(textureless={"declared": True}), errors)
        self.assertTrue(any("evidence" in error for error in errors), errors)

    def test_an_empty_evidence_list_is_rejected(self):
        errors: list[str] = []
        validate_textureless("m", base_material(textureless={"declared": True, "evidence": ["  "]}),
                             errors)
        self.assertTrue(any("evidence" in error for error in errors), errors)

    def test_declared_false_is_rejected_rather_than_treated_as_absent(self):
        errors: list[str] = []
        validate_textureless("m", base_material(textureless={"declared": False}), errors)
        self.assertTrue(any("must be true" in error for error in errors), errors)

    def test_a_material_cannot_be_textureless_and_carry_a_texture_field(self):
        for field, value in (
            ("normal", {"strength": 0.4}),
            ("bump", {"amplitude": 0.2}),
            ("displacement", {"amplitude": 0.1}),
            ("surfaceFrequencyBands", [{"id": "macro", "frequency": 2.0, "amplitude": 0.4}]),
            ("textureProjection", {"mode": "uv"}),
            ("textureResolution", 2048),
            ("referencePbr", {"usable": True}),
        ):
            with self.subTest(field=field):
                errors: list[str] = []
                validate_textureless("m", base_material(textureless=DECLARED, **{field: value}),
                                     errors)
                self.assertTrue(
                    any("cannot both have no texture" in error for error in errors), errors
                )

    def test_absent_declaration_changes_nothing(self):
        errors: list[str] = []
        validate_textureless("m", base_material(), errors)
        self.assertEqual(errors, [])
        self.assertFalse(is_textureless(base_material()))


class QualityBar(unittest.TestCase):
    def test_an_undeclared_material_still_fails_the_texture_channel_bar(self):
        """Negative control: the exemption must not leak to materials that did not declare it."""
        errors: list[str] = []
        warnings: list[str] = []
        spec = quality_first_spec([base_material()])
        validate_look_dev_targets(spec, errors, warnings)
        self.assertTrue(
            any("textureResolution must be >=" in warning for warning in warnings), warnings
        )

    def test_a_declared_material_is_exempt_from_the_texture_channel_bar(self):
        errors: list[str] = []
        warnings: list[str] = []
        spec = quality_first_spec([base_material(textureless=DECLARED)])
        validate_look_dev_targets(spec, errors, warnings)
        for phrase in ("textureResolution must be >=", "surface frequency bands",
                       "independent roughness map", "referencePbr extracted"):
            self.assertFalse(
                any(phrase in warning for warning in warnings),
                f"{phrase!r} should not fire on a declared-textureless material: {warnings}",
            )

    def test_the_exemption_is_per_material_not_global(self):
        errors: list[str] = []
        warnings: list[str] = []
        spec = quality_first_spec([
            base_material(textureless=DECLARED),
            base_material(id="painted-metal"),
        ])
        validate_look_dev_targets(spec, errors, warnings)
        offenders = [w for w in warnings if "textureResolution must be >=" in w]
        self.assertEqual(len(offenders), 1, warnings)
        self.assertIn("painted-metal", offenders[0])

    def test_the_declaration_is_hard_validated_through_validate_materials(self):
        errors: list[str] = []
        warnings: list[str] = []
        validate_materials(
            {"materials": [base_material(textureless={"declared": True})]}, errors, warnings
        )
        self.assertTrue(any("textureless.evidence" in error for error in errors), errors)


class EmittedFactory(unittest.TestCase):
    """A declared-textureless material must not be given a synthesised texture set.

    The emitted `createSculptMaterial` forces `color` to white and `roughness` to 1 whenever a
    texture set exists, and reads both from the generated maps instead. A subject measured to have
    no texture would therefore lose its authored albedo AND its reference-derived roughness, and
    gain mottling that is not in the reference.
    """

    def test_the_emitted_material_skips_texture_synthesis_when_declared(self):
        import generate_threejs_factory as generator

        source = generator._SCULPT_MATERIAL_HELPER_SOURCE if hasattr(
            generator, "_SCULPT_MATERIAL_HELPER_SOURCE"
        ) else generator.generate(_minimal_spec(), "blockout")
        self.assertIn("const textureless =", source)
        self.assertIn("? null", source)
        self.assertIn("makeProceduralTextureSet(id, spec, options)", source)

    def test_a_material_without_the_declaration_still_gets_its_texture_set(self):
        """Negative control: the synthesis path is scoped, not deleted."""
        import generate_threejs_factory as generator

        source = generator.generate(_minimal_spec(), "blockout")
        self.assertIn("makeReferenceTextureSet(spec, options) ?? makeProceduralTextureSet", source)


def _minimal_spec():
    return {
        "targetName": "Textureless",
        "targetId": "textureless",
        "schemaVersion": "2.1",
        "suitability": "pass",
        "coordinateFrame": {"front": "+Z", "up": "+Y", "scaleReference": "unit"},
        "silhouette": {"boundingShape": "test", "symmetry": "bilateral"},
        "proceduralStrategy": ["blockout"],
        "materials": [base_material(textureless=DECLARED)],
        "buildPasses": [{"id": "blockout", "acceptance": []}],
        "componentTree": [
            {
                "id": "body",
                "name": "Body",
                "level": "macro",
                "role": "body",
                "primitive": "ellipsoid",
                "topologyClass": "continuous-sculpt",
                "topologyRationale": "test",
                "parent": None,
                "material": "vinyl-fur",
                "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative"},
                "transform": {"position": [0, 0.5, 0], "rotation": [0, 0, 0]},
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
