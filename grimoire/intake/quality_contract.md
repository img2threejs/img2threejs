# Pre-Spec Assessment And Quality Contract

Use this reference before authoring an `ObjectSculptSpec`. The purpose is to prevent shallow specs that are technically valid but too vague to recreate the reference object.

Do not use fixed domain profiles. Assess the object from observed traits, complexity, and target fidelity.

## Soft Object Classification

Describe the object using multiple axes:

- form language: organic, hard-surface, mechanical, architectural, botanical-like, character-like, amorphous, sculptural, fabric-like, transparent-like
- structure kind: single body, compound object, branching hierarchy, repeated modules, layered shell, articulated assembly, deformable surface
- motion potential: static prop, whole-object transform, articulated, bendable, detachable, destructible, effect-emitter
- material families: wood, bark, leaf, metal, stone, ceramic, plastic, rubber, cloth, glass-like, liquid-like, skin-like, mixed

These are descriptors, not domain templates. Use only what the image supports.

## Complexity Scoring

Score each axis from 0 to 3:

- silhouette complexity: simple outline to heavily interrupted/organic silhouette
- component count: one piece to many visible subparts
- hierarchy depth: flat object to deep parent-child structure
- repetition density: none to thousands of repeated marks/leaves/scales/rivets
- material layer count: one material to many layered local material responses
- local detail density: plain surface to dense scratches, bumps, moss, seams, chips, pores, or grain
- occlusion risk: fully visible to many hidden/inferred parts
- action readiness need: static to many pivots/sockets/colliders/destruction seams

Map total judgment to:

- `simple`: few parts, low detail, one or two materials
- `moderate`: several parts, visible local detail, shallow hierarchy
- `complex`: many parts, repeated systems, multiple materials, several hierarchy levels
- `ultra-complex`: dense organic/mechanical/architectural structure where fidelity depends on deep hierarchy and repeated microstructure

## Quality Contract

Before generating code, define exactly what makes the model good enough:

- definition of done for this object
- minimum macro, meso, and micro feature counts
- required repeated systems and their distribution rules
- required material layers and local overrides
- screenshot viewpoints required for visual comparison
- failure modes that should block `continue`

Good feature groups are specific to the image:

- weak: `make leaves look good`
- strong: `leaf clusters must form irregular overlapping canopy masses, with varied card size/orientation/color and gaps exposing secondary branches`

- weak: `add bark texture`
- strong: `trunk and primary branches need vertical ridges, cavity-darkened cracks, moss/lichen patches near roots and inner forks, roughness variation, and nonuniform displacement/bump`

## Strict Quality Gate

Run `../../forge/stage2_spec/validate_sculpt_spec.py spec.json --strict-quality` before code generation. The script path is relative to the skill folder.

If strict validation fails:

- refine `preSpecAssessment` if complexity was underestimated
- refine `qualityContract` if definition of done is too generic
- add missing components, material layers, repetition systems, evidence refs, or local features
- only lower the quality bar if the user explicitly accepts a simpler approximation

The gate should block code generation when the spec could describe many different objects instead of the provided reference.
