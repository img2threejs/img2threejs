from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import trimesh

from integrations.mesh3d.dense_evidence.cache import (
    ExtractionCacheInput,
    extraction_cache_key,
)
from integrations.mesh3d.dense_evidence.model import (
    canonical_sha256,
    DenseEvidenceError,
    InfluenceScope,
    sha256_file,
    validate_provider_run,
    write_json_atomic,
)
from integrations.mesh3d.dense_evidence.alignment import validate_alignment
from integrations.mesh3d.dense_evidence.extract import ExtractionConfig, extract_run


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_completed_run(
    root: Path,
    source: Path,
    *,
    reviewed: bool = True,
    multipart: bool = False,
    degenerate: bool = False,
) -> Path:
    run = root / "run"
    normalized = run / "normalized"
    review = run / "review"
    normalized.mkdir(parents=True)
    review.mkdir(parents=True)
    glb = normalized / "reference.glb"
    obj = normalized / "reference.obj"
    extents = (1.0, 0.0, 1.0) if degenerate else (2.0, 3.0, 4.0)
    primary = trimesh.creation.box(extents=extents)
    scene = trimesh.Scene()
    scene.add_geometry(primary, node_name="body", geom_name="body-mesh")
    if multipart:
        roof = trimesh.creation.box(extents=(1.5, 0.5, 2.0))
        roof.apply_translation((0.0, 1.75, 0.0))
        scene.add_geometry(roof, node_name="roof", geom_name="roof-mesh")
    glb.write_bytes(scene.export(file_type="glb"))
    obj.write_text(scene.export(file_type="obj"), encoding="utf-8")
    _write_json(
        run / "provider-receipt.json",
        {
            "providerId": "trellis-zerogpu",
            "sourceImageSha256": [sha256_file(source)],
            "normalizedGlbSha256": sha256_file(glb),
            "normalizedObjSha256": sha256_file(obj),
        },
    )
    _write_json(
        review / "admission.json",
        {
            "status": "pass",
            "glbSha256": sha256_file(glb),
            "objSha256": sha256_file(obj),
            "probe": {
                "scene": {
                    "meshCount": 2 if multipart else 1,
                    "primitiveCount": 2 if multipart else 1,
                },
                "semanticStatus": "sufficient" if multipart else "insufficient",
            },
        },
    )
    if reviewed:
        _write_json(
            review / "visual-review.json",
            {
                "status": "reviewed",
                "verdict": "retain-as-generative-proxy-only",
                "glbSha256": sha256_file(glb),
            },
        )
    return run


def valid_alignment(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schemaVersion": 1,
        "profileVersion": "source-view-alignment-v1",
        "sourceViewTransform": np.eye(4).reshape(-1).tolist(),
        "upAxis": "+Y",
        "forwardAxis": "+Z",
        "handedness": "right-handed",
        "axisOperationAudit": ["identity; no reflection applied"],
        "chiralityStatus": "reviewed",
        "sourceViewSilhouetteIou": 0.80,
        "projectedAspectRatioError": 0.05,
        "browserCaptures": [
            {"path": "review/preview.png", "sha256": "f" * 64, "view": "source"}
        ],
    }
    value.update(overrides)
    return value


def _valid_spec() -> dict[str, object]:
    return {
        "schemaVersion": "2.1",
        "targetName": "Fixture House",
        "qualityContract": {"denseEvidence": {"maxNumericDeltaFraction": 0.2}},
        "componentTree": [
            {
                "id": "body",
                "primitive": "box",
                "topologyClass": "assembled-solid",
                "topologyRationale": "Observed wall volume.",
                "dimensions": {"width": 1.5, "height": 2.0, "depth": 3.0},
                "transform": {"position": [0.0, 0.0, 0.0]},
                "children": [],
            }
        ],
    }


def _hash_tree(*roots: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            result[str(path.relative_to(root.parent))] = sha256_file(path)
    return result


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "integrations.mesh3d.dense_evidence", *arguments],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        capture_output=True,
        check=False,
    )


class DenseEvidenceModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.png"
        self.source.write_bytes(b"image")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_scope_enum_contains_only_three_deny_by_default_values(self) -> None:
        self.assertEqual(
            [item.value for item in InfluenceScope],
            ["none", "global-massing", "component-measurements"],
        )

    def test_run_validation_rejects_hash_drift_and_unreviewed_mesh(self) -> None:
        run = make_completed_run(self.root, self.source)
        validated = validate_provider_run(run, (self.source,))
        self.assertEqual(validated.glb_sha256, sha256_file(run / "normalized/reference.glb"))
        (run / "normalized/reference.glb").write_bytes(b"changed")
        with self.assertRaisesRegex(DenseEvidenceError, "evidence_hash_mismatch"):
            validate_provider_run(run, (self.source,))

        unreviewed_root = self.root / "unreviewed"
        unreviewed_root.mkdir()
        unreviewed = make_completed_run(unreviewed_root, self.source, reviewed=False)
        with self.assertRaisesRegex(DenseEvidenceError, "visual_review_missing"):
            validate_provider_run(unreviewed, (self.source,))

    def test_atomic_json_write_leaves_complete_record(self) -> None:
        target = self.root / "dense-evidence" / "status.json"
        write_json_atomic(target, {"status": "complete"})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"status": "complete"})
        self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_cache_key_changes_for_every_authoritative_input(self) -> None:
        base = ExtractionCacheInput(
            "a" * 64,
            "b" * 64,
            ("c" * 64,),
            "v1",
            "a1",
            None,
            "m1",
        )
        keys = {
            extraction_cache_key(base),
            extraction_cache_key(dataclasses.replace(base, glb_sha256="d" * 64)),
            extraction_cache_key(
                dataclasses.replace(base, source_image_sha256=("e" * 64,))
            ),
            extraction_cache_key(
                dataclasses.replace(base, measurement_config_sha256="m2")
            ),
        }
        self.assertEqual(len(keys), 4)


class DenseEvidenceExtractionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.png"
        self.source.write_bytes(b"image")
        self.out = self.root / "dense-evidence"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_merged_glb_is_capped_at_global_massing(self) -> None:
        run = make_completed_run(self.root, self.source)
        result = extract_run(run, (self.source,), valid_alignment(), self.out)
        self.assertEqual(result["admission"]["maximumInfluenceScope"], "global-massing")
        self.assertEqual(result["admission"]["approvedInfluenceScope"], "none")
        self.assertEqual(result["regions"], [])
        self.assertLessEqual(result["globalGeometry"]["occupancyGrid"]["resolution"], 32)

    def test_multipart_boundaries_remain_candidate_only(self) -> None:
        run = make_completed_run(self.root, self.source, multipart=True)
        result = extract_run(run, (self.source,), valid_alignment(), self.out)
        self.assertEqual(result["admission"]["maximumInfluenceScope"], "component-measurements")
        self.assertGreaterEqual(len(result["regions"]), 2)
        self.assertTrue(all(item["candidateOnly"] for item in result["regions"]))
        self.assertTrue(all(item["semanticLabel"] is None for item in result["regions"]))

    def test_threshold_failure_denies_instead_of_relaxing(self) -> None:
        with self.assertRaisesRegex(DenseEvidenceError, "alignment_failed"):
            validate_alignment(
                valid_alignment(sourceViewSilhouetteIou=0.64), "global-massing"
            )

    def test_chirality_ambiguity_caps_component_scope_at_global(self) -> None:
        alignment = validate_alignment(
            valid_alignment(chiralityStatus="ambiguous"), "sufficient"
        )
        self.assertEqual(alignment.maximum_scope, InfluenceScope.GLOBAL_MASSING)

    def test_single_view_hidden_surfaces_are_non_authoritative(self) -> None:
        result = extract_run(
            make_completed_run(self.root, self.source),
            (self.source,),
            valid_alignment(),
            self.out,
        )
        self.assertEqual(result["uncertainty"]["hiddenSurfacePolicy"], "non-authoritative")
        self.assertEqual(result["uncertainty"]["rearConfidence"], "low")

    def test_caps_and_degenerate_geometry_fail_closed(self) -> None:
        with self.assertRaisesRegex(DenseEvidenceError, "measurement_limit_exceeded"):
            extract_run(
                make_completed_run(self.root / "oversize", self.source),
                (self.source,),
                valid_alignment(),
                self.out,
                ExtractionConfig(resolution=33),
            )
        with self.assertRaisesRegex(DenseEvidenceError, "degenerate_geometry"):
            extract_run(
                make_completed_run(self.root / "flat", self.source, degenerate=True),
                (self.source,),
                valid_alignment(),
                self.root / "flat-out",
            )


class DenseEvidenceCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.png"
        self.source.write_bytes(b"image")
        self.run = make_completed_run(self.root, self.source)
        self.alignment = self.root / "reviewed-alignment.json"
        _write_json(self.alignment, valid_alignment())
        self.out = self.root / "dense-evidence"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_help_exposes_no_provider_upload_token_or_retry_flags(self) -> None:
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for forbidden in ("provider", "upload", "token", "retry", "endpoint", "max-cost"):
            self.assertNotIn(f"--{forbidden}", result.stdout)

    def test_extract_and_verify_cache_are_offline_and_hash_bound(self) -> None:
        first = run_cli(
            "extract",
            "--run",
            str(self.run),
            "--source-image",
            str(self.source),
            "--alignment",
            str(self.alignment),
            "--out-dir",
            str(self.out),
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        for name in (
            "extraction-request.json",
            "alignment.json",
            "dense-evidence.v1.json",
            "status.json",
        ):
            self.assertTrue((self.out / name).is_file(), name)
        second = run_cli(
            "verify-cache",
            "--run",
            str(self.run),
            "--source-image",
            str(self.source),
            "--alignment",
            str(self.alignment),
            "--out-dir",
            str(self.out),
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertTrue(json.loads(second.stdout)["cacheHit"])
        self.source.write_bytes(b"drift")
        stale = run_cli(
            "verify-cache",
            "--run",
            str(self.run),
            "--source-image",
            str(self.source),
            "--alignment",
            str(self.alignment),
            "--out-dir",
            str(self.out),
        )
        self.assertEqual(stale.returncode, 1)

    def test_import_and_extraction_construct_no_network_or_provider(self) -> None:
        import socket
        import urllib.request

        from integrations.mesh3d.dense_evidence import cli

        argv = [
            "extract",
            "--run",
            str(self.run),
            "--source-image",
            str(self.source),
            "--alignment",
            str(self.alignment),
            "--out-dir",
            str(self.out),
        ]
        with (
            mock.patch.object(socket, "socket", side_effect=AssertionError("network")),
            mock.patch.object(
                urllib.request, "urlopen", side_effect=AssertionError("network")
            ),
        ):
            self.assertEqual(cli.main(argv), 0)
        self.assertNotIn("integrations.mesh3d.free_assist.providers", sys.modules)


class DenseEvidenceEndToEndTest(unittest.TestCase):
    def test_cached_run_to_proposed_spec_is_offline_and_preserves_inputs(self) -> None:
        from forge.stage2_spec.apply_dense_evidence import build_proposal

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"image")
            run = make_completed_run(root, source)
            before = _hash_tree(run / "raw", run / "normalized", run / "review")
            evidence = extract_run(
                run, (source,), valid_alignment(), root / "dense-evidence"
            )
            spec = _valid_spec()
            admission = {
                "decision": "ALLOW",
                "approvedInfluenceScope": "global-massing",
                "binding": {
                    "glbSha256": evidence["provenance"]["glbSha256"],
                    "evidenceSha256": canonical_sha256(evidence),
                    "visualReviewSha256": "9" * 64,
                    "scope": "global-massing",
                    "targetSpecSha256": canonical_sha256(spec),
                },
            }
            with mock.patch("socket.socket", side_effect=AssertionError("network")):
                proposal, delta, fit_plan = build_proposal(spec, evidence, admission)
            self.assertEqual(
                _hash_tree(run / "raw", run / "normalized", run / "review"), before
            )
            self.assertNotEqual(proposal, spec)
            self.assertTrue(delta["changes"])
            self.assertEqual(fit_plan["maximumSourceSilhouetteRegression"], 0.02)
            self.assertNotIn("integrations.mesh3d.free_assist.providers", sys.modules)

if __name__ == "__main__":
    unittest.main()
