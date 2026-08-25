#!/usr/bin/env python3
"""Tests for the `standProud` component property.

The property exists because the humanoid demo holds the same requirement twice and gets two
different outcomes: the garment holds it as a measurement (`sectionedLoft`'s `hug`) and it works,
the hair holds it as a comment and it broke. Widening the hair side masses took closure from 42.2%
to 40.9% with dark coverage DOWN, and crown scalp exposure measured on the archived captures rose on
all four views, worst by 14.9 points. Nothing in the schema objected.

Run: python3 forge/tests/test_stand_proud.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage2_spec"))

from validate_sculpt_spec import (  # noqa: E402
    STAND_PROUD_EXPECTED_ROLES,
    validate_stand_proud,
)


def run(component: dict, component_id: str = "hair-crown"):
    errors: list[str] = []
    warnings: list[str] = []
    refs: list[tuple[str, str]] = []
    validate_stand_proud(component_id, component, errors, warnings, refs)
    return errors, warnings, refs


def proud(**overrides) -> dict:
    base = {"againstComponentId": "head", "clearance": 0.012, "maxPush": 0.04}
    base.update(overrides)
    return {"role": "hair", "standProud": base}


class Accepted(unittest.TestCase):
    def test_a_well_formed_declaration_passes_and_records_its_reference(self) -> None:
        errors, warnings, refs = run(proud())
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(refs, [("hair-crown", "head")])

    def test_a_non_hair_component_may_also_declare_it(self) -> None:
        """Garments need it too; the hair role only decides where the MISSING warning fires."""
        errors, _, refs = run({"role": "garment", "standProud":
                               {"againstComponentId": "torso", "clearance": 0.04, "maxPush": 0.06}},
                              component_id="skirt")
        self.assertEqual(errors, [])
        self.assertEqual(refs, [("skirt", "torso")])

    def test_maxPush_equal_to_clearance_is_allowed(self) -> None:
        errors, _, _ = run(proud(clearance=0.03, maxPush=0.03))
        self.assertEqual(errors, [])


class MissingIsWarnedNotIgnored(unittest.TestCase):
    def test_a_hair_component_without_it_warns(self) -> None:
        errors, warnings, _ = run({"role": "hair"})
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("standProud", warnings[0])
        self.assertIn("bald", warnings[0])

    def test_a_non_hair_component_without_it_is_silent(self) -> None:
        errors, warnings, _ = run({"role": "detail"}, component_id="nose")
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_the_expected_role_set_is_exported_so_the_rule_is_inspectable(self) -> None:
        self.assertIn("hair", STAND_PROUD_EXPECTED_ROLES)


class HardErrors(unittest.TestCase):
    def test_a_non_object_declaration_is_an_error(self) -> None:
        for bad in ("yes", 1, [], True):
            with self.subTest(bad=bad):
                errors, _, _ = run({"role": "hair", "standProud": bad})
                self.assertEqual(len(errors), 1)
                self.assertIn("must be an object", errors[0])

    def test_a_missing_target_is_an_error(self) -> None:
        for bad in ({}, {"againstComponentId": ""}, {"againstComponentId": 7}):
            with self.subTest(bad=bad):
                declaration = dict(bad)
                declaration.setdefault("clearance", 0.01)
                declaration.setdefault("maxPush", 0.02)
                errors, _, _ = run({"role": "hair", "standProud": declaration})
                self.assertTrue(any("againstComponentId is required" in e for e in errors))

    def test_standing_proud_of_itself_is_an_error(self) -> None:
        errors, _, refs = run(proud(againstComponentId="hair-crown"))
        self.assertTrue(any("references itself" in e for e in errors))
        self.assertEqual(refs, [])

    def test_a_non_positive_clearance_is_an_error(self) -> None:
        """Zero clearance permits the surfaces to touch, which z-fights rather than covers."""
        for bad in (0.0, -0.01, None, "thin"):
            with self.subTest(bad=bad):
                errors, _, _ = run(proud(clearance=bad))
                self.assertTrue(any("clearance must be a positive number" in e for e in errors))

    def test_a_non_positive_maxPush_is_an_error(self) -> None:
        """An uncapped march walks inner vertices through the target and out the far side."""
        for bad in (0.0, -1.0, None):
            with self.subTest(bad=bad):
                errors, _, _ = run(proud(maxPush=bad))
                self.assertTrue(any("maxPush must be a positive number" in e for e in errors))

    def test_maxPush_below_clearance_is_an_error(self) -> None:
        errors, _, _ = run(proud(clearance=0.05, maxPush=0.01))
        self.assertTrue(any("below its clearance" in e for e in errors))

    def test_a_bad_clearance_short_circuits_before_the_maxPush_comparison(self) -> None:
        """One clear error beats two derived ones."""
        errors, _, _ = run(proud(clearance=-1.0, maxPush=-2.0))
        self.assertEqual(len(errors), 1)


class ReferenceResolution(unittest.TestCase):
    def test_an_unknown_target_is_caught_by_the_spec_level_pass(self) -> None:
        """Resolution is deferred so a forward reference is legal, exactly like `parent`."""
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage2_spec"))
        from validate_sculpt_spec import validate_components  # noqa: PLC0415

        errors: list[str] = []
        warnings: list[str] = []
        validate_components(
            {
                "schemaVersion": "2.0",
                "materials": [{"id": "hair"}],
                "componentTree": [
                    {"id": "head", "primitive": "ellipsoid", "material": "hair"},
                    {"id": "hair-crown", "primitive": "ellipsoid", "material": "hair",
                     "role": "hair", "parent": "head",
                     "standProud": {"againstComponentId": "skull-that-does-not-exist",
                                    "clearance": 0.01, "maxPush": 0.02}},
                ],
            },
            {"hair"},
            set(),
            errors,
            warnings,
        )
        self.assertTrue(
            any("standProud references missing component" in e for e in errors),
            f"expected a missing-target error, got {errors}",
        )

    def test_a_forward_reference_is_legal(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage2_spec"))
        from validate_sculpt_spec import validate_components  # noqa: PLC0415

        errors: list[str] = []
        warnings: list[str] = []
        validate_components(
            {
                "schemaVersion": "2.0",
                "materials": [{"id": "hair"}],
                "componentTree": [
                    {"id": "hair-crown", "primitive": "ellipsoid", "material": "hair",
                     "role": "hair",
                     "standProud": {"againstComponentId": "head", "clearance": 0.01, "maxPush": 0.02}},
                    {"id": "head", "primitive": "ellipsoid", "material": "hair"},
                ],
            },
            {"hair"},
            set(),
            errors,
            warnings,
        )
        self.assertFalse([e for e in errors if "standProud" in e], errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
