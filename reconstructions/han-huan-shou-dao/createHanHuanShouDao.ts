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

// bevelEnabled defaults to true on THREE.ExtrudeGeometry and rounds every
// corner — sharp/pointed profiles (blades, fork tines, spikes) need
// bevelEnabled: false plus lineTo()-only path segments near the tip, since a
// curve command cannot produce a true converging point.
function buildExtrudeShape(points: [number, number][], holes?: [number, number][][]): THREE.Shape {
  const shape = new THREE.Shape();
  if (points.length > 0) {
    shape.moveTo(points[0][0], points[0][1]);
    for (let i = 1; i < points.length; i += 1) {
      shape.lineTo(points[i][0], points[i][1]);
    }
  }
  // Cutouts (e.g. an oval wire-cutter hole) as THREE.Path added to shape.holes —
  // dep-free boolean subtraction via the tessellator, no CSG library needed.
  for (const loop of holes ?? []) {
    if (loop.length < 3) continue;
    const path = new THREE.Path();
    path.moveTo(loop[0][0], loop[0][1]);
    for (let i = 1; i < loop.length; i += 1) path.lineTo(loop[i][0], loop[i][1]);
    path.closePath();
    shape.holes.push(path);
  }
  return shape;
}

// Build an N-gon oval loop (for hole authoring from a compact {cx,cy,rx,ry} descriptor).
function ovalLoop(cx: number, cy: number, rx: number, ry: number, seg = 24): [number, number][] {
  const loop: [number, number][] = [];
  for (let i = 0; i < seg; i += 1) {
    const a = (i / seg) * Math.PI * 2;
    loop.push([cx + Math.cos(a) * rx, cy + Math.sin(a) * ry]);
  }
  return loop;
}

function buildExtrudeGeometry(profile: { points: [number, number][]; depth: number; holes?: [number, number][][]; ovalHoles?: { cx: number; cy: number; rx: number; ry: number }[] }): THREE.ExtrudeGeometry {
  const holes = [...(profile.holes ?? []), ...((profile.ovalHoles ?? []).map((o) => ovalLoop(o.cx, o.cy, o.rx, o.ry)))];
  const shape = buildExtrudeShape(profile.points, holes);
  return new THREE.ExtrudeGeometry(shape, {
    depth: profile.depth,
    bevelEnabled: false,
    steps: 1,
  });
}

// Ground blade: lofts a beveled cross-section along [x, spineY, edgeY] stations.
// Per station the section is: sharp cutting EDGE (z=0) → PRIMARY BEVEL up to the
// grind line (±T) → flat/saber body → SWEDGE near the tip (spine grinds to a false
// edge, z=0) or a squared spine elsewhere. Each cross-section face keeps a hard
// edge while indexed vertices smooth that face along the blade length. UVs map
// the doppler gradient along length (u) and height (v).
function buildGroundBladeGeometry(spec: { stations: [number, number, number][]; thickness?: number; thicknesses?: number[]; grindFrac?: number; swedgeFromTipFrac?: number; edgeTone?: number }): THREE.BufferGeometry {
  const st = spec.stations;
  if (st.length < 2) throw new Error('ground-blade requires at least two stations');
  if (spec.thicknesses && spec.thicknesses.length !== st.length) throw new Error('ground-blade thicknesses must match stations');
  const baseThickness = spec.thickness ?? 0.05;
  const grindFrac = spec.grindFrac ?? 0.55;
  const swedgeFrac = spec.swedgeFromTipFrac ?? 0.34;
  const xG = st[0][0];
  const xT = st[st.length - 1][0];
  const len = (xT - xG) || 1;
  // Actual blade Y bounds (stations are [x, topY, botY]) — v must span THESE, not a
  // hardcoded ±0.12: a blade positioned off-origin would otherwise clamp v→1 and make
  // every face sample the bright spine-rim row (the white-tip/washed-facet bug).
  let yMin = Infinity, yMax = -Infinity;
  for (const s of st) { yMin = Math.min(yMin, s[2]); yMax = Math.max(yMax, s[1]); }
  const yH = (yMax - yMin) || 1;
  const ring = (s: [number, number, number], stationIndex: number): [number, number, number][] => {
    const [x, topY, botY] = s;
    const rawHeight = topY - botY;
    if (rawHeight <= 1e-6) return Array.from({ length: 7 }, () => [x, (topY + botY) * 0.5, 0] as [number, number, number]);
    const h = rawHeight;
    const T = (spec.thicknesses?.[stationIndex] ?? baseThickness) / 2;
    const grindY = botY + grindFrac * h;
    const swedgeY = topY - 0.42 * h;
    const sz = ((xT - x) / len < swedgeFrac) ? 0 : T;  // swedge → sharp false edge near tip
    return [
      [x, botY, 0], [x, grindY, T], [x, swedgeY, T], [x, topY, sz],
      [x, topY, -sz], [x, swedgeY, -T], [x, grindY, -T],
    ];
  };
  const rings = st.map(ring);
  const pos: number[] = [];
  const uv: number[] = [];
  const color: number[] = [];
  const idx: number[] = [];
  const edgeTone = THREE.MathUtils.clamp(spec.edgeTone ?? 0.55, 0.1, 1);
  const sectionTones = [edgeTone, edgeTone, edgeTone, 1, 1, edgeTone, edgeTone];
  const vertex = (p: number[], tone = 1) => {
    const n = pos.length / 3;
    const v = (p[1] - yMin) / yH;
    pos.push(p[0], p[1], p[2]);
    uv.push((p[0] - xG) / len, v);
    color.push(tone, tone, tone);
    return n;
  };
  // Duplicate the seven longitudinal strips at their boundaries. This preserves
  // the cutting edge / grind / spine creases while smoothing within each strip.
  for (let k = 0; k < 7; k++) {
    const k2 = (k + 1) % 7;
    const strip: [number, number][] = rings.map((r) => [vertex(r[k], sectionTones[k]), vertex(r[k2], sectionTones[k2])]);
    for (let i = 0; i < strip.length - 1; i++) {
      const [a, b] = strip[i];
      const [d, c] = strip[i + 1];
      idx.push(a, c, b, a, d, c);
    }
  }
  const cap = (r: [number, number, number][], reverse: boolean) => {
    const c = vertex([r.reduce((sum, p) => sum + p[0], 0) / 7, r.reduce((sum, p) => sum + p[1], 0) / 7, r.reduce((sum, p) => sum + p[2], 0) / 7]);
    for (let k = 0; k < 7; k++) {
      const a = vertex(r[k], sectionTones[k]);
      const b = vertex(r[(k + 1) % 7], sectionTones[(k + 1) % 7]);
      if (reverse) idx.push(c, a, b); else idx.push(c, b, a);
    }
  };
  cap(rings[0], true);
  cap(rings[rings.length - 1], false);
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.setAttribute('color', new THREE.Float32BufferAttribute(color, 3));
  g.userData.solidVolume = { kind: 'closed-surface-volume', watertightIntent: true, cappedEnds: true };
  return g;
}

function buildTubeGeometry(
  path: { points: [number, number, number][]; radius?: number; radialSegments?: number; closed?: boolean },
): THREE.TubeGeometry {
  const vectors = path.points.map(([x, y, z]) => new THREE.Vector3(x, y, z));
  const curve = new THREE.CatmullRomCurve3(vectors, path.closed ?? false);
  const tubularSegments = Math.max(8, path.points.length * 6);
  return new THREE.TubeGeometry(curve, tubularSegments, path.radius ?? 0.05, path.radialSegments ?? 8, path.closed ?? false);
}

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
  const dirtAmount = clamp01(readLayerNumber(spec.dirt, ['amount'], 0));
  const dirtCavityBias = clamp01(readLayerNumber(spec.dirt, ['cavityBias'], 0));
  const dirtColor = hexToRgb(typeof spec.dirt?.color === 'string' ? spec.dirt.color : '#3A3028');
  const edgeWear = clamp01(readLayerNumber(spec.wear, ['edgeWear', 'amount'], 0));
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
      const cavityMask = clamp01(cavity * 12 + (1 - center) * 0.35);
      const dirtMix = dirtAmount * THREE.MathUtils.lerp(0.2, 1, cavityMask * dirtCavityBias);
      const wearMix = edgeWear * clamp01((center - 0.58) * 2.4);
      for (let channel = 0; channel < 3; channel += 1) {
        const base = images.albedo.data[offset + channel];
        const dirtied = THREE.MathUtils.lerp(base, dirtColor[channel], dirtMix);
        images.albedo.data[offset + channel] = THREE.MathUtils.lerp(dirtied, Math.min(240, dirtied + 18), wearMix);
      }
      const heightByte = center * 255;
      const roughnessByte = clamp01(roughnessField[index] + dirtMix * 0.12 - wearMix * 0.18) * 255;
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
    vertexColors: spec.vertexColors === true,
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
  if (spec.vertexToneFinal === true) {
    material.onBeforeCompile = (shader) => {
      shader.fragmentShader = shader.fragmentShader.replace(
        '#include <tonemapping_fragment>',
        '#ifdef USE_COLOR\n  gl_FragColor.rgb *= vColor;\n#endif\n#include <tonemapping_fragment>',
      );
    };
    material.customProgramCacheKey = () => `${id}:vertex-tone-final`;
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

// Generated from ObjectSculptSpec target: Han Huan-Shou Dao
// Sculpt build pass: optimization-pass
// This factory is intentionally pass-gated. Finish browser screenshot review before unlocking deeper passes.
export function createHanHuanShouDaoModel(options: ProceduralModelOptions = {}): THREE.Group {
  const root = new THREE.Group();
  root.name = "Han Huan-Shou Dao";
  root.userData.reconstructionEvidence = {"itemFamily": null, "subtype": null, "componentAdapter": null, "route": null, "exactnessTier": null, "referenceCamera": {"solved": false, "fovDegrees": 40.0, "aspect": 1.0, "orientation": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}, "positionHint": [0.0, 0.0, 3.0], "note": "For likeness work, solve the reference camera (forge/stage1_intake/solve_camera_pose.py) so the review render aligns with the photo and the reference can be projected. Confirm by overlay review."}, "approximationNotes": []};
  root.userData.materialPipeline = {};
  root.userData.materialReferenceRegistry = null;

  const materialMap: Record<string, THREE.Material> = {};
  materialMap["polished-steel"] = createSculptMaterial(
    "polished-steel",
    {"id": "polished-steel", "name": "Polished blade steel", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#AEB4BA", "color": "#AEB4BA", "albedo": {"dominant": "#AEB4BA", "secondary": ["#747B82", "#C8CDD1"], "samplingNotes": "Cool grey steel from the three-view plate, not the rusted photo."}, "colorVariation": {"palette": ["#747B82", "#AEB4BA", "#C8CDD1"], "pattern": "longitudinal-grind", "amplitude": 0.12, "heightCorrelation": 0.15}, "textureResolution": 1024, "textureProjection": {"mode": "triplanar", "repeat": [4.0, 1.0], "anisotropy": 8, "texelDensityIntent": "Keep grind lines running heel-to-tip."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 1.2, "amplitude": 0.12, "role": "broad value along the hamon zone"}, {"id": "meso", "frequency": 8.0, "amplitude": 0.08, "role": "longitudinal grind"}, {"id": "micro", "frequency": 36.0, "amplitude": 0.04, "role": "fine polish grit"}], "roughness": {"base": 0.44, "variation": 0.08, "map": "independent-procedural-field", "localResponse": "slightly higher below the hamon, lower on the polished shinogi"}, "metalness": {"base": 0.84, "variation": 0.05}, "envMapIntensity": 0.68, "vertexColors": true, "vertexToneFinal": true, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.14, "scale": 22.0, "space": "tangent"}, "bump": {"pattern": "longitudinal-grind", "amplitude": 0.002, "scale": 20.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.18, "contactShadowBias": 0.2, "notes": "Keep the blade bright; only darken the guard and wrap contacts."}, "wear": {"edgeWear": 0.16, "scratches": ["faint heel-to-tip grind"], "chips": []}, "dirt": {"amount": 0.04, "cavityBias": 0.2, "color": "#6A6864"}, "patina": {"amount": 0.0, "color": "#AEB4BA", "notes": "Illustrated as clean steel."}, "localOverrides": [{"id": "hamon-band", "region": "wavy band along the edge, distal two-thirds", "albedo": "#D8DCE0", "roughness": 0.34, "notes": "Hamon is drawn, not a geometry bevel. Blockout keeps it as a value/roughness stain.", "evidenceRefs": ["blade-face"]}, {"id": "edge-brightening", "region": "cutting edge", "albedo": "#E8EBEE", "roughness": 0.2, "evidenceRefs": ["blade-face"]}], "shaderNotes": ["Polished steel: high metalness, mid-low roughness. Not rust, not chrome mirror.", "Do not reuse albedo as roughness."], "notes": "Reconstruct the three-view illustration, not the excavated relic photo."},
    options
  );
  materialMap["gilt-bronze"] = createSculptMaterial(
    "gilt-bronze",
    {"id": "gilt-bronze", "name": "Gilt bronze fittings", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#C4A46A", "color": "#C4A46A", "albedo": {"dominant": "#C4A46A", "secondary": ["#8A7040", "#E0C890"], "samplingNotes": "Guard, collars, and ring share this yellow-metal family."}, "colorVariation": {"palette": ["#C4A46A", "#8A7040", "#E0C890"], "pattern": "soft-cast-mottle", "amplitude": 0.14, "heightCorrelation": 0.2}, "textureResolution": 1024, "textureProjection": {"mode": "triplanar", "repeat": [1.0, 1.0], "anisotropy": 4, "texelDensityIntent": "Cast-metal scale on small fittings; do not stretch around the ring."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 2.0, "amplitude": 0.14, "role": "cast value shift"}, {"id": "meso", "frequency": 10.0, "amplitude": 0.08, "role": "engraving suggestion"}, {"id": "micro", "frequency": 32.0, "amplitude": 0.04, "role": "fine grit"}], "roughness": {"base": 0.3, "variation": 0.08, "map": "independent-procedural-field", "localResponse": "duller in engraved recesses on the ring"}, "metalness": {"base": 0.86, "variation": 0.06}, "envMapIntensity": 1.05, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.18, "scale": 10.0, "space": "tangent"}, "bump": {"pattern": "cast-engraving", "amplitude": 0.005, "scale": 10.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.28, "contactShadowBias": 0.22, "notes": "Darken the ring inner diameter and guard/handle seams."}, "wear": {"edgeWear": 0.18, "scratches": [], "chips": []}, "dirt": {"amount": 0.08, "cavityBias": 0.3, "color": "#5A4030"}, "localOverrides": [{"id": "ring-recess", "region": "inner and engraved face of the ring", "albedo": "#8A7040", "roughness": 0.48, "evidenceRefs": ["pommel-ring"]}], "shaderNotes": ["Yellow metal fittings, not painted plastic.", "Keep hue from shifting blue under ACES."], "notes": "Illustration gilt; alloy is not specified."},
    options
  );
  materialMap["cord-wrap"] = createSculptMaterial(
    "cord-wrap",
    {"id": "cord-wrap", "name": "Dark cord-wrapped grip", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#3A2418", "color": "#3A2418", "albedo": {"dominant": "#3A2418", "secondary": ["#241610", "#5A3A28"], "samplingNotes": "Dark brown wrap with diamond gilt studs sitting on top."}, "colorVariation": {"palette": ["#241610", "#3A2418", "#5A3A28"], "pattern": "helical-wrap", "amplitude": 0.16, "heightCorrelation": 0.35}, "textureResolution": 1024, "textureProjection": {"mode": "cylindrical", "repeat": [1.0, 8.0], "anisotropy": 4, "texelDensityIntent": "Wrap turns run around the grip, not along the blade."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 3.0, "amplitude": 0.22, "role": "wrap-turn ridges"}, {"id": "meso", "frequency": 12.0, "amplitude": 0.12, "role": "cord twist"}, {"id": "micro", "frequency": 40.0, "amplitude": 0.05, "role": "fiber grit"}], "roughness": {"base": 0.78, "variation": 0.1, "map": "independent-procedural-field", "localResponse": "higher in wrap valleys"}, "metalness": {"base": 0.02, "variation": 0.02}, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.35, "scale": 14.0, "space": "tangent"}, "bump": {"pattern": "cord-wrap", "amplitude": 0.012, "scale": 12.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.4, "contactShadowBias": 0.25, "notes": "Darken wrap valleys and the stud sockets."}, "wear": {"edgeWear": 0.08, "scratches": [], "chips": []}, "dirt": {"amount": 0.12, "cavityBias": 0.4, "color": "#1A100C"}, "localOverrides": [{"id": "wrap-valley", "region": "helical recesses between cord turns", "albedo": "#241610", "roughness": 0.86, "evidenceRefs": ["handle"]}], "shaderNotes": ["Dielectric wrap, not metal.", "Do not reuse albedo as roughness."], "notes": "Dark cord or leather wrap as drawn; blockout is a cylinder plus six inlays."},
    options
  );
  materialMap["wrap-seam"] = createSculptMaterial(
    "wrap-seam",
    {"id": "wrap-seam", "name": "Recessed cord-wrap seam", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#21140E", "color": "#21140E", "albedo": {"dominant": "#21140E", "secondary": ["#160D09", "#322018"], "samplingNotes": "Dark crossing valleys between the illustrated wrap turns."}, "colorVariation": {"palette": ["#241610", "#3A2418", "#5A3A28"], "pattern": "helical-wrap", "amplitude": 0.16, "heightCorrelation": 0.35}, "textureResolution": 1024, "textureProjection": {"mode": "cylindrical", "repeat": [1.0, 8.0], "anisotropy": 4, "texelDensityIntent": "Wrap turns run around the grip, not along the blade."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 3.0, "amplitude": 0.22, "role": "wrap-turn ridges"}, {"id": "meso", "frequency": 12.0, "amplitude": 0.12, "role": "cord twist"}, {"id": "micro", "frequency": 40.0, "amplitude": 0.05, "role": "fiber grit"}], "roughness": {"base": 0.78, "variation": 0.1, "map": "independent-procedural-field", "localResponse": "higher in wrap valleys"}, "metalness": {"base": 0.02, "variation": 0.02}, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.35, "scale": 14.0, "space": "tangent"}, "bump": {"pattern": "cord-wrap", "amplitude": 0.012, "scale": 12.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.4, "contactShadowBias": 0.25, "notes": "Darken wrap valleys and the stud sockets."}, "wear": {"edgeWear": 0.08, "scratches": [], "chips": []}, "dirt": {"amount": 0.12, "cavityBias": 0.4, "color": "#1A100C"}, "localOverrides": [{"id": "wrap-valley", "region": "helical recesses between cord turns", "albedo": "#241610", "roughness": 0.86, "evidenceRefs": ["handle"]}], "shaderNotes": ["Dielectric wrap, not metal.", "Do not reuse albedo as roughness."], "notes": "Thin procedural seam geometry set into the grip surface."},
    options
  );
  materialMap["hamon-steel"] = createSculptMaterial(
    "hamon-steel",
    {"id": "hamon-steel", "name": "Polished hamon line", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#B8BFC6", "color": "#B8BFC6", "albedo": {"dominant": "#B8BFC6", "secondary": ["#9AA2A9", "#C9CED3"], "samplingNotes": "Primary etched hamon: cooler than chrome, not a white highlight rail."}, "colorVariation": {"palette": ["#9AA2A9", "#B8BFC6", "#C9CED3"], "pattern": "fine-etched-line", "amplitude": 0.05, "heightCorrelation": 0.0}, "textureResolution": 1024, "textureProjection": {"mode": "triplanar", "repeat": [4.0, 1.0], "anisotropy": 8, "texelDensityIntent": "Keep grind lines running heel-to-tip."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 1.2, "amplitude": 0.12, "role": "broad value along the hamon zone"}, {"id": "meso", "frequency": 8.0, "amplitude": 0.08, "role": "longitudinal grind"}, {"id": "micro", "frequency": 36.0, "amplitude": 0.04, "role": "fine polish grit"}], "roughness": {"base": 0.48, "variation": 0.06, "map": "independent-procedural-field"}, "metalness": {"base": 0.7, "variation": 0.05}, "envMapIntensity": 0.22, "vertexColors": false, "vertexToneFinal": false, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.14, "scale": 22.0, "space": "tangent"}, "bump": {"pattern": "longitudinal-grind", "amplitude": 0.002, "scale": 20.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.18, "contactShadowBias": 0.2, "notes": "Keep the blade bright; only darken the guard and wrap contacts."}, "wear": {"edgeWear": 0.16, "scratches": ["faint heel-to-tip grind"], "chips": []}, "dirt": {"amount": 0.04, "cavityBias": 0.2, "color": "#6A6864"}, "patina": {"amount": 0.0, "color": "#AEB4BA", "notes": "Illustrated as clean steel."}, "localOverrides": [{"id": "hamon-band", "region": "wavy band along the edge, distal two-thirds", "albedo": "#D8DCE0", "roughness": 0.34, "notes": "Hamon is drawn, not a geometry bevel. Blockout keeps it as a value/roughness stain.", "evidenceRefs": ["blade-face"]}, {"id": "edge-brightening", "region": "cutting edge", "albedo": "#E8EBEE", "roughness": 0.2, "evidenceRefs": ["blade-face"]}], "shaderNotes": ["Polished steel: high metalness, mid-low roughness. Not rust, not chrome mirror.", "Do not reuse albedo as roughness."], "notes": "Reconstruct the three-view illustration, not the excavated relic photo."},
    options
  );
  materialMap["hamon-steel-secondary"] = createSculptMaterial(
    "hamon-steel-secondary",
    {"id": "hamon-steel-secondary", "name": "Quiet hamon companion line", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#8E959C", "color": "#8E959C", "albedo": {"dominant": "#8E959C", "secondary": ["#7A8188", "#A4ABB1"], "samplingNotes": "Thinner, darker companion etch; must not compete with the primary hamon."}, "colorVariation": {"palette": ["#7A8188", "#8E959C", "#A4ABB1"], "pattern": "fine-etched-line", "amplitude": 0.04, "heightCorrelation": 0.0}, "textureResolution": 1024, "textureProjection": {"mode": "triplanar", "repeat": [4.0, 1.0], "anisotropy": 8, "texelDensityIntent": "Keep grind lines running heel-to-tip."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 1.2, "amplitude": 0.12, "role": "broad value along the hamon zone"}, {"id": "meso", "frequency": 8.0, "amplitude": 0.08, "role": "longitudinal grind"}, {"id": "micro", "frequency": 36.0, "amplitude": 0.04, "role": "fine polish grit"}], "roughness": {"base": 0.58, "variation": 0.05, "map": "independent-procedural-field"}, "metalness": {"base": 0.62, "variation": 0.04}, "envMapIntensity": 0.12, "vertexColors": false, "vertexToneFinal": false, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.14, "scale": 22.0, "space": "tangent"}, "bump": {"pattern": "longitudinal-grind", "amplitude": 0.002, "scale": 20.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.18, "contactShadowBias": 0.2, "notes": "Keep the blade bright; only darken the guard and wrap contacts."}, "wear": {"edgeWear": 0.16, "scratches": ["faint heel-to-tip grind"], "chips": []}, "dirt": {"amount": 0.04, "cavityBias": 0.2, "color": "#6A6864"}, "patina": {"amount": 0.0, "color": "#AEB4BA", "notes": "Illustrated as clean steel."}, "localOverrides": [{"id": "hamon-band", "region": "wavy band along the edge, distal two-thirds", "albedo": "#D8DCE0", "roughness": 0.34, "notes": "Hamon is drawn, not a geometry bevel. Blockout keeps it as a value/roughness stain.", "evidenceRefs": ["blade-face"]}, {"id": "edge-brightening", "region": "cutting edge", "albedo": "#E8EBEE", "roughness": 0.2, "evidenceRefs": ["blade-face"]}], "shaderNotes": ["Polished steel: high metalness, mid-low roughness. Not rust, not chrome mirror.", "Do not reuse albedo as roughness."], "notes": "Reconstruct the three-view illustration, not the excavated relic photo."},
    options
  );
  materialMap["gilt-engraving"] = createSculptMaterial(
    "gilt-engraving",
    {"id": "gilt-engraving", "name": "Dark gilt engraving recess", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#6F5427", "color": "#6F5427", "albedo": {"dominant": "#6F5427", "secondary": ["#4F391A", "#8A7040"], "samplingNotes": "Dark recess tone sampled conceptually from the ring engraving."}, "colorVariation": {"palette": ["#4F391A", "#6F5427", "#8A7040"], "pattern": "engraved-recess", "amplitude": 0.06, "heightCorrelation": 0.0}, "textureResolution": 1024, "textureProjection": {"mode": "triplanar", "repeat": [1.0, 1.0], "anisotropy": 4, "texelDensityIntent": "Cast-metal scale on small fittings; do not stretch around the ring."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 2.0, "amplitude": 0.14, "role": "cast value shift"}, {"id": "meso", "frequency": 10.0, "amplitude": 0.08, "role": "engraving suggestion"}, {"id": "micro", "frequency": 32.0, "amplitude": 0.04, "role": "fine grit"}], "roughness": {"base": 0.55, "variation": 0.08, "map": "independent-procedural-field"}, "metalness": {"base": 0.86, "variation": 0.06}, "envMapIntensity": 0.25, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.18, "scale": 10.0, "space": "tangent"}, "bump": {"pattern": "cast-engraving", "amplitude": 0.005, "scale": 10.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.28, "contactShadowBias": 0.22, "notes": "Darken the ring inner diameter and guard/handle seams."}, "wear": {"edgeWear": 0.18, "scratches": [], "chips": []}, "dirt": {"amount": 0.08, "cavityBias": 0.3, "color": "#5A4030"}, "localOverrides": [{"id": "ring-recess", "region": "inner and engraved face of the ring", "albedo": "#8A7040", "roughness": 0.48, "evidenceRefs": ["pommel-ring"]}], "shaderNotes": ["Yellow metal fittings, not painted plastic.", "Keep hue from shifting blue under ACES."], "notes": "Illustration gilt; alloy is not specified."},
    options
  );

  const nodes: Record<string, THREE.Object3D> = { root };
  const meshes: Record<string, THREE.Mesh> = {};
  const sockets: Record<string, THREE.Object3D> = {};
  const colliders: Record<string, unknown> = {};
  const destructionGroups: Record<string, THREE.Object3D[]> = {};

  const endpoint_root_0 = makeAttachmentEndpoint(null);
  const node_root_0 = new THREE.Group();
  node_root_0.name = "Han Huan-Shou Dao assembly__pivot";
  node_root_0.scale.set(1, 1, 1);
  if (endpoint_root_0) {
    node_root_0.position.copy(endpoint_root_0.start);
    node_root_0.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_root_0.position.set(0.0, 0.0, 0.0);
    node_root_0.rotation.set(0.0, 0.0, 0.0);
  }
  node_root_0.userData.sculptComponent = {"id": "root", "name": "Han Huan-Shou Dao assembly", "level": "macro", "role": "assembly", "importance": 1.0, "confidence": 0.9, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Assembly pivot only; keep the cube below visibility so it cannot sit on the blade.", "geometryDescriptor": {"topologyIntent": "Han Huan-Shou Dao assembly reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": null, "attachment": null, "dimensions": {"width": 0.001, "height": 0.001, "depth": 0.001, "units": "relative", "confidence": 0.9}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [0.001, 0.001, 0.001]}, "actionProfile": {"animationRole": "root", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [{"id": "blade-heel", "localPosition": [1.814504716981132, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}, {"id": "guard-back", "localPosition": [1.8105389150943394, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}, {"id": "front-ferrule-back", "localPosition": [1.8195247641509433, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}, {"id": "handle-back", "localPosition": [2.1050931603773586, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}, {"id": "rear-ferrule-back", "localPosition": [2.1131544811320757, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}, {"id": "pommel-anchor", "localPosition": [2.1996544811320757, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}], "collider": {"type": "box", "offset": [1.15, 0, 0], "scale": [2.4, 0.2, 0.08], "isTrigger": false, "notes": "whole-weapon proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "root", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "polished-steel"}}, "material": "polished-steel", "materialLayers": ["polished-steel"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 200, 204, 1.0)", "secondaryAlbedo": "rgba(154, 160, 166, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.8}};
  node_root_0.userData.actionProfile = {"animationRole": "root", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [{"id": "blade-heel", "localPosition": [1.814504716981132, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}, {"id": "guard-back", "localPosition": [1.8105389150943394, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}, {"id": "front-ferrule-back", "localPosition": [1.8195247641509433, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}, {"id": "handle-back", "localPosition": [2.1050931603773586, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}, {"id": "rear-ferrule-back", "localPosition": [2.1131544811320757, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}, {"id": "pommel-anchor", "localPosition": [2.1996544811320757, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}], "collider": {"type": "box", "offset": [1.15, 0, 0], "scale": [2.4, 0.2, 0.08], "isTrigger": false, "notes": "whole-weapon proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "root", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "polished-steel"}};
  node_root_0.userData.explodeWithParent = null;
  (nodes["root"] ?? root).add(node_root_0);
  nodes["root"] = node_root_0;
  const mesh_root_0Geometry = endpoint_root_0
    ? new THREE.CylinderGeometry(endpoint_root_0.endRadius, endpoint_root_0.baseRadius, endpoint_root_0.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_root_0) {
    mesh_root_0Geometry.scale(0.001, 0.001, 0.001);
  }
  const mesh_root_0 = new THREE.Mesh(
    mesh_root_0Geometry,
    materialMap["polished-steel"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_root_0.name = "Han Huan-Shou Dao assembly";
  if (endpoint_root_0) {
    mesh_root_0.position.copy(endpoint_root_0.midpoint);
    mesh_root_0.quaternion.copy(endpoint_root_0.quaternion);
  }
  mesh_root_0.castShadow = options.castShadow ?? true;
  mesh_root_0.receiveShadow = options.receiveShadow ?? true;
  mesh_root_0.userData.sculptComponent = {"id": "root", "name": "Han Huan-Shou Dao assembly", "level": "macro", "role": "assembly", "importance": 1.0, "confidence": 0.9, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Assembly pivot only; keep the cube below visibility so it cannot sit on the blade.", "geometryDescriptor": {"topologyIntent": "Han Huan-Shou Dao assembly reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": null, "attachment": null, "dimensions": {"width": 0.001, "height": 0.001, "depth": 0.001, "units": "relative", "confidence": 0.9}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [0.001, 0.001, 0.001]}, "actionProfile": {"animationRole": "root", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [{"id": "blade-heel", "localPosition": [1.814504716981132, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}, {"id": "guard-back", "localPosition": [1.8105389150943394, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}, {"id": "front-ferrule-back", "localPosition": [1.8195247641509433, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}, {"id": "handle-back", "localPosition": [2.1050931603773586, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}, {"id": "rear-ferrule-back", "localPosition": [2.1131544811320757, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}, {"id": "pommel-anchor", "localPosition": [2.1996544811320757, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]}], "collider": {"type": "box", "offset": [1.15, 0, 0], "scale": [2.4, 0.2, 0.08], "isTrigger": false, "notes": "whole-weapon proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "root", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "polished-steel"}}, "material": "polished-steel", "materialLayers": ["polished-steel"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 200, 204, 1.0)", "secondaryAlbedo": "rgba(154, 160, 166, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.8}};
  mesh_root_0.userData.explodeWithParent = null;
  node_root_0.add(mesh_root_0);
  meshes["root"] = mesh_root_0;
  colliders["root"] = {"type": "box", "offset": [1.15, 0, 0], "scale": [2.4, 0.2, 0.08], "isTrigger": false, "notes": "whole-weapon proxy"};
  destructionGroups["root"] ??= [];
  destructionGroups["root"].push(node_root_0);
  const socket_root_blade_heel_0 = new THREE.Object3D();
  socket_root_blade_heel_0.name = "blade-heel";
  socket_root_blade_heel_0.position.set(1.814504716981132, 0.0, 0.0);
  socket_root_blade_heel_0.rotation.set(0.0, 0.0, 0.0);
  socket_root_blade_heel_0.userData.socket = {"id": "blade-heel", "localPosition": [1.814504716981132, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]};
  node_root_0.add(socket_root_blade_heel_0);
  sockets["root:blade-heel"] = socket_root_blade_heel_0;
  const socket_root_guard_back_1 = new THREE.Object3D();
  socket_root_guard_back_1.name = "guard-back";
  socket_root_guard_back_1.position.set(1.8105389150943394, 0.0, 0.0);
  socket_root_guard_back_1.rotation.set(0.0, 0.0, 0.0);
  socket_root_guard_back_1.userData.socket = {"id": "guard-back", "localPosition": [1.8105389150943394, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]};
  node_root_0.add(socket_root_guard_back_1);
  sockets["root:guard-back"] = socket_root_guard_back_1;
  const socket_root_front_ferrule_back_2 = new THREE.Object3D();
  socket_root_front_ferrule_back_2.name = "front-ferrule-back";
  socket_root_front_ferrule_back_2.position.set(1.8195247641509433, 0.0, 0.0);
  socket_root_front_ferrule_back_2.rotation.set(0.0, 0.0, 0.0);
  socket_root_front_ferrule_back_2.userData.socket = {"id": "front-ferrule-back", "localPosition": [1.8195247641509433, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]};
  node_root_0.add(socket_root_front_ferrule_back_2);
  sockets["root:front-ferrule-back"] = socket_root_front_ferrule_back_2;
  const socket_root_handle_back_3 = new THREE.Object3D();
  socket_root_handle_back_3.name = "handle-back";
  socket_root_handle_back_3.position.set(2.1050931603773586, 0.0, 0.0);
  socket_root_handle_back_3.rotation.set(0.0, 0.0, 0.0);
  socket_root_handle_back_3.userData.socket = {"id": "handle-back", "localPosition": [2.1050931603773586, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]};
  node_root_0.add(socket_root_handle_back_3);
  sockets["root:handle-back"] = socket_root_handle_back_3;
  const socket_root_rear_ferrule_back_4 = new THREE.Object3D();
  socket_root_rear_ferrule_back_4.name = "rear-ferrule-back";
  socket_root_rear_ferrule_back_4.position.set(2.1131544811320757, 0.0, 0.0);
  socket_root_rear_ferrule_back_4.rotation.set(0.0, 0.0, 0.0);
  socket_root_rear_ferrule_back_4.userData.socket = {"id": "rear-ferrule-back", "localPosition": [2.1131544811320757, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]};
  node_root_0.add(socket_root_rear_ferrule_back_4);
  sockets["root:rear-ferrule-back"] = socket_root_rear_ferrule_back_4;
  const socket_root_pommel_anchor_5 = new THREE.Object3D();
  socket_root_pommel_anchor_5.name = "pommel-anchor";
  socket_root_pommel_anchor_5.position.set(2.1996544811320757, 0.0, 0.0);
  socket_root_pommel_anchor_5.rotation.set(0.0, 0.0, 0.0);
  socket_root_pommel_anchor_5.userData.socket = {"id": "pommel-anchor", "localPosition": [2.1996544811320757, 0.0, 0.0], "localRotation": [0.0, 0.0, 0.0]};
  node_root_0.add(socket_root_pommel_anchor_5);
  sockets["root:pommel-anchor"] = socket_root_pommel_anchor_5;

  const endpoint_blade_1 = makeAttachmentEndpoint(null);
  const node_blade_1 = new THREE.Group();
  node_blade_1.name = "Dao blade__pivot";
  node_blade_1.scale.set(1, 1, 1);
  if (endpoint_blade_1) {
    node_blade_1.position.copy(endpoint_blade_1.start);
    node_blade_1.rotation.set(0.0, 3.141592653589793, 0.0);
  } else {
    node_blade_1.position.set(1.814504716981132, 0.0, 0.0);
    node_blade_1.rotation.set(0.0, 3.141592653589793, 0.0);
  }
  node_blade_1.userData.sculptComponent = {"id": "blade", "name": "Dao blade", "level": "macro", "role": "blade", "importance": 1.0, "confidence": 0.88, "primitive": "ground-blade", "topologyClass": "assembled-solid", "topologyRationale": "Single-edged bar lofted from the face-view stations; tip climbs to the spine. Not a rusted card and not a jian diamond.", "geometryDescriptor": {"topologyIntent": "Dao blade reconstruction", "edgeTreatment": {"type": "chamfer", "bevelRadius": 0.003, "segments": 2}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "bladeSpec": {"stations": [[0.0, 0.051533018867924524, -0.04882075471698113], [0.1952830188679245, 0.05017688679245283, -0.051533018867924524], [0.39870283018867925, 0.04882075471698113, -0.05424528301886792], [0.5885613207547169, 0.04882075471698113, -0.05560141509433962], [0.7784198113207547, 0.047464622641509434, -0.05560141509433962], [0.9411556603773584, 0.047464622641509434, -0.05424528301886792], [1.0767688679245282, 0.04610849056603773, -0.052889150943396225], [1.1852594339622642, 0.04610849056603773, -0.051533018867924524], [1.266627358490566, 0.04475235849056604, -0.05017688679245283], [1.347995283018868, 0.04475235849056604, -0.047464622641509434], [1.4022405660377357, 0.04339622641509434, -0.04610849056603773], [1.4564858490566037, 0.04339622641509434, -0.04339622641509434], [1.5107311320754715, 0.042040094339622636, -0.04068396226415094], [1.5514150943396225, 0.04068396226415094, -0.036615566037735844], [1.5920990566037734, 0.04068396226415094, -0.032547169811320754], [1.6327830188679244, 0.03932783018867924, -0.02712264150943396], [1.6734669811320753, 0.037971698113207546, -0.018985849056603773], [1.7005896226415094, 0.037971698113207546, -0.012205188679245282], [1.7277122641509433, 0.036615566037735844, -0.002712264150943396], [1.7548349056603771, 0.036615566037735844, 0.00678066037735849], [1.7711084905660377, 0.03525943396226415, 0.01356132075471698], [1.787382075471698, 0.03525943396226415, 0.02169811320754717], [1.800943396226415, 0.03525943396226415, 0.028478773584905658], [1.814504716981132, 0.03525943396226415, 0.03525943396226415]], "thickness": 0.0502, "thicknesses": [0.0502, 0.0488, 0.0475, 0.0461, 0.0448, 0.0434, 0.042, 0.0393, 0.0366, 0.0339, 0.0312, 0.0285, 0.0258, 0.0231, 0.0203, 0.0176, 0.0149, 0.0122, 0.0095, 0.0075, 0.0054, 0.0034, 0.0014, 0.0], "grindFrac": 0.42, "swedgeFromTipFrac": 0.0, "edgeTone": 0.58}}, "parent": "root", "attachment": null, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.88}, "transform": {"position": [1.814504716981132, 0.0, 0.0], "rotation": [0.0, 3.141592653589793, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0.907252358490566, 0, 0], "scale": [1.814504716981132, 0.11, 0.05], "isTrigger": false, "notes": "blade proxy in local heel-to-tip X"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "polished-steel"}}, "material": "polished-steel", "materialLayers": ["polished-steel"], "deformations": [], "joints": [], "seams": [], "localFeatures": [{"id": "distal-taper", "kind": "bevel", "notes": "Edge rises to meet the spine; last stations collapse to a point."}, {"id": "edge-grind", "kind": "bevel", "notes": "Primary bevel on -Y from the side view thickness."}], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["full-object", "blade-face"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 200, 204, 1.0)", "secondaryAlbedo": "rgba(228, 231, 234, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.84, "colorGradient": {"type": "linear", "stops": [{"offset": 0.0, "color": "rgba(154, 160, 166, 1.0)"}, {"offset": 0.55, "color": "rgba(196, 200, 204, 1.0)"}, {"offset": 1.0, "color": "rgba(228, 231, 234, 1.0)"}]}}};
  node_blade_1.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0.907252358490566, 0, 0], "scale": [1.814504716981132, 0.11, 0.05], "isTrigger": false, "notes": "blade proxy in local heel-to-tip X"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "polished-steel"}};
  node_blade_1.userData.explodeWithParent = null;
  (nodes["root"] ?? root).add(node_blade_1);
  nodes["blade"] = node_blade_1;
  const mesh_blade_1Geometry = endpoint_blade_1
    ? new THREE.CylinderGeometry(endpoint_blade_1.endRadius, endpoint_blade_1.baseRadius, endpoint_blade_1.length, 32, 12)
    : buildGroundBladeGeometry({"stations": [[0.0, 0.051533018867924524, -0.04882075471698113], [0.1952830188679245, 0.05017688679245283, -0.051533018867924524], [0.39870283018867925, 0.04882075471698113, -0.05424528301886792], [0.5885613207547169, 0.04882075471698113, -0.05560141509433962], [0.7784198113207547, 0.047464622641509434, -0.05560141509433962], [0.9411556603773584, 0.047464622641509434, -0.05424528301886792], [1.0767688679245282, 0.04610849056603773, -0.052889150943396225], [1.1852594339622642, 0.04610849056603773, -0.051533018867924524], [1.266627358490566, 0.04475235849056604, -0.05017688679245283], [1.347995283018868, 0.04475235849056604, -0.047464622641509434], [1.4022405660377357, 0.04339622641509434, -0.04610849056603773], [1.4564858490566037, 0.04339622641509434, -0.04339622641509434], [1.5107311320754715, 0.042040094339622636, -0.04068396226415094], [1.5514150943396225, 0.04068396226415094, -0.036615566037735844], [1.5920990566037734, 0.04068396226415094, -0.032547169811320754], [1.6327830188679244, 0.03932783018867924, -0.02712264150943396], [1.6734669811320753, 0.037971698113207546, -0.018985849056603773], [1.7005896226415094, 0.037971698113207546, -0.012205188679245282], [1.7277122641509433, 0.036615566037735844, -0.002712264150943396], [1.7548349056603771, 0.036615566037735844, 0.00678066037735849], [1.7711084905660377, 0.03525943396226415, 0.01356132075471698], [1.787382075471698, 0.03525943396226415, 0.02169811320754717], [1.800943396226415, 0.03525943396226415, 0.028478773584905658], [1.814504716981132, 0.03525943396226415, 0.03525943396226415]], "thickness": 0.0502, "thicknesses": [0.0502, 0.0488, 0.0475, 0.0461, 0.0448, 0.0434, 0.042, 0.0393, 0.0366, 0.0339, 0.0312, 0.0285, 0.0258, 0.0231, 0.0203, 0.0176, 0.0149, 0.0122, 0.0095, 0.0075, 0.0054, 0.0034, 0.0014, 0.0], "grindFrac": 0.42, "swedgeFromTipFrac": 0.0, "edgeTone": 0.58});
  if (!endpoint_blade_1) {
    mesh_blade_1Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_blade_1 = new THREE.Mesh(
    mesh_blade_1Geometry,
    materialMap["polished-steel"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_blade_1.name = "Dao blade";
  if (endpoint_blade_1) {
    mesh_blade_1.position.copy(endpoint_blade_1.midpoint);
    mesh_blade_1.quaternion.copy(endpoint_blade_1.quaternion);
  }
  mesh_blade_1.castShadow = options.castShadow ?? true;
  mesh_blade_1.receiveShadow = options.receiveShadow ?? true;
  mesh_blade_1.userData.sculptComponent = {"id": "blade", "name": "Dao blade", "level": "macro", "role": "blade", "importance": 1.0, "confidence": 0.88, "primitive": "ground-blade", "topologyClass": "assembled-solid", "topologyRationale": "Single-edged bar lofted from the face-view stations; tip climbs to the spine. Not a rusted card and not a jian diamond.", "geometryDescriptor": {"topologyIntent": "Dao blade reconstruction", "edgeTreatment": {"type": "chamfer", "bevelRadius": 0.003, "segments": 2}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "bladeSpec": {"stations": [[0.0, 0.051533018867924524, -0.04882075471698113], [0.1952830188679245, 0.05017688679245283, -0.051533018867924524], [0.39870283018867925, 0.04882075471698113, -0.05424528301886792], [0.5885613207547169, 0.04882075471698113, -0.05560141509433962], [0.7784198113207547, 0.047464622641509434, -0.05560141509433962], [0.9411556603773584, 0.047464622641509434, -0.05424528301886792], [1.0767688679245282, 0.04610849056603773, -0.052889150943396225], [1.1852594339622642, 0.04610849056603773, -0.051533018867924524], [1.266627358490566, 0.04475235849056604, -0.05017688679245283], [1.347995283018868, 0.04475235849056604, -0.047464622641509434], [1.4022405660377357, 0.04339622641509434, -0.04610849056603773], [1.4564858490566037, 0.04339622641509434, -0.04339622641509434], [1.5107311320754715, 0.042040094339622636, -0.04068396226415094], [1.5514150943396225, 0.04068396226415094, -0.036615566037735844], [1.5920990566037734, 0.04068396226415094, -0.032547169811320754], [1.6327830188679244, 0.03932783018867924, -0.02712264150943396], [1.6734669811320753, 0.037971698113207546, -0.018985849056603773], [1.7005896226415094, 0.037971698113207546, -0.012205188679245282], [1.7277122641509433, 0.036615566037735844, -0.002712264150943396], [1.7548349056603771, 0.036615566037735844, 0.00678066037735849], [1.7711084905660377, 0.03525943396226415, 0.01356132075471698], [1.787382075471698, 0.03525943396226415, 0.02169811320754717], [1.800943396226415, 0.03525943396226415, 0.028478773584905658], [1.814504716981132, 0.03525943396226415, 0.03525943396226415]], "thickness": 0.0502, "thicknesses": [0.0502, 0.0488, 0.0475, 0.0461, 0.0448, 0.0434, 0.042, 0.0393, 0.0366, 0.0339, 0.0312, 0.0285, 0.0258, 0.0231, 0.0203, 0.0176, 0.0149, 0.0122, 0.0095, 0.0075, 0.0054, 0.0034, 0.0014, 0.0], "grindFrac": 0.42, "swedgeFromTipFrac": 0.0, "edgeTone": 0.58}}, "parent": "root", "attachment": null, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.88}, "transform": {"position": [1.814504716981132, 0.0, 0.0], "rotation": [0.0, 3.141592653589793, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "box", "offset": [0.907252358490566, 0, 0], "scale": [1.814504716981132, 0.11, 0.05], "isTrigger": false, "notes": "blade proxy in local heel-to-tip X"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "polished-steel"}}, "material": "polished-steel", "materialLayers": ["polished-steel"], "deformations": [], "joints": [], "seams": [], "localFeatures": [{"id": "distal-taper", "kind": "bevel", "notes": "Edge rises to meet the spine; last stations collapse to a point."}, {"id": "edge-grind", "kind": "bevel", "notes": "Primary bevel on -Y from the side view thickness."}], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["full-object", "blade-face"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 200, 204, 1.0)", "secondaryAlbedo": "rgba(228, 231, 234, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.84, "colorGradient": {"type": "linear", "stops": [{"offset": 0.0, "color": "rgba(154, 160, 166, 1.0)"}, {"offset": 0.55, "color": "rgba(196, 200, 204, 1.0)"}, {"offset": 1.0, "color": "rgba(228, 231, 234, 1.0)"}]}}};
  mesh_blade_1.userData.explodeWithParent = null;
  node_blade_1.add(mesh_blade_1);
  meshes["blade"] = mesh_blade_1;
  colliders["blade"] = {"type": "box", "offset": [0.907252358490566, 0, 0], "scale": [1.814504716981132, 0.11, 0.05], "isTrigger": false, "notes": "blade proxy in local heel-to-tip X"};
  destructionGroups["blade"] ??= [];
  destructionGroups["blade"].push(node_blade_1);

  const attachment_hamon_1_2 = {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_hamon_1_2 = makeAttachmentEndpoint(attachment_hamon_1_2);
  const node_hamon_1_2 = new THREE.Group();
  node_hamon_1_2.name = "Hamon front line 1__pivot";
  node_hamon_1_2.scale.set(1, 1, 1);
  if (endpoint_hamon_1_2) {
    node_hamon_1_2.position.copy(endpoint_hamon_1_2.start);
    node_hamon_1_2.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_hamon_1_2.position.set(0.0, 0.0, 0.0);
    node_hamon_1_2.rotation.set(0.0, 0.0, 0.0);
  }
  node_hamon_1_2.userData.sculptComponent = {"id": "hamon-1", "name": "Hamon front line 1", "level": "micro", "role": "detail", "importance": 0.58, "confidence": 0.78, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Primary hamon plus quieter companion etches on the front face; low-frequency wander with tapered ends, not three equal highlight rails.", "geometryDescriptor": {"topologyIntent": "Hamon front line 1 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[0.28, 0.00819, 0.01291], [0.33333, 0.00804, 0.01444], [0.38667, 0.0077, 0.01576], [0.44, 0.00764, 0.01709], [0.49333, 0.00762, 0.0182], [0.54667, 0.00689, 0.01908], [0.6, 0.00682, 0.01996], [0.65333, 0.00644, 0.02075], [0.70667, 0.00591, 0.02141], [0.76, 0.00593, 0.02191], [0.81333, 0.00666, 0.02219], [0.86667, 0.00773, 0.02247], [0.92, 0.00851, 0.0227], [0.97333, 0.00895, 0.02293], [1.02667, 0.00893, 0.02316], [1.08, 0.00886, 0.02335], [1.13333, 0.00867, 0.02353], [1.18667, 0.00845, 0.02372], [1.24, 0.00823, 0.0239], [1.29333, 0.00793, 0.0241], [1.34667, 0.00764, 0.0243], [1.4, 0.00743, 0.02449], [1.45333, 0.00766, 0.02467], [1.50667, 0.00822, 0.02484], [1.56, 0.00895, 0.02501], [1.61333, 0.00972, 0.02518], [1.66667, 0.01041, 0.02537], [1.72, 0.01095, 0.02556]], "radius": 0.00042, "radialSegments": 5, "closed": false}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.78}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel-secondary"}}, "material": "hamon-steel-secondary", "materialLayers": ["hamon-steel-secondary"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["blade-face"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(142, 149, 156, 1.0)", "secondaryAlbedo": "rgba(122, 129, 136, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.6}, "explodeWithParent": "blade", "ownerModule": "blade", "face": "front", "mergePolicy": "bake"};
  node_hamon_1_2.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel-secondary"}};
  node_hamon_1_2.userData.explodeWithParent = "blade";
  (nodes["root"] ?? root).add(node_hamon_1_2);
  nodes["hamon-1"] = node_hamon_1_2;
  const mesh_hamon_1_2Geometry = endpoint_hamon_1_2
    ? new THREE.CylinderGeometry(endpoint_hamon_1_2.endRadius, endpoint_hamon_1_2.baseRadius, endpoint_hamon_1_2.length, 32, 12)
    : buildTubeGeometry({"points": [[0.28, 0.00819, 0.01291], [0.33333, 0.00804, 0.01444], [0.38667, 0.0077, 0.01576], [0.44, 0.00764, 0.01709], [0.49333, 0.00762, 0.0182], [0.54667, 0.00689, 0.01908], [0.6, 0.00682, 0.01996], [0.65333, 0.00644, 0.02075], [0.70667, 0.00591, 0.02141], [0.76, 0.00593, 0.02191], [0.81333, 0.00666, 0.02219], [0.86667, 0.00773, 0.02247], [0.92, 0.00851, 0.0227], [0.97333, 0.00895, 0.02293], [1.02667, 0.00893, 0.02316], [1.08, 0.00886, 0.02335], [1.13333, 0.00867, 0.02353], [1.18667, 0.00845, 0.02372], [1.24, 0.00823, 0.0239], [1.29333, 0.00793, 0.0241], [1.34667, 0.00764, 0.0243], [1.4, 0.00743, 0.02449], [1.45333, 0.00766, 0.02467], [1.50667, 0.00822, 0.02484], [1.56, 0.00895, 0.02501], [1.61333, 0.00972, 0.02518], [1.66667, 0.01041, 0.02537], [1.72, 0.01095, 0.02556]], "radius": 0.00042, "radialSegments": 5, "closed": false});
  if (!endpoint_hamon_1_2) {
    mesh_hamon_1_2Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_hamon_1_2 = new THREE.Mesh(
    mesh_hamon_1_2Geometry,
    materialMap["hamon-steel-secondary"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_hamon_1_2.name = "Hamon front line 1";
  if (endpoint_hamon_1_2) {
    mesh_hamon_1_2.position.copy(endpoint_hamon_1_2.midpoint);
    mesh_hamon_1_2.quaternion.copy(endpoint_hamon_1_2.quaternion);
  }
  mesh_hamon_1_2.castShadow = options.castShadow ?? true;
  mesh_hamon_1_2.receiveShadow = options.receiveShadow ?? true;
  mesh_hamon_1_2.userData.sculptComponent = {"id": "hamon-1", "name": "Hamon front line 1", "level": "micro", "role": "detail", "importance": 0.58, "confidence": 0.78, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Primary hamon plus quieter companion etches on the front face; low-frequency wander with tapered ends, not three equal highlight rails.", "geometryDescriptor": {"topologyIntent": "Hamon front line 1 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[0.28, 0.00819, 0.01291], [0.33333, 0.00804, 0.01444], [0.38667, 0.0077, 0.01576], [0.44, 0.00764, 0.01709], [0.49333, 0.00762, 0.0182], [0.54667, 0.00689, 0.01908], [0.6, 0.00682, 0.01996], [0.65333, 0.00644, 0.02075], [0.70667, 0.00591, 0.02141], [0.76, 0.00593, 0.02191], [0.81333, 0.00666, 0.02219], [0.86667, 0.00773, 0.02247], [0.92, 0.00851, 0.0227], [0.97333, 0.00895, 0.02293], [1.02667, 0.00893, 0.02316], [1.08, 0.00886, 0.02335], [1.13333, 0.00867, 0.02353], [1.18667, 0.00845, 0.02372], [1.24, 0.00823, 0.0239], [1.29333, 0.00793, 0.0241], [1.34667, 0.00764, 0.0243], [1.4, 0.00743, 0.02449], [1.45333, 0.00766, 0.02467], [1.50667, 0.00822, 0.02484], [1.56, 0.00895, 0.02501], [1.61333, 0.00972, 0.02518], [1.66667, 0.01041, 0.02537], [1.72, 0.01095, 0.02556]], "radius": 0.00042, "radialSegments": 5, "closed": false}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.78}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel-secondary"}}, "material": "hamon-steel-secondary", "materialLayers": ["hamon-steel-secondary"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["blade-face"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(142, 149, 156, 1.0)", "secondaryAlbedo": "rgba(122, 129, 136, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.6}, "explodeWithParent": "blade", "ownerModule": "blade", "face": "front", "mergePolicy": "bake"};
  mesh_hamon_1_2.userData.explodeWithParent = "blade";
  node_hamon_1_2.add(mesh_hamon_1_2);
  meshes["hamon-1"] = mesh_hamon_1_2;
  colliders["hamon-1"] = null;
  destructionGroups["blade"] ??= [];
  destructionGroups["blade"].push(node_hamon_1_2);

  const attachment_hamon_2_3 = {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_hamon_2_3 = makeAttachmentEndpoint(attachment_hamon_2_3);
  const node_hamon_2_3 = new THREE.Group();
  node_hamon_2_3.name = "Hamon front line 2__pivot";
  node_hamon_2_3.scale.set(1, 1, 1);
  if (endpoint_hamon_2_3) {
    node_hamon_2_3.position.copy(endpoint_hamon_2_3.start);
    node_hamon_2_3.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_hamon_2_3.position.set(0.0, 0.0, 0.0);
    node_hamon_2_3.rotation.set(0.0, 0.0, 0.0);
  }
  node_hamon_2_3.userData.sculptComponent = {"id": "hamon-2", "name": "Hamon front line 2", "level": "micro", "role": "detail", "importance": 0.78, "confidence": 0.78, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Primary hamon plus quieter companion etches on the front face; low-frequency wander with tapered ends, not three equal highlight rails.", "geometryDescriptor": {"topologyIntent": "Hamon front line 2 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[0.28, 0.01579, 0.01291], [0.33333, 0.01564, 0.01444], [0.38667, 0.01526, 0.01576], [0.44, 0.01511, 0.01709], [0.49333, 0.01495, 0.0182], [0.54667, 0.01408, 0.01908], [0.6, 0.01391, 0.01996], [0.65333, 0.01354, 0.02075], [0.70667, 0.01328, 0.02141], [0.76, 0.01392, 0.02191], [0.81333, 0.01559, 0.02219], [0.86667, 0.01765, 0.02247], [0.92, 0.01911, 0.0227], [0.97333, 0.01957, 0.02293], [1.02667, 0.01881, 0.02316], [1.08, 0.01746, 0.02335], [1.13333, 0.01583, 0.02353], [1.18667, 0.01445, 0.02372], [1.24, 0.01355, 0.0239], [1.29333, 0.01308, 0.0241], [1.34667, 0.01298, 0.0243], [1.4, 0.01318, 0.02449], [1.45333, 0.01389, 0.02467], [1.50667, 0.01495, 0.02484], [1.56, 0.01612, 0.02501], [1.61333, 0.01718, 0.02518], [1.66667, 0.01799, 0.02537], [1.72, 0.01855, 0.02556]], "radius": 0.00092, "radialSegments": 5, "closed": false}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.78}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel"}}, "material": "hamon-steel", "materialLayers": ["hamon-steel"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["blade-face"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(184, 191, 198, 1.0)", "secondaryAlbedo": "rgba(154, 162, 169, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.72}, "explodeWithParent": "blade", "ownerModule": "blade", "face": "front", "mergePolicy": "bake"};
  node_hamon_2_3.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel"}};
  node_hamon_2_3.userData.explodeWithParent = "blade";
  (nodes["root"] ?? root).add(node_hamon_2_3);
  nodes["hamon-2"] = node_hamon_2_3;
  const mesh_hamon_2_3Geometry = endpoint_hamon_2_3
    ? new THREE.CylinderGeometry(endpoint_hamon_2_3.endRadius, endpoint_hamon_2_3.baseRadius, endpoint_hamon_2_3.length, 32, 12)
    : buildTubeGeometry({"points": [[0.28, 0.01579, 0.01291], [0.33333, 0.01564, 0.01444], [0.38667, 0.01526, 0.01576], [0.44, 0.01511, 0.01709], [0.49333, 0.01495, 0.0182], [0.54667, 0.01408, 0.01908], [0.6, 0.01391, 0.01996], [0.65333, 0.01354, 0.02075], [0.70667, 0.01328, 0.02141], [0.76, 0.01392, 0.02191], [0.81333, 0.01559, 0.02219], [0.86667, 0.01765, 0.02247], [0.92, 0.01911, 0.0227], [0.97333, 0.01957, 0.02293], [1.02667, 0.01881, 0.02316], [1.08, 0.01746, 0.02335], [1.13333, 0.01583, 0.02353], [1.18667, 0.01445, 0.02372], [1.24, 0.01355, 0.0239], [1.29333, 0.01308, 0.0241], [1.34667, 0.01298, 0.0243], [1.4, 0.01318, 0.02449], [1.45333, 0.01389, 0.02467], [1.50667, 0.01495, 0.02484], [1.56, 0.01612, 0.02501], [1.61333, 0.01718, 0.02518], [1.66667, 0.01799, 0.02537], [1.72, 0.01855, 0.02556]], "radius": 0.00092, "radialSegments": 5, "closed": false});
  if (!endpoint_hamon_2_3) {
    mesh_hamon_2_3Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_hamon_2_3 = new THREE.Mesh(
    mesh_hamon_2_3Geometry,
    materialMap["hamon-steel"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_hamon_2_3.name = "Hamon front line 2";
  if (endpoint_hamon_2_3) {
    mesh_hamon_2_3.position.copy(endpoint_hamon_2_3.midpoint);
    mesh_hamon_2_3.quaternion.copy(endpoint_hamon_2_3.quaternion);
  }
  mesh_hamon_2_3.castShadow = options.castShadow ?? true;
  mesh_hamon_2_3.receiveShadow = options.receiveShadow ?? true;
  mesh_hamon_2_3.userData.sculptComponent = {"id": "hamon-2", "name": "Hamon front line 2", "level": "micro", "role": "detail", "importance": 0.78, "confidence": 0.78, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Primary hamon plus quieter companion etches on the front face; low-frequency wander with tapered ends, not three equal highlight rails.", "geometryDescriptor": {"topologyIntent": "Hamon front line 2 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[0.28, 0.01579, 0.01291], [0.33333, 0.01564, 0.01444], [0.38667, 0.01526, 0.01576], [0.44, 0.01511, 0.01709], [0.49333, 0.01495, 0.0182], [0.54667, 0.01408, 0.01908], [0.6, 0.01391, 0.01996], [0.65333, 0.01354, 0.02075], [0.70667, 0.01328, 0.02141], [0.76, 0.01392, 0.02191], [0.81333, 0.01559, 0.02219], [0.86667, 0.01765, 0.02247], [0.92, 0.01911, 0.0227], [0.97333, 0.01957, 0.02293], [1.02667, 0.01881, 0.02316], [1.08, 0.01746, 0.02335], [1.13333, 0.01583, 0.02353], [1.18667, 0.01445, 0.02372], [1.24, 0.01355, 0.0239], [1.29333, 0.01308, 0.0241], [1.34667, 0.01298, 0.0243], [1.4, 0.01318, 0.02449], [1.45333, 0.01389, 0.02467], [1.50667, 0.01495, 0.02484], [1.56, 0.01612, 0.02501], [1.61333, 0.01718, 0.02518], [1.66667, 0.01799, 0.02537], [1.72, 0.01855, 0.02556]], "radius": 0.00092, "radialSegments": 5, "closed": false}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.78}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel"}}, "material": "hamon-steel", "materialLayers": ["hamon-steel"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["blade-face"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(184, 191, 198, 1.0)", "secondaryAlbedo": "rgba(154, 162, 169, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.72}, "explodeWithParent": "blade", "ownerModule": "blade", "face": "front", "mergePolicy": "bake"};
  mesh_hamon_2_3.userData.explodeWithParent = "blade";
  node_hamon_2_3.add(mesh_hamon_2_3);
  meshes["hamon-2"] = mesh_hamon_2_3;
  colliders["hamon-2"] = null;
  destructionGroups["blade"] ??= [];
  destructionGroups["blade"].push(node_hamon_2_3);

  const attachment_hamon_3_4 = {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_hamon_3_4 = makeAttachmentEndpoint(attachment_hamon_3_4);
  const node_hamon_3_4 = new THREE.Group();
  node_hamon_3_4.name = "Hamon front line 3__pivot";
  node_hamon_3_4.scale.set(1, 1, 1);
  if (endpoint_hamon_3_4) {
    node_hamon_3_4.position.copy(endpoint_hamon_3_4.start);
    node_hamon_3_4.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_hamon_3_4.position.set(0.0, 0.0, 0.0);
    node_hamon_3_4.rotation.set(0.0, 0.0, 0.0);
  }
  node_hamon_3_4.userData.sculptComponent = {"id": "hamon-3", "name": "Hamon front line 3", "level": "micro", "role": "detail", "importance": 0.58, "confidence": 0.78, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Primary hamon plus quieter companion etches on the front face; low-frequency wander with tapered ends, not three equal highlight rails.", "geometryDescriptor": {"topologyIntent": "Hamon front line 3 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[0.28, 0.02459, 0.01291], [0.33333, 0.02439, 0.01444], [0.38667, 0.02392, 0.01576], [0.44, 0.02372, 0.01709], [0.49333, 0.02369, 0.0182], [0.54667, 0.0232, 0.01908], [0.6, 0.02361, 0.01996], [0.65333, 0.02385, 0.02075], [0.70667, 0.02385, 0.02141], [0.76, 0.02411, 0.02191], [0.81333, 0.02468, 0.02219], [0.86667, 0.02519, 0.02247], [0.92, 0.02513, 0.0227], [0.97333, 0.02459, 0.02293], [1.02667, 0.02361, 0.02316], [1.08, 0.02273, 0.02335], [1.13333, 0.02201, 0.02353], [1.18667, 0.02168, 0.02372], [1.24, 0.02179, 0.0239], [1.29333, 0.02222, 0.0241], [1.34667, 0.02285, 0.0243], [1.4, 0.02349, 0.02449], [1.45333, 0.0243, 0.02467], [1.50667, 0.02509, 0.02484], [1.56, 0.02577, 0.02501], [1.61333, 0.02635, 0.02518], [1.66667, 0.02687, 0.02537], [1.72, 0.02735, 0.02556]], "radius": 0.00036, "radialSegments": 5, "closed": false}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.78}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel-secondary"}}, "material": "hamon-steel-secondary", "materialLayers": ["hamon-steel-secondary"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["blade-face"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(142, 149, 156, 1.0)", "secondaryAlbedo": "rgba(122, 129, 136, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.6}, "explodeWithParent": "blade", "ownerModule": "blade", "face": "front", "mergePolicy": "bake"};
  node_hamon_3_4.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel-secondary"}};
  node_hamon_3_4.userData.explodeWithParent = "blade";
  (nodes["root"] ?? root).add(node_hamon_3_4);
  nodes["hamon-3"] = node_hamon_3_4;
  const mesh_hamon_3_4Geometry = endpoint_hamon_3_4
    ? new THREE.CylinderGeometry(endpoint_hamon_3_4.endRadius, endpoint_hamon_3_4.baseRadius, endpoint_hamon_3_4.length, 32, 12)
    : buildTubeGeometry({"points": [[0.28, 0.02459, 0.01291], [0.33333, 0.02439, 0.01444], [0.38667, 0.02392, 0.01576], [0.44, 0.02372, 0.01709], [0.49333, 0.02369, 0.0182], [0.54667, 0.0232, 0.01908], [0.6, 0.02361, 0.01996], [0.65333, 0.02385, 0.02075], [0.70667, 0.02385, 0.02141], [0.76, 0.02411, 0.02191], [0.81333, 0.02468, 0.02219], [0.86667, 0.02519, 0.02247], [0.92, 0.02513, 0.0227], [0.97333, 0.02459, 0.02293], [1.02667, 0.02361, 0.02316], [1.08, 0.02273, 0.02335], [1.13333, 0.02201, 0.02353], [1.18667, 0.02168, 0.02372], [1.24, 0.02179, 0.0239], [1.29333, 0.02222, 0.0241], [1.34667, 0.02285, 0.0243], [1.4, 0.02349, 0.02449], [1.45333, 0.0243, 0.02467], [1.50667, 0.02509, 0.02484], [1.56, 0.02577, 0.02501], [1.61333, 0.02635, 0.02518], [1.66667, 0.02687, 0.02537], [1.72, 0.02735, 0.02556]], "radius": 0.00036, "radialSegments": 5, "closed": false});
  if (!endpoint_hamon_3_4) {
    mesh_hamon_3_4Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_hamon_3_4 = new THREE.Mesh(
    mesh_hamon_3_4Geometry,
    materialMap["hamon-steel-secondary"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_hamon_3_4.name = "Hamon front line 3";
  if (endpoint_hamon_3_4) {
    mesh_hamon_3_4.position.copy(endpoint_hamon_3_4.midpoint);
    mesh_hamon_3_4.quaternion.copy(endpoint_hamon_3_4.quaternion);
  }
  mesh_hamon_3_4.castShadow = options.castShadow ?? true;
  mesh_hamon_3_4.receiveShadow = options.receiveShadow ?? true;
  mesh_hamon_3_4.userData.sculptComponent = {"id": "hamon-3", "name": "Hamon front line 3", "level": "micro", "role": "detail", "importance": 0.58, "confidence": 0.78, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Primary hamon plus quieter companion etches on the front face; low-frequency wander with tapered ends, not three equal highlight rails.", "geometryDescriptor": {"topologyIntent": "Hamon front line 3 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[0.28, 0.02459, 0.01291], [0.33333, 0.02439, 0.01444], [0.38667, 0.02392, 0.01576], [0.44, 0.02372, 0.01709], [0.49333, 0.02369, 0.0182], [0.54667, 0.0232, 0.01908], [0.6, 0.02361, 0.01996], [0.65333, 0.02385, 0.02075], [0.70667, 0.02385, 0.02141], [0.76, 0.02411, 0.02191], [0.81333, 0.02468, 0.02219], [0.86667, 0.02519, 0.02247], [0.92, 0.02513, 0.0227], [0.97333, 0.02459, 0.02293], [1.02667, 0.02361, 0.02316], [1.08, 0.02273, 0.02335], [1.13333, 0.02201, 0.02353], [1.18667, 0.02168, 0.02372], [1.24, 0.02179, 0.0239], [1.29333, 0.02222, 0.0241], [1.34667, 0.02285, 0.0243], [1.4, 0.02349, 0.02449], [1.45333, 0.0243, 0.02467], [1.50667, 0.02509, 0.02484], [1.56, 0.02577, 0.02501], [1.61333, 0.02635, 0.02518], [1.66667, 0.02687, 0.02537], [1.72, 0.02735, 0.02556]], "radius": 0.00036, "radialSegments": 5, "closed": false}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.78}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel-secondary"}}, "material": "hamon-steel-secondary", "materialLayers": ["hamon-steel-secondary"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["blade-face"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(142, 149, 156, 1.0)", "secondaryAlbedo": "rgba(122, 129, 136, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.6}, "explodeWithParent": "blade", "ownerModule": "blade", "face": "front", "mergePolicy": "bake"};
  mesh_hamon_3_4.userData.explodeWithParent = "blade";
  node_hamon_3_4.add(mesh_hamon_3_4);
  meshes["hamon-3"] = mesh_hamon_3_4;
  colliders["hamon-3"] = null;
  destructionGroups["blade"] ??= [];
  destructionGroups["blade"].push(node_hamon_3_4);

  const attachment_hamon_back_1_5 = {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_hamon_back_1_5 = makeAttachmentEndpoint(attachment_hamon_back_1_5);
  const node_hamon_back_1_5 = new THREE.Group();
  node_hamon_back_1_5.name = "Hamon back line 1__pivot";
  node_hamon_back_1_5.scale.set(1, 1, 1);
  if (endpoint_hamon_back_1_5) {
    node_hamon_back_1_5.position.copy(endpoint_hamon_back_1_5.start);
    node_hamon_back_1_5.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_hamon_back_1_5.position.set(0.0, 0.0, 0.0);
    node_hamon_back_1_5.rotation.set(0.0, 0.0, 0.0);
  }
  node_hamon_back_1_5.userData.sculptComponent = {"id": "hamon-back-1", "name": "Hamon back line 1", "level": "micro", "role": "detail", "importance": 0.58, "confidence": 0.68, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Primary hamon plus quieter companion etches on the back face; low-frequency wander with tapered ends, not three equal highlight rails.", "geometryDescriptor": {"topologyIntent": "Hamon back line 1 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[0.28, 0.00819, -0.01291], [0.33333, 0.00802, -0.01444], [0.38667, 0.00763, -0.01576], [0.44, 0.00748, -0.01709], [0.49333, 0.00738, -0.0182], [0.54667, 0.00665, -0.01908], [0.6, 0.00668, -0.01996], [0.65333, 0.00649, -0.02075], [0.70667, 0.00621, -0.02141], [0.76, 0.00649, -0.02191], [0.81333, 0.00743, -0.02219], [0.86667, 0.0086, -0.02247], [0.92, 0.00934, -0.0227], [0.97333, 0.00956, -0.02293], [1.02667, 0.00918, -0.02316], [1.08, 0.00864, -0.02335], [1.13333, 0.00798, -0.02353], [1.18667, 0.00742, -0.02372], [1.24, 0.00707, -0.0239], [1.29333, 0.00684, -0.0241], [1.34667, 0.0068, -0.0243], [1.4, 0.0069, -0.02449], [1.45333, 0.0074, -0.02467], [1.50667, 0.00816, -0.02484], [1.56, 0.00898, -0.02501], [1.61333, 0.00976, -0.02518], [1.66667, 0.01042, -0.02537], [1.72, 0.01095, -0.02556]], "radius": 0.00042, "radialSegments": 5, "closed": false}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.68}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel-secondary"}}, "material": "hamon-steel-secondary", "materialLayers": ["hamon-steel-secondary"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["blade-face"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(142, 149, 156, 1.0)", "secondaryAlbedo": "rgba(122, 129, 136, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.6}, "explodeWithParent": "blade", "ownerModule": "blade", "face": "back", "mergePolicy": "bake"};
  node_hamon_back_1_5.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel-secondary"}};
  node_hamon_back_1_5.userData.explodeWithParent = "blade";
  (nodes["root"] ?? root).add(node_hamon_back_1_5);
  nodes["hamon-back-1"] = node_hamon_back_1_5;
  const mesh_hamon_back_1_5Geometry = endpoint_hamon_back_1_5
    ? new THREE.CylinderGeometry(endpoint_hamon_back_1_5.endRadius, endpoint_hamon_back_1_5.baseRadius, endpoint_hamon_back_1_5.length, 32, 12)
    : buildTubeGeometry({"points": [[0.28, 0.00819, -0.01291], [0.33333, 0.00802, -0.01444], [0.38667, 0.00763, -0.01576], [0.44, 0.00748, -0.01709], [0.49333, 0.00738, -0.0182], [0.54667, 0.00665, -0.01908], [0.6, 0.00668, -0.01996], [0.65333, 0.00649, -0.02075], [0.70667, 0.00621, -0.02141], [0.76, 0.00649, -0.02191], [0.81333, 0.00743, -0.02219], [0.86667, 0.0086, -0.02247], [0.92, 0.00934, -0.0227], [0.97333, 0.00956, -0.02293], [1.02667, 0.00918, -0.02316], [1.08, 0.00864, -0.02335], [1.13333, 0.00798, -0.02353], [1.18667, 0.00742, -0.02372], [1.24, 0.00707, -0.0239], [1.29333, 0.00684, -0.0241], [1.34667, 0.0068, -0.0243], [1.4, 0.0069, -0.02449], [1.45333, 0.0074, -0.02467], [1.50667, 0.00816, -0.02484], [1.56, 0.00898, -0.02501], [1.61333, 0.00976, -0.02518], [1.66667, 0.01042, -0.02537], [1.72, 0.01095, -0.02556]], "radius": 0.00042, "radialSegments": 5, "closed": false});
  if (!endpoint_hamon_back_1_5) {
    mesh_hamon_back_1_5Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_hamon_back_1_5 = new THREE.Mesh(
    mesh_hamon_back_1_5Geometry,
    materialMap["hamon-steel-secondary"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_hamon_back_1_5.name = "Hamon back line 1";
  if (endpoint_hamon_back_1_5) {
    mesh_hamon_back_1_5.position.copy(endpoint_hamon_back_1_5.midpoint);
    mesh_hamon_back_1_5.quaternion.copy(endpoint_hamon_back_1_5.quaternion);
  }
  mesh_hamon_back_1_5.castShadow = options.castShadow ?? true;
  mesh_hamon_back_1_5.receiveShadow = options.receiveShadow ?? true;
  mesh_hamon_back_1_5.userData.sculptComponent = {"id": "hamon-back-1", "name": "Hamon back line 1", "level": "micro", "role": "detail", "importance": 0.58, "confidence": 0.68, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Primary hamon plus quieter companion etches on the back face; low-frequency wander with tapered ends, not three equal highlight rails.", "geometryDescriptor": {"topologyIntent": "Hamon back line 1 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[0.28, 0.00819, -0.01291], [0.33333, 0.00802, -0.01444], [0.38667, 0.00763, -0.01576], [0.44, 0.00748, -0.01709], [0.49333, 0.00738, -0.0182], [0.54667, 0.00665, -0.01908], [0.6, 0.00668, -0.01996], [0.65333, 0.00649, -0.02075], [0.70667, 0.00621, -0.02141], [0.76, 0.00649, -0.02191], [0.81333, 0.00743, -0.02219], [0.86667, 0.0086, -0.02247], [0.92, 0.00934, -0.0227], [0.97333, 0.00956, -0.02293], [1.02667, 0.00918, -0.02316], [1.08, 0.00864, -0.02335], [1.13333, 0.00798, -0.02353], [1.18667, 0.00742, -0.02372], [1.24, 0.00707, -0.0239], [1.29333, 0.00684, -0.0241], [1.34667, 0.0068, -0.0243], [1.4, 0.0069, -0.02449], [1.45333, 0.0074, -0.02467], [1.50667, 0.00816, -0.02484], [1.56, 0.00898, -0.02501], [1.61333, 0.00976, -0.02518], [1.66667, 0.01042, -0.02537], [1.72, 0.01095, -0.02556]], "radius": 0.00042, "radialSegments": 5, "closed": false}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.68}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel-secondary"}}, "material": "hamon-steel-secondary", "materialLayers": ["hamon-steel-secondary"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["blade-face"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(142, 149, 156, 1.0)", "secondaryAlbedo": "rgba(122, 129, 136, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.6}, "explodeWithParent": "blade", "ownerModule": "blade", "face": "back", "mergePolicy": "bake"};
  mesh_hamon_back_1_5.userData.explodeWithParent = "blade";
  node_hamon_back_1_5.add(mesh_hamon_back_1_5);
  meshes["hamon-back-1"] = mesh_hamon_back_1_5;
  colliders["hamon-back-1"] = null;
  destructionGroups["blade"] ??= [];
  destructionGroups["blade"].push(node_hamon_back_1_5);

  const attachment_hamon_back_2_6 = {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_hamon_back_2_6 = makeAttachmentEndpoint(attachment_hamon_back_2_6);
  const node_hamon_back_2_6 = new THREE.Group();
  node_hamon_back_2_6.name = "Hamon back line 2__pivot";
  node_hamon_back_2_6.scale.set(1, 1, 1);
  if (endpoint_hamon_back_2_6) {
    node_hamon_back_2_6.position.copy(endpoint_hamon_back_2_6.start);
    node_hamon_back_2_6.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_hamon_back_2_6.position.set(0.0, 0.0, 0.0);
    node_hamon_back_2_6.rotation.set(0.0, 0.0, 0.0);
  }
  node_hamon_back_2_6.userData.sculptComponent = {"id": "hamon-back-2", "name": "Hamon back line 2", "level": "micro", "role": "detail", "importance": 0.78, "confidence": 0.68, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Primary hamon plus quieter companion etches on the back face; low-frequency wander with tapered ends, not three equal highlight rails.", "geometryDescriptor": {"topologyIntent": "Hamon back line 2 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[0.28, 0.01579, -0.01291], [0.33333, 0.0156, -0.01444], [0.38667, 0.01513, -0.01576], [0.44, 0.01488, -0.01709], [0.49333, 0.01474, -0.0182], [0.54667, 0.01406, -0.01908], [0.6, 0.01426, -0.01996], [0.65333, 0.01435, -0.02075], [0.70667, 0.0145, -0.02141], [0.76, 0.01537, -0.02191], [0.81333, 0.01698, -0.02219], [0.86667, 0.0187, -0.02247], [0.92, 0.01956, -0.0227], [0.97333, 0.01924, -0.02293], [1.02667, 0.01765, -0.02316], [1.08, 0.01553, -0.02335], [1.13333, 0.01338, -0.02353], [1.18667, 0.01182, -0.02372], [1.24, 0.01117, -0.0239], [1.29333, 0.01126, -0.0241], [1.34667, 0.0119, -0.0243], [1.4, 0.01277, -0.02449], [1.45333, 0.01395, -0.02467], [1.50667, 0.01522, -0.02484], [1.56, 0.01639, -0.02501], [1.61333, 0.01734, -0.02518], [1.66667, 0.01804, -0.02537], [1.72, 0.01855, -0.02556]], "radius": 0.00092, "radialSegments": 5, "closed": false}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.68}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel"}}, "material": "hamon-steel", "materialLayers": ["hamon-steel"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["blade-face"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(184, 191, 198, 1.0)", "secondaryAlbedo": "rgba(154, 162, 169, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.72}, "explodeWithParent": "blade", "ownerModule": "blade", "face": "back", "mergePolicy": "bake"};
  node_hamon_back_2_6.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel"}};
  node_hamon_back_2_6.userData.explodeWithParent = "blade";
  (nodes["root"] ?? root).add(node_hamon_back_2_6);
  nodes["hamon-back-2"] = node_hamon_back_2_6;
  const mesh_hamon_back_2_6Geometry = endpoint_hamon_back_2_6
    ? new THREE.CylinderGeometry(endpoint_hamon_back_2_6.endRadius, endpoint_hamon_back_2_6.baseRadius, endpoint_hamon_back_2_6.length, 32, 12)
    : buildTubeGeometry({"points": [[0.28, 0.01579, -0.01291], [0.33333, 0.0156, -0.01444], [0.38667, 0.01513, -0.01576], [0.44, 0.01488, -0.01709], [0.49333, 0.01474, -0.0182], [0.54667, 0.01406, -0.01908], [0.6, 0.01426, -0.01996], [0.65333, 0.01435, -0.02075], [0.70667, 0.0145, -0.02141], [0.76, 0.01537, -0.02191], [0.81333, 0.01698, -0.02219], [0.86667, 0.0187, -0.02247], [0.92, 0.01956, -0.0227], [0.97333, 0.01924, -0.02293], [1.02667, 0.01765, -0.02316], [1.08, 0.01553, -0.02335], [1.13333, 0.01338, -0.02353], [1.18667, 0.01182, -0.02372], [1.24, 0.01117, -0.0239], [1.29333, 0.01126, -0.0241], [1.34667, 0.0119, -0.0243], [1.4, 0.01277, -0.02449], [1.45333, 0.01395, -0.02467], [1.50667, 0.01522, -0.02484], [1.56, 0.01639, -0.02501], [1.61333, 0.01734, -0.02518], [1.66667, 0.01804, -0.02537], [1.72, 0.01855, -0.02556]], "radius": 0.00092, "radialSegments": 5, "closed": false});
  if (!endpoint_hamon_back_2_6) {
    mesh_hamon_back_2_6Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_hamon_back_2_6 = new THREE.Mesh(
    mesh_hamon_back_2_6Geometry,
    materialMap["hamon-steel"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_hamon_back_2_6.name = "Hamon back line 2";
  if (endpoint_hamon_back_2_6) {
    mesh_hamon_back_2_6.position.copy(endpoint_hamon_back_2_6.midpoint);
    mesh_hamon_back_2_6.quaternion.copy(endpoint_hamon_back_2_6.quaternion);
  }
  mesh_hamon_back_2_6.castShadow = options.castShadow ?? true;
  mesh_hamon_back_2_6.receiveShadow = options.receiveShadow ?? true;
  mesh_hamon_back_2_6.userData.sculptComponent = {"id": "hamon-back-2", "name": "Hamon back line 2", "level": "micro", "role": "detail", "importance": 0.78, "confidence": 0.68, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Primary hamon plus quieter companion etches on the back face; low-frequency wander with tapered ends, not three equal highlight rails.", "geometryDescriptor": {"topologyIntent": "Hamon back line 2 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[0.28, 0.01579, -0.01291], [0.33333, 0.0156, -0.01444], [0.38667, 0.01513, -0.01576], [0.44, 0.01488, -0.01709], [0.49333, 0.01474, -0.0182], [0.54667, 0.01406, -0.01908], [0.6, 0.01426, -0.01996], [0.65333, 0.01435, -0.02075], [0.70667, 0.0145, -0.02141], [0.76, 0.01537, -0.02191], [0.81333, 0.01698, -0.02219], [0.86667, 0.0187, -0.02247], [0.92, 0.01956, -0.0227], [0.97333, 0.01924, -0.02293], [1.02667, 0.01765, -0.02316], [1.08, 0.01553, -0.02335], [1.13333, 0.01338, -0.02353], [1.18667, 0.01182, -0.02372], [1.24, 0.01117, -0.0239], [1.29333, 0.01126, -0.0241], [1.34667, 0.0119, -0.0243], [1.4, 0.01277, -0.02449], [1.45333, 0.01395, -0.02467], [1.50667, 0.01522, -0.02484], [1.56, 0.01639, -0.02501], [1.61333, 0.01734, -0.02518], [1.66667, 0.01804, -0.02537], [1.72, 0.01855, -0.02556]], "radius": 0.00092, "radialSegments": 5, "closed": false}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.68}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel"}}, "material": "hamon-steel", "materialLayers": ["hamon-steel"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["blade-face"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(184, 191, 198, 1.0)", "secondaryAlbedo": "rgba(154, 162, 169, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.72}, "explodeWithParent": "blade", "ownerModule": "blade", "face": "back", "mergePolicy": "bake"};
  mesh_hamon_back_2_6.userData.explodeWithParent = "blade";
  node_hamon_back_2_6.add(mesh_hamon_back_2_6);
  meshes["hamon-back-2"] = mesh_hamon_back_2_6;
  colliders["hamon-back-2"] = null;
  destructionGroups["blade"] ??= [];
  destructionGroups["blade"].push(node_hamon_back_2_6);

  const attachment_hamon_back_3_7 = {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_hamon_back_3_7 = makeAttachmentEndpoint(attachment_hamon_back_3_7);
  const node_hamon_back_3_7 = new THREE.Group();
  node_hamon_back_3_7.name = "Hamon back line 3__pivot";
  node_hamon_back_3_7.scale.set(1, 1, 1);
  if (endpoint_hamon_back_3_7) {
    node_hamon_back_3_7.position.copy(endpoint_hamon_back_3_7.start);
    node_hamon_back_3_7.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_hamon_back_3_7.position.set(0.0, 0.0, 0.0);
    node_hamon_back_3_7.rotation.set(0.0, 0.0, 0.0);
  }
  node_hamon_back_3_7.userData.sculptComponent = {"id": "hamon-back-3", "name": "Hamon back line 3", "level": "micro", "role": "detail", "importance": 0.58, "confidence": 0.68, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Primary hamon plus quieter companion etches on the back face; low-frequency wander with tapered ends, not three equal highlight rails.", "geometryDescriptor": {"topologyIntent": "Hamon back line 3 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[0.28, 0.02459, -0.01291], [0.33333, 0.02438, -0.01444], [0.38667, 0.02391, -0.01576], [0.44, 0.02375, -0.01709], [0.49333, 0.0238, -0.0182], [0.54667, 0.02342, -0.01908], [0.6, 0.02396, -0.01996], [0.65333, 0.02426, -0.02075], [0.70667, 0.0242, -0.02141], [0.76, 0.02426, -0.02191], [0.81333, 0.0245, -0.02219], [0.86667, 0.02464, -0.02247], [0.92, 0.02424, -0.0227], [0.97333, 0.02347, -0.02293], [1.02667, 0.02241, -0.02316], [1.08, 0.0216, -0.02335], [1.13333, 0.02108, -0.02353], [1.18667, 0.02103, -0.02372], [1.24, 0.02147, -0.0239], [1.29333, 0.02221, -0.0241], [1.34667, 0.02307, -0.0243], [1.4, 0.02384, -0.02449], [1.45333, 0.02465, -0.02467], [1.50667, 0.02536, -0.02484], [1.56, 0.02593, -0.02501], [1.61333, 0.02642, -0.02518], [1.66667, 0.02688, -0.02537], [1.72, 0.02735, -0.02556]], "radius": 0.00036, "radialSegments": 5, "closed": false}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.68}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel-secondary"}}, "material": "hamon-steel-secondary", "materialLayers": ["hamon-steel-secondary"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["blade-face"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(142, 149, 156, 1.0)", "secondaryAlbedo": "rgba(122, 129, 136, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.6}, "explodeWithParent": "blade", "ownerModule": "blade", "face": "back", "mergePolicy": "bake"};
  node_hamon_back_3_7.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel-secondary"}};
  node_hamon_back_3_7.userData.explodeWithParent = "blade";
  (nodes["root"] ?? root).add(node_hamon_back_3_7);
  nodes["hamon-back-3"] = node_hamon_back_3_7;
  const mesh_hamon_back_3_7Geometry = endpoint_hamon_back_3_7
    ? new THREE.CylinderGeometry(endpoint_hamon_back_3_7.endRadius, endpoint_hamon_back_3_7.baseRadius, endpoint_hamon_back_3_7.length, 32, 12)
    : buildTubeGeometry({"points": [[0.28, 0.02459, -0.01291], [0.33333, 0.02438, -0.01444], [0.38667, 0.02391, -0.01576], [0.44, 0.02375, -0.01709], [0.49333, 0.0238, -0.0182], [0.54667, 0.02342, -0.01908], [0.6, 0.02396, -0.01996], [0.65333, 0.02426, -0.02075], [0.70667, 0.0242, -0.02141], [0.76, 0.02426, -0.02191], [0.81333, 0.0245, -0.02219], [0.86667, 0.02464, -0.02247], [0.92, 0.02424, -0.0227], [0.97333, 0.02347, -0.02293], [1.02667, 0.02241, -0.02316], [1.08, 0.0216, -0.02335], [1.13333, 0.02108, -0.02353], [1.18667, 0.02103, -0.02372], [1.24, 0.02147, -0.0239], [1.29333, 0.02221, -0.0241], [1.34667, 0.02307, -0.0243], [1.4, 0.02384, -0.02449], [1.45333, 0.02465, -0.02467], [1.50667, 0.02536, -0.02484], [1.56, 0.02593, -0.02501], [1.61333, 0.02642, -0.02518], [1.66667, 0.02688, -0.02537], [1.72, 0.02735, -0.02556]], "radius": 0.00036, "radialSegments": 5, "closed": false});
  if (!endpoint_hamon_back_3_7) {
    mesh_hamon_back_3_7Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_hamon_back_3_7 = new THREE.Mesh(
    mesh_hamon_back_3_7Geometry,
    materialMap["hamon-steel-secondary"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_hamon_back_3_7.name = "Hamon back line 3";
  if (endpoint_hamon_back_3_7) {
    mesh_hamon_back_3_7.position.copy(endpoint_hamon_back_3_7.midpoint);
    mesh_hamon_back_3_7.quaternion.copy(endpoint_hamon_back_3_7.quaternion);
  }
  mesh_hamon_back_3_7.castShadow = options.castShadow ?? true;
  mesh_hamon_back_3_7.receiveShadow = options.receiveShadow ?? true;
  mesh_hamon_back_3_7.userData.sculptComponent = {"id": "hamon-back-3", "name": "Hamon back line 3", "level": "micro", "role": "detail", "importance": 0.58, "confidence": 0.68, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Primary hamon plus quieter companion etches on the back face; low-frequency wander with tapered ends, not three equal highlight rails.", "geometryDescriptor": {"topologyIntent": "Hamon back line 3 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[0.28, 0.02459, -0.01291], [0.33333, 0.02438, -0.01444], [0.38667, 0.02391, -0.01576], [0.44, 0.02375, -0.01709], [0.49333, 0.0238, -0.0182], [0.54667, 0.02342, -0.01908], [0.6, 0.02396, -0.01996], [0.65333, 0.02426, -0.02075], [0.70667, 0.0242, -0.02141], [0.76, 0.02426, -0.02191], [0.81333, 0.0245, -0.02219], [0.86667, 0.02464, -0.02247], [0.92, 0.02424, -0.0227], [0.97333, 0.02347, -0.02293], [1.02667, 0.02241, -0.02316], [1.08, 0.0216, -0.02335], [1.13333, 0.02108, -0.02353], [1.18667, 0.02103, -0.02372], [1.24, 0.02147, -0.0239], [1.29333, 0.02221, -0.0241], [1.34667, 0.02307, -0.0243], [1.4, 0.02384, -0.02449], [1.45333, 0.02465, -0.02467], [1.50667, 0.02536, -0.02484], [1.56, 0.02593, -0.02501], [1.61333, 0.02642, -0.02518], [1.66667, 0.02688, -0.02537], [1.72, 0.02735, -0.02556]], "radius": 0.00036, "radialSegments": 5, "closed": false}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.68}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "blade", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hamon-steel-secondary"}}, "material": "hamon-steel-secondary", "materialLayers": ["hamon-steel-secondary"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["blade-face"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(142, 149, 156, 1.0)", "secondaryAlbedo": "rgba(122, 129, 136, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.6}, "explodeWithParent": "blade", "ownerModule": "blade", "face": "back", "mergePolicy": "bake"};
  mesh_hamon_back_3_7.userData.explodeWithParent = "blade";
  node_hamon_back_3_7.add(mesh_hamon_back_3_7);
  meshes["hamon-back-3"] = mesh_hamon_back_3_7;
  colliders["hamon-back-3"] = null;
  destructionGroups["blade"] ??= [];
  destructionGroups["blade"].push(node_hamon_back_3_7);

  const attachment_guard_8 = {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.012, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_guard_8 = makeAttachmentEndpoint(attachment_guard_8);
  const node_guard_8 = new THREE.Group();
  node_guard_8.name = "Disk guard__pivot";
  node_guard_8.scale.set(1, 1, 1);
  if (endpoint_guard_8) {
    node_guard_8.position.copy(endpoint_guard_8.start);
    node_guard_8.rotation.set(0.0, 0.0, 1.5707963267948966);
  } else {
    node_guard_8.position.set(1.814504716981132, 0.0, 0.0);
    node_guard_8.rotation.set(0.0, 0.0, 1.5707963267948966);
  }
  node_guard_8.userData.sculptComponent = {"id": "guard", "name": "Disk guard", "level": "meso", "role": "body", "importance": 0.95, "confidence": 0.86, "primitive": "cylinder", "topologyClass": "assembled-solid", "topologyRationale": "Disk axis along the blade. Face-on it is a thin gilt edge; from the tip or a 3/4 it reads as a circle.", "geometryDescriptor": {"topologyIntent": "Disk guard reconstruction", "edgeTreatment": {"type": "chamfer", "bevelRadius": 0.0015, "segments": 2}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.012, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 0.11933962264150942, "height": 0.004068396226415094, "depth": 0.11933962264150942, "units": "relative", "confidence": 0.86}, "transform": {"position": [1.814504716981132, 0.0, 0.0], "rotation": [0.0, 0.0, 1.5707963267948966], "scale": [0.11933962264150942, 0.004068396226415094, 0.11933962264150942]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "disk guard proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "guard", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [{"id": "disk-guard", "kind": "contour", "notes": "Circle in side/top; thin gilt line in the face view."}], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["side-guard", "full-object"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.8}};
  node_guard_8.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "disk guard proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "guard", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}};
  node_guard_8.userData.explodeWithParent = null;
  (nodes["root"] ?? root).add(node_guard_8);
  nodes["guard"] = node_guard_8;
  const mesh_guard_8Geometry = endpoint_guard_8
    ? new THREE.CylinderGeometry(endpoint_guard_8.endRadius, endpoint_guard_8.baseRadius, endpoint_guard_8.length, 32, 12)
    : new THREE.CylinderGeometry(0.5, 0.5, 1, 48, 16);
  if (!endpoint_guard_8) {
    mesh_guard_8Geometry.scale(0.11933962264150942, 0.004068396226415094, 0.11933962264150942);
  }
  const mesh_guard_8 = new THREE.Mesh(
    mesh_guard_8Geometry,
    materialMap["gilt-bronze"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_guard_8.name = "Disk guard";
  if (endpoint_guard_8) {
    mesh_guard_8.position.copy(endpoint_guard_8.midpoint);
    mesh_guard_8.quaternion.copy(endpoint_guard_8.quaternion);
  }
  mesh_guard_8.castShadow = options.castShadow ?? true;
  mesh_guard_8.receiveShadow = options.receiveShadow ?? true;
  mesh_guard_8.userData.sculptComponent = {"id": "guard", "name": "Disk guard", "level": "meso", "role": "body", "importance": 0.95, "confidence": 0.86, "primitive": "cylinder", "topologyClass": "assembled-solid", "topologyRationale": "Disk axis along the blade. Face-on it is a thin gilt edge; from the tip or a 3/4 it reads as a circle.", "geometryDescriptor": {"topologyIntent": "Disk guard reconstruction", "edgeTreatment": {"type": "chamfer", "bevelRadius": 0.0015, "segments": 2}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.012, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 0.11933962264150942, "height": 0.004068396226415094, "depth": 0.11933962264150942, "units": "relative", "confidence": 0.86}, "transform": {"position": [1.814504716981132, 0.0, 0.0], "rotation": [0.0, 0.0, 1.5707963267948966], "scale": [0.11933962264150942, 0.004068396226415094, 0.11933962264150942]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "disk guard proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "guard", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [{"id": "disk-guard", "kind": "contour", "notes": "Circle in side/top; thin gilt line in the face view."}], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["side-guard", "full-object"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.8}};
  mesh_guard_8.userData.explodeWithParent = null;
  node_guard_8.add(mesh_guard_8);
  meshes["guard"] = mesh_guard_8;
  colliders["guard"] = {"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "disk guard proxy"};
  destructionGroups["guard"] ??= [];
  destructionGroups["guard"].push(node_guard_8);

  const attachment_collar_9 = {"parentId": "root", "parentSocket": "guard-back", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.012, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_collar_9 = makeAttachmentEndpoint(attachment_collar_9);
  const node_collar_9 = new THREE.Group();
  node_collar_9.name = "Gilt front ferrule__pivot";
  node_collar_9.scale.set(1, 1, 1);
  if (endpoint_collar_9) {
    node_collar_9.position.copy(endpoint_collar_9.start);
    node_collar_9.rotation.set(0.0, 0.0, 1.5707963267948966);
  } else {
    node_collar_9.position.set(1.8200318396226414, 0.0, 0.0);
    node_collar_9.rotation.set(0.0, 0.0, 1.5707963267948966);
  }
  node_collar_9.userData.sculptComponent = {"id": "collar", "name": "Gilt front ferrule", "level": "meso", "role": "body", "importance": 0.8, "confidence": 0.8, "primitive": "cylinder", "topologyClass": "assembled-solid", "topologyRationale": "Short gilt sleeve between the disk and the wrap. Thinner than the wrap so it reads as a band, not a cap.", "geometryDescriptor": {"topologyIntent": "Gilt front ferrule reconstruction", "edgeTreatment": {"type": "chamfer", "bevelRadius": 0.0015, "segments": 2}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "guard-back", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.012, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 0.07865566037735848, "height": 0.018985849056603773, "depth": 0.07865566037735848, "units": "relative", "confidence": 0.8}, "transform": {"position": [1.8200318396226414, 0.0, 0.0], "rotation": [0.0, 0.0, 1.5707963267948966], "scale": [0.07865566037735848, 0.018985849056603773, 0.07865566037735848]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "front ferrule proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "collar", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [{"id": "front-ferrule", "kind": "seam", "notes": "Visually distinct from wrap and steel."}], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["full-object", "handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.78}};
  node_collar_9.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "front ferrule proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "collar", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}};
  node_collar_9.userData.explodeWithParent = null;
  (nodes["root"] ?? root).add(node_collar_9);
  nodes["collar"] = node_collar_9;
  const mesh_collar_9Geometry = endpoint_collar_9
    ? new THREE.CylinderGeometry(endpoint_collar_9.endRadius, endpoint_collar_9.baseRadius, endpoint_collar_9.length, 32, 12)
    : new THREE.CylinderGeometry(0.5, 0.5, 1, 48, 16);
  if (!endpoint_collar_9) {
    mesh_collar_9Geometry.scale(0.07865566037735848, 0.018985849056603773, 0.07865566037735848);
  }
  const mesh_collar_9 = new THREE.Mesh(
    mesh_collar_9Geometry,
    materialMap["gilt-bronze"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_collar_9.name = "Gilt front ferrule";
  if (endpoint_collar_9) {
    mesh_collar_9.position.copy(endpoint_collar_9.midpoint);
    mesh_collar_9.quaternion.copy(endpoint_collar_9.quaternion);
  }
  mesh_collar_9.castShadow = options.castShadow ?? true;
  mesh_collar_9.receiveShadow = options.receiveShadow ?? true;
  mesh_collar_9.userData.sculptComponent = {"id": "collar", "name": "Gilt front ferrule", "level": "meso", "role": "body", "importance": 0.8, "confidence": 0.8, "primitive": "cylinder", "topologyClass": "assembled-solid", "topologyRationale": "Short gilt sleeve between the disk and the wrap. Thinner than the wrap so it reads as a band, not a cap.", "geometryDescriptor": {"topologyIntent": "Gilt front ferrule reconstruction", "edgeTreatment": {"type": "chamfer", "bevelRadius": 0.0015, "segments": 2}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "guard-back", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.012, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 0.07865566037735848, "height": 0.018985849056603773, "depth": 0.07865566037735848, "units": "relative", "confidence": 0.8}, "transform": {"position": [1.8200318396226414, 0.0, 0.0], "rotation": [0.0, 0.0, 1.5707963267948966], "scale": [0.07865566037735848, 0.018985849056603773, 0.07865566037735848]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "front ferrule proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "collar", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [{"id": "front-ferrule", "kind": "seam", "notes": "Visually distinct from wrap and steel."}], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["full-object", "handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.78}};
  mesh_collar_9.userData.explodeWithParent = null;
  node_collar_9.add(mesh_collar_9);
  meshes["collar"] = mesh_collar_9;
  colliders["collar"] = {"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "front ferrule proxy"};
  destructionGroups["collar"] ??= [];
  destructionGroups["collar"].push(node_collar_9);

  const attachment_handle_10 = {"parentId": "root", "parentSocket": "front-ferrule-back", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.02, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_handle_10 = makeAttachmentEndpoint(attachment_handle_10);
  const node_handle_10 = new THREE.Group();
  node_handle_10.name = "Cord-wrapped grip__pivot";
  node_handle_10.scale.set(1, 1, 1);
  if (endpoint_handle_10) {
    node_handle_10.position.copy(endpoint_handle_10.start);
    node_handle_10.rotation.set(0.0, 0.0, 1.5707963267948966);
  } else {
    node_handle_10.position.set(1.9653089622641509, 0.0, 0.0);
    node_handle_10.rotation.set(0.0, 0.0, 1.5707963267948966);
  }
  node_handle_10.userData.sculptComponent = {"id": "handle", "name": "Cord-wrapped grip", "level": "macro", "role": "handle", "importance": 0.95, "confidence": 0.84, "primitive": "cylinder", "topologyClass": "assembled-solid", "topologyRationale": "Dark cylindrical wrap. Degenerate attachment keeps the authored cylinder.", "geometryDescriptor": {"topologyIntent": "Cord-wrapped grip reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "front-ferrule-back", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.02, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 0.09086084905660377, "height": 0.29156839622641506, "depth": 0.09086084905660377, "units": "relative", "confidence": 0.84}, "transform": {"position": [1.9653089622641509, 0.0, 0.0], "rotation": [0.0, 0.0, 1.5707963267948966], "scale": [0.09086084905660377, 0.29156839622641506, 0.09086084905660377]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "grip proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "cord-wrap"}}, "material": "cord-wrap", "materialLayers": ["cord-wrap"], "deformations": [], "joints": [], "seams": [], "localFeatures": [{"id": "wrapped-grip", "kind": "seam", "notes": "Two counter-wound helical seam tubes define the crossed wrap."}, {"id": "diamond-inlays", "kind": "fastener", "notes": "Six thin gilt lozenges per face sit nearly flush in paired dark seats."}], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(58, 36, 24, 1.0)", "secondaryAlbedo": "rgba(36, 22, 16, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.78}};
  node_handle_10.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "grip proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "cord-wrap"}};
  node_handle_10.userData.explodeWithParent = null;
  (nodes["root"] ?? root).add(node_handle_10);
  nodes["handle"] = node_handle_10;
  const mesh_handle_10Geometry = endpoint_handle_10
    ? new THREE.CylinderGeometry(endpoint_handle_10.endRadius, endpoint_handle_10.baseRadius, endpoint_handle_10.length, 32, 12)
    : new THREE.CylinderGeometry(0.5, 0.5, 1, 48, 16);
  if (!endpoint_handle_10) {
    mesh_handle_10Geometry.scale(0.09086084905660377, 0.29156839622641506, 0.09086084905660377);
  }
  const mesh_handle_10 = new THREE.Mesh(
    mesh_handle_10Geometry,
    materialMap["cord-wrap"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_handle_10.name = "Cord-wrapped grip";
  if (endpoint_handle_10) {
    mesh_handle_10.position.copy(endpoint_handle_10.midpoint);
    mesh_handle_10.quaternion.copy(endpoint_handle_10.quaternion);
  }
  mesh_handle_10.castShadow = options.castShadow ?? true;
  mesh_handle_10.receiveShadow = options.receiveShadow ?? true;
  mesh_handle_10.userData.sculptComponent = {"id": "handle", "name": "Cord-wrapped grip", "level": "macro", "role": "handle", "importance": 0.95, "confidence": 0.84, "primitive": "cylinder", "topologyClass": "assembled-solid", "topologyRationale": "Dark cylindrical wrap. Degenerate attachment keeps the authored cylinder.", "geometryDescriptor": {"topologyIntent": "Cord-wrapped grip reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "front-ferrule-back", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.02, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 0.09086084905660377, "height": 0.29156839622641506, "depth": 0.09086084905660377, "units": "relative", "confidence": 0.84}, "transform": {"position": [1.9653089622641509, 0.0, 0.0], "rotation": [0.0, 0.0, 1.5707963267948966], "scale": [0.09086084905660377, 0.29156839622641506, 0.09086084905660377]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "grip proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "cord-wrap"}}, "material": "cord-wrap", "materialLayers": ["cord-wrap"], "deformations": [], "joints": [], "seams": [], "localFeatures": [{"id": "wrapped-grip", "kind": "seam", "notes": "Two counter-wound helical seam tubes define the crossed wrap."}, {"id": "diamond-inlays", "kind": "fastener", "notes": "Six thin gilt lozenges per face sit nearly flush in paired dark seats."}], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(58, 36, 24, 1.0)", "secondaryAlbedo": "rgba(36, 22, 16, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.78}};
  mesh_handle_10.userData.explodeWithParent = null;
  node_handle_10.add(mesh_handle_10);
  meshes["handle"] = mesh_handle_10;
  colliders["handle"] = {"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "grip proxy"};
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_handle_10);

  const attachment_wrap_seam_1_11 = {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_wrap_seam_1_11 = makeAttachmentEndpoint(attachment_wrap_seam_1_11);
  const node_wrap_seam_1_11 = new THREE.Group();
  node_wrap_seam_1_11.name = "Cord wrap seam 1__pivot";
  node_wrap_seam_1_11.scale.set(1, 1, 1);
  if (endpoint_wrap_seam_1_11) {
    node_wrap_seam_1_11.position.copy(endpoint_wrap_seam_1_11.start);
    node_wrap_seam_1_11.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_wrap_seam_1_11.position.set(0.0, 0.0, 0.0);
    node_wrap_seam_1_11.rotation.set(0.0, 0.0, 0.0);
  }
  node_wrap_seam_1_11.userData.sculptComponent = {"id": "wrap-seam-1", "name": "Cord wrap seam 1", "level": "micro", "role": "detail", "importance": 0.7, "confidence": 0.8, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "A shallow helical valley makes the wrapped grip read in face and orbit views.", "geometryDescriptor": {"topologyIntent": "Cord wrap seam 1 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[1.82552, 0.01536, 0.04208], [1.82844, 0.0239, 0.03789], [1.83135, 0.03136, 0.03199], [1.83426, 0.0374, 0.02465], [1.83717, 0.04176, 0.0162], [1.84009, 0.04424, 0.00701], [1.843, 0.04473, -0.00248], [1.84591, 0.04319, -0.01187], [1.84882, 0.03971, -0.02072], [1.85173, 0.03444, -0.02864], [1.85465, 0.02762, -0.03526], [1.85756, 0.01956, -0.0403], [1.86047, 0.01061, -0.04352], [1.86338, 0.00118, -0.04478], [1.8663, -0.0083, -0.04402], [1.86921, -0.01741, -0.04127], [1.87212, -0.02573, -0.03667], [1.87503, -0.03289, -0.03041], [1.87794, -0.03857, -0.02278], [1.88086, -0.04251, -0.01413], [1.88377, -0.04453, -0.00484], [1.88668, -0.04455, 0.00468], [1.88959, -0.04256, 0.01397], [1.8925, -0.03865, 0.02264], [1.89542, -0.033, 0.03029], [1.89833, -0.02586, 0.03658], [1.90124, -0.01755, 0.04121], [1.90415, -0.00846, 0.04399], [1.90707, 0.00102, 0.04478], [1.90998, 0.01045, 0.04356], [1.91289, 0.01941, 0.04037], [1.9158, 0.0275, 0.03536], [1.91871, 0.03434, 0.02876], [1.92163, 0.03964, 0.02086], [1.92454, 0.04315, 0.01202], [1.92745, 0.04472, 0.00264], [1.93036, 0.04427, -0.00686], [1.93328, 0.04182, -0.01605], [1.93619, 0.03749, -0.02451], [1.9391, 0.03147, -0.03188], [1.94201, 0.02403, -0.0378], [1.94492, 0.01551, -0.04202], [1.94784, 0.00629, -0.04435], [1.95075, -0.00322, -0.04468], [1.95366, -0.01257, -0.04299], [1.95657, -0.02137, -0.03937], [1.95948, -0.0292, -0.03397], [1.9624, -0.03571, -0.02704], [1.96531, -0.04062, -0.01889], [1.96822, -0.04369, -0.00989], [1.97113, -0.04479, -0.00045], [1.97405, -0.04388, 0.00902], [1.97696, -0.04098, 0.01808], [1.97987, -0.03624, 0.02632], [1.98278, -0.02987, 0.03338], [1.98569, -0.02215, 0.03894], [1.98861, -0.01343, 0.04273], [1.99152, -0.00411, 0.04461], [1.99443, 0.0054, 0.04447], [1.99734, 0.01467, 0.04232], [2.00026, 0.02327, 0.03827], [2.00317, 0.03083, 0.0325], [2.00608, 0.037, 0.02526], [2.00899, 0.04149, 0.01688], [2.0119, 0.04412, 0.00774], [2.01482, 0.04476, -0.00175], [2.01773, 0.04338, -0.01116], [2.02064, 0.04005, -0.02007], [2.02355, 0.03491, -0.02807], [2.02646, 0.0282, -0.03481], [2.02938, 0.02021, -0.03998], [2.03229, 0.01132, -0.04334], [2.0352, 0.00191, -0.04475], [2.03811, -0.00758, -0.04415], [2.04103, -0.01673, -0.04155], [2.04394, -0.02512, -0.03709], [2.04685, -0.03239, -0.03095], [2.04976, -0.03819, -0.02341], [2.05267, -0.04227, -0.01482], [2.05559, -0.04445, -0.00556], [2.0585, -0.04462, 0.00395], [2.06141, -0.04278, 0.01328], [2.06432, -0.03901, 0.02201], [2.06723, -0.03349, 0.02975], [2.07015, -0.02645, 0.03615], [2.07306, -0.01823, 0.04092], [2.07597, -0.00918, 0.04384], [2.07888, 0.00029, 0.04479], [2.0818, 0.00974, 0.04372], [2.08471, 0.01875, 0.04068], [2.08762, 0.02691, 0.03581], [2.09053, 0.03387, 0.02932], [2.09344, 0.03929, 0.02151], [2.09636, 0.04295, 0.01273], [2.09927, 0.04467, 0.00338], [2.10218, 0.04437, -0.00613], [2.10509, 0.04208, -0.01536]], "radius": 0.00115, "radialSegments": 6, "closed": false}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(33, 20, 14, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.78}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "wrap", "mergePolicy": "bake"};
  node_wrap_seam_1_11.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}};
  node_wrap_seam_1_11.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_wrap_seam_1_11);
  nodes["wrap-seam-1"] = node_wrap_seam_1_11;
  const mesh_wrap_seam_1_11Geometry = endpoint_wrap_seam_1_11
    ? new THREE.CylinderGeometry(endpoint_wrap_seam_1_11.endRadius, endpoint_wrap_seam_1_11.baseRadius, endpoint_wrap_seam_1_11.length, 32, 12)
    : buildTubeGeometry({"points": [[1.82552, 0.01536, 0.04208], [1.82844, 0.0239, 0.03789], [1.83135, 0.03136, 0.03199], [1.83426, 0.0374, 0.02465], [1.83717, 0.04176, 0.0162], [1.84009, 0.04424, 0.00701], [1.843, 0.04473, -0.00248], [1.84591, 0.04319, -0.01187], [1.84882, 0.03971, -0.02072], [1.85173, 0.03444, -0.02864], [1.85465, 0.02762, -0.03526], [1.85756, 0.01956, -0.0403], [1.86047, 0.01061, -0.04352], [1.86338, 0.00118, -0.04478], [1.8663, -0.0083, -0.04402], [1.86921, -0.01741, -0.04127], [1.87212, -0.02573, -0.03667], [1.87503, -0.03289, -0.03041], [1.87794, -0.03857, -0.02278], [1.88086, -0.04251, -0.01413], [1.88377, -0.04453, -0.00484], [1.88668, -0.04455, 0.00468], [1.88959, -0.04256, 0.01397], [1.8925, -0.03865, 0.02264], [1.89542, -0.033, 0.03029], [1.89833, -0.02586, 0.03658], [1.90124, -0.01755, 0.04121], [1.90415, -0.00846, 0.04399], [1.90707, 0.00102, 0.04478], [1.90998, 0.01045, 0.04356], [1.91289, 0.01941, 0.04037], [1.9158, 0.0275, 0.03536], [1.91871, 0.03434, 0.02876], [1.92163, 0.03964, 0.02086], [1.92454, 0.04315, 0.01202], [1.92745, 0.04472, 0.00264], [1.93036, 0.04427, -0.00686], [1.93328, 0.04182, -0.01605], [1.93619, 0.03749, -0.02451], [1.9391, 0.03147, -0.03188], [1.94201, 0.02403, -0.0378], [1.94492, 0.01551, -0.04202], [1.94784, 0.00629, -0.04435], [1.95075, -0.00322, -0.04468], [1.95366, -0.01257, -0.04299], [1.95657, -0.02137, -0.03937], [1.95948, -0.0292, -0.03397], [1.9624, -0.03571, -0.02704], [1.96531, -0.04062, -0.01889], [1.96822, -0.04369, -0.00989], [1.97113, -0.04479, -0.00045], [1.97405, -0.04388, 0.00902], [1.97696, -0.04098, 0.01808], [1.97987, -0.03624, 0.02632], [1.98278, -0.02987, 0.03338], [1.98569, -0.02215, 0.03894], [1.98861, -0.01343, 0.04273], [1.99152, -0.00411, 0.04461], [1.99443, 0.0054, 0.04447], [1.99734, 0.01467, 0.04232], [2.00026, 0.02327, 0.03827], [2.00317, 0.03083, 0.0325], [2.00608, 0.037, 0.02526], [2.00899, 0.04149, 0.01688], [2.0119, 0.04412, 0.00774], [2.01482, 0.04476, -0.00175], [2.01773, 0.04338, -0.01116], [2.02064, 0.04005, -0.02007], [2.02355, 0.03491, -0.02807], [2.02646, 0.0282, -0.03481], [2.02938, 0.02021, -0.03998], [2.03229, 0.01132, -0.04334], [2.0352, 0.00191, -0.04475], [2.03811, -0.00758, -0.04415], [2.04103, -0.01673, -0.04155], [2.04394, -0.02512, -0.03709], [2.04685, -0.03239, -0.03095], [2.04976, -0.03819, -0.02341], [2.05267, -0.04227, -0.01482], [2.05559, -0.04445, -0.00556], [2.0585, -0.04462, 0.00395], [2.06141, -0.04278, 0.01328], [2.06432, -0.03901, 0.02201], [2.06723, -0.03349, 0.02975], [2.07015, -0.02645, 0.03615], [2.07306, -0.01823, 0.04092], [2.07597, -0.00918, 0.04384], [2.07888, 0.00029, 0.04479], [2.0818, 0.00974, 0.04372], [2.08471, 0.01875, 0.04068], [2.08762, 0.02691, 0.03581], [2.09053, 0.03387, 0.02932], [2.09344, 0.03929, 0.02151], [2.09636, 0.04295, 0.01273], [2.09927, 0.04467, 0.00338], [2.10218, 0.04437, -0.00613], [2.10509, 0.04208, -0.01536]], "radius": 0.00115, "radialSegments": 6, "closed": false});
  if (!endpoint_wrap_seam_1_11) {
    mesh_wrap_seam_1_11Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_wrap_seam_1_11 = new THREE.Mesh(
    mesh_wrap_seam_1_11Geometry,
    materialMap["wrap-seam"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_wrap_seam_1_11.name = "Cord wrap seam 1";
  if (endpoint_wrap_seam_1_11) {
    mesh_wrap_seam_1_11.position.copy(endpoint_wrap_seam_1_11.midpoint);
    mesh_wrap_seam_1_11.quaternion.copy(endpoint_wrap_seam_1_11.quaternion);
  }
  mesh_wrap_seam_1_11.castShadow = options.castShadow ?? true;
  mesh_wrap_seam_1_11.receiveShadow = options.receiveShadow ?? true;
  mesh_wrap_seam_1_11.userData.sculptComponent = {"id": "wrap-seam-1", "name": "Cord wrap seam 1", "level": "micro", "role": "detail", "importance": 0.7, "confidence": 0.8, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "A shallow helical valley makes the wrapped grip read in face and orbit views.", "geometryDescriptor": {"topologyIntent": "Cord wrap seam 1 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[1.82552, 0.01536, 0.04208], [1.82844, 0.0239, 0.03789], [1.83135, 0.03136, 0.03199], [1.83426, 0.0374, 0.02465], [1.83717, 0.04176, 0.0162], [1.84009, 0.04424, 0.00701], [1.843, 0.04473, -0.00248], [1.84591, 0.04319, -0.01187], [1.84882, 0.03971, -0.02072], [1.85173, 0.03444, -0.02864], [1.85465, 0.02762, -0.03526], [1.85756, 0.01956, -0.0403], [1.86047, 0.01061, -0.04352], [1.86338, 0.00118, -0.04478], [1.8663, -0.0083, -0.04402], [1.86921, -0.01741, -0.04127], [1.87212, -0.02573, -0.03667], [1.87503, -0.03289, -0.03041], [1.87794, -0.03857, -0.02278], [1.88086, -0.04251, -0.01413], [1.88377, -0.04453, -0.00484], [1.88668, -0.04455, 0.00468], [1.88959, -0.04256, 0.01397], [1.8925, -0.03865, 0.02264], [1.89542, -0.033, 0.03029], [1.89833, -0.02586, 0.03658], [1.90124, -0.01755, 0.04121], [1.90415, -0.00846, 0.04399], [1.90707, 0.00102, 0.04478], [1.90998, 0.01045, 0.04356], [1.91289, 0.01941, 0.04037], [1.9158, 0.0275, 0.03536], [1.91871, 0.03434, 0.02876], [1.92163, 0.03964, 0.02086], [1.92454, 0.04315, 0.01202], [1.92745, 0.04472, 0.00264], [1.93036, 0.04427, -0.00686], [1.93328, 0.04182, -0.01605], [1.93619, 0.03749, -0.02451], [1.9391, 0.03147, -0.03188], [1.94201, 0.02403, -0.0378], [1.94492, 0.01551, -0.04202], [1.94784, 0.00629, -0.04435], [1.95075, -0.00322, -0.04468], [1.95366, -0.01257, -0.04299], [1.95657, -0.02137, -0.03937], [1.95948, -0.0292, -0.03397], [1.9624, -0.03571, -0.02704], [1.96531, -0.04062, -0.01889], [1.96822, -0.04369, -0.00989], [1.97113, -0.04479, -0.00045], [1.97405, -0.04388, 0.00902], [1.97696, -0.04098, 0.01808], [1.97987, -0.03624, 0.02632], [1.98278, -0.02987, 0.03338], [1.98569, -0.02215, 0.03894], [1.98861, -0.01343, 0.04273], [1.99152, -0.00411, 0.04461], [1.99443, 0.0054, 0.04447], [1.99734, 0.01467, 0.04232], [2.00026, 0.02327, 0.03827], [2.00317, 0.03083, 0.0325], [2.00608, 0.037, 0.02526], [2.00899, 0.04149, 0.01688], [2.0119, 0.04412, 0.00774], [2.01482, 0.04476, -0.00175], [2.01773, 0.04338, -0.01116], [2.02064, 0.04005, -0.02007], [2.02355, 0.03491, -0.02807], [2.02646, 0.0282, -0.03481], [2.02938, 0.02021, -0.03998], [2.03229, 0.01132, -0.04334], [2.0352, 0.00191, -0.04475], [2.03811, -0.00758, -0.04415], [2.04103, -0.01673, -0.04155], [2.04394, -0.02512, -0.03709], [2.04685, -0.03239, -0.03095], [2.04976, -0.03819, -0.02341], [2.05267, -0.04227, -0.01482], [2.05559, -0.04445, -0.00556], [2.0585, -0.04462, 0.00395], [2.06141, -0.04278, 0.01328], [2.06432, -0.03901, 0.02201], [2.06723, -0.03349, 0.02975], [2.07015, -0.02645, 0.03615], [2.07306, -0.01823, 0.04092], [2.07597, -0.00918, 0.04384], [2.07888, 0.00029, 0.04479], [2.0818, 0.00974, 0.04372], [2.08471, 0.01875, 0.04068], [2.08762, 0.02691, 0.03581], [2.09053, 0.03387, 0.02932], [2.09344, 0.03929, 0.02151], [2.09636, 0.04295, 0.01273], [2.09927, 0.04467, 0.00338], [2.10218, 0.04437, -0.00613], [2.10509, 0.04208, -0.01536]], "radius": 0.00115, "radialSegments": 6, "closed": false}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(33, 20, 14, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.78}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "wrap", "mergePolicy": "bake"};
  mesh_wrap_seam_1_11.userData.explodeWithParent = "handle";
  node_wrap_seam_1_11.add(mesh_wrap_seam_1_11);
  meshes["wrap-seam-1"] = mesh_wrap_seam_1_11;
  colliders["wrap-seam-1"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_wrap_seam_1_11);

  const attachment_wrap_seam_2_12 = {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_wrap_seam_2_12 = makeAttachmentEndpoint(attachment_wrap_seam_2_12);
  const node_wrap_seam_2_12 = new THREE.Group();
  node_wrap_seam_2_12.name = "Cord wrap seam 2__pivot";
  node_wrap_seam_2_12.scale.set(1, 1, 1);
  if (endpoint_wrap_seam_2_12) {
    node_wrap_seam_2_12.position.copy(endpoint_wrap_seam_2_12.start);
    node_wrap_seam_2_12.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_wrap_seam_2_12.position.set(0.0, 0.0, 0.0);
    node_wrap_seam_2_12.rotation.set(0.0, 0.0, 0.0);
  }
  node_wrap_seam_2_12.userData.sculptComponent = {"id": "wrap-seam-2", "name": "Cord wrap seam 2", "level": "micro", "role": "detail", "importance": 0.7, "confidence": 0.8, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "A shallow helical valley makes the wrapped grip read in face and orbit views.", "geometryDescriptor": {"topologyIntent": "Cord wrap seam 2 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[1.82552, -0.01536, 0.04208], [1.82844, -0.0239, 0.03789], [1.83135, -0.03136, 0.03199], [1.83426, -0.0374, 0.02465], [1.83717, -0.04176, 0.0162], [1.84009, -0.04424, 0.00701], [1.843, -0.04473, -0.00248], [1.84591, -0.04319, -0.01187], [1.84882, -0.03971, -0.02072], [1.85173, -0.03444, -0.02864], [1.85465, -0.02762, -0.03526], [1.85756, -0.01956, -0.0403], [1.86047, -0.01061, -0.04352], [1.86338, -0.00118, -0.04478], [1.8663, 0.0083, -0.04402], [1.86921, 0.01741, -0.04127], [1.87212, 0.02573, -0.03667], [1.87503, 0.03289, -0.03041], [1.87794, 0.03857, -0.02278], [1.88086, 0.04251, -0.01413], [1.88377, 0.04453, -0.00484], [1.88668, 0.04455, 0.00468], [1.88959, 0.04256, 0.01397], [1.8925, 0.03865, 0.02264], [1.89542, 0.033, 0.03029], [1.89833, 0.02586, 0.03658], [1.90124, 0.01755, 0.04121], [1.90415, 0.00846, 0.04399], [1.90707, -0.00102, 0.04478], [1.90998, -0.01045, 0.04356], [1.91289, -0.01941, 0.04037], [1.9158, -0.0275, 0.03536], [1.91871, -0.03434, 0.02876], [1.92163, -0.03964, 0.02086], [1.92454, -0.04315, 0.01202], [1.92745, -0.04472, 0.00264], [1.93036, -0.04427, -0.00686], [1.93328, -0.04182, -0.01605], [1.93619, -0.03749, -0.02451], [1.9391, -0.03147, -0.03188], [1.94201, -0.02403, -0.0378], [1.94492, -0.01551, -0.04202], [1.94784, -0.00629, -0.04435], [1.95075, 0.00322, -0.04468], [1.95366, 0.01257, -0.04299], [1.95657, 0.02137, -0.03937], [1.95948, 0.0292, -0.03397], [1.9624, 0.03571, -0.02704], [1.96531, 0.04062, -0.01889], [1.96822, 0.04369, -0.00989], [1.97113, 0.04479, -0.00045], [1.97405, 0.04388, 0.00902], [1.97696, 0.04098, 0.01808], [1.97987, 0.03624, 0.02632], [1.98278, 0.02987, 0.03338], [1.98569, 0.02215, 0.03894], [1.98861, 0.01343, 0.04273], [1.99152, 0.00411, 0.04461], [1.99443, -0.0054, 0.04447], [1.99734, -0.01467, 0.04232], [2.00026, -0.02327, 0.03827], [2.00317, -0.03083, 0.0325], [2.00608, -0.037, 0.02526], [2.00899, -0.04149, 0.01688], [2.0119, -0.04412, 0.00774], [2.01482, -0.04476, -0.00175], [2.01773, -0.04338, -0.01116], [2.02064, -0.04005, -0.02007], [2.02355, -0.03491, -0.02807], [2.02646, -0.0282, -0.03481], [2.02938, -0.02021, -0.03998], [2.03229, -0.01132, -0.04334], [2.0352, -0.00191, -0.04475], [2.03811, 0.00758, -0.04415], [2.04103, 0.01673, -0.04155], [2.04394, 0.02512, -0.03709], [2.04685, 0.03239, -0.03095], [2.04976, 0.03819, -0.02341], [2.05267, 0.04227, -0.01482], [2.05559, 0.04445, -0.00556], [2.0585, 0.04462, 0.00395], [2.06141, 0.04278, 0.01328], [2.06432, 0.03901, 0.02201], [2.06723, 0.03349, 0.02975], [2.07015, 0.02645, 0.03615], [2.07306, 0.01823, 0.04092], [2.07597, 0.00918, 0.04384], [2.07888, -0.00029, 0.04479], [2.0818, -0.00974, 0.04372], [2.08471, -0.01875, 0.04068], [2.08762, -0.02691, 0.03581], [2.09053, -0.03387, 0.02932], [2.09344, -0.03929, 0.02151], [2.09636, -0.04295, 0.01273], [2.09927, -0.04467, 0.00338], [2.10218, -0.04437, -0.00613], [2.10509, -0.04208, -0.01536]], "radius": 0.00115, "radialSegments": 6, "closed": false}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(33, 20, 14, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.78}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "wrap", "mergePolicy": "bake"};
  node_wrap_seam_2_12.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}};
  node_wrap_seam_2_12.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_wrap_seam_2_12);
  nodes["wrap-seam-2"] = node_wrap_seam_2_12;
  const mesh_wrap_seam_2_12Geometry = endpoint_wrap_seam_2_12
    ? new THREE.CylinderGeometry(endpoint_wrap_seam_2_12.endRadius, endpoint_wrap_seam_2_12.baseRadius, endpoint_wrap_seam_2_12.length, 32, 12)
    : buildTubeGeometry({"points": [[1.82552, -0.01536, 0.04208], [1.82844, -0.0239, 0.03789], [1.83135, -0.03136, 0.03199], [1.83426, -0.0374, 0.02465], [1.83717, -0.04176, 0.0162], [1.84009, -0.04424, 0.00701], [1.843, -0.04473, -0.00248], [1.84591, -0.04319, -0.01187], [1.84882, -0.03971, -0.02072], [1.85173, -0.03444, -0.02864], [1.85465, -0.02762, -0.03526], [1.85756, -0.01956, -0.0403], [1.86047, -0.01061, -0.04352], [1.86338, -0.00118, -0.04478], [1.8663, 0.0083, -0.04402], [1.86921, 0.01741, -0.04127], [1.87212, 0.02573, -0.03667], [1.87503, 0.03289, -0.03041], [1.87794, 0.03857, -0.02278], [1.88086, 0.04251, -0.01413], [1.88377, 0.04453, -0.00484], [1.88668, 0.04455, 0.00468], [1.88959, 0.04256, 0.01397], [1.8925, 0.03865, 0.02264], [1.89542, 0.033, 0.03029], [1.89833, 0.02586, 0.03658], [1.90124, 0.01755, 0.04121], [1.90415, 0.00846, 0.04399], [1.90707, -0.00102, 0.04478], [1.90998, -0.01045, 0.04356], [1.91289, -0.01941, 0.04037], [1.9158, -0.0275, 0.03536], [1.91871, -0.03434, 0.02876], [1.92163, -0.03964, 0.02086], [1.92454, -0.04315, 0.01202], [1.92745, -0.04472, 0.00264], [1.93036, -0.04427, -0.00686], [1.93328, -0.04182, -0.01605], [1.93619, -0.03749, -0.02451], [1.9391, -0.03147, -0.03188], [1.94201, -0.02403, -0.0378], [1.94492, -0.01551, -0.04202], [1.94784, -0.00629, -0.04435], [1.95075, 0.00322, -0.04468], [1.95366, 0.01257, -0.04299], [1.95657, 0.02137, -0.03937], [1.95948, 0.0292, -0.03397], [1.9624, 0.03571, -0.02704], [1.96531, 0.04062, -0.01889], [1.96822, 0.04369, -0.00989], [1.97113, 0.04479, -0.00045], [1.97405, 0.04388, 0.00902], [1.97696, 0.04098, 0.01808], [1.97987, 0.03624, 0.02632], [1.98278, 0.02987, 0.03338], [1.98569, 0.02215, 0.03894], [1.98861, 0.01343, 0.04273], [1.99152, 0.00411, 0.04461], [1.99443, -0.0054, 0.04447], [1.99734, -0.01467, 0.04232], [2.00026, -0.02327, 0.03827], [2.00317, -0.03083, 0.0325], [2.00608, -0.037, 0.02526], [2.00899, -0.04149, 0.01688], [2.0119, -0.04412, 0.00774], [2.01482, -0.04476, -0.00175], [2.01773, -0.04338, -0.01116], [2.02064, -0.04005, -0.02007], [2.02355, -0.03491, -0.02807], [2.02646, -0.0282, -0.03481], [2.02938, -0.02021, -0.03998], [2.03229, -0.01132, -0.04334], [2.0352, -0.00191, -0.04475], [2.03811, 0.00758, -0.04415], [2.04103, 0.01673, -0.04155], [2.04394, 0.02512, -0.03709], [2.04685, 0.03239, -0.03095], [2.04976, 0.03819, -0.02341], [2.05267, 0.04227, -0.01482], [2.05559, 0.04445, -0.00556], [2.0585, 0.04462, 0.00395], [2.06141, 0.04278, 0.01328], [2.06432, 0.03901, 0.02201], [2.06723, 0.03349, 0.02975], [2.07015, 0.02645, 0.03615], [2.07306, 0.01823, 0.04092], [2.07597, 0.00918, 0.04384], [2.07888, -0.00029, 0.04479], [2.0818, -0.00974, 0.04372], [2.08471, -0.01875, 0.04068], [2.08762, -0.02691, 0.03581], [2.09053, -0.03387, 0.02932], [2.09344, -0.03929, 0.02151], [2.09636, -0.04295, 0.01273], [2.09927, -0.04467, 0.00338], [2.10218, -0.04437, -0.00613], [2.10509, -0.04208, -0.01536]], "radius": 0.00115, "radialSegments": 6, "closed": false});
  if (!endpoint_wrap_seam_2_12) {
    mesh_wrap_seam_2_12Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_wrap_seam_2_12 = new THREE.Mesh(
    mesh_wrap_seam_2_12Geometry,
    materialMap["wrap-seam"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_wrap_seam_2_12.name = "Cord wrap seam 2";
  if (endpoint_wrap_seam_2_12) {
    mesh_wrap_seam_2_12.position.copy(endpoint_wrap_seam_2_12.midpoint);
    mesh_wrap_seam_2_12.quaternion.copy(endpoint_wrap_seam_2_12.quaternion);
  }
  mesh_wrap_seam_2_12.castShadow = options.castShadow ?? true;
  mesh_wrap_seam_2_12.receiveShadow = options.receiveShadow ?? true;
  mesh_wrap_seam_2_12.userData.sculptComponent = {"id": "wrap-seam-2", "name": "Cord wrap seam 2", "level": "micro", "role": "detail", "importance": 0.7, "confidence": 0.8, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "A shallow helical valley makes the wrapped grip read in face and orbit views.", "geometryDescriptor": {"topologyIntent": "Cord wrap seam 2 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[1.82552, -0.01536, 0.04208], [1.82844, -0.0239, 0.03789], [1.83135, -0.03136, 0.03199], [1.83426, -0.0374, 0.02465], [1.83717, -0.04176, 0.0162], [1.84009, -0.04424, 0.00701], [1.843, -0.04473, -0.00248], [1.84591, -0.04319, -0.01187], [1.84882, -0.03971, -0.02072], [1.85173, -0.03444, -0.02864], [1.85465, -0.02762, -0.03526], [1.85756, -0.01956, -0.0403], [1.86047, -0.01061, -0.04352], [1.86338, -0.00118, -0.04478], [1.8663, 0.0083, -0.04402], [1.86921, 0.01741, -0.04127], [1.87212, 0.02573, -0.03667], [1.87503, 0.03289, -0.03041], [1.87794, 0.03857, -0.02278], [1.88086, 0.04251, -0.01413], [1.88377, 0.04453, -0.00484], [1.88668, 0.04455, 0.00468], [1.88959, 0.04256, 0.01397], [1.8925, 0.03865, 0.02264], [1.89542, 0.033, 0.03029], [1.89833, 0.02586, 0.03658], [1.90124, 0.01755, 0.04121], [1.90415, 0.00846, 0.04399], [1.90707, -0.00102, 0.04478], [1.90998, -0.01045, 0.04356], [1.91289, -0.01941, 0.04037], [1.9158, -0.0275, 0.03536], [1.91871, -0.03434, 0.02876], [1.92163, -0.03964, 0.02086], [1.92454, -0.04315, 0.01202], [1.92745, -0.04472, 0.00264], [1.93036, -0.04427, -0.00686], [1.93328, -0.04182, -0.01605], [1.93619, -0.03749, -0.02451], [1.9391, -0.03147, -0.03188], [1.94201, -0.02403, -0.0378], [1.94492, -0.01551, -0.04202], [1.94784, -0.00629, -0.04435], [1.95075, 0.00322, -0.04468], [1.95366, 0.01257, -0.04299], [1.95657, 0.02137, -0.03937], [1.95948, 0.0292, -0.03397], [1.9624, 0.03571, -0.02704], [1.96531, 0.04062, -0.01889], [1.96822, 0.04369, -0.00989], [1.97113, 0.04479, -0.00045], [1.97405, 0.04388, 0.00902], [1.97696, 0.04098, 0.01808], [1.97987, 0.03624, 0.02632], [1.98278, 0.02987, 0.03338], [1.98569, 0.02215, 0.03894], [1.98861, 0.01343, 0.04273], [1.99152, 0.00411, 0.04461], [1.99443, -0.0054, 0.04447], [1.99734, -0.01467, 0.04232], [2.00026, -0.02327, 0.03827], [2.00317, -0.03083, 0.0325], [2.00608, -0.037, 0.02526], [2.00899, -0.04149, 0.01688], [2.0119, -0.04412, 0.00774], [2.01482, -0.04476, -0.00175], [2.01773, -0.04338, -0.01116], [2.02064, -0.04005, -0.02007], [2.02355, -0.03491, -0.02807], [2.02646, -0.0282, -0.03481], [2.02938, -0.02021, -0.03998], [2.03229, -0.01132, -0.04334], [2.0352, -0.00191, -0.04475], [2.03811, 0.00758, -0.04415], [2.04103, 0.01673, -0.04155], [2.04394, 0.02512, -0.03709], [2.04685, 0.03239, -0.03095], [2.04976, 0.03819, -0.02341], [2.05267, 0.04227, -0.01482], [2.05559, 0.04445, -0.00556], [2.0585, 0.04462, 0.00395], [2.06141, 0.04278, 0.01328], [2.06432, 0.03901, 0.02201], [2.06723, 0.03349, 0.02975], [2.07015, 0.02645, 0.03615], [2.07306, 0.01823, 0.04092], [2.07597, 0.00918, 0.04384], [2.07888, -0.00029, 0.04479], [2.0818, -0.00974, 0.04372], [2.08471, -0.01875, 0.04068], [2.08762, -0.02691, 0.03581], [2.09053, -0.03387, 0.02932], [2.09344, -0.03929, 0.02151], [2.09636, -0.04295, 0.01273], [2.09927, -0.04467, 0.00338], [2.10218, -0.04437, -0.00613], [2.10509, -0.04208, -0.01536]], "radius": 0.00115, "radialSegments": 6, "closed": false}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "blade-heel", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(33, 20, 14, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.78}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "wrap", "mergePolicy": "bake"};
  mesh_wrap_seam_2_12.userData.explodeWithParent = "handle";
  node_wrap_seam_2_12.add(mesh_wrap_seam_2_12);
  meshes["wrap-seam-2"] = mesh_wrap_seam_2_12;
  colliders["wrap-seam-2"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_wrap_seam_2_12);

  const endpoint_stud_seat_a_13 = makeAttachmentEndpoint(null);
  const node_stud_seat_a_13 = new THREE.Group();
  node_stud_seat_a_13.name = "Diamond inlay seat 1__pivot";
  node_stud_seat_a_13.scale.set(1, 1, 1);
  if (endpoint_stud_seat_a_13) {
    node_stud_seat_a_13.position.copy(endpoint_stud_seat_a_13.start);
    node_stud_seat_a_13.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_seat_a_13.position.set(1.8611773921832884, 0.0, 0.04558042452830188);
    node_stud_seat_a_13.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_seat_a_13.userData.sculptComponent = {"id": "stud-seat-a", "name": "Diamond inlay seat 1", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the front gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond inlay seat 1 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [1.8611773921832884, 0.0, 0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "bake"};
  node_stud_seat_a_13.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}};
  node_stud_seat_a_13.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_seat_a_13);
  nodes["stud-seat-a"] = node_stud_seat_a_13;
  const mesh_stud_seat_a_13Geometry = endpoint_stud_seat_a_13
    ? new THREE.CylinderGeometry(endpoint_stud_seat_a_13.endRadius, endpoint_stud_seat_a_13.baseRadius, endpoint_stud_seat_a_13.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_seat_a_13) {
    mesh_stud_seat_a_13Geometry.scale(0.029, 0.021, 0.001);
  }
  const mesh_stud_seat_a_13 = new THREE.Mesh(
    mesh_stud_seat_a_13Geometry,
    materialMap["wrap-seam"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_seat_a_13.name = "Diamond inlay seat 1";
  if (endpoint_stud_seat_a_13) {
    mesh_stud_seat_a_13.position.copy(endpoint_stud_seat_a_13.midpoint);
    mesh_stud_seat_a_13.quaternion.copy(endpoint_stud_seat_a_13.quaternion);
  }
  mesh_stud_seat_a_13.castShadow = options.castShadow ?? true;
  mesh_stud_seat_a_13.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_seat_a_13.userData.sculptComponent = {"id": "stud-seat-a", "name": "Diamond inlay seat 1", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the front gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond inlay seat 1 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [1.8611773921832884, 0.0, 0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "bake"};
  mesh_stud_seat_a_13.userData.explodeWithParent = "handle";
  node_stud_seat_a_13.add(mesh_stud_seat_a_13);
  meshes["stud-seat-a"] = mesh_stud_seat_a_13;
  colliders["stud-seat-a"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_seat_a_13);

  const endpoint_stud_seat_b_14 = makeAttachmentEndpoint(null);
  const node_stud_seat_b_14 = new THREE.Group();
  node_stud_seat_b_14.name = "Diamond inlay seat 2__pivot";
  node_stud_seat_b_14.scale.set(1, 1, 1);
  if (endpoint_stud_seat_b_14) {
    node_stud_seat_b_14.position.copy(endpoint_stud_seat_b_14.start);
    node_stud_seat_b_14.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_seat_b_14.position.set(1.9028300202156334, 0.0, 0.04558042452830188);
    node_stud_seat_b_14.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_seat_b_14.userData.sculptComponent = {"id": "stud-seat-b", "name": "Diamond inlay seat 2", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the front gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond inlay seat 2 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [1.9028300202156334, 0.0, 0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "bake"};
  node_stud_seat_b_14.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}};
  node_stud_seat_b_14.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_seat_b_14);
  nodes["stud-seat-b"] = node_stud_seat_b_14;
  const mesh_stud_seat_b_14Geometry = endpoint_stud_seat_b_14
    ? new THREE.CylinderGeometry(endpoint_stud_seat_b_14.endRadius, endpoint_stud_seat_b_14.baseRadius, endpoint_stud_seat_b_14.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_seat_b_14) {
    mesh_stud_seat_b_14Geometry.scale(0.029, 0.021, 0.001);
  }
  const mesh_stud_seat_b_14 = new THREE.Mesh(
    mesh_stud_seat_b_14Geometry,
    materialMap["wrap-seam"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_seat_b_14.name = "Diamond inlay seat 2";
  if (endpoint_stud_seat_b_14) {
    mesh_stud_seat_b_14.position.copy(endpoint_stud_seat_b_14.midpoint);
    mesh_stud_seat_b_14.quaternion.copy(endpoint_stud_seat_b_14.quaternion);
  }
  mesh_stud_seat_b_14.castShadow = options.castShadow ?? true;
  mesh_stud_seat_b_14.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_seat_b_14.userData.sculptComponent = {"id": "stud-seat-b", "name": "Diamond inlay seat 2", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the front gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond inlay seat 2 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [1.9028300202156334, 0.0, 0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "bake"};
  mesh_stud_seat_b_14.userData.explodeWithParent = "handle";
  node_stud_seat_b_14.add(mesh_stud_seat_b_14);
  meshes["stud-seat-b"] = mesh_stud_seat_b_14;
  colliders["stud-seat-b"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_seat_b_14);

  const endpoint_stud_seat_c_15 = makeAttachmentEndpoint(null);
  const node_stud_seat_c_15 = new THREE.Group();
  node_stud_seat_c_15.name = "Diamond inlay seat 3__pivot";
  node_stud_seat_c_15.scale.set(1, 1, 1);
  if (endpoint_stud_seat_c_15) {
    node_stud_seat_c_15.position.copy(endpoint_stud_seat_c_15.start);
    node_stud_seat_c_15.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_seat_c_15.position.set(1.9444826482479782, 0.0, 0.04558042452830188);
    node_stud_seat_c_15.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_seat_c_15.userData.sculptComponent = {"id": "stud-seat-c", "name": "Diamond inlay seat 3", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the front gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond inlay seat 3 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [1.9444826482479782, 0.0, 0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "bake"};
  node_stud_seat_c_15.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}};
  node_stud_seat_c_15.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_seat_c_15);
  nodes["stud-seat-c"] = node_stud_seat_c_15;
  const mesh_stud_seat_c_15Geometry = endpoint_stud_seat_c_15
    ? new THREE.CylinderGeometry(endpoint_stud_seat_c_15.endRadius, endpoint_stud_seat_c_15.baseRadius, endpoint_stud_seat_c_15.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_seat_c_15) {
    mesh_stud_seat_c_15Geometry.scale(0.029, 0.021, 0.001);
  }
  const mesh_stud_seat_c_15 = new THREE.Mesh(
    mesh_stud_seat_c_15Geometry,
    materialMap["wrap-seam"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_seat_c_15.name = "Diamond inlay seat 3";
  if (endpoint_stud_seat_c_15) {
    mesh_stud_seat_c_15.position.copy(endpoint_stud_seat_c_15.midpoint);
    mesh_stud_seat_c_15.quaternion.copy(endpoint_stud_seat_c_15.quaternion);
  }
  mesh_stud_seat_c_15.castShadow = options.castShadow ?? true;
  mesh_stud_seat_c_15.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_seat_c_15.userData.sculptComponent = {"id": "stud-seat-c", "name": "Diamond inlay seat 3", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the front gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond inlay seat 3 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [1.9444826482479782, 0.0, 0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "bake"};
  mesh_stud_seat_c_15.userData.explodeWithParent = "handle";
  node_stud_seat_c_15.add(mesh_stud_seat_c_15);
  meshes["stud-seat-c"] = mesh_stud_seat_c_15;
  colliders["stud-seat-c"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_seat_c_15);

  const endpoint_stud_seat_d_16 = makeAttachmentEndpoint(null);
  const node_stud_seat_d_16 = new THREE.Group();
  node_stud_seat_d_16.name = "Diamond inlay seat 4__pivot";
  node_stud_seat_d_16.scale.set(1, 1, 1);
  if (endpoint_stud_seat_d_16) {
    node_stud_seat_d_16.position.copy(endpoint_stud_seat_d_16.start);
    node_stud_seat_d_16.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_seat_d_16.position.set(1.9861352762803233, 0.0, 0.04558042452830188);
    node_stud_seat_d_16.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_seat_d_16.userData.sculptComponent = {"id": "stud-seat-d", "name": "Diamond inlay seat 4", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the front gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond inlay seat 4 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [1.9861352762803233, 0.0, 0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "bake"};
  node_stud_seat_d_16.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}};
  node_stud_seat_d_16.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_seat_d_16);
  nodes["stud-seat-d"] = node_stud_seat_d_16;
  const mesh_stud_seat_d_16Geometry = endpoint_stud_seat_d_16
    ? new THREE.CylinderGeometry(endpoint_stud_seat_d_16.endRadius, endpoint_stud_seat_d_16.baseRadius, endpoint_stud_seat_d_16.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_seat_d_16) {
    mesh_stud_seat_d_16Geometry.scale(0.029, 0.021, 0.001);
  }
  const mesh_stud_seat_d_16 = new THREE.Mesh(
    mesh_stud_seat_d_16Geometry,
    materialMap["wrap-seam"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_seat_d_16.name = "Diamond inlay seat 4";
  if (endpoint_stud_seat_d_16) {
    mesh_stud_seat_d_16.position.copy(endpoint_stud_seat_d_16.midpoint);
    mesh_stud_seat_d_16.quaternion.copy(endpoint_stud_seat_d_16.quaternion);
  }
  mesh_stud_seat_d_16.castShadow = options.castShadow ?? true;
  mesh_stud_seat_d_16.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_seat_d_16.userData.sculptComponent = {"id": "stud-seat-d", "name": "Diamond inlay seat 4", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the front gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond inlay seat 4 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [1.9861352762803233, 0.0, 0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "bake"};
  mesh_stud_seat_d_16.userData.explodeWithParent = "handle";
  node_stud_seat_d_16.add(mesh_stud_seat_d_16);
  meshes["stud-seat-d"] = mesh_stud_seat_d_16;
  colliders["stud-seat-d"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_seat_d_16);

  const endpoint_stud_seat_e_17 = makeAttachmentEndpoint(null);
  const node_stud_seat_e_17 = new THREE.Group();
  node_stud_seat_e_17.name = "Diamond inlay seat 5__pivot";
  node_stud_seat_e_17.scale.set(1, 1, 1);
  if (endpoint_stud_seat_e_17) {
    node_stud_seat_e_17.position.copy(endpoint_stud_seat_e_17.start);
    node_stud_seat_e_17.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_seat_e_17.position.set(2.0277879043126683, 0.0, 0.04558042452830188);
    node_stud_seat_e_17.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_seat_e_17.userData.sculptComponent = {"id": "stud-seat-e", "name": "Diamond inlay seat 5", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the front gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond inlay seat 5 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [2.0277879043126683, 0.0, 0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "bake"};
  node_stud_seat_e_17.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}};
  node_stud_seat_e_17.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_seat_e_17);
  nodes["stud-seat-e"] = node_stud_seat_e_17;
  const mesh_stud_seat_e_17Geometry = endpoint_stud_seat_e_17
    ? new THREE.CylinderGeometry(endpoint_stud_seat_e_17.endRadius, endpoint_stud_seat_e_17.baseRadius, endpoint_stud_seat_e_17.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_seat_e_17) {
    mesh_stud_seat_e_17Geometry.scale(0.029, 0.021, 0.001);
  }
  const mesh_stud_seat_e_17 = new THREE.Mesh(
    mesh_stud_seat_e_17Geometry,
    materialMap["wrap-seam"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_seat_e_17.name = "Diamond inlay seat 5";
  if (endpoint_stud_seat_e_17) {
    mesh_stud_seat_e_17.position.copy(endpoint_stud_seat_e_17.midpoint);
    mesh_stud_seat_e_17.quaternion.copy(endpoint_stud_seat_e_17.quaternion);
  }
  mesh_stud_seat_e_17.castShadow = options.castShadow ?? true;
  mesh_stud_seat_e_17.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_seat_e_17.userData.sculptComponent = {"id": "stud-seat-e", "name": "Diamond inlay seat 5", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the front gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond inlay seat 5 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [2.0277879043126683, 0.0, 0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "bake"};
  mesh_stud_seat_e_17.userData.explodeWithParent = "handle";
  node_stud_seat_e_17.add(mesh_stud_seat_e_17);
  meshes["stud-seat-e"] = mesh_stud_seat_e_17;
  colliders["stud-seat-e"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_seat_e_17);

  const endpoint_stud_seat_f_18 = makeAttachmentEndpoint(null);
  const node_stud_seat_f_18 = new THREE.Group();
  node_stud_seat_f_18.name = "Diamond inlay seat 6__pivot";
  node_stud_seat_f_18.scale.set(1, 1, 1);
  if (endpoint_stud_seat_f_18) {
    node_stud_seat_f_18.position.copy(endpoint_stud_seat_f_18.start);
    node_stud_seat_f_18.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_seat_f_18.position.set(2.0694405323450136, 0.0, 0.04558042452830188);
    node_stud_seat_f_18.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_seat_f_18.userData.sculptComponent = {"id": "stud-seat-f", "name": "Diamond inlay seat 6", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the front gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond inlay seat 6 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [2.0694405323450136, 0.0, 0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "bake"};
  node_stud_seat_f_18.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}};
  node_stud_seat_f_18.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_seat_f_18);
  nodes["stud-seat-f"] = node_stud_seat_f_18;
  const mesh_stud_seat_f_18Geometry = endpoint_stud_seat_f_18
    ? new THREE.CylinderGeometry(endpoint_stud_seat_f_18.endRadius, endpoint_stud_seat_f_18.baseRadius, endpoint_stud_seat_f_18.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_seat_f_18) {
    mesh_stud_seat_f_18Geometry.scale(0.029, 0.021, 0.001);
  }
  const mesh_stud_seat_f_18 = new THREE.Mesh(
    mesh_stud_seat_f_18Geometry,
    materialMap["wrap-seam"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_seat_f_18.name = "Diamond inlay seat 6";
  if (endpoint_stud_seat_f_18) {
    mesh_stud_seat_f_18.position.copy(endpoint_stud_seat_f_18.midpoint);
    mesh_stud_seat_f_18.quaternion.copy(endpoint_stud_seat_f_18.quaternion);
  }
  mesh_stud_seat_f_18.castShadow = options.castShadow ?? true;
  mesh_stud_seat_f_18.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_seat_f_18.userData.sculptComponent = {"id": "stud-seat-f", "name": "Diamond inlay seat 6", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the front gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond inlay seat 6 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [2.0694405323450136, 0.0, 0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "bake"};
  mesh_stud_seat_f_18.userData.explodeWithParent = "handle";
  node_stud_seat_f_18.add(mesh_stud_seat_f_18);
  meshes["stud-seat-f"] = mesh_stud_seat_f_18;
  colliders["stud-seat-f"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_seat_f_18);

  const endpoint_stud_seat_back_a_19 = makeAttachmentEndpoint(null);
  const node_stud_seat_back_a_19 = new THREE.Group();
  node_stud_seat_back_a_19.name = "Diamond back inlay seat 1__pivot";
  node_stud_seat_back_a_19.scale.set(1, 1, 1);
  if (endpoint_stud_seat_back_a_19) {
    node_stud_seat_back_a_19.position.copy(endpoint_stud_seat_back_a_19.start);
    node_stud_seat_back_a_19.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_seat_back_a_19.position.set(1.8611773921832884, 0.0, -0.04558042452830188);
    node_stud_seat_back_a_19.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_seat_back_a_19.userData.sculptComponent = {"id": "stud-seat-back-a", "name": "Diamond back inlay seat 1", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the back gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay seat 1 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [1.8611773921832884, 0.0, -0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "bake"};
  node_stud_seat_back_a_19.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}};
  node_stud_seat_back_a_19.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_seat_back_a_19);
  nodes["stud-seat-back-a"] = node_stud_seat_back_a_19;
  const mesh_stud_seat_back_a_19Geometry = endpoint_stud_seat_back_a_19
    ? new THREE.CylinderGeometry(endpoint_stud_seat_back_a_19.endRadius, endpoint_stud_seat_back_a_19.baseRadius, endpoint_stud_seat_back_a_19.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_seat_back_a_19) {
    mesh_stud_seat_back_a_19Geometry.scale(0.029, 0.021, 0.001);
  }
  const mesh_stud_seat_back_a_19 = new THREE.Mesh(
    mesh_stud_seat_back_a_19Geometry,
    materialMap["wrap-seam"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_seat_back_a_19.name = "Diamond back inlay seat 1";
  if (endpoint_stud_seat_back_a_19) {
    mesh_stud_seat_back_a_19.position.copy(endpoint_stud_seat_back_a_19.midpoint);
    mesh_stud_seat_back_a_19.quaternion.copy(endpoint_stud_seat_back_a_19.quaternion);
  }
  mesh_stud_seat_back_a_19.castShadow = options.castShadow ?? true;
  mesh_stud_seat_back_a_19.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_seat_back_a_19.userData.sculptComponent = {"id": "stud-seat-back-a", "name": "Diamond back inlay seat 1", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the back gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay seat 1 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [1.8611773921832884, 0.0, -0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "bake"};
  mesh_stud_seat_back_a_19.userData.explodeWithParent = "handle";
  node_stud_seat_back_a_19.add(mesh_stud_seat_back_a_19);
  meshes["stud-seat-back-a"] = mesh_stud_seat_back_a_19;
  colliders["stud-seat-back-a"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_seat_back_a_19);

  const endpoint_stud_seat_back_b_20 = makeAttachmentEndpoint(null);
  const node_stud_seat_back_b_20 = new THREE.Group();
  node_stud_seat_back_b_20.name = "Diamond back inlay seat 2__pivot";
  node_stud_seat_back_b_20.scale.set(1, 1, 1);
  if (endpoint_stud_seat_back_b_20) {
    node_stud_seat_back_b_20.position.copy(endpoint_stud_seat_back_b_20.start);
    node_stud_seat_back_b_20.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_seat_back_b_20.position.set(1.9028300202156334, 0.0, -0.04558042452830188);
    node_stud_seat_back_b_20.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_seat_back_b_20.userData.sculptComponent = {"id": "stud-seat-back-b", "name": "Diamond back inlay seat 2", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the back gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay seat 2 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [1.9028300202156334, 0.0, -0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "bake"};
  node_stud_seat_back_b_20.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}};
  node_stud_seat_back_b_20.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_seat_back_b_20);
  nodes["stud-seat-back-b"] = node_stud_seat_back_b_20;
  const mesh_stud_seat_back_b_20Geometry = endpoint_stud_seat_back_b_20
    ? new THREE.CylinderGeometry(endpoint_stud_seat_back_b_20.endRadius, endpoint_stud_seat_back_b_20.baseRadius, endpoint_stud_seat_back_b_20.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_seat_back_b_20) {
    mesh_stud_seat_back_b_20Geometry.scale(0.029, 0.021, 0.001);
  }
  const mesh_stud_seat_back_b_20 = new THREE.Mesh(
    mesh_stud_seat_back_b_20Geometry,
    materialMap["wrap-seam"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_seat_back_b_20.name = "Diamond back inlay seat 2";
  if (endpoint_stud_seat_back_b_20) {
    mesh_stud_seat_back_b_20.position.copy(endpoint_stud_seat_back_b_20.midpoint);
    mesh_stud_seat_back_b_20.quaternion.copy(endpoint_stud_seat_back_b_20.quaternion);
  }
  mesh_stud_seat_back_b_20.castShadow = options.castShadow ?? true;
  mesh_stud_seat_back_b_20.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_seat_back_b_20.userData.sculptComponent = {"id": "stud-seat-back-b", "name": "Diamond back inlay seat 2", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the back gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay seat 2 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [1.9028300202156334, 0.0, -0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "bake"};
  mesh_stud_seat_back_b_20.userData.explodeWithParent = "handle";
  node_stud_seat_back_b_20.add(mesh_stud_seat_back_b_20);
  meshes["stud-seat-back-b"] = mesh_stud_seat_back_b_20;
  colliders["stud-seat-back-b"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_seat_back_b_20);

  const endpoint_stud_seat_back_c_21 = makeAttachmentEndpoint(null);
  const node_stud_seat_back_c_21 = new THREE.Group();
  node_stud_seat_back_c_21.name = "Diamond back inlay seat 3__pivot";
  node_stud_seat_back_c_21.scale.set(1, 1, 1);
  if (endpoint_stud_seat_back_c_21) {
    node_stud_seat_back_c_21.position.copy(endpoint_stud_seat_back_c_21.start);
    node_stud_seat_back_c_21.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_seat_back_c_21.position.set(1.9444826482479782, 0.0, -0.04558042452830188);
    node_stud_seat_back_c_21.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_seat_back_c_21.userData.sculptComponent = {"id": "stud-seat-back-c", "name": "Diamond back inlay seat 3", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the back gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay seat 3 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [1.9444826482479782, 0.0, -0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "bake"};
  node_stud_seat_back_c_21.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}};
  node_stud_seat_back_c_21.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_seat_back_c_21);
  nodes["stud-seat-back-c"] = node_stud_seat_back_c_21;
  const mesh_stud_seat_back_c_21Geometry = endpoint_stud_seat_back_c_21
    ? new THREE.CylinderGeometry(endpoint_stud_seat_back_c_21.endRadius, endpoint_stud_seat_back_c_21.baseRadius, endpoint_stud_seat_back_c_21.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_seat_back_c_21) {
    mesh_stud_seat_back_c_21Geometry.scale(0.029, 0.021, 0.001);
  }
  const mesh_stud_seat_back_c_21 = new THREE.Mesh(
    mesh_stud_seat_back_c_21Geometry,
    materialMap["wrap-seam"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_seat_back_c_21.name = "Diamond back inlay seat 3";
  if (endpoint_stud_seat_back_c_21) {
    mesh_stud_seat_back_c_21.position.copy(endpoint_stud_seat_back_c_21.midpoint);
    mesh_stud_seat_back_c_21.quaternion.copy(endpoint_stud_seat_back_c_21.quaternion);
  }
  mesh_stud_seat_back_c_21.castShadow = options.castShadow ?? true;
  mesh_stud_seat_back_c_21.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_seat_back_c_21.userData.sculptComponent = {"id": "stud-seat-back-c", "name": "Diamond back inlay seat 3", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the back gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay seat 3 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [1.9444826482479782, 0.0, -0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "bake"};
  mesh_stud_seat_back_c_21.userData.explodeWithParent = "handle";
  node_stud_seat_back_c_21.add(mesh_stud_seat_back_c_21);
  meshes["stud-seat-back-c"] = mesh_stud_seat_back_c_21;
  colliders["stud-seat-back-c"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_seat_back_c_21);

  const endpoint_stud_seat_back_d_22 = makeAttachmentEndpoint(null);
  const node_stud_seat_back_d_22 = new THREE.Group();
  node_stud_seat_back_d_22.name = "Diamond back inlay seat 4__pivot";
  node_stud_seat_back_d_22.scale.set(1, 1, 1);
  if (endpoint_stud_seat_back_d_22) {
    node_stud_seat_back_d_22.position.copy(endpoint_stud_seat_back_d_22.start);
    node_stud_seat_back_d_22.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_seat_back_d_22.position.set(1.9861352762803233, 0.0, -0.04558042452830188);
    node_stud_seat_back_d_22.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_seat_back_d_22.userData.sculptComponent = {"id": "stud-seat-back-d", "name": "Diamond back inlay seat 4", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the back gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay seat 4 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [1.9861352762803233, 0.0, -0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "bake"};
  node_stud_seat_back_d_22.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}};
  node_stud_seat_back_d_22.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_seat_back_d_22);
  nodes["stud-seat-back-d"] = node_stud_seat_back_d_22;
  const mesh_stud_seat_back_d_22Geometry = endpoint_stud_seat_back_d_22
    ? new THREE.CylinderGeometry(endpoint_stud_seat_back_d_22.endRadius, endpoint_stud_seat_back_d_22.baseRadius, endpoint_stud_seat_back_d_22.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_seat_back_d_22) {
    mesh_stud_seat_back_d_22Geometry.scale(0.029, 0.021, 0.001);
  }
  const mesh_stud_seat_back_d_22 = new THREE.Mesh(
    mesh_stud_seat_back_d_22Geometry,
    materialMap["wrap-seam"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_seat_back_d_22.name = "Diamond back inlay seat 4";
  if (endpoint_stud_seat_back_d_22) {
    mesh_stud_seat_back_d_22.position.copy(endpoint_stud_seat_back_d_22.midpoint);
    mesh_stud_seat_back_d_22.quaternion.copy(endpoint_stud_seat_back_d_22.quaternion);
  }
  mesh_stud_seat_back_d_22.castShadow = options.castShadow ?? true;
  mesh_stud_seat_back_d_22.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_seat_back_d_22.userData.sculptComponent = {"id": "stud-seat-back-d", "name": "Diamond back inlay seat 4", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the back gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay seat 4 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [1.9861352762803233, 0.0, -0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "bake"};
  mesh_stud_seat_back_d_22.userData.explodeWithParent = "handle";
  node_stud_seat_back_d_22.add(mesh_stud_seat_back_d_22);
  meshes["stud-seat-back-d"] = mesh_stud_seat_back_d_22;
  colliders["stud-seat-back-d"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_seat_back_d_22);

  const endpoint_stud_seat_back_e_23 = makeAttachmentEndpoint(null);
  const node_stud_seat_back_e_23 = new THREE.Group();
  node_stud_seat_back_e_23.name = "Diamond back inlay seat 5__pivot";
  node_stud_seat_back_e_23.scale.set(1, 1, 1);
  if (endpoint_stud_seat_back_e_23) {
    node_stud_seat_back_e_23.position.copy(endpoint_stud_seat_back_e_23.start);
    node_stud_seat_back_e_23.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_seat_back_e_23.position.set(2.0277879043126683, 0.0, -0.04558042452830188);
    node_stud_seat_back_e_23.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_seat_back_e_23.userData.sculptComponent = {"id": "stud-seat-back-e", "name": "Diamond back inlay seat 5", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the back gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay seat 5 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [2.0277879043126683, 0.0, -0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "bake"};
  node_stud_seat_back_e_23.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}};
  node_stud_seat_back_e_23.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_seat_back_e_23);
  nodes["stud-seat-back-e"] = node_stud_seat_back_e_23;
  const mesh_stud_seat_back_e_23Geometry = endpoint_stud_seat_back_e_23
    ? new THREE.CylinderGeometry(endpoint_stud_seat_back_e_23.endRadius, endpoint_stud_seat_back_e_23.baseRadius, endpoint_stud_seat_back_e_23.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_seat_back_e_23) {
    mesh_stud_seat_back_e_23Geometry.scale(0.029, 0.021, 0.001);
  }
  const mesh_stud_seat_back_e_23 = new THREE.Mesh(
    mesh_stud_seat_back_e_23Geometry,
    materialMap["wrap-seam"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_seat_back_e_23.name = "Diamond back inlay seat 5";
  if (endpoint_stud_seat_back_e_23) {
    mesh_stud_seat_back_e_23.position.copy(endpoint_stud_seat_back_e_23.midpoint);
    mesh_stud_seat_back_e_23.quaternion.copy(endpoint_stud_seat_back_e_23.quaternion);
  }
  mesh_stud_seat_back_e_23.castShadow = options.castShadow ?? true;
  mesh_stud_seat_back_e_23.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_seat_back_e_23.userData.sculptComponent = {"id": "stud-seat-back-e", "name": "Diamond back inlay seat 5", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the back gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay seat 5 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [2.0277879043126683, 0.0, -0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "bake"};
  mesh_stud_seat_back_e_23.userData.explodeWithParent = "handle";
  node_stud_seat_back_e_23.add(mesh_stud_seat_back_e_23);
  meshes["stud-seat-back-e"] = mesh_stud_seat_back_e_23;
  colliders["stud-seat-back-e"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_seat_back_e_23);

  const endpoint_stud_seat_back_f_24 = makeAttachmentEndpoint(null);
  const node_stud_seat_back_f_24 = new THREE.Group();
  node_stud_seat_back_f_24.name = "Diamond back inlay seat 6__pivot";
  node_stud_seat_back_f_24.scale.set(1, 1, 1);
  if (endpoint_stud_seat_back_f_24) {
    node_stud_seat_back_f_24.position.copy(endpoint_stud_seat_back_f_24.start);
    node_stud_seat_back_f_24.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_seat_back_f_24.position.set(2.0694405323450136, 0.0, -0.04558042452830188);
    node_stud_seat_back_f_24.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_seat_back_f_24.userData.sculptComponent = {"id": "stud-seat-back-f", "name": "Diamond back inlay seat 6", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the back gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay seat 6 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [2.0694405323450136, 0.0, -0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "bake"};
  node_stud_seat_back_f_24.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}};
  node_stud_seat_back_f_24.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_seat_back_f_24);
  nodes["stud-seat-back-f"] = node_stud_seat_back_f_24;
  const mesh_stud_seat_back_f_24Geometry = endpoint_stud_seat_back_f_24
    ? new THREE.CylinderGeometry(endpoint_stud_seat_back_f_24.endRadius, endpoint_stud_seat_back_f_24.baseRadius, endpoint_stud_seat_back_f_24.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_seat_back_f_24) {
    mesh_stud_seat_back_f_24Geometry.scale(0.029, 0.021, 0.001);
  }
  const mesh_stud_seat_back_f_24 = new THREE.Mesh(
    mesh_stud_seat_back_f_24Geometry,
    materialMap["wrap-seam"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_seat_back_f_24.name = "Diamond back inlay seat 6";
  if (endpoint_stud_seat_back_f_24) {
    mesh_stud_seat_back_f_24.position.copy(endpoint_stud_seat_back_f_24.midpoint);
    mesh_stud_seat_back_f_24.quaternion.copy(endpoint_stud_seat_back_f_24.quaternion);
  }
  mesh_stud_seat_back_f_24.castShadow = options.castShadow ?? true;
  mesh_stud_seat_back_f_24.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_seat_back_f_24.userData.sculptComponent = {"id": "stud-seat-back-f", "name": "Diamond back inlay seat 6", "level": "micro", "role": "detail", "importance": 0.48, "confidence": 0.78, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Dark shallow socket leaves a narrow wrap-colored border around the back gilt lozenge.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay seat 6 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.029, "height": 0.021, "depth": 0.001, "units": "relative", "confidence": 0.78}, "transform": {"position": [2.0694405323450136, 0.0, -0.04558042452830188], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.029, 0.021, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "wrap-seam"}}, "material": "wrap-seam", "materialLayers": ["wrap-seam"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.78, "microRoughness": 0.86, "bumpAmplitude": 0.012, "normalPattern": "cord-wrap", "displacementPattern": "wrap-turn ridges", "occlusionPattern": "valleys between turns", "edgeWearPattern": "none", "notes": "Dark wrapped grip from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(36, 22, 16, 1.0)", "secondaryAlbedo": "rgba(22, 13, 9, 1.0)", "materialClass": "fabric", "materialClassConfidence": 0.72}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "bake"};
  mesh_stud_seat_back_f_24.userData.explodeWithParent = "handle";
  node_stud_seat_back_f_24.add(mesh_stud_seat_back_f_24);
  meshes["stud-seat-back-f"] = mesh_stud_seat_back_f_24;
  colliders["stud-seat-back-f"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_seat_back_f_24);

  const endpoint_stud_a_25 = makeAttachmentEndpoint(null);
  const node_stud_a_25 = new THREE.Group();
  node_stud_a_25.name = "Diamond inlay 1__pivot";
  node_stud_a_25.scale.set(1, 1, 1);
  if (endpoint_stud_a_25) {
    node_stud_a_25.position.copy(endpoint_stud_a_25.start);
    node_stud_a_25.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_a_25.position.set(1.8611773921832884, 0.0, 0.04598042452830189);
    node_stud_a_25.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_a_25.userData.sculptComponent = {"id": "stud-a", "name": "Diamond inlay 1", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark front wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond inlay 1 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [1.8611773921832884, 0.0, 0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "keep"};
  node_stud_a_25.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}};
  node_stud_a_25.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_a_25);
  nodes["stud-a"] = node_stud_a_25;
  const mesh_stud_a_25Geometry = endpoint_stud_a_25
    ? new THREE.CylinderGeometry(endpoint_stud_a_25.endRadius, endpoint_stud_a_25.baseRadius, endpoint_stud_a_25.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_a_25) {
    mesh_stud_a_25Geometry.scale(0.022, 0.013, 0.001);
  }
  const mesh_stud_a_25 = new THREE.Mesh(
    mesh_stud_a_25Geometry,
    materialMap["gilt-bronze"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_a_25.name = "Diamond inlay 1";
  if (endpoint_stud_a_25) {
    mesh_stud_a_25.position.copy(endpoint_stud_a_25.midpoint);
    mesh_stud_a_25.quaternion.copy(endpoint_stud_a_25.quaternion);
  }
  mesh_stud_a_25.castShadow = options.castShadow ?? true;
  mesh_stud_a_25.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_a_25.userData.sculptComponent = {"id": "stud-a", "name": "Diamond inlay 1", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark front wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond inlay 1 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [1.8611773921832884, 0.0, 0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "keep"};
  mesh_stud_a_25.userData.explodeWithParent = "handle";
  node_stud_a_25.add(mesh_stud_a_25);
  meshes["stud-a"] = mesh_stud_a_25;
  colliders["stud-a"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_a_25);

  const endpoint_stud_b_26 = makeAttachmentEndpoint(null);
  const node_stud_b_26 = new THREE.Group();
  node_stud_b_26.name = "Diamond inlay 2__pivot";
  node_stud_b_26.scale.set(1, 1, 1);
  if (endpoint_stud_b_26) {
    node_stud_b_26.position.copy(endpoint_stud_b_26.start);
    node_stud_b_26.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_b_26.position.set(1.9028300202156334, 0.0, 0.04598042452830189);
    node_stud_b_26.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_b_26.userData.sculptComponent = {"id": "stud-b", "name": "Diamond inlay 2", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark front wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond inlay 2 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [1.9028300202156334, 0.0, 0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "keep"};
  node_stud_b_26.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}};
  node_stud_b_26.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_b_26);
  nodes["stud-b"] = node_stud_b_26;
  const mesh_stud_b_26Geometry = endpoint_stud_b_26
    ? new THREE.CylinderGeometry(endpoint_stud_b_26.endRadius, endpoint_stud_b_26.baseRadius, endpoint_stud_b_26.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_b_26) {
    mesh_stud_b_26Geometry.scale(0.022, 0.013, 0.001);
  }
  const mesh_stud_b_26 = new THREE.Mesh(
    mesh_stud_b_26Geometry,
    materialMap["gilt-bronze"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_b_26.name = "Diamond inlay 2";
  if (endpoint_stud_b_26) {
    mesh_stud_b_26.position.copy(endpoint_stud_b_26.midpoint);
    mesh_stud_b_26.quaternion.copy(endpoint_stud_b_26.quaternion);
  }
  mesh_stud_b_26.castShadow = options.castShadow ?? true;
  mesh_stud_b_26.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_b_26.userData.sculptComponent = {"id": "stud-b", "name": "Diamond inlay 2", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark front wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond inlay 2 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [1.9028300202156334, 0.0, 0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "keep"};
  mesh_stud_b_26.userData.explodeWithParent = "handle";
  node_stud_b_26.add(mesh_stud_b_26);
  meshes["stud-b"] = mesh_stud_b_26;
  colliders["stud-b"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_b_26);

  const endpoint_stud_c_27 = makeAttachmentEndpoint(null);
  const node_stud_c_27 = new THREE.Group();
  node_stud_c_27.name = "Diamond inlay 3__pivot";
  node_stud_c_27.scale.set(1, 1, 1);
  if (endpoint_stud_c_27) {
    node_stud_c_27.position.copy(endpoint_stud_c_27.start);
    node_stud_c_27.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_c_27.position.set(1.9444826482479782, 0.0, 0.04598042452830189);
    node_stud_c_27.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_c_27.userData.sculptComponent = {"id": "stud-c", "name": "Diamond inlay 3", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark front wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond inlay 3 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [1.9444826482479782, 0.0, 0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "keep"};
  node_stud_c_27.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}};
  node_stud_c_27.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_c_27);
  nodes["stud-c"] = node_stud_c_27;
  const mesh_stud_c_27Geometry = endpoint_stud_c_27
    ? new THREE.CylinderGeometry(endpoint_stud_c_27.endRadius, endpoint_stud_c_27.baseRadius, endpoint_stud_c_27.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_c_27) {
    mesh_stud_c_27Geometry.scale(0.022, 0.013, 0.001);
  }
  const mesh_stud_c_27 = new THREE.Mesh(
    mesh_stud_c_27Geometry,
    materialMap["gilt-bronze"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_c_27.name = "Diamond inlay 3";
  if (endpoint_stud_c_27) {
    mesh_stud_c_27.position.copy(endpoint_stud_c_27.midpoint);
    mesh_stud_c_27.quaternion.copy(endpoint_stud_c_27.quaternion);
  }
  mesh_stud_c_27.castShadow = options.castShadow ?? true;
  mesh_stud_c_27.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_c_27.userData.sculptComponent = {"id": "stud-c", "name": "Diamond inlay 3", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark front wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond inlay 3 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [1.9444826482479782, 0.0, 0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "keep"};
  mesh_stud_c_27.userData.explodeWithParent = "handle";
  node_stud_c_27.add(mesh_stud_c_27);
  meshes["stud-c"] = mesh_stud_c_27;
  colliders["stud-c"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_c_27);

  const endpoint_stud_d_28 = makeAttachmentEndpoint(null);
  const node_stud_d_28 = new THREE.Group();
  node_stud_d_28.name = "Diamond inlay 4__pivot";
  node_stud_d_28.scale.set(1, 1, 1);
  if (endpoint_stud_d_28) {
    node_stud_d_28.position.copy(endpoint_stud_d_28.start);
    node_stud_d_28.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_d_28.position.set(1.9861352762803233, 0.0, 0.04598042452830189);
    node_stud_d_28.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_d_28.userData.sculptComponent = {"id": "stud-d", "name": "Diamond inlay 4", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark front wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond inlay 4 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [1.9861352762803233, 0.0, 0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "keep"};
  node_stud_d_28.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}};
  node_stud_d_28.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_d_28);
  nodes["stud-d"] = node_stud_d_28;
  const mesh_stud_d_28Geometry = endpoint_stud_d_28
    ? new THREE.CylinderGeometry(endpoint_stud_d_28.endRadius, endpoint_stud_d_28.baseRadius, endpoint_stud_d_28.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_d_28) {
    mesh_stud_d_28Geometry.scale(0.022, 0.013, 0.001);
  }
  const mesh_stud_d_28 = new THREE.Mesh(
    mesh_stud_d_28Geometry,
    materialMap["gilt-bronze"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_d_28.name = "Diamond inlay 4";
  if (endpoint_stud_d_28) {
    mesh_stud_d_28.position.copy(endpoint_stud_d_28.midpoint);
    mesh_stud_d_28.quaternion.copy(endpoint_stud_d_28.quaternion);
  }
  mesh_stud_d_28.castShadow = options.castShadow ?? true;
  mesh_stud_d_28.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_d_28.userData.sculptComponent = {"id": "stud-d", "name": "Diamond inlay 4", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark front wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond inlay 4 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [1.9861352762803233, 0.0, 0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "keep"};
  mesh_stud_d_28.userData.explodeWithParent = "handle";
  node_stud_d_28.add(mesh_stud_d_28);
  meshes["stud-d"] = mesh_stud_d_28;
  colliders["stud-d"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_d_28);

  const endpoint_stud_e_29 = makeAttachmentEndpoint(null);
  const node_stud_e_29 = new THREE.Group();
  node_stud_e_29.name = "Diamond inlay 5__pivot";
  node_stud_e_29.scale.set(1, 1, 1);
  if (endpoint_stud_e_29) {
    node_stud_e_29.position.copy(endpoint_stud_e_29.start);
    node_stud_e_29.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_e_29.position.set(2.0277879043126683, 0.0, 0.04598042452830189);
    node_stud_e_29.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_e_29.userData.sculptComponent = {"id": "stud-e", "name": "Diamond inlay 5", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark front wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond inlay 5 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [2.0277879043126683, 0.0, 0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "keep"};
  node_stud_e_29.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}};
  node_stud_e_29.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_e_29);
  nodes["stud-e"] = node_stud_e_29;
  const mesh_stud_e_29Geometry = endpoint_stud_e_29
    ? new THREE.CylinderGeometry(endpoint_stud_e_29.endRadius, endpoint_stud_e_29.baseRadius, endpoint_stud_e_29.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_e_29) {
    mesh_stud_e_29Geometry.scale(0.022, 0.013, 0.001);
  }
  const mesh_stud_e_29 = new THREE.Mesh(
    mesh_stud_e_29Geometry,
    materialMap["gilt-bronze"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_e_29.name = "Diamond inlay 5";
  if (endpoint_stud_e_29) {
    mesh_stud_e_29.position.copy(endpoint_stud_e_29.midpoint);
    mesh_stud_e_29.quaternion.copy(endpoint_stud_e_29.quaternion);
  }
  mesh_stud_e_29.castShadow = options.castShadow ?? true;
  mesh_stud_e_29.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_e_29.userData.sculptComponent = {"id": "stud-e", "name": "Diamond inlay 5", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark front wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond inlay 5 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [2.0277879043126683, 0.0, 0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "keep"};
  mesh_stud_e_29.userData.explodeWithParent = "handle";
  node_stud_e_29.add(mesh_stud_e_29);
  meshes["stud-e"] = mesh_stud_e_29;
  colliders["stud-e"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_e_29);

  const endpoint_stud_f_30 = makeAttachmentEndpoint(null);
  const node_stud_f_30 = new THREE.Group();
  node_stud_f_30.name = "Diamond inlay 6__pivot";
  node_stud_f_30.scale.set(1, 1, 1);
  if (endpoint_stud_f_30) {
    node_stud_f_30.position.copy(endpoint_stud_f_30.start);
    node_stud_f_30.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_f_30.position.set(2.0694405323450136, 0.0, 0.04598042452830189);
    node_stud_f_30.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_f_30.userData.sculptComponent = {"id": "stud-f", "name": "Diamond inlay 6", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark front wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond inlay 6 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [2.0694405323450136, 0.0, 0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "keep"};
  node_stud_f_30.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}};
  node_stud_f_30.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_f_30);
  nodes["stud-f"] = node_stud_f_30;
  const mesh_stud_f_30Geometry = endpoint_stud_f_30
    ? new THREE.CylinderGeometry(endpoint_stud_f_30.endRadius, endpoint_stud_f_30.baseRadius, endpoint_stud_f_30.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_f_30) {
    mesh_stud_f_30Geometry.scale(0.022, 0.013, 0.001);
  }
  const mesh_stud_f_30 = new THREE.Mesh(
    mesh_stud_f_30Geometry,
    materialMap["gilt-bronze"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_f_30.name = "Diamond inlay 6";
  if (endpoint_stud_f_30) {
    mesh_stud_f_30.position.copy(endpoint_stud_f_30.midpoint);
    mesh_stud_f_30.quaternion.copy(endpoint_stud_f_30.quaternion);
  }
  mesh_stud_f_30.castShadow = options.castShadow ?? true;
  mesh_stud_f_30.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_f_30.userData.sculptComponent = {"id": "stud-f", "name": "Diamond inlay 6", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark front wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond inlay 6 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [2.0694405323450136, 0.0, 0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "front", "mergePolicy": "keep"};
  mesh_stud_f_30.userData.explodeWithParent = "handle";
  node_stud_f_30.add(mesh_stud_f_30);
  meshes["stud-f"] = mesh_stud_f_30;
  colliders["stud-f"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_f_30);

  const endpoint_stud_back_a_31 = makeAttachmentEndpoint(null);
  const node_stud_back_a_31 = new THREE.Group();
  node_stud_back_a_31.name = "Diamond back inlay 1__pivot";
  node_stud_back_a_31.scale.set(1, 1, 1);
  if (endpoint_stud_back_a_31) {
    node_stud_back_a_31.position.copy(endpoint_stud_back_a_31.start);
    node_stud_back_a_31.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_back_a_31.position.set(1.8611773921832884, 0.0, -0.04598042452830189);
    node_stud_back_a_31.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_back_a_31.userData.sculptComponent = {"id": "stud-back-a", "name": "Diamond back inlay 1", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark back wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay 1 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [1.8611773921832884, 0.0, -0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "keep"};
  node_stud_back_a_31.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}};
  node_stud_back_a_31.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_back_a_31);
  nodes["stud-back-a"] = node_stud_back_a_31;
  const mesh_stud_back_a_31Geometry = endpoint_stud_back_a_31
    ? new THREE.CylinderGeometry(endpoint_stud_back_a_31.endRadius, endpoint_stud_back_a_31.baseRadius, endpoint_stud_back_a_31.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_back_a_31) {
    mesh_stud_back_a_31Geometry.scale(0.022, 0.013, 0.001);
  }
  const mesh_stud_back_a_31 = new THREE.Mesh(
    mesh_stud_back_a_31Geometry,
    materialMap["gilt-bronze"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_back_a_31.name = "Diamond back inlay 1";
  if (endpoint_stud_back_a_31) {
    mesh_stud_back_a_31.position.copy(endpoint_stud_back_a_31.midpoint);
    mesh_stud_back_a_31.quaternion.copy(endpoint_stud_back_a_31.quaternion);
  }
  mesh_stud_back_a_31.castShadow = options.castShadow ?? true;
  mesh_stud_back_a_31.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_back_a_31.userData.sculptComponent = {"id": "stud-back-a", "name": "Diamond back inlay 1", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark back wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay 1 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [1.8611773921832884, 0.0, -0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "keep"};
  mesh_stud_back_a_31.userData.explodeWithParent = "handle";
  node_stud_back_a_31.add(mesh_stud_back_a_31);
  meshes["stud-back-a"] = mesh_stud_back_a_31;
  colliders["stud-back-a"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_back_a_31);

  const endpoint_stud_back_b_32 = makeAttachmentEndpoint(null);
  const node_stud_back_b_32 = new THREE.Group();
  node_stud_back_b_32.name = "Diamond back inlay 2__pivot";
  node_stud_back_b_32.scale.set(1, 1, 1);
  if (endpoint_stud_back_b_32) {
    node_stud_back_b_32.position.copy(endpoint_stud_back_b_32.start);
    node_stud_back_b_32.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_back_b_32.position.set(1.9028300202156334, 0.0, -0.04598042452830189);
    node_stud_back_b_32.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_back_b_32.userData.sculptComponent = {"id": "stud-back-b", "name": "Diamond back inlay 2", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark back wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay 2 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [1.9028300202156334, 0.0, -0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "keep"};
  node_stud_back_b_32.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}};
  node_stud_back_b_32.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_back_b_32);
  nodes["stud-back-b"] = node_stud_back_b_32;
  const mesh_stud_back_b_32Geometry = endpoint_stud_back_b_32
    ? new THREE.CylinderGeometry(endpoint_stud_back_b_32.endRadius, endpoint_stud_back_b_32.baseRadius, endpoint_stud_back_b_32.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_back_b_32) {
    mesh_stud_back_b_32Geometry.scale(0.022, 0.013, 0.001);
  }
  const mesh_stud_back_b_32 = new THREE.Mesh(
    mesh_stud_back_b_32Geometry,
    materialMap["gilt-bronze"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_back_b_32.name = "Diamond back inlay 2";
  if (endpoint_stud_back_b_32) {
    mesh_stud_back_b_32.position.copy(endpoint_stud_back_b_32.midpoint);
    mesh_stud_back_b_32.quaternion.copy(endpoint_stud_back_b_32.quaternion);
  }
  mesh_stud_back_b_32.castShadow = options.castShadow ?? true;
  mesh_stud_back_b_32.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_back_b_32.userData.sculptComponent = {"id": "stud-back-b", "name": "Diamond back inlay 2", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark back wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay 2 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [1.9028300202156334, 0.0, -0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "keep"};
  mesh_stud_back_b_32.userData.explodeWithParent = "handle";
  node_stud_back_b_32.add(mesh_stud_back_b_32);
  meshes["stud-back-b"] = mesh_stud_back_b_32;
  colliders["stud-back-b"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_back_b_32);

  const endpoint_stud_back_c_33 = makeAttachmentEndpoint(null);
  const node_stud_back_c_33 = new THREE.Group();
  node_stud_back_c_33.name = "Diamond back inlay 3__pivot";
  node_stud_back_c_33.scale.set(1, 1, 1);
  if (endpoint_stud_back_c_33) {
    node_stud_back_c_33.position.copy(endpoint_stud_back_c_33.start);
    node_stud_back_c_33.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_back_c_33.position.set(1.9444826482479782, 0.0, -0.04598042452830189);
    node_stud_back_c_33.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_back_c_33.userData.sculptComponent = {"id": "stud-back-c", "name": "Diamond back inlay 3", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark back wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay 3 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [1.9444826482479782, 0.0, -0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "keep"};
  node_stud_back_c_33.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}};
  node_stud_back_c_33.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_back_c_33);
  nodes["stud-back-c"] = node_stud_back_c_33;
  const mesh_stud_back_c_33Geometry = endpoint_stud_back_c_33
    ? new THREE.CylinderGeometry(endpoint_stud_back_c_33.endRadius, endpoint_stud_back_c_33.baseRadius, endpoint_stud_back_c_33.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_back_c_33) {
    mesh_stud_back_c_33Geometry.scale(0.022, 0.013, 0.001);
  }
  const mesh_stud_back_c_33 = new THREE.Mesh(
    mesh_stud_back_c_33Geometry,
    materialMap["gilt-bronze"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_back_c_33.name = "Diamond back inlay 3";
  if (endpoint_stud_back_c_33) {
    mesh_stud_back_c_33.position.copy(endpoint_stud_back_c_33.midpoint);
    mesh_stud_back_c_33.quaternion.copy(endpoint_stud_back_c_33.quaternion);
  }
  mesh_stud_back_c_33.castShadow = options.castShadow ?? true;
  mesh_stud_back_c_33.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_back_c_33.userData.sculptComponent = {"id": "stud-back-c", "name": "Diamond back inlay 3", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark back wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay 3 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [1.9444826482479782, 0.0, -0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "keep"};
  mesh_stud_back_c_33.userData.explodeWithParent = "handle";
  node_stud_back_c_33.add(mesh_stud_back_c_33);
  meshes["stud-back-c"] = mesh_stud_back_c_33;
  colliders["stud-back-c"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_back_c_33);

  const endpoint_stud_back_d_34 = makeAttachmentEndpoint(null);
  const node_stud_back_d_34 = new THREE.Group();
  node_stud_back_d_34.name = "Diamond back inlay 4__pivot";
  node_stud_back_d_34.scale.set(1, 1, 1);
  if (endpoint_stud_back_d_34) {
    node_stud_back_d_34.position.copy(endpoint_stud_back_d_34.start);
    node_stud_back_d_34.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_back_d_34.position.set(1.9861352762803233, 0.0, -0.04598042452830189);
    node_stud_back_d_34.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_back_d_34.userData.sculptComponent = {"id": "stud-back-d", "name": "Diamond back inlay 4", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark back wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay 4 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [1.9861352762803233, 0.0, -0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "keep"};
  node_stud_back_d_34.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}};
  node_stud_back_d_34.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_back_d_34);
  nodes["stud-back-d"] = node_stud_back_d_34;
  const mesh_stud_back_d_34Geometry = endpoint_stud_back_d_34
    ? new THREE.CylinderGeometry(endpoint_stud_back_d_34.endRadius, endpoint_stud_back_d_34.baseRadius, endpoint_stud_back_d_34.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_back_d_34) {
    mesh_stud_back_d_34Geometry.scale(0.022, 0.013, 0.001);
  }
  const mesh_stud_back_d_34 = new THREE.Mesh(
    mesh_stud_back_d_34Geometry,
    materialMap["gilt-bronze"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_back_d_34.name = "Diamond back inlay 4";
  if (endpoint_stud_back_d_34) {
    mesh_stud_back_d_34.position.copy(endpoint_stud_back_d_34.midpoint);
    mesh_stud_back_d_34.quaternion.copy(endpoint_stud_back_d_34.quaternion);
  }
  mesh_stud_back_d_34.castShadow = options.castShadow ?? true;
  mesh_stud_back_d_34.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_back_d_34.userData.sculptComponent = {"id": "stud-back-d", "name": "Diamond back inlay 4", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark back wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay 4 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [1.9861352762803233, 0.0, -0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "keep"};
  mesh_stud_back_d_34.userData.explodeWithParent = "handle";
  node_stud_back_d_34.add(mesh_stud_back_d_34);
  meshes["stud-back-d"] = mesh_stud_back_d_34;
  colliders["stud-back-d"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_back_d_34);

  const endpoint_stud_back_e_35 = makeAttachmentEndpoint(null);
  const node_stud_back_e_35 = new THREE.Group();
  node_stud_back_e_35.name = "Diamond back inlay 5__pivot";
  node_stud_back_e_35.scale.set(1, 1, 1);
  if (endpoint_stud_back_e_35) {
    node_stud_back_e_35.position.copy(endpoint_stud_back_e_35.start);
    node_stud_back_e_35.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_back_e_35.position.set(2.0277879043126683, 0.0, -0.04598042452830189);
    node_stud_back_e_35.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_back_e_35.userData.sculptComponent = {"id": "stud-back-e", "name": "Diamond back inlay 5", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark back wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay 5 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [2.0277879043126683, 0.0, -0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "keep"};
  node_stud_back_e_35.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}};
  node_stud_back_e_35.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_back_e_35);
  nodes["stud-back-e"] = node_stud_back_e_35;
  const mesh_stud_back_e_35Geometry = endpoint_stud_back_e_35
    ? new THREE.CylinderGeometry(endpoint_stud_back_e_35.endRadius, endpoint_stud_back_e_35.baseRadius, endpoint_stud_back_e_35.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_back_e_35) {
    mesh_stud_back_e_35Geometry.scale(0.022, 0.013, 0.001);
  }
  const mesh_stud_back_e_35 = new THREE.Mesh(
    mesh_stud_back_e_35Geometry,
    materialMap["gilt-bronze"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_back_e_35.name = "Diamond back inlay 5";
  if (endpoint_stud_back_e_35) {
    mesh_stud_back_e_35.position.copy(endpoint_stud_back_e_35.midpoint);
    mesh_stud_back_e_35.quaternion.copy(endpoint_stud_back_e_35.quaternion);
  }
  mesh_stud_back_e_35.castShadow = options.castShadow ?? true;
  mesh_stud_back_e_35.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_back_e_35.userData.sculptComponent = {"id": "stud-back-e", "name": "Diamond back inlay 5", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark back wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay 5 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [2.0277879043126683, 0.0, -0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "keep"};
  mesh_stud_back_e_35.userData.explodeWithParent = "handle";
  node_stud_back_e_35.add(mesh_stud_back_e_35);
  meshes["stud-back-e"] = mesh_stud_back_e_35;
  colliders["stud-back-e"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_back_e_35);

  const endpoint_stud_back_f_36 = makeAttachmentEndpoint(null);
  const node_stud_back_f_36 = new THREE.Group();
  node_stud_back_f_36.name = "Diamond back inlay 6__pivot";
  node_stud_back_f_36.scale.set(1, 1, 1);
  if (endpoint_stud_back_f_36) {
    node_stud_back_f_36.position.copy(endpoint_stud_back_f_36.start);
    node_stud_back_f_36.rotation.set(0.0, 0.0, 0.7853981633974483);
  } else {
    node_stud_back_f_36.position.set(2.0694405323450136, 0.0, -0.04598042452830189);
    node_stud_back_f_36.rotation.set(0.0, 0.0, 0.7853981633974483);
  }
  node_stud_back_f_36.userData.sculptComponent = {"id": "stud-back-f", "name": "Diamond back inlay 6", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark back wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay 6 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [2.0694405323450136, 0.0, -0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "keep"};
  node_stud_back_f_36.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}};
  node_stud_back_f_36.userData.explodeWithParent = "handle";
  (nodes["root"] ?? root).add(node_stud_back_f_36);
  nodes["stud-back-f"] = node_stud_back_f_36;
  const mesh_stud_back_f_36Geometry = endpoint_stud_back_f_36
    ? new THREE.CylinderGeometry(endpoint_stud_back_f_36.endRadius, endpoint_stud_back_f_36.baseRadius, endpoint_stud_back_f_36.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_stud_back_f_36) {
    mesh_stud_back_f_36Geometry.scale(0.022, 0.013, 0.001);
  }
  const mesh_stud_back_f_36 = new THREE.Mesh(
    mesh_stud_back_f_36Geometry,
    materialMap["gilt-bronze"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_stud_back_f_36.name = "Diamond back inlay 6";
  if (endpoint_stud_back_f_36) {
    mesh_stud_back_f_36.position.copy(endpoint_stud_back_f_36.midpoint);
    mesh_stud_back_f_36.quaternion.copy(endpoint_stud_back_f_36.quaternion);
  }
  mesh_stud_back_f_36.castShadow = options.castShadow ?? true;
  mesh_stud_back_f_36.receiveShadow = options.receiveShadow ?? true;
  mesh_stud_back_f_36.userData.sculptComponent = {"id": "stud-back-f", "name": "Diamond back inlay 6", "level": "micro", "role": "detail", "importance": 0.55, "confidence": 0.82, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Small gilt lozenge sits nearly flush inside a larger dark back wrap socket.", "geometryDescriptor": {"topologyIntent": "Diamond back inlay 6 reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.022, "height": 0.013, "depth": 0.001, "units": "relative", "confidence": 0.82}, "transform": {"position": [2.0694405323450136, 0.0, -0.04598042452830189], "rotation": [0.0, 0.0, 0.7853981633974483], "scale": [0.022, 0.013, 0.001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "handle", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "handle", "ownerModule": "handle", "face": "back", "mergePolicy": "keep"};
  mesh_stud_back_f_36.userData.explodeWithParent = "handle";
  node_stud_back_f_36.add(mesh_stud_back_f_36);
  meshes["stud-back-f"] = mesh_stud_back_f_36;
  colliders["stud-back-f"] = null;
  destructionGroups["handle"] ??= [];
  destructionGroups["handle"].push(node_stud_back_f_36);

  const attachment_ferrule_37 = {"parentId": "root", "parentSocket": "handle-back", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.01, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_ferrule_37 = makeAttachmentEndpoint(attachment_ferrule_37);
  const node_ferrule_37 = new THREE.Group();
  node_ferrule_37.name = "Gilt rear ferrule__pivot";
  node_ferrule_37.scale.set(1, 1, 1);
  if (endpoint_ferrule_37) {
    node_ferrule_37.position.copy(endpoint_ferrule_37.start);
    node_ferrule_37.rotation.set(0.0, 0.0, 1.5707963267948966);
  } else {
    node_ferrule_37.position.set(2.111873820754717, 0.0, 0.0);
    node_ferrule_37.rotation.set(0.0, 0.0, 1.5707963267948966);
  }
  node_ferrule_37.userData.sculptComponent = {"id": "ferrule", "name": "Gilt rear ferrule", "level": "meso", "role": "body", "importance": 0.8, "confidence": 0.8, "primitive": "cylinder", "topologyClass": "assembled-solid", "topologyRationale": "Thin gilt band between wrap and ring. Stops short of the ring so it does not fill the aperture.", "geometryDescriptor": {"topologyIntent": "Gilt rear ferrule reconstruction", "edgeTreatment": {"type": "chamfer", "bevelRadius": 0.0015, "segments": 2}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "handle-back", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.01, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 0.07594339622641509, "height": 0.01356132075471698, "depth": 0.07594339622641509, "units": "relative", "confidence": 0.8}, "transform": {"position": [2.111873820754717, 0.0, 0.0], "rotation": [0.0, 0.0, 1.5707963267948966], "scale": [0.07594339622641509, 0.01356132075471698, 0.07594339622641509]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "rear ferrule proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ferrule", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["pommel-ring", "handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.78}};
  node_ferrule_37.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "rear ferrule proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ferrule", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}};
  node_ferrule_37.userData.explodeWithParent = null;
  (nodes["root"] ?? root).add(node_ferrule_37);
  nodes["ferrule"] = node_ferrule_37;
  const mesh_ferrule_37Geometry = endpoint_ferrule_37
    ? new THREE.CylinderGeometry(endpoint_ferrule_37.endRadius, endpoint_ferrule_37.baseRadius, endpoint_ferrule_37.length, 32, 12)
    : new THREE.CylinderGeometry(0.5, 0.5, 1, 48, 16);
  if (!endpoint_ferrule_37) {
    mesh_ferrule_37Geometry.scale(0.07594339622641509, 0.01356132075471698, 0.07594339622641509);
  }
  const mesh_ferrule_37 = new THREE.Mesh(
    mesh_ferrule_37Geometry,
    materialMap["gilt-bronze"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_ferrule_37.name = "Gilt rear ferrule";
  if (endpoint_ferrule_37) {
    mesh_ferrule_37.position.copy(endpoint_ferrule_37.midpoint);
    mesh_ferrule_37.quaternion.copy(endpoint_ferrule_37.quaternion);
  }
  mesh_ferrule_37.castShadow = options.castShadow ?? true;
  mesh_ferrule_37.receiveShadow = options.receiveShadow ?? true;
  mesh_ferrule_37.userData.sculptComponent = {"id": "ferrule", "name": "Gilt rear ferrule", "level": "meso", "role": "body", "importance": 0.8, "confidence": 0.8, "primitive": "cylinder", "topologyClass": "assembled-solid", "topologyRationale": "Thin gilt band between wrap and ring. Stops short of the ring so it does not fill the aperture.", "geometryDescriptor": {"topologyIntent": "Gilt rear ferrule reconstruction", "edgeTreatment": {"type": "chamfer", "bevelRadius": 0.0015, "segments": 2}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "handle-back", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.01, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 0.07594339622641509, "height": 0.01356132075471698, "depth": 0.07594339622641509, "units": "relative", "confidence": 0.8}, "transform": {"position": [2.111873820754717, 0.0, 0.0], "rotation": [0.0, 0.0, 1.5707963267948966], "scale": [0.07594339622641509, 0.01356132075471698, 0.07594339622641509]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "rear ferrule proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ferrule", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["pommel-ring", "handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.78}};
  mesh_ferrule_37.userData.explodeWithParent = null;
  node_ferrule_37.add(mesh_ferrule_37);
  meshes["ferrule"] = mesh_ferrule_37;
  colliders["ferrule"] = {"type": "cylinder", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "rear ferrule proxy"};
  destructionGroups["ferrule"] ??= [];
  destructionGroups["ferrule"].push(node_ferrule_37);

  const attachment_ring_neck_38 = {"parentId": "root", "parentSocket": "rear-ferrule-back", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.01, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_ring_neck_38 = makeAttachmentEndpoint(attachment_ring_neck_38);
  const node_ring_neck_38 = new THREE.Group();
  node_ring_neck_38.name = "Huan-shou neck__pivot";
  node_ring_neck_38.scale.set(1, 1, 1);
  if (endpoint_ring_neck_38) {
    node_ring_neck_38.position.copy(endpoint_ring_neck_38.start);
    node_ring_neck_38.rotation.set(0.0, 0.0, 1.5707963267948966);
  } else {
    node_ring_neck_38.position.set(2.1221544811320756, 0.0, 0.0);
    node_ring_neck_38.rotation.set(0.0, 0.0, 1.5707963267948966);
  }
  node_ring_neck_38.userData.sculptComponent = {"id": "ring-neck", "name": "Huan-shou neck", "level": "meso", "role": "body", "importance": 0.75, "confidence": 0.82, "primitive": "cylinder", "topologyClass": "assembled-solid", "topologyRationale": "Short gilt neck bridges the rear ferrule to the offset ring profile.", "geometryDescriptor": {"topologyIntent": "Huan-shou neck reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "rear-ferrule-back", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.01, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 0.045, "height": 0.018, "depth": 0.045, "units": "relative", "confidence": 0.82}, "transform": {"position": [2.1221544811320756, 0.0, 0.0], "rotation": [0.0, 0.0, 1.5707963267948966], "scale": [0.045, 0.018, 0.045]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["pommel-ring", "handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.78}, "explodeWithParent": "ring"};
  node_ring_neck_38.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}};
  node_ring_neck_38.userData.explodeWithParent = "ring";
  (nodes["root"] ?? root).add(node_ring_neck_38);
  nodes["ring-neck"] = node_ring_neck_38;
  const mesh_ring_neck_38Geometry = endpoint_ring_neck_38
    ? new THREE.CylinderGeometry(endpoint_ring_neck_38.endRadius, endpoint_ring_neck_38.baseRadius, endpoint_ring_neck_38.length, 32, 12)
    : new THREE.CylinderGeometry(0.5, 0.5, 1, 48, 16);
  if (!endpoint_ring_neck_38) {
    mesh_ring_neck_38Geometry.scale(0.045, 0.018, 0.045);
  }
  const mesh_ring_neck_38 = new THREE.Mesh(
    mesh_ring_neck_38Geometry,
    materialMap["gilt-bronze"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_ring_neck_38.name = "Huan-shou neck";
  if (endpoint_ring_neck_38) {
    mesh_ring_neck_38.position.copy(endpoint_ring_neck_38.midpoint);
    mesh_ring_neck_38.quaternion.copy(endpoint_ring_neck_38.quaternion);
  }
  mesh_ring_neck_38.castShadow = options.castShadow ?? true;
  mesh_ring_neck_38.receiveShadow = options.receiveShadow ?? true;
  mesh_ring_neck_38.userData.sculptComponent = {"id": "ring-neck", "name": "Huan-shou neck", "level": "meso", "role": "body", "importance": 0.75, "confidence": 0.82, "primitive": "cylinder", "topologyClass": "assembled-solid", "topologyRationale": "Short gilt neck bridges the rear ferrule to the offset ring profile.", "geometryDescriptor": {"topologyIntent": "Huan-shou neck reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry"}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "rear-ferrule-back", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.01, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 0.045, "height": 0.018, "depth": 0.045, "units": "relative", "confidence": 0.82}, "transform": {"position": [2.1221544811320756, 0.0, 0.0], "rotation": [0.0, 0.0, 1.5707963267948966], "scale": [0.045, 0.018, 0.045]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["pommel-ring", "handle"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.78}, "explodeWithParent": "ring"};
  mesh_ring_neck_38.userData.explodeWithParent = "ring";
  node_ring_neck_38.add(mesh_ring_neck_38);
  meshes["ring-neck"] = mesh_ring_neck_38;
  colliders["ring-neck"] = null;
  destructionGroups["ring"] ??= [];
  destructionGroups["ring"].push(node_ring_neck_38);

  const endpoint_ring_39 = makeAttachmentEndpoint(null);
  const node_ring_39 = new THREE.Group();
  node_ring_39.name = "Huan-shou ring__pivot";
  node_ring_39.scale.set(1, 1, 1);
  if (endpoint_ring_39) {
    node_ring_39.position.copy(endpoint_ring_39.start);
    node_ring_39.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_ring_39.position.set(2.1996544811320757, 0.0, -0.006);
    node_ring_39.rotation.set(0.0, 0.0, 0.0);
  }
  node_ring_39.userData.sculptComponent = {"id": "ring", "name": "Huan-shou ring", "level": "macro", "role": "pommel", "importance": 1.0, "confidence": 0.9, "primitive": "extrude", "topologyClass": "assembled-solid", "topologyRationale": "Shallow gilt profile in the blade-face plane; an extruded oval hole preserves the aperture without the inflated look of a torus.", "geometryDescriptor": {"topologyIntent": "Huan-shou ring reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "profile2D": {"points": [[0.5, 0.0], [0.49039, 0.09755], [0.46194, 0.19134], [0.41573, 0.27779], [0.35355, 0.35355], [0.27779, 0.41573], [0.19134, 0.46194], [0.09755, 0.49039], [0.0, 0.5], [-0.09755, 0.49039], [-0.19134, 0.46194], [-0.27779, 0.41573], [-0.35355, 0.35355], [-0.41573, 0.27779], [-0.46194, 0.19134], [-0.49039, 0.09755], [-0.5, 0.0], [-0.49039, -0.09755], [-0.46194, -0.19134], [-0.41573, -0.27779], [-0.35355, -0.35355], [-0.27779, -0.41573], [-0.19134, -0.46194], [-0.09755, -0.49039], [-0.0, -0.5], [0.09755, -0.49039], [0.19134, -0.46194], [0.27779, -0.41573], [0.35355, -0.35355], [0.41573, -0.27779], [0.46194, -0.19134], [0.49039, -0.09755]], "depth": 1.0, "ovalHoles": [{"cx": 0.04, "cy": 0.0, "rx": 0.28, "ry": 0.262}]}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "rear-ferrule-back", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.007, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 0.148, "height": 0.158, "depth": 0.012, "units": "relative", "confidence": 0.9}, "transform": {"position": [2.1996544811320757, 0.0, -0.006], "rotation": [0.0, 0.0, 0.0], "scale": [0.148, 0.158, 0.012]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "sphere", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "ring bounds"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [{"id": "ring-aperture", "kind": "hole", "notes": "Negative space must remain open in every orbit view."}], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["pommel-ring"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.84}};
  node_ring_39.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "sphere", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "ring bounds"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}};
  node_ring_39.userData.explodeWithParent = null;
  (nodes["root"] ?? root).add(node_ring_39);
  nodes["ring"] = node_ring_39;
  const mesh_ring_39Geometry = endpoint_ring_39
    ? new THREE.CylinderGeometry(endpoint_ring_39.endRadius, endpoint_ring_39.baseRadius, endpoint_ring_39.length, 32, 12)
    : buildExtrudeGeometry({"points": [[0.5, 0.0], [0.49039, 0.09755], [0.46194, 0.19134], [0.41573, 0.27779], [0.35355, 0.35355], [0.27779, 0.41573], [0.19134, 0.46194], [0.09755, 0.49039], [0.0, 0.5], [-0.09755, 0.49039], [-0.19134, 0.46194], [-0.27779, 0.41573], [-0.35355, 0.35355], [-0.41573, 0.27779], [-0.46194, 0.19134], [-0.49039, 0.09755], [-0.5, 0.0], [-0.49039, -0.09755], [-0.46194, -0.19134], [-0.41573, -0.27779], [-0.35355, -0.35355], [-0.27779, -0.41573], [-0.19134, -0.46194], [-0.09755, -0.49039], [-0.0, -0.5], [0.09755, -0.49039], [0.19134, -0.46194], [0.27779, -0.41573], [0.35355, -0.35355], [0.41573, -0.27779], [0.46194, -0.19134], [0.49039, -0.09755]], "depth": 1.0, "ovalHoles": [{"cx": 0.04, "cy": 0.0, "rx": 0.28, "ry": 0.262}]});
  if (!endpoint_ring_39) {
    mesh_ring_39Geometry.scale(0.148, 0.158, 0.012);
  }
  const mesh_ring_39 = new THREE.Mesh(
    mesh_ring_39Geometry,
    materialMap["gilt-bronze"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_ring_39.name = "Huan-shou ring";
  if (endpoint_ring_39) {
    mesh_ring_39.position.copy(endpoint_ring_39.midpoint);
    mesh_ring_39.quaternion.copy(endpoint_ring_39.quaternion);
  }
  mesh_ring_39.castShadow = options.castShadow ?? true;
  mesh_ring_39.receiveShadow = options.receiveShadow ?? true;
  mesh_ring_39.userData.sculptComponent = {"id": "ring", "name": "Huan-shou ring", "level": "macro", "role": "pommel", "importance": 1.0, "confidence": 0.9, "primitive": "extrude", "topologyClass": "assembled-solid", "topologyRationale": "Shallow gilt profile in the blade-face plane; an extruded oval hole preserves the aperture without the inflated look of a torus.", "geometryDescriptor": {"topologyIntent": "Huan-shou ring reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "profile2D": {"points": [[0.5, 0.0], [0.49039, 0.09755], [0.46194, 0.19134], [0.41573, 0.27779], [0.35355, 0.35355], [0.27779, 0.41573], [0.19134, 0.46194], [0.09755, 0.49039], [0.0, 0.5], [-0.09755, 0.49039], [-0.19134, 0.46194], [-0.27779, 0.41573], [-0.35355, 0.35355], [-0.41573, 0.27779], [-0.46194, 0.19134], [-0.49039, 0.09755], [-0.5, 0.0], [-0.49039, -0.09755], [-0.46194, -0.19134], [-0.41573, -0.27779], [-0.35355, -0.35355], [-0.27779, -0.41573], [-0.19134, -0.46194], [-0.09755, -0.49039], [-0.0, -0.5], [0.09755, -0.49039], [0.19134, -0.46194], [0.27779, -0.41573], [0.35355, -0.35355], [0.41573, -0.27779], [0.46194, -0.19134], [0.49039, -0.09755]], "depth": 1.0, "ovalHoles": [{"cx": 0.04, "cy": 0.0, "rx": 0.28, "ry": 0.262}]}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "rear-ferrule-back", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.007, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 0.148, "height": 0.158, "depth": 0.012, "units": "relative", "confidence": 0.9}, "transform": {"position": [2.1996544811320757, 0.0, -0.006], "rotation": [0.0, 0.0, 0.0], "scale": [0.148, 0.158, 0.012]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": {"type": "sphere", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "ring bounds"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-bronze"}}, "material": "gilt-bronze", "materialLayers": ["gilt-bronze"], "deformations": [], "joints": [], "seams": [], "localFeatures": [{"id": "ring-aperture", "kind": "hole", "notes": "Negative space must remain open in every orbit view."}], "surfaceDetail": {"macroRoughness": 0.4, "microRoughness": 0.5, "bumpAmplitude": 0.005, "normalPattern": "cast-engraving", "displacementPattern": "none", "occlusionPattern": "inner ring and seams", "edgeWearPattern": "brighter gilt on outer rim", "notes": "Gilt fittings from the three-view plate."}, "evidenceRefs": ["pommel-ring"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(196, 164, 106, 1.0)", "secondaryAlbedo": "rgba(138, 112, 64, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.84}};
  mesh_ring_39.userData.explodeWithParent = null;
  node_ring_39.add(mesh_ring_39);
  meshes["ring"] = mesh_ring_39;
  colliders["ring"] = {"type": "sphere", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "ring bounds"};
  destructionGroups["ring"] ??= [];
  destructionGroups["ring"].push(node_ring_39);

  const attachment_ring_engraving_outer_40 = {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_ring_engraving_outer_40 = makeAttachmentEndpoint(attachment_ring_engraving_outer_40);
  const node_ring_engraving_outer_40 = new THREE.Group();
  node_ring_engraving_outer_40.name = "Ring engraving outer__pivot";
  node_ring_engraving_outer_40.scale.set(1, 1, 1);
  if (endpoint_ring_engraving_outer_40) {
    node_ring_engraving_outer_40.position.copy(endpoint_ring_engraving_outer_40.start);
    node_ring_engraving_outer_40.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_ring_engraving_outer_40.position.set(0.0, 0.0, 0.0);
    node_ring_engraving_outer_40.rotation.set(0.0, 0.0, 0.0);
  }
  node_ring_engraving_outer_40.userData.sculptComponent = {"id": "ring-engraving-outer", "name": "Ring engraving outer", "level": "micro", "role": "detail", "importance": 0.68, "confidence": 0.74, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Closed dark line follows the ring front face as shallow engraved relief.", "geometryDescriptor": {"topologyIntent": "Ring engraving outer reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[2.26548, 0.0, 0.0067], [2.26423, 0.01356, 0.0067], [2.26053, 0.0266, 0.0067], [2.25451, 0.03862, 0.0067], [2.24641, 0.04916, 0.0067], [2.23654, 0.0578, 0.0067], [2.22529, 0.06423, 0.0067], [2.21307, 0.06818, 0.0067], [2.20036, 0.06952, 0.0067], [2.18766, 0.06818, 0.0067], [2.17544, 0.06423, 0.0067], [2.16419, 0.0578, 0.0067], [2.15432, 0.04916, 0.0067], [2.14622, 0.03862, 0.0067], [2.1402, 0.0266, 0.0067], [2.1365, 0.01356, 0.0067], [2.13524, 0.0, 0.0067], [2.1365, -0.01356, 0.0067], [2.1402, -0.0266, 0.0067], [2.14622, -0.03862, 0.0067], [2.15432, -0.04916, 0.0067], [2.16419, -0.0578, 0.0067], [2.17544, -0.06423, 0.0067], [2.18766, -0.06818, 0.0067], [2.20036, -0.06952, 0.0067], [2.21307, -0.06818, 0.0067], [2.22529, -0.06423, 0.0067], [2.23654, -0.0578, 0.0067], [2.24641, -0.04916, 0.0067], [2.25451, -0.03862, 0.0067], [2.26053, -0.0266, 0.0067], [2.26423, -0.01356, 0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.74}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}}, "material": "gilt-engraving", "materialLayers": ["gilt-engraving"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["pommel-ring"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(111, 84, 39, 1.0)", "secondaryAlbedo": "rgba(79, 57, 26, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "ring", "ownerModule": "ring", "face": "front", "mergePolicy": "bake"};
  node_ring_engraving_outer_40.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}};
  node_ring_engraving_outer_40.userData.explodeWithParent = "ring";
  (nodes["root"] ?? root).add(node_ring_engraving_outer_40);
  nodes["ring-engraving-outer"] = node_ring_engraving_outer_40;
  const mesh_ring_engraving_outer_40Geometry = endpoint_ring_engraving_outer_40
    ? new THREE.CylinderGeometry(endpoint_ring_engraving_outer_40.endRadius, endpoint_ring_engraving_outer_40.baseRadius, endpoint_ring_engraving_outer_40.length, 32, 12)
    : buildTubeGeometry({"points": [[2.26548, 0.0, 0.0067], [2.26423, 0.01356, 0.0067], [2.26053, 0.0266, 0.0067], [2.25451, 0.03862, 0.0067], [2.24641, 0.04916, 0.0067], [2.23654, 0.0578, 0.0067], [2.22529, 0.06423, 0.0067], [2.21307, 0.06818, 0.0067], [2.20036, 0.06952, 0.0067], [2.18766, 0.06818, 0.0067], [2.17544, 0.06423, 0.0067], [2.16419, 0.0578, 0.0067], [2.15432, 0.04916, 0.0067], [2.14622, 0.03862, 0.0067], [2.1402, 0.0266, 0.0067], [2.1365, 0.01356, 0.0067], [2.13524, 0.0, 0.0067], [2.1365, -0.01356, 0.0067], [2.1402, -0.0266, 0.0067], [2.14622, -0.03862, 0.0067], [2.15432, -0.04916, 0.0067], [2.16419, -0.0578, 0.0067], [2.17544, -0.06423, 0.0067], [2.18766, -0.06818, 0.0067], [2.20036, -0.06952, 0.0067], [2.21307, -0.06818, 0.0067], [2.22529, -0.06423, 0.0067], [2.23654, -0.0578, 0.0067], [2.24641, -0.04916, 0.0067], [2.25451, -0.03862, 0.0067], [2.26053, -0.0266, 0.0067], [2.26423, -0.01356, 0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true});
  if (!endpoint_ring_engraving_outer_40) {
    mesh_ring_engraving_outer_40Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_ring_engraving_outer_40 = new THREE.Mesh(
    mesh_ring_engraving_outer_40Geometry,
    materialMap["gilt-engraving"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_ring_engraving_outer_40.name = "Ring engraving outer";
  if (endpoint_ring_engraving_outer_40) {
    mesh_ring_engraving_outer_40.position.copy(endpoint_ring_engraving_outer_40.midpoint);
    mesh_ring_engraving_outer_40.quaternion.copy(endpoint_ring_engraving_outer_40.quaternion);
  }
  mesh_ring_engraving_outer_40.castShadow = options.castShadow ?? true;
  mesh_ring_engraving_outer_40.receiveShadow = options.receiveShadow ?? true;
  mesh_ring_engraving_outer_40.userData.sculptComponent = {"id": "ring-engraving-outer", "name": "Ring engraving outer", "level": "micro", "role": "detail", "importance": 0.68, "confidence": 0.74, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Closed dark line follows the ring front face as shallow engraved relief.", "geometryDescriptor": {"topologyIntent": "Ring engraving outer reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[2.26548, 0.0, 0.0067], [2.26423, 0.01356, 0.0067], [2.26053, 0.0266, 0.0067], [2.25451, 0.03862, 0.0067], [2.24641, 0.04916, 0.0067], [2.23654, 0.0578, 0.0067], [2.22529, 0.06423, 0.0067], [2.21307, 0.06818, 0.0067], [2.20036, 0.06952, 0.0067], [2.18766, 0.06818, 0.0067], [2.17544, 0.06423, 0.0067], [2.16419, 0.0578, 0.0067], [2.15432, 0.04916, 0.0067], [2.14622, 0.03862, 0.0067], [2.1402, 0.0266, 0.0067], [2.1365, 0.01356, 0.0067], [2.13524, 0.0, 0.0067], [2.1365, -0.01356, 0.0067], [2.1402, -0.0266, 0.0067], [2.14622, -0.03862, 0.0067], [2.15432, -0.04916, 0.0067], [2.16419, -0.0578, 0.0067], [2.17544, -0.06423, 0.0067], [2.18766, -0.06818, 0.0067], [2.20036, -0.06952, 0.0067], [2.21307, -0.06818, 0.0067], [2.22529, -0.06423, 0.0067], [2.23654, -0.0578, 0.0067], [2.24641, -0.04916, 0.0067], [2.25451, -0.03862, 0.0067], [2.26053, -0.0266, 0.0067], [2.26423, -0.01356, 0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.74}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}}, "material": "gilt-engraving", "materialLayers": ["gilt-engraving"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["pommel-ring"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(111, 84, 39, 1.0)", "secondaryAlbedo": "rgba(79, 57, 26, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "ring", "ownerModule": "ring", "face": "front", "mergePolicy": "bake"};
  mesh_ring_engraving_outer_40.userData.explodeWithParent = "ring";
  node_ring_engraving_outer_40.add(mesh_ring_engraving_outer_40);
  meshes["ring-engraving-outer"] = mesh_ring_engraving_outer_40;
  colliders["ring-engraving-outer"] = null;
  destructionGroups["ring"] ??= [];
  destructionGroups["ring"].push(node_ring_engraving_outer_40);

  const attachment_ring_engraving_middle_41 = {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_ring_engraving_middle_41 = makeAttachmentEndpoint(attachment_ring_engraving_middle_41);
  const node_ring_engraving_middle_41 = new THREE.Group();
  node_ring_engraving_middle_41.name = "Ring engraving middle__pivot";
  node_ring_engraving_middle_41.scale.set(1, 1, 1);
  if (endpoint_ring_engraving_middle_41) {
    node_ring_engraving_middle_41.position.copy(endpoint_ring_engraving_middle_41.start);
    node_ring_engraving_middle_41.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_ring_engraving_middle_41.position.set(0.0, 0.0, 0.0);
    node_ring_engraving_middle_41.rotation.set(0.0, 0.0, 0.0);
  }
  node_ring_engraving_middle_41.userData.sculptComponent = {"id": "ring-engraving-middle", "name": "Ring engraving middle", "level": "micro", "role": "detail", "importance": 0.68, "confidence": 0.74, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Closed dark line follows the ring front face as shallow engraved relief.", "geometryDescriptor": {"topologyIntent": "Ring engraving middle reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[2.25459, 0.0, 0.0067], [2.25357, 0.0111, 0.0067], [2.25054, 0.02177, 0.0067], [2.24561, 0.0316, 0.0067], [2.23899, 0.04022, 0.0067], [2.23091, 0.04729, 0.0067], [2.2217, 0.05255, 0.0067], [2.21171, 0.05579, 0.0067], [2.20131, 0.05688, 0.0067], [2.19092, 0.05579, 0.0067], [2.18092, 0.05255, 0.0067], [2.17171, 0.04729, 0.0067], [2.16364, 0.04022, 0.0067], [2.15701, 0.0316, 0.0067], [2.15209, 0.02177, 0.0067], [2.14906, 0.0111, 0.0067], [2.14803, 0.0, 0.0067], [2.14906, -0.0111, 0.0067], [2.15209, -0.02177, 0.0067], [2.15701, -0.0316, 0.0067], [2.16364, -0.04022, 0.0067], [2.17171, -0.04729, 0.0067], [2.18092, -0.05255, 0.0067], [2.19092, -0.05579, 0.0067], [2.20131, -0.05688, 0.0067], [2.21171, -0.05579, 0.0067], [2.2217, -0.05255, 0.0067], [2.23091, -0.04729, 0.0067], [2.23899, -0.04022, 0.0067], [2.24561, -0.0316, 0.0067], [2.25054, -0.02177, 0.0067], [2.25357, -0.0111, 0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.74}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}}, "material": "gilt-engraving", "materialLayers": ["gilt-engraving"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["pommel-ring"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(111, 84, 39, 1.0)", "secondaryAlbedo": "rgba(79, 57, 26, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "ring", "ownerModule": "ring", "face": "front", "mergePolicy": "bake"};
  node_ring_engraving_middle_41.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}};
  node_ring_engraving_middle_41.userData.explodeWithParent = "ring";
  (nodes["root"] ?? root).add(node_ring_engraving_middle_41);
  nodes["ring-engraving-middle"] = node_ring_engraving_middle_41;
  const mesh_ring_engraving_middle_41Geometry = endpoint_ring_engraving_middle_41
    ? new THREE.CylinderGeometry(endpoint_ring_engraving_middle_41.endRadius, endpoint_ring_engraving_middle_41.baseRadius, endpoint_ring_engraving_middle_41.length, 32, 12)
    : buildTubeGeometry({"points": [[2.25459, 0.0, 0.0067], [2.25357, 0.0111, 0.0067], [2.25054, 0.02177, 0.0067], [2.24561, 0.0316, 0.0067], [2.23899, 0.04022, 0.0067], [2.23091, 0.04729, 0.0067], [2.2217, 0.05255, 0.0067], [2.21171, 0.05579, 0.0067], [2.20131, 0.05688, 0.0067], [2.19092, 0.05579, 0.0067], [2.18092, 0.05255, 0.0067], [2.17171, 0.04729, 0.0067], [2.16364, 0.04022, 0.0067], [2.15701, 0.0316, 0.0067], [2.15209, 0.02177, 0.0067], [2.14906, 0.0111, 0.0067], [2.14803, 0.0, 0.0067], [2.14906, -0.0111, 0.0067], [2.15209, -0.02177, 0.0067], [2.15701, -0.0316, 0.0067], [2.16364, -0.04022, 0.0067], [2.17171, -0.04729, 0.0067], [2.18092, -0.05255, 0.0067], [2.19092, -0.05579, 0.0067], [2.20131, -0.05688, 0.0067], [2.21171, -0.05579, 0.0067], [2.2217, -0.05255, 0.0067], [2.23091, -0.04729, 0.0067], [2.23899, -0.04022, 0.0067], [2.24561, -0.0316, 0.0067], [2.25054, -0.02177, 0.0067], [2.25357, -0.0111, 0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true});
  if (!endpoint_ring_engraving_middle_41) {
    mesh_ring_engraving_middle_41Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_ring_engraving_middle_41 = new THREE.Mesh(
    mesh_ring_engraving_middle_41Geometry,
    materialMap["gilt-engraving"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_ring_engraving_middle_41.name = "Ring engraving middle";
  if (endpoint_ring_engraving_middle_41) {
    mesh_ring_engraving_middle_41.position.copy(endpoint_ring_engraving_middle_41.midpoint);
    mesh_ring_engraving_middle_41.quaternion.copy(endpoint_ring_engraving_middle_41.quaternion);
  }
  mesh_ring_engraving_middle_41.castShadow = options.castShadow ?? true;
  mesh_ring_engraving_middle_41.receiveShadow = options.receiveShadow ?? true;
  mesh_ring_engraving_middle_41.userData.sculptComponent = {"id": "ring-engraving-middle", "name": "Ring engraving middle", "level": "micro", "role": "detail", "importance": 0.68, "confidence": 0.74, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Closed dark line follows the ring front face as shallow engraved relief.", "geometryDescriptor": {"topologyIntent": "Ring engraving middle reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[2.25459, 0.0, 0.0067], [2.25357, 0.0111, 0.0067], [2.25054, 0.02177, 0.0067], [2.24561, 0.0316, 0.0067], [2.23899, 0.04022, 0.0067], [2.23091, 0.04729, 0.0067], [2.2217, 0.05255, 0.0067], [2.21171, 0.05579, 0.0067], [2.20131, 0.05688, 0.0067], [2.19092, 0.05579, 0.0067], [2.18092, 0.05255, 0.0067], [2.17171, 0.04729, 0.0067], [2.16364, 0.04022, 0.0067], [2.15701, 0.0316, 0.0067], [2.15209, 0.02177, 0.0067], [2.14906, 0.0111, 0.0067], [2.14803, 0.0, 0.0067], [2.14906, -0.0111, 0.0067], [2.15209, -0.02177, 0.0067], [2.15701, -0.0316, 0.0067], [2.16364, -0.04022, 0.0067], [2.17171, -0.04729, 0.0067], [2.18092, -0.05255, 0.0067], [2.19092, -0.05579, 0.0067], [2.20131, -0.05688, 0.0067], [2.21171, -0.05579, 0.0067], [2.2217, -0.05255, 0.0067], [2.23091, -0.04729, 0.0067], [2.23899, -0.04022, 0.0067], [2.24561, -0.0316, 0.0067], [2.25054, -0.02177, 0.0067], [2.25357, -0.0111, 0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.74}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}}, "material": "gilt-engraving", "materialLayers": ["gilt-engraving"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["pommel-ring"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(111, 84, 39, 1.0)", "secondaryAlbedo": "rgba(79, 57, 26, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "ring", "ownerModule": "ring", "face": "front", "mergePolicy": "bake"};
  mesh_ring_engraving_middle_41.userData.explodeWithParent = "ring";
  node_ring_engraving_middle_41.add(mesh_ring_engraving_middle_41);
  meshes["ring-engraving-middle"] = mesh_ring_engraving_middle_41;
  colliders["ring-engraving-middle"] = null;
  destructionGroups["ring"] ??= [];
  destructionGroups["ring"].push(node_ring_engraving_middle_41);

  const attachment_ring_engraving_inner_42 = {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_ring_engraving_inner_42 = makeAttachmentEndpoint(attachment_ring_engraving_inner_42);
  const node_ring_engraving_inner_42 = new THREE.Group();
  node_ring_engraving_inner_42.name = "Ring engraving inner__pivot";
  node_ring_engraving_inner_42.scale.set(1, 1, 1);
  if (endpoint_ring_engraving_inner_42) {
    node_ring_engraving_inner_42.position.copy(endpoint_ring_engraving_inner_42.start);
    node_ring_engraving_inner_42.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_ring_engraving_inner_42.position.set(0.0, 0.0, 0.0);
    node_ring_engraving_inner_42.rotation.set(0.0, 0.0, 0.0);
  }
  node_ring_engraving_inner_42.userData.sculptComponent = {"id": "ring-engraving-inner", "name": "Ring engraving inner", "level": "micro", "role": "detail", "importance": 0.68, "confidence": 0.74, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Closed dark line follows the ring front face as shallow engraved relief.", "geometryDescriptor": {"topologyIntent": "Ring engraving inner reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[2.24506, 0.0, 0.0067], [2.24424, 0.00894, 0.0067], [2.24179, 0.01753, 0.0067], [2.23783, 0.02546, 0.0067], [2.23249, 0.0324, 0.0067], [2.22599, 0.0381, 0.0067], [2.21857, 0.04233, 0.0067], [2.21051, 0.04494, 0.0067], [2.20214, 0.04582, 0.0067], [2.19377, 0.04494, 0.0067], [2.18572, 0.04233, 0.0067], [2.1783, 0.0381, 0.0067], [2.17179, 0.0324, 0.0067], [2.16645, 0.02546, 0.0067], [2.16249, 0.01753, 0.0067], [2.16005, 0.00894, 0.0067], [2.15922, 0.0, 0.0067], [2.16005, -0.00894, 0.0067], [2.16249, -0.01753, 0.0067], [2.16645, -0.02546, 0.0067], [2.17179, -0.0324, 0.0067], [2.1783, -0.0381, 0.0067], [2.18572, -0.04233, 0.0067], [2.19377, -0.04494, 0.0067], [2.20214, -0.04582, 0.0067], [2.21051, -0.04494, 0.0067], [2.21857, -0.04233, 0.0067], [2.22599, -0.0381, 0.0067], [2.23249, -0.0324, 0.0067], [2.23783, -0.02546, 0.0067], [2.24179, -0.01753, 0.0067], [2.24424, -0.00894, 0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.74}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}}, "material": "gilt-engraving", "materialLayers": ["gilt-engraving"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["pommel-ring"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(111, 84, 39, 1.0)", "secondaryAlbedo": "rgba(79, 57, 26, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "ring", "ownerModule": "ring", "face": "front", "mergePolicy": "bake"};
  node_ring_engraving_inner_42.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}};
  node_ring_engraving_inner_42.userData.explodeWithParent = "ring";
  (nodes["root"] ?? root).add(node_ring_engraving_inner_42);
  nodes["ring-engraving-inner"] = node_ring_engraving_inner_42;
  const mesh_ring_engraving_inner_42Geometry = endpoint_ring_engraving_inner_42
    ? new THREE.CylinderGeometry(endpoint_ring_engraving_inner_42.endRadius, endpoint_ring_engraving_inner_42.baseRadius, endpoint_ring_engraving_inner_42.length, 32, 12)
    : buildTubeGeometry({"points": [[2.24506, 0.0, 0.0067], [2.24424, 0.00894, 0.0067], [2.24179, 0.01753, 0.0067], [2.23783, 0.02546, 0.0067], [2.23249, 0.0324, 0.0067], [2.22599, 0.0381, 0.0067], [2.21857, 0.04233, 0.0067], [2.21051, 0.04494, 0.0067], [2.20214, 0.04582, 0.0067], [2.19377, 0.04494, 0.0067], [2.18572, 0.04233, 0.0067], [2.1783, 0.0381, 0.0067], [2.17179, 0.0324, 0.0067], [2.16645, 0.02546, 0.0067], [2.16249, 0.01753, 0.0067], [2.16005, 0.00894, 0.0067], [2.15922, 0.0, 0.0067], [2.16005, -0.00894, 0.0067], [2.16249, -0.01753, 0.0067], [2.16645, -0.02546, 0.0067], [2.17179, -0.0324, 0.0067], [2.1783, -0.0381, 0.0067], [2.18572, -0.04233, 0.0067], [2.19377, -0.04494, 0.0067], [2.20214, -0.04582, 0.0067], [2.21051, -0.04494, 0.0067], [2.21857, -0.04233, 0.0067], [2.22599, -0.0381, 0.0067], [2.23249, -0.0324, 0.0067], [2.23783, -0.02546, 0.0067], [2.24179, -0.01753, 0.0067], [2.24424, -0.00894, 0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true});
  if (!endpoint_ring_engraving_inner_42) {
    mesh_ring_engraving_inner_42Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_ring_engraving_inner_42 = new THREE.Mesh(
    mesh_ring_engraving_inner_42Geometry,
    materialMap["gilt-engraving"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_ring_engraving_inner_42.name = "Ring engraving inner";
  if (endpoint_ring_engraving_inner_42) {
    mesh_ring_engraving_inner_42.position.copy(endpoint_ring_engraving_inner_42.midpoint);
    mesh_ring_engraving_inner_42.quaternion.copy(endpoint_ring_engraving_inner_42.quaternion);
  }
  mesh_ring_engraving_inner_42.castShadow = options.castShadow ?? true;
  mesh_ring_engraving_inner_42.receiveShadow = options.receiveShadow ?? true;
  mesh_ring_engraving_inner_42.userData.sculptComponent = {"id": "ring-engraving-inner", "name": "Ring engraving inner", "level": "micro", "role": "detail", "importance": 0.68, "confidence": 0.74, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Closed dark line follows the ring front face as shallow engraved relief.", "geometryDescriptor": {"topologyIntent": "Ring engraving inner reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[2.24506, 0.0, 0.0067], [2.24424, 0.00894, 0.0067], [2.24179, 0.01753, 0.0067], [2.23783, 0.02546, 0.0067], [2.23249, 0.0324, 0.0067], [2.22599, 0.0381, 0.0067], [2.21857, 0.04233, 0.0067], [2.21051, 0.04494, 0.0067], [2.20214, 0.04582, 0.0067], [2.19377, 0.04494, 0.0067], [2.18572, 0.04233, 0.0067], [2.1783, 0.0381, 0.0067], [2.17179, 0.0324, 0.0067], [2.16645, 0.02546, 0.0067], [2.16249, 0.01753, 0.0067], [2.16005, 0.00894, 0.0067], [2.15922, 0.0, 0.0067], [2.16005, -0.00894, 0.0067], [2.16249, -0.01753, 0.0067], [2.16645, -0.02546, 0.0067], [2.17179, -0.0324, 0.0067], [2.1783, -0.0381, 0.0067], [2.18572, -0.04233, 0.0067], [2.19377, -0.04494, 0.0067], [2.20214, -0.04582, 0.0067], [2.21051, -0.04494, 0.0067], [2.21857, -0.04233, 0.0067], [2.22599, -0.0381, 0.0067], [2.23249, -0.0324, 0.0067], [2.23783, -0.02546, 0.0067], [2.24179, -0.01753, 0.0067], [2.24424, -0.00894, 0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.74}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}}, "material": "gilt-engraving", "materialLayers": ["gilt-engraving"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["pommel-ring"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(111, 84, 39, 1.0)", "secondaryAlbedo": "rgba(79, 57, 26, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "ring", "ownerModule": "ring", "face": "front", "mergePolicy": "bake"};
  mesh_ring_engraving_inner_42.userData.explodeWithParent = "ring";
  node_ring_engraving_inner_42.add(mesh_ring_engraving_inner_42);
  meshes["ring-engraving-inner"] = mesh_ring_engraving_inner_42;
  colliders["ring-engraving-inner"] = null;
  destructionGroups["ring"] ??= [];
  destructionGroups["ring"].push(node_ring_engraving_inner_42);

  const attachment_ring_engraving_back_outer_43 = {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_ring_engraving_back_outer_43 = makeAttachmentEndpoint(attachment_ring_engraving_back_outer_43);
  const node_ring_engraving_back_outer_43 = new THREE.Group();
  node_ring_engraving_back_outer_43.name = "Ring back engraving outer__pivot";
  node_ring_engraving_back_outer_43.scale.set(1, 1, 1);
  if (endpoint_ring_engraving_back_outer_43) {
    node_ring_engraving_back_outer_43.position.copy(endpoint_ring_engraving_back_outer_43.start);
    node_ring_engraving_back_outer_43.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_ring_engraving_back_outer_43.position.set(0.0, 0.0, 0.0);
    node_ring_engraving_back_outer_43.rotation.set(0.0, 0.0, 0.0);
  }
  node_ring_engraving_back_outer_43.userData.sculptComponent = {"id": "ring-engraving-back-outer", "name": "Ring back engraving outer", "level": "micro", "role": "detail", "importance": 0.68, "confidence": 0.74, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Closed dark line follows the ring back face as shallow engraved relief.", "geometryDescriptor": {"topologyIntent": "Ring back engraving outer reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[2.26548, 0.0, -0.0067], [2.26423, 0.01356, -0.0067], [2.26053, 0.0266, -0.0067], [2.25451, 0.03862, -0.0067], [2.24641, 0.04916, -0.0067], [2.23654, 0.0578, -0.0067], [2.22529, 0.06423, -0.0067], [2.21307, 0.06818, -0.0067], [2.20036, 0.06952, -0.0067], [2.18766, 0.06818, -0.0067], [2.17544, 0.06423, -0.0067], [2.16419, 0.0578, -0.0067], [2.15432, 0.04916, -0.0067], [2.14622, 0.03862, -0.0067], [2.1402, 0.0266, -0.0067], [2.1365, 0.01356, -0.0067], [2.13524, 0.0, -0.0067], [2.1365, -0.01356, -0.0067], [2.1402, -0.0266, -0.0067], [2.14622, -0.03862, -0.0067], [2.15432, -0.04916, -0.0067], [2.16419, -0.0578, -0.0067], [2.17544, -0.06423, -0.0067], [2.18766, -0.06818, -0.0067], [2.20036, -0.06952, -0.0067], [2.21307, -0.06818, -0.0067], [2.22529, -0.06423, -0.0067], [2.23654, -0.0578, -0.0067], [2.24641, -0.04916, -0.0067], [2.25451, -0.03862, -0.0067], [2.26053, -0.0266, -0.0067], [2.26423, -0.01356, -0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.74}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}}, "material": "gilt-engraving", "materialLayers": ["gilt-engraving"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["pommel-ring"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(111, 84, 39, 1.0)", "secondaryAlbedo": "rgba(79, 57, 26, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "ring", "ownerModule": "ring", "face": "back", "mergePolicy": "bake"};
  node_ring_engraving_back_outer_43.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}};
  node_ring_engraving_back_outer_43.userData.explodeWithParent = "ring";
  (nodes["root"] ?? root).add(node_ring_engraving_back_outer_43);
  nodes["ring-engraving-back-outer"] = node_ring_engraving_back_outer_43;
  const mesh_ring_engraving_back_outer_43Geometry = endpoint_ring_engraving_back_outer_43
    ? new THREE.CylinderGeometry(endpoint_ring_engraving_back_outer_43.endRadius, endpoint_ring_engraving_back_outer_43.baseRadius, endpoint_ring_engraving_back_outer_43.length, 32, 12)
    : buildTubeGeometry({"points": [[2.26548, 0.0, -0.0067], [2.26423, 0.01356, -0.0067], [2.26053, 0.0266, -0.0067], [2.25451, 0.03862, -0.0067], [2.24641, 0.04916, -0.0067], [2.23654, 0.0578, -0.0067], [2.22529, 0.06423, -0.0067], [2.21307, 0.06818, -0.0067], [2.20036, 0.06952, -0.0067], [2.18766, 0.06818, -0.0067], [2.17544, 0.06423, -0.0067], [2.16419, 0.0578, -0.0067], [2.15432, 0.04916, -0.0067], [2.14622, 0.03862, -0.0067], [2.1402, 0.0266, -0.0067], [2.1365, 0.01356, -0.0067], [2.13524, 0.0, -0.0067], [2.1365, -0.01356, -0.0067], [2.1402, -0.0266, -0.0067], [2.14622, -0.03862, -0.0067], [2.15432, -0.04916, -0.0067], [2.16419, -0.0578, -0.0067], [2.17544, -0.06423, -0.0067], [2.18766, -0.06818, -0.0067], [2.20036, -0.06952, -0.0067], [2.21307, -0.06818, -0.0067], [2.22529, -0.06423, -0.0067], [2.23654, -0.0578, -0.0067], [2.24641, -0.04916, -0.0067], [2.25451, -0.03862, -0.0067], [2.26053, -0.0266, -0.0067], [2.26423, -0.01356, -0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true});
  if (!endpoint_ring_engraving_back_outer_43) {
    mesh_ring_engraving_back_outer_43Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_ring_engraving_back_outer_43 = new THREE.Mesh(
    mesh_ring_engraving_back_outer_43Geometry,
    materialMap["gilt-engraving"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_ring_engraving_back_outer_43.name = "Ring back engraving outer";
  if (endpoint_ring_engraving_back_outer_43) {
    mesh_ring_engraving_back_outer_43.position.copy(endpoint_ring_engraving_back_outer_43.midpoint);
    mesh_ring_engraving_back_outer_43.quaternion.copy(endpoint_ring_engraving_back_outer_43.quaternion);
  }
  mesh_ring_engraving_back_outer_43.castShadow = options.castShadow ?? true;
  mesh_ring_engraving_back_outer_43.receiveShadow = options.receiveShadow ?? true;
  mesh_ring_engraving_back_outer_43.userData.sculptComponent = {"id": "ring-engraving-back-outer", "name": "Ring back engraving outer", "level": "micro", "role": "detail", "importance": 0.68, "confidence": 0.74, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Closed dark line follows the ring back face as shallow engraved relief.", "geometryDescriptor": {"topologyIntent": "Ring back engraving outer reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[2.26548, 0.0, -0.0067], [2.26423, 0.01356, -0.0067], [2.26053, 0.0266, -0.0067], [2.25451, 0.03862, -0.0067], [2.24641, 0.04916, -0.0067], [2.23654, 0.0578, -0.0067], [2.22529, 0.06423, -0.0067], [2.21307, 0.06818, -0.0067], [2.20036, 0.06952, -0.0067], [2.18766, 0.06818, -0.0067], [2.17544, 0.06423, -0.0067], [2.16419, 0.0578, -0.0067], [2.15432, 0.04916, -0.0067], [2.14622, 0.03862, -0.0067], [2.1402, 0.0266, -0.0067], [2.1365, 0.01356, -0.0067], [2.13524, 0.0, -0.0067], [2.1365, -0.01356, -0.0067], [2.1402, -0.0266, -0.0067], [2.14622, -0.03862, -0.0067], [2.15432, -0.04916, -0.0067], [2.16419, -0.0578, -0.0067], [2.17544, -0.06423, -0.0067], [2.18766, -0.06818, -0.0067], [2.20036, -0.06952, -0.0067], [2.21307, -0.06818, -0.0067], [2.22529, -0.06423, -0.0067], [2.23654, -0.0578, -0.0067], [2.24641, -0.04916, -0.0067], [2.25451, -0.03862, -0.0067], [2.26053, -0.0266, -0.0067], [2.26423, -0.01356, -0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.74}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}}, "material": "gilt-engraving", "materialLayers": ["gilt-engraving"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["pommel-ring"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(111, 84, 39, 1.0)", "secondaryAlbedo": "rgba(79, 57, 26, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "ring", "ownerModule": "ring", "face": "back", "mergePolicy": "bake"};
  mesh_ring_engraving_back_outer_43.userData.explodeWithParent = "ring";
  node_ring_engraving_back_outer_43.add(mesh_ring_engraving_back_outer_43);
  meshes["ring-engraving-back-outer"] = mesh_ring_engraving_back_outer_43;
  colliders["ring-engraving-back-outer"] = null;
  destructionGroups["ring"] ??= [];
  destructionGroups["ring"].push(node_ring_engraving_back_outer_43);

  const attachment_ring_engraving_back_middle_44 = {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_ring_engraving_back_middle_44 = makeAttachmentEndpoint(attachment_ring_engraving_back_middle_44);
  const node_ring_engraving_back_middle_44 = new THREE.Group();
  node_ring_engraving_back_middle_44.name = "Ring back engraving middle__pivot";
  node_ring_engraving_back_middle_44.scale.set(1, 1, 1);
  if (endpoint_ring_engraving_back_middle_44) {
    node_ring_engraving_back_middle_44.position.copy(endpoint_ring_engraving_back_middle_44.start);
    node_ring_engraving_back_middle_44.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_ring_engraving_back_middle_44.position.set(0.0, 0.0, 0.0);
    node_ring_engraving_back_middle_44.rotation.set(0.0, 0.0, 0.0);
  }
  node_ring_engraving_back_middle_44.userData.sculptComponent = {"id": "ring-engraving-back-middle", "name": "Ring back engraving middle", "level": "micro", "role": "detail", "importance": 0.68, "confidence": 0.74, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Closed dark line follows the ring back face as shallow engraved relief.", "geometryDescriptor": {"topologyIntent": "Ring back engraving middle reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[2.25459, 0.0, -0.0067], [2.25357, 0.0111, -0.0067], [2.25054, 0.02177, -0.0067], [2.24561, 0.0316, -0.0067], [2.23899, 0.04022, -0.0067], [2.23091, 0.04729, -0.0067], [2.2217, 0.05255, -0.0067], [2.21171, 0.05579, -0.0067], [2.20131, 0.05688, -0.0067], [2.19092, 0.05579, -0.0067], [2.18092, 0.05255, -0.0067], [2.17171, 0.04729, -0.0067], [2.16364, 0.04022, -0.0067], [2.15701, 0.0316, -0.0067], [2.15209, 0.02177, -0.0067], [2.14906, 0.0111, -0.0067], [2.14803, 0.0, -0.0067], [2.14906, -0.0111, -0.0067], [2.15209, -0.02177, -0.0067], [2.15701, -0.0316, -0.0067], [2.16364, -0.04022, -0.0067], [2.17171, -0.04729, -0.0067], [2.18092, -0.05255, -0.0067], [2.19092, -0.05579, -0.0067], [2.20131, -0.05688, -0.0067], [2.21171, -0.05579, -0.0067], [2.2217, -0.05255, -0.0067], [2.23091, -0.04729, -0.0067], [2.23899, -0.04022, -0.0067], [2.24561, -0.0316, -0.0067], [2.25054, -0.02177, -0.0067], [2.25357, -0.0111, -0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.74}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}}, "material": "gilt-engraving", "materialLayers": ["gilt-engraving"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["pommel-ring"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(111, 84, 39, 1.0)", "secondaryAlbedo": "rgba(79, 57, 26, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "ring", "ownerModule": "ring", "face": "back", "mergePolicy": "bake"};
  node_ring_engraving_back_middle_44.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}};
  node_ring_engraving_back_middle_44.userData.explodeWithParent = "ring";
  (nodes["root"] ?? root).add(node_ring_engraving_back_middle_44);
  nodes["ring-engraving-back-middle"] = node_ring_engraving_back_middle_44;
  const mesh_ring_engraving_back_middle_44Geometry = endpoint_ring_engraving_back_middle_44
    ? new THREE.CylinderGeometry(endpoint_ring_engraving_back_middle_44.endRadius, endpoint_ring_engraving_back_middle_44.baseRadius, endpoint_ring_engraving_back_middle_44.length, 32, 12)
    : buildTubeGeometry({"points": [[2.25459, 0.0, -0.0067], [2.25357, 0.0111, -0.0067], [2.25054, 0.02177, -0.0067], [2.24561, 0.0316, -0.0067], [2.23899, 0.04022, -0.0067], [2.23091, 0.04729, -0.0067], [2.2217, 0.05255, -0.0067], [2.21171, 0.05579, -0.0067], [2.20131, 0.05688, -0.0067], [2.19092, 0.05579, -0.0067], [2.18092, 0.05255, -0.0067], [2.17171, 0.04729, -0.0067], [2.16364, 0.04022, -0.0067], [2.15701, 0.0316, -0.0067], [2.15209, 0.02177, -0.0067], [2.14906, 0.0111, -0.0067], [2.14803, 0.0, -0.0067], [2.14906, -0.0111, -0.0067], [2.15209, -0.02177, -0.0067], [2.15701, -0.0316, -0.0067], [2.16364, -0.04022, -0.0067], [2.17171, -0.04729, -0.0067], [2.18092, -0.05255, -0.0067], [2.19092, -0.05579, -0.0067], [2.20131, -0.05688, -0.0067], [2.21171, -0.05579, -0.0067], [2.2217, -0.05255, -0.0067], [2.23091, -0.04729, -0.0067], [2.23899, -0.04022, -0.0067], [2.24561, -0.0316, -0.0067], [2.25054, -0.02177, -0.0067], [2.25357, -0.0111, -0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true});
  if (!endpoint_ring_engraving_back_middle_44) {
    mesh_ring_engraving_back_middle_44Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_ring_engraving_back_middle_44 = new THREE.Mesh(
    mesh_ring_engraving_back_middle_44Geometry,
    materialMap["gilt-engraving"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_ring_engraving_back_middle_44.name = "Ring back engraving middle";
  if (endpoint_ring_engraving_back_middle_44) {
    mesh_ring_engraving_back_middle_44.position.copy(endpoint_ring_engraving_back_middle_44.midpoint);
    mesh_ring_engraving_back_middle_44.quaternion.copy(endpoint_ring_engraving_back_middle_44.quaternion);
  }
  mesh_ring_engraving_back_middle_44.castShadow = options.castShadow ?? true;
  mesh_ring_engraving_back_middle_44.receiveShadow = options.receiveShadow ?? true;
  mesh_ring_engraving_back_middle_44.userData.sculptComponent = {"id": "ring-engraving-back-middle", "name": "Ring back engraving middle", "level": "micro", "role": "detail", "importance": 0.68, "confidence": 0.74, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Closed dark line follows the ring back face as shallow engraved relief.", "geometryDescriptor": {"topologyIntent": "Ring back engraving middle reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[2.25459, 0.0, -0.0067], [2.25357, 0.0111, -0.0067], [2.25054, 0.02177, -0.0067], [2.24561, 0.0316, -0.0067], [2.23899, 0.04022, -0.0067], [2.23091, 0.04729, -0.0067], [2.2217, 0.05255, -0.0067], [2.21171, 0.05579, -0.0067], [2.20131, 0.05688, -0.0067], [2.19092, 0.05579, -0.0067], [2.18092, 0.05255, -0.0067], [2.17171, 0.04729, -0.0067], [2.16364, 0.04022, -0.0067], [2.15701, 0.0316, -0.0067], [2.15209, 0.02177, -0.0067], [2.14906, 0.0111, -0.0067], [2.14803, 0.0, -0.0067], [2.14906, -0.0111, -0.0067], [2.15209, -0.02177, -0.0067], [2.15701, -0.0316, -0.0067], [2.16364, -0.04022, -0.0067], [2.17171, -0.04729, -0.0067], [2.18092, -0.05255, -0.0067], [2.19092, -0.05579, -0.0067], [2.20131, -0.05688, -0.0067], [2.21171, -0.05579, -0.0067], [2.2217, -0.05255, -0.0067], [2.23091, -0.04729, -0.0067], [2.23899, -0.04022, -0.0067], [2.24561, -0.0316, -0.0067], [2.25054, -0.02177, -0.0067], [2.25357, -0.0111, -0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.74}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}}, "material": "gilt-engraving", "materialLayers": ["gilt-engraving"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["pommel-ring"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(111, 84, 39, 1.0)", "secondaryAlbedo": "rgba(79, 57, 26, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "ring", "ownerModule": "ring", "face": "back", "mergePolicy": "bake"};
  mesh_ring_engraving_back_middle_44.userData.explodeWithParent = "ring";
  node_ring_engraving_back_middle_44.add(mesh_ring_engraving_back_middle_44);
  meshes["ring-engraving-back-middle"] = mesh_ring_engraving_back_middle_44;
  colliders["ring-engraving-back-middle"] = null;
  destructionGroups["ring"] ??= [];
  destructionGroups["ring"].push(node_ring_engraving_back_middle_44);

  const attachment_ring_engraving_back_inner_45 = {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0};
  const endpoint_ring_engraving_back_inner_45 = makeAttachmentEndpoint(attachment_ring_engraving_back_inner_45);
  const node_ring_engraving_back_inner_45 = new THREE.Group();
  node_ring_engraving_back_inner_45.name = "Ring back engraving inner__pivot";
  node_ring_engraving_back_inner_45.scale.set(1, 1, 1);
  if (endpoint_ring_engraving_back_inner_45) {
    node_ring_engraving_back_inner_45.position.copy(endpoint_ring_engraving_back_inner_45.start);
    node_ring_engraving_back_inner_45.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_ring_engraving_back_inner_45.position.set(0.0, 0.0, 0.0);
    node_ring_engraving_back_inner_45.rotation.set(0.0, 0.0, 0.0);
  }
  node_ring_engraving_back_inner_45.userData.sculptComponent = {"id": "ring-engraving-back-inner", "name": "Ring back engraving inner", "level": "micro", "role": "detail", "importance": 0.68, "confidence": 0.74, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Closed dark line follows the ring back face as shallow engraved relief.", "geometryDescriptor": {"topologyIntent": "Ring back engraving inner reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[2.24506, 0.0, -0.0067], [2.24424, 0.00894, -0.0067], [2.24179, 0.01753, -0.0067], [2.23783, 0.02546, -0.0067], [2.23249, 0.0324, -0.0067], [2.22599, 0.0381, -0.0067], [2.21857, 0.04233, -0.0067], [2.21051, 0.04494, -0.0067], [2.20214, 0.04582, -0.0067], [2.19377, 0.04494, -0.0067], [2.18572, 0.04233, -0.0067], [2.1783, 0.0381, -0.0067], [2.17179, 0.0324, -0.0067], [2.16645, 0.02546, -0.0067], [2.16249, 0.01753, -0.0067], [2.16005, 0.00894, -0.0067], [2.15922, 0.0, -0.0067], [2.16005, -0.00894, -0.0067], [2.16249, -0.01753, -0.0067], [2.16645, -0.02546, -0.0067], [2.17179, -0.0324, -0.0067], [2.1783, -0.0381, -0.0067], [2.18572, -0.04233, -0.0067], [2.19377, -0.04494, -0.0067], [2.20214, -0.04582, -0.0067], [2.21051, -0.04494, -0.0067], [2.21857, -0.04233, -0.0067], [2.22599, -0.0381, -0.0067], [2.23249, -0.0324, -0.0067], [2.23783, -0.02546, -0.0067], [2.24179, -0.01753, -0.0067], [2.24424, -0.00894, -0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.74}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}}, "material": "gilt-engraving", "materialLayers": ["gilt-engraving"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["pommel-ring"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(111, 84, 39, 1.0)", "secondaryAlbedo": "rgba(79, 57, 26, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "ring", "ownerModule": "ring", "face": "back", "mergePolicy": "bake"};
  node_ring_engraving_back_inner_45.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}};
  node_ring_engraving_back_inner_45.userData.explodeWithParent = "ring";
  (nodes["root"] ?? root).add(node_ring_engraving_back_inner_45);
  nodes["ring-engraving-back-inner"] = node_ring_engraving_back_inner_45;
  const mesh_ring_engraving_back_inner_45Geometry = endpoint_ring_engraving_back_inner_45
    ? new THREE.CylinderGeometry(endpoint_ring_engraving_back_inner_45.endRadius, endpoint_ring_engraving_back_inner_45.baseRadius, endpoint_ring_engraving_back_inner_45.length, 32, 12)
    : buildTubeGeometry({"points": [[2.24506, 0.0, -0.0067], [2.24424, 0.00894, -0.0067], [2.24179, 0.01753, -0.0067], [2.23783, 0.02546, -0.0067], [2.23249, 0.0324, -0.0067], [2.22599, 0.0381, -0.0067], [2.21857, 0.04233, -0.0067], [2.21051, 0.04494, -0.0067], [2.20214, 0.04582, -0.0067], [2.19377, 0.04494, -0.0067], [2.18572, 0.04233, -0.0067], [2.1783, 0.0381, -0.0067], [2.17179, 0.0324, -0.0067], [2.16645, 0.02546, -0.0067], [2.16249, 0.01753, -0.0067], [2.16005, 0.00894, -0.0067], [2.15922, 0.0, -0.0067], [2.16005, -0.00894, -0.0067], [2.16249, -0.01753, -0.0067], [2.16645, -0.02546, -0.0067], [2.17179, -0.0324, -0.0067], [2.1783, -0.0381, -0.0067], [2.18572, -0.04233, -0.0067], [2.19377, -0.04494, -0.0067], [2.20214, -0.04582, -0.0067], [2.21051, -0.04494, -0.0067], [2.21857, -0.04233, -0.0067], [2.22599, -0.0381, -0.0067], [2.23249, -0.0324, -0.0067], [2.23783, -0.02546, -0.0067], [2.24179, -0.01753, -0.0067], [2.24424, -0.00894, -0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true});
  if (!endpoint_ring_engraving_back_inner_45) {
    mesh_ring_engraving_back_inner_45Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_ring_engraving_back_inner_45 = new THREE.Mesh(
    mesh_ring_engraving_back_inner_45Geometry,
    materialMap["gilt-engraving"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_ring_engraving_back_inner_45.name = "Ring back engraving inner";
  if (endpoint_ring_engraving_back_inner_45) {
    mesh_ring_engraving_back_inner_45.position.copy(endpoint_ring_engraving_back_inner_45.midpoint);
    mesh_ring_engraving_back_inner_45.quaternion.copy(endpoint_ring_engraving_back_inner_45.quaternion);
  }
  mesh_ring_engraving_back_inner_45.castShadow = options.castShadow ?? true;
  mesh_ring_engraving_back_inner_45.receiveShadow = options.receiveShadow ?? true;
  mesh_ring_engraving_back_inner_45.userData.sculptComponent = {"id": "ring-engraving-back-inner", "name": "Ring back engraving inner", "level": "micro", "role": "detail", "importance": 0.68, "confidence": 0.74, "primitive": "tube", "topologyClass": "continuous-sculpt", "topologyRationale": "Closed dark line follows the ring back face as shallow engraved relief.", "geometryDescriptor": {"topologyIntent": "Ring back engraving inner reconstruction", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "vertex normals from generated geometry", "tubePath": {"points": [[2.24506, 0.0, -0.0067], [2.24424, 0.00894, -0.0067], [2.24179, 0.01753, -0.0067], [2.23783, 0.02546, -0.0067], [2.23249, 0.0324, -0.0067], [2.22599, 0.0381, -0.0067], [2.21857, 0.04233, -0.0067], [2.21051, 0.04494, -0.0067], [2.20214, 0.04582, -0.0067], [2.19377, 0.04494, -0.0067], [2.18572, 0.04233, -0.0067], [2.1783, 0.0381, -0.0067], [2.17179, 0.0324, -0.0067], [2.16645, 0.02546, -0.0067], [2.16249, 0.01753, -0.0067], [2.16005, 0.00894, -0.0067], [2.15922, 0.0, -0.0067], [2.16005, -0.00894, -0.0067], [2.16249, -0.01753, -0.0067], [2.16645, -0.02546, -0.0067], [2.17179, -0.0324, -0.0067], [2.1783, -0.0381, -0.0067], [2.18572, -0.04233, -0.0067], [2.19377, -0.04494, -0.0067], [2.20214, -0.04582, -0.0067], [2.21051, -0.04494, -0.0067], [2.21857, -0.04233, -0.0067], [2.22599, -0.0381, -0.0067], [2.23249, -0.0324, -0.0067], [2.23783, -0.02546, -0.0067], [2.24179, -0.01753, -0.0067], [2.24424, -0.00894, -0.0067]], "radius": 0.0011, "radialSegments": 5, "closed": true}}, "parent": "root", "attachment": {"parentId": "root", "parentSocket": "pommel-anchor", "localStart": [0.0, 0.0, 0.0], "localEnd": [0.0, 0.0, 0.0], "contactType": "sleeve", "overlap": 0.001, "gapTolerance": 0.004, "embedDepth": 0.0}, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.74}, "transform": {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": true}, "sockets": [], "collider": null, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "gilt-engraving"}}, "material": "gilt-engraving", "materialLayers": ["gilt-engraving"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.28, "microRoughness": 0.18, "bumpAmplitude": 0.004, "normalPattern": "fine-grind-lines", "displacementPattern": "none", "occlusionPattern": "contact at fittings", "edgeWearPattern": "brighter steel at the cutting edge", "notes": "Orthographic illustration: polished steel, not excavated rust."}, "evidenceRefs": ["pommel-ring"], "details": [], "fidelityTier": "blockout", "colorMaterialRecipe": {"dominantAlbedo": "rgba(111, 84, 39, 1.0)", "secondaryAlbedo": "rgba(79, 57, 26, 1.0)", "materialClass": "metal", "materialClassConfidence": 0.7}, "explodeWithParent": "ring", "ownerModule": "ring", "face": "back", "mergePolicy": "bake"};
  mesh_ring_engraving_back_inner_45.userData.explodeWithParent = "ring";
  node_ring_engraving_back_inner_45.add(mesh_ring_engraving_back_inner_45);
  meshes["ring-engraving-back-inner"] = mesh_ring_engraving_back_inner_45;
  colliders["ring-engraving-back-inner"] = null;
  destructionGroups["ring"] ??= [];
  destructionGroups["ring"].push(node_ring_engraving_back_inner_45);

  root.userData.sculptRuntime = { nodes, meshes, sockets, colliders, destructionGroups } satisfies ProceduralModelRuntime;
  root.userData.lookDevTargets = {"qualityPriority": "stylized-approximate", "materialPass": {"albedoPaletteRequired": true, "roughnessVariationRequired": true, "normalOrBumpRequired": true, "localOverridesRequired": true, "minimumTextureResolution": 1024, "preferredTextureResolution": 2048, "independentMapChannels": ["albedo", "roughness", "height", "normal", "ambient-occlusion"], "requiredSurfaceFrequencyBands": ["macro", "meso", "micro"], "geometryReliefRequiredWhenSilhouetteAffected": true, "referencePbrExtraction": {"requiredWhenSourceImagePresent": false, "targetThreshold": 0.7, "stopOnLowConfidence": true, "script": "forge/stage1_intake/extract_pbr_evidence.py", "acceptedLimitation": "Source is an illustration plate, not a photographed material. Procedural steel / gilt / wrap is the honest path; extracted maps would copy ink, not PBR."}, "mustAvoid": ["single flat albedo per material", "uniform roughness", "albedo texture reused as roughness/height/normal/AO", "single-frequency random noise", "plastic-looking smooth bark, stone, cloth, foliage, or aged material", "local color/detail described only in prose without material masks", "claiming exact PBR recovery when confidence is below the target threshold"]}, "lightingPass": {"requiredTerms": ["key light", "fill light", "rim or environment light", "exposure", "tone mapping", "background", "contact shadow"], "mustAvoid": ["ambient-only lighting", "flat value range", "missing contact shadow", "reference lighting copied without separating material readability"]}, "screenshotReview": ["Compare albedo palette and local color zones.", "Compare roughness/normal/bump response under light.", "Compare cavity dirt, edge wear, stains, moss, scratches, or other local masks.", "Compare key/fill/rim structure, exposure, tone mapping, background, and contact shadows.", "Capture a neutral-light render to verify material readability without reference lighting.", "Capture a grazing-light close-up to expose flat normals, uniform roughness, tiling, and plastic highlights.", "Capture a reference-matched render from the same camera framing as the source."]};
  root.userData.performanceBudget = {"qualityPriority": "stylized-approximate", "targetTriangles": 250000, "maxDrawCalls": 160, "textureSize": 2048, "fpsTarget": 30, "optimizationPolicy": "Reach accepted visual fidelity first, then optimize without removing reference-critical geometry or surface layers."};
  root.userData.lodPlan = [{"tier": "near", "distance": 0, "strategy": "full component tree and material layers"}, {"tier": "far", "distance": 30, "strategy": "merge static components and reduce local feature geometry"}];
  root.userData.optimizationPlan = {"policy": "Stay below the authored runtime budgets without removing accepted silhouette, material, or interaction evidence.", "runtimeAudit": ["triangles", "draw-calls", "measured-fps", "unique-geometries", "shared-materials", "texture-memory"], "benchmarkPolicy": "FPS is a hard gate on hardware-accelerated WebGL; SwiftShader or llvmpipe measurements are retained as report-only environment diagnostics.", "repetitionDecisions": [{"family": "handle-inlays", "count": 12, "strategy": "retain-selectable-components", "reason": "Each front/back stud and recessed seat is a named component; shared materials already avoid duplicate texture sets."}, {"family": "hamon-lines", "count": 6, "strategy": "retain-distinct-curves", "reason": "The six front/back curves have distinct paths and cannot share one instance transform."}, {"family": "ring-engravings", "count": 6, "strategy": "retain-distinct-profiles", "reason": "Front/back concentric profiles differ by face and size and remain integral ring details."}], "lodStrategy": {"near": "Full component tree, procedural PBR maps, and interaction metadata.", "far": "At 30 relative units, a host application may hide micro integral details while preserving blade, guard, handle, and ring silhouettes.", "implementation": "Documented host integration contract; no automatic LOD swap in the review viewer because the fixed evidence camera is always near-tier."}};
  root.userData.actionReadiness = {
    note: 'Use root.userData.sculptRuntime.nodes for transforms, sockets for attachments, colliders for physics proxies, and destructionGroups for breakable sets.',
  };
  return root;
}

export function createHanHuanShouDaoLookDevLights(
  mode: 'neutral' | 'grazing' | 'reference' = 'neutral',
): THREE.Group {
  const lights = new THREE.Group();
  lights.name = "Han Huan-Shou Dao look-dev lights";
  const hemi = new THREE.HemisphereLight(
    mode === 'reference' ? 0xfff0d6 : 0xf2f4ff,
    0x363b42,
    mode === 'grazing' ? 0.28 : mode === 'reference' ? 0.72 : 0.85,
  );
  hemi.name = 'lookdev-hemi';
  lights.add(hemi);
  const key = new THREE.DirectionalLight(
    mode === 'reference' ? 0xffcf8a : 0xfff4e8,
    mode === 'grazing' ? 4.2 : mode === 'reference' ? 2.6 : 2.15,
  );
  if (mode === 'grazing') key.position.set(7.5, 1.1, 4.0);
  else if (mode === 'reference') key.position.set(-4.5, 7.5, 5.0);
  else key.position.set(-4.0, 6.0, 5.5);
  key.name = 'lookdev-key';
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
  fill.name = 'lookdev-fill';
  fill.position.set(4.0, 3.0, 3.5);
  lights.add(fill);
  const rim = new THREE.DirectionalLight(0xfff1c4, mode === 'grazing' ? 0.28 : 0.85);
  rim.name = 'lookdev-rim';
  rim.position.set(0.5, 4.5, -6.0);
  lights.add(rim);
  lights.userData.reviewMode = mode;
  lights.userData.lightingFromPhoto = ["Key: even orthographic plate lighting, slightly above-front, no hard indoor bounce.", "Fill: white page surround, high value, keeps steel from falling to mid-grey.", "Rim / environment: weak studio rim so the disk guard and ring read as volumes.", "Exposure: 1.0; protect steel highlights with ACES filmic tone mapping.", "Background: pure white plate field with no gradient or floor plane.", "Contact shadow: optional; the plate is on white, so review shots may omit the floor."];
  lights.userData.lookDevTargets = {"qualityPriority": "stylized-approximate", "materialPass": {"albedoPaletteRequired": true, "roughnessVariationRequired": true, "normalOrBumpRequired": true, "localOverridesRequired": true, "minimumTextureResolution": 1024, "preferredTextureResolution": 2048, "independentMapChannels": ["albedo", "roughness", "height", "normal", "ambient-occlusion"], "requiredSurfaceFrequencyBands": ["macro", "meso", "micro"], "geometryReliefRequiredWhenSilhouetteAffected": true, "referencePbrExtraction": {"requiredWhenSourceImagePresent": false, "targetThreshold": 0.7, "stopOnLowConfidence": true, "script": "forge/stage1_intake/extract_pbr_evidence.py", "acceptedLimitation": "Source is an illustration plate, not a photographed material. Procedural steel / gilt / wrap is the honest path; extracted maps would copy ink, not PBR."}, "mustAvoid": ["single flat albedo per material", "uniform roughness", "albedo texture reused as roughness/height/normal/AO", "single-frequency random noise", "plastic-looking smooth bark, stone, cloth, foliage, or aged material", "local color/detail described only in prose without material masks", "claiming exact PBR recovery when confidence is below the target threshold"]}, "lightingPass": {"requiredTerms": ["key light", "fill light", "rim or environment light", "exposure", "tone mapping", "background", "contact shadow"], "mustAvoid": ["ambient-only lighting", "flat value range", "missing contact shadow", "reference lighting copied without separating material readability"]}, "screenshotReview": ["Compare albedo palette and local color zones.", "Compare roughness/normal/bump response under light.", "Compare cavity dirt, edge wear, stains, moss, scratches, or other local masks.", "Compare key/fill/rim structure, exposure, tone mapping, background, and contact shadows.", "Capture a neutral-light render to verify material readability without reference lighting.", "Capture a grazing-light close-up to expose flat normals, uniform roughness, tiling, and plastic highlights.", "Capture a reference-matched render from the same camera framing as the source."]};
  return lights;
}

// PBR materials (clearcoat/iridescence/transmission/anisotropy) need an environment
// map to visually behave as intended — call this once per renderer and assign the
// result to scene.environment before rendering. No external HDR asset required.
export function createHanHuanShouDaoEnvironment(renderer: THREE.WebGLRenderer): THREE.Texture {
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
export function frameHanHuanShouDaoCamera(
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
export function createHanHuanShouDaoPresentationComposer(
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

export function configureHanHuanShouDaoRenderer(renderer: THREE.WebGLRenderer): void {
  // Load-bearing for view-dependent finishes (anodized / Doppler): without ACES + sRGB
  // the environment reflection reads flat/washed instead of a believable metal response.
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
}

export function createHanHuanShouDaoInspectControls(
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
