from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
VALID_STATUSES = {"evidence-only", "complete", "skipped", "failed"}
VALID_MODES = {"single-view", "basic", "standard", "full", "optimal", "hybrid"}
DIMENSION_KEYS = {"width", "height", "depth", "diameter", "radius", "length"}


def is_number(value: JsonValue) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_confidence(value: JsonValue) -> bool:
    return is_number(value) and 0.0 <= float(value) <= 1.0


def validate_geometry_brief(brief: Mapping[str, JsonValue]) -> list[str]:
    errors: list[str] = []
    status = brief.get("status")
    if status is not None and status not in VALID_STATUSES:
        errors.append("status must be evidence-only, complete, skipped, or failed")
    view_count = brief.get("viewCount")
    if not isinstance(view_count, int) or isinstance(view_count, bool) or view_count < 1:
        errors.append("viewCount must be an integer >= 1")
    mode = brief.get("synthesisMode")
    if not isinstance(mode, str) or mode not in VALID_MODES:
        errors.append("synthesisMode must be a supported mode")
    confidence = brief.get("confidence")
    if not is_confidence(confidence):
        errors.append("confidence must be a number from 0 to 1")
    named_views = brief.get("namedViews")
    if named_views is not None and (
        not isinstance(named_views, dict)
        or not all(isinstance(name, str) and isinstance(path, str) and path for name, path in named_views.items())
    ):
        errors.append("namedViews must map non-empty names to non-empty paths")
    components = brief.get("components")
    if components is not None and not isinstance(components, dict):
        errors.append("components must be an object")
        return errors
    if isinstance(components, dict):
        for component_id, component in components.items():
            component_errors = validate_component(component_id, component)
            errors.extend(component_errors)
    return errors


def validate_component(component_id: str, component: JsonValue) -> list[str]:
    label = f"components.{component_id}"
    if not isinstance(component, dict):
        return [f"{label} must be an object"]
    errors: list[str] = []
    visible_in = component.get("visibleIn")
    if not isinstance(visible_in, list) or not visible_in or not all(
        isinstance(view_name, str) and view_name for view_name in visible_in
    ):
        errors.append(f"{label}.visibleIn must be a non-empty array of strings")
    confidence = component.get("confidence")
    if not is_confidence(confidence):
        errors.append(f"{label}.confidence must be a number from 0 to 1")
    dimensions = component.get("dimensions")
    if dimensions is not None:
        if not isinstance(dimensions, dict):
            errors.append(f"{label}.dimensions must be an object")
        elif not any(key in dimensions for key in DIMENSION_KEYS):
            errors.append(f"{label}.dimensions must include a dimension")
        else:
            for key, value in dimensions.items():
                if key in DIMENSION_KEYS and (not is_number(value) or float(value) <= 0.0):
                    errors.append(f"{label}.dimensions.{key} must be a positive number")
    curvature = component.get("curvature")
    if curvature is not None and not isinstance(curvature, (str, dict)):
        errors.append(f"{label}.curvature must be a string or object")
    return errors
