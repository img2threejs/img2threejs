// Program-owned scene capture. Never execute a provider-supplied script.
import * as THREE from 'three';

globalThis.__DELIVERED_CAPTURE__ = (async () => {
  const config = await (await fetch('/config.json')).json();
  // The browser host checks this sentinel route in addition to all scene inputs.
  await (await fetch('/delivery.glb')).arrayBuffer();
  const {createComposedScene} = await import(config.moduleUrl);
  const {scene, camera, objects} = await createComposedScene();
  scene.updateMatrixWorld(true);
  scene.traverse(node => { if (node.isSkinnedMesh) node.skeleton.update(); });
  const owners = new Map();
  for (const row of config.inventory) {
    const root = objects.get(row.objectId) ?? objects.get(row.artifactObjectId);
    if (!root) throw new Error(`inventory-geometry-mismatch: ${row.objectId}`);
    if (objects.has(row.objectId)) owners.set(root, row.objectId);
    else {
      const matches = [];
      root.traverse(node => { if (node.name === row.objectId) matches.push(node); });
      if (matches.length !== 1) throw new Error(`inventory-node-mismatch: ${row.objectId}`);
      owners.set(matches[0], row.objectId);
    }
  }
  // Repeated instances have their own parent rather than the artifact owner's.
  for (const [id, root] of objects) if (config.inventory.some(row => row.objectId === id)) owners.set(root, id);
  const geometry = new Map(config.inventory.map(row => [row.objectId, []]));
  const meshOwners = new Map();
  const support = {ground: []};
  let total = 0;
  scene.traverse(mesh => {
    if (!mesh.isMesh) return;
    let owner = null;
    for (let node = mesh; node; node = node.parent) if (owners.has(node)) { owner = owners.get(node); break; }
    const target = owner === null ? (mesh.name === 'base-ground' ? support.ground : null) : geometry.get(owner);
    if (target === null || target === undefined) return;
    const g = mesh.geometry;
    if (!g?.attributes.position || (g.drawRange.count !== Infinity && g.drawRange.count < (g.index?.count ?? g.attributes.position.count))) throw new Error('unsupported-draw-range');
    const count = g.index?.count ?? g.attributes.position.count;
    if (count % 3 || (total += count / 3) > 1000000) throw new Error('invalid-triangle-budget');
    const cache = new Map();
    const vertex = new THREE.Vector3();
    const point = index => {
      if (!cache.has(index)) {
        const p = mesh.getVertexPosition(index, vertex).applyMatrix4(mesh.matrixWorld);
        if (!Number.isFinite(p.x) || !Number.isFinite(p.y) || !Number.isFinite(p.z)) throw new Error('nonfinite-world-vertex');
        cache.set(index, p.toArray());
      }
      return cache.get(index);
    };
    for (let i = 0; i < count; i += 3) target.push([
      point(g.index ? g.index.getX(i) : i),
      point(g.index ? g.index.getX(i + 1) : i + 1),
      point(g.index ? g.index.getX(i + 2) : i + 2)]);
    if (owner !== null) meshOwners.set(mesh, owner);
  });
  camera.updateMatrixWorld(true);
  camera.updateProjectionMatrix();
  const records = [];
  const projected = new THREE.Vector3();
  for (const [id, triangles] of geometry) {
    if (!triangles.length) throw new Error(`empty-instance-geometry: ${id}`);
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    let clipped = false;
    for (const tri of triangles) for (const point of tri) {
      const p = projected.fromArray(point).project(camera);
      if (!Number.isFinite(p.x) || !Number.isFinite(p.y) || !Number.isFinite(p.z) || p.z <= -1 || p.z >= 1) clipped = true;
      const x = (p.x + 1) / 2, y = (1 - p.y) / 2;
      x0 = Math.min(x0, x); x1 = Math.max(x1, x);
      y0 = Math.min(y0, y); y1 = Math.max(y1, y);
    }
    const box = [Math.max(0, x0), Math.max(0, y0), Math.min(1, x1), Math.min(1, y1)];
    records.push({id, triangles, projectedBox: box, clipped});
  }
  const renderer = new THREE.WebGLRenderer({antialias: true, preserveDrawingBuffer: true});
  renderer.setPixelRatio(1);
  renderer.setSize(config.viewport.width, config.viewport.height);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setClearColor(config.background, 1);
  document.body.append(renderer.domElement);
  renderer.render(scene, camera);
  const target = new THREE.WebGLRenderTarget(config.viewport.width, config.viewport.height);
  const originals = new Map(), temporary = [];
  const ids = new Map(records.map((row, index) => [row.id, index + 1]));
  scene.traverse(mesh => {
    if (!mesh.isMesh) return;
    originals.set(mesh, mesh.material);
    const value = ids.get(meshOwners.get(mesh)) ?? 0;
    const color = new THREE.Color().setRGB((value & 255) / 255, ((value >> 8) & 255) / 255, ((value >> 16) & 255) / 255, THREE.LinearSRGBColorSpace);
    const source = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    const replacements = source.map(material => {
      const replacement = new THREE.MeshBasicMaterial({color, side: material.side,
        depthTest: material.depthTest, depthWrite: material.depthWrite,
        transparent: material.transparent, opacity: material.opacity, alphaTest: material.alphaTest,
        map: material.map, alphaMap: material.alphaMap, visible: material.visible});
      replacement.onBeforeCompile = shader => {
        shader.fragmentShader = shader.fragmentShader.replace('#include <map_fragment>', `
          #ifdef USE_MAP
            diffuseColor.a *= texture2D(map, vMapUv).a;
          #endif
        `);
      };
      temporary.push(replacement);
      return replacement;
    });
    mesh.material = Array.isArray(mesh.material) ? replacements : replacements[0];
  });
  const oldColorSpace = renderer.outputColorSpace;
  renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
  renderer.setClearColor(0, 1);
  renderer.setRenderTarget(target);
  renderer.render(scene, camera);
  const pixels = new Uint8Array(config.viewport.width * config.viewport.height * 4);
  renderer.readRenderTargetPixels(target, 0, 0, config.viewport.width, config.viewport.height, pixels);
  const counts = new Map();
  const objectMask = new Array(config.viewport.width * config.viewport.height).fill(false);
  for (let i = 0; i < pixels.length; i += 4) {
    const id = pixels[i] + (pixels[i + 1] << 8) + (pixels[i + 2] << 16);
    counts.set(id, (counts.get(id) ?? 0) + 1);
  }
  for (const row of records) row.visiblePixels = counts.get(ids.get(row.id)) ?? 0;
  for (let y = 0; y < config.viewport.height; y++) for (let x = 0; x < config.viewport.width; x++) {
    const i = ((config.viewport.height - 1 - y) * config.viewport.width + x) * 4;
    const id = pixels[i] + (pixels[i + 1] << 8) + (pixels[i + 2] << 16);
    objectMask[y * config.viewport.width + x] = id >= 1 && id <= records.length;
  }
  for (const [mesh, material] of originals) mesh.material = material;
  for (const material of temporary) material.dispose();
  renderer.setRenderTarget(null);
  renderer.outputColorSpace = oldColorSpace;
  renderer.setClearColor(config.background, 1);
  renderer.render(scene, camera);
  target.dispose();
  const lights = [];
  scene.traverse(node => { if (node.isLight) lights.push(node); });
  return {kind: 'img2.scene-runtime-measurement', version: 1, sceneSha256: config.sceneSha256,
    referenceSha256: config.referenceSha256, viewport: config.viewport,
    threeRevision: THREE.REVISION, objects: records, support, objectMask,
    lightRigCount: lights.length === 1 && lights[0].isHemisphereLight && lights[0].parent.name === 'recorded-environment' ? 1 : (lights.length === 1 ? 2 : lights.length)};
})();
