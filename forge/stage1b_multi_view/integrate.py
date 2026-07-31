"""
Multi-View Synthesis Integration

This module provides integration between the multi-view synthesis pipeline
and the img2threejs intake process.

Usage:
    from forge.stage1b_multi_view.integrate import run_synthesis_for_intake

    # During intake processing
    synthesis_result = run_synthesis_for_intake(
        image_paths=[Path("front.png"), Path("back.png"), Path("top.png")],
        item_name="PS5 DualSense Controller",
    )
"""

from pathlib import Path
from typing import List, Dict, Any, Optional
import json
import logging
from copy import deepcopy

from .synthesize import synthesize_geometry_brief
from .hybrid_synthesis import hybrid_synthesis
from .view_counter import count_views

logger = logging.getLogger(__name__)


def run_synthesis_for_intake(
    image_paths: List[Path],
    item_name: str,
    output_dir: Optional[Path] = None,
    agent_analysis: Optional[Dict[str, Any]] = None,
    calibration: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run multi-view synthesis during intake process.

    Args:
        image_paths: List of paths to reference images
        item_name: Name of the item being processed
        output_dir: Optional directory to save synthesis results

    Returns:
        Synthesis result dictionary
    """
    view_count = count_views(image_paths)

    # Handle single-view fallback
    if view_count == 1:
        logger.info(f"Single view provided for {item_name} - skipping synthesis")
        return {
            "status": "skipped",
            "reason": "single-view",
            "viewCount": 1,
            "synthesisMode": "single-view",
            "confidence": 0.3,
        }

    # Run synthesis
    logger.info(f"Running multi-view synthesis for {item_name} with {view_count} views")

    try:
        # Determine output path
        output_path = None
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            safe_name = item_name.lower().replace(" ", "_").replace("-", "_")
            output_path = output_dir / f"{safe_name}_geometry_brief.json"

        # Run synthesis
        brief = (
            hybrid_synthesis(image_paths, agent_analysis, calibration)
            if agent_analysis is not None
            else synthesize_geometry_brief(image_paths, calibration=calibration)
        )
        if output_path:
            output_path.write_text(json.dumps(brief, indent=2), encoding="utf-8")

        result = {
            "status": brief.get("status", "evidence-only"),
            "viewCount": brief.get("viewCount", view_count),
            "synthesisMode": brief.get("synthesisMode", "unknown"),
            "confidence": brief.get("confidence", 0.0),
            "brief": brief,
            "outputPath": str(output_path) if output_path else None,
        }

        logger.info(
            f"Synthesis complete for {item_name}: "
            f"mode={result['synthesisMode']}, "
            f"confidence={result['confidence']:.2f}"
        )

        return result

    except Exception as e:
        logger.error(f"Synthesis failed for {item_name}: {e}")
        return {
            "status": "failed",
            "error": str(e),
            "viewCount": view_count,
            "synthesisMode": "fallback",
            "confidence": 0.0,
        }


def enhance_intake_record_with_synthesis(
    intake_record: Dict[str, Any],
    synthesis_result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Enhance intake record with synthesis results.

    Args:
        intake_record: Existing intake record
        synthesis_result: Result from run_synthesis_for_intake

    Returns:
        Updated intake record
    """
    enhanced_record = deepcopy(intake_record)
    enhanced_record["synthesis"] = {
        "viewCount": synthesis_result.get("viewCount", 0),
        "synthesisMode": synthesis_result.get("synthesisMode", "unknown"),
        "confidence": synthesis_result.get("confidence", 0.0),
        "status": synthesis_result.get("status", "unknown"),
    }

    # Add brief if available
    if synthesis_result.get("brief"):
        enhanced_record["synthesis"]["brief"] = deepcopy(synthesis_result["brief"])

    return enhanced_record


def get_synthesis_summary(synthesis_result: Dict[str, Any]) -> str:
    """
    Get human-readable summary of synthesis result.

    Args:
        synthesis_result: Result from run_synthesis_for_intake

    Returns:
        Summary string
    """
    status = synthesis_result.get("status", "unknown")
    view_count = synthesis_result.get("viewCount", 0)
    mode = synthesis_result.get("synthesisMode", "unknown")
    confidence = synthesis_result.get("confidence", 0.0)

    if status == "skipped":
        return f"Single view provided - using code-written geometry"
    elif status == "failed":
        error = synthesis_result.get("error", "unknown error")
        return f"Synthesis failed ({error}) - falling back to code geometry"
    elif status in {"complete", "evidence-only"}:
        label = "evidence extracted" if status == "evidence-only" else "complete"
        return (
            f"Multi-view synthesis {label}: {view_count} views, "
            f"mode={mode}, confidence={confidence:.2f}"
        )
    else:
        return f"Synthesis status: {status}"
