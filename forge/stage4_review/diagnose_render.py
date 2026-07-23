#!/usr/bin/env python3
"""Tier-1 cheap, deterministic diagnostics — run BEFORE any expensive AI-vision
comparison-sheet review (Plan 1.3 Workstream B). Pure Python 3.10+ standard
library only, no PIL/numpy, matching the rest of forge/.

Output is a machine-checked pass/fail plus numbers — no visual judgment, no
AI-vision call. `orchestrate_passes.py` refuses to unlock the comparison-sheet
step until a passing tier1Result exists for the current render's hash
(Workstream D).

Known scope limitation: per_part_color_delta compares the render's OVERALL
dominant color clusters against each component's colorMaterialRecipe, not a
true per-component cropped region (that would need per-component render-crop
coordinates, which the pipeline does not yet track). This is a coarser signal
than the plan's ideal, but per Risk R7, Tier 1 only needs to be discriminative
enough to catch gross mismatches, not pixel-perfect — documented here rather
than silently overclaimed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stage1_intake"))
from extract_pbr_evidence import build_foreground_mask, load_image  # noqa: E402
from extract_part_color_recipe import lab_distance, lab_kmeans_palette, srgb_to_lab  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stage3_build"))
from orchestrate_passes import DEFAULT_PASS_ORDER, load_spec  # noqa: E402


def color_is_gated(pass_id: str | None) -> bool:
    """Per-part color fidelity is a hard criterion only from `material-pass` onward.

    Blockout / structural / form-refinement passes render the model with clay
    (materials deliberately stripped) so the silhouette can be judged on shape
    alone — comparing their per-part color against a colored reference always
    fails and says nothing about those passes' goals. Before material-pass (or
    when the pass is unknown) the color delta is recorded as informational, never
    a failure. This mirrors the skill doctrine: "blockout: silhouette reads
    correctly WITHOUT materials".
    """
    if pass_id is None:
        return False
    try:
        return DEFAULT_PASS_ORDER.index(pass_id) >= DEFAULT_PASS_ORDER.index("material-pass")
    except ValueError:
        return False


SILHOUETTE_IOU_THRESHOLD = 0.85
ASPECT_RATIO_DELTA_THRESHOLD = 0.05
SCALE_DELTA_THRESHOLD = 0.08
SYMMETRY_ERROR_THRESHOLD = 0.10
COLOR_DELTA_E_THRESHOLD = 20.0  # generous vs. the JND (~2-3) to tolerate render/photo lighting gaps
MASK_GRID_SIZE = 224


def load_mask(png_path: Path, size: int = MASK_GRID_SIZE) -> list[bool]:
    """Returns a flat row-major boolean grid of length size*size (foreground=True)."""
    width, height, pixels, _warnings = load_image(png_path)
    mask, _diag, _warn = build_foreground_mask(width, height, pixels)
    resized: list[bool] = []
    for y in range(size):
        sy = min(height - 1, int(y * height / size))
        for x in range(size):
            sx = min(width - 1, int(x * width / size))
            resized.append(mask[sy * width + sx])
    return resized


def silhouette_iou(reference_mask: list[bool], render_mask: list[bool]) -> float:
    intersection = 0
    union = 0
    for ref, render in zip(reference_mask, render_mask):
        if ref or render:
            union += 1
            if ref and render:
                intersection += 1
    return intersection / union if union else 1.0


def bbox_of(mask: list[bool], size: int = MASK_GRID_SIZE) -> tuple[int, int, int, int]:
    xs: list[int] = []
    ys: list[int] = []
    for index, value in enumerate(mask):
        if value:
            xs.append(index % size)
            ys.append(index // size)
    if not xs:
        return (0, 0, 0, 0)
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    return (x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def proportion_delta(
    reference_bbox: tuple[int, int, int, int],
    render_bbox: tuple[int, int, int, int],
) -> dict[str, float]:
    _rx, _ry, rw, rh = reference_bbox
    _dx, _dy, dw, dh = render_bbox
    ref_ar = rw / rh if rh else 0.0
    render_ar = dw / dh if dh else 0.0
    aspect_ratio_delta = abs(ref_ar - render_ar) / ref_ar if ref_ar else (0.0 if render_ar == 0 else 1.0)
    ref_area = rw * rh
    render_area = dw * dh
    scale_delta = abs(ref_area - render_area) / ref_area if ref_area else (0.0 if render_area == 0 else 1.0)
    return {"aspect_ratio_delta": round(aspect_ratio_delta, 4), "scale_delta": round(scale_delta, 4)}


def bilateral_symmetry_error(mask: list[bool], size: int = MASK_GRID_SIZE) -> float:
    total = 0
    mismatches = 0
    for y in range(size):
        row_offset = y * size
        for x in range(size):
            mirrored_x = size - 1 - x
            total += 1
            if mask[row_offset + x] != mask[row_offset + mirrored_x]:
                mismatches += 1
    return mismatches / total if total else 0.0


def per_part_color_delta(recipes: list[dict[str, Any]], render_path: Path) -> dict[str, Any]:
    """Compares each component's colorMaterialRecipe against the render's overall
    dominant Lab-space color clusters (see module docstring for the per-component-
    region scope limitation). Returns per-recipe delta-E and a pass/fail summary."""
    if not recipes:
        return {"checked": 0, "maxDeltaE": 0.0, "perComponent": []}
    width, height, pixels, _warnings = load_image(render_path)
    mask, _diag, _warn = build_foreground_mask(width, height, pixels)
    foreground_lab = [srgb_to_lab((r, g, b)) for (r, g, b, _a), keep in zip(pixels, mask) if keep]
    clusters = lab_kmeans_palette(foreground_lab, k=min(5, max(1, len(recipes))))
    results = []
    for recipe in recipes:
        dominant = recipe.get("dominantAlbedo")
        if not isinstance(dominant, str):
            continue
        try:
            rgb_text = dominant[dominant.index("(") + 1 : dominant.index(")")]
            r, g, b = (int(float(part.strip())) for part in rgb_text.split(",")[:3])
        except (ValueError, IndexError):
            continue
        expected_lab = srgb_to_lab((r, g, b))
        best_delta = min((lab_distance(expected_lab, c["center"]) for c in clusters), default=999.0)
        results.append({"componentId": recipe.get("componentId"), "deltaE": round(best_delta, 2)})
    max_delta = max((entry["deltaE"] for entry in results), default=0.0)
    return {"checked": len(results), "maxDeltaE": round(max_delta, 2), "perComponent": results}


def render_hash(render_path: Path) -> str:
    return hashlib.sha256(render_path.read_bytes()).hexdigest()[:16]


def run_tier1(
    reference_path: Path,
    render_path: Path,
    spec_path: Path | None = None,
    pass_id: str | None = None,
) -> dict[str, Any]:
    reference_mask = load_mask(reference_path)
    render_mask = load_mask(render_path)

    iou = silhouette_iou(reference_mask, render_mask)
    reference_bbox = bbox_of(reference_mask)
    render_bbox = bbox_of(render_mask)
    proportions = proportion_delta(reference_bbox, render_bbox)
    symmetry = bilateral_symmetry_error(render_mask)

    checks: dict[str, Any] = {
        "silhouetteIoU": round(iou, 4),
        "aspectRatioDelta": proportions["aspect_ratio_delta"],
        "scaleDelta": proportions["scale_delta"],
        "bilateralSymmetryError": round(symmetry, 4),
    }
    failures: list[str] = []
    if iou < SILHOUETTE_IOU_THRESHOLD:
        failures.append(f"silhouette IoU {iou:.3f} is below threshold {SILHOUETTE_IOU_THRESHOLD}")
    if proportions["aspect_ratio_delta"] > ASPECT_RATIO_DELTA_THRESHOLD:
        failures.append(
            f"aspect-ratio delta {proportions['aspect_ratio_delta']:.3f} exceeds "
            f"threshold {ASPECT_RATIO_DELTA_THRESHOLD}"
        )
    if proportions["scale_delta"] > SCALE_DELTA_THRESHOLD:
        failures.append(f"scale delta {proportions['scale_delta']:.3f} exceeds threshold {SCALE_DELTA_THRESHOLD}")

    if spec_path is not None:
        spec = load_spec(spec_path)
        recipes = [
            component["colorMaterialRecipe"]
            for component in spec.get("componentTree", [])
            if isinstance(component, dict) and isinstance(component.get("colorMaterialRecipe"), dict)
        ]
        color_report = per_part_color_delta(recipes, render_path)
        gated = color_is_gated(pass_id)
        color_report["gated"] = gated
        checks["colorDelta"] = color_report
        if gated and color_report["maxDeltaE"] > COLOR_DELTA_E_THRESHOLD:
            failures.append(
                f"max per-part color delta-E {color_report['maxDeltaE']} exceeds "
                f"threshold {COLOR_DELTA_E_THRESHOLD}"
            )

    return {
        "passed": not failures,
        "checks": checks,
        "failures": failures,
        "renderHash": render_hash(render_path),
        "passId": pass_id,
    }


def record_tier1_result(spec: dict[str, Any], result: dict[str, Any]) -> None:
    """Appends to spec['tier1Results'] so orchestrate_passes.py (Workstream D) can
    refuse to unlock the Tier-2 comparison-sheet step until a passing entry exists
    for the current pass/render hash."""
    results = spec.setdefault("tier1Results", [])
    if isinstance(results, list):
        results.append(result)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--render", type=Path, required=True)
    parser.add_argument("--spec", type=Path, help="ObjectSculptSpec JSON (for per-part color delta + recording the result)")
    parser.add_argument("--pass-id")
    parser.add_argument("--in-place", action="store_true", help="Record the result into --spec")
    parser.add_argument("--out-spec", type=Path, help="Write the spec with the recorded result to this path")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        spec_path = args.spec.expanduser().resolve() if args.spec else None
        result = run_tier1(
            args.reference.expanduser().resolve(),
            args.render.expanduser().resolve(),
            spec_path,
            args.pass_id,
        )
        if spec_path and (args.in_place or args.out_spec):
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            record_tier1_result(spec, result)
            output = spec_path if args.in_place else args.out_spec.expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["passed"] else 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
