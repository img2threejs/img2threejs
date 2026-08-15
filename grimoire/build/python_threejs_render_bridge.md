# Python ↔ Three.js render bridge skill

Use this skill when Python is involved in rendering, inspecting, or batch-capturing a
procedural Three.js character, including the explicit GLB-mediated reference route.

## Contract

Python is the deterministic controller and evidence producer. The browser-hosted Three.js
runtime is the rendering authority. A Python image, Blender image, notebook preview, or GLB
export is not a substitute for a screenshot of the target Three.js route.

## Routing

1. Existing Chrome DevTools MCP available: use it first for runtime capture.
2. Existing project Playwright dependency or explicit user permission: use Playwright as a
   cross-browser/headless fallback.
3. BlenderProc or Blender Python: use only for optional reference passes, synthetic data,
   preprocessing, or an explicitly requested external asset route.
4. pythreejs: use for notebook-side parameter exploration; never use it as the final fidelity
   gate unless the target itself is a pythreejs application.

## GLB-mediated reference route

When the user provides a GLB produced from an image, the GLB is an intermediate reference
mesh, not the procedural output. The required order is:

```text
image (optional provenance)
        -> user/adapter-produced GLB
        -> probe_glb.py + hash/provenance
        -> render the GLB through the target Three.js browser route
        -> reference baseline captures
        -> derive a procedural ObjectSculptSpec
        -> generate TypeScript geometry/material/rig
        -> render the procedural route with the same camera batch
        -> compare procedural captures against GLB baseline captures
```

Initialize this route with:

```bash
python3 forge/stage1_intake/probe_glb.py /path/to/reference.glb
python3 forge/stage4_review/render_bridge.py init \
  --reference-glb /path/to/reference.glb \
  --reference-browser-url /references/reference.glb \
  --runtime-url http://127.0.0.1:5173/#/character-demo \
  --out work/character/render-manifest.json

python3 scripts/capture_threejs_playwright.py \
  --manifest work/character/render-manifest.json --mode reference
```

The browser route must expose an explicit reference mode that loads the local GLB with
`GLTFLoader`, applies the same renderer/camera/lighting contract, and captures the GLB baseline.
The optional Playwright adapter uses `setReferenceMode({kind: "glb", url})` and records those
images with `render_bridge.py record-reference` semantics. Chrome DevTools MCP integrations must
provide the same mode switch and write to the same manifest fields. Only then may
`render_bridge.py diagnose` compare the procedural hero against the baseline.

This route measures agreement with the intermediate GLB. If the original image is not retained,
it does not prove agreement with the image that produced the GLB; hidden geometry, materials,
and generation artifacts must be recorded as confidence/approximation notes.

## Capture invariant

Every batch must preserve:

```text
reference hash + reference kind + runtime URL + viewport/DPR + camera transform + renderer settings
  + ready evidence + screenshot hash + diagnostics + visual decision
```

Before accepting a render, reopen the saved screenshot with an image-capable tool. Reject
background-only, clipped, stale, unreadable, or incorrectly sized files. Capture fixed,
`±35°`, profile, rear, and head close-up views for a character.

## GLB-mediated v2 passes

Pass `--render-profile docs/specs/render-profile.v2.example.json` to `render_bridge.py init`
to opt into the v2 fidelity track. The profile is validated once and hashed into the manifest;
both the GLB reference mode and procedural mode must use it. Record each browser-produced pass
with:

```bash
python3 forge/stage4_review/render_bridge.py record-pass \
  --manifest work/character/render-manifest.json \
  --capture-id hero --pass-id semantic-id --image work/character/hero/semantic-id.png
```

Add `--reference` for the GLB baseline pass. The required pass IDs are `beauty`,
`alpha-silhouette`, `semantic-id`, `depth`, `normal`, and `roughness-material-id`.
After all paired passes exist, run:

```bash
python3 forge/stage4_review/compare_region_passes.py \
  --manifest work/character/render-manifest.json --capture-id hero \
  --out work/character/hero-pass-comparison.json
```

The comparison blocks per-region claims when the semantic-ID pass or region colors are absent.
It is a deterministic diagnostic, not an AI likeness score.

## Failure routing

- no browser/MCP: `request-input` and ask for installation/authentication;
- runtime not ready or console error: `refine-code`/runtime repair;
- background-only close-up: repair near/far, target, settle frames, and recapture;
- degenerate orbit: `refine-code` geometry, not a material tweak;
- diagnostics pass but semantic identity fails: `refine-code` and list the failed feature;
- repeated plateau: `refine-spec` or `request-input` for more views;
- never report confidence >9 without readable head/profile evidence.

## Minimal manifest fields

`schemaVersion`, `reference.kind`, `reference.path`, `reference.sha256`, `runtime.url`, `runtime.viewport`,
`runtime.devicePixelRatio`, `runtime.renderer`, `captures[]`, each capture's camera transform,
`readySignal`, screenshot path/hash, diagnostics paths, and final decision.

## Executable alpha path

The 1.5-alpha branch includes a dependency-free manifest controller and an optional Playwright
adapter. From the repository root:

```bash
python3 forge/stage4_review/render_bridge.py init \
  --reference /path/to/reference.png \
  --runtime-url http://127.0.0.1:5173/#/character-demo \
  --out work/character/render-manifest.json

# Use Chrome DevTools MCP or the optional adapter to produce the actual browser PNGs.
python3 scripts/capture_threejs_playwright.py \
  --manifest work/character/render-manifest.json

python3 forge/stage4_review/render_bridge.py validate \
  --manifest work/character/render-manifest.json --require-complete

python3 forge/stage4_review/render_bridge.py diagnose \
  --manifest work/character/render-manifest.json \
  --out work/character/diagnostics.json
```

The Playwright adapter is intentionally optional. Install it outside the stdlib core only when
needed: `python3 -m pip install playwright` followed by `playwright install chromium`. The target
route must expose this small browser-side contract:

```js
window.__IMG2THREEJS_READY__ = true;
window.__IMG2THREEJS_CAPTURE__ = {
  async setCamera({ azimuthDegrees, elevationDegrees, target, near, far }) {
    // Apply the camera and controls to the real Three.js scene, then resolve.
  },
  async getEvidenceSnapshot({ captureId, sessionNonce }) {
    // Required for adaptive hard-gate captures. Read these values from the
    // live scene/camera after setCamera has settled; do not echo the plan.
    return {
      captureId,
      sessionNonce,
      sceneBuildSha256: computeStableSceneBuildSha256(),
      objectCount: scene.children.length,
      camera: {
        direction: actualCameraDirectionFromTarget(),
        matrixWorld: camera.matrixWorld.toArray(),
        projectionMatrix: camera.projectionMatrix.toArray(),
      },
    };
  },
  async capturePass({ passId, mode }) {
    // Select beauty/diagnostic render target, settle, and return { ok: true, selector: 'canvas' }.
  },
};
```

For an adaptive capture the ready value must be strict boolean `true`; the adapter injects an
unpredictable browser-session nonce before navigation and requires `getEvidenceSnapshot` to echo it
with the capture ID. It captures the actual WebGL `canvas`, records document/scene/camera/browser
provenance, and hashes both PNG bytes and decoded RGBA pixels. If the ready signal, capture contract,
canvas, screenshot, provenance, or hash check fails, the adapter stops.
The receipt is a trusted local Playwright-runner attestation, not cryptographic or remote
attestation. The generic `render_bridge.py record` CLI cannot mint it from a hand-written PNG.
Viewport, browser DPR, canvas CSS/backing size, WebGL drawing buffer, and screenshot dimensions must
form one consistent chain.
It does not fall back to a Python/Blender image and does not claim that a render happened.

## Adaptive hostile camera loop

The fixed camera batch is the minimum evidence set. For models that must hold up from arbitrary
angles, `adaptive_harsh_critic.py` adds a bounded adaptive layer without replacing that baseline:

```text
six cube-map root nextViews
  -> render_bridge.py schedule-adaptive
  -> browser setCamera + real scene PNG
  -> adaptive_harsh_critic.py request (re-hash pixels + fixed turntable gate)
  -> separate critic agent (critic.id != creator.id)
  -> adaptive_harsh_critic.py advance
  -> mandatory uniform split to level 1 (24 more views)
  -> then defective cells split into four nextViews
  -> repeat until pass or bounded stop
```

`schedule-adaptive` only creates pending capture records. The existing browser adapter can capture
the generated IDs because they use the same `azimuthDegrees`, `elevationDegrees`, target, near and
far camera contract as static views. For these IDs, the generic `render_bridge.py record` command is
not an evidence path: only `scripts/capture_threejs_playwright.py` can mint the required browser
receipt. Scheduling rejects non-canonical session/view/cell geometry and unsafe output paths; every
capture/reference/pass path must resolve beneath the manifest evidence root, and the manifest update
is transactional. Different directions with the same decoded pixels or camera matrix fail closed. Every
critique and finding is bound to the resulting PNG hash, decoded-pixel hash, browser-receipt hash and
exact direction. Never hand an agent only the state JSON and call that visual review.
One-pixel and global/structured ±1/2/3-LSB perturbations are also rejected by alpha-composited
low-frequency visible RGB plus demeaned-luma structure; pHash and mean visible color constrain the
wider structural envelope. Chroma is retained so visibly
different isoluminant colors remain admissible, while hidden RGB under alpha=0 cannot fake a new
frame. Random session-scoped IDs and paths prevent a fresh init from reusing stale capture names.
The first `sceneBuildSha256`, the current source reference hash, optional per-view reference hashes,
and all prior round evidence remain in the state ledger and are revalidated before every request and
advance. GLB adaptive views require their paired browser-rendered reference capture.
The default clean floor is 30 captures (6 roots + 24 level-1 cells); a max-view cap below that blocks.
All rounds in one adaptive session observe one immutable scene build. After a code correction,
create a new manifest and critic state and recapture the fixed baseline as well as adaptive views;
never append corrected-build pixels to the previous session's evidence.

Full critic and convergence contract: `grimoire/review/adaptive_harsh_critic.md`.

## Prohibited shortcuts

- Do not use Python to claim it rendered Three.js when it rendered Blender instead.
- Do not copy GLB/VRM topology, vertices, or imported materials into a code-only factory.
- Do not compare a procedural screenshot directly to an unrendered GLB; the GLB must first produce
  a fresh baseline screenshot through the same Three.js runtime.
- Do not compare orbit images to a reference angle that was never supplied.
- Do not infer visual success from TypeScript compilation, `__READY__`, or an inline preview.
- Do not add Playwright/Chromium dependencies to the zero-install core without explicit scope.
