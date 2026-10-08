import * as THREE from 'three';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { BokehPass } from 'three/examples/jsm/postprocessing/BokehPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

export type ProceduralModelOptions = {
  wireframe?: boolean;
  castShadow?: boolean;
  receiveShadow?: boolean;
  textureSize?: number;
  textureAnisotropy?: number;
  qualityPriority?: 'reference-fidelity' | 'balanced';
};

export type ProceduralModelRuntime = {
  nodes: Record<string, THREE.Object3D>;
  meshes: Record<string, THREE.Mesh>;
  sockets: Record<string, THREE.Object3D>;
  colliders: Record<string, unknown>;
  destructionGroups: Record<string, THREE.Object3D[]>;
};

type SculptMaterialSpec = Record<string, any>;

function hashString(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function readLayerNumber(value: unknown, keys: string[], fallback: number): number {
  if (typeof value === 'number') return value;
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    for (const key of keys) {
      if (typeof record[key] === 'number') return record[key] as number;
    }
  }
  return fallback;
}

function hexToRgb(hex: string): [number, number, number] {
  const normalized = /^#[0-9a-f]{3}$/i.test(hex)
    ? '#' + hex.slice(1).split('').map((part) => part + part).join('')
    : hex;
  const value = /^#[0-9a-f]{6}$/i.test(normalized) ? Number.parseInt(normalized.slice(1), 16) : 0x8a7a5f;
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

function materialPalette(spec: SculptMaterialSpec): string[] {
  const palette = spec.colorVariation?.palette;
  if (Array.isArray(palette) && palette.length > 0) return palette.filter((value) => typeof value === 'string');
  const secondary = spec.albedo?.secondary;
  const colors = [spec.baseColor ?? spec.color ?? spec.albedo?.dominant, ...(Array.isArray(secondary) ? secondary : [])];
  return colors.filter((value): value is string => typeof value === 'string' && value.startsWith('#'));
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

function smoothCurve(value: number): number {
  return value * value * (3 - 2 * value);
}

function periodicHash(x: number, y: number, seed: number, periodX: number, periodY: number): number {
  const wrappedX = ((x % periodX) + periodX) % periodX;
  const wrappedY = ((y % periodY) + periodY) % periodY;
  let value = Math.imul(wrappedX + seed * 17, 374761393) ^ Math.imul(wrappedY + seed * 31, 668265263);
  value = Math.imul(value ^ (value >>> 13), 1274126177);
  return ((value ^ (value >>> 16)) >>> 0) / 4294967295;
}

function periodicValueNoise(u: number, v: number, seed: number, periodX: number, periodY: number): number {
  const x = u * periodX;
  const y = v * periodY;
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const tx = smoothCurve(x - x0);
  const ty = smoothCurve(y - y0);
  const a = periodicHash(x0, y0, seed, periodX, periodY);
  const b = periodicHash(x0 + 1, y0, seed, periodX, periodY);
  const c = periodicHash(x0, y0 + 1, seed, periodX, periodY);
  const d = periodicHash(x0 + 1, y0 + 1, seed, periodX, periodY);
  return THREE.MathUtils.lerp(THREE.MathUtils.lerp(a, b, tx), THREE.MathUtils.lerp(c, d, tx), ty);
}

type SurfaceBand = {
  frequency: number;
  amplitude: number;
  stretchX: number;
  stretchY: number;
  ridge: boolean;
};

function surfaceBands(spec: SculptMaterialSpec): SurfaceBand[] {
  const source = Array.isArray(spec.surfaceFrequencyBands) ? spec.surfaceFrequencyBands : [];
  const parsed = source.flatMap((item: unknown) => {
    if (!item || typeof item !== 'object') return [];
    const band = item as Record<string, unknown>;
    const frequency = typeof band.frequency === 'number' ? band.frequency : 0;
    const amplitude = typeof band.amplitude === 'number' ? band.amplitude : 0;
    if (frequency <= 0 || amplitude <= 0) return [];
    const stretch = Array.isArray(band.stretch) ? band.stretch : [1, 1];
    const description = `${String(band.pattern ?? '')} ${String(band.role ?? '')}`.toLowerCase();
    return [{
      frequency,
      amplitude,
      stretchX: typeof stretch[0] === 'number' ? Math.max(0.1, stretch[0]) : 1,
      stretchY: typeof stretch[1] === 'number' ? Math.max(0.1, stretch[1]) : 1,
      ridge: /(ridge|groove|grain|fiber|striated|crack)/.test(description),
    }];
  });
  return parsed.length > 0 ? parsed : [
    { frequency: 2, amplitude: 0.42, stretchX: 1, stretchY: 1, ridge: false },
    { frequency: 12, amplitude: 0.22, stretchX: 1, stretchY: 1, ridge: false },
    { frequency: 56, amplitude: 0.08, stretchX: 1, stretchY: 1, ridge: false },
  ];
}

function sampleSurface(u: number, v: number, bands: SurfaceBand[], seed: number): number {
  let value = 0;
  let weight = 0;
  for (let index = 0; index < bands.length; index += 1) {
    const band = bands[index];
    const periodX = Math.max(1, Math.round(band.frequency * band.stretchX));
    const periodY = Math.max(1, Math.round(band.frequency * band.stretchY));
    let sample = periodicValueNoise(u, v, seed + index * 1013, periodX, periodY);
    if (band.ridge) sample = 1 - Math.abs(sample * 2 - 1);
    value += sample * band.amplitude;
    weight += band.amplitude;
  }
  return weight > 0 ? clamp01(value / weight) : 0.5;
}

function mixPalette(colors: [number, number, number][], value: number): [number, number, number] {
  if (colors.length === 1) return colors[0];
  const scaled = clamp01(value) * (colors.length - 1);
  const index = Math.min(colors.length - 2, Math.floor(scaled));
  const mix = scaled - index;
  const a = colors[index];
  const b = colors[index + 1];
  return [
    Math.round(THREE.MathUtils.lerp(a[0], b[0], mix)),
    Math.round(THREE.MathUtils.lerp(a[1], b[1], mix)),
    Math.round(THREE.MathUtils.lerp(a[2], b[2], mix)),
  ];
}

type ColorGradientStop = { offset: number; color: string };
type ColorGradientSpec = {
  type: 'linear' | 'radial';
  axis: [number, number];
  stops: ColorGradientStop[];
};

function parseRgba(value: string): [number, number, number] {
  const match = /rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/.exec(value);
  if (!match) return [138, 122, 95];
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

// Analytical per-pixel gradient sample. The extraction schema's colorGradient carries
// exact rgba(...) stop colors (see extract_part_color_recipe.py), so this samples the
// same trend directly in JS math rather than round-tripping through a Canvas 2D
// createLinearGradient/createRadialGradient object — same visual result, and it composes
// directly with the existing noise/height-correlated colorVariation blend below.
function sampleColorGradient(gradient: ColorGradientSpec, u: number, v: number): [number, number, number] {
  const stops = gradient.stops.length >= 2 ? gradient.stops : [{ offset: 0, color: 'rgba(138,122,95,1)' }, { offset: 1, color: 'rgba(138,122,95,1)' }];
  let t: number;
  if (gradient.type === 'radial') {
    const [cx, cy] = gradient.axis;
    const dx = u - cx;
    const dy = v - cy;
    const maxRadius = Math.max(0.001, Math.hypot(Math.max(cx, 1 - cx), Math.max(cy, 1 - cy)));
    t = clamp01(Math.hypot(dx, dy) / maxRadius);
  } else {
    const [ax, ay] = gradient.axis;
    const projection = (u - 0.5) * ax + (v - 0.5) * ay;
    const maxProjection = 0.5 * (Math.abs(ax) + Math.abs(ay)) || 0.5;
    t = clamp01(projection / maxProjection + 0.5);
  }
  const scaled = t * (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.max(0, Math.floor(scaled)));
  const mix = scaled - index;
  const a = parseRgba(stops[index].color);
  const b = parseRgba(stops[index + 1].color);
  return [
    THREE.MathUtils.lerp(a[0], b[0], mix),
    THREE.MathUtils.lerp(a[1], b[1], mix),
    THREE.MathUtils.lerp(a[2], b[2], mix),
  ];
}

function writePixel(data: Uint8ClampedArray, offset: number, red: number, green: number, blue: number): void {
  data[offset] = Math.max(0, Math.min(255, Math.round(red)));
  data[offset + 1] = Math.max(0, Math.min(255, Math.round(green)));
  data[offset + 2] = Math.max(0, Math.min(255, Math.round(blue)));
  data[offset + 3] = 255;
}

function makeCanvas(size: number): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  return canvas;
}

function createMapTexture(
  canvas: HTMLCanvasElement,
  colorSpace: THREE.ColorSpace,
  spec: SculptMaterialSpec,
  options: ProceduralModelOptions,
): THREE.CanvasTexture {
  const texture = new THREE.CanvasTexture(canvas);
  const projection = spec.textureProjection && typeof spec.textureProjection === 'object' ? spec.textureProjection : {};
  const repeat = Array.isArray(projection.repeat) ? projection.repeat : [2, 2];
  texture.colorSpace = colorSpace;
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(
    typeof repeat[0] === 'number' ? repeat[0] : 2,
    typeof repeat[1] === 'number' ? repeat[1] : 2,
  );
  texture.anisotropy = Math.max(1, Math.round(options.textureAnisotropy ?? projection.anisotropy ?? 8));
  texture.needsUpdate = true;
  return texture;
}

type ProceduralTextureSet = {
  albedo: THREE.Texture;
  roughness: THREE.Texture;
  height: THREE.Texture;
  normal: THREE.Texture;
  ao: THREE.Texture;
  source: 'reference-pixel-extraction' | 'procedural';
};

function referenceMapUrl(spec: SculptMaterialSpec, channel: string): string | null {
  const reference = spec.referencePbr;
  if (!reference || typeof reference !== 'object') return null;
  if (reference.usable === false) return null;
  const confidence = typeof reference.confidence === 'number'
    ? reference.confidence
    : (typeof reference.estimatedFidelity === 'number' ? reference.estimatedFidelity : 0);
  const threshold = typeof reference.targetThreshold === 'number' ? reference.targetThreshold : 0.7;
  if (confidence < threshold) return null;
  const maps = reference.maps;
  if (!maps || typeof maps !== 'object') return null;
  const map = (maps as Record<string, unknown>)[channel];
  if (!map || typeof map !== 'object') return null;
  const record = map as Record<string, unknown>;
  const url = typeof record.url === 'string' && record.url.trim() ? record.url : record.path;
  return typeof url === 'string' && url.trim() ? url : null;
}

function createLoadedMapTexture(
  url: string,
  colorSpace: THREE.ColorSpace,
  spec: SculptMaterialSpec,
  options: ProceduralModelOptions,
): THREE.Texture {
  const texture = new THREE.TextureLoader().load(url);
  const projection = spec.textureProjection && typeof spec.textureProjection === 'object' ? spec.textureProjection : {};
  const repeat = Array.isArray(projection.repeat) ? projection.repeat : [1, 1];
  texture.colorSpace = colorSpace;
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(
    typeof repeat[0] === 'number' ? repeat[0] : 1,
    typeof repeat[1] === 'number' ? repeat[1] : 1,
  );
  texture.anisotropy = Math.max(1, Math.round(options.textureAnisotropy ?? projection.anisotropy ?? 8));
  texture.needsUpdate = true;
  return texture;
}

function makeReferenceTextureSet(spec: SculptMaterialSpec, options: ProceduralModelOptions): ProceduralTextureSet | null {
  const albedo = referenceMapUrl(spec, 'albedo');
  const roughness = referenceMapUrl(spec, 'roughness');
  const height = referenceMapUrl(spec, 'height');
  const normal = referenceMapUrl(spec, 'normal');
  const ao = referenceMapUrl(spec, 'ao');
  if (!albedo || !roughness || !height || !normal || !ao) return null;
  return {
    albedo: createLoadedMapTexture(albedo, THREE.SRGBColorSpace, spec, options),
    roughness: createLoadedMapTexture(roughness, THREE.NoColorSpace, spec, options),
    height: createLoadedMapTexture(height, THREE.NoColorSpace, spec, options),
    normal: createLoadedMapTexture(normal, THREE.NoColorSpace, spec, options),
    ao: createLoadedMapTexture(ao, THREE.NoColorSpace, spec, options),
    source: 'reference-pixel-extraction',
  };
}

function makeProceduralTextureSet(
  id: string,
  spec: SculptMaterialSpec,
  options: ProceduralModelOptions,
): ProceduralTextureSet | null {
  if (typeof document === 'undefined') return null;
  const qualityFirst = (options.qualityPriority ?? 'reference-fidelity') === 'reference-fidelity';
  const requested = options.textureSize ?? spec.textureResolution;
  const requestedSize = typeof requested === 'number' && Number.isFinite(requested)
    ? requested
    : (qualityFirst ? 1024 : 512);
  const size = Math.max(256, Math.min(2048, 2 ** Math.round(Math.log2(requestedSize))));
  const canvases = {
    albedo: makeCanvas(size),
    roughness: makeCanvas(size),
    height: makeCanvas(size),
    normal: makeCanvas(size),
    ao: makeCanvas(size),
  };
  const contexts = {
    albedo: canvases.albedo.getContext('2d'),
    roughness: canvases.roughness.getContext('2d'),
    height: canvases.height.getContext('2d'),
    normal: canvases.normal.getContext('2d'),
    ao: canvases.ao.getContext('2d'),
  };
  if (!contexts.albedo || !contexts.roughness || !contexts.height || !contexts.normal || !contexts.ao) return null;
  const images = {
    albedo: contexts.albedo.createImageData(size, size),
    roughness: contexts.roughness.createImageData(size, size),
    height: contexts.height.createImageData(size, size),
    normal: contexts.normal.createImageData(size, size),
    ao: contexts.ao.createImageData(size, size),
  };
  const seed = hashString(id);
  const bands = surfaceBands(spec);
  const heightField = new Float32Array(size * size);
  const roughnessField = new Float32Array(size * size);
  const palette = materialPalette(spec);
  const fallback = typeof spec.baseColor === 'string' ? spec.baseColor : '#8A7A5F';
  const colors = (palette.length >= 2 ? palette : [fallback, '#6E614B', '#A08F70']).map(hexToRgb);
  const baseRoughness = clamp01(readLayerNumber(spec.roughness, ['base'], 0.76));
  const roughnessVariation = clamp01(readLayerNumber(spec.roughness, ['variation'], 0.18));
  const colorAmplitude = clamp01(readLayerNumber(spec.colorVariation, ['amplitude', 'variation'], 0.18));
  const heightCorrelation = clamp01(readLayerNumber(spec.colorVariation, ['heightCorrelation'], 0.3));
  const colorGradient: ColorGradientSpec | undefined = spec.colorGradient;
  for (let y = 0; y < size; y += 1) {
    const v = y / size;
    for (let x = 0; x < size; x += 1) {
      const u = x / size;
      const index = y * size + x;
      const height = sampleSurface(u, v, bands, seed + 101);
      const roughNoise = sampleSurface(u, v, bands, seed + 7001);
      const colorNoise = sampleSurface(u, v, bands, seed + 15013);
      heightField[index] = height;
      roughnessField[index] = clamp01(baseRoughness + (roughNoise - 0.5) * roughnessVariation * 2);
      let color: [number, number, number];
      if (colorGradient) {
        // Evidence-derived spatial gradient (Plan 1.3 Workstream C) takes priority
        // over the noise-based palette blend below — it is a measured trend, not a guess.
        color = sampleColorGradient(colorGradient, u, v);
      } else {
        const paletteValue = clamp01(
          0.5 + (colorNoise - 0.5) * colorAmplitude * 2 + (height - 0.5) * heightCorrelation
        );
        color = mixPalette(colors, paletteValue);
      }
      writePixel(images.albedo.data, index * 4, color[0], color[1], color[2]);
    }
  }
  const normalStrength = Math.max(0.05, readLayerNumber(spec.normal, ['strength', 'amplitude'], 0.35));
  const aoStrength = clamp01(readLayerNumber(spec.ambientOcclusion, ['cavityStrength', 'strength'], 0.35));
  for (let y = 0; y < size; y += 1) {
    const up = ((y - 1 + size) % size) * size;
    const down = ((y + 1) % size) * size;
    for (let x = 0; x < size; x += 1) {
      const left = (x - 1 + size) % size;
      const right = (x + 1) % size;
      const index = y * size + x;
      const center = heightField[index];
      const dx = (heightField[y * size + right] - heightField[y * size + left]) * normalStrength * 6;
      const dy = (heightField[down + x] - heightField[up + x]) * normalStrength * 6;
      const inverseLength = 1 / Math.sqrt(dx * dx + dy * dy + 1);
      const normalX = -dx * inverseLength;
      const normalY = -dy * inverseLength;
      const normalZ = inverseLength;
      const neighborAverage = (
        heightField[y * size + left] + heightField[y * size + right]
        + heightField[up + x] + heightField[down + x]
      ) * 0.25;
      const cavity = Math.max(0, neighborAverage - center);
      const ao = clamp01(1 - aoStrength * (cavity * 12 + (1 - center) * 0.16));
      const offset = index * 4;
      const heightByte = center * 255;
      const roughnessByte = roughnessField[index] * 255;
      writePixel(images.height.data, offset, heightByte, heightByte, heightByte);
      writePixel(images.roughness.data, offset, roughnessByte, roughnessByte, roughnessByte);
      writePixel(
        images.normal.data, offset,
        (normalX * 0.5 + 0.5) * 255,
        (normalY * 0.5 + 0.5) * 255,
        (normalZ * 0.5 + 0.5) * 255,
      );
      writePixel(images.ao.data, offset, ao * 255, ao * 255, ao * 255);
    }
  }
  contexts.albedo.putImageData(images.albedo, 0, 0);
  contexts.roughness.putImageData(images.roughness, 0, 0);
  contexts.height.putImageData(images.height, 0, 0);
  contexts.normal.putImageData(images.normal, 0, 0);
  contexts.ao.putImageData(images.ao, 0, 0);
  return {
    albedo: createMapTexture(canvases.albedo, THREE.SRGBColorSpace, spec, options),
    roughness: createMapTexture(canvases.roughness, THREE.NoColorSpace, spec, options),
    height: createMapTexture(canvases.height, THREE.NoColorSpace, spec, options),
    normal: createMapTexture(canvases.normal, THREE.NoColorSpace, spec, options),
    ao: createMapTexture(canvases.ao, THREE.NoColorSpace, spec, options),
    source: 'procedural',
  };
}

function createSculptMaterial(id: string, spec: SculptMaterialSpec, options: ProceduralModelOptions): THREE.MeshPhysicalMaterial {
  const textures = makeReferenceTextureSet(spec, options) ?? makeProceduralTextureSet(id, spec, options);
  const material = new THREE.MeshPhysicalMaterial({
    color: textures ? 0xffffff : new THREE.Color(typeof spec.baseColor === 'string' ? spec.baseColor : '#8A7A5F'),
    roughness: textures ? 1 : clamp01(readLayerNumber(spec.roughness, ['base'], 0.76)),
    metalness: clamp01(readLayerNumber(spec.metalness, ['base'], 0.0)),
    clearcoat: clamp01(readLayerNumber(spec.clearcoat, ['base', 'amount'], 0)),
    clearcoatRoughness: clamp01(readLayerNumber(spec.clearcoatRoughness, ['base'], 0.25)),
    transmission: clamp01(readLayerNumber(spec.transmission, ['base', 'amount'], 0)),
    ior: Math.max(1, readLayerNumber(spec.ior, ['base', 'value'], 1.5)),
    thickness: Math.max(0, readLayerNumber(spec.thickness, ['base', 'amount'], 0)),
    attenuationDistance: Math.max(0.001, readLayerNumber(spec.attenuationDistance, ['base', 'value'], Infinity)),
    attenuationColor: new THREE.Color(typeof spec.attenuationColor === 'string' ? spec.attenuationColor : '#ffffff'),
    sheen: clamp01(readLayerNumber(spec.sheen, ['base', 'amount'], 0)),
    sheenColor: new THREE.Color(typeof spec.sheenColor === 'string' ? spec.sheenColor : '#ffffff'),
    sheenRoughness: clamp01(readLayerNumber(spec.sheenRoughness, ['base'], 1.0)),
    iridescence: clamp01(readLayerNumber(spec.iridescence, ['base', 'amount'], 0)),
    iridescenceIOR: Math.max(1, readLayerNumber(spec.iridescenceIOR, ['base', 'value'], 1.3)),
    anisotropy: clamp01(readLayerNumber(spec.anisotropy, ['base', 'amount'], 0)),
    anisotropyRotation: readLayerNumber(spec.anisotropy, ['rotation'], 0),
    specularIntensity: clamp01(readLayerNumber(spec.specularIntensity, ['base'], 1.0)),
    specularColor: new THREE.Color(typeof spec.specularColor === 'string' ? spec.specularColor : '#ffffff'),
    emissive: new THREE.Color(typeof spec.emissive === 'string' ? spec.emissive : '#000000'),
    emissiveIntensity: Math.max(0, readLayerNumber(spec.emissiveIntensity, ['base'], 1.0)),
    opacity: clamp01(readLayerNumber(spec.opacity, ['base'], 1)),
    transparent: readLayerNumber(spec.transmission, ['base', 'amount'], 0) > 0 || readLayerNumber(spec.opacity, ['base'], 1) < 1,
    alphaTest: Math.max(0, readLayerNumber(spec.alpha, ['cutoff', 'alphaTest'], 0)),
    wireframe: options.wireframe ?? false,
    side: spec.doubleSided === true ? THREE.DoubleSide : THREE.FrontSide,
  });
  if (textures) {
    material.map = textures.albedo;
    material.roughnessMap = textures.roughness;
    material.normalMap = textures.normal;
    material.normalScale.setScalar(Math.max(0.05, readLayerNumber(spec.normal, ['strength', 'amplitude'], 0.35)));
    material.aoMap = textures.ao;
    material.aoMap.channel = 0;
    material.aoMapIntensity = readLayerNumber(spec.ambientOcclusion, ['cavityStrength', 'strength'], 0.35);
    const bumpScale = Math.max(0, readLayerNumber(spec.bump, ['amplitude', 'strength'], 0));
    if (bumpScale > 0) {
      material.bumpMap = textures.height;
      material.bumpScale = bumpScale;
    }
    const displacementScale = Math.max(0, readLayerNumber(spec.displacement, ['amplitude', 'strength'], 0));
    if (displacementScale > 0) {
      material.displacementMap = textures.height;
      material.displacementScale = displacementScale;
      material.displacementBias = -displacementScale * 0.5;
    }
  }
  material.envMapIntensity = readLayerNumber(spec, ['envMapIntensity'], 0.8);
  material.userData.sculptMaterial = spec;
  material.userData.proceduralMapsIndependent = true;
  material.userData.pbrTextureSource = textures?.source ?? 'flat-fallback';
  material.userData.referencePbr = spec.referencePbr ?? null;
  material.needsUpdate = true;
  return material;
}

type AttachmentEndpoint = {
  start: THREE.Vector3;
  midpoint: THREE.Vector3;
  quaternion: THREE.Quaternion;
  length: number;
  baseRadius: number;
  endRadius: number;
};

function readVector3(value: unknown, fallback: [number, number, number]): THREE.Vector3 {
  if (Array.isArray(value) && value.length === 3 && value.every((item) => typeof item === 'number')) {
    return new THREE.Vector3(value[0], value[1], value[2]);
  }
  return new THREE.Vector3(fallback[0], fallback[1], fallback[2]);
}

function readNumber(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function makeAttachmentEndpoint(attachment: unknown): AttachmentEndpoint | null {
  if (!attachment || typeof attachment !== 'object') return null;
  const record = attachment as Record<string, unknown>;
  const start = readVector3(record.localStart, [0, 0, 0]);
  const end = readVector3(record.localEnd, [0, 1, 0]);
  const delta = end.clone().sub(start);
  const length = delta.length();
  if (length <= 0.0001) return null;
  const direction = delta.clone().normalize();
  const quaternion = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction);
  const baseRadius = Math.max(0.005, readNumber(record.baseRadius, 0.06));
  const endRadius = Math.max(0.003, readNumber(record.endRadius, baseRadius * 0.55));
  return {
    start,
    midpoint: delta.multiplyScalar(0.5),
    quaternion,
    length,
    baseRadius,
    endRadius,
  };
}

// Generated from ObjectSculptSpec target: Langstroth Beehive
// Sculpt build pass: blockout
// This factory is intentionally pass-gated. Finish browser screenshot review before unlocking deeper passes.
export function createLangstrothBeehiveModel(options: ProceduralModelOptions = {}): THREE.Group {
  const root = new THREE.Group();
  root.name = "Langstroth Beehive";
  root.userData.reconstructionEvidence = {"itemFamily": null, "subtype": null, "componentAdapter": null, "route": null, "exactnessTier": null, "referenceCamera": {"solved": false, "fovDegrees": 35.0, "aspect": 1.0, "orientation": {"yaw": 28.0, "pitch": -8.0, "roll": 0.0}, "positionHint": [1.35, 1.05, 2.4], "targetHint": [0.0, 0.5760000000000002, 0.0], "note": "Approximate three-quarter studio camera matching reference framing."}, "approximationNotes": []};

  const materialMap: Record<string, THREE.Material> = {};
  materialMap["varnished-pine"] = createSculptMaterial(
    "varnished-pine",
    {"id": "varnished-pine", "name": "Varnished softwood (pine)", "type": "standard", "shaderModel": "MeshPhysicalMaterial", "baseColor": "#C9A06A", "color": "#C9A06A", "albedo": {"dominant": "#936C47", "secondary": ["#BD8E5B", "#C5A177", "#D0CEC8"], "samplingNotes": "Reference-derived from foreground pixels; de-lit to reduce baked shadows/highlights.", "map": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_pine\\varnished-pine_albedo.png", "url": "varnished-pine_albedo.png", "channel": "albedo", "source": "reference-pixel-extraction"}}, "colorVariation": {"palette": ["#936C47", "#BD8E5B", "#C5A177", "#D0CEC8", "#573A1F"], "pattern": "reference-derived pixel palette", "amplitude": 0.219, "heightCorrelation": 0.42}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [2.0, 3.0], "anisotropy": 8, "texelDensityIntent": "Grain runs vertical on box faces; end-grain on finger joints."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 2.0, "amplitude": 0.463, "role": "reference-derived broad albedo and height breakup"}, {"id": "meso", "frequency": 14.0, "amplitude": 0.35, "role": "reference-derived cracks, ridges, pores, grain, or leaf clusters"}, {"id": "micro", "frequency": 72.0, "amplitude": 0.14, "role": "reference-derived micro highlight breakup under grazing light"}], "roughness": {"base": 0.701, "variation": 0.094, "map": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_pine\\varnished-pine_roughness.png", "url": "varnished-pine_roughness.png", "channel": "roughness", "source": "reference-pixel-extraction"}, "localResponse": "reference-derived roughness estimate; cavities and textured zones trend rougher, bright highlights trend smoother"}, "metalness": {"base": 0.0, "variation": 0.0}, "normal": {"pattern": "reference-derived height-gradient normal map", "strength": 0.218, "map": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_pine\\varnished-pine_normal.png", "url": "varnished-pine_normal.png", "channel": "normal", "source": "reference-pixel-extraction"}, "heightSource": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_pine\\varnished-pine_height.png", "url": "varnished-pine_height.png", "channel": "height", "source": "reference-pixel-extraction"}, "space": "tangent"}, "bump": {"pattern": "reference-derived height field", "amplitude": 0.024, "map": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_pine\\varnished-pine_height.png", "url": "varnished-pine_height.png", "channel": "height", "source": "reference-pixel-extraction"}}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.38, "contactShadowBias": 0.35, "map": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_pine\\varnished-pine_ao.png", "url": "varnished-pine_ao.png", "channel": "ao", "source": "reference-pixel-extraction"}, "notes": "Reference-derived cavity estimate from local height minima; verify against grazing-light screenshot."}, "wear": {"edgeWear": 0.15, "scratches": [{"direction": "random-shallow", "density": 0.08}], "chips": []}, "dirt": {"amount": 0.08, "cavityBias": 0.55, "color": "#4A3A28"}, "clearcoat": 0.35, "clearcoatRoughness": 0.25, "localOverrides": [{"id": "end-grain-fingers", "region": "finger-joint-corners", "albedo": "#8B6340", "roughness": 0.62, "notes": "Darker end-grain fingers at box joints."}, {"id": "handhold-cavity", "region": "recessed-handholds", "roughness": 0.7, "aoBoost": 0.35, "notes": "Matte recessed wood inside hand holds."}, {"id": "seam-dirt", "region": "super-stack-seams", "dirt": 0.2, "notes": "Slight grime line between stacked boxes."}, {"id": "reference-pbr-pixel-evidence", "type": "material-map-evidence", "evidenceRefs": ["full-object"], "channels": ["albedo", "roughness", "height", "normal", "ambient-occlusion"], "notes": "Use generated maps as material evidence, then refine after browser screenshot comparison."}], "referencePbr": {"version": "1.0", "sourceImage": "C:\\Users\\ifranjo\\scripts\\imag_3js\\refs\\langstroth_hive_gen.png", "extractor": "stage1_intake/extract_pbr_evidence.py", "method": "single-image pixel evidence with de-lighting estimate; not photogrammetry", "usable": true, "verdict": "pass", "confidence": 0.86, "estimatedFidelity": 0.86, "targetThreshold": 0.5, "hardLimit": "A single image cannot uniquely recover true albedo/roughness/normal/AO; maps are reference-derived estimates.", "maps": {"albedo": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_pine\\varnished-pine_albedo.png", "url": "varnished-pine_albedo.png", "channel": "albedo", "source": "reference-pixel-extraction"}, "roughness": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_pine\\varnished-pine_roughness.png", "url": "varnished-pine_roughness.png", "channel": "roughness", "source": "reference-pixel-extraction"}, "height": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_pine\\varnished-pine_height.png", "url": "varnished-pine_height.png", "channel": "height", "source": "reference-pixel-extraction"}, "normal": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_pine\\varnished-pine_normal.png", "url": "varnished-pine_normal.png", "channel": "normal", "source": "reference-pixel-extraction"}, "ao": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_pine\\varnished-pine_ao.png", "url": "varnished-pine_ao.png", "channel": "ao", "source": "reference-pixel-extraction"}}, "diagnostics": {"sourceWidth": 1024, "sourceHeight": 1024, "mapSize": 1024, "cropBBoxPixels": {"x": 152, "y": 78, "width": 872, "height": 898}, "mask": {"backgroundColor": "#F0F2F0", "backgroundNoise": 9.695, "transparentPixelFraction": 0.0, "foregroundCoverage": 0.5645}, "mapStats": {"valueRange": 0.5216, "heightP90Gradient": 0.05228, "roughnessBase": 0.701, "roughnessVariation": 0.094, "normalStrength": 0.218, "blurRadius": 21}, "palette": ["#936C47", "#BD8E5B", "#C5A177", "#D0CEC8", "#573A1F"]}, "warnings": ["single-image inverse rendering cannot prove true physical PBR; confidence is capped"]}, "shaderNotes": ["MeshPhysicalMaterial with mild clearcoat for varnish sheen.", "Independent roughness/AO maps; do not reuse albedo as roughness.", "Reference-derived maps are estimates from image pixels; verify with neutral, grazing, and reference-matched renders.", "Do not treat baked image shadows as final albedo; rerun extraction with a tighter material crop if highlights/shadows pollute the maps."]},
    options
  );
  materialMap["galvanized-steel"] = createSculptMaterial(
    "galvanized-steel",
    {"id": "galvanized-steel", "name": "Galvanized / painted steel hardware", "type": "standard", "shaderModel": "MeshStandardMaterial", "baseColor": "#A8B0B5", "color": "#A8B0B5", "albedo": {"dominant": "#936C47", "secondary": ["#BD8E5B", "#C5A177", "#D0CEC8"], "samplingNotes": "Reference-derived from foreground pixels; de-lit to reduce baked shadows/highlights.", "map": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_metal\\galvanized-steel_albedo.png", "url": "galvanized-steel_albedo.png", "channel": "albedo", "source": "reference-pixel-extraction"}}, "colorVariation": {"palette": ["#936C47", "#BD8E5B", "#C5A177", "#D0CEC8", "#573A1F"], "pattern": "reference-derived pixel palette", "amplitude": 0.219, "heightCorrelation": 0.42}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [1.0, 1.0], "anisotropy": 4, "texelDensityIntent": "Thin trim strips; keep texel density high enough for edge glints."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 2.0, "amplitude": 0.463, "role": "reference-derived broad albedo and height breakup"}, {"id": "meso", "frequency": 14.0, "amplitude": 0.35, "role": "reference-derived cracks, ridges, pores, grain, or leaf clusters"}, {"id": "micro", "frequency": 72.0, "amplitude": 0.14, "role": "reference-derived micro highlight breakup under grazing light"}], "roughness": {"base": 0.701, "variation": 0.094, "map": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_metal\\galvanized-steel_roughness.png", "url": "galvanized-steel_roughness.png", "channel": "roughness", "source": "reference-pixel-extraction"}, "localResponse": "reference-derived roughness estimate; cavities and textured zones trend rougher, bright highlights trend smoother"}, "metalness": {"base": 0.85, "variation": 0.1}, "normal": {"pattern": "reference-derived height-gradient normal map", "strength": 0.218, "map": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_metal\\galvanized-steel_normal.png", "url": "galvanized-steel_normal.png", "channel": "normal", "source": "reference-pixel-extraction"}, "heightSource": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_metal\\galvanized-steel_height.png", "url": "galvanized-steel_height.png", "channel": "height", "source": "reference-pixel-extraction"}, "space": "tangent"}, "bump": {"pattern": "reference-derived height field", "amplitude": 0.024, "map": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_metal\\galvanized-steel_height.png", "url": "galvanized-steel_height.png", "channel": "height", "source": "reference-pixel-extraction"}}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.38, "contactShadowBias": 0.35, "map": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_metal\\galvanized-steel_ao.png", "url": "galvanized-steel_ao.png", "channel": "ao", "source": "reference-pixel-extraction"}, "notes": "Reference-derived cavity estimate from local height minima; verify against grazing-light screenshot."}, "wear": {"edgeWear": 0.2, "scratches": [{"direction": "along-edge", "density": 0.1}], "chips": []}, "dirt": {"amount": 0.05, "cavityBias": 0.3, "color": "#3A3A3A"}, "localOverrides": [{"id": "handle-dark", "region": "roof-handle", "albedo": "#2A2A2A", "metalness": 0.7, "roughness": 0.55}, {"id": "reference-pbr-pixel-evidence", "type": "material-map-evidence", "evidenceRefs": ["full-object"], "channels": ["albedo", "roughness", "height", "normal", "ambient-occlusion"], "notes": "Use generated maps as material evidence, then refine after browser screenshot comparison."}], "referencePbr": {"version": "1.0", "sourceImage": "C:\\Users\\ifranjo\\scripts\\imag_3js\\refs\\langstroth_hive_gen.png", "extractor": "stage1_intake/extract_pbr_evidence.py", "method": "single-image pixel evidence with de-lighting estimate; not photogrammetry", "usable": true, "verdict": "pass", "confidence": 0.86, "estimatedFidelity": 0.86, "targetThreshold": 0.5, "hardLimit": "A single image cannot uniquely recover true albedo/roughness/normal/AO; maps are reference-derived estimates.", "maps": {"albedo": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_metal\\galvanized-steel_albedo.png", "url": "galvanized-steel_albedo.png", "channel": "albedo", "source": "reference-pixel-extraction"}, "roughness": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_metal\\galvanized-steel_roughness.png", "url": "galvanized-steel_roughness.png", "channel": "roughness", "source": "reference-pixel-extraction"}, "height": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_metal\\galvanized-steel_height.png", "url": "galvanized-steel_height.png", "channel": "height", "source": "reference-pixel-extraction"}, "normal": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_metal\\galvanized-steel_normal.png", "url": "galvanized-steel_normal.png", "channel": "normal", "source": "reference-pixel-extraction"}, "ao": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_metal\\galvanized-steel_ao.png", "url": "galvanized-steel_ao.png", "channel": "ao", "source": "reference-pixel-extraction"}}, "diagnostics": {"sourceWidth": 1024, "sourceHeight": 1024, "mapSize": 1024, "cropBBoxPixels": {"x": 152, "y": 78, "width": 872, "height": 898}, "mask": {"backgroundColor": "#F0F2F0", "backgroundNoise": 9.695, "transparentPixelFraction": 0.0, "foregroundCoverage": 0.5645}, "mapStats": {"valueRange": 0.5216, "heightP90Gradient": 0.05228, "roughnessBase": 0.701, "roughnessVariation": 0.094, "normalStrength": 0.218, "blurRadius": 21}, "palette": ["#936C47", "#BD8E5B", "#C5A177", "#D0CEC8", "#573A1F"]}, "warnings": ["single-image inverse rendering cannot prove true physical PBR; confidence is capped"]}, "shaderNotes": ["Separate dark material override for wire handle.", "Reference-derived maps are estimates from image pixels; verify with neutral, grazing, and reference-matched renders.", "Do not treat baked image shadows as final albedo; rerun extraction with a tighter material crop if highlights/shadows pollute the maps."]},
    options
  );
  materialMap["entrance-metal"] = createSculptMaterial(
    "entrance-metal",
    {"id": "entrance-metal", "name": "Dark entrance reducer / landing metal", "type": "standard", "shaderModel": "MeshStandardMaterial", "baseColor": "#4A4A48", "color": "#4A4A48", "albedo": {"dominant": "#936C47", "secondary": ["#BD8E5B", "#C5A177", "#D0CEC8"], "samplingNotes": "Reference-derived from foreground pixels; de-lit to reduce baked shadows/highlights.", "map": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_entrance\\entrance-metal_albedo.png", "url": "entrance-metal_albedo.png", "channel": "albedo", "source": "reference-pixel-extraction"}}, "colorVariation": {"palette": ["#936C47", "#BD8E5B", "#C5A177", "#D0CEC8", "#573A1F"], "pattern": "reference-derived pixel palette", "amplitude": 0.219, "heightCorrelation": 0.42}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [4.0, 1.0], "anisotropy": 4, "texelDensityIntent": "Teeth pattern along width."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 2.0, "amplitude": 0.463, "role": "reference-derived broad albedo and height breakup"}, {"id": "meso", "frequency": 14.0, "amplitude": 0.35, "role": "reference-derived cracks, ridges, pores, grain, or leaf clusters"}, {"id": "micro", "frequency": 72.0, "amplitude": 0.14, "role": "reference-derived micro highlight breakup under grazing light"}], "roughness": {"base": 0.701, "variation": 0.094, "map": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_entrance\\entrance-metal_roughness.png", "url": "entrance-metal_roughness.png", "channel": "roughness", "source": "reference-pixel-extraction"}, "localResponse": "reference-derived roughness estimate; cavities and textured zones trend rougher, bright highlights trend smoother"}, "metalness": {"base": 0.6, "variation": 0.1}, "normal": {"pattern": "reference-derived height-gradient normal map", "strength": 0.218, "map": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_entrance\\entrance-metal_normal.png", "url": "entrance-metal_normal.png", "channel": "normal", "source": "reference-pixel-extraction"}, "heightSource": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_entrance\\entrance-metal_height.png", "url": "entrance-metal_height.png", "channel": "height", "source": "reference-pixel-extraction"}, "space": "tangent"}, "bump": {"pattern": "reference-derived height field", "amplitude": 0.024, "map": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_entrance\\entrance-metal_height.png", "url": "entrance-metal_height.png", "channel": "height", "source": "reference-pixel-extraction"}}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.38, "contactShadowBias": 0.35, "map": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_entrance\\entrance-metal_ao.png", "url": "entrance-metal_ao.png", "channel": "ao", "source": "reference-pixel-extraction"}, "notes": "Reference-derived cavity estimate from local height minima; verify against grazing-light screenshot."}, "wear": {"edgeWear": 0.25, "scratches": [], "chips": []}, "dirt": {"amount": 0.2, "cavityBias": 0.6, "color": "#1A1A18"}, "localOverrides": [{"id": "entrance-slot-void", "region": "entrance-opening", "albedo": "#0A0A0A", "roughness": 0.9, "metalness": 0.0}, {"id": "reference-pbr-pixel-evidence", "type": "material-map-evidence", "evidenceRefs": ["full-object"], "channels": ["albedo", "roughness", "height", "normal", "ambient-occlusion"], "notes": "Use generated maps as material evidence, then refine after browser screenshot comparison."}], "referencePbr": {"version": "1.0", "sourceImage": "C:\\Users\\ifranjo\\scripts\\imag_3js\\refs\\langstroth_hive_gen.png", "extractor": "stage1_intake/extract_pbr_evidence.py", "method": "single-image pixel evidence with de-lighting estimate; not photogrammetry", "usable": true, "verdict": "pass", "confidence": 0.86, "estimatedFidelity": 0.86, "targetThreshold": 0.5, "hardLimit": "A single image cannot uniquely recover true albedo/roughness/normal/AO; maps are reference-derived estimates.", "maps": {"albedo": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_entrance\\entrance-metal_albedo.png", "url": "entrance-metal_albedo.png", "channel": "albedo", "source": "reference-pixel-extraction"}, "roughness": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_entrance\\entrance-metal_roughness.png", "url": "entrance-metal_roughness.png", "channel": "roughness", "source": "reference-pixel-extraction"}, "height": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_entrance\\entrance-metal_height.png", "url": "entrance-metal_height.png", "channel": "height", "source": "reference-pixel-extraction"}, "normal": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_entrance\\entrance-metal_normal.png", "url": "entrance-metal_normal.png", "channel": "normal", "source": "reference-pixel-extraction"}, "ao": {"path": "C:\\Users\\ifranjo\\scripts\\imag_3js\\work\\hive\\pbr_entrance\\entrance-metal_ao.png", "url": "entrance-metal_ao.png", "channel": "ao", "source": "reference-pixel-extraction"}}, "diagnostics": {"sourceWidth": 1024, "sourceHeight": 1024, "mapSize": 1024, "cropBBoxPixels": {"x": 152, "y": 78, "width": 872, "height": 898}, "mask": {"backgroundColor": "#F0F2F0", "backgroundNoise": 9.695, "transparentPixelFraction": 0.0, "foregroundCoverage": 0.5645}, "mapStats": {"valueRange": 0.5216, "heightP90Gradient": 0.05228, "roughnessBase": 0.701, "roughnessVariation": 0.094, "normalStrength": 0.218, "blurRadius": 21}, "palette": ["#936C47", "#BD8E5B", "#C5A177", "#D0CEC8", "#573A1F"]}, "warnings": ["single-image inverse rendering cannot prove true physical PBR; confidence is capped"]}, "shaderNotes": ["Reference-derived maps are estimates from image pixels; verify with neutral, grazing, and reference-matched renders.", "Do not treat baked image shadows as final albedo; rerun extraction with a tighter material crop if highlights/shadows pollute the maps."]},
    options
  );

  const nodes: Record<string, THREE.Object3D> = { root };
  const meshes: Record<string, THREE.Mesh> = {};
  const sockets: Record<string, THREE.Object3D> = {};
  const colliders: Record<string, unknown> = {};
  const destructionGroups: Record<string, THREE.Object3D[]> = {};

  const attachment_root_0 = null;
  const endpoint_root_0 = makeAttachmentEndpoint(attachment_root_0);
  const node_root_0 = new THREE.Group();
  node_root_0.name = "Langstroth Beehive Assembly__pivot";
  if (endpoint_root_0) {
    node_root_0.position.copy(endpoint_root_0.start);
    node_root_0.rotation.set(0, 0, 0);
    node_root_0.scale.set(1, 1, 1);
  } else {
    node_root_0.position.set(0.0, 0.0, 0.0);
    node_root_0.rotation.set(0.0, 0.0, 0.0);
    node_root_0.scale.set(1.0, 1.0, 1.0);
  }
  node_root_0.userData.sculptComponent = {"id": "root", "name": "Langstroth Beehive Assembly", "level": "macro", "role": "assembly-root", "importance": 1.0, "confidence": 0.95, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Multi-part stacked carpentered assembly of discrete boxes and hardware.", "geometryDescriptor": {"topologyIntent": "hierarchical assembly of rectangular supers + lid + base", "edgeTreatment": {"type": "bevel", "bevelRadius": 0.004, "segments": 2}, "deformationStack": [], "uvStrategy": "per-component box UVs", "normalStrategy": "vertex normals + grain bump"}, "parent": null, "attachment": null, "dimensions": {"width": 1.0, "height": 1.2800000000000002, "depth": 0.8799999999999999, "units": "relative", "confidence": 0.85}, "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "actionProfile": {"animationRole": "root", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.8}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "hive", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "varnished-pine"}}, "material": "varnished-pine", "materialLayers": ["varnished-pine", "galvanized-steel", "entrance-metal"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.15, "bumpAmplitude": 0.01, "normalPattern": "wood-grain", "displacementPattern": "", "occlusionPattern": "stack-seams", "edgeWearPattern": "corner-wear", "notes": "Root is structural parent only."}, "evidenceRefs": ["full-object"], "details": ["overall stacked rectangular silhouette"], "fidelityTier": "structural", "colorMaterialRecipe": {"dominantAlbedo": "rgba(201, 160, 106, 1.0)", "secondaryAlbedo": "rgba(166, 124, 74, 1.0)", "materialClass": "wood", "materialClassConfidence": 0.9}};
  node_root_0.userData.actionProfile = {"animationRole": "root", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.8}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "hive", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "varnished-pine"}};
  (nodes["root"] ?? root).add(node_root_0);
  nodes["root"] = node_root_0;
  const mesh_root_0Geometry = endpoint_root_0
    ? new THREE.CylinderGeometry(endpoint_root_0.endRadius, endpoint_root_0.baseRadius, endpoint_root_0.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  const mesh_root_0 = new THREE.Mesh(
    mesh_root_0Geometry,
    materialMap["varnished-pine"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_root_0.name = "Langstroth Beehive Assembly";
  if (endpoint_root_0) {
    mesh_root_0.position.copy(endpoint_root_0.midpoint);
    mesh_root_0.quaternion.copy(endpoint_root_0.quaternion);
  }
  mesh_root_0.castShadow = options.castShadow ?? true;
  mesh_root_0.receiveShadow = options.receiveShadow ?? true;
  mesh_root_0.userData.sculptComponent = {"id": "root", "name": "Langstroth Beehive Assembly", "level": "macro", "role": "assembly-root", "importance": 1.0, "confidence": 0.95, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Multi-part stacked carpentered assembly of discrete boxes and hardware.", "geometryDescriptor": {"topologyIntent": "hierarchical assembly of rectangular supers + lid + base", "edgeTreatment": {"type": "bevel", "bevelRadius": 0.004, "segments": 2}, "deformationStack": [], "uvStrategy": "per-component box UVs", "normalStrategy": "vertex normals + grain bump"}, "parent": null, "attachment": null, "dimensions": {"width": 1.0, "height": 1.2800000000000002, "depth": 0.8799999999999999, "units": "relative", "confidence": 0.85}, "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "actionProfile": {"animationRole": "root", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.8}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "hive", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "varnished-pine"}}, "material": "varnished-pine", "materialLayers": ["varnished-pine", "galvanized-steel", "entrance-metal"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.15, "bumpAmplitude": 0.01, "normalPattern": "wood-grain", "displacementPattern": "", "occlusionPattern": "stack-seams", "edgeWearPattern": "corner-wear", "notes": "Root is structural parent only."}, "evidenceRefs": ["full-object"], "details": ["overall stacked rectangular silhouette"], "fidelityTier": "structural", "colorMaterialRecipe": {"dominantAlbedo": "rgba(201, 160, 106, 1.0)", "secondaryAlbedo": "rgba(166, 124, 74, 1.0)", "materialClass": "wood", "materialClassConfidence": 0.9}};
  node_root_0.add(mesh_root_0);
  meshes["root"] = mesh_root_0;
  colliders["root"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""};
  destructionGroups["hive"] ??= [];
  destructionGroups["hive"].push(node_root_0);

  const attachment_bottom_board_1 = {"parentSocket": "base", "localStart": [0, 0, 0], "localEnd": [0, 0.12, 0], "contactType": "abut", "embedDepth": 0.0, "overlap": 0.0, "gapTolerance": 0.002, "notes": "Attached to root"};
  const endpoint_bottom_board_1 = makeAttachmentEndpoint(attachment_bottom_board_1);
  const node_bottom_board_1 = new THREE.Group();
  node_bottom_board_1.name = "Bottom board / landing__pivot";
  if (endpoint_bottom_board_1) {
    node_bottom_board_1.position.copy(endpoint_bottom_board_1.start);
    node_bottom_board_1.rotation.set(0, 0, 0);
    node_bottom_board_1.scale.set(1, 1, 1);
  } else {
    node_bottom_board_1.position.set(0.0, 0.06, 0.03);
    node_bottom_board_1.rotation.set(0.0, 0.0, 0.0);
    node_bottom_board_1.scale.set(1.0, 1.0, 1.0);
  }
  node_bottom_board_1.userData.sculptComponent = {"id": "bottom-board", "name": "Bottom board / landing", "level": "macro", "role": "base", "importance": 0.95, "confidence": 0.88, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Flat wooden plank assembly with slight front overhang.", "geometryDescriptor": {"topologyIntent": "wide low plank with front landing ledge", "edgeTreatment": {"type": "bevel", "bevelRadius": 0.003, "segments": 2}, "deformationStack": [], "uvStrategy": "box UV, grain along X", "normalStrategy": "vertex normals"}, "parent": "root", "attachment": {"parentSocket": "base", "localStart": [0, 0, 0], "localEnd": [0, 0.12, 0], "contactType": "abut", "embedDepth": 0.0, "overlap": 0.0, "gapTolerance": 0.002, "notes": "Attached to root"}, "dimensions": {"width": 1.08, "height": 0.12, "depth": 0.94, "units": "relative", "confidence": 0.85}, "transform": {"position": [0, 0.06, 0.03], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "actionProfile": {"animationRole": "static-base", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.8}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "base", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "varnished-pine"}}, "material": "varnished-pine", "materialLayers": ["varnished-pine"], "deformations": [], "joints": [], "seams": [{"with": "super-bottom", "type": "abut-seam"}], "localFeatures": [{"id": "landing-overhang", "kind": "ledge", "description": "Front plank extends beyond super stack", "affects": "silhouette"}], "surfaceDetail": {"macroRoughness": 0.45, "microRoughness": 0.2, "bumpAmplitude": 0.012, "normalPattern": "wood-grain", "displacementPattern": "", "occlusionPattern": "under-super", "edgeWearPattern": "front-edge-wear", "notes": ""}, "evidenceRefs": ["full-object"], "details": ["bottom-board-ledge"], "fidelityTier": "structural", "colorMaterialRecipe": {"dominantAlbedo": "rgba(184, 144, 96, 1.0)", "secondaryAlbedo": "rgba(139, 99, 64, 1.0)", "materialClass": "wood", "materialClassConfidence": 0.85}};
  node_bottom_board_1.userData.actionProfile = {"animationRole": "static-base", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.8}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "base", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "varnished-pine"}};
  (nodes["root"] ?? root).add(node_bottom_board_1);
  nodes["bottom-board"] = node_bottom_board_1;
  const mesh_bottom_board_1Geometry = endpoint_bottom_board_1
    ? new THREE.CylinderGeometry(endpoint_bottom_board_1.endRadius, endpoint_bottom_board_1.baseRadius, endpoint_bottom_board_1.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  const mesh_bottom_board_1 = new THREE.Mesh(
    mesh_bottom_board_1Geometry,
    materialMap["varnished-pine"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_bottom_board_1.name = "Bottom board / landing";
  if (endpoint_bottom_board_1) {
    mesh_bottom_board_1.position.copy(endpoint_bottom_board_1.midpoint);
    mesh_bottom_board_1.quaternion.copy(endpoint_bottom_board_1.quaternion);
  }
  mesh_bottom_board_1.castShadow = options.castShadow ?? true;
  mesh_bottom_board_1.receiveShadow = options.receiveShadow ?? true;
  mesh_bottom_board_1.userData.sculptComponent = {"id": "bottom-board", "name": "Bottom board / landing", "level": "macro", "role": "base", "importance": 0.95, "confidence": 0.88, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Flat wooden plank assembly with slight front overhang.", "geometryDescriptor": {"topologyIntent": "wide low plank with front landing ledge", "edgeTreatment": {"type": "bevel", "bevelRadius": 0.003, "segments": 2}, "deformationStack": [], "uvStrategy": "box UV, grain along X", "normalStrategy": "vertex normals"}, "parent": "root", "attachment": {"parentSocket": "base", "localStart": [0, 0, 0], "localEnd": [0, 0.12, 0], "contactType": "abut", "embedDepth": 0.0, "overlap": 0.0, "gapTolerance": 0.002, "notes": "Attached to root"}, "dimensions": {"width": 1.08, "height": 0.12, "depth": 0.94, "units": "relative", "confidence": 0.85}, "transform": {"position": [0, 0.06, 0.03], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "actionProfile": {"animationRole": "static-base", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.8}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "base", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "varnished-pine"}}, "material": "varnished-pine", "materialLayers": ["varnished-pine"], "deformations": [], "joints": [], "seams": [{"with": "super-bottom", "type": "abut-seam"}], "localFeatures": [{"id": "landing-overhang", "kind": "ledge", "description": "Front plank extends beyond super stack", "affects": "silhouette"}], "surfaceDetail": {"macroRoughness": 0.45, "microRoughness": 0.2, "bumpAmplitude": 0.012, "normalPattern": "wood-grain", "displacementPattern": "", "occlusionPattern": "under-super", "edgeWearPattern": "front-edge-wear", "notes": ""}, "evidenceRefs": ["full-object"], "details": ["bottom-board-ledge"], "fidelityTier": "structural", "colorMaterialRecipe": {"dominantAlbedo": "rgba(184, 144, 96, 1.0)", "secondaryAlbedo": "rgba(139, 99, 64, 1.0)", "materialClass": "wood", "materialClassConfidence": 0.85}};
  node_bottom_board_1.add(mesh_bottom_board_1);
  meshes["bottom-board"] = mesh_bottom_board_1;
  colliders["bottom-board"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""};
  destructionGroups["base"] ??= [];
  destructionGroups["base"].push(node_bottom_board_1);

  const attachment_super_bottom_2 = {"parentSocket": "stack-0", "localStart": [0, 0.12, 0], "localEnd": [0, 0.48, 0], "contactType": "abut", "embedDepth": 0.0, "overlap": 0.0, "gapTolerance": 0.003, "notes": "Attached to root"};
  const endpoint_super_bottom_2 = makeAttachmentEndpoint(attachment_super_bottom_2);
  const node_super_bottom_2 = new THREE.Group();
  node_super_bottom_2.name = "Bottom super (brood box)__pivot";
  if (endpoint_super_bottom_2) {
    node_super_bottom_2.position.copy(endpoint_super_bottom_2.start);
    node_super_bottom_2.rotation.set(0, 0, 0);
    node_super_bottom_2.scale.set(1, 1, 1);
  } else {
    node_super_bottom_2.position.set(0.0, 0.3, 0.0);
    node_super_bottom_2.rotation.set(0.0, 0.0, 0.0);
    node_super_bottom_2.scale.set(1.0, 1.0, 1.0);
  }
  node_super_bottom_2.userData.sculptComponent = {"id": "super-bottom", "name": "Bottom super (brood box)", "level": "macro", "role": "body-segment", "importance": 1.0, "confidence": 0.9, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Closed rectangular wooden box with finger-joint corners and recessed hand holds.", "geometryDescriptor": {"topologyIntent": "hollow-looking solid blockout box; wall thickness implied by joints", "edgeTreatment": {"type": "bevel", "bevelRadius": 0.004, "segments": 2}, "deformationStack": [], "uvStrategy": "box UV vertical grain", "normalStrategy": "vertex normals + grain"}, "parent": "root", "attachment": {"parentSocket": "stack-0", "localStart": [0, 0.12, 0], "localEnd": [0, 0.48, 0], "contactType": "abut", "embedDepth": 0.0, "overlap": 0.0, "gapTolerance": 0.003, "notes": "Attached to root"}, "dimensions": {"width": 1.0, "height": 0.36, "depth": 0.82, "units": "relative", "confidence": 0.9}, "transform": {"position": [0, 0.3, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "actionProfile": {"animationRole": "detachable-stack", "pivot": {"mode": "bottom-center", "localPosition": [0, -0.18, 0], "axis": [0, 1, 0], "confidence": 0.85}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": true, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": true, "fractureGroup": "super-0", "seamRefs": [], "detachableFragments": [], "breakImpulse": 12.0, "debrisMaterial": "varnished-pine"}}, "material": "varnished-pine", "materialLayers": ["varnished-pine"], "deformations": [], "joints": [{"type": "box-joint", "corners": ["all-vertical"]}], "seams": [{"with": "neighbor-super", "type": "horizontal-abut"}], "localFeatures": [{"id": "finger-joints", "kind": "joinery", "description": "Alternating finger joints on vertical edges", "affects": "edge-rhythm"}, {"id": "front-handhold", "kind": "recess", "description": "Elongated horizontal hand-hold on front face", "affects": "form", "placement": {"face": "front", "width": 0.28, "height": 0.06, "depth": 0.03}}, {"id": "side-handhold", "kind": "recess", "description": "Hand-hold on right side face", "affects": "form", "placement": {"face": "right", "width": 0.06, "height": 0.12, "depth": 0.03}}], "surfaceDetail": {"macroRoughness": 0.42, "microRoughness": 0.15, "bumpAmplitude": 0.012, "normalPattern": "wood-grain", "displacementPattern": "", "occlusionPattern": "handhold-cavity", "edgeWearPattern": "corner-soft", "notes": "End-grain fingers darker."}, "evidenceRefs": ["full-object"], "details": ["finger-joint-corners", "recessed-handholds", "side-handholds", "wood-grain-varnish"], "fidelityTier": "structural", "colorMaterialRecipe": {"dominantAlbedo": "rgba(201, 160, 106, 1.0)", "secondaryAlbedo": "rgba(166, 124, 74, 1.0)", "materialClass": "wood", "materialClassConfidence": 0.9}, "repetitionSystemRef": "super-stack"};
  node_super_bottom_2.userData.actionProfile = {"animationRole": "detachable-stack", "pivot": {"mode": "bottom-center", "localPosition": [0, -0.18, 0], "axis": [0, 1, 0], "confidence": 0.85}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": true, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": true, "fractureGroup": "super-0", "seamRefs": [], "detachableFragments": [], "breakImpulse": 12.0, "debrisMaterial": "varnished-pine"}};
  (nodes["root"] ?? root).add(node_super_bottom_2);
  nodes["super-bottom"] = node_super_bottom_2;
  const mesh_super_bottom_2Geometry = endpoint_super_bottom_2
    ? new THREE.CylinderGeometry(endpoint_super_bottom_2.endRadius, endpoint_super_bottom_2.baseRadius, endpoint_super_bottom_2.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  const mesh_super_bottom_2 = new THREE.Mesh(
    mesh_super_bottom_2Geometry,
    materialMap["varnished-pine"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_super_bottom_2.name = "Bottom super (brood box)";
  if (endpoint_super_bottom_2) {
    mesh_super_bottom_2.position.copy(endpoint_super_bottom_2.midpoint);
    mesh_super_bottom_2.quaternion.copy(endpoint_super_bottom_2.quaternion);
  }
  mesh_super_bottom_2.castShadow = options.castShadow ?? true;
  mesh_super_bottom_2.receiveShadow = options.receiveShadow ?? true;
  mesh_super_bottom_2.userData.sculptComponent = {"id": "super-bottom", "name": "Bottom super (brood box)", "level": "macro", "role": "body-segment", "importance": 1.0, "confidence": 0.9, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Closed rectangular wooden box with finger-joint corners and recessed hand holds.", "geometryDescriptor": {"topologyIntent": "hollow-looking solid blockout box; wall thickness implied by joints", "edgeTreatment": {"type": "bevel", "bevelRadius": 0.004, "segments": 2}, "deformationStack": [], "uvStrategy": "box UV vertical grain", "normalStrategy": "vertex normals + grain"}, "parent": "root", "attachment": {"parentSocket": "stack-0", "localStart": [0, 0.12, 0], "localEnd": [0, 0.48, 0], "contactType": "abut", "embedDepth": 0.0, "overlap": 0.0, "gapTolerance": 0.003, "notes": "Attached to root"}, "dimensions": {"width": 1.0, "height": 0.36, "depth": 0.82, "units": "relative", "confidence": 0.9}, "transform": {"position": [0, 0.3, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "actionProfile": {"animationRole": "detachable-stack", "pivot": {"mode": "bottom-center", "localPosition": [0, -0.18, 0], "axis": [0, 1, 0], "confidence": 0.85}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": true, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": true, "fractureGroup": "super-0", "seamRefs": [], "detachableFragments": [], "breakImpulse": 12.0, "debrisMaterial": "varnished-pine"}}, "material": "varnished-pine", "materialLayers": ["varnished-pine"], "deformations": [], "joints": [{"type": "box-joint", "corners": ["all-vertical"]}], "seams": [{"with": "neighbor-super", "type": "horizontal-abut"}], "localFeatures": [{"id": "finger-joints", "kind": "joinery", "description": "Alternating finger joints on vertical edges", "affects": "edge-rhythm"}, {"id": "front-handhold", "kind": "recess", "description": "Elongated horizontal hand-hold on front face", "affects": "form", "placement": {"face": "front", "width": 0.28, "height": 0.06, "depth": 0.03}}, {"id": "side-handhold", "kind": "recess", "description": "Hand-hold on right side face", "affects": "form", "placement": {"face": "right", "width": 0.06, "height": 0.12, "depth": 0.03}}], "surfaceDetail": {"macroRoughness": 0.42, "microRoughness": 0.15, "bumpAmplitude": 0.012, "normalPattern": "wood-grain", "displacementPattern": "", "occlusionPattern": "handhold-cavity", "edgeWearPattern": "corner-soft", "notes": "End-grain fingers darker."}, "evidenceRefs": ["full-object"], "details": ["finger-joint-corners", "recessed-handholds", "side-handholds", "wood-grain-varnish"], "fidelityTier": "structural", "colorMaterialRecipe": {"dominantAlbedo": "rgba(201, 160, 106, 1.0)", "secondaryAlbedo": "rgba(166, 124, 74, 1.0)", "materialClass": "wood", "materialClassConfidence": 0.9}, "repetitionSystemRef": "super-stack"};
  node_super_bottom_2.add(mesh_super_bottom_2);
  meshes["super-bottom"] = mesh_super_bottom_2;
  colliders["super-bottom"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""};
  destructionGroups["super-0"] ??= [];
  destructionGroups["super-0"].push(node_super_bottom_2);

  const attachment_super_middle_3 = {"parentSocket": "stack-1", "localStart": [0, 0.48000000000000004, 0], "localEnd": [0, 0.8400000000000001, 0], "contactType": "abut", "embedDepth": 0.0, "overlap": 0.0, "gapTolerance": 0.003, "notes": "Attached to root"};
  const endpoint_super_middle_3 = makeAttachmentEndpoint(attachment_super_middle_3);
  const node_super_middle_3 = new THREE.Group();
  node_super_middle_3.name = "Middle super__pivot";
  if (endpoint_super_middle_3) {
    node_super_middle_3.position.copy(endpoint_super_middle_3.start);
    node_super_middle_3.rotation.set(0, 0, 0);
    node_super_middle_3.scale.set(1, 1, 1);
  } else {
    node_super_middle_3.position.set(0.0, 0.66, 0.0);
    node_super_middle_3.rotation.set(0.0, 0.0, 0.0);
    node_super_middle_3.scale.set(1.0, 1.0, 1.0);
  }
  node_super_middle_3.userData.sculptComponent = {"id": "super-middle", "name": "Middle super", "level": "macro", "role": "body-segment", "importance": 0.98, "confidence": 0.9, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Closed rectangular wooden box with finger-joint corners and recessed hand holds.", "geometryDescriptor": {"topologyIntent": "hollow-looking solid blockout box; wall thickness implied by joints", "edgeTreatment": {"type": "bevel", "bevelRadius": 0.004, "segments": 2}, "deformationStack": [], "uvStrategy": "box UV vertical grain", "normalStrategy": "vertex normals + grain"}, "parent": "root", "attachment": {"parentSocket": "stack-1", "localStart": [0, 0.48000000000000004, 0], "localEnd": [0, 0.8400000000000001, 0], "contactType": "abut", "embedDepth": 0.0, "overlap": 0.0, "gapTolerance": 0.003, "notes": "Attached to root"}, "dimensions": {"width": 1.0, "height": 0.36, "depth": 0.82, "units": "relative", "confidence": 0.9}, "transform": {"position": [0, 0.66, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "actionProfile": {"animationRole": "detachable-stack", "pivot": {"mode": "bottom-center", "localPosition": [0, -0.18, 0], "axis": [0, 1, 0], "confidence": 0.85}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": true, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": true, "fractureGroup": "super-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 12.0, "debrisMaterial": "varnished-pine"}}, "material": "varnished-pine", "materialLayers": ["varnished-pine"], "deformations": [], "joints": [{"type": "box-joint", "corners": ["all-vertical"]}], "seams": [{"with": "neighbor-super", "type": "horizontal-abut"}], "localFeatures": [{"id": "finger-joints", "kind": "joinery", "description": "Alternating finger joints on vertical edges", "affects": "edge-rhythm"}, {"id": "front-handhold", "kind": "recess", "description": "Elongated horizontal hand-hold on front face", "affects": "form", "placement": {"face": "front", "width": 0.28, "height": 0.06, "depth": 0.03}}, {"id": "side-handhold", "kind": "recess", "description": "Hand-hold on right side face", "affects": "form", "placement": {"face": "right", "width": 0.06, "height": 0.12, "depth": 0.03}}], "surfaceDetail": {"macroRoughness": 0.42, "microRoughness": 0.15, "bumpAmplitude": 0.012, "normalPattern": "wood-grain", "displacementPattern": "", "occlusionPattern": "handhold-cavity", "edgeWearPattern": "corner-soft", "notes": "End-grain fingers darker."}, "evidenceRefs": ["full-object"], "details": ["finger-joint-corners", "recessed-handholds", "side-handholds", "wood-grain-varnish"], "fidelityTier": "structural", "colorMaterialRecipe": {"dominantAlbedo": "rgba(201, 160, 106, 1.0)", "secondaryAlbedo": "rgba(166, 124, 74, 1.0)", "materialClass": "wood", "materialClassConfidence": 0.9}, "repetitionSystemRef": "super-stack"};
  node_super_middle_3.userData.actionProfile = {"animationRole": "detachable-stack", "pivot": {"mode": "bottom-center", "localPosition": [0, -0.18, 0], "axis": [0, 1, 0], "confidence": 0.85}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": true, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": true, "fractureGroup": "super-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 12.0, "debrisMaterial": "varnished-pine"}};
  (nodes["root"] ?? root).add(node_super_middle_3);
  nodes["super-middle"] = node_super_middle_3;
  const mesh_super_middle_3Geometry = endpoint_super_middle_3
    ? new THREE.CylinderGeometry(endpoint_super_middle_3.endRadius, endpoint_super_middle_3.baseRadius, endpoint_super_middle_3.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  const mesh_super_middle_3 = new THREE.Mesh(
    mesh_super_middle_3Geometry,
    materialMap["varnished-pine"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_super_middle_3.name = "Middle super";
  if (endpoint_super_middle_3) {
    mesh_super_middle_3.position.copy(endpoint_super_middle_3.midpoint);
    mesh_super_middle_3.quaternion.copy(endpoint_super_middle_3.quaternion);
  }
  mesh_super_middle_3.castShadow = options.castShadow ?? true;
  mesh_super_middle_3.receiveShadow = options.receiveShadow ?? true;
  mesh_super_middle_3.userData.sculptComponent = {"id": "super-middle", "name": "Middle super", "level": "macro", "role": "body-segment", "importance": 0.98, "confidence": 0.9, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Closed rectangular wooden box with finger-joint corners and recessed hand holds.", "geometryDescriptor": {"topologyIntent": "hollow-looking solid blockout box; wall thickness implied by joints", "edgeTreatment": {"type": "bevel", "bevelRadius": 0.004, "segments": 2}, "deformationStack": [], "uvStrategy": "box UV vertical grain", "normalStrategy": "vertex normals + grain"}, "parent": "root", "attachment": {"parentSocket": "stack-1", "localStart": [0, 0.48000000000000004, 0], "localEnd": [0, 0.8400000000000001, 0], "contactType": "abut", "embedDepth": 0.0, "overlap": 0.0, "gapTolerance": 0.003, "notes": "Attached to root"}, "dimensions": {"width": 1.0, "height": 0.36, "depth": 0.82, "units": "relative", "confidence": 0.9}, "transform": {"position": [0, 0.66, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "actionProfile": {"animationRole": "detachable-stack", "pivot": {"mode": "bottom-center", "localPosition": [0, -0.18, 0], "axis": [0, 1, 0], "confidence": 0.85}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": true, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": true, "fractureGroup": "super-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 12.0, "debrisMaterial": "varnished-pine"}}, "material": "varnished-pine", "materialLayers": ["varnished-pine"], "deformations": [], "joints": [{"type": "box-joint", "corners": ["all-vertical"]}], "seams": [{"with": "neighbor-super", "type": "horizontal-abut"}], "localFeatures": [{"id": "finger-joints", "kind": "joinery", "description": "Alternating finger joints on vertical edges", "affects": "edge-rhythm"}, {"id": "front-handhold", "kind": "recess", "description": "Elongated horizontal hand-hold on front face", "affects": "form", "placement": {"face": "front", "width": 0.28, "height": 0.06, "depth": 0.03}}, {"id": "side-handhold", "kind": "recess", "description": "Hand-hold on right side face", "affects": "form", "placement": {"face": "right", "width": 0.06, "height": 0.12, "depth": 0.03}}], "surfaceDetail": {"macroRoughness": 0.42, "microRoughness": 0.15, "bumpAmplitude": 0.012, "normalPattern": "wood-grain", "displacementPattern": "", "occlusionPattern": "handhold-cavity", "edgeWearPattern": "corner-soft", "notes": "End-grain fingers darker."}, "evidenceRefs": ["full-object"], "details": ["finger-joint-corners", "recessed-handholds", "side-handholds", "wood-grain-varnish"], "fidelityTier": "structural", "colorMaterialRecipe": {"dominantAlbedo": "rgba(201, 160, 106, 1.0)", "secondaryAlbedo": "rgba(166, 124, 74, 1.0)", "materialClass": "wood", "materialClassConfidence": 0.9}, "repetitionSystemRef": "super-stack"};
  node_super_middle_3.add(mesh_super_middle_3);
  meshes["super-middle"] = mesh_super_middle_3;
  colliders["super-middle"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""};
  destructionGroups["super-1"] ??= [];
  destructionGroups["super-1"].push(node_super_middle_3);

  const attachment_super_top_4 = {"parentSocket": "stack-2", "localStart": [0, 0.8400000000000001, 0], "localEnd": [0, 1.2, 0], "contactType": "abut", "embedDepth": 0.0, "overlap": 0.0, "gapTolerance": 0.003, "notes": "Attached to root"};
  const endpoint_super_top_4 = makeAttachmentEndpoint(attachment_super_top_4);
  const node_super_top_4 = new THREE.Group();
  node_super_top_4.name = "Top super__pivot";
  if (endpoint_super_top_4) {
    node_super_top_4.position.copy(endpoint_super_top_4.start);
    node_super_top_4.rotation.set(0, 0, 0);
    node_super_top_4.scale.set(1, 1, 1);
  } else {
    node_super_top_4.position.set(0.0, 1.02, 0.0);
    node_super_top_4.rotation.set(0.0, 0.0, 0.0);
    node_super_top_4.scale.set(1.0, 1.0, 1.0);
  }
  node_super_top_4.userData.sculptComponent = {"id": "super-top", "name": "Top super", "level": "macro", "role": "body-segment", "importance": 0.96, "confidence": 0.9, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Closed rectangular wooden box with finger-joint corners and recessed hand holds.", "geometryDescriptor": {"topologyIntent": "hollow-looking solid blockout box; wall thickness implied by joints", "edgeTreatment": {"type": "bevel", "bevelRadius": 0.004, "segments": 2}, "deformationStack": [], "uvStrategy": "box UV vertical grain", "normalStrategy": "vertex normals + grain"}, "parent": "root", "attachment": {"parentSocket": "stack-2", "localStart": [0, 0.8400000000000001, 0], "localEnd": [0, 1.2, 0], "contactType": "abut", "embedDepth": 0.0, "overlap": 0.0, "gapTolerance": 0.003, "notes": "Attached to root"}, "dimensions": {"width": 1.0, "height": 0.36, "depth": 0.82, "units": "relative", "confidence": 0.9}, "transform": {"position": [0, 1.02, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "actionProfile": {"animationRole": "detachable-stack", "pivot": {"mode": "bottom-center", "localPosition": [0, -0.18, 0], "axis": [0, 1, 0], "confidence": 0.85}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": true, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": true, "fractureGroup": "super-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 12.0, "debrisMaterial": "varnished-pine"}}, "material": "varnished-pine", "materialLayers": ["varnished-pine"], "deformations": [], "joints": [{"type": "box-joint", "corners": ["all-vertical"]}], "seams": [{"with": "neighbor-super", "type": "horizontal-abut"}], "localFeatures": [{"id": "finger-joints", "kind": "joinery", "description": "Alternating finger joints on vertical edges", "affects": "edge-rhythm"}, {"id": "front-handhold", "kind": "recess", "description": "Elongated horizontal hand-hold on front face", "affects": "form", "placement": {"face": "front", "width": 0.28, "height": 0.06, "depth": 0.03}}, {"id": "side-handhold", "kind": "recess", "description": "Hand-hold on right side face", "affects": "form", "placement": {"face": "right", "width": 0.06, "height": 0.12, "depth": 0.03}}], "surfaceDetail": {"macroRoughness": 0.42, "microRoughness": 0.15, "bumpAmplitude": 0.012, "normalPattern": "wood-grain", "displacementPattern": "", "occlusionPattern": "handhold-cavity", "edgeWearPattern": "corner-soft", "notes": "End-grain fingers darker."}, "evidenceRefs": ["full-object"], "details": ["finger-joint-corners", "recessed-handholds", "side-handholds", "wood-grain-varnish"], "fidelityTier": "structural", "colorMaterialRecipe": {"dominantAlbedo": "rgba(201, 160, 106, 1.0)", "secondaryAlbedo": "rgba(166, 124, 74, 1.0)", "materialClass": "wood", "materialClassConfidence": 0.9}, "repetitionSystemRef": "super-stack"};
  node_super_top_4.userData.actionProfile = {"animationRole": "detachable-stack", "pivot": {"mode": "bottom-center", "localPosition": [0, -0.18, 0], "axis": [0, 1, 0], "confidence": 0.85}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": true, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": true, "fractureGroup": "super-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 12.0, "debrisMaterial": "varnished-pine"}};
  (nodes["root"] ?? root).add(node_super_top_4);
  nodes["super-top"] = node_super_top_4;
  const mesh_super_top_4Geometry = endpoint_super_top_4
    ? new THREE.CylinderGeometry(endpoint_super_top_4.endRadius, endpoint_super_top_4.baseRadius, endpoint_super_top_4.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  const mesh_super_top_4 = new THREE.Mesh(
    mesh_super_top_4Geometry,
    materialMap["varnished-pine"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_super_top_4.name = "Top super";
  if (endpoint_super_top_4) {
    mesh_super_top_4.position.copy(endpoint_super_top_4.midpoint);
    mesh_super_top_4.quaternion.copy(endpoint_super_top_4.quaternion);
  }
  mesh_super_top_4.castShadow = options.castShadow ?? true;
  mesh_super_top_4.receiveShadow = options.receiveShadow ?? true;
  mesh_super_top_4.userData.sculptComponent = {"id": "super-top", "name": "Top super", "level": "macro", "role": "body-segment", "importance": 0.96, "confidence": 0.9, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Closed rectangular wooden box with finger-joint corners and recessed hand holds.", "geometryDescriptor": {"topologyIntent": "hollow-looking solid blockout box; wall thickness implied by joints", "edgeTreatment": {"type": "bevel", "bevelRadius": 0.004, "segments": 2}, "deformationStack": [], "uvStrategy": "box UV vertical grain", "normalStrategy": "vertex normals + grain"}, "parent": "root", "attachment": {"parentSocket": "stack-2", "localStart": [0, 0.8400000000000001, 0], "localEnd": [0, 1.2, 0], "contactType": "abut", "embedDepth": 0.0, "overlap": 0.0, "gapTolerance": 0.003, "notes": "Attached to root"}, "dimensions": {"width": 1.0, "height": 0.36, "depth": 0.82, "units": "relative", "confidence": 0.9}, "transform": {"position": [0, 1.02, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "actionProfile": {"animationRole": "detachable-stack", "pivot": {"mode": "bottom-center", "localPosition": [0, -0.18, 0], "axis": [0, 1, 0], "confidence": 0.85}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": true, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": true, "fractureGroup": "super-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 12.0, "debrisMaterial": "varnished-pine"}}, "material": "varnished-pine", "materialLayers": ["varnished-pine"], "deformations": [], "joints": [{"type": "box-joint", "corners": ["all-vertical"]}], "seams": [{"with": "neighbor-super", "type": "horizontal-abut"}], "localFeatures": [{"id": "finger-joints", "kind": "joinery", "description": "Alternating finger joints on vertical edges", "affects": "edge-rhythm"}, {"id": "front-handhold", "kind": "recess", "description": "Elongated horizontal hand-hold on front face", "affects": "form", "placement": {"face": "front", "width": 0.28, "height": 0.06, "depth": 0.03}}, {"id": "side-handhold", "kind": "recess", "description": "Hand-hold on right side face", "affects": "form", "placement": {"face": "right", "width": 0.06, "height": 0.12, "depth": 0.03}}], "surfaceDetail": {"macroRoughness": 0.42, "microRoughness": 0.15, "bumpAmplitude": 0.012, "normalPattern": "wood-grain", "displacementPattern": "", "occlusionPattern": "handhold-cavity", "edgeWearPattern": "corner-soft", "notes": "End-grain fingers darker."}, "evidenceRefs": ["full-object"], "details": ["finger-joint-corners", "recessed-handholds", "side-handholds", "wood-grain-varnish"], "fidelityTier": "structural", "colorMaterialRecipe": {"dominantAlbedo": "rgba(201, 160, 106, 1.0)", "secondaryAlbedo": "rgba(166, 124, 74, 1.0)", "materialClass": "wood", "materialClassConfidence": 0.9}, "repetitionSystemRef": "super-stack"};
  node_super_top_4.add(mesh_super_top_4);
  meshes["super-top"] = mesh_super_top_4;
  colliders["super-top"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""};
  destructionGroups["super-2"] ??= [];
  destructionGroups["super-2"].push(node_super_top_4);

  const attachment_roof_5 = {"parentSocket": "top-socket", "localStart": [0, 1.2000000000000002, 0], "localEnd": [0, 1.2800000000000002, 0], "contactType": "abut", "embedDepth": 0.0, "overlap": 0.0, "gapTolerance": 0.003, "notes": "Attached to root"};
  const endpoint_roof_5 = makeAttachmentEndpoint(attachment_roof_5);
  const node_roof_5 = new THREE.Group();
  node_roof_5.name = "Telescoping cover / roof__pivot";
  if (endpoint_roof_5) {
    node_roof_5.position.copy(endpoint_roof_5.start);
    node_roof_5.rotation.set(0, 0, 0);
    node_roof_5.scale.set(1, 1, 1);
  } else {
    node_roof_5.position.set(0.0, 1.2400000000000002, 0.0);
    node_roof_5.rotation.set(0.0, 0.0, 0.0);
    node_roof_5.scale.set(1.0, 1.0, 1.0);
  }
  node_roof_5.userData.sculptComponent = {"id": "roof", "name": "Telescoping cover / roof", "level": "macro", "role": "lid", "importance": 0.95, "confidence": 0.9, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Flat wooden lid slightly larger than supers with metal drip edge.", "geometryDescriptor": {"topologyIntent": "flat lid plank with slight overhang", "edgeTreatment": {"type": "bevel", "bevelRadius": 0.003, "segments": 2}, "deformationStack": [], "uvStrategy": "box UV", "normalStrategy": "vertex normals"}, "parent": "root", "attachment": {"parentSocket": "top-socket", "localStart": [0, 1.2000000000000002, 0], "localEnd": [0, 1.2800000000000002, 0], "contactType": "abut", "embedDepth": 0.0, "overlap": 0.0, "gapTolerance": 0.003, "notes": "Attached to root"}, "dimensions": {"width": 1.06, "height": 0.08, "depth": 0.8799999999999999, "units": "relative", "confidence": 0.9}, "transform": {"position": [0, 1.2400000000000002, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "actionProfile": {"animationRole": "lid", "pivot": {"mode": "back-hinge", "localPosition": [0, 0, -0.328], "axis": [1, 0, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": true, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": true, "fractureGroup": "roof", "seamRefs": [], "detachableFragments": [], "breakImpulse": 12.0, "debrisMaterial": "varnished-pine"}}, "material": "varnished-pine", "materialLayers": ["varnished-pine", "galvanized-steel"], "deformations": [], "joints": [], "seams": [{"with": "super-top", "type": "abut-seam"}], "localFeatures": [{"id": "flat-top", "kind": "plane", "description": "Planar top surface"}], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.15, "bumpAmplitude": 0.01, "normalPattern": "wood-grain", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": ["metal-roof-drip-edge", "roof-wire-handle"], "fidelityTier": "structural", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 160, 112, 1.0)", "secondaryAlbedo": "rgba(166, 124, 74, 1.0)", "materialClass": "wood", "materialClassConfidence": 0.88}};
  node_roof_5.userData.actionProfile = {"animationRole": "lid", "pivot": {"mode": "back-hinge", "localPosition": [0, 0, -0.328], "axis": [1, 0, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": true, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": true, "fractureGroup": "roof", "seamRefs": [], "detachableFragments": [], "breakImpulse": 12.0, "debrisMaterial": "varnished-pine"}};
  (nodes["root"] ?? root).add(node_roof_5);
  nodes["roof"] = node_roof_5;
  const mesh_roof_5Geometry = endpoint_roof_5
    ? new THREE.CylinderGeometry(endpoint_roof_5.endRadius, endpoint_roof_5.baseRadius, endpoint_roof_5.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  const mesh_roof_5 = new THREE.Mesh(
    mesh_roof_5Geometry,
    materialMap["varnished-pine"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_roof_5.name = "Telescoping cover / roof";
  if (endpoint_roof_5) {
    mesh_roof_5.position.copy(endpoint_roof_5.midpoint);
    mesh_roof_5.quaternion.copy(endpoint_roof_5.quaternion);
  }
  mesh_roof_5.castShadow = options.castShadow ?? true;
  mesh_roof_5.receiveShadow = options.receiveShadow ?? true;
  mesh_roof_5.userData.sculptComponent = {"id": "roof", "name": "Telescoping cover / roof", "level": "macro", "role": "lid", "importance": 0.95, "confidence": 0.9, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Flat wooden lid slightly larger than supers with metal drip edge.", "geometryDescriptor": {"topologyIntent": "flat lid plank with slight overhang", "edgeTreatment": {"type": "bevel", "bevelRadius": 0.003, "segments": 2}, "deformationStack": [], "uvStrategy": "box UV", "normalStrategy": "vertex normals"}, "parent": "root", "attachment": {"parentSocket": "top-socket", "localStart": [0, 1.2000000000000002, 0], "localEnd": [0, 1.2800000000000002, 0], "contactType": "abut", "embedDepth": 0.0, "overlap": 0.0, "gapTolerance": 0.003, "notes": "Attached to root"}, "dimensions": {"width": 1.06, "height": 0.08, "depth": 0.8799999999999999, "units": "relative", "confidence": 0.9}, "transform": {"position": [0, 1.2400000000000002, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "actionProfile": {"animationRole": "lid", "pivot": {"mode": "back-hinge", "localPosition": [0, 0, -0.328], "axis": [1, 0, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": true, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""}, "constraints": [], "destruction": {"breakable": true, "fractureGroup": "roof", "seamRefs": [], "detachableFragments": [], "breakImpulse": 12.0, "debrisMaterial": "varnished-pine"}}, "material": "varnished-pine", "materialLayers": ["varnished-pine", "galvanized-steel"], "deformations": [], "joints": [], "seams": [{"with": "super-top", "type": "abut-seam"}], "localFeatures": [{"id": "flat-top", "kind": "plane", "description": "Planar top surface"}], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.15, "bumpAmplitude": 0.01, "normalPattern": "wood-grain", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": ["metal-roof-drip-edge", "roof-wire-handle"], "fidelityTier": "structural", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 160, 112, 1.0)", "secondaryAlbedo": "rgba(166, 124, 74, 1.0)", "materialClass": "wood", "materialClassConfidence": 0.88}};
  node_roof_5.add(mesh_roof_5);
  meshes["roof"] = mesh_roof_5;
  colliders["roof"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": ""};
  destructionGroups["roof"] ??= [];
  destructionGroups["roof"].push(node_roof_5);

  root.userData.sculptRuntime = { nodes, meshes, sockets, colliders, destructionGroups } satisfies ProceduralModelRuntime;
  root.userData.lookDevTargets = {"qualityPriority": "reference-fidelity", "materialPass": {"albedoPaletteRequired": true, "roughnessVariationRequired": true, "normalOrBumpRequired": true, "localOverridesRequired": true, "minimumTextureResolution": 1024, "preferredTextureResolution": 2048, "independentMapChannels": ["albedo", "roughness", "height", "normal", "ambient-occlusion"], "requiredSurfaceFrequencyBands": ["macro", "meso", "micro"], "geometryReliefRequiredWhenSilhouetteAffected": true, "referencePbrExtraction": {"requiredWhenSourceImagePresent": true, "targetThreshold": 0.7, "stopOnLowConfidence": true, "script": "forge/stage1_intake/extract_pbr_evidence.py", "acceptedLimitation": "single-image extraction is reference-derived inference, not exact photogrammetry"}, "mustAvoid": ["single flat albedo per material", "uniform roughness", "albedo texture reused as roughness/height/normal/AO", "single-frequency random noise", "plastic-looking smooth bark, stone, cloth, foliage, or aged material", "local color/detail described only in prose without material masks", "claiming exact PBR recovery when confidence is below the target threshold"]}, "lightingPass": {"requiredTerms": ["key light", "fill light", "rim or environment light", "exposure", "tone mapping", "background", "contact shadow"], "mustAvoid": ["ambient-only lighting", "flat value range", "missing contact shadow", "reference lighting copied without separating material readability"]}, "screenshotReview": ["Compare albedo palette and local color zones.", "Compare roughness/normal/bump response under light.", "Compare cavity dirt, edge wear, stains, moss, scratches, or other local masks.", "Compare key/fill/rim structure, exposure, tone mapping, background, and contact shadows.", "Capture a neutral-light render to verify material readability without reference lighting.", "Capture a grazing-light close-up to expose flat normals, uniform roughness, tiling, and plastic highlights.", "Capture a reference-matched render from the same camera framing as the source."], "primaryMaterials": ["varnished-pine", "galvanized-steel", "entrance-metal"], "exposure": 1.0, "toneMapping": "ACES filmic", "contactShadow": "ground plane contact shadow + seam AO", "groundShadow": true};
  root.userData.actionReadiness = {
    note: 'Use root.userData.sculptRuntime.nodes for transforms, sockets for attachments, colliders for physics proxies, and destructionGroups for breakable sets.',
  };
  return root;
}

export function createLangstrothBeehiveLookDevLights(
  mode: 'neutral' | 'grazing' | 'reference' = 'neutral',
): THREE.Group {
  const lights = new THREE.Group();
  lights.name = "Langstroth Beehive look-dev lights";
  const hemi = new THREE.HemisphereLight(
    mode === 'reference' ? 0xfff0d6 : 0xf2f4ff,
    0x363b42,
    mode === 'grazing' ? 0.28 : mode === 'reference' ? 0.72 : 0.85,
  );
  lights.add(hemi);
  const key = new THREE.DirectionalLight(
    mode === 'reference' ? 0xffcf8a : 0xfff4e8,
    mode === 'grazing' ? 4.2 : mode === 'reference' ? 2.6 : 2.15,
  );
  if (mode === 'grazing') key.position.set(7.5, 1.1, 4.0);
  else if (mode === 'reference') key.position.set(-4.5, 7.5, 5.0);
  else key.position.set(-4.0, 6.0, 5.5);
  key.castShadow = true;
  key.shadow.mapSize.set(4096, 4096);
  key.shadow.bias = -0.00025;
  key.shadow.normalBias = 0.018;
  key.shadow.radius = 7;
  key.shadow.blurSamples = 24;
  key.shadow.camera.near = 0.5;
  key.shadow.camera.far = 30;
  key.shadow.camera.left = -2.6;
  key.shadow.camera.right = 2.6;
  key.shadow.camera.top = 2.6;
  key.shadow.camera.bottom = -2.6;
  key.shadow.camera.updateProjectionMatrix();
  lights.add(key);
  const fill = new THREE.DirectionalLight(0xa8c4ff, mode === 'grazing' ? 0.12 : 0.42);
  fill.position.set(4.0, 3.0, 3.5);
  lights.add(fill);
  const rim = new THREE.DirectionalLight(0xfff1c4, mode === 'grazing' ? 0.28 : 0.85);
  rim.position.set(0.5, 4.5, -6.0);
  lights.add(rim);
  lights.userData.reviewMode = mode;
  lights.userData.lightingFromPhoto = [{"id": "key", "type": "directional", "role": "key", "directionHint": [-0.45, 0.75, 0.45], "color": "#FFF5E6", "intensity": 1.35, "notes": "Soft upper-left studio key; warm. exposure ~1.0 EV; tone mapping ACES filmic; contact shadow under hive on ground plane."}, {"id": "fill", "type": "hemisphere", "role": "fill", "skyColor": "#F0F2F5", "groundColor": "#C8C0B4", "intensity": 0.45, "notes": "Neutral soft fill from white cyclorama. soft ground shadow and ambient occlusion in stack seams."}, {"id": "rim", "type": "directional", "role": "rim", "directionHint": [0.55, 0.35, -0.65], "color": "#E8EEF5", "intensity": 0.35, "notes": "Subtle cool rim separating hive from white background."}, {"id": "env", "type": "environment", "role": "reflection", "preset": "studio-soft", "intensity": 0.55, "notes": "Low-contrast studio env for varnish reflections."}];
  lights.userData.lookDevTargets = {"qualityPriority": "reference-fidelity", "materialPass": {"albedoPaletteRequired": true, "roughnessVariationRequired": true, "normalOrBumpRequired": true, "localOverridesRequired": true, "minimumTextureResolution": 1024, "preferredTextureResolution": 2048, "independentMapChannels": ["albedo", "roughness", "height", "normal", "ambient-occlusion"], "requiredSurfaceFrequencyBands": ["macro", "meso", "micro"], "geometryReliefRequiredWhenSilhouetteAffected": true, "referencePbrExtraction": {"requiredWhenSourceImagePresent": true, "targetThreshold": 0.7, "stopOnLowConfidence": true, "script": "forge/stage1_intake/extract_pbr_evidence.py", "acceptedLimitation": "single-image extraction is reference-derived inference, not exact photogrammetry"}, "mustAvoid": ["single flat albedo per material", "uniform roughness", "albedo texture reused as roughness/height/normal/AO", "single-frequency random noise", "plastic-looking smooth bark, stone, cloth, foliage, or aged material", "local color/detail described only in prose without material masks", "claiming exact PBR recovery when confidence is below the target threshold"]}, "lightingPass": {"requiredTerms": ["key light", "fill light", "rim or environment light", "exposure", "tone mapping", "background", "contact shadow"], "mustAvoid": ["ambient-only lighting", "flat value range", "missing contact shadow", "reference lighting copied without separating material readability"]}, "screenshotReview": ["Compare albedo palette and local color zones.", "Compare roughness/normal/bump response under light.", "Compare cavity dirt, edge wear, stains, moss, scratches, or other local masks.", "Compare key/fill/rim structure, exposure, tone mapping, background, and contact shadows.", "Capture a neutral-light render to verify material readability without reference lighting.", "Capture a grazing-light close-up to expose flat normals, uniform roughness, tiling, and plastic highlights.", "Capture a reference-matched render from the same camera framing as the source."], "primaryMaterials": ["varnished-pine", "galvanized-steel", "entrance-metal"], "exposure": 1.0, "toneMapping": "ACES filmic", "contactShadow": "ground plane contact shadow + seam AO", "groundShadow": true};
  return lights;
}

// PBR materials (clearcoat/iridescence/transmission/anisotropy) need an environment
// map to visually behave as intended — call this once per renderer and assign the
// result to scene.environment before rendering. No external HDR asset required.
export function createLangstrothBeehiveEnvironment(renderer: THREE.WebGLRenderer): THREE.Texture {
  const pmrem = new THREE.PMREMGenerator(renderer);
  const texture = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  pmrem.dispose();
  return texture;
}

// Plan 1.3 §3.2 — auto-framing by bounding box. The Divine Eye can only compare a
// render to the reference if the object is FRAMED consistently (an object framed
// differently scores as wrong even when its shape is right). This positions the camera
// deterministically from the object's bounding box so it fills the frame at a stable
// margin, and sets near/far to the object scale. Call after adding the model to the
// scene, and again on resize (after updating camera.aspect).
export function frameLangstrothBeehiveCamera(
  camera: THREE.PerspectiveCamera,
  object: THREE.Object3D,
  options: { margin?: number; azimuthDeg?: number; elevationDeg?: number } = {},
): void {
  const box = new THREE.Box3().setFromObject(object);
  if (box.isEmpty()) return;
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const margin = options.margin ?? 1.15;
  const maxDim = Math.max(size.x, size.y, size.z) * margin;
  const fov = (camera.fov * Math.PI) / 180;
  // distance so the largest object dimension fits vertically in the frame
  const distance = (maxDim / 2) / Math.tan(fov / 2);
  const az = ((options.azimuthDeg ?? 0) * Math.PI) / 180;
  const el = ((options.elevationDeg ?? 0) * Math.PI) / 180;
  const dir = new THREE.Vector3(
    Math.sin(az) * Math.cos(el),
    Math.sin(el),
    Math.cos(az) * Math.cos(el),
  );
  camera.position.copy(center).addScaledVector(dir, distance);
  camera.near = Math.max(0.01, distance - maxDim);
  camera.far = distance + maxDim * 2;
  camera.lookAt(center);
  camera.updateProjectionMatrix();
}

// Plan 1.3 §3.2c — PRESENTATION composer (DOF + bloom). CRITICAL (R-POSTFX): this is
// for the showcase/hero render ONLY. The Divine Eye's EVALUATION render MUST use a
// plain renderer with NO composer — bloom blows highlights and DOF blurs edges, which
// would corrupt the deterministic IoU/DCD/edge/blowout signals. Enable dof/bloom ONLY
// when the reference photo actually exhibits them (detect_reference_effects.py authorizes).
export function createLangstrothBeehivePresentationComposer(
  renderer: THREE.WebGLRenderer,
  scene: THREE.Scene,
  camera: THREE.Camera,
  options: { dof?: boolean; bloom?: boolean; bloomStrength?: number; dofFocus?: number; dofAperture?: number } = {},
): EffectComposer {
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  if (options.dof) {
    composer.addPass(new BokehPass(scene, camera, {
      focus: options.dofFocus ?? 10.0,
      aperture: options.dofAperture ?? 0.0002,
      maxblur: 0.01,
    }));
  }
  if (options.bloom) {
    const size = new THREE.Vector2();
    renderer.getSize(size);
    composer.addPass(new UnrealBloomPass(size, options.bloomStrength ?? 0.4, 0.4, 0.85));
  }
  return composer;
}

export function configureLangstrothBeehiveRenderer(renderer: THREE.WebGLRenderer): void {
  // Load-bearing for view-dependent finishes (anodized / Doppler): without ACES + sRGB
  // the environment reflection reads flat/washed instead of a believable metal response.
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
}

export function createLangstrothBeehiveInspectControls(
  camera: THREE.Camera,
  domElement: HTMLElement,
): OrbitControls {
  // View-dependent finishes only read correctly once the user orbits — their color
  // comes from the environment reflection, not albedo, so free rotation matters here.
  const controls = new OrbitControls(camera, domElement);
  controls.enableDamping = true;
  controls.minDistance = 1.0;
  controls.maxDistance = 8.0;
  controls.autoRotate = false;
  return controls;
}
