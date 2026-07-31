"""
Hybrid Synthesis Mode

Combines deterministic and agent-driven synthesis for robust multi-view analysis.
"""

from typing import Dict, Any, Optional, List
from pathlib import Path
import logging

from .synthesize import synthesize_geometry_brief
from .view_counter import count_views

logger = logging.getLogger(__name__)


def hybrid_synthesis(
    image_paths: List[Path],
    agent_analysis: Optional[Dict[str, Any]] = None,
    calibration: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run hybrid synthesis combining deterministic and agent-driven analysis.

    Args:
        image_paths: List of paths to reference images
        agent_analysis: Optional agent-driven analysis results

    Returns:
        Combined synthesis results
    """
    view_count = count_views(image_paths)

    try:
        deterministic_result = synthesize_geometry_brief(image_paths, calibration=calibration)
    except (OSError, RuntimeError, ValueError) as error:
        if agent_analysis is None:
            raise
        deterministic_result = _fallback_evidence_result(view_count, str(error))

    # If no agent analysis provided, return deterministic result
    if agent_analysis is None:
        return deterministic_result

    # Combine results
    combined = combine_results(deterministic_result, agent_analysis)

    return combined


def combine_results(
    deterministic: Dict[str, Any],
    agent: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Combine deterministic and agent-driven results.

    Args:
        deterministic: Deterministic synthesis results
        agent: Agent-driven analysis results

    Returns:
        Combined results
    """
    combined = deterministic.copy()

    # Merge confidence scores
    det_confidence = deterministic.get("confidence", 0.0)
    agent_confidence = agent.get("confidence", 0.0)

    # Weighted average (deterministic 0.6, agent 0.4)
    combined_confidence = (det_confidence * 0.6) + (agent_confidence * 0.4)

    # Merge components
    if "components" in agent:
        conflicts = find_component_conflicts(
            deterministic.get("components", {}),
            agent.get("components", {}),
        )
        combined["components"] = merge_components(
            deterministic.get("components", {}),
            agent.get("components", {}),
        )
        if conflicts:
            combined["conflicts"] = conflicts

    # Add agent notes
    if "notes" in agent:
        combined["agentNotes"] = agent["notes"]

    combined["synthesisMode"] = "hybrid"
    has_components = bool(combined.get("components"))
    calibrated_depth = deterministic.get("evidence", {}).get("calibratedDepthAvailable") is True
    combined["status"] = "complete" if calibrated_depth and has_components else "evidence-only"
    combined["confidence"] = (
        combined_confidence if calibrated_depth else min(0.49, combined_confidence)
    )
    combined["calibrated"] = calibrated_depth
    combined["agentCalibrationClaim"] = agent.get("calibrated") is True

    return combined


def find_component_conflicts(
    deterministic_components: Dict[str, Any],
    agent_components: Dict[str, Any],
) -> List[Dict[str, Any]]:
    conflicts: List[Dict[str, Any]] = []
    for component_id, deterministic_component in deterministic_components.items():
        agent_component = agent_components.get(component_id)
        if not isinstance(deterministic_component, dict) or not isinstance(agent_component, dict):
            continue
        deterministic_dimensions = deterministic_component.get("dimensions")
        agent_dimensions = agent_component.get("dimensions")
        if not isinstance(deterministic_dimensions, dict) or not isinstance(agent_dimensions, dict):
            continue
        for dimension, deterministic_value in deterministic_dimensions.items():
            agent_value = agent_dimensions.get(dimension)
            if not _dimension_values_conflict(deterministic_value, agent_value):
                continue
            conflicts.append(
                {
                    "component": component_id,
                    "dimension": dimension,
                    "deterministic": deterministic_value,
                    "agent": agent_value,
                    "action": "resolve-with-capture-evidence",
                }
            )
    return conflicts


def _dimension_values_conflict(deterministic_value: Any, agent_value: Any) -> bool:
    if isinstance(deterministic_value, bool) or isinstance(agent_value, bool):
        return False
    if not isinstance(deterministic_value, (int, float)):
        return False
    if not isinstance(agent_value, (int, float)):
        return False
    reference = max(abs(float(deterministic_value)), 1e-6)
    return abs(float(deterministic_value) - float(agent_value)) / reference > 0.1


def _fallback_evidence_result(view_count: int, failure: str) -> Dict[str, Any]:
    return {
        "viewCount": view_count,
        "inputViewCount": view_count,
        "duplicateViewCount": 0,
        "synthesisMode": "hybrid",
        "status": "evidence-only",
        "confidence": 0.0,
        "components": {},
        "evidence": {
            "featureCoverage": 0.0,
            "matchCoverage": 0.0,
            "matchConfidence": 0.0,
            "poseConfidence": 0.0,
            "depthConfidence": 0.0,
            "calibratedDepthAvailable": False,
        },
        "deterministicFailure": failure,
        "notes": "Deterministic synthesis failed; retaining agent evidence for review.",
    }


def merge_components(
    deterministic_components: Dict[str, Any],
    agent_components: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Merge components from deterministic and agent analysis.

    Args:
        deterministic_components: Components from deterministic synthesis
        agent_components: Components from agent analysis

    Returns:
        Merged components
    """
    merged = deterministic_components.copy()

    for component_name, agent_data in agent_components.items():
        if component_name in merged:
            # Merge existing component
            merged[component_name] = merge_single_component(
                merged[component_name],
                agent_data,
            )
        else:
            # Add new component from agent
            merged[component_name] = agent_data

    return merged


def merge_single_component(
    deterministic: Dict[str, Any],
    agent: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Merge a single component from deterministic and agent analysis.

    Args:
        deterministic: Component from deterministic synthesis
        agent: Component from agent analysis

    Returns:
        Merged component
    """
    merged = deterministic.copy()

    # Use agent dimensions if available (higher confidence)
    if "dimensions" in agent:
        merged["dimensions"] = agent["dimensions"]

    # Use agent curvature if available
    if "curvature" in agent:
        merged["curvature"] = agent["curvature"]

    # Merge confidence scores
    det_conf = deterministic.get("confidence", 0.0)
    agent_conf = agent.get("confidence", 0.0)
    merged["confidence"] = (det_conf * 0.6) + (agent_conf * 0.4)

    # Add agent notes
    if "notes" in agent:
        merged["agentNotes"] = agent["notes"]

    return merged


def create_agent_analysis_template() -> Dict[str, Any]:
    """
    Create a template for agent-driven analysis.

    Returns:
        Template dictionary
    """
    return {
        "viewCount": 0,
        "confidence": 0.0,
        "components": {},
        "notes": "",
        "observations": [],
        "inferences": [],
    }
