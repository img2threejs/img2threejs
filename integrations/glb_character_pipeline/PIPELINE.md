# Character Pipeline

**Packaged here** as an opt-in img2threejs integration (see `README.md`): parameterised, runnable copies
of the character-agnostic scripts under `python/` and `node/`, a generic orchestrator
(`build-character.sh`), and two `.env` configs (`configs/girl-character.env`, reproducing girl-character's
own build; `configs/example.env`, a template for a new reference). This integration runs entirely against
a companion `img2threejs-showcase` checkout, pointed to via `IMG2THREEJS_SHOWCASE_ROOT` — every showcase-
relative path below (`public/mesh/`, `work/progress.txt`, `src/demos/...`) means "inside that checkout,"
never inside this repo.

Distilled from building `girl-character` in that showcase: a GLB baseline reconstructed as code-only
TypeScript/three.js, with a fully procedural walk rig and no external asset fetched at runtime. This
document is the reusable process, kept separate from the demo's own source so it can be applied to the
next character. Where a stage references a script by a bare `scripts/...` path, that script lives only in
the showcase repo's own top-level `scripts/` (character-specific, not packaged here); where it references
`python/...` or `node/...`, that is a script packaged in this integration.

Everything here is grounded in the showcase's own `work/progress.txt` (48 recorded runs as of packaging)
and the actual scripts. Where a step says "measure," it means a script produced a number before the next
decision was made — not that the number was assumed.

## When to use this

You have a multipart GLB (or several — a PBR and a shaded variant, ideally) as a **measurement target**,
not an asset to ship, and you want a character that:
- renders from TypeScript/three.js code alone, with no `.glb`/`.bin` fetched by the running demo
- matches the baseline's silhouette and surface character closely enough that the two are hard to tell
  apart in a side-by-side, while remaining honest that it is a reconstruction, not a copy
- animates (at minimum a walk cycle) without a rig baked into the source file

## Applicability gate — GLB is optional, check for it before anything else

**A GLB reference is optional for a character build, and this whole integration only applies when one
exists.** Before running anything below, check the current build request for a GLB reference (a
`CHARACTER_GLB` path, an attached/linked `.glb`, or an explicit mention of one):

- **A GLB reference exists** → proceed through Stage 0 onward, as described below.
- **No GLB reference** → **skip every stage in this document (Stage 0 through Stage 9) entirely.** Do
  not treat the absence as an error, a blocker, or something to substitute a placeholder for. Build the
  character through the core img2threejs image-driven procedural pipeline instead (`forge/`) — that path
  never depends on a GLB. This integration has no code path that runs without one; every stage below
  (the cross-section loft included) measures a real GLB point cloud, so there is nothing here to
  partially apply to an image-only build.

## Stage 0 — Intake

1. Symlink the baseline GLB(s) into `public/mesh/` and `public/baseline/` (both gitignored — they are
   measurement instruments, never shipped). A missing symlink resolves to `index.html` under Vite and
   masquerades as a 200 response with the wrong content; check the magic bytes of anything you load, not
   just the HTTP status.
2. Parse the GLB in JavaScript (see `node/encode_surfaces.mjs`'s `cloudMinima`) or Python
   (`python/build_head_surface.py`'s accessor walk) — whichever the rest of that stage's tooling uses.
   Do **not** assume `numpy` is installed; check first (`python3 -c "import numpy"`), and if it is not,
   write the JS equivalent rather than blocking the whole pipeline on one dependency.
3. Record per-node: vertex/triangle count, whether `NORMAL` is present (needed for oriented SDF splatting),
   whether the primitive is single-material/single-connected-component (needed later if you ever want to
   separate sub-features like eye/lash cards — see Stage 6's negative result).
4. Capture a reference photo if one exists and register it as `referenceImage` in the demo entry — it is
   what a human compares the result against, and it is not part of the reconstruction pipeline itself.

## Stage 1 — Cross-section loft (the code-only floor)

Build the coarse body from **measured cross-sections** of each node's own point cloud: cluster the cloud,
slice by height, take the radial concave outline per band, resample onto fixed spokes, loft between bands.
This is real code, fully parametric, and must always work with **zero external data** — it is the fallback
path (`?sdf=0` in this demo) and the thing that proves the pipeline is not secretly shipping the source
mesh.

Key parameters, tuned by measurement, not assumption:
- **spokes per node** = `min(convergence, density)`, where convergence is where the radial outline stops
  changing and density is the most spokes that keep a monotone outline — both measured per node, not
  shared globally.
- **centripetal (α=0.5) Catmull-Rom**, not uniform: uniform overshoot was measured at 31 mm on this model;
  centripetal cut it to 6 mm. Do not clamp the curve to the control polygon — that removes the exact
  bowing that turns a polygon into a circle.
- **adaptive subdivision** to a target edge length matched to the baseline's own measured median edge, not
  a fixed triangle budget.

This stage alone will not match the baseline closely on organic detail (a face, a navel) — a cross-section
is fundamentally 2.5D (one radius per angular bin), and no amount of spoke density recovers a fold that
doubles back on itself (an eyelid, a nostril). That is what Stage 2 is for.

**Read this before treating Stage 2/3 as "more of the same" measurement-to-parameter process this stage
is.** Stage 1 measures the GLB down to a SPARSE set of ring samples per node (girl-character: 748 rings,
86,240 points total, from a 2.1-million-vertex source) plus a handful of tuning numbers (spoke counts,
control-curve behaviour, a target edge length). At runtime a loft function SYNTHESISES the walls between
consecutive rings by interpolation -- that geometry does not exist in the measurement at all. Stage 2/3
does the opposite: it measures DENSELY (every sign-changing voxel cell, millions of vertices) and
re-encodes that whole capture (compressed, but still the whole thing) as embedded code, with no
interpolation at decode time -- what comes back out is the measured surface itself, not a synthesis
between sparse samples of it. Both are "code-only, no runtime fetch," and both embed genuinely measured
data as committed TypeScript; they differ in how much of the surface that data is and whether runtime
code synthesises between it or merely decodes it. Stage 2/3 is reached for only where Stage 1's 2.5D
loft-and-interpolate cannot express the geometry at all (a fold that doubles back on itself).

## Stage 2 — Implicit surface reconstruction (Surface Nets)

For nodes where the loft is not enough, splat a signed distance field from that node's own point cloud
using its own `NORMAL` attribute (oriented reconstruction — unoriented has to guess inside from outside)
and contour it with **Surface Nets**, not marching cubes: Surface Nets is a few dozen lines and one vertex
per sign-changing cell, marching cubes is a 256-case table that is easy to get subtly wrong with only
stdlib available.

- `python/build_head_surface.py <node> <cell>` — the field + Surface Nets for one node.
- `python/export_sdf_surfaces.py <node...> [xN]` — batches the above across every node that needs it,
  bakes per-vertex colour from the diffuse texture (mean of 4 nearest source vertices, **sRGB decoded to
  linear** — three.js treats a vertex colour attribute as already being in the working space), and writes
  a versioned binary (`HEDS`, magic-checked, not just HTTP-status-checked) as the intermediate artifact.
- Cell size per node, not a global constant: a lid margin is ~1 mm, so the head needs ~1.5 mm; the
  trousers carry nothing that fine and cost twice the file for no measured gain at anything below 2.5 mm.
  Measure the noise-vs-cell curve before picking one.

**This intermediate binary is not the deliverable.** It is 107.6 MB for this model, larger than the GLB it
is measured against — the point of Stage 2 is fidelity, and Stage 3 is what makes that fidelity shippable
as code.

## Stage 3 — Compact TypeScript encoding (no binary asset ships)

The insight that makes this stage possible: **Surface Nets places exactly one vertex per sign-changing
voxel cell.** A vertex's cell coordinate identifies it, so the index buffer is derivable from cell
adjacency and does not need to be stored — that was 50.6 MB of 107.6 on this model. Normals are similarly
derivable by recomputing them from the rebuilt triangles (another 25 MB). **Verify this claim on the real
data before encoding anything**, on every node, not just the ones that look regular:
- recover the grid origin from the builder's own rule (`lo = point-cloud min − 5×cell`, read from the GLB
  accessor minima) — it is not safely *searchable* by sweeping a sub-cell phase; that put 29% of vertices
  in an already-occupied cell in this project.
- require vertex → cell to be a bijection (watch for exact-boundary float ties; resolve by searching the
  27-cell neighbourhood for one that still contains the point, with a tolerance scaled to the actual
  float32 precision at that coordinate magnitude — not an arbitrary epsilon).
- rebuild every triangle from cell adjacency alone and diff the triangle **sets** (not just counts) against
  the source. `node/verify_cells.mjs <bin> [glb]` does this and must show 0 collisions and every quad
  rebuilt before you write a single byte of encoder.

What actually gets written, per node (`node/encode_surfaces.mjs <level...>`):
- the active cell list as **varint deltas of the linear index** (zigzag, signed — the source order must be
  *preserved*, not re-sorted, or every later index-based comparison becomes meaningless; sorting to fix an
  apparent non-monotonicity is what silently reordered 1,193 vertices on one run of this project)
- the in-cell offset, 8 bits/axis
- colour, 8 bits/channel
- **4 bits per axis of connectivity** — exists / reversed / which corner the quad's diagonal split starts
  from. Two bits (exists + reversed) is *not enough*: a quad is split into two triangles from its first
  corner, and a rotation of the same four corners cuts the *other* diagonal. On non-planar Surface Nets
  quads (most of them) that is a real, different surface — it rendered as rows of diamond-shaped facets
  across a third of one region before the extra 2 bits were added.
- an explicit exception list for the handful of quads (~0.001% on this model) the reduction cannot
  express, so the rebuild is bit-exact rather than approximately right.

Result on this model: **9.0–9.1 bytes/vertex**, ~20–24% of the binary's size, base64-embedded in a
generated `.ts` module (`node/emit_surface_module.mjs <level> [dest.ts]`) — base64 because the same
data as a plain number array is roughly 10× larger as source text, and every browser has `atob` built in.

**Verify the round trip against the real decoder, not a second implementation of it** — transpile the
actual shipping `surfaceCodec.ts` with esbuild and run it inside the check script
(`node/verify_roundtrip.mjs <level>`), so what is tested is what ships. Compare, per node: vertex count,
worst-case position error against the *quantisation step* the encoding is actually allowed (not an
arbitrary round number — a tolerance the same size as the error it is meant to tolerate measures nothing),
colour delta, and the triangle set by **index**, only after confirming vertex order is preserved (measure
that too — don't assume it).

Ship **multiple detail levels as separate committed modules** (this project ships three:
`surfaceData[.ts|Low.ts|Medium.ts]`), selected in code, not fetched — a detail selector is a UI choice
between modules that are all already in the bundle, never a network request.

## Stage 4 — Runtime decoder

A single decode function (`surfaceCodec.ts`) that:
- walks one shared byte cursor across every node in the stream and **asserts it lands exactly on the
  declared length of each section**, not just the total stream length at the end — a per-node assertion
  catches drift at the node where it happened; a total-length check only tells you *something* drifted,
  after every later node already decoded from the wrong offset.
- range-checks a neighbour cell coordinate *before* packing it into a linear index — a negative offset
  does not go out of range, it silently wraps into a different valid cell.
- recomputes normals from the rebuilt index buffer (never stored).

## Stage 5 — Procedural rig

Build a skeleton and skin weights from the model's own measured proportions, not from a rig baked into the
GLB (there usually isn't one).
- **Capsule-radius weighting** per bone, with a **region → bone whitelist** (a glove only takes arm-chain
  bones, a boot only takes leg-chain bones) and a **trunk-exclusion shield** with smooth attenuation —
  plain distance-to-bone-segment weighting reads the body inside out (a shoulder joint dominating the
  torso's own weights) and drags the wrong body part when an adjacent limb moves.
- Derive the skeleton's spine axis from the **legs**, not the whole model's bounding box — the bbox average
  includes both arms and can put the spine several centimetres off-centre, silently, with no error to
  catch it except a measured band-by-band lateral-influence check.
- 4 bone influences, inverse-linear falloff, **no hard 0→100% steps** at a region boundary — that is what
  tears an arm off at the shoulder during animation.
- Gait curves as a **Fourier series**, not `Math.max(0, sin(...))` — the latter is C¹-discontinuous and
  produces a visible kink at the joint; square the clamped sine, or use a proper series, if you need a
  one-sided pulse.
- **Verify direction with forward kinematics before trusting how it looks**: proportion of the cycle spent
  moving forward vs. backward is a number, and "looks about right" is not.
- Rigid attachments (buckles, studs, fasteners) bind to the **nearest host *surface vertex's* bone**, not
  the nearest bone by raw distance — nearest-bone tore studs off a knee pad whose skin was 97% weighted to
  the hip.

## Stage 6 — Facial features (the hardest, least-resolved stage)

Everything below is real experience from ~20 iterations on eyes specifically. Read it as a decision tree,
not a recipe, because the terrain depends entirely on what the source GLB actually put at that node.

**First, measure what the source socket actually is**, by ray-casting along the face-normal axis from
outside in: is it a shallow bowl, a deep cavity, closed, torn? On this model it was a 20–36 mm deep bowl
with the eye *painted* into the diffuse (flat grey discs) and the lash/eye geometry drawn as thin cards
inside the head mesh. **A distance field splatted from a thin card has no interior, so it always contours
into a torn shell — that is not a bug in Stage 2, it is what an SDF does to a sheet, and it cannot be fixed
downstream.** Confirm before spending time on it: check whether the eye/lash geometry is its own
primitive/material/connected-component (it will not be, on most consumer GLBs) and whether its triangles
are statistically distinguishable from skin by shape (on this model they were not — p50 triangle quality
0.815 at the socket vs. 0.805 on a cheek control patch; a distance field makes uniform triangles everywhere
it runs, including on a torn shell). If both come back negative, **the socket cannot be repaired from
outside the SDF build** — the fix is rebuilding that node's field at a finer cell or with the lash
primitives excluded before splatting, nothing else works, and every attempt below that skips this check
will eventually be reverted.

Approaches tried, cheapest lesson first:
1. **A ball in the socket.** Fails structurally: an eye socket on this kind of asset is a broad shallow
   bowl, not a lid-shaped cavity, so any ball radius either stands proud of the face or shrinks the iris
   below a legible size. Don't start here.
2. **Sculpting a lid by pulling socket vertices onto a shell concentric with the ball.** Tore the face into
   flaps — "near the eye" is most of the eye region on a bowl this size, and a plain proximity sculpt moves
   far more geometry than a lid actually is.
3. **A dome that caps the whole bowl, sized to cover the tearing, with the opening painted into its
   texture.** This is the most promising shape and the one that surfaces every remaining subtlety:
   - the dome's rim depth has to be a **robust fit** (harmonics through a ring of casts, clamped against
     a high-percentile anchor) — the raw casts hit torn cards as often as skin, and a plain average sinks
     the whole dome below the face.
   - the join between dome and socket has a **sign problem, not a precision problem**: fit error puts the
     rim behind the real skin about half the time, and *that* half shows as a dark line all the way round
     the eye. Three rebuilds were spent trying to make the error *zero* (welding to the exact boundary
     loop, ordering it by angle, reading depth per sector) and each traded the gap for a different visible
     defect (a starburst, a ring of saw-teeth, stippled cheeks). What actually worked was making the error
     **one-sided**: lift the rim slightly proud of the fitted depth, so the dome always laps *over* real
     skin and never sinks under it. Two constants, not a fourth geometry rebuild.
   - covering the socket by literally cutting a hole in the head and capping it **always leaves a line of
     sight** between the cap's edge and the hole's edge at a grazing angle, however tight the fit — a cap
     over a hole in a closed surface is unavoidably a hole. Leaving the head's own geometry intact and
     only removing triangles that would visibly *poke through* the dome (compared against the dome's own
     rendered surface, not a second, independently-fitted approximation of it — the latter has a blind
     spot exactly where a shard is dense enough to define its own "surface") removes this failure mode by
     construction.
   - the dome's edge needs to disappear into the face in **three separate channels**, and each is a
     distinct bug class: **colour** (read the real vertex colour at each rim point directly rather than
     approximating it as one face-wide average — an averaged patch is right at one point on the face and
     visibly wrong everywhere else on it); **shading** (blend the rim's normals toward the face's own
     normals at the same point, or the seam is a lighting discontinuity no matter how well the colour
     matches); **material** (a cornea's wet clearcoat has to stay off the skin band via a roughness/
     clearcoat mask, or the blended surround reads as wet plastic).
   - **describing the same boundary shape twice, in two different parametrisations, is the single most
     repeated bug in this whole stage.** Painting the iris opening as a hand-fitted ellipse while the lid
     shape is drawn as cubic Bézier curves guarantees the two disagree somewhere — a bright sliver where
     the ellipse runs wider, a dark band cutting the iris where it runs narrower. Read the boundary back
     out of the canvas the shape was actually drawn on (rasterise, sample the alpha) rather than writing a
     second geometric description of it.
   - texture UV and the dome's own footprint radius are coupled by default (`u,v` as a function of the
     same `(radius, angle)` the geometry uses) — **decoupling one without the other stretches or distorts
     the painted iris.** If you change how far the dome reaches in a given direction, either the texture
     frame has to reach exactly as far, or the mapping needs its own fixed reference span independent of
     the geometry's.
4. **An ellipse that is only the eye, with no surrounding face geometry at all** — the approach actually
   shipped in this project, on the client's explicit direction after (3) kept needing another blend fix.
   This sidesteps every edge-blending problem in (3) because **there is no edge to blend** — the face
   around the eye is simply the face, untouched. The tradeoff is explicit and cannot be avoided by more
   engineering: with no cover, the source's torn socket geometry is visible around the small eye lens; with
   a cover big enough to hide it, you are back in (3) and its edge problems. **State this tradeoff to
   whoever is directing the work rather than re-solving it silently** — it is a real choice, not a bug.
   - Even the "just an ellipse" version is not immune to earlier lessons: it must be a clean **analytic**
     cap (e.g. `depth = -set + cap × (1 − t²)`), not one that still samples the measured, torn socket
     surface — sampling copies the tearing straight into the lens and produces an off-centre iris in an
     irregular blob.
   - Iris/pupil size is specified in millimetres and converted through the mesh's own half-spans; when the
     mesh shrinks, **re-derive the iris size from that conversion**, don't assume it still fits.

## Stage 7 — Detail-level strategy

Don't ship a triangle budget you haven't measured the cost of. For each candidate level, capture the same
six camera views and score (see Stage 8) against the *full* level, not just against the baseline — that
tells you what coarsening actually costs, separately from what reconstruction already costs. On this
project the jump from full to a 4×-coarser level cost 0.02 IoU and was worth shipping at a quarter of the
file size and build time; going further started visibly softening the face specifically, so the head node
was exempted from the global coarsening factor while the body kept it.

## Stage 8 — Verification harness

The single highest-leverage habit in this whole project: **when a fix looks done, hide the thing you just
added and re-render the same shot.** More defects were correctly diagnosed by "render with the new mesh
hidden and see if the problem is still there" than by any other technique — it is what separated "this is
geometry I introduced" from "this is damage already in the source," and got the fix effort pointed at the
right layer more than once after it had been pointed at the wrong one.

Metrics that mattered, computed the same way on both sides being compared:
- **IoU** of the two silhouettes (everything not the declared background colour, with a tolerance wide
  enough to keep the figure's own near-black rim without picking up compression noise).
- **colour error**: mean absolute RGB difference inside the *intersection* of the two silhouettes.
- **surface noise**: mean |Laplacian| of luma inside the silhouette — a roughness reading, not an error;
  the baseline has its own non-zero value, and the question is whether the reconstruction sits near it,
  not whether it is small. A noise value *below* the baseline's is evidence of smoothing, not fidelity.
- Comparing two builds directly (encoded-in-code vs. the intermediate binary it replaced) isolates the
  *encoding's* error from the *reconstruction's* error — do this before also comparing either one against
  the original baseline, or a real distortion becomes impossible to attribute to the right stage.
- If the numpy-based comparator isn't available in the working environment, **write the arithmetic to run
  inside a headless browser** (`node/compare_views.mjs`) rather than skipping the measurement — a PNG
  decodes to pixels there with no dependency at all.
- **A tolerance the same size as the error it's meant to tolerate measures nothing.** A position check
  that quantised to 10 µm against an encoding allowed 9.8 µm of error reported 99.94% of triangles wrong
  when the actual defect rate was near zero, in the opposite direction from a separate bug that really was
  present. Derive the tolerance from the encoding's own stated precision, not a round number.
- **Counting is not comparing.** A check that only compares triangle *counts* will pass a mesh where every
  triangle joins the wrong three vertices. Compare the actual index/position sets.

## Stage 9 — Build & regression checklist

Before calling a pass done:
- [ ] `npx tsc --noEmit` exits 0
- [ ] `npm run build` (`tsc --noEmit && vite build`) succeeds; note bundle size and gzip size if the change
      touched an embedded data module — that number is the actual cost users pay
- [ ] the demo renders with every asset directory it *shouldn't* need moved out of the way
      (`public/head`, `public/mesh`, `public/baseline`) — this is the literal proof nothing is fetched
- [ ] mesh/triangle/region counts read off the live scene match what the encoder reported, within ~1%
- [ ] `node/compare_views.mjs <newBuild> <previousBuild>` shows the change you intended and nothing else
- [ ] `work/progress.txt` gets an entry: what changed, what was measured, what negative results were found
      (a reverted attempt is only worth what it teaches the next person — write it down even when it fails)

---

## Appendix A — Anti-pattern catalog (grep-able, one line each)

- **A relative import breaks the moment the importing file moves.** Packaging
  `verify_roundtrip.mjs` one directory deeper broke its `../work/codec/...` dynamic import --
  `outfile` and the dynamic `import()` must agree on an ABSOLUTE path (resolved against
  `process.cwd()`, since the orchestrator always `cd`s to the repo root first), not a path relative to
  the script's own location. Caught by actually running the packaged copy, not by reading it.
- **A swapped filename mapping writes valid, wrong data with no error at all.** Packaging this pipeline's
  own orchestrator, `x2` and `x3` got assigned to the wrong destination file ("Low" vs. "Medium") --
  every check (tsc, the round-trip verifier, `npm run build`) passed, because the file was still
  perfectly valid TypeScript carrying a perfectly valid, just mislabelled, level. Only `git diff` on the
  file the script had just written caught it. The orchestrator now runs that same `git diff` check on
  every file it writes and prints a warning if a tracked file changed -- add the equivalent check to any
  script that overwrites a file that might already be finished and committed.
- **Trusting an HTTP status without checking content.** A dev server serves `index.html` for a missing
  file with a 200; check the magic bytes / parse result, not just `res.ok`.
- **Sweeping for a grid phase instead of deriving it from the builder's own rule.** A search-based origin
  put 29% of vertices in the wrong cell; the builder's formula got 100% right.
- **A tolerance sized around convenience, not around the error it's meant to catch.** Quantify the actual
  precision the upstream step promises, then set the check a notch tighter than that — not looser.
- **Comparing counts instead of contents.** Same triangle count, same vertex count, wrong mesh.
- **Describing one boundary shape twice in two coordinate systems.** They will disagree somewhere; sample
  the one that was actually drawn instead of re-deriving the other.
- **Coupling a UV parametrisation to a geometry parameter you're about to change.** Decide which one is
  allowed to move before touching either.
- **Trying to make a fitted error exactly zero.** Three rebuilds chasing zero error on a socket rim; two
  constants making the error one-sided (always proud, never sunk) fixed the actual complaint.
- **Cutting a hole and capping it.** Always leaves a sightline at the cap's own edge at some angle; prefer
  "remove only what pokes through," leaving the surface intact everywhere else.
- **A single global average standing in for a gradient.** Skin colour, socket depth, subdivision density —
  whichever one is being averaged, measure it locally instead once the average visibly fails at any point.
- **Assuming a moved/resized mesh keeps its old proportions.** Anything specified in absolute units
  (millimetres) through a converted local frame has to be re-checked against the frame every time the
  frame's size changes.
- **Declaring victory from the intended camera angle only.** The grazing angle, the angle from below, and
  the angle the actual reporter used are where a construction seam shows up; render all of them.
- **Not re-rendering with the new geometry hidden.** The fastest way to learn whether a defect is "mine" or
  "already in the source" and to stop fixing the wrong layer.

## Appendix B — File map (this project's instance)

| Stage | Script/module | Produces |
|---|---|---|
| 1 | `python/slice_node.py`, `python/measure_density_convergence.py`, `python/build_cross_sections.py`, `src/demos/girl-character/crossSections.ts` | measured ring data, loft geometry |
| 1 (gated) | `python/bake_atlas_uvs.py` (`CHARACTER_ALLOW_BASELINE_UV=1` only) | baseline-transferred UVs -- authorised one-off deviation, not the default path |
| 2 | `python/build_head_surface.py`, `python/export_sdf_surfaces.py` | per-node SDF + Surface Nets → `HEDS` binary (dev-only intermediate) |
| 3 | `node/encode_surfaces.mjs`, `node/emit_surface_module.mjs` | `src/demos/girl-character/surfaceData[.ts\|Low.ts\|Medium.ts]` |
| 4 | `src/demos/girl-character/surfaceCodec.ts` | runtime decode → `THREE.BufferGeometry` |
| 5 | `src/demos/girl-character/walkRig.ts`, `hardware.ts`, `measuredAnchors.ts` | skeleton, skin weights, gait, attachments |
| 6 | `createGirlCharacterModel.ts` (eye functions) | eye lens mesh + texture |
| 8 | `node/verify_cells.mjs`, `node/verify_roundtrip.mjs`, `node/compare_views.mjs`, `node/capture-character.mjs` | correctness + fidelity evidence |

## Appendix C — Open item on this character

The eye sockets still show the source's own torn card geometry around the lens (Stage 6, approach 4's
stated tradeoff). It is not fixable from any of the downstream stages — confirmed by measurement, not
assumption (single primitive, single material, single connected component; triangle shape statistically
identical to skin). Closing it requires going back to Stage 2 and either rebuilding node 9 at a finer cell
or excluding the GLB's lash/eye card primitives before splatting. Budget that as its own pass, not a
touch-up on top of Stage 6.
