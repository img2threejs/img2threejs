#!/usr/bin/env python3
"""Milestone 0 rig kill test.

Question this answers: can a generator EMIT a working skinned rig from a
RigSpec, or not? See docs/PLAN_1.5_ANIMATION_READY_RIGS.md.

This is a REAL-RUN test only. It emits TypeScript from a hand-written RigSpec
(forge/stage5_rig/emit_rig.py), executes that emitted code with the
showcase's real `three` via a Node subprocess
(img2threejs-showcase/scripts/rig-milestone0.mjs), and gates on numbers read
back from the executed geometry. No mock, no hand-computed expectation. If
the subprocess cannot run or does not produce a parseable result, this test
fails closed and loudly (a missing showcase checkout, a missing `node`, or an
unexecutable emit are all treated as a Milestone 0 FAIL, not a skip).

Reproduce standalone:
    node <showcase>/scripts/rig-milestone0.mjs <emitted.ts> --out result.json

Pure Python 3.10+ stdlib on this side. No pip installs.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

FORGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FORGE_ROOT))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from showcase_test_support import showcase_root  # noqa: E402
from stage5_rig.emit_rig import emit_typescript  # noqa: E402
from stage5_rig.rig_spec import BoneSpec, RigSpec  # noqa: E402


def resolve_gate() -> tuple[str, Path, Path]:
    """Locate `node` and the showcase gate script, or skip.

    The showcase checkout owns `three` and the reference rig this milestone was benchmarked
    against; it is deliberately not vendored into forge, and this test only reads from it. Its
    location comes from IMG2THREEJS_SHOWCASE_ROOT like every other showcase-backed test — an
    absolute path here passes on one machine and errors out on every other one, CI included.
    `showcase_root()` still fails closed rather than skipping when IMG2THREEJS_REQUIRE_SHOWCASE=1.
    """
    root = showcase_root()
    gate = root / "scripts" / "rig-milestone0.mjs"
    if not gate.exists():
        raise unittest.SkipTest(f"showcase checkout has no rig gate script at {gate}")
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("executing the emitted rig needs `node` on PATH")
    return node, gate, root

# --- Gate thresholds (Milestone 0 brief) --------------------------------
# (a) calibration reference: the hand-authored dragon achieves 2.98e-8.
MAX_WEIGHT_ERROR_THRESHOLD = 1e-6


def build_three_bone_arm_spec() -> RigSpec:
    """shoulder -> elbow -> wrist, skinning a single capsule.

    Schema ambiguity readings taken here (see rig_spec.py docstrings for the
    general rule; this is the concrete instance):

    - shoulder.tipPos and elbow.tipPos are both OMITTED and default to their
      single child's jointPos (shoulder -> elbow's jointPos, elbow -> wrist's
      jointPos), which is the unambiguous, plan-stated case.
    - wrist is a leaf (no children). The plan's default ("child's jointPos")
      has nothing to default from, so wrist.tipPos is authored explicitly
      here — a 0.4-unit "hand" extension past the wrist joint. This is the
      spec author's job, not the emitter's (Pillar 2): the emitter REJECTS
      any leaf skinned bone missing an explicit tipPos rather than inventing
      one.
    """
    return RigSpec(
        version="1.0",
        bind_pose="T",
        forward="+Z",
        bones=[
            BoneSpec(
                id="shoulder",
                parent=None,
                joint_pos=(0.0, 0.0, 0.0),
                component="arm",
                role="skinned",
                chain="arm",
            ),
            BoneSpec(
                id="elbow",
                parent="shoulder",
                joint_pos=(0.0, -1.0, 0.0),
                component="arm",
                role="skinned",
                chain="arm",
            ),
            BoneSpec(
                id="wrist",
                parent="elbow",
                joint_pos=(0.0, -2.0, 0.0),
                tip_pos=(0.0, -2.4, 0.0),
                component="arm",
                role="skinned",
                chain="arm",
            ),
        ],
    )


# Component bounding-box extents perpendicular to the bone axis (PLAN_1.5
# §4.3 inputs W/D). READING CHOSEN: RigSpec.bones[].component normally
# indexes into ObjectSculptSpec.components, which does not exist in this
# standalone milestone (there is no full sculpt spec, only a hand-written
# RigSpec plus one hand-specified capsule mesh). Rather than inventing a
# competing schema field, this test supplies the same (width, depth)
# information ObjectSculptSpec.components would have carried, as a plain
# side-channel dict keyed by component id. All three bones share the one
# capsule component, so all three get the same derived R_b.
COMPONENT_EXTENTS = {
    "arm": (0.6, 0.6),  # capsule diameter 0.6 (radius 0.3) in both perpendicular axes
}
CAPSULE_RADIUS = 0.3


class RigMilestone0(unittest.TestCase):
    result: dict

    @classmethod
    def setUpClass(cls) -> None:
        node, gate_script, showcase = resolve_gate()

        spec = build_three_bone_arm_spec()
        source = emit_typescript(spec, COMPONENT_EXTENTS, CAPSULE_RADIUS)

        cls._tempdir = tempfile.mkdtemp(prefix="rig-milestone0-")
        entry_path = Path(cls._tempdir) / "rig-milestone0-arm.ts"
        entry_path.write_text(source, encoding="utf-8")
        out_path = Path(cls._tempdir) / "result.json"
        cls.entry_path = entry_path
        cls.out_path = out_path

        proc = subprocess.run(
            [node, str(gate_script), str(entry_path), "--out", str(out_path)],
            cwd=str(showcase),
            capture_output=True,
            text=True,
            timeout=120,
        )
        cls.proc = proc
        if proc.returncode != 0:
            raise RuntimeError(
                "FAIL CLOSED: rig-milestone0.mjs exited non-zero "
                f"(code {proc.returncode}).\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
            )
        if not out_path.exists():
            raise RuntimeError(
                "FAIL CLOSED: rig-milestone0.mjs exited 0 but wrote no result file.\n"
                f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
            )
        try:
            cls.result = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"FAIL CLOSED: result file was not parseable JSON: {exc}\n"
                f"raw contents: {out_path.read_text(encoding='utf-8')!r}"
            ) from exc

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tempdir, ignore_errors=True)

    def test_gate_a_weight_normalization(self) -> None:
        measured = self.result["maxWeightError"]
        print(f"\n[gate a] maxWeightError = {measured!r} (threshold < {MAX_WEIGHT_ERROR_THRESHOLD})")
        self.assertLess(
            measured,
            MAX_WEIGHT_ERROR_THRESHOLD,
            f"gate (a) FAIL: max skin-weight error {measured} >= {MAX_WEIGHT_ERROR_THRESHOLD}",
        )

    def test_gate_b_deformation_delta(self) -> None:
        """Predicate per team-lead decision (2026-07-30), replacing the
        original ">0 for every influenced vertex":

        A point exactly ON the elbow's rotation axis is fixed by the
        rotation, and also fixed by every other (unrotated) bone's identity
        transform, so a linear blend of transforms that each fix the point
        also fixes it — displacement exactly 0 there is correct skinning,
        not a defect. Vertices within AXIS_EPSILON of the rotation axis are
        therefore exempt from the ">0" check, but:

          - the exempt count is measured and reported (axisExemptVertexCount)
            rather than silently absorbed;
          - if the exemption swallows every influenced vertex, that IS a
            FAIL (it would mean nothing off-axis was ever influenced);
          - the structural precondition (>=1 influenced vertex at all) is
            still enforced first, upstream, by rig-milestone0.mjs itself
            (see setUpClass — a rig with zero influenced vertices exits
            non-zero before this test ever runs).

        The rank correlation between displacement and axis distance is
        logged for visibility but NOT gated — no calibrated threshold for
        "strongly positive" exists yet (same rule the colour-signal gates
        use).
        """
        influenced = self.result["influencedVertexCount"]
        exempt = self.result["axisExemptVertexCount"]
        epsilon = self.result["axisEpsilon"]
        min_delta = self.result["minInfluencedDeformationDelta"]
        zero_count = self.result["zeroDeltaVertexCount"]
        correlation = self.result["displacementVsAxisDistanceSpearman"]
        print(
            f"\n[gate b] influencedVertexCount={influenced}, axisExemptVertexCount={exempt} "
            f"(epsilon={epsilon}), minInfluencedDeformationDelta={min_delta!r}, "
            f"zeroDeltaVertexCount={zero_count}, "
            f"displacementVsAxisDistanceSpearman={correlation!r} (report-only, not gated)"
        )
        self.assertGreater(influenced, 0, "gate (b) FAIL: elbow has no influenced vertices to sweep")
        self.assertLess(
            exempt,
            influenced,
            f"gate (b) FAIL: all {influenced} influenced vertices are axis-exempt "
            "(within epsilon of the rotation axis) — nothing off-axis was ever "
            "influenced, so this pose-sweep tested nothing",
        )
        self.assertIsNotNone(
            min_delta,
            "gate (b) FAIL: no non-exempt influenced vertex to measure a deformation delta from",
        )
        self.assertGreater(
            min_delta,
            0.0,
            f"gate (b) FAIL: {zero_count} non-axis-exempt influenced vertex/vertices did not move "
            "when the elbow rotated 0deg->90deg (bone rotates but mesh stays still)",
        )

    def test_gate_c_envelope_containment(self) -> None:
        violations = self.result["envelopeViolations"]
        checked = self.result["envelopeChecked"]
        fallback = self.result["fallbackVertexCount"]
        print(
            f"\n[gate c] envelopeViolations={violations}/{checked} checked "
            f"(fallbackVertexCount={fallback} excluded from this check)"
        )
        self.assertEqual(
            violations,
            0,
            f"gate (c) FAIL: {violations} (vertex, bone) pairs exceeded the derived "
            "envelope radius R_b",
        )

    def test_gate_d_boundary_edges(self) -> None:
        boundary = self.result["boundaryEdges"]
        non_manifold = self.result["nonManifoldEdges"]
        print(f"\n[gate d] boundaryEdges={boundary}, nonManifoldEdges={non_manifold}")
        self.assertEqual(boundary, 0, f"gate (d) FAIL: {boundary} boundary edges after bind")
        self.assertEqual(non_manifold, 0, f"gate (d) FAIL: {non_manifold} non-manifold edges after bind")


class RigMilestone0TemplateOrdering(unittest.TestCase):
    """Static text checks on emit_typescript()'s output -- no node/showcase needed.

    `mesh.add(root)` puts the bones' local-space rest transforms into the scene graph, but they
    stay at their local values until something recomputes world matrices. `new THREE.Skeleton(...)`
    calls `calculateInverses()` internally, which reads each bone's CURRENT world matrix at
    construction time. Skeleton-then-updateMatrixWorld (or no updateMatrixWorld at all) captures
    identity matrices into the inverse binds; those inverses then fail to cancel the rest pose
    during skinning, so every vertex is displaced by its bone's offset at rest. This compiles,
    binds, reports `bound: true`, and renders a corpse -- see
    test_rig_hierarchy_emission.py::test_bone_rotation_deforms_its_own_mesh_and_not_a_distant_one
    for the equivalent failure mode in the main factory emitter, which generate_threejs_factory.py
    fixed the same way (`root.updateMatrixWorld(true)` before `new THREE.Skeleton(...)`).
    """

    def test_update_matrix_world_runs_between_mesh_add_and_skeleton_construction(self) -> None:
        spec = build_three_bone_arm_spec()
        source = emit_typescript(spec, COMPONENT_EXTENTS, CAPSULE_RADIUS)

        mesh_add_index = source.index("mesh.add(root);")
        update_matrix_world_index = source.index("mesh.updateMatrixWorld(true);")
        skeleton_index = source.index("new THREE.Skeleton(BONE_IDS.map((id) => bones[id]));")

        self.assertLess(
            mesh_add_index, update_matrix_world_index,
            "mesh.updateMatrixWorld(true) must run AFTER the bones are attached to the mesh "
            "(mesh.add(root)), or it recomputes world matrices for a graph the bones aren't "
            "even part of yet",
        )
        self.assertLess(
            update_matrix_world_index, skeleton_index,
            "mesh.updateMatrixWorld(true) must run BEFORE `new THREE.Skeleton(...)` -- "
            "calculateInverses() reads each bone's CURRENT world matrix at construction time, "
            "so a Skeleton built first captures identity matrices and the rig renders a corpse",
        )


class AxisExemptionSelfCheck(unittest.TestCase):
    """Closes a coverage gap team-lead identified (2026-07-30): the derived
    3-bone-arm capsule always measures axisExemptVertexCount == 0, so the
    exemption branch in gate (b) has never actually fired on that path. That
    proves the counter reads zero when nothing qualifies; it does not prove
    an on-axis vertex is correctly classified when one exists.

    `node scripts/rig-milestone0.mjs --self-check` runs four hand-placed
    synthetic cases (on the rotation axis; on the axis but offset along it;
    5e-7 from it; 2e-6 from it) through the SAME perpendicularDistanceFromAxis()
    gate (b) uses (see scripts/rig-milestone0-axis.mjs) — not a
    reimplementation, so a broken real predicate cannot pass by comparing
    against a second broken copy of itself. This test asserts the
    classifications from that JSON, plus an analytic cross-check of the
    off-axis displacements against the closed-form 90-degree chord length
    (distance * sqrt(2)) rather than against a recorded constant, so a wrong
    implementation cannot pass by matching a number this test previously
    happened to observe.

    Deliberately does NOT touch RING_RESOLUTION_DIVISOR, N, or any capsule
    geometry -- per team-lead's instruction, this is a synthetic case built
    for exactly this purpose, not a perturbation of the derived tessellation.

    NOTE (2026-07-30): the 'on-axis-offset-along-axis' case and the
    analytic cross-check were folded in from a standalone
    scripts/rig-milestone0-axis-exemption.test.mjs, which team-lead found
    was never wired into any npm script or CI path (package.json's
    test:review-apparatus lists explicit files, not a glob) -- so it always
    passed by hand and never ran otherwise. That file has been deleted;
    this class is now the one canonical, wired path for this coverage.
    """

    result: dict

    @classmethod
    def setUpClass(cls) -> None:
        node, gate_script, showcase = resolve_gate()

        cls._tempdir = tempfile.mkdtemp(prefix="rig-milestone0-selfcheck-")
        out_path = Path(cls._tempdir) / "self-check.json"
        cls.out_path = out_path

        proc = subprocess.run(
            [node, str(gate_script), "--self-check", "--out", str(out_path)],
            cwd=str(showcase),
            capture_output=True,
            text=True,
            timeout=60,
        )
        cls.proc = proc
        if proc.returncode != 0:
            raise RuntimeError(
                "FAIL CLOSED: rig-milestone0.mjs --self-check exited non-zero "
                f"(code {proc.returncode}).\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
            )
        if not out_path.exists():
            raise RuntimeError(
                "FAIL CLOSED: --self-check exited 0 but wrote no result file.\n"
                f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
            )
        try:
            cls.result = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"FAIL CLOSED: self-check result was not parseable JSON: {exc}\n"
                f"raw contents: {out_path.read_text(encoding='utf-8')!r}"
            ) from exc

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tempdir, ignore_errors=True)

    def _case(self, label: str) -> dict:
        matches = [c for c in self.result["cases"] if c["label"] == label]
        self.assertEqual(len(matches), 1, f"expected exactly one '{label}' case, found {len(matches)}")
        return matches[0]

    def test_on_axis_vertex_is_exempt_with_zero_displacement(self) -> None:
        case = self._case("on-axis")
        print(f"\n[self-check] on-axis: {case!r}")
        self.assertLessEqual(case["distanceFromAxis"], self.result["axisEpsilon"])
        self.assertTrue(case["exempt"], "a vertex exactly on the rotation axis must be exempt")
        self.assertEqual(case["displacement"], 0, "a vertex exactly on the rotation axis must not move")

    def test_on_axis_offset_along_axis_is_exempt(self) -> None:
        """The strongest case: a vertex on the axis LINE but 0.5 away from
        the pivot POINT. Distinguishes a correct implementation (measuring
        perpendicular distance from the axis, which stays 0 anywhere along
        it) from the plausible wrong one (measuring distance from the pivot
        point, which would wrongly flag this vertex as off-axis)."""
        case = self._case("on-axis-offset-along-axis")
        print(f"\n[self-check] on-axis-offset-along-axis: {case!r}")
        self.assertLessEqual(
            case["distanceFromAxis"],
            self.result["axisEpsilon"],
            "a vertex 0.5 units along the axis from the pivot is still ON the axis "
            "line and must be exempt -- if this fails, the implementation is measuring "
            "distance from the pivot POINT rather than the axis LINE",
        )
        self.assertTrue(case["exempt"])
        self.assertEqual(case["displacement"], 0, "a vertex on the rotation axis must not move, regardless of where along it")

    def test_vertex_inside_epsilon_is_exempt(self) -> None:
        case = self._case("inside-epsilon")
        print(f"\n[self-check] inside-epsilon (5e-7): {case!r}")
        self.assertLessEqual(case["distanceFromAxis"], self.result["axisEpsilon"])
        self.assertTrue(case["exempt"], "a vertex 5e-7 from the axis (inside epsilon=1e-6) must be exempt")
        self._assert_matches_analytic_chord_length(case)

    def test_vertex_outside_epsilon_is_not_exempt_and_moves(self) -> None:
        case = self._case("outside-epsilon")
        print(f"\n[self-check] outside-epsilon (2e-6): {case!r}")
        self.assertGreater(case["distanceFromAxis"], self.result["axisEpsilon"])
        self.assertFalse(case["exempt"], "a vertex 2e-6 from the axis (outside epsilon=1e-6) must NOT be exempt")
        self.assertGreater(
            case["displacement"],
            0.0,
            "a non-exempt vertex must actually deform under the pose sweep",
        )
        self._assert_matches_analytic_chord_length(case)

    def _assert_matches_analytic_chord_length(self, case: dict) -> None:
        """Cross-check against the closed form for a 90-degree rotation about
        an axis at perpendicular distance d: chord length = d * sqrt(2).
        Deliberately NOT compared against a previously-recorded constant --
        that would only prove the code hasn't changed, not that it was right
        to begin with."""
        expected = case["distanceFromAxis"] * math.sqrt(2)
        self.assertAlmostEqual(
            case["displacement"],
            expected,
            delta=1e-9,
            msg=f"displacement {case['displacement']} should match the analytic 90-degree "
            f"chord length {expected} for distanceFromAxis={case['distanceFromAxis']}",
        )


if __name__ == "__main__":
    unittest.main()
