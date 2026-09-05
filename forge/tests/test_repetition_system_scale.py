#!/usr/bin/env python3
"""PLAN_1.5 WS-E follow-up: a repetitionSystems cluster parented to a non-uniformly
scaled component must not inherit that component's shape.

`generate_threejs_factory.py`'s repetition-system emitter parents its
`THREE.InstancedMesh` directly onto the referenced component's pivot Group
(`parent.add(cluster)`). Before the WS-E fix that Group carried the component's
declared dimensional scale (`node.scale.set(component.transform.scale)`), so an
instanced fastener/rivet/spoke was implicitly re-scaled by its host's shape, and a
`radial` ring's placement (computed in the plane perpendicular to `placement.axis`)
was squashed into an ellipse by a non-uniform host. After the fix the pivot Group's
scale is always identity, so `instanceScale` and `placement.radius` are absolute,
independent of the host's shape.

This is a REAL-RUN test (tsc + node, same pattern as `test_hierarchy_scale.py` /
`test_visual_hull.py`): a "plate" component (box, sharply non-uniform scale
[4, 1, 0.3]) parents a `repetitionSystems` entry ("rivets", 8 instances, radial
placement about the Y axis). Every instance's ACTUAL executed world transform is
read back via `InstancedMesh.getMatrixAt` composed with `cluster.matrixWorld` --
no hand-computed expectation is substituted for execution.

Pure Python 3.10+ stdlib on this side. No pip installs.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if __package__:
    from .showcase_test_support import NPX, showcase_root
else:
    from showcase_test_support import NPX, showcase_root

ROOT = Path(__file__).resolve().parent.parent


def import_forge_modules():
    module_names = ("generate_threejs_factory", "validate_sculpt_spec")
    original_modules = {name: sys.modules.pop(name, None) for name in module_names}
    original_path = sys.path[:]
    sys.path[:0] = [str(ROOT / "stage2_spec"), str(ROOT / "stage3_build")]
    try:
        from generate_threejs_factory import generate
        from validate_sculpt_spec import validate_spec
    finally:
        sys.path[:] = original_path
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return generate, validate_spec


generate, validate_spec = import_forge_modules()

# A sharply non-uniform "plate" (box, scale [4, 1, 0.3]) parenting a radial ring of
# 8 "rivets" (instanceScale [0.05, 0.05, 0.05], radius 1.0, axis +Y). The ring's
# placement plane is perpendicular to axis +Y, i.e. the local XZ plane -- exactly
# the plane the plate's X and Z scale factors (4 and 0.3) would squash if
# instanceScale/placement were still relative to the host's declared scale.
RIVETED_PLATE_SPEC = {
    "targetName": "Riveted Plate",
    "schemaVersion": "2.1",
    "suitability": "pass",
    "coordinateFrame": {},
    "silhouette": {},
    "proceduralStrategy": [],
    "materials": [{"id": "clay"}],
    "componentTree": [
        {
            "id": "plate",
            "name": "Plate",
            "level": "macro",
            "role": "body",
            "primitive": "box",
            "parent": None,
            "material": "clay",
            "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [4.0, 1.0, 0.3]},
        },
    ],
    "repetitionSystems": [
        {
            "id": "rivets",
            "parent": "plate",
            "level": "macro",
            "count": 8,
            "primitive": "sphere",
            "material": "clay",
            "instanceScale": [0.05, 0.05, 0.05],
            "placement": {"mode": "radial", "axis": [0.0, 1.0, 0.0], "radius": 1.0, "startAngleDeg": 0.0},
        },
    ],
}


def compile_generated_module(generated: str, work_dir: Path) -> tuple[subprocess.CompletedProcess[str], Path]:
    source = work_dir / "repetition-system-scale.ts"
    build_dir = work_dir / "build"
    source.write_text(generated, encoding="utf-8")
    result = subprocess.run(
        [
            NPX, "tsc", "--target", "ES2020", "--module", "NodeNext", "--moduleResolution", "NodeNext",
            "--strict", "--skipLibCheck", "--noUnusedLocals", "--noUnusedParameters",
            "--outDir", str(build_dir), str(source),
        ],
        cwd=showcase_root(),
        capture_output=True,
        text=True,
    )
    return result, build_dir / "repetition-system-scale.js"


_EVAL_SCRIPT = """
import * as THREE from 'three';
import { createRivetedPlateModel } from './build/repetition-system-scale.js';

const model = createRivetedPlateModel();
const plate = model.userData.sculptRuntime.nodes['plate'];
const cluster = plate.children.find((child) => child.name === 'rivets');
if (!cluster) throw new Error("repetition cluster 'rivets' not found under plate");

model.updateMatrixWorld(true);

const localMatrix = new THREE.Matrix4();
const combined = new THREE.Matrix4();
const pos = new THREE.Vector3();
const quat = new THREE.Quaternion();
const scl = new THREE.Vector3();
const distances = [];
const scales = [];
for (let i = 0; i < cluster.count; i += 1) {
  cluster.getMatrixAt(i, localMatrix);
  combined.multiplyMatrices(cluster.matrixWorld, localMatrix);
  combined.decompose(pos, quat, scl);
  distances.push(pos.length());
  scales.push(scl.toArray());
}

console.log(JSON.stringify({
  plateScale: plate.scale.toArray(),
  instanceCount: cluster.count,
  distances,
  scales,
}));
"""


class RepetitionSystemScaleTest(unittest.TestCase):
    result: dict

    @classmethod
    def setUpClass(cls) -> None:
        errors, _warnings = validate_spec(RIVETED_PLATE_SPEC)
        if errors:
            raise RuntimeError(f"FAIL CLOSED: fixture spec did not validate: {errors}")
        generated = generate(RIVETED_PLATE_SPEC, "blockout")

        cls._tempdir_ctx = tempfile.TemporaryDirectory(dir=showcase_root())
        work_dir = Path(cls._tempdir_ctx.name)
        compile_result, _module_path = compile_generated_module(generated, work_dir)
        if compile_result.returncode != 0:
            raise RuntimeError(f"FAIL CLOSED: tsc did not compile the emitted module: {compile_result.stderr}")

        runtime = subprocess.run(
            ["node", "--input-type=module", "--eval", _EVAL_SCRIPT],
            cwd=work_dir,
            capture_output=True,
            text=True,
        )
        if runtime.returncode != 0:
            raise RuntimeError(f"FAIL CLOSED: node execution of the emitted module failed: {runtime.stderr}")
        try:
            cls.result = json.loads(runtime.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"FAIL CLOSED: result was not parseable JSON: {exc}\nstdout: {runtime.stdout!r}") from exc

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tempdir_ctx.cleanup()

    def test_cluster_found_all_instances_present(self) -> None:
        self.assertEqual(self.result["instanceCount"], 8)

    def test_host_pivot_scale_is_identity(self) -> None:
        self.assertEqual(self.result["plateScale"], [1, 1, 1])

    def test_instance_world_scale_equals_instance_scale_not_host_scale(self) -> None:
        # Regression check: instanceScale [0.05, 0.05, 0.05] must reach the screen
        # unmodified. Under the pre-fix behaviour it would have been multiplied by
        # the plate's declared scale [4, 1, 0.3], measuring [0.2, 0.05, 0.015].
        for index, scale in enumerate(self.result["scales"]):
            for axis, value in enumerate(scale):
                self.assertAlmostEqual(
                    value, 0.05, places=4,
                    msg=f"instance {index} axis {axis}: world scale {value} != instanceScale 0.05 "
                    "(looks like it inherited the host's declared scale)",
                )

    def test_radial_ring_stays_circular_under_a_nonuniform_host(self) -> None:
        # This is the mutation check: under the pre-fix behaviour the plate's
        # [4, 1, 0.3] scale would cascade onto the ring (which lies in the local XZ
        # plane, perpendicular to placement.axis +Y), squashing distance-from-center
        # from 0.5 up to 2.0 along X and down to 0.15 along Z depending on angle --
        # an ellipse, not a ring. With the fix every instance sits at the same
        # distance from center regardless of angle.
        distances = self.result["distances"]
        self.assertGreater(min(distances), 0.1, "ring collapsed toward the center")
        self.assertAlmostEqual(
            min(distances), max(distances), places=2,
            msg=f"ring is not circular: distances range {min(distances)}..{max(distances)} (expected a constant radius)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
