#!/usr/bin/env python3
"""Assembly gate: does the BUILT model actually contain the parts the spec promised?

Every other gate in this pipeline scores pixels. This one scores the part tree, because a
reconstruction can pass every silhouette and colour gate while being one fused mesh wearing a
photograph — and because a component that was specified and then quietly never built leaves no
trace in a render the reviewer is looking at from the reference angle.

Three independent checks:

  A. componentTree -> model     every specified component exists as a named, selectable part;
                                no two components collapse onto the same part (fused)
  B. detailInventory -> spec    every inventoried detail still resolves to a component or
                                material field (`mapsTo`), so an observed feature cannot be
                                dropped silently between intake and spec
  C. model hygiene              meshes that belong to no named part cannot be selected or
                                exploded, so they are reported with a count

WHAT THIS CANNOT DO: it compares the model against the spec, and the spec against the detail
inventory. It cannot invent domain knowledge that intake never captured. A Glock-18 whose fire
selector was never observed, never inventoried and never specified will pass every check here.
Closing that gap is the job of the detail inventory and the family adapter, not of this script.

Usage:
    check_part_coverage.py --spec object-sculpt-spec.json --manifest parts.json
    check_part_coverage.py --spec spec.json --manifest parts.json --json coverage.json
    check_part_coverage.py --spec spec.json --manifest parts.json --require fire-selector,rear-sight

The manifest is a runtime dump of the built part tree, produced by the viewer:

    {
      "model": "glock-ghost-protocol",
      "parts": [{"name": "slide", "kind": "part", "module": "slide", "triangles": 18588}],
      "unnamedMeshes": 0,
      "integralMeshes": 61
    }

Exit code 1 when any error-severity finding survives (use --warn-only to always exit 0).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


def norm(text: str) -> str:
    """Fold naming conventions together: 'rear-sight', 'rearSight', 'Rear Sight' -> 'rearsight'."""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def load(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"check_part_coverage: no such file: {path}")
    except json.JSONDecodeError as exc:
        sys.exit(f"check_part_coverage: {path} is not valid JSON: {exc}")


def severity_for(component: dict[str, Any]) -> str:
    """A macro component or an explicitly important one is a build failure when absent.

    Micro detail is a warning: it is often legitimately folded into a parent's relief rather
    than built as a separate mesh, and failing the build for that would punish the correct
    choice.
    """
    importance = component.get("importance")
    if isinstance(importance, (int, float)):
        return "error" if importance >= 0.7 else "warning"
    return "error" if component.get("level", "meso") in {"macro", "meso"} else "warning"


def collect_local_feature_keys(spec: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for component in spec.get("componentTree", []):
        if not isinstance(component, dict):
            continue
        for field in ("id", "name"):
            if component.get(field):
                keys.add(norm(component[field]))
        for feature in component.get("localFeatures", []) or []:
            if isinstance(feature, dict):
                for field in ("id", "name", "feature"):
                    if feature.get(field):
                        keys.add(norm(feature[field]))
            elif isinstance(feature, str):
                keys.add(norm(feature))
    for material in spec.get("materials", []) or []:
        if not isinstance(material, dict):
            continue
        for override in material.get("localOverrides", []) or []:
            if isinstance(override, dict):
                for field in ("id", "name", "region"):
                    if override.get(field):
                        keys.add(norm(override[field]))
            elif isinstance(override, str):
                keys.add(norm(override))
    return keys


def find_inventory(spec: dict[str, Any], inventory_arg: str | None) -> list[dict[str, Any]]:
    if inventory_arg:
        blob = load(inventory_arg)
    else:
        blob = spec.get("preSpecAssessment", {}) or {}
    entries = blob.get("detailInventory")
    if isinstance(entries, dict):
        entries = entries.get("details")
    return [e for e in (entries or []) if isinstance(e, dict)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", required=True, help="object-sculpt-spec.json")
    ap.add_argument("--manifest", required=True, help="runtime part-tree dump from the viewer")
    ap.add_argument("--inventory", help="standalone detail-inventory.json (default: read from spec)")
    ap.add_argument("--require", help="comma-separated part names that MUST exist, whatever the spec says")
    ap.add_argument("--json", dest="json_out", help="write the findings as JSON")
    ap.add_argument("--warn-only", action="store_true", help="always exit 0")
    args = ap.parse_args()

    spec = load(args.spec)
    manifest = load(args.manifest)

    parts = [p for p in manifest.get("parts", []) if isinstance(p, dict) and p.get("name")]
    if not parts:
        print("FAIL  model exposes no named parts at all — nothing is selectable or separable")
        print("      the factory must name its meshes; see the assembly contract in SKILL.md")
        return 0 if args.warn_only else 1

    by_norm: dict[str, list[str]] = {}
    for part in parts:
        by_norm.setdefault(norm(part["name"]), []).append(part["name"])

    findings: list[dict[str, str]] = []
    claimed: dict[str, list[str]] = {}

    # ---- A. componentTree -> model -------------------------------------------------
    all_components = [c for c in spec.get("componentTree", []) if isinstance(c, dict) and c.get("id")]
    # Exclude only the TREE ROOT — a parentless component that everything else hangs off. It
    # is the spec's stand-in for the model group, and the viewer likewise never lists its root
    # as a part. Do NOT treat every parent as a container: in a real spec the blade and the
    # grip are both genuine geometry AND the parents of their own relief features, so the
    # broader rule silently drops the most important components from the check.
    parent_ids = {c["parent"] for c in all_components if c.get("parent")}
    roots = {c["id"] for c in all_components if not c.get("parent") and c["id"] in parent_ids}
    components = [c for c in all_components if c["id"] not in roots]

    missing: list[dict[str, Any]] = []
    matched: dict[str, str] = {}
    for component in components:
        cid = component["id"]
        candidates = [norm(cid)]
        if component.get("name"):
            candidates.append(norm(component["name"]))
        hit = next((c for c in candidates if c in by_norm), None)
        if hit is None:
            missing.append(component)
        else:
            matched[cid] = hit
            claimed.setdefault(hit, []).append(cid)

    for component in missing:
        cid = component["id"]
        severity = severity_for(component)
        parent = component.get("parent")
        # The tree root is not a part and is never "missing" — treat it as present so the
        # message below does not blame a top-level component's absence on its own root.
        parent_built = bool(parent) and (parent in roots or parent in matched or norm(parent) in by_norm)
        if parent_built and severity != "error":
            # The normal, CORRECT case: a bevel, a jimping band or a choil is relief cut into
            # the part it belongs to, not a mesh of its own. Report it so the coverage is
            # visible, but do not call a right answer a defect.
            findings.append({
                "check": "component-folded",
                "severity": "info",
                "subject": cid,
                "detail": f"no mesh of its own — folded into {parent!r}, which is built. Correct "
                          f"for relief; a real gap only if this needed to be separable.",
            })
        else:
            findings.append({
                "check": "component-missing",
                "severity": severity,
                "subject": cid,
                "detail": f"specified component {cid!r} has no matching part in the built model"
                          + ("" if not parent else f", and its parent {parent!r} is missing too"),
            })

    for part_key, owners in claimed.items():
        if len(owners) > 1:
            findings.append({
                "check": "components-fused",
                "severity": "error",
                "subject": by_norm[part_key][0],
                "detail": f"components {', '.join(sorted(owners))} all resolve to the single part "
                          f"{by_norm[part_key][0]!r} — they are fused, not separable",
            })

    for name in filter(None, (args.require or "").split(",")):
        if norm(name) not in by_norm:
            findings.append({
                "check": "required-part-missing",
                "severity": "error",
                "subject": name.strip(),
                "detail": f"required part {name.strip()!r} is absent from the built model",
            })

    # ---- B. detailInventory -> spec ------------------------------------------------
    keys = collect_local_feature_keys(spec)
    for index, entry in enumerate(find_inventory(spec, args.inventory)):
        maps_to = entry.get("mapsTo")
        # The scaffolder writes mapsTo as {"ref": ..., "via": ...}; hand-authored specs
        # sometimes use a bare string. Accept both rather than failing the honest one.
        if isinstance(maps_to, dict):
            maps_to = maps_to.get("ref") or maps_to.get("id") or ""
        label = entry.get("id") or entry.get("description") or f"detail[{index}]"
        if not maps_to or not str(maps_to).strip():
            findings.append({
                "check": "detail-unmapped",
                "severity": "warning",
                "subject": str(label)[:60],
                "detail": "inventoried detail has no mapsTo — it was observed but never assigned "
                          "to a component or material field",
            })
        elif norm(maps_to) not in keys:
            findings.append({
                "check": "detail-dangling",
                "severity": "warning",
                "subject": str(label)[:60],
                "detail": f"mapsTo {maps_to!r} matches no component, localFeature or localOverride",
            })

    # ---- C. model hygiene ----------------------------------------------------------
    unnamed = int(manifest.get("unnamedMeshes") or 0)
    if unnamed:
        findings.append({
            "check": "unnamed-meshes",
            "severity": "warning",
            "subject": f"{unnamed} mesh(es)",
            "detail": "meshes belonging to no named part: they cannot be selected, and each one "
                      "explodes on its own instead of riding the part it decorates",
        })

    extras = sorted(set(by_norm) - set(claimed))
    for key in extras:
        findings.append({
            "check": "part-not-specified",
            "severity": "info",
            "subject": by_norm[key][0],
            "detail": "built part has no matching component in the spec (fine for detail groups; "
                      "a sign of spec drift for anything larger)",
        })

    # ---- report --------------------------------------------------------------------
    rank = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: rank[f["severity"]])
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")

    label = {"error": "FAIL", "warning": "WARN", "info": "note"}
    for finding in findings:
        print(f"{label[finding['severity']]:<5} {finding['check']}: {finding['subject']}")
        print(f"      {finding['detail']}")

    print(
        f"\ncheck_part_coverage: {len(components)} specified, {len(parts)} built, "
        f"{errors} error(s), {warnings} warning(s)"
    )

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(
                {
                    "model": manifest.get("model"),
                    "specifiedComponents": len(components),
                    "builtParts": len(parts),
                    "errors": errors,
                    "warnings": warnings,
                    "result": "fail" if errors else "pass",
                    "findings": findings,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    return 1 if errors and not args.warn_only else 0


if __name__ == "__main__":
    sys.exit(main())
