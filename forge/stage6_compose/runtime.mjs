// Template consumed by compose.py; only the JSON data marker is replaced.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const DATA = __COMPOSITION_JSON__;
export const validation = Object.freeze({runtime: 'unevaluated', visualQuality: 'unevaluated', sceneQuality: 'unevaluated'});

async function verifiedBytes(entry) {
  const url = new URL(entry.url, import.meta.url);
  const response = await fetch(url);
  if (!response.ok) throw new Error(`artifact-fetch-failed: ${entry.path}`);
  const bytes = await response.arrayBuffer();
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
  if (hash !== entry.sha256) throw new Error(`hash-mismatch: ${entry.path}`);
  return bytes;
}

/** Build a candidate only. No acceptance, lighting proof or visual result is implied. */
export async function createComposedScene() {
  // Verify all retained input/evidence bytes, not just the renderable GLBs.
  const bytes = new Map();
  for (const file of DATA.files) bytes.set(file.path, await verifiedBytes(file));
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(DATA.camera.fov, DATA.camera.aspect, DATA.camera.near, DATA.camera.far);
  camera.position.fromArray(DATA.camera.position);
  camera.up.set(0, 1, 0);
  camera.lookAt(...DATA.camera.target);
  const rig = new THREE.Group();
  rig.name = 'recorded-environment';
  rig.add(new THREE.HemisphereLight(DATA.environment.skyColor, DATA.environment.groundColor, DATA.environment.intensity));
  scene.add(rig);
  if (DATA.baseContainer !== null) {
    for (const name of ['ground', 'backdrop']) {
      const record = DATA.baseContainer[name];
      const plane = new THREE.Mesh(new THREE.PlaneGeometry(record.width, record.height), new THREE.MeshBasicMaterial({color: record.color, side: THREE.DoubleSide}));
      plane.name = `base-${name}`;
      plane.position.fromArray(record.position);
      plane.rotation.set(...record.rotation);
      scene.add(plane);
    }
  }
  const manager = new THREE.LoadingManager();
  // Composition supports only self-contained GLBs; never fetch undeclared resources.
  manager.setURLModifier(url => {
    if (!url.startsWith('data:') && !url.startsWith('blob:')) throw new Error('external-glb-resource');
    return url;
  });
  const loader = new GLTFLoader(manager);
  const objects = new Map();
  const animations = new Map();
  for (const item of DATA.instances) {
    // Parse each instance independently: cloning a SkinnedMesh can break its skeleton.
    const gltf = await loader.parseAsync(bytes.get(item.modelPath), '');
    if (!gltf.scene) throw new Error(`missing-gltf-scene: ${item.objectId}`);
    const parent = new THREE.Group();
    parent.name = item.objectId;
    parent.position.fromArray(item.position);
    parent.rotation.y = item.yawBin * Math.PI / 4;
    parent.scale.fromArray(item.scale);
    // No traversal, recentering, child transform, material or animation edits.
    parent.add(gltf.scene);
    scene.add(parent);
    objects.set(item.objectId, parent);
    animations.set(item.objectId, gltf.animations);
  }
  return {scene, camera, objects, animations, validation};
}
