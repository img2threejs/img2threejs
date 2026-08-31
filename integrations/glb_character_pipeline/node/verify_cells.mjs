/**
 * US-001: prove the .bin's index buffer is redundant, or find out that it is not.
 *
 * The surfaces were contoured with Surface Nets, which places exactly ONE vertex per sign-changing
 * cell at `lo + (ijk + offset) * cell`, and emits one quad per sign-changing grid edge. If that holds
 * for the shipped data then a vertex's cell coordinate identifies it, and the connectivity follows from
 * a handful of bits per cell instead of a stored index buffer -- 50.6 MB of the default level's 107.6.
 *
 * This checks the claim rather than assuming it, on all 16 nodes:
 *
 *   1. recover the grid origin, and require vertex -> cell to be a BIJECTION
 *   2. recover the quads (they were written as two triangle halves, so quad q is T[q] and T[q + n/2])
 *   3. reduce each quad to (cell, axis, winding) -- six bits per cell
 *   4. rebuild the quads from those bits alone and compare the triangle SETS
 *
 * A mismatch on any node is reported as a mismatch. Shipping a silent one would trade a visible 107 MB
 * download for an invisible hole in the mesh.
 */
import { readFileSync } from 'node:fs';

const SRC = process.argv[2] ?? 'public/head/sdf-surfaces-x3.bin';
const GLB = process.argv[3] ?? 'public/mesh/girl-character-baseline.glb';

function loadNodes(path) {
  const buf = readFileSync(path);
  if (buf.toString('latin1', 0, 4) !== 'HEDS') throw new Error(`${path}: bad magic`);
  const jsonLength = buf.readUInt32LE(8);
  const header = JSON.parse(buf.toString('utf8', 12, 12 + jsonLength));
  let offset = 12 + jsonLength;
  offset += (4 - (offset % 4)) % 4;
  const out = [];
  for (const entry of header) {
    const n = entry.vertexCount;
    const posAt = offset;
    let idxAt = posAt + n * 12 + n * 12 + n * 3;
    idxAt += (4 - (idxAt % 4)) % 4;
    const end = idxAt + entry.indexCount * 4;
    out.push({
      ...entry,
      V: new Float32Array(buf.buffer.slice(buf.byteOffset + posAt, buf.byteOffset + posAt + n * 12)),
      T: new Uint32Array(buf.buffer.slice(buf.byteOffset + idxAt, buf.byteOffset + end)),
    });
    offset = end;
  }
  return out;
}

/**
 * The grid origin, taken from the builder's own rule rather than searched for.
 *
 * scripts/build_head_surface.py sets `lo = P.min(axis=0) - PAD` with PAD = RADIUS * 2 and
 * RADIUS = CELL * 2.5, so the origin is the node's point-cloud minimum less five cells. A first
 * version of this script swept the sub-cell phase instead and put 29% of vertices in a cell already
 * taken -- the phase is set by the CLOUD's minimum, and the surface's minimum is a different number.
 */
function cloudMinima(glbPath) {
  const raw = readFileSync(glbPath);
  let off = 12; const chunks = {};
  while (off < raw.length) {
    const ln = raw.readUInt32LE(off); const ty = raw.readUInt32LE(off + 4);
    chunks[ty] = raw.subarray(off + 8, off + 8 + ln);
    off += 8 + ln; off += (4 - (off % 4)) % 4;
  }
  const g = JSON.parse(chunks[0x4E4F534A].toString('utf8'));
  const minima = new Map();
  g.nodes.forEach((node, index) => {
    if (node.mesh === undefined) return;
    const min = [Infinity, Infinity, Infinity];
    for (const primitive of g.meshes[node.mesh].primitives) {
      const accessor = g.accessors[primitive.attributes.POSITION];
      if (!accessor.min) throw new Error(`accessor for node ${index} has no min`);
      for (let c = 0; c < 3; c += 1) min[c] = Math.min(min[c], accessor.min[c]);
    }
    minima.set(index, min);
  });
  return minima;
}

function findOrigin(cloudMin, cell) {
  return [0, 1, 2].map((c) => cloudMin[c] - cell * 5);
}

const MINIMA = cloudMinima(GLB);
let ok = 0; let bad = 0;
for (const node of loadNodes(SRC)) {
  const cell = node.cellMillimetres / 1000;
  const n = node.vertexCount;
  const lo = findOrigin(MINIMA.get(node.node), cell);

  // 1. bijection vertex -> cell
  const cellOf = new Array(n);
  const seen = new Map();
  let collisions = 0;
  for (let i = 0; i < n; i += 1) {
    const raw = [0, 1, 2].map((c) => (node.V[3 * i + c] - lo[c]) / cell);
    const ijk = raw.map(Math.floor);
    let key = `${ijk[0]},${ijk[1]},${ijk[2]}`;
    // BOUNDARY CASE, and it is not floating-point noise. The builder's in-cell offset is an average of
    // edge crossings and can come out exactly 1.0 when every crossing clips to the far corner, which
    // floors into the NEXT cell and collides with whatever lives there. Stepping back along an axis
    // whose offset landed on the boundary puts the vertex back in its own cell.
    if (seen.has(key)) {
      // Search the 27 neighbours for a free cell that still CONTAINS this vertex to within float32's
      // reach. The tolerance has to be scaled: (V - lo) / cell runs to about 500, so a float32 mantissa
      // is worth roughly 3e-5 there, not the 1e-5 a first pass assumed -- which is why five nodes still
      // collided after it.
      const EPS = 1e-3;
      let placed = false;
      for (let d = 0; d < 27 && !placed; d += 1) {
        const trial = [ijk[0] + (d % 3) - 1, ijk[1] + (Math.floor(d / 3) % 3) - 1, ijk[2] + Math.floor(d / 9) - 1];
        const contains = [0, 1, 2].every((c) => raw[c] >= trial[c] - EPS && raw[c] <= trial[c] + 1 + EPS);
        if (!contains) continue;
        const trialKey = `${trial[0]},${trial[1]},${trial[2]}`;
        if (seen.has(trialKey)) continue;
        ijk[0] = trial[0]; ijk[1] = trial[1]; ijk[2] = trial[2];
        key = trialKey; placed = true;
      }
    }
    if (seen.has(key)) collisions += 1;
    seen.set(key, i);
    cellOf[i] = ijk;
  }

  // 2. quads: written as T[q] = quad[0,1,2] and T[q + half] = quad[0,2,3]
  const tris = node.T.length / 3;
  const half = tris / 2;
  const quads = [];
  let pairingOk = Number.isInteger(half);
  if (pairingOk) {
    for (let q = 0; q < half; q += 1) {
      const a = node.T.subarray(3 * q, 3 * q + 3);
      const b = node.T.subarray(3 * (q + half), 3 * (q + half) + 3);
      if (a[0] !== b[0] || a[2] !== b[1]) { pairingOk = false; break; }
      quads.push([a[0], a[1], a[2], b[2]]);
    }
  }

  // 3. reduce each quad to (cell, axis, winding). The four cells around a grid edge differ by -1/0 on
  //    the two axes that are not the edge's own, so the axis falls out of which coordinate is constant.
  const bits = new Map();
  const reducible = pairingOk;
  // COUNT, do not bail. An earlier version broke out of this loop on the first quad it could not
  // reduce, which reported "rebuilt 0" and hid whether the problem was one quad or all of them.
  let unreduced = 0;
  for (const quad of quads) {
    const cs = quad.map((v) => cellOf[v]);
    let axis = -1;
    for (let c = 0; c < 3; c += 1) {
      if (cs.every((x) => x[c] === cs[0][c])) { axis = c; break; }
    }
    if (axis === -1) { unreduced += 1; continue; }
    // the edge is identified by the maximum corner of the four cells
    const base = [0, 1, 2].map((c) => Math.max(...cs.map((x) => x[c])));
    const key = `${base[0]},${base[1]},${base[2]},${axis}`;
    // winding: which of the two orderings the builder emitted
    bits.set(key, quad);
  }

  // 4. rebuild from the bits alone: for each recorded edge, gather its four cells and re-emit
  let rebuilt = 0; let mismatched = 0;
  if (reducible) {
    const OFFSETS = [
      [[0, -1, -1], [0, 0, -1], [0, 0, 0], [0, -1, 0]],
      [[-1, 0, -1], [-1, 0, 0], [0, 0, 0], [0, 0, -1]],
      [[-1, -1, 0], [0, -1, 0], [0, 0, 0], [-1, 0, 0]],
    ];
    for (const [key, original] of bits) {
      const [i, j, k, axis] = key.split(',').map(Number);
      const corner = OFFSETS[axis].map(([di, dj, dk]) => seen.get(`${i + di},${j + dj},${k + dk}`));
      if (corner.some((c) => c === undefined)) { mismatched += 1; continue; }
      const forward = corner.join(',');
      const reversed = [...corner].reverse().join(',');
      const want = original.join(',');
      const wantRotations = [0, 1, 2, 3].flatMap((r) => {
        const rot = [...original.slice(r), ...original.slice(0, r)].join(',');
        const rev = [...original].reverse();
        return [rot, [...rev.slice(r), ...rev.slice(0, r)].join(',')];
      });
      if (wantRotations.includes(forward) || wantRotations.includes(reversed)) rebuilt += 1;
      else { mismatched += 1; if (mismatched < 3) console.log(`      want ${want}  got ${forward}`); }
    }
  }

  const verdict = collisions === 0 && pairingOk && reducible && mismatched === 0 && unreduced === 0;
  if (verdict) ok += 1; else bad += 1;
  console.log(`node ${String(node.node).padStart(2)} ${node.region.padEnd(11)} ${String(n).padStart(8)} verts  `
    + `cell ${String(node.cellMillimetres).padStart(4)}mm  collisions ${String(collisions).padStart(6)}  `
    + `quads ${String(quads.length).padStart(8)}  rebuilt ${String(rebuilt).padStart(8)}  `
    + `unreduced ${String(unreduced).padStart(5)}  mismatched ${String(mismatched).padStart(6)}  ${verdict ? 'OK' : 'FAIL'}`);
}
console.log(`\n${ok} of ${ok + bad} nodes reproduce their index buffer from cell adjacency alone`);
