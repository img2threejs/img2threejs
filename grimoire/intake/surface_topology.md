# Surface Topology Classification

Use this reference before assigning a `primitive` to any component. Classifying the *kind* of
surface first prevents the most common geometry-mismatch failure: picking a primitive that is
structurally wrong for what the silhouette actually is (see Plan 1.3 Workstream A/F — a continuous
organic bulge modeled as a box-stack, or a blade modeled as a box because `extrude` was never
attempted).

## The six classes

- **`continuous-sculpt`** — a single, smoothly-varying volume with no internal seams or panel
  breaks: a horn, a smooth organic bulge, a worn stone, a revolved vessel. Never `box`/`cylinder`/
  `cone` — use `lathe` (rotationally symmetric), `extrude` (profile with depth), or `curve-sweep`
  (follows a 3D path).
- **`assembled-solid`** — a discrete rigid part with flat or simply-curved faces, genuinely built
  from a primitive: a crate, a cylindrical canister, a boxy chassis panel. `box`/`cylinder`/`cone`/
  `capsule`/`torus` are all fine here.
- **`conforming-shell`** — a thin surface that follows the form of something underneath it rather
  than having independent volume: a fuselage skin panel, a thin curved cowling, a fitted cloth
  layer over a body. Usually needs a `plane-card` bent/shaped to the underlying form, or a shallow
  `extrude`.
- **`surface-relief`** — detail that changes the surface but not the overall silhouette-defining
  volume: ridges, panel lines, rivets, embossed logos. Represent via geometry only when the relief
  is large enough to affect the silhouette at the intended viewing distance (see
  `grimoire/feedback/shading_realism.md`'s "geometric relief" rule) — otherwise this is a material
  concern (normal/bump), not a `topologyClass` concern, and the component doesn't need its own
  entry.
- **`fiber-strand`** — a thin, elongated, often-repeated strand-like form: cable, rope, hair clump,
  root, vine, wire. Never `box`/`plane-card` — use `tube` (follows a path) or `instanced-cluster`
  (many repeated strands).
- **`material-only`** — no independent geometric footprint of its own; purely a material/decal
  layer riding on a parent's surface (a printed logo panel, a flat sticker). Any primitive is
  acceptable here since the "geometry" is just a thin carrier for the material.

## Decision tree

1. Does this component have its own volume, or does it just carry a material/decal on a parent's
   surface? → if the latter, `material-only`, stop here.
2. Is it long, thin, and either follows a path or repeats many times? → `fiber-strand`.
3. Is the *silhouette itself* affected by fine detail (ridges/rivets/panel lines) with no other
   independent volume? → `surface-relief`.
4. Does it have hard, distinct faces you could point to and count ("this cube, that cylinder")? →
   `assembled-solid`.
5. Is it a thin skin that follows another form's curvature, with no volume of its own? →
   `conforming-shell`.
6. Otherwise — one continuous, smoothly-varying mass — `continuous-sculpt`.

## Worked examples

| Object | Component | Class | Why |
| --- | --- | --- | --- |
| Gerber knife | blade | `continuous-sculpt` | Smooth tapering wedge to a point — `extrude` with a `lineTo()`-only tip profile, not a box. |
| Gerber knife | paracord wrap | `fiber-strand` | Repeated wound cord — `instanced-cluster` or a `tube` following a helical path. |
| Doraemon house | wall panel | `assembled-solid` | Flat rigid panel, genuinely box-shaped. |
| Sony earbuds | case shell | `conforming-shell` | Thin curved shell conforming to the earbud cavities beneath it — not solid all the way through. |
| Character | shirt chest logo | `material-only` | Flat decal riding on the torso's surface; carries no geometry of its own. |
| Warhauler | antenna wire | `fiber-strand` | Thin cable — `tube`, never `box`. |

## Common mistake

Classifying by "how big is it" instead of "what kind of surface is it." A large smooth stone and
a small smooth pebble are both `continuous-sculpt`; a large flat wall panel and a small flat plate
are both `assembled-solid`. Size doesn't change the class — surface behavior does.
