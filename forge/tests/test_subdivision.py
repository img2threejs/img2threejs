#!/usr/bin/env python3
"""Contracts for opt-in Catmull-Clark subdivision cages.

Run runtime checks with:
IMG2THREEJS_SHOWCASE_ROOT=/path/to/img2threejs-showcase python3 tests/test_subdivision.py
Set IMG2THREEJS_REQUIRE_SHOWCASE=1 to make missing showcase configuration fail in CI.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from showcase_test_support import showcase_root

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "subdivision_cage.json"
IMPLICIT_FIXTURE = ROOT / "tests" / "fixtures" / "implicit_character_torso_limb.json"


def import_forge_modules():
    module_names = ("generate_threejs_factory", "subdivision", "validate_sculpt_spec")
    original_modules = {name: sys.modules.pop(name, None) for name in module_names}
    original_path = sys.path[:]
    sys.path[:0] = [str(ROOT / "_shared"), str(ROOT / "stage2_spec"), str(ROOT / "stage3_build")]
    try:
        from generate_threejs_factory import generate
        from subdivision import (
            CAPSULE_CAP_SEGMENTS,
            CAPSULE_RADIAL_SEGMENTS,
            MAX_SUBDIVISION_ITERATIONS,
            MAX_SUBDIVISION_QUAD_FACES,
            PLANE_HEIGHT_SEGMENTS,
            PLANE_WIDTH_SEGMENTS,
            SPHERE_HEIGHT_SEGMENTS,
            SPHERE_WIDTH_SEGMENTS,
            SUBDIVISION_SOURCE_FACE_ESTIMATES,
            TORUS_RADIAL_SEGMENTS,
            TORUS_TUBULAR_SEGMENTS,
            capsule_source_face_count,
        )
        from validate_sculpt_spec import validate_spec
    finally:
        sys.path[:] = original_path
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return (
        generate,
        validate_spec,
        MAX_SUBDIVISION_ITERATIONS,
        MAX_SUBDIVISION_QUAD_FACES,
        SUBDIVISION_SOURCE_FACE_ESTIMATES,
        CAPSULE_CAP_SEGMENTS,
        CAPSULE_RADIAL_SEGMENTS,
        PLANE_HEIGHT_SEGMENTS,
        PLANE_WIDTH_SEGMENTS,
        SPHERE_HEIGHT_SEGMENTS,
        SPHERE_WIDTH_SEGMENTS,
        TORUS_RADIAL_SEGMENTS,
        TORUS_TUBULAR_SEGMENTS,
        capsule_source_face_count,
    )


(
    generate,
    validate_spec,
    MAX_SUBDIVISION_ITERATIONS,
    MAX_SUBDIVISION_QUAD_FACES,
    SUBDIVISION_SOURCE_FACE_ESTIMATES,
    CAPSULE_CAP_SEGMENTS,
    CAPSULE_RADIAL_SEGMENTS,
    PLANE_HEIGHT_SEGMENTS,
    PLANE_WIDTH_SEGMENTS,
    SPHERE_HEIGHT_SEGMENTS,
    SPHERE_WIDTH_SEGMENTS,
    TORUS_RADIAL_SEGMENTS,
    TORUS_TUBULAR_SEGMENTS,
    capsule_source_face_count,
) = import_forge_modules()


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def load_implicit_fixture() -> dict:
    return json.loads(IMPLICIT_FIXTURE.read_text(encoding="utf-8"))


def compile_generated_module(
    generated: str,
    work_dir: Path,
    export_subdivision_helper: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    source = work_dir / "subdivision.ts"
    build_dir = work_dir / "build"
    export_statement = (
        "\nexport { subdivideCatmullClark, SubdivisionGeometryBudgetError, SubdivisionInputError, SubdivisionTopologyError };\n"
        if export_subdivision_helper
        else ""
    )
    source.write_text(generated + export_statement, encoding="utf-8")
    result = subprocess.run(
        [
            (shutil.which("npx") or "npx"),
            "tsc",
            "--target",
            "ES2020",
            "--module",
            "NodeNext",
            "--moduleResolution",
            "NodeNext",
            "--strict",
            "--skipLibCheck",
            "--noUnusedLocals",
            "--noUnusedParameters",
            "--outDir",
            str(build_dir),
            str(source),
        ],
        cwd=showcase_root(),
        capture_output=True,
        text=True,
    )
    return result, build_dir / "subdivision.js"


class SubdivisionContractTest(unittest.TestCase):
    def test_accepts_opt_in_subdivision_iterations(self) -> None:
        for iterations in (0, 2, MAX_SUBDIVISION_ITERATIONS):
            with self.subTest(iterations=iterations):
                spec = load_fixture()
                component = spec["componentTree"][0]
                component["geometryDescriptor"]["subdivide"]["iterations"] = iterations

                errors, _warnings = validate_spec(spec)

                self.assertEqual(component["geometryDescriptor"]["subdivide"]["iterations"], iterations)
                self.assertEqual(errors, [])

    def test_accepts_empty_subdivide_as_a_no_op(self) -> None:
        spec = load_fixture()
        spec["componentTree"][0]["geometryDescriptor"]["subdivide"] = {}

        errors, _warnings = validate_spec(spec)
        generated = generate(spec, "blockout")

        self.assertEqual(errors, [])
        self.assertNotIn("function subdivideCatmullClark", generated)

    def test_rejects_negative_fractional_and_over_limit_iterations(self) -> None:
        cases = (
            ("boolean", True, "must be a non-negative integer"),
            ("negative", -1, "must be a non-negative integer"),
            ("fractional", 1.5, "must be a non-negative integer"),
            ("over limit", MAX_SUBDIVISION_ITERATIONS + 1, f"must not exceed {MAX_SUBDIVISION_ITERATIONS}"),
        )
        for label, iterations, reason in cases:
            with self.subTest(label=label):
                spec = load_fixture()
                spec["componentTree"][0]["geometryDescriptor"]["subdivide"]["iterations"] = iterations

                errors, _warnings = validate_spec(spec)

                self.assertTrue(
                    any(
                        "geometryDescriptor.subdivide.iterations" in error and reason in error
                        for error in errors
                    ),
                    errors,
                )

    def test_generate_rejects_invalid_subdivision_iterations(self) -> None:
        cases = (
            (True, "must be a non-negative integer"),
            (-1, "must be a non-negative integer"),
            (1.5, "must be a non-negative integer"),
            (MAX_SUBDIVISION_ITERATIONS + 1, f"must not exceed {MAX_SUBDIVISION_ITERATIONS}"),
        )
        for iterations, reason in cases:
            with self.subTest(iterations=iterations):
                spec = load_fixture()
                spec["componentTree"][0]["geometryDescriptor"]["subdivide"]["iterations"] = iterations

                with self.assertRaisesRegex(ValueError, reason):
                    generate(spec, "blockout")

    def test_generator_cli_rejects_invalid_subdivision_iterations(self) -> None:
        spec = load_fixture()
        spec["componentTree"][0]["geometryDescriptor"]["subdivide"]["iterations"] = 1.5

        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            spec_path = work_dir / "invalid-subdivision.json"
            output_path = work_dir / "model.ts"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "stage3_build" / "generate_threejs_factory.py"),
                    str(spec_path),
                    "--out",
                    str(output_path),
                    "--allow-nonstrict",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("geometryDescriptor.subdivide.iterations", result.stderr)
        self.assertFalse(output_path.exists())

    def test_generates_catmull_clark_helper_and_recomputes_normals(self) -> None:
        spec = load_fixture()
        generated = generate(spec, "blockout")

        self.assertIn("function subdivideCatmullClark", generated)
        self.assertIn("const nextFaces: number[][] = []", generated)
        self.assertIn("nextFaces.push([", generated)
        self.assertIn("subdivideCatmullClark(geometry", generated)
        self.assertIn("subdivideCatmullClark(geometry, 2)", generated)
        self.assertIn(f"const MAX_SUBDIVISION_QUAD_FACES = {MAX_SUBDIVISION_QUAD_FACES};", generated)
        self.assertIn(f"const MAX_SUBDIVISION_ITERATIONS = {MAX_SUBDIVISION_ITERATIONS};", generated)
        helper_start = generated.index("function subdivideCatmullClark")
        helper_end = generated.index("return geometry;", helper_start)
        self.assertIn("computeVertexNormals()", generated[helper_start:helper_end])

    def test_generated_cage_reports_runtime_quad_growth(self) -> None:
        self.assertTrue(showcase_root().exists(), showcase_root())
        expected_counts = ((1, 24), (2, 96))
        for iterations, expected_quad_count in expected_counts:
            with self.subTest(iterations=iterations):
                spec = load_fixture()
                spec["componentTree"][0]["geometryDescriptor"]["subdivide"]["iterations"] = iterations
                generated = generate(spec, "blockout")

                with tempfile.TemporaryDirectory(dir=showcase_root()) as directory:
                    work_dir = Path(directory)
                    compile_result, _module_path = compile_generated_module(generated, work_dir)
                    self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
                    runtime = subprocess.run(
                        [
                            "node",
                            "--input-type=module",
                            "--eval",
                            (
                                "import { createSubdivisionCageModel } from './build/subdivision.js'; "
                                "const model = createSubdivisionCageModel(); "
                                "const mesh = model.userData.sculptRuntime.meshes['rounded-cage']; "
                                "const uv = mesh.geometry.getAttribute('uv'); "
                                "console.log(JSON.stringify({ "
                                "subdivision: mesh.geometry.userData.subdivision, "
                                "positionCount: mesh.geometry.getAttribute('position').count, "
                                "uvCount: uv?.count ?? 0 "
                                "}));"
                            ),
                        ],
                        cwd=work_dir,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(runtime.returncode, 0, runtime.stderr)
                    result = json.loads(runtime.stdout)

                self.assertEqual(result["subdivision"]["sourceFaceCount"], 6)
                self.assertEqual(result["subdivision"]["quadFaceCount"], expected_quad_count)
                self.assertEqual(result["subdivision"]["iterations"], iterations)
                self.assertGreater(result["uvCount"], 0)
                self.assertEqual(result["uvCount"], result["positionCount"])

    def test_generated_subdivision_propagates_uv2(self) -> None:
        spec = load_fixture()
        generated = generate(spec, "blockout")

        with tempfile.TemporaryDirectory(dir=showcase_root()) as directory:
            work_dir = Path(directory)
            compile_result, _module_path = compile_generated_module(
                generated,
                work_dir,
                export_subdivision_helper=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            runtime = subprocess.run(
                [
                    "node",
                    "--input-type=module",
                    "--eval",
                    (
                        "import * as THREE from 'three'; "
                        "import { subdivideCatmullClark } from './build/subdivision.js'; "
                        "const source = new THREE.BoxGeometry(1, 1, 1); "
                        "source.setAttribute('uv2', source.getAttribute('uv').clone()); "
                        "const geometry = subdivideCatmullClark(source, 1); "
                        "const uv = geometry.getAttribute('uv'); "
                        "const uv2 = geometry.getAttribute('uv2'); "
                        "const seamSets = (input) => { "
                        "const position = input.getAttribute('position'); "
                        "const inputUv = input.getAttribute('uv'); "
                        "const corners = new Map(); "
                        "for (let index = 0; index < position.count; index += 1) { "
                        "const key = `${position.getX(index)},${position.getY(index)},${position.getZ(index)}`; "
                        "const values = corners.get(key) ?? new Set(); "
                        "values.add(`${inputUv.getX(index)},${inputUv.getY(index)}`); "
                        "corners.set(key, values); "
                        "} "
                        "return Array.from(corners.values()) "
                        ".filter((values) => values.size > 1) "
                        ".map((values) => Array.from(values).sort()); "
                        "}; "
                        "const sourceSeams = seamSets(source); "
                        "const subdivisionSeams = seamSets(geometry); "
                        "const preservesSourceSeam = sourceSeams.some((sourceValues) => "
                        "subdivisionSeams.some((subdivisionValues) => "
                        "sourceValues.every((value) => subdivisionValues.includes(value)))); "
                        "const uvValues = new Set(Array.from(uv.array).map((value, index) => "
                        "index % 2 === 0 ? `${value},${uv.array[index + 1]}` : '')); "
                        "const hasExpectedInterpolation = uvValues.has('0.5,0') && uvValues.has('0.5,0.5'); "
                        "const usable = [uv, uv2].every((attribute) => attribute "
                        "&& attribute.count === geometry.getAttribute('position').count "
                        "&& Array.from(attribute.array).every(Number.isFinite)); "
                        "console.log(JSON.stringify({ "
                        "usable, "
                        "sourceSeamCount: sourceSeams.length, "
                        "subdivisionSeamCount: subdivisionSeams.length, "
                        "preservesSourceSeam, "
                        "hasExpectedInterpolation, "
                        "uvCount: uv?.count ?? 0, "
                        "uv2Count: uv2?.count ?? 0 "
                        "}));"
                    ),
                ],
                cwd=work_dir,
                capture_output=True,
                text=True,
            )
            self.assertEqual(runtime.returncode, 0, runtime.stderr)
            result = json.loads(runtime.stdout)

        self.assertTrue(result["usable"])
        self.assertGreater(result["sourceSeamCount"], 0)
        self.assertGreater(result["subdivisionSeamCount"], 0)
        self.assertTrue(result["preservesSourceSeam"])
        self.assertTrue(result["hasExpectedInterpolation"])
        self.assertEqual(result["uvCount"], result["uv2Count"])

    def test_generated_subdivision_rejects_dense_geometry_over_budget(self) -> None:
        spec = load_fixture()
        generated = generate(spec, "blockout")

        with tempfile.TemporaryDirectory(dir=showcase_root()) as directory:
            work_dir = Path(directory)
            compile_result, _module_path = compile_generated_module(
                generated,
                work_dir,
                export_subdivision_helper=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            runtime = subprocess.run(
                [
                    "node",
                    "--input-type=module",
                    "--eval",
                    (
                        "import * as THREE from 'three'; "
                        "import { subdivideCatmullClark, SubdivisionGeometryBudgetError } from './build/subdivision.js'; "
                        "try { "
                        "subdivideCatmullClark(new THREE.SphereGeometry(0.5, 64, 40), 4); "
                        "console.log(JSON.stringify({ rejected: false })); "
                        "} catch (error) { "
                        "console.log(JSON.stringify({ "
                        "rejected: error instanceof SubdivisionGeometryBudgetError, "
                        "name: error instanceof Error ? error.name : '' "
                        "})); "
                        "}"
                    ),
                ],
                cwd=work_dir,
                capture_output=True,
                text=True,
            )
            self.assertEqual(runtime.returncode, 0, runtime.stderr)
            result = json.loads(runtime.stdout)

        self.assertTrue(result["rejected"])
        self.assertEqual(result["name"], "SubdivisionGeometryBudgetError")

    def test_generated_subdivision_rejects_invalid_runtime_iterations(self) -> None:
        spec = load_fixture()
        generated = generate(spec, "blockout")

        with tempfile.TemporaryDirectory(dir=showcase_root()) as directory:
            work_dir = Path(directory)
            compile_result, _module_path = compile_generated_module(
                generated,
                work_dir,
                export_subdivision_helper=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            runtime = subprocess.run(
                [
                    "node",
                    "--input-type=module",
                    "--eval",
                    (
                        "import * as THREE from 'three'; "
                        "import { subdivideCatmullClark, SubdivisionInputError } from './build/subdivision.js'; "
                        f"const values = [true, 1.5, -1, Number.NaN, {MAX_SUBDIVISION_ITERATIONS + 1}]; "
                        "const rejected = values.map((value) => { "
                        "try { subdivideCatmullClark(new THREE.BoxGeometry(1, 1, 1), value); return false; } "
                        "catch (error) { return error instanceof SubdivisionInputError; } "
                        "}); "
                        "const source = new THREE.BoxGeometry(1, 1, 1); "
                        "const zero = subdivideCatmullClark(source, 0); "
                        "console.log(JSON.stringify({ rejected, zeroIsNoOp: zero === source }));"
                    ),
                ],
                cwd=work_dir,
                capture_output=True,
                text=True,
            )
            self.assertEqual(runtime.returncode, 0, runtime.stderr)
            result = json.loads(runtime.stdout)

        self.assertTrue(all(result["rejected"]))
        self.assertTrue(result["zeroIsNoOp"])

    def test_rejects_dense_primitive_subdivision_before_generation(self) -> None:
        spec = load_fixture()
        component = spec["componentTree"][0]
        component["primitive"] = "sphere"
        component["geometryDescriptor"]["subdivide"]["iterations"] = MAX_SUBDIVISION_ITERATIONS

        errors, _warnings = validate_spec(spec)

        self.assertTrue(
            any(
                "geometryDescriptor.subdivide.iterations" in error
                and "would produce" in error
                and "sphere" in error
                for error in errors
            ),
            errors,
        )

    def test_static_budget_allows_runtime_safe_cylinder_and_cone_subdivision(self) -> None:
        self.assertEqual(
            SUBDIVISION_SOURCE_FACE_ESTIMATES["capsule"],
            capsule_source_face_count(CAPSULE_CAP_SEGMENTS, CAPSULE_RADIAL_SEGMENTS),
        )
        self.assertGreaterEqual(
            SUBDIVISION_SOURCE_FACE_ESTIMATES["sphere"],
            2 * SPHERE_WIDTH_SEGMENTS * (SPHERE_HEIGHT_SEGMENTS - 1),
        )
        self.assertGreaterEqual(
            SUBDIVISION_SOURCE_FACE_ESTIMATES["torus"],
            2 * TORUS_TUBULAR_SEGMENTS * TORUS_RADIAL_SEGMENTS,
        )
        self.assertGreaterEqual(
            SUBDIVISION_SOURCE_FACE_ESTIMATES["plane-card"],
            2 * PLANE_WIDTH_SEGMENTS * PLANE_HEIGHT_SEGMENTS,
        )
        for primitive, maximum_safe_iterations in (("cylinder", 3), ("cone", 3), ("capsule", 2)):
            with self.subTest(primitive=primitive):
                spec = load_fixture()
                component = spec["componentTree"][0]
                component["primitive"] = primitive
                component["geometryDescriptor"]["subdivide"]["iterations"] = maximum_safe_iterations

                errors, _warnings = validate_spec(spec)

                self.assertEqual(errors, [])

                component["geometryDescriptor"]["subdivide"]["iterations"] = maximum_safe_iterations + 1
                errors, _warnings = validate_spec(spec)

                self.assertTrue(
                    any(
                        "geometryDescriptor.subdivide.iterations" in error
                        and "would produce" in error
                        and primitive in error
                        for error in errors
                    ),
                    errors,
                )

    def test_generated_cylinder_and_cone_subdivision_execute_safely(self) -> None:
        for primitive in ("cylinder", "cone", "capsule"):
            with self.subTest(primitive=primitive):
                spec = load_fixture()
                component = spec["componentTree"][0]
                component["primitive"] = primitive
                component["geometryDescriptor"]["subdivide"]["iterations"] = 1

                errors, _warnings = validate_spec(spec)
                self.assertEqual(errors, [])
                generated = generate(spec, "blockout")
                if primitive == "cone":
                    self.assertIn("new THREE.CylinderGeometry(0.5, 0.0001, 1, 48, 16)", generated)

                with tempfile.TemporaryDirectory(dir=showcase_root()) as directory:
                    work_dir = Path(directory)
                    compile_result, _module_path = compile_generated_module(generated, work_dir)
                    self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
                    runtime = subprocess.run(
                        [
                            "node",
                            "--input-type=module",
                            "--eval",
                            (
                                "import { createSubdivisionCageModel } from './build/subdivision.js'; "
                                "const model = createSubdivisionCageModel(); "
                                "const mesh = model.userData.sculptRuntime.meshes['rounded-cage']; "
                                "console.log(JSON.stringify({ "
                                "subdivision: mesh.geometry.userData.subdivision, "
                                "positionCount: mesh.geometry.getAttribute('position').count "
                                "}));"
                            ),
                        ],
                        cwd=work_dir,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(runtime.returncode, 0, runtime.stderr)
                    result = json.loads(runtime.stdout)

                self.assertEqual(result["subdivision"]["iterations"], 1)
                self.assertLessEqual(
                    result["subdivision"]["sourceFaceCount"],
                    SUBDIVISION_SOURCE_FACE_ESTIMATES[primitive],
                )
                self.assertGreater(result["subdivision"]["quadFaceCount"], 0)
                self.assertGreater(result["positionCount"], 0)

    def test_non_subdivided_cone_preserves_cone_geometry_output(self) -> None:
        # heightSegments is 1 here (CONE_HEIGHT_SEGMENTS), not 16 (CYLINDER_HEIGHT_SEGMENTS):
        # a real ConeGeometry(0.5, 1, 48, 16) measured 2448 raw / 2160 welded boundary
        # edges (a genuine topology defect, unlike box/cylinder/sphere/torus, which weld
        # cleanly at their own segment counts) -- a TAPERING cone does not weld cleanly at
        # 16 height segments the way a constant-radius cylinder does. heightSegments=1
        # (three.js's own default) welds to 0/0, verified directly; see
        # forge/tests/test_primitive_watertightness.py. The subdivision-requested
        # substitute geometry below is untouched by this and still uses
        # CYLINDER_HEIGHT_SEGMENTS -- it's a near-degenerate CylinderGeometry, not a
        # literal cone, so it doesn't have the tapering defect in the first place.
        spec = load_fixture()
        component = spec["componentTree"][0]
        component["primitive"] = "cone"
        del component["geometryDescriptor"]["subdivide"]

        generated = generate(spec, "blockout")

        self.assertIn("new THREE.ConeGeometry(0.5, 1, 48, 1)", generated)
        self.assertNotIn("new THREE.CylinderGeometry(0.5, 0.0001, 1, 48, 16)", generated)

    def test_default_instanced_cluster_subdivision_uses_closed_box_source(self) -> None:
        for iterations in (1, MAX_SUBDIVISION_ITERATIONS):
            with self.subTest(iterations=iterations):
                spec = load_fixture()
                component = spec["componentTree"][0]
                component["primitive"] = "instanced-cluster"
                component["geometryDescriptor"] = {"subdivide": {"iterations": iterations}}

                errors, _warnings = validate_spec(spec)
                self.assertEqual(errors, [])
                generated = generate(spec, "blockout")
                self.assertIn("new THREE.BoxGeometry(1, 1, 1)", generated)
                self.assertNotIn("new THREE.BoxGeometry(1, 1, 1, 12, 12, 12)", generated)

                with tempfile.TemporaryDirectory(dir=showcase_root()) as directory:
                    work_dir = Path(directory)
                    compile_result, _module_path = compile_generated_module(generated, work_dir)
                    self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
                    runtime = subprocess.run(
                        [
                            "node",
                            "--input-type=module",
                            "--eval",
                            (
                                "import { createSubdivisionCageModel } from './build/subdivision.js'; "
                                "const model = createSubdivisionCageModel(); "
                                "const mesh = model.userData.sculptRuntime.meshes['rounded-cage']; "
                                "console.log(JSON.stringify(mesh.geometry.userData.subdivision));"
                            ),
                        ],
                        cwd=work_dir,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(runtime.returncode, 0, runtime.stderr)
                    result = json.loads(runtime.stdout)

                self.assertEqual(result["sourceFaceCount"], SUBDIVISION_SOURCE_FACE_ESTIMATES["box"])
                self.assertEqual(result["quadFaceCount"], 6 * 4**iterations)

    def test_rejects_attached_box_subdivision_over_attachment_cylinder_budget(self) -> None:
        spec = load_fixture()
        component = spec["componentTree"][0]
        component["geometryDescriptor"]["subdivide"]["iterations"] = MAX_SUBDIVISION_ITERATIONS
        component["attachment"] = {
            "localStart": [0, 0, 0],
            "localEnd": [0, 1, 0],
            "baseRadius": 0.1,
            "endRadius": 0.05,
        }

        errors, _warnings = validate_spec(spec)

        self.assertTrue(
            any(
                "geometryDescriptor.subdivide.iterations" in error
                and "would produce" in error
                and "attachment cylinder" in error
                for error in errors
            ),
            errors,
        )

    def test_torus_subdivision_is_rejected_before_generation(self) -> None:
        spec = load_fixture()
        component = spec["componentTree"][0]
        component["primitive"] = "torus"
        component["geometryDescriptor"]["subdivide"]["iterations"] = 1

        errors, _warnings = validate_spec(spec)

        self.assertTrue(
            any(
                "torus subdivision topology is unsupported" in error
                for error in errors
            ),
            errors,
        )

        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            spec_path = work_dir / "torus-subdivision.json"
            output_path = work_dir / "model.ts"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "stage3_build" / "generate_threejs_factory.py"),
                    str(spec_path),
                    "--out",
                    str(output_path),
                    "--allow-nonstrict",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("torus subdivision topology is unsupported", result.stderr)
        self.assertFalse(output_path.exists())

    def test_plane_card_subdivision_is_rejected_before_generation(self) -> None:
        spec = load_fixture()
        component = spec["componentTree"][0]
        component["primitive"] = "plane-card"
        component["geometryDescriptor"]["subdivide"]["iterations"] = 1

        errors, _warnings = validate_spec(spec)

        self.assertTrue(
            any(
                "plane-card subdivision topology is unsupported" in error
                for error in errors
            ),
            errors,
        )

        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            spec_path = work_dir / "plane-card-subdivision.json"
            output_path = work_dir / "model.ts"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "stage3_build" / "generate_threejs_factory.py"),
                    str(spec_path),
                    "--out",
                    str(output_path),
                    "--allow-nonstrict",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("plane-card subdivision topology is unsupported", result.stderr)
        self.assertFalse(output_path.exists())

    def test_subdivision_preflight_covers_all_generator_paths(self) -> None:
        for primitive in ("tube", "lathe", "extrude", "ground-blade", "curve-sweep"):
            with self.subTest(primitive=primitive):
                spec = load_fixture()
                component = spec["componentTree"][0]
                component["primitive"] = primitive
                component["geometryDescriptor"]["subdivide"]["iterations"] = 1

                errors, _warnings = validate_spec(spec)

                self.assertTrue(
                    any(
                        "cannot statically budget emitted primitive" in error
                        and primitive in error
                        for error in errors
                    ),
                    errors,
                )

        spec = load_fixture()
        component = spec["componentTree"][0]
        component["primitive"] = "instanced-cluster"
        component["geometryDescriptor"] = {"subdivide": {"iterations": 1}}

        errors, _warnings = validate_spec(spec)

        self.assertEqual(errors, [])

        component["geometryDescriptor"]["baseGeometry"] = "tube"
        errors, _warnings = validate_spec(spec)

        self.assertTrue(
            any(
                "cannot statically budget emitted primitive" in error
                and "tube" in error
                for error in errors
            ),
            errors,
        )

        implicit_spec = load_implicit_fixture()
        implicit_component = implicit_spec["componentTree"][0]
        implicit_component["geometryDescriptor"]["subdivide"] = {"iterations": 1}
        errors, _warnings = validate_spec(implicit_spec)

        self.assertTrue(
            any(
                "cannot statically budget emitted primitive 'implicit sdf'" in error
                for error in errors
            ),
            errors,
        )

        implicit_component["attachment"] = {
            "localStart": [0, 0, 0],
            "localEnd": [0, 1, 0],
            "baseRadius": 0.1,
            "endRadius": 0.05,
        }
        errors, _warnings = validate_spec(implicit_spec)

        self.assertTrue(
            any(
                "cannot statically budget emitted primitive 'implicit sdf'" in error
                for error in errors
            ),
            errors,
        )

    def test_generated_subdivision_rejects_non_manifold_edges(self) -> None:
        spec = load_fixture()
        generated = generate(spec, "blockout")

        with tempfile.TemporaryDirectory(dir=showcase_root()) as directory:
            work_dir = Path(directory)
            compile_result, _module_path = compile_generated_module(
                generated,
                work_dir,
                export_subdivision_helper=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            runtime = subprocess.run(
                [
                    "node",
                    "--input-type=module",
                    "--eval",
                    (
                        "import * as THREE from 'three'; "
                        "import { subdivideCatmullClark, SubdivisionTopologyError } from './build/subdivision.js'; "
                        "const source = new THREE.BufferGeometry(); "
                        "source.setAttribute('position', new THREE.Float32BufferAttribute(["
                        "0, 0, 0, 1, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 1"
                        "], 3)); "
                        "source.setIndex([0, 1, 2, 1, 0, 3, 0, 1, 4]); "
                        "try { "
                        "subdivideCatmullClark(source, 1); "
                        "console.log(JSON.stringify({ rejected: false })); "
                        "} catch (error) { "
                        "console.log(JSON.stringify({ "
                        "rejected: error instanceof SubdivisionTopologyError, "
                        "name: error instanceof Error ? error.name : '' "
                        "})); "
                        "}"
                    ),
                ],
                cwd=work_dir,
                capture_output=True,
                text=True,
            )
            self.assertEqual(runtime.returncode, 0, runtime.stderr)
            result = json.loads(runtime.stdout)

        self.assertTrue(result["rejected"])
        self.assertEqual(result["name"], "SubdivisionTopologyError")

    def test_generated_subdivision_rejects_open_boundary_topology(self) -> None:
        spec = load_fixture()
        generated = generate(spec, "blockout")

        with tempfile.TemporaryDirectory(dir=showcase_root()) as directory:
            work_dir = Path(directory)
            compile_result, _module_path = compile_generated_module(
                generated,
                work_dir,
                export_subdivision_helper=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            runtime = subprocess.run(
                [
                    "node",
                    "--input-type=module",
                    "--eval",
                    (
                        "import * as THREE from 'three'; "
                        "import { subdivideCatmullClark, SubdivisionTopologyError } from './build/subdivision.js'; "
                        "const source = new THREE.BufferGeometry(); "
                        "source.setAttribute('position', new THREE.Float32BufferAttribute(["
                        "0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0"
                        "], 3)); "
                        "source.setIndex([0, 1, 2, 0, 2, 3]); "
                        "try { "
                        "subdivideCatmullClark(source, 1); "
                        "console.log(JSON.stringify({ rejected: false })); "
                        "} catch (error) { "
                        "console.log(JSON.stringify({ "
                        "rejected: error instanceof SubdivisionTopologyError, "
                        "name: error instanceof Error ? error.name : '', "
                        "reason: error instanceof SubdivisionTopologyError ? error.reason : '' "
                        "})); "
                        "}"
                    ),
                ],
                cwd=work_dir,
                capture_output=True,
                text=True,
            )
            self.assertEqual(runtime.returncode, 0, runtime.stderr)
            result = json.loads(runtime.stdout)

        self.assertTrue(result["rejected"])
        self.assertEqual(result["name"], "SubdivisionTopologyError")
        self.assertEqual(result["reason"], "open boundary edge 0:1 has 1 incident face")

    def test_generated_subdivision_rejects_disconnected_incident_face_fans(self) -> None:
        spec = load_fixture()
        generated = generate(spec, "blockout")

        with tempfile.TemporaryDirectory(dir=showcase_root()) as directory:
            work_dir = Path(directory)
            compile_result, _module_path = compile_generated_module(
                generated,
                work_dir,
                export_subdivision_helper=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            runtime = subprocess.run(
                [
                    "node",
                    "--input-type=module",
                    "--eval",
                    (
                        "import * as THREE from 'three'; "
                        "import { subdivideCatmullClark, SubdivisionTopologyError } from './build/subdivision.js'; "
                        "const source = new THREE.BufferGeometry(); "
                        "source.setAttribute('position', new THREE.Float32BufferAttribute(["
                        "0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, -1, 0, 0, 0, -1, 0, 0, 0, -1"
                        "], 3)); "
                        "source.setIndex(["
                        "0, 1, 2, 0, 2, 3, 0, 3, 1, 1, 3, 2, "
                        "0, 4, 5, 0, 5, 6, 0, 6, 4, 4, 6, 5"
                        "]); "
                        "try { "
                        "subdivideCatmullClark(source, 1); "
                        "console.log(JSON.stringify({ rejected: false })); "
                        "} catch (error) { "
                        "console.log(JSON.stringify({ "
                        "rejected: error instanceof SubdivisionTopologyError, "
                        "reason: error instanceof SubdivisionTopologyError ? error.reason : '' "
                        "})); "
                        "}"
                    ),
                ],
                cwd=work_dir,
                capture_output=True,
                text=True,
            )
            self.assertEqual(runtime.returncode, 0, runtime.stderr)
            result = json.loads(runtime.stdout)

        self.assertTrue(result["rejected"])
        self.assertIn("disconnected incident face fan", result["reason"])

    def test_primitive_only_factory_omits_subdivision_helper(self) -> None:
        spec = copy.deepcopy(load_fixture())
        del spec["componentTree"][0]["geometryDescriptor"]["subdivide"]

        generated = generate(spec, "blockout")

        self.assertNotIn("function subdivideCatmullClark", generated)
        self.assertNotIn("subdivideCatmullClark(geometry", generated)


if __name__ == "__main__":
    unittest.main(verbosity=2)
