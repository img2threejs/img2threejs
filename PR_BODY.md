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
