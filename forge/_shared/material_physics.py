#!/usr/bin/env python3
"""What three.js actually does with a material value, as code rather than as a comment.

WHY THIS MODULE EXISTS. A material number can be authored, validated, emitted, rendered -- and still
never reach a pixel, because the engine gates it behind another value, clamps it, adds to it, or folds
it into a different uniform. None of that is on the docs page. All of it is in the source, and every
constant below cites the file:line in `three@0.169.0` it came from.

The two failure modes this prevents:

    AUTHORING A VALUE THE ENGINE IGNORES.  `sheen: 0.65` with three's default `sheenColor` of
                                           0x000000 contributes exactly zero, because the sheen term
                                           is `sheenColor * (D * V)` -- a multiply by black.

    EXPOSING ONE DEGREE OF FREEDOM TWICE.  `sheen` and `sheenColor` are multiplied together before
                                           upload, so `sheen 1.0 / #808080` is bit-identical to
                                           `sheen 0.5 / #ffffff`. Extracting both from a reference as
                                           independent evidence extracts the same number twice and
                                           produces priors that contradict each other on paper while
                                           rendering identically.

Full derivation, with every citation: `grimoire/build/threejs_skin_and_cloth_materials.md`.

Pure Python 3.10+ standard library.
"""
from __future__ import annotations

from typing import Any, Final

# ---------------------------------------------------------------------------------------------
# Engine constants. Each is a measured property of three@0.169.0, not a policy choice.
# ---------------------------------------------------------------------------------------------

THREE_VERSION: Final = "0.169.0"

# `material.clearcoatRoughness = max( clearcoatRoughness, 0.0525 )`
#   ShaderChunk/lights_physical_fragment.glsl.js:72
# Anything below this renders identically to it, so accepting a smaller number as meaningful is
# accepting a value the engine will not honour.
CLEARCOAT_ROUGHNESS_FLOOR: Final = 0.0525

# `float sheenEnergyComp = 1.0 - 0.157 * max3( material.sheenColor );`
# `outgoingLight = outgoingLight * sheenEnergyComp + sheenSpecularDirect + sheenSpecularIndirect;`
#   ShaderLib/meshphysical.glsl.js:205,207
# Turning sheen on DARKENS the diffuse base by this coefficient times the effective sheen strength.
SHEEN_ENERGY_COMPENSATION_COEFFICIENT: Final = 0.157

# `clearcoatF0 = vec3( 0.04 )` -- fixed, not authorable.
#   ShaderChunk/lights_physical_fragment.glsl.js:56
CLEARCOAT_F0: Final = 0.04

# three's own defaults, needed to know when an omitted value is a silent no-op rather than a
# reasonable fallback. MeshPhysicalMaterial.js:52,54 / Material.js:24,25
THREE_DEFAULT_SHEEN_COLOR: Final = "#000000"
THREE_DEFAULT_SHEEN_ROUGHNESS: Final = 1.0
THREE_DEFAULT_SIDE: Final = "FrontSide"

# Properties whose entire uniform block is gated behind the scalar being > 0, and whose crossing of
# zero bumps `material.version` and forces a shader recompile.
#   WebGLMaterials.js:406 (sheen); MeshPhysicalMaterial.js getters for the rest.
ZERO_GATED_FEATURES: Final = frozenset({
    "sheen", "clearcoat", "transmission", "iridescence", "anisotropy", "dispersion",
})

# Pairs the engine collapses into a single degree of freedom. Authoring both as independent
# reference-derived evidence is authoring one number twice.
#   sheen x sheenColor: WebGLMaterials.js:408
#   ior <-> reflectivity: MeshPhysicalMaterial.js:34-44
COLLAPSED_DEGREES_OF_FREEDOM: Final = (
    ("sheen", "sheenColor", "multiplied into one uniform before upload"),
    ("ior", "reflectivity", "reflectivity is a derived accessor over ior"),
)

# Skin's real signature at this fidelity is a broad soft dielectric highlight over a warm diffuse
# base, which is a low-strength mid-roughness clearcoat. `transmission` is a SCREEN-SPACE REFRACTION
# model -- glass, not subsurface -- and on a closed opaque body mesh it produces a glassy figure at
# the cost of an extra render target. The registry states the same limit for skin.human.
SKIN_FORBIDDEN_PROPERTIES: Final = frozenset({"transmission", "thickness", "attenuationDistance"})


def effective_sheen_strength(sheen: float, sheen_color: str | None) -> float:
    """What the engine actually applies, after folding the scalar into the colour.

    `uniforms.sheenColor = sheenColor x sheen` (WebGLMaterials.js:408), and the BRDF multiplies by
    that colour, so the strength that matters is the scalar times the colour's largest channel --
    `max3` is exactly what the energy term reads.
    """
    if sheen <= 0.0:
        return 0.0
    channel = _max_channel(sheen_color if sheen_color is not None else THREE_DEFAULT_SHEEN_COLOR)
    return sheen * channel


def sheen_base_darkening(sheen: float, sheen_color: str | None) -> float:
    """How much the diffuse base is scaled DOWN by enabling sheen, in [0, 0.157].

    This is the number that makes "the garment came out too dark" diagnosable rather than a matter of
    opinion: given the authored sheen, the darkening is known before the render exists.
    """
    return SHEEN_ENERGY_COMPENSATION_COEFFICIENT * effective_sheen_strength(sheen, sheen_color)


def compensated_base_luminance(target: float, sheen: float, sheen_color: str | None) -> float:
    """The base luminance to author so the RENDER matches `target` once sheen has darkened it.

    Returns `target` unchanged when sheen is off, so it is safe to apply unconditionally.
    """
    darkening = sheen_base_darkening(sheen, sheen_color)
    if darkening <= 0.0:
        return target
    return min(1.0, target / (1.0 - darkening))


def _max_channel(color: str) -> float:
    """Largest of r, g, b in [0, 1] for a `#rgb` or `#rrggbb` string. Unparseable -> 0.0.

    Returning 0 rather than raising is deliberate: an unparseable colour cannot be shown to carry
    sheen, and the caller's job here is to detect a no-op, not to validate colour syntax.
    """
    text = str(color).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return 0.0
    try:
        channels = [int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except ValueError:
        return 0.0
    return max(channels)


def check_material_physics(
    material_id: str,
    material: dict[str, Any],
    *,
    family: str | None = None,
) -> tuple[list[str], list[str]]:
    """`(errors, warnings)` for one material, judged against what the engine will do with it.

    `family` routes the family-specific rules -- `skin` forbids transmission, and a `fabric` or
    `hair` family that ships no sheen tint has no woven cue at all.
    """
    errors: list[str] = []
    warnings: list[str] = []

    sheen = _number(material.get("sheen"))
    sheen_color = material.get("sheenColor")

    # A no-op, not a subtle mis-tune: the term is a multiply by colour and the default colour is black.
    if sheen is not None and sheen > 0.0:
        if sheen_color is None:
            errors.append(
                f"material {material_id!r} sets sheen {sheen} but no sheenColor. three's default "
                f"sheenColor is {THREE_DEFAULT_SHEEN_COLOR} and the sheen term is a multiply by that "
                f"colour, so this contributes exactly zero. Declare sheenColor."
            )
        elif _max_channel(str(sheen_color)) == 0.0:
            errors.append(
                f"material {material_id!r} sets sheen {sheen} with sheenColor {sheen_color!r}, whose "
                f"channels are all zero, so the sheen term evaluates to zero."
            )
        else:
            darkening = sheen_base_darkening(sheen, str(sheen_color))
            if darkening > 0.0:
                warnings.append(
                    f"quality: material {material_id!r} sheen darkens its own diffuse base by "
                    f"{darkening * 100:.1f}% (sheenEnergyComp). Author the base colour "
                    f"{1.0 / (1.0 - darkening):.3f}x brighter than the reference sample, or the "
                    f"render lands darker than the reference at every non-grazing angle."
                )

    # A value below the clamp is indistinguishable from the clamp, so reporting it as authored is
    # reporting a precision the engine does not have.
    clearcoat_roughness = _number(material.get("clearcoatRoughness"))
    if clearcoat_roughness is not None and 0.0 < clearcoat_roughness < CLEARCOAT_ROUGHNESS_FLOOR:
        warnings.append(
            f"quality: material {material_id!r} clearcoatRoughness {clearcoat_roughness} is below "
            f"three's floor of {CLEARCOAT_ROUGHNESS_FLOOR} and will render as exactly that. Author "
            f"{CLEARCOAT_ROUGHNESS_FLOOR} so the spec says what ships."
        )

    # Both halves of a collapsed pair authored at once: the spec looks richer than it is, and the two
    # numbers can disagree while rendering identically.
    for first, second, why in COLLAPSED_DEGREES_OF_FREEDOM:
        if material.get(first) is not None and material.get(second) is not None:
            if (first, second) == ("ior", "reflectivity"):
                errors.append(
                    f"material {material_id!r} sets both {first} and {second}: {why}. Author {first} "
                    f"only -- it is the physical quantity."
                )
            else:
                warnings.append(
                    f"quality: material {material_id!r} sets both {first} and {second}: {why}. They "
                    f"are one control, so these are not independent evidence."
                )

    normalized_family = (family or material.get("family") or "").lower()

    if normalized_family == "skin":
        for forbidden in sorted(SKIN_FORBIDDEN_PROPERTIES):
            if material.get(forbidden) is not None:
                errors.append(
                    f"material {material_id!r} is family 'skin' and sets {forbidden!r}. transmission "
                    f"is a screen-space refraction (glass) model, not subsurface scattering; on a "
                    f"closed body mesh it renders a glassy figure and costs an extra render target. "
                    f"Use clearcoat over a warm base instead. A thin translucent membrane is a "
                    f"separate component with a separate material, declared as an approximation."
                )

    if normalized_family in {"fabric", "hair"} and (sheen is None or sheen <= 0.0):
        warnings.append(
            f"quality: material {material_id!r} is family {normalized_family!r} but carries no sheen. "
            f"With no textures available, sheen is the whole of the woven or fibre cue this pipeline "
            f"can emit."
        )

    return errors, warnings


def check_open_boundary_sides(component_id: str, component: dict[str, Any]) -> list[str]:
    """A garment shell with an open boundary must render both sides.

    `Material.side` defaults to `FrontSide` (Material.js:24), which culls backfaces. A sleeve, hem or
    collar opening therefore shows a HOLE rather than the inside of the garment: the geometry is
    there, the engine simply does not draw the faces pointing away. Reported as an error because a
    hole at a hem is indistinguishable at a glance from a garment that is too short, and the two want
    completely different fixes.
    """
    garment = component.get("garment")
    if not isinstance(garment, dict):
        return []
    boundaries = garment.get("boundaries")
    if not isinstance(boundaries, list) or not boundaries:
        return []
    has_open = any(
        isinstance(entry, dict) and entry.get("closed") is not True for entry in boundaries
    )
    if not has_open:
        return []
    if str(component.get("side") or THREE_DEFAULT_SIDE) == "FrontSide":
        return [
            f"component {component_id!r} is a garment with an open boundary but leaves side at "
            f"'FrontSide', so backfaces are culled and the opening renders as a hole rather than as "
            f"the inside of the garment. Set side to 'DoubleSide', or author the garment as a closed "
            f"offset volume and mark its boundaries closed."
        ]
    return []


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
