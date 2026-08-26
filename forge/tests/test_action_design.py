#!/usr/bin/env python3
"""Tests for Stage R4 action design, built around the round trip that is the whole point.

`test_walk_round_trip_classifier_independently_reports_walk` and its run counterpart are the
argument for this module. §R4 says a designed clip is accepted only when "the classifier from
§2 must agree the clip is what it claims to be", so the test derives gait parameters from
`walk_targets`, authors tracks from them, measures those tracks with §1, and asks §2 what it
just looked at — without ever telling it. Then it raises the speed per the spec's run recipe
and asks again, and the answer has to change on its own. Anything less is a test that the
authoring code returns the number the authoring code put in.

Falls back to a local §1/§2 implementation when `clip_features.py` is not importable, so this
suite is self-contained. `test_the_fallback_classifier_matches_the_spec_validation_table`
pins that fallback to the spec's own published measurements rather than to what it happens to
compute, because a fallback nothing checks is a second bug waiting to agree with the first.

Pure Python 3.10+ stdlib.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stage5_rig"))

from action_design import (  # noqa: E402
    FOOT_SLIDE_LIMIT,
    POSE_RETURN_DEGREES,
    ChainResolutionError,
    JointNode,
    PRIMITIVES,
    accepts,
    band_features,
    build_payload,
    check_medial_lateral,
    classifier_verdict,
    default_classifier,
    default_measure,
    foot_slide,
    gait_parameters,
    hip_relative_clip,
    resolve_chains,
    run_targets,
    synthesise_gait_tracks,
    walk_targets,
)

MODULE = ROOT / "stage5_rig" / "action_design.py"


# ---------------------------------------------------------------------------------------------
# §1 / §2 fallback — used only when clip_features.py is absent.
#
# This is a deliberate, minimal transcription of the spec's own tables, kept in the TEST so the
# suite is self-contained while `clip_features.py` (owned by another author) lands. It is NOT a
# second implementation for production use: action_design imports the real one and this file
# prefers it whenever it is importable.
# ---------------------------------------------------------------------------------------------


class _FallbackFeatures:
    """§1's feature vector, normalised by H exactly as `clip_features.measure_clip` does."""

    def __init__(self, clip, figure_height):
        height = float(figure_height)
        positions = clip["landmarkPositions"]
        hip = positions["hip"]
        start = hip[0]
        self.duration = float(clip["duration"])
        self.travel = max(math.hypot(p[0] - start[0], p[2] - start[2]) for p in hip) / height
        self.rise = (max(p[1] for p in hip) - min(p[1] for p in hip)) / height
        self.speed = self.travel / self.duration
        self.landmark_ranges = {
            name: tuple((max(p[axis] for p in track) - min(p[axis] for p in track)) / height for axis in range(3))
            for name, track in positions.items()
        }
        self.hand_range = self._pooled(positions, ("hand.l", "hand.r"), height)
        self.foot_range = self._pooled(positions, ("foot.l", "foot.r"), height)
        self.head_rise = self.landmark_ranges["head"][1]
        self.scale_delta = max(clip["jointScaleDelta"]) if clip["jointScaleDelta"] else 0.0
        self.pose_return = clip.get("poseReturn")

    @staticmethod
    def _pooled(positions, names, height):
        pooled = [p for name in names for p in positions[name]]
        return max((max(p[axis] for p in pooled) - min(p[axis] for p in pooled)) for axis in range(3)) / height


def _fallback_measure(clip, figure_height):
    return _FallbackFeatures(clip, figure_height)


def _fallback_classify(features):
    """§2's threshold table, verbatim. Returns every matching label plus a primary class."""
    labels = []
    if features.travel < 0.02 and features.rise < 0.02:
        labels.append("idle")
    in_place = features.travel < 0.30
    if in_place:
        labels.append("in-place")
    if 0.30 <= features.speed < 0.60:
        labels.append("walk")
    if 0.60 <= features.speed < 1.50:
        labels.append("run")
    if features.speed >= 1.50:
        labels.append("dash")
    if features.rise >= 0.15 and features.travel < 0.50:
        labels.append("jump")
    if features.rise >= 0.15 and features.travel >= 0.50:
        labels.append("leap")
    if features.foot_range < 0.10:
        labels.append("planted")
    if in_place and features.hand_range >= 0.40:
        labels.append("gesture")
    precedence = ("dash", "run", "walk", "leap", "jump", "idle", "in-place")
    primary = next((label for label in precedence if label in labels), None)
    return primary, tuple(labels)


MEASURE = default_measure() or _fallback_measure
CLASSIFY = default_classifier() or _fallback_classify
USING_REAL_CLIP_FEATURES = default_measure() is not None


def measure(clip, figure_height):
    return MEASURE(clip, figure_height)


def classify(features):
    return CLASSIFY(features)


# ---------------------------------------------------------------------------------------------
# Synthetic humanoid hierarchy with DELIBERATELY UNINFORMATIVE joint names.
#
# Every joint is `node_NN`, in an order that does not follow the body. If `resolve_chains` can
# still find the arms, the legs and the spine, it found them from the shape of the graph, which
# is what §R4 ("Resolve from topology") and Stage R0 ("Do not infer from names") require.
# ---------------------------------------------------------------------------------------------

HUMANOID = [
    JointNode("node_00", None, (0.0, 0.50, 0.0)),        # root
    JointNode("node_17", "node_00", (0.0, 0.00, 0.0)),   # pelvis: the first branch
    JointNode("node_04", "node_17", (0.0, 0.10, 0.0)),   # lower spine (medial)
    JointNode("node_29", "node_17", (0.09, -0.02, 0.0)),  # +X thigh
    JointNode("node_08", "node_17", (-0.09, -0.02, 0.0)),  # -X thigh
    JointNode("node_31", "node_29", (0.0, -0.22, 0.0)),
    JointNode("node_02", "node_31", (0.0, -0.22, 0.02)),  # +X foot leaf
    JointNode("node_11", "node_08", (0.0, -0.22, 0.0)),
    JointNode("node_23", "node_11", (0.0, -0.22, 0.02)),  # -X foot leaf
    JointNode("node_19", "node_04", (0.0, 0.14, 0.0)),   # chest: the second branch
    JointNode("node_06", "node_19", (0.0, 0.10, 0.0)),   # neck (medial)
    JointNode("node_21", "node_06", (0.0, 0.10, 0.0)),   # head leaf
    JointNode("node_13", "node_19", (0.05, 0.06, 0.0)),  # +X clavicle
    JointNode("node_27", "node_19", (-0.05, 0.06, 0.0)),  # -X clavicle
    JointNode("node_09", "node_13", (0.10, 0.0, 0.0)),
    JointNode("node_15", "node_09", (0.24, 0.0, 0.0)),
    JointNode("node_03", "node_15", (0.22, 0.0, 0.0)),   # +X hand leaf
    JointNode("node_25", "node_27", (-0.10, 0.0, 0.0)),
    JointNode("node_12", "node_25", (-0.24, 0.0, 0.0)),
    JointNode("node_07", "node_12", (-0.22, 0.0, 0.0)),  # -X hand leaf
]

# The side claim, as it would come off the source rig's own naming. Deliberately kept apart from
# the geometry: this is what a mirrored rig gets wrong, and comparing it against the geometry is
# the entire content of the medial/lateral gate.
TRUE_SIDE_LABELS = {"node_13": "l", "node_27": "r", "node_29": "l", "node_08": "r"}


def mirrored_humanoid():
    """The same rig with every X negated — a mirrored build, plausible from every angle."""
    return [
        JointNode(node.id, node.parent, (-node.local_position[0], node.local_position[1], node.local_position[2]))
        for node in HUMANOID
    ]


H = 1.0


def authored(band, figure_height=H, source_name="authored"):
    """targets -> gait parameters -> authored clip, the first two arrows of §R4's loop."""
    params = gait_parameters(band, figure_height)
    return params, synthesise_gait_tracks(params, source_name=source_name)


class WalkRunRoundTrip(unittest.TestCase):
    """§R4's loop, run end to end: target features -> author -> measure -> compare."""

    def test_walk_round_trip_classifier_independently_reports_walk(self) -> None:
        band = walk_targets(H)
        params, clip = authored(band, source_name="authored-walk")

        features = measure(clip, H)
        primary, labels = classifier_verdict(classify(features))
        print(
            f"\n[walk round trip] speed={features.speed:.4f} travel={features.travel:.4f} "
            f"rise={features.rise:.4f} -> {primary!r} {list(labels)}"
        )

        self.assertEqual(
            primary,
            "walk",
            f"§2 was handed the authored clip's measurements with no hint and reported {primary!r} "
            f"(labels {list(labels)}); §R4 requires it to agree the clip is a walk",
        )
        self.assertNotIn("run", labels)
        self.assertNotIn("idle", labels)
        self.assertNotIn("planted", labels, "a walk whose feet read as planted is not a walk")

        check = band.contains(band_features(clip, H, measure=measure))
        self.assertTrue(check.ok, f"authored walk fell outside its own target band: {check.summary()}")
        self.assertEqual(check.unevaluated, (), check.summary())

    def test_run_recipe_makes_the_classifier_say_run_not_walk(self) -> None:
        """§R4: "The classifier must then report `run`, not `walk`, without being told."""
        walk_band, run_band = walk_targets(H), run_targets(H)

        # The spec's run recipe: three changes and nothing else.
        self.assertGreater(run_band.target("speed"), walk_band.target("speed"))
        self.assertLess(run_band.target("contact"), 0.5)
        self.assertGreater(run_band.target("handRange"), walk_band.target("handRange"))

        params, clip = authored(run_band, source_name="authored-run")
        self.assertGreater(params.flight_fraction, 0.0, "contact below 0.5 must produce a flight phase")
        self.assertEqual(params.double_support_fraction, 0.0)

        features = measure(clip, H)
        primary, labels = classifier_verdict(classify(features))
        print(f"\n[run round trip] speed={features.speed:.4f} -> {primary!r} {list(labels)}")

        self.assertEqual(primary, "run", f"§2 reported {primary!r} (labels {list(labels)}), expected run")
        self.assertNotIn(
            "walk",
            labels,
            "raising the speed per the run recipe must move the clip out of the walk band entirely, "
            "not merely add a run label alongside it",
        )

        check = run_band.contains(band_features(clip, H, measure=measure))
        self.assertTrue(check.ok, f"authored run fell outside its own target band: {check.summary()}")

    def test_walk_and_run_do_not_share_a_classification(self) -> None:
        """The two authored clips must land in different classes, from the same code path."""
        _, walk_clip = authored(walk_targets(H))
        _, run_clip = authored(run_targets(H))
        walk_primary, _ = classifier_verdict(classify(measure(walk_clip, H)))
        run_primary, _ = classifier_verdict(classify(measure(run_clip, H)))
        self.assertNotEqual(walk_primary, run_primary)
        self.assertEqual((walk_primary, run_primary), ("walk", "run"))


class FootContact(unittest.TestCase):
    def test_a_stance_foot_that_drifts_fails_the_gate_and_a_planted_one_passes(self) -> None:
        """§R4's most valuable gate, shown failing and passing on the same gait.

        The drifting version is a legitimate-looking animation: the foot is in the right place
        at every keyframe, the timing is unchanged, and the only thing wrong with it is that
        the contact constraint is not enforced. That is exactly the defect that "reads as
        'floaty' or 'skating' to every viewer while being hard to name by eye".
        """
        params, clip = authored(walk_targets(H))
        report = foot_slide(clip["landmarkPositions"], clip["sampleTimes"], clip["stance"], H)
        print(f"\n[footSlide, contact enforced] {report.status}: {report.reason}")
        self.assertEqual(report.status, "pass")
        self.assertEqual(report.max_slide_fraction, 0.0, "an enforced contact constraint means exactly zero slide")
        self.assertGreater(len(report.per_stance), 0)

        drifted = json.loads(json.dumps(clip))
        stance_end = params.contact_fraction * params.duration
        moved = 0
        for index, t in enumerate(drifted["sampleTimes"]):
            if t <= stance_end + 1e-9:
                drifted["landmarkPositions"]["foot.l"][index][2] += 0.05 * H * (t / stance_end if stance_end else 0.0)
                moved += 1
        self.assertGreater(moved, 1, "the drift has to touch at least two stance frames to be measurable")

        drifted_report = foot_slide(
            drifted["landmarkPositions"], drifted["sampleTimes"], drifted["stance"], H
        )
        print(f"[footSlide, 0.05H drift] {drifted_report.status}: {drifted_report.reason}")
        self.assertEqual(drifted_report.status, "fail")
        self.assertGreater(drifted_report.max_slide_fraction, FOOT_SLIDE_LIMIT)
        self.assertAlmostEqual(drifted_report.max_slide_fraction, 0.05, places=9)
        self.assertEqual(drifted_report.worst.landmark, "foot.l")

    def test_no_stance_data_is_unevaluated_not_a_pass(self) -> None:
        _, clip = authored(walk_targets(H))
        report = foot_slide(clip["landmarkPositions"], clip["sampleTimes"], {}, H)
        self.assertEqual(report.status, "unevaluated")
        self.assertFalse(report.ok)
        self.assertIsNone(report.max_slide_fraction)
        self.assertIn("stance", report.reason)


class MedialLateralGate(unittest.TestCase):
    def test_catches_a_mirrored_rig(self) -> None:
        """`leftAnchor.x > 0 > rightAnchor.x`, on a rig where it does not hold.

        The mirrored rig is built by negating every X. Nothing about it looks wrong: the
        proportions are identical, both arms swing, and it animates cleanly. The only visible
        symptom is that every left-hand action plays on the right.
        """
        good = resolve_chains(HUMANOID, side_labels=TRUE_SIDE_LABELS)
        self.assertTrue(good.gate_is_independent, "with side_labels the gate must be able to fail")
        self.assertEqual(check_medial_lateral(good), [], "the correctly built rig must pass cleanly")

        mirrored = resolve_chains(mirrored_humanoid(), side_labels=TRUE_SIDE_LABELS)
        errors = check_medial_lateral(mirrored)
        print("\n[medial/lateral] " + "\n  ".join(errors))
        self.assertTrue(errors, "a mirrored rig must be rejected")

        self.assertLess(
            mirrored.arm["l"].anchor_position[0], 0.0, "precondition: the mirrored rig's left anchor is at negative x"
        )
        self.assertTrue(any("arm.l" in message and "expected x > 0" in message for message in errors), errors)
        self.assertTrue(any("arm.r" in message and "expected x < 0" in message for message in errors), errors)
        self.assertTrue(any("leg.l" in message for message in errors), errors)

    def test_without_side_labels_the_gate_reports_that_it_cannot_fail(self) -> None:
        """A pass that could not have been a fail is not evidence, and says so."""
        mirrored = resolve_chains(mirrored_humanoid())
        self.assertEqual(mirrored.side_source, "model-x")
        self.assertFalse(mirrored.gate_is_independent)
        self.assertEqual(check_medial_lateral(mirrored), [])
        self.assertTrue(any("gate_is_independent is False" in note for note in mirrored.notes), mirrored.notes)

    def test_medial_axis_check_is_opt_in_and_catches_an_off_midline_spine(self) -> None:
        chains = resolve_chains(HUMANOID, side_labels=TRUE_SIDE_LABELS)
        # Shift the NECK only, so the head leaves the midline while both lateral pairs stay
        # exactly where they were. A whole-rig shift would move the arm anchors too, and the
        # lateral half of the gate would fire instead of the medial half under test.
        offset = [
            JointNode(
                n.id,
                n.parent,
                (n.local_position[0] + (0.3 if n.id == "node_06" else 0.0), n.local_position[1], n.local_position[2]),
            )
            for n in HUMANOID
        ]
        shifted = resolve_chains(offset, side_labels=TRUE_SIDE_LABELS)
        self.assertEqual(check_medial_lateral(chains, medial_tolerance=0.02 * H), [])
        self.assertEqual(check_medial_lateral(shifted), [], "off by default: no absolute tolerance is defensible")
        self.assertTrue(any("MEDIAL_AXIS" in e for e in check_medial_lateral(shifted, medial_tolerance=0.02 * H)))


class ChainResolutionFromTopology(unittest.TestCase):
    def test_resolves_arms_legs_and_spine_without_reading_a_single_joint_name(self) -> None:
        """Every joint in the fixture is called `node_NN`. Only the graph's shape can resolve it."""
        chains = resolve_chains(HUMANOID, side_labels=TRUE_SIDE_LABELS)

        self.assertTrue(
            all(joint.startswith("node_") for chain in chains.all_chains() for joint in chain.joint_ids),
            "fixture precondition: no joint name carries any information about what it is",
        )

        self.assertEqual(chains.arm["l"].joint_ids, ("node_13", "node_09", "node_15", "node_03"))
        self.assertEqual(chains.arm["r"].joint_ids, ("node_27", "node_25", "node_12", "node_07"))
        self.assertEqual(chains.leg["l"].joint_ids, ("node_29", "node_31", "node_02"))
        self.assertEqual(chains.leg["r"].joint_ids, ("node_08", "node_11", "node_23"))
        self.assertEqual(
            chains.spine.joint_ids,
            ("node_00", "node_17", "node_04", "node_19", "node_06", "node_21"),
            "spine runs root -> hip branch -> shoulder branch -> head leaf",
        )
        self.assertEqual(chains.spine.leaf, "node_21", "the spine ends at the head leaf")
        self.assertEqual(chains["arm.l"].leaf, "node_03", "arm chains end at a hand leaf")
        self.assertEqual(chains["leg.r"].leaf, "node_23", "leg chains end at a foot leaf")

    def test_symmetry_pairs_come_from_mirrored_local_x(self) -> None:
        chains = resolve_chains(HUMANOID)
        for group in (chains.arm, chains.leg):
            left_x = chains.world_positions[group["l"].anchor][0]
            right_x = chains.world_positions[group["r"].anchor][0]
            self.assertAlmostEqual(left_x, -right_x, places=9)

    def test_a_hierarchy_with_no_shoulder_branch_is_rejected_not_guessed(self) -> None:
        legs_only = [node for node in HUMANOID if node.id not in {"node_13", "node_27", "node_09", "node_15", "node_03", "node_25", "node_12", "node_07"}]
        with self.assertRaises(ChainResolutionError) as caught:
            resolve_chains(legs_only)
        self.assertIn("arm.l", str(caught.exception))

    def test_two_roots_are_rejected(self) -> None:
        with self.assertRaises(ChainResolutionError):
            resolve_chains(HUMANOID + [JointNode("node_99", None, (0.0, 0.0, 0.0))])


class GaitIsAPhaseMachine(unittest.TestCase):
    def test_stance_intervals_are_explicit_and_wrap(self) -> None:
        params = gait_parameters(walk_targets(H), H)
        left, right = params.phase("leg.l"), params.phase("leg.r")
        self.assertEqual(left.stance_intervals(), ((0.0, params.contact_fraction * params.duration),))
        self.assertEqual(len(right.stance_intervals()), 2, "the trailing leg's stance wraps across t=0")
        total = sum(hi - lo for hi, lo in ((b, a) for a, b in right.stance_intervals()))
        self.assertAlmostEqual(total, params.contact_fraction * params.duration, places=12)

    def test_double_support_is_the_specs_own_arithmetic(self) -> None:
        walk = gait_parameters(walk_targets(H), H)
        self.assertAlmostEqual(walk.contact_fraction, 0.60, places=12)
        self.assertAlmostEqual(walk.double_support_fraction, 0.20, places=12)
        self.assertEqual(walk.flight_fraction, 0.0)
        run = gait_parameters(run_targets(H), H)
        self.assertAlmostEqual(run.flight_fraction, 0.20, places=12)

    def test_the_foot_world_position_is_literally_constant_during_stance(self) -> None:
        """Not "nearly constant" — a sine-wave foot would move at every instant but the turns."""
        params, clip = authored(walk_targets(H))
        times = clip["sampleTimes"]
        for side in ("l", "r"):
            phase = params.phase(f"leg.{side}")
            track = clip["landmarkPositions"][f"foot.{side}"]
            by_plant: dict[int, set[tuple[float, float, float]]] = {}
            for index, t in enumerate(times):
                if phase.is_stance(t):
                    by_plant.setdefault(phase.plant_index(t)[0], set()).add(tuple(track[index]))
            self.assertTrue(by_plant)
            for plant, seen in by_plant.items():
                self.assertEqual(len(seen), 1, f"foot.{side} moved during plant {plant}: {seen}")

    def test_gait_parameters_scale_with_figure_height(self) -> None:
        """The band is in fractions of H; the authored parameters are in model units."""
        small = gait_parameters(walk_targets(H), 1.0)
        large = gait_parameters(walk_targets(H), 2.5)
        self.assertAlmostEqual(large.travel / small.travel, 2.5, places=12)
        self.assertAlmostEqual(large.hip_rise / small.hip_rise, 2.5, places=12)
        self.assertAlmostEqual(large.duration, small.duration, places=12)
        big_clip = synthesise_gait_tracks(large)
        self.assertEqual(
            classifier_verdict(classify(measure(big_clip, 2.5)))[0],
            "walk",
            "a walk on a 2.5-unit-tall figure must still classify as a walk",
        )

    def test_primitives_table_carries_the_specs_parameters(self) -> None:
        self.assertEqual(set(PRIMITIVES), {"gait", "ballistic", "reach", "additive"})
        self.assertEqual(
            PRIMITIVES["gait"].parameters,
            ("cadence", "stride", "contactFraction", "hipRise", "armCounterSwing"),
        )
        self.assertEqual(
            PRIMITIVES["ballistic"].parameters,
            ("takeoffVelocity", "apexRise", "flightTime", "landAbsorption"),
        )
        self.assertEqual(
            PRIMITIVES["reach"].parameters, ("targetSocket", "windupFraction", "followThrough", "return")
        )
        self.assertEqual(PRIMITIVES["additive"].parameters, ("amplitude", "period", "jointMask"))


class TargetBands(unittest.TestCase):
    def test_contains_names_the_feature_that_fell_outside(self) -> None:
        """"Outside the band" is not actionable; "rise=0.19H is above [0.015, 0.025]" is."""
        band = walk_targets(H)
        good = band_features(authored(band)[1], H, measure=measure)

        limping = dict(good, rise=0.19)
        check = band.contains(limping)
        print(f"\n[band] {check.summary()}")
        self.assertFalse(check.ok)
        self.assertEqual(len(check.outside), 1)
        miss = check.outside[0]
        self.assertEqual(miss.feature, "rise")
        self.assertEqual(miss.side, "above")
        self.assertEqual(miss.value, 0.19)
        self.assertIn("rise", str(miss))
        self.assertIn("0.19", str(miss))
        self.assertIn("rise", check.summary())

        below = band.contains(dict(good, handRange=0.01))
        self.assertEqual([m.feature for m in below.outside], ["handRange"])
        self.assertEqual(below.outside[0].side, "below")

        both = band.contains(dict(good, rise=0.19, handRange=0.01))
        self.assertEqual(sorted(m.feature for m in both.outside), ["handRange", "rise"])

    def test_a_missing_feature_is_unevaluated_not_inside(self) -> None:
        band = walk_targets(H)
        good = band_features(authored(band)[1], H, measure=measure)
        partial = {k: v for k, v in good.items() if k != "contact"}
        check = band.contains(partial)
        self.assertTrue(check.ok, "nothing measured fell outside")
        self.assertEqual([name for name, _ in check.unevaluated], ["contact"])
        self.assertNotIn("contact", check.inside)

    def test_speed_band_mirrors_the_classifier_boundary_exclusively(self) -> None:
        """§2 writes `speed < 0.60H/s`; a band with an inclusive 0.60 would accept a run."""
        walk = walk_targets(H).ranges["speed"]
        self.assertTrue(walk.hi_exclusive)
        self.assertTrue(walk.contains(0.5999))
        self.assertFalse(walk.contains(0.60))
        self.assertEqual((walk.lo, walk.hi, walk.target), (0.30, 0.60, 0.40))

        run = run_targets(H).ranges["speed"]
        self.assertEqual((run.lo, run.hi), (0.60, 1.50))
        self.assertTrue(run.hi_exclusive)

    def test_the_worked_walk_example_is_encoded_verbatim(self) -> None:
        band = walk_targets(H)
        self.assertEqual((band.ranges["duration"].lo, band.ranges["duration"].hi), (1.0, 1.2))
        self.assertEqual((band.ranges["rise"].lo, band.ranges["rise"].hi), (0.015, 0.025))
        self.assertEqual((band.ranges["handRange"].lo, band.ranges["handRange"].hi), (0.15, 0.25))
        self.assertEqual(band.ranges["contact"].target, 0.60)
        self.assertEqual((band.ranges["scaleDelta"].lo, band.ranges["scaleDelta"].hi), (0.0, 0.0))
        self.assertEqual(band.ranges["poseReturn"].hi, POSE_RETURN_DEGREES)
        # travel = speed x duration, at the spec's 0.40H/s target
        self.assertAlmostEqual(band.ranges["travel"].lo, 0.40 * 1.0, places=12)
        self.assertAlmostEqual(band.ranges["travel"].hi, 0.40 * 1.2, places=12)
        # footRange ~= travel / 2
        self.assertAlmostEqual(band.ranges["footRange"].target, 0.5 * 0.5 * (0.40 + 0.48), places=12)

    def test_run_targets_change_only_the_three_things_the_spec_changes(self) -> None:
        walk, run = walk_targets(H), run_targets(H)
        changed = {name for name in walk.ranges if walk.ranges[name] != run.ranges[name]}
        self.assertEqual(
            changed,
            {"speed", "contact", "handRange", "travel", "footRange"},
            "speed, contact and handRange are the spec's three; travel and footRange are derived from speed",
        )


class GateR4(unittest.TestCase):
    def _accepted_walk(self, **overrides):
        band = walk_targets(H)
        params, clip = authored(band)
        features = dict(band_features(clip, H, measure=measure))
        features.update(overrides.pop("features", {}))
        kwargs = dict(
            classifier=classify,
            classifier_input=measure(clip, H),
            foot_slide_result=foot_slide(clip["landmarkPositions"], clip["sampleTimes"], clip["stance"], H),
            source_ranges={"node_29": (-0.4, 0.4)},
            authored_ranges={"node_29": (-0.2, 0.2)},
        )
        kwargs.update(overrides)
        return accepts(features, band, "walk", **kwargs)

    def test_a_correctly_authored_walk_passes_every_criterion(self) -> None:
        acceptance = self._accepted_walk(declared_loop=None)
        for criterion in acceptance.criteria:
            print(f"\n[R4] {criterion.name}: {criterion.status} — {criterion.reason}")
        self.assertTrue(acceptance.accepted, [c.reason for c in acceptance.failures])
        self.assertEqual(acceptance.verdict, "accepted-with-unevaluated")
        self.assertEqual(
            [c.name for c in acceptance.unevaluated],
            ["pose-return-loop"],
            "the clip declares no loop flag, and §4 forbids guessing false",
        )
        self.assertEqual(acceptance.criterion("classifier-agreement").status, "pass")
        self.assertEqual(acceptance.criterion("foot-slide").status, "pass")

    def test_rejects_a_clip_with_scale_delta_above_zero_even_when_everything_else_passes(self) -> None:
        """Gate R4: `scaleDelta == 0`. §1 calls it a tripwire, not a descriptor."""
        clean = self._accepted_walk(declared_loop=None)
        self.assertTrue(clean.accepted)

        scaled = self._accepted_walk(declared_loop=None, features={"scaleDelta": 1e-6})
        print(f"\n[R4 scaleDelta] {scaled.criterion('no-joint-scale').reason}")
        self.assertFalse(scaled.accepted, "a non-zero scaleDelta must reject the clip on its own")
        self.assertEqual(scaled.verdict, "rejected")
        self.assertEqual(
            sorted(c.name for c in scaled.failures),
            ["band-containment", "no-joint-scale"],
            "the scale tripwire fires, and the band's own scaleDelta == 0 range fires with it",
        )
        for name in ("classifier-agreement", "foot-slide"):
            self.assertEqual(scaled.criterion(name).status, "pass", "every other criterion still passes")

    def test_joint_range_is_unevaluated_not_passed_when_no_source_clips_exist(self) -> None:
        """"no joint exceeds its measured range from the source clips, **where source clips exist**"."""
        acceptance = self._accepted_walk(declared_loop=None, source_ranges=None, authored_ranges=None)
        criterion = acceptance.criterion("joint-range-vs-source")
        print(f"\n[R4 joint range] {criterion.status}: {criterion.reason}")
        self.assertEqual(criterion.status, "unevaluated")
        self.assertNotEqual(criterion.status, "pass")
        self.assertIn("no source clips", criterion.reason)
        self.assertTrue(acceptance.accepted, "an unevaluated criterion is not a failure")
        self.assertFalse(acceptance.complete, "but it does mean the gate was not fully answered")

    def test_joint_range_fails_when_an_authored_joint_exceeds_its_source_range(self) -> None:
        acceptance = self._accepted_walk(
            declared_loop=None,
            source_ranges={"node_29": (-0.4, 0.4)},
            authored_ranges={"node_29": (-0.9, 0.4)},
        )
        criterion = acceptance.criterion("joint-range-vs-source")
        self.assertEqual(criterion.status, "fail")
        self.assertIn("node_29", criterion.reason)
        self.assertFalse(acceptance.accepted)

    def test_classifier_disagreement_rejects_the_clip(self) -> None:
        """The acceptance test is the classifier, so a clip it calls something else is rejected."""
        band = run_targets(H)
        _, run_clip = authored(band)
        features = band_features(run_clip, H, measure=measure)
        acceptance = accepts(
            features,
            band,
            "walk",  # the clip claims to be a walk; the measurements say otherwise
            classifier=classify,
            classifier_input=measure(run_clip, H),
        )
        criterion = acceptance.criterion("classifier-agreement")
        print(f"\n[R4 classifier] {criterion.reason}")
        self.assertEqual(criterion.status, "fail")
        self.assertIn("does not agree", criterion.reason)
        self.assertFalse(acceptance.accepted)

    def test_a_declared_loop_is_held_to_the_pose_return_bound(self) -> None:
        good = self._accepted_walk(declared_loop=True)
        self.assertEqual(good.criterion("pose-return-loop").status, "pass")
        bad = self._accepted_walk(declared_loop=True, features={"poseReturn": 4.0})
        self.assertEqual(bad.criterion("pose-return-loop").status, "fail")
        self.assertFalse(bad.accepted)

    def test_missing_foot_slide_is_unevaluated_not_a_pass(self) -> None:
        acceptance = self._accepted_walk(declared_loop=None, foot_slide_result=None)
        criterion = acceptance.criterion("foot-slide")
        self.assertEqual(criterion.status, "unevaluated")
        self.assertIn("has not been asked", criterion.reason)

    def test_missing_classifier_is_unevaluated_and_says_why_that_matters(self) -> None:
        band = walk_targets(H)
        _, clip = authored(band)
        acceptance = accepts(band_features(clip, H, measure=measure), band, "walk", classifier=lambda _f: None)
        self.assertEqual(acceptance.criterion("classifier-agreement").status, "fail")


class HipRelativeFrame(unittest.TestCase):
    def test_removing_root_motion_keeps_vertical_motion(self) -> None:
        _, clip = authored(walk_targets(H))
        relative = hip_relative_clip(clip)
        world_features, relative_features = measure(clip, H), measure(relative, H)
        self.assertAlmostEqual(relative_features.travel, 0.0, places=12)
        self.assertAlmostEqual(relative_features.rise, world_features.rise, places=12)
        self.assertLess(relative_features.foot_range, world_features.foot_range)

    def test_foot_slide_on_a_hip_relative_clip_would_reject_a_correct_gait(self) -> None:
        """The reason `hip_relative_clip` carries a warning: this is the wrong frame for G8."""
        _, clip = authored(walk_targets(H))
        relative = hip_relative_clip(clip)
        world = foot_slide(clip["landmarkPositions"], clip["sampleTimes"], clip["stance"], H)
        wrong_frame = foot_slide(relative["landmarkPositions"], relative["sampleTimes"], relative["stance"], H)
        self.assertEqual(world.status, "pass")
        self.assertEqual(wrong_frame.status, "fail", "documenting the trap, not endorsing it")


class FallbackAndHelpers(unittest.TestCase):
    def test_the_fallback_classifier_matches_the_specs_validation_table(self) -> None:
        """§2's published measurements, run through whichever classifier this suite is using.

        Pins the local fallback to the spec rather than to itself. Without this, a fallback
        that quietly disagreed with §2 would let the round-trip tests pass on a classifier
        nobody had checked.
        """

        class Row:
            def __init__(self, travel, rise, speed, hand_range=0.0, foot_range=1.0):
                self.travel, self.rise, self.speed = travel, rise, speed
                self.hand_range, self.foot_range = hand_range, foot_range
                self.duration, self.head_rise, self.scale_delta, self.pose_return = 1.0, 0.0, 0.0, 0.0

        table = [
            ("walk-forward", Row(1.133, 0.019, 0.400), "walk"),
            ("run-forward", Row(1.465, 0.021, 0.799), "run"),
            ("dash-forward", Row(2.906, 0.02, 2.250), "dash"),
            ("jump-in-place", Row(0.272, 0.248, 0.0), "jump"),
            ("leap-forward", Row(2.826, 0.517, 0.0), "leap"),
            ("idle-still", Row(0.013, 0.0001, 0.0), "idle"),
        ]
        for name, row, expected in table:
            primary, labels = classifier_verdict(classify(row))
            self.assertEqual(primary, expected, f"{name}: got {primary!r} (labels {list(labels)})")

        _, arms_only = classifier_verdict(classify(Row(0.01, 0.008, 0.0, hand_range=0.55, foot_range=0.02)))
        self.assertIn("planted", arms_only)
        self.assertIn("gesture", arms_only)

    def test_classifier_verdict_normalises_every_plausible_return_shape(self) -> None:
        self.assertEqual(classifier_verdict("walk"), ("walk", ("walk",)))
        self.assertEqual(classifier_verdict(["in-place", "walk"])[0], "walk")
        self.assertEqual(classifier_verdict(("run", ["run", "in-place"])), ("run", ("run", "in-place")))
        self.assertEqual(classifier_verdict({"primary": "dash", "labels": ["dash"]}), ("dash", ("dash",)))
        self.assertEqual(classifier_verdict(None), (None, ()))
        self.assertEqual(classifier_verdict([]), (None, ()), "an empty match stays empty; no class is invented")

    def test_sample_times_do_not_double_count_the_loop_point(self) -> None:
        _, clip = authored(walk_targets(H))
        times = clip["sampleTimes"]
        self.assertEqual(len(times), 25)
        self.assertEqual(times[0], 0.0)
        self.assertLess(times[-1], clip["duration"])

    def test_authored_clips_validate_against_the_contract_payload_shape(self) -> None:
        _, clip = authored(walk_targets(H))
        payload = build_payload([clip], H)
        self.assertEqual(payload["figureHeight"], H)
        for landmark in payload["landmarks"]:
            self.assertEqual(len(clip["landmarkPositions"][landmark]), len(clip["sampleTimes"]))
        self.assertEqual(len(clip["jointScaleDelta"]), len(clip["sampleTimes"]))
        self.assertEqual(max(clip["jointScaleDelta"]), 0.0)
        if USING_REAL_CLIP_FEATURES:
            from clip_features import load_payload  # noqa: PLC0415

            load_payload(payload)  # raises if the shape is wrong


class Cli(unittest.TestCase):
    def _run(self, document, intended, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(MODULE), str(path), intended, *extra],
                capture_output=True,
                text=True,
                timeout=120,
            )

    def test_cli_accepts_a_good_walk_and_exits_zero(self) -> None:
        if not USING_REAL_CLIP_FEATURES:
            self.skipTest("the payload path of the CLI needs clip_features.measure_clip")
        _, clip = authored(walk_targets(H))
        proc = self._run(build_payload([clip], H), "walk")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["gate"], "R4")
        self.assertTrue(report["accepted"])

    def test_cli_exits_one_on_rejection(self) -> None:
        document = {
            "figureHeight": H,
            "features": {
                "duration": 1.1, "travel": 0.42, "rise": 0.02, "speed": 0.384,
                "handRange": 0.20, "footRange": 0.26, "contact": 0.6,
                "poseReturn": 0.0, "scaleDelta": 0.004,
            },
        }
        proc = self._run(document, "walk")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        report = json.loads(proc.stdout)
        self.assertEqual(report["verdict"], "rejected")
        self.assertIn("no-joint-scale", [c["criterion"] for c in report["criteria"] if c["status"] == "fail"])


if __name__ == "__main__":
    print(
        f"clip_features {'IS' if USING_REAL_CLIP_FEATURES else 'is NOT'} importable; "
        f"using the {'real' if USING_REAL_CLIP_FEATURES else 'local fallback'} §1/§2 implementation"
    )
    unittest.main(verbosity=2)
