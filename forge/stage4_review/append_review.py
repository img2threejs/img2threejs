#!/usr/bin/env python3
"""Append a self-correction review entry to an ObjectSculptSpec JSON file."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
from feature_acceptance_policy import feature_gate_failures


VALID_ACTIONS = {"continue", "refine-spec", "refine-code", "request-input", "stop"}
VISUAL_PASS_IDS = {
    "blockout",
    "structural-pass",
    "form-refinement",
    "material-pass",
    "surface-pass",
    "lighting-pass",
    "interaction-pass",
}
DEFAULT_PASS_ORDER = [
    "blockout",
    "structural-pass",
    "form-refinement",
    "material-pass",
    "surface-pass",
    "lighting-pass",
    "interaction-pass",
    "optimization-pass",
]


def split_items(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(";") if item.strip()]


def load_spec(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("spec must be a JSON object")
    return payload


def load_json_argument(value: str | None, label: str) -> object | None:
    if not value:
        return None
    candidate = Path(value).expanduser()
    text = candidate.read_text(encoding="utf-8") if candidate.is_file() else value
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be valid inline JSON or a JSON file path") from exc


def clamp_score(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def is_remote_or_virtual_path(value: str) -> bool:
    return "://" in value or value.startswith("data:") or value.startswith("blob:")


def validate_optional_file(value: str | None, label: str) -> None:
    if not value or is_remote_or_virtual_path(value):
        return
    if not Path(value).expanduser().exists():
        raise FileNotFoundError(f"{label} does not exist: {value}")


def visual_acceptance_threshold(spec: dict) -> float:
    loop = spec.get("selfCorrectLoop")
    if isinstance(loop, dict):
        acceptance = loop.get("visualAcceptance")
        if isinstance(acceptance, dict) and isinstance(acceptance.get("threshold"), (int, float)):
            return clamp_score(float(acceptance["threshold"]))
    targets = spec.get("qualityTargets")
    if isinstance(targets, dict) and isinstance(targets.get("targetFidelity"), (int, float)):
        return clamp_score(float(targets["targetFidelity"]))
    return 0.7


def visual_acceptance_config(spec: dict) -> dict:
    loop = spec.get("selfCorrectLoop")
    if not isinstance(loop, dict):
        return {}
    acceptance = loop.get("visualAcceptance")
    return acceptance if isinstance(acceptance, dict) else {}


def pass_order(spec: dict) -> list[str]:
    ids: list[str] = []
    for item in spec.get("buildPasses", []):
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
            ids.append(item["id"])
    return ids or DEFAULT_PASS_ORDER.copy()


def pass_acceptance(spec: dict, pass_id: str) -> list[str]:
    for item in spec.get("buildPasses", []):
        if isinstance(item, dict) and item.get("id") == pass_id:
            acceptance = item.get("acceptance", [])
            if isinstance(acceptance, list):
                return [str(value) for value in acceptance if str(value).strip()]
    return []


def pass_specific_evidence(pass_id: str) -> list[str]:
    if pass_id in {"structural-pass", "form-refinement"}:
        return [
            "attachment contracts for child appendages/connectors",
            "no floating child roots/joints in the browser screenshot",
        ]
    if pass_id == "material-pass":
        return [
            "reference-derived albedo palette with dominant, secondary, and accent colors",
            "independent albedo, roughness, height/normal, and AO maps",
            "macro, meso, and micro surface-frequency response at 1024px or higher",
            "local material masks: AO, dirt, wear, stains, moss, chips, scratches, wetness, or equivalent",
            "neutral, grazing-light close-up, and reference-matched browser screenshots",
        ]
    if pass_id == "surface-pass":
        return [
            "component surfaceDetail for tactile normal/bump/displacement and locality",
        ]
    if pass_id == "lighting-pass":
        return [
            "lightingFromPhoto with key/fill/rim or environment light",
            "exposure, tone mapping, background, shadow softness, and contact shadow behavior",
        ]
    return []


def review_completes_pass(spec: dict, entry: dict, pass_id: str) -> bool:
    if entry.get("passId") != pass_id or entry.get("action") != "continue":
        return False
    visual = entry.get("visualEvidence")
    if pass_id in VISUAL_PASS_IDS and not (isinstance(visual, dict) and visual.get("renderScreenshot")):
        return False
    if pass_id in VISUAL_PASS_IDS:
        score = entry.get("aiVisionScore")
        threshold = entry.get("visualAcceptanceThreshold", 0.7)
        if not isinstance(score, (int, float)) or not isinstance(threshold, (int, float)):
            return False
        if float(score) < float(threshold):
            return False
        if not (isinstance(visual, dict) and visual.get("comparisonImage")):
            return False
        if feature_gate_failures(spec, entry, pass_id):
            return False
    return True


def sync_pipeline(spec: dict) -> None:
    ids = pass_order(spec)
    history = spec.get("reviewHistory", [])
    completed: list[str] = []
    if isinstance(history, list):
        for pass_id in ids:
            if any(
                isinstance(entry, dict) and review_completes_pass(spec, entry, pass_id)
                for entry in history
            ):
                completed.append(pass_id)
            else:
                break
    current = "complete" if len(completed) >= len(ids) else ids[len(completed)]
    required = [] if current == "complete" else pass_acceptance(spec, current)
    required.extend(pass_specific_evidence(current))
    if current in VISUAL_PASS_IDS:
        required.extend(
            [
                "browser render screenshot from your agent's browser/screenshot tool",
                "single side-by-side full reference/render comparison sheet",
                "all critical semantic feature scores at or above their thresholds",
                "self-correction review appended with action=continue before the next pass",
            ]
        )
    pipeline = spec.setdefault("sculptPipeline", {})
    if not isinstance(pipeline, dict):
        pipeline = {}
        spec["sculptPipeline"] = pipeline
    pipeline.update(
        {
            "passGateMode": "locked-sequential",
            "passOrder": ids,
            "currentPass": current,
            "completedPasses": completed,
            "lastCompletedPass": completed[-1] if completed else "",
            "blockedReason": "" if current != "complete" else "all build passes completed",
            "nextRequiredEvidence": required,
        }
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--pass-id", required=True, help="Build pass being reviewed")
    parser.add_argument("--fidelity", type=float, required=True, help="Estimated match score from 0 to 1")
    parser.add_argument("--action", choices=sorted(VALID_ACTIONS), required=True)
    parser.add_argument("--summary", required=True, help="Short review summary")
    parser.add_argument("--matched", help="Semicolon-separated matched criteria")
    parser.add_argument("--mismatches", help="Semicolon-separated mismatches")
    parser.add_argument("--spec-fixes", help="Semicolon-separated spec refinement tasks")
    parser.add_argument("--code-fixes", help="Semicolon-separated code refinement tasks")
    parser.add_argument("--evidence", help="Semicolon-separated screenshot/image/render paths or notes")
    parser.add_argument("--reference-screenshot", help="Reference image/screenshot path or URL used for visual comparison")
    parser.add_argument("--render-screenshot", help="Rendered browser screenshot path or URL for this pass")
    parser.add_argument("--comparison-image", help="Side-by-side reference/render contact sheet reviewed by AI vision")
    parser.add_argument("--ai-vision-score", type=float, help="AI vision visual match score from 0 to 1")
    parser.add_argument("--layer-scores-json", help="JSON object with AI vision layer scores, e.g. silhouette/material/lighting")
    parser.add_argument("--feature-reviews-json", help="JSON array or file path containing per-feature scores from the same full image pair")
    parser.add_argument("--ai-vision-notes", help="AI vision critique explaining the score and mismatch root causes")
    parser.add_argument("--visual-threshold", type=float, help="Override visual acceptance threshold for this review")
    parser.add_argument("--camera-view", help="Camera/viewpoint label, e.g. front, three-quarter, side, close-up")
    parser.add_argument("--visual-notes", help="Short notes from screenshot comparison")
    parser.add_argument(
        "--require-screenshot-files",
        action="store_true",
        help="Require local screenshot paths to exist before writing the review",
    )
    parser.add_argument("--in-place", action="store_true", help="Write back to the input spec")
    parser.add_argument("--out", type=Path, help="Output JSON path when not using --in-place")
    args = parser.parse_args(argv)

    if args.require_screenshot_files:
        validate_optional_file(args.reference_screenshot, "--reference-screenshot")
        validate_optional_file(args.render_screenshot, "--render-screenshot")
        validate_optional_file(args.comparison_image, "--comparison-image")
    if args.pass_id in VISUAL_PASS_IDS and args.action == "continue" and not args.render_screenshot:
        raise ValueError(
            "visual pass cannot use action=continue without --render-screenshot; "
            "capture a browser screenshot or choose refine-code/request-input"
        )

    spec_path = args.spec.expanduser().resolve()
    spec = load_spec(spec_path)
    history = spec.setdefault("reviewHistory", [])
    if not isinstance(history, list):
        raise ValueError("reviewHistory must be an array")
    threshold = clamp_score(args.visual_threshold) if args.visual_threshold is not None else visual_acceptance_threshold(spec)
    layer_scores = None
    if args.layer_scores_json:
        layer_scores = load_json_argument(args.layer_scores_json, "--layer-scores-json")
        if not isinstance(layer_scores, dict):
            raise ValueError("--layer-scores-json must be a JSON object")
        for key, value in layer_scores.items():
            if not isinstance(key, str) or not isinstance(value, (int, float)):
                raise ValueError("--layer-scores-json values must be numeric scores")
            if not 0 <= float(value) <= 1:
                raise ValueError("--layer-scores-json values must be from 0 to 1")
    if args.ai_vision_score is not None and not 0 <= args.ai_vision_score <= 1:
        raise ValueError("--ai-vision-score must be from 0 to 1")
    if args.visual_threshold is not None and not 0 <= args.visual_threshold <= 1:
        raise ValueError("--visual-threshold must be from 0 to 1")
    feature_reviews = load_json_argument(args.feature_reviews_json, "--feature-reviews-json")
    if feature_reviews is None:
        feature_reviews = []
    if not isinstance(feature_reviews, list):
        raise ValueError("--feature-reviews-json must be a JSON array")
    for index, review in enumerate(feature_reviews):
        if not isinstance(review, dict):
            raise ValueError(f"feature review {index} must be an object")
        if not isinstance(review.get("id"), str) or not review["id"].strip():
            raise ValueError(f"feature review {index}.id is required")
        score = review.get("score")
        if score is not None and (
            not isinstance(score, (int, float)) or not 0 <= float(score) <= 1
        ):
            raise ValueError(f"feature review {index}.score must be from 0 to 1")
    if args.pass_id in VISUAL_PASS_IDS and args.action == "continue":
        if not args.comparison_image:
            raise ValueError(
                "visual pass cannot use action=continue without --comparison-image; "
                "create one with stage4_review/make_comparison_sheet.py"
            )
        if args.ai_vision_score is None:
            raise ValueError(
                "visual pass cannot use action=continue without --ai-vision-score; "
                "AI vision must review the comparison sheet"
            )
        if clamp_score(args.ai_vision_score) < threshold:
            raise ValueError(
                f"AI vision score {clamp_score(args.ai_vision_score):.3f} is below threshold "
                f"{threshold:.3f}; choose refine-spec/refine-code/request-input instead of continue"
            )
        acceptance = visual_acceptance_config(spec)
        if acceptance.get("layerScoresRequired") is True and not layer_scores:
            raise ValueError("visual pass cannot use action=continue without --layer-scores-json")
        required_layers = acceptance.get("requiredLayerScores", [])
        if isinstance(required_layers, list) and layer_scores:
            missing_layers = [
                layer
                for layer in required_layers
                if isinstance(layer, str) and layer not in layer_scores
            ]
            if missing_layers:
                raise ValueError(
                    "--layer-scores-json is missing required layers: "
                    + ", ".join(missing_layers)
                )

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "passId": args.pass_id,
        "estimatedFidelity": clamp_score(args.fidelity),
        "aiVisionScore": clamp_score(args.ai_vision_score) if args.ai_vision_score is not None else None,
        "visualAcceptanceThreshold": threshold,
        "layerScores": layer_scores or {},
        "featureReviews": feature_reviews,
        "action": args.action,
        "summary": args.summary,
        "matched": split_items(args.matched),
        "mismatches": split_items(args.mismatches),
        "specFixes": split_items(args.spec_fixes),
        "codeFixes": split_items(args.code_fixes),
        "evidence": split_items(args.evidence),
    }
    if args.pass_id in VISUAL_PASS_IDS and args.action == "continue":
        feature_failures = feature_gate_failures(spec, entry, args.pass_id)
        if feature_failures:
            raise ValueError(
                "feature-level AI vision gate failed: " + "; ".join(feature_failures)
            )

    has_visual_evidence = any(
        [
            args.reference_screenshot,
            args.render_screenshot,
            args.comparison_image,
            args.camera_view,
            args.visual_notes,
            args.ai_vision_notes,
        ]
    )
    if has_visual_evidence:
        visual_evidence = {
            "referenceScreenshot": args.reference_screenshot or spec.get("sourceImage", ""),
            "renderScreenshot": args.render_screenshot or "",
            "comparisonImage": args.comparison_image or "",
            "cameraView": args.camera_view or "",
            "notes": args.visual_notes or "",
            "aiVisionNotes": args.ai_vision_notes or "",
        }
        entry["visualEvidence"] = visual_evidence

        visual_history = spec.setdefault("visualEvidence", [])
        if not isinstance(visual_history, list):
            raise ValueError("visualEvidence must be an array")
        visual_history.append(
            {
                "timestamp": entry["timestamp"],
                "passId": args.pass_id,
                "estimatedFidelity": entry["estimatedFidelity"],
                "aiVisionScore": entry["aiVisionScore"],
                "visualAcceptanceThreshold": entry["visualAcceptanceThreshold"],
                "layerScores": entry["layerScores"],
                "featureReviews": entry["featureReviews"],
                **visual_evidence,
            }
        )
    history.append(entry)
    sync_pipeline(spec)

    output = spec_path if args.in_place else (args.out.expanduser().resolve() if args.out else None)
    payload = json.dumps(spec, indent=2, ensure_ascii=False) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
        print(output)
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
