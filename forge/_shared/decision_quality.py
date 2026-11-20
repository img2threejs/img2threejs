"""Deterministic decisions over supplied measurements, not a renderer or capturer.

The caller admits revision-bound reports and supplies the complete gate/feature
inventory. This module checks their contents; hashes are binding identifiers, not
proof that this module opened an image. Policy v1 is immutable. Ranking is
lexicographic in the policy's dimension order, never a weighted average. Critical
scores use sorted feature ids; candidate id breaks exact ties, never input order.
Repair criteria must be frozen by the caller before executing the trial.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
import math
from pathlib import Path
import re


@lru_cache(maxsize=1)
def _default_policy():
    with Path(__file__).with_name("route_quality_policy_v1.json").open(encoding="utf-8") as stream:
        return json.load(stream)


def _policy(policy):
    frozen = _default_policy()
    if policy is not None:
        try:
            supplied = json.dumps(policy, sort_keys=True, allow_nan=False)
            expected = json.dumps(frozen, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("policy-mismatch") from None
        if supplied != expected:
            raise ValueError("policy-mismatch")
    return frozen


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _status(checks):
    if any(check["status"] == "fail" for check in checks):
        return "fail"
    if any(check["status"] != "pass" for check in checks):
        return "unevaluated"
    return "pass"


def _check(checks, name, status, reason=None):
    checks.append({"id": name, "status": status, "reason": reason or name + ":" + status})


def _result(candidate, checks, hard, semantic, version):
    status = _status(checks)
    return {
        "id": candidate.get("id"), "status": status,
        "passed": status == "pass", "deliverable": status == "pass",
        "hardStatus": hard, "semanticStatus": semantic,
        "policyVersion": version, "checks": checks,
        "reasons": [check["reason"] for check in checks if check["status"] != "pass"],
        "evidence": deepcopy(candidate.get("evidence")),
        "report": deepcopy(candidate),
    }


def assess_candidate(candidate, policy=None) -> dict:
    """Assess measured gates before semantic review; absent evidence never passes.

    ``features: []`` is an explicit empty inventory; an absent inventory is not.
    Each supplied gate is blocking. A gate needs an id, producer and known status.
    A feature's authored 0..4 threshold is preserved, with a 3/4 critical floor;
    callers adapting older 0..1 contracts must convert, not lower, their threshold.
    """
    candidate = candidate if isinstance(candidate, dict) else {}
    checks = []
    try:
        rules = _policy(policy)
    except ValueError:
        _check(checks, "policy", "unevaluated", "policy-mismatch")
        return _result(candidate, checks, "unevaluated", "unevaluated", None)
    for field in ("id", "outputKind"):
        _check(checks, field, "pass" if _text(candidate.get(field)) else "unevaluated")
    evidence = candidate.get("evidence")
    for field in ("referenceSha256", "cameraSha256", "viewSetSha256"):
        value = evidence.get(field) if isinstance(evidence, dict) else None
        valid = isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None
        _check(checks, "evidence:" + field, "pass" if valid else "unevaluated")
    views = candidate.get("views")
    valid_views = isinstance(views, list) and all(_number(view) for view in views)
    _check(checks, "views", "pass" if valid_views and all(
        view in views for view in rules["requiredViews"]) else "unevaluated")

    gates = candidate.get("gateResults")
    if not isinstance(gates, list) or not gates:
        _check(checks, "gates", "unevaluated", "gates:missing-blocking-report")
    else:
        seen = set()
        for index, gate in enumerate(gates):
            if not isinstance(gate, dict):
                _check(checks, "gate:" + str(index), "unevaluated")
                continue
            name = gate.get("id")
            valid = _text(name) and name not in seen and _text(gate.get("producer"))
            if _text(name):
                seen.add(name)
            status = gate.get("status")
            if not valid or status not in ("pass", "fail", "uncertain", "unevaluated"):
                status = "unevaluated"
            if status == "uncertain":
                status = "unevaluated"
            _check(checks, "gate:" + str(name), status)

    measurements = candidate.get("measurements")
    measurements = measurements if isinstance(measurements, dict) else {}
    valid_numbers = {}
    for name in ("width", "height", "depth", "frontArea", "sideArea", "selfIntersectionCount"):
        value = measurements.get(name)
        valid = _number(value)
        valid_numbers[name] = valid
        if not valid:
            status = "unevaluated"
        elif name in ("width", "height", "depth", "frontArea"):
            status = "pass" if value > 0 else "fail"
        elif name == "selfIntersectionCount":
            status = "pass" if value >= 0 and value == int(value) else "unevaluated"
        else:
            status = "pass" if value >= 0 else "fail"
        _check(checks, "measurement:" + name, status)
    count = measurements.get("selfIntersectionCount")
    if valid_numbers["selfIntersectionCount"] and count >= 0 and count == int(count):
        _check(checks, "geometry:selfIntersections", "pass" if count <= rules["maximumSelfIntersections"] else "fail")

    flatness = candidate.get("flatness")
    if flatness not in ("flat", "volumetric"):
        _check(checks, "flatness", "unevaluated")
    else:
        extent_valid = all(valid_numbers[name] and measurements[name] > 0 for name in ("width", "height", "depth"))
        ratio = measurements["depth"] / max(measurements["width"], measurements["height"]) if extent_valid else None
        minimum = rules["flat"]["minimumThicknessRatio"] if flatness == "flat" else rules["volumetric"]["minimumDepthRatio"]
        _check(checks, "geometry:depth", "unevaluated" if not _number(ratio) else "pass" if ratio >= minimum else "fail")
        if flatness == "flat":
            for name in ("closed", "twoSided"):
                value = measurements.get(name)
                required = rules["flat"]["requireClosed" if name == "closed" else "requireTwoSided"]
                status = "unevaluated" if type(value) is not bool else "pass" if value is required else "fail"
                _check(checks, "geometry:" + name, status)
        else:
            areas_valid = valid_numbers["frontArea"] and valid_numbers["sideArea"] and measurements["frontArea"] > 0 and measurements["sideArea"] >= 0
            ratio = measurements["sideArea"] / measurements["frontArea"] if areas_valid else None
            _check(checks, "geometry:sideArea", "unevaluated" if not _number(ratio) else "pass" if ratio >= rules["volumetric"]["minimumSideFrontRatio"] else "fail")
            covered = measurements.get("partCoveragePassed")
            _check(checks, "geometry:partCoverage", "unevaluated" if type(covered) is not bool else "pass" if covered else "fail")
    hard = _status(checks)

    semantic = []
    features = candidate.get("features")
    if not isinstance(features, list):
        _check(semantic, "features", "unevaluated", "features:missing-inventory")
    else:
        seen = set()
        for index, feature in enumerate(features):
            if not isinstance(feature, dict):
                _check(semantic, "feature:" + str(index), "unevaluated")
                continue
            name = feature.get("id")
            tier = feature.get("tier")
            score, threshold = feature.get("score"), feature.get("threshold")
            valid = (_text(name) and name not in seen and tier in ("critical", "important")
                     and _number(score) and 0 <= score <= rules["scoreMaximum"]
                     and _number(threshold) and 0 <= threshold <= rules["scoreMaximum"])
            if _text(name):
                seen.add(name)
            status = feature.get("status")
            if not valid or status not in ("pass", "fail", "uncertain", "unevaluated") or status in ("uncertain", "unevaluated"):
                status = "unevaluated"
            elif tier == "critical":
                threshold = max(threshold, rules["criticalScoreFloor"])
                status = "pass" if status == "pass" and score >= threshold else "fail"
            else:
                # Important findings remain visible without becoming critical gates.
                status = "pass"
            _check(semantic, "feature:" + str(name), status)
    checks.extend(semantic)

    metrics = candidate.get("metrics")
    for name in rules["protectedMetrics"]:
        value = metrics.get(name) if isinstance(metrics, dict) else None
        _check(checks, "metric:" + name, "pass" if _number(value) and 0 <= value <= 1 else "unevaluated")
    for name in ("cost", "timeMs"):
        value = candidate.get(name)
        _check(checks, name, "pass" if _number(value) and value >= 0 else "unevaluated")
    return _result(candidate, checks, hard, _status(semantic), rules["version"])


def _comparison_key(candidate):
    evidence = candidate["evidence"]
    features = tuple(sorted((feature["id"], feature["tier"], feature["threshold"]) for feature in candidate["features"]))
    return (candidate["outputKind"], candidate["flatness"],
            *(evidence[name].lower() for name in ("referenceSha256", "cameraSha256", "viewSetSha256")), features)


def _incomparable(assessment, reason):
    assessment.update(status="unevaluated", passed=False, deliverable=False, rank=None)
    assessment["reasons"].append(reason)
    return assessment


def rank_candidates(candidates, policy=None) -> list:
    """Return ranked accepted reports followed by unranked ineligible reports.

    Mixed binding/output/rubric cohorts have no winner. Every returned entry keeps
    the raw report and assessment; only a pass with an integer rank is selectable.
    """
    if not isinstance(candidates, list):
        return [_incomparable(assess_candidate({}, policy), "candidates:invalid-list")]
    assessments = [assess_candidate(candidate, policy) for candidate in candidates]
    for assessment in assessments:
        assessment["rank"] = None
    accepted = [assessment for assessment in assessments if assessment["passed"]]
    ids = [assessment["id"] for assessment in assessments]
    duplicate_ids = len([value for value in ids if isinstance(value, str)]) != len(set(value for value in ids if isinstance(value, str)))
    if duplicate_ids:
        return [_incomparable(assessment, "candidates:duplicate-id") for assessment in assessments]
    try:
        cohorts = {_comparison_key(assessment["report"]) for assessment in assessments}
    except (KeyError, TypeError, AttributeError):
        return [_incomparable(assessment, "comparison:missing-comparison-contract") for assessment in assessments]
    if len(cohorts) > 1:
        return [_incomparable(assessment, "comparison:unmatched-output-evidence-or-rubric") for assessment in assessments]
    if not accepted:
        return assessments
    rules = _policy(policy)

    def key(assessment):
        candidate = assessment["report"]
        values = []
        for dimension in rules["ranking"]:
            name = dimension["metric"]
            if name == "criticalFeatures":
                value = tuple(-feature["score"] for feature in sorted(candidate["features"], key=lambda feature: feature["id"]) if feature["tier"] == "critical")
            else:
                value = candidate[name] if name in ("cost", "timeMs") else candidate["metrics"][name]
                if dimension["direction"] == "max":
                    value = -value
            values.append(value)
        return (*values, candidate["id"])

    accepted.sort(key=key)
    for index, assessment in enumerate(accepted, 1):
        assessment["rank"] = index
    return accepted + [assessment for assessment in assessments if not assessment["passed"]]


def _metric(candidate, name):
    if name in ("cost", "timeMs"):
        return candidate.get(name)
    section = "metrics"
    if "." in name:
        section, name = name.split(".", 1)
    elif name in ("width", "height", "depth", "frontArea", "sideArea", "selfIntersectionCount"):
        section = "measurements"
    fields = {
        "metrics": ("silhouetteIoU", "interiorDifference", "orbitConsistency"),
        "measurements": ("width", "height", "depth", "frontArea", "sideArea", "selfIntersectionCount"),
    }
    if name not in fields.get(section, ()):
        return None
    values = candidate.get(section)
    return values.get(name) if isinstance(values, dict) else None


def compare_repair(before, after, criteria, policy=None) -> dict:
    """Retain only measured improvement with explicit protected tolerances.

    Hard/critical failures are legitimate repair inputs. A retained revision may
    still fail acceptance: ``retained`` is not ``passed`` or ``deliverable``.
    Missing, changed-contract and incomparable evidence yields unevaluated.
    """
    first, second = assess_candidate(before, policy), assess_candidate(after, policy)
    result = {"status": "unevaluated", "retained": False, "passed": False,
              "deliverable": False, "reasons": [], "before": first, "after": second}
    reasons = result["reasons"]
    try:
        rules = _policy(policy)
    except ValueError:
        reasons.append("policy-mismatch")
        return result
    if not isinstance(before, dict) or not isinstance(after, dict):
        reasons.append("comparison:missing-report")
        return result
    unknown = [check for assessment in (first, second) for check in assessment["checks"] if check["status"] == "unevaluated"]
    if unknown:
        reasons.extend(check["reason"] for check in unknown)
        return result
    if _comparison_key(before) != _comparison_key(after):
        reasons.append("comparison:unmatched-output-evidence-or-rubric")
        return result
    old_gates = {gate["id"] for gate in before["gateResults"]}
    new_gates = {gate["id"] for gate in after["gateResults"]}
    if old_gates != new_gates:
        reasons.append("comparison:changed-gate-inventory")
        return result
    if not isinstance(criteria, dict):
        reasons.append("criteria:missing")
        return result
    target, direction = criteria.get("targetMetric"), criteria.get("direction")
    minimum, tolerances = criteria.get("minImprovement"), criteria.get("tolerances")
    if not _text(target) or direction not in ("min", "max") or not _number(minimum) or minimum < 0 or not isinstance(tolerances, dict):
        reasons.append("criteria:invalid")
        return result
    target_name = target.split(".")[-1]
    normalized = {name.split(".")[-1] for name in tolerances if isinstance(name, str)}
    if not (set(rules["protectedMetrics"]) - {target_name}).issubset(normalized):
        reasons.append("criteria:missing-protected-tolerances")
        return result
    old, new = _metric(before, target), _metric(after, target)
    if not _number(old) or not _number(new):
        reasons.append("target:missing-measurement")
        return result
    improvement = new - old if direction == "max" else old - new
    if not math.isfinite(improvement):
        reasons.append("target:nonfinite-improvement")
        return result
    result["improvement"] = improvement
    regressions = []
    for name, tolerance in tolerances.items():
        metric_direction = rules["metricDirections"].get(name.split(".")[-1]) if isinstance(name, str) else None
        old_value, new_value = _metric(before, name) if isinstance(name, str) else None, _metric(after, name) if isinstance(name, str) else None
        if metric_direction is None or not _number(tolerance) or tolerance < 0 or not _number(old_value) or not _number(new_value):
            reasons.append("tolerance:invalid-or-unmeasured:" + str(name))
            return result
        regression = old_value - new_value if metric_direction == "max" else new_value - old_value
        if not math.isfinite(regression):
            reasons.append("tolerance:nonfinite-regression:" + name)
            return result
        if regression > tolerance:
            regressions.append("regression:" + name)
    previous = {check["id"]: check for check in first["checks"]}
    for check in second["checks"]:
        protected = check["id"].startswith(("gate:", "geometry:", "measurement:"))
        if check["id"].startswith("feature:"):
            feature_id = check["id"][len("feature:"):]
            protected = any(feature["id"] == feature_id and feature["tier"] == "critical" for feature in before["features"])
        if protected and previous.get(check["id"], {}).get("status") == "pass" and check["status"] != "pass":
            regressions.append("regression:" + check["id"])
    if improvement <= 0 or improvement < minimum:
        regressions.append("target:insufficient-improvement")
    if regressions:
        result.update(status="reject", reasons=regressions)
        return result
    result.update(status="retain", retained=True, passed=second["passed"], deliverable=second["deliverable"])
    reasons.append("repair:measured-improvement" if second["passed"] else "repair:improved-but-not-deliverable")
    return result
