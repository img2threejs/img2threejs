# Adaptive subject adapters

This is the generic route for a reference subject that does not have an existing domain or family
adapter. It applies to weapons, props, machines, vehicles, furniture, plants, creatures, and any
other image-defined subject.

For CS2, keep `cs2-intake.json` as the identity/surface-evidence manifest: it records reference
admission, classification, metadata provenance, route, exactness tier, and hidden-region confidence.
It is not the geometry source of truth. A registered `componentAdapter` in that manifest is a hint
or domain template; the LLM still has to produce the measured `subjectAdapter` for the actual item.
If no template exists, continue with `mode: llm-synthesized` instead of routing through a different
family. Non-CS2 subjects do not need `cs2-intake.json`; use the generic assessment plus this
adapter contract.

## Contract

Before code generation, write a `subjectAdapter` (or `adapterContract`) in the sculpt spec. It
must contain:

```json
{
  "id": "generated:<subject-slug>:v1",
  "mode": "llm-synthesized",
  "domain": "object|character|hybrid",
  "subjectClass": "observed class or conservative label",
  "evidenceRefs": ["view-front", "crop-scope"],
  "researchRefs": ["source-manual-1"],
  "components": [],
  "attachmentRules": [],
  "criticalFeatures": [],
  "reviewViewpoints": [],
  "confidence": {}
}
```

The component tree remains the source of geometry truth. The adapter is a contract for how the
tree was derived, not a permission to use a generic placeholder tree.

## Synthesis sequence

1. Classify the subject only as far as the image supports. A broad label such as
   `mechanical-prop` is safer than an invented product name.
2. Decompose macro, meso, and micro components. Separate parts when a seam, fastener, material
   boundary, joint, negative space, or independent motion makes them physically meaningful.
3. Build a parent-child/contact graph. Every child has a parent, parent socket, local contact
   points, contact type, overlap/embed strategy, and tolerance.
4. Assign a topology class and procedural recipe to every component: primitive, traced extrusion,
   loft, surface-of-revolution, curve sweep, helix, boolean opening, instanced detail, or a
   deliberately planar surface.
5. Define material regions independently from geometry. A patterned image region is a finish
   route, not a replacement for the substrate mesh.
6. Define pivots, sockets, colliders, and `userData.tick` candidates for anything that can move,
   detach, fold, open, cycle, or be interacted with.
7. Define critical features and review viewpoints from the subject, not from a hardcoded domain
   template. Include at least two meaningful views for every non-planar identity-defining part.

## Evidence discipline

Every adapter decision is one of:

- `observed`: directly visible in an image region;
- `researched`: supported by a cited technical or domain source;
- `inferred`: a conservative 3D completion selected because the first two are incomplete;
- `unknown`: unresolved and represented with a confidence value.

An unfamiliar subject does not cause an `unsupported-family` stop. It causes a new adapter and a
lower confidence budget for hidden or ambiguous regions. If the image is unreadable or the subject
cannot be separated from its background, use the normal suitability/request-input gate.

## Adapter anti-patterns

- Do not copy a knife, pistol, rifle, character, or other adapter because the silhouette is vaguely
  similar.
- Do not put all visible features into one root component and call the adapter complete.
- Do not encode a research guess as direct image evidence.
- Do not let a registered adapter claim exact geometry when it only provides topology hints.
- Do not create an image plane or depth-map shell as a substitute for the component graph.

The adapter is complete only when the spec can explain the geometry, material route, runtime hooks,
and review gate for every identity-defining region.
