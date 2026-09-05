#!/usr/bin/env python3
"""Deterministic per-material crop comparison and mismatch classification."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "forge" / "stage1_intake"))
from extract_pbr_evidence import build_foreground_mask, load_image  # noqa: E402
sys.path.insert(0, str(ROOT / "forge" / "_shared"))
from color_metrics import delta_e_rgb  # noqa: E402


GRID = 48


def _grid(path: Path) -> tuple[list[tuple[int, int, int]], list[bool], dict[str, Any]]:
    width, height, pixels, warnings = load_image(path)
    mask, diagnostics, mask_warnings = build_foreground_mask(width, height, pixels)
    values: list[tuple[int, int, int]] = []
    selected: list[bool] = []
    for y in range(GRID):
        sy = min(height - 1, int((y + 0.5) * height / GRID))
        for x in range(GRID):
            sx = min(width - 1, int((x + 0.5) * width / GRID))
            index = sy * width + sx
            red, green, blue, _alpha = pixels[index]
            values.append((red, green, blue))
            selected.append(mask[index])
    return values, selected, {
        "width": width,
        "height": height,
        "foregroundCoverage": round(sum(selected) / max(1, len(selected)), 4),
        "warnings": warnings + mask_warnings,
        "readback": True,
        "diagnostics": diagnostics,
    }


def _mean(values: list[tuple[int, int, int]], selected: list[bool]) -> tuple[int, int, int]:
    kept = [value for value, use in zip(values, selected) if use] or values
    return tuple(round(sum(value[channel] for value in kept) / len(kept)) for channel in range(3))  # type: ignore[return-value]


def _luma(rgb: tuple[int, int, int]) -> float:
    return (rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722) / 255.0


def _features(values: list[tuple[int, int, int]], selected: list[bool]) -> dict[str, float]:
    luma = [_luma(value) for value in values]
    kept = [value for value, use in zip(luma, selected) if use] or luma
    average = sum(kept) / max(1, len(kept))
    variance = sum((value - average) ** 2 for value in kept) / max(1, len(kept))
    horizontal = 0.0
    vertical = 0.0
    count_h = count_v = 0
    for y in range(GRID):
        for x in range(GRID):
            index = y * GRID + x
            if x:
                horizontal += abs(luma[index] - luma[index - 1])
                count_h += 1
            if y:
                vertical += abs(luma[index] - luma[index - GRID])
                count_v += 1
    horizontal /= max(1, count_h)
    vertical /= max(1, count_v)
    anisotropy = max(horizontal, vertical) / max(1e-6, min(horizontal, vertical))
    return {
        "meanLuma": average,
        "lumaVariance": variance,
        "horizontalGradient": horizontal,
        "verticalGradient": vertical,
        "anisotropyProxy": min(4.0, anisotropy),
    }


def _similarity(a: float, b: float, scale: float) -> float:
    return max(0.0, min(1.0, 1.0 - abs(a - b) / max(1e-6, scale)))


def _expected_mismatch(expected: dict[str, Any] | None, features: dict[str, float]) -> list[str]:
    if not expected:
        return []
    mismatches: list[str] = []
    prior = expected.get("renderPrior", expected)
    anisotropy_value = prior.get("anisotropy", 0.0) if isinstance(prior, dict) else 0.0
    anisotropy = anisotropy_value.get("default", 0.0) if isinstance(anisotropy_value, dict) else anisotropy_value
    if isinstance(anisotropy, (int, float)) and anisotropy > 0.5 and features["anisotropyProxy"] < 1.25:
        mismatches.append("wrong-anisotropy")
    roughness_value = prior.get("roughness", 0.5) if isinstance(prior, dict) else 0.5
    roughness = roughness_value.get("default", 0.5) if isinstance(roughness_value, dict) else roughness_value
    if isinstance(roughness, (int, float)):
        # A roughness prior is only a cue here; image evidence cannot prove a scalar.
        if roughness < 0.2 and features["lumaVariance"] < 0.003:
            mismatches.append("highlight-response-too-flat")
        if roughness > 0.8 and features["lumaVariance"] > 0.08:
            mismatches.append("highlight-response-too-sharp")
    return mismatches


def compare_material_crops(
    reference: Path,
    render: Path,
    *,
    material_id: str | None = None,
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reference_values, reference_mask, reference_meta = _grid(reference)
    render_values, render_mask, render_meta = _grid(render)
    reference_mean = _mean(reference_values, reference_mask)
    render_mean = _mean(render_values, render_mask)
    delta_e = delta_e_rgb(reference_mean, render_mean)
    reference_features = _features(reference_values, reference_mask)
    render_features = _features(render_values, render_mask)
    scores = {
        "baseColor": _similarity(delta_e, 0.0, 50.0),
        "meanLuma": _similarity(reference_features["meanLuma"], render_features["meanLuma"], 0.45),
        "microstructure": _similarity(reference_features["lumaVariance"], render_features["lumaVariance"], 0.12),
        "directionalResponse": _similarity(reference_features["anisotropyProxy"], render_features["anisotropyProxy"], 2.0),
    }
    scores["overall"] = round(
        scores["baseColor"] * 0.40
        + scores["meanLuma"] * 0.20
        + scores["microstructure"] * 0.25
        + scores["directionalResponse"] * 0.15,
        4,
    )
    mismatches: list[str] = []
    if delta_e > 20:
        mismatches.append("wrong-base-color")
    if scores["meanLuma"] < 0.72:
        mismatches.append("wrong-exposure-or-value")
    if scores["microstructure"] < 0.62:
        mismatches.append("wrong-roughness-or-microstructure")
    mismatches.extend(_expected_mismatch(expected, render_features))
    if expected and expected.get("family") in {"metal", "glass", "gemstone"} and render_features["lumaVariance"] < 0.002:
        mismatches.append("missing-environment-response")
    unique_mismatches = list(dict.fromkeys(mismatches))
    return {
        "schemaVersion": 1,
        "kind": "img2threejs.material-comparison",
        "materialId": material_id,
        "reference": {"path": str(reference.resolve()), "meanRgb": reference_mean, "features": reference_features, **reference_meta},
        "render": {"path": str(render.resolve()), "meanRgb": render_mean, "features": render_features, **render_meta},
        "metrics": {"deltaE00": round(delta_e, 3), "scores": scores},
        "mismatches": unique_mismatches,
        "passed": scores["overall"] >= 0.7 and not {"wrong-base-color", "missing-environment-response"} & set(unique_mismatches),
        "nextAction": "continue" if scores["overall"] >= 0.7 and not unique_mismatches else "refine-code",
        "limitation": "Deterministic crop metrics are evidence; material identity still requires vision review for ambiguous cases.",
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--render", type=Path, required=True)
    parser.add_argument("--material-id")
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        expected = json.loads(args.expected.read_text(encoding="utf-8")) if args.expected else None
        result = compare_material_crops(args.reference, args.render, material_id=args.material_id, expected=expected)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"passed": result["passed"], "overall": result["metrics"]["scores"]["overall"], "mismatches": result["mismatches"]}, indent=2))
        return 0 if result["passed"] else 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


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
