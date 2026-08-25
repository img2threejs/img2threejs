#!/usr/bin/env python3
"""PLAN_1.5 WS-E kill test: a non-uniformly-scaled parent must not distort a child
nested under it.

Question this answers: does `generate_threejs_factory.py` still conflate a part's
*shape dimensions* with its *place in the hierarchy*? Before WS-E, a component's
declared scale was applied directly to the pivot `THREE.Group` (`node.scale.set(...)`),
which is exactly the anti-pattern documented in
`forge/stage2_spec/new_sculpt_spec.py` (search "FLATTENED to world space"): nesting a
child under a non-uniformly-scaled parent Group would cascade that scale onto the
child and distort it, which is why every character part was flattened onto a single
hidden, unit-scaled root instead of a real parent chain. See
docs/PLAN_1.5_ANIMATION_READY_RIGS.md, WS-E ("Proportion baked into geometry").

This is a REAL-RUN test: it emits TypeScript from a hand-written two-component spec
(a non-uniformly-scaled "torso" box parenting a unit "head" sphere), executes that
emitted code with the showcase's real `three` via `tsc` + `node` (the same pattern
`test_visual_hull.py` / `test_subdivision.py` already use — no esbuild bundling is
needed because tsc compiles into a tempdir nested inside the showcase checkout, so
`three` resolves from its node_modules), and measures the child's actual geometry
and world transform. No hand-computed expectation is substituted for execution.

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
    from .showcase_test_support import showcase_root
else:
    from showcase_test_support import showcase_root

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

# A non-uniformly-scaled "torso" (box, scale [3, 1, 0.2] -- a flat wide slab, not a
# cube) parenting a "head" (unit sphere) via a REAL parent reference, not "root".
# Nothing in this repo's templates currently authors this shape of spec (every
# existing template parents every part to "root" -- see new_sculpt_spec.py's
# `_cnode(..., "root", ...)` calls) but the schema and the generator must both
# support it once WS-E lands, which is exactly what this test exercises directly.
NESTED_SCALE_SPEC = {
    "targetName": "Nested Scale Rig",
    "schemaVersion": "2.1",
    "suitability": "pass",
    "coordinateFrame": {},
    "silhouette": {},
    "proceduralStrategy": [],
    "materials": [{"id": "clay"}],
    "componentTree": [
        {
            "id": "torso",
            "name": "Torso",
            "level": "macro",
            "role": "body",
            "primitive": "box",
            "parent": None,
            "material": "clay",
            "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [3.0, 1.0, 0.2]},
        },
        {
            "id": "head",
            "name": "Head",
            "level": "macro",
            "role": "body",
            "primitive": "sphere",
            "parent": "torso",
            "material": "clay",
            "transform": {"position": [0.3, 2.0, 0.3], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]},
        },
    ],
}


def compile_generated_module(generated: str, work_dir: Path) -> tuple[subprocess.CompletedProcess[str], Path]:
    source = work_dir / "hierarchy-scale.ts"
    build_dir = work_dir / "build"
    source.write_text(generated, encoding="utf-8")
    result = subprocess.run(
        [
            "npx", "tsc", "--target", "ES2020", "--module", "NodeNext", "--moduleResolution", "NodeNext",
            "--strict", "--skipLibCheck", "--noUnusedLocals", "--noUnusedParameters",
            "--outDir", str(build_dir), str(source),
        ],
        cwd=showcase_root(),
        capture_output=True,
        text=True,
    )
    return result, build_dir / "hierarchy-scale.js"


# Rotate the parent 90 degrees about Y after the first measurement: the head's local
# offset has nonzero X and Z (see NESTED_SCALE_SPEC above) so this is guaranteed to
# move it, unlike a rotation axis the offset happens to sit on.
_EVAL_SCRIPT = """
import * as THREE from 'three';
import { createNestedScaleRigModel } from './build/hierarchy-scale.js';

const model = createNestedScaleRigModel();
const torso = model.userData.sculptRuntime.nodes['torso'];
const head = model.userData.sculptRuntime.nodes['head'];
const headMesh = model.userData.sculptRuntime.meshes['head'];

headMesh.geometry.computeBoundingBox();
const localBox = headMesh.geometry.boundingBox;
const localExtent = [
  localBox.max.x - localBox.min.x,
  localBox.max.y - localBox.min.y,
  localBox.max.z - localBox.min.z,
];

model.updateMatrixWorld(true);
const worldBoxBefore = new THREE.Box3().setFromObject(headMesh);
const worldExtentBefore = [
  worldBoxBefore.max.x - worldBoxBefore.min.x,
  worldBoxBefore.max.y - worldBoxBefore.min.y,
  worldBoxBefore.max.z - worldBoxBefore.min.z,
];
const worldPosBefore = new THREE.Vector3();
head.getWorldPosition(worldPosBefore);

torso.rotation.set(0, Math.PI / 2, 0);
model.updateMatrixWorld(true);
const worldPosAfter = new THREE.Vector3();
head.getWorldPosition(worldPosAfter);

console.log(JSON.stringify({
  torsoScale: torso.scale.toArray(),
  headScale: head.scale.toArray(),
  localExtent,
  worldExtentBefore,
  worldPosBefore: worldPosBefore.toArray(),
  worldPosAfter: worldPosAfter.toArray(),
}));
"""


class HierarchyScaleTest(unittest.TestCase):
    result: dict

    @classmethod
    def setUpClass(cls) -> None:
        errors, _warnings = validate_spec(NESTED_SCALE_SPEC)
        if errors:
            raise RuntimeError(f"FAIL CLOSED: fixture spec did not validate: {errors}")
        generated = generate(NESTED_SCALE_SPEC, "blockout")

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

    def test_non_attachment_primitives_keep_strict_endpoint_types(self) -> None:
        """Ordinary primitives must compile without invented attachment variables.

        A literal-null endpoint narrows the shared guarded branches to `never` under strict
        TypeScript. The pure helper keeps the same runtime null while preserving its declared
        `AttachmentEndpoint | null` return type.
        """
        generated = generate(NESTED_SCALE_SPEC, "blockout")
        self.assertIn("const endpoint_torso_0 = makeAttachmentEndpoint(null);", generated)
        self.assertIn("const endpoint_head_1 = makeAttachmentEndpoint(null);", generated)
        self.assertNotIn("const attachment_torso_0", generated)
        self.assertNotIn("const attachment_head_1", generated)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tempdir_ctx.cleanup()

    def test_pivot_nodes_always_carry_identity_scale(self) -> None:
        # WS-E: the hierarchy transform (the pivot Group) never carries a part's shape
        # dimensions -- not even for "torso", whose authored scale [3, 1, 0.2] is
        # sharply non-uniform. If this regresses to node.scale.set(componentScale),
        # this assertion catches it directly.
        self.assertEqual(self.result["torsoScale"], [1, 1, 1])
        self.assertEqual(self.result["headScale"], [1, 1, 1])

    def test_child_geometry_is_not_distorted_by_a_nonuniform_parent(self) -> None:
        # "head" is an authored unit sphere (scale [1, 1, 1]). Its OWN local geometry
        # must be a plain unit sphere (diameter 1 on every axis) regardless of what its
        # parent "torso" declares.
        for axis, extent in enumerate(self.result["localExtent"]):
            self.assertAlmostEqual(extent, 1.0, places=3, msg=f"axis {axis}: local geometry is distorted")

    def test_child_world_shape_is_undistorted_by_a_nonuniform_parent(self) -> None:
        # This is the mutation check: torso's non-uniform scale [3, 1, 0.2] must not
        # cascade onto head through the THREE.Group hierarchy. Reverting the WS-E fix
        # (re-applying component scale to node.scale instead of to the geometry) makes
        # torso's runtime Group.scale become [3, 1, 0.2]; since head is parented under
        # torso, head's world bounding box would then measure roughly
        # [3, 1, 0.2] instead of [1, 1, 1] -- an egg-shaped head. This assertion fails
        # under that regression and passes under the fix.
        for axis, extent in enumerate(self.result["worldExtentBefore"]):
            self.assertAlmostEqual(extent, 1.0, places=2, msg=f"axis {axis}: world shape is distorted by parent scale")

    def test_rotating_parent_moves_descendant(self) -> None:
        # Definition of done #2: rotating a parent node must move its descendants --
        # position/rotation still cascade through the Group chain exactly as before.
        self.assertNotEqual(self.result["worldPosBefore"], self.result["worldPosAfter"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
