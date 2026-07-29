# GLB Export Capability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a native, decoupled browser module that exports any img2threejs procedural model to a `.glb` file from the viewer, so it can be imported into Blender.

**Architecture:** A single dependency-free ES module `export/glbExporter.js` wraps three's `GLTFExporter`. It exports a sanitized clone of the model (stripping circular `sculptRuntime` userData and neutralizing the animated root transform) and provides `attachExportButton(root, options)` to add a floating "Exportar .glb" button to any viewer. Documentation makes it a standard step of the pipeline's output.

**Tech Stack:** JavaScript (browser ES modules), three.js r0.164.1 + `three/addons/exporters/GLTFExporter.js`, resolved via the viewer's importmap. Verification uses a static HTTP server + browser automation (playwright).

## Global Constraints

- Target three.js version: `0.164.1` (matches the viewer importmap `three` / `three/addons/`). Copied verbatim from the master-sword viewer.
- The module MUST be a browser ES module with **no build step and no npm install** — bare imports (`three`, `three/addons/…`) are resolved by the consuming page's importmap.
- The module MUST NOT depend on any `forge/` script, the sculpt spec, or a specific viewer — it operates purely on a passed `THREE.Object3D`.
- Default export format: **binary `.glb`** (single file, textures embedded).
- The live scene MUST be left untouched after an export (userData and transforms restored).
- Public API names are fixed: `exportModelToGLB(object3D, options)` and `attachExportButton(object3D, options)`.
- Repo files only for the upstream PR: `export/glbExporter.js`, `grimoire/export/glb_export.md`, `SKILL.md`, `README.md`, `CHANGELOG.md`, `ROADMAP.md`, `PR_BODY.md`. The `master-sword/` viewer is the user's project (outside the repo) and is used for verification, not shipped in the PR.
- Scratchpad for throwaway files: `C:\pginstall\claude\C--Users-Juan-Cam-s-Documents-CreacionOBJ3D\0474ec03-c7e5-4edd-8926-26ce243f6234\scratchpad`.

---

### Task 1: The export module (`export/glbExporter.js`)

**Files:**
- Create: `export/glbExporter.js`
- Test: syntax gate via a scratchpad `.mjs` copy + exported-symbol grep (no JS test runner exists in this repo; real execution is verified in Task 2).

**Interfaces:**
- Consumes: `GLTFExporter` from `three/addons/exporters/GLTFExporter.js` (bare specifier, resolved by the page importmap).
- Produces:
  - `exportModelToGLB(object3D: THREE.Object3D, options?: { binary?: boolean, resetRootTransform?: boolean, includeSculptUserData?: boolean }) => Promise<Blob>`
  - `attachExportButton(object3D: THREE.Object3D, options?: { filename?: string, label?: string, onExported?: (blob: Blob) => void, binary?: boolean, resetRootTransform?: boolean, includeSculptUserData?: boolean }) => { button: HTMLButtonElement, detach: () => void }`

- [ ] **Step 1: Write the module**

Create `export/glbExporter.js` with exactly this content:

```js
// export/glbExporter.js
// Decoupled GLB export for img2threejs procedural models.
// Runs in the browser using three's GLTFExporter. No pipeline dependency.
//
// Usage:
//   import { attachExportButton } from '<path>/export/glbExporter.js';
//   attachExportButton(root, { filename: 'master-sword' });

import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';

/**
 * Export a THREE.Object3D to a GLB (or glTF) Blob.
 *
 * Exports a sanitized clone so the live scene keeps its runtime userData and
 * animated transform untouched, and the file carries no circular/bloated extras.
 *
 * @param {import('three').Object3D} object3D
 * @param {Object} [options]
 * @param {boolean} [options.binary=true]              // true -> .glb, false -> .gltf JSON
 * @param {boolean} [options.resetRootTransform=true]  // export in canonical pose
 * @param {boolean} [options.includeSculptUserData=false]
 * @returns {Promise<Blob>}
 */
export async function exportModelToGLB(object3D, options = {}) {
  const {
    binary = true,
    resetRootTransform = true,
    includeSculptUserData = false,
  } = options;

  if (!object3D || typeof object3D.traverse !== 'function') {
    throw new TypeError('exportModelToGLB: expected a THREE.Object3D');
  }

  // 1. Stash userData across the subtree. THREE.Object3D.clone() deep-copies
  //    userData via JSON.stringify, which throws on the circular Object3D graph
  //    stored at root.userData.sculptRuntime. Stripping it first keeps clone
  //    safe and keeps per-node sculpt-spec blobs out of the exported extras.
  const stash = new Map();
  object3D.traverse((child) => {
    stash.set(child, child.userData);
    child.userData = includeSculptUserData ? toJsonSafe(child.userData) : {};
  });

  let clone;
  try {
    clone = object3D.clone(true); // safe now: userData is JSON-serializable
  } finally {
    // 2. Restore the live scene immediately (keeps runtime data + spin intact).
    for (const [child, data] of stash) child.userData = data;
  }

  // 3. Export in canonical pose so the viewer's animated spin/placement is not baked in.
  if (resetRootTransform) {
    clone.position.set(0, 0, 0);
    clone.quaternion.set(0, 0, 0, 1);
    clone.scale.set(1, 1, 1);
    clone.updateMatrixWorld(true);
  }

  // 4. Serialize with GLTFExporter (standard PBR + CanvasTexture export cleanly).
  const exporter = new GLTFExporter();
  const result = await exporter.parseAsync(clone, { binary, onlyVisible: true });

  return binary
    ? new Blob([result], { type: 'model/gltf-binary' })
    : new Blob([JSON.stringify(result)], { type: 'model/gltf+json' });
}

/**
 * Attach a floating "Exportar .glb" button that exports `object3D` on click.
 *
 * @param {import('three').Object3D} object3D
 * @param {Object} [options] filename/label/onExported plus exportModelToGLB options.
 * @returns {{ button: HTMLButtonElement, detach: () => void }}
 */
export function attachExportButton(object3D, options = {}) {
  const {
    filename = 'model',
    label = 'Exportar .glb',
    onExported,
    ...exportOptions
  } = options;

  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  Object.assign(button.style, {
    position: 'fixed', right: '14px', top: '14px', zIndex: '9999',
    font: '13px/1 system-ui, sans-serif', padding: '9px 14px',
    color: '#eaf6fa', background: '#1b2740', border: '1px solid #3a4a6b',
    borderRadius: '8px', cursor: 'pointer', boxShadow: '0 2px 8px #0008',
  });

  async function run() {
    if (button.disabled) return;
    button.disabled = true;
    button.textContent = 'Exportando…';
    try {
      const ext = exportOptions.binary === false ? 'gltf' : 'glb';
      const blob = await exportModelToGLB(object3D, exportOptions);
      downloadBlob(blob, `${filename}.${ext}`);
      console.log('[glbExporter] .glb ready:', blob.size, 'bytes');
      button.textContent = '✓ Exportado';
      if (typeof onExported === 'function') onExported(blob);
    } catch (error) {
      console.error('[glbExporter] export failed:', error);
      button.textContent = '⚠ Error';
    } finally {
      setTimeout(() => { button.textContent = label; button.disabled = false; }, 1600);
    }
  }

  button.addEventListener('click', run);
  document.body.appendChild(button);

  return {
    button,
    detach() { button.removeEventListener('click', run); button.remove(); },
  };
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function toJsonSafe(userData) {
  try {
    return JSON.parse(JSON.stringify(userData));
  } catch {
    return {}; // drop anything with circular refs (e.g. the sculptRuntime node graph)
  }
}
```

- [ ] **Step 2: Syntax gate (parse as ESM)**

`node --check` treats a lone `.js` as CommonJS and would reject `import`. Copy to a `.mjs` in the scratchpad and check there (syntax-only; bare imports are not resolved):

Run:
```bash
SCRATCH="C:/pginstall/claude/C--Users-Juan-Cam-s-Documents-CreacionOBJ3D/0474ec03-c7e5-4edd-8926-26ce243f6234/scratchpad"
cp export/glbExporter.js "$SCRATCH/glbExporter.mjs" && node --check "$SCRATCH/glbExporter.mjs" && echo "SYNTAX OK"
```
Expected: prints `SYNTAX OK` with no parse errors.

- [ ] **Step 3: Verify the public API symbols exist**

Run:
```bash
grep -nE 'export (async )?function (exportModelToGLB|attachExportButton)' export/glbExporter.js
```
Expected: two matching lines (`exportModelToGLB` and `attachExportButton`).

- [ ] **Step 4: Commit**

```bash
git add export/glbExporter.js
git commit -m "feat: add decoupled GLB exporter module for viewers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Wire into the master-sword viewer and verify a valid GLB

**Files:**
- Modify: `../master-sword/index.html` (the user's project, outside the repo — real feature wiring + a test hook; NOT part of the PR).

**Interfaces:**
- Consumes: `attachExportButton` and `exportModelToGLB` from `export/glbExporter.js` (Task 1).
- Produces: a wired viewer plus `window.__swordRoot` (the model root) for automated structure validation.

- [ ] **Step 1: Add the import to the master-sword module**

In `../master-sword/index.html`, add this import at the top of the `<script type="module">` block, right after the existing `import { RoomEnvironment } ...` line (around line 34):

```js
import { attachExportButton } from '../img2threejs-repo/export/glbExporter.js';
```

- [ ] **Step 2: Wire the button and the test hook**

In the same file, immediately after `scene.add(sword);` (around line 421), add:

```js
    attachExportButton(sword, { filename: 'master-sword' });
    window.__swordRoot = sword; // verification hook (local demo only)
```

- [ ] **Step 3: Start a static server at the CreacionOBJ3D root**

The viewer uses ES modules, which need http(s), not `file://`. Serve the parent of both folders so `../img2threejs-repo/...` resolves.

Run (background):
```bash
python -m http.server 8765 --directory "C:/Users/Juan Camús/Documents/CreacionOBJ3D"
```
Expected: server listening on port 8765.

- [ ] **Step 4: Load the viewer and assert the button renders (red → green)**

Use browser automation (playwright-cli skill) to navigate to:
```
http://localhost:8765/master-sword/index.html
```
Assert: no uncaught page/console errors, and a `<button>` with text `Exportar .glb` is present. (Before Task 1 existed, the module import would 404 and the button would be absent — this is the failing-first state.)

- [ ] **Step 5: Validate a real GLB in-page**

Run this via the browser automation `evaluate` against the loaded page and capture the returned object:

```js
async () => {
  const { exportModelToGLB } = await import('/img2threejs-repo/export/glbExporter.js');
  const blob = await exportModelToGLB(window.__swordRoot, { binary: true });
  const buf = new Uint8Array(await blob.arrayBuffer());
  const dv = new DataView(buf.buffer);
  const magic = String.fromCharCode(buf[0], buf[1], buf[2], buf[3]);
  const version = dv.getUint32(4, true);
  const total = dv.getUint32(8, true);
  const jsonLen = dv.getUint32(12, true);
  const jsonType = dv.getUint32(16, true); // 0x4E4F534A === 'JSON'
  const gltf = JSON.parse(new TextDecoder().decode(buf.slice(20, 20 + jsonLen)));
  return {
    magic, version, total, bytes: buf.length, jsonType,
    meshes: (gltf.meshes || []).length,
    materials: (gltf.materials || []).length,
    nodes: (gltf.nodes || []).length,
    namedNodes: (gltf.nodes || []).filter((n) => n.name).map((n) => n.name),
  };
}
```

Expected assertions (all must hold):
- `magic === 'glTF'`
- `version === 2`
- `jsonType === 0x4E4F534A` (1313821514)
- `total === bytes`
- `meshes > 0`, `materials > 0`, `nodes > 0`
- `namedNodes` contains at least some of: `blade`, `guard`, `grip`, `pommel`

- [ ] **Step 6: Click-path smoke check**

Click the `Exportar .glb` button and confirm the console logs a line matching `[glbExporter] .glb ready: <N> bytes` with `N > 0` and no `export failed` error is logged.

- [ ] **Step 7: Stop the server**

Stop the background `http.server` process. No repo commit for this task (the modified file is outside the repo); the deliverable is the passing verification above. Record the returned validation object as evidence.

---

### Task 3: Documentation — make export a native pipeline step

**Files:**
- Create: `grimoire/export/glb_export.md`
- Modify: `SKILL.md` (the `## Output` section, near line 130)
- Modify: `README.md` (the `## What you get` list, near line 178)
- Modify: `CHANGELOG.md` (add an `## [Unreleased]` section after line 7)
- Modify: `ROADMAP.md` (the v1.4 details paragraph, line 31)

**Interfaces:**
- Consumes: the `attachExportButton` API from Task 1.
- Produces: no code; documentation only.

- [ ] **Step 1: Write the grimoire doc**

Create `grimoire/export/glb_export.md` with exactly this content:

````markdown
# GLB export (Blender-ready)

img2threejs models are code that builds a `THREE.Group` in the browser. To hand a
model to Blender (or Godot, Unity, or any glTF tool), export it to `.glb` from the
viewer with the decoupled module `export/glbExporter.js` — no build step, no pipeline
dependency.

## Wire it into any viewer

Add one import and one call. Adjust the relative path to wherever `glbExporter.js`
is served relative to your viewer page:

```js
import { attachExportButton } from '<path>/export/glbExporter.js';

// `root` is the THREE.Group returned by your create<Name>Model factory:
attachExportButton(root, { filename: 'my-model' });
```

This adds a floating "Exportar .glb" button. `GLTFExporter` (from `three/addons`) must
be reachable through the page importmap (`"three/addons/"`), which the standard viewer
already provides.

## API

- `exportModelToGLB(object3D, options?) => Promise<Blob>` — pure logic, returns the file
  as a Blob. Options: `binary` (default `true`, `.glb`; `false` gives `.gltf` JSON),
  `resetRootTransform` (default `true`), `includeSculptUserData` (default `false`).
- `attachExportButton(object3D, options?) => { button, detach }` — the button plus a
  `detach()` cleanup. Accepts `filename`, `label`, `onExported(blob)`, and every
  `exportModelToGLB` option.

## What it does (and why)

- **Strips runtime userData.** `root.userData.sculptRuntime` holds a circular graph of
  `Object3D` references. The exporter serializes a sanitized clone so cloning does not
  throw and the file is not bloated with per-node sculpt-spec blobs. Node `.name` values
  (`blade`, `guard`, …) are preserved, so the named hierarchy survives into Blender.
- **Exports in canonical pose.** The viewer animates the model's spin; `resetRootTransform`
  neutralizes the root transform on the clone so the animation is not baked in.
- **Model only, not lights.** Only the model group is exported; Blender lights the scene
  itself.

## Materials

All materials are standard PBR (`MeshStandardMaterial` / `MeshPhysicalMaterial`) plus
`CanvasTexture`, which `GLTFExporter` exports fully: clearcoat (`KHR_materials_clearcoat`),
emissive, transmission (`KHR_materials_transmission`), and canvas textures baked to PNG.

## Import into Blender

`File > Import > glTF 2.0 (.glb/.gltf)` and select the exported file.
````

- [ ] **Step 2: Add the export step to `SKILL.md`**

In `SKILL.md`, in the `## Output` section, add a new bullet immediately after the
`**Implementation**:` bullet:

```markdown
- **Export**: every viewer wires `attachExportButton(root, { filename })` from
  `export/glbExporter.js` so the model exports to a Blender-ready `.glb` in one click.
  Details: `grimoire/export/glb_export.md`.
```

- [ ] **Step 3: Add a bullet to `README.md`**

In `README.md`, in the `## What you get` list, add after the existing factory bullet
(the line describing `createObjectNameModel`):

```markdown
- A one-click **GLB export** from the viewer (`export/glbExporter.js`): import the model straight into Blender, Godot, Unity, or any glTF 2.0 tool.
```

- [ ] **Step 4: Add an `Unreleased` entry to `CHANGELOG.md`**

In `CHANGELOG.md`, insert immediately after line 6 (the blank line before `## [1.2.0]`):

```markdown
## [Unreleased]

### Added

- **GLB export from the viewer.** A decoupled browser module (`export/glbExporter.js`)
  adds a one-click "Exportar .glb" button that exports any procedural model to a
  Blender-ready `.glb`. Exports a sanitized clone (drops circular runtime userData,
  neutralizes the animated root transform); standard PBR materials and canvas textures
  are preserved. Docs: `grimoire/export/glb_export.md`.

```

- [ ] **Step 5: Note early delivery in `ROADMAP.md`**

In `ROADMAP.md`, at the end of the v1.4 paragraph (line 31, after "…animation tools and
game engines."), append:

```markdown
 Model-level GLB export from the viewer (`export/glbExporter.js`) ships ahead of the rig work, so any current model can already be exported to `.glb` for Blender.
```

- [ ] **Step 6: Verify doc cross-references resolve**

Run:
```bash
test -f grimoire/export/glb_export.md && test -f export/glbExporter.js \
  && grep -q 'glbExporter.js' SKILL.md \
  && grep -q 'GLB export' README.md \
  && grep -q 'Unreleased' CHANGELOG.md \
  && grep -q 'glbExporter.js' ROADMAP.md \
  && echo "DOCS WIRED"
```
Expected: prints `DOCS WIRED`.

- [ ] **Step 7: Commit**

```bash
git add grimoire/export/glb_export.md SKILL.md README.md CHANGELOG.md ROADMAP.md
git commit -m "docs: document native GLB export capability

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Prepare the PR body

**Files:**
- Create: `PR_BODY.md`

**Interfaces:**
- Consumes: the committed work from Tasks 1 and 3.
- Produces: `PR_BODY.md`, ready to paste into a GitHub PR.

- [ ] **Step 1: Write `PR_BODY.md`**

Create `PR_BODY.md` with exactly this content:

```markdown
## Native GLB export from the viewer

Adds a decoupled browser module, `export/glbExporter.js`, that exports any img2threejs
procedural model to a Blender-ready `.glb` with one click — no build step, no npm
install, no pipeline coupling.

### What's included
- `export/glbExporter.js` — `exportModelToGLB(root, options)` (returns a Blob) and
  `attachExportButton(root, options)` (adds a floating "Exportar .glb" button). Wraps
  `three/addons/exporters/GLTFExporter.js`, resolved via the viewer importmap.
- `grimoire/export/glb_export.md` — integration + behavior docs.
- `SKILL.md`, `README.md`, `CHANGELOG.md`, `ROADMAP.md` — export documented as a
  standard output step.

### Design notes
- Exports a **sanitized clone**: strips the circular `root.userData.sculptRuntime`
  graph (which would otherwise break cloning / bloat `extras`) while preserving node
  `.name` values, so the named hierarchy survives into Blender.
- `resetRootTransform` neutralizes the viewer's animated spin so it isn't baked in.
- Model only — scene lights are excluded.
- Standard PBR materials (`MeshStandardMaterial` / `MeshPhysicalMaterial`) and
  `CanvasTexture` export fully (clearcoat, emissive, transmission, baked canvas textures).

### Verification
Wired into a real viewer (Master Sword) and validated end-to-end: the exported `.glb`
has a correct binary header (`glTF`, version 2, JSON chunk), and its glTF contains the
expected meshes, materials, and named nodes (`blade`, `guard`, `grip`, `pommel`).

### Out of scope
Headless Node CLI export, material editing, and non-GLB formats (OBJ/FBX/USD).
```

- [ ] **Step 2: Commit**

```bash
git add PR_BODY.md
git commit -m "docs: add PR body for GLB export feature

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 3: Final review — branch contents**

Run:
```bash
git log --oneline main..feat/glb-export && echo "---" && git diff --stat main..feat/glb-export
```
Expected: four commits (design spec, module, docs, PR body) and a diff touching only the
repo files listed in Global Constraints. Report the summary to the user with next-step
fork/push instructions:

```bash
gh repo fork hoainho/img2threejs --clone=false
git remote add fork https://github.com/<your-user>/img2threejs.git
git push fork feat/glb-export
gh pr create --repo hoainho/img2threejs --head <your-user>:feat/glb-export --title "Native GLB export from the viewer" --body-file PR_BODY.md
```

---

## Self-Review

**Spec coverage:**
- Decoupled module, browser button, `exportModelToGLB` + `attachExportButton` → Task 1. ✓
- Sanitized clone / userData / transform / lights-excluded behavior → Task 1 (code) + Task 3 (docs). ✓
- Native pipeline step (SKILL/README/CHANGELOG/ROADMAP + grimoire doc) → Task 3. ✓
- Verification on master-sword via playwright + GLB structure validation → Task 2. ✓
- Local branch + prepared PR, no push/fork → Task 4 (PR_BODY + instructions), work committed on `feat/glb-export`. ✓
- GLB binary default, `.glb` single file → Task 1 defaults. ✓
- Out of scope (Node CLI, material editor, non-GLB) → excluded, noted in PR body. ✓

**Placeholder scan:** No TBD/TODO; every code and doc step contains full content. ✓

**Type consistency:** `exportModelToGLB` and `attachExportButton` signatures and option
names (`binary`, `resetRootTransform`, `includeSculptUserData`, `filename`, `label`,
`onExported`) are identical across Task 1 code, Task 2 usage, Task 3 docs, and Task 4 PR
body. The `window.__swordRoot` hook and the `[glbExporter] .glb ready:` log line are
defined in Task 1/Task 2 and consumed in Task 2 verification. ✓
