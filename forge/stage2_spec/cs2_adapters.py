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

    def component_tree_contract(self) -> dict[str, Any]:
        return {"family": self.family, "subtype": self.subtype, "topology": self.topology, "paintedRegions": self.painted_regions, "materialAssignments": self.material_assignments, "featureTargets": self.feature_targets, "attachmentRules": self.attachment_rules, "reviewViewpoints": self.review_viewpoints}


_KNIFE = FamilyAdapter(
    "knife", "generic-supported", ("ground-blade", "curve-sweep", "extrude", "assembled-solid"),
    ("blade-painted", "grip-painted", "guard-bare-metal", "pommel-bare-metal"),
    ("skin-finish", "substrate"),
    ("silhouette", "blade-edge-spine", "grip", "guard-quillon", "fastener", "pommel"),
    ("guard-to-blade", "grip-to-guard", "pommel-to-grip"),
    ("reference", "orbit-left", "orbit-right"),
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
)
SUPPORTED_PISTOL_SUBTYPES = frozenset({"glock-18"})

_ADAPTERS = {"knife": (_KNIFE, SUPPORTED_KNIFE_SUBTYPES), "pistol": (_PISTOL, SUPPORTED_PISTOL_SUBTYPES)}


def get_family_adapter(family: str, subtype: str | None = None) -> FamilyAdapter:
    entry = _ADAPTERS.get(family)
    if entry is None:
        raise ValueError(f"unsupported-family: {family}")
    adapter, supported = entry
    if subtype and subtype not in supported:
        raise ValueError(f"unsupported-subtype: {subtype}")
    return adapter if subtype is None else replace(adapter, subtype=subtype)
