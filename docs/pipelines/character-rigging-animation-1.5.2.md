# img2threejs 1.5.2 — Character Rigging & Animation Pipeline

**Status:** implemented in img2threejs 1.5.2 (`forge/stage5_rig/`; module map in
`forge/stage5_rig/CONTRACT_1.5.2.md`, routing in `grimoire/readiness/animation_contract.md`).
**Scope:** computing and designing animation for character rigs.
**Provenance:** distilled from the Lee Sin showcase build (1.5.1). That demo is read-only evidence
here; nothing in this document is specific to it.

Every threshold below is expressed as a fraction of **figure height H**, never in absolute units.
Lee Sin was normalised to H = 1.0, so its measured numbers read directly as fractions; a rig at a
different scale must convert. Where a number came from one subject only, it is marked
`single-subject` and should be treated as a starting value, not a constant.

---

## 0. Why this pipeline exists

Rigging failures in 1.5.1 were not "the animation looks slightly wrong". They were **total and
silent**: eleven clips held actions, the mixer held state, buttons dispatched, and nothing moved.
Two separate bugs each produced a plausible-looking scene with zero motion. Neither was visible in
code review; both were found only by measuring.

The pipeline is therefore built around one rule:

> **Nothing about a rig is believed until it has been evaluated at a sampled time and compared
> against the value the track says it should have.** A clip that exists is not a clip that plays.

---

## 1. The measurement vocabulary

One feature vector underpins the whole pipeline. It is what identifies an unknown clip (Stage R3)
**and** what specifies a clip you are about to author (Stage R4). Same code, both directions.

Sample the clip at `N = 25` evenly spaced times. At each time, record the **world** position of six
landmark joints: `hip`, `head`, `hand.l`, `hand.r`, `foot.l`, `foot.r`.

| Feature | Definition |
|---|---|
| `duration` | clip length, seconds |
| `travel` | `max_t ‖hip_xz(t) − hip_xz(0)‖` — planar displacement of the root |
| `rise` | `max_t hip_y − min_t hip_y` — vertical excursion of the root |
| `speed` | `travel / duration`, in H per second |
| `handRange` | largest per-axis range over both hands |
| `footRange` | largest per-axis range over both feet |
| `headRise` | vertical range of the head |
| `scaleDelta` | `max_t max_j |scale_j(t) − 1|` |
| `poseReturn` | max per-joint transform delta between `t = 0` and `t = duration` |

`scaleDelta` is a tripwire, not a descriptor. Lee Sin measured `0.000` on all eleven clips; a
non-zero value means the source rig scales joints, which changes what Stage R2 may legally do to
skin weights and must be surfaced before anything else proceeds.

---

## 2. Classification thresholds

Derived from eleven measured clips on one subject. Boundaries sit in the empty gaps between
observed clusters, not at round numbers.

```
idle          travel < 0.02H  and  rise < 0.02H
in-place      travel < 0.30H
walk          0.30H/s ≤ speed < 0.60H/s
run           0.60H/s ≤ speed < 1.50H/s
dash          speed ≥ 1.50H/s
jump          rise ≥ 0.15H  and  travel < 0.50H
leap          rise ≥ 0.15H  and  travel ≥ 0.50H
planted       footRange < 0.10H            (feet do not participate)
gesture       handRange ≥ 0.40H  while in-place
```

Validation against the source clips these came from:

| clip | travel | rise | speed | lands as |
|---|---|---|---|---|
| walk-forward | 1.133 | 0.019 | **0.400** | walk ✓ |
| run-forward | 1.465 | 0.021 | **0.799** | run ✓ |
| dash-forward | 2.906 | — | **2.250** | dash ✓ |
| jump-in-place | 0.272 | **0.248** | — | jump ✓ |
| leap-forward | 2.826 | **0.517** | — | leap ✓ |
| idle-still | 0.013 | 0.0001 | — | idle ✓ |
| arms-only | — | 0.008 | — | planted + gesture ✓ |

The walk/run gap (0.400 → 0.799) and run/dash gap (0.799 → 2.250) are both wide. `single-subject`:
confirm the gaps survive on a second rig before hardening these.

---

## 3. Naming and the honesty flag

Source clips arrive named `NlaTrack`, `NlaTrack.001`, … — Blender NLA strip names carrying no
information. Rename from measurement, and record **which measurement the name rests on**.

Names that describe motion (`run-forward`, `arms-only-feet-planted`) are provable. Names that imply
intent (`strike`, `guard`, `taunt`) are **not** — no kinematic feature distinguishes a strike from a
stumble. Those carry `inferred: true`.

```ts
interface ClipName {
  sourceName: string;   // never discarded — keep the accessor-parity chain intact
  id: string;
  label: string;
  measured: string;     // the numbers the name rests on
  inferred: boolean;    // true when wording implies intent measurement cannot prove
  loop: boolean;
}
```

Two of eleven source clips could only be named by inference. Marking them cost nothing and keeps the
distinction between "measured" and "guessed" visible to whoever reads it next.

---

## 4. The loop rule — corrected

glTF carries no loop flag, so it must be decided.

1.5.1 used *"a clip that neither travels nor rises can repeat seamlessly."* **That rule does not
match its own data**: `idle-gesture` has `travel = 0.121` — six times the idle threshold — and is
correctly marked loopable, because what makes a clip loop is not that the root stays put but that
**the last pose returns to the first**.

**Use instead:**

```
loop  ⟺  poseReturn ≤ 0.5° per rotation joint
     AND  ‖hip(T) − hip(0)‖ ≤ 0.01H
```

`poseReturn` is measured directly on the joint transforms, so it catches a clip that wanders and
comes back (loopable) and rejects one that ends mid-stride a centimetre from where it started
(not loopable). Travel and rise stay as descriptors; they stop being the loop criterion.

---

## Stage R0 — Rig intake

**Input:** a joint hierarchy with local TRS, a skin (joint list + inverse bind matrices), clips.
**Output:** a typed node graph, plus a classification of every node.

1. **Deform vs technical.** A node is a `Bone` **iff it appears in `skin.joints`**; everything else
   is a `Group`. Do not infer from names. Lee Sin's rig has 42 deform joints inside a 113-node
   graph, so the graph is far larger than the skeleton and a traversal-order skeleton would be a
   different skeleton — building non-joint nodes as bones corrupts the index space.

   **Correction, measured 2026-08-25.** 1.5.2 originally said "clips target technical nodes that are
   not joints" and cited this rig for it. Re-measured with `stage5_rig/glb_rig_reference.py` on the
   asset the pipeline was distilled from: **all 1,353 animation channels across all 11 clips target
   deform joints; zero target technical nodes.** The rule stands — it is what keeps the skin's index
   space intact, and a rig that *does* animate technical nodes is entirely legal glTF — but the
   evidence offered for it did not reproduce, so the claim is now stated as the risk it guards
   against rather than as an observation. `deformVsTechnical` reports the real count per asset;
   read it instead of assuming either way.
2. **Link, then root.** Build all nodes first, then attach children, then attach scene roots to one
   container. Any other order produces a graph whose world matrices are stale at skeleton time.
3. **Skeleton.** `new THREE.Skeleton(bones, boneInverses)` where `bones[i]` is the node for
   `skin.joints[i]` and `boneInverses[i]` is matrix `i` of the inverse-bind accessor. The ordering
   is the skin's, never the traversal's.
4. **Landmarks.** Resolve the six landmark joints (§1) once and cache them. Prefer topology over
   names: the hip is the highest-degree node nearest the root; hands and feet are chain leaves;
   left/right are separated by the sign of local X.

**Gate R0:** `maxSkinIndex ≤ bones.length − 1`. A single out-of-range index silently reads a
garbage matrix and deforms one vertex to infinity.

---

## Stage R1 — Bind space

**This stage caused every rigging failure in 1.5.1. Read it before writing any binding code.**

Three.js `SkinnedMesh` defaults to **attached** bind mode, in which `bindMatrixInverse` is
recomputed from the mesh's `matrixWorld` on *every* `updateMatrixWorld`. The skinning evaluation is
therefore:

```
v' = skinMatrix · bindMatrix · v          // matrixWorld cancels out entirely
```

Three consequences, each of which was hit and measured:

| What was tried | What rendered |
|---|---|
| `mesh.bind(skeleton, armature.matrixWorld)` | **blank frame** — bind applied twice |
| identity bind + a `skinSpace` wrapper | figure 0.5 m high, floating |
| identity bind + armature translation added to the display offset | figure offset by exactly the armature translation |

**The rule:**

```ts
mesh.bind(skeleton, new THREE.Matrix4());   // identity, always, in attached mode
group.position.copy(displayOffset);          // presentation only
```

and the display offset must be computed **from the mesh bounds alone** — `(−centre.x, −min.y,
−centre.z)` to seat the figure on Y = 0 — with **no armature translation added**. The armature's
transform is already inside `skinMatrix`; adding it again double-counts.

**Gate R1 (the one that catches silent death):** seek each clip to five times, then for every track
compare the node's actual transform against `track.createInterpolant().evaluate(t)`:

```
maxSampledBindingDelta ≤ 2^-23        (float32 epsilon)
```

This proves the binding path *reaches the node*. A clip can exist, be loaded, hold an action and
report a duration while driving nothing — this is the only check that distinguishes the two.

---

## Stage R2 — Skin conditioning

Applies to any character built from **multiple overlapping parts**, which is most generated
characters.

### The failure

Two vertices sitting on top of each other in bind pose but bound to *different* joints **must**
separate the moment those joints diverge. That is not a bug in the weights; it is what the weights
say. It appears as the skin tearing open mid-clip.

### What did not work

Welding only exactly-coincident vertices (within 1e-4 H). It closed the neck-to-collarbone crack
but a sweep of 11 clips × 4 times × 3 azimuths = 132 frames still found cracks in **28 frames,
1,410 px** of background showing through. Adjacent parts mostly **overlap**; they rarely share a
rim, so coincidence-welding finds almost nothing.

### What worked — proximity weight blending

For every vertex with a vertex from **another part** within radius `R`:

```
w_mixed = w_own + Σ_q  w_q · (1 − d_q/R)²
w_final = renormalise( top4( w_mixed ) )
```

Implementation requirements, each non-optional:

- **Dense accumulation.** Expand the 4-influence binding to a dense vector over all joints before
  mixing, then reduce back to 4. Mixing sparse 4-slot bindings directly loses influences.
- **Write after all reads.** Buffer results and commit at the end, or the blend depends on
  iteration order and stops being reproducible.
- **Uniform grid hash at one cell per radius**, so a query touches at most 27 buckets. Brute force
  is O(n²) and unusable past ~50k vertices.
- **Interior vertices are never touched** — they keep the source binding exactly.

**Radius:** `R = 0.006H` `single-subject`. Chosen against a control run with the blend disabled,
sweeping 11 clips × 4 times × 2 shoulders × 2 azimuths = **176 frames**:

| | background through splits | thin dark creases |
|---|---|---|
| blend off | 974 px in 30 blobs | 31,316 px |
| blend R = 0.006H | **287 px in 15 blobs** | 36,470 px |

**The trade is real and must be stated when this stage runs:** averaging two parts' bindings makes
them travel together, which closes the hole, but pulls each slightly off the path its own joints
would take — so creases get *worse* by ~16%. This was accepted deliberately: **a hole shows the
background, a crease shows skin.** Do not let a later stage "fix" the crease count by disabling the
blend.

Residual, documented rather than hidden: creases are not eliminated by this method. Removing them
entirely requires welding the parts into one continuous mesh with a single unified weight field,
which changes the part structure and breaks per-part UI.

**Gate R2:**
```
|1 − Σ w| ≤ 2e-7   for every vertex        (normalised)
maxSkinIndex ≤ bones.length − 1            (still in range after reduction)
```

---

## Stage R3 — Clip identification

For a rig that arrives **with** clips.

1. Sample the feature vector (§1) for each clip.
2. Classify (§2), name, set the `inferred` flag (§3), decide `loop` (§4).
3. Transfer tracks exactly: key times and values byte-for-byte, `STEP → InterpolateDiscrete`,
   `LINEAR → InterpolateLinear`, binding string `${nodeName}.${property}` with
   `rotation → quaternion`, `translation → position`, `scale → scale`.
4. Keep `sourceName`, channel/sampler indices and per-accessor hashes on `track.userData` so the
   parity chain back to one source animation survives renaming.

**Gate R3:** track count, key count, per-track byte equality, duration equality, and exactly two
distinct interpolation constants across the set.

---

## Stage R4 — Action design

For authoring clips a rig does **not** have. This is where the measurement vocabulary pays off
twice: the same feature vector that identifies a clip **specifies** one.

### The loop

```
target features  →  author tracks  →  measure with §1  →  compare  →  iterate
```

A designed clip is accepted only when its measured features land inside the target band. "Looks
right" is not a criterion; the classifier from §2 must agree the clip is what it claims to be.

### Chain resolution

Author on **chains**, not individual joints. Resolve from topology:

- `spine` — root → highest-degree path toward the head
- `arm.{l,r}` — clavicle → hand leaf
- `leg.{l,r}` — hip → foot leaf
- symmetry pairs by mirrored local X within tolerance

**Medial/lateral convention gate:** after resolution, assert `leftAnchor.x > 0 > rightAnchor.x` in
model space. This one comparison catches a mirrored rig — the failure where every left-hand action
plays on the right and nothing about it looks wrong in isolation.

### Primitives

| Primitive | Use | Parameters |
|---|---|---|
| **Gait** | walk / run / dash | cadence, stride, contact fraction, hip rise, arm counter-swing |
| **Ballistic** | jump / leap | takeoff velocity, apex rise, flight time, land absorption |
| **Reach** | strike / gesture | target socket, windup fraction, follow-through, return |
| **Additive** | breathing, sway | amplitude, period, joint mask — layered on any base |

Gait is a phase machine, not a sine wave. Each leg has stance and swing; the **contact constraint**
is what separates an animation from a slide: while a foot is in stance its world position must not
move.

```
footSlide = max over stance frames  ‖foot_world(t) − foot_world(t_contact)‖
require    footSlide ≤ 0.01H
```

This is the single most valuable gate in the stage. A gait that violates it reads as "floaty" or
"skating" to every viewer while being hard to name by eye.

### Design targets, worked

To author a walk for a rig of height H:

```
duration   ≈ 1.0–1.2 s per cycle
speed      target 0.40H/s      → travel = speed × duration
rise       0.015H–0.025H       (hips stay flat; more reads as a limp)
handRange  0.15H–0.25H         (counter-swing, opposite phase to the leg) — hip-relative
footRange  ≈ stride            ≈ travel / 2 per foot per cycle — hip-relative
contact    0.60 of cycle per foot, so both feet are down 0.20 of the time
poseReturn ≤ 0.5°              → loopable by §4
```

**Frame correction — found by implementing this.** §1 defines `handRange` and `footRange` as
**world-space** per-axis ranges, and under that definition the two bands above are unsatisfiable at
any speed: a forward-travelling gait carries its limbs with the body, so `footRange ≈ travel` and
`handRange ≈ travel + swing`. The bands only become meaningful once the root's planar motion is
removed, so the limb bands in this section are **hip-relative** and are marked as such. The global
features — `duration`, `travel`, `rise`, `speed`, `scaleDelta`, `poseReturn` — stay in world space,
which is the only frame the §2 classifier is meaningful in. Two frames, stated, rather than one
frame that quietly cannot hold.

`footRange ≈ travel / 2` also compares two different quantities: `travel / 2` is the **step length**
between alternating footfalls, while §1's `footRange` is a **positional range**. A phase machine
puts the hip-relative foot excursion at `contactFraction × travel` — 1.2× the spec's figure at the
walk contact fraction of 0.60, 0.8× at the run's 0.40. The implementation reads the `≈` as ±30%.

Run: raise speed into `[0.60H/s, 1.50H/s)`, drop contact fraction below 0.5 so a flight phase
exists, raise `handRange` toward 0.35H. The classifier must then report `run`, not `walk`, without
being told — that is the acceptance test.

**Gate R4:**
```
classifier(authored) == intended class          (§2 agrees)
footSlide ≤ 0.01H during every stance frame
scaleDelta == 0                                  (never author joint scale)
poseReturn ≤ 0.5°  for any clip declared loop
no joint exceeds its measured range from the source clips, where source clips exist
```

---

## Stage R5 — Runtime assembly

Three integration traps, all of which produced *zero visible motion* in a scene that otherwise
rendered correctly.

### 5.1 Ticker collection vs lazy geometry

A viewer that walks the scene once at `start()` collecting `userData.tick` **misses every demo
whose rig arrives later**. The mixer holds the action; nothing calls `mixer.update`; every clip
plays silently.

```ts
refreshTickers(): void { /* re-walk the scene for userData.tick */ }
// call after any late geometry lands
```

Any lazily-built character must trigger a ticker refresh when its payload resolves. Same for the
animation UI: mount the panel as a function so it can also run *after* the controller exists.

### 5.2 Presentation offsets vs animated pose

If the viewer offsets parts for an explode/inspect view, it must **subtract the offset it applied
last frame** before the rig ticks, and re-apply after — never restore a cached rest pose. A cached
rest pose overwrites whatever the animation just produced:

```ts
removeExplodeOffset();      // hand the animated transform back
for (const tick of tickers) tick(dt, elapsed);
applyExplodeOffset();       // presentation on top
```

### 5.3 Controller contract

```
play(name)   stopAllAction → restore bind pose → reset → set loop mode → play
seek(name,t) as play, then paused = true, time = t, mixer.update(0), updateMatrixWorld(true)
stop()       stopAllAction → restore bind pose → active = 'idle'
advance(dt)  mixer.update(dt) → updateMatrixWorld(true)
```

Restoring the bind pose **before** each play is what stops clips bleeding into one another: a
clip that ends mid-pose leaves joints displaced, and the next clip's tracks only overwrite the
joints they address. Loop mode comes from the measured `loop` flag: `LoopRepeat, Infinity` or
`LoopOnce, 1` with `clampWhenFinished = true`.

---

## Stage R6 — Gates

Nothing ships until all of these pass. Each exists because its absence let a real failure through.

| # | Gate | Criterion | Catches |
|---|---|---|---|
| G1 | binding reaches node | `maxSampledBindingDelta ≤ 2^-23` over ≥5 times × all clips | clips that play silently |
| G2 | deformation finite | `applyBoneTransform` on ≥64 vertices/mesh/frame, all finite | bad indices, NaN weights |
| G3 | bind restore | `maxBindRestoreDelta ≤ 1e-12` after `stop()` | pose bleed between clips |
| G4 | weights normalised | `|1 − Σw| ≤ 2e-7` every vertex | shrinking/inflating limbs |
| G5 | indices in range | `maxSkinIndex ≤ bones−1` | one vertex to infinity |
| G6 | every visible mesh bound | `visibleMeshCount == visibleSkinnedMeshCount` | a part left behind in bind pose |
| G7 | medial/lateral | `leftAnchor.x > 0 > rightAnchor.x` | mirrored rig |
| G8 | foot contact | `footSlide ≤ 0.01H` in stance | skating gaits |
| G9 | no joint scale | `scaleDelta == 0` unless the source has it | weight-blend invalidation |
| G10 | skin integrity sweep | background-through-split ≤ measured baseline | tearing under motion |

### G10 is a visual gate and needs a harness

Sweeping poses is not optional — **five poses is not coverage**. The sweep that found the real
defects was 11 clips × 4 times × 2 sides × 2 azimuths = **176 frames**.

Method: render on a saturated background the model cannot contain, count background pixels *inside*
the silhouette, then apply a distance transform and keep only blobs thin enough to be a split rather
than a legitimate opening (an armpit, a gap between straps). Report both numbers — holes and
creases — because Stage R2 trades one against the other, and a single combined score hides that.

---

## Appendix — the failures behind the rules

Kept because each cost real time and none was visible in review.

1. **Attached bind mode cancels `matrixWorld`.** Two "fixes" shipped before the right one: a blank
   frame, then a half-height figure. Rule: identity bind, offset from bounds only. → R1
2. **Ticker snapshot before lazy geometry.** Every clip silent, UI fully functional. → R5.1
3. **Display offset double-counting the armature translation.** Exactly 0.5 m of float. → R1
4. **Coincidence-welding instead of proximity blending.** Closed one crack, left 28 frames
   cracked. → R2
5. **Index-paired comparison between meshes of different vertex counts.** Produced a confident,
   completely invalid number (29.08% vs an actual 2.51%). Any cross-mesh comparison needs a spatial
   join. → measurement discipline
6. **Comparing local-space geometry against world-space reference.** Off by 0.47 m and self-
   consistent. Always state the frame. → measurement discipline
7. **Flipped V when sampling textures.** glTF UV origin is top-left. → R2/material
8. **`NlaTrack.00N` shipped as user-facing labels.** Eleven unusable buttons. → R3
9. **A loop rule that contradicted its own data.** `idle-gesture`, travel 0.121, correctly looped.
   → R4

---

## What this pipeline does not solve

- **Creases at part overlaps.** R2 reduces holes and slightly worsens creases. Eliminating creases
  needs one continuous mesh with a unified weight field — a different topology contract.
- **Retargeting between skeletons.** Everything here assumes one skeleton. Cross-rig retarget needs
  a joint correspondence and its own gates.
- **Intent.** No measurement distinguishes a strike from a stumble. `inferred: true` is the honest
  answer, not a placeholder for a better classifier.
