#!/usr/bin/env python3
"""Left and right, in one place, with the two ways of getting it wrong.

WHY THIS MODULE EXISTS. Two chirality defects shipped in the same figure and neither was caught by
any gate, because both produce geometry that is internally tidy and only wrong with respect to a
convention nothing had written down as code.

    THE HAND     `place()` built the mirrored limb as `[side*along, height, side*across]`, negating
                 x AND z. Two negations is not a reflection -- it is a 180-degree ROTATION about Y,
                 and a rotation PRESERVES handedness. The left hand was the right hand turned
                 around. Measured on the thumb tip: z +0.288 on one side and -0.288 on the other,
                 where a mirror leaves z alone.

    THE FOOT     The pair WAS a correct reflection, so any pair test passes. But the toes were
                 ordered little-to-big across a knuckle strip whose index 0 lands on the medial
                 edge, so the big toe went lateral -- on both feet. Measured in the render's toe
                 band: mass 350 medial / 443 lateral, against a reference of 529 / 488. A foot with
                 its big toe outside IS the other foot, so the pair read as swapped.

They need DIFFERENT tests, and that is the point of this module:

    check_pair()            catches the hand. Compares a left component against its right partner.
    medial_lateral_bias()   catches the foot. Compares one limb's own asymmetry against a reference,
                            because a pair that is wrong the SAME way on both sides is still a
                            perfectly good mirror of itself.

THE CONVENTION, stated once so nothing can quietly diverge from it. `new_sculpt_spec.py` carries it
as a comment -- "'left' is the CHARACTER's left, which the component tree spells `-l`. On a
front-facing reference that is the viewer's right." -- and a comment cannot be imported. With
`forward: +Z`, Y up and a right-handed frame, the camera in front looks along -Z and its right is
+X, so the character's own left is +X.

Pure Python 3.10+ standard library.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, Sequence

Point = Sequence[float]

# Index of the left-right axis in an (x, y, z) triple. The ONLY axis a sagittal mirror negates.
LATERAL_AXIS = 0
# Sign of the character's own left, given coordinateFrame up=+Y forward=+Z and a right-handed
# system. Derived, not chosen: the camera that sees the front looks along -Z, its right is +X, and
# a figure facing the camera has its own left on the viewer's right.
CHARACTER_LEFT_SIGN = 1

# Suffixes that mark a component as one half of a lateral pair.
LEFT_SUFFIX = "-l"
RIGHT_SUFFIX = "-r"
_PAIR_RE = re.compile(r"^(?P<stem>.+)-(?P<side>[lr])$")

# How far apart two mirrored coordinates may sit before the pair is called broken, in world units.
# Not a tuning knob: mirrored values are produced by negating the same authored number, so they
# agree to floating-point noise or they disagree structurally. Anything between those is itself a
# defect worth seeing.
MIRROR_TOLERANCE = 1e-6


def mirror_point(point: Point) -> tuple[float, float, float]:
    """The sagittal mirror of a position: negate the lateral axis, leave the rest alone."""
    values = [float(v) for v in point]
    if len(values) != 3:
        raise ValueError(f"a point needs three components, got {len(values)}")
    values[LATERAL_AXIS] = -values[LATERAL_AXIS]
    return (values[0], values[1], values[2])


def mirror_vector(vector: Point) -> tuple[float, float, float]:
    """The sagittal mirror of a DIRECTION. Same rule as a point.

    Kept as its own name because the reflex is to think a direction transforms differently, and
    reaching for a rotation here is exactly the recorded bug.
    """
    return mirror_point(vector)


def side_of(component_id: str) -> str | None:
    """'l', 'r', or None for a component that is not one half of a pair."""
    match = _PAIR_RE.match(str(component_id))
    return match.group("side") if match else None


def pair_stem(component_id: str) -> str | None:
    match = _PAIR_RE.match(str(component_id))
    return match.group("stem") if match else None


def find_pairs(component_ids: Iterable[str]) -> list[tuple[str, str]]:
    """Every `(right_id, left_id)` both halves of which are present. Right first, because the
    convention is stated as 'the left is the mirror of the right'."""
    by_stem: dict[str, dict[str, str]] = {}
    for component_id in component_ids:
        stem = pair_stem(component_id)
        side = side_of(component_id)
        if stem and side:
            by_stem.setdefault(stem, {})[side] = str(component_id)
    return [
        (sides["r"], sides["l"])
        for _stem, sides in sorted(by_stem.items())
        if "l" in sides and "r" in sides
    ]


def classify_relation(right: Point, left: Point) -> str:
    """How the two halves of a pair are actually related. One of:

        'reflection'   the left is the sagittal mirror of the right. Correct.
        'rotation'     the left is the right ROTATED about the vertical axis, not mirrored. This is
                       the recorded hand defect and the reason this function names it rather than
                       just saying 'mismatch': the two are trivially confused, they agree exactly on
                       a symmetric part, and they differ only in handedness.
        'translation'  the left is the right moved, not transformed at all.
        'unrelated'    none of the above.
    """
    r = [float(v) for v in right]
    l = [float(v) for v in left]
    if len(r) != 3 or len(l) != 3:
        raise ValueError("both points need three components")

    def close(a: Sequence[float], b: Sequence[float]) -> bool:
        return all(abs(x - y) <= MIRROR_TOLERANCE for x, y in zip(a, b))

    if close(mirror_point(r), l):
        return "reflection"
    # 180 degrees about Y negates x and z, leaves y. Indistinguishable from a reflection whenever
    # the part sits on the midline in z, which is why a symmetric torso never exposed this.
    if close((-r[0], r[1], -r[2]), l):
        return "rotation"
    if close((r[0], r[1], r[2]), l):
        return "translation"
    return "unrelated"


def check_pair(
    stem: str,
    right: Point,
    left: Point,
) -> tuple[bool, str]:
    """`(ok, message)` for one lateral pair. `ok` is True only for a true reflection."""
    relation = classify_relation(right, left)
    if relation == "reflection":
        return (True, "")
    expected = mirror_point(right)
    if relation == "rotation":
        return (False, (
            f"{stem}: the left half is the right half ROTATED about the vertical axis, not "
            f"mirrored. A rotation preserves handedness, so both halves are the same hand. "
            f"right {tuple(round(float(v), 6) for v in right)} should mirror to "
            f"{tuple(round(v, 6) for v in expected)}, but the left is "
            f"{tuple(round(float(v), 6) for v in left)}. Negate the lateral axis only."
        ))
    if relation == "translation":
        return (False, (
            f"{stem}: both halves sit on the same side -- the left is the right translated, not "
            f"mirrored at all. Expected {tuple(round(v, 6) for v in expected)}."
        ))
    return (False, (
        f"{stem}: the halves are not a sagittal mirror. right "
        f"{tuple(round(float(v), 6) for v in right)} mirrors to "
        f"{tuple(round(v, 6) for v in expected)}, but the left is "
        f"{tuple(round(float(v), 6) for v in left)}."
    ))


def medial_lateral_bias(
    samples: Sequence[tuple[float, float]],
    midline: float = 0.0,
) -> dict[str, Any]:
    """Which half of a limb carries more of it: the half toward the body, or the half away.

    `samples` is `(lateral_coordinate, weight)`. The limb's own midline is taken from its extent,
    and `midline` is the BODY's centreline, which decides which of its halves is medial.

    THIS IS THE TEST A PAIR CHECK CANNOT REPLACE. Two limbs can be perfect mirrors of each other and
    both be the wrong hand. The foot defect was exactly that: `check_pair` passes, and only
    comparing one foot's own internal asymmetry against a reference shows the big toe on the wrong
    edge.
    """
    if not samples:
        return {"medial": 0.0, "lateral": 0.0, "bias": 0.0, "heavier": "none", "sampleCount": 0}
    coords = [float(c) for c, _ in samples]
    low, high = min(coords), max(coords)
    limb_centre = (low + high) / 2.0
    # Medial means nearer the body centreline. Which direction that is depends on which side of the
    # body this limb is on, so it is derived from the limb's own position rather than assumed.
    toward_body = -1.0 if limb_centre > midline else 1.0

    medial = lateral = 0.0
    for coordinate, weight in samples:
        offset = (float(coordinate) - limb_centre) * toward_body
        if offset > 0:
            medial += float(weight)
        elif offset < 0:
            lateral += float(weight)
    total = medial + lateral
    bias = (medial - lateral) / total if total else 0.0
    return {
        "medial": round(medial, 6),
        "lateral": round(lateral, 6),
        "bias": round(bias, 6),
        "heavier": "medial" if bias > 0 else ("lateral" if bias < 0 else "even"),
        "sampleCount": len(samples),
        "limbCentre": round(limb_centre, 6),
    }


# Below this the reference is treated as too symmetric to judge handedness from.
#
# CALIBRATED, and the first guess was not. 0.05 was picked by eye and it would have made the gate
# blind to the exact defect it was written for: the reference feet measure +0.0403 and +0.0579, so a
# 0.05 floor calls one of them unjudgeable. Measured endpoints, toe band, front view:
#
#   reference, left foot    +0.0403   medial-heavy, as a real foot is
#   reference, right foot   +0.0579   same sign, which is what makes the weak signal trustworthy
#   the defect              -0.1173   opposite sign, and three times the magnitude
#   after the fix           +0.0867
#
# 0.025 sits below the weaker reference reading with 38% margin and nowhere near the defect.
MIN_REFERENCE_BIAS = 0.025


def compare_bias(
    reference: dict[str, Any],
    candidate: dict[str, Any],
    label: str,
    tolerance: float = MIN_REFERENCE_BIAS,
) -> tuple[bool, str]:
    """`(ok, message)`. Fails when the candidate's asymmetry points the OPPOSITE way to the
    reference's, which is the signature of a limb built with the wrong handedness.

    A difference in MAGNITUDE is not a chirality defect -- it is a proportion difference, and other
    gates own that. Only the sign is judged here, and only when the reference's own bias is large
    enough to mean something.
    """
    reference_bias = float(reference.get("bias", 0.0))
    candidate_bias = float(candidate.get("bias", 0.0))
    if abs(reference_bias) < tolerance:
        return (True, (
            f"{label}: the reference is close to symmetric here (bias {reference_bias:+.3f}), so "
            f"handedness cannot be judged from it"
        ))
    if reference_bias * candidate_bias > 0:
        return (True, "")
    return (False, (
        f"{label}: mass sits on the {candidate.get('heavier')} side while the reference has it "
        f"{reference.get('heavier')} (bias {candidate_bias:+.3f} against {reference_bias:+.3f}). "
        f"A limb whose internal asymmetry points the wrong way is the OTHER limb -- check the order "
        f"of anything laid out across it, not its position."
    ))


def sagittal_symmetry_error(points: Sequence[Point]) -> float:
    """RMS distance from each point to the nearest mirror of another point.

    A whole-figure sanity number: near zero for a symmetric figure, and large for one where a
    lateral pair has drifted. Reported rather than gated, because deliberate asymmetry -- a
    side-swept fringe, a hand on a hip -- is legitimate and common.
    """
    if not points:
        return 0.0
    mirrored = [mirror_point(p) for p in points]
    total = 0.0
    for point in points:
        best = min(
            (p[0] - point[0]) ** 2 + (p[1] - point[1]) ** 2 + (p[2] - point[2]) ** 2
            for p in mirrored
        )
        total += best
    return math.sqrt(total / len(points))
