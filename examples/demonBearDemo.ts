import * as THREE from 'three';
import { createDemonBearModel, animateDemonBear } from '../src/createDemonBearModel';

/**
 * Demo: Render the demon bear character in an interactive Three.js scene
 */
export function setupDemonBearDemo(): {
  scene: THREE.Scene;
  renderer: THREE.WebGLRenderer;
  camera: THREE.PerspectiveCamera;
  model: THREE.Group;
} {
  // Scene setup
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x1a1a2e);
  scene.fog = new THREE.Fog(0x1a1a2e, 10, 50);

  // Camera
  const camera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.1, 1000);
  camera.position.set(0, 1.5, 4);
  camera.lookAt(0, 1, 0);

  // Renderer
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowShadowMap;
  document.body.appendChild(renderer.domElement);

  // Lighting
  const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
  scene.add(ambientLight);

  const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8);
  directionalLight.position.set(5, 8, 5);
  directionalLight.castShadow = true;
  directionalLight.shadow.mapSize.width = 2048;
  directionalLight.shadow.mapSize.height = 2048;
  directionalLight.shadow.camera.far = 50;
  directionalLight.shadow.camera.left = -10;
  directionalLight.shadow.camera.right = 10;
  directionalLight.shadow.camera.top = 10;
  directionalLight.shadow.camera.bottom = -10;
  scene.add(directionalLight);

  const pointLight = new THREE.PointLight(0xff6666, 0.5, 20);
  pointLight.position.set(-3, 2, 3);
  scene.add(pointLight);

  // Ground plane
  const groundGeom = new THREE.PlaneGeometry(20, 20);
  const groundMat = new THREE.MeshStandardMaterial({
    color: 0x2a2a3e,
    roughness: 0.8,
  });
  const ground = new THREE.Mesh(groundGeom, groundMat);
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -2;
  ground.receiveShadow = true;
  scene.add(ground);

  // Create demon bear model
  const model = createDemonBearModel({ scale: 1.5, animationSpeed: 0.8 });
  model.position.set(0, -0.5, 0);
  scene.add(model);

  // Animation loop
  let lastTime = Date.now();
  const animate = () => {
    requestAnimationFrame(animate);

    const currentTime = Date.now();
    const elapsed = (currentTime - lastTime) / 1000;
    lastTime = currentTime;

    // Animate model
    animateDemonBear(model, elapsed);

    // Orbit camera with mouse
    const canvas = renderer.domElement;
    const rect = canvas.getBoundingClientRect();
    const x = (event?.clientX || 0) - rect.left;
    const y = (event?.clientY || 0) - rect.top;
    const mx = (x / canvas.clientWidth) * 2 - 1;
    const my = -(y / canvas.clientHeight) * 2 + 1;

    if (Math.abs(mx) < 1 && Math.abs(my) < 1) {
      const angle = Math.atan2(mx, 1);
      const distance = 4;
      camera.position.x = Math.sin(angle) * distance;
      camera.position.z = Math.cos(angle) * distance;
      camera.position.y = 1.5 + my * 0.5;
      camera.lookAt(0, 1, 0);
    }

    renderer.render(scene, camera);
  };

  animate();

  // Handle window resize
  window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });

  return { scene, renderer, camera, model };
}

// Auto-start if this is the main module
if (typeof window !== 'undefined') {
  window.addEventListener('load', () => {
    setupDemonBearDemo();
  });
}
