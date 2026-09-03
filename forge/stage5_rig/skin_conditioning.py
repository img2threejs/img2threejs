#!/usr/bin/env python3
"""Stage R2 — skin conditioning by proximity weight blending.

A character built from multiple overlapping parts tears open the moment it moves. Two vertices
sitting on top of each other in bind pose but bound to DIFFERENT joints **must** separate as soon as
those joints diverge; that is not a bug in the weights, it is what the weights say. This module is
the fix: where two parts come within R of each other, every vertex is rebound to the weight field of
its whole neighbourhood instead of to its own part alone, so neighbouring vertices travel together
and the seam stops opening.

    w_mixed = w_own + Sigma_q  w_q * (1 - d_q/R)^2       q = every vertex within R, either part
    w_final = renormalise( top4( w_mixed ) )

The sum runs over the vertex's own binding AND every vertex within R of it, own part or foreign; the
presence of at least one FOREIGN vertex within R is what decides whether a vertex is blended at all.
That distinction matters and is not cosmetic: two coincident vertices then see exactly the same
neighbourhood -- the sum for each contains the other with kernel weight (1 - 0/R)^2 = 1 -- so they
come out with the same mixed field and the same reduced binding, which is precisely the tear this
stage exists to close. Summing only over foreign neighbours would instead hand each vertex a weight
field dominated by the OTHER part, and a symmetric seam would come out with the two parts' weights
swapped rather than shared.

THE TRADE, WHICH MUST BE STATED WHENEVER THIS STAGE RUNS
--------------------------------------------------------
Averaging two parts' bindings makes them travel together, which closes the hole, but it also pulls
each vertex slightly off the path its own joints would take -- so creases get about 16% WORSE.
Measured over a 176-frame sweep (11 clips x 4 times x 2 shoulders x 2 azimuths):

    blend off          background through splits  974 px in 30 blobs    thin dark creases  31,316 px
    blend R = 0.006H   background through splits  287 px in 15 blobs    thin dark creases  36,470 px

This was accepted deliberately: **a hole shows the background, a crease shows skin.** A later stage
must NOT "fix" the crease count by disabling the blend -- that trades a defect you can see through
for a defect you can see, and the sweep above is the evidence, not an opinion.

RESIDUAL, DOCUMENTED RATHER THAN HIDDEN
---------------------------------------
Creases are not eliminated by this method. Removing them entirely requires welding the parts into
one continuous mesh with a single unified weight field, which changes the part structure and breaks
per-part UI. That is a different topology contract, not a tuning of this one.

WHAT DID NOT WORK
-----------------
Welding only exactly-coincident vertices (within 1e-4 H). It closed the neck-to-collarbone crack, but
a sweep of 11 clips x 4 times x 3 azimuths = 132 frames still found cracks in 28 frames, 1,410 px of
background showing through: adjacent parts mostly OVERLAP, they rarely share a rim, so coincidence
welding finds almost nothing to weld. `weld_coincident` below is kept only so that negative result
stays reproducible -- it is not part of the pipeline.

Pure Python 3.10+ stdlib. No pip installs, no numpy.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# R = 0.006H, `single-subject`: chosen against a control run on ONE figure, so it is a caller
# overridable default and never a constant. The value actually used is recorded in the BlendReport.
BLEND_RADIUS = 0.006
WEIGHT_SUM_TOLERANCE = 2e-7
MAX_INFLUENCES = 4
# The rejected approach's tolerance, kept with the rejected approach.
WELD_TOLERANCE = 1e-4
# A reduction to top-4 always throws something away; this is the line above which the discarded mass
# is worth counting. An 8-bit normalised weight buffer quantises to 1/255 ~= 3.9e-3, so anything
# above a thousandth of the mixed mass can still survive quantisation and is a real loss. It is a
# REPORTING threshold only -- nothing behaves differently on either side of it.
DISCARD_REPORT_THRESHOLD = 1e-3
# A gate failure list is for a human to read. Past this many the list stops being readable and the
# count is the useful part.
_MAX_REPORTED_FAILURES = 20

TRADE_NOTE = (
    "Proximity blending closes holes and makes creases ~16% worse (measured over 176 frames: "
    "holes 974px/30 blobs -> 287px/15 blobs, creases 31,316px -> 36,470px). A hole shows the "
    "background, a crease shows skin; the trade was accepted deliberately. Do not disable the "
    "blend to bring the crease count back down."
)
RESIDUAL_NOTE = (
    "Creases are not eliminated by this method. Removing them entirely needs one continuous mesh "
    "with a unified weight field, which changes the part structure and breaks per-part UI."
)

# One cell per radius means the R-ball around any query point is covered by the 3x3x3 block of cells
# centred on the point's own cell -- 27 buckets, whatever the vertex count.
_CELL_OFFSETS: list[tuple[int, int, int]] = [
    (dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
]


@dataclass(frozen=True)
class SkinBinding:
    """A skinned vertex set: position, owning part, and 4 influences per vertex.

    `skin_indices` and `skin_weights` are FLAT, four entries per vertex, matching the glTF accessor
    layout the emitter writes -- so a binding can be round-tripped without a reshape that would have
    to guess the stride.
    """

    positions: list[list[float]]
    part_ids: list[Any]
    skin_indices: list[int]
    skin_weights: list[float]
    joint_count: int

    @property
    def vertex_count(self) -> int:
        return len(self.positions)

    def influences(self, index: int) -> list[tuple[int, float]]:
        base = index * MAX_INFLUENCES
        return [
            (self.skin_indices[base + slot], self.skin_weights[base + slot])
            for slot in range(MAX_INFLUENCES)
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "positions": [list(p) for p in self.positions],
            "partIds": list(self.part_ids),
            "skinIndices": list(self.skin_indices),
            "skinWeights": list(self.skin_weights),
            "jointCount": self.joint_count,
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "SkinBinding":
        try:
            return SkinBinding(
                positions=[[float(c) for c in point] for point in payload["positions"]],
                part_ids=list(payload["partIds"]),
                skin_indices=[int(i) for i in payload["skinIndices"]],
                skin_weights=[float(w) for w in payload["skinWeights"]],
                joint_count=int(payload["jointCount"]),
            )
        except KeyError as exc:
            raise ValueError(f"binding payload is missing {exc.args[0]!r}") from exc


@dataclass(frozen=True)
class BlendReport:
    """What the blend actually did, including what it threw away."""

    vertices_total: int
    vertices_touched: int
    vertices_interior: int
    vertices_without_weight: int
    figure_height: float
    radius_fraction: float
    radius_world: float
    max_influences_seen: int
    lossy_reductions: int
    max_discarded_fraction: float
    discard_threshold: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "verticesTotal": self.vertices_total,
            "verticesTouched": self.vertices_touched,
            "verticesInterior": self.vertices_interior,
            "verticesWithoutWeight": self.vertices_without_weight,
            "figureHeight": self.figure_height,
            "radiusFraction": self.radius_fraction,
            "radiusWorld": self.radius_world,
            "maxInfluencesSeen": self.max_influences_seen,
            "lossyReductions": self.lossy_reductions,
            "maxDiscardedFraction": self.max_discarded_fraction,
            "discardThreshold": self.discard_threshold,
            "trade": TRADE_NOTE,
            "residual": RESIDUAL_NOTE,
        }


class UniformGrid:
    """A uniform spatial hash at exactly ONE CELL PER QUERY RADIUS.

    Sizing the cell to the radius is the whole point: the R-ball around a query point cannot reach
    past the 3x3x3 block of cells around it, so a query touches at most 27 buckets no matter how many
    vertices exist. Brute force is O(n^2) and unusable past ~50k vertices; a smaller cell would make
    the neighbourhood span more than 27 buckets and a larger one would drag in vertices that have to
    be distance-tested and thrown away.
    """

    def __init__(self, positions: list[list[float]], cell_size: float):
        if cell_size <= 0.0:
            raise ValueError("cell_size must be positive")
        self.cell_size = cell_size
        self.buckets: dict[tuple[int, int, int], list[int]] = {}
        self.buckets_visited = 0
        self.max_buckets_per_query = 0
        for index, point in enumerate(positions):
            self.buckets.setdefault(self.cell_of(point), []).append(index)
        self._positions = positions

    def cell_of(self, point: list[float]) -> tuple[int, int, int]:
        size = self.cell_size
        return (
            math.floor(point[0] / size),
            math.floor(point[1] / size),
            math.floor(point[2] / size),
        )

    def query(self, point: list[float], radius: float) -> list[tuple[int, float]]:
        """Every vertex within `radius` of `point`, as (vertex index, distance).

        `radius` may not exceed the cell size; a larger radius would need more than the 27 buckets
        this walks and would silently miss neighbours instead of being slow, which is the worse
        failure of the two.
        """
        if radius > self.cell_size:
            raise ValueError(
                f"radius {radius} exceeds cell size {self.cell_size}; the 27-bucket walk would "
                "miss neighbours. Build the grid at one cell per radius."
            )
        x, y, z = self.cell_of(point)
        found: list[tuple[int, float]] = []
        visited = 0
        for dx, dy, dz in _CELL_OFFSETS:
            visited += 1
            for index in self.buckets.get((x + dx, y + dy, z + dz), ()):
                distance = math.dist(point, self._positions[index])
                if distance <= radius:
                    found.append((index, distance))
        self.buckets_visited += visited
        self.max_buckets_per_query = max(self.max_buckets_per_query, visited)
        return found


def brute_force_query(
    positions: list[list[float]], point: list[float], radius: float
) -> list[tuple[int, float]]:
    """The O(n^2) neighbour search the grid replaces, kept so the grid can be CHECKED against it.

    Without it "the grid finds the same neighbours" is an assertion; with it a test can compare the
    two on a small fixture and see that it is true.
    """
    found = []
    for index, other in enumerate(positions):
        distance = math.dist(point, other)
        if distance <= radius:
            found.append((index, distance))
    return found


def _check_shape(binding: SkinBinding) -> None:
    count = binding.vertex_count
    if count == 0:
        raise ValueError("binding has no vertices")
    if len(binding.part_ids) != count:
        raise ValueError(
            f"part_ids has {len(binding.part_ids)} entries for {count} vertices; one per vertex"
        )
    if len(binding.skin_indices) != count * MAX_INFLUENCES:
        raise ValueError(
            f"skin_indices has {len(binding.skin_indices)} entries; expected "
            f"{count * MAX_INFLUENCES} ({MAX_INFLUENCES} per vertex, flat)"
        )
    if len(binding.skin_weights) != count * MAX_INFLUENCES:
        raise ValueError(
            f"skin_weights has {len(binding.skin_weights)} entries; expected "
            f"{count * MAX_INFLUENCES} ({MAX_INFLUENCES} per vertex, flat)"
        )
    if binding.joint_count < 1:
        raise ValueError("joint_count must be at least 1")


def _dense_field(
    binding: SkinBinding, neighbours: list[tuple[int, float]], radius: float
) -> dict[int, float]:
    """Expand the neighbourhood's 4-slot bindings into a DENSE vector over all joints.

    Mixing the sparse 4-slot rows directly -- adding a neighbour's weight only where its joint
    already occupies one of this vertex's four slots -- silently drops every influence that does not
    already fit, which is most of them at a seam where two parts reference different joints. The
    expansion is what makes the mix see all of them; the reduction back to 4 then happens ONCE, on
    the finished field, where it can pick the four largest rather than the four that happened to be
    there first.

    Terms are summed with `math.fsum`, not `+=`: fsum is correctly rounded and therefore
    order-independent, so the field does not depend on the order the grid handed the neighbours back.
    """
    terms: dict[int, list[float]] = {}
    for index, distance in neighbours:
        kernel = (1.0 - distance / radius) ** 2
        base = index * MAX_INFLUENCES
        for slot in range(MAX_INFLUENCES):
            weight = binding.skin_weights[base + slot]
            if weight <= 0.0:
                # A zero-weight slot carries no influence; recording its joint would inflate the
                # influence count with joints that do not actually move the vertex.
                continue
            terms.setdefault(binding.skin_indices[base + slot], []).append(weight * kernel)
    return {joint: math.fsum(values) for joint, values in terms.items()}


def _reduce(dense: dict[int, float]) -> tuple[list[int], list[float], float]:
    """Keep the four largest influences and renormalise. Returns (indices, weights, discarded)."""
    # Ties broken by joint index so the result is a function of the field alone.
    ordered = sorted(dense.items(), key=lambda item: (-item[1], item[0]))
    kept = ordered[:MAX_INFLUENCES]
    total = math.fsum(weight for _, weight in dense.items())
    kept_total = math.fsum(weight for _, weight in kept)
    if kept_total <= 0.0:
        return [0] * MAX_INFLUENCES, [0.0] * MAX_INFLUENCES, 0.0
    indices = [0] * MAX_INFLUENCES
    weights = [0.0] * MAX_INFLUENCES
    for slot, (joint, weight) in enumerate(kept):
        indices[slot] = joint
        weights[slot] = weight / kept_total
    # Put the rounding residual on the dominant slot. Four divisions do not have to sum to exactly
    # 1.0 in float, and Gate R2 is |1 - sum(w)| <= 2e-7 -- close enough to the tolerance to be worth
    # removing at the source rather than hoping.
    weights[0] += 1.0 - math.fsum(weights)
    discarded = (total - kept_total) / total if total > 0.0 else 0.0
    return indices, weights, discarded


def blend_weights(
    binding: SkinBinding,
    figure_height: float,
    radius: float = BLEND_RADIUS,
) -> tuple[SkinBinding, BlendReport]:
    """Proximity weight blending. Returns a NEW binding and a report; the input is never mutated.

    `radius` is a FRACTION of figure height H, so the same value works at any scale; the world-space
    radius actually used is `radius * figure_height` and is recorded in the report.

    A vertex with no vertex from another part within R is INTERIOR and keeps its source binding
    exactly -- the same indices and the same float weights, not a recomputed approximation of them.
    Interior vertices are the overwhelming majority, and re-deriving them would introduce a
    difference the whole model would carry for no reason.

    Every result is buffered and committed after all reads finish. Reading a partly blended field
    would make each vertex depend on the ones visited before it, which makes the output a function of
    the vertex ORDER rather than of the geometry -- reproducible only by accident.
    """
    _check_shape(binding)
    if figure_height <= 0.0:
        raise ValueError("figure_height must be positive")
    if radius <= 0.0:
        raise ValueError("radius must be positive")

    world_radius = radius * figure_height
    grid = UniformGrid(binding.positions, world_radius)

    # WRITE AFTER ALL READS: nothing below assigns into the output arrays.
    pending: dict[int, tuple[list[int], list[float]]] = {}
    touched = 0
    interior = 0
    without_weight = 0
    max_influences_seen = 0
    lossy = 0
    max_discarded = 0.0

    for index in range(binding.vertex_count):
        neighbours = grid.query(binding.positions[index], world_radius)
        own_part = binding.part_ids[index]
        if not any(binding.part_ids[other] != own_part for other, _ in neighbours):
            interior += 1
            continue
        dense = _dense_field(binding, neighbours, world_radius)
        max_influences_seen = max(max_influences_seen, len(dense))
        if not dense or math.fsum(dense.values()) <= 0.0:
            # Every influence in the neighbourhood is zero, so there is no field to blend. Keeping
            # the source binding is the only answer that does not invent one.
            without_weight += 1
            continue
        indices, weights, discarded = _reduce(dense)
        max_discarded = max(max_discarded, discarded)
        if discarded > DISCARD_REPORT_THRESHOLD:
            lossy += 1
        pending[index] = (indices, weights)
        touched += 1

    out_indices: list[int] = []
    out_weights: list[float] = []
    for index in range(binding.vertex_count):
        base = index * MAX_INFLUENCES
        result = pending.get(index)
        if result is None:
            out_indices.extend(binding.skin_indices[base : base + MAX_INFLUENCES])
            out_weights.extend(binding.skin_weights[base : base + MAX_INFLUENCES])
            continue
        out_indices.extend(result[0])
        out_weights.extend(result[1])

    blended = SkinBinding(
        positions=[list(point) for point in binding.positions],
        part_ids=list(binding.part_ids),
        skin_indices=out_indices,
        skin_weights=out_weights,
        joint_count=binding.joint_count,
    )
    report = BlendReport(
        vertices_total=binding.vertex_count,
        vertices_touched=touched,
        vertices_interior=interior,
        vertices_without_weight=without_weight,
        figure_height=figure_height,
        radius_fraction=radius,
        radius_world=world_radius,
        max_influences_seen=max_influences_seen,
        lossy_reductions=lossy,
        max_discarded_fraction=max_discarded,
        discard_threshold=DISCARD_REPORT_THRESHOLD,
    )
    return blended, report


def weld_coincident(
    binding: SkinBinding,
    figure_height: float,
    tolerance: float = WELD_TOLERANCE,
) -> tuple[SkinBinding, int]:
    """THE REJECTED APPROACH, kept so its failure stays measurable. Do not ship this.

    Averages the bindings of vertices lying within `tolerance` * H of each other. It closed one
    visible crack and left 28 of 132 swept frames still cracked, because adjacent parts mostly
    OVERLAP rather than share a rim -- so on a real seam there is usually nothing within 1e-4 H to
    weld and the pass is a no-op. `blend_weights` is what replaced it.

    Returns the welded binding and the number of groups that actually spanned more than one part.
    """
    _check_shape(binding)
    if figure_height <= 0.0:
        raise ValueError("figure_height must be positive")
    world_tolerance = tolerance * figure_height
    grid = UniformGrid(binding.positions, world_tolerance)

    parent = list(range(binding.vertex_count))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for index in range(binding.vertex_count):
        for other, _ in grid.query(binding.positions[index], world_tolerance):
            a, b = find(index), find(other)
            if a != b:
                parent[max(a, b)] = min(a, b)

    groups: dict[int, list[int]] = {}
    for index in range(binding.vertex_count):
        groups.setdefault(find(index), []).append(index)

    pending: dict[int, tuple[list[int], list[float]]] = {}
    welded_groups = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        if len({binding.part_ids[member] for member in members}) < 2:
            continue
        welded_groups += 1
        dense = _dense_field(binding, [(member, 0.0) for member in members], world_tolerance)
        indices, weights, _ = _reduce(dense)
        for member in members:
            pending[member] = (indices, weights)

    out_indices: list[int] = []
    out_weights: list[float] = []
    for index in range(binding.vertex_count):
        base = index * MAX_INFLUENCES
        result = pending.get(index)
        if result is None:
            out_indices.extend(binding.skin_indices[base : base + MAX_INFLUENCES])
            out_weights.extend(binding.skin_weights[base : base + MAX_INFLUENCES])
            continue
        out_indices.extend(result[0])
        out_weights.extend(result[1])

    return (
        SkinBinding(
            positions=[list(point) for point in binding.positions],
            part_ids=list(binding.part_ids),
            skin_indices=out_indices,
            skin_weights=out_weights,
            joint_count=binding.joint_count,
        ),
        welded_groups,
    )


def validate_binding(binding: SkinBinding) -> list[str]:
    """Gate R2. Returns human-readable failures; an empty list is a pass.

        |1 - sum(w)| <= 2e-7   for every vertex
        maxSkinIndex <= joint_count - 1

    Both exist because of what they catch, not for tidiness: a weight sum off by a percent inflates
    or shrinks a limb every frame it is posed, and a single out-of-range index reads a garbage matrix
    and sends one vertex to infinity. The index check is here rather than only at bind time because
    the reduction to top-4 rewrites the indices, so being in range before it says nothing.
    """
    failures: list[str] = []

    count = binding.vertex_count
    if count == 0:
        return ["binding has no vertices"]
    if len(binding.part_ids) != count:
        failures.append(
            f"part_ids has {len(binding.part_ids)} entries for {count} vertices; expected one each"
        )
    if len(binding.skin_indices) != count * MAX_INFLUENCES:
        failures.append(
            f"skin_indices has {len(binding.skin_indices)} entries; expected "
            f"{count * MAX_INFLUENCES}"
        )
    if len(binding.skin_weights) != count * MAX_INFLUENCES:
        failures.append(
            f"skin_weights has {len(binding.skin_weights)} entries; expected "
            f"{count * MAX_INFLUENCES}"
        )
    if binding.joint_count < 1:
        failures.append(f"joint_count is {binding.joint_count}; expected at least 1")
    if failures:
        # The per-vertex checks below index into these arrays; running them on a malformed binding
        # would report an exception instead of the shape problem that caused it.
        return failures

    sum_failures = 0
    range_failures = 0
    worst_sum_error = 0.0
    max_index = 0
    for index in range(count):
        base = index * MAX_INFLUENCES
        weights = binding.skin_weights[base : base + MAX_INFLUENCES]
        error = abs(1.0 - math.fsum(weights))
        if not (error <= WEIGHT_SUM_TOLERANCE):  # not(<=) also catches NaN
            sum_failures += 1
            worst_sum_error = max(worst_sum_error, error if error == error else float("inf"))
            if len(failures) < _MAX_REPORTED_FAILURES:
                failures.append(
                    f"vertex {index}: |1 - sum(w)| = {error:.3e} exceeds {WEIGHT_SUM_TOLERANCE:.1e} "
                    f"(weights {weights})"
                )
        for slot in range(MAX_INFLUENCES):
            joint = binding.skin_indices[base + slot]
            max_index = max(max_index, joint)
            if joint < 0 or joint > binding.joint_count - 1:
                range_failures += 1
                if len(failures) < _MAX_REPORTED_FAILURES:
                    failures.append(
                        f"vertex {index} slot {slot}: skin index {joint} outside "
                        f"[0, {binding.joint_count - 1}]"
                    )

    if sum_failures + range_failures > len(failures):
        failures.append(
            f"... {sum_failures} weight-sum failures (worst {worst_sum_error:.3e}) and "
            f"{range_failures} index-range failures in total; maxSkinIndex = {max_index}, "
            f"joint_count = {binding.joint_count}"
        )
    return failures


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Stage R2: blend skin weights across part seams by proximity."
    )
    parser.add_argument("binding", type=Path, help="JSON binding payload")
    parser.add_argument(
        "--radius",
        type=float,
        default=None,
        help=f"blend radius as a fraction of figure height (default {BLEND_RADIUS}, single-subject)",
    )
    parser.add_argument(
        "--figure-height",
        type=float,
        default=None,
        help="figure height H, if the payload does not carry `figureHeight`",
    )
    parser.add_argument("--out", type=Path, help="also write the result here")
    args = parser.parse_args(argv)

    try:
        payload = json.loads(args.binding.read_text())
        source = SkinBinding.from_dict(payload)
        height = args.figure_height if args.figure_height is not None else payload.get("figureHeight")
        if height is None:
            raise ValueError("no figure height: pass --figure-height or add `figureHeight`")
        radius = args.radius
        if radius is None:
            radius = float(payload.get("blendRadius", BLEND_RADIUS))
        blended, report = blend_weights(source, float(height), radius)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    failures = validate_binding(blended)
    result = {
        "schemaVersion": 1,
        "kind": "blended-skin-binding",
        "binding": blended.to_dict(),
        "report": report.to_dict(),
        "gateR2": {"passed": not failures, "failures": failures},
    }
    if args.out:
        args.out.write_text(json.dumps(result))
    print(json.dumps(result, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
