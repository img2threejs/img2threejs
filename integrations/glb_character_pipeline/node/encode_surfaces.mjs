/**
 * US-002: encode the Surface Nets output compactly enough to live inside a TypeScript module.
 *
 * US-001 established what is actually information in the .bin and what is not. Every vertex sits in its
 * own voxel cell, so the cell coordinate identifies it and the connectivity follows from cell adjacency:
 * the stored index buffer (50.6 MB of the default level's 107.6) and the stored normals (another 25 MB)
 * are both derivable and are not written here at all.
 *
 * WHAT IS WRITTEN, per node:
 *
 *   grid       origin, cell size, dimensions -- 7 numbers, from the GLB's accessor minima
 *   cells      the active cell list, sorted by linear index and DELTA-encoded as varints. Sorted
 *              deltas are mostly small, which is the whole reason this fits: a raw (i,j,k) is 5 bytes
 *              and a delta is usually 1.
 *   offsets    the in-cell position, 8 bits per axis. One cell is 1.5-2.5 mm, so a step is 6-10 microns
 *              -- three orders below the cell, and the cell is already the resolution limit.
 *   colours    8 bits per channel, unchanged from the source bake.
 *   edges      6 bits per cell: for each of the three axes, whether the grid edge leaving this cell
 *              changes sign, and which way the resulting quad winds. This is what replaces the indices.
 *   exceptions the 0.001% of quads US-001 could not reduce, written out explicitly, so the rebuild is
 *              bit-exact instead of approximately right.
 *
 * Emitted as base64 inside a .ts module, so the demo imports it and fetches nothing.
 */
import { readFileSync, writeFileSync, statSync } from 'node:fs';

const LEVELS = process.argv.slice(2).length ? process.argv.slice(2) : ['x3'];
/**
 * PACKAGED (v1.5.1). GLB path, .bin source directory and the intermediate work/surfaces* filename
 * prefix are overridable, so a second character can be encoded in the same checkout without colliding
 * with girl-character's own artifacts. build-character.sh sets these from a per-character .env config.
 */
const GLB = process.env.CHARACTER_GLB ?? 'public/mesh/girl-character-baseline.glb';
const BIN_DIR = process.env.CHARACTER_BIN_DIR ?? 'public/head';
const WORK_TAG = process.env.CHARACTER_WORK_TAG ?? '';

function loadNodes(path) {
  const buf = readFileSync(path);
  if (buf.toString('latin1', 0, 4) !== 'HEDS') throw new Error(`${path}: bad magic`);
  const jsonLength = buf.readUInt32LE(8);
  const header = JSON.parse(buf.toString('utf8', 12, 12 + jsonLength));
  let offset = 12 + jsonLength;
  offset += (4 - (offset % 4)) % 4;
  return header.map((entry) => {
    const n = entry.vertexCount;
    const posAt = offset;
    const colAt = posAt + n * 12 + n * 12;
    let idxAt = colAt + n * 3;
    idxAt += (4 - (idxAt % 4)) % 4;
    const end = idxAt + entry.indexCount * 4;
    const out = {
      ...entry,
      V: new Float32Array(buf.buffer.slice(buf.byteOffset + posAt, buf.byteOffset + posAt + n * 12)),
      C: new Uint8Array(buf.buffer.slice(buf.byteOffset + colAt, buf.byteOffset + colAt + n * 3)),
      T: new Uint32Array(buf.buffer.slice(buf.byteOffset + idxAt, buf.byteOffset + end)),
    };
    offset = end;
    return out;
  });
}

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
    const max = [-Infinity, -Infinity, -Infinity];
    for (const primitive of g.meshes[node.mesh].primitives) {
      const accessor = g.accessors[primitive.attributes.POSITION];
      for (let c = 0; c < 3; c += 1) {
        min[c] = Math.min(min[c], accessor.min[c]);
        max[c] = Math.max(max[c], accessor.max[c]);
      }
    }
    minima.set(index, { min, max });
  });
  return minima;
}

/** Varint, low seven bits first, high bit as the continuation flag. */
function pushVarint(bytes, value) {
  let v = value;
  while (v >= 0x80) { bytes.push((v & 0x7f) | 0x80); v = Math.floor(v / 128); }
  bytes.push(v);
}

const MINIMA = cloudMinima(GLB);

for (const level of LEVELS) {
  const suffix = level === '' || level === 'default' ? '' : `-${level}`;
  const src = `${BIN_DIR}/sdf-surfaces${suffix}.bin`;
  const nodes = loadNodes(src);
  const out = { level: suffix, nodes: [] };
  const blocks = [];
  let totalVertices = 0; let totalQuads = 0; let totalExceptions = 0;

  for (const node of nodes) {
    const cell = node.cellMillimetres / 1000;
    const n = node.vertexCount;
    const bounds = MINIMA.get(node.node);
    const lo = bounds.min.map((m) => m - cell * 5);
    // THE BUILDER'S OWN GRID, reproduced rather than invented. build_head_surface.py sets
    // hi = P.max + PAD and dims = ceil((hi - lo) / cell) + 1, and emits vertices in C-order over the
    // CELL grid, which is dims - 1. Using a tighter grid of my own changed the linear index, so the
    // sort reordered the vertices relative to the source -- 1,193 of them on the trousers -- and every
    // index-based comparison downstream became meaningless. Matching the builder means the source is
    // already in order and nothing is reordered at all.
    const hi = bounds.max.map((m) => m + cell * 5);
    const grid = [0, 1, 2].map((c) => Math.ceil((hi[c] - lo[c]) / cell) + 1);
    const cellDims = grid.map((v) => v - 1);

    // Cell assignment, with the same boundary handling US-001 needed.
    const taken = new Map();
    const ijkOf = new Array(n);
    const fracOf = new Array(n);
    for (let i = 0; i < n; i += 1) {
      const raw = [0, 1, 2].map((c) => (node.V[3 * i + c] - lo[c]) / cell);
      const ijk = raw.map(Math.floor);
      let key = `${ijk[0]},${ijk[1]},${ijk[2]}`;
      if (taken.has(key)) {
        const EPS = 1e-3;
        for (let d = 0; d < 27; d += 1) {
          const t = [ijk[0] + (d % 3) - 1, ijk[1] + (Math.floor(d / 3) % 3) - 1, ijk[2] + Math.floor(d / 9) - 1];
          if (![0, 1, 2].every((c) => raw[c] >= t[c] - EPS && raw[c] <= t[c] + 1 + EPS)) continue;
          const tk = `${t[0]},${t[1]},${t[2]}`;
          if (taken.has(tk)) continue;
          ijk[0] = t[0]; ijk[1] = t[1]; ijk[2] = t[2]; key = tk; break;
        }
      }
      taken.set(key, i);
      ijkOf[i] = ijk;
      fracOf[i] = [0, 1, 2].map((c) => Math.min(1, Math.max(0, raw[c] - ijk[c])));
    }

    // Grid dimensions from the cells present, so the linear index is compact.
    const dims = cellDims;
    // A coordinate outside the grid must be rejected, not packed: negative j or k wraps into another
    // valid cell rather than out of range, which silently connects the wrong vertices.
    const inGrid = (ijk) => ijk.every((v, c) => v >= 0 && v < dims[c]);
    const linear = (ijk) => (ijk[0] * dims[1] + ijk[1]) * dims[2] + ijk[2];
    const cellIndex = (ijk) => (inGrid(ijk) ? linear(ijk) : -1);

    // Sort by linear index: this is what makes the deltas small.
    // NOT SORTED, so vertex i means the same point on both sides. With the builder's grid the source is
    // very nearly ascending already, but not perfectly: the collision search moves a handful of
    // vertices into a neighbouring cell, which can step backwards. Sorting to fix that is what
    // reordered 1,193 vertices on the trousers and made every index-based check meaningless, so the
    // order is kept and the delta is allowed to be negative instead.
    const order = Array.from({ length: n }, (_, i) => i);
    let backwards = 0;
    for (let i = 1; i < n; i += 1) {
      if (linear(ijkOf[order[i]]) < linear(ijkOf[order[i - 1]])) backwards += 1;
    }
    const rank = new Array(n);
    order.forEach((original, position) => { rank[original] = position; });
    const cellAt = new Map();
    order.forEach((original, position) => { cellAt.set(linear(ijkOf[original]), position); });

    // Quads, recovered as US-001 did, then reduced to per-cell edge bits.
    const tris = node.T.length / 3;
    const half = tris / 2;
    const edgeBits = new Uint16Array(n);
    const exceptions = [];
    const OFFSETS = [
      [[0, -1, -1], [0, 0, -1], [0, 0, 0], [0, -1, 0]],
      [[-1, 0, -1], [-1, 0, 0], [0, 0, 0], [0, 0, -1]],
      [[-1, -1, 0], [0, -1, 0], [0, 0, 0], [-1, 0, 0]],
    ];
    for (let q = 0; q < half; q += 1) {
      const a = node.T.subarray(3 * q, 3 * q + 3);
      const b = node.T.subarray(3 * (q + half), 3 * (q + half) + 3);
      const quad = [a[0], a[1], a[2], b[2]];
      const cs = quad.map((v) => ijkOf[v]);
      let axis = -1;
      for (let c = 0; c < 3; c += 1) if (cs.every((x) => x[c] === cs[0][c])) { axis = c; break; }
      if (axis === -1) { exceptions.push(quad.map((v) => rank[v])); continue; }
      const base = [0, 1, 2].map((c) => Math.max(...cs.map((x) => x[c])));
      const owner = cellAt.get(cellIndex(base));
      if (owner === undefined) { exceptions.push(quad.map((v) => rank[v])); continue; }
      // Does the rebuild from this cell reproduce the quad? If not, keep the quad verbatim.
      const corner = OFFSETS[axis].map(([di, dj, dk]) => cellAt.get(cellIndex([base[0] + di, base[1] + dj, base[2] + dk])));
      if (corner.some((c) => c === undefined)) { exceptions.push(quad.map((v) => rank[v])); continue; }
      const wanted = quad.map((v) => rank[v]).join(',');
      // WHICH CORNER THE SPLIT STARTS FROM MATTERS. The quad is cut into two triangles from its first
      // vertex, so a rotation of the same four corners cuts the OTHER diagonal -- a different surface
      // wherever the quad is not planar, which for Surface Nets output is most of them. Storing only
      // "these four corners, maybe reversed" left exactly one triangle per quad wrong and rendered as
      // rows of diamond-shaped facets. So the rotation is stored too: four bits per axis, not two.
      let rotation = -1; let reversed = false;
      for (let rev = 0; rev < 2 && rotation < 0; rev += 1) {
        const seq = rev ? [...corner].reverse() : corner;
        for (let r = 0; r < 4; r += 1) {
          if ([...seq.slice(r), ...seq.slice(0, r)].join(',') === wanted) { rotation = r; reversed = rev === 1; break; }
        }
      }
      if (rotation < 0) { exceptions.push(quad.map((v) => rank[v])); continue; }
      // bit 0 of the nibble: the edge exists. bit 1: reversed. bits 2-3: the rotation.
      edgeBits[owner] |= (1 | (reversed ? 2 : 0) | (rotation << 2)) << (4 * axis);
    }

    // Stream it.
    const cells = [];
    let previous = 0;
    for (const original of order) {
      const value = linear(ijkOf[original]);
      // DELTA, NOT DELTA MINUS ONE. Two vertices can land in the same cell when the collision search
      // cannot free one -- five times in 2.1 million on the full level -- and `delta - 1` is then -1,
      // which Uint8Array stores as 255: a continuation byte that makes the decoder read past the end of
      // the section. It drifted node 10 by six bytes and every node after it decoded from the wrong
      // offset. Encoding the delta itself costs nothing (a run of consecutive cells is still one byte)
      // and makes a duplicate representable; the quads around it become explicit exceptions.
      // Zigzag, so a backwards step costs one bit rather than being unrepresentable.
      const delta = value - previous;
      pushVarint(cells, delta >= 0 ? delta * 2 : -delta * 2 - 1);
      previous = value;
    }
    const offsets = new Uint8Array(n * 3);
    const colours = new Uint8Array(n * 3);
    order.forEach((original, position) => {
      for (let c = 0; c < 3; c += 1) {
        offsets[position * 3 + c] = Math.round(fracOf[original][c] * 255);
        colours[position * 3 + c] = node.C[original * 3 + c];
      }
    });
    const exceptionBytes = [];
    pushVarint(exceptionBytes, exceptions.length);
    for (const quad of exceptions) for (const v of quad) pushVarint(exceptionBytes, v);

    const edgeBytes = new Uint8Array(edgeBits.buffer, edgeBits.byteOffset, edgeBits.byteLength);
    const parts = [Uint8Array.from(cells), offsets, colours, edgeBytes, Uint8Array.from(exceptionBytes)];
    const block = Buffer.concat(parts.map((p) => Buffer.from(p.buffer, p.byteOffset, p.byteLength)));
    blocks.push(block);
    out.nodes.push({
      node: node.node, region: node.region, material: node.material ?? null,
      cellMillimetres: node.cellMillimetres,
      vertexCount: n, origin: lo, dims,
      bytes: { cells: cells.length, offsets: offsets.length, colours: colours.length, edges: n * 2, exceptions: exceptionBytes.length },
      exceptionQuads: exceptions.length,
      backwardsSteps: backwards,
    });
    totalVertices += n; totalQuads += half; totalExceptions += exceptions.length;
  }

  const body = Buffer.concat(blocks);
  const base64 = body.toString('base64');
  const dest = `work/surfaces${WORK_TAG}${suffix || '-default'}.json`;
  writeFileSync(dest, JSON.stringify(out, null, 1));
  writeFileSync(`work/surfaces${WORK_TAG}${suffix || '-default'}.b64`, base64);
  const binMb = statSync(src).size / 1e6;
  console.log(`${src}`);
  console.log(`  ${totalVertices.toLocaleString()} verts  ${totalQuads.toLocaleString()} quads  `
    + `${totalExceptions} exception quads (${(100 * totalExceptions / totalQuads).toFixed(4)}%)`);
  console.log(`  stream ${(body.length / 1e6).toFixed(2)} MB = ${(body.length / totalVertices).toFixed(2)} bytes/vertex`
    + `   base64 ${(base64.length / 1e6).toFixed(2)} MB   was ${binMb.toFixed(1)} MB  `
    + `(${(100 * base64.length / (binMb * 1e6)).toFixed(1)}% of the .bin)`);
}
