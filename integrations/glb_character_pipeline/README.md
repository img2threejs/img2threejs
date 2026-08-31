# GLB character pipeline (opt-in)

**GLB reference is optional. If the character build has none, skip this integration entirely** — do
not error, do not partially apply it, do not substitute anything. Build through the core img2threejs
image-driven pipeline (`forge/`) instead; nothing here has a code path that runs without a real GLB
point cloud to measure. Only reach for anything below once you've confirmed a GLB reference actually
exists for this character (see `PIPELINE.md`'s "Applicability gate").

This integration reconstructs a **multipart GLB baseline** as code-only TypeScript/three.js — a fully
procedural character with no `.glb`/`.bin` fetched at runtime — using an SDF point-cloud splat and
Surface Nets contouring for organic detail a cross-section loft alone can't reach (eyelids, folds,
navels). It exists as an opt-in integration, not core `forge/`, because it needs `numpy` and `Pillow`
for the splat/bake step and `playwright`/`esbuild` for the Node-side capture and round-trip verification
— dependencies the stdlib-only `forge` core deliberately does not carry. `CONTRIBUTING.md` allows this
explicitly: "If you want a projection or generative-assist path, propose it as an explicit, flagged,
opt-in mode."

This is a **companion-showcase** tool: every script here operates against a separate
`img2threejs-showcase` checkout (where the demo's own `src/demos/<character>/`, `public/mesh/`, and
`work/` directories live), never against this repo's own tree. Point `IMG2THREEJS_SHOWCASE_ROOT` at
that checkout before running anything below — the same env var `forge/tests` already uses to reach a
companion showcase for its TypeScript typecheck gates.

**The GLB is a measurement instrument, never a shipped asset — but "code-only" covers two genuinely
different things here, and they should not be read as the same claim.** Both stages embed measured
data as committed `.ts` code (no runtime fetch either way); they differ in HOW MUCH of the measured
surface that data actually is, and what runtime code does with it:
- **Stage 1 (cross-section loft)** measures a SPARSE set of ring samples per node (girl-character: 748
  rings, 86,240 points total, from a 2.1-million-vertex source) and a small set of tuning numbers
  (per-node spoke count, Catmull-Rom control behaviour, target edge length). At runtime, a loft
  function SYNTHESISES new geometry by interpolating *between* those measured rings — the walls
  between ring i and ring i+1 do not exist in the measurement at all; they are generated.
- **Stage 2/3 (SDF splat + Surface Nets + compact encoding)** measures DENSELY — every sign-changing
  voxel cell, millions of vertices — and re-encodes that (varint deltas + connectivity bits,
  ~8 bytes/vertex) as base64 inside a committed `.ts` module. At runtime there is no interpolation or
  synthesis: decoding reproduces the measured surface itself, essentially unchanged. It exists only
  because a cross-section loft is fundamentally 2.5D and cannot express detail that folds back on
  itself (an eyelid, a nostril, a navel) — see `PIPELINE.md` Stage 1's closing note and Stage 2's
  opening.

Read `PIPELINE.md` end to end before running this on a new character — it is the full, stage-by-stage
methodology (intake, cross-section loft, Surface Nets, compact encoding, verification, the eye-socket
lesson in Stage 6), distilled from actually building `girl-character`. This README only covers install
and the day-to-day commands.

## Install

```bash
uv sync --project integrations/glb_character_pipeline --python 3.11
cd integrations/glb_character_pipeline/node && npm install && npx playwright install chromium
```

## Use

```bash
export IMG2THREEJS_SHOWCASE_ROOT=/path/to/img2threejs-showcase
export IMG2THREEJS_GLB_PIPELINE_PYTHON="uv run --project integrations/glb_character_pipeline python3"

# reproduce girl-character's own shipped build, byte-for-byte
integrations/glb_character_pipeline/build-character.sh \
  --config integrations/glb_character_pipeline/configs/girl-character.env

# bring up a new character: copy configs/example.env, fill in every value, then run the same way
integrations/glb_character_pipeline/build-character.sh --config path/to/your-character.env
```

`--skip-splat` reuses whatever `.bin` intermediate already exists on disk (skip Stage 2); `--skip-build`
stops before Stage 9's `tsc`/`vite build` and screenshot capture, useful while iterating on Stage 3
alone. See `PIPELINE.md`'s Appendix A for anti-patterns worth avoiding on a first read, and Appendix B
for the full stage → script → output file map.

## What's here

- `python/slice_node.py`, `python/measure_density_convergence.py`, `python/build_cross_sections.py` —
  Stage 1: cross-section loft, the code-only floor. `measure_density_convergence.py` only prints
  tables — deciding the final per-node spoke count is a human/agent judgment call, not something it
  resolves for you (see `PIPELINE.md` Stage 1). Optional per build: leave `CHARACTER_SECTION_REGIONS_JSON`
  / `CHARACTER_SPOKES_JSON` / `CHARACTER_CROSS_SECTIONS` unset to skip and keep an existing file as-is.
- `python/bake_atlas_uvs.py` — an explicitly-gated (`CHARACTER_ALLOW_BASELINE_UV=1`) escape hatch that
  transfers a baseline GLB's own texture UVs onto the cross-sections. This departs from img2threejs's
  normal no-baseline-assets rule (it was a one-off, authorised exception for girl-character) — read its
  own docstring in full before ever setting that env var for a new character.
- `python/build_head_surface.py`, `python/export_sdf_surfaces.py` — Stage 2: SDF splat + Surface Nets,
  per node, batched across a character's node list.
- `node/verify_cells.mjs` — Stage 3 pre-check: rebuild every triangle from cell adjacency alone and diff
  against the source; must show 0 collisions before any encoder byte is written.
- `node/encode_surfaces.mjs`, `node/emit_surface_module.mjs` — Stage 3: compact varint + connectivity
  encoding, base64-embedded into a committed `.ts` module (no runtime fetch).
- `node/verify_roundtrip.mjs` — Stage 3 verify: round-trip every emitted level against the binary it
  replaces.
- `node/capture-character.mjs`, `node/compare_views.mjs` — Stage 9: headless-browser renders with the
  binary intermediate moved out of the way (proof nothing was fetched), and pixel-diff against a prior
  capture.
- `build-character.sh` — the generic orchestrator chaining all of the above; every path and node list
  comes from a `--config` `.env` file (`configs/example.env`, `configs/girl-character.env`), never
  hardcoded in the script.

## Environment

- Python: `integrations/glb_character_pipeline/.venv/` (via `uv sync`).
- Node: `integrations/glb_character_pipeline/node/node_modules/` (via `npm install`).

See `docs/integrations/reference_fidelity_tooling.md` for how this fits alongside the other
diagnostic/reference tooling, and `grimoire/build/python_threejs_render_bridge.md` for the GLB-mediated
capture bridge contract this integration's capture step relies on.
