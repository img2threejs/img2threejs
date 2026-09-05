# GLB animated-character prompt

> **Checklist authority note (extract-animated-character, 2026-09-03):** the `animated-character`
> profile is served by the installed **plugin-character** (`img2 add img2threejs/plugin-character`);
> its `domain.json` owns the rig-step order and invokes the plugin's `tools/` ports. The
> `forge/stage5_rig/...` commands below remain runnable as the base LIBRARY the emitters use, but
> the checklist runs the plugin's copies -- when the two disagree, the plugin is the authority.

One copy-paste prompt that carries a subject from a GLB reference to a **rigged, animated,
gate-cleared** procedural Three.js character, for img2threejs 1.5.2 and later.

It is the animated sibling of [`prompts/build.md`](standard-prompts/build.md). That one stops
at a static reconstruction; this one continues through Stage R — rigging, animation, and the G1–G10
gates — under one rule the static route never needed:

> **Repair the mesh before you freeze it. After the freeze, rigging may only ADD.**

## Why this prompt exists

Animation used to break meshes, and the reason was structural rather than accidental.

1. **The gates were unreachable.** Every module under `forge/stage5_rig/` was callable and nothing
   in the workflow ever told anyone to call one. `next.py` walks the checklist, so a gate absent
   from the checklist never runs — and a gate that never runs reports a clean verdict forever. The
   `animated-character` profile puts Stage R on the checklist, so the gates are now unskippable
   rather than merely available.
2. **The rig was authored beside the GLB instead of read from it.** A GLB carries a real skin and
   real clips; its animation channels target ITS node indices and its `skinIndex` values address
   ITS joint array. Feeding either into a procedurally authored skeleton indexes a different
   skeleton entirely, which shreds the mesh. Hence: read the rig FROM the GLB, and where a
   procedural skeleton must coexist, build an explicit measured correspondence rather than assuming
   the indices line up.
3. **Nothing proved the mesh survived.** `validate_rig_payload.py` proves structural payload
   integrity only — by its own scope note, never pose stress or likeness. A green payload was being
   read as "animation is safe". `mesh_parity.py` closes that: hash the geometry before rigging,
   re-hash after, and a single changed float names the mesh, the attribute and the vertex.

Two distinct symptoms have two distinct causes, and it is worth knowing which one you are looking
at before hunting:

| What you see | What it is |
|---|---|
| Nothing moves; the scene renders fine | Binding never reaches the node, or inverse binds cancel the wrong pose — **G1** |
| Parts fly apart / the figure is shredded | Geometry rewritten during rigging, or joint indices addressing a different skeleton — **mesh parity** and **correspondence** |

## How to use it

1. Replace every `<PLACEHOLDER>`.
2. Paste the whole thing as one prompt. It is written to complete in a single pass.
3. A hard stop is the prompt working. Answer the question it asks rather than telling it to continue.

## The prompt

````text
Build a rigged, animated procedural Three.js character from a GLB reference using img2threejs 1.5.2.

## Inputs
- GLB reference:  <ABSOLUTE_PATH_TO_GLB>
- Reference photo (optional): <PATH_OR_NONE>
- Subject name:   <SubjectName>
- Demo id:        <subject-id>
- Showcase root:  <PATH_TO_img2threejs-showcase>
- Real longest dimension: <e.g. 1.70 m>   # sanity-checks scale; never used to scale

## Hard contract — do not violate these

1. **The GLB is a measurement instrument, never an asset.** Symlink it into `public/mesh/`
   (gitignored). No `.glb`/`.bin` may be fetched by the running demo. Its topology and textures are
   measured, never copied into the factory.
2. **Rigging and animation are REFERENCED FROM THE GLB.** Skeleton, skin joint order, inverse bind
   matrices and clips come from `forge/stage5_rig/glb_rig_reference.py`, not from invention. The
   skin's joint ordering is the authority — never traversal order, never node names.
3. **Repair before the freeze; after the freeze, only ADD.** If meshes are broken, fix them in the
   `mesh-repair` step and say what you fixed. Then freeze. From that moment vertex positions,
   normals, uvs and indices are evidence: rigging may add `skeleton`, `skinIndex` and `skinWeight`
   and nothing else. `mesh_parity.py verify` must pass. If it fails, the rigging is wrong — never
   re-freeze to make it pass.
4. **State gate on every step.** `python3 forge/next.py --state .img2threejs/state.json` at start,
   at resume, and before every correction. Exit 3 / `status=stopped` is a hard stop: report and ask,
   never continue from memory.
5. **Never label a node from its name.** Baseline assets name nodes `root.0..root.N` and rigs name
   joints `node_17`. Labels come from measured world-space bounds; joint correspondence comes from
   measured position. Treat every label as `hypothesis-requires-render-confirmation`.
6. **An unevaluated gate is not a pass.** If a gate's input is missing, produce the input or report
   the gate as unevaluated with the reason. Never record a gate as green because it did not run.
7. **Never claim "done" when it is "improved."** Name what still does not match.

## Procedure

### Stage 0 — Intake, on the animated profile
```
python3 forge/state.py init --state .img2threejs/state.json --reference <glb> \
  --profile animated-character --spec object-sculpt-spec.json
python3 forge/stage1_intake/probe_glb.py <glb> --out glb-probe.json
python3 forge/stage1_intake/label_glb_nodes.py <glb> --out nodes.json --min-confidence 0.6
```
`--profile animated-character` is what puts Stage R on the checklist. On `character` the rig gates
are absent and the build will complete without ever running them.

Read `glb-probe.json` for `skinCount` and `animationCount` before anything else. `skinCount > 1`
means you must choose a skin explicitly and say why. `animationCount: 0` means Stage R4 authoring,
not R3 identification — a different route with different gates.

### Stages 1–3 — Reconstruction
Follow `docs/standard-prompts/build.md` for the geometry route (cross-section loft, implicit surface
only where the loft cannot reach, TypeScript encoding, `verify_cells.mjs` before any encode). None
of it changes here. Come back when the static model renders and its gates are green.

### Stage R — rigging and animation

Read `grimoire/readiness/animation_contract.md` and
`docs/pipelines/character-rigging-animation-1.5.2.md` completely first. Both are short and every
rule in them traces to a measured failure.

**R-1. Read the rig from the GLB.**
```
python3 forge/stage5_rig/glb_rig_reference.py <glb> --out glb-rig.json
```
Report `deformVsTechnical`: clips routinely target technical nodes that are not joints, and
building those as bones corrupts the skeleton's index space. If a procedural skeleton must coexist
with the GLB's, build the correspondence and read `usable` — an unmatched joint on either side
means it is not usable for retargeting, and inventing the mapping is what shreds a mesh.

**R-2. Repair, then freeze.**
Inspect the meshes and repair what is broken NOW. Then:
```
node runtime/scripts/export_mesh_buffers.mjs --url <preview> --out meshes.json
python3 forge/stage5_rig/mesh_parity.py freeze meshes.json --out mesh-manifest.json
```
Everything after this point is measured against that manifest.

**R-3. Bind — additively.**
```
python3 forge/stage5_rig/validate_rig_payload.py <spec>
```
Then bind. Three rules, each of which produced a real failure:
- `mesh.bind(skeleton, new THREE.Matrix4())` — identity, always, in attached bind mode. Attached
  mode recomputes `bindMatrixInverse` from `matrixWorld` every frame, so `matrixWorld` cancels out
  entirely. Binding with the armature's matrix rendered a blank frame; adding the armature
  translation to the display offset moved the figure by exactly that translation.
- The display offset comes from the **mesh bounds alone**: `(−centre.x, −min.y, −centre.z)`.
- `updateMatrixWorld(true)` BEFORE `new THREE.Skeleton(...)`. `calculateInverses()` reads each
  bone's current world matrix; constructed first it captures identity, the rest pose never cancels,
  and every vertex is displaced by its bone's offset. That failure compiles, binds, reports
  `bound: true`, and renders a corpse.

**R-4. Prove the mesh survived.**
```
node runtime/scripts/export_mesh_buffers.mjs --url <preview> --out meshes-after.json
python3 forge/stage5_rig/mesh_parity.py verify mesh-manifest.json meshes-after.json
```
`skinIndex`/`skinWeight` appearing is expected and reported as legally added. A changed position,
normal, uv or index is a failure naming the mesh and the first differing vertex.

Use `export_mesh_buffers.mjs`, not `export_mesh_geometry.mjs`. The latter multiplies every vertex by
`matrixWorld` — correct for self-intersection, wrong here in both directions: posing changes
`matrixWorld` without touching the buffer, so a correctly rigged mesh would report as changed; and a
real buffer edit cancelled by a compensating transform would report as unchanged.

**R-5. Measure the clips; never name them by guess.**
```
python3 forge/stage5_rig/glb_rig_reference.py <glb> --sample-clips --landmarks landmarks.json \
  --out sampled-clips.json
python3 forge/stage5_rig/clip_features.py sampled-clips.json
```
Source clips arrive as `NlaTrack.001` and carry no information. Rename from measurement and record
which measurement the name rests on. A name that describes motion is provable; a name that implies
intent is not — no kinematic feature distinguishes a strike from a stumble, so that carries
`inferred: true`. Loop comes from `poseReturn`, never from travel: a clip that wanders and returns
loops, and one that ends mid-stride a centimetre away does not.

`scaleDelta ≠ 0` is a tripwire, not a descriptor. Surface it before anything else proceeds.

**R-6. Gates.**
```
python3 forge/stage5_rig/rig_gates.py rig-gate-payload.json
```
G1 is the one that catches silent death: seek each clip to five times and compare each node's actual
transform against `track.createInterpolant().evaluate(t)`, requiring `maxSampledBindingDelta ≤ 2⁻²³`.
It is the only check that distinguishes a clip that plays from one that exists, loads, holds an
action, reports a duration and drives nothing. Under-coverage is a failure, not a pass: a harness
that sampled one clip once is not evidence.

G10 is visual and needs a real sweep. Five poses is not coverage — the sweep that found the actual
defects was 11 clips × 4 times × 2 sides × 2 azimuths = 176 frames. Report holes and creases as two
numbers; a single combined score hides the fact that Stage R2 trades one against the other.

### Stage 8 — Prove nothing is fetched, then look at it
Move the binary directory aside, rebuild, re-render. Renders that still appear are the proof.
```
npx tsc --noEmit && npx vite build
```
Then actually watch the animation, at 0°, 40° and a grazing angle. Metrics hide structural failure.
If the render and the metric disagree, the render is right.

## Report format
Per stage: what was **measured** (the number and the command that printed it), what was **decided**
from it, what remains **unverified**. Close with:
- every gate and its status, with `unevaluated` listed as unevaluated and never as a pass;
- the mesh-parity verdict, and what was repaired before the freeze;
- which clips were named by measurement and which carry `inferred: true`;
- per-region confidence, and what still does not match the reference.

"This rig cannot be animated from this reference without breaking the mesh" is a valid result. Say
it rather than shipping a shredded figure.
````

## Where each rule comes from

| Rule in the prompt | What it prevents |
|---|---|
| `--profile animated-character` | Stage R gates existed but nothing invoked them; a gate that never runs reports clean forever |
| Rig read from the GLB | GLB clips target GLB node indices; fed to a procedural skeleton they index a different rig and shred the mesh |
| Correspondence by measured position | Joint names are `node_17`; a name-matched correspondence is confidently wrong |
| Repair before freeze | Freezing a broken mesh certifies the breakage instead of catching it |
| Freeze before bind | Binding first leaves nothing to compare against |
| Parity verified after bind | Binding is the only moment "implementation did not touch the mesh" can be falsified |
| Identity bind in attached mode | Binding with `armature.matrixWorld` rendered a blank frame; a `skinSpace` wrapper gave a half-height floating figure |
| `updateMatrixWorld` before `Skeleton` | Inverse binds capture identity, the rest pose never cancels, and it renders a corpse while reporting `bound: true` |
| Loop from `poseReturn` | The travel-based rule contradicted its own data: `idle-gesture` travels 0.121H and loops correctly |
| G1 coverage counts | A harness reporting a tiny delta over one clip at one time is not evidence |
| 176-frame sweep | Five poses missed defects that a real sweep found in 28 frames |

## Related

- [`docs/pipelines/character-rigging-animation-1.5.2.md`](pipelines/character-rigging-animation-1.5.2.md) — the derivation and the failure log.
- [`grimoire/readiness/animation_contract.md`](../grimoire/readiness/animation_contract.md) — the routing file read at Stage R.
- [`forge/stage5_rig/CONTRACT_1.5.2.md`](../forge/stage5_rig/CONTRACT_1.5.2.md) — module map and payload shapes.
- [`docs/standard-prompts/build.md`](standard-prompts/build.md) — the static reconstruction route this one continues.
- [`docs/standard-prompts/glb-force-measured.md`](standard-prompts/glb-force-measured.md) — when every parameter the GLB measures should be forced rather than approximated.
