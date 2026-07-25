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


def get_family_adapter(family: str, subtype: str | None = None) -> FamilyAdapter:
    if family != "knife":
        raise ValueError(f"unsupported-family: {family}")
    if subtype and subtype not in SUPPORTED_KNIFE_SUBTYPES:
        raise ValueError(f"unsupported-subtype: {subtype}")
    return _KNIFE if subtype is None else replace(_KNIFE, subtype=subtype)
