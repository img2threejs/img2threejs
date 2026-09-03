# Animation contract — 1.5.2

Read this when a character rig must **move**, not merely bind. The payload/bind contract lives in
`procedural_rigging_contract.md`; this file covers what happens after a skeleton exists. The full
derivation, the measured numbers and the failure log are in
`docs/pipelines/character-rigging-animation-1.5.2.md` — read that file at the moment you reach
Stage R, not before.

Every threshold is a fraction of **figure height H**, never an absolute unit. A rig at a different
scale converts; no module may assume `H == 1.0`.

## The one rule

> Nothing about a rig is believed until it has been evaluated at a sampled time and compared
> against the value the track says it should have. **A clip that exists is not a clip that plays.**

Two separate 1.5.1 bugs each produced a plausible scene with **zero motion**: eleven clips held
actions, the mixer held state, buttons dispatched, nothing moved. Neither was visible in code
review. Both were found only by measuring.

## Stage order and the script that owns each

| Stage | What it decides | Script |
| --- | --- | --- |
| R0 | deform vs technical nodes, skeleton index space, landmarks | `stage5_rig/validate_rig_payload.py` |
| R1 | bind space — identity bind, offset from bounds alone | `stage5_rig/emit_animation_runtime.py` |
| R2 | skin conditioning — proximity weight blending | `stage5_rig/skin_conditioning.py` |
| R3 | clip identification — measure, classify, name, loop | `stage5_rig/clip_features.py` |
| R4 | action design — author to a target band, then prove it | `stage5_rig/action_design.py` |
| R5 | runtime assembly — tickers, offsets, controller contract | `stage5_rig/emit_animation_runtime.py` |
| R6 | gates G1–G10 | `stage5_rig/rig_gates.py` |

The module boundaries and the sampled-clip payload shape are fixed in
`forge/stage5_rig/CONTRACT_1.5.2.md`.

## Hard rules, one line each

- A node is a `Bone` **iff it appears in `skin.joints`**. Never infer from names — clips target
  technical nodes that are not joints, and building those as bones corrupts the index space.
- Build all nodes, then attach children, then attach roots. Any other order leaves world matrices
  stale at skeleton time.
- `mesh.bind(skeleton, new THREE.Matrix4())` — identity, always, in attached bind mode. The
  armature transform is already inside `skinMatrix`; adding it again double-counts.
- Display offset comes from the **mesh bounds alone**: `(−centre.x, −min.y, −centre.z)`.
- `scaleDelta ≠ 0` is a tripwire, not a descriptor. Surface it before anything else proceeds: a
  rig that scales joints changes what R2 may legally do to skin weights.
- Loop is decided by **poseReturn**, not by travel: `poseReturn ≤ 0.5°` **and**
  `‖hip(T) − hip(0)‖ ≤ 0.01H`. The 1.5.1 rule contradicted its own data.
- Names that describe motion are provable; names that imply intent are not. Intent carries
  `inferred: true` — that is the honest answer, not a placeholder for a better classifier.
- R2 trades holes against creases deliberately: **a hole shows the background, a crease shows
  skin.** A later stage must never "fix" the crease count by disabling the blend.
- A lazily-built character MUST trigger `refreshTickers()` when its payload resolves, or every
  clip plays silently in a scene that otherwise renders correctly.
- Presentation offsets are removed before the rig ticks and re-applied after. Never restore a
  cached rest pose — it overwrites the pose the animation just produced.

## Gates — nothing ships until all pass

`G1` binding reaches node (`maxSampledBindingDelta ≤ 2⁻²³`) · `G2` deformation finite ·
`G3` bind restore (`≤ 1e-12`) · `G4` weights normalised (`≤ 2e-7`) · `G5` indices in range ·
`G6` every visible mesh bound · `G7` medial/lateral (`leftAnchor.x > 0 > rightAnchor.x`) ·
`G8` foot contact (`footSlide ≤ 0.01H`) · `G9` no joint scale · `G10` skin integrity sweep.

**G1 is the gate that catches silent death** — it is the only check that distinguishes a clip that
plays from one that exists, loads, holds an action and reports a duration while driving nothing.

**G10 needs a harness, and five poses is not coverage.** The sweep that found the real defects was
11 clips × 4 times × 2 sides × 2 azimuths = **176 frames**. Report holes and creases as two
numbers; a single combined score hides the R2 trade.

A gate whose input is absent reports `unevaluated` with a reason. It is never silently a pass.

## What this does not solve

Creases at part overlaps (needs one continuous mesh with a unified weight field — a different
topology contract), retargeting between skeletons (needs joint correspondence and its own gates),
and intent (no measurement distinguishes a strike from a stumble).
