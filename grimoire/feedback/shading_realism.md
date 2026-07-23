# Material And Lighting Realism

Use this reference whenever the model silhouette is acceptable but the render still looks unlike the source image.

## Common Failure Pattern

A procedural object often fails after the shape pass because the render has:

- one flat albedo color per material
- no roughness variation or cavity response
- no normal/bump/displacement response on surfaces that should be tactile
- missing local overrides such as moss, stains, edge wear, dirt, sap, rust, dust, scorch, or faded zones
- lighting that is only ambient or too evenly exposed
- weak contact shadows, no rim separation, and no tone mapping/exposure target

Treat this as a `LookDev Reset`, not a geometry problem.

## Material-Pass Requirements

Before implementing or accepting `material-pass`, the spec must contain:

- `albedo` palette: dominant, secondary, accent colors, and where they appear on the object.
- `roughness` response: base value, variation, and local response such as smoother worn edges or rougher cavities.
- tactile response: at least one of `normal`, `bump`, or `displacement` with scale/amplitude/strength.
- locality: `localOverrides`, dirt, wear, scratches, chips, stains, moss, patina, wetness, soot, or cavity masks tied to `viewEvidence`.
- material-specific behavior: alpha/transmission/translucency for thin or transparent parts, metalness/clearcoat for reflective parts, cloth/fiber grain for fabric-like parts.
- independent PBR channels: albedo, roughness, height/normal, and AO must be generated or authored separately; never reuse albedo as a roughness, height, normal, or AO map.
- reference-derived PBR extraction: when a source image is available and fidelity matters, run `../../forge/stage1_intake/extract_pbr_evidence.py` for each important material or crop before accepting material-pass. The default target threshold is `0.7`; below that, stop or request better references unless the user explicitly accepts a lower-fidelity approximation.
- scale hierarchy: close-up materials must describe macro, meso, and micro surface-frequency bands with object-relative frequency and amplitude.
- projection/UV intent: state UV, triplanar, cylindrical, planar, or another projection strategy, plus repeat/texel-density intent so detail does not stretch across scaled components.
- quality-first resolution: use at least 1024px procedural maps for important close-up materials and prefer 2048px when reference fidelity is the priority.
- geometric relief: if a ridge, crack, seam, chip, bark plate, fold, or dent affects the visible silhouette, represent it with geometry or displacement-capable topology instead of texture alone.

Do not accept "brown bark", "gold leaves", "dark metal", or "rough stone" as sufficient. Translate it into PBR terms: albedo palette, roughness, normal/bump, AO, dirt/wear, and local masks.

Do not claim exact PBR recovery from a single image. Pixels include baked lighting, exposure, shadow, view angle, and camera response. Treat extracted maps as reference-derived material evidence that still needs neutral/grazing/reference screenshot review.

Do not accept a material merely because all required fields are present. The browser render must prove that:

- roughness breaks highlights independently from albedo color
- normal/height detail remains readable under grazing light
- cavities and contacts have coherent AO rather than uniformly dark noise
- referencePbr maps, when present, are loaded by the generated Three.js material and have confidence at or above the configured threshold
- micro detail does not visibly tile or swim when the object is scaled
- local overrides appear in the same regions supported by `viewEvidence`

## Lighting-Pass Requirements

Before accepting `lighting-pass`, the spec must contain:

- key light direction, color temperature, intensity, and shadow softness
- fill light color/intensity, or explicit reason for no fill
- rim/back light or environment reflection cue when the silhouette needs separation
- ambient/hemisphere/environment color
- exposure and tone-mapping intent
- background color or gradient
- contact shadow / ground shadow behavior

Separate object material from photo lighting: a material should still read correctly in neutral turntable lighting, then a reference-matching lighting setup can be added.

## Screenshot Review

For material and lighting screenshots, compare in this order:

1. Albedo palette: are dominant and accent colors close to the reference?
2. Value range: are dark cavities and bright highlights in the right places?
3. Surface response: does roughness/normal/bump catch light?
4. Locality: are moss, stains, dirt, wear, chips, or color patches placed where the reference shows them?
5. Light structure: can you identify key, fill, rim/environment, contact shadow, and exposure?
6. Material-vs-light split: if the scene is relit neutrally, does the object still have believable material detail?

For quality-first work, capture three deliberate look-dev views before choosing `continue`:

1. `neutral`: broad soft key/fill lighting for honest albedo and form reading.
2. `grazing`: a low-angle hard or semi-hard key close-up that exposes smooth-plastic highlights, weak normals, uniform roughness, and texture tiling.
3. `reference-match`: the source camera and lighting direction as closely as the available evidence allows.

A material that only looks convincing in the reference-matched light has not passed. Fix its PBR response first, then tune the reference lighting.

If the mismatch is mostly color/texture/lighting, choose `refine-code` only when the spec already has the above details. Otherwise choose `refine-spec` first.

---

## Material & colour lessons — real-object reconstructions (2026-07: BMX + M9 Doppler)

**Solid colour vs reference-crop albedo — pick by finish type:**
- **Flat paint / single-colour** (bike frame): use a **solid** albedo (dominant colour) + flat normal. A raw photo crop tiles logos/gradients into stripes across long tubes. Sample the reference's *lit mid-tone* for the paint hue, then deepen/saturate (a photo mid-tone rendered under studio light reads lighter than the sample).
- **Patterned / gemstone finish** (Doppler blade, hydro-dip, camo, quartz): use the **real reference crop** as albedo → the exact palette + smoke/pattern is literally the reference pixels ("100% same colour"). Extract the gradient **palette stops** (guard→tip) to document the look. Make it gem-glossy: high `metalness` + low `roughness` (solid-dark roughness map) + `clearcoat` + raised `envMapIntensity` → layered shine.

**Sample the CORRECT region per material.** Verify the crop is actually on the part you think — a "handle" crop taken from the blade region gave a navy-blue "grey" handle. Crop, *look at the crop*, then extract.

**Aged / worn / faded materials — preserve mottling, don't flatten.** A uniform dark band looks crude ("thô"). Instead: keep the crop's real luminance variation (scratches, worn patches) but remap it into a **dark aged band** (e.g. luminance→charcoal 15–45), add micro-grain, and a **roughness map that varies** (worn high-spots slightly glossier, grooves matte). Keep the cast cool-neutral for gunmetal — a warm remap drifts to tan. Darken more than you think: a mid-grey albedo reads light under a strong key light.

**Render-capture must wait for textures.** `TextureLoader` is async; a screenshot fired before maps load shows `color:white` + metalness = a false "chrome" render (and a Divine-Eye false-reject). Poll `material.map.image.complete` before capturing. The render host must serve the reference PBR maps (copy to `public/`) or they 404 → white.

**Soft shadows for a studio look:** `key.shadow.radius`/`blurSamples` + a tight shadow-camera frustum + a low `ShadowMaterial.opacity` (~0.16) beat a hard dark blob.

## Candy/anodized colour washes to blue under a bright env — and prose can fight the reference
- **Symptom:** a doppler/candy blade whose albedo genuinely contains violet (verified by sampling the
  PNG: mid-body RGB ~ (150,50,170)) still renders **blue** in a white-studio scene.
- **Cause 1 — metalness steals the hue.** At `metalness ≈ 0.7–1.0` most of the surface colour is the
  *environment reflection* (specular F0), not the albedo diffuse; a bright/cool white env reflects
  blue-white and the albedo hue is a minority contributor. A colored PVD/candy/anodized coat is
  visually a **dielectric-led** surface: render it `metalness ≈ 0.35` + `clearcoat ≈ 0.6`
  (roughness ~0.18) so the albedo colour leads and the coat supplies gloss. Also trim
  `envMapIntensity` (~0.7) and clearcoat so the white env stops desaturating the hue.
- **Cause 2 — a blue-leaning purple reads blue when darkened.** A violet with `B > R`
  (e.g. 158,52,206) collapses toward blue under any tone-map/shadow darkening because B dominates.
  For a purple that *survives* shading, make it **magenta-leaning, `R ≳ B`** (e.g. 175,48,150).
- **Cause 3 (the real one here) — prose contradicted the reference.** A written brief said
  "cyan guard → PURPLE middle (45%) → indigo tip"; the actual reference image was
  **violet at the ricasso → SAPPHIRE-BLUE body (dominant) → navy-black tip**. img2threejs is
  reconstruction-*from-the-image*: when a colour brief disagrees with the reference photo, **sample
  the reference and match the photo**, then surface the discrepancy to the user — do not chase the
  prose (it cost 4 wasted render iterations pushing purple into a region the photo shows as blue).
