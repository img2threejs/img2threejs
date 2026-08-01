#!/usr/bin/env python3
"""Per-family geometry-topology adapters for CS2 weapon/glove skins.

Each FamilyAdapter is a QUALITATIVE CONTRACT (topology hints, painted-region names, feature
targets) for a human/agent to build a componentTree against -- it does not generate geometry
itself. The real component tree still has to be measured from the actual reference image(s)
and hand-authored, the same way src/demos/awp-medusa/createAwpMedusaModel.ts was built in the
img2threejs-showcase project: registering a family/subtype here only stops the intake gate from
rejecting the item before that work starts.

Canonical taxonomy source: the community-maintained `ByMykel/CSGO-API` skins.json
(https://github.com/ByMykel/CSGO-API), fetched via forge/stage1_intake/fetch_cs2_metadata.py.
Each record's `weapon.name` gives the authoritative weapon name; cross-check the *_SUBTYPES
frozensets below against the distinct weapon names per category if Valve ships a new weapon,
rather than trusting this file's hardcoded list to stay current on its own.
"""
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
SUPPORTED_KNIFE_SUBTYPES = frozenset({
    "karambit", "butterfly", "bayonet", "m9", "flip", "gut", "falchion", "bowie", "navaja",
    "talon", "classic",
    # Added when generalizing CS2 family support beyond the original knife-only fast path:
    "huntsman", "kukri", "nomad", "paracord", "shadow-daggers", "skeleton", "stiletto",
    "survival", "ursus",
})

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
SUPPORTED_PISTOL_SUBTYPES = frozenset({
    "glock-18", "usp-s", "p2000", "dual-berettas", "p250", "cz75-auto", "five-seven", "tec-9",
    "desert-eagle", "r8-revolver",
})

# A bolt-action sniper rifle is neither a knife nor a pistol: barrel and scope are true
# surfaces of revolution (silhouette height == diameter along their whole length, unlike the
# pistol's traced-outline slide), the receiver/stock is one boxy extruded shell with a genuine
# boolean thumbhole rather than a two-body slide/frame assembly, and small attached
# appendages (bolt handle, bipod leg, scope rings) need their own attachment contracts rather
# than being folded into the body silhouette. This is CS2's "Sniper Rifles" Market category
# (AWP/SSG08/G3SG1/SCAR-20), distinct from the semi-auto "Rifles" category below -- they were
# a single 'rifle' family in an earlier revision of this file (built against AWP only), split
# out once non-sniper rifles were added so an AK-47 request can't collide with AWP's adapter.
_SNIPER = FamilyAdapter(
    "sniper", "generic-supported",
    ("revolve", "extrude-traced-outline", "outline-with-hole", "assembled-solid", "bent-rod"),
    ("receiver-painted", "stock-painted", "scope-bell-painted-partial",
     "barrel-bare-metal", "scope-tube-bare-metal", "magazine-bare-metal", "buttplate-bare-rubber"),
    ("skin-finish", "substrate"),
    ("silhouette", "barrel-and-muzzle-device", "receiver-and-ejection-port",
     "scope-bell-tube-turret-profile", "scope-ring-mounts", "magazine-and-well",
     "trigger-and-guard", "thumbhole-or-grip", "bolt-handle-or-bipod", "buttplate"),
    ("scope-to-rail", "magazine-to-magwell", "trigger-to-receiver",
     "bolt-handle-to-receiver", "bipod-to-barrel"),
    ("reference", "orbit-left", "orbit-right", "muzzle-on", "top-down"),
)
# AWP is bolt-action (the shape above was measured against it). SSG08/G3SG1/SCAR-20 are not yet
# built against a real reference and may need adapter adjustments (G3SG1/SCAR-20 are semi-auto
# with a gas system and a larger detachable magazine, closer to the rifle family below) once one
# is actually attempted -- registering the subtype here only unblocks intake, per this file's
# module docstring.
SUPPORTED_SNIPER_SUBTYPES = frozenset({"awp", "ssg08", "g3sg1", "scar-20"})

# A semi/full-auto rifle (CS2's "Rifles" Market category: AK-47, M4A4, M4A1-S, FAMAS, Galil AR,
# SG 553, AUG) differs from the sniper family above mainly by NOT carrying a separate scope
# assembly (SG553/AUG have an integrated low-power scope molded into the receiver instead of a
# detachable rail-mounted unit) and by usually showing a distinct muzzle device (compensator/
# birdcage), a pistol grip separate from the stock, and a shorter, plainer buttstock.
_RIFLE = FamilyAdapter(
    "rifle", "generic-supported",
    ("revolve", "extrude-traced-outline", "outline-with-hole", "assembled-solid", "bent-rod"),
    ("receiver-painted", "handguard-painted", "stock-painted", "pistol-grip-painted",
     "barrel-bare-metal", "gas-block-bare-metal", "magazine-bare-metal", "sights-bare-metal"),
    ("skin-finish", "substrate"),
    ("silhouette", "barrel-and-muzzle-device", "gas-block-and-handguard",
     "receiver-and-ejection-port", "front-and-rear-sights-or-integrated-scope",
     "magazine-and-well", "trigger-and-guard", "pistol-grip", "stock-and-buttplate"),
    ("magazine-to-magwell", "trigger-to-receiver", "pistol-grip-to-receiver",
     "stock-to-receiver", "handguard-to-barrel"),
    ("reference", "orbit-left", "orbit-right", "muzzle-on", "top-down"),
)
SUPPORTED_RIFLE_SUBTYPES = frozenset({"ak-47", "m4a4", "m4a1-s", "famas", "galil-ar", "sg-553", "aug"})

# An SMG shares the rifle family's broad silhouette grammar (barrel/receiver/stock/magazine)
# but is shorter, more often has a folding or fixed wire/skeleton stock (or none, on some
# models), and several subtypes have genuinely distinct magazine topology worth calling out
# explicitly rather than assuming a generic box magazine: P90 feeds from a horizontal
# top-mounted magazine, PP-Bizon from a vertical helical drum -- both are a form-affecting
# silhouette feature, not a texture detail.
_SMG = FamilyAdapter(
    "smg", "generic-supported",
    ("revolve", "extrude-traced-outline", "outline-with-hole", "assembled-solid", "bent-rod"),
    ("receiver-painted", "handguard-painted", "stock-painted",
     "barrel-bare-metal", "magazine-bare-metal", "sights-bare-metal"),
    ("skin-finish", "substrate"),
    ("silhouette", "barrel-and-muzzle-device", "receiver-and-ejection-port",
     "magazine-and-well-or-top-mount-or-drum", "trigger-and-guard", "pistol-grip",
     "stock-fixed-or-folding-or-absent", "sights"),
    ("magazine-to-magwell", "trigger-to-receiver", "stock-to-receiver"),
    ("reference", "orbit-left", "orbit-right", "muzzle-on", "top-down"),
)
SUPPORTED_SMG_SUBTYPES = frozenset({"mac-10", "mp9", "mp7", "mp5-sd", "ump-45", "p90", "pp-bizon"})

# "Heavy" is CS2's Market category for shotguns AND machine guns, which are not one shape:
# shotguns (Nova, XM1014, Sawed-Off, MAG-7) center on a pump/tube magazine under the barrel and
# often a shorter, wider receiver; machine guns (M249, Negev) are belt/box-fed with a bipod and
# a much longer, heavier barrel shroud. This adapter's topology/feature lists cover the union;
# expect to specialize per subtype (e.g. drop 'bipod' for a shotgun, drop 'pump-slide-and-tube'
# for a machine gun) once a specific reference is attempted, per this file's module docstring.
_HEAVY = FamilyAdapter(
    "heavy", "generic-supported",
    ("revolve", "extrude-traced-outline", "outline-with-hole", "assembled-solid", "bent-rod"),
    ("receiver-painted", "stock-painted", "handguard-painted",
     "barrel-bare-metal", "magazine-or-feed-bare-metal"),
    ("skin-finish", "substrate"),
    ("silhouette", "barrel-and-muzzle-device", "receiver", "pump-slide-and-tube-or-belt-feed",
     "magazine-or-ammo-box", "trigger-and-guard", "stock-and-buttplate", "bipod-if-present"),
    ("magazine-or-feed-to-receiver", "trigger-to-receiver", "stock-to-receiver",
     "bipod-to-barrel"),
    ("reference", "orbit-left", "orbit-right", "muzzle-on", "top-down"),
)
SUPPORTED_HEAVY_SUBTYPES = frozenset({"nova", "xm1014", "sawed-off", "mag-7", "m249", "negev"})

# Gloves are worn on the player model's hands -- they are not a weapon topology (no barrel,
# receiver, or blade) and not a full humanoid character either (no face/torso/proportions).
# This is its own hand/wrist-specific contract rather than being forced through either the
# weapon adapters above or the skill's separate humanoid character track.
_GLOVE = FamilyAdapter(
    "glove", "generic-supported",
    ("extrude-traced-outline", "outline-with-hole", "assembled-solid", "curve-sweep"),
    ("cuff-painted", "back-of-hand-painted", "knuckle-painted", "palm-bare-material",
     "finger-segments-painted", "stitching-bare-material"),
    ("skin-finish", "substrate"),
    ("silhouette", "cuff-and-wrist-strap", "back-of-hand-panel", "knuckle-plate-or-padding",
     "finger-and-thumb-segmentation", "palm-grip-texture", "stitching-and-seams"),
    ("finger-segments-to-palm", "cuff-to-wrist"),
    ("reference", "palm-side", "back-of-hand-side"),
)
SUPPORTED_GLOVE_SUBTYPES = frozenset({
    "bloodhound", "broken-fang", "driver", "hand-wraps", "hydra", "moto", "specialist", "sport",
})

_ADAPTERS = {
    "knife": (_KNIFE, SUPPORTED_KNIFE_SUBTYPES),
    "pistol": (_PISTOL, SUPPORTED_PISTOL_SUBTYPES),
    "sniper": (_SNIPER, SUPPORTED_SNIPER_SUBTYPES),
    "rifle": (_RIFLE, SUPPORTED_RIFLE_SUBTYPES),
    "smg": (_SMG, SUPPORTED_SMG_SUBTYPES),
    "heavy": (_HEAVY, SUPPORTED_HEAVY_SUBTYPES),
    "glove": (_GLOVE, SUPPORTED_GLOVE_SUBTYPES),
}


def get_family_adapter(family: str, subtype: str | None = None) -> FamilyAdapter:
    entry = _ADAPTERS.get(family)
    if entry is None:
        raise ValueError(f"unsupported-family: {family}")
    adapter, supported = entry
    if subtype and subtype not in supported:
        raise ValueError(f"unsupported-subtype: {subtype}")
    return adapter if subtype is None else replace(adapter, subtype=subtype)
