# Stage R backlog — what is missing, in the order it should be closed

Distilled from building the 1.5.2 rigging and animation pipeline. Every claim here was verified by
running something, not by reading; where a claim could not be verified it says so.

Companion documents: `STAGE_R_TEST_PLAN.md` (how to test), `GLB_ANIMATED_CHARACTER_PROMPT.md` (how to
run), `pipelines/character-rigging-animation-1.5.2.md` (why each rule exists).

---

## Part 1 — The principles this work produced

These govern everything below. Each one cost real time to learn.

### A gate nothing invokes reports a clean verdict forever

Every `forge/stage5_rig/` module was callable and nothing in the workflow ever told anyone to call
one. `next.py` walks the checklist, so a gate absent from the checklist never runs — and a build
completes green having checked nothing.

**This was then reproduced while fixing it.** Nine rig steps were added to the checklist and
`next_entry()` was never taught to dispatch scope `"rig"`; the steps sat pending forever while the
build reported `complete`. Sixteen tests passed because they asserted the *contents* of the list.

> **Presence in a list is not reachability. Assert the behaviour, not the data structure.**
> Mutation-check it: break the dispatch on purpose and confirm a test goes red.

### `unevaluated` is not `pass`

A check whose input is absent must say so and must make the report not-ok. Four gates are in that
state today, which is why `rig_gates.py` cannot return green yet. That is the system being honest,
and the moment it is softened to "assume pass when unmeasured" the whole suite becomes decorative.

### Two symptoms, two causes — decide which one you are looking at

| What you see | Mechanism |
|---|---|
| Nothing moves; scene renders fine | Binding never reaches the node, or inverse binds cancel the wrong pose |
| Parts fly apart; figure shredded | Geometry rewritten during rigging, or joint indices addressing a different skeleton |

The 1.5.1 build had **both**, and each produced a plausible-looking scene.

### State the frame, always

Mesh parity must read the **raw local buffers**. The existing exporter emits world space — correct
for self-intersection, wrong here in both directions: posing changes `matrixWorld` without touching
a byte (false positive), and a buffer edit cancelled by a compensating transform reads as unchanged
(false negative).

The same class of error was found in the spec itself: §R4's `handRange`/`footRange` bands are
unsatisfiable in world space for a travelling gait, because the limbs go where the body goes. They
are hip-relative; the global features stay world-space.

### Measure before recommending

A guard was added against a double world-bake and then recommended for retention on the grounds that
"there is no evidence sharing is legitimate" — without going to look. Investigation afterwards found
**61 meshes, 61 distinct geometry variables, zero sharing, impossible by construction**. The
recommendation was right by accident and the reasoning was wrong.

> **"I found no evidence against it" is not evidence for it. Go and measure.**

### Claims are not measurements — including other people's

`kimodo`'s reported metrics all **exclude** its post-processing stage "to ensure a fair comparison
between methods", so the correction module's benefit is asserted and never measured. Read other
projects' numbers for what they actually measured.

---

## Part 2 — The backlog

Priority reflects what unblocks the next honest answer, not what is cheapest.

### V — Verification (highest value: nothing below is grounded until these are done)

Everything built so far is machinery to catch a bug **nobody has seen**. The three mesh-damage
mechanisms were inferred by reading code. Building more gates before observing the failure repeats
that mistake at higher cost.

---

**V-1 · Reproduce the mesh damage on a real character**

*Why* — "fixed" is currently unfalsifiable. No render has ever been produced by this work.

*Done when* — a dated recording exists of a real demo (`warrior`, `girl-character` or
`low-poly-humanoid`) showing the failure at three angles (0°, 40°, grazing), plus a frozen
`baseline-broken.json` manifest of its geometry, and the symptom is classified as *corpse* or
*shredded* per the table above.

*Cost* — ~1h. Blocked by nothing. CloakBrowser is installed and all three demos carry a rig.

---

**V-2 · Run one character end to end through `animated-character`**

*Why* — the decisive test of the whole pipeline. Three outcomes, all valuable: the gate catches it
(machinery works), the gate misses it (names the missing gate — the most valuable result), or nothing
breaks (the bug is elsewhere; stop guessing).

*Done when* — the run reaches `rig-gates` with a `mesh_parity verify` verdict recorded, and Phase 4
of the test plan is answered in writing.

*Cost* — ~half a day. Depends on V-1 for a baseline. `rig-payload-validate` will need skipping with
`--reason` until **P-2** lands; that is expected, not a blocker.

---

### P — Producers (four gates can never pass without them)

**P-1 · CLI for `emit_animation_runtime.py` → unblocks G1**

*Why* — G1 is the only check that distinguishes a clip that plays from one that exists, loads, holds
an action, reports a duration and drives nothing. The `runGateR1()` harness that produces
`bindingSamples` is **already written inside the emitter**; nothing exposes it to a shell. Cheapest
possible path to the most important gate.

*Done when* — `python3 forge/stage5_rig/emit_animation_runtime.py <clips.json> --out runtime.ts`
works, the emitted harness runs in a browser and writes a `bindingSamples` block that `rig_gates.py`
accepts, and G1 reports `pass` or `fail` — never `unevaluated` — on a real rig.

*Cost* — small. 6 of 8 stage5 modules already have a `__main__`; this follows the same idiom.

---

**P-2 · Converter producing `rig-payload.json`**

*Why* — the `rig-payload-validate` step calls `validate_rig_payload.py --payload rig-payload.json`
and **nothing writes that file**. `new_sculpt_spec.py:derive_character_rig` produces a differently
shaped `spec["rig"]`. Verified: the filename appears only in `workflow_state.py`.

*Done when* — a converter turns a sculpt spec's `rig` block into the payload schema the validator
reads, the Stage R step runs without `--reason`, and a round-trip test proves the two schemas agree
field for field.

*Cost* — small-to-medium. Needs both schemas read side by side; the mismatch may be substantive
rather than cosmetic.

---

**P-3 · Producers for G3 (`bindRestore`) and G6 (`meshVisibility`)**

*Why* — verified: **zero files** produce either key outside the gate and its tests. G3 catches pose
bleed between clips; G6 catches a part left behind in bind pose.

*Done when* — both keys are emitted by a browser harness in the shape `rig_gates.py` parses, and
both gates report a real verdict on a real rig.

*Cost* — medium. Same harness shape as `export_mesh_buffers.mjs`; reuse its CloakBrowser scaffolding.

---

**P-4 · G10 skin-integrity sweep harness**

*Why* — the visual gate, and the most expensive. Five poses is not coverage: the sweep that found the
real defects was 11 clips × 4 times × 2 sides × 2 azimuths = **176 frames**. Must report holes and
creases as **two** numbers, because Stage R2 trades one against the other and a combined score hides
it. Also needs a **measured blend-off baseline** — an absent baseline leaves G10 `unevaluated` rather
than borrowing someone else's threshold.

*Done when* — the sweep runs, both numbers are reported, and a control run with blending disabled
supplies the baseline.

*Cost* — large. Do last, and only if V-2 shows tearing that the cheaper gates miss.

---

### H — Hardening and plumbing

**H-1 · Decide `runtime/` tracking**

*Why* — `runtime/` is gitignored with **zero files tracked**, so `export_mesh_buffers.mjs` (the
producer for G4/G5/G11) does not ship. This is pre-existing, not new: `export_mesh_geometry.mjs`,
which `SKILL.md` instructs agents to run, is equally absent from the repository.

*Done when* — either `runtime/scripts/` is un-ignored and both scripts are tracked, or the docs stop
referencing scripts a clean checkout does not contain. **A user decision, not an implementation one.**

*Cost* — trivial once decided.

---

**H-2 · Give Stage R its own correction budget**

*Why* — verified: loop caps are global (`maxPerPass`, `maxTotal`; `perPass` keyed by build pass).
Corrections spent in the pass loop can hard-stop a run before any rig step executes, and Stage R
inherits a budget that may already be exhausted.

*Done when* — Stage R carries its own allowance, and a test proves a run that exhausted the pass
budget can still reach `rig-gates`.

*Cost* — small.

---

**H-3 · `mesh-repair` and `rig-bind` produce verifiable artifacts**

*Why* — both are prose steps with no script. `rig-bind` is the step that actually creates the
skeleton; its only downstream check is `mesh-parity-verify`, which proves the buffers were untouched
— **not that the bind is correct**.

*Done when* — each emits an artifact a gate can read: for `mesh-repair`, what was repaired and the
before/after measurement; for `rig-bind`, enough to confirm identity bind, bounds-derived display
offset, and `updateMatrixWorld` ordering.

*Cost* — medium.

---

**H-4 · Put the orphaned gates on the checklist**

*Why* — verified: `turntable_gate`, `self_intersection`, `attachment_anchor`, `interior_difference`,
`scalp_exposure`, `divine_eye`, `vlm_gate` and `correction_loop` are all mandated by `SKILL.md` and
**none has a checklist step id**, so `next.py` never asks for them. Same defect class as Stage R
before it was wired.

*Done when* — each has a step id in the appropriate scope, and a dispatch test (not a contents test)
proves `next_entry()` reaches it.

*Cost* — medium. Do it in one pass and reuse `test_rig_workflow_steps.py`'s dispatch-test shape.

---

**H-5 · `ARCHITECTURE.md` covers all of `stage5_rig/`**

*Why* — verified: it lists **4 of 12** files. Anyone reading it to build a driver misses the entire
1.5.2 stack.

*Cost* — trivial.

---

**H-6 · Second subject for the `single-subject` thresholds**

*Why* — the blend radius `R = 0.006H` and every §2 classifier boundary come from **one rig**. The
walk/run gap (0.400 → 0.799 H/s) and run/dash gap (0.799 → 2.250) are both wide, but wide on one
subject only.

*Done when* — the gaps are confirmed to survive on a second rig, or the thresholds are re-derived.
Until then they stay documented as starting values, never constants.

*Cost* — medium, and gated on having a second rigged asset.

---

**H-7 · Rebase the GLB fast lane onto 1.5.2**

*Why* — verified: the fastlane worktree is `version: 1.5.1` and its `stage5_rig/` holds only the four
pre-1.5.2 files. The two branches are **mutually exclusive**; `--profile glb` and
`--profile animated-character` cannot currently be combined.

*Done when* — one branch offers both profiles and the combined suite is green.

*Cost* — medium. **Do not start before V-2** — merging two branches before knowing whether one of
them is correct doubles the surface to debug.

---

### K — From `kimodo` (NVIDIA Toronto AI Lab)

Researched because it was suggested as a rigging reference. **It is not** — it is a kinematic motion
diffusion model over a hardcoded skeleton that never touches a mesh: no skeleton derivation, no
skinning weights, one trained model per skeleton. It is orthogonal to both of our hard problems.

Three ideas are worth taking, all implementable in stdlib with no checkpoint, no PyTorch and no
NVIDIA Open Model License dependency.

---

**K-1 · `retarget_tag` — semantic role tags instead of position matching**

*Why* — kimodo stamps each joint with an anatomical role (`LeftHand`, `RightFoot`, …) and **asserts
exactly one joint matches each tag**, failing otherwise. Our `correspondence()` currently matches by
measured position, which is better than names but still fuzzy. Because our generator **authors** the
skeleton, it can stamp the tag at emit time — correspondence becomes a join, not an inference. The
exactly-one assertion is a free structural gate.

*Done when* — emitted skeletons carry `retargetTag`, `correspondence()` prefers a tag join and falls
back to position, and the exactly-one assertion is a gate.

*Cost* — small. **But do it after V-2** — correspondence has not yet been shown to be a cause of any
observed damage.

---

**K-2 · Foot contact as an emitted channel**

*Why* — kimodo ships `foot_contacts [T,4]` alongside the motion rather than re-deriving it
downstream. A parallel contact track on our clips makes foot-lock trivial in JS and makes the
contact claim checkable instead of implicit.

*Cost* — small.

---

**K-3 · Two-detector contact-consistency gate**

*Why* — compare **declared** contacts against an **independent geometric detector**. Catches
"animation looks fine but the contact metadata lies" — a class our current `footSlide` gate cannot
see. Computable from joint positions alone: pure stdlib, no new dependency.

*Liftable constants, quoted from kimodo*: `contact_threshold = 0.5`, `vel_thresh = 0.15 m/s` and
`height_thresh = 0.10 m` for the consistency detector; `vel_thresh = 0.2 m/s` and
`height_thresh = 0.05 m` for foot-skate; `above_ground_offset = 0.007`; `root_margin = 0.04`.
These are **their** measurements on **their** skeletons — treat as starting values and re-derive,
exactly as with our own `single-subject` numbers.

*Cost* — small-to-medium.

---

**Explicitly not adopted from kimodo:** the checkpoint (282M params, ~17GB VRAM, PyTorch +
LLM2Vec, weights under NVIDIA Open Model License — the Apache 2.0 covers **code only**); the
MotionCorrection C++ library (needs a C++17 toolchain, undocumented algorithm, **benefit never
measured**); one-model-per-skeleton (incompatible with a per-image skeleton); BVH as interchange
(SOMA-only, reintroduces an opaque asset plus a metres→cm scale trap).

---

### S — Seam continuity under animation

The requirement that meshes stay **continuous while animating** is currently listed under *what this
pipeline does not solve*, and the gate that would measure it has no producer. These three close that.

Full design: `PLAN_1.6_ANIMATION_STAGE.md` §3.

---

**S-1 · Shared-boundary welding with a unified weight zone**

*Why* — proximity blending is a **mitigation, not a fix**, and says so: holes fell 974 px/30 blobs →
287 px/15 blobs while creases rose 16% (31,316 → 36,470 px). Its own residual note states the
complete fix is *"one continuous mesh with a unified weight field, which changes the part structure
and breaks per-part UI"* — and per-part UI is a shipping gate.

The third path was already **declared** in the showcase IR and never built:
`topologyBridge: "shared-boundary"` + `deformationBridge: "shared-weight-zone"`. `validateContinuity()`
only checks the constraint objects are well-formed; it never touches geometry. A declaration with
nothing implementing or verifying it — the same defect class as a gate nothing invokes.

*Done when* — vertices on a welded seam share **one bit-identical binding**, so no pose can separate
them; parts remain separately addressable so explode/click still passes `check_part_coverage.py`; and
the 176-frame sweep reports **zero background pixels** through every welded seam.

*Cost* — large. Touches topology. **Do not start before V-1** measures the real gap first.

---

**S-2 · G10 producer — promoted from P-4**

*Why* — without it S-1 is unverifiable, and the current hole/crease numbers are inherited from an
earlier build rather than measured on today's code. Originally ranked last on cost; the continuity
requirement makes it essential rather than a luxury.

*Done when* — as P-4, plus a re-measured baseline for the current pipeline.

---

**S-3 · A `rig` group in the correction loop**

*Why* — verified: `correction_loop.py` mentions `rig`, `skin` and `weight` **zero times**. Its groups
are camera → silhouette → face → clothing → accessory → materials → lighting. Visual feedback can
therefore never drive a rig or weighting fix, only a material or lighting one.

*Done when* — a `rig` correction group exists, and a defect visible only under animation routes to it.

*Cost* — medium.

---

## Part 3 — Suggested order

```
V-1  reproduce the damage          ← start here; everything else is speculation until this
V-2  run one character end to end
      │
      ├── the run names what actually matters, then:
      │
P-1  G1 producer                   ← cheapest gate with the highest value
P-2  rig-payload converter         ← removes the one hard stop in Stage R
H-1  runtime/ decision             ← trivial, user's call, unblocks shipping the producer
H-2  Stage R correction budget
H-5  ARCHITECTURE.md
      │
S-2  G10 producer                  ← was P-4; the continuity requirement makes it essential
K-1  retarget_tag                  ← the standardisation Stage 6 clips are authored against
      │
S-1  shared-boundary welding       ← the seam fix; needs S-2 to be verifiable at all
      │
P-3  G3 + G6 producers
S-3  rig group in the correction loop
H-3  mesh-repair / rig-bind artifacts
H-4  orphaned gates onto the checklist
K-2  contact channel
K-3  contact-consistency gate
H-6  second subject
H-7  fastlane rebase               ← last: do not merge branches before one is known correct
```

`P-4` is superseded by `S-2`, which is the same harness at a higher priority.

## Standing rules for every task above

1. **Write the falsifying test first.** A test that asserts the data structure instead of the
   behaviour will pass while the feature is dead.
2. **Mutation-check the test.** Break the thing on purpose; if nothing goes red, the test is
   decorative.
3. **A new gate ships with its producer**, or it ships reporting `unevaluated` and says so.
4. **State the frame and the units.** Local or world; absolute or a fraction of figure height H.
5. **Mark every threshold that came from one subject** `single-subject`, including borrowed ones.
6. **Never claim "done" when it is "improved."** Name what still does not match.
