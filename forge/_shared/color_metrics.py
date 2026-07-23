#!/usr/bin/env python3
"""Perceptual colour math for the harness — pure stdlib (sRGB→CIELAB + CIEDE2000).

Canonical sRGB→CIELAB and the full CIEDE2000 (ΔE00) difference, used by the Divine Eye
hue-zone signal and any per-region colour check. CIEDE2000 corrects CIELAB's non-uniformity
(the ~275° blue region especially — exactly where the M9 violet→blue failure lives) via the
R_T hue-rotation term, so it is the right metric for "is this the same hue zone?".

Verified against Sharma et al.'s published CIEDE2000 test pairs (see test_color_metrics.py).
"""
from __future__ import annotations

import math

# D65 reference white (2° observer)
_XN, _YN, _ZN = 95.047, 100.0, 108.883


def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def srgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = _lin(rgb[0]), _lin(rgb[1]), _lin(rgb[2])
    # linear sRGB → XYZ (D65)
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) * 100.0
    y = (r * 0.2126 + g * 0.7152 + b * 0.0722) * 100.0
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) * 100.0

    def f(t: float) -> float:
        return t ** (1.0 / 3.0) if t > 0.008856 else (7.787 * t + 16.0 / 116.0)

    fx, fy, fz = f(x / _XN), f(y / _YN), f(z / _ZN)
    return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


def ciede2000(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    """Full CIEDE2000 colour difference (kL=kC=kH=1). Returns ΔE00."""
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2
    c1 = math.hypot(a1, b1)
    c2 = math.hypot(a2, b2)
    c_bar = (c1 + c2) / 2.0
    g = 0.5 * (1.0 - math.sqrt(c_bar ** 7 / (c_bar ** 7 + 25.0 ** 7))) if c_bar > 0 else 0.0
    a1p = (1.0 + g) * a1
    a2p = (1.0 + g) * a2
    c1p = math.hypot(a1p, b1)
    c2p = math.hypot(a2p, b2)

    def hp(ap: float, bp: float) -> float:
        if ap == 0.0 and bp == 0.0:
            return 0.0
        deg = math.degrees(math.atan2(bp, ap))
        return deg + 360.0 if deg < 0 else deg

    h1p = hp(a1p, b1)
    h2p = hp(a2p, b2)

    dLp = l2 - l1
    dCp = c2p - c1p
    if c1p * c2p == 0.0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180.0:
        dhp = h2p - h1p
    elif h2p - h1p > 180.0:
        dhp = h2p - h1p - 360.0
    else:
        dhp = h2p - h1p + 360.0
    dHp = 2.0 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp) / 2.0)

    Lp_bar = (l1 + l2) / 2.0
    Cp_bar = (c1p + c2p) / 2.0
    if c1p * c2p == 0.0:
        hp_bar = h1p + h2p
    elif abs(h1p - h2p) <= 180.0:
        hp_bar = (h1p + h2p) / 2.0
    elif h1p + h2p < 360.0:
        hp_bar = (h1p + h2p + 360.0) / 2.0
    else:
        hp_bar = (h1p + h2p - 360.0) / 2.0

    t = (
        1.0
        - 0.17 * math.cos(math.radians(hp_bar - 30.0))
        + 0.24 * math.cos(math.radians(2.0 * hp_bar))
        + 0.32 * math.cos(math.radians(3.0 * hp_bar + 6.0))
        - 0.20 * math.cos(math.radians(4.0 * hp_bar - 63.0))
    )
    d_theta = 30.0 * math.exp(-(((hp_bar - 275.0) / 25.0) ** 2))
    rc = 2.0 * math.sqrt(Cp_bar ** 7 / (Cp_bar ** 7 + 25.0 ** 7)) if Cp_bar > 0 else 0.0
    sl = 1.0 + (0.015 * (Lp_bar - 50.0) ** 2) / math.sqrt(20.0 + (Lp_bar - 50.0) ** 2)
    sc = 1.0 + 0.045 * Cp_bar
    sh = 1.0 + 0.015 * Cp_bar * t
    rt = -math.sin(math.radians(2.0 * d_theta)) * rc

    return math.sqrt(
        (dLp / sl) ** 2
        + (dCp / sc) ** 2
        + (dHp / sh) ** 2
        + rt * (dCp / sc) * (dHp / sh)
    )


def delta_e_rgb(rgb1: tuple[int, int, int], rgb2: tuple[int, int, int]) -> float:
    """Convenience: CIEDE2000 between two sRGB colours."""
    return ciede2000(srgb_to_lab(rgb1), srgb_to_lab(rgb2))
