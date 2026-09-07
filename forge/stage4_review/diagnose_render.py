#!/usr/bin/env python3
"""Tier-1 cheap, deterministic diagnostics — run BEFORE any expensive AI-vision
comparison-sheet review (Plan 1.3 Workstream B). Pure Python 3.10+ standard
library only, no PIL/numpy, matching the rest of forge/.

Output is a machine-checked pass/fail plus numbers — no visual judgment, no
AI-vision call. `orchestrate_passes.py` refuses to unlock the comparison-sheet
step until a passing tier1Result exists for the current render's hash
(Workstream D).

Scope of the per-part color check: a component whose material-region analysis mapped a
reference crop onto it is scored on that region — the reference's own pixels there against
the render's pixels in the same place. A component without such evidence falls back to
comparing its authored recipe against the render's OVERALL dominant color clusters, which
cannot resolve a small chromatically distinct part in a multi-material scene (a pixel-exact
reconstruction of one such reference scores 50 delta-E against a 20 threshold on that basis).
The verdict records how many components were scored each way rather than presenting one
number as if it had a single meaning.
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
from geometry_integrity import measure_geometry_integrity  # noqa: E402
from reference_mask_artifact import (  # noqa: E402
    MASK_SOURCE_ARTIFACT,
    MASK_SOURCE_HEURISTIC,
    ReferenceMaskArtifact,
    load_reference_mask_artifact,
)
from status_banner import emit_status, load_optional_spec  # noqa: E402


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
REGION_PALETTE_K = 3       # clusters per material region: the material, its shading, one intruder
REGION_SAMPLE_CAP = 3000   # pixels sampled per region; a stride keeps full-res references cheap


def mask_is_inverted(warnings: list[str]) -> bool:
    """Return whether foreground extraction fell back to whole-frame coverage."""
    return any("tiny" in str(warning).lower() for warning in warnings)


def largest_component(mask: list[bool], size: int) -> tuple[list[bool], float]:
    """Keep the largest 4-connected blob; return it and the fraction of cells discarded.

    WHY. `bbox_of` is an EXTREMAL statistic: one stray foreground cell in a corner moves the
    bounding box to the frame edge, and every proportion derived from it with it. Measured on a
    real review plate, a subject filling 24% of the grid reported a bbox of the entire 224x224
    grid, so `aspectRatioDelta` and `scaleDelta` were describing the render's background gradient
    and did not move at all when the camera did.

    The discarded fraction is returned rather than swallowed: a subject with genuinely separated
    parts in projection -- a floating accessory, a detached prop -- would lose them here, and that
    has to be visible instead of quietly improving the numbers.
    """
    seen = [False] * len(mask)
    best: list[int] = []
    total = sum(1 for value in mask if value)
    for start in range(len(mask)):
        if not mask[start] or seen[start]:
            continue
        stack = [start]
        seen[start] = True
        blob = []
        while stack:
            index = stack.pop()
            blob.append(index)
            y, x = divmod(index, size)
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < size and 0 <= ny < size:
                    neighbour = ny * size + nx
                    if mask[neighbour] and not seen[neighbour]:
                        seen[neighbour] = True
                        stack.append(neighbour)
        if len(blob) > len(best):
            best = blob
    filtered = [False] * len(mask)
    for index in best:
        filtered[index] = True
    discarded = (total - len(best)) / total if total else 0.0
    return filtered, discarded


def condition_mask(
    mask: list[bool],
    width: int,
    height: int,
    size: int = MASK_GRID_SIZE,
) -> tuple[list[bool], list[str]]:
    """Reduce a full-resolution foreground mask to the scoring grid, keeping the largest blob.

    Every mask that reaches a silhouette signal passes through here, whatever produced it, so a
    heuristic mask and an adapter mask are always compared on identically conditioned grids.
    """
    resized: list[bool] = []
    for y in range(size):
        sy = min(height - 1, int(y * height / size))
        for x in range(size):
            sx = min(width - 1, int(x * width / size))
            resized.append(mask[sy * width + sx])
    filtered, discarded = largest_component(resized, size)
    warnings: list[str] = []
    if discarded > 0.02:
        warnings.append(
            f"{discarded:.1%} of foreground cells lie outside the largest connected blob and were "
            "excluded from the bounding box; if the subject really has separated parts in this "
            "projection, they are not being measured"
        )
    return filtered, warnings


def load_mask(png_path: Path, size: int = MASK_GRID_SIZE) -> tuple[list[bool], list[str]]:
    """Return the resized heuristic foreground mask and extraction warnings."""
    width, height, pixels, _warnings = load_image(png_path)
    mask, _diag, mask_warnings = build_foreground_mask(width, height, pixels)
    filtered, grid_warnings = condition_mask(mask, width, height, size)
    return filtered, list(mask_warnings) + grid_warnings


def load_reference_mask(
    reference_path: Path,
    size: int = MASK_GRID_SIZE,
) -> tuple[list[bool], list[str], str, ReferenceMaskArtifact | None]:
    """Reference-side mask, named: (mask, warnings, source, artifact-or-None).

    The workspace's published segmentation artifact wins when it authenticates; otherwise the
    corner heuristic, unchanged. An artifact that is present but cannot be authenticated raises
    (see reference_mask_artifact) rather than degrading to the heuristic behind the score. The
    RENDER side never comes through here: a render's backdrop is uniform, which is exactly the
    case the heuristic is right about.
    """
    artifact = load_reference_mask_artifact(reference_path)
    if artifact is None:
        mask, warnings = load_mask(reference_path, size)
        return mask, warnings, MASK_SOURCE_HEURISTIC, None
    mask, grid_warnings = condition_mask(artifact.full, artifact.width, artifact.height, size)
    return mask, list(artifact.warnings) + grid_warnings, MASK_SOURCE_ARTIFACT, artifact


def silhouette_iou(reference_mask: list[bool], render_mask: list[bool]) -> float:
    intersection = 0
    union = 0
    for ref, render in zip(reference_mask, render_mask):
        if ref or render:
            union += 1
            if ref and render:
                intersection += 1
    return intersection / union if union else 0.0


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


def recipe_albedo_lab(recipe: dict[str, Any]) -> tuple[float, float, float] | None:
    """Parse `colorMaterialRecipe.dominantAlbedo` ("rgba(r, g, b, a)") into Lab, or None."""
    dominant = recipe.get("dominantAlbedo")
    if not isinstance(dominant, str):
        return None
    try:
        rgb_text = dominant[dominant.index("(") + 1 : dominant.index(")")]
        red, green, blue = (int(float(part.strip())) for part in rgb_text.split(",")[:3])
    except (ValueError, IndexError):
        return None
    return srgb_to_lab((red, green, blue))


def current_region_crops(spec: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """componentId -> {regionId: crop} for the regions the CURRENT analysis maps to that part.

    `apply_material_analysis` appends to a component's `materialRegions` on every run and
    replaces `materialPipeline.regions` wholesale, so the accumulated list holds superseded
    bounding boxes and regions that were later re-labelled onto a different component. Reading
    the pipeline as the mapping and letting the last appended entry win per region keeps the
    crop that the latest analysis actually produced; a spec with no pipeline block (an older
    profile) falls back to the component's own list.
    """
    pipeline = spec.get("materialPipeline")
    mapped: set[tuple[str, str]] | None = None
    if isinstance(pipeline, dict) and isinstance(pipeline.get("regions"), list):
        mapped = {
            (str(entry.get("componentId")), str(entry.get("regionId")))
            for entry in pipeline["regions"]
            if isinstance(entry, dict)
        }
    crops_by_component: dict[str, dict[str, dict[str, Any]]] = {}
    for component in spec.get("componentTree", []):
        if not isinstance(component, dict):
            continue
        component_id = str(component.get("id"))
        crops: dict[str, dict[str, Any]] = {}
        for entry in component.get("materialRegions") or []:
            if not isinstance(entry, dict):
                continue
            region_id = str(entry.get("regionId"))
            if mapped is not None and (component_id, region_id) not in mapped:
                continue
            crop = entry.get("crop")
            if isinstance(crop, dict) and isinstance(crop.get("bbox"), dict):
                crops[region_id] = crop
        if crops:
            crops_by_component[component_id] = crops
    return crops_by_component


def _region_box(
    crop: dict[str, Any],
    component_id: str,
    region_id: str,
    reference_path: Path,
    reference_width: int,
    reference_height: int,
) -> tuple[int, int, int, int]:
    """Validate a region crop against the reference being scored and return its bbox.

    Evidence cut from a different image cannot say anything about this reference, and a gate
    that quietly downgraded such a component to the whole-frame cluster comparison would hide
    broken evidence behind a plausible number. Broken evidence stops the run instead.
    """
    source_width = crop.get("sourceWidth")
    source_height = crop.get("sourceHeight")
    if (source_width, source_height) != (reference_width, reference_height):
        raise ValueError(
            f"material region {region_id!r} on component {component_id!r} was cut from a "
            f"{source_width}x{source_height} image but the scored reference {reference_path} is "
            f"{reference_width}x{reference_height}; re-run the workspace's material region "
            "analysis so the evidence describes this reference"
        )
    bbox = crop["bbox"]
    try:
        x0 = int(bbox["x"])
        y0 = int(bbox["y"])
        box_width = int(bbox["width"])
        box_height = int(bbox["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"material region {region_id!r} on component {component_id!r} has an unreadable "
            f"crop bbox {bbox!r}"
        ) from exc
    if (box_width <= 0 or box_height <= 0 or x0 < 0 or y0 < 0
            or x0 + box_width > reference_width or y0 + box_height > reference_height):
        raise ValueError(
            f"material region {region_id!r} on component {component_id!r} has bbox "
            f"({x0}, {y0}, {box_width}, {box_height}) outside the "
            f"{reference_width}x{reference_height} reference"
        )
    return x0, y0, box_width, box_height


def _region_palette(
    width: int,
    height: int,
    pixels: list[tuple[int, int, int, int]],
    box: tuple[int, int, int, int],
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> list[dict[str, Any]]:
    """Lab clusters of one region's pixels, subsampled to a bounded cost.

    Both sides of a region comparison go through this same routine, so a render that IS the
    reference scores exactly zero — the property that makes the gate reachable at all.
    """
    x0, y0, box_width, box_height = box
    x0 = int(x0 * scale_x)
    y0 = int(y0 * scale_y)
    box_width = max(1, int(box_width * scale_x))
    box_height = max(1, int(box_height * scale_y))
    stride = max(1, int(((box_width * box_height) / REGION_SAMPLE_CAP) ** 0.5))
    samples: list[tuple[float, float, float]] = []
    for y in range(y0, min(height, y0 + box_height), stride):
        for x in range(x0, min(width, x0 + box_width), stride):
            red, green, blue, alpha = pixels[y * width + x]
            if alpha >= 16:
                samples.append(srgb_to_lab((red, green, blue)))
    return lab_kmeans_palette(samples, REGION_PALETTE_K)


def per_part_color_delta(
    spec: dict[str, Any],
    render_path: Path,
    reference_path: Path,
) -> dict[str, Any]:
    """Per-part colour fidelity, on the most honest basis each component's evidence allows.

    A component whose material-region analysis mapped a reference crop onto it is scored where
    that part actually lives: the crop's own reference pixels against the render's pixels in the
    same place, so the number moves when that part's colour is wrong and reaches zero when the
    render reproduces the reference. Everything else keeps the whole-frame cluster comparison,
    which cannot resolve a small chromatically distinct part in a multi-material scene and is
    reported as such. The two counts are recorded so a score always names its basis.

    Region sampling reads the render at the reference's coordinates, which assumes the render is
    the reference-view capture — the same assumption the silhouette-IoU gate beside it makes, and
    a wrong-view render fails that gate on the same inputs.
    """
    components = [
        component for component in spec.get("componentTree", [])
        if isinstance(component, dict) and isinstance(component.get("colorMaterialRecipe"), dict)
    ]
    if not components:
        return {"checked": 0, "maxDeltaE": 0.0, "perComponent": [], "regionBasis": 0, "clusterBasis": 0}
    region_crops = current_region_crops(spec)
    render_width, render_height, render_pixels, _warnings = load_image(render_path)
    reference: tuple[int, int, list[tuple[int, int, int, int]]] | None = None
    clusters: list[dict[str, Any]] | None = None
    results: list[dict[str, Any]] = []
    for component in components:
        component_id = str(component.get("id"))
        recipe = component["colorMaterialRecipe"]
        crops = region_crops.get(component_id)
        label = recipe.get("componentId") or component_id
        if crops:
            if reference is None:
                ref_width, ref_height, ref_pixels, _ref_warnings = load_image(reference_path)
                reference = (ref_width, ref_height, ref_pixels)
            ref_width, ref_height, ref_pixels = reference
            scale_x = render_width / ref_width
            scale_y = render_height / ref_height
            worst: dict[str, Any] | None = None
            for region_id, crop in crops.items():
                box = _region_box(crop, component_id, region_id, reference_path, ref_width, ref_height)
                expected = _region_palette(ref_width, ref_height, ref_pixels, box)
                rendered = _region_palette(render_width, render_height, render_pixels, box, scale_x, scale_y)
                if not expected or not rendered:
                    continue
                match = min(rendered, key=lambda entry: lab_distance(expected[0]["center"], entry["center"]))
                delta = lab_distance(expected[0]["center"], match["center"])
                if worst is None or delta > worst["deltaE"]:
                    # The matched cluster's share is recorded because the nearest-cluster rule
                    # can match a small patch inside the region; a low share is the reader's
                    # warning that the part passed on a minority of its own pixels.
                    worst = {
                        "componentId": label,
                        "deltaE": round(delta, 2),
                        "basis": "region",
                        "regionId": region_id,
                        "matchedClusterShare": round(match["share_pct"], 3),
                    }
            if worst is not None:
                results.append(worst)
                continue
        expected_lab = recipe_albedo_lab(recipe)
        if expected_lab is None:
            continue
        if clusters is None:
            mask, _diag, _warn = build_foreground_mask(render_width, render_height, render_pixels)
            foreground_lab = [
                srgb_to_lab((r, g, b)) for (r, g, b, _a), keep in zip(render_pixels, mask) if keep
            ]
            clusters = lab_kmeans_palette(foreground_lab, k=min(5, max(1, len(components))))
        best_delta = min((lab_distance(expected_lab, c["center"]) for c in clusters), default=999.0)
        results.append({
            "componentId": label,
            "deltaE": round(best_delta, 2),
            "basis": "cluster",
        })
    max_delta = max((entry["deltaE"] for entry in results), default=0.0)
    return {
        "checked": len(results),
        "maxDeltaE": round(max_delta, 2),
        "perComponent": results,
        "regionBasis": sum(1 for entry in results if entry["basis"] == "region"),
        "clusterBasis": sum(1 for entry in results if entry["basis"] == "cluster"),
    }


def render_hash(render_path: Path) -> str:
    return hashlib.sha256(render_path.read_bytes()).hexdigest()[:16]


def strip_material_maps(scene: object) -> object:
    if isinstance(scene, list):
        return [strip_material_maps(item) for item in scene]
    if not isinstance(scene, dict):
        return scene
    result = {key: strip_material_maps(value) for key, value in scene.items()}
    for key in ("map", "normalMap", "roughnessMap", "metalnessMap", "aoMap"):
        if key in result:
            result[key] = None
    return result


def run_tier1(
    reference_path: Path,
    render_path: Path,
    spec_path: Path | None = None,
    pass_id: str | None = None,
) -> dict[str, Any]:
    reference_mask, reference_mask_warnings, reference_mask_source, _artifact = (
        load_reference_mask(reference_path)
    )
    render_mask, render_mask_warnings = load_mask(render_path)
    mask_warnings = (
        [f"reference: {w}" for w in reference_mask_warnings]
        + [f"render: {w}" for w in render_mask_warnings]
    )

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
    if mask_is_inverted(reference_mask_warnings) or mask_is_inverted(render_mask_warnings):
        failures.append(
            "silhouette evidence is unusable: the foreground mask fell back to whole-frame "
            "coverage (subject under 3.5% of the frame), so IoU and proportion are not "
            "measuring the subject; re-capture with the subject filling more of the frame"
        )
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
        color_report = per_part_color_delta(spec, render_path, reference_path)
        gated = color_is_gated(pass_id)
        color_report["gated"] = gated
        checks["colorDelta"] = color_report
        if gated and color_report["maxDeltaE"] > COLOR_DELTA_E_THRESHOLD:
            over = [
                entry for entry in color_report["perComponent"]
                if entry["deltaE"] > COLOR_DELTA_E_THRESHOLD
            ]
            worst = max(over, key=lambda entry: entry["deltaE"])
            failures.append(
                f"max per-part color delta-E {color_report['maxDeltaE']} exceeds "
                f"threshold {COLOR_DELTA_E_THRESHOLD} "
                f"(worst {worst['componentId']} on the {worst['basis']} basis; "
                f"{len(over)} of {color_report['checked']} parts over threshold)"
            )
        geometry = spec.get("builtGeometry") or spec.get("geometry")
        if isinstance(geometry, dict):
            structural = measure_geometry_integrity(geometry)
            checks["geometryIntegrity"] = structural
            failures.extend(structural["failures"])

    return {
        "passed": not failures,
        "checks": checks,
        "failures": failures,
        "maskWarnings": mask_warnings,
        # Which ruler cut the reference silhouette. Without it a recorded verdict cannot be
        # compared with any other verdict on the same bytes.
        "referenceMaskSource": reference_mask_source,
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
    parser.add_argument("--map-stripped-scene", type=Path, help="Write a scene JSON with material maps disabled")
    parser.add_argument("--map-stripped-render", type=Path, help="Existing unlit/map-stripped render evidence")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        emit_status(load_optional_spec(args.spec), next_command="diagnose_render.py", stream=sys.stderr if args.json else sys.stdout)
        if args.map_stripped_scene:
            scene = json.loads(args.map_stripped_scene.read_text(encoding="utf-8"))
            args.map_stripped_scene.write_text(json.dumps(strip_material_maps(scene), indent=2) + "\n", encoding="utf-8")
        spec_path = args.spec.expanduser().resolve() if args.spec else None
        result = run_tier1(
            args.reference.expanduser().resolve(),
            args.render.expanduser().resolve(),
            spec_path,
            args.pass_id,
        )
        if args.pass_id == "blockout":
            if not args.map_stripped_render:
                result.setdefault("failures", []).append("blockout requires --map-stripped-render evidence")
                result["passed"] = False
            else:
                result["mapStrippedRender"] = str(args.map_stripped_render)
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
