"""
Multi-View Review Enhancement

Enhances the Divine Eye review process with multi-view comparison.
"""

from typing import Callable, Dict, Any, Optional, List, Tuple
from pathlib import Path
import json
import logging

logger = logging.getLogger(__name__)

ViewEvaluator = Callable[[Path, Path], Dict[str, Any]]


def enhance_review_with_multi_view(
    review_config: Dict[str, Any],
    brief: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Enhance review configuration with multi-view data.

    Args:
        review_config: Existing review configuration
        brief: Multi-view geometry brief

    Returns:
        Enhanced review configuration with multi-view data
    """
    # Add multiViewBrief field to review config
    review_config["multiViewBrief"] = {
        "viewCount": brief.get("viewCount", 0),
        "synthesisMode": brief.get("synthesisMode", "unknown"),
        "confidence": brief.get("confidence", 0.0),
        "namedViews": brief.get("namedViews", {}),
    }

    # Add view coverage information
    review_config["viewCoverage"] = calculate_view_coverage(brief)

    return review_config


def calculate_view_coverage(brief: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate view coverage for each component.

    Args:
        brief: Multi-view geometry brief

    Returns:
        View coverage information
    """
    coverage = {
        "totalViews": brief.get("viewCount", 0),
        "componentCoverage": {},
    }

    components = brief.get("components", {})
    for component_name, component_data in components.items():
        visible_in = component_data.get("visibleIn", [])
        coverage["componentCoverage"][component_name] = {
            "visibleInViewCount": len(visible_in),
            "visibleInViews": visible_in,
            "coveragePercentage": (len(visible_in) / max(1, brief.get("viewCount", 1))) * 100,
        }

    return coverage


def compare_multi_view(
    render_paths: Dict[str, Path],
    reference_paths: Dict[str, Path],
    *,
    evaluator: ViewEvaluator,
) -> Dict[str, Any]:
    """
    Compare render against multiple reference views.

    Args:
        render_paths: Dictionary mapping view names to render paths
        reference_paths: Dictionary mapping view names to reference paths

    Returns:
        Multi-view comparison results
    """
    results = {
        "viewCount": len(reference_paths),
        "perViewResults": {},
        "aggregatedScore": 0.0,
        "complete": False,
        "passed": False,
        "missingViews": [],
        "failedViews": [],
        "worstView": None,
        "worstScore": 0.0,
    }

    for view_name, reference_path in reference_paths.items():
        if view_name in render_paths:
            render_path = render_paths[view_name]
            view_result = compare_single_view(render_path, reference_path, evaluator=evaluator)
            results["perViewResults"][view_name] = view_result
        else:
            results["perViewResults"][view_name] = {
                "status": "missing",
                "score": 0.0,
            }
            results["missingViews"].append(view_name)

    results["complete"] = not results["missingViews"] and all(
        result.get("status") == "compared"
        for result in results["perViewResults"].values()
    )
    results["failedViews"] = [
        view_name
        for view_name, result in results["perViewResults"].items()
        if result.get("status") != "compared" or result.get("verdict") != "pass"
    ]
    results["passed"] = results["complete"] and not results["failedViews"]
    results["aggregatedScore"] = aggregate_multi_view_scores(
        results["perViewResults"]
    )
    if results["perViewResults"]:
        worst_view, worst_result = min(
            results["perViewResults"].items(),
            key=lambda item: item[1].get("score", 0.0),
        )
        results["worstView"] = worst_view
        results["worstScore"] = worst_result.get("score", 0.0)

    return results


def compare_single_view(
    render_path: Path,
    reference_path: Path,
    *,
    evaluator: ViewEvaluator,
) -> Dict[str, Any]:
    """
    Compare render against a single reference view.

    Args:
        render_path: Path to render image
        reference_path: Path to reference image

    Returns:
        Single view comparison result
    """
    if not render_path.is_file() or not reference_path.is_file():
        return {
            "status": "missing",
            "renderPath": str(render_path),
            "referencePath": str(reference_path),
            "score": 0.0,
        }

    try:
        evaluation = evaluator(reference_path, render_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("Multi-view comparison failed: %s", exc)
        return {
            "status": "error",
            "renderPath": str(render_path),
            "referencePath": str(reference_path),
            "score": 0.0,
            "error": str(exc),
        }

    signals = evaluation.get("signals", {})
    return {
        "status": "compared",
        "renderPath": str(render_path),
        "referencePath": str(reference_path),
        "score": evaluation.get("fidelity", 0.0),
        "verdict": evaluation.get("verdict", "unknown"),
        "action": evaluation.get("action", "probe"),
        "silhouetteIoU": signals.get("silhouetteIoU", 0.0),
        "proportionDelta": signals.get("aspectRatioDelta", 1.0),
        "hardGateFailures": evaluation.get("hardGateFailures", []),
    }


def aggregate_multi_view_scores(
    per_view_results: Dict[str, Any],
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """
    Aggregate scores from multiple views.

    Args:
        per_view_results: Per-view comparison results
        weights: Optional weights for each view

    Returns:
        Aggregated score
    """
    if not per_view_results:
        return 0.0

    # Equal weighting is the safe default. Primary-view policy belongs to the
    # caller because projects use names such as "front-primary".
    if weights is None:
        weights = {view_name: 1.0 for view_name in per_view_results}

    total_weight = 0.0
    weighted_score = 0.0

    for view_name, result in per_view_results.items():
        weight = weights.get(view_name, 1.0)
        score = result.get("score", 0.0)
        weighted_score += score * weight
        total_weight += weight

    if total_weight < 1e-6:
        return 0.0

    return weighted_score / total_weight


def generate_multi_view_report(
    comparison_results: Dict[str, Any],
    brief: Dict[str, Any],
) -> str:
    """
    Generate human-readable multi-view comparison report.

    Args:
        comparison_results: Multi-view comparison results
        brief: Multi-view geometry brief

    Returns:
        Report string
    """
    lines = []
    lines.append("## Multi-View Comparison Report")
    lines.append("")

    # Summary
    view_count = comparison_results.get("viewCount", 0)
    aggregated_score = comparison_results.get("aggregatedScore", 0.0)
    lines.append(f"**Views Compared:** {view_count}")
    lines.append(f"**Aggregated Score:** {aggregated_score:.3f}")
    lines.append("")

    # Per-view results
    lines.append("### Per-View Results")
    lines.append("")
    for view_name, result in comparison_results.get("perViewResults", {}).items():
        status = result.get("status", "unknown")
        score = result.get("score", 0.0)
        lines.append(f"- **{view_name}:** {status} (score: {score:.3f})")

    lines.append("")

    # View coverage
    coverage = brief.get("viewCount", 0)
    lines.append(f"**Total Views in Brief:** {coverage}")

    return "\n".join(lines)
