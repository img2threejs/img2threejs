> Last updated: 2026-09-01 03:30

# TRELLIS dense-evidence bridge

This optional bridge turns an already cached and reviewed TRELLIS or Stable Fast 3D mesh into
bounded geometric evidence for the native img2threejs pipeline. The source image is authoritative;
the generated mesh is advisory. The final artifact remains a semantic, editable, code-only
TypeScript/Three.js factory.

The bridge is offline and preserves the free-assist policy `maxCostUsd = 0`. Extraction makes no provider call,
performs no upload, consumes no quota, accepts no token argument, and never retries
or switches providers. The normalized GLB/OBJ and their reviews are immutable inputs. They are
never shipped at runtime and are never copied into procedural source code.

## Authority and scope

Influence is deny-by-default and has exactly three levels:

- `none`: the extractor's output; no procedural parameter may change.
- `global-massing`: bounded whole-object dimensions and component positions may be proposed.
- `component-measurements`: only explicitly mapped, observed component dimensions may be proposed.

A merged one-mesh/one-primitive asset is capped at `global-massing`. Multipart node and primitive
boundaries remain candidate selectors; names, materials, colors, connected components, and provider
metadata never become semantic labels automatically. `component-measurements` requires a
human-reviewed component map with confidence at least 0.80. Hidden and rear surfaces from a single
view remain non-authoritative.

The accepted ObjectSculptSpec is never overwritten. The bridge emits
`proposed-object-sculpt-spec.json`, `spec-delta.json`, and `fit-plan.json`; the reverse delta must
restore the accepted spec exactly. Version 1 cannot modify IDs, hierarchy, topology, primitive
types, geometry descriptors, materials, pivots, sockets, attachments, repetition systems,
interactions, or build passes.

## Pipeline order

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

The A/B gate requires all existing deterministic geometry gates, no critical source-feature
regression, at most `0.02` source silhouette regression, and at least `0.01` improvement against a
declared evidence metric. A better match to the advisory GLB cannot hide a worse match to the source
image.

## Reviewed alignment

Extraction does not solve a camera or guess chirality. Supply an alignment JSON with a finite
16-number `sourceViewTransform`, `+Y` up, `+Z` forward, right-handed output, an axis-operation audit,
`chiralityStatus`, browser-capture paths and hashes, silhouette IoU, and projected aspect-ratio
error. Global use requires IoU at least `0.65`; component use requires at least `0.75`; aspect-ratio
error must not exceed `0.15`. Ambiguous chirality caps influence at `global-massing`.

## Offline commands

Install only the isolated optional mesh environment:

```bash
uv sync --project integrations/mesh3d
```

Inspect the maximum possible scope without writing an approval:

```bash
uv run --project integrations/mesh3d python -m integrations.mesh3d.dense_evidence propose-scope \
  --run RUN \
  --source-image reference.png \
  --alignment reviewed-alignment.json
```

Extract or reuse the content-addressed cache:

```bash
uv run --project integrations/mesh3d python -m integrations.mesh3d.dense_evidence extract \
  --run RUN \
  --source-image reference.png \
  --alignment reviewed-alignment.json \
  --out-dir RUN/dense-evidence

uv run --project integrations/mesh3d python -m integrations.mesh3d.dense_evidence verify-cache \
  --run RUN \
  --source-image reference.png \
  --alignment reviewed-alignment.json \
  --out-dir RUN/dense-evidence
```

Validate through the stdlib core:

```bash
python3 forge/stage1_intake/check_dense_evidence.py \
  --evidence RUN/dense-evidence/dense-evidence.v1.json \
  --spec object-sculpt-spec.json \
  --out dense-evidence-validation.json
```

For multipart evidence, seed an empty map and fill it only after review:

```bash
python3 forge/stage2_spec/new_component_evidence_map.py \
  --spec object-sculpt-spec.json \
  --evidence RUN/dense-evidence/dense-evidence.v1.json \
  --out component-evidence-map.json
```

Create a hash-bound approval only after the user approves this exact tuple:

```bash
python3 forge/stage4_review/admit_dense_influence.py \
  --evidence RUN/dense-evidence/dense-evidence.v1.json \
  --visual-review RUN/review/visual-review.json \
  --target-spec object-sculpt-spec.json \
  --scope global-massing \
  --approve-influence \
  --out influence-admission.json
```

Omitting `--approve-influence` returns `NEEDS_USER_ACTION` and writes no ALLOW record. A changed
GLB, evidence file, review, requested scope, component map, or target spec invalidates the approval.

Generate the reversible proposal, never in place:

```bash
python3 forge/stage2_spec/apply_dense_evidence.py \
  --spec object-sculpt-spec.json \
  --evidence RUN/dense-evidence/dense-evidence.v1.json \
  --admission influence-admission.json \
  --out proposed-object-sculpt-spec.json \
  --delta-out spec-delta.json \
  --fit-plan-out fit-plan.json
```

Then run the existing strict validator, factory generator, browser captures, and
`compare_dense_evidence.py`. Runtime scanning rejects GLB/OBJ loaders, mesh asset paths, provider
URLs, signed URLs, and copied mesh payload markers.

## Cache, resume, and artifacts

`dense-evidence/` contains `extraction-request.json`, `alignment.json`,
`dense-evidence.v1.json`, and `status.json`. The cache identity binds normalized GLB/OBJ hashes,
ordered source-image hashes, extractor/alignment versions, exact alignment, and measurement caps.
A component-map change invalidates mapping/proposal work without recomputing base geometry.

Local extraction failures record a normalized category and last durable artifact. Re-running resumes
from the existing normalized GLB/OBJ; it can never trigger generation. A cache hit is returned only
after authoritative hashes are recomputed.

Exit codes are `0` for pass/cache hit/accepted, `1` for a valid policy or quality `DENY`, `2` for
malformed input or local execution failure, and `3` when explicit approval or browser evidence is
required. Common categories include `evidence_hash_mismatch`, `alignment_failed`,
`semantic_boundary_insufficient`, `component_mapping_invalid`, `influence_scope_exceeded`,
`source_fidelity_regression`, `fit_no_improvement`, and `browser_evidence_missing`.

This route is optional and generic-object-only in version 1. It does not replace the image-only,
character, or CS2 pipelines, and TRELLIS does not generate the final procedural factory.
