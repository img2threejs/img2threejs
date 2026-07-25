# CS2 / Steam Item Finishes

Rulebook for the supported CS2 **knife** finish track. The reconstruction stays **image-first**: a
reference image is the only required input. A skin name, descriptor (`finishStyle`, `float`,
`paintSeed`), or an extracted Valve texture are optional layers that add exactness when the user
supplies them — they never block the default path.

**Two finish routes, chosen by goal — do NOT default to procedural for a reference-match.**

This rulebook does not authorize pistol, rifle, SMG, sniper, heavy, glove, or unknown knife
subtypes. Intake must return `unsupported-family` or `unsupported-subtype` before finish selection
for those families. The route (`reference-projection`, `authored-texture`, or `procedural-finish`)
and evidence tier (`image-only`, `metadata-assisted`, or `exact-texture`) are separate fields in
`cs2-intake.json`; a route fallback never changes the evidence tier.

- **Projection (default when the goal is matching a specific reference image — e.g. rebuilding a
  named skin from its photo):** author the geometry procedurally, then project the **de-lit**
  reference crop onto the blade/grip as the finish and bake it into UVs — camera-match
  (`solve_camera_pose.py`) → de-light (`delight_albedo.py`) → project + bake
  (`bake_projected_texture.py`). Recipe: `grimoire/character/likeness_maximization.md` (its levers
  generalize from faces to objects). This is what reaches reference fidelity for patterned finishes
  (Doppler / Gamma Doppler / Marble Fade / Fade), and is what the v1.3 baseline demos use. A
  procedural finish for a patterned skin reads visibly wrong beside the reference — that mismatch
  is the #1 CS2 fidelity failure.
- **Procedural per finish style (default ONLY when live view-dependent response matters more than
  matching one reference):** build the finish from independent PBR channels + paint-seed for a real
  environment/anodized response.

**Never** project the raw 2D inventory **icon** as albedo: an icon is a flat render with baked
lighting, no paint-seed, no PBR channels, no environment response; projecting *that* locks in one
wrong, flat-lit pattern. The projection route sidesteps this by **de-lighting first** — the real
distinction is *de-lit reference crop* (the fidelity path) vs *raw flat-lit icon* (banned), NOT
projection vs procedural.

## Identity precedence (who decides the finish style)

`resolve_cs2_finish_style()` (in `forge/stage2_spec/new_sculpt_spec.py`) resolves in order:

1. **explicit descriptor** (`--finish-style`) —
2. **skin name** (`--skin-name`, mapped by `SKIN_NAME_FINISH_HINTS`) —
3. **vision inference** (`--vision-finish-style` + `--vision-confidence`).

A vision read below the confidence threshold (default 0.55) is still used but **flagged**, never
silently dropped. When two sources disagree, the higher-precedence one wins **and the conflict is
written to `preSpecAssessment.unknownsToResolveBeforeImplementation`** — never silently
overwritten. With nothing resolvable, the default is `anodized-multicolored` (the Doppler case the
track exists to fix).

## Finish styles

Eight styles, each with its own PBR recipe and macro/meso/micro `surfaceFrequencyBands`
(`CS2_FINISH_PROFILES`):

| finishStyle | metalness | roughness | view-dependent | note |
|---|---|---|---|---|
| `solid` | low | mid | no | near-uniform lacquer |
| `hydrographic` | low | mid | no | dip-film swirl print |
| `anodized` | high | low | **yes** | dyed metal; colour from reflections |
| `spray-paint` | low | high | no | matte overspray speckle |
| `anodized-multicolored` | high | very low | **yes** | Doppler/Marble-Fade; strong env |
| `custom-paint-job` | low–mid | mid | no | non-tiled illustrative art over dark substrate |
| `patina` | mid | mid | no | oxidation: darkens/hue-shifts with wear |
| `gunsmith` | mid | mid | no | custom-paint ↔ patina mask blend |

**View-dependent** finishes (`anodized`, `anodized-multicolored`) read their colour from
environment reflections. Without an environment map + filmic tonemapping they render muddy — see
*Environment* below.

## Float → wear

`_cs2_wear_mask(float_value)` maps a CS2 float `0.0` (Factory New) … `1.0` (Battle-Scarred) to a
wear mask (albedo-alpha wearness curve + AO bias + curvature-weighted edge scratches/chips), so
scratching concentrates on exposed edges and recesses accumulate grime. Higher float → higher
`edgeWear`, scratches above ~0.15, chips above ~0.55.

With **no float** (image-only default) the mask is an approximated light-moderate condition
estimated from the visible reference and marked `approximated: true` — never presented as an exact
per-item float.

## Paint seed → pattern placement

`_cs2_pattern_affine(paint_seed)` derives a deterministic `A = T2·R·S·T1` affine (rotation +
translation + scale, and the resulting 2×3 matrix) from the seed, so two items sharing a skin but
differing in seed place the pattern differently, and the same seed reproduces identically.

With **no paint seed** (image-only default) a fixed centered placement is used and marked
`approximated: true` — it reproduces the skin's *look*, not a specific item's exact "Max" pattern.
Exact per-item seed matching (community pattern DB / inspect-link) is a deferred follow-up.

## PBR and environment

- **Material**: metallic-roughness `MeshPhysicalMaterial` with independent map channels (never
  alias albedo into roughness/normal/AO). Profiles seed metalness / roughness / clearcoat /
  `envMapIntensity`.
- **Environment (code-generated default)**: `create<Name>Environment(renderer, hdriUrl?)` in the
  generated factory builds a `RoomEnvironment` → `PMREMGenerator` IBL **in code — no bundled image
  asset**, so the image-only path always has one. A user HDRI (`hdriUrl`, via `RGBELoader`) is an
  optional realism upgrade; it falls back to the code-generated default on omission or load
  failure.
- **Renderer**: `configure<Name>Renderer(renderer)` sets `ACESFilmicToneMapping` + `SRGBColorSpace`
  — load-bearing for view-dependent finishes, not cosmetic.
- **Inspect**: `create<Name>InspectControls(camera, domElement)` wraps `OrbitControls` tuned for
  close inspection, because a view-dependent finish only reads correctly in motion.

### The no-environment gate

`validate_cs2_view_dependent_environment()` (in `validate_sculpt_spec.py`) **blocks generation
only** when a `needsEnvironment` material would render with no environment at all — i.e.
`cs2Finish.environmentAvailable` is explicitly `false` (`--no-environment`). Because the
code-generated default always exists otherwise, this is a last-resort guard, **not** the common
path: the flagship image-only Doppler case passes normally.

## Complexity tier & detail floor

A supported CS2 knife skin always carries more identity-defining detail than a generic object
at the same structural tier, so `--cs2` **defaults the complexity tier to `ultra-complex`**
(`targetMinDetails` 16) — the CS2 track is held to the top fidelity bar regardless of how simple
the bare geometry looks. If `--complexity` is set lower by hand, the detail-count floor still
never drops below **9** (see `grimoire/intake/quality_contract.md`).

## Honest ceiling

Image-only reproduces the skin's *look* faithfully but cannot recover a specific item's exact paint
seed / float — the pipeline reports these as approximated rather than implying a byte-exact match.
For exactness, layer in a descriptor or extracted Valve textures
(`grimoire/intake/cs2_texture_acquisition.md`).
