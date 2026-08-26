#!/usr/bin/env python3
"""Tests for reading a GLB's own rig, built around the defect the module exists to remove.

The centrepiece is `TheSkinOrderDefect`. Everything else in this file is downstream of one claim:
that `skin.joints` -- not the node array, not a depth-first traversal -- is the ordering authority
for bones and inverse binds (§R0.3). So the fixture rig deliberately scrambles its skin order away
from its node order, and the first test asserts BOTH that the reader returns skin order AND that
traversal order would have produced a different, wrong mapping. Reading it in the wrong order is
how a rigged mesh comes out disjointed, and a test that only checked "we got six joints" would pass
through that defect untouched.

Every GLB here is synthesised in-process: real 12-byte header, real JSON chunk, real BIN chunk. The
suite depends on no external asset.

Pure Python 3.10+ stdlib.
"""

from __future__ import annotations

import io
import json
import math
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stage5_rig"))
sys.path.insert(0, str(ROOT.parent))

import clip_features  # noqa: E402
from glb_rig_reference import (  # noqa: E402
    CORRESPONDENCE_TOLERANCE,
    INVERSE_BIND_OMITTED,
    UnsupportedInterpolation,
    correspondence,
    derive_figure_height,
    joint_rest_positions,
    main,
    normalize_quat,
    read_rig,
    sample_clips,
    slerp,
)

GLB_MAGIC = b"glTF"
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


# ---------------------------------------------------------------------------------------------
# Minimal GLB writer -- the one investment that keeps every test asset-free
# ---------------------------------------------------------------------------------------------


class BinChunk:
    """Accumulates float data and hands back accessor indices, so a test names data, not offsets."""

    def __init__(self) -> None:
        self.data = bytearray()
        self.views: list[dict[str, Any]] = []
        self.accessors: list[dict[str, Any]] = []

    def add(self, values: Sequence[float], element_type: str, count: int, **extra: Any) -> int:
        while len(self.data) % 4:
            self.data.append(0)
        offset = len(self.data)
        payload = struct.pack(f"<{len(values)}f", *values)
        self.data.extend(payload)
        self.views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(payload)})
        accessor: dict[str, Any] = {
            "bufferView": len(self.views) - 1,
            "componentType": 5126,
            "count": count,
            "type": element_type,
        }
        accessor.update(extra)
        self.accessors.append(accessor)
        return len(self.accessors) - 1


def pack_glb(document: dict[str, Any], binary: bytes) -> bytes:
    """Write a real binary glTF: 12-byte header, padded JSON chunk, padded BIN chunk."""
    json_bytes = json.dumps(document).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    bin_bytes = bytes(binary)
    bin_bytes += b"\x00" * (-len(bin_bytes) % 4)
    body = struct.pack("<II", len(json_bytes), JSON_CHUNK) + json_bytes
    if bin_bytes:
        body += struct.pack("<II", len(bin_bytes), BIN_CHUNK) + bin_bytes
    return struct.pack("<4sII", GLB_MAGIC, 2, 12 + len(body)) + body


def translation_matrix(x: float, y: float, z: float) -> list[float]:
    """Column-major 4x4 -- translation lives at indices 12, 13, 14."""
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1]


def quat_z(degrees: float) -> tuple[float, float, float, float]:
    half = math.radians(degrees) / 2.0
    return (0.0, 0.0, math.sin(half), math.cos(half))


def nlerp(a: Sequence[float], b: Sequence[float], t: float) -> tuple[float, float, float, float]:
    """The normalised lerp that a naive implementation would use instead of slerp."""
    return normalize_quat(tuple(a[i] + (b[i] - a[i]) * t for i in range(4)))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------
# The fixture rig
#
# Node order (0..7) and skin order are DELIBERATELY different. Node names are exporter-style
# ("node_3"), never anatomical, so no test can accidentally pass by matching a name.
# ---------------------------------------------------------------------------------------------

# node index -> local translation
NODE_LAYOUT: dict[int, tuple[str, tuple[float, float, float], list[int]]] = {
    0: ("node_0", (0.0, 0.0, 0.0), [1, 7]),
    1: ("node_1", (0.0, 0.5, 0.0), [2, 3, 4, 5, 6]),   # hip
    2: ("node_2", (0.0, 0.4, 0.0), []),                # head
    3: ("node_3", (-0.3, 0.1, 0.0), []),               # hand.l
    4: ("node_4", (0.3, 0.1, 0.0), []),                # hand.r
    5: ("node_5", (-0.1, -0.5, 0.0), []),              # foot.l
    6: ("node_6", (0.1, -0.5, 0.0), []),               # foot.r
    7: ("node_7", (0.0, 0.0, 0.0), []),                # skinned mesh node
}

# Skin order is head, foot.l, hip, foot.r, hand.l, hand.r -- nothing like node order.
SKIN_JOINTS = [2, 5, 1, 6, 3, 4]
TRAVERSAL_ORDER = [1, 2, 3, 4, 5, 6]  # what a depth-first walk of the joint nodes would produce

LANDMARK_NODES = {"hip": 1, "head": 2, "hand.l": 3, "hand.r": 4, "foot.l": 5, "foot.r": 6}

# World rest positions implied by NODE_LAYOUT, used by the correspondence tests.
JOINT_WORLD = {
    1: (0.0, 0.5, 0.0),
    2: (0.0, 0.9, 0.0),
    3: (-0.3, 0.6, 0.0),
    4: (0.3, 0.6, 0.0),
    5: (-0.1, 0.0, 0.0),
    6: (0.1, 0.0, 0.0),
}


def build_glb(
    path: Path,
    *,
    skin_joints: Sequence[int] = SKIN_JOINTS,
    inverse_binds: bool = True,
    extra_skin_joints: Sequence[int] | None = None,
    animations: Sequence[dict[str, Any]] | None = None,
) -> Path:
    """Write the fixture rig. `animations` entries are {"name", "channels": [channel spec]}.

    A channel spec is {"node", "path", "times", "values", "interpolation"}; `values` is flat.
    """
    chunk = BinChunk()
    # A 1.0-high mesh so figure height H measures to 1.0 from the file's own bounds.
    positions = chunk.add(
        [-0.5, 0.0, -0.5, 0.5, 0.0, -0.5, 0.0, 1.0, 0.0],
        "VEC3",
        3,
        min=[-0.5, 0.0, -0.5],
        max=[0.5, 1.0, 0.5],
    )

    nodes: list[dict[str, Any]] = []
    for index in sorted(NODE_LAYOUT):
        name, translation, children = NODE_LAYOUT[index]
        node: dict[str, Any] = {"name": name, "translation": list(translation)}
        if children:
            node["children"] = list(children)
        nodes.append(node)
    nodes[7]["mesh"] = 0
    nodes[7]["skin"] = 0

    skins: list[dict[str, Any]] = [{"name": "skin_0", "joints": list(skin_joints)}]
    if inverse_binds:
        # Each matrix encodes the node index of the joint it belongs to, so an index-for-index
        # check has something to catch. (A real inverse bind is the inverse world bind matrix;
        # this stands in for one because the module under test only pairs, never interprets.)
        flat: list[float] = []
        for node_index in skin_joints:
            flat.extend(translation_matrix(node_index * 100.0, 0.0, 0.0))
        skins[0]["inverseBindMatrices"] = chunk.add(flat, "MAT4", len(skin_joints))
    if extra_skin_joints is not None:
        skins.append({"name": "skin_1", "joints": list(extra_skin_joints)})

    animation_records: list[dict[str, Any]] = []
    for animation in animations or []:
        samplers: list[dict[str, Any]] = []
        channels: list[dict[str, Any]] = []
        for spec in animation["channels"]:
            times = list(spec["times"])
            values = list(spec["values"])
            element_type = {"translation": "VEC3", "scale": "VEC3", "rotation": "VEC4"}[spec["path"]]
            components = 4 if element_type == "VEC4" else 3
            input_accessor = chunk.add(times, "SCALAR", len(times), min=[min(times)], max=[max(times)])
            output_accessor = chunk.add(values, element_type, len(values) // components)
            samplers.append(
                {
                    "input": input_accessor,
                    "output": output_accessor,
                    "interpolation": spec.get("interpolation", "LINEAR"),
                }
            )
            channels.append(
                {"sampler": len(samplers) - 1, "target": {"node": spec["node"], "path": spec["path"]}}
            )
        animation_records.append(
            {"name": animation["name"], "samplers": samplers, "channels": channels}
        )

    document: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "test_glb_rig_reference"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": nodes,
        "meshes": [{"name": "body", "primitives": [{"attributes": {"POSITION": positions}}]}],
        "skins": skins,
        "buffers": [{"byteLength": len(chunk.data)}],
        "bufferViews": chunk.views,
        "accessors": chunk.accessors,
    }
    if animation_records:
        document["animations"] = animation_records
    path.write_bytes(pack_glb(document, bytes(chunk.data)))
    return path


def hip_walk_animation(name: str = "clip_walk") -> dict[str, Any]:
    """Hip slides 1.0H forward in x over one second -- travel 1.0H, speed 1.0 H/s."""
    return {
        "name": name,
        "channels": [
            {
                "node": 1,
                "path": "translation",
                "times": [0.0, 1.0],
                "values": [0.0, 0.5, 0.0, 1.0, 0.5, 0.0],
            }
        ],
    }


class GlbFixtureMixin:
    def setUp(self) -> None:  # noqa: D102
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, **kwargs: Any) -> Path:
        name = kwargs.pop("name", "rig.glb")
        return build_glb(self.tmp / name, **kwargs)


# ---------------------------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------------------------


class TheSkinOrderDefect(GlbFixtureMixin, unittest.TestCase):
    """§R0.3: bones[i] is the node for skin.joints[i]. The ordering is the skin's, never the
    traversal's. This is the mechanism behind a mesh that comes out disjointed, so it is asserted
    from both sides: what the reader returns, and what the wrong reading would have returned."""

    def test_joints_are_returned_in_skin_order_and_traversal_order_would_be_wrong(self) -> None:
        rig = read_rig(self.write())

        returned = [joint.node_index for joint in rig.joints]
        self.assertEqual(returned, SKIN_JOINTS, "joints must come back in skin.joints order")
        self.assertEqual(
            [joint.skin_joint_index for joint in rig.joints],
            list(range(len(SKIN_JOINTS))),
            "skinJointIndex must be the slot in skin.joints, densely and in order",
        )

        # The wrong reading, spelled out: a depth-first walk of the same joint nodes.
        self.assertNotEqual(
            returned,
            TRAVERSAL_ORDER,
            "the fixture must actually differ from traversal order or this test proves nothing",
        )
        # Every slot where the two orders disagree is a bone that would deform the wrong vertices.
        disagreements = [
            slot for slot, node_index in enumerate(returned) if node_index != TRAVERSAL_ORDER[slot]
        ]
        self.assertEqual(len(disagreements), 6)
        self.assertEqual(
            rig.to_dict()["skins"][0]["jointOrder"],
            "skin.joints (§R0.3: the ordering is the skin's, never the traversal's)",
        )

    def test_joint_node_names_are_never_used_to_order_or_identify(self) -> None:
        rig = read_rig(self.write())
        # Exporter names carry no anatomy. The reader keeps them verbatim and orders by the skin.
        self.assertEqual(
            [joint.node_name for joint in rig.joints],
            [f"node_{index}" for index in SKIN_JOINTS],
        )


class InverseBindPairing(GlbFixtureMixin, unittest.TestCase):
    def test_inverse_bind_matrices_align_index_for_index_with_skin_joints(self) -> None:
        rig = read_rig(self.write())
        self.assertEqual(len(rig.inverse_bind_matrices), len(rig.joints))
        for joint, matrix in zip(rig.joints, rig.inverse_bind_matrices):
            # The fixture encodes the joint's node index in the matrix's translation.
            self.assertAlmostEqual(matrix[12], joint.node_index * 100.0, places=4)
        self.assertEqual(rig.inverse_bind_source, "accessor:1")

    def test_an_omitted_accessor_is_reported_not_silently_filled(self) -> None:
        rig = read_rig(self.write(inverse_binds=False, name="no-ibm.glb"))
        self.assertEqual(rig.inverse_bind_source, INVERSE_BIND_OMITTED)
        # glTF says identity, so identity is what is returned -- but the provenance says which.
        self.assertEqual(len(rig.inverse_bind_matrices), len(SKIN_JOINTS))
        for matrix in rig.inverse_bind_matrices:
            self.assertEqual(list(matrix), [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
        self.assertEqual(rig.to_dict()["inverseBindSource"], INVERSE_BIND_OMITTED)


class DeformVersusTechnical(GlbFixtureMixin, unittest.TestCase):
    """§R0.1: a node is a Bone IFF it appears in skin.joints. Clips target technical nodes too."""

    def test_channels_targeting_non_joint_nodes_are_counted_and_named(self) -> None:
        path = self.write(
            animations=[
                {
                    "name": "clip_mixed",
                    "channels": [
                        # node 1 is in skin.joints; node 0 (the container) is not.
                        {"node": 1, "path": "translation", "times": [0.0, 1.0],
                         "values": [0.0, 0.5, 0.0, 0.0, 0.6, 0.0]},
                        {"node": 0, "path": "translation", "times": [0.0, 1.0],
                         "values": [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]},
                    ],
                }
            ]
        )
        rig = read_rig(path)
        report = rig.deform_vs_technical
        self.assertEqual(report["status"], "evaluated")
        self.assertEqual(report["channelsTargetingJoints"], 1)
        self.assertEqual(report["channelsTargetingTechnicalNodes"], 1)
        self.assertEqual([entry["nodeIndex"] for entry in report["technicalNodes"]], [0])
        self.assertEqual([entry["nodeIndex"] for entry in report["deformNodes"]], [1])
        self.assertTrue(
            any("corrupts the skeleton's index space" in warning for warning in rig.warnings),
            rig.warnings,
        )

    def test_with_no_skin_the_classification_is_unevaluated_not_a_pass(self) -> None:
        # A GLB with clips but no skin has no membership test to run. CONTRACT_1.5.2: an absent
        # input reports `unevaluated`, never silently a pass.
        chunk = BinChunk()
        positions = chunk.add(
            [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0], "VEC3", 3,
            min=[0.0, 0.0, 0.0], max=[1.0, 1.0, 0.0],
        )
        times = chunk.add([0.0, 1.0], "SCALAR", 2, min=[0.0], max=[1.0])
        values = chunk.add([0.0, 0.0, 0.0, 0.0, 1.0, 0.0], "VEC3", 2)
        document = {
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"name": "node_0", "mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": positions}}]}],
            "animations": [
                {
                    "name": "clip_static",
                    "samplers": [{"input": times, "output": values, "interpolation": "LINEAR"}],
                    "channels": [{"sampler": 0, "target": {"node": 0, "path": "translation"}}],
                }
            ],
            "buffers": [{"byteLength": len(chunk.data)}],
            "bufferViews": chunk.views,
            "accessors": chunk.accessors,
        }
        path = self.tmp / "skinless.glb"
        path.write_bytes(pack_glb(document, bytes(chunk.data)))
        rig = read_rig(path)
        self.assertEqual(rig.deform_vs_technical["status"], "unevaluated")
        self.assertIsNone(rig.deform_vs_technical["channelsTargetingTechnicalNodes"])
        self.assertIn("no skin", rig.deform_vs_technical["reason"])
        self.assertIsNotNone(rig.structural_failure)


class MultipleSkins(GlbFixtureMixin, unittest.TestCase):
    def test_two_skins_leave_no_primary_and_nothing_is_guessed(self) -> None:
        rig = read_rig(self.write(extra_skin_joints=[3, 4], name="two-skins.glb"))
        self.assertEqual(len(rig.skins), 2)
        self.assertIsNone(rig.primary_skin)
        self.assertEqual(rig.joints, ())
        self.assertEqual(rig.inverse_bind_matrices, ())
        self.assertIn("2 skins", rig.structural_failure or "")
        # Both skins are still reported in full -- refusing to choose is not refusing to read.
        self.assertEqual([skin.skin_index for skin in rig.skins], [0, 1])
        self.assertEqual([joint.node_index for joint in rig.skins[1].joints], [3, 4])

    def test_the_cli_exits_1_on_multiple_skins(self) -> None:
        path = self.write(extra_skin_joints=[3, 4], name="two-skins.glb")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main([str(path)])
        self.assertEqual(code, 1)
        result = json.loads(buffer.getvalue())
        self.assertFalse(result["ok"])
        self.assertIsNone(result["primarySkinIndex"])
        self.assertEqual(result["joints"], [])
        self.assertTrue(any("skins" in error for error in result["errors"]), result["errors"])

    def test_naming_one_skin_makes_the_cli_succeed(self) -> None:
        path = self.write(extra_skin_joints=[3, 4], name="two-skins.glb")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main([str(path), "--skin", "0"])
        self.assertEqual(code, 0)
        result = json.loads(buffer.getvalue())
        self.assertEqual(result["primarySkinIndex"], 0)
        self.assertEqual([joint["nodeIndex"] for joint in result["joints"]], SKIN_JOINTS)

    def test_the_cli_exits_1_when_the_glb_has_no_skin(self) -> None:
        chunk = BinChunk()
        positions = chunk.add(
            [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0], "VEC3", 3,
            min=[0.0, 0.0, 0.0], max=[1.0, 1.0, 0.0],
        )
        document = {
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"name": "node_0", "mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": positions}}]}],
            "buffers": [{"byteLength": len(chunk.data)}],
            "bufferViews": chunk.views,
            "accessors": chunk.accessors,
        }
        path = self.tmp / "skinless.glb"
        path.write_bytes(pack_glb(document, bytes(chunk.data)))
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main([str(path)])
        self.assertEqual(code, 1)
        self.assertIn("no skin", json.loads(buffer.getvalue())["errors"][0])


# ---------------------------------------------------------------------------------------------
# Correspondence
# ---------------------------------------------------------------------------------------------


def bones_at_joints(offset: dict[int, tuple[float, float, float]] | None = None) -> list[dict[str, Any]]:
    """Procedural bones with real anatomical ids, sitting on the GLB's measured joint positions."""
    labels = {1: "hip", 2: "head", 3: "upper-arm-l", 4: "upper-arm-r", 5: "foot-l", 6: "foot-r"}
    offset = offset or {}
    bones = []
    for node_index, label in labels.items():
        base = JOINT_WORLD[node_index]
        shift = offset.get(node_index, (0.0, 0.0, 0.0))
        bones.append({"id": label, "position": [base[i] + shift[i] for i in range(3)]})
    return bones


class CorrespondenceTests(GlbFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.rig = read_rig(self.write())

    def test_the_fixtures_rest_positions_are_what_the_layout_says(self) -> None:
        positions = joint_rest_positions(self.rig)
        for joint in self.rig.joints:
            for axis in range(3):
                self.assertAlmostEqual(
                    positions[joint.skin_joint_index][axis], JOINT_WORLD[joint.node_index][axis], places=6
                )

    def test_a_bone_0_01H_away_matches_and_one_0_20H_away_does_not(self) -> None:
        near = correspondence(self.rig, bones_at_joints({1: (0.01, 0.0, 0.0)}), figure_height=1.0)
        hip = next(match for match in near.matches if match.bone_id == "hip")
        self.assertEqual(hip.node_index, 1)
        self.assertAlmostEqual(hip.distance_h, 0.01, places=6)
        self.assertAlmostEqual(hip.confidence, 0.8, places=6)
        self.assertTrue(near.usable, near.reason)

        far = correspondence(self.rig, bones_at_joints({1: (0.20, 0.0, 0.0)}), figure_height=1.0)
        hip = next(match for match in far.matches if match.bone_id == "hip")
        self.assertIsNone(hip.node_index)
        self.assertIsNone(hip.distance_h)
        self.assertEqual(hip.confidence, 0.0)
        self.assertIn(f"{CORRESPONDENCE_TOLERANCE}H", hip.reason)

    def test_an_unmatched_procedural_bone_makes_it_unusable_with_a_stated_reason(self) -> None:
        bones = bones_at_joints() + [{"id": "tail-tip", "position": [0.0, -5.0, 0.0]}]
        result = correspondence(self.rig, bones, figure_height=1.0)
        self.assertFalse(result.usable)
        self.assertEqual(result.unmatched_procedural_bones, ("tail-tip",))
        self.assertIn("tail-tip", result.reason)
        self.assertIn("not usable for retargeting", result.reason)

    def test_an_unmatched_glb_joint_makes_it_unusable_with_a_stated_reason(self) -> None:
        bones = [bone for bone in bones_at_joints() if bone["id"] != "foot-r"]
        result = correspondence(self.rig, bones, figure_height=1.0)
        self.assertFalse(result.usable)
        self.assertEqual(result.unmatched_procedural_bones, ())
        self.assertEqual([joint["nodeIndex"] for joint in result.unmatched_glb_joints], [6])
        self.assertIn("node_6", result.reason)
        self.assertIn("not usable for retargeting", result.reason)

    def test_correspondence_never_matches_by_name(self) -> None:
        bones = bones_at_joints()
        glb_names = {joint.node_name for joint in self.rig.joints}
        bone_ids = {str(bone["id"]) for bone in bones}
        self.assertEqual(
            glb_names & bone_ids,
            set(),
            "the fixture must share no name between the two rigs or this proves nothing",
        )
        result = correspondence(self.rig, bones, figure_height=1.0)
        self.assertTrue(result.usable, result.reason)
        self.assertEqual(
            {match.bone_id: match.node_name for match in result.matches},
            {
                "hip": "node_1",
                "head": "node_2",
                "upper-arm-l": "node_3",
                "upper-arm-r": "node_4",
                "foot-l": "node_5",
                "foot-r": "node_6",
            },
        )
        self.assertEqual(result.to_dict()["matchedBy"], "measured model-space position (never node names)")

    def test_two_bones_cannot_claim_the_same_glb_joint(self) -> None:
        bones = bones_at_joints() + [{"id": "hip-duplicate", "position": [0.001, 0.5, 0.0]}]
        result = correspondence(self.rig, bones, figure_height=1.0)
        claimed = [match.skin_joint_index for match in result.matches if match.skin_joint_index is not None]
        self.assertEqual(len(claimed), len(set(claimed)))
        self.assertIn("hip-duplicate", result.unmatched_procedural_bones)
        self.assertFalse(result.usable)

    def test_figure_height_is_measured_from_the_file_when_not_supplied(self) -> None:
        height, source = derive_figure_height(self.rig)
        self.assertAlmostEqual(height, 1.0, places=6)
        self.assertEqual(source, "meshPositionBoundsYExtent")
        result = correspondence(self.rig, bones_at_joints())
        self.assertAlmostEqual(result.figure_height, 1.0, places=6)

    def test_no_primary_skin_means_no_correspondence_and_a_reason(self) -> None:
        rig = read_rig(self.write(extra_skin_joints=[3, 4], name="two-skins.glb"))
        result = correspondence(rig, bones_at_joints(), figure_height=1.0)
        self.assertFalse(result.usable)
        self.assertIn("skins", result.reason)
        self.assertEqual(len(result.unmatched_procedural_bones), 6)


# ---------------------------------------------------------------------------------------------
# Sampling: the handoff into clip_features IS the deliverable
# ---------------------------------------------------------------------------------------------


class SampledClipHandoff(GlbFixtureMixin, unittest.TestCase):
    def test_the_payload_is_accepted_by_clip_features_and_measures(self) -> None:
        rig = read_rig(self.write(animations=[hip_walk_animation()]))
        payload = sample_clips(rig, LANDMARK_NODES)

        loaded = clip_features.load_payload(payload)
        self.assertAlmostEqual(loaded["figureHeight"], 1.0, places=6)
        self.assertEqual(len(loaded["clips"]), 1)

        features = clip_features.measure_clip(loaded["clips"][0], loaded["figureHeight"])
        self.assertEqual(features.sample_count, 25)
        self.assertAlmostEqual(features.duration, 1.0, places=6)
        self.assertAlmostEqual(features.travel, 1.0, places=6)
        self.assertAlmostEqual(features.speed, 1.0, places=6)
        self.assertAlmostEqual(features.rise, 0.0, places=9)
        self.assertFalse(features.scales_joints)
        self.assertEqual(clip_features.classify(features).primary, "run")
        self.assertEqual(features.source_name, "clip_walk")

    def test_the_payload_carries_its_own_provenance(self) -> None:
        rig = read_rig(self.write(animations=[hip_walk_animation()]))
        payload = sample_clips(rig, LANDMARK_NODES, sample_count=7)
        self.assertEqual(len(payload["clips"][0]["sampleTimes"]), 7)
        self.assertEqual(payload["provenance"]["jointCount"], 6)
        self.assertEqual(payload["provenance"]["figureHeightSource"], "meshPositionBoundsYExtent")
        self.assertNotIn("stance", payload["clips"][0])
        self.assertIn("omitted", payload["provenance"]["stance"])

    def test_missing_landmarks_are_refused_before_a_payload_is_built(self) -> None:
        rig = read_rig(self.write(animations=[hip_walk_animation()]))
        partial = {name: index for name, index in LANDMARK_NODES.items() if name != "foot.r"}
        with self.assertRaises(ValueError) as caught:
            sample_clips(rig, partial)
        self.assertIn("foot.r", str(caught.exception))

    def test_joint_scale_delta_reports_a_scaling_rig(self) -> None:
        # §1: scaleDelta is a tripwire. A rig that scales joints must be surfaced before anything
        # else proceeds, so it has to reach the payload as a real number.
        rig = read_rig(
            self.write(
                animations=[
                    {
                        "name": "clip_scale",
                        "channels": [
                            {
                                "node": 1,
                                "path": "scale",
                                "times": [0.0, 1.0],
                                "values": [1.0, 1.0, 1.0, 1.25, 1.0, 1.0],
                            }
                        ],
                    }
                ]
            )
        )
        payload = sample_clips(rig, LANDMARK_NODES, sample_count=5)
        self.assertAlmostEqual(max(payload["clips"][0]["jointScaleDelta"]), 0.25, places=6)
        features = clip_features.measure_clip(
            clip_features.load_payload(payload)["clips"][0], 1.0
        )
        self.assertTrue(features.scales_joints)


class Interpolation(GlbFixtureMixin, unittest.TestCase):
    def _hip_x(self, interpolation: str) -> list[float]:
        rig = read_rig(
            self.write(
                animations=[
                    {
                        "name": f"clip_{interpolation.lower()}",
                        "channels": [
                            {
                                "node": 1,
                                "path": "translation",
                                "times": [0.0, 1.0],
                                "values": [0.0, 0.5, 0.0, 2.0, 0.5, 0.0],
                                "interpolation": interpolation,
                            }
                        ],
                    }
                ],
                name=f"{interpolation}.glb",
            )
        )
        payload = sample_clips(rig, LANDMARK_NODES, sample_count=3)
        return [point[0] for point in payload["clips"][0]["landmarkPositions"]["hip"]]

    def test_step_holds_its_value_between_keys_and_linear_interpolates(self) -> None:
        step = self._hip_x("STEP")
        linear = self._hip_x("LINEAR")
        self.assertEqual(step[0], 0.0)
        self.assertEqual(step[1], 0.0, "STEP must hold the previous key's value at t = 0.5")
        self.assertAlmostEqual(step[2], 2.0, places=6)
        self.assertAlmostEqual(linear[1], 1.0, places=6, msg="LINEAR must interpolate at t = 0.5")
        self.assertNotAlmostEqual(step[1], linear[1], places=3)

    def test_the_rotation_path_uses_slerp_not_a_normalised_lerp(self) -> None:
        # A 170-degree arc sampled at t = 0.25, where slerp and nlerp visibly disagree. (At t = 0.5
        # they coincide by symmetry, which is why the quarter point is the honest place to look.)
        start = (0.0, 0.0, 0.0, 1.0)
        end = quat_z(170.0)
        rig = read_rig(
            self.write(
                animations=[
                    {
                        "name": "clip_turn",
                        "channels": [
                            {
                                "node": 1,
                                "path": "rotation",
                                "times": [0.0, 1.0],
                                "values": list(start) + list(end),
                            }
                        ],
                    }
                ],
                name="turn.glb",
            )
        )
        payload = sample_clips(rig, LANDMARK_NODES, sample_count=5)
        head = payload["clips"][0]["landmarkPositions"]["head"][1]  # t = 0.25

        def head_position(q: Sequence[float]) -> tuple[float, float]:
            # Every rotation here is about Z, so the angle reads straight off the quaternion.
            # The head sits at local (0, 0.4, 0) under the hip at world (0, 0.5, 0).
            angle = 2.0 * math.atan2(q[2], q[3])
            return (-0.4 * math.sin(angle), 0.5 + 0.4 * math.cos(angle))

        expected_slerp = head_position(slerp(start, end, 0.25))
        expected_nlerp = head_position(nlerp(start, end, 0.25))
        self.assertAlmostEqual(head[0], expected_slerp[0], places=6)
        self.assertAlmostEqual(head[1], expected_slerp[1], places=6)
        self.assertGreater(
            abs(expected_slerp[0] - expected_nlerp[0]),
            0.01,
            "the fixture must put slerp and nlerp measurably apart or this proves nothing",
        )
        self.assertGreater(abs(head[0] - expected_nlerp[0]), 0.01)

    def test_slerp_and_nlerp_disagree_on_the_arc_this_module_cares_about(self) -> None:
        start = (0.0, 0.0, 0.0, 1.0)
        end = quat_z(170.0)
        a = slerp(start, end, 0.25)
        b = nlerp(start, end, 0.25)
        angle_a = math.degrees(2.0 * math.atan2(a[2], a[3]))
        angle_b = math.degrees(2.0 * math.atan2(b[2], b[3]))
        self.assertAlmostEqual(angle_a, 42.5, places=4)
        self.assertGreater(abs(angle_a - angle_b), 5.0)

    def test_cubicspline_is_reported_unsupported_and_never_read_as_linear(self) -> None:
        start = [0.0, 0.5, 0.0]
        end = [2.0, 0.5, 0.0]
        # CUBICSPLINE output is [inTangent, value, outTangent] per key.
        values = [0.0, 0.0, 0.0] + start + [0.0, 0.0, 0.0] + [0.0, 0.0, 0.0] + end + [0.0, 0.0, 0.0]
        rig = read_rig(
            self.write(
                animations=[
                    {
                        "name": "clip_spline",
                        "channels": [
                            {
                                "node": 1,
                                "path": "translation",
                                "times": [0.0, 1.0],
                                "values": values,
                                "interpolation": "CUBICSPLINE",
                            }
                        ],
                    }
                ],
                name="spline.glb",
            )
        )
        # Visible from `read_rig` alone, before anyone asks for samples.
        self.assertEqual(rig.unsupported_interpolation_clips, ("clip_spline",))
        self.assertEqual(rig.clips[0].channels[0].interpolation, "CUBICSPLINE")

        with self.assertRaises(UnsupportedInterpolation) as caught:
            sample_clips(rig, LANDMARK_NODES)
        self.assertEqual(caught.exception.clip_name, "clip_spline")
        self.assertEqual(caught.exception.interpolation, "CUBICSPLINE")
        self.assertIn("rather than approximated as LINEAR", str(caught.exception))

    def test_the_cli_exits_1_when_sampling_hits_cubicspline(self) -> None:
        values = [0.0] * 3 + [0.0, 0.5, 0.0] + [0.0] * 6 + [2.0, 0.5, 0.0] + [0.0] * 3
        path = self.write(
            animations=[
                {
                    "name": "clip_spline",
                    "channels": [
                        {
                            "node": 1,
                            "path": "translation",
                            "times": [0.0, 1.0],
                            "values": values,
                            "interpolation": "CUBICSPLINE",
                        }
                    ],
                }
            ],
            name="spline.glb",
        )
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main([str(path), "--sample-clips", "--landmarks", json.dumps(LANDMARK_NODES)])
        self.assertEqual(code, 1)
        result = json.loads(buffer.getvalue())
        self.assertFalse(result["ok"])
        self.assertNotIn("sampledClips", result)
        self.assertTrue(any("CUBICSPLINE" in error for error in result["errors"]), result["errors"])


class PoseReturn(GlbFixtureMixin, unittest.TestCase):
    """§4: the loop rule needs a measured poseReturn, not a guess. Both directions are asserted."""

    def _pose_return(self, keys: Sequence[Sequence[float]], times: Sequence[float]) -> float:
        flat: list[float] = []
        for key in keys:
            flat.extend(key)
        rig = read_rig(
            self.write(
                animations=[
                    {
                        "name": "clip_pose",
                        "channels": [
                            {"node": 1, "path": "rotation", "times": list(times), "values": flat}
                        ],
                    }
                ],
                name=f"pose-{len(keys)}.glb",
            )
        )
        payload = sample_clips(rig, LANDMARK_NODES)
        return payload["clips"][0]["poseReturn"]

    def test_a_clip_whose_last_key_equals_its_first_returns_about_zero_degrees(self) -> None:
        identity = (0.0, 0.0, 0.0, 1.0)
        value = self._pose_return([identity, quat_z(90.0), identity], [0.0, 0.5, 1.0])
        self.assertLess(value, clip_features.POSE_RETURN_DEGREES)
        self.assertAlmostEqual(value, 0.0, places=6)

    def test_a_clip_ending_mid_rotation_does_not(self) -> None:
        identity = (0.0, 0.0, 0.0, 1.0)
        value = self._pose_return([identity, quat_z(90.0)], [0.0, 1.0])
        self.assertAlmostEqual(value, 90.0, places=4)
        self.assertGreater(value, clip_features.POSE_RETURN_DEGREES)

    def test_the_loop_rule_can_actually_decide_with_this_payload(self) -> None:
        # Without poseReturn the rule reports `loop: null`. The point of measuring it is that the
        # decision becomes decidable at all.
        rig = read_rig(self.write(animations=[hip_walk_animation()]))
        payload = sample_clips(rig, LANDMARK_NODES)
        loaded = clip_features.load_payload(payload)
        features = clip_features.measure_clip(loaded["clips"][0], loaded["figureHeight"])
        self.assertIsNotNone(features.pose_return)
        decision = clip_features.decide_loop(features)
        self.assertIsNotNone(decision.loop)


class ClipRecords(GlbFixtureMixin, unittest.TestCase):
    def test_clip_duration_is_the_largest_channel_time_max(self) -> None:
        rig = read_rig(
            self.write(
                animations=[
                    {
                        "name": "clip_two_channels",
                        "channels": [
                            {"node": 1, "path": "translation", "times": [0.0, 0.5],
                             "values": [0.0, 0.5, 0.0, 0.0, 0.6, 0.0]},
                            {"node": 2, "path": "translation", "times": [0.0, 2.25],
                             "values": [0.0, 0.4, 0.0, 0.0, 0.5, 0.0]},
                        ],
                    }
                ]
            )
        )
        clip = rig.clips[0]
        self.assertAlmostEqual(clip.duration, 2.25, places=6)
        self.assertEqual([channel.key_count for channel in clip.channels], [2, 2])
        self.assertEqual([channel.path for channel in clip.channels], ["translation", "translation"])
        self.assertEqual([channel.target_node_index for channel in clip.channels], [1, 2])
        self.assertAlmostEqual(clip.channels[1].time_max, 2.25, places=6)

    def test_skinned_mesh_nodes_are_the_nodes_carrying_both_mesh_and_skin(self) -> None:
        rig = read_rig(self.write())
        self.assertEqual(
            list(rig.skinned_mesh_nodes),
            [{"nodeIndex": 7, "nodeName": "node_7", "meshIndex": 0, "skinIndex": 0}],
        )


if __name__ == "__main__":
    unittest.main()
