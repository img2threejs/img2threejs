"""The emitted SDF polygoniser must be a real isosurface mesher, not an occupancy mesher.

This exists because the original implementation emitted one axis-aligned quad per voxel face
wherever a solid voxel met an empty one. It read only the SIGN of the distance field, never the
value, so every vertex landed on a lattice corner and the output was blocks at every resolution --
and since the grid is capped at 64 cells, there was no setting at which it converged. A cast
figure came out of it stair-stepped on the torso, head, hands, boots and both cloth shells.

These are static contract checks on the generated TypeScript. The numeric check lives in
forge/tests/fixtures/sdf_mesher_probe.ts (needs node + three): on an analytic sphere it measures
max deviation at 0.042 of a cell, exactly radial normals, and zero backfacing triangles.
"""
from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "forge" / "stage3_build" / "generate_threejs_factory.py"


class SdfMesherContract(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SOURCE.read_text(encoding="utf-8")
        start = self.text.index("function polygonizeSdf")
        end = self.text.index("\n}", self.text.index("if (positions.length === 0)", start))
        self.body = self.text[start:end]

    def test_samples_a_corner_lattice_not_cell_centres(self) -> None:
        # An isosurface mesher needs the eight corners of each cell to interpolate between.
        self.assertIn("const side = resolution + 1", self.body)
        self.assertNotIn("(x + 0.5) * step.x", self.body)

    def test_interpolates_the_zero_crossing(self) -> None:
        # The defining property: the vertex position must depend on the distance VALUE.
        self.assertIn("va / denom", self.body)
        self.assertIn("crossings", self.body)

    def test_emits_no_axis_aligned_voxel_faces(self) -> None:
        # The old implementation's signature: a per-face `inside()` neighbour test.
        self.assertNotIn("if (!inside(x - 1, y, z)) addFace", self.body)
        self.assertNotIn("const inside = (x: number, y: number, z: number): boolean", self.body)

    def test_uses_analytic_gradient_normals(self) -> None:
        # Face-averaged normals show the grid through the shading on a dual mesh.
        self.assertIn("Analytic gradient normals", self.body)
        self.assertIn("geometry.setAttribute('normal'", self.body)

    def test_emits_uvs(self) -> None:
        # A mesh with no `uv` samples its base-colour map as white, so an SDF part renders as
        # chrome beside its lathed neighbours in bronze.
        self.assertIn("geometry.setAttribute('uv'", self.body)
        self.assertIn("geometry.setAttribute('uv1'", self.body)

    def test_winding_is_documented_against_the_inside_out_failure(self) -> None:
        # Reversed winding makes the front faces the interior, which on a metal reads as chrome.
        self.assertIn("bright chrome rather than as its own albedo", self.body)


if __name__ == "__main__":
    unittest.main()
