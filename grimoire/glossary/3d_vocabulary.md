# 3D Graphics Terminology For Object Sculpt Specs

Use this reference when writing or reviewing an `ObjectSculptSpec`. The goal is to describe what the agent should build in terms a Three.js/technical-artist workflow can act on.

## Geometry And Topology

- `silhouette`: the outer read of the object from a given camera angle.
- `primitive family`: box, sphere, ellipsoid, cylinder, cone, capsule, torus, lathe, extrusion, tube, plane card, instanced cluster.
- `topology intent`: hard-surface blockout, subdivision-ready surface, low-poly prop, organic deformed mesh, alpha-card cluster.
- `bevel radius`: size of rounded edge transition in object-relative units.
- `bevel segments`: number of edge-rounding subdivisions.
- `chamfer`: flat angled edge cut, usually cheaper than a rounded bevel.
- `taper`: gradual scale change along an axis.
- `bend`: curvature deformation along an axis or spline.
- `twist`: rotational deformation along an axis.
- `boolean cut`: subtractive shape such as hole, notch, slot, recess, or carved opening.
- `edge loop`: repeated ring/path of vertices used to support shape or deformation.
- `local deformation`: localized dent, swelling, pinch, warp, sag, or buckle.
- `displacement`: geometry-level height movement that can affect silhouette.
- `normal strategy`: vertex normals, weighted normals, flat shading, generated tangent-space normal.
- `UV strategy`: generated procedural coordinates, cylindrical projection, triplanar-like mapping, atlas-ready unwrapped regions.

## Material And PBR

- `albedo` / `baseColor`: diffuse color independent of lighting.
- `roughness`: microfacet scatter; high roughness is matte, low roughness is glossy.
- `metalness`: whether material behaves as conductive metal in PBR.
- `normal map`: tangent-space normal detail that changes lighting without changing silhouette.
- `bump map`: height-derived normal detail, usually procedural and cheaper than displacement.
- `displacement map`: geometry displacement; use only when relief should affect silhouette or close-up shape.
- `ambient occlusion`: darkening in creases, contact zones, and cavities.
- `cavity dirt`: localized dark/dusty buildup in recessed areas.
- `edge wear`: exposed lighter/polished/damaged material on protruding edges.
- `clearcoat`: secondary glossy layer over base material.
- `transmission`: light passing through transparent/translucent material.
- `alpha`: opacity or cutout transparency.
- `anisotropy`: directional highlight stretch, useful for brushed metal or fibers.
- `procedural noise scale`: spatial frequency of generated variation.
- `local mask`: region-specific control for color, roughness, dirt, wear, or bump.

## Surface Local Features

- `raised ridge`: geometry or normal detail protruding from surface.
- `recessed groove`: carved or shadowed line cut into the surface.
- `seam line`: boundary between joined pieces or material panels.
- `scratch cluster`: group of thin directional marks affecting albedo/roughness/normal.
- `chip`: broken missing piece, often with exposed underlayer.
- `dent`: inward local deformation.
- `stain`: local albedo/roughness color change without strong geometry change.
- `contact wear`: abrasion where object touches ground, hands, joints, or other parts.
- `decal region`: image/text/logo-like area; approximate with colored planes or generated texture unless exact fidelity is required.

## Lighting And Rendering

- `key light`: dominant light source.
- `fill light`: softer light used to lift shadows.
- `rim light`: back/side light that outlines silhouette.
- `environment reflection`: skybox/HDRI-like reflection source.
- `contact shadow`: near-surface shadow grounding the object.
- `shadow softness`: blur/spread of shadow edge.
- `color temperature`: warm/cool light tint.
- `exposure`: scene brightness scale.
- `tone mapping`: output transform affecting contrast and highlights.

## Animation, Physics, And Destruction

- `pivot`: local rotation origin.
- `hinge`: constrained rotation joint.
- `socket`: attachment point for a child part.
- `collider`: simplified physics shape.
- `rigid body`: simulated physical body.
- `fracture seam`: planned break line.
- `detachable fragment`: piece that can separate during destruction.
- `impulse direction`: force vector used to trigger movement or breakage.

## Writing Rule

Bad: `the surface is ugly and too smooth`.

Better: `increase microRoughness to 0.45, add tangent-space fine-noise normal with strength 0.2 and scale 32, add low-frequency albedo mottling at amplitude 0.12, and add cavity dirt local masks in recessed grooves`.

Bad: `make the edges realistic`.

Better: `add 0.025 relative bevel radius with 3 segments on exposed hard-surface edges, add edge-wear local overrides on bevel crests, and keep internal seams sharper with 1-segment chamfers`.
