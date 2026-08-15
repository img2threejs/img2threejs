# Scripts Cheatsheet

All scripts are pure Python 3.10+ **standard library** — no pip install, no PIL/numpy, no
Playwright/Chromium. PNG read/write is done via `struct`/`zlib`. Run from the skill root so
paths resolve as `forge/<name>.py`. Non-zero exit = a gate failed; read the printed reasons.

Division of labor: **scripts enforce structure and package evidence; they never score visuals.**
The acceptance score always comes from the agent's own vision inspecting the comparison sheet.

## Input and evidence hardening

- PNG and baseline 8-bit JPEG references are decoded in-process by the stdlib core. Unsupported
  progressive/12-bit/CMYK JPEGs must take an explicit external-converter fallback; never guess at
  pixels.
- `diagnose_render.py` and `divine_eye.py` treat tiny/inverted foreground masks and empty unions as
  unusable evidence. Re-capture the reference/render with the subject filling the frame.
- `vlm_gate.py --samples` requires a non-empty JSON list whose entries are objects. `analyze_texture.py`
  requires `--spec` and `--material-id` together, and `--in-place` requires both.
## state.py and next.py

- `state.py init --state .img2threejs/state.json --reference IMG [--profile generic|cs2|character]`
  creates the local mandatory checklist. It refuses to overwrite existing state.
- `state.py status --state .img2threejs/state.json [--json]` reports the current step and loop limits.
- `state.py mark STEP... --state .img2threejs/state.json --evidence PATH` records completed evidence.
  Use `--status skipped --reason "..."` only when a step is genuinely not applicable.
- `next.py --state .img2threejs/state.json [spec.json]` is the mandatory start/resume gate. It
  derives correction counts from `reviewHistory` and exits 3 at the per-pass or total hard ceiling.

Defaults are 3 `refine-spec`/`refine-code` decisions per pass and 6 total. These are safety limits,
not targets; stop earlier on success, repeated defects, oscillation, or plateau.

The pass checklist is executable in dependency order: generate, render, Tier 1, multi-angle,
`orchestrate_passes.py check`, profile-specific review, AI review, then sync. The CS2 profile runs:

`stage4_review/cs2_review.py --manifest cs2-intake.json --metrics cs2-review-inputs.json --scene forge/tests/fixtures/knife_review_scene.json --out cs2-review.json`

The character profile requires the reconstruction/likeness contracts, landmark evidence, and an
explicit stylized-versus-projection route decision before pre-spec authoring.
Every profile also records a reference-suitability verdict, a projection-route decision, and a
material/PBR evidence decision. A non-applicable conditional gate must be skipped with a reason.

## stage1_intake/probe_image.py
`stage1_intake/probe_image.py <image>` — image type, dimensions, aspect ratio, obvious technical
issues. Metadata only; not a substitute for visual inspection.

## stage2_spec/new_pre_spec_assessment.py
`stage2_spec/new_pre_spec_assessment.py "Name" [--image IMG] [--complexity simple|moderate|complex|ultra-complex] --out assessment.json [--force]`
Emits a pre-spec assessment + `qualityContract` skeleton. Refine `--complexity` after looking at
the image. See `intake/quality_contract.md` for the scoring axes and contract checklist.

## stage2_spec/new_sculpt_spec.py
`stage2_spec/new_sculpt_spec.py "Name" [--image IMG] [--assessment assessment.json] --out object-sculpt-spec.json [--force]`
Starter `ObjectSculptSpec` (schema 2.0). With `--assessment` it seeds from the completed gate.
Always replace generic starter `featureReviewTargets` with real identity-defining systems.

## stage2_spec/validate_sculpt_spec.py
`stage2_spec/validate_sculpt_spec.py spec.json [--json] [--strict-quality]`
Normal: checks required fields, score ranges, material refs, component IDs, parent links,
transforms, primitive names (warnings allowed). `--strict-quality`: promotes quality warnings to
errors — blocks code gen when the spec is too shallow for its contract (min macro/meso/micro
counts, material layers, repetition systems, review viewpoints, non-generic feature targets,
material-pass locality, lighting-pass real lights). Fix per `intake/quality_contract.md`.

## stage3_build/orchestrate_passes.py
- `status spec.json` — current unlocked pass + required evidence.
- `check spec.json --pass-id <pass>` — non-zero unless that pass is unlocked or already done.
- `sync spec.json --in-place` — recompute `sculptPipeline` from `reviewHistory`.

Ordered passes: `blockout → structural-pass → form-refinement → material-pass → lighting-pass →
interaction-pass → optimization-pass`. A pass unlocks only after the prior pass has a review with
`action=continue` backed by a render screenshot, a comparison sheet, a global AI-vision score ≥
threshold (default 0.7), and every critical feature ≥ its own threshold.

## stage3_build/generate_threejs_factory.py
`stage3_build/generate_threejs_factory.py spec.json --out src/createObjectModel.ts [--pass-id PASS] [--force]`
First enforces `strict-quality`; if that gate fails it prints a machine-readable `BLOCKED` report,
optionally writes it with `--blocked-report`, and does not create or overwrite the factory. It emits
a TypeScript Three.js `Group` factory for the **current unlocked pass only**. Passing a
future `--pass-id` fails until earlier passes are reviewed `continue`. Output exposes
`root.userData.sculptRuntime` (nodes/meshes/sockets/colliders/destructionGroups) — hand-refine it.
`--allow-nonstrict` is reserved for legacy test fixtures and must not be used for production output.

## Forge subdivision runtime validation
Runtime subdivision tests compile generated TypeScript against `img2threejs-showcase`. Set
`IMG2THREEJS_SHOWCASE_ROOT` to that checkout. Without it, runtime-only cases skip locally with an
actionable message while static contracts continue; set `IMG2THREEJS_REQUIRE_SHOWCASE=1` in CI to
fail when the checkout is unavailable. Forge showcase tests share this resolver, including visual-hull
runtime and smoke coverage.

```bash
IMG2THREEJS_SHOWCASE_ROOT=/path/to/img2threejs-showcase python3 forge/tests/test_subdivision.py
IMG2THREEJS_SHOWCASE_ROOT=/path/to/img2threejs-showcase python3 -m unittest discover -s forge/tests
IMG2THREEJS_SHOWCASE_ROOT=/path/to/img2threejs-showcase python3 forge/tests/test_showcase_tsc_smoke.py
```

## Triangle budget: tessellation tiers and decimation

`performanceBudget.targetTriangles` picks a tessellation tier for every primitive that has
segment counts, and caps implicit-surface sampling grids:

| targetTriangles | tier | sphere | cylinder | SDF grid ceiling |
|---|---|---|---|---|
| ≤ 6,000 | `low` | 16×10 | 10×4 | 24 |
| ≤ 60,000 | `standard` | 32×20 | 24×8 | 40 |
| otherwise / absent | `hero` | 64×40 | 48×16 | 64 |

`hero` IS the pre-tier constants, so a spec without a budget generates byte-identical output.
Height segments never drop below 4 and cone height segments are pinned at 1 — the first
because a single quad across a joint leaves no vertex at the pivot and the joint collapses
(`emit_rig.py:399` derives the same floor), the second because a tapering cone only welds
cleanly at 1. `validate_tier()` raises rather than letting a tier violate either.

When a tier is not precise enough — an SDF's grid is quantised, so it can only get near a
number — decimate that component:

```json
"geometryDescriptor": { "decimate": { "targetRatio": 0.4 } }
```

Emits a Garland-Heckbert quadric collapse into the generated factory, refusing collapses that
would flip a face or erode a boundary edge. It runs **before** skin binding: the bind pass
recomputes weights from `position`, so weights land on the surviving vertices and no
skinIndex/skinWeight is interpolated across a vertex merge. It keeps `position` only and
recomputes normals, so it is refused on an authored/unwrapped `uvStrategy`.

Measured on the implicit fixture: 856 → 342 triangles at 0.4. On a rigged humanoid at 0.5:
828 → 414 triangles, 49 bones and 5 SkinnedMesh intact, every vertex's four skin weights
still summing to 1.0.

For offline LOD tiers from an exported mesh, the same algorithm:

```bash
python3 forge/stage3_build/decimate.py meshes.json --ratio 0.5 --json
```

## Visual-hull descriptor

`geometryDescriptor.visualHull` is an opt-in deterministic orthographic carving path. It requires
`boundsSpace: "component-local"`; bounded `min`/`max` local extents are created before the existing
component pivot applies its `transform`, plus a voxel `resolution` from 4 to 32, a triangle budget, and
at least two distinct `front`, `side`, or `top` binary silhouettes. Each view carries a 0 to 1
confidence value; generated geometry records every unobserved region as low-confidence metadata. A
valid descriptor whose silhouettes intersect to no voxels throws `VisualHullOccupancyError` at runtime
instead of silently returning an empty geometry.

```json
{
  "visualHull": {
    "projection": "orthographic",
    "boundsSpace": "component-local",
    "bounds": { "min": [-1, -1, -1], "max": [1, 1, 1] },
    "resolution": 16,
    "triangleBudget": 50000,
    "views": [
      { "axis": "front", "confidence": 0.94, "mask": ["0110", "1111", "1111", "0110"] },
      { "axis": "side", "confidence": 0.91, "mask": ["0110", "1111", "1111", "0110"] }
    ]
  }
}
```

```bash
IMG2THREEJS_SHOWCASE_ROOT=/path/to/img2threejs-showcase python3 forge/tests/test_visual_hull.py
```
Use `--force` for the next pass only after preserving valid hand refinements in the spec.
Do not regenerate after `refine-code`; edit the existing artifact. Regenerate with `--force` after
`refine-spec` or when advancing to a new pass.

## stage4_review/make_comparison_sheet.py
`stage4_review/make_comparison_sheet.py --reference IMG --render SHOT --out cmp.png [--panel-width N] [--panel-height N] [--gutter N] [--json]`
Aligns + packages one side-by-side sheet. It does **not** compute an acceptance score — inspect
`cmp.png` with agent vision and write the score back via `stage4_review/append_review.py`.

## stage4_review/append_review.py
`stage4_review/append_review.py spec.json --pass-id PASS --fidelity 0-1 --action continue|refine-spec|refine-code|request-input|stop --summary "..." [evidence flags] --in-place`
Evidence flags: `--matched --mismatches --spec-fixes --code-fixes --evidence --reference-screenshot
--render-screenshot --comparison-image --ai-vision-score 0-1 --layer-scores-json '{...}'
--feature-reviews-json f.json --ai-vision-notes "..." --visual-threshold 0-1 --camera-view NAME
--require-screenshot-files`. Layer keys: `silhouetteProportion, componentStructure, formDetail,
materialSurface, lightingCamera`. Records one self-correction entry into `reviewHistory`.

## GLB-mediated v2 render profile

`stage4_review/validate_render_profile.py docs/specs/render-profile.v2.example.json`
validates the shared browser renderer/camera/environment contract. Use it when initializing
the GLB-mediated route:

`stage4_review/render_bridge.py init --reference-glb GLB --render-profile PROFILE --runtime-url URL --out render-manifest.json`

Record each pass with `stage4_review/render_bridge.py record-pass --manifest MANIFEST
--capture-id hero --pass-id semantic-id --image semantic-id.png [--reference]`. Required passes
are `beauty`, `alpha-silhouette`, `semantic-id`, `depth`, `normal`, and
`roughness-material-id`. Compare paired browser evidence with
`stage4_review/compare_region_passes.py --manifest MANIFEST --capture-id hero --out comparison.json`.

## stage1_intake/extract_pbr_evidence.py
`stage1_intake/extract_pbr_evidence.py <crop> --out-dir DIR --material-id ID [--target-threshold 0.7] [--size N]
[--palette-size N] [--spec spec.json --in-place | --out-spec p.json] [--report r.json]
[--allow-low-confidence] [--multi-view-reference]`
Extracts reference-derived evidence: albedo palette, de-lit albedo, roughness estimate, height,
normal, AO. **Inference, not inverse rendering** — pixels include baked lighting. Exits non-zero
and refuses to patch the spec when confidence < `--target-threshold` (default 0.7) unless
`--allow-low-confidence`. Treat sub-threshold as `request-input`/`refine-spec`, not a pass.

## _shared/feature_acceptance_policy.py
Internal helper imported by the orchestrator/validator (`feature_gate_failures`,
`feature_review_policy`). Enforces the ≤5 critical / ≤3 important feature-tier policy. Not a CLI.

## Character geometry pipeline

All analytic — no trained model, no weights. Each replaces a capability the pipeline named but never
implemented, or supplies one it never had.

### stage3_build/visual_hull.py
`stage3_build/visual_hull.py descriptor.json [--out mesh.json] [--json]`
`carve_visual_hull()` intersects the silhouette cones on a voxel grid and emits only the faces between
solid and empty, so the result is a closed surface rather than a box soup with interior walls. Read
`occupiedVoxelCount` before trusting a mesh: survival requires foreground in EVERY view, so one bad
mask erases the model rather than degrading it. `unconstrainedAxes` names the direction a two-view
hull extrudes along, and a hull can never contain a concavity no supplied view sees as background.

### stage3_build/uv_unwrap.py
`stage3_build/uv_unwrap.py mesh.json [--angle DEG] [--out uv.json] [--json]`
Chart segmentation by normal similarity (growth compared against the SEED, so a chart cannot creep
around a cylinder one tolerable step at a time), LSCM solved by conjugate gradient, skyline packing.
**Read `areaDistortionMedian` and `areaDistortionP95`, not the max** — sweeping the threshold on a real
skull gave 2299 / 93609 / 6595 / 378 / 15.85, which is one sliver chart dominating a maximum, not a
trend. Non-disk charts are cut, not merely reported: leaving seven in place drove distortion to 171300
with twelve inverted triangles. Vertices in `seamVertices` carry more than one UV and must be
duplicated before a bake.

### stage5_rig/geodesic_skinning.py
`stage5_rig/geodesic_skinning.py mesh.json --bones bones.json [--resolution N] [--json]`
Distance measured THROUGH the solid, not in a straight line. On an arm-beside-torso fixture the field
correctly reads 1.37 units to the spine and 4.45 to the arm; the residual cross-talk after that is set
by `DEFAULT_FALLOFF_POWER` (power 2 leaves 8.6%, power 3 leaves 2.8%, power 4 leaves 0.9%) and not by
the distance field. `euclidean_bind` is kept so the difference can be measured rather than asserted.

### stage4_review/joint_loops.py
`stage4_review/joint_loops.py meshes.json --bones bones.json [--min-loops N] [--json]`
Counts distinct vertex BANDS along the bone axis near each joint. Bands, not vertices: ten thousand
vertices in two rings still cannot bend, and a vertex count calls that mesh dense. The window is axial,
not a sphere, because a limb's thickness has nothing to do with whether its joint can bend.

### stage4_review/pairwise_penetration.py
`stage4_review/pairwise_penetration.py meshes.json [--allow nameA,nameB]... [--json]`
Ray parity across meshes. Samples vertices, edge midpoints and face centroids — vertices alone miss a
bar driven through a block, where every corner of each is outside the other. Still sampling, not exact
intersection; `samplingLimitation` says so. Use `--allow` for parts meant to touch.

### stage3_build/morph_targets.py and stage3_build/decimate.py
`morph_targets.py base.json --target pose.json [--out morphs.json]`
`decimate.py mesh.json --ratio 0.5 [--out lod.json]`
Morph targets are RELATIVE deltas; set `morphTargetsRelative = true` in Three.js or every target is
read as an absolute position and the mesh collapses toward the origin. A target with a mismatched
vertex count is refused rather than zip-truncated into a plausible-looking nonsense deformation.
Decimation refuses any collapse that would flip a face or erode a boundary, and reports
`collapsesRefusedForFlip` — stopping short of the target is not a failure, but hitting the number with
a folded surface would be.

## Off-axis and placement gates

Three checks that exist because a review captured only from the reference camera, and scored only by
edge counts, passed a model with a hole through its skull, a hat mounted at hip height, and a charm
floating detached below the ground plane. Each answers a question no earlier gate asked. All three
exit `0` clean / `1` gate failure / `2` error, so they compose in a script.

### stage4_review/self_intersection.py
`stage4_review/self_intersection.py meshes.json [--max-samples N] [--epsilon E] [--json]`
Ray-parity test for a surface that has folded through its own volume. `geometry_integrity.py` counts
only `boundaryEdges` and `nonManifoldEdges`, which are **topological** — pushing existing vertices
through the far side of a mesh changes no connectivity, so a punched-through model reports 0 and 0 and
passes. This is the geometric check that can see it. Reports `sampledVertexCount` /
`totalVertexCount` / `samplingStride`: read them, because a clean verdict over a strided sample is a
weaker claim than a clean verdict over the whole mesh. `undecided` samples (grazing rays) are counted
separately and never folded into either answer.

Input is the same mesh shape `geometry_integrity.py` accepts. Produce it from a live scene with
`runtime/scripts/export_mesh_geometry.mjs` (below).

`measure_geometry_integrity` calls this automatically for every mesh that supplies `vertices` and
`indices`, reporting a `selfIntersection` block per mesh and raising a `self-intersection` failure.
That call site is deliberate: as a standalone CLI the check only runs when somebody remembers to run
it, and the defect it exists to catch survived eight review rounds precisely because nobody did.

### stage4_review/turntable_gate.py
`stage4_review/turntable_gate.py --capture 0=front.png --capture 90=right.png ... [--required N]... [--collapse-ratio R] [--allow-holes] [--json]`
Two things `diagnose_render_multi_angle.py` does not do. First, **coverage is mandatory**: a missing
required azimuth (default 0/90/180/270) fails the gate rather than going unnoticed, which is the
entire point — defects that exist only off-axis survive any number of front-only review rounds.
Second, **interior-hole detection**: flood-fill the background from the border, and any background
region left unreached is enclosed by the object. A hole through a model barely changes silhouette
AREA, so the collapse check cannot see it; this can. Use `--allow-holes` for a subject that genuinely
has a through-hole at that angle — the hole is still reported, only the verdict changes.

### stage4_review/adaptive_harsh_critic.py

Deterministic controller for independent, pixel-bound scene criticism. It does not call a model or
render pixels. Round 0 starts with six cube-map cells, then default `minimumUniformLevel=1` forces
all six roots to split and requires 24 more real captures even when their centres are clean. Only
after that 6+24 floor does a finding selectively split its cell into four `nextViews`. Angular
coverage can refine without a fixed side count while repeated-defect/plateau/max-round/max-view
policies still terminate every run.

```bash
python3 forge/stage4_review/adaptive_harsh_critic.py init \
  --manifest render-manifest.json --creator-id builder-agent \
  --minimum-uniform-level 1 --out critic-state.json
python3 forge/stage4_review/render_bridge.py schedule-adaptive \
  --manifest render-manifest.json --plan critic-state.json
# Browser-capture every new manifest ID here. The generic render_bridge.py
# record command cannot mint adaptive evidence.
python3 scripts/capture_threejs_playwright.py \
  --manifest render-manifest.json --capture-id ahc-<session>-harsh-front-root
python3 forge/stage4_review/adaptive_harsh_critic.py request \
  --state critic-state.json --out critic-request.json
# A separate critic agent inspects every request.views[] PNG and writes critic-response.json.
python3 forge/stage4_review/adaptive_harsh_critic.py advance \
  --state critic-state.json --request critic-request.json \
  --reviews critic-response.json --in-place
```

`request` first re-runs the fixed front/right/rear/left turntable baseline. It refuses pending,
missing, changed, provenance-free, duplicate-pixel, duplicate-camera, or direction-mismatched
captures. It derives `requestId` from the complete canonical request and writes the digest to
`state.pendingRequest`; therefore the command intentionally updates the state as well as creating
the request. A second request is refused until `advance` successfully consumes the first. `advance`
recomputes that digest and rejects any request mutation before reading the review. It also requires
`critic.id != creator.id`,
one response per requested view, and the actual capture SHA-256 + exact direction on both the view
review and every finding. There is no aggregate pass score: one critical finding blocks the run.
If max rounds/views cannot complete the required uniform level, the state blocks rather than
granting a partial-coverage pass.
Schema and full host-agent contract: `docs/specs/adaptive-harsh-critic.v1.schema.json` and
`grimoire/review/adaptive_harsh_critic.md`.

`render_bridge.py schedule-adaptive --manifest M --plan STATE [--output-dir DIR]` appends the
state's `nextViews` as pending `adaptive-critic` captures. Re-importing an identical plan is
idempotent; reusing a view ID with another direction/session fails. The command validates the full
state plus canonical session/view/cell/round geometry. `DIR` must be canonical and relative, and all
capture/reference/pass paths are resolved beneath `M`'s evidence root before a transactional manifest
update; traversal, absolute, drive-qualified, backslash, and dot-component paths fail closed.
Adaptive records require the
Playwright receipt (`strict ready → exact runtime document → WebGL canvas → scene build digest →
actual camera matrices → encoded and decoded-pixel hashes`). `render_bridge.py record` rejects them,
including a caller-supplied ready-signal string.
The receipt is trusted local-runner attestation, not cryptographic proof; the generic record CLI
cannot mint it. Each init gets a random session-scoped view/path namespace. Exact and near-identical
pixels (alpha-composited visible RGB + demeaned-luma structure, with pHash/mean-color guards) collapse fail closed without
mistaking isoluminant but visibly different colors for the same frame. The first scene build digest,
source reference hash, per-view GLB references, and every round's evidence ledger stay pinned and are
re-opened. Keep the scene build immutable through one adaptive state. A correction starts a new
render manifest and critic state so the fixed turntable and all adaptive captures are from the
corrected build. `advance` only accepts `--in-place`; it has no branch-like `--out` mode.

### stage4_review/attachment_anchor.py
`stage4_review/attachment_anchor.py spec.json [--measured measured.json] [--json]`
Relates a worn or held item to the thing it is worn on or held by. `ANCHOR_DECLARED`,
`ANCHOR_RESOLVES`, `ANCHOR_NOT_ROOT` (the literal shared bug — parenting to root leaves the item's
transform unrelated to its body part), `ANCHOR_NOT_CYCLIC`, and, when `--measured` world positions are
supplied, `ANCHOR_PROXIMITY` against `attachment.maxOffset`. Attachments absent from `measured` are
listed under `unmeasuredAttachments` instead of counting as passes — "0 violations" because the check
never ran is the failure this repository keeps rediscovering. A spec with no attachment metadata
passes cleanly, so existing specs are unaffected.

### runtime/scripts/export_mesh_geometry.mjs
`node runtime/scripts/export_mesh_geometry.mjs --url URL --out meshes.json [--include RE] [--exclude RE] [--max-triangles N] [--ready-flag F] [--viewer-handle H]`
Dumps a running model's meshes as the JSON `self_intersection.py` reads. Vertices are emitted in
**world** space on purpose: a parent's non-uniform scale can fold a mesh through itself even when its
local geometry is fine, and local space would hide exactly that. Normals go through the
inverse-transpose. Every mesh it declines to emit — instanced, over the triangle cap, filtered out —
is listed with its reason, so a short mesh list cannot be mistaken for a clean one.

## Reference comparison and baselines

### stage4_review/mesh_reference_compare.py
`stage4_review/mesh_reference_compare.py REFERENCE.glb CANDIDATE.glb [--bands N] [--json]`
Says **where** a candidate is wrong, band by band, instead of returning one aggregate score. Both
meshes are normalised from the **feet** (lowest point to 0, height to 1) because the ground is a
landmark both subjects share, while the top of the bounding box is whatever pokes up highest — three
earlier attempts banded down from the bbox top and measured their own misalignment. Each band reports
the 5th–95th percentile width rather than the extremes, so a long thin staff stops dominating the
number, and the lateral/depth **centroid** as well as the width, which is what catches a limb that is
the right size on the wrong side. Reads uncompressed `.glb` with the standard library only.

### scripts/character_audit.sh
`scripts/character_audit.sh <page-url> <output-dir> [mesh-name-regex] [--allow a,b]...`
Runs every geometry gate against a live model and writes a baseline to diff against later, so "before
and after" is a number rather than an impression. Arguments after the regex are forwarded to the
penetration gate, which is where `--allow` belongs: parts that *should* share space (an ear root in a
skull, a hand gripping a staff) are contact, not defects, and a gate with no exemption list flags them
until someone switches the gate off entirely.

### integrations/mesh3d/generate_reference_mesh.py — optional, external
`integrations/mesh3d/generate_reference_mesh.py <image>... --out-dir <dir> [--space S] [--hf-token T]`
Generates a reference mesh from reference image(s) via a hosted Space, emitting GLB **and** OBJ from
one generation and one transform. GLB is the transport format so the reference can be rendered with
the same camera and shader as the candidate — comparing a PBR render against a photograph is what
pins `ssim` at 0. OBJ is the scoring format, because `forge/` gates are pure-stdlib by house rule and
OBJ is ASCII a short parser reads. Unlike everything else in this cheatsheet it needs network access
and a third-party endpoint, so it is never on a required path: its output is an input to review, not
evidence that a gate passed.
