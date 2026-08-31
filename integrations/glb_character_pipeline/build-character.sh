#!/usr/bin/env bash
# GLB Character Pipeline (img2threejs integration) — generic orchestrator.
#
# Ported from the img2threejs-showcase repo's character-pipeline v1.5.1: chains Stage 0 (intake
# check) -> Stage 2 (re-splat, Python/numpy) -> Stage 3 (encode + emit TypeScript, per level, with
# round-trip verification) -> Stage 9 (build + no-fetch regression), as described in PIPELINE.md.
# Every path and node list comes from a --config .env file, not from anything hardcoded here.
#
# This is an OPT-IN integration (see README.md): it depends on numpy, Pillow and Playwright, which
# the img2threejs stdlib-only core deliberately does not. It operates entirely inside a companion
# showcase checkout, never inside this repo's own tree -- point IMG2THREEJS_SHOWCASE_ROOT at that
# checkout before running this script.
#
# Written for macOS's stock /bin/bash (3.2): no associative arrays, no readarray.
#
# Usage:
#   IMG2THREEJS_SHOWCASE_ROOT=/path/to/img2threejs-showcase \
#     build-character.sh --config path/to/character.env [--skip-splat] [--skip-build]
#
#   --skip-splat   skip Stage 2 even if numpy is available (reuse whatever .bin already exists on disk)
#   --skip-build   skip Stage 9's tsc/vite build + capture (useful while iterating on Stage 3 alone)
set -euo pipefail

: "${IMG2THREEJS_SHOWCASE_ROOT:?set IMG2THREEJS_SHOWCASE_ROOT to a companion img2threejs-showcase checkout (see integrations/glb_character_pipeline/README.md)}"
PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$IMG2THREEJS_SHOWCASE_ROOT" && pwd)"
export IMG2THREEJS_SHOWCASE_ROOT="$REPO_ROOT"
cd "$REPO_ROOT"

CONFIG=""
SKIP_SPLAT=0
SKIP_BUILD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --skip-splat) SKIP_SPLAT=1; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    *) echo "unrecognised argument: $1" >&2; exit 1 ;;
  esac
done
if [ -z "$CONFIG" ]; then
  echo "usage: IMG2THREEJS_SHOWCASE_ROOT=... build-character.sh --config path/to/character.env [--skip-splat] [--skip-build]" >&2
  echo "  see configs/example.env for a template, configs/girl-character.env for a filled-in one" >&2
  exit 1
fi
if [ ! -f "$CONFIG" ]; then
  echo "config not found: $CONFIG" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$CONFIG"

PY_SCRIPTS="$PKG_DIR/python"
NODE_SCRIPTS="$PKG_DIR/node"

# Override to e.g. `uv run --project integrations/glb_character_pipeline python3` (run from the
# img2threejs checkout) to use this integration's own isolated numpy/Pillow environment; falls back
# to plain python3, which works fine if numpy is already available in the ambient environment.
PY="${IMG2THREEJS_GLB_PIPELINE_PYTHON:-python3}"

: "${CHARACTER_GLB:?config must set CHARACTER_GLB}"
: "${CHARACTER_DEMO_ID:?config must set CHARACTER_DEMO_ID}"
: "${CHARACTER_NODES:?config must set CHARACTER_NODES}"
: "${CHARACTER_LEVELS:?config must set CHARACTER_LEVELS}"
export CHARACTER_GLB
export CHARACTER_DIFFUSE="${CHARACTER_DIFFUSE:-work/baseline-textures/01-texture_diffuse.png}"
export CHARACTER_OUT_PREFIX="${CHARACTER_OUT_PREFIX:-public/head/sdf-surfaces}"
export CHARACTER_WORKDIR="${CHARACTER_WORKDIR:-work/head}"
export CHARACTER_BIN_DIR="${CHARACTER_BIN_DIR:-public/head}"
export CHARACTER_WORK_TAG="${CHARACTER_WORK_TAG:-}"
export CHARACTER_CODEC="${CHARACTER_CODEC:-src/demos/girl-character/surfaceCodec.ts}"
export CHARACTER_CODEC_IMPORT="${CHARACTER_CODEC_IMPORT:-./surfaceCodec}"
export CHARACTER_REGIONS_JSON="${CHARACTER_REGIONS_JSON:-}"
export CHARACTER_CELL_SIZES_JSON="${CHARACTER_CELL_SIZES_JSON:-}"
export CHARACTER_SECTION_REGIONS_JSON="${CHARACTER_SECTION_REGIONS_JSON:-}"
export CHARACTER_SPOKES_JSON="${CHARACTER_SPOKES_JSON:-}"
export CHARACTER_CROSS_SECTIONS="${CHARACTER_CROSS_SECTIONS:-}"

echo "== Stage 0: baseline present and is actually a GLB? =="
if [ ! -e "$CHARACTER_GLB" ]; then
  echo "  MISSING $CHARACTER_GLB (relative to $REPO_ROOT) -- symlink the baseline GLB there before continuing (PIPELINE.md Stage 0)." >&2
  exit 1
fi
magic="$(head -c 4 "$CHARACTER_GLB" 2>/dev/null || true)"
if [ "$magic" != "glTF" ]; then
  echo "  $CHARACTER_GLB does not start with the glTF magic (got: '$magic')." >&2
  echo "  A dev server serves index.html for a missing file with HTTP 200 -- if this path came from a" >&2
  echo "  URL fetch rather than a local symlink, that is almost certainly what happened." >&2
  exit 1
fi
echo "  ok: $CHARACTER_GLB"

echo
echo "== Stage 1: cross-section loft (Python, the code-only floor) =="
if [ -z "$CHARACTER_SPOKES_JSON" ] || [ -z "$CHARACTER_SECTION_REGIONS_JSON" ] || [ -z "$CHARACTER_CROSS_SECTIONS" ]; then
  echo "  CHARACTER_SPOKES_JSON / CHARACTER_SECTION_REGIONS_JSON / CHARACTER_CROSS_SECTIONS not all set --"
  echo "  skipping regeneration and leaving any existing crossSections.ts alone. See PIPELINE.md Stage 1"
  echo "  and configs/example.env before setting these for a new character: the per-node spoke budget is"
  echo "  a MEASURED, per-character decision (measure_density_convergence.py), not something to guess."
else
  "$PY" "$PY_SCRIPTS/build_cross_sections.py"
  if [ "${CHARACTER_ALLOW_BASELINE_UV:-}" = "1" ]; then
    if "$PY" -c "import numpy" 2>/dev/null; then
      echo "  CHARACTER_ALLOW_BASELINE_UV=1 -- baking baseline UVs onto the cross-sections (authorised"
      echo "  deviation from the no-baseline-assets rule; see bake_atlas_uvs.py's own docstring)."
      "$PY" "$PY_SCRIPTS/bake_atlas_uvs.py"
    else
      echo "  CHARACTER_ALLOW_BASELINE_UV=1 but numpy is not installed for \$PY ($PY) -- skipping." >&2
    fi
  fi
fi

echo
echo "== Stage 2: re-splat implicit surfaces (Python + numpy) =="
if [ "$SKIP_SPLAT" -eq 1 ]; then
  echo "  --skip-splat: reusing whatever ${CHARACTER_BIN_DIR}/sdf-surfaces*.bin already exists on disk."
elif "$PY" -c "import numpy" 2>/dev/null; then
  # shellcheck disable=SC2086
  "$PY" "$PY_SCRIPTS/export_sdf_surfaces.py" $CHARACTER_NODES
else
  echo "  numpy not installed for \$PY ($PY) -- skipping Stage 2." >&2
  echo "  Run 'uv sync --project integrations/glb_character_pipeline' from the img2threejs checkout" >&2
  echo "  and set IMG2THREEJS_GLB_PIPELINE_PYTHON to that venv's python3 to re-splat node geometry;" >&2
  echo "  Stage 3 below re-encodes whatever ${CHARACTER_BIN_DIR}/sdf-surfaces*.bin already exists on" >&2
  echo "  disk, which is a no-op the first time a new character runs this without numpy available." >&2
fi

echo
echo "== Stage 3: encode + emit TypeScript modules, one per level =="
for level in $CHARACTER_LEVELS; do
  case "$level" in
    # x3 coarsens MORE than x2 (a bigger cell, so fewer vertices), so x3 defaults to "Low" and x2
    # to "Medium" -- checked against girl-character's own shipped files, not assumed.
    x2) dest="${CHARACTER_DEST_X2:-src/demos/$CHARACTER_DEMO_ID/surfaceDataMedium.ts}" ;;
    x3) dest="${CHARACTER_DEST_X3:-src/demos/$CHARACTER_DEMO_ID/surfaceDataLow.ts}" ;;
    *)  dest="${CHARACTER_DEST_DEFAULT:-src/demos/$CHARACTER_DEMO_ID/surfaceData.ts}" ;;
  esac
  echo "  -- level $level -> $dest"
  case "$level" in
    default|"") surface_bin="${CHARACTER_BIN_DIR}/sdf-surfaces.bin" ;;
    *)          surface_bin="${CHARACTER_BIN_DIR}/sdf-surfaces-${level}.bin" ;;
  esac
  node "$NODE_SCRIPTS/verify_cells.mjs" "$surface_bin" "$CHARACTER_GLB"
  node --max-old-space-size=8192 "$NODE_SCRIPTS/encode_surfaces.mjs" "$level"
  node "$NODE_SCRIPTS/emit_surface_module.mjs" "$level" "$dest"
  # SAFETY NET, carried over from the showcase pipeline after this exact mistake happened once: a
  # swapped x2/x3 destination mapping wrote the wrong level's data into an already-shipped, committed
  # file with no error -- npm run build, tsc and the round-trip check all still passed, because the
  # file was still perfectly valid TypeScript carrying a perfectly valid (just wrong) level. Only
  # `git diff` caught it. Never rely on catching this by eye again.
  if git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    && git -C "$REPO_ROOT" ls-files --error-unmatch "$dest" >/dev/null 2>&1 \
    && ! git -C "$REPO_ROOT" diff --quiet -- "$dest"; then
    echo "  !! $dest CHANGED relative to the last commit. If this was already shipped, that is worth a"
    echo "     second look before it gets committed -- run: git diff -- $dest"
  fi
done

echo
echo "== Stage 3 verify: round-trip every level against the binary it replaces =="
for level in $CHARACTER_LEVELS; do
  echo "  -- level $level"
  node --max-old-space-size=8192 "$NODE_SCRIPTS/verify_roundtrip.mjs" "$level"
done

if [ "$SKIP_BUILD" -eq 1 ]; then
  echo
  echo "--skip-build: stopping before Stage 9."
  exit 0
fi

echo
echo "== Stage 9: build & regression =="
npx tsc --noEmit
npx vite build

echo
echo "== Stage 9: renders with $CHARACTER_BIN_DIR moved out of the way =="
tmp="$(mktemp -d)"
if [ -e "$CHARACTER_BIN_DIR" ]; then mv "$CHARACTER_BIN_DIR" "$tmp/bin_dir"; fi
node "$NODE_SCRIPTS/capture-character.mjs" \
  --base http://127.0.0.1:5200/img2threejs-showcase/ \
  --demo "$CHARACTER_DEMO_ID" \
  --out "work/cmp/pipeline-check${CHARACTER_WORK_TAG}"
if [ -e "$tmp/bin_dir" ]; then mv "$tmp/bin_dir" "$CHARACTER_BIN_DIR"; fi
rmdir "$tmp" 2>/dev/null || true
echo "  wrote work/cmp/pipeline-check${CHARACTER_WORK_TAG}/*.png with $CHARACTER_BIN_DIR absent -- inspect"
echo "  these by hand; their existence with that directory absent is the proof nothing was fetched."

echo
echo "Done. Compare against a prior capture with:"
echo "  node $NODE_SCRIPTS/compare_views.mjs work/cmp/pipeline-check${CHARACTER_WORK_TAG} work/cmp/<previous>"
echo "Then record the run in \$IMG2THREEJS_SHOWCASE_ROOT/work/progress.txt, per PIPELINE.md Stage 9."
