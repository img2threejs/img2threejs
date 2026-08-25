"""Rebuild the head as an implicit surface, then contour it with Surface Nets.

WHY NOT CROSS-SECTIONS. A radial outline stores ONE radius per angular bin, and an eyelid is several
surfaces at one azimuth, so the face is unrecoverable at any triangle count -- 2.97 M left the head an
egg. That the face IS geometry was proven by rendering baseline node 9 with map, normalMap and
roughnessMap all null: eyelids, brow ridges, nostrils, a philtrum.

WHY SURFACE NETS AND NOT MARCHING CUBES. Only numpy and PIL are available here. Marching cubes needs a
256-case table that is easy to get subtly wrong; Surface Nets emits one vertex per sign-changing cell
and one quad per sign-changing grid edge, which is a few dozen lines and cannot be half-right.

The field is the signed distance to the point cloud's local tangent planes, splatted: every vertex
contributes dot(cell - p, n_p) to the cells within RADIUS, weighted by a smooth falloff. Using the GLB's
own NORMAL attribute is what makes this reliable -- unoriented reconstruction has to guess inside from
outside, and oriented does not.
"""
from __future__ import annotations
import json, os, struct, sys
from pathlib import Path
import numpy as np

# ADAPTED FOR img2threejs (opt-in integration): this script's own location is no longer the
# showcase repo root -- it lives in integrations/glb_character_pipeline/python/ inside the
# img2threejs tool repo. Every showcase-side default path is resolved against
# IMG2THREEJS_SHOWCASE_ROOT instead (the same env var forge/tests already uses to reach a
# companion showcase checkout), falling back to the current directory only if unset.
ROOT = Path(os.environ.get('IMG2THREEJS_SHOWCASE_ROOT', '.')).resolve()
NODE = int(sys.argv[1]) if len(sys.argv) > 1 else 9
CELL = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0012
RADIUS = CELL * 2.5
PAD = RADIUS * 2

# PACKAGED (v1.5.1). GLB path and output directory are overridable so a second character can be built in
# the same checkout without colliding with girl-character's own artifacts. export_sdf_surfaces.py passes
# these through as env vars when it calls this script as a subprocess.
GLB_PATH = Path(os.environ.get('CHARACTER_GLB', str(ROOT / 'public/mesh/girl-character-baseline.glb')))
OUT_DIR = Path(os.environ.get('CHARACTER_WORKDIR', str(ROOT / 'work/head')))
OUT_DIR.mkdir(parents=True, exist_ok=True)

raw = GLB_PATH.read_bytes()
off, chunks = 12, {}
while off < len(raw):
    ln, ty = struct.unpack_from('<II', raw, off); chunks[ty] = raw[off+8:off+8+ln]; off += 8+ln
    off = off if off % 4 == 0 else off + (4 - off % 4)
g = json.loads(chunks[0x4E4F534A].decode()); BIN = chunks[0x004E4942]
def acc(i):
    a=g['accessors'][i]; bv=g['bufferViews'][a['bufferView']]
    dt={5120:'i1',5121:'u1',5122:'i2',5123:'u2',5125:'u4',5126:'f4'}[a['componentType']]
    nc={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4}[a['type']]
    o=bv.get('byteOffset',0)+a.get('byteOffset',0)
    return np.frombuffer(BIN,dtype=np.dtype('<'+dt),count=a['count']*nc,offset=o).reshape(a['count'],nc)
prims = g['meshes'][g['nodes'][NODE]['mesh']]['primitives']
P = np.concatenate([acc(p['attributes']['POSITION']) for p in prims]).astype(np.float64)
N = np.concatenate([acc(p['attributes']['NORMAL']) for p in prims]).astype(np.float64)
N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)
print(f"node {NODE}: {len(P):,} vertices, cell {CELL*1000:.2f} mm")

lo = P.min(axis=0) - PAD
hi = P.max(axis=0) + PAD
dims = np.ceil((hi - lo) / CELL).astype(int) + 1
print(f"  grid {dims[0]}x{dims[1]}x{dims[2]} = {np.prod(dims)/1e6:.1f} M cells")

sum_w = np.zeros(dims, dtype=np.float32)
sum_f = np.zeros(dims, dtype=np.float32)
K = int(np.ceil(RADIUS / CELL))
oc = np.arange(-K, K+1)
ox, oy, oz = np.meshgrid(oc, oc, oc, indexing='ij')
ox, oy, oz = ox.ravel(), oy.ravel(), oz.ravel()
base = np.floor((P - lo) / CELL).astype(np.int32)
CH = 4000
for s in range(0, len(P), CH):
    b = base[s:s+CH]; p = P[s:s+CH]; n = N[s:s+CH]
    ix = (b[:, None, 0] + ox[None, :]).ravel()
    iy = (b[:, None, 1] + oy[None, :]).ravel()
    iz = (b[:, None, 2] + oz[None, :]).ravel()
    ok = (ix >= 0) & (ix < dims[0]) & (iy >= 0) & (iy < dims[1]) & (iz >= 0) & (iz < dims[2])
    pid = np.repeat(np.arange(len(b)), len(ox))
    ix, iy, iz, pid = ix[ok], iy[ok], iz[ok], pid[ok]
    cx = lo[0] + ix*CELL; cy = lo[1] + iy*CELL; cz = lo[2] + iz*CELL
    dx = cx - p[pid, 0]; dy = cy - p[pid, 1]; dz = cz - p[pid, 2]
    d2 = dx*dx + dy*dy + dz*dz
    keep = d2 <= RADIUS*RADIUS
    ix, iy, iz, pid = ix[keep], iy[keep], iz[keep], pid[keep]
    dx, dy, dz, d2 = dx[keep], dy[keep], dz[keep], d2[keep]
    w = (1.0 - d2/(RADIUS*RADIUS))**2
    f = dx*n[pid,0] + dy*n[pid,1] + dz*n[pid,2]
    np.add.at(sum_w, (ix, iy, iz), w.astype(np.float32))
    np.add.at(sum_f, (ix, iy, iz), (w*f).astype(np.float32))
covered = sum_w > 1e-6
print(f"  cells with support: {covered.sum()/1e6:.2f} M ({covered.mean():.1%})")
# Outside the support the field must be POSITIVE, or the contour closes around empty space.
F = np.full(dims, RADIUS, dtype=np.float32)
F[covered] = sum_f[covered] / sum_w[covered]
del sum_w, sum_f

# ---- Surface Nets -------------------------------------------------------------------------------
sgn = F > 0
# a cell (i,j,k) owns the cube spanning i..i+1 in each axis
c000 = sgn[:-1,:-1,:-1]; c100 = sgn[1:,:-1,:-1]; c010 = sgn[:-1,1:,:-1]; c001 = sgn[:-1,:-1,1:]
c110 = sgn[1:,1:,:-1];   c101 = sgn[1:,:-1,1:];  c011 = sgn[:-1,1:,1:];  c111 = sgn[1:,1:,1:]
allsame = (c000==c100)&(c000==c010)&(c000==c001)&(c000==c110)&(c000==c101)&(c000==c011)&(c000==c111)
active = ~allsame
print(f"  active cells: {active.sum():,}")
ai, aj, ak = np.nonzero(active)
vid = -np.ones(np.array(dims)-1, dtype=np.int64)
vid[ai, aj, ak] = np.arange(len(ai))
# vertex position: average of zero crossings on the cube's 12 edges
acc_p = np.zeros((len(ai), 3)); acc_n = np.zeros(len(ai))
EDGES = [((0,0,0),(1,0,0)),((0,1,0),(1,1,0)),((0,0,1),(1,0,1)),((0,1,1),(1,1,1)),
         ((0,0,0),(0,1,0)),((1,0,0),(1,1,0)),((0,0,1),(0,1,1)),((1,0,1),(1,1,1)),
         ((0,0,0),(0,0,1)),((1,0,0),(1,0,1)),((0,1,0),(0,1,1)),((1,1,0),(1,1,1))]
for (a0,b0,c0),(a1,b1,c1) in EDGES:
    f0 = F[ai+a0, aj+b0, ak+c0]; f1 = F[ai+a1, aj+b1, ak+c1]
    cross = (f0 > 0) != (f1 > 0)
    if not cross.any(): continue
    t = np.zeros(len(ai)); den = (f0 - f1)
    with np.errstate(divide='ignore', invalid='ignore'):
        t = np.where(np.abs(den) > 1e-12, f0/den, 0.5)
    t = np.clip(t, 0.0, 1.0)
    px = (a0 + t*(a1-a0)); py = (b0 + t*(b1-b0)); pz = (c0 + t*(c1-c0))
    acc_p[cross] += np.stack([px[cross], py[cross], pz[cross]], axis=1)
    acc_n[cross] += 1
acc_n = np.maximum(acc_n, 1)
# Keep every vertex strictly inside the active cell that owns it. A zero crossing can average to an
# exact face/corner; float32 then floors it into the neighbouring cell and breaks the one-cell/one-
# vertex invariant the compact encoder verifies. The guard is one quarter of the encoder's own 8-bit
# in-cell quantisation step, so the largest correction is < cell/1020 (1.47 um at a 1.5 mm cell) and
# remains below the already-declared encoding precision.
fraction = acc_p / acc_n[:, None]
cell_guard = 1.0 / (255.0 * 4.0)
fraction = np.clip(fraction, cell_guard, 1.0 - cell_guard)
V = lo + (np.stack([ai, aj, ak], axis=1) + fraction) * CELL
# quads: one per sign-changing grid edge whose 4 surrounding cells are all active
# one quad per sign-changing grid edge, per axis
out = []
for axis in range(3):
    sl0 = [slice(1, dims[d]-1) if d == axis else slice(1, dims[d]) for d in range(3)]
    sl1 = list(sl0); sl1[axis] = slice(2, dims[axis])
    a = sgn[tuple(sl0)]; b = sgn[tuple(sl1)]
    ch = a != b
    if not ch.any(): continue
    idx = np.nonzero(ch)
    i = idx[0] + 1; j = idx[1] + 1; k = idx[2] + 1
    o = [(0,0,0)]*4
    if axis == 0: o = [(0,-1,-1),(0,0,-1),(0,0,0),(0,-1,0)]
    if axis == 1: o = [(-1,0,-1),(-1,0,0),(0,0,0),(0,0,-1)]
    if axis == 2: o = [(-1,-1,0),(0,-1,0),(0,0,0),(-1,0,0)]
    corner = []
    good = np.ones(len(i), dtype=bool)
    for (di,dj,dk) in o:
        ii, jj, kk = i+di, j+dj, k+dk
        inb = (ii>=0)&(ii<vid.shape[0])&(jj>=0)&(jj<vid.shape[1])&(kk>=0)&(kk<vid.shape[2])
        c = np.where(inb, vid[np.clip(ii,0,vid.shape[0]-1), np.clip(jj,0,vid.shape[1]-1), np.clip(kk,0,vid.shape[2]-1)], -1)
        good &= inb & (c >= 0)
        corner.append(c)
    q = np.stack([c[good] for c in corner], axis=1)
    flip = a[idx][good]
    q = np.where(flip[:,None], q[:, ::-1], q)
    out.append(q)
Q = np.concatenate(out) if out else np.zeros((0,4), dtype=np.int64)
T = np.concatenate([Q[:, [0,1,2]], Q[:, [0,2,3]]])
print(f"  surface: {len(V):,} vertices, {len(T):,} triangles")
np.save(OUT_DIR / 'V.npy', V.astype(np.float32))
np.save(OUT_DIR / 'T.npy', T.astype(np.int32))
np.save(OUT_DIR / 'cloud.npy', P.astype(np.float32))
print(f'  wrote {OUT_DIR}/{{V,T,cloud}}.npy')
