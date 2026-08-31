"""Pack one or more Surface Nets node surfaces into a single binary the demo fetches.

Supersedes export_head_surface.py, which handled node 9 alone. Same construction throughout: a signed
distance field splatted from the node's own point cloud using the GLB's NORMAL attribute, contoured by
Surface Nets, coloured per vertex from the GLB diffuse (mean of the four nearest source vertices,
decoded sRGB -> linear because three.js treats a colour attribute as already being in the working
space). No UVs, so no UV seams.

WHY THIS EXISTS FOR MORE THAN THE HEAD. Five regions never moved across an entire pass of filtering and
material work -- pouches, boots, knee-pads, katana, canister -- and all five sit 1.3-1.8x the baseline's
surface noise. US-001 established that the residual is a SMOOTH error in the radial estimator, not
noise: a notch filter with zero gain at the ring frequency removed 100% of that component and moved the
figure by 6.8%. The head then showed what replacing the estimator is worth -- 17.40 -> 9.65, below the
baseline's own 11.76, with IoU 0.995.

Layout, little-endian:
    magic 'HEDS'  u32 version(3)  u32 jsonLength
    json bytes, padded to a 4-byte boundary
    then, per entry in json order: f32 position[3n], f32 normal[3n], u8 colour[3n] padded, u32 index[m]
"""
from __future__ import annotations
import json, os, struct, subprocess, sys
from collections import defaultdict
from io import BytesIO
from pathlib import Path
import numpy as np
from PIL import Image
Image.MAX_IMAGE_PIXELS = None

# ADAPTED FOR img2threejs (opt-in integration): this script's own location is no longer the
# showcase repo root -- it lives in integrations/glb_character_pipeline/python/ inside the
# img2threejs tool repo. Every showcase-side default path is resolved against
# IMG2THREEJS_SHOWCASE_ROOT instead (the same env var forge/tests already uses to reach a
# companion showcase checkout), falling back to the current directory only if unset.
ROOT = Path(os.environ.get('IMG2THREEJS_SHOWCASE_ROOT', '.')).resolve()

# PACKAGED (v1.5.1). Everything a new character has to supply is read from environment variables, with
# girl-character's own values as the default so this script reproduces the existing build unchanged when
# none are set. build-character.sh sets all of these from a per-character .env config file; see
# configs/example.env for what a new reference needs.
#
#   CHARACTER_GLB           path to the baseline GLB
#   CHARACTER_DIFFUSE       path to the extracted diffuse texture (run extract_glb_images.py first, or
#                           point this at whatever PNG holds the node's colour bake)
#   CHARACTER_REGIONS_JSON  path to a JSON object mapping node index (string) -> region label, e.g.
#                           {"0": "torso", "9": "head"}. A node missing from this map falls back to
#                           "region<N>" rather than crashing, so an incomplete map degrades gracefully.
#   CHARACTER_OUT_PREFIX    output .bin path prefix (default public/head/sdf-surfaces)
#   CHARACTER_WORKDIR       passed through to build_head_surface.py (default work/head)
GLB_PATH = Path(os.environ.get('CHARACTER_GLB', str(ROOT / 'public/mesh/girl-character-baseline.glb')))
_diffuse_value = os.environ.get('CHARACTER_DIFFUSE', str(ROOT / 'work/baseline-textures/01-texture_diffuse.png'))
EMBEDDED_MATERIALS = _diffuse_value.lower() in {'embedded', 'glb'}
DIFFUSE_PATH = None if _diffuse_value.lower() in {'none', 'neutral', '', 'embedded', 'glb'} else Path(_diffuse_value)
OUT_PREFIX = os.environ.get('CHARACTER_OUT_PREFIX', str(ROOT / 'public/head/sdf-surfaces'))
_regions_file = os.environ.get('CHARACTER_REGIONS_JSON')
if _regions_file:
    NODE_REGION = {int(k): v for k, v in json.loads(Path(_regions_file).read_text()).items()}
else:
    NODE_REGION = {0:'overalls',1:'skin',2:'boots',3:'skin',4:'boots',5:'pouches',6:'canister',7:'pouches',
                   8:'katana',9:'hair',10:'knee-pads',11:'boots',12:'knee-pads',13:'boots',14:'gloves',
                   15:'skin'}
# node -> cell size in metres. The head needs 1.5 mm because an eyelid's relief is 1.6 mm at an 8 mm
# scale; nothing else on the figure carries a feature that fine, and 2.0 mm keeps p95 near 1.17 mm.
# The head needs 1.5 mm because an eyelid's relief is 1.6 mm at an 8 mm scale. The trousers are the
# largest surface on the figure and carry nothing near that fine, so they take 2.5 mm to keep the file
# from doubling for no measured gain.
CELL = {9: 0.0015, 0: 0.0025}
DEFAULT_CELL = 0.0020
_cell_sizes_file = os.environ.get('CHARACTER_CELL_SIZES_JSON')
if _cell_sizes_file:
    CELL.update({int(k): float(v) for k, v in json.loads(Path(_cell_sizes_file).read_text()).items()})

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

_image_cache = {}
def embedded_image(image_index):
    cached = _image_cache.get(image_index)
    if cached is not None:
        return cached
    image = g['images'][image_index]
    view = g['bufferViews'][image['bufferView']]
    start = view.get('byteOffset', 0)
    data = BIN[start:start + view['byteLength']]
    decoded = np.asarray(Image.open(BytesIO(data)).convert('RGB'))
    _image_cache[image_index] = decoded
    return decoded

TEXTURE_SOURCE = [texture.get('source') for texture in g.get('textures', [])]

def sample_texture(texture_info, uv, fallback):
    if texture_info is None:
        return np.broadcast_to(np.asarray(fallback, dtype=np.float64), (len(uv), len(fallback))).copy()
    source = TEXTURE_SOURCE[texture_info['index']]
    image = embedded_image(source)
    height, width = image.shape[:2]
    x = np.clip((uv[:, 0] % 1.0 * width).astype(int), 0, width - 1)
    y = np.clip((uv[:, 1] % 1.0 * height).astype(int), 0, height - 1)
    return image[y, x].astype(np.float64)

if DIFFUSE_PATH is not None:
    diffuse = np.asarray(Image.open(DIFFUSE_PATH).convert('RGB'))
    TH, TW = diffuse.shape[:2]
else:
    diffuse = None
    TH = TW = 0

# Optional trailing "xN" argument scales every cell size, for measuring what triangle budget actually
# costs in fidelity rather than assuming it.
scale = 1.0
keep_head = False
argv = list(sys.argv[1:])
if argv and argv[-1] == 'keephead':
    keep_head = True
    argv.pop()
if argv and argv[-1].startswith('x'):
    scale = float(argv.pop()[1:])
nodes = [int(a) for a in argv] or [9]
entries, blocks = [], []
for node in nodes:
    # The head is exempt from coarsening. It is the only part whose detail the eye tracks -- the
    # measured comparison across detail levels shows the nose and eyes softening first, while boots,
    # trousers and armour hold up -- so scaling it with everything else spends the budget in the one
    # place it is worst spent.
    cell = CELL.get(node, DEFAULT_CELL) * (1.0 if node == 9 and keep_head else scale)
    workdir = Path(os.environ.get('CHARACTER_WORKDIR', str(ROOT / 'work/head')))
    subprocess.run([sys.executable, str(Path(__file__).resolve().parent / 'build_head_surface.py'),
                     str(node), str(cell)],
                   check=True, cwd=ROOT, stdout=subprocess.DEVNULL, env={**os.environ, 'CHARACTER_GLB': str(GLB_PATH)})
    V = np.load(workdir/'V.npy').astype(np.float64)
    T = np.load(workdir/'T.npy').astype(np.int64)
    prims = g['meshes'][g['nodes'][node]['mesh']]['primitives']
    P = np.concatenate([acc(p['attributes']['POSITION']) for p in prims]).astype(np.float64)
    material_info = None
    if EMBEDDED_MATERIALS:
        colours = []
        roughness = []
        metalness = []
        material_records = []
        for primitive in prims:
            uv = acc(primitive['attributes']['TEXCOORD_0']).astype(np.float64)
            material = g['materials'][primitive.get('material', 0)]
            pbr = material.get('pbrMetallicRoughness', {})
            base_factor = np.asarray(pbr.get('baseColorFactor', [1, 1, 1, 1])[:3], dtype=np.float64)
            base_srgb = sample_texture(pbr.get('baseColorTexture'), uv, [255, 255, 255]) / 255.0
            base_linear = np.where(base_srgb <= 0.04045, base_srgb / 12.92,
                                   ((base_srgb + 0.055) / 1.055) ** 2.4)
            colours.append(np.clip(base_linear * base_factor, 0, 1))

            mr = sample_texture(pbr.get('metallicRoughnessTexture'), uv, [255, 255, 255]) / 255.0
            roughness.append(np.clip(mr[:, 1] * float(pbr.get('roughnessFactor', 1)), 0, 1))
            metalness.append(np.clip(mr[:, 2] * float(pbr.get('metallicFactor', 1)), 0, 1))
            material_records.append({
                'materialIndex': int(primitive.get('material', 0)),
                'baseColorFactor': [float(value) for value in pbr.get('baseColorFactor', [1, 1, 1, 1])],
                'metallicFactor': float(pbr.get('metallicFactor', 1)),
                'roughnessFactor': float(pbr.get('roughnessFactor', 1)),
                'doubleSided': bool(material.get('doubleSided', False)),
                'alphaMode': material.get('alphaMode', 'OPAQUE'),
                'alphaCutoff': float(material.get('alphaCutoff', 0.5)),
                'emissiveFactor': [float(value) for value in material.get('emissiveFactor', [0, 0, 0])],
                'normalScale': float(material.get('normalTexture', {}).get('scale', 1)),
                'occlusionStrength': float(material.get('occlusionTexture', {}).get('strength', 1)),
                'hasBaseColorTexture': 'baseColorTexture' in pbr,
                'hasMetallicRoughnessTexture': 'metallicRoughnessTexture' in pbr,
                'hasNormalTexture': 'normalTexture' in material,
                'hasOcclusionTexture': 'occlusionTexture' in material,
                'hasEmissiveTexture': 'emissiveTexture' in material,
            })
        source_colour = np.concatenate(colours)
        source_roughness = np.concatenate(roughness)
        source_metalness = np.concatenate(metalness)
        material_info = {
            **material_records[0],
            'roughnessMedian': float(np.median(source_roughness)),
            'roughnessP25': float(np.percentile(source_roughness, 25)),
            'roughnessP75': float(np.percentile(source_roughness, 75)),
            'metalnessMedian': float(np.median(source_metalness)),
            'metalnessP25': float(np.percentile(source_metalness, 25)),
            'metalnessP75': float(np.percentile(source_metalness, 75)),
            'surfaceColourEncoding': 'linear RGB sampled from embedded baseColor texture',
        }
    elif diffuse is None:
        # Geometry-only mode for references whose materials/textures may be measured but not copied.
        # White vertex colour leaves the independently-authored Three.js material un-tinted.
        COL = np.full((len(V), 3), 255, dtype=np.uint8)
    else:
        UV = np.concatenate([acc(p['attributes']['TEXCOORD_0']) for p in prims]).astype(np.float64)
        source_srgb = diffuse[np.clip((UV[:,1] % 1.0 * TH).astype(int), 0, TH-1),
                              np.clip((UV[:,0] % 1.0 * TW).astype(int), 0, TW-1)].astype(np.float64) / 255.0
        source_colour = np.where(source_srgb <= 0.04045, source_srgb / 12.92,
                                 ((source_srgb + 0.055) / 1.055) ** 2.4)
    if EMBEDDED_MATERIALS or diffuse is not None:
        hash_cell = 0.005
        lo = np.minimum(V.min(0), P.min(0)) - hash_cell
        buck = defaultdict(list)
        for i, key in enumerate(map(tuple, np.floor((P - lo)/hash_cell).astype(np.int64))):
            buck[key].append(i)
        vk = np.floor((V - lo)/hash_cell).astype(np.int64)
        linear = np.zeros((len(V), 3))
        for index in range(len(V)):
            key = tuple(vk[index]); candidates = []
            for dx in (-1,0,1):
                for dy in (-1,0,1):
                    for dz in (-1,0,1):
                        candidates += buck.get((key[0]+dx,key[1]+dy,key[2]+dz), [])
            if not candidates:
                continue
            candidates = np.asarray(candidates)
            nearest = candidates[np.argsort(np.linalg.norm(P[candidates]-V[index], axis=1))[:4]]
            linear[index] = source_colour[nearest].mean(axis=0)
        COL = np.clip(np.round(linear*255), 0, 255).astype(np.uint8)
    fn = np.cross(V[T[:,1]]-V[T[:,0]], V[T[:,2]]-V[T[:,0]])
    N = np.zeros_like(V)
    for c in range(3): np.add.at(N, T[:,c], fn)
    N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)
    # Keep the configured cell exactly enough to reproduce the builder's grid origin. Rounding a
    # measured 1.504 mm cell to 1.50 mm shifted the recovered grid and created 110,695 apparent cell
    # collisions on one node even though Surface Nets emits one vertex per active cell by construction.
    entry = {'node': node, 'region': NODE_REGION.get(node, f'region{node}'), 'cellMillimetres': round(cell*1000, 6),
             'vertexCount': int(len(V)), 'indexCount': int(T.size)}
    if material_info is not None:
        entry['material'] = material_info
    entries.append(entry)
    body = bytearray()
    body += V.astype('<f4').tobytes()
    body += N.astype('<f4').tobytes()
    body += COL.tobytes()
    while len(body) % 4: body += b'\x00'
    body += T.ravel().astype('<u4').tobytes()
    blocks.append(bytes(body))
    print(f"node {node:2d} {NODE_REGION.get(node, f'region{node}'):10s} cell {cell*1000:.1f} mm  "
          f"{len(V):,} verts  {T.shape[0]:,} tris  {len(body)/1e6:.2f} MB")

hdr = json.dumps(entries, separators=(',', ':')).encode()
out = bytearray(b'HEDS') + struct.pack('<II', 3, len(hdr)) + hdr
while len(out) % 4: out += b'\x00'
for b in blocks: out += b
suffix = '' if scale == 1.0 else f'-x{scale:g}'
if keep_head:
    suffix += '-sharpface'
dest = Path(f'{OUT_PREFIX}{suffix}.bin')
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_bytes(out)
resolved_dest = dest.resolve()
try:
    _shown = resolved_dest.relative_to(ROOT)
except ValueError:
    _shown = resolved_dest
print(f"\nwrote {_shown}  {len(out)/1e6:.2f} MB  ({len(entries)} node(s))")
