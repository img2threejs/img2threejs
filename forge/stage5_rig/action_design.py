#!/usr/bin/env python3
"""Stage R4 — action design: target bands, gait primitives, chain resolution, footSlide.

Spec: `docs/pipelines/character-rigging-animation-1.5.2.md`, Stage R4, read against §1
(the measurement vocabulary) and §2 (the classifier), because R4's acceptance test IS the
§2 classifier: a designed clip is accepted only when the classifier — which is never told
what the clip was meant to be — independently reports the intended class.

    target features  ->  author tracks  ->  measure with §1  ->  compare  ->  iterate

**"Looks right" is not a criterion.** Nothing in this module accepts a clip on the strength
of an eyeball. Every criterion in `Acceptance` is a number compared against a band, and a
criterion whose input is missing reports `unevaluated` with a reason — never a silent pass.

Module boundary: `forge/stage5_rig/CONTRACT_1.5.2.md`. This file owns §R4 targets,
primitives, chain resolution and footSlide. It does NOT own the feature vocabulary or the
classifier — those live in `clip_features.py` and are imported defensively below.

Everything is a fraction of figure height H. No function here assumes `H == 1.0`.

Pure Python 3.10+ stdlib. No pip installs, no three.js, no renderer.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Mapping, Optional, Sequence

# --------------------------------------------------------------------------------------
# Sibling module, imported defensively.
#
# `clip_features.py` (§1 vocabulary, §2 classifier) is owned by another author and may land
# after this file. The import is guarded with a bare `except Exception` rather than
# `except ImportError` on purpose: a module that exists but is half-written raises whatever
# its own body raises, and this module must still import so its own tests can run. Callers
# can always inject `measure` / `classifier` explicitly, which is what the tests do.
# --------------------------------------------------------------------------------------

try:  # pragma: no cover - presence depends on the sibling module landing
    from clip_features import (  # type: ignore  # noqa: F401
        ClipFeatures,
        classify as _clip_features_classify,
        measure_clip as _clip_features_measure,
    )

    CLIP_FEATURES_AVAILABLE = True
except Exception:  # noqa: BLE001 - see comment above
    ClipFeatures = None  # type: ignore[assignment]
    _clip_features_classify = None  # type: ignore[assignment]
    _clip_features_measure = None  # type: ignore[assignment]
    CLIP_FEATURES_AVAILABLE = False


def default_measure() -> Optional[Callable[..., Any]]:
    """`clip_features.measure_clip` if it is importable, else None (never a stub)."""
    return _clip_features_measure


def default_classifier() -> Optional[Callable[..., Any]]:
    """`clip_features.classify` if it is importable, else None (never a stub)."""
    return _clip_features_classify


# --------------------------------------------------------------------------------------
# Named tolerances (CONTRACT_1.5.2.md "Shared numeric conventions"). Every one is a module
# constant carrying the spec's own name, and every one is overridable at the call site.
# --------------------------------------------------------------------------------------

FOOT_SLIDE_LIMIT = 0.01
"""Gate R4 / G8: `footSlide <= 0.01H` during every stance frame."""

POSE_RETURN_DEGREES = 0.5
"""§4: `loop <=> poseReturn <= 0.5 deg per rotation joint`."""

SYMMETRY_TOLERANCE = 1e-4
"""Mirrored-local-X tolerance used to pair left/right chains. Absolute, in model units."""

FOOT_RANGE_TOLERANCE = 0.30
"""Reading chosen for the spec's `footRange ~= travel / 2`.

The spec writes `~=` and gives no band. +/-30% is the reading taken here, and it has to be
at least +/-20% for a reason worth stating rather than smoothing over: a gait built as a
phase machine puts the hip-relative foot excursion at `contactFraction x travel`, which is
1.2x the spec's figure at the walk contact fraction of 0.60 and 0.8x at the run's 0.40.
`travel / 2` is the *step* length between alternating footfalls; §1's `footRange` is a
per-axis positional range. They are not the same quantity. See LIMB_FEATURE_FRAME.
"""

MEDIAL_AXIS_TOLERANCE: Optional[float] = None
"""Default absolute tolerance for "the spine sits on the midline". None = do not check.

Left deliberately off by default: the spine-medial check needs an absolute model-unit
tolerance, and inventing one from H would make `check_medial_lateral` fail on rigs that are
merely built off-origin. Callers with a known H pass `medial_tolerance=0.02 * H`.
"""

LIMB_FEATURE_FRAME = "hip-relative"
"""The frame the `handRange` / `footRange` bands in §R4's worked example are stated in.

§1 defines both as world-space per-axis ranges **pooled over both limbs**. Two properties
of that definition put the spec's authoring bands out of reach, and `clip_features`
documents both on `measure_clip` itself:

1. World space carries the root along, so a forward-travelling gait gives
   `footRange ~= travel` and `handRange ~= travel + swing`. Neither can land in the spec's
   `0.15H-0.25H` / `~travel/2` bands at any speed.
2. Pooling both limbs puts the *lateral separation* between them inside the number. Two
   hands held 0.18H apart and swung 0.20H measure whichever is larger, so an authoring
   band on the swing reads the stance width instead.

`band_features()` therefore measures these two against a hip-relative clip and takes each
limb's own largest axis range (`ClipFeatures.landmark_ranges`, which exists for exactly
this) rather than the pooled figure. This changes nothing about §2: the classifier is
always handed the world-space, pooled `ClipFeatures`, because those are the numbers its
thresholds were derived from. The global features (`duration`, `travel`, `rise`, `speed`,
`scaleDelta`, `poseReturn`) stay in world space too.
"""

MOTION_CLASSES = ("idle", "walk", "run", "dash", "jump", "leap")
"""§2's primary motion classes. `in-place`, `planted` and `gesture` are modifiers, not
primaries — a clip can carry several at once (CONTRACT: "classify returns all matching
labels ... plus a primary motion class")."""

Status = Literal["pass", "fail", "unevaluated"]


# --------------------------------------------------------------------------------------
# Small vector helpers (stdlib only; no numpy)
# --------------------------------------------------------------------------------------

Vec3 = tuple[float, float, float]


def _vec3(value: Any, label: str) -> Vec3:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} must be a length-3 vector, got {value!r}")
    out = tuple(float(component) for component in value)
    if not all(math.isfinite(component) for component in out):
        raise ValueError(f"{label} must be finite, got {value!r}")
    return out  # type: ignore[return-value]


def _distance(a: Vec3, b: Vec3) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


# --------------------------------------------------------------------------------------
# Feature access — tolerant of ClipFeatures' exact field naming
# --------------------------------------------------------------------------------------

FEATURE_ALIASES: dict[str, tuple[str, ...]] = {
    "duration": ("duration",),
    "travel": ("travel",),
    "rise": ("rise",),
    "speed": ("speed",),
    "handRange": ("handRange", "hand_range"),
    "footRange": ("footRange", "foot_range"),
    "headRise": ("headRise", "head_rise"),
    "scaleDelta": ("scaleDelta", "scale_delta"),
    "poseReturn": ("poseReturn", "pose_return"),
    "footSlide": ("footSlide", "foot_slide"),
    "contact": ("contact", "contactFraction", "contact_fraction"),
}


def feature_value(features: Any, name: str) -> Optional[float]:
    """Read one §1 feature off a `ClipFeatures`, a dict, or anything with attributes.

    Returns None when the feature is genuinely absent — which is what makes a criterion
    `unevaluated` rather than a pass. `clip_features.ClipFeatures` may spell its fields in
    either snake_case or camelCase; both are tried, so this module does not have to guess
    the sibling's house style at import time.
    """
    for alias in FEATURE_ALIASES.get(name, (name,)):
        if isinstance(features, Mapping):
            if alias in features:
                value = features[alias]
                return None if value is None else float(value)
        else:
            value = getattr(features, alias, None)
            if value is not None:
                return float(value)
    return None


_CANONICAL_BY_ALIAS: dict[str, str] = {
    alias: canonical for canonical, aliases in FEATURE_ALIASES.items() for alias in aliases
}


class _FeatureView:
    """Attribute access over a feature mapping, so §2's classifier can read a plain dict.

    `clip_features.classify` reads `features.travel`, `features.speed` and friends off a
    `ClipFeatures`. A caller holding a measured-features JSON document has a dict instead.
    This adapts one to the other without copying §2's threshold logic anywhere.

    A feature that is genuinely absent raises `AttributeError` rather than reading as zero —
    a classifier silently told `travel = 0` returns `idle` with full confidence.
    """

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = values

    def __getattr__(self, name: str) -> float:
        value = feature_value(self._values, _CANONICAL_BY_ALIAS.get(name, name))
        if value is None:
            raise AttributeError(
                f"the measured features carry no {name!r}; §2 cannot classify without it "
                f"(present: {sorted(self._values)})"
            )
        return value

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"_FeatureView({dict(self._values)!r})"


def as_classifier_input(features: Any) -> Any:
    """Whatever §2's classifier needs, from whatever the caller has.

    A `ClipFeatures` passes straight through; a mapping is wrapped in `_FeatureView`.
    """
    return _FeatureView(features) if isinstance(features, Mapping) else features


def classifier_verdict(result: Any) -> tuple[Optional[str], tuple[str, ...]]:
    """Normalise whatever `classify` returned into `(primary_class, all_labels)`.

    CONTRACT_1.5.2.md says `classify` returns all matching labels plus a primary motion
    class, but not the container. Every plausible shape is accepted here rather than
    pinning the sibling's return type: a bare string, a sequence of labels, a
    `(primary, labels)` pair, a mapping, or a dataclass with `primary` / `labels` fields.
    An empty match stays empty — CONTRACT: "It never invents a class for an empty match."
    """
    if result is None:
        return None, ()
    if isinstance(result, str):
        return (result if result in MOTION_CLASSES else None), (result,)
    if isinstance(result, Mapping):
        primary = result.get("primary") or result.get("motionClass") or result.get("motion_class")
        labels = result.get("labels") or result.get("classes") or result.get("matches") or ()
        return (str(primary) if primary else None), tuple(str(label) for label in labels)
    # (primary, labels) pair
    if isinstance(result, (list, tuple)) and len(result) == 2 and isinstance(result[0], (str, type(None))):
        if isinstance(result[1], (list, tuple, set, frozenset)):
            primary = result[0]
            labels = tuple(str(label) for label in result[1])
            return (str(primary) if primary else None), labels
    if isinstance(result, (list, tuple, set, frozenset)):
        labels = tuple(str(label) for label in result)
        primary = next((label for label in labels if label in MOTION_CLASSES), None)
        return primary, labels
    # dataclass / plain object
    primary = getattr(result, "primary", None) or getattr(result, "motion_class", None) or getattr(result, "motionClass", None)
    labels_attr = getattr(result, "labels", None) or getattr(result, "classes", None) or ()
    labels = tuple(str(label) for label in labels_attr)
    if primary is None and labels:
        primary = next((label for label in labels if label in MOTION_CLASSES), None)
    if primary is not None and str(primary) not in labels:
        labels = (str(primary),) + labels
    return (str(primary) if primary else None), labels


# --------------------------------------------------------------------------------------
# Chain resolution — from topology, never from names
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class JointNode:
    """One joint in the input hierarchy: id, parent, and position local to the parent."""

    id: str
    parent: Optional[str]
    local_position: Vec3


@dataclass(frozen=True)
class Chain:
    """One resolved chain: an ordered joint path from an anchor to a leaf."""

    name: str
    joint_ids: tuple[str, ...]
    anchor_position: Vec3
    """World (model-space) position of `joint_ids[0]`. This is what Gate G7 compares."""

    @property
    def anchor(self) -> str:
        return self.joint_ids[0]

    @property
    def leaf(self) -> str:
        return self.joint_ids[-1]


@dataclass(frozen=True)
class Chains:
    """The resolved chain set for one rig, plus how each side label was decided."""

    spine: Chain
    arm: dict[str, Chain]
    leg: dict[str, Chain]
    world_positions: dict[str, Vec3]
    side_source: str
    """`"labels"` when the caller supplied an independent left/right claim, `"model-x"`
    when this module fell back to assigning left = positive X. See `gate_is_independent`."""
    symmetry_tolerance: float
    notes: tuple[str, ...] = ()

    @property
    def gate_is_independent(self) -> bool:
        """True when `check_medial_lateral` is capable of failing.

        The medial/lateral gate compares a *claim* about which chain is the left one
        against the *geometry*. When `side_source == "model-x"` the claim was derived from
        that same geometry, so the comparison is a tautology and passes by construction —
        it is still run, and still catches a pair that is not mirrored at all, but it can
        no longer catch a mirrored rig. Pass `side_labels=` to `resolve_chains` (from the
        source rig's own naming, or from an explicit map) to give the gate teeth.
        """
        return self.side_source == "labels"

    def __getitem__(self, name: str) -> Chain:
        if name == "spine":
            return self.spine
        group, _, side = name.partition(".")
        if group == "arm" and side in self.arm:
            return self.arm[side]
        if group == "leg" and side in self.leg:
            return self.leg[side]
        raise KeyError(name)

    def all_chains(self) -> tuple[Chain, ...]:
        return (self.spine, self.arm["l"], self.arm["r"], self.leg["l"], self.leg["r"])


class ChainResolutionError(ValueError):
    """Raised when the hierarchy does not present the structure R4 needs.

    Fail-closed on purpose (same discipline as `rig_spec.validate_rig_spec`): a chain
    resolver that guesses produces an action authored onto the wrong joints, which is
    exactly the class of failure §0 says is total and silent.
    """


def _coerce_nodes(nodes: Iterable[Any]) -> list[JointNode]:
    coerced: list[JointNode] = []
    for index, node in enumerate(nodes):
        if isinstance(node, JointNode):
            coerced.append(node)
            continue
        if isinstance(node, Mapping):
            node_id = node.get("id") or node.get("name")
            parent = node.get("parent")
            position = node.get("local_position", node.get("localPosition", node.get("position")))
        elif isinstance(node, (list, tuple)) and len(node) == 3:
            node_id, parent, position = node
        else:
            raise ChainResolutionError(f"node {index} is not a JointNode, mapping or 3-tuple: {node!r}")
        if not node_id:
            raise ChainResolutionError(f"node {index} has no id")
        coerced.append(
            JointNode(
                id=str(node_id),
                parent=None if parent in (None, "") else str(parent),
                local_position=_vec3(position, f"node {node_id!r} local_position"),
            )
        )
    return coerced


def _children_map(nodes: Sequence[JointNode]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {node.id: [] for node in nodes}
    for node in nodes:
        if node.parent is None:
            continue
        if node.parent not in children:
            raise ChainResolutionError(f"joint {node.id!r} has unresolved parent {node.parent!r}")
        children[node.parent].append(node.id)
    return children


def _world_positions(nodes: Sequence[JointNode]) -> dict[str, Vec3]:
    by_id = {node.id: node for node in nodes}
    world: dict[str, Vec3] = {}

    def resolve(node_id: str, seen: frozenset[str]) -> Vec3:
        if node_id in world:
            return world[node_id]
        if node_id in seen:
            raise ChainResolutionError(f"cycle in the joint hierarchy through {node_id!r}")
        node = by_id[node_id]
        if node.parent is None:
            world[node_id] = node.local_position
        else:
            world[node_id] = _add(resolve(node.parent, seen | {node_id}), node.local_position)
        return world[node_id]

    for node in nodes:
        resolve(node.id, frozenset())
    return world


def _descend_to_branch(start: str, children: Mapping[str, list[str]]) -> list[str]:
    """Walk the single-child chain from `start` until a branch or a leaf. Inclusive."""
    path = [start]
    current = start
    while len(children[current]) == 1:
        current = children[current][0]
        path.append(current)
    return path


def _deepest_leaf_path(start: str, children: Mapping[str, list[str]], world: Mapping[str, Vec3]) -> list[str]:
    """Path from `start` to the leaf of its longest sub-chain.

    Ties are broken deterministically — longest joint count, then greatest accumulated
    segment length, then lexicographic id — so a hand that branches into fingers resolves
    to the same leaf on every run rather than to whichever child happened to be first.
    """

    def walk(node_id: str) -> tuple[int, float, list[str]]:
        kids = children[node_id]
        if not kids:
            return 1, 0.0, [node_id]
        best: Optional[tuple[int, float, list[str]]] = None
        for kid in sorted(kids):
            depth, length, path = walk(kid)
            segment = _distance(world[node_id], world[kid])
            candidate = (depth + 1, length + segment, [node_id] + path)
            if best is None or (candidate[0], candidate[1]) > (best[0], best[1]):
                best = candidate
        assert best is not None
        return best

    return walk(start)[2]


def _mirror_pairs(
    candidates: Sequence[str],
    local: Mapping[str, Vec3],
    tolerance: float,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Partition sibling joints into mirrored-local-X pairs plus the unpaired remainder."""
    unused = list(candidates)
    pairs: list[tuple[str, str]] = []
    while unused:
        a = unused.pop(0)
        ax, ay, az = local[a]
        if abs(ax) <= tolerance:
            continue  # on the midline: medial, cannot be half of a lateral pair
        match: Optional[str] = None
        for b in unused:
            bx, by, bz = local[b]
            if abs(ax + bx) <= tolerance and abs(ay - by) <= tolerance and abs(az - bz) <= tolerance:
                match = b
                break
        if match is not None:
            unused.remove(match)
            pairs.append((a, match))
    paired = {joint for pair in pairs for joint in pair}
    unpaired = [joint for joint in candidates if joint not in paired]
    return pairs, unpaired


def resolve_chains(
    nodes: Iterable[Any],
    side_labels: Optional[Mapping[str, str]] = None,
    symmetry_tolerance: float = SYMMETRY_TOLERANCE,
) -> Chains:
    """Resolve `spine`, `arm.{l,r}` and `leg.{l,r}` from the hierarchy's shape.

    §R4: "Author on **chains**, not individual joints. Resolve from topology." Nothing here
    reads a joint name, and Stage R0 gives the reason: "Do not infer from names ... clips
    target technical nodes that are not joints". A rig whose joints are called `node_17`
    resolves exactly as well as one called `clavicle.l`.

    The shape being matched:

      root -> (single-child chain) -> HIP BRANCH        first node with >= 2 children
          the mirrored-X child pair here    -> leg.{l,r}, each descending to a foot leaf
          the medial child here             -> spine, continuing upward to
      SHOULDER BRANCH                                    next node with >= 2 children
          the mirrored-X child pair here    -> arm.{l,r}, each descending to a hand leaf
          the medial child here             -> the rest of the spine, ending at the head leaf

    SIDE LABELS — the ambiguity worth naming. Topology gives the *pair*; it cannot give
    which half is "left". That is a labelling claim, and it has to come from outside the
    geometry or the medial/lateral gate below becomes a tautology. `side_labels` maps any
    joint id in a chain to `"l"` or `"r"` (typically read off the source rig's own naming,
    which is precisely the claim a mirrored rig gets wrong). Without it this function falls
    back to left = positive model X, records `side_source="model-x"`, and
    `Chains.gate_is_independent` reports False so no caller mistakes the resulting pass for
    evidence.

    Raises `ChainResolutionError` rather than guessing when the hierarchy does not present
    two branch points with a mirrored pair at each.
    """
    node_list = _coerce_nodes(nodes)
    if not node_list:
        raise ChainResolutionError("no joints supplied")
    roots = [node.id for node in node_list if node.parent is None]
    if len(roots) != 1:
        raise ChainResolutionError(f"expected exactly one root joint, found {len(roots)}: {sorted(roots)}")
    root = roots[0]

    children = _children_map(node_list)
    world = _world_positions(node_list)
    local = {node.id: node.local_position for node in node_list}
    notes: list[str] = []

    hip_path = _descend_to_branch(root, children)
    hip = hip_path[-1]
    if len(children[hip]) < 2:
        raise ChainResolutionError(
            f"no branch point below the root: the chain from {root!r} ends at leaf {hip!r}. "
            "R4 needs a hip branch (legs + spine) and a shoulder branch (arms + neck)."
        )

    hip_pairs, hip_medial = _mirror_pairs(children[hip], local, symmetry_tolerance)
    if not hip_pairs:
        raise ChainResolutionError(
            f"hip branch {hip!r} has no mirrored-X child pair within {symmetry_tolerance} "
            f"(children: {sorted(children[hip])}) — cannot resolve leg.l / leg.r"
        )
    if len(hip_pairs) > 1:
        # More than one lateral pair at the hip (legs plus, say, tails or skirt bones).
        # The legs are the pair whose leaves sit lowest; the rest are recorded, not silently
        # dropped, so a reader can see the resolver made a choice.
        hip_pairs.sort(key=lambda pair: min(world[_deepest_leaf_path(joint, children, world)[-1]][1] for joint in pair))
        notes.append(
            f"hip branch {hip!r} had {len(hip_pairs)} mirrored pairs; took the lowest-leaved pair as the legs"
        )
    leg_pair = hip_pairs[0]

    if not hip_medial:
        raise ChainResolutionError(
            f"hip branch {hip!r} has no medial child to continue the spine through "
            f"(children: {sorted(children[hip])})"
        )
    if len(hip_medial) > 1:
        hip_medial.sort(key=lambda joint: -world[_deepest_leaf_path(joint, children, world)[-1]][1])
        notes.append(
            f"hip branch {hip!r} had {len(hip_medial)} medial children; took the highest-leaved one as the spine"
        )
    spine_continuation = hip_medial[0]

    shoulder_path = _descend_to_branch(spine_continuation, children)
    shoulder = shoulder_path[-1]
    if len(children[shoulder]) < 2:
        raise ChainResolutionError(
            f"no shoulder branch above the hip: the spine from {spine_continuation!r} ends at "
            f"leaf {shoulder!r} — cannot resolve arm.l / arm.r"
        )

    arm_pairs, shoulder_medial = _mirror_pairs(children[shoulder], local, symmetry_tolerance)
    if not arm_pairs:
        raise ChainResolutionError(
            f"shoulder branch {shoulder!r} has no mirrored-X child pair within {symmetry_tolerance} "
            f"(children: {sorted(children[shoulder])}) — cannot resolve arm.l / arm.r"
        )
    if len(arm_pairs) > 1:
        arm_pairs.sort(key=lambda pair: -min(world[joint][1] for joint in pair))
        notes.append(
            f"shoulder branch {shoulder!r} had {len(arm_pairs)} mirrored pairs; took the highest as the arms"
        )
    arm_pair = arm_pairs[0]

    if not shoulder_medial:
        raise ChainResolutionError(
            f"shoulder branch {shoulder!r} has no medial child to carry the spine to the head "
            f"(children: {sorted(children[shoulder])})"
        )
    if len(shoulder_medial) > 1:
        shoulder_medial.sort(key=lambda joint: -world[_deepest_leaf_path(joint, children, world)[-1]][1])
        notes.append(f"shoulder branch {shoulder!r} had {len(shoulder_medial)} medial children; took the highest")
    head_path = _deepest_leaf_path(shoulder_medial[0], children, world)

    # Each segment starts at a child of the previous segment's last joint, so there is no
    # shared joint to trim between them.
    spine_ids = tuple(hip_path + shoulder_path + head_path)

    def side_of(pair: tuple[str, str], group: str) -> dict[str, str]:
        if side_labels:
            resolved: dict[str, str] = {}
            for joint in pair:
                path = _deepest_leaf_path(joint, children, world)
                label = next((side_labels[j] for j in path if j in side_labels), None)
                if label is not None:
                    resolved[str(label).lower()[:1]] = joint
            if set(resolved) == {"l", "r"}:
                return resolved
            raise ChainResolutionError(
                f"side_labels did not resolve both halves of the {group} pair {pair}: got {resolved}"
            )
        return {("l" if world[joint][0] > 0 else "r"): joint for joint in pair}

    leg_sides = side_of(leg_pair, "leg")
    arm_sides = side_of(arm_pair, "arm")
    if set(leg_sides) != {"l", "r"} or set(arm_sides) != {"l", "r"}:
        raise ChainResolutionError(
            "a mirrored pair resolved to the same side — both anchors have the same sign of X, "
            f"legs={leg_sides} arms={arm_sides}"
        )

    side_source = "labels" if side_labels else "model-x"
    if side_source == "model-x":
        notes.append(
            "no side_labels supplied: left/right were assigned from the sign of model X, so "
            "check_medial_lateral cannot fail on a mirrored rig (Chains.gate_is_independent is False)"
        )

    def build(name: str, anchor: str) -> Chain:
        path = _deepest_leaf_path(anchor, children, world)
        return Chain(name=name, joint_ids=tuple(path), anchor_position=world[anchor])

    return Chains(
        spine=Chain(name="spine", joint_ids=spine_ids, anchor_position=world[root]),
        arm={side: build(f"arm.{side}", anchor) for side, anchor in arm_sides.items()},
        leg={side: build(f"leg.{side}", anchor) for side, anchor in leg_sides.items()},
        world_positions=world,
        side_source=side_source,
        symmetry_tolerance=symmetry_tolerance,
        notes=tuple(notes),
    )


def check_medial_lateral(
    chains: Chains,
    medial_tolerance: Optional[float] = MEDIAL_AXIS_TOLERANCE,
) -> list[str]:
    """Gate G7 / §R4: assert `leftAnchor.x > 0 > rightAnchor.x` in model space.

    This one comparison catches a mirrored rig — the failure where every left-hand action
    plays on the right and nothing about it looks wrong in isolation. There is no frame in
    which a mirrored rig renders badly: both sides are anatomically plausible, both animate
    smoothly, and the defect only surfaces when someone notices the character draws its
    sword with the wrong hand. Three inequalities per pair are the whole detector.

    Returns a list of error strings; an empty list means the convention holds. Fail-closed:
    a non-empty list is a hard rejection, not a warning.

    Note what this can and cannot see. It compares the *claim* about which chain is left
    against the *geometry*. When `chains.side_source == "model-x"` the claim came from that
    geometry, so the mirrored-rig case is unreachable and only a degenerate pair (both
    anchors on one side, or an anchor sitting on the midline) can still be caught — see
    `Chains.gate_is_independent`.

    `medial_tolerance` optionally adds the spine check: the spine's anchor and head should
    sit on the midline. It is off by default because the tolerance is in absolute model
    units and there is no defensible value without knowing H; callers pass `0.02 * H`.
    """
    errors: list[str] = []
    for group, mapping in (("arm", chains.arm), ("leg", chains.leg)):
        left = mapping["l"]
        right = mapping["r"]
        lx = left.anchor_position[0]
        rx = right.anchor_position[0]
        if not lx > 0:
            errors.append(
                f"MEDIAL_LATERAL: {group}.l anchor {left.anchor!r} has x={lx:.6g}, expected x > 0 "
                "(left is +X in model space) — the rig is mirrored, or the side labels are swapped"
            )
        if not rx < 0:
            errors.append(
                f"MEDIAL_LATERAL: {group}.r anchor {right.anchor!r} has x={rx:.6g}, expected x < 0 "
                "(right is -X in model space) — the rig is mirrored, or the side labels are swapped"
            )
        if lx * rx > 0:
            errors.append(
                f"MEDIAL_LATERAL: {group}.l ({lx:.6g}) and {group}.r ({rx:.6g}) are on the same side of the midline"
            )
    if medial_tolerance is not None:
        for joint in (chains.spine.anchor, chains.spine.leaf):
            x = chains.world_positions[joint][0]
            if abs(x) > medial_tolerance:
                errors.append(
                    f"MEDIAL_AXIS: spine joint {joint!r} has x={x:.6g}, off the midline by more than "
                    f"{medial_tolerance:.6g}"
                )
    return errors


# --------------------------------------------------------------------------------------
# Target bands
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureRange:
    """An inclusive-low band for one §1 feature, with the spec's authoring target."""

    lo: Optional[float] = None
    hi: Optional[float] = None
    hi_exclusive: bool = False
    """§2 writes its upper bounds with `<` (`0.30H/s <= speed < 0.60H/s`). A band that
    mirrors a classifier boundary must mirror its strictness or the two can disagree at the
    boundary — which is the one place they must not."""
    target: Optional[float] = None
    units: str = ""
    source: str = ""
    """Where the number came from, quoted from the spec where possible."""

    def contains(self, value: float) -> bool:
        if self.lo is not None and value < self.lo:
            return False
        if self.hi is not None:
            if self.hi_exclusive and not value < self.hi:
                return False
            if not self.hi_exclusive and value > self.hi:
                return False
        return True

    def describe(self) -> str:
        low = "-inf" if self.lo is None else f"{self.lo:.6g}"
        if self.hi is None:
            high = "+inf)"
        else:
            high = f"{self.hi:.6g}" + (")" if self.hi_exclusive else "]")
        target = "" if self.target is None else f" target {self.target:.6g}"
        return f"[{low}, {high}{target} {self.units}".rstrip()

    @property
    def midpoint(self) -> Optional[float]:
        if self.target is not None:
            return self.target
        if self.lo is not None and self.hi is not None:
            return 0.5 * (self.lo + self.hi)
        return self.lo if self.lo is not None else self.hi

    def to_dict(self) -> dict[str, Any]:
        return {
            "lo": self.lo,
            "hi": self.hi,
            "hiExclusive": self.hi_exclusive,
            "target": self.target,
            "units": self.units,
            "source": self.source,
        }


@dataclass(frozen=True)
class FeatureMiss:
    """One feature that fell outside its band, and on which side."""

    feature: str
    value: float
    side: Literal["below", "above"]
    band: FeatureRange

    def __str__(self) -> str:
        return f"{self.feature}={self.value:.6g} is {self.side} its band {self.band.describe()}"

    def to_dict(self) -> dict[str, Any]:
        return {"feature": self.feature, "value": self.value, "side": self.side, "band": self.band.to_dict()}


@dataclass(frozen=True)
class BandCheck:
    """The result of `TargetBand.contains` — always says *which* feature, never just no."""

    band_name: str
    inside: tuple[str, ...]
    outside: tuple[FeatureMiss, ...]
    unevaluated: tuple[tuple[str, str], ...]

    @property
    def ok(self) -> bool:
        return not self.outside

    def __bool__(self) -> bool:
        return self.ok

    def summary(self) -> str:
        if self.ok and not self.unevaluated:
            return f"{self.band_name}: all {len(self.inside)} features inside band"
        parts = []
        if self.outside:
            parts.append("outside: " + "; ".join(str(miss) for miss in self.outside))
        if self.unevaluated:
            parts.append("unevaluated: " + "; ".join(f"{name} ({why})" for name, why in self.unevaluated))
        return f"{self.band_name}: " + " | ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "band": self.band_name,
            "ok": self.ok,
            "inside": list(self.inside),
            "outside": [miss.to_dict() for miss in self.outside],
            "unevaluated": [{"feature": name, "reason": why} for name, why in self.unevaluated],
        }


@dataclass(frozen=True)
class TargetBand:
    """A per-feature min/max specification for a clip that is about to be authored.

    §R4: "the same feature vector that identifies a clip **specifies** one." A TargetBand is
    that vector read in the authoring direction.

    UNITS — the trap worth stating once, loudly. Every positional bound here is a **fraction
    of H**, not model units, because `clip_features.measure_clip` normalises by H before it
    returns. A band in model units compared against a normalised measurement agrees on every
    rig of height 1.0 and silently disagrees on every other one, which is the exact shape of
    failure §0 is about. `figure_height` records the H the band was built for so a caller can
    convert; `gait_parameters()` is where the conversion to model units happens, because
    authored tracks are the one thing that does live in model space.
    """

    name: str
    figure_height: float
    intended_class: str
    ranges: dict[str, FeatureRange]
    limb_frame: str = LIMB_FEATURE_FRAME
    notes: tuple[str, ...] = ()

    def contains(self, features: Any) -> BandCheck:
        """Check measured features against every range, naming each one that missed.

        Never returns a bare bool. A caller that only wants yes/no can use the truthiness of
        the returned `BandCheck`, but the miss list is always there — "the clip is outside
        the band" is not actionable and "footRange=0.41H is above [0.154, 0.286]" is.

        A feature the measurement does not carry is reported `unevaluated`, not inside.
        """
        inside: list[str] = []
        outside: list[FeatureMiss] = []
        unevaluated: list[tuple[str, str]] = []
        for name, band in self.ranges.items():
            value = feature_value(features, name)
            if value is None:
                unevaluated.append((name, "not present in the measured features"))
                continue
            if band.contains(value):
                inside.append(name)
            else:
                below = band.lo is not None and value < band.lo
                outside.append(FeatureMiss(feature=name, value=value, side="below" if below else "above", band=band))
        return BandCheck(
            band_name=self.name,
            inside=tuple(inside),
            outside=tuple(outside),
            unevaluated=tuple(unevaluated),
        )

    def target(self, feature: str) -> float:
        band = self.ranges[feature]
        value = band.midpoint
        if value is None:
            raise KeyError(f"band {self.name!r} has no usable target for {feature!r}")
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "figureHeight": self.figure_height,
            "intendedClass": self.intended_class,
            "limbFrame": self.limb_frame,
            "ranges": {name: band.to_dict() for name, band in self.ranges.items()},
            "notes": list(self.notes),
        }


def _gait_band(
    *,
    name: str,
    intended_class: str,
    figure_height: float,
    duration_lo: float,
    duration_hi: float,
    speed_lo: float,
    speed_hi: float,
    speed_target: float,
    rise_lo: float,
    rise_hi: float,
    hand_lo: float,
    hand_hi: float,
    hand_hi_exclusive: bool,
    hand_target: float,
    contact_lo: float,
    contact_hi: float,
    contact_hi_exclusive: bool,
    contact_target: float,
    foot_range_tolerance: float,
    notes: Sequence[str],
) -> TargetBand:
    # All positional bounds are fractions of H — see TargetBand's UNITS note.
    travel_lo = speed_target * duration_lo
    travel_hi = speed_target * duration_hi
    step = 0.5 * 0.5 * (travel_lo + travel_hi)  # spec: footRange ~= travel / 2
    ranges = {
        "duration": FeatureRange(
            lo=duration_lo, hi=duration_hi, units="s", source="spec R4: duration ~ 1.0-1.2 s per cycle"
        ),
        "speed": FeatureRange(
            lo=speed_lo,
            hi=speed_hi,
            hi_exclusive=True,
            target=speed_target,
            units="H/s",
            source=f"spec §2 class band for {intended_class}; target from spec R4",
        ),
        "travel": FeatureRange(
            lo=travel_lo,
            hi=travel_hi,
            target=speed_target * 0.5 * (duration_lo + duration_hi),
            units="H",
            source="spec R4: travel = speed x duration",
        ),
        "rise": FeatureRange(
            lo=rise_lo,
            hi=rise_hi,
            units="H",
            source="spec R4: rise 0.015H-0.025H (hips stay flat; more reads as a limp)",
        ),
        "handRange": FeatureRange(
            lo=hand_lo,
            hi=hand_hi,
            hi_exclusive=hand_hi_exclusive,
            target=hand_target,
            units=f"H ({LIMB_FEATURE_FRAME})",
            source="spec R4: counter-swing, opposite phase to the leg",
        ),
        "footRange": FeatureRange(
            lo=step * (1.0 - foot_range_tolerance),
            hi=step * (1.0 + foot_range_tolerance),
            target=step,
            units=f"H ({LIMB_FEATURE_FRAME})",
            source=f"spec R4: footRange ~= travel/2 per foot per cycle, +/-{foot_range_tolerance:.0%}",
        ),
        "contact": FeatureRange(
            lo=contact_lo,
            hi=contact_hi,
            hi_exclusive=contact_hi_exclusive,
            target=contact_target,
            units="fraction of cycle per foot",
            source="spec R4 contact fraction",
        ),
        "poseReturn": FeatureRange(
            lo=0.0, hi=POSE_RETURN_DEGREES, units="deg", source="spec R4/§4: poseReturn <= 0.5 deg -> loopable"
        ),
        "scaleDelta": FeatureRange(lo=0.0, hi=0.0, units="", source="Gate R4: scaleDelta == 0, never author joint scale"),
    }
    return TargetBand(
        name=name,
        figure_height=figure_height,
        intended_class=intended_class,
        ranges=ranges,
        notes=tuple(notes),
    )


def walk_targets(figure_height: float) -> TargetBand:
    """The spec's worked walk example, encoded verbatim.

    From §R4 "Design targets, worked", for a rig of height H:

        duration   ~ 1.0-1.2 s per cycle
        speed      target 0.40H/s      -> travel = speed x duration
        rise       0.015H-0.025H       (hips stay flat; more reads as a limp)
        handRange  0.15H-0.25H         (counter-swing, opposite phase to the leg)
        footRange  ~ stride            ~ travel / 2 per foot per cycle
        contact    0.60 of cycle per foot, so both feet are down 0.20 of the time
        poseReturn <= 0.5 deg          -> loopable by §4

    Two readings the spec leaves open, taken here rather than hidden:

    1. **speed is a target, not a band.** The band around it is §2's own walk class,
       `0.30H/s <= speed < 0.60H/s`, upper bound exclusive. Deriving the band from the
       classifier instead of inventing one is what stops the acceptance test from being
       satisfiable by a clip the classifier would reject.
    2. **`footRange`/`handRange` are hip-relative.** See `LIMB_FEATURE_FRAME`: under §1's
       world-space definition a forward-travelling gait cannot land in these bands at any
       speed, because the limbs go where the body goes.

    `contact` is not a §1 feature — it is an authoring parameter, measurable from a clip's
    stance intervals. `band_features()` computes it; a caller who has no stance data gets
    `unevaluated` for it, never a pass.
    """
    return _gait_band(
        name="walk",
        intended_class="walk",
        figure_height=figure_height,
        duration_lo=1.0,
        duration_hi=1.2,
        speed_lo=0.30,
        speed_hi=0.60,
        speed_target=0.40,
        rise_lo=0.015,
        rise_hi=0.025,
        hand_lo=0.15,
        hand_hi=0.25,
        hand_hi_exclusive=False,
        hand_target=0.20,
        contact_lo=0.55,
        contact_hi=0.65,
        contact_hi_exclusive=False,
        contact_target=0.60,
        foot_range_tolerance=FOOT_RANGE_TOLERANCE,
        notes=(
            "handRange and footRange bands are stated in the hip-relative, per-limb frame; see LIMB_FEATURE_FRAME.",
            "contact 0.60 per foot puts both feet down 0.20 of the cycle (0.60 + 0.60 - 1.00).",
            "R4's 'poseReturn <= 0.5 deg -> loopable by §4' is only half of §4's rule: §4 also requires "
            "||hip(T) - hip(0)|| <= 0.01H, which a forward-travelling walk cycle cannot satisfy in world "
            "space at any speed. Such a clip loops once its root motion is extracted, not before — so it "
            "should not be declared a loop on the strength of poseReturn alone.",
        ),
    )


def run_targets(figure_height: float) -> TargetBand:
    """The spec's run recipe: exactly three changes to `walk_targets`, nothing else.

    §R4: "Run: raise speed into `[0.60H/s, 1.50H/s)`, drop contact fraction below 0.5 so a
    flight phase exists, raise `handRange` toward 0.35H. The classifier must then report
    `run`, not `walk`, without being told — that is the acceptance test."

    Readings taken:

    - **Only those three move.** `duration` and `rise` are inherited from the walk band
      because the spec changes neither. A real run has more hip rise than 0.015H-0.025H;
      that is a divergence from observed running gaits, not from this spec, and it is left
      as written rather than quietly improved.
    - **speed target 0.80H/s** — the measured `run-forward` value (0.799) from §2's own
      validation table, so the target is a number the spec already recorded rather than a
      midpoint invented here.
    - **contact target 0.40**, giving a 0.20 flight phase by the same arithmetic the walk's
      0.60 uses for double support. The band's upper bound is exclusive at 0.50, matching
      "below 0.5".
    - **handRange band `[0.25H, 0.40H)`**, upper bound exclusive at §2's `gesture`
      threshold. `gesture` only fires "while in-place" so a run could not collide with it,
      but a band that runs up to and past another class's boundary invites exactly the
      silent overlap §2 avoided by putting its boundaries in the empty gaps.
    """
    return _gait_band(
        name="run",
        intended_class="run",
        figure_height=figure_height,
        duration_lo=1.0,
        duration_hi=1.2,
        speed_lo=0.60,
        speed_hi=1.50,
        speed_target=0.80,
        rise_lo=0.015,
        rise_hi=0.025,
        hand_lo=0.25,
        hand_hi=0.40,
        hand_hi_exclusive=True,
        hand_target=0.35,
        contact_lo=0.30,
        contact_hi=0.50,
        contact_hi_exclusive=True,
        contact_target=0.40,
        foot_range_tolerance=FOOT_RANGE_TOLERANCE,
        notes=(
            "inherits duration and rise from walk_targets: §R4's run recipe changes speed, contact and handRange only.",
            "contact 0.40 per foot leaves a 0.20 flight phase (1.00 - 0.40 - 0.40).",
            "handRange upper bound is exclusive at §2's gesture threshold of 0.40H.",
        ),
    )


# --------------------------------------------------------------------------------------
# Foot contact — the single most valuable gate in the stage
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StanceSlide:
    """The worst slide measured inside one stance interval of one foot."""

    landmark: str
    interval: tuple[float, float]
    contact_time: float
    frames: int
    max_slide: float
    max_slide_fraction: float
    worst_time: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "landmark": self.landmark,
            "interval": list(self.interval),
            "contactTime": self.contact_time,
            "frames": self.frames,
            "maxSlide": self.max_slide,
            "maxSlideFraction": self.max_slide_fraction,
            "worstTime": self.worst_time,
        }


@dataclass(frozen=True)
class FootSlideReport:
    figure_height: float
    limit: float
    per_stance: tuple[StanceSlide, ...]
    max_slide_fraction: Optional[float]
    status: Status
    reason: str
    unmeasured: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "pass"

    @property
    def worst(self) -> Optional[StanceSlide]:
        if not self.per_stance:
            return None
        return max(self.per_stance, key=lambda stance: stance.max_slide_fraction)

    def to_dict(self) -> dict[str, Any]:
        return {
            "figureHeight": self.figure_height,
            "limit": self.limit,
            "status": self.status,
            "reason": self.reason,
            "maxSlideFraction": self.max_slide_fraction,
            "perStance": [stance.to_dict() for stance in self.per_stance],
            "unmeasured": list(self.unmeasured),
        }


def foot_slide(
    landmark_positions: Mapping[str, Sequence[Sequence[float]]],
    sample_times: Sequence[float],
    stance_intervals: Mapping[str, Sequence[Sequence[float]]],
    figure_height: float,
    limit: float = FOOT_SLIDE_LIMIT,
    time_tolerance: float = 1e-9,
) -> FootSlideReport:
    """`footSlide = max over stance frames ||foot_world(t) - foot_world(t_contact)||`.

        require footSlide <= 0.01H     (FOOT_SLIDE_LIMIT)

    §R4: "This is the single most valuable gate in the stage. A gait that violates it reads
    as 'floaty' or 'skating' to every viewer while being hard to name by eye." That is the
    whole argument for measuring it. Every viewer detects a skating gait instantly and
    almost nobody can say what is wrong with it, so it survives review, survives playtest
    notes, and is fixed only when someone finally puts a number on the stance frames.

    `landmark_positions` must be **world** positions. Passing a hip-relative clip here
    inverts the measurement: with the root's planar motion removed, a *correctly* planted
    foot slides backwards by exactly `travel` per cycle and this gate rejects the one gait
    that was right. `hip_relative_clip()` carries the same warning.

    Reports `unevaluated` when there is no stance data at all — Gate G8 with no stance
    intervals is not a pass, it is an unasked question (CONTRACT: "It is never silently a
    pass"). Individual intervals spanning fewer than two samples are listed in `unmeasured`
    rather than contributing a reassuring zero.
    """
    if figure_height <= 0:
        raise ValueError(f"figure_height must be positive, got {figure_height}")
    times = [float(t) for t in sample_times]

    per_stance: list[StanceSlide] = []
    unmeasured: list[str] = []
    considered = 0

    for landmark, intervals in sorted(stance_intervals.items()):
        positions = landmark_positions.get(landmark)
        if positions is None:
            unmeasured.append(f"{landmark}: stance intervals given but no landmark positions")
            continue
        if len(positions) != len(times):
            raise ValueError(
                f"{landmark}: {len(positions)} positions for {len(times)} sample times — "
                "the sampled-clip payload requires them equal"
            )
        for interval in intervals:
            considered += 1
            t0, t1 = float(interval[0]), float(interval[1])
            frame_indices = [i for i, t in enumerate(times) if t0 - time_tolerance <= t <= t1 + time_tolerance]
            if len(frame_indices) < 2:
                unmeasured.append(
                    f"{landmark} stance [{t0:.6g}, {t1:.6g}]: {len(frame_indices)} sample frame(s), "
                    "too few to measure a slide"
                )
                continue
            contact_index = frame_indices[0]
            anchor = _vec3(positions[contact_index], f"{landmark}[{contact_index}]")
            worst = 0.0
            worst_time = times[contact_index]
            for i in frame_indices:
                slide = _distance(_vec3(positions[i], f"{landmark}[{i}]"), anchor)
                if slide > worst:
                    worst = slide
                    worst_time = times[i]
            per_stance.append(
                StanceSlide(
                    landmark=landmark,
                    interval=(t0, t1),
                    contact_time=times[contact_index],
                    frames=len(frame_indices),
                    max_slide=worst,
                    max_slide_fraction=worst / figure_height,
                    worst_time=worst_time,
                )
            )

    if not per_stance:
        return FootSlideReport(
            figure_height=figure_height,
            limit=limit,
            per_stance=(),
            max_slide_fraction=None,
            status="unevaluated",
            reason=(
                "no measurable stance interval: Gate G8 needs `stance` on the clip payload and at least "
                f"two sample frames inside an interval ({considered} interval(s) considered)"
            ),
            unmeasured=tuple(unmeasured),
        )

    worst_overall = max(stance.max_slide_fraction for stance in per_stance)
    if worst_overall <= limit:
        status: Status = "pass"
        reason = f"footSlide {worst_overall:.6g}H <= {limit:.6g}H over {len(per_stance)} stance interval(s)"
    else:
        status = "fail"
        offender = max(per_stance, key=lambda stance: stance.max_slide_fraction)
        reason = (
            f"footSlide {worst_overall:.6g}H > {limit:.6g}H — {offender.landmark} slid "
            f"{offender.max_slide:.6g} model units at t={offender.worst_time:.6g} while in stance "
            f"[{offender.interval[0]:.6g}, {offender.interval[1]:.6g}]"
        )
    return FootSlideReport(
        figure_height=figure_height,
        limit=limit,
        per_stance=tuple(per_stance),
        max_slide_fraction=worst_overall,
        status=status,
        reason=reason,
        unmeasured=tuple(unmeasured),
    )


# --------------------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Primitive:
    """One row of §R4's primitive table."""

    name: str
    use: str
    parameters: tuple[str, ...]
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "use": self.use, "parameters": list(self.parameters), "note": self.note}


PRIMITIVES: dict[str, Primitive] = {
    "gait": Primitive(
        name="Gait",
        use="walk / run / dash",
        parameters=("cadence", "stride", "contactFraction", "hipRise", "armCounterSwing"),
        note=(
            "A phase machine, not a sine wave. Each leg has stance and swing, and while a foot is in "
            "stance its world position must not move — see GaitPhase and foot_slide()."
        ),
    ),
    "ballistic": Primitive(
        name="Ballistic",
        use="jump / leap",
        parameters=("takeoffVelocity", "apexRise", "flightTime", "landAbsorption"),
    ),
    "reach": Primitive(
        name="Reach",
        use="strike / gesture",
        parameters=("targetSocket", "windupFraction", "followThrough", "return"),
        note="§3: a name for a Reach that implies intent carries inferred: true — no feature separates a strike from a stumble.",
    ),
    "additive": Primitive(
        name="Additive",
        use="breathing, sway",
        parameters=("amplitude", "period", "jointMask"),
        note="Layered on any base clip.",
    ),
}


@dataclass(frozen=True)
class GaitPhase:
    """Stance and swing for one leg, as intervals in seconds — not an amplitude.

    §R4: "Gait is a phase machine, not a sine wave. Each leg has stance and swing; the
    **contact constraint** is what separates an animation from a slide: while a foot is in
    stance its world position must not move."

    A sine-wave foot track cannot express that constraint at all — a sinusoid is moving at
    every instant except its two turning points, so a foot authored as a sine slides through
    its entire stance by construction and `foot_slide` rejects it every time. The stance
    interval is therefore first-class here and the foot position is *constant* across it.
    """

    leg: str
    duration: float
    contact_fraction: float
    stance_start_fraction: float

    def stance_intervals(self) -> tuple[tuple[float, float], ...]:
        """Stance windows clipped into `[0, duration]`, wrapping where the phase does."""
        start = self.stance_start_fraction * self.duration
        end = start + self.contact_fraction * self.duration
        if end <= self.duration:
            return ((start, end),)
        return ((0.0, end - self.duration), (start, self.duration))

    def swing_intervals(self) -> tuple[tuple[float, float], ...]:
        stance = self.stance_intervals()
        cuts = [0.0]
        for lo, hi in stance:
            cuts.extend((lo, hi))
        cuts.append(self.duration)
        out: list[tuple[float, float]] = []
        covered = sorted(stance)
        cursor = 0.0
        for lo, hi in covered:
            if lo - cursor > 1e-12:
                out.append((cursor, lo))
            cursor = max(cursor, hi)
        if self.duration - cursor > 1e-12:
            out.append((cursor, self.duration))
        return tuple(out)

    def plant_index(self, t: float) -> tuple[int, float]:
        """`(which plant, fraction of cycle since it)` for time `t`.

        The plant index is what makes the foot advance: each successive stance is one
        `travel` further down the path, so a foot cannot be "the same plant" one cycle later.
        """
        u = t / self.duration - self.stance_start_fraction
        k = math.floor(u)
        return k, u - k

    def is_stance(self, t: float, tolerance: float = 1e-12) -> bool:
        return self.plant_index(t)[1] <= self.contact_fraction + tolerance

    def to_dict(self) -> dict[str, Any]:
        return {
            "leg": self.leg,
            "duration": self.duration,
            "contactFraction": self.contact_fraction,
            "stanceStartFraction": self.stance_start_fraction,
            "stanceIntervals": [list(interval) for interval in self.stance_intervals()],
            "swingIntervals": [list(interval) for interval in self.swing_intervals()],
        }


FOOT_LIFT_FRACTION_OF_STRIDE = 0.12
"""Swing-foot apex height as a fraction of stride. NOT from the spec — an authoring default.

§R4's gait parameter list has no foot lift, so there is no spec number to quote. It is
exposed as an override and recorded on `GaitParameters` so it is visible as a choice. It
touches no gate: it never moves a stance frame (footSlide), and at any plausible value the
Y excursion stays well under the Z excursion that `footRange`'s per-axis max reads.
"""

HIP_HEIGHT_FRACTION = 0.55
ARM_LATERAL_FRACTION = 0.18
FOOT_LATERAL_FRACTION = 0.09
HAND_HEIGHT_FRACTION = 0.55
HEAD_HEIGHT_FRACTION = 0.94
"""Rest-pose placements for `synthesise_gait_tracks`. Presentation only: no §1 feature is a
function of an absolute position, so these shift the tracks without moving any measurement."""


@dataclass(frozen=True)
class GaitParameters:
    """Gait primitive parameters derived from a target band."""

    band_name: str
    figure_height: float
    duration: float
    cadence: float
    """Steps per second. One cycle is two steps, so `cadence = 2 / duration`."""
    speed: float
    travel: float
    stride: float
    """Step length between alternating footfalls: `travel / 2`. See FOOT_RANGE_TOLERANCE for
    why this is not the same quantity as §1's `footRange`."""
    contact_fraction: float
    double_support_fraction: float
    """`2 * contact - 1`, clamped at 0. Positive is double support; negative would be a
    flight phase, and is reported as `flight_fraction` instead."""
    flight_fraction: float
    hip_rise: float
    arm_counter_swing: float
    foot_lift: float
    phases: tuple[GaitPhase, GaitPhase]

    def phase(self, leg: str) -> GaitPhase:
        for gait_phase in self.phases:
            if gait_phase.leg == leg:
                return gait_phase
        raise KeyError(leg)

    def stance_intervals(self) -> dict[str, list[list[float]]]:
        """Stance windows keyed by the §1 foot landmark, ready for the clip payload."""
        return {
            f"foot.{gait_phase.leg.rsplit('.', 1)[-1]}": [list(i) for i in gait_phase.stance_intervals()]
            for gait_phase in self.phases
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "band": self.band_name,
            "figureHeight": self.figure_height,
            "duration": self.duration,
            "cadence": self.cadence,
            "speed": self.speed,
            "travel": self.travel,
            "stride": self.stride,
            "contactFraction": self.contact_fraction,
            "doubleSupportFraction": self.double_support_fraction,
            "flightFraction": self.flight_fraction,
            "hipRise": self.hip_rise,
            "armCounterSwing": self.arm_counter_swing,
            "footLift": self.foot_lift,
            "phases": [gait_phase.to_dict() for gait_phase in self.phases],
        }


def gait_parameters(
    band: TargetBand,
    figure_height: float,
    foot_lift_fraction: float = FOOT_LIFT_FRACTION_OF_STRIDE,
) -> GaitParameters:
    """Derive the Gait primitive's five parameters from a target band.

    cadence, stride, contact fraction, hip rise and arm counter-swing all come out of the
    band's targets, so a band edit propagates into the authored motion instead of the two
    drifting apart.

    The two legs are half a cycle out of phase (`stance_start_fraction` 0.0 and 0.5). That
    offset plus the contact fraction is the entire gait: at contact 0.60 the overlap is
    0.20 of the cycle of double support (`0.60 + 0.60 - 1.00`, the spec's own arithmetic);
    at contact 0.40 the same subtraction gives -0.20, reported as a 0.20 flight phase, which
    is what §R4 means by "drop contact fraction below 0.5 so a flight phase exists".

    UNITS: the band is in fractions of H (see `TargetBand`); the returned parameters are in
    **model units**, multiplied through by `figure_height`, because they exist to be turned
    into tracks and tracks are model-space. `duration`, `cadence` and `contact_fraction` are
    dimensionless or in seconds and pass through untouched.
    """
    duration = band.target("duration")
    speed = band.target("speed") * figure_height
    travel = speed * duration
    contact = band.target("contact")
    overlap = 2.0 * contact - 1.0
    stride = 0.5 * travel

    hip_rise = band.target("rise") * figure_height
    arm_counter_swing = band.target("handRange") * figure_height

    phases = (
        GaitPhase(leg="leg.l", duration=duration, contact_fraction=contact, stance_start_fraction=0.0),
        GaitPhase(leg="leg.r", duration=duration, contact_fraction=contact, stance_start_fraction=0.5),
    )
    return GaitParameters(
        band_name=band.name,
        figure_height=figure_height,
        duration=duration,
        cadence=2.0 / duration,
        speed=speed,
        travel=travel,
        stride=stride,
        contact_fraction=contact,
        double_support_fraction=max(0.0, overlap),
        flight_fraction=max(0.0, -overlap),
        hip_rise=hip_rise,
        arm_counter_swing=arm_counter_swing,
        foot_lift=foot_lift_fraction * stride,
        phases=phases,
    )


# --------------------------------------------------------------------------------------
# Authoring — turn gait parameters into a sampled-clip payload
# --------------------------------------------------------------------------------------

SAMPLE_COUNT = 25
"""§1: "Sample the clip at N = 25 evenly spaced times." """


def sample_times(duration: float, count: int = SAMPLE_COUNT, endpoint: bool = False) -> list[float]:
    """`count` evenly spaced times over `[0, duration]`.

    READING CHOSEN: `endpoint=False`, i.e. `t_i = i * duration / count`, so `t = duration`
    is not sampled. For a looping clip the pose at `t = duration` is the pose at `t = 0`;
    including both double-counts it and, worse, offsets every other sample by
    `duration / (N - 1)` instead of `duration / N`, which walks the samples off the phase
    boundaries a gait is defined by. `endpoint=True` is available for a clip that is not a
    loop and whose final pose is genuinely distinct.
    """
    if count < 2:
        raise ValueError(f"need at least 2 samples, got {count}")
    divisor = (count - 1) if endpoint else count
    return [index * duration / divisor for index in range(count)]


def _smoothstep(x: float) -> float:
    return x * x * (3.0 - 2.0 * x)


def synthesise_gait_tracks(
    params: GaitParameters,
    count: int = SAMPLE_COUNT,
    endpoint: bool = False,
    source_name: str = "authored",
) -> dict[str, Any]:
    """Author world-space landmark tracks for a gait, as a CONTRACT_1.5.2 clip payload.

    This is the "author tracks" arrow of §R4's loop, kept in this module so the round trip
    `targets -> author -> measure -> classify` can actually be run rather than asserted.

    The contact constraint is honoured literally: for every sample inside a stance window
    the foot's world position is the plant position, unchanged. It is not "nearly constant"
    or "constant to within an easing curve" — it is the same three floats, so `foot_slide`
    on this payload measures exactly 0.

    Swing uses smoothstep, which is monotone. That matters beyond looking smooth: a
    non-monotone swing would overshoot the plant positions and inflate the measured
    `footRange` past the geometry the band was derived from.

    `poseReturn` is emitted as 0.0 because every track here is exactly periodic by
    construction (each landmark's hip-relative position at `t = duration` equals its
    position at `t = 0`). A host evaluating a real rig measures it off the joint transforms
    instead; this is a synthesised payload declaring a property of its own construction.
    """
    height = params.figure_height
    duration = params.duration
    times = sample_times(duration, count=count, endpoint=endpoint)

    contact = params.contact_fraction
    travel = params.travel
    speed = params.speed

    # Plant offsets chosen so each foot's hip-relative excursion is symmetric about 0:
    # the foot plants ahead of the hip by half its stance excursion (contact * travel / 2)
    # and lifts the same distance behind it.
    plant_z = {"l": 0.5 * contact * travel, "r": (0.5 + 0.5 * contact) * travel}
    lateral_x = {"l": FOOT_LATERAL_FRACTION * height, "r": -FOOT_LATERAL_FRACTION * height}

    positions: dict[str, list[list[float]]] = {
        name: [] for name in ("hip", "head", "hand.l", "hand.r", "foot.l", "foot.r")
    }

    for t in times:
        cycle = t / duration
        hip_z = speed * t
        # Hips fall and rise twice per cycle — once per step, not once per cycle.
        hip_y = HIP_HEIGHT_FRACTION * height + 0.5 * params.hip_rise * math.cos(4.0 * math.pi * cycle)
        positions["hip"].append([0.0, hip_y, hip_z])
        positions["head"].append(
            [0.0, HEAD_HEIGHT_FRACTION * height + 0.5 * params.hip_rise * math.cos(4.0 * math.pi * cycle), hip_z]
        )

        for side in ("l", "r"):
            gait_phase = params.phase(f"leg.{side}")
            plant, frac = gait_phase.plant_index(t)
            base_z = plant_z[side] + plant * travel
            if frac <= contact + 1e-12:
                foot = [lateral_x[side], 0.0, base_z]
            else:
                w = (frac - contact) / (1.0 - contact)
                foot = [
                    lateral_x[side],
                    params.foot_lift * math.sin(math.pi * w),
                    base_z + travel * _smoothstep(w),
                ]
            positions[f"foot.{side}"].append(foot)

            # Counter-swing: the hand is in opposite phase to the leg on the same side.
            swing = 0.5 * params.arm_counter_swing * math.cos(2.0 * math.pi * (cycle - gait_phase.stance_start_fraction))
            positions[f"hand.{side}"].append(
                [ARM_LATERAL_FRACTION * height * (1 if side == "l" else -1), HAND_HEIGHT_FRACTION * height, hip_z - swing]
            )

    return {
        "sourceName": source_name,
        "duration": duration,
        "sampleTimes": times,
        "landmarkPositions": positions,
        "jointScaleDelta": [0.0] * len(times),
        "poseReturn": 0.0,
        "stance": params.stance_intervals(),
    }


def build_payload(clips: Sequence[Mapping[str, Any]], figure_height: float) -> dict[str, Any]:
    """Wrap authored clips in the CONTRACT_1.5.2 sampled-clip payload envelope."""
    return {
        "figureHeight": figure_height,
        "landmarks": ["hip", "head", "hand.l", "hand.r", "foot.l", "foot.r"],
        "clips": [dict(clip) for clip in clips],
    }


def hip_relative_clip(clip: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of `clip` with the root's **planar** motion removed from every landmark.

    This is the frame `LIMB_FEATURE_FRAME` names, and the frame the `handRange`/`footRange`
    bands are stated in. Vertical motion is untouched, so `rise` and `headRise` read the same
    in both frames.

    NEVER feed the result to `foot_slide`. With the root's advance removed, a correctly
    planted foot travels backwards by exactly `travel` per cycle, so the gate would reject
    the one gait that satisfies it. footSlide is a world-space measurement, always.
    """
    positions = clip["landmarkPositions"]
    hip = [_vec3(p, "hip") for p in positions["hip"]]
    origin = hip[0]
    out: dict[str, list[list[float]]] = {}
    for name, track in positions.items():
        moved = []
        for index, point in enumerate(track):
            x, y, z = _vec3(point, name)
            moved.append([x - (hip[index][0] - origin[0]), y, z - (hip[index][2] - origin[2])])
        out[name] = moved
    result = dict(clip)
    result["landmarkPositions"] = out
    result["frame"] = "hip-relative"
    return result


def measured_contact_fraction(clip: Mapping[str, Any]) -> Optional[float]:
    """Mean per-foot stance time as a fraction of the cycle, from the clip's stance data."""
    stance = clip.get("stance")
    duration = clip.get("duration")
    if not stance or not duration:
        return None
    fractions = []
    for intervals in stance.values():
        total = sum(float(interval[1]) - float(interval[0]) for interval in intervals)
        fractions.append(total / float(duration))
    return sum(fractions) / len(fractions) if fractions else None


def _call_measure(measure: Callable[..., Any], clip: Mapping[str, Any], figure_height: float) -> Any:
    """Call `measure_clip` without pinning down which signature the sibling settled on."""
    for args in ((clip, figure_height), (clip,)):
        try:
            return measure(*args)
        except TypeError as exc:  # only a signature mismatch, never a measurement failure
            if "positional argument" not in str(exc) and "argument" not in str(exc):
                raise
    return measure(clip, figure_height=figure_height)


def _per_limb_range(features: Any, landmarks: Sequence[str]) -> Optional[float]:
    """Max over `landmarks` of that landmark's own largest axis range, or None.

    The un-pooled reading of `handRange` / `footRange`. See `LIMB_FEATURE_FRAME` point 2 for
    why pooling is wrong for an authoring band. Returns None when the measurement does not
    carry per-landmark ranges, so the caller falls back to the pooled figure rather than
    getting a fabricated one.
    """
    ranges = getattr(features, "landmark_ranges", None)
    if ranges is None and isinstance(features, Mapping):
        ranges = features.get("landmarkRanges") or features.get("landmark_ranges")
    if not ranges:
        return None
    values = [max(ranges[name]) for name in landmarks if name in ranges]
    return max(values) if values else None


def band_features(
    clip: Mapping[str, Any],
    figure_height: float,
    measure: Optional[Callable[..., Any]] = None,
) -> dict[str, float]:
    """Measure a clip into the feature dict a `TargetBand` expects.

    Global features (`duration`, `travel`, `rise`, `speed`, `headRise`, `scaleDelta`,
    `poseReturn`) are read in world space — the only frame §2's classifier is meaningful in,
    since `travel` and `speed` are zero by construction in any root-relative frame.
    `handRange` and `footRange` are read per-limb from `hip_relative_clip(clip)`, per
    `LIMB_FEATURE_FRAME`. `contact` comes from the clip's stance intervals.

    The result is a plain dict for `TargetBand.contains`. It is deliberately NOT what gets
    handed to §2's classifier — see `accepts(classifier_input=...)`.

    `measure` defaults to `clip_features.measure_clip`; a caller must supply one when that
    module is not importable, and gets a clear error rather than a fabricated measurement.
    """
    measure = measure or default_measure()
    if measure is None:
        raise RuntimeError(
            "no measure_clip available: clip_features could not be imported and no `measure` was supplied. "
            "band_features refuses to invent a measurement."
        )
    world = _call_measure(measure, clip, figure_height)
    relative = _call_measure(measure, hip_relative_clip(clip), figure_height)

    values: dict[str, float] = {}
    for name in ("duration", "travel", "rise", "speed", "headRise", "scaleDelta", "poseReturn"):
        value = feature_value(world, name)
        if value is not None:
            values[name] = value
    for name, landmarks in (("handRange", ("hand.l", "hand.r")), ("footRange", ("foot.l", "foot.r"))):
        value = _per_limb_range(relative, landmarks)
        if value is None:
            value = feature_value(relative, name)
        if value is not None:
            values[name] = value
    contact = measured_contact_fraction(clip)
    if contact is not None:
        values["contact"] = contact
    return values


# --------------------------------------------------------------------------------------
# Acceptance — Gate R4
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Criterion:
    name: str
    status: Status
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"criterion": self.name, "status": self.status, "reason": self.reason, "detail": self.detail}


@dataclass(frozen=True)
class Acceptance:
    """Gate R4's verdict: every criterion, with pass / fail / unevaluated and a reason.

    `accepted` is False if any criterion failed. `complete` is False if any criterion could
    not be evaluated. They are separate on purpose: "nothing failed" and "everything was
    checked" are different claims, and collapsing them into one boolean is how an
    unevaluated gate becomes a silent pass.
    """

    intended_class: str
    band_name: str
    criteria: tuple[Criterion, ...]

    @property
    def failures(self) -> tuple[Criterion, ...]:
        return tuple(c for c in self.criteria if c.status == "fail")

    @property
    def unevaluated(self) -> tuple[Criterion, ...]:
        return tuple(c for c in self.criteria if c.status == "unevaluated")

    @property
    def accepted(self) -> bool:
        return not self.failures

    @property
    def complete(self) -> bool:
        return not self.unevaluated

    @property
    def verdict(self) -> str:
        if not self.accepted:
            return "rejected"
        return "accepted" if self.complete else "accepted-with-unevaluated"

    def criterion(self, name: str) -> Criterion:
        for candidate in self.criteria:
            if candidate.name == name:
                return candidate
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": "R4",
            "intendedClass": self.intended_class,
            "band": self.band_name,
            "verdict": self.verdict,
            "accepted": self.accepted,
            "complete": self.complete,
            "criteria": [c.to_dict() for c in self.criteria],
        }


def accepts(
    measured_features: Any,
    band: TargetBand,
    intended_class: str,
    classifier: Optional[Callable[..., Any]] = None,
    *,
    classifier_input: Any = None,
    foot_slide_result: Optional[FootSlideReport] = None,
    declared_loop: Optional[bool] = None,
    source_ranges: Optional[Mapping[str, Sequence[float]]] = None,
    authored_ranges: Optional[Mapping[str, Sequence[float]]] = None,
) -> Acceptance:
    """Gate R4, in full.

        classifier(authored) == intended class          (§2 agrees)
        footSlide <= 0.01H during every stance frame
        scaleDelta == 0                                  (never author joint scale)
        poseReturn <= 0.5 deg for any clip declared loop
        no joint exceeds its measured range from the source clips, where source clips exist

    plus the containment check §R4's loop is built around: the measured features land inside
    the target band.

    **"Looks right" is not a criterion.** Every entry below is a number against a bound. The
    classifier is deliberately not told the intended class — it is handed the measurement and
    asked what the clip is, and the answer either matches or the clip is rejected. A gate the
    author can talk their way past is not a gate.

    Any criterion whose input is absent reports `unevaluated` with a reason and does not
    count as a pass (CONTRACT: "It is never silently a pass"). `declared_loop=False` also
    reports `unevaluated` on the poseReturn criterion, with the reason that a non-loop clip
    is out of the criterion's scope — under a three-value vocabulary that is more honest than
    recording a pass nobody checked.

    `classifier_input` exists because the two halves read different measurements. The band
    check wants `band_features()`'s hip-relative, per-limb dict; §2's classifier wants the
    world-space pooled `ClipFeatures` its thresholds were derived from, and would be a
    different classifier if handed anything else. Pass the `ClipFeatures` here and the merged
    dict as `measured_features`; when omitted, both read `measured_features`.
    """
    criteria: list[Criterion] = []

    # --- band containment ---------------------------------------------------------------
    check = band.contains(measured_features)
    if check.outside:
        criteria.append(
            Criterion(
                name="band-containment",
                status="fail",
                reason=check.summary(),
                detail=check.to_dict(),
            )
        )
    elif check.unevaluated:
        criteria.append(
            Criterion(
                name="band-containment",
                status="unevaluated",
                reason=check.summary(),
                detail=check.to_dict(),
            )
        )
    else:
        criteria.append(
            Criterion(
                name="band-containment",
                status="pass",
                reason=check.summary(),
                detail=check.to_dict(),
            )
        )

    # --- §2 must agree ------------------------------------------------------------------
    classifier = classifier or default_classifier()
    if classifier is None:
        criteria.append(
            Criterion(
                name="classifier-agreement",
                status="unevaluated",
                reason=(
                    "no §2 classifier available: clip_features could not be imported and no `classifier` "
                    "was supplied. The acceptance test for a designed clip IS the classifier, so this is "
                    "the criterion that matters most and it was not run."
                ),
            )
        )
    else:
        subject = as_classifier_input(measured_features if classifier_input is None else classifier_input)
        primary, labels = classifier_verdict(classifier(subject))
        agrees = intended_class == primary or intended_class in labels
        criteria.append(
            Criterion(
                name="classifier-agreement",
                status="pass" if agrees else "fail",
                reason=(
                    f"§2 classified the authored clip as {primary!r} (labels {list(labels)}), "
                    f"intended {intended_class!r}" + ("" if agrees else " — the classifier does not agree")
                ),
                detail={"primary": primary, "labels": list(labels), "intended": intended_class},
            )
        )

    # --- foot slide ---------------------------------------------------------------------
    if foot_slide_result is None:
        slide_value = feature_value(measured_features, "footSlide")
        if slide_value is None:
            criteria.append(
                Criterion(
                    name="foot-slide",
                    status="unevaluated",
                    reason=(
                        "no footSlide measurement: pass `foot_slide_result=foot_slide(...)`, or a "
                        "`footSlide` feature. A gait with no stance data has not passed G8, it has "
                        "not been asked."
                    ),
                )
            )
        else:
            ok = slide_value <= FOOT_SLIDE_LIMIT
            criteria.append(
                Criterion(
                    name="foot-slide",
                    status="pass" if ok else "fail",
                    reason=f"footSlide {slide_value:.6g}H vs limit {FOOT_SLIDE_LIMIT:.6g}H",
                    detail={"footSlide": slide_value, "limit": FOOT_SLIDE_LIMIT},
                )
            )
    else:
        criteria.append(
            Criterion(
                name="foot-slide",
                status=foot_slide_result.status,
                reason=foot_slide_result.reason,
                detail=foot_slide_result.to_dict(),
            )
        )

    # --- never author joint scale -------------------------------------------------------
    scale_delta = feature_value(measured_features, "scaleDelta")
    if scale_delta is None:
        criteria.append(
            Criterion(
                name="no-joint-scale",
                status="unevaluated",
                reason="no scaleDelta measurement — §1 calls this a tripwire; an unread tripwire is not a pass",
            )
        )
    else:
        criteria.append(
            Criterion(
                name="no-joint-scale",
                status="pass" if scale_delta == 0 else "fail",
                reason=(
                    f"scaleDelta = {scale_delta!r}, required exactly 0"
                    + ("" if scale_delta == 0 else " — an authored clip must never scale a joint (Gate R4/G9)")
                ),
                detail={"scaleDelta": scale_delta},
            )
        )

    # --- loop pose return ---------------------------------------------------------------
    pose_return = feature_value(measured_features, "poseReturn")
    if declared_loop is None:
        criteria.append(
            Criterion(
                name="pose-return-loop",
                status="unevaluated",
                reason="the clip does not declare whether it loops; §4 forbids guessing false",
                detail={"poseReturn": pose_return},
            )
        )
    elif not declared_loop:
        criteria.append(
            Criterion(
                name="pose-return-loop",
                status="unevaluated",
                reason="clip is not declared a loop, so §4's poseReturn bound is out of scope for it",
                detail={"poseReturn": pose_return, "declaredLoop": False},
            )
        )
    elif pose_return is None:
        criteria.append(
            Criterion(
                name="pose-return-loop",
                status="unevaluated",
                reason="clip is declared a loop but carries no poseReturn measurement",
                detail={"declaredLoop": True},
            )
        )
    else:
        ok = pose_return <= POSE_RETURN_DEGREES
        criteria.append(
            Criterion(
                name="pose-return-loop",
                status="pass" if ok else "fail",
                reason=f"poseReturn {pose_return:.6g} deg vs {POSE_RETURN_DEGREES:.6g} deg for a declared loop",
                detail={"poseReturn": pose_return, "limit": POSE_RETURN_DEGREES, "declaredLoop": True},
            )
        )

    # --- joint range vs source clips ----------------------------------------------------
    criteria.append(_joint_range_criterion(source_ranges, authored_ranges))

    return Acceptance(intended_class=intended_class, band_name=band.name, criteria=tuple(criteria))


def _joint_range_criterion(
    source_ranges: Optional[Mapping[str, Sequence[float]]],
    authored_ranges: Optional[Mapping[str, Sequence[float]]],
) -> Criterion:
    """"no joint exceeds its measured range from the source clips, **where source clips exist**".

    The trailing clause is the whole point: a rig authored from scratch has no source clips,
    so there is no measured range to stay inside and the criterion is `unevaluated`. Calling
    that a pass would claim a bound was checked when there was no bound.
    """
    if not source_ranges:
        return Criterion(
            name="joint-range-vs-source",
            status="unevaluated",
            reason=(
                "no source clips exist for this rig, so there is no measured joint range to bound the "
                "authored one — the criterion is out of evidence, not satisfied"
            ),
        )
    if not authored_ranges:
        return Criterion(
            name="joint-range-vs-source",
            status="unevaluated",
            reason="source ranges were supplied but the authored clip's per-joint ranges were not",
            detail={"sourceJoints": sorted(source_ranges)},
        )
    violations: list[dict[str, Any]] = []
    unbounded: list[str] = []
    checked = 0
    for joint, authored in sorted(authored_ranges.items()):
        source = source_ranges.get(joint)
        if source is None:
            unbounded.append(joint)
            continue
        checked += 1
        a_lo, a_hi = float(authored[0]), float(authored[1])
        s_lo, s_hi = float(source[0]), float(source[1])
        if a_lo < s_lo or a_hi > s_hi:
            violations.append(
                {"joint": joint, "authored": [a_lo, a_hi], "source": [s_lo, s_hi]}
            )
    if checked == 0:
        return Criterion(
            name="joint-range-vs-source",
            status="unevaluated",
            reason="no authored joint shares an id with a source-measured joint range",
            detail={"unbounded": unbounded},
        )
    if violations:
        worst = "; ".join(
            f"{v['joint']} authored [{v['authored'][0]:.6g}, {v['authored'][1]:.6g}] exceeds source "
            f"[{v['source'][0]:.6g}, {v['source'][1]:.6g}]"
            for v in violations
        )
        return Criterion(
            name="joint-range-vs-source",
            status="fail",
            reason=f"{len(violations)} of {checked} joint(s) exceed their source-measured range: {worst}",
            detail={"violations": violations, "unbounded": unbounded, "checked": checked},
        )
    return Criterion(
        name="joint-range-vs-source",
        status="pass",
        reason=f"all {checked} joint(s) with a source-measured range stayed inside it",
        detail={"checked": checked, "unbounded": unbounded},
    )


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

BANDS: dict[str, Callable[[float], TargetBand]] = {"walk": walk_targets, "run": run_targets}


def _load_input(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage R4 acceptance: report Gate R4 for a measured clip against a target band."
    )
    parser.add_argument(
        "clip_json",
        help=(
            "either a CONTRACT_1.5.2 sampled-clip payload (needs clip_features for measurement), "
            "or a features document {figureHeight, features, declaredLoop?, sourceRanges?, authoredRanges?}"
        ),
    )
    parser.add_argument("intended_class", help="the class the clip claims to be, e.g. walk")
    parser.add_argument("--band", choices=sorted(BANDS), default=None, help="target band (default: intended class)")
    parser.add_argument("--height", type=float, default=None, help="figure height H (default: from the document)")
    parser.add_argument("--clip-index", type=int, default=0, help="which clip of a payload to accept")
    args = parser.parse_args(argv)

    document = _load_input(args.clip_json)
    height = args.height or float(document.get("figureHeight", 1.0))
    band_name = args.band or args.intended_class
    if band_name not in BANDS:
        print(json.dumps({"error": f"no target band for {band_name!r}; known: {sorted(BANDS)}"}), file=sys.stdout)
        return 1
    band = BANDS[band_name](height)

    foot_slide_result: Optional[FootSlideReport] = None
    classifier_input: Any = None
    if "clips" in document:
        clip = document["clips"][args.clip_index]
        measure = default_measure()
        if measure is None:
            print(
                json.dumps(
                    {
                        "error": (
                            "a sampled-clip payload needs clip_features.measure_clip to measure it, and that "
                            "module is not importable; supply a pre-measured features document instead"
                        )
                    }
                )
            )
            return 1
        features: Any = band_features(clip, height, measure=measure)
        classifier_input = _call_measure(measure, clip, height)
        if clip.get("stance"):
            foot_slide_result = foot_slide(
                clip["landmarkPositions"], clip["sampleTimes"], clip["stance"], height
            )
        declared_loop = document.get("declaredLoop", clip.get("loop"))
        source_ranges = document.get("sourceRanges")
        authored_ranges = document.get("authoredRanges")
    else:
        features = document.get("features", document)
        declared_loop = document.get("declaredLoop")
        source_ranges = document.get("sourceRanges")
        authored_ranges = document.get("authoredRanges")

    acceptance = accepts(
        features,
        band,
        args.intended_class,
        classifier_input=classifier_input,
        foot_slide_result=foot_slide_result,
        declared_loop=declared_loop,
        source_ranges=source_ranges,
        authored_ranges=authored_ranges,
    )
    report = acceptance.to_dict()
    report["figureHeight"] = height
    report["measuredFeatures"] = features if isinstance(features, dict) else str(features)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if acceptance.accepted else 1


if __name__ == "__main__":
    sys.exit(main())
