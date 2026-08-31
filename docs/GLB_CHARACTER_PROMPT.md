# GLB-reference character prompt — build

The first of three prompts for the GLB-mediated character route. It takes a GLB reference and
produces the procedural TypeScript that reproduces its measured surface, with no `.glb` or `.bin`
fetched at runtime.

| | |
|---|---|
| **Use it when** | you have a GLB reference and no built surfaces yet |
| **Do not use it when** | there is no GLB — use the single-image route in the README's "Driving it harder" section instead; or the build already completed and the result merely looks wrong, which is [`GLB_CHARACTER_POLISH_PROMPT.md`](GLB_CHARACTER_POLISH_PROMPT.md) |
| **Next** | [polish](GLB_CHARACTER_POLISH_PROMPT.md) if the shape is off, then [animation](GLB_CHARACTER_ANIMATION_PROMPT.md) |

## Read this before using it

**This is reference material, not a guarantee.** It is written to be general, so it cannot know your
subject: a different GLB has different node counts, different feature sizes and a different density
ceiling, and the measured figures quoted throughout came from one character. Expect to adapt it, and
expect the first run not to be the last. Treat every number in it as an example of *what to measure*,
not as a value to copy.

**Do not re-run it hoping for a better result.** A full pass re-splats every node and is expensive.
If it completed and the shape is wrong, the polish prompt localises the cause in one node instead of
rebuilding all of them. Re-run this prompt only when the surfaces were never built — the emitted
header's `nodes` count tells you which case you are in.

## Requirements

- an img2threejs checkout, and a companion `img2threejs-showcase` checkout for the emitted demo
- `uv sync --project integrations/glb_character_pipeline` — the integration needs numpy and Pillow,
  which the stdlib-only core deliberately does not
- a multipart GLB is preferred; a merged single-node asset still runs, but cannot support per-region
  claims

## The prompt

Copy everything inside this block, fill the placeholders, and paste it as a single message.

````markdown

Build a procedural Three.js character from a GLB reference using img2threejs 1.5.1.

## Inputs

```
GLB reference:            <ABSOLUTE_PATH_TO_GLB>
Diffuse texture:          <ABSOLUTE_PATH_TO_PNG_OR_NONE>
Reference photo:          <PATH_OR_NONE>
Demo id:                  <subject-id>          # lowercase, hyphenated; becomes src/demos/<id>/
Showcase root:            <PATH_TO_img2threejs-showcase>
Real longest dimension:   <e.g. 1.70 m>         # sanity-checks scale; never used to scale
```

## Force these to the GLB, 1:1

The reference is a **measurement instrument**, and every parameter it genuinely carries is measured
rather than approximated. Do not eyeball, round or "improve" any of these — each one has a source in
the file and a check that proves it landed:

| Parameter | Where it comes from in the GLB | How it is verified |
|---|---|---|
| Size and bounds | node world-space bounds from `probe_glb.py` / `label_glb_nodes.py` | figure height and per-node extents match the reference; state the residual in metres |
| Proportions | per-band widths and centroids | `mesh_reference_compare.py --align landmarks` — `widthDelta`, `depthDelta`, `centroidXDelta`, `centroidZDelta` per band |
| Geometry | the node's own vertex cloud, contoured by Surface Nets at the measured cell | `verify_cells.mjs` reports 0 collisions and every quad rebuilt; `verify_roundtrip.mjs` reports identical vertex and triangle counts, identical colours, and positions inside the quantisation step |
| Base colour | `baseColorTexture` x `baseColorFactor`, sampled per vertex, sRGB decoded to linear | median per-region colour delta against the reference render |
| Roughness, metalness | `metallicRoughnessTexture` green/blue x `roughnessFactor` / `metallicFactor`, per vertex | the emitted material record per region, read back and compared to the GLB's declared figures |
| Region count and extents | `semanticDecomposition` plus measured bounds | every node in `CHARACTER_NODES` appears in the emitted header's `nodes` |

Scale is measured, never fitted: the cell size that recovers the grid must be the measured value
carried at full precision. Rounding a measured 1.504 mm to 1.50 mm shifted the recovered grid and
produced 110,695 apparent cell collisions on one node.

### Three things cannot be 1:1, and saying so is part of the job

Read this before promising a match, because two of the three are commonly assumed to be available
and are not:

1. **Rigging and animation are usually absent from the asset.** A baseline GLB typically reports
   `skinCount: 0`, `animationCount: 0` and carries no `JOINTS_0` or `WEIGHTS_0`. There is nothing to
   match 1:1 — every joint, weight and pose is computed, and that must be reported as computed. If
   your GLB *does* carry skins or animations, say so and ask before proceeding: matching them is a
   different deliverable from this pipeline's contract.
2. **Texture images, the UV atlas and normal maps are deliberately not copied.** SKILL.md's contract
   is that the reference's "topology/materials are never copied into the factory", and the skill
   emits no textures. The *values* above are measured and baked into code; the image files are not
   shipped. A surface detail below the cell size — leather grain, fabric weave — is therefore
   authored procedurally, and must be labelled authored, not measured.
3. **Anything finer than the cell.** A 1.5 mm cell cannot carry a 0.2 mm feature. That is a
   resolution statement, not a tuning failure; lower the cell for that node or state the limit.

If a build genuinely needs the baseline's UV atlas, the pipeline has one gated escape hatch —
`CHARACTER_ALLOW_BASELINE_UV=1`, which `bake_atlas_uvs.py` refuses to run without. It is documented
as an authorised deviation from the no-baseline-assets rule. Using it means the result is no longer
purely code-only, so say that in the report rather than leaving the claim standing.

## Step 0 — Preflight. Do this before anything else and stop if any line fails.

Stage 2 is what makes the result resemble the GLB, and it **skips silently** when numpy is
missing — the run then exits 0 having produced nothing, and Stage 3 re-encodes an empty
directory. That is the single most common way this pipeline "succeeds" and returns a figure
that looks nothing like the reference. So prove the environment first:

```bash
cd <img2threejs checkout>
uv sync --project integrations/glb_character_pipeline
export IMG2THREEJS_GLB_PIPELINE_PYTHON="uv run --project integrations/glb_character_pipeline python3"
$IMG2THREEJS_GLB_PIPELINE_PYTHON -c "import numpy, PIL; print('numpy', numpy.__version__)"
export IMG2THREEJS_SHOWCASE_ROOT=<PATH_TO_img2threejs-showcase>
head -c 4 "<ABSOLUTE_PATH_TO_GLB>" | xxd     # must read glTF
```

Report the printed numpy version. If the import fails, **stop** — do not continue with a
plan to "encode whatever is on disk".

## Step 1 — Write the decoder, because nothing ships one

The integration emits `surfaceData.ts` containing `import type { EncodedNode } from './surfaceCodec'`.
**That file does not exist for a new demo and no script creates it.** Write
`src/demos/<subject-id>/surfaceCodec.ts` now, before any encoding, or Step 5's round-trip
check has nothing to verify against. Do not point `CHARACTER_CODEC` at another demo's copy —
it round-trips against the wrong contract and the mistake surfaces as geometry, not an error.

The decode contract, per node, in stream order:

| Field | Encoding |
|---|---|
| `cells` | zigzag varint deltas of the linear cell index, ascending |
| `offsets` | position inside the cell, 8 bits per axis |
| `colours` | 8 bits per channel, already in the linear working space |
| `edges` | one byte per cell: bit `2*axis` = grid edge changes sign, bit `2*axis+1` = quad winds the other way |
| `exceptions` | varint count, then four vertex indices per quad the edge byte cannot express |

Recover the grid origin from the builder's own rule, `lo = cloud_min - 5*cell`. Never sweep
for a sub-cell phase: a search-based origin put 29% of vertices in an already-occupied cell.
Normals are not carried — recompute them from the rebuilt triangles.

## Step 2 — Intake, and label nodes by measurement

```bash
python3 forge/state.py init --state .img2threejs/state.json --reference <glb> \
  --profile character --spec object-sculpt-spec.json
python3 forge/stage1_intake/probe_glb.py <glb> --out glb-probe.json
python3 forge/stage1_intake/label_glb_nodes.py <glb> --out nodes.json --min-confidence 0.6
```

Baseline assets name nodes `root.0..root.N`, so any name-reading labeller produces confident
nonsense. Labels come from measured world-space bounds and stay
`hypothesis-requires-render-confirmation` until a render confirms them.

Record per node: vertex and triangle count, whether `NORMAL` exists (**required** — the SDF
splat takes its sign from the normal, so a missing or flipped normal inverts the surface
rather than roughening it), and whether the primitive is single-material.

If `semanticDecomposition` reports a merged single-node asset, per-region claims are not
available from this reference. Say so, then **continue** with the whole figure as one region
and label it `single-region-merged-asset` — do not stop, and do not invent regions.

## Step 3 — Measure the spoke budget, per node

```bash
$IMG2THREEJS_GLB_PIPELINE_PYTHON \
  integrations/glb_character_pipeline/python/measure_density_convergence.py <glb> <node>
```

Take **`min(convergence, density ceiling)`** from the printed tables. The tool prints
`density ceiling (largest median <= 5%)` as its last line; if it says `none`, no spoke count
is supported for that node — say so rather than picking the floor.

Convergence alone is wrong and fails silently: `radial_outline` interpolates an empty
angular bin from its neighbours, so past a node's density the outline bridges arcs holding
no vertices and bulges outward.

| Measured on a real subject | |
|---|---|
| 192 spokes on a glove | area grew to 1.12x baseline while IoU *fell* 0.896 → 0.867 |
| same run, a pouch | pushed 7.96 mm past its own point cloud |
| slice count | error vs a 320-slice reference is U-shaped: 13.81% at 20, **8.59% at 40**, 13.83% at 160 |

Slice count stays at 40. A thinner band holds fewer points and its percentile turns to
sampling noise.

## Step 4 — Write one config file, then run one command

Every sub-script defaults to `girl-character` paths (`public/mesh/girl-character-baseline.glb`,
`src/demos/girl-character/surfaceData.ts`, `public/head`). Calling them by hand without the
orchestrator's exports reads the wrong GLB and writes into another demo. **Use the
orchestrator.**

Copy `integrations/glb_character_pipeline/configs/example.env`, fill it, and keep the level
mapping exactly as below — `x3` uses a bigger cell, so `x3` is the *coarser* tier:

```bash
CHARACTER_GLB="public/mesh/<subject-id>-baseline.glb"     # symlink, gitignored
CHARACTER_DIFFUSE="work/<subject-id>-textures/diffuse.png"
CHARACTER_DEMO_ID="<subject-id>"
CHARACTER_NODES="<every node, coarsest-first>"
CHARACTER_LEVELS="x2 x3 default"
CHARACTER_BIN_DIR="public/head-<subject-id>"
CHARACTER_OUT_PREFIX="public/head-<subject-id>/sdf-surfaces"
CHARACTER_WORKDIR="work/head-<subject-id>"
CHARACTER_WORK_TAG="-<subject-id>"
CHARACTER_CODEC="src/demos/<subject-id>/surfaceCodec.ts"
CHARACTER_CELL_SIZES_JSON="<abs path to cells.json>"
CHARACTER_SECTION_REGIONS_JSON="<abs path>"   # all three or Stage 1 silently skips
CHARACTER_SPOKES_JSON="<abs path>"
CHARACTER_CROSS_SECTIONS="src/demos/<subject-id>/crossSections.ts"
CHARACTER_DEST_X2="src/demos/<subject-id>/surfaceDataMedium.ts"
CHARACTER_DEST_X3="src/demos/<subject-id>/surfaceDataLow.ts"
CHARACTER_DEST_DEFAULT="src/demos/<subject-id>/surfaceData.ts"
```

**Start with every node in `CHARACTER_NODES`.** Fidelity to the reference comes almost
entirely from the implicit surface; a cross-section is 2.5D — one radius per angular bin — so
no spoke count recovers a fold that doubles back, such as an eyelid or a nostril. Demote a
node to the loft only after measuring that its noise already matches the baseline.

Cell size is **per node**, from the finest real feature: a lid margin is ~1 mm, so a head
needs ~1.5 mm; a large smooth surface carries nothing that fine and doubles the file for no
measured gain below ~2.5 mm. **Do not round a measured cell.** Stage 3 recovers the grid
origin from it, so rounding a measured 1.504 mm to 1.50 mm shifted the grid and produced
110,695 apparent cell collisions on one node.

Then:

```bash
IMG2THREEJS_SHOWCASE_ROOT=<showcase> \
  integrations/glb_character_pipeline/build-character.sh --config <subject-id>.env
```

## Step 5 — Verify the run did what it printed

Exit 0 does not mean the stages ran. Both Stage 1 and Stage 2 skip with a message and
succeed. After the run, assert all four:

```bash
ls -l public/head-<subject-id>/sdf-surfaces*.bin          # non-empty, mtime from this run
grep -c "^" src/demos/<subject-id>/crossSections.ts        # regenerated, not left alone
git diff --stat -- src/demos/<subject-id>/                 # the tiers that changed
grep -A12 "Measured, on the level shipped here" src/demos/<subject-id>/surfaceData.ts
```

The orchestrator runs `verify_cells.mjs` before every encode and `verify_roundtrip.mjs` after;
both must report **0 collisions, every quad rebuilt**. Read the emitted header's `nodes`,
`vertices` and `bytes per vertex` and confirm they match what Step 3 and Step 4 decided —
that header is machine-printed and is the one number in this pipeline nobody can fudge.

`git diff` each written file. A swapped level→destination mapping writes valid, wrong data
with no error anywhere: tsc, the round-trip check and the build all pass, because the module
is still valid TypeScript carrying a valid but mislabelled level.

## Step 6 — Surface features

Hair is a hard gate on geometry **before** any render:

```bash
python3 forge/stage4_review/scalp_exposure.py --rings <rings.json> --hair-points <points.json>
```

A bald patch is always a failure; a coverage shortfall is a soft signal and never on its own
authorises widening the masses.

A facial feature placed as a thin card (eye, lash) is fitted as a clean analytic form seated
into its socket. Do not chase zero error on the socket rim — make the residual **one-sided**
(always proud, never sunk). Chasing zero cost three rebuilds; two constants fixed it.

Left/right is a **reflection**, never a rotation: negate the lateral axis only, then flip the
triangle winding back or `flatShading` lights the limb as though lit from behind. Two defects
need two checks and `validate_sculpt_spec.py <spec> --json` runs both: `validate_chirality`
catches a rotation mistaken for a reflection, and `medial_lateral_bias` catches a pair wrong
the *same* way on both sides, which any mirror test passes by construction.

**Rigging and motion are not part of this pass.** A skeleton bound to a surface that has not
been gated yet hides surface defects as art problems. Finish Step 8, then use
[`GLB_CHARACTER_ANIMATION_PROMPT.md`](GLB_CHARACTER_ANIMATION_PROMPT.md).

## Step 7 — Gates. One viewpoint is not evidence.

```bash
python3 forge/stage4_review/turntable_gate.py \
  --capture 0=front.png --capture 90=right.png --capture 180=rear.png --capture 270=left.png --json
node $IMG2THREEJS_SHOWCASE_ROOT/pipelines/warrior/export_mesh_geometry.mjs --url <preview> --out meshes.json
python3 forge/stage4_review/self_intersection.py meshes.json --json
python3 forge/stage4_review/attachment_anchor.py <spec> --measured measured.json --json
python3 forge/stage4_review/interior_difference.py <baseline.png> <render.png> --json
python3 forge/stage4_review/diagnose_render_multi_angle.py ...
```

Measure **inside** the silhouette. Silhouette IoU reads ~11% of figure cells: a model with
its face deleted scored 0.8803, identical to four decimals to the finished face.

Read `sampledVertexCount`, `unmeasuredAttachments` and `missingAzimuths` before believing a
clean verdict — each names the part of the model the gate did not look at. A hole through a
skull, a hat at hip height and a floating charm all survived eight front-only review rounds
before these gates existed.

Each gate answers one question and no gate covers another's:

| Gate | The question it answers |
|---|---|
| `self_intersection.py` | does a mesh cross itself |
| `pairwise_penetration.py <meshes> --allow A,B` | do two parts interpenetrate — every allowed pair is one it stops checking |
| `objectness.py --reference R --render X` | does the render read as the object at all |
| `check_part_coverage.py --spec S --manifest M` | is every part the spec promised actually present |
| `vertex_region_gate.py --geometry G --palette P` | did each region land where it was claimed |
| `swept_arc_gate.py --geometry G --component C` | is a curved component actually that curve — a silhouette IoU passes a straight cone |
| `joint_loops.py <meshes>` | is there enough geometry at each joint to survive bending later |

Run `joint_loops.py` in *this* pass even though nothing moves yet. It fails on the surface
build, not on the rig, and finding that out after a skeleton is bound wastes the rig work.

## Step 8 — Prove nothing is fetched, then look at it

Move the binary directory out of the way, rebuild, re-render. Renders that still appear with
that directory absent **are** the proof.

```bash
npx tsc --noEmit && npx vite build
```

**Then actually look at the render, at 0°, 40° and a grazing angle.** Metrics hide structural
failure: a surface can score 2.49 mm median accuracy and still read as a lumpy, hole-riddled
shell rather than a face. If the render and the metric disagree, the render is right.

## Standing rules

1. **The GLB is a measurement instrument, never an asset.** Symlink it into `public/mesh/`
   (gitignored). No topology, material or texture is copied into the factory, and no
   `.glb`/`.bin` is fetched by the running demo.
2. **Verify magic bytes, never HTTP status.** A dev server returns `index.html` with HTTP 200
   for a missing file. Check for `glTF`.
3. **Measure before every numeric decision.** Any spoke count, cell size, threshold or tier
   you cannot point at a printed measurement for is a guess — label it one.
4. **Colour is baked sRGB-decoded to linear**; Three.js treats a vertex-colour attribute as
   already in the working space. Keep `material.color` white or it tints the bake.
5. **State gate at start, at resume, and before every correction:**
   `python3 forge/next.py --state .img2threejs/state.json`. Exit 3 / `status=stopped` is a
   hard stop — report and ask.
6. **Never claim "done" when it is "improved."** Name what still does not match.

## Report format

Per step: what was **measured** (the number and the command that printed it), what was
**decided** from it, what remains **unverified**. Close with per-region confidence and the
regions no view covered; every number you could not measure, named as an assumption; and what
still does not match the reference.

"This cannot reach the requested fidelity from this reference" is a valid result. Say it
rather than producing a confident wrong surface.
````

---

If this run completes and the result still does not match the reference, do **not** re-run this
prompt — it rebuilds everything and localises nothing. Use
[`GLB_CHARACTER_POLISH_PROMPT.md`](GLB_CHARACTER_POLISH_PROMPT.md) instead.
