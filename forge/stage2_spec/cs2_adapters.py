#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True, slots=True)
class FamilyAdapter:
    family: str
    subtype: str
    topology: tuple[str, ...]
    painted_regions: tuple[str, ...]
    material_assignments: tuple[str, ...]
    feature_targets: tuple[str, ...]
    attachment_rules: tuple[str, ...]
    review_viewpoints: tuple[str, ...]
    adapter_id: str = ""
    contract_version: str = "1"
    active: bool = True
    fixture_id: str = ""

    def component_tree_contract(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "subtype": self.subtype,
            "topology": self.topology,
            "paintedRegions": self.painted_regions,
            "materialAssignments": self.material_assignments,
            "featureTargets": self.feature_targets,
            "attachmentRules": self.attachment_rules,
            "reviewViewpoints": self.review_viewpoints,
            "adapterId": self.adapter_id,
            "contractVersion": self.contract_version,
            "active": self.active,
            "fixtureId": self.fixture_id,
        }


_KNIFE = FamilyAdapter(
    "knife", "generic-supported", ("ground-blade", "curve-sweep", "extrude", "assembled-solid"),
    ("blade-painted", "grip-painted", "guard-bare-metal", "pommel-bare-metal"),
    ("skin-finish", "substrate"),
    ("silhouette", "blade-edge-spine", "grip", "guard-quillon", "fastener", "pommel"),
    ("guard-to-blade", "grip-to-guard", "pommel-to-grip"),
    ("reference", "orbit-left", "orbit-right"),
    "cs2-knife-v1", "1", True, "cs2-knife-front-v1",
)
SUPPORTED_KNIFE_SUBTYPES = frozenset({"karambit", "butterfly", "bayonet", "m9", "flip", "gut", "falchion", "bowie", "navaja", "talon", "classic"})

# A pistol is not a knife with different proportions: it is a two-body assembly (slide riding a
# frame) with a through-hole in the trigger guard, an internal mechanism the shell may reveal,
# and controls that stand proud of the broad faces. It gets its own tree rather than the knife
# tree with renamed parts.
_PISTOL = FamilyAdapter(
    "pistol", "generic-supported",
    ("extrude-traced-outline", "outline-with-hole", "assembled-solid", "revolve"),
    ("slide-painted", "frame-painted", "magazine-painted", "grip-panel-painted",
     "breech-bare-metal", "barrel-bare-metal", "controls-bare-polymer"),
    ("skin-finish", "substrate", "translucent-shell", "internal-mechanism"),
    ("silhouette", "slide-frame-parting-line", "ejection-port", "sights",
     "trigger-and-safety-blade", "trigger-guard-loop", "grip-rake-and-panel",
     "magazine-extension", "pin-and-control-placement", "muzzle-and-barrel"),
    ("slide-to-frame", "magazine-to-magwell", "trigger-to-pin",
     "grip-panel-to-frame", "internals-inside-shell"),
    ("reference", "orbit-left", "orbit-right", "muzzle-on", "top-down"),
    "cs2-pistol-glock18-v1", "1", True, "cs2-pistol-front-v1",
)
SUPPORTED_PISTOL_SUBTYPES = frozenset({"glock-18"})

_RIFLE = FamilyAdapter(
    "rifle", "generic-supported",
    ("custom-profile-loft", "revolve", "tube", "outline-with-hole", "assembled-solid",
     "instanced-fasteners", "projected-surface"),
    ("stock-painted", "receiver-painted", "grip-painted", "magazine-painted",
     "barrel-bare-metal", "bolt-bare-metal", "optic-bare-metal", "bipod-bare-metal"),
    ("skin-finish", "painted-shell", "bare-metal", "projected-albedo", "wear-response"),
    ("silhouette", "thumbhole-stock", "receiver-barrel-axis", "bolt-handle", "scope-and-rings",
     "magazine-and-trigger", "muzzle-device", "folded-bipod", "medusa-pattern-placement"),
    ("stock-to-receiver", "barrel-to-receiver", "optic-to-rail", "bolt-to-receiver",
     "magazine-to-well", "grip-to-trigger-guard", "bipod-to-fore-end"),
    ("reference", "orbit-left", "orbit-right", "top-down", "muzzle-on"),
    "cs2-rifle-v1", "1", True, "cs2-rifle-awp-front-v1",
)
SUPPORTED_RIFLE_SUBTYPES = frozenset({"awp"})

# V2 is a real versioned rifle contract, not a metadata alias for the knife tree or an
# unregistered route string. Keep the v1 contract available for existing manifests, while
# giving the foundation-first V2 rebuild its own component/fixture identity. The richer
# topology and attachment vocabulary mirrors the measured AWP V2 component tree; dimensions
# still come from the versioned reference packet, never from this adapter alone.
_RIFLE_V2 = FamilyAdapter(
    "rifle", "generic-supported",
    (
        "custom-profile-loft", "revolve", "tube", "outline-with-hole", "assembled-solid",
        "instanced-fasteners", "projected-surface", "spring-helix", "telescoping-leg",
        "optic-ring-clamp", "mechanical-pivot", "functional-clearance",
    ),
    (
        "stock-painted", "receiver-painted", "grip-painted", "magazine-painted",
        "barrel-bare-metal", "bolt-bare-metal", "optic-bare-metal", "bipod-bare-metal",
        "optic-crown-decal",
    ),
    (
        "skin-finish", "painted-shell", "bare-metal", "projected-albedo", "wear-response",
        "mirror-glass", "foil-sticker", "rubber-contact",
    ),
    (
        "silhouette", "thumbhole-stock", "receiver-barrel-axis", "receiver-action",
        "centered-trigger-guard", "bolt-handle", "scope-and-rings", "magazine-and-trigger",
        "muzzle-bore", "folded-bipod", "independent-coil-springs", "medusa-pattern-placement",
    ),
    (
        "stock-to-receiver", "barrel-to-receiver", "optic-to-rail", "bolt-to-receiver",
        "magazine-to-well", "grip-to-trigger-guard", "trigger-clearance-to-receiver",
        "bipod-to-fore-end", "spring-to-hinge-and-leg",
    ),
    (
        "reference", "orbit-left", "orbit-right", "top-down", "muzzle-on",
        "trigger-close", "bipod-close",
    ),
    "cs2-rifle-v2", "2", True, "cs2-rifle-awp-v2-blockout",
)

_ADAPTERS = {
    "knife": (_KNIFE, SUPPORTED_KNIFE_SUBTYPES),
    "pistol": (_PISTOL, SUPPORTED_PISTOL_SUBTYPES),
    "rifle": (_RIFLE, SUPPORTED_RIFLE_SUBTYPES),
}

_ADAPTERS_BY_ID = {
    adapter.adapter_id: (adapter, supported)
    for adapter, supported in (
        (_KNIFE, SUPPORTED_KNIFE_SUBTYPES),
        (_PISTOL, SUPPORTED_PISTOL_SUBTYPES),
        (_RIFLE, SUPPORTED_RIFLE_SUBTYPES),
        (_RIFLE_V2, SUPPORTED_RIFLE_SUBTYPES),
    )
}

FAMILY_ALIASES = {"sniper": "rifle"}


def registered_adapter_ids(family: str, subtype: str | None = None) -> tuple[str, ...]:
    """Return concrete adapter ids for a canonical family/subtype.

    The default intake route remains the v1 contract for backward compatibility. Callers that
    have an authoritative versioned route must pass that id to ``get_family_adapter`` instead of
    silently accepting the default.
    """
    canonical_family = FAMILY_ALIASES.get(family, family)
    return tuple(
        adapter_id
        for adapter_id, (adapter, supported) in _ADAPTERS_BY_ID.items()
        if adapter.family == canonical_family and (subtype is None or subtype in supported)
    )


def get_family_adapter(
    family: str,
    subtype: str | None = None,
    *,
    adapter_id: str | None = None,
) -> FamilyAdapter:
    """Resolve a registered family adapter, optionally by its explicit versioned id."""
    canonical_family = FAMILY_ALIASES.get(family, family)
    if family == "sniper" and subtype != "awp":
        raise ValueError(f"unsupported-family: {family}")
    if adapter_id is not None:
        entry = _ADAPTERS_BY_ID.get(adapter_id)
        if entry is None:
            raise ValueError(f"unsupported-adapter: {adapter_id}")
        if entry[0].family != canonical_family:
            raise ValueError(f"adapter-family-mismatch: {adapter_id}:{canonical_family}")
    else:
        entry = _ADAPTERS.get(canonical_family)
    if entry is None:
        raise ValueError(f"unsupported-family: {family}")
    adapter, supported = entry
    if subtype and subtype not in supported:
        raise ValueError(f"unsupported-subtype: {subtype}")
    return adapter if subtype is None else replace(adapter, subtype=subtype, family=canonical_family)
