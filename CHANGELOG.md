# Changelog

All notable changes to **img2threejs** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-07-21

**Theme: Humanoid character generator.** Characters and hybrid subjects become
first-class citizens of the reconstruction pipeline, alongside a round of engine
and harness improvements to the underlying code generator.

### Added

- **Character / hybrid domain detection.** Assessment now recognizes character-like
  form language and routes the reconstruction through an anatomy-aware track instead
  of the hard-surface object path.
- **Humanoid component template.** A flattened humanoid template with measured
  head-unit proportions, facial landmark placement, and pose alignment is emitted
  from the assessment stage.
- **Proportion-lock build pass.** New gated pass that enforces anatomical proportion
  correctness before form/material work proceeds.
- **Feature-placement build pass.** New gated pass that places and validates facial
  and body landmarks against the reference.
- **Per-part character materials.** Skin, hair, cloth, and accessory materials
  integrate with the Track A detail machinery for stylized human figures with
  recognizable likeness.
- **Surface topology classification.** Parts are classified by surface topology to
  drive more accurate geometry choices.
- **Per-part color / RGBA recipes.** Explicit per-part color and RGBA material
  recipes for tighter reference matching.
- **Tier-1 diagnostics.** Diagnostic reporting layer for the generation harness.
- **Hash caching.** Content-hash caching to avoid redundant recompute across passes.
- **Real extrude / lathe / tube geometry.** Genuine extrude, lathe, and tube geometry
  generation replaces prior approximations.

### Changed

- Restructured the project layout ahead of the full harness rebuild, including
  stage-prefixed script names for clearer pipeline ordering.

### Docs

- Published a public ROADMAP (v1.0 → v1.5) and a token-cost document.
- README remake: 3D showcase, live-demo links, new logo, and animated GIF previews
  (shotgun, knife, war-hauler, Sony, Doraemon House, Crowned Loot Chest).
- Added LICENSE, CONTRIBUTING, and a community-outreach promotion playbook.
- Funding pointed to the VN donate page (MoMo / VietQR).

## [1.1.0] - 2026-07-15

**Theme: Detail-first analysis.**

### Added

- Required `detailInventory` artifact enumerating identity-defining micro-details
  (gloss zones, bevels, fasteners, engraved/painted linework, contours, stains, wear).
- Strict-quality gate that blocks code generation until every detail maps to a real
  component or material entry, preventing shallow specs from reaching the renderer.

## [1.0.0] - 2026-07-15

**Theme: Object pipeline.** Initial release.

### Added

- Staged sculpt pipeline: blockout → structure → form → material → lighting →
  interaction → optimization, with a visual gate on each pass.
- Image suitability validation and `ObjectSculptSpec` authoring (components + materials).
- Render-vs-reference review loop using side-by-side comparison sheets.
- Action-ready runtime hierarchy exposing pivots, sockets, and colliders.
- Token-efficient, code-only output (diffable TypeScript + JSON spec, no binaries).

[1.2.0]: https://github.com/hoainho/img2threejs/releases/tag/v1.2.0
[1.1.0]: https://github.com/hoainho/img2threejs/releases/tag/v1.1.0
[1.0.0]: https://github.com/hoainho/img2threejs/releases/tag/v1.0.0
