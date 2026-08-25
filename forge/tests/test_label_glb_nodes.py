"""The point of these tests is that a NAME can never become evidence.

The asset this was written against has 16 nodes called `root.0`..`root.15`, so any labeller that reads
names produces confident nonsense. Every assertion below is about measurement surviving and names not.
"""
from __future__ import annotations

import json
import math
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage1_intake"))

from label_glb_nodes import BANDS, label, main  # noqa: E402


def _glb(gltf: dict) -> bytes:
    payload = json.dumps(gltf).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)
    return (
        struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(payload))
        + struct.pack("<II", len(payload), 0x4E4F534A)
        + payload
    )


def _asset(nodes: list[dict], meshes: list[dict], accessors: list[dict]) -> dict:
    return {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "accessors": accessors,
    }


def _box(lo, hi):
    return {"type": "VEC3", "componentType": 5126, "count": 8, "min": list(lo), "max": list(hi)}


class TwoNodeFigure(unittest.TestCase):
    """A 2-unit figure: a boot at the bottom, a head at the top, both named meaninglessly."""

    def setUp(self):
        gltf = _asset(
            nodes=[
                {"name": "root.0", "mesh": 0, "translation": [0.0, 0.0, 0.0]},
                {"name": "root.1", "mesh": 1, "translation": [0.0, 0.0, 0.0]},
            ],
            meshes=[
                {"primitives": [{"attributes": {"POSITION": 0}}]},
                {"primitives": [{"attributes": {"POSITION": 1}}]},
            ],
            accessors=[_box((-0.2, 0.0, -0.1), (0.2, 0.2, 0.1)), _box((-0.15, 1.8, -0.1), (0.15, 2.0, 0.1))],
        )
        self.path = Path(tempfile.mkdtemp()) / "a.glb"
        self.path.write_bytes(_glb(gltf))
        self.report = label(self.path)

    def test_every_node_is_labelled(self):
        self.assertEqual(len(self.report["nodes"]), 2)
        self.assertEqual({n["nodeIndex"] for n in self.report["nodes"]}, {0, 1})

    def test_names_are_recorded_but_never_used_as_evidence(self):
        self.assertFalse(self.report["namesUsedAsEvidence"])
        self.assertEqual(self.report["observedNodeNames"], ["root.0", "root.1"])
        for node in self.report["nodes"]:
            self.assertEqual(node["evidence"]["method"], "measured-world-bounds")

    def test_the_bottom_node_bands_as_footwear_and_the_top_as_head(self):
        by_index = {n["nodeIndex"]: n for n in self.report["nodes"]}
        self.assertEqual(by_index[0]["band"], "footwear")
        self.assertEqual(by_index[1]["band"], "head")

    def test_status_refuses_to_claim_confirmation(self):
        self.assertEqual(self.report["status"], "hypothesis-requires-render-confirmation")

    def test_figure_height_is_measured_not_assumed(self):
        self.assertAlmostEqual(self.report["figureBounds"]["size"][1], 2.0, places=6)

    def test_every_node_carries_a_confidence_and_a_reason(self):
        for node in self.report["nodes"]:
            self.assertIsInstance(node["confidence"], float)
            self.assertTrue(node["evidence"]["reason"])


class SideDetection(unittest.TestCase):
    def _side_for(self, x_centre: float) -> str:
        gltf = _asset(
            nodes=[
                {"name": "n", "mesh": 0},
                {"name": "wide", "mesh": 1},
            ],
            meshes=[
                {"primitives": [{"attributes": {"POSITION": 0}}]},
                {"primitives": [{"attributes": {"POSITION": 1}}]},
            ],
            accessors=[
                _box((x_centre - 0.05, 0.0, -0.05), (x_centre + 0.05, 0.2, 0.05)),
                _box((-0.5, 0.0, -0.1), (0.5, 2.0, 0.1)),
            ],
        )
        path = Path(tempfile.mkdtemp()) / "b.glb"
        path.write_bytes(_glb(gltf))
        return next(n for n in label(path)["nodes"] if n["nodeIndex"] == 0)["side"]

    def test_positive_x_is_the_characters_own_left(self):
        """Matches CHARACTER_LEFT_SIGN in forge/_shared/chirality.py, which exists so the two
        cannot silently diverge."""
        self.assertEqual(self._side_for(0.4), "left")

    def test_negative_x_is_the_characters_own_right(self):
        self.assertEqual(self._side_for(-0.4), "right")

    def test_a_node_on_the_axis_has_no_side(self):
        self.assertEqual(self._side_for(0.0), "midline")


class HonestyAboutMergedShells(unittest.TestCase):
    def test_a_node_spanning_most_of_the_figure_is_called_a_merged_shell(self):
        """Naming the band its centroid lands in would be a confident wrong answer for a shell that
        covers hip through head."""
        gltf = _asset(
            nodes=[{"name": "root.0", "mesh": 0}],
            meshes=[{"primitives": [{"attributes": {"POSITION": 0}}]}],
            accessors=[_box((-0.3, 0.0, -0.1), (0.3, 2.0, 0.1))],
        )
        path = Path(tempfile.mkdtemp()) / "c.glb"
        path.write_bytes(_glb(gltf))
        node = label(path)["nodes"][0]
        self.assertEqual(node["band"], "multi-region-shell")
        self.assertLess(node["confidence"], 0.30)
        self.assertIn("45%", node["evidence"]["reason"])


class RotationIsHandled(unittest.TestCase):
    def test_a_rotated_node_uses_all_eight_corners(self):
        """Transforming only min and max is the classic wrong way to rotate an AABB.

        The rotation here is 45 deg, NOT 90, and that choice is the whole test. A 90 deg rotation maps
        an axis-aligned box onto another axis-aligned box, so the two-corner shortcut happens to return
        the correct answer and the test passes while the code is wrong -- verified by mutation, which is
        how an earlier version of this test was caught being worthless.

        At 45 deg the unit square's true x-extent is the diagonal, sqrt(2). The shortcut transforms only
        (0,0,0) and (1,1,0), which both land on x = 0, so it reports an x-extent of ZERO.
        """
        # Quaternion for a rotation of THETA about Z is (0, 0, sin(THETA/2), cos(THETA/2)).
        theta = math.radians(45.0)
        s, c = math.sin(theta / 2), math.cos(theta / 2)
        gltf = _asset(
            nodes=[{"name": "r", "mesh": 0, "rotation": [0.0, 0.0, s, c]}],
            meshes=[{"primitives": [{"attributes": {"POSITION": 0}}]}],
            accessors=[_box((0.0, 0.0, 0.0), (1.0, 1.0, 0.1))],
        )
        path = Path(tempfile.mkdtemp()) / "d.glb"
        path.write_bytes(_glb(gltf))
        size = label(path)["nodes"][0]["bounds"]["size"]
        self.assertAlmostEqual(size[0], 2 ** 0.5, places=5)
        self.assertAlmostEqual(size[1], 2 ** 0.5, places=5)


class ParentTransformsCompose(unittest.TestCase):
    def test_a_child_inherits_its_parent_translation(self):
        gltf = {
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [
                {"name": "parent", "translation": [0.0, 1.0, 0.0], "children": [1]},
                {"name": "child", "mesh": 0},
            ],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
            "accessors": [_box((-0.1, 0.0, -0.1), (0.1, 0.2, 0.1))],
        }
        path = Path(tempfile.mkdtemp()) / "e.glb"
        path.write_bytes(_glb(gltf))
        bounds = label(path)["nodes"][0]["bounds"]
        self.assertAlmostEqual(bounds["min"][1], 1.0, places=6)
        self.assertAlmostEqual(bounds["max"][1], 1.2, places=6)


class CliGate(unittest.TestCase):
    def test_min_confidence_gates_a_weak_map(self):
        """The whole reason the flag exists: a downstream per-region claim must be blockable."""
        gltf = _asset(
            nodes=[{"name": "root.0", "mesh": 0}],
            meshes=[{"primitives": [{"attributes": {"POSITION": 0}}]}],
            accessors=[_box((-0.3, 0.0, -0.1), (0.3, 2.0, 0.1))],
        )
        path = Path(tempfile.mkdtemp()) / "f.glb"
        path.write_bytes(_glb(gltf))
        self.assertEqual(main([str(path), "--min-confidence", "0.6"]), 1)
        self.assertEqual(main([str(path), "--min-confidence", "0.0"]), 0)

    def test_a_non_glb_fails_with_exit_two(self):
        path = Path(tempfile.mkdtemp()) / "g.glb"
        path.write_bytes(b"not a glb at all")
        self.assertEqual(main([str(path)]), 2)

    def test_bands_are_ordered_bottom_to_top(self):
        lows = [low for _name, low, _high in BANDS]
        self.assertEqual(lows, sorted(lows))


if __name__ == "__main__":
    unittest.main()
