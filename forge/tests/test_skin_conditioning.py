#!/usr/bin/env python3
"""Tests for Stage R2 skin conditioning, built around the defect it exists to remove.

`test_coincident_vertices_bound_to_different_joints_stop_separating` is the whole argument for this
module. It builds two overlapping parts, poses them with a real linear-blend skin transform whose two
joints diverge, and MEASURES the gap that opens at the seam before and after blending -- because
"proximity blending stops the skin tearing" is otherwise just a claim. Everything else here defends
one of the four implementation requirements, each of which is load-bearing on its own.

Pure Python 3.10+ stdlib.
"""

from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stage5_rig"))

from skin_conditioning import (  # noqa: E402
    BLEND_RADIUS,
    MAX_INFLUENCES,
    WEIGHT_SUM_TOLERANCE,
    SkinBinding,
    UniformGrid,
    blend_weights,
    brute_force_query,
    validate_binding,
    weld_coincident,
    _CELL_OFFSETS,
)

TORSO = 0
ARM = 1


class Fixture:
    """A two-part seam plus one deep interior vertex per part.

    Coordinates are fractions of figure height H and are scaled by `height` on the way in, so the
    same fixture can be built at any scale. The two grids are mirror images about the seam row, which
    is what makes the coincident probe pair a fair test: neither part is favoured by the geometry.
    """

    def __init__(self, height: float = 1.0, arm_shift: float = 0.0):
        self.height = height
        xs = [0.000, 0.002, 0.004]
        torso_ys = [0.000, 0.002, 0.004]
        arm_ys = [0.004 + arm_shift, 0.006 + arm_shift, 0.008 + arm_shift]

        positions: list[list[float]] = []
        parts: list[str] = []
        indices: list[int] = []
        weights: list[float] = []

        def add(point: tuple[float, float, float], part: str, joint: int) -> int:
            positions.append([coordinate * height for coordinate in point])
            parts.append(part)
            indices.extend([joint, 0, 0, 0])
            weights.extend([1.0, 0.0, 0.0, 0.0])
            return len(positions) - 1

        for y in torso_ys:
            for x in xs:
                index = add((x, y, 0.0), "torso", TORSO)
                if (x, y) == (0.002, torso_ys[2]):
                    self.torso_probe = index
        for y in arm_ys:
            for x in xs:
                index = add((x, y, 0.0), "arm", ARM)
                if (x, y) == (0.002, arm_ys[0]):
                    self.arm_probe = index

        # Far from the seam in both directions: no vertex of the other part is anywhere near them, so
        # they are interior by definition and must come back untouched.
        self.torso_interior = add((0.002, -0.5, 0.0), "torso", TORSO)
        self.arm_interior = add((0.002, 0.5, 0.0), "arm", ARM)

        self.binding = SkinBinding(
            positions=positions,
            part_ids=parts,
            skin_indices=indices,
            skin_weights=weights,
            joint_count=2,
        )


def rotate_about_z(degrees: float, pivot: list[float]):
    """One joint's world transform: a rotation about a pivot, the way a shoulder actually moves."""
    angle = math.radians(degrees)
    cosine, sine = math.cos(angle), math.sin(angle)

    def transform(point: list[float]) -> list[float]:
        x = point[0] - pivot[0]
        y = point[1] - pivot[1]
        return [cosine * x - sine * y + pivot[0], sine * x + cosine * y + pivot[1], point[2]]

    return transform


def identity(point: list[float]) -> list[float]:
    return list(point)


def diverging_joints(height: float = 1.0):
    """Joint 0 holds still, joint 1 swings 40 degrees about a pivot below the seam.

    Any pair of vertices bound to different joints must separate under this; that separation is the
    defect being measured, not an artefact of the fixture.
    """
    return [identity, rotate_about_z(40.0, [0.0, -0.4 * height, 0.0])]


def pose(binding: SkinBinding, joints, index: int) -> list[list[float]]:
    """Linear blend skinning of one vertex: v' = sum_i w_i * (M_i . v)."""
    point = binding.positions[index]
    result = [0.0, 0.0, 0.0]
    for joint, weight in binding.influences(index):
        if weight == 0.0:
            continue
        moved = joints[joint](point)
        for axis in range(3):
            result[axis] += weight * moved[axis]
    return result


def separation(binding: SkinBinding, joints, a: int, b: int) -> float:
    return math.dist(pose(binding, joints, a), pose(binding, joints, b))


def slots(binding: SkinBinding, index: int) -> tuple[list[int], list[float]]:
    base = index * MAX_INFLUENCES
    return (
        binding.skin_indices[base : base + MAX_INFLUENCES],
        binding.skin_weights[base : base + MAX_INFLUENCES],
    )


class TheDefectThisFixes(unittest.TestCase):
    """The seam tear, measured on a posed mesh before and after blending."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = Fixture()
        cls.joints = diverging_joints()
        cls.blended, cls.report = blend_weights(cls.fixture.binding, 1.0)

    def test_the_probe_vertices_really_are_coincident_and_differently_bound(self) -> None:
        # If this were false the measurement below would prove nothing.
        source = self.fixture.binding
        self.assertEqual(
            source.positions[self.fixture.torso_probe], source.positions[self.fixture.arm_probe]
        )
        self.assertNotEqual(
            source.influences(self.fixture.torso_probe)[0][0],
            source.influences(self.fixture.arm_probe)[0][0],
        )
        self.assertNotEqual(
            source.part_ids[self.fixture.torso_probe], source.part_ids[self.fixture.arm_probe]
        )

    def test_coincident_vertices_bound_to_different_joints_stop_separating(self) -> None:
        before = separation(
            self.fixture.binding, self.joints, self.fixture.torso_probe, self.fixture.arm_probe
        )
        after = separation(
            self.blended, self.joints, self.fixture.torso_probe, self.fixture.arm_probe
        )
        # The tear is real before the blend: two vertices at the same point in bind pose end up a
        # quarter of a figure height apart once their joints diverge. That is the hole.
        self.assertGreater(before, 0.2)
        # And it is gone afterwards. Both vertices see the same neighbourhood -- each contains the
        # other at distance 0 -- so they get the same mixed field and the same reduced binding, and
        # a shared binding cannot separate under any pose.
        self.assertLess(after, before * 0.01)

    def test_the_blend_does_not_work_by_freezing_the_arm(self) -> None:
        # A fix that simply averaged everything toward the torso would also close the seam, and would
        # be useless. Deep arm vertices must still move with the arm joint alone.
        moved = pose(self.blended, self.joints, self.fixture.arm_interior)
        expected = self.joints[ARM](self.blended.positions[self.fixture.arm_interior])
        self.assertLess(math.dist(moved, expected), 1e-12)

    def test_the_report_counts_what_it_touched(self) -> None:
        total = self.fixture.binding.vertex_count
        self.assertEqual(
            self.report.vertices_touched
            + self.report.vertices_interior
            + self.report.vertices_without_weight,
            total,
        )
        self.assertGreater(self.report.vertices_touched, 0)
        self.assertEqual(self.report.radius_fraction, BLEND_RADIUS)
        self.assertAlmostEqual(self.report.radius_world, BLEND_RADIUS * 1.0)


class WeldingWasNotEnough(unittest.TestCase):
    """The rejected approach, reproduced so it can never come back.

    Coincidence welding closed one visible crack and left 28 of 132 swept frames still cracked. The
    reason is geometric, not a tuning failure: adjacent parts mostly OVERLAP rather than share a rim,
    so on a real seam there is nothing within 1e-4 H to weld.
    """

    @classmethod
    def setUpClass(cls) -> None:
        # arm_shift = -0.001H: the parts interpenetrate, and the nearest cross-part pair is 0.001H
        # apart -- ten times the weld tolerance, well inside the blend radius.
        cls.fixture = Fixture(arm_shift=-0.001)
        cls.joints = diverging_joints()
        cls.welded, cls.welded_groups = weld_coincident(cls.fixture.binding, 1.0)
        cls.blended, _ = blend_weights(cls.fixture.binding, 1.0)

    def test_the_overlap_fixture_has_nothing_exactly_coincident(self) -> None:
        source = self.fixture.binding
        nearest = min(
            math.dist(source.positions[a], source.positions[b])
            for a in range(source.vertex_count)
            for b in range(source.vertex_count)
            if a < b and source.part_ids[a] != source.part_ids[b]
        )
        self.assertGreater(nearest, 1e-4)
        self.assertLess(nearest, BLEND_RADIUS)

    def test_welding_finds_nothing_to_weld_and_changes_nothing(self) -> None:
        self.assertEqual(self.welded_groups, 0)
        self.assertEqual(self.welded.skin_indices, self.fixture.binding.skin_indices)
        self.assertEqual(self.welded.skin_weights, self.fixture.binding.skin_weights)

    def test_the_seam_still_opens_after_welding_but_not_after_blending(self) -> None:
        probes = (self.fixture.torso_probe, self.fixture.arm_probe)
        bind_gap = math.dist(
            self.fixture.binding.positions[probes[0]], self.fixture.binding.positions[probes[1]]
        )
        opened_unfixed = separation(self.fixture.binding, self.joints, *probes) - bind_gap
        opened_welded = separation(self.welded, self.joints, *probes) - bind_gap
        opened_blended = separation(self.blended, self.joints, *probes) - bind_gap
        # Welding is a no-op here, so the hole is exactly as wide as it was.
        self.assertAlmostEqual(opened_welded, opened_unfixed, places=12)
        self.assertGreater(opened_unfixed, 0.2)
        # Blending is not a no-op on the same geometry. The parts do not become identical -- these
        # vertices are not coincident -- but the seam closes by most of its width.
        self.assertLess(opened_blended, opened_unfixed * 0.25)


class DenseAccumulation(unittest.TestCase):
    """Expand to a dense vector over all joints before mixing, reduce back to 4 afterwards."""

    @classmethod
    def setUpClass(cls) -> None:
        # A centre vertex bound to joints 0 and 1, with four foreign neighbours referencing joints
        # 2, 3, 4 and 5 at increasing distance. Six distinct joints in the neighbourhood, four slots
        # to hold them.
        positions = [[0.0, 0.0, 0.0]]
        parts = ["a"]
        indices = [0, 1, 0, 0]
        weights = [0.7, 0.3, 0.0, 0.0]
        for offset, joint in ((0.0010, 2), (0.0015, 3), (0.0020, 4), (0.0025, 5)):
            positions.append([offset, 0.0, 0.0])
            parts.append("b")
            indices.extend([joint, 0, 0, 0])
            weights.extend([1.0, 0.0, 0.0, 0.0])
        cls.source = SkinBinding(positions, parts, indices, weights, joint_count=6)
        cls.blended, cls.report = blend_weights(cls.source, 1.0)

    def dense_by_hand(self) -> dict[int, float]:
        """The mixed field at the centre vertex, recomputed independently of the module."""
        radius = BLEND_RADIUS
        field: dict[int, float] = {}
        for index, point in enumerate(self.source.positions):
            distance = math.dist(self.source.positions[0], point)
            if distance > radius:
                continue
            kernel = (1.0 - distance / radius) ** 2
            for joint, weight in self.source.influences(index):
                if weight > 0.0:
                    field[joint] = field.get(joint, 0.0) + weight * kernel
        return field

    def sparse_mix_by_hand(self) -> set[int]:
        """The WRONG method: mix into the four slots the vertex already has and drop the rest."""
        radius = BLEND_RADIUS
        own = {joint for joint, weight in self.source.influences(0) if weight > 0.0}
        field = {joint: weight for joint, weight in self.source.influences(0) if weight > 0.0}
        for index, point in enumerate(self.source.positions[1:], start=1):
            distance = math.dist(self.source.positions[0], point)
            if distance > radius:
                continue
            kernel = (1.0 - distance / radius) ** 2
            for joint, weight in self.source.influences(index):
                if weight > 0.0 and joint in own:
                    field[joint] += weight * kernel
        return set(field)

    def test_the_neighbourhood_really_references_more_than_four_joints(self) -> None:
        self.assertGreater(len(self.dense_by_hand()), MAX_INFLUENCES)
        self.assertGreaterEqual(self.report.max_influences_seen, len(self.dense_by_hand()))

    def test_the_four_largest_influences_of_the_full_field_survive(self) -> None:
        expected = [
            joint
            for joint, _ in sorted(self.dense_by_hand().items(), key=lambda kv: (-kv[1], kv[0]))
        ][:MAX_INFLUENCES]
        got, _ = slots(self.blended, 0)
        self.assertEqual(got, expected)

    def test_mixing_the_sparse_slots_directly_would_give_a_different_answer(self) -> None:
        # This is why the dense expansion is not an optimisation detail. The sparse mix can only ever
        # return joints the vertex already had, so every influence arriving from the other part --
        # which is the entire point of the blend -- is dropped before it is even weighed.
        wrong = self.sparse_mix_by_hand()
        got, _ = slots(self.blended, 0)
        self.assertNotEqual(set(got), wrong)
        self.assertTrue(set(got) - wrong, "the dense field must keep joints the sparse mix loses")

    def test_a_lossy_reduction_is_reported_not_hidden(self) -> None:
        # Six influences into four slots throws real weight away here. The number is reported so a
        # caller can see it; hiding it would make a visibly wrong deformation look like a clean run.
        self.assertGreaterEqual(self.report.lossy_reductions, 1)
        self.assertGreater(self.report.max_discarded_fraction, 0.0)


class WriteAfterAllReads(unittest.TestCase):
    def test_shuffling_the_vertex_order_changes_nothing(self) -> None:
        fixture = Fixture()
        source = fixture.binding
        order = list(range(source.vertex_count))
        random.Random(20250825).shuffle(order)

        shuffled = SkinBinding(
            positions=[source.positions[i] for i in order],
            part_ids=[source.part_ids[i] for i in order],
            skin_indices=[
                source.skin_indices[i * MAX_INFLUENCES + slot]
                for i in order
                for slot in range(MAX_INFLUENCES)
            ],
            skin_weights=[
                source.skin_weights[i * MAX_INFLUENCES + slot]
                for i in order
                for slot in range(MAX_INFLUENCES)
            ],
            joint_count=source.joint_count,
        )

        straight, _ = blend_weights(source, 1.0)
        mixed, _ = blend_weights(shuffled, 1.0)
        for position, original in enumerate(order):
            self.assertEqual(
                slots(mixed, position),
                slots(straight, original),
                f"vertex {original} blended differently when visited in a different order",
            )


class InteriorVerticesAreNeverTouched(unittest.TestCase):
    def test_interior_vertices_are_bit_identical_to_the_source(self) -> None:
        fixture = Fixture()
        source = fixture.binding
        blended, report = blend_weights(source, 1.0)
        for index in (fixture.torso_interior, fixture.arm_interior):
            source_indices, source_weights = slots(source, index)
            blended_indices, blended_weights = slots(blended, index)
            self.assertEqual(blended_indices, source_indices)
            # Bit-identical, not merely close: a recomputed interior weight would put a difference
            # into every vertex of the model for no reason at all.
            self.assertEqual(blended_weights, source_weights)
            for got, want in zip(blended_weights, source_weights):
                self.assertEqual(math.copysign(1.0, got), math.copysign(1.0, want))
        self.assertGreaterEqual(report.vertices_interior, 2)

    def test_a_vertex_with_a_foreign_neighbour_just_outside_the_radius_stays_interior(self) -> None:
        # The boundary case: one part's vertex sits a hair beyond R from the other part.
        positions = [[0.0, 0.0, 0.0], [BLEND_RADIUS * 1.0001, 0.0, 0.0]]
        source = SkinBinding(positions, ["a", "b"], [0, 0, 0, 0, 1, 0, 0, 0],
                             [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0], joint_count=2)
        blended, report = blend_weights(source, 1.0)
        self.assertEqual(report.vertices_interior, 2)
        self.assertEqual(report.vertices_touched, 0)
        self.assertEqual(blended.skin_indices, source.skin_indices)
        self.assertEqual(blended.skin_weights, source.skin_weights)


class GridHash(unittest.TestCase):
    def test_grid_results_equal_brute_force_results_exactly(self) -> None:
        rng = random.Random(7)
        positions = [
            [rng.uniform(-0.02, 0.02), rng.uniform(-0.02, 0.02), rng.uniform(-0.02, 0.02)]
            for _ in range(400)
        ]
        grid = UniformGrid(positions, BLEND_RADIUS)
        for point in positions:
            got = sorted(grid.query(point, BLEND_RADIUS))
            want = sorted(brute_force_query(positions, point, BLEND_RADIUS))
            self.assertEqual(got, want)

    def test_a_query_touches_at_most_27_buckets(self) -> None:
        # One cell per radius is what bounds this. The bound is the reason the stage survives a real
        # mesh: brute force is O(n^2) and unusable past ~50k vertices.
        self.assertEqual(len(_CELL_OFFSETS), 27)
        rng = random.Random(11)
        positions = [
            [rng.uniform(-0.05, 0.05), rng.uniform(-0.05, 0.05), rng.uniform(-0.05, 0.05)]
            for _ in range(2000)
        ]
        grid = UniformGrid(positions, BLEND_RADIUS)
        queries = 0
        for point in positions:
            grid.query(point, BLEND_RADIUS)
            queries += 1
        self.assertLessEqual(grid.max_buckets_per_query, 27)
        self.assertEqual(grid.buckets_visited, queries * 27)

    def test_a_query_wider_than_the_cell_is_refused_not_silently_wrong(self) -> None:
        grid = UniformGrid([[0.0, 0.0, 0.0]], BLEND_RADIUS)
        with self.assertRaises(ValueError):
            grid.query([0.0, 0.0, 0.0], BLEND_RADIUS * 2)


class GateR2(unittest.TestCase):
    def test_a_blended_binding_passes_the_gate(self) -> None:
        blended, _ = blend_weights(Fixture().binding, 1.0)
        self.assertEqual(validate_binding(blended), [])

    def test_weights_renormalise_within_tolerance(self) -> None:
        blended, _ = blend_weights(Fixture().binding, 1.0)
        worst = max(
            abs(1.0 - math.fsum(slots(blended, index)[1]))
            for index in range(blended.vertex_count)
        )
        self.assertLessEqual(worst, WEIGHT_SUM_TOLERANCE)

    def test_every_index_stays_in_range_after_reduction(self) -> None:
        positions = [[0.0, 0.0, 0.0], [0.001, 0.0, 0.0], [0.002, 0.0, 0.0]]
        binding = SkinBinding(
            positions,
            ["a", "b", "b"],
            [0, 0, 0, 0, 1, 0, 0, 0, 2, 0, 0, 0],
            [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
            joint_count=3,
        )
        blended, _ = blend_weights(binding, 1.0)
        self.assertLessEqual(max(blended.skin_indices), blended.joint_count - 1)
        self.assertGreaterEqual(min(blended.skin_indices), 0)
        self.assertEqual(validate_binding(blended), [])

    def test_the_gate_actually_fails_a_broken_binding(self) -> None:
        # A gate that cannot fail proves nothing about the bindings that pass it.
        broken = SkinBinding(
            positions=[[0.0, 0.0, 0.0]],
            part_ids=["a"],
            skin_indices=[9, 0, 0, 0],
            skin_weights=[0.5, 0.0, 0.0, 0.0],
            joint_count=2,
        )
        failures = validate_binding(broken)
        self.assertTrue(any("sum(w)" in message for message in failures))
        self.assertTrue(any("outside" in message for message in failures))


class RadiusScalesWithHeight(unittest.TestCase):
    def test_the_same_figure_at_double_height_blends_identically(self) -> None:
        # R is a fraction of H, so doubling every coordinate and H must be invisible to the weights.
        # A radius in absolute units would silently stop reaching across the seam on a taller rig.
        small, _ = blend_weights(Fixture(height=1.0).binding, 1.0)
        large, _ = blend_weights(Fixture(height=2.0).binding, 2.0)
        for index in range(small.vertex_count):
            self.assertEqual(slots(large, index), slots(small, index))

    def test_a_caller_supplied_radius_is_the_one_recorded(self) -> None:
        _, report = blend_weights(Fixture().binding, 1.0, radius=0.012)
        self.assertEqual(report.radius_fraction, 0.012)
        self.assertAlmostEqual(report.radius_world, 0.012)

    def test_rejects_input_it_cannot_blend(self) -> None:
        fixture = Fixture()
        with self.assertRaises(ValueError):
            blend_weights(fixture.binding, 0.0)
        with self.assertRaises(ValueError):
            blend_weights(fixture.binding, 1.0, radius=0.0)
        with self.assertRaises(ValueError):
            blend_weights(
                SkinBinding([[0.0, 0.0, 0.0]], ["a"], [0, 0, 0, 0], [1.0, 0.0, 0.0], 1), 1.0
            )


class DoesNotMutateItsInput(unittest.TestCase):
    def test_the_source_binding_is_untouched(self) -> None:
        fixture = Fixture()
        before_indices = list(fixture.binding.skin_indices)
        before_weights = list(fixture.binding.skin_weights)
        before_positions = [list(point) for point in fixture.binding.positions]
        blend_weights(fixture.binding, 1.0)
        self.assertEqual(fixture.binding.skin_indices, before_indices)
        self.assertEqual(fixture.binding.skin_weights, before_weights)
        self.assertEqual(fixture.binding.positions, before_positions)


if __name__ == "__main__":
    unittest.main()
