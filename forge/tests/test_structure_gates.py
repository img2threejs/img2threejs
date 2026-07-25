from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stage4_review"))
sys.path.insert(0, str(ROOT / "stage3_build"))

from geometry_integrity import measure_geometry_integrity, mesh_edge_counts
from append_review import main as append_review_main
from orchestrate_passes import ledger_disagreements, pass_order


class StructureGateTest(unittest.TestCase):
    def test_coincident_vertices_are_merged_for_edge_counts(self) -> None:
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 0], [1, 0, 0], [0, 1, 0]]
        self.assertEqual(mesh_edge_counts(vertices, [0, 1, 2, 3, 5, 4])["boundaryEdges"], 0)

    def test_open_separate_geometry_fails_and_map_only_is_reported(self) -> None:
        result = measure_geometry_integrity({"meshes": [
            {"id": "hardware", "realization": "separate-geometry", "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "indices": [0, 1, 2]},
            {"id": "decal", "realization": "map-only"},
        ]})
        self.assertFalse(result["passed"])
        self.assertEqual(result["meshes"][0]["boundaryEdges"], 3)
        self.assertEqual(result["meshes"][1]["realization"], "map-only")

    def test_out_of_order_append_leaves_spec_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.json"
            path.write_text(json.dumps({"buildPasses": [{"id": "blockout"}, {"id": "material-pass"}], "reviewHistory": []}) + "\n")
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                append_review_main([str(path), "--pass-id", "material-pass", "--fidelity", "0.9", "--action", "stop", "--summary", "out of order"])
            self.assertEqual(path.read_bytes(), before)

    def test_sync_disagreement_names_uncredited_review(self) -> None:
        spec = {"buildPasses": [{"id": "blockout"}, {"id": "material-pass"}], "reviewHistory": [{"passId": "material-pass", "action": "continue"}]}
        self.assertTrue(any(item.startswith("material-pass:") for item in ledger_disagreements(spec, [])))


if __name__ == "__main__":
    unittest.main()
