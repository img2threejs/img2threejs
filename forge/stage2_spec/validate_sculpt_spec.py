#!/usr/bin/env python3
"""Validate an ObjectSculptSpec JSON file for procedural Three.js generation."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stage3_build"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from chirality import CHARACTER_LEFT_SIGN, check_pair, find_pairs  # noqa: E402
from feature_acceptance_policy import feature_gate_failures, feature_review_policy
from hair_profile import REJECTED_HAIR_PRIMITIVES, validate_hair_profile
from material_physics import check_material_physics, check_open_boundary_sides
from sdf_primitives import validate_sdf_descriptor
from vertex_paint import VertexPaintError, normalize_vertex_paint
from subdivision import (
    ATTACHMENT_CYLINDER_SUBDIVISION_SOURCE_FACES,
    MAX_SUBDIVISION_ITERATIONS,
    MAX_SUBDIVISION_QUAD_FACES,
    resolve_instanced_cluster_base,
    SUBDIVISION_SOURCE_FACE_ESTIMATES,
)
from visual_hull import validate_visual_hull_descriptor
from pipeline_routing import resolve_pipeline_routing, validate_pipeline_routing


REQUIRED_TOP_LEVEL = {
    "targetName": str,
    "suitability": str,
    "coordinateFrame": dict,
    "silhouette": dict,
    "componentTree": list,
    "materials": list,
    "proceduralStrategy": list,
}
VALID_SUITABILITY = {"pass", "conditional", "reject"}
VALID_PRIMITIVES = {
    "box",
    "sphere",
    "ellipsoid",
    "cylinder",
    "cone",
    "capsule",
    "torus",
    "tube",
    "lathe",
    "extrude",
    "ground-blade",
    "curve-sweep",
    "tapered-sweep",
    "plane-card",
    "instanced-cluster",
}
VALID_COMPONENT_LEVELS = {"macro", "meso", "micro"}
# Roles that are expected to declare `standProud`. Hair is the only one so far, and it earned the
# place: it is the one subsystem that carried the requirement as prose while the garment beside it
# carried the same requirement as a measurement.
STAND_PROUD_EXPECTED_ROLES = {"hair"}
VALID_COMPLEXITY_TIERS = {"unassessed", "simple", "moderate", "complex", "ultra-complex"}
TERMINOLOGY_LIST_FIELDS = {"geometryTerms", "materialTerms", "lightingTerms"}
VALID_REVIEW_ACTIONS = {"continue", "refine-spec", "refine-code", "request-input", "stop"}
VALID_TOPOLOGY_CLASSES = {
    "continuous-sculpt",
    "assembled-solid",
    "conforming-shell",
    "surface-relief",
    "fiber-strand",
    "material-only",
    "implicit",
    # Plan 1.5: a zero-thickness, two-sided surface -- a wing membrane, a cape, a leaf,
    # a fin. None of the other seven classes fit: it is not a volume (assembled-solid /
    # continuous-sculpt / implicit all describe solids), and it is not relief carved into
    # a host surface (surface-relief / conforming-shell). Before this class existed such a
    # part had to be routed through `implicit`, but an SDF is a distance field to a
    # *volume boundary* and cannot represent true zero thickness -- it is forced to
    # thicken the membrane into a thin solid. This is the same field-representation limit
    # TRELLIS.2's O-Voxel format removes; see docs/RESEARCH_TRELLIS2_TO_IMG2THREEJS.md
    # section 1.1 and section 4. See validate_open_shell_topology below for what this forbids.
    "open-shell",
}
CS2_ROUTES = {"reference-projection", "authored-texture", "procedural-finish"}
CS2_EXACTNESS_TIERS = {"image-only", "metadata-assisted", "exact-texture"}
# Plan 1.3 Workstream A: primitives that are structurally wrong for a given topology class.
# Prevents "Flat-Projection Bias" (e.g. a continuous organic bulge picked as a box-stack).
DISALLOWED_TOPOLOGY_PRIMITIVE_PAIRS: dict[str, set[str]] = {
    "continuous-sculpt": {"box", "cylinder", "cone"},
    "fiber-strand": {"box", "plane-card"},
}
TOPOLOGY_ALLOWED_HINT = {
    "continuous-sculpt": "lathe, extrude, or curve-sweep",
    "fiber-strand": "tube or instanced-cluster",
}


# Plan 1.5 — the recessed-feature gate (the US-004 defect: "the eye reads as a patch, not
# a recessed socket"). This is a spec-authoring failure, not a measurement failure, and it
# is undetectable downstream: a silhouette gate cannot see an interior concavity (a dimple
# inside the outline changes no silhouette in any view), and a dark-pixel ratio on a
# concave feature measures cavity SHADING, not material (the reference ear reads 14.3%
# "dark" from shading gradient alone, peaking at luminance 60-79, versus the wing's 32.9%
# near-black from actual material). So this class of defect has to be caught in the schema.
#
# How a component declares "recessed": we extend the existing free-text `role` field
# (already token-matched by ATTACHMENT_ROLES / component_requires_attachment below) rather
# than invent a parallel boolean field. A spec author names the part's role with one of
# these tokens (`role: "eye-socket"`, `"ear-canal"`, `"mouth-cavity"`, `"nostril"`, ...);
# `component.name`/`id` are checked too since components are commonly named after the
# feature they are. This was chosen over (a) a new `concave: true` field, which would be a
# second place to encode the same fact `role` already carries and would need its own
# authoring discipline, and (b) reusing `topologyClass` itself, which describes HOW a part
# is built, not WHAT it represents -- the whole point of this gate is to compare the two.
#
# Token design, revised after review — plain "socket" collides with a DIFFERENT, load-
# bearing meaning already in this file: ATTACHMENT_ROLES (below) uses bare "socket" for an
# attachment POINT (`attachment.parentSocket`, `actionProfile.sockets[]`), e.g. a handle's
# hilt socket. That is a real, common authoring case and is not a concavity, so bare
# "socket" is deliberately NOT a recessed-feature token. Instead we match the specific
# compound "eyesocket" as a normalized substring (hyphens/spaces/underscores stripped, so
# `role: "eye-socket"`, `"eye_socket"`, and `"eye socket"` all match) -- narrower than a
# bare word, so it cannot fire on a plain attachment socket. "hollow" and "concave" are
# real words but too easily legitimate outside a cavity context ("hollow tube", "concave
# lens" are both real, non-recessed parts), so they are matched against `role` ONLY, never
# against `name`/`id`, keeping them out of reach of incidental part-naming collisions.
# "dimple" was dropped entirely: a dimple is shallow by definition, so it is the wrong word
# to hold to a depth-requiring rule (below) in the first place.
#
# THE CONTRACT, spelled out because the rule below is now strict (allow-list: implicit +
# subtract, nothing else) and that strictness needs to be a stated trade, not a trap:
# the token IS the declaration. If a component's role/name/id calls it a cavity, canal, or
# recess, this rule holds it to building a REAL one. A shallow decorative relief -- a 0.2mm
# panel line, a knurl pattern -- is not a cavity; do not name it one. Rename it instead
# (e.g. `panel-relief`, `groove-relief`) and it is untouched by this gate, free to be
# `surface-relief` or any other topologyClass. That is the legitimate way out, and
# `test_shallow_relief_panel_without_a_cavity_token_is_accepted` in
# forge/tests/test_recessed_and_open_shell_topology.py proves it actually works.
RECESSED_FEATURE_TOKENS = {"cavity", "canal", "recess", "recessed", "nostril"}
RECESSED_FEATURE_COMPOUND_PHRASES = {"eyesocket"}
RECESSED_FEATURE_ROLE_ONLY_TOKENS = {"hollow", "concave"}
# A recessed feature is real concavity: it must be carved out of a volume (`implicit` +
# an SDF `subtract` operation -- see sdf_primitives.VALID_SDF_OPERATIONS). This is
# deliberately an ALLOW-list (state the one right shape), not a deny-list (enumerate every
# wrong one), after review found that a deny-list of {"surface-relief", "plane-card"} still
# let a THIRD route to the exact same US-004 defect through: `topologyClass:
# "assembled-solid"` + a convex sphere primitive is not surface-relief, is not plane-card,
# and never reaches the subtract check below (which was scoped to `implicit`) -- yet it is
# exactly a convex ball sitting where a recess belongs, and `assembled-solid` + sphere is
# the MOST likely authoring mistake of the three, since assembled-solid is the common
# default and a sphere is the obvious eye shape. A deny-list has to predict every wrong
# answer and a fourth route always remains possible; an allow-list only has to state the
# right one, so it closes all of them at once.
RECESSED_FEATURE_REQUIRED_TOPOLOGY = "implicit"


def component_role_tokens(component: dict[str, Any]) -> set[str]:
    """Same tokenization ATTACHMENT_ROLES matching uses: lowercase, split on non-alphanumerics,
    across role/name/id so a part authored as e.g. `id: "left-eye-socket"` is caught even if
    `role` itself is generic or absent. Fields are joined with a literal space, which also
    acts as the token separator, so a word split across two fields (role="fake eye", name=
    "socket-thing") can never merge into one token here."""
    role = str(component.get("role") or "")
    name = str(component.get("name") or "")
    component_id = str(component.get("id") or "")
    return set(re.findall(r"[a-z0-9]+", f"{role} {name} {component_id}".lower()))


def _normalize_identity_field(value: Any) -> str:
    return re.sub(r"[\s_-]+", "", str(value or "").lower())


def component_recessed_feature_matches(component: dict[str, Any]) -> set[str]:
    """Return which recessed-feature signal(s) fired, for both the boolean gate and the
    error message. Compound phrases are checked per-field (role, name, id separately, never
    concatenated) so a phrase can never assemble itself across a field boundary."""
    matches = component_role_tokens(component) & RECESSED_FEATURE_TOKENS
    normalized_fields = (
        _normalize_identity_field(component.get("role")),
        _normalize_identity_field(component.get("name")),
        _normalize_identity_field(component.get("id")),
    )
    matches |= {
        phrase
        for phrase in RECESSED_FEATURE_COMPOUND_PHRASES
        if any(phrase in field for field in normalized_fields)
    }
    role_tokens = set(re.findall(r"[a-z0-9]+", str(component.get("role") or "").lower()))
    matches |= role_tokens & RECESSED_FEATURE_ROLE_ONLY_TOKENS
    return matches


def component_is_recessed_feature(component: dict[str, Any]) -> bool:
    return bool(component_recessed_feature_matches(component))


def validate_recessed_feature_topology(component_id: str, component: dict[str, Any], errors: list[str]) -> None:
    matches = component_recessed_feature_matches(component)
    if not matches:
        return
    topology_class = component.get("topologyClass")
    primitive = component.get("primitive")
    if topology_class != RECESSED_FEATURE_REQUIRED_TOPOLOGY:
        errors.append(
            f"component {component_id!r} is authored as a recessed feature (role/name/id matches "
            f"{', '.join(sorted(matches))!r}) but is "
            f"topologyClass={topology_class!r} primitive={primitive!r} -- a recessed feature must be real "
            "concavity carved OUT of a volume, not this shape or relief (a convex primitive, a flat "
            "plane-card, and a surface-relief bump are all the same US-004 defect: an eye that reads as a "
            "patch, not a recess) -- reclassify as topologyClass 'implicit' with a geometryDescriptor.sdf "
            "whose operations include 'subtract' to carve the cavity out of the parent volume "
            "(see forge/_shared/sdf_primitives.py VALID_SDF_OPERATIONS)"
        )
        return
    # A rule should verify what it advises: the message above tells the author to use
    # `implicit` + a `subtract` operation, so `implicit` alone is not enough -- an implicit
    # component built ONLY from union/smooth-union operations is a bulge sticking OUT of its
    # parent, not a cavity carved INTO it. That is the same US-004 defect (an eye that reads
    # as a patch, not a recess) wearing a different disguise, and this gate would otherwise
    # wave it through. `subtract` must be PRESENT among the operations, not the only one --
    # a socket legitimately built by smooth-unioning two shapes and then subtracting the
    # result is fine. If `geometryDescriptor.sdf` itself is missing, skip: the
    # `topologyClass 'implicit' requires geometryDescriptor.sdf` check elsewhere already
    # covers that structural case, so this stays free of a duplicate/confusing error.
    descriptor = component.get("geometryDescriptor")
    sdf = descriptor.get("sdf") if isinstance(descriptor, dict) else None
    if isinstance(sdf, dict):
        operations = sdf.get("operations")
        operation_types = (
            [operation.get("type") for operation in operations if isinstance(operation, dict)]
            if isinstance(operations, list)
            else []
        )
        if "subtract" not in operation_types:
            found = ", ".join(sorted({str(item) for item in operation_types})) or "none"
            errors.append(
                f"component {component_id!r} is authored as a recessed feature (role/name/id matches "
                f"{', '.join(sorted(matches))!r}) and is topologyClass 'implicit', but its "
                f"geometryDescriptor.sdf.operations contain no 'subtract' operation (found: {found}) -- "
                "a recessed feature must be carved OUT of a volume; building it only from "
                "union/smooth-union operations produces a bulge sticking OUT, not a cavity, which is "
                "the same US-004 defect (an eye that reads as a patch, not a recess) in disguise. Add a "
                "'subtract' operation that removes volume from the parent shape "
                "(see forge/_shared/sdf_primitives.py VALID_SDF_OPERATIONS)"
            )


# Plan 1.5 — open-shell may not pair with a closed SDF: an SDF is a distance field to a
# volume boundary and has no way to express zero thickness, so combining `topologyClass:
# "open-shell"` with `geometryDescriptor.sdf` would silently thicken the membrane into a
# thin solid, defeating the reason open-shell exists. A double-sided material is required
# for the same reason a one-sided membrane renders invisible from behind: Three.js
# backface-culls a single-sided material, and an open-shell part is, by definition, seen
# from both sides.
def validate_open_shell_topology(
    component_id: str,
    component: dict[str, Any],
    materials_by_id: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    if component.get("topologyClass") != "open-shell":
        return
    descriptor = component.get("geometryDescriptor")
    if isinstance(descriptor, dict) and "sdf" in descriptor:
        errors.append(
            f"component {component_id!r} topologyClass 'open-shell' cannot combine with "
            "geometryDescriptor.sdf -- an SDF is a closed distance field and cannot represent zero "
            "thickness, so it would silently thicken the membrane into a thin solid; drop the sdf "
            "descriptor and build the open-shell surface directly (e.g. plane-card, curve-sweep, or "
            "extrude)"
        )
    material_id = component.get("material")
    material = materials_by_id.get(material_id) if isinstance(material_id, str) else None
    if not isinstance(material, dict) or material.get("doubleSided") is not True:
        errors.append(
            f"component {component_id!r} topologyClass 'open-shell' is a zero-thickness two-sided "
            f"surface but its material {material_id!r} does not set doubleSided: true -- a one-sided "
            "membrane renders invisible from behind; set materials[].doubleSided = true on its material"
        )


# Plan 1.3 G.1 — spec-level flatness gate (the karambit-blade defect signature).
# The robust discriminator is SEMANTIC, not a fragile geometric curvature estimate:
# `continuous-sculpt` asserts "a continuous volumetric 3D form", so pairing it with a
# THIN straight extrude (a flat slab) is a contradiction that will read as a flat plane
# from non-reference angles. A legitimately flat object is tagged surface-relief /
# conforming-shell / material-only, NOT continuous-sculpt, so it never reaches this gate.
FLATNESS_DEPTH_RATIO_MAX = 0.15   # depth/diagonal below this ⇒ thin slab


def _bbox_diagonal(points: list) -> float:
    xs = [p[0] for p in points if isinstance(p, (list, tuple)) and len(p) >= 2]
    ys = [p[1] for p in points if isinstance(p, (list, tuple)) and len(p) >= 2]
    if not xs or not ys:
        return 0.0
    return ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) ** 0.5


TAPER_RATIO_MAX = 0.55   # tip/root radius above this ⇒ it is not tapering, it is a tube


def taper_risk(component_id: str, component: dict[str, Any]) -> tuple[str, str]:
    """Warn when a `tapered-sweep`'s stations do not actually taper.

    Measured from a recovered build: eleven hair locks each ended at radius 0.0327 -- identical to
    four decimals across every lock -- for tip/root ratios of 0.58 to 0.79. The machinery was
    correct and the authored stations were not, so the locks came out blunt and read as noodles.
    Nothing caught it, because a sweep that refuses to taper is still a valid sweep. The reference
    those locks were built against measures 0.087.
    """
    descriptor = component.get("geometryDescriptor")
    if not isinstance(descriptor, dict):
        return ("OK", "")
    sweep = descriptor.get("taperedSweep")
    if not isinstance(sweep, dict):
        return ("OK", "")
    stations = sweep.get("stations")
    if not isinstance(stations, list) or len(stations) < 2:
        return ("OK", "")
    radii = [
        max(
            float(station.get("rx", 0.0)) if is_number(station.get("rx")) else 0.0,
            float(station.get("rz", 0.0)) if is_number(station.get("rz")) else 0.0,
        )
        for station in stations
        if isinstance(station, dict)
    ]
    if len(radii) < 2:
        return ("OK", "")
    root, tip = max(radii), min(radii)
    if root <= 0:
        return ("OK", "")
    # Narrowest station over widest, across ALL stations -- not first over last.
    #
    # Two things break an end-to-end reading. First, station order is not free: a sweep's stations
    # run along its path, and reversing them reverses the triangle winding with them, so a limb
    # authored tip-first renders as an open shell seen from the inside. A part may therefore
    # legitimately be authored root-last, and reading station[0] as "the root" reported a real
    # 0.53 taper as 1.89. Second, plenty of real forms are widest in the MIDDLE -- a barrel torso,
    # a spindle, a lemon -- and their two ends are near-identical however hard they taper; the
    # end-to-end reading called a torso that runs 0.055 to 0.213 and back a constant-radius noodle.
    #
    # The question this check is actually asking is whether the radii vary at all, and that has
    # neither a direction nor a preferred pair of stations.
    ratio = tip / root
    if ratio > TAPER_RATIO_MAX:
        return (
            "HIGH",
            f"quality: component {component_id!r} declares primitive 'tapered-sweep' but its "
            f"stations barely taper (narrow/wide={ratio:.2f} > {TAPER_RATIO_MAX}); it will read as "
            f"a constant-radius noodle. Either taper one end toward the fraction the reference "
            f"measures, or use 'tube' and say so.",
        )
    return ("OK", "")


def validate_chirality(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    """Every `-l`/`-r` pair must be a sagittal MIRROR of each other, not a rotated copy.

    WHY THIS IS A HARD GATE. Two chirality defects shipped in one figure and no gate saw either,
    because both produce geometry that is internally tidy and only wrong against a convention that
    lived in a comment.

    The one this catches: a limb pair built by negating x AND z. That is a 180-degree rotation about
    the vertical axis, and a rotation PRESERVES handedness, so both limbs come out the same hand.
    Measured on the humanoid's thumb, whose tip sat at z +0.288 on one side and -0.288 on the other
    where a mirror leaves z alone. Fixing it moved the hand region 46% closer to the reference in
    the front view.

    The one it CANNOT catch, stated here so nobody trusts a green result too far: a pair that is
    wrong the SAME way on both sides is still a perfect mirror of itself. The humanoid's feet were
    exactly that -- both had the big toe on the outer edge -- and this test passes them. That needs
    `chirality.medial_lateral_bias` against a reference, which lives in the stage 4 gate.
    """
    components = spec.get("componentTree")
    if not isinstance(components, list):
        return

    positions: dict[str, list[float]] = {}
    for component in components:
        if not isinstance(component, dict) or not component.get("id"):
            continue
        transform = component.get("transform")
        offset = transform.get("position") if isinstance(transform, dict) else None
        if isinstance(offset, list) and len(offset) == 3 and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in offset
        ):
            positions[str(component["id"])] = [float(v) for v in offset]

    for right_id, left_id in find_pairs(positions):
        ok, message = check_pair(right_id.rsplit("-", 1)[0], positions[right_id], positions[left_id])
        if not ok:
            errors.append(f"chirality: {message}")

    # A component whose id says left but whose position says right. Separate from the pair test
    # because it fires even when only one half of the pair exists.
    #
    # Measured on WORLD x, not on the component's own transform. `transform.position` is relative
    # to the parent, so a nested pair member -- a toe inside a left paw, a claw inside that toe --
    # legitimately carries a negative local x while sitting on the character's left. Reading the
    # local value flagged every inboard toe of a correctly mirrored pair, and the only way to
    # silence it would have been to stop mirroring them, which is the defect this whole function
    # exists to catch. The pair test above deliberately stays on LOCAL coordinates, where a
    # mirrored pair under mirrored parents is exactly a sign flip.
    parents = {
        str(component["id"]): component.get("parent")
        for component in components
        if isinstance(component, dict) and component.get("id")
    }

    def world_x(component_id: str) -> float:
        total = 0.0
        seen: set[str] = set()
        current = component_id
        while isinstance(current, str) and current in positions and current not in seen:
            seen.add(current)
            total += positions[current][0]
            current = parents.get(current)
        return total

    for component_id, _offset in sorted(positions.items()):
        side = component_id.rsplit("-", 1)[-1] if "-" in component_id else ""
        accumulated = world_x(component_id)
        if side not in ("l", "r") or abs(accumulated) < 1e-9:
            continue
        expected = CHARACTER_LEFT_SIGN if side == "l" else -CHARACTER_LEFT_SIGN
        if (accumulated > 0) != (expected > 0):
            warnings.append(
                f"quality: component {component_id!r} is named for the character's "
                f"{'left' if side == 'l' else 'right'} but sits at world x {accumulated:+.4f}. With "
                f"forward +Z and a right-handed frame the character's left is +X, so a pose or "
                f"animation addressed by anatomical joint name would drive the wrong side."
            )


def validate_stand_proud(
    component_id: str,
    component: dict[str, Any],
    errors: list[str],
    warnings: list[str],
    proud_refs: list[tuple[str, str]],
) -> None:
    """A component may declare that it must stay OUTSIDE another component's surface.

    WHY THIS IS A SCHEMA PROPERTY AND NOT AUTHORING DISCIPLINE. The humanoid demo holds the same
    requirement twice, in two different ways, with two different outcomes. The garment holds it as a
    measurement -- `sectionedLoft`'s `hug` marches every vertex outward along its own spoke until a
    signed distance field reads at least `clearance` -- and it works. The hair holds it as a comment:

        EVERY piece must stand proud of the skull at its own height. Where the skull is proud of the
        hair, the head renders bald there.

    Widening the hair side masses by hand broke that comment and nothing objected. Closure went
    42.2% to 40.9%, worse on all six views, and dark coverage went DOWN because the widened mass had
    slid off the skull. Measured on the archived captures, crown scalp exposure rose on every view,
    by 14.9 points on the worst one.

    `clearance` is how far outside the target surface the component must sit. `maxPush` caps how far
    a vertex may travel to get there, and is required rather than optional: an uncapped march walks
    inner vertices straight through the target and out the far side, closing the very gap the
    component exists to leave.
    """
    proud = component.get("standProud")
    if proud is None:
        if str(component.get("role") or "").lower() in STAND_PROUD_EXPECTED_ROLES:
            warnings.append(
                f"quality: component {component_id!r} has role 'hair' but declares no standProud; "
                f"nothing will stop it sinking into the head, which renders as a bald patch. "
                f"Declare standProud against the head component, or say why it cannot sink."
            )
        return

    if not isinstance(proud, dict):
        errors.append(f"component {component_id!r} standProud must be an object")
        return

    against = proud.get("againstComponentId")
    if not isinstance(against, str) or not against.strip():
        errors.append(f"component {component_id!r} standProud.againstComponentId is required")
    elif against == component_id:
        errors.append(f"component {component_id!r} standProud references itself")
    else:
        proud_refs.append((component_id, against))

    clearance = proud.get("clearance")
    max_push = proud.get("maxPush")
    if not is_number(clearance) or float(clearance) <= 0.0:
        errors.append(
            f"component {component_id!r} standProud.clearance must be a positive number "
            f"(got {clearance!r}); a clearance of zero permits the surfaces to touch and z-fight"
        )
        return
    if not is_number(max_push) or float(max_push) <= 0.0:
        errors.append(
            f"component {component_id!r} standProud.maxPush must be a positive number "
            f"(got {max_push!r}); an uncapped march walks vertices through the target"
        )
        return
    if float(max_push) < float(clearance):
        errors.append(
            f"component {component_id!r} standProud.maxPush ({max_push}) is below its clearance "
            f"({clearance}); the march would stop before reaching the clearance it asks for"
        )


def flatness_risk(component_id: str, component: dict[str, Any]) -> tuple[str, str]:
    """Return ('HIGH'|'OK', message). HIGH when a component the spec declares to be a
    continuous 3D form (`continuous-sculpt`, gated at the call site) is built as a THIN
    straight extrude — a flat slab that only reads correctly from the reference angle.
    The fix is `curve-sweep` (sweep a thin cross-section along a 3D spine)."""
    descriptor = component.get("geometryDescriptor") if isinstance(component.get("geometryDescriptor"), dict) else {}
    profile = descriptor.get("profile2D") if isinstance(descriptor.get("profile2D"), dict) else None
    if not profile:
        return ("OK", "")
    points = profile.get("points")
    depth = profile.get("depth")
    if not isinstance(points, list) or not is_number(depth):
        return ("OK", "")
    diagonal = _bbox_diagonal(points)
    if diagonal <= 0:
        return ("OK", "")
    depth_ratio = float(depth) / diagonal
    if depth_ratio < FLATNESS_DEPTH_RATIO_MAX:
        return (
            "HIGH",
            f"quality: component {component_id!r} flatness risk — declared continuous-sculpt (a "
            f"continuous 3D form) but built as a thin straight extrude "
            f"(depth/diagonal={depth_ratio:.3f} < {FLATNESS_DEPTH_RATIO_MAX}); it will read as a flat "
            f"plane bent into a curve from non-reference angles. Use primitive 'curve-sweep' (sweep a "
            f"thin cross-section along a 3D spine), or re-classify the topology if it really is flat.",
        )
    return ("OK", "")


def schema_version_tuple(spec: dict[str, Any]) -> tuple[int, int]:
    raw = spec.get("schemaVersion")
    if not isinstance(raw, str):
        return (0, 0)
    parts = raw.split(".")
    try:
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except ValueError:
        return (0, 0)


def requires_topology_classification(spec: dict[str, Any]) -> bool:
    # Plan 1.3 Risk R2: only specs authored under schema 2.1+ are held to the new
    # topologyClass/topologyRationale requirement, so pre-1.3 specs are not silently
    # broken by a field they were never told to fill in.
    return schema_version_tuple(spec) >= (2, 1)
VISUAL_PASS_IDS = {
    "blockout",
    "structural-pass",
    "form-refinement",
    "material-pass",
    "surface-pass",
    "lighting-pass",
    "interaction-pass",
}
VALID_PIPELINE_PASS_IDS = VISUAL_PASS_IDS | {"optimization-pass"}
ATTACHMENT_ROLES = {
    "appendage",
    "branch",
    "limb",
    "arm",
    "leg",
    "handle",
    "connector",
    "tube",
    "cable",
    "horn",
    "wing",
    "tail",
    "root",
    "fork",
    "rib",
    "support",
    "hinge",
    "socket",
    "pipe",
}
ATTACHMENT_PRIMITIVES = {"cylinder", "cone", "capsule", "tube", "curve-sweep"}


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_unit_interval(value: Any, label: str, errors: list[str]) -> None:
    if not is_number(value) or value < 0 or value > 1:
        errors.append(f"{label} must be a number from 0 to 1")


def load_spec(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("spec must be a JSON object")
    return payload


def as_number_list(value: Any, length: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == length
        and all(isinstance(item, (int, float)) for item in value)
    )


def validate_score_block(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    scores = spec.get("scores")
    if scores is None:
        warnings.append("missing scores block; image validation evidence will be weaker")
        return
    if not isinstance(scores, dict):
        errors.append("scores must be an object")
        return
    for key, value in scores.items():
        if not isinstance(value, int) or value < 0 or value > 3:
            errors.append(f"score {key!r} must be an integer from 0 to 3")


def validate_nonnegative_int(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        errors.append(f"{label} must be a non-negative integer")


def validate_pre_spec_assessment(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    assessment = spec.get("preSpecAssessment")
    if assessment is None:
        warnings.append("quality: missing preSpecAssessment; spec may be shallow because complexity was not assessed first")
        return
    if not isinstance(assessment, dict):
        errors.append("preSpecAssessment must be an object")
        return
    object_class = assessment.get("objectClass")
    if not isinstance(object_class, dict):
        errors.append("preSpecAssessment.objectClass must be an object")
    else:
        primary_type = object_class.get("primaryType")
        if primary_type is not None and not isinstance(primary_type, str):
            errors.append("preSpecAssessment.objectClass.primaryType must be a string")
        if primary_type in {None, "", "unassessed"}:
            warnings.append("quality: preSpecAssessment.objectClass.primaryType is unassessed")
        for field in ("formLanguage", "structureKind", "motionPotential", "materialFamilies"):
            validate_string_array(object_class.get(field), f"preSpecAssessment.objectClass.{field}", errors)
            if isinstance(object_class.get(field), list) and not object_class[field]:
                warnings.append(f"quality: preSpecAssessment.objectClass.{field} is empty")
    complexity = assessment.get("complexity")
    if not isinstance(complexity, dict):
        errors.append("preSpecAssessment.complexity must be an object")
    else:
        tier = complexity.get("tier")
        if tier not in VALID_COMPLEXITY_TIERS:
            errors.append(f"preSpecAssessment.complexity.tier must be one of: {', '.join(sorted(VALID_COMPLEXITY_TIERS))}")
        if tier == "unassessed":
            warnings.append("quality: preSpecAssessment.complexity.tier is unassessed")
        scores = complexity.get("scores")
        if not isinstance(scores, dict):
            errors.append("preSpecAssessment.complexity.scores must be an object")
        else:
            for key, value in scores.items():
                if not isinstance(value, int) or value < 0 or value > 3:
                    errors.append(f"preSpecAssessment.complexity.scores.{key} must be an integer from 0 to 3")
        estimated = complexity.get("estimatedCounts")
        if not isinstance(estimated, dict):
            errors.append("preSpecAssessment.complexity.estimatedCounts must be an object")
        else:
            for field in ("macroComponents", "mesoComponents", "microFeatureGroups", "materialLayers", "repetitionSystems"):
                if field in estimated:
                    validate_nonnegative_int(estimated[field], f"preSpecAssessment.complexity.estimatedCounts.{field}", errors)
        validate_string_array(complexity.get("reasoning"), "preSpecAssessment.complexity.reasoning", errors)
    decision = assessment.get("specDepthDecision")
    if not isinstance(decision, dict):
        errors.append("preSpecAssessment.specDepthDecision must be an object")
    else:
        required_depth = decision.get("requiredDepth")
        if required_depth not in VALID_COMPLEXITY_TIERS:
            errors.append("preSpecAssessment.specDepthDecision.requiredDepth must be a valid complexity tier")
        if required_depth == "unassessed":
            warnings.append("quality: preSpecAssessment.specDepthDecision.requiredDepth is unassessed")
        validate_string_array(decision.get("minimumComponentLevels"), "preSpecAssessment.specDepthDecision.minimumComponentLevels", errors)
        for field in (
            "needsRepetitionSystems",
            "needsMaterialLocalOverrides",
            "needsMultipleReviewViews",
            "needsActionReadyHierarchy",
        ):
            if field in decision and not isinstance(decision[field], bool):
                errors.append(f"preSpecAssessment.specDepthDecision.{field} must be boolean")
    unknowns = assessment.get("unknownsToResolveBeforeImplementation")
    validate_string_array(unknowns, "preSpecAssessment.unknownsToResolveBeforeImplementation", errors)
    if isinstance(unknowns, list) and unknowns:
        warnings.append("quality: preSpecAssessment has unresolved unknowns before implementation")


def validate_terminology_profile(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    profile = spec.get("terminologyProfile")
    if profile is None:
        warnings.append("missing terminologyProfile; descriptions may drift into vague non-3D language")
        return
    if not isinstance(profile, dict):
        errors.append("terminologyProfile must be an object")
        return
    for field in TERMINOLOGY_LIST_FIELDS:
        value = profile.get(field)
        if value is None:
            warnings.append(f"terminologyProfile.{field} is missing")
            continue
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            errors.append(f"terminologyProfile.{field} must be an array of non-empty strings")
    rule = profile.get("descriptionRule")
    if rule is not None and not isinstance(rule, str):
        errors.append("terminologyProfile.descriptionRule must be a string")


def validate_evidence(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> set[str]:
    refs: set[str] = set()
    evidence = spec.get("viewEvidence", [])
    if evidence is None:
        return refs
    if not isinstance(evidence, list):
        errors.append("viewEvidence must be an array when present")
        return refs
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            errors.append(f"viewEvidence[{index}] must be an object")
            continue
        evidence_id = item.get("id")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            errors.append(f"viewEvidence[{index}].id is required")
            continue
        if evidence_id in refs:
            errors.append(f"duplicate viewEvidence id {evidence_id!r}")
        refs.add(evidence_id)
        confidence = item.get("confidence")
        if confidence is not None:
            validate_unit_interval(confidence, f"viewEvidence {evidence_id!r} confidence", errors)
        region = item.get("imageRegion")
        if region is not None:
            if not isinstance(region, dict):
                errors.append(f"viewEvidence {evidence_id!r} imageRegion must be an object")
            else:
                for key in ("x", "y", "width", "height"):
                    if key in region and not is_number(region[key]):
                        errors.append(f"viewEvidence {evidence_id!r} imageRegion.{key} must be numeric")
    if not refs:
        warnings.append("missing viewEvidence; local visual claims cannot be traced back to image regions")
    return refs


def validate_material_scalar_or_layer(value: Any, label: str, errors: list[str]) -> None:
    if value is None:
        return
    if is_number(value):
        return
    if not isinstance(value, dict):
        errors.append(f"{label} must be a number or object")
        return
    base = value.get("base")
    if base is not None and not is_number(base):
        errors.append(f"{label}.base must be numeric")
    variation = value.get("variation")
    if variation is not None and not is_number(variation):
        errors.append(f"{label}.variation must be numeric")


def validate_reference_pbr_map(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return
    has_locator = False
    for field in ("path", "url"):
        item = value.get(field)
        if item is not None:
            if not isinstance(item, str) or not item.strip():
                errors.append(f"{label}.{field} must be a non-empty string when present")
            else:
                has_locator = True
    if not has_locator:
        errors.append(f"{label} needs a path or url")
    channel = value.get("channel")
    if channel is not None and not isinstance(channel, str):
        errors.append(f"{label}.channel must be a string")


def validate_reference_pbr(material_id: str, value: Any, errors: list[str], warnings: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append(f"material {material_id!r} referencePbr must be an object")
        return
    for field in ("version", "sourceImage", "extractor", "method", "verdict", "hardLimit"):
        item = value.get(field)
        if item is not None and not isinstance(item, str):
            errors.append(f"material {material_id!r} referencePbr.{field} must be a string")
    usable = value.get("usable")
    if usable is not None and not isinstance(usable, bool):
        errors.append(f"material {material_id!r} referencePbr.usable must be boolean")
    for field in ("confidence", "estimatedFidelity", "targetThreshold"):
        item = value.get(field)
        if item is not None:
            validate_unit_interval(item, f"material {material_id!r} referencePbr.{field}", errors)
    maps = value.get("maps")
    if maps is None:
        warnings.append(f"quality: material {material_id!r} referencePbr is missing maps")
        return
    if not isinstance(maps, dict):
        errors.append(f"material {material_id!r} referencePbr.maps must be an object")
        return
    required = ("albedo", "roughness", "height", "normal", "ao")
    for channel in required:
        if channel not in maps:
            warnings.append(f"quality: material {material_id!r} referencePbr.maps missing {channel}")
        else:
            validate_reference_pbr_map(maps[channel], f"material {material_id!r} referencePbr.maps.{channel}", errors)


def validate_cs2_view_dependent_environment(spec: dict[str, Any], errors: list[str]) -> None:
    """View-dependent CS2 finishes (anodized / anodized-multicolored) read their color from
    environment reflections -- rendering one with no environment at all is a muddy-render gate
    failure, not a quality nit. The code-generated default environment always exists unless
    explicitly disabled (cs2Finish.environmentAvailable = false), so this only fires as the
    last-resort guard described in design.md, never on the default image-only path.
    See grimoire/build/cs2_finishes.md."""
    materials = [m for m in spec.get("materials", []) if isinstance(m, dict)]
    view_dependent = [m for m in materials if m.get("needsEnvironment") is True]
    if not view_dependent:
        return
    cs2_finish = spec.get("cs2Finish")
    environment_available = not (isinstance(cs2_finish, dict) and cs2_finish.get("environmentAvailable") is False)
    if not environment_available:
        names = ", ".join(str(m.get("id")) for m in view_dependent)
        errors.append(
            f"material(s) {names} are view-dependent and need an environment map (scene.environment) "
            "or they render muddy, but cs2Finish.environmentAvailable is false -- enable the "
            "code-generated default environment or supply a user HDRI before generating "
            "(see grimoire/build/cs2_finishes.md)"
        )


def validate_cs2_contract(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    intake = spec.get("cs2Intake")
    if intake is None:
        return
    if not isinstance(intake, dict):
        errors.append("cs2Intake must be an object")
        return
    route = intake.get("route")
    tier = intake.get("exactnessTier")
    if route not in CS2_ROUTES:
        errors.append("cs2Intake.route must be a supported CS2 route")
    if tier not in CS2_EXACTNESS_TIERS:
        errors.append("cs2Intake.exactnessTier must be a supported exactness tier")
    if intake.get("itemFamily") != "knife":
        errors.append("cs2Intake requires the registered knife adapter")
    if route == "reference-projection":
        camera = spec.get("referenceCamera")
        source = intake.get("deLitAlbedo") or intake.get("sourceImage")
        if not isinstance(camera, dict) or camera.get("solved") is not True:
            warnings.append("quality: reference-projection needs solved referenceCamera")
        if not isinstance(source, str) or not source.strip():
            errors.append("reference-projection requires a de-lit source image")
    if route == "authored-texture":
        materials = [item for item in spec.get("materials", []) if isinstance(item, dict)]
        pbr = next((item.get("referencePbr") for item in materials if item.get("id") == "skin-finish"), None)
        maps = pbr.get("maps") if isinstance(pbr, dict) else None
        required = ("albedo", "normal", "roughness", "metalness")
        if tier == "exact-texture" and (not isinstance(maps, dict) or not all(key in maps for key in required)):
            errors.append("exact-texture authored route requires independent albedo, normal, roughness, and metalness maps")
    if route == "procedural-finish" and tier == "exact-texture":
        errors.append("procedural-finish cannot claim exact-texture")


def validate_pipeline_routing_contract(spec: dict[str, Any], errors: list[str]) -> None:
    routing = spec.get("pipelineRouting")
    legacy_cs2 = routing is None and spec.get("cs2Intake") is not None
    if routing is None:
        if spec.get("cs2Intake") is None:
            return
        routing = resolve_pipeline_routing(legacy_cs2=True)
    routing_errors = validate_pipeline_routing(routing)
    errors.extend(routing_errors)
    if not isinstance(routing, dict) or routing.get("status") != "resolved":
        errors.append("pipelineRouting must be resolved before validation")
        return
    routing_track = routing.get("track")
    object_class = spec.get("preSpecAssessment", {}).get("objectClass", {})
    if routing_track == "character-v1.5" and spec.get("cs2Intake") is not None:
        errors.append("character-v1.5 routing cannot carry cs2Intake")
    if routing_track == "character-v1.5" and object_class.get("primaryDomain") not in {"character", "hybrid"}:
        errors.append("character-v1.5 routing requires the character template")
    if routing_track == "weapon-v1.4" and not legacy_cs2 and object_class.get("cs2") is not True:
        errors.append("weapon-v1.4 routing requires the CS2 weapon template")


def validate_materials(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> set[str]:
    material_ids: set[str] = set()
    for index, material in enumerate(spec.get("materials", [])):
        if not isinstance(material, dict):
            errors.append(f"materials[{index}] must be an object")
            continue
        material_id = material.get("id")
        if not isinstance(material_id, str) or not material_id.strip():
            errors.append(f"materials[{index}].id is required")
            continue
        if material_id in material_ids:
            errors.append(f"duplicate material id {material_id!r}")
        material_ids.add(material_id)
        # What three.js will ACTUALLY do with these numbers -- gated features, clamps, values folded
        # into one uniform, and the base-darkening sheen applies to itself. A value the engine ignores
        # is worse than a missing one: it reads as authored evidence and renders as nothing.
        # Derivation with every file:line: grimoire/build/threejs_skin_and_cloth_materials.md
        physics_errors, physics_warnings = check_material_physics(material_id, material)
        errors.extend(physics_errors)
        warnings.extend(physics_warnings)
        color = material.get("baseColor", material.get("color"))
        if color is not None and not (isinstance(color, str) and color.startswith("#") and len(color) in {4, 7}):
            errors.append(f"material {material_id!r} baseColor/color should be #RGB or #RRGGBB")
        for field in ("shaderModel", "type"):
            value = material.get(field)
            if value is not None and not isinstance(value, str):
                errors.append(f"material {material_id!r} {field} must be a string")
        double_sided = material.get("doubleSided")
        if double_sided is not None and not isinstance(double_sided, bool):
            errors.append(f"material {material_id!r} doubleSided must be boolean")
        for field in ("albedo", "ambientOcclusion"):
            value = material.get(field)
            if value is not None and not isinstance(value, dict):
                errors.append(f"material {material_id!r} {field} must be an object")
        validate_material_scalar_or_layer(material.get("roughness"), f"material {material_id!r} roughness", errors)
        validate_material_scalar_or_layer(material.get("metalness"), f"material {material_id!r} metalness", errors)
        for field in ("normal", "bump", "displacement", "wear", "dirt"):
            value = material.get(field)
            if value is not None and not isinstance(value, dict):
                errors.append(f"material {material_id!r} {field} must be an object")
        texture_resolution = material.get("textureResolution")
        if texture_resolution is not None and (
            not isinstance(texture_resolution, int)
            or isinstance(texture_resolution, bool)
            or texture_resolution < 64
            or texture_resolution > 4096
        ):
            errors.append(f"material {material_id!r} textureResolution must be an integer from 64 to 4096")
        projection = material.get("textureProjection")
        if projection is not None:
            if not isinstance(projection, dict):
                errors.append(f"material {material_id!r} textureProjection must be an object")
            else:
                mode = projection.get("mode")
                if mode is not None and not isinstance(mode, str):
                    errors.append(f"material {material_id!r} textureProjection.mode must be a string")
                repeat = projection.get("repeat")
                if repeat is not None and not (
                    isinstance(repeat, list)
                    and len(repeat) == 2
                    and all(is_number(item) and item > 0 for item in repeat)
                ):
                    errors.append(f"material {material_id!r} textureProjection.repeat must contain two positive numbers")
                anisotropy = projection.get("anisotropy")
                if anisotropy is not None and (not is_number(anisotropy) or anisotropy < 1):
                    errors.append(f"material {material_id!r} textureProjection.anisotropy must be >= 1")
        frequency_bands = material.get("surfaceFrequencyBands")
        if frequency_bands is not None:
            if not isinstance(frequency_bands, list):
                errors.append(f"material {material_id!r} surfaceFrequencyBands must be an array")
            else:
                seen_band_ids: set[str] = set()
                for band_index, band in enumerate(frequency_bands):
                    if not isinstance(band, dict):
                        errors.append(
                            f"material {material_id!r} surfaceFrequencyBands[{band_index}] must be an object"
                        )
                        continue
                    band_id = band.get("id")
                    if not isinstance(band_id, str) or not band_id.strip():
                        errors.append(
                            f"material {material_id!r} surfaceFrequencyBands[{band_index}].id is required"
                        )
                    elif band_id in seen_band_ids:
                        errors.append(f"material {material_id!r} has duplicate surface band {band_id!r}")
                    else:
                        seen_band_ids.add(band_id)
                    for field in ("frequency", "amplitude"):
                        value = band.get(field)
                        if not is_number(value) or value <= 0:
                            errors.append(
                                f"material {material_id!r} surfaceFrequencyBands[{band_index}].{field} "
                                "must be a positive number"
                            )
        local_overrides = material.get("localOverrides", [])
        if local_overrides is not None and not isinstance(local_overrides, list):
            errors.append(f"material {material_id!r} localOverrides must be an array")
        shader_notes = material.get("shaderNotes")
        if shader_notes is not None:
            validate_string_array(shader_notes, f"material {material_id!r} shaderNotes", errors)
        validate_textureless(material_id, material, errors)
        validate_reference_pbr(material_id, material.get("referencePbr"), errors, warnings)
    if not material_ids:
        errors.append("at least one material is required")
    return material_ids


def validate_material_pipeline_contract(spec: dict[str, Any], material_ids: set[str], errors: list[str], warnings: list[str]) -> None:
    """Validate the optional v1.5 material-reference hand-off.

    Legacy specs remain valid. Once ``materialPipeline`` is present, every
    analyzed region must point at a real spec material and retain evidence.
    """
    pipeline = spec.get("materialPipeline")
    if pipeline is None:
        return
    if not isinstance(pipeline, dict):
        errors.append("materialPipeline must be an object")
        return
    if pipeline.get("schemaVersion") != 1:
        errors.append("materialPipeline.schemaVersion must be 1")
    status = pipeline.get("status")
    if status not in {"proceed", "probe"}:
        errors.append("materialPipeline.status must be proceed or probe")
    regions = pipeline.get("regions")
    if not isinstance(regions, list) or not regions:
        errors.append("materialPipeline.regions must be a non-empty array")
        return
    for index, region in enumerate(regions):
        if not isinstance(region, dict):
            errors.append(f"materialPipeline.regions[{index}] must be an object")
            continue
        material_id = region.get("specMaterialId")
        if material_id not in material_ids:
            errors.append(f"materialPipeline region {region.get('regionId')!r} references unknown material {material_id!r}")
        for field in ("componentId", "regionId", "profileId"):
            if not isinstance(region.get(field), str) or not region[field].strip():
                errors.append(f"materialPipeline.regions[{index}].{field} is required")
    registry = pipeline.get("registry")
    if not isinstance(registry, str) or not registry.strip():
        errors.append("materialPipeline.registry is required")
    elif not registry.endswith("material-reference.json"):
        warnings.append("quality: materialPipeline.registry does not point to material-reference.json")
def validate_dimensions(component_id: str, dimensions: Any, errors: list[str]) -> None:
    if dimensions is None:
        return
    if not isinstance(dimensions, dict):
        errors.append(f"component {component_id!r} dimensions must be an object")
        return
    for field in ("width", "height", "depth", "radius", "length"):
        if field in dimensions and not is_number(dimensions[field]):
            errors.append(f"component {component_id!r} dimensions.{field} must be numeric")
    confidence = dimensions.get("confidence")
    if confidence is not None:
        validate_unit_interval(confidence, f"component {component_id!r} dimensions.confidence", errors)


def validate_geometry_descriptor(component_id: str, descriptor: Any, errors: list[str]) -> None:
    if descriptor is None:
        return
    if not isinstance(descriptor, dict):
        errors.append(f"component {component_id!r} geometryDescriptor must be an object")
        return
    for field in ("topologyIntent", "uvStrategy", "normalStrategy"):
        value = descriptor.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"component {component_id!r} geometryDescriptor.{field} must be a string")
    edge = descriptor.get("edgeTreatment")
    if edge is not None:
        if not isinstance(edge, dict):
            errors.append(f"component {component_id!r} geometryDescriptor.edgeTreatment must be an object")
        else:
            if "bevelRadius" in edge and not is_number(edge["bevelRadius"]):
                errors.append(f"component {component_id!r} edgeTreatment.bevelRadius must be numeric")
            if "segments" in edge and not isinstance(edge["segments"], int):
                errors.append(f"component {component_id!r} edgeTreatment.segments must be an integer")
    stack = descriptor.get("deformationStack")
    if stack is not None and not isinstance(stack, list):
        errors.append(f"component {component_id!r} geometryDescriptor.deformationStack must be an array")
    subdivide = descriptor.get("subdivide")
    if subdivide is not None:
        if not isinstance(subdivide, dict):
            errors.append(f"component {component_id!r} geometryDescriptor.subdivide must be an object")
        elif "iterations" in subdivide:
            iterations = subdivide["iterations"]
            label = f"component {component_id!r} geometryDescriptor.subdivide.iterations"
            if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations < 0:
                errors.append(f"{label} must be a non-negative integer")
            elif iterations > MAX_SUBDIVISION_ITERATIONS:
                errors.append(f"{label} must not exceed {MAX_SUBDIVISION_ITERATIONS}")
    decimate = descriptor.get("decimate")
    if decimate is not None:
        label = f"component {component_id!r} geometryDescriptor.decimate"
        if not isinstance(decimate, dict):
            errors.append(f"{label} must be an object")
        else:
            ratio = decimate.get("targetRatio")
            if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
                errors.append(f"{label}.targetRatio must be a number")
            elif not 0.0 < float(ratio) < 1.0:
                # 1.0 is rejected rather than treated as a no-op: asking to keep everything is
                # almost always a mistaken ratio, and paying for a quadric pass that removes
                # nothing is worse than being told.
                errors.append(f"{label}.targetRatio must be greater than 0 and less than 1")
            uv_strategy = str(descriptor.get("uvStrategy") or "")
            if "unwrap" in uv_strategy.lower() or "authored" in uv_strategy.lower():
                # Decimation keeps `position` and recomputes normals; a quadric collapse has no
                # correct answer for an authored UV at the merged vertex, so the seam would move.
                errors.append(
                    f"{label} cannot combine with an authored/unwrapped uvStrategy "
                    f"({uv_strategy!r}); decimation keeps position only and drops UVs"
                )
    if "sdf" in descriptor:
        validate_sdf_descriptor(component_id, descriptor["sdf"], errors)
    if "visualHull" in descriptor:
        validate_visual_hull_descriptor(component_id, descriptor["visualHull"], errors)
    if "sdf" in descriptor and "visualHull" in descriptor:
        errors.append(f"component {component_id!r} geometryDescriptor cannot combine sdf and visualHull")
    if "visualHull" in descriptor and "subdivide" in descriptor:
        errors.append(f"component {component_id!r} geometryDescriptor.visualHull cannot combine with subdivide")


def attachment_emits_cylinder(attachment: Any) -> bool:
    if not isinstance(attachment, dict):
        return False
    start = attachment.get("localStart")
    end = attachment.get("localEnd")
    start_vector = start if as_number_list(start, 3) else [0, 0, 0]
    end_vector = end if as_number_list(end, 3) else [0, 1, 0]
    return sum((float(end_vector[index]) - float(start_vector[index])) ** 2 for index in range(3)) > 0.0001**2


def emitted_subdivision_primitive(primitive: str, topology_class: Any, descriptor: dict[str, Any]) -> str:
    if topology_class == "implicit":
        return "implicit sdf"
    return resolve_instanced_cluster_base(primitive, descriptor, VALID_PRIMITIVES)


def validate_subdivision_budget(
    component_id: str,
    primitive: Any,
    topology_class: Any,
    descriptor: Any,
    attachment: Any,
    errors: list[str],
) -> None:
    if not isinstance(primitive, str) or not isinstance(descriptor, dict):
        return
    subdivide = descriptor.get("subdivide")
    if not isinstance(subdivide, dict):
        return
    iterations = subdivide.get("iterations")
    if (
        not isinstance(iterations, int)
        or isinstance(iterations, bool)
        or iterations < 1
        or iterations > MAX_SUBDIVISION_ITERATIONS
    ):
        return
    emitted_primitive = emitted_subdivision_primitive(primitive, topology_class, descriptor)
    if emitted_primitive == "implicit sdf":
        errors.append(
            f"component {component_id!r} geometryDescriptor.subdivide.iterations cannot statically budget "
            "emitted primitive 'implicit sdf'; subdivision is unsupported for this generator path"
        )
        return
    if emitted_primitive == "plane-card":
        errors.append(
            f"component {component_id!r} geometryDescriptor.subdivide.iterations plane-card subdivision topology is unsupported "
            "because generated PlaneGeometry has open boundary edges"
        )
        return
    uses_attachment_cylinder = attachment_emits_cylinder(attachment)
    if emitted_primitive == "torus" and not uses_attachment_cylinder:
        errors.append(
            f"component {component_id!r} geometryDescriptor.subdivide.iterations torus subdivision topology is unsupported "
            "because generated TorusGeometry has an open weld seam"
        )
        return
    source_faces = ATTACHMENT_CYLINDER_SUBDIVISION_SOURCE_FACES if uses_attachment_cylinder else SUBDIVISION_SOURCE_FACE_ESTIMATES.get(emitted_primitive)
    if source_faces is None:
        errors.append(
            f"component {component_id!r} geometryDescriptor.subdivide.iterations cannot statically budget "
            f"emitted primitive {emitted_primitive!r}; subdivision is unsupported for this generator path"
        )
        return
    projected_faces = source_faces * (4**iterations)
    if projected_faces > MAX_SUBDIVISION_QUAD_FACES:
        source_label = "attachment cylinder" if uses_attachment_cylinder else f"primitive {emitted_primitive!r}"
        errors.append(
            f"component {component_id!r} geometryDescriptor.subdivide.iterations would produce "
            f"{projected_faces} quad faces for {source_label}, exceeding "
            f"the maximum {MAX_SUBDIVISION_QUAD_FACES}"
        )


def validate_bool_object(value: Any, label: str, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return
    for key, item in value.items():
        if not isinstance(key, str):
            errors.append(f"{label} keys must be strings")
        if not isinstance(item, bool):
            errors.append(f"{label}.{key} must be boolean")


def validate_action_profile(component_id: str, profile: Any, errors: list[str], warnings: list[str]) -> None:
    if profile is None:
        warnings.append(f"component {component_id!r} is missing actionProfile; future animation/destruction may require refactor")
        return
    if not isinstance(profile, dict):
        errors.append(f"component {component_id!r} actionProfile must be an object")
        return
    role = profile.get("animationRole")
    if role is not None and not isinstance(role, str):
        errors.append(f"component {component_id!r} actionProfile.animationRole must be a string")
    pivot = profile.get("pivot")
    if pivot is not None:
        if not isinstance(pivot, dict):
            errors.append(f"component {component_id!r} actionProfile.pivot must be an object")
        else:
            mode = pivot.get("mode")
            if mode is not None and not isinstance(mode, str):
                errors.append(f"component {component_id!r} actionProfile.pivot.mode must be a string")
            for field in ("localPosition", "axis"):
                if field in pivot and not as_number_list(pivot[field], 3):
                    errors.append(f"component {component_id!r} actionProfile.pivot.{field} must be [number, number, number]")
            confidence = pivot.get("confidence")
            if confidence is not None:
                validate_unit_interval(confidence, f"component {component_id!r} actionProfile.pivot.confidence", errors)
    validate_bool_object(profile.get("transformChannels"), f"component {component_id!r} actionProfile.transformChannels", errors)
    sockets = profile.get("sockets")
    if sockets is not None:
        if not isinstance(sockets, list):
            errors.append(f"component {component_id!r} actionProfile.sockets must be an array")
        else:
            for socket_index, socket in enumerate(sockets):
                if not isinstance(socket, dict):
                    errors.append(f"component {component_id!r} actionProfile.sockets[{socket_index}] must be an object")
                    continue
                socket_id = socket.get("id")
                if socket_id is not None and not isinstance(socket_id, str):
                    errors.append(f"component {component_id!r} actionProfile.sockets[{socket_index}].id must be a string")
                for field in ("localPosition", "position", "localRotation", "rotation"):
                    if field in socket and not as_number_list(socket[field], 3):
                        errors.append(
                            f"component {component_id!r} actionProfile.sockets[{socket_index}].{field} must be [number, number, number]"
                        )
    collider = profile.get("collider")
    if collider is not None:
        if not isinstance(collider, dict):
            errors.append(f"component {component_id!r} actionProfile.collider must be an object")
        else:
            collider_type = collider.get("type")
            if collider_type is not None and not isinstance(collider_type, str):
                errors.append(f"component {component_id!r} actionProfile.collider.type must be a string")
            for field in ("offset", "scale"):
                if field in collider and not as_number_list(collider[field], 3):
                    errors.append(f"component {component_id!r} actionProfile.collider.{field} must be [number, number, number]")
            if "isTrigger" in collider and not isinstance(collider["isTrigger"], bool):
                errors.append(f"component {component_id!r} actionProfile.collider.isTrigger must be boolean")
    constraints = profile.get("constraints")
    if constraints is not None and not isinstance(constraints, list):
        errors.append(f"component {component_id!r} actionProfile.constraints must be an array")
    destruction = profile.get("destruction")
    if destruction is not None:
        if not isinstance(destruction, dict):
            errors.append(f"component {component_id!r} actionProfile.destruction must be an object")
        else:
            if "breakable" in destruction and not isinstance(destruction["breakable"], bool):
                errors.append(f"component {component_id!r} actionProfile.destruction.breakable must be boolean")
            if "breakImpulse" in destruction and not is_number(destruction["breakImpulse"]):
                errors.append(f"component {component_id!r} actionProfile.destruction.breakImpulse must be numeric")
            for field in ("fractureGroup", "debrisMaterial"):
                value = destruction.get(field)
                if value is not None and not isinstance(value, str):
                    errors.append(f"component {component_id!r} actionProfile.destruction.{field} must be a string")
            for field in ("seamRefs", "detachableFragments"):
                validate_string_array(destruction.get(field), f"component {component_id!r} actionProfile.destruction.{field}", errors)


def component_requires_attachment(component: dict[str, Any]) -> bool:
    if not component.get("parent"):
        return False
    role = str(component.get("role") or "").lower()
    name = str(component.get("name") or component.get("id") or "").lower()
    primitive = str(component.get("primitive") or "").lower()
    profile = component.get("actionProfile") if isinstance(component.get("actionProfile"), dict) else {}
    animation_role = str(profile.get("animationRole") or "").lower()
    tokens = {role, animation_role} | set(re.findall(r"[a-z0-9]+", name))
    return bool(tokens & ATTACHMENT_ROLES) or primitive in ATTACHMENT_PRIMITIVES


def has_attachment_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def attachment_is_complete(attachment: dict[str, Any]) -> bool:
    has_endpoint = as_number_list(attachment.get("localStart"), 3) and as_number_list(attachment.get("localEnd"), 3)
    has_socket = isinstance(attachment.get("parentSocket"), str) and bool(attachment["parentSocket"].strip())
    has_parent_id = isinstance(attachment.get("parentId"), str) and bool(attachment["parentId"].strip())
    has_contact = isinstance(attachment.get("contactType"), str) and bool(attachment["contactType"].strip())
    has_overlap = (
        has_attachment_number(attachment.get("embedDepth"))
        and float(attachment["embedDepth"]) > 0
    ) or (
        has_attachment_number(attachment.get("overlap"))
        and float(attachment["overlap"]) > 0
    )
    has_tolerance = has_attachment_number(attachment.get("gapTolerance"))
    return has_endpoint and (has_socket or has_parent_id) and has_contact and has_overlap and has_tolerance


def validate_attachment(
    component_id: str,
    parent: str | None,
    attachment: Any,
    required: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    if attachment is None:
        if required:
            warnings.append(
                f"quality: component {component_id!r} requires attachment.parentSocket, localStart/localEnd, "
                "contactType, embedDepth or overlap, and gapTolerance"
            )
        return
    if not isinstance(attachment, dict):
        errors.append(f"component {component_id!r} attachment must be an object")
        return
    for field in ("parentId", "parentSocket", "contactType"):
        value = attachment.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"component {component_id!r} attachment.{field} must be a string")
    if parent and isinstance(attachment.get("parentId"), str) and attachment["parentId"] != parent:
        warnings.append(
            f"quality: component {component_id!r} attachment.parentId {attachment['parentId']!r} "
            f"does not match parent {parent!r}"
        )
    for field in ("localStart", "localEnd", "contactNormal"):
        value = attachment.get(field)
        if value is not None and not as_number_list(value, 3):
            errors.append(f"component {component_id!r} attachment.{field} must be [number, number, number]")
    for field in ("embedDepth", "overlap", "gapTolerance", "baseRadius", "endRadius"):
        value = attachment.get(field)
        if value is not None and (not has_attachment_number(value) or float(value) < 0):
            errors.append(f"component {component_id!r} attachment.{field} must be a non-negative number")
    validate_string_array(attachment.get("evidenceRefs"), f"component {component_id!r} attachment.evidenceRefs", errors)
    if required and not attachment_is_complete(attachment):
        warnings.append(
            f"quality: component {component_id!r} requires attachment.parentSocket, localStart/localEnd, "
            "contactType, embedDepth or overlap, and gapTolerance"
        )


def validate_string_array(value: Any, label: str, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append(f"{label} must be an array of strings")


VALID_MATERIAL_CLASSES = {
    "metal", "plastic", "wood", "fabric", "skin", "glass", "ceramic", "rubber", "stone", "unknown",
}
# Alpha accepts "0", "1", "0.xxx", "1.xxx", or ".xxx" — must match what lab_to_rgba() in
# extract_part_color_recipe.py actually emits (round(alpha, 3) renders full opacity as "1.0",
# not bare "1"), or every extracted recipe fails this check on its own real output.
RGBA_PATTERN = re.compile(r"^rgba\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*(?:(?:0|1)(?:\.\d+)?|\.\d+)\s*\)$")


def is_rgba_string(value: Any) -> bool:
    return isinstance(value, str) and bool(RGBA_PATTERN.match(value.strip()))


def validate_color_material_recipe(component_id: str, recipe: Any, warnings: list[str]) -> None:
    """Plan 1.3 Workstream C: every non-material-only component needs a structured,
    evidence-linked colorMaterialRecipe instead of bare-word colors. Fires as a
    'quality:' warning so --strict-quality (not normal validation) enforces it,
    matching the topologyClass gating above."""
    if not isinstance(recipe, dict):
        warnings.append(f"quality: component {component_id!r} is missing colorMaterialRecipe")
        return
    for field in ("dominantAlbedo", "secondaryAlbedo"):
        if not is_rgba_string(recipe.get(field)):
            warnings.append(
                f"quality: component {component_id!r} colorMaterialRecipe.{field} must be an "
                f"'rgba(r, g, b, a)' string"
            )
    material_class = recipe.get("materialClass")
    if material_class not in VALID_MATERIAL_CLASSES:
        warnings.append(
            f"quality: component {component_id!r} colorMaterialRecipe.materialClass must be one "
            f"of: {', '.join(sorted(VALID_MATERIAL_CLASSES))}"
        )
    confidence = recipe.get("materialClassConfidence")
    if not is_number(confidence) or not (0.0 <= float(confidence) <= 1.0):
        warnings.append(
            f"quality: component {component_id!r} colorMaterialRecipe.materialClassConfidence "
            f"must be a number between 0.0 and 1.0"
        )
    gradient = recipe.get("colorGradient")
    if gradient is not None:
        if not isinstance(gradient, dict) or gradient.get("type") not in {"linear", "radial"}:
            warnings.append(
                f"quality: component {component_id!r} colorMaterialRecipe.colorGradient.type "
                f"must be 'linear' or 'radial'"
            )
        stops = gradient.get("stops") if isinstance(gradient, dict) else None
        if not isinstance(stops, list) or len(stops) < 2:
            warnings.append(
                f"quality: component {component_id!r} colorMaterialRecipe.colorGradient.stops "
                f"must have at least 2 entries"
            )
        elif any(not is_rgba_string(stop.get("color")) for stop in stops if isinstance(stop, dict)):
            warnings.append(
                f"quality: component {component_id!r} colorMaterialRecipe.colorGradient.stops[].color "
                f"must be 'rgba(r, g, b, a)' strings"
            )


def validate_components(
    spec: dict[str, Any],
    material_ids: set[str],
    evidence_ids: set[str],
    errors: list[str],
    warnings: list[str],
) -> None:
    components = spec.get("componentTree", [])
    materials_by_id = {
        material.get("id"): material
        for material in spec.get("materials", [])
        if isinstance(material, dict) and isinstance(material.get("id"), str)
    }
    ids: set[str] = set()
    parent_refs: list[tuple[str, str]] = []
    proud_refs: list[tuple[str, str]] = []
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            errors.append(f"componentTree[{index}] must be an object")
            continue
        component_id = component.get("id")
        if not isinstance(component_id, str) or not component_id.strip():
            errors.append(f"componentTree[{index}].id is required")
            continue
        if component_id in ids:
            errors.append(f"duplicate component id {component_id!r}")
        ids.add(component_id)
        primitive = component.get("primitive")
        if primitive not in VALID_PRIMITIVES:
            errors.append(
                f"component {component_id!r} primitive must be one of: {', '.join(sorted(VALID_PRIMITIVES))}"
            )
        # Plan 1.5: recessed-feature gate runs unconditionally (not schema-version gated like
        # topologyClass below) -- a flat patch faking a socket is a defect at any schema version.
        validate_recessed_feature_topology(component_id, component, errors)
        if requires_topology_classification(spec):
            topology_class = component.get("topologyClass")
            topology_rationale = component.get("topologyRationale")
            if topology_class not in VALID_TOPOLOGY_CLASSES:
                warnings.append(
                    f"quality: component {component_id!r} missing or invalid topologyClass "
                    f"(must be one of: {', '.join(sorted(VALID_TOPOLOGY_CLASSES))})"
                )
            else:
                if not isinstance(topology_rationale, str) or not topology_rationale.strip():
                    warnings.append(
                        f"quality: component {component_id!r} topologyRationale is required alongside topologyClass"
                    )
                else:
                    normalized_rationale = re.sub(r"[\s_-]+", "", topology_rationale.strip().lower())
                    normalized_class = re.sub(r"[\s_-]+", "", topology_class.lower())
                    if normalized_rationale == normalized_class:
                        warnings.append(
                            f"quality: component {component_id!r} topologyRationale restates the enum "
                            f"value instead of citing visible evidence"
                        )
                disallowed = DISALLOWED_TOPOLOGY_PRIMITIVE_PAIRS.get(topology_class, set())
                if primitive in disallowed:
                    hint = TOPOLOGY_ALLOWED_HINT.get(topology_class, "an allowed primitive")
                    warnings.append(
                        f"quality: component {component_id!r} pairs topologyClass={topology_class!r} "
                        f"with disallowed primitive {primitive!r} (use {hint} instead)"
                    )
                # Plan 1.3 Workstream C: gated on topologyClass (already required above),
                # not colorMaterialRecipe.materialClass — that field only exists once the
                # recipe is present, so using it as its own gate would be circular.
                if topology_class != "material-only":
                    validate_color_material_recipe(component_id, component.get("colorMaterialRecipe"), warnings)
                # Plan 1.3 G.1: flatness pre-check — catch a flat extrude faking a curved
                # 3D form BEFORE any render (the karambit-blade defect: a thin extrude of a
                # curved silhouette only looks right from the reference camera angle).
                if primitive == "extrude" and topology_class == "continuous-sculpt":
                    severity, message = flatness_risk(component_id, component)
                    if severity == "HIGH":
                        warnings.append(message)
                taper_severity, taper_message = taper_risk(component_id, component)
                if taper_severity == "HIGH":
                    warnings.append(taper_message)
                if topology_class == "implicit":
                    descriptor = component.get("geometryDescriptor")
                    if not isinstance(descriptor, dict) or "sdf" not in descriptor:
                        errors.append(f"component {component_id!r} topologyClass 'implicit' requires geometryDescriptor.sdf")
                if topology_class == "open-shell":
                    validate_open_shell_topology(component_id, component, materials_by_id, errors)
        level = component.get("level")
        if level is not None and level not in VALID_COMPONENT_LEVELS:
            errors.append(f"component {component_id!r} level must be macro, meso, or micro")
        for field in ("importance", "confidence"):
            value = component.get(field)
            if value is not None:
                validate_unit_interval(value, f"component {component_id!r} {field}", errors)
        parent = component.get("parent")
        if parent:
            if not isinstance(parent, str):
                errors.append(f"component {component_id!r} parent must be a string or null")
            else:
                parent_refs.append((component_id, parent))
        material = component.get("material")
        if material and material not in material_ids:
            errors.append(f"component {component_id!r} references unknown material {material!r}")
        validate_geometry_descriptor(component_id, component.get("geometryDescriptor"), errors)
        validate_stand_proud(component_id, component, errors, warnings, proud_refs)
        # three's Material.side defaults to FrontSide, which culls backfaces, so a garment opening
        # renders as a HOLE rather than as the inside of the sleeve -- indistinguishable at a glance
        # from a garment that is simply too short, and the two want opposite fixes.
        errors.extend(check_open_boundary_sides(component_id, component))
        # The decision left open in docs/UPGRADE_PLAN.md since v1.2 -- "hair cards vs
        # tube-along-curve per lock" -- closed here by what this pipeline can actually emit.
        if str(component.get("role") or "").lower() == "hair":
            reason = REJECTED_HAIR_PRIMITIVES.get(str(primitive))
            if reason:
                errors.append(
                    f"component {component_id!r} has role 'hair' and may not use primitive "
                    f"{primitive!r}: {reason}"
                )
        validate_subdivision_budget(
            component_id,
            primitive,
            component.get("topologyClass"),
            component.get("geometryDescriptor"),
            component.get("attachment"),
            errors,
        )
        material_layers = component.get("materialLayers")
        if material_layers is not None:
            validate_string_array(material_layers, f"component {component_id!r} materialLayers", errors)
            if isinstance(material_layers, list):
                for material_layer in material_layers:
                    if material_layer not in material_ids:
                        errors.append(
                            f"component {component_id!r} materialLayers references unknown material {material_layer!r}"
                        )
        validate_dimensions(component_id, component.get("dimensions"), errors)
        transform = component.get("transform", {})
        if transform is not None and not isinstance(transform, dict):
            errors.append(f"component {component_id!r} transform must be an object")
        elif isinstance(transform, dict):
            for field in ("position", "rotation", "scale"):
                if field in transform and not as_number_list(transform[field], 3):
                    errors.append(f"component {component_id!r} transform.{field} must be [number, number, number]")
        validate_action_profile(component_id, component.get("actionProfile"), errors, warnings)
        validate_attachment(
            component_id,
            parent if isinstance(parent, str) else None,
            component.get("attachment"),
            component_requires_attachment(component),
            errors,
            warnings,
        )
        for field in ("deformations", "joints", "seams", "localFeatures"):
            value = component.get(field)
            if value is not None and not isinstance(value, list):
                errors.append(f"component {component_id!r} {field} must be an array")
        paint = component.get("vertexPaint")
        if paint is not None:
            try:
                normalize_vertex_paint(paint, f"component {component_id!r} vertexPaint")
            except VertexPaintError as error:
                errors.append(str(error))
            # Both write the geometry's `color` attribute, and the second one to run wins. A spec
            # declaring both is asking for two different colours on the same vertex and would get
            # whichever the emission order happens to apply last -- an ordering dependency no gate
            # can see, so it is refused at spec time instead.
            if component.get("rootTipGradient") is not None:
                errors.append(
                    f"component {component_id!r} declares both vertexPaint and rootTipGradient; "
                    "both write the vertex colour attribute, so only one may be used"
                )
        surface = component.get("surfaceDetail")
        if surface is not None:
            if not isinstance(surface, dict):
                errors.append(f"component {component_id!r} surfaceDetail must be an object")
            else:
                for field in ("macroRoughness", "microRoughness", "bumpAmplitude"):
                    if field in surface and not is_number(surface[field]):
                        errors.append(f"component {component_id!r} surfaceDetail.{field} must be numeric")
        evidence_refs = component.get("evidenceRefs")
        if evidence_refs is not None:
            validate_string_array(evidence_refs, f"component {component_id!r} evidenceRefs", errors)
            if isinstance(evidence_refs, list):
                for evidence_ref in evidence_refs:
                    if evidence_ids and evidence_ref not in evidence_ids:
                        errors.append(f"component {component_id!r} references missing evidence {evidence_ref!r}")
    for component_id, parent in parent_refs:
        if parent not in ids:
            errors.append(f"component {component_id!r} references missing parent {parent!r}")
    # Resolved after the loop, like parents: a component may legitimately stand proud of one that
    # appears later in the tree, and a forward reference is not a defect.
    for component_id, against in proud_refs:
        if against not in ids:
            errors.append(
                f"component {component_id!r} standProud references missing component {against!r}"
            )
    if not ids:
        errors.append("at least one component is required")
    if len(ids) == 1:
        warnings.append("only one component found; this is likely still blockout quality")


def validate_quality_targets(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    targets = spec.get("qualityTargets")
    if targets is None:
        warnings.append("missing qualityTargets; self-correction loop has no explicit fidelity bar")
        return
    if not isinstance(targets, dict):
        errors.append("qualityTargets must be an object")
        return
    target_fidelity = targets.get("targetFidelity")
    if target_fidelity is not None:
        validate_unit_interval(target_fidelity, "qualityTargets.targetFidelity", errors)
    for field in ("mustMatch", "niceToHave", "reviewViewpoints"):
        validate_string_array(targets.get(field), f"qualityTargets.{field}", errors)


def validate_quality_contract(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    contract = spec.get("qualityContract")
    if contract is None:
        warnings.append("quality: missing qualityContract; no explicit definition of done prevents shallow specs")
        return
    if not isinstance(contract, dict):
        errors.append("qualityContract must be an object")
        return
    quality_bar = contract.get("qualityBar")
    if quality_bar is not None and not isinstance(quality_bar, str):
        errors.append("qualityContract.qualityBar must be a string")
    if quality_bar in {None, "", "unassessed"}:
        warnings.append("quality: qualityContract.qualityBar is unassessed")
    validate_string_array(contract.get("definitionOfDone"), "qualityContract.definitionOfDone", errors)
    if isinstance(contract.get("definitionOfDone"), list) and not contract["definitionOfDone"]:
        warnings.append("quality: qualityContract.definitionOfDone is empty")
    minimums = contract.get("minimumSpecDepth")
    if not isinstance(minimums, dict):
        errors.append("qualityContract.minimumSpecDepth must be an object")
    else:
        for field in (
            "macroComponents",
            "mesoComponents",
            "microFeatureGroups",
            "materialLayers",
            "repetitionSystems",
            "reviewViewpoints",
        ):
            if field in minimums:
                validate_nonnegative_int(minimums[field], f"qualityContract.minimumSpecDepth.{field}", errors)
    feature_groups = contract.get("featureGroups")
    if not isinstance(feature_groups, list):
        errors.append("qualityContract.featureGroups must be an array")
    else:
        if len(feature_groups) < 3:
            warnings.append("quality: qualityContract.featureGroups has fewer than 3 groups; spec may miss important visual layers")
        for index, group in enumerate(feature_groups):
            if not isinstance(group, dict):
                errors.append(f"qualityContract.featureGroups[{index}] must be an object")
                continue
            for field in ("id", "name"):
                value = group.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"qualityContract.featureGroups[{index}].{field} is required")
            if "required" in group and not isinstance(group["required"], bool):
                errors.append(f"qualityContract.featureGroups[{index}].required must be boolean")
            validate_string_array(group.get("qualityCriteria"), f"qualityContract.featureGroups[{index}].qualityCriteria", errors)
            validate_string_array(group.get("evidenceRefs"), f"qualityContract.featureGroups[{index}].evidenceRefs", errors)
            validate_string_array(group.get("failureModes"), f"qualityContract.featureGroups[{index}].failureModes", errors)
            if group.get("required") is True and not group.get("qualityCriteria"):
                warnings.append(f"quality: required feature group {group.get('id', index)!r} has no qualityCriteria")
    for field in ("visualDeltaChecks", "antiShallowSpecRules", "mustNotDo"):
        validate_string_array(contract.get(field), f"qualityContract.{field}", errors)
        if isinstance(contract.get(field), list) and not contract[field]:
            warnings.append(f"quality: qualityContract.{field} is empty")


def validate_quality_depth(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    contract = spec.get("qualityContract")
    if not isinstance(contract, dict) or not isinstance(contract.get("minimumSpecDepth"), dict):
        return
    minimums = contract["minimumSpecDepth"]
    components = [item for item in spec.get("componentTree", []) if isinstance(item, dict)]
    level_counts = {
        "macroComponents": sum(1 for item in components if item.get("level") == "macro"),
        "mesoComponents": sum(1 for item in components if item.get("level") == "meso"),
        "microFeatureGroups": sum(
            len(item.get("localFeatures", []))
            for item in components
            if isinstance(item.get("localFeatures", []), list)
        ),
        "materialLayers": len([item for item in spec.get("materials", []) if isinstance(item, dict)]),
        "repetitionSystems": len([item for item in spec.get("repetitionSystems", []) if isinstance(item, dict)]),
        "reviewViewpoints": len(spec.get("qualityTargets", {}).get("reviewViewpoints", []))
        if isinstance(spec.get("qualityTargets"), dict)
        and isinstance(spec.get("qualityTargets", {}).get("reviewViewpoints"), list)
        else 0,
    }
    for field, actual in level_counts.items():
        required = minimums.get(field)
        if isinstance(required, int) and actual < required:
            warnings.append(f"quality: {field} below qualityContract minimum ({actual} < {required})")


def validate_action_readiness(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    readiness = spec.get("actionReadiness")
    if readiness is None:
        warnings.append("missing actionReadiness; generated model may not be ready for animation/transformation/destruction")
        return
    if not isinstance(readiness, dict):
        errors.append("actionReadiness must be an object")
        return
    for field in ("contract", "defaultRigType", "rootMotionNode"):
        value = readiness.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"actionReadiness.{field} must be a string")
    for field in ("requiredComponentFields", "transformChannels", "authoringRules"):
        validate_string_array(readiness.get(field), f"actionReadiness.{field}", errors)
    policy = readiness.get("destructionPolicy")
    if policy is not None and not isinstance(policy, dict):
        errors.append("actionReadiness.destructionPolicy must be an object")


def validate_self_correct_loop(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    loop = spec.get("selfCorrectLoop")
    if loop is None:
        warnings.append("missing selfCorrectLoop; construction may not review/refine after each pass")
        return
    if not isinstance(loop, dict):
        errors.append("selfCorrectLoop must be an object")
        return
    enabled = loop.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        errors.append("selfCorrectLoop.enabled must be boolean")
    for field in ("reviewAfterPasses", "allowedActions", "specRefineTriggers", "codeRefineTriggers", "stopCriteria"):
        validate_string_array(loop.get(field), f"selfCorrectLoop.{field}", errors)
    actions = loop.get("allowedActions", [])
    if isinstance(actions, list):
        for action in actions:
            if action not in VALID_REVIEW_ACTIONS:
                errors.append(f"selfCorrectLoop.allowedActions contains invalid action {action!r}")
    visual_acceptance = loop.get("visualAcceptance")
    if visual_acceptance is None:
        warnings.append("quality: selfCorrectLoop.visualAcceptance is missing; AI vision cannot enforce visual fidelity")
    elif not isinstance(visual_acceptance, dict):
        errors.append("selfCorrectLoop.visualAcceptance must be an object")
    else:
        reviewer = visual_acceptance.get("reviewer")
        if reviewer is not None and not isinstance(reviewer, str):
            errors.append("selfCorrectLoop.visualAcceptance.reviewer must be a string")
        threshold = visual_acceptance.get("threshold")
        if threshold is None:
            warnings.append("quality: selfCorrectLoop.visualAcceptance.threshold is missing")
        else:
            validate_unit_interval(threshold, "selfCorrectLoop.visualAcceptance.threshold", errors)
        for field in (
            "comparisonArtifactRequired",
            "layerScoresRequired",
            "codePixelDiffIsAcceptanceAuthority",
        ):
            value = visual_acceptance.get(field)
            if value is not None and not isinstance(value, bool):
                errors.append(f"selfCorrectLoop.visualAcceptance.{field} must be boolean")
        scoring_rule = visual_acceptance.get("scoringRule")
        if scoring_rule is not None and not isinstance(scoring_rule, str):
            errors.append("selfCorrectLoop.visualAcceptance.scoringRule must be a string")
        validate_string_array(
            visual_acceptance.get("requiredLayerScores"),
            "selfCorrectLoop.visualAcceptance.requiredLayerScores",
            errors,
        )
        feature_policy = visual_acceptance.get("featureReviewPolicy")
        if feature_policy is None:
            warnings.append("quality: visualAcceptance.featureReviewPolicy is missing")
        elif not isinstance(feature_policy, dict):
            errors.append("selfCorrectLoop.visualAcceptance.featureReviewPolicy must be an object")
        else:
            for field in (
                "enabled",
                "adaptiveEscalation",
                "singleImagePairOnly",
            ):
                value = feature_policy.get(field)
                if value is not None and not isinstance(value, bool):
                    errors.append(
                        f"selfCorrectLoop.visualAcceptance.featureReviewPolicy.{field} must be boolean"
                    )
            for field in ("maxCriticalFeaturesPerPass", "maxImportantFeaturesPerPass"):
                value = feature_policy.get(field)
                if value is not None:
                    validate_nonnegative_int(
                        value,
                        f"selfCorrectLoop.visualAcceptance.featureReviewPolicy.{field}",
                        errors,
                    )
            for field in ("criticalDefaultThreshold", "importantAverageThreshold"):
                value = feature_policy.get(field)
                if value is not None:
                    validate_unit_interval(
                        value,
                        f"selfCorrectLoop.visualAcceptance.featureReviewPolicy.{field}",
                        errors,
                    )
            for field in ("reviewUnit", "selectionRule"):
                value = feature_policy.get(field)
                if value is not None and not isinstance(value, str):
                    errors.append(
                        f"selfCorrectLoop.visualAcceptance.featureReviewPolicy.{field} must be a string"
                    )
    policy = loop.get("screenshotPolicy")
    if policy is None:
        warnings.append("selfCorrectLoop.screenshotPolicy is missing; visual review may drift without screenshots")
    elif not isinstance(policy, dict):
        errors.append("selfCorrectLoop.screenshotPolicy must be an object")
    else:
        validate_string_array(policy.get("requiredForPasses"), "selfCorrectLoop.screenshotPolicy.requiredForPasses", errors)
        for field in (
            "preferredCapture",
            "fallbackCapture",
            "minimumEvidence",
            "reviewPairRule",
            "acceptanceAuthority",
        ):
            value = policy.get(field)
            if value is not None and not isinstance(value, str):
                errors.append(f"selfCorrectLoop.screenshotPolicy.{field} must be a string")


def validate_visual_evidence_item(item: Any, label: str, errors: list[str]) -> None:
    if not isinstance(item, dict):
        errors.append(f"{label} must be an object")
        return
    for field in (
        "passId",
        "referenceScreenshot",
        "renderScreenshot",
        "comparisonImage",
        "cameraView",
        "notes",
        "aiVisionNotes",
    ):
        value = item.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"{label}.{field} must be a string")
    fidelity = item.get("estimatedFidelity")
    if fidelity is not None:
        validate_unit_interval(fidelity, f"{label}.estimatedFidelity", errors)
    score = item.get("aiVisionScore")
    if score is not None:
        validate_unit_interval(score, f"{label}.aiVisionScore", errors)
    threshold = item.get("visualAcceptanceThreshold")
    if threshold is not None:
        validate_unit_interval(threshold, f"{label}.visualAcceptanceThreshold", errors)
    layer_scores = item.get("layerScores")
    if layer_scores is not None:
        if not isinstance(layer_scores, dict):
            errors.append(f"{label}.layerScores must be an object")
        else:
            for key, value in layer_scores.items():
                if not isinstance(key, str):
                    errors.append(f"{label}.layerScores keys must be strings")
                if not is_number(value) or value < 0 or value > 1:
                    errors.append(f"{label}.layerScores.{key} must be a number from 0 to 1")


def validate_feature_review_targets(
    spec: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> None:
    targets = spec.get("featureReviewTargets")
    policy = feature_review_policy(spec)
    if targets is None:
        if policy.get("enabled") is True:
            errors.append("featureReviewTargets must be an array when feature review is enabled")
        else:
            warnings.append("quality: featureReviewTargets is missing; feature-level visual gating is disabled")
        return
    if not isinstance(targets, list):
        errors.append("featureReviewTargets must be an array")
        return
    if not targets:
        warnings.append("quality: featureReviewTargets is empty; component-level visual gaps can hide in the overall score")
        return
    ids: set[str] = set()
    critical_by_pass: dict[str, int] = {}
    important_by_pass: dict[str, int] = {}
    for index, target in enumerate(targets):
        label = f"featureReviewTargets[{index}]"
        if not isinstance(target, dict):
            errors.append(f"{label} must be an object")
            continue
        target_id = target.get("id")
        if not isinstance(target_id, str) or not target_id.strip():
            errors.append(f"{label}.id is required")
        elif target_id in ids:
            errors.append(f"duplicate feature review target id {target_id!r}")
        else:
            ids.add(target_id)
        if not isinstance(target.get("name"), str) or not target["name"].strip():
            errors.append(f"{label}.name is required")
        tier = target.get("tier")
        if tier not in {"critical", "important", "detail"}:
            errors.append(f"{label}.tier must be critical, important, or detail")
        validate_string_array(target.get("passIds"), f"{label}.passIds", errors)
        validate_string_array(target.get("componentRefs"), f"{label}.componentRefs", errors)
        validate_string_array(target.get("evidenceRefs"), f"{label}.evidenceRefs", errors)
        minimum = target.get("minimumScore")
        if minimum is not None:
            validate_unit_interval(minimum, f"{label}.minimumScore", errors)
        for field in ("mustPass",):
            value = target.get(field)
            if value is not None and not isinstance(value, bool):
                errors.append(f"{label}.{field} must be boolean")
        if tier == "critical" or target.get("mustPass") is True:
            pass_ids = target.get("passIds", [])
            if isinstance(pass_ids, list):
                for pass_id in pass_ids:
                    if isinstance(pass_id, str):
                        critical_by_pass[pass_id] = critical_by_pass.get(pass_id, 0) + 1
        elif tier == "important":
            pass_ids = target.get("passIds", [])
            if isinstance(pass_ids, list):
                for pass_id in pass_ids:
                    if isinstance(pass_id, str):
                        important_by_pass[pass_id] = important_by_pass.get(pass_id, 0) + 1
    maximum = policy.get("maxCriticalFeaturesPerPass", 5)
    if is_number(maximum):
        for pass_id, count in critical_by_pass.items():
            if count > int(maximum):
                errors.append(
                    f"pass {pass_id!r} has {count} critical feature targets; "
                    f"maximum is {int(maximum)}"
                )
    important_maximum = policy.get("maxImportantFeaturesPerPass", 3)
    if is_number(important_maximum):
        for pass_id, count in important_by_pass.items():
            if count > int(important_maximum):
                errors.append(
                    f"pass {pass_id!r} has {count} important feature targets; "
                    f"maximum is {int(important_maximum)}"
                )
    assessment = spec.get("preSpecAssessment")
    complexity = (
        assessment.get("complexity", {}).get("tier")
        if isinstance(assessment, dict) and isinstance(assessment.get("complexity"), dict)
        else None
    )
    starter_ids = {
        "overall-silhouette",
        "primary-structure",
        "reference-material-system",
    }
    if complexity in {"moderate", "complex", "ultra-complex"} and ids.issubset(starter_ids):
        warnings.append(
            "quality: replace generic starter featureReviewTargets with object-specific "
            "identity-defining semantic systems before strict validation"
        )


def validate_feature_reviews(
    entry: dict[str, Any],
    label: str,
    errors: list[str],
) -> None:
    reviews = entry.get("featureReviews")
    if reviews is None:
        return
    if not isinstance(reviews, list):
        errors.append(f"{label}.featureReviews must be an array")
        return
    ids: set[str] = set()
    for index, review in enumerate(reviews):
        item_label = f"{label}.featureReviews[{index}]"
        if not isinstance(review, dict):
            errors.append(f"{item_label} must be an object")
            continue
        feature_id = review.get("id")
        if not isinstance(feature_id, str) or not feature_id.strip():
            errors.append(f"{item_label}.id is required")
        elif feature_id in ids:
            errors.append(f"{label}.featureReviews has duplicate id {feature_id!r}")
        else:
            ids.add(feature_id)
        score = review.get("score")
        if score is not None:
            validate_unit_interval(score, f"{item_label}.score", errors)
        for field in ("notes",):
            value = review.get(field)
            if value is not None and not isinstance(value, str):
                errors.append(f"{item_label}.{field} must be a string")
        visible = review.get("visible")
        if visible is not None and not isinstance(visible, bool):
            errors.append(f"{item_label}.visible must be boolean")


def validate_review_history(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    history = spec.get("reviewHistory", [])
    if history is None:
        return
    if not isinstance(history, list):
        errors.append("reviewHistory must be an array")
        return
    for index, entry in enumerate(history):
        if not isinstance(entry, dict):
            errors.append(f"reviewHistory[{index}] must be an object")
            continue
        action = entry.get("action")
        if action is not None and action not in VALID_REVIEW_ACTIONS:
            errors.append(f"reviewHistory[{index}].action is invalid")
        fidelity = entry.get("estimatedFidelity")
        if fidelity is not None:
            validate_unit_interval(fidelity, f"reviewHistory[{index}].estimatedFidelity", errors)
        for field in ("matched", "mismatches", "specFixes", "codeFixes", "evidence"):
            validate_string_array(entry.get(field), f"reviewHistory[{index}].{field}", errors)
        visual = entry.get("visualEvidence")
        if visual is not None:
            validate_visual_evidence_item(visual, f"reviewHistory[{index}].visualEvidence", errors)
        validate_feature_reviews(entry, f"reviewHistory[{index}]", errors)
        pass_id = entry.get("passId")
        if (
            pass_id in VISUAL_PASS_IDS
            and action == "continue"
            and not (isinstance(visual, dict) and visual.get("renderScreenshot"))
        ):
            warnings.append(
                f"reviewHistory[{index}] continues visual pass {pass_id!r} without a render screenshot"
            )
        if pass_id in VISUAL_PASS_IDS and action == "continue":
            if not isinstance(visual, dict) or not visual.get("comparisonImage"):
                warnings.append(
                    f"quality: reviewHistory[{index}] continues visual pass {pass_id!r} without an AI vision comparison image"
                )
            score = entry.get("aiVisionScore")
            threshold = entry.get("visualAcceptanceThreshold", 0.7)
            if not is_number(score):
                warnings.append(
                    f"quality: reviewHistory[{index}] continues visual pass {pass_id!r} without aiVisionScore"
                )
            elif is_number(threshold) and float(score) < float(threshold):
                warnings.append(
                    f"quality: reviewHistory[{index}] aiVisionScore {score} is below threshold {threshold}"
                )
            loop = spec.get("selfCorrectLoop")
            acceptance = loop.get("visualAcceptance", {}) if isinstance(loop, dict) else {}
            if isinstance(acceptance, dict) and acceptance.get("layerScoresRequired") is True:
                layer_scores = entry.get("layerScores")
                if not isinstance(layer_scores, dict) or not layer_scores:
                    warnings.append(
                        f"quality: reviewHistory[{index}] continues visual pass {pass_id!r} without layerScores"
                    )
                else:
                    required_layers = acceptance.get("requiredLayerScores", [])
                    if isinstance(required_layers, list):
                        missing_layers = [
                            layer
                            for layer in required_layers
                            if isinstance(layer, str) and layer not in layer_scores
                        ]
                        if missing_layers:
                            warnings.append(
                                f"quality: reviewHistory[{index}] layerScores missing: "
                                + ", ".join(missing_layers)
                            )
            failures = feature_gate_failures(spec, entry, str(pass_id))
            for failure in failures:
                warnings.append(
                    f"quality: reviewHistory[{index}] feature gate failed: {failure}"
                )


def validate_visual_evidence_history(spec: dict[str, Any], errors: list[str]) -> None:
    visual_history = spec.get("visualEvidence", [])
    if visual_history is None:
        return
    if not isinstance(visual_history, list):
        errors.append("visualEvidence must be an array")
        return
    for index, item in enumerate(visual_history):
        validate_visual_evidence_item(item, f"visualEvidence[{index}]", errors)


def validate_build_passes(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> list[str]:
    build_passes = spec.get("buildPasses")
    if build_passes is None:
        warnings.append("quality: missing buildPasses; model construction can skip blockout/structural/material gates")
        return []
    if not isinstance(build_passes, list):
        errors.append("buildPasses must be an array")
        return []
    ids: list[str] = []
    for index, item in enumerate(build_passes):
        if not isinstance(item, dict):
            errors.append(f"buildPasses[{index}] must be an object")
            continue
        pass_id = item.get("id")
        if not isinstance(pass_id, str) or not pass_id.strip():
            errors.append(f"buildPasses[{index}].id is required")
            continue
        if pass_id in ids:
            errors.append(f"duplicate buildPasses id {pass_id!r}")
        ids.append(pass_id)
        for field in ("goal",):
            value = item.get(field)
            if value is not None and not isinstance(value, str):
                errors.append(f"buildPasses[{index}].{field} must be a string")
        validate_string_array(item.get("componentRefs"), f"buildPasses[{index}].componentRefs", errors)
        validate_string_array(item.get("acceptance"), f"buildPasses[{index}].acceptance", errors)
    if ids:
        if ids[0] != "blockout":
            warnings.append("quality: first build pass should be blockout")
        if "structural-pass" not in ids:
            warnings.append("quality: missing structural-pass; component hierarchy may be skipped")
        if not ({"material-pass", "surface-pass"} & set(ids)):
            warnings.append("quality: missing material/surface pass; model may stay as flat geometry")
    return ids


def review_completes_pass(
    spec: dict[str, Any],
    entry: dict[str, Any],
    pass_id: str,
) -> bool:
    if entry.get("passId") != pass_id or entry.get("action") != "continue":
        return False
    visual = entry.get("visualEvidence")
    if pass_id in VISUAL_PASS_IDS:
        if not (
            isinstance(visual, dict)
            and visual.get("renderScreenshot")
            and visual.get("comparisonImage")
        ):
            return False
        score = entry.get("aiVisionScore")
        threshold = entry.get("visualAcceptanceThreshold", 0.7)
        if not is_number(score) or not is_number(threshold) or float(score) < float(threshold):
            return False
        if feature_gate_failures(spec, entry, pass_id):
            return False
    return True


def completed_passes_from_history(spec: dict[str, Any], pass_ids: list[str]) -> list[str]:
    history = spec.get("reviewHistory", [])
    if not isinstance(history, list):
        return []
    completed: list[str] = []
    for pass_id in pass_ids:
        if any(
            isinstance(entry, dict) and review_completes_pass(spec, entry, pass_id)
            for entry in history
        ):
            completed.append(pass_id)
        else:
            break
    return completed


def validate_sculpt_pipeline(
    spec: dict[str, Any],
    build_pass_ids: list[str],
    errors: list[str],
    warnings: list[str],
) -> None:
    pipeline = spec.get("sculptPipeline")
    if pipeline is None:
        warnings.append("quality: missing sculptPipeline; pass order is not locked and generation can skip build passes")
        return
    if not isinstance(pipeline, dict):
        errors.append("sculptPipeline must be an object")
        return
    pass_order = pipeline.get("passOrder")
    if pass_order is None:
        warnings.append("quality: sculptPipeline.passOrder is missing")
        pass_order_ids = build_pass_ids
    else:
        validate_string_array(pass_order, "sculptPipeline.passOrder", errors)
        pass_order_ids = [str(value) for value in pass_order] if isinstance(pass_order, list) else build_pass_ids
    if build_pass_ids and pass_order_ids and pass_order_ids != build_pass_ids:
        warnings.append("sculptPipeline.passOrder differs from buildPasses order; sync the pipeline before generation")
    current = pipeline.get("currentPass")
    if current is not None and current != "complete" and current not in (pass_order_ids or build_pass_ids):
        errors.append("sculptPipeline.currentPass must be a known build pass or complete")
    completed = pipeline.get("completedPasses", [])
    validate_string_array(completed, "sculptPipeline.completedPasses", errors)
    if isinstance(completed, list):
        expected = completed_passes_from_history(spec, pass_order_ids or build_pass_ids)
        if list(completed) != expected:
            warnings.append("sculptPipeline.completedPasses is out of sync with reviewHistory; run stage3_build/orchestrate_passes.py sync")
        for pass_id in completed:
            if pass_id not in (pass_order_ids or build_pass_ids):
                errors.append(f"sculptPipeline.completedPasses contains unknown pass {pass_id!r}")
    gate_mode = pipeline.get("passGateMode")
    if gate_mode != "locked-sequential":
        warnings.append("quality: sculptPipeline.passGateMode should be locked-sequential")
    validate_string_array(pipeline.get("nextRequiredEvidence"), "sculptPipeline.nextRequiredEvidence", errors)


def has_non_empty_detail(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() not in {"none", "unassessed", "n/a"}
    if isinstance(value, list):
        return any(has_non_empty_detail(item) for item in value)
    if isinstance(value, dict):
        return any(has_non_empty_detail(item) for item in value.values())
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return abs(float(value)) > 0
    return False


def layer_number(value: Any, keys: tuple[str, ...]) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                return float(item)
    return 0.0


def reference_pbr_usable(material: dict[str, Any], threshold: float) -> tuple[bool, str]:
    reference = material.get("referencePbr")
    material_id = str(material.get("id") or "(unnamed)")
    if not isinstance(reference, dict):
        return False, f"material {material_id!r} needs usable referencePbr extracted from source pixels"
    if reference.get("usable") is not True:
        return False, f"material {material_id!r} referencePbr.usable must be true"
    confidence = reference.get("confidence", reference.get("estimatedFidelity"))
    if not is_number(confidence) or float(confidence) < threshold:
        return False, f"material {material_id!r} referencePbr confidence must be >= {threshold}"
    maps = reference.get("maps")
    if not isinstance(maps, dict):
        return False, f"material {material_id!r} referencePbr needs maps"
    for channel in ("albedo", "roughness", "height", "normal", "ao"):
        entry = maps.get(channel)
        if not isinstance(entry, dict) or not has_non_empty_detail(entry.get("url") or entry.get("path")):
            return False, f"material {material_id!r} referencePbr missing {channel} map path/url"
    return True, ""


TEXTURE_AUTHORING_FIELDS = (
    "normal",
    "bump",
    "displacement",
    "surfaceFrequencyBands",
    "textureProjection",
    "textureResolution",
    "referencePbr",
)


def is_textureless(material: dict[str, Any]) -> bool:
    declaration = material.get("textureless")
    return isinstance(declaration, dict) and declaration.get("declared") is True


def validate_textureless(material_id: str, material: dict[str, Any], errors: list[str]) -> None:
    """A material may declare that its subject carries NO texture detail — with evidence.

    WHY THIS EXISTS. The quality-first material bar below requires independent albedo, roughness,
    height, normal and AO channels at >= 1024, because the subjects it was written for are
    photographic and their identity lives in surface detail. Some subjects genuinely have none:
    a flat-shaded designer-toy render carries no grain, no print and no pores, and its identity is
    entirely silhouette, proportion and the boundaries between flat colour regions.

    Without this declaration the only ways past that bar are to fabricate a texture resolution and
    frequency bands the renderer will never read, or to mislabel a hero material `qualityTier:
    "utility"`. Both put numbers in the spec that describe nothing — which is worse than a missing
    value, because they read as authored evidence.

    So the escape is explicit, evidence-bearing, and closed on both sides: a textureless material
    must say what measurement supports the claim, and must NOT then carry any texture-authoring
    field, since a material cannot both have no texture and specify one.
    """
    declaration = material.get("textureless")
    if declaration is None:
        return
    if not isinstance(declaration, dict):
        errors.append(f"material {material_id!r} textureless must be an object")
        return
    if declaration.get("declared") is not True:
        errors.append(
            f"material {material_id!r} textureless.declared must be true; remove the block instead "
            "of declaring it false"
        )
        return
    evidence = declaration.get("evidence")
    if not isinstance(evidence, list) or not [
        item for item in evidence if isinstance(item, str) and item.strip()
    ]:
        errors.append(
            f"material {material_id!r} textureless.evidence must name at least one measurement or "
            "reference view supporting the claim that the subject carries no texture detail"
        )
    for field in TEXTURE_AUTHORING_FIELDS:
        if material.get(field) is not None:
            errors.append(
                f"material {material_id!r} declares textureless but also carries {field!r}; a "
                "material cannot both have no texture and specify one"
            )


def validate_look_dev_targets(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    targets = spec.get("lookDevTargets")
    if targets is None:
        warnings.append("quality: missing lookDevTargets; material/color/lighting passes may stay flat")
    elif not isinstance(targets, dict):
        errors.append("lookDevTargets must be an object")
    materials = [item for item in spec.get("materials", []) if isinstance(item, dict)]
    if materials:
        has_palette = any(
            has_non_empty_detail(item.get("colorVariation"))
            or has_non_empty_detail(item.get("albedo", {}).get("secondary") if isinstance(item.get("albedo"), dict) else None)
            for item in materials
        )
        has_response = any(
            layer_number(item.get("roughness"), ("variation", "base")) > 0
            or layer_number(item.get("normal"), ("strength", "amplitude")) > 0
            or layer_number(item.get("bump"), ("amplitude", "strength")) > 0
            or layer_number(item.get("displacement"), ("amplitude", "strength")) > 0
            for item in materials
        )
        has_locality = any(
            has_non_empty_detail(item.get("localOverrides"))
            or (
                isinstance(item.get("wear"), dict)
                and (
                    layer_number(item["wear"].get("edgeWear"), ("base", "amount")) > 0
                    or has_non_empty_detail(item["wear"].get("scratches"))
                    or has_non_empty_detail(item["wear"].get("chips"))
                )
            )
            or (
                isinstance(item.get("dirt"), dict)
                and (
                    layer_number(item["dirt"].get("amount"), ("base", "amount")) > 0
                    or layer_number(item["dirt"].get("cavityBias"), ("base", "amount")) > 0
                )
            )
            or has_non_empty_detail(item.get("moss"))
            or has_non_empty_detail(item.get("stains"))
            or has_non_empty_detail(item.get("scratches"))
            or has_non_empty_detail(item.get("chips"))
            or has_non_empty_detail(item.get("wetness"))
            or has_non_empty_detail(item.get("patina"))
            for item in materials
        )
        if not has_palette:
            warnings.append("quality: material-pass needs reference-derived albedo palette or secondary/accent color zones")
        if not has_response:
            warnings.append("quality: material-pass needs roughness variation or normal/bump/displacement response")
        if not has_locality:
            warnings.append("quality: material-pass needs local overrides, AO, dirt, wear, stains, moss, chips, scratches, or equivalent masks")
        quality_first = isinstance(targets, dict) and targets.get("qualityPriority") == "reference-fidelity"
        if quality_first:
            material_targets = targets.get("materialPass", {})
            if not isinstance(material_targets, dict):
                warnings.append("quality: quality-first lookDevTargets.materialPass must be an object")
                material_targets = {}
            minimum_resolution = material_targets.get("minimumTextureResolution", 1024)
            if not isinstance(minimum_resolution, int) or isinstance(minimum_resolution, bool):
                warnings.append("quality: minimumTextureResolution must be an integer")
                minimum_resolution = 1024
            required_channels = {
                str(item).lower()
                for item in material_targets.get("independentMapChannels", [])
                if isinstance(item, str)
            }
            extraction_targets = material_targets.get("referencePbrExtraction", {})
            if not isinstance(extraction_targets, dict):
                extraction_targets = {}
            pbr_required = (
                extraction_targets.get("requiredWhenSourceImagePresent") is True
                and has_non_empty_detail(spec.get("sourceImage"))
            )
            pbr_threshold = extraction_targets.get("targetThreshold", 0.7)
            if not is_number(pbr_threshold):
                pbr_threshold = 0.7
            expected_channels = {"albedo", "roughness", "height", "normal", "ambient-occlusion"}
            if not expected_channels.issubset(required_channels):
                warnings.append(
                    "quality: quality-first materialPass must require independent albedo, roughness, "
                    "height, normal, and ambient-occlusion channels"
                )
            for material in materials:
                if material.get("qualityTier") == "utility":
                    continue
                # A material that declares -- with evidence -- that its subject carries no texture
                # detail is not held to the texture-channel bar. See validate_textureless: the
                # declaration is hard-validated, so this skip cannot be taken without the evidence
                # and cannot coexist with any texture-authoring field.
                if is_textureless(material):
                    continue
                material_id = str(material.get("id") or "(unnamed)")
                resolution = material.get("textureResolution")
                if not isinstance(resolution, int) or isinstance(resolution, bool) or resolution < minimum_resolution:
                    warnings.append(
                        f"quality: material {material_id!r} textureResolution must be >= {minimum_resolution}"
                    )
                projection = material.get("textureProjection")
                if not isinstance(projection, dict) or not has_non_empty_detail(projection.get("mode")):
                    warnings.append(
                        f"quality: material {material_id!r} needs textureProjection.mode and texel-density intent"
                    )
                bands = material.get("surfaceFrequencyBands")
                band_ids = {
                    str(item.get("id")).lower()
                    for item in bands
                    if isinstance(item, dict) and has_non_empty_detail(item.get("id"))
                } if isinstance(bands, list) else set()
                missing_bands = {"macro", "meso", "micro"} - band_ids
                if missing_bands:
                    warnings.append(
                        f"quality: material {material_id!r} missing surface frequency bands: "
                        + ", ".join(sorted(missing_bands))
                    )
                roughness = material.get("roughness")
                roughness_map = roughness.get("map") if isinstance(roughness, dict) else None
                if not has_non_empty_detail(roughness_map) or "albedo" in str(roughness_map).lower():
                    warnings.append(f"quality: material {material_id!r} needs an independent roughness map")
                if not has_non_empty_detail(material.get("ambientOcclusion")):
                    warnings.append(
                        f"quality: material {material_id!r} needs an independent ambient-occlusion response"
                    )
                if pbr_required:
                    ok, message = reference_pbr_usable(material, float(pbr_threshold))
                    if not ok:
                        warnings.append(f"quality: {message}")
    lighting = spec.get("lightingFromPhoto", [])
    if not isinstance(lighting, list):
        errors.append("lightingFromPhoto must be an array")
    else:
        meaningful = [item for item in lighting if has_non_empty_detail(item)]
        if len(meaningful) < 3:
            warnings.append("quality: lighting-pass needs concrete key/fill/rim or environment light entries")
        lighting_text = " ".join(str(item).lower() for item in meaningful)
        if meaningful and not any(term in lighting_text for term in ("exposure", "tone", "aces", "filmic")):
            warnings.append("quality: lighting-pass needs exposure and tone mapping intent")
        if meaningful and not any(term in lighting_text for term in ("contact shadow", "ground shadow", "ambient occlusion", "ao")):
            warnings.append("quality: lighting-pass needs contact shadow or ground shadow behavior")


VALID_DETAIL_KINDS = {
    "gloss", "bevel", "fastener", "linework", "contour", "seam", "stitch",
    "stain", "scratch", "chip", "decal", "emissive", "hole", "groove", "ridge",
}


def _detail_link_keys(spec: dict[str, Any]) -> set[str]:
    """Collect keys a detailInventory item may map to: component ids, local feature ids,
    material ids, and material localOverride ids (with and without owner prefix)."""
    keys: set[str] = set()
    for comp in spec.get("componentTree", []):
        if not isinstance(comp, dict):
            continue
        cid = comp.get("id")
        if isinstance(cid, str):
            keys.add(cid)
        for feat in comp.get("localFeatures", []) or []:
            if isinstance(feat, str):
                keys.add(feat)
                if isinstance(cid, str):
                    keys.add(f"{cid}/{feat}")
            elif isinstance(feat, dict) and isinstance(feat.get("id"), str):
                keys.add(feat["id"])
                if isinstance(cid, str):
                    keys.add(f"{cid}/{feat['id']}")
    for mat in spec.get("materials", []):
        if not isinstance(mat, dict):
            continue
        mid = mat.get("id")
        if isinstance(mid, str):
            keys.add(mid)
        for over in mat.get("localOverrides", []) or []:
            if isinstance(over, dict) and isinstance(over.get("id"), str):
                keys.add(over["id"])
                if isinstance(mid, str):
                    keys.add(f"{mid}/{over['id']}")
    return keys


def _has_gloss_response(spec: dict[str, Any]) -> bool:
    for mat in spec.get("materials", []):
        if not isinstance(mat, dict):
            continue
        rough = mat.get("roughness")
        base = rough.get("base") if isinstance(rough, dict) else rough
        if is_number(base) and float(base) < 0.35:
            return True
        if is_number(mat.get("clearcoat")) or isinstance(mat.get("clearcoat"), dict):
            return True
        for over in mat.get("localOverrides", []) or []:
            if isinstance(over, dict) and is_number(over.get("roughness")) and float(over["roughness"]) < 0.3:
                return True
    return False


def _has_repetition_or_small_parts(spec: dict[str, Any]) -> bool:
    for repetition in spec.get("repetitionSystems", []):
        if not isinstance(repetition, dict):
            continue
        if repetition.get("realization") == "map-only" or repetition.get("buildsGeometry") is False:
            continue
        if repetition.get("geometry") is not None or repetition.get("instances") is not None or repetition.get("buildsGeometry") is True:
            return True
    return any(
        isinstance(c, dict) and c.get("level") == "micro"
        for c in spec.get("componentTree", [])
    )


def validate_detail_inventory(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    """Gate the detail inventory. Backward compatible: only enforced when a detailInventory
    block with a positive targetMinDetails is present (new-pipeline specs)."""
    assessment = spec.get("preSpecAssessment")
    if not isinstance(assessment, dict):
        return
    inv = assessment.get("detailInventory")
    if not isinstance(inv, dict):
        return
    details = inv.get("details", [])
    if not isinstance(details, list):
        errors.append("preSpecAssessment.detailInventory.details must be an array")
        return
    target = inv.get("targetMinDetails", 0)
    if not (isinstance(target, int) and not isinstance(target, bool) and target > 0):
        return  # not a new-pipeline spec; skip enforcement
    if len(details) < target:
        warnings.append(
            f"quality: detailInventory has {len(details)} details but targetMinDetails is {target}; "
            "enumerate identity-defining details (gloss, bevel, fasteners, linework, stains) before code generation"
        )
    link_keys = _detail_link_keys(spec)
    has_gloss = has_fastener = False
    for index, detail in enumerate(details):
        if not isinstance(detail, dict):
            errors.append(f"detailInventory.details[{index}] must be an object")
            continue
        did = detail.get("id", index)
        kind = detail.get("kind")
        if kind not in VALID_DETAIL_KINDS:
            warnings.append(f"quality: detailInventory detail {did!r} has unknown kind {kind!r}")
        maps = detail.get("mapsTo")
        ref = maps.get("ref") if isinstance(maps, dict) else None
        if not (isinstance(ref, str) and ref in link_keys):
            warnings.append(
                f"quality: detailInventory detail {did!r} does not map to a component.localFeatures "
                "or material.localOverrides entry (no prose-only details)"
            )
        if kind == "gloss":
            has_gloss = True
        elif kind == "fastener":
            has_fastener = True
    if has_gloss and not _has_gloss_response(spec):
        warnings.append(
            "quality: detailInventory lists a gloss detail but no material provides low roughness or clearcoat response"
        )
    if has_fastener and not _has_repetition_or_small_parts(spec):
        warnings.append(
            "quality: detailInventory lists fastener details but no repetitionSystem/instancing or micro parts represent them"
        )


def validate_character_track(spec: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    """Gate the character track. Backward compatible: only enforced when primaryDomain is
    character or hybrid."""
    assessment = spec.get("preSpecAssessment")
    if not isinstance(assessment, dict):
        return
    object_class = assessment.get("objectClass")
    domain = object_class.get("primaryDomain") if isinstance(object_class, dict) else None
    if domain not in {"character", "hybrid"}:
        return
    anatomy = assessment.get("anatomy")
    if not isinstance(anatomy, dict) or anatomy.get("applies") is not True:
        warnings.append(
            "quality: primaryDomain is character/hybrid but anatomy.applies is not true; "
            "fill anatomy (styleHeads, proportions, pose, faceLandmarks) from the reference"
        )
        return
    if not (is_number(anatomy.get("styleHeads")) and float(anatomy["styleHeads"]) > 0):
        warnings.append("quality: character anatomy.styleHeads must be greater than 0 (head-unit proportion)")
    proportions = anatomy.get("proportions")
    if not (isinstance(proportions, dict) and any(
        is_number(proportions.get(k)) and float(proportions[k]) > 0 for k in ("torso", "legs")
    )):
        warnings.append("quality: character anatomy.proportions must set torso/legs head-unit ratios")
    landmarks = anatomy.get("faceLandmarks")
    if not (isinstance(landmarks, dict) and any(
        is_number(landmarks.get(k)) and float(landmarks[k]) > 0 for k in ("eyeLine", "noseBase", "mouthLine")
    )):
        warnings.append("quality: character anatomy.faceLandmarks must set eyeLine/noseBase/mouthLine from the reference")
    targets = spec.get("featureReviewTargets", [])
    character_ids = {"anatomy-proportion", "face-landmark-placement", "pose-silhouette", "outfit-and-palette"}
    if not any(isinstance(t, dict) and t.get("id") in character_ids for t in targets):
        warnings.append(
            "quality: character track needs featureReviewTargets covering anatomy/face/pose/outfit "
            "(add anatomy-proportion, face-landmark-placement, pose-silhouette, outfit-and-palette)"
        )


# PLAN_1.5 §5.2 Half A — the Joint Admission Gate. Pure semantics and arithmetic, which is why it
# folds into this file rather than becoming a new module: §5.2 says so explicitly, and warns that
# `forge/stage4_review/geometry_integrity.py` already owns that name. Half B
# (INSIDE_VOLUME / UNIFORM_BONE_SCALE / NO_PRE_ROTATION) needs real geometry and belongs to a Node
# script at stage 4, not here.
SYMMETRY_PARITY_TOLERANCE = 0.05
POOL_FLOOR_MIN_BONES = 4
# §5.2 states PROPORTION_LIMIT as "bone length against the head-unit template (e.g. femur <= 2.5
# HU)". READING CHOSEN: the rig carries no head unit — demanding `anatomy.proportions` would reject
# the default `--character` template, which has no anatomy block at all — so the limit is expressed
# as a fraction of the skeleton's own height. That is scale-free and needs no external input. On a
# 6.78-head figure the plan's 2.5 HU is 2.5/6.78 = 37% of height, so 0.40 sits just above it.
PROPORTION_LIMIT_FRACTION = 0.40


def _mirror_partner(bone_id: str) -> str | None:
    """`upper-arm-l` -> `upper-arm-r`, `thumb-l-1` -> `thumb-r-1`. None when not a left id.

    Digit ids carry the side in the MIDDLE (`thumb-l-1`), so matching only a trailing `-l` would
    silently skip all thirty phalanges — the majority of the skeleton.
    """
    if bone_id.endswith("-l"):
        return bone_id[:-2] + "-r"
    if "-l-" in bone_id:
        return bone_id.replace("-l-", "-r-", 1)
    return None


def validate_rig_admission(
    spec: dict[str, Any], errors: list[str], warnings: list[str]
) -> None:
    """The five Half-A checks. Runs only when a `rig` is present, so the pivot track is a no-op.

    SYMMETRY_PARITY **snaps** rather than rejects, per §5.2's "On fail" column — an asymmetric
    pair is a fixable authoring slip, not a broken skeleton. The other four reject.
    """
    rig = spec.get("rig")
    if not isinstance(rig, dict):
        return
    bones = [b for b in (rig.get("bones") or []) if isinstance(b, dict) and b.get("id")]
    if not bones:
        return
    by_id = {b["id"]: b for b in bones}

    def joint(bone: dict[str, Any]) -> list[float]:
        return [float(v) for v in (bone.get("jointPos") or [0.0, 0.0, 0.0])]

    def tip(bone: dict[str, Any]) -> list[float]:
        return [float(v) for v in (bone.get("tipPos") or [0.0, 0.0, 0.0])]

    def length(bone: dict[str, Any]) -> float:
        j, t = joint(bone), tip(bone)
        return sum((t[i] - j[i]) ** 2 for i in range(3)) ** 0.5

    # ---- NAME_UNIQUENESS ----
    ids = [b["id"] for b in bones]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        errors.append(f"NAME_UNIQUENESS: duplicate bone id(s) {duplicates}")
    roots = [b for b in bones if b.get("parent") in (None, "")]
    if len(roots) != 1:
        errors.append(
            f"NAME_UNIQUENESS: exactly one root bone required (parent: null), found {len(roots)}"
            + (f" ({sorted(b['id'] for b in roots)})" if roots else "")
        )
    for bone in bones:
        parent = bone.get("parent")
        if parent and parent not in by_id:
            errors.append(
                f"NAME_UNIQUENESS: bone {bone['id']!r} has unresolved parent {parent!r}"
            )

    # ---- POOL_FLOOR ----
    if len(bones) < POOL_FLOOR_MIN_BONES:
        errors.append(
            f"POOL_FLOOR: the skeleton resolves only {len(bones)} bone(s); the weight function "
            f"keeps four influences per vertex, so fewer than {POOL_FLOOR_MIN_BONES} leaves slots "
            f"structurally unfillable"
        )

    # ---- SYMMETRY_PARITY (snap, do not reject) ----
    for bone in bones:
        partner_id = _mirror_partner(bone["id"])
        if not partner_id or partner_id not in by_id:
            continue
        left, right = joint(bone), joint(by_id[partner_id])
        mirrored = [-left[0], left[1], left[2]]
        delta = max(abs(right[i] - mirrored[i]) for i in range(3))
        if delta > SYMMETRY_PARITY_TOLERANCE:
            by_id[partner_id]["jointPos"] = [round(v, 5) for v in mirrored]
            left_tip = tip(bone)
            by_id[partner_id]["tipPos"] = [round(v, 5) for v in
                                           (-left_tip[0], left_tip[1], left_tip[2])]
            warnings.append(
                f"SYMMETRY_PARITY: {partner_id!r} was {delta:.4f} off the mirror of "
                f"{bone['id']!r} (tolerance {SYMMETRY_PARITY_TOLERANCE}); snapped to the "
                f"mirrored coordinate rather than rejected"
            )

    # ---- MONOTONIC_CHAIN ----
    # §5.2 words this as "cumulative length along a limb chain must increase monotonically; no
    # bone may fold back through its parent". READING CHOSEN: the operative clause is the second.
    #
    # I first implemented the first clause as euclidean distance from the root joint to each tip,
    # and it REJECTED the correct 49-bone template on ten bones — which is the strongest possible
    # evidence that a check is mis-specified rather than the model being wrong. The reason is
    # anatomy: a clavicle reaches 0.70 up at the shoulder, then the upper arm hangs DOWN so its
    # tip lands at 0.49, back toward the hips. Distance-from-root is legitimately non-monotonic
    # for any chain that goes out then down. (Read as arc length the clause is trivially true,
    # since bone lengths are positive, so that cannot be the intent either.)
    #
    # So this compares DIRECTION. A bone folds back only when it points substantially opposite
    # its parent. The threshold is generous on purpose: a thumb sits near 90 degrees to the palm
    # and must pass, while a genuinely inverted bone sits near 180 and must not.
    MONOTONIC_CHAIN_OPPOSED_DOT = -0.5           # ~120 degrees apart

    def direction(bone: dict[str, Any]) -> list[float] | None:
        j, t = joint(bone), tip(bone)
        delta = [t[i] - j[i] for i in range(3)]
        norm = sum(d * d for d in delta) ** 0.5
        return [d / norm for d in delta] if norm > 1e-9 else None

    for bone in bones:
        parent_id = bone.get("parent")
        parent = by_id.get(parent_id) if parent_id else None
        if not parent or parent.get("chain") != bone.get("chain"):
            continue          # a limb leaving the spine is a branch, not a continuation
        child_dir, parent_dir = direction(bone), direction(parent)
        if not child_dir or not parent_dir:
            continue
        dot = sum(child_dir[i] * parent_dir[i] for i in range(3))
        if dot < MONOTONIC_CHAIN_OPPOSED_DOT:
            errors.append(
                f"MONOTONIC_CHAIN: bone {bone['id']!r} points {dot:.3f} against its parent "
                f"{parent_id!r} (limit {MONOTONIC_CHAIN_OPPOSED_DOT}) — it folds back through "
                f"its parent instead of extending the chain"
            )

    # ---- PROPORTION_LIMIT ----
    ys = [v for bone in bones for v in (joint(bone)[1], tip(bone)[1])]
    skeleton_height = max(ys) - min(ys) if ys else 0.0
    if skeleton_height > 0:
        limit = skeleton_height * PROPORTION_LIMIT_FRACTION
        for bone in bones:
            if length(bone) > limit:
                errors.append(
                    f"PROPORTION_LIMIT: bone {bone['id']!r} is {length(bone):.4f} long, over "
                    f"{PROPORTION_LIMIT_FRACTION:.0%} of the skeleton's {skeleton_height:.4f} "
                    f"height ({limit:.4f})"
                )


def validate_spec(spec: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for key, expected_type in REQUIRED_TOP_LEVEL.items():
        if key not in spec:
            errors.append(f"missing top-level field {key!r}")
        elif not isinstance(spec[key], expected_type):
            errors.append(f"field {key!r} must be {expected_type.__name__}")
    suitability = spec.get("suitability")
    if suitability not in VALID_SUITABILITY:
        errors.append("suitability must be pass, conditional, or reject")
    validate_pre_spec_assessment(spec, errors, warnings)
    validate_terminology_profile(spec, errors, warnings)
    validate_score_block(spec, errors, warnings)
    validate_quality_targets(spec, errors, warnings)
    validate_quality_contract(spec, errors, warnings)
    validate_action_readiness(spec, errors, warnings)
    validate_self_correct_loop(spec, errors, warnings)
    validate_feature_review_targets(spec, errors, warnings)
    validate_chirality(spec, errors, warnings)
    validate_hair_profile(spec.get("hairProfile"), errors, warnings)
    validate_review_history(spec, errors, warnings)
    validate_visual_evidence_history(spec, errors)
    build_pass_ids = validate_build_passes(spec, errors, warnings)
    validate_sculpt_pipeline(spec, build_pass_ids, errors, warnings)
    validate_look_dev_targets(spec, errors, warnings)
    evidence_ids = validate_evidence(spec, errors, warnings)
    material_ids = validate_materials(spec, errors, warnings)
    validate_material_pipeline_contract(spec, material_ids, errors, warnings)
    validate_cs2_contract(spec, errors, warnings)
    validate_pipeline_routing_contract(spec, errors)
    validate_cs2_view_dependent_environment(spec, errors)
    validate_components(spec, material_ids, evidence_ids, errors, warnings)
    lod_plan = spec.get("lodPlan")
    if lod_plan is not None and not isinstance(lod_plan, list):
        errors.append("lodPlan must be an array")
    performance = spec.get("performanceBudget")
    if performance is not None and not isinstance(performance, dict):
        errors.append("performanceBudget must be an object")
    validate_quality_depth(spec, errors, warnings)
    validate_detail_inventory(spec, errors, warnings)
    validate_character_track(spec, errors, warnings)
    validate_rig_admission(spec, errors, warnings)
    if suitability == "pass" and spec.get("risks"):
        warnings.append("suitability is pass but risks are present; confirm they are acceptable")
    return errors, warnings


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--json", action="store_true", help="Print machine-readable result")
    parser.add_argument(
        "--strict-quality",
        action="store_true",
        help="Treat quality warnings as validation errors before implementation/generation",
    )
    args = parser.parse_args(argv)

    try:
        spec = load_spec(args.spec)
        errors, warnings = validate_spec(spec)
    except ValueError as exc:
        errors, warnings = [str(exc)], []

    if args.strict_quality:
        errors.extend(
            f"strict quality failure: {warning.removeprefix('quality: ').strip()}"
            for warning in warnings
            if warning.startswith("quality:")
        )

    ok = not errors
    result = {
        "ok": ok,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "targetName": spec.get("targetName") if "spec" in locals() else None,
            "suitability": spec.get("suitability") if "spec" in locals() else None,
            "components": len(spec.get("componentTree", [])) if "spec" in locals() else 0,
            "materials": len(spec.get("materials", [])) if "spec" in locals() else 0,
        },
    }
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("PASS" if ok else "FAIL")
        for warning in warnings:
            print(f"warning: {warning}")
        for error in errors:
            print(f"error: {error}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
