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
    let exported = null;
    try {
      const ext = exportOptions.binary === false ? 'gltf' : 'glb';
      const blob = await exportModelToGLB(object3D, exportOptions);
      downloadBlob(blob, `${filename}.${ext}`);
      console.log('[glbExporter] .glb ready:', blob.size, 'bytes');
      button.textContent = '✓ Exportado';
      exported = blob;
    } catch (error) {
      console.error('[glbExporter] export failed:', error);
      button.textContent = '⚠ Error';
    } finally {
      setTimeout(() => { button.textContent = label; button.disabled = false; }, 1600);
    }
    // Notify after the export/catch so a throwing callback can't be mislabeled as an export failure.
    if (exported && typeof onExported === 'function') onExported(exported);
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
