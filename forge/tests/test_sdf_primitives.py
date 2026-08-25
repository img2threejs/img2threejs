#!/usr/bin/env python3
"""Red contracts for implicit SDF descriptors and generated polygonizers."""

from __future__ import annotations

import json
import math
import sys
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "implicit_character_torso_limb.json"


def import_forge_modules():
    module_names = ("generate_threejs_factory", "validate_sculpt_spec")
    original_modules = {name: sys.modules.pop(name, None) for name in module_names}
    original_path = sys.path[:]
    sys.path[:0] = [str(ROOT / "stage2_spec"), str(ROOT / "stage3_build")]
    try:
        from generate_threejs_factory import generate
        from validate_sculpt_spec import VALID_PRIMITIVES, validate_spec
    finally:
        sys.path[:] = original_path
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return generate, VALID_PRIMITIVES, validate_spec


generate, VALID_PRIMITIVES, validate_spec = import_forge_modules()


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class SdfPrimitiveContractTest(unittest.TestCase):
    def test_accepts_implicit_topology_with_maximum_sdf_resolution(self) -> None:
        spec = load_fixture()
        component = spec["componentTree"][0]
        component["geometryDescriptor"]["sdf"]["resolution"] = 64
        errors, warnings = validate_spec(spec)

        self.assertEqual(component["primitive"], "capsule")
        self.assertIn(component["primitive"], VALID_PRIMITIVES)
        self.assertEqual(component["topologyClass"], "implicit")
        self.assertEqual(errors, [])
        self.assertFalse(
            any("topologyClass" in warning for warning in warnings),
            warnings,
        )

    def test_rejects_invalid_sdf_primitive_operation_resolution_and_nonfinite_values(self) -> None:
        cases = (
            (
                "primitive",
                ("primitives", 0, "type"),
                "not-a-primitive",
                "geometryDescriptor.sdf.primitives[0].type",
                "must be one of",
            ),
            (
                "operation",
                ("operations", 0, "type"),
                "xor",
                "geometryDescriptor.sdf.operations[0].type",
                "must be one of",
            ),
            (
                "resolution",
                ("resolution",),
                65,
                "geometryDescriptor.sdf.resolution",
                "must not exceed 64",
            ),
            (
                "nonfinite radius",
                ("primitives", 0, "radius"),
                math.inf,
                "geometryDescriptor.sdf.primitives[0].radius",
                "must be finite",
            ),
        )
        for label, path, value, field_path, reason in cases:
            with self.subTest(label=label):
                spec = load_fixture()
                sdf = spec["componentTree"][0]["geometryDescriptor"]["sdf"]
                target = sdf
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value

                errors, _warnings = validate_spec(spec)

                self.assertTrue(
                    any(field_path in error and reason in error for error in errors),
                    errors,
                )

    def test_rejects_unsupported_sdf_transform_fields(self) -> None:
        spec = load_fixture()
        primitive = spec["componentTree"][0]["geometryDescriptor"]["sdf"]["primitives"][0]
        primitive["transform"] = {"center": [0.0, 0.0, 0.0]}

        errors, _warnings = validate_spec(spec)

        self.assertTrue(
            any(
                "geometryDescriptor.sdf.primitives[0].transform.center" in error
                and "is not supported" in error
                for error in errors
            ),
            errors,
        )

    def test_rejects_unsupported_sdf_primitive_fields(self) -> None:
        spec = load_fixture()
        primitive = spec["componentTree"][0]["geometryDescriptor"]["sdf"]["primitives"][0]
        primitive["position"] = [0.0, 0.0, 0.0]

        errors, _warnings = validate_spec(spec)

        self.assertTrue(
            any(
                "geometryDescriptor.sdf.primitives[0].position" in error
                and "is not supported" in error
                for error in errors
            ),
            errors,
        )

    def test_rejects_sdf_operation_output_id_collisions(self) -> None:
        cases = (
            ("primitive", {"id": "torso"}),
            ("prior operation", {"id": "blend"}),
        )
        for label, output in cases:
            with self.subTest(label=label):
                spec = load_fixture()
                operations = spec["componentTree"][0]["geometryDescriptor"]["sdf"]["operations"]
                operations[0].update(output)
                if label == "prior operation":
                    operations.append({"type": "intersect", "left": "blend", "right": "torso", "id": "blend"})

                errors, _warnings = validate_spec(spec)

                self.assertTrue(
                    any("geometryDescriptor.sdf.operations" in error and "duplicates" in error for error in errors),
                    errors,
                )

    def test_rejects_unsupported_sdf_operation_fields(self) -> None:
        spec = load_fixture()
        operation = spec["componentTree"][0]["geometryDescriptor"]["sdf"]["operations"][0]
        operation["blendWidth"] = 0.2

        errors, _warnings = validate_spec(spec)

        self.assertTrue(
            any(
                "geometryDescriptor.sdf.operations[0].blendWidth" in error
                and "is not supported" in error
                for error in errors
            ),
            errors,
        )

    def test_rejects_conflicting_sdf_operation_id_and_output(self) -> None:
        spec = load_fixture()
        operation = spec["componentTree"][0]["geometryDescriptor"]["sdf"]["operations"][0]
        operation.update({"id": "blend", "output": "alternate-blend"})

        errors, _warnings = validate_spec(spec)

        self.assertTrue(
            any(
                "geometryDescriptor.sdf.operations[0].id" in error and "cannot both be set" in error
                for error in errors
            ),
            errors,
        )

    def test_rejects_collapsed_or_reversed_sdf_bounds(self) -> None:
        cases = (
            ("collapsed", [0.0, -1.0, -1.0], [0.0, 1.0, 1.0]),
            ("reversed", [1.0, -1.0, -1.0], [0.0, 1.0, 1.0]),
        )
        for label, minimum, maximum in cases:
            with self.subTest(label=label):
                spec = load_fixture()
                sdf = spec["componentTree"][0]["geometryDescriptor"]["sdf"]
                sdf["bounds"] = {"min": minimum, "max": maximum}

                errors, _warnings = validate_spec(spec)

                self.assertTrue(
                    any(
                        "geometryDescriptor.sdf.bounds.min[0]" in error and "less than" in error
                        for error in errors
                    ),
                    errors,
                )

    def test_rejects_sdf_descriptor_cardinality_over_limits(self) -> None:
        cases = (("primitives", 33, 64), ("operations", 129, 128))
        for field, multiplier, limit in cases:
            with self.subTest(field=field):
                spec = load_fixture()
                sdf = spec["componentTree"][0]["geometryDescriptor"]["sdf"]
                sdf[field] *= multiplier

                errors, _warnings = validate_spec(spec)

                self.assertTrue(
                    any(
                        f"geometryDescriptor.sdf.{field}" in error
                        and f"must not contain more than {limit}" in error
                        for error in errors
                    ),
                    errors,
                )

    def test_generates_sdf_and_polygonizer_markers(self) -> None:
        spec = load_fixture()
        sdf = spec["componentTree"][0]["geometryDescriptor"]["sdf"]
        generated = generate(spec, "blockout")

        self.assertIn("function sdfCapsule", generated)
        self.assertIn("function smin", generated)
        self.assertIn("function polygonizeSdf", generated)
        polygonizer_start = generated.index("function polygonizeSdf")
        polygonizer_end = generated.index("return geometry;", polygonizer_start)
        body = generated[polygonizer_start:polygonizer_end]
        # Normals come from the field gradient, which is the implicit surface's exact normal.
        # `computeVertexNormals` averaged face normals and carried the sampling grid's imprint into
        # the shading; this assertion used to require it and so encoded the old voxel extractor.
        self.assertIn("setAttribute('normal'", body)
        self.assertIn("const gradient = ", body)
        self.assertNotIn("computeVertexNormals()", body)
        for marker in (
            '"type": "capsule"',
            '"type": "smooth-union"',
            '"resolution": 32',
            '"radius": 0.16',
            '"height": 1.1',
        ):
            self.assertIn(marker, generated)
        self.assertIn(f"polygonizeSdf({json.dumps(sdf)})", generated)

    def test_generates_inverse_quaternion_for_multi_axis_sdf_rotation(self) -> None:
        spec = load_fixture()
        primitive = spec["componentTree"][0]["geometryDescriptor"]["sdf"]["primitives"][0]
        primitive["transform"] = {"rotation": [0.37, -0.61, 1.13]}

        generated = generate(spec, "blockout")

        self.assertIn("setFromEuler", generated)
        self.assertIn(".invert()", generated)



class ImplicitSurfaceIsSmooth(unittest.TestCase):
    """An implicit surface must not be a voxel shell.

    The polygonizer used to emit one axis-aligned quad per exposed voxel face, so every vertex sat
    exactly on a grid plane and every edge was a 90-degree step. That is the wrong output for the
    only kind of subject anyone reaches for an implicit surface to build — a smooth blended organic
    form — and it is worse than the assembled primitives it replaces.
    """

    SPHERE = {
        "primitives": [{"id": "ball", "type": "sphere", "radius": 0.6,
                        "transform": {"position": [0, 0, 0]}}],
        "operations": [],
        "resolution": 24,
        "bounds": {"min": [-1, -1, -1], "max": [1, 1, 1]},
    }

    def _mesh(self, sdf):
        node = shutil.which("node")
        if node is None:
            self.fail("node is required to execute the emitted polygonizer")
        showcase = Path(__file__).resolve().parents[2].parent / "img2threejs-showcase"
        if not (showcase / "node_modules" / "three").is_dir():
            self.skipTest(f"three is not installed at {showcase / 'node_modules' / 'three'}")
        spec = load_fixture()
        component = spec["componentTree"][0]
        component["geometryDescriptor"]["sdf"] = sdf
        source = generate(spec, "blockout")
        work = showcase / "node_modules" / ".cache" / "sdf-smoothness"
        work.mkdir(parents=True, exist_ok=True)
        entry = work / "factory.ts"
        entry.write_text(source, encoding="utf-8")
        subprocess.run(
            [str(showcase / "node_modules" / ".bin" / "esbuild"), str(entry), "--bundle",
             "--format=esm", "--platform=node", "--external:three",
             f"--outfile={work / 'factory.mjs'}", "--log-level=error"],
            check=True, capture_output=True, text=True, cwd=showcase,
        )
        export = next(name for name in ("createImplicitCharacterTorsoLimbModel",)
                      if name in source) if False else None
        harness = work / "run.mjs"
        harness.write_text(
            "import * as factory from './factory.mjs';\n"
            "const create = Object.entries(factory).find(([k, v]) =>\n"
            "  k.startsWith('create') && k.endsWith('Model') && typeof v === 'function')[1];\n"
            "const model = create({});\n"
            "let out = null;\n"
            "model.traverse((o) => { if (o.isMesh && !out) { const g = o.geometry;\n"
            "  out = { positions: Array.from(g.getAttribute('position').array),\n"
            "          normals: Array.from(g.getAttribute('normal').array),\n"
            "          indices: Array.from(g.getIndex().array) }; } });\n"
            "console.log(JSON.stringify(out));\n",
            encoding="utf-8",
        )
        result = subprocess.run([node, str(harness)], capture_output=True, text=True, cwd=work)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_vertices_do_not_sit_on_the_sampling_grid_planes(self):
        """The voxel shell's signature: every coordinate is an exact multiple of the cell size."""
        mesh = self._mesh(self.SPHERE)
        step = 2.0 / self.SPHERE["resolution"]
        offsets = []
        for value in mesh["positions"]:
            local = ((value + 1.0) / step) % 1.0
            offsets.append(min(local, 1.0 - local))
        on_grid = sum(1 for offset in offsets if offset < 1e-6) / len(offsets)
        self.assertLess(on_grid, 0.5, "most vertices lie exactly on grid planes — voxel shell")

    def test_the_recovered_surface_is_close_to_the_sphere_it_was_sampled_from(self):
        mesh = self._mesh(self.SPHERE)
        radii = [
            math.dist((0.0, 0.0, 0.0), mesh["positions"][i:i + 3])
            for i in range(0, len(mesh["positions"]), 3)
        ]
        mean = sum(radii) / len(radii)
        self.assertAlmostEqual(mean, 0.6, delta=0.03)
        spread = max(abs(r - 0.6) for r in radii)
        self.assertLess(spread, 0.06, "surface deviates from the sampled sphere by more than a cell")

    def test_the_surface_encloses_a_positive_volume(self):
        """Winding, which the normals cannot reveal.

        Gradient normals are outward whatever the triangle order is, so an inverted winding shows
        only as back-face culling removing the front surface — the model renders as a hollow shell
        with its interior visible, which reads as a modelling fault rather than a winding one.
        """
        mesh = self._mesh(self.SPHERE)
        total = 0.0
        indices = mesh["indices"]
        positions = mesh["positions"]
        for i in range(0, len(indices), 3):
            a = positions[indices[i] * 3:indices[i] * 3 + 3]
            b = positions[indices[i + 1] * 3:indices[i + 1] * 3 + 3]
            c = positions[indices[i + 2] * 3:indices[i + 2] * 3 + 3]
            cross = (b[1] * c[2] - b[2] * c[1], b[2] * c[0] - b[0] * c[2], b[0] * c[1] - b[1] * c[0])
            total += (a[0] * cross[0] + a[1] * cross[1] + a[2] * cross[2]) / 6.0
        self.assertGreater(total, 0.0, "implicit surface is inside-out")
        # 4/3 pi r^3 for r = 0.6 is 0.905; surface nets on a 24-cell grid lands a little under.
        self.assertAlmostEqual(total, 0.905, delta=0.06)

    # A sphere whose surface pushes out through the six face centres of its own sampling box:
    # face centres sit at 1.0, the radius is 1.25, and the corners at sqrt(3) stay outside. The
    # in-bounds SPHERE above never exercises the boundary planes, which is why the quad pass could
    # read a cell index of `resolution` for years without a test noticing.
    SPHERE_THROUGH_BOUNDS = {
        "primitives": [{"id": "ball", "type": "sphere", "radius": 1.25,
                        "transform": {"position": [0, 0, 0]}}],
        "operations": [],
        "resolution": 24,
        "bounds": {"min": [-1, -1, -1], "max": [1, 1, 1]},
    }

    def test_a_surface_reaching_its_sampling_bounds_emits_no_stray_index(self):
        """The quad pass must not index a cell that does not exist.

        Each quad joins the four cells sharing one grid edge. Bounding only the edge axis and the
        lower end of the other two let an index reach `resolution` -- a corner coordinate, not a cell
        coordinate -- so `cellAt` either aliased into an unrelated slot or read past the array, where
        a typed-array read gives `undefined`. `undefined < 0` is false, so it survived the guard in
        `quad` and reached `setIndex`, which coerces it to 0: triangles wired to whichever vertex
        happens to be first. A range check cannot catch that (0 is a valid index), so this measures
        edge length instead -- adjacent cells are at most a few cells apart, and a stray index
        produces an edge spanning the model.
        """
        mesh = self._mesh(self.SPHERE_THROUGH_BOUNDS)
        indices, positions = mesh["indices"], mesh["positions"]
        self.assertGreater(len(indices), 0, "no surface was emitted at all")

        step = 2.0 / self.SPHERE_THROUGH_BOUNDS["resolution"]
        longest = 0.0
        for i in range(0, len(indices), 3):
            triangle = [positions[indices[i + k] * 3:indices[i + k] * 3 + 3] for k in range(3)]
            for a, b in ((0, 1), (1, 2), (2, 0)):
                longest = max(longest, math.dist(triangle[a], triangle[b]))

        # Two cells sharing a grid edge are at most one cell apart per axis, so a legitimate edge
        # cannot exceed a couple of cell diagonals. Six cells is loose and still far below the
        # model-spanning edges a stray index produces.
        self.assertLess(
            longest, 6 * step,
            f"longest triangle edge {longest:.4f} exceeds {6 * step:.4f}; an index points at a cell "
            f"that is not adjacent, which is the signature of an out-of-range or aliased cell read",
        )

    def test_normals_point_outward_and_are_unit_length(self):
        mesh = self._mesh(self.SPHERE)
        checked = 0
        for i in range(0, len(mesh["positions"]), 3):
            position = mesh["positions"][i:i + 3]
            normal = mesh["normals"][i:i + 3]
            length = math.sqrt(sum(component * component for component in normal))
            self.assertAlmostEqual(length, 1.0, places=4)
            dot = sum(p * n for p, n in zip(position, normal))
            self.assertGreater(dot, 0.0, "normal points into the solid")
            checked += 1
        self.assertGreater(checked, 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
