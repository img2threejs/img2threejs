from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def validate_release_evidence(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    target = payload.get("target")
    if not isinstance(target, str) or not target.strip():
        errors.append("target must be a non-empty string")
    baseline = payload.get("baseline")
    multi_view = payload.get("multiView")
    if not isinstance(baseline, dict):
        errors.append("baseline must be an object")
    else:
        _validate_baseline(baseline, errors)
    if not isinstance(multi_view, dict):
        errors.append("multiView must be an object")
    else:
        _validate_multi_view(multi_view, errors)
    return errors


def _validate_baseline(baseline: dict[str, Any], errors: list[str]) -> None:
    iteration_count = baseline.get("iterationCount")
    if not isinstance(iteration_count, int) or isinstance(iteration_count, bool) or iteration_count < 40:
        errors.append("baseline.iterationCount must be an integer >= 40")
    if not _is_non_empty_string(baseline.get("evidenceRef")):
        errors.append("baseline.evidenceRef must be a non-empty string")


def _validate_multi_view(multi_view: dict[str, Any], errors: list[str]) -> None:
    iteration_count = multi_view.get("iterationCount")
    if not isinstance(iteration_count, int) or isinstance(iteration_count, bool) or not 1 <= iteration_count <= 15:
        errors.append("multiView.iterationCount must be an integer from 1 to 15")
    source_images = multi_view.get("sourceImages")
    if not isinstance(source_images, list) or len(source_images) < 2 or not all(
        _is_non_empty_string(image) for image in source_images
    ):
        errors.append("multiView.sourceImages must contain at least two non-empty paths")
    review = multi_view.get("review")
    if not isinstance(review, dict):
        errors.append("multiView.review must be a Divine Eye multi-view result")
        return
    if review.get("complete") is not True or review.get("passed") is not True:
        errors.append("multiView.review must be complete and passed")
    per_view_results = review.get("perViewResults")
    if not isinstance(per_view_results, dict) or not per_view_results:
        errors.append("multiView.review.perViewResults must be a non-empty object")
        return
    for view_name, result in per_view_results.items():
        if not isinstance(result, dict) or result.get("status") != "compared" or result.get("verdict") != "pass":
            errors.append(f"multiView.review view {view_name!r} must be compared and pass")


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate empirical release evidence for multi-view synthesis.")
    parser.add_argument("evidence", type=Path, help="Path to multi-view release evidence JSON")
    args = parser.parse_args()
    try:
        payload = json.loads(args.evidence.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        parser.error(f"cannot read evidence JSON: {error}")
    if not isinstance(payload, dict):
        parser.error("evidence JSON must be an object")
    errors = validate_release_evidence(payload)
    if errors:
        print("FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
