#!/usr/bin/env python3
"""Seed a deny-by-default component map without inferring semantic labels."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _component_ids(spec: dict[str, object]) -> list[str]:
    result: list[str] = []

    def visit(items: object) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("id"), str):
                result.append(item["id"])
            visit(item.get("children"))

    visit(spec.get("componentTree"))
    return sorted(result)


def seed_component_map(
    spec: dict[str, object], evidence: dict[str, object]
) -> dict[str, Any]:
    admission = evidence.get("admission")
    if not isinstance(admission, dict) or admission.get("maximumInfluenceScope") != "component-measurements":
        raise ValueError("semantic_boundary_insufficient: component map requires multipart evidence")
    regions = evidence.get("regions")
    if not isinstance(regions, list) or not regions:
        raise ValueError("semantic_boundary_insufficient: no candidate regions")
    provenance = evidence.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("evidence provenance is missing")
    return {
        "schemaVersion": 1,
        "kind": "component-evidence-map",
        "targetSpecSha256": _canonical_sha256(spec),
        "evidenceSha256": _canonical_sha256(evidence),
        "glbSha256": provenance.get("glbSha256"),
        "availableComponentIds": _component_ids(spec),
        "candidateRegions": regions,
        "mappings": [],
        "extensions": {},
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        result = seed_component_map(spec, evidence)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1 if "semantic_boundary_insufficient" in str(error) else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
