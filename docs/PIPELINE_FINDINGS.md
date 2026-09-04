# Pipeline findings from two reconstruction runs

Two agent sessions ran the full pipeline in this checkout at the same time, on unrelated subjects:
a **fire extinguisher** (product photo, dielectric-dominated, small bright parts) and a **widebody
coupe** (engine render, bare metal, four wheels, implicit-surface body). Both reached the material
pass. This file merges what each found, tagged by where the fix belongs.

Findings that **both runs hit independently** are listed first, because two unrelated subjects
producing the same failure is much stronger evidence than either run alone. Every number quoted here
was measured during a run, not estimated.

Scope note: this is a findings register, not a plan. Nothing here is scheduled, and the two entries
under *Already fixed* are the only ones with code on this branch.

---

## 1. Confirmed independently by both runs

### 1.1 No browser or capture route ships with the skill

`docs/` and `grimoire/scripts.md` reference `runtime/scripts/export_mesh_geometry.mjs` and
`scripts/character_audit.sh`, but `runtime/` is gitignored and neither file is in the checkout. Both
runs wrote a capture harness from scratch under `work/`, and neither survives the session.

Every visual gate depends on captures the skill cannot produce. What a harness has to do, learned the
hard way in both runs, is more than "take a screenshot":

- a **deterministic camera** that a geometry change cannot reframe;
- an **alpha-mask pass**, because threshold segmentation cannot separate a bright subject from a
  white studio background (see 1.2);
- a **map-stripped pass**, which `diagnose_render.py` requires for `blockout` and refuses to run
  without;
- a **semantic-ID pass** with all geometry present, so per-part colour can be measured with occlusion
  resolved (see 1.3);
- a **texture-ready wait**, because reference-PBR maps load asynchronously and a capture taken early
  shows the white-texture fallback, which reads as a material failure rather than a timing one;
- a **fresh browser profile per run**. Reusing one profile makes a second launch attach to the
  already-running browser, so a navigation to an identical URL is a same-document no-op and the
  previous run's mutated scene is what gets captured. This produced two byte-identical renders across
  a real material change, which looks exactly like "the change had no effect".

**Destination:** new `runtime/scripts/`, shipped rather than gitignored.

### 1.2 Foreground segmentation is the largest single source of false gate failures

`build_foreground_mask` samples a corner background and thresholds against it. Both runs broke it,
in four different ways:

| Run | What the mask did | Consequence |
| --- | --- | --- |
| coupe | reference car on a two-axis sky gradient | subject reported at `widthFraction` 1.001 of the frame against an actual 0.653; every cross-image silhouette number was measuring the segmentation |
| coupe | bright bare metal against the white studio background | phantom interior "hole" of 60,263 px; turntable gate failed with no defect in the model |
| extinguisher | ground shadow counted as subject | Tier 1 failure, no visible defect |
| extinguisher | disconnected hose read as a separate blob, white label read as background | Tier 1 failures, no visible defect |

Both runs worked around it the same way, by matting the reference and capturing renders with an alpha
channel so segmentation is exact instead of inferred.

**Destination:** `forge/stage1_intake/` for a matting helper; `grimoire/review/` for the rule that a
gate's segmentation must be verified before its verdict is believed.

### 1.3 The per-part colour gate compares quantities that are not comparable

Both runs failed this gate, from opposite directions.

- The extinguisher run hit it as a brass collar covering a fraction of a percent of the frame scored
  against the whole render's five dominant clusters, which a part that small can never form. That run
  extended `per_part_color_delta` with per-part `samplePoints` so a component is read from its own
  pixels. That change is on this branch (see 5.2).
- The coupe run hit it with the sample-points fix already in place: the check compares each
  component's authored `colorMaterialRecipe.dominantAlbedo` against **lit** render pixels. Those are
  different physical quantities, and the gap is systematic, not a defect. Measured on the coupe, the
  same materials scored a median CIEDE2000 of 6.09 and a maximum of 9.79 when compared like for like
  against their own reference crops, while the gate reported a maximum delta-E of 71.31.

The module docstring already flags the cluster limitation. The albedo-versus-lit-pixel mismatch is
not yet written down anywhere.

**Destination:** `forge/stage4_review/diagnose_render.py` for the comparison itself; `SKILL.md` and
`grimoire/feedback/shading_realism.md` for what a recipe colour is supposed to mean.

### 1.4 The generated look-dev rig ignores the spec's lighting

`spec.lightingFromPhoto` is authored, validated, and copied into `userData`, but
`create<Name>LookDevLights()` emits hard-coded intensities. A calibrated rig therefore exists only in
whatever viewer the agent built, and cannot be reproduced from the spec.

**Destination:** `forge/stage3_build/generate_threejs_factory.py`.

### 1.5 The correction budget is consumed by calibration, not by modelling

The loop allows three corrections per pass and six in total. Both runs spent most of that on
calibration rather than on the model: the extinguisher on exposure, label tone, strip placement and
chrome finish; the coupe on camera pose and framing, where a mirrored pose alone scored silhouette
overlap 0.19 and a framing mismatch scored a scale delta of 0.71 before any model judgement was
possible.

Both runs independently invented the same workaround, taking calibration frames outside the review
record so they did not count against the budget. A process that forces the same workaround in two
unrelated runs is missing a concept.

**Destination:** `SKILL.md` and `forge/_shared/workflow_state.py` — a calibration frame that is
recorded as evidence but does not consume a correction.

---

## 2. A defect class worth one sweep rather than six patches

Six instances turned up across the two runs of the same underlying problem: **a spec field that
validates cleanly and then does nothing, or is silently overridden, at runtime.**

| Field | What happens |
| --- | --- |
| `geometryDescriptor.edgeTreatment` chamfer on `extrude` | declared, never emitted |
| `attachment` on a `tube` or `cylinder` | silently overrides the authored primitive with a straight endpoint cylinder; the extinguisher hose had to be made parentless to survive |
| `material.envMapIntensity` | inert for any material relying on `scene.environment`; bracketed live at 0 and at 3 with byte-identical renders |
| `material.roughness.base` | discarded whenever a texture set exists, because the generator sets `roughness: 1` and multiplies by the map |
| `spec.lightingFromPhoto` | see 1.4 |
| implicit-surface UVs | not a field but the same shape of bug: nothing emitted one, so any textured material on an SDF component shaded black (fixed, see 5.1) |

A single audit of spec fields against generator output would find the rest of these more cheaply than
discovering them one reconstruction at a time.

**Destination:** `forge/tests/` as a coverage test; whatever it finds lands in
`forge/stage3_build/generate_threejs_factory.py`.

---

## 3. Findings from one run only

### 3.1 Coupe run

- **Reference-PBR map URLs are bare filenames.** `material_region_analysis.py` calls the extractor
  with `url_prefix=""`, so the emitted `url` is `body-metal_albedo.png`. The browser resolves that
  against the page origin, gets a 404 for all five channels, and `createSculptMaterial` then forces
  `color` to white and `roughness` to 1 because it believes a texture set exists. Every material —
  bare metal, near-black glass, matte rubber — rendered as the same flat chrome grey. One line, large
  blast radius. **`forge/stage1_intake/material_region_analysis.py`.**
- **Extracted roughness carries no level information.** Across eight materials spanning polished glass
  to matte rubber, every emitted roughness map had a mean between 0.685 and 0.784; glass came out at
  0.716 against a registry prior of 0.05. Combined with the scalar being discarded (section 2), every
  material rendered at roughly 0.7. Either the extractor should not emit a channel it cannot estimate,
  or the map should be normalised around the authored value.
  **`forge/stage1_intake/extract_pbr_evidence.py`.**
- **Conductor albedo double-counts the environment.** For a metal the map is the material's
  reflectance tint and the scene supplies what it reflects, but a crop taken off a mirror-finish panel
  is a photograph of the reflected sky. Feeding it back bakes the sky in and then reflects it again.
  The registry says "bright neutral conductor" for `metal.aluminum`; nothing in the extraction path
  knows the difference between a conductor and a dielectric. **`forge/stage1_intake/` and
  `grimoire/build/threejs_texture_reference.md`.**
- **Review framing must be re-solved per pass.** Pass gating changes which components are emitted, so
  the model's bounding box changes, so the same camera frames the subject at a different size. The
  structural pass silently failed Tier 1 on `scaleDelta` for this reason alone. **`SKILL.md`.**
- **The assembly gate and the spec validator disagreed about one field's format** (fixed, see 5.1).

### 3.2 Extinguisher run

- **Colour recipes are compared against the reference's observed crop colours, not physical albedos.**
  Nothing in `SKILL.md` or the grimoire says which is meant, and chrome authored as a bright physical
  F0 can never pass. Cost one material loop. **`SKILL.md` and `grimoire/`.**
- **The showcase scaffold looks for a registry array name that no longer exists**, so
  `npm run new-demo` cannot insert an entry. **showcase repo.**
- **The generator publishes `sculptRuntime.nodes` while the showcase viewer reads `pivots`.**
  **`forge/stage3_build/` or the showcase, whichever is deemed canonical.**
- **`divine_eye.py` returns a non-zero exit code on an advisory reject**, which breaks any shell chain
  that treats exit status as fatal. **`forge/stage4_review/divine_eye.py`.**

---

## 4. Coordination

Both sessions edited `forge/` in the same working tree at the same time. This is not hypothetical: one
session reverted `forge/stage4_review/diagnose_render.py` with `git checkout --` without first
checking whether the changes were its own, and destroyed the other session's uncommitted work. It was
rebuilt from output captured before the revert and the original test file survived because it was
untracked, but that recovery was luck rather than process.

Two rules would have prevented it:

1. Never revert a tracked file without checking `git diff` for it first.
2. Land work in focused commits early, so a concurrent session rebases instead of overwriting.

---

## 5. Already fixed on this branch

### 5.1 Implicit surfaces emitted no UV attribute

Surface nets produce no parameterisation, and a textured material on a geometry with no `uv` attribute
does not render untextured — three.js derives the normal-map tangent frame from screen-space UV
derivatives, which are zero for a constant UV, so the perturbed normal is NaN and the surface shades
black. Measured on the coupe: the implicit body shell rendered pitch black beside correctly lit
extruded panels of the same material, with outward, unit-length normals. The polygonizer now emits a
dominant-axis planar projection at world scale. Test:
`forge/tests/test_sdf_primitives.py::test_emits_finite_world_scale_uvs_for_texturing`.

### 5.2 Two gates disagreed about one documented field format

`validate_sculpt_spec._detail_link_keys` accepts a `detailInventory` entry's `mapsTo.ref` as either a
bare feature id or the owner-prefixed `<componentId>/<featureId>`, and the prefixed form is what an
author reaches for once two components own a feature of the same name.
`check_part_coverage.collect_local_feature_keys` registered only the bare id, so a spec that cleared
`--strict-quality` still drew 18 "unresolved mapsTo" warnings from the assembly gate. Composite keys
are now registered in the normalised form the lookup uses. Test:
`forge/tests/test_part_coverage_link_keys.py`.

### 5.3 Per-part colour sampling (from the extinguisher run)

`per_part_color_delta` now reads a component's own pixels when the viewer publishes `samplePoints` in
its part manifest, instead of scoring every component against the whole render's dominant clusters.
Test: `forge/tests/test_tier1_part_samples.py`.

---

## 6. Open design question

When a parts manifest is supplied and a component has fewer than `MIN_PART_SAMPLES` visible pixels,
`per_part_color_delta` currently falls back to the global clusters and labels the method
`global-cluster`, so nothing goes unscored. The alternative is to record it as not visible and drop it
from the maximum, on the grounds that a component the camera cannot see is not evidence either way.

The coupe run's failing entry was an amber side marker with zero visible pixels scoring delta-E 71.31
against a frame of greys and metals — a number that describes the framing, not the model. The
fallback's defence is that dropping components silently hides coverage.

Both readings are defensible and the current behaviour is the one under test. Recorded here so the
choice is made deliberately rather than by whoever edits the file next.
