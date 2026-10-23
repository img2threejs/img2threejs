#!/usr/bin/env python3
"""Tests for the self-intersection gate, each proven by breaking an input on purpose.

The centrepiece is `test_topology_gate_passes_the_mesh_this_gate_rejects`: it takes one mutated
mesh and asserts BOTH that the new gate flags it AND that `geometry_integrity.mesh_edge_counts`
still reports it as perfectly closed. That pairing is the whole argument for this module existing.
Everything else here is scaffolding around it.

The fixtures are built procedurally -- an icosphere by recursive subdivision, with midpoints welded
through a cache so the result is a genuinely closed 2-manifold rather than a pile of loose
triangles. Nothing is loaded from disk, so the fixtures cannot silently drift.

Pure Python 3.10+ stdlib. No pip installs.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "stage4_review"))

from geometry_integrity import mesh_edge_counts  # noqa: E402
from self_intersection import (  # noqa: E402
    MAX_SAMPLED_VERTICES,
    RAY_DIRECTIONS,
    _DirectionGrid,
    analyze_mesh,
    analyze_meshes,
    main,
)


def icosphere(subdivisions: int = 2) -> tuple[list[list[float]], list[int]]:
    """A closed unit icosphere: subdivided icosahedron, midpoints welded via a cache."""
    phi = (1.0 + 5.0 ** 0.5) / 2.0
    raw = [
        (-1, phi, 0), (1, phi, 0), (-1, -phi, 0), (1, -phi, 0),
        (0, -1, phi), (0, 1, phi), (0, -1, -phi), (0, 1, -phi),
        (phi, 0, -1), (phi, 0, 1), (-phi, 0, -1), (-phi, 0, 1),
    ]
    vertices: list[list[float]] = []
    for point in raw:
        length = sum(value * value for value in point) ** 0.5
        vertices.append([value / length for value in point])
    faces = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
        (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
        (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
        (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
    ]
    cache: dict[tuple[int, int], int] = {}

    def midpoint(a: int, b: int) -> int:
        key = (min(a, b), max(a, b))
        if key not in cache:
            point = [(vertices[a][axis] + vertices[b][axis]) / 2.0 for axis in range(3)]
            length = sum(value * value for value in point) ** 0.5
            cache[key] = len(vertices)
            vertices.append([value / length for value in point])
        return cache[key]

    for _ in range(subdivisions):
        next_faces: list[tuple[int, int, int]] = []
        for a, b, c in faces:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            next_faces += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces = next_faces

    indices: list[int] = []
    for face in faces:
        indices += list(face)
    return vertices, indices


def punch_through(
    vertices: list[list[float]],
    cap_height: float = 0.7,
    push: float = 2.6,
) -> list[list[float]]:
    """The production defect, reproduced: push a polar cap inward past the far surface.

    On a unit sphere the outward normal is the position itself, so pushing a cap along -normal by
    2.6 -- more than the sphere's diameter of 2 -- sends it through the centre and out the opposite
    side. The result is a funnel of stretched triangles running clean through the volume, exactly
    like an eye socket recessed deeper than the skull is thick. No index is touched, so the mesh's
    connectivity, and therefore its topology, is untouched too.
    """
    moved: list[list[float]] = []
    for vertex in vertices:
        if vertex[2] >= cap_height:
            moved.append([value - value * push for value in vertex])
        else:
            moved.append(list(vertex))
    return moved


def surface_net(sdf, n: int = 16, bound: float = 1.1, with_normals: bool = True) -> dict:
    """Naive surface nets over [-bound, bound]^3, mirroring the emitted `polygonizeSdf`.

    One vertex per sign-changing cell at the mean of its edge crossings, one quad (split into two
    triangles) per sign-changing grid edge, normals from the central-difference field gradient.
    This is the mesh class -- smooth, closed, but with SDF-union creases and non-star-shaped
    bodies -- that the gate used to reject on a clean fox build.
    """
    step = 2.0 * bound / n
    corner = lambda i, j, k: (-bound + i * step, -bound + j * step, -bound + k * step)  # noqa: E731
    field = {
        (i, j, k): sdf(*corner(i, j, k))
        for i in range(n + 1) for j in range(n + 1) for k in range(n + 1)
    }
    offsets = [(i, j, k) for i in (0, 1) for j in (0, 1) for k in (0, 1)]
    cube_edges = [(a, b) for a in offsets for b in offsets if a < b and sum(abs(x - y) for x, y in zip(a, b)) == 1]
    vertices: list[list[float]] = []
    normals: list[list[float]] = []
    cell_vertex: dict[tuple[int, int, int], int] = {}
    g = step * 0.25
    for cell in [(i, j, k) for i in range(n) for j in range(n) for k in range(n)]:
        crossings = []
        for a, b in cube_edges:
            ca = tuple(c + o for c, o in zip(cell, a))
            cb = tuple(c + o for c, o in zip(cell, b))
            fa, fb = field[ca], field[cb]
            if (fa <= 0) != (fb <= 0):
                t = fa / (fa - fb)
                pa, pb = corner(*ca), corner(*cb)
                crossings.append([pa[x] + t * (pb[x] - pa[x]) for x in range(3)])
        if crossings:
            cell_vertex[cell] = len(vertices)
            point = [sum(c[x] for c in crossings) / len(crossings) for x in range(3)]
            vertices.append(point)
            grad = [
                sdf(*[point[y] + (g if y == x else 0.0) for y in range(3)])
                - sdf(*[point[y] - (g if y == x else 0.0) for y in range(3)])
                for x in range(3)
            ]
            length = sum(c * c for c in grad) ** 0.5 or 1.0
            normals.append([c / length for c in grad])
    indices: list[int] = []
    for axis in range(3):
        u, v = (axis + 1) % 3, (axis + 2) % 3
        for start, value in field.items():
            if start[axis] >= n or not (1 <= start[u] <= n - 1 and 1 <= start[v] <= n - 1):
                continue
            end = list(start)
            end[axis] += 1
            if (value <= 0) == (field[tuple(end)] <= 0):
                continue
            quad = []
            for du, dv in ((-1, -1), (0, -1), (0, 0), (-1, 0)):
                around = list(start)
                around[u] += du
                around[v] += dv
                quad.append(cell_vertex[tuple(around)])
            if value > 0:
                quad.reverse()
            indices += [quad[0], quad[1], quad[2], quad[0], quad[2], quad[3]]
    mesh = {"vertices": vertices, "indices": indices}
    if with_normals:
        mesh["normals"] = normals
    return mesh


def torus_sdf(x: float, y: float, z: float) -> float:
    """Not star-shaped: from the centroid, the inner ring's outward direction points into the tube."""
    return (((x * x + z * z) ** 0.5 - 0.65) ** 2 + y * y) ** 0.5 - 0.25


def crease_sdf(x: float, y: float, z: float) -> float:
    """A hard SDF union (min) of two spheres: the concave crease where they meet."""
    a = ((x + 0.3) ** 2 + y * y + z * z) ** 0.5 - 0.5
    b = ((x - 0.3) ** 2 + (y - 0.1) ** 2 + (z - 0.05) ** 2) ** 0.5 - 0.35
    return min(a, b)


class CleanMesh(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.vertices, cls.indices = icosphere(2)

    def test_closed_convex_mesh_is_clean(self) -> None:
        result = analyze_mesh({"name": "icosphere", "vertices": self.vertices, "indices": self.indices})
        self.assertTrue(result["analyzed"], result["error"])
        self.assertFalse(result["selfIntersecting"])
        self.assertEqual(result["insideVertexCount"], 0)
        self.assertEqual(result["worstRegions"], [])
        # Undecided samples are a fact about the ray casts, not a hiding place: on a clean convex
        # sphere they must be a rounding error, not a way to reach a clean verdict by abstention.
        self.assertLess(result["undecidedVertexCount"], result["sampledVertexCount"] // 20)
        self.assertEqual(
            result["outsideVertexCount"] + result["insideVertexCount"] + result["undecidedVertexCount"],
            result["sampledVertexCount"],
        )

    def test_supplied_normals_are_used_and_reported(self) -> None:
        # On a unit sphere the vertex normal is the position, so this is the exact normal set.
        result = analyze_mesh({
            "vertices": self.vertices,
            "indices": self.indices,
            "normals": [list(vertex) for vertex in self.vertices],
        })
        self.assertEqual(result["normalSource"], "vertexNormals")
        self.assertEqual(result["normalFallbackCount"], 0)
        self.assertFalse(result["selfIntersecting"])

    def test_face_normals_are_derived_when_normals_absent(self) -> None:
        result = analyze_mesh({"vertices": self.vertices, "indices": self.indices})
        self.assertEqual(result["normalSource"], "faceNormals")
        self.assertEqual(result["normalFallbackCount"], 0)

    def test_wrong_length_normals_fall_back_rather_than_trusting_them(self) -> None:
        result = analyze_mesh({
            "vertices": self.vertices,
            "indices": self.indices,
            "normals": [[0.0, 0.0, 1.0]],
        })
        self.assertEqual(result["normalSource"], "faceNormals")
        self.assertFalse(result["selfIntersecting"])

    def test_inward_wound_mesh_without_normals_is_still_clean(self) -> None:
        # Derived normals follow the winding, so an inward-wound mesh must be re-oriented by its
        # signed volume, or every sample is stepped inward and the whole mesh reads as folded.
        flipped = [index for i in range(0, len(self.indices), 3) for index in self.indices[i:i + 3][::-1]]
        result = analyze_mesh({"vertices": self.vertices, "indices": flipped})
        self.assertFalse(result["selfIntersecting"], result["worstRegions"])

    def test_epsilon_scales_with_the_model(self) -> None:
        big = [[value * 1000.0 for value in vertex] for vertex in self.vertices]
        small = analyze_mesh({"vertices": self.vertices, "indices": self.indices})
        large = analyze_mesh({"vertices": big, "indices": self.indices})
        self.assertAlmostEqual(large["epsilon"] / small["epsilon"], 1000.0, places=6)
        # The verdict is the point: an absolute epsilon would be wrong at one of these two scales.
        self.assertFalse(small["selfIntersecting"])
        self.assertFalse(large["selfIntersecting"])
        self.assertEqual(large["insideVertexCount"], 0)


class SelfIntersectionRegression(unittest.TestCase):
    """The defect that got through every existing gate."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.vertices, cls.indices = icosphere(2)
        cls.broken = punch_through(cls.vertices)

    def test_surface_pushed_through_the_far_side_is_caught(self) -> None:
        result = analyze_mesh({"name": "punched", "vertices": self.broken, "indices": self.indices})
        self.assertTrue(result["analyzed"], result["error"])
        self.assertTrue(result["selfIntersecting"])
        self.assertGreater(result["insideVertexCount"], 0)
        self.assertTrue(result["worstRegions"])
        for region in result["worstRegions"]:
            self.assertEqual(len(region["position"]), 3)
            self.assertIn("vertexIndex", region)

    def test_topology_gate_passes_the_mesh_this_gate_rejects(self) -> None:
        """The single load-bearing test: same mesh, old gate clean, new gate rejects.

        Pushing vertices around changes no connectivity, so `mesh_edge_counts` reads the mutated
        mesh as watertight and manifold -- 0 boundary edges, 0 non-manifold edges -- while the
        surface is running straight through its own volume. This is why counting edges cannot
        substitute for measuring geometry.
        """
        topology = mesh_edge_counts(self.broken, self.indices)
        self.assertEqual(topology["boundaryEdges"], 0)
        self.assertEqual(topology["nonManifoldEdges"], 0)

        clean_topology = mesh_edge_counts(self.vertices, self.indices)
        self.assertEqual(clean_topology["boundaryEdges"], 0)
        self.assertEqual(clean_topology["nonManifoldEdges"], 0)
        # Identical topology readings, opposite verdicts from this module.
        self.assertEqual(topology["edgeCount"], clean_topology["edgeCount"])

        self.assertFalse(
            analyze_mesh({"vertices": self.vertices, "indices": self.indices})["selfIntersecting"]
        )
        self.assertTrue(
            analyze_mesh({"vertices": self.broken, "indices": self.indices})["selfIntersecting"]
        )

    def test_aggregate_reports_the_offending_mesh(self) -> None:
        result = analyze_meshes([
            {"name": "clean", "vertices": self.vertices, "indices": self.indices},
            {"name": "punched", "vertices": self.broken, "indices": self.indices},
        ])
        self.assertTrue(result["selfIntersecting"])
        self.assertEqual(result["meshCount"], 2)
        self.assertEqual(result["errors"], [])
        by_name = {report["name"]: report for report in result["meshes"]}
        self.assertFalse(by_name["clean"]["selfIntersecting"])
        self.assertTrue(by_name["punched"]["selfIntersecting"])


class SurfaceNetFalsePositives(unittest.TestCase):
    """Regression: a clean surface-net fox build read 295/1968 samples "inside"; a generalized
    winding number found 0. Two independent causes, one test each.
    """

    def test_non_star_shaped_mesh_without_normals_is_clean(self) -> None:
        # Cause 1: with no supplied normals the gate stepped each vertex away from the CENTROID.
        # On a torus (or a cape shell, a curled tail) that direction points into the solid for the
        # whole inner side, so ~a quarter of the samples were stepped inside and flagged.
        result = analyze_mesh(surface_net(torus_sdf, with_normals=False))
        self.assertFalse(result["selfIntersecting"], result["worstRegions"])

    def test_concave_union_crease_is_clean(self) -> None:
        # Cause 2: the gate masked every triangle incident to the sample vertex. In a concave
        # crease a ray from just above the surface legitimately ENTERS through one of those
        # triangles; masking it dropped the entry, kept the exit, and flipped the parity. The
        # gradient normals here are exact, so this fails on masking alone.
        result = analyze_mesh(surface_net(crease_sdf, n=20))
        self.assertEqual(result["normalSource"], "vertexNormals")
        self.assertFalse(result["selfIntersecting"], result["worstRegions"])

    def test_a_punched_surface_net_is_still_caught(self) -> None:
        # Negative control: the fixes remove false positives, not the gate's sight. Push the top
        # of the tube (0.5 thick) down 0.6, through and out of its own underside.
        mesh = surface_net(torus_sdf, with_normals=False)
        mesh["vertices"] = [
            [x, y - 0.6, z] if y > 0.15 else [x, y, z] for x, y, z in mesh["vertices"]
        ]
        self.assertTrue(analyze_mesh(mesh)["selfIntersecting"])


class ParityIsReal(unittest.TestCase):
    """A parity test that never counts a crossing agrees with every clean mesh, for the wrong reason.

    These two check the machinery itself rather than a verdict: that the ray really does count
    crossings, and that the broad-phase grid really does hand it the triangles to count.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.vertices, cls.indices = icosphere(3)

    def test_inverted_normals_put_every_sample_inside(self) -> None:
        # Flipping the supplied normals steps each sample INWARD instead of outward, so the point
        # under test is genuinely inside the sphere. Everything must read inside. If crossings were
        # being missed, this would come back clean and expose the whole gate as vacuous.
        result = analyze_mesh({
            "vertices": self.vertices,
            "indices": self.indices,
            "normals": [[-value for value in vertex] for vertex in self.vertices],
        })
        self.assertTrue(result["selfIntersecting"])
        self.assertGreater(result["insideVertexCount"], result["sampledVertexCount"] * 0.95)

    def test_every_vertex_gets_broad_phase_candidates(self) -> None:
        # Regression: the query used to bail out when a point projected one cell past the grid's
        # end, which is exactly where the vertex defining the projection's maximum lands. It
        # returned no candidates, so no crossings, so a confident "outside" for the samples at the
        # silhouette's extremes.
        triangles = [tuple(self.indices[i:i + 3]) for i in range(0, len(self.indices), 3)]
        for direction in RAY_DIRECTIONS:
            grid = _DirectionGrid(direction, [tuple(v) for v in self.vertices], triangles)
            for vertex in self.vertices:
                self.assertTrue(
                    grid.candidates(tuple(vertex)),
                    f"no candidate triangles for {vertex} along {direction}",
                )


class IndexEncodings(unittest.TestCase):
    def test_flat_and_grouped_indices_agree(self) -> None:
        vertices, flat = icosphere(2)
        grouped = [flat[i:i + 3] for i in range(0, len(flat), 3)]
        first = analyze_mesh({"vertices": vertices, "indices": flat})
        second = analyze_mesh({"vertices": vertices, "indices": grouped})
        self.assertEqual(first["triangleCount"], second["triangleCount"])
        self.assertEqual(first["insideVertexCount"], second["insideVertexCount"])
        self.assertEqual(first["undecidedVertexCount"], second["undecidedVertexCount"])
        self.assertFalse(first["selfIntersecting"])

    def test_both_encodings_catch_the_defect(self) -> None:
        vertices, flat = icosphere(2)
        broken = punch_through(vertices)
        grouped = [flat[i:i + 3] for i in range(0, len(flat), 3)]
        self.assertTrue(analyze_mesh({"vertices": broken, "indices": flat})["selfIntersecting"])
        self.assertTrue(analyze_mesh({"vertices": broken, "indices": grouped})["selfIntersecting"])


class SamplingHonesty(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.vertices, cls.indices = icosphere(2)

    def test_full_coverage_is_reported_as_stride_one(self) -> None:
        result = analyze_mesh({"vertices": self.vertices, "indices": self.indices})
        self.assertLess(len(self.vertices), MAX_SAMPLED_VERTICES)
        self.assertEqual(result["samplingStride"], 1)
        self.assertEqual(result["sampledVertexCount"], len(self.vertices))
        self.assertEqual(result["totalVertexCount"], len(self.vertices))

    def test_budget_exceeded_reports_a_real_stride(self) -> None:
        budget = 50
        result = analyze_mesh({"vertices": self.vertices, "indices": self.indices}, max_samples=budget)
        self.assertGreater(len(self.vertices), budget)
        self.assertGreater(result["samplingStride"], 1)
        self.assertLessEqual(result["sampledVertexCount"], budget)
        self.assertEqual(result["totalVertexCount"], len(self.vertices))
        # The claim that matters: the result never pretends the whole mesh was inspected.
        self.assertLess(result["sampledVertexCount"], result["totalVertexCount"])

    def test_sampling_is_deterministic(self) -> None:
        mesh = {"vertices": self.vertices, "indices": self.indices}
        first = analyze_mesh(mesh, max_samples=137)
        second = analyze_mesh(mesh, max_samples=137)
        self.assertEqual(first["sampledVertexCount"], second["sampledVertexCount"])
        self.assertEqual(first["insideVertexCount"], second["insideVertexCount"])
        self.assertEqual(first["worstRegions"], second["worstRegions"])


class DegenerateInput(unittest.TestCase):
    def _expect_error(self, mesh: dict) -> dict:
        result = analyze_mesh(mesh)
        self.assertFalse(result["analyzed"])
        self.assertTrue(result["error"])
        self.assertFalse(result["selfIntersecting"])
        return result

    def test_empty_vertices(self) -> None:
        self._expect_error({"vertices": [], "indices": [0, 1, 2]})

    def test_missing_vertices(self) -> None:
        self._expect_error({"indices": [0, 1, 2]})

    def test_empty_indices(self) -> None:
        vertices, _ = icosphere(0)
        self._expect_error({"vertices": vertices, "indices": []})

    def test_missing_indices(self) -> None:
        vertices, _ = icosphere(0)
        self._expect_error({"vertices": vertices})

    def test_index_count_not_a_multiple_of_three(self) -> None:
        vertices, indices = icosphere(0)
        result = self._expect_error({"vertices": vertices, "indices": indices[:-1]})
        self.assertIn("multiple of three", result["error"])

    def test_grouped_indices_with_a_short_group(self) -> None:
        vertices, indices = icosphere(0)
        grouped = [indices[i:i + 3] for i in range(0, len(indices), 3)]
        grouped[4] = grouped[4][:2]
        self._expect_error({"vertices": vertices, "indices": grouped})

    def test_index_out_of_range(self) -> None:
        vertices, indices = icosphere(0)
        broken = list(indices)
        broken[0] = len(vertices) + 5
        result = self._expect_error({"vertices": vertices, "indices": broken})
        self.assertIn("out of range", result["error"])

    def test_two_dimensional_vertex(self) -> None:
        vertices, indices = icosphere(0)
        vertices[0] = [0.0, 1.0]
        self._expect_error({"vertices": vertices, "indices": indices})

    def test_zero_extent_mesh(self) -> None:
        result = self._expect_error({"vertices": [[0.0, 0.0, 0.0]] * 3, "indices": [0, 1, 2]})
        self.assertIn("extent", result["error"])

    def test_non_dict_mesh_entry_does_not_abort_the_batch(self) -> None:
        vertices, indices = icosphere(1)
        result = analyze_meshes(["not a mesh", {"name": "real", "vertices": vertices, "indices": indices}])
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["meshCount"], 2)
        self.assertTrue(result["meshes"][1]["analyzed"])


class CommandLine(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        cls.vertices, cls.indices = icosphere(2)

    def _write(self, name: str, payload) -> Path:
        path = self.tmp / name
        path.write_text(json.dumps(payload))
        return path

    def _run(self, argv: list[str]) -> tuple[int, str]:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(io.StringIO()):
            code = main(argv)
        return code, stream.getvalue()

    def test_clean_mesh_exits_zero(self) -> None:
        path = self._write("clean.json", {"meshes": [{"name": "clean", "vertices": self.vertices, "indices": self.indices}]})
        code, output = self._run([str(path)])
        self.assertEqual(code, 0)
        self.assertIn("no self-intersection", output)

    def test_self_intersecting_mesh_exits_one(self) -> None:
        broken = punch_through(self.vertices)
        path = self._write("broken.json", [{"name": "punched", "vertices": broken, "indices": self.indices}])
        code, output = self._run([str(path)])
        self.assertEqual(code, 1)
        self.assertIn("SELF-INTERSECTION DETECTED", output)

    def test_json_flag_emits_parseable_output(self) -> None:
        path = self._write("single.json", {"name": "solo", "vertices": self.vertices, "indices": self.indices})
        code, output = self._run([str(path), "--json"])
        self.assertEqual(code, 0)
        parsed = json.loads(output)
        self.assertEqual(parsed["meshCount"], 1)
        self.assertEqual(parsed["meshes"][0]["name"], "solo")
        self.assertEqual(parsed["meshes"][0]["samplingStride"], 1)

    def test_bad_mesh_exits_two(self) -> None:
        path = self._write("bad.json", {"meshes": [{"name": "bad", "vertices": [], "indices": []}]})
        code, _ = self._run([str(path)])
        self.assertEqual(code, 2)

    def test_missing_file_exits_two(self) -> None:
        code, _ = self._run([str(self.tmp / "nope.json")])
        self.assertEqual(code, 2)

    def test_unusable_payload_exits_two(self) -> None:
        path = self._write("scalar.json", 17)
        code, _ = self._run([str(path)])
        self.assertEqual(code, 2)

    def test_max_samples_flag_is_honoured(self) -> None:
        path = self._write("budget.json", {"name": "solo", "vertices": self.vertices, "indices": self.indices})
        code, output = self._run([str(path), "--json", "--max-samples", "40"])
        self.assertEqual(code, 0)
        parsed = json.loads(output)
        self.assertGreater(parsed["meshes"][0]["samplingStride"], 1)
        self.assertLessEqual(parsed["meshes"][0]["sampledVertexCount"], 40)


class Performance(unittest.TestCase):
    def test_five_thousand_triangle_mesh_is_usable(self) -> None:
        vertices, indices = icosphere(4)  # 5120 triangles, 2562 vertices
        self.assertEqual(len(indices) // 3, 5120)
        started = time.perf_counter()
        result = analyze_mesh({"vertices": vertices, "indices": indices})
        elapsed = time.perf_counter() - started
        self.assertFalse(result["selfIntersecting"])
        self.assertEqual(result["sampledVertexCount"], 2562)
        print(f"\n  analyze_mesh: {len(indices) // 3} triangles, "
              f"{result['sampledVertexCount']} samples in {elapsed:.2f}s")
        # A gate nobody will wait for is a gate nobody will run.
        self.assertLess(elapsed, 60.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
