# Browser Screenshot Feedback

Use this reference when a procedural Three.js reconstruction has a browser-renderable preview.

## Capture Rule

Each visual build pass should produce at least one rendered screenshot from a named review viewpoint. Use your agent's browser/screenshot tool (Claude Code browser MCP, your agent's in-app browser/preview, or the project's own preview) first. Do not install or download Playwright/Chromium just for this skill; use Playwright or another browser automation path only when the user explicitly allows it or the project already depends on it. If the in-app Browser is unavailable, ask for a screenshot path or use browser tooling that is already present in the target project.

Create a side-by-side review image after capture:

```bash
../../forge/stage4_review/make_comparison_sheet.py \
  --reference reference.png \
  --render render.png \
  --out comparison.png \
  --json
```

The script only aligns and packages evidence. It must not calculate the acceptance score. your agent's vision must inspect `comparison.png`.

---

## Deterministic Viewer for Review (Critical — from Bowie Knife reconstruction)

**The problem:** OrbitControls' per-frame `controls.update()` overrides a scripted `setView`, rendering the object tilted and making screenshots non-deterministic. This breaks comparison integrity.

**Rule:** For review screenshots, the viewer must be deterministic:
- Disable interactive controls before capture: `window.__interactive = false; controls.enabled = false`
- Only enable controls when the user explicitly opts into interaction (e.g., `controls.addEventListener('start', () => { window.__interactive = true; })`)
- In the render loop, only call `controls.update()` when `window.__interactive` is true
- Emulate the exact reference viewport (e.g., 1600×900×1) so the screenshot equals the canvas
- A browser restart resets emulation — be aware of this when comparing across sessions

**Implementation pattern:**
```javascript
const params = new URLSearchParams(location.search);
let spin = params.get('spin') === '1';
window.__interactive = false;
controls.addEventListener('start', () => { window.__interactive = true; });

function loop() {
  requestAnimationFrame(loop);
  if (spin) { model.rotation.y += 0.01; }
  if (window.__interactive) controls.update();
  renderer.render(scene, cam);
  window.__ready = true;
}
```

## Reference Framing Match (Critical — from Bowie Knife reconstruction)

**The problem:** If the render framing (scale/position) doesn't match the reference, Divine Eye scale/aspect hard-gates fail on a framing artifact rather than a real defect. This causes false rejects.

**Rule:** Match framing to the reference, or explicitly account for framing differences:
- Align the model's bounding box to the reference's object extent
- Use a "match" view that scales/positions the camera to approximate the reference framing
- Set the camera position so the object occupies ~98% of the reference width
- If framing cannot match exactly (different aspect ratios, backgrounds), state this in review notes
- Never rely on Divine Eye gates when framing is intentionally different from reference

**Implementation pattern:**
```javascript
const views = {
  front:  [0, 0, 14],
  match:  [0, 0, 6.5],    // framing to match the reference photo extent (~98% width)
  // ...
};
function setView(name) {
  const v = views[name] || views.front;
  cam.up.set(0, 1, 0);
  cam.position.set(v[0], v[1], v[2]);
  cam.lookAt(0, 0, 0);
  controls.target.set(0, 0, 0);
  controls.update();
}
```

Use the same full image pair to score at most five critical semantic features per pass. A feature is a subsystem such as a hull, cabin system, roof system, limb assembly, face, control panel, or sail-and-rigging system. It is not an individual mesh and does not need a separate crop. Score up to three uncertain important features only when adaptive escalation is useful.

The starter spec contains generic review targets only as placeholders. Replace them with object-specific systems discovered during pre-spec assessment; otherwise strict quality validation should not pass a moderate or complex object.

## Compare By Layer

Review screenshot evidence in this order:

1. Silhouette and proportions: bounding shape, width/height/depth cues, taper, symmetry, negative space.
2. Component structure: parent/child placement, joints, contact points, repeated systems, floating or detached parts.
3. Form detail: bevels, chamfers, curvature, bends, dents, seams, raised ridges, holes, deformation scale.
4. Surface response: albedo zones, roughness variation, metalness, clearcoat, transmission, normal/bump/displacement, ambient occlusion.
5. Local features: scratches, chips, dirt accumulation, moss, stains, color patches, edge wear, contact wear.
6. Lighting/camera: exposure, shadow softness, contact shadows, color temperature, rim light, reflection readability.
7. Performance tradeoff: whether missing detail is intentional because of triangle, draw call, texture, or FPS budgets.

## Decision Matrix

- If the screenshot reveals a missing or wrong component, choose `refine-spec`.
- If the spec describes the component but the render does not match it, choose `refine-code`.
- If the screenshot is too dark, too close, too far, or from the wrong viewpoint, choose `refine-code` for camera/lighting before judging model fidelity.
- If the source image does not reveal enough geometry or material information, choose `request-input`.
- If the screenshot matches the pass acceptance criteria and does not hide future risk, choose `continue`.

`continue` is allowed only when the global AI vision score meets `selfCorrectLoop.visualAcceptance.threshold`, normally `0.7`, and every critical semantic feature meets its own threshold. A numeric or pixel-difference script may help diagnose alignment, but it cannot approve the pass.

## AI Vision Scorecard

Score each applicable layer from `0` to `1`, then assign one overall score based on the pass goal:

- `silhouetteProportion`: outer contour, mass distribution, negative space, camera-normalized proportions.
- `componentStructure`: hierarchy, placement, attachment, repeated systems, floating or disconnected parts.
- `formDetail`: taper, bend, bevel, deformation, secondary forms, local geometry.
- `materialSurface`: albedo, roughness, reflectance, normal/displacement, AO, local wear, tactile frequency.
- `lightingCamera`: camera match, exposure, key/fill/rim balance, shadow/contact response, background.

Do not hide a critical failed layer inside a high average. If a layer is essential to the current pass and remains visibly wrong, choose `refine-spec` or `refine-code` even when the arithmetic mean is above threshold.

## Feature Tiers

- `critical`: identity-defining, user-prioritized, visually salient, or high-risk subsystem. Must be visible in the full pair and pass independently.
- `important`: useful secondary subsystem. Review only suspicious items; the reviewed average must meet the configured threshold.
- `detail`: micro detail. Record mismatch notes and defer to refinement unless the user promotes it.

Repeated parts should be one target when they form one recognizable system. For example, review three cabins as `cabin-system`, not three separate cabin targets.

## Evidence Format

Record screenshot evidence with:

- `referenceScreenshot`: source image, crop, or marked-up reference path.
- `renderScreenshot`: browser-rendered screenshot path.
- `comparisonImage`: side-by-side evidence image reviewed by AI vision.
- `cameraView`: named viewpoint such as `front`, `three-quarter`, `side`, `top`, or `close-up-material`.
- `notes`: concise mismatch summary using 3D graphics terms.
- `aiVisionScore`: overall score from `0` to `1`.
- `layerScores`: per-layer scores from the scorecard.
- `aiVisionNotes`: concrete matched features, mismatches, root causes, and next correction.
- `featureReviews`: feature ID, score, visibility in the shared pair, and focused notes.

Never use screenshots as decoration only. They are the ground truth for the self-correction loop.
