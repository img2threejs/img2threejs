"""Per-feature verification for the img2threejs review stage.

A high *global* fidelity score can still hide a wrong or missing
identity-defining feature (a blade tip, a logo, a recurved hook). This
module verifies EACH declared feature independently against its own
threshold and fails the pass when a *gating* feature is missing or below
its threshold — even if the global score passed.

Operationalizes SKILL.md's rule: "fail a pass if an identity-defining
feature is wrong even when the global score looks fine."

Pure-logic module: it consumes already-computed per-feature scores and
decides pass/fail. It does NOT load or inspect images. Stdlib only.

Data shapes
-----------
feature_targets : list[dict]
    {"id": str, "tier": "critical"|"important"|"detail",
     "minimumScore": float (optional), "mustPass": bool (optional)}
feature_scores : dict[str, float | None]
    id -> score in [0, 1], or None meaning the feature's region showed
    no corresponding signal in the render (MISSING). A target id absent
    from this dict is treated the same as None (missing).
"""

import argparse
import json
import sys

TIER_DEFAULTS = {
    "critical": 0.8,
    "important": 0.65,
    "detail": 0.5,
}
UNKNOWN_TIER_DEFAULT = 0.5


def threshold_for(target):
    """Return the minimum score a feature must reach.

    Uses target["minimumScore"] when it is a number, otherwise the tier
    default (critical 0.8, important 0.65, detail 0.5, unknown 0.5).
    """
    minimum = target.get("minimumScore")
    if isinstance(minimum, bool):
        # bool is a subclass of int/float; never treat it as a threshold.
        minimum = None
    if isinstance(minimum, (int, float)):
        return float(minimum)
    tier = target.get("tier")
    return TIER_DEFAULTS.get(tier, UNKNOWN_TIER_DEFAULT)


def is_gating(target):
    """A feature gates the pass when it is critical OR flagged mustPass."""
    return target.get("tier") == "critical" or target.get("mustPass") is True


def evaluate_features(feature_targets, feature_scores):
    """Evaluate every declared feature independently.

    Returns a dict:
        {"passed": bool, "action": str,
         "features": [{"id","tier","score","threshold",
                       "status","gating"}, ...],
         "defects": [str, ...]}

    "passed" is True only if NO gating feature is missing or below its
    threshold. Non-gating features below threshold are reported but do
    not fail the pass.
    """
    feature_scores = feature_scores or {}

    features = []
    defects = []
    any_gating_missing = False
    any_gating_below = False

    for target in feature_targets:
        fid = target.get("id")
        tier = target.get("tier")
        gating = is_gating(target)
        threshold = threshold_for(target)

        # A target absent from the scores dict is treated as missing (None).
        score = feature_scores.get(fid) if fid in feature_scores else None

        if score is None:
            status = "missing"
            defects.append("missing-feature:%s" % fid)
            if gating:
                any_gating_missing = True
        elif score < threshold:
            status = "below"
            defects.append(
                "below-threshold:%s(%s<%s)" % (fid, _fmt(score), _fmt(threshold))
            )
            if gating:
                any_gating_below = True
        else:
            status = "ok"

        features.append(
            {
                "id": fid,
                "tier": tier,
                "score": score,
                "threshold": threshold,
                "status": status,
                "gating": gating,
            }
        )

    passed = not (any_gating_missing or any_gating_below)

    if passed:
        action = "continue"
    elif any_gating_missing:
        # A gating feature was never built — a spec problem.
        action = "refine-spec"
    else:
        # Built but below threshold — a manifestation problem.
        action = "refine-code"

    return {
        "passed": passed,
        "action": action,
        "features": features,
        "defects": defects,
    }


def _fmt(value):
    """Compact numeric formatting for defect strings (0.61, 0.75, ...)."""
    text = ("%.4f" % float(value)).rstrip("0").rstrip(".")
    return text if text else "0"


def _load_targets(path):
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        return data.get("featureReviewTargets", [])
    return data


def _load_scores(path):
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("scores file must be a JSON object {id: score-or-null}")
    return data


def _format_text(result):
    lines = []
    lines.append("passed: %s" % result["passed"])
    lines.append("action: %s" % result["action"])
    lines.append("features:")
    for feature in result["features"]:
        score = feature["score"]
        score_text = "missing" if score is None else _fmt(score)
        gating = " [gating]" if feature["gating"] else ""
        lines.append(
            "  - %s (%s): %s / %s -> %s%s"
            % (
                feature["id"],
                feature["tier"],
                score_text,
                _fmt(feature["threshold"]),
                feature["status"],
                gating,
            )
        )
    if result["defects"]:
        lines.append("defects:")
        for defect in result["defects"]:
            lines.append("  - %s" % defect)
    else:
        lines.append("defects: none")
    return "\n".join(lines)


def main(argv):
    parser = argparse.ArgumentParser(
        description="Per-feature verification for img2threejs review."
    )
    parser.add_argument("--targets", required=True, help="JSON file of feature targets")
    parser.add_argument("--scores", required=True, help="JSON file of feature scores")
    parser.add_argument("--json", action="store_true", help="emit JSON result")
    args = parser.parse_args(argv)

    try:
        targets = _load_targets(args.targets)
        scores = _load_scores(args.scores)
        result = evaluate_features(targets, scores)
    except Exception as exc:  # noqa: BLE001 - surface any failure cleanly
        sys.stderr.write("error: %s\n" % exc)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(_format_text(result))

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
