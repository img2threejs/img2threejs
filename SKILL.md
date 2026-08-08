---
name: img2threejs
description: Turn an object or character reference image into a quality-gated, animation-ready procedural Three.js model built in code. Use for image-to-3D reconstruction, detail-accurate object rebuilds, stylized/likeness-maximized human characters, sculpt specs, and staged code generation.
license: Apache-2.0
version: 1.4.4
---

# img2threejs — Image to procedural Three.js

Rebuild the object visible in a reference image as a **code-only** procedural Three.js model,
gated by a staged sculpting pipeline and an AI-vision self-correction loop. This is
reconstruction-by-code, **not** photogrammetry, mesh extraction, or downloaded art packs.

Agent-agnostic: works under Claude Code, Codex, or OpenCode. Wherever this doc says "agent
vision" or "agent browser tool", use whatever the host provides — native image reading, a
browser MCP (playwright/chrome-devtools), the project preview, or a user-supplied screenshot.

## Canonical shared checkout

Keep one checkout of this repository and let every host enter it through a symlink, so Claude and
Codex execute the same code instead of drifting apart:

```text
~/.claude/skills/img2threejs -> <your checkout>
~/.codex/skills/img2threejs  -> <your checkout>
```

## When To Use

The user attaches/points to an object image and wants a procedural Three.js model, a
reconstruction/animation/destruction plan, a sculpt spec, or code. Also for material studies,
action-ready props, game objects, botanical/mechanical parts, and stylized reconstructions.

## Core Promise

Sculpt from a photo, in order — never one-shot a mesh:
1. **Run `python3 forge/next.py <spec>` first, or `python3 forge/next.py --state .img2threejs/state.json`.** The state form reports the ordered local checklist, exact next command, evidence status, and bounded correction-loop status; it never replaces the spec/pass gates.
1. **Use local state first.** Initialize it once, then run
   `python3 forge/next.py --state .img2threejs/state.json [<spec>]` at every start/resume and before
   every correction iteration. Obey a hard stop; never continue from memory.
2. **Validate** the image is a suitable 3D target (`grimoire/intake/validation_rubric.md`).
3. **Assess** object class + complexity, then write a `qualityContract` before any code.
3. **Spec** it: component hierarchy, materials, lighting, pivots, sockets, action anchors.
4. **Build pass-by-pass** from blockout → structure → form → material → lighting → interaction → optimization.
5. **Verify** each pass with a screenshot compared against the reference; fail a pass if an identity-defining feature is wrong even when the global score looks fine.

State explicitly when output is approximate/stylized/low-poly. A single image cannot reveal
hidden sides or guarantee exact geometry — say so instead of faking confidence.

## Resumable local workflow

For a cross-agent or multi-session reconstruction, initialize the local state before intake:

```bash
python3 forge/state.py init --reference <image> --profile character --spec object-sculpt-spec.json
python3 forge/next.py --state .img2threejs/state.json
python3 forge/state.py mark image-analysis --evidence analysis.md
```

`generic`, `character`, and `cs2` profiles insert their required intake gates in order. Every
completed step needs evidence; every skipped step needs a reason. The state file is a resumability
index, not visual evidence: renders, specs, review history, and deterministic gates remain the
authoritative artifacts.

## Transparency and Process Debugging (Critical — from Bowie Knife reconstruction)

**The problem:** When the user cannot tell what was done or where something went wrong, they cannot debug the process. Over-claiming (reporting success when features still don't match) destroys trust and makes iterative improvement impossible.

**Rule:** Be transparent + don't over-claim. State exactly what changed each pass, with evidence, and name what still doesn't match:
- After each pass, explicitly list what changed: "Updated guard shape to extend left edge from -0.56 to -0.48 for handle overlap"
- Provide evidence: reference the specific values, coordinates, or parameters that changed
- Name what still doesn't match: "Handle silhouette traced but still flat plane (no Z palm-swell), procedural crosshatch not reference's exact dot-grid knurl"
- Explain why a change was made: "Extended guard left edge because handle ends at X=-0.42 and guard ended at X=-0.20, causing visual gap"
- Never claim a feature is "done" when it's only "improved" — use precise language
- When a gate passes but visual inspection shows issues, explain the limitation: "2D gate passed (fidelity 0.83) but three-quarter render shows blade reads as toy (no grind wedge) — 2D gates are blind to 3D realism"

**The user needs to be able to debug the process, not just the output.** If something is wrong, they should be able to trace which decision led to the error and correct it. Opaque processes force restarts; transparent processes enable refinement.
## Transparency and Process Debugging

Report what changed each pass with evidence (exact values/coordinates), name what still doesn't
match, and never claim "done" when only "improved". A passing gate is not proof of 3D realism.
Full rule + examples: `grimoire/review/self_correction.md`.

## GLB-mediated v2 render-fidelity track (1.5 alpha)

When the user supplies a GLB as an intermediate reference, use the browser-rendered GLB
as the structural and visual baseline, then author an independent procedural factory. The
raw GLB is never pixel evidence and its topology/materials are never copied into the factory.

Before any factory edit:

1. Run `forge/stage1_intake/probe_glb.py` and inspect `semanticDecomposition`. A merged
   one-node/one-mesh/one-primitive/one-material asset is `insufficient` for reliable semantic
   labels; connected-component/curvature/normal/UV segmentation is hypothesis evidence only.
   Request a multipart GLB or capture a browser semantic-ID pass before claiming exact regions.
2. Author and validate one shared `render-profile.v2` with
   `forge/stage4_review/validate_render_profile.py`. Both GLB and procedural routes must use
   the same output color space, linear working space, tone mapping/exposure, PMREM environment,
   viewport/DPR, camera, background and lighting settings.
3. Capture six passes for every admitted view: `beauty`, `alpha-silhouette`, `semantic-id`,
   `depth`, `normal`, and `roughness-material-id`. Use
   `forge/stage4_review/compare_region_passes.py` for deterministic global/per-region evidence;
   missing semantic-ID data blocks per-region confidence instead of falling back to whole-image
   color or silhouette scores.
4. Use region-specific continuous geometry/material strategies. Do not replace a face/head volume,
   cloth shell, kasa, staff, or tail with a generic collection of floating primitives when the
   region's silhouette or attachment requires a continuous surface.
5. Run one correction group per loop in this order: `camera → silhouette → face → clothing →
   accessory → materials → lighting`. Recapture the full pass set after each group and record
   the changed group, hashes and score. Never combine groups when diagnosing improvement.

The machine-readable contract lives in `docs/specs/render-profile.v2.schema.json` and
`docs/specs/render-profile.v2.example.json`. The executable manifest bridge accepts
`--render-profile` and exposes `record-pass`; its `glb-mediated-v2` validation is fail-closed.

## Required Inputs

- one image path / screenshot / URL / attached image (if missing or unreadable, ask)
- intended use: prop, game object, hero render, playable/destructible object, animation rig
  (default: real-time browser prop with interactive performance)
- for a CS2 request, an authoritative classification record (family/subtype and evidence refs) or
  an explicit request for the user/vision provider to supply one; heuristic detection alone is not
  enough to select a geometry adapter

## Mandatory Local State Gate

Conversation context is disposable; `.img2threejs/state.json` is the local checklist authority.
Initialize it once per reconstruction:

`python3 forge/state.py init --state .img2threejs/state.json --reference <img> --profile <generic|cs2|character>`

At every fresh start, resume, or correction loop, run
`python3 forge/next.py --state .img2threejs/state.json [object-sculpt-spec.json]` before touching
code. It prints the current step, pass, incomplete mandatory steps, exact next command, and
`loop/max`. Exit code 3 or `status=stopped` is a hard stop: report the reason and request input.
Never bypass it by reconstructing progress from chat history.

After evidence exists, record it with
`python3 forge/state.py mark <step-id> --state .img2threejs/state.json --evidence <path>`.
Mark a non-applicable step `skipped` only with `--reason`; silent omission is forbidden. Loop counts
are derived from `reviewHistory` actions `refine-spec`/`refine-code`, not agent memory. Defaults are
3 corrections per pass and 6 total.

Profiles add mandatory gates rather than changing the core order: `cs2` requires classification,
manifest, and a machine-readable CS2 review before AI review; `character` requires the character
contracts and landmark evidence. Every profile records suitability, projection applicability, and
material-evidence applicability; conditional steps require evidence or an explicit skip reason.

## What the state gate now enforces (and why)

The mandatory reads and the primary evaluator used to live only in this file's prose. Prose is not
a control: an agent can run the whole pipeline, mark every checklist step, and never open
`geometry_patterns.md` or run `divine_eye.py`, because nothing asked. That happened, and the rules
it skipped were the exact ones that then broke the reconstruction ("do NOT eyeball proportions",
"never report a 2D-gate pass as done"). So they are checklist steps now:

| Step | Enforces |
| --- | --- |
| `topology-contract-read` | `surface_topology.md` + `detail_inventory.md`, before any primitive is chosen |
| `build-contract-read` | `geometry_patterns.md` + `shading_realism.md`, before any geometry is generated |
| `character-pipeline-read` | `standard_character_pipeline.md` on the character profile |
| `divine-eye` | `divine_eye.py` runs on every pass; it is the harness heart, not an optional extra |

Two guards back them up:

- **A contract-read step must name its documents.** `state.py mark review-contract-read --evidence
  object-sculpt-spec.json` is refused; the evidence has to include every required path. Nothing can
  prove a document was read, but marking the step is no longer free.
- **`append_review.py` refuses a score it cannot check.** A visual pass requires
  `--divine-eye-json`, and a claim more than 0.15 above the measured fidelity needs
  `--over-claim-reason` in writing. This exists because a review was once recorded at 0.72 while
  Divine Eye measured 0.5072 on the same render. `self_correction.md` opens with a section about
  over-claiming; it did not stop it, because it was only prose.

## The Loop (scripts do enforcement; agent vision does judgment)

Run scripts from the skill root (`forge/...`). Pure Python 3.10+ stdlib, no pip installs.
Full flags: `grimoire/scripts.md`. Never let a script *score* visuals — that is the agent's job.

1. **Analyze the image first** (agent vision, before any script): work the layered observation
   protocol in `grimoire/intake/image_analysis.md` — identify/classify, decompose macro→meso→micro,
   map part relationships, name materials in PBR terms, list identity-defining features, and flag
   what the single view hides. Observation before inference; controlled 3D vocabulary; 3D
   object-space not 2D image-space. This is generic for any subject and feeds every field below.
   Then probe local images: `forge/stage1_intake/probe_image.py <image>` (metadata only, not a visual check).
1a. **Local Spec Search** — after image analysis and before writing or refining a spec, local
    evidence is a pipeline stage, not an optional memory lookup, whenever the request needs
    domain-specific anatomy, PBR, wear, geometry, runtime, or physics specifications. The pre-spec
    command automatically runs BM25, chooses `cs2` for CS2 targets and `core_3d` otherwise, and
    writes a `localSpecSearch` evidence bundle into the assessment:
    `python3 forge/stage2_spec/new_pre_spec_assessment.py "Name" --image <img> --out assessment.json`.
    Add observed terms with repeatable `--spec-query "<term>"`; use `--collection <collection>` only
    when the automatic collection choice is insufficient. `new_sculpt_spec.py --assessment` carries
    that bundle into the final spec, including snippets, `source_refs`, and `evidence_refs`.
    For extra focused retrieval, the direct CLI remains available:
    `python3 forge/stage1_intake/search_specs.py "<query>" --collection <collection> --limit 3 --snippet-chars 250 --json`.
    For CS2, include the anatomical and the colloquial name, for example
    `--spec-query "safety ring finger ring"` or `search_specs.py "roughness matte" --collection cs2`.
    Expand queries with object names,
    component names, material/finish terms, behavior terms, and known aliases; retry focused
    alternatives when the first result is incomplete. Build the spec from returned evidence and do
    not invent domain specs when local evidence exists. Search caches are local/generated only;
    preserve JSONL records and source provenance rather than replacing them with cache output.
1aa. **Optional fidelity evidence adapters** — use them only when they improve an observed weak
    point; the stdlib core remains authoritative. Route thin/complex masks to local SAM2, character
    face/pose evidence to MediaPipe, and weak front/back cues to Depth Anything V2:
    `python3 forge/stage1_intake/run_vision_adapter.py <segment|landmarks|depth> ...`.
    Every adapter emits provenance. Confirm SAM2 selected the intended component; treat monocular
    depth as relative only; review landmarks before copying them into anatomy. For browser work,
    prefer Chrome DevTools MCP for live console/network/performance diagnosis, use
    threejs-devtools MCP read-only to inspect scene/material/renderer state, and reserve Playwright
    MCP for cross-browser or host fallback. MCP-only scene mutations never count as implementation:
    write the proven change back to the spec or TypeScript, rebuild, and recapture. Use Context7
    only with the target project's installed Three.js version; local types, typecheck and runtime
    smoke tests override live docs. Full routing and commands:
    `docs/integrations/reference_fidelity_tooling.md`.
1b. **CS2 intake manifest** — for a CS2 request, create and validate `cs2-intake.json` before
    pre-spec authoring. Run admission and probing for every source view, record the heuristic signal
    as non-authoritative evidence, attach the classification record, resolve the supported family,
    and choose `route` independently from `exactnessTier`. Missing classification, insufficient
    coverage, or a contradictory high-confidence class is `request-input`; unsupported families do
    not continue into spec generation.
2. **Pre-Spec Assessment Gate** — classify + score complexity + write the quality contract:
   `forge/stage2_spec/new_pre_spec_assessment.py "Name" --image <img> --complexity <simple|moderate|complex|ultra-complex> --out assessment.json`. Rules: `grimoire/intake/quality_contract.md`.
   Set `objectClass.primaryDomain` (`object` | `character` | `hybrid`) and fill the seeded
   `detailInventory` (its `targetMinDetails` scales with complexity). **Supported CS2 knife skins
   and Glock-18 assets**: always pass `--cs2`, which defaults the complexity tier to `ultra-complex`
   (`targetMinDetails` 16) — the finish/wear/hardware is the item, so CS2 is held to the top
   fidelity bar; `targetMinDetails` never drops below the 9 floor even if downgraded by hand.
   **Author procedural GEOMETRY (blade/guard/grip profiles) but make the FINISH a de-lit
   reference-crop PROJECTION, not a procedural finish material** — projecting the photo's own
   pixels is what reaches reference fidelity for patterned skins (Doppler/Gamma/Marble/Fade), and
   is what the v1.3 baseline demos do; a procedural finish for a patterned skin reads visibly wrong
   against the reference. Take the projection path in step 2c (it generalizes from characters to
   any reference-matched surface). Procedural finish is the fallback ONLY when live view-dependent
   response matters more than matching this one reference. Finish routes + rulebook:
   `grimoire/build/cs2_finishes.md`; optional exact-texture acquisition:
   `grimoire/intake/cs2_texture_acquisition.md`.
1a. **Local Spec Search** — after image analysis, before writing or refining a spec, pull local
    domain evidence (anatomy/PBR/wear/geometry/runtime/physics) rather than inventing it:
    `python3 forge/stage2_spec/new_pre_spec_assessment.py "Name" --image <img> --out assessment.json`
    (auto-runs BM25, auto-picks `cs2`/`core_3d` collection, writes a `localSpecSearch` bundle that
    `new_sculpt_spec.py --assessment` carries into the spec). Full query-expansion recipe
    (bilingual terms, focused `search_specs.py` retrieval, cache rules):
    `grimoire/intake/local_spec_search.md`. MUST read it before retrying an incomplete or
    domain-specific query.
1b. **CS2 intake manifest** — for a CS2 request, create and validate `cs2-intake.json` before
    pre-spec authoring (admission, heuristic signal, classification, family/route resolution).
    MUST read `grimoire/intake/cs2_intake_contract.md` completely before creating the manifest or
    running pre-spec assessment.
2. **Pre-Spec Assessment Gate** — classify + score complexity + write the quality contract:
   `forge/stage2_spec/new_pre_spec_assessment.py "Name" --image <img> --complexity <simple|moderate|complex|ultra-complex> --out assessment.json`. Rules: `grimoire/intake/quality_contract.md`.
   Set `objectClass.primaryDomain` (`object` | `character` | `hybrid`) and fill the seeded
   `detailInventory` (its `targetMinDetails` scales with complexity). **Supported CS2 knife
   skins**: always pass `--cs2`, which defaults the complexity tier to `ultra-complex`
   (`targetMinDetails` 16, floor 9) — the finish/wear/hardware is the item, so CS2 is held to the
   top fidelity bar. Author procedural GEOMETRY but route the FINISH through the projection path in
   step 2c — a procedural finish for a patterned skin (Doppler/Gamma/Marble/Fade) reads visibly
   wrong against the reference. Finish routes + rulebook: `grimoire/build/cs2_finishes.md`;
   optional exact-texture acquisition: `grimoire/intake/cs2_texture_acquisition.md`.
2b. **Detail inventory** (do not skip for detailed subjects) — scan zones and enumerate every
   identity-defining small detail (gloss, bevel, fasteners, linework, contours, stains):
   `forge/stage1_intake/build_detail_inventory.py <image> --mode grid-3x3 --out-dir <dir> --out di.json`.
   Each detail MUST map to a `component.localFeatures` or `material.localOverrides` entry — never
   prose only. Taxonomy + 3D-term recipes: `grimoire/intake/detail_inventory.md`.
2c. **Projection-first fidelity (characters AND reference-matched surfaces — supported CS2 skins, decals,
   painted patterns)** — when the goal is matching a specific reference's surface, put the photo's
   own pixels on the mesh instead of approximating them procedurally. This is the single biggest
   fidelity lever; a procedural material for a patterned surface is the #1 reconstruction failure.
   Recipe (`grimoire/character/likeness_maximization.md` — its two levers, align-mesh+camera and
   project-the-photo, generalize past characters): solve the camera
   (`stage1_intake/solve_camera_pose.py` → `referenceCamera`), **de-light** the reference so it is
   free of baked lighting (`stage1_intake/delight_albedo.py`, hard requirement — this is what makes
   projection safe, not the flat-lit icon), then project the de-lit crop onto the mesh and bake it
   into UVs (`stage3_build/bake_projected_texture.py --mesh-id <id>`). For a CS2 skin the mesh is the
   procedural family-specific component tree you author in the spec, and the projected de-lit crop IS the finish
   (front + back from the two views) — no procedural Doppler material. For characters, first capture
   landmarks (`stage1_intake/extract_landmarks.py --out anatomy.json`), fill `preSpecAssessment.anatomy`,
   route `grimoire/character/reconstruction.md`. A single view cannot show hidden sides — report
   per-region confidence and request more views when it matters.
   Character sub-routes, in the order they are needed — decide what parts exist before shaping any
   of them, and shape the head before the hair that sits on it:
   - **Parts** — `grimoire/character/structure_decomposition.md`: which parts the figure is made of,
     and where each one's boundary falls.
   - **Head** — `grimoire/character/head_construction.md`: skull, face plane and feature placement,
     the sub-route the likeness gate reads against.
   - **Hair** — `grimoire/character/stylized_hair_threejs.md`, with the parameter contract in
     `grimoire/character/threejs_hair_parameter_contract.json`. Lock topology first: material
     tuning cannot repair wrong lock topology, so run it only after the silhouette review passes.
2d. **Reference-free humanoid** — when the request is a generic figure with no reference image
   ("a low-poly humanoid"), there is nothing to measure, so fill anatomy from public canon with
   `forge/stage2_spec/humanoid_proportions.py <spec> --style-heads 8 --in-place`. It writes
   `anatomy.source: "canon-table"` so canon is never mistaken for measured evidence, refuses to
   run at all when the spec names a reference image, and lists what the corpus does NOT supply
   under `anatomy.unsourced`. Only complete head counts are derivable (currently 8); anything
   else fails with the missing landmark named rather than being interpolated.
3. Author the spec from the assessment:
   `forge/stage2_spec/new_sculpt_spec.py "Name" --image <img> --assessment assessment.json --manifest cs2-intake.json --out object-sculpt-spec.json`.
   Replace generic starter `featureReviewTargets` with the object's real identity-defining
   systems (≤5 critical, ≤3 important per pass); for characters add `anatomy-proportion`,
   `face-landmark-placement`, `pose-silhouette`, `outfit-and-palette`. Use 3D-graphics terms only
   (`grimoire/glossary/3d_vocabulary.md`), never "nice/smooth/shiny". Classify every component's
   `topologyClass`/`topologyRationale` per `grimoire/intake/surface_topology.md` before picking a
   `primitive` — this is what prevents a continuous organic form from being picked as a box.
4. When material fidelity matters and a source image exists, analyze each material's **finish** then
   extract reference PBR evidence, both per crop (crop the correct region — verify the crop is on the
   part you think it is):
   - `forge/stage1_intake/analyze_texture.py <crop> --spec spec.json --material-id <id> --in-place`
     classifies the finish (`gem-metal | gemstone | painted-metal | worn-composite | brushed-steel |
     plastic`), extracts the gradient palette, and writes doc-grounded MeshPhysicalMaterial scalars
     (metalness/roughness/clearcoat/transmission/ior/anisotropy/envMapIntensity) onto the material.
     Recipes + Three.js texture/PBR rules (colorSpace, CanvasTexture/DataTexture, height→normal) live
     in `grimoire/build/threejs_texture_reference.md`. Rule of thumb: **solid albedo for flat paint,
     real reference crop for patterned finishes** (doppler/quartz/hydro-dip/camo).
   - `forge/stage1_intake/extract_pbr_evidence.py <crop> --out-dir <dir> --material-id <id> --target-threshold 0.7`.
   Confidence < 0.7 is a stop/refine-input signal, not a pass. It is inference, not inverse rendering.
   - For multiple named regions, use `forge/stage1_intake/material_region_analysis.py --manifest regions.json --out-dir material-evidence --out material-analysis.json`. Resolve each accepted assignment from `docs/materials/material-reference.json`, then wire it into the spec with `forge/stage2_spec/apply_material_analysis.py`.
   - Emit the controlled material camera/crop contract with `forge/stage4_review/material_views.py`, compare saved visible-footprint crops with `forge/stage4_review/material_comparator.py`, apply only bounded material-scoped corrections with `forge/stage4_review/material_feedback.py`, and record the blocking result with `forge/stage4_review/material_gate.py`.
5. Validate, then strict-validate before generating code:
   `forge/stage2_spec/validate_sculpt_spec.py object-sculpt-spec.json` then `--strict-quality`.
   Strict blocks shallow specs (a complex object with one root, no repetition systems, no
   local overrides, no micro groups is NOT implementation-ready even if JSON validates).
6. **Locked build passes** — only touch the currently unlocked pass:
   `forge/stage3_build/orchestrate_passes.py status object-sculpt-spec.json`
   `forge/stage3_build/generate_threejs_factory.py object-sculpt-spec.json --out src/createObjectModel.ts`
   (generator is fail-closed: `strict-quality` must pass before it can write any factory; a future
   `--pass-id` also fails until prior passes are reviewed `continue`). If blocked, preserve the
   `BLOCKED` artifact and refine the subject-specific spec; do not substitute a generic template.
6a. **Hitting a triangle budget.** `performanceBudget.targetTriangles` selects a tessellation
   tier for every primitive that has segment counts (low ≤6k, standard ≤60k, else hero), and
   caps implicit-surface sampling grids. Where that is not precise enough — an SDF's grid is
   quantised, so a tier can only get near a number — add
   `geometryDescriptor.decimate: {"targetRatio": 0.4}` to that component. It emits a
   Garland-Heckbert quadric collapse into the generated factory and runs **before** skin
   binding, so weights are computed on the surviving vertices and no skinning data is
   interpolated across a vertex merge. It keeps `position` only, recomputing normals, so it is
   refused on an authored/unwrapped `uvStrategy`. For offline LOD tiers from an exported mesh,
   `forge/stage3_build/decimate.py <mesh.json> --ratio <r> --json` is the same algorithm.
7. Render the current pass in a browser/preview, capture a screenshot at a review viewpoint.
7a. **Off-axis and placement gates — a single review viewpoint is not evidence about the model.**
   Capture a turntable, not one frame, and run all three. Each catches a defect class the older gates
   pass by construction; skipping them is how a hole through a skull, a hat at hip height and a charm
   floating below the ground plane survived eight front-only review rounds.
   `forge/stage4_review/turntable_gate.py --capture 0=front.png --capture 90=right.png --capture 180=rear.png --capture 270=left.png --json`
   `node runtime/scripts/export_mesh_geometry.mjs --url <preview> --out meshes.json` then
   `forge/stage4_review/self_intersection.py meshes.json --json`
   `forge/stage4_review/attachment_anchor.py object-sculpt-spec.json --measured measured.json --json`
   All three exit `0` clean / `1` gate failure / `2` error. A failure blocks `continue` for the pass
   even when the global fidelity score passes — the score is computed from one camera and a 64×64 luma
   grid, and neither can represent any of these defects. Read `sampledVertexCount` /
   `unmeasuredAttachments` / `missingAzimuths` before believing a clean verdict: each names the part of
   the model the gate did not actually look at.
8. Package one side-by-side sheet, then inspect it with agent vision:
   `forge/stage4_review/make_comparison_sheet.py --reference <img> --render <shot> --out cmp.png --json`.
9. Record the review (overall + per-layer + per-feature scores + decision):
    `forge/stage4_review/append_review.py object-sculpt-spec.json --pass-id <pass> --fidelity <0-1> --action <continue|refine-spec|refine-code|request-input|stop> --summary "..." --render-screenshot <shot> --comparison-image cmp.png --ai-vision-score <0-1> --layer-scores-json '{...}' --feature-reviews-json <f.json> --in-place`.
   For the CS2 family path, also attach the versioned report with
   `--cs2-review-json cs2-review.json --review-scene-json forge/tests/fixtures/knife_review_scene.json`.
   A failed family, painted-region, projection-coverage, critical-detail, or orbit gate blocks
   `continue` even when the global score passes. See `docs/cs2/review-gates.md`.
10. Sync pipeline state after manual review edits:
     `forge/stage3_build/orchestrate_passes.py sync object-sculpt-spec.json --in-place`.

## Forge Runtime Contracts

Subdivision runtime tests compile generated TypeScript against the showcase checkout. Set
`IMG2THREEJS_SHOWCASE_ROOT` to that checkout; without it, local runtime-only tests skip with an
actionable message while static contracts still run. CI should set `IMG2THREEJS_REQUIRE_SHOWCASE=1`
to turn a missing showcase checkout into a test failure.

```bash
IMG2THREEJS_SHOWCASE_ROOT=/path/to/img2threejs-showcase python3 forge/tests/test_subdivision.py
IMG2THREEJS_SHOWCASE_ROOT=/path/to/img2threejs-showcase python3 -m unittest discover -s forge/tests
IMG2THREEJS_SHOWCASE_ROOT=/path/to/img2threejs-showcase python3 forge/tests/test_showcase_tsc_smoke.py
```
   (generator is pass-gated: a future `--pass-id` fails until prior passes are reviewed `continue`).
   The local state adds `--force` only for a new pass or `refine-spec`; `refine-code` edits the
   current artifact without regenerating it. Before overwriting, carry valid hand refinement back
   into the spec; generated code must not be the only copy of reconstruction decisions.
7. Render the current pass in a browser/preview, capture a screenshot at a review viewpoint.
8. **Run deterministic gates before AI vision.** MUST read
   `grimoire/review/gates_reference.md` and `grimoire/review/self_correction.md` completely. Run
   `forge/stage4_review/diagnose_render.py` and record the passing Tier 1 result with
   `--spec object-sculpt-spec.json --pass-id <pass> --in-place`; for non-planar forms also run
   `forge/stage4_review/diagnose_render_multi_angle.py` with the fixed view and at least two
   meaningful orbit views. Then run
   `forge/stage3_build/orchestrate_passes.py check object-sculpt-spec.json --pass-id <pass>`.
9. Package one side-by-side sheet, then inspect it with agent vision:
   `forge/stage4_review/make_comparison_sheet.py --reference <img> --render <shot> --out cmp.png --json`.
10. Record the review (overall + per-layer + per-feature scores + decision):
    `forge/stage4_review/append_review.py object-sculpt-spec.json --pass-id <pass> --fidelity <0-1> --action <continue|refine-spec|refine-code|request-input|stop> --summary "..." --render-screenshot <shot> --comparison-image cmp.png --ai-vision-score <0-1> --layer-scores-json '{...}' --feature-reviews-json <f.json> --in-place`.
   For the CS2 knife path, also attach the versioned report with
   `--cs2-review-json cs2-review.json --review-scene-json forge/tests/fixtures/knife_review_scene.json`.
   Produce that report first with
   `forge/stage4_review/cs2_review.py --manifest cs2-intake.json --metrics cs2-review-inputs.json --scene forge/tests/fixtures/knife_review_scene.json --out cs2-review.json`.
   A failed family, painted-region, projection-coverage, critical-detail, or orbit gate blocks
   `continue` even when the global score passes. See `docs/cs2/review-gates.md`.
11. Sync pipeline state after manual review edits, record checklist evidence, then re-run the local
    state gate before another correction or pass:
    `forge/stage3_build/orchestrate_passes.py sync object-sculpt-spec.json --in-place`
    `python3 forge/next.py --state .img2threejs/state.json object-sculpt-spec.json`.
12. Before declaring completion, run
    `forge/stage4_review/check_part_coverage.py --spec object-sculpt-spec.json --manifest parts.json`
    and verify the action-ready hierarchy. Mark `part-coverage` and `action-ready` only with evidence.

## CS2 image-matched rule

For a CS2 item, the target is observable agreement between the supplied image and the rendered
item: silhouette, proportions, edge profile, hardware layout, coating colour, pattern placement,
wear, roughness response, and camera framing. Every decision must be traceable to evidence or be
labelled as an approximation.

The initial CS2 family boundary covers supported **knife** subtypes and the **Glock-18** pistol
adapter. Rifle, SMG, sniper, heavy, glove, unsupported pistol, and unknown knife subtypes must stop
with `unsupported-family` or `unsupported-subtype`; they must not receive another family's component
tree as a generic fallback.

### Layer contract

Pass these records between layers. Do not copy an informal vision description into the next stage:

| Layer | Owns | Must emit | Must not decide alone |
| --- | --- | --- | --- |
| Intake | view validity and technical evidence | role, path/hash, resolution, coverage, duplicate status, admission verdict | item identity from aspect ratio or filename |
| Classification | semantic identity | family, subtype, confidence, evidence refs, provider/version, timeout state | geometry or finish parameters |
| Identity | skin/name/paint metadata | precedence, resolved values, ambiguity candidates, provenance | guessed paint index, float, or seed |
| Surface evidence | pixels and texture sources | de-lit reference, PBR channels, map provenance, colour space, UV orientation, confidence | albedo reused as roughness/normal/AO |
| Geometry adapter | family-specific form | component tree, topology, dimensions, edge/spine, hardware relationships, painted regions | hidden geometry without confidence notes |
| Spec/route | evidence-backed implementation choice | route, exactness tier, assumptions, feature targets, camera contract | exact-texture claim without exact evidence |
| Build/review | rendered observables | fixed view, two non-degenerate orbit views, per-region results, failed gates, next action | overriding a failed critical feature with a global score |

The canonical hand-off is `cs2-intake.json` (`schemaVersion: 1`). Its state is one of
`proceed`, `request-input`, `fallback`, `rejected`, `unsupported-family`, or
`unsupported-subtype`. Write it atomically and preserve unknown provider fields under
`extensions`; a fallback must never erase prior evidence.

### CS2 intake order

1. Admit and technically probe every view. Reject undecodable, empty, tiny, fragmented, or
   duplicate references before classification.
2. Record the heuristic CS2 signal only as a routing hint. `detect_cs2.py` is never authoritative
   identity evidence.
3. Require a classification record before selecting a family adapter. If classification is absent,
   timed out, or contradicts a high-confidence objectness result, return `request-input`.
4. Resolve identity in this order: explicit user metadata, uniquely resolved metadata, then the
   authoritative classification record. Preserve ambiguity rather than guessing.
5. Select route and exactness independently:
   - `reference-projection`: default for matching a specific patterned image;
   - `authored-texture`: only when independent texture maps are supplied or legally acquired;
   - `procedural-finish`: fallback when projection evidence is unavailable or live response is the
     stated priority.
   Exactness is `image-only`, `metadata-assisted`, or `exact-texture`; changing route must not
   silently upgrade or downgrade the evidence tier.
6. Select the family adapter only after family/subtype validation. Record painted regions, unpainted
   substrate, visible hardware, hidden-region confidence, and every approximation in the spec.
7. For projection, solve the camera and de-light the source first. Projected pixels provide colour
   evidence, not automatic geometry truth; geometry still comes from the adapter and silhouette
   review.

### Surface and review rule

For a specific CS2 reference, preserve the reference's own colour/pattern pixels whenever legal and
technically possible. Procedural Doppler/Fade/Gamma/Marble patterns are not equivalent to the input
image and may only be used with an explicit `procedural-finish` route and approximation warning.
Keep albedo, roughness, metalness, normal/height, AO, mask, and wear as independent channels. Record
channel source, colour space, UV orientation, dimensions, packed-channel decoding, and missing-channel
derivation. A low-confidence PBR inference is a refine-input signal, not proof of exact material.

Single-view reconstruction may proceed only when visible identity features are sufficiently covered;
hidden blade sides, underside, and back hardware must carry inference confidence and may trigger
`request-input`. Review the fixed camera plus two meaningful orbit views. Report what changed, which
evidence caused it, what still differs, and choose exactly one next action:
`continue`, `refine-spec`, `refine-code`, `request-input`, or `stop`.

## Gates (do not skip)

- **Suitability + reference integrity**: pass / conditional / reject before any planning
  (`grimoire/intake/validation_rubric.md`), AND every reference admitted via
  `forge/stage1_intake/check_reference_admission.py` (rejects empty/fragmented/tiny/duplicate/
  undecodable refs with a reason). Intake understanding cross-checked by
  `forge/stage1_intake/check_intake_correctness.py` (halts on a confident class contradiction).
- **Divine Eye (the harness heart) — deterministic-first, model-last**: the render evaluator is
  `forge/stage4_review/divine_eye.py` — a zero-token multi-signal ensemble (IoU/scale HARD gates;
  proportion/symmetry-parity/pHash/SSIM/edge/blowout/flat/tonal-parity soft) with self-uncertainty
  (`probe` on signal disagreement) and deterministic routing (`continue`/`refine-spec`/`refine-code`/
  `probe`). The VLM (`forge/stage4_review/vlm_gate.py`) is a gated, calibrated, cross-checked
  last layer: **never consulted on a hard-gate failure**, multi-sample-voted, and can rescue a
  soft near-threshold reject but never grant past a hard geometric failure.
- **Multi-angle or it didn't happen**: a non-planar form must hold from ≥2 camera angles.
  `forge/stage4_review/diagnose_render_multi_angle.py` flags `degenerate-view` when an orbited
  silhouette collapses (a flat plane faking a volume). Orbit angles use reference-free
  self-consistency — never scored against a reference angle the photo doesn't cover.
- **CS2 review contract**: `forge/stage4_review/cs2_review.py` consumes the manifest and
  versioned scene fixture, then blocks wrong family identity, missing projection coverage,
  painted-region mismatch, critical identity-detail failure, finish/material response failure,
  and degenerate orbit form. It records exactness tier, hidden-region confidence, per-region
  confidence, approximation notes, camera, environment hash, exposure, tone mapping, resolution,
  background, and renderer version.
- **Bounded correction loop (token-burn safety)**: `forge/stage4_review/correction_loop.py`
   guarantees termination: hard gates route to `refine-code`; repeated defects and oscillation route
   to `refine-spec`; plateau and the hard ceiling route to `request-input` — never a silent infinite
   burn. Deterministic analysis-by-synthesis parameter fitting and Divine Eye provenance are
   documented in `grimoire/build/analysis_by_synthesis_fitting.md`.
- **Executable Divine Eye fitting**: `fit_against_divine_eye()` in
  `forge/stage4_review/fit_params.py` connects deterministic parameter-to-render callbacks to
  bounded gate-aware Divine Eye optimization. Clean candidates use raw fidelity; hard-gated
  candidates score below all clean results while retaining original fidelity and provenance. The
  returned objective and optional selected raw fidelity are explicit, and each copied record has
  candidate/reference/render provenance. It lazily loads the default evaluator and returns
  normalized raw-fidelity correction-loop provenance without mutating sources.
- **Tier 1 (legacy, still valid)**: "Tier 2 (AI-vision) never runs against a render that has not passed Tier 1." Run `forge/stage4_review/diagnose_render.py` (silhouette IoU/proportion/symmetry/per-part color) and record it (`--spec ... --in-place`) before requesting a comparison sheet; `orchestrate_passes.py check` refuses otherwise.
- **Pre-spec / strict-quality**: blocks code gen until the spec is deep enough for its contract.
- **Screenshot feedback**: `continue` is allowed only with a render + comparison sheet + global
  AI-vision score ≥ threshold (default 0.7) AND every critical feature ≥ its own threshold.
  Details + per-layer scorecard: `grimoire/feedback/render_capture.md`.
- **Action-ready**: build a runtime hierarchy (pivots, sockets, colliders, destruction groups),
  never an inert lump; expose `root.userData.sculptRuntime`. `grimoire/readiness/action_rigging.md`.
- **Procedural rig contract (1.5-alpha)**: for humanoid/character builds, validate the authored
  `joints`/`parents`/`names`/`matrix_local`/packed skin payload with
  `forge/stage5_rig/validate_rig_payload.py` before binding `THREE.Skeleton`. The gate proves
  structural payload integrity only; pose stress, dynamic bounds, readable screenshots, and
  visual likeness remain separate gates. Payload ownership and non-goals:
  `grimoire/readiness/procedural_rigging_contract.md`.
- **Assembly gate (structure, not pixels) — every model ships explodable AND clickable**: this is
  a build requirement, not a per-project extra. Name every mesh; flag surface relief
  `userData.explodeWithParent` so it rides its shell; let a named group of *anonymous* meshes be one
  part while a named group of *named* parts stays a container. Explode and part-picking must share
  one definition of "a part" — if they disagree, both are wrong. Separate parts by SCALING the
  layout about the model centre, never by pushing every part the same distance (that translates the
  arrangement without opening any gap). Then run
  `forge/stage4_review/check_part_coverage.py --spec <spec> --manifest <parts.json>`: it FAILS on a
  specified component that was never built and on two components fused onto one mesh; it warns on
  inventoried details that never reached the spec and on meshes belonging to no named part. This is
  the only gate that scores STRUCTURE — every other one scores pixels, and a single fused mesh
  wearing a projected photo passes all of those. Its limit is honest and must be stated when
  reporting: it proves you built what you specified, never that you specified enough.
  Full contract + the two rules it took a wrong pass to learn: `grimoire/build/geometry_patterns.md`.
- **Attachment**: child appendages (branches/limbs/handles/tubes) need `attachment.parentSocket`,
  `localStart`, `localEnd`, `contactType`, `embedDepth`/`overlap`, `gapTolerance` — no mid-air parts.
  `grimoire/readiness/joint_attachment.md`.
- **Material/lighting**: `grimoire/feedback/shading_realism.md` — independent PBR channels
  (never alias albedo into roughness/normal/AO), macro/meso/micro frequency bands, real lights.
- **Detail inventory**: for `moderate`+ subjects strict-quality blocks code gen until the
  `detailInventory` reaches `targetMinDetails` and every detail maps to a real component/material
  entry (gloss needs low-roughness/clearcoat; fasteners need instancing/micro parts).
- **Character track**: when `primaryDomain` is `character`/`hybrid` (or `--character`), the spec
  author auto-builds a stylized humanoid template (head/neck/torso/arms + hair, glasses,
  headphones, face features), flattened to world space under a hidden root, with per-part
  character materials and character build passes (`proportion-lock`, `feature-placement`).
  strict-quality requires a filled `anatomy` block (head-units, proportions, face landmarks) and
  character feature targets. Suitability routing for humans: `grimoire/intake/validation_rubric.md`
  (stylized vs maximum-likeness). Stylized bust, not a face-copy; refine positions per reference.
The initial CS2 family boundary is **knife only**. Pistol, rifle, SMG, sniper, heavy, glove, and
unknown knife subtypes must stop with `unsupported-family` or `unsupported-subtype`; they must not
receive the knife component tree as a generic fallback.

For every CS2 reconstruction, MUST read the full layer contract, intake order, and surface/review
rule in `grimoire/intake/cs2_intake_contract.md` before intake state can advance.

## Gates (do not skip)

Before any visual review or `continue` decision, MUST read the full gate-by-gate contract in
`grimoire/review/gates_reference.md` (Divine Eye, VLM rescue, multi-angle, CS2 review, bounded
correction, screenshot feedback, assembly, attachment, material, detail inventory, character
track). In short:

- Validate references first (`grimoire/intake/validation_rubric.md`, `check_reference_admission.py`).
- `divine_eye.py` is deterministic-first; the VLM (`vlm_gate.py`) is a gated last layer, never
  consulted on a hard-gate failure.
- A non-planar form must hold from ≥2 angles (`diagnose_render_multi_angle.py`).
- CS2 knife builds also run `cs2_review.py` against the versioned scene fixture.
- Local state enforces 3 corrections per pass and 6 total by default; reaching either limit is a
  hard stop. `correction_loop.py` may stop earlier on repeated defects, oscillation, or plateau.
- `continue` requires a render + comparison sheet + AI-vision score ≥ threshold, every critical
  feature ≥ its own threshold (`grimoire/feedback/render_capture.md`).
- Every model ships explodable AND clickable — a structure gate, not pixels
  (`check_part_coverage.py`, `grimoire/build/geometry_patterns.md`).
- Action-ready, attachment, material/lighting, detail inventory, and character-track requirements:
  `grimoire/readiness/action_rigging.md`, `grimoire/readiness/joint_attachment.md`,
  `grimoire/feedback/shading_realism.md`, `grimoire/intake/quality_contract.md`,
  `grimoire/intake/validation_rubric.md`.

## Self-Correction

After every pass, decide exactly one: `continue | refine-spec | refine-code | request-input | stop`.
`refine-spec` fixes a wrong/missing/shallow spec (re-validate, don't patch code around it);
`refine-code` fixes geometry/material/lighting that doesn't match a sound spec. Before making the
decision, MUST read the root-cause guide + fidelity scale in `grimoire/review/self_correction.md`,
record the decision, and re-run the local state gate.

**Small features need a different instrument.** Divine Eye's SSIM/tonal/edge signals run on a 64×64
luma grid and a 96×96 edge grid, so a detail a few pixels wide in the reference is not scored badly —
it is absent before any comparison happens, and `per_feature.py` cannot compensate because it consumes
a scores dict and never opens an image. When fidelity depends on individual tears, spars, fangs or
eyes, use the four-tier microscope: `grimoire/review/divine_eye_microscope.md`. Two rules there are
empirically established rather than proposed, and both produced confident false findings before being
understood: measure fidelity on a component's **visible footprint** (the full frame minus a
component-hidden frame), never on an isolation render, which reveals geometry the reference cannot
see; and never colour-gate a **concave** feature, where a dark ratio captures cavity shading rather
than material.

## Implementation Rules (brief)

TypeScript + plain Three.js unless the project uses a wrapper. `Group` factory
`createObjectNameModel(spec, options)`, reconstruction data kept separate from renderer objects,
deterministic seeds for all procedural noise. Prefer primitives / `Shape` extrude / curve+tube /
instancing / displacement / generated canvas textures before any external art. Full geometry &
material recipes + hard-won failure patterns: `grimoire/build/geometry_patterns.md`.

### Optional Python ↔ Three.js render bridge

When Python is requested for character rendering, use it as a deterministic job/evidence layer
around the browser Three.js runtime: camera-batch manifests, source/output hashes, readiness and
settle checks, screenshot persistence, masks, diagnostics, and comparison packaging. The target
Three.js browser route remains the rendering authority. Do not silently replace the procedural
TypeScript factory with Blender/VRM/GLB output. Full routing, manifest fields, and failure rules:
`grimoire/build/python_threejs_render_bridge.md`.

### Standard character pipeline (merged 1.5 beta + alpha)

Use `grimoire/readiness/standard_character_pipeline.md` for character work. Beta owns the
strict sculpt/build/review gates; alpha owns deterministic camera manifests, browser screenshot
evidence and UniRig-shaped rig validation. CharacterGen, Tripo, VRM and other neural/asset
systems are opt-in adapters with source, checkpoint, license, coordinate conversion and output
hashes. They never silently replace the procedural TypeScript factory. Image-to-mesh systems emit a
static mesh with no skeleton, so their output is never animation-ready however good it looks; neural
riggers are offline inference whose value here is the serialized payload, not a browser dependency.

Executable entry points are `forge/stage4_review/render_bridge.py` and
`scripts/capture_threejs_playwright.py`. Run `init → browser capture → validate → diagnose`;
capture must operate on the real showcase/browser route and leave readable PNGs in the workspace.

## Output

- **Analysis-only**: suitability verdict + scores, object extraction, macro→micro hierarchy,
  geometry strategy, material/lighting recipe, animation/destruction feasibility, plan + risks.
- **Implementation**: the above briefly, then edit code; verify with typecheck/build + a screenshot.
- **Not feasible**: name the blocker, ask for more views / cleaner image / accepted stylization /
  a narrower target. "This cannot reach the requested fidelity from this image" is a valid result.
