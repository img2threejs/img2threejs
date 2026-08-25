import * as THREE from 'three';
import {
  createHanHuanShouDaoModel,
  createHanHuanShouDaoLookDevLights,
  createHanHuanShouDaoEnvironment,
  configureHanHuanShouDaoRenderer,
  createHanHuanShouDaoInspectControls,
} from '../createHanHuanShouDao.ts';

const canvas = document.querySelector('#stage');
const renderer = new THREE.WebGLRenderer({
  canvas,
  antialias: true,
  preserveDrawingBuffer: true,
});
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setSize(window.innerWidth, window.innerHeight, false);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.shadowMap.autoUpdate = false;
renderer.shadowMap.needsUpdate = true;
configureHanHuanShouDaoRenderer(renderer);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xffffff);

let viewAspect = window.innerWidth / Math.max(1, window.innerHeight);
const camera = new THREE.OrthographicCamera(-viewAspect, viewAspect, 1, -1, 0.01, 100);
const model = createHanHuanShouDaoModel({
  castShadow: true,
  receiveShadow: true,
  textureSize: 1024,
});
scene.add(model);

let lights = createHanHuanShouDaoLookDevLights('neutral');
scene.add(lights);

scene.environment = createHanHuanShouDaoEnvironment(renderer);

const controls = createHanHuanShouDaoInspectControls(camera, renderer.domElement);
controls.maxDistance = 16;
controls.minDistance = 0.25;
const runtime = model.userData.sculptRuntime;
const originalNodePositions = new Map(
  Object.entries(runtime.nodes).map(([id, node]) => [id, node.position.clone()]),
);
let selectionHelper = null;

function moduleIdFor(id) {
  const node = runtime.nodes[id];
  return node?.userData.explodeWithParent || id;
}

function assemblyModules() {
  const modules = new Map();
  for (const [id, node] of Object.entries(runtime.nodes)) {
    if (id === 'root' || !runtime.meshes[id]) continue;
    const moduleId = moduleIdFor(id);
    if (!modules.has(moduleId)) modules.set(moduleId, []);
    modules.get(moduleId).push(node);
  }
  return modules;
}

function moduleBox(moduleId) {
  const box = new THREE.Box3();
  for (const node of assemblyModules().get(moduleId) || []) {
    box.union(new THREE.Box3().setFromObject(node));
  }
  return box;
}

function selectPart(id) {
  if (selectionHelper) {
    scene.remove(selectionHelper);
    selectionHelper.dispose();
    selectionHelper = null;
  }
  if (!id || !runtime.nodes[id]) return { componentId: null, moduleId: null };
  const moduleId = moduleIdFor(id);
  const box = moduleBox(moduleId);
  if (!box.isEmpty()) {
    selectionHelper = new THREE.Box3Helper(box, 0x2474a6);
    selectionHelper.name = 'assembly-selection';
    scene.add(selectionHelper);
  }
  renderer.render(scene, camera);
  return { componentId: id, moduleId };
}

function setExplode(amount = 0) {
  const factor = THREE.MathUtils.clamp(Number(amount) || 0, 0, 1);
  for (const [id, node] of Object.entries(runtime.nodes)) {
    const original = originalNodePositions.get(id);
    if (original) node.position.copy(original);
  }
  model.updateMatrixWorld(true);
  const modules = [...assemblyModules().entries()]
    .map(([id, nodes]) => ({ id, nodes, center: moduleBox(id).getCenter(new THREE.Vector3()) }))
    .sort((a, b) => a.center.x - b.center.x);
  const midpoint = (modules.length - 1) * 0.5;
  let maxDisplacement = 0;
  modules.forEach((entry, index) => {
    const displacement = (index - midpoint) * 0.12 * factor;
    maxDisplacement = Math.max(maxDisplacement, Math.abs(displacement));
    for (const node of entry.nodes) {
      const componentId = node.userData.sculptComponent?.id;
      const original = originalNodePositions.get(componentId);
      if (original) node.position.set(original.x + displacement, original.y, original.z);
    }
  });
  model.updateMatrixWorld(true);
  selectPart(null);
  renderer.shadowMap.needsUpdate = true;
  renderer.render(scene, camera);
  return {
    factor,
    modules: modules.map((entry) => entry.id),
    movedModules: factor > 0 ? modules.length : 0,
    maxDisplacement,
  };
}

function getAssemblyManifest() {
  const parts = Object.entries(runtime.meshes).map(([id, mesh]) => {
    const position = mesh.geometry.getAttribute('position');
    const triangles = Math.floor((mesh.geometry.index?.count || position?.count || 0) / 3);
    const moduleId = moduleIdFor(id);
    return {
      name: id,
      kind: moduleId === id ? 'part' : 'integral',
      module: moduleId,
      triangles,
    };
  });
  let unnamedMeshes = 0;
  model.traverse((object) => {
    if (object.isMesh && !object.name) unnamedMeshes += 1;
  });
  return {
    model: 'han-huan-shou-dao',
    parts,
    modules: [...assemblyModules().keys()],
    sockets: Object.keys(runtime.sockets),
    colliders: Object.entries(runtime.colliders)
      .filter(([, collider]) => collider)
      .map(([id]) => id),
    unnamedMeshes,
    integralMeshes: parts.filter((part) => part.kind === 'integral').length,
  };
}

function getMaterialAudit() {
  const byUuid = new Map();
  for (const [componentId, mesh] of Object.entries(runtime.meshes)) {
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const material of materials) {
      if (!material || byUuid.has(material.uuid)) {
        const existing = material ? byUuid.get(material.uuid) : null;
        if (existing) existing.components.push(componentId);
        continue;
      }
      const spec = material.userData.sculptMaterial || {};
      const textures = {
        albedo: material.map,
        roughness: material.roughnessMap,
        height: material.bumpMap || material.displacementMap,
        normal: material.normalMap,
        ao: material.aoMap,
      };
      const channels = Object.fromEntries(
        Object.entries(textures).map(([channel, texture]) => [channel, {
          present: Boolean(texture),
          uuid: texture?.uuid || null,
          width: texture?.image?.width || 0,
          height: texture?.image?.height || 0,
        }]),
      );
      const presentUuids = Object.values(channels).filter((channel) => channel.present).map((channel) => channel.uuid);
      byUuid.set(material.uuid, {
        id: spec.id || material.name || material.uuid,
        components: [componentId],
        source: material.userData.pbrTextureSource || 'unknown',
        channels,
        independentChannels: presentUuids.length === new Set(presentUuids).size,
        surfaceBands: Array.isArray(spec.surfaceFrequencyBands)
          ? spec.surfaceFrequencyBands.map((band) => band.id).filter(Boolean)
          : [],
        roughnessVariation: Number(spec.roughness?.variation || 0),
        normalStrength: Number(spec.normal?.strength || 0),
        bumpScale: Number(material.bumpScale || material.displacementScale || 0),
        aoIntensity: Number(material.aoMapIntensity || 0),
        dirtAmount: Number(spec.dirt?.amount || 0),
        edgeWear: Number(spec.wear?.edgeWear || 0),
        localOverrideCount: Array.isArray(spec.localOverrides) ? spec.localOverrides.length : 0,
      });
    }
  }
  const materials = [...byUuid.values()];
  const requiredChannels = ['albedo', 'roughness', 'height', 'normal', 'ao'];
  const requiredBands = ['macro', 'meso', 'micro'];
  const failures = [];
  for (const material of materials) {
    for (const channel of requiredChannels) {
      const texture = material.channels[channel];
      if (!texture.present) failures.push(`${material.id}: missing ${channel}`);
      else if (Math.min(texture.width, texture.height) < 1024) failures.push(`${material.id}: ${channel} below 1024px`);
    }
    if (!material.independentChannels) failures.push(`${material.id}: map channels share a texture`);
    for (const band of requiredBands) {
      if (!material.surfaceBands.includes(band)) failures.push(`${material.id}: missing ${band} surface band`);
    }
    if (material.roughnessVariation <= 0) failures.push(`${material.id}: uniform roughness`);
    if (material.normalStrength <= 0 || material.bumpScale <= 0) failures.push(`${material.id}: missing normal/height response`);
    if (material.aoIntensity <= 0) failures.push(`${material.id}: missing AO response`);
    if (material.dirtAmount <= 0 && material.edgeWear <= 0) failures.push(`${material.id}: no active surface-locality mask`);
    if (material.localOverrideCount <= 0) failures.push(`${material.id}: missing local override`);
  }
  return { requiredChannels, requiredBands, materials, failures };
}

function setLightingMode(mode = 'neutral') {
  if (!['neutral', 'grazing', 'reference'].includes(mode)) throw new Error(`unsupported lighting mode: ${mode}`);
  scene.remove(lights);
  lights = createHanHuanShouDaoLookDevLights(mode);
  scene.add(lights);
  renderer.shadowMap.needsUpdate = true;
  renderer.render(scene, camera);
  return { mode, lightCount: lights.children.length };
}

function getLightingAudit() {
  const requiredNotes = ['key', 'fill', 'rim', 'exposure', 'tone mapping', 'background', 'contact shadow'];
  const modes = ['neutral', 'grazing', 'reference'].map((mode) => {
    const group = createHanHuanShouDaoLookDevLights(mode);
    const byName = Object.fromEntries(group.children.map((light) => [light.name, light]));
    return {
      mode,
      lightCount: group.children.length,
      hemisphereCount: group.children.filter((light) => light.isHemisphereLight).length,
      directionalCount: group.children.filter((light) => light.isDirectionalLight).length,
      keyIntensity: Number(byName['lookdev-key']?.intensity || 0),
      fillIntensity: Number(byName['lookdev-fill']?.intensity || 0),
      rimIntensity: Number(byName['lookdev-rim']?.intensity || 0),
      keyCastsShadow: byName['lookdev-key']?.castShadow === true,
    };
  });
  const notes = Array.isArray(lights.userData.lightingFromPhoto) ? lights.userData.lightingFromPhoto : [];
  const noteText = notes.join(' ').toLowerCase();
  const failures = [];
  for (const mode of modes) {
    if (mode.lightCount !== 4 || mode.hemisphereCount !== 1 || mode.directionalCount !== 3) {
      failures.push(`${mode.mode}: expected one hemisphere plus key/fill/rim`);
    }
    if (mode.keyIntensity <= mode.fillIntensity || mode.keyIntensity <= mode.rimIntensity) {
      failures.push(`${mode.mode}: key light is not dominant`);
    }
    if (!mode.keyCastsShadow) failures.push(`${mode.mode}: key light does not cast shadows`);
  }
  for (const term of requiredNotes) {
    if (!noteText.includes(term)) failures.push(`lightingFromPhoto missing ${term}`);
  }
  if (renderer.toneMapping !== THREE.ACESFilmicToneMapping) failures.push('renderer is not using ACES filmic tone mapping');
  if (renderer.toneMappingExposure !== 1) failures.push('renderer exposure is not 1.0');
  if (renderer.outputColorSpace !== THREE.SRGBColorSpace) failures.push('renderer output is not sRGB');
  if (!scene.environment) failures.push('scene environment is missing');
  if (!(scene.background?.isColor && scene.background.getHex() === 0xffffff)) failures.push('background is not pure white');
  return {
    requiredNotes,
    notes,
    renderer: {
      toneMapping: renderer.toneMapping,
      exposure: renderer.toneMappingExposure,
      outputColorSpace: renderer.outputColorSpace,
      shadowsEnabled: renderer.shadowMap.enabled,
    },
    background: scene.background?.isColor ? `#${scene.background.getHexString()}` : null,
    environmentPresent: Boolean(scene.environment),
    modes,
    failures,
  };
}

function getInteractionAudit() {
  const expectedModules = ['blade', 'guard', 'collar', 'handle', 'ferrule', 'ring'];
  const expectedSockets = [
    'root:blade-heel',
    'root:guard-back',
    'root:front-ferrule-back',
    'root:handle-back',
    'root:rear-ferrule-back',
    'root:pommel-anchor',
  ];
  const expectedColliders = ['root', ...expectedModules];
  const actionable = Object.entries(runtime.nodes)
    .filter(([, node]) => ['macro', 'meso'].includes(node.userData.sculptComponent?.level))
    .map(([id, node]) => {
      const action = node.userData.actionProfile || {};
      return {
        id,
        level: node.userData.sculptComponent?.level,
        pivotName: node.name,
        stablePivot: node.name.endsWith('__pivot'),
        meshIsDirectChild: runtime.meshes[id]?.parent === node,
        hasPivotMetadata: Boolean(action.pivot),
        hasColliderMetadata: Object.prototype.hasOwnProperty.call(action, 'collider'),
        hasDestructionMetadata: Boolean(action.destruction?.fractureGroup),
      };
    });
  const mappings = [
    ['hamon-1', 'blade'],
    ['stud-c', 'handle'],
    ['ring-engraving-inner', 'ring'],
  ].map(([componentId, expectedModule]) => {
    const selection = selectPart(componentId);
    return { componentId, expectedModule, resolvedModule: selection.moduleId };
  });
  selectPart(null);

  const before = new Map(Object.entries(runtime.nodes).map(([id, node]) => [id, node.position.clone()]));
  const exploded = setExplode(1);
  const moduleDisplacements = expectedModules.map((moduleId) => {
    const nodes = assemblyModules().get(moduleId) || [];
    const displacements = nodes.map((node) => node.position.distanceTo(before.get(node.userData.sculptComponent?.id)));
    return {
      moduleId,
      nodeCount: nodes.length,
      minDisplacement: displacements.length ? Math.min(...displacements) : 0,
      maxDisplacement: displacements.length ? Math.max(...displacements) : 0,
    };
  });
  setExplode(0);
  const restorationError = Math.max(
    0,
    ...Object.entries(runtime.nodes).map(([id, node]) => node.position.distanceTo(before.get(id))),
  );
  const selectionCleared = !scene.getObjectByName('assembly-selection');
  const socketIds = Object.keys(runtime.sockets).sort();
  const colliderIds = Object.entries(runtime.colliders)
    .filter(([, collider]) => collider)
    .map(([id]) => id)
    .sort();
  const destructionGroups = Object.fromEntries(
    Object.entries(runtime.destructionGroups).map(([id, nodes]) => [id, nodes.length]),
  );
  const failures = [];
  for (const item of actionable) {
    if (!item.stablePivot) failures.push(`${item.id}: unstable pivot name`);
    if (!item.meshIsDirectChild) failures.push(`${item.id}: mesh is not a direct pivot child`);
    if (!item.hasPivotMetadata) failures.push(`${item.id}: missing pivot metadata`);
    if (!item.hasColliderMetadata) failures.push(`${item.id}: missing collider metadata`);
    if (!item.hasDestructionMetadata) failures.push(`${item.id}: missing destruction metadata`);
  }
  for (const mapping of mappings) {
    if (mapping.resolvedModule !== mapping.expectedModule) {
      failures.push(`${mapping.componentId}: resolved to ${mapping.resolvedModule}, expected ${mapping.expectedModule}`);
    }
  }
  if (exploded.modules.join(',') !== expectedModules.join(',')) failures.push('explode module order or membership changed');
  for (const module of moduleDisplacements) {
    if (module.nodeCount === 0 || module.maxDisplacement <= 0) failures.push(`${module.moduleId}: module did not move`);
    if (module.maxDisplacement - module.minDisplacement > 1e-8) failures.push(`${module.moduleId}: integral nodes moved incoherently`);
  }
  if (restorationError > 1e-8) failures.push(`explode restore drifted by ${restorationError}`);
  if (!selectionCleared) failures.push('selection helper was not cleared');
  for (const socket of expectedSockets) if (!socketIds.includes(socket)) failures.push(`missing socket ${socket}`);
  for (const collider of expectedColliders) if (!colliderIds.includes(collider)) failures.push(`missing collider ${collider}`);
  for (const module of ['root', ...expectedModules]) {
    if (!destructionGroups[module]) failures.push(`missing destruction group ${module}`);
  }
  return {
    expectedModules,
    expectedSockets,
    expectedColliders,
    actionable,
    mappings,
    exploded,
    moduleDisplacements,
    restorationError,
    selectionCleared,
    socketIds,
    colliderIds,
    destructionGroups,
    failures,
  };
}

async function getPerformanceAudit() {
  selectPart(null);
  setExplode(0);
  renderer.info.reset();
  renderer.render(scene, camera);
  const render = {
    drawCalls: renderer.info.render.calls,
    triangles: renderer.info.render.triangles,
    lines: renderer.info.render.lines,
    points: renderer.info.render.points,
  };
  const geometries = new Set();
  const materials = new Map();
  const textures = new Set();
  let meshCount = 0;
  let instancedMeshCount = 0;
  model.traverse((object) => {
    if (!object.isMesh) return;
    meshCount += 1;
    if (object.isInstancedMesh) instancedMeshCount += 1;
    if (object.geometry) geometries.add(object.geometry.uuid);
    const list = Array.isArray(object.material) ? object.material : [object.material];
    for (const material of list) {
      if (!material) continue;
      materials.set(material.uuid, (materials.get(material.uuid) || 0) + 1);
      for (const key of ['map', 'roughnessMap', 'bumpMap', 'displacementMap', 'normalMap', 'aoMap']) {
        if (material[key]) textures.add(material[key].uuid);
      }
    }
  });
  const frameTimes = [];
  for (let index = 0; index < 31; index += 1) {
    frameTimes.push(await new Promise((resolve) => requestAnimationFrame(resolve)));
  }
  const elapsedSeconds = Math.max(1e-6, (frameTimes.at(-1) - frameTimes[0]) / 1000);
  const measuredFps = 30 / elapsedSeconds;
  const gl = renderer.getContext();
  const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
  const rendererBackend = debugInfo
    ? gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL)
    : gl.getParameter(gl.RENDERER);
  const softwareRenderer = /swiftshader|llvmpipe|software/i.test(String(rendererBackend));
  const budget = model.userData.performanceBudget || {};
  const lodPlan = model.userData.lodPlan || [];
  const optimizationPlan = model.userData.optimizationPlan || {};
  const repeatedDetailDecisions = Array.isArray(optimizationPlan.repetitionDecisions)
    ? optimizationPlan.repetitionDecisions
    : [];
  const failures = [];
  const warnings = [];
  if (render.triangles > Number(budget.targetTriangles || 0)) failures.push('triangle budget exceeded');
  if (render.drawCalls > Number(budget.maxDrawCalls || 0)) failures.push('draw-call budget exceeded');
  if (measuredFps + 0.5 < Number(budget.fpsTarget || 0)) {
    const message = `measured FPS below target on ${rendererBackend}`;
    if (softwareRenderer) warnings.push(message);
    else failures.push(message);
  }
  if (!lodPlan.some((tier) => tier.tier === 'near') || !lodPlan.some((tier) => tier.tier === 'far')) {
    failures.push('near/far LOD strategy missing');
  }
  if (!repeatedDetailDecisions.length) failures.push('repeated-detail decisions missing');
  for (const decision of repeatedDetailDecisions) {
    if (!decision.family || !decision.strategy || !decision.reason) failures.push('incomplete repeated-detail decision');
  }
  return {
    budget,
    render,
    measuredFps: Number(measuredFps.toFixed(2)),
    rendererBackend,
    fpsAuthority: softwareRenderer ? 'report-only-software-renderer' : 'hardware-webgl-gate',
    resources: {
      meshCount,
      instancedMeshCount,
      uniqueGeometries: geometries.size,
      uniqueMaterials: materials.size,
      sharedMaterials: [...materials.values()].filter((uses) => uses > 1).length,
      uniqueTextures: textures.size,
      rendererGeometries: renderer.info.memory.geometries,
      rendererTextures: renderer.info.memory.textures,
    },
    lodPlan,
    repeatedDetailDecisions,
    optimizationPolicy: optimizationPlan.policy || null,
    benchmarkPolicy: optimizationPlan.benchmarkPolicy || null,
    warnings,
    failures,
  };
}

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
let pointerStart = null;
canvas.addEventListener('pointerdown', (event) => {
  pointerStart = { x: event.clientX, y: event.clientY };
});
canvas.addEventListener('pointerup', (event) => {
  if (!pointerStart || Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) > 4) return;
  const rect = canvas.getBoundingClientRect();
  pointer.set(
    ((event.clientX - rect.left) / rect.width) * 2 - 1,
    -((event.clientY - rect.top) / rect.height) * 2 + 1,
  );
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObjects(Object.values(runtime.meshes), false)[0];
  selectPart(hit?.object.userData.sculptComponent?.id || null);
});

function frameFilled(object, azimuthDeg = 0, elevationDeg = 6, margin = 1.08) {
  const box = new THREE.Box3().setFromObject(object);
  if (box.isEmpty()) return;
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const az = (azimuthDeg * Math.PI) / 180;
  const el = (elevationDeg * Math.PI) / 180;
  const horiz = size.x * Math.abs(Math.cos(az)) + size.z * Math.abs(Math.sin(az));
  const depth = size.x * Math.abs(Math.sin(az)) + size.z * Math.abs(Math.cos(az));
  const vert = size.y * Math.abs(Math.cos(el)) + depth * Math.abs(Math.sin(el));
  const aspect = Math.max(0.2, viewAspect);
  const halfHeight = Math.max(vert * 0.5, (horiz * 0.5) / aspect) * margin;
  camera.left = -halfHeight * aspect;
  camera.right = halfHeight * aspect;
  camera.top = halfHeight;
  camera.bottom = -halfHeight;
  const dir = new THREE.Vector3(
    Math.sin(az) * Math.cos(el),
    Math.sin(el),
    Math.cos(az) * Math.cos(el),
  );
  const maxSize = Math.max(size.x, size.y, size.z);
  const distance = Math.max(maxSize * 2 + 0.5, 0.8);
  camera.position.copy(center).addScaledVector(dir, distance);
  camera.near = 0.01;
  camera.far = distance + maxSize * 4;
  camera.lookAt(center);
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.update();
}

function frameView(azimuthDeg = 0, elevationDeg = 6, margin = 1.08) {
  frameFilled(model, azimuthDeg, elevationDeg, margin);
}

frameView(0, 0, 1.055);

const captureState = {
  azimuthDegrees: 0,
  elevationDegrees: 0,
};
const authoredMaterials = new Map();

async function waitFrames(count = 2) {
  for (let i = 0; i < count; i += 1) {
    await new Promise((resolve) => requestAnimationFrame(resolve));
  }
}

window.__IMG2THREEJS_CAPTURE__ = {
  getAssemblyManifest,
  getInteractionAudit,
  getLightingAudit,
  getMaterialAudit,
  getPerformanceAudit,
  selectPart,
  setExplode,
  setLightingMode,
  setMapStripped(enabled) {
    model.traverse((object) => {
      if (!object.isMesh) return;
      if (enabled && !authoredMaterials.has(object)) {
        authoredMaterials.set(object, object.material);
        object.material = new THREE.MeshBasicMaterial({ color: 0x9da1a4 });
      } else if (!enabled && authoredMaterials.has(object)) {
        object.material.dispose();
        object.material = authoredMaterials.get(object);
        authoredMaterials.delete(object);
      }
    });
    renderer.render(scene, camera);
    return { ok: true };
  },
  async setCamera(spec = {}) {
    const azimuth = Number.isFinite(spec.azimuthDegrees) ? spec.azimuthDegrees : 0;
    const elevation = Number.isFinite(spec.elevationDegrees) ? spec.elevationDegrees : 0;
    const margin = Number.isFinite(spec.margin) ? spec.margin : 1.08;
    captureState.azimuthDegrees = azimuth;
    captureState.elevationDegrees = elevation;
    if (typeof spec.componentId === 'string' && runtime.nodes[spec.componentId]) {
      frameFilled(runtime.nodes[spec.componentId], azimuth, elevation || 12, Number.isFinite(spec.margin) ? spec.margin : 1.18);
    } else if (spec.role === 'head-closeup') {
      const ring = model.getObjectByName('Huan-shou ring__pivot') ?? model;
      frameFilled(ring, azimuth, elevation || 12, Number.isFinite(spec.margin) ? spec.margin : 1.22);
    } else {
      frameView(azimuth, elevation, margin);
    }
    if (Number.isFinite(spec.near)) camera.near = spec.near;
    if (Number.isFinite(spec.far)) camera.far = spec.far;
    camera.updateProjectionMatrix();
    renderer.render(scene, camera);
    await waitFrames(2);
    return { ok: true };
  },
};

function resize() {
  const width = window.innerWidth;
  const height = window.innerHeight;
  viewAspect = width / Math.max(1, height);
  renderer.setSize(width, height, false);
}

window.addEventListener('resize', () => {
  resize();
  frameView(captureState.azimuthDegrees, captureState.elevationDegrees);
});

function tick() {
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(tick);
}

resize();
renderer.render(scene, camera);
tick();
window.__IMG2THREEJS_READY__ = true;
