from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from forge.stage2_spec.cs2_adapters import get_family_adapter, registered_adapter_ids
except ModuleNotFoundError:  # direct execution from the forge checkout
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from stage2_spec.cs2_adapters import get_family_adapter, registered_adapter_ids

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
    scene_family = review_scene.get("identity", {}).get("family") if isinstance(review_scene.get("identity"), dict) else None
    if family not in {"knife", "rifle"}:
        failed.append(f"unsupported-family:{family or 'missing'}")
    elif scene_family and family != scene_family:
        failed.append(f"unsupported-family:{family or 'missing'}")
    else:
        adapter_id = manifest.get("componentAdapter")
        try:
            adapter = get_family_adapter(family, manifest.get("subtype"), adapter_id=adapter_id)
        except (TypeError, ValueError):
            expected = ",".join(registered_adapter_ids(family, manifest.get("subtype"))) or "registered"
            failed.append(f"adapter-mismatch:{adapter_id or 'missing'}:{expected}")
        else:
            declared_route = manifest.get("adapterRoute")
            if declared_route is not None and declared_route != adapter.adapter_id:
                failed.append(f"adapter-route-mismatch:{declared_route}:{adapter.adapter_id}")
            declared_version = manifest.get("adapterContractVersion")
            if declared_version is not None and str(declared_version) != adapter.contract_version:
                failed.append(
                    f"adapter-contract-mismatch:{declared_version}:{adapter.contract_version}"
                )
            declared_fixture = manifest.get("adapterFixtureId")
            if declared_fixture is not None and declared_fixture != adapter.fixture_id:
                failed.append(f"fixture-mismatch:{declared_fixture}:{adapter.fixture_id}")
            if review_scene.get("fixtureId") != adapter.fixture_id:
                failed.append(
                    f"fixture-mismatch:{review_scene.get('fixtureId', 'missing')}:{adapter.fixture_id}"
                )
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
            item.startswith((
                "unsupported-family", "manifest-state", "adapter-mismatch", "adapter-route-mismatch",
                "adapter-contract-mismatch", "fixture-mismatch", "projection-evidence", "orbit-coverage",
            ))
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


def _load_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the blocking CS2 knife review contract")
    parser.add_argument("--manifest", type=Path, required=True, help="validated cs2-intake.json")
    parser.add_argument("--metrics", type=Path, required=True, help="render/review metrics JSON")
    parser.add_argument(
        "--scene",
        type=Path,
        default=Path("forge/tests/fixtures/knife_review_scene.json"),
        help="versioned review scene fixture",
    )
    parser.add_argument("--out", type=Path, required=True, help="output review report JSON")
    args = parser.parse_args(argv)
    try:
        manifest = _load_object(args.manifest, "manifest")
        metrics = _load_object(args.metrics, "metrics")
        scene = load_review_scene(args.scene.expanduser())
        report = evaluate_knife_review(manifest, metrics, scene)
        _write_json_atomic(args.out, report)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["verdict"] == "pass" else 1
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
