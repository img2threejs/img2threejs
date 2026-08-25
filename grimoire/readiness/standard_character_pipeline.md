# Standard character pipeline contract — 1.5 beta + 1.5 alpha

Beta owns the quality-gated sculpt/build/review pipeline. Alpha owns the Python manifest,
browser capture evidence and UniRig-shaped rig payload boundary. External neural/GLB/VRM
systems are adapters, never silent replacements.

## Route selection

```text
code-only:
  reference → spec → procedural TypeScript factory → authored/validated rig → Three.js

external-asset (opt-in):
  reference → CharacterGen/Tripo/etc. → GLB/VRM → UniRig/adapter → Three.js

glb-mediated-reference (opt-in, new):
  image (optional) → user/adapter-produced GLB → GLB probe → Three.js GLB baseline
  → procedural TypeScript factory → same Three.js camera batch → comparison
```

The external route is valid for proving character-generation capability, but its artifact must
not be reported as a procedural factory. The GLB-mediated route uses the imported GLB as a
structural reference and browser-rendered baseline only; it still generates a separate procedural
TypeScript factory. Both routes share the same browser capture, diagnostic and reference-loop gates.

## Required artifacts

| Artifact | Owner | Required content |
| --- | --- | --- |
| `reference-set.json` | intake | admitted views, paths, hashes, dimensions, camera role, hidden-region confidence |
| `glb-reference.json` | GLB intake | GLB hash, scene/mesh/material/skin inventory, bounds, warnings, provenance |
| `assessment.json` | intake/spec | character classification, complexity, anatomy, landmarks, local evidence refs |
| `character-sculpt-spec.json` | spec | hierarchy, topology class, materials, feature targets, camera and quality contract |
| `rig-payload.json` | rig | Y-up/right-handed coordinates, joints, parents, names, local matrices, packed skin weights |
| `render-manifest.json` | alpha bridge | runtime URL, viewport/DPR, camera batch, reference/output hashes, readiness evidence |
| `comparison-sheet.png` | review | fixed reference beside current browser render |
| `diagnose-render.json` | review | tier-1 and multi-angle deterministic diagnostics plus next action |

For a GLB-mediated reference, `render-manifest.json` additionally stores a `reference.kind` of
`glb` and a browser-rendered baseline capture for every camera used in the procedural batch.
The raw GLB is never passed to pixel comparison as if it were an image.

For the 1.5 alpha GLB-mediated v2 track, the manifest may declare
`fidelityTrack: "glb-mediated-v2"` plus a validated `renderProfile` shared by both routes.
Each camera record then owns six paired browser passes: `beauty`, `alpha-silhouette`,
`semantic-id`, `depth`, `normal`, and `roughness-material-id`. Per-region scores are blocked
until the semantic-ID pass is readable and has declared region colors. A merged single-node
GLB is recorded as semantically insufficient; region-growing or curvature partitions are
hypotheses, not labels.

## Character scene contract

- Root is a named `THREE.Group` with `root.userData.sculptRuntime`.
- Every identity-defining part has a stable name, material ID and optional anchor/socket.
- Body, head, hair, outfit, armor, weapon and accessories are separate semantic modules.
- Face geometry is one continuous head/face volume wherever possible. Eyes, nose and mouth
  should use controlled surface/color/material regions or landmark-derived attached forms;
  avoid arbitrary floating primitives.
- Stylized hair uses a mass plus directional clumps with root/tip, side/rear coverage and
  overlap checks. A flat billboard is not an acceptable character hair solution.

## Coordinate, unit and rig contract

- Internal procedural payloads are Y-up, right-handed and finite.
- Root joint is index `0` with `parent = null`; every later parent index is less than the
  child index. BFS/DFS is an authoring choice, not the runtime invariant.
- WebGL skinning uses four packed influence slots per vertex with finite, nonnegative weights
  normalized to one. The validator reports joints with no active weights.
- When exporting external VRM, convert to meter units, right-handed Y-up, model facing `-Z`,
  humanoid mapping and T-pose. Record conversion rather than hiding it in loader code.
- Structural rig validation is not deformation proof. Neutral, shoulder, elbow, wrist, knee,
  foot and accessory stress poses still require runtime evidence.

## Browser capture contract

The target showcase route must expose:

```js
window.__IMG2THREEJS_READY__ = true;
window.__IMG2THREEJS_CAPTURE__ = {
  setCamera(cameraSpec) { /* apply camera, target, near/far, exposure */ },
  setReferenceMode({ kind, url }) { /* load GLB reference and swap the visible subject */ },
  capturePass({ passId, mode }) { /* select pass and resolve when pixels are ready */ }
};
```

For a GLB-mediated run, the route must also provide an explicit reference mode, for example
`#/character-demo?reference=glb`, that loads the local GLB with `GLTFLoader`. The capture manifest
records whether each PNG is the `reference` baseline or the `procedural` render. Both must use the
same camera, viewport, DPR, tone mapping, exposure and background.
For v2, `capturePass` must return `{ok: true, selector?: "canvas"}` or a comparable
target selector so the adapter saves the actual pass canvas rather than surrounding UI.

The capture adapter waits for readiness, rejects fatal console errors and zero-sized canvases,
sets the camera, waits for settled frames, saves PNGs inside the workspace, reopens them with an
image-capable reader, and records hashes in the manifest. Python never renders a replacement scene.

For paired GLB/model batches, each capture record owns one complete camera specification shared by
both routes: azimuth, elevation, target, distance, FOV, near and far. Do not auto-frame the GLB and
procedural model separately. If the GLB loads asynchronously, enumerate its multipart meshes and
build diagnostic ID materials after readiness, then compare the executed count to the intake probe.

## Required character capture batch

```text
hero/reference-match
orbit +35°
orbit -35°
profile 90°
rear three-quarter 135°
rear 180°
head hero
head three-quarter
```

This is six full-body angles plus two head controls. A head crop cannot substitute for a missing
full-body azimuth when the reconstruction contract requires six whole-character views.

Only the fixed view is pixel/feature aligned to the supplied reference. Orbit views are judged
for volume, attachment, rear coverage, hair continuity, deformation and non-degenerate form.

## Acceptance order

1. reference admission and image/GLB readback;
2. for GLB-mediated runs, GLB probe plus fresh browser baseline captures;
3. character spec and strict-quality validation;
4. blockout silhouette and proportion lock;
5. face landmarks and unified head volume;
6. hair/outfit/material modules;
7. rig payload validation and runtime pose stress;
8. browser readiness and fresh screenshot batch;
9. tier-1 diagnostics, side-by-side sheet and semantic/per-feature review;
10. multi-angle/attachment/self-intersection gates;
11. exactly one next action: `continue`, `refine-spec`, `refine-code`, `request-input` or `stop`.

The v2 correction order is one group per loop: camera, silhouette, face, clothing, accessory,
materials, then lighting. Changing multiple groups in one loop invalidates attribution of the
comparison result.

Confidence above 9/10 is a visual acceptance target, not something inferred from tests,
generated code, GLB export or a runtime-ready flag.

## Migration map

| Area | Beta contribution | Alpha contribution | Merged standard |
| --- | --- | --- | --- |
| intake/spec/build/review | strict-quality, geometry/material and review gates | character/rig research references | beta gates remain authoritative; alpha research feeds spec fields |
| rigging | shared skeleton and pose/deformation tests | UniRig payload schema and validator | validate payload, then run real Three.js stress poses |
| rendering | browser/runtime smoke infrastructure | Python manifest, camera batch, hash and diagnosis bridge | browser is renderer; Python is evidence controller |
| external generation | optional asset handoffs | CharacterGen/UniRig research boundary | explicit adapter only, with provenance and no code-only claim |
| visual acceptance | comparison/review gates | screenshot readback and multi-angle manifest | all screenshot and diagnostic gates are mandatory |
