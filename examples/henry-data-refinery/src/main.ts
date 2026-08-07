import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {resolveAnimationTime} from './animationTime';
import {createDataRefineryModel} from './createDataRefineryModel';
import './styles.css';

const app = document.querySelector<HTMLDivElement>('#app');
const resetButton = document.querySelector<HTMLButtonElement>('[data-testid="reset-camera"]');

if (!app || !resetButton) {
  throw new Error('Data refinery viewer shell is incomplete.');
}
const viewport = app;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xf4f7fb);
scene.fog = new THREE.FogExp2(0xf4f7fb, 0.018);

const renderer = new THREE.WebGLRenderer({antialias: true, alpha: false});
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.12;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
app.append(renderer.domElement);

const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100);
const defaultCameraPosition = new THREE.Vector3(9, 7, 11);
const defaultTarget = new THREE.Vector3(0, 2, 0);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.055;
controls.minDistance = 7;
controls.maxDistance = 30;
controls.maxPolarAngle = Math.PI * 0.48;
controls.target.copy(defaultTarget);

function resetCamera(): void {
  camera.position.copy(defaultCameraPosition);
  controls.target.copy(defaultTarget);
  controls.update();
}

resetCamera();
resetButton.addEventListener('click', resetCamera);

scene.add(new THREE.HemisphereLight(0xf4fbff, 0x27374b, 2.2));

const keyLight = new THREE.DirectionalLight(0xffffff, 3.4);
keyLight.position.set(-6, 12, 9);
keyLight.castShadow = true;
keyLight.shadow.mapSize.set(2048, 2048);
keyLight.shadow.camera.left = -12;
keyLight.shadow.camera.right = 12;
keyLight.shadow.camera.top = 10;
keyLight.shadow.camera.bottom = -8;
scene.add(keyLight);

const blueRim = new THREE.DirectionalLight(0x5ca8ff, 2.1);
blueRim.position.set(8, 6, -8);
scene.add(blueRim);

const tealFill = new THREE.PointLight(0x38e1d3, 2.6, 12, 1.5);
tealFill.position.set(-1.5, 3.2, 2.2);
scene.add(tealFill);

const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(34, 24),
  new THREE.MeshStandardMaterial({color: 0xe6edf5, roughness: 0.82, metalness: 0.05}),
);
ground.name = 'Ground';
ground.rotation.x = -Math.PI / 2;
ground.position.y = -0.11;
ground.receiveShadow = true;
scene.add(ground);

const grid = new THREE.GridHelper(28, 28, 0x8fadc8, 0xc9d7e5);
grid.position.y = -0.1;
const gridMaterials = Array.isArray(grid.material) ? grid.material : [grid.material];
for (const material of gridMaterials) {
  material.transparent = true;
  material.opacity = 0.23;
}
scene.add(grid);

const model = createDataRefineryModel();
model.rotation.y = -0.06;
scene.add(model);

function resize(): void {
  const width = viewport.clientWidth;
  const height = viewport.clientHeight;
  renderer.setSize(width, height, false);
  camera.aspect = width / Math.max(height, 1);
  camera.updateProjectionMatrix();
}

window.addEventListener('resize', resize);
resize();

const clock = new THREE.Clock();
const animationQuery = new URLSearchParams(window.location.search);
renderer.setAnimationLoop(() => {
  model.userData.tick(resolveAnimationTime(clock.getElapsedTime(), animationQuery));
  controls.update();
  renderer.render(scene, camera);
});
