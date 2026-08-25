#!/usr/bin/env python3
"""Left and right: the two ways it went wrong, and the two different tests that catch them.

Both defects below shipped in the same figure and no gate saw either, because both produce geometry
that is internally tidy and only wrong against a convention that lived in a comment. They are
reproduced here from their measured values, so the gates are validated against real failures rather
than against invented ones.

    THE HAND    Built as `[side*along, height, side*across]` -- x AND z negated. Two negations is a
                180-degree ROTATION about Y, not a reflection, and a rotation preserves handedness,
                so the left hand was the right hand turned around. Caught by `check_pair`.
                After the fix the hand region moved 46.0% closer to the reference in the front view.

    THE FOOT    A correct reflection, so `check_pair` PASSES it -- and it was still wrong. The toes
                were ordered little-to-big across a strip whose index 0 is medial, so the big toe
                went lateral on BOTH feet. Caught only by `medial_lateral_bias` against a reference.
                Measured toe-band mass: reference 529 medial / 488 lateral, ours 350 / 443.

Run: python3 forge/tests/test_chirality.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage2_spec"))

from chirality import (  # noqa: E402
    CHARACTER_LEFT_SIGN,
    check_pair,
    classify_relation,
    compare_bias,
    find_pairs,
    medial_lateral_bias,
    mirror_point,
    mirror_vector,
    pair_stem,
    sagittal_symmetry_error,
    side_of,
)
import validate_sculpt_spec  # noqa: E402


class TheConvention(unittest.TestCase):
    def test_the_characters_left_is_plus_x(self) -> None:
        """Derived, not chosen: with forward +Z and a right-handed frame, the camera that sees the
        front looks along -Z and its right is +X, so a figure facing it has its own left there."""
        self.assertEqual(CHARACTER_LEFT_SIGN, 1)

    def test_a_mirror_negates_the_lateral_axis_and_nothing_else(self) -> None:
        self.assertEqual(mirror_point((2.8, 4.643, 0.288)), (-2.8, 4.643, 0.288))

    def test_a_direction_mirrors_by_the_same_rule(self) -> None:
        """The reflex is to think a direction transforms differently. Reaching for a rotation here
        is exactly the recorded bug."""
        self.assertEqual(mirror_vector((0.72, 0.0, -0.69)), (-0.72, 0.0, -0.69))

    def test_a_malformed_point_is_rejected(self) -> None:
        for bad in ((1.0, 2.0), (1.0, 2.0, 3.0, 4.0), ()):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    mirror_point(bad)


class NamingPairs(unittest.TestCase):
    def test_sides_are_read_off_the_suffix(self) -> None:
        self.assertEqual(side_of("hand-l"), "l")
        self.assertEqual(side_of("foot-r"), "r")
        self.assertIsNone(side_of("torso"))

    def test_a_stem_survives_internal_hyphens(self) -> None:
        self.assertEqual(pair_stem("hair-temple-l"), "hair-temple")

    def test_only_complete_pairs_are_returned(self) -> None:
        pairs = find_pairs(["hand-l", "hand-r", "foot-l", "torso", "tail-r"])
        self.assertEqual(pairs, [("hand-r", "hand-l")])

    def test_the_right_half_comes_first(self) -> None:
        """The convention is stated as 'the left is the mirror of the right'."""
        self.assertEqual(find_pairs(["eye-l", "eye-r"]), [("eye-r", "eye-l")])


class TheHandDefect(unittest.TestCase):
    """Reproduced from the measured thumb positions."""

    RIGHT_THUMB_TIP = (2.800, 4.643, 0.288)
    LEFT_AS_BUILT = (-2.800, 4.643, -0.288)      # side applied to x AND z
    LEFT_CORRECT = (-2.800, 4.643, 0.288)        # sagittal mirror

    def test_the_defect_is_named_a_rotation_not_just_a_mismatch(self) -> None:
        """The two are trivially confused, agree exactly on any symmetric part, and differ only in
        handedness. Saying 'mismatch' would leave the reader to rediscover that."""
        self.assertEqual(classify_relation(self.RIGHT_THUMB_TIP, self.LEFT_AS_BUILT), "rotation")

    def test_the_defect_fails_the_pair_check(self) -> None:
        ok, message = check_pair("thumb", self.RIGHT_THUMB_TIP, self.LEFT_AS_BUILT)
        self.assertFalse(ok)
        self.assertIn("ROTATED", message)
        self.assertIn("preserves handedness", message)
        self.assertIn("same hand", message)

    def test_the_fixed_pair_passes(self) -> None:
        ok, message = check_pair("thumb", self.RIGHT_THUMB_TIP, self.LEFT_CORRECT)
        self.assertTrue(ok, message)

    def test_a_part_on_the_midline_hides_the_defect(self) -> None:
        """Why nothing caught it for so long: at z = 0 a rotation and a reflection are identical,
        so every symmetric component agrees under both and only a chiral one disagrees."""
        on_midline = (2.8, 4.6, 0.0)
        self.assertEqual(classify_relation(on_midline, (-2.8, 4.6, 0.0)), "reflection")

    def test_both_halves_on_the_same_side_is_reported_as_such(self) -> None:
        ok, message = check_pair("hand", (2.8, 4.6, 0.1), (2.8, 4.6, 0.1))
        self.assertFalse(ok)
        self.assertIn("same side", message)


class TheFootDefect(unittest.TestCase):
    """The one a pair check cannot catch, with the numbers measured off the renders."""

    REFERENCE = [(0.0, 529.0), (1.0, 488.0)]     # medial-heavy, as a real foot is
    BEFORE = [(0.0, 350.0), (1.0, 443.0)]        # lateral-heavy: big toe on the wrong edge
    AFTER = [(0.0, 426.0), (1.0, 358.0)]

    def build(self, samples, midline=-1.0):
        """A left foot: its own centre sits left of the body midline, so 'medial' is +lateral."""
        return medial_lateral_bias([(c, w) for c, w in samples], midline=midline)

    def test_a_perfect_mirror_pair_can_still_both_be_wrong(self) -> None:
        """The whole reason this test exists beside `check_pair`. Both feet were built from one
        authored shape and mirrored correctly, so the pair check is happy."""
        ok, _ = check_pair("foot", (0.37, 0.0, 0.0), (-0.37, 0.0, 0.0))
        self.assertTrue(ok)

    def test_the_reference_is_medial_heavy(self) -> None:
        self.assertEqual(self.build(self.REFERENCE)["heavier"], "medial")

    def test_the_defect_is_lateral_heavy_and_is_caught(self) -> None:
        reference = self.build(self.REFERENCE)
        before = self.build(self.BEFORE)
        self.assertEqual(before["heavier"], "lateral")
        ok, message = compare_bias(reference, before, "foot-l toe band")
        self.assertFalse(ok)
        self.assertIn("the OTHER limb", message)
        self.assertIn("order of anything laid out across it", message)

    def test_the_fix_passes(self) -> None:
        ok, message = compare_bias(self.build(self.REFERENCE), self.build(self.AFTER), "foot-l")
        self.assertTrue(ok, message)

    def test_a_magnitude_difference_alone_is_not_a_chirality_failure(self) -> None:
        """Proportion is another gate's job. Only the SIGN is judged here."""
        reference = self.build([(0.0, 700.0), (1.0, 300.0)])
        candidate = self.build([(0.0, 520.0), (1.0, 480.0)])
        ok, _ = compare_bias(reference, candidate, "foot-l")
        self.assertTrue(ok)

    def test_the_floor_is_low_enough_to_see_the_real_reference(self) -> None:
        """The first floor was 0.05, picked by eye, and it made the gate blind to the very defect
        it exists for: the reference feet measure +0.0403 and +0.0579."""
        from chirality import MIN_REFERENCE_BIAS  # noqa: PLC0415
        self.assertLess(MIN_REFERENCE_BIAS, 0.0403)
        self.assertGreater(MIN_REFERENCE_BIAS, 0.0)

    def test_a_symmetric_reference_cannot_judge_handedness_and_says_so(self) -> None:
        reference = self.build([(0.0, 505.0), (1.0, 500.0)])
        candidate = self.build([(0.0, 300.0), (1.0, 700.0)])
        ok, message = compare_bias(reference, candidate, "foot-l")
        self.assertTrue(ok)
        self.assertIn("cannot be judged", message)

    def test_medial_is_derived_from_which_side_the_limb_is_on(self) -> None:
        """Not assumed: the same samples on the right foot must report the opposite side."""
        left = medial_lateral_bias([(-0.50, 100.0), (-0.25, 400.0)], midline=0.0)
        right = medial_lateral_bias([(0.25, 400.0), (0.50, 100.0)], midline=0.0)
        self.assertEqual(left["heavier"], right["heavier"])

    def test_no_samples_does_not_raise(self) -> None:
        self.assertEqual(medial_lateral_bias([])["sampleCount"], 0)


class SpecLevelGate(unittest.TestCase):
    def run_spec(self, tree):
        from validate_sculpt_spec import validate_chirality  # noqa: PLC0415

        errors: list[str] = []
        warnings: list[str] = []
        validate_chirality({"componentTree": tree}, errors, warnings)
        return errors, warnings

    def test_a_rotated_pair_is_a_hard_error(self) -> None:
        errors, _ = self.run_spec([
            {"id": "hand-r", "transform": {"position": [2.8, 4.6, 0.288]}},
            {"id": "hand-l", "transform": {"position": [-2.8, 4.6, -0.288]}},
        ])
        self.assertEqual(len(errors), 1)
        self.assertIn("ROTATED", errors[0])

    def test_a_mirrored_pair_on_the_right_convention_is_clean(self) -> None:
        """`-l` at +X, which is the character's own left. Writing this test the other way round
        first was instructive: the warning fired, correctly, and I had reached for the demo's
        inverted convention without noticing."""
        errors, warnings = self.run_spec([
            {"id": "hand-l", "transform": {"position": [2.8, 4.6, 0.288]}},
            {"id": "hand-r", "transform": {"position": [-2.8, 4.6, 0.288]}},
        ])
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_a_left_named_component_sitting_on_the_right_warns(self) -> None:
        """Fires even with no partner present, and this is the divergence the showcase demo has:
        it spells the character's left `-l` and places it at -X, which is the opposite of the
        generator's own documented convention."""
        _, warnings = self.run_spec([
            {"id": "foot-l", "transform": {"position": [-0.37, 0.0, 0.0]}},
            {"id": "foot-r", "transform": {"position": [0.37, 0.0, 0.0]}},
        ])
        self.assertEqual(len(warnings), 2)
        self.assertIn("would drive the wrong side", warnings[0])

    def test_the_generators_own_convention_passes_clean(self) -> None:
        _, warnings = self.run_spec([
            {"id": "ear-l", "transform": {"position": [0.43, 0.02, -0.02]}},
            {"id": "ear-r", "transform": {"position": [-0.43, 0.02, -0.02]}},
        ])
        self.assertEqual(warnings, [])

    def test_components_on_the_midline_are_not_judged(self) -> None:
        _, warnings = self.run_spec([{"id": "thing-l", "transform": {"position": [0.0, 1.0, 0.0]}}])
        self.assertEqual(warnings, [])

    def test_unpaired_and_untransformed_components_are_ignored(self) -> None:
        errors, warnings = self.run_spec([
            {"id": "torso"}, {"id": "head", "transform": {}},
            {"id": "arm-l", "transform": {"position": ["x", 1, 2]}},
        ])
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])


class WholeFigureSanity(unittest.TestCase):
    def test_a_symmetric_figure_reports_near_zero(self) -> None:
        points = [(0.3, 1.0, 0.0), (-0.3, 1.0, 0.0), (0.0, 2.0, 0.0)]
        self.assertLess(sagittal_symmetry_error(points), 1e-9)

    def test_a_drifted_pair_raises_the_number(self) -> None:
        points = [(0.3, 1.0, 0.0), (-0.3, 1.0, 0.4), (0.0, 2.0, 0.0)]
        self.assertGreater(sagittal_symmetry_error(points), 0.1)

    def test_no_points_does_not_raise(self) -> None:
        self.assertEqual(sagittal_symmetry_error([]), 0.0)



class NestedPairSideNaming(unittest.TestCase):
    """The side-naming warning reads WORLD x, so a nested pair member is judged by where it is.

    A toe inside a left paw legitimately carries a negative LOCAL x while sitting on the
    character's left. Reading the local value flagged every inboard toe of a correctly mirrored
    pair, and the only way to silence it would have been to stop mirroring them.
    """

    @staticmethod
    def _spec(toe_offset_left: float, toe_offset_right: float):
        def part(component_id, parent, x):
            return {
                "id": component_id,
                "name": component_id,
                "level": "meso",
                "role": "paw",
                "primitive": "ellipsoid",
                "topologyClass": "continuous-sculpt",
                "topologyRationale": "test",
                "parent": parent,
                "dimensions": {"width": 0.1, "height": 0.1, "depth": 0.1, "units": "relative"},
                "transform": {"position": [x, 0.0, 0.0], "rotation": [0, 0, 0]},
            }

        return {
            "componentTree": [
                part("paw-l", None, 0.12),
                part("paw-r", None, -0.12),
                part("toe-0-l", "paw-l", toe_offset_left),
                part("toe-0-r", "paw-r", toe_offset_right),
            ]
        }

    def test_a_correctly_mirrored_inboard_toe_is_not_flagged(self):
        errors, warnings = [], []
        validate_sculpt_spec.validate_chirality(self._spec(-0.04, 0.04), errors, warnings)
        self.assertEqual(errors, [])
        self.assertEqual([w for w in warnings if "named for the character" in w], [])

    def test_a_toe_that_really_crosses_the_midline_is_still_flagged(self):
        """Negative control: the world-space read must not simply stop catching the defect."""
        errors, warnings = [], []
        validate_sculpt_spec.validate_chirality(self._spec(-0.30, 0.30), errors, warnings)
        flagged = [w for w in warnings if "named for the character" in w]
        self.assertEqual(len(flagged), 2, warnings)
        self.assertIn("world x", flagged[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
