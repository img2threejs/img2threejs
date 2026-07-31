"""
Multi-View Brief Enhancement for Build

Enhances Three.js code generation with multi-view geometry brief data.
"""

from typing import Dict, Any, Optional, List
from pathlib import Path
import json
import logging
from copy import deepcopy

logger = logging.getLogger(__name__)


def enhance_build_with_brief(
    build_config: Dict[str, Any],
    brief: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Enhance build configuration with multi-view geometry brief.

    Args:
        build_config: Existing build configuration
        brief: Multi-view geometry brief

    Returns:
        Enhanced build configuration with brief data
    """
    enhanced_config = deepcopy(build_config)
    enhanced_config["multiViewBrief"] = {
        "viewCount": brief.get("viewCount", 0),
        "synthesisMode": brief.get("synthesisMode", "unknown"),
        "confidence": brief.get("confidence", 0.0),
    }

    # Evidence-only briefs intentionally carry no geometry components. Keep
    # their provenance above, but do not add an empty build component layer.
    brief_components = brief.get("components")
    if isinstance(brief_components, dict) and brief_components:
        enhanced_config["components"] = enhance_build_components(
            enhanced_config.get("components", {}),
            brief_components,
        )

    return enhanced_config


def enhance_build_components(
    build_components: Dict[str, Any],
    brief_components: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Enhance build components with brief data.

    Args:
        build_components: Components from build configuration
        brief_components: Components from geometry brief

    Returns:
        Enhanced components
    """
    enhanced = deepcopy(build_components)

    for component_name, brief_data in brief_components.items():
        if component_name in enhanced:
            # Enhance existing component
            enhanced[component_name] = enhance_single_build_component(
                enhanced[component_name],
                brief_data,
            )
        else:
            # Add new component from brief
            enhanced[component_name] = create_build_component_from_brief(
                component_name,
                brief_data,
            )

    return enhanced


def enhance_single_build_component(
    component: Dict[str, Any],
    brief_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Enhance a single build component with brief data.

    Args:
        component: Component from build configuration
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


def create_build_component_from_brief(
    component_name: str,
    brief_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Create a new build component from brief data.

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


def generate_geometry_from_brief(
    component_name: str,
    brief_data: Dict[str, Any],
) -> str:
    """
    Generate Three.js geometry code from brief data.

    Args:
        component_name: Name of the component
        brief_data: Component data from geometry brief

    Returns:
        Three.js geometry code string
    """
    dimensions = brief_data.get("dimensions", {})
    curvature = brief_data.get("curvature", "")

    # Generate basic geometry based on dimensions
    width = dimensions.get("width", 1.0)
    height = dimensions.get("height", 1.0)
    depth = dimensions.get("depth", 1.0)

    # Simple box geometry for now
    code = f"""
    // {component_name} - generated from multi-view brief
    const {component_name}Geometry = new THREE.BoxGeometry({width}, {height}, {depth});
    const {component_name}Material = new THREE.MeshStandardMaterial({{
      color: 0xffffff,
      roughness: 0.8,
      metalness: 0.0,
    }});
    const {component_name} = new THREE.Mesh({component_name}Geometry, {component_name}Material);
    """

    return code


def get_brief_for_build(brief_path: Optional[Path]) -> Optional[Dict[str, Any]]:
    """
    Load geometry brief for build process.

    Args:
        brief_path: Path to geometry brief JSON file

    Returns:
        Geometry brief dictionary or None if not found
    """
    if brief_path is None:
        return None

    try:
        with open(brief_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"Geometry brief not found: {brief_path}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in geometry brief: {e}")
        return None
