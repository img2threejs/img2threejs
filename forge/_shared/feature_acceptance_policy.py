#!/usr/bin/env python3
"""Shared feature-level acceptance logic for visual sculpt passes."""

from __future__ import annotations

from typing import Any


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def feature_review_policy(spec: dict[str, Any]) -> dict[str, Any]:
    loop = spec.get("selfCorrectLoop")
    if not isinstance(loop, dict):
        return {}
    acceptance = loop.get("visualAcceptance")
    if not isinstance(acceptance, dict):
        return {}
    policy = acceptance.get("featureReviewPolicy")
    return policy if isinstance(policy, dict) else {}


def feature_targets_for_pass(spec: dict[str, Any], pass_id: str) -> list[dict[str, Any]]:
    targets = spec.get("featureReviewTargets", [])
    if not isinstance(targets, list):
        return []
    applicable: list[dict[str, Any]] = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        pass_ids = target.get("passIds", [])
        if isinstance(pass_ids, list) and pass_id in pass_ids:
            applicable.append(target)
    return applicable


def feature_gate_failures(
    spec: dict[str, Any],
    entry: dict[str, Any],
    pass_id: str,
) -> list[str]:
    policy = feature_review_policy(spec)
    if policy.get("enabled") is not True:
        return []

    targets = feature_targets_for_pass(spec, pass_id)
    critical = [
        target
        for target in targets
        if target.get("tier") == "critical" or target.get("mustPass") is True
    ]
    max_critical = policy.get("maxCriticalFeaturesPerPass", 5)
    failures: list[str] = []
    if is_number(max_critical) and len(critical) > int(max_critical):
        failures.append(
            f"pass {pass_id!r} defines {len(critical)} critical features; "
            f"group them into at most {int(max_critical)} semantic systems"
        )
    important = [target for target in targets if target.get("tier") == "important"]
    max_important = policy.get("maxImportantFeaturesPerPass", 3)
    if is_number(max_important) and len(important) > int(max_important):
        failures.append(
            f"pass {pass_id!r} defines {len(important)} important features; "
            f"keep only the {int(max_important)} most uncertain or high-value systems"
        )

    reviews = entry.get("featureReviews", [])
    review_by_id = {
        review.get("id"): review
        for review in reviews
        if isinstance(review, dict) and isinstance(review.get("id"), str)
    } if isinstance(reviews, list) else {}

    default_threshold = policy.get("criticalDefaultThreshold", 0.8)
    for target in critical:
        target_id = target.get("id")
        if not isinstance(target_id, str) or not target_id:
            continue
        review = review_by_id.get(target_id)
        if not isinstance(review, dict):
            failures.append(f"critical feature {target_id!r} has no AI vision review")
            continue
        if review.get("visible") is False:
            failures.append(f"critical feature {target_id!r} is not visible in the review view")
            continue
        score = review.get("score")
        minimum = target.get("minimumScore", default_threshold)
        if not is_number(score):
            failures.append(f"critical feature {target_id!r} has no numeric score")
        elif not is_number(minimum) or float(score) < float(minimum):
            failures.append(
                f"critical feature {target_id!r} score {score} is below {minimum}"
            )
    important_ids = {
        target.get("id")
        for target in targets
        if target.get("tier") == "important" and isinstance(target.get("id"), str)
    }
    important_scores = [
        float(review["score"])
        for feature_id, review in review_by_id.items()
        if feature_id in important_ids and is_number(review.get("score"))
    ]
    important_threshold = policy.get("importantAverageThreshold", 0.65)
    if important_scores and is_number(important_threshold):
        average = sum(important_scores) / len(important_scores)
        if average < float(important_threshold):
            failures.append(
                f"reviewed important features average {average:.3f} is below "
                f"{float(important_threshold):.3f}"
            )
    return failures
