#!/usr/bin/env python3
"""Signed distance to a skull built as a stack of ellipse rings.

WHY THIS EXISTS. Hair was being held off the skull by a comment. The low-poly humanoid demo carries
this invariant in prose:

    EVERY piece must stand proud of the skull at its own height. Where the skull is proud of the
    hair, the head renders bald there.

The garment in the same file is held off the body by a MEASUREMENT instead -- `sectionedLoft`'s `hug`
marches every vertex outward along its own spoke until a signed distance field reads at least
`clearance`. Its docstring records why authoring the numbers by hand does not work:

    Five rounds of widening these sections by hand still left the body poking through 0.09 H of the
    front ... Any single ellipse that clears the widest point is loose at the narrowest and vice
    versa, so the error moves rather than shrinks.

That is exactly what happened when the hair side-masses were widened by hand: closure went 42.2% to
40.9%, worse on all six views, and dark coverage went DOWN because the widened mass slid off the
skull instead of growing on it. The error moved rather than shrank. This module is the field the
hair never had.

The skull is already a ring stack in every character spec -- the humanoid's head is eight rows of
`[y, radiusX, radiusZ]` plus a per-row z offset -- so the field is derived, never authored twice.

SIGN IS EXACT, MAGNITUDE IS AN ESTIMATE. Inside/outside comes from the sign of the ellipse function,
which is exact. The distance magnitude uses the first-order estimate `f / |grad f|`, which is the
standard Newton step for an ellipse and is not the true Euclidean distance. Every gate built on this
must therefore treat the sign as authoritative and the magnitude as approximate.

Pure Python 3.10+ standard library. No numpy, no PIL.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

Ring = tuple[float, float, float, float]
"""One ring: (y, radiusX, radiusZ, zCentre)."""


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite, got {number!r}")
    return number


def normalise_rings(rings: Iterable[Sequence[float] | dict[str, float]]) -> list[Ring]:
    """Accept the shapes a spec actually carries and return sorted, validated rings.

    A ring may arrive as `(y, rx, rz)`, `(y, rx, rz, zc)`, or a mapping with those keys. The demo's
    head table is the three-tuple form with the z offsets held in a parallel array, so both are real.
    """
    out: list[Ring] = []
    for index, ring in enumerate(rings):
        label = f"ring[{index}]"
        if isinstance(ring, dict):
            y = _finite(ring.get("y"), f"{label}.y")
            rx = _finite(ring.get("rx", ring.get("radiusX")), f"{label}.rx")
            rz = _finite(ring.get("rz", ring.get("radiusZ")), f"{label}.rz")
            zc = _finite(ring.get("zc", ring.get("zCentre", 0.0)), f"{label}.zc")
        else:
            values = list(ring)
            if len(values) not in (3, 4):
                raise ValueError(f"{label} must have 3 or 4 values, got {len(values)}")
            y = _finite(values[0], f"{label}.y")
            rx = _finite(values[1], f"{label}.rx")
            rz = _finite(values[2], f"{label}.rz")
            zc = _finite(values[3], f"{label}.zc") if len(values) == 4 else 0.0
        if rx <= 0.0 or rz <= 0.0:
            raise ValueError(f"{label} radii must be positive, got rx={rx} rz={rz}")
        out.append((y, rx, rz, zc))

    if len(out) < 2:
        raise ValueError("a scalp field needs at least 2 rings")
    out.sort(key=lambda r: r[0])
    for lower, upper in zip(out, out[1:]):
        if upper[0] - lower[0] <= 1e-9:
            raise ValueError(f"rings must have distinct y values, found two at y={lower[0]}")
    return out


class ScalpField:
    """The skull as a lofted ellipse stack, queryable as a signed distance field.

    Negative inside, positive outside, zero on the surface.
    """

    def __init__(self, rings: Iterable[Sequence[float] | dict[str, float]]) -> None:
        self.rings: list[Ring] = normalise_rings(rings)
        self.y_min: float = self.rings[0][0]
        self.y_max: float = self.rings[-1][0]

    # ---- interpolation -------------------------------------------------------------------

    def section(self, y: float) -> tuple[float, float, float]:
        """Interpolated (rx, rz, zCentre) at height `y`, clamped to the stack's own extent."""
        if y <= self.y_min:
            _, rx, rz, zc = self.rings[0]
            return rx, rz, zc
        if y >= self.y_max:
            _, rx, rz, zc = self.rings[-1]
            return rx, rz, zc
        for lower, upper in zip(self.rings, self.rings[1:]):
            if lower[0] <= y <= upper[0]:
                span = upper[0] - lower[0]
                t = (y - lower[0]) / span
                return (
                    lower[1] + (upper[1] - lower[1]) * t,
                    lower[2] + (upper[2] - lower[2]) * t,
                    lower[3] + (upper[3] - lower[3]) * t,
                )
        # Unreachable given the clamps above, but a silent wrong answer here would be a bald patch.
        raise AssertionError(f"no ring interval contains y={y}")

    # ---- the field -----------------------------------------------------------------------

    def radial_distance(self, x: float, y: float, z: float) -> float:
        """Signed distance to the ellipse at this height, ignoring the caps.

        The sign is exact. The magnitude is the first-order estimate `f / |grad f|`.
        """
        rx, rz, zc = self.section(y)
        dx = x / rx
        dz = (z - zc) / rz
        f = dx * dx + dz * dz - 1.0
        # grad f = (2x/rx^2, 2z'/rz^2). At the axis the gradient vanishes and the estimate blows up;
        # the point is then as deep inside as the section is wide, which is the honest answer.
        gx = 2.0 * x / (rx * rx)
        gz = 2.0 * (z - zc) / (rz * rz)
        grad = math.hypot(gx, gz)
        if grad < 1e-12:
            return -min(rx, rz)
        return f / grad

    def distance(self, x: float, y: float, z: float) -> float:
        """Signed distance to the capped ring stack. Negative inside.

        This is the canonical capped-extrusion composition: outside contributions combine by
        Pythagoras, inside contributions take the least-negative (nearest) surface.
        """
        radial = self.radial_distance(x, y, z)
        axial = max(self.y_min - y, y - self.y_max)
        outside = math.hypot(max(radial, 0.0), max(axial, 0.0))
        inside = min(max(radial, axial), 0.0)
        return outside + inside

    # ---- the surface ---------------------------------------------------------------------

    def sample(self, u: float, v: float) -> tuple[float, float, float]:
        """A point on the skull. `u` is azimuth in [0,1), `v` is height in [0,1] bottom to top.

        This is the parametrisation hair roots bind to. A root held as (u,v) cannot slide off the
        skull when its mass is widened, which is the whole reason the binding exists -- the failure
        it prevents is a measured one, not a hypothetical.
        """
        theta = 2.0 * math.pi * u
        y = self.y_min + (self.y_max - self.y_min) * v
        rx, rz, zc = self.section(y)
        return (rx * math.cos(theta), y, zc + rz * math.sin(theta))

    def normal(self, u: float, v: float) -> tuple[float, float, float]:
        """Outward unit normal at `sample(u, v)`.

        Taken as the cross product of the surface's own partial derivatives rather than as the
        horizontal radial direction: the skull's radius changes with height, so near the crown the
        true normal tilts upward substantially and a radial approximation would push hair sideways
        off the top of the head.
        """
        theta = 2.0 * math.pi * u
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        height = self.y_max - self.y_min
        y = self.y_min + height * v
        rx, rz, _ = self.section(y)

        # d/dv of the section, by central difference -- the stack is piecewise linear, so the
        # derivative is undefined exactly at a ring and a central difference is the sane reading.
        step = max(height * 1e-4, 1e-9)
        rx_hi, rz_hi, zc_hi = self.section(min(y + step, self.y_max))
        rx_lo, rz_lo, zc_lo = self.section(max(y - step, self.y_min))
        actual = min(y + step, self.y_max) - max(y - step, self.y_min)
        if actual <= 0.0:
            d_rx = d_rz = d_zc = 0.0
        else:
            d_rx = (rx_hi - rx_lo) / actual
            d_rz = (rz_hi - rz_lo) / actual
            d_zc = (zc_hi - zc_lo) / actual

        # dS/du and dS/dv, with y parametrised by v so dy/dv = height.
        du = (-rx * sin_t, 0.0, rz * cos_t)
        dv = (d_rx * cos_t * height, height, (d_zc + d_rz * sin_t) * height)

        # dv x du points outward; verified against a cylinder, where it reduces to (cos, 0, sin).
        nx = dv[1] * du[2] - dv[2] * du[1]
        ny = dv[2] * du[0] - dv[0] * du[2]
        nz = dv[0] * du[1] - dv[1] * du[0]
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length < 1e-12:
            return (cos_t, 0.0, sin_t)
        return (nx / length, ny / length, nz / length)

    # ---- convenience ---------------------------------------------------------------------

    def surface_samples(
        self,
        u_count: int,
        v_count: int,
        v_range: tuple[float, float] = (0.0, 1.0),
    ) -> list[dict[str, Any]]:
        """Area-weighted samples over the surface, for gates that integrate over the scalp.

        Each entry is `{weight, point, normal, u, v, cap}`. The weight is the local patch area, so a
        gate reporting an exposed FRACTION reports area and not sample count -- rings near the crown
        are far shorter in circumference than rings at the temples, and counting samples would let a
        small bare crown hide behind a large well-covered band.

        THE CAP IS INCLUDED, and that is the part worth stating. The (u, v) parametrisation walks v
        from the bottom ring to the top and samples the ELLIPSE at each height, so the flat disc
        closing the stack at `y_max` belongs to no (u, v) and was invisible to a caller that only
        iterated the band. On a skull whose top ring still has real radius that disc IS the crown,
        and a completely bare crown measured as fully covered.
        """
        if u_count < 3 or v_count < 2:
            raise ValueError("need at least 3 azimuth and 2 height samples")
        low, high = v_range
        if not 0.0 <= low < high <= 1.0:
            raise ValueError(f"v_range must be an ascending band inside [0,1], got {v_range}")

        out: list[dict[str, Any]] = []
        height = self.y_max - self.y_min
        band = high - low
        for j in range(v_count):
            v = low + band * (j + 0.5) / v_count
            y = self.y_min + height * v
            rx, rz, _ = self.section(y)
            # Ramanujan's first approximation to the ellipse perimeter; the exact value needs an
            # elliptic integral and the weights only need to be right relative to each other.
            circumference = math.pi * (3.0 * (rx + rz) - math.sqrt((3.0 * rx + rz) * (rx + 3.0 * rz)))
            weight = (circumference / u_count) * (height * band / v_count)
            for i in range(u_count):
                u = i / u_count
                out.append({"weight": weight, "point": self.sample(u, v),
                            "normal": self.normal(u, v), "u": u, "v": v, "cap": False})

        if high >= 1.0 - 1e-9:
            top_rx, top_rz, top_zc = self.section(self.y_max)
            cap_rings = max(1, v_count // 4)
            for ring_index in range(cap_rings):
                # Mid-radius of an annulus, so a sample stands for the area around it, not a line.
                middle = (ring_index + 0.5) / cap_rings
                outer = (ring_index + 1) / cap_rings
                inner = ring_index / cap_rings
                annulus = math.pi * top_rx * top_rz * (outer * outer - inner * inner)
                weight = annulus / u_count
                for i in range(u_count):
                    u = i / u_count
                    angle = 2.0 * math.pi * u
                    point = (top_rx * middle * math.cos(angle), self.y_max,
                             top_zc + top_rz * middle * math.sin(angle))
                    # `capRing` distinguishes the annuli. They all share v = 1.0 by construction,
                    # and a consumer that buckets rows by v alone collapses the whole disc into one
                    # row -- `largest_exposed_run` then reports a full-circle run the moment any
                    # single annulus is bare.
                    out.append({"weight": weight, "point": point, "normal": (0.0, 1.0, 0.0),
                                "u": u, "v": 1.0, "cap": True, "capRing": ring_index})
        return out


def field_from_component(component: dict) -> ScalpField:
    """Build the field from a spec component that carries a ring stack.

    Accepts the two shapes the pipeline produces: `geometryDescriptor.ringStack.rings`, and the
    parallel-array form `{rings: [[y, rx, rz], ...], zOffsets: [...]}` that the humanoid head uses.
    """
    descriptor = component.get("geometryDescriptor") if isinstance(component, dict) else None
    if not isinstance(descriptor, dict):
        raise ValueError("component has no geometryDescriptor")
    stack = descriptor.get("ringStack")
    if not isinstance(stack, dict):
        raise ValueError("component.geometryDescriptor has no ringStack")
    rings = stack.get("rings")
    if not isinstance(rings, list) or not rings:
        raise ValueError("ringStack.rings must be a non-empty array")

    offsets = stack.get("zOffsets")
    if isinstance(offsets, list) and offsets:
        if len(offsets) != len(rings):
            raise ValueError(
                f"ringStack.zOffsets has {len(offsets)} entries for {len(rings)} rings"
            )
        merged: list[Sequence[float]] = []
        for ring, offset in zip(rings, offsets):
            values = list(ring)
            if len(values) < 3:
                raise ValueError("each ring needs at least [y, rx, rz]")
            merged.append([values[0], values[1], values[2], offset])
        return ScalpField(merged)
    return ScalpField(rings)
