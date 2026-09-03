#!/usr/bin/env python3
"""The measurement vocabulary, classifier, naming and loop rule for animation clips (1.5.2 §1-§4).

WHY THIS MODULE EXISTS AT ALL. 1.5.1 shipped eleven clips named `NlaTrack`, `NlaTrack.001`, ... and
a loop rule that contradicted its own data. Both failures have the same shape: a claim about a clip
that nobody measured. So there is exactly one feature vector here (§1) and everything else -- the
class, the name, the loop flag -- is a function of it. If a claim cannot be written as a function of
these numbers it is marked `inferred` rather than dressed up as a measurement.

The same vector runs in both directions. Stage R3 measures an unknown clip to identify it; Stage R4
states a target band and measures the clip it authored to check the classifier agrees. Same code,
so the two can never drift apart.

Every length is a fraction of figure height H. Nothing here assumes H == 1.0; the payload carries it.

Pure Python 3.10+ stdlib. No pip installs, no numpy, no three.js.
"""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

# The six landmarks §1 names. Fewer than six and the vector has holes; the module refuses rather
# than reporting a range over the joints that happened to be present.
REQUIRED_LANDMARKS: tuple[str, ...] = ("hip", "head", "hand.l", "hand.r", "foot.l", "foot.r")

# ---------------------------------------------------------------------------------------------
# §2 thresholds. Every one of these is `single-subject`: derived from eleven clips on one rig
# (Lee Sin, H = 1.0), with the boundaries dropped into the empty gaps between observed clusters
# rather than at round numbers. They are starting values, not constants -- confirm the gaps survive
# on a second rig before hardening any of them. `classify(features, thresholds=...)` takes an
# override and the returned Classification records the set that was actually used, so a report can
# always be traced back to the numbers that produced it.
# ---------------------------------------------------------------------------------------------
IDLE_TRAVEL = 0.02              # single-subject: idle-still measured travel 0.013H
IDLE_RISE = 0.02                # single-subject: idle-still measured rise 0.0001H
IN_PLACE_TRAVEL = 0.30          # single-subject: jump-in-place measured travel 0.272H
WALK_SPEED = 0.30               # single-subject: walk-forward measured 0.400 H/s
RUN_SPEED = 0.60               # single-subject: the walk/run gap is 0.400 -> 0.799 H/s
DASH_SPEED = 1.50               # single-subject: the run/dash gap is 0.799 -> 2.250 H/s
JUMP_RISE = 0.15                # single-subject: jump-in-place measured rise 0.248H
LEAP_TRAVEL = 0.50              # single-subject: separates jump-in-place (0.272H) from leap (2.826H)
PLANTED_FOOT_RANGE = 0.10       # single-subject: "feet do not participate"
GESTURE_HAND_RANGE = 0.40       # single-subject: arms-only, and only meaningful while in-place

# §4 loop rule. These two are named in CONTRACT_1.5.2.md and shared with rig_gates.
POSE_RETURN_DEGREES = 0.5
LOOP_HIP_TOLERANCE = 0.01

# §1 tripwire. Lee Sin measured 0.000 on all eleven clips. Raising this above zero is a deliberate
# admission that the source rig scales joints -- which changes what Stage R2 may legally do to skin
# weights -- not a way to quiet the flag.
SCALE_DELTA_TRIPWIRE = 0.0

# Order matters and is the spec's own: jump/leap are decided by rise+travel and outrank the speed
# classes, because a leap that covers 2.8H in 1.4s is genuinely fast but calling it `dash` loses the
# fact that it left the ground. Speed classes outrank idle/in-place, which are the travel-only
# fallbacks.
MOTION_PRECEDENCE: tuple[str, ...] = ("leap", "jump", "dash", "run", "walk", "idle", "in-place")

# §3. Words that describe an INTENTION. No kinematic feature distinguishes a strike from a stumble:
# the hand accelerates, reaches, and returns in both, and the vector above is blind to which one the
# animator meant. A label containing any of these gets `inferred: true` -- not as a placeholder for a
# better classifier, but because it is the honest answer and there is no better classifier coming.
INTENT_WORDS: frozenset[str] = frozenset(
    {
        "attack", "block", "cast", "celebrate", "channel", "charge", "counter", "death", "defeat",
        "defend", "dodge", "emote", "greet", "guard", "hurt", "parry", "provoke", "ready", "recall",
        "salute", "strike", "stumble", "taunt", "threaten", "victory", "wave",
    }
)


@dataclass(frozen=True)
class Thresholds:
    """The §2 table, as data a caller can replace.

    Held together in one object so a report can record the whole set rather than the two numbers
    that happened to fire. A classification carried around without the thresholds that produced it
    is not reproducible.
    """

    idle_travel: float = IDLE_TRAVEL
    idle_rise: float = IDLE_RISE
    in_place_travel: float = IN_PLACE_TRAVEL
    walk_speed: float = WALK_SPEED
    run_speed: float = RUN_SPEED
    dash_speed: float = DASH_SPEED
    jump_rise: float = JUMP_RISE
    leap_travel: float = LEAP_TRAVEL
    planted_foot_range: float = PLANTED_FOOT_RANGE
    gesture_hand_range: float = GESTURE_HAND_RANGE
    provenance: str = "single-subject (11 clips, one rig, H = 1.0)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "idleTravel": self.idle_travel,
            "idleRise": self.idle_rise,
            "inPlaceTravel": self.in_place_travel,
            "walkSpeed": self.walk_speed,
            "runSpeed": self.run_speed,
            "dashSpeed": self.dash_speed,
            "jumpRise": self.jump_rise,
            "leapTravel": self.leap_travel,
            "plantedFootRange": self.planted_foot_range,
            "gestureHandRange": self.gesture_hand_range,
            "provenance": self.provenance,
        }


DEFAULT_THRESHOLDS = Thresholds()


@dataclass(frozen=True)
class ClipFeatures:
    """§1, measured. Every length already divided by H, so these read as fractions of figure height.

    `scales_joints` is not a descriptor sitting alongside the others -- it is the tripwire. A caller
    is expected to look at it BEFORE looking at anything else, because a rig that scales joints
    changes what Stage R2 may legally do to skin weights.

    `pose_return` is degrees and may be None: a host that cannot measure per-joint rotation deltas
    omits it, and `decide_loop` then reports `loop: None`. It is never filled in with a zero.

    `landmark_ranges` is the per-landmark, per-axis range (normalised), kept because `hand_range` and
    `foot_range` POOL both sides -- see `measure_clip` for why that matters and what it costs.
    """

    source_name: str
    figure_height: float
    sample_count: int
    duration: float
    travel: float
    rise: float
    speed: float
    hand_range: float
    foot_range: float
    head_rise: float
    scale_delta: float
    scales_joints: bool
    pose_return: float | None
    hip_return: float
    landmark_ranges: dict[str, tuple[float, float, float]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceName": self.source_name,
            "figureHeight": self.figure_height,
            "sampleCount": self.sample_count,
            "duration": self.duration,
            "travel": self.travel,
            "rise": self.rise,
            "speed": self.speed,
            "handRange": self.hand_range,
            "footRange": self.foot_range,
            "headRise": self.head_rise,
            "scaleDelta": self.scale_delta,
            "scalesJoints": self.scales_joints,
            "poseReturn": self.pose_return,
            "hipReturn": self.hip_return,
            "landmarkRanges": {name: list(axes) for name, axes in self.landmark_ranges.items()},
        }


@dataclass(frozen=True)
class Classification:
    """Every label that matched, plus one primary motion class -- or None.

    `labels` is a set, not a winner. A clip really can be `in-place` AND `planted` AND `gesture` at
    once; collapsing that to one string throws away the two facts a reader most wants (the feet are
    out of it, the hands are doing the work). `primary` exists only because a UI needs one word.

    `primary` is None when nothing in the table matched. There is no `unknown` class and no nearest
    neighbour: inventing a label for a clip that fell in a gap is how a threshold table stops being
    evidence.
    """

    labels: tuple[str, ...]
    primary: str | None
    reasons: dict[str, str]
    thresholds: Thresholds

    def to_dict(self) -> dict[str, Any]:
        return {
            "labels": list(self.labels),
            "primary": self.primary,
            "reasons": dict(self.reasons),
            "thresholds": self.thresholds.to_dict(),
        }


@dataclass(frozen=True)
class LoopDecision:
    """§4. `loop` is tri-state: True, False, or None for "nobody measured poseReturn"."""

    loop: bool | None
    reason: str
    pose_return: float | None
    hip_return: float
    pose_return_limit: float
    hip_tolerance: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "loop": self.loop,
            "reason": self.reason,
            "poseReturn": self.pose_return,
            "hipReturn": self.hip_return,
            "poseReturnLimit": self.pose_return_limit,
            "hipTolerance": self.hip_tolerance,
        }


@dataclass(frozen=True)
class ClipName:
    """§3, mirroring the TS interface. `source_name` is never discarded.

    Dropping the source name breaks the parity chain back to one source animation -- the chain that
    lets Gate R3 prove the renamed clip is byte-for-byte the clip that arrived. A friendly label is
    worth nothing if it cannot be traced to the strip it came from.
    """

    source_name: str
    id: str
    label: str
    measured: str
    inferred: bool
    loop: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceName": self.source_name,
            "id": self.id,
            "label": self.label,
            "measured": self.measured,
            "inferred": self.inferred,
            "loop": self.loop,
        }


class _Auto:
    """Sentinel: `loop=None` and `inferred=None` are meaningful values, so absence needs its own."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "<auto>"


AUTO = _Auto()


# ---------------------------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------------------------


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _vec3(value: Any, label: str, clip_name: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3 or not all(_finite(item) for item in value):
        raise ValueError(f"clip {clip_name!r}: {label} must be a finite [x, y, z]; got {value!r}")
    return float(value[0]), float(value[1]), float(value[2])


def _validate_clip(
    clip: Any,
    figure_height: float,
    landmarks: Sequence[str] = REQUIRED_LANDMARKS,
) -> dict[str, Any]:
    """Check one clip against the CONTRACT_1.5.2 payload shape, or raise naming clip and field.

    The error message always carries the clip's own name. A payload with eleven clips that reports
    "sampleTimes length mismatch" and nothing else costs an afternoon of bisecting a JSON file.
    """
    if not isinstance(clip, Mapping):
        raise ValueError(f"clip must be an object; got {type(clip).__name__}")
    name = clip.get("sourceName")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("clip.sourceName must be a non-empty string (it is never discarded, so it must exist)")

    if not _finite(figure_height) or float(figure_height) <= 0.0:
        raise ValueError(f"clip {name!r}: figureHeight must be a finite positive number; got {figure_height!r}")

    duration = clip.get("duration")
    if not _finite(duration) or float(duration) <= 0.0:
        raise ValueError(f"clip {name!r}: duration must be a finite positive number of seconds; got {duration!r}")

    times = clip.get("sampleTimes")
    if not isinstance(times, (list, tuple)) or len(times) < 2:
        raise ValueError(f"clip {name!r}: sampleTimes must be a list of at least 2 times")
    if not all(_finite(t) for t in times):
        raise ValueError(f"clip {name!r}: sampleTimes must contain only finite numbers")
    if any(float(times[i + 1]) < float(times[i]) for i in range(len(times) - 1)):
        raise ValueError(f"clip {name!r}: sampleTimes must be non-decreasing")
    count = len(times)

    positions = clip.get("landmarkPositions")
    if not isinstance(positions, Mapping):
        raise ValueError(f"clip {name!r}: landmarkPositions must be an object keyed by landmark name")
    resolved: dict[str, list[tuple[float, float, float]]] = {}
    for landmark in landmarks:
        track = positions.get(landmark)
        if track is None:
            raise ValueError(
                f"clip {name!r}: landmarkPositions is missing required landmark {landmark!r} "
                f"(§1 needs all of {', '.join(landmarks)})"
            )
        if not isinstance(track, (list, tuple)):
            raise ValueError(f"clip {name!r}: landmarkPositions[{landmark!r}] must be a list of [x, y, z]")
        if len(track) != count:
            raise ValueError(
                f"clip {name!r}: sampleTimes has {count} entries but "
                f"landmarkPositions[{landmark!r}] has {len(track)}; every track must be sampled at "
                f"the same times"
            )
        resolved[landmark] = [_vec3(point, f"landmarkPositions[{landmark!r}][{i}]", name) for i, point in enumerate(track)]

    scale_delta = clip.get("jointScaleDelta")
    if not isinstance(scale_delta, (list, tuple)):
        raise ValueError(
            f"clip {name!r}: jointScaleDelta must be a list with one entry per sample "
            f"(it is the §1 tripwire; an absent one cannot be assumed to be zero)"
        )
    if len(scale_delta) != count:
        raise ValueError(
            f"clip {name!r}: sampleTimes has {count} entries but jointScaleDelta has {len(scale_delta)}"
        )
    if not all(_finite(value) for value in scale_delta):
        raise ValueError(f"clip {name!r}: jointScaleDelta must contain only finite numbers")

    pose_return = clip.get("poseReturn")
    if pose_return is not None and not _finite(pose_return):
        raise ValueError(
            f"clip {name!r}: poseReturn must be a finite number of degrees, or omitted entirely "
            f"if the host cannot measure it; got {pose_return!r}"
        )

    stance = clip.get("stance")
    if stance is not None:
        if not isinstance(stance, Mapping):
            raise ValueError(f"clip {name!r}: stance must be an object keyed by foot landmark")
        for foot, intervals in stance.items():
            if not isinstance(intervals, (list, tuple)):
                raise ValueError(f"clip {name!r}: stance[{foot!r}] must be a list of [t0, t1] intervals")
            for index, interval in enumerate(intervals):
                if (
                    not isinstance(interval, (list, tuple))
                    or len(interval) != 2
                    or not all(_finite(t) for t in interval)
                    or float(interval[0]) > float(interval[1])
                ):
                    raise ValueError(
                        f"clip {name!r}: stance[{foot!r}][{index}] must be [t0, t1] with finite t0 <= t1"
                    )

    return {
        "sourceName": name,
        "duration": float(duration),
        "sampleTimes": [float(t) for t in times],
        "landmarkPositions": resolved,
        "jointScaleDelta": [float(value) for value in scale_delta],
        "poseReturn": None if pose_return is None else float(pose_return),
        "stance": stance,
    }


def load_payload(path_or_dict: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    """Read and validate a sampled-clip payload (CONTRACT_1.5.2.md), or raise a ValueError that names
    the offending clip and field.

    Accepts a path or an already-parsed mapping so a test fixture and a file take the same route --
    a validator that only runs on the file path is a validator half the callers skip.
    """
    if isinstance(path_or_dict, Mapping):
        payload: Any = path_or_dict
        origin = "<dict>"
    else:
        path = Path(path_or_dict).expanduser()
        origin = str(path)
        payload = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(payload, Mapping):
        raise ValueError(f"{origin}: payload root must be an object")

    figure_height = payload.get("figureHeight")
    if not _finite(figure_height) or float(figure_height) <= 0.0:
        raise ValueError(f"{origin}: figureHeight must be a finite positive number; got {figure_height!r}")

    landmarks = payload.get("landmarks", list(REQUIRED_LANDMARKS))
    if not isinstance(landmarks, (list, tuple)) or not all(isinstance(item, str) for item in landmarks):
        raise ValueError(f"{origin}: landmarks must be a list of strings")
    missing = [name for name in REQUIRED_LANDMARKS if name not in landmarks]
    if missing:
        raise ValueError(
            f"{origin}: landmarks is missing {', '.join(missing)}; §1 measures all six of "
            f"{', '.join(REQUIRED_LANDMARKS)}"
        )

    clips = payload.get("clips")
    if not isinstance(clips, (list, tuple)) or not clips:
        raise ValueError(f"{origin}: clips must be a non-empty list")

    validated = [_validate_clip(clip, float(figure_height)) for clip in clips]
    return {
        "figureHeight": float(figure_height),
        "landmarks": list(landmarks),
        "clips": validated,
    }


# ---------------------------------------------------------------------------------------------
# §1 measurement
# ---------------------------------------------------------------------------------------------


def _axis_ranges(tracks: Sequence[Sequence[tuple[float, float, float]]]) -> tuple[float, float, float]:
    """Per-axis (max - min) over every sample of every supplied track, pooled."""
    ranges: list[float] = []
    for axis in range(3):
        values = [point[axis] for track in tracks for point in track]
        ranges.append(max(values) - min(values))
    return ranges[0], ranges[1], ranges[2]


def measure_clip(clip: Mapping[str, Any], figure_height: float) -> ClipFeatures:
    """Measure the §1 feature vector for one sampled clip.

    Definitions, verbatim from §1 and then normalised by H:
      travel   max_t ||hip_xz(t) - hip_xz(0)||   -- planar, so a jump does not read as travel
      rise     max_t hip_y - min_t hip_y
      speed    travel / duration, in H per second
      handRange / footRange   largest per-axis range over BOTH hands / BOTH feet
      headRise vertical range of the head
      scaleDelta  max over samples of the payload's per-sample max |scale_j - 1|

    TWO PROPERTIES OF handRange / footRange THAT WILL BITE A CALLER WHO ASSUMES OTHERWISE:

    1. They are measured in WORLD space, so root travel is inside them. A walk that covers 1.13H
       measures handRange ~1.13H, not the 0.15-0.25H counter-swing band §R4 quotes as a design
       target. That is why `gesture` is gated on `in-place`: the hand-range threshold only means
       what it says once the root is known to be still. An R4 authoring check that wants the
       counter-swing alone must subtract the hip, and should say so.

    2. They POOL both sides before taking the range, as the vocabulary states. Pooling puts the
       stance width inside footRange -- two feet standing perfectly still 0.12H apart measure
       footRange 0.12H and miss the `planted` threshold despite not moving at all. `landmark_ranges`
       carries each landmark's own per-axis range so a caller that wants the per-foot reading
       (max over feet of that foot's own largest axis range) can take it without re-sampling.
    """
    validated = _validate_clip(clip, figure_height)
    height = float(figure_height)
    positions = validated["landmarkPositions"]
    hip = positions["hip"]

    hip_start = hip[0]
    travel = max(math.hypot(point[0] - hip_start[0], point[2] - hip_start[2]) for point in hip) / height
    rise = (max(point[1] for point in hip) - min(point[1] for point in hip)) / height
    duration = validated["duration"]
    speed = travel / duration

    landmark_ranges = {
        name: tuple(value / height for value in _axis_ranges([track]))
        for name, track in positions.items()
    }
    hand_range = max(_axis_ranges([positions["hand.l"], positions["hand.r"]])) / height
    foot_range = max(_axis_ranges([positions["foot.l"], positions["foot.r"]])) / height
    head_rise = landmark_ranges["head"][1]

    scale_delta = max(validated["jointScaleDelta"]) if validated["jointScaleDelta"] else 0.0
    hip_end = hip[-1]
    hip_return = math.dist(hip_end, hip_start) / height

    return ClipFeatures(
        source_name=validated["sourceName"],
        figure_height=height,
        sample_count=len(validated["sampleTimes"]),
        duration=duration,
        travel=travel,
        rise=rise,
        speed=speed,
        hand_range=hand_range,
        foot_range=foot_range,
        head_rise=head_rise,
        scale_delta=scale_delta,
        scales_joints=scale_delta > SCALE_DELTA_TRIPWIRE,
        pose_return=validated["poseReturn"],
        hip_return=hip_return,
        landmark_ranges=landmark_ranges,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------------------------
# §2 classification
# ---------------------------------------------------------------------------------------------


def classify(features: ClipFeatures, thresholds: Thresholds | None = None) -> Classification:
    """Apply the §2 table and return EVERY label that matched, plus one primary motion class.

    Labels are not mutually exclusive and are not ranked: `arms-only` is `in-place` and `planted` and
    `gesture`, all three measured, all three true. `primary` picks one motion class out of
    idle/walk/run/dash/jump/leap/in-place by MOTION_PRECEDENCE for callers that need a single word,
    and is None when the clip fell in a gap between clusters. A gap is a finding, not a bug to be
    smoothed over with a nearest-neighbour guess.
    """
    limits = thresholds or DEFAULT_THRESHOLDS
    labels: list[str] = []
    reasons: dict[str, str] = {}

    def hit(label: str, reason: str) -> None:
        labels.append(label)
        reasons[label] = reason

    if features.travel < limits.idle_travel and features.rise < limits.idle_rise:
        hit("idle", f"travel {features.travel:.4f} < {limits.idle_travel} and rise {features.rise:.4f} < {limits.idle_rise}")
    in_place = features.travel < limits.in_place_travel
    if in_place:
        hit("in-place", f"travel {features.travel:.4f} < {limits.in_place_travel}")
    if limits.walk_speed <= features.speed < limits.run_speed:
        hit("walk", f"{limits.walk_speed} <= speed {features.speed:.4f} < {limits.run_speed}")
    if limits.run_speed <= features.speed < limits.dash_speed:
        hit("run", f"{limits.run_speed} <= speed {features.speed:.4f} < {limits.dash_speed}")
    if features.speed >= limits.dash_speed:
        hit("dash", f"speed {features.speed:.4f} >= {limits.dash_speed}")
    if features.rise >= limits.jump_rise and features.travel < limits.leap_travel:
        hit("jump", f"rise {features.rise:.4f} >= {limits.jump_rise} and travel {features.travel:.4f} < {limits.leap_travel}")
    if features.rise >= limits.jump_rise and features.travel >= limits.leap_travel:
        hit("leap", f"rise {features.rise:.4f} >= {limits.jump_rise} and travel {features.travel:.4f} >= {limits.leap_travel}")
    if features.foot_range < limits.planted_foot_range:
        hit("planted", f"footRange {features.foot_range:.4f} < {limits.planted_foot_range}")
    if in_place and features.hand_range >= limits.gesture_hand_range:
        hit("gesture", f"handRange {features.hand_range:.4f} >= {limits.gesture_hand_range} while in-place")

    primary = next((label for label in MOTION_PRECEDENCE if label in labels), None)
    return Classification(
        labels=tuple(labels),
        primary=primary,
        reasons=reasons,
        thresholds=limits,
    )


# ---------------------------------------------------------------------------------------------
# §4 the loop rule -- corrected
# ---------------------------------------------------------------------------------------------


def decide_loop(
    features: ClipFeatures,
    pose_return_limit: float = POSE_RETURN_DEGREES,
    hip_tolerance: float = LOOP_HIP_TOLERANCE,
) -> LoopDecision:
    """Decide whether a clip can repeat seamlessly. glTF carries no loop flag, so someone must.

        loop  <=>  poseReturn <= 0.5 deg per rotation joint  AND  ||hip(T) - hip(0)|| <= 0.01H

    WHY NOT THE 1.5.1 RULE. 1.5.1 used "a clip that neither travels nor rises can repeat seamlessly",
    and that rule contradicted the very data it was derived from: `idle-gesture` measured
    travel = 0.121H -- six times the idle threshold -- and was correctly marked loopable. What makes
    a clip loop is not that the root stays put but that THE LAST POSE RETURNS TO THE FIRST. Measured
    on the joint transforms, the corrected rule accepts a clip that wanders and comes back and
    rejects one that ends mid-stride a centimetre from where it started. `travel` and `rise` remain
    descriptors; they stop being the loop criterion.

    UNDECIDABLE IS A THIRD ANSWER. A host that cannot measure per-joint rotation deltas omits
    `poseReturn`, and this returns `loop=None`. Defaulting to False would look conservative and be
    a lie: it would silently mark every loopable clip as one-shot on any host without the
    measurement, and nothing downstream could tell the difference between "measured, does not loop"
    and "nobody looked".
    """
    if features.pose_return is None:
        return LoopDecision(
            loop=None,
            reason="poseReturn not measured",
            pose_return=None,
            hip_return=features.hip_return,
            pose_return_limit=pose_return_limit,
            hip_tolerance=hip_tolerance,
        )

    pose_ok = features.pose_return <= pose_return_limit
    hip_ok = features.hip_return <= hip_tolerance
    if pose_ok and hip_ok:
        reason = (
            f"poseReturn {features.pose_return:.3f} deg <= {pose_return_limit} and "
            f"||hip(T) - hip(0)|| {features.hip_return:.4f}H <= {hip_tolerance}H"
        )
        return LoopDecision(True, reason, features.pose_return, features.hip_return, pose_return_limit, hip_tolerance)

    failures = []
    if not pose_ok:
        failures.append(f"poseReturn {features.pose_return:.3f} deg > {pose_return_limit}")
    if not hip_ok:
        failures.append(f"||hip(T) - hip(0)|| {features.hip_return:.4f}H > {hip_tolerance}H")
    return LoopDecision(False, "; ".join(failures), features.pose_return, features.hip_return, pose_return_limit, hip_tolerance)


def _legacy_loop_rule_1_5_1(features: ClipFeatures, thresholds: Thresholds | None = None) -> bool:
    """The rule §4 replaced: "a clip that neither travels nor rises can repeat seamlessly".

    Kept ONLY so a test can execute the contradiction rather than describe it. `idle-gesture` travels
    0.121H and loops; this function returns False for it and `decide_loop` returns True. Never call
    this from production code -- it is a regression pin, not an alternative.
    """
    limits = thresholds or DEFAULT_THRESHOLDS
    return features.travel < limits.idle_travel and features.rise < limits.idle_rise


# ---------------------------------------------------------------------------------------------
# §3 naming
# ---------------------------------------------------------------------------------------------

_MEASURED_BY_CLASS: dict[str, tuple[str, ...]] = {
    "walk": ("speed", "rise"),
    "run": ("speed", "rise"),
    "dash": ("speed", "travel"),
    "jump": ("rise", "travel"),
    "leap": ("rise", "travel"),
    "idle": ("travel", "rise"),
    "in-place": ("travel", "rise"),
}

_MEASURED_TEXT = {
    "speed": lambda f: f"speed {f.speed:.3f} H/s",
    "travel": lambda f: f"travel {f.travel:.3f}H",
    "rise": lambda f: f"rise {f.rise:.3f}H",
    "handRange": lambda f: f"handRange {f.hand_range:.3f}H",
    "footRange": lambda f: f"footRange {f.foot_range:.3f}H",
    "duration": lambda f: f"duration {f.duration:.3f}s",
}


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "clip"


def label_implies_intent(label: str, intent_words: frozenset[str] = INTENT_WORDS) -> bool:
    """True when any word of the label names an intention rather than a movement."""
    return any(word in intent_words for word in re.split(r"[^a-z0-9]+", label.lower()) if word)


def _measured_summary(features: ClipFeatures, classification: Classification) -> str:
    keys = list(_MEASURED_BY_CLASS.get(classification.primary or "", ("travel", "rise", "speed")))
    for label, key in (("planted", "footRange"), ("gesture", "handRange")):
        if label in classification.labels and key not in keys:
            keys.append(key)
    return ", ".join(_MEASURED_TEXT[key](features) for key in keys)


def name_clip(
    features: ClipFeatures,
    label: str,
    clip_id: str | None = None,
    inferred: bool | _Auto = AUTO,
    loop: bool | None | _Auto = AUTO,
    source_name: str | None = None,
    classification: Classification | None = None,
    intent_words: frozenset[str] = INTENT_WORDS,
) -> ClipName:
    """Rename a clip from its measurements and record WHICH measurement the name rests on.

    Source clips arrive as `NlaTrack`, `NlaTrack.001`, ... -- Blender NLA strip names carrying no
    information. 1.5.1 shipped those verbatim as eleven unusable buttons. Renaming is therefore
    required, and the two rules that make it honest are:

    - `source_name` survives verbatim, so the parity chain back to the source animation is intact.
    - `inferred` is True whenever the label's wording implies intent. Detected from INTENT_WORDS by
      default and overridable, because a label can imply intent with words no list contains. Two of
      the eleven source clips could only be named by inference; marking them cost nothing and kept
      the distinction between measured and guessed visible to whoever read it next.

    `measured` is the short human-readable string of the numbers behind the name -- e.g.
    "speed 0.799 H/s, rise 0.021H". It is chosen from the classification, not from the label text,
    so a hand-written label cannot change which numbers get quoted underneath it.
    """
    resolved_classification = classification or classify(features)
    resolved_loop = decide_loop(features).loop if isinstance(loop, _Auto) else loop
    resolved_inferred = (
        label_implies_intent(label, intent_words) if isinstance(inferred, _Auto) else bool(inferred)
    )
    return ClipName(
        source_name=source_name if source_name is not None else features.source_name,
        id=clip_id or _slug(label),
        label=label,
        measured=_measured_summary(features, resolved_classification),
        inferred=resolved_inferred,
        loop=resolved_loop,
    )


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def report(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Measure, classify, name and loop-decide every clip in an already-validated payload."""
    height = float(payload["figureHeight"])
    entries: list[dict[str, Any]] = []
    tripped: list[str] = []
    for clip in payload["clips"]:
        features = measure_clip(clip, height)
        classification = classify(features)
        loop = decide_loop(features)
        label = classification.primary or features.source_name
        name = name_clip(features, label, classification=classification, loop=loop.loop)
        if features.scales_joints:
            tripped.append(features.source_name)
        entries.append(
            {
                "sourceName": features.source_name,
                "features": features.to_dict(),
                "classification": classification.to_dict(),
                "loop": loop.to_dict(),
                "name": name.to_dict(),
            }
        )
    return {
        "schemaVersion": 1,
        "kind": "clip-features",
        "figureHeight": height,
        "clipCount": len(entries),
        "scaleTripwire": {
            "limit": SCALE_DELTA_TRIPWIRE,
            "tripped": bool(tripped),
            "clips": tripped,
            "note": (
                "A non-zero scaleDelta means the source rig scales joints, which changes what "
                "Stage R2 may legally do to skin weights. Surface it before anything else proceeds."
            ),
        },
        "clips": entries,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] in {"-h", "--help"}:
        print("usage: clip_features.py <payload.json>", file=sys.stderr)
        return 2
    try:
        payload = load_payload(argv[0])
        result = report(payload)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["scaleTripwire"]["tripped"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
