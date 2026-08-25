# Image analysis — Han Huan-Shou Dao

Reference: `references/chinese-swords/汉代环首刀.jpg`
Probe: JPEG 590×439, 19456 bytes, aspect 1.34, `technicalSuitability: conditional` (low resolution).
User classification: **汉代环首刀** (Han ring-pommel dao), not a double-edged jian.
Fidelity accepted for this first pass: **stylized / approximate**.

## Layer 1 — Identification

- **Observe:** one isolated long iron blade lying on pale square floor tiles. Distal end tapers to a point; proximal end terminates in a closed circular ring. A short yellowish metal band sits immediately adjacent to the ring. No guard / crossguard is visible. Floor grout lines provide a weak scale cue.
- **Classify:** single-edged long blade, hard-surface archaeological / excavated object. `primaryDomain: object`. Confidence 0.78 that this is a huan-shou dao rather than a jian (no visible median ridge, one long edge reads sharper, user confirmed dao).
- **Inference (flagged):** originally ~80–110 cm class; photo has no ruler. Tile squares look ~30 cm, object spans roughly 3 tiles → ~90 cm plausible.

## Layer 2 — Form and silhouette

- Bounding volume: a very long, thin rectangular bar with a distal taper and a proximal torus. Aspect roughly **length : width : thickness ≈ 24 : 1 : 0.25** (thickness inferred; only the flat is visible).
- Symmetry: bilateral about the long axis in plan; **not** rotational. Slight sag / saber bend along length.
- Shape language: geometric hard-surface, worn.
- Negative space: the ring aperture is the only enclosed hole.

## Layer 3 — Macro / meso / micro

- Macro: blade body; ring pommel.
- Meso: tang / stem between blade heel and ring; yellowish collar / ferrule at the ring junction; short neck entering the ring.
- Micro: rust mottling, mid-blade brighter patch, tip wear, collar/tang seam, ring/tang junction.

## Layer 4 — Spatial relationships

- `<blade, continuous-with, tang>` — no guard; butt / overlap at heel, same rust family.
- `<collar, wrapped-around, tang>` near the ring; contact type overlap.
- `<ring, attached-to, pommel-neck>` — ring plane is the same as the blade face (full circle visible in the top-down photo), hole through Z.
- Nothing floats; all parts share one long axis (X).

## Layer 5 — Materials (PBR, observation then inference)

- Blade / tang / ring: oxidized iron. Albedo dark red-brown to near-black. Metalness residual (rust is dielectric oxide over metal) — treat as low-mid metalness 0.15–0.35, roughness 0.55–0.85. Pitting / flake relief.
- Mid-blade brighter patch: **ambiguous** — could be thinner rust exposing metal, or a floor specular bounce. Record as a local lower-roughness stain, confidence 0.4.
- Collar: yellow-tan, lower saturation than brass; inference = copper / gilt remnant, metalness ~0.6, roughness ~0.45, with tarnish.

## Layer 6 — Color and finish

- Blade dominant: dark rust brown `#4A3024` / `#3A241C`.
- Blade secondary: mid rust `#6B4030`, near-black pits `#2A1A14`.
- Mid sheen: lighter brown `#8A5A40`.
- Collar: dull gold `#C4A46A` → tarnish `#8A7040`.
- Finish: matte oxide, not polished steel. No candy-coat, no anodized hue.

## Layer 7 — Identity-defining features

1. Closed ring pommel, in the blade-face plane.
2. No guard.
3. Extreme slender ratio, almost constant width until the distal third.
4. Single-edged plan (spine straighter, edge tapers).
5. Continuous rusted iron from tip to ring.
6. Short dissimilar-metal collar only at the ring, not a full handle wrap.

## Layer 8 — Unknowns

- Thickness / grind angle / whether a true bevel exists (top-down only).
- Back face, scabbard side, tang construction (hidden vs wrapped).
- Whether the mid highlight is rust or floor bounce.
- Exact ring gauge and whether the ring is welded closed or a formed loop.
- Original polish vs excavated rust (we reconstruct the **photo**, i.e. rusted relic, not a museum-polished replica).

Suitability: **conditional** — one object, readable silhouette, inferable hidden side, but low-res single view. Do not claim exact geometry.
