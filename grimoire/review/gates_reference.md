# Gates Reference (full contract)

Read this reference completely before any visual review or `continue` decision. `SKILL.md` keeps
only the executable order and one-line summary; this file defines the mandatory gate behavior.

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
  background, and renderer version. The CS2 family boundary covers supported **knife** subtypes and
  the **Glock-18** pistol; every other family and unknown subtype must stop with
  `unsupported-family` or `unsupported-subtype`, and must never receive another family's component
  tree as a generic fallback. Registering a family is not implementing its geometry. Authoritative
  boundary and intake order: `grimoire/intake/cs2_intake_contract.md` and `SKILL.md`'s CS2 section.
- **Bounded correction loop (token-burn safety)**: `forge/stage4_review/correction_loop.py`
  guarantees termination (success/repeated-defect/oscillation/plateau/hard-ceiling), escalating to
  `request-input` — never a silent infinite burn. Hard gates route to `refine-code`; repeated defects
  and oscillation route to `refine-spec`. Deterministic analysis-by-synthesis parameter fitting and
  Divine Eye provenance: `grimoire/build/analysis_by_synthesis_fitting.md`.
- **Executable Divine Eye fitting**: `fit_against_divine_eye()` in
  `forge/stage4_review/fit_params.py` connects deterministic parameter-to-render callbacks to
  bounded gate-aware Divine Eye optimization. Clean candidates use raw fidelity; hard-gated
  candidates score below all clean results while retaining original fidelity and provenance. The
  returned objective and optional selected raw fidelity are explicit, and each copied record has
  candidate/reference/render provenance. It lazily loads the default evaluator and returns
  normalized raw-fidelity correction-loop provenance without mutating sources.
- **Interior difference (required per visual pass)**: `forge/stage4_review/interior_difference.py`
  measures appearance INSIDE the silhouette, banded by height. Silhouette IoU is computed from
  roughly 11% of figure cells — the ones on the outline — so it is blind to the other 89%. Measured:
  a finished face and the same model with its face DELETED both scored 0.8803, identical to four
  decimals, and adding an entire mouth moved the outline metric −0.0002, in the wrong direction. An
  outline metric must never be the signal a correction loop optimises for interior work. It refuses
  to score when either foreground mask fell back to whole-frame coverage, and reports `cellsCompared`
  so a difference measured over a handful of cells cannot pass as evidence.
- **Chirality (spec time, HARD)**: `validate_chirality` in `forge/stage2_spec/validate_sculpt_spec.py`
  requires every `-l`/`-r` pair to be a sagittal MIRROR, not a rotated copy. Negating x *and* z is a
  180° rotation about Y, and rotation preserves handedness, so both limbs come out the same hand.
  What this CANNOT catch, so nobody trusts a green result too far: a pair wrong the SAME way on both
  sides is still a perfect mirror of itself, and needs `chirality.medial_lateral_bias` against a
  reference. Convention as code: `forge/_shared/chirality.py`.
- **Scalp exposure (HARD, hair subjects, before any render)**:
  `forge/stage4_review/scalp_exposure.py` finds bald patches geometrically, on points, so it needs no
  browser and no capture. Exposure above `--hard-max` is always a failure. It counts only hair
  OUTSIDE the skull — a nearest-neighbour test passes the failing build, because those vertices were
  still nearby, merely sunk below the surface. Silhouette IoU cannot see a bald crown (it is
  interior) and interior difference cannot separate it from a colour shift.
- **Hair gate (soft, hair subjects)**: `forge/stage4_review/hair_gate.py` compares banded coverage,
  hairline offset and highlight-band position. Its verdict is subordinate to `scalp_exposure`. **A
  coverage shortfall never authorises widening the masses on its own** — doing so took closure from
  42.2% to 40.9%, worse on all six views, with crown exposure up 14.9 points on the worst view,
  because the widened masses slid off the skull. Full record: `docs/HAIR_PIPELINE.md`.
- **Procedural rig contract**: for humanoid/character builds, validate the authored
  `joints`/`parents`/`names`/`matrix_local`/packed skin payload with
  `forge/stage5_rig/validate_rig_payload.py` before binding `THREE.Skeleton`. The gate proves
  structural payload integrity only; pose stress, dynamic bounds, readable screenshots, and
  visual likeness remain separate gates. Payload ownership and non-goals:
  `grimoire/readiness/procedural_rigging_contract.md`.
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
