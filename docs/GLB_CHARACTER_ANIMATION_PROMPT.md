# GLB-reference character prompt — animation

The third of three prompts for the GLB-mediated character route. It rigs and animates a character
whose surfaces are already built and gated.

| | |
|---|---|
| **Use it when** | the figure looks right standing still, and has passed the build pass's gates including `joint_loops.py` |
| **Do not use it when** | the surface has not been gated — a skeleton bound to an ungated surface makes surface defects read as art problems; or `joint_loops.py` fails, which is a surface-build finding and no amount of weight tuning reaches it |
| **Before this** | [build](GLB_CHARACTER_PROMPT.md), then [polish](GLB_CHARACTER_POLISH_PROMPT.md) |

## Read this before using it

**This is reference material, not a guarantee.** The measured figures throughout — dominant-bone
percentages, capsule radii, gait harmonics — came from one character with one axis convention. They
are there to show *what to measure and what a wrong answer looks like*, not to be copied. The sign
table in Step 5 in particular must be re-derived for your own axes; copying it is how a gait ends up
playing backwards.

**Do not run this before the shape is settled.** Rigging is the most expensive pass to redo, because
a surface change invalidates the weights. Finish the build and polish passes first.

## The prompt

Copy everything inside this block, fill the placeholders, and paste it as a single message.

````markdown

Rig and animate a GLB-referenced img2threejs character whose surfaces are already built.

## Inputs

```
GLB reference:   <ABSOLUTE_PATH_TO_GLB>
Demo id:         <subject-id>
Showcase root:   <PATH_TO_img2threejs-showcase>
Motion wanted:   <e.g. "walk cycle", "idle breathing", "draw the sword">
Props:           <rigid items held or worn — sword, bag, tool — or NONE>
```

## What can and cannot be 1:1 here

This is the stage where "match the GLB exactly" most often means matching something the file does
not contain. Establish which case you are in at Step 0 and put it in the report:

| | |
|---|---|
| **Rig, when the GLB carries none** | `skinCount: 0`, `animationCount: 0`, no `JOINTS_0`/`WEIGHTS_0` — there is nothing to match. Every joint, radius, weight and pose is **computed**, and each must trace to a measurement of this model or a cited gait dataset. Report them as computed, never as matched. |
| **Rig, when the GLB carries one** | matching it is a different deliverable from this pipeline's contract, and a licence question. Stop and ask. |
| **Bind-pose proportions** | these *are* 1:1 and must stay so. Joint positions come from the built model's own measured bounds, so a knee sits where the shin mesh ends. Re-measure after rigging: binding must not move a vertex at rest. |
| **Anything below the cell size** | a resolution statement, not a rig problem. |

The one 1:1 check this stage owes the reference: **the rest pose must still match.** Re-run the band
comparison after binding. A skeleton that shifts the figure at phase 0 has broken the shape the
previous two passes measured, and no motion quality makes that acceptable.

## Step 0 — Confirm there is no rig to copy

```bash
python3 forge/stage1_intake/probe_glb.py <glb> --out glb-probe.json
python3 -c "import json;d=json.load(open('glb-probe.json'));print({k:d.get(k) for k in ('skinCount','animationCount')})"
```

A baseline asset typically reports `skinCount: 0`, `animationCount: 0` and carries no `JOINTS_0` or
`WEIGHTS_0` on any primitive. If so, **state it in the report**: there is no rig in the asset, so
every joint position, every skin weight and every pose is computed here. That is also the honest
answer to "which part of this is actually procedural" — this part is, with no alternative.

If the GLB *does* carry skins or animations, stop and ask. Copying them is a different deliverable
with a different licence question, and this pipeline's contract is that reference topology and
materials are never copied into the factory.

## Step 1 — Joints from the model's own bounds, never from fractions of figure height

The factory's parts are named anatomically, so a knee goes where the shin mesh actually **ends**.
Measure the built model and write the table into the rig module. From a real subject:

```
boots        y -0.001 .. 0.167      ankle at the boot top
shins        y  0.158 .. 0.433      knee at the shin top
trousers     y  0.522 .. 1.154      hip below the belt
torso+arms   y  0.974 .. 1.602      shoulder below the neck
head         y  1.525 .. 1.749
```

`left` and `right` are the **character's**, so left is at positive x when the figure faces +z. Write
which way your figure faces and derive from it; do not carry this sentence over unchecked.

## Step 2 — Skin, unless the part is a rigid prop

Decide per part, and record the reason:

- **Skinned**, when one mesh spans a joint. A single node holding the torso *and both arms* cannot
  swing an arm by rigid rotation, and linear blend skinning is also what keeps the surface
  continuous at hip and shoulder where rigid parts open a visible gap.
- **One bone, rigid**, for a prop. A sword allowed both the arm chain and the pelvis measured
  **44.7% arm-bound and 55% hip-bound** — one solid object split between the hand holding it and the
  body it hangs beside, so every arm movement stretched it and the scabbard tore away from the hilt.
  A rigid prop wants a rigid attachment.

## Step 3 — Weights fail in three independent ways. All three need fixing.

This is the core of the stage. Each defect below has a different cause and **none of the three fixes
addresses another**. Skipping any one produces motion that looks wrong in a way weight tuning cannot
reach.

### 3a. Influence radius, not point distance — or the torso follows the arms

Weighting by raw distance to a bone *segment* reads the body inside out: a torso surface sits
~0.15 m from the spine axis while the arms hang against the ribs, so an arm segment passes **closer
to a chest vertex than the spine does**. Measured on a first build, the torso mesh's dominant bone
was `shoulder.L` at **25.2%**, ahead of `spine` at **21.8%** — and the torso duly swung with the arm.

Give each bone a capsule radius taken from measured part widths (that subject: torso x-extent
~0.26 m without the arms, shins ~0.11, boots ~0.14). A vertex inside the capsule is distance zero,
so a thick bone owns its own volume and a thin one cannot reach across the body.

### 3b. Geodesic distance, not euclidean — or raising an arm drags the chest

Straight-line distance does not care whether the path stays **inside** the model. An arm resting
against the ribs is millimetres from the chest *through the air*. Every character whose limbs touch
the body has this defect; it is not an edge case, it is the default pose.

```bash
python3 forge/stage5_rig/geodesic_skinning.py <mesh.json> --bones <bones.json> \
  --resolution <N> --out weights.json --json
```

Geodesic voxel binding (Dionne & de Lasa, SCA 2013) voxelizes the mesh, seeds each bone's voxels and
propagates without crossing empty space, so the arm-to-chest path is forced around through the
shoulder and is correctly long. Purely geometric — no training data, no time constants, nothing to
tune at bind or pose time.

### 3c. A region → bone allow-list — because geometry cannot know how clothing is worn

A capsule radius fixes "the torso follows the arm". It cannot fix "the belt pouch follows the left
leg", because a pouch at hip height genuinely **is** closest to the thigh bone and no radius makes
that false. What makes it wrong is knowledge the geometry does not carry and the part list does: a
pouch is worn on the **belt**, so it rides the pelvis however the leg swings.

Measured before the allow-list existed:

| Part | Dominant bones | Consequence |
|---|---|---|
| cage canister | `hip.L` 66%, `hips` 34% | would swing with the left leg |
| belt pouch, left | `hip.L` 100% | same |
| head and hair | `chest` 58%, `head` 39% | skull owned by the chest capsule — the head would not turn |
| trousers | 13.8% arm-**dominated** | the arm swings and the trousers go with it |

So write an explicit `{region: [allowed bones]}` map. Constrain a rigid prop to one bone. Leave
regions that genuinely span the figure (bare skin, a full-body garment) unconstrained, and say which
ones you left open.

## Step 4 — Topology has to survive being bent

```bash
python3 forge/stage4_review/joint_loops.py <meshes.json> --min-loops <N> --json
```

A joint with too few rings of geometry across it collapses when it flexes: the elbow pinches into a
crease, the knee loses its volume, the shoulder folds. **No amount of weight tuning fixes it**,
because the vertices needed to describe a bent surface do not exist.

Automatic remeshing does not reliably produce deformation-grade topology — but that constraint
belongs to pipelines that *receive* a finished mesh. A procedural generator writes its own topology
and can place loops at joints by construction. If this gate fails, the fix is upstream in the
surface build, not in the rig.

## Step 5 — The gait, as a Fourier series

A walk is not a sine wave and a clamp is not a joint.

The knee flexes **twice** per stride, and that is the whole difference between a walk and a limp: a
small stance flexion of ~18° just after heel strike absorbing the load, and a large swing flexion of
~60° while the foot clears the ground. A single-peak curve reads as disjointed **no matter how the
amplitudes are tuned** — that is the symptom to recognise.

Fit coefficients to standard sagittal joint angles for adult level walking, sampled every 5% of the
cycle from heel strike. Four harmonics were enough to reproduce them to:

```
hip 0.27°      knee 0.89°      ankle 1.44°
```

A truncated Fourier series is smooth by construction — infinitely differentiable and periodic — so
unlike a clamped sine it cannot introduce a kink at any amplitude, and the cycle joins itself
seamlessly at the wrap. The opposite leg is exactly half a cycle behind.

**Verify the signs by forward kinematics, never by eye.** Reversed signs play the gait backwards, and
because the pose stays symmetric it is not obvious — it just reads as moonwalking. Measure where the
ankle actually goes:

| Sign choice | Result |
|---|---|
| hip +, knee − | 66% of the cycle moving forward, peak +2.49 / −4.02 — fast **backward** |
| hip −, knee + | 34%, peak +4.02 / −2.49 — fast forward, **40% swing** |

The second matches the human 40% swing / 60% stance split. Bone local axes are world-aligned in this
rig, so a positive x rotation swings a downward limb to −Z, i.e. backward: hip flexion is −x, knee
flexion +x, ankle dorsiflexion −x. **Re-derive this for your own axis convention rather than copying
the signs.**

Expose one `scale` that trims every joint together, so the walk can be made gentler without changing
its shape. Tuning individual amplitudes is what breaks the coordination between them.

If the figure carries something in one hand, damp that arm's counter-swing — a full swing on an armed
hand reads as flailing rather than walking.

## Step 6 — Chirality: a reflection, never a rotation

Negate the lateral axis only. Reflecting inverts triangle winding, so flip it back or `flatShading`
lights the limb as though lit from behind.

```bash
python3 forge/stage2_spec/validate_sculpt_spec.py <spec> --json
```

That runs two checks that catch two different defects, and you need both: `validate_chirality`
catches a rotation mistaken for a reflection, and `medial_lateral_bias` against a reference catches a
pair wrong the **same** way on both sides — which any mirror test passes by construction.

## Step 7 — Gate through the cycle. A rest pose proves nothing about motion.

Every static gate is necessary and none is sufficient: the defects this stage introduces only exist
while the figure moves. Sample the cycle — at minimum phase 0, 0.25, 0.5, 0.75 — and run the
penetration and coverage gates **at each phase**.

```bash
python3 forge/stage5_rig/validate_rig_payload.py --payload <payload.json>   # BEFORE binding any Skeleton
node $IMG2THREEJS_SHOWCASE_ROOT/pipelines/warrior/export_mesh_geometry.mjs --url <preview?phase=0.25> --out meshes-p25.json
python3 forge/stage4_review/self_intersection.py meshes-p25.json --json
python3 forge/stage4_review/pairwise_penetration.py meshes-p25.json --allow <nameA,nameB> --json
python3 forge/stage4_review/vertex_region_gate.py --geometry meshes-p25.json --palette palette.json --expect expect.json --json
python3 forge/stage4_review/swept_arc_gate.py --geometry meshes-p25.json --component <name> --expect <spec> --json
python3 forge/stage4_review/turntable_gate.py --capture 0=p25-front.png --capture 90=p25-right.png --json
```

- `validate_rig_payload.py` runs **before** any `THREE.Skeleton` is constructed. A bad payload
  becomes a bound skeleton, and a bound skeleton's defects read as art problems.
- `pairwise_penetration.py --allow` is for pairs genuinely permitted to touch. Every pair you allow
  is a pair the gate stops checking — list them in the report.
- `swept_arc_gate.py` fits a curved component's actual bend radius, angular span and taper. A
  silhouette IoU passes a straight cone that occupies roughly the right cells; this does not.
- Read `sampledVertexCount`, `unmeasuredAttachments` and `missingAzimuths` before believing a clean
  verdict — each names what the gate did not look at.

Then **watch the animation**, not a strip of stills: a foot that slides, a limb that pops at the
wrap, and a prop that lags one frame are all invisible in single frames and obvious in motion.

## Standing rules

1. **No typed-in anatomical constant.** Every joint position, radius and amplitude traces to a
   measurement of this model or to a cited gait dataset. Anything else is labelled an assumption.
2. **One change, then re-measure.** Weight defects mask each other; two changes at once means the
   next agent inherits a fact that is not true.
3. **A rest pose is not evidence of motion**, and a strip of stills is not evidence of a cycle.
4. **Never claim "done" when it is "improved."** Name what still reads wrong and at which phase.

## Report format

Per step: what was **measured** (the number and the command that printed it), what was **decided**,
what is still **unverified**. Close with:

- the dominant-bone percentage per region, before and after the allow-list;
- every pair passed to `--allow`, and why;
- which phases were gated and which were not;
- every constant you could not measure, named as an assumption;
- what still reads wrong in motion.

"This motion cannot be reached from this rig without changing the surface topology" is a valid
result — `joint_loops.py` failing is exactly that finding. Say it rather than tuning weights against
a joint with no geometry in it.
````
