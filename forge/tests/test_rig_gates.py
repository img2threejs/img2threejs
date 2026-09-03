#!/usr/bin/env python3
"""Tests for the Stage R6 gate runner (forge/stage5_rig/rig_gates.py).

Each test traces to one row of the R6 table in
docs/pipelines/character-rigging-animation-1.5.2.md, or to the honesty rule in
forge/stage5_rig/CONTRACT_1.5.2.md ("Any gate that cannot be evaluated because its input is absent
reports status: unevaluated ... It is never silently a pass").

The tests that matter most here are not the ones that feed a gate a bad number. They are the ones
that feed a gate a GOOD number over thin coverage — `test_g1_fails_a_tiny_delta_sampled_only_three
_times`, `test_g2_fails_on_under_sampled_vertex_coverage`,
`test_g10_fails_a_five_frame_sweep_even_with_zero_holes` — because that is the shape every 1.5.1
failure actually had: a plausible measurement over the frames nobody looked at.

Pure Python 3.10+ stdlib.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stage5_rig"))

import mesh_parity  # noqa: E402

from rig_gates import (  # noqa: E402
    BINDING_EPSILON,
    BIND_RESTORE_TOLERANCE,
    GATE_SPECS,
    MINIMUM_G2_VERTICES,
    MINIMUM_GATE_R1_SAMPLES,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_UNEVALUATED,
    run_gates,
)

H = 1.0

# The joint hierarchy G7 resolves chains from. Shape, not names: root -> single-child chain -> hip
# branch (mirrored leg pair + medial spine) -> shoulder branch (mirrored arm pair + medial neck).
JOINTS = [
    {"id": "root", "parent": None, "localPosition": [0.0, 0.0, 0.0]},
    {"id": "hips", "parent": "root", "localPosition": [0.0, 0.5, 0.0]},
    {"id": "thigh.l", "parent": "hips", "localPosition": [0.1, -0.05, 0.0]},
    {"id": "thigh.r", "parent": "hips", "localPosition": [-0.1, -0.05, 0.0]},
    {"id": "shin.l", "parent": "thigh.l", "localPosition": [0.0, -0.22, 0.0]},
    {"id": "shin.r", "parent": "thigh.r", "localPosition": [0.0, -0.22, 0.0]},
    {"id": "foot.l", "parent": "shin.l", "localPosition": [0.0, -0.23, 0.0]},
    {"id": "foot.r", "parent": "shin.r", "localPosition": [0.0, -0.23, 0.0]},
    {"id": "spine", "parent": "hips", "localPosition": [0.0, 0.15, 0.0]},
    {"id": "chest", "parent": "spine", "localPosition": [0.0, 0.15, 0.0]},
    {"id": "clavicle.l", "parent": "chest", "localPosition": [0.08, 0.05, 0.0]},
    {"id": "clavicle.r", "parent": "chest", "localPosition": [-0.08, 0.05, 0.0]},
    {"id": "upperarm.l", "parent": "clavicle.l", "localPosition": [0.06, 0.0, 0.0]},
    {"id": "upperarm.r", "parent": "clavicle.r", "localPosition": [-0.06, 0.0, 0.0]},
    {"id": "hand.l", "parent": "upperarm.l", "localPosition": [0.18, -0.12, 0.0]},
    {"id": "hand.r", "parent": "upperarm.r", "localPosition": [-0.18, -0.12, 0.0]},
    {"id": "neck", "parent": "chest", "localPosition": [0.0, 0.06, 0.0]},
    {"id": "head", "parent": "neck", "localPosition": [0.0, 0.12, 0.0]},
]

# The side-label claim comes from OUTSIDE the geometry (the source rig's own naming). Without it
# `resolve_chains` assigns left = +X and G7 becomes a tautology — see ambiguity 8 in rig_gates.
SIDE_LABELS = {"thigh.l": "l", "thigh.r": "r", "clavicle.l": "l", "clavicle.r": "r"}
MIRRORED_LABELS = {"thigh.l": "r", "thigh.r": "l", "clavicle.l": "r", "clavicle.r": "l"}


def _walk_clip(skating: bool = False, scale_delta: float = 0.0) -> dict:
    """A one-cycle walk sampled at six times, with both feet planted through their stance.

    `skating=True` moves foot.l 0.05H mid-stance — five times FOOT_SLIDE_LIMIT, and invisible to
    anyone reading the track values.
    """
    times = [0.0, 0.24, 0.48, 0.72, 0.96, 1.2]
    hip_z = [0.0, 0.096, 0.192, 0.288, 0.384, 0.48]
    left_planted_z = 0.05 if skating else 0.0
    return {
        "sourceName": "walk-forward",
        "duration": 1.2,
        "sampleTimes": list(times),
        "landmarkPositions": {
            "hip": [[0.0, 0.5, z] for z in hip_z],
            "head": [[0.0, 0.95, z] for z in hip_z],
            "hand.l": [[0.16, 0.72, z] for z in hip_z],
            "hand.r": [[-0.16, 0.72, z] for z in hip_z],
            "foot.l": [
                [0.1, 0.0, 0.0],
                [0.1, 0.0, left_planted_z],
                [0.1, 0.0, 0.0],
                [0.1, 0.05, 0.24],
                [0.1, 0.02, 0.40],
                [0.1, 0.0, 0.48],
            ],
            "foot.r": [
                [-0.1, 0.0, -0.24],
                [-0.1, 0.05, 0.0],
                [-0.1, 0.02, 0.12],
                [-0.1, 0.0, 0.24],
                [-0.1, 0.0, 0.24],
                [-0.1, 0.0, 0.24],
            ],
        },
        "jointScaleDelta": [scale_delta] * len(times),
        "poseReturn": 0.1,
        "stance": {"foot.l": [[0.0, 0.48]], "foot.r": [[0.72, 1.2]]},
    }


def _idle_clip(scale_delta: float = 0.0) -> dict:
    times = [0.0, 0.5, 1.0]
    return {
        "sourceName": "idle-still",
        "duration": 1.0,
        "sampleTimes": list(times),
        "landmarkPositions": {
            "hip": [[0.0, 0.5, 0.0]] * 3,
            "head": [[0.0, 0.95, 0.0]] * 3,
            "hand.l": [[0.16, 0.72, 0.0]] * 3,
            "hand.r": [[-0.16, 0.72, 0.0]] * 3,
            "foot.l": [[0.1, 0.0, 0.0]] * 3,
            "foot.r": [[-0.1, 0.0, 0.0]] * 3,
        },
        "jointScaleDelta": [scale_delta] * len(times),
        "poseReturn": 0.0,
        "stance": {"foot.l": [[0.0, 1.0]], "foot.r": [[0.0, 1.0]]},
    }


def _binding(weight_bias: float = 0.0, bad_index: bool = False) -> dict:
    """Two vertices, four influences each. `weight_bias` breaks normalisation; `bad_index` puts one
    skin index past bones - 1."""
    indices = [0, 1, 2, 3, 1, 2, 3, 0]
    if bad_index:
        indices[3] = 9
    return {
        "positions": [[0.0, 0.0, 0.0], [0.1, 0.0, 0.0]],
        "partIds": ["torso", "arm"],
        "skinIndices": indices,
        "skinWeights": [0.25 + weight_bias, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25],
        "jointCount": 4,
    }


# ---------------------------------------------------------------------------------------------
# G11 / G12 fixtures
#
# The freeze manifest is built by calling `mesh_parity.freeze` on the pre-rig payload rather than
# being hand-written, so the manifest and the "after" payload cannot drift apart in the fixture and
# hand us a green test for the wrong reason.
# ---------------------------------------------------------------------------------------------

_TORSO_POSITION = [0.0, 0.0, 0.0, 0.1, 0.0, 0.0, 0.1, 0.2, 0.0, 0.0, 0.2, 0.0]
_TORSO_NORMAL = [0.0, 0.0, 1.0] * 4
_TORSO_UV = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
_TORSO_INDEX = [0, 1, 2, 0, 2, 3]
_ARM_POSITION = [0.2, 0.5, 0.0, 0.3, 0.5, 0.0, 0.3, 0.6, 0.0]
_ARM_NORMAL = [0.0, 0.0, 1.0] * 3
_ARM_UV = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0]
_ARM_INDEX = [0, 1, 2]


def _mesh(name, position, normal, uv, index, skinning=False):
    attributes = {"position": list(position), "normal": list(normal), "uv": list(uv)}
    if skinning:
        vertices = len(position) // 3
        # The one thing rigging is ALLOWED to add. Outside FROZEN_BUFFERS on purpose.
        attributes["skinIndex"] = [0, 1, 0, 0] * vertices
        attributes["skinWeight"] = [1.0, 0.0, 0.0, 0.0] * vertices
    return {"name": name, "attributes": attributes, "index": list(index)}


def _pre_rig_meshes():
    """The geometry as it stood before rigging: no skinning attributes anywhere."""
    return {
        "meshes": [
            _mesh("torso", _TORSO_POSITION, _TORSO_NORMAL, _TORSO_UV, _TORSO_INDEX),
            _mesh("arm", _ARM_POSITION, _ARM_NORMAL, _ARM_UV, _ARM_INDEX),
        ]
    }


def _post_rig_meshes(nudge=False, skinning=True):
    """The same geometry after rigging. `nudge` moves one torso vertex by 0.001."""
    position = list(_TORSO_POSITION)
    if nudge:
        position[4] = position[4] + 0.001
    return {
        "meshes": [
            _mesh("torso", position, _TORSO_NORMAL, _TORSO_UV, _TORSO_INDEX, skinning=skinning),
            _mesh("arm", _ARM_POSITION, _ARM_NORMAL, _ARM_UV, _ARM_INDEX, skinning=skinning),
        ]
    }


def _freeze_manifest():
    return mesh_parity.freeze(_pre_rig_meshes()).to_dict()


def _glb_report(structural_failure=None, primary_skin=0, unsupported=(), errors=(), ok=None):
    """A `glb_rig_reference` CLI-shaped report: GlbRig.to_dict() plus the CLI's `ok`/`errors`."""
    report = {
        "schemaVersion": 1,
        "kind": "glb-rig-reference",
        "path": "character.glb",
        "nodeCount": 48,
        "skinCount": 1 if primary_skin is not None else 0,
        "clipCount": 2,
        "primarySkinIndex": primary_skin,
        "inverseBindSource": "accessor",
        "unsupportedInterpolationClips": list(unsupported),
        "structuralFailure": structural_failure,
        "warnings": [],
        "errors": list(errors),
    }
    if ok is not None:
        report["ok"] = ok
    return report


def _correspondence(usable=True):
    if usable:
        return {
            "figureHeight": H,
            "tolerance": 0.05,
            "matchedBy": "measured model-space position (never node names)",
            "matches": [
                {"boneId": "hips", "skinJointIndex": 0, "nodeName": "mixamorig:Hips",
                 "distanceH": 0.002, "confidence": 0.96},
                {"boneId": "chest", "skinJointIndex": 1, "nodeName": "mixamorig:Spine2",
                 "distanceH": 0.004, "confidence": 0.92},
            ],
            "unmatchedProceduralBones": [],
            "unmatchedGlbJoints": [],
            "usable": True,
            "reason": "all 2 procedural bones matched all 2 GLB joints within 0.05H",
        }
    return {
        "figureHeight": H,
        "tolerance": 0.05,
        "matchedBy": "measured model-space position (never node names)",
        "matches": [
            {"boneId": "hips", "skinJointIndex": 0, "nodeName": "mixamorig:Hips",
             "distanceH": 0.002, "confidence": 0.96},
        ],
        "unmatchedProceduralBones": ["chest", "clavicle.l"],
        "unmatchedGlbJoints": [{"skinJointIndex": 1, "name": "mixamorig:Spine2"}],
        "usable": False,
        "reason": "2 procedural bones and 1 GLB joint are unmatched within 0.05H",
    }


def full_payload() -> dict:
    """Every measurement inside tolerance and every coverage axis satisfied."""
    return {
        "figureHeight": H,
        "landmarks": ["hip", "head", "hand.l", "hand.r", "foot.l", "foot.r"],
        "clips": [_walk_clip(), _idle_clip()],
        "sourceScalesJoints": False,
        "bindingSamples": {
            "maxSampledBindingDelta": 3.0e-9,
            "clips": {"walk-forward": 5, "idle-still": 6},
        },
        "deformation": {
            "nonFiniteCount": 0,
            "allFinite": True,
            "meshes": {
                "body": {"frames": 4, "verticesPerFrame": 64},
                "hair": {"frames": 4, "verticesPerFrame": [64, 80, 64, 96]},
            },
        },
        "bindRestore": {"maxBindRestoreDelta": 0.0},
        "binding": _binding(),
        "meshVisibility": {
            "visibleMeshCount": 3,
            "visibleSkinnedMeshCount": 3,
            "unboundMeshes": [],
        },
        "chainAnchors": {"joints": JOINTS, "sideLabels": SIDE_LABELS},
        "skinIntegritySweep": {
            "frames": 32,
            "clips": 2,
            "times": 4,
            "sides": 2,
            "azimuths": 2,
            "backgroundThroughSplitPx": 287,
            "backgroundThroughSplitBlobs": 15,
            "creasePx": 36470,
            "baseline": {
                "backgroundThroughSplitPx": 974,
                "backgroundThroughSplitBlobs": 30,
                "creasePx": 31316,
            },
        },
        "meshParity": {"manifest": _freeze_manifest(), "after": _post_rig_meshes()},
        "rigReference": {"source": "glb", "glb": _glb_report()},
    }


class GateReportShapeTest(unittest.TestCase):
    def test_fully_populated_payload_passes_all_twelve(self):
        report = run_gates(full_payload())
        statuses = {result.id: (result.status, result.reason) for result in report.results}
        self.assertEqual(len(report.results), 12)
        for gate_id, (status, reason) in statuses.items():
            self.assertEqual(status, STATUS_PASS, f"{gate_id} is {status}: {reason}")
        self.assertTrue(report.ok)

    def test_summary_is_table_shaped_and_names_what_each_gate_catches(self):
        """The R6 "Catches" column rides on every result so a failing report explains itself."""
        summary = run_gates(full_payload()).summary()
        self.assertEqual([row["id"] for row in summary["rows"]], [s.id for s in GATE_SPECS])
        self.assertEqual(summary["counts"], {"total": 12, "pass": 12, "fail": 0, "unevaluated": 0})
        for row in summary["rows"]:
            for key in ("id", "gate", "status", "measured", "threshold", "catches", "reason"):
                self.assertIn(key, row)
            self.assertTrue(row["catches"], f"{row['id']} carries no 'catches' text")
        self.assertEqual(summary["rows"][0]["catches"], "clips that play silently")
        self.assertEqual(summary["rows"][6]["catches"], "mirrored rig")
        self.assertEqual(summary["rows"][10]["catches"], "rigging that rewrites mesh geometry")
        self.assertEqual(
            summary["rows"][11]["catches"],
            "clip channels and skinIndex values addressing a different skeleton",
        )


class G1BindingReachesNodeTest(unittest.TestCase):
    def test_g1_fails_when_delta_exceeds_float32_epsilon(self):
        payload = full_payload()
        payload["bindingSamples"]["maxSampledBindingDelta"] = 1.0e-4
        result = run_gates(payload).by_id("G1")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertIn("exceeds float32 epsilon", result.reason)
        self.assertEqual(result.measured["maxSampledBindingDelta"], 1.0e-4)

    def test_g1_fails_a_tiny_delta_sampled_only_three_times(self):
        """THIS IS THE TEST THAT MATTERS.

        A delta of 1e-12 looks like proof and is not. Gate R1 exists because a clip can hold an
        action, report a duration and drive nothing; the harness that samples three times has
        measured three moments of one clip and said nothing about the rest. Under-coverage is a
        FAILURE, not a pass — otherwise the gate is satisfied by exactly the sloppiness it was
        written to catch.
        """
        payload = full_payload()
        payload["bindingSamples"] = {
            "maxSampledBindingDelta": 1.0e-12,
            "clips": {"walk-forward": 3, "idle-still": 3},
        }
        result = run_gates(payload).by_id("G1")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertLess(result.measured["maxSampledBindingDelta"], BINDING_EPSILON)
        self.assertIn(f"fewer than {MINIMUM_GATE_R1_SAMPLES} times", result.reason)
        self.assertIn("under-coverage is a failure", result.reason)
        self.assertEqual(
            result.measured["coverage"]["clipsUnderSampled"],
            {"walk-forward": 3, "idle-still": 3},
        )

    def test_g1_fails_a_tiny_delta_that_skipped_a_clip(self):
        """Same shape as the test above: the clip nobody sampled is the clip that plays silently."""
        payload = full_payload()
        payload["bindingSamples"] = {
            "maxSampledBindingDelta": 1.0e-12,
            "clips": {"walk-forward": 25},
        }
        result = run_gates(payload).by_id("G1")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertIn("skipped 1 of 2 clips", result.reason)
        self.assertEqual(result.measured["coverage"]["clipsMissing"], ["idle-still"])

    def test_g1_roster_is_never_taken_from_the_harness_own_report(self):
        """Without an independent roster "covered every clip" is true by construction."""
        payload = full_payload()
        payload.pop("clips")
        result = run_gates(payload).by_id("G1")
        self.assertEqual(result.status, STATUS_UNEVALUATED)
        self.assertIn("no independent clip roster", result.reason)


class G2DeformationFiniteTest(unittest.TestCase):
    def test_g2_fails_on_non_finite_deformation(self):
        payload = full_payload()
        payload["deformation"]["nonFiniteCount"] = 7
        payload["deformation"]["allFinite"] = False
        result = run_gates(payload).by_id("G2")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertIn("non-finite", result.reason)
        self.assertEqual(result.measured["nonFiniteCount"], 7)

    def test_g2_fails_on_under_sampled_vertex_coverage(self):
        """All-finite over 8 vertices is not all-finite: the vertex sent to infinity is one of the
        19,992 the harness never transformed."""
        payload = full_payload()
        payload["deformation"]["meshes"]["body"] = {"frames": 4, "verticesPerFrame": 8}
        result = run_gates(payload).by_id("G2")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertEqual(result.measured["nonFiniteCount"], 0)
        self.assertIn(f"below {MINIMUM_G2_VERTICES}/mesh/frame", result.reason)
        self.assertIn("body", result.reason)

    def test_g2_checks_every_frame_not_just_the_best_one(self):
        payload = full_payload()
        payload["deformation"]["meshes"]["hair"] = {"frames": 4, "verticesPerFrame": [64, 64, 12, 64]}
        result = run_gates(payload).by_id("G2")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertIn("hair transformed 12 vertices", result.reason)


class G3BindRestoreTest(unittest.TestCase):
    def test_g3_catches_pose_bleed(self):
        """A clip that ends mid-pose leaves joints displaced, and the next clip's tracks only
        overwrite the joints they address."""
        payload = full_payload()
        payload["bindRestore"]["maxBindRestoreDelta"] = 1.0e-6
        result = run_gates(payload).by_id("G3")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertEqual(result.measured, 1.0e-6)
        self.assertEqual(result.threshold, BIND_RESTORE_TOLERANCE)
        self.assertIn("still displaced after stop()", result.reason)


class G4G5DelegationTest(unittest.TestCase):
    def test_g4_delegates_the_weight_sum_check_to_skin_conditioning(self):
        payload = full_payload()
        payload["binding"] = _binding(weight_bias=0.01)  # sums to 1.01
        report = run_gates(payload)
        g4 = report.by_id("G4")
        self.assertEqual(g4.status, STATUS_FAIL)
        self.assertEqual(g4.details["delegatedTo"], "skin_conditioning.validate_binding")
        joined = " ".join(g4.details["delegatedFailures"])
        self.assertIn("|1 - sum(w)|", joined)  # the text is skin_conditioning's, not ours
        self.assertIn("vertex 0", joined)
        # The indices were untouched, so G5 must not be dragged down with it.
        self.assertEqual(report.by_id("G5").status, STATUS_PASS)

    def test_g5_delegates_the_index_range_check_to_skin_conditioning(self):
        payload = full_payload()
        payload["binding"] = _binding(bad_index=True)  # index 9 with jointCount 4
        report = run_gates(payload)
        g5 = report.by_id("G5")
        self.assertEqual(g5.status, STATUS_FAIL)
        self.assertEqual(g5.details["delegatedTo"], "skin_conditioning.validate_binding")
        joined = " ".join(g5.details["delegatedFailures"])
        self.assertIn("skin index 9 outside [0, 3]", joined)  # again, skin_conditioning's words
        self.assertEqual(g5.measured["maxSkinIndex"], 9)
        self.assertEqual(g5.measured["boneCount"], 4)
        self.assertEqual(report.by_id("G4").status, STATUS_PASS)


class G6MeshBindingTest(unittest.TestCase):
    def test_g6_fails_when_a_visible_mesh_is_unbound(self):
        payload = full_payload()
        payload["meshVisibility"] = {
            "visibleMeshCount": 3,
            "visibleSkinnedMeshCount": 2,
            "unboundMeshes": ["shoulder-pad.l"],
        }
        result = run_gates(payload).by_id("G6")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertIn("shoulder-pad.l", result.reason)
        self.assertIn("1 mesh(es) unaccounted for", result.reason)


class G7MedialLateralTest(unittest.TestCase):
    def test_g7_catches_a_mirrored_rig(self):
        """Every left-hand action plays on the right, and nothing about it looks wrong in
        isolation. Three inequalities per pair are the whole detector."""
        payload = full_payload()
        payload["chainAnchors"] = {"joints": JOINTS, "sideLabels": MIRRORED_LABELS}
        result = run_gates(payload).by_id("G7")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertEqual(result.details["delegatedTo"], "action_design.check_medial_lateral")
        self.assertIn("MEDIAL_LATERAL", result.details["delegatedFailures"][0])
        self.assertIn("the rig is mirrored", result.details["delegatedFailures"][0])

    def test_g7_is_unevaluated_not_pass_without_an_independent_side_claim(self):
        """Ambiguity 8. With left = +X assigned from the same geometry the gate compares against,
        an errorless run proves nothing — so it is not reported as a pass."""
        payload = full_payload()
        payload["chainAnchors"] = {"joints": JOINTS}  # no sideLabels
        report = run_gates(payload)
        result = report.by_id("G7")
        self.assertEqual(result.status, STATUS_UNEVALUATED)
        self.assertIn("could not have been caught", result.reason)
        self.assertFalse(report.ok)


class G8FootContactTest(unittest.TestCase):
    def test_g8_passes_a_gait_with_the_contact_constraint_enforced(self):
        result = run_gates(full_payload()).by_id("G8")
        self.assertEqual(result.status, STATUS_PASS)
        self.assertEqual(result.measured["maxFootSlideFraction"], 0.0)
        self.assertEqual(result.measured["clipsMeasured"], 2)
        self.assertEqual(result.details["delegatedTo"], "action_design.foot_slide")

    def test_g8_catches_a_skating_gait(self):
        """Reads as "floaty" to every viewer and is hard to name by eye; 0.05H is five times the
        limit and completely invisible in the track values."""
        payload = full_payload()
        payload["clips"] = [_walk_clip(skating=True), _idle_clip()]
        result = run_gates(payload).by_id("G8")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertAlmostEqual(result.measured["maxFootSlideFraction"], 0.05, places=9)
        self.assertEqual(result.measured["worstClip"], "walk-forward")
        self.assertIn("walk-forward", result.reason)
        self.assertIn("foot.l", result.reason)


class G9JointScaleTest(unittest.TestCase):
    def test_g9_fails_on_scale_delta_above_zero(self):
        payload = full_payload()
        payload["clips"] = [_walk_clip(scale_delta=0.004), _idle_clip()]
        result = run_gates(payload).by_id("G9")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertAlmostEqual(result.measured["maxScaleDelta"], 0.004, places=9)
        self.assertEqual(result.measured["clipsScaling"], ["walk-forward"])
        self.assertIn("Nothing declares sourceScalesJoints", result.reason)

    def test_g9_reports_pass_with_warning_when_the_source_declares_joint_scale(self):
        """"unless the source has it" is an admission, never a silent pass: R2's proximity blend
        averages bindings across parts and a scaled joint invalidates that averaging."""
        payload = full_payload()
        payload["clips"] = [_walk_clip(scale_delta=0.004), _idle_clip()]
        payload["sourceScalesJoints"] = True
        result = run_gates(payload).by_id("G9")
        self.assertEqual(result.status, STATUS_PASS)
        self.assertTrue(result.warnings, "a declared joint scale must not pass silently")
        self.assertIn("walk-forward", result.warnings[0])
        self.assertIn("Stage R2", result.warnings[0])
        self.assertIn("pass with warning", result.reason)


class G10SkinIntegritySweepTest(unittest.TestCase):
    def test_g10_fails_a_five_frame_sweep_even_with_zero_holes(self):
        """"Five poses is not coverage." Coincidence-welding looked correct until a 132-frame
        sweep found cracks in 28 frames; a clean number over 5 frames is not evidence."""
        payload = full_payload()
        payload["skinIntegritySweep"] = {
            "frames": 5,
            "clips": 5,
            "times": 1,
            "sides": 1,
            "azimuths": 1,
            "backgroundThroughSplitPx": 0,
            "backgroundThroughSplitBlobs": 0,
            "creasePx": 120,
            "baseline": {"backgroundThroughSplitPx": 974, "creasePx": 31316},
        }
        result = run_gates(payload).by_id("G10")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertEqual(result.measured["backgroundThroughSplitPx"], 0.0)
        self.assertIn("five poses is not coverage", result.reason)
        self.assertIn("materially thinner", result.reason)

    def test_g10_fails_a_sweep_thinner_than_its_declared_clip_set_allows(self):
        payload = full_payload()
        payload["skinIntegritySweep"].update(
            {"frames": 16, "clips": 1, "times": 4, "sides": 2, "azimuths": 2}
        )
        result = run_gates(payload).by_id("G10")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertIn("covered 1 of 2 clip(s)", result.reason)

    def test_g10_fails_a_declared_frame_count_its_axes_do_not_support(self):
        payload = full_payload()
        payload["skinIntegritySweep"]["frames"] = 176  # axes still multiply to 32
        result = run_gates(payload).by_id("G10")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertIn("axes multiply to 32", result.reason)

    def test_g10_reports_holes_and_creases_as_two_separate_numbers(self):
        """R2 trades one against the other on purpose; a single combined score hides the trade."""
        result = run_gates(full_payload()).by_id("G10")
        self.assertEqual(result.status, STATUS_PASS)
        self.assertEqual(result.measured["backgroundThroughSplitPx"], 287.0)
        self.assertEqual(result.measured["creasePx"], 36470.0)
        self.assertEqual(result.measured["baselineBackgroundThroughSplitPx"], 974.0)
        self.assertEqual(result.measured["baselineCreasePx"], 31316.0)
        self.assertNotIn("score", result.measured)
        # Creases rose ~16% and that is REPORTED, not gated — gating it would ask a later stage to
        # disable the blend, which is the failure R2 warns about.
        self.assertTrue(any("crease" in w for w in result.warnings))

    def test_g10_is_unevaluated_without_a_measured_baseline(self):
        payload = full_payload()
        payload["skinIntegritySweep"].pop("baseline")
        result = run_gates(payload).by_id("G10")
        self.assertEqual(result.status, STATUS_UNEVALUATED)
        self.assertIn("baseline", result.reason)

    def test_g10_is_unevaluated_without_a_per_axis_breakdown(self):
        payload = full_payload()
        for axis in ("clips", "times", "sides", "azimuths"):
            payload["skinIntegritySweep"].pop(axis)
        result = run_gates(payload).by_id("G10")
        self.assertEqual(result.status, STATUS_UNEVALUATED)
        self.assertIn("per-axis breakdown", result.reason)


class G11MeshParityTest(unittest.TestCase):
    def test_g11_fails_on_a_nudged_vertex_and_names_mesh_attribute_and_element(self):
        """"The hash differs" hands a human two megabytes of numbers and a diff tool. The gate has
        to carry through which mesh, which attribute, and which element — "1 of 12 elements differ"
        is a completely different bug report from "all 12 differ"."""
        payload = full_payload()
        payload["meshParity"] = {
            "manifest": _freeze_manifest(),
            "after": _post_rig_meshes(nudge=True),
        }
        result = run_gates(payload).by_id("G11")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertEqual(result.details["delegatedTo"], "mesh_parity.verify")
        self.assertEqual(result.measured["failureCount"], 1)
        failure = result.measured["failures"][0]
        self.assertEqual(failure["mesh"], "torso")
        self.assertEqual(failure["attribute"], "position")
        self.assertEqual(failure["kind"], mesh_parity.KIND_HASH)
        self.assertEqual(failure["differingElements"], 1)
        self.assertEqual(failure["differences"][0]["index"], 4)
        self.assertAlmostEqual(failure["differences"][0]["frozen"], 0.0, places=9)
        self.assertAlmostEqual(failure["differences"][0]["current"], 0.001, places=9)
        # The headline reason must stand on its own without opening the payload.
        self.assertIn("torso.position", result.reason)
        self.assertIn("element 4", result.reason)

    def test_g11_passes_when_only_skin_index_and_skin_weight_were_added(self):
        """THE TEST THAT KEEPS THE GATE SWITCHED ON.

        skinIndex and skinWeight sit outside FROZEN_BUFFERS deliberately — adding them is the entire
        legal purpose of the implementation phase. A gate that failed every successful rig would be
        switched off within a week, which is strictly worse than not having it at all.
        """
        payload = full_payload()
        payload["meshParity"] = {
            "manifest": _freeze_manifest(),
            "after": _post_rig_meshes(skinning=True),
        }
        result = run_gates(payload).by_id("G11")
        self.assertEqual(result.status, STATUS_PASS, result.reason)
        self.assertEqual(result.measured["failureCount"], 0)
        self.assertEqual(result.measured["meshesCompared"], 2)
        # The additions are reported so they are visible, and never gated.
        self.assertEqual(
            sorted(result.measured["addedLegal"]),
            ["arm.skinIndex", "arm.skinWeight", "torso.skinIndex", "torso.skinWeight"],
        )
        self.assertTrue(any("legal" in warning for warning in result.warnings))

    def test_g11_passes_a_rig_that_added_nothing_at_all(self):
        payload = full_payload()
        payload["meshParity"] = {
            "manifest": _freeze_manifest(),
            "after": _post_rig_meshes(skinning=False),
        }
        result = run_gates(payload).by_id("G11")
        self.assertEqual(result.status, STATUS_PASS)
        self.assertEqual(result.measured["addedLegalCount"], 0)

    def test_g11_is_unevaluated_when_the_manifest_half_is_absent(self):
        payload = full_payload()
        payload["meshParity"] = {"after": _post_rig_meshes()}
        result = run_gates(payload).by_id("G11")
        self.assertEqual(result.status, STATUS_UNEVALUATED)
        self.assertIn("`manifest`", result.reason)
        self.assertNotIn("`after`", result.reason)
        self.assertIn("unproven, which is not the same as unbroken", result.reason)

    def test_g11_is_unevaluated_when_the_after_half_is_absent(self):
        payload = full_payload()
        payload["meshParity"] = {"manifest": _freeze_manifest()}
        result = run_gates(payload).by_id("G11")
        self.assertEqual(result.status, STATUS_UNEVALUATED)
        self.assertIn("`after`", result.reason)
        self.assertNotIn("`manifest` (the freeze record", result.reason)

    def test_g11_defers_the_mesh_set_decision_to_mesh_parity(self):
        """Ambiguity 10: a mesh removed after the freeze is mesh_parity's call, not ours."""
        payload = full_payload()
        after = _post_rig_meshes()
        after["meshes"] = [mesh for mesh in after["meshes"] if mesh["name"] != "arm"]
        payload["meshParity"] = {"manifest": _freeze_manifest(), "after": after}
        result = run_gates(payload).by_id("G11")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertEqual(result.measured["failures"][0]["kind"], mesh_parity.KIND_MESH_MISSING)
        self.assertEqual(result.measured["failures"][0]["mesh"], "arm")


class G12RigReferenceTest(unittest.TestCase):
    def test_g12_passes_a_rig_that_came_straight_from_the_glb(self):
        result = run_gates(full_payload()).by_id("G12")
        self.assertEqual(result.status, STATUS_PASS, result.reason)
        self.assertEqual(result.measured["primarySkinIndex"], 0)
        self.assertTrue(result.measured["glbOk"])
        self.assertIn("the file's own index spaces", result.reason)

    def test_g12_passes_a_procedural_skeleton_with_a_usable_correspondence(self):
        payload = full_payload()
        payload["rigReference"] = {
            "source": "procedural",
            "glb": _glb_report(),
            "correspondence": _correspondence(usable=True),
        }
        result = run_gates(payload).by_id("G12")
        self.assertEqual(result.status, STATUS_PASS, result.reason)
        self.assertIs(result.measured["correspondenceUsable"], True)

    def test_g12_fails_when_the_correspondence_is_not_usable_and_surfaces_the_reason(self):
        """`usable` is False whenever ANY joint on EITHER side is unmatched. A partial map
        retargets the bones it knows and leaves the rest at bind pose — the disjointed mesh."""
        payload = full_payload()
        payload["rigReference"] = {
            "source": "procedural",
            "glb": _glb_report(),
            "correspondence": _correspondence(usable=False),
        }
        result = run_gates(payload).by_id("G12")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertIn("2 procedural bones and 1 GLB joint are unmatched", result.reason)
        self.assertIn("chest", result.reason)
        self.assertIn("mixamorig:Spine2", result.reason)
        self.assertEqual(result.measured["unmatchedProceduralBones"], ["chest", "clavicle.l"])
        self.assertIn("the mapping was invented", result.reason)

    def test_g12_fails_not_unevaluated_on_a_structural_failure(self):
        """A structural failure is a known-bad answer, not a missing one."""
        payload = full_payload()
        payload["rigReference"] = {
            "source": "glb",
            "glb": _glb_report(
                structural_failure="GLB carries 3 skins and no primary skin was chosen; each skin "
                "owns its own joint index space",
                primary_skin=None,
            ),
        }
        result = run_gates(payload).by_id("G12")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertNotEqual(result.status, STATUS_UNEVALUATED)
        self.assertIn("carries 3 skins", result.reason)
        self.assertIn("no usable skeleton of record", result.reason)

    def test_g12_fails_on_unsupported_interpolation(self):
        """Ambiguity 13: glb_rig_reference keeps this separate from structuralFailure; the gate
        fails on either and names which one fired."""
        payload = full_payload()
        payload["rigReference"] = {
            "source": "glb",
            "glb": _glb_report(unsupported=["dash-forward"]),
        }
        result = run_gates(payload).by_id("G12")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertIn("dash-forward", result.reason)
        self.assertIn("interpolation", result.reason)
        self.assertEqual(result.measured["unsupportedInterpolationClips"], ["dash-forward"])

    def test_g12_fails_a_procedural_source_carrying_no_correspondence(self):
        """Ambiguity 14: the defect stated in the payload, not absent input."""
        payload = full_payload()
        payload["rigReference"] = {"source": "procedural", "glb": _glb_report()}
        result = run_gates(payload).by_id("G12")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertIn("needs an explicit mapping", result.reason)

    def test_g12_honours_an_explicit_ok_false_and_derives_it_when_absent(self):
        """Ambiguity 12: `ok` comes from the CLI, not GlbRig.to_dict(), so it is derived when the
        caller worked in-process."""
        derived = run_gates(full_payload()).by_id("G12")
        self.assertFalse(derived.measured["okWasDeclared"])
        self.assertTrue(derived.measured["glbOk"])

        payload = full_payload()
        payload["rigReference"] = {
            "source": "glb",
            "glb": _glb_report(ok=False, errors=["--sample-clips needs --landmarks"]),
        }
        result = run_gates(payload).by_id("G12")
        self.assertEqual(result.status, STATUS_FAIL)
        self.assertTrue(result.measured["okWasDeclared"])
        self.assertIn("not ok", result.reason)

    def test_g12_is_unevaluated_when_the_block_carries_no_glb_report(self):
        payload = full_payload()
        payload["rigReference"] = {"source": "glb"}
        result = run_gates(payload).by_id("G12")
        self.assertEqual(result.status, STATUS_UNEVALUATED)
        self.assertIn("no skeleton of record", result.reason)


class HonestyRuleTest(unittest.TestCase):
    """The honesty rule, from CONTRACT_1.5.2.md.

    "Any gate that cannot be evaluated because its input is absent reports `status: unevaluated`
    with a reason. It is never silently a pass."
    """

    INPUT_KEYS = {
        "G1": "bindingSamples",
        "G2": "deformation",
        "G3": "bindRestore",
        "G4": "binding",
        "G5": "binding",
        "G6": "meshVisibility",
        "G7": "chainAnchors",
        "G8": "clips",
        "G9": "clips",
        "G10": "skinIntegritySweep",
        "G11": "meshParity",
        "G12": "rigReference",
    }

    def test_every_gate_is_unevaluated_not_pass_when_its_input_is_absent(self):
        self.assertEqual(sorted(self.INPUT_KEYS), sorted(spec.id for spec in GATE_SPECS))
        for spec in GATE_SPECS:
            with self.subTest(gate=spec.id):
                payload = copy.deepcopy(full_payload())
                payload.pop(self.INPUT_KEYS[spec.id])
                report = run_gates(payload)
                result = report.by_id(spec.id)
                self.assertEqual(
                    result.status,
                    STATUS_UNEVALUATED,
                    f"{spec.id} reported {result.status} with {self.INPUT_KEYS[spec.id]!r} absent",
                )
                self.assertTrue(result.reason, f"{spec.id} gave no reason")
                self.assertIsNone(result.measured)
                self.assertFalse(
                    report.ok, f"a report containing an unevaluated {spec.id} must not be ok"
                )

    def test_an_unevaluated_gate_makes_the_whole_report_not_ok(self):
        payload = full_payload()
        payload.pop("bindRestore")
        report = run_gates(payload)
        self.assertFalse(report.ok)
        self.assertEqual([r.id for r in report.unevaluated], ["G3"])
        self.assertEqual(report.failed, ())
        self.assertEqual(report.summary()["counts"]["pass"], 11)

    def test_an_empty_payload_evaluates_nothing_and_is_not_ok(self):
        report = run_gates({})
        self.assertEqual(len(report.unevaluated), 12)
        self.assertFalse(report.ok)


class CliTest(unittest.TestCase):
    SCRIPT = ROOT / "stage5_rig" / "rig_gates.py"

    def _run(self, payload: dict) -> tuple[int, dict]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payload.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(self.SCRIPT), str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
        return proc.returncode, json.loads(proc.stdout)

    def test_cli_prints_the_report_and_exits_zero_when_every_gate_passes(self):
        code, report = self._run(full_payload())
        self.assertEqual(code, 0, report)
        self.assertTrue(report["ok"])
        # Derived from GATE_SPECS, not hardcoded: adding a gate must not need a test edit here.
        self.assertEqual(len(report["gates"]), len(GATE_SPECS))

    def test_cli_exits_one_on_failure(self):
        payload = full_payload()
        payload["bindRestore"]["maxBindRestoreDelta"] = 1.0
        code, report = self._run(payload)
        self.assertEqual(code, 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["summary"]["failed"], ["G3"])


if __name__ == "__main__":
    unittest.main()
