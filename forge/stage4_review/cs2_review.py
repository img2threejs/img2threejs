from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REQUIRED_SCENE_KEYS = {
    "version",
    "fixtureId",
    "reference",
    "identity",
    "camera",
    "transform",
    "environment",
    "resolution",
    "background",
    "rendererVersion",
    "calibration",
    "thresholds",
}
REQUIRED_THRESHOLDS = {
    "silhouetteIoU",
    "aspectRatioDelta",
    "scaleDelta",
    "projectionCoverage",
    "finishMaterialResponse",
    "identityDetail",
    "paintedRegion",
    "maxOrbitCollapseRatio",
}


def load_review_scene(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("review scene must be a JSON object")
    missing = REQUIRED_SCENE_KEYS - payload.keys()
    if missing:
        raise ValueError("review scene is missing: " + ", ".join(sorted(missing)))
    thresholds = payload.get("thresholds")
    if not isinstance(thresholds, dict) or not REQUIRED_THRESHOLDS.issubset(thresholds):
        raise ValueError("review scene thresholds are incomplete")
    if payload.get("version") != 1:
        raise ValueError("unsupported review scene version")
    return payload


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _failed_threshold(metrics: dict[str, Any], key: str, threshold: float, *, maximum: bool) -> bool:
    value = metrics.get(key)
    if not _number(value):
        return True
    return float(value) > threshold if maximum else float(value) < threshold


def _region_results(inputs: dict[str, Any], threshold: float) -> tuple[list[dict[str, Any]], list[str]]:
    raw = inputs.get("paintedRegions", [])
    if not isinstance(raw, list):
        return [], ["painted-regions-invalid"]
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    for region in raw:
        if not isinstance(region, dict) or not isinstance(region.get("id"), str):
            failures.append("painted-region-invalid")
            continue
        score = region.get("score")
        confidence = region.get("confidence")
        result = {"id": region["id"], "score": score, "confidence": confidence}
        results.append(result)
        if not _number(score) or float(score) < threshold:
            failures.append(f"painted-region:{region['id']}")
        if not _number(confidence):
            failures.append(f"painted-region-confidence:{region['id']}")
    return results, failures


def _critical_feature_failures(inputs: dict[str, Any], default_threshold: float) -> list[str]:
    raw = inputs.get("criticalFeatures", [])
    if not isinstance(raw, list):
        return ["critical-features-invalid"]
    failures: list[str] = []
    for feature in raw:
        if not isinstance(feature, dict) or not isinstance(feature.get("id"), str):
            failures.append("critical-feature-invalid")
            continue
        threshold = feature.get("threshold", default_threshold)
        if not _number(feature.get("score")) or not _number(threshold):
            failures.append(f"critical-feature:{feature['id']}")
        elif float(feature["score"]) < float(threshold):
            failures.append(f"critical-feature:{feature['id']}")
    return failures


def evaluate_knife_review(
    manifest: dict[str, Any],
    inputs: dict[str, Any],
    review_scene: dict[str, Any],
) -> dict[str, Any]:
    thresholds = review_scene["thresholds"]
    failed: list[str] = []
    family = manifest.get("itemFamily")
    if family != "knife":
        failed.append(f"unsupported-family:{family or 'missing'}")
    if manifest.get("componentAdapter") != "cs2-knife-v1":
        failed.append("knife-adapter-missing")
    if manifest.get("state") != "proceed":
        failed.append(f"manifest-state:{manifest.get('state', 'missing')}")

    for key in ("silhouetteIoU", "aspectRatioDelta", "scaleDelta"):
        maximum = key != "silhouetteIoU"
        if _failed_threshold(inputs, key, float(thresholds[key]), maximum=maximum):
            failed.append(key)
    for key in ("finishMaterialResponse", "identityDetail"):
        if _failed_threshold(inputs, key, float(thresholds[key]), maximum=False):
            failed.append(key)

    region_results, region_failures = _region_results(inputs, float(thresholds["paintedRegion"]))
    failed.extend(region_failures)
    failed.extend(_critical_feature_failures(inputs, float(thresholds["identityDetail"])))

    projection = inputs.get("projection")
    if manifest.get("route") == "reference-projection":
        if not isinstance(projection, dict) or projection.get("required") is not True:
            failed.append("projection-evidence-missing")
        elif _failed_threshold(projection, "coverage", float(thresholds["projectionCoverage"]), maximum=False):
            failed.append("projection-coverage")

    multi_angle = inputs.get("multiAngle")
    if not isinstance(multi_angle, dict) or multi_angle.get("degenerate") is True:
        failed.append("degenerate-orbit")
    elif len(multi_angle.get("angles", [])) < 2:
        failed.append("orbit-coverage-missing")

    notes = manifest.get("approximationNotes", inputs.get("approximationNotes", []))
    approximation_notes = [str(note) for note in notes] if isinstance(notes, list) else []
    hidden_confidence = manifest.get("confidence", {}).get("hiddenRegions")
    report = {
        "verdict": "pass" if not failed else "reject",
        "action": "continue" if not failed else ("request-input" if any(
            item.startswith(("unsupported-family", "manifest-state", "projection-evidence", "orbit-coverage"))
            for item in failed
        ) else "refine-code"),
        "family": family,
        "subtype": manifest.get("subtype"),
        "exactnessTier": manifest.get("exactnessTier"),
        "route": manifest.get("route"),
        "reviewScene": {
            "version": review_scene["version"],
            "fixtureId": review_scene["fixtureId"],
            "camera": review_scene["camera"],
            "transform": review_scene["transform"],
            "environment": review_scene["environment"],
            "resolution": review_scene["resolution"],
            "background": review_scene["background"],
            "rendererVersion": review_scene["rendererVersion"],
            "calibration": review_scene["calibration"],
        },
        "metrics": inputs,
        "paintedRegions": region_results,
        "perRegionConfidence": {region["id"]: region["confidence"] for region in region_results},
        "hiddenRegionConfidence": hidden_confidence,
        "approximationNotes": approximation_notes,
        "failedGates": failed,
    }
    return report
