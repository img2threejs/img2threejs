#!/usr/bin/env python3
"""Validate a reference-motion manifest without third-party dependencies."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
SYSTEM_CLASSES = {"camera", "geometry", "material", "lighting", "overlay", "silhouette", "other"}
INTERVAL_CLASSES = {"transition", "stable", "morph", "hold", "other"}
DECISIONS = {"ready-for-implementation", "refine-analysis", "request-input", "stop"}
ROOT_REVIEW_ACTIONS = {"continue", "refine-spec", "refine-code", "request-input", "stop"}
TOP_LEVEL_KEYS = {
    "schemaVersion",
    "source",
    "systems",
    "frames",
    "intervals",
    "comparison",
    "evidence",
    "preImplementationDecision",
    "rootReviewDecision",
}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _confidence(value: Any) -> bool:
    return _is_number(value) and 0.0 <= float(value) <= 1.0


def validate_manifest(document: Any) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []

    def add(path: str, code: str, message: str) -> None:
        errors.append({"path": path, "code": code, "message": message})

    if not isinstance(document, dict):
        add("$", "TYPE", "manifest must be a JSON object")
        return errors

    missing = sorted(TOP_LEVEL_KEYS - set(document))
    unknown = sorted(set(document) - TOP_LEVEL_KEYS)
    for key in missing:
        add("$", "REQUIRED", f"missing top-level field: {key}")
    for key in unknown:
        add(f"$.{key}", "UNKNOWN_FIELD", "unknown top-level field")
    if missing:
        return errors

    if document.get("schemaVersion") != 1:
        add("$.schemaVersion", "SCHEMA_VERSION", "schemaVersion must equal 1")

    source = document.get("source")
    if not isinstance(source, dict):
        add("$.source", "TYPE", "source must be an object")
        return errors

    required_source = {
        "kind",
        "sourceHashSha256",
        "width",
        "height",
        "fps",
        "frameCount",
        "intervalSeconds",
        "decodePolicy",
        "requestedCoverage",
        "analysisCoverage",
        "bootstrapReferenceFrame",
        "viewCount",
        "colorSpace",
        "transferFunction",
    }
    for key in sorted(required_source - set(source)):
        add("$.source", "REQUIRED", f"missing source field: {key}")

    width = source.get("width")
    height = source.get("height")
    frame_count = source.get("frameCount")
    view_count = source.get("viewCount")
    if source.get("kind") not in {"video", "image-sequence"}:
        add("$.source.kind", "ENUM", "kind must be video or image-sequence")
    if not isinstance(source.get("sourceHashSha256"), str) or not SHA256.fullmatch(source["sourceHashSha256"]):
        add("$.source.sourceHashSha256", "HASH", "source hash must be 64 hexadecimal characters")
    for key, value, minimum in (
        ("width", width, 1),
        ("height", height, 1),
        ("frameCount", frame_count, 2),
        ("viewCount", view_count, 1),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            add(f"$.source.{key}", "RANGE", f"{key} must be an integer >= {minimum}")
    if not _is_number(source.get("fps")) or float(source.get("fps", 0.0)) <= 0.0:
        add("$.source.fps", "RANGE", "fps must be a finite number > 0")

    interval = source.get("intervalSeconds")
    interval_start = interval_end = None
    if (
        not isinstance(interval, list)
        or len(interval) != 2
        or not all(_is_number(value) for value in interval)
        or float(interval[0]) >= float(interval[1])
    ):
        add("$.source.intervalSeconds", "INTERVAL", "intervalSeconds must be [start, end] with start < end")
    else:
        interval_start, interval_end = map(float, interval)

    if source.get("decodePolicy") != "original-resolution-no-resample":
        add("$.source.decodePolicy", "DECODE_POLICY", "decodePolicy must be original-resolution-no-resample")
    requested_coverage = source.get("requestedCoverage")
    analysis_coverage = source.get("analysisCoverage")
    if requested_coverage not in {"every-frame", "sampled-allowed"}:
        add("$.source.requestedCoverage", "ENUM", "requestedCoverage must be every-frame or sampled-allowed")
    if analysis_coverage not in {"every-frame", "sampled"}:
        add("$.source.analysisCoverage", "ENUM", "analysisCoverage must be every-frame or sampled")
    if requested_coverage == "every-frame" and analysis_coverage != "every-frame":
        add("$.source.analysisCoverage", "COVERAGE", "an every-frame request cannot be satisfied by sampled analysis")
    if analysis_coverage == "sampled" and not isinstance(source.get("samplingRule"), str):
        add("$.source.samplingRule", "REQUIRED", "sampled analysis requires a samplingRule")
    for key in ("colorSpace", "transferFunction"):
        if not isinstance(source.get(key), str) or not source[key].strip():
            add(f"$.source.{key}", "REQUIRED", f"{key} must be a non-empty string")

    bootstrap = source.get("bootstrapReferenceFrame")
    bootstrap_time = None
    bootstrap_rule = None
    if not isinstance(bootstrap, dict):
        add("$.source.bootstrapReferenceFrame", "TYPE", "bootstrapReferenceFrame must be an object")
    else:
        bootstrap_time = bootstrap.get("timestampSeconds")
        bootstrap_rule = bootstrap.get("selectionRule")
        if not _is_number(bootstrap_time):
            add("$.source.bootstrapReferenceFrame.timestampSeconds", "TYPE", "timestampSeconds must be finite")
        elif interval_start is not None and not (interval_start - 1e-6 <= float(bootstrap_time) <= interval_end + 1e-6):
            add("$.source.bootstrapReferenceFrame.timestampSeconds", "BOUNDS", "bootstrap frame is outside source interval")
        if bootstrap_rule not in {"interval-midpoint", "stable-hold-midpoint", "manual-semantic-keyframe"}:
            add("$.source.bootstrapReferenceFrame.selectionRule", "ENUM", "unknown bootstrap frame selection rule")
        if not isinstance(bootstrap.get("reason"), str) or not bootstrap["reason"].strip():
            add("$.source.bootstrapReferenceFrame.reason", "REQUIRED", "bootstrap frame selection requires a reason")

    roi = source.get("measurementRoi")
    if roi is not None:
        if not isinstance(roi, dict):
            add("$.source.measurementRoi", "TYPE", "measurementRoi must be an object")
        else:
            values = [roi.get(key) for key in ("x", "y", "width", "height")]
            if not all(isinstance(value, int) and not isinstance(value, bool) for value in values):
                add("$.source.measurementRoi", "TYPE", "ROI x, y, width, and height must be integers")
            elif values[0] < 0 or values[1] < 0 or values[2] < 1 or values[3] < 1:
                add("$.source.measurementRoi", "RANGE", "ROI must have non-negative origin and positive size")
            elif isinstance(width, int) and isinstance(height, int) and (
                values[0] + values[2] > width or values[1] + values[3] > height
            ):
                add("$.source.measurementRoi", "BOUNDS", "ROI extends outside the source frame")

    systems = document.get("systems")
    system_ids: set[str] = set()
    has_camera_system = False
    if not isinstance(systems, list) or len(systems) < 2:
        add("$.systems", "COUNT", "systems must contain at least two independently tracked systems")
    else:
        for index, system in enumerate(systems):
            path = f"$.systems[{index}]"
            if not isinstance(system, dict):
                add(path, "TYPE", "system must be an object")
                continue
            system_id = system.get("id")
            if not isinstance(system_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", system_id):
                add(f"{path}.id", "ID", "system id must use lower-case letters, digits, and hyphens")
            elif system_id in system_ids:
                add(f"{path}.id", "DUPLICATE", f"duplicate system id: {system_id}")
            else:
                system_ids.add(system_id)
            system_class = system.get("class")
            if system_class not in SYSTEM_CLASSES:
                add(f"{path}.class", "ENUM", "unknown system class")
            has_camera_system = has_camera_system or system_class == "camera"
            if not isinstance(system.get("observedFeature"), str) or not system["observedFeature"].strip():
                add(f"{path}.observedFeature", "REQUIRED", "observedFeature must be a non-empty string")
    if systems and not has_camera_system:
        add("$.systems", "CAMERA_SYSTEM", "record camera motion as its own system, even when static")

    frames = document.get("frames")
    if not isinstance(frames, list) or len(frames) < 2:
        add("$.frames", "COUNT", "frames must contain at least two measured frames")
        frames = []
    if analysis_coverage == "every-frame" and isinstance(frame_count, int) and len(frames) != frame_count:
        add("$.frames", "COVERAGE", "every-frame analysis requires len(frames) == source.frameCount")

    frame_timestamps: list[float] = []
    previous_index: int | None = None
    previous_time: float | None = None
    for frame_position, frame in enumerate(frames):
        path = f"$.frames[{frame_position}]"
        if not isinstance(frame, dict):
            add(path, "TYPE", "frame must be an object")
            continue
        index = frame.get("index")
        timestamp = frame.get("timestampSeconds")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            add(f"{path}.index", "RANGE", "frame index must be an integer >= 0")
        elif previous_index is not None and index <= previous_index:
            add(f"{path}.index", "ORDER", "frame indexes must be strictly increasing")
        else:
            previous_index = index
        if not _is_number(timestamp):
            add(f"{path}.timestampSeconds", "TYPE", "timestamp must be a finite number")
        else:
            timestamp = float(timestamp)
            if previous_time is not None and timestamp <= previous_time:
                add(f"{path}.timestampSeconds", "ORDER", "timestamps must be strictly increasing")
            previous_time = timestamp
            frame_timestamps.append(timestamp)
            if interval_start is not None and (timestamp < interval_start - 1e-6 or timestamp > interval_end + 1e-6):
                add(f"{path}.timestampSeconds", "BOUNDS", "timestamp is outside source.intervalSeconds")

        observed = frame.get("observed")
        features = observed.get("features") if isinstance(observed, dict) else None
        if not isinstance(features, list) or not features:
            add(f"{path}.observed.features", "COUNT", "each frame requires at least one observed feature")
            features = []
        for feature_position, feature in enumerate(features):
            feature_path = f"{path}.observed.features[{feature_position}]"
            if not isinstance(feature, dict):
                add(feature_path, "TYPE", "observed feature must be an object")
                continue
            if feature.get("systemId") not in system_ids:
                add(f"{feature_path}.systemId", "REFERENCE", "observed feature references an unknown system")
            if not isinstance(feature.get("featureId"), str) or not feature["featureId"].strip():
                add(f"{feature_path}.featureId", "REQUIRED", "featureId must be a non-empty string")
            if not _confidence(feature.get("confidence")):
                add(f"{feature_path}.confidence", "RANGE", "confidence must be between 0 and 1")
            residual = feature.get("fitResidualPx")
            if residual is not None and (not _is_number(residual) or float(residual) < 0.0):
                add(f"{feature_path}.fitResidualPx", "RANGE", "fitResidualPx must be a finite number >= 0")
            points = feature.get("sourcePoints")
            if not isinstance(points, list) or not points:
                add(f"{feature_path}.sourcePoints", "COUNT", "sourcePoints must contain at least one point")
            else:
                for point_position, point in enumerate(points):
                    point_path = f"{feature_path}.sourcePoints[{point_position}]"
                    if not isinstance(point, list) or len(point) != 2 or not all(_is_number(value) for value in point):
                        add(point_path, "TYPE", "source point must be [x, y] finite numbers")
                    elif isinstance(width, int) and isinstance(height, int) and not (
                        0.0 <= float(point[0]) < width and 0.0 <= float(point[1]) < height
                    ):
                        add(point_path, "BOUNDS", "source point lies outside the original frame")

        inferred = frame.get("inferred")
        properties = inferred.get("properties") if isinstance(inferred, dict) else None
        if not isinstance(properties, list):
            add(f"{path}.inferred.properties", "TYPE", "inferred.properties must be a list")
            properties = []
        for property_position, inferred_property in enumerate(properties):
            property_path = f"{path}.inferred.properties[{property_position}]"
            if not isinstance(inferred_property, dict):
                add(property_path, "TYPE", "inferred property must be an object")
                continue
            if inferred_property.get("systemId") not in system_ids:
                add(f"{property_path}.systemId", "REFERENCE", "inferred property references an unknown system")
            for key in ("property", "units"):
                if not isinstance(inferred_property.get(key), str) or not inferred_property[key].strip():
                    add(f"{property_path}.{key}", "REQUIRED", f"{key} must be a non-empty string")
            if "value" not in inferred_property:
                add(f"{property_path}.value", "REQUIRED", "inferred property requires a value")
            confidence_value = inferred_property.get("confidence")
            if not _confidence(confidence_value):
                add(f"{property_path}.confidence", "RANGE", "confidence must be between 0 and 1")
            assumptions = inferred_property.get("assumptions")
            calibration = inferred_property.get("calibrationEvidence", [])
            if not isinstance(assumptions, list) or not all(isinstance(item, str) and item.strip() for item in assumptions):
                add(f"{property_path}.assumptions", "TYPE", "assumptions must be a list of non-empty strings")
                assumptions = []
            if not assumptions and not calibration:
                add(f"{property_path}.assumptions", "INFERENCE_EVIDENCE", "inference requires assumptions or calibration evidence")
            if (
                view_count == 1
                and _confidence(confidence_value)
                and float(confidence_value) > 0.65
                and (not isinstance(calibration, list) or not calibration)
            ):
                add(
                    f"{property_path}.confidence",
                    "SINGLE_VIEW_CONFIDENCE",
                    "single-view inference above 0.65 requires calibrationEvidence",
                )

    intervals = document.get("intervals")
    has_stable_interval = False
    stable_intervals: list[tuple[float, float]] = []
    if not isinstance(intervals, list) or not intervals:
        add("$.intervals", "COUNT", "at least one classified interval is required")
        intervals = []
    for interval_position, item in enumerate(intervals):
        path = f"$.intervals[{interval_position}]"
        if not isinstance(item, dict):
            add(path, "TYPE", "interval must be an object")
            continue
        classification = item.get("classification")
        if classification not in INTERVAL_CLASSES:
            add(f"{path}.classification", "ENUM", "unknown interval classification")
        has_stable_interval = has_stable_interval or classification in {"stable", "hold"}
        start = item.get("startSeconds")
        end = item.get("endSeconds")
        if not _is_number(start) or not _is_number(end) or float(start) >= float(end):
            add(path, "INTERVAL", "interval requires finite startSeconds < endSeconds")
        elif interval_start is not None and (float(start) < interval_start - 1e-6 or float(end) > interval_end + 1e-6):
            add(path, "BOUNDS", "classified interval lies outside source interval")
        elif classification in {"stable", "hold"}:
            stable_intervals.append((float(start), float(end)))

    if _is_number(bootstrap_time) and frame_timestamps:
        midpoint_candidates = frame_timestamps
        midpoint_target = None
        if bootstrap_rule == "interval-midpoint" and interval_start is not None:
            midpoint_target = (interval_start + interval_end) / 2.0
        elif bootstrap_rule == "stable-hold-midpoint" and stable_intervals:
            first_stable = min(stable_intervals, key=lambda bounds: (bounds[0], bounds[1]))
            midpoint_candidates = [
                timestamp
                for timestamp in frame_timestamps
                if first_stable[0] - 1e-6 <= timestamp <= first_stable[1] + 1e-6
            ]
            midpoint_target = (first_stable[0] + first_stable[1]) / 2.0
        elif bootstrap_rule == "stable-hold-midpoint":
            add(
                "$.source.bootstrapReferenceFrame.timestampSeconds",
                "BOOTSTRAP_SELECTION",
                "stable-hold-midpoint requires a stable or hold interval with a measured frame",
            )

        if bootstrap_rule == "manual-semantic-keyframe":
            nearest_recorded = min(frame_timestamps, key=lambda timestamp: abs(timestamp - float(bootstrap_time)))
            if abs(float(bootstrap_time) - nearest_recorded) > 1e-6:
                add(
                    "$.source.bootstrapReferenceFrame.timestampSeconds",
                    "BOOTSTRAP_SELECTION",
                    "manual semantic keyframe must match a measured native-frame timestamp",
                )
        elif midpoint_target is not None and midpoint_candidates:
            expected_time = min(midpoint_candidates, key=lambda timestamp: (abs(timestamp - midpoint_target), timestamp))
            if abs(float(bootstrap_time) - expected_time) > 1e-6:
                add(
                    "$.source.bootstrapReferenceFrame.timestampSeconds",
                    "BOOTSTRAP_SELECTION",
                    f"{bootstrap_rule} requires timestamp {expected_time:g}, the nearest measured frame to the midpoint",
                )

    comparison = document.get("comparison")
    if not isinstance(comparison, dict):
        add("$.comparison", "TYPE", "comparison must be an object")
        comparison = {}
    comparison_status = comparison.get("status")
    if comparison_status not in {"not-run", "complete"}:
        add("$.comparison.status", "ENUM", "comparison status must be not-run or complete")
    if comparison_status == "not-run" and set(comparison) != {"status"}:
        add("$.comparison", "STALE_COMPARISON", "not-run comparison must contain only status")
    if comparison_status == "complete":
        equivalent = comparison.get("featureEquivalent")
        classification = comparison.get("classification")
        if not isinstance(equivalent, bool):
            add("$.comparison.featureEquivalent", "TYPE", "featureEquivalent must be boolean")
        elif equivalent and classification != "like-for-like":
            add("$.comparison.classification", "FEATURE_IDENTITY", "equivalent features require like-for-like classification")
        elif not equivalent and classification != "proxy":
            add("$.comparison.classification", "FEATURE_IDENTITY", "non-equivalent features must be classified as proxy")
        for key in ("referenceFeatureId", "renderFeatureId", "metricInterpretation"):
            if not isinstance(comparison.get(key), str) or not comparison[key].strip():
                add(f"$.comparison.{key}", "REQUIRED", f"{key} must be a non-empty string")
        for key in ("synchronizedTimes", "cameraMatched"):
            if not isinstance(comparison.get(key), bool):
                add(f"$.comparison.{key}", "TYPE", f"{key} must be boolean")
            elif comparison[key] is not True:
                add(f"$.comparison.{key}", "COMPARISON_ALIGNMENT", f"completed comparison requires {key}=true")
        metrics = comparison.get("metrics")
        if not isinstance(metrics, list):
            add("$.comparison.metrics", "TYPE", "metrics must be a list")
        else:
            for metric_position, metric in enumerate(metrics):
                path = f"$.comparison.metrics[{metric_position}]"
                if not isinstance(metric, dict):
                    add(path, "TYPE", "metric must be an object")
                    continue
                if not isinstance(metric.get("name"), str) or not metric["name"].strip():
                    add(f"{path}.name", "REQUIRED", "metric name must be non-empty")
                if not _is_number(metric.get("value")):
                    add(f"{path}.value", "TYPE", "metric value must be finite")
                if not isinstance(metric.get("units"), str) or not metric["units"].strip():
                    add(f"{path}.units", "REQUIRED", "metric units must be non-empty")

    evidence = document.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        add("$.evidence", "COUNT", "evidence must contain at least one artifact")
        evidence = {}
    for name, artifact in evidence.items():
        path = f"$.evidence.{name}"
        if not isinstance(artifact, dict):
            add(path, "TYPE", "evidence artifact must be an object")
            continue
        if not isinstance(artifact.get("path"), str) or not artifact["path"].strip():
            add(f"{path}.path", "REQUIRED", "evidence path must be non-empty")
        if not isinstance(artifact.get("sha256"), str) or not SHA256.fullmatch(artifact["sha256"]):
            add(f"{path}.sha256", "HASH", "evidence hash must be 64 hexadecimal characters")

    decision = document.get("preImplementationDecision")
    if not isinstance(decision, dict):
        add("$.preImplementationDecision", "TYPE", "preImplementationDecision must be an object")
        decision = {}
    action = decision.get("action")
    if action not in DECISIONS:
        add("$.preImplementationDecision.action", "ENUM", "unknown pre-implementation decision")
    reasons = decision.get("reasons")
    if not isinstance(reasons, list) or not reasons or not all(isinstance(item, str) and item.strip() for item in reasons):
        add("$.preImplementationDecision.reasons", "COUNT", "preImplementationDecision requires at least one non-empty reason")
    ambiguities = decision.get("unresolvedAmbiguities")
    if not isinstance(ambiguities, list) or not all(isinstance(item, str) and item.strip() for item in ambiguities):
        add("$.preImplementationDecision.unresolvedAmbiguities", "TYPE", "unresolvedAmbiguities must be a list of non-empty strings")
    if action == "ready-for-implementation":
        if not has_stable_interval:
            add("$.intervals", "READINESS", "implementation readiness requires a stable or hold interval")
        for evidence_name in ("contactSheet", "annotatedKeyframes"):
            if evidence_name not in evidence:
                add("$.evidence", "READINESS", f"implementation readiness requires {evidence_name} evidence")

    root_review = document.get("rootReviewDecision")
    if not isinstance(root_review, dict):
        add("$.rootReviewDecision", "TYPE", "rootReviewDecision must be an object")
        root_review = {}
    root_status = root_review.get("status")
    if root_status not in {"not-run", "complete"}:
        add("$.rootReviewDecision.status", "ENUM", "root review status must be not-run or complete")
    if root_status == "not-run" and set(root_review) != {"status"}:
        add("$.rootReviewDecision", "STALE_REVIEW", "not-run root review must contain only status")
    if root_status == "complete":
        root_action = root_review.get("action")
        if root_action not in ROOT_REVIEW_ACTIONS:
            add("$.rootReviewDecision.action", "ENUM", "unknown root img2threejs review action")
        root_reasons = root_review.get("reasons")
        if not isinstance(root_reasons, list) or not root_reasons or not all(
            isinstance(item, str) and item.strip() for item in root_reasons
        ):
            add("$.rootReviewDecision.reasons", "COUNT", "completed root review requires at least one non-empty reason")
        root_ambiguities = root_review.get("unresolvedAmbiguities")
        if not isinstance(root_ambiguities, list) or not all(
            isinstance(item, str) and item.strip() for item in root_ambiguities
        ):
            add("$.rootReviewDecision.unresolvedAmbiguities", "TYPE", "unresolvedAmbiguities must be a list of non-empty strings")
    if comparison_status == "not-run" and root_status != "not-run":
        add("$.rootReviewDecision.status", "PHASE_MISMATCH", "root review must be not-run before comparison")
    if comparison_status == "complete" and root_status != "complete":
        add("$.rootReviewDecision.status", "PHASE_MISMATCH", "completed comparison requires completed root review")
    if root_status == "complete" and action != "ready-for-implementation":
        add(
            "$.rootReviewDecision.status",
            "PHASE_MISMATCH",
            "a completed post-render review requires a prior ready-for-implementation decision",
        )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    try:
        document = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        result = {"valid": False, "errorType": "input-error", "message": str(exc)}
        if args.as_json:
            print(json.dumps(result, indent=2))
        else:
            print(f"INPUT ERROR: {exc}", file=sys.stderr)
        return 2

    errors = validate_manifest(document)
    action = document.get("preImplementationDecision", {}).get("action") if isinstance(document, dict) else None
    root_action = document.get("rootReviewDecision", {}).get("action") if isinstance(document, dict) else None
    result = {
        "valid": not errors,
        "schemaVersion": document.get("schemaVersion") if isinstance(document, dict) else None,
        "frameCount": len(document.get("frames", [])) if isinstance(document, dict) and isinstance(document.get("frames"), list) else 0,
        "systemCount": len(document.get("systems", [])) if isinstance(document, dict) and isinstance(document.get("systems"), list) else 0,
        "preImplementationDecision": action,
        "rootReviewDecision": root_action,
        "errors": errors,
    }
    if args.as_json:
        print(json.dumps(result, indent=2, allow_nan=False))
    elif errors:
        print(f"INVALID ({len(errors)} errors)")
        for error in errors:
            print(f"- {error['path']} [{error['code']}]: {error['message']}")
    else:
        print(
            f"VALID: {result['frameCount']} frames, {result['systemCount']} systems, "
            f"preImplementationDecision={action}, rootReviewDecision={root_action or 'not-run'}"
        )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
