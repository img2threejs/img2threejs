> Last updated: 2026-09-01 03:00

# TRELLIS Dense-Evidence Integration Specification

## 1. Summary

Connect an admitted TRELLIS or Stable Fast 3D reference mesh to the native img2threejs reconstruction pipeline as an optional measurement instrument. The provider GLB must improve measurement, fitting, and review without becoming the shipped model.

The final deliverable remains a semantic, editable, code-only TypeScript/Three.js factory. The GLB may influence explicitly approved dimensions and profiles only after structural admission, browser review, semantic-confidence checks, and a human-selected influence scope.

This specification extends `free-generative-assist-spec.md`. It does not change its immutable `maxCostUsd = 0`, upload approval, provider allowlist, retry, fallback, caching, credential, or raw-artifact rules.

## 2. Current gap

The repository currently has three separate capabilities:

1. `integrations/mesh3d/free_assist/` obtains, normalizes, caches, and structurally reviews provider GLBs.
2. `forge/` converts image evidence and an `ObjectSculptSpec` into a staged procedural Three.js factory.
3. `integrations/glb_character_pipeline/` can reconstruct a multipart character GLB through cross-sections, SDF sampling, and compact TypeScript encoding, but it is a separate character-specific workflow.

There is no executable generic bridge from a completed `free_assist` run to `ObjectSculptSpec`, parameter fitting, or `generate_threejs_factory.py`. `render_bridge.py` can render a GLB for comparison, but it does not turn that GLB into semantic procedural decisions.

Consequently, the current house acceptance run stops at:

```text
image -> TRELLIS -> GLB/OBJ -> normalization -> admission -> browser comparison
```

The required path is:

```text
image -> TRELLIS -> admitted GLB/OBJ
                       |
                       v
             normalized dense evidence
                       |
                       v
       explicit component/evidence mapping
                       |
                       v
         bounded ObjectSculptSpec proposals
                       |
                       v
     procedural Three.js build and A/B review
```

## 3. Goals

- Use provider output for measurable global massing, proportions, silhouettes, cross-sections, and component dimensions.
- Preserve the source image as the visible-identity and appearance authority.
- Preserve `ObjectSculptSpec` as the semantic authority.
- Produce only procedural TypeScript/Three.js runtime geometry.
- Support generic static objects and buildings in the first implementation.
- Reuse cached `free_assist` GLBs without another provider request.
- Make every GLB-derived change traceable to a source hash, region, measurement, confidence, and spec field.
- Restrict merged single-mesh GLBs to global evidence unless an explicit semantic mapping supplies stronger evidence.
- Compare the evidence-assisted result against the image-only baseline before accepting changes.
- Reject evidence that worsens source-image fidelity or contradicts critical features.
- Keep optional third-party mesh dependencies outside the stdlib-only `forge/` core.

## 4. Non-goals

- Shipping the TRELLIS GLB, OBJ, texture, topology, or a renamed copy at runtime.
- Encoding the full provider mesh as Base64 TypeScript for generic objects.
- Automatically converting arbitrary triangles into a clean semantic component tree.
- Treating inferred back, side, bottom, or occluded surfaces from a single image as ground truth.
- Automatically admitting a provider result because structural checks passed.
- Automatically rewriting handwritten TypeScript.
- Replacing the existing character GLB pipeline.
- Rigging or animation generation in the first generic-object implementation.
- Retopologizing and exporting the TRELLIS mesh as the final asset.
- Additional provider calls, automatic retries, provider switching, purchases, or paid inference.
- Training, fine-tuning, or running TRELLIS locally.

## 5. Considered approaches

### A. Runtime hybrid: GLB base plus semantic Three.js overlays

Keep the provider GLB in the application and add named procedural details around it. This preserves visual density quickly, but it breaks the code-only contract, leaves an opaque merged mesh at the center of the model, and makes editing, interaction, destruction, and rigging inconsistent.

**Decision:** rejected.

### B. Direct mesh post-processing

Run cleanup, decimation, retopology, material repair, and optional rigging directly on the provider mesh. This can produce a better conventional asset, but the output remains provider topology rather than a semantic img2threejs factory. It is a valid future export mode, not the native-pipeline integration requested here.

**Decision:** rejected for this feature.

### C. Dense evidence to semantic procedural fitting

Extract bounded geometric evidence from the admitted GLB, map only reliable regions to existing semantic component IDs, propose spec-level parameter changes, regenerate the procedural factory, and accept changes only through A/B review gates.

**Decision:** selected. It raises the measurement ceiling while preserving editability, provenance, code-only output, and the existing pass system.

## 6. Scope and routing

### 6.1 First implementation

The first implementation supports static `object` and `hybrid` subjects, including buildings, props, furniture, vehicles without articulated rigs, stylized environment pieces, and mechanical objects whose component tree can be authored from image evidence.

### 6.2 Characters

Character requests continue to use the existing image-driven character profile or the separately invoked `integrations/glb_character_pipeline/`. This specification may reuse its measurement ideas, but must not silently route a character into that integration.

### 6.3 Route decision

The route is explicit:

- no admitted GLB: use the normal image-only `forge/` pipeline;
- admitted merged GLB: allow `global-massing` evidence only;
- admitted multipart or explicitly mapped GLB: allow reviewed `component-measurements` evidence;
- character GLB: request the existing character-specific route or continue image-only;
- rejected or visually unreviewed GLB: preserve it as an artifact but allow no influence.

Availability or confidence must never start evidence extraction, spec mutation, or code generation automatically.

## 7. Authority model

The pipeline uses three authorities with non-overlapping responsibilities:

1. **Source image authority**
   - visible silhouette, visible feature placement, color, material appearance, lighting evidence, and identity-defining details;
   - overrides the provider mesh when the two disagree in the observed view.
2. **ObjectSculptSpec authority**
   - component IDs, hierarchy, topology classes, pivots, sockets, interactions, repetition systems, materials, and build-pass order;
   - cannot be invented from an unlabeled merged mesh.
3. **Generated-mesh evidence authority**
   - admitted global bounds, coarse occupancy, cross-sections, relative depths, principal directions, and explicitly mapped component measurements;
   - never owns semantics, appearance, hidden-surface truth, or runtime topology.

Every conflict resolves in that order. A generated mesh is evidence, not a vote with equal authority.

## 8. Architecture

Optional mesh dependencies remain under `integrations/mesh3d/`. The bridge emits versioned JSON and review images that the stdlib-only core can validate and consume.

```text
free_assist completed run
  raw/reference.glb
  normalized/reference.glb
  normalized/reference.obj
  review/admission.json
  review/visual-review.json
             |
             v
integrations/mesh3d/dense_evidence/
  validate admission and hashes
  align GLB to source camera/object frame
  extract global measurements
  extract candidate regions when justified
  emit dense-evidence.v1.json
             |
             v
forge/stage1_intake/check_dense_evidence.py
  stdlib schema, provenance, scope, and confidence gate
             |
             v
explicit component-evidence-map.json
             |
             v
forge/stage2_spec/apply_dense_evidence.py
  emit proposed-object-sculpt-spec.json
  emit spec-delta.json
             |
             v
existing strict validation and locked build passes
             |
             v
procedural TypeScript factory
             |
             v
dual-baseline browser comparison
  source image vs procedural render
  provider GLB vs procedural geometry passes
             |
             v
accept proposal or retain prior spec
```

The extractor and the core consumer communicate only through files. `forge/` must not import `trimesh`, SciPy, NumPy, Gradio, Hugging Face, or provider adapters.

## 9. Proposed modules

### 9.1 Optional integration modules

- `integrations/mesh3d/dense_evidence/model.py` — evidence records, enums, and serialization.
- `integrations/mesh3d/dense_evidence/extract.py` — GLB/OBJ geometry sampling and measurement extraction.
- `integrations/mesh3d/dense_evidence/alignment.py` — object frame, source-camera, scale, chirality, and orientation alignment.
- `integrations/mesh3d/dense_evidence/regions.py` — connected-component inventory and explicit semantic-map application.
- `integrations/mesh3d/dense_evidence/cli.py` — offline `extract`, `propose-scope`, and `verify-cache` commands.

These modules may depend on the existing mesh3d environment. They must never call a provider.

### 9.2 Core modules

- `forge/stage1_intake/check_dense_evidence.py` — pure-stdlib schema and admission validation.
- `forge/stage2_spec/new_component_evidence_map.py` — seeds an explicit mapping against existing spec component IDs without claiming automatic semantics.
- `forge/stage2_spec/apply_dense_evidence.py` — produces a new proposed spec and a machine-readable delta; never mutates the accepted spec by default.
- `forge/stage4_review/compare_dense_evidence.py` — evaluates geometry passes against the GLB baseline and visible passes against the source image.
- `forge/stage4_review/admit_dense_influence.py` — records the human-approved influence scope and review evidence.

### 9.3 Existing modules reused

- `forge/stage1_intake/probe_glb.py`
- `forge/stage3_build/generate_threejs_factory.py`
- `forge/stage3_build/orchestrate_passes.py`
- `forge/stage4_review/render_bridge.py`
- `forge/stage4_review/compare_region_passes.py`
- `forge/stage4_review/fit_params.py`
- `forge/stage4_review/append_review.py`
- `forge/stage4_review/turntable_gate.py`
- `forge/stage4_review/make_comparison_sheet.py`

## 10. Evidence contract

The extractor writes `dense-evidence.v1.json` with:

```json
{
  "schemaVersion": 1,
  "kind": "generated-mesh-dense-evidence",
  "provenance": {
    "providerRun": "runs/<run-id>",
    "providerId": "hf-zerogpu-trellis",
    "sourceImageSha256": "64-lowercase-hex-characters",
    "glbSha256": "64-lowercase-hex-characters",
    "extractorVersion": "dense-evidence-extractor-v1"
  },
  "admission": {
    "structuralStatus": "pass",
    "visualReviewStatus": "reviewed",
    "approvedInfluenceScope": "global-massing"
  },
  "frame": {
    "upAxis": "+Y",
    "forwardAxis": "+Z",
    "handedness": "right",
    "sourceViewTransform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    "confidence": 0.91
  },
  "globalGeometry": {
    "bounds": {},
    "principalAxes": [],
    "occupancyGrid": {},
    "crossSections": [],
    "silhouetteViews": []
  },
  "regions": [],
  "uncertainty": {
    "observedViews": [],
    "hiddenSurfacePolicy": "non-authoritative",
    "warnings": []
  }
}
```

The contract stores bounded measurements, not a full vertex/index copy. Occupancy is capped at `32^3` cells for global fitting in version 1. Cross-sections are capped and resampled to stable point counts. Large dense captures remain local intermediate files and cannot enter the generated factory.

## 11. Semantic mapping

### 11.1 Merged GLBs

A one-node, one-mesh, one-primitive GLB has no reliable semantic boundaries. It may provide only:

- global bounds and aspect ratios;
- whole-object occupancy and silhouette;
- coarse cross-sections;
- source-view depth ordering with explicit monocular uncertainty.

It must not claim that a triangle cluster is a roof, tower, window, wheel, limb, or other semantic part.

### 11.2 Multipart GLBs

Named nodes, separate primitives, materials, and disconnected components are candidate boundaries, not automatic labels. Component-level influence requires a `component-evidence-map.json` entry linking:

- one existing `ObjectSculptSpec` component ID;
- one or more GLB node, primitive, material, or reviewed region IDs;
- mapping method and evidence;
- confidence;
- permitted measurement fields;
- observed and hidden-view limitations.

### 11.3 Explicit mapping gate

The mapping validator rejects:

- unknown spec component IDs;
- overlapping exclusive mappings;
- mappings derived only from texture color where geometry is claimed;
- component confidence below `0.80`;
- hidden-surface measurements from single-view generation;
- any mapping whose source GLB hash differs from the admitted run.

## 12. Influence scopes

Influence is deny-by-default and has exactly three scopes:

1. `none`
   - evidence may be rendered and inspected but cannot change the spec.
2. `global-massing`
   - may propose object-level width, height, depth, center, orientation, global profile, and coarse silhouette parameters;
   - cannot add, delete, rename, or reshape semantic components independently.
3. `component-measurements`
   - includes global massing;
   - may propose allowlisted dimensions and geometry-descriptor parameters for explicitly mapped components;
   - cannot change hierarchy, topology class, materials, pivots, sockets, or interactions without the normal spec review.

There is no `full-mesh`, `copy-topology`, or `automatic` scope.

`admit_dense_influence.py` requires the exact GLB hash, evidence hash, visual-review report, selected scope, and target spec hash. Approval applies only to that tuple and becomes invalid when any member changes.

## 13. Alignment and measurement

Before measurements can influence the spec, the adapter must establish:

- valid glTF right-handed coordinates and declared scale;
- project coordinate convention: Y up, forward +Z;
- chirality through asymmetric visible landmarks when available;
- normalized object center and bounds;
- source-view camera alignment against the original image;
- foreground silhouette agreement;
- evidence for any axis swap, reflection, or rotation.

Alignment may use source-image silhouette and a browser-rendered GLB. It must never choose an orientation solely because it produces a numerically smaller bound.

Initial admission thresholds are versioned configuration, not scattered constants:

- source-view whole-object silhouette IoU at least `0.65` for `global-massing`;
- source-view silhouette IoU at least `0.75` for `component-measurements`;
- projected aspect-ratio error at most `15%`;
- mapping confidence at least `0.80` per influenced component;
- finite, non-degenerate dimensions and consistent GLB/OBJ axes.

Threshold failure reduces the proposed scope or returns `DENY`; it never silently relaxes a threshold.

## 14. Spec proposal and fitting

`apply_dense_evidence.py` reads an already valid spec and emits:

- `proposed-object-sculpt-spec.json`;
- `spec-delta.json` containing old value, proposed value, measurement, confidence, source region, and reason;
- `fit-plan.json` containing the bounded parameter set and review views.

It does not edit the accepted spec in place.

The fitting loop follows these rules:

- fit global massing before component form;
- fit geometry before materials and lighting;
- change one component or one correction group per iteration;
- use only allowlisted numeric fields and compatible geometry descriptors;
- preserve component hierarchy and topology class;
- cap each numeric delta according to the spec quality contract;
- stop on plateau, oscillation, gate regression, or the normal correction-loop budget;
- regenerate the factory from the proposed spec rather than patching generated TypeScript as the only source of truth.

The provider mesh cannot create a new component. Missing identity-defining components must come from source-image analysis and normal spec authoring.

## 15. Dual-baseline review

The evidence-assisted build must be compared against two different baselines:

### 15.1 Source-image baseline

Authoritative for:

- source-view silhouette;
- visible feature position and scale;
- visible component relationships;
- materials, color, lighting response, and identity.

### 15.2 Provider-GLB baseline

Advisory for:

- coarse three-dimensional massing;
- admitted source-facing cross-sections and relative depth;
- explicitly mapped component geometry;
- orbit-view continuity, with hidden surfaces marked as generated hypotheses.

The candidate is accepted only when:

- no critical source-image feature regresses;
- source-view silhouette IoU decreases by no more than `0.02` from the image-only baseline;
- at least one declared evidence-target metric improves by `0.01` or more;
- turntable, attachment, intersection, strict-quality, and pass-order gates remain clean;
- the change stays within the approved influence scope;
- browser evidence and hashes are recorded.

If the provider mesh and source image disagree, improving agreement with the GLB is not success.

## 16. Hidden surfaces and uncertainty

For a single source image:

- only the visible source-facing region can receive high confidence;
- side confidence is capped at medium unless directly visible;
- rear, bottom, and occluded surfaces are non-authoritative;
- invented hidden details cannot add components or override the spec;
- orbit renders exist to expose provider hallucinations, not validate them as truth.

Multiple independently supplied source views may raise confidence only when their hashes, cameras, and coverage are recorded. Multiple renders generated from the same single-image GLB do not count as independent observations.

## 17. State and artifacts

The bridge adds an evidence directory beside the free-assist run without modifying raw provider artifacts:

```text
runs/<run-id>/
  raw/reference.glb
  normalized/reference.glb
  normalized/reference.obj
  review/admission.json
  review/visual-review.json
  review/preview.png
  dense-evidence/
    extraction-request.json
    dense-evidence.v1.json
    alignment.json
    component-evidence-map.json
    influence-admission.json
    spec-delta.json
    fit-plan.json
    comparison-manifest.json
    comparison-sheet.png
    status.json
```

The normal `.img2threejs/state.json` receives a new optional `dense-evidence-admission` checklist item only when this route is selected. Image-only projects do not acquire a new mandatory step.

## 18. Cache and resumability

The dense-evidence cache key contains:

- normalized GLB SHA-256;
- normalized OBJ SHA-256;
- source image SHA-256 values in order;
- extractor version;
- alignment-profile version;
- component-map hash, when present;
- measurement configuration hash.

Extraction and fitting never call the provider. A changed component map invalidates only mapping and downstream proposals, not geometry extraction. A failed local fit resumes from persisted evidence and never regenerates the GLB.

## 19. Failure handling

Machine-readable failure categories include:

- `provider_run_incomplete`
- `mesh_not_structurally_admitted`
- `visual_review_missing`
- `influence_not_approved`
- `evidence_hash_mismatch`
- `alignment_failed`
- `chirality_ambiguous`
- `semantic_boundary_insufficient`
- `component_mapping_invalid`
- `influence_scope_exceeded`
- `fit_no_improvement`
- `source_fidelity_regression`
- `strict_quality_failed`
- `browser_evidence_missing`

Every failure preserves the cached GLB, extracted evidence, previous accepted spec, proposed spec, and comparison artifacts. The image-only path remains available.

## 20. Security, cost, and privacy

- The downstream bridge is offline and has no provider client.
- It cannot upload images or meshes.
- It cannot consume ZeroGPU quota.
- It accepts no API token or credential argument.
- It inherits the immutable `maxCostUsd = 0` provider contract.
- Reports store hashes and local artifact references, never tokens or signed provider URLs.
- Raw provider GLBs remain immutable.
- Generated evidence is local and is not committed by default.

## 21. Testing strategy

### 21.1 Unit tests

- evidence schema accepts valid records and rejects missing provenance;
- merged one-mesh GLBs cannot receive component scope;
- multipart boundaries remain candidates until explicitly mapped;
- hash drift invalidates admission;
- hidden surfaces from a single image remain non-authoritative;
- scope validation blocks forbidden spec fields;
- spec proposals are non-mutating and produce reversible deltas;
- cache keys change for every authoritative input.

### 21.2 Offline integration tests

- cached free-assist fixture to global evidence without network access;
- admitted multipart fixture to mapped component measurements;
- invalid, mirrored, degenerate, over-budget, and visually rejected fixtures fail closed;
- proposed spec passes existing validation before factory generation;
- image-only pipeline output remains unchanged;
- no test imports or constructs a provider adapter.

### 21.3 Runtime tests

- generate and typecheck the procedural factory in the companion showcase;
- verify no `GLTFLoader`, `.glb`, `.obj`, binary fetch, or provider URL exists in runtime source;
- capture source-view and turntable renders through the real Three.js route;
- compare the image-only baseline, provider reference, and evidence-assisted candidate;
- verify that rejected proposals leave the accepted spec and factory unchanged.

### 21.4 Live acceptance

Use the already cached whimsical-house TRELLIS run. No new upload or generation is allowed for the first bridge acceptance.

Because that GLB is one merged mesh and its current review retains it as a proxy only, the initial acceptance may exercise extraction and `global-massing` proposal generation but must not approve component influence without a new explicit review decision. The test succeeds when the bridge respects this limitation; it does not need to force a visual improvement from unsuitable evidence.

## 22. Implementation phases

### Phase 1 — Contracts and global evidence

- add schemas, extractor, alignment, cache, core validator, and `global-massing` scope;
- use merged GLB fixtures and the cached house run;
- do not mutate specs or generate code yet.

### Phase 2 — Reversible spec proposals

- add spec-delta generation and bounded global fitting;
- generate a proposed factory through existing strict and pass gates;
- retain the image-only factory as A/B baseline.

### Phase 3 — Explicit component mapping

- support multipart and reviewed region mappings;
- add `component-measurements` scope and per-component fitting;
- keep automatic semantic labeling outside scope.

### Phase 4 — Dual-baseline browser acceptance

- add comparison manifests, source/GLB geometry passes, regression gates, and influence admission;
- complete the cached-house acceptance without any provider call.

Character generalization, rigging, and direct mesh-export modes require separate specifications.

## 23. Acceptance criteria

The feature is complete only when all of the following are true:

- A completed cached `free_assist` run can produce versioned dense evidence without network access.
- The raw and normalized provider artifacts are not modified.
- Merged GLBs are limited to global evidence.
- Component-level influence requires an exact reviewed mapping and confidence gate.
- Influence approval is bound to GLB, evidence, review, scope, and target-spec hashes.
- The accepted `ObjectSculptSpec` is never overwritten by default.
- Every proposed field change has provenance and a reversible delta.
- Existing strict-quality and locked-pass gates remain authoritative.
- The final runtime contains no provider GLB, OBJ, texture, loader, or URL.
- The generated TypeScript factory remains semantic and editable.
- A candidate cannot pass by matching the provider while regressing the source image.
- Hidden surfaces generated from one image remain explicitly non-authoritative.
- Cache and resume avoid repeated extraction, fitting, and provider generation.
- Automated tests perform no external calls and consume no quota.
- The existing image-only and GLB-character routes remain backward compatible.
- The cached whimsical-house run demonstrates the offline bridge boundary and conservative scope behavior.

## 24. Explicit approval boundary

Approval of this specification authorizes only creation of an implementation plan. It does not authorize code changes, provider calls, new uploads, quota consumption, modification of accepted showcase models, commits, pushes, or deployment.
