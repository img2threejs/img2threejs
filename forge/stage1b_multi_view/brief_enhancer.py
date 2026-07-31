"""
Multi-View Brief Enhancement for Spec Generation

Enhances sculpt specs with multi-view synthesis data.
"""

from typing import Dict, Any, Optional, List
from pathlib import Path
import json
import logging
from copy import deepcopy

logger = logging.getLogger(__name__)


def enhance_spec_with_brief(
    spec: Dict[str, Any],
    brief: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Enhance sculpt spec with multi-view geometry brief.

    Args:
        spec: Existing sculpt spec
        brief: Multi-view geometry brief

    Returns:
        Enhanced spec with brief data
    """
    enhanced_spec = deepcopy(spec)
    enhanced_spec["multiViewBrief"] = {
        "viewCount": brief.get("viewCount", 0),
        "synthesisMode": brief.get("synthesisMode", "unknown"),
        "confidence": brief.get("confidence", 0.0),
    }

    # Evidence-only briefs intentionally carry no geometry components. Keep
    # their provenance above, but do not run an empty component enhancement.
    brief_components = brief.get("components")
    if isinstance(brief_components, dict) and brief_components and "components" in enhanced_spec:
        enhanced_spec["components"] = enhance_components_with_brief(
            enhanced_spec["components"],
            brief_components,
        )

    return enhanced_spec


def enhance_components_with_brief(
    spec_components: Dict[str, Any],
    brief_components: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Enhance spec components with brief data.

    Args:
        spec_components: Components from sculpt spec
        brief_components: Components from geometry brief

    Returns:
        Enhanced components
    """
    enhanced = deepcopy(spec_components)

    for component_name, brief_data in brief_components.items():
        if component_name in enhanced:
            # Enhance existing component
            enhanced[component_name] = enhance_single_component(
                enhanced[component_name],
                brief_data,
            )
        else:
            # Add new component from brief
            enhanced[component_name] = create_component_from_brief(
                component_name,
                brief_data,
            )

    return enhanced


def enhance_single_component(
    component: Dict[str, Any],
    brief_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Enhance a single component with brief data.

    Args:
        component: Component from sculpt spec
        brief_data: Component data from geometry brief

    Returns:
        Enhanced component
    """
    enhanced = component.copy()

    # Update dimensions if available
    if "dimensions" in brief_data:
        if "dimensions" not in enhanced:
            enhanced["dimensions"] = {}
        enhanced["dimensions"].update(brief_data["dimensions"])

    # Update curvature if available
    if "curvature" in brief_data:
        enhanced["curvature"] = brief_data["curvature"]

    # Add confidence score
    if "confidence" in brief_data:
        enhanced["multiViewConfidence"] = brief_data["confidence"]

    # Add visibility information
    if "visibleIn" in brief_data:
        enhanced["visibleInViews"] = brief_data["visibleIn"]

    return enhanced


def create_component_from_brief(
    component_name: str,
    brief_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Create a new component from brief data.

    Args:
        component_name: Name of the component
        brief_data: Component data from geometry brief

    Returns:
        New component dictionary
    """
    component = {
        "name": component_name,
        "source": "multi-view-synthesis",
    }

    # Add dimensions if available
    if "dimensions" in brief_data:
        component["dimensions"] = brief_data["dimensions"]

    # Add curvature if available
    if "curvature" in brief_data:
        component["curvature"] = brief_data["curvature"]

    # Add confidence score
    if "confidence" in brief_data:
        component["multiViewConfidence"] = brief_data["confidence"]

    # Add visibility information
    if "visibleIn" in brief_data:
        component["visibleInViews"] = brief_data["visibleIn"]

    return component


def load_brief_from_file(brief_path: Path) -> Optional[Dict[str, Any]]:
    """
    Load geometry brief from file.

    Args:
        brief_path: Path to geometry brief JSON file

    Returns:
        Geometry brief dictionary or None if not found
    """
    try:
        with open(brief_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"Geometry brief not found: {brief_path}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in geometry brief: {e}")
        return None


def get_brief_summary(brief: Dict[str, Any]) -> str:
    """
    Get human-readable summary of geometry brief.

    Args:
        brief: Geometry brief dictionary

    Returns:
        Summary string
    """
    view_count = brief.get("viewCount", 0)
    mode = brief.get("synthesisMode", "unknown")
    confidence = brief.get("confidence", 0.0)
    component_count = len(brief.get("components", {}))

    return (
        f"Multi-view brief: {view_count} views, "
        f"mode={mode}, confidence={confidence:.2f}, "
        f"{component_count} components"
    )
