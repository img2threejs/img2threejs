#!/usr/bin/env python3
"""Contracts for opt-in orthographic visual-hull geometry carving."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if __package__:
    from .showcase_test_support import showcase_root
else:
    from showcase_test_support import showcase_root


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "visual_hull_two_views.json"


def import_forge_modules():
    module_names = ("generate_threejs_factory", "validate_sculpt_spec", "visual_hull")
    original_modules = {name: sys.modules.pop(name, None) for name in module_names}
    original_path = sys.path[:]
    sys.path[:0] = [str(ROOT / "stage2_spec"), str(ROOT / "stage3_build")]
    try:
        from generate_threejs_factory import generate
        from validate_sculpt_spec import validate_spec
        from visual_hull import MAX_VISUAL_HULL_RESOLUTION, MAX_VISUAL_HULL_TRIANGLES
    finally:
        sys.path[:] = original_path
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return generate, validate_spec, MAX_VISUAL_HULL_RESOLUTION, MAX_VISUAL_HULL_TRIANGLES


generate, validate_spec, MAX_VISUAL_HULL_RESOLUTION, MAX_VISUAL_HULL_TRIANGLES = import_forge_modules()


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def compile_generated_module(generated: str, work_dir: Path) -> tuple[subprocess.CompletedProcess[str], Path]:
    source = work_dir / "visual-hull.ts"
    build_dir = work_dir / "build"
    source.write_text(generated, encoding="utf-8")
    result = subprocess.run(
        [
            (shutil.which("npx") or "npx"), "tsc", "--target", "ES2020", "--module", "NodeNext", "--moduleResolution", "NodeNext",
            "--strict", "--skipLibCheck", "--noUnusedLocals", "--noUnusedParameters", "--outDir", str(build_dir), str(source),
        ],
        cwd=showcase_root(),
        capture_output=True,
        text=True,
    )
    return result, build_dir / "visual-hull.js"


class VisualHullContractTest(unittest.TestCase):
    def test_accepts_two_orthographic_silhouettes_with_bounded_budget(self) -> None:
        spec = load_fixture()

        errors, _warnings = validate_spec(spec)

        self.assertEqual(errors, [])

    def test_rejects_invalid_visual_hull_shape_bounds_confidence_and_budget(self) -> None:
        cases = (
            ("projection", ("projection",), "perspective", "projection must be 'orthographic'"),
            ("bounds space", ("boundsSpace",), "world", "boundsSpace must be 'component-local'"),
            ("resolution", ("resolution",), MAX_VISUAL_HULL_RESOLUTION + 1, "must not exceed"),
            ("budget", ("triangleBudget",), 12, "requires at least"),
            ("bounds", ("bounds", "min", 0), 1.0, "must be less than"),
            ("confidence", ("views", 0, "confidence"), 1.1, "must be a number from 0 to 1"),
            ("mask", ("views", 0, "mask", 0), "00102", "must contain only '0' and '1'"),
            ("axis", ("views", 1, "axis"), "front", "must use distinct axes"),
        )
        for label, path, value, expected in cases:
            with self.subTest(label=label):
                spec = load_fixture()
                target = spec["componentTree"][0]["geometryDescriptor"]["visualHull"]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value

                errors, _warnings = validate_spec(spec)

                self.assertTrue(any("geometryDescriptor.visualHull" in error and expected in error for error in errors), errors)

    def test_requires_two_views_and_preserves_default_source_without_helper(self) -> None:
        visual_hull_spec = load_fixture()
        one_view_spec = copy.deepcopy(visual_hull_spec)
        one_view_spec["componentTree"][0]["geometryDescriptor"]["visualHull"]["views"] = one_view_spec["componentTree"][0]["geometryDescriptor"]["visualHull"]["views"][:1]
        default_spec = copy.deepcopy(visual_hull_spec)
        default_spec["componentTree"][0]["geometryDescriptor"] = {}

        errors, _warnings = validate_spec(one_view_spec)
        default_source = generate(default_spec, "blockout")
        visual_hull_source = generate(visual_hull_spec, "blockout")

        self.assertTrue(any("at least two views" in error for error in errors), errors)
        self.assertNotIn("function buildVisualHullGeometry", default_source)
        self.assertIn("function buildVisualHullGeometry", visual_hull_source)
        self.assertIn("hiddenRegions", visual_hull_source)

    def test_component_local_bounds_preserve_component_transform_application(self) -> None:
        """PLAN_1.5 WS-E: shape dimensions are baked into a part's geometry, never into
        the pivot Group's scale. The pivot node's own scale must stay identity (so a
        non-uniform component cannot distort a child that is later parented under it);
        the component's declared scale instead multiplies the geometry's vertex data
        directly. This supersedes the pre-WS-E contract, which asserted the opposite
        (`node.scale == component.transform.scale`, geometry left un-scaled) — that was
        exactly the anti-pattern WS-E removes, since it is what forced every character
        part to flatten onto a single hidden root instead of nesting under a real
        parent (see docs/PLAN_1.5_ANIMATION_READY_RIGS.md, WS-E)."""
        baseline_spec = load_fixture()
        scaled_spec = load_fixture()
        scaled_spec["componentTree"][0]["transform"] = {
            "position": [3.0, 2.0, -1.0],
            "rotation": [0.0, 0.0, 0.0],
            "scale": [2.0, 1.0, 3.0],
        }
        baseline_source = generate(baseline_spec, "blockout")
        scaled_source = generate(scaled_spec, "blockout")

        def read_bounds(generated: str, work_dir: Path) -> dict:
            compile_result, _module_path = compile_generated_module(generated, work_dir)
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            runtime = subprocess.run(
                [
                    "node", "--input-type=module", "--eval",
                    (
                        "import { createTwoViewVisualHullModel } from './build/visual-hull.js'; "
                        "const model = createTwoViewVisualHullModel(); "
                        "const node = model.userData.sculptRuntime.nodes['carved-body']; "
                        "const geometry = model.userData.sculptRuntime.meshes['carved-body'].geometry; "
                        "geometry.computeBoundingBox(); "
                        "console.log(JSON.stringify({ position: node.position.toArray(), scale: node.scale.toArray(), localMin: geometry.boundingBox.min.toArray(), localMax: geometry.boundingBox.max.toArray() }));"
                    ),
                ],
                cwd=work_dir,
                capture_output=True,
                text=True,
            )
            self.assertEqual(runtime.returncode, 0, runtime.stderr)
            return json.loads(runtime.stdout)

        with tempfile.TemporaryDirectory(dir=showcase_root()) as directory:
            base_dir = Path(directory) / "baseline"
            scaled_dir = Path(directory) / "scaled"
            base_dir.mkdir()
            scaled_dir.mkdir()
            baseline = read_bounds(baseline_source, base_dir)
            scaled = read_bounds(scaled_source, scaled_dir)

        # Position/rotation still cascade through the pivot Group exactly as before.
        self.assertEqual(scaled["position"], [3, 2, -1])
        # The pivot node's scale is always identity now, in both the default-scale and
        # the non-uniform-scale case — dimensions never live on this transform.
        self.assertEqual(baseline["scale"], [1, 1, 1])
        self.assertEqual(scaled["scale"], [1, 1, 1])
        # The component's declared [2, 1, 3] scale is applied directly to the geometry's
        # vertex data instead: the scaled local bounds equal the baseline (unit-scale)
        # local bounds multiplied elementwise by the component scale.
        factor = [2, 1, 3]
        for axis in range(3):
            self.assertAlmostEqual(scaled["localMin"][axis], baseline["localMin"][axis] * factor[axis], places=5)
            self.assertAlmostEqual(scaled["localMax"][axis], baseline["localMax"][axis] * factor[axis], places=5)

    def test_contradictory_silhouettes_raise_typed_occupancy_error(self) -> None:
        spec = load_fixture()
        views = spec["componentTree"][0]["geometryDescriptor"]["visualHull"]["views"]
        views[0]["mask"] = ["1111111111111111"] * 8 + ["0000000000000000"] * 8
        views[1]["mask"] = ["0000000000000000"] * 8 + ["1111111111111111"] * 8
        errors, _warnings = validate_spec(spec)
        self.assertEqual(errors, [])
        generated = generate(spec, "blockout")

        with tempfile.TemporaryDirectory(dir=showcase_root()) as directory:
            work_dir = Path(directory)
            compile_result, _module_path = compile_generated_module(generated, work_dir)
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            runtime = subprocess.run(
                [
                    "node", "--input-type=module", "--eval",
                    (
                        "import { createTwoViewVisualHullModel } from './build/visual-hull.js'; "
                        "try { createTwoViewVisualHullModel(); console.log(JSON.stringify({ rejected: false })); } "
                        "catch (error) { console.log(JSON.stringify({ rejected: error instanceof Error && error.name === 'VisualHullOccupancyError', name: error instanceof Error ? error.name : '' })); }"
                    ),
                ],
                cwd=work_dir,
                capture_output=True,
                text=True,
            )
            self.assertEqual(runtime.returncode, 0, runtime.stderr)
            result = json.loads(runtime.stdout)

        self.assertTrue(result["rejected"])
        self.assertEqual(result["name"], "VisualHullOccupancyError")

    def test_generated_visual_hull_is_deterministic_welded_and_manifold(self) -> None:
        spec = load_fixture()
        generated = generate(spec, "blockout")
        self.assertEqual(generated, generate(copy.deepcopy(spec), "blockout"))

        with tempfile.TemporaryDirectory(dir=showcase_root()) as directory:
            work_dir = Path(directory)
            compile_result, _module_path = compile_generated_module(generated, work_dir)
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            runtime = subprocess.run(
                [
                    "node", "--input-type=module", "--eval",
                    (
                        "import { createTwoViewVisualHullModel } from './build/visual-hull.js'; "
                        "const model = createTwoViewVisualHullModel(); "
                        "const geometry = model.userData.sculptRuntime.meshes['carved-body'].geometry; "
                        "const position = geometry.getAttribute('position'); const uv = geometry.getAttribute('uv'); "
                        "const index = geometry.getIndex(); const edges = new Map(); "
                        "for (let offset = 0; offset < index.count; offset += 3) { "
                        "const triangle = [index.getX(offset), index.getX(offset + 1), index.getX(offset + 2)]; "
                        "for (let corner = 0; corner < 3; corner += 1) { const a = triangle[corner]; const b = triangle[(corner + 1) % 3]; const key = a < b ? `${a}:${b}` : `${b}:${a}`; edges.set(key, (edges.get(key) ?? 0) + 1); } } "
                        "const repeat = createTwoViewVisualHullModel().userData.sculptRuntime.meshes['carved-body'].geometry; "
                        "console.log(JSON.stringify({ positions: position.count, uvCount: uv.count, triangles: index.count / 3, manifold: Array.from(edges.values()).every((count) => count === 2), metadata: geometry.userData.visualHull, sameIndexCount: repeat.getIndex().count === index.count }));"
                    ),
                ],
                cwd=work_dir,
                capture_output=True,
                text=True,
            )
            self.assertEqual(runtime.returncode, 0, runtime.stderr)
            result = json.loads(runtime.stdout)

        self.assertGreater(result["positions"], 8)
        self.assertEqual(result["uvCount"], result["positions"])
        self.assertLessEqual(result["triangles"], 50000)
        self.assertTrue(result["manifold"])
        self.assertTrue(result["sameIndexCount"])
        self.assertTrue(result["metadata"]["lowConfidence"]["hiddenRegions"])
        self.assertEqual(result["metadata"]["observedAxes"], ["front", "side"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
