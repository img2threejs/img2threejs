---
name: img2threejs
description: Turn an object or character reference image into a quality-gated, animation-ready procedural Three.js model built in code. Supports combined workflow with image-generation tools (Agnes, DALL-E) to produce reference images before 3D reconstruction. Use for image-to-3D reconstruction, detail-accurate object rebuilds, stylized/likeness-maximized human characters, sculpt specs, and staged code generation.
license: Apache-2.0
version: 1.4.3
---

# img2threejs — Image to procedural Three.js

Rebuild the object visible in a reference image as a **code-only** procedural Three.js model,
gated by a staged sculpting pipeline and an AI-vision self-correction loop. This is
reconstruction-by-code, **not** photogrammetry, mesh extraction, or downloaded art packs.

Agent-agnostic: works under Claude Code, Codex, or OpenCode. Wherever this doc says "agent
vision" or "agent browser tool", use whatever the host provides — native image reading, a
browser MCP (playwright/chrome-devtools), the project preview, or a user-supplied screenshot.

## When To Use

The user attaches/points to an object image and wants a procedural Three.js model, a
reconstruction/animation/destruction plan, a sculpt spec, or code. Also for material studies,
action-ready props, game objects, botanical/mechanical parts, and stylized reconstructions.

## Host Model Capability Requirement

**This skill requires an AI agent with vision capability to complete the full pipeline.**

The staged sculpting pipeline relies on agent vision at two critical points:
1. **Image analysis** (step 1) — identify/classify the object, decompose structure, list identity-defining details
2. **Render review** (step 9) — score the rendered comparison sheet against the reference

Without vision, the pre-spec assessment JSON will have `primaryDomain`, `primaryType`, and
all `complexity.scores` fields left as `"unassessed"` or `0`. The non-visual scripts
(`probe_image.py`, `validate_sculpt_spec.py`, `new_pre_spec_assessment.py`) can still run
and produce structured scaffolding, but the spec authoring (step 3) and review loop will stall.

### Vision fallback strategies

| Scenario | Workaround |
|---|---|
| Host is text-only (e.g. DeepSeek-V4-Free) | **Recommended: use the dedicated vision backend** (`python3 vision_query.py <image> "<prompt>"`). See [Dedicated Vision Backend](#dedicated-vision-backend) below. Alternatively, use an image-generation skill (Agnes, DALL-E, StableDiffusion) to produce the reference, then run the non-visual scripts manually. |
| Host has limited vision | The "Divine Eye" deterministic harness (`forge/stage4_review/divine_eye.py`) can replace some VLM review passes with zero-token geometric checks (IoU, pHash, SSIM). Also use `vision_query.py` for any visual judgment the host struggles with. |
| No browser for rendering | Use the agent's project preview, an MCP browser tool (Playwright/Chrome DevTools), or a user-supplied screenshot as the render capture. |

**Known working hosts**: Claude Code (native image), Codex (project preview), Cursor,
any agent with a browser MCP or screenshot tool.

**Text-only hosts with dedicated backend**: DeepSeek-V4-Free, and any text-only model that
has `vision_query.py` configured — the full pipeline becomes viable because vision-dependent
steps are delegated to the dedicated backend automatically.

## Core Promise

Sculpt from a photo, in order — never one-shot a mesh:
1. **Run `python3 forge/next.py <spec>` first.** It reports the current unlocked pass, exact next command, and unmet acceptance criteria.
2. **Validate** the image is a suitable 3D target (`grimoire/intake/validation_rubric.md`).
3. **Assess** object class + complexity, then write a `qualityContract` before any code.
3. **Spec** it: component hierarchy, materials, lighting, pivots, sockets, action anchors.
4. **Build pass-by-pass** from blockout → structure → form → material → lighting → interaction → optimization.
5. **Verify** each pass with a screenshot compared against the reference; fail a pass if an identity-defining feature is wrong even when the global score looks fine.

State explicitly when output is approximate/stylized/low-poly. A single image cannot reveal
hidden sides or guarantee exact geometry — say so instead of faking confidence.

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

## Required Inputs

- one image path / screenshot / URL / attached image (if missing or unreadable, ask)
- intended use: prop, game object, hero render, playable/destructible object, animation rig
  (default: real-time browser prop with interactive performance)
- for a CS2 request, an authoritative classification record (family/subtype and evidence refs) or
  an explicit request for the user/vision provider to supply one; heuristic detection alone is not
  enough to select a geometry adapter

## Reference Image Best Practices

The quality of the output Three.js model depends heavily on the reference image.

### Strong vs weak subjects

| Subject type | img2threejs quality | Notes |
|---|---|---|
| **Hard-surface objects** (weapons, tools, vehicles, electronics, furniture) | ⭐⭐⭐⭐⭐ | Best results. Clear component hierarchy, defined edges, distinct materials. |
| **Styled characters** (cartoon, game figurine) | ⭐⭐⭐ | Stylized reconstruction, not photoreal. Use `primaryDomain: character`. |
| **Architecture / dioramas** (rooms, buildings, isometric scenes) | ⭐⭐⭐⭐ | Supported via v1.6+ roadmap. Works for boxy structures. |
| **Organic / biological subjects** (eyes, faces, skin, animals, plants) | ⭐⭐ | The code-only, procedural approach cannot replicate subsurface scattering, soft tissue, or complex organic topology seen in a photo. Output reads as stylized/low-poly. Use `--complexity ultra-complex` and accept approximation. |
| **Single body parts** (just an eye, just a hand without context) | ⭐ | Lacks the structural context the component tree needs. The pre-spec will show shallow hierarchy. Better to include the full face/body. |

### Generating reference images with AI (combined workflow)

When using an image-generation tool to produce the reference (Agnes, DALL-E, Stable Diffusion),
optimise for img2threejs success:

| Do generate | Don't generate |
|---|---|
| Product shots on plain background | Busy, cluttered scenes |
| Objects with clear geometric forms | Organic blobs, smoke, liquids |
| Side or 3/4 view showing structure | Close-up macro of one feature |
| Matte / diffuse lighting | Heavy lens flare or volumetric fog |
| Objects with distinct material zones | Subjects where subsurface scattering dominates |
| Hard-surface sci-fi/cyberpunk props | Photoreal animals, faces, skin close-ups |

**Practical tip**: If the only available image is organic (a person, an eye, a flower), it can still
proceed — but set expectations upfront: the output will be a stylized/low-poly recreation, not
photoreal. State this before the first spec is written.

### Minimal image requirements

- Resolution ≥ 512×512 (tested: 1152×864 works well)
- Object occupies ≥ 40% of the frame
- No heavy compression artifacts
- Single subject, not a group of objects
- Visible edges and boundaries — not blending into background
- PNG or JPEG format (PNG preferred for lossless detail)

### Reference Image Prompt Engineering

When generating reference images with an AI image tool (Agnes, DALL-E, Stable Diffusion),
the prompt directly controls how well the 3D pipeline will work. A prompt with skin context
will produce skin in the 3D model; a prompt with isolated subject produces clean geometry.

**Do use — produce clean, reconstructable subjects:**

```
A human eye, extreme close-up macro shot, ISOLATED on a solid grey background,
product photography style, matte studio lighting, sharp focus on iris and pupil,
no eyelashes or surrounding skin visible, the eye fills the entire frame,
photorealistic, 3D render style, clean edges.

AVOID: face, eyelid, eyebrow, skin texture, makeup, shadows on skin, background context.
```

Key phrases that improve 3D output:
- **"isolated on solid [color] background"** — removes surrounding context
- **"fills the entire frame"** — maximizes subject size in frame
- **"product photography style, matte lighting"** — reduces reflections and shadows
- **"sharp focus on [subject]"** — keeps edges crisp
- **"no [unwanted context]"** — explicitly suppresses what you don't want
- **"hard-surface, distinct parts, clear edges"** — for mechanical objects
- **"avoid: [clutter, reflections, complex background]"** — negative prompt for cleaner output

**Don't use — produce hard-to-reconstruct images:**

| Weak prompt | Problem |
|---|---|
| `a human eye` | Generates eye + surrounding face, skin, eyelashes — pipeline rebuilds everything |
| `a red apple on a wooden table` | Multiple objects, complex background, shadows — hard to isolate |
| `cyberpunk city street` | Too complex, no single subject |
| `close-up of a person's face` | Organic, subsurface scattering, skin — output will be stylized/low-poly |

**Before/after example:**

```
❌ Bad: "A human eye close-up photo"
    → Pipeline generates: eye, eyelids, skin, eyelashes, tear duct, brow area (9 components)

✅ Good: "A human eye globe isolated on grey background, fills the frame, no skin or eyelashes visible"
    → Pipeline generates: eye, sclera, iris, pupil, cornea (5 components, focused)
```

**Practical tip for the agent:** When asked to generate a reference image for 3D reconstruction,
write the prompt to:
1. Name the subject first
2. Add "isolated on solid [color] background"
3. Add "fills the entire frame" or "tight crop"
4. Explicitly exclude surrounding context ("no skin", "no background", "no eyelashes")
5. Specify "product photography style, matte lighting"
6. End with "AVOID: [list of unwanted elements]"

This is the single most impactful optimization for 3D reconstruction quality.

### Pre-validation: catch bad images early

```bash
# Before running the full pipeline, check if the image is suitable
python3 tools/pre_validate_image.py reference.png

# Sample output:
#   Subject: 49% of frame
#   ⚠️  Subject touches frame edges — may include surrounding context
#   ⚠️  Background corners not uniform — subject may blend into background
```

The pre-validation script checks:
- Subject coverage (% of frame occupied by subject)
- Edge variance (does subject touch frame boundaries?)
- Corner entropy (is background uniform?)
- Resolution and aspect ratio

This catches the exact eye-with-surrounding-skin problem before it reaches spec generation.

### Scope pruning: remove unwanted components

After spec generation, use `fix_spec.py` to keep only the components you want:

```bash
# Keep only eyeball components (remove skin, eyelids, lashes)
python3 tools/fix_spec.py spec.json --scope eye

# Or specify exact IDs to keep
python3 tools/fix_spec.py spec.json --keep eye-root,sclera,iris,pupil,cornea

# Or prune by prefix
python3 tools/fix_spec.py spec.json --prune skin-
```

## Dedicated Vision Backend

When the host AI agent lacks native vision capability (e.g. DeepSeek-V4-Free, a text-only
model), you can use the dedicated vision backend configured in `backend-vision.json` to
perform image analysis and render review. This delegates visual judgment to a remote
vision-capable model via `vision_query.py`.

### Quick start

```bash
# Analyze a reference image
python3 vision_query.py /path/to/reference.png "Describe this object in detail. What are its main parts, materials, and colors?"

# Save analysis to a JSON file
python3 vision_query.py /path/to/reference.png \
  "Classify this object: hard-surface, organic, character, or hybrid. List 5+ identity-defining features." \
  --out outputs/analysis.json
```

### Backend configuration

The `backend-vision.json` file defines two backends in a primary/fallback chain:

| Role | Provider | Model | Base URL | Status |
|---|---|---|---|---|
| **Primary** | Agnes | `agnes-2.0-flash` | `https://apihub.agnes-ai.com/v1` | ✅ Verified — vision works |
| **Fallback** | OpenCode Zen | `deepseek-v4-flash-free` | `https://opencode.ai/zen/v1` | ⚠️ Text-only on free tier |

The fallback is kept as a placeholder — upgrade to a paid OpenCode Zen key to enable
vision on non-free models (e.g. `deepseek-v4-flash`, `qwen3.5-plus`).

Both endpoints are OpenAI-compatible and receive vision requests as base64-encoded images.
The script tries Primary first; on error or rate-limit it falls back automatically.

> **Security**: `backend-vision.json` contains API keys and is excluded from git
> (see `.gitignore`). Treat it like a credential file.

### When to use

Call `vision_query.py` at every point where the pipeline says "agent vision":

1. **Image analysis** (pipeline step 1) — object classification, macro→meso→micro decomposition,
   part relationships, PBR material identification. Use targeted prompts per the layered
   observation protocol in `grimoire/intake/image_analysis.md`.
2. **Pre-spec assessment** (step 2) — fill the visual fields (`primaryDomain`, `complexity.scores`,
   `detailInventory`) that the deterministic scripts leave as `0` or `"unassessed"`.
3. **Render review** (step 9) — score the comparison sheet against the reference image.
   Prompt with the specific feature targets from the spec.
4. **Detail inventory** (step 2b) — enumerate identity-defining small features per region.

### Prompting guidelines

- **Be specific**: "List the visible parts of this object and their approximate colors" rather
  than "What do you see?"
- **Use 3D vocabulary**: Ask for component hierarchy, topology class (box/cylinder/sphere/torus/loft),
  material classification (metal/plastic/gemstone/paint), and surface finish (gloss/matte/brushed).
  See `grimoire/glossary/3d_vocabulary.md`.
- **One question at a time**: Complex multi-part questions dilute the model's focus on each aspect.
  Split into 2-3 separate calls for macro description → material analysis → detail detection.
- **Temperature 0.3** (already default): lower temperature gives more consistent, deterministic
  descriptions. Raise to 0.7 only for creative brainstorming.
- **Image size matters**: The default `imageDetail: "high"` loads full resolution. For very large
  images (>4K), use a cropped region of interest or switch to `"auto"` to reduce token usage.

### Integration with the pipeline loop

When running the full staged pipeline on a text-only host:

1. Run the intake scripts as normal (they are deterministic and need no vision):
   ```bash
   python3 forge/stage1_intake/probe_image.py <image>
   python3 forge/stage2_spec/new_pre_spec_assessment.py "Name" --image <img> --out assessment.json
   ```
2. For each field left as `"unassessed"` or `0` by the scripts, call `vision_query.py`
   with the relevant question, then manually fill the assessment JSON from the analysis.
3. Author the spec from the filled assessment.
4. Generate code with the normal build scripts.
5. For render review (step 9), call `vision_query.py` with the comparison sheet image
   and the feature targets as the prompt.
6. Write the review result into the spec with `append_review.py`.

This replaces the agent's built-in vision with a reproducible, scriptable pipeline
that works on any host model — not just those with native vision.

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
    For CS2, include English/Vietnamese variants, for example `--spec-query "safety ring vòng ngón"`
    or `search_specs.py "roughness độ nhám" --collection cs2`. Expand queries with object names,
    component names, material/finish terms, behavior terms, and bilingual aliases; retry focused
    alternatives when the first result is incomplete. Build the spec from returned evidence and do
    not invent domain specs when local evidence exists. Search caches are local/generated only;
    preserve JSONL records and source provenance rather than replacing them with cache output.
1b. **CS2 intake manifest** — for a CS2 request, create and validate `cs2-intake.json` before
    pre-spec authoring. Run admission and probing for every source view, record the heuristic signal
    as non-authoritative evidence, attach the classification record, resolve the supported family,
    and choose `route` independently from `exactnessTier`. Missing classification, insufficient
    coverage, or a contradictory high-confidence class is `request-input`; unsupported families do
    not continue into spec generation.
2. **Pre-Spec Assessment Gate** — classify + score complexity + write the quality contract:
   `forge/stage2_spec/new_pre_spec_assessment.py "Name" --image <img> --complexity <simple|moderate|complex|ultra-complex> --out assessment.json`. Rules: `grimoire/intake/quality_contract.md`.
   Set `objectClass.primaryDomain` (`object` | `character` | `hybrid`) and fill the seeded
   `detailInventory` (its `targetMinDetails` scales with complexity). **Supported CS2 knife
   skins**: always pass `--cs2`, which defaults the complexity tier to `ultra-complex`
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
2b. **Detail inventory** (do not skip for detailed subjects) — scan zones and enumerate every
   identity-defining small detail (gloss, bevel, fasteners, linework, contours, stains):
   `forge/stage1_intake/build_detail_inventory.py <image> --mode grid-3x3 --out-dir <dir> --out di.json`.
   Each detail MUST map to a `component.localFeatures` or `material.localOverrides` entry — never
   prose only. Taxonomy + 3D-term recipes: `grimoire/intake/detail_inventory.md`.
2c. **Projection-first fidelity (characters AND reference-matched surfaces — supported CS2 knife skins, decals,
   painted patterns)** — when the goal is matching a specific reference's surface, put the photo's
   own pixels on the mesh instead of approximating them procedurally. This is the single biggest
   fidelity lever; a procedural material for a patterned surface is the #1 reconstruction failure.
   Recipe (`grimoire/character/likeness_maximization.md` — its two levers, align-mesh+camera and
   project-the-photo, generalize past characters): solve the camera
   (`stage1_intake/solve_camera_pose.py` → `referenceCamera`), **de-light** the reference so it is
   free of baked lighting (`stage1_intake/delight_albedo.py`, hard requirement — this is what makes
   projection safe, not the flat-lit icon), then project the de-lit crop onto the mesh and bake it
   into UVs (`stage3_build/bake_projected_texture.py --mesh-id <id>`). For a CS2 skin the mesh is the
   procedural blade/guard/grip you author in the spec, and the projected de-lit crop IS the finish
   (front + back from the two views) — no procedural Doppler material. For characters, first capture
   landmarks (`stage1_intake/extract_landmarks.py --out anatomy.json`), fill `preSpecAssessment.anatomy`,
   route `grimoire/character/reconstruction.md`. A single view cannot show hidden sides — report
   per-region confidence and request more views when it matters.
3. Author the spec from the assessment:
   `forge/stage2_spec/new_sculpt_spec.py "Name" --image <img> --assessment assessment.json --manifest cs2-intake.json --out object-sculpt-spec.json`.
   **Note: `new_sculpt_spec.py` outputs a skeleton spec with only one root component.**
   For a full component tree, after skeleton generation run:
   `python3 tools/auto_tree.py assessment.json object-sculpt-spec.json --in-place`.
   This matches the assessment's `objectClass.primaryType` to a component tree template
   (car, eye, face, etc.) and populates components, materials, repetition systems, lighting,
   and feature review targets automatically.
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
5. Validate, then strict-validate before generating code:
   `forge/stage2_spec/validate_sculpt_spec.py object-sculpt-spec.json` then `--strict-quality`.
   Strict blocks shallow specs (a complex object with one root, no repetition systems, no
   local overrides, no micro groups is NOT implementation-ready even if JSON validates).
6. **Locked build passes** — only touch the currently unlocked pass:
   `forge/stage3_build/orchestrate_passes.py status object-sculpt-spec.json`
   `forge/stage3_build/orchestrate_passes.py check object-sculpt-spec.json --pass-id <pass>`
   `forge/stage3_build/generate_threejs_factory.py object-sculpt-spec.json --out src/createObjectModel.ts`
   (generator is pass-gated: a future `--pass-id` fails until prior passes are reviewed `continue`).
7. Render the current pass in a browser/preview, capture a screenshot at a review viewpoint.
8. Package one side-by-side sheet, then inspect it with agent vision:
   `forge/stage4_review/make_comparison_sheet.py --reference <img> --render <shot> --out cmp.png --json`.
9. Record the review (overall + per-layer + per-feature scores + decision):
    `forge/stage4_review/append_review.py object-sculpt-spec.json --pass-id <pass> --fidelity <0-1> --action <continue|refine-spec|refine-code|request-input|stop> --summary "..." --render-screenshot <shot> --comparison-image cmp.png --ai-vision-score <0-1> --layer-scores-json '{...}' --feature-reviews-json <f.json> --in-place`.
   For the CS2 knife path, also attach the versioned report with
   `--cs2-review-json cs2-review.json --review-scene-json forge/tests/fixtures/knife_review_scene.json`.
   A failed family, painted-region, projection-coverage, critical-detail, or orbit gate blocks
   `continue` even when the global score passes. See `docs/cs2/review-gates.md`.
10. Sync pipeline state after manual review edits:
    `forge/stage3_build/orchestrate_passes.py sync object-sculpt-spec.json --in-place`.

## CS2 image-matched rule

For a CS2 item, the target is observable agreement between the supplied image and the rendered
item: silhouette, proportions, edge profile, hardware layout, coating colour, pattern placement,
wear, roughness response, and camera framing. Every decision must be traceable to evidence or be
labelled as an approximation.

The initial CS2 family boundary is **knife only**. Pistol, rifle, SMG, sniper, heavy, glove, and
unknown knife subtypes must stop with `unsupported-family` or `unsupported-subtype`; they must not
receive the knife component tree as a generic fallback.

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
6. Select the knife adapter only after family/subtype validation. Record painted regions, unpainted
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
- **CS2 knife review contract**: `forge/stage4_review/cs2_review.py` consumes the manifest and
  versioned scene fixture, then blocks wrong family identity, missing projection coverage,
  painted-region mismatch, critical identity-detail failure, finish/material response failure,
  and degenerate orbit form. It records exactness tier, hidden-region confidence, per-region
  confidence, approximation notes, camera, environment hash, exposure, tone mapping, resolution,
  background, and renderer version.
- **Bounded correction loop (token-burn safety)**: `forge/stage4_review/correction_loop.py`
  guarantees termination (success/repeated-defect/oscillation/plateau/hard-ceiling), escalating to
  `request-input` — never a silent infinite burn.
- **Tier 1 (legacy, still valid)**: "Tier 2 (AI-vision) never runs against a render that has not passed Tier 1." Run `forge/stage4_review/diagnose_render.py` (silhouette IoU/proportion/symmetry/per-part color) and record it (`--spec ... --in-place`) before requesting a comparison sheet; `orchestrate_passes.py check` refuses otherwise.
- **Pre-spec / strict-quality**: blocks code gen until the spec is deep enough for its contract.
- **Screenshot feedback**: `continue` is allowed only with a render + comparison sheet + global
  AI-vision score ≥ threshold (default 0.7) AND every critical feature ≥ its own threshold.
  Details + per-layer scorecard: `grimoire/feedback/render_capture.md`.
- **Action-ready**: build a runtime hierarchy (pivots, sockets, colliders, destruction groups),
  never an inert lump; expose `root.userData.sculptRuntime`. `grimoire/readiness/action_rigging.md`.
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

## Platform Compatibility Notes

### Windows (Git Bash / PowerShell)

All scripts are pure Python 3.10+ stdlib and run on Windows without modification. Verified on
Python 3.14.6. Known considerations:

| Issue | Workaround |
|---|---|
| **Git Bash path conversion** | Python's `pathlib` handles both `/c/Users/...` and `C:\...` paths. Use absolute paths or forward-slashed relative paths. |
| **Temp file location** | `new_pre_spec_assessment.py --out /tmp/...` creates files in Git Bash's temp mapping. Use explicit `C:\Users\...\Temp\` paths for reliable cross-session access. |
| **CS2 test fixtures** | 2 of 56 pipeline tests fail on Windows without CS2 map-stripped render fixtures. These are CS2-specific gates (`append_review.py --map-stripped-render`) and do not affect core object/character pipeline. Run tests with `python3 forge/tests/test_pipeline.py`; 54/56 pass. |
| **PNG handling** | Scripts use `struct` + `zlib` for PNG I/O (no Pillow). Verified working on Windows. |

### macOS / Linux

Expected to work identically — all scripts use cross-platform Python stdlib only.

### Python version

Requires Python ≥ 3.10 (stdlib only — no pip/npm installs for the pipeline scripts).
The generated TypeScript output requires Node.js and Three.js in the target project.

## Project Structure

Each reconstruction lives in its own standalone project folder under
`~/Documents/ZCodeProjects/<project-name>/`. This keeps artifacts portable and independent
of the skill directory.

### Standard layout

```
~/Documents/ZCodeProjects/
  <project-name>/
    ├── index.html                     # Vite preview page
    ├── src/main.ts                    # Viewer entry
    ├── create<PascalName>Model.ts     # Generated Three.js factory (1k–3k lines)
    ├── <image>.png                    # Reference image
    ├── <project-name>-sculpt-spec.json # Validated sculpt spec
    ├── <project-name>-vision-analysis.json  # Vision backend output
    ├── package.json / tsconfig.json / vite.config.ts
    ├── dist/                          # Production build
    └── node_modules/                  # npm dependencies
```

### Quick-start from a reference image

```bash
# One-command setup (runs analysis → spec → validate → code gen → npm install)
python3 tools/init_project.py --image /path/to/ref.png --name my-project

# Then preview
cd ~/Documents/ZCodeProjects/my-project && npx vite
# → http://localhost:3000
```

### Template-based Component Tree (auto_tree)

The pre-spec assessment JSON contains rich information about the object: its type, estimated
component count, materials, and identity-defining details. However, `new_sculpt_spec.py`
only produces a one-root-component skeleton regardless of this data. **`auto_tree.py`**
bridges this gap by matching the assessment to a component tree template.

```bash
# After generating assessment + skeleton spec, enrich the spec with a full component tree:
python3 tools/auto_tree.py outputs/my-project-pre-spec-assessment.json outputs/my-project-sculpt-spec.json

# Specify output path explicitly:
python3 tools/auto_tree.py assessment.json spec.json --out enriched-spec.json --force
```

**How it works:**
1. Reads the assessment's `objectClass.primaryType` (e.g. "compact hatchback automobile")
2. Scores built-in templates by keyword matching (car, eye, face, hand, etc.)
3. Selects the best-matching template and instantiates a full component tree with parameterized dimensions
4. Populates materials, repetition systems, lighting, and feature review targets
5. Maps assessment `detailInventory` entries to component local features
6. Runs validation (cross-references, topology-primitive compatibility, tier fields)

**Integration in the pipeline** — `init_project.py` runs `auto_tree.py` automatically after
skeleton generation, before validation and factory code generation. The result is a spec with
a proper component hierarchy (10-20+ components for complex objects) instead of a single root box.

**When no template matches**, `auto_tree.py` prints a note and returns the skeleton unchanged —
it never fails on unknown object types. Templates live in `tools/auto_tree.py` as Python dicts;
add new templates for your domain by following the existing car template pattern.

### Iterative development with watch_rebuild

When iterating on a sculpt spec JSON (tweaking component positions, dimensions, or materials),
the manual loop is: edit spec → re-run factory generator → validate → restart Vite.
**`watch_rebuild.py`** automates this:

```bash
# Watch all specs in outputs/ and auto-rebuild on changes
python3 tools/watch_rebuild.py

# Watch a specific spec file
python3 tools/watch_rebuild.py --spec outputs/my-project-sculpt-spec.json

# Watch with a custom project directory
python3 tools/watch_rebuild.py --spec spec.json --project-dir ~/Documents/ZCodeProjects/my-project
```

The watcher polls every 2 seconds, debounces rapid edits (500ms), and on each detected change:
1. Runs `validate_sculpt_spec.py` to check spec integrity
2. Runs `generate_threejs_factory.py` to regenerate the TypeScript model file
3. Prints pass/fail and debounce status

After rebuilding, manually reload the Vite preview page to see the updated model. Or leave
both `vite` and `watch_rebuild.py` running in separate terminals for a tight edit→rebuild→review loop.

### Manual setup (when init_project doesn't fit)

1. Collect all artifacts in `~/Documents/ZCodeProjects/<name>/`
2. Generate factory: `python3 forge/stage3_build/generate_threejs_factory.py <spec.json> --out ~/Documents/ZCodeProjects/<name>/createModel.ts --force`
3. Copy spec and image alongside
4. Create `index.html` + `src/main.ts` + `package.json` (see existing projects as template)
5. `cd` into the project folder and run `npm install && npx vite`

## Known validation pitfalls (and fixes)

| Error/Warning | Cause | Fix |
|---|---|---|
| `albedo must be an object` | Material albedo is a hex string `"#D4A574"` | Change to `{"hex": "#D4A574", "type": "sRGB"}` |
| `colorMaterialRecipe` missing | Component has a string recipe instead of object | Replace with `{"dominantAlbedo": "rgba(r,g,b,a)", "materialClass": "skin", "materialClassConfidence": 0.95}` |
| `detailInventory detail has unknown kind` | Detail kind not in taxonomy | Use one of: gloss, bevel, fastener, linework, contour, seam, stitch, stain, scratch, chip, decal, emissive, hole, groove, ridge |
| `detail does not map to a component.localFeatures or material.localOverrides entry` | Detail ref doesn't match any component/material in spec | Fix `mapsTo.ref` to an existing component/material id, and add a matching entry to that component's `localFeatures` or material's `localOverrides` |
| `ambientOcclusion must be an object` | AO is a boolean `true` | Change to `{"map": {"type": "procedural"}, "intensity": 0.5}` |
| `referencePbr.usable must be true` | No PBR maps available | Set `referencePbr` to `null` (the check only fires when keyed by `lookDevTargets`) |
| `surfaceFrequencyBands` missing | Wrong field name or format | Use `surfaceFrequencyBands: [{"id": "macro", "frequency": 1.0, "amplitude": 0.5}, ...]` |
| `mesoComponents/minimumSpecDepth` below minimum | Component levels set wrong | Adjust component `level` fields (macro/meso/micro) and update `estimatedCounts` |
| Organics generate full humanoid bust | `--character` flag was set | For single body parts, omit `--character` to get a flat component tree |
| Viewer shows black screen | `createHumanEyeLookDevLights()` returns a `THREE.Group` not an array | Use `scene.add(createLights())` not `scene.add(...forEach)` |

## Self-Correction

After every pass, decide exactly one: `continue | refine-spec | refine-code | request-input | stop`.
`refine-spec` fixes a wrong/missing/shallow spec (re-validate, don't patch code around it);
`refine-code` fixes geometry/material/lighting that doesn't match a sound spec. Full root-cause
guide + fidelity scale: `grimoire/review/self_correction.md`.

## Implementation Rules (brief)

TypeScript + plain Three.js unless the project uses a wrapper. `Group` factory
`createObjectNameModel(spec, options)`, reconstruction data kept separate from renderer objects,
deterministic seeds for all procedural noise. Prefer primitives / `Shape` extrude / curve+tube /
instancing / displacement / generated canvas textures before any external art. Full geometry &
material recipes + hard-won failure patterns: `grimoire/build/geometry_patterns.md`.

## Output

- **Analysis-only**: suitability verdict + scores, object extraction, macro→micro hierarchy,
  geometry strategy, material/lighting recipe, animation/destruction feasibility, plan + risks.
- **Implementation**: the above briefly, then edit code; verify with typecheck/build + a screenshot.
- **Not feasible**: name the blocker, ask for more views / cleaner image / accepted stylization /
  a narrower target. "This cannot reach the requested fidelity from this image" is a valid result.

## Combined Workflow: Image Generation → 3D Reconstruction

When the user does not have a reference image ready, you can combine an image-generation
skill (Agnes, DALL-E, Stable Diffusion) with img2threejs in a two-step pipeline:

### Step 1: Generate a suitable reference image

Use a text-to-image tool to create a hard-surface object. Optimize the prompt for 3D reconstruction:

```
A [object name], product photography style, isolated on white background,
3/4 perspective view, matte lighting, distinct geometric parts, clear edges,
no reflections or lens flare, photorealistic.

Avoid: close-up crops, organic textures, volumetric effects, multiple objects,
busy backgrounds, extreme lighting.
```

Output size: ≥ 1024×768. Save as PNG.

### Step 2: Reconstruct with img2threejs

```
/img2threejs Rebuild this object as a procedural Three.js model.
```

The pipeline runs normally from this point. The AI-generated reference may have
inconsistent hidden geometry (the model never saw the back side) — the review loop
will flag this; report it as an approximation.

### When the generated image is unsuitable

| Symptom | Likely cause | Fix |
|---|---|---|
| `technicalSuitability: conditional` | Image too small, compressed, or ambiguous | Regenerate with higher resolution and plainer background |
| `primaryDomain: unassessed` (pre-spec) | Agent has no vision | Run `python3 vision_query.py <image> "Classify this object and identify its main parts and materials"` to fill the assessment manually. See [Dedicated Vision Backend](#dedicated-vision-backend). |
| `complexity.scores.* = 0` (pre-spec) | Vision required | Use `vision_query.py` to get visual scores, then edit the assessment JSON to fill the fields. |
| `detailInventory` has < `targetMinDetails` entries | Subject is organic or poorly visible | Use hard-surface subject, or accept stylized output. For organic subjects, use `vision_query.py` with a targeted "enumerate details by region" prompt. |
| Shallow spec rejection by strict-quality | Object lacks visible component hierarchy | Choose an object with distinct, separable parts. Use `vision_query.py` to help decompose the visible structure. |
