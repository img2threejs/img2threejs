# Three.js for skin and cloth — what the engine actually does

Distilled from the **source of the exact version the showcase pins, `three@0.169.0`**, not from the
docs site. Every number below is a `file:line` citation into `node_modules/three/src`, because the
docs page omits the clamps, the foldings and the energy terms — and those are precisely what decides
whether an authored number reaches the pixel.

Read this before choosing a material for skin or cloth. Its purpose is to stop the two failure modes
that cost the most: **authoring a value the engine ignores**, and **exposing one degree of freedom
twice as if it were two**.

---

## 1. Sheen — the cloth carrier

### The mechanism

| Fact | Source |
|---|---|
| BRDF is Estevez & Kulla 2017 "Production Friendly Microfacet Sheen", `D_Charlie` distribution | `shaders/ShaderChunk/lights_physical_pars_fragment.glsl.js:320,337,345` |
| The sheen term is `sheenColor * (D * V)` — a **multiply** by colour | `…lights_physical_pars_fragment.glsl.js:348` |
| `sheenColor` default is **`0x000000`** | `materials/MeshPhysicalMaterial.js:52` |
| `sheenRoughness` default is **`1.0`** | `materials/MeshPhysicalMaterial.js:54` |
| The uniform is uploaded **only when `sheen > 0`** | `renderers/webgl/WebGLMaterials.js:406` |
| `sheen` is folded into the colour: `uniforms.sheenColor = sheenColor × sheen` | `renderers/webgl/WebGLMaterials.js:408` |
| `sheen` crossing zero bumps `material.version` → shader recompile | `materials/MeshPhysicalMaterial.js` getter/setter on `_sheen` |

### Three consequences that must be encoded, not remembered

**S-1. `sheen` alone does nothing.** Default `sheenColor` is black and the term is a multiply, so
`sheen: 0.65` with an untouched `sheenColor` contributes exactly `0`. A fabric profile that specifies
`sheen` and omits `sheenColor` is specifying nothing.

> Status in this repo: `generate_threejs_factory.py` already defaults `sheenColor` to `#ffffff`
> (grep `sheenColor:` — it is the file's only occurrence, in the emitted
> `MeshPhysicalMaterial` constructor)
> rather than three's black, so emitted code escapes the trap. But **no registry profile declares
> `sheenColor`** — `fabric.woven-matte` and `hair.human.code-only` both give `sheen` without it. The
> tint therefore comes from a generator fallback and is never reference-derived. Fix by adding
> `sheenColor` to the profile, not by changing the fallback.

**S-2. `sheen` and `sheenColor` are ONE degree of freedom, not two.** They are multiplied before
upload, so `sheen: 1.0, sheenColor: #808080` is bit-identical to `sheen: 0.5, sheenColor: #ffffff`.
A spec that extracts both from the reference as independent evidence is extracting the same number
twice and will produce contradictory-looking priors that render identically. **Extract one: the
sheen tint. Keep `sheen` as a scalar strength with a fixed authored default.**

**S-3. Turning sheen on makes the cloth DARKER, by a computable amount.**

```glsl
float sheenEnergyComp = 1.0 - 0.157 * max3( material.sheenColor );
outgoingLight = outgoingLight * sheenEnergyComp + sheenSpecularDirect + sheenSpecularIndirect;
```
`ShaderLib/meshphysical.glsl.js:205,207`

With full white sheen the diffuse base is scaled by `0.843` — **a 15.7% darkening** — before the
sheen lobe is added back. So a garment whose base colour is matched to the reference *and then* given
sheen renders darker than the reference at every non-grazing angle. The correction is predictable:

```
effective_sheen_strength = sheen × max(sheenColor.r, .g, .b)
base_darkening           = 0.157 × effective_sheen_strength
compensated_base_colour  = target_colour / (1 - base_darkening)
```

This is a **gate-able number**, not a matter of taste: given the authored sheen, the expected
darkening is known in advance, so a cloth region that is darker than the reference by roughly
`0.157 × strength` is diagnosable as "sheen applied without base compensation" rather than as a
generic colour miss.

### Choosing sheenRoughness

`sheenRoughness` is the Charlie distribution width, so it maps to how tight the grazing rim is:

| Fabric | `sheenRoughness` | reads as |
|---|---|---|
| cotton, linen, wool, felt | `0.7 – 1.0` | broad, soft, almost no visible rim |
| brushed / matte synthetic | `0.5 – 0.7` | a wide band |
| satin, silk | `0.15 – 0.35` | a narrow bright rim — the satin signature |

Low `sheenRoughness` on a low-poly garment concentrates the rim onto a few facets and reads as
plastic. Prefer the high end unless the reference clearly shows a satin rim.

---

## 2. Clearcoat — the skin carrier

### The mechanism

| Fact | Source |
|---|---|
| `clearcoatF0` is **fixed at `0.04`** and is not authorable | `ShaderChunk/lights_physical_fragment.glsl.js:56` |
| `clearcoatRoughness` is **clamped up**: `max(clearcoatRoughness, 0.0525)` | `…lights_physical_fragment.glsl.js:72` |
| Geometric roughness is **added**: `clearcoatRoughness += geometryRoughness` | `…lights_physical_fragment.glsl.js:73` |
| The same addition applies to base roughness: `material.roughness += geometryRoughness` | `…lights_physical_fragment.glsl.js:9` |
| `geometryRoughness = max(max(dxy.x, dxy.y), dxy.z)` from normal derivatives | `…lights_physical_fragment.glsl.js:6` |
| `clearcoat` crossing zero bumps `material.version` | `MeshPhysicalMaterial.js` `_clearcoat` setter |
| `clearcoatRoughness` default `0.0`, `clearcoatNormalScale` default `(1,1)` | `MeshPhysicalMaterial.js:27,29` |

### Two consequences

**C-1. `clearcoatRoughness` below `0.0525` is a lie.** Authoring `0.0` and authoring `0.05` produce
the identical render. A validator must not accept a value below the clamp as if it were meaningful,
and evidence extraction must not report one.

**C-2. Faceting roughens skin automatically, and this is the low-poly coupling.** `geometryRoughness`
comes from screen-space normal derivatives, so **flat shading raises it at every facet edge**. A
clearcoat tuned on smooth geometry will read blurrier on the same figure flat-shaded, at no change to
the material. Consequences for this pipeline, which deliberately uses a low-poly faceted language:

- Skin clearcoat should be authored on the **final tessellation**, never on a smooth proxy.
- Two components with the same skin material but different facet density will not match. That is an
  engine property, not a bug, and it is why skin cannot be gated on absolute specular response.
- It is another reason the standing rule holds: **never gate roughness from a reference with unknown
  lighting — report only.** Here even the *geometry* moves the number.

### Why clearcoat and not transmission for skin

Skin's real signature at this fidelity is a **broad soft dielectric highlight sitting over a warm
diffuse base** — which is exactly a low-strength, mid-roughness clearcoat over a warm base colour.
`clearcoat 0.1–0.3` with `clearcoatRoughness 0.3–0.5` produces it with two numbers and no maps.

---

## 3. Transmission and thickness — rejected for skin, with the reason

`transmission` is a **screen-space refraction** approximation: it needs `transparent: true`, a
transmission render target, and it samples the backbuffer. It is a glass model, not a subsurface
model.

| Fact | Source |
|---|---|
| `thickness` default `0`, `attenuationDistance` default `Infinity` | `MeshPhysicalMaterial.js:59,61` |
| `attenuationColor` default white | `MeshPhysicalMaterial.js:62` |
| The generator already forces `transparent` when `transmission > 0` | `generate_threejs_factory.py`, emitted `transparent:` in the `MeshPhysicalMaterial` constructor |

On a closed opaque body mesh, transmission produces a glassy figure, not fleshy skin — and it costs a
full extra render target. The registry already states the honest limit: *"MeshPhysicalMaterial has no
true multilayer skin subsurface scattering"* (`docs/materials/material-reference.json`, `skin.human`).

**Rule: skin never sets `transmission`.** Approximating SSS with a glass model and presenting the
result as reconstruction is invention. Thin-membrane translucency — an earlobe, a nostril rim, a
webbed finger — is the only legitimate use, is a *separate* material on a *separate* thin component,
and must be declared as an approximation.

---

## 4. Degrees of freedom that are secretly the same

Exposing either pair twice creates specs that look richer than they are and priors that contradict
each other while rendering identically.

| Pair | Relation | Source |
|---|---|---|
| `sheen` × `sheenColor` | multiplied into one uniform before upload | `WebGLMaterials.js:408` |
| `ior` ↔ `reflectivity` | `reflectivity = clamp(2.5(ior−1)/(ior+1), 0, 1)`; writing `reflectivity` writes `ior = (1+0.4r)/(1−0.4r)` | `MeshPhysicalMaterial.js:34–44` |

**Author `ior`, never `reflectivity`.** `ior` is the physical quantity, has a meaningful range
(skin ≈ 1.33–1.5, most fabric ≈ 1.45–1.55), and `reflectivity` is a derived view of it.

---

## 5. Base-material facts that bite cloth specifically

| Fact | Default | Why it matters for a garment | Source |
|---|---|---|---|
| `side` | `FrontSide` | A garment shell open at hem, cuff or collar shows **nothing** through the opening — backfaces are culled, so the inside of the sleeve is a hole. Any garment with an open boundary needs `DoubleSide`, and `shadowSide` set so it still casts. | `Material.js:24` |
| `vertexColors` | `false` | Must be enabled explicitly. Materials are shared by id, so **clone before enabling** or every component sharing the material gets tinted — the generator already does this for `rootTipGradient`. | `Material.js:25` |
| `flatShading` | `false` | The low-poly language depends on it, and it raises `geometryRoughness` (§2 C-2). | `Material.js:382` (toJSON) |
| `roughness` / `metalness` | `1.0` / `0.0` | A fabric left at default roughness `1.0` is already correct-ish; skin is not. | `MeshStandardMaterial.js:20,21` |
| `polygonOffset` | `false` | Cloth offset from skin by a small clearance can still z-fight where the offset approaches zero. Prefer real geometric clearance (`standProud`) over depth tricks. | `Material.js` |

---

## 6. Geometry: which component for which garment part

The trap this section exists to prevent is reaching for a named convenience geometry because its name
matches the noun.

| Need | Use | Do **not** use, and why |
|---|---|---|
| Sleeve, trouser leg, anything tapering along a curved spine | **`tapered-sweep`** (this repo's primitive; parallel transport framing) | `TubeGeometry` — constant radius, reads as a noodle. `ExtrudeGeometry` with a path — Frenet frames flip 180° at an inflection. |
| A garment body that must follow the torso's own section | **offset surface derived from the covered components' ring stack**, then `standProud` | `LatheGeometry` — a revolve is circular before scaling, so it cannot express a torso's front/back asymmetry. Authoring `rx ≠ rz` on a lathe describes a surface the geometry does not have (this repo already gates that case). |
| Hem, waistband, collar edge — a crisp boundary | **plain intersection against a slab** (`max`, not `smin`) | A smooth union / `smin` — it rolls the edge, and a rolled hem is visibly not a hem. |
| Flat trim, a patch, a badge | `ShapeGeometry` / `ExtrudeGeometry` from a `Shape` | `PlaneGeometry` + alpha — needs a texture this pipeline cannot emit. |
| Repeated fasteners: buttons, studs, eyelets | `InstancedMesh` (this repo's `instanced-cluster`) | Individual meshes — cost with no fidelity gain. |
| Cloth folds and wrinkles | **geometry**, at meso scale, or nothing | `normalMap` — no textures. `displacementMap` — same. The registry's own `fabric.woven-matte` note already says *"Large folds and hems belong in geometry"*. |

**`side: DoubleSide` is a geometry decision as much as a material one:** if a garment is authored as a
closed offset volume rather than an open shell, `FrontSide` is correct and cheaper. Choose closed
volumes where possible and reserve `DoubleSide` for genuinely open boundaries.

---

## 7. What no Three.js feature can give this pipeline

Stated so it is never planned around:

- **No true skin subsurface scattering.** `MeshPhysicalMaterial` has no multilayer SSS. `transmission`
  is a glass model (§3).
- **No cloth simulation.** Three.js has no cloth solver, and a single reference image contains no
  motion and no material parameters to fit one against.
- **No per-fibre or per-thread detail without textures.** Sheen is the whole of the woven cue
  available to a code-only pipeline.
- **No alpha-cut silhouette detail.** Which is why `plane-card` is rejected for hair and for cloth
  trim alike.

---

## 8. The authored-value checklist

Before a skin or cloth number reaches a spec:

- [ ] Does the engine read it at all, or is it gated behind another value being `> 0`? (`sheen`,
      `clearcoat`, `transmission`, `iridescence`, `anisotropy` all gate their own uniform block.)
- [ ] Is it clamped? (`clearcoatRoughness` ≥ `0.0525`.)
- [ ] Is something added to it at shade time? (`geometryRoughness` → `roughness`, `clearcoatRoughness`.)
- [ ] Is it multiplied into another value before upload, making the pair one degree of freedom?
      (`sheen` × `sheenColor`.)
- [ ] Is it a derived view of a different property? (`reflectivity` of `ior`.)
- [ ] Does it require a map this pipeline cannot emit? If yes, the value is decoration.
- [ ] Does turning it on change the base layer? (`sheenEnergyComp` darkens by `0.157 × strength`.)
