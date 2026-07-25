# 3D Technical Reference — CS2 to Three.js Mapping
# Từ điển Kỹ thuật 3D — Mapping CS2 sang Three.js

> NotebookLM Research ID: `30ec3dba-5a20-4980-8886-ce89b506b634`
> Last updated: July 2026

---

## Table of Contents

1. [PBR Property Map](#1-pbr-property-map)
2. [Material Preset Recipes](#2-material-preset-recipes)
3. [CS2 Finish Styles → PBR Profiles](#3-cs2-finish-styles--pbr-profiles)
4. [Wear System Bridge](#4-wear-system-bridge)
5. [Component Recipe Database](#5-component-recipe-database)
6. [Vocabulary Glossary](#6-vocabulary-glossary)

---

## 1. PBR Property Map

MeshPhysicalMaterial properties with Three.js bindings, value ranges, and performance cost.

### Core Properties

| Property | Type | Default | Range | Visual Effect | Perf Cost |
|----------|------|---------|-------|---------------|-----------|
| `color` | Color | `0xffffff` | — | Base surface color (Albedo) | Low |
| `metalness` | Float | `0.0` | 0.0–1.0 | 0=dielectric (plastic, wood), 1=metal | Low |
| `roughness` | Float | `1.0` | 0.0–1.0 | 0=mirror-smooth, 1=completely rough/matte | Low |
| `envMapIntensity` | Float | `1.0` | 0.0–∞ | Strength of environment map reflections | Low |
| `flatShading` | Boolean | `false` | — | Faceted vs smooth shading | Low |
| `side` | Enum | `FRONT` | — | FRONT, BACK, DOUBLE sided rendering | Low |

### Advanced Properties

| Property | Type | Default | Range | Visual Effect | Perf Cost |
|----------|------|---------|-------|---------------|-----------|
| `clearcoat` | Float | `0.0` | 0.0–1.0 | Extra glossy layer on top (car paint effect) | Medium |
| `clearcoatRoughness` | Float | `0.0` | 0.0–1.0 | Roughness of the clearcoat layer | Medium |
| `transmission` | Float | `0.0` | 0.0–1.0 | Physical light transmission (NOT opacity) | **Very High** |
| `thickness` | Float | `0.0` | 0.0–∞ | Thickness for transmission absorption | High |
| `ior` | Float | `1.5` | 1.0–2.33 | Index of Refraction — light bending angle | Medium |
| `sheen` | Float | `0.0` | 0.0–1.0 | Soft back-scatter (velvet/fabric effect) | Medium |
| `sheenRoughness` | Float | `0.0` | 0.0–1.0 | Roughness of sheen layer | Medium |
| `sheenColor` | Color | `0x000000` | — | Color tint of sheen effect | Medium |
| `iridescence` | Float | `0.0` | 0.0–1.0 | Thin-film interference (oil slick, soap bubble) | High |
| `iridescenceIOR` | Float | `1.3` | 1.0–3.0 | IOR of the iridescent layer | High |
| `iridescenceThicknessRange` | [min,max] | `[100,400]` | nm | Thickness range for iridescence | High |
| `anisotropy` | Float | `0.0` | 0.0–1.0 | Stretched highlights (brushed metal, CD) | High |
| `anisotropyRotation` | Float | `0.0` | 0–2π | Direction of anisotropic highlights (radians) | High |
| `attenuationColor` | Color | `0xffffff` | — | Color absorption in transmission | High |
| `attenuationDistance` | Float | `0.5` | 0.0–∞ | Distance for color absorption | High |

### Code: Property Reference

```typescript
// Basic PBR
const basic = new THREE.MeshPhysicalMaterial({
  color: 0xaaaaaa,
  metalness: 1.0,
  roughness: 0.05,
});

// Clearcoat (car paint, lacquered wood)
const clearcoat = new THREE.MeshPhysicalMaterial({
  color: 0x222222,
  metalness: 0.9,
  roughness: 0.3,
  clearcoat: 1.0,
  clearcoatRoughness: 0.1,
});

// Transmission (glass, crystal)
const glass = new THREE.MeshPhysicalMaterial({
  color: 0xffffff,
  metalness: 0.0,
  roughness: 0.0,
  transmission: 1.0,
  thickness: 0.5,
  ior: 1.5,
});

// Sheen (fabric, velvet)
const fabric = new THREE.MeshPhysicalMaterial({
  color: 0x334455,
  metalness: 0.0,
  roughness: 0.9,
  sheen: 1.0,
  sheenRoughness: 0.3,
  sheenColor: 0xffffff,
});

// Iridescence (patina, oil slick)
const iridescent = new THREE.MeshPhysicalMaterial({
  color: 0x445566,
  metalness: 1.0,
  roughness: 0.2,
  iridescence: 0.5,
  iridescenceIOR: 1.3,
  iridescenceThicknessRange: [100, 400],
});

// Anisotropy (brushed steel)
const brushed = new THREE.MeshPhysicalMaterial({
  color: 0x888888,
  metalness: 1.0,
  roughness: 0.3,
  anisotropy: 0.8,
  anisotropyRotation: Math.PI / 4,
});
```

---

## 2. Material Preset Recipes

Quick-copy recipes for common materials in CS2 items.

### Polished Metal (Chrome, New Steel)

```typescript
new THREE.MeshPhysicalMaterial({
  color: 0xaaaaaa,
  metalness: 1.0,
  roughness: 0.05,
  envMapIntensity: 1.5,
});
```

### Brushed Steel (Satin Finish)

```typescript
new THREE.MeshPhysicalMaterial({
  color: 0x888888,
  metalness: 1.0,
  roughness: 0.3,
  anisotropy: 0.8,
  anisotropyRotation: Math.PI / 4,
});
```

### Matte Plastic (G10 Handle, Polymer)

```typescript
new THREE.MeshPhysicalMaterial({
  color: 0x1a1a1a,
  metalness: 0.0,
  roughness: 0.75,
  ior: 1.45,
});
```

### Gunmetal (Dark Steel, Blued)

```typescript
new THREE.MeshPhysicalMaterial({
  color: 0x3a3a3a,
  metalness: 1.0,
  roughness: 0.45,
  envMapIntensity: 1.2,
});
```

### Glass (Scope Lens, Sight)

```typescript
new THREE.MeshPhysicalMaterial({
  color: 0xffffff,
  metalness: 0.0,
  roughness: 0.0,
  transmission: 1.0,
  thickness: 0.5,
  ior: 1.5,
});
```

### Wood (Rifle Stock, Handle Scale)

```typescript
new THREE.MeshPhysicalMaterial({
  color: 0x6f4e37,
  metalness: 0.0,
  roughness: 0.6,
  clearcoat: 0.2, // Light varnish
  clearcoatRoughness: 0.3,
});
```

### Fabric (Paracord, Lanyard, Grip Tape)

```typescript
new THREE.MeshPhysicalMaterial({
  color: 0x2d2d2d,
  metalness: 0.0,
  roughness: 0.9,
  sheen: 1.0,
  sheenRoughness: 0.3,
  sheenColor: 0xffffff,
});
```

### Rubber (Grip, Bumper)

```typescript
new THREE.MeshPhysicalMaterial({
  color: 0x111111,
  metalness: 0.0,
  roughness: 0.85,
  clearcoat: 0.1,
});
```

---

## 3. CS2 Finish Styles → PBR Profiles

How each CS2 finish style maps to Three.js material properties.

### Anodized

| Property | Value | Notes |
|----------|-------|-------|
| metalness | 1.0 | Full metallic |
| roughness | 0.05–0.15 | Very smooth, mirror-like |
| clearcoat | 0.3–0.5 | Protective glossy layer |
| iridescence | 0.0–0.2 | Slight color shift at angles |
| color | Tinted (varies) | Blue, gold, red, etc. |

**Wear behavior**: Color coating peels from edges, revealing base metal underneath.
**Examples**: Fade, Blue Steel, Case Hardened (anodized variant)

```typescript
// Anodized Blue
new THREE.MeshPhysicalMaterial({
  color: 0x2244aa,
  metalness: 1.0,
  roughness: 0.08,
  clearcoat: 0.4,
  clearcoatRoughness: 0.05,
});
```

### Anodized Multicolored

| Property | Value | Notes |
|----------|-------|-------|
| metalness | 1.0 | Full metallic |
| roughness | 0.05–0.10 | Ultra smooth |
| iridescence | 0.3–0.5 | Strong multi-color shift |
| iridescenceIOR | 1.5–2.0 | Higher IOR = more color |

**Wear behavior**: Gloss fades before base metal shows. Color gradient shifts with wear.
**Examples**: Fade (100%), Doppler (all phases)

```typescript
// Anodized Multicolored (Fade)
new THREE.MeshPhysicalMaterial({
  color: 0xff6600,
  metalness: 1.0,
  roughness: 0.05,
  iridescence: 0.4,
  iridescenceIOR: 1.8,
  iridescenceThicknessRange: [100, 500],
});
```

### Custom Paint Job

| Property | Value | Notes |
|----------|-------|-------|
| metalness | 0.0 | Non-metallic (painted over) |
| roughness | 0.3–0.5 | Semi-gloss paint |
| clearcoat | 0.2–0.4 | Protective clear coat |
| clearcoatRoughness | 0.1–0.2 | Smooth clear coat |

**Wear behavior**: Paint scratches from edges and high-friction areas, revealing metal.
**Examples**: Slaughter, Crimson Web, Asiimov, Hyper Beast

```typescript
// Custom Paint Job (Asiimov)
new THREE.MeshPhysicalMaterial({
  map: asiimovTexture, // Custom paint texture
  metalness: 0.0,
  roughness: 0.4,
  clearcoat: 0.3,
  clearcoatRoughness: 0.15,
});
```

### Gunsmith

| Property | Value | Notes |
|----------|-------|-------|
| metalness | 0.7–0.9 | Mostly metallic |
| roughness | 0.2–0.4 | Moderate roughness |
| clearcoat | 0.0–0.1 | Minimal clear coat |

**Wear behavior**: Combined paint fading + metal exposure. Chemical treatment look.
**Examples**: Night, Corticera, Guardian

```typescript
// Gunsmith
new THREE.MeshPhysicalMaterial({
  color: 0x333344,
  metalness: 0.8,
  roughness: 0.3,
  envMapIntensity: 1.0,
});
```

### Hydrographic

| Property | Value | Notes |
|----------|-------|-------|
| metalness | 0.0 | Non-metallic (film transfer) |
| roughness | 0.5–0.7 | Matte to semi-gloss |
| clearcoat | 0.1–0.2 | Light protective layer |

**Wear behavior**: Large patches peel off, exposing base material.
**Examples**: Boreal Forest, Safari Mesh, Arctic Wolf

```typescript
// Hydrographic (Boreal Forest)
new THREE.MeshPhysicalMaterial({
  map: borealTexture, // Camo pattern texture
  metalness: 0.0,
  roughness: 0.6,
  clearcoat: 0.15,
});
```

### Patina

| Property | Value | Notes |
|----------|-------|-------|
| metalness | 0.9–1.0 | Full metallic |
| roughness | 0.2–0.4 | Moderate |
| iridescence | 0.3–0.6 | Color-shifting oxidation |
| iridescenceIOR | 1.3–1.5 | Subtle color |

**Wear behavior**: Color changes (darkens/shifts) rather than peeling. Oxidation effect.
**Examples**: Case Hardened, Blue Steel, Damascus Steel

```typescript
// Patina (Case Hardened / Blue Gem)
new THREE.MeshPhysicalMaterial({
  color: 0x667788,
  metalness: 1.0,
  roughness: 0.25,
  iridescence: 0.5,
  iridescenceIOR: 1.4,
  envMapIntensity: 1.3,
});
```

### Solid Color

| Property | Value | Notes |
|----------|-------|-------|
| metalness | 0.0 | Non-metallic |
| roughness | 0.3–0.5 | Semi-gloss |
| clearcoat | 0.2–0.3 | Protective layer |

**Wear behavior**: Scratches reveal underlying metal at edges.
**Examples**: Urban Masked, Jungle Spray

```typescript
// Solid Color
new THREE.MeshPhysicalMaterial({
  color: 0x556633,
  metalness: 0.0,
  roughness: 0.4,
  clearcoat: 0.25,
});
```

### Sprayed

| Property | Value | Notes |
|----------|-------|-------|
| metalness | 0.0 | Non-metallic |
| roughness | 0.6–0.8 | Rough, uneven |
| clearcoat | 0.0 | No clear coat |

**Wear behavior**: Heavy scratching, base metal clearly visible at edges.
**Examples**: Scorched, Sand Dune, Dragon Lore (Spray variant)

```typescript
// Sprayed
new THREE.MeshPhysicalMaterial({
  map: sprayTexture,
  metalness: 0.0,
  roughness: 0.7,
  clearcoat: 0.0,
});
```

---

## 4. Wear System Bridge

How CS2 Float Value translates to Three.js shader parameters.

### Float Value → Visual Effect Mapping

| Wear Tier | Float Range | Visual Characteristics |
|-----------|-------------|----------------------|
| Factory New (FN) | 0.00–0.07 | Pristine. Almost no visible wear. |
| Minimal Wear (MW) | 0.07–0.15 | Minor scratches at edges. Very clean. |
| Field-Tested (FT) | 0.15–0.38 | Noticeable scratches, fading at edges. |
| Well-Worn (WW) | 0.38–0.45 | Significant wear, paint loss. |
| Battle-Scarred (BS) | 0.45–1.00 | Heavy damage, deep scratches, large blemishes. |

### Per-Skin Float Clipping

CS2 restricts float ranges per skin via `wear_remap_min` / `wear_remap_max` in `items_game.txt`:

```json
{
  "wear_remap_min": 0.00,
  "wear_remap_max": 0.80
}
```

This means a skin with `wear_remap_max: 0.80` can NEVER reach Battle-Scarred (0.45+).

### Three.js Wear Implementation

#### Approach 1: Texture Layer Blending (Recommended)

```typescript
// Wear as uniform parameter
const material = new THREE.ShaderMaterial({
  uniforms: {
    baseColorMap: { value: baseTexture },
    wearMap: { value: wearTexture },
    normalMap: { value: normalTexture },
    wearNormalMap: { value: wearNormalTexture },
    float: { value: 0.223 }, // The item's float value
    roughnessClean: { value: 0.3 },
    roughnessWorn: { value: 0.7 },
  },
  vertexShader: `...`,
  fragmentShader: `
    uniform sampler2D baseColorMap;
    uniform sampler2D wearMap;
    uniform float float;
    varying vec2 vUv;

    void main() {
      vec4 clean = texture2D(baseColorMap, vUv);
      vec4 worn = texture2D(wearMap, vUv);

      // Wear alpha: 0 at edges, 1 in center
      // Float value controls blend
      float wearAlpha = smoothstep(0.0, 0.5, float);

      vec4 color = mix(clean, worn, wearAlpha);
      gl_FragColor = color;
    }
  `,
});
```

#### Approach 2: Vertex-Based Wear Zones

```typescript
// Store wear sensitivity per vertex
const wearAttribute = new THREE.BufferAttribute(
  new Float32Array(vertexCount),
  1
);

// 0.0 = protected (won't wear), 1.0 = high wear (edges, corners)
geometry.setAttribute('wearSensitivity', wearAttribute);

// In shader: multiply wear sensitivity with float value
```

#### Approach 3: Three.js Built-in Blending

```typescript
// For simple wear without custom shaders
const material = new THREE.MeshPhysicalMaterial({
  map: baseColorTexture,
  roughnessMap: wearRoughnessTexture, // Rougher where worn
  metalnessMap: wearMetalnessTexture, // More metallic where paint removed
  normalMap: combinedNormalTexture,    // Scratch normals
  metalness: 0.0,                     // Base: non-metallic (painted)
  roughness: 0.4,                     // Base: semi-gloss
});
```

### Paint Channels (RGB Masks)

CS2 uses RGB channels to control different wear behaviors:

| Channel | Controls | Three.js Mapping |
|---------|----------|------------------|
| Red | Wear position (edges first) | Alpha mask for base→worn blend |
| Green | Roughness variation | RoughnessMap intensity |
| Blue | Color tint / pattern offset | Color modulation |

### Spatial Wear & Damage Glossary

#### 1. Core Industry Terms (Thuật Ngữ Cốt Lõi)

| EN Term | VI Term | Definition & 3D Application | Practical Example |
| :--- | :--- | :--- | :--- |
| **Surface Imperfections** | **Khuyết điểm bề mặt** | Microscopic surface variations that break up uniform specular reflections. Essential for photorealism. | Fingerprints on glass, dust layers on screens, micro-scratches on polished metal. |
| **Weathering / Aging** | **Phong hóa / Lão hóa** | Surface degradation caused by environmental exposure (rain, temperature, sunlight) over time. | Oxidation on metals (rust/patina), sun-fading on paint, moss growth on damp grips. |
| **Wear and Tear** | **Hao mòn sử dụng** | Physical damage from human handling or operational friction. | Blunted knife tips, hand-worn glossy grips, paint peeling on high-friction weapon parts. |
| **Edge Wear** | **Mòn cạnh viền** | Damage concentrated along the sharp boundaries, corners, or protrusions of a model. | Paint scraping off along the slide edges of a Glock or the guard corners of a Bowie knife. |
| **Procedural Grunge** | **Cáu bẩn thuật toán** | Procedurally generated grime or dirt, often mask-blended using Ambient Occlusion (AO) or curvature. | Dust settling in recessed grooves, gun grease building up in hard-to-clean weapon crevices. |

#### 2. Detailed Classification & PBR Map Impact

| Damage Type | Albedo (Color) Impact | Roughness Map Impact | Metalness Map Impact | Normal/Height Map Impact |
| :--- | :--- | :--- | :--- | :--- |
| **Oxidation / Rust / Patina** | Shifts to orange-brown (rust) or dark-green (patina). | Increases significantly (0.8 - 1.0) due to dry, rough microfacets. | Drops to 0.0 (conductive metal turns into non-conductive oxide). | Adds high-frequency noise, pitting, and scaling surface volume. |
| **Scratches / Dents** | Exposes base metal underneath (usually light grey/silver). | Decreases at scratch center (reveals shiny substrate); increases at scratch lip. | Increases to 1.0 at scratch center if painted coating is scraped off. | Creates sharp, linear indentations or local coordinate normals. |
| **Smudges / Fingerprints** | Negligible change. | Localized increases (0.4 - 0.6) where natural body oils distort reflections. | Unchanged. | Unchanged. |
| **Dust / Dirt / Mud** | Matches environmental soil colors (tan, brown, grey). | Becomes completely rough and diffuse (0.9 - 1.0). | Drops to 0.0 (non-metallic soil coating). | Smooths out micro-structures (dust) or adds thick volumes (caked mud). |

#### 3. Spatial Modifiers

| EN Term | VI Term | Definition |
| :--- | :--- | :--- |
| **Tip-to-spine gradient** | **Gradient từ mũi đến sống dao** | Damage concentration decreases gradually from the tip towards the mid-body. |
| **Edge-heavy wear** | **Mòn tập trung ở cạnh** | Scratches or paint loss occurring exclusively along high-exposure outer edges. |
| **Friction-point wear** | **Mòn điểm ma sát** | Concentrated wear at mechanical contact points (magazine slides, knife locks). |
| **Speckled / Spotted** | **Dạng đốm** | Scattered, point-like occurrences of rust or paint chipping. |
| **Exposed core** | **Lộ lõi** | Complete removal of an outer layer, showing the raw underlying material. |

#### 4. Technical Shader Representation (Three.js TSL)

To describe complex damage distributions (e.g. "Rust covering 50% from tip to spine"), use spatial coordinates and noise mixers in your shader logic:

```javascript
import { Fn, uv, smoothstep, mix, color, float, noise } from 'three/tsl';

const wearLogic = Fn(({ baseColor, rustColor, uvCoord, floatVal }) => {
    // 1. Gradient Mask: Rust 50% from tip (uv.y = 0.0) to spine (uv.y = 0.5)
    const gradientMask = smoothstep(float(0.0), float(0.5), uv().y);
    
    // 2. Add Stochastic Noise to prevent flat gradients
    const noiseMask = noise(uv().multiply(10.0));
    const finalMask = mix(gradientMask, noiseMask, float(0.2));

    // 3. Blend PBR parameters based on mask
    const finalAlbedo = mix(baseColor, rustColor, finalMask);
    const finalRoughness = mix(float(0.2), float(0.9), finalMask); // Polish to Rust
    const finalMetalness = mix(float(1.0), float(0.0), finalMask); // Metal to Oxide

    return { finalAlbedo, finalRoughness, finalMetalness };
});
```


### CS2 → Three.js Wear Pipeline

```
Float Value (0.000-1.000)
  │
  ├─→ wear_remap_min/max (per-skin clamp)
  │     │
  │     └─→ Clamped float for this item
  │
  ├─→ RGB Wear Mask
  │     ├─→ Red: Base→Worn alpha blend
  │     ├─→ Green: Roughness modulation
  │     └─→ Blue: Color desaturation
  │
  └─→ Shader Uniforms
        ├─→ float: Wear amount (0-1)
        ├─→ roughnessClean: Starting roughness
        ├─→ roughnessWorn: Ending roughness
        └─→ metalnessWorn: Exposed metal metalness
```

---

## 5. Component Recipe Database

### Knife Components

#### Blade Types

| Type | Three.js Geometry | Proportions (W:L) | PBR (Metal/Rough) | Notes |
|------|-------------------|-------------------|-------------------|-------|
| Clip-point | `ExtrudeGeometry` | 1:3.5 | 1.0 / 0.15 | Notch cutout at tip |
| Drop-point | `ExtrudeGeometry` | 1:3 | 0.9 / 0.2 | Smooth spine curve |
| Tanto | `ExtrudeGeometry` | 1:3 | 1.0 / 0.1 | Angular chisel tip |
| Karambit | `ExtrudeGeometry` | 1:2 (curved) | 1.0 / 0.05 | Extreme curve |
| Falchion | `LatheGeometry` / `Extrude` | 1:4 | 1.0 / 0.15 | Wide, slightly curved |
| Gut Hook | `Extrude` + CSG `Subtract` | 1:2.5 | 1.0 / 0.3 | Hook via boolean |

#### Handle Construction

| Part | Geometry | PBR | Notes |
|------|----------|-----|-------|
| Tang Frame | `BoxGeometry` | Metal 1.0 / 0.4 | Structural core |
| Scales (×2) | `ExtrudeGeometry` | Non-metal 0.0 / 0.75 | G10, Micarta, Wood |
| Screws (×4) | `CylinderGeometry` | Metal 1.0 / 0.35 | Phillips head |
| Guard | `BoxGeometry` | Metal 1.0 / 0.3 | Cross guard or none |
| Pommel | `CylinderGeometry` | Metal 1.0 / 0.35 | End cap |
| Lanyard | `TorusGeometry` | Non-metal 0.0 / 0.9 | Paracord loop |

### Pistol Components

| Part | Geometry | Proportions | Boolean Ops |
|------|----------|-------------|-------------|
| Slide | `BoxGeometry` | 1:0.3:4 | Subtract: ejection port, rail |
| Frame | `ExtrudeGeometry` + `Box` | Complex | Union: grip; Subtract: mag well |
| Barrel | `CylinderGeometry` | r:0.05, L:5 | Subtract: inner bore |
| Trigger Guard | `ExtrudeGeometry` | Thin arc | Union: to frame |
| Magazine | `BoxGeometry` | 0.8:0.2:2.5 | Subtract: feed lips |
| Sights (×2) | `BoxGeometry` | Small | Union: to slide |
| Grip Texture | Normal map | — | NOT geometry |

### Rifle Components (AK-47, M4, AWP)

| Part | Geometry | Notes |
|------|----------|-------|
| Receiver (Upper) | `BoxGeometry` | Main body, rails on top |
| Receiver (Lower) | `BoxGeometry` | Trigger group, mag well |
| Barrel | `CylinderGeometry` | Long cylinder, fluting optional |
| Handguard | `BoxGeometry` / `CylinderGeometry` | Rail system, M-LOK slots |
| Stock | `BoxGeometry` | Fixed, folding, or collapsible |
| Magazine | `BoxGeometry` | Curved (AK) or straight (M4) |
| Pistol Grip | `ExtrudeGeometry` | Ergonomic profile |
| Scope (AWP) | `TubeGeometry` + `SphereGeometry` | Cylinder + lens halves |
| Muzzle Device | `CylinderGeometry` | Flash hider or suppressor |
| Bolt Carrier | `BoxGeometry` | Visible in ejection port |

### Boolean Operations Guide

| Operation | Library | Use Case |
|-----------|---------|----------|
| Subtract | `three-bvh-csg` | Holes, cutouts, slots, ejection ports |
| Union | `three-bvh-csg` | Merging components (frame + grip) |
| Intersect | `three-bvh-csg` | Complex shape intersections |

### Edge Treatment

| Zone | Treatment | How |
|------|-----------|-----|
| Cutting edge | Hard crease | `geometry.computeVertexNormals()` + manual normal adjustment |
| Spine-to-plate | Hard edge | Split vertices at seam |
| Sawback teeth | All hard | No smoothing groups |
| Guard corners | Hard | BoxGeometry naturally hard |
| Handle scales | Smooth | Subdivision or smooth normals |
| Grip checkering | Normal map | NOT geometry |

---

## 6. Vocabulary Glossary

See `3D_Vocabulary_CS2_Dictionary.md` for the full bilingual (EN/VI) glossary covering:

1. **Core 3D Modeling** — Vertex, Edge, Face, Mesh, Topology, Primitives
2. **UV Mapping & Texturing** — UV Coordinates, Texture Maps, Mipmapping
3. **PBR Materials** — PBR Workflow, Metalness, Roughness, Fresnel
4. **CS2 Specific** — Skin System, Wear System, Pattern System, Economy
5. **3D File Formats** — OBJ, FBX, glTF, MDL, VTF, VMT
6. **Animation & Rigging** — Skeleton, Bones, IK/FK, Morph Targets
7. **Rendering & Optimization** — Draw Calls, LOD, Batching
8. **Three.js / Web 3D** — Scene, Camera, Materials, Geometry
9. **CS2 Workshop** — Submission pipeline, QC requirements

---

## Sources

- Three.js MeshPhysicalMaterial documentation
- CS2 Workshop Tools (Valve Developer Community)
- CSFloat Blog: Float Values Technical Deep Dive
- Steam Community Workshop guides
- Three.js community discussions
- CS2 skin finish style documentation

---

*Generated via NotebookLM research — notebook ID: `30ec3dba-5a20-4980-8886-ce89b506b634`*
