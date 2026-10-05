# Three.js material rendering reference

This is the human-readable companion to `material-reference.json`. The JSON file is canonical for
machine lookup; this document explains how to interpret it when reconstructing a model from images.

## Source-backed Three.js contract

The following are renderer contracts from official Three.js/Khronos sources, not material presets:

- `MeshStandardMaterial` and `MeshPhysicalMaterial` use metallic-roughness PBR.
- `metalness` and `roughness` are in `[0, 1]`; roughness and metalness maps multiply their scalar.
- `MeshPhysicalMaterial` adds clearcoat, sheen, transmission/volume, IOR, iridescence, anisotropy
  and dispersion at additional GPU cost.
- `ior` is constrained to `[1, 2.333]`; transmission uses `opacity = 1` and needs meaningful
  geometry thickness or a thickness map.
- PBR validation needs a meaningful environment. Prefilter HDR environments with `PMREMGenerator`.
- Colour textures use sRGB; mathematical data maps stay untagged/linear (`NoColorSpace`).
- Switching texture slots or material features may trigger shader recompilation. Wait for texture
  upload before capturing review evidence.

Primary sources are listed with NotebookLM source IDs in `material-reference.json`. Context7 was
also used to check the current `/mrdoob/three.js` API and official examples before freezing the code
patterns below.

## Research source hierarchy

1. Three.js official documentation, source and examples define the runtime API.
2. Khronos glTF core and material extensions define portable metallic-roughness, sheen,
   transmission and volume semantics.
3. Primary research/reference implementations explain phenomena not modeled directly by stock
   Three.js, especially skin subsurface scattering, hair scattering and visual material recognition.
4. Adobe's PBR Guide and Google's Filament documentation supply established production guardrails.

The library intentionally excludes forum recipes from its canonical values. Official Three.js
examples are API evidence, not automatically physically correct presets; for example, a demo named
"car paint" does not override the layer rule that intact pigment/binder is dielectric.

## How to read the numbers

Every per-material value is an **inferred starting prior** constrained by the official property
semantics. It is not a claim that all objects of that material have one roughness. The image analyst
must select a family, subtype and finish, apply the prior, then fit colour/variation within the range
against the admitted component crops.

| Canonical material | metalness | roughness | advanced starting values | Essential evidence |
| --- | ---: | ---: | --- | --- |
| Human skin | 0 | 0.52 | clearcoat 0.12, IOR 1.4 | neutral + grazing; pores without plastic shine |
| Human hair | 0 | 0.45 | anisotropy 0.65 | directional highlight follows strands/clumps |
| Stylized fur | 0 | 0.78 | sheen 0.45 | grazing response plus broken silhouette |
| Woven matte fabric | 0 | 0.85 | sheen 0.65 | woven normal and broad grazing sheen |
| Silk/satin | 0 | 0.32 | sheen 0.85, anisotropy 0.45 | moving directional highlight |
| Velvet | 0 | 0.72 | sheen 1.0 | dark frontal body with soft bright rim |
| Matte leather | 0 | 0.62 | clearcoat 0.08 | irregular grain and smoother worn edges |
| Polished steel/chrome | 1 | 0.12 | — | sharp neutral environment reflection |
| Brushed steel | 1 | 0.35 | anisotropy 0.85 | elongated reflection aligned with brushing |
| Aluminium | 1 | 0.30 | anisotropy 0.20 | bright neutral satin conductor |
| Copper | 1 | 0.28 | — | red-orange conductor reflection; patina is separate |
| Brass/bronze | 1 | 0.30 | — | yellow-brown conductor response |
| Gold | 1 | 0.22 | — | yellow reflected energy, not yellow diffuse paint |
| Painted/coated metal | 0 | 0.45 | clearcoat 0.75 | dielectric paint; metallic chips need a mask/region |
| Metallic car paint | 0 | 0.32 | clearcoat 1.0, flake micro-normal | metallic sparkle at grazing; dielectric film under clear |
| Glossy plastic | 0 | 0.28 | clearcoat 0.20, IOR 1.5 | neutral specular over coloured diffuse body |
| Matte plastic | 0 | 0.68 | IOR 1.5 | broad highlight; molded/stipple microstructure |
| Matte rubber | 0 | 0.88 | IOR 1.48 | weak broad highlight with grip microtexture |
| Unfinished wood | 0 | 0.72 | anisotropy 0.12 | grain direction affects colour and roughness |
| Varnished wood | 0 | 0.42 | clearcoat 0.75 | wood grain under a separate coating reflection |
| Natural stone | 0 | 0.78 | optional clearcoat | multi-scale mineral pattern and relief |
| Glazed ceramic | 0 | 0.26 | clearcoat 0.70, IOR 1.5 | hard dielectric body with glaze reflection |
| Clear glass | 0 | 0.05 | transmission 1, IOR 1.5 | background distortion, reflection and thickness |
| Frosted glass | 0 | 0.52 | transmission 0.85, IOR 1.5 | blurred transmission and etched micro-normal |
| Quartz-like gemstone | 0 | 0.06 | transmission 0.9, IOR 1.54 | facets and thickness-dependent transmission |

Use the JSON ranges, not only this default column. Worn, wet, oxidized, dusty, scratched, polished
or stylized surfaces may sit elsewhere in the range or require a new finish record.

## Current Three.js implementation pattern

Create candidates from the selected profile, then attach independently authored maps. Do not bake
lighting into `map`, and do not assign sRGB to mathematical maps:

```ts
import * as THREE from "three";

const loader = new THREE.TextureLoader();

function loadColorMap(url: string): THREE.Texture {
  const texture = loader.load(url);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function loadDataMap(url: string): THREE.Texture {
  const texture = loader.load(url);
  texture.colorSpace = THREE.NoColorSpace;
  return texture;
}

const material = new THREE.MeshPhysicalMaterial({
  color: 0xffffff, // image-derived tint; keep white when map already owns colour
  map: loadColorMap("base-color.png"),
  roughness: 0.85,
  roughnessMap: loadDataMap("roughness.png"),
  metalness: 0.0,
  metalnessMap: loadDataMap("metalness.png"),
  normalMap: loadDataMap("normal.png"),
  sheen: 0.65,
  sheenRoughness: 0.75,
});
```

The scalar and map multiply one another, so a white data map preserves the scalar while black
suppresses it. When a texture slot is attached after first compilation, set `material.needsUpdate =
true`. Wait for all texture loads before recording comparison evidence.

Use a stable HDR environment for every PBR comparison:

```ts
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0; // calibrate once, never per material

const pmrem = new THREE.PMREMGenerator(renderer);
const environment = pmrem.fromEquirectangular(hdrTexture).texture;
scene.environment = environment;
pmrem.dispose();
```

For glass, use `MeshPhysicalMaterial({ transmission: 1, opacity: 1, metalness: 0, ior: 1.5,
thickness })`. `thickness` is in world-space units, so it cannot be a universal preset: derive it
from model scale or a thickness map. A background/environment and closed, correctly oriented
geometry are part of the material test.

## Material creation recipes

### Skin

- Use de-lit image colour for `map`; never bake facial shadow into albedo.
- Use independent pore/wrinkle normal and low-amplitude roughness variation.
- Keep metalness at zero. Clearcoat is a restrained wet-oil approximation, not a plastic shell.
- Geometry owns facial planes, folds and wrinkles that alter silhouette.
- If likeness requires real subsurface transport, record that `MeshPhysicalMaterial` is insufficient
  rather than compensating with orange albedo.

### Hair and fur

- Geometry clumps/cards own silhouette and parting; maps own strand-scale variation.
- Author an anisotropy map with direction matching UV/tangent flow for hair.
- Fur and velvet use sheen for grazing response, but still require geometry where fibres change the
  visible outline.
- Validate under a moving grazing light. A static beauty view cannot prove directional response.

### Fabric and leather

- Fabric is a dielectric: metalness zero, high roughness, weave normal, optional sheen.
- Satin/silk needs lower roughness and directional response; matte cotton does not.
- Leather needs irregular grain and wear-aware roughness. Do not identify leather from brown colour.
- Large folds, seams, hems, stitches and panel thickness are geometry or explicit relief, not albedo.

### Bare metal

- Set metalness to one for a clean exposed conductor; drive visible tint through base colour/map.
- A PMREM-filtered environment is mandatory for useful validation.
- Roughness controls reflection spread; anisotropy plus a directional map models brushing.
- Oxidation, dirt, paint and patina are separate dielectric/transition regions. Do not average them
  into one fractional-metalness surface unless the pixels are genuinely unresolved at target scale.

### Painted metal

- Intact paint is dielectric (`metalness` near zero) even though its substrate is metal.
- Use clearcoat for lacquer/automotive coatings and an independent clearcoat roughness map.
- Chips exposing substrate need a region or metalness mask. Paint colour alone cannot reveal the
  underlying metal.

### Plastic and rubber

- Both are dielectrics. Distinguish them through roughness, deformation/context and microstructure,
  not black colour or highlight presence alone.
- Glossy plastic may use a restrained clearcoat; matte rubber generally does not.
- Add molded stipple/grip normals and wear-specific roughness where visible.

### Wood, stone and ceramic

- Wood grain must align across colour, roughness and normal channels.
- Varnished wood is a layered dielectric: grain body plus a clear coating.
- Stone uses multi-scale albedo and relief; polished stone remains dielectric.
- Glazed ceramic uses a coating reflection; chips should expose a rougher body region.

### Glass and gemstones

- Use `transmission`, not low opacity, for physically based transparency.
- Keep opacity at one; derive thickness from geometry scale and/or `thicknessMap`.
- Provide a PMREM environment and a background that can be refracted.
- Frosting is primarily roughness plus etched micro-normal, not grey albedo.
- Opaque gem-like paint is a coating, not transmissive quartz; classify by light response.

## Required controlled renders

| View | What it proves |
| --- | --- |
| `albedo-unlit` | De-lit colour, pattern and region assignment |
| `neutral-studio` | Balanced diffuse/specular response |
| `grazing` | Roughness, normal strength, sheen, anisotropy and coating |
| `environment-reflection` | Conductor and polished-layer response |
| `backlight-transmission` | Transmission, volume, absorption and thickness |
| `reference-beauty` | Final agreement at the solved camera |

The component microscope must use the full scene's visible footprint. Isolation renders are useful
for structural inspection but can expose surfaces hidden in the reference and produce false material
scores.

## Common classification failures

- Black fabric, leather, rubber and plastic classified only from albedo.
- Brass, gold and metallic yellow paint collapsed into one `gold` preset.
- Painted metal assigned `metalness = 1` across intact paint.
- Hair made matte and uniform, so it reads as rubber or clay.
- Fabric given a flat normal and no sheen, so it reads as plastic.
- Clear glass implemented with opacity, so it lacks volume/refraction.
- Environment or exposure changed to hide a wrong material instead of fixing the material.
- Raw reference highlights baked into albedo and then lit a second time.

If controlled views disagree, retain the disagreement in evidence and refine the classification or
request input. Do not let a high beauty similarity score override a failed material hard gate.
