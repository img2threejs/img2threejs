# Data Refinery MVP acceptance

Date: 2026-08-07
Branch: `henry/mvp-20260807`

## Outcome

The scoped local MVP passes structural, build, interaction, screenshot, multi-angle, and privacy checks. It is accepted only as a procedural visual approximation; it is not an engineering model, and deterministic full-image fidelity diagnostics reject it as a close pixel-level reconstruction of the complete source illustration.

## Input identity

- Label: `embodied-ai-data-refinery`
- SHA-256: `d0c4da693c43891d9934072eef079a1aa7391d58fc0771f5369e1985d90c3ce0`
- Private fixture: byte-identical copy, 2,208,431 bytes, mode `0600`

No source location is committed. The reference, private manifest, screenshots, and comparison sheet remain outside tracked Git content.

## Implemented systems

| System | Measured evidence |
| --- | --- |
| Model contract | Root is `DataRefinery`; `CoreChamber`, `PipeNetwork`, `InputStream`, and `OutputPlatforms` exist. |
| Useful bounds | Unit test verifies width > 4, height > 3, and depth > 3. |
| Core chamber | Procedural glass vessel, metal rims, inner pulse, and 90 deterministic points. |
| Secondary equipment | Three tanks, five curved pipe runs, layered base, processor housing, and coils. |
| Data flow | Three input curves with 45 elapsed-time-driven particles. |
| Outputs | Three transparent platforms with deterministic synthetic features and shimmer. |
| Viewer | WebGL canvas, orbit, zoom, pan, resize, and reset to camera `(9, 7, 11)` / target `(0, 2, 0)`. |
| Limitation disclosure | `视觉近似，非工程模型` is visible and asserted by the browser smoke. |

## Acceptance runs

| Gate | Result |
| --- | --- |
| Upstream baseline | PASS — 673 `unittest` tests, 25 skipped. |
| Model tests | PASS — 2 Vitest tests. The motion test proves equal elapsed time reproduces the same particle position and a different time moves it. |
| Production build | PASS — TypeScript strict no-emit check and Vite production build. Vite reports a non-blocking 529 kB Three.js bundle-size warning. |
| Browser smoke | PASS — canvas, reset, and limitation label visible; reset clicked; zero `pageerror` events. |
| Captures | PASS — fixed 286,454 bytes; left orbit 203,438 bytes; right orbit 214,042 bytes; each 1536 × 1024 and visually read back. |
| Multi-angle geometry | PASS — no degenerate view; orbit silhouette ratios 0.852 and 0.608, both above the 0.15 collapse threshold. |
| Source integrity | PASS — source digest remained equal to the recorded SHA-256 after all work. |
| Tracking privacy | PASS — no tracked `private`, `reference.png`, or `artifacts` path; no committed private absolute source path. |

Port `4173` was already occupied by an unrelated local service during validation, so the documented `MVP_URL` interface was exercised against the preview on `http://127.0.0.1:4174`.

## Visual comparison evidence

A local side-by-side sheet was generated and inspected with the source on the left and the fixed render on the right. The procedural scene preserves the main visual grammar—central refinery, flowing inputs, blue/green/cyan outputs, pale technical background—but intentionally omits source-only photographic panels, the fourth purple terrain panel, dense particle volume, and much of the fine pipework.

Semantic inspection scores are heuristic 0–1 judgments from that same sheet, not physical measurements:

| Feature | Score | Review |
| --- | ---: | --- |
| Central chamber | 0.76 | Clear transparent chamber, cap/rim, blue tint, and particles; source is denser and more luminous. |
| Secondary tanks | 0.69 | Multiple metallic vessels are legible; count, proportions, and rear tower detail are simplified. |
| Pipe network | 0.62 | Curved metallic connectivity reads correctly; source has substantially greater routing density. |
| Input stream | 0.48 | Direction and discrete particles are present; source has wider, layered ribbons and more volume. |
| Output platforms | 0.61 | Three color-coded floating panels are clear; source has denser feature clouds plus an unsupported fourth panel. |
| Overall scoped approximation | 0.63 | Suitable for an interactive MVP; not close enough for full-image fidelity acceptance. |

Deterministic pixel/feature diagnostics were also retained rather than overruled:

- `diagnose_render.py`: REJECT, exit 1 — silhouette IoU 0.3587, aspect-ratio delta 0.1597, scale delta 0.1473, bilateral-symmetry error 0.3743.
- `divine_eye.py`: REJECT, exit 1 — fidelity 0.4043 against target 0.85; pHash 0.5938, SSIM 0.2352, edge overlap 0.4173, tonal parity 0.7139, objectness 0.6436.
- These failures are expected for the intentionally scoped procedural scene versus the complete multi-panel 2D composition. They prohibit any claim of close visual reproduction; they do not invalidate the tested local interaction MVP.

## Commands

```bash
python3 -m unittest discover -s forge/tests -p 'test_*.py'
cd examples/henry-data-refinery
npm test
npm run build
npx vite preview --host 127.0.0.1 --port 4173
MVP_URL=http://127.0.0.1:4174 npm run smoke
```

Privacy checks from the repository root:

```bash
git status --short
git ls-files | rg 'private|reference\.png|artifacts' && exit 1 || true
```

## One-image limitation and unsupported claims

Only one view was available, so rear faces, hidden connections, true depth, and physical scale cannot be recovered from evidence. The app does not claim engineering topology, process correctness, flow simulation, exact dimensions, manufacturability, materials accuracy, performance capacity, or bill-of-material completeness. Orbit views demonstrate that the authored scene is genuinely three-dimensional; they do not validate unseen geometry against the source.
