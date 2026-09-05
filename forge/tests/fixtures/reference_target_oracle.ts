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
  return [clampAlbedoChannel((value >> 16) & 255), clampAlbedoChannel((value >> 8) & 255), clampAlbedoChannel(value & 255)];
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

function clampAlbedoChannel(value: number): number {
  return Math.max(30, Math.min(240, Math.round(value)));
}

function clampPbrF0(value: number): number {
  return Math.max(0.02, Math.min(1, value));
}

function clampPbrIor(value: number): number {
  return Math.max(1, Math.min(2.5, value));
}

function clampPbrMetalness(value: number): number {
  return value >= 0.5 ? 1 : 0;
}

function clampedAlbedoColor(spec: SculptMaterialSpec): THREE.Color {
  const source = typeof spec.baseColor === 'string' ? spec.baseColor : '#8A7A5F';
  // setStyle with an explicit SRGBColorSpace, NOT the numeric constructor.
  //
  // `new THREE.Color(r, g, b)` treats its arguments as LINEAR working-space components,
  // while an authored `baseColor` hex is sRGB. Feeding one to the other skipped the
  // transfer function and lifted every dark albedo: #2e2a28, authored as a near-black
  // vinyl, rendered at roughly sRGB 0.46 — a mid grey. The error is largest exactly where
  // it matters most, because the transfer curve is steepest near black.
  return new THREE.Color().setStyle(source, THREE.SRGBColorSpace);
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
  return [clampAlbedoChannel(Number(match[1])), clampAlbedoChannel(Number(match[2])), clampAlbedoChannel(Number(match[3]))];
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

function createSculptMaterial(id: string, spec: SculptMaterialSpec, options: ProceduralModelOptions, denseComponent = false): THREE.MeshPhysicalMaterial {
  // A material that declares -- with evidence -- that its subject carries no texture
  // detail gets NO texture set. Synthesising one anyway is not a harmless default: the
  // branch below then forces color to white and roughness to 1 and reads both from the
  // generated maps, so the authored albedo and the reference-derived roughness are both
  // discarded, and the model gains mottling the reference does not have. Measured on the
  // tuxedo cat, whose black fur rendered as speckled grey-and-white from a palette that
  // only ever described two flat regions.
  const textureless = (spec.textureless as { declared?: boolean } | undefined)?.declared === true;
  const textures = textureless
    ? null
    : makeReferenceTextureSet(spec, options) ?? makeProceduralTextureSet(id, spec, options);
  const material = new THREE.MeshPhysicalMaterial({
    color: textures ? 0xffffff : clampedAlbedoColor(spec),
    roughness: textures ? 1 : clamp01(readLayerNumber(spec.roughness, ['base'], 0.76)),
    metalness: clampPbrMetalness(readLayerNumber(spec.metalness, ['base'], 0.0)),
    clearcoat: clamp01(readLayerNumber(spec.clearcoat, ['base', 'amount'], 0)),
    clearcoatRoughness: clamp01(readLayerNumber(spec.clearcoatRoughness, ['base'], 0.25)),
    transmission: clamp01(readLayerNumber(spec.transmission, ['base', 'amount'], 0)),
    ior: clampPbrIor(readLayerNumber(spec.ior, ['base', 'value'], 1.5)),
    thickness: Math.max(0, readLayerNumber(spec.thickness, ['base', 'amount'], 0)),
    attenuationDistance: Math.max(0.001, readLayerNumber(spec.attenuationDistance, ['base', 'value'], Infinity)),
    attenuationColor: new THREE.Color(typeof spec.attenuationColor === 'string' ? spec.attenuationColor : '#ffffff'),
    sheen: clamp01(readLayerNumber(spec.sheen, ['base', 'amount'], 0)),
    sheenColor: new THREE.Color(typeof spec.sheenColor === 'string' ? spec.sheenColor : '#ffffff'),
    sheenRoughness: clamp01(readLayerNumber(spec.sheenRoughness, ['base'], 1.0)),
    iridescence: clamp01(readLayerNumber(spec.iridescence, ['base', 'amount'], 0)),
    iridescenceIOR: clampPbrIor(readLayerNumber(spec.iridescenceIOR, ['base', 'value'], 1.3)),
    anisotropy: clamp01(readLayerNumber(spec.anisotropy, ['base', 'amount'], 0)),
    anisotropyRotation: readLayerNumber(spec.anisotropy, ['rotation'], 0),
    specularIntensity: clampPbrF0(readLayerNumber(spec.specularF0 ?? spec.f0 ?? spec.specularIntensity, ['base', 'value'], 1.0)),
    specularColor: new THREE.Color(typeof spec.specularColor === 'string' ? spec.specularColor : '#ffffff'),
    emissive: new THREE.Color(typeof spec.emissive === 'string' ? spec.emissive : '#000000'),
    emissiveIntensity: Math.max(0, readLayerNumber(spec.emissiveIntensity, ['base'], 1.0)),
    opacity: clamp01(readLayerNumber(spec.opacity, ['base'], 1)),
    transparent: readLayerNumber(spec.transmission, ['base', 'amount'], 0) > 0 || readLayerNumber(spec.opacity, ['base'], 1) < 1,
    alphaTest: Math.max(0, readLayerNumber(spec.alpha, ['cutoff', 'alphaTest'], 0)),
    wireframe: options.wireframe ?? false,
    side: spec.doubleSided === true ? THREE.DoubleSide : THREE.FrontSide,
    flatShading: spec.flatShading === true,
  });
  if (textures) {
    material.map = textures.albedo;
    material.roughnessMap = textures.roughness;
    material.normalMap = textures.normal;
    material.normalScale.setScalar(Math.max(0.05, readLayerNumber(spec.normal, ['strength', 'amplitude'], 0.35)));
    material.aoMap = textures.ao;
    material.aoMap.channel = 0;
    material.aoMapIntensity = readLayerNumber(spec.ambientOcclusion, ['cavityStrength', 'strength'], 0.35);
    const denseMesh = denseComponent || spec.denseMesh === true || spec.geometryDensity === 'dense' || spec.topologyClass === 'dense';
    const bumpScale = Math.max(0, readLayerNumber(spec.bump, ['amplitude', 'strength'], 0));
    const effectiveBumpScale = denseMesh ? Math.max(0.05, bumpScale) : bumpScale;
    if (effectiveBumpScale > 0) {
      material.bumpMap = textures.height;
      material.bumpScale = effectiveBumpScale;
    }
    const displacementScale = Math.max(0, readLayerNumber(spec.displacement, ['amplitude', 'strength'], 0));
    const effectiveDisplacementScale = denseMesh ? Math.max(0.005, displacementScale) : displacementScale;
    if (effectiveDisplacementScale > 0) {
      material.displacementMap = textures.height;
      material.displacementScale = effectiveDisplacementScale;
      material.displacementBias = -effectiveDisplacementScale * 0.5;
    }
  }
  material.envMapIntensity = readLayerNumber(spec, ['envMapIntensity'], 0.8);
  material.userData.sculptMaterial = spec;
  material.userData.proceduralMapsIndependent = true;
  material.userData.pbrConstraints = { albedoRange: [30, 240], binaryMetalness: true, f0Range: [0.02, 1], iorRange: [1, 2.5] };
  material.userData.pbrTextureSource = textures?.source ?? 'flat-fallback';
  material.userData.referencePbr = spec.referencePbr ?? null;
  material.userData.referenceMaterialId = spec.referenceMaterialId ?? spec.materialReference?.profileId ?? null;
  material.userData.materialEvidence = spec.materialEvidence ?? null;
  material.userData.validationViews = spec.materialReference?.validationViews ?? [];
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

// Generated from ObjectSculptSpec target: TradeIt Hard Case
// Sculpt build pass: blockout
// This factory is intentionally pass-gated. Finish browser screenshot review before unlocking deeper passes.
export function createTradeItHardCaseModel(options: ProceduralModelOptions = {}): THREE.Group {
  const root = new THREE.Group();
  root.name = "TradeIt Hard Case";
  root.userData.reconstructionEvidence = {"itemFamily": null, "subtype": null, "componentAdapter": null, "route": null, "exactnessTier": null, "referenceCamera": {"solved": false, "fovDegrees": 35.0, "aspect": 1.0, "orientation": {"yaw": 28.0, "pitch": 12.0, "roll": 0.0}, "positionHint": [1.1, 0.55, 1.8], "note": "3/4 front-left studio framing, not solved via solve_camera_pose.py (flat print decal route does not require pixel-exact projection); used only as a review-render hint."}, "approximationNotes": []};
  root.userData.materialPipeline = {};
  root.userData.materialReferenceRegistry = null;

  const materialMap: Record<string, THREE.Material> = {};
  materialMap["shellMatte"] = createSculptMaterial(
    "shellMatte",
    {"id": "shellMatte", "name": "Rotomolded shell (matte composite)", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#212327", "color": "#212327", "albedo": {"dominant": "#212327", "secondary": ["#242629", "#181a1c"], "samplingNotes": "Estimated by eye from a 400x492px studio photo with light falloff toward the silhouette edge; not spectrophotometric. Near-black, slightly warm dark gray, matte -- no crisp specular hotspot on flat panels."}, "colorVariation": {"palette": ["#212327", "#242629", "#181a1c"], "pattern": "uniform", "amplitude": 0.06, "heightCorrelation": 0.1}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [1.0, 1.0], "anisotropy": 8, "texelDensityIntent": "Preserve stable world-scale detail; decal graphics use a dedicated canvas-baked UV island, not tiled repeat."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 1.5, "amplitude": 0.1, "role": "broad color/value read"}, {"id": "meso", "frequency": 8.0, "amplitude": 0.05, "role": "fine matte grain / brushing"}, {"id": "micro", "frequency": 32.0, "amplitude": 0.02, "role": "highlight breakup under grazing light"}], "roughness": {"base": 0.62, "variation": 0.08, "map": "independent-procedural-field", "localResponse": "slightly lower roughness on rounded edges/ribs than on flat panels"}, "metalness": {"base": 0.0, "variation": 0.03}, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.15, "scale": 20.0, "space": "tangent"}, "bump": {"pattern": "fine-matte-grain", "amplitude": 0.01, "scale": 4.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.3, "contactShadowBias": 0.3, "notes": "Darken the lid/body seam, latch mounts, hasp loop, and handle pocket."}, "wear": {"edgeWear": 0.05, "scratches": [], "chips": []}, "dirt": {"amount": 0.0, "cavityBias": 0.0, "color": "#2F2A22"}, "localOverrides": [{"id": "tradeitLogoIcon", "description": "Circular blue double-arrow/handshake icon baked into a canvas decal texture, flat print (#2e57e0 fill, white #f2f2f2 linework), no separate geometry/relief.", "confidence": 0.75}, {"id": "tradeitWordmark", "description": "White 'TRADEIT' wordmark below the icon, same canvas decal, flat print, no relief.", "confidence": 0.75}, {"id": "diagonalChevron", "description": "Wide diagonal blue (#2e57e0) wedge/chevron baked into the same canvas decal, widening from upper-right toward lower-left across the front-right third of the shell.", "confidence": 0.7}, {"id": "frontFacetLines", "description": "Faint vertical panel/facet lines on the lower front face — low-medium confidence, could be render/lighting artifact rather than molded relief; represented as a subtle normal-map micro-band, not geometry, so it can be safely wrong.", "confidence": 0.35}], "shaderNotes": ["Approximate PBR inference from a single low-resolution studio photo, not extracted measurement; confidence flagged per-field in preSpecAssessment.unknownsToResolveBeforeImplementation."]},
    options
  );
  materialMap["hardwareSatin"] = createSculptMaterial(
    "hardwareSatin",
    {"id": "hardwareSatin", "name": "Latch/rivet/loop hardware (satin metal)", "type": "physical", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#8c8e92", "color": "#8c8e92", "albedo": {"dominant": "#8c8e92", "secondary": ["#a2a4a8", "#5a5c60"], "samplingNotes": "Estimated by eye from a 400x492px studio photo with light falloff toward the silhouette edge; not spectrophotometric. Latch wire bail, rivet/screw heads, hasp eyeloop -- brighter, higher-specular than the shell."}, "colorVariation": {"palette": ["#8c8e92", "#a2a4a8", "#5a5c60"], "pattern": "mottled", "amplitude": 0.06, "heightCorrelation": 0.1}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [1.0, 1.0], "anisotropy": 8, "texelDensityIntent": "Preserve stable world-scale detail; decal graphics use a dedicated canvas-baked UV island, not tiled repeat."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 1.5, "amplitude": 0.1, "role": "broad color/value read"}, {"id": "meso", "frequency": 8.0, "amplitude": 0.05, "role": "fine matte grain / brushing"}, {"id": "micro", "frequency": 32.0, "amplitude": 0.02, "role": "highlight breakup under grazing light"}], "roughness": {"base": 0.35, "variation": 0.08, "map": "independent-procedural-field", "localResponse": "slightly lower roughness on rounded edges/ribs than on flat panels"}, "metalness": {"base": 0.78, "variation": 0.03}, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.3, "scale": 20.0, "space": "tangent"}, "bump": {"pattern": "brushed-linear", "amplitude": 0.01, "scale": 4.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.3, "contactShadowBias": 0.3, "notes": "Darken the lid/body seam, latch mounts, hasp loop, and handle pocket."}, "wear": {"edgeWear": 0.05, "scratches": [], "chips": []}, "dirt": {"amount": 0.0, "cavityBias": 0.0, "color": "#2F2A22"}, "localOverrides": [], "shaderNotes": ["Approximate PBR inference from a single low-resolution studio photo, not extracted measurement; confidence flagged per-field in preSpecAssessment.unknownsToResolveBeforeImplementation."]},
    options
  );
  materialMap["brassPlated"] = createSculptMaterial(
    "brassPlated",
    {"id": "brassPlated", "name": "Padlock body (plated brass)", "type": "physical", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#c9a227", "color": "#c9a227", "albedo": {"dominant": "#c9a227", "secondary": ["#d8b646", "#8f701a"], "samplingNotes": "Estimated by eye from a 400x492px studio photo with light falloff toward the silhouette edge; not spectrophotometric. Warm gold-yellow plated metal, small in frame (~28x40px), color read solid, roughness estimated."}, "colorVariation": {"palette": ["#c9a227", "#d8b646", "#8f701a"], "pattern": "mottled", "amplitude": 0.06, "heightCorrelation": 0.1}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [1.0, 1.0], "anisotropy": 8, "texelDensityIntent": "Preserve stable world-scale detail; decal graphics use a dedicated canvas-baked UV island, not tiled repeat."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 1.5, "amplitude": 0.1, "role": "broad color/value read"}, {"id": "meso", "frequency": 8.0, "amplitude": 0.05, "role": "fine matte grain / brushing"}, {"id": "micro", "frequency": 32.0, "amplitude": 0.02, "role": "highlight breakup under grazing light"}], "roughness": {"base": 0.3, "variation": 0.08, "map": "independent-procedural-field", "localResponse": "slightly lower roughness on rounded edges/ribs than on flat panels"}, "metalness": {"base": 0.75, "variation": 0.03}, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.3, "scale": 20.0, "space": "tangent"}, "bump": {"pattern": "brushed-linear", "amplitude": 0.01, "scale": 4.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.3, "contactShadowBias": 0.3, "notes": "Darken the lid/body seam, latch mounts, hasp loop, and handle pocket."}, "wear": {"edgeWear": 0.05, "scratches": [], "chips": []}, "dirt": {"amount": 0.0, "cavityBias": 0.0, "color": "#2F2A22"}, "localOverrides": [], "shaderNotes": ["Approximate PBR inference from a single low-resolution studio photo, not extracted measurement; confidence flagged per-field in preSpecAssessment.unknownsToResolveBeforeImplementation."]},
    options
  );
  materialMap["steelSatin"] = createSculptMaterial(
    "steelSatin",
    {"id": "steelSatin", "name": "Padlock shackle + latch bail (satin steel)", "type": "physical", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#9a9ca2", "color": "#9a9ca2", "albedo": {"dominant": "#9a9ca2", "secondary": ["#b4b6bc", "#6a6c72"], "samplingNotes": "Estimated by eye from a 400x492px studio photo with light falloff toward the silhouette edge; not spectrophotometric. Cooler gray than the brass body; shared finish class with hardwareSatin but kept distinct for the shackle's slightly higher metalness/lower roughness observed highlight falloff."}, "colorVariation": {"palette": ["#9a9ca2", "#b4b6bc", "#6a6c72"], "pattern": "mottled", "amplitude": 0.06, "heightCorrelation": 0.1}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [1.0, 1.0], "anisotropy": 8, "texelDensityIntent": "Preserve stable world-scale detail; decal graphics use a dedicated canvas-baked UV island, not tiled repeat."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 1.5, "amplitude": 0.1, "role": "broad color/value read"}, {"id": "meso", "frequency": 8.0, "amplitude": 0.05, "role": "fine matte grain / brushing"}, {"id": "micro", "frequency": 32.0, "amplitude": 0.02, "role": "highlight breakup under grazing light"}], "roughness": {"base": 0.3, "variation": 0.08, "map": "independent-procedural-field", "localResponse": "slightly lower roughness on rounded edges/ribs than on flat panels"}, "metalness": {"base": 0.85, "variation": 0.03}, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.3, "scale": 20.0, "space": "tangent"}, "bump": {"pattern": "brushed-linear", "amplitude": 0.01, "scale": 4.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.3, "contactShadowBias": 0.3, "notes": "Darken the lid/body seam, latch mounts, hasp loop, and handle pocket."}, "wear": {"edgeWear": 0.05, "scratches": [], "chips": []}, "dirt": {"amount": 0.0, "cavityBias": 0.0, "color": "#2F2A22"}, "localOverrides": [], "shaderNotes": ["Approximate PBR inference from a single low-resolution studio photo, not extracted measurement; confidence flagged per-field in preSpecAssessment.unknownsToResolveBeforeImplementation."]},
    options
  );

  const nodes: Record<string, THREE.Object3D> = { root };
  const meshes: Record<string, THREE.Mesh> = {};
  const sockets: Record<string, THREE.Object3D> = {};
  const colliders: Record<string, unknown> = {};
  const destructionGroups: Record<string, THREE.Object3D[]> = {};

  const endpoint_root_0 = makeAttachmentEndpoint(null);
  const node_root_0 = new THREE.Group();
  node_root_0.name = "Body shell__pivot";
  node_root_0.scale.set(1, 1, 1);
  if (endpoint_root_0) {
    node_root_0.position.copy(endpoint_root_0.start);
    node_root_0.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_root_0.position.set(0.0, 0.0, 0.0);
    node_root_0.rotation.set(0.0, 0.0, 0.0);
  }
  node_root_0.userData.sculptComponent = {"id": "root", "name": "Body shell", "level": "macro", "role": "body", "importance": 1.0, "confidence": 0.75, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Rigid molded lower shell assembled from a rounded box blockout with a taper deform; not an organic/continuous surface.", "geometryDescriptor": {"topologyIntent": "tapered rounded box, full width at the top seam narrowing toward the base", "edgeTreatment": {"type": "rounded", "bevelRadius": 0.045, "segments": 4}, "deformationStack": [{"type": "taper", "axis": "y", "factor": 0.86, "notes": "body narrows toward the base (rotomold draft angle); silhouette-affecting, estimated ~4-6deg/side"}], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": null, "attachment": null, "dimensions": {"width": 1.0, "height": 0.46, "depth": 0.78, "units": "relative", "confidence": 0.75}, "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "material": "shellMatte", "materialLayers": ["shellMatte"], "colorMaterialRecipe": {"dominantAlbedo": "rgba(33, 35, 39, 1.0)", "secondaryAlbedo": "rgba(24, 26, 28, 1.0)", "materialClass": "plastic", "materialClassConfidence": 0.6, "evidence": ["closed-front-3q"], "samplingNotes": "Near-black warm dark gray read from mid-tone shell pixels, discounting studio falloff toward pure black at the silhouette edge."}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "explicit", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.6}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "Coarse proxy; refine after first browser render."}, "constraints": [], "destruction": {"breakable": false, "breakImpulse": 0.0, "fractureGroup": "", "debrisMaterial": "", "seamRefs": [], "detachableFragments": []}}, "localFeatures": [{"id": "rounded-bevel-edges", "description": "Continuous rounded fillet on all 12 body/lid edges, molded in.", "confidence": 0.8, "level": "macro", "evidence": ["closed-front-3q"]}, {"id": "body-taper", "description": "Body narrows from lid seam to base.", "confidence": 0.7, "level": "macro", "evidence": ["closed-front-3q"]}, {"id": "front-facet-lines", "description": "Faint vertical panel lines, low-medium confidence -- see material localOverrides.frontFacetLines.", "confidence": 0.35, "level": "micro", "evidence": ["closed-front-3q"]}], "evidenceRefs": ["closed-front-3q"]};
  node_root_0.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "explicit", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.6}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "Coarse proxy; refine after first browser render."}, "constraints": [], "destruction": {"breakable": false, "breakImpulse": 0.0, "fractureGroup": "", "debrisMaterial": "", "seamRefs": [], "detachableFragments": []}};
  (nodes["root"] ?? root).add(node_root_0);
  nodes["root"] = node_root_0;
  const mesh_root_0Geometry = endpoint_root_0
    ? new THREE.CylinderGeometry(endpoint_root_0.endRadius, endpoint_root_0.baseRadius, endpoint_root_0.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_root_0) {
    mesh_root_0Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_root_0 = new THREE.Mesh(
    mesh_root_0Geometry,
    materialMap["shellMatte"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_root_0.name = "Body shell";
  if (endpoint_root_0) {
    mesh_root_0.position.copy(endpoint_root_0.midpoint);
    mesh_root_0.quaternion.copy(endpoint_root_0.quaternion);
  }
  mesh_root_0.castShadow = options.castShadow ?? true;
  mesh_root_0.receiveShadow = options.receiveShadow ?? true;
  mesh_root_0.userData.sculptComponent = {"id": "root", "name": "Body shell", "level": "macro", "role": "body", "importance": 1.0, "confidence": 0.75, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Rigid molded lower shell assembled from a rounded box blockout with a taper deform; not an organic/continuous surface.", "geometryDescriptor": {"topologyIntent": "tapered rounded box, full width at the top seam narrowing toward the base", "edgeTreatment": {"type": "rounded", "bevelRadius": 0.045, "segments": 4}, "deformationStack": [{"type": "taper", "axis": "y", "factor": 0.86, "notes": "body narrows toward the base (rotomold draft angle); silhouette-affecting, estimated ~4-6deg/side"}], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": null, "attachment": null, "dimensions": {"width": 1.0, "height": 0.46, "depth": 0.78, "units": "relative", "confidence": 0.75}, "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "material": "shellMatte", "materialLayers": ["shellMatte"], "colorMaterialRecipe": {"dominantAlbedo": "rgba(33, 35, 39, 1.0)", "secondaryAlbedo": "rgba(24, 26, 28, 1.0)", "materialClass": "plastic", "materialClassConfidence": 0.6, "evidence": ["closed-front-3q"], "samplingNotes": "Near-black warm dark gray read from mid-tone shell pixels, discounting studio falloff toward pure black at the silhouette edge."}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "explicit", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.6}, "transformChannels": {"translate": true, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "Coarse proxy; refine after first browser render."}, "constraints": [], "destruction": {"breakable": false, "breakImpulse": 0.0, "fractureGroup": "", "debrisMaterial": "", "seamRefs": [], "detachableFragments": []}}, "localFeatures": [{"id": "rounded-bevel-edges", "description": "Continuous rounded fillet on all 12 body/lid edges, molded in.", "confidence": 0.8, "level": "macro", "evidence": ["closed-front-3q"]}, {"id": "body-taper", "description": "Body narrows from lid seam to base.", "confidence": 0.7, "level": "macro", "evidence": ["closed-front-3q"]}, {"id": "front-facet-lines", "description": "Faint vertical panel lines, low-medium confidence -- see material localOverrides.frontFacetLines.", "confidence": 0.35, "level": "micro", "evidence": ["closed-front-3q"]}], "evidenceRefs": ["closed-front-3q"]};
  node_root_0.add(mesh_root_0);
  meshes["root"] = mesh_root_0;
  colliders["root"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "Coarse proxy; refine after first browser render."};

  const endpoint_lid_1 = makeAttachmentEndpoint(null);
  const node_lid_1 = new THREE.Group();
  node_lid_1.name = "Lid__pivot";
  node_lid_1.scale.set(1, 1, 1);
  if (endpoint_lid_1) {
    node_lid_1.position.copy(endpoint_lid_1.start);
    node_lid_1.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_lid_1.position.set(0.0, 0.31, 0.0);
    node_lid_1.rotation.set(0.0, 0.0, 0.0);
  }
  node_lid_1.userData.sculptComponent = {"id": "lid", "name": "Lid", "level": "macro", "role": "hinge-lid", "importance": 0.95, "confidence": 0.7, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Rigid molded upper shell, same construction family as the body, hinged as a rigid rotation not a deforming shell.", "geometryDescriptor": {"topologyIntent": "rounded box slab, front-bottom lip step at the seam", "edgeTreatment": {"type": "rounded", "bevelRadius": 0.04, "segments": 4}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "lidHingeSocket", "localStart": [0, 0.23, -0.39], "localEnd": [0, 0.23, 0.39], "contactType": "pivot-hinge", "embedDepth": 0.01, "gapTolerance": 0.004, "evidenceRefs": ["closed-front-3q"]}, "dimensions": {"width": 1.02, "height": 0.16, "depth": 0.7956000000000001, "units": "relative", "confidence": 0.7}, "transform": {"position": [0, 0.31, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "material": "shellMatte", "materialLayers": ["shellMatte"], "colorMaterialRecipe": {"dominantAlbedo": "rgba(33, 35, 39, 1.0)", "secondaryAlbedo": "rgba(24, 26, 28, 1.0)", "materialClass": "plastic", "materialClassConfidence": 0.6, "evidence": ["closed-front-3q"], "samplingNotes": "Near-black warm dark gray read from mid-tone shell pixels, discounting studio falloff toward pure black at the silhouette edge."}, "actionProfile": {"animationRole": "hinge", "pivot": {"mode": "explicit", "localPosition": [0, 0.23, -0.39], "axis": [1, 0, 0], "confidence": 0.6}, "transformChannels": {"translate": false, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "Coarse proxy; refine after first browser render."}, "constraints": [], "destruction": {"breakable": false, "breakImpulse": 0.0, "fractureGroup": "", "debrisMaterial": "", "seamRefs": [], "detachableFragments": []}}, "localFeatures": [{"id": "lid-front-lip-step", "description": "Shallow step in the shell where the lid meets the body seam.", "confidence": 0.6, "level": "meso", "evidence": ["closed-front-3q"]}], "evidenceRefs": ["closed-front-3q", "open-mechanism"]};
  node_lid_1.userData.actionProfile = {"animationRole": "hinge", "pivot": {"mode": "explicit", "localPosition": [0, 0.23, -0.39], "axis": [1, 0, 0], "confidence": 0.6}, "transformChannels": {"translate": false, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "Coarse proxy; refine after first browser render."}, "constraints": [], "destruction": {"breakable": false, "breakImpulse": 0.0, "fractureGroup": "", "debrisMaterial": "", "seamRefs": [], "detachableFragments": []}};
  (nodes["root"] ?? root).add(node_lid_1);
  nodes["lid"] = node_lid_1;
  const mesh_lid_1Geometry = endpoint_lid_1
    ? new THREE.CylinderGeometry(endpoint_lid_1.endRadius, endpoint_lid_1.baseRadius, endpoint_lid_1.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_lid_1) {
    mesh_lid_1Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_lid_1 = new THREE.Mesh(
    mesh_lid_1Geometry,
    materialMap["shellMatte"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_lid_1.name = "Lid";
  if (endpoint_lid_1) {
    mesh_lid_1.position.copy(endpoint_lid_1.midpoint);
    mesh_lid_1.quaternion.copy(endpoint_lid_1.quaternion);
  }
  mesh_lid_1.castShadow = options.castShadow ?? true;
  mesh_lid_1.receiveShadow = options.receiveShadow ?? true;
  mesh_lid_1.userData.sculptComponent = {"id": "lid", "name": "Lid", "level": "macro", "role": "hinge-lid", "importance": 0.95, "confidence": 0.7, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Rigid molded upper shell, same construction family as the body, hinged as a rigid rotation not a deforming shell.", "geometryDescriptor": {"topologyIntent": "rounded box slab, front-bottom lip step at the seam", "edgeTreatment": {"type": "rounded", "bevelRadius": 0.04, "segments": 4}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "lidHingeSocket", "localStart": [0, 0.23, -0.39], "localEnd": [0, 0.23, 0.39], "contactType": "pivot-hinge", "embedDepth": 0.01, "gapTolerance": 0.004, "evidenceRefs": ["closed-front-3q"]}, "dimensions": {"width": 1.02, "height": 0.16, "depth": 0.7956000000000001, "units": "relative", "confidence": 0.7}, "transform": {"position": [0, 0.31, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "material": "shellMatte", "materialLayers": ["shellMatte"], "colorMaterialRecipe": {"dominantAlbedo": "rgba(33, 35, 39, 1.0)", "secondaryAlbedo": "rgba(24, 26, 28, 1.0)", "materialClass": "plastic", "materialClassConfidence": 0.6, "evidence": ["closed-front-3q"], "samplingNotes": "Near-black warm dark gray read from mid-tone shell pixels, discounting studio falloff toward pure black at the silhouette edge."}, "actionProfile": {"animationRole": "hinge", "pivot": {"mode": "explicit", "localPosition": [0, 0.23, -0.39], "axis": [1, 0, 0], "confidence": 0.6}, "transformChannels": {"translate": false, "rotate": true, "scale": false, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "Coarse proxy; refine after first browser render."}, "constraints": [], "destruction": {"breakable": false, "breakImpulse": 0.0, "fractureGroup": "", "debrisMaterial": "", "seamRefs": [], "detachableFragments": []}}, "localFeatures": [{"id": "lid-front-lip-step", "description": "Shallow step in the shell where the lid meets the body seam.", "confidence": 0.6, "level": "meso", "evidence": ["closed-front-3q"]}], "evidenceRefs": ["closed-front-3q", "open-mechanism"]};
  node_lid_1.add(mesh_lid_1);
  meshes["lid"] = mesh_lid_1;
  colliders["lid"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "Coarse proxy; refine after first browser render."};

  root.userData.sculptRuntime = { nodes, meshes, sockets, colliders, destructionGroups } satisfies ProceduralModelRuntime;
  root.userData.lookDevTargets = {"qualityPriority": "reference-fidelity", "materialPass": {"albedoPaletteRequired": true, "roughnessVariationRequired": true, "normalOrBumpRequired": true, "localOverridesRequired": true, "minimumTextureResolution": 1024, "preferredTextureResolution": 2048, "independentMapChannels": ["albedo", "roughness", "height", "normal", "ambient-occlusion"], "requiredSurfaceFrequencyBands": ["macro", "meso", "micro"], "geometryReliefRequiredWhenSilhouetteAffected": true, "referencePbrExtraction": {"requiredWhenSourceImagePresent": false, "targetThreshold": 0.7, "stopOnLowConfidence": true, "script": "forge/stage1_intake/extract_pbr_evidence.py", "acceptedLimitation": "single-image extraction is reference-derived inference, not exact photogrammetry", "skipReason": "Flat matte-plastic shell + 2-color printed decal, no patterned/photoreal skin -- scalar PBR values estimated by eye per grimoire/build/threejs_texture_reference.md 'solid albedo for flat paint' rule instead of extract_pbr_evidence.py. See spec.risks[0]."}, "mustAvoid": ["single flat albedo per material", "uniform roughness", "albedo texture reused as roughness/height/normal/AO", "single-frequency random noise", "plastic-looking smooth bark, stone, cloth, foliage, or aged material", "local color/detail described only in prose without material masks", "claiming exact PBR recovery when confidence is below the target threshold"]}, "lightingPass": {"requiredTerms": ["key light", "fill light", "rim or environment light", "exposure", "tone mapping", "background", "contact shadow"], "mustAvoid": ["ambient-only lighting", "flat value range", "missing contact shadow", "reference lighting copied without separating material readability"]}, "screenshotReview": ["Compare albedo palette and local color zones.", "Compare roughness/normal/bump response under light.", "Compare cavity dirt, edge wear, stains, moss, scratches, or other local masks.", "Compare key/fill/rim structure, exposure, tone mapping, background, and contact shadows.", "Capture a neutral-light render to verify material readability without reference lighting.", "Capture a grazing-light close-up to expose flat normals, uniform roughness, tiling, and plastic highlights.", "Capture a reference-matched render from the same camera framing as the source."]};
  root.userData.actionReadiness = {
    note: 'Use root.userData.sculptRuntime.nodes for transforms, sockets for attachments, colliders for physics proxies, and destructionGroups for breakable sets.',
  };
  return root;
}

export function createTradeItHardCaseLookDevLights(
  mode: 'neutral' | 'grazing' | 'reference' = 'neutral',
): THREE.Group {
  const lights = new THREE.Group();
  lights.name = "TradeIt Hard Case look-dev lights";
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
  lights.userData.lightingFromPhoto = [{"role": "key", "direction": "upper-left, ~45deg elevation", "colorTemp": "neutral-cool studio", "intensity": "moderate", "evidence": ["closed-front-3q"]}, {"role": "fill", "direction": "ambient/bounce, low contrast ratio", "colorTemp": "neutral", "intensity": "low", "evidence": ["closed-front-3q"]}, {"role": "environment", "direction": "soft studio IBL", "colorTemp": "neutral", "intensity": "low", "note": "ACES filmic tone mapping, exposure ~1.0; soft contact shadow under the case on the ground plane.", "evidence": ["closed-front-3q"]}];
  lights.userData.lookDevTargets = {"qualityPriority": "reference-fidelity", "materialPass": {"albedoPaletteRequired": true, "roughnessVariationRequired": true, "normalOrBumpRequired": true, "localOverridesRequired": true, "minimumTextureResolution": 1024, "preferredTextureResolution": 2048, "independentMapChannels": ["albedo", "roughness", "height", "normal", "ambient-occlusion"], "requiredSurfaceFrequencyBands": ["macro", "meso", "micro"], "geometryReliefRequiredWhenSilhouetteAffected": true, "referencePbrExtraction": {"requiredWhenSourceImagePresent": false, "targetThreshold": 0.7, "stopOnLowConfidence": true, "script": "forge/stage1_intake/extract_pbr_evidence.py", "acceptedLimitation": "single-image extraction is reference-derived inference, not exact photogrammetry", "skipReason": "Flat matte-plastic shell + 2-color printed decal, no patterned/photoreal skin -- scalar PBR values estimated by eye per grimoire/build/threejs_texture_reference.md 'solid albedo for flat paint' rule instead of extract_pbr_evidence.py. See spec.risks[0]."}, "mustAvoid": ["single flat albedo per material", "uniform roughness", "albedo texture reused as roughness/height/normal/AO", "single-frequency random noise", "plastic-looking smooth bark, stone, cloth, foliage, or aged material", "local color/detail described only in prose without material masks", "claiming exact PBR recovery when confidence is below the target threshold"]}, "lightingPass": {"requiredTerms": ["key light", "fill light", "rim or environment light", "exposure", "tone mapping", "background", "contact shadow"], "mustAvoid": ["ambient-only lighting", "flat value range", "missing contact shadow", "reference lighting copied without separating material readability"]}, "screenshotReview": ["Compare albedo palette and local color zones.", "Compare roughness/normal/bump response under light.", "Compare cavity dirt, edge wear, stains, moss, scratches, or other local masks.", "Compare key/fill/rim structure, exposure, tone mapping, background, and contact shadows.", "Capture a neutral-light render to verify material readability without reference lighting.", "Capture a grazing-light close-up to expose flat normals, uniform roughness, tiling, and plastic highlights.", "Capture a reference-matched render from the same camera framing as the source."]};
  return lights;
}

// PBR materials (clearcoat/iridescence/transmission/anisotropy) need an environment
// map to visually behave as intended — call this once per renderer and assign the
// result to scene.environment before rendering. No external HDR asset required.
export function createTradeItHardCaseEnvironment(renderer: THREE.WebGLRenderer): THREE.Texture {
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
export function frameTradeItHardCaseCamera(
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
export function createTradeItHardCasePresentationComposer(
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

export function configureTradeItHardCaseRenderer(renderer: THREE.WebGLRenderer): void {
  // Load-bearing for view-dependent finishes (anodized / Doppler): without ACES + sRGB
  // the environment reflection reads flat/washed instead of a believable metal response.
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
}

export function createTradeItHardCaseInspectControls(
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
