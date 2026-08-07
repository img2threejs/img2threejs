# Data Refinery procedural MVP

A locally runnable Three.js visual approximation of an embodied-AI data-refinery illustration. It is a presentation model, not an engineering-accurate plant, process simulation, or source of dimensions.

## Private input identity

- Label: `embodied-ai-data-refinery`
- SHA-256: `d0c4da693c43891d9934072eef079a1aa7391d58fc0771f5369e1985d90c3ce0`

Only the label and digest are committed. The reference image and all browser captures remain in ignored local paths.

## Run locally

Use Node.js 20.19 or newer, then run one command from this directory:

```bash
./scripts/demo-local.sh
```

Drag to orbit, use the wheel or trackpad to zoom, right-drag to pan, and use `重置视角` to restore camera `(9, 7, 11)` with target `(0, 2, 0)`.

## Modeled systems

- Transparent central chamber with deterministic particle motion and pulse
- Three secondary tanks and a procedural metallic pipe network
- Layered refinery base and front processor coils
- Three incoming particle streams
- Three color-coded output platforms with synthetic columns or cylinders

All geometry, materials, and motion are generated in code. No meshes, textures, or art packs are downloaded by the app.

## Test and capture

```bash
npm test
npm run build
npx vite preview --host 127.0.0.1 --port 4173
npm run smoke
```

The smoke script uses `http://127.0.0.1:4173` by default. Override a busy port with `MVP_URL=http://127.0.0.1:4174 npm run smoke`. It checks the canvas, reset control, visible limitation label, zero page errors, and produces one fixed plus two orbit screenshots under the ignored `artifacts/` directory. Capture URLs pin only the procedural animation to 1.25 seconds for reproducible pixels; orbit and reset controls stay active, while ordinary browser sessions continue using live elapsed time.

## Limitations

This reconstruction is based on one image. Occluded and rear geometry, depth, scale, and connectivity are inferred rather than observed. The left-side photo/robot panels, the fourth purple terrain panel, dense micro-particle effects, exact factory topology, physical fluid behavior, dimensions, tolerances, and bill of materials are unsupported. See `docs/acceptance.md` for measured structural, browser, privacy, and visual-comparison evidence.
