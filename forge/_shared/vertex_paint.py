"""Region-painted vertex colour: the shape predicates, shared by the validator, the gate and TS.

WHY THIS EXISTS. A subject whose identity is a set of flat colour regions with hard boundaries --
a tuxedo cat's blaze, bib and socks; a livery stripe; a painted marking -- cannot be reproduced by
`rootTipGradient`, which is a single linear ramp along one axis, and must not be reproduced by a
texture, because this pipeline emits code and no image assets. The remaining honest representation
is per-vertex colour driven by a declared region shape.

The predicates live here, in Python, rather than only inside the emitted TypeScript, because the
region boundary is an identity feature and therefore has to be GATED -- and a gate that can only run
after a browser render is a gate that runs too late. `forge/stage4_review/vertex_region_gate.py`
evaluates these same functions on exported geometry. `_VERTEX_PAINT_HELPER_SOURCE` in
generate_threejs_factory.py implements the identical maths in TS, and
`forge/tests/test_vertex_paint.py` holds the two to the same numbers on a fixture so they cannot
drift apart silently.

Three shapes, deliberately not more. Each earns its place on a real boundary class:

- `axis-band`   a slab between two planes on one local axis -- a sock ending at an ankle height.
- `ellipsoid`   a closed blob -- a nose pad, a moustache patch, an inner ear.
- `tapered-capsule`  a segment whose radius varies from end to end -- a blaze running down a nose
  bridge, a bib widening under the chin and tapering down the chest. This is the shape a constant
  radius capsule cannot express, and it is the one the bib actually needs.

Every shape takes a `softness` in the component's own local units. `softness: 0` is a hard
boundary. A non-zero softness is a smooth ramp and is the ONLY approximation offered here -- it is
not fur, and anything using it to stand in for a fur fringe must say so.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

VALID_REGION_KINDS = {"axis-band", "ellipsoid", "tapered-capsule"}
VALID_AXES = {"x", "y", "z"}


class VertexPaintError(ValueError):
    """Raised when a paint declaration cannot be evaluated as written."""


def _as_vec3(value: Any, label: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise VertexPaintError(f"{label} must be a 3-number array")
    out = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise VertexPaintError(f"{label} must be a 3-number array")
        out.append(float(item))
    return (out[0], out[1], out[2])


def _as_number(value: Any, label: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VertexPaintError(f"{label} must be a number")
    number = float(value)
    if minimum is not None and number < minimum:
        raise VertexPaintError(f"{label} must be >= {minimum}")
    return number


def parse_hex_color(value: Any, label: str) -> tuple[float, float, float]:
    """`#rrggbb` to linear-ish 0..1 floats.

    No colour-space conversion is applied: Three.js decides how to interpret a colour set from a
    hex string, and doing a second conversion here would put the gate and the renderer on two
    different values for the same authored colour.
    """
    if not isinstance(value, str) or not value.startswith("#") or len(value) != 7:
        raise VertexPaintError(f"{label} must be a '#rrggbb' string")
    try:
        red = int(value[1:3], 16)
        green = int(value[3:5], 16)
        blue = int(value[5:7], 16)
    except ValueError as error:
        raise VertexPaintError(f"{label} must be a '#rrggbb' string") from error
    return (red / 255.0, green / 255.0, blue / 255.0)


def normalize_region(region: Any, label: str) -> dict[str, Any]:
    """Validate one region and return it in canonical form."""
    if not isinstance(region, dict):
        raise VertexPaintError(f"{label} must be an object")
    kind = region.get("kind")
    if kind not in VALID_REGION_KINDS:
        raise VertexPaintError(
            f"{label}.kind must be one of: {', '.join(sorted(VALID_REGION_KINDS))}"
        )
    region_id = region.get("id")
    if not isinstance(region_id, str) or not region_id:
        raise VertexPaintError(f"{label}.id must be a non-empty string")
    color = region.get("color")
    parse_hex_color(color, f"{label}.color")
    softness = _as_number(region.get("softness", 0.0), f"{label}.softness", minimum=0.0)

    canonical: dict[str, Any] = {
        "id": region_id,
        "kind": kind,
        "color": color,
        "softness": softness,
    }
    if kind == "axis-band":
        axis = region.get("axis")
        if axis not in VALID_AXES:
            raise VertexPaintError(f"{label}.axis must be one of: x, y, z")
        low = _as_number(region.get("min"), f"{label}.min")
        high = _as_number(region.get("max"), f"{label}.max")
        if high <= low:
            raise VertexPaintError(f"{label}.max must be greater than {label}.min")
        canonical.update({"axis": axis, "min": low, "max": high})
    elif kind == "ellipsoid":
        canonical["center"] = list(_as_vec3(region.get("center"), f"{label}.center"))
        radii = _as_vec3(region.get("radii"), f"{label}.radii")
        if min(radii) <= 0.0:
            raise VertexPaintError(f"{label}.radii must all be positive")
        canonical["radii"] = list(radii)
    else:
        canonical["start"] = list(_as_vec3(region.get("start"), f"{label}.start"))
        canonical["end"] = list(_as_vec3(region.get("end"), f"{label}.end"))
        start_radius = _as_number(region.get("startRadius"), f"{label}.startRadius", minimum=0.0)
        end_radius = _as_number(region.get("endRadius"), f"{label}.endRadius", minimum=0.0)
        if start_radius <= 0.0 and end_radius <= 0.0:
            raise VertexPaintError(f"{label} needs at least one positive radius")
        if canonical["start"] == canonical["end"]:
            raise VertexPaintError(f"{label}.start and {label}.end must differ")
        canonical.update({"startRadius": start_radius, "endRadius": end_radius})
    return canonical


def normalize_vertex_paint(paint: Any, label: str = "vertexPaint") -> dict[str, Any]:
    """Validate a whole `vertexPaint` block and return it in canonical form."""
    if not isinstance(paint, dict):
        raise VertexPaintError(f"{label} must be an object")
    base_color = paint.get("baseColor")
    parse_hex_color(base_color, f"{label}.baseColor")
    regions = paint.get("regions")
    if not isinstance(regions, list) or not regions:
        raise VertexPaintError(f"{label}.regions must be a non-empty array")
    seen: set[str] = set()
    canonical_regions = []
    for index, region in enumerate(regions):
        canonical = normalize_region(region, f"{label}.regions[{index}]")
        if canonical["id"] in seen:
            raise VertexPaintError(f"{label}.regions has a duplicate id {canonical['id']!r}")
        seen.add(canonical["id"])
        canonical_regions.append(canonical)
    return {"baseColor": base_color, "regions": canonical_regions}


def _smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge1 <= edge0:
        return 0.0 if value < edge1 else 1.0
    t = (value - edge0) / (edge1 - edge0)
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return t * t * (3.0 - 2.0 * t)


def signed_distance(region: dict[str, Any], point: Sequence[float]) -> float:
    """Distance from `point` to the region boundary: negative inside, positive outside.

    Reported in the component's own local units so `softness` is expressed in the same units as
    the geometry, not in a normalised parameter nobody can measure against the reference.
    """
    x, y, z = float(point[0]), float(point[1]), float(point[2])
    kind = region["kind"]
    if kind == "axis-band":
        value = {"x": x, "y": y, "z": z}[region["axis"]]
        low, high = region["min"], region["max"]
        if value < low:
            return low - value
        if value > high:
            return value - high
        return -min(value - low, high - value)
    if kind == "ellipsoid":
        cx, cy, cz = region["center"]
        rx, ry, rz = region["radii"]
        # Scaled-space distance times the smallest radius: an exact SDF for a sphere and a
        # conservative, continuous approximation for a general ellipsoid. It is monotonic in the
        # true distance, which is all the boundary test and the softness ramp need.
        q = math.sqrt(((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 + ((z - cz) / rz) ** 2)
        return (q - 1.0) * min(rx, ry, rz)
    ax, ay, az = region["start"]
    bx, by, bz = region["end"]
    abx, aby, abz = bx - ax, by - ay, bz - az
    apx, apy, apz = x - ax, y - ay, z - az
    denominator = abx * abx + aby * aby + abz * abz
    t = (apx * abx + apy * aby + apz * abz) / denominator if denominator > 0.0 else 0.0
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    closest = (ax + abx * t, ay + aby * t, az + abz * t)
    distance = math.sqrt(
        (x - closest[0]) ** 2 + (y - closest[1]) ** 2 + (z - closest[2]) ** 2
    )
    radius = region["startRadius"] + (region["endRadius"] - region["startRadius"]) * t
    return distance - radius


def region_weight(region: dict[str, Any], point: Sequence[float]) -> float:
    """How strongly a region claims a point: 1 well inside, 0 well outside."""
    distance = signed_distance(region, point)
    softness = region["softness"]
    if softness <= 0.0:
        return 1.0 if distance <= 0.0 else 0.0
    return 1.0 - _smoothstep(-softness * 0.5, softness * 0.5, distance)


def paint_point(paint: dict[str, Any], point: Sequence[float]) -> tuple[float, float, float]:
    """The final colour at one local-space point, regions applied in declaration order."""
    color = list(parse_hex_color(paint["baseColor"], "vertexPaint.baseColor"))
    for region in paint["regions"]:
        weight = region_weight(region, point)
        if weight <= 0.0:
            continue
        target = parse_hex_color(region["color"], "region.color")
        for channel in range(3):
            color[channel] += (target[channel] - color[channel]) * weight
    return (color[0], color[1], color[2])


def dominant_region(paint: dict[str, Any], point: Sequence[float], threshold: float = 0.5) -> str:
    """Which region owns a point, or `'base'`.

    Later regions win ties, matching `paint_point`'s ordered application: the last region to claim
    a point is the one whose colour ends up dominating it.
    """
    owner = "base"
    for region in paint["regions"]:
        if region_weight(region, point) >= threshold:
            owner = region["id"]
    return owner


def classify_points(
    paint: dict[str, Any], points: Iterable[Sequence[float]], threshold: float = 0.5
) -> list[str]:
    return [dominant_region(paint, point, threshold) for point in points]
