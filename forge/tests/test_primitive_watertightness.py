#!/usr/bin/env python3
"""Measure boundary/non-manifold edges on every emitted primitive, real-executed.

Nothing in the suite measured this before this file existed. See
docs/PLAN_1.5_ANIMATION_READY_RIGS.md and the WS-E capsule fix in
generate_threejs_factory.py.

Two distinct properties are measured per primitive, both real-executed (tsc + node,
same pattern as test_hierarchy_scale.py) rather than hand-computed:

  - `raw`: boundary/non-manifold edges on the geometry's own index buffer, exactly
    as emitted -- no welding, no dedup.
  - `welded`: TRUE topology, identifying vertices by rounded position (the same
    1e-6-precision key subdivideCatmullClark's own `vertexIndexAt` uses) and
    dropping any triangle that becomes degenerate under that identity -- exactly
    what subdivideCatmullClark does before it ever builds an edge graph. This is
    deliberately NOT a plain `BufferGeometryUtils.mergeVertices()` call: mergeVertices
    re-indexes vertices but keeps every triangle reference, including ones that
    become degenerate after the merge, and a degenerate triangle (a, a, b)
    double-counts the surviving edge (a, b) -- producing a "non-manifold" reading
    for topology that is actually a perfectly ordinary closed 2-manifold. This
    exact mistake is why an earlier version of this file, and the report that
    preceded it, wrongly concluded the raw capsule was non-manifold (see the
    capsule finding below) -- corrected once the discrepancy between "mergeVertices
    says non-manifold" and "the real subdivideCatmullClark completes without
    throwing" was run down to its root cause instead of accepted at face value.

Findings encoded as assertions here (each verified by running this exact
tsc+node harness against the TRUE-topology method above, not by inspecting source
or trusting a single measurement tool):

  - box, cylinder, sphere, ellipsoid, torus, instanced-cluster (defaults to a box
    base), extrude, curve-sweep, capsule: 0/0 once welded by true position identity.
    Their raw boundary count is the normal, benign UV-seam duplication every
    textured three.js primitive has; welding proves the underlying topology is
    genuinely closed. capsule (built via buildWatertightCapsule, not
    THREE.CapsuleGeometry) is additionally 0/0 even RAW, with no degenerate
    triangles at all -- closed by construction, not by a weld.
  - cone: 0/0 once welded -- but the ORIGINAL construction (heightSegments=16,
    CYLINDER_HEIGHT_SEGMENTS) measured 2448 raw / 2160 welded boundary edges, with
    ZERO degenerate triangles filtered -- a GENUINE topology defect, verified with
    this exact rigorous method, unrelated to the capsule's degenerate-triangle
    counting artifact. A tapering cone does not weld cleanly at 16 height segments
    the way a constant-radius cylinder does. Fixed by giving cone its own
    CONE_HEIGHT_SEGMENTS=1 (three.js's own default). The subdivision-requested
    substitute geometry (a near-degenerate CylinderGeometry) is untouched.
  - plane-card, tube, lathe (with the generator's default open profile): boundary
    edges are EXPECTED and correct -- these are not closed solids by design (a flat
    card, an open-ended pipe, and a lathed profile that never touches the axis at
    either end all have a real physical edge).
  - ground-blade: CONFIRMED, UNFIXED topology defect, found while building this
    measurement (a fully custom-built BufferGeometry, not a three.js primitive) --
    measures 6 real boundary edges under true topology (an actual open/unclosed
    region; a 7th "non-manifold" edge from an earlier, mergeVertices-based
    measurement was the same degenerate-triangle counting artifact as the capsule's
    -- one harmless zero-area sliver triangle, correctly excluded here). Off the
    humanoid character's component path (no ground-blade components), small,
    reported to team-lead and left as-is by direction; pinned here as an
    exact-count tripwire so it cannot silently get worse.

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
        from generate_threejs_factory import generate, VALID_PRIMITIVES
        from validate_sculpt_spec import validate_spec
    finally:
        sys.path[:] = original_path
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return generate, validate_spec, VALID_PRIMITIVES


generate, validate_spec, VALID_PRIMITIVES = import_forge_modules()
PRIMITIVES = sorted(VALID_PRIMITIVES)

# Every closed-solid primitive that MUST be watertight (0 boundary, 0 non-manifold)
# after a position-only weld -- box/cylinder/sphere/ellipsoid/torus/instanced-cluster
# via their normal, benign UV-seam duplication, capsule via true construction (RAW,
# no weld needed).
# tapered-sweep caps both ends by default (capEnds), so it is a closed solid; its ring seam
# duplicates one column of vertices exactly like the UV seam on a cylinder, and welds away.
EXPECTED_WELDED_WATERTIGHT = {"box", "cylinder", "sphere", "ellipsoid", "torus", "instanced-cluster", "extrude", "curve-sweep", "capsule", "cone", "tapered-sweep"}
# capsule needs no weld at all: built closed by construction.
EXPECTED_WATERTIGHT_EVEN_RAW = {"capsule"}
# Inherently open shapes by design (a real edge is CORRECT, not a defect): a flat
# card, a tube with no end caps (closed: false, the generator's default), and the
# generator's default lathe profile (points [[0.3,-0.5],[0.15,0],[0.3,0.5]]) which
# never touches the lathe axis at either end, i.e. an open vessel with no caps.
EXPECTED_OPEN_BY_DESIGN = {"plane-card", "tube", "lathe"}
# Confirmed, unfixed defects (see module docstring) -- pinned to their measured
# values as a regression tripwire, not silently ignored. If a future fix changes
# these numbers, update this dict together with the fix, do not just relax it.
KNOWN_DEFECT_WELDED_COUNTS = {
    # 6 real boundary edges (an actual open/unclosed region in this custom-built
    # BufferGeometry) plus one harmless degenerate (zero-area) triangle, which the
    # true-topology method correctly excludes rather than double-counting it into a
    # false non-manifold reading (see module docstring).
    "ground-blade": {"boundary": 6, "nonManifold": 0},
}

assert set(PRIMITIVES) == (
    EXPECTED_WELDED_WATERTIGHT | EXPECTED_OPEN_BY_DESIGN | set(KNOWN_DEFECT_WELDED_COUNTS)
), "every VALID_PRIMITIVES entry must be categorized in this test file"


def _ground_blade_descriptor() -> dict:
    # _DEFAULT_BLADE_SPEC used to carry a "spineFlat" key that buildGroundBladeGeometry's
    # TS parameter type didn't declare, which failed `tsc --strict` on ANY spec using
    # ground-blade with no explicit bladeSpec override (found while building this
    # probe, since fixed by dropping the dead key from _DEFAULT_BLADE_SPEC). Kept as
    # an explicit descriptor here anyway, for a stable probe shape independent of
    # that module-level default.
    return {
        "bladeSpec": {
            "stations": [[0.0, 0.08, -0.09], [0.5, 0.08, -0.1], [0.88, 0.0, 0.0]],
            "thickness": 0.05,
            "grindFrac": 0.55,
            "swedgeFromTipFrac": 0.34,
        }
    }


def build_probe_spec() -> dict:
    components = [
        {
            "id": "root", "name": "Root", "level": "macro", "role": "body",
            "primitive": "box", "parent": None, "material": "clay",
            "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]},
        }
    ]
    for index, primitive in enumerate(PRIMITIVES):
        component = {
            "id": f"probe-{primitive}", "name": f"Probe {primitive}", "level": "macro", "role": "body",
            "primitive": primitive, "parent": None, "material": "clay",
            "transform": {"position": [float(index) * 3.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]},
        }
        if primitive == "ground-blade":
            component["geometryDescriptor"] = _ground_blade_descriptor()
        components.append(component)
    return {
        "targetName": "Primitive Watertightness Probe",
        "schemaVersion": "2.1",
        "suitability": "pass",
        "coordinateFrame": {},
        "silhouette": {},
        "proceduralStrategy": [],
        "materials": [{"id": "clay"}],
        "componentTree": components,
    }


def compile_generated_module(generated: str, work_dir: Path) -> tuple[subprocess.CompletedProcess[str], Path]:
    source = work_dir / "primitive-watertightness.ts"
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
    return result, build_dir / "primitive-watertightness.js"


_EVAL_SCRIPT = """
import * as THREE from 'three';
import { createPrimitiveWatertightnessProbeModel } from './build/primitive-watertightness.js';

const primitives = __PRIMITIVES_JSON__;
const model = createPrimitiveWatertightnessProbeModel();

// RAW: boundary/non-manifold edges on the geometry's own index buffer, exactly as
// emitted -- no welding, no dedup. This is where a UV seam's duplicate vertex shows
// up as a "boundary" edge (benign, expected for a textured primitive).
function rawEdgeStats(geometry) {
  let index = geometry.index;
  const position = geometry.getAttribute('position');
  if (!index) {
    const arr = [];
    for (let i = 0; i < position.count; i++) arr.push(i);
    index = new THREE.Uint32BufferAttribute(arr, 1);
  }
  const counts = new Map();
  for (let offset = 0; offset + 2 < index.count; offset += 3) {
    const tri = [index.getX(offset), index.getX(offset + 1), index.getX(offset + 2)];
    for (let e = 0; e < 3; e++) {
      const a = tri[e];
      const b = tri[(e + 1) % 3];
      if (a === b) continue;
      const key = a < b ? a + ':' + b : b + ':' + a;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
  }
  let boundary = 0;
  let nonManifold = 0;
  for (const c of counts.values()) {
    if (c === 1) boundary += 1;
    else if (c > 2) nonManifold += 1;
  }
  return { boundary, nonManifold };
}

// TRUE TOPOLOGY: identify vertices by ROUNDED POSITION (1e-6 precision -- the same
// key subdivideCatmullClark's own vertexIndexAt() uses) and DROP any triangle that
// becomes degenerate (two or more corners collapse to the same vertex) under that
// identity, exactly like subdivideCatmullClark does, BEFORE counting edges.
//
// This is NOT the same as a plain three.js mergeVertices() weld, and the
// difference matters: mergeVertices happily re-indexes vertices but keeps every
// original triangle reference, including ones that become degenerate after the
// merge. A degenerate triangle (a, a, b) contributes edges (a,a) [a self-loop,
// ignored] and TWO instances of the SAME undirected edge (a,b) -- so if edge (a,b)
// already has 2 legitimate incident triangles, one degenerate triangle silently
// inflates its count to 4, reading as "non-manifold" when the true topology is a
// perfectly ordinary closed 2-manifold. This is exactly what happened investigating
// the raw capsule: a plain position-only mergeVertices() reported 64 non-manifold
// edges, stable across weld tolerances from 1e-4 to 1e-10 (ruling out a tolerance
// problem) -- but replicating subdivideCatmullClark's own degenerate-triangle-aware
// vertex identity finds ZERO non-manifold edges, with the discarded-degenerate
// count (64) matching the false non-manifold count exactly. Confirmed independently
// by feeding a raw (pre-fix) THREE.CapsuleGeometry directly into the real,
// unmodified subdivideCatmullClark(..., 1): it completes without throwing, which a
// genuinely non-manifold input would not survive (see
// test_generated_subdivision_rejects_non_manifold_edges, which DOES throw on a
// hand-built 3-triangles-one-edge fixture).
//
// cone's defect is NOT of this kind -- re-verified with this exact method
// (degenerateFiltered: 0, boundary: 2160 either way) -- it is a genuine topology
// defect, unrelated to degenerate-triangle counting.
function trueTopologyStats(geometry) {
  const position = geometry.getAttribute('position');
  const index = geometry.index;
  const count = index ? index.count : position.count;
  const byKey = new Map();
  const remap = new Int32Array(position.count);
  for (let i = 0; i < position.count; i += 1) {
    const key = Math.round(position.getX(i) * 1e6) + ',' + Math.round(position.getY(i) * 1e6) + ',' + Math.round(position.getZ(i) * 1e6);
    let id = byKey.get(key);
    if (id === undefined) { id = byKey.size; byKey.set(key, id); }
    remap[i] = id;
  }
  const triangles = [];
  let degenerateFiltered = 0;
  for (let offset = 0; offset + 2 < count; offset += 3) {
    const ai = index ? index.getX(offset) : offset;
    const bi = index ? index.getX(offset + 1) : offset + 1;
    const ci = index ? index.getX(offset + 2) : offset + 2;
    const a = remap[ai];
    const b = remap[bi];
    const c = remap[ci];
    if (a !== b && b !== c && c !== a) triangles.push([a, b, c]);
    else degenerateFiltered += 1;
  }
  const edgeCounts = new Map();
  for (const [a, b, c] of triangles) {
    for (const [u, v] of [[a, b], [b, c], [c, a]]) {
      const key = u < v ? u + ':' + v : v + ':' + u;
      edgeCounts.set(key, (edgeCounts.get(key) ?? 0) + 1);
    }
  }
  let boundary = 0;
  let nonManifold = 0;
  for (const c of edgeCounts.values()) {
    if (c === 1) boundary += 1;
    else if (c > 2) nonManifold += 1;
  }
  return { boundary, nonManifold, degenerateFiltered };
}

const rows = {};
for (const prim of primitives) {
  const mesh = model.userData.sculptRuntime.meshes['probe-' + prim];
  if (!mesh) { rows[prim] = { error: 'mesh not found' }; continue; }
  const raw = rawEdgeStats(mesh.geometry);
  const welded = trueTopologyStats(mesh.geometry);
  rows[prim] = { raw, welded };
}
console.log(JSON.stringify(rows));
"""


class PrimitiveWatertightnessTest(unittest.TestCase):
    result: dict

    @classmethod
    def setUpClass(cls) -> None:
        spec = build_probe_spec()
        errors, _warnings = validate_spec(spec)
        if errors:
            raise RuntimeError(f"FAIL CLOSED: probe spec did not validate: {errors}")
        generated = generate(spec, "blockout")

        cls._tempdir_ctx = tempfile.TemporaryDirectory(dir=showcase_root())
        work_dir = Path(cls._tempdir_ctx.name)
        compile_result, _module_path = compile_generated_module(generated, work_dir)
        if compile_result.returncode != 0:
            raise RuntimeError(f"FAIL CLOSED: tsc did not compile the emitted module: {compile_result.stderr}")

        eval_script = _EVAL_SCRIPT.replace("__PRIMITIVES_JSON__", json.dumps(PRIMITIVES))
        runtime = subprocess.run(
            ["node", "--input-type=module", "--eval", eval_script],
            cwd=work_dir, capture_output=True, text=True,
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

    def test_every_valid_primitive_was_measured(self) -> None:
        for primitive in PRIMITIVES:
            self.assertIn(primitive, self.result, f"{primitive} produced no mesh to measure")
            self.assertNotIn("error", self.result[primitive], self.result[primitive])

    def test_closed_solids_are_watertight_under_true_topology(self) -> None:
        for primitive in sorted(EXPECTED_WELDED_WATERTIGHT):
            with self.subTest(primitive=primitive):
                welded = self.result[primitive]["welded"]
                self.assertEqual(welded["boundary"], 0, f"{primitive}: {welded['boundary']} boundary edges under true topology")
                self.assertEqual(welded["nonManifold"], 0, f"{primitive}: {welded['nonManifold']} non-manifold edges under true topology")

    def test_capsule_is_watertight_even_without_welding(self) -> None:
        # The whole point of buildWatertightCapsule: closed BY CONSTRUCTION, not by
        # a tolerance-dependent weld pass applied afterward.
        for primitive in sorted(EXPECTED_WATERTIGHT_EVEN_RAW):
            with self.subTest(primitive=primitive):
                raw = self.result[primitive]["raw"]
                self.assertEqual(raw["boundary"], 0, f"{primitive}: {raw['boundary']} RAW boundary edges (expected watertight by construction)")
                self.assertEqual(raw["nonManifold"], 0, f"{primitive}: {raw['nonManifold']} RAW non-manifold edges")

    def test_open_by_design_primitives_have_a_real_boundary(self) -> None:
        # These are not closed solids -- a card, an open pipe, an open lathed
        # vessel. A boundary here is correct; asserting it stays NONZERO catches a
        # future accidental cap/weld pass silently "fixing" something that was
        # never broken (and would visually change these primitives' shape).
        for primitive in sorted(EXPECTED_OPEN_BY_DESIGN):
            with self.subTest(primitive=primitive):
                self.assertGreater(
                    self.result[primitive]["welded"]["boundary"], 0,
                    f"{primitive}: expected a real open boundary (it is not a closed solid by design)",
                )

    def test_known_unfixed_defects_are_pinned_not_worse(self) -> None:
        # ground-blade is a CONFIRMED, UNFIXED topology defect (see module
        # docstring) -- reported to team-lead, left as-is by direction (off the
        # humanoid character's component path). Pinned to its exact measured counts
        # so it cannot silently regress further, and so a real fix is forced to
        # touch this test rather than leave it stale.
        for primitive, expected in sorted(KNOWN_DEFECT_WELDED_COUNTS.items()):
            with self.subTest(primitive=primitive):
                welded = self.result[primitive]["welded"]
                self.assertEqual(welded["boundary"], expected["boundary"], f"{primitive}: boundary edge count changed -- update this test together with whatever changed it")
                self.assertEqual(welded["nonManifold"], expected["nonManifold"], f"{primitive}: non-manifold edge count changed -- update this test together with whatever changed it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
