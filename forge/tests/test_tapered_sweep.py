#!/usr/bin/env python3
"""Tests for the `tapered-sweep` primitive.

Every other sweep this factory emits carries a CONSTANT cross-section: `buildTubeGeometry` takes one
radius, `buildCurveSweepGeometry` extrudes one Shape along a path. Nothing that comes to a point --
a hair lock, a horn, a tail, a finger, a blade tip -- could be expressed, so those subjects were
built from stacked constant-radius pieces and read as noodles.

The taper warning exists because of a measured failure, not a theory. A recovered build contained
eleven hair locks whose tip radius was 0.0327 on every single one, identical to four decimals, for
tip/root ratios of 0.58-0.79 against a reference that measures 0.087. The frame maths was correct;
the authored stations were not, and nothing objected.

Run: python3 forge/tests/test_tapered_sweep.py
"""
import sys
import json
import math
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage2_spec"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage3_build"))

import generate_threejs_factory  # noqa: E402
from generate_threejs_factory import _DEFAULT_TAPERED_SWEEP, geometry_for  # noqa: E402
from validate_sculpt_spec import TAPER_RATIO_MAX, VALID_PRIMITIVES, taper_risk  # noqa: E402


def component(stations: list[dict[str, object]]) -> dict[str, object]:
    return {"geometryDescriptor": {"taperedSweep": {"stations": stations}}}


def station(y: float, rx: float, rz: float, twist: float = 0.0) -> dict[str, object]:
    return {"position": [0.0, y, 0.0], "rx": rx, "rz": rz, "twist": twist}


class PrimitiveRegistration(unittest.TestCase):
    def test_primitive_is_accepted_by_the_schema(self) -> None:
        self.assertIn("tapered-sweep", VALID_PRIMITIVES)

    def test_geometry_for_emits_the_builder(self) -> None:
        call = geometry_for("tapered-sweep", {"taperedSweep": _DEFAULT_TAPERED_SWEEP}, {})
        self.assertTrue(call.startswith("buildTaperedSweepGeometry("))
        self.assertIn("stations", call)

    def test_a_missing_descriptor_falls_back_to_a_tapering_default(self) -> None:
        """The default must not itself trip the taper warning -- otherwise every spec that omits
        the descriptor inherits a warning it cannot act on."""
        stations = _DEFAULT_TAPERED_SWEEP["stations"]
        ratio = max(stations[-1]["rx"], stations[-1]["rz"]) / max(stations[0]["rx"], stations[0]["rz"])
        self.assertLess(ratio, TAPER_RATIO_MAX)

        call = geometry_for("tapered-sweep", {}, {})
        self.assertIn("buildTaperedSweepGeometry(", call)


class TaperWarning(unittest.TestCase):
    def test_a_lock_that_reaches_a_point_passes(self) -> None:
        severity, _ = taper_risk(
            "hair-lock",
            component([station(-0.5, 0.060, 0.040), station(0.0, 0.030, 0.020), station(0.5, 0.005, 0.003)]),
        )
        self.assertEqual(severity, "OK")

    def test_the_recovered_blunt_lock_is_caught(self) -> None:
        """The exact numbers from the recovered build: root 0.0538, tip 0.0323, ratio 0.60."""
        severity, message = taper_risk(
            "Hair_Fringe_L",
            component([station(-0.5, 0.0538, 0.0538), station(0.5, 0.0323, 0.0323)]),
        )
        self.assertEqual(severity, "HIGH")
        self.assertIn("0.60", message)
        self.assertIn("noodle", message)

    def test_a_constant_radius_sweep_is_caught(self) -> None:
        severity, _ = taper_risk(
            "cable", component([station(-0.5, 0.02, 0.02), station(0.5, 0.02, 0.02)])
        )
        self.assertEqual(severity, "HIGH")

    def test_components_without_the_descriptor_are_untouched(self) -> None:
        for candidate in ({}, {"geometryDescriptor": {}}, {"geometryDescriptor": {"tubePath": {}}}):
            with self.subTest(candidate=candidate):
                self.assertEqual(taper_risk("x", candidate)[0], "OK")

    def test_malformed_stations_do_not_raise(self) -> None:
        for stations in ([], [station(0, 0.1, 0.1)], ["not-a-dict", 7], [station(0, 0.0, 0.0), station(1, 0.0, 0.0)]):
            with self.subTest(stations=stations):
                self.assertEqual(taper_risk("x", component(stations))[0], "OK")


class EmittedSource(unittest.TestCase):
    """The emitted TypeScript is the deliverable; these assert the properties that make it correct.

    A full typecheck of the generated factory runs in test_showcase_tsc_smoke when a showcase
    checkout is configured; these checks hold with no browser and no Node.
    """

    _cached: str | None = None

    def source(self) -> str:
        """Generate a real factory from a spec that uses the primitive, and read what came out."""
        if EmittedSource._cached is None:
            from generate_threejs_factory import generate  # noqa: PLC0415

            spec = {
                "targetName": "TaperTest",
                "schemaVersion": "2.1",
                "suitability": "pass",
                "coordinateFrame": {},
                "silhouette": {},
                "proceduralStrategy": [],
                "materials": [{"id": "hair"}],
                "componentTree": [
                    {
                        "id": "lock",
                        "name": "Lock",
                        "primitive": "tapered-sweep",
                        "materialId": "hair",
                        "geometryDescriptor": {"taperedSweep": _DEFAULT_TAPERED_SWEEP},
                    }
                ],
            }
            EmittedSource._cached = generate(spec, "blockout")
        return EmittedSource._cached

    def test_the_builder_is_emitted_and_called(self) -> None:
        source = self.source()
        self.assertIn("function buildTaperedSweepGeometry(", source)
        self.assertIn("buildTaperedSweepGeometry({", source)

    def test_uses_parallel_transport_not_frenet(self) -> None:
        source = self.source()
        self.assertIn("buildTaperedSweepGeometry", source)
        self.assertNotIn("computeFrenetFrames", source)

    def test_guards_the_degenerate_seed_axis(self) -> None:
        """A reference axis parallel to the first tangent makes the first cross product zero and
        collapses the sweep to a line."""
        self.assertIn("> 0.9", self.source())

    def test_guards_coincident_stations(self) -> None:
        """Two stations at the same position normalise to NaN and poison every later vertex."""
        self.assertIn("1e-12", self.source())

    def test_recomputes_normals_after_building(self) -> None:
        self.assertIn("computeVertexNormals", self.source())

    def test_a_collapsed_station_emits_one_vertex_not_a_zero_radius_ring(self) -> None:
        """The reason the taper warning was not enough on its own.

        A ring of radius 0 still carries `radial` coincident vertices and `radial` zero-area
        triangles, so the sweep ends in a blunt cap the width of floating-point noise. The existing
        `sectionedLoft` in the humanoid demo collapses such a section to a single vertex, and a hair
        lock, a horn or a blade tip has to actually reach a point.
        """
        source = self.source()
        self.assertIn("st.rx <= 1e-6 && st.rz <= 1e-6", source)
        self.assertIn("isPoint", source)

    def test_a_point_end_is_not_capped_again(self) -> None:
        self.assertIn("if (isPoint[end]) continue;", self.source())


class DefaultDescriptor(unittest.TestCase):
    def test_the_default_tip_is_a_true_point(self) -> None:
        tip = _DEFAULT_TAPERED_SWEEP["stations"][-1]
        self.assertEqual(tip["rx"], 0.0)
        self.assertEqual(tip["rz"], 0.0)

    def test_the_default_passes_its_own_taper_gate(self) -> None:
        severity, _ = taper_risk(
            "default", {"geometryDescriptor": {"taperedSweep": _DEFAULT_TAPERED_SWEEP}}
        )
        self.assertEqual(severity, "OK")



class TaperRiskIsDirectionAgnostic(unittest.TestCase):
    """A sweep authored ankle-to-haunch tapers exactly as much as one authored haunch-to-ankle.

    Station order is not free: reversing it reverses the triangle winding, so a limb authored
    tip-first renders as an open shell seen from the inside. Reading station[0] as "the root"
    therefore reported a real 0.53 taper as 1.89 and pushed the author toward breaking the winding
    to satisfy the check.
    """

    @staticmethod
    def _component(radii):
        return {
            "geometryDescriptor": {
                "taperedSweep": {
                    "stations": [
                        {"position": [0.0, index * 0.1, 0.0], "rx": radius, "rz": radius}
                        for index, radius in enumerate(radii)
                    ]
                }
            }
        }

    def test_the_same_limb_scores_the_same_either_way_round(self):
        from validate_sculpt_spec import taper_risk

        narrow_first = taper_risk("leg", self._component([0.061, 0.09, 0.113]))
        wide_first = taper_risk("leg", self._component([0.113, 0.09, 0.061]))
        self.assertEqual(narrow_first[0], wide_first[0])
        self.assertEqual(narrow_first[0], "OK")

    def test_a_barrel_widest_in_the_middle_is_not_called_a_noodle(self):
        """A torso, a spindle and a lemon all taper hard and have near-identical ends."""
        from validate_sculpt_spec import taper_risk

        level, _ = taper_risk("torso", self._component([0.055, 0.18, 0.213, 0.18, 0.092]))
        self.assertEqual(level, "OK")

    def test_a_genuinely_untapered_sweep_still_warns_in_both_orders(self):
        """Negative control: the check is not simply weakened."""
        from validate_sculpt_spec import taper_risk

        for radii in ([0.10, 0.10, 0.098], [0.098, 0.10, 0.10]):
            with self.subTest(radii=radii):
                level, message = taper_risk("noodle", self._component(radii))
                self.assertEqual(level, "HIGH")
                self.assertIn("narrow/wide", message)



class SweepWindingFacesOutward(unittest.TestCase):
    """A closed sweep must have POSITIVE signed volume, i.e. outward-facing triangles.

    Measured on a built model before the fix: torso -0.0674, tail -0.0044, and each leg -0.000155
    against a true volume of +0.0032 — the legs' two triangles per quad were wound OPPOSITE to each
    other, so the solid nearly cancelled itself out and rendered as an open shell. Nothing caught
    it: the mesh was watertight by vertex count, the silhouette was right, and the defect showed
    only as a pale, see-through part that reads as a material fault.
    """

    @staticmethod
    def _signed_volume(positions, indices):
        total = 0.0
        for i in range(0, len(indices), 3):
            a = positions[indices[i] * 3:indices[i] * 3 + 3]
            b = positions[indices[i + 1] * 3:indices[i + 1] * 3 + 3]
            c = positions[indices[i + 2] * 3:indices[i + 2] * 3 + 3]
            cross = (
                b[1] * c[2] - b[2] * c[1],
                b[2] * c[0] - b[0] * c[2],
                b[0] * c[1] - b[1] * c[0],
            )
            total += (a[0] * cross[0] + a[1] * cross[1] + a[2] * cross[2]) / 6.0
        return total

    def _build(self, sweep):
        node = shutil.which("node")
        if node is None:
            self.fail("node is required to execute the emitted sweep builder")
        showcase = ROOT.parent / "img2threejs-showcase"
        if not (showcase / "node_modules" / "three").is_dir():
            self.skipTest(f"three is not installed at {showcase / 'node_modules' / 'three'}")
        source = generate_threejs_factory.generate(_sweep_spec(sweep), "blockout")
        # Built inside the showcase's node_modules so `three` resolves; a module outside the
        # project tree cannot see the project's dependencies.
        work = showcase / "node_modules" / ".cache" / "sweep-winding"
        work.mkdir(parents=True, exist_ok=True)
        entry = work / "factory.ts"
        entry.write_text(source, encoding="utf-8")
        module = work / "factory.mjs"
        subprocess.run(
            [
                str(showcase / "node_modules" / ".bin" / "esbuild"),
                str(entry),
                "--bundle", "--format=esm", "--platform=node", "--external:three",
                f"--outfile={module}", "--log-level=error",
            ],
            text=True, check=True, capture_output=True, cwd=showcase,
        )
        harness = work / "run.mjs"
        harness.write_text(
            "import { createSweepModel } from './factory.mjs';\n"
            "const model = createSweepModel({});\n"
            "let out = null;\n"
            "model.traverse((o) => { if (o.isMesh) { const g = o.geometry;\n"
            "  out = { positions: Array.from(g.getAttribute('position').array),\n"
            "          indices: Array.from(g.getIndex().array) }; } });\n"
            "console.log(JSON.stringify(out));\n",
            encoding="utf-8",
        )
        result = subprocess.run([node, str(harness)], capture_output=True, text=True, cwd=work)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_a_straight_sweep_encloses_a_positive_volume(self):
        mesh = self._build({
            "stations": [
                {"position": [0.0, 0.0, 0.0], "rx": 0.06, "rz": 0.06, "twist": 0.0},
                {"position": [0.0, 0.5, 0.0], "rx": 0.10, "rz": 0.10, "twist": 0.0},
                {"position": [0.0, 1.0, 0.0], "rx": 0.04, "rz": 0.04, "twist": 0.0},
            ],
            "radialSegments": 16,
            "capEnds": True,
        })
        volume = self._signed_volume(mesh["positions"], mesh["indices"])
        self.assertGreater(volume, 0.0, "sweep is inside-out")
        # A rough solid-of-revolution estimate, to catch a sign that is right and a magnitude that
        # is not — which is what a self-cancelling quad split produces.
        self.assertGreater(volume, 0.01)

    def test_a_sweep_ending_in_a_point_also_encloses_a_positive_volume(self):
        mesh = self._build({
            "stations": [
                {"position": [0.0, 0.0, 0.0], "rx": 0.08, "rz": 0.08, "twist": 0.0},
                {"position": [0.0, 0.6, 0.0], "rx": 0.05, "rz": 0.05, "twist": 0.0},
                {"position": [0.0, 1.0, 0.0], "rx": 0.0, "rz": 0.0, "twist": 0.0},
            ],
            "radialSegments": 14,
            "capEnds": True,
        })
        self.assertGreater(self._signed_volume(mesh["positions"], mesh["indices"]), 0.005)

    def test_a_curved_sweep_encloses_a_positive_volume(self):
        stations = []
        for index in range(20):
            angle = math.radians(-85 - 190 * index / 19)
            stations.append({
                "position": [0.0, 0.19 * math.sin(angle), 0.19 * math.cos(angle)],
                "rx": 0.05, "rz": 0.05, "twist": 0.0,
            })
        mesh = self._build({"stations": stations, "radialSegments": 16, "capEnds": True})
        self.assertGreater(self._signed_volume(mesh["positions"], mesh["indices"]), 0.002)


def _sweep_spec(sweep):
    return {
        "targetName": "Sweep",
        "targetId": "sweep",
        "schemaVersion": "2.1",
        "suitability": "pass",
        "coordinateFrame": {"front": "+Z", "up": "+Y", "scaleReference": "unit"},
        "silhouette": {"boundingShape": "test", "symmetry": "bilateral"},
        "proceduralStrategy": ["blockout"],
        "materials": [{"id": "base", "name": "Base", "baseColor": "#808080"}],
        "buildPasses": [{"id": "blockout", "acceptance": []}],
        "componentTree": [{
            "id": "sweep",
            "name": "Sweep",
            "level": "macro",
            "role": "body",
            "primitive": "tapered-sweep",
            "topologyClass": "continuous-sculpt",
            "topologyRationale": "test",
            "parent": None,
            "material": "base",
            "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative"},
            "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
            "geometryDescriptor": {"taperedSweep": sweep},
        }],
    }


if __name__ == "__main__":
    unittest.main(verbosity=2)
