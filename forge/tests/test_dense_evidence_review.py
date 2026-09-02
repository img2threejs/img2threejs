from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from forge.stage4_review.compare_dense_evidence import (
    compare_dense_evidence,
    scan_runtime_sources,
)


def clean_gates() -> dict[str, bool]:
    return {
        "strictQuality": True,
        "passOrder": True,
        "turntable": True,
        "attachment": True,
        "intersection": True,
    }


def source_metrics(iou: float, *, roof: bool = True) -> dict[str, object]:
    return {
        "silhouetteIou": iou,
        "criticalFeatures": {"roof": roof},
        "browserHashes": {"source": "a" * 64, "render": "b" * 64},
    }


def evidence_metrics(score: float) -> dict[str, object]:
    return {
        "massingSimilarity": score,
        "browserHashes": {"provider": "c" * 64, "procedural": "d" * 64},
    }


def valid_admission(*, scope: str = "global-massing", maximum: str = "global-massing") -> dict[str, object]:
    return {
        "decision": "ALLOW",
        "approvedInfluenceScope": scope,
        "maximumInfluenceScope": maximum,
        "binding": {"scope": scope},
    }


class DenseEvidenceReviewTest(unittest.TestCase):
    def test_candidate_needs_source_safety_and_evidence_improvement(self) -> None:
        report = compare_dense_evidence(
            baseline_source=source_metrics(0.81),
            candidate_source=source_metrics(0.80),
            baseline_evidence=evidence_metrics(0.70),
            candidate_evidence=evidence_metrics(0.72),
            deterministic_gates=clean_gates(),
            admission=valid_admission(),
        )
        self.assertEqual(report["decision"], "ALLOW")

    def test_glb_improvement_cannot_hide_source_regression(self) -> None:
        report = compare_dense_evidence(
            baseline_source=source_metrics(0.81),
            candidate_source=source_metrics(0.78),
            baseline_evidence=evidence_metrics(0.50),
            candidate_evidence=evidence_metrics(0.95),
            deterministic_gates=clean_gates(),
            admission=valid_admission(),
        )
        self.assertEqual(report["decision"], "DENY")
        self.assertEqual(report["failureCategory"], "source_fidelity_regression")

    def test_critical_feature_gate_and_minimum_improvement_are_blocking(self) -> None:
        critical = compare_dense_evidence(
            source_metrics(0.81), source_metrics(0.81, roof=False),
            evidence_metrics(0.70), evidence_metrics(0.90), clean_gates(), valid_admission()
        )
        self.assertEqual(critical["failureCategory"], "source_fidelity_regression")
        flat = compare_dense_evidence(
            source_metrics(0.81), source_metrics(0.81),
            evidence_metrics(0.70), evidence_metrics(0.705), clean_gates(), valid_admission()
        )
        self.assertEqual(flat["failureCategory"], "fit_no_improvement")

    def test_missing_browser_hash_dirty_gate_and_scope_are_denied(self) -> None:
        missing = source_metrics(0.81)
        missing["browserHashes"] = {}
        report = compare_dense_evidence(
            missing, source_metrics(0.81), evidence_metrics(0.70), evidence_metrics(0.72),
            clean_gates(), valid_admission()
        )
        self.assertEqual(report["failureCategory"], "browser_evidence_missing")
        gates = clean_gates()
        gates["intersection"] = False
        dirty = compare_dense_evidence(
            source_metrics(0.81), source_metrics(0.81), evidence_metrics(0.70),
            evidence_metrics(0.72), gates, valid_admission()
        )
        self.assertEqual(dirty["failureCategory"], "deterministic_gate_failed")
        scope = compare_dense_evidence(
            source_metrics(0.81), source_metrics(0.81), evidence_metrics(0.70),
            evidence_metrics(0.72), clean_gates(),
            valid_admission(scope="component-measurements", maximum="global-massing")
        )
        self.assertEqual(scope["failureCategory"], "influence_scope_exceeded")


class DenseEvidenceRuntimeGuardTest(unittest.TestCase):
    def test_code_only_typescript_factory_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory = Path(directory) / "createHouseModel.ts"
            factory.write_text(
                "import * as THREE from 'three';\nexport function createHouseModel(){return new THREE.Group();}\n",
                encoding="utf-8",
            )
            report = scan_runtime_sources([factory])
            self.assertTrue(report["passed"], report["violations"])

    def test_runtime_glb_loader_provider_url_and_copied_payload_are_rejected(self) -> None:
        samples = {
            "loader.ts": "new GLTFLoader().load('house.glb')",
            "provider.ts": "fetch('https://trellis-community.example/api')",
            "payload.ts": "const copiedMeshPayload = [0, 1, 2]",
        }
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name, source in samples.items():
                path = Path(directory) / name
                path.write_text(source, encoding="utf-8")
                paths.append(path)
            report = scan_runtime_sources(paths)
            self.assertFalse(report["passed"])
            self.assertEqual(len(report["violations"]), 3)


if __name__ == "__main__":
    unittest.main()
