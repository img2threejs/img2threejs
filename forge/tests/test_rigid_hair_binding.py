#!/usr/bin/env python3
"""Hair is rigidly parented, never geodesically skinned.

`bind()` weights every vertex of whatever mesh it is handed and has no notion of role, which is the
right contract for a distance solver and the wrong default for a character. Today nothing calls it
with a component tree -- `emit_rig.py` is still a milestone-0 proof that builds a single SkinnedMesh
-- so the defect has not shipped. The rule is being written before that wiring lands, not after.

`test_the_hazard_is_real` is the load-bearing one: it does the wrong thing on purpose and measures
the result, so the exclusion rests on a demonstrated number rather than on an argument.

Run: python3 forge/tests/test_rigid_hair_binding.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage5_rig"))

from geodesic_skinning import RIGID_ROLES, bind, partition_for_binding  # noqa: E402


def column(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float,
           base: int = 0) -> tuple[list[list[float]], list[int]]:
    """A closed axis-aligned box: eight vertices, twelve triangles."""
    vertices = [[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    faces = [
        (0, 1, 3), (0, 3, 2), (4, 7, 5), (4, 6, 7),
        (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
        (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3),
    ]
    indices = [base + i for face in faces for i in face]
    return vertices, indices


def head_on_neck_with_hair() -> dict:
    """Neck below, head above, hair capping the head -- one merged mesh, as a rig would receive it.

    Deliberately built as ONE solid so the geodesic field can actually propagate from the neck up
    through the head into the hair. That connectivity is the hazard being measured.
    """
    vertices: list[list[float]] = []
    indices: list[int] = []
    for box in (
        (-0.15, 0.15, 0.00, 1.00, -0.15, 0.15),   # neck
        (-0.40, 0.40, 1.00, 2.00, -0.40, 0.40),   # head
        (-0.42, 0.42, 2.00, 2.20, -0.42, 0.42),   # hair cap, sitting on the crown
    ):
        box_vertices, box_indices = column(*box, base=len(vertices))
        vertices.extend(box_vertices)
        indices.extend(box_indices)
    return {"vertices": vertices, "indices": indices}


NECK_BONE = {"id": "neck", "jointPos": [0.0, 0.0, 0.0], "tipPos": [0.0, 1.0, 0.0]}
HEAD_BONE = {"id": "head", "jointPos": [0.0, 1.0, 0.0], "tipPos": [0.0, 1.9, 0.0]}


def weight_of(result: dict, vertex: int, bone: str) -> float:
    """Total weight a named bone holds on one vertex.

    Summed rather than looked up: unused influence slots are padded with bone index 0 and weight
    0.0, so building a dict from `zip(indices, weights)` lets a pad silently overwrite the real
    entry for bone 0. That mistake reported the neck's share of the crown as 0.000 when it is 0.081.
    """
    slot = result["boneOrder"].index(bone)
    return sum(
        weight
        for index, weight in zip(result["skinIndices"][vertex], result["skinWeights"][vertex])
        if index == slot
    )


class TheHazardIsReal(unittest.TestCase):
    def test_the_neck_bone_reaches_the_crown_through_the_skull(self) -> None:
        """Skin the hair and the neck claims a real share of it, because the path runs through
        the head rather than around it.

        This is the measurement the exclusion rests on. Measured on this fixture at resolution 48:
        the geodesic distance from the neck to a crown vertex is 33.6 voxels against the head
        bone's 15.0, and with the falloff power of 3 that leaves the neck holding 8.1% of the
        crown. Turning the neck would drag the hair off the skull by that fraction -- and the
        `standProud` and `scalpExposure` guarantees are bind-pose checks that would never see it.
        """
        mesh = head_on_neck_with_hair()
        result = bind(mesh, [NECK_BONE, HEAD_BONE], resolution=48)

        crown = [i for i, v in enumerate(mesh["vertices"]) if v[1] >= 2.19]
        self.assertTrue(crown, "fixture has no crown vertices")

        worst = max(weight_of(result, index, "neck") for index in crown)
        self.assertGreater(
            worst, 0.05,
            "the neck bone was expected to hold a visible share of the crown; if it no longer "
            "does, re-derive whether hair still needs excluding rather than deleting the rule",
        )

    def test_every_influence_slot_is_accounted_for(self) -> None:
        """Guards the weight_of helper above -- the bug it documents cost a wrong conclusion."""
        mesh = head_on_neck_with_hair()
        result = bind(mesh, [NECK_BONE, HEAD_BONE], resolution=32)
        for index in range(len(mesh["vertices"])):
            total = weight_of(result, index, "neck") + weight_of(result, index, "head")
            self.assertAlmostEqual(total, 1.0, places=6)


class ThePartition(unittest.TestCase):
    def test_hair_is_excluded_and_reported_not_dropped(self) -> None:
        partition = partition_for_binding([
            {"id": "neck", "role": "body"},
            {"id": "head", "role": "head", "parent": "neck"},
            {"id": "hair-crown", "role": "hair", "parent": "head"},
        ])
        self.assertEqual(partition["skinned"], ["neck", "head"])
        self.assertEqual(len(partition["rigidlyParented"]), 1)
        self.assertEqual(partition["rigidlyParented"][0]["id"], "hair-crown")
        self.assertEqual(partition["rigidlyParented"][0]["parentJoint"], "head")

    def test_all_four_rigid_roles_are_excluded(self) -> None:
        partition = partition_for_binding(
            [{"id": "head", "role": "head"}]
            + [{"id": role, "role": role, "parent": "head"} for role in sorted(RIGID_ROLES)]
        )
        self.assertEqual(partition["skinned"], ["head"])
        self.assertEqual(
            {entry["id"] for entry in partition["rigidlyParented"]}, set(RIGID_ROLES)
        )

    def test_a_lock_rides_the_first_SKINNED_ancestor_not_its_immediate_parent(self) -> None:
        """A lock parented to a hair mass rides the head; the mass is not a joint."""
        partition = partition_for_binding([
            {"id": "head", "role": "head"},
            {"id": "hair-mass", "role": "hair", "parent": "head"},
            {"id": "hair-lock", "role": "hair", "parent": "hair-mass"},
        ])
        joints = {entry["id"]: entry["parentJoint"] for entry in partition["rigidlyParented"]}
        self.assertEqual(joints["hair-lock"], "head")
        self.assertEqual(joints["hair-mass"], "head")

    def test_an_orphan_rigid_component_warns(self) -> None:
        partition = partition_for_binding([{"id": "hair-crown", "role": "hair"}])
        self.assertEqual(len(partition["warnings"]), 1)
        self.assertIn("no skinned ancestor", partition["warnings"][0])
        self.assertIsNone(partition["rigidlyParented"][0]["parentJoint"])

    def test_a_parent_cycle_terminates(self) -> None:
        partition = partition_for_binding([
            {"id": "a", "role": "hair", "parent": "b"},
            {"id": "b", "role": "hair", "parent": "a"},
        ])
        self.assertEqual(len(partition["warnings"]), 2)

    def test_the_role_set_is_overridable(self) -> None:
        """A caller that genuinely wants skinned hair must say so explicitly."""
        partition = partition_for_binding(
            [{"id": "head", "role": "head"}, {"id": "hair", "role": "hair", "parent": "head"}],
            rigid_roles=frozenset(),
        )
        self.assertEqual(partition["skinned"], ["head", "hair"])
        self.assertEqual(partition["rigidlyParented"], [])

    def test_components_without_ids_are_skipped_not_crashed_on(self) -> None:
        partition = partition_for_binding([{"role": "hair"}, None, {"id": "head", "role": "head"}])
        self.assertEqual(partition["skinned"], ["head"])

    def test_role_matching_is_case_insensitive(self) -> None:
        partition = partition_for_binding([
            {"id": "head", "role": "head"},
            {"id": "hair", "role": "Hair", "parent": "head"},
        ])
        self.assertEqual(partition["skinned"], ["head"])


class BindReportsThePartition(unittest.TestCase):
    """The rule has to be reachable from `bind`, or it is a policy nobody is holding.

    An earlier version put `partition_for_binding` beside `bind` and left the two unconnected, so a
    caller doing the obvious thing -- hand `bind` a merged mesh -- got weights for the hair and no
    signal whatsoever that some of those weights should not exist.
    """

    COMPONENTS = [
        {"id": "neck", "role": "body"},
        {"id": "head", "role": "head", "parent": "neck"},
        {"id": "hair-crown", "role": "hair", "parent": "head"},
    ]

    def test_bind_carries_the_partition_when_given_components(self) -> None:
        result = bind(head_on_neck_with_hair(), [NECK_BONE, HEAD_BONE], resolution=24,
                      components=self.COMPONENTS)
        partition = result["bindingPartition"]
        self.assertIsNotNone(partition)
        self.assertEqual([e["id"] for e in partition["rigidlyParented"]], ["hair-crown"])

    def test_bind_names_the_rigid_components_it_found(self) -> None:
        result = bind(head_on_neck_with_hair(), [NECK_BONE, HEAD_BONE], resolution=24,
                      components=self.COMPONENTS)
        joined = " ".join(result["partitionWarnings"])
        self.assertIn("parented to a joint rather than skinned", joined)
        self.assertIn("hair", joined)

    def test_bind_without_components_behaves_exactly_as_before(self) -> None:
        """The parameter is additive; existing callers must not change behaviour."""
        plain = bind(head_on_neck_with_hair(), [NECK_BONE, HEAD_BONE], resolution=24)
        self.assertIsNone(plain["bindingPartition"])
        self.assertEqual(plain["partitionWarnings"], [])
        self.assertEqual(len(plain["skinWeights"]), len(head_on_neck_with_hair()["vertices"]))

    def test_a_tree_with_no_rigid_roles_produces_no_warning(self) -> None:
        result = bind(head_on_neck_with_hair(), [NECK_BONE, HEAD_BONE], resolution=24,
                      components=[{"id": "neck", "role": "body"}, {"id": "head", "role": "head"}])
        self.assertEqual(result["partitionWarnings"], [])
        self.assertEqual(result["bindingPartition"]["rigidlyParented"], [])


class BindActuallyExcludes(unittest.TestCase):
    """Reporting the partition is not enough. The obvious thing to do with a returned weight array
    is to use it, so weights that should not exist have to not exist.

    Rigid parenting expressed in the only language a skinned mesh speaks: weight 1.0 on the single
    joint the component rides, and nothing else.
    """

    COMPONENTS = [
        {"id": "neck", "role": "body"},
        {"id": "head", "role": "head", "parent": "neck"},
        {"id": "hair-crown", "role": "hair", "parent": "head"},
    ]
    OWNERS = ["neck"] * 8 + ["head"] * 8 + ["hair-crown"] * 8

    def mesh_with_owners(self) -> dict:
        mesh = head_on_neck_with_hair()
        mesh["vertexComponents"] = list(self.OWNERS)
        return mesh

    def hair_vertices(self) -> list[int]:
        return [i for i, owner in enumerate(self.OWNERS) if owner == "hair-crown"]

    def test_the_neck_loses_its_grip_on_the_crown_entirely(self) -> None:
        mesh = self.mesh_with_owners()
        without = bind(mesh, [NECK_BONE, HEAD_BONE], resolution=48)
        with_exclusion = bind(mesh, [NECK_BONE, HEAD_BONE], resolution=48,
                              components=self.COMPONENTS)
        hair = self.hair_vertices()

        self.assertGreater(max(weight_of(without, i, "neck") for i in hair), 0.05)
        self.assertEqual(max(weight_of(with_exclusion, i, "neck") for i in hair), 0.0)

    def test_rigid_vertices_ride_their_joint_at_full_weight(self) -> None:
        result = bind(self.mesh_with_owners(), [NECK_BONE, HEAD_BONE], resolution=48,
                      components=self.COMPONENTS)
        for index in self.hair_vertices():
            with self.subTest(vertex=index):
                self.assertEqual(weight_of(result, index, "head"), 1.0)

    def test_skinned_vertices_are_untouched(self) -> None:
        mesh = self.mesh_with_owners()
        plain = bind(mesh, [NECK_BONE, HEAD_BONE], resolution=48)
        excluded = bind(mesh, [NECK_BONE, HEAD_BONE], resolution=48, components=self.COMPONENTS)
        for index, owner in enumerate(self.OWNERS):
            if owner == "hair-crown":
                continue
            with self.subTest(vertex=index):
                self.assertEqual(plain["skinWeights"][index], excluded["skinWeights"][index])

    def test_the_count_of_pinned_vertices_is_reported(self) -> None:
        result = bind(self.mesh_with_owners(), [NECK_BONE, HEAD_BONE], resolution=48,
                      components=self.COMPONENTS)
        self.assertEqual(result["rigidPinnedVertexCount"], 8)

    def test_without_vertexComponents_the_exclusion_says_it_could_not_run(self) -> None:
        """The one thing worse than not excluding is claiming to have excluded."""
        result = bind(head_on_neck_with_hair(), [NECK_BONE, HEAD_BONE], resolution=24,
                      components=self.COMPONENTS)
        self.assertEqual(result["rigidPinnedVertexCount"], 0)
        self.assertTrue(any("could NOT be excluded" in w for w in result["partitionWarnings"]))

    def test_a_joint_that_is_not_among_the_bones_is_reported(self) -> None:
        result = bind(self.mesh_with_owners(), [NECK_BONE], resolution=24,
                      components=self.COMPONENTS)
        self.assertTrue(any("not among the bones" in w for w in result["partitionWarnings"]))
        self.assertEqual(result["rigidPinnedVertexCount"], 0)

    def test_weights_still_sum_to_one_after_pinning(self) -> None:
        result = bind(self.mesh_with_owners(), [NECK_BONE, HEAD_BONE], resolution=48,
                      components=self.COMPONENTS)
        for index in range(len(self.OWNERS)):
            with self.subTest(vertex=index):
                total = weight_of(result, index, "neck") + weight_of(result, index, "head")
                self.assertAlmostEqual(total, 1.0, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
