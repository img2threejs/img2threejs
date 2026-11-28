import * as THREE from 'three';
import { GLTFLoader } from '/deps/loaders/GLTFLoader.js';

// No plugin factory, HTML, script, animation callback or URL is evaluated here.
globalThis.__DELIVERED_CAPTURE__ = (async () => {
  const response = await fetch('/config.json', {cache: 'no-store'});
  if (!response.ok) throw new Error('config-fetch-failed');
  const config = await response.json();
  const modelResponse = await fetch('/delivery.glb', {cache: 'no-store'});
  if (!modelResponse.ok) throw new Error('glb-fetch-failed');
  const bytes = await modelResponse.arrayBuffer();
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
  if (digest !== config.deliverySha256) throw new Error('glb-hash-mismatch');
  const manager = new THREE.LoadingManager();
  let resourceLoadFailed = false;
  // GLTFLoader can resolve with a null texture after an image loader error.
  manager.onError = () => { resourceLoadFailed = true; };
  manager.setURLModifier(url => {
    if (!url.startsWith('blob:') && !/^data:image\/(png|jpeg);base64,/.test(url)) throw new Error('external-resource-forbidden');
    return url;
  });
  const gltf = await new GLTFLoader(manager).parseAsync(bytes, '');
  if (resourceLoadFailed) throw new Error('glb-resource-load-failed');
  if (!gltf.scene) throw new Error('missing-gltf-scene');
  const renderer = new THREE.WebGLRenderer({antialias: false, alpha: false, preserveDrawingBuffer: true, powerPreference: 'low-power'});
  renderer.debug.onShaderError = () => { throw new Error('shader-compilation-failed'); };
  renderer.setPixelRatio(1);
  renderer.setSize(config.viewport.width, config.viewport.height, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NoToneMapping;
  renderer.shadowMap.enabled = false;
  document.body.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(config.background);
  const camera = new THREE.PerspectiveCamera(config.camera.fov, config.viewport.width / config.viewport.height, config.camera.near, config.camera.far);
  camera.position.fromArray(config.camera.position);
  camera.up.fromArray(config.camera.up);
  camera.lookAt(...config.camera.target);
  const parent = new THREE.Group();
  parent.position.fromArray(config.transform.position);
  parent.rotation.set(...config.transform.rotation);
  parent.scale.fromArray(config.effectiveScale);
  parent.add(gltf.scene);
  scene.add(parent);
  scene.add(new THREE.AmbientLight(config.lighting.ambient.color, config.lighting.ambient.intensity));
  for (const item of config.lighting.directional) {
    const light = new THREE.DirectionalLight(item.color, item.intensity);
    light.position.fromArray(item.position);
    light.target.position.fromArray(item.target);
    scene.add(light, light.target);
  }
  renderer.render(scene, camera);
  const gl = renderer.getContext();
  gl.finish();
  if (gl.isContextLost() || gl.getError() !== gl.NO_ERROR) throw new Error('webgl-render-failed');
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const size = renderer.getDrawingBufferSize(new THREE.Vector2());
  const extension = gl.getExtension('WEBGL_debug_renderer_info');
  return {
    deliverySha256: digest,
    threeRevision: THREE.REVISION,
    viewport: {width: innerWidth, height: innerHeight, devicePixelRatio},
    canvas: {width: size.x, height: size.y},
    camera: {projection: 'perspective', aspect: camera.aspect, fov: camera.fov, near: camera.near, far: camera.far,
      position: camera.position.toArray(), up: camera.up.toArray(), target: config.camera.target},
    frame: config.frame,
    renderer: {name: 'WebGLRenderer', version: gl.getParameter(gl.VERSION),
      vendor: gl.getParameter(gl.VENDOR), renderer: gl.getParameter(gl.RENDERER),
      unmaskedRenderer: extension ? gl.getParameter(extension.UNMASKED_RENDERER_WEBGL) : null,
      antialias: false, outputColorSpace: renderer.outputColorSpace, toneMapping: renderer.toneMapping,
      shadows: false, animation: 'authored-rest-pose'},
    drawCalls: renderer.info.render.calls,
    triangles: renderer.info.render.triangles,
    animationClipCount: gltf.animations.length
  };
})();
