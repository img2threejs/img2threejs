# Transcribe a GLB 1:1 — force route

Copy a GLB into code **as it is**. No reconstruction, no primitives, no interpretation. The mesh
you have is the answer; this route's only job is to carry it into TypeScript without changing it,
and to prove it did not change it.

This is the opposite contract to [`build.md`](build.md) and to
[`GLB_ANIMATED_CHARACTER_PROMPT.md`](../GLB_ANIMATED_CHARACTER_PROMPT.md), both of which treat a GLB
as *a measurement instrument, never an asset*. Here the GLB **is** the asset.

## Why it needs a route of its own

`--strict-quality` was written to stop a **shallow spec** reaching codegen — an underspecified
`detailInventory`, an `objectClass` nobody assessed, a `colorMaterialRecipe` nobody derived. Every
one of those checks asks *"did you infer this responsibly?"*

On a transcription **nothing is inferred**. Every vertex is measured. Running fidelity gates against
it is a category error, and it is the specific reason this route used to stall: an agent producing a
perfect copy was told it had 89 quality failures.

So the gates are **replaced, not skipped**. Fidelity gates go out; parity gates come in. Parity is
strictly harder to satisfy than fidelity — it compares against the source byte for byte, where
fidelity compares against a judgement.

## What 1:1 actually means — the loss budget, stated up front

Do not promise more than this. Every number is a property of the codec, not a tuning choice:

| Channel | Fidelity | Loss |
|---|---|---|
| positions | uint16 quantised over the model's own bounding box | ~0.03 mm on a 2 m figure — finer than the source triangles |
| indices | varint zigzag deltas | **lossless** |
| normals | 16-bit octahedral | ~1°, and authored hard edges survive because normals are carried, not recomputed |
| skin weights | float32, renormalised to Σ=1 | glTF does not promise Σ=1; renormalising is a fix, not a loss |
| keyframes | float32, **not quantised** | none — a quantised quaternion drifts visibly over a loop |
| **colour** | per-vertex sRGB, sampled from the base-colour texture at each vertex UV | **this is the real loss.** Texture detail finer than the vertex spacing is gone |
| **material maps** | metalness/roughness reduced to scalars (medians where texture-driven) | normal / AO / emissive maps are not carried |

So: **geometry is 1:1 within quantisation; appearance is vertex-baked, not textured.**

If appearance must also be 1:1, say so and take the textured variant instead — keep the `uv`
attribute, ship the texture beside the factory, and accept that the result is no longer a
single self-contained code file. Choose deliberately; do not discover it at review.

## The prompt

````text
Transcribe this GLB into a pure Three.js factory, 1:1. Do NOT reconstruct it.

## Inputs
- GLB:          <ABSOLUTE_PATH_TO_GLB>
- Subject name: <SubjectName>
- Demo id:      <subject-id>
- Appearance:   <vertex-baked | textured>     # see the loss budget; vertex-baked is the default
- Force:        <yes | no>                    # yes = overwrite an existing demo of this id

## Route declaration — set this first and do not drift from it

    route: transcription
    exactnessTier: measured-surface
    inference: none

Every part name is a HYPOTHESIS from measured bounds and must say so — except on a rigged mesh,
where the bone names are the rig's OWN and must NOT carry the hypothesis caveat.

## Step 1 — Ask the bytes what this file is, before decoding it

    python3 forge/stage1_intake/probe_glb.py <glb> --out glb-probe.json

Read `skinCount` and `animationCount` first; they decide the whole route:
  · skinCount == 0            -> static route: parts stay separate, LOD ladder allowed
  · skinCount >= 1            -> RIGGED route: see Step 3. Read the skin, do not author one.
  · skinCount > 1             -> choose one explicitly and say why
  · animationCount > 0        -> clips are transcribed too, keyed to BONE INDEX, never bone name

## Step 2 — Transcribe the surface

Keep vertices in the space the file put them in. For a rigged mesh that is BIND space — the space
the inverse bind matrices are expressed in. Normalisation is a TRANSFORM, never an edit to the
buffer: scale on the mesh so bones parented to it scale with the skin, offset on the group.
Editing vertices to normalise moves the mesh out from under its own skeleton.

Per-vertex colour comes from the base-colour TEXTURE at each vertex UV, not from a `color`
attribute. A rigged GLB typically ships UVs and a texture and no vertex colours at all; reading only
the attribute makes every vertex fall back to the flat base colour and the figure comes out white.
The texture is sRGB bytes and the material factor is linear: apply the factor in linear space, then
convert back.

## Step 3 — If it is rigged, four rules, all non-negotiable

1. ONE detail level. Do not decimate. Decimation rewrites the vertex list while `skinIndex` /
   `skinWeight` are per-vertex — every weight would point at a vertex that no longer exists, and the
   figure tears apart the moment a clip plays. It looks perfect until then.
2. ONE skinned shell, not named parts. Rigging merges the mesh; advertising per-part pivots would be
   a contract the geometry cannot honour. Expose the skeleton instead.
3. Bones emitted parents-first, `parent` as an INDEX with `-1` for a root. Clip tracks keyed to bone
   index. A clip naming a bone the skeleton does not have is DROPPED and named in the report — never
   bound to whatever sits at that index.
4. `updateMatrixWorld(true)` BEFORE constructing the Skeleton. `calculateInverses()` reads each
   bone's current world matrix; built in the wrong order it captures identity, the rest pose never
   cancels, and the model renders a corpse while reporting `bound: true`.

## Step 4 — Parity gates. These replace the fidelity gates; they do not relax them.

Compare the built model against the source GLB and report each as a number, not a verdict:

    vertex count        built vs source        must be EQUAL
    triangle count      built vs source        must be EQUAL
    bounding box        built vs source        within quantisation step
    Σ skin weights      per vertex             |Σw - 1| < 1e-5
    skinIndex range     every index            < bone count
    bone count          built vs source        must be EQUAL
    clip count          built vs source        EQUAL, or the difference named clip by clip
    clip duration       per clip               EQUAL to source
    binding delta       seek each clip to >= 5 times    <= 2^-23

The last one is the only check that separates a clip that PLAYS from a clip that merely exists,
loads, holds an action and reports a duration while driving nothing. Two 1.5.1 bugs each produced a
plausible scene with zero motion and neither was visible in code review.

A parity gate that cannot be evaluated reports `unevaluated` with the missing input named. Never a
pass.

## Step 5 — Emit

Factory + embedded surface data + codec + spec, all TypeScript, nothing fetched at runtime. No
`.glb` or `.bin` may be requested by the running demo. If `Force: yes`, overwrite the existing files
for this demo id and KEEP its id, camera and registry entry — do not re-litigate settled decisions.

## What NOT to do on this route

  · do not run `--strict-quality`; it asks about inference and there is none. Parity is the gate.
  · do not enumerate a `detailInventory`; the vertices ARE the detail.
  · do not "improve" the mesh — no smoothing, no re-topology, no welding, no decimation.
  · do not rename anything the file already named.
  · do not stop because a fidelity gate is unhappy. Stop only when a PARITY gate fails.

## Report

vertex/triangle count built vs source · bounds delta · bone count · clips with durations ·
maxSampledBindingDelta per clip · LOD count · appearance mode · and one line: what in the source did
NOT survive the transcription, from the loss budget above.
````
