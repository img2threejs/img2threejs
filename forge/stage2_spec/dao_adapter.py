#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any


@dataclass(frozen=True, slots=True)
class DaoFamilyAdapter:
    family: str
    subtype: str
    slots: tuple[str, ...]
    topology: tuple[str, ...]
    feature_targets: tuple[str, ...]
    attachment_rules: tuple[str, ...]
    review_viewpoints: tuple[str, ...]
    slot_components: tuple[tuple[str, str], ...] = ()
    slot_sockets: tuple[tuple[str, str], ...] = ()
    integral_component_owners: tuple[tuple[str, str], ...] = ()

    def component_tree_contract(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["featureTargets"] = payload.pop("feature_targets")
        payload["attachmentRules"] = payload.pop("attachment_rules")
        payload["reviewViewpoints"] = payload.pop("review_viewpoints")
        payload["slotComponents"] = dict(payload.pop("slot_components"))
        payload["slotSockets"] = dict(payload.pop("slot_sockets"))
        payload["integralComponentOwners"] = dict(payload.pop("integral_component_owners"))
        return payload


_DAO = DaoFamilyAdapter(
    family="dao",
    subtype="generic-supported",
    slots=("blade", "guard", "front-ferrule", "handle", "rear-ferrule", "pommel"),
    topology=("ground-blade", "assembled-solid", "profile-with-hole"),
    feature_targets=("blade-edge-spine", "blade-thickness", "guard", "grip", "pommel-negative-space"),
    attachment_rules=("blade-to-guard", "guard-to-handle", "handle-to-pommel"),
    review_viewpoints=("reference-face", "true-side", "top-down", "orbit-left", "orbit-right"),
)

SUPPORTED_DAO_SUBTYPES = frozenset({"han-huan-shou"})

_HAN_HUAN_SHOU = replace(
    _DAO,
    subtype="han-huan-shou",
    slot_components=(
        ("blade", "blade"),
        ("guard", "guard"),
        ("front-ferrule", "collar"),
        ("handle", "handle"),
        ("rear-ferrule", "ferrule"),
        ("pommel", "ring"),
    ),
    slot_sockets=(
        ("guard", "blade-heel"),
        ("front-ferrule", "guard-back"),
        ("handle", "front-ferrule-back"),
        ("rear-ferrule", "handle-back"),
        ("pommel", "rear-ferrule-back"),
    ),
    integral_component_owners=(
        ("hamon-1", "blade"),
        ("hamon-2", "blade"),
        ("hamon-3", "blade"),
        ("wrap-seam-1", "handle"),
        ("wrap-seam-2", "handle"),
        ("stud-a", "handle"),
        ("stud-b", "handle"),
        ("stud-c", "handle"),
        ("stud-d", "handle"),
        ("stud-e", "handle"),
        ("stud-f", "handle"),
        ("stud-seat-a", "handle"),
        ("stud-seat-b", "handle"),
        ("stud-seat-c", "handle"),
        ("stud-seat-d", "handle"),
        ("stud-seat-e", "handle"),
        ("stud-seat-f", "handle"),
        ("ring-neck", "ring"),
        ("ring-engraving-outer", "ring"),
        ("ring-engraving-middle", "ring"),
        ("ring-engraving-inner", "ring"),
    ),
)


def get_dao_family_adapter(subtype: str | None = None) -> DaoFamilyAdapter:
    if subtype and subtype not in SUPPORTED_DAO_SUBTYPES:
        raise ValueError(f"unsupported-subtype: {subtype}")
    return _DAO if subtype is None else _HAN_HUAN_SHOU


def validate_dao_component_tree(adapter: DaoFamilyAdapter, components: list[dict[str, Any]]) -> list[str]:
    """Check subtype slots and integral-detail ownership before code generation."""
    by_id = {
        str(component.get("id")): component
        for component in components
        if isinstance(component, dict) and component.get("id")
    }
    failures: list[str] = []
    for slot, component_id in adapter.slot_components:
        if component_id not in by_id:
            failures.append(f"dao slot {slot!r} requires component {component_id!r}")
    slot_components = dict(adapter.slot_components)
    for slot, socket_id in adapter.slot_sockets:
        component_id = slot_components.get(slot)
        component = by_id.get(component_id or "")
        attachment = component.get("attachment") if isinstance(component, dict) else None
        if not isinstance(attachment, dict) or attachment.get("parentSocket") != socket_id:
            failures.append(f"dao slot {slot!r} must attach at socket {socket_id!r}")
    for component_id, owner_id in adapter.integral_component_owners:
        component = by_id.get(component_id)
        if component is None:
            failures.append(f"dao integral component {component_id!r} is missing")
            continue
        if owner_id not in by_id:
            failures.append(f"dao integral owner {owner_id!r} is missing")
        if component.get("explodeWithParent") != owner_id:
            failures.append(f"dao integral component {component_id!r} must explode with {owner_id!r}")
        action = component.get("actionProfile")
        destruction = action.get("destruction") if isinstance(action, dict) else None
        if not isinstance(destruction, dict) or destruction.get("fractureGroup") != owner_id:
            failures.append(f"dao integral component {component_id!r} must use destruction group {owner_id!r}")
    return failures


@dataclass(frozen=True, slots=True)
class DaoDimensions:
    blade_length: float
    blade_thickness: float
    guard_kind: str
    guard_diameter: float
    guard_thickness: float
    front_ferrule_length: float
    front_ferrule_diameter: float
    handle_kind: str
    handle_length: float
    handle_diameter: float
    rear_ferrule_length: float
    rear_ferrule_diameter: float
    pommel_kind: str
    pommel_length: float
    inlay_count: int = 0
    front_overlap: float = 0.0
    handle_overlap: float = 0.0
    rear_overlap: float = 0.0
    pommel_overlap: float = 0.0


def assemble_dao_dimensions(dimensions: DaoDimensions) -> dict[str, Any]:
    positive = {
        name: value
        for name, value in asdict(dimensions).items()
        if name.endswith(("_length", "_thickness", "_diameter"))
    }
    invalid = [name for name, value in positive.items() if not isinstance(value, (int, float)) or value <= 0]
    if invalid:
        raise ValueError(f"dao dimensions must be positive: {', '.join(sorted(invalid))}")
    if dimensions.guard_kind not in {"disk", "bar", "none"}:
        raise ValueError(f"unsupported dao guard kind: {dimensions.guard_kind}")
    if dimensions.handle_kind not in {"cord-wrap", "wood", "bare-tang"}:
        raise ValueError(f"unsupported dao handle kind: {dimensions.handle_kind}")
    if dimensions.pommel_kind not in {"ring", "cap", "none"}:
        raise ValueError(f"unsupported dao pommel kind: {dimensions.pommel_kind}")
    if dimensions.inlay_count < 0:
        raise ValueError("dao inlay_count must be non-negative")

    blade_heel_x = dimensions.blade_length
    guard_x = blade_heel_x
    front_ferrule_x = (
        guard_x
        + dimensions.guard_thickness * 0.5
        + dimensions.front_ferrule_length * 0.5
        - dimensions.front_overlap
    )
    handle_x = (
        front_ferrule_x
        + dimensions.front_ferrule_length * 0.5
        + dimensions.handle_length * 0.5
        - dimensions.handle_overlap
    )
    rear_ferrule_x = (
        handle_x
        + dimensions.handle_length * 0.5
        + dimensions.rear_ferrule_length * 0.5
        - dimensions.rear_overlap
    )
    pommel_x = (
        rear_ferrule_x
        + dimensions.rear_ferrule_length * 0.5
        + dimensions.pommel_length * 0.5
        - dimensions.pommel_overlap
    )
    if dimensions.inlay_count:
        spacing = dimensions.handle_length / (dimensions.inlay_count + 1)
        inlay_xs = tuple(
            handle_x - dimensions.handle_length * 0.5 + spacing * index
            for index in range(1, dimensions.inlay_count + 1)
        )
    else:
        inlay_xs = ()

    return {
        "blade": {"heelX": blade_heel_x, "length": dimensions.blade_length, "thickness": dimensions.blade_thickness},
        "guard": {"kind": dimensions.guard_kind, "x": guard_x, "diameter": dimensions.guard_diameter, "thickness": dimensions.guard_thickness},
        "frontFerrule": {"x": front_ferrule_x, "length": dimensions.front_ferrule_length, "diameter": dimensions.front_ferrule_diameter},
        "handle": {"kind": dimensions.handle_kind, "x": handle_x, "length": dimensions.handle_length, "diameter": dimensions.handle_diameter},
        "rearFerrule": {"x": rear_ferrule_x, "length": dimensions.rear_ferrule_length, "diameter": dimensions.rear_ferrule_diameter},
        "pommel": {"kind": dimensions.pommel_kind, "x": pommel_x, "length": dimensions.pommel_length},
        "inlayXs": inlay_xs,
    }
