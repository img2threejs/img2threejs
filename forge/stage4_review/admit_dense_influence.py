#!/usr/bin/env python3
"""Create a human-approved, hash-bound dense-evidence influence record."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.stage1_intake.check_dense_evidence import SCOPE_RANK, validate_dense_evidence


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def create_admission(
    evidence_path: Path,
    visual_review_path: Path,
    target_spec_path: Path,
    requested_scope: str,
    *,
    approved: bool,
    component_map_path: Path | None = None,
) -> dict[str, Any]:
    evidence = _load(evidence_path)
    review = _load(visual_review_path)
    spec = _load(target_spec_path)
    component_map = _load(component_map_path) if component_map_path else None
    validation = validate_dense_evidence(evidence, spec, component_map)
    maximum = str(validation["maximumInfluenceScope"])
    provenance = evidence.get("provenance", {})
    glb_hash = provenance.get("glbSha256") if isinstance(provenance, dict) else None
    if requested_scope not in SCOPE_RANK or requested_scope == "none":
        return {
            "schemaVersion": 1,
            "decision": "DENY",
            "failureCategory": "influence_scope_exceeded",
            "reasons": ["requested scope is invalid for an approval"],
        }
    if not validation["passed"]:
        category = (
            "semantic_boundary_insufficient"
            if requested_scope == "component-measurements"
            and maximum != "component-measurements"
            else str(validation["failureCategories"][0])
        )
        return {
            "schemaVersion": 1,
            "decision": "DENY",
            "failureCategory": category,
            "reasons": validation["errors"],
        }
    if SCOPE_RANK[requested_scope] > SCOPE_RANK[maximum]:
        return {
            "schemaVersion": 1,
            "decision": "DENY",
            "failureCategory": "semantic_boundary_insufficient",
            "reasons": [f"requested {requested_scope} exceeds {maximum}"],
        }
    if review.get("glbSha256") != glb_hash:
        return {
            "schemaVersion": 1,
            "decision": "DENY",
            "failureCategory": "admission_hash_mismatch",
            "reasons": ["visual review is bound to another GLB"],
        }
    if not approved:
        return {
            "schemaVersion": 1,
            "decision": "NEEDS_USER_ACTION",
            "failureCategory": "influence_approval_required",
            "requestedScope": requested_scope,
            "maximumInfluenceScope": maximum,
            "reasons": ["explicit --approve-influence is required"],
        }
    binding: dict[str, str] = {
        "glbSha256": str(glb_hash),
        "evidenceSha256": _sha256_file(evidence_path),
        "visualReviewSha256": _sha256_file(visual_review_path),
        "scope": requested_scope,
        "targetSpecSha256": _sha256_file(target_spec_path),
    }
    if component_map_path is not None:
        binding["componentMapSha256"] = _sha256_file(component_map_path)
    return {
        "schemaVersion": 1,
        "kind": "dense-influence-admission",
        "decision": "ALLOW",
        "approvedAt": datetime.now(UTC).isoformat(),
        "binding": binding,
        "maximumInfluenceScope": maximum,
        "approvedInfluenceScope": requested_scope,
        "extensions": {},
    }


def validate_admission(
    admission: dict[str, object],
    evidence_path: Path,
    visual_review_path: Path,
    target_spec_path: Path,
    component_map_path: Path | None = None,
) -> dict[str, object]:
    errors: list[str] = []
    binding = admission.get("binding")
    evidence = _load(evidence_path)
    provenance = evidence.get("provenance", {})
    expected = {
        "glbSha256": provenance.get("glbSha256") if isinstance(provenance, dict) else None,
        "evidenceSha256": _sha256_file(evidence_path),
        "visualReviewSha256": _sha256_file(visual_review_path),
        "targetSpecSha256": _sha256_file(target_spec_path),
    }
    if component_map_path is not None:
        expected["componentMapSha256"] = _sha256_file(component_map_path)
    if admission.get("decision") != "ALLOW" or not isinstance(binding, dict):
        errors.append("admission is not an ALLOW record")
    else:
        for field, value in expected.items():
            if binding.get(field) != value:
                errors.append(f"{field} mismatch")
        if binding.get("scope") != admission.get("approvedInfluenceScope"):
            errors.append("scope mismatch")
    return {
        "passed": not errors,
        "failureCategories": [] if not errors else ["admission_hash_mismatch"],
        "errors": errors,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--visual-review", type=Path, required=True)
    parser.add_argument("--target-spec", type=Path, required=True)
    parser.add_argument("--scope", choices=("global-massing", "component-measurements"), required=True)
    parser.add_argument("--component-map", type=Path)
    parser.add_argument("--approve-influence", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = create_admission(
            args.evidence,
            args.visual_review,
            args.target_spec,
            args.scope,
            approved=args.approve_influence,
            component_map_path=args.component_map,
        )
        if record["decision"] == "ALLOW":
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(record, ensure_ascii=False))
        return 0 if record["decision"] == "ALLOW" else (3 if record["decision"] == "NEEDS_USER_ACTION" else 1)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
