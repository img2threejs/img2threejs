"""Animated-character domain profile (Stage R, v1.5.2's rigging/animation pipeline).

Ported from the hardcoded `RIG_STEPS`/profile branch v1.5.2 added on main into the registry the
CS2 extraction introduced, in the same merge that brought the two together: an animated character
is a character (same setup contributions) plus the rig track appended after the FINAL steps.

Rig docs: docs/pipelines/character-rigging-animation-1.5.2.md.

These rig steps exist as a checklist scope because the 1.5.2 gates were reachable only by hand:
every module under forge/stage5_rig/ was callable, and nothing in the workflow ever told anyone to
call one. A gate that nothing invokes reports a clean verdict forever, which is the exact failure
the whole gate system exists to end.

The ORDER of rigSteps is load-bearing and is not a preference:
  - mesh repair happens BEFORE the freeze, because a mesh may legitimately need fixing;
  - the freeze happens BEFORE any rig work, because after it the geometry is evidence;
  - mesh-parity is verified AFTER binding, because that is the only moment the claim
    "implementation did not touch the mesh" can be falsified.
Moving the freeze later would let a bind quietly rewrite vertices and then freeze the result, and
the manifest would certify the damage instead of catching it.
"""

from __future__ import annotations

from typing import Any, Final

DOMAIN: Final[dict[str, Any]] = {
    "id": "animated-character",
    # An animated character is still a character; the anatomy contract must not be lost. The two
    # steps are declared here rather than imported from character.py: registry modules load
    # independently (importlib by path), and each declaration stays readable on its own.
    "setupSteps": (
        (
            "character-contract-read",
            "Read grimoire/character/reconstruction.md and grimoire/character/likeness_maximization.md completely",
        ),
        (
            "character-landmarks",
            "python3 forge/stage1_intake/extract_landmarks.py {reference} --out anatomy.json --overlay landmarks.png",
        ),
    ),
    "setupAnchorBefore": "local-spec-search",
    "rigSteps": (
        (
            "rig-contract-read",
            "Read grimoire/readiness/animation_contract.md and docs/pipelines/character-rigging-animation-1.5.2.md completely",
        ),
        (
            "glb-rig-reference",
            "python3 forge/stage5_rig/glb_rig_reference.py {reference} --out glb-rig.json "
            "(skeleton, skin joint order, inverse binds and clips read FROM the GLB; skip with a reason only when there is no GLB)",
        ),
        (
            "mesh-repair",
            "Inspect the meshes for breakage and repair them now, before the freeze; "
            "record what was repaired, or skip with a reason when nothing is broken",
        ),
        (
            "mesh-freeze",
            "node runtime/scripts/export_mesh_buffers.mjs --url <preview> --out meshes.json && "
            "python3 forge/stage5_rig/mesh_parity.py freeze meshes.json --out mesh-manifest.json",
        ),
        (
            "rig-payload-validate",
            "python3 forge/stage5_rig/validate_rig_payload.py --payload rig-payload.json "
            "(structural payload integrity ONLY -- never pose stress or likeness; a sculpt spec is NOT "
            "a rig payload, they are different schemas)",
        ),
        (
            "rig-bind",
            "Bind the skeleton: ADD skeleton, skinIndex and skinWeight only. Vertex positions, normals, uvs and indices are frozen evidence",
        ),
        (
            "mesh-parity-verify",
            "node runtime/scripts/export_mesh_buffers.mjs --url <preview> --out meshes-after.json && "
            "python3 forge/stage5_rig/mesh_parity.py verify mesh-manifest.json meshes-after.json",
        ),
        (
            "clip-measure",
            "python3 forge/stage5_rig/clip_features.py sampled-clips.json (measure, classify, name, and decide loop from poseReturn)",
        ),
        (
            "rig-gates",
            "python3 forge/stage5_rig/rig_gates.py rig-gate-payload.json (G1-G10; an unevaluated gate is not a pass)",
        ),
    ),
}
