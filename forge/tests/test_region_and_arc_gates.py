from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "forge" / "stage4_review"
sys.path.insert(0, str(REVIEW))

import swept_arc_gate  # noqa: E402
import vertex_region_gate  # noqa: E402

BLACK = "#1d1b1a"
WHITE = "#f2efe9"


def _rgb(value: str) -> tuple[float, float, float]:
    number = int(value.lstrip("#"), 16)
    return (((number >> 16) & 255) / 255, ((number >> 8) & 255) / 255, (number & 255) / 255)


def two_tone_box(white_below_y: float) -> dict:
    """A 1x1x1 grid of vertices, white below a height and black above it.

    A synthetic body whose one colour boundary sits at a known height, so the gate's reported box
    can be checked against a number derived by hand rather than against another run of itself.
    """
    positions: list[float] = []
    colors: list[float] = []
    steps = 11
    for i in range(steps):
        for j in range(steps):
            for k in range(steps):
                x = -0.5 + i / (steps - 1)
                y = -0.5 + j / (steps - 1)
                z = -0.5 + k / (steps - 1)
                positions.extend([x, y, z])
                colors.extend(_rgb(WHITE if y <= white_below_y else BLACK))
    return {"meshes": [{"id": "body", "positions": positions, "colors": colors}]}


def arc_tube(bend_radius: float, tube_radius: float, span_degrees: float, rings: int = 40,
             around: int = 16) -> dict:
    positions: list[float] = []
    for ring in range(rings):
        theta = math.radians(span_degrees) * ring / (rings - 1)
        for step in range(around):
            phi = 2 * math.pi * step / around
            radius = bend_radius + tube_radius * math.cos(phi)
            positions.extend([radius * math.cos(theta), radius * math.sin(theta),
                              tube_radius * math.sin(phi)])
    return {"meshes": [{"id": "tail", "positions": positions}]}


def two_patches():
    """Two dense, well-separated white patches on one mesh — a bib and a sock, in miniature."""
    positions, colors = [], []
    for cy in (0.31, -0.41):
        for i in range(12):
            for j in range(12):
                positions.extend([-0.05 + 0.1 * i / 11, cy + 0.04 * j / 11, 0.0])
                colors.extend(_rgb(WHITE))
    return positions, colors


def straight_cone(length: float, base_radius: float, rings: int = 40, around: int = 16) -> dict:
    positions: list[float] = []
    for ring in range(rings):
        t = ring / (rings - 1)
        radius = base_radius * (1 - t)
        for step in range(around):
            phi = 2 * math.pi * step / around
            positions.extend([radius * math.cos(phi), t * length, radius * math.sin(phi)])
    return {"meshes": [{"id": "tail", "positions": positions}]}


class VertexRegionGate(unittest.TestCase):
    def test_the_measured_boundary_matches_the_height_it_was_built_at(self):
        geometry = two_tone_box(white_below_y=-0.2)
        meshes = vertex_region_gate.collect_vertices(geometry)
        measured = vertex_region_gate.measure(
            meshes, {"white": _rgb(WHITE), "black": _rgb(BLACK)}, azimuth=0.0, tolerance=0.06
        )
        white = measured["regions"]["white"]
        # The box spans y -0.5..0.5, so a boundary at y=-0.2 sits 0.3 up a unit height: in
        # reference coordinates (y measured downward from the top) the white region runs from
        # y0 = 0.7 to y1 = 1.0.
        self.assertAlmostEqual(white["y1"], 1.0, places=4)
        self.assertAlmostEqual(white["y0"], 0.7, places=4)
        self.assertEqual(measured["unclassifiedFraction"], 0.0)

    def test_moving_the_boundary_moves_the_measurement(self):
        """Negative control: the gate must not report the same box for a different model."""
        low = vertex_region_gate.measure(
            vertex_region_gate.collect_vertices(two_tone_box(-0.2)),
            {"white": _rgb(WHITE), "black": _rgb(BLACK)}, 0.0, 0.06,
        )["regions"]["white"]
        high = vertex_region_gate.measure(
            vertex_region_gate.collect_vertices(two_tone_box(0.1)),
            {"white": _rgb(WHITE), "black": _rgb(BLACK)}, 0.0, 0.06,
        )["regions"]["white"]
        self.assertNotAlmostEqual(low["y0"], high["y0"], places=3)

    def test_an_expectation_outside_tolerance_fails_and_names_the_delta(self):
        measured = vertex_region_gate.measure(
            vertex_region_gate.collect_vertices(two_tone_box(-0.2)),
            {"white": _rgb(WHITE), "black": _rgb(BLACK)}, 0.0, 0.06,
        )
        evaluation = vertex_region_gate.evaluate(
            measured,
            [{"id": "sock", "regions": ["white"],
              "expected": {"x0": 0.0, "x1": 1.0, "y0": 0.55, "y1": 1.0}, "tolerance": 0.04}],
        )
        self.assertEqual(evaluation["failures"], 1)
        self.assertEqual(evaluation["results"][0]["status"], "fail")
        self.assertAlmostEqual(evaluation["results"][0]["deltas"]["y0"], 0.15, places=3)

    def test_a_matching_expectation_passes(self):
        measured = vertex_region_gate.measure(
            vertex_region_gate.collect_vertices(two_tone_box(-0.2)),
            {"white": _rgb(WHITE), "black": _rgb(BLACK)}, 0.0, 0.06,
        )
        evaluation = vertex_region_gate.evaluate(
            measured,
            [{"id": "sock", "regions": ["white"],
              "expected": {"x0": 0.0, "x1": 1.0, "y0": 0.70, "y1": 1.0}, "tolerance": 0.04}],
        )
        self.assertEqual(evaluation["failures"], 0)

    def test_a_region_with_no_vertices_is_reported_missing_rather_than_passing(self):
        measured = vertex_region_gate.measure(
            vertex_region_gate.collect_vertices(two_tone_box(-0.2)),
            {"white": _rgb(WHITE), "black": _rgb(BLACK), "pink": _rgb("#c07060")}, 0.0, 0.06,
        )
        evaluation = vertex_region_gate.evaluate(
            measured,
            [{"id": "ear-inner", "regions": ["pink"],
              "expected": {"x0": 0.1, "x1": 0.2, "y0": 0.1, "y1": 0.2}}],
        )
        self.assertEqual(evaluation["results"][0]["status"], "missing")
        self.assertEqual(evaluation["failures"], 1)

    def test_unpaintable_vertices_are_counted_not_absorbed(self):
        geometry = two_tone_box(-0.2)
        # Repaint a slab mid-grey: it matches neither palette entry and must be declared.
        colors = geometry["meshes"][0]["colors"]
        for index in range(0, len(colors), 3):
            if colors[index] > 0.5 and index % 9 == 0:
                colors[index] = colors[index + 1] = colors[index + 2] = 0.5
        measured = vertex_region_gate.measure(
            vertex_region_gate.collect_vertices(geometry),
            {"white": _rgb(WHITE), "black": _rgb(BLACK)}, 0.0, 0.06,
        )
        self.assertGreater(measured["unclassifiedFraction"], 0.0)

    def test_azimuth_rotates_what_is_measured(self):
        positions = [-0.5, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.5, 0.4, 0.0, -0.5, -0.4]
        colors = list(_rgb(WHITE)) * 2 + list(_rgb(BLACK)) * 2
        geometry = {"meshes": [{"id": "m", "positions": positions, "colors": colors}]}
        meshes = vertex_region_gate.collect_vertices(geometry)
        front = vertex_region_gate.measure(meshes, {"white": _rgb(WHITE)}, 0.0, 0.06)
        side = vertex_region_gate.measure(meshes, {"white": _rgb(WHITE)}, 90.0, 0.06)
        self.assertNotAlmostEqual(front["regions"]["white"]["x1"],
                                  side["regions"]["white"]["x1"], places=3)

    def test_a_scoped_measurement_ignores_the_same_colour_on_another_mesh(self):
        """Colour alone cannot separate a bib from a sock; the part they sit on can."""
        body = two_tone_box(-0.2)["meshes"][0]
        body["id"] = "torso"
        sock = {
            "id": "paw",
            "positions": [0.0, -1.4, 0.0, 0.1, -1.3, 0.0, -0.1, -1.3, 0.0],
            "colors": list(_rgb(WHITE)) * 3,
        }
        meshes = vertex_region_gate.collect_vertices({"meshes": [body, sock]})
        palette = {"white": _rgb(WHITE), "black": _rgb(BLACK)}
        unscoped = vertex_region_gate.measure(meshes, palette, 0.0, 0.06)
        scoped = vertex_region_gate.measure(meshes, palette, 0.0, 0.06, scope={"torso"})
        # Unscoped, the white region runs all the way to the sock at the bottom of the model.
        self.assertAlmostEqual(unscoped["regions"]["white"]["y1"], 1.0, places=3)
        # Scoped to the torso it stops at the torso's own white band, well above it.
        self.assertLess(scoped["regions"]["white"]["y1"], 0.85)
        # Both are normalised to the WHOLE model, so the two numbers are comparable.
        self.assertEqual(unscoped["projectedExtent"], scoped["projectedExtent"])

    def test_an_empty_scope_is_an_error_rather_than_an_empty_pass(self):
        meshes = vertex_region_gate.collect_vertices(two_tone_box(-0.2))
        with self.assertRaises(SystemExit):
            vertex_region_gate.measure(meshes, {"white": _rgb(WHITE)}, 0.0, 0.06,
                                       scope={"no-such-mesh"})

    def test_one_colour_in_two_places_splits_into_two_blobs(self):
        """The fused-mesh case: a bib and a sock are the same colour on the same mesh.

        Without clustering, the measured "sock" box ran from the muzzle to the ground, because a
        colour bucket on one mesh is one region however many separate places it appears in.
        """
        # Dense patches, because the clustering grid is a connectivity test on a real mesh: three
        # scattered points land in three separate cells and are three blobs, correctly.
        positions, colors = two_patches()
        positions.extend([0.0, 0.0, 0.0])
        colors.extend(_rgb(BLACK))
        meshes = vertex_region_gate.collect_vertices(
            {"meshes": [{"id": "body", "positions": positions, "colors": colors}]}
        )
        measured = vertex_region_gate.measure(
            meshes, {"white": _rgb(WHITE), "black": _rgb(BLACK)}, 0.0, 0.06
        )
        blobs = measured["regions"]["white"]["blobs"]
        self.assertEqual(len(blobs), 2)
        # The whole-region box spans both patches; each blob covers only its own.
        self.assertGreater(measured["regions"]["white"]["y1"]
                           - measured["regions"]["white"]["y0"], 0.8)
        for blob in blobs:
            self.assertLess(blob["y1"] - blob["y0"], 0.2)

    def test_selecting_a_blob_measures_that_blob_and_not_the_other(self):
        positions, colors = two_patches()
        meshes = vertex_region_gate.collect_vertices(
            {"meshes": [{"id": "body", "positions": positions, "colors": colors}]}
        )
        measured = vertex_region_gate.measure(meshes, {"white": _rgb(WHITE)}, 0.0, 0.06)
        blobs = measured["regions"]["white"]["blobs"]
        self.assertEqual(len(blobs), 2)
        # Expectations are built from each blob's own measured box, so this tests the SELECTION
        # rather than re-deriving the geometry by hand.
        for rank, blob in enumerate(blobs):
            with self.subTest(rank=rank):
                evaluation = vertex_region_gate.evaluate(
                    measured,
                    [{"id": "patch", "regions": ["white"], "blobs": [rank],
                      "expected": {k: blob[k] for k in ("x0", "x1", "y0", "y1")},
                      "tolerance": 0.001}],
                )
                self.assertEqual(evaluation["failures"], 0, evaluation)
        # And the two selections are genuinely different boxes.
        self.assertGreater(abs(blobs[0]["y0"] - blobs[1]["y0"]), 0.5)

    def test_a_spatial_filter_picks_the_blob_by_where_it_is_not_by_its_rank(self):
        """Rank is not stable between the reference and the model.

        On the reference the bib is the largest white blob; once the model's legs and paws are
        fused the four socks merge into a larger band, so rank 0 means different features on the
        two sides. Every sock comparison was then off by -0.45 in y0 and every bib comparison by
        +0.56, all in the same direction — a swapped correspondence, not a misplaced boundary.
        """
        positions, colors = two_patches()
        meshes = vertex_region_gate.collect_vertices(
            {"meshes": [{"id": "body", "positions": positions, "colors": colors}]}
        )
        measured = vertex_region_gate.measure(meshes, {"white": _rgb(WHITE)}, 0.0, 0.06)
        blobs = measured["regions"]["white"]["blobs"]
        upper = min(blobs, key=lambda blob: blob["centroidY"])
        lower = max(blobs, key=lambda blob: blob["centroidY"])
        self.assertGreater(lower["centroidY"] - upper["centroidY"], 0.5)

        evaluation = vertex_region_gate.evaluate(
            measured,
            [{"id": "sock", "regions": ["white"], "blobFilter": {"centroidYMin": 0.5},
              "expected": {k: lower[k] for k in ("x0", "x1", "y0", "y1")}, "tolerance": 0.001},
             {"id": "bib", "regions": ["white"], "blobFilter": {"centroidYMax": 0.5},
              "expected": {k: upper[k] for k in ("x0", "x1", "y0", "y1")}, "tolerance": 0.001}],
        )
        self.assertEqual(evaluation["failures"], 0, evaluation)

    def test_a_spatial_filter_matching_nothing_is_missing_not_a_pass(self):
        """Negative control: an empty selection must not quietly measure the whole region."""
        positions, colors = two_patches()
        meshes = vertex_region_gate.collect_vertices(
            {"meshes": [{"id": "body", "positions": positions, "colors": colors}]}
        )
        measured = vertex_region_gate.measure(meshes, {"white": _rgb(WHITE)}, 0.0, 0.06)
        evaluation = vertex_region_gate.evaluate(
            measured,
            [{"id": "sock", "regions": ["white"],
              "blobFilter": {"centroidYMin": 0.99, "centroidYMax": 1.0},
              "expected": {"x0": 0.0, "x1": 1.0, "y0": 0.0, "y1": 1.0}, "tolerance": 0.05}],
        )
        self.assertEqual(evaluation["results"][0]["status"], "missing")
        self.assertEqual(evaluation["failures"], 1)

    def test_naming_a_blob_that_does_not_exist_is_missing_not_a_silent_whole_region(self):
        """Negative control: an out-of-range rank must not quietly fall back to everything."""
        positions, colors = two_patches()
        meshes = vertex_region_gate.collect_vertices(
            {"meshes": [{"id": "body", "positions": positions, "colors": colors}]}
        )
        measured = vertex_region_gate.measure(meshes, {"white": _rgb(WHITE)}, 0.0, 0.06)
        evaluation = vertex_region_gate.evaluate(
            measured,
            [{"id": "sock", "regions": ["white"], "blobs": [3],
              "expected": {"x0": 0.0, "x1": 1.0, "y0": 0.0, "y1": 1.0}, "tolerance": 0.05}],
        )
        self.assertEqual(evaluation["results"][0]["status"], "missing")
        self.assertEqual(evaluation["failures"], 1)

    def test_cli_exits_one_on_a_failed_expectation_and_zero_on_a_met_one(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "geometry.json").write_text(json.dumps(two_tone_box(-0.2)), encoding="utf-8")
            (base / "palette.json").write_text(json.dumps({"white": WHITE, "black": BLACK}),
                                               encoding="utf-8")
            (base / "bad.json").write_text(json.dumps(
                [{"id": "sock", "regions": ["white"],
                  "expected": {"x0": 0.0, "x1": 1.0, "y0": 0.4, "y1": 1.0}, "tolerance": 0.04}]),
                encoding="utf-8")
            (base / "good.json").write_text(json.dumps(
                [{"id": "sock", "regions": ["white"],
                  "expected": {"x0": 0.0, "x1": 1.0, "y0": 0.7, "y1": 1.0}, "tolerance": 0.04}]),
                encoding="utf-8")
            for expectation, expected_code in (("bad.json", 1), ("good.json", 0)):
                result = subprocess.run(
                    [sys.executable, str(REVIEW / "vertex_region_gate.py"),
                     "--geometry", str(base / "geometry.json"),
                     "--palette", str(base / "palette.json"),
                     "--expect", str(base / expectation), "--json"],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)


class SweptArcGate(unittest.TestCase):
    EXPECTATIONS = {
        "minAngularSpanDeg": 150.0,
        "maxCentreDistanceOverExtent": 0.75,
        "bendRadius": 1.0,
        "bendRadiusTolerance": 0.08,
        "tubeRadius": 0.25,
        "tubeRadiusTolerance": 0.06,
        "maxRadiusSpreadOverBendRadius": 0.25,
    }

    def _points(self, geometry):
        positions = geometry["meshes"][0]["positions"]
        return [
            (positions[i], positions[i + 1], positions[i + 2])
            for i in range(0, len(positions), 3)
        ]

    def test_a_hook_recovers_the_radius_span_and_tube_it_was_built_with(self):
        measured = swept_arc_gate.analyse(self._points(arc_tube(1.0, 0.25, 190.0)))
        self.assertAlmostEqual(measured["bendRadius"], 1.0, delta=0.06)
        self.assertGreater(measured["angularSpanDeg"], 150.0)
        self.assertAlmostEqual(measured["tubeRadius"]["mean"], 0.25, delta=0.05)
        self.assertEqual(swept_arc_gate.evaluate(measured, self.EXPECTATIONS)["failures"], 0)

    def test_a_straight_tapered_cone_fails_the_same_gate(self):
        """The negative control the tail claim rests on.

        A cone is the shape the reference is explicitly not. If it passed, a passing verdict on the
        real tail would mean nothing.
        """
        measured = swept_arc_gate.analyse(self._points(straight_cone(2.0, 0.3)))
        evaluation = swept_arc_gate.evaluate(measured, self.EXPECTATIONS)
        self.assertGreater(evaluation["failures"], 0)
        failed = {check["check"] for check in evaluation["checks"] if check["status"] == "fail"}
        self.assertIn("angularSpan", failed)

    def test_a_thin_shallow_arc_fails_the_span_requirement(self):
        """A shallow but genuinely planar sweep must fail on span, not on the plane fit."""
        measured = swept_arc_gate.analyse(self._points(arc_tube(1.0, 0.02, 40.0)))
        evaluation = swept_arc_gate.evaluate(measured, self.EXPECTATIONS)
        failed = {check["check"] for check in evaluation["checks"] if check["status"] == "fail"}
        self.assertNotIn("planeDetermined", failed)
        self.assertIn("angularSpan", failed)

    def test_a_short_sweep_of_a_thick_tube_reports_an_undetermined_plane(self):
        """A rod does not determine a plane, and the gate must say so rather than fit one anyway.

        This is a real defect the gate had before the check existed: a 40-degree sweep of a
        0.25-radius tube fitted its own circular CROSS-SECTION and reported bend radius 0.256 with
        a 352-degree span — a confident number describing the wrong circle entirely.
        """
        measured = swept_arc_gate.analyse(self._points(arc_tube(1.0, 0.25, 40.0)))
        evaluation = swept_arc_gate.evaluate(measured, self.EXPECTATIONS)
        failed = {check["check"] for check in evaluation["checks"] if check["status"] == "fail"}
        self.assertIn("planeDetermined", failed)
        self.assertLess(measured["planarity"], 0.35)

    def test_a_real_hook_reports_a_determined_plane(self):
        """Negative control for the planarity check: it must not fire on the shape that is fine."""
        measured = swept_arc_gate.analyse(self._points(arc_tube(1.0, 0.25, 190.0)))
        self.assertGreater(measured["planarity"], 0.35)

    def test_a_hook_of_the_wrong_radius_fails_the_radius_check_and_not_the_span(self):
        measured = swept_arc_gate.analyse(self._points(arc_tube(1.6, 0.25, 190.0)))
        evaluation = swept_arc_gate.evaluate(measured, self.EXPECTATIONS)
        failed = {check["check"] for check in evaluation["checks"] if check["status"] == "fail"}
        self.assertIn("bendRadius", failed)
        self.assertNotIn("angularSpan", failed)

    def test_a_hook_of_the_wrong_thickness_fails_the_tube_check(self):
        measured = swept_arc_gate.analyse(self._points(arc_tube(1.0, 0.45, 190.0)))
        evaluation = swept_arc_gate.evaluate(measured, self.EXPECTATIONS)
        failed = {check["check"] for check in evaluation["checks"] if check["status"] == "fail"}
        self.assertIn("tubeRadius", failed)

    def test_the_fit_reports_which_plane_it_used_and_how_far_off_it_the_points_are(self):
        measured = swept_arc_gate.analyse(self._points(arc_tube(1.0, 0.25, 190.0)))
        self.assertEqual(len(measured["planeNormal"]), 3)
        self.assertAlmostEqual(measured["maxOffPlaneDistance"], 0.25, delta=0.05)

    def test_cli_reports_a_cone_failure_with_a_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "cone.json").write_text(json.dumps(straight_cone(2.0, 0.3)), encoding="utf-8")
            (base / "expect.json").write_text(json.dumps(self.EXPECTATIONS), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(REVIEW / "swept_arc_gate.py"),
                 "--geometry", str(base / "cone.json"), "--component", "tail",
                 "--expect", str(base / "expect.json"), "--json"],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("angularSpan", result.stdout)


if __name__ == "__main__":
    unittest.main()
