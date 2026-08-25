# Hair

How img2threejs reconstructs hair, what it measures, and — as precisely as possible — what it does
not know.

## Why hair needed its own subsystem

Before this, hair was one ellipsoid (`forge/stage2_spec/new_sculpt_spec.py`, the `("hair", "Hair",
"ellipsoid", ...)` entry). A grep for `hair` across `forge/` returned 44 lines, all of them tests,
comments or material ids, and none of them logic. Three pieces existed and were unused: a
`faceLandmarks.hairline` slot no generator read, a `fiber-strand` topology class with nothing behind
it, and the `tapered-sweep` primitive with zero specs using it.

The gap that mattered was not geometric. In one session, four separate diagnoses of a single
hairstyle were wrong, and every one was caught only by measurement after the fact. So the gates were
built before the generators.

## The measurement that set the architecture

The reference this pipeline calibrates against is
`public/references/low-poly-humanoid-glb/human_pbr.glb`. Probing it:

| property | value |
| --- | --- |
| vertices | 570,400 |
| triangles | 1,000,000 |
| meshes / materials | 1 / 1, merged |
| textures | diffuse, normal, metallic-roughness (PNG) |
| skins / animations | 0 / 0 |

`forge/stage1_intake/probe_glb.py` reports `"reliableSemanticBoundary": "multipart-glb-required"` —
the hair cannot be separated from the body by metadata.

Surface roughness, measured as the radial step between adjacent azimuth bins at each height:

| height band | radial step | vs control |
| --- | --- | --- |
| crown / hair | 0.00338 | **+8%** |
| upper hair | 0.00405 | +30% |
| hair / brow | 0.00429 | +38% |
| face / cheek | 0.00385 | +23% |
| **torso (no hair, control)** | **0.00312** | — |

The hair surface is as smooth as a torso. At a head radius of ~0.10 that is roughly 3.4 mm of
undulation, where a real lock is 10–30 mm. **The reference contains no lock geometry.** Its entire
strand appearance is in the diffuse and normal textures.

Two consequences follow, and they run in opposite directions to the obvious plan:

1. Since img2threejs emits code and no textures, the strand impression must come from **faceting and
   material response**, not from adding lock geometry.
2. The reference **cannot calibrate lock parameters**, because it has none to measure.

## Representation tiers

| tier | what it is | calibration |
| --- | --- | --- |
| `shell` (default) | a faceted mass hugging the scalp | measurable against the reference |
| `masses` | a shell plus a few named regional masses | envelope measurable |
| `locks` | individual tapered locks | **no ground truth available** |

`locks` is not forbidden. It emits a warning naming exactly why its numbers are derived rather than
observed. Unblocking it needs a multipart GLB with a separated, named hair mesh.

## Primitives

| primitive | verdict |
| --- | --- |
| `tapered-sweep` | preferred — curved spine, parallel transport, reaches a true point |
| `curve-sweep`, `lathe`, `ellipsoid` | allowed |
| `instanced-cluster` | allowed, for distant strand impressions only |
| `plane-card` | **rejected** — needs an alpha texture, which this pipeline cannot emit |
| `tube` | **rejected** — constant radius, reads as a noodle |
| `box` | **rejected** |

Enforced in `forge/stage2_spec/hair_profile.py` and again per-component in
`validate_sculpt_spec.py`.

## The stages

### Stage 1 — `extract_hair_evidence.py`

Measures from reference images: hair/skin split (Otsu, not a percentile — see below), banded dark
coverage across crown/mid/jaw, the hairline (writing the `faceLandmarks.hairline` slot that has
existed unfilled since v1.2), the specular band position, and the root-to-tip luminance delta.

It reports `notObserved` explicitly. A frontal-only set states that the rear of the head was never
seen, so nothing downstream authors a nape as if it were measured.

> **A trap worth recording.** The first implementation thresholded at a fixed percentile. That makes
> the reported hair fraction true by construction: it read 0.380, 0.384 and 0.382 across three
> different views of the same subject, which looks like agreement and is arithmetic. Otsu's
> between-class variance split fixed it, and the same three views now read 0.387 / 0.592 / 0.747 —
> front lowest because the face occupies it, rear highest.

### Stage 2 — `hair_profile.py`

**This stage defines and validates a schema. It does not compile one.** There is no
profile-to-`componentTree` compiler yet; a spec still authors its hair components directly, and the
profile's rules are enforced by the validator plus the `standProud` march the generator emits.
Writing that compiler is the next piece of work, and it is called out here because a schema reads
exactly like a working feature and this one is not one yet.

The schema has four blocks: `scalpComponentId` (the head component whose ring stack the scalp is
derived from, never authored twice), `hairline` (control points in scalp `(u, v)`), `flowField`
(part line, whorls, gravity, sweep — about six numbers, which is what makes a wide range of styles
reachable without authoring a direction per mass), and `masses[]`.

**Roots are `(u, v)` on the scalp, never absolute positions.** An absolute root is a hard validation
error. This is the same binding Blender uses for hair, where a curve root is a
`surface_uv_coordinate` on the emitter mesh, and it makes the recorded failure structurally
impossible rather than a matter of authoring care.

### Stage 3 — `standProud` and the clearance march

A component declares `standProud: {againstComponentId, clearance, maxPush}`. The generator emits
`applyStandProud`, which pushes every vertex outward along its **own radial spoke** until a signed
distance field reads at least `clearance`, capped at `maxPush`.

This is `hug` from the hand-written showcase demo, generalised into the skill. In that demo the
garment held its clearance as a measurement and worked; the hair held the same requirement as a
comment and broke. `maxPush` is required, not optional: an uncapped march walks inner vertices
through the target and out the far side.

Hair material comes from `hair.human.code-only` in the material reference — no maps, higher sheen
and anisotropy than the textured profile, because with no `anisotropyMap` and no `normalMap` those
are the only carriers left. A `rootTipGradient` emits vertex colours along the mass's own axis.

### Stage 4 — the gates

| gate | kind | catches |
| --- | --- | --- |
| `scalp_exposure.py` | **hard** | skull showing through, geometrically, before any render |
| `hair_gate.py` | soft | banded coverage delta, hairline offset, highlight-band offset |

The hard/soft split is the point. A bald patch is always wrong; a coverage shortfall is often the
right compromise. Conflating them is what produced the recorded regression: a shortfall was answered
by widening the masses, which pushed them off the skull.

`scalp_exposure` only counts hair that is **outside** the skull. A nearest-neighbour test would have
passed the failing build, because the vertices were still nearby — they had sunk below the surface.

### Stage 5 — rigging

Hair is **rigidly parented**, never smooth-skinned. `geodesic_skinning.RIGID_ROLES` excludes
`hair`, `detail`, `decal` and `panel`.

Measured on the test fixture: the geodesic distance from the neck joint to a crown vertex is 33.6
voxels against the head bone's 15.0, which at falloff power 3 leaves the **neck holding 8.1% of the
crown**. Turning the neck would shear the hair against the skull it sits on — and because
`standProud` and `scalpExposure` are bind-pose checks, no gate would ever see it.

Long hair that crosses a joint uses a short chain of bones with one mesh segment rigidly parented
per bone. Still no blended vertex weights.

## What this does not know

- **Lock parameters are derived, not measured.** Taper ratios, cross-section aspect, lock counts,
  whorl strengths. Listed in `hair_profile.UNCALIBRATED_FIELDS`, and any mass setting one without
  `uncalibrated: true` gets a warning. Precedent for why: a recovered build had eleven hair locks
  sharing a tip radius of 0.0327 to four decimal places, and nothing objected.
- **Every gate threshold in this subsystem is uncalibrated** and reports itself as such.
- **Dynamics is out of scope.** A single image contains no motion, so any hair simulation would be
  invention rather than reconstruction.
- **Strand-level hair is out of scope permanently**, by architecture. No textures, no alpha.
- **There is no `hairProfile` compiler.** The schema is validated; nothing turns it into components.
- **The `standProud` march can fail silently-ish.** A vertex that exhausts `maxPush` while still
  inside the target is counted and warned about at runtime (`geometry.userData.standProud`), but
  nothing fails the build over it. Measured on the shipped fixture: 2 of 8 sampled hair vertices sat
  0.059 inside the skull against a 0.04 cap and could never have reached clear.
- **`scalp_exposure` measures spec-derived points, while the march displaces vertices at runtime.**
  So the gate does not measure the geometry that ships. Closing that means moving the march into
  Python at build time — the single largest improvement still available here.

## The recorded failure, in full

It is referenced throughout the code, so here it is once, completely.

The hair side masses of the low-poly humanoid demo were widened by hand to close a measured coverage
deficit (profile mid band: reference 41.5%, ours 7.8%).

| view | before | after | delta |
| --- | --- | --- | --- |
| profile | 46.8% | 43.2% | **−3.6** |
| left-profile | 61.4% | 59.0% | −2.5 |
| orbit−35 | 30.7% | 29.6% | −1.2 |
| front | 38.7% | 38.3% | −0.5 |
| orbit+35 | 25.7% | 25.3% | −0.4 |
| rear | 49.9% | 49.8% | −0.0 |
| **mean** | **42.2%** | **40.9%** | **−1.3** |

Worse on all six. Dark coverage went *down*. Crown scalp exposure, measured on the archived captures:

| view | before | after | delta |
| --- | --- | --- | --- |
| orbit+35 | 54.0% | 68.9% | **+14.9** |
| profile | 48.8% | 55.1% | +6.3 |
| front | 53.3% | 57.6% | +4.3 |
| orbit−35 | 43.3% | 44.3% | +1.0 |

The cause: `sectionedLoft` has a straight root-to-tip spine while the skull is convex, so thickening
a section moved the surface sideways rather than outward. The mass slid off the skull, the skull
became proud of the hair, and the render grew a bare strip. The invariant it broke was already
written in the file it broke in:

> EVERY piece must stand proud of the skull at its own height. Where the skull is proud of the hair,
> the head renders bald there.

Every gate in this subsystem exists to make that sentence arithmetic.
