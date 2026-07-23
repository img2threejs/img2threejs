# Changelog

All notable changes to **img2threejs** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] — 2026-07-22

The "quality & efficiency" line: a deterministic-first review harness (Divine Eye), stronger
input integrity, geometry-truth gates, and reference-grounded texture/material analysis.

### Added — Plan 1.3 (Phases 1–7)
- **Input integrity** — reference admission (`check_reference_admission.py`), intake-correctness
  cross-check (`check_intake_correctness.py`), property auto-binding, shared pHash.
- **Geometry truth** — curve-sweep (F.6), flatness gate (G.1), Blum lathe-profile derivation.
- **Divine Eye** — deterministic multi-signal ensemble (`divine_eye.py`): IoU/scale hard gates;
  proportion / symmetry-parity / pHash / SSIM / edge / blowout / flat / tonal-parity soft signals;
  self-uncertainty `probe` routing.
- **Multi-angle** — degenerate-view detection (`diagnose_render_multi_angle.py`) with reference-free
  self-consistency; auto-framing.
- **Eye judgment layers** — gated VLM gate (`vlm_gate.py`), per-feature verification (§3.8),
  bounded stop policy (§3.6), calibration harness (report-only + separation check).
- **Efficiency** — per-module codegen cache (§3.7 neighbor invalidation).
- **Presentation** — reference-conditional post-fx (DOF/bloom) strictly off the evaluation path.

### Added — session capability work (folded into 1.3)
- **Texture-finish analysis** — `stage1_intake/analyze_texture.py`: classifies finish
  (gem-metal / gemstone / painted-metal / worn-composite / brushed-steel / plastic) and writes
  doc-grounded MeshPhysicalMaterial scalars; `grimoire/build/threejs_texture_reference.md`.
- **Objectness (OSIM-lite)** — `stage4_review/objectness.py`: pure-stdlib HOG-like descriptor +
  cosine similarity; wired into Divine Eye as a soft signal + reconstruction-mode rescue.
- **`ground-blade` primitive** — lofted beveled cross-section (primary bevel + swedge/false edge)
  in the generator + validator whitelist.
- **Color-gate fix** — `diagnose_render.py` `color_is_gated(pass_id)` (color hard-fail only from
  the material pass onward, so clay blockouts don't false-fail).

### Added — reconstruction-fidelity upgrades (folded into 1.3)
- **Reference-grounded gradient stops** — `stage1_intake/extract_gradient_stops.py`: foreground-masked
  per-band median sampling extracts a material's true gradient from the reference (kills hand-guessed
  STOPS), names hue zones, and flags blue-leaning violet/blue stops (`B > R`) as `blue-collapse`
  (collapses to blue under tone-mapping) with a magenta-lean suggested correction.
- **`candy-coat` finish class** — `stage1_intake/analyze_texture.py`: an anodized/PVD/doppler
  dielectric-led recipe (metalness 0.35 / clearcoat 0.60 / envMapIntensity 0.70) so a saturated
  coloured coat keeps its hue instead of the environment stealing it; chrome-specular stays
  `gem-metal`, bright-clean stays `gemstone`. Plus a `paletteHueRisk` hue-survival annotation.
- **CIEDE2000 colour math** — `_shared/color_metrics.py`: sRGB→CIELAB + full ΔE00, verified against
  the canonical Sharma test pairs.
- **Colour-aware Divine Eye signals (report-only)** — `hue_zone_parity` (per-band CIEDE2000 along the
  axis; catches "purple rendered blue" that luma/structure signals miss) and `specular_wash`
  (saturation-decay + hue-drift-toward-cyan detector). Both ship report-only (no ensemble weight)
  until calibrated, so they never silently move a verdict.
- **InstancedMesh emission** — repetition systems now emit one `THREE.InstancedMesh` (single
  draw-call) instead of a per-instance `Mesh` clone loop; the `instanced-cluster` primitive resolves
  to its base geometry instead of failing.
- **`ground-blade` UV fix** — blade UVs now span the geometry's actual Y bounds instead of a
  hardcoded range, so an off-origin blade no longer clamps every face to the bright spine-rim row
  (the flat "one colour" / white-tip bug); the length gradient reads correctly.
- **Dep-free cutouts** — `extrude` supports `THREE.Shape.holes` + an `ovalLoop` helper (e.g. a
  wire-cutter oval hole) with no CSG dependency.

### Notes
- Pure Python 3.10+ stdlib in `forge/` (no pip installs). 20/20 forge test suites green.
- Grimoire lessons updated: shading realism (hue-survival under tone-mapping; reference beats prose),
  geometry patterns, self-correction.

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
