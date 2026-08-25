---
name: img2threejs
description: Turn an object or character reference image into a quality-gated, animation-ready procedural Three.js model built in code. Use for image-to-3D reconstruction, detail-accurate object rebuilds, stylized/likeness-maximized human characters, sculpt specs, and staged code generation.
license: Apache-2.0
version: 1.5.1
---

# img2threejs — Image to procedural Three.js

Rebuild the object visible in a reference image as a **code-only** procedural Three.js model,
gated by a staged sculpting pipeline and an AI-vision self-correction loop. This is
reconstruction-by-code, **not** photogrammetry, mesh extraction, or downloaded art packs.

Agent-agnostic: works under Claude Code, Codex, or OpenCode. Wherever this doc says "agent
vision" or "agent browser tool", use whatever the host provides — native image reading, a
browser MCP (playwright/chrome-devtools), the project preview, or a user-supplied screenshot.

This file is the always-loaded router: it holds the order of operations and every hard rule as one
line. The full contract behind each rule lives in the `grimoire/` or `docs/` file that rule names —
read the named file at the moment you reach that stage, not before.

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
1. **Run `python3 forge/next.py --state .img2threejs/state.json [<spec>]` first**, at every start,
   resume, and before every correction iteration. It reports the ordered checklist, exact next
   command, evidence status, and bounded correction-loop status; it never replaces the spec/pass
   gates. Obey a hard stop; never continue from memory.
2. **Validate** the image is a suitable 3D target (`grimoire/intake/validation_rubric.md`).
3. **Assess** object class + complexity, then write a `qualityContract` before any code.
4. **Spec** it: component hierarchy, materials, lighting, pivots, sockets, action anchors.
5. **Build pass-by-pass** from blockout → structure → form → material → lighting → interaction → optimization.
6. **Verify** each pass with a screenshot compared against the reference; fail a pass if an
   identity-defining feature is wrong even when the global score looks fine.

State explicitly when output is approximate/stylized/low-poly. A single image cannot reveal
hidden sides or guarantee exact geometry — say so instead of faking confidence.

## Mandatory Local State Gate

Conversation context is disposable; `.img2threejs/state.json` is the local checklist authority.
Initialize once per reconstruction, then gate every step through it:

```bash
python3 forge/state.py init --state .img2threejs/state.json --reference <img> --profile <generic|cs2|character> --spec object-sculpt-spec.json
python3 forge/next.py --state .img2threejs/state.json [object-sculpt-spec.json]
python3 forge/state.py mark <step-id> --state .img2threejs/state.json --evidence <path>
```

- `next.py` prints the current step, pass, incomplete mandatory steps, exact next command, and
  `loop/max`. Exit code 3 or `status=stopped` is a hard stop: report the reason and request input.
  Never bypass it by reconstructing progress from chat history.
- Every completed step needs evidence; mark a non-applicable step `skipped` only with `--reason` —
  silent omission is forbidden. Loop counts derive from `reviewHistory` actions
  (`refine-spec`/`refine-code`), not agent memory. Defaults: 3 corrections per pass, 6 total.
- Profiles add mandatory gates without changing the core order: `cs2` requires classification,
  manifest, and a machine-readable CS2 review before AI review; `character` requires the character
  contracts and landmark evidence. Every profile records suitability, projection applicability, and
  material-evidence applicability. The state file is a resumability index, not visual evidence:
  renders, specs, review history, and deterministic gates remain the authoritative artifacts.

## Required Inputs

- one image path / screenshot / URL / attached image (if missing or unreadable, ask)
- intended use: prop, game object, hero render, playable/destructible object, animation rig
  (default: real-time browser prop with interactive performance)
- for a CS2 request, an authoritative classification record (family/subtype and evidence refs) or
  an explicit request for the user/vision provider to supply one; heuristic detection alone is not
  enough to select a geometry adapter

## The Loop (scripts do enforcement; agent vision does judgment)

Run scripts from the skill root (`forge/...`). Pure Python 3.10+ stdlib, no pip installs.
Full flags: `grimoire/scripts.md`. Never let a script *score* visuals — that is the agent's job.

1. **Analyze the image first** (agent vision, before any script): work the layered observation
   protocol in `grimoire/intake/image_analysis.md` — identify/classify, decompose macro→meso→micro,
   map part relationships, name materials in PBR terms, list identity-defining features, and flag
   what the single view hides. Observation before inference; controlled 3D vocabulary; 3D
   object-space not 2D image-space. Then probe local images:
   `forge/stage1_intake/probe_image.py <image>` (metadata only, not a visual check).
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
1c. **Optional fidelity evidence adapters** — only when they improve an observed weak point; the
    stdlib core remains authoritative. Thin/complex masks → local SAM2; character face/pose →
    MediaPipe; weak front/back cues → Depth Anything V2
    (`forge/stage1_intake/run_vision_adapter.py <segment|landmarks|depth> ...`; every adapter emits
    provenance; monocular depth is relative only). **MCP-only scene mutations never count as
    implementation** — write the proven change back to the spec or TypeScript, rebuild, recapture.
    Full adapter + MCP routing and authority boundaries:
    `docs/integrations/reference_fidelity_tooling.md`.
2. **Pre-Spec Assessment Gate** — classify + score complexity + write the quality contract:
   `forge/stage2_spec/new_pre_spec_assessment.py "Name" --image <img> --complexity <simple|moderate|complex|ultra-complex> --out assessment.json`. Rules: `grimoire/intake/quality_contract.md`.
   Set `objectClass.primaryDomain` (`object` | `character` | `hybrid`) and fill the seeded
   `detailInventory` (its `targetMinDetails` scales with complexity). **Supported CS2 knife skins
   and Glock-18 assets**: always pass `--cs2`, which defaults the complexity tier to `ultra-complex`
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
2c. **Projection-first fidelity** (characters AND reference-matched surfaces — supported CS2 skins,
   decals, painted patterns) — when the goal is matching a specific reference's surface, put the
   photo's own pixels on the mesh instead of approximating them procedurally. This is the single
   biggest fidelity lever; a procedural material for a patterned surface is the #1 reconstruction
   failure. Recipe (`grimoire/character/likeness_maximization.md` — its two levers generalize past
   characters): solve the camera (`stage1_intake/solve_camera_pose.py` → `referenceCamera`),
   **de-light** the reference (`stage1_intake/delight_albedo.py`, hard requirement — de-lighting is
   what makes projection safe), then project the de-lit crop and bake it into UVs
   (`stage3_build/bake_projected_texture.py --mesh-id <id>`). For a CS2 skin the projected de-lit
   crop IS the finish — no procedural Doppler material. For characters, first capture landmarks
   (`stage1_intake/extract_landmarks.py --out anatomy.json`), fill `preSpecAssessment.anatomy`,
   route `grimoire/character/reconstruction.md`. A single view cannot show hidden sides — report
   per-region confidence and request more views when it matters.
   Character sub-routes, in order — decide what parts exist before shaping any, and shape the head
   before the hair that sits on it:
   - **Parts** — `grimoire/character/structure_decomposition.md`
   - **Head** — `grimoire/character/head_construction.md` (what the likeness gate reads against)
   - **Hair** — `grimoire/character/stylized_hair_threejs.md` + parameter contract in
     `grimoire/character/threejs_hair_parameter_contract.json`. Lock topology only after the
     silhouette review passes: material tuning cannot repair wrong lock topology.
2d. **Reference-free humanoid** — a generic figure with no reference image has nothing to measure,
   so fill anatomy from public canon:
   `forge/stage2_spec/humanoid_proportions.py <spec> --style-heads 8 --in-place`. It writes
   `anatomy.source: "canon-table"` so canon is never mistaken for measurement, refuses to run when
   the spec names a reference image, and names anything the corpus does not supply rather than
   interpolating it.
3. Author the spec from the assessment:
   `forge/stage2_spec/new_sculpt_spec.py "Name" --image <img> --assessment assessment.json --manifest cs2-intake.json --out object-sculpt-spec.json`.
   Replace generic starter `featureReviewTargets` with the object's real identity-defining
   systems (≤5 critical, ≤3 important per pass); for characters add `anatomy-proportion`,
   `face-landmark-placement`, `pose-silhouette`, `outfit-and-palette`. Use 3D-graphics terms only
   (`grimoire/glossary/3d_vocabulary.md`), never "nice/smooth/shiny". Classify every component's
   `topologyClass`/`topologyRationale` per `grimoire/intake/surface_topology.md` before picking a
   `primitive` — this is what prevents a continuous organic form from being picked as a box.
4. When material fidelity matters and a source image exists, analyze each material's **finish** then
   extract reference PBR evidence, both per crop (verify the crop is on the part you think it is):
   - `forge/stage1_intake/analyze_texture.py <crop> --spec spec.json --material-id <id> --in-place`
     classifies the finish, extracts the gradient palette, and writes doc-grounded
     MeshPhysicalMaterial scalars onto the material. Recipes + Three.js texture/PBR rules:
     `grimoire/build/threejs_texture_reference.md`. Rule of thumb: **solid albedo for flat paint,
     real reference crop for patterned finishes**.
   - `forge/stage1_intake/extract_pbr_evidence.py <crop> --out-dir <dir> --material-id <id> --target-threshold 0.7`.
     Confidence < 0.7 is a stop/refine-input signal, not a pass. It is inference, not inverse rendering.
   - For multiple named regions: `forge/stage1_intake/material_region_analysis.py --manifest regions.json --out-dir material-evidence --out material-analysis.json`,
     resolve each assignment from `docs/materials/material-reference.json`, wire it in with
     `forge/stage2_spec/apply_material_analysis.py`.
   - Emit the controlled material camera/crop contract (`forge/stage4_review/material_views.py`),
     compare visible-footprint crops (`material_comparator.py`), apply only bounded material-scoped
     corrections (`material_feedback.py`), and record the blocking result (`material_gate.py`).
5. Validate, then strict-validate before generating code:
   `forge/stage2_spec/validate_sculpt_spec.py object-sculpt-spec.json` then `--strict-quality`.
   Strict blocks shallow specs (a complex object with one root, no repetition systems, no
   local overrides, no micro groups is NOT implementation-ready even if JSON validates).
6. **Locked build passes** — only touch the currently unlocked pass:
   `forge/stage3_build/orchestrate_passes.py status object-sculpt-spec.json`
   `forge/stage3_build/generate_threejs_factory.py object-sculpt-spec.json --out src/createObjectModel.ts`
   The generator is fail-closed: `strict-quality` must pass before it writes any factory, and a
   future `--pass-id` fails until prior passes are reviewed `continue`. If blocked, preserve the
   `BLOCKED` artifact and refine the subject-specific spec; do not substitute a generic template.
   The local state adds `--force` only for a new pass or `refine-spec`; `refine-code` edits the
   current artifact without regenerating it. Before overwriting, carry valid hand refinement back
   into the spec; generated code must not be the only copy of reconstruction decisions.
6a. **Hitting a triangle budget.** `performanceBudget.targetTriangles` selects a tessellation tier
   for every primitive with segment counts (low ≤6k, standard ≤60k, else hero) and caps
   implicit-surface sampling grids. Where a tier is not precise enough, add
   `geometryDescriptor.decimate: {"targetRatio": 0.4}` to that component — a quadric collapse in
   the generated factory, run **before** skin binding so weights are computed on surviving
   vertices. It keeps `position` only (normals recomputed), so it is refused on an
   authored/unwrapped `uvStrategy`. Offline LOD tiers:
   `forge/stage3_build/decimate.py <mesh.json> --ratio <r> --json`.
7. Render the current pass in a browser/preview, capture a screenshot at a review viewpoint.
7a. **Off-axis and placement gates — a single review viewpoint is not evidence about the model.**
   Capture a turntable, not one frame, and run all three; each catches a defect class the older
   gates pass by construction (a hole through a skull, a hat at hip height and a floating charm all
   survived eight front-only review rounds):
   `forge/stage4_review/turntable_gate.py --capture 0=front.png --capture 90=right.png --capture 180=rear.png --capture 270=left.png --json`
   `node runtime/scripts/export_mesh_geometry.mjs --url <preview> --out meshes.json` then
   `forge/stage4_review/self_intersection.py meshes.json --json`
   `forge/stage4_review/attachment_anchor.py object-sculpt-spec.json --measured measured.json --json`
   All three exit `0` clean / `1` gate failure / `2` error. A failure blocks `continue` even when
   the global fidelity score passes. Read `sampledVertexCount` / `unmeasuredAttachments` /
   `missingAzimuths` before believing a clean verdict: each names what the gate did not look at.
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
    For the CS2 family path, produce the versioned report first with
    `forge/stage4_review/cs2_review.py --manifest cs2-intake.json --metrics cs2-review-inputs.json --scene forge/tests/fixtures/knife_review_scene.json --out cs2-review.json`
    and attach it with `--cs2-review-json cs2-review.json --review-scene-json forge/tests/fixtures/knife_review_scene.json`.
    A failed family, painted-region, projection-coverage, critical-detail, or orbit gate blocks
    `continue` even when the global score passes. See `docs/cs2/review-gates.md`.
11. Sync pipeline state after manual review edits, record checklist evidence, then re-run the local
    state gate before another correction or pass:
    `forge/stage3_build/orchestrate_passes.py sync object-sculpt-spec.json --in-place`
    `python3 forge/next.py --state .img2threejs/state.json object-sculpt-spec.json`.
12. Before declaring completion, run
    `forge/stage4_review/check_part_coverage.py --spec object-sculpt-spec.json --manifest parts.json`
    and verify the action-ready hierarchy. Mark `part-coverage` and `action-ready` only with evidence.

## GLB-mediated v2 render-fidelity track (1.5 alpha)

When the user supplies a GLB as an intermediate reference, the browser-rendered GLB is the
structural and visual baseline for an independently authored procedural factory. The raw GLB is
never pixel evidence and its topology/materials are never copied into the factory. Before any
factory edit — full contract in `grimoire/build/python_threejs_render_bridge.md`, machine-readable
schema in `docs/specs/render-profile.v2.schema.json` (+ example; fail-closed validation):

1. `forge/stage1_intake/probe_glb.py` first. A merged one-node/one-mesh asset is `insufficient`
   for semantic labels; request a multipart GLB or a browser semantic-ID pass before claiming
   exact regions.
2. Author ONE shared `render-profile.v2` (`forge/stage4_review/validate_render_profile.py`) used by
   both the GLB and procedural routes. Region IDs are subject-specific, never inherited from the
   example profile; declare the required set in `extensions.requiredSemanticRegions` so omission is
   a hard validation error.
3. Capture six passes per admitted view (`beauty`, `alpha-silhouette`, `semantic-id`, `depth`,
   `normal`, `roughness-material-id`); score with `forge/stage4_review/compare_region_passes.py`.
   Missing semantic-ID data blocks per-region confidence rather than falling back to whole-image
   scores.
4. Use region-specific continuous geometry — never replace a face/head volume, cloth shell, or tail
   with floating primitives when the region's silhouette requires a continuous surface.
5. Run ONE correction group per loop, in order: `camera → silhouette → face → clothing → accessory
   → materials → lighting`; recapture the full pass set after each group and record the changed
   group, hashes and score. Never combine groups when diagnosing improvement.

## Gates (do not skip)

Before any visual review or `continue` decision, MUST read the full gate-by-gate contract in
`grimoire/review/gates_reference.md` (Divine Eye, VLM rescue, multi-angle, interior difference,
chirality, hair, CS2 review, bounded correction, Divine Eye fitting, screenshot feedback, assembly,
attachment, material, detail inventory, rig payload, character track). In short:

- Validate references first (`grimoire/intake/validation_rubric.md`, `check_reference_admission.py`).
- `divine_eye.py` is deterministic-first; the VLM (`vlm_gate.py`) is a gated last layer, never
  consulted on a hard-gate failure.
- A non-planar form must hold from ≥2 angles (`diagnose_render_multi_angle.py`).
- Measure INSIDE the silhouette every visual pass (`interior_difference.py`). Silhouette IoU reads
  ~11% of figure cells: a model with its face deleted scored the same 0.8803 as the finished face.
- Every `-l`/`-r` pair is a MIRROR, not a rotation — hard at spec time (`validate_chirality`). A pair
  wrong the same way on both sides still passes, and needs `medial_lateral_bias` vs a reference.
- Hair subjects: `scalp_exposure.py` is HARD and runs on geometry before any render; `hair_gate.py`
  is soft and subordinate to it. A coverage shortfall never authorises widening the masses.
- Flat colour regions with hard boundaries (blaze/bib/socks, livery stripe, painted marking) are an
  identity feature, so their boundaries are gated on geometry: `vertex_region_gate.py`. Never a
  texture — this pipeline emits code; the shape predicates live in `_shared/vertex_paint.py`.
- A curve claim ("curled into a hook, not a straight cone") needs `swept_arc_gate.py`: silhouette IoU
  passes a straight cone occupying roughly the right cells.
- Character builds validate the rig payload (`stage5_rig/validate_rig_payload.py`) before binding a
  `THREE.Skeleton`; it proves payload integrity only, never pose stress or likeness.
- CS2 builds also run `cs2_review.py` against the versioned scene fixture.
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
luma grid, so a detail a few pixels wide is absent before any comparison happens. When fidelity
depends on individual tears, spars, fangs or eyes, use the four-tier microscope:
`grimoire/review/divine_eye_microscope.md`. Two empirically established rules from it: measure
fidelity on a component's **visible footprint** (full frame minus a component-hidden frame), never
on an isolation render; and never colour-gate a **concave** feature, where a dark ratio captures
cavity shading rather than material.

## Transparency and Process Debugging

Report what changed each pass with evidence (exact values/coordinates), name what still doesn't
match, and never claim "done" when only "improved". A passing gate is not proof of 3D realism.
Full rule + examples: `grimoire/review/self_correction.md`.

## Left and right

A left/right pair is a **reflection**, never a rotation: negate the lateral axis and nothing else,
`(x, y, z) → (-x, y, z)`. With `forward: +Z`, Y up and a right-handed frame, the character's own
left is **+X**. The convention lives as code in `forge/_shared/chirality.py`
(`CHARACTER_LEFT_SIGN`), with two different gates for the two defects that shipped from getting it
wrong: `validate_chirality` catches a rotation-mistaken-for-reflection at spec time, and
`medial_lateral_bias` vs a reference catches a pair that is wrong the same way on both sides.
Reflecting also inverts triangle winding — flip it back on the mirrored side or `flatShading`
lights the limb as though lit from behind. Full write-up with the measured defects:
`grimoire/scripts.md` ("Left and right").

## Hair

Hair has its own subsystem because it has failure modes no other gate can see. Full contract,
measurements and non-goals: `docs/HAIR_PIPELINE.md`. The hard rules:

- Roots bind to the scalp as `(u, v)`, never absolute positions (hard validation error).
- `standProud` is enforced by the generator, not advisory.
- `scalp_exposure.py` is a HARD gate on geometry before any render; a coverage shortfall never on
  its own authorises widening the masses.
- Default representation tier is `shell`, not locks; strand impression comes from faceting and
  material (`hair.human.code-only`), since this skill emits no textures.
- `plane-card` is rejected for hair (needs an alpha texture this skill cannot emit).
- Hair is rigidly parented, never smooth-skinned (the geodesic field runs through the skull).

## CS2 image-matched rule

For a CS2 item, the target is observable agreement between the supplied image and the rendered
item: silhouette, proportions, edge profile, hardware layout, coating colour, pattern placement,
wear, roughness response, and camera framing. Every decision must be traceable to evidence or be
labelled as an approximation.

The initial CS2 family boundary covers supported **knife** subtypes and the **Glock-18** pistol
adapter. Rifle, SMG, sniper, heavy, glove, unsupported pistol, and unknown knife subtypes must stop
with `unsupported-family` or `unsupported-subtype`; they must not receive another family's component
tree as a generic fallback.

The full layer contract (what each layer owns, must emit, and must never decide alone), the CS2
intake order, and the surface/review rule live in `grimoire/intake/cs2_intake_contract.md` — step
1b already requires reading it completely before intake state can advance. The canonical hand-off
is `cs2-intake.json` (`schemaVersion: 1`, states `proceed | request-input | fallback | rejected |
unsupported-family | unsupported-subtype`); write it atomically, preserve unknown provider fields
under `extensions`, and never let a fallback erase prior evidence.

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
static mesh with no skeleton, so their output is never animation-ready however good it looks.
Executable entry points: `forge/stage4_review/render_bridge.py` and
`scripts/capture_threejs_playwright.py` (`init → browser capture → validate → diagnose`; capture
must operate on the real showcase/browser route and leave readable PNGs in the workspace).

## Output

- **Analysis-only**: suitability verdict + scores, object extraction, macro→micro hierarchy,
  geometry strategy, material/lighting recipe, animation/destruction feasibility, plan + risks.
- **Implementation**: the above briefly, then edit code; verify with typecheck/build + a screenshot.
- **Not feasible**: name the blocker, ask for more views / cleaner image / accepted stylization /
  a narrower target. "This cannot reach the requested fidelity from this image" is a valid result.
