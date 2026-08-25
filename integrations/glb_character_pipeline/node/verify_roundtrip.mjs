/**
 * US-002/US-003: decode the encoded stream with the SHIPPING codec and compare it to the .bin.
 *
 * The point of using the real `surfaceCodec.ts` rather than a second implementation in this script is
 * that a reimplementation can agree with itself and still disagree with the browser. esbuild is already
 * a vite dependency, so the TypeScript is transpiled and imported here exactly as the bundle will get
 * it.
 *
 * What is compared, per node:
 *
 *   vertex count      must match exactly
 *   position          worst-case error, against the cell/255 quantisation step it is allowed
 *   colour            must match exactly; the encoder copies these bytes untouched
 *   triangle count    must match exactly
 *   triangle set      the same triangles, up to rotation and winding, as a set
 *
 * A difference in the triangle SET is the one that matters: positions can be a micron out without
 * anyone seeing it, but a missing triangle is a hole.
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const LEVEL = process.argv[2] ?? 'x2';
const suffix = LEVEL === 'default' ? '' : `-${LEVEL}`;
const tag = LEVEL === 'default' ? '-default' : suffix;
/**
 * PACKAGED (v1.5.1). The codec entrypoint, the .bin source directory and the work/surfaces* tag are
 * overridable. The codec itself (surfaceCodec.ts) is character-agnostic -- it decodes whatever
 * EncodedNode metadata it is given -- so a new character normally reuses the SAME codec file and only
 * needs its own CHARACTER_BIN_DIR / CHARACTER_WORK_TAG.
 */
const CODEC_ENTRY = process.env.CHARACTER_CODEC ?? 'src/demos/girl-character/surfaceCodec.ts';
const BIN_DIR = process.env.CHARACTER_BIN_DIR ?? 'public/head';
const WORK_TAG = process.env.CHARACTER_WORK_TAG ?? '';

mkdirSync('work/codec', { recursive: true });
await build({
  entryPoints: [CODEC_ENTRY],
  // PACKAGED (v1.5.1) FIX: an absolute path, resolved against process.cwd() (build-character.sh always
  // cds to the repo root first). A path relative to THIS SCRIPT's own location broke the moment the
  // script moved one directory deeper during packaging -- '../work/codec/...' resolved to
  // v1.5.1/work/codec/... instead of <repo root>/work/codec/..., and the dynamic import below threw
  // ERR_MODULE_NOT_FOUND. Caught by actually running this against real data, not by inspection.
  outfile: path.resolve(process.cwd(), 'work/codec/surfaceCodec.mjs'),
  format: 'esm', platform: 'node', bundle: true, external: ['three'], logLevel: 'error',
});
const codecOut = path.resolve(process.cwd(), 'work/codec/surfaceCodec.mjs');
const { decodeSurfaces } = await import(pathToFileURL(codecOut).href);

function loadNodes(path) {
  const buf = readFileSync(path);
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

const meta = JSON.parse(readFileSync(`work/surfaces${WORK_TAG}${tag}.json`, 'utf8'));
const base64 = readFileSync(`work/surfaces${WORK_TAG}${tag}.b64`, 'utf8');
const decoded = decodeSurfaces(base64, meta.nodes);
const original = loadNodes(`${BIN_DIR}/sdf-surfaces${suffix}.bin`);
const byNode = new Map(original.map((o) => [o.node, o]));

/**
 * Triangles compared as unordered index triples, which is valid because the vertex ORDER is preserved.
 *
 * Two earlier versions of this comparison were both wrong, in opposite directions. The first compared
 * only triangle COUNTS and passed a mesh that rendered with flakes all over it -- counting is not
 * comparing. The second keyed on positions quantised to 1e-5, which is 10 microns, against a
 * quantisation error the encoder is allowed up to 9.8 microns: keys fell on either side of a bucket
 * edge and it reported 99.94% of triangles wrong when the codec was right. A comparison whose tolerance
 * is the same size as the thing it tolerates measures nothing.
 *
 * The position check above already establishes that vertex i means the same point in both, so indices
 * are directly comparable and exact.
 */
const triangleKey = (a, b, c) => [a, b, c].sort((x, y) => x - y).join(',');

let failures = 0;
console.log(`level ${LEVEL}\n`);
for (const surface of decoded) {
  const source = byNode.get(surface.node);
  const n = surface.position.length / 3;
  const cell = surface.cellMillimetres / 1000;

  // ORDER FIRST, because the triangle comparison below depends on it. Comparing index triples is only
  // valid if vertex i means the same point on both sides; this measures that rather than assuming it. A
  // permutation would show up here as centimetres, not microns.
  let worst = 0;
  for (let i = 0; i < n * 3; i += 1) worst = Math.max(worst, Math.abs(surface.position[i] - source.V[i]));
  let displaced = 0;
  for (let i = 0; i < n; i += 1) {
    const d = Math.hypot(surface.position[3 * i] - source.V[3 * i],
                         surface.position[3 * i + 1] - source.V[3 * i + 1],
                         surface.position[3 * i + 2] - source.V[3 * i + 2]);
    if (d > cell) displaced += 1;
  }
  let colourDiff = 0;
  for (let i = 0; i < n * 3; i += 1) colourDiff = Math.max(colourDiff, Math.abs(surface.colour[i] - source.C[i]));

  const mine = new Set();
  for (let t = 0; t < surface.index.length; t += 3) {
    mine.add(triangleKey(surface.index[t], surface.index[t + 1], surface.index[t + 2]));
  }
  const theirs = new Set();
  for (let t = 0; t < source.T.length; t += 3) {
    theirs.add(triangleKey(source.T[t], source.T[t + 1], source.T[t + 2]));
  }
  let missing = 0;
  for (const key of theirs) if (!mine.has(key)) missing += 1;
  let extra = 0;
  for (const key of mine) if (!theirs.has(key)) extra += 1;

  const step = cell / 255;
  const pass = n === source.vertexCount
    && worst <= step * 1.01
    && colourDiff === 0
    && surface.index.length === source.T.length
    && missing === 0 && extra === 0 && displaced === 0;
  if (!pass) failures += 1;
  console.log(`node ${String(surface.node).padStart(2)} ${surface.region.padEnd(11)} `
    + `verts ${String(n).padStart(7)}/${String(source.vertexCount).padStart(7)}  `
    + `pos worst ${(worst * 1e6).toFixed(1).padStart(7)} um (step ${(step * 1e6).toFixed(1)} um)  `
    + `order-broken ${String(displaced).padStart(5)}  tris ${String(surface.index.length / 3).padStart(8)}/`
    + `${String(source.T.length / 3).padStart(8)}  missing ${String(missing).padStart(7)}  `
    + `extra ${String(extra).padStart(7)}  ${pass ? 'OK' : 'FAIL'}`);
}
console.log(`\n${decoded.length - failures} of ${decoded.length} nodes decode to the source within the quantisation step`);
writeFileSync(`work/roundtrip${WORK_TAG}${tag}.txt`, `failures ${failures}\n`);
if (failures) process.exitCode = 1;
