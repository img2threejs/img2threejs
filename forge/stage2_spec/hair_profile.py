#!/usr/bin/env python3
"""The semantic description of a hairstyle: its schema and its validation rules.

WHAT THIS IS AND IS NOT, PRECISELY. This module defines and VALIDATES a `hairProfile`. It does not
compile one into geometry -- there is no profile-to-componentTree compiler yet, and a spec still
authors its hair components directly. Enforcement of the profile's rules today comes from the
validator here plus the `standProud` march the generator emits.

That distinction is stated first because getting it wrong is the specific way this subsystem is
likely to mislead someone: a schema is cheap to write and reads exactly like a working feature.

WHY A PROFILE AND NOT GEOMETRY. Everywhere else in this pipeline the model writes a spec and a
generator writes the geometry. Hair was the one subsystem where the model still placed vertices by
hand, and it showed: four separate attempts to fix one hairstyle by adjusting numbers, four wrong
diagnoses, and the only thing that ever caught them was measurement after the fact. A profile moves
hair onto the same footing as everything else -- the model reverse-engineers PARAMETERS from the
reference and a compiler owns topology. That compiler is not written yet -- see the header.

WHY THE COMPILER RUNS IN PYTHON. The obvious design puts a hair engine in the emitted TypeScript
and lets it generate guide curves at runtime. That would break the one property this project sells:
output a person can read. A hundred procedurally generated curves is not readable. So the profile is
intended to be compiled at spec time into a finite, named, readable set of masses. That step does
not exist yet; today the schema is validated and hair components are still authored directly.

WHY TIER 0 IS A SHELL. Measured on the reference this pipeline is calibrated against -- a 570,400
vertex merged scan -- the surface roughness over the hair is 0.00338 against a torso control of
0.00312, a difference of 8%. Hair on a real reference at this fidelity is a SMOOTH SHELL whose
strand detail lives entirely in the diffuse and normal textures. There are no locks in it to copy.
Since this pipeline emits code and no textures, the strand impression has to come from faceting and
material response, not from lock geometry, and the default representation is therefore a shell with
masses layered on it.

WHAT IS NOT CALIBRATED. Every numeric bound below that could not be traced to a measurement is
listed in UNCALIBRATED_FIELDS and reported as such. No multipart GLB with separated hair geometry is
available, so lock-tier parameters -- taper ratios, cross-section aspect, lock counts -- have no
ground truth. They are derived from anatomy and declared derived. The alternative, quietly inventing
plausible numbers, is precisely how eleven hair locks all ended up with a tip radius of 0.0327.

Pure Python 3.10+ standard library.
"""
from __future__ import annotations

from typing import Any

VALID_REPRESENTATION_TIERS = ("shell", "masses", "locks")
DEFAULT_REPRESENTATION_TIER = "shell"

VALID_HAIR_REGIONS = {
    "crown", "fringe", "temple-left", "temple-right", "side-left", "side-right",
    "rear", "nape", "sideburn-left", "sideburn-right", "tail", "stray",
}

# Primitives a hair mass may be built from, and why the obvious alternatives are not here.
#
#   plane-card       needs an alpha texture to read as hair. This pipeline emits code and no
#                    textures, so a card is a visible opaque rectangle. This is the decision left
#                    open in docs/UPGRADE_PLAN.md since v1.2 ("hair cards vs tube-along-curve per
#                    lock"), now closed by measurement: the review metrics respond to silhouette and
#                    banded interior luminance, both of which come from mass, not from card count.
#   tube             constant radius. A lock that does not taper reads as a noodle; that failure is
#                    already recorded in taper_risk().
VALID_HAIR_PRIMITIVES = {"tapered-sweep", "curve-sweep", "lathe", "ellipsoid", "instanced-cluster"}
REJECTED_HAIR_PRIMITIVES = {
    "plane-card": (
        "a card needs an alpha texture to read as hair, and this pipeline emits code with no "
        "textures, so it renders as an opaque rectangle"
    ),
    "tube": (
        "a tube has a constant radius, so a lock built from one reads as a noodle; use "
        "'tapered-sweep', which can reach a real point"
    ),
    "box": "a box cannot describe a hair mass; use 'tapered-sweep' or 'lathe'",
}

# Fields whose numeric bounds are DERIVED, not measured, because no reference with separated hair
# geometry exists to calibrate them against. Anything listed here must carry uncalibrated: true.
UNCALIBRATED_FIELDS = (
    "masses[].taper",
    "masses[].crossSection.aspect",
    "flowField.gravity",
    "flowField.whorls[].strength",
)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _unit(value: Any) -> bool:
    return _is_number(value) and 0.0 <= float(value) <= 1.0


def _validate_hairline(hairline: Any, errors: list[str], warnings: list[str]) -> None:
    """The hairline is five control points across the brow, held in scalp (u, v) coordinates.

    It gets its own object because it drives the silhouette out of proportion to its size and
    because the pipeline already has an unfilled `faceLandmarks.hairline` slot that nothing reads.
    """
    if hairline is None:
        warnings.append(
            "hairProfile.hairline is missing; the hairline sets where the face ends and the hair "
            "begins, and without it the fringe has no anchor"
        )
        return
    if not isinstance(hairline, dict):
        errors.append("hairProfile.hairline must be an object")
        return
    points = hairline.get("controlPoints")
    if not isinstance(points, list) or len(points) < 3:
        errors.append(
            "hairProfile.hairline.controlPoints needs at least 3 points "
            "(temple-left, peak, temple-right at minimum)"
        )
        return
    for index, point in enumerate(points):
        label = f"hairProfile.hairline.controlPoints[{index}]"
        if not isinstance(point, dict):
            errors.append(f"{label} must be an object with u and v")
            continue
        if not _unit(point.get("u")) or not _unit(point.get("v")):
            errors.append(f"{label} must carry u and v in [0,1] on the scalp surface")


def _validate_flow_field(flow: Any, errors: list[str], warnings: list[str]) -> None:
    """Direction as a FIELD over the scalp rather than a direction per mass.

    This is what makes a wide range of hairstyles reachable without authoring a direction for every
    piece: a style is largely a part line, one or two whorls, how much gravity wins, and a sweep
    direction. Roughly six numbers instead of thirty.
    """
    if flow is None:
        warnings.append(
            "hairProfile.flowField is missing; every mass will then need its own authored "
            "direction, which is what produced fourteen competing directions in a recorded build"
        )
        return
    if not isinstance(flow, dict):
        errors.append("hairProfile.flowField must be an object")
        return

    gravity = flow.get("gravity")
    if gravity is not None and not _unit(gravity):
        errors.append("hairProfile.flowField.gravity must be a number in [0,1]")

    part = flow.get("partLine")
    if part is not None:
        if not isinstance(part, dict):
            errors.append("hairProfile.flowField.partLine must be an object")
        elif not _unit(part.get("u")):
            errors.append("hairProfile.flowField.partLine.u must be in [0,1]")

    whorls = flow.get("whorls")
    if whorls is not None:
        if not isinstance(whorls, list):
            errors.append("hairProfile.flowField.whorls must be an array")
        else:
            for index, whorl in enumerate(whorls):
                label = f"hairProfile.flowField.whorls[{index}]"
                if not isinstance(whorl, dict):
                    errors.append(f"{label} must be an object")
                    continue
                if not _unit(whorl.get("u")) or not _unit(whorl.get("v")):
                    errors.append(f"{label} needs u and v in [0,1]")
                strength = whorl.get("strength")
                if strength is not None and not _unit(strength):
                    errors.append(f"{label}.strength must be in [0,1]")


def _validate_mass(index: int, mass: Any, tier: str, errors: list[str], warnings: list[str]) -> None:
    label = f"hairProfile.masses[{index}]"
    if not isinstance(mass, dict):
        errors.append(f"{label} must be an object")
        return

    if not isinstance(mass.get("id"), str) or not str(mass.get("id")).strip():
        errors.append(f"{label}.id is required")

    region = mass.get("region")
    if region is not None and region not in VALID_HAIR_REGIONS:
        errors.append(
            f"{label}.region {region!r} is not one of: {', '.join(sorted(VALID_HAIR_REGIONS))}"
        )

    primitive = mass.get("primitive")
    if primitive in REJECTED_HAIR_PRIMITIVES:
        errors.append(f"{label} may not use primitive {primitive!r}: {REJECTED_HAIR_PRIMITIVES[primitive]}")
    elif primitive is not None and primitive not in VALID_HAIR_PRIMITIVES:
        errors.append(
            f"{label}.primitive {primitive!r} is not usable for hair; "
            f"choose from: {', '.join(sorted(VALID_HAIR_PRIMITIVES))}"
        )

    # THE ROOT RULE. A root held as an absolute position can drift off the skull the moment the mass
    # is resized -- which is the measured failure this whole subsystem exists to prevent. Held as
    # (u, v) on the scalp it cannot: the surface carries it. This is the same binding Blender uses
    # for hair, where a curve's root is a `surface_uv_coordinate` on the emitter mesh.
    root = mass.get("root")
    if not isinstance(root, dict):
        errors.append(
            f"{label}.root is required and must be {{u, v}} on the scalp surface"
        )
    elif "position" in root or "xyz" in root:
        errors.append(
            f"{label}.root must be scalp (u, v), not an absolute position: an absolute root slides "
            f"off the skull when the mass is resized, which renders as a bald patch"
        )
    elif not _unit(root.get("u")) or not _unit(root.get("v")):
        errors.append(f"{label}.root needs u and v in [0,1] on the scalp surface")

    for field in ("length", "width", "thickness"):
        value = mass.get(field)
        if value is not None and (not _is_number(value) or float(value) <= 0.0):
            errors.append(f"{label}.{field} must be a positive number")

    taper = mass.get("taper")
    if taper is not None:
        if not _unit(taper):
            errors.append(f"{label}.taper must be a tip/root ratio in [0,1]")
        elif not mass.get("uncalibrated"):
            warnings.append(
                f"quality: {label}.taper is set but the mass is not marked uncalibrated; no "
                f"reference with separated hair geometry exists to calibrate a taper ratio against"
            )

    if tier == "locks" and mass.get("primitive") not in (None, "tapered-sweep", "curve-sweep"):
        warnings.append(
            f"quality: {label} is in a 'locks' tier profile but is not a swept primitive; a lock "
            f"has to reach a point and only the sweeps can"
        )


def validate_hair_profile(profile: Any, errors: list[str], warnings: list[str]) -> None:
    """Validate a `hairProfile` block. Appends to `errors` and `warnings` in place."""
    if profile is None:
        return
    if not isinstance(profile, dict):
        errors.append("hairProfile must be an object")
        return

    tier = profile.get("representationTier", DEFAULT_REPRESENTATION_TIER)
    if tier not in VALID_REPRESENTATION_TIERS:
        errors.append(
            f"hairProfile.representationTier must be one of: {', '.join(VALID_REPRESENTATION_TIERS)}"
        )
        tier = DEFAULT_REPRESENTATION_TIER

    scalp = profile.get("scalpComponentId")
    if not isinstance(scalp, str) or not scalp.strip():
        errors.append(
            "hairProfile.scalpComponentId is required; it names the component whose surface hair "
            "roots bind to and stand proud of"
        )

    _validate_hairline(profile.get("hairline"), errors, warnings)
    _validate_flow_field(profile.get("flowField"), errors, warnings)

    masses = profile.get("masses")
    if masses is None:
        if tier != "shell":
            errors.append(f"hairProfile.masses is required for representationTier {tier!r}")
    elif not isinstance(masses, list):
        errors.append("hairProfile.masses must be an array")
    else:
        seen: set[str] = set()
        for index, mass in enumerate(masses):
            _validate_mass(index, mass, tier, errors, warnings)
            if isinstance(mass, dict) and isinstance(mass.get("id"), str):
                if mass["id"] in seen:
                    errors.append(f"hairProfile.masses has a duplicate id {mass['id']!r}")
                seen.add(mass["id"])

    if tier == "locks":
        warnings.append(
            "quality: representationTier 'locks' has no calibration reference. The available "
            "reference GLB is one merged 570,400-vertex scan whose hair is a smooth shell "
            "(surface roughness 0.00338 against a torso control of 0.00312), so it contains no "
            "lock geometry to measure. Lock parameters here are derived, not observed."
        )


def hair_profile_report(profile: Any) -> dict[str, Any]:
    """A machine-readable summary, including what in it is uncalibrated."""
    errors: list[str] = []
    warnings: list[str] = []
    validate_hair_profile(profile, errors, warnings)
    tier = DEFAULT_REPRESENTATION_TIER
    mass_count = 0
    if isinstance(profile, dict):
        tier = profile.get("representationTier", DEFAULT_REPRESENTATION_TIER)
        masses = profile.get("masses")
        mass_count = len(masses) if isinstance(masses, list) else 0
    return {
        "schemaVersion": 1,
        "kind": "hair-profile-report",
        "representationTier": tier,
        "massCount": mass_count,
        "errors": errors,
        "warnings": warnings,
        "uncalibratedFields": list(UNCALIBRATED_FIELDS),
        "calibrationNote": (
            "Shell-tier envelope values can be measured against the reference scan. Lock-tier "
            "parameters cannot: that reference is one merged mesh whose hair is a smooth textured "
            "shell, so it holds no lock geometry. A multipart GLB with a separated hair mesh is "
            "required before those bounds mean anything."
        ),
    }
