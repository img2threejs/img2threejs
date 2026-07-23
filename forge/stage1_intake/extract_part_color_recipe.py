#!/usr/bin/env python3
"""Extract a structured per-component color/material recipe from a crop.

Plan 1.3 Workstream C. Extends extract_pbr_evidence.py's pixel-evidence approach
with CIE L*a*b* clustering (not RGB), Bradford chromatic adaptation, specular-
hotspot-based roughness estimation, and optional smooth color-gradient detection.
Colors are emitted as "rgba(r, g, b, a)" strings, matching the Canvas 2D
addColorStop(offset, color) signature the codegen already consumes, so no
format conversion is needed between extraction and generate_threejs_factory.py.

Pure Python 3.10+ standard library only — no PIL/numpy, matching the rest of forge/.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_pbr_evidence import (  # noqa: E402
    build_foreground_mask,
    clamp,
    clamp01,
    load_image,
    mask_bbox,
    representative_samples,
    resample_crop,
    slugify,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
from artifact_cache import cache_key, get_cached, manifest_path_for, put_cached  # noqa: E402


# ---------- CIE L*a*b* conversion (sRGB, D65 reference white) ----------

_D65 = (0.95047, 1.0, 1.08883)

# sRGB (linear) -> XYZ, D65
_RGB_TO_XYZ = (
    (0.4124564, 0.3575761, 0.1804375),
    (0.2126729, 0.7151522, 0.0721750),
    (0.0193339, 0.1191920, 0.9503041),
)
_XYZ_TO_RGB = (
    (3.2404542, -1.5371385, -0.4985314),
    (-0.9692660, 1.8760108, 0.0415560),
    (0.0556434, -0.2040259, 1.0572252),
)

# Bradford cone-response matrix, used for chromatic adaptation.
_BRADFORD = (
    (0.8951, 0.2664, -0.1614),
    (-0.7502, 1.7135, 0.0367),
    (0.0389, -0.0685, 1.0296),
)
_BRADFORD_INV = (
    (0.9869929, -0.1470543, 0.1599627),
    (0.4323053, 0.5183603, 0.0492912),
    (-0.0085287, 0.0400428, 0.9684867),
)


def _matmul3(matrix: tuple[tuple[float, float, float], ...], vector: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(
        sum(matrix[row][col] * vector[col] for col in range(3)) for row in range(3)
    )  # type: ignore[return-value]


def _srgb_to_linear(channel: float) -> float:
    c = channel / 255.0
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(channel: float) -> float:
    c = clamp01(channel)
    if c <= 0.0031308:
        srgb = c * 12.92
    else:
        srgb = 1.055 * (c ** (1.0 / 2.4)) - 0.055
    return clamp(srgb * 255.0, 0.0, 255.0)


def _lab_f(t: float) -> float:
    delta = 6.0 / 29.0
    if t > delta ** 3:
        return t ** (1.0 / 3.0)
    return t / (3.0 * delta * delta) + 4.0 / 29.0


def _lab_f_inv(t: float) -> float:
    delta = 6.0 / 29.0
    if t > delta:
        return t ** 3
    return 3.0 * delta * delta * (t - 4.0 / 29.0)


def rgb_to_xyz(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    linear = tuple(_srgb_to_linear(channel) for channel in rgb)
    return _matmul3(_RGB_TO_XYZ, linear)  # type: ignore[arg-type]


def xyz_to_rgb(xyz: tuple[float, float, float]) -> tuple[int, int, int]:
    linear = _matmul3(_XYZ_TO_RGB, xyz)
    return tuple(round(_linear_to_srgb(channel)) for channel in linear)  # type: ignore[return-value]


def xyz_to_lab(xyz: tuple[float, float, float], white: tuple[float, float, float] = _D65) -> tuple[float, float, float]:
    xr, yr, zr = (xyz[i] / white[i] for i in range(3))
    fx, fy, fz = _lab_f(xr), _lab_f(yr), _lab_f(zr)
    l_star = 116.0 * fy - 16.0
    a_star = 500.0 * (fx - fy)
    b_star = 200.0 * (fy - fz)
    return (l_star, a_star, b_star)


def lab_to_xyz(lab: tuple[float, float, float], white: tuple[float, float, float] = _D65) -> tuple[float, float, float]:
    l_star, a_star, b_star = lab
    fy = (l_star + 16.0) / 116.0
    fx = fy + a_star / 500.0
    fz = fy - b_star / 200.0
    return (white[0] * _lab_f_inv(fx), white[1] * _lab_f_inv(fy), white[2] * _lab_f_inv(fz))


def srgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    return xyz_to_lab(rgb_to_xyz(rgb))


def lab_to_rgb(lab: tuple[float, float, float]) -> tuple[int, int, int]:
    return xyz_to_rgb(lab_to_xyz(lab))


def lab_to_rgba(lab: tuple[float, float, float], alpha: float = 1.0) -> str:
    r, g, b = lab_to_rgb(lab)
    return f"rgba({r}, {g}, {b}, {round(clamp01(alpha), 3)})"


def lab_distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    """CIE76 (Euclidean Lab) distance — sufficient for this project's thresholds."""
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def bradford_adapt(
    lab: tuple[float, float, float],
    source_white: tuple[float, float, float],
    target_white: tuple[float, float, float] = _D65,
) -> tuple[float, float, float]:
    """Chromatic-adapt a Lab color from an estimated source illuminant to target_white
    (D65 by default) via the Bradford cone-response transform, approximating a neutral
    white-balance so warm/cool ambient light doesn't skew the extracted albedo."""
    xyz = lab_to_xyz(lab, white=source_white)
    src_cone = _matmul3(_BRADFORD, source_white)
    dst_cone = _matmul3(_BRADFORD, target_white)
    cone = _matmul3(_BRADFORD, xyz)
    adapted_cone = tuple(
        cone[i] * (dst_cone[i] / src_cone[i] if src_cone[i] else 1.0) for i in range(3)
    )
    adapted_xyz = _matmul3(_BRADFORD_INV, adapted_cone)  # type: ignore[arg-type]
    return xyz_to_lab(adapted_xyz, white=target_white)


# ---------- Lab-space k-means palette ----------

def lab_kmeans_palette(samples: list[tuple[float, float, float]], k: int) -> list[dict[str, Any]]:
    if not samples:
        return []
    k = max(1, min(k, len(samples)))
    ordered = sorted(samples, key=lambda lab: lab[0])
    centers = [ordered[int((index + 0.5) * (len(ordered) - 1) / k)] for index in range(k)]
    groups: list[list[tuple[float, float, float]]] = [[] for _ in centers]
    for _ in range(8):
        groups = [[] for _ in centers]
        for sample in samples:
            nearest = min(range(len(centers)), key=lambda idx: lab_distance(sample, centers[idx]))
            groups[nearest].append(sample)
        new_centers = []
        for group, center in zip(groups, centers):
            if not group:
                new_centers.append(center)
                continue
            new_centers.append(tuple(sum(s[c] for s in group) / len(group) for c in range(3)))
        centers = new_centers  # type: ignore[assignment]
    total = len(samples)
    result = [
        {"center": centers[index], "share_pct": len(group) / total}
        for index, group in enumerate(groups)
        if group
    ]
    result.sort(key=lambda entry: entry["share_pct"], reverse=True)
    return result


# ---------- Hotspot-based roughness (Cook-Torrance half-angle proxy) ----------

def estimate_roughness_from_hotspot(
    lumas: list[float],
    mask: list[bool],
) -> tuple[float, str]:
    """Sharp/tight hotspot -> low roughness; wide/diffuse hotspot -> high roughness.
    This is a proxy measurement (spatial spread of the brightest foreground pixels),
    not a true BRDF fit, but it is a real measurement off the pixels rather than a
    heuristic default."""
    foreground_lumas = [luma for luma, keep in zip(lumas, mask) if keep]
    if not foreground_lumas:
        return 0.7, "no foreground pixels; default mid-roughness"
    peak = max(foreground_lumas)
    # Measure what fraction of the surface reads as "near the actual peak
    # brightness" (within 0.1 luma of it), not "above a fixed percentile" — a
    # percentile-based cutoff breaks down when the highlight covers only a tiny
    # fraction of pixels, since the percentile value itself then falls back into
    # the dark background range and every pixel spuriously satisfies ">= cutoff".
    near_peak_threshold = max(0.0, peak - 0.1)
    hotspot_fraction = sum(1 for luma in foreground_lumas if luma >= near_peak_threshold) / len(foreground_lumas)
    # A tight/sharp specular hotspot occupies a small fraction of the surface near
    # peak brightness; a broad/diffuse highlight covers much more of the surface.
    spread = clamp01(hotspot_fraction * 2.0)
    roughness = clamp(0.12 + spread * 0.75, 0.05, 0.95)
    if spread < 0.3:
        evidence = "sharp, tight specular hotspot — supports low roughness/high specularity"
    elif spread > 0.7:
        evidence = "broad, gradually-fading highlight — supports high roughness/diffuse response"
    else:
        evidence = "moderate hotspot spread — mid-range roughness"
    return round(roughness, 3), evidence


# ---------- Material classification (rule-based, evidence-cited) ----------

def classify_material(
    dominant_lab: tuple[float, float, float],
    saturation: float,
    hotspot_roughness: float,
) -> tuple[str, float]:
    lightness = dominant_lab[0]
    chroma = math.sqrt(dominant_lab[1] ** 2 + dominant_lab[2] ** 2)
    if saturation < 0.12 and hotspot_roughness < 0.3:
        return "metal", round(clamp(0.55 + (0.3 - hotspot_roughness) * 0.8, 0.4, 0.85), 3)
    if saturation < 0.15 and lightness > 70 and hotspot_roughness < 0.45:
        return "glass", round(clamp(0.45 + (70 - abs(lightness - 85)) * 0.005, 0.35, 0.75), 3)
    if chroma > 25 and hotspot_roughness > 0.55:
        return "fabric", round(clamp(0.4 + (hotspot_roughness - 0.55) * 0.6, 0.35, 0.7), 3)
    if chroma > 15 and hotspot_roughness < 0.5:
        return "plastic", round(clamp(0.4 + (0.5 - hotspot_roughness) * 0.5, 0.35, 0.7), 3)
    if lightness < 45 and chroma < 20:
        return "stone", round(0.45, 3)
    return "unknown", round(0.3, 3)


# ---------- Color-gradient detection ----------

_GRADIENT_MIN_DELTA_E = 8.0
_GRADIENT_MIN_MONOTONIC_FRACTION = 0.8
_GRADIENT_STOP_OFFSETS = (0.0, 0.55, 1.0)


def _sample_axis(
    size: int,
    lab_grid: list[tuple[float, float, float]],
    mask_grid: list[bool],
    start: tuple[float, float],
    end: tuple[float, float],
    steps: int = 9,
) -> list[tuple[float, float, float]] | None:
    samples: list[tuple[float, float, float]] = []
    for step in range(steps):
        t = step / (steps - 1)
        x = start[0] + (end[0] - start[0]) * t
        y = start[1] + (end[1] - start[1]) * t
        sx = min(size - 1, max(0, int(round(x))))
        sy = min(size - 1, max(0, int(round(y))))
        index = sy * size + sx
        if not mask_grid[index]:
            return None
        samples.append(lab_grid[index])
    return samples


def _is_monotonic(values: list[float]) -> bool:
    """Strictly-equal consecutive samples count as neutral (support neither direction),
    not as evidence for both — otherwise an aliased flat/oscillating signal (e.g. nearest-
    pixel sampling across a checkerboard) can spuriously look monotonic when ties inflate
    whichever real direction happens to have a one-sample edge."""
    if len(values) < 2:
        return True
    increasing = sum(1 for i in range(1, len(values)) if values[i] > values[i - 1])
    decreasing = sum(1 for i in range(1, len(values)) if values[i] < values[i - 1])
    fraction = max(increasing, decreasing) / (len(values) - 1)
    return fraction >= _GRADIENT_MIN_MONOTONIC_FRACTION


def detect_color_gradient(
    size: int,
    lab_grid: list[tuple[float, float, float]],
    mask_grid: list[bool],
) -> dict[str, Any] | None:
    """Tests 4 linear axes + 1 radial-from-centroid axis; returns the strongest
    candidate that clears BOTH the magnitude (ΔE >= 8.0) and monotonicity (>=80%
    consistent-direction sample pairs) gates, or None if nothing qualifies. Both
    gates are required — magnitude alone would accept textured/patterned regions
    with real-but-non-directional color variance (see Risk R8)."""
    foreground_indices = [i for i, keep in enumerate(mask_grid) if keep]
    if len(foreground_indices) < 9:
        return None
    xs = [i % size for i in foreground_indices]
    ys = [i // size for i in foreground_indices]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    candidates: list[tuple[str, tuple[float, float], tuple[float, float], tuple[float, float]]] = [
        ("linear", (x0, cy), (x1, cy), (1.0, 0.0)),
        ("linear", (cx, y0), (cx, y1), (0.0, 1.0)),
        ("linear", (x0, y0), (x1, y1), (0.7071, 0.7071)),
        ("linear", (x0, y1), (x1, y0), (0.7071, -0.7071)),
        ("radial", (cx, cy), (x1, cy), (1.0, 0.0)),
    ]
    best: dict[str, Any] | None = None
    best_delta_e = 0.0
    for kind, start, end, axis in candidates:
        samples = _sample_axis(size, lab_grid, mask_grid, start, end)
        if samples is None:
            continue
        delta_e = lab_distance(samples[0], samples[-1])
        if delta_e < _GRADIENT_MIN_DELTA_E:
            continue
        if not all(_is_monotonic([s[channel] for s in samples]) for channel in range(3)):
            continue
        if delta_e > best_delta_e:
            best_delta_e = delta_e
            stops = []
            for offset in _GRADIENT_STOP_OFFSETS:
                idx = min(len(samples) - 1, max(0, round(offset * (len(samples) - 1))))
                stops.append({"offset": offset, "color": lab_to_rgba(samples[idx])})
            axis_field = list(axis) if kind == "linear" else [cx / size, cy / size]
            best = {
                "type": kind,
                "axis": axis_field,
                "stops": stops,
                "confidence": round(clamp01(delta_e / 30.0), 3),
            }
    return best


# ---------- Top-level recipe assembly ----------

def build_recipe(
    component_id: str,
    crop_path: Path,
    target_threshold: float = 0.7,
    material_class_hint: str | None = None,
    size: int = 96,
) -> dict[str, Any]:
    width, height, pixels, _load_warnings = load_image(crop_path)
    rgba_pixels = pixels
    rgb_pixels = [(r, g, b) for r, g, b, _a in rgba_pixels]
    mask, _mask_diag, _mask_warnings = build_foreground_mask(width, height, rgba_pixels)
    bbox = mask_bbox(width, height, mask)
    sampled_pixels, sampled_mask = resample_crop(width, height, rgba_pixels, mask, bbox, size)

    lab_grid = [srgb_to_lab(pixel) for pixel in sampled_pixels]
    lumas = [0.2126 * p[0] / 255.0 + 0.7152 * p[1] / 255.0 + 0.0722 * p[2] / 255.0 for p in sampled_pixels]

    foreground_lab = [lab for lab, keep in zip(lab_grid, sampled_mask) if keep]
    samples_for_kmeans = representative_samples(foreground_lab, [True] * len(foreground_lab), limit=4000) if foreground_lab else []
    clusters = lab_kmeans_palette(samples_for_kmeans, k=3)

    if not clusters:
        raise ValueError(f"no foreground pixels found in crop for component {component_id!r}")

    dominant_lab = clusters[0]["center"]
    secondary_lab = clusters[1]["center"] if len(clusters) > 1 else dominant_lab

    background_white = _D65  # neutral D65 assumption; refine with a metered gray card in future work
    dominant_adapted = bradford_adapt(dominant_lab, source_white=background_white)
    secondary_adapted = bradford_adapt(secondary_lab, source_white=background_white)

    roughness, hotspot_evidence = estimate_roughness_from_hotspot(lumas, sampled_mask)

    dominant_rgb = lab_to_rgb(dominant_adapted)
    saturation = 0.0 if max(dominant_rgb) == 0 else (max(dominant_rgb) - min(dominant_rgb)) / max(dominant_rgb)
    material_class, material_confidence = classify_material(dominant_adapted, saturation, roughness)
    if material_class_hint:
        material_class = material_class_hint
        material_confidence = max(material_confidence, 0.6)

    metalness = 1.0 if material_class == "metal" else 0.0

    gradient = detect_color_gradient(size, lab_grid, sampled_mask)

    recipe: dict[str, Any] = {
        "componentId": component_id,
        "dominantAlbedo": lab_to_rgba(dominant_adapted),
        "secondaryAlbedo": lab_to_rgba(secondary_adapted),
        "materialClass": material_class,
        "materialClassConfidence": material_confidence,
        "roughnessEstimate": roughness,
        "metalnessEstimate": metalness,
        "highlightEvidence": hotspot_evidence,
        "sourceCropPath": str(crop_path.resolve()),
        "labClusterMeta": {
            "clusterCount": len(clusters),
            "dominantClusterSharePct": round(clusters[0]["share_pct"], 3),
        },
    }
    if gradient is not None:
        recipe["colorGradient"] = gradient
    return recipe


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("crop", type=Path)
    parser.add_argument("--component-id", required=True)
    parser.add_argument("--material-class-hint")
    parser.add_argument("--target-threshold", type=float, default=0.7)
    parser.add_argument("--spec", type=Path, help="ObjectSculptSpec JSON to patch")
    parser.add_argument("--in-place", action="store_true", help="Patch --spec in place when confidence passes")
    parser.add_argument("--out-spec", type=Path, help="Write patched spec to this path")
    parser.add_argument("--allow-low-confidence", action="store_true")
    parser.add_argument("--no-cache", action="store_true", help="Skip the hash-based extraction cache")
    args = parser.parse_args(argv)

    try:
        crop_path = args.crop.expanduser().resolve()
        cache_dir = (args.spec.expanduser().resolve().parent if args.spec else crop_path.parent)
        manifest_path = manifest_path_for(cache_dir, "color_recipe_cache.json")
        key = cache_key(crop_path, Path(__file__).resolve())
        cached = None if args.no_cache else get_cached(manifest_path, key)
        if cached is not None:
            # The cache key is crop+script content only (no component-id), since the
            # pixel-derived data is identical regardless of which component reuses this
            # exact crop — but componentId is a contextual label, not derived from
            # pixels, so it must be re-stamped to the CURRENT invocation's value.
            recipe = dict(cached)
            recipe["componentId"] = args.component_id
            recipe["cacheHit"] = True
        else:
            recipe = build_recipe(
                args.component_id,
                crop_path,
                target_threshold=args.target_threshold,
                material_class_hint=args.material_class_hint,
            )
            recipe["cacheHit"] = False
            if not args.no_cache:
                put_cached(manifest_path, key, {k: v for k, v in recipe.items() if k != "cacheHit"})
        ok = recipe["materialClassConfidence"] >= args.target_threshold
        if args.spec:
            if not ok and not args.allow_low_confidence:
                raise ValueError(
                    f"materialClassConfidence {recipe['materialClassConfidence']} is below target "
                    f"{args.target_threshold}; spec was not patched (use --allow-low-confidence to override)"
                )
            spec_path = args.spec.expanduser().resolve()
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            components = spec.get("componentTree", [])
            target = next((c for c in components if isinstance(c, dict) and c.get("id") == args.component_id), None)
            if target is None:
                raise ValueError(f"component {args.component_id!r} not found in spec componentTree")
            # cacheHit is a runtime diagnostic for this invocation, not evidence data —
            # don't persist it into the spec's colorMaterialRecipe.
            target["colorMaterialRecipe"] = {k: v for k, v in recipe.items() if k != "cacheHit"}
            # colorMaterialRecipe lives on the component (evidence/audit trail: which crop,
            # what confidence). But generate_threejs_factory.py's texture codegen reads
            # colorGradient off the shared MATERIAL entry (materials[] is the actual
            # rendering unit, referenced by id from possibly-multiple components) — so a
            # detected gradient must also be mirrored onto that material to actually render.
            if "colorGradient" in recipe:
                material_id = target.get("material")
                material = next(
                    (m for m in spec.get("materials", []) if isinstance(m, dict) and m.get("id") == material_id),
                    None,
                )
                if material is not None:
                    material["colorGradient"] = recipe["colorGradient"]
            output = spec_path if args.in_place else (args.out_spec.expanduser().resolve() if args.out_spec else None)
            if output:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(recipe, indent=2, ensure_ascii=False))
        # Low confidence without --spec is informational only (nothing was patched,
        # so there's nothing to "fail"); the --spec low-confidence case already
        # raised above and was caught by the except clause below.
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
