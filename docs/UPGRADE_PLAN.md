# img2threejs Upgrade Plan

Status: v1.2.0 SHIPPED. v1.1.0 = Track A detail-first + Track B/likeness scaffolding. v1.2.0 = humanoid character GENERATOR: a flattened, world-space humanoid componentTree template (head/neck/torso/arms + hair, glasses, headphones, face features), per-part character materials, character build passes (proportion-lock, feature-placement) and feature targets, auto-enabled when primaryDomain is character/hybrid (or --character). Verified end-to-end (spec -> generate -> browser render) on a real portrait, producing a recognizable stylized bust. 19/19 tests pass; object pipeline unaffected.
Target versions: 1.1.0 (Track A), 1.2.0 (Track B + character generator)

v1.2.0 implementation notes:
- Generator nodes carry transform.scale that cascades to children, so the humanoid template flattens all parts to world space under a hidden, unit-scaled root to avoid non-uniform-scale distortion. (Rig/pivot hierarchy for animation is a future refinement.)
- createSculptMaterial only honours a colorVariation.palette with >= 2 entries (else it blends in beige fallback tones), so each character material provides two shades of its intended colour.
- Remaining polish (future): rectangular glasses frames, canvas-texture wordmark on the shirt decal (currently a flat orange panel), headphone placement around the neck sides, hair-shape refinement, and the projection-first likeness path (delight + camera-match + texture projection) wired into the character render.

Shipped in 1.1.0:
- Schema: preSpecAssessment.detailInventory (+ targetMinDetails by complexity), objectClass.primaryDomain, preSpecAssessment.anatomy, top-level referenceCamera.
- Gates: stage2_spec/validate_sculpt_spec.py detail-inventory gate (count + component/material linkage + gloss/fastener checks) and character track gate (anatomy + character feature targets); both backward compatible.
- References: intake/detail_inventory.md, character/reconstruction.md, character/likeness_maximization.md; build/geometry_patterns.md detail + character recipes; intake/validation_rubric.md character suitability branch.
- Scripts: stage1_intake/build_detail_inventory.py, stage1_intake/extract_landmarks.py, stage1_intake/solve_camera_pose.py, stage1_intake/delight_albedo.py, stage3_build/bake_projected_texture.py.
- Tests: 16 pass (8 new covering the schema, gates, backward compat, and new scripts). SKILL.md + README updated; version bumped to 1.1.0.

Not yet implemented (follow-up, do not assume present):
- stage3_build/generate_threejs_factory.py humanoid rig + face-landmark placement + detail-emission hooks.
- stage2_spec/new_sculpt_spec.py character componentTree template.
- stage3_build/orchestrate_passes.py proportion-lock / feature-placement passes.
- delight/camera/projection scripts are documented approximations/descriptors, not GPU-accurate implementations.
Scope decisions locked with maintainer:
- Primary goal for human/character subjects: maximize likeness to the reference image, aiming as close to 100 percent as the input allows.
- Honest constraint: pure hand-authored primitives cannot reach photoreal likeness. The realistic path to near-100 percent, confirmed by the research in section 13, is a projection-first pipeline: fit a parametric humanoid/face template to image landmarks, then project the (de-lit) reference image itself onto the mesh as texture, and camera-match the render to the photo. Stylized/figurine remains the safe fallback when the input is weak or the user accepts it.
- Deliver both tracks. This document is the canonical spec for the work.

---

## 1. Problem statement

Two weaknesses in the current 1.0 pipeline:

1. Character / human subjects. The pipeline is built for hard-surface objects. Generated humans do not resemble the reference because there is no anatomy proportion system, no facial-landmark placement, no pose/skeleton alignment, and the suitability rubric actively rejects hair and cloth folds (the defining traits of people).
2. Fine detail is under-captured at the analysis stage. Small but identity-defining details (gloss zones, corner rounding, screws/rivets, engraved or painted linework, contour lines, stains and wear) are represented only as a single 0-3 "local detail density" score. There is no forced, evidence-linked inventory of these details before the spec is authored, so they get skipped and never reach the render.

## 2. Root-cause map (current files)

| Symptom | Root cause | Location |
| --- | --- | --- |
| Humans look nothing like the image | No anatomy/landmark/pose model; object-only geometry recipes | `grimoire/build/geometry_patterns.md`, `forge/stage2_spec/new_pre_spec_assessment.py`, `forge/stage2_spec/new_sculpt_spec.py` |
| Humans get rejected early | Rubric rejects hair/cloth-fold-dominant subjects | `grimoire/intake/validation_rubric.md` |
| Whole-image score hides face/proportion errors | Feature gate has no anatomy features | `forge/_shared/feature_acceptance_policy.py`, spec `featureReviewTargets` |
| Small details missed | Detail density is one score; no inventory artifact or gate | `grimoire/intake/quality_contract.md`, `forge/stage2_spec/new_pre_spec_assessment.py`, `forge/stage2_spec/validate_sculpt_spec.py` |
| No help inspecting detail zones | Probe returns metadata only | `forge/stage1_intake/probe_image.py` |

## 3. Design principles (must hold across the upgrade)

- Scripts enforce and package; the agent's vision judges. Do not move visual scoring into scripts.
- Pure Python 3.10+ standard library only. No pip, no PIL/numpy/Playwright. PNG via `struct`/`zlib`, matching existing scripts.
- Token efficiency preserved: new gates fail fast before codegen; one packaged sheet per review; pass-gated generation unchanged.
- Backward compatible: object-only specs from 1.0 must still validate. New blocks are additive and only enforced when relevant (by complexity or by domain).
- Agent-agnostic: any new "look at the image" step works with native vision, a browser MCP, or user-supplied crops.

---

## 4. Track A - Detail-first analysis (v1.1.0)

Goal: make micro-detail capture a required, evidence-linked artifact with its own gate, so gloss, bevels, fasteners, linework, and stains reliably reach the render.

### 4.1 New artifact: `detailInventory`

Produced during assessment, carried into the spec. Shape:

```json
{
  "detailInventory": {
    "scanMethod": "component-zones | grid-3x3 | grid-4x4",
    "targetMinDetails": 6,
    "details": [
      {
        "id": "top-bevel-gloss",
        "kind": "gloss",
        "description": "sharp specular hotspot along the top chamfer under key light",
        "region": { "x": 0.2, "y": 0.15, "width": 0.6, "height": 0.12, "units": "normalized" },
        "scale": "meso",
        "affects": "material",
        "mapsTo": { "type": "material.localOverride", "ref": "enamel-gradient/top-highlight" },
        "evidenceRef": "gradient-body",
        "confidence": 0.8
      }
    ]
  }
}
```

Supported `kind` values (the detail taxonomy): `gloss`, `bevel`, `fastener` (screw/rivet/bolt), `linework` (engraving/painted line/panel-line), `contour` (edge outline/toon rim), `seam`, `stitch`, `stain` (dirt/patina/discolour/faded), `scratch`, `chip`, `decal`, `emissive`, `hole`, `groove`, `ridge`.

Rule: every `detail` must set `affects` (geometry or material or both) and `mapsTo` a real `component.localFeatures[]` entry or `material.localOverrides[]` entry. A detail described only in prose is a gate failure.

### 4.2 New reference: `grimoire/intake/detail_inventory.md`

Taxonomy plus how to express each detail in 3D-graphics terms:
- gloss / do bong -> roughness low zone, clearcoat, specular hotspot location, anisotropy if streaked.
- bevel / bo goc -> `edgeTreatment.type=chamfer`, bevelRadius, segments; note whether it reads as a bright rim highlight.
- fastener / oc vit -> instanced mesh, count, spacing/distribution, head shape (hemisphere/flat), recess.
- linework / net ve, duong net -> engraved groove (geometry) vs painted line (canvas-texture decal) vs panel-line (dark AO seam); legibility target.
- stain / vet o -> dirt amount, cavity bias, vertical streak, patina colour, faded/sun-bleached zone, mask location.
- seam/stitch/chip/scratch/decal/hole -> mapping to localFeatures with placement, size, orientation, material effect, geometry effect, confidence.

Each entry states: where, what changes, how strong, which evidence supports it.

### 4.3 New script: `forge/stage1_intake/build_detail_inventory.py`

- Input: reference image (+ optional component regions).
- Output: crops of each zone (grid or per-component) into a directory + a `detailInventory` skeleton JSON to fill.
- Purpose: force systematic zone-by-zone inspection so the agent does not eyeball the whole image once and miss small marks.
- Pure stdlib PNG slicing (struct/zlib), consistent with `stage4_review/make_comparison_sheet.py`.

### 4.4 Schema and gate changes

- `forge/stage2_spec/new_pre_spec_assessment.py`: emit `detailInventory` skeleton; set `targetMinDetails` from complexity tier (simple 3, moderate 6, complex 10, ultra 16 as starting values, tunable).
- `forge/stage2_spec/new_sculpt_spec.py`: carry `detailInventory` into the spec; wire a helper so each detail links to a component/material.
- `forge/stage2_spec/validate_sculpt_spec.py` (`--strict-quality`): new checks
  - detail count >= `targetMinDetails` for the tier.
  - every detail `mapsTo` an existing component localFeature or material localOverride (no orphan prose).
  - a "detailed" object must carry material roughness variation + at least one bevel/edgeTreatment when the inventory lists gloss/bevel details.
  - gloss details require a low-roughness localOverride or clearcoat; fastener details require an instanced/repetition system or explicit small-count meso parts.
- `grimoire/feedback/render_capture.md`: add a mandatory detail close-up review - grazing-light shot that must show bevel highlights, countable fasteners, legible linework, and stains in the correct regions. Add a per-detail checklist to the review sheet.

### 4.5 Track A acceptance criteria

- Re-running the loot-chest demo: assessment enumerates at least gloss, bevel, rivets, latch, side-handle, crown-emissive, and any stains, each with an evidence region; strict-quality blocks the spec if the inventory is empty or unlinked.
- A detail close-up comparison sheet is produced and reviewed; the review records per-detail pass/fail.
- Object-only 1.0 specs still validate (backward compatible) - the new gate only fires when `detailInventory` is present or complexity >= moderate.

---

## 5. Track B - Character / figurine reconstruction (v1.2.0)

Goal: add a first-class character track that reconstructs a humanoid matching the reference as closely as the input allows - proportions, pose, facial landmarks, clothing, and palette. The default high-likeness path is projection-first (section 5.8: parametric template fit + de-lit photo projection + camera match). Stylized/figurine is the fallback when the input is weak or the user accepts it. A guaranteed 100 percent likeness from one image is not promised (section 5.10).

### 5.1 Domain detection

- `forge/stage2_spec/new_pre_spec_assessment.py`: add `objectClass.primaryDomain = object | character | hybrid` inferred from the agent's classification (character-like form language + skin/cloth/hair materials + humanoid silhouette).
- When `character` or `hybrid`, the assessment additionally emits an `anatomy` block and character feature targets.

### 5.2 New reference: `grimoire/character/reconstruction.md`

- Proportion system in head-units, with a style axis: realistic ~7.5 heads, stylized ~5-6, figurine/chibi 2-3. Record measured ratios from the image (head : torso : legs, shoulder width, hip width).
- Facial landmark layout: eye line near vertical mid-head, eye spacing, nose base, mouth line, hairline, ear top/bottom. Store normalized coordinates.
- Pose / skeleton: neck, shoulders, elbows, wrists, hips, knees, ankles; match the silhouette and limb angles.
- Character materials (stylized): skin (approximate subsurface via warm base + soft roughness + rim/backlight, not true SSS), hair (hair cards or tube-along-curve per lock, layered), eyes (glossy sphere + iris decal), cloth (extrude/plane panels with fold normals), metal/leather accessories reuse Track A detail machinery.

### 5.3 Anatomy data model

```json
{
  "anatomy": {
    "styleHeads": 3.0,
    "proportions": { "headUnit": 0.18, "torso": 2.2, "legs": 3.0, "shoulderWidth": 1.6, "hipWidth": 1.3 },
    "pose": { "type": "T-pose | contrapposto | action", "jointAngles": { "leftShoulder": [0,0,-10] } },
    "faceLandmarks": {
      "eyeLine": 0.52, "eyeSpacing": 0.3, "noseBase": 0.66, "mouthLine": 0.78, "hairline": 0.34
    },
    "features": ["hair-style-id", "outfit-parts", "accessories"],
    "confidence": 0.6
  }
}
```

### 5.4 New script: `forge/stage1_intake/extract_landmarks.py`

- Overlays a labelled grid / guide on the reference so the agent's vision can fill in landmark coordinates (eyes, shoulders, hips, joints).
- Outputs an `anatomy` skeleton with normalized coordinates + an overlay image for review.
- Stdlib only; no face-detection library - the agent supplies the judgments, the script packages the guide and records them.

### 5.5 Generator and pipeline

- `forge/stage2_spec/new_sculpt_spec.py`: humanoid component template - `rig` root with joint pivot nodes; head, face-feature group, hair group, torso, arms, legs as capsule/tapered primitives; outfit parts as separate components with Track A detail hooks.
- `forge/stage3_build/generate_threejs_factory.py`: emit the humanoid rig (named joint pivots, action-ready sockets), capsule limbs, face-feature placement from landmarks, hair cards; expose everything via `root.userData.sculptRuntime`.
- Two new build sub-passes inserted for the character domain, before `material-pass`:
  - `proportion-lock`: block out the humanoid at the measured head-unit proportions and pose; gate on silhouette + proportion match.
  - `feature-placement`: place facial features and hair to the landmark coordinates; gate on face landmark alignment.
- `grimoire/intake/validation_rubric.md`: add a character suitability branch - classify humans as `character-conditional -> stylized` rather than reject; specify when to request more views (front, side, full-body) and confirm accepted stylization level.

### 5.6 Character feature gates

`forge/_shared/feature_acceptance_policy.py` + spec `featureReviewTargets`: add critical character features with their own thresholds:
- anatomy-proportion (head-unit ratios, limb lengths)
- face-landmark-placement (eye line, feature spacing)
- pose-silhouette (limb angles, stance)
- outfit-and-palette (clothing shapes + colour zones)

Reviews compare against a landmark-overlay sheet, not just a whole-image score.

### 5.7 Track B acceptance criteria

- A human reference is classified `character` and produces an `anatomy` block with measured proportions + landmarks.
- A test character reconstructs at the correct head-unit count and pose, with facial features on the landmark lines and clothing colour zones matching.
- Character feature gates score face/proportion/pose independently; at least one test case passes all critical character thresholds at the stylized bar.
- Object pipeline is unaffected when `primaryDomain = object`.

### 5.8 Maximizing likeness - projection-first pipeline (research-driven)

The single largest likeness win, per section 13, is to stop hand-sculpting faces and instead fit a template and project the photo. Adopt this as the default high-likeness path for characters; keep the freehand stylized path as fallback.

1. Parametric template fit (the industry standard for likeness):
   - Ship a lightweight, code-generated parametric humanoid template (head-unit-parameterized body plus a morphable face), conceptually mirroring SMPL-X (body + face + hands, jaw and eye joints) and FLAME (face-from-scans). This is still procedural: the template is generated by code and driven by parameters, not a downloaded art pack.
   - Fit template parameters (shape, pose, expression) to the 2D landmarks and proportions extracted from the image, following the SMPLify-X idea: minimize the reprojection error between template landmarks and observed image landmarks. Landmarks come from `stage1_intake/extract_landmarks.py` (agent-vision assisted).
   - Rationale: proportions + landmark alignment are where "looks like the person" lives; freehand primitives cannot hit this reliably.

2. Photo texture projection (biggest single-image likeness gain):
   - Solve a camera for the reference (focal/FOV/orientation) so the mesh aligns with the photo, then project the reference image onto the fitted mesh via projective/camera-projection texturing (Three.js `ShaderMaterial`, or the `three-projected-material` approach) and bake it into the mesh UVs for the visible (front) side.
   - Infer unseen back/sides by mirroring the front texture across the symmetry plane, palette-continuing, or requesting a back/side view. Flag inferred regions with lower confidence.

3. De-light before projecting:
   - The raw photo contains baked shadows, highlights, and AO. Before treating it as albedo, run a de-lighting step to recover a neutral base color (high-pass / overlay neutralization, or an AI delighter equivalent), then generate independent roughness/normal/AO. An albedo map must be free of baked lighting. Without this, the projected texture will fight the scene lights and break likeness.

4. Camera match:
   - Estimate and store camera focal length, FOV, and orientation so the review render can be taken from the same viewpoint as the photo, enabling pixel-level overlay comparison and correct projection. Add a `referenceCamera` block to the spec.

5. Turnaround / reference-plane workflow:
   - Follow the artist standard: front, side, and (when available) back orthographic references at matched height, a proportion grid in head-units, silhouette-first blockout per view, and a color palette captured alongside. When only one view exists, mark it and request more via `request-input`.

6. Rig and deform in Three.js:
   - Emit a `SkinnedMesh` with a joint skeleton for the body and morph targets / blend shapes for facial expression, exportable as glTF. Keep predictable topology so blendshapes deform cleanly (retopology principle). Expose the skeleton and morph channels through `root.userData.sculptRuntime`.

7. Part-specific recipes (stylized-to-realistic dial):
   - Skin: approximate subsurface via warm base color, soft roughness, and a rim/back light; use `MeshPhysicalMaterial` sheen/transmission cautiously.
   - Hair: hair cards or tube-along-curve per lock, layered, with an alpha/anisotropic highlight; hair is the classic single-image failure, so prefer stylized clumps over strands.
   - Eyes: glossy sphere plus an iris decal/texture; correct catchlight sells realism.
   - Clothing: extrude/plane panels with fold normals; reuse Track A detail machinery for seams, stitches, buttons, prints.

### 5.9 Optional generative-assist mode (documented escape hatch)

Modern image-to-3D generators (TRELLIS, Tripo, Hunyuan3D, Rodin, TripoSR) reach roughly 80-95 percent front-face shape accuracy and the most photoreal textures via Gaussian-splat/LRM methods (section 13). Pure procedural code cannot match that for a real human.

- Offer an explicit, opt-in `generativeAssist` mode: import a base mesh produced by an external image-to-3D model, then use img2threejs to retopologize expectations, rig, apply the projection-first texture/material pipeline, and run the same review gates.
- This breaks the "code-only, no downloaded assets" promise, so it must be clearly flagged in the spec (`meshSource: procedural | generative-assist`) and in the output, and never the silent default.
- Even in this mode, the likeness gains still come mostly from de-lighting, projection, and camera match layered on top.

### 5.10 Honesty about 100 percent

State plainly in outputs: a single image cannot yield a guaranteed 100 percent likeness because back/sides, occluded geometry, and true skin/hair microstructure are not observable. The pipeline maximizes likeness through parametric fit + photo projection + de-lighting + camera match, reports per-region confidence, and requests additional views when the target is a real person and fidelity matters.

---

## 6. Cross-cutting: feedback loop upgrades

- `stage4_review/make_comparison_sheet.py`: optional overlay mode (landmark lines for characters; region boxes for detail review).
- Reviews record per-detail and per-landmark scores, not only the five existing layer scores.
- Keep one packaged sheet per review to preserve token efficiency.

## 7. File-by-file change summary

New files:
- `grimoire/intake/detail_inventory.md`
- `grimoire/character/reconstruction.md`
- `grimoire/character/likeness_maximization.md` (projection-first pipeline: template fit, photo projection, de-lighting, camera match, turnaround)
- `forge/stage1_intake/build_detail_inventory.py`
- `forge/stage1_intake/extract_landmarks.py`
- `forge/stage1_intake/solve_camera_pose.py` (estimate focal/FOV/orientation; emit `referenceCamera` block)
- `forge/stage3_build/bake_projected_texture.py` (project the de-lit reference onto the fitted mesh view and bake to UV; stdlib PNG)
- `forge/stage1_intake/delight_albedo.py` (recover neutral albedo from the photo before projection)
- a code-generated parametric humanoid/face template module (head-unit body + morphable face), inline in the generator or as `assets/humanoid_template` data
- demo assets for one detailed object close-up and one high-likeness character (projection-based)

Modified files:
- `grimoire/intake/quality_contract.md` (detail inventory + domain + anatomy)
- `grimoire/build/geometry_patterns.md` (character recipes + detail recipes)
- `grimoire/intake/validation_rubric.md` (character suitability branch)
- `grimoire/feedback/shading_realism.md` (skin/hair/cloth notes)
- `grimoire/feedback/render_capture.md` (detail close-up + landmark overlay review)
- `grimoire/review/self_correction.md` (character sub-pass guidance)
- `forge/stage2_spec/new_pre_spec_assessment.py`
- `forge/stage2_spec/new_sculpt_spec.py`
- `forge/stage2_spec/validate_sculpt_spec.py`
- `forge/stage3_build/generate_threejs_factory.py`
- `forge/_shared/feature_acceptance_policy.py`
- `forge/stage3_build/orchestrate_passes.py` (register proportion-lock / feature-placement passes for character domain)
- `forge/stage1_intake/probe_image.py` (hint detail-scan when complexity high)
- `forge/tests/test_pipeline.py` (new coverage)
- `SKILL.md`, `README.md` (document both tracks, bump version)

## 8. Phased roadmap

| Phase | Version | Content | Acceptance |
| --- | --- | --- | --- |
| P1 | 1.1.0 | detail-inventory reference + build script + assessment/spec fields | loot-chest assessment enumerates linked details; strict gate blocks empty/unlinked inventory |
| P2 | 1.1.0 | detail close-up review + validation gate + tests | close-up sheet proves bevel/fastener/stain; tests green; 1.0 specs still valid |
| P3 | 1.2.0 | character reference + rubric branch + domain/anatomy in assessment | human image -> character-stylized + anatomy block |
| P4 | 1.2.0 | character spec template + landmark script + humanoid generator | test character at correct head-units + pose + landmarks |
| P5 | 1.2.0 | character feature gates + proportion-lock/feature-placement passes | face/proportion/pose scored; one case passes stylized bar |
| P6 | 1.3.0 | likeness-max: `stage1_intake/solve_camera_pose.py` + `stage1_intake/delight_albedo.py` + `stage3_build/bake_projected_texture.py` + parametric template fit | render camera-matches the photo; de-lit reference projected onto the fitted mesh; front-face likeness visibly higher than stylized baseline |
| P7 | 1.3.0 | optional `generativeAssist` mode (flagged, non-default) + confidence reporting for unseen regions | import-and-refine path works end-to-end and is clearly labelled non-procedural |
| P8 | 1.3.0 | docs + demos + version bump | SKILL.md/README updated, high-likeness character demo, no emoji |

## 9. Testing strategy

- Extend `forge/tests/test_pipeline.py`: detail-inventory validation (pass/fail cases), domain detection, anatomy validation, character feature-gate thresholds, backward-compat (a 1.0 object spec still validates).
- End-to-end smoke: rebuild the loot-chest with the detail gate on; reconstruct one stylized character end-to-end.

## 10. Backward compatibility

- All new blocks are optional at the schema level; strict-quality only enforces them when `detailInventory` exists or complexity >= moderate (Track A) or `primaryDomain != object` (Track B).
- Existing object specs and the current loot-chest demo must continue to pass.

## 11. Risks and mitigations

- Photoreal humans are infeasible procedurally: scope locked to stylized/figurine; state this in outputs and request more views when needed.
- Over-strict gates could block simple objects: gate strength scales with complexity/domain; simple objects keep the light path.
- Single image lacks hidden faces/pose ambiguity: use `request-input` to ask for front/side/full-body views before committing.
- Token cost creep from more analysis: keep one packaged sheet per review; scripts do the enumeration scaffolding, the model only judges.

## 12. Open questions

- Default `targetMinDetails` per tier - tune after the first detailed run.
- Hair strategy default: hair cards vs tube-along-curve per lock (perf vs look).
- Whether to ship a small parametric humanoid template file or generate it inline in the factory.
- How far to build camera-solve and de-lighting in pure stdlib vs documenting an optional external step; both may need a pragmatic approximation first.
- Whether `generativeAssist` should call an external API at all, or only accept a user-supplied base mesh (keeps the skill offline and asset-free by default).

## 13. Research summary: best practices for maximum human likeness

Findings from web research (July 2026), used to shape sections 5.8 to 5.10.

1. Parametric templates plus landmark fitting are the standard for likeness. SMPL-X is a unified body-plus-hands-plus-face model (10,475 vertices, 54 joints including jaw and eyes) using linear blend skinning with corrective blendshapes; it embeds MANO (hands) and FLAME (face, learned from ~3,800 head scans). SMPLify-X fits it to a single image by optimizing pose, shape, and expression to match observed 2D landmarks - the de facto initialization for single-image human recovery. Takeaway: fit a parametric template to image landmarks instead of hand-sculpting.

2. Photo texture projection is the biggest single-image likeness lever. Projective/camera-projection texturing maps the reference image onto the mesh as if projected from the solved camera; the Three.js `three-projected-material` library and custom `ShaderMaterial` approaches implement this, including "snapshotting" the projection then baking it. Texture-projection modules that encode high-frequency detail from sparse views are how recent human-texture methods (for example TexDreamer) achieve fidelity.

3. De-lighting is mandatory before using a photo as albedo. An albedo map must be free of baked shadows, highlights, and AO; tools like Substance 3D Sampler Delight, Agisoft Delighter, Unity de-lighting, and AI delighters exist precisely for this, and a manual high-pass/overlay neutralization is the common fallback. Skipping this makes projected texture fight scene lighting.

4. Orthographic turnaround workflow drives proportion accuracy. Artists model from front/side/back references at matched height, using a head-unit proportion grid and silhouette-first blockout, with a color palette captured alongside; consistent lines across views let the model match features. Adopt reference planes plus proportion grid; request missing views.

5. Morph targets / blend shapes plus SkinnedMesh are the Three.js primitives for faces and bodies; predictable (retopologized) topology is required for clean blendshape deformation, and LOD (3-4 levels) manages performance. Three.js ships a morph-targets face example.

6. Generative image-to-3D (2026) sets the realistic likeness ceiling for a real person: TRELLIS (top open-source quality, Gaussian-splat textures), Tripo (Tripo P1 reconstructs facial features accurately with sharp textures), Hunyuan3D (strong open-source), Rodin (high-fidelity geometry), TripoSR/Stable Fast 3D (sub-second, lower fidelity). Reported ~80-95 percent front-face shape accuracy. This is ML, not procedural, hence the optional flagged `generativeAssist` mode; the procedural path still layers projection + de-lighting + camera match for likeness.

Sources:
- [Expressive Body Capture: SMPL-X / SMPLify-X (arXiv 1904.05866)](https://arxiv.org/pdf/1904.05866)
- [SMPLify-X overview (EmergentMind)](https://www.emergentmind.com/topics/smplify-x)
- [Playing with Texture Projection in Three.js (Codrops)](https://tympanus.net/codrops/2020/01/07/playing-with-texture-projection-in-three-js/)
- [three-projected-material (GitHub)](https://github.com/marcofugaro/three-projected-material)
- [three.js morph targets - face example](https://threejs.org/examples/webgl_morphtargets_face.html)
- [TexDreamer: high-fidelity 3D human texture (arXiv 2403.12906)](https://arxiv.org/pdf/2403.12906)
- [Delight AI - Adobe Substance 3D Sampler](https://helpx.adobe.com/substance-3d-sampler/filters/tools/delight-ai-powered.html)
- [De-Lighting 3D Scans (Sketchfab community)](https://sketchfab.com/blogs/community/de-lighting-3d-scans-in-unity-by-pete-mcnally/)
- [Character Turnaround Guide (spines.com)](https://spines.com/character-turnaround/)
- [How to Create a 3D Character Model Reference (Coohom)](https://www.coohom.com/article/how-to-create-a-3d-character-model-reference)
- [Best AI 3D Model Generators 2026 (TRELLIS vs Meshy vs Tripo vs Hitem3D)](https://trellis2.app/blog/best-ai-3d-model-generator)
- [7 Image-to-3D AI Generators, July 2026 (Vitalify)](https://www.vitalify.asia/en/blog/generative-ai/ai-image-to-3d-generators-comparison)
- [How To Deploy Image-To-3D Models In Three.js (Threedium)](https://threedium.io/create/3d-models/platform/threejs)
