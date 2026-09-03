from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from forge.stage1_intake.check_dense_evidence import validate_dense_evidence
from forge.stage2_spec.new_component_evidence_map import seed_component_map
from forge.stage2_spec.apply_dense_evidence import (
    COMPONENT_NUMERIC_FIELDS,
    GLOBAL_NUMERIC_FIELDS,
    apply_reverse_delta,
    build_proposal,
)
from forge.stage4_review.admit_dense_influence import create_admission, validate_admission


ROOT = Path(__file__).resolve().parents[2]


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def valid_spec() -> dict[str, object]:
    return {
        "schemaVersion": "2.1",
        "targetName": "House",
        "qualityContract": {"denseEvidence": {"maxNumericDeltaFraction": 0.2}},
        "componentTree": [
            {
                "id": "body",
                "primitive": "box",
                "topologyClass": "assembled-solid",
                "topologyRationale": "Observed wall volume with hard assembled seams.",
                "dimensions": {"width": 2.0, "height": 2.5, "depth": 3.0},
                "transform": {"position": [0.0, 0.0, 0.0]},
                "children": [
                    {
                        "id": "roof-main",
                        "primitive": "wedge",
                        "topologyClass": "assembled-solid",
                        "topologyRationale": "Observed roof planes meet at a ridge.",
                        "dimensions": {"width": 2.2, "height": 1.0, "depth": 3.2},
                        "transform": {"position": [0.0, 1.75, 0.0]},
                        "children": [],
                    }
                ],
            }
        ],
    }


def valid_evidence(*, maximum_scope: str = "global-massing", multipart: bool = False) -> dict[str, object]:
    regions = []
    semantic_status = "insufficient"
    if multipart:
        semantic_status = "sufficient"
        maximum_scope = "component-measurements"
        regions = [
            {
                "regionId": "node:body/geometry:body-mesh",
                "node": "body",
                "geometry": "body-mesh",
                "candidateOnly": True,
                "semanticLabel": None,
                "bounds": {"min": [-1.0, -1.0, -1.5], "max": [1.0, 1.0, 1.5], "size": [2.0, 2.0, 3.0]},
            },
            {
                "regionId": "node:roof/geometry:roof-mesh",
                "node": "roof",
                "geometry": "roof-mesh",
                "candidateOnly": True,
                "semanticLabel": None,
                "bounds": {"min": [-1.1, 1.0, -1.6], "max": [1.1, 2.0, 1.6], "size": [2.2, 1.0, 3.2]},
            },
        ]
    evidence = {
        "schemaVersion": 1,
        "kind": "dense-evidence",
        "extractorVersion": "dense-evidence-extractor-v1",
        "createdAt": "2026-09-01T00:00:00+00:00",
        "provenance": {
            "providerId": "hf-zerogpu-trellis",
            "runPath": "/tmp/run",
            "glbPath": "/tmp/run/normalized/reference.glb",
            "glbSha256": "a" * 64,
            "objSha256": "b" * 64,
            "sourceImageSha256": ["c" * 64],
            "visualReviewStatus": "retain-as-generative-proxy-only",
            "alignmentProfileVersion": "source-view-alignment-v1",
            "alignmentSha256": "d" * 64,
        },
        "cache": {"baseExtractionKey": "e" * 64, "measurementConfigSha256": "f" * 64},
        "admission": {
            "structuralStatus": "structural-pass-visual-review-required",
            "semanticStatus": semantic_status,
            "maximumInfluenceScope": maximum_scope,
            "approvedInfluenceScope": "none",
        },
        "alignment": {
            "schemaVersion": 1,
            "profileVersion": "source-view-alignment-v1",
            "sourceViewTransform": [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0],
            "upAxis": "+Y",
            "forwardAxis": "+Z",
            "handedness": "right-handed",
            "axisOperationAudit": ["identity"],
            "chiralityStatus": "reviewed",
            "sourceViewSilhouetteIou": 0.8,
            "projectedAspectRatioError": 0.05,
            "browserCaptures": [{"path": "review/preview.png", "sha256": "1" * 64, "view": "source"}],
        },
        "globalGeometry": {
            "bounds": {"min": [-1.0, -1.0, -1.5], "max": [1.0, 2.0, 1.5], "size": [2.0, 3.0, 3.0]},
            "principalAxes": [
                {"axis": [1.0, 0.0, 0.0], "variance": 3.0},
                {"axis": [0.0, 1.0, 0.0], "variance": 2.0},
                {"axis": [0.0, 0.0, 1.0], "variance": 1.0},
            ],
            "occupancyGrid": {"resolution": 24, "occupiedCells": [[0, 0, 0]], "occupiedCellCount": 1},
            "crossSections": [{"axis": "y", "position": 0.0, "profile": [[-1.0, -1.5], [1.0, 1.5], [0.0, 1.0]]}],
            "silhouetteViews": [{"view": "source", "path": "review/preview.png", "sha256": "1" * 64, "silhouetteIou": 0.8, "projectedAspectRatioError": 0.05}],
        },
        "regions": regions,
        "uncertainty": {
            "sourceViewCount": 1,
            "hiddenSurfacePolicy": "non-authoritative",
            "rearConfidence": "low",
            "singleViewLimitations": ["rear is not observed"],
        },
        "extensions": {},
    }
    evidence["provenance"]["alignmentSha256"] = canonical_sha256(
        evidence["alignment"]
    )
    return evidence


def valid_component_map(evidence: dict[str, object], spec: dict[str, object], *, observed: bool = True) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "kind": "component-evidence-map",
        "targetSpecSha256": canonical_sha256(spec),
        "evidenceSha256": canonical_sha256(evidence),
        "glbSha256": evidence["provenance"]["glbSha256"],
        "mappings": [
            {
                "componentId": "roof-main",
                "selectors": [{"regionId": "node:roof/geometry:roof-mesh"}],
                "mappingMethod": "human-reviewed-node-boundary",
                "evidenceRefs": ["review/semantic-roof.png"],
                "confidence": 0.86,
                "permittedFields": ["dimensions.width", "dimensions.height"],
                "observedSurface": observed,
                "hiddenLimitations": ["rear slope is non-authoritative"],
            }
        ],
        "extensions": {},
    }


class DenseEvidenceContractTest(unittest.TestCase):
    def test_valid_global_record_passes_without_optional_dependencies(self) -> None:
        report = validate_dense_evidence(valid_evidence())
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["maximumInfluenceScope"], "global-massing")

    def test_merged_mesh_component_claim_fails_closed(self) -> None:
        evidence = valid_evidence()
        mapping = valid_component_map(evidence, valid_spec())
        report = validate_dense_evidence(evidence, valid_spec(), mapping)
        self.assertIn("semantic_boundary_insufficient", report["failureCategories"])

    def test_single_view_hidden_measurement_is_rejected(self) -> None:
        evidence = valid_evidence(multipart=True)
        mapping = valid_component_map(evidence, valid_spec(), observed=False)
        report = validate_dense_evidence(evidence, valid_spec(), mapping)
        self.assertIn("component_mapping_invalid", report["failureCategories"])

    def test_caps_hashes_and_unknown_fields_fail_closed(self) -> None:
        evidence = valid_evidence()
        evidence["globalGeometry"]["occupancyGrid"]["resolution"] = 33
        evidence["unexpectedAuthority"] = True
        evidence["provenance"]["glbSha256"] = "bad"
        report = validate_dense_evidence(evidence)
        self.assertFalse(report["passed"])
        self.assertIn("measurement_limit_exceeded", report["failureCategories"])
        self.assertIn("schema_invalid", report["failureCategories"])
        self.assertIn("evidence_hash_mismatch", report["failureCategories"])

    def test_alignment_content_is_hash_bound_and_matrix_is_exact(self) -> None:
        evidence = valid_evidence()
        evidence["alignment"]["sourceViewTransform"] = [1.0] * 15
        report = validate_dense_evidence(evidence)
        self.assertFalse(report["passed"])
        self.assertIn("evidence_hash_mismatch", report["failureCategories"])
        self.assertIn("schema_invalid", report["failureCategories"])


class ComponentEvidenceMapTest(unittest.TestCase):
    def test_seed_lists_existing_components_and_candidate_regions_without_mapping_them(self) -> None:
        result = seed_component_map(valid_spec(), valid_evidence(multipart=True))
        self.assertEqual(result["mappings"], [])
        self.assertEqual(result["availableComponentIds"], ["body", "roof-main"])
        self.assertTrue(all(item["candidateOnly"] for item in result["candidateRegions"]))

    def test_merged_evidence_refuses_component_map_seed(self) -> None:
        with self.assertRaisesRegex(ValueError, "semantic_boundary_insufficient"):
            seed_component_map(valid_spec(), valid_evidence())

    def test_mapping_rejects_low_confidence_duplicate_and_unknown_component(self) -> None:
        evidence = valid_evidence(multipart=True)
        mapping = valid_component_map(evidence, valid_spec())
        mapping["mappings"][0]["confidence"] = 0.79
        duplicate = copy.deepcopy(mapping["mappings"][0])
        duplicate["componentId"] = "missing"
        mapping["mappings"].append(duplicate)
        report = validate_dense_evidence(evidence, valid_spec(), mapping)
        self.assertIn("component_mapping_invalid", report["failureCategories"])


class DenseInfluenceAdmissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.evidence = self.root / "dense-evidence.v1.json"
        self.review = self.root / "visual-review.json"
        self.spec = self.root / "object-sculpt-spec.json"
        self.evidence.write_text(json.dumps(valid_evidence()), encoding="utf-8")
        self.review.write_text(
            json.dumps({"decision": "retain-as-generative-proxy-only", "glbSha256": "a" * 64}),
            encoding="utf-8",
        )
        self.spec.write_text(json.dumps(valid_spec()), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_approval_is_bound_to_glb_evidence_review_scope_and_spec(self) -> None:
        record = create_admission(
            self.evidence, self.review, self.spec, "global-massing", approved=True
        )
        self.assertEqual(record["decision"], "ALLOW")
        self.assertEqual(
            set(record["binding"]),
            {"glbSha256", "evidenceSha256", "visualReviewSha256", "scope", "targetSpecSha256"},
        )
        self.assertTrue(validate_admission(record, self.evidence, self.review, self.spec)["passed"])

    def test_without_explicit_approval_needs_user_action(self) -> None:
        record = create_admission(
            self.evidence, self.review, self.spec, "global-massing", approved=False
        )
        self.assertEqual(record["decision"], "NEEDS_USER_ACTION")

    def test_scope_above_evidence_ceiling_is_denied(self) -> None:
        record = create_admission(
            self.evidence, self.review, self.spec, "component-measurements", approved=True
        )
        self.assertEqual(record["decision"], "DENY")
        self.assertEqual(record["failureCategory"], "semantic_boundary_insufficient")

    def test_changed_target_or_review_invalidates_approval(self) -> None:
        record = create_admission(
            self.evidence, self.review, self.spec, "global-massing", approved=True
        )
        self.spec.write_text(json.dumps({**valid_spec(), "targetName": "Changed"}), encoding="utf-8")
        report = validate_admission(record, self.evidence, self.review, self.spec)
        self.assertFalse(report["passed"])
        self.assertIn("admission_hash_mismatch", report["failureCategories"])


def direct_admission(
    spec: dict[str, object], evidence: dict[str, object], scope: str = "global-massing"
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "kind": "dense-influence-admission",
        "decision": "ALLOW",
        "approvedInfluenceScope": scope,
        "binding": {
            "glbSha256": evidence["provenance"]["glbSha256"],
            "evidenceSha256": canonical_sha256(evidence),
            "visualReviewSha256": "9" * 64,
            "scope": scope,
            "targetSpecSha256": canonical_sha256(spec),
        },
    }


def component_signature(spec: dict[str, object]) -> list[tuple[object, ...]]:
    signature: list[tuple[object, ...]] = []

    def visit(items: object, parent: str | None = None) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, dict):
                signature.append(
                    (
                        item.get("id"),
                        parent,
                        item.get("primitive"),
                        item.get("topologyClass"),
                        item.get("materialId"),
                    )
                )
                visit(item.get("children"), str(item.get("id")))

    visit(spec.get("componentTree"))
    return signature


class DenseEvidenceProposalTest(unittest.TestCase):
    def test_global_massing_uses_shape_ratios_independent_of_mesh_unit_scale(self) -> None:
        accepted = valid_spec()
        evidence = valid_evidence()
        evidence["globalGeometry"]["bounds"]["size"] = [2.4, 4.2, 4.0]
        _, _, normal_fit = build_proposal(
            accepted, evidence, direct_admission(accepted, evidence)
        )

        scaled_evidence = copy.deepcopy(evidence)
        scaled_evidence["globalGeometry"]["bounds"]["size"] = [0.24, 0.42, 0.40]
        _, _, scaled_fit = build_proposal(
            accepted,
            scaled_evidence,
            direct_admission(accepted, scaled_evidence),
        )

        for scaled, normal in zip(
            scaled_fit["parameterVector"], normal_fit["parameterVector"], strict=True
        ):
            self.assertAlmostEqual(scaled, normal, places=12)
        self.assertGreater(len({round(value, 8) for value in normal_fit["parameterVector"]}), 1)

    def test_global_proposal_is_copy_only_and_reversible(self) -> None:
        accepted = valid_spec()
        before = copy.deepcopy(accepted)
        evidence = valid_evidence()
        evidence["globalGeometry"]["bounds"]["size"] = [2.4, 4.2, 4.0]
        proposal, delta, fit_plan = build_proposal(
            accepted, evidence, direct_admission(accepted, evidence)
        )
        self.assertEqual(accepted, before)
        self.assertNotEqual(proposal, accepted)
        self.assertEqual(apply_reverse_delta(proposal, delta), accepted)
        self.assertTrue(delta["changes"])
        self.assertTrue(
            all(item["scope"] == "global-massing" for item in delta["changes"])
        )
        self.assertEqual(fit_plan["minimumEvidenceImprovement"], 0.01)
        self.assertEqual(fit_plan["maximumSourceSilhouetteRegression"], 0.02)

    def test_forbidden_fields_never_change_and_scale_is_bounded(self) -> None:
        accepted = valid_spec()
        evidence = valid_evidence()
        evidence["globalGeometry"]["bounds"]["size"] = [20.0, 30.0, 30.0]
        proposal, delta, _ = build_proposal(
            accepted, evidence, direct_admission(accepted, evidence)
        )
        for change in delta["changes"]:
            self.assertIn(change["field"], GLOBAL_NUMERIC_FIELDS)
            self.assertLessEqual(change["new"] / change["old"] if change["old"] else 1.0, 1.2 + 1e-9)
        self.assertEqual(component_signature(proposal), component_signature(accepted))

    def test_component_scope_changes_only_explicit_fields(self) -> None:
        accepted = valid_spec()
        evidence = valid_evidence(multipart=True)
        evidence["regions"][1]["bounds"] = {
            "min": [-1.32, 1.0, -1.6],
            "max": [1.32, 2.2, 1.6],
            "size": [2.64, 1.2, 3.2],
        }
        mapping = valid_component_map(evidence, accepted)
        admission = direct_admission(accepted, evidence, "component-measurements")
        admission["binding"]["componentMapSha256"] = canonical_sha256(mapping)
        proposal, delta, _ = build_proposal(accepted, evidence, admission, mapping)
        self.assertEqual({item["field"] for item in delta["changes"]}, {
            "dimensions.width", "dimensions.height"
        })
        self.assertTrue(all(item["field"] in COMPONENT_NUMERIC_FIELDS for item in delta["changes"]))
        self.assertEqual(proposal["componentTree"][0]["dimensions"], accepted["componentTree"][0]["dimensions"])

    def test_stale_admission_and_forbidden_component_field_are_rejected(self) -> None:
        accepted = valid_spec()
        evidence = valid_evidence(multipart=True)
        mapping = valid_component_map(evidence, accepted)
        mapping["mappings"][0]["permittedFields"] = ["geometryDescriptor.profile"]
        admission = direct_admission(accepted, evidence, "component-measurements")
        admission["binding"]["componentMapSha256"] = canonical_sha256(mapping)
        with self.assertRaisesRegex(ValueError, "influence_scope_exceeded"):
            build_proposal(accepted, evidence, admission, mapping)
        stale = direct_admission(accepted, evidence)
        stale["binding"]["targetSpecSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "admission_hash_mismatch"):
            build_proposal(accepted, evidence, stale)


class DenseEvidenceSchemaFilesTest(unittest.TestCase):
    def test_schema_files_are_strict_and_versioned(self) -> None:
        for relative in (
            "docs/specs/dense-evidence.v1.schema.json",
            "docs/specs/component-evidence-map.v1.schema.json",
        ):
            schema = json.loads((ROOT / relative).read_text(encoding="utf-8"))
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertFalse(schema["additionalProperties"])


class DenseEvidenceDocumentationTest(unittest.TestCase):
    def test_docs_define_authority_cost_and_runtime_boundaries(self) -> None:
        text = (ROOT / "docs/integrations/trellis-dense-evidence.md").read_text(
            encoding="utf-8"
        )
        for phrase in (
            "source image is authoritative",
            "maxCostUsd = 0",
            "offline",
            "global-massing",
            "component-measurements",
            "no provider call",
            "never shipped at runtime",
            "proposed-object-sculpt-spec.json",
        ):
            self.assertIn(phrase, text)

    def test_every_changed_markdown_starts_with_last_updated(self) -> None:
        paths = (
            ROOT / "docs/integrations/trellis-dense-evidence.md",
            ROOT / "SKILL.md",
            ROOT / "README.md",
            ROOT / "ROADMAP.md",
            ROOT / "CHANGELOG.md",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertRegex(
                    path.read_text(encoding="utf-8").splitlines()[0],
                    # the contract is "starts with a dated Last-updated line", not a
                    # specific day: these files keep being updated after this feature
                    r"^> Last updated: \d{4}-\d{2}-\d{2} \d{2}:\d{2}$",
                )

    def test_public_docs_do_not_claim_trellis_generates_final_factory(self) -> None:
        for path in (ROOT / "README.md", ROOT / "ROADMAP.md", ROOT / "CHANGELOG.md"):
            text = path.read_text(encoding="utf-8")
            self.assertIn("dense evidence", text.lower())
            self.assertIn("code-only", text.lower())


if __name__ == "__main__":
    unittest.main()
