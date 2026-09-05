#!/usr/bin/env python3
"""Compare browser-captured GLB/procedural diagnostic passes.

This is a deterministic companion to the agent/vision review. It never converts a
global pixel score into a likeness claim. Per-region scores are keyed by the GLB
semantic-ID pass when that pass exists; otherwise the output explicitly says that
region scoring is unavailable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage1_intake"))
from extract_pbr_evidence import load_image  # type: ignore[import-not-found]

PASS_IDS = ("beauty", "alpha-silhouette", "semantic-id", "depth", "normal", "roughness-material-id")


def _resolve(manifest_path: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else manifest_path.parent / path


def _load(path: Path) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    width, height, pixels, warnings = load_image(path)
    if warnings:
        # Warnings are not fatal; the result records paths and metrics only.
        pass
    return width, height, pixels


def _aligned_pixels(
    reference: tuple[int, int, list[tuple[int, int, int, int]]],
    render: tuple[int, int, list[tuple[int, int, int, int]]],
) -> list[tuple[tuple[int, int, int, int], tuple[int, int, int, int]]]:
    rw, rh, rp = reference
    ww, wh, wp = render
    if (rw, rh) != (ww, wh):
        raise ValueError(f"pass dimensions differ: reference={rw}x{rh}, render={ww}x{wh}")
    return list(zip(rp, wp))


def _mask_from_id(pixels: list[tuple[int, int, int, int]], color: tuple[int, int, int]) -> list[bool]:
    return [(r, g, b) == color for r, g, b, _a in pixels]


def _similarity(
    pairs: list[tuple[tuple[int, int, int, int], tuple[int, int, int, int]]],
    mask: list[bool] | None = None,
) -> dict[str, Any]:
    selected = [pair for index, pair in enumerate(pairs) if mask is None or mask[index]]
    if not selected:
        return {"pixels": 0, "similarity": None, "meanAbsoluteError": None}
    error = sum(
        abs(reference[channel] - render[channel])
        for reference, render in selected
        for channel in range(3)
    ) / (len(selected) * 3 * 255.0)
    return {
        "pixels": len(selected),
        "similarity": round(max(0.0, 1.0 - error), 6),
        "meanAbsoluteError": round(error * 255.0, 4),
    }


def _iou(reference_mask: list[bool], render_mask: list[bool], mask: list[bool] | None = None) -> dict[str, Any]:
    indices = range(len(reference_mask)) if mask is None else (i for i, value in enumerate(mask) if value)
    intersection = union = 0
    for index in indices:
        reference = reference_mask[index]
        render = render_mask[index]
        if reference or render:
            union += 1
            if reference and render:
                intersection += 1
    return {"intersection": intersection, "union": union, "iou": round(intersection / union, 6) if union else 1.0}


def compare_capture(manifest_path: Path, capture_id: str) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    capture = next(item for item in manifest.get("captures", []) if item.get("id") == capture_id)
    reference = capture.get("reference")
    if not isinstance(reference, dict):
        raise ValueError("capture has no GLB reference record")
    reference_passes = reference.get("passes")
    render_passes = capture.get("passes")
    if not isinstance(reference_passes, dict) or not isinstance(render_passes, dict):
        raise ValueError("capture is missing v2 pass records")

    profile_path = manifest.get("renderProfile", {}).get("path")
    profile: dict[str, Any] = {}
    if profile_path:
        profile = json.loads(_resolve(manifest_path, profile_path).read_text(encoding="utf-8"))
    region_colors: dict[str, tuple[int, int, int]] = {}
    for region in profile.get("regions", []):
        if not isinstance(region, dict) or not isinstance(region.get("idColor"), list) or len(region["idColor"]) != 3:
            continue
        region_colors[str(region["id"])] = tuple(int(value) for value in region["idColor"])

    loaded: dict[str, tuple[tuple[int, int, list[tuple[int, int, int, int]]], tuple[int, int, list[tuple[int, int, int, int]]]]] = {}
    for pass_id in PASS_IDS:
        ref_record = reference_passes.get(pass_id, {})
        render_record = render_passes.get(pass_id, {})
        if ref_record.get("status") != "recorded" or render_record.get("status") != "recorded":
            continue
        loaded[pass_id] = (
            _load(_resolve(manifest_path, str(ref_record["path"]))),
            _load(_resolve(manifest_path, str(render_record["path"]))),
        )

    semantic_masks: dict[str, list[bool]] = {}
    if "semantic-id" in loaded:
        reference_semantic = loaded["semantic-id"][0][2]
        semantic_masks = {region: _mask_from_id(reference_semantic, color) for region, color in region_colors.items()}

    results: dict[str, Any] = {}
    for pass_id, (reference_image, render_image) in loaded.items():
        pairs = _aligned_pixels(reference_image, render_image)
        if pass_id == "alpha-silhouette":
            reference_mask = [alpha > 0 and (red or green or blue) > 0 for red, green, blue, alpha in reference_image[2]]
            render_mask = [alpha > 0 and (red or green or blue) > 0 for red, green, blue, alpha in render_image[2]]
            global_result: dict[str, Any] = _iou(reference_mask, render_mask)
            per_region = {region: _iou(reference_mask, render_mask, mask) for region, mask in semantic_masks.items()}
        elif pass_id == "semantic-id":
            global_result = {"pixels": len(pairs), "exactMatch": round(sum(ref[:3] == out[:3] for ref, out in pairs) / len(pairs), 6) if pairs else None}
            per_region = {
                region: {"pixels": sum(mask), "exactMatch": round(sum(ref[:3] == out[:3] for index, (ref, out) in enumerate(pairs) if mask[index]) / sum(mask), 6) if sum(mask) else None}
                for region, mask in semantic_masks.items()
            }
        else:
            global_result = _similarity(pairs)
            per_region = {region: _similarity(pairs, mask) for region, mask in semantic_masks.items()}
        results[pass_id] = {"global": global_result, "perRegion": per_region}

    return {
        "schemaVersion": 1,
        "captureId": capture_id,
        "comparisonBasis": "browser-rendered-glb-vs-browser-rendered-procedural",
        "passesCompared": sorted(results),
        "regionEvidence": "semantic-id-reference-pass" if semantic_masks else "unavailable",
        "results": results,
        "blockedClaims": [] if semantic_masks else ["per-region scores are unavailable until semantic-id pass has region colors"],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--capture-id", default="hero")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = compare_capture(args.manifest.expanduser().resolve(), args.capture_id)
        args.out.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        args.out.expanduser().resolve().write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if not result["blockedClaims"] else 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    import sys
    from pathlib import Path

    try:
        sys.path.insert(0, str(next(
            parent / "forge" / "_shared"
            for parent in Path(__file__).resolve().parents
            if (parent / "forge" / "_shared" / "cli_run.py").is_file()
        )))
        from cli_run import run_entry
    except (ImportError, StopIteration):
        # vendored/fixture copies without the forge runtime: run bare, no pipe handling
        def run_entry(main_fn, argv=None):
            return main_fn(sys.argv[1:] if argv is None else argv)

    raise SystemExit(run_entry(main))
