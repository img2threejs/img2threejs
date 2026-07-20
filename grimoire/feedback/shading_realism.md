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
