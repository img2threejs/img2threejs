# Changelog

All notable changes to **img2threejs** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed — BREAKING

- **A domain is no longer inferred from the target's name.** `detect_cs2_intent` and its 17-keyword
  table (including bare `"fade"` and `"karambit"`) are removed, so a target called
  `"AK-47 | Redline"` or `"Karambit Doppler"` no longer identifies itself as CS2 — it resolves to the
  generic `core_3d` evidence collection like any other object. Declare the domain instead; `--cs2`
  and `--profile cs2` are unchanged. Name similarity is not a legitimate resolution input
  (`PLUGIN_CONTRACT.md` §13), and the old heuristic silently applied CS2's quality floors and
  evidence collection to anything whose name happened to contain one of those words.
- The local-spec-search collection now defaults to `core_3d` rather than `cs2`. That step runs on
  every profile, so every generic run was previously searching a domain corpus by default.
- `objectClass.cs2: true` is replaced by `objectClass.domain: "<id>"`. Specs authored before this
  change carry the old field.
- `weapon-v1.4` no longer requires a CS2 marker to pass strict validation. It is the weapon *shape*
  template, keyed by classified kind, and CS2 is a finish/material domain riding the generic
  hard-surface path — so requiring the marker meant a plain sword could pass only by being labelled a
  CS2 skin. Domain coherence is still enforced by `validate_cs2_contract`.

### Added

- `forge/_shared/domains/` — a domain registry. Each module declares one `DOMAIN` mapping
  contributing checklist steps, a splice anchor, and an evidence collection; registration is by
  presence, so the base pipeline holds no list of domain names. `--profile` choices derive from it,
  and an unregistered profile fails loud naming what is available instead of quietly running as
  generic. `workflow_state.py` and `forge/state.py` now contain no domain name at all.

**Theme: rigging may only ADD.** The 1.5.2 modules existed and nothing ever ran them; animation
broke meshes because no step in the workflow forbade it and no gate could tell a rigged mesh from a
damaged one. This closes the loop between the gates and the pipeline that is supposed to enforce them.

### Added (rigging and animation workflow, merged from main)

- **`animated-character` workflow profile** (now `forge/_shared/domains/animated_character.py` --
  ported from the hardcoded `RIG_STEPS` into the domain registry when this branch merged v1.5.2;
  the registry gained a `rigSteps` key, appended after the FINAL steps). Nine Stage R steps join the checklist: read the contract, read the rig from the
  GLB, repair the mesh, freeze it, validate the payload, bind, verify parity, measure the clips, run
  the gates. Every `forge/stage5_rig/` module was callable before this and nothing in the workflow
  ever told anyone to call one — `next.py` walks the checklist, so a gate absent from the checklist
  never runs, and a gate that never runs reports a clean verdict forever.
- The Stage R order is enforced by `forge/tests/test_rig_workflow_steps.py`, not by convention.
  Repair precedes the freeze; the freeze precedes every rig step; parity is verified after the bind.
  Moving the freeze later would let a bind rewrite vertices and then certify the rewritten buffer,
  so the manifest would attest to the damage instead of catching it.
- **`forge/stage5_rig/mesh_parity.py`** — freeze the geometry, then prove it survived. Hashes are
  taken over packed bytes rather than JSON text, so a formatter changing its mind is not a reported
  change while a changed number cannot be formatted into a collision. `position`, `normal`, `uv` and
  `index` are frozen; `skinIndex` and `skinWeight` are deliberately not, because adding them is the
  entire legal purpose of rigging — freezing them would fail every successful rig and the gate would
  be switched off within a day. A single nudged float is caught and located to the mesh, the
  attribute, the element and both values.
- **`forge/stage5_rig/glb_rig_reference.py`** — read the skeleton, the skin's joint ordering, the
  inverse bind matrices and the clips FROM the GLB, and sample its own animation channels into the
  payload `clip_features` consumes. Joint correspondence to a procedural skeleton is matched by
  measured position, never by name, and reports `usable: false` when any joint on either side is
  unmatched. A GLB's channels target its node indices and its `skinIndex` values address its joint
  array; fed to a procedurally authored skeleton they index a different rig entirely.
- **`runtime/scripts/export_mesh_buffers.mjs`** — raw geometry buffers, no `matrixWorld`.
  `export_mesh_geometry.mjs` emits world space, which is correct for self-intersection and wrong for
  parity in both directions: posing changes `matrixWorld` without touching a byte of the buffer, and
  a real buffer edit cancelled by a compensating transform would read as unchanged.
- **`docs/GLB_ANIMATED_CHARACTER_PROMPT.md`** — one copy-paste prompt carrying a subject from a GLB
  to a rigged, animated, gate-cleared character in a single pass.

### Fixed

- `forge/stage5_rig/emit_rig.py` constructed `THREE.Skeleton` before `updateMatrixWorld`, so
  `calculateInverses()` captured identity matrices, the rest pose never cancelled, and every vertex
  sat displaced by its bone's offset. That failure compiles, binds, reports `bound: true` and
  renders a corpse. The main factory emitter already ordered this correctly; only the Milestone-0
  emitter did not.
- `forge/stage3_build/generate_threejs_factory.py` now refuses to bake a geometry twice.
  `applyMatrix4` mutates in place and is not idempotent, so a second pass applies the world matrix
  squared and scatters the parts. It throws rather than skipping, because a silent skip would leave
  a mesh in the wrong space and resurface later as a subtler misplacement.

### Changed

- `docs/pipelines/character-rigging-animation-1.5.2.md` §R0.1 carried a claim that did not
  reproduce. It cited the Lee Sin rig for "clips target technical nodes that are not joints";
  re-measured on that asset, **all 1,353 channels across all 11 clips target deform joints and zero
  target technical nodes.** The rule stands — it is what keeps the skin's index space intact — but
  it is now stated as the risk it guards against rather than as an observation, and
  `deformVsTechnical` reports the real count per asset.

## [1.5.2] — 2026-08-25

**Theme: a clip that exists is not a clip that plays.** Rigging failures in 1.5.1 were not "the
animation looks slightly wrong" — they were total and silent: eleven clips held actions, the mixer
held state, buttons dispatched, nothing moved. Two separate bugs each produced a plausible scene
with zero motion, and neither was visible in code review. This release turns the pipeline distilled
from that build into executable modules and gates.

### Added

**The animation pipeline — `forge/stage5_rig/`**
- `clip_features.py`: the §1 measurement vocabulary (duration, travel, rise, speed, handRange,
  footRange, headRise, scaleDelta, poseReturn) sampled at N = 25, the §2 classifier, §3 naming with
  the `inferred` honesty flag, and the §4 loop rule. `scaleDelta` is a tripwire, not a descriptor:
  a non-zero value means the source rig scales joints, which changes what R2 may legally do to skin
  weights, and it is surfaced before anything else proceeds.
- **The loop rule is corrected, and the old one is pinned as a regression test.** 1.5.1 used "a clip
  that neither travels nor rises can repeat seamlessly", which contradicted its own data —
  `idle-gesture` travels 0.121H, six times the idle threshold, and is correctly loopable. Loop is
  now `poseReturn ≤ 0.5°` **and** `‖hip(T) − hip(0)‖ ≤ 0.01H`. A host that cannot measure
  `poseReturn` gets `loop = None`, never a defaulted `false` — "measured, does not loop" and
  "nobody looked" are different answers.
- `skin_conditioning.py`: proximity weight blending for characters built from overlapping parts.
  Dense accumulation before mixing, buffered writes so the result is order-independent, a uniform
  grid hash at one cell per radius (≤ 27 buckets per query), and interior vertices left
  bit-identical. Coincidence-welding is kept as an executed negative result: it closes one crack and
  leaves 28 frames cracked, because adjacent parts overlap rather than share a rim.
- The R2 trade is stated wherever it runs rather than buried: blending closes holes (974 px in 30
  blobs → 287 px in 15) and makes creases ~16% worse (31,316 px → 36,470 px). A hole shows the
  background, a crease shows skin. A later stage must not "fix" the crease count by disabling it.
- `action_design.py`: §R4 target bands, topology-driven chain resolution (arms, legs and spine
  resolved without reading a single joint name), the medial/lateral gate that catches a mirrored
  rig, gait as a phase machine with real stance/swing intervals, and `foot_slide` — the gate that
  catches a gait reading as "floaty" or "skating" while being hard to name by eye.
- `rig_gates.py`: the G1–G10 runner. **A gate whose input is absent reports `unevaluated`, never a
  pass**, and any unevaluated gate makes the whole report not-ok.
- `emit_animation_runtime.py`: emits the R1/R5 runtime as TypeScript — identity bind in attached
  mode, display offset from mesh bounds alone, `refreshTickers()` for lazily-built characters, the
  explode-offset order around the tick loop, and the controller contract. A clip whose loop was
  never measured emits `LoopOnce` with a comment saying so, never a silent `LoopRepeat`.
- `docs/pipelines/character-rigging-animation-1.5.2.md` (the derivation and the failure log),
  `forge/stage5_rig/CONTRACT_1.5.2.md` (module map and payload shape) and
  `grimoire/readiness/animation_contract.md` (the routing file read at Stage R).

### Fixed

- **§R4's limb bands were unsatisfiable as written, and the implementation proved it.** §1 defines
  `handRange`/`footRange` as world-space ranges; under that definition a forward-travelling gait
  gives `footRange ≈ travel` and `handRange ≈ travel + swing`, so no speed lands in the stated
  `0.15H–0.25H` / `≈ travel/2` bands. The limb bands are hip-relative and now say so; the global
  features stay world-space, the only frame the §2 classifier is meaningful in.
- `footRange ≈ travel / 2` compared two different quantities: `travel / 2` is the step length
  between alternating footfalls, `footRange` is a positional range. The `≈` is read as ±30%.

## [1.5.1] — 2026-08-22

### Added

- Add code-only skin and cloth material profiles with validation for engine clamps, folded
  uniforms, and sheen configuration.
- Label multipart GLB nodes from measured bounds while keeping semantic assignments explicitly
  provisional until render confirmation.
- Add executed-geometry gates for flat-colour vertex regions and swept-arc bend, span, and taper,
  plus required semantic-region declarations in render-profile v2.

### Fixed

- Deduplicate `SKILL.md` into one continuous reconstruction loop so every host loads one canonical
  sequence of gates.
- Bound all three axes in the SDF quad pass, preventing out-of-range and aliased cell reads at the
  sampling-grid boundary.

## [1.5.0] — 2026-08-12

**Theme: The Character Update.** A character stops being a stylized approximation. A skeleton is
derived from the component tree and bound to real `SkinnedMesh` geometry, hair gets its own
subsystem spanning all five stages, and left/right becomes an importable constant instead of a
comment. Every gate added here traces to a regression that was measured, not imagined.

This release supersedes the unreleased `1.4.4-beta.1`/`1.4.4-beta.2` line and the `v1.5-beta` tags.
`1.4.4` was never published as a stable version.

### Added

**Animation-ready rigs — new `forge/stage5_rig/`**
- Derive a validated `RigSpec` from the component tree rather than authoring a skeleton beside it,
  so the bones cannot drift from the geometry they drive. A generated humanoid is 61 components and
  49 bones (pelvis, 3 spine, 2 clavicle, 8 arm, 6 leg, head, neck, 30 digit phalanges).
- Emit the bone hierarchy parents-first, one shared `THREE.Skeleton`, exactly one vertex-weight
  helper, and bind every skinned component as a `THREE.SkinnedMesh`. `root.userData.rig` reports
  `bound` honestly rather than asserting it. Enforced: `|Σw − 1| < 1e-5`, every `skinIndex` in
  range, no zero-weight-sum vertex, bone lookup through a `Map`.
- `root.updateMatrixWorld(true)` before constructing the `Skeleton`, because `calculateInverses()`
  reads each bone's current world matrix and those inverses are what cancel the rest pose. Built in
  the wrong order it captures identity matrices and every vertex is displaced by its bone's offset
  at rest — measured, not assumed.
- Add `forge/stage5_rig/geodesic_skinning.py`: weights from distance measured *through the solid*
  on a voxel grid with 26-connectivity and true step lengths, so falloff does not bias along grid
  axes. A vertex no bone can reach is reported, never quietly pinned to the nearest bone in space.
- Add `forge/stage5_rig/validate_rig_payload.py`, which proves structural payload integrity only.
  Pose stress, dynamic bounds and visual likeness remain separate gates, and this is stated rather
  than implied.
- Add canonical humanoid proportions (`forge/stage2_spec/humanoid_proportions.py`) and reachable
  character sub-routes.

**Hair — a subsystem spanning all five stages**
- Replace the single `ellipsoid` that was the entirety of hair generation. Full contract, every
  measurement behind it, and every stated non-goal: `docs/HAIR_PIPELINE.md`.
- Add `forge/_shared/scalp_field.py`: signed distance to a skull built as a stack of ellipse rings,
  derived from the head component rather than authored twice. The sign is exact; the magnitude is a
  first-order estimate that errs toward pushing further, which is the safe direction.
- Add `forge/stage1_intake/extract_hair_evidence.py`, which finally writes the
  `faceLandmarks.hairline` slot that has existed unfilled since v1.2, and measures banded coverage,
  highlight-band position and root-to-tip luminance delta. Views never seen are reported as
  `notObserved` instead of authored as if measured.
- Add `forge/stage2_spec/hair_profile.py`: the hairstyle schema and its validation. Roots bind to
  the scalp as `(u, v)`; an absolute root position is a hard error, which makes the recorded
  "mass slides off the skull" failure structurally impossible rather than a matter of care.
- Add `standProud` as a component property, enforced by an emitted per-vertex clearance march
  (`applyStandProud`). This generalises `hug` out of the hand-written showcase demo and into the
  skill. `maxPush` is required, not optional: an uncapped march walks inner vertices through the
  target and out the far side.
- Add `forge/stage4_review/scalp_exposure.py`, a HARD gate that finds bald patches geometrically
  before anything is rendered. Silhouette IoU is blind to them (only ~11% of figure cells lie on the
  outline) and interior difference cannot separate them from a colour shift. It counts only hair
  *outside* the skull — a nearest-neighbour test passes the failing build, because those vertices
  were still nearby, merely sunk below the surface.
- Add `forge/stage4_review/hair_gate.py`, separating hard failures (a bald patch, always wrong) from
  soft signals (a coverage shortfall, often the right compromise) so the two stop being answered the
  same way. A shortfall never authorises widening the masses on its own.
- Add a `hair.human.code-only` material profile requiring no maps, and a `rootTipGradient` vertex
  colour ramp that runs along the mass's own axis rather than world Y.

**Left and right**
- Add `forge/_shared/chirality.py`. `CHARACTER_LEFT_SIGN` is derived, not chosen: with `forward: +Z`,
  Y up and a right-handed frame, the character's own left is `+X`. It is a constant because a comment
  cannot be imported and two checkouts had already diverged on it.
- `validate_chirality` is a hard spec-time gate naming the actual relation between a pair
  (`rotation` / `translation` / `unrelated`) instead of reporting "mismatch". A pair built by negating
  x *and* z is a 180° rotation, and rotation preserves handedness, so both limbs come out the same
  hand. Fixing that moved the humanoid's hand region 46% closer to the reference in front.
- `chirality.medial_lateral_bias` catches what a pair test structurally cannot: a pair that is wrong
  the *same* way on both sides is still a perfect mirror of itself. Threshold `0.025` is calibrated
  against measured reference feet (`+0.0403`, `+0.0579`) — the first guess of `0.05` would have made
  the gate blind to the defect it was written for.

**Review gates**
- Add `forge/stage4_review/interior_difference.py`: appearance difference *inside* the silhouette,
  banded by height, and required per visual pass. Silhouette IoU reads ~11% of figure cells, so a
  finished face and the same model with its face deleted both scored `0.8803`, and adding an entire
  mouth moved it `-0.0002` — in the wrong direction. An outline metric must not be the signal a
  correction loop optimises for interior work.
- Add the Divine Eye fitting loop (`forge/stage4_review/fit_params.py`), connecting deterministic
  parameter-to-render callbacks to bounded, gate-aware optimisation with explicit provenance.
- Add per-feature correction, self-intersection, turntable, attachment-anchor, joint-loop,
  pairwise-penetration and geometry-integrity gates.
- Add the Python ↔ Three.js render bridge and render profile v2, with capture-readback validation.
- Add `forge/stage4_review/compare_region_passes.py`, which blocks per-region claims that paired
  browser diagnostic passes do not support.

**Material pipeline**
- Add region crop admission, PBR extraction and registry resolution
  (`material_region_analysis.py`, `forge/materials/reference.py`), the spec hand-off
  (`apply_material_analysis.py`), the deterministic camera/crop/microscope plan (`material_views.py`),
  the per-region comparator, bounded material feedback, cross-pass compatibility, and a blocking
  `material_gate.py`. The registry never decides from colour alone; an ambiguous or low-confidence
  region stays `probe`/`request-input`.

**Build**
- Add the `tapered-sweep` primitive: `rx`/`rz` vary per station and framing uses parallel transport,
  because `extrudePath`'s Frenet frames flip 180° at an inflection. Nothing that came to a point
  could be built before, so hair locks, horns and tails were assembled from constant-radius pieces
  that read as noodles. The validator warns when stations do not actually taper.
- Add SDF primitives and subdivision surfaces capped to tessellation tiers, UV unwrap, visual-hull
  carving, morph targets and quadric decimation.

**Intake**
- Add the camera-fitting solver, `probe_glb.py` (provenance, bounds, scene inventory and a
  conservative semantic-readiness assessment), and semantic decomposition.
- Add optional isolated SAM2 / MediaPipe / Depth Anything V2 evidence adapters
  (`run_vision_adapter.py`). They never approve a pass and never supply geometry.
- Add a pure-stdlib baseline JPEG decoder with an explicit fallback for unsupported
  progressive/12-bit/CMYK modes, removing the macOS-only `sips` dependency for common inputs.

**Workflow**
- Add a repository-local mandatory workflow checklist, resume gate, and evidence-backed step
  tracking under `.img2threejs/state.json`, with `generic`, `character` and `cs2` profiles inserting
  their required intake gates in order.
- Add per-pass and total correction ceilings derived from `reviewHistory`; `forge/next.py` hard-stops
  when either limit is reached.
- Add fail-closed pipeline routing (`forge/_shared/pipeline_routing.py`): below a confidence of
  `0.82` the track resolves to `request-input` rather than guessing between weapon and character.
- Add an executable CS2 review CLI and profile-specific CS2/character checklist gates.
- Add explicit suitability, projection-route, and material-evidence decisions to every profile.

### Changed
- Resolve the hair strategy question open since v1.2. `plane-card` is rejected for hair: it needs an
  alpha texture this skill cannot emit. The default representation tier is `shell`, not locks —
  measured on the reference GLB, hair surface roughness is 0.00338 against a torso control of
  0.00312, so its hair is a smooth shell with all strand detail in textures. `tube` and `box` are
  rejected for hair as well.
- Exclude `hair`, `detail`, `decal` and `panel` roles from geodesic skinning. The field measures
  distance through the solid, so on the test fixture a crown vertex takes 8.1% neck weight through
  the skull and shears against it under rotation. Rigid components are excluded from the returned
  weights, not merely noted beside them.
- Give the primitive list one owner. `generate_threejs_factory.py` kept its own copy of
  `VALID_PRIMITIVES`; a primitive present in one list and absent from the other did not error, it
  was silently rewritten to `box`.
- Keep progressive-disclosure references while making CS2 intake, deterministic review gates,
  self-correction, multi-angle review, part coverage, and action-ready validation explicit router
  requirements.
- Order each pass as build, render, Tier 1, multi-angle, deterministic pass check, profile review,
  AI review, and sync.
- Make regeneration action-aware so `refine-code` cannot overwrite the artifact it must repair,
  and reject spec paths that disagree with local state.
- Make the material aliases, CS2 vocabularies and spec-search examples English-only; the dataset
  vocabulary stays bilingual by design.
- Resolve the companion showcase through `IMG2THREEJS_SHOWCASE_ROOT` instead of an absolute path, and
  remove machine-specific paths from the shipped skill. Research distillations stay local.

### Fixed
- Let a `tapered-sweep` station collapse to a real point. A ring of radius zero still carried
  coincident vertices and zero-area triangles, so a sweep ended in a blunt cap the width of
  floating-point noise — and the taper warning would have passed it.
- Resolve the instanced-cluster base primitive through the shared subdivision helper, so emitted
  helpers and geometry calls cannot drift at that seam.
- Read the version front-matter key the release tooling actually writes.
- Score an empty IoU/edge union as `0`, not `1`, and treat inverted or tiny foreground masks as
  unusable evidence rather than a perfect match.

### Known limits at this release
Stated because a schema reads exactly like a working feature:
- There is no `hairProfile` → `componentTree` compiler. The schema is validated; a spec still authors
  its hair components directly.
- `scalp_exposure` measures spec-derived points while the clearance march displaces vertices at
  runtime, so the gate does not measure the geometry that ships. Moving the march into Python at
  build time is the largest improvement still available in that subsystem.
- Lock-tier hair parameters and every hair gate threshold are derived, not measured, and report
  themselves as such. No multipart GLB with separated hair geometry exists to calibrate against.
- `BoneSpec.ik` exists in the schema and nothing populates it. Pose-sweep gating is not implemented.
- Hair dynamics and strand-level hair are permanently out of scope: a single image carries no motion,
  and this pipeline emits no textures or alpha.

## [1.4.3] — 2026-07-30

The accepted current release line is `1.4.x`. GitHub Releases are the canonical changelog from
the governed `v1.4.3` tag onward.

### Changed
- Release publication now occurs only from an approved annotated version tag; merging a pull
  request never changes the version or creates a release.

## Invalid historical record: 1.5.0 — not released

The entry below was generated by the retired push-to-`main` release automation. It is retained as
historical context only and does not represent an accepted release or release-note baseline.

### Added
- add Python CI and automated releases
- update changelog and roadmap
- enhance skill and strict cs2 component render

### Fixed
- stabilize pHash brightness invariance
- align tests with review evidence gates
- sponsor donate link + weekly and all-language Trendshift badges (#42)
- use the logo mark for the README, at its real aspect ratio (#36)
- restore assets/logo.svg so the README logo stops 404-ing (#35)

## [1.4.1] — 2026-07-26

The hardening update for the CS2 reconstruction pipeline: explicit component coverage, a pistol
assembly contract, and review evidence that distinguishes real structure from a convincing texture.

### Added
- **Assembly coverage gate** — `stage4_review/check_part_coverage.py` verifies that every specified
  component is built, prevents multiple specified components from collapsing into one mesh, and
  reports unowned meshes and inventory details that never reached the spec.
- **Glock-18 adapter** — the CS2 route now supports a dedicated `pistol` / `glock-18` component tree
  with separate slide, frame, magazine, trigger-guard, control, barrel, and internal-mechanism
  contracts; it does not reuse the knife topology.
- **Structure-first guidance** — documented rules for named, explodable and selectable parts, plus
  correct layout scaling for exploded views in `SKILL.md` and
  `grimoire/build/geometry_patterns.md`.

### Changed
- **Strict review evidence** — the pipeline requires map-stripped blockout evidence, ordered pass
  credit, and thickness- and long-axis viewpoints before a visual pass can continue.
- **Geometry-integrity checks** — Tier 1 now surfaces open separate geometry, insufficient seams,
  constant blade grinds, and missing distal taper so projection cannot hide structural defects.

## [1.4.0] — 2026-07-25

**Theme: Weapon Pipeline.** Image-matched CS2 hard-surface reconstruction: evidence-backed identity,
projection-first finish matching, family-specific geometry, and gate-driven review.

### Added
- **CS2 intake and provenance contract** — reference admission, technical probing, identity routing,
  metadata lookup, VPK/texture discovery, and an atomic `cs2-intake.json` hand-off that preserves
  uncertainty instead of guessing.
- **CS2 knowledge base and local search** — bilingual BM25 search profiles, curated vocabulary and
  anatomy references, plus provenance-aware result handling for specification work.
- **Family-specific reconstruction** — knife adapters, supported subtype validation, component-tree
  contracts, projected-texture baking, and a strict-quality route for CS2 assets.
- **Evidence-backed CS2 review** — `cs2_review.py`, geometry-integrity measurements, fixed and orbit
  review views, family/finish/projection/critical-detail gates, and versioned review-scene metadata.
- **Reference preview and prompt assets** — a browser smoke-tested CS2 knife preview, reference
  fixture, and focused knife, pistol, and technical-analysis skill prompts.

### Changed
- **Projection-first finish workflow** — de-lit reference crops are the default path for matching
  skin patterns, decals, and painted surfaces; procedural finishes remain an explicitly disclosed
  fallback.
- **Divine Eye calibration** — scale and aspect signals are live, and the reconstruction rescue path
  now requires objectness, soft-fidelity, and proportion evidence rather than accepting an IoU-only
  result.

## [1.3.0] — 2026-07-22

The "quality & efficiency" line: a deterministic-first review harness (Divine Eye), stronger
input integrity, geometry-truth gates, and reference-grounded texture/material analysis.

### Added — Plan 1.3 (Phases 1–7)
- **Input integrity** — reference admission (`check_reference_admission.py`), intake-correctness
  cross-check (`check_intake_correctness.py`), property auto-binding, shared pHash.
- **Geometry truth** — curve-sweep (F.6), flatness gate (G.1), Blum lathe-profile derivation.
- **Divine Eye** — deterministic multi-signal ensemble (`divine_eye.py`): IoU/scale hard gates;
  proportion / symmetry-parity / pHash / SSIM / edge / blowout / flat / tonal-parity soft signals;
  self-uncertainty `probe` routing.
- **Multi-angle** — degenerate-view detection (`diagnose_render_multi_angle.py`) with reference-free
  self-consistency; auto-framing.
- **Eye judgment layers** — gated VLM gate (`vlm_gate.py`), per-feature verification (§3.8),
  bounded stop policy (§3.6), calibration harness (report-only + separation check).
- **Efficiency** — per-module codegen cache (§3.7 neighbor invalidation).
- **Presentation** — reference-conditional post-fx (DOF/bloom) strictly off the evaluation path.

### Added — session capability work (folded into 1.3)
- **Texture-finish analysis** — `stage1_intake/analyze_texture.py`: classifies finish
  (gem-metal / gemstone / painted-metal / worn-composite / brushed-steel / plastic) and writes
  doc-grounded MeshPhysicalMaterial scalars; `grimoire/build/threejs_texture_reference.md`.
- **Objectness (OSIM-lite)** — `stage4_review/objectness.py`: pure-stdlib HOG-like descriptor +
  cosine similarity; wired into Divine Eye as a soft signal + reconstruction-mode rescue.
- **`ground-blade` primitive** — lofted beveled cross-section (primary bevel + swedge/false edge)
  in the generator + validator whitelist.
- **Color-gate fix** — `diagnose_render.py` `color_is_gated(pass_id)` (color hard-fail only from
  the material pass onward, so clay blockouts don't false-fail).

### Added — reconstruction-fidelity upgrades (folded into 1.3)
- **Reference-grounded gradient stops** — `stage1_intake/extract_gradient_stops.py`: foreground-masked
  per-band median sampling extracts a material's true gradient from the reference (kills hand-guessed
  STOPS), names hue zones, and flags blue-leaning violet/blue stops (`B > R`) as `blue-collapse`
  (collapses to blue under tone-mapping) with a magenta-lean suggested correction.
- **`candy-coat` finish class** — `stage1_intake/analyze_texture.py`: an anodized/PVD/doppler
  dielectric-led recipe (metalness 0.35 / clearcoat 0.60 / envMapIntensity 0.70) so a saturated
  coloured coat keeps its hue instead of the environment stealing it; chrome-specular stays
  `gem-metal`, bright-clean stays `gemstone`. Plus a `paletteHueRisk` hue-survival annotation.
- **CIEDE2000 colour math** — `_shared/color_metrics.py`: sRGB→CIELAB + full ΔE00, verified against
  the canonical Sharma test pairs.
- **Colour-aware Divine Eye signals (report-only)** — `hue_zone_parity` (per-band CIEDE2000 along the
  axis; catches "purple rendered blue" that luma/structure signals miss) and `specular_wash`
  (saturation-decay + hue-drift-toward-cyan detector). Both ship report-only (no ensemble weight)
  until calibrated, so they never silently move a verdict.
- **InstancedMesh emission** — repetition systems now emit one `THREE.InstancedMesh` (single
  draw-call) instead of a per-instance `Mesh` clone loop; the `instanced-cluster` primitive resolves
  to its base geometry instead of failing.
- **`ground-blade` UV fix** — blade UVs now span the geometry's actual Y bounds instead of a
  hardcoded range, so an off-origin blade no longer clamps every face to the bright spine-rim row
  (the flat "one colour" / white-tip bug); the length gradient reads correctly.
- **Dep-free cutouts** — `extrude` supports `THREE.Shape.holes` + an `ovalLoop` helper (e.g. a
  wire-cutter oval hole) with no CSG dependency.

### Notes
- Pure Python 3.10+ stdlib in `forge/` (no pip installs). 20/20 forge test suites green.
- Grimoire lessons updated: shading realism (hue-survival under tone-mapping; reference beats prose),
  geometry patterns, self-correction.

## [1.2.0] - 2026-07-21

**Theme: Humanoid character generator.** Characters and hybrid subjects become
first-class citizens of the reconstruction pipeline, alongside a round of engine
and harness improvements to the underlying code generator.

### Added

- **Character / hybrid domain detection.** Assessment now recognizes character-like
  form language and routes the reconstruction through an anatomy-aware track instead
  of the hard-surface object path.
- **Humanoid component template.** A flattened humanoid template with measured
  head-unit proportions, facial landmark placement, and pose alignment is emitted
  from the assessment stage.
- **Proportion-lock build pass.** New gated pass that enforces anatomical proportion
  correctness before form/material work proceeds.
- **Feature-placement build pass.** New gated pass that places and validates facial
  and body landmarks against the reference.
- **Per-part character materials.** Skin, hair, cloth, and accessory materials
  integrate with the Track A detail machinery for stylized human figures with
  recognizable likeness.
- **Surface topology classification.** Parts are classified by surface topology to
  drive more accurate geometry choices.
- **Per-part color / RGBA recipes.** Explicit per-part color and RGBA material
  recipes for tighter reference matching.
- **Tier-1 diagnostics.** Diagnostic reporting layer for the generation harness.
- **Hash caching.** Content-hash caching to avoid redundant recompute across passes.
- **Real extrude / lathe / tube geometry.** Genuine extrude, lathe, and tube geometry
  generation replaces prior approximations.

### Changed

- Restructured the project layout ahead of the full harness rebuild, including
  stage-prefixed script names for clearer pipeline ordering.

### Docs

- Published a public ROADMAP (v1.0 → v1.5) and a token-cost document.
- README remake: 3D showcase, live-demo links, new logo, and animated GIF previews
  (shotgun, knife, war-hauler, Sony, Doraemon House, Crowned Loot Chest).
- Added LICENSE, CONTRIBUTING, and a community-outreach promotion playbook.
- Funding pointed to the VN donate page (MoMo / VietQR).

## [1.1.0] - 2026-07-15

**Theme: Detail-first analysis.**

### Added

- Required `detailInventory` artifact enumerating identity-defining micro-details
  (gloss zones, bevels, fasteners, engraved/painted linework, contours, stains, wear).
- Strict-quality gate that blocks code generation until every detail maps to a real
  component or material entry, preventing shallow specs from reaching the renderer.

## [1.0.0] - 2026-07-15

**Theme: Object pipeline.** Initial release.

### Added

- Staged sculpt pipeline: blockout → structure → form → material → lighting →
  interaction → optimization, with a visual gate on each pass.
- Image suitability validation and `ObjectSculptSpec` authoring (components + materials).
- Render-vs-reference review loop using side-by-side comparison sheets.
- Action-ready runtime hierarchy exposing pivots, sockets, and colliders.
- Token-efficient, code-only output (diffable TypeScript + JSON spec, no binaries).

[1.5.1]: https://github.com/img2threejs/img2threejs/compare/v1.5.0...v1.5.1
[1.5.0]: https://github.com/img2threejs/img2threejs/compare/v1.4.3...v1.5.0
[1.4.1]: https://github.com/hoainho/img2threejs/compare/v1.4.0...4e9fbecae0e63b370581737c89991d4dca84c287
[1.4.0]: https://github.com/hoainho/img2threejs/releases/tag/v1.4.0
[1.3.0]: https://github.com/hoainho/img2threejs/releases/tag/v1.3
[1.2.0]: https://github.com/hoainho/img2threejs/releases/tag/v1.2.0
[1.1.0]: https://github.com/hoainho/img2threejs/releases/tag/v1.1.0
[1.0.0]: https://github.com/hoainho/img2threejs/releases/tag/v1.0.0
[1.4.3]: https://github.com/img2threejs/img2threejs/releases/tag/v1.4.3
