"""Pull a domain's spec augmentation into a base-authored spec.

The base pipeline authors a skeleton and lets the agent infer the shape from the reference. When a
domain plugin is installed, it publishes an authoritative recipe as a workspace artifact and the base
merges it here. The base PULLS; a plugin never writes into the base's spec (PLUGIN_CONTRACT.md §14).

Three partitions, three different rules, because they carry different kinds of authority:

  specSections     domain-authored content. Accepted opaquely -- the base cannot validate a finish
                   recipe or a rig -- but refused for any key the base owns.
  assessmentPatch  merged into preSpecAssessment. The resolved-domain marker is refused: that is set
                   by domain resolution, never proposed by the artifact that resolution selected.
  qualityFloors    merged RAISE-ONLY. This is the security property of the whole mechanism: a plugin
                   must never be able to lower the bar it was installed to raise.

BASE_OWNED is a deny-list on purpose. An allow-list would have to name every domain's sections, and
the base naming its domains is exactly what this design exists to stop.
"""

from __future__ import annotations

from typing import Any, Final

ARTIFACT_KIND: Final = "spec-augmentation-v1"

BASE_OWNED: Final = frozenset({"qualityContract", "preSpecAssessment", "pipelineRouting", "sourceImage", "targetName", "localSpecSearch"})

# The authority ruling: for each spec section the base knows of, WHETHER a plugin may author it and
# WHY. It sits here rather than in a design document because the reasoning is about this deny-list
# and drifts from it otherwise.
#
# IT IS NOT AN ALLOW-LIST, and must never become one. The merge still admits a section named nowhere
# below -- that is the deny-list property this module's docstring defends, and a third-party domain
# contributing a section the base has never heard of is the mechanism working, not a gap. What the
# ruling buys is that a section the base HAS thought about carries the thinking: `rig` is admitted
# opaquely on purpose and re-checked afterwards, which is a decision, and without it written down the
# next reader sees only that the validator happens to run.
#
# A provider may assert its own sections appear here (plugin-character's
# tests/test_spec_augmentation_artifact.py does), which is a check on the base having ruled, not a
# gate on the plugin.
SECTION_AUTHORITY: Final = {
    "componentTree": "PLUGIN. The domain's own decomposition. The base has no view on how many parts "
                     "a character or a rifle has; it validates shape, not anatomy.",
    "rig": "PLUGIN, then re-checked. Admitted opaquely -- the base cannot judge a skeleton it did "
           "not design -- but validate_rig_admission's five checks run on it afterwards, because "
           "opaque admission of a structure stage5 will bind vertices to is not the same as "
           "unchecked admission.",
    "materials": "PLUGIN. Material ids and their physics are domain vocabulary. The base's own "
                 "generic default is replaced wholesale, so a provider that wants it must carry it.",
    "buildPasses": "PLUGIN. The pass list is the domain's build order.",
    "featureReviewTargets": "PLUGIN. Which features get scored, and against what, is domain "
                            "knowledge; the SCORES stay base-owned via qualityContract.",
    "sculptPipeline": "PLUGIN, with a base remainder. The provider sets passOrder and currentPass; "
                      "passGateMode, completedPasses, lastCompletedPass, blockedReason and "
                      "nextRequiredEvidence are gate state the base advances. Because the section is "
                      "assigned wholesale, a provider MUST carry those five forward or the merge "
                      "drops them -- there is no per-key fallback, deliberately: a partial section "
                      "would make 'wholesale' untrue for one key and true for the rest.",
    **{key: "BASE. In BASE_OWNED; the merge refuses it." for key in sorted(BASE_OWNED)},
}

# assessmentPatch keys whose values carry guarded content (the domain marker, the raise-only detail
# floor). Named once, like BASE_OWNED: a future guarded key is added here, and the loop's shape
# guard picks it up without a second edit site.
PATCH_GUARDED: Final = frozenset({"objectClass", "detailInventory"})

# Ordered loosest to strictest. A domain may move a tier stricter, never looser.
TIER_ORDER: Final = ("simple", "moderate", "complex", "ultra-complex")


class SpecAugmentationError(ValueError):
    pass


def _stricter_tier(current: Any, proposed: Any, clamped: list[str]) -> Any:
    if proposed not in TIER_ORDER:
        raise SpecAugmentationError(f"unknown quality tier {proposed!r}; expected one of {', '.join(TIER_ORDER)}")
    if current is None:
        return proposed
    if current not in TIER_ORDER:
        # A malformed base tier is a base defect to surface, not a blank the plugin gets to fill:
        # silently accepting the proposal here let a looser tier replace a typo'd stricter one.
        raise SpecAugmentationError(f"the spec's existing quality tier {current!r} is not one of {', '.join(TIER_ORDER)}")
    if TIER_ORDER.index(proposed) > TIER_ORDER.index(current):
        return proposed
    if proposed != current:
        clamped.append(f"qualityBar: kept {current} over proposed {proposed}")
    return current


def _raise_only_number(path: str, current: Any, proposed: Any, clamped: list[str]) -> Any:
    if not isinstance(proposed, (int, float)) or isinstance(proposed, bool):
        raise SpecAugmentationError(f"{path} must be a number, got {proposed!r}")
    if isinstance(current, (int, float)) and not isinstance(current, bool) and proposed < current:
        clamped.append(f"{path}: kept {current} over proposed {proposed}")
        return current
    return proposed


def merge_spec_augmentation(spec: dict[str, Any], artifact: Any, *, domain_id: str | None = None) -> dict[str, Any]:
    """Merge `artifact` into `spec` in place and return the record of what happened."""
    if not isinstance(artifact, dict):
        raise SpecAugmentationError("spec augmentation artifact must be a JSON object")
    kind = artifact.get("kind")
    if kind != ARTIFACT_KIND:
        raise SpecAugmentationError(f"unsupported spec augmentation kind {kind!r}; this harness reads {ARTIFACT_KIND}")
    provenance = artifact.get("provenance")
    if not isinstance(provenance, dict) or not provenance.get("provider") or not provenance.get("version"):
        raise SpecAugmentationError("spec augmentation must declare provenance {provider, version}")

    unknown = sorted(set(artifact) - {"kind", "provenance", "specSections", "assessmentPatch", "qualityFloors"})
    if unknown:
        # Refused, not ignored: a typo'd partition would otherwise contribute nothing in silence.
        raise SpecAugmentationError(f"spec augmentation has unknown key(s) {unknown}")

    clamped: list[str] = []

    sections = artifact.get("specSections") or {}
    if not isinstance(sections, dict):
        raise SpecAugmentationError("specSections must be an object")
    for key, value in sections.items():
        if key in BASE_OWNED:
            raise SpecAugmentationError(f"specSections may not set the base-owned key {key!r}")
        spec[key] = value

    patch = artifact.get("assessmentPatch") or {}
    if not isinstance(patch, dict):
        raise SpecAugmentationError("assessmentPatch must be an object")
    pre = spec.setdefault("preSpecAssessment", {})
    for key, value in patch.items():
        if key in PATCH_GUARDED and not isinstance(value, dict):
            # A non-dict replacement would fall through to the plain-assignment branch below and
            # clobber the dict the guards live in -- refusing it keeps every path to a floor
            # clamped, not just the well-formed one.
            raise SpecAugmentationError(f"assessmentPatch.{key} must be an object, got {value!r}")
        if key == "objectClass":
            if "domain" in value:
                raise SpecAugmentationError("assessmentPatch may not set objectClass.domain; the base sets it from domain resolution")
            pre.setdefault("objectClass", {}).update(value)
        elif key == "detailInventory":
            # The one floor-controlled value reachable through this partition. The clamp guards the
            # VALUE, whichever partition carries it -- without this, a patch lowered the floor the
            # strict validator reads while `clamped` reported nothing, and qualityFloors' own clamp
            # never ran because the patched value arrived first.
            inv = pre.setdefault(key, {})
            for sub, proposed in value.items():
                if sub == "targetMinDetails":
                    inv[sub] = _raise_only_number("targetMinDetails", inv.get(sub), proposed, clamped)
                else:
                    inv[sub] = proposed
        elif isinstance(value, dict):
            pre.setdefault(key, {}).update(value)
        else:
            pre[key] = value
    if domain_id:
        pre.setdefault("objectClass", {})["domain"] = domain_id

    floors = artifact.get("qualityFloors") or {}
    if not isinstance(floors, dict):
        raise SpecAugmentationError("qualityFloors must be an object")
    contract = spec.setdefault("qualityContract", {})
    for key, value in floors.items():
        if key == "qualityBar":
            contract["qualityBar"] = _stricter_tier(contract.get("qualityBar"), value, clamped)
        elif key == "targetMinDetails":
            inv = pre.setdefault("detailInventory", {})
            inv["targetMinDetails"] = _raise_only_number("targetMinDetails", inv.get("targetMinDetails"), value, clamped)
        elif key == "minimumSpecDepth":
            if not isinstance(value, dict):
                raise SpecAugmentationError("qualityFloors.minimumSpecDepth must be an object")
            depth = contract.setdefault("minimumSpecDepth", {})
            for sub, proposed in value.items():
                depth[sub] = _raise_only_number(f"minimumSpecDepth.{sub}", depth.get(sub), proposed, clamped)
        else:
            raise SpecAugmentationError(f"qualityFloors has unknown key {key!r}")

    record = {
        "kind": kind,
        "provider": provenance["provider"],
        "version": provenance["version"],
        "sections": sorted(sections),
        "clamped": clamped,
    }
    spec["specAugmentation"] = record
    return record
