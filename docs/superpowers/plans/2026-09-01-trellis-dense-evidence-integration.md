> Last updated: 2026-09-01 20:10

# TRELLIS Dense-Evidence Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task in the current checkout. Steps use checkbox (`- [x]`) syntax for tracking. Do not dispatch subagents unless the user later requests delegation.

**Goal:** Connect an already admitted, cached TRELLIS/SF3D GLB to the native img2threejs pipeline as offline, bounded geometric evidence while preserving a semantic, editable, code-only TypeScript/Three.js runtime model.

**Architecture:** Add an optional `integrations/mesh3d/dense_evidence/` extractor that reads immutable free-assist artifacts and emits bounded, versioned JSON. Keep `forge/` stdlib-only: it validates that JSON, binds an explicit human-approved influence tuple, creates a reversible proposed `ObjectSculptSpec`, and compares image-only versus evidence-assisted browser results. The source image remains authoritative; merged GLBs can influence only global massing, and no provider call, upload, runtime GLB, automatic semantic labeling, or in-place accepted-spec mutation is allowed.

**Tech Stack:** Python 3.11-3.13; stdlib `argparse`, `dataclasses`, `hashlib`, `json`, `pathlib`, `unittest`; optional existing mesh3d environment with `trimesh`, NumPy, and SciPy; existing ObjectSculptSpec validator/generator; existing browser render bridge and image comparison helpers.

---

## Authorization and worktree constraints

- The current checkout is intentionally dirty. Preserve the existing edits to `forge/tests/test_free_generative_assist.py`, `integrations/mesh3d/pyproject.toml`, and `integrations/mesh3d/uv.lock`.
- Add dense-evidence tests in new files so the prior free-assist bugfix is not rewritten accidentally.
- The approved implementation must remain offline. Do not invoke TRELLIS, SF3D, Hugging Face, Gradio, upload APIs, or provider metadata probes.
- Use the cached whimsical-house run only for the final read-only acceptance. Never modify `raw/`, `normalized/`, `review/admission.json`, or `review/visual-review.json`.
- Do not commit, push, deploy, delete, or clean generated/user artifacts without a separate explicit authorization.

## File map

### New optional integration files

- `integrations/mesh3d/dense_evidence/__init__.py`: stable public imports and extractor version.
- `integrations/mesh3d/dense_evidence/__main__.py`: module entry point.
- `integrations/mesh3d/dense_evidence/model.py`: enums, typed records, canonical hashing, atomic JSON writes, and fail-closed errors.
- `integrations/mesh3d/dense_evidence/alignment.py`: validates the reviewed source-view transform, chirality, IoU, aspect ratio, and admissible scope.
- `integrations/mesh3d/dense_evidence/regions.py`: inventories mesh/node/primitive boundaries and applies explicit region selectors without inventing labels.
- `integrations/mesh3d/dense_evidence/extract.py`: loads normalized GLB/OBJ, extracts capped bounds/PCA/occupancy/cross-sections, and writes immutable-provenance evidence.
- `integrations/mesh3d/dense_evidence/cache.py`: content-addressed extraction cache and downstream invalidation keys.
- `integrations/mesh3d/dense_evidence/cli.py`: offline `extract`, `propose-scope`, and `verify-cache` commands.
- `integrations/mesh3d/tests/test_dense_evidence.py`: optional-dependency unit and offline extraction tests.

### New stdlib core files

- `forge/stage1_intake/check_dense_evidence.py`: validates schema, hashes, admission, uncertainty, scope ceiling, and optional component map.
- `forge/stage2_spec/new_component_evidence_map.py`: seeds a deny-by-default mapping against existing component IDs.
- `forge/stage2_spec/apply_dense_evidence.py`: emits a proposed spec, reversible delta, and bounded fit plan without changing the accepted spec.
- `forge/stage4_review/admit_dense_influence.py`: records approval bound to GLB/evidence/review/scope/spec hashes.
- `forge/stage4_review/compare_dense_evidence.py`: evaluates the dual baseline and returns accept/deny with explicit regressions.
- `forge/tests/test_dense_evidence_contract.py`: stdlib schema, mapping, admission, proposal, workflow, and CLI tests.
- `forge/tests/test_dense_evidence_review.py`: dual-baseline and runtime-source guard tests.

### Documentation/schema files

- `docs/specs/dense-evidence.v1.schema.json`: machine-readable evidence contract.
- `docs/specs/component-evidence-map.v1.schema.json`: explicit mapping contract.
- `docs/integrations/trellis-dense-evidence.md`: operating guide, authority model, artifact layout, and offline commands.
- `SKILL.md`: route the optional free-assist output through the new bridge only after admission and influence approval.
- `README.md`, `ROADMAP.md`, `CHANGELOG.md`: expose the implemented boundary and non-goals honestly.

### Existing files modified

- `forge/_shared/workflow_state.py`: add the optional dense-evidence setup gate only when selected.
- `forge/state.py`: add `--dense-evidence` to `init`.
- `forge/tests/test_workflow_state.py`: prove image-only compatibility and optional route ordering.
- `integrations/mesh3d/pyproject.toml`: add no dependency unless extraction demonstrates a missing existing runtime dependency.
- `integrations/mesh3d/uv.lock`: update only if `pyproject.toml` genuinely changes.

## Stable contracts chosen by this plan

### Evidence scope

```python
class InfluenceScope(str, Enum):
    NONE = "none"
    GLOBAL_MASSING = "global-massing"
    COMPONENT_MEASUREMENTS = "component-measurements"

SCOPE_RANK = {
    InfluenceScope.NONE: 0,
    InfluenceScope.GLOBAL_MASSING: 1,
    InfluenceScope.COMPONENT_MEASUREMENTS: 2,
}
```

There is no automatic, topology-copy, or full-mesh scope. Extraction always writes `approvedInfluenceScope: "none"`; only `admit_dense_influence.py` can produce a separate approval record with a higher scope. This removes the apparent circularity in the specification's example record.

### Version-1 evidence limits

```python
EXTRACTOR_VERSION = "dense-evidence-extractor-v1"
ALIGNMENT_PROFILE_VERSION = "source-view-alignment-v1"
MAX_OCCUPANCY_RESOLUTION = 32
MAX_CROSS_SECTIONS = 32
MAX_CROSS_SECTION_POINTS = 64
MIN_GLOBAL_IOU = 0.65
MIN_COMPONENT_IOU = 0.75
MAX_ASPECT_RATIO_ERROR = 0.15
MIN_COMPONENT_CONFIDENCE = 0.80
```

### Version-1 mutation allowlist

The bridge changes numeric geometry only. It never changes IDs, parent links, topology classes, primitive names, materials, pivots, sockets, attachments, interactions, repetition systems, or build passes.

```python
GLOBAL_NUMERIC_FIELDS = frozenset({
    "dimensions.width", "dimensions.height", "dimensions.depth",
    "dimensions.radius", "dimensions.length",
    "transform.position.0", "transform.position.1", "transform.position.2",
})

COMPONENT_NUMERIC_FIELDS = frozenset({
    "dimensions.width", "dimensions.height", "dimensions.depth",
    "dimensions.radius", "dimensions.length",
})
```

`global-massing` applies one shared per-axis scale vector to all existing top-level and descendant component dimensions and local positions. `component-measurements` may additionally change only the dimension fields explicitly named in an approved component mapping. Geometry-descriptor mutation remains denied in version 1; the specification permits it but does not require it, and adding descriptor-specific semantics without per-primitive contracts would violate YAGNI and the reversible-delta guarantee.

### Exit codes

- `0`: valid/complete/cache hit/accepted.
- `1`: valid input but policy or quality gate returns `DENY`.
- `2`: malformed input, unreadable artifact, or local execution error.
- `3`: explicit approval or browser evidence is required.

## Task 1: Evidence model, hashing, and immutable provider-run validation

**Files:**
- Create: `integrations/mesh3d/dense_evidence/__init__.py`
- Create: `integrations/mesh3d/dense_evidence/model.py`
- Create: `integrations/mesh3d/dense_evidence/cache.py`
- Create: `integrations/mesh3d/tests/test_dense_evidence.py`

- [x] **Step 1: Write failing model and cache tests**

```python
class DenseEvidenceModelTest(unittest.TestCase):
    def test_scope_enum_contains_only_three_deny_by_default_values(self):
        self.assertEqual(
            [item.value for item in InfluenceScope],
            ["none", "global-massing", "component-measurements"],
        )

    def test_run_validation_rejects_hash_drift_and_unreviewed_mesh(self):
        run = make_completed_run(self.root, visual_status="reviewed")
        validated = validate_provider_run(run, (self.source,))
        self.assertEqual(validated.glb_sha256, sha256_file(run / "normalized/reference.glb"))
        (run / "normalized/reference.glb").write_bytes(b"changed")
        with self.assertRaisesRegex(DenseEvidenceError, "evidence_hash_mismatch"):
            validate_provider_run(run, (self.source,))

    def test_cache_key_changes_for_every_authoritative_input(self):
        base = ExtractionCacheInput("a" * 64, "b" * 64, ("c" * 64,), "v1", "a1", None, "m1")
        keys = {
            extraction_cache_key(base),
            extraction_cache_key(dataclasses.replace(base, glb_sha256="d" * 64)),
            extraction_cache_key(dataclasses.replace(base, source_image_sha256=("e" * 64,))),
            extraction_cache_key(dataclasses.replace(base, measurement_config_sha256="m2")),
        }
        self.assertEqual(len(keys), 4)
```

- [x] **Step 2: Run the optional test to verify RED**

Run:

```bash
uv run --project integrations/mesh3d python -m unittest integrations.mesh3d.tests.test_dense_evidence.DenseEvidenceModelTest -v
```

Expected: `ModuleNotFoundError: integrations.mesh3d.dense_evidence`.

- [x] **Step 3: Implement immutable records, canonical hashes, and provider-run validation**

```python
@dataclass(frozen=True)
class ProviderRun:
    root: Path
    source_image_sha256: tuple[str, ...]
    glb_sha256: str
    obj_sha256: str
    provider_id: str
    structural_status: str
    visual_review_status: str
    semantic_status: str

class DenseEvidenceError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(f"{category}: {message}")
        self.category = category

def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

`validate_provider_run()` must resolve the run directory, load `provider-receipt.json`, `review/admission.json`, and `review/visual-review.json`, recompute normalized GLB/OBJ and ordered source-image hashes, require structural `pass`, require a completed visual review, and derive semantic readiness from `probe_glb.py` data. It returns `mesh_not_structurally_admitted`, `visual_review_missing`, or `evidence_hash_mismatch` without touching any run artifact. Treat the house verdict `retain-as-generative-proxy-only` as reviewed but cap it at global evidence.

- [x] **Step 4: Implement content-addressed cache records**

```python
def extraction_cache_key(value: ExtractionCacheInput) -> str:
    return canonical_sha256({
        "normalizedGlbSha256": value.glb_sha256,
        "normalizedObjSha256": value.obj_sha256,
        "sourceImageSha256": list(value.source_image_sha256),
        "extractorVersion": value.extractor_version,
        "alignmentProfileVersion": value.alignment_profile_version,
        "componentMapSha256": value.component_map_sha256,
        "measurementConfigSha256": value.measurement_config_sha256,
    })
```

Write cache/status JSON atomically under `dense-evidence/`; a component-map change invalidates only mapping/proposal keys, not the base geometry extraction key.

- [x] **Step 5: Run the test to verify GREEN**

Run:

```bash
uv run --project integrations/mesh3d python -m unittest integrations.mesh3d.tests.test_dense_evidence.DenseEvidenceModelTest -v
```

Expected: scope, immutable-run, hash-drift, atomic-write, and cache-key tests pass.

## Task 2: Reviewed alignment and bounded global extraction

**Files:**
- Create: `integrations/mesh3d/dense_evidence/alignment.py`
- Create: `integrations/mesh3d/dense_evidence/regions.py`
- Create: `integrations/mesh3d/dense_evidence/extract.py`
- Modify: `integrations/mesh3d/tests/test_dense_evidence.py`

- [x] **Step 1: Write failing alignment and extraction tests**

```python
class DenseEvidenceExtractionTest(unittest.TestCase):
    def test_merged_glb_is_capped_at_global_massing(self):
        run = make_completed_run(self.root, mesh_count=1, primitive_count=1)
        result = extract_run(run, (self.source,), valid_alignment(), self.out)
        self.assertEqual(result["admission"]["maximumInfluenceScope"], "global-massing")
        self.assertEqual(result["admission"]["approvedInfluenceScope"], "none")
        self.assertEqual(result["regions"], [])
        self.assertLessEqual(result["globalGeometry"]["occupancyGrid"]["resolution"], 32)

    def test_threshold_failure_denies_instead_of_relaxing(self):
        with self.assertRaisesRegex(DenseEvidenceError, "alignment_failed"):
            validate_alignment({**valid_alignment(), "sourceViewSilhouetteIou": 0.64}, "global-massing")

    def test_single_view_hidden_surfaces_are_non_authoritative(self):
        result = extract_run(make_completed_run(self.root), (self.source,), valid_alignment(), self.out)
        self.assertEqual(result["uncertainty"]["hiddenSurfacePolicy"], "non-authoritative")
        self.assertEqual(result["uncertainty"]["rearConfidence"], "low")
```

- [x] **Step 2: Run the extraction tests to verify RED**

Run:

```bash
uv run --project integrations/mesh3d python -m unittest integrations.mesh3d.tests.test_dense_evidence.DenseEvidenceExtractionTest -v
```

Expected: imports for `validate_alignment` and `extract_run` fail.

- [x] **Step 3: Implement alignment validation with no automatic camera solving**

```python
def maximum_scope(alignment: dict[str, object], semantic_status: str) -> InfluenceScope:
    iou = finite_unit(alignment["sourceViewSilhouetteIou"], "sourceViewSilhouetteIou")
    aspect_error = finite_unit(alignment["projectedAspectRatioError"], "projectedAspectRatioError")
    if aspect_error > MAX_ASPECT_RATIO_ERROR or iou < MIN_GLOBAL_IOU:
        raise DenseEvidenceError("alignment_failed", "source-view thresholds were not met")
    if semantic_status == "sufficient" and iou >= MIN_COMPONENT_IOU:
        return InfluenceScope.COMPONENT_MEASUREMENTS
    return InfluenceScope.GLOBAL_MASSING
```

Require a 16-float finite `sourceViewTransform`, `+Y` up, `+Z` forward, right-handed output, an explicit axis-operation audit, chirality status, source silhouette IoU, aspect-ratio error, browser capture paths, and hashes. `chirality_ambiguous` caps the result at global evidence; it never guesses a reflection.

- [x] **Step 4: Implement mesh sampling and bounded measurements**

Load the normalized GLB with `trimesh.load(..., force="scene", process=False)`, apply node transforms, and concatenate sampled world-space vertices. Compute:

```python
global_geometry = {
    "bounds": {"min": mins.tolist(), "max": maxs.tolist(), "size": (maxs - mins).tolist()},
    "principalAxes": pca_axes(points),
    "occupancyGrid": sparse_occupancy(points, resolution=min(config.resolution, 32)),
    "crossSections": cross_sections(points, axis="y", count=min(config.sections, 32), max_points=64),
    "silhouetteViews": [reviewed_source_view(alignment)],
}
```

Reject non-finite points, zero-volume bounds, missing geometry, resolution above `32^3`, more than 32 sections, or more than 64 points per section. Store sparse occupied cell indices and resampled section profiles, never full vertices/indices.

- [x] **Step 5: Implement candidate-boundary inventory without semantic labels**

```python
def inventory_boundaries(scene: trimesh.Scene) -> list[dict[str, object]]:
    return [
        {"regionId": f"node:{node}/geometry:{geometry}", "node": node,
         "geometry": geometry, "candidateOnly": True, "semanticLabel": None}
        for node, geometry in sorted(scene.graph.nodes_geometry)
    ]
```

A one-node/one-geometry/one-primitive scene emits no component regions. Multipart records remain `candidateOnly: true`; names/materials are provenance selectors, not accepted semantic labels.

- [x] **Step 6: Run extraction tests to verify GREEN**

Run:

```bash
uv run --project integrations/mesh3d python -m unittest integrations.mesh3d.tests.test_dense_evidence.DenseEvidenceExtractionTest -v
```

Expected: merged, multipart-candidate, mirrored, degenerate, threshold, occupancy-cap, cross-section-cap, and uncertainty tests pass.

## Task 3: Offline extractor CLI and resumability

**Files:**
- Create: `integrations/mesh3d/dense_evidence/cli.py`
- Create: `integrations/mesh3d/dense_evidence/__main__.py`
- Modify: `integrations/mesh3d/dense_evidence/__init__.py`
- Modify: `integrations/mesh3d/tests/test_dense_evidence.py`

- [x] **Step 1: Write failing CLI/no-network tests**

```python
class DenseEvidenceCliTest(unittest.TestCase):
    def test_help_exposes_no_provider_upload_token_or_retry_flags(self):
        result = run_cli("--help")
        for forbidden in ("provider", "upload", "token", "retry", "endpoint", "max-cost"):
            self.assertNotIn(f"--{forbidden}", result.stdout)

    def test_extract_cache_hit_does_not_load_mesh_again(self):
        first = run_cli("extract", "--run", str(self.run), "--source-image", str(self.source),
                        "--alignment", str(self.alignment), "--out-dir", str(self.out))
        self.assertEqual(first.returncode, 0)
        second = run_cli("verify-cache", "--run", str(self.run), "--source-image", str(self.source),
                         "--alignment", str(self.alignment), "--out-dir", str(self.out))
        self.assertEqual(json.loads(second.stdout)["cacheHit"], True)
```

- [x] **Step 2: Run CLI tests to verify RED**

Run:

```bash
uv run --project integrations/mesh3d python -m unittest integrations.mesh3d.tests.test_dense_evidence.DenseEvidenceCliTest -v
```

Expected: module entry point is missing.

- [x] **Step 3: Implement the exact offline commands**

```text
python -m integrations.mesh3d.dense_evidence extract \
  --run RUN --source-image IMAGE [--source-image IMAGE] \
  --alignment ALIGNMENT.json --out-dir RUN/dense-evidence

python -m integrations.mesh3d.dense_evidence propose-scope \
  --run RUN --source-image IMAGE --alignment ALIGNMENT.json

python -m integrations.mesh3d.dense_evidence verify-cache \
  --run RUN --source-image IMAGE --alignment ALIGNMENT.json \
  --out-dir RUN/dense-evidence
```

`extract` writes `extraction-request.json`, `alignment.json`, `dense-evidence.v1.json`, and `status.json` atomically. A cache hit returns the persisted evidence only after rechecking every authoritative hash. A failed extraction records a category and last durable artifact, and a later invocation resumes locally from normalized GLB/OBJ.

- [x] **Step 4: Add an import/network construction guard**

Patch `socket.socket`, `urllib.request.urlopen`, `huggingface_hub`, and free-assist provider factory symbols to raise during every test. Assert that extraction completes anyway. The dense-evidence package must not import `integrations.mesh3d.free_assist.providers` or accept credentials.

- [x] **Step 5: Run CLI tests to verify GREEN**

Run:

```bash
uv run --project integrations/mesh3d python -m unittest integrations.mesh3d.tests.test_dense_evidence.DenseEvidenceCliTest -v
```

Expected: CLI, cache, resume, atomic artifact, and no-network tests pass.

## Task 4: Machine-readable schemas and stdlib admission validator

**Files:**
- Create: `docs/specs/dense-evidence.v1.schema.json`
- Create: `docs/specs/component-evidence-map.v1.schema.json`
- Create: `forge/stage1_intake/check_dense_evidence.py`
- Create: `forge/tests/test_dense_evidence_contract.py`

- [x] **Step 1: Write failing stdlib validator tests**

```python
class DenseEvidenceContractTest(unittest.TestCase):
    def test_valid_global_record_passes_without_optional_dependencies(self):
        report = validate_dense_evidence(valid_evidence(), expected_spec=None, component_map=None)
        self.assertTrue(report["passed"])
        self.assertEqual(report["maximumInfluenceScope"], "global-massing")

    def test_merged_mesh_component_claim_fails_closed(self):
        evidence = valid_evidence(maximum_scope="global-massing")
        mapping = valid_component_map()
        report = validate_dense_evidence(evidence, expected_spec=valid_spec(), component_map=mapping)
        self.assertIn("semantic_boundary_insufficient", report["failureCategories"])

    def test_single_view_hidden_measurement_is_rejected(self):
        mapping = valid_component_map(permitted_fields=["dimensions.depth"], observed=False)
        report = validate_dense_evidence(valid_evidence(), valid_spec(), mapping)
        self.assertIn("component_mapping_invalid", report["failureCategories"])
```

- [x] **Step 2: Run validator tests to verify RED**

Run:

```bash
python3 -m unittest forge.tests.test_dense_evidence_contract.DenseEvidenceContractTest -v
```

Expected: `forge.stage1_intake.check_dense_evidence` is missing.

- [x] **Step 3: Add complete JSON schemas**

The evidence schema must require exactly the top-level contract groups and constrain all hashes, enums, transform lengths, confidence values, occupancy resolution, cross-section counts, and hidden-surface policy. The component-map schema must require:

```json
{
  "schemaVersion": 1,
  "kind": "component-evidence-map",
  "targetSpecSha256": "<64 lowercase hex>",
  "evidenceSha256": "<64 lowercase hex>",
  "glbSha256": "<64 lowercase hex>",
  "mappings": [{
    "componentId": "roof-main",
    "selectors": [{"regionId": "node:Roof/geometry:RoofMesh"}],
    "mappingMethod": "human-reviewed-node-boundary",
    "evidenceRefs": ["review/semantic-roof.png"],
    "confidence": 0.86,
    "permittedFields": ["dimensions.width", "dimensions.height"],
    "observedSurface": true,
    "hiddenLimitations": ["rear slope is non-authoritative"]
  }]
}
```

Set `additionalProperties: false` on authority-bearing records; reserve optional non-authoritative extension data under an explicit `extensions` object.

- [x] **Step 4: Implement pure-stdlib semantic validation**

```python
def validate_dense_evidence(
    evidence: object,
    expected_spec: dict[str, object] | None = None,
    component_map: dict[str, object] | None = None,
) -> dict[str, object]:
    errors: list[str] = []
    categories: set[str] = set()
    # Validate exact schema version/kind, hashes, finite values, caps, authority and mapping.
    return {
        "schemaVersion": 1,
        "passed": not errors,
        "failureCategories": sorted(categories),
        "errors": errors,
        "maximumInfluenceScope": maximum_scope,
    }
```

Do not import NumPy, SciPy, trimesh, provider code, or JSON Schema libraries. The CLI accepts `--evidence`, optional `--spec`, optional `--component-map`, `--out`, and returns `0` pass, `1` deny, `2` malformed invocation.

- [x] **Step 5: Run validator tests to verify GREEN**

Run:

```bash
python3 -m unittest forge.tests.test_dense_evidence_contract.DenseEvidenceContractTest -v
```

Expected: provenance, caps, merged/multipart, hash drift, confidence, overlap, hidden-surface, unknown-component, and forbidden-field tests pass.

## Task 5: Explicit component-map authoring

**Files:**
- Create: `forge/stage2_spec/new_component_evidence_map.py`
- Modify: `forge/tests/test_dense_evidence_contract.py`

- [x] **Step 1: Write failing map-seeding tests**

```python
class ComponentEvidenceMapTest(unittest.TestCase):
    def test_seed_lists_existing_components_and_candidate_regions_without_mapping_them(self):
        result = seed_component_map(valid_spec(), multipart_evidence())
        self.assertEqual(result["mappings"], [])
        self.assertEqual(result["availableComponentIds"], ["body", "roof-main"])
        self.assertTrue(all(item["candidateOnly"] for item in result["candidateRegions"]))

    def test_merged_evidence_refuses_component_map_seed(self):
        with self.assertRaisesRegex(ValueError, "semantic_boundary_insufficient"):
            seed_component_map(valid_spec(), valid_evidence(maximum_scope="global-massing"))
```

- [x] **Step 2: Run map tests to verify RED**

Run:

```bash
python3 -m unittest forge.tests.test_dense_evidence_contract.ComponentEvidenceMapTest -v
```

Expected: map seeder import fails.

- [x] **Step 3: Implement deny-by-default seeding and CLI**

```text
python3 forge/stage2_spec/new_component_evidence_map.py \
  --spec object-sculpt-spec.json \
  --evidence dense-evidence.v1.json \
  --out component-evidence-map.json
```

Hash the exact input bytes, list current component IDs and candidate selectors, and emit an empty `mappings` array. Never infer labels from node names, materials, colors, connected components, or provider metadata. Validation requires confidence `>= 0.80`, explicit evidence refs, observed-surface status, non-overlapping exclusive selectors, and only known component IDs.

- [x] **Step 4: Run map tests to verify GREEN**

Run:

```bash
python3 -m unittest forge.tests.test_dense_evidence_contract.ComponentEvidenceMapTest -v
```

Expected: empty seed, merged refusal, unknown component, duplicate selector, low confidence, and hash binding tests pass.

## Task 6: Hash-bound influence admission

**Files:**
- Create: `forge/stage4_review/admit_dense_influence.py`
- Modify: `forge/tests/test_dense_evidence_contract.py`

- [x] **Step 1: Write failing admission tests**

```python
class DenseInfluenceAdmissionTest(unittest.TestCase):
    def test_approval_is_bound_to_glb_evidence_review_scope_and_spec(self):
        record = create_admission(
            evidence_path=self.evidence, visual_review_path=self.review,
            target_spec_path=self.spec, requested_scope="global-massing",
        )
        self.assertEqual(record["decision"], "ALLOW")
        self.assertEqual(set(record["binding"]), {
            "glbSha256", "evidenceSha256", "visualReviewSha256", "scope", "targetSpecSha256"
        })

    def test_scope_above_evidence_ceiling_is_denied(self):
        result = create_admission(self.evidence, self.review, self.spec, "component-measurements")
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["failureCategory"], "semantic_boundary_insufficient")
```

- [x] **Step 2: Run admission tests to verify RED**

Run:

```bash
python3 -m unittest forge.tests.test_dense_evidence_contract.DenseInfluenceAdmissionTest -v
```

Expected: admission module is missing.

- [x] **Step 3: Implement explicit admission with no implicit approval**

```text
python3 forge/stage4_review/admit_dense_influence.py \
  --evidence dense-evidence.v1.json \
  --visual-review review/visual-review.json \
  --target-spec object-sculpt-spec.json \
  --scope global-massing \
  --approve-influence \
  --out influence-admission.json
```

Without `--approve-influence`, return `NEEDS_USER_ACTION` and write no ALLOW record. Refuse a scope above the extractor/validator ceiling. Recompute every hash when creating and consuming admission. A changed GLB, evidence, review, scope, map, or target spec invalidates the approval.

- [x] **Step 4: Run admission tests to verify GREEN**

Run:

```bash
python3 -m unittest forge.tests.test_dense_evidence_contract.DenseInfluenceAdmissionTest -v
```

Expected: no-approval, tuple binding, scope ceiling, stale review, changed target spec, and changed evidence tests pass.

## Task 7: Reversible ObjectSculptSpec proposals and bounded fitting

**Files:**
- Create: `forge/stage2_spec/apply_dense_evidence.py`
- Modify: `forge/tests/test_dense_evidence_contract.py`

- [x] **Step 1: Write failing non-mutation and allowlist tests**

```python
class DenseEvidenceProposalTest(unittest.TestCase):
    def test_global_proposal_is_copy_only_and_reversible(self):
        accepted = valid_spec()
        before = copy.deepcopy(accepted)
        proposal, delta, fit_plan = build_proposal(accepted, self.evidence, self.admission)
        self.assertEqual(accepted, before)
        self.assertNotEqual(proposal, accepted)
        self.assertEqual(apply_reverse_delta(proposal, delta), accepted)
        self.assertTrue(all(item["scope"] == "global-massing" for item in delta["changes"]))

    def test_forbidden_fields_never_change(self):
        proposal, delta, _ = build_proposal(valid_spec(), self.evidence, self.admission)
        for change in delta["changes"]:
            self.assertIn(change["field"], GLOBAL_NUMERIC_FIELDS)
        self.assertEqual(component_signature(proposal), component_signature(valid_spec()))
```

- [x] **Step 2: Run proposal tests to verify RED**

Run:

```bash
python3 -m unittest forge.tests.test_dense_evidence_contract.DenseEvidenceProposalTest -v
```

Expected: proposal module is missing.

- [x] **Step 3: Implement global-massing proposal math**

```python
def bounded_ratio(measured: float, authored: float, maximum_delta: float) -> float:
    raw = measured / authored
    return min(1.0 + maximum_delta, max(1.0 - maximum_delta, raw))

def scale_dimensions(component: dict[str, object], xyz: tuple[float, float, float]) -> None:
    dimensions = component.get("dimensions")
    if isinstance(dimensions, dict):
        for field, factor in (("width", xyz[0]), ("height", xyz[1]), ("depth", xyz[2])):
            if isinstance(dimensions.get(field), (int, float)):
                dimensions[field] = float(dimensions[field]) * factor
```

Derive the authored aggregate bounds from existing component dimensions/transforms, compute one shared axis scale, clamp each axis using `qualityContract.denseEvidence.maxNumericDeltaFraction` or default `0.20`, apply it consistently to dimensions and local positions, and record old/new/measured/confidence/source-region/reason for every numeric change. Radius uses the mean X/Z factor; length uses the component's declared dominant axis only when present, otherwise it is not changed.

- [x] **Step 4: Implement component-measurement proposals**

Require a valid component map and admission at component scope. For each mapping, read only reviewed region bounds and modify only explicitly permitted dimension fields. Reject hierarchy, topology, material, pivot, socket, attachment, interaction, repetition, or geometry-descriptor paths with `influence_scope_exceeded`.

- [x] **Step 5: Emit proposal, delta, and fit plan atomically**

```text
python3 forge/stage2_spec/apply_dense_evidence.py \
  --spec object-sculpt-spec.json \
  --evidence dense-evidence.v1.json \
  --admission influence-admission.json \
  [--component-map component-evidence-map.json] \
  --out proposed-object-sculpt-spec.json \
  --delta-out spec-delta.json \
  --fit-plan-out fit-plan.json
```

The `fit-plan.json` names the bounded parameter vector, baseline hashes, correction group, required browser views, minimum improvement `0.01`, maximum source silhouette regression `0.02`, and existing correction-loop budget. No `--in-place` flag exists.

- [x] **Step 6: Validate proposed specs through the existing validator**

Run within the test:

```bash
python3 forge/stage2_spec/validate_sculpt_spec.py proposed-object-sculpt-spec.json
python3 forge/stage2_spec/validate_sculpt_spec.py proposed-object-sculpt-spec.json --strict-quality
```

If either fails, persist proposal/delta/fit plan and return `strict_quality_failed`; never change the accepted spec or factory.

- [x] **Step 7: Run proposal tests to verify GREEN**

Run:

```bash
python3 -m unittest forge.tests.test_dense_evidence_contract.DenseEvidenceProposalTest -v
```

Expected: non-mutation, reversibility, bounds, global consistency, component allowlist, scope, admission-hash, and strict-validation tests pass.

## Task 8: Optional workflow-state routing without image-only regression

**Files:**
- Modify: `forge/_shared/workflow_state.py`
- Modify: `forge/state.py`
- Modify: `forge/tests/test_workflow_state.py`

- [x] **Step 1: Write failing opt-in routing tests**

```python
def test_dense_evidence_step_is_absent_by_default(self):
    ids = [item["id"] for item in new_state("reference.png")["checklist"]]
    self.assertNotIn("dense-evidence-admission", ids)

def test_dense_evidence_route_adds_ordered_optional_gate(self):
    state = new_state("reference.png", spec="spec.json", dense_evidence=True)
    ids = [item["id"] for item in state["checklist"]]
    self.assertLess(ids.index("reference-admission"), ids.index("dense-evidence-admission"))
    self.assertLess(ids.index("dense-evidence-admission"), ids.index("strict-validation"))
    command = next(item["command"] for item in state["checklist"] if item["id"] == "dense-evidence-admission")
    self.assertIn("check_dense_evidence.py", command)
```

- [x] **Step 2: Run workflow tests to verify RED**

Run:

```bash
python3 -m unittest forge.tests.test_workflow_state.WorkflowStateTest.test_dense_evidence_step_is_absent_by_default forge.tests.test_workflow_state.WorkflowStateTest.test_dense_evidence_route_adds_ordered_optional_gate -v
```

Expected: `new_state()` rejects `dense_evidence`.

- [x] **Step 3: Add the opt-in state flag and setup step**

```python
DENSE_EVIDENCE_STEPS: Final = ((
    "dense-evidence-admission",
    "Validate dense-evidence.v1.json, record hash-bound influence approval, and emit a proposed spec; never mutate {spec} in place",
),)

def new_state(..., dense_evidence: bool = False) -> dict[str, Any]:
    # Insert only when requested and persist artifacts.denseEvidenceSelected.
```

Add `forge/state.py init --dense-evidence`, store the selection in `artifacts`, and validate it as a boolean. Keep schema version 1 backward compatible by treating an absent field as false. Do not add this route to `character` or `cs2`; return a clear state error if combined in version 1.

- [x] **Step 4: Run workflow tests to verify GREEN**

Run:

```bash
python3 -m unittest forge.tests.test_workflow_state -v
```

Expected: all existing workflow tests plus opt-in route tests pass unchanged for image-only runs.

## Task 9: Dual-baseline comparison and final acceptance decision

**Files:**
- Create: `forge/stage4_review/compare_dense_evidence.py`
- Create: `forge/tests/test_dense_evidence_review.py`

- [x] **Step 1: Write failing dual-baseline tests**

```python
class DenseEvidenceReviewTest(unittest.TestCase):
    def test_candidate_needs_source_safety_and_evidence_improvement(self):
        report = compare_dense_evidence(
            baseline_source={"silhouetteIou": 0.81, "criticalFeatures": {"roof": True}},
            candidate_source={"silhouetteIou": 0.80, "criticalFeatures": {"roof": True}},
            baseline_evidence={"massingSimilarity": 0.70},
            candidate_evidence={"massingSimilarity": 0.72},
            deterministic_gates=clean_gates(),
            admission=valid_admission(),
        )
        self.assertEqual(report["decision"], "ALLOW")

    def test_glb_improvement_cannot_hide_source_regression(self):
        report = compare_dense_evidence(
            baseline_source={"silhouetteIou": 0.81, "criticalFeatures": {"roof": True}},
            candidate_source={"silhouetteIou": 0.78, "criticalFeatures": {"roof": True}},
            baseline_evidence={"massingSimilarity": 0.50},
            candidate_evidence={"massingSimilarity": 0.95},
            deterministic_gates=clean_gates(),
            admission=valid_admission(),
        )
        self.assertEqual(report["failureCategory"], "source_fidelity_regression")
```

- [x] **Step 2: Run review tests to verify RED**

Run:

```bash
python3 -m unittest forge.tests.test_dense_evidence_review.DenseEvidenceReviewTest -v
```

Expected: comparison module is missing.

- [x] **Step 3: Implement the exact decision rule**

```python
source_regression = baseline_source_iou - candidate_source_iou
evidence_improvement = max(
    candidate_evidence[name] - baseline_evidence[name]
    for name in declared_evidence_targets
)
allowed = (
    not critical_regressions
    and source_regression <= 0.02
    and evidence_improvement >= 0.01
    and all(deterministic_gates.values())
    and within_approved_scope
    and browser_hashes_complete
)
```

Consume recorded image-only and candidate source metrics plus provider/procedural geometry-pass metrics. Require strict-quality, pass order, turntable, attachment, and intersection gates. For merged GLBs, per-region claims remain blocked even if global scores improve. Output `source_fidelity_regression`, `fit_no_improvement`, `influence_scope_exceeded`, or `browser_evidence_missing` as appropriate.

- [x] **Step 4: Add runtime-source exclusion tests**

Scan the generated candidate factory and its package graph. Fail if it contains `GLTFLoader`, `.glb`, `.obj`, binary `fetch(`, provider endpoint strings, signed URLs, or copied mesh payload markers. Assert that the accepted runtime remains a TypeScript factory built from the proposed spec.

- [x] **Step 5: Run review tests to verify GREEN**

Run:

```bash
python3 -m unittest forge.tests.test_dense_evidence_review -v
```

Expected: source regression, evidence improvement, critical feature, missing browser evidence, dirty deterministic gate, scope, and runtime-loader tests pass.

## Task 10: Documentation and discoverability

**Files:**
- Create: `docs/integrations/trellis-dense-evidence.md`
- Modify: `SKILL.md`
- Modify: `README.md`
- Modify: `ROADMAP.md`
- Modify: `CHANGELOG.md`
- Modify: `forge/tests/test_dense_evidence_contract.py`

- [x] **Step 1: Write failing documentation contract tests**

```python
class DenseEvidenceDocumentationTest(unittest.TestCase):
    def test_docs_define_authority_cost_and_runtime_boundaries(self):
        text = DOC.read_text(encoding="utf-8")
        for phrase in (
            "source image is authoritative", "maxCostUsd = 0", "offline",
            "global-massing", "component-measurements", "no provider call",
            "never shipped at runtime", "proposed-object-sculpt-spec.json",
        ):
            self.assertIn(phrase, text)

    def test_every_changed_markdown_starts_with_last_updated(self):
        for path in CHANGED_MARKDOWN:
            self.assertRegex(path.read_text(encoding="utf-8").splitlines()[0], r"^> Last updated: 2026-09-01 \d{2}:\d{2}$")
```

- [x] **Step 2: Run documentation tests to verify RED**

Run:

```bash
python3 -m unittest forge.tests.test_dense_evidence_contract.DenseEvidenceDocumentationTest -v
```

Expected: integration guide is missing.

- [x] **Step 3: Write the operating guide and route documentation**

Document the exact order:

```text
cached free_assist run
  -> reviewed alignment manifest
  -> offline extraction/cache
  -> stdlib evidence validation
  -> optional explicit component map
  -> hash-bound human influence approval
  -> proposed spec + reversible delta + fit plan
  -> existing strict validation and factory generation
  -> browser A/B comparison against source and advisory GLB
  -> accept proposal or retain image-only baseline
```

State single-view uncertainty, merged-mesh limitation, immutable provider artifacts, no automatic semantic labels, no token arguments, no upload/provider retry, no quota use, no runtime GLB, and no character-route replacement. Include failure categories, exit codes, cache invalidation, resume behavior, and complete example commands.

- [x] **Step 4: Update public capability status**

Add the new optional route to `SKILL.md` after free generative reference acquisition, and to README/roadmap/changelog without implying that TRELLIS directly generates the final semantic factory. Keep every changed Markdown file's `Last updated` line first.

- [x] **Step 5: Run documentation tests to verify GREEN**

Run:

```bash
python3 -m unittest forge.tests.test_dense_evidence_contract.DenseEvidenceDocumentationTest -v
```

Expected: route, safety, authority, uncertainty, and Last-updated contracts pass.

## Task 11: Offline integration, cached-house acceptance, and regression verification

**Files:**
- Modify: `integrations/mesh3d/tests/test_dense_evidence.py`
- Modify: `forge/tests/test_dense_evidence_contract.py`
- Modify: `forge/tests/test_dense_evidence_review.py`
- Create locally, do not commit: cached-run `dense-evidence/` artifacts listed by the specification

- [x] **Step 1: Add a generated-fixture end-to-end test**

```python
def test_cached_run_to_proposed_spec_is_offline_and_preserves_inputs(self):
    before = hash_tree(self.run / "raw", self.run / "normalized", self.run / "review")
    evidence = extract_run(self.run, (self.source,), valid_alignment(), self.out)
    admission = approved_global_admission(evidence, self.spec, self.review)
    proposal, delta, fit_plan = build_proposal(self.spec_payload, evidence, admission)
    self.assertEqual(hash_tree(self.run / "raw", self.run / "normalized", self.run / "review"), before)
    self.assertTrue(delta["changes"])
    self.assertEqual(fit_plan["maximumSourceSilhouetteRegression"], 0.02)
    self.assertEqual(PROVIDER_CALLS, [])
```

- [x] **Step 2: Run all focused suites**

Run:

```bash
uv run --project integrations/mesh3d python -m unittest integrations.mesh3d.tests.test_dense_evidence -v
python3 -m unittest forge.tests.test_dense_evidence_contract forge.tests.test_dense_evidence_review forge.tests.test_workflow_state -v
```

Expected: all focused optional and stdlib tests pass with no network access.

- [x] **Step 3: Exercise the cached whimsical-house boundary read-only**

Use:

```text
/Users/nicco/Desktop/img2threejs-free-assist-artifacts/whimsical-hearth-house/runs/20260831T234449.690726Z-16e8f1d8c0
```

First hash all immutable input artifacts. Author a reviewed `alignment.json` from the existing browser evidence; do not create new provider output. Run `propose-scope`, then `extract`. Expected:

- provider calls: `0`;
- monetary cost: `$0`;
- raw/normalized/review input hashes unchanged;
- semantic boundary: merged/insufficient;
- maximum scope: `global-massing`;
- approved scope in evidence: `none`;
- component influence: denied;
- extraction/proposal artifacts: preserved locally.

Do not manufacture an `ALLOW` influence record merely to make the acceptance green. The cached house succeeds by demonstrating the conservative boundary. Any actual global-massing approval remains a separate user decision tied to exact hashes.

- [x] **Step 4: Generate and typecheck only when the proposal is actually admitted**

If a hash-bound `global-massing` approval exists, run:

```bash
python3 forge/stage2_spec/validate_sculpt_spec.py proposed-object-sculpt-spec.json --strict-quality
python3 forge/stage3_build/generate_threejs_factory.py proposed-object-sculpt-spec.json --out src/createObjectModel.ts
IMG2THREEJS_SHOWCASE_ROOT=/Users/nicco/.config/superpowers/worktrees/img2threejs-showcase/cartoon-courier python3 forge/tests/test_showcase_tsc_smoke.py
```

If approval is absent, record `influence_not_approved` and stop before factory generation. This is expected fail-closed behavior, not a failed implementation.

- [x] **Step 5: Run the complete offline test suites**

Run:

```bash
python3 -m unittest discover -s forge/tests -v
uv run --project integrations/mesh3d python -m unittest discover -s integrations/mesh3d/tests -v
```

Expected: the existing forge suite and new mesh3d suite pass; documented environment-dependent skips remain explicit; no test performs an external request.

- [x] **Step 6: Run security, runtime, and formatting checks**

Run:

```bash
rg -n --hidden -g '!integrations/mesh3d/.venv/**' -g '!*.lock' '(hf_[A-Za-z0-9]{20,}|Bearer[[:space:]]+[A-Za-z0-9._-]+|token=|authorization)' .
rg -n '(GLTFLoader|\.glb|\.obj|trellis-community|stabilityai/stable-fast-3d)' src integrations/mesh3d/dense_evidence forge/stage1_intake/check_dense_evidence.py forge/stage2_spec/apply_dense_evidence.py
git diff --check
git status --short --branch
```

Expected: no credential match; any `.glb`/`.obj` matches are confined to offline adapter/validator paths, never runtime `src`; no whitespace errors; only intended files plus preserved pre-existing changes are listed.

- [x] **Step 7: Produce a final evidence summary without committing**

Report:

- focused and full test counts/results;
- exact cached-house artifact paths and hashes;
- zero provider calls and zero spend;
- maximum and approved influence scopes;
- whether a proposed spec/factory was generated or correctly blocked;
- unchanged immutable input hashes;
- remaining single-view and proxy-quality limitations;
- complete `git status --short --branch` separating prior edits from this implementation.

## Self-review against the approved specification

- Source image, ObjectSculptSpec, and generated-mesh authority are separated explicitly.
- The extractor is optional-dependency code; `forge/` remains stdlib-only and communicates through JSON.
- Merged GLBs are capped at global evidence; multipart boundaries stay candidates until explicit mapping.
- Influence has exactly `none`, `global-massing`, and `component-measurements` scopes.
- Approval binds the exact GLB, evidence, review, scope, and target-spec hashes.
- Hidden single-view surfaces remain non-authoritative.
- The accepted spec is never overwritten; proposal and delta are reversible.
- The final runtime is code-only and rejects GLB/OBJ loaders or provider artifacts.
- Dual-baseline acceptance prevents a provider match from hiding source-image regression.
- Cache and resume are downstream-only and cannot trigger provider work.
- Automated tests are offline; the cached house is reused without a new upload or generation.
- The image-only and character routes remain backward compatible.
- No commit, push, deployment, destructive cleanup, purchase, refill, or quota-consuming action is part of this plan.

## Execution checkpoint

After approval, execute this plan inline with `superpowers:executing-plans`, then use `superpowers:test-driven-development` for every implementation task, `superpowers:systematic-debugging` for unexpected failures, and `superpowers:verification-before-completion` before reporting success. Pause if the cached house needs a new influence approval; extraction itself remains offline and read-only over existing provider artifacts.

## Execution record — 2026-09-01

All tasks are implemented and verified: forge suite 1169 tests OK (29
environment-dependent skips), mesh3d suite 14 tests OK, security/runtime/
whitespace checks clean. The cached whimsical-house acceptance was completed
end-to-end offline (0 provider calls, $0, immutable inputs hash-verified
unchanged): extraction, validation, hash-bound `global-massing` admission,
reversible proposal, typechecked candidate factory, and the dual-baseline
browser A/B. Final decision: **DENY — retain the image-only baseline**
(`dense-evidence/ab/ab-decision.json`): the candidate improves the declared
evidence target massingSimilarity 0.649 → 0.923 but regresses source
silhouette IoU by 0.0211 (cap 0.02), and the turntable/intersection gates
fail on the generated blockout for both variants alike. The conservative
boundary held exactly as designed; artifacts and metrics are preserved under
the run's `dense-evidence/ab/` directory.
