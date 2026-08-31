# Force-measured — lock the build to what the GLB actually measures

For a build that has a GLB alongside the reference. The GLB is a **measurement instrument**: the
pipeline reads its parameters, encodes the measured surface, and emits TypeScript that carries those
numbers. No `.glb` and no `.bin` ships or is fetched at runtime, and no texture image is copied —
this pipeline emits code, and that contract does not bend because a mesh happens to be available.

Same rule as [`build.md`](build.md) and the GLB prompts on `main`. What this one adds is a single
word: **force**.

## What "force" means, and what "1:1" refers to

Without it, a build measures the GLB and then *approximates* — a diameter becomes "about right", a
roughness becomes a plausible guess, a proportion drifts toward the template. Every one of those is
a value the GLB already answered exactly.

> **1:1 refers to the measured PARAMETERS, not to the file.** Every parameter the GLB genuinely
> carries is forced to its measured value instead of an inferred one, and each comes with the check
> that proves it landed in the emitted code.

Forced to measured:

| | |
|---|---|
| dimensions and proportions | overall size, per-band widths, centroids, part bounds |
| appearance scalars | base colour, roughness, metalness, emissive, alpha mode |
| structure | part count and hierarchy, socket and pivot positions |
| rig, when present | skeleton, skin joint order, inverse binds, joint weights |
| motion, when present | clip names, durations, keyframes |

## What the GLB cannot hand over — say this before the review, not after

None of these is a defect and none is negotiable:

- **Texture images and normal maps are deliberately not copied.** The code-only contract is the
  point of this project, not an obstacle to it. Appearance is carried as per-vertex colour sampled
  from the base-colour map at each vertex UV, so detail finer than the vertex spacing is not carried.
  Where a map drove metalness or roughness, the emitted value is a measured median, and that is
  recorded as a median rather than presented as an authored constant.
- **Nothing finer than the node's own cell size survives**, because nothing finer was measured.
- **Rigging and animation are usually absent.** `skinCount: 0` and `animationCount: 0` are the
  common case; then there is nothing to force and Stage R authoring applies instead.
- Quantisation, stated once so nobody rediscovers it at review: positions are 16-bit over the
  model's own bounding box (~0.03 mm on a 2 m figure, finer than the source triangles), normals are
  16-bit octahedral (~1°, and authored hard edges survive because normals are carried rather than
  recomputed), indices are lossless, and keyframes stay float32 — a quantised quaternion drifts
  visibly over a looping clip.

## Why the usual quality gate is the wrong instrument here

`--strict-quality` exists to stop a **shallow spec** reaching codegen: an unassessed `objectClass`,
an empty `detailInventory`, a `colorMaterialRecipe` nobody derived. Every one of those asks *"did
you infer this responsibly?"*

On a forced build the answer is that the value was not inferred at all — it was measured. So the
gate is **replaced, not lowered**: parity gates compare the emitted code against the measurement,
which is a stricter question than whether a judgement was defensible.

## The prompt

````text
Build this subject with every parameter the GLB measures FORCED to its measured value.

## Inputs
- GLB (measurement instrument): <ABSOLUTE_PATH_TO_GLB>
- Reference image (optional):   <PATH_OR_NONE>
- Subject name:  <SubjectName>
- Demo id:       <subject-id>
- Overwrite:     <yes | no>     # yes = replace an existing demo of this id in place

## Contract

The GLB is measured, never shipped. Symlink it somewhere gitignored; the running demo must fetch no
`.glb` and no `.bin`. Texture images are not copied — appearance is carried as per-vertex colour.
The deliverable is TypeScript.

Part names are HYPOTHESES from measured bounds and must say so. On a rigged mesh the BONE names are
the rig's own and must NOT carry that caveat.

## Step 1 — Ask the bytes what the file is, before decoding it

    python3 forge/stage1_intake/probe_glb.py <glb> --out glb-probe.json

`skinCount` and `animationCount` decide the route:
  · skinCount == 0        static: parts stay separate, an LOD ladder is allowed
  · skinCount >= 1        rigged: read the skin, never author one beside it. See Step 3.
  · skinCount > 1         choose one explicitly and say why
  · animationCount > 0    clips are measured too, keyed to BONE INDEX, never to bone name

## Step 2 — Measure, then force

Take every parameter in the table above from the GLB and write the measured value into the spec.
Where the pipeline would normally infer, it must not: name each forced field and the number it took.

Keep vertices in the space the file put them in. For a rigged mesh that is BIND space — the space
the inverse bind matrices are expressed in. Normalisation is a TRANSFORM, never an edit to the
buffer: scale on the mesh so bones parented to it scale with the skin, offset on the group. Editing
vertices to normalise moves the mesh out from under its own skeleton.

Per-vertex colour is sampled from the base-colour TEXTURE at each vertex UV, not from a `color`
attribute. A rigged GLB typically ships UVs and a texture and no vertex colours at all; reading only
the attribute makes every vertex fall back to the flat base colour and the figure comes out white.
The texture is sRGB bytes and the material factor is linear: apply the factor in linear space, then
convert back.

## Step 3 — If it is rigged, four rules, none negotiable

1. ONE detail level. Do not decimate. Decimation rewrites the vertex list while `skinIndex` /
   `skinWeight` are per-vertex — every weight would address a vertex that no longer exists and the
   figure tears apart the moment a clip plays. It looks perfect until then.
2. ONE skinned shell, not named parts. Rigging merges the mesh; advertising per-part pivots would be
   a contract the geometry cannot honour. Expose the skeleton instead.
3. Bones parents-first, `parent` as an INDEX with `-1` for a root, clip tracks keyed to bone index.
   A clip naming a bone the skeleton does not have is DROPPED and named in the report — never bound
   to whatever happens to sit at that index.
4. `updateMatrixWorld(true)` BEFORE constructing the Skeleton. `calculateInverses()` reads each
   bone's current world matrix; built in the wrong order it captures identity, the rest pose never
   cancels, and the model renders a corpse while reporting `bound: true`.

## Step 4 — Parity gates. These REPLACE the fidelity gates and are stricter, not looser.

Report each as a number, not a verdict:

    vertex count        emitted vs measured     EQUAL
    triangle count      emitted vs measured     EQUAL
    bounding box        emitted vs measured     within the quantisation step
    base colour         per part                within the stated tolerance
    roughness/metalness per part                EQUAL to the measured value or median
    Σ skin weights      per vertex              |Σw - 1| < 1e-5
    skinIndex range     every index             < bone count
    bone count          emitted vs measured     EQUAL
    clip count          emitted vs measured     EQUAL, or the difference named clip by clip
    clip duration       per clip                EQUAL
    binding delta       seek each clip to >= 5 times    <= 2^-23

The last one is the only check that separates a clip that PLAYS from one that merely exists, loads,
holds an action and reports a duration while driving nothing.

A parity gate that cannot be evaluated reports `unevaluated` with the missing input named. Never a
pass.

## Step 5 — Emit

Factory, embedded surface data, codec and spec — all TypeScript, nothing fetched at runtime. With
`Overwrite: yes`, replace the files for this demo id and KEEP its id, camera and registry entry;
do not re-litigate settled decisions.

## What NOT to do on this route

  · do not run `--strict-quality`; it asks about inference and there is none here. Parity is the gate.
  · do not enumerate a `detailInventory` for geometry the GLB already measured.
  · do not smooth, re-topologise, weld or decimate. Those change the thing you are measuring.
  · do not rename what the file already named.
  · do not stop because a fidelity gate is unhappy. Stop when a PARITY gate fails.

## Report

Forced parameters and the value each took · vertex/triangle counts emitted vs measured · bounds
delta · bone count · clips with durations · maxSampledBindingDelta per clip · LOD count · and one
line naming what the GLB could not hand over, from the list above.
````
