# Object Image Validation Rubric

Use this reference when the suitability decision is unclear.

## Pass

- one obvious target object
- object occupies enough of the frame
- at least one strong silhouette
- major materials are visible
- hidden side can be reasonably inferred
- target can be approximated with procedural primitives

## Conditional

- one view only but object has rotational symmetry
- some occlusion but macro shape is clear
- fine surface detail can be represented with procedural texture
- target is organic but user accepts stylization
- exact brand/logo/text fidelity is not required

## Reject

- target object is ambiguous
- photo is a scene, not an object reference
- important shape is hidden, cropped, blurred, or transparent
- request demands exact mesh extraction or manufacturing-grade dimensions
- object relies primarily on smoke, liquid, glass caustics, or lace (no reconstruction path exists for these)

## Character / Human Suitability

Do not blanket-reject a subject for being hair- or cloth-fold-dominant. If the form language is character-like (humanoid silhouette, skin/cloth/hair materials), classify it `character-conditional -> stylized` instead of `reject`. Route through `grimoire/character/reconstruction.md` (proportions, landmarks, pose, stylized materials) by default.

- **character-conditional -> stylized**: humanoid subject, at least one clear frontal view, pose readable, hair/cloth is present but the user accepts the stylized-clump/fold-normal treatment rather than photoreal strands or drape simulation. Proceed with the standard character pipeline.
- **character-conditional -> maximum likeness**: user explicitly wants the closest possible match to a specific person/character. Confirm this intent before starting, then route through `grimoire/character/likeness_maximization.md` (projection-first: template fit, camera match, de-lighting, texture projection). State up front that a single image cannot guarantee 100 percent likeness; report per-region confidence instead of claiming an exact match.
- **still reject**: no humanoid silhouette is discernible at all, the figure is fully occluded/cropped below usable proportions, or the request demands photoreal skin/hair microstructure from a single low-resolution image with no willingness to provide more views or accept stylization.

Before committing to a character spec:

- confirm which stylization level the user accepts (realistic ~7.5 heads / stylized 5-6 / chibi 2-3) — do not assume realistic by default
- request front, side, and back (or full-body) views whenever the visible view cannot support pose, proportion, or back-of-head/body inference
- if maximum likeness is requested but only one low-quality view is available, say so explicitly and offer the stylized fallback as the practical alternative

## Ask For Better Input

Ask for:

- front, side, and back views
- a neutral background
- higher resolution
- close-ups of material/detail
- desired style: realistic, stylized, low-poly, game prop, hero render

## Complex Object Detail Standard

For objects with many details, require:

- macro components for the overall mass
- meso components for visible sub-assemblies
- micro components or local features for repeated/tiny details
- material layer stack for every visually distinct surface
- local overrides for stains, scratches, dirt, color changes, wear, bumps, and roughness shifts
- confidence per component or feature
- evidence refs to image regions

If these cannot be inferred from the image, mark the spec `conditional` and list missing views or close-ups.

---

## CS2 Item Identity and References (Critical — from Bowie Knife reconstruction)

**The problem:** `--cs2` only sets the difficulty tier; it does NOT fetch metadata or official references. Assuming stock features from skin names leads to wrong geometry (e.g., assuming "no stock Bowie has sawback" when the vanilla render proves it does).

**Rule:** Get the item's real market name/identity EARLY, and pull official references FIRST before authoring geometry:

1. **Ask the user for the exact market name** up front (e.g., "Autotronic" not just "Bowie Knife with red/black finish")
2. **Use `fetch_cs2_metadata.py`** to resolve paint index + official CDN render + confirm the skin exists
3. **Fetch official + vanilla renders** before authoring geometry — these show the base model features (sawback, clip point, tang, guard style) that skin names don't reveal
4. **Never infer stock features from skin names alone** — the vanilla render is the source of truth for base model geometry

**Reference sources:** Official CS2 CDN renders, vanilla (factory new) renders, and any official artwork provided by the user. Side-view orthographic reference images are ideal for profile extraction; 3/4 angle renders help verify form and materials.

**Verification:** Before writing geometry, confirm you have:
- Exact item name (market name, not description)
- Official vanilla render (to see base model features)
- Official skin render (to see finish/pattern)
- Any orthographic side views if available
