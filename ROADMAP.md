# img2threejs Roadmap

Where img2threejs is going: from rebuilding one object at a time to generating whole playable
worlds from reference images. For the full technical specification and acceptance criteria of
in-flight work, see [docs/UPGRADE_PLAN.md](docs/UPGRADE_PLAN.md).

Each release has one theme. That is deliberate — a version people can name is a version people
can plan around.

## Shipped

| Version | Theme | Date | Highlights |
|---|---|---|---|
| v1.0 | Object pipeline | 2026-07-15 | Staged sculpt pipeline (blockout through optimization), render-vs-reference review loop, action-ready runtime hierarchy |
| v1.1 | Detail-first analysis | 2026-07-15 | Required `detailInventory` artifact (gloss, bevel, fasteners, linework, stains), strict-quality gate blocking shallow specs before codegen |
| v1.2 | Humanoid character generator | 2026-07-21 | Character/hybrid domain detection, anatomy-aware track, proportion-lock and feature-placement gated passes, per-part character materials |
| v1.3 | Quality & efficiency (Divine Eye) | 2026-07-22 | Deterministic multi-signal review harness, input-integrity and geometry-truth gates, reference-grounded texture/material analysis, CIEDE2000 colour math |
| v1.4 | Weapon Pipeline | 2026-07-25–26 | CS2 image-matched reconstruction, provenance-aware intake and local search, projection-first finishes, family-specific adapters, structural review and component-coverage gates |

### v1.2 — Humanoid character generator

Characters and hybrid subjects became first-class citizens of the pipeline, alongside a round of
engine work on the code generator itself.

- **Character / hybrid domain detection** — assessment recognises character-like form language and
  routes the reconstruction through an anatomy-aware track instead of the hard-surface object path.
- **Humanoid component template** — measured head-unit proportions, facial landmark placement, and
  pose alignment emitted from the assessment stage.
- **Proportion-lock build pass** — a gated pass enforcing anatomical proportion correctness before
  any form or material work proceeds.
- **Feature-placement build pass** — a gated pass placing and validating facial and body landmarks
  against the reference.
- **Per-part character materials** — skin, hair, cloth, and accessory materials wired into the
  detail machinery, for stylized figures with recognisable likeness.
- **Surface-topology classification** — parts classified by surface topology to drive more accurate
  geometry choices, with per-part colour / RGBA recipes for tighter reference matching.
- **Real extrude / lathe / tube geometry** — genuine geometry generation replaced the prior
  approximations; plus tier-1 diagnostics and content-hash caching across passes.

### v1.3 — Quality & efficiency (Divine Eye)

The review harness became deterministic-first: signals are computed by scripts, and the model's
judgment is spent only where a script cannot decide.

- **Divine Eye** — a deterministic multi-signal ensemble (`divine_eye.py`): IoU and scale hard
  gates; proportion, symmetry-parity, pHash, SSIM, edge, blowout, flatness, and tonal-parity soft
  signals; self-uncertainty `probe` routing.
- **Input integrity** — reference admission and intake-correctness cross-checks, property
  auto-binding, shared pHash.
- **Geometry truth** — curve-sweep, a flatness gate, and Blum lathe-profile derivation.
- **Multi-angle** — degenerate-view detection with reference-free self-consistency, plus
  auto-framing.
- **Eye judgment layers** — a gated VLM check, per-feature verification, a bounded stop policy, and
  a calibration harness (report-only, with a separation check).
- **Texture-finish analysis** — classifies finish (gem-metal / gemstone / painted-metal /
  worn-composite / brushed-steel / plastic / `candy-coat`) and writes doc-grounded
  `MeshPhysicalMaterial` scalars. The `candy-coat` recipe exists so a saturated anodized or doppler
  coat keeps its hue instead of the environment stealing it.
- **Reference-grounded gradient stops** — foreground-masked per-band median sampling extracts a
  material's true gradient from the reference instead of hand-guessing it, and flags blue-leaning
  stops that would collapse to blue under tone-mapping.
- **CIEDE2000 colour math** — full ΔE00 (`_shared/color_metrics.py`), verified against the canonical
  Sharma test pairs, feeding report-only `hue_zone_parity` and `specular_wash` signals that catch
  "purple rendered blue" where luma and structure signals cannot.
- **Objectness** — a pure-stdlib HOG-like descriptor and cosine similarity, wired in as a soft
  signal plus a reconstruction-mode rescue.
- **Efficiency** — per-module codegen cache with neighbour invalidation.
- **Presentation** — reference-conditional post-fx (DOF / bloom) kept strictly off the evaluation
  path, so bloom cannot blow highlights the gate is measuring.

## Roadmap

| Version | Theme | Primary goal | Highlights |
|---|---|---|---|
| **v1.5** | Character Pipeline | Start supporting characters properly | Character reconstruction · facial features · rigging-ready topology · blendshape preparation · hair & clothing improvements |
| **v1.6** | Environment Pipeline | Build scenes, not just objects | Buildings · rooms · streets · trees & vegetation · terrain-aware generation · multi-object reconstruction |
| **v1.7** | Game Pipeline | Game-ready assets | Unity exporter · Unreal exporter · Blender bridge · FBX / OBJ / glTF improvements · LOD generation · collision mesh generation |
| **v1.8** | Animation Pipeline | Move assets into production | Auto rigging · auto skin weights · Mixamo compatibility · facial rig · lip-sync preparation · animation-ready exports |
| **v1.9** | AI Studio | Cut the manual work | Web UI · drag & drop workflow · batch processing · visual prompt builder · project management · cloud rendering · public showcase integration |
| **v2.0** | Procedural World Generation | Whole worlds from reference images | Multi-view reconstruction · large scene generation · semantic world understanding · procedural city generation · interior reconstruction · multi-agent generation pipeline · plugin ecosystem & API |

### Release names

- **v1.5 — The Character Update**
- **v1.6 — The Environment Update**
- **v1.7 — The Game Pipeline Update**
- **v1.8 — The Animation Update**
- **v1.9 — The AI Studio Update**
- **v2.0 — The Procedural World Update**

## Version details

### v1.4 — The Weapon Update · *shipped 2026-07-25–26*

v1.4 establishes image-matched reconstruction for CS2 hard-surface assets. The pipeline records
reference admission, family/subtype identity, metadata and texture provenance, exactness tier, and
hidden-region confidence before authoring geometry. It defaults patterned finishes, decals, and skin
surfaces to a de-lit reference projection, rather than presenting a procedural approximation as an
exact match.

The initial family route covers supported knives and the Glock-18 with dedicated component contracts.
Review now combines fixed and orbit renders with family, finish, projection, critical-detail,
geometry-integrity, and component-coverage gates. The v1.4.1 hardening update also requires
map-stripped blockout evidence and ordered pass credit, so a fused or incomplete assembly cannot pass
on the strength of a projected texture alone.

### v1.5 — The Character Update

Characters become a first-class subject rather than a stylized approximation. Facial features get
dedicated treatment, and the output topology is built to be rigged: clean, predictable loops that
deform without artifacts, plus blendshape preparation so expression work has somewhere to attach.
Hair and clothing — the two things that most often break a code-built character — get their own
material and geometry improvements.

### v1.6 — The Environment Update

The unit of reconstruction grows from one object to a scene. Buildings, rooms, and streets become
buildable subjects; trees and vegetation get procedural treatment suited to code-only generation;
and generation becomes terrain-aware, so objects sit in a scene rather than floating in a void.
Multi-object reconstruction lands here: one reference image, several subjects, correct relative
placement and scale.

### v1.7 — The Game Pipeline Update

Assets stop being Three.js-only. First-class exporters for Unity and Unreal, a Blender bridge, and
improved FBX / OBJ / glTF output make a generated model something you can drop into an existing
production pipeline. LOD generation and collision-mesh generation cover the two things every engine
asks for and no image-to-3D tool ships by default.

### v1.8 — The Animation Update

Rigging becomes automatic: skeleton generation, skin weights, and a facial rig, with Mixamo
compatibility so existing animation libraries apply without hand work. Lip-sync preparation and
animation-ready exports close the gap between "a model exists" and "a character performs".

### v1.9 — The AI Studio Update

The pipeline gets a front door for people who don't live in a terminal: a web UI with a
drag-and-drop workflow, batch processing for more than one asset at a time, a visual prompt builder,
project management, and cloud rendering. Public showcase integration wires the studio directly to
the [live gallery](https://img2threejs-showcase.pages.dev/), so publishing a result is a button
rather than a pull request.

### v2.0 — The Procedural World Update

Multi-view reconstruction removes the single-image blind-side limit that has bounded every prior
version. Large scene generation, semantic world understanding, procedural city generation, and
interior reconstruction combine into a pipeline that produces a place rather than a prop, driven by
a multi-agent generation pipeline. A plugin ecosystem and public API make the whole thing
extensible by other people.

## The long view

**Phase 1 (v1.4–v1.5) — Assets.** Build high-quality individual assets: weapons, props, characters.

**Phase 2 (v1.6–v1.7) — Worlds.** Build environments and game-ready content: buildings, vegetation,
streets, export pipelines.

**Phase 3 (v1.8–v1.9) — Production.** Turn generated assets into production-ready content: rigging,
animation, Blender, Unity, Unreal, and a web platform.

**Phase 4 (v2.0) — AI game-asset platform.** Generate entire playable worlds from reference images:
multi-view understanding, procedural world generation, AI planning, a full ecosystem, and
extensible APIs and plugins.

## Known gaps (deep-research audit — 2026-07-22)

A capability audit (NotebookLM research + `file:line` code-map) enumerated Three.js / tech-art
features the skill does not yet cover. Recorded here so they are tracked, not lost. Most are
**irrelevant to hard-surface static props** and now map onto a themed version above; one is a real
latent bug.

| # | Gap | Status / plan | Priority |
|---|---|---|---|
| G1 | SkinnedMesh + Bones + Morph targets (organic deform, facial expression) | Roadmap **v1.8 — Animation** (rig-ready topology prepared in v1.5) | deferred |
| G2 | glTF / GLB export + AnimationMixer (engine portability) | Roadmap **v1.7 — Game Pipeline** | deferred |
| G3 | **InstancedMesh — real latent bug**: `instanced-cluster` in `VALID_PRIMITIVES` has no `geometry_for()` branch; repetition systems emit a hand-rolled `Mesh` clone loop, not `InstancedMesh` (`generate_threejs_factory.py:1091-1146`) | **Hotfix in progress** | high |
| G4 | UV unwrapping / atlas, normal+AO baking (high→low), LOD, BVH; procedural-UV seams stretch at primitive joins | Partial today (procedural cyl/triplanar UV + height→normal). Baking and LOD land in **v1.7** | med |
| G5 | WebGPURenderer + TSL node materials | Deferred — architecture, not render quality | low |
| G6 | Topology / retopology / CSG boolean-merge (clean welded mesh for skinning) | Feeds **v1.5** rigging-ready topology; `three-bvh-csg` is for export/skinning, not static-prop quality | low |

**Corrections to the audit (verified against code):**
- *"IBL/PMREM not integrated"* — **inaccurate.** The generator emits `create<Type>Environment(renderer)`
  via `PMREMGenerator.fromScene(new RoomEnvironment())` (`generate_threejs_factory.py:1209-1210`);
  the showcase `Viewer` also builds a PMREM environment. IBL **is** integrated.
- *"Only a plain WebGLRenderer / no post-processing"* — **half-true by design.** The generator emits a
  `create<Type>PresentationComposer` (bloom/DOF) for the hero render, but deliberately keeps the
  evaluation render composer-free (`generate_threejs_factory.py:1251`: "plain renderer with NO
  composer — bloom blows highlights and DOF blurs edges"). A bright HDRI/bloom on the *eval* path
  would also **steal hue** from candy/anodized coats — so it must stay off the eval path.

## Contributing

img2threejs welcomes contributions, and the roadmap is responsive to real usage. If you want to work
on something above, see [CONTRIBUTING.md](CONTRIBUTING.md) — say which version's theme your work
belongs to so it lands in the right tranche. Feature requests and bug reports genuinely move
priorities: the [showcase gallery](https://img2threejs-showcase.pages.dev/) tracks likes per
category precisely so that demand, not guesswork, decides what gets built next.
