#!/usr/bin/env python3
"""Tests for the §1-§4 clip vocabulary, built around the defects it exists to remove.

The centrepiece is `TheSpecValidationTable`. It does not hand `classify` a struct full of numbers --
anyone can make a classifier agree with a struct they typed. It SYNTHESISES landmark tracks that
should measure to the numbers the spec's own validation table reports, measures them with
`measure_clip`, and only then classifies. Synthesise -> measure -> classify is the whole round trip;
break the measurement and the table stops reproducing, which is exactly what a silent unit change or
a dropped normalisation would do.

The second target is the loop rule. 1.5.1 shipped a rule that contradicted its own data, so
`test_the_1_5_1_rule_would_have_rejected_the_clip_it_was_derived_from` executes both rules on the
same clip and asserts they disagree. Describing the contradiction in a comment would let it come
back; running it cannot.

Pure Python 3.10+ stdlib.
"""

from __future__ import annotations

import io
import json
import math
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stage5_rig"))

from clip_features import (  # noqa: E402
    AUTO,
    DEFAULT_THRESHOLDS,
    REQUIRED_LANDMARKS,
    ClipFeatures,
    Thresholds,
    _legacy_loop_rule_1_5_1,
    classify,
    decide_loop,
    load_payload,
    main,
    measure_clip,
    name_clip,
    report,
)

SAMPLES = 25          # §1: "Sample the clip at N = 25 evenly spaced times"
HIP_Y = 0.50          # hip height, fraction of H
HEAD_OFFSET = 0.42    # head above hip, fraction of H


def wave(index: int, samples: int = SAMPLES, cycles: int = 2) -> float:
    """A sine over the clip that hits exactly +1 and -1 at N = 25 with two cycles.

    Exactness matters: `rise` is a max-minus-min, so if the wave never quite reached its extremes the
    synthesised clip would measure slightly under its target and every assertion below would need a
    fudge factor large enough to hide a real error.
    """
    return math.sin(2.0 * math.pi * cycles * index / (samples - 1))


def synth_clip(
    source_name: str,
    *,
    duration: float,
    travel: float,
    rise: float,
    hand_swing: float = 0.10,
    stance_half_width: float = 0.06,
    feet_follow_hip: bool = True,
    foot_jitter: float = 0.0,
    hip_returns: bool = False,
    pose_return: float | None = 0.0,
    scale_delta: float = 0.0,
    height: float = 1.0,
    samples: int = SAMPLES,
) -> dict:
    """Build a sampled clip whose §1 measurements are known in advance.

    `travel` and `rise` are stated as fractions of H and the tracks are then multiplied by `height`,
    which is what makes the H-scale invariance test meaningful: the same call at height 2.0 produces
    genuinely doubled world coordinates, not a rescaled feature struct.

    `hip_returns` walks the hip out and back so it ends where it started -- the `idle-gesture` shape
    that broke the 1.5.1 loop rule.
    """
    times, hip, head, hand_l, hand_r, foot_l, foot_r = [], [], [], [], [], [], []
    for index in range(samples):
        unit = index / (samples - 1)
        swing = wave(index, samples)
        hip_x = travel * (math.sin(math.pi * unit) if hip_returns else unit)
        hip_y = HIP_Y + 0.5 * rise * swing
        foot_base = hip_x if feet_follow_hip else 0.0
        times.append(duration * unit)
        hip.append([hip_x * height, hip_y * height, 0.0])
        head.append([hip_x * height, (hip_y + HEAD_OFFSET) * height, 0.0])
        hand_l.append([(hip_x + hand_swing * swing) * height, (hip_y + 0.05) * height, 0.02 * height])
        hand_r.append([(hip_x - hand_swing * swing) * height, (hip_y + 0.05) * height, -0.02 * height])
        foot_l.append([(foot_base + stance_half_width + foot_jitter * swing) * height, 0.0, 0.0])
        foot_r.append([(foot_base - stance_half_width - foot_jitter * swing) * height, 0.0, 0.0])

    clip = {
        "sourceName": source_name,
        "duration": duration,
        "sampleTimes": times,
        "landmarkPositions": {
            "hip": hip,
            "head": head,
            "hand.l": hand_l,
            "hand.r": hand_r,
            "foot.l": foot_l,
            "foot.r": foot_r,
        },
        # One non-zero sample is enough to trip the wire; scaleDelta is a max over samples.
        "jointScaleDelta": [scale_delta if index == samples // 2 else 0.0 for index in range(samples)],
    }
    if pose_return is not None:
        clip["poseReturn"] = pose_return
    return clip


def payload_of(clips: list[dict], height: float = 1.0) -> dict:
    return {"figureHeight": height, "landmarks": list(REQUIRED_LANDMARKS), "clips": clips}


def measure(clip: dict, height: float = 1.0) -> ClipFeatures:
    return measure_clip(clip, height)


# The spec's own validation table (§2). `duration` is chosen so that travel / duration reproduces the
# quoted speed exactly; the dashes in the spec's table are filled with values that keep the clip in
# the class the table assigns it and are never asserted as if they came from the source.
VALIDATION_TABLE: list[dict] = [
    {
        "name": "walk-forward", "travel": 1.133, "rise": 0.019, "speed": 0.400,
        "duration": 1.133 / 0.400, "primary": "walk", "kwargs": {"hand_swing": 0.10},
    },
    {
        "name": "run-forward", "travel": 1.465, "rise": 0.021, "speed": 0.799,
        "duration": 1.465 / 0.799, "primary": "run", "kwargs": {"hand_swing": 0.18},
    },
    {
        "name": "dash-forward", "travel": 2.906, "rise": 0.030, "speed": 2.250,
        "duration": 2.906 / 2.250, "primary": "dash", "kwargs": {"hand_swing": 0.22},
    },
    {
        "name": "jump-in-place", "travel": 0.272, "rise": 0.248, "speed": 0.272,
        "duration": 1.0, "primary": "jump", "kwargs": {"hand_swing": 0.05},
    },
    {
        "name": "leap-forward", "travel": 2.826, "rise": 0.517, "speed": 2.826 / 1.4,
        "duration": 1.4, "primary": "leap", "kwargs": {"hand_swing": 0.20},
    },
    {
        "name": "idle-still", "travel": 0.013, "rise": 0.0001, "speed": 0.0065,
        "duration": 2.0, "primary": "idle",
        "kwargs": {"hand_swing": 0.01, "feet_follow_hip": False, "stance_half_width": 0.02, "foot_jitter": 0.005},
    },
    {
        "name": "arms-only", "travel": 0.050, "rise": 0.008, "speed": 0.050 / 1.5,
        "duration": 1.5, "primary": "in-place",
        "kwargs": {"hand_swing": 0.30, "feet_follow_hip": False, "stance_half_width": 0.02, "foot_jitter": 0.005},
    },
]


def table_clip(row: dict, height: float = 1.0) -> dict:
    return synth_clip(
        row["name"],
        duration=row["duration"],
        travel=row["travel"],
        rise=row["rise"],
        height=height,
        **row["kwargs"],
    )


class TheSpecValidationTable(unittest.TestCase):
    """Synthesise -> measure -> classify, against the seven clips §2 validates itself on."""

    def test_synthesised_clips_measure_the_numbers_the_spec_table_reports(self) -> None:
        for row in VALIDATION_TABLE:
            with self.subTest(clip=row["name"]):
                features = measure(table_clip(row))
                self.assertAlmostEqual(features.travel, row["travel"], places=9)
                self.assertAlmostEqual(features.rise, row["rise"], places=9)
                self.assertAlmostEqual(features.speed, row["speed"], places=9)
                self.assertAlmostEqual(features.duration, row["duration"], places=9)
                self.assertEqual(features.sample_count, SAMPLES)

    def test_each_clip_lands_in_the_class_the_table_says(self) -> None:
        for row in VALIDATION_TABLE:
            with self.subTest(clip=row["name"]):
                classification = classify(measure(table_clip(row)))
                self.assertEqual(classification.primary, row["primary"])
                self.assertIn(row["primary"], classification.labels)
                self.assertIn(row["primary"], classification.reasons)

    def test_a_leap_outranks_the_speed_class_it_also_matches(self) -> None:
        # leap-forward covers 2.826H in 1.4s, which is genuinely dash speed. Both labels are true and
        # both are returned; `dash` as the primary would lose the fact that it left the ground.
        classification = classify(measure(table_clip(VALIDATION_TABLE[4])))
        self.assertIn("dash", classification.labels)
        self.assertIn("leap", classification.labels)
        self.assertEqual(classification.primary, "leap")

    def test_arms_only_carries_all_three_labels_at_once(self) -> None:
        row = VALIDATION_TABLE[6]
        classification = classify(measure(table_clip(row)))
        # Exactly these three: the table says "planted + gesture", and a clip whose hips travel
        # 0.050H is also `in-place`. Nothing else may creep in.
        self.assertEqual(set(classification.labels), {"in-place", "planted", "gesture"})
        self.assertEqual(classification.primary, "in-place")

    def test_the_same_motion_at_H_2_classifies_identically(self) -> None:
        """H-scale invariance. Every feature is a fraction of H, so doubling the rig must change
        nothing -- a normalisation dropped anywhere turns every threshold into a unit-dependent
        accident."""
        for row in VALIDATION_TABLE:
            with self.subTest(clip=row["name"]):
                unit = measure(table_clip(row, height=1.0), 1.0)
                doubled = measure(table_clip(row, height=2.0), 2.0)
                for attribute in ("travel", "rise", "speed", "hand_range", "foot_range", "head_rise", "hip_return"):
                    self.assertAlmostEqual(
                        getattr(doubled, attribute), getattr(unit, attribute), places=9,
                        msg=f"{attribute} is not H-invariant",
                    )
                self.assertEqual(classify(doubled).labels, classify(unit).labels)
                self.assertEqual(classify(doubled).primary, classify(unit).primary)


class TheLoopRule(unittest.TestCase):
    """§4. The rule 1.5.1 got wrong, and the data that proves it wrong."""

    def wandering_clip(self) -> ClipFeatures:
        # `idle-gesture`: travel 0.121H -- six times the idle threshold -- and correctly loopable,
        # because the hip walks out and comes back and the pose returns.
        return measure(
            synth_clip(
                "NlaTrack.007", duration=2.0, travel=0.121, rise=0.010,
                hand_swing=0.30, hip_returns=True, pose_return=0.2,
                feet_follow_hip=False, stance_half_width=0.02,
            )
        )

    def test_a_clip_that_wanders_and_returns_loops(self) -> None:
        features = self.wandering_clip()
        self.assertAlmostEqual(features.travel, 0.121, places=9)
        self.assertLess(features.hip_return, 1e-9)
        decision = decide_loop(features)
        self.assertIs(decision.loop, True)
        self.assertIn("poseReturn", decision.reason)

    def test_the_1_5_1_rule_would_have_rejected_the_clip_it_was_derived_from(self) -> None:
        """The contradiction, executed rather than described.

        1.5.1: "a clip that neither travels nor rises can repeat seamlessly". idle-gesture travels
        0.121H and loops. Both rules run on the same features here, and they must disagree -- if this
        test ever goes green by agreement, the corrected rule has been reverted.
        """
        features = self.wandering_clip()
        self.assertFalse(_legacy_loop_rule_1_5_1(features))
        self.assertIs(decide_loop(features).loop, True)
        self.assertGreater(features.travel, DEFAULT_THRESHOLDS.idle_travel * 6)

    def test_a_clip_that_ends_mid_stride_does_not_loop(self) -> None:
        # Ends 0.013H from where it started -- a centimetre on a 1 m figure. Small enough to look
        # fine in a viewport, large enough to pop every time the clip wraps.
        features = measure(
            synth_clip("NlaTrack.004", duration=1.0, travel=0.013, rise=0.012, pose_return=0.2)
        )
        self.assertAlmostEqual(features.hip_return, 0.013, places=6)
        self.assertGreater(features.hip_return, decide_loop(features).hip_tolerance)
        decision = decide_loop(features)
        self.assertIs(decision.loop, False)
        self.assertIn("hip(T)", decision.reason)
        # And note what the OLD rule would have said: travel 0.013 < 0.02 and rise 0.012 < 0.02, so
        # it would have called this one loopable. The two rules fail in BOTH directions.
        self.assertTrue(_legacy_loop_rule_1_5_1(features))

    def test_pose_return_over_the_limit_rejects_even_when_the_hip_comes_home(self) -> None:
        features = measure(
            synth_clip("NlaTrack.009", duration=1.0, travel=0.20, rise=0.01, hip_returns=True, pose_return=8.0)
        )
        decision = decide_loop(features)
        self.assertIs(decision.loop, False)
        self.assertIn("poseReturn", decision.reason)

    def test_missing_pose_return_is_none_not_false(self) -> None:
        """Undecidable is a third answer.

        A host that cannot measure per-joint deltas omits the key. Reporting False would look
        conservative and be a lie: every loopable clip on that host silently becomes one-shot, and
        nothing downstream can tell "measured, does not loop" from "nobody looked".
        """
        features = measure(
            synth_clip("NlaTrack.002", duration=1.0, travel=0.05, rise=0.01, pose_return=None)
        )
        self.assertIsNone(features.pose_return)
        decision = decide_loop(features)
        self.assertIsNone(decision.loop)
        self.assertIsNot(decision.loop, False)
        self.assertEqual(decision.reason, "poseReturn not measured")

    def test_the_hip_tolerance_is_the_one_the_contract_names(self) -> None:
        features = measure(synth_clip("NlaTrack.010", duration=1.0, travel=0.05, rise=0.01, pose_return=0.0))
        self.assertEqual(decide_loop(features).hip_tolerance, 0.01)
        self.assertEqual(decide_loop(features).pose_return_limit, 0.5)


class TheScaleTripwire(unittest.TestCase):
    """§1. `scaleDelta` is a tripwire, not a descriptor -- it must be visible before anything else."""

    def test_scale_delta_above_zero_raises_the_flag(self) -> None:
        features = measure(
            synth_clip("NlaTrack.005", duration=1.0, travel=0.05, rise=0.01, scale_delta=0.004)
        )
        self.assertAlmostEqual(features.scale_delta, 0.004, places=12)
        self.assertTrue(features.scales_joints)

    def test_a_clean_clip_leaves_the_flag_down(self) -> None:
        features = measure(synth_clip("NlaTrack.006", duration=1.0, travel=0.05, rise=0.01))
        self.assertEqual(features.scale_delta, 0.0)
        self.assertFalse(features.scales_joints)

    def test_the_report_names_every_clip_that_trips_it(self) -> None:
        payload = load_payload(
            payload_of(
                [
                    synth_clip("clean", duration=1.0, travel=0.05, rise=0.01),
                    synth_clip("scaled", duration=1.0, travel=0.05, rise=0.01, scale_delta=0.02),
                ]
            )
        )
        result = report(payload)
        self.assertTrue(result["scaleTripwire"]["tripped"])
        self.assertEqual(result["scaleTripwire"]["clips"], ["scaled"])

    def test_the_cli_exits_1_when_a_clip_trips_the_tripwire(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.json"
            path.write_text(
                json.dumps(
                    payload_of([synth_clip("scaled", duration=1.0, travel=0.05, rise=0.01, scale_delta=0.02)])
                )
            )
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main([str(path)])
            self.assertEqual(code, 1)
            self.assertTrue(json.loads(buffer.getvalue())["scaleTripwire"]["tripped"])

    def test_the_cli_exits_0_on_a_clean_payload_and_prints_a_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.json"
            path.write_text(json.dumps(payload_of([table_clip(row) for row in VALIDATION_TABLE])))
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main([str(path)])
            self.assertEqual(code, 0)
            result = json.loads(buffer.getvalue())
            self.assertEqual(result["clipCount"], len(VALIDATION_TABLE))
            self.assertEqual(
                [entry["classification"]["primary"] for entry in result["clips"]],
                [row["primary"] for row in VALIDATION_TABLE],
            )

    def test_the_cli_exits_1_on_an_invalid_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.json"
            path.write_text(json.dumps({"figureHeight": 1.0, "clips": []}))
            buffer = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(buffer), redirect_stderr(errors):
                code = main([str(path)])
            self.assertEqual(code, 1)
            self.assertIn("clips must be a non-empty list", errors.getvalue())
            self.assertEqual(buffer.getvalue(), "")


class TheVocabulary(unittest.TestCase):
    """§1 definitions that a plausible-looking wrong implementation would still pass the table with."""

    def hand_built(self, hands: tuple[list, list], feet: tuple[list, list]) -> ClipFeatures:
        samples = len(hands[0])
        hip = [[0.0, 0.5, 0.0] for _ in range(samples)]
        clip = {
            "sourceName": "hand-built",
            "duration": 1.0,
            "sampleTimes": [index / (samples - 1) for index in range(samples)],
            "landmarkPositions": {
                "hip": hip,
                "head": [[0.0, 0.92, 0.0] for _ in range(samples)],
                "hand.l": hands[0],
                "hand.r": hands[1],
                "foot.l": feet[0],
                "foot.r": feet[1],
            },
            "jointScaleDelta": [0.0] * samples,
            "poseReturn": 0.0,
        }
        return measure(clip)

    def test_hand_and_foot_ranges_pool_both_sides(self) -> None:
        # hand.l spans x in [0.0, 0.2]; hand.r spans x in [-0.3, -0.1]. Per-hand the largest range is
        # 0.2, pooled it is 0.5. §1 says "over both hands", so 0.5 is the answer.
        hand_l = [[0.0, 0.6, 0.0], [0.2, 0.6, 0.0]]
        hand_r = [[-0.1, 0.6, 0.0], [-0.3, 0.6, 0.0]]
        foot_l = [[0.015, 0.0, 0.0], [0.015, 0.0, 0.0]]
        foot_r = [[-0.015, 0.0, 0.0], [-0.015, 0.0, 0.0]]
        features = self.hand_built((hand_l, hand_r), (foot_l, foot_r))
        self.assertAlmostEqual(features.hand_range, 0.5, places=12)
        self.assertAlmostEqual(features.foot_range, 0.03, places=12)

    def test_landmark_ranges_expose_the_per_foot_reading_pooling_hides(self) -> None:
        """Pooling puts stance width inside footRange; the per-landmark ranges let a caller see past it.

        Two feet standing perfectly still 0.12H apart pool to footRange 0.12H and miss `planted`
        despite not moving at all. That is what the vocabulary says, so that is what it returns -- and
        `landmark_ranges` carries the number that shows the feet never moved.
        """
        foot_l = [[0.06, 0.0, 0.0], [0.06, 0.0, 0.0]]
        foot_r = [[-0.06, 0.0, 0.0], [-0.06, 0.0, 0.0]]
        hands = ([[0.0, 0.6, 0.0], [0.0, 0.6, 0.0]], [[0.0, 0.6, 0.0], [0.0, 0.6, 0.0]])
        features = self.hand_built(hands, (foot_l, foot_r))
        self.assertAlmostEqual(features.foot_range, 0.12, places=12)
        self.assertNotIn("planted", classify(features).labels)
        per_foot = max(max(features.landmark_ranges[foot]) for foot in ("foot.l", "foot.r"))
        self.assertEqual(per_foot, 0.0)

    def test_hand_range_is_world_space_so_root_travel_is_inside_it(self) -> None:
        """The pitfall §R4's design targets will walk a caller straight into.

        §R4 quotes handRange 0.15-0.25H as the walk target, meaning the counter-swing. §1 measures
        world positions, so a walk that covers 1.13H measures handRange a little OVER 1.13H: the
        root dominates and the swing barely shows. The two numbers are not the same quantity, and
        reading the §R4 band against this one would fail every walk ever authored. This is why
        `gesture` is gated on `in-place` -- the hand threshold only means what it says once the root
        is known to be still. An R4 check that wants the counter-swing alone must subtract the hip.
        """
        row = VALIDATION_TABLE[0]  # walk-forward, hand_swing 0.10 -> 0.20H of counter-swing
        features = measure(table_clip(row))
        self.assertGreater(features.hand_range, row["travel"])
        self.assertGreater(features.hand_range, 4 * 0.25)  # far outside §R4's 0.15-0.25H band
        # Above the gesture threshold on the number alone, and correctly not a gesture.
        self.assertGreater(features.hand_range, DEFAULT_THRESHOLDS.gesture_hand_range)
        self.assertNotIn("gesture", classify(features).labels)

    def test_travel_is_planar_so_a_vertical_hop_is_not_travel(self) -> None:
        features = measure(synth_clip("hop", duration=1.0, travel=0.0, rise=0.30))
        self.assertEqual(features.travel, 0.0)
        self.assertAlmostEqual(features.rise, 0.30, places=9)
        self.assertEqual(classify(features).primary, "jump")

    def test_travel_counts_the_z_axis_too(self) -> None:
        samples = 5
        hip = [[0.0, 0.5, 0.4 * index / (samples - 1)] for index in range(samples)]
        clip = {
            "sourceName": "sidestep",
            "duration": 1.0,
            "sampleTimes": [index / (samples - 1) for index in range(samples)],
            "landmarkPositions": {
                "hip": hip,
                "head": [[point[0], point[1] + 0.42, point[2]] for point in hip],
                "hand.l": [[0.1, 0.55, point[2]] for point in hip],
                "hand.r": [[-0.1, 0.55, point[2]] for point in hip],
                "foot.l": [[0.015, 0.0, point[2]] for point in hip],
                "foot.r": [[-0.015, 0.0, point[2]] for point in hip],
            },
            "jointScaleDelta": [0.0] * samples,
            "poseReturn": 0.0,
        }
        self.assertAlmostEqual(measure(clip).travel, 0.4, places=12)

    def test_head_rise_is_the_head_not_the_hip(self) -> None:
        features = measure(synth_clip("crouch", duration=1.0, travel=0.0, rise=0.20))
        # In this fixture the head rides the hip exactly, so the two agree -- which is what makes it
        # a usable pin: headRise reading anything else means it sampled the wrong landmark.
        self.assertAlmostEqual(features.head_rise, features.rise, places=12)


class Naming(unittest.TestCase):
    """§3. What a name is allowed to claim."""

    def features_for(self, row_index: int) -> ClipFeatures:
        return measure(table_clip(VALIDATION_TABLE[row_index]))

    def test_a_measured_name_is_not_inferred_and_quotes_its_numbers(self) -> None:
        features = self.features_for(1)  # run-forward
        named = name_clip(features, "run-forward")
        self.assertFalse(named.inferred)
        self.assertEqual(named.id, "run-forward")
        self.assertEqual(named.measured, "speed 0.799 H/s, rise 0.021H")

    def test_an_intent_word_sets_inferred(self) -> None:
        # No kinematic feature distinguishes a strike from a stumble.
        features = self.features_for(6)  # arms-only
        self.assertTrue(name_clip(features, "strike-overhead").inferred)
        self.assertTrue(name_clip(features, "taunt").inferred)
        self.assertFalse(name_clip(features, "arms-only-feet-planted").inferred)

    def test_inferred_can_be_overridden_because_no_word_list_is_complete(self) -> None:
        features = self.features_for(6)
        self.assertTrue(name_clip(features, "reaching-forward", inferred=True).inferred)
        self.assertFalse(name_clip(features, "strike-overhead", inferred=False).inferred)

    def test_source_name_survives_renaming(self) -> None:
        clip = table_clip(VALIDATION_TABLE[0])
        clip["sourceName"] = "NlaTrack.003"
        named = name_clip(measure(clip), "walk-forward")
        self.assertEqual(named.source_name, "NlaTrack.003")
        self.assertEqual(named.label, "walk-forward")

    def test_the_measured_string_mentions_the_labels_that_fired(self) -> None:
        features = self.features_for(6)  # arms-only: in-place + planted + gesture
        named = name_clip(features, "arms-only-feet-planted")
        self.assertIn("footRange", named.measured)
        self.assertIn("handRange", named.measured)

    def test_loop_is_decided_from_the_features_unless_supplied(self) -> None:
        features = measure(
            synth_clip("NlaTrack.008", duration=1.0, travel=0.05, rise=0.01, hip_returns=True, pose_return=0.1)
        )
        self.assertIs(name_clip(features, "sway", loop=AUTO).loop, True)
        self.assertIs(name_clip(features, "sway", loop=False).loop, False)
        undecidable = measure(
            synth_clip("NlaTrack.011", duration=1.0, travel=0.05, rise=0.01, pose_return=None)
        )
        self.assertIsNone(name_clip(undecidable, "sway").loop)


class Thresholding(unittest.TestCase):
    """§2 thresholds are `single-subject` starting values, so they must be replaceable and recorded."""

    def test_classification_records_the_thresholds_it_used(self) -> None:
        features = measure(table_clip(VALIDATION_TABLE[0]))
        self.assertIs(classify(features).thresholds, DEFAULT_THRESHOLDS)
        custom = Thresholds(walk_speed=0.9, provenance="second rig")
        self.assertIs(classify(features, custom).thresholds, custom)

    def test_raising_the_walk_floor_declassifies_the_walk_clip(self) -> None:
        features = measure(table_clip(VALIDATION_TABLE[0]))  # 0.400 H/s
        self.assertEqual(classify(features).primary, "walk")
        self.assertIsNone(classify(features, Thresholds(walk_speed=0.5, run_speed=0.9)).primary)

    def test_a_clip_in_a_gap_gets_no_primary_class(self) -> None:
        """travel 0.40H is too far to be `in-place`, speed 0.20 H/s too slow to be `walk`.

        `primary` is None and `labels` is empty. Nearest-neighbour guessing here would make the
        threshold table stop being evidence -- the gaps are the finding.
        """
        features = measure(synth_clip("gap", duration=2.0, travel=0.40, rise=0.05))
        classification = classify(features)
        self.assertEqual(classification.labels, ())
        self.assertIsNone(classification.primary)


class PayloadValidation(unittest.TestCase):
    """A bad payload must name the clip and the field. Eleven clips and a bare "length mismatch"
    costs an afternoon of bisecting JSON."""

    def test_rejects_a_clip_whose_sample_times_and_positions_disagree(self) -> None:
        clip = synth_clip("NlaTrack.003", duration=1.0, travel=0.05, rise=0.01)
        clip["landmarkPositions"]["hip"] = clip["landmarkPositions"]["hip"][:-1]
        with self.assertRaises(ValueError) as caught:
            load_payload(payload_of([clip]))
        message = str(caught.exception)
        self.assertIn("NlaTrack.003", message)
        self.assertIn("hip", message)
        self.assertIn("25", message)
        self.assertIn("24", message)

    def test_rejects_a_missing_landmark(self) -> None:
        clip = synth_clip("NlaTrack.001", duration=1.0, travel=0.05, rise=0.01)
        del clip["landmarkPositions"]["foot.r"]
        with self.assertRaisesRegex(ValueError, r"NlaTrack\.001.*foot\.r"):
            load_payload(payload_of([clip]))

    def test_rejects_a_missing_joint_scale_delta_rather_than_assuming_zero(self) -> None:
        clip = synth_clip("NlaTrack.001", duration=1.0, travel=0.05, rise=0.01)
        del clip["jointScaleDelta"]
        with self.assertRaisesRegex(ValueError, r"NlaTrack\.001.*jointScaleDelta"):
            load_payload(payload_of([clip]))

    def test_rejects_a_non_finite_coordinate(self) -> None:
        clip = synth_clip("NlaTrack.001", duration=1.0, travel=0.05, rise=0.01)
        clip["landmarkPositions"]["hand.l"][3] = [0.0, float("nan"), 0.0]
        with self.assertRaisesRegex(ValueError, r"NlaTrack\.001.*hand\.l"):
            load_payload(payload_of([clip]))

    def test_rejects_a_zero_or_negative_figure_height(self) -> None:
        clip = synth_clip("NlaTrack.001", duration=1.0, travel=0.05, rise=0.01)
        with self.assertRaisesRegex(ValueError, "figureHeight"):
            load_payload(payload_of([clip], height=0.0))

    def test_rejects_a_zero_duration(self) -> None:
        clip = synth_clip("NlaTrack.001", duration=1.0, travel=0.05, rise=0.01)
        clip["duration"] = 0.0
        with self.assertRaisesRegex(ValueError, r"NlaTrack\.001.*duration"):
            load_payload(payload_of([clip]))

    def test_accepts_a_payload_that_omits_pose_return_entirely(self) -> None:
        clip = synth_clip("NlaTrack.001", duration=1.0, travel=0.05, rise=0.01, pose_return=None)
        loaded = load_payload(payload_of([clip]))
        self.assertIsNone(loaded["clips"][0]["poseReturn"])

    def test_a_path_and_a_dict_take_the_same_route(self) -> None:
        payload = payload_of([table_clip(VALIDATION_TABLE[0])])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.json"
            path.write_text(json.dumps(payload))
            from_path = load_payload(path)
        self.assertEqual(from_path, load_payload(payload))


if __name__ == "__main__":
    unittest.main()
