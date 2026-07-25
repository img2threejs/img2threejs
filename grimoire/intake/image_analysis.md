# Image Analysis Protocol

Use this reference **first**, before `probe_image.py` and before the pre-spec assessment. It
exists because the agent tends to glance at the whole image once and jump straight to code,
skipping the disciplined observation that every later gate depends on. This is a **generic,
object-agnostic** protocol — it applies to any subject (prop, tool, weapon, vehicle part,
botanical, mechanical, character). Domain tracks (CS2 skins, characters) are specializations
layered *after* this pass, not replacements for it.

## The Rule

Describe what is **there**, in a fixed bottom-up order, using controlled 3D vocabulary — not
what it *means* or how it *feels*. Three disciplines carry the whole protocol:

1. **Observation before inference.** State the observable fact ("a low-roughness band along the
   spine") separately from what you infer from it ("probably a polished bevel"). Mark every
   inference as inference.
2. **Controlled vocabulary over adjectives.** Use the terms below and in
   `grimoire/glossary/3d_vocabulary.md`. Never "nice / sleek / aggressive / high-quality".
3. **3D object-space, not 2D image-space.** Describe parts by front/back/lateral/proximal, not
   left/right-of-the-photo. A single photo is a projection — say what perspective hides.

Run the layers in order; each feeds a real assessment field (mapping at the end). The output of
this protocol IS the raw material for `new_pre_spec_assessment.py` and `build_detail_inventory.py`.

## Layer 1 — Identification & classification

- **Observe:** what the object *is*, its category, and your confidence. Complete a physical
  inventory before any claim about value/purpose.
- **Vocabulary:** work type (a specific noun — *statuette, karambit, socket wrench, rhyton*),
  broad classification (*bladed tool, furnishing, mechanical part*), `primaryDomain`
  (`object` | `character` | `hybrid`), confidence 0–1.
- **Avoid:** using the object's *title/name* as the description; asserting meaning before the
  inventory; indexing beyond the visible evidence.

## Layer 2 — Overall form & silhouette

- **Observe:** the bounding volume and footprint as a small set of primitives; symmetry.
- **Vocabulary:** primitives (*cuboid, cylinder, sphere, cone, extruded profile, lofted curve*);
  symmetry (*bilateral, radial, asymmetric*); shape language (*geometric* vs *organic*);
  aspect/proportion relative to a named reference dimension.
- **Avoid:** emotive shape words; "large/small" with no reference; forcing an organic form into
  one primitive when it is a blend.

## Layer 3 — Macro → meso → micro decomposition

- **Observe:** the whole broken into major assemblies, then sub-parts, then surface-level
  feature groups — a `parent-child` hierarchy for component-based modelling.
- **Vocabulary:** macro (independent major parts — *blade, grip, guard*), meso (sub-assemblies —
  *rivet row, finger choil, pommel*), micro (feature groups — *fastener cluster, engraving band*).
- **Avoid:** treating the object as one monolithic mesh; over-nesting a simple structure; skipping
  a level (jumping macro → micro with no meso).

## Layer 4 — Spatial relationships (scene-graph)

- **Observe:** how parts connect and sit relative to each other, in 3D.
- **Vocabulary:** visual triplets `<subject, predicate, object>` (`<guard, separates, blade+grip>`);
  spatial predicates *attached-to, above, below, inside, behind, flush-with, embedded-in*; each
  connection notes a contact type (*butt, overlap, socket, embed*).
- **Avoid:** 2D image-space placement (left/right of frame); describing adjacency without stating
  how the parts actually join (mid-air parts break the attachment gate later).

## Layer 5 — Materials & surface (PBR)

- **Observe:** the substance of each part and how it responds to light. One material claim per
  distinct surface, tied to a component.
- **Vocabulary:** *albedo/base color* (surface color with lighting removed), *metalness*
  (0 dielectric / 1 raw metal), *roughness* (0 polished → 1 matte), *specular F0* (~4% for
  dielectrics), *normal/relief* (*pitting, grain, pores, brushing*), *translucency*
  (*opaque / semi-translucent / transparent*).
- **Avoid:** reading baked-in highlights/shadows as albedo; calling shiny plastic "metal";
  aliasing one channel into another (see `grimoire/feedback/shading_realism.md`).

## Layer 6 — Color & finish

- **Observe:** hue, value, saturation per region; the surface finish.
- **Vocabulary:** *hue / value / saturation*; finish *matte, satin, gloss, metallic, anodized*;
  gradients as ordered stops with positions, not "fades to".
- **Avoid:** subjective/brand color names ("royal blue") instead of standard descriptors
  ("vivid blue, mid value"); one flat color where a gradient or multi-tone finish exists.

## Layer 7 — Identity-defining features

- **Observe:** the marks that make *this* item recognizable, not a generic member of its class.
- **Vocabulary:** inscriptions/marks (signatures, dates, logos, serials), wear patterns
  (*scratch, dent, oxidation/patina, stain, edge-wear*), recurring motifs.
- **Avoid:** overlooking small but critical identifiers (a maker's mark, a unique gouge that
  changes topology). Each identity feature should become a `detailInventory` entry and, if it
  can be wrong, a `featureReviewTarget`.

## Layer 8 — Uncertainty & single-image limits

- **Observe:** what the one view does not show; what is blurry or ambiguous.
- **Vocabulary:** *occluded* (blocked by another part), *hidden* (back-face / interior, not in
  this view), *uncertain* (blurry/ambiguous), *needs another view*, *undetermined*.
- **Avoid:** hallucinating occluded/hidden detail without flagging it speculative; ignoring
  perspective distortion. Every unknown here becomes a
  `preSpecAssessment.unknownsToResolveBeforeImplementation` entry and may justify `request-input`.

## Output → where each layer lands

| Layer | Feeds |
|---|---|
| 1 identification | `objectClass.primaryType` / `primaryDomain`, complexity classification |
| 2 form & silhouette | complexity tier, geometry strategy, `referenceCamera` framing |
| 3 macro/meso/micro | `componentTree` levels + `minimumSpecDepth` |
| 4 spatial relationships | `attachment` (parentSocket, contactType, embed/overlap) |
| 5 materials & surface | `materials` PBR channels + `material.localOverrides` |
| 6 color & finish | `colorMaterialRecipe`, gradient stops, `finishStyle` |
| 7 identity features | `detailInventory` details + `featureReviewTargets` |
| 8 uncertainty | `unknownsToResolveBeforeImplementation`, `request-input` decision |

## Domain specializations (apply after this pass)

This generic pass runs for every subject. When Layer 1 identifies a specialized domain, layer its
extra rules on top **without** skipping any generic layer:

- **CS2 weapon/knife/glove skins** → `grimoire/build/cs2_finishes.md` (finish style, float, paint
  seed, view-dependent environment) and `grimoire/intake/cs2_texture_acquisition.md`.
- **Characters / hybrids** → `grimoire/character/reconstruction.md` (head-units, landmarks,
  proportion lock).

The generic protocol decides *what is there*; the domain doc decides *how that class is
conventionally parameterized*.
