# Procedural Three.js Object Patterns

Use this reference only when implementing a model.

## Geometry Choices

- box: flat machinery, furniture, panels, blockout masses
- sphere/ellipsoid: fruit, knobs, organic joints, rounded stones
- cylinder/cone/capsule: trunks, pipes, limbs, handles, bottles, rockets
- torus: rings, tires, loops, trim, cable coils
- shape extrude: logos, flat ornamental plates, blades, keys, leaves
- lathe: vases, bottles, bowls, lamps, wheels
- tube along curve: cables, roots, branches, straps, hoses
- instanced mesh: screws, rivets, leaves, needles, scales, pebbles, repeated ornaments
- plane cards: thin leaves, feathers, labels, cloth strips, decals

## Material Recipes

- wood: brown base, vertical grain normal, roughness variation, darker creases, lighter worn edges
- stone: mottled albedo, high roughness, bump/normal noise, lichen/dirt patches
- metal: lower roughness, metalness, edge scratches, anisotropic-looking streaks via texture
- plastic: controlled roughness, subtle color variation, bevels to catch highlights
- leaf/plant: alpha cards or thin shape geometry, green hue variation, central vein, translucent-ish bright rim
- water/glass: transparent material only if needed; add environment/reflection cues or it reads as a flat sheet

## Material Layer Fields

For each material, prefer a layered description:

- `baseColor`: dominant sampled color.
- `colorVariation`: palette, mottling pattern, amplitude, regional masks.
- `roughness`: base value, variation amount, map/pattern source.
- `metalness`: base value and local changes.
- `normal`: procedural pattern, strength, scale.
- `bump`: amplitude and scale for small tactile relief.
- `displacement`: only for silhouette-visible or close-up relief.
- `wear`: edge wear, scratches, chips, polish, exposed underlayer.
- `dirt`: amount, cavity bias, color, vertical streaking, contact staining.
- `localOverrides`: named regions where color/roughness/bump differs from the base.

Local overrides should answer: where, what changes, how strong, and which image evidence supports it.

## Local Feature Types

Use `component.localFeatures` for details that matter to recognizability:

- raised ridge
- recessed groove
- seam line
- screw or rivet
- chip or dent
- scratch cluster
- stain or dirt patch
- decal or label area
- hole or socket
- bevel highlight
- fabric stitch
- leaf vein or serrated edge

Each feature should include placement, approximate size, orientation, material effect, geometry effect, and confidence.

## Detail Recipes

Concrete Three.js material/geometry approach per `detailInventory` kind. Cross-reference
`grimoire/intake/detail_inventory.md` for the full taxonomy and the evidence/mapping rule.

- gloss: `MeshPhysicalMaterial` with a low-`roughness` localOverride (0.05-0.2) sized to the
  hotspot region; use `clearcoat`/`clearcoatRoughness` for a lacquer layer over a rougher
  base, `anisotropy`/`anisotropyRotation` for brushed/streaked highlights.
- bevel: real geometry, not a normal map - `edgeTreatment.type = chamfer`, `bevelRadius`
  object-relative (0.02-0.08), `segments` 2-4 for a soft catch-light rim, 1 for a hard edge.
- fastener: `InstancedMesh` for the repeated part; `count` + spacing pattern (linear, radial,
  grid) + head shape (hemisphere/flat/hex) + recess (raised vs countersunk); low-roughness
  metal material on the head crown.
- linework: pick engraved groove (real recessed geometry along a path, catches shadow),
  painted line/decal (canvas-texture localOverride, color contrast only, no relief), or
  panel-line (thin dark AO/roughness localOverride along a seam, no depth) - match whichever
  the reference evidence shows; do not default to decal for something that casts a shadow.
- stain: `material.localOverrides` region with `dirtAmount`, `cavityBias` (concentrate in
  crevices), `streak` (directional, usually gravity-down), `patinaColor` for oxidation hue
  shift, or a `fadedMask` (lighter, desaturated) for sun-bleaching - the inverse of dirt.

## Character Geometry And Material Recipes

Use these when `objectClass.primaryDomain` is `character` or `hybrid`. Pair with
`grimoire/character/reconstruction.md` for proportion/landmark data.

- head: sphere or ellipsoid scaled to the measured head-unit, then displaced/tapered toward
  the reference face shape (jaw width, chin point, cheek fullness) rather than left spherical.
- limbs: capsule or tapered cylinder per segment (upper arm, forearm, thigh, shin); taper
  ratio and length come from `anatomy.proportions`; capsules keep joints visually continuous.
- hands: simplified capsule-cluster (palm block + finger capsules) at low segment count;
  do not attempt per-knuckle detail unless the reference is close-up and complexity is ultra.
- hair: hair cards (alpha-mapped planes layered in clumps) for stylized/low-complexity, or a
  tube-along-curve per lock for wavy/flowing hair with visible strand structure; prefer cards
  by default - hair is the classic single-image failure mode, so favor legible clumps over
  many thin strands that swim or alias.
- face feature placement: position eyes, brows, nose, mouth using `anatomy.faceLandmarks`
  normalized coordinates (eyeLine, eyeSpacing, noseBase, mouthLine, hairline); never eyeball
  placement freehand once landmarks exist.
- eyes: glossy sphere (low roughness, slight clearcoat) plus an iris decal/texture; a correct
  catchlight (small bright localOverride matching the key light) sells more realism than
  extra geometry.
- clothing: extrude or plane panels per garment piece, with fold normals (a normal-map or
  displacement pattern following expected gravity/pose creases) rather than a flat shell;
  reuse Track A detail machinery (seam, stitch, decal, stain) for prints, buttons, wear.
- skin: approximate subsurface scattering, not true SSS - warm base albedo, soft/lower
  roughness variation (skin is not uniformly matte), and a rim or backlight to fake light
  passing through thin tissue (ears, nose edge). Avoid pure-Lambertian flat skin.

## Verification Cues

A procedural object is usually failing when:

- silhouette reads wrong even before material
- every edge is perfectly sharp or perfectly smooth
- material has one flat color and no roughness variation
- lighting hides the form instead of explaining it
- repeated details are too evenly spaced
- close-up details add triangles but not recognizability

---

## Hard-won patterns — real-object reconstructions (2026-07: BMX bike + M9 bayonet)

**Tube-network > single sweep for framed/tubular subjects.** A bike frame, knife-handle grip, fork, handlebar are *networks of straight members*. Model each member as a component with `attachment.localStart`/`localEnd` (+`baseRadius`) — the generator emits an oriented cylinder (quaternion Y→dir). A single closed `curve-sweep` CatmullRom-smooths into a teardrop blob. (BMX frame was a teardrop until rebuilt as a tube-network.)

**Blockout must contain every silhouette-defining macro part.** A bike blockout with the frame but no wheels does not read as a bike, and coarse silhouette-IoU won't catch the omission. Put wheels/blade/major masses in at `level: macro`.

**Root/container `transform.scale` MUST be `[1,1,1]`.** Children parent to the root node and inherit its transform — a `0.02` "hide" scale shrinks the whole model to a speck. Hide the container with a transparent material (`opacity:0`), never with scale.

**Cloned components inherit `actionProfile.animationRole` — reset it.** Cloning a seeded root carries `animationRole:"root"`, and `root` ∈ ATTACHMENT_ROLES, so every part trips the structural attachment gate. Set a sensible per-part `animationRole` (e.g. `"static-part"`); keep roles like `handle` off non-appendage parts.

**Curve the small details.** Serrations/scallops/teeth as straight boxes look wrong. Use `ellipsoid` (or slightly canted primitives, alternating ±angle) for rounded scallop teeth. Each detail with its own small cant reads as a hand-ground edge.

**Grip / friction texture = geometric ridge segments.** For a knurled/wrapped/segmented grip, model raised barrel bands: a thin core cylinder + N short attachment-tube segments (radius just *proud* of the core, small groove gaps). Size them barely larger than the core — oversized tori read as a coil/spring, not a grip. Material texture alone (no geometry) reads as smooth/"thô".

**`invisibleRoot`/container material is still subject to the material-pass PBR gate.** Give the container a *complete* material (roughness map, frequency bands, textureResolution) — copy a proven one — or it fails "needs usable referencePbr / roughness map" even though it never renders.

---

## Critical Reconstruction Patterns (from Bowie Knife reconstruction failure analysis)

### Failure record: Classic Fade projection passed as structure

The Classic Fade incident exposed open card meshes, constant blade stock, and seams
below the documented overlap. Portable gates now enforce mesh boundaries, seam
overlap, blade grind/distal taper, map-stripped blockout evidence, and ordered pass
credit so each failure is visible at the pass that owns it.

**Blades need a real grind, not constant thickness.** A constant-thickness slab reads as a toy cutout even with perfect silhouette. Model a wedge cross-section tapering to a sharp cutting edge using a grind function:
- For each point on the blade surface, compute height ratio from cutting edge (0) to spine (1)
- Apply a grind curve (smoothstep or power function) to taper thickness: full stock at spine, zero at edge
- For clip-point blades, also thin the false edge near the tip
- Implementation example: Z-warp the projected face plates via a `grindWarp` function that applies `halfThk * grind(height)` per vertex

**Do NOT eyeball proportions — extract 1-to-1 from reference.** Eyeballed shapes (guard, pommel, curves) are consistently wrong. Instead:
- Trace each part's exact outline from the reference image (foreground / colour-masked top & bottom per image column)
- Use a fixed image→world mapping function: `X = (nx - 0.5) * SX`, `Y = (CY - ny) * SY` (adjust SX, SY, CY to your reference dimensions)
- Sample exact colours as RGB medians from reference regions, never guess visually
- Store traced points as coordinate arrays (world space) and use them directly in Shape constructors
- For smooth curves, use `splineThru` through traced points rather than manual control point tuning

**Colours: sample, don't guess.** Visual colour estimation is unreliable. For each material:
- Sample RGB median values from reference regions using image analysis tools
- Convert RGB (0-255) to hex: `0xRRGGBB` where each component is in hex
- Example from Bowie: guard gunmetal (71,74,79) → 0x474a4f, handle gray (140,148,158) → 0x8c949e
- Store these sampled values in comments for traceability and verification

**Parts must physically connect, not just be near each other.** Adjacent components must overlap at their shared seam:
- Check XY overlap between adjacent components (e.g., guard ↔ handle, blade ↔ guard)
- Example bug: guard ended at X=-0.20, handle started at X=-0.42 → gap → "floating" appearance
- Fix: extend one or both shapes so they overlap by at least 0.02-0.05 world units at the seam
- Verify overlap by checking that `partA.end >= partB.start` for each axis where they meet

### Failure record: Glock-18 Ghost Protocol — separable parts, still 2.5D (2026-07)

The build scored 0.986 silhouette IoU on both references, matched 6 of 8 colour zones to
within ±3.3 counts, held across five non-degenerate orbit views — and still read as "just a
projection" to the user. Every gate passed because **no gate measures cross-section**.

**The distinctZ test — run it before declaring a pass.** For each mesh, count the distinct
Z values its vertices take (rounded to ~1e-3). A planar extrusion with a bevel lands on
6–10 planes no matter how many triangles it has; a genuinely revolved or lofted part lands
on 11+. On the Glock: slide 856 tris / **10** planes, frame 4900 tris / **10**, magazine 876 /
**10**, trigger 476 / **10** — while barrel 57 tris / **15**, bore 49 / **13** were the only parts
that read as real. Triangle count is not evidence of form; plane count is.

    zs = set(); for i in range(pos.count): zs.add(round(pos.getZ(i), 3))
    # <= 10 on a part that should have a profile  =>  it is a slab, not a solid

**A high silhouette IoU actively hides this.** IoU is computed from the broadside view, which
is exactly the view a flat extrusion nails perfectly. The tell-tales are elsewhere:
- top-down / muzzle-on render is a plain rectangle with no interior modelling
- an axis-aligned cross-section render shows constant width top to bottom
- the object looks right at 0° and progressively more like cardboard as it rotates

**"Separable" and "3D" are different properties — do not conflate them.** The Glock had 29
named meshes in 4 pivot groups, every one explodable and addressable via `sculptRuntime.nodes`.
Separation was perfect. That bought nothing, because each separated part was itself a slab.
When a user says details look fake, audit cross-section first, part hierarchy second.

**Internals must be mechanism, not primitives.** Hiding the translucent shell exposed the
"internals" as an 8-triangle box for the magazine body, an 8-triangle box for the breech, and
a plain tube for the barrel with no chamber, hood, lug or feed ramp. A shell at transmission
0.3 over dark internals reveals ~20–30% of them, so placeholder boxes survive every 2D gate
while contributing nothing but a vague smudge. Either model the mechanism or drop it and say
the interior is not reconstructed — a box that reads as a box is worse than an honest absence.

**Watch for `[top, bottom]` where the helper wants `[min, max]`.** A block helper computing
`y[1] - y[0]` silently accepts a negative height and produces an inside-out mesh with flipped
normals. It renders — you see the far interior wall — so it never throws and never fails a
colour gate. Assert `y[1] > y[0]` in the helper.

**Projection core poke-through prevention.** When using photo-projected face plates over a solid core:
- A solid core behind photo-projected face plates bleeds onto the blade face in the grind-transition band
- Keep the core a thin spine rail (top ~18% of blade height) raised well above the red/black boundary
- Translate the core to sit safely inside the plates: `translate(0, 0, -HALF * 0.525)` for ±0.021 plates
- Ensure the core never reaches the red/black boundary or the grind zone so it never shows on the blade face

### Fix record: the variable-thickness loft that fixed it (2026-07)

The repair for the 2.5D failure above. Replace `ExtrudeGeometry` with a loft: sweep the traced
outline through ~11 rings whose Z is `t * halfWidth(x, y)`, where `halfWidth` is a hand-authored
field naming each anatomical feature (dust cover thinner than receiver, palm swell, raised grip
panel plateau, slide-deck break, magazine floorplate flare). Groups come back as +Z cap / -Z cap /
walls, so each broad face keeps its own reference projection. Result on the Glock: distinct-Z per
shell went 10 → 693 (frame) / 363 (slide) / 231 (magazine) with silhouette IoU held at 0.985.

Four traps, each of which cost a debugging cycle:

1. **The cap has no interior vertices.** `ShapeUtils.triangulateShape` puts vertices only on the
   outline, so a swell in the MIDDLE of a part has nothing to displace and collapses to flat
   facets. Subdivide cap triangles 4-way and re-sample the field at each new interior vertex.
   Midpoints landing on a boundary edge must keep the straight-line interpolation of their
   parents, or they leave the wall and crack the seam.
2. **Never take cap normals from the triangulation.** Ear-clipping returns slivers; three nearly
   collinear samples of a curved field give an averaged normal that swings wildly, and the grip
   renders as a fan of hard creases radiating across it. Compute the normal analytically from the
   field gradient (`normalize(-f_x, -f_y, 1) * sign(t)`) for interior vertices only — boundary
   vertices are shared with the wall and carry the rolled rim. Laplacian relaxation of the mesh is
   NOT a substitute; it made it worse.
3. **Roll must be per-vertex, off the LOCAL half-width.** A roll sized off nominal thickness turns
   a locally slim section (a trigger-guard bow at 58% of the receiver) almost entirely into roll,
   and it renders as a white tube.
4. **A sloped cap surface is scored by the BROADSIDE gate, and what it reads is the SLOPE, not the
   depth.** An over-scaled slide-deck break put that zone +23 luma over the reference. Halving the
   guard bow's ramp — a steeper slope for a *shallower* waist — took it from +19 to +54. Widen the
   shoulder to dim it. Sanity-check every new bevel against the reference before keeping it.

Measure the baseline before claiming a photometric regression: `git stash` the rewritten factory,
re-render, diff per zone. Two of eight zones here were genuine regressions; two others were
pre-existing and the rework improved them.

### Diagnosis record: the reviewer names the SYMPTOM, not the cause (2026-07)

Across three review rounds on the Glock, every reported symptom was real and the stated cause was
wrong more often than right. Acting on the stated cause would have made the model worse twice.

| Reported cause | Actual cause |
| --- | --- |
| "floating micro-meshes used for serrations — use normal maps instead" | ribs are half-sunk in the shell; the EXPLODE was giving each its own offset and scattering them |
| "trigger ghost is a projection artefact — apply an isolation mask to the albedo" | the traced guard hole wrapped AROUND the trigger, so the frame carried a solid trigger tongue. Masking the albedo would have changed nothing |
| "no normal map on the serrations" | there was one, at normalScale 0.42 |
| "set transmission 0.8 / roughness 0.1" | transmission was solved at 0.3 against the reference; roughness is a per-pixel authored map, not a scalar |

Self-inflicted too: the ejection-port zone was mis-attributed twice (blamed the rib material, then
the raceway colour — each moved it by ~0.1) before the real cause turned out to be opening the
slide's underside. **Two failed attributions in a row means stop guessing and run a discriminating
test.**

Four cheap tests that each settled a question in one shot:

- **Flat-material render** — hide everything but the part, swap in `MeshBasicMaterial` with no maps,
  render. Anything still visible is GEOMETRY, not texture. Settled the trigger ghost instantly.
- **Axis raycast** — for any "the hole is blocked" report, cast a ray down the axis from outside and
  print the ordered hit list with object names. It names the blocker. `slide@1.708` ended the
  guesswork about the bore in one call.
- **`git stash` the rewrite, re-render** — before conceding a photometric regression, measure the
  baseline. Half the "regressions" were pre-existing and the rework had improved them.
- **Bracket the parameter** — change the suspect the WRONG way. If the symptom does not worsen, it is
  not the cause. Tightening the guard-bow ramp took it +19 → +54, which proved the slope was the
  driver and pointed at the fix.

### Failure record: a loft's cavities are not all holes (2026-07)

A loft swept along Z has caps at ±Z and WALLS around the XY silhouette. That decides the mechanism:

- cavity opening along ±Z → a **hole in the outline**
- cavity opening along any other axis → **missing wall**, a different operation

The slide's muzzle bore opens along +X and the U-channel opens along -Y, so neither is a hole; both
needed wall-skipping. Modelled as outline holes they would have been cut on the wrong axis. This is
usually what a reviewer means by "it is solid / it cannot be assembled".

Negative result worth not repeating: a cavity whose inner surface is a back-faces-only mesh does NOT
work inside a translucent shell of varying thickness — the magwell attempt ghosted through the grip
as a grey slab and was reverted. Cutting the mouth is only half the job; the cavity needs a surface
that stays contained, and a simple inverted box will not.

### Failure record: the trace absorbs adjacent opaque parts (2026-07)

Alpha-tracing a photo where part B is opaque and touches part A gives an A-outline that swallows B
as a solid tongue. Model B separately as well and the build carries TWO Bs — one real, one fake and
full-thickness. Here the traced trigger-guard hole wrapped around the trigger, so the frame kept a
trigger-shaped tongue at full receiver thickness under the real shoe.

Fix at the trace, never downstream: punch B's footprint out of the mask BEFORE tracing A's holes.
`build_geo.py` now does this; the guard hole went 114 pts → 75 and every other field in `geo.json`
stayed byte-identical. Audit for it by rendering each shell alone with a flat material.

**A 2D gate can reward the defect.** Removing the tongue dropped front IoU 0.9855 → 0.9834, because
the tongue had been padding the silhouette. Do not defend a metric that was earned by wrong
geometry — report the drop as the correction it is.

### Explode contract: surface detail is not a part

Serrations, stria, inner raceways, port floors and muzzle faces belong TO a part; they are not parts.
Give them a flag (`userData.explodeWithParent`), parent them to their shell, and have the explode
skip them so they ride it. Without it a disassembly shatters into a comb of loose slivers and reads
as broken geometry — which is exactly how it was reported. The corollary is the positive one: a real
sub-assembly (trigger shoe + safety lever + bar + connector) should move as ONE module, so parent
those to the shoe and flag them too.

### Assembly contract: every model ships explodable and clickable (2026-07)

Not optional, not one-demo-only. Both are cheap once the naming is right, and together they are the
only *structural* check in a pipeline whose every other gate scores pixels: a model that can be
taken apart and clicked part-by-part cannot be one fused mesh wearing a photograph. Build them from
a single definition of "a part" — if the explode and the picker disagree about what one part is,
both are wrong.

**Naming rules the runtime depends on.** These are the whole contract; get them right and the
viewer needs no per-demo configuration:

1. **Name every mesh.** An unnamed mesh cannot be selected, cannot be reported, and explodes on its
   own. The manifest counts them and the assembly gate warns.
2. **`userData.explodeWithParent` on surface detail**, as above. It now does double duty: it makes
   a click on a serration resolve *up* to the comb instead of selecting one sliver.
3. **A named Group means one of two things**, and the runtime tells them apart by what is inside: a
   group holding *named parts* is a container/pivot (`slideAssembly`, `triggerPivot`) and is
   descended through; a group holding *anonymous* meshes is itself the part and travels whole. So a
   bracket set authored as eight unnamed meshes under `corner-brackets` explodes as one bracket set
   — what the author meant — instead of bursting into twenty slivers.
4. **Publish `sculptRuntime.destructionGroups`** when the object has assemblies; the part list
   groups by it for free.

**Separation is a layout SCALE, not a uniform push.** Displacing every part the same distance
outward slides the whole arrangement without opening the gaps between neighbours — parts that
touched still touch, and are neither readable nor clickable. Scale each part's distance from the
model centre (≈2×), add a base clearance for parts near the middle where the scaling term vanishes,
fan concentric stacks (a barrel inside a slide) along the model's THINNEST axis in alternating
growing steps, then dolly the camera by how much the layout actually grew rather than a fixed guess.

### Assembly gate: `forge/stage4_review/check_part_coverage.py`

Compares the BUILT part tree against the spec's `componentTree`, and the spec against its own
`detailInventory`. Catches a specified component that was never built, two components fused onto one
mesh, an inventoried detail that never reached the spec, and meshes belonging to no named part.
Feed it a manifest dumped from the running page; the script's docstring gives the shape.

Two rules it took a wrong pass to learn, both about not punishing correct work:

- **A missing component whose parent IS built is usually right.** A bevel, a jimping band, a choil
  is relief cut into the part it belongs to. Report it as a note; reserve failure for an important
  component whose branch is absent entirely.
- **"Has children" does not mean "is a container".** In a real spec the blade and the grip are
  genuine geometry *and* the parents of their own relief features; that rule silently drops the most
  important components from the check. Only the parentless tree root is a container.

**Its honest limit:** it compares model → spec → inventory. It cannot invent knowledge intake never
captured. The Glock-18 fire selector — never observed, never inventoried, never specified — passes
every check here. Closing *that* gap is the detail inventory's job and the family adapter's job. A
coverage gate proves you built what you said; it cannot prove you said enough.
