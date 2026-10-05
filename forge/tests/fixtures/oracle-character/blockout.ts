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

type SdfVector = readonly [number, number, number];
type SdfTransform = { position?: SdfVector; translation?: SdfVector; rotation?: SdfVector; scale?: SdfVector };
type SdfPrimitive = {
  readonly id: string;
  readonly type: 'sphere' | 'capsule' | 'box' | 'cone' | 'ellipsoid';
  readonly center?: SdfVector;
  readonly radius?: number | SdfVector;
  readonly height?: number;
  readonly size?: SdfVector;
  readonly dimensions?: SdfVector;
  readonly radii?: SdfVector;
  readonly transform?: SdfTransform;
};
type SdfOperation = {
  readonly id?: string;
  readonly output?: string;
  readonly type: 'smooth-union' | 'subtract' | 'intersect';
  readonly left: string;
  readonly right: string;
  readonly radius?: number;
};
type SdfDescriptor = {
  readonly primitives: readonly SdfPrimitive[];
  readonly operations?: readonly SdfOperation[];
  readonly resolution: number;
  readonly bounds?: { readonly min: SdfVector; readonly max: SdfVector };
};
type SdfFunction = (point: THREE.Vector3) => number;

function sdfSphere(point: THREE.Vector3, radius: number): number {
  return point.length() - radius;
}

function sdfCapsule(point: THREE.Vector3, radius: number, height: number): number {
  const halfHeight = height * 0.5;
  const y = Math.max(-halfHeight, Math.min(halfHeight, point.y));
  return point.distanceTo(new THREE.Vector3(0, y, 0)) - radius;
}

function sdfBox(point: THREE.Vector3, size: SdfVector): number {
  const q = new THREE.Vector3(Math.abs(point.x), Math.abs(point.y), Math.abs(point.z))
    .sub(new THREE.Vector3(size[0] * 0.5, size[1] * 0.5, size[2] * 0.5));
  return q.clone().max(new THREE.Vector3()).length() + Math.min(Math.max(q.x, q.y, q.z), 0);
}

function sdfCone(point: THREE.Vector3, radius: number, height: number): number {
  const halfHeight = height * 0.5;
  const taper = radius * (1 - (point.y + halfHeight) / height);
  return Math.max(Math.hypot(point.x, point.z) - Math.max(0, taper), Math.abs(point.y) - halfHeight);
}

function sdfEllipsoid(point: THREE.Vector3, radii: SdfVector): number {
  const scaled = new THREE.Vector3(point.x / radii[0], point.y / radii[1], point.z / radii[2]);
  return (scaled.length() - 1) * Math.min(radii[0], radii[1], radii[2]);
}

function sdfRadii(primitive: SdfPrimitive): SdfVector {
  const radius = primitive.radius;
  if (primitive.radii) return primitive.radii;
  if (typeof radius === 'number') return [radius, radius, radius];
  return radius ?? [0.5, 0.5, 0.5];
}

function smin(left: number, right: number, radius: number): number {
  const blend = Math.max(radius - Math.abs(left - right), 0) / radius;
  return Math.min(left, right) - blend * blend * radius * 0.25;
}

function sdfLocalPoint(point: THREE.Vector3, primitive: SdfPrimitive): { point: THREE.Vector3; scale: number } {
  const transform = primitive.transform;
  const translation = transform?.position ?? transform?.translation ?? primitive.center ?? [0, 0, 0];
  const rotation = transform?.rotation ?? [0, 0, 0];
  const scale = transform?.scale ?? [1, 1, 1];
  const local = point.clone().sub(new THREE.Vector3(translation[0], translation[1], translation[2]));
  const inverseRotation = new THREE.Quaternion()
    .setFromEuler(new THREE.Euler(rotation[0], rotation[1], rotation[2]))
    .invert();
  local.applyQuaternion(inverseRotation);
  local.set(local.x / scale[0], local.y / scale[1], local.z / scale[2]);
  return { point: local, scale: Math.min(scale[0], scale[1], scale[2]) };
}

function sdfPrimitive(point: THREE.Vector3, primitive: SdfPrimitive): number {
  const local = sdfLocalPoint(point, primitive);
  let distance: number;
  switch (primitive.type) {
    case 'sphere':
      distance = sdfSphere(local.point, typeof primitive.radius === 'number' ? primitive.radius : 0.5);
      break;
    case 'capsule':
      distance = sdfCapsule(local.point, typeof primitive.radius === 'number' ? primitive.radius : 0.25, primitive.height ?? 1);
      break;
    case 'box':
      distance = sdfBox(local.point, primitive.size ?? primitive.dimensions ?? [1, 1, 1]);
      break;
    case 'cone':
      distance = sdfCone(local.point, typeof primitive.radius === 'number' ? primitive.radius : 0.5, primitive.height ?? 1);
      break;
    case 'ellipsoid':
      distance = sdfEllipsoid(local.point, sdfRadii(primitive));
      break;
  }
  return distance * local.scale;
}

function sdfSample(descriptor: SdfDescriptor): SdfFunction {
  const nodes = new Map<string, SdfFunction>();
  for (const primitive of descriptor.primitives) nodes.set(primitive.id, (point) => sdfPrimitive(point, primitive));
  let result = descriptor.primitives.length > 0 ? nodes.get(descriptor.primitives[0].id) : undefined;
  for (let index = 0; index < (descriptor.operations?.length ?? 0); index += 1) {
    const operation = descriptor.operations?.[index];
    if (!operation) continue;
    const left = nodes.get(operation.left);
    const right = nodes.get(operation.right);
    if (!left || !right) continue;
    let combined: SdfFunction;
    switch (operation.type) {
      case 'smooth-union':
        combined = (point) => smin(left(point), right(point), operation.radius ?? 0.1);
        break;
      case 'subtract':
        combined = (point) => Math.max(left(point), -right(point));
        break;
      case 'intersect':
        combined = (point) => Math.max(left(point), right(point));
        break;
    }
    nodes.set(operation.id ?? operation.output ?? `operation-${index}`, combined);
    result = combined;
  }
  return result ?? (() => Infinity);
}

function polygonizeSdf(descriptor: SdfDescriptor): THREE.BufferGeometry {
  // SURFACE NETS, not a voxel shell.
  //
  // This used to emit one axis-aligned quad per exposed voxel face, which is a Minecraft surface:
  // every face is axis-aligned, every edge is a 90-degree step, and the result is stair-stepped at
  // exactly the scale of the sampling grid. For a subject whose whole identity is smooth blended
  // organic form -- which is the only kind of subject anyone reaches for an implicit surface to
  // build -- that is worse than the assembled primitives it was meant to replace.
  //
  // Naive surface nets places ONE vertex per sign-changing cell, at the average of the linearly
  // interpolated crossings on that cell's edges, and joins the four cells around each crossing
  // edge into a quad. It is compact, manifold, and smooth, and it is a natural fit for a field
  // that can be sampled anywhere rather than only at corners.
  //
  // Normals come from the field GRADIENT, not from face averaging: the gradient is the exact
  // surface normal of the implicit surface, so shading no longer carries the grid's imprint.
  const resolution = Math.max(4, Math.min(64, Math.floor(descriptor.resolution)));
  const defaultBounds: { readonly min: SdfVector; readonly max: SdfVector } = { min: [-2, -2, -2], max: [2, 2, 2] };
  const bounds = descriptor.bounds ?? defaultBounds;
  const min = new THREE.Vector3(bounds.min[0], bounds.min[1], bounds.min[2]);
  const step = new THREE.Vector3(
    (bounds.max[0] - bounds.min[0]) / resolution,
    (bounds.max[1] - bounds.min[1]) / resolution,
    (bounds.max[2] - bounds.min[2]) / resolution,
  );
  const sample = sdfSample(descriptor);
  const scratch = new THREE.Vector3();

  // Corner grid: one more corner than cells on each axis.
  const side = resolution + 1;
  const field = new Float32Array(side * side * side);
  const cornerAt = (x: number, y: number, z: number): number => (z * side + y) * side + x;
  for (let z = 0; z < side; z += 1) {
    for (let y = 0; y < side; y += 1) {
      for (let x = 0; x < side; x += 1) {
        scratch.set(min.x + x * step.x, min.y + y * step.y, min.z + z * step.z);
        field[cornerAt(x, y, z)] = sample(scratch);
      }
    }
  }

  // The 12 cell edges as corner-offset pairs.
  const CUBE_EDGES: readonly (readonly [number, number, number, number, number, number])[] = [
    [0, 0, 0, 1, 0, 0], [1, 0, 0, 1, 1, 0], [0, 1, 0, 1, 1, 0], [0, 0, 0, 0, 1, 0],
    [0, 0, 1, 1, 0, 1], [1, 0, 1, 1, 1, 1], [0, 1, 1, 1, 1, 1], [0, 0, 1, 0, 1, 1],
    [0, 0, 0, 0, 0, 1], [1, 0, 0, 1, 0, 1], [1, 1, 0, 1, 1, 1], [0, 1, 0, 0, 1, 1],
  ];

  const positions: number[] = [];
  const normals: number[] = [];
  const indices: number[] = [];
  const cellVertex = new Int32Array(resolution * resolution * resolution).fill(-1);
  const cellAt = (x: number, y: number, z: number): number => (z * resolution + y) * resolution + x;

  // Central-difference gradient, stepped at a fraction of a cell so it follows the field rather
  // than the grid.
  const epsilon = Math.min(step.x, step.y, step.z) * 0.25;
  const gradient = (point: THREE.Vector3): THREE.Vector3 => {
    const gx = sample(scratch.set(point.x + epsilon, point.y, point.z))
      - sample(scratch.set(point.x - epsilon, point.y, point.z));
    const gy = sample(scratch.set(point.x, point.y + epsilon, point.z))
      - sample(scratch.set(point.x, point.y - epsilon, point.z));
    const gz = sample(scratch.set(point.x, point.y, point.z + epsilon))
      - sample(scratch.set(point.x, point.y, point.z - epsilon));
    const normal = new THREE.Vector3(gx, gy, gz);
    // A point where the field is flat has no defined normal; +Y is arbitrary but finite, and
    // leaving a zero vector would poison every lighting calculation downstream.
    return normal.lengthSq() < 1e-20 ? new THREE.Vector3(0, 1, 0) : normal.normalize();
  };

  for (let z = 0; z < resolution; z += 1) {
    for (let y = 0; y < resolution; y += 1) {
      for (let x = 0; x < resolution; x += 1) {
        let crossings = 0;
        let sumX = 0;
        let sumY = 0;
        let sumZ = 0;
        for (const [ax, ay, az, bx, by, bz] of CUBE_EDGES) {
          const a = field[cornerAt(x + ax, y + ay, z + az)];
          const b = field[cornerAt(x + bx, y + by, z + bz)];
          if ((a <= 0) === (b <= 0)) continue;
          const t = a / (a - b);
          sumX += (ax + (bx - ax) * t);
          sumY += (ay + (by - ay) * t);
          sumZ += (az + (bz - az) * t);
          crossings += 1;
        }
        if (crossings === 0) continue;
        const px = min.x + (x + sumX / crossings) * step.x;
        const py = min.y + (y + sumY / crossings) * step.y;
        const pz = min.z + (z + sumZ / crossings) * step.z;
        cellVertex[cellAt(x, y, z)] = positions.length / 3;
        positions.push(px, py, pz);
        const normal = gradient(new THREE.Vector3(px, py, pz));
        normals.push(normal.x, normal.y, normal.z);
      }
    }
  }

  // One quad per sign-changing grid edge, joining the four cells that share it.
  //
  // Winding, worked out rather than guessed. For the +x edge from corner (x,y,z), the four cells
  // around it are (x, y-1, z-1), (x, y, z-1), (x, y, z), (x, y-1, z); in the (y,z) plane that
  // traversal is +y, +z, -y, whose cross product is +x. So when the corner is INSIDE and its
  // neighbour is outside, the unflipped order already faces out, and the flip belongs on the
  // opposite case. Getting this backwards is invisible in the normals -- those come from the
  // gradient and stay correct -- and shows only as back-face culling removing the front surface,
  // i.e. the model rendering as a hollow shell with its interior visible.
  const quad = (a: number, b: number, c: number, d: number, flip: boolean): void => {
    if (a < 0 || b < 0 || c < 0 || d < 0) return;
    if (flip) indices.push(a, c, b, a, d, c);
    else indices.push(a, b, c, a, c, d);
  };
  // Each quad joins the FOUR cells sharing one grid edge, so every one of those cells must exist.
  // Bounding only the edge axis and the lower end of the other two let y/z reach `resolution`, which
  // is a corner index, not a cell index: `cellAt` then strides into an unrelated slot (with
  // resolution 8, `cellAt(3, 8, 1)` is 131 -- the slot for cell (3, 0, 2)) or past the end of the
  // array, where a typed-array read yields `undefined`. `undefined < 0` is false, so the guard in
  // `quad` passed it through to `setIndex`, which coerces it to 0. Measured on a sphere reaching its
  // own bounds at resolution 8: 60 out-of-range reads and 108 aliased reads. A surface that touches
  // the sampling box is therefore left OPEN at that face rather than closed with wrong triangles --
  // pad `bounds` past the surface to get a closed mesh.
  for (let z = 0; z < side; z += 1) {
    for (let y = 0; y < side; y += 1) {
      for (let x = 0; x < side; x += 1) {
        const here = field[cornerAt(x, y, z)] <= 0;
        if (x + 1 < side && y > 0 && z > 0 && y < side - 1 && z < side - 1
          && here !== (field[cornerAt(x + 1, y, z)] <= 0)) {
          quad(
            cellVertex[cellAt(x, y - 1, z - 1)], cellVertex[cellAt(x, y, z - 1)],
            cellVertex[cellAt(x, y, z)], cellVertex[cellAt(x, y - 1, z)], !here,
          );
        }
        if (y + 1 < side && x > 0 && z > 0 && x < side - 1 && z < side - 1
          && here !== (field[cornerAt(x, y + 1, z)] <= 0)) {
          quad(
            cellVertex[cellAt(x - 1, y, z - 1)], cellVertex[cellAt(x - 1, y, z)],
            cellVertex[cellAt(x, y, z)], cellVertex[cellAt(x, y, z - 1)], !here,
          );
        }
        if (z + 1 < side && x > 0 && y > 0 && x < side - 1 && y < side - 1
          && here !== (field[cornerAt(x, y, z + 1)] <= 0)) {
          quad(
            cellVertex[cellAt(x - 1, y - 1, z)], cellVertex[cellAt(x, y - 1, z)],
            cellVertex[cellAt(x, y, z)], cellVertex[cellAt(x - 1, y, z)], !here,
          );
        }
      }
    }
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
  geometry.setIndex(indices);
  geometry.computeBoundingSphere();
  return geometry;
}

// THREE.CapsuleGeometry duplicates every UV-seam vertex (measured: 194 boundary
// edges on the default radius/segments below) -- same benign pattern as box/
// cylinder/sphere/torus, all of which weld cleanly to 0 given a CORRECT weld.
// (A naive vertex-only mergeVertices() reports 64 'non-manifold' edges here, but
// that is a counting artifact, not a real defect: it double-counts a handful of
// near-pole triangles that become degenerate once two of their three corners
// coincide -- confirmed by replicating subdivideCatmullClark's own degenerate-
// triangle-aware vertex identity, which finds a perfectly ordinary 2-manifold.)
// A capsule is the primary shape for skinned limbs/torso (PLAN_1.5), and skinning
// weight computation is O(vertices x bones), so fewer, guaranteed-simple vertices
// is worth having regardless -- authored as a deterministic, closed-by-
// construction mesh instead: shared pole vertices, and
// the radial index taken `% radialSegments` so the seam is never a duplicate
// vertex in the first place, rather than something to weld away afterward.
// Adapted from forge/stage5_rig/emit_rig.py's buildWatertightCapsule (verified
// there: 0 boundary edges, 0 non-manifold edges, deterministic across repeated
// runs) -- ported here rather than imported because this factory and the rig
// emitter are separate generated-output surfaces with no shared runtime module;
// see forge/tests/test_primitive_watertightness.py for the measured proof, and
// coordinate with the rig owner before changing either copy independently.
function buildWatertightCapsule(
  radius: number,
  cylLength: number,
  capSegments: number,
  radialSegments: number,
  heightSegments: number,
): THREE.BufferGeometry {
  const positions: number[] = [];
  const indices: number[] = [];
  const uvs: number[] = [];
  const halfCyl = cylLength / 2;
  const totalSpan = 2 * (Math.PI / 2 * radius) + Math.max(0, cylLength);
  const vOf = (fromBottom: number) => (totalSpan > 0 ? fromBottom / totalSpan : 0);

  const bottomPoleIndex = positions.length / 3;
  positions.push(0, -halfCyl - radius, 0);
  uvs.push(0.5, vOf(0));

  const ringStarts: number[] = [];
  const ringV: number[] = [];
  for (let ring = 1; ring <= capSegments; ring += 1) {
    const phi = (Math.PI / 2) * (ring / capSegments);
    const y = -halfCyl - radius * Math.cos(phi);
    const r = radius * Math.sin(phi);
    const start = positions.length / 3;
    ringStarts.push(start);
    ringV.push(vOf(radius * phi));
    for (let radial = 0; radial < radialSegments; radial += 1) {
      const theta = (radial / radialSegments) * Math.PI * 2;
      positions.push(r * Math.cos(theta), y, r * Math.sin(theta));
      uvs.push(radial / radialSegments, vOf(radius * phi));
    }
  }

  const cylinderRingStarts: number[] = [];
  if (cylLength > 0) {
    for (let step = 1; step <= heightSegments; step += 1) {
      const y = -halfCyl + (cylLength * step) / heightSegments;
      const start = positions.length / 3;
      cylinderRingStarts.push(start);
      const v = vOf(radius * (Math.PI / 2) + halfCyl + y);
      for (let radial = 0; radial < radialSegments; radial += 1) {
        const theta = (radial / radialSegments) * Math.PI * 2;
        positions.push(radius * Math.cos(theta), y, radius * Math.sin(theta));
        uvs.push(radial / radialSegments, v);
      }
    }
  }

  const topRingStarts: number[] = [];
  for (let ring = capSegments - 1; ring >= 1; ring -= 1) {
    const phi = (Math.PI / 2) * (ring / capSegments);
    const y = halfCyl + radius * Math.cos(phi);
    const r = radius * Math.sin(phi);
    const start = positions.length / 3;
    topRingStarts.push(start);
    const v = vOf(radius * (Math.PI / 2) + Math.max(0, cylLength) + radius * (Math.PI / 2 - phi));
    for (let radial = 0; radial < radialSegments; radial += 1) {
      const theta = (radial / radialSegments) * Math.PI * 2;
      positions.push(r * Math.cos(theta), y, r * Math.sin(theta));
      uvs.push(radial / radialSegments, v);
    }
  }

  const topPoleIndex = positions.length / 3;
  positions.push(0, halfCyl + radius, 0);
  uvs.push(0.5, vOf(totalSpan));

  const firstBottomRing = ringStarts[0];
  for (let radial = 0; radial < radialSegments; radial += 1) {
    const next = (radial + 1) % radialSegments;
    indices.push(bottomPoleIndex, firstBottomRing + radial, firstBottomRing + next);
  }

  const allRings = [...ringStarts, ...cylinderRingStarts, ...topRingStarts];
  for (let i = 0; i < allRings.length - 1; i += 1) {
    const a = allRings[i];
    const b = allRings[i + 1];
    for (let radial = 0; radial < radialSegments; radial += 1) {
      const next = (radial + 1) % radialSegments;
      indices.push(a + radial, a + next, b + next);
      indices.push(a + radial, b + next, b + radial);
    }
  }

  const lastRing = allRings[allRings.length - 1];
  for (let radial = 0; radial < radialSegments; radial += 1) {
    const next = (radial + 1) % radialSegments;
    indices.push(topPoleIndex, lastRing + next, lastRing + radial);
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  return geometry;
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

// Generated from ObjectSculptSpec target: Oracle Figure
// Sculpt build pass: blockout
// This factory is intentionally pass-gated. Finish browser screenshot review before unlocking deeper passes.
export function createOracleFigureModel(options: ProceduralModelOptions = {}): THREE.Group {
  const root = new THREE.Group();
  root.name = "Oracle Figure";
  root.userData.reconstructionEvidence = {"itemFamily": null, "subtype": null, "componentAdapter": null, "route": null, "exactnessTier": null, "referenceCamera": {"solved": false, "fovDegrees": 40.0, "aspect": 1.0, "orientation": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}, "positionHint": [0.0, 0.0, 3.0], "note": "For likeness work, solve the reference camera (forge/stage1_intake/solve_camera_pose.py) so the review render aligns with the photo and the reference can be projected. Confirm by overlay review."}, "approximationNotes": []};
  root.userData.materialPipeline = {};
  root.userData.materialReferenceRegistry = null;

  const materialMap: Record<string, THREE.Material> = {};
  materialMap["base"] = createSculptMaterial(
    "base",
    {"id": "base", "name": "Base material", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#8A7A5F", "color": "#8A7A5F", "albedo": {"dominant": "#8A7A5F", "secondary": ["#6E614B", "#A08F70"], "samplingNotes": "Use image-observed local color zones, not a single averaged color."}, "colorVariation": {"palette": ["#8A7A5F", "#6E614B", "#A08F70"], "pattern": "mottled", "amplitude": 0.15, "heightCorrelation": 0.3}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [2.0, 2.0], "anisotropy": 8, "texelDensityIntent": "Preserve stable world/object-scale detail; do not stretch micro detail with component scale."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 2.0, "amplitude": 0.42, "role": "broad color and height breakup"}, {"id": "meso", "frequency": 12.0, "amplitude": 0.22, "role": "ridges, pores, grain, dents, or equivalent visible relief"}, {"id": "micro", "frequency": 56.0, "amplitude": 0.08, "role": "highlight breakup visible under grazing light"}], "roughness": {"base": 0.75, "variation": 0.15, "map": "independent-procedural-field", "localResponse": "higher roughness in cavities, lower roughness on worn edges"}, "metalness": {"base": 0.0, "variation": 0.0}, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.35, "scale": 24.0, "space": "tangent"}, "bump": {"pattern": "none", "amplitude": 0.0, "scale": 1.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.25, "contactShadowBias": 0.35, "notes": "Darken creases, seams, intersections, and recessed local features."}, "wear": {"edgeWear": 0.0, "scratches": [], "chips": []}, "dirt": {"amount": 0.0, "cavityBias": 0.0, "color": "#2F2A22"}, "localOverrides": [], "shaderNotes": ["Prefer MeshPhysicalMaterial when clearcoat, sheen, transmission, or thin-surface response is observed; otherwise use MeshStandardMaterial-compatible PBR channels.", "Generate albedo, roughness, height/normal, and AO independently; never alias albedo into roughness.", "Use normal/bump/displacement only when they map to observed surface relief.", "Use displacement geometry when the observed relief changes the close-up silhouette; texture-only relief is insufficient there."], "notes": "Replace with image-derived color, roughness, noise, and edge-wear notes."},
    options
  );
  materialMap["hidden"] = createSculptMaterial(
    "hidden",
    {"id": "hidden", "name": "Base material", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#000000", "color": "#000000", "albedo": {"dominant": "#000000", "secondary": ["#000000"]}, "colorVariation": {"palette": ["#000000", "#000000"], "pattern": "flat", "amplitude": 0.05, "heightCorrelation": 0.0}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [2.0, 2.0], "anisotropy": 8, "texelDensityIntent": "Preserve stable world/object-scale detail; do not stretch micro detail with component scale."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 2.0, "amplitude": 0.42, "role": "broad color and height breakup"}, {"id": "meso", "frequency": 12.0, "amplitude": 0.22, "role": "ridges, pores, grain, dents, or equivalent visible relief"}, {"id": "micro", "frequency": 56.0, "amplitude": 0.08, "role": "highlight breakup visible under grazing light"}], "roughness": {"base": 1.0, "variation": 0.0}, "metalness": {"base": 0.0, "variation": 0.0}, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.35, "scale": 24.0, "space": "tangent"}, "bump": {"pattern": "none", "amplitude": 0.0, "scale": 1.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.25, "contactShadowBias": 0.35, "notes": "Darken creases, seams, intersections, and recessed local features."}, "wear": {"edgeWear": 0.0, "scratches": [], "chips": []}, "dirt": {"amount": 0.0, "cavityBias": 0.0, "color": "#2F2A22"}, "localOverrides": [], "shaderNotes": ["Prefer MeshPhysicalMaterial when clearcoat, sheen, transmission, or thin-surface response is observed; otherwise use MeshStandardMaterial-compatible PBR channels.", "Generate albedo, roughness, height/normal, and AO independently; never alias albedo into roughness.", "Use normal/bump/displacement only when they map to observed surface relief.", "Use displacement geometry when the observed relief changes the close-up silhouette; texture-only relief is insufficient there."], "notes": "Replace with image-derived color, roughness, noise, and edge-wear notes.", "opacity": {"base": 0.0}},
    options
  );
  materialMap["skin"] = createSculptMaterial(
    "skin",
    {"id": "skin", "name": "Base material", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#e8b98f", "color": "#e8b98f", "albedo": {"dominant": "#e8b98f", "secondary": ["#be9875"]}, "colorVariation": {"palette": ["#e8b98f", "#be9875"], "pattern": "flat", "amplitude": 0.05, "heightCorrelation": 0.0}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [2.0, 2.0], "anisotropy": 8, "texelDensityIntent": "Preserve stable world/object-scale detail; do not stretch micro detail with component scale."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 2.0, "amplitude": 0.42, "role": "broad color and height breakup"}, {"id": "meso", "frequency": 12.0, "amplitude": 0.22, "role": "ridges, pores, grain, dents, or equivalent visible relief"}, {"id": "micro", "frequency": 56.0, "amplitude": 0.08, "role": "highlight breakup visible under grazing light"}], "roughness": {"base": 0.55, "variation": 0.08}, "metalness": {"base": 0.0, "variation": 0.0}, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.35, "scale": 24.0, "space": "tangent"}, "bump": {"pattern": "none", "amplitude": 0.0, "scale": 1.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.25, "contactShadowBias": 0.35, "notes": "Darken creases, seams, intersections, and recessed local features."}, "wear": {"edgeWear": 0.0, "scratches": [], "chips": []}, "dirt": {"amount": 0.0, "cavityBias": 0.0, "color": "#2F2A22"}, "localOverrides": [], "shaderNotes": ["Prefer MeshPhysicalMaterial when clearcoat, sheen, transmission, or thin-surface response is observed; otherwise use MeshStandardMaterial-compatible PBR channels.", "Generate albedo, roughness, height/normal, and AO independently; never alias albedo into roughness.", "Use normal/bump/displacement only when they map to observed surface relief.", "Use displacement geometry when the observed relief changes the close-up silhouette; texture-only relief is insufficient there."], "notes": "Replace with image-derived color, roughness, noise, and edge-wear notes."},
    options
  );
  materialMap["hair"] = createSculptMaterial(
    "hair",
    {"id": "hair", "name": "Base material", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#171310", "color": "#171310", "albedo": {"dominant": "#171310", "secondary": ["#13100d"]}, "colorVariation": {"palette": ["#171310", "#13100d"], "pattern": "flat", "amplitude": 0.05, "heightCorrelation": 0.0}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [2.0, 2.0], "anisotropy": 8, "texelDensityIntent": "Preserve stable world/object-scale detail; do not stretch micro detail with component scale."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 2.0, "amplitude": 0.42, "role": "broad color and height breakup"}, {"id": "meso", "frequency": 12.0, "amplitude": 0.22, "role": "ridges, pores, grain, dents, or equivalent visible relief"}, {"id": "micro", "frequency": 56.0, "amplitude": 0.08, "role": "highlight breakup visible under grazing light"}], "roughness": {"base": 0.42, "variation": 0.1}, "metalness": {"base": 0.0, "variation": 0.0}, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.35, "scale": 24.0, "space": "tangent"}, "bump": {"pattern": "none", "amplitude": 0.0, "scale": 1.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.25, "contactShadowBias": 0.35, "notes": "Darken creases, seams, intersections, and recessed local features."}, "wear": {"edgeWear": 0.0, "scratches": [], "chips": []}, "dirt": {"amount": 0.0, "cavityBias": 0.0, "color": "#2F2A22"}, "localOverrides": [], "shaderNotes": ["Prefer MeshPhysicalMaterial when clearcoat, sheen, transmission, or thin-surface response is observed; otherwise use MeshStandardMaterial-compatible PBR channels.", "Generate albedo, roughness, height/normal, and AO independently; never alias albedo into roughness.", "Use normal/bump/displacement only when they map to observed surface relief.", "Use displacement geometry when the observed relief changes the close-up silhouette; texture-only relief is insufficient there."], "notes": "Replace with image-derived color, roughness, noise, and edge-wear notes."},
    options
  );
  materialMap["shirt"] = createSculptMaterial(
    "shirt",
    {"id": "shirt", "name": "Base material", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#20202a", "color": "#20202a", "albedo": {"dominant": "#20202a", "secondary": ["#1a1a22"]}, "colorVariation": {"palette": ["#20202a", "#1a1a22"], "pattern": "flat", "amplitude": 0.05, "heightCorrelation": 0.0}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [2.0, 2.0], "anisotropy": 8, "texelDensityIntent": "Preserve stable world/object-scale detail; do not stretch micro detail with component scale."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 2.0, "amplitude": 0.42, "role": "broad color and height breakup"}, {"id": "meso", "frequency": 12.0, "amplitude": 0.22, "role": "ridges, pores, grain, dents, or equivalent visible relief"}, {"id": "micro", "frequency": 56.0, "amplitude": 0.08, "role": "highlight breakup visible under grazing light"}], "roughness": {"base": 0.85, "variation": 0.12}, "metalness": {"base": 0.0, "variation": 0.0}, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.35, "scale": 24.0, "space": "tangent"}, "bump": {"pattern": "none", "amplitude": 0.0, "scale": 1.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.25, "contactShadowBias": 0.35, "notes": "Darken creases, seams, intersections, and recessed local features."}, "wear": {"edgeWear": 0.0, "scratches": [], "chips": []}, "dirt": {"amount": 0.0, "cavityBias": 0.0, "color": "#2F2A22"}, "localOverrides": [], "shaderNotes": ["Prefer MeshPhysicalMaterial when clearcoat, sheen, transmission, or thin-surface response is observed; otherwise use MeshStandardMaterial-compatible PBR channels.", "Generate albedo, roughness, height/normal, and AO independently; never alias albedo into roughness.", "Use normal/bump/displacement only when they map to observed surface relief.", "Use displacement geometry when the observed relief changes the close-up silhouette; texture-only relief is insufficient there."], "notes": "Replace with image-derived color, roughness, noise, and edge-wear notes."},
    options
  );
  materialMap["pants"] = createSculptMaterial(
    "pants",
    {"id": "pants", "name": "Base material", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#2b2d33", "color": "#2b2d33", "albedo": {"dominant": "#2b2d33", "secondary": ["#23252a"]}, "colorVariation": {"palette": ["#2b2d33", "#23252a"], "pattern": "flat", "amplitude": 0.05, "heightCorrelation": 0.0}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [2.0, 2.0], "anisotropy": 8, "texelDensityIntent": "Preserve stable world/object-scale detail; do not stretch micro detail with component scale."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 2.0, "amplitude": 0.42, "role": "broad color and height breakup"}, {"id": "meso", "frequency": 12.0, "amplitude": 0.22, "role": "ridges, pores, grain, dents, or equivalent visible relief"}, {"id": "micro", "frequency": 56.0, "amplitude": 0.08, "role": "highlight breakup visible under grazing light"}], "roughness": {"base": 0.75, "variation": 0.1}, "metalness": {"base": 0.0, "variation": 0.0}, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.35, "scale": 24.0, "space": "tangent"}, "bump": {"pattern": "none", "amplitude": 0.0, "scale": 1.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.25, "contactShadowBias": 0.35, "notes": "Darken creases, seams, intersections, and recessed local features."}, "wear": {"edgeWear": 0.0, "scratches": [], "chips": []}, "dirt": {"amount": 0.0, "cavityBias": 0.0, "color": "#2F2A22"}, "localOverrides": [], "shaderNotes": ["Prefer MeshPhysicalMaterial when clearcoat, sheen, transmission, or thin-surface response is observed; otherwise use MeshStandardMaterial-compatible PBR channels.", "Generate albedo, roughness, height/normal, and AO independently; never alias albedo into roughness.", "Use normal/bump/displacement only when they map to observed surface relief.", "Use displacement geometry when the observed relief changes the close-up silhouette; texture-only relief is insufficient there."], "notes": "Replace with image-derived color, roughness, noise, and edge-wear notes."},
    options
  );
  materialMap["shoes"] = createSculptMaterial(
    "shoes",
    {"id": "shoes", "name": "Base material", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#171512", "color": "#171512", "albedo": {"dominant": "#171512", "secondary": ["#13110f"]}, "colorVariation": {"palette": ["#171512", "#13110f"], "pattern": "flat", "amplitude": 0.05, "heightCorrelation": 0.0}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [2.0, 2.0], "anisotropy": 8, "texelDensityIntent": "Preserve stable world/object-scale detail; do not stretch micro detail with component scale."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 2.0, "amplitude": 0.42, "role": "broad color and height breakup"}, {"id": "meso", "frequency": 12.0, "amplitude": 0.22, "role": "ridges, pores, grain, dents, or equivalent visible relief"}, {"id": "micro", "frequency": 56.0, "amplitude": 0.08, "role": "highlight breakup visible under grazing light"}], "roughness": {"base": 0.55, "variation": 0.08}, "metalness": {"base": 0.0, "variation": 0.0}, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.35, "scale": 24.0, "space": "tangent"}, "bump": {"pattern": "none", "amplitude": 0.0, "scale": 1.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.25, "contactShadowBias": 0.35, "notes": "Darken creases, seams, intersections, and recessed local features."}, "wear": {"edgeWear": 0.0, "scratches": [], "chips": []}, "dirt": {"amount": 0.0, "cavityBias": 0.0, "color": "#2F2A22"}, "localOverrides": [], "shaderNotes": ["Prefer MeshPhysicalMaterial when clearcoat, sheen, transmission, or thin-surface response is observed; otherwise use MeshStandardMaterial-compatible PBR channels.", "Generate albedo, roughness, height/normal, and AO independently; never alias albedo into roughness.", "Use normal/bump/displacement only when they map to observed surface relief.", "Use displacement geometry when the observed relief changes the close-up silhouette; texture-only relief is insufficient there."], "notes": "Replace with image-derived color, roughness, noise, and edge-wear notes."},
    options
  );
  materialMap["eye"] = createSculptMaterial(
    "eye",
    {"id": "eye", "name": "Base material", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#f2eee4", "color": "#f2eee4", "albedo": {"dominant": "#f2eee4", "secondary": ["#c6c3bb"]}, "colorVariation": {"palette": ["#f2eee4", "#c6c3bb"], "pattern": "flat", "amplitude": 0.05, "heightCorrelation": 0.0}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [2.0, 2.0], "anisotropy": 8, "texelDensityIntent": "Preserve stable world/object-scale detail; do not stretch micro detail with component scale."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 2.0, "amplitude": 0.42, "role": "broad color and height breakup"}, {"id": "meso", "frequency": 12.0, "amplitude": 0.22, "role": "ridges, pores, grain, dents, or equivalent visible relief"}, {"id": "micro", "frequency": 56.0, "amplitude": 0.08, "role": "highlight breakup visible under grazing light"}], "roughness": {"base": 0.2, "variation": 0.03}, "metalness": {"base": 0.0, "variation": 0.0}, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.35, "scale": 24.0, "space": "tangent"}, "bump": {"pattern": "none", "amplitude": 0.0, "scale": 1.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.25, "contactShadowBias": 0.35, "notes": "Darken creases, seams, intersections, and recessed local features."}, "wear": {"edgeWear": 0.0, "scratches": [], "chips": []}, "dirt": {"amount": 0.0, "cavityBias": 0.0, "color": "#2F2A22"}, "localOverrides": [], "shaderNotes": ["Prefer MeshPhysicalMaterial when clearcoat, sheen, transmission, or thin-surface response is observed; otherwise use MeshStandardMaterial-compatible PBR channels.", "Generate albedo, roughness, height/normal, and AO independently; never alias albedo into roughness.", "Use normal/bump/displacement only when they map to observed surface relief.", "Use displacement geometry when the observed relief changes the close-up silhouette; texture-only relief is insufficient there."], "notes": "Replace with image-derived color, roughness, noise, and edge-wear notes."},
    options
  );
  materialMap["lips"] = createSculptMaterial(
    "lips",
    {"id": "lips", "name": "Base material", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#c98070", "color": "#c98070", "albedo": {"dominant": "#c98070", "secondary": ["#a5695c"]}, "colorVariation": {"palette": ["#c98070", "#a5695c"], "pattern": "flat", "amplitude": 0.05, "heightCorrelation": 0.0}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [2.0, 2.0], "anisotropy": 8, "texelDensityIntent": "Preserve stable world/object-scale detail; do not stretch micro detail with component scale."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 2.0, "amplitude": 0.42, "role": "broad color and height breakup"}, {"id": "meso", "frequency": 12.0, "amplitude": 0.22, "role": "ridges, pores, grain, dents, or equivalent visible relief"}, {"id": "micro", "frequency": 56.0, "amplitude": 0.08, "role": "highlight breakup visible under grazing light"}], "roughness": {"base": 0.5, "variation": 0.05}, "metalness": {"base": 0.0, "variation": 0.0}, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.35, "scale": 24.0, "space": "tangent"}, "bump": {"pattern": "none", "amplitude": 0.0, "scale": 1.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.25, "contactShadowBias": 0.35, "notes": "Darken creases, seams, intersections, and recessed local features."}, "wear": {"edgeWear": 0.0, "scratches": [], "chips": []}, "dirt": {"amount": 0.0, "cavityBias": 0.0, "color": "#2F2A22"}, "localOverrides": [], "shaderNotes": ["Prefer MeshPhysicalMaterial when clearcoat, sheen, transmission, or thin-surface response is observed; otherwise use MeshStandardMaterial-compatible PBR channels.", "Generate albedo, roughness, height/normal, and AO independently; never alias albedo into roughness.", "Use normal/bump/displacement only when they map to observed surface relief.", "Use displacement geometry when the observed relief changes the close-up silhouette; texture-only relief is insufficient there."], "notes": "Replace with image-derived color, roughness, noise, and edge-wear notes."},
    options
  );

  const nodes: Record<string, THREE.Object3D> = { root };
  const meshes: Record<string, THREE.Mesh> = {};
  const sockets: Record<string, THREE.Object3D> = {};
  const colliders: Record<string, unknown> = {};
  const destructionGroups: Record<string, THREE.Object3D[]> = {};

  const endpoint_root_0 = makeAttachmentEndpoint(null);
  const node_root_0 = new THREE.Group();
  node_root_0.name = "Character (root)__pivot";
  node_root_0.scale.set(1, 1, 1);
  if (endpoint_root_0) {
    node_root_0.position.copy(endpoint_root_0.start);
    node_root_0.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_root_0.position.set(0.0, 0.0, 0.0);
    node_root_0.rotation.set(0.0, 0.0, 0.0);
  }
  node_root_0.userData.sculptComponent = {"id": "root", "name": "Character (root)", "level": "macro", "role": "body", "importance": 1.0, "confidence": 0.8, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Character (root) is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": null, "attachment": null, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.8}, "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "actionProfile": {"animationRole": "root", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "root", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hidden"}}, "material": "hidden", "materialLayers": ["hidden"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_root_0.userData.actionProfile = {"animationRole": "root", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "root", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hidden"}};
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
    materialMap["hidden"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_root_0.name = "Character (root)";
  if (endpoint_root_0) {
    mesh_root_0.position.copy(endpoint_root_0.midpoint);
    mesh_root_0.quaternion.copy(endpoint_root_0.quaternion);
  }
  mesh_root_0.castShadow = options.castShadow ?? true;
  mesh_root_0.receiveShadow = options.receiveShadow ?? true;
  mesh_root_0.userData.sculptComponent = {"id": "root", "name": "Character (root)", "level": "macro", "role": "body", "importance": 1.0, "confidence": 0.8, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Character (root) is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": null, "attachment": null, "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative", "confidence": 0.8}, "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}, "actionProfile": {"animationRole": "root", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "root", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hidden"}}, "material": "hidden", "materialLayers": ["hidden"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_root_0.add(mesh_root_0);
  meshes["root"] = mesh_root_0;
  colliders["root"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["root"] ??= [];
  destructionGroups["root"].push(node_root_0);

  const endpoint_pelvis_1 = makeAttachmentEndpoint(null);
  const node_pelvis_1 = new THREE.Group();
  node_pelvis_1.name = "Pelvis__pivot";
  node_pelvis_1.scale.set(1, 1, 1);
  if (endpoint_pelvis_1) {
    node_pelvis_1.position.copy(endpoint_pelvis_1.start);
    node_pelvis_1.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_pelvis_1.position.set(0.0, -0.18200000000000002, 0.0);
    node_pelvis_1.rotation.set(0.0, 0.0, 0.0);
  }
  node_pelvis_1.userData.sculptComponent = {"id": "pelvis", "name": "Pelvis", "level": "macro", "role": "body", "importance": 0.9, "confidence": 0.8, "primitive": "ellipsoid", "topologyClass": "assembled-solid", "topologyRationale": "Pelvis is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.4522, "height": 0.2856, "depth": 0.35700000000000004, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, -0.18200000000000002, 0.0], "rotation": [0, 0, 0], "scale": [0.4522, 0.2856, 0.35700000000000004]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "pelvis", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "pants"}}, "material": "pants", "materialLayers": ["pants"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_pelvis_1.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "pelvis", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "pants"}};
  (nodes["root"] ?? root).add(node_pelvis_1);
  nodes["pelvis"] = node_pelvis_1;
  const mesh_pelvis_1Geometry = endpoint_pelvis_1
    ? new THREE.CylinderGeometry(endpoint_pelvis_1.endRadius, endpoint_pelvis_1.baseRadius, endpoint_pelvis_1.length, 32, 12)
    : new THREE.SphereGeometry(0.5, 64, 40);
  if (!endpoint_pelvis_1) {
    mesh_pelvis_1Geometry.scale(0.4522, 0.2856, 0.35700000000000004);
  }
  const mesh_pelvis_1 = new THREE.SkinnedMesh(
    mesh_pelvis_1Geometry,
    materialMap["pants"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_pelvis_1.name = "Pelvis";
  if (endpoint_pelvis_1) {
    mesh_pelvis_1.position.copy(endpoint_pelvis_1.midpoint);
    mesh_pelvis_1.quaternion.copy(endpoint_pelvis_1.quaternion);
  }
  mesh_pelvis_1.castShadow = options.castShadow ?? true;
  mesh_pelvis_1.receiveShadow = options.receiveShadow ?? true;
  mesh_pelvis_1.userData.sculptComponent = {"id": "pelvis", "name": "Pelvis", "level": "macro", "role": "body", "importance": 0.9, "confidence": 0.8, "primitive": "ellipsoid", "topologyClass": "assembled-solid", "topologyRationale": "Pelvis is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "root", "attachment": null, "dimensions": {"width": 0.4522, "height": 0.2856, "depth": 0.35700000000000004, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, -0.18200000000000002, 0.0], "rotation": [0, 0, 0], "scale": [0.4522, 0.2856, 0.35700000000000004]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "pelvis", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "pants"}}, "material": "pants", "materialLayers": ["pants"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_pelvis_1.add(mesh_pelvis_1);
  meshes["pelvis"] = mesh_pelvis_1;
  colliders["pelvis"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["pelvis"] ??= [];
  destructionGroups["pelvis"].push(node_pelvis_1);

  const attachment_abdomen_2 = {"parentSocket": "pelvis-waist", "localStart": [0.0, 0.028, 0.0], "localEnd": [0.0, 0.28672, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.1904, "endRadius": 0.21896, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_abdomen_2 = makeAttachmentEndpoint(attachment_abdomen_2);
  const node_abdomen_2 = new THREE.Group();
  node_abdomen_2.name = "Abdomen__pivot";
  node_abdomen_2.scale.set(1, 1, 1);
  if (endpoint_abdomen_2) {
    node_abdomen_2.position.copy(endpoint_abdomen_2.start);
    node_abdomen_2.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_abdomen_2.position.set(0.0, 0.027999999999999997, 0.0);
    node_abdomen_2.rotation.set(0.0, 0.0, 0.0);
  }
  node_abdomen_2.userData.sculptComponent = {"id": "abdomen", "name": "Abdomen", "level": "macro", "role": "shell", "importance": 0.95, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Abdomen is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "pelvis", "attachment": {"parentSocket": "pelvis-waist", "localStart": [0.0, 0.028, 0.0], "localEnd": [0.0, 0.28672, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.1904, "endRadius": 0.21896, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.42840000000000006, "height": 0.25872000000000006, "depth": 0.3808, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, 0.027999999999999997, 0.0], "rotation": [0, 0, 0], "scale": [0.42840000000000006, 0.25872000000000006, 0.3808]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "abdomen", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shirt"}}, "material": "shirt", "materialLayers": ["shirt"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_abdomen_2.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "abdomen", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shirt"}};
  (nodes["pelvis"] ?? root).add(node_abdomen_2);
  nodes["abdomen"] = node_abdomen_2;
  const mesh_abdomen_2Geometry = endpoint_abdomen_2
    ? new THREE.CylinderGeometry(endpoint_abdomen_2.endRadius, endpoint_abdomen_2.baseRadius, endpoint_abdomen_2.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_abdomen_2) {
    mesh_abdomen_2Geometry.scale(0.42840000000000006, 0.25872000000000006, 0.3808);
  }
  const mesh_abdomen_2 = new THREE.SkinnedMesh(
    mesh_abdomen_2Geometry,
    materialMap["shirt"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_abdomen_2.name = "Abdomen";
  if (endpoint_abdomen_2) {
    mesh_abdomen_2.position.copy(endpoint_abdomen_2.midpoint);
    mesh_abdomen_2.quaternion.copy(endpoint_abdomen_2.quaternion);
  }
  mesh_abdomen_2.castShadow = options.castShadow ?? true;
  mesh_abdomen_2.receiveShadow = options.receiveShadow ?? true;
  mesh_abdomen_2.userData.sculptComponent = {"id": "abdomen", "name": "Abdomen", "level": "macro", "role": "shell", "importance": 0.95, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Abdomen is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "pelvis", "attachment": {"parentSocket": "pelvis-waist", "localStart": [0.0, 0.028, 0.0], "localEnd": [0.0, 0.28672, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.1904, "endRadius": 0.21896, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.42840000000000006, "height": 0.25872000000000006, "depth": 0.3808, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, 0.027999999999999997, 0.0], "rotation": [0, 0, 0], "scale": [0.42840000000000006, 0.25872000000000006, 0.3808]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "abdomen", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shirt"}}, "material": "shirt", "materialLayers": ["shirt"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_abdomen_2.add(mesh_abdomen_2);
  meshes["abdomen"] = mesh_abdomen_2;
  colliders["abdomen"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["abdomen"] ??= [];
  destructionGroups["abdomen"].push(node_abdomen_2);

  const attachment_chest_3 = {"parentSocket": "abdomen-chest", "localStart": [0.0, 0.25872, 0.0028], "localEnd": [0.0, 0.616, 0.0056], "contactType": "rigid-weld", "baseRadius": 0.24472, "endRadius": 0.16744, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_chest_3 = makeAttachmentEndpoint(attachment_chest_3);
  const node_chest_3 = new THREE.Group();
  node_chest_3.name = "Chest__pivot";
  node_chest_3.scale.set(1, 1, 1);
  if (endpoint_chest_3) {
    node_chest_3.position.copy(endpoint_chest_3.start);
    node_chest_3.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_chest_3.position.set(0.0, 0.25872000000000006, 0.0028000000000000004);
    node_chest_3.rotation.set(0.0, 0.0, 0.0);
  }
  node_chest_3.userData.sculptComponent = {"id": "chest", "name": "Chest", "level": "macro", "role": "shell", "importance": 1.0, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Chest is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "abdomen", "attachment": {"parentSocket": "abdomen-chest", "localStart": [0.0, 0.25872, 0.0028], "localEnd": [0.0, 0.616, 0.0056], "contactType": "rigid-weld", "baseRadius": 0.24472, "endRadius": 0.16744, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.6118, "height": 0.35728000000000004, "depth": 0.4046, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, 0.25872000000000006, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.6118, 0.35728000000000004, 0.4046]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "chest", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shirt"}}, "material": "shirt", "materialLayers": ["shirt"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_chest_3.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "chest", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shirt"}};
  (nodes["abdomen"] ?? root).add(node_chest_3);
  nodes["chest"] = node_chest_3;
  const mesh_chest_3Geometry = endpoint_chest_3
    ? new THREE.CylinderGeometry(endpoint_chest_3.endRadius, endpoint_chest_3.baseRadius, endpoint_chest_3.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_chest_3) {
    mesh_chest_3Geometry.scale(0.6118, 0.35728000000000004, 0.4046);
  }
  const mesh_chest_3 = new THREE.SkinnedMesh(
    mesh_chest_3Geometry,
    materialMap["shirt"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_chest_3.name = "Chest";
  if (endpoint_chest_3) {
    mesh_chest_3.position.copy(endpoint_chest_3.midpoint);
    mesh_chest_3.quaternion.copy(endpoint_chest_3.quaternion);
  }
  mesh_chest_3.castShadow = options.castShadow ?? true;
  mesh_chest_3.receiveShadow = options.receiveShadow ?? true;
  mesh_chest_3.userData.sculptComponent = {"id": "chest", "name": "Chest", "level": "macro", "role": "shell", "importance": 1.0, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Chest is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "abdomen", "attachment": {"parentSocket": "abdomen-chest", "localStart": [0.0, 0.25872, 0.0028], "localEnd": [0.0, 0.616, 0.0056], "contactType": "rigid-weld", "baseRadius": 0.24472, "endRadius": 0.16744, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.6118, "height": 0.35728000000000004, "depth": 0.4046, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, 0.25872000000000006, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.6118, 0.35728000000000004, 0.4046]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "chest", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shirt"}}, "material": "shirt", "materialLayers": ["shirt"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_chest_3.add(mesh_chest_3);
  meshes["chest"] = mesh_chest_3;
  colliders["chest"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["chest"] ??= [];
  destructionGroups["chest"].push(node_chest_3);

  const attachment_neck_4 = {"parentSocket": "chest-neck-base", "localStart": [0.0, 0.33488, 0.0028], "localEnd": [0.0, 0.42728, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.0728, "endRadius": 0.056, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_neck_4 = makeAttachmentEndpoint(attachment_neck_4);
  const node_neck_4 = new THREE.Group();
  node_neck_4.name = "Neck__pivot";
  node_neck_4.scale.set(1, 1, 1);
  if (endpoint_neck_4) {
    node_neck_4.position.copy(endpoint_neck_4.start);
    node_neck_4.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_neck_4.position.set(0.0, 0.33488000000000007, 0.0028000000000000004);
    node_neck_4.rotation.set(0.0, 0.0, 0.0);
  }
  node_neck_4.userData.sculptComponent = {"id": "neck", "name": "Neck", "level": "meso", "role": "support", "importance": 0.6, "confidence": 0.8, "primitive": "cylinder", "topologyClass": "assembled-solid", "topologyRationale": "Neck is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "chest", "attachment": {"parentSocket": "chest-neck-base", "localStart": [0.0, 0.33488, 0.0028], "localEnd": [0.0, 0.42728, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.0728, "endRadius": 0.056, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.15400000000000003, "height": 0.09240000000000004, "depth": 0.15400000000000003, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, 0.33488000000000007, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.15400000000000003, 0.09240000000000004, 0.15400000000000003]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "neck", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_neck_4.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "neck", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["chest"] ?? root).add(node_neck_4);
  nodes["neck"] = node_neck_4;
  const mesh_neck_4Geometry = endpoint_neck_4
    ? new THREE.CylinderGeometry(endpoint_neck_4.endRadius, endpoint_neck_4.baseRadius, endpoint_neck_4.length, 32, 12)
    : new THREE.CylinderGeometry(0.5, 0.5, 1, 48, 16);
  if (!endpoint_neck_4) {
    mesh_neck_4Geometry.scale(0.15400000000000003, 0.09240000000000004, 0.15400000000000003);
  }
  const mesh_neck_4 = new THREE.SkinnedMesh(
    mesh_neck_4Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_neck_4.name = "Neck";
  if (endpoint_neck_4) {
    mesh_neck_4.position.copy(endpoint_neck_4.midpoint);
    mesh_neck_4.quaternion.copy(endpoint_neck_4.quaternion);
  }
  mesh_neck_4.castShadow = options.castShadow ?? true;
  mesh_neck_4.receiveShadow = options.receiveShadow ?? true;
  mesh_neck_4.userData.sculptComponent = {"id": "neck", "name": "Neck", "level": "meso", "role": "support", "importance": 0.6, "confidence": 0.8, "primitive": "cylinder", "topologyClass": "assembled-solid", "topologyRationale": "Neck is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "chest", "attachment": {"parentSocket": "chest-neck-base", "localStart": [0.0, 0.33488, 0.0028], "localEnd": [0.0, 0.42728, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.0728, "endRadius": 0.056, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.15400000000000003, "height": 0.09240000000000004, "depth": 0.15400000000000003, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, 0.33488000000000007, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.15400000000000003, 0.09240000000000004, 0.15400000000000003]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "neck", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_neck_4.add(mesh_neck_4);
  meshes["neck"] = mesh_neck_4;
  colliders["neck"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["neck"] ??= [];
  destructionGroups["neck"].push(node_neck_4);

  const endpoint_head_5 = makeAttachmentEndpoint(null);
  const node_head_5 = new THREE.Group();
  node_head_5.name = "Head__pivot";
  node_head_5.scale.set(1, 1, 1);
  if (endpoint_head_5) {
    node_head_5.position.copy(endpoint_head_5.start);
    node_head_5.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_head_5.position.set(0.0, 0.2380000000000001, 0.0);
    node_head_5.rotation.set(0.0, 0.0, 0.0);
  }
  node_head_5.userData.sculptComponent = {"id": "head", "name": "Head", "level": "macro", "role": "body", "importance": 1.0, "confidence": 0.8, "primitive": "ellipsoid", "topologyClass": "assembled-solid", "topologyRationale": "Head is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "neck", "attachment": null, "dimensions": {"width": 0.25760000000000005, "height": 0.31360000000000005, "depth": 0.27440000000000003, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, 0.2380000000000001, 0.0], "rotation": [0, 0, 0], "scale": [0.25760000000000005, 0.31360000000000005, 0.27440000000000003]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "head", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_head_5.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "head", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["neck"] ?? root).add(node_head_5);
  nodes["head"] = node_head_5;
  const mesh_head_5Geometry = endpoint_head_5
    ? new THREE.CylinderGeometry(endpoint_head_5.endRadius, endpoint_head_5.baseRadius, endpoint_head_5.length, 32, 12)
    : new THREE.SphereGeometry(0.5, 64, 40);
  if (!endpoint_head_5) {
    mesh_head_5Geometry.scale(0.25760000000000005, 0.31360000000000005, 0.27440000000000003);
  }
  const mesh_head_5 = new THREE.SkinnedMesh(
    mesh_head_5Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_head_5.name = "Head";
  if (endpoint_head_5) {
    mesh_head_5.position.copy(endpoint_head_5.midpoint);
    mesh_head_5.quaternion.copy(endpoint_head_5.quaternion);
  }
  mesh_head_5.castShadow = options.castShadow ?? true;
  mesh_head_5.receiveShadow = options.receiveShadow ?? true;
  mesh_head_5.userData.sculptComponent = {"id": "head", "name": "Head", "level": "macro", "role": "body", "importance": 1.0, "confidence": 0.8, "primitive": "ellipsoid", "topologyClass": "assembled-solid", "topologyRationale": "Head is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "neck", "attachment": null, "dimensions": {"width": 0.25760000000000005, "height": 0.31360000000000005, "depth": 0.27440000000000003, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, 0.2380000000000001, 0.0], "rotation": [0, 0, 0], "scale": [0.25760000000000005, 0.31360000000000005, 0.27440000000000003]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "head", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_head_5.add(mesh_head_5);
  meshes["head"] = mesh_head_5;
  colliders["head"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["head"] ??= [];
  destructionGroups["head"].push(node_head_5);

  const endpoint_hair_6 = makeAttachmentEndpoint(null);
  const node_hair_6 = new THREE.Group();
  node_hair_6.name = "Hair__pivot";
  node_hair_6.scale.set(1, 1, 1);
  if (endpoint_hair_6) {
    node_hair_6.position.copy(endpoint_hair_6.start);
    node_hair_6.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_hair_6.position.set(0.0, 0.084, -0.005600000000000001);
    node_hair_6.rotation.set(0.0, 0.0, 0.0);
  }
  node_hair_6.userData.sculptComponent = {"id": "hair", "name": "Hair", "level": "meso", "role": "hair", "importance": 0.8, "confidence": 0.8, "primitive": "ellipsoid", "topologyClass": "assembled-solid", "topologyRationale": "Hair is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.28, "height": 0.21840000000000004, "depth": 0.2856, "units": "relative", "confidence": 0.8}, "transform": {"position": [0, 0.084, -0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.28, 0.21840000000000004, 0.2856]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "hair", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hair"}}, "material": "hair", "materialLayers": ["hair"], "deformations": [], "joints": [], "seams": [], "localFeatures": ["short, neutral stylized hairstyle"], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_hair_6.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "hair", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hair"}};
  (nodes["head"] ?? root).add(node_hair_6);
  nodes["hair"] = node_hair_6;
  const mesh_hair_6Geometry = endpoint_hair_6
    ? new THREE.CylinderGeometry(endpoint_hair_6.endRadius, endpoint_hair_6.baseRadius, endpoint_hair_6.length, 32, 12)
    : new THREE.SphereGeometry(0.5, 64, 40);
  if (!endpoint_hair_6) {
    mesh_hair_6Geometry.scale(0.28, 0.21840000000000004, 0.2856);
  }
  const mesh_hair_6 = new THREE.Mesh(
    mesh_hair_6Geometry,
    materialMap["hair"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_hair_6.name = "Hair";
  if (endpoint_hair_6) {
    mesh_hair_6.position.copy(endpoint_hair_6.midpoint);
    mesh_hair_6.quaternion.copy(endpoint_hair_6.quaternion);
  }
  mesh_hair_6.castShadow = options.castShadow ?? true;
  mesh_hair_6.receiveShadow = options.receiveShadow ?? true;
  mesh_hair_6.userData.sculptComponent = {"id": "hair", "name": "Hair", "level": "meso", "role": "hair", "importance": 0.8, "confidence": 0.8, "primitive": "ellipsoid", "topologyClass": "assembled-solid", "topologyRationale": "Hair is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.28, "height": 0.21840000000000004, "depth": 0.2856, "units": "relative", "confidence": 0.8}, "transform": {"position": [0, 0.084, -0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.28, 0.21840000000000004, 0.2856]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "hair", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hair"}}, "material": "hair", "materialLayers": ["hair"], "deformations": [], "joints": [], "seams": [], "localFeatures": ["short, neutral stylized hairstyle"], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_hair_6.add(mesh_hair_6);
  meshes["hair"] = mesh_hair_6;
  colliders["hair"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["hair"] ??= [];
  destructionGroups["hair"].push(node_hair_6);

  const endpoint_brow_l_7 = makeAttachmentEndpoint(null);
  const node_brow_l_7 = new THREE.Group();
  node_brow_l_7.name = "Eyebrow L__pivot";
  node_brow_l_7.scale.set(1, 1, 1);
  if (endpoint_brow_l_7) {
    node_brow_l_7.position.copy(endpoint_brow_l_7.start);
    node_brow_l_7.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_brow_l_7.position.set(0.05600000000000001, 0.033600000000000005, 0.12880000000000003);
    node_brow_l_7.rotation.set(0.0, 0.0, 0.0);
  }
  node_brow_l_7.userData.sculptComponent = {"id": "brow-l", "name": "Eyebrow L", "level": "micro", "role": "detail", "importance": 0.4, "confidence": 0.8, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Eyebrow L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.06160000000000001, "height": 0.011200000000000002, "depth": 0.016800000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.05600000000000001, 0.033600000000000005, 0.12880000000000003], "rotation": [0, 0, 0], "scale": [0.06160000000000001, 0.011200000000000002, 0.016800000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "brow-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hair"}}, "material": "hair", "materialLayers": ["hair"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_brow_l_7.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "brow-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hair"}};
  (nodes["head"] ?? root).add(node_brow_l_7);
  nodes["brow-l"] = node_brow_l_7;
  const mesh_brow_l_7Geometry = endpoint_brow_l_7
    ? new THREE.CylinderGeometry(endpoint_brow_l_7.endRadius, endpoint_brow_l_7.baseRadius, endpoint_brow_l_7.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_brow_l_7) {
    mesh_brow_l_7Geometry.scale(0.06160000000000001, 0.011200000000000002, 0.016800000000000002);
  }
  const mesh_brow_l_7 = new THREE.Mesh(
    mesh_brow_l_7Geometry,
    materialMap["hair"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_brow_l_7.name = "Eyebrow L";
  if (endpoint_brow_l_7) {
    mesh_brow_l_7.position.copy(endpoint_brow_l_7.midpoint);
    mesh_brow_l_7.quaternion.copy(endpoint_brow_l_7.quaternion);
  }
  mesh_brow_l_7.castShadow = options.castShadow ?? true;
  mesh_brow_l_7.receiveShadow = options.receiveShadow ?? true;
  mesh_brow_l_7.userData.sculptComponent = {"id": "brow-l", "name": "Eyebrow L", "level": "micro", "role": "detail", "importance": 0.4, "confidence": 0.8, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Eyebrow L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.06160000000000001, "height": 0.011200000000000002, "depth": 0.016800000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.05600000000000001, 0.033600000000000005, 0.12880000000000003], "rotation": [0, 0, 0], "scale": [0.06160000000000001, 0.011200000000000002, 0.016800000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "brow-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hair"}}, "material": "hair", "materialLayers": ["hair"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_brow_l_7.add(mesh_brow_l_7);
  meshes["brow-l"] = mesh_brow_l_7;
  colliders["brow-l"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["brow-l"] ??= [];
  destructionGroups["brow-l"].push(node_brow_l_7);

  const endpoint_brow_r_8 = makeAttachmentEndpoint(null);
  const node_brow_r_8 = new THREE.Group();
  node_brow_r_8.name = "Eyebrow R__pivot";
  node_brow_r_8.scale.set(1, 1, 1);
  if (endpoint_brow_r_8) {
    node_brow_r_8.position.copy(endpoint_brow_r_8.start);
    node_brow_r_8.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_brow_r_8.position.set(-0.05600000000000001, 0.033600000000000005, 0.12880000000000003);
    node_brow_r_8.rotation.set(0.0, 0.0, 0.0);
  }
  node_brow_r_8.userData.sculptComponent = {"id": "brow-r", "name": "Eyebrow R", "level": "micro", "role": "detail", "importance": 0.4, "confidence": 0.8, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Eyebrow R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.06160000000000001, "height": 0.011200000000000002, "depth": 0.016800000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.05600000000000001, 0.033600000000000005, 0.12880000000000003], "rotation": [0, 0, 0], "scale": [0.06160000000000001, 0.011200000000000002, 0.016800000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "brow-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hair"}}, "material": "hair", "materialLayers": ["hair"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_brow_r_8.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "brow-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hair"}};
  (nodes["head"] ?? root).add(node_brow_r_8);
  nodes["brow-r"] = node_brow_r_8;
  const mesh_brow_r_8Geometry = endpoint_brow_r_8
    ? new THREE.CylinderGeometry(endpoint_brow_r_8.endRadius, endpoint_brow_r_8.baseRadius, endpoint_brow_r_8.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_brow_r_8) {
    mesh_brow_r_8Geometry.scale(0.06160000000000001, 0.011200000000000002, 0.016800000000000002);
  }
  const mesh_brow_r_8 = new THREE.Mesh(
    mesh_brow_r_8Geometry,
    materialMap["hair"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_brow_r_8.name = "Eyebrow R";
  if (endpoint_brow_r_8) {
    mesh_brow_r_8.position.copy(endpoint_brow_r_8.midpoint);
    mesh_brow_r_8.quaternion.copy(endpoint_brow_r_8.quaternion);
  }
  mesh_brow_r_8.castShadow = options.castShadow ?? true;
  mesh_brow_r_8.receiveShadow = options.receiveShadow ?? true;
  mesh_brow_r_8.userData.sculptComponent = {"id": "brow-r", "name": "Eyebrow R", "level": "micro", "role": "detail", "importance": 0.4, "confidence": 0.8, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Eyebrow R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.06160000000000001, "height": 0.011200000000000002, "depth": 0.016800000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.05600000000000001, 0.033600000000000005, 0.12880000000000003], "rotation": [0, 0, 0], "scale": [0.06160000000000001, 0.011200000000000002, 0.016800000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "brow-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hair"}}, "material": "hair", "materialLayers": ["hair"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_brow_r_8.add(mesh_brow_r_8);
  meshes["brow-r"] = mesh_brow_r_8;
  colliders["brow-r"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["brow-r"] ??= [];
  destructionGroups["brow-r"].push(node_brow_r_8);

  const endpoint_ear_l_9 = makeAttachmentEndpoint(null);
  const node_ear_l_9 = new THREE.Group();
  node_ear_l_9.name = "Ear L__pivot";
  node_ear_l_9.scale.set(1, 1, 1);
  if (endpoint_ear_l_9) {
    node_ear_l_9.position.copy(endpoint_ear_l_9.start);
    node_ear_l_9.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_ear_l_9.position.set(0.12040000000000001, 0.005600000000000001, -0.005600000000000001);
    node_ear_l_9.rotation.set(0.0, 0.0, 0.0);
  }
  node_ear_l_9.userData.sculptComponent = {"id": "ear-l", "name": "Ear L", "level": "micro", "role": "detail", "importance": 0.45, "confidence": 0.8, "primitive": "ellipsoid", "topologyClass": "assembled-solid", "topologyRationale": "Ear L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.0252, "height": 0.0728, "depth": 0.04760000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.12040000000000001, 0.005600000000000001, -0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.0252, 0.0728, 0.04760000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ear-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": ["outer helix reads as a flattened shell against the skull, not a disc"], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_ear_l_9.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ear-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["head"] ?? root).add(node_ear_l_9);
  nodes["ear-l"] = node_ear_l_9;
  const mesh_ear_l_9Geometry = endpoint_ear_l_9
    ? new THREE.CylinderGeometry(endpoint_ear_l_9.endRadius, endpoint_ear_l_9.baseRadius, endpoint_ear_l_9.length, 32, 12)
    : new THREE.SphereGeometry(0.5, 64, 40);
  if (!endpoint_ear_l_9) {
    mesh_ear_l_9Geometry.scale(0.0252, 0.0728, 0.04760000000000001);
  }
  const mesh_ear_l_9 = new THREE.Mesh(
    mesh_ear_l_9Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_ear_l_9.name = "Ear L";
  if (endpoint_ear_l_9) {
    mesh_ear_l_9.position.copy(endpoint_ear_l_9.midpoint);
    mesh_ear_l_9.quaternion.copy(endpoint_ear_l_9.quaternion);
  }
  mesh_ear_l_9.castShadow = options.castShadow ?? true;
  mesh_ear_l_9.receiveShadow = options.receiveShadow ?? true;
  mesh_ear_l_9.userData.sculptComponent = {"id": "ear-l", "name": "Ear L", "level": "micro", "role": "detail", "importance": 0.45, "confidence": 0.8, "primitive": "ellipsoid", "topologyClass": "assembled-solid", "topologyRationale": "Ear L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.0252, "height": 0.0728, "depth": 0.04760000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.12040000000000001, 0.005600000000000001, -0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.0252, 0.0728, 0.04760000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ear-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": ["outer helix reads as a flattened shell against the skull, not a disc"], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_ear_l_9.add(mesh_ear_l_9);
  meshes["ear-l"] = mesh_ear_l_9;
  colliders["ear-l"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["ear-l"] ??= [];
  destructionGroups["ear-l"].push(node_ear_l_9);

  const endpoint_ear_r_10 = makeAttachmentEndpoint(null);
  const node_ear_r_10 = new THREE.Group();
  node_ear_r_10.name = "Ear R__pivot";
  node_ear_r_10.scale.set(1, 1, 1);
  if (endpoint_ear_r_10) {
    node_ear_r_10.position.copy(endpoint_ear_r_10.start);
    node_ear_r_10.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_ear_r_10.position.set(-0.12040000000000001, 0.005600000000000001, -0.005600000000000001);
    node_ear_r_10.rotation.set(0.0, 0.0, 0.0);
  }
  node_ear_r_10.userData.sculptComponent = {"id": "ear-r", "name": "Ear R", "level": "micro", "role": "detail", "importance": 0.45, "confidence": 0.8, "primitive": "ellipsoid", "topologyClass": "assembled-solid", "topologyRationale": "Ear R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.0252, "height": 0.0728, "depth": 0.04760000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.12040000000000001, 0.005600000000000001, -0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.0252, 0.0728, 0.04760000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ear-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": ["outer helix reads as a flattened shell against the skull, not a disc"], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_ear_r_10.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ear-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["head"] ?? root).add(node_ear_r_10);
  nodes["ear-r"] = node_ear_r_10;
  const mesh_ear_r_10Geometry = endpoint_ear_r_10
    ? new THREE.CylinderGeometry(endpoint_ear_r_10.endRadius, endpoint_ear_r_10.baseRadius, endpoint_ear_r_10.length, 32, 12)
    : new THREE.SphereGeometry(0.5, 64, 40);
  if (!endpoint_ear_r_10) {
    mesh_ear_r_10Geometry.scale(0.0252, 0.0728, 0.04760000000000001);
  }
  const mesh_ear_r_10 = new THREE.Mesh(
    mesh_ear_r_10Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_ear_r_10.name = "Ear R";
  if (endpoint_ear_r_10) {
    mesh_ear_r_10.position.copy(endpoint_ear_r_10.midpoint);
    mesh_ear_r_10.quaternion.copy(endpoint_ear_r_10.quaternion);
  }
  mesh_ear_r_10.castShadow = options.castShadow ?? true;
  mesh_ear_r_10.receiveShadow = options.receiveShadow ?? true;
  mesh_ear_r_10.userData.sculptComponent = {"id": "ear-r", "name": "Ear R", "level": "micro", "role": "detail", "importance": 0.45, "confidence": 0.8, "primitive": "ellipsoid", "topologyClass": "assembled-solid", "topologyRationale": "Ear R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.0252, "height": 0.0728, "depth": 0.04760000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.12040000000000001, 0.005600000000000001, -0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.0252, 0.0728, 0.04760000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ear-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": ["outer helix reads as a flattened shell against the skull, not a disc"], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_ear_r_10.add(mesh_ear_r_10);
  meshes["ear-r"] = mesh_ear_r_10;
  colliders["ear-r"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["ear-r"] ??= [];
  destructionGroups["ear-r"].push(node_ear_r_10);

  const endpoint_nose_11 = makeAttachmentEndpoint(null);
  const node_nose_11 = new THREE.Group();
  node_nose_11.name = "Nose__pivot";
  node_nose_11.scale.set(1, 1, 1);
  if (endpoint_nose_11) {
    node_nose_11.position.copy(endpoint_nose_11.start);
    node_nose_11.rotation.set(1.4, 0.0, 0.0);
  } else {
    node_nose_11.position.set(0.0, -0.011200000000000002, 0.14);
    node_nose_11.rotation.set(1.4, 0.0, 0.0);
  }
  node_nose_11.userData.sculptComponent = {"id": "nose", "name": "Nose", "level": "micro", "role": "detail", "importance": 0.4, "confidence": 0.8, "primitive": "ellipsoid", "topologyClass": "assembled-solid", "topologyRationale": "Nose is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.039200000000000006, "height": 0.07840000000000001, "depth": 0.0504, "units": "relative", "confidence": 0.8}, "transform": {"position": [0, -0.011200000000000002, 0.14], "rotation": [1.4, 0, 0], "scale": [0.039200000000000006, 0.07840000000000001, 0.0504]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "nose", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_nose_11.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "nose", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["head"] ?? root).add(node_nose_11);
  nodes["nose"] = node_nose_11;
  const mesh_nose_11Geometry = endpoint_nose_11
    ? new THREE.CylinderGeometry(endpoint_nose_11.endRadius, endpoint_nose_11.baseRadius, endpoint_nose_11.length, 32, 12)
    : new THREE.SphereGeometry(0.5, 64, 40);
  if (!endpoint_nose_11) {
    mesh_nose_11Geometry.scale(0.039200000000000006, 0.07840000000000001, 0.0504);
  }
  const mesh_nose_11 = new THREE.Mesh(
    mesh_nose_11Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_nose_11.name = "Nose";
  if (endpoint_nose_11) {
    mesh_nose_11.position.copy(endpoint_nose_11.midpoint);
    mesh_nose_11.quaternion.copy(endpoint_nose_11.quaternion);
  }
  mesh_nose_11.castShadow = options.castShadow ?? true;
  mesh_nose_11.receiveShadow = options.receiveShadow ?? true;
  mesh_nose_11.userData.sculptComponent = {"id": "nose", "name": "Nose", "level": "micro", "role": "detail", "importance": 0.4, "confidence": 0.8, "primitive": "ellipsoid", "topologyClass": "assembled-solid", "topologyRationale": "Nose is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.039200000000000006, "height": 0.07840000000000001, "depth": 0.0504, "units": "relative", "confidence": 0.8}, "transform": {"position": [0, -0.011200000000000002, 0.14], "rotation": [1.4, 0, 0], "scale": [0.039200000000000006, 0.07840000000000001, 0.0504]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "nose", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_nose_11.add(mesh_nose_11);
  meshes["nose"] = mesh_nose_11;
  colliders["nose"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["nose"] ??= [];
  destructionGroups["nose"].push(node_nose_11);

  const endpoint_mouth_12 = makeAttachmentEndpoint(null);
  const node_mouth_12 = new THREE.Group();
  node_mouth_12.name = "Mouth__pivot";
  node_mouth_12.scale.set(1, 1, 1);
  if (endpoint_mouth_12) {
    node_mouth_12.position.copy(endpoint_mouth_12.start);
    node_mouth_12.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_mouth_12.position.set(0.0, -0.09520000000000002, 0.12880000000000003);
    node_mouth_12.rotation.set(0.0, 0.0, 0.0);
  }
  node_mouth_12.userData.sculptComponent = {"id": "mouth", "name": "Mouth", "level": "micro", "role": "detail", "importance": 0.4, "confidence": 0.8, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Mouth is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.06720000000000001, "height": 0.011200000000000002, "depth": 0.014000000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0, -0.09520000000000002, 0.12880000000000003], "rotation": [0, 0, 0], "scale": [0.06720000000000001, 0.011200000000000002, 0.014000000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "mouth", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "lips"}}, "material": "lips", "materialLayers": ["lips"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_mouth_12.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "mouth", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "lips"}};
  (nodes["head"] ?? root).add(node_mouth_12);
  nodes["mouth"] = node_mouth_12;
  const mesh_mouth_12Geometry = endpoint_mouth_12
    ? new THREE.CylinderGeometry(endpoint_mouth_12.endRadius, endpoint_mouth_12.baseRadius, endpoint_mouth_12.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_mouth_12) {
    mesh_mouth_12Geometry.scale(0.06720000000000001, 0.011200000000000002, 0.014000000000000002);
  }
  const mesh_mouth_12 = new THREE.Mesh(
    mesh_mouth_12Geometry,
    materialMap["lips"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_mouth_12.name = "Mouth";
  if (endpoint_mouth_12) {
    mesh_mouth_12.position.copy(endpoint_mouth_12.midpoint);
    mesh_mouth_12.quaternion.copy(endpoint_mouth_12.quaternion);
  }
  mesh_mouth_12.castShadow = options.castShadow ?? true;
  mesh_mouth_12.receiveShadow = options.receiveShadow ?? true;
  mesh_mouth_12.userData.sculptComponent = {"id": "mouth", "name": "Mouth", "level": "micro", "role": "detail", "importance": 0.4, "confidence": 0.8, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Mouth is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.06720000000000001, "height": 0.011200000000000002, "depth": 0.014000000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0, -0.09520000000000002, 0.12880000000000003], "rotation": [0, 0, 0], "scale": [0.06720000000000001, 0.011200000000000002, 0.014000000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "mouth", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "lips"}}, "material": "lips", "materialLayers": ["lips"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_mouth_12.add(mesh_mouth_12);
  meshes["mouth"] = mesh_mouth_12;
  colliders["mouth"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["mouth"] ??= [];
  destructionGroups["mouth"].push(node_mouth_12);

  const endpoint_eye_l_13 = makeAttachmentEndpoint(null);
  const node_eye_l_13 = new THREE.Group();
  node_eye_l_13.name = "Eye L__pivot";
  node_eye_l_13.scale.set(1, 1, 1);
  if (endpoint_eye_l_13) {
    node_eye_l_13.position.copy(endpoint_eye_l_13.start);
    node_eye_l_13.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_eye_l_13.position.set(0.053200000000000004, 0.008400000000000001, 0.11200000000000002);
    node_eye_l_13.rotation.set(0.0, 0.0, 0.0);
  }
  node_eye_l_13.userData.sculptComponent = {"id": "eye-l", "name": "Eye L", "level": "micro", "role": "detail", "importance": 0.5, "confidence": 0.8, "primitive": "sphere", "topologyClass": "assembled-solid", "topologyRationale": "Eye L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.030800000000000004, "height": 0.030800000000000004, "depth": 0.030800000000000004, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.053200000000000004, 0.008400000000000001, 0.11200000000000002], "rotation": [0, 0, 0], "scale": [0.030800000000000004, 0.030800000000000004, 0.030800000000000004]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "eye-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "eye"}}, "material": "eye", "materialLayers": ["eye"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_eye_l_13.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "eye-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "eye"}};
  (nodes["head"] ?? root).add(node_eye_l_13);
  nodes["eye-l"] = node_eye_l_13;
  const mesh_eye_l_13Geometry = endpoint_eye_l_13
    ? new THREE.CylinderGeometry(endpoint_eye_l_13.endRadius, endpoint_eye_l_13.baseRadius, endpoint_eye_l_13.length, 32, 12)
    : new THREE.SphereGeometry(0.5, 64, 40);
  if (!endpoint_eye_l_13) {
    mesh_eye_l_13Geometry.scale(0.030800000000000004, 0.030800000000000004, 0.030800000000000004);
  }
  const mesh_eye_l_13 = new THREE.Mesh(
    mesh_eye_l_13Geometry,
    materialMap["eye"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_eye_l_13.name = "Eye L";
  if (endpoint_eye_l_13) {
    mesh_eye_l_13.position.copy(endpoint_eye_l_13.midpoint);
    mesh_eye_l_13.quaternion.copy(endpoint_eye_l_13.quaternion);
  }
  mesh_eye_l_13.castShadow = options.castShadow ?? true;
  mesh_eye_l_13.receiveShadow = options.receiveShadow ?? true;
  mesh_eye_l_13.userData.sculptComponent = {"id": "eye-l", "name": "Eye L", "level": "micro", "role": "detail", "importance": 0.5, "confidence": 0.8, "primitive": "sphere", "topologyClass": "assembled-solid", "topologyRationale": "Eye L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.030800000000000004, "height": 0.030800000000000004, "depth": 0.030800000000000004, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.053200000000000004, 0.008400000000000001, 0.11200000000000002], "rotation": [0, 0, 0], "scale": [0.030800000000000004, 0.030800000000000004, 0.030800000000000004]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "eye-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "eye"}}, "material": "eye", "materialLayers": ["eye"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_eye_l_13.add(mesh_eye_l_13);
  meshes["eye-l"] = mesh_eye_l_13;
  colliders["eye-l"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["eye-l"] ??= [];
  destructionGroups["eye-l"].push(node_eye_l_13);

  const endpoint_eye_cavity_l_14 = makeAttachmentEndpoint(null);
  const node_eye_cavity_l_14 = new THREE.Group();
  node_eye_cavity_l_14.name = "Eye cavity L__pivot";
  node_eye_cavity_l_14.scale.set(1, 1, 1);
  if (endpoint_eye_cavity_l_14) {
    node_eye_cavity_l_14.position.copy(endpoint_eye_cavity_l_14.start);
    node_eye_cavity_l_14.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_eye_cavity_l_14.position.set(0.053200000000000004, 0.008400000000000001, 0.12040000000000001);
    node_eye_cavity_l_14.rotation.set(0.0, 0.0, 0.0);
  }
  node_eye_cavity_l_14.userData.sculptComponent = {"id": "eye-cavity-l", "name": "Eye cavity L", "level": "micro", "role": "cavity", "importance": 0.4, "confidence": 0.8, "primitive": "sphere", "topologyClass": "implicit", "topologyRationale": "The eye reads as a recessed concave cavity carved out of the head volume with a boolean subtraction (US-004), not a flat decal or shaded patch.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals", "sdf": {"primitives": [{"id": "shell", "type": "sphere", "center": [0.0, 0.0, 0.0], "radius": 0.0252}, {"id": "carve", "type": "sphere", "center": [0.0, 0.0, 0.0154], "radius": 0.021}], "operations": [{"id": "socket", "type": "subtract", "left": "shell", "right": "carve"}], "bounds": {"min": [-0.0455, -0.0455, -0.0455], "max": [0.0455, 0.0455, 0.0455]}, "resolution": 24}}, "parent": "head", "attachment": null, "dimensions": {"width": 0.0504, "height": 0.0504, "depth": 0.0504, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.053200000000000004, 0.008400000000000001, 0.12040000000000001], "rotation": [0, 0, 0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "eye-cavity-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_eye_cavity_l_14.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "eye-cavity-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["head"] ?? root).add(node_eye_cavity_l_14);
  nodes["eye-cavity-l"] = node_eye_cavity_l_14;
  const mesh_eye_cavity_l_14Geometry = polygonizeSdf({"primitives": [{"id": "shell", "type": "sphere", "center": [0.0, 0.0, 0.0], "radius": 0.0252}, {"id": "carve", "type": "sphere", "center": [0.0, 0.0, 0.0154], "radius": 0.021}], "operations": [{"id": "socket", "type": "subtract", "left": "shell", "right": "carve"}], "bounds": {"min": [-0.0455, -0.0455, -0.0455], "max": [0.0455, 0.0455, 0.0455]}, "resolution": 24});
  if (!endpoint_eye_cavity_l_14) {
    mesh_eye_cavity_l_14Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_eye_cavity_l_14 = new THREE.Mesh(
    mesh_eye_cavity_l_14Geometry,
    createSculptMaterial("skin", {"id": "skin", "name": "Base material", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#e8b98f", "color": "#e8b98f", "albedo": {"dominant": "#e8b98f", "secondary": ["#be9875"]}, "colorVariation": {"palette": ["#e8b98f", "#be9875"], "pattern": "flat", "amplitude": 0.05, "heightCorrelation": 0.0}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [2.0, 2.0], "anisotropy": 8, "texelDensityIntent": "Preserve stable world/object-scale detail; do not stretch micro detail with component scale."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 2.0, "amplitude": 0.42, "role": "broad color and height breakup"}, {"id": "meso", "frequency": 12.0, "amplitude": 0.22, "role": "ridges, pores, grain, dents, or equivalent visible relief"}, {"id": "micro", "frequency": 56.0, "amplitude": 0.08, "role": "highlight breakup visible under grazing light"}], "roughness": {"base": 0.55, "variation": 0.08}, "metalness": {"base": 0.0, "variation": 0.0}, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.35, "scale": 24.0, "space": "tangent"}, "bump": {"pattern": "none", "amplitude": 0.0, "scale": 1.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.25, "contactShadowBias": 0.35, "notes": "Darken creases, seams, intersections, and recessed local features."}, "wear": {"edgeWear": 0.0, "scratches": [], "chips": []}, "dirt": {"amount": 0.0, "cavityBias": 0.0, "color": "#2F2A22"}, "localOverrides": [], "shaderNotes": ["Prefer MeshPhysicalMaterial when clearcoat, sheen, transmission, or thin-surface response is observed; otherwise use MeshStandardMaterial-compatible PBR channels.", "Generate albedo, roughness, height/normal, and AO independently; never alias albedo into roughness.", "Use normal/bump/displacement only when they map to observed surface relief.", "Use displacement geometry when the observed relief changes the close-up silhouette; texture-only relief is insufficient there."], "notes": "Replace with image-derived color, roughness, noise, and edge-wear notes."}, options, true)
  );
  mesh_eye_cavity_l_14.name = "Eye cavity L";
  if (endpoint_eye_cavity_l_14) {
    mesh_eye_cavity_l_14.position.copy(endpoint_eye_cavity_l_14.midpoint);
    mesh_eye_cavity_l_14.quaternion.copy(endpoint_eye_cavity_l_14.quaternion);
  }
  mesh_eye_cavity_l_14.castShadow = options.castShadow ?? true;
  mesh_eye_cavity_l_14.receiveShadow = options.receiveShadow ?? true;
  mesh_eye_cavity_l_14.userData.sculptComponent = {"id": "eye-cavity-l", "name": "Eye cavity L", "level": "micro", "role": "cavity", "importance": 0.4, "confidence": 0.8, "primitive": "sphere", "topologyClass": "implicit", "topologyRationale": "The eye reads as a recessed concave cavity carved out of the head volume with a boolean subtraction (US-004), not a flat decal or shaded patch.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals", "sdf": {"primitives": [{"id": "shell", "type": "sphere", "center": [0.0, 0.0, 0.0], "radius": 0.0252}, {"id": "carve", "type": "sphere", "center": [0.0, 0.0, 0.0154], "radius": 0.021}], "operations": [{"id": "socket", "type": "subtract", "left": "shell", "right": "carve"}], "bounds": {"min": [-0.0455, -0.0455, -0.0455], "max": [0.0455, 0.0455, 0.0455]}, "resolution": 24}}, "parent": "head", "attachment": null, "dimensions": {"width": 0.0504, "height": 0.0504, "depth": 0.0504, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.053200000000000004, 0.008400000000000001, 0.12040000000000001], "rotation": [0, 0, 0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "eye-cavity-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_eye_cavity_l_14.add(mesh_eye_cavity_l_14);
  meshes["eye-cavity-l"] = mesh_eye_cavity_l_14;
  colliders["eye-cavity-l"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["eye-cavity-l"] ??= [];
  destructionGroups["eye-cavity-l"].push(node_eye_cavity_l_14);

  const endpoint_eye_r_15 = makeAttachmentEndpoint(null);
  const node_eye_r_15 = new THREE.Group();
  node_eye_r_15.name = "Eye R__pivot";
  node_eye_r_15.scale.set(1, 1, 1);
  if (endpoint_eye_r_15) {
    node_eye_r_15.position.copy(endpoint_eye_r_15.start);
    node_eye_r_15.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_eye_r_15.position.set(-0.053200000000000004, 0.008400000000000001, 0.11200000000000002);
    node_eye_r_15.rotation.set(0.0, 0.0, 0.0);
  }
  node_eye_r_15.userData.sculptComponent = {"id": "eye-r", "name": "Eye R", "level": "micro", "role": "detail", "importance": 0.5, "confidence": 0.8, "primitive": "sphere", "topologyClass": "assembled-solid", "topologyRationale": "Eye R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.030800000000000004, "height": 0.030800000000000004, "depth": 0.030800000000000004, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.053200000000000004, 0.008400000000000001, 0.11200000000000002], "rotation": [0, 0, 0], "scale": [0.030800000000000004, 0.030800000000000004, 0.030800000000000004]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "eye-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "eye"}}, "material": "eye", "materialLayers": ["eye"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_eye_r_15.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "eye-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "eye"}};
  (nodes["head"] ?? root).add(node_eye_r_15);
  nodes["eye-r"] = node_eye_r_15;
  const mesh_eye_r_15Geometry = endpoint_eye_r_15
    ? new THREE.CylinderGeometry(endpoint_eye_r_15.endRadius, endpoint_eye_r_15.baseRadius, endpoint_eye_r_15.length, 32, 12)
    : new THREE.SphereGeometry(0.5, 64, 40);
  if (!endpoint_eye_r_15) {
    mesh_eye_r_15Geometry.scale(0.030800000000000004, 0.030800000000000004, 0.030800000000000004);
  }
  const mesh_eye_r_15 = new THREE.Mesh(
    mesh_eye_r_15Geometry,
    materialMap["eye"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_eye_r_15.name = "Eye R";
  if (endpoint_eye_r_15) {
    mesh_eye_r_15.position.copy(endpoint_eye_r_15.midpoint);
    mesh_eye_r_15.quaternion.copy(endpoint_eye_r_15.quaternion);
  }
  mesh_eye_r_15.castShadow = options.castShadow ?? true;
  mesh_eye_r_15.receiveShadow = options.receiveShadow ?? true;
  mesh_eye_r_15.userData.sculptComponent = {"id": "eye-r", "name": "Eye R", "level": "micro", "role": "detail", "importance": 0.5, "confidence": 0.8, "primitive": "sphere", "topologyClass": "assembled-solid", "topologyRationale": "Eye R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "head", "attachment": null, "dimensions": {"width": 0.030800000000000004, "height": 0.030800000000000004, "depth": 0.030800000000000004, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.053200000000000004, 0.008400000000000001, 0.11200000000000002], "rotation": [0, 0, 0], "scale": [0.030800000000000004, 0.030800000000000004, 0.030800000000000004]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "eye-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "eye"}}, "material": "eye", "materialLayers": ["eye"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_eye_r_15.add(mesh_eye_r_15);
  meshes["eye-r"] = mesh_eye_r_15;
  colliders["eye-r"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["eye-r"] ??= [];
  destructionGroups["eye-r"].push(node_eye_r_15);

  const endpoint_eye_cavity_r_16 = makeAttachmentEndpoint(null);
  const node_eye_cavity_r_16 = new THREE.Group();
  node_eye_cavity_r_16.name = "Eye cavity R__pivot";
  node_eye_cavity_r_16.scale.set(1, 1, 1);
  if (endpoint_eye_cavity_r_16) {
    node_eye_cavity_r_16.position.copy(endpoint_eye_cavity_r_16.start);
    node_eye_cavity_r_16.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_eye_cavity_r_16.position.set(-0.053200000000000004, 0.008400000000000001, 0.12040000000000001);
    node_eye_cavity_r_16.rotation.set(0.0, 0.0, 0.0);
  }
  node_eye_cavity_r_16.userData.sculptComponent = {"id": "eye-cavity-r", "name": "Eye cavity R", "level": "micro", "role": "cavity", "importance": 0.4, "confidence": 0.8, "primitive": "sphere", "topologyClass": "implicit", "topologyRationale": "The eye reads as a recessed concave cavity carved out of the head volume with a boolean subtraction (US-004), not a flat decal or shaded patch.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals", "sdf": {"primitives": [{"id": "shell", "type": "sphere", "center": [0.0, 0.0, 0.0], "radius": 0.0252}, {"id": "carve", "type": "sphere", "center": [0.0, 0.0, 0.0154], "radius": 0.021}], "operations": [{"id": "socket", "type": "subtract", "left": "shell", "right": "carve"}], "bounds": {"min": [-0.0455, -0.0455, -0.0455], "max": [0.0455, 0.0455, 0.0455]}, "resolution": 24}}, "parent": "head", "attachment": null, "dimensions": {"width": 0.0504, "height": 0.0504, "depth": 0.0504, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.053200000000000004, 0.008400000000000001, 0.12040000000000001], "rotation": [0, 0, 0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "eye-cavity-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_eye_cavity_r_16.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "eye-cavity-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["head"] ?? root).add(node_eye_cavity_r_16);
  nodes["eye-cavity-r"] = node_eye_cavity_r_16;
  const mesh_eye_cavity_r_16Geometry = polygonizeSdf({"primitives": [{"id": "shell", "type": "sphere", "center": [0.0, 0.0, 0.0], "radius": 0.0252}, {"id": "carve", "type": "sphere", "center": [0.0, 0.0, 0.0154], "radius": 0.021}], "operations": [{"id": "socket", "type": "subtract", "left": "shell", "right": "carve"}], "bounds": {"min": [-0.0455, -0.0455, -0.0455], "max": [0.0455, 0.0455, 0.0455]}, "resolution": 24});
  if (!endpoint_eye_cavity_r_16) {
    mesh_eye_cavity_r_16Geometry.scale(1.0, 1.0, 1.0);
  }
  const mesh_eye_cavity_r_16 = new THREE.Mesh(
    mesh_eye_cavity_r_16Geometry,
    createSculptMaterial("skin", {"id": "skin", "name": "Base material", "type": "standard", "shaderModel": "MeshStandardMaterial / PBR approximation", "baseColor": "#e8b98f", "color": "#e8b98f", "albedo": {"dominant": "#e8b98f", "secondary": ["#be9875"]}, "colorVariation": {"palette": ["#e8b98f", "#be9875"], "pattern": "flat", "amplitude": 0.05, "heightCorrelation": 0.0}, "textureResolution": 1024, "textureProjection": {"mode": "uv", "repeat": [2.0, 2.0], "anisotropy": 8, "texelDensityIntent": "Preserve stable world/object-scale detail; do not stretch micro detail with component scale."}, "surfaceFrequencyBands": [{"id": "macro", "frequency": 2.0, "amplitude": 0.42, "role": "broad color and height breakup"}, {"id": "meso", "frequency": 12.0, "amplitude": 0.22, "role": "ridges, pores, grain, dents, or equivalent visible relief"}, {"id": "micro", "frequency": 56.0, "amplitude": 0.08, "role": "highlight breakup visible under grazing light"}], "roughness": {"base": 0.55, "variation": 0.08}, "metalness": {"base": 0.0, "variation": 0.0}, "normal": {"pattern": "derived-from-independent-height-field", "strength": 0.35, "scale": 24.0, "space": "tangent"}, "bump": {"pattern": "none", "amplitude": 0.0, "scale": 1.0}, "displacement": {"pattern": "none", "amplitude": 0.0, "scale": 1.0, "silhouetteAffects": false}, "ambientOcclusion": {"cavityStrength": 0.25, "contactShadowBias": 0.35, "notes": "Darken creases, seams, intersections, and recessed local features."}, "wear": {"edgeWear": 0.0, "scratches": [], "chips": []}, "dirt": {"amount": 0.0, "cavityBias": 0.0, "color": "#2F2A22"}, "localOverrides": [], "shaderNotes": ["Prefer MeshPhysicalMaterial when clearcoat, sheen, transmission, or thin-surface response is observed; otherwise use MeshStandardMaterial-compatible PBR channels.", "Generate albedo, roughness, height/normal, and AO independently; never alias albedo into roughness.", "Use normal/bump/displacement only when they map to observed surface relief.", "Use displacement geometry when the observed relief changes the close-up silhouette; texture-only relief is insufficient there."], "notes": "Replace with image-derived color, roughness, noise, and edge-wear notes."}, options, true)
  );
  mesh_eye_cavity_r_16.name = "Eye cavity R";
  if (endpoint_eye_cavity_r_16) {
    mesh_eye_cavity_r_16.position.copy(endpoint_eye_cavity_r_16.midpoint);
    mesh_eye_cavity_r_16.quaternion.copy(endpoint_eye_cavity_r_16.quaternion);
  }
  mesh_eye_cavity_r_16.castShadow = options.castShadow ?? true;
  mesh_eye_cavity_r_16.receiveShadow = options.receiveShadow ?? true;
  mesh_eye_cavity_r_16.userData.sculptComponent = {"id": "eye-cavity-r", "name": "Eye cavity R", "level": "micro", "role": "cavity", "importance": 0.4, "confidence": 0.8, "primitive": "sphere", "topologyClass": "implicit", "topologyRationale": "The eye reads as a recessed concave cavity carved out of the head volume with a boolean subtraction (US-004), not a flat decal or shaded patch.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals", "sdf": {"primitives": [{"id": "shell", "type": "sphere", "center": [0.0, 0.0, 0.0], "radius": 0.0252}, {"id": "carve", "type": "sphere", "center": [0.0, 0.0, 0.0154], "radius": 0.021}], "operations": [{"id": "socket", "type": "subtract", "left": "shell", "right": "carve"}], "bounds": {"min": [-0.0455, -0.0455, -0.0455], "max": [0.0455, 0.0455, 0.0455]}, "resolution": 24}}, "parent": "head", "attachment": null, "dimensions": {"width": 0.0504, "height": 0.0504, "depth": 0.0504, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.053200000000000004, 0.008400000000000001, 0.12040000000000001], "rotation": [0, 0, 0], "scale": [1.0, 1.0, 1.0]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "eye-cavity-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_eye_cavity_r_16.add(mesh_eye_cavity_r_16);
  meshes["eye-cavity-r"] = mesh_eye_cavity_r_16;
  colliders["eye-cavity-r"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["eye-cavity-r"] ??= [];
  destructionGroups["eye-cavity-r"].push(node_eye_cavity_r_16);

  const attachment_clavicle_l_17 = {"parentSocket": "chest-clavicle-l", "localStart": [0.05152, 0.34048, 0.0056], "localEnd": [0.322, 0.33488, 0.0112], "contactType": "rigid-weld", "baseRadius": 0.0308, "endRadius": 0.0476, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_clavicle_l_17 = makeAttachmentEndpoint(attachment_clavicle_l_17);
  const node_clavicle_l_17 = new THREE.Group();
  node_clavicle_l_17.name = "Clavicle L__pivot";
  node_clavicle_l_17.scale.set(1, 1, 1);
  if (endpoint_clavicle_l_17) {
    node_clavicle_l_17.position.copy(endpoint_clavicle_l_17.start);
    node_clavicle_l_17.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_clavicle_l_17.position.set(0.05152, 0.34048000000000006, 0.005600000000000001);
    node_clavicle_l_17.rotation.set(0.0, 0.0, 0.0);
  }
  node_clavicle_l_17.userData.sculptComponent = {"id": "clavicle-l", "name": "Clavicle L", "level": "meso", "role": "support", "importance": 0.6, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Clavicle L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "chest", "attachment": {"parentSocket": "chest-clavicle-l", "localStart": [0.05152, 0.34048, 0.0056], "localEnd": [0.322, 0.33488, 0.0112], "contactType": "rigid-weld", "baseRadius": 0.0308, "endRadius": 0.0476, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.27048, "height": 0.09520000000000002, "depth": 0.09520000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.05152, 0.34048000000000006, 0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.27048, 0.09520000000000002, 0.09520000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "clavicle-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_clavicle_l_17.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "clavicle-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["chest"] ?? root).add(node_clavicle_l_17);
  nodes["clavicle-l"] = node_clavicle_l_17;
  const mesh_clavicle_l_17Geometry = endpoint_clavicle_l_17
    ? new THREE.CylinderGeometry(endpoint_clavicle_l_17.endRadius, endpoint_clavicle_l_17.baseRadius, endpoint_clavicle_l_17.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_clavicle_l_17) {
    mesh_clavicle_l_17Geometry.scale(0.27048, 0.09520000000000002, 0.09520000000000002);
  }
  const mesh_clavicle_l_17 = new THREE.SkinnedMesh(
    mesh_clavicle_l_17Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_clavicle_l_17.name = "Clavicle L";
  if (endpoint_clavicle_l_17) {
    mesh_clavicle_l_17.position.copy(endpoint_clavicle_l_17.midpoint);
    mesh_clavicle_l_17.quaternion.copy(endpoint_clavicle_l_17.quaternion);
  }
  mesh_clavicle_l_17.castShadow = options.castShadow ?? true;
  mesh_clavicle_l_17.receiveShadow = options.receiveShadow ?? true;
  mesh_clavicle_l_17.userData.sculptComponent = {"id": "clavicle-l", "name": "Clavicle L", "level": "meso", "role": "support", "importance": 0.6, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Clavicle L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "chest", "attachment": {"parentSocket": "chest-clavicle-l", "localStart": [0.05152, 0.34048, 0.0056], "localEnd": [0.322, 0.33488, 0.0112], "contactType": "rigid-weld", "baseRadius": 0.0308, "endRadius": 0.0476, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.27048, "height": 0.09520000000000002, "depth": 0.09520000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.05152, 0.34048000000000006, 0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.27048, 0.09520000000000002, 0.09520000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "clavicle-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_clavicle_l_17.add(mesh_clavicle_l_17);
  meshes["clavicle-l"] = mesh_clavicle_l_17;
  colliders["clavicle-l"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["clavicle-l"] ??= [];
  destructionGroups["clavicle-l"].push(node_clavicle_l_17);

  const attachment_upper_arm_l_18 = {"parentSocket": "clavicle-shoulder-l", "localStart": [0.27048, -0.0056, 0.0056], "localEnd": [0.33442, -0.32104, 0.0056], "contactType": "socket-joint", "baseRadius": 0.0448, "endRadius": 0.0364, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_upper_arm_l_18 = makeAttachmentEndpoint(attachment_upper_arm_l_18);
  const node_upper_arm_l_18 = new THREE.Group();
  node_upper_arm_l_18.name = "Upper arm L__pivot";
  node_upper_arm_l_18.scale.set(1, 1, 1);
  if (endpoint_upper_arm_l_18) {
    node_upper_arm_l_18.position.copy(endpoint_upper_arm_l_18.start);
    node_upper_arm_l_18.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_upper_arm_l_18.position.set(0.27048, -0.005599999999999994, 0.005600000000000001);
    node_upper_arm_l_18.rotation.set(0.0, 0.0, 0.0);
  }
  node_upper_arm_l_18.userData.sculptComponent = {"id": "upper-arm-l", "name": "Upper arm L", "level": "meso", "role": "arm", "importance": 0.7, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Upper arm L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "clavicle-l", "attachment": {"parentSocket": "clavicle-shoulder-l", "localStart": [0.27048, -0.0056, 0.0056], "localEnd": [0.33442, -0.32104, 0.0056], "contactType": "socket-joint", "baseRadius": 0.0448, "endRadius": 0.0364, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.08960000000000001, "height": 0.32186000000000003, "depth": 0.08960000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.27048, -0.005599999999999994, 0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.08960000000000001, 0.32186000000000003, 0.08960000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "upper-arm-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shirt"}}, "material": "shirt", "materialLayers": ["shirt"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_upper_arm_l_18.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "upper-arm-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shirt"}};
  (nodes["clavicle-l"] ?? root).add(node_upper_arm_l_18);
  nodes["upper-arm-l"] = node_upper_arm_l_18;
  const mesh_upper_arm_l_18Geometry = endpoint_upper_arm_l_18
    ? new THREE.CylinderGeometry(endpoint_upper_arm_l_18.endRadius, endpoint_upper_arm_l_18.baseRadius, endpoint_upper_arm_l_18.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_upper_arm_l_18) {
    mesh_upper_arm_l_18Geometry.scale(0.08960000000000001, 0.32186000000000003, 0.08960000000000001);
  }
  const mesh_upper_arm_l_18 = new THREE.SkinnedMesh(
    mesh_upper_arm_l_18Geometry,
    materialMap["shirt"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_upper_arm_l_18.name = "Upper arm L";
  if (endpoint_upper_arm_l_18) {
    mesh_upper_arm_l_18.position.copy(endpoint_upper_arm_l_18.midpoint);
    mesh_upper_arm_l_18.quaternion.copy(endpoint_upper_arm_l_18.quaternion);
  }
  mesh_upper_arm_l_18.castShadow = options.castShadow ?? true;
  mesh_upper_arm_l_18.receiveShadow = options.receiveShadow ?? true;
  mesh_upper_arm_l_18.userData.sculptComponent = {"id": "upper-arm-l", "name": "Upper arm L", "level": "meso", "role": "arm", "importance": 0.7, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Upper arm L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "clavicle-l", "attachment": {"parentSocket": "clavicle-shoulder-l", "localStart": [0.27048, -0.0056, 0.0056], "localEnd": [0.33442, -0.32104, 0.0056], "contactType": "socket-joint", "baseRadius": 0.0448, "endRadius": 0.0364, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.08960000000000001, "height": 0.32186000000000003, "depth": 0.08960000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.27048, -0.005599999999999994, 0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.08960000000000001, 0.32186000000000003, 0.08960000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "upper-arm-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shirt"}}, "material": "shirt", "materialLayers": ["shirt"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_upper_arm_l_18.add(mesh_upper_arm_l_18);
  meshes["upper-arm-l"] = mesh_upper_arm_l_18;
  colliders["upper-arm-l"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["upper-arm-l"] ??= [];
  destructionGroups["upper-arm-l"].push(node_upper_arm_l_18);

  const attachment_forearm_l_19 = {"parentSocket": "upper-arm-elbow-l", "localStart": [0.06394, -0.31544, 0.0], "localEnd": [0.09547, -0.57689, 0.0], "contactType": "hinge-joint", "baseRadius": 0.0336, "endRadius": 0.0252, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_forearm_l_19 = makeAttachmentEndpoint(attachment_forearm_l_19);
  const node_forearm_l_19 = new THREE.Group();
  node_forearm_l_19.name = "Forearm L__pivot";
  node_forearm_l_19.scale.set(1, 1, 1);
  if (endpoint_forearm_l_19) {
    node_forearm_l_19.position.copy(endpoint_forearm_l_19.start);
    node_forearm_l_19.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_forearm_l_19.position.set(0.0639437108096984, -0.3154442287439821, 0.0);
    node_forearm_l_19.rotation.set(0.0, 0.0, 0.0);
  }
  node_forearm_l_19.userData.sculptComponent = {"id": "forearm-l", "name": "Forearm L", "level": "meso", "role": "arm", "importance": 0.65, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Forearm L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "upper-arm-l", "attachment": {"parentSocket": "upper-arm-elbow-l", "localStart": [0.06394, -0.31544, 0.0], "localEnd": [0.09547, -0.57689, 0.0], "contactType": "hinge-joint", "baseRadius": 0.0336, "endRadius": 0.0252, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.0728, "height": 0.26334, "depth": 0.0728, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0639437108096984, -0.3154442287439821, 0.0], "rotation": [0, 0, 0], "scale": [0.0728, 0.26334, 0.0728]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "forearm-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_forearm_l_19.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "forearm-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["upper-arm-l"] ?? root).add(node_forearm_l_19);
  nodes["forearm-l"] = node_forearm_l_19;
  const mesh_forearm_l_19Geometry = endpoint_forearm_l_19
    ? new THREE.CylinderGeometry(endpoint_forearm_l_19.endRadius, endpoint_forearm_l_19.baseRadius, endpoint_forearm_l_19.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_forearm_l_19) {
    mesh_forearm_l_19Geometry.scale(0.0728, 0.26334, 0.0728);
  }
  const mesh_forearm_l_19 = new THREE.SkinnedMesh(
    mesh_forearm_l_19Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_forearm_l_19.name = "Forearm L";
  if (endpoint_forearm_l_19) {
    mesh_forearm_l_19.position.copy(endpoint_forearm_l_19.midpoint);
    mesh_forearm_l_19.quaternion.copy(endpoint_forearm_l_19.quaternion);
  }
  mesh_forearm_l_19.castShadow = options.castShadow ?? true;
  mesh_forearm_l_19.receiveShadow = options.receiveShadow ?? true;
  mesh_forearm_l_19.userData.sculptComponent = {"id": "forearm-l", "name": "Forearm L", "level": "meso", "role": "arm", "importance": 0.65, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Forearm L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "upper-arm-l", "attachment": {"parentSocket": "upper-arm-elbow-l", "localStart": [0.06394, -0.31544, 0.0], "localEnd": [0.09547, -0.57689, 0.0], "contactType": "hinge-joint", "baseRadius": 0.0336, "endRadius": 0.0252, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.0728, "height": 0.26334, "depth": 0.0728, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0639437108096984, -0.3154442287439821, 0.0], "rotation": [0, 0, 0], "scale": [0.0728, 0.26334, 0.0728]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "forearm-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_forearm_l_19.add(mesh_forearm_l_19);
  meshes["forearm-l"] = mesh_forearm_l_19;
  colliders["forearm-l"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["forearm-l"] ??= [];
  destructionGroups["forearm-l"].push(node_forearm_l_19);

  const endpoint_hand_l_20 = makeAttachmentEndpoint(null);
  const node_hand_l_20 = new THREE.Group();
  node_hand_l_20.name = "Hand L__pivot";
  node_hand_l_20.scale.set(1, 1, 1);
  if (endpoint_hand_l_20) {
    node_hand_l_20.position.copy(endpoint_hand_l_20.start);
    node_hand_l_20.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_hand_l_20.position.set(0.036888119554007615, -0.3059240530520103, 0.0);
    node_hand_l_20.rotation.set(0.0, 0.0, 0.0);
  }
  node_hand_l_20.userData.sculptComponent = {"id": "hand-l", "name": "Hand L", "level": "meso", "role": "hand", "importance": 0.55, "confidence": 0.8, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Hand L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "forearm-l", "attachment": null, "dimensions": {"width": 0.06160000000000001, "height": 0.08960000000000001, "depth": 0.0364, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.036888119554007615, -0.3059240530520103, 0.0], "rotation": [0, 0, 0], "scale": [0.06160000000000001, 0.08960000000000001, 0.0364]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "hand-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_hand_l_20.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "hand-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["forearm-l"] ?? root).add(node_hand_l_20);
  nodes["hand-l"] = node_hand_l_20;
  const mesh_hand_l_20Geometry = endpoint_hand_l_20
    ? new THREE.CylinderGeometry(endpoint_hand_l_20.endRadius, endpoint_hand_l_20.baseRadius, endpoint_hand_l_20.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_hand_l_20) {
    mesh_hand_l_20Geometry.scale(0.06160000000000001, 0.08960000000000001, 0.0364);
  }
  const mesh_hand_l_20 = new THREE.SkinnedMesh(
    mesh_hand_l_20Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_hand_l_20.name = "Hand L";
  if (endpoint_hand_l_20) {
    mesh_hand_l_20.position.copy(endpoint_hand_l_20.midpoint);
    mesh_hand_l_20.quaternion.copy(endpoint_hand_l_20.quaternion);
  }
  mesh_hand_l_20.castShadow = options.castShadow ?? true;
  mesh_hand_l_20.receiveShadow = options.receiveShadow ?? true;
  mesh_hand_l_20.userData.sculptComponent = {"id": "hand-l", "name": "Hand L", "level": "meso", "role": "hand", "importance": 0.55, "confidence": 0.8, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Hand L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "forearm-l", "attachment": null, "dimensions": {"width": 0.06160000000000001, "height": 0.08960000000000001, "depth": 0.0364, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.036888119554007615, -0.3059240530520103, 0.0], "rotation": [0, 0, 0], "scale": [0.06160000000000001, 0.08960000000000001, 0.0364]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "hand-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_hand_l_20.add(mesh_hand_l_20);
  meshes["hand-l"] = mesh_hand_l_20;
  colliders["hand-l"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["hand-l"] ??= [];
  destructionGroups["hand-l"].push(node_hand_l_20);

  const attachment_thumb_l_1_21 = {"parentSocket": "hand-l-thumb-1", "localStart": [-0.028, -0.00538, 0.0056], "localEnd": [-0.04312, -0.0184, 0.0119], "contactType": "rigid-weld", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_thumb_l_1_21 = makeAttachmentEndpoint(attachment_thumb_l_1_21);
  const node_thumb_l_1_21 = new THREE.Group();
  node_thumb_l_1_21.name = "Thumb L phalanx 1__pivot";
  node_thumb_l_1_21.scale.set(1, 1, 1);
  if (endpoint_thumb_l_1_21) {
    node_thumb_l_1_21.position.copy(endpoint_thumb_l_1_21.start);
    node_thumb_l_1_21.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_thumb_l_1_21.position.set(-0.028000000000000025, -0.005375999999999992, 0.005600000000000001);
    node_thumb_l_1_21.rotation.set(0.0, 0.0, 0.0);
  }
  node_thumb_l_1_21.userData.sculptComponent = {"id": "thumb-l-1", "name": "Thumb L phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Thumb L phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-l", "attachment": {"parentSocket": "hand-l-thumb-1", "localStart": [-0.028, -0.00538, 0.0056], "localEnd": [-0.04312, -0.0184, 0.0119], "contactType": "rigid-weld", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.017920000000000002, "height": 0.021, "depth": 0.017920000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.028000000000000025, -0.005375999999999992, 0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.017920000000000002, 0.021, 0.017920000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-l-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_thumb_l_1_21.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-l-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["hand-l"] ?? root).add(node_thumb_l_1_21);
  nodes["thumb-l-1"] = node_thumb_l_1_21;
  const mesh_thumb_l_1_21Geometry = endpoint_thumb_l_1_21
    ? new THREE.CylinderGeometry(endpoint_thumb_l_1_21.endRadius, endpoint_thumb_l_1_21.baseRadius, endpoint_thumb_l_1_21.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_thumb_l_1_21) {
    mesh_thumb_l_1_21Geometry.scale(0.017920000000000002, 0.021, 0.017920000000000002);
  }
  const mesh_thumb_l_1_21 = new THREE.SkinnedMesh(
    mesh_thumb_l_1_21Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_thumb_l_1_21.name = "Thumb L phalanx 1";
  if (endpoint_thumb_l_1_21) {
    mesh_thumb_l_1_21.position.copy(endpoint_thumb_l_1_21.midpoint);
    mesh_thumb_l_1_21.quaternion.copy(endpoint_thumb_l_1_21.quaternion);
  }
  mesh_thumb_l_1_21.castShadow = options.castShadow ?? true;
  mesh_thumb_l_1_21.receiveShadow = options.receiveShadow ?? true;
  mesh_thumb_l_1_21.userData.sculptComponent = {"id": "thumb-l-1", "name": "Thumb L phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Thumb L phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-l", "attachment": {"parentSocket": "hand-l-thumb-1", "localStart": [-0.028, -0.00538, 0.0056], "localEnd": [-0.04312, -0.0184, 0.0119], "contactType": "rigid-weld", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.017920000000000002, "height": 0.021, "depth": 0.017920000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.028000000000000025, -0.005375999999999992, 0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.017920000000000002, 0.021, 0.017920000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-l-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_thumb_l_1_21.add(mesh_thumb_l_1_21);
  meshes["thumb-l-1"] = mesh_thumb_l_1_21;
  colliders["thumb-l-1"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["thumb-l-1"] ??= [];
  destructionGroups["thumb-l-1"].push(node_thumb_l_1_21);

  const attachment_thumb_l_2_22 = {"parentSocket": "thumb-l-1-thumb-2", "localStart": [-0.01512, -0.01302, 0.0063], "localEnd": [-0.02621, -0.02257, 0.01092], "contactType": "hinge-joint", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_thumb_l_2_22 = makeAttachmentEndpoint(attachment_thumb_l_2_22);
  const node_thumb_l_2_22 = new THREE.Group();
  node_thumb_l_2_22.name = "Thumb L phalanx 2__pivot";
  node_thumb_l_2_22.scale.set(1, 1, 1);
  if (endpoint_thumb_l_2_22) {
    node_thumb_l_2_22.position.copy(endpoint_thumb_l_2_22.start);
    node_thumb_l_2_22.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_thumb_l_2_22.position.set(-0.015120000000000022, -0.013020000000000004, 0.0063);
    node_thumb_l_2_22.rotation.set(0.0, 0.0, 0.0);
  }
  node_thumb_l_2_22.userData.sculptComponent = {"id": "thumb-l-2", "name": "Thumb L phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Thumb L phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "thumb-l-1", "attachment": {"parentSocket": "thumb-l-1-thumb-2", "localStart": [-0.01512, -0.01302, 0.0063], "localEnd": [-0.02621, -0.02257, 0.01092], "contactType": "hinge-joint", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.017920000000000002, "height": 0.015400000000000002, "depth": 0.017920000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.015120000000000022, -0.013020000000000004, 0.0063], "rotation": [0, 0, 0], "scale": [0.017920000000000002, 0.015400000000000002, 0.017920000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-l-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_thumb_l_2_22.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-l-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["thumb-l-1"] ?? root).add(node_thumb_l_2_22);
  nodes["thumb-l-2"] = node_thumb_l_2_22;
  const mesh_thumb_l_2_22Geometry = endpoint_thumb_l_2_22
    ? new THREE.CylinderGeometry(endpoint_thumb_l_2_22.endRadius, endpoint_thumb_l_2_22.baseRadius, endpoint_thumb_l_2_22.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_thumb_l_2_22) {
    mesh_thumb_l_2_22Geometry.scale(0.017920000000000002, 0.015400000000000002, 0.017920000000000002);
  }
  const mesh_thumb_l_2_22 = new THREE.SkinnedMesh(
    mesh_thumb_l_2_22Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_thumb_l_2_22.name = "Thumb L phalanx 2";
  if (endpoint_thumb_l_2_22) {
    mesh_thumb_l_2_22.position.copy(endpoint_thumb_l_2_22.midpoint);
    mesh_thumb_l_2_22.quaternion.copy(endpoint_thumb_l_2_22.quaternion);
  }
  mesh_thumb_l_2_22.castShadow = options.castShadow ?? true;
  mesh_thumb_l_2_22.receiveShadow = options.receiveShadow ?? true;
  mesh_thumb_l_2_22.userData.sculptComponent = {"id": "thumb-l-2", "name": "Thumb L phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Thumb L phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "thumb-l-1", "attachment": {"parentSocket": "thumb-l-1-thumb-2", "localStart": [-0.01512, -0.01302, 0.0063], "localEnd": [-0.02621, -0.02257, 0.01092], "contactType": "hinge-joint", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.017920000000000002, "height": 0.015400000000000002, "depth": 0.017920000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.015120000000000022, -0.013020000000000004, 0.0063], "rotation": [0, 0, 0], "scale": [0.017920000000000002, 0.015400000000000002, 0.017920000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-l-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_thumb_l_2_22.add(mesh_thumb_l_2_22);
  meshes["thumb-l-2"] = mesh_thumb_l_2_22;
  colliders["thumb-l-2"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["thumb-l-2"] ??= [];
  destructionGroups["thumb-l-2"].push(node_thumb_l_2_22);

  const attachment_thumb_l_3_23 = {"parentSocket": "thumb-l-2-thumb-3", "localStart": [-0.01109, -0.00955, 0.00462], "localEnd": [-0.01915, -0.01649, 0.00798], "contactType": "hinge-joint", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_thumb_l_3_23 = makeAttachmentEndpoint(attachment_thumb_l_3_23);
  const node_thumb_l_3_23 = new THREE.Group();
  node_thumb_l_3_23.name = "Thumb L phalanx 3__pivot";
  node_thumb_l_3_23.scale.set(1, 1, 1);
  if (endpoint_thumb_l_3_23) {
    node_thumb_l_3_23.position.copy(endpoint_thumb_l_3_23.start);
    node_thumb_l_3_23.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_thumb_l_3_23.position.set(-0.011087999999999987, -0.009548000000000001, 0.004620000000000003);
    node_thumb_l_3_23.rotation.set(0.0, 0.0, 0.0);
  }
  node_thumb_l_3_23.userData.sculptComponent = {"id": "thumb-l-3", "name": "Thumb L phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Thumb L phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "thumb-l-2", "attachment": {"parentSocket": "thumb-l-2-thumb-3", "localStart": [-0.01109, -0.00955, 0.00462], "localEnd": [-0.01915, -0.01649, 0.00798], "contactType": "hinge-joint", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.017920000000000002, "height": 0.011200000000000002, "depth": 0.017920000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.011087999999999987, -0.009548000000000001, 0.004620000000000003], "rotation": [0, 0, 0], "scale": [0.017920000000000002, 0.011200000000000002, 0.017920000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-l-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_thumb_l_3_23.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-l-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["thumb-l-2"] ?? root).add(node_thumb_l_3_23);
  nodes["thumb-l-3"] = node_thumb_l_3_23;
  const mesh_thumb_l_3_23Geometry = endpoint_thumb_l_3_23
    ? new THREE.CylinderGeometry(endpoint_thumb_l_3_23.endRadius, endpoint_thumb_l_3_23.baseRadius, endpoint_thumb_l_3_23.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_thumb_l_3_23) {
    mesh_thumb_l_3_23Geometry.scale(0.017920000000000002, 0.011200000000000002, 0.017920000000000002);
  }
  const mesh_thumb_l_3_23 = new THREE.SkinnedMesh(
    mesh_thumb_l_3_23Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_thumb_l_3_23.name = "Thumb L phalanx 3";
  if (endpoint_thumb_l_3_23) {
    mesh_thumb_l_3_23.position.copy(endpoint_thumb_l_3_23.midpoint);
    mesh_thumb_l_3_23.quaternion.copy(endpoint_thumb_l_3_23.quaternion);
  }
  mesh_thumb_l_3_23.castShadow = options.castShadow ?? true;
  mesh_thumb_l_3_23.receiveShadow = options.receiveShadow ?? true;
  mesh_thumb_l_3_23.userData.sculptComponent = {"id": "thumb-l-3", "name": "Thumb L phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Thumb L phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "thumb-l-2", "attachment": {"parentSocket": "thumb-l-2-thumb-3", "localStart": [-0.01109, -0.00955, 0.00462], "localEnd": [-0.01915, -0.01649, 0.00798], "contactType": "hinge-joint", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.017920000000000002, "height": 0.011200000000000002, "depth": 0.017920000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.011087999999999987, -0.009548000000000001, 0.004620000000000003], "rotation": [0, 0, 0], "scale": [0.017920000000000002, 0.011200000000000002, 0.017920000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-l-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_thumb_l_3_23.add(mesh_thumb_l_3_23);
  meshes["thumb-l-3"] = mesh_thumb_l_3_23;
  colliders["thumb-l-3"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["thumb-l-3"] ??= [];
  destructionGroups["thumb-l-3"].push(node_thumb_l_3_23);

  const attachment_index_l_1_24 = {"parentSocket": "hand-l-index-1", "localStart": [-0.021, -0.03763, 0.0028], "localEnd": [-0.01748, -0.06682, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_index_l_1_24 = makeAttachmentEndpoint(attachment_index_l_1_24);
  const node_index_l_1_24 = new THREE.Group();
  node_index_l_1_24.name = "Index L phalanx 1__pivot";
  node_index_l_1_24.scale.set(1, 1, 1);
  if (endpoint_index_l_1_24) {
    node_index_l_1_24.position.copy(endpoint_index_l_1_24.start);
    node_index_l_1_24.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_index_l_1_24.position.set(-0.02100000000000002, -0.037632, 0.0028000000000000004);
    node_index_l_1_24.rotation.set(0.0, 0.0, 0.0);
  }
  node_index_l_1_24.userData.sculptComponent = {"id": "index-l-1", "name": "Index L phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Index L phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-l", "attachment": {"parentSocket": "hand-l-index-1", "localStart": [-0.021, -0.03763, 0.0028], "localEnd": [-0.01748, -0.06682, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015680000000000003, "height": 0.029400000000000003, "depth": 0.015680000000000003, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.02100000000000002, -0.037632, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.015680000000000003, 0.029400000000000003, 0.015680000000000003]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-l-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_index_l_1_24.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-l-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["hand-l"] ?? root).add(node_index_l_1_24);
  nodes["index-l-1"] = node_index_l_1_24;
  const mesh_index_l_1_24Geometry = endpoint_index_l_1_24
    ? new THREE.CylinderGeometry(endpoint_index_l_1_24.endRadius, endpoint_index_l_1_24.baseRadius, endpoint_index_l_1_24.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_index_l_1_24) {
    mesh_index_l_1_24Geometry.scale(0.015680000000000003, 0.029400000000000003, 0.015680000000000003);
  }
  const mesh_index_l_1_24 = new THREE.SkinnedMesh(
    mesh_index_l_1_24Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_index_l_1_24.name = "Index L phalanx 1";
  if (endpoint_index_l_1_24) {
    mesh_index_l_1_24.position.copy(endpoint_index_l_1_24.midpoint);
    mesh_index_l_1_24.quaternion.copy(endpoint_index_l_1_24.quaternion);
  }
  mesh_index_l_1_24.castShadow = options.castShadow ?? true;
  mesh_index_l_1_24.receiveShadow = options.receiveShadow ?? true;
  mesh_index_l_1_24.userData.sculptComponent = {"id": "index-l-1", "name": "Index L phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Index L phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-l", "attachment": {"parentSocket": "hand-l-index-1", "localStart": [-0.021, -0.03763, 0.0028], "localEnd": [-0.01748, -0.06682, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015680000000000003, "height": 0.029400000000000003, "depth": 0.015680000000000003, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.02100000000000002, -0.037632, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.015680000000000003, 0.029400000000000003, 0.015680000000000003]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-l-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_index_l_1_24.add(mesh_index_l_1_24);
  meshes["index-l-1"] = mesh_index_l_1_24;
  colliders["index-l-1"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["index-l-1"] ??= [];
  destructionGroups["index-l-1"].push(node_index_l_1_24);

  const attachment_index_l_2_25 = {"parentSocket": "index-l-1-index-2", "localStart": [0.00352, -0.02919, 0.0], "localEnd": [0.00593, -0.0492, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_index_l_2_25 = makeAttachmentEndpoint(attachment_index_l_2_25);
  const node_index_l_2_25 = new THREE.Group();
  node_index_l_2_25.name = "Index L phalanx 2__pivot";
  node_index_l_2_25.scale.set(1, 1, 1);
  if (endpoint_index_l_2_25) {
    node_index_l_2_25.position.copy(endpoint_index_l_2_25.start);
    node_index_l_2_25.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_index_l_2_25.position.set(0.0035195388942942385, -0.029188573894103675, 0.0);
    node_index_l_2_25.rotation.set(0.0, 0.0, 0.0);
  }
  node_index_l_2_25.userData.sculptComponent = {"id": "index-l-2", "name": "Index L phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Index L phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "index-l-1", "attachment": {"parentSocket": "index-l-1-index-2", "localStart": [0.00352, -0.02919, 0.0], "localEnd": [0.00593, -0.0492, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015680000000000003, "height": 0.02016, "depth": 0.015680000000000003, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0035195388942942385, -0.029188573894103675, 0.0], "rotation": [0, 0, 0], "scale": [0.015680000000000003, 0.02016, 0.015680000000000003]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-l-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_index_l_2_25.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-l-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["index-l-1"] ?? root).add(node_index_l_2_25);
  nodes["index-l-2"] = node_index_l_2_25;
  const mesh_index_l_2_25Geometry = endpoint_index_l_2_25
    ? new THREE.CylinderGeometry(endpoint_index_l_2_25.endRadius, endpoint_index_l_2_25.baseRadius, endpoint_index_l_2_25.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_index_l_2_25) {
    mesh_index_l_2_25Geometry.scale(0.015680000000000003, 0.02016, 0.015680000000000003);
  }
  const mesh_index_l_2_25 = new THREE.SkinnedMesh(
    mesh_index_l_2_25Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_index_l_2_25.name = "Index L phalanx 2";
  if (endpoint_index_l_2_25) {
    mesh_index_l_2_25.position.copy(endpoint_index_l_2_25.midpoint);
    mesh_index_l_2_25.quaternion.copy(endpoint_index_l_2_25.quaternion);
  }
  mesh_index_l_2_25.castShadow = options.castShadow ?? true;
  mesh_index_l_2_25.receiveShadow = options.receiveShadow ?? true;
  mesh_index_l_2_25.userData.sculptComponent = {"id": "index-l-2", "name": "Index L phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Index L phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "index-l-1", "attachment": {"parentSocket": "index-l-1-index-2", "localStart": [0.00352, -0.02919, 0.0], "localEnd": [0.00593, -0.0492, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015680000000000003, "height": 0.02016, "depth": 0.015680000000000003, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0035195388942942385, -0.029188573894103675, 0.0], "rotation": [0, 0, 0], "scale": [0.015680000000000003, 0.02016, 0.015680000000000003]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-l-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_index_l_2_25.add(mesh_index_l_2_25);
  meshes["index-l-2"] = mesh_index_l_2_25;
  colliders["index-l-2"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["index-l-2"] ??= [];
  destructionGroups["index-l-2"].push(node_index_l_2_25);

  const attachment_index_l_3_26 = {"parentSocket": "index-l-2-index-3", "localStart": [0.00241, -0.02002, 0.0], "localEnd": [0.00402, -0.03336, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_index_l_3_26 = makeAttachmentEndpoint(attachment_index_l_3_26);
  const node_index_l_3_26 = new THREE.Group();
  node_index_l_3_26.name = "Index L phalanx 3__pivot";
  node_index_l_3_26.scale.set(1, 1, 1);
  if (endpoint_index_l_3_26) {
    node_index_l_3_26.position.copy(endpoint_index_l_3_26.start);
    node_index_l_3_26.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_index_l_3_26.position.set(0.0024133980989446413, -0.020015022098813923, 0.0);
    node_index_l_3_26.rotation.set(0.0, 0.0, 0.0);
  }
  node_index_l_3_26.userData.sculptComponent = {"id": "index-l-3", "name": "Index L phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Index L phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "index-l-2", "attachment": {"parentSocket": "index-l-2-index-3", "localStart": [0.00241, -0.02002, 0.0], "localEnd": [0.00402, -0.03336, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015680000000000003, "height": 0.013440000000000002, "depth": 0.015680000000000003, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0024133980989446413, -0.020015022098813923, 0.0], "rotation": [0, 0, 0], "scale": [0.015680000000000003, 0.013440000000000002, 0.015680000000000003]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-l-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_index_l_3_26.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-l-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["index-l-2"] ?? root).add(node_index_l_3_26);
  nodes["index-l-3"] = node_index_l_3_26;
  const mesh_index_l_3_26Geometry = endpoint_index_l_3_26
    ? new THREE.CylinderGeometry(endpoint_index_l_3_26.endRadius, endpoint_index_l_3_26.baseRadius, endpoint_index_l_3_26.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_index_l_3_26) {
    mesh_index_l_3_26Geometry.scale(0.015680000000000003, 0.013440000000000002, 0.015680000000000003);
  }
  const mesh_index_l_3_26 = new THREE.SkinnedMesh(
    mesh_index_l_3_26Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_index_l_3_26.name = "Index L phalanx 3";
  if (endpoint_index_l_3_26) {
    mesh_index_l_3_26.position.copy(endpoint_index_l_3_26.midpoint);
    mesh_index_l_3_26.quaternion.copy(endpoint_index_l_3_26.quaternion);
  }
  mesh_index_l_3_26.castShadow = options.castShadow ?? true;
  mesh_index_l_3_26.receiveShadow = options.receiveShadow ?? true;
  mesh_index_l_3_26.userData.sculptComponent = {"id": "index-l-3", "name": "Index L phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Index L phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "index-l-2", "attachment": {"parentSocket": "index-l-2-index-3", "localStart": [0.00241, -0.02002, 0.0], "localEnd": [0.00402, -0.03336, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015680000000000003, "height": 0.013440000000000002, "depth": 0.015680000000000003, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0024133980989446413, -0.020015022098813923, 0.0], "rotation": [0, 0, 0], "scale": [0.015680000000000003, 0.013440000000000002, 0.015680000000000003]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-l-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_index_l_3_26.add(mesh_index_l_3_26);
  meshes["index-l-3"] = mesh_index_l_3_26;
  colliders["index-l-3"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["index-l-3"] ??= [];
  destructionGroups["index-l-3"].push(node_index_l_3_26);

  const attachment_middle_l_1_27 = {"parentSocket": "hand-l-middle-1", "localStart": [-0.007, -0.03763, 0.0028], "localEnd": [-0.00315, -0.0696, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_middle_l_1_27 = makeAttachmentEndpoint(attachment_middle_l_1_27);
  const node_middle_l_1_27 = new THREE.Group();
  node_middle_l_1_27.name = "Middle L phalanx 1__pivot";
  node_middle_l_1_27.scale.set(1, 1, 1);
  if (endpoint_middle_l_1_27) {
    node_middle_l_1_27.position.copy(endpoint_middle_l_1_27.start);
    node_middle_l_1_27.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_middle_l_1_27.position.set(-0.007000000000000006, -0.037632, 0.0028000000000000004);
    node_middle_l_1_27.rotation.set(0.0, 0.0, 0.0);
  }
  node_middle_l_1_27.userData.sculptComponent = {"id": "middle-l-1", "name": "Middle L phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Middle L phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-l", "attachment": {"parentSocket": "hand-l-middle-1", "localStart": [-0.007, -0.03763, 0.0028], "localEnd": [-0.00315, -0.0696, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.01624, "height": 0.032200000000000006, "depth": 0.01624, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.007000000000000006, -0.037632, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.01624, 0.032200000000000006, 0.01624]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-l-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_middle_l_1_27.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-l-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["hand-l"] ?? root).add(node_middle_l_1_27);
  nodes["middle-l-1"] = node_middle_l_1_27;
  const mesh_middle_l_1_27Geometry = endpoint_middle_l_1_27
    ? new THREE.CylinderGeometry(endpoint_middle_l_1_27.endRadius, endpoint_middle_l_1_27.baseRadius, endpoint_middle_l_1_27.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_middle_l_1_27) {
    mesh_middle_l_1_27Geometry.scale(0.01624, 0.032200000000000006, 0.01624);
  }
  const mesh_middle_l_1_27 = new THREE.SkinnedMesh(
    mesh_middle_l_1_27Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_middle_l_1_27.name = "Middle L phalanx 1";
  if (endpoint_middle_l_1_27) {
    mesh_middle_l_1_27.position.copy(endpoint_middle_l_1_27.midpoint);
    mesh_middle_l_1_27.quaternion.copy(endpoint_middle_l_1_27.quaternion);
  }
  mesh_middle_l_1_27.castShadow = options.castShadow ?? true;
  mesh_middle_l_1_27.receiveShadow = options.receiveShadow ?? true;
  mesh_middle_l_1_27.userData.sculptComponent = {"id": "middle-l-1", "name": "Middle L phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Middle L phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-l", "attachment": {"parentSocket": "hand-l-middle-1", "localStart": [-0.007, -0.03763, 0.0028], "localEnd": [-0.00315, -0.0696, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.01624, "height": 0.032200000000000006, "depth": 0.01624, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.007000000000000006, -0.037632, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.01624, 0.032200000000000006, 0.01624]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-l-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_middle_l_1_27.add(mesh_middle_l_1_27);
  meshes["middle-l-1"] = mesh_middle_l_1_27;
  colliders["middle-l-1"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["middle-l-1"] ??= [];
  destructionGroups["middle-l-1"].push(node_middle_l_1_27);

  const attachment_middle_l_2_28 = {"parentSocket": "middle-l-1-middle-2", "localStart": [0.00385, -0.03197, 0.0], "localEnd": [0.00654, -0.05421, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_middle_l_2_28 = makeAttachmentEndpoint(attachment_middle_l_2_28);
  const node_middle_l_2_28 = new THREE.Group();
  node_middle_l_2_28.name = "Middle L phalanx 2__pivot";
  node_middle_l_2_28.scale.set(1, 1, 1);
  if (endpoint_middle_l_2_28) {
    node_middle_l_2_28.position.copy(endpoint_middle_l_2_28.start);
    node_middle_l_2_28.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_middle_l_2_28.position.set(0.003854733074703187, -0.03196843807449448, 0.0);
    node_middle_l_2_28.rotation.set(0.0, 0.0, 0.0);
  }
  node_middle_l_2_28.userData.sculptComponent = {"id": "middle-l-2", "name": "Middle L phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Middle L phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "middle-l-1", "attachment": {"parentSocket": "middle-l-1-middle-2", "localStart": [0.00385, -0.03197, 0.0], "localEnd": [0.00654, -0.05421, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.01624, "height": 0.022400000000000003, "depth": 0.01624, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.003854733074703187, -0.03196843807449448, 0.0], "rotation": [0, 0, 0], "scale": [0.01624, 0.022400000000000003, 0.01624]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-l-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_middle_l_2_28.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-l-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["middle-l-1"] ?? root).add(node_middle_l_2_28);
  nodes["middle-l-2"] = node_middle_l_2_28;
  const mesh_middle_l_2_28Geometry = endpoint_middle_l_2_28
    ? new THREE.CylinderGeometry(endpoint_middle_l_2_28.endRadius, endpoint_middle_l_2_28.baseRadius, endpoint_middle_l_2_28.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_middle_l_2_28) {
    mesh_middle_l_2_28Geometry.scale(0.01624, 0.022400000000000003, 0.01624);
  }
  const mesh_middle_l_2_28 = new THREE.SkinnedMesh(
    mesh_middle_l_2_28Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_middle_l_2_28.name = "Middle L phalanx 2";
  if (endpoint_middle_l_2_28) {
    mesh_middle_l_2_28.position.copy(endpoint_middle_l_2_28.midpoint);
    mesh_middle_l_2_28.quaternion.copy(endpoint_middle_l_2_28.quaternion);
  }
  mesh_middle_l_2_28.castShadow = options.castShadow ?? true;
  mesh_middle_l_2_28.receiveShadow = options.receiveShadow ?? true;
  mesh_middle_l_2_28.userData.sculptComponent = {"id": "middle-l-2", "name": "Middle L phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Middle L phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "middle-l-1", "attachment": {"parentSocket": "middle-l-1-middle-2", "localStart": [0.00385, -0.03197, 0.0], "localEnd": [0.00654, -0.05421, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.01624, "height": 0.022400000000000003, "depth": 0.01624, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.003854733074703187, -0.03196843807449448, 0.0], "rotation": [0, 0, 0], "scale": [0.01624, 0.022400000000000003, 0.01624]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-l-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_middle_l_2_28.add(mesh_middle_l_2_28);
  meshes["middle-l-2"] = mesh_middle_l_2_28;
  colliders["middle-l-2"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["middle-l-2"] ??= [];
  destructionGroups["middle-l-2"].push(node_middle_l_2_28);

  const attachment_middle_l_3_29 = {"parentSocket": "middle-l-2-middle-3", "localStart": [0.00268, -0.02224, 0.0], "localEnd": [0.00436, -0.03614, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_middle_l_3_29 = makeAttachmentEndpoint(attachment_middle_l_3_29);
  const node_middle_l_3_29 = new THREE.Group();
  node_middle_l_3_29.name = "Middle L phalanx 3__pivot";
  node_middle_l_3_29.scale.set(1, 1, 1);
  if (endpoint_middle_l_3_29) {
    node_middle_l_3_29.position.copy(endpoint_middle_l_3_29.start);
    node_middle_l_3_29.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_middle_l_3_29.position.set(0.0026815534432718113, -0.022238913443126618, 0.0);
    node_middle_l_3_29.rotation.set(0.0, 0.0, 0.0);
  }
  node_middle_l_3_29.userData.sculptComponent = {"id": "middle-l-3", "name": "Middle L phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Middle L phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "middle-l-2", "attachment": {"parentSocket": "middle-l-2-middle-3", "localStart": [0.00268, -0.02224, 0.0], "localEnd": [0.00436, -0.03614, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.01624, "height": 0.014000000000000002, "depth": 0.01624, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0026815534432718113, -0.022238913443126618, 0.0], "rotation": [0, 0, 0], "scale": [0.01624, 0.014000000000000002, 0.01624]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-l-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_middle_l_3_29.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-l-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["middle-l-2"] ?? root).add(node_middle_l_3_29);
  nodes["middle-l-3"] = node_middle_l_3_29;
  const mesh_middle_l_3_29Geometry = endpoint_middle_l_3_29
    ? new THREE.CylinderGeometry(endpoint_middle_l_3_29.endRadius, endpoint_middle_l_3_29.baseRadius, endpoint_middle_l_3_29.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_middle_l_3_29) {
    mesh_middle_l_3_29Geometry.scale(0.01624, 0.014000000000000002, 0.01624);
  }
  const mesh_middle_l_3_29 = new THREE.SkinnedMesh(
    mesh_middle_l_3_29Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_middle_l_3_29.name = "Middle L phalanx 3";
  if (endpoint_middle_l_3_29) {
    mesh_middle_l_3_29.position.copy(endpoint_middle_l_3_29.midpoint);
    mesh_middle_l_3_29.quaternion.copy(endpoint_middle_l_3_29.quaternion);
  }
  mesh_middle_l_3_29.castShadow = options.castShadow ?? true;
  mesh_middle_l_3_29.receiveShadow = options.receiveShadow ?? true;
  mesh_middle_l_3_29.userData.sculptComponent = {"id": "middle-l-3", "name": "Middle L phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Middle L phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "middle-l-2", "attachment": {"parentSocket": "middle-l-2-middle-3", "localStart": [0.00268, -0.02224, 0.0], "localEnd": [0.00436, -0.03614, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.01624, "height": 0.014000000000000002, "depth": 0.01624, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0026815534432718113, -0.022238913443126618, 0.0], "rotation": [0, 0, 0], "scale": [0.01624, 0.014000000000000002, 0.01624]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-l-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_middle_l_3_29.add(mesh_middle_l_3_29);
  meshes["middle-l-3"] = mesh_middle_l_3_29;
  colliders["middle-l-3"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["middle-l-3"] ??= [];
  destructionGroups["middle-l-3"].push(node_middle_l_3_29);

  const attachment_ring_l_1_30 = {"parentSocket": "hand-l-ring-1", "localStart": [0.007, -0.03763, 0.0028], "localEnd": [0.01052, -0.06682, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_ring_l_1_30 = makeAttachmentEndpoint(attachment_ring_l_1_30);
  const node_ring_l_1_30 = new THREE.Group();
  node_ring_l_1_30.name = "Ring L phalanx 1__pivot";
  node_ring_l_1_30.scale.set(1, 1, 1);
  if (endpoint_ring_l_1_30) {
    node_ring_l_1_30.position.copy(endpoint_ring_l_1_30.start);
    node_ring_l_1_30.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_ring_l_1_30.position.set(0.007000000000000006, -0.037632, 0.0028000000000000004);
    node_ring_l_1_30.rotation.set(0.0, 0.0, 0.0);
  }
  node_ring_l_1_30.userData.sculptComponent = {"id": "ring-l-1", "name": "Ring L phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Ring L phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-l", "attachment": {"parentSocket": "hand-l-ring-1", "localStart": [0.007, -0.03763, 0.0028], "localEnd": [0.01052, -0.06682, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015120000000000001, "height": 0.029400000000000003, "depth": 0.015120000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.007000000000000006, -0.037632, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.015120000000000001, 0.029400000000000003, 0.015120000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-l-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_ring_l_1_30.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-l-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["hand-l"] ?? root).add(node_ring_l_1_30);
  nodes["ring-l-1"] = node_ring_l_1_30;
  const mesh_ring_l_1_30Geometry = endpoint_ring_l_1_30
    ? new THREE.CylinderGeometry(endpoint_ring_l_1_30.endRadius, endpoint_ring_l_1_30.baseRadius, endpoint_ring_l_1_30.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_ring_l_1_30) {
    mesh_ring_l_1_30Geometry.scale(0.015120000000000001, 0.029400000000000003, 0.015120000000000001);
  }
  const mesh_ring_l_1_30 = new THREE.SkinnedMesh(
    mesh_ring_l_1_30Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_ring_l_1_30.name = "Ring L phalanx 1";
  if (endpoint_ring_l_1_30) {
    mesh_ring_l_1_30.position.copy(endpoint_ring_l_1_30.midpoint);
    mesh_ring_l_1_30.quaternion.copy(endpoint_ring_l_1_30.quaternion);
  }
  mesh_ring_l_1_30.castShadow = options.castShadow ?? true;
  mesh_ring_l_1_30.receiveShadow = options.receiveShadow ?? true;
  mesh_ring_l_1_30.userData.sculptComponent = {"id": "ring-l-1", "name": "Ring L phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Ring L phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-l", "attachment": {"parentSocket": "hand-l-ring-1", "localStart": [0.007, -0.03763, 0.0028], "localEnd": [0.01052, -0.06682, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015120000000000001, "height": 0.029400000000000003, "depth": 0.015120000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.007000000000000006, -0.037632, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.015120000000000001, 0.029400000000000003, 0.015120000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-l-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_ring_l_1_30.add(mesh_ring_l_1_30);
  meshes["ring-l-1"] = mesh_ring_l_1_30;
  colliders["ring-l-1"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["ring-l-1"] ??= [];
  destructionGroups["ring-l-1"].push(node_ring_l_1_30);

  const attachment_ring_l_2_31 = {"parentSocket": "ring-l-1-ring-2", "localStart": [0.00352, -0.02919, 0.0], "localEnd": [0.00593, -0.0492, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_ring_l_2_31 = makeAttachmentEndpoint(attachment_ring_l_2_31);
  const node_ring_l_2_31 = new THREE.Group();
  node_ring_l_2_31.name = "Ring L phalanx 2__pivot";
  node_ring_l_2_31.scale.set(1, 1, 1);
  if (endpoint_ring_l_2_31) {
    node_ring_l_2_31.position.copy(endpoint_ring_l_2_31.start);
    node_ring_l_2_31.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_ring_l_2_31.position.set(0.0035195388942942385, -0.029188573894103675, 0.0);
    node_ring_l_2_31.rotation.set(0.0, 0.0, 0.0);
  }
  node_ring_l_2_31.userData.sculptComponent = {"id": "ring-l-2", "name": "Ring L phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Ring L phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "ring-l-1", "attachment": {"parentSocket": "ring-l-1-ring-2", "localStart": [0.00352, -0.02919, 0.0], "localEnd": [0.00593, -0.0492, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015120000000000001, "height": 0.02016, "depth": 0.015120000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0035195388942942385, -0.029188573894103675, 0.0], "rotation": [0, 0, 0], "scale": [0.015120000000000001, 0.02016, 0.015120000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-l-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_ring_l_2_31.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-l-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["ring-l-1"] ?? root).add(node_ring_l_2_31);
  nodes["ring-l-2"] = node_ring_l_2_31;
  const mesh_ring_l_2_31Geometry = endpoint_ring_l_2_31
    ? new THREE.CylinderGeometry(endpoint_ring_l_2_31.endRadius, endpoint_ring_l_2_31.baseRadius, endpoint_ring_l_2_31.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_ring_l_2_31) {
    mesh_ring_l_2_31Geometry.scale(0.015120000000000001, 0.02016, 0.015120000000000001);
  }
  const mesh_ring_l_2_31 = new THREE.SkinnedMesh(
    mesh_ring_l_2_31Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_ring_l_2_31.name = "Ring L phalanx 2";
  if (endpoint_ring_l_2_31) {
    mesh_ring_l_2_31.position.copy(endpoint_ring_l_2_31.midpoint);
    mesh_ring_l_2_31.quaternion.copy(endpoint_ring_l_2_31.quaternion);
  }
  mesh_ring_l_2_31.castShadow = options.castShadow ?? true;
  mesh_ring_l_2_31.receiveShadow = options.receiveShadow ?? true;
  mesh_ring_l_2_31.userData.sculptComponent = {"id": "ring-l-2", "name": "Ring L phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Ring L phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "ring-l-1", "attachment": {"parentSocket": "ring-l-1-ring-2", "localStart": [0.00352, -0.02919, 0.0], "localEnd": [0.00593, -0.0492, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015120000000000001, "height": 0.02016, "depth": 0.015120000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0035195388942942385, -0.029188573894103675, 0.0], "rotation": [0, 0, 0], "scale": [0.015120000000000001, 0.02016, 0.015120000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-l-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_ring_l_2_31.add(mesh_ring_l_2_31);
  meshes["ring-l-2"] = mesh_ring_l_2_31;
  colliders["ring-l-2"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["ring-l-2"] ??= [];
  destructionGroups["ring-l-2"].push(node_ring_l_2_31);

  const attachment_ring_l_3_32 = {"parentSocket": "ring-l-2-ring-3", "localStart": [0.00241, -0.02002, 0.0], "localEnd": [0.00396, -0.0328, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_ring_l_3_32 = makeAttachmentEndpoint(attachment_ring_l_3_32);
  const node_ring_l_3_32 = new THREE.Group();
  node_ring_l_3_32.name = "Ring L phalanx 3__pivot";
  node_ring_l_3_32.scale.set(1, 1, 1);
  if (endpoint_ring_l_3_32) {
    node_ring_l_3_32.position.copy(endpoint_ring_l_3_32.start);
    node_ring_l_3_32.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_ring_l_3_32.position.set(0.0024133980989446413, -0.020015022098813923, 0.0);
    node_ring_l_3_32.rotation.set(0.0, 0.0, 0.0);
  }
  node_ring_l_3_32.userData.sculptComponent = {"id": "ring-l-3", "name": "Ring L phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Ring L phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "ring-l-2", "attachment": {"parentSocket": "ring-l-2-ring-3", "localStart": [0.00241, -0.02002, 0.0], "localEnd": [0.00396, -0.0328, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015120000000000001, "height": 0.01288, "depth": 0.015120000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0024133980989446413, -0.020015022098813923, 0.0], "rotation": [0, 0, 0], "scale": [0.015120000000000001, 0.01288, 0.015120000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-l-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_ring_l_3_32.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-l-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["ring-l-2"] ?? root).add(node_ring_l_3_32);
  nodes["ring-l-3"] = node_ring_l_3_32;
  const mesh_ring_l_3_32Geometry = endpoint_ring_l_3_32
    ? new THREE.CylinderGeometry(endpoint_ring_l_3_32.endRadius, endpoint_ring_l_3_32.baseRadius, endpoint_ring_l_3_32.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_ring_l_3_32) {
    mesh_ring_l_3_32Geometry.scale(0.015120000000000001, 0.01288, 0.015120000000000001);
  }
  const mesh_ring_l_3_32 = new THREE.SkinnedMesh(
    mesh_ring_l_3_32Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_ring_l_3_32.name = "Ring L phalanx 3";
  if (endpoint_ring_l_3_32) {
    mesh_ring_l_3_32.position.copy(endpoint_ring_l_3_32.midpoint);
    mesh_ring_l_3_32.quaternion.copy(endpoint_ring_l_3_32.quaternion);
  }
  mesh_ring_l_3_32.castShadow = options.castShadow ?? true;
  mesh_ring_l_3_32.receiveShadow = options.receiveShadow ?? true;
  mesh_ring_l_3_32.userData.sculptComponent = {"id": "ring-l-3", "name": "Ring L phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Ring L phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "ring-l-2", "attachment": {"parentSocket": "ring-l-2-ring-3", "localStart": [0.00241, -0.02002, 0.0], "localEnd": [0.00396, -0.0328, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015120000000000001, "height": 0.01288, "depth": 0.015120000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0024133980989446413, -0.020015022098813923, 0.0], "rotation": [0, 0, 0], "scale": [0.015120000000000001, 0.01288, 0.015120000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-l-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_ring_l_3_32.add(mesh_ring_l_3_32);
  meshes["ring-l-3"] = mesh_ring_l_3_32;
  colliders["ring-l-3"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["ring-l-3"] ??= [];
  destructionGroups["ring-l-3"].push(node_ring_l_3_32);

  const attachment_little_l_1_33 = {"parentSocket": "hand-l-little-1", "localStart": [0.0196, -0.03763, 0.0028], "localEnd": [0.02228, -0.05987, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_little_l_1_33 = makeAttachmentEndpoint(attachment_little_l_1_33);
  const node_little_l_1_33 = new THREE.Group();
  node_little_l_1_33.name = "Little L phalanx 1__pivot";
  node_little_l_1_33.scale.set(1, 1, 1);
  if (endpoint_little_l_1_33) {
    node_little_l_1_33.position.copy(endpoint_little_l_1_33.start);
    node_little_l_1_33.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_little_l_1_33.position.set(0.019600000000000006, -0.037632, 0.0028000000000000004);
    node_little_l_1_33.rotation.set(0.0, 0.0, 0.0);
  }
  node_little_l_1_33.userData.sculptComponent = {"id": "little-l-1", "name": "Little L phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Little L phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-l", "attachment": {"parentSocket": "hand-l-little-1", "localStart": [0.0196, -0.03763, 0.0028], "localEnd": [0.02228, -0.05987, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.013440000000000002, "height": 0.022400000000000003, "depth": 0.013440000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.019600000000000006, -0.037632, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.013440000000000002, 0.022400000000000003, 0.013440000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-l-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_little_l_1_33.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-l-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["hand-l"] ?? root).add(node_little_l_1_33);
  nodes["little-l-1"] = node_little_l_1_33;
  const mesh_little_l_1_33Geometry = endpoint_little_l_1_33
    ? new THREE.CylinderGeometry(endpoint_little_l_1_33.endRadius, endpoint_little_l_1_33.baseRadius, endpoint_little_l_1_33.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_little_l_1_33) {
    mesh_little_l_1_33Geometry.scale(0.013440000000000002, 0.022400000000000003, 0.013440000000000002);
  }
  const mesh_little_l_1_33 = new THREE.SkinnedMesh(
    mesh_little_l_1_33Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_little_l_1_33.name = "Little L phalanx 1";
  if (endpoint_little_l_1_33) {
    mesh_little_l_1_33.position.copy(endpoint_little_l_1_33.midpoint);
    mesh_little_l_1_33.quaternion.copy(endpoint_little_l_1_33.quaternion);
  }
  mesh_little_l_1_33.castShadow = options.castShadow ?? true;
  mesh_little_l_1_33.receiveShadow = options.receiveShadow ?? true;
  mesh_little_l_1_33.userData.sculptComponent = {"id": "little-l-1", "name": "Little L phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Little L phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-l", "attachment": {"parentSocket": "hand-l-little-1", "localStart": [0.0196, -0.03763, 0.0028], "localEnd": [0.02228, -0.05987, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.013440000000000002, "height": 0.022400000000000003, "depth": 0.013440000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.019600000000000006, -0.037632, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.013440000000000002, 0.022400000000000003, 0.013440000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-l-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_little_l_1_33.add(mesh_little_l_1_33);
  meshes["little-l-1"] = mesh_little_l_1_33;
  colliders["little-l-1"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["little-l-1"] ??= [];
  destructionGroups["little-l-1"].push(node_little_l_1_33);

  const attachment_little_l_2_34 = {"parentSocket": "little-l-1-little-2", "localStart": [0.00268, -0.02224, 0.0], "localEnd": [0.00463, -0.03836, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_little_l_2_34 = makeAttachmentEndpoint(attachment_little_l_2_34);
  const node_little_l_2_34 = new THREE.Group();
  node_little_l_2_34.name = "Little L phalanx 2__pivot";
  node_little_l_2_34.scale.set(1, 1, 1);
  if (endpoint_little_l_2_34) {
    node_little_l_2_34.position.copy(endpoint_little_l_2_34.start);
    node_little_l_2_34.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_little_l_2_34.position.set(0.0026815534432718113, -0.022238913443126618, 0.0);
    node_little_l_2_34.rotation.set(0.0, 0.0, 0.0);
  }
  node_little_l_2_34.userData.sculptComponent = {"id": "little-l-2", "name": "Little L phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Little L phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "little-l-1", "attachment": {"parentSocket": "little-l-1-little-2", "localStart": [0.00268, -0.02224, 0.0], "localEnd": [0.00463, -0.03836, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.013440000000000002, "height": 0.01624, "depth": 0.013440000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0026815534432718113, -0.022238913443126618, 0.0], "rotation": [0, 0, 0], "scale": [0.013440000000000002, 0.01624, 0.013440000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-l-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_little_l_2_34.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-l-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["little-l-1"] ?? root).add(node_little_l_2_34);
  nodes["little-l-2"] = node_little_l_2_34;
  const mesh_little_l_2_34Geometry = endpoint_little_l_2_34
    ? new THREE.CylinderGeometry(endpoint_little_l_2_34.endRadius, endpoint_little_l_2_34.baseRadius, endpoint_little_l_2_34.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_little_l_2_34) {
    mesh_little_l_2_34Geometry.scale(0.013440000000000002, 0.01624, 0.013440000000000002);
  }
  const mesh_little_l_2_34 = new THREE.SkinnedMesh(
    mesh_little_l_2_34Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_little_l_2_34.name = "Little L phalanx 2";
  if (endpoint_little_l_2_34) {
    mesh_little_l_2_34.position.copy(endpoint_little_l_2_34.midpoint);
    mesh_little_l_2_34.quaternion.copy(endpoint_little_l_2_34.quaternion);
  }
  mesh_little_l_2_34.castShadow = options.castShadow ?? true;
  mesh_little_l_2_34.receiveShadow = options.receiveShadow ?? true;
  mesh_little_l_2_34.userData.sculptComponent = {"id": "little-l-2", "name": "Little L phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Little L phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "little-l-1", "attachment": {"parentSocket": "little-l-1-little-2", "localStart": [0.00268, -0.02224, 0.0], "localEnd": [0.00463, -0.03836, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.013440000000000002, "height": 0.01624, "depth": 0.013440000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0026815534432718113, -0.022238913443126618, 0.0], "rotation": [0, 0, 0], "scale": [0.013440000000000002, 0.01624, 0.013440000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-l-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_little_l_2_34.add(mesh_little_l_2_34);
  meshes["little-l-2"] = mesh_little_l_2_34;
  colliders["little-l-2"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["little-l-2"] ??= [];
  destructionGroups["little-l-2"].push(node_little_l_2_34);

  const attachment_little_l_3_35 = {"parentSocket": "little-l-2-little-3", "localStart": [0.00194, -0.01612, 0.0], "localEnd": [0.00322, -0.02669, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_little_l_3_35 = makeAttachmentEndpoint(attachment_little_l_3_35);
  const node_little_l_3_35 = new THREE.Group();
  node_little_l_3_35.name = "Little L phalanx 3__pivot";
  node_little_l_3_35.scale.set(1, 1, 1);
  if (endpoint_little_l_3_35) {
    node_little_l_3_35.position.copy(endpoint_little_l_3_35.start);
    node_little_l_3_35.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_little_l_3_35.position.set(0.0019441262463720244, -0.01612321224626681, 0.0);
    node_little_l_3_35.rotation.set(0.0, 0.0, 0.0);
  }
  node_little_l_3_35.userData.sculptComponent = {"id": "little-l-3", "name": "Little L phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Little L phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "little-l-2", "attachment": {"parentSocket": "little-l-2-little-3", "localStart": [0.00194, -0.01612, 0.0], "localEnd": [0.00322, -0.02669, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.013440000000000002, "height": 0.01064, "depth": 0.013440000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0019441262463720244, -0.01612321224626681, 0.0], "rotation": [0, 0, 0], "scale": [0.013440000000000002, 0.01064, 0.013440000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-l-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_little_l_3_35.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-l-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["little-l-2"] ?? root).add(node_little_l_3_35);
  nodes["little-l-3"] = node_little_l_3_35;
  const mesh_little_l_3_35Geometry = endpoint_little_l_3_35
    ? new THREE.CylinderGeometry(endpoint_little_l_3_35.endRadius, endpoint_little_l_3_35.baseRadius, endpoint_little_l_3_35.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_little_l_3_35) {
    mesh_little_l_3_35Geometry.scale(0.013440000000000002, 0.01064, 0.013440000000000002);
  }
  const mesh_little_l_3_35 = new THREE.SkinnedMesh(
    mesh_little_l_3_35Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_little_l_3_35.name = "Little L phalanx 3";
  if (endpoint_little_l_3_35) {
    mesh_little_l_3_35.position.copy(endpoint_little_l_3_35.midpoint);
    mesh_little_l_3_35.quaternion.copy(endpoint_little_l_3_35.quaternion);
  }
  mesh_little_l_3_35.castShadow = options.castShadow ?? true;
  mesh_little_l_3_35.receiveShadow = options.receiveShadow ?? true;
  mesh_little_l_3_35.userData.sculptComponent = {"id": "little-l-3", "name": "Little L phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Little L phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "little-l-2", "attachment": {"parentSocket": "little-l-2-little-3", "localStart": [0.00194, -0.01612, 0.0], "localEnd": [0.00322, -0.02669, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.013440000000000002, "height": 0.01064, "depth": 0.013440000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0019441262463720244, -0.01612321224626681, 0.0], "rotation": [0, 0, 0], "scale": [0.013440000000000002, 0.01064, 0.013440000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-l-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_little_l_3_35.add(mesh_little_l_3_35);
  meshes["little-l-3"] = mesh_little_l_3_35;
  colliders["little-l-3"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["little-l-3"] ??= [];
  destructionGroups["little-l-3"].push(node_little_l_3_35);

  const attachment_clavicle_r_36 = {"parentSocket": "chest-clavicle-r", "localStart": [-0.05152, 0.34048, 0.0056], "localEnd": [-0.322, 0.33488, 0.0112], "contactType": "rigid-weld", "baseRadius": 0.0308, "endRadius": 0.0476, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_clavicle_r_36 = makeAttachmentEndpoint(attachment_clavicle_r_36);
  const node_clavicle_r_36 = new THREE.Group();
  node_clavicle_r_36.name = "Clavicle R__pivot";
  node_clavicle_r_36.scale.set(1, 1, 1);
  if (endpoint_clavicle_r_36) {
    node_clavicle_r_36.position.copy(endpoint_clavicle_r_36.start);
    node_clavicle_r_36.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_clavicle_r_36.position.set(-0.05152, 0.34048000000000006, 0.005600000000000001);
    node_clavicle_r_36.rotation.set(0.0, 0.0, 0.0);
  }
  node_clavicle_r_36.userData.sculptComponent = {"id": "clavicle-r", "name": "Clavicle R", "level": "meso", "role": "support", "importance": 0.6, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Clavicle R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "chest", "attachment": {"parentSocket": "chest-clavicle-r", "localStart": [-0.05152, 0.34048, 0.0056], "localEnd": [-0.322, 0.33488, 0.0112], "contactType": "rigid-weld", "baseRadius": 0.0308, "endRadius": 0.0476, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.27048, "height": 0.09520000000000002, "depth": 0.09520000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.05152, 0.34048000000000006, 0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.27048, 0.09520000000000002, 0.09520000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "clavicle-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_clavicle_r_36.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "clavicle-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["chest"] ?? root).add(node_clavicle_r_36);
  nodes["clavicle-r"] = node_clavicle_r_36;
  const mesh_clavicle_r_36Geometry = endpoint_clavicle_r_36
    ? new THREE.CylinderGeometry(endpoint_clavicle_r_36.endRadius, endpoint_clavicle_r_36.baseRadius, endpoint_clavicle_r_36.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_clavicle_r_36) {
    mesh_clavicle_r_36Geometry.scale(0.27048, 0.09520000000000002, 0.09520000000000002);
  }
  const mesh_clavicle_r_36 = new THREE.SkinnedMesh(
    mesh_clavicle_r_36Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_clavicle_r_36.name = "Clavicle R";
  if (endpoint_clavicle_r_36) {
    mesh_clavicle_r_36.position.copy(endpoint_clavicle_r_36.midpoint);
    mesh_clavicle_r_36.quaternion.copy(endpoint_clavicle_r_36.quaternion);
  }
  mesh_clavicle_r_36.castShadow = options.castShadow ?? true;
  mesh_clavicle_r_36.receiveShadow = options.receiveShadow ?? true;
  mesh_clavicle_r_36.userData.sculptComponent = {"id": "clavicle-r", "name": "Clavicle R", "level": "meso", "role": "support", "importance": 0.6, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Clavicle R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "chest", "attachment": {"parentSocket": "chest-clavicle-r", "localStart": [-0.05152, 0.34048, 0.0056], "localEnd": [-0.322, 0.33488, 0.0112], "contactType": "rigid-weld", "baseRadius": 0.0308, "endRadius": 0.0476, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.27048, "height": 0.09520000000000002, "depth": 0.09520000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.05152, 0.34048000000000006, 0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.27048, 0.09520000000000002, 0.09520000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "clavicle-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_clavicle_r_36.add(mesh_clavicle_r_36);
  meshes["clavicle-r"] = mesh_clavicle_r_36;
  colliders["clavicle-r"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["clavicle-r"] ??= [];
  destructionGroups["clavicle-r"].push(node_clavicle_r_36);

  const attachment_upper_arm_r_37 = {"parentSocket": "clavicle-shoulder-r", "localStart": [-0.27048, -0.0056, 0.0056], "localEnd": [-0.33442, -0.32104, 0.0056], "contactType": "socket-joint", "baseRadius": 0.0448, "endRadius": 0.0364, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_upper_arm_r_37 = makeAttachmentEndpoint(attachment_upper_arm_r_37);
  const node_upper_arm_r_37 = new THREE.Group();
  node_upper_arm_r_37.name = "Upper arm R__pivot";
  node_upper_arm_r_37.scale.set(1, 1, 1);
  if (endpoint_upper_arm_r_37) {
    node_upper_arm_r_37.position.copy(endpoint_upper_arm_r_37.start);
    node_upper_arm_r_37.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_upper_arm_r_37.position.set(-0.27048, -0.005599999999999994, 0.005600000000000001);
    node_upper_arm_r_37.rotation.set(0.0, 0.0, 0.0);
  }
  node_upper_arm_r_37.userData.sculptComponent = {"id": "upper-arm-r", "name": "Upper arm R", "level": "meso", "role": "arm", "importance": 0.7, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Upper arm R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "clavicle-r", "attachment": {"parentSocket": "clavicle-shoulder-r", "localStart": [-0.27048, -0.0056, 0.0056], "localEnd": [-0.33442, -0.32104, 0.0056], "contactType": "socket-joint", "baseRadius": 0.0448, "endRadius": 0.0364, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.08960000000000001, "height": 0.32186000000000003, "depth": 0.08960000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.27048, -0.005599999999999994, 0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.08960000000000001, 0.32186000000000003, 0.08960000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "upper-arm-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shirt"}}, "material": "shirt", "materialLayers": ["shirt"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_upper_arm_r_37.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "upper-arm-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shirt"}};
  (nodes["clavicle-r"] ?? root).add(node_upper_arm_r_37);
  nodes["upper-arm-r"] = node_upper_arm_r_37;
  const mesh_upper_arm_r_37Geometry = endpoint_upper_arm_r_37
    ? new THREE.CylinderGeometry(endpoint_upper_arm_r_37.endRadius, endpoint_upper_arm_r_37.baseRadius, endpoint_upper_arm_r_37.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_upper_arm_r_37) {
    mesh_upper_arm_r_37Geometry.scale(0.08960000000000001, 0.32186000000000003, 0.08960000000000001);
  }
  const mesh_upper_arm_r_37 = new THREE.SkinnedMesh(
    mesh_upper_arm_r_37Geometry,
    materialMap["shirt"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_upper_arm_r_37.name = "Upper arm R";
  if (endpoint_upper_arm_r_37) {
    mesh_upper_arm_r_37.position.copy(endpoint_upper_arm_r_37.midpoint);
    mesh_upper_arm_r_37.quaternion.copy(endpoint_upper_arm_r_37.quaternion);
  }
  mesh_upper_arm_r_37.castShadow = options.castShadow ?? true;
  mesh_upper_arm_r_37.receiveShadow = options.receiveShadow ?? true;
  mesh_upper_arm_r_37.userData.sculptComponent = {"id": "upper-arm-r", "name": "Upper arm R", "level": "meso", "role": "arm", "importance": 0.7, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Upper arm R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "clavicle-r", "attachment": {"parentSocket": "clavicle-shoulder-r", "localStart": [-0.27048, -0.0056, 0.0056], "localEnd": [-0.33442, -0.32104, 0.0056], "contactType": "socket-joint", "baseRadius": 0.0448, "endRadius": 0.0364, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.08960000000000001, "height": 0.32186000000000003, "depth": 0.08960000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.27048, -0.005599999999999994, 0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.08960000000000001, 0.32186000000000003, 0.08960000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "upper-arm-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shirt"}}, "material": "shirt", "materialLayers": ["shirt"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_upper_arm_r_37.add(mesh_upper_arm_r_37);
  meshes["upper-arm-r"] = mesh_upper_arm_r_37;
  colliders["upper-arm-r"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["upper-arm-r"] ??= [];
  destructionGroups["upper-arm-r"].push(node_upper_arm_r_37);

  const attachment_forearm_r_38 = {"parentSocket": "upper-arm-elbow-r", "localStart": [-0.06394, -0.31544, 0.0], "localEnd": [-0.09547, -0.57689, 0.0], "contactType": "hinge-joint", "baseRadius": 0.0336, "endRadius": 0.0252, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_forearm_r_38 = makeAttachmentEndpoint(attachment_forearm_r_38);
  const node_forearm_r_38 = new THREE.Group();
  node_forearm_r_38.name = "Forearm R__pivot";
  node_forearm_r_38.scale.set(1, 1, 1);
  if (endpoint_forearm_r_38) {
    node_forearm_r_38.position.copy(endpoint_forearm_r_38.start);
    node_forearm_r_38.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_forearm_r_38.position.set(-0.0639437108096984, -0.3154442287439821, 0.0);
    node_forearm_r_38.rotation.set(0.0, 0.0, 0.0);
  }
  node_forearm_r_38.userData.sculptComponent = {"id": "forearm-r", "name": "Forearm R", "level": "meso", "role": "arm", "importance": 0.65, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Forearm R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "upper-arm-r", "attachment": {"parentSocket": "upper-arm-elbow-r", "localStart": [-0.06394, -0.31544, 0.0], "localEnd": [-0.09547, -0.57689, 0.0], "contactType": "hinge-joint", "baseRadius": 0.0336, "endRadius": 0.0252, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.0728, "height": 0.26334, "depth": 0.0728, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.0639437108096984, -0.3154442287439821, 0.0], "rotation": [0, 0, 0], "scale": [0.0728, 0.26334, 0.0728]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "forearm-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_forearm_r_38.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "forearm-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["upper-arm-r"] ?? root).add(node_forearm_r_38);
  nodes["forearm-r"] = node_forearm_r_38;
  const mesh_forearm_r_38Geometry = endpoint_forearm_r_38
    ? new THREE.CylinderGeometry(endpoint_forearm_r_38.endRadius, endpoint_forearm_r_38.baseRadius, endpoint_forearm_r_38.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_forearm_r_38) {
    mesh_forearm_r_38Geometry.scale(0.0728, 0.26334, 0.0728);
  }
  const mesh_forearm_r_38 = new THREE.SkinnedMesh(
    mesh_forearm_r_38Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_forearm_r_38.name = "Forearm R";
  if (endpoint_forearm_r_38) {
    mesh_forearm_r_38.position.copy(endpoint_forearm_r_38.midpoint);
    mesh_forearm_r_38.quaternion.copy(endpoint_forearm_r_38.quaternion);
  }
  mesh_forearm_r_38.castShadow = options.castShadow ?? true;
  mesh_forearm_r_38.receiveShadow = options.receiveShadow ?? true;
  mesh_forearm_r_38.userData.sculptComponent = {"id": "forearm-r", "name": "Forearm R", "level": "meso", "role": "arm", "importance": 0.65, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Forearm R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "upper-arm-r", "attachment": {"parentSocket": "upper-arm-elbow-r", "localStart": [-0.06394, -0.31544, 0.0], "localEnd": [-0.09547, -0.57689, 0.0], "contactType": "hinge-joint", "baseRadius": 0.0336, "endRadius": 0.0252, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.0728, "height": 0.26334, "depth": 0.0728, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.0639437108096984, -0.3154442287439821, 0.0], "rotation": [0, 0, 0], "scale": [0.0728, 0.26334, 0.0728]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "forearm-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_forearm_r_38.add(mesh_forearm_r_38);
  meshes["forearm-r"] = mesh_forearm_r_38;
  colliders["forearm-r"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["forearm-r"] ??= [];
  destructionGroups["forearm-r"].push(node_forearm_r_38);

  const endpoint_hand_r_39 = makeAttachmentEndpoint(null);
  const node_hand_r_39 = new THREE.Group();
  node_hand_r_39.name = "Hand R__pivot";
  node_hand_r_39.scale.set(1, 1, 1);
  if (endpoint_hand_r_39) {
    node_hand_r_39.position.copy(endpoint_hand_r_39.start);
    node_hand_r_39.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_hand_r_39.position.set(-0.036888119554007615, -0.3059240530520103, 0.0);
    node_hand_r_39.rotation.set(0.0, 0.0, 0.0);
  }
  node_hand_r_39.userData.sculptComponent = {"id": "hand-r", "name": "Hand R", "level": "meso", "role": "hand", "importance": 0.55, "confidence": 0.8, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Hand R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "forearm-r", "attachment": null, "dimensions": {"width": 0.06160000000000001, "height": 0.08960000000000001, "depth": 0.0364, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.036888119554007615, -0.3059240530520103, 0.0], "rotation": [0, 0, 0], "scale": [0.06160000000000001, 0.08960000000000001, 0.0364]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "hand-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_hand_r_39.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "hand-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["forearm-r"] ?? root).add(node_hand_r_39);
  nodes["hand-r"] = node_hand_r_39;
  const mesh_hand_r_39Geometry = endpoint_hand_r_39
    ? new THREE.CylinderGeometry(endpoint_hand_r_39.endRadius, endpoint_hand_r_39.baseRadius, endpoint_hand_r_39.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_hand_r_39) {
    mesh_hand_r_39Geometry.scale(0.06160000000000001, 0.08960000000000001, 0.0364);
  }
  const mesh_hand_r_39 = new THREE.SkinnedMesh(
    mesh_hand_r_39Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_hand_r_39.name = "Hand R";
  if (endpoint_hand_r_39) {
    mesh_hand_r_39.position.copy(endpoint_hand_r_39.midpoint);
    mesh_hand_r_39.quaternion.copy(endpoint_hand_r_39.quaternion);
  }
  mesh_hand_r_39.castShadow = options.castShadow ?? true;
  mesh_hand_r_39.receiveShadow = options.receiveShadow ?? true;
  mesh_hand_r_39.userData.sculptComponent = {"id": "hand-r", "name": "Hand R", "level": "meso", "role": "hand", "importance": 0.55, "confidence": 0.8, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Hand R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "forearm-r", "attachment": null, "dimensions": {"width": 0.06160000000000001, "height": 0.08960000000000001, "depth": 0.0364, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.036888119554007615, -0.3059240530520103, 0.0], "rotation": [0, 0, 0], "scale": [0.06160000000000001, 0.08960000000000001, 0.0364]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "hand-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_hand_r_39.add(mesh_hand_r_39);
  meshes["hand-r"] = mesh_hand_r_39;
  colliders["hand-r"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["hand-r"] ??= [];
  destructionGroups["hand-r"].push(node_hand_r_39);

  const attachment_thumb_r_1_40 = {"parentSocket": "hand-r-thumb-1", "localStart": [0.028, -0.00538, 0.0056], "localEnd": [0.04312, -0.0184, 0.0119], "contactType": "rigid-weld", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_thumb_r_1_40 = makeAttachmentEndpoint(attachment_thumb_r_1_40);
  const node_thumb_r_1_40 = new THREE.Group();
  node_thumb_r_1_40.name = "Thumb R phalanx 1__pivot";
  node_thumb_r_1_40.scale.set(1, 1, 1);
  if (endpoint_thumb_r_1_40) {
    node_thumb_r_1_40.position.copy(endpoint_thumb_r_1_40.start);
    node_thumb_r_1_40.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_thumb_r_1_40.position.set(0.028000000000000025, -0.005375999999999992, 0.005600000000000001);
    node_thumb_r_1_40.rotation.set(0.0, 0.0, 0.0);
  }
  node_thumb_r_1_40.userData.sculptComponent = {"id": "thumb-r-1", "name": "Thumb R phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Thumb R phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-r", "attachment": {"parentSocket": "hand-r-thumb-1", "localStart": [0.028, -0.00538, 0.0056], "localEnd": [0.04312, -0.0184, 0.0119], "contactType": "rigid-weld", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.017920000000000002, "height": 0.021, "depth": 0.017920000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.028000000000000025, -0.005375999999999992, 0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.017920000000000002, 0.021, 0.017920000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-r-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_thumb_r_1_40.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-r-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["hand-r"] ?? root).add(node_thumb_r_1_40);
  nodes["thumb-r-1"] = node_thumb_r_1_40;
  const mesh_thumb_r_1_40Geometry = endpoint_thumb_r_1_40
    ? new THREE.CylinderGeometry(endpoint_thumb_r_1_40.endRadius, endpoint_thumb_r_1_40.baseRadius, endpoint_thumb_r_1_40.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_thumb_r_1_40) {
    mesh_thumb_r_1_40Geometry.scale(0.017920000000000002, 0.021, 0.017920000000000002);
  }
  const mesh_thumb_r_1_40 = new THREE.SkinnedMesh(
    mesh_thumb_r_1_40Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_thumb_r_1_40.name = "Thumb R phalanx 1";
  if (endpoint_thumb_r_1_40) {
    mesh_thumb_r_1_40.position.copy(endpoint_thumb_r_1_40.midpoint);
    mesh_thumb_r_1_40.quaternion.copy(endpoint_thumb_r_1_40.quaternion);
  }
  mesh_thumb_r_1_40.castShadow = options.castShadow ?? true;
  mesh_thumb_r_1_40.receiveShadow = options.receiveShadow ?? true;
  mesh_thumb_r_1_40.userData.sculptComponent = {"id": "thumb-r-1", "name": "Thumb R phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Thumb R phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-r", "attachment": {"parentSocket": "hand-r-thumb-1", "localStart": [0.028, -0.00538, 0.0056], "localEnd": [0.04312, -0.0184, 0.0119], "contactType": "rigid-weld", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.017920000000000002, "height": 0.021, "depth": 0.017920000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.028000000000000025, -0.005375999999999992, 0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.017920000000000002, 0.021, 0.017920000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-r-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_thumb_r_1_40.add(mesh_thumb_r_1_40);
  meshes["thumb-r-1"] = mesh_thumb_r_1_40;
  colliders["thumb-r-1"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["thumb-r-1"] ??= [];
  destructionGroups["thumb-r-1"].push(node_thumb_r_1_40);

  const attachment_thumb_r_2_41 = {"parentSocket": "thumb-r-1-thumb-2", "localStart": [0.01512, -0.01302, 0.0063], "localEnd": [0.02621, -0.02257, 0.01092], "contactType": "hinge-joint", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_thumb_r_2_41 = makeAttachmentEndpoint(attachment_thumb_r_2_41);
  const node_thumb_r_2_41 = new THREE.Group();
  node_thumb_r_2_41.name = "Thumb R phalanx 2__pivot";
  node_thumb_r_2_41.scale.set(1, 1, 1);
  if (endpoint_thumb_r_2_41) {
    node_thumb_r_2_41.position.copy(endpoint_thumb_r_2_41.start);
    node_thumb_r_2_41.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_thumb_r_2_41.position.set(0.015120000000000022, -0.013020000000000004, 0.0063);
    node_thumb_r_2_41.rotation.set(0.0, 0.0, 0.0);
  }
  node_thumb_r_2_41.userData.sculptComponent = {"id": "thumb-r-2", "name": "Thumb R phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Thumb R phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "thumb-r-1", "attachment": {"parentSocket": "thumb-r-1-thumb-2", "localStart": [0.01512, -0.01302, 0.0063], "localEnd": [0.02621, -0.02257, 0.01092], "contactType": "hinge-joint", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.017920000000000002, "height": 0.015400000000000002, "depth": 0.017920000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.015120000000000022, -0.013020000000000004, 0.0063], "rotation": [0, 0, 0], "scale": [0.017920000000000002, 0.015400000000000002, 0.017920000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-r-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_thumb_r_2_41.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-r-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["thumb-r-1"] ?? root).add(node_thumb_r_2_41);
  nodes["thumb-r-2"] = node_thumb_r_2_41;
  const mesh_thumb_r_2_41Geometry = endpoint_thumb_r_2_41
    ? new THREE.CylinderGeometry(endpoint_thumb_r_2_41.endRadius, endpoint_thumb_r_2_41.baseRadius, endpoint_thumb_r_2_41.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_thumb_r_2_41) {
    mesh_thumb_r_2_41Geometry.scale(0.017920000000000002, 0.015400000000000002, 0.017920000000000002);
  }
  const mesh_thumb_r_2_41 = new THREE.SkinnedMesh(
    mesh_thumb_r_2_41Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_thumb_r_2_41.name = "Thumb R phalanx 2";
  if (endpoint_thumb_r_2_41) {
    mesh_thumb_r_2_41.position.copy(endpoint_thumb_r_2_41.midpoint);
    mesh_thumb_r_2_41.quaternion.copy(endpoint_thumb_r_2_41.quaternion);
  }
  mesh_thumb_r_2_41.castShadow = options.castShadow ?? true;
  mesh_thumb_r_2_41.receiveShadow = options.receiveShadow ?? true;
  mesh_thumb_r_2_41.userData.sculptComponent = {"id": "thumb-r-2", "name": "Thumb R phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Thumb R phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "thumb-r-1", "attachment": {"parentSocket": "thumb-r-1-thumb-2", "localStart": [0.01512, -0.01302, 0.0063], "localEnd": [0.02621, -0.02257, 0.01092], "contactType": "hinge-joint", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.017920000000000002, "height": 0.015400000000000002, "depth": 0.017920000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.015120000000000022, -0.013020000000000004, 0.0063], "rotation": [0, 0, 0], "scale": [0.017920000000000002, 0.015400000000000002, 0.017920000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-r-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_thumb_r_2_41.add(mesh_thumb_r_2_41);
  meshes["thumb-r-2"] = mesh_thumb_r_2_41;
  colliders["thumb-r-2"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["thumb-r-2"] ??= [];
  destructionGroups["thumb-r-2"].push(node_thumb_r_2_41);

  const attachment_thumb_r_3_42 = {"parentSocket": "thumb-r-2-thumb-3", "localStart": [0.01109, -0.00955, 0.00462], "localEnd": [0.01915, -0.01649, 0.00798], "contactType": "hinge-joint", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_thumb_r_3_42 = makeAttachmentEndpoint(attachment_thumb_r_3_42);
  const node_thumb_r_3_42 = new THREE.Group();
  node_thumb_r_3_42.name = "Thumb R phalanx 3__pivot";
  node_thumb_r_3_42.scale.set(1, 1, 1);
  if (endpoint_thumb_r_3_42) {
    node_thumb_r_3_42.position.copy(endpoint_thumb_r_3_42.start);
    node_thumb_r_3_42.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_thumb_r_3_42.position.set(0.011087999999999987, -0.009548000000000001, 0.004620000000000003);
    node_thumb_r_3_42.rotation.set(0.0, 0.0, 0.0);
  }
  node_thumb_r_3_42.userData.sculptComponent = {"id": "thumb-r-3", "name": "Thumb R phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Thumb R phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "thumb-r-2", "attachment": {"parentSocket": "thumb-r-2-thumb-3", "localStart": [0.01109, -0.00955, 0.00462], "localEnd": [0.01915, -0.01649, 0.00798], "contactType": "hinge-joint", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.017920000000000002, "height": 0.011200000000000002, "depth": 0.017920000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.011087999999999987, -0.009548000000000001, 0.004620000000000003], "rotation": [0, 0, 0], "scale": [0.017920000000000002, 0.011200000000000002, 0.017920000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-r-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_thumb_r_3_42.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-r-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["thumb-r-2"] ?? root).add(node_thumb_r_3_42);
  nodes["thumb-r-3"] = node_thumb_r_3_42;
  const mesh_thumb_r_3_42Geometry = endpoint_thumb_r_3_42
    ? new THREE.CylinderGeometry(endpoint_thumb_r_3_42.endRadius, endpoint_thumb_r_3_42.baseRadius, endpoint_thumb_r_3_42.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_thumb_r_3_42) {
    mesh_thumb_r_3_42Geometry.scale(0.017920000000000002, 0.011200000000000002, 0.017920000000000002);
  }
  const mesh_thumb_r_3_42 = new THREE.SkinnedMesh(
    mesh_thumb_r_3_42Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_thumb_r_3_42.name = "Thumb R phalanx 3";
  if (endpoint_thumb_r_3_42) {
    mesh_thumb_r_3_42.position.copy(endpoint_thumb_r_3_42.midpoint);
    mesh_thumb_r_3_42.quaternion.copy(endpoint_thumb_r_3_42.quaternion);
  }
  mesh_thumb_r_3_42.castShadow = options.castShadow ?? true;
  mesh_thumb_r_3_42.receiveShadow = options.receiveShadow ?? true;
  mesh_thumb_r_3_42.userData.sculptComponent = {"id": "thumb-r-3", "name": "Thumb R phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Thumb R phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "thumb-r-2", "attachment": {"parentSocket": "thumb-r-2-thumb-3", "localStart": [0.01109, -0.00955, 0.00462], "localEnd": [0.01915, -0.01649, 0.00798], "contactType": "hinge-joint", "baseRadius": 0.00896, "endRadius": 0.00735, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.017920000000000002, "height": 0.011200000000000002, "depth": 0.017920000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.011087999999999987, -0.009548000000000001, 0.004620000000000003], "rotation": [0, 0, 0], "scale": [0.017920000000000002, 0.011200000000000002, 0.017920000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thumb-r-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_thumb_r_3_42.add(mesh_thumb_r_3_42);
  meshes["thumb-r-3"] = mesh_thumb_r_3_42;
  colliders["thumb-r-3"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["thumb-r-3"] ??= [];
  destructionGroups["thumb-r-3"].push(node_thumb_r_3_42);

  const attachment_index_r_1_43 = {"parentSocket": "hand-r-index-1", "localStart": [0.021, -0.03763, 0.0028], "localEnd": [0.01748, -0.06682, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_index_r_1_43 = makeAttachmentEndpoint(attachment_index_r_1_43);
  const node_index_r_1_43 = new THREE.Group();
  node_index_r_1_43.name = "Index R phalanx 1__pivot";
  node_index_r_1_43.scale.set(1, 1, 1);
  if (endpoint_index_r_1_43) {
    node_index_r_1_43.position.copy(endpoint_index_r_1_43.start);
    node_index_r_1_43.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_index_r_1_43.position.set(0.02100000000000002, -0.037632, 0.0028000000000000004);
    node_index_r_1_43.rotation.set(0.0, 0.0, 0.0);
  }
  node_index_r_1_43.userData.sculptComponent = {"id": "index-r-1", "name": "Index R phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Index R phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-r", "attachment": {"parentSocket": "hand-r-index-1", "localStart": [0.021, -0.03763, 0.0028], "localEnd": [0.01748, -0.06682, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015680000000000003, "height": 0.029400000000000003, "depth": 0.015680000000000003, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.02100000000000002, -0.037632, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.015680000000000003, 0.029400000000000003, 0.015680000000000003]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-r-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_index_r_1_43.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-r-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["hand-r"] ?? root).add(node_index_r_1_43);
  nodes["index-r-1"] = node_index_r_1_43;
  const mesh_index_r_1_43Geometry = endpoint_index_r_1_43
    ? new THREE.CylinderGeometry(endpoint_index_r_1_43.endRadius, endpoint_index_r_1_43.baseRadius, endpoint_index_r_1_43.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_index_r_1_43) {
    mesh_index_r_1_43Geometry.scale(0.015680000000000003, 0.029400000000000003, 0.015680000000000003);
  }
  const mesh_index_r_1_43 = new THREE.SkinnedMesh(
    mesh_index_r_1_43Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_index_r_1_43.name = "Index R phalanx 1";
  if (endpoint_index_r_1_43) {
    mesh_index_r_1_43.position.copy(endpoint_index_r_1_43.midpoint);
    mesh_index_r_1_43.quaternion.copy(endpoint_index_r_1_43.quaternion);
  }
  mesh_index_r_1_43.castShadow = options.castShadow ?? true;
  mesh_index_r_1_43.receiveShadow = options.receiveShadow ?? true;
  mesh_index_r_1_43.userData.sculptComponent = {"id": "index-r-1", "name": "Index R phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Index R phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-r", "attachment": {"parentSocket": "hand-r-index-1", "localStart": [0.021, -0.03763, 0.0028], "localEnd": [0.01748, -0.06682, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015680000000000003, "height": 0.029400000000000003, "depth": 0.015680000000000003, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.02100000000000002, -0.037632, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.015680000000000003, 0.029400000000000003, 0.015680000000000003]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-r-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_index_r_1_43.add(mesh_index_r_1_43);
  meshes["index-r-1"] = mesh_index_r_1_43;
  colliders["index-r-1"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["index-r-1"] ??= [];
  destructionGroups["index-r-1"].push(node_index_r_1_43);

  const attachment_index_r_2_44 = {"parentSocket": "index-r-1-index-2", "localStart": [-0.00352, -0.02919, 0.0], "localEnd": [-0.00593, -0.0492, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_index_r_2_44 = makeAttachmentEndpoint(attachment_index_r_2_44);
  const node_index_r_2_44 = new THREE.Group();
  node_index_r_2_44.name = "Index R phalanx 2__pivot";
  node_index_r_2_44.scale.set(1, 1, 1);
  if (endpoint_index_r_2_44) {
    node_index_r_2_44.position.copy(endpoint_index_r_2_44.start);
    node_index_r_2_44.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_index_r_2_44.position.set(-0.0035195388942942385, -0.029188573894103675, 0.0);
    node_index_r_2_44.rotation.set(0.0, 0.0, 0.0);
  }
  node_index_r_2_44.userData.sculptComponent = {"id": "index-r-2", "name": "Index R phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Index R phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "index-r-1", "attachment": {"parentSocket": "index-r-1-index-2", "localStart": [-0.00352, -0.02919, 0.0], "localEnd": [-0.00593, -0.0492, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015680000000000003, "height": 0.02016, "depth": 0.015680000000000003, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.0035195388942942385, -0.029188573894103675, 0.0], "rotation": [0, 0, 0], "scale": [0.015680000000000003, 0.02016, 0.015680000000000003]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-r-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_index_r_2_44.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-r-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["index-r-1"] ?? root).add(node_index_r_2_44);
  nodes["index-r-2"] = node_index_r_2_44;
  const mesh_index_r_2_44Geometry = endpoint_index_r_2_44
    ? new THREE.CylinderGeometry(endpoint_index_r_2_44.endRadius, endpoint_index_r_2_44.baseRadius, endpoint_index_r_2_44.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_index_r_2_44) {
    mesh_index_r_2_44Geometry.scale(0.015680000000000003, 0.02016, 0.015680000000000003);
  }
  const mesh_index_r_2_44 = new THREE.SkinnedMesh(
    mesh_index_r_2_44Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_index_r_2_44.name = "Index R phalanx 2";
  if (endpoint_index_r_2_44) {
    mesh_index_r_2_44.position.copy(endpoint_index_r_2_44.midpoint);
    mesh_index_r_2_44.quaternion.copy(endpoint_index_r_2_44.quaternion);
  }
  mesh_index_r_2_44.castShadow = options.castShadow ?? true;
  mesh_index_r_2_44.receiveShadow = options.receiveShadow ?? true;
  mesh_index_r_2_44.userData.sculptComponent = {"id": "index-r-2", "name": "Index R phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Index R phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "index-r-1", "attachment": {"parentSocket": "index-r-1-index-2", "localStart": [-0.00352, -0.02919, 0.0], "localEnd": [-0.00593, -0.0492, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015680000000000003, "height": 0.02016, "depth": 0.015680000000000003, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.0035195388942942385, -0.029188573894103675, 0.0], "rotation": [0, 0, 0], "scale": [0.015680000000000003, 0.02016, 0.015680000000000003]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-r-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_index_r_2_44.add(mesh_index_r_2_44);
  meshes["index-r-2"] = mesh_index_r_2_44;
  colliders["index-r-2"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["index-r-2"] ??= [];
  destructionGroups["index-r-2"].push(node_index_r_2_44);

  const attachment_index_r_3_45 = {"parentSocket": "index-r-2-index-3", "localStart": [-0.00241, -0.02002, 0.0], "localEnd": [-0.00402, -0.03336, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_index_r_3_45 = makeAttachmentEndpoint(attachment_index_r_3_45);
  const node_index_r_3_45 = new THREE.Group();
  node_index_r_3_45.name = "Index R phalanx 3__pivot";
  node_index_r_3_45.scale.set(1, 1, 1);
  if (endpoint_index_r_3_45) {
    node_index_r_3_45.position.copy(endpoint_index_r_3_45.start);
    node_index_r_3_45.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_index_r_3_45.position.set(-0.0024133980989446413, -0.020015022098813923, 0.0);
    node_index_r_3_45.rotation.set(0.0, 0.0, 0.0);
  }
  node_index_r_3_45.userData.sculptComponent = {"id": "index-r-3", "name": "Index R phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Index R phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "index-r-2", "attachment": {"parentSocket": "index-r-2-index-3", "localStart": [-0.00241, -0.02002, 0.0], "localEnd": [-0.00402, -0.03336, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015680000000000003, "height": 0.013440000000000002, "depth": 0.015680000000000003, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.0024133980989446413, -0.020015022098813923, 0.0], "rotation": [0, 0, 0], "scale": [0.015680000000000003, 0.013440000000000002, 0.015680000000000003]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-r-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_index_r_3_45.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-r-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["index-r-2"] ?? root).add(node_index_r_3_45);
  nodes["index-r-3"] = node_index_r_3_45;
  const mesh_index_r_3_45Geometry = endpoint_index_r_3_45
    ? new THREE.CylinderGeometry(endpoint_index_r_3_45.endRadius, endpoint_index_r_3_45.baseRadius, endpoint_index_r_3_45.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_index_r_3_45) {
    mesh_index_r_3_45Geometry.scale(0.015680000000000003, 0.013440000000000002, 0.015680000000000003);
  }
  const mesh_index_r_3_45 = new THREE.SkinnedMesh(
    mesh_index_r_3_45Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_index_r_3_45.name = "Index R phalanx 3";
  if (endpoint_index_r_3_45) {
    mesh_index_r_3_45.position.copy(endpoint_index_r_3_45.midpoint);
    mesh_index_r_3_45.quaternion.copy(endpoint_index_r_3_45.quaternion);
  }
  mesh_index_r_3_45.castShadow = options.castShadow ?? true;
  mesh_index_r_3_45.receiveShadow = options.receiveShadow ?? true;
  mesh_index_r_3_45.userData.sculptComponent = {"id": "index-r-3", "name": "Index R phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Index R phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "index-r-2", "attachment": {"parentSocket": "index-r-2-index-3", "localStart": [-0.00241, -0.02002, 0.0], "localEnd": [-0.00402, -0.03336, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00784, "endRadius": 0.00643, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015680000000000003, "height": 0.013440000000000002, "depth": 0.015680000000000003, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.0024133980989446413, -0.020015022098813923, 0.0], "rotation": [0, 0, 0], "scale": [0.015680000000000003, 0.013440000000000002, 0.015680000000000003]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "index-r-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_index_r_3_45.add(mesh_index_r_3_45);
  meshes["index-r-3"] = mesh_index_r_3_45;
  colliders["index-r-3"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["index-r-3"] ??= [];
  destructionGroups["index-r-3"].push(node_index_r_3_45);

  const attachment_middle_r_1_46 = {"parentSocket": "hand-r-middle-1", "localStart": [0.007, -0.03763, 0.0028], "localEnd": [0.00315, -0.0696, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_middle_r_1_46 = makeAttachmentEndpoint(attachment_middle_r_1_46);
  const node_middle_r_1_46 = new THREE.Group();
  node_middle_r_1_46.name = "Middle R phalanx 1__pivot";
  node_middle_r_1_46.scale.set(1, 1, 1);
  if (endpoint_middle_r_1_46) {
    node_middle_r_1_46.position.copy(endpoint_middle_r_1_46.start);
    node_middle_r_1_46.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_middle_r_1_46.position.set(0.007000000000000006, -0.037632, 0.0028000000000000004);
    node_middle_r_1_46.rotation.set(0.0, 0.0, 0.0);
  }
  node_middle_r_1_46.userData.sculptComponent = {"id": "middle-r-1", "name": "Middle R phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Middle R phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-r", "attachment": {"parentSocket": "hand-r-middle-1", "localStart": [0.007, -0.03763, 0.0028], "localEnd": [0.00315, -0.0696, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.01624, "height": 0.032200000000000006, "depth": 0.01624, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.007000000000000006, -0.037632, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.01624, 0.032200000000000006, 0.01624]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-r-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_middle_r_1_46.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-r-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["hand-r"] ?? root).add(node_middle_r_1_46);
  nodes["middle-r-1"] = node_middle_r_1_46;
  const mesh_middle_r_1_46Geometry = endpoint_middle_r_1_46
    ? new THREE.CylinderGeometry(endpoint_middle_r_1_46.endRadius, endpoint_middle_r_1_46.baseRadius, endpoint_middle_r_1_46.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_middle_r_1_46) {
    mesh_middle_r_1_46Geometry.scale(0.01624, 0.032200000000000006, 0.01624);
  }
  const mesh_middle_r_1_46 = new THREE.SkinnedMesh(
    mesh_middle_r_1_46Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_middle_r_1_46.name = "Middle R phalanx 1";
  if (endpoint_middle_r_1_46) {
    mesh_middle_r_1_46.position.copy(endpoint_middle_r_1_46.midpoint);
    mesh_middle_r_1_46.quaternion.copy(endpoint_middle_r_1_46.quaternion);
  }
  mesh_middle_r_1_46.castShadow = options.castShadow ?? true;
  mesh_middle_r_1_46.receiveShadow = options.receiveShadow ?? true;
  mesh_middle_r_1_46.userData.sculptComponent = {"id": "middle-r-1", "name": "Middle R phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Middle R phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-r", "attachment": {"parentSocket": "hand-r-middle-1", "localStart": [0.007, -0.03763, 0.0028], "localEnd": [0.00315, -0.0696, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.01624, "height": 0.032200000000000006, "depth": 0.01624, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.007000000000000006, -0.037632, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.01624, 0.032200000000000006, 0.01624]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-r-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_middle_r_1_46.add(mesh_middle_r_1_46);
  meshes["middle-r-1"] = mesh_middle_r_1_46;
  colliders["middle-r-1"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["middle-r-1"] ??= [];
  destructionGroups["middle-r-1"].push(node_middle_r_1_46);

  const attachment_middle_r_2_47 = {"parentSocket": "middle-r-1-middle-2", "localStart": [-0.00385, -0.03197, 0.0], "localEnd": [-0.00654, -0.05421, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_middle_r_2_47 = makeAttachmentEndpoint(attachment_middle_r_2_47);
  const node_middle_r_2_47 = new THREE.Group();
  node_middle_r_2_47.name = "Middle R phalanx 2__pivot";
  node_middle_r_2_47.scale.set(1, 1, 1);
  if (endpoint_middle_r_2_47) {
    node_middle_r_2_47.position.copy(endpoint_middle_r_2_47.start);
    node_middle_r_2_47.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_middle_r_2_47.position.set(-0.003854733074703187, -0.03196843807449448, 0.0);
    node_middle_r_2_47.rotation.set(0.0, 0.0, 0.0);
  }
  node_middle_r_2_47.userData.sculptComponent = {"id": "middle-r-2", "name": "Middle R phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Middle R phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "middle-r-1", "attachment": {"parentSocket": "middle-r-1-middle-2", "localStart": [-0.00385, -0.03197, 0.0], "localEnd": [-0.00654, -0.05421, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.01624, "height": 0.022400000000000003, "depth": 0.01624, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.003854733074703187, -0.03196843807449448, 0.0], "rotation": [0, 0, 0], "scale": [0.01624, 0.022400000000000003, 0.01624]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-r-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_middle_r_2_47.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-r-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["middle-r-1"] ?? root).add(node_middle_r_2_47);
  nodes["middle-r-2"] = node_middle_r_2_47;
  const mesh_middle_r_2_47Geometry = endpoint_middle_r_2_47
    ? new THREE.CylinderGeometry(endpoint_middle_r_2_47.endRadius, endpoint_middle_r_2_47.baseRadius, endpoint_middle_r_2_47.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_middle_r_2_47) {
    mesh_middle_r_2_47Geometry.scale(0.01624, 0.022400000000000003, 0.01624);
  }
  const mesh_middle_r_2_47 = new THREE.SkinnedMesh(
    mesh_middle_r_2_47Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_middle_r_2_47.name = "Middle R phalanx 2";
  if (endpoint_middle_r_2_47) {
    mesh_middle_r_2_47.position.copy(endpoint_middle_r_2_47.midpoint);
    mesh_middle_r_2_47.quaternion.copy(endpoint_middle_r_2_47.quaternion);
  }
  mesh_middle_r_2_47.castShadow = options.castShadow ?? true;
  mesh_middle_r_2_47.receiveShadow = options.receiveShadow ?? true;
  mesh_middle_r_2_47.userData.sculptComponent = {"id": "middle-r-2", "name": "Middle R phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Middle R phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "middle-r-1", "attachment": {"parentSocket": "middle-r-1-middle-2", "localStart": [-0.00385, -0.03197, 0.0], "localEnd": [-0.00654, -0.05421, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.01624, "height": 0.022400000000000003, "depth": 0.01624, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.003854733074703187, -0.03196843807449448, 0.0], "rotation": [0, 0, 0], "scale": [0.01624, 0.022400000000000003, 0.01624]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-r-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_middle_r_2_47.add(mesh_middle_r_2_47);
  meshes["middle-r-2"] = mesh_middle_r_2_47;
  colliders["middle-r-2"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["middle-r-2"] ??= [];
  destructionGroups["middle-r-2"].push(node_middle_r_2_47);

  const attachment_middle_r_3_48 = {"parentSocket": "middle-r-2-middle-3", "localStart": [-0.00268, -0.02224, 0.0], "localEnd": [-0.00436, -0.03614, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_middle_r_3_48 = makeAttachmentEndpoint(attachment_middle_r_3_48);
  const node_middle_r_3_48 = new THREE.Group();
  node_middle_r_3_48.name = "Middle R phalanx 3__pivot";
  node_middle_r_3_48.scale.set(1, 1, 1);
  if (endpoint_middle_r_3_48) {
    node_middle_r_3_48.position.copy(endpoint_middle_r_3_48.start);
    node_middle_r_3_48.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_middle_r_3_48.position.set(-0.0026815534432718113, -0.022238913443126618, 0.0);
    node_middle_r_3_48.rotation.set(0.0, 0.0, 0.0);
  }
  node_middle_r_3_48.userData.sculptComponent = {"id": "middle-r-3", "name": "Middle R phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Middle R phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "middle-r-2", "attachment": {"parentSocket": "middle-r-2-middle-3", "localStart": [-0.00268, -0.02224, 0.0], "localEnd": [-0.00436, -0.03614, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.01624, "height": 0.014000000000000002, "depth": 0.01624, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.0026815534432718113, -0.022238913443126618, 0.0], "rotation": [0, 0, 0], "scale": [0.01624, 0.014000000000000002, 0.01624]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-r-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_middle_r_3_48.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-r-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["middle-r-2"] ?? root).add(node_middle_r_3_48);
  nodes["middle-r-3"] = node_middle_r_3_48;
  const mesh_middle_r_3_48Geometry = endpoint_middle_r_3_48
    ? new THREE.CylinderGeometry(endpoint_middle_r_3_48.endRadius, endpoint_middle_r_3_48.baseRadius, endpoint_middle_r_3_48.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_middle_r_3_48) {
    mesh_middle_r_3_48Geometry.scale(0.01624, 0.014000000000000002, 0.01624);
  }
  const mesh_middle_r_3_48 = new THREE.SkinnedMesh(
    mesh_middle_r_3_48Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_middle_r_3_48.name = "Middle R phalanx 3";
  if (endpoint_middle_r_3_48) {
    mesh_middle_r_3_48.position.copy(endpoint_middle_r_3_48.midpoint);
    mesh_middle_r_3_48.quaternion.copy(endpoint_middle_r_3_48.quaternion);
  }
  mesh_middle_r_3_48.castShadow = options.castShadow ?? true;
  mesh_middle_r_3_48.receiveShadow = options.receiveShadow ?? true;
  mesh_middle_r_3_48.userData.sculptComponent = {"id": "middle-r-3", "name": "Middle R phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Middle R phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "middle-r-2", "attachment": {"parentSocket": "middle-r-2-middle-3", "localStart": [-0.00268, -0.02224, 0.0], "localEnd": [-0.00436, -0.03614, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00812, "endRadius": 0.00666, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.01624, "height": 0.014000000000000002, "depth": 0.01624, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.0026815534432718113, -0.022238913443126618, 0.0], "rotation": [0, 0, 0], "scale": [0.01624, 0.014000000000000002, 0.01624]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "middle-r-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_middle_r_3_48.add(mesh_middle_r_3_48);
  meshes["middle-r-3"] = mesh_middle_r_3_48;
  colliders["middle-r-3"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["middle-r-3"] ??= [];
  destructionGroups["middle-r-3"].push(node_middle_r_3_48);

  const attachment_ring_r_1_49 = {"parentSocket": "hand-r-ring-1", "localStart": [-0.007, -0.03763, 0.0028], "localEnd": [-0.01052, -0.06682, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_ring_r_1_49 = makeAttachmentEndpoint(attachment_ring_r_1_49);
  const node_ring_r_1_49 = new THREE.Group();
  node_ring_r_1_49.name = "Ring R phalanx 1__pivot";
  node_ring_r_1_49.scale.set(1, 1, 1);
  if (endpoint_ring_r_1_49) {
    node_ring_r_1_49.position.copy(endpoint_ring_r_1_49.start);
    node_ring_r_1_49.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_ring_r_1_49.position.set(-0.007000000000000006, -0.037632, 0.0028000000000000004);
    node_ring_r_1_49.rotation.set(0.0, 0.0, 0.0);
  }
  node_ring_r_1_49.userData.sculptComponent = {"id": "ring-r-1", "name": "Ring R phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Ring R phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-r", "attachment": {"parentSocket": "hand-r-ring-1", "localStart": [-0.007, -0.03763, 0.0028], "localEnd": [-0.01052, -0.06682, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015120000000000001, "height": 0.029400000000000003, "depth": 0.015120000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.007000000000000006, -0.037632, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.015120000000000001, 0.029400000000000003, 0.015120000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-r-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_ring_r_1_49.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-r-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["hand-r"] ?? root).add(node_ring_r_1_49);
  nodes["ring-r-1"] = node_ring_r_1_49;
  const mesh_ring_r_1_49Geometry = endpoint_ring_r_1_49
    ? new THREE.CylinderGeometry(endpoint_ring_r_1_49.endRadius, endpoint_ring_r_1_49.baseRadius, endpoint_ring_r_1_49.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_ring_r_1_49) {
    mesh_ring_r_1_49Geometry.scale(0.015120000000000001, 0.029400000000000003, 0.015120000000000001);
  }
  const mesh_ring_r_1_49 = new THREE.SkinnedMesh(
    mesh_ring_r_1_49Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_ring_r_1_49.name = "Ring R phalanx 1";
  if (endpoint_ring_r_1_49) {
    mesh_ring_r_1_49.position.copy(endpoint_ring_r_1_49.midpoint);
    mesh_ring_r_1_49.quaternion.copy(endpoint_ring_r_1_49.quaternion);
  }
  mesh_ring_r_1_49.castShadow = options.castShadow ?? true;
  mesh_ring_r_1_49.receiveShadow = options.receiveShadow ?? true;
  mesh_ring_r_1_49.userData.sculptComponent = {"id": "ring-r-1", "name": "Ring R phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Ring R phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-r", "attachment": {"parentSocket": "hand-r-ring-1", "localStart": [-0.007, -0.03763, 0.0028], "localEnd": [-0.01052, -0.06682, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015120000000000001, "height": 0.029400000000000003, "depth": 0.015120000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.007000000000000006, -0.037632, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.015120000000000001, 0.029400000000000003, 0.015120000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-r-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_ring_r_1_49.add(mesh_ring_r_1_49);
  meshes["ring-r-1"] = mesh_ring_r_1_49;
  colliders["ring-r-1"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["ring-r-1"] ??= [];
  destructionGroups["ring-r-1"].push(node_ring_r_1_49);

  const attachment_ring_r_2_50 = {"parentSocket": "ring-r-1-ring-2", "localStart": [-0.00352, -0.02919, 0.0], "localEnd": [-0.00593, -0.0492, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_ring_r_2_50 = makeAttachmentEndpoint(attachment_ring_r_2_50);
  const node_ring_r_2_50 = new THREE.Group();
  node_ring_r_2_50.name = "Ring R phalanx 2__pivot";
  node_ring_r_2_50.scale.set(1, 1, 1);
  if (endpoint_ring_r_2_50) {
    node_ring_r_2_50.position.copy(endpoint_ring_r_2_50.start);
    node_ring_r_2_50.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_ring_r_2_50.position.set(-0.0035195388942942385, -0.029188573894103675, 0.0);
    node_ring_r_2_50.rotation.set(0.0, 0.0, 0.0);
  }
  node_ring_r_2_50.userData.sculptComponent = {"id": "ring-r-2", "name": "Ring R phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Ring R phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "ring-r-1", "attachment": {"parentSocket": "ring-r-1-ring-2", "localStart": [-0.00352, -0.02919, 0.0], "localEnd": [-0.00593, -0.0492, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015120000000000001, "height": 0.02016, "depth": 0.015120000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.0035195388942942385, -0.029188573894103675, 0.0], "rotation": [0, 0, 0], "scale": [0.015120000000000001, 0.02016, 0.015120000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-r-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_ring_r_2_50.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-r-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["ring-r-1"] ?? root).add(node_ring_r_2_50);
  nodes["ring-r-2"] = node_ring_r_2_50;
  const mesh_ring_r_2_50Geometry = endpoint_ring_r_2_50
    ? new THREE.CylinderGeometry(endpoint_ring_r_2_50.endRadius, endpoint_ring_r_2_50.baseRadius, endpoint_ring_r_2_50.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_ring_r_2_50) {
    mesh_ring_r_2_50Geometry.scale(0.015120000000000001, 0.02016, 0.015120000000000001);
  }
  const mesh_ring_r_2_50 = new THREE.SkinnedMesh(
    mesh_ring_r_2_50Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_ring_r_2_50.name = "Ring R phalanx 2";
  if (endpoint_ring_r_2_50) {
    mesh_ring_r_2_50.position.copy(endpoint_ring_r_2_50.midpoint);
    mesh_ring_r_2_50.quaternion.copy(endpoint_ring_r_2_50.quaternion);
  }
  mesh_ring_r_2_50.castShadow = options.castShadow ?? true;
  mesh_ring_r_2_50.receiveShadow = options.receiveShadow ?? true;
  mesh_ring_r_2_50.userData.sculptComponent = {"id": "ring-r-2", "name": "Ring R phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Ring R phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "ring-r-1", "attachment": {"parentSocket": "ring-r-1-ring-2", "localStart": [-0.00352, -0.02919, 0.0], "localEnd": [-0.00593, -0.0492, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015120000000000001, "height": 0.02016, "depth": 0.015120000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.0035195388942942385, -0.029188573894103675, 0.0], "rotation": [0, 0, 0], "scale": [0.015120000000000001, 0.02016, 0.015120000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-r-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_ring_r_2_50.add(mesh_ring_r_2_50);
  meshes["ring-r-2"] = mesh_ring_r_2_50;
  colliders["ring-r-2"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["ring-r-2"] ??= [];
  destructionGroups["ring-r-2"].push(node_ring_r_2_50);

  const attachment_ring_r_3_51 = {"parentSocket": "ring-r-2-ring-3", "localStart": [-0.00241, -0.02002, 0.0], "localEnd": [-0.00396, -0.0328, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_ring_r_3_51 = makeAttachmentEndpoint(attachment_ring_r_3_51);
  const node_ring_r_3_51 = new THREE.Group();
  node_ring_r_3_51.name = "Ring R phalanx 3__pivot";
  node_ring_r_3_51.scale.set(1, 1, 1);
  if (endpoint_ring_r_3_51) {
    node_ring_r_3_51.position.copy(endpoint_ring_r_3_51.start);
    node_ring_r_3_51.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_ring_r_3_51.position.set(-0.0024133980989446413, -0.020015022098813923, 0.0);
    node_ring_r_3_51.rotation.set(0.0, 0.0, 0.0);
  }
  node_ring_r_3_51.userData.sculptComponent = {"id": "ring-r-3", "name": "Ring R phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Ring R phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "ring-r-2", "attachment": {"parentSocket": "ring-r-2-ring-3", "localStart": [-0.00241, -0.02002, 0.0], "localEnd": [-0.00396, -0.0328, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015120000000000001, "height": 0.01288, "depth": 0.015120000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.0024133980989446413, -0.020015022098813923, 0.0], "rotation": [0, 0, 0], "scale": [0.015120000000000001, 0.01288, 0.015120000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-r-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_ring_r_3_51.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-r-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["ring-r-2"] ?? root).add(node_ring_r_3_51);
  nodes["ring-r-3"] = node_ring_r_3_51;
  const mesh_ring_r_3_51Geometry = endpoint_ring_r_3_51
    ? new THREE.CylinderGeometry(endpoint_ring_r_3_51.endRadius, endpoint_ring_r_3_51.baseRadius, endpoint_ring_r_3_51.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_ring_r_3_51) {
    mesh_ring_r_3_51Geometry.scale(0.015120000000000001, 0.01288, 0.015120000000000001);
  }
  const mesh_ring_r_3_51 = new THREE.SkinnedMesh(
    mesh_ring_r_3_51Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_ring_r_3_51.name = "Ring R phalanx 3";
  if (endpoint_ring_r_3_51) {
    mesh_ring_r_3_51.position.copy(endpoint_ring_r_3_51.midpoint);
    mesh_ring_r_3_51.quaternion.copy(endpoint_ring_r_3_51.quaternion);
  }
  mesh_ring_r_3_51.castShadow = options.castShadow ?? true;
  mesh_ring_r_3_51.receiveShadow = options.receiveShadow ?? true;
  mesh_ring_r_3_51.userData.sculptComponent = {"id": "ring-r-3", "name": "Ring R phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Ring R phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "ring-r-2", "attachment": {"parentSocket": "ring-r-2-ring-3", "localStart": [-0.00241, -0.02002, 0.0], "localEnd": [-0.00396, -0.0328, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00756, "endRadius": 0.0062, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.015120000000000001, "height": 0.01288, "depth": 0.015120000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.0024133980989446413, -0.020015022098813923, 0.0], "rotation": [0, 0, 0], "scale": [0.015120000000000001, 0.01288, 0.015120000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "ring-r-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_ring_r_3_51.add(mesh_ring_r_3_51);
  meshes["ring-r-3"] = mesh_ring_r_3_51;
  colliders["ring-r-3"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["ring-r-3"] ??= [];
  destructionGroups["ring-r-3"].push(node_ring_r_3_51);

  const attachment_little_r_1_52 = {"parentSocket": "hand-r-little-1", "localStart": [-0.0196, -0.03763, 0.0028], "localEnd": [-0.02228, -0.05987, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_little_r_1_52 = makeAttachmentEndpoint(attachment_little_r_1_52);
  const node_little_r_1_52 = new THREE.Group();
  node_little_r_1_52.name = "Little R phalanx 1__pivot";
  node_little_r_1_52.scale.set(1, 1, 1);
  if (endpoint_little_r_1_52) {
    node_little_r_1_52.position.copy(endpoint_little_r_1_52.start);
    node_little_r_1_52.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_little_r_1_52.position.set(-0.019600000000000006, -0.037632, 0.0028000000000000004);
    node_little_r_1_52.rotation.set(0.0, 0.0, 0.0);
  }
  node_little_r_1_52.userData.sculptComponent = {"id": "little-r-1", "name": "Little R phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Little R phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-r", "attachment": {"parentSocket": "hand-r-little-1", "localStart": [-0.0196, -0.03763, 0.0028], "localEnd": [-0.02228, -0.05987, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.013440000000000002, "height": 0.022400000000000003, "depth": 0.013440000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.019600000000000006, -0.037632, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.013440000000000002, 0.022400000000000003, 0.013440000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-r-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_little_r_1_52.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-r-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["hand-r"] ?? root).add(node_little_r_1_52);
  nodes["little-r-1"] = node_little_r_1_52;
  const mesh_little_r_1_52Geometry = endpoint_little_r_1_52
    ? new THREE.CylinderGeometry(endpoint_little_r_1_52.endRadius, endpoint_little_r_1_52.baseRadius, endpoint_little_r_1_52.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_little_r_1_52) {
    mesh_little_r_1_52Geometry.scale(0.013440000000000002, 0.022400000000000003, 0.013440000000000002);
  }
  const mesh_little_r_1_52 = new THREE.SkinnedMesh(
    mesh_little_r_1_52Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_little_r_1_52.name = "Little R phalanx 1";
  if (endpoint_little_r_1_52) {
    mesh_little_r_1_52.position.copy(endpoint_little_r_1_52.midpoint);
    mesh_little_r_1_52.quaternion.copy(endpoint_little_r_1_52.quaternion);
  }
  mesh_little_r_1_52.castShadow = options.castShadow ?? true;
  mesh_little_r_1_52.receiveShadow = options.receiveShadow ?? true;
  mesh_little_r_1_52.userData.sculptComponent = {"id": "little-r-1", "name": "Little R phalanx 1", "level": "micro", "role": "finger", "importance": 0.3, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Little R phalanx 1 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "hand-r", "attachment": {"parentSocket": "hand-r-little-1", "localStart": [-0.0196, -0.03763, 0.0028], "localEnd": [-0.02228, -0.05987, 0.0028], "contactType": "rigid-weld", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.013440000000000002, "height": 0.022400000000000003, "depth": 0.013440000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.019600000000000006, -0.037632, 0.0028000000000000004], "rotation": [0, 0, 0], "scale": [0.013440000000000002, 0.022400000000000003, 0.013440000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-r-1", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_little_r_1_52.add(mesh_little_r_1_52);
  meshes["little-r-1"] = mesh_little_r_1_52;
  colliders["little-r-1"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["little-r-1"] ??= [];
  destructionGroups["little-r-1"].push(node_little_r_1_52);

  const attachment_little_r_2_53 = {"parentSocket": "little-r-1-little-2", "localStart": [-0.00268, -0.02224, 0.0], "localEnd": [-0.00463, -0.03836, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_little_r_2_53 = makeAttachmentEndpoint(attachment_little_r_2_53);
  const node_little_r_2_53 = new THREE.Group();
  node_little_r_2_53.name = "Little R phalanx 2__pivot";
  node_little_r_2_53.scale.set(1, 1, 1);
  if (endpoint_little_r_2_53) {
    node_little_r_2_53.position.copy(endpoint_little_r_2_53.start);
    node_little_r_2_53.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_little_r_2_53.position.set(-0.0026815534432718113, -0.022238913443126618, 0.0);
    node_little_r_2_53.rotation.set(0.0, 0.0, 0.0);
  }
  node_little_r_2_53.userData.sculptComponent = {"id": "little-r-2", "name": "Little R phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Little R phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "little-r-1", "attachment": {"parentSocket": "little-r-1-little-2", "localStart": [-0.00268, -0.02224, 0.0], "localEnd": [-0.00463, -0.03836, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.013440000000000002, "height": 0.01624, "depth": 0.013440000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.0026815534432718113, -0.022238913443126618, 0.0], "rotation": [0, 0, 0], "scale": [0.013440000000000002, 0.01624, 0.013440000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-r-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_little_r_2_53.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-r-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["little-r-1"] ?? root).add(node_little_r_2_53);
  nodes["little-r-2"] = node_little_r_2_53;
  const mesh_little_r_2_53Geometry = endpoint_little_r_2_53
    ? new THREE.CylinderGeometry(endpoint_little_r_2_53.endRadius, endpoint_little_r_2_53.baseRadius, endpoint_little_r_2_53.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_little_r_2_53) {
    mesh_little_r_2_53Geometry.scale(0.013440000000000002, 0.01624, 0.013440000000000002);
  }
  const mesh_little_r_2_53 = new THREE.SkinnedMesh(
    mesh_little_r_2_53Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_little_r_2_53.name = "Little R phalanx 2";
  if (endpoint_little_r_2_53) {
    mesh_little_r_2_53.position.copy(endpoint_little_r_2_53.midpoint);
    mesh_little_r_2_53.quaternion.copy(endpoint_little_r_2_53.quaternion);
  }
  mesh_little_r_2_53.castShadow = options.castShadow ?? true;
  mesh_little_r_2_53.receiveShadow = options.receiveShadow ?? true;
  mesh_little_r_2_53.userData.sculptComponent = {"id": "little-r-2", "name": "Little R phalanx 2", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Little R phalanx 2 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "little-r-1", "attachment": {"parentSocket": "little-r-1-little-2", "localStart": [-0.00268, -0.02224, 0.0], "localEnd": [-0.00463, -0.03836, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.013440000000000002, "height": 0.01624, "depth": 0.013440000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.0026815534432718113, -0.022238913443126618, 0.0], "rotation": [0, 0, 0], "scale": [0.013440000000000002, 0.01624, 0.013440000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-r-2", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_little_r_2_53.add(mesh_little_r_2_53);
  meshes["little-r-2"] = mesh_little_r_2_53;
  colliders["little-r-2"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["little-r-2"] ??= [];
  destructionGroups["little-r-2"].push(node_little_r_2_53);

  const attachment_little_r_3_54 = {"parentSocket": "little-r-2-little-3", "localStart": [-0.00194, -0.01612, 0.0], "localEnd": [-0.00322, -0.02669, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_little_r_3_54 = makeAttachmentEndpoint(attachment_little_r_3_54);
  const node_little_r_3_54 = new THREE.Group();
  node_little_r_3_54.name = "Little R phalanx 3__pivot";
  node_little_r_3_54.scale.set(1, 1, 1);
  if (endpoint_little_r_3_54) {
    node_little_r_3_54.position.copy(endpoint_little_r_3_54.start);
    node_little_r_3_54.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_little_r_3_54.position.set(-0.0019441262463720244, -0.01612321224626681, 0.0);
    node_little_r_3_54.rotation.set(0.0, 0.0, 0.0);
  }
  node_little_r_3_54.userData.sculptComponent = {"id": "little-r-3", "name": "Little R phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Little R phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "little-r-2", "attachment": {"parentSocket": "little-r-2-little-3", "localStart": [-0.00194, -0.01612, 0.0], "localEnd": [-0.00322, -0.02669, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.013440000000000002, "height": 0.01064, "depth": 0.013440000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.0019441262463720244, -0.01612321224626681, 0.0], "rotation": [0, 0, 0], "scale": [0.013440000000000002, 0.01064, 0.013440000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-r-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_little_r_3_54.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-r-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}};
  (nodes["little-r-2"] ?? root).add(node_little_r_3_54);
  nodes["little-r-3"] = node_little_r_3_54;
  const mesh_little_r_3_54Geometry = endpoint_little_r_3_54
    ? new THREE.CylinderGeometry(endpoint_little_r_3_54.endRadius, endpoint_little_r_3_54.baseRadius, endpoint_little_r_3_54.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_little_r_3_54) {
    mesh_little_r_3_54Geometry.scale(0.013440000000000002, 0.01064, 0.013440000000000002);
  }
  const mesh_little_r_3_54 = new THREE.SkinnedMesh(
    mesh_little_r_3_54Geometry,
    materialMap["skin"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_little_r_3_54.name = "Little R phalanx 3";
  if (endpoint_little_r_3_54) {
    mesh_little_r_3_54.position.copy(endpoint_little_r_3_54.midpoint);
    mesh_little_r_3_54.quaternion.copy(endpoint_little_r_3_54.quaternion);
  }
  mesh_little_r_3_54.castShadow = options.castShadow ?? true;
  mesh_little_r_3_54.receiveShadow = options.receiveShadow ?? true;
  mesh_little_r_3_54.userData.sculptComponent = {"id": "little-r-3", "name": "Little R phalanx 3", "level": "micro", "role": "finger", "importance": 0.2, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Little R phalanx 3 is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "little-r-2", "attachment": {"parentSocket": "little-r-2-little-3", "localStart": [-0.00194, -0.01612, 0.0], "localEnd": [-0.00322, -0.02669, 0.0], "contactType": "hinge-joint", "baseRadius": 0.00672, "endRadius": 0.00551, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.013440000000000002, "height": 0.01064, "depth": 0.013440000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.0019441262463720244, -0.01612321224626681, 0.0], "rotation": [0, 0, 0], "scale": [0.013440000000000002, 0.01064, 0.013440000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "little-r-3", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "skin"}}, "material": "skin", "materialLayers": ["skin"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_little_r_3_54.add(mesh_little_r_3_54);
  meshes["little-r-3"] = mesh_little_r_3_54;
  colliders["little-r-3"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["little-r-3"] ??= [];
  destructionGroups["little-r-3"].push(node_little_r_3_54);

  const attachment_thigh_l_55 = {"parentSocket": "pelvis-hip-l", "localStart": [0.1428, -0.1288, 0.0056], "localEnd": [0.1428, -0.66304, 0.0056], "contactType": "socket-joint", "baseRadius": 0.056, "endRadius": 0.0448, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_thigh_l_55 = makeAttachmentEndpoint(attachment_thigh_l_55);
  const node_thigh_l_55 = new THREE.Group();
  node_thigh_l_55.name = "Thigh L__pivot";
  node_thigh_l_55.scale.set(1, 1, 1);
  if (endpoint_thigh_l_55) {
    node_thigh_l_55.position.copy(endpoint_thigh_l_55.start);
    node_thigh_l_55.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_thigh_l_55.position.set(0.1428, -0.1288, 0.005600000000000001);
    node_thigh_l_55.rotation.set(0.0, 0.0, 0.0);
  }
  node_thigh_l_55.userData.sculptComponent = {"id": "thigh-l", "name": "Thigh L", "level": "meso", "role": "leg", "importance": 0.75, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Thigh L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "pelvis", "attachment": {"parentSocket": "pelvis-hip-l", "localStart": [0.1428, -0.1288, 0.0056], "localEnd": [0.1428, -0.66304, 0.0056], "contactType": "socket-joint", "baseRadius": 0.056, "endRadius": 0.0448, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.10640000000000001, "height": 0.5342400000000002, "depth": 0.10640000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.1428, -0.1288, 0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.10640000000000001, 0.5342400000000002, 0.10640000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thigh-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "pants"}}, "material": "pants", "materialLayers": ["pants"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_thigh_l_55.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thigh-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "pants"}};
  (nodes["pelvis"] ?? root).add(node_thigh_l_55);
  nodes["thigh-l"] = node_thigh_l_55;
  const mesh_thigh_l_55Geometry = endpoint_thigh_l_55
    ? new THREE.CylinderGeometry(endpoint_thigh_l_55.endRadius, endpoint_thigh_l_55.baseRadius, endpoint_thigh_l_55.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_thigh_l_55) {
    mesh_thigh_l_55Geometry.scale(0.10640000000000001, 0.5342400000000002, 0.10640000000000001);
  }
  const mesh_thigh_l_55 = new THREE.SkinnedMesh(
    mesh_thigh_l_55Geometry,
    materialMap["pants"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_thigh_l_55.name = "Thigh L";
  if (endpoint_thigh_l_55) {
    mesh_thigh_l_55.position.copy(endpoint_thigh_l_55.midpoint);
    mesh_thigh_l_55.quaternion.copy(endpoint_thigh_l_55.quaternion);
  }
  mesh_thigh_l_55.castShadow = options.castShadow ?? true;
  mesh_thigh_l_55.receiveShadow = options.receiveShadow ?? true;
  mesh_thigh_l_55.userData.sculptComponent = {"id": "thigh-l", "name": "Thigh L", "level": "meso", "role": "leg", "importance": 0.75, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Thigh L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "pelvis", "attachment": {"parentSocket": "pelvis-hip-l", "localStart": [0.1428, -0.1288, 0.0056], "localEnd": [0.1428, -0.66304, 0.0056], "contactType": "socket-joint", "baseRadius": 0.056, "endRadius": 0.0448, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.10640000000000001, "height": 0.5342400000000002, "depth": 0.10640000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.1428, -0.1288, 0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.10640000000000001, 0.5342400000000002, 0.10640000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thigh-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "pants"}}, "material": "pants", "materialLayers": ["pants"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_thigh_l_55.add(mesh_thigh_l_55);
  meshes["thigh-l"] = mesh_thigh_l_55;
  colliders["thigh-l"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["thigh-l"] ??= [];
  destructionGroups["thigh-l"].push(node_thigh_l_55);

  const attachment_shin_l_56 = {"parentSocket": "thigh-knee-l", "localStart": [0.0, -0.53424, 0.0], "localEnd": [0.0, -1.008, -0.0056], "contactType": "hinge-joint", "baseRadius": 0.0392, "endRadius": 0.028, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_shin_l_56 = makeAttachmentEndpoint(attachment_shin_l_56);
  const node_shin_l_56 = new THREE.Group();
  node_shin_l_56.name = "Shin L__pivot";
  node_shin_l_56.scale.set(1, 1, 1);
  if (endpoint_shin_l_56) {
    node_shin_l_56.position.copy(endpoint_shin_l_56.start);
    node_shin_l_56.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_shin_l_56.position.set(0.0, -0.5342400000000003, 0.0);
    node_shin_l_56.rotation.set(0.0, 0.0, 0.0);
  }
  node_shin_l_56.userData.sculptComponent = {"id": "shin-l", "name": "Shin L", "level": "meso", "role": "leg", "importance": 0.7, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Shin L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "thigh-l", "attachment": {"parentSocket": "thigh-knee-l", "localStart": [0.0, -0.53424, 0.0], "localEnd": [0.0, -1.008, -0.0056], "contactType": "hinge-joint", "baseRadius": 0.0392, "endRadius": 0.028, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.07840000000000001, "height": 0.47376000000000007, "depth": 0.07840000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, -0.5342400000000003, 0.0], "rotation": [0, 0, 0], "scale": [0.07840000000000001, 0.47376000000000007, 0.07840000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "shin-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "pants"}}, "material": "pants", "materialLayers": ["pants"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_shin_l_56.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "shin-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "pants"}};
  (nodes["thigh-l"] ?? root).add(node_shin_l_56);
  nodes["shin-l"] = node_shin_l_56;
  const mesh_shin_l_56Geometry = endpoint_shin_l_56
    ? new THREE.CylinderGeometry(endpoint_shin_l_56.endRadius, endpoint_shin_l_56.baseRadius, endpoint_shin_l_56.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_shin_l_56) {
    mesh_shin_l_56Geometry.scale(0.07840000000000001, 0.47376000000000007, 0.07840000000000001);
  }
  const mesh_shin_l_56 = new THREE.SkinnedMesh(
    mesh_shin_l_56Geometry,
    materialMap["pants"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_shin_l_56.name = "Shin L";
  if (endpoint_shin_l_56) {
    mesh_shin_l_56.position.copy(endpoint_shin_l_56.midpoint);
    mesh_shin_l_56.quaternion.copy(endpoint_shin_l_56.quaternion);
  }
  mesh_shin_l_56.castShadow = options.castShadow ?? true;
  mesh_shin_l_56.receiveShadow = options.receiveShadow ?? true;
  mesh_shin_l_56.userData.sculptComponent = {"id": "shin-l", "name": "Shin L", "level": "meso", "role": "leg", "importance": 0.7, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Shin L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "thigh-l", "attachment": {"parentSocket": "thigh-knee-l", "localStart": [0.0, -0.53424, 0.0], "localEnd": [0.0, -1.008, -0.0056], "contactType": "hinge-joint", "baseRadius": 0.0392, "endRadius": 0.028, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.07840000000000001, "height": 0.47376000000000007, "depth": 0.07840000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, -0.5342400000000003, 0.0], "rotation": [0, 0, 0], "scale": [0.07840000000000001, 0.47376000000000007, 0.07840000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "shin-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "pants"}}, "material": "pants", "materialLayers": ["pants"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_shin_l_56.add(mesh_shin_l_56);
  meshes["shin-l"] = mesh_shin_l_56;
  colliders["shin-l"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["shin-l"] ??= [];
  destructionGroups["shin-l"].push(node_shin_l_56);

  const endpoint_foot_l_57 = makeAttachmentEndpoint(null);
  const node_foot_l_57 = new THREE.Group();
  node_foot_l_57.name = "Foot L__pivot";
  node_foot_l_57.scale.set(1, 1, 1);
  if (endpoint_foot_l_57) {
    node_foot_l_57.position.copy(endpoint_foot_l_57.start);
    node_foot_l_57.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_foot_l_57.position.set(0.0, -0.4877600000000002, 0.039200000000000006);
    node_foot_l_57.rotation.set(0.0, 0.0, 0.0);
  }
  node_foot_l_57.userData.sculptComponent = {"id": "foot-l", "name": "Foot L", "level": "meso", "role": "foot", "importance": 0.5, "confidence": 0.8, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Foot L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "shin-l", "attachment": null, "dimensions": {"width": 0.06720000000000001, "height": 0.044800000000000006, "depth": 0.12320000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, -0.4877600000000002, 0.039200000000000006], "rotation": [0, 0, 0], "scale": [0.06720000000000001, 0.044800000000000006, 0.12320000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "foot-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shoes"}}, "material": "shoes", "materialLayers": ["shoes"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_foot_l_57.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "foot-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shoes"}};
  (nodes["shin-l"] ?? root).add(node_foot_l_57);
  nodes["foot-l"] = node_foot_l_57;
  const mesh_foot_l_57Geometry = endpoint_foot_l_57
    ? new THREE.CylinderGeometry(endpoint_foot_l_57.endRadius, endpoint_foot_l_57.baseRadius, endpoint_foot_l_57.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_foot_l_57) {
    mesh_foot_l_57Geometry.scale(0.06720000000000001, 0.044800000000000006, 0.12320000000000002);
  }
  const mesh_foot_l_57 = new THREE.SkinnedMesh(
    mesh_foot_l_57Geometry,
    materialMap["shoes"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_foot_l_57.name = "Foot L";
  if (endpoint_foot_l_57) {
    mesh_foot_l_57.position.copy(endpoint_foot_l_57.midpoint);
    mesh_foot_l_57.quaternion.copy(endpoint_foot_l_57.quaternion);
  }
  mesh_foot_l_57.castShadow = options.castShadow ?? true;
  mesh_foot_l_57.receiveShadow = options.receiveShadow ?? true;
  mesh_foot_l_57.userData.sculptComponent = {"id": "foot-l", "name": "Foot L", "level": "meso", "role": "foot", "importance": 0.5, "confidence": 0.8, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Foot L is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "shin-l", "attachment": null, "dimensions": {"width": 0.06720000000000001, "height": 0.044800000000000006, "depth": 0.12320000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, -0.4877600000000002, 0.039200000000000006], "rotation": [0, 0, 0], "scale": [0.06720000000000001, 0.044800000000000006, 0.12320000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "foot-l", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shoes"}}, "material": "shoes", "materialLayers": ["shoes"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_foot_l_57.add(mesh_foot_l_57);
  meshes["foot-l"] = mesh_foot_l_57;
  colliders["foot-l"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["foot-l"] ??= [];
  destructionGroups["foot-l"].push(node_foot_l_57);

  const attachment_thigh_r_58 = {"parentSocket": "pelvis-hip-r", "localStart": [-0.1428, -0.1288, 0.0056], "localEnd": [-0.1428, -0.66304, 0.0056], "contactType": "socket-joint", "baseRadius": 0.056, "endRadius": 0.0448, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_thigh_r_58 = makeAttachmentEndpoint(attachment_thigh_r_58);
  const node_thigh_r_58 = new THREE.Group();
  node_thigh_r_58.name = "Thigh R__pivot";
  node_thigh_r_58.scale.set(1, 1, 1);
  if (endpoint_thigh_r_58) {
    node_thigh_r_58.position.copy(endpoint_thigh_r_58.start);
    node_thigh_r_58.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_thigh_r_58.position.set(-0.1428, -0.1288, 0.005600000000000001);
    node_thigh_r_58.rotation.set(0.0, 0.0, 0.0);
  }
  node_thigh_r_58.userData.sculptComponent = {"id": "thigh-r", "name": "Thigh R", "level": "meso", "role": "leg", "importance": 0.75, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Thigh R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "pelvis", "attachment": {"parentSocket": "pelvis-hip-r", "localStart": [-0.1428, -0.1288, 0.0056], "localEnd": [-0.1428, -0.66304, 0.0056], "contactType": "socket-joint", "baseRadius": 0.056, "endRadius": 0.0448, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.10640000000000001, "height": 0.5342400000000002, "depth": 0.10640000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.1428, -0.1288, 0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.10640000000000001, 0.5342400000000002, 0.10640000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thigh-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "pants"}}, "material": "pants", "materialLayers": ["pants"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_thigh_r_58.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thigh-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "pants"}};
  (nodes["pelvis"] ?? root).add(node_thigh_r_58);
  nodes["thigh-r"] = node_thigh_r_58;
  const mesh_thigh_r_58Geometry = endpoint_thigh_r_58
    ? new THREE.CylinderGeometry(endpoint_thigh_r_58.endRadius, endpoint_thigh_r_58.baseRadius, endpoint_thigh_r_58.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_thigh_r_58) {
    mesh_thigh_r_58Geometry.scale(0.10640000000000001, 0.5342400000000002, 0.10640000000000001);
  }
  const mesh_thigh_r_58 = new THREE.SkinnedMesh(
    mesh_thigh_r_58Geometry,
    materialMap["pants"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_thigh_r_58.name = "Thigh R";
  if (endpoint_thigh_r_58) {
    mesh_thigh_r_58.position.copy(endpoint_thigh_r_58.midpoint);
    mesh_thigh_r_58.quaternion.copy(endpoint_thigh_r_58.quaternion);
  }
  mesh_thigh_r_58.castShadow = options.castShadow ?? true;
  mesh_thigh_r_58.receiveShadow = options.receiveShadow ?? true;
  mesh_thigh_r_58.userData.sculptComponent = {"id": "thigh-r", "name": "Thigh R", "level": "meso", "role": "leg", "importance": 0.75, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Thigh R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "pelvis", "attachment": {"parentSocket": "pelvis-hip-r", "localStart": [-0.1428, -0.1288, 0.0056], "localEnd": [-0.1428, -0.66304, 0.0056], "contactType": "socket-joint", "baseRadius": 0.056, "endRadius": 0.0448, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.10640000000000001, "height": 0.5342400000000002, "depth": 0.10640000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [-0.1428, -0.1288, 0.005600000000000001], "rotation": [0, 0, 0], "scale": [0.10640000000000001, 0.5342400000000002, 0.10640000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "thigh-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "pants"}}, "material": "pants", "materialLayers": ["pants"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_thigh_r_58.add(mesh_thigh_r_58);
  meshes["thigh-r"] = mesh_thigh_r_58;
  colliders["thigh-r"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["thigh-r"] ??= [];
  destructionGroups["thigh-r"].push(node_thigh_r_58);

  const attachment_shin_r_59 = {"parentSocket": "thigh-knee-r", "localStart": [0.0, -0.53424, 0.0], "localEnd": [0.0, -1.008, -0.0056], "contactType": "hinge-joint", "baseRadius": 0.0392, "endRadius": 0.028, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]};
  const endpoint_shin_r_59 = makeAttachmentEndpoint(attachment_shin_r_59);
  const node_shin_r_59 = new THREE.Group();
  node_shin_r_59.name = "Shin R__pivot";
  node_shin_r_59.scale.set(1, 1, 1);
  if (endpoint_shin_r_59) {
    node_shin_r_59.position.copy(endpoint_shin_r_59.start);
    node_shin_r_59.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_shin_r_59.position.set(0.0, -0.5342400000000003, 0.0);
    node_shin_r_59.rotation.set(0.0, 0.0, 0.0);
  }
  node_shin_r_59.userData.sculptComponent = {"id": "shin-r", "name": "Shin R", "level": "meso", "role": "leg", "importance": 0.7, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Shin R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "thigh-r", "attachment": {"parentSocket": "thigh-knee-r", "localStart": [0.0, -0.53424, 0.0], "localEnd": [0.0, -1.008, -0.0056], "contactType": "hinge-joint", "baseRadius": 0.0392, "endRadius": 0.028, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.07840000000000001, "height": 0.47376000000000007, "depth": 0.07840000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, -0.5342400000000003, 0.0], "rotation": [0, 0, 0], "scale": [0.07840000000000001, 0.47376000000000007, 0.07840000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "shin-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "pants"}}, "material": "pants", "materialLayers": ["pants"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_shin_r_59.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "shin-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "pants"}};
  (nodes["thigh-r"] ?? root).add(node_shin_r_59);
  nodes["shin-r"] = node_shin_r_59;
  const mesh_shin_r_59Geometry = endpoint_shin_r_59
    ? new THREE.CylinderGeometry(endpoint_shin_r_59.endRadius, endpoint_shin_r_59.baseRadius, endpoint_shin_r_59.length, 32, 12)
    : buildWatertightCapsule(0.35, 0.7, 16, 32, 1);
  if (!endpoint_shin_r_59) {
    mesh_shin_r_59Geometry.scale(0.07840000000000001, 0.47376000000000007, 0.07840000000000001);
  }
  const mesh_shin_r_59 = new THREE.SkinnedMesh(
    mesh_shin_r_59Geometry,
    materialMap["pants"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_shin_r_59.name = "Shin R";
  if (endpoint_shin_r_59) {
    mesh_shin_r_59.position.copy(endpoint_shin_r_59.midpoint);
    mesh_shin_r_59.quaternion.copy(endpoint_shin_r_59.quaternion);
  }
  mesh_shin_r_59.castShadow = options.castShadow ?? true;
  mesh_shin_r_59.receiveShadow = options.receiveShadow ?? true;
  mesh_shin_r_59.userData.sculptComponent = {"id": "shin-r", "name": "Shin R", "level": "meso", "role": "leg", "importance": 0.7, "confidence": 0.8, "primitive": "capsule", "topologyClass": "assembled-solid", "topologyRationale": "Shin R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "thigh-r", "attachment": {"parentSocket": "thigh-knee-r", "localStart": [0.0, -0.53424, 0.0], "localEnd": [0.0, -1.008, -0.0056], "contactType": "hinge-joint", "baseRadius": 0.0392, "endRadius": 0.028, "embedDepth": 0.03, "gapTolerance": 0.01, "evidenceRefs": ["full-object"]}, "dimensions": {"width": 0.07840000000000001, "height": 0.47376000000000007, "depth": 0.07840000000000001, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, -0.5342400000000003, 0.0], "rotation": [0, 0, 0], "scale": [0.07840000000000001, 0.47376000000000007, 0.07840000000000001]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "shin-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "pants"}}, "material": "pants", "materialLayers": ["pants"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_shin_r_59.add(mesh_shin_r_59);
  meshes["shin-r"] = mesh_shin_r_59;
  colliders["shin-r"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["shin-r"] ??= [];
  destructionGroups["shin-r"].push(node_shin_r_59);

  const endpoint_foot_r_60 = makeAttachmentEndpoint(null);
  const node_foot_r_60 = new THREE.Group();
  node_foot_r_60.name = "Foot R__pivot";
  node_foot_r_60.scale.set(1, 1, 1);
  if (endpoint_foot_r_60) {
    node_foot_r_60.position.copy(endpoint_foot_r_60.start);
    node_foot_r_60.rotation.set(0.0, 0.0, 0.0);
  } else {
    node_foot_r_60.position.set(0.0, -0.4877600000000002, 0.039200000000000006);
    node_foot_r_60.rotation.set(0.0, 0.0, 0.0);
  }
  node_foot_r_60.userData.sculptComponent = {"id": "foot-r", "name": "Foot R", "level": "meso", "role": "foot", "importance": 0.5, "confidence": 0.8, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Foot R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "shin-r", "attachment": null, "dimensions": {"width": 0.06720000000000001, "height": 0.044800000000000006, "depth": 0.12320000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, -0.4877600000000002, 0.039200000000000006], "rotation": [0, 0, 0], "scale": [0.06720000000000001, 0.044800000000000006, 0.12320000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "foot-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shoes"}}, "material": "shoes", "materialLayers": ["shoes"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_foot_r_60.userData.actionProfile = {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "foot-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shoes"}};
  (nodes["shin-r"] ?? root).add(node_foot_r_60);
  nodes["foot-r"] = node_foot_r_60;
  const mesh_foot_r_60Geometry = endpoint_foot_r_60
    ? new THREE.CylinderGeometry(endpoint_foot_r_60.endRadius, endpoint_foot_r_60.baseRadius, endpoint_foot_r_60.length, 32, 12)
    : new THREE.BoxGeometry(1, 1, 1, 12, 12, 12);
  if (!endpoint_foot_r_60) {
    mesh_foot_r_60Geometry.scale(0.06720000000000001, 0.044800000000000006, 0.12320000000000002);
  }
  const mesh_foot_r_60 = new THREE.SkinnedMesh(
    mesh_foot_r_60Geometry,
    materialMap["shoes"] ?? new THREE.MeshStandardMaterial({ color: 0x888888 })
  );
  mesh_foot_r_60.name = "Foot R";
  if (endpoint_foot_r_60) {
    mesh_foot_r_60.position.copy(endpoint_foot_r_60.midpoint);
    mesh_foot_r_60.quaternion.copy(endpoint_foot_r_60.quaternion);
  }
  mesh_foot_r_60.castShadow = options.castShadow ?? true;
  mesh_foot_r_60.receiveShadow = options.receiveShadow ?? true;
  mesh_foot_r_60.userData.sculptComponent = {"id": "foot-r", "name": "Foot R", "level": "meso", "role": "foot", "importance": 0.5, "confidence": 0.8, "primitive": "box", "topologyClass": "assembled-solid", "topologyRationale": "Foot R is a discrete primitive body part assembled onto the humanoid rig, not a continuous sculpt or shell.", "geometryDescriptor": {"topologyIntent": "stylized character part", "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1}, "deformationStack": [], "uvStrategy": "generated procedural coordinates", "normalStrategy": "smooth vertex normals"}, "parent": "shin-r", "attachment": null, "dimensions": {"width": 0.06720000000000001, "height": 0.044800000000000006, "depth": 0.12320000000000002, "units": "relative", "confidence": 0.8}, "transform": {"position": [0.0, -0.4877600000000002, 0.039200000000000006], "rotation": [0, 0, 0], "scale": [0.06720000000000001, 0.044800000000000006, 0.12320000000000002]}, "actionProfile": {"animationRole": "static", "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7}, "transformChannels": {"translate": true, "rotate": true, "scale": true, "bend": false, "twist": false, "detach": false, "visibility": true, "materialState": false}, "sockets": [], "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"}, "constraints": [], "destruction": {"breakable": false, "fractureGroup": "foot-r", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "shoes"}}, "material": "shoes", "materialLayers": ["shoes"], "deformations": [], "joints": [], "seams": [], "localFeatures": [], "surfaceDetail": {"macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0, "normalPattern": "", "displacementPattern": "", "occlusionPattern": "", "edgeWearPattern": "", "notes": ""}, "evidenceRefs": ["full-object"], "details": [], "fidelityTier": "blockout"};
  node_foot_r_60.add(mesh_foot_r_60);
  meshes["foot-r"] = mesh_foot_r_60;
  colliders["foot-r"] = {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": false, "notes": "box proxy"};
  destructionGroups["foot-r"] ??= [];
  destructionGroups["foot-r"].push(node_foot_r_60);

  // PLAN_1.5 WS-C slice 1: bone hierarchy from spec.rig. Model-space joints are
  // converted to parent-local offsets here. Nothing is bound yet (rig.bound === false).
  const bones: Record<string, THREE.Bone> = {};
  const boneOrder: string[] = [];
  const bone_pelvis = new THREE.Bone();
  bone_pelvis.name = "pelvis";
  bone_pelvis.position.set(0.0, -0.182, 0.0);
  root.add(bone_pelvis);
  bones["pelvis"] = bone_pelvis;
  boneOrder.push("pelvis");
  const bone_abdomen = new THREE.Bone();
  bone_abdomen.name = "abdomen";
  bone_abdomen.position.set(0.0, 0.027999999999999997, 0.0);
  bone_pelvis.add(bone_abdomen);
  bones["abdomen"] = bone_abdomen;
  boneOrder.push("abdomen");
  const bone_chest = new THREE.Bone();
  bone_chest.name = "chest";
  bone_chest.position.set(0.0, 0.25872, 0.0028);
  bone_abdomen.add(bone_chest);
  bones["chest"] = bone_chest;
  boneOrder.push("chest");
  const bone_clavicle_l = new THREE.Bone();
  bone_clavicle_l.name = "clavicle-l";
  bone_clavicle_l.position.set(0.05152, 0.34048, 0.005599999999999999);
  bone_chest.add(bone_clavicle_l);
  bones["clavicle-l"] = bone_clavicle_l;
  boneOrder.push("clavicle-l");
  const bone_clavicle_r = new THREE.Bone();
  bone_clavicle_r.name = "clavicle-r";
  bone_clavicle_r.position.set(-0.05152, 0.34048, 0.005599999999999999);
  bone_chest.add(bone_clavicle_r);
  bones["clavicle-r"] = bone_clavicle_r;
  boneOrder.push("clavicle-r");
  const bone_thigh_l = new THREE.Bone();
  bone_thigh_l.name = "thigh-l";
  bone_thigh_l.position.set(0.1428, -0.12880000000000003, 0.0056);
  bone_pelvis.add(bone_thigh_l);
  bones["thigh-l"] = bone_thigh_l;
  boneOrder.push("thigh-l");
  const bone_shin_l = new THREE.Bone();
  bone_shin_l.name = "shin-l";
  bone_shin_l.position.set(0.0, -0.53424, 0.0);
  bone_thigh_l.add(bone_shin_l);
  bones["shin-l"] = bone_shin_l;
  boneOrder.push("shin-l");
  const bone_foot_l = new THREE.Bone();
  bone_foot_l.name = "foot-l";
  bone_foot_l.position.set(0.0, -0.48775999999999997, 0.0392);
  bone_shin_l.add(bone_foot_l);
  bones["foot-l"] = bone_foot_l;
  boneOrder.push("foot-l");
  const bone_thigh_r = new THREE.Bone();
  bone_thigh_r.name = "thigh-r";
  bone_thigh_r.position.set(-0.1428, -0.12880000000000003, 0.0056);
  bone_pelvis.add(bone_thigh_r);
  bones["thigh-r"] = bone_thigh_r;
  boneOrder.push("thigh-r");
  const bone_shin_r = new THREE.Bone();
  bone_shin_r.name = "shin-r";
  bone_shin_r.position.set(0.0, -0.53424, 0.0);
  bone_thigh_r.add(bone_shin_r);
  bones["shin-r"] = bone_shin_r;
  boneOrder.push("shin-r");
  const bone_foot_r = new THREE.Bone();
  bone_foot_r.name = "foot-r";
  bone_foot_r.position.set(0.0, -0.48775999999999997, 0.0392);
  bone_shin_r.add(bone_foot_r);
  bones["foot-r"] = bone_foot_r;
  boneOrder.push("foot-r");
  const bone_upper_arm_l = new THREE.Bone();
  bone_upper_arm_l.name = "upper-arm-l";
  bone_upper_arm_l.position.set(0.27048, -0.005599999999999994, 0.005600000000000001);
  bone_clavicle_l.add(bone_upper_arm_l);
  bones["upper-arm-l"] = bone_upper_arm_l;
  boneOrder.push("upper-arm-l");
  const bone_forearm_l = new THREE.Bone();
  bone_forearm_l.name = "forearm-l";
  bone_forearm_l.position.set(0.06394, -0.31544, 0.0);
  bone_upper_arm_l.add(bone_forearm_l);
  bones["forearm-l"] = bone_forearm_l;
  boneOrder.push("forearm-l");
  const bone_upper_arm_r = new THREE.Bone();
  bone_upper_arm_r.name = "upper-arm-r";
  bone_upper_arm_r.position.set(-0.27048, -0.005599999999999994, 0.005600000000000001);
  bone_clavicle_r.add(bone_upper_arm_r);
  bones["upper-arm-r"] = bone_upper_arm_r;
  boneOrder.push("upper-arm-r");
  const bone_forearm_r = new THREE.Bone();
  bone_forearm_r.name = "forearm-r";
  bone_forearm_r.position.set(-0.06394, -0.31544, 0.0);
  bone_upper_arm_r.add(bone_forearm_r);
  bones["forearm-r"] = bone_forearm_r;
  boneOrder.push("forearm-r");
  const bone_hand_l = new THREE.Bone();
  bone_hand_l.name = "hand-l";
  bone_hand_l.position.set(0.03688999999999998, -0.30593, 0.0);
  bone_forearm_l.add(bone_hand_l);
  bones["hand-l"] = bone_hand_l;
  boneOrder.push("hand-l");
  const bone_hand_r = new THREE.Bone();
  bone_hand_r.name = "hand-r";
  bone_hand_r.position.set(-0.03688999999999998, -0.30593, 0.0);
  bone_forearm_r.add(bone_hand_r);
  bones["hand-r"] = bone_hand_r;
  boneOrder.push("hand-r");
  const bone_neck = new THREE.Bone();
  bone_neck.name = "neck";
  bone_neck.position.set(0.0, 0.33488, 0.0028);
  bone_chest.add(bone_neck);
  bones["neck"] = bone_neck;
  boneOrder.push("neck");
  const bone_head = new THREE.Bone();
  bone_head.name = "head";
  bone_head.position.set(0.0, 0.238, 0.0);
  bone_neck.add(bone_head);
  bones["head"] = bone_head;
  boneOrder.push("head");
  const bone_index_l_1 = new THREE.Bone();
  bone_index_l_1.name = "index-l-1";
  bone_index_l_1.position.set(-0.020999999999999963, -0.037630000000000025, 0.0027999999999999987);
  bone_hand_l.add(bone_index_l_1);
  bones["index-l-1"] = bone_index_l_1;
  boneOrder.push("index-l-1");
  const bone_index_l_2 = new THREE.Bone();
  bone_index_l_2.name = "index-l-2";
  bone_index_l_2.position.set(0.0035199999999999676, -0.029189999999999994, 0.0);
  bone_index_l_1.add(bone_index_l_2);
  bones["index-l-2"] = bone_index_l_2;
  boneOrder.push("index-l-2");
  const bone_index_l_3 = new THREE.Bone();
  bone_index_l_3.name = "index-l-3";
  bone_index_l_3.position.set(0.0024100000000000232, -0.02001, 0.0);
  bone_index_l_2.add(bone_index_l_3);
  bones["index-l-3"] = bone_index_l_3;
  boneOrder.push("index-l-3");
  const bone_index_r_1 = new THREE.Bone();
  bone_index_r_1.name = "index-r-1";
  bone_index_r_1.position.set(0.020999999999999963, -0.037630000000000025, 0.0027999999999999987);
  bone_hand_r.add(bone_index_r_1);
  bones["index-r-1"] = bone_index_r_1;
  boneOrder.push("index-r-1");
  const bone_index_r_2 = new THREE.Bone();
  bone_index_r_2.name = "index-r-2";
  bone_index_r_2.position.set(-0.0035199999999999676, -0.029189999999999994, 0.0);
  bone_index_r_1.add(bone_index_r_2);
  bones["index-r-2"] = bone_index_r_2;
  boneOrder.push("index-r-2");
  const bone_index_r_3 = new THREE.Bone();
  bone_index_r_3.name = "index-r-3";
  bone_index_r_3.position.set(-0.0024100000000000232, -0.02001, 0.0);
  bone_index_r_2.add(bone_index_r_3);
  bones["index-r-3"] = bone_index_r_3;
  boneOrder.push("index-r-3");
  const bone_little_l_1 = new THREE.Bone();
  bone_little_l_1.name = "little-l-1";
  bone_little_l_1.position.set(0.019600000000000006, -0.037630000000000025, 0.0027999999999999987);
  bone_hand_l.add(bone_little_l_1);
  bones["little-l-1"] = bone_little_l_1;
  boneOrder.push("little-l-1");
  const bone_little_l_2 = new THREE.Bone();
  bone_little_l_2.name = "little-l-2";
  bone_little_l_2.position.set(0.0026800000000000157, -0.022239999999999982, 0.0);
  bone_little_l_1.add(bone_little_l_2);
  bones["little-l-2"] = bone_little_l_2;
  boneOrder.push("little-l-2");
  const bone_little_l_3 = new THREE.Bone();
  bone_little_l_3.name = "little-l-3";
  bone_little_l_3.position.set(0.0019500000000000073, -0.016119999999999995, 0.0);
  bone_little_l_2.add(bone_little_l_3);
  bones["little-l-3"] = bone_little_l_3;
  boneOrder.push("little-l-3");
  const bone_little_r_1 = new THREE.Bone();
  bone_little_r_1.name = "little-r-1";
  bone_little_r_1.position.set(-0.019600000000000006, -0.037630000000000025, 0.0027999999999999987);
  bone_hand_r.add(bone_little_r_1);
  bones["little-r-1"] = bone_little_r_1;
  boneOrder.push("little-r-1");
  const bone_little_r_2 = new THREE.Bone();
  bone_little_r_2.name = "little-r-2";
  bone_little_r_2.position.set(-0.0026800000000000157, -0.022239999999999982, 0.0);
  bone_little_r_1.add(bone_little_r_2);
  bones["little-r-2"] = bone_little_r_2;
  boneOrder.push("little-r-2");
  const bone_little_r_3 = new THREE.Bone();
  bone_little_r_3.name = "little-r-3";
  bone_little_r_3.position.set(-0.0019500000000000073, -0.016119999999999995, 0.0);
  bone_little_r_2.add(bone_little_r_3);
  bones["little-r-3"] = bone_little_r_3;
  boneOrder.push("little-r-3");
  const bone_middle_l_1 = new THREE.Bone();
  bone_middle_l_1.name = "middle-l-1";
  bone_middle_l_1.position.set(-0.007000000000000006, -0.037630000000000025, 0.0027999999999999987);
  bone_hand_l.add(bone_middle_l_1);
  bones["middle-l-1"] = bone_middle_l_1;
  boneOrder.push("middle-l-1");
  const bone_middle_l_2 = new THREE.Bone();
  bone_middle_l_2.name = "middle-l-2";
  bone_middle_l_2.position.set(0.00386000000000003, -0.03196999999999997, 0.0);
  bone_middle_l_1.add(bone_middle_l_2);
  bones["middle-l-2"] = bone_middle_l_2;
  boneOrder.push("middle-l-2");
  const bone_middle_l_3 = new THREE.Bone();
  bone_middle_l_3.name = "middle-l-3";
  bone_middle_l_3.position.set(0.0026800000000000157, -0.022240000000000038, 0.0);
  bone_middle_l_2.add(bone_middle_l_3);
  bones["middle-l-3"] = bone_middle_l_3;
  boneOrder.push("middle-l-3");
  const bone_middle_r_1 = new THREE.Bone();
  bone_middle_r_1.name = "middle-r-1";
  bone_middle_r_1.position.set(0.007000000000000006, -0.037630000000000025, 0.0027999999999999987);
  bone_hand_r.add(bone_middle_r_1);
  bones["middle-r-1"] = bone_middle_r_1;
  boneOrder.push("middle-r-1");
  const bone_middle_r_2 = new THREE.Bone();
  bone_middle_r_2.name = "middle-r-2";
  bone_middle_r_2.position.set(-0.00386000000000003, -0.03196999999999997, 0.0);
  bone_middle_r_1.add(bone_middle_r_2);
  bones["middle-r-2"] = bone_middle_r_2;
  boneOrder.push("middle-r-2");
  const bone_middle_r_3 = new THREE.Bone();
  bone_middle_r_3.name = "middle-r-3";
  bone_middle_r_3.position.set(-0.0026800000000000157, -0.022240000000000038, 0.0);
  bone_middle_r_2.add(bone_middle_r_3);
  bones["middle-r-3"] = bone_middle_r_3;
  boneOrder.push("middle-r-3");
  const bone_ring_l_1 = new THREE.Bone();
  bone_ring_l_1.name = "ring-l-1";
  bone_ring_l_1.position.set(0.007000000000000006, -0.037630000000000025, 0.0027999999999999987);
  bone_hand_l.add(bone_ring_l_1);
  bones["ring-l-1"] = bone_ring_l_1;
  boneOrder.push("ring-l-1");
  const bone_ring_l_2 = new THREE.Bone();
  bone_ring_l_2.name = "ring-l-2";
  bone_ring_l_2.position.set(0.003520000000000023, -0.029189999999999994, 0.0);
  bone_ring_l_1.add(bone_ring_l_2);
  bones["ring-l-2"] = bone_ring_l_2;
  boneOrder.push("ring-l-2");
  const bone_ring_l_3 = new THREE.Bone();
  bone_ring_l_3.name = "ring-l-3";
  bone_ring_l_3.position.set(0.0024099999999999677, -0.02001, 0.0);
  bone_ring_l_2.add(bone_ring_l_3);
  bones["ring-l-3"] = bone_ring_l_3;
  boneOrder.push("ring-l-3");
  const bone_ring_r_1 = new THREE.Bone();
  bone_ring_r_1.name = "ring-r-1";
  bone_ring_r_1.position.set(-0.007000000000000006, -0.037630000000000025, 0.0027999999999999987);
  bone_hand_r.add(bone_ring_r_1);
  bones["ring-r-1"] = bone_ring_r_1;
  boneOrder.push("ring-r-1");
  const bone_ring_r_2 = new THREE.Bone();
  bone_ring_r_2.name = "ring-r-2";
  bone_ring_r_2.position.set(-0.003520000000000023, -0.029189999999999994, 0.0);
  bone_ring_r_1.add(bone_ring_r_2);
  bones["ring-r-2"] = bone_ring_r_2;
  boneOrder.push("ring-r-2");
  const bone_ring_r_3 = new THREE.Bone();
  bone_ring_r_3.name = "ring-r-3";
  bone_ring_r_3.position.set(-0.0024099999999999677, -0.02001, 0.0);
  bone_ring_r_2.add(bone_ring_r_3);
  bones["ring-r-3"] = bone_ring_r_3;
  boneOrder.push("ring-r-3");
  const bone_thumb_l_1 = new THREE.Bone();
  bone_thumb_l_1.name = "thumb-l-1";
  bone_thumb_l_1.position.set(-0.02799999999999997, -0.005370000000000014, 0.005599999999999999);
  bone_hand_l.add(bone_thumb_l_1);
  bones["thumb-l-1"] = bone_thumb_l_1;
  boneOrder.push("thumb-l-1");
  const bone_thumb_l_2 = new THREE.Bone();
  bone_thumb_l_2.name = "thumb-l-2";
  bone_thumb_l_2.position.set(-0.015120000000000022, -0.013020000000000004, 0.0063);
  bone_thumb_l_1.add(bone_thumb_l_2);
  bones["thumb-l-2"] = bone_thumb_l_2;
  boneOrder.push("thumb-l-2");
  const bone_thumb_l_3 = new THREE.Bone();
  bone_thumb_l_3.name = "thumb-l-3";
  bone_thumb_l_3.position.set(-0.011089999999999989, -0.009550000000000003, 0.004619999999999999);
  bone_thumb_l_2.add(bone_thumb_l_3);
  bones["thumb-l-3"] = bone_thumb_l_3;
  boneOrder.push("thumb-l-3");
  const bone_thumb_r_1 = new THREE.Bone();
  bone_thumb_r_1.name = "thumb-r-1";
  bone_thumb_r_1.position.set(0.02799999999999997, -0.005370000000000014, 0.005599999999999999);
  bone_hand_r.add(bone_thumb_r_1);
  bones["thumb-r-1"] = bone_thumb_r_1;
  boneOrder.push("thumb-r-1");
  const bone_thumb_r_2 = new THREE.Bone();
  bone_thumb_r_2.name = "thumb-r-2";
  bone_thumb_r_2.position.set(0.015120000000000022, -0.013020000000000004, 0.0063);
  bone_thumb_r_1.add(bone_thumb_r_2);
  bones["thumb-r-2"] = bone_thumb_r_2;
  boneOrder.push("thumb-r-2");
  const bone_thumb_r_3 = new THREE.Bone();
  bone_thumb_r_3.name = "thumb-r-3";
  bone_thumb_r_3.position.set(0.011089999999999989, -0.009550000000000003, 0.004619999999999999);
  bone_thumb_r_2.add(bone_thumb_r_3);
  bones["thumb-r-3"] = bone_thumb_r_3;
  boneOrder.push("thumb-r-3");
  // The bones are now in REST position. updateMatrixWorld() before constructing the
  // Skeleton is load-bearing: calculateInverses() reads each bone's CURRENT world matrix,
  // and those inverses are what cancel the rest pose during skinning. Constructed before
  // this call it captures identity matrices, the rest pose never cancels, and every
  // vertex is displaced by its bone's offset at rest. Measured, not assumed --
  // scratchpad/bind_experiment.mjs read (0, 3, 0) for a vertex authored at (0, 2, 0).
  root.updateMatrixWorld(true);
  const skeleton = new THREE.Skeleton(boneOrder.map((id) => bones[id]));
  const boneIndexOf = new Map<string, number>(boneOrder.map((id, i) => [id, i]));

  // ---- PLAN_1.5 §4 weight function: ONE function over the complete bone set. No
  // mesh-id or vertex-index branching -- only positions, segment endpoints and the
  // envelope radius derived per §4.3. Ported from forge/stage5_rig/emit_rig.py, which
  // measured max |sum(w) - 1| = 2.98e-8 on executed geometry.
  const BONE_JOINT: Record<string, number[]> = {"pelvis": [0.0, -0.182, 0.0], "abdomen": [0.0, -0.154, 0.0], "chest": [0.0, 0.10472, 0.0028], "clavicle-l": [0.05152, 0.4452, 0.0084], "clavicle-r": [-0.05152, 0.4452, 0.0084], "thigh-l": [0.1428, -0.3108, 0.0056], "shin-l": [0.1428, -0.84504, 0.0056], "foot-l": [0.1428, -1.3328, 0.0448], "thigh-r": [-0.1428, -0.3108, 0.0056], "shin-r": [-0.1428, -0.84504, 0.0056], "foot-r": [-0.1428, -1.3328, 0.0448], "upper-arm-l": [0.322, 0.4396, 0.014], "forearm-l": [0.38594, 0.12416, 0.014], "upper-arm-r": [-0.322, 0.4396, 0.014], "forearm-r": [-0.38594, 0.12416, 0.014], "hand-l": [0.42283, -0.18177, 0.014], "hand-r": [-0.42283, -0.18177, 0.014], "neck": [0.0, 0.4396, 0.0056], "head": [0.0, 0.6776, 0.0056], "index-l-1": [0.40183, -0.2194, 0.0168], "index-l-2": [0.40535, -0.24859, 0.0168], "index-l-3": [0.40776, -0.2686, 0.0168], "index-r-1": [-0.40183, -0.2194, 0.0168], "index-r-2": [-0.40535, -0.24859, 0.0168], "index-r-3": [-0.40776, -0.2686, 0.0168], "little-l-1": [0.44243, -0.2194, 0.0168], "little-l-2": [0.44511, -0.24164, 0.0168], "little-l-3": [0.44706, -0.25776, 0.0168], "little-r-1": [-0.44243, -0.2194, 0.0168], "little-r-2": [-0.44511, -0.24164, 0.0168], "little-r-3": [-0.44706, -0.25776, 0.0168], "middle-l-1": [0.41583, -0.2194, 0.0168], "middle-l-2": [0.41969, -0.25137, 0.0168], "middle-l-3": [0.42237, -0.27361, 0.0168], "middle-r-1": [-0.41583, -0.2194, 0.0168], "middle-r-2": [-0.41969, -0.25137, 0.0168], "middle-r-3": [-0.42237, -0.27361, 0.0168], "ring-l-1": [0.42983, -0.2194, 0.0168], "ring-l-2": [0.43335, -0.24859, 0.0168], "ring-l-3": [0.43576, -0.2686, 0.0168], "ring-r-1": [-0.42983, -0.2194, 0.0168], "ring-r-2": [-0.43335, -0.24859, 0.0168], "ring-r-3": [-0.43576, -0.2686, 0.0168], "thumb-l-1": [0.39483, -0.18714, 0.0196], "thumb-l-2": [0.37971, -0.20016, 0.0259], "thumb-l-3": [0.36862, -0.20971, 0.03052], "thumb-r-1": [-0.39483, -0.18714, 0.0196], "thumb-r-2": [-0.37971, -0.20016, 0.0259], "thumb-r-3": [-0.36862, -0.20971, 0.03052]};
  const BONE_TIP: Record<string, number[]> = {"pelvis": [0.0, -0.154, 0.0], "abdomen": [0.0, 0.10472, 0.0028], "chest": [0.0, 0.4396, 0.0056], "clavicle-l": [0.322, 0.4396, 0.014], "clavicle-r": [-0.322, 0.4396, 0.014], "thigh-l": [0.1428, -0.84504, 0.0056], "shin-l": [0.1428, -1.3328, 0.0448], "foot-l": [0.1428, -1.37746, 0.04839], "thigh-r": [-0.1428, -0.84504, 0.0056], "shin-r": [-0.1428, -1.3328, 0.0448], "foot-r": [-0.1428, -1.37746, 0.04839], "upper-arm-l": [0.38594, 0.12416, 0.014], "forearm-l": [0.42283, -0.18177, 0.014], "upper-arm-r": [-0.38594, 0.12416, 0.014], "forearm-r": [-0.42283, -0.18177, 0.014], "hand-l": [0.41583, -0.2194, 0.0168], "hand-r": [-0.41583, -0.2194, 0.0168], "neck": [0.0, 0.6776, 0.0056], "head": [0.0, 0.9912, 0.0056], "index-l-1": [0.40535, -0.24859, 0.0168], "index-l-2": [0.40776, -0.2686, 0.0168], "index-l-3": [0.40937, -0.28195, 0.0168], "index-r-1": [-0.40535, -0.24859, 0.0168], "index-r-2": [-0.40776, -0.2686, 0.0168], "index-r-3": [-0.40937, -0.28195, 0.0168], "little-l-1": [0.44511, -0.24164, 0.0168], "little-l-2": [0.44706, -0.25776, 0.0168], "little-l-3": [0.44833, -0.26833, 0.0168], "little-r-1": [-0.44511, -0.24164, 0.0168], "little-r-2": [-0.44706, -0.25776, 0.0168], "little-r-3": [-0.44833, -0.26833, 0.0168], "middle-l-1": [0.41969, -0.25137, 0.0168], "middle-l-2": [0.42237, -0.27361, 0.0168], "middle-l-3": [0.42404, -0.28751, 0.0168], "middle-r-1": [-0.41969, -0.25137, 0.0168], "middle-r-2": [-0.42237, -0.27361, 0.0168], "middle-r-3": [-0.42404, -0.28751, 0.0168], "ring-l-1": [0.43335, -0.24859, 0.0168], "ring-l-2": [0.43576, -0.2686, 0.0168], "ring-l-3": [0.43731, -0.28139, 0.0168], "ring-r-1": [-0.43335, -0.24859, 0.0168], "ring-r-2": [-0.43576, -0.2686, 0.0168], "ring-r-3": [-0.43731, -0.28139, 0.0168], "thumb-l-1": [0.37971, -0.20016, 0.0259], "thumb-l-2": [0.36862, -0.20971, 0.03052], "thumb-l-3": [0.36053, -0.21668, 0.03389], "thumb-r-1": [-0.37971, -0.20016, 0.0259], "thumb-r-2": [-0.36862, -0.20971, 0.03052], "thumb-r-3": [-0.36053, -0.21668, 0.03389]};
  const BONE_ENVELOPE: Record<string, number> = {"pelvis": 0.27132, "abdomen": 0.25704, "chest": 0.36708, "clavicle-l": 0.162288, "clavicle-r": 0.162288, "thigh-l": 0.06384, "shin-l": 0.04704, "foot-l": 0.07392, "thigh-r": 0.06384, "shin-r": 0.04704, "foot-r": 0.07392, "upper-arm-l": 0.05376, "forearm-l": 0.04368, "upper-arm-r": 0.05376, "forearm-r": 0.04368, "hand-l": 0.03696, "hand-r": 0.03696, "neck": 0.0924, "head": 0.16464, "index-l-1": 0.009408, "index-l-2": 0.009408, "index-l-3": 0.009408, "index-r-1": 0.009408, "index-r-2": 0.009408, "index-r-3": 0.009408, "little-l-1": 0.008064, "little-l-2": 0.008064, "little-l-3": 0.008064, "little-r-1": 0.008064, "little-r-2": 0.008064, "little-r-3": 0.008064, "middle-l-1": 0.009744, "middle-l-2": 0.009744, "middle-l-3": 0.009744, "middle-r-1": 0.009744, "middle-r-2": 0.009744, "middle-r-3": 0.009744, "ring-l-1": 0.009072, "ring-l-2": 0.009072, "ring-l-3": 0.009072, "ring-r-1": 0.009072, "ring-r-2": 0.009072, "ring-r-3": 0.009072, "thumb-l-1": 0.010752, "thumb-l-2": 0.010752, "thumb-l-3": 0.010752, "thumb-r-1": 0.010752, "thumb-r-2": 0.010752, "thumb-r-3": 0.010752};
  const _closest = new THREE.Vector3();
  const distanceToSegment = (p: THREE.Vector3, s: number[], e: number[]): number => {
    const ab = [e[0] - s[0], e[1] - s[1], e[2] - s[2]];
    const ap = [p.x - s[0], p.y - s[1], p.z - s[2]];
    const abLenSq = ab[0] * ab[0] + ab[1] * ab[1] + ab[2] * ab[2];
    const t = abLenSq > 1e-12
      ? THREE.MathUtils.clamp((ap[0] * ab[0] + ap[1] * ab[1] + ap[2] * ab[2]) / abLenSq, 0, 1)
      : 0;
    _closest.set(s[0] + ab[0] * t, s[1] + ab[1] * t, s[2] + ab[2] * t);
    return p.distanceTo(_closest);
  };
  const computeVertexWeights = (p: THREE.Vector3) => {
    const scored = boneOrder.map((id) => {
      const d = distanceToSegment(p, BONE_JOINT[id], BONE_TIP[id]);
      const u = d / BONE_ENVELOPE[id];
      const falloff = Math.max(0, 1 - u * u);
      return { id, d, w: falloff * falloff };
    });
    scored.sort((a, b) => b.w - a.w);
    const kept = scored.slice(0, 4);
    const total = kept.reduce((sum, c) => sum + c.w, 0);
    const indices = [0, 0, 0, 0];
    const weights = [0, 0, 0, 0];
    if (total > 0) {
      for (let slot = 0; slot < kept.length; slot++) {
        indices[slot] = boneIndexOf.get(kept[slot].id) ?? 0;
        weights[slot] = kept[slot].w / total;
      }
      return { indices, weights, fallback: false };
    }
    // Mandatory zero-sum fallback (PLAN_1.5 §4 / ADR-8). Without it three.js's own
    // normalizeSkinWeights() rewrites an all-zero vertex to (1,0,0,0) against bone 0
    // regardless of distance, which spikes stray vertices toward the hips. Instead:
    // ignore the envelope and pin weight 1.0 to the absolutely nearest bone.
    let nearest = boneOrder[0];
    let nearestDistance = Infinity;
    for (const id of boneOrder) {
      const d = distanceToSegment(p, BONE_JOINT[id], BONE_TIP[id]);
      if (d < nearestDistance) { nearestDistance = d; nearest = id; }
    }
    indices[0] = boneIndexOf.get(nearest) ?? 0;
    weights[0] = 1;
    return { indices, weights, fallback: true };
  };

  // ---- Bake to model space, weight, and bind.
  //
  // The arrangement below was chosen by measurement, not derivation, because the same
  // geometry can be skinned four plausible ways and three of them are wrong. With a
  // vertex authored at model-space (0, 2, 0) fully weighted to a bone at (0, 1, 0) and
  // that bone rotated +90 degrees about X (correct answer: (0, 1, 1)):
  //
  //   pivot transform kept, bind identity     -> rest pose already wrong, no deformation
  //   pivot transform kept, bind matrixWorld  -> (0, 1.5, 0.5): HALF the correct swing,
  //                                              because the pivot applies on top of skinning
  //   geometry baked, pivot bypassed          -> (0, 1, 1): correct
  //   no pivot at all                         -> (0, 1, 1): correct, and identical
  //
  // The last two agreeing is the finding: what matters is that the mesh's own world
  // transform is identity and its geometry lives in the skeleton's space. So each skinned
  // mesh gets its world matrix folded into its vertex data and is reparented to `root`
  // with an identity transform. Meshes are leaves -- components are added to their pivot
  // Group, never to another mesh -- so reparenting one moves nothing else.
  // No component carries an authored pose, so there is nothing to rest.
  root.updateMatrixWorld(true);
  const skinnedMeshNames: string[] = [];
  let boundCount = 0;
  for (const boneId of boneOrder) {
    const mesh = meshes[boneId];
    if (!mesh) continue;
    const position = mesh.geometry.getAttribute('position');
    if (!position) continue;
    mesh.updateWorldMatrix(true, false);
    // applyMatrix4 mutates the vertex buffer in place and is NOT idempotent: running it
    // twice on one geometry applies the world matrix squared, and every component lands
    // somewhere it has no reason to be -- the model reads as blown apart rather than
    // wrong. Throw rather than skip, because a silent skip would leave a mesh in the
    // wrong space and the failure would resurface later as a subtler misplacement.
    if (mesh.geometry.userData.worldBaked) {
      throw new Error(
        `geometry for '${boneId}' is already world-baked; baking twice squares the ` +
        'world matrix and scatters the parts. Build a fresh factory instead of re-binding.'
      );
    }
    mesh.geometry.applyMatrix4(mesh.matrixWorld);
    mesh.geometry.userData.worldBaked = true;
    root.add(mesh);
    mesh.position.set(0, 0, 0);
    mesh.quaternion.identity();
    mesh.scale.set(1, 1, 1);
    mesh.updateMatrixWorld(true);
    // Vertices are model-space now, which is the space the weight function measures in,
    // so no per-vertex matrix multiply is needed any more.
    const count = position.count;
    const skinIndices = new Uint16Array(count * 4);
    const skinWeights = new Float32Array(count * 4);
    const vertex = new THREE.Vector3();
    for (let v = 0; v < count; v++) {
      vertex.fromBufferAttribute(position, v);
      const { indices, weights } = computeVertexWeights(vertex);
      for (let slot = 0; slot < 4; slot++) {
        skinIndices[v * 4 + slot] = indices[slot];
        skinWeights[v * 4 + slot] = weights[slot];
      }
    }
    mesh.geometry.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(skinIndices, 4));
    mesh.geometry.setAttribute('skinWeight', new THREE.Float32BufferAttribute(skinWeights, 4));
    skinnedMeshNames.push(boneId);
    const skinned = mesh as THREE.SkinnedMesh;
    if (!skinned.isSkinnedMesh) continue;
    // bindMode is left at its default (AttachedBindMode). The bones live under `root`
    // rather than under any one mesh because a single Skeleton is shared by every skinned
    // mesh and cannot be parented under all of them; with root and each mesh at identity
    // the bone world matrices are the same either way.
    skinned.bind(skeleton, new THREE.Matrix4());
    // A SkinnedMesh's boundingSphere is computed from its REST vertex data and is not
    // recomputed when bones move, so a posed limb that swings outside its rest bounds gets
    // culled and vanishes -- worse, it vanishes only from certain camera angles, which
    // reads as a geometry bug rather than a culling one. Disabling the test outright is
    // chosen over recomputing bounds every frame because these are small, always-onscreen
    // character parts where the test saves nothing. Recorded in userData.rig so a consumer
    // that DOES need culling knows it has to supply its own bounds.
    skinned.frustumCulled = false;
    boundCount += 1;
  }
  root.userData.rig = { bones, skeleton, boneOrder, boneIndexOf, skinAttributes: skinnedMeshNames, bound: skinnedMeshNames.length > 0 && boundCount === skinnedMeshNames.length, frustumCulled: false, cullingNote: 'skinned meshes set frustumCulled = false; bone motion does not update a SkinnedMesh boundingSphere, so a consumer that needs culling must recompute bounds per frame' };

  root.userData.sculptRuntime = { nodes, meshes, sockets, colliders, destructionGroups } satisfies ProceduralModelRuntime;
  root.userData.lookDevTargets = {"qualityPriority": "reference-fidelity", "materialPass": {"albedoPaletteRequired": true, "roughnessVariationRequired": true, "normalOrBumpRequired": true, "localOverridesRequired": true, "minimumTextureResolution": 1024, "preferredTextureResolution": 2048, "independentMapChannels": ["albedo", "roughness", "height", "normal", "ambient-occlusion"], "requiredSurfaceFrequencyBands": ["macro", "meso", "micro"], "geometryReliefRequiredWhenSilhouetteAffected": true, "referencePbrExtraction": {"requiredWhenSourceImagePresent": true, "targetThreshold": 0.7, "stopOnLowConfidence": true, "script": "forge/stage1_intake/extract_pbr_evidence.py", "acceptedLimitation": "single-image extraction is reference-derived inference, not exact photogrammetry"}, "mustAvoid": ["single flat albedo per material", "uniform roughness", "albedo texture reused as roughness/height/normal/AO", "single-frequency random noise", "plastic-looking smooth bark, stone, cloth, foliage, or aged material", "local color/detail described only in prose without material masks", "claiming exact PBR recovery when confidence is below the target threshold"]}, "lightingPass": {"requiredTerms": ["key light", "fill light", "rim or environment light", "exposure", "tone mapping", "background", "contact shadow"], "mustAvoid": ["ambient-only lighting", "flat value range", "missing contact shadow", "reference lighting copied without separating material readability"]}, "screenshotReview": ["Compare albedo palette and local color zones.", "Compare roughness/normal/bump response under light.", "Compare cavity dirt, edge wear, stains, moss, scratches, or other local masks.", "Compare key/fill/rim structure, exposure, tone mapping, background, and contact shadows.", "Capture a neutral-light render to verify material readability without reference lighting.", "Capture a grazing-light close-up to expose flat normals, uniform roughness, tiling, and plastic highlights.", "Capture a reference-matched render from the same camera framing as the source."]};
  root.userData.actionReadiness = {
    note: 'Use root.userData.sculptRuntime.nodes for transforms, sockets for attachments, colliders for physics proxies, and destructionGroups for breakable sets.',
  };
  return root;
}

export function createOracleFigureLookDevLights(
  mode: 'neutral' | 'grazing' | 'reference' = 'neutral',
): THREE.Group {
  const lights = new THREE.Group();
  lights.name = "Oracle Figure look-dev lights";
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
  lights.userData.lightingFromPhoto = [];
  lights.userData.lookDevTargets = {"qualityPriority": "reference-fidelity", "materialPass": {"albedoPaletteRequired": true, "roughnessVariationRequired": true, "normalOrBumpRequired": true, "localOverridesRequired": true, "minimumTextureResolution": 1024, "preferredTextureResolution": 2048, "independentMapChannels": ["albedo", "roughness", "height", "normal", "ambient-occlusion"], "requiredSurfaceFrequencyBands": ["macro", "meso", "micro"], "geometryReliefRequiredWhenSilhouetteAffected": true, "referencePbrExtraction": {"requiredWhenSourceImagePresent": true, "targetThreshold": 0.7, "stopOnLowConfidence": true, "script": "forge/stage1_intake/extract_pbr_evidence.py", "acceptedLimitation": "single-image extraction is reference-derived inference, not exact photogrammetry"}, "mustAvoid": ["single flat albedo per material", "uniform roughness", "albedo texture reused as roughness/height/normal/AO", "single-frequency random noise", "plastic-looking smooth bark, stone, cloth, foliage, or aged material", "local color/detail described only in prose without material masks", "claiming exact PBR recovery when confidence is below the target threshold"]}, "lightingPass": {"requiredTerms": ["key light", "fill light", "rim or environment light", "exposure", "tone mapping", "background", "contact shadow"], "mustAvoid": ["ambient-only lighting", "flat value range", "missing contact shadow", "reference lighting copied without separating material readability"]}, "screenshotReview": ["Compare albedo palette and local color zones.", "Compare roughness/normal/bump response under light.", "Compare cavity dirt, edge wear, stains, moss, scratches, or other local masks.", "Compare key/fill/rim structure, exposure, tone mapping, background, and contact shadows.", "Capture a neutral-light render to verify material readability without reference lighting.", "Capture a grazing-light close-up to expose flat normals, uniform roughness, tiling, and plastic highlights.", "Capture a reference-matched render from the same camera framing as the source."]};
  return lights;
}

// PBR materials (clearcoat/iridescence/transmission/anisotropy) need an environment
// map to visually behave as intended — call this once per renderer and assign the
// result to scene.environment before rendering. No external HDR asset required.
export function createOracleFigureEnvironment(renderer: THREE.WebGLRenderer): THREE.Texture {
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
export function frameOracleFigureCamera(
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
export function createOracleFigurePresentationComposer(
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

export function configureOracleFigureRenderer(renderer: THREE.WebGLRenderer): void {
  // Load-bearing for view-dependent finishes (anodized / Doppler): without ACES + sRGB
  // the environment reflection reads flat/washed instead of a believable metal response.
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
}

export function createOracleFigureInspectControls(
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
