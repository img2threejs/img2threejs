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

### Executed geometry gates before browser capture

After generation, execute the factory without a renderer and inspect
`root.userData.sculptRuntime`; do this before opening the browser. For multipart characters, the
pre-browser report must cover every named spec component and measure, where applicable:

- left/right reflection from world-space bounds, plus thumb/index chirality for hands;
- ordered garment-shell intervals so a waist layer cannot sink into the layer beneath it;
- every `geometry.userData.standProud.unresolved` count (zero is the exact pass condition);
- engine-visible `material.userData.referenceMaterialId`, not an ignored authoring field;
- garment boundary positions against the relevant articulation joints, not all unrelated bones.

Global width/height/depth ratios may be recorded against a GLB baseline, but remain diagnostic and
must declare themselves uncalibrated until paired multi-angle silhouette controls establish an
acceptance threshold. Do not turn an arbitrary ratio tolerance into a gate. Every exact gate added
for a showcase needs a passing fixture and a mutation that makes it fail (missing mesh, same-side
reflection, sunk layer, swapped thumb, unresolved proud vertex, missing material ID, or boundary
moved onto its joint). A clean TypeScript build is not executed-geometry evidence.

Ordinary primitives stay on their authored geometry path: generated factories do not invent an
attachment variable for them, and the shared endpoint branches retain the declared
`AttachmentEndpoint | null` helper return type rather than a literal null that strict TypeScript
narrows to `never`. Verify that negative-control path with a spec containing no attachment-derived
primitives before accepting the showcase build.

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
the GLB-mediated route. `regions` must name the actual subject regions; list the mandatory subset
under `extensions.requiredSemanticRegions`. The validator rejects missing declared regions and does
not impose the example subject's names on another reconstruction:

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

### stage4_review/vertex_region_gate.py
`stage4_review/vertex_region_gate.py --geometry meshes.json --palette palette.json [--expect expect.json] [--azimuth 0] [--color-tolerance T] [--max-unclassified N] [--out report.json] [--json]`
Gates colour-region BOUNDARIES on executed geometry, before any browser render. When a subject's
identity is carried by flat colour regions with hard edges — a tuxedo cat's blaze, bib and socks; a
livery stripe; a painted marking — the position of those boundaries is an identity feature, so it is
measured rather than eyeballed. `--palette` is `{regionId: '#rrggbb'}` for every region to measure;
without `--expect` the gate only reports measurements instead of passing or failing. Read
`--max-unclassified`: vertices matching no palette entry are named, so a clean verdict over a mostly
unclassified mesh cannot be mistaken for agreement. The shape predicates it shares with the
validator and the emitted TypeScript live in `_shared/vertex_paint.py` (no CLI).

### stage4_review/swept_arc_gate.py
`stage4_review/swept_arc_gate.py --geometry meshes.json --component ID [--expect expect.json] [--out report.json] [--json]`
Gates a swept component's SHAPE — bend radius, angular span and taper — on executed geometry.
"Curled upward into a hook; a curved spine, not a straight cone" is a claim about a curve, and no
other gate can hold it: a silhouette IoU passes a straight cone that happens to occupy roughly the
right cells, and `self_intersection.py` asks whether a mesh crosses itself rather than what shape it
is. `--component` takes a mesh id or name.

## Reference comparison and baselines

### stage4_review/interior_difference.py
`stage4_review/interior_difference.py BASELINE.png RENDER.png [--from 0] [--to 0.19] [--json]`
Appearance difference **inside** the silhouette, banded by height. Required evidence on every visual
pass, because silhouette IoU is computed from roughly 11% of figure cells — the ones on the outline —
and is blind to the other 89%. The measured proof: a model with its face deleted scored 0.8803
against the finished face's 0.8803, identical to four decimals, and adding an entire mouth moved
that metric −0.0002. Both renders are aligned by foreground bounding box, the same normalisation the
IoU scorer uses, and only cells that are figure in **both** are compared so outline agreement cannot
leak back in. Refuses to score when either foreground mask fell back to whole-frame coverage — the
same hard gate `divine_eye` makes, for the same reason. Reports `cellsCompared`, so a difference
measured over a handful of cells cannot pass as evidence. On a standing figure the head is roughly
`--from 0 --to 0.19`.

## Hair

Full contract, every measurement behind it, and every stated non-goal: `docs/HAIR_PIPELINE.md`.
Run these only when the subject has hair — `orchestrate_passes.py` demands them via
`spec_has_hair()`, which reads a `hairProfile` block or any component whose role is `hair`, so a
chair and a knife are never asked for hair evidence.

### stage1_intake/extract_hair_evidence.py
`stage1_intake/extract_hair_evidence.py front=ref.front.png rear=ref.rear.png [--out evidence.json]`
Measures what the reference actually says about its hair: the hair/skin split, banded dark coverage
across crown/mid/jaw, the hairline (writing the `faceLandmarks.hairline` slot that existed unfilled
since v1.2), the specular band position, and the root-to-tip luminance delta. Views not supplied are
reported as `notObserved`, so nothing downstream authors a nape as if it had been seen. The split is
Otsu's between-class variance, not a percentile: a fixed percentile makes the reported hair fraction
true by construction and read 0.380 / 0.384 / 0.382 across three different views of one subject,
which looks like agreement and is arithmetic. The same three views now read 0.387 / 0.592 / 0.747.

### stage4_review/scalp_exposure.py — HARD
`stage4_review/scalp_exposure.py --rings skull.json --hair-points hair.json [--v-low 0] [--v-high 1] [--hard-max 0.05] [--out report.json]`
Finds bald patches geometrically, on points, before anything is rendered — so it needs no browser, no
GPU and no capture, and works on any hair representation. It counts only hair **outside** the skull:
a nearest-neighbour test passes the failing build, because those vertices were still nearby, merely
sunk below the surface. Exposure above `--hard-max` is a hard failure, never a soft signal.
`--hard-max` is deliberately loose and uncalibrated, and the report says so.

### stage4_review/hair_gate.py — soft
`stage4_review/hair_gate.py --reference front=ref.png --render front=out.png [--scalp-exposure report.json] [--out gate.json]`
Compares banded coverage, hairline offset and highlight-band position against the reference, and
classifies each difference by kind. Pass `--scalp-exposure` and its verdict dominates: a bald patch
is always wrong, while a coverage shortfall is often the best available compromise at a given
triangle budget. Conflating the two produced four wrong fixes in one session — a shortfall was read
as "add more hair", the masses were widened, and the widening pushed them off the skull, taking
closure from 42.2% to 40.9%, worse on all six views, with crown exposure up 14.9 points on the worst.
**A coverage shortfall never authorises widening the masses on its own.**

### Hair libraries (no CLI)
- `_shared/scalp_field.py` — signed distance to a skull built as a stack of ellipse rings, derived
  from the head component so it is never authored twice. Sign is exact; magnitude is the first-order
  estimate `f / |grad f|`, so treat the sign as authoritative and the magnitude as approximate.
- `stage2_spec/hair_profile.py` — the hairstyle schema and its validation rules. Roots are `(u, v)`
  on the scalp; an absolute root is a hard error. `plane-card`, `tube` and `box` are rejected for
  hair. Default representation tier is `shell`. **This module validates a profile; it does not
  compile one into components** — no profile-to-`componentTree` compiler exists yet.

## Left and right

### _shared/chirality.py (no CLI)
Two chirality defects can ship in one figure and need **different** tests, which is why both exist:
- `check_pair()` — enforced at spec time by `validate_sculpt_spec.py`. A pair built by negating x
  *and* z is a 180° rotation, and rotation preserves handedness, so both limbs come out the same
  hand. It names the relation (`rotation` / `translation` / `unrelated`) rather than saying
  "mismatch", because the two are trivially confused and agree exactly on a symmetric part.
  Measured on the humanoid: the thumb tip sat at z ±0.288 across the pair where a mirror leaves z
  alone; fixing it moved the hand region **46% closer** to the reference in front view.
- `medial_lateral_bias()` + `compare_bias()` — needs a reference. Catches what a pair test
  structurally cannot: a pair wrong the *same* way on both sides is still a perfect mirror of
  itself. Only the **sign** of the bias is judged; a magnitude difference is a proportion issue that
  other gates own. Measured on the humanoid: toes ordered little-to-big across a strip whose index 0
  is medial put the big toe outboard on *both* feet — toe-band mass reference 529 medial / 488
  lateral, ours 350 / 443 — and a foot with its big toe outside is the other foot. Below `MIN_REFERENCE_BIAS` (0.025) the reference is treated as too symmetric to
  judge handedness from.

`CHARACTER_LEFT_SIGN` is the convention as code: with `forward: +Z`, Y up and a right-handed frame,
the character's own left is `+X`. Reflecting also inverts triangle winding — flip it back on the
mirrored side, or `flatShading` derives every normal from the reversed winding and the limb lights as
though lit from behind.

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
