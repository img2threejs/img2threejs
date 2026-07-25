# CS2 Research Distill — Consolidated Findings from 13 NotebookLM Sessions

> Sources: 13 notebooks, ~130 sources total
> Generated: 2026-07-24
> Notebook IDs: 3302413b, 7b0376c6, ed67e020, 8430ab72, e20b3968, f783b110, 7b0376c6, 3302413b, 3a1c3eb9, 3957b5dc, e9c7dbee, 370faecc, ee512cf3

---

## 1. Knife Anatomy — Actionable Geometry Rules

| Knife Type | Signature Geometry | Three.js Primitive | Critical Detail |
|---|---|---|---|
| **Karambit** | Concave Hawkbill blade + Safety Ring at pommel | `ExtrudeGeometry` (curved profile) | Ring diameter ≈ 20mm, finger hole |
| **Bowie** | Massive Clip-point + brass crossguard | `ExtrudeGeometry` + `BoxGeometry` (guard) | Clip notch depth ≈ 15% blade length |
| **Skeleton Knife** | Full tang with large index finger hole, tape wrap | `ExtrudeGeometry` (perforated) | Hole diameter ≈ 18mm |
| **Balisong** | Two handles + pivot pins + stop pin + latch | `CylinderGeometry` (pins) | Pivot pin is rotation axis; detent ball holds blade closed |
| **M9 Bayonet** | Clip-point + sawback serrations (11 teeth) | `ExtrudeGeometry` + `BoxGeometry` ×11 | Tooth spacing ≈ 14mm, depth ≈ 15mm |

### Universal Knife Details (from all anatomy notebooks)
- **Jimping**: Notches on spine for thumb traction → Normal map, NOT geometry
- **Choil**: Notch near heel for sharpening → Small boolean subtract
- **Handle Belly**: Swell curving outward from centerline for palm grip
- **Tang types**: Full tang (visible at handle edges), Hidden tang (rat-tail), Skeletonized tang
- **Handle materials**: G10 (roughness 0.75), Micarta (0.7), Wood (0.6, clearcoat 0.2), Paracord wrap (roughness 0.9)

---

## 2. Pistol Anatomy — Key Technical Findings

### Mechanism Differences (Critical for animation)
| Pistol | Mechanism | Key Components |
|---|---|---|
| **Desert Eagle** | Gas-operated, rotating bolt | Piston, 3-lug bolt, polygonal rifling |
| **Tec-9** | Blowback | Tubular bolt inside steel-stamped upper receiver |
| **Glock/USP** | Striker-fired | No external hammer, polymer frame |
| **R8 Revolver** | Double-action | 8-shot cylinder, ejector rod, cylinder release latch |

### Material Mapping
- **Polymer frames** (Glock, P250, Tec-9): `metalness: 0.0, roughness: 0.75`
- **Steel slides/barrels**: `metalness: 1.0, roughness: 0.3`
- **Picatinny rails**: Standard 7/8" spacing → Box geometry with boolean slots
- **Threaded barrels**: For suppressor attachment → Cylinder with helical thread normal

---

## 3. Rifle/SMG/Sniper Key Details

### AK-47 vs M4A4 vs AWP
| Component | AK-47 | M4A4 | AWP |
|---|---|---|---|
| Receiver | Stamped steel, curved magazine | Forged aluminum, straight mag | Bolt-action, massive receiver |
| Handguard | Wooden/polymer, ventilated holes | M-LOK rail system | Minimal, barrel exposed |
| Stock | Fixed wood/polymer | Collapsible (6 positions) | Adjustable cheek rest |
| Magazine | 30-rd curved | 30-rd straight | 5-rd box |

### SMG Specifics
- **MP5**: Roller-delayed blowback, slim handguard, drum mag option
- **P90**: Bullpup, top-mounted magazine, 50-rd capacity
- **PP-Bizon**: Helical magazine under barrel

---

## 4. PBR Properties — Strict CS2 Ranges

### Binary Metalness Rule (CRITICAL)
> **CS2 uses a strict two-bit metalness model**: surfaces are either `0.0` (dielectric: wood, polymer, fabric) or `1.0` (conductive: steel, gold, chrome). Intermediate values cause lighting artifacts.

### Albedo Ranges
| Material | Albedo RGB Range | Metalness | Roughness |
|---|---|---|---|
| **Dielectric (polymer)** | [30, 240] per channel | 0.0 | 0.4–0.8 |
| **Conductive (steel)** | [180, 255] per channel | 1.0 | 0.2–0.5 |
| **Chrome/Anodized** | [180, 255] | 1.0 | 0.05–0.15 |
| **Patina (Case Hardened)** | [100, 200] | 0.80–0.95 | 0.20–0.35 |

### Environment Requirements
- **HDR cubemap** mandatory for anodized/chrome finishes
- `renderer.toneMapping = THREE.ACESFilmicToneMapping`
- `.colorSpace = THREE.SRGBColorSpace` for albedo/emissive maps
- `NoColorSpace` for normalMap, roughnessMap, metalnessMap

---

## 5. Finish Styles — Technical Recipes

| Finish | Metalness | Roughness | Key Property | Wear Behavior |
|---|---|---|---|---|
| **Anodized** | 0.90–1.00 | 0.05–0.15 | `envMapIntensity: 1.5–2.5` | Color peels from edges |
| **Anodized Multicolored** | 0.90–1.00 | 0.03–0.10 | Iridescence + noise ramp | Gloss fades, gradient shifts |
| **Custom Paint Job** | 0.0 | 0.3–0.5 | `clearcoat: 0.2–0.4` | Paint scratches from edges |
| **Hydrographic** | 0.0 | 0.5–0.7 | Dip-film swirl print | Large patches peel |
| **Patina** | 0.80–0.95 | 0.20–0.35 | Iridescence 0.3–0.6 | Color shifts, not peeling |
| **Gunsmith** | 0.7–0.9 | 0.2–0.4 | Patina + paint blend | Combined fading |
| **Solid Color** | 0.0 | 0.3–0.5 | `clearcoat: 0.2–0.3` | Scratches at edges |
| **Sprayed** | 0.0 | 0.6–0.8 | No clearcoat | Heavy base metal exposure |

---

## 6. Wear System — Shader Implementation

### Float Value → Visual Mapping
| Tier | Range | Roughness Delta | Metalness Delta | Albedo Shift |
|---|---|---|---|---|
| FN | 0.00–0.07 | +0.0 | +0.0 | None |
| MW | 0.07–0.15 | +0.1 | +0.05 | Slight darkening at edges |
| FT | 0.15–0.38 | +0.2 | +0.15 | Noticeable fading |
| WW | 0.38–0.45 | +0.3 | +0.3 | Significant desaturation |
| BS | 0.45–1.00 | +0.5 | +0.5 | Heavy oxidation appearance |

### Geometric-Driven Wear Masks
- **Convex curvature map** → concentrates scratches on sharp outer edges
- **Ambient Occlusion (AO)** → protects recesses from peeling; accumulates grime/dark oxidation
- **Paint Seed deterministic transform**: `A = T₂ × R × S × T₁` (centered translation → base scale → rotation → correction translation)

---

## 7. Pattern System — Technical Details

### Paint Seed / Pattern Index
- Integer 0–999, initialized at drop time
- Controls UV offset via deterministic affine matrix
- **Fade**: Gradient sequence Yellow → Orange → Pink → Purple; percentage = how much gradient visible from tip
- **Doppler**: Fixed indices (#415 Ruby, #416 Sapphire, #417 Black Pearl); iridescent reflections via 3D noise color-ramp lookup
- **Case Hardened**: Randomized charcoal-heated look; Blue Gem = mostly blue on playside (rare patterns exist in database)

### UV Mapping Rules for CS2
- Asymmetrical UV: playside gets larger UV island for higher detail
- LOD0 mesh required for inspect/viewmodel (maximum polygon density)
- UV scale formula: for Hydrographic/Anodized Multicolored → `s = weapon_length × 0.027777778`

---

## 8. Attachments & Special Items

### StatTrak™
- **On firearms**: Independent mesh module (ST_counter) with **orange LED emissive** (`#FF4500`)
- **On knives**: Dynamic tangent-space bump map procedurally engraves kill count into blade
- Drop rate: ~10% of normal drops

### Stickers
- Max 5 slots per weapon
- Parameters: `offset_x`, `offset_y` (float32), `rotation` (0–360°), `scale`
- Wear formula: `Base_Wear × (1.0 - (α_channel × UnWearStrength))`
- Scrape levels: 100% → 75% → 50% → 25%

### Charms
- Independent 3D mesh, attached via skeletal spring-joint
- Physics: spring-damper `τ = -kθ - cω` for swing motion
- Clasp bone attachment point

---

## 9. 3D Reconstruction Pipeline Insights

### Channel Packing (Source 2 → Three.js)
- Roughness + Metalness packed in single texture (R + G channels)
- Must unpack in shader: `roughness = texture2D(rmMap, uv).g; metalness = texture2D(rmMap, uv).b;`

### Structured Image Description Protocol (from 370faecc)
1. **Observation before inference** — state observable facts separately from inferences
2. **Controlled vocabulary** — use 3D terms, never "nice/sleek/aggressive"
3. **3D object-space, not 2D image-space** — describe by front/back/lateral/proximal
4. **Structural decomposition**: Macro → Meso → Micro
5. **Visual Triplets**: Subject-Predicate-Object for spatial relationships
6. **Single-image limits** — explicitly state what's occluded/hidden/uncertain

### 3D Types Taxonomy (from ee512cf3)
- **PBR workflow**: Albedo + Normal + Roughness + Metallic + AO
- **UV efficiency target**: 85–95%
- **Texel density**: 512–1024 px/m for hero assets
- **Web optimization**: glTF/GLB format, Draco compression (90–95% reduction), KTX2 for textures
- **LOD chains**: 50% reduction per level
- **Collision mesh**: Simplified primitives (`cdt_` prefix)

---

## 10. Texture Extraction Pipeline (from 3a1c3eb9)

### CSGO-API Metadata
- `weapon_id`, `paint_index`, `min_float`, `max_float` from ByMykel/CSGO-API
- CDN image URL for reference screenshots

### Valve Texture Format
- VTF → PNG conversion via Source2Viewer-CLI
- Channel packing: Roughness (R) + Metalness (G) in single file
- Must tangle channels for Three.js MeshPhysicalMaterial

### Legal Note
- Extracted textures are Valve IP — stay in local `cs2_textures/` workspace
- Never commit or redistribute
- "Extractive use" doctrine may apply for personal/research use

---

## 11. CS2 Detection Signals (for detect_cs2.py)

Visual markers that identify a CS2 weapon/item from a 2D image:

| Signal | Weight | Detection Method |
|---|---|---|
| **Weapon silhouette** (blade, rifle, pistol shape) | High | Aspect ratio + contour analysis |
| **CS2 skin patterns** (Doppler gradient, Fade, Crimson Web) | High | Color distribution + pattern recognition |
| **Steam Workshop aesthetics** (clean render, white/grey background) | Medium | Background color histogram |
| **StatTrak™ module** (orange LED counter) | High | Small emissive orange rectangle |
| **CS2-specific wear patterns** (edge wear on blade, patina on receiver) | Medium | Edge contrast analysis |
| **Knife guard/handle construction** (crossguard, tang, pommel) | Medium | Component decomposition |

---

*Distilled from 13 NotebookLM sessions covering: knives (3302413b), pistols (7b0376c6), rifles (ed67e020), heavy (8430ab72), snipers (e20b3968), SMGs (f783b110), gloves (d353b31a), texture extraction (3a1c3eb9), item attributes (3957b5dc), skin specs (e9c7dbee), image description (370faecc), 3D taxonomy (ee512cf3), and current session (30ec3dba).*
