# Shared project instructions

This repository is the canonical source for the `img2threejs` skill: it turns one reference image
into a **code-only, procedural Three.js model** — a TypeScript `THREE.Group` factory built from
primitives, procedural shaders, and generated geometry. No mesh downloads, no photogrammetry, no
art packs.

Host entrypoints (`~/.claude/skills/img2threejs`, `~/.codex/skills/img2threejs`) should be symlinks
to one checkout — never independent copies, or the two hosts drift apart silently. Same rule in
`SKILL.md` → *Canonical shared checkout*.

Audited at `9fbd0ca` (main, v1.5.1, 2026-08-29); the counts below were refreshed on this branch.

## Architecture

`SKILL.md` is the agent-facing entrypoint. Everything else splits three ways:

- **`forge/`** — deterministic Python tooling. 107 modules, **Python 3.10+ standard library only**
  (zero third-party imports; verified). 75 of them are argparse CLIs the agent shells out to.
- **`grimoire/`** — routed reference material the agent reads, not runs. 33 Markdown documents plus
  one JSON contract, indexed by `grimoire/scripts.md`.
- **`integrations/`** — *optional*, isolated, and the one place third-party dependencies live.

Deterministic Python does validation and gating; model tokens are spent only on visual judgment
and code generation. That split is the token-efficiency design (`docs/TOKEN_COST.md`).

### Pipeline

Staged sculpting, one pass at a time, each vision-reviewed and self-corrected until it clears its
gate. Authoritative order is `DEFAULT_PASS_ORDER` in `forge/stage3_build/orchestrate_passes.py:18`:

```
blockout → structural-pass → form-refinement → material-pass
        → surface-pass → lighting-pass → interaction-pass → optimization-pass
```

`VISUAL_PASS_IDS` is every pass except `optimization-pass`. Spec `schemaVersion` is `2.0`/`2.1`.
Full diagram and gate logic: `docs/ARCHITECTURE.md`.

## Directory structure

| Path | Contents |
| --- | --- |
| `forge/next.py`, `state.py`, `report.py` | Top-level entry points: resume, workflow state, status |
| `forge/_shared/` | 15 cross-stage modules (spec search, image hash, SDF primitives, subdivision) |
| `forge/stage1_intake/` | 31 modules — image probe, detail inventory, landmarks, CS2 extraction |
| `forge/stage2_spec/` | 8 modules — spec authoring and `validate_sculpt_spec.py` |
| `forge/stage3_build/` | 8 modules — `generate_threejs_factory.py`, pass orchestration, UV, hull |
| `forge/stage4_review/` | 34 modules — every gate, comparison, and `diagnose_render.py` |
| `forge/stage5_rig/` | Rig emission, geodesic skinning, payload validation |
| `forge/materials/` | Versioned Three.js material registry and compatibility |
| `forge/tests/` | 87 `test_*.py` files, 5 fixtures |
| `grimoire/{intake,build,review,character,readiness,feedback,glossary}/` | Routed reference docs |
| `docs/` | `ARCHITECTURE.md`, `TOKEN_COST.md`, CS2 gates/anatomy, materials, render-profile schema |
| `skills/` | 4 sub-skill documents (CS2 knife/pistol, technical analysis, generic extract) |
| `scripts/` | 4 utilities — Playwright capture, character audit, issue triage, release metadata |
| `integrations/` | `vision/`, `mesh3d/`, `glb_character_pipeline/` |

## Change rules

- Preserve the code-only procedural Three.js contract; do not silently download meshes or art packs.
- Keep claims honest: distinguish implemented capability from roadmap or design-only documentation.
- Treat `forge/` as deterministic tooling and `grimoire/` as routed reference material.
- Keep `forge/` free of third-party imports. New dependencies belong in `integrations/`, behind
  their own `pyproject.toml`, and must stay optional.
- Keep backward compatibility for existing sculpt specs unless a migration is explicitly planned.
- When changing schema, gates, generators, or review behavior, add or update focused tests.
- Keep `SKILL.md`, `README.md`, `CHANGELOG.md`, and `ROADMAP.md` consistent when release-facing
  behavior changes.
- Reference the companion showcase through `IMG2THREEJS_SHOWCASE_ROOT`, never a path that only
  exists on one machine — it passes there and fails everywhere else, CI included.
  **This rule is currently violated in three tests** that hardcode a sibling checkout instead of
  calling `showcase_test_support.showcase_root()`:
  `test_albedo_color_space.py:85`, `test_sdf_primitives.py:296`, `test_tapered_sweep.py:269`.

## Coding conventions

Extracted from the 107 non-test `forge/` modules, not assumed:

- `from __future__ import annotations` at the top (103/107). Modern type hints — `dict[str, Any]`,
  `list[str]`, `X | None`.
- CLI modules carry `#!/usr/bin/env python3`, a one-line module docstring, `def main()`, and an
  `if __name__ == "__main__":` guard.
- Cross-stage imports go through `sys.path[:0] = [...]` against a `_FORGE_ROOT`/`ROOT` computed
  from `Path(__file__).resolve()`, then a plain module import with `# noqa: E402`.
- `pathlib.Path` everywhere; JSON in and JSON out.
- Test files are self-runnable (`Run: python3 forge/tests/test_x.py`) and several open with a long
  docstring recording the measurement that motivated the gate. Preserve that style.

## Dependencies

- **`forge/`** — none. `forge/requirements.txt` exists only to state that: Python ≥ 3.10 stdlib,
  no Pillow / numpy / OpenCV / Playwright. PNG writing and comparison sheets use `struct`/`zlib`.
- **`integrations/vision/`** — `mediapipe`, `numpy`, `pillow`, `torch`, `torchvision`,
  `transformers`. Python `>=3.11,<3.13`, managed by `uv`.
- **`integrations/glb_character_pipeline/`** — `numpy`, `pillow` (Python `>=3.11,<3.13`, `uv`),
  plus a Node side under `node/` with its own `package.json`.
- **Showcase (external)** — TypeScript gates shell out to `npx tsc` and `node_modules/.bin/esbuild`
  from an `img2threejs-showcase` checkout with `npm ci` run. Not vendored here.

## Verification

```bash
python3 -m unittest discover -s forge/tests -p 'test_*.py'
```

CI (`.github/workflows/ci.yml`) runs the same command on Python 3.10 via the shared
`img2threejs/ci-workflows` reusable workflow — **without** a showcase root. The TypeScript gates
therefore skip in CI, so a green CI run has *not* proven the emitted Three.js compiles.

Set `IMG2THREEJS_SHOWCASE_ROOT` to a showcase checkout to include the TypeScript typecheck gates;
without it they skip. Add `IMG2THREEJS_REQUIRE_SHOWCASE=1` to turn that skip into a failure.
Measured on this branch: 1093 tests / 29 skipped without it, 1122 tests / 4 skipped with it. The
29 are the showcase-dependent gates themselves. The last 4 are upstream gaps — archived reference
captures and `scripts/rig-milestone0.mjs` are not present in the public showcase.

Do not report completion without reading the fresh outputs. For visual reconstruction changes,
structural tests and screenshot/reference-loop validation are separate required gates.

### Running on Windows

The suite assumes a POSIX host. Three portability defects bite otherwise:

- **Encoding.** 34 non-test `forge/` modules and 131 test call sites use `read_text()` /
  `write_text()` / text-mode `subprocess` without `encoding=`. Python < 3.15 on Windows defaults to
  cp1252, so any non-ASCII target name or material label raises `UnicodeDecodeError`. Export
  `PYTHONUTF8=1`.
- **`npx`.** List-form `subprocess.run(["npx", ...])` fails with `WinError 2`; `CreateProcess` only
  auto-appends `.exe`, never `.cmd`. Resolve with `shutil.which("npx")`.
- **`.bin/esbuild`.** npm writes an extensionless shell script beside the real `esbuild.cmd`;
  running the former raises `WinError 193`. Resolve with `shutil.which("esbuild", path=<.bin>)`.

## Mandatory visual screenshot gate

For every visual reconstruction task, a readable screenshot is a hard prerequisite for visual
implementation claims and completion:

1. Before accepting visual results, verify that the browser/screenshot tooling is installed,
   authenticated, reachable, and able to capture the running showcase.
2. Save fresh PNG/JPEG screenshots inside the workspace, including the fixed reference view and the
   required orbit views. Inline previews alone are not evidence.
3. Read the saved screenshots back with an image-capable tool and verify they contain the rendered
   model at the expected dimensions. A screenshot that cannot be opened or visually read is a failed
   gate.
4. Produce and retain a side-by-side reference/render comparison, semantic image scoring,
   pixel/feature comparison, and the `forge/stage4_review/diagnose_render.py` output for the saved
   render before reporting visual validation.
5. If capture, file write, readback, comparison, scoring, or diagnosis fails, stop the visual
   workflow and repair the tooling first. Do not infer visual evidence from runtime readiness,
   structural tests, inline previews, or code review, and do not claim the visual gate passed.

## Current state

- **v1.5.1** (2026-08-22) — "The Character Update", plus the region and swept-arc gates.
- 52 commits, 2026-07-15 → 2026-08-29. Tags: `v1.5.1`, `v1.5-beta`, `v1.4.3`, `v1.3`, `v1.0`.
- Active areas from recent history: character track, material pipeline, CS2 reconstruction,
  release automation, sponsor/doc upkeep.
- Known gaps are tracked in `ROADMAP.md` → *Known gaps*.
- `.cache/spec-search/` is a gitignored runtime artifact; `assets/` is gitignored except
  `logo.svg` and `sponsors/`.
