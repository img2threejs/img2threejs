#!/usr/bin/env python3
"""Tests for the hairProfile schema.

The profile exists so the model reverse-engineers PARAMETERS and a compiler owns topology, which is
how every other subsystem in this pipeline already works. Hair was the exception, and it produced
four wrong diagnoses of one hairstyle in a single session, each caught only by measurement after the
fact.

Two rules here carry measurements rather than opinions:

  the root rule       a root held as an absolute position slides off the skull when the mass is
                      resized. Measured: widening the hair side masses took closure from 42.2% to
                      40.9%, worse on all six views, with crown scalp exposure UP 14.9 points on the
                      worst view.
  the tier default    the calibration reference is a 570,400-vertex merged scan whose hair surface
                      roughness is 0.00338 against a torso control of 0.00312. Its hair is a smooth
                      shell with the strand detail in textures, so shell is the default and lock
                      parameters have no ground truth at all.

Run: python3 forge/tests/test_hair_profile.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage2_spec"))

from hair_profile import (  # noqa: E402
    DEFAULT_REPRESENTATION_TIER,
    REJECTED_HAIR_PRIMITIVES,
    UNCALIBRATED_FIELDS,
    VALID_REPRESENTATION_TIERS,
    hair_profile_report,
    validate_hair_profile,
)


def check(profile):
    errors: list[str] = []
    warnings: list[str] = []
    validate_hair_profile(profile, errors, warnings)
    return errors, warnings


def mass(**overrides) -> dict:
    base = {
        "id": "crown", "region": "crown", "primitive": "tapered-sweep",
        "root": {"u": 0.5, "v": 0.8}, "length": 0.12, "width": 0.09, "thickness": 0.05,
    }
    base.update(overrides)
    return base


def profile(**overrides) -> dict:
    base = {
        "scalpComponentId": "head",
        "representationTier": "masses",
        "hairline": {"controlPoints": [
            {"u": 0.15, "v": 0.55}, {"u": 0.50, "v": 0.62}, {"u": 0.85, "v": 0.55},
        ]},
        "flowField": {"gravity": 0.6, "partLine": {"u": 0.62}, "whorls": [{"u": 0.5, "v": 0.95}]},
        "masses": [mass()],
    }
    base.update(overrides)
    return base


class Baseline(unittest.TestCase):
    def test_a_well_formed_profile_is_clean(self) -> None:
        errors, warnings = check(profile())
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_an_absent_profile_is_not_an_error(self) -> None:
        """Most subjects are not characters, and none of them should be asked about hair."""
        self.assertEqual(check(None), ([], []))

    def test_a_non_object_profile_is_an_error(self) -> None:
        for bad in ("hair", 7, []):
            with self.subTest(bad=bad):
                errors, _ = check(bad)
                self.assertEqual(errors, ["hairProfile must be an object"])


class TheRootRule(unittest.TestCase):
    def test_an_absolute_root_is_rejected_with_the_reason(self) -> None:
        errors, _ = check(profile(masses=[mass(root={"position": [0.1, 5.9, 0.0]})]))
        self.assertTrue(any("must be scalp (u, v)" in e for e in errors), errors)
        self.assertTrue(any("bald patch" in e for e in errors), errors)

    def test_an_xyz_root_is_rejected_too(self) -> None:
        errors, _ = check(profile(masses=[mass(root={"xyz": [0, 0, 0]})]))
        self.assertTrue(any("must be scalp (u, v)" in e for e in errors), errors)

    def test_a_missing_root_is_rejected(self) -> None:
        bare = mass()
        del bare["root"]
        errors, _ = check(profile(masses=[bare]))
        self.assertTrue(any(".root is required" in e for e in errors), errors)

    def test_uv_outside_the_unit_square_is_rejected(self) -> None:
        for bad in ({"u": 1.4, "v": 0.5}, {"u": 0.5, "v": -0.1}, {"u": "left", "v": 0.5}):
            with self.subTest(bad=bad):
                errors, _ = check(profile(masses=[mass(root=bad)]))
                self.assertTrue(any("u and v in [0,1]" in e for e in errors), errors)


class RepresentationTier(unittest.TestCase):
    def test_the_default_is_shell(self) -> None:
        self.assertEqual(DEFAULT_REPRESENTATION_TIER, "shell")
        report = hair_profile_report({"scalpComponentId": "head"})
        self.assertEqual(report["representationTier"], "shell")

    def test_shell_needs_no_masses(self) -> None:
        errors, _ = check({"scalpComponentId": "head", "representationTier": "shell",
                           "hairline": profile()["hairline"], "flowField": profile()["flowField"]})
        self.assertEqual(errors, [])

    def test_a_mass_tier_without_masses_is_an_error(self) -> None:
        payload = profile()
        del payload["masses"]
        errors, _ = check(payload)
        self.assertTrue(any("masses is required" in e for e in errors), errors)

    def test_an_unknown_tier_is_rejected(self) -> None:
        errors, _ = check(profile(representationTier="strands"))
        self.assertTrue(any("representationTier must be one of" in e for e in errors), errors)

    def test_the_lock_tier_declares_that_it_has_no_calibration(self) -> None:
        """The reference scan holds no lock geometry to measure, and the profile must say so."""
        _, warnings = check(profile(representationTier="locks"))
        joined = " ".join(warnings)
        self.assertIn("no calibration reference", joined)
        self.assertIn("0.00338", joined)

    def test_all_three_tiers_are_reachable(self) -> None:
        for tier in VALID_REPRESENTATION_TIERS:
            with self.subTest(tier=tier):
                errors, _ = check(profile(representationTier=tier))
                self.assertFalse([e for e in errors if "representationTier" in e], errors)


class PrimitiveChoice(unittest.TestCase):
    def test_a_plane_card_is_rejected_because_there_is_no_texture(self) -> None:
        errors, _ = check(profile(masses=[mass(primitive="plane-card")]))
        self.assertTrue(any("alpha texture" in e for e in errors), errors)

    def test_a_tube_is_rejected_because_it_cannot_taper(self) -> None:
        errors, _ = check(profile(masses=[mass(primitive="tube")]))
        self.assertTrue(any("noodle" in e for e in errors), errors)

    def test_an_unusable_primitive_names_the_alternatives(self) -> None:
        errors, _ = check(profile(masses=[mass(primitive="torus")]))
        self.assertTrue(any("choose from" in e for e in errors), errors)

    def test_the_rejection_table_carries_a_reason_for_every_entry(self) -> None:
        for primitive, reason in REJECTED_HAIR_PRIMITIVES.items():
            with self.subTest(primitive=primitive):
                self.assertTrue(reason and len(reason) > 20, f"{primitive} has no usable reason")


class Calibration(unittest.TestCase):
    def test_a_taper_without_an_uncalibrated_flag_warns(self) -> None:
        _, warnings = check(profile(masses=[mass(taper=0.12)]))
        self.assertTrue(any("not marked uncalibrated" in w for w in warnings), warnings)

    def test_a_flagged_taper_is_silent(self) -> None:
        _, warnings = check(profile(masses=[mass(taper=0.12, uncalibrated=True)]))
        self.assertEqual(warnings, [])

    def test_the_report_lists_what_is_uncalibrated(self) -> None:
        report = hair_profile_report(profile())
        self.assertEqual(report["uncalibratedFields"], list(UNCALIBRATED_FIELDS))
        self.assertIn("multipart GLB", report["calibrationNote"])

    def test_no_uncalibrated_field_is_silently_absent_from_the_list(self) -> None:
        """Guards the honesty rule: taper is derived, so it must appear."""
        self.assertIn("masses[].taper", UNCALIBRATED_FIELDS)


class HairlineAndFlow(unittest.TestCase):
    def test_a_missing_hairline_warns(self) -> None:
        payload = profile()
        del payload["hairline"]
        _, warnings = check(payload)
        self.assertTrue(any("hairline is missing" in w for w in warnings), warnings)

    def test_too_few_control_points_is_an_error(self) -> None:
        errors, _ = check(profile(hairline={"controlPoints": [{"u": 0.5, "v": 0.6}]}))
        self.assertTrue(any("at least 3 points" in e for e in errors), errors)

    def test_a_missing_flow_field_warns_with_the_recorded_reason(self) -> None:
        payload = profile()
        del payload["flowField"]
        _, warnings = check(payload)
        self.assertTrue(any("fourteen competing directions" in w for w in warnings), warnings)

    def test_gravity_outside_the_unit_interval_is_an_error(self) -> None:
        errors, _ = check(profile(flowField={"gravity": 1.5}))
        self.assertTrue(any("gravity must be a number in [0,1]" in e for e in errors), errors)

    def test_a_whorl_needs_a_position_on_the_scalp(self) -> None:
        errors, _ = check(profile(flowField={"whorls": [{"strength": 0.5}]}))
        self.assertTrue(any("needs u and v" in e for e in errors), errors)


class Structure(unittest.TestCase):
    def test_the_scalp_component_is_required(self) -> None:
        payload = profile()
        del payload["scalpComponentId"]
        errors, _ = check(payload)
        self.assertTrue(any("scalpComponentId is required" in e for e in errors), errors)

    def test_duplicate_mass_ids_are_rejected(self) -> None:
        errors, _ = check(profile(masses=[mass(), mass()]))
        self.assertTrue(any("duplicate id" in e for e in errors), errors)

    def test_an_unknown_region_is_rejected(self) -> None:
        errors, _ = check(profile(masses=[mass(region="moustache")]))
        self.assertTrue(any("is not one of" in e for e in errors), errors)

    def test_non_positive_dimensions_are_rejected(self) -> None:
        for field in ("length", "width", "thickness"):
            with self.subTest(field=field):
                errors, _ = check(profile(masses=[mass(**{field: 0.0})]))
                self.assertTrue(any(f".{field} must be a positive number" in e for e in errors))


class ComponentLevelRejection(unittest.TestCase):
    """The same rule applied where components are declared, not only inside the profile."""

    def test_a_hair_role_component_may_not_be_a_plane_card(self) -> None:
        from validate_sculpt_spec import validate_components  # noqa: PLC0415

        errors: list[str] = []
        warnings: list[str] = []
        validate_components(
            {
                "schemaVersion": "2.0",
                "materials": [{"id": "hair"}],
                "componentTree": [
                    {"id": "head", "primitive": "ellipsoid", "material": "hair"},
                    {"id": "hair-card", "primitive": "plane-card", "material": "hair",
                     "role": "hair", "parent": "head",
                     "standProud": {"againstComponentId": "head", "clearance": 0.01, "maxPush": 0.02}},
                ],
            },
            {"hair"},
            set(),
            errors,
            warnings,
        )
        self.assertTrue(any("may not use primitive 'plane-card'" in e for e in errors), errors)

    def test_a_non_hair_component_may_still_be_a_plane_card(self) -> None:
        from validate_sculpt_spec import validate_components  # noqa: PLC0415

        errors: list[str] = []
        warnings: list[str] = []
        validate_components(
            {
                "schemaVersion": "2.0",
                "materials": [{"id": "glass"}],
                "componentTree": [
                    {"id": "lens", "primitive": "plane-card", "material": "glass", "role": "panel"},
                ],
            },
            {"glass"},
            set(),
            errors,
            warnings,
        )
        self.assertFalse([e for e in errors if "plane-card" in e], errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
