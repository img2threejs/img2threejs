# Three.js texture & PBR reference (notebooklm research, 2026-07-22)

Doc-grounded (threejs.org MeshPhysicalMaterial / MeshStandardMaterial / Texture / CanvasTexture /
DataTexture / manual §Textures). Drives `analyze_texture.py` (finish classification + recipe) and
the emitted `makeProceduralTextureSet` / material presets in `generate_threejs_factory.py`.

## Colour space — get this right or everything looks wrong
- **Albedo / `map` / `emissiveMap` / `sheenColorMap` → `THREE.SRGBColorSpace`.**
- **Data maps (`roughnessMap`, `metalnessMap`, `normalMap`, `aoMap`, `bumpMap`, `displacementMap`,
  `thicknessMap`, `anisotropyMap`) → `THREE.NoColorSpace`** (linear data, never sRGB).
- `CanvasTexture`: `flipY = true` (default), auto `needsUpdate` on construct.
- `DataTexture`: `flipY = false` (default) — memory (0,0) is bottom-left. Set `needsUpdate = true`.
- After changing `wrapS/wrapT` on an already-used texture you MUST set `needsUpdate = true`.
- `aoMap`/`lightMap` read UV channel 1 — geometry needs a 2nd UV set, or set `texture.channel = 1`.
- Tiling: `wrapS = wrapT = THREE.RepeatWrapping` + `repeat.set(u,v)`; set `anisotropy =
  renderer.capabilities.getMaxAnisotropy()` so oblique angles stay sharp.

## Per-finish material recipe (MeshPhysicalMaterial scalars)
| finishClass | metalness | roughness | clearcoat | ccRoughness | transmission | ior | envMapIntensity | anisotropy | author maps |
|---|---|---|---|---|---|---|---|---|---|
| `gemstone` (translucent quartz/glass) | 0.0 | 0.05 | 1.0 | 0.0 | 1.0 | 1.54 | 1.0 | 0.0 | map(gradient), thicknessMap |
| `gem-metal` (chromed doppler blade — our blend) | 0.75 | 0.14 | 0.6 | 0.06 | 0.0 | 1.5 | 1.3 | 0.0 | map(gradient+smoke), roughnessMap |
| `painted-metal` (glossy paint) | 0.0 | 0.5 | 1.0 | 0.05 | 0.0 | 1.5 | 1.0 | 0.0 | map, roughnessMap, clearcoatRoughnessMap |
| `worn-composite` (aged rubber/grip) | 0.0 | 0.9 | 0.0 | 0.0 | 0.0 | 1.5 | 0.5 | 0.0 | map(mottled), roughnessMap, bumpMap |
| `brushed-steel` | 1.0 | 0.35 | 0.0 | 0.0 | 0.0 | 1.5 | 1.0 | 1.0 | anisotropyMap, roughnessMap, normalMap |

> Note: pure `gemstone` (transmission 1.0) is see-through glass. A painted/anodised metallic blade
> (CS Doppler) is opaque — use `gem-metal` (metalness + gradient map + clearcoat, no transmission).

## Procedural map generation (in code)
- **CanvasTexture** — draw to an `HTMLCanvasElement`: linear/radial gradients across palette stops
  (gemstone/doppler), layered low-alpha blobs for smoke, per-pixel mottle noise for worn finishes,
  thin horizontal streaks for brushed metal. `new THREE.CanvasTexture(canvas)` then set `colorSpace`.
- **DataTexture** — push computed noise/height into a `Uint8Array`, `new THREE.DataTexture(arr,w,h,
  THREE.RGBAFormat, THREE.UnsignedByteType)`, `colorSpace = NoColorSpace`, `needsUpdate = true`.
- **Height → Normal (no native helper — compute it):** Sobel/neighbour-difference the height field →
  `R = (dx*strength + 1)*0.5*255`, `G = (dy*strength + 1)*0.5*255`, `B = 255` (up +Z), A = 255.
- **Independent channels** (skill rule, confirmed): never alias one map into another — albedo,
  roughness, normal/height, AO are separate signals with separate frequency content.

## Analysis → which finishClass?
`analyze_texture.py` classifies a reference crop by pure-stdlib stats:
- saturation + hue spread → chromatic gemstone vs neutral metal/grey.
- luminance gradient across the crop → `gradient` finish (doppler) vs flat.
- local variance / mottle → `worn-composite`.
- directional (horizontal) high-freq streaks → `brushed-steel`.
- specular-highlight fraction (very bright pixels) → metalness proxy.
Output: `{finishClass, palette:[stops], mottle, gradientAxis, metalnessHint}` → material recipe.
