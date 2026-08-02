# No-cheat geometry contract

The deliverable must be a real procedural 3D model. A texture may reproduce surface appearance, but
it cannot hide missing form or make a component exist only from the reference camera.

## Hard failures

Reject the pass if any of these are used as the primary reconstruction:

- depth-map extrusion of the reference image;
- a camera-facing image plane or `projectedShell` surrounding the real body;
- a second view-only mesh that disappears or changes when the camera orbits;
- a floating decal/sticker used to cover a misregistered component;
- an albedo/normal/height map used instead of geometry for form-critical thickness, holes, bevels,
  fasteners, joints, springs, rails, or negative spaces;
- a parent/group bounding-box contact check presented as proof that two meshes visually connect.

## Allowed surface matching

Projection and decals are valid only when all conditions hold:

1. The receiving component is an authored 3D mesh with real thickness and topology.
2. The projection is bound to that component's UVs or a conforming surface decal.
3. The texture has no geometry authority: removing it must leave a recognizable 3D substrate.
4. The projected region remains attached in front/back and orbit renders; it does not slide,
   float, z-fight, or cross the silhouette.
5. The material route records source image, camera solve/de-lighting status, and confidence.

## Attachment proof

For each non-root component, record parent, parent socket, local contact points, contact type,
overlap/embed strategy, tolerance, and visual evidence. Validate actual child meshes after world
transforms. Then inspect at least one oblique view where the contact seam is visible. A zero AABB gap
is useful deterministic evidence, but it is not sufficient by itself.

## Review evidence

The minimum evidence for a non-planar subject is:

- reference-matched view(s) for silhouette and finish;
- at least two meaningful orbit views;
- component crops for every critical feature;
- a mesh/contact audit;
- a written list of inferred or unknown hidden regions.

If a view exposes a shell, floating part, projection slide, or impossible intersection, the pass is
`refine-spec` or `refine-code`, never `continue`.
