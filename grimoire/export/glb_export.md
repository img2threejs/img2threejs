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
