"""Validation for a human-reviewed source-view alignment manifest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model import DenseEvidenceError, InfluenceScope, finite_number


ALIGNMENT_PROFILE_VERSION = "source-view-alignment-v1"
MIN_GLOBAL_IOU = 0.65
MIN_COMPONENT_IOU = 0.75
MAX_ASPECT_RATIO_ERROR = 0.15


@dataclass(frozen=True)
class ValidatedAlignment:
    payload: dict[str, Any]
    transform: tuple[float, ...]
    maximum_scope: InfluenceScope


def _finite_unit(value: object, field: str) -> float:
    result = finite_number(value, field)
    if not 0.0 <= result <= 1.0:
        raise DenseEvidenceError("malformed_input", f"{field} must be in [0, 1]")
    return result


def _maximum_scope(
    iou: float,
    aspect_error: float,
    semantic_status: str,
    chirality_status: str,
) -> InfluenceScope:
    if aspect_error > MAX_ASPECT_RATIO_ERROR or iou < MIN_GLOBAL_IOU:
        raise DenseEvidenceError(
            "alignment_failed", "source-view thresholds were not met"
        )
    if (
        semantic_status == "sufficient"
        and chirality_status == "reviewed"
        and iou >= MIN_COMPONENT_IOU
    ):
        return InfluenceScope.COMPONENT_MEASUREMENTS
    return InfluenceScope.GLOBAL_MASSING


def validate_alignment(
    alignment: dict[str, object], semantic_status: str
) -> ValidatedAlignment:
    if alignment.get("schemaVersion") != 1:
        raise DenseEvidenceError("malformed_input", "alignment schemaVersion must be 1")
    if alignment.get("profileVersion") != ALIGNMENT_PROFILE_VERSION:
        raise DenseEvidenceError("malformed_input", "unsupported alignment profile")
    transform_value = alignment.get("sourceViewTransform")
    if not isinstance(transform_value, list) or len(transform_value) != 16:
        raise DenseEvidenceError(
            "malformed_input", "sourceViewTransform must contain 16 numbers"
        )
    transform = tuple(
        finite_number(value, f"sourceViewTransform[{index}]")
        for index, value in enumerate(transform_value)
    )
    if alignment.get("upAxis") != "+Y" or alignment.get("forwardAxis") != "+Z":
        raise DenseEvidenceError("alignment_failed", "canonical axes must be +Y up and +Z forward")
    if alignment.get("handedness") != "right-handed":
        raise DenseEvidenceError("alignment_failed", "output must be right-handed")
    audit = alignment.get("axisOperationAudit")
    if not isinstance(audit, list) or not audit or not all(
        isinstance(item, str) and item.strip() for item in audit
    ):
        raise DenseEvidenceError("malformed_input", "axisOperationAudit is required")
    chirality = alignment.get("chiralityStatus")
    if chirality not in {"reviewed", "ambiguous"}:
        raise DenseEvidenceError(
            "malformed_input", "chiralityStatus must be reviewed or ambiguous"
        )
    captures = alignment.get("browserCaptures")
    if not isinstance(captures, list) or not captures:
        raise DenseEvidenceError("malformed_input", "browserCaptures are required")
    for capture in captures:
        if not isinstance(capture, dict):
            raise DenseEvidenceError("malformed_input", "browser capture must be an object")
        if not isinstance(capture.get("path"), str) or not isinstance(capture.get("view"), str):
            raise DenseEvidenceError("malformed_input", "browser capture path and view are required")
        digest = capture.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise DenseEvidenceError("malformed_input", "browser capture SHA-256 is invalid")
    iou = _finite_unit(alignment.get("sourceViewSilhouetteIou"), "sourceViewSilhouetteIou")
    aspect = _finite_unit(
        alignment.get("projectedAspectRatioError"), "projectedAspectRatioError"
    )
    return ValidatedAlignment(
        payload=dict(alignment),
        transform=transform,
        maximum_scope=_maximum_scope(iou, aspect, semantic_status, str(chirality)),
    )
