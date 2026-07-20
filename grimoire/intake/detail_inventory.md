# Detail Inventory

Use this reference during analysis, before the spec is authored. It exists because small
identity-defining marks (a bevel highlight, a row of rivets, a stain) get skipped when the
agent only eyeballs the whole image once. Scan zone by zone and record every mark as a
structured `detail`, not as prose.

## The Rule

Every `detail` entry records: where (`region`, normalized), what changes (`kind` + `affects`),
how strong (`scale`, intensity implied by the recipe below), evidence region (`evidenceRef`),
and confidence. It MUST set `mapsTo` a real `component.localFeatures[]` entry or
`material.localOverrides[]` entry. A detail described only in prose is a gate failure - if it
does not map to a field the generator reads, it will not reach the render.

## Taxonomy - kind to graphics terms

### gloss (do bong)
Localized low-roughness zone or specular hotspot, not a global material change.
- `material.localOverrides`: `roughness` low value (0.05-0.2) over the region, or
  `clearcoat` + `clearcoatRoughness` on `MeshPhysicalMaterial` for a lacquer/wet look.
- Streaked highlights (brushed metal, hair) -> `anisotropy` + `anisotropyRotation`.
- Record hotspot position relative to the key light direction; a gloss detail with no
  matching light direction will not render visibly.

### bevel (bo goc)
Edge treatment, not a texture trick - light catches a real chamfer.
- `component.localFeatures` geometry effect: `edgeTreatment.type = chamfer`,
  `bevelRadius` (object-relative, e.g. 0.02-0.08), `segments` (2-4 for a soft rim, 1 for hard).
- Note whether it reads as a bright rim highlight under grazing light; if the reference
  shows a crisp bright line along an edge, the bevel must be real geometry, not a normal map.

### fastener (screw / rivet / bolt)
Repeated small parts - always an instanced system, never one-off meshes.
- `InstancedMesh`, `count`, spacing/distribution (linear, radial, grid), head shape
  (hemisphere, flat, hex), recess (raised vs countersunk), material (usually metal, low
  roughness at the head crown).
- Confidence should reflect whether every instance is visible or only a legible subset
  (partial rows behind occlusion still count if spacing is inferable).

### linework (engraving / painted line / panel-line)
Three distinct techniques - pick the one the evidence supports, they read differently:
- Engraved groove: geometry effect, a recessed `groove` (see below) following a path;
  catches shadow, no geometry it will look flat under any light.
- Painted line / decal: `material.localOverrides` with a canvas-texture decal region;
  color contrast only, no relief.
- Panel-line: dark AO seam - a thin `localOverride` darkening roughness/AO along a seam
  without true depth; use when the reference shows a soft dark line, not a hard groove.
- State a legibility target: line must remain readable at the review's grazing-light shot.

### contour (edge outline / toon rim)
Stylized outline, usually a rim-light or a backface-outline technique.
- `material.localOverrides` or a dedicated outline pass (inverted-hull or shader rim).
- Record which silhouette edges carry it; partial outlines (only the top edge) are common.

### seam
Construction line where two surfaces meet (molded parts, fabric panels, armor plates).
- Geometry effect: a thin recessed `groove` or a raised `ridge` (whichever the reference
  shows) plus a slightly darker AO localOverride in the crevice.

### stitch (fabric stitch)
- `component.localFeatures`: small repeated bumps or a dashed groove along a seam path;
  usually paired with a `linework: painted line` for the thread color contrast.
- Instance or repeat along a curve like a fastener row, but finer spacing.

### stain (dirt / patina / discolour / faded)
Always a `material.localOverrides` region, described with these sub-fields:
- `dirtAmount`: 0-1, how much darker/desaturated.
- `cavityBias`: whether it concentrates in crevices/cavities (usually yes for dirt/grime).
- `streak`: vertical/directional streaking flag + direction (gravity-fed dirt runs down).
- `patinaColor`: hex or named hue shift for oxidation/verdigris/rust bloom.
- `fadedMask`: a lighter, desaturated region for sun-bleaching - opposite of dirt, still a
  localOverride.
- `region`: where on the object, tied to `evidenceRef`.

### scratch
Thin localized roughness/normal perturbation, optionally exposing an underlayer color.
- `material.localOverrides`: scratch cluster with orientation (usually radial or directional
  from handling), width, and whether it exposes a different base color underneath.

### chip
Small area of missing surface material, usually at an edge or corner.
- Geometry effect if it changes silhouette (a notch); otherwise a localOverride exposing
  an underlayer color/roughness at a corner/edge component.

### decal
Printed/applied graphic or label, flat against the surface.
- `material.localOverrides` with a canvas-texture region; record placement, approximate
  size, and rotation. Decals do not add geometry unless they have physical thickness
  (a sticker edge) - if so, add a thin raised `component.localFeatures` plate.

### emissive
Self-lit region (LED, glow, screen, ember).
- `material.localOverrides`: `emissive` color + `emissiveIntensity`, and whether it should
  bloom under the renderer's tone mapping. Record whether it is constant or should read as
  a light source affecting nearby surfaces (may need a matching point/area light).

### hole
Actual opening or socket, changes silhouette/topology.
- `component.localFeatures` geometry effect: a real cut or socket, not a dark texture patch.
  Record depth and whether the interior needs its own material (visible cavity).

### groove
Recessed linear or curved channel.
- Geometry effect: negative relief along a path, width/depth object-relative, plus AO
  darkening in the channel. Shares mechanics with engraved linework and seams.

### ridge
Raised linear or curved feature, the geometric inverse of a groove.
- Geometry effect: positive relief along a path, width/height object-relative, catches
  highlight along its top edge (pair with a gloss or bevel note if the reference shows a
  highlight line on the ridge crest).

## Scan Method

Pick one and record it as `scanMethod`:
- `component-zones`: walk each planned component's bounding region; best when component
  boundaries are already known.
- `grid-3x3` / `grid-4x4`: divide the image into a uniform grid and inspect every cell;
  use when components are not yet decided or the object has no obvious part boundaries.

Set `targetMinDetails` from complexity tier: simple 3, moderate 6, complex 10, ultra 16
(starting values, tune after runs). Scanning zone by zone against a minimum count is what
prevents a single-glance miss of small marks.

## Confidence

Score 0-1 per detail. Lower confidence for: partially occluded regions, marks inferred by
symmetry rather than seen, or ambiguous kind classification (e.g. scratch vs. panel-line).
Do not inflate confidence to pad `targetMinDetails` - an unlinked or low-confidence detail
that fails the `mapsTo` check still blocks the gate.
