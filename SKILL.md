---
name: img2threejs
description: Turn an object or character reference image into a quality-gated, animation-ready procedural Three.js model built in code. Use for image-to-3D reconstruction, detail-accurate object rebuilds, stylized/likeness-maximized human characters, sculpt specs, and staged code generation.
license: Apache-2.0
version: 1.4.4-beta.3
---

# img2threejs — Image to procedural Three.js

Rebuild the subject visible in reference image(s) as a **code-only** procedural Three.js model,
gated by a staged sculpting pipeline and an AI-vision self-correction loop. This is
reconstruction-by-code, **not** photogrammetry, mesh extraction, downloaded art packs, or a
depth-map/image-shell trick.

Agent-agnostic: works under Claude Code, Codex, or OpenCode. Wherever this doc says "agent
vision" or "agent browser tool", use whatever the host provides — native image reading, a
browser MCP (playwright/chrome-devtools), the project preview, or a user-supplied screenshot.

## When To Use

The user attaches/points to an object image and wants a procedural Three.js model, a
reconstruction/animation/destruction plan, a sculpt spec, or code. Also for material studies,
action-ready props, game objects, botanical/mechanical parts, and stylized reconstructions.

## Core Promise

Sculpt from image evidence, in order — never one-shot a mesh:
1. **Use local state first.** Initialize it once, then run
   `python3 forge/next.py --state .img2threejs/state.json [<spec>]` at every start/resume and before
   every correction iteration. Obey a hard stop; never continue from memory.
2. **Validate** the image is a suitable 3D target (`grimoire/intake/validation_rubric.md`).
3. **Assess** object class + complexity, then write a `qualityContract` before any code.
3. **Spec** it: component hierarchy, materials, lighting, pivots, sockets, action anchors.
4. **Build pass-by-pass** from blockout → structure → form → material → lighting → interaction → optimization.
5. **Verify** each pass with a screenshot compared against the reference; fail a pass if an identity-defining feature is wrong even when the global score looks fine.

The pipeline is subject-generic. Do not require a pre-registered adapter for a new object,
weapon family, mechanism, prop, plant, vehicle, or character. Synthesize a subject adapter and
spec from the evidence before code generation; never route an unfamiliar subject through a
nearby object's component tree. The adapter is a reconstruction contract, not permission to
invent hidden detail. Read `grimoire/intake/adaptive_adapter.md` and
`grimoire/intake/research_evidence.md` whenever the subject has no existing domain template.

Every reconstruction has an explicit artifact root and loop budget. The default artifact root is
`.img2threejs/`; the user's explicit loop budget overrides the generic defaults. For the AWP
reconstruction, set both `maxPerPass` and `maxTotal` to `20` and carry those values into state.

State explicitly when output is approximate/stylized/low-poly. A single image cannot reveal
hidden sides or guarantee exact geometry — say so instead of faking confidence.

## Transparency and Process Debugging

Report what changed each pass with evidence (exact values/coordinates), name what still doesn't
match, and never claim "done" when only "improved". A passing gate is not proof of 3D realism.
Full rule + examples: `grimoire/review/self_correction.md`.

## Required Inputs

- one image path / screenshot / URL / attached image (if missing or unreadable, ask)
- intended use: prop, game object, hero render, playable/destructible object, animation rig
  (default: real-time browser prop with interactive performance)
- for a CS2 request, an authoritative classification record (family/subtype and evidence refs) is
  preferred for identity/metadata, but it does not replace subject-adapter synthesis

## Mandatory Local State Gate

Conversation context is disposable; `.img2threejs/state.json` is the local checklist authority.
Initialize it once per reconstruction:

`python3 forge/state.py init --state .img2threejs/state.json --reference <img> --profile <generic|cs2|character> --max-per-pass <n> --max-total <n>`

Use `--max-per-pass 20 --max-total 20` for the AWP task. Never silently fall back to the
skill's default `3/6` when the task or state explicitly declares another budget. Record the
artifact root in `state.artifacts.root` and keep generated evidence under that root. Read
`grimoire/intake/artifact_storage.md` before creating outputs.

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

## The Loop (scripts do enforcement; agent vision does judgment)

Run scripts from the skill root (`forge/...`). Pure Python 3.10+ stdlib, no pip installs.
Full flags: `grimoire/scripts.md`. Never let a script *score* visuals — that is the agent's job.

1. **Analyze the image first** (agent vision, before any script): work the layered observation
   protocol in `grimoire/intake/image_analysis.md` — identify/classify, decompose macro→meso→micro,
   map part relationships, name materials in PBR terms, list identity-defining features, and flag
   what the single view hides. Observation before inference; controlled 3D vocabulary; 3D
   object-space not 2D image-space. This is generic for any subject and feeds every field below.
   Then probe local images: `forge/stage1_intake/probe_image.py <image>` (metadata only, not a visual check).
1a. **Local Spec Search** — after image analysis, before writing or refining a spec, pull local
    domain evidence (anatomy/PBR/wear/geometry/runtime/physics) rather than inventing it:
    `python3 forge/stage2_spec/new_pre_spec_assessment.py "Name" --image <img> --out assessment.json`
    (auto-runs BM25, auto-picks `cs2`/`core_3d` collection, writes a `localSpecSearch` bundle that
    `new_sculpt_spec.py --assessment` carries into the spec). Full query-expansion recipe
    (bilingual terms, focused `search_specs.py` retrieval, cache rules):
    `grimoire/intake/local_spec_search.md`. MUST read it before retrying an incomplete or
    domain-specific query.
1b. **Subject-adapter synthesis** — if no adapter exactly matches the subject, create one now.
    Classify the subject at the highest justified level (`object`, `character`, or `hybrid`),
    enumerate macro/meso/micro components, write the parent-child/contact graph, select a
    topology-appropriate recipe for every component, and define the review crops and viewpoints.
    Store this as `subjectAdapter`/`adapterContract` in the spec with `mode: llm-synthesized`,
    evidence references, research references, and per-region confidence. A new adapter is a
    normal continuation path, not an `unsupported-family` stop. Read
    `grimoire/intake/adaptive_adapter.md`.
1c. **CS2 intake manifest** — for a CS2 request, create and validate `cs2-intake.json` before
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
2c. **Projection-first fidelity (characters and reference-matched surfaces)** — when the goal is
   matching a specific reference's surface, put the photo's own pixels on the authored mesh
   instead of approximating them procedurally. This is the single biggest fidelity lever; a
   procedural material for a patterned surface is the #1 reconstruction failure. Projection is
   a surface/UV operation only: it must be bound to the real component mesh, survive orbit views,
   and never create a camera-only shell or substitute for missing form. Read
   `grimoire/review/no_cheat_geometry.md` before selecting projection or decals.
   Recipe (`grimoire/character/likeness_maximization.md` — its two levers, align-mesh+camera and
   project-the-photo, generalize past characters): solve the camera
   (`stage1_intake/solve_camera_pose.py` → `referenceCamera`), **de-light** the reference so it is
   free of baked lighting (`stage1_intake/delight_albedo.py`, hard requirement — this is what makes
   projection safe, not the flat-lit icon), then project the de-lit crop onto the mesh and bake it
   into UVs (`stage3_build/bake_projected_texture.py --mesh-id <id>`). For a patterned object finish,
   the projected de-lit crop IS the finish only after the real component mesh and UV binding pass.
   For characters, first capture
   landmarks (`stage1_intake/extract_landmarks.py --out anatomy.json`), fill `preSpecAssessment.anatomy`,
   route `grimoire/character/reconstruction.md`. A single view cannot show hidden sides — report
   per-region confidence and request more views when it matters.
3. Author the spec from the assessment and synthesized adapter:
   `forge/stage2_spec/new_sculpt_spec.py "Name" --image <img> --assessment assessment.json --manifest cs2-intake.json --out object-sculpt-spec.json`.
   Replace generic starter `featureReviewTargets` with the object's real identity-defining
   systems (≤5 critical, ≤3 important per pass); for characters add `anatomy-proportion`,
   `face-landmark-placement`, `pose-silhouette`, `outfit-and-palette`. Use 3D-graphics terms only
   (`grimoire/glossary/3d_vocabulary.md`), never "nice/smooth/shiny". Classify every component's
   `topologyClass`/`topologyRationale` per `grimoire/intake/surface_topology.md` before picking a
   `primitive` — this is what prevents a continuous organic form from being picked as a box. The
   spec must also carry the adapter contract and evidence ledger; a generic starter tree is never
   the final subject tree.
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
5. Validate, then strict-validate before generating code:
   `forge/stage2_spec/validate_sculpt_spec.py object-sculpt-spec.json` then `--strict-quality`.
   Validate the synthesized adapter as well:
   `forge/stage2_spec/adaptive_adapter.py --spec object-sculpt-spec.json`.
   Strict blocks shallow specs (a complex object with one root, no repetition systems, no
   local overrides, no micro groups is NOT implementation-ready even if JSON validates).
6. **Locked build passes** — only touch the currently unlocked pass, and lock identity-defining
   components as they pass their own crop/attachment gate before opening the next component:
   `forge/stage3_build/orchestrate_passes.py status object-sculpt-spec.json`
   `forge/stage3_build/generate_threejs_factory.py object-sculpt-spec.json --out src/createObjectModel.ts`
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
   `forge/stage3_build/orchestrate_passes.py check object-sculpt-spec.json --pass-id <pass>`. Also
   run the no-cheat geometry/attachment checks; a passing AABB or metadata check alone does not
   prove visual contact or true 3D form.
9. Package one side-by-side sheet under `.img2threejs/`, then inspect it with agent vision:
   `forge/stage4_review/make_comparison_sheet.py --reference <img> --render <shot> --out .img2threejs/reviews/<pass>/comparison.png --json`.
10. Record the review (overall + per-layer + per-feature scores + decision) in the spec/evidence
    under `.img2threejs/`:
    `forge/stage4_review/append_review.py object-sculpt-spec.json --pass-id <pass> --fidelity <0-1> --action <continue|refine-spec|refine-code|request-input|stop> --summary "..." --render-screenshot .img2threejs/renders/<pass>.png --comparison-image .img2threejs/reviews/<pass>/comparison.png --ai-vision-score <0-1> --layer-scores-json '{...}' --feature-reviews-json .img2threejs/reviews/<pass>/features.json --in-place`.
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

## Artifact and loop rule

Generated images and information belong in `.img2threejs/` beside `state.json`: renders, crops,
comparison sheets, orbit audits, manifests, review JSON, research notes, confidence ledgers,
diagnostic reports, and NotebookLM exports. Use stable subfolders such as `renders/`, `reviews/`,
`audit/`, `research/`, and `manifests/`. Do not write generated evidence into `src/`, a demo's
`review/` directory, or an arbitrary temporary folder. Keep source code, committed skill assets,
and the user-provided original reference paths outside this rule; record their paths in state.

The loop budget is a task contract, not a license to bypass gates. A loop may be spent only after a
recorded review action and state sync. Do not advance a pass because the budget remains; do not stop
early merely because the generic default is smaller than the explicit task budget.

## Generic image-matched rule

For every subject, target observable agreement between supplied image(s) and the rendered item:
silhouette, proportions, edge profile, component layout, material response, local detail placement,
and camera framing. Every decision must be traceable to direct image evidence, a cited research
source, or an explicitly labelled inference. A single image cannot prove hidden geometry; infer a
plausible 3D continuation with confidence rather than claiming exactness.

## CS2 image-matched rule

For a CS2 item, the target is observable agreement between the supplied image and the rendered
item: silhouette, proportions, edge profile, hardware layout, coating colour, pattern placement,
wear, roughness response, and camera framing. Every decision must be traceable to evidence or be
labelled as an approximation.

CS2 family adapters are domain templates, not a limit on the generic pipeline. If a CS2 family or
subtype has no registered template, synthesize a `subjectAdapter` from the image and CS2 evidence
before authoring the spec. Do not route it through the knife, pistol, or rifle tree by analogy, and
do not let a family registration claim geometry coverage that has not been measured. A generated
adapter must carry its own component tree, attachment rules, critical features, review viewpoints,
and confidence ledger.

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
- CS2 builds also run `cs2_review.py` against the versioned scene fixture when one exists; if the
  family has no fixture yet, synthesize a subject-specific review fixture before `continue`.
- Local state enforces 3 corrections per pass and 6 total by default; reaching either limit is a
  hard stop. `correction_loop.py` may stop earlier on repeated defects, oscillation, or plateau.
- `continue` requires a render + comparison sheet + AI-vision score ≥ threshold, every critical
  feature ≥ its own threshold (`grimoire/feedback/render_capture.md`).
- Every model ships explodable AND clickable — a structure gate, not pixels
  (`check_part_coverage.py`, `grimoire/build/geometry_patterns.md`).
- No-cheat geometry is a hard gate: no depth-map extrusion, camera-only image shell, floating
  decal, or texture used as a replacement for form-critical geometry. See
  `grimoire/review/no_cheat_geometry.md`.
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

## Implementation Rules (brief)

TypeScript + plain Three.js unless the project uses a wrapper. `Group` factory
`createObjectNameModel(spec, options)`, reconstruction data kept separate from renderer objects,
deterministic seeds for all procedural noise. Prefer primitives / `Shape` extrude / curve+tube /
instancing / displacement / generated canvas textures before any external art. Generate a new
adapter when the subject is novel; record the evidence/research that caused each non-observed
decision. Full geometry & material recipes + hard-won failure patterns:
`grimoire/build/geometry_patterns.md`.

## Output

- **Analysis-only**: suitability verdict + scores, object extraction, macro→micro hierarchy,
  geometry strategy, material/lighting recipe, animation/destruction feasibility, plan + risks.
- **Implementation**: the above briefly, then edit code; verify with typecheck/build + a screenshot.
- **Not feasible**: name the blocker, ask for more views / cleaner image / accepted stylization /
  a narrower target. "This cannot reach the requested fidelity from this image" is a valid result.
