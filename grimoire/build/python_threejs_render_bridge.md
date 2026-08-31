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
background-only, clipped, stale, unreadable, or incorrectly sized files. When a subject contract
requires six full-body angles, head close-ups do not count toward the six: capture fixed, `±35°`,
profile, rear three-quarter and rear full-body views, then capture head views separately.

The default manifest stores orbit cameras as `azimuthDegrees`, `elevationDegrees` and `target`.
For paired GLB/model evidence, also author an explicit shared `distance` and `fovDegrees`; deriving
distance independently from each subject's auto-frame produces different cameras even when the
azimuths match. Treat `near` and `far` as shared camera fields too. A reference-only camera override
is forbidden: the one capture record is the authority for both routes.

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

See also: `integrations/glb_character_pipeline/` for an opt-in, GLB-baseline reconstruction pipeline
(SDF point-cloud splat + Surface Nets) that produces the procedural TypeScript/three.js character this
bridge captures passes from — distinct from this doc's own manifest/pass-recording contract.

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
  async setCamera({ azimuthDegrees, elevationDegrees, target, distance, fovDegrees, near, far }) {
    // Apply the camera and controls to the real Three.js scene, then resolve.
  },
  async capturePass({ passId, mode }) {
    // Select beauty/diagnostic render target, settle, and return { ok: true, selector: 'canvas' }.
  },
};
```

If the ready signal, capture contract, canvas, screenshot, or hash check fails, the adapter stops.
It does not fall back to a Python/Blender image and does not claim that a render happened.

For an asynchronously loaded multipart GLB, install the capture API immediately but enumerate its
meshes and allocate anonymous ID materials only after the route's `ready` promise resolves. Capture
must fail if the post-ready mesh count differs from the probed contract. Enumerating an empty group
at bridge installation time can otherwise yield a plausible beauty image and empty semantic-ID
passes. Anonymous object IDs prove physical boundaries only; generic `model.*` names never become
garment or anatomy labels.

## Prohibited shortcuts

- Do not use Python to claim it rendered Three.js when it rendered Blender instead.
- Do not copy GLB/VRM topology, vertices, or imported materials into a code-only factory.
- Do not compare a procedural screenshot directly to an unrendered GLB; the GLB must first produce
  a fresh baseline screenshot through the same Three.js runtime.
- Do not compare orbit images to a reference angle that was never supplied.
- Do not infer visual success from TypeScript compilation, `__READY__`, or an inline preview.
- Do not add Playwright/Chromium dependencies to the zero-install core without explicit scope.
