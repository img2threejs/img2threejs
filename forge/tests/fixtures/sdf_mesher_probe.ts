import * as THREE from 'three';
import { polygonizeSdf } from './mesher';
const R = 0.5, RES = 24, LO = -0.7, HI = 0.7;
const g = polygonizeSdf({ primitives: [{ id: 's', type: 'sphere', center: [0,0,0], radius: R }],
  operations: [], bounds: { min: [LO,LO,LO], max: [HI,HI,HI] }, resolution: RES } as any);
const pos = g.getAttribute('position'), nrm = g.getAttribute('normal');
const v = new THREE.Vector3(), n = new THREE.Vector3();
let maxDev = 0, sumDev = 0, worstDot = 1;
for (let i = 0; i < pos.count; i++) {
  v.fromBufferAttribute(pos, i);
  const d = Math.abs(v.length() - R); maxDev = Math.max(maxDev, d); sumDev += d;
  n.fromBufferAttribute(nrm, i);
  worstDot = Math.min(worstDot, v.clone().normalize().dot(n));
}
const cell = (HI - LO) / RES;
// outward-facing test: every triangle's geometric normal must agree with its vertex normals
const idx = g.index!; let backfacing = 0;
const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), fn = new THREE.Vector3();
for (let t = 0; t < idx.count; t += 3) {
  a.fromBufferAttribute(pos, idx.getX(t)); b.fromBufferAttribute(pos, idx.getX(t+1)); c.fromBufferAttribute(pos, idx.getX(t+2));
  fn.copy(b).sub(a).cross(c.clone().sub(a));
  n.fromBufferAttribute(nrm, idx.getX(t));
  if (fn.dot(n) < 0) backfacing++;
}
console.log('sphere r=0.5, grid 24 over [-0.7,0.7], cell =', cell.toFixed(5));
console.log('  verts', pos.count, ' tris', idx.count/3);
console.log('  maxDev  ', maxDev.toFixed(5), '=', (maxDev/cell).toFixed(3), 'cells');
console.log('  meanDev ', (sumDev/pos.count).toFixed(5), '=', (sumDev/pos.count/cell).toFixed(3), 'cells');
console.log('  worst normal-radial dot', worstDot.toFixed(5));
console.log('  backfacing triangles', backfacing, '/', idx.count/3);
const ok = maxDev < cell*0.35 && worstDot > 0.99 && backfacing === 0;
console.log(ok ? '\nPASS' : '\nFAIL');
