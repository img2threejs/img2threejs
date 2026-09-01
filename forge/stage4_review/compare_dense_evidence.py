#!/usr/bin/env python3
"""Dual-baseline gate for dense-evidence-assisted procedural proposals."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


SCOPE_RANK = {"none": 0, "global-massing": 1, "component-measurements": 2}
REQUIRED_GATES = ("strictQuality", "passOrder", "turntable", "attachment", "intersection")
FORBIDDEN_RUNTIME_PATTERNS = (
    ("runtime_mesh_loader", re.compile(r"\bGLTFLoader\b|\bOBJLoader\b")),
    ("runtime_mesh_asset", re.compile(r"\.(?:glb|obj)(?:['\"?#]|\b)", re.IGNORECASE)),
    ("provider_endpoint", re.compile(r"trellis-community|stable-fast-3d|huggingface\.co|hf\.space", re.IGNORECASE)),
    ("signed_url", re.compile(r"[?&](?:X-Amz-Signature|Signature|Expires|token)=", re.IGNORECASE)),
    ("copied_mesh_payload", re.compile(r"copiedMeshPayload|embeddedMeshPayload", re.IGNORECASE)),
)


def _finite_unit(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and 0.0 <= number <= 1.0 else None


def _hashes_complete(metrics: dict[str, object]) -> bool:
    hashes = metrics.get("browserHashes")
    return (
        isinstance(hashes, dict)
        and bool(hashes)
        and all(
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
            for value in hashes.values()
        )
    )


def _deny(category: str, reasons: list[str], **metrics: object) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "decision": "DENY",
        "failureCategory": category,
        "reasons": reasons,
        "metrics": metrics,
    }


def compare_dense_evidence(
    baseline_source: dict[str, object],
    candidate_source: dict[str, object],
    baseline_evidence: dict[str, object],
    candidate_evidence: dict[str, object],
    deterministic_gates: dict[str, bool],
    admission: dict[str, object],
    *,
    declared_evidence_targets: tuple[str, ...] | None = None,
) -> dict[str, object]:
    if not all(
        _hashes_complete(metrics)
        for metrics in (
            baseline_source,
            candidate_source,
            baseline_evidence,
            candidate_evidence,
        )
    ):
        return _deny(
            "browser_evidence_missing", ["all four browser metric records need complete hashes"]
        )
    if admission.get("decision") != "ALLOW":
        return _deny("influence_not_approved", ["hash-bound ALLOW admission is required"])
    scope = admission.get("approvedInfluenceScope")
    maximum = admission.get("maximumInfluenceScope")
    binding = admission.get("binding")
    if (
        scope not in SCOPE_RANK
        or maximum not in SCOPE_RANK
        or SCOPE_RANK[str(scope)] > SCOPE_RANK[str(maximum)]
        or not isinstance(binding, dict)
        or binding.get("scope") != scope
    ):
        return _deny("influence_scope_exceeded", ["approval scope exceeds its evidence ceiling"])
    missing_gates = [name for name in REQUIRED_GATES if deterministic_gates.get(name) is not True]
    if missing_gates:
        return _deny(
            "deterministic_gate_failed",
            [f"required gate did not pass: {name}" for name in missing_gates],
        )
    baseline_iou = _finite_unit(baseline_source.get("silhouetteIou"))
    candidate_iou = _finite_unit(candidate_source.get("silhouetteIou"))
    if baseline_iou is None or candidate_iou is None:
        return _deny("browser_evidence_missing", ["source silhouette IoU is missing or invalid"])
    baseline_features = baseline_source.get("criticalFeatures")
    candidate_features = candidate_source.get("criticalFeatures")
    if not isinstance(baseline_features, dict) or not isinstance(candidate_features, dict):
        return _deny("browser_evidence_missing", ["critical feature results are missing"])
    critical_regressions = sorted(
        name
        for name, passed in baseline_features.items()
        if passed is True and candidate_features.get(name) is not True
    )
    source_regression = baseline_iou - candidate_iou
    if critical_regressions or source_regression > 0.02 + 1e-12:
        return _deny(
            "source_fidelity_regression",
            [
                *(f"critical feature regressed: {name}" for name in critical_regressions),
                *(
                    [f"source silhouette regressed by {source_regression:.6f}, above 0.02"]
                    if source_regression > 0.02 + 1e-12
                    else []
                ),
            ],
            sourceRegression=source_regression,
            criticalRegressions=critical_regressions,
        )
    targets = declared_evidence_targets or tuple(
        sorted(
            key
            for key in set(baseline_evidence) & set(candidate_evidence)
            if key != "browserHashes"
            and _finite_unit(baseline_evidence.get(key)) is not None
            and _finite_unit(candidate_evidence.get(key)) is not None
        )
    )
    if not targets:
        return _deny("browser_evidence_missing", ["no declared evidence target has valid metrics"])
    improvements = {
        name: float(candidate_evidence[name]) - float(baseline_evidence[name])
        for name in targets
        if _finite_unit(baseline_evidence.get(name)) is not None
        and _finite_unit(candidate_evidence.get(name)) is not None
    }
    if len(improvements) != len(targets):
        return _deny("browser_evidence_missing", ["a declared evidence target is missing"])
    best_improvement = max(improvements.values())
    if best_improvement < 0.01 - 1e-12:
        return _deny(
            "fit_no_improvement",
            [f"best evidence improvement {best_improvement:.6f} is below 0.01"],
            evidenceImprovements=improvements,
        )
    return {
        "schemaVersion": 1,
        "decision": "ALLOW",
        "failureCategory": None,
        "sourceRegression": source_regression,
        "criticalRegressions": [],
        "evidenceImprovements": improvements,
        "approvedInfluenceScope": scope,
        "runtimeAuthority": "procedural-typescript-factory",
    }


def scan_runtime_sources(paths: list[Path]) -> dict[str, object]:
    violations: list[dict[str, str]] = []
    for path in paths:
        if path.suffix not in {".ts", ".tsx", ".js", ".mjs"}:
            violations.append(
                {"path": str(path), "category": "runtime_source_invalid", "match": path.suffix}
            )
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            violations.append(
                {"path": str(path), "category": "runtime_source_unreadable", "match": str(error)}
            )
            continue
        for category, pattern in FORBIDDEN_RUNTIME_PATTERNS:
            match = pattern.search(source)
            if match:
                violations.append(
                    {"path": str(path), "category": category, "match": match.group(0)}
                )
                break
    return {
        "passed": not violations,
        "runtimeAuthority": "procedural-typescript-factory",
        "scanned": [str(path) for path in paths],
        "violations": violations,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", type=Path, required=True)
    parser.add_argument("--candidate-source", type=Path, required=True)
    parser.add_argument("--baseline-evidence", type=Path, required=True)
    parser.add_argument("--candidate-evidence", type=Path, required=True)
    parser.add_argument("--deterministic-gates", type=Path, required=True)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--runtime-source", type=Path, action="append", required=True)
    parser.add_argument("--evidence-target", action="append")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        values = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (
                args.baseline_source,
                args.candidate_source,
                args.baseline_evidence,
                args.candidate_evidence,
                args.deterministic_gates,
                args.admission,
            )
        ]
        report = compare_dense_evidence(
            *values,
            declared_evidence_targets=tuple(args.evidence_target) if args.evidence_target else None,
        )
        runtime = scan_runtime_sources(args.runtime_source)
        if not runtime["passed"]:
            report = _deny("runtime_mesh_dependency", ["candidate runtime is not code-only"], runtime=runtime)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["decision"] == "ALLOW" else 1
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
