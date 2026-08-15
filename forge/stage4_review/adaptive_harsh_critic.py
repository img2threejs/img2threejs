#!/usr/bin/env python3
"""Adaptive, adversarial multi-view review controller for a real Three.js scene.

The module is deliberately model-free.  It schedules browser camera captures,
packages immutable pixel evidence for a *separate* critic agent, validates the
critic response, and recursively subdivides only view cells where a defect was
observed.  It never calls an LLM and it never turns scores into visual truth.

The view sphere is represented by six cube-map faces.  A defective face cell is
split into four children, so angular resolution can keep increasing without a
fixed list of camera sides.  Runtime work is nevertheless bounded by repeated-
defect, plateau, max-round and max-view stop policies.

Security / evidence invariants:

* ``critic.id`` MUST differ from ``creator.id``;
* every reviewed view MUST bind to a browser-produced capture SHA-256 and exact
  unit view direction from the request;
* every finding repeats that binding (view ID + SHA-256 + direction), so prose
  detached from pixels is rejected;
* one critical finding blocks the run.  There is no averaging path.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import secrets
import sys
from pathlib import Path
from typing import Any

try:
    from .render_bridge import (  # type: ignore[import-not-found]
        canonical_sha256 as _canonical_sha256,
        decoded_pixel_sha256 as _decoded_pixel_sha256,
        validate_adaptive_capture_set,
    )
except ImportError:  # direct ``python forge/stage4_review/...py`` execution
    from render_bridge import (  # type: ignore[no-redef]
        canonical_sha256 as _canonical_sha256,
        decoded_pixel_sha256 as _decoded_pixel_sha256,
        validate_adaptive_capture_set,
    )


KIND_STATE = "img2threejs.adaptive-harsh-critic-state"
KIND_REQUEST = "img2threejs.adaptive-harsh-critic-request"
KIND_RESPONSE = "img2threejs.adaptive-harsh-critic-response"
SCHEMA_VERSION = 1

DEFAULT_MAX_ROUNDS = 6
DEFAULT_MAX_VIEWS = 96
DEFAULT_REPEATED_DEFECT_ROUNDS = 2
DEFAULT_PLATEAU_ROUNDS = 2
DEFAULT_MIN_DEFECT_REDUCTION = 1
DEFAULT_MINIMUM_UNIFORM_LEVEL = 1

SEVERITY_RANK = {"minor": 1, "major": 2, "critical": 3}
FACE_ORDER = ("front", "right", "rear", "left", "top", "bottom")
EQUATOR_FACES = ("front", "right", "rear", "left")
REQUIRED_ACKNOWLEDGEMENTS = (
    "inspectedPixels",
    "noScoreAveraging",
    "criticalDefectsAreBlocking",
)
FORBIDDEN_AGGREGATE_FIELDS = {"score", "globalScore", "averageScore", "fidelity"}
SESSION_ID_PREFIX_LENGTH = 24  # ``ahc-`` plus 20 random lowercase hex chars


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _canonical_request_digest(request: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in request.items()
        if key not in {"requestId", "requestDigest"}
    }
    return _canonical_sha256(payload)


def _request_id_from_digest(digest: str) -> str:
    return "ahcr-" + digest[:24]


def _reject_extra_fields(value: dict[str, Any], allowed: set[str], label: str) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        raise ValueError(f"{label} contains schema-forbidden fields: {extra}")


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _scope_view_to_session(view: dict[str, Any], session_id: str) -> dict[str, Any]:
    scoped = dict(view)
    scoped["id"] = f"{session_id}-{view['id']}"
    if view.get("parentId") is not None:
        scoped["parentId"] = f"{session_id}-{view['parentId']}"
    scoped["cell"] = dict(view["cell"])
    return scoped


def _finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _unit(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(value * value for value in vector))
    if length <= 0.0:
        raise ValueError("view direction cannot be the zero vector")
    return tuple(value / length for value in vector)


def _rounded_direction(vector: tuple[float, float, float]) -> list[float]:
    return [round(value, 12) for value in _unit(vector)]


def _cube_direction(face: str, u: float, v: float) -> list[float]:
    """Map one cube-map face coordinate to a Three.js orbit direction.

    Front is +Z and right is +X.  The exact face orientation is part of the
    serialized cell contract, so subdivision remains deterministic at seams.
    """
    vectors = {
        "front": (u, v, 1.0),
        "right": (1.0, v, -u),
        "rear": (-u, v, -1.0),
        "left": (-1.0, v, u),
        "top": (u, 1.0, -v),
        "bottom": (u, -1.0, v),
    }
    if face not in vectors:
        raise ValueError(f"unknown cube-map face: {face}")
    return _rounded_direction(vectors[face])


def _orbit_angles(direction: list[float]) -> tuple[float, float]:
    x, y, z = direction
    azimuth = math.degrees(math.atan2(x, z))
    if azimuth <= -180.0:
        azimuth = 180.0
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, y))))
    return round(azimuth, 9), round(elevation, 9)


def _angular_distance(a: list[float], b: list[float]) -> float:
    dot = max(-1.0, min(1.0, sum(left * right for left, right in zip(a, b))))
    return math.degrees(math.acos(dot))


def _make_view(
    face: str,
    bounds: tuple[float, float, float, float],
    *,
    path: str,
    parent_id: str | None,
    round_index: int,
    level: int,
) -> dict[str, Any]:
    u_min, u_max, v_min, v_max = bounds
    u_center = (u_min + u_max) / 2.0
    v_center = (v_min + v_max) / 2.0
    direction = _cube_direction(face, u_center, v_center)
    azimuth, elevation = _orbit_angles(direction)
    corners = [
        _cube_direction(face, u, v)
        for u, v in (
            (u_min, v_min),
            (u_min, v_max),
            (u_max, v_min),
            (u_max, v_max),
        )
    ]
    angular_radius = max(_angular_distance(direction, corner) for corner in corners)
    suffix = path if path else "root"
    view_id = f"harsh-{face}-{suffix}"
    return {
        "id": view_id,
        "round": round_index,
        "parentId": parent_id,
        "direction": direction,
        "azimuthDegrees": azimuth,
        "elevationDegrees": elevation,
        "angularRadiusDegrees": round(angular_radius, 9),
        "cell": {
            "projection": "cube-map",
            "face": face,
            "uMin": u_min,
            "uMax": u_max,
            "vMin": v_min,
            "vMax": v_max,
            "level": level,
            "path": path,
        },
    }


def base_views() -> list[dict[str, Any]]:
    """Return the six cube-map root cells covering the complete view sphere."""
    return [
        _make_view(
            face,
            (-1.0, 1.0, -1.0, 1.0),
            path="",
            parent_id=None,
            round_index=0,
            level=0,
        )
        for face in FACE_ORDER
    ]


def subdivide_view(view: dict[str, Any], round_index: int) -> list[dict[str, Any]]:
    """Split one cube-map cell into four deterministic child view cells."""
    cell = view.get("cell")
    if not isinstance(cell, dict) or cell.get("projection") != "cube-map":
        raise ValueError(f"view {view.get('id')!r} has no cube-map cell")
    face = str(cell.get("face", ""))
    u_min, u_max = float(cell["uMin"]), float(cell["uMax"])
    v_min, v_max = float(cell["vMin"]), float(cell["vMax"])
    u_mid, v_mid = (u_min + u_max) / 2.0, (v_min + v_max) / 2.0
    parent_path = str(cell.get("path", ""))
    level = int(cell.get("level", 0)) + 1
    # Stable Morton-like order: lower-left, lower-right, upper-left, upper-right.
    quadrants = (
        ("0", (u_min, u_mid, v_min, v_mid)),
        ("1", (u_mid, u_max, v_min, v_mid)),
        ("2", (u_min, u_mid, v_mid, v_max)),
        ("3", (u_mid, u_max, v_mid, v_max)),
    )
    children = [
        _make_view(
            face,
            bounds,
            path=parent_path + digit,
            parent_id=str(view["id"]),
            round_index=round_index,
            level=level,
        )
        for digit, bounds in quadrants
    ]
    parent_id = str(view["id"])
    marker = parent_id.find("harsh-")
    namespace = parent_id[:marker] if marker > 0 else ""
    if namespace:
        for child in children:
            child["id"] = namespace + str(child["id"])
    return children


def _validate_policy(policy: dict[str, Any]) -> None:
    required_fields = {
        "baseCoverage",
        "subdivision",
        "criticalRule",
        "maxRounds",
        "maxViews",
        "repeatedDefectRounds",
        "plateauRounds",
        "minDefectReduction",
        "minimumUniformLevel",
        "allowHoles",
    }
    _reject_extra_fields(
        policy,
        required_fields,
        "state.policy",
    )
    if set(policy) != required_fields:
        raise ValueError(
            f"state.policy is missing required fields: {sorted(required_fields - set(policy))}"
        )
    integer_fields = {
        "maxRounds": 1,
        "maxViews": len(FACE_ORDER),
        "repeatedDefectRounds": 2,
        "plateauRounds": 1,
        "minDefectReduction": 1,
        "minimumUniformLevel": 0,
    }
    for field, minimum in integer_fields.items():
        value = policy.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"policy.{field} must be an integer >= {minimum}")
    if policy.get("baseCoverage") != "cube-map-6":
        raise ValueError("policy.baseCoverage must be cube-map-6")
    if policy.get("subdivision") != "defect-cell-quadtree":
        raise ValueError("policy.subdivision must be defect-cell-quadtree")
    if policy.get("criticalRule") != "logical-and-no-averaging":
        raise ValueError("policy.criticalRule must be logical-and-no-averaging")
    if not isinstance(policy.get("allowHoles"), bool):
        raise ValueError("policy.allowHoles must be a boolean")


def _bounds_for_cell_path(path: str) -> tuple[float, float, float, float]:
    """Reconstruct the exact quadtree cell encoded by a Morton-like path."""
    u_min, u_max, v_min, v_max = -1.0, 1.0, -1.0, 1.0
    for digit in path:
        u_mid = (u_min + u_max) / 2.0
        v_mid = (v_min + v_max) / 2.0
        if digit == "0":
            u_max, v_max = u_mid, v_mid
        elif digit == "1":
            u_min, v_max = u_mid, v_mid
        elif digit == "2":
            u_max, v_min = u_mid, v_mid
        elif digit == "3":
            u_min, v_min = u_mid, v_mid
        else:  # guarded by the caller; keep this helper fail closed alone too
            raise ValueError(f"invalid cube-cell path digit: {digit}")
    return u_min, u_max, v_min, v_max


def _validate_view_plan(
    view: dict[str, Any], label: str, *, session_id: str | None = None
) -> None:
    required_view_fields = {
        "id",
        "round",
        "parentId",
        "direction",
        "azimuthDegrees",
        "elevationDegrees",
        "angularRadiusDegrees",
        "cell",
    }
    _reject_extra_fields(
        view,
        required_view_fields,
        label,
    )
    if set(view) != required_view_fields:
        raise ValueError(
            f"{label} is missing required fields: {sorted(required_view_fields - set(view))}"
        )
    view_id = view.get("id")
    if not isinstance(view_id, str) or not view_id:
        raise ValueError(f"{label}.id is required")
    if session_id is not None and not view_id.startswith(f"{session_id}-harsh-"):
        raise ValueError(f"{label}.id is not scoped to state.sessionId")
    parent_id = view.get("parentId")
    if parent_id is not None and (
        not isinstance(parent_id, str)
        or (session_id is not None and not parent_id.startswith(f"{session_id}-harsh-"))
    ):
        raise ValueError(f"{label}.parentId is invalid")
    round_index = view.get("round")
    if isinstance(round_index, bool) or not isinstance(round_index, int) or round_index < 0:
        raise ValueError(f"{label}.round must be a non-negative integer")
    direction = view.get("direction")
    if not isinstance(direction, list) or len(direction) != 3 or not all(
        _finite_number(value) for value in direction
    ):
        raise ValueError(f"{label}.direction must be a finite 3-vector")
    magnitude = math.sqrt(sum(float(value) ** 2 for value in direction))
    if abs(magnitude - 1.0) > 1e-8:
        raise ValueError(f"{label}.direction must be a unit vector")
    for field in ("azimuthDegrees", "elevationDegrees", "angularRadiusDegrees"):
        if not _finite_number(view.get(field)):
            raise ValueError(f"{label}.{field} must be finite")
    if not -180.0 <= float(view["azimuthDegrees"]) <= 180.0:
        raise ValueError(f"{label}.azimuthDegrees is outside [-180, 180]")
    if not -90.0 <= float(view["elevationDegrees"]) <= 90.0:
        raise ValueError(f"{label}.elevationDegrees is outside [-90, 90]")
    if not 0.0 < float(view["angularRadiusDegrees"]) <= 90.0:
        raise ValueError(f"{label}.angularRadiusDegrees must be in (0, 90]")
    cell = view.get("cell")
    if not isinstance(cell, dict):
        raise ValueError(f"{label}.cell is required")
    required_cell_fields = {
        "projection",
        "face",
        "uMin",
        "uMax",
        "vMin",
        "vMax",
        "level",
        "path",
    }
    _reject_extra_fields(
        cell,
        required_cell_fields,
        f"{label}.cell",
    )
    if set(cell) != required_cell_fields:
        raise ValueError(
            f"{label}.cell is missing required fields: "
            f"{sorted(required_cell_fields - set(cell))}"
        )
    if cell.get("projection") != "cube-map" or cell.get("face") not in FACE_ORDER:
        raise ValueError(f"{label}.cell projection/face is invalid")
    for field in ("uMin", "uMax", "vMin", "vMax"):
        if not _finite_number(cell.get(field)) or not -1.0 <= float(cell[field]) <= 1.0:
            raise ValueError(f"{label}.cell.{field} must be finite and inside [-1, 1]")
    if float(cell["uMin"]) >= float(cell["uMax"]):
        raise ValueError(f"{label}.cell requires uMin < uMax")
    if float(cell["vMin"]) >= float(cell["vMax"]):
        raise ValueError(f"{label}.cell requires vMin < vMax")
    level = cell.get("level")
    if isinstance(level, bool) or not isinstance(level, int) or level < 0:
        raise ValueError(f"{label}.cell.level must be a non-negative integer")
    path = cell.get("path")
    if not isinstance(path, str) or any(char not in "0123" for char in path):
        raise ValueError(f"{label}.cell.path is invalid")
    if len(path) != level:
        raise ValueError(f"{label}.cell.path length must equal cell.level")
    if round_index != level:
        raise ValueError(f"{label}.round must equal cell.level")
    face = str(cell["face"])
    suffix = path if path else "root"
    expected_id = f"harsh-{face}-{suffix}"
    if session_id is not None:
        expected_id = f"{session_id}-{expected_id}"
    if view_id != expected_id:
        raise ValueError(f"{label}.id does not encode its cube cell")
    expected_parent: str | None = None
    if level > 0:
        parent_suffix = path[:-1] if path[:-1] else "root"
        expected_parent = f"harsh-{face}-{parent_suffix}"
        if session_id is not None:
            expected_parent = f"{session_id}-{expected_parent}"
    if parent_id != expected_parent:
        raise ValueError(f"{label}.parentId does not encode the parent cube cell")
    expected_bounds = _bounds_for_cell_path(path)
    actual_bounds = tuple(float(cell[field]) for field in ("uMin", "uMax", "vMin", "vMax"))
    if any(abs(actual - expected) > 1e-12 for actual, expected in zip(actual_bounds, expected_bounds)):
        raise ValueError(f"{label}.cell bounds do not match its quadtree path")
    expected_view = _make_view(
        face,
        expected_bounds,
        path=path,
        parent_id=expected_parent,
        round_index=round_index,
        level=level,
    )
    if not _directions_equal(direction, expected_view["direction"]):
        raise ValueError(f"{label}.direction does not point at its cube-cell centre")
    for field in ("azimuthDegrees", "elevationDegrees", "angularRadiusDegrees"):
        if abs(float(view[field]) - float(expected_view[field])) > 1e-8:
            raise ValueError(f"{label}.{field} does not match its cube-cell geometry")


EVIDENCE_LEDGER_REQUIRED_FIELDS = {
    "viewId",
    "round",
    "capturePath",
    "captureSha256",
    "capturePixelSha256",
    "browserEvidenceSha256",
    "sceneBuildSha256",
    "direction",
}
EVIDENCE_LEDGER_OPTIONAL_FIELDS = {
    "referenceCapturePath",
    "referenceCaptureSha256",
}


def _validate_ledger_entry(entry: dict[str, Any], label: str) -> None:
    _reject_extra_fields(
        entry,
        EVIDENCE_LEDGER_REQUIRED_FIELDS | EVIDENCE_LEDGER_OPTIONAL_FIELDS,
        label,
    )
    if not EVIDENCE_LEDGER_REQUIRED_FIELDS <= set(entry):
        missing = sorted(EVIDENCE_LEDGER_REQUIRED_FIELDS - set(entry))
        raise ValueError(f"{label} is missing required fields: {missing}")
    if not isinstance(entry.get("viewId"), str) or not entry["viewId"]:
        raise ValueError(f"{label}.viewId is required")
    if (
        isinstance(entry.get("round"), bool)
        or not isinstance(entry.get("round"), int)
        or entry["round"] < 0
    ):
        raise ValueError(f"{label}.round must be a non-negative integer")
    if not isinstance(entry.get("capturePath"), str) or not entry["capturePath"]:
        raise ValueError(f"{label}.capturePath is required")
    for field in (
        "captureSha256",
        "capturePixelSha256",
        "browserEvidenceSha256",
        "sceneBuildSha256",
    ):
        if not _valid_sha256(entry.get(field)):
            raise ValueError(f"{label}.{field} is invalid")
    if not isinstance(entry.get("direction"), list) or not _directions_equal(
        entry["direction"], entry["direction"]
    ):
        raise ValueError(f"{label}.direction is invalid")
    has_reference_path = "referenceCapturePath" in entry
    has_reference_hash = "referenceCaptureSha256" in entry
    if has_reference_path != has_reference_hash:
        raise ValueError(f"{label} reference path/hash must appear together")
    if has_reference_path:
        if (
            not isinstance(entry["referenceCapturePath"], str)
            or not entry["referenceCapturePath"]
            or not _valid_sha256(entry["referenceCaptureSha256"])
        ):
            raise ValueError(f"{label} reference capture binding is invalid")


def _validate_state(state: dict[str, Any]) -> None:
    required_state_fields = {
        "kind",
        "schemaVersion",
        "sessionId",
        "creator",
        "scene",
        "policy",
        "currentRound",
        "rounds",
        "nextViews",
        "scheduledViewCount",
        "status",
        "action",
        "pendingRequest",
        "evidenceLedger",
    }
    allowed_state_fields = required_state_fields | {
        "stopReason",
        "blockingDefects",
        "plateauStreak",
        "note",
        "refinementMode",
        "repeatedDefects",
        "unscheduledNextViewCount",
    }
    _reject_extra_fields(
        state,
        allowed_state_fields,
        "state",
    )
    if not required_state_fields <= set(state):
        raise ValueError(
            "state is missing required fields: "
            f"{sorted(required_state_fields - set(state))}"
        )
    if state.get("kind") != KIND_STATE or state.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("not an adaptive harsh critic v1 state")
    session_id = state.get("sessionId")
    if (
        not isinstance(session_id, str)
        or len(session_id) != SESSION_ID_PREFIX_LENGTH
        or not session_id.startswith("ahc-")
        or any(char not in "0123456789abcdef" for char in session_id[4:])
    ):
        raise ValueError("state.sessionId must be ahc- plus 20 lowercase hex characters")
    creator = state.get("creator")
    if not isinstance(creator, dict) or not isinstance(creator.get("id"), str) or not creator["id"].strip():
        raise ValueError("state.creator.id is required")
    _reject_extra_fields(creator, {"id", "role"}, "state.creator")
    if creator.get("role") != "scene-creator":
        raise ValueError("state.creator.role must be scene-creator")
    scene = state.get("scene")
    if not isinstance(scene, dict):
        raise ValueError("state.scene must be an object")
    required_scene_fields = {
        "manifestPath",
        "manifestSha256AtInit",
        "runtimeUrl",
        "referenceKind",
        "referencePath",
        "referenceSha256",
        "sceneBuildSha256",
    }
    _reject_extra_fields(
        scene,
        required_scene_fields,
        "state.scene",
    )
    if set(scene) != required_scene_fields:
        raise ValueError(
            f"state.scene is missing required fields: {sorted(required_scene_fields - set(scene))}"
        )
    if not isinstance(scene.get("manifestPath"), str) or not scene["manifestPath"].strip():
        raise ValueError("state.scene.manifestPath is required")
    if not _valid_sha256(scene.get("manifestSha256AtInit")):
        raise ValueError("state.scene.manifestSha256AtInit is invalid")
    if not isinstance(scene.get("runtimeUrl"), str) or not scene["runtimeUrl"].strip():
        raise ValueError("state.scene.runtimeUrl is required")
    if not isinstance(scene.get("referencePath"), str) or not scene["referencePath"]:
        raise ValueError("state.scene.referencePath is required")
    if scene.get("referenceKind") not in {"image", "glb"}:
        raise ValueError("state.scene.referenceKind must be image or glb")
    if not _valid_sha256(scene.get("referenceSha256")):
        raise ValueError("state.scene.referenceSha256 is invalid")
    if scene.get("sceneBuildSha256") is not None and not _valid_sha256(
        scene.get("sceneBuildSha256")
    ):
        raise ValueError("state.scene.sceneBuildSha256 is invalid")
    policy = state.get("policy")
    if not isinstance(policy, dict):
        raise ValueError("state.policy must be an object")
    _validate_policy(policy)
    if not isinstance(state.get("rounds"), list) or not isinstance(state.get("nextViews"), list):
        raise ValueError("state rounds/nextViews must be lists")
    status = state.get("status")
    action = state.get("action")
    legal_actions = {
        "needs-render": {"capture-next-views"},
        "passed": {"continue"},
        "blocked": {"refine-code", "request-input"},
    }
    if status not in legal_actions:
        raise ValueError("state.status is invalid")
    if action not in legal_actions[status]:
        raise ValueError(f"state.status/action combination is invalid: {status}/{action}")
    stop_reason = state.get("stopReason")
    if stop_reason is not None and not isinstance(stop_reason, str):
        raise ValueError("state.stopReason must be a string or null")
    if status == "needs-render" and stop_reason is not None:
        raise ValueError("needs-render state.stopReason must be null")
    if status in {"passed", "blocked"} and (
        not isinstance(stop_reason, str) or not stop_reason.strip()
    ):
        raise ValueError(f"{status} state.stopReason must be a non-empty string")
    blocking_defects = state.get("blockingDefects")
    if blocking_defects is not None and not isinstance(blocking_defects, list):
        raise ValueError("state.blockingDefects must be a list")
    plateau_streak = state.get("plateauStreak")
    if plateau_streak is not None and (
        isinstance(plateau_streak, bool)
        or not isinstance(plateau_streak, int)
        or plateau_streak < 0
    ):
        raise ValueError("state.plateauStreak must be a non-negative integer")
    if "note" in state and not isinstance(state["note"], str):
        raise ValueError("state.note must be a string")
    if "refinementMode" in state and state["refinementMode"] not in {
        "minimum-uniform-coverage",
        "defect-directed",
    }:
        raise ValueError("state.refinementMode is invalid")
    if "repeatedDefects" in state and (
        not isinstance(state["repeatedDefects"], list)
        or not all(isinstance(item, str) for item in state["repeatedDefects"])
    ):
        raise ValueError("state.repeatedDefects must be a string list")
    unscheduled_count = state.get("unscheduledNextViewCount")
    if unscheduled_count is not None and (
        isinstance(unscheduled_count, bool)
        or not isinstance(unscheduled_count, int)
        or unscheduled_count < 0
    ):
        raise ValueError("state.unscheduledNextViewCount must be a non-negative integer")
    current_round = state.get("currentRound")
    if isinstance(current_round, bool) or not isinstance(current_round, int) or current_round < 0:
        raise ValueError("state.currentRound must be a non-negative integer")
    scheduled_views_by_id: dict[str, dict[str, Any]] = {}
    next_view_ids: list[str] = []
    for index, view in enumerate(state["nextViews"]):
        if not isinstance(view, dict):
            raise ValueError(f"state.nextViews[{index}] must be an object")
        _validate_view_plan(view, f"state.nextViews[{index}]", session_id=session_id)
        view_id = str(view["id"])
        if view_id in scheduled_views_by_id:
            raise ValueError(f"state.nextViews contains duplicate view id: {view_id}")
        if view["round"] != current_round:
            raise ValueError(f"state.nextViews view is bound to the wrong round: {view_id}")
        scheduled_views_by_id[view_id] = view
        next_view_ids.append(view_id)
    ledger = state.get("evidenceLedger")
    if not isinstance(ledger, list):
        raise ValueError("state.evidenceLedger must be a list")
    ledger_by_id: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(ledger):
        if not isinstance(entry, dict):
            raise ValueError(f"state.evidenceLedger[{index}] must be an object")
        _validate_ledger_entry(entry, f"state.evidenceLedger[{index}]")
        view_id = str(entry["viewId"])
        if view_id in ledger_by_id:
            raise ValueError(f"state.evidenceLedger has duplicate viewId: {view_id}")
        if not view_id.startswith(f"{session_id}-harsh-"):
            raise ValueError(f"state.evidenceLedger view is from another session: {view_id}")
        ledger_by_id[view_id] = entry
    completed_view_ids: set[str] = set()
    completed_rounds: set[int] = set()
    for round_index, record in enumerate(state["rounds"]):
        if not isinstance(record, dict):
            raise ValueError(f"state.rounds[{round_index}] must be an object")
        record_round = record.get("round")
        if (
            isinstance(record_round, bool)
            or not isinstance(record_round, int)
            or record_round < 0
        ):
            raise ValueError(f"state.rounds[{round_index}].round is invalid")
        if record_round in completed_rounds:
            raise ValueError(f"state.rounds has duplicate round: {record_round}")
        completed_rounds.add(record_round)
        evidence = record.get("evidence")
        if not isinstance(evidence, list):
            raise ValueError(f"state.rounds[{round_index}].evidence must be a list")
        views = record.get("views")
        if not isinstance(views, list):
            raise ValueError(f"state.rounds[{round_index}].views must be a list")
        for view_index, view in enumerate(views):
            if not isinstance(view, dict):
                raise ValueError(
                    f"state.rounds[{round_index}].views[{view_index}] must be an object"
                )
            _validate_view_plan(
                view,
                f"state.rounds[{round_index}].views[{view_index}]",
                session_id=session_id,
            )
            view_id = str(view["id"])
            if view["round"] != record_round:
                raise ValueError(
                    f"state.rounds[{round_index}] view is bound to the wrong round: {view_id}"
                )
            if view_id in scheduled_views_by_id:
                raise ValueError(f"state contains duplicate scheduled view id: {view_id}")
            scheduled_views_by_id[view_id] = view
            completed_view_ids.add(view_id)
        round_evidence: dict[str, dict[str, Any]] = {}
        for evidence_index, entry in enumerate(evidence):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"state.rounds[{round_index}].evidence[{evidence_index}] must be an object"
                )
            _validate_ledger_entry(
                entry, f"state.rounds[{round_index}].evidence[{evidence_index}]"
            )
            view_id = str(entry["viewId"])
            if view_id in round_evidence:
                raise ValueError(
                    f"state.rounds[{round_index}].evidence has duplicate viewId: {view_id}"
                )
            if entry["round"] != record_round:
                raise ValueError(
                    f"state.rounds[{round_index}] evidence is bound to the wrong round: {view_id}"
                )
            round_evidence[view_id] = entry
        expected_ids = {str(view["id"]) for view in views}
        if set(round_evidence) != expected_ids:
            raise ValueError(
                f"state.rounds[{round_index}] lost scheduled evidence ledger entries"
            )
        for view_id, entry in round_evidence.items():
            if ledger_by_id.get(view_id) != entry:
                raise ValueError(
                    f"state.rounds[{round_index}] evidence differs from state ledger: {view_id}"
                )
    scheduled_count = state.get("scheduledViewCount")
    if (
        isinstance(scheduled_count, bool)
        or not isinstance(scheduled_count, int)
        or scheduled_count < len(FACE_ORDER)
        or scheduled_count != len(scheduled_views_by_id)
    ):
        raise ValueError("state.scheduledViewCount differs from unique scheduled views")
    for view_id, entry in ledger_by_id.items():
        planned = scheduled_views_by_id.get(view_id)
        if planned is None:
            raise ValueError(f"state.evidenceLedger contains an unscheduled view: {view_id}")
        if entry["round"] != planned["round"] or not _directions_equal(
            entry["direction"], planned["direction"]
        ):
            raise ValueError(f"state.evidenceLedger binding differs from view plan: {view_id}")
    pending = state.get("pendingRequest")
    if status != "needs-render" and pending is not None:
        raise ValueError(f"{status} state.pendingRequest must be null")
    if status == "needs-render" and not state["nextViews"]:
        raise ValueError("needs-render state.nextViews must be non-empty")
    if status in {"passed", "blocked"} and state["nextViews"]:
        raise ValueError(f"{status} state.nextViews must be empty")
    if pending is not None:
        if not isinstance(pending, dict):
            raise ValueError("state.pendingRequest must be an object or null")
        _reject_extra_fields(
            pending,
            {"requestId", "canonicalSha256", "round", "manifestSha256", "viewIds"},
            "state.pendingRequest",
        )
        required_pending_fields = {
            "requestId",
            "canonicalSha256",
            "round",
            "manifestSha256",
            "viewIds",
        }
        if set(pending) != required_pending_fields:
            raise ValueError(
                "state.pendingRequest is missing required fields: "
                f"{sorted(required_pending_fields - set(pending))}"
            )
        digest = pending.get("canonicalSha256")
        request_id = pending.get("requestId")
        round_index = pending.get("round")
        view_ids = pending.get("viewIds")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError("state.pendingRequest.canonicalSha256 is invalid")
        if request_id != _request_id_from_digest(digest):
            raise ValueError("state.pendingRequest.requestId is not derived from canonicalSha256")
        if not _valid_sha256(pending.get("manifestSha256")):
            raise ValueError("state.pendingRequest.manifestSha256 is invalid")
        if round_index != state.get("currentRound"):
            raise ValueError("state.pendingRequest.round differs from currentRound")
        if not isinstance(view_ids, list) or not all(isinstance(item, str) for item in view_ids):
            raise ValueError("state.pendingRequest.viewIds must be a string list")
        if len(view_ids) != len(set(view_ids)):
            raise ValueError("state.pendingRequest.viewIds contains duplicates")
        if view_ids != next_view_ids:
            raise ValueError("state.pendingRequest.viewIds differs from ordered state.nextViews")
        if any(view_id not in ledger_by_id for view_id in view_ids):
            raise ValueError("state.pendingRequest refers to evidence absent from state.evidenceLedger")
    expected_ledger_ids = completed_view_ids | (
        set(next_view_ids) if pending is not None else set()
    )
    if set(ledger_by_id) != expected_ledger_ids:
        raise ValueError(
            "state.evidenceLedger must contain exactly completed-round evidence plus pending views"
        )


def init_state(
    manifest_path: Path,
    creator_id: str,
    *,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    max_views: int = DEFAULT_MAX_VIEWS,
    repeated_defect_rounds: int = DEFAULT_REPEATED_DEFECT_ROUNDS,
    plateau_rounds: int = DEFAULT_PLATEAU_ROUNDS,
    min_defect_reduction: int = DEFAULT_MIN_DEFECT_REDUCTION,
    minimum_uniform_level: int = DEFAULT_MINIMUM_UNIFORM_LEVEL,
    allow_holes: bool = False,
) -> dict[str, Any]:
    manifest_path = manifest_path.expanduser().resolve()
    manifest = _read_json(manifest_path)
    if not creator_id.strip():
        raise ValueError("creator_id must be non-empty")
    runtime = manifest.get("runtime")
    reference = manifest.get("reference")
    if not isinstance(runtime, dict) or not runtime.get("url"):
        raise ValueError("render manifest runtime.url is required")
    if not isinstance(reference, dict) or not reference.get("sha256"):
        raise ValueError("render manifest reference.sha256 is required")
    reference_source = _validate_reference_source(manifest_path, manifest)
    policy = {
        "baseCoverage": "cube-map-6",
        "subdivision": "defect-cell-quadtree",
        "criticalRule": "logical-and-no-averaging",
        "maxRounds": max_rounds,
        "maxViews": max_views,
        "repeatedDefectRounds": repeated_defect_rounds,
        "plateauRounds": plateau_rounds,
        "minDefectReduction": min_defect_reduction,
        "minimumUniformLevel": minimum_uniform_level,
        "allowHoles": bool(allow_holes),
    }
    _validate_policy(policy)
    # A new invocation is a new evidence session even if all inputs are the
    # same.  Deterministic IDs allowed stale captures from an earlier run to be
    # mistaken for the newly scheduled evidence.
    session_id = "ahc-" + secrets.token_hex(10)
    views = [_scope_view_to_session(view, session_id) for view in base_views()]
    state = {
        "kind": KIND_STATE,
        "schemaVersion": SCHEMA_VERSION,
        "sessionId": session_id,
        "creator": {"id": creator_id.strip(), "role": "scene-creator"},
        "scene": {
            "manifestPath": str(manifest_path),
            "manifestSha256AtInit": _sha256(manifest_path),
            "runtimeUrl": str(runtime["url"]),
            "referenceKind": reference.get("kind"),
            "referencePath": str(reference_source),
            "referenceSha256": str(reference["sha256"]),
            "sceneBuildSha256": None,
        },
        "policy": policy,
        "currentRound": 0,
        "rounds": [],
        "nextViews": views,
        "scheduledViewCount": len(views),
        "status": "needs-render",
        "action": "capture-next-views",
        "stopReason": None,
        "blockingDefects": [],
        "pendingRequest": None,
        "evidenceLedger": [],
        "plateauStreak": 0,
        "note": (
            "all six root faces are uniformly subdivided through minimumUniformLevel before "
            "a clean pass is possible; defect cells can then refine without a fixed angular-"
            "resolution ceiling, while policy caps still guarantee termination"
        ),
    }
    _validate_state(state)
    return state


def _all_views(state: dict[str, Any]) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    for round_record in state.get("rounds", []):
        if isinstance(round_record, dict) and isinstance(round_record.get("views"), list):
            views.extend(item for item in round_record["views"] if isinstance(item, dict))
    views.extend(item for item in state.get("nextViews", []) if isinstance(item, dict))
    return views


def _find_capture(manifest: dict[str, Any], view_id: str) -> dict[str, Any]:
    for capture in manifest.get("captures", []):
        if isinstance(capture, dict) and capture.get("id") == view_id:
            return capture
    raise ValueError(f"render manifest has no scheduled capture for view {view_id}")


def _manifest_path(manifest_path: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else (manifest_path.parent / candidate).resolve()


def _validate_reference_source(
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    expected_sha256: str | None = None,
    expected_path: str | None = None,
) -> Path:
    """Re-open the actual source image/GLB rather than trusting manifest prose."""
    reference = manifest.get("reference")
    if not isinstance(reference, dict):
        raise ValueError("render manifest reference must be an object")
    path_value = reference.get("path")
    declared_hash = reference.get("sha256")
    if not isinstance(path_value, str) or not path_value:
        raise ValueError("render manifest reference.path is required")
    if not _valid_sha256(declared_hash):
        raise ValueError("render manifest reference.sha256 is invalid")
    source = _manifest_path(manifest_path, path_value)
    if not source.is_file():
        raise ValueError(f"reference source file is missing: {source}")
    actual_hash = _sha256(source)
    if actual_hash != declared_hash:
        raise ValueError("reference source file hash differs from render manifest")
    if expected_sha256 is not None and actual_hash != expected_sha256:
        raise ValueError("reference source file hash differs from adaptive state lock")
    if expected_path is not None and source != Path(expected_path).expanduser().resolve():
        raise ValueError("reference source path differs from adaptive state lock")
    return source


def _directions_equal(left: Any, right: Any, tolerance: float = 1e-9) -> bool:
    if not isinstance(left, list) or not isinstance(right, list) or len(left) != 3 or len(right) != 3:
        return False
    if not all(_finite_number(value) for value in left + right):
        return False
    return all(abs(float(a) - float(b)) <= tolerance for a, b in zip(left, right))


def _capture_evidence(
    manifest_path: Path,
    manifest: dict[str, Any],
    view: dict[str, Any],
    *,
    expected_session_id: str,
    require_reference: bool = False,
) -> dict[str, Any]:
    view_id = str(view["id"])
    capture = _find_capture(manifest, view_id)
    if capture.get("status") != "recorded":
        raise ValueError(f"adaptive view has not been browser-captured: {view_id}")
    binding = capture.get("adaptiveCritic")
    if not isinstance(binding, dict):
        raise ValueError(f"capture {view_id} lacks adaptiveCritic scene binding")
    if not _directions_equal(binding.get("direction"), view.get("direction")):
        raise ValueError(f"capture {view_id} direction differs from adaptive view plan")
    if binding.get("sessionId") != expected_session_id:
        raise ValueError(f"capture {view_id} belongs to a different adaptive critic session")
    if capture.get("azimuthDegrees") != view.get("azimuthDegrees"):
        raise ValueError(f"capture {view_id} azimuth differs from adaptive view plan")
    if capture.get("elevationDegrees") != view.get("elevationDegrees"):
        raise ValueError(f"capture {view_id} elevation differs from adaptive view plan")
    screenshot_value = capture.get("path")
    if not isinstance(screenshot_value, str) or not screenshot_value:
        raise ValueError(f"capture {view_id} has no screenshot path")
    screenshot = _manifest_path(manifest_path, screenshot_value)
    if not screenshot.is_file():
        raise ValueError(f"capture file is missing: {screenshot}")
    current_hash = _sha256(screenshot)
    if capture.get("screenshotSha256") != current_hash:
        raise ValueError(f"capture hash changed or was never recorded: {view_id}")
    pixel_hash = _decoded_pixel_sha256(screenshot)
    if capture.get("pixelSha256") != pixel_hash:
        raise ValueError(f"capture decoded-pixel hash changed or was never recorded: {view_id}")
    browser_evidence = capture.get("browserEvidence")
    browser_evidence_hash = capture.get("browserEvidenceSha256")
    if not isinstance(browser_evidence, dict) or not isinstance(browser_evidence_hash, str):
        raise ValueError(f"capture lacks Playwright browser provenance: {view_id}")
    if _canonical_sha256(browser_evidence) != browser_evidence_hash:
        raise ValueError(f"capture browser provenance hash changed: {view_id}")
    evidence: dict[str, Any] = {
        "viewId": view_id,
        "capturePath": str(screenshot),
        "captureSha256": current_hash,
        "capturePixelSha256": pixel_hash,
        "browserEvidenceSha256": browser_evidence_hash,
        "browserEvidence": browser_evidence,
        "direction": view["direction"],
        "azimuthDegrees": view["azimuthDegrees"],
        "elevationDegrees": view["elevationDegrees"],
        "angularRadiusDegrees": view["angularRadiusDegrees"],
        "cell": view["cell"],
    }
    reference = capture.get("reference")
    if isinstance(reference, dict) and reference.get("status") == "recorded" and reference.get("path"):
        if require_reference and (
            reference.get("readySignal") is not True or reference.get("consoleErrors")
        ):
            raise ValueError(
                f"GLB reference capture lacks clean strict-ready browser evidence: {view_id}"
            )
        reference_path = _manifest_path(manifest_path, str(reference["path"]))
        if not reference_path.is_file():
            raise ValueError(f"reference capture file is missing: {reference_path}")
        reference_hash = _sha256(reference_path)
        if reference.get("screenshotSha256") != reference_hash:
            raise ValueError(f"reference capture hash changed: {view_id}")
        evidence["referenceCapturePath"] = str(reference_path)
        evidence["referenceCaptureSha256"] = reference_hash
    elif require_reference:
        raise ValueError(
            f"GLB adaptive view has no recorded browser reference capture: {view_id}"
        )
    return evidence


def _ledger_entry_from_evidence(
    evidence: dict[str, Any], round_index: int
) -> dict[str, Any]:
    browser_evidence = evidence.get("browserEvidence")
    scene_hash = (
        browser_evidence.get("runtime", {}).get("sceneBuildSha256")
        if isinstance(browser_evidence, dict)
        else None
    )
    entry = {
        "viewId": str(evidence["viewId"]),
        "round": round_index,
        "capturePath": str(evidence["capturePath"]),
        "captureSha256": str(evidence["captureSha256"]),
        "capturePixelSha256": str(evidence["capturePixelSha256"]),
        "browserEvidenceSha256": str(evidence["browserEvidenceSha256"]),
        "sceneBuildSha256": scene_hash,
        "direction": list(evidence["direction"]),
    }
    if "referenceCapturePath" in evidence or "referenceCaptureSha256" in evidence:
        entry["referenceCapturePath"] = evidence.get("referenceCapturePath")
        entry["referenceCaptureSha256"] = evidence.get("referenceCaptureSha256")
    _validate_ledger_entry(entry, f"evidence ledger entry {entry['viewId']}")
    return entry


def _validate_historical_evidence(
    state: dict[str, Any], manifest_path: Path, manifest: dict[str, Any]
) -> None:
    """Re-open every ledger item and reject disappearance or mutation.

    The manifest is expected to grow as new rounds are scheduled, so pinning one
    manifest-file hash for the entire run would be incorrect.  Instead this
    ledger pins every evidence-critical capture field and revalidates the live
    file and receipt on each request.
    """
    views_by_id = {str(view["id"]): view for view in _all_views(state)}
    for index, saved in enumerate(state.get("evidenceLedger", [])):
        if not isinstance(saved, dict):
            raise ValueError(f"state.evidenceLedger[{index}] must be an object")
        view_id = str(saved.get("viewId", ""))
        planned = views_by_id.get(view_id)
        if planned is None:
            raise ValueError(f"historical scheduled evidence view disappeared: {view_id}")
        current = _ledger_entry_from_evidence(
            _capture_evidence(
                manifest_path,
                manifest,
                planned,
                expected_session_id=state["sessionId"],
                require_reference=state["scene"].get("referenceKind") == "glb",
            ),
            int(planned["round"]),
        )
        if current != saved:
            raise ValueError(f"historical adaptive evidence changed: {view_id}")


def _base_turntable_result(
    state: dict[str, Any], manifest_path: Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    views_by_face = {
        str(view.get("cell", {}).get("face")): view
        for view in _all_views(state)
        if isinstance(view.get("cell"), dict)
        and int(view["cell"].get("level", -1)) == 0
    }
    missing = [face for face in EQUATOR_FACES if face not in views_by_face]
    if missing:
        raise ValueError(f"adaptive state is missing fixed turntable base faces: {missing}")
    captures: list[tuple[float, Path]] = []
    for face in EQUATOR_FACES:
        evidence = _capture_evidence(
            manifest_path,
            manifest,
            views_by_face[face],
            expected_session_id=state["sessionId"],
            require_reference=state["scene"].get("referenceKind") == "glb",
        )
        captures.append((float(evidence["azimuthDegrees"]), Path(evidence["capturePath"])))
    try:
        from .turntable_gate import analyze_turntable  # type: ignore[import-not-found]
    except ImportError:  # direct ``python forge/stage4_review/...py`` execution
        from turntable_gate import analyze_turntable

    result = analyze_turntable(
        captures,
        allow_holes=bool(state["policy"].get("allowHoles", False)),
    )
    if not result.get("passed"):
        raise ValueError(
            "fixed turntable baseline failed before independent critique: "
            f"missing={result.get('missingAzimuths')} "
            f"unsegmented={result.get('unsegmentedAzimuths')} "
            f"degenerate={result.get('degenerate')} holed={result.get('holed')}"
        )
    return result


def build_critic_request(
    state: dict[str, Any], manifest_path: Path
) -> dict[str, Any]:
    # Treat direct API calls transactionally just like the CLI's atomic state
    # file write. All locks/ledger/pending mutations happen on a deep copy and
    # are committed to the caller only after every baseline/history gate and
    # final state validation succeeds.
    caller_state = state
    state = copy.deepcopy(state)
    _validate_state(state)
    if state.get("status") != "needs-render" or not state.get("nextViews"):
        raise ValueError("state has no nextViews awaiting real scene captures")
    if state.get("pendingRequest") is not None:
        raise ValueError(
            "state already has an unconsumed pending critic request; advance it before issuing another"
        )
    manifest_path = manifest_path.expanduser().resolve()
    expected_manifest = Path(str(state["scene"]["manifestPath"])).expanduser().resolve()
    if manifest_path != expected_manifest:
        raise ValueError("manifest path differs from the scene pinned in state")
    manifest = _read_json(manifest_path)
    if str(manifest.get("runtime", {}).get("url")) != state["scene"]["runtimeUrl"]:
        raise ValueError("runtime URL changed from the scene pinned at critic init")
    reference = manifest.get("reference", {})
    if reference.get("kind") != state["scene"]["referenceKind"]:
        raise ValueError("reference kind changed from the scene pinned at critic init")
    if reference.get("sha256") != state["scene"]["referenceSha256"]:
        raise ValueError("reference hash changed from the scene pinned at critic init")
    reference_source = _validate_reference_source(
        manifest_path,
        manifest,
        expected_sha256=state["scene"]["referenceSha256"],
        expected_path=state["scene"]["referencePath"],
    )

    capture_set_integrity = validate_adaptive_capture_set(
        manifest_path,
        manifest,
        session_id=state["sessionId"],
    )
    scene_build_hash = capture_set_integrity.get("sceneBuildSha256")
    if not _valid_sha256(scene_build_hash):
        raise ValueError("adaptive capture set has no single valid sceneBuildSha256")
    locked_scene_hash = state["scene"].get("sceneBuildSha256")
    if locked_scene_hash is None:
        state["scene"]["sceneBuildSha256"] = scene_build_hash
    elif locked_scene_hash != scene_build_hash:
        raise ValueError(
            "adaptive capture sceneBuildSha256 differs from the first-round state lock"
        )

    _validate_historical_evidence(state, manifest_path, manifest)
    expected_view_ids = {str(view["id"]) for view in _all_views(state)}
    recorded_view_ids = {
        str(capture.get("id"))
        for capture in manifest.get("captures", [])
        if isinstance(capture, dict)
        and capture.get("role") == "adaptive-critic"
        and capture.get("status") == "recorded"
        and isinstance(capture.get("adaptiveCritic"), dict)
        and capture["adaptiveCritic"].get("sessionId") == state["sessionId"]
    }
    if recorded_view_ids != expected_view_ids:
        raise ValueError(
            "adaptive session evidence does not exactly match all scheduled views; "
            f"missing={sorted(expected_view_ids - recorded_view_ids)} "
            f"extra={sorted(recorded_view_ids - expected_view_ids)}"
        )

    evidence = [
        _capture_evidence(
            manifest_path,
            manifest,
            view,
            expected_session_id=state["sessionId"],
            require_reference=state["scene"].get("referenceKind") == "glb",
        )
        for view in state["nextViews"]
    ]
    current_ledger = [
        _ledger_entry_from_evidence(item, int(state["currentRound"]))
        for item in evidence
    ]
    existing_ledger_ids = {
        str(item["viewId"])
        for item in state["evidenceLedger"]
        if isinstance(item, dict) and item.get("viewId")
    }
    repeated_ids = sorted(
        str(item["viewId"])
        for item in current_ledger
        if str(item["viewId"]) in existing_ledger_ids
    )
    if repeated_ids:
        raise ValueError(f"current evidence was already consumed into the ledger: {repeated_ids}")
    state["evidenceLedger"].extend(current_ledger)
    baseline = _base_turntable_result(state, manifest_path, manifest)
    request: dict[str, Any] = {
        "kind": KIND_REQUEST,
        "schemaVersion": SCHEMA_VERSION,
        "sessionId": state["sessionId"],
        "round": state["currentRound"],
        "creator": state["creator"],
        "scene": {
            "manifestPath": str(manifest_path),
            "manifestSha256": _sha256(manifest_path),
            "runtimeUrl": state["scene"]["runtimeUrl"],
            "referenceKind": state["scene"]["referenceKind"],
            "referencePath": str(reference_source),
            "referenceSha256": state["scene"]["referenceSha256"],
            "sceneBuildSha256": state["scene"]["sceneBuildSha256"],
        },
        "fixedTurntableBaseline": baseline,
        "captureSetIntegrity": capture_set_integrity,
        "views": evidence,
        "criticContract": {
            "requiredRole": "independent-harsh-critic",
            "identityRule": "critic.id MUST differ from creator.id",
            "pixelRule": (
                "open every capture at native resolution; bind every finding to its "
                "viewId, captureSha256, and exact direction"
            ),
            "stance": (
                "assume the scene is wrong until observable geometry, attachment, "
                "material response, camera readability, and off-axis form survive inspection"
            ),
            "criticalRule": (
                "one critical finding blocks the entire run; never average defects "
                "across views or features"
            ),
            "requiredAcknowledgements": list(REQUIRED_ACKNOWLEDGEMENTS),
            "minimumUniformLevel": state["policy"]["minimumUniformLevel"],
            "responseKind": KIND_RESPONSE,
            "schema": "docs/specs/adaptive-harsh-critic.v1.schema.json",
        },
    }
    digest = _canonical_request_digest(request)
    request["requestDigest"] = digest
    request["requestId"] = _request_id_from_digest(digest)
    state["pendingRequest"] = {
        "requestId": request["requestId"],
        "canonicalSha256": digest,
        "round": state["currentRound"],
        "manifestSha256": request["scene"]["manifestSha256"],
        "viewIds": [str(item["viewId"]) for item in evidence],
    }
    _validate_state(state)
    caller_state.clear()
    caller_state.update(copy.deepcopy(state))
    return request


def _validate_response_identity(
    state: dict[str, Any], request: dict[str, Any], response: dict[str, Any]
) -> dict[str, Any]:
    _reject_extra_fields(
        response,
        {"kind", "schemaVersion", "requestId", "critic", "views"},
        "critic response",
    )
    if response.get("kind") != KIND_RESPONSE or response.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("not an adaptive harsh critic v1 response")
    if response.get("requestId") != request.get("requestId"):
        raise ValueError("critic response requestId does not match request")
    critic = response.get("critic")
    if not isinstance(critic, dict):
        raise ValueError("response.critic must be an object")
    _reject_extra_fields(
        critic, {"id", "role", "acknowledgements"}, "response.critic"
    )
    _forbid_aggregate_fields(critic, "response.critic")
    critic_id = critic.get("id")
    if not isinstance(critic_id, str) or not critic_id.strip():
        raise ValueError("response.critic.id is required")
    if critic_id.strip() == state["creator"]["id"]:
        raise ValueError("critic.id MUST differ from creator.id")
    if critic.get("role") != "independent-harsh-critic":
        raise ValueError("response.critic.role must be independent-harsh-critic")
    acknowledgements = critic.get("acknowledgements")
    if not isinstance(acknowledgements, dict):
        raise ValueError("response.critic.acknowledgements must be an object")
    _reject_extra_fields(
        acknowledgements,
        set(REQUIRED_ACKNOWLEDGEMENTS),
        "response.critic.acknowledgements",
    )
    for field in REQUIRED_ACKNOWLEDGEMENTS:
        if acknowledgements.get(field) is not True:
            raise ValueError(f"critic acknowledgement must be true: {field}")
    return critic


def _forbid_aggregate_fields(value: dict[str, Any], label: str) -> None:
    forbidden = sorted(FORBIDDEN_AGGREGATE_FIELDS & set(value))
    if forbidden:
        raise ValueError(
            f"{label} contains forbidden aggregate score fields {forbidden}; "
            "adaptive critical defects use logical AND, never averaging"
        )


def _validate_request_integrity(state: dict[str, Any], request: dict[str, Any]) -> None:
    _reject_extra_fields(
        request,
        {
            "kind",
            "schemaVersion",
            "requestId",
            "requestDigest",
            "sessionId",
            "round",
            "creator",
            "scene",
            "fixedTurntableBaseline",
            "captureSetIntegrity",
            "views",
            "criticContract",
        },
        "critic request",
    )
    pending = state.get("pendingRequest")
    if not isinstance(pending, dict):
        raise ValueError("state has no pending critic request to consume")
    actual_digest = _canonical_request_digest(request)
    declared_digest = request.get("requestDigest")
    derived_id = _request_id_from_digest(actual_digest)
    if declared_digest != actual_digest:
        raise ValueError("critic request canonical digest does not match its complete content")
    if request.get("requestId") != derived_id:
        raise ValueError("critic requestId is not derived from the complete canonical request")
    if pending.get("canonicalSha256") != actual_digest:
        raise ValueError("critic request differs from state.pendingRequest canonical digest")
    if pending.get("requestId") != request.get("requestId"):
        raise ValueError("critic requestId differs from state.pendingRequest")
    if pending.get("round") != request.get("round"):
        raise ValueError("critic request round differs from state.pendingRequest")
    scene = request.get("scene")
    if not isinstance(scene, dict) or pending.get("manifestSha256") != scene.get("manifestSha256"):
        raise ValueError("critic request manifest differs from state.pendingRequest")
    request_views = request.get("views")
    if not isinstance(request_views, list):
        raise ValueError("critic request views must be a list")
    if pending.get("viewIds") != [str(item.get("viewId")) for item in request_views if isinstance(item, dict)]:
        raise ValueError("critic request view order differs from state.pendingRequest")


def _validate_request_bindings(state: dict[str, Any], request: dict[str, Any]) -> None:
    if request.get("creator") != state.get("creator"):
        raise ValueError("critic request creator differs from state")
    contract = request.get("criticContract")
    if not isinstance(contract, dict):
        raise ValueError("critic request criticContract must be an object")
    _reject_extra_fields(
        contract,
        {
            "requiredRole",
            "identityRule",
            "pixelRule",
            "stance",
            "criticalRule",
            "requiredAcknowledgements",
            "minimumUniformLevel",
            "responseKind",
            "schema",
        },
        "critic request.criticContract",
    )
    if contract.get("requiredRole") != "independent-harsh-critic":
        raise ValueError("critic request requiredRole was changed")
    if contract.get("criticalRule") != (
        "one critical finding blocks the entire run; never average defects "
        "across views or features"
    ):
        raise ValueError("critic request criticalRule was changed")
    if contract.get("requiredAcknowledgements") != list(REQUIRED_ACKNOWLEDGEMENTS):
        raise ValueError("critic request requiredAcknowledgements were changed")
    if contract.get("minimumUniformLevel") != state["policy"]["minimumUniformLevel"]:
        raise ValueError("critic request minimumUniformLevel was changed")
    state_views = {str(item["id"]): item for item in state.get("nextViews", [])}
    ledger_by_id = {
        str(item["viewId"]): item
        for item in state.get("evidenceLedger", [])
        if isinstance(item, dict) and item.get("viewId")
    }
    request_views = request.get("views")
    if not isinstance(request_views, list):
        raise ValueError("critic request views must be a list")
    for item in request_views:
        if not isinstance(item, dict) or str(item.get("viewId")) not in state_views:
            raise ValueError("critic request contains a view not present in state.nextViews")
        _reject_extra_fields(
            item,
            {
                "viewId",
                "capturePath",
                "captureSha256",
                "capturePixelSha256",
                "browserEvidenceSha256",
                "browserEvidence",
                "direction",
                "azimuthDegrees",
                "elevationDegrees",
                "angularRadiusDegrees",
                "cell",
                "referenceCapturePath",
                "referenceCaptureSha256",
            },
            f"critic request view {item.get('viewId')}",
        )
        planned = state_views[str(item["viewId"])]
        if not _directions_equal(item.get("direction"), planned.get("direction")):
            raise ValueError(f"critic request direction differs from state: {item.get('viewId')}")
        if item.get("cell") != planned.get("cell"):
            raise ValueError(f"critic request cell differs from state: {item.get('viewId')}")
        ledger_entry = _ledger_entry_from_evidence(item, int(request["round"]))
        if ledger_by_id.get(str(item["viewId"])) != ledger_entry:
            raise ValueError(
                f"critic request evidence differs from state evidence ledger: {item.get('viewId')}"
            )
        _validate_capture_still_matches(item)
    scene = request.get("scene")
    if not isinstance(scene, dict):
        raise ValueError("critic request scene must be an object")
    _reject_extra_fields(
        scene,
        {
            "manifestPath",
            "manifestSha256",
            "runtimeUrl",
            "referenceKind",
            "referencePath",
            "referenceSha256",
            "sceneBuildSha256",
        },
        "critic request.scene",
    )
    if scene.get("sceneBuildSha256") != state["scene"].get("sceneBuildSha256"):
        raise ValueError("critic request scene build differs from state lock")
    if (
        scene.get("referenceKind") != state["scene"].get("referenceKind")
        or scene.get("referencePath") != state["scene"].get("referencePath")
        or scene.get("referenceSha256") != state["scene"].get("referenceSha256")
    ):
        raise ValueError("critic request reference source differs from state lock")
    capture_integrity = request.get("captureSetIntegrity")
    if not isinstance(capture_integrity, dict):
        raise ValueError("critic request captureSetIntegrity must be an object")
    _reject_extra_fields(
        capture_integrity,
        {
            "recordedCaptureCount",
            "uniquePixelCount",
            "uniqueCameraMatrixCount",
            "sceneBuildSha256",
        },
        "critic request.captureSetIntegrity",
    )
    if capture_integrity.get("sceneBuildSha256") != state["scene"].get(
        "sceneBuildSha256"
    ):
        raise ValueError("critic request capture set differs from state scene build lock")
    manifest_value = scene.get("manifestPath")
    manifest_hash = scene.get("manifestSha256")
    if not isinstance(manifest_value, str) or not isinstance(manifest_hash, str):
        raise ValueError("critic request scene manifest binding is missing")
    manifest_path = Path(manifest_value).expanduser().resolve()
    if manifest_path != Path(str(state["scene"]["manifestPath"])).expanduser().resolve():
        raise ValueError("critic request scene manifest path differs from state")
    if not manifest_path.is_file() or _sha256(manifest_path) != manifest_hash:
        raise ValueError("critic request scene manifest hash changed")
    manifest = _read_json(manifest_path)
    if manifest.get("reference", {}).get("kind") != state["scene"]["referenceKind"]:
        raise ValueError("render manifest reference kind differs from adaptive state lock")
    _validate_reference_source(
        manifest_path,
        manifest,
        expected_sha256=state["scene"]["referenceSha256"],
        expected_path=state["scene"]["referencePath"],
    )
    actual_integrity = validate_adaptive_capture_set(
        manifest_path, manifest, session_id=state["sessionId"]
    )
    if actual_integrity != capture_integrity:
        raise ValueError("critic request captureSetIntegrity differs from live scene evidence")
    _validate_historical_evidence(state, manifest_path, manifest)


def _validate_capture_still_matches(view: dict[str, Any]) -> None:
    capture_path = Path(str(view["capturePath"])).expanduser().resolve()
    if not capture_path.is_file():
        raise ValueError(f"critic evidence capture is missing: {capture_path}")
    if _sha256(capture_path) != view["captureSha256"]:
        raise ValueError(f"critic evidence capture hash changed: {view['viewId']}")
    if _decoded_pixel_sha256(capture_path) != view.get("capturePixelSha256"):
        raise ValueError(f"critic evidence decoded-pixel hash changed: {view['viewId']}")
    browser_evidence = view.get("browserEvidence")
    if not isinstance(browser_evidence, dict):
        raise ValueError(f"critic evidence browser provenance is missing: {view['viewId']}")
    if _canonical_sha256(browser_evidence) != view.get("browserEvidenceSha256"):
        raise ValueError(f"critic evidence browser provenance hash changed: {view['viewId']}")
    has_reference_path = "referenceCapturePath" in view
    has_reference_hash = "referenceCaptureSha256" in view
    if has_reference_path != has_reference_hash:
        raise ValueError(
            f"critic evidence reference path/hash binding is incomplete: {view['viewId']}"
        )
    if has_reference_path:
        reference_path = Path(str(view["referenceCapturePath"])).expanduser().resolve()
        if not reference_path.is_file():
            raise ValueError(
                f"critic evidence reference capture is missing: {reference_path}"
            )
        if _sha256(reference_path) != view["referenceCaptureSha256"]:
            raise ValueError(
                f"critic evidence reference capture hash changed: {view['viewId']}"
            )


def _normalize_reviews(
    request: dict[str, Any], response: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    request_views = {str(item["viewId"]): item for item in request.get("views", [])}
    raw_reviews = response.get("views")
    if not isinstance(raw_reviews, list):
        raise ValueError("response.views must be a list")
    reviews_by_id: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []
    unreviewable = False
    for index, review in enumerate(raw_reviews):
        if not isinstance(review, dict):
            raise ValueError(f"response.views[{index}] must be an object")
        _reject_extra_fields(
            review,
            {"viewId", "captureSha256", "direction", "verdict", "findings"},
            f"response.views[{index}]",
        )
        _forbid_aggregate_fields(review, f"response.views[{index}]")
        view_id = review.get("viewId")
        if not isinstance(view_id, str) or view_id not in request_views:
            raise ValueError(f"response.views[{index}].viewId is not requested")
        if view_id in reviews_by_id:
            raise ValueError(f"duplicate critic review for view: {view_id}")
        requested = request_views[view_id]
        _validate_capture_still_matches(requested)
        if review.get("captureSha256") != requested["captureSha256"]:
            raise ValueError(f"review captureSha256 does not bind requested pixels: {view_id}")
        if not _directions_equal(review.get("direction"), requested["direction"]):
            raise ValueError(f"review direction does not bind requested view: {view_id}")
        verdict = review.get("verdict")
        if verdict not in {"pass", "defect", "unreviewable"}:
            raise ValueError(f"invalid review verdict for {view_id}")
        raw_findings = review.get("findings")
        if not isinstance(raw_findings, list):
            raise ValueError(f"review findings must be a list: {view_id}")
        if verdict == "pass" and raw_findings:
            raise ValueError(f"pass review cannot contain findings: {view_id}")
        if verdict == "defect" and not raw_findings:
            raise ValueError(f"defect review must contain findings: {view_id}")
        if verdict == "unreviewable":
            unreviewable = True
        normalized_findings: list[dict[str, Any]] = []
        for finding_index, finding in enumerate(raw_findings):
            if not isinstance(finding, dict):
                raise ValueError(f"finding {view_id}[{finding_index}] must be an object")
            prefix = f"finding {view_id}[{finding_index}]"
            _reject_extra_fields(
                finding,
                {
                    "defectKey",
                    "severity",
                    "category",
                    "description",
                    "viewId",
                    "captureSha256",
                    "direction",
                },
                prefix,
            )
            _forbid_aggregate_fields(finding, prefix)
            for field in ("defectKey", "category", "description"):
                if not isinstance(finding.get(field), str) or not finding[field].strip():
                    raise ValueError(f"{prefix}.{field} must be a non-empty string")
            severity = finding.get("severity")
            if severity not in SEVERITY_RANK:
                raise ValueError(f"{prefix}.severity must be minor, major, or critical")
            if finding.get("viewId") != view_id:
                raise ValueError(f"{prefix}.viewId does not bind the reviewed view")
            if finding.get("captureSha256") != requested["captureSha256"]:
                raise ValueError(f"{prefix}.captureSha256 does not bind actual pixels")
            if not _directions_equal(finding.get("direction"), requested["direction"]):
                raise ValueError(f"{prefix}.direction does not bind the reviewed camera")
            normalized = {
                "defectKey": finding["defectKey"].strip(),
                "severity": severity,
                "category": finding["category"].strip(),
                "description": finding["description"].strip(),
                "viewId": view_id,
                "captureSha256": requested["captureSha256"],
                "direction": requested["direction"],
            }
            normalized_findings.append(normalized)
            findings.append(normalized)
        reviews_by_id[view_id] = {
            "viewId": view_id,
            "captureSha256": requested["captureSha256"],
            "direction": requested["direction"],
            "verdict": verdict,
            "findings": normalized_findings,
        }
    missing = sorted(set(request_views) - set(reviews_by_id))
    extra = sorted(set(reviews_by_id) - set(request_views))
    if missing or extra:
        raise ValueError(f"critic must review every requested view exactly once; missing={missing} extra={extra}")
    ordered = [reviews_by_id[str(item["viewId"])] for item in request["views"]]
    return ordered, findings, unreviewable


def _round_defect_keys(round_record: dict[str, Any]) -> set[str]:
    return set(str(key) for key in round_record.get("defectKeys", []))


def _repeated_defects(rounds: list[dict[str, Any]], count: int) -> list[str]:
    if len(rounds) < count:
        return []
    shared = _round_defect_keys(rounds[-1])
    for record in rounds[-count:-1]:
        shared &= _round_defect_keys(record)
    return sorted(shared)


def _defect_profile(findings: list[dict[str, Any]]) -> tuple[int, int]:
    worst = max((SEVERITY_RANK[item["severity"]] for item in findings), default=0)
    return worst, len(findings)


def _is_improvement(
    previous: dict[str, Any], current: dict[str, Any], min_reduction: int
) -> bool:
    previous_worst = int(previous.get("worstSeverityRank", 0))
    current_worst = int(current.get("worstSeverityRank", 0))
    if current_worst < previous_worst:
        return True
    if current_worst > previous_worst:
        return False
    return int(previous.get("findingCount", 0)) - int(current.get("findingCount", 0)) >= min_reduction


def _blocked(
    state: dict[str, Any], reason: str, action: str, findings: list[dict[str, Any]]
) -> dict[str, Any]:
    state["nextViews"] = []
    state["status"] = "blocked"
    state["action"] = action
    state["stopReason"] = reason
    state["blockingDefects"] = findings
    state["pendingRequest"] = None
    return state


def advance_state(
    state: dict[str, Any], request: dict[str, Any], response: dict[str, Any]
) -> dict[str, Any]:
    """Validate one independent critique and produce the next adaptive views."""
    _validate_state(state)
    if state.get("status") != "needs-render":
        raise ValueError("only a needs-render state can accept a critic response")
    if request.get("kind") != KIND_REQUEST or request.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("not an adaptive harsh critic v1 request")
    if request.get("sessionId") != state.get("sessionId"):
        raise ValueError("critic request belongs to a different session")
    if request.get("round") != state.get("currentRound"):
        raise ValueError("critic request round does not match state")
    _validate_request_integrity(state, request)
    _validate_request_bindings(state, request)
    _forbid_aggregate_fields(response, "critic response")
    critic = _validate_response_identity(state, request, response)
    reviews, findings, unreviewable = _normalize_reviews(request, response)
    requested_ids = [str(item["id"]) for item in state["nextViews"]]
    request_ids = [str(item["viewId"]) for item in request.get("views", [])]
    if requested_ids != request_ids:
        raise ValueError("critic request views differ from state.nextViews")

    worst_rank, finding_count = _defect_profile(findings)
    critical_count = sum(1 for item in findings if item["severity"] == "critical")
    defect_keys = sorted({item["defectKey"] for item in findings})
    ledger_by_id = {
        str(item["viewId"]): item
        for item in state["evidenceLedger"]
        if isinstance(item, dict) and item.get("viewId")
    }
    round_evidence = [
        dict(ledger_by_id[str(view["viewId"])]) for view in request["views"]
    ]
    completed = {
        "round": state["currentRound"],
        "requestId": request["requestId"],
        "requestDigest": request["requestDigest"],
        "captureSetIntegrity": request["captureSetIntegrity"],
        "critic": {"id": critic["id"], "role": critic["role"]},
        "views": state["nextViews"],
        "evidence": round_evidence,
        "reviews": reviews,
        "defectKeys": defect_keys,
        "findingCount": finding_count,
        "criticalCount": critical_count,
        "worstSeverityRank": worst_rank,
        "criticalRulePassed": critical_count == 0,
    }
    minimum_uniform_level = int(state["policy"]["minimumUniformLevel"])
    uniform_sources = [
        view
        for view in state["nextViews"]
        if int(view.get("cell", {}).get("level", -1)) < minimum_uniform_level
    ]
    completed["minimumUniformCoverageComplete"] = not uniform_sources
    rounds = list(state["rounds"])
    rounds.append(completed)
    state["rounds"] = rounds
    state["blockingDefects"] = findings

    if unreviewable:
        return _blocked(state, "unreviewable-evidence", "request-input", findings)
    if not findings and not uniform_sources:
        state["nextViews"] = []
        state["status"] = "passed"
        state["action"] = "continue"
        state["stopReason"] = "no-defects-after-minimum-uniform-coverage"
        state["blockingDefects"] = []
        state["pendingRequest"] = None
        return state

    if findings:
        repeated = _repeated_defects(rounds, int(state["policy"]["repeatedDefectRounds"]))
        if repeated:
            state["repeatedDefects"] = repeated
            return _blocked(state, "repeated-defect", "refine-code", findings)

    plateau_streak = int(state.get("plateauStreak", 0))
    if (
        findings
        and len(rounds) >= 2
        and int(rounds[-2].get("findingCount", 0)) > 0
        and not _is_improvement(
            rounds[-2], rounds[-1], int(state["policy"]["minDefectReduction"])
        )
    ):
        plateau_streak += 1
    else:
        plateau_streak = 0
    state["plateauStreak"] = plateau_streak
    if plateau_streak >= int(state["policy"]["plateauRounds"]):
        return _blocked(state, "plateau", "request-input", findings)

    if len(rounds) >= int(state["policy"]["maxRounds"]):
        reason = "max-rounds-before-minimum-coverage" if uniform_sources else "max-rounds"
        return _blocked(state, reason, "request-input", findings)

    next_round = int(state["currentRound"]) + 1
    next_views: list[dict[str, Any]] = []
    if uniform_sources:
        # Fail-closed baseline: even six clean face centres are not enough.
        # Subdivide EVERY under-covered face cell before defect-only refinement.
        subdivision_sources = uniform_sources
        state["refinementMode"] = "minimum-uniform-coverage"
    else:
        defective_view_ids = {item["viewId"] for item in findings}
        subdivision_sources = [
            view for view in state["nextViews"] if view["id"] in defective_view_ids
        ]
        state["refinementMode"] = "defect-directed"
    for view in subdivision_sources:
        if isinstance(view, dict):
            next_views.extend(subdivide_view(view, next_round))
    # A malformed critic response cannot create a finding detached from a view,
    # but keep this fail-closed check explicit because no next view would look
    # deceptively like convergence.
    if not next_views:
        return _blocked(state, "defects-without-refinement-cells", "request-input", findings)
    proposed_total = int(state["scheduledViewCount"]) + len(next_views)
    if proposed_total > int(state["policy"]["maxViews"]):
        state["unscheduledNextViewCount"] = len(next_views)
        reason = "max-views-before-minimum-coverage" if uniform_sources else "max-views"
        return _blocked(state, reason, "request-input", findings)

    state["currentRound"] = next_round
    state["nextViews"] = next_views
    state["scheduledViewCount"] = proposed_total
    state["status"] = "needs-render"
    state["action"] = "capture-next-views"
    state["stopReason"] = None
    state["pendingRequest"] = None
    return state


def _status_summary(state: dict[str, Any]) -> dict[str, Any]:
    _validate_state(state)
    return {
        "sessionId": state["sessionId"],
        "status": state["status"],
        "action": state["action"],
        "stopReason": state.get("stopReason"),
        "completedRounds": len(state["rounds"]),
        "currentRound": state["currentRound"],
        "scheduledViewCount": state["scheduledViewCount"],
        "pendingRequest": state.get("pendingRequest"),
        "nextViews": state["nextViews"],
        "blockingDefects": state.get("blockingDefects", []),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="initialize six-face adaptive scene review")
    init.add_argument("--manifest", type=Path, required=True)
    init.add_argument("--creator-id", required=True)
    init.add_argument("--out", type=Path, required=True)
    init.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS)
    init.add_argument("--max-views", type=int, default=DEFAULT_MAX_VIEWS)
    init.add_argument("--repeated-defect-rounds", type=int, default=DEFAULT_REPEATED_DEFECT_ROUNDS)
    init.add_argument("--plateau-rounds", type=int, default=DEFAULT_PLATEAU_ROUNDS)
    init.add_argument("--min-defect-reduction", type=int, default=DEFAULT_MIN_DEFECT_REDUCTION)
    init.add_argument("--minimum-uniform-level", type=int, default=DEFAULT_MINIMUM_UNIFORM_LEVEL)
    init.add_argument("--allow-holes", action="store_true")

    request_parser = commands.add_parser(
        "request", help="bind real scene captures and emit an independent critic request"
    )
    request_parser.add_argument("--state", type=Path, required=True)
    request_parser.add_argument("--manifest", type=Path)
    request_parser.add_argument("--out", type=Path, required=True)

    advance = commands.add_parser("advance", help="validate critic response and plan nextViews")
    advance.add_argument("--state", type=Path, required=True)
    advance.add_argument("--request", type=Path, required=True)
    advance.add_argument("--reviews", type=Path, required=True)
    advance.add_argument(
        "--in-place",
        action="store_true",
        required=True,
        help="consume the pending request in the canonical source state",
    )

    status = commands.add_parser("status", help="show current critic state")
    status.add_argument("--state", type=Path, required=True)
    status.add_argument("--json", action="store_true")

    try:
        args = parser.parse_args(argv)
        if args.command == "init":
            result = init_state(
                args.manifest,
                args.creator_id,
                max_rounds=args.max_rounds,
                max_views=args.max_views,
                repeated_defect_rounds=args.repeated_defect_rounds,
                plateau_rounds=args.plateau_rounds,
                min_defect_reduction=args.min_defect_reduction,
                minimum_uniform_level=args.minimum_uniform_level,
                allow_holes=args.allow_holes,
            )
            output = args.out.expanduser().resolve()
            if output.exists():
                raise ValueError(f"refusing to overwrite existing state: {output}")
            _write_json(output, result)
            print(json.dumps(_status_summary(result), indent=2, ensure_ascii=False))
            return 0
        if args.command == "request":
            state_path = args.state.expanduser().resolve()
            state = _read_json(state_path)
            manifest_path = (
                args.manifest.expanduser().resolve()
                if args.manifest
                else Path(str(state.get("scene", {}).get("manifestPath", ""))).expanduser().resolve()
            )
            output = args.out.expanduser().resolve()
            if output.exists():
                raise ValueError(f"refusing to overwrite existing critic request: {output}")
            result = build_critic_request(state, manifest_path)
            _write_json(output, result)
            # A request file without its state pin is unusable; write the request
            # first, then atomically persist the pending digest in the state.
            # If the second write fails, advance still fails closed because the
            # on-disk state has no matching pending request.
            _write_json(state_path, state)
            print(
                json.dumps(
                    {
                        "request": str(output),
                        "requestId": result["requestId"],
                        "requestDigest": result["requestDigest"],
                        "statePinned": str(state_path),
                        "views": len(result["views"]),
                    },
                    indent=2,
                )
            )
            return 0
        if args.command == "advance":
            state_path = args.state.expanduser().resolve()
            state = _read_json(state_path)
            result = advance_state(
                state,
                _read_json(args.request.expanduser().resolve()),
                _read_json(args.reviews.expanduser().resolve()),
            )
            # There is exactly one canonical state transition.  ``advance``
            # intentionally has no --out mode because two filesystem targets
            # cannot be committed atomically and a failed snapshot must never
            # make a consumed request look retryable.
            _write_json(state_path, result)
            print(json.dumps(_status_summary(result), indent=2, ensure_ascii=False))
            return 1 if result["status"] == "blocked" else 0
        if args.command == "status":
            result = _status_summary(_read_json(args.state.expanduser().resolve()))
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                print(
                    f"status={result['status']} action={result['action']} "
                    f"round={result['currentRound']} views={result['scheduledViewCount']} "
                    f"stop={result['stopReason']}"
                )
            return 1 if result["status"] == "blocked" else 0
        raise ValueError(f"unknown command: {args.command}")
    except Exception as exc:  # noqa: BLE001 - CLI boundary must fail closed without a traceback
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
