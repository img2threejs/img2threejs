# Character Reconstruction

Use this reference when `objectClass.primaryDomain` is `character` or `hybrid`. It replaces guesswork proportions with a measured system so a generated humanoid actually resembles the reference pose and build.

## Proportion System (head-units)

Measure everything in head-units (HU): total body height divided by head height. Pick the style axis from the image, do not assume realistic by default:

- realistic: ~7.5 HU (adult human)
- stylized / anime-adjacent: ~5-6 HU
- chibi / figurine: ~2-3 HU

Record measured ratios, not assumed ones:

- `headUnit`: head height as a fraction of total image height
- `torso`: crown-to-hip distance in HU
- `legs`: hip-to-floor distance in HU
- `shoulderWidth`: in HU (roughly 1.5-2 HU realistic, wider for stylized heroic builds)
- `hipWidth`: in HU

If the image crops the legs or feet, mark `legs` and `hipWidth` as inferred and lower confidence rather than guessing a stock adult ratio.

## Facial Landmark Layout

Store landmarks as normalized coordinates (0-1) relative to head bounding box, not the full image, so they survive scale changes:

- `hairline`: ~0.0-0.15 from crown depending on hairstyle bulk
- `eyeLine`: ~0.45-0.55 (near vertical mid-head; lower for chibi, higher forehead for stylized)
- `eyeSpacing`: horizontal gap between inner eye corners, ~0.2-0.35 of head width (wider spacing reads as more stylized/cute)
- `noseBase`: ~0.6-0.7
- `mouthLine`: ~0.75-0.85
- `earTop` / `earBottom`: roughly bracket `eyeLine` to `noseBase`

Pull these from the actual image via `forge/stage1_intake/extract_landmarks.py` overlay, not from a generic face chart. A stylized face with huge eyes will violate realistic ratios on purpose — match what is observed.

## Pose / Skeleton

Define joints as a minimal skeleton, matched to the reference silhouette and limb angles, not a default T-pose:

- root -> neck -> head
- neck -> left/right shoulder -> elbow -> wrist
- root -> left/right hip -> knee -> ankle

For each joint record an approximate angle (degrees, relative to rest pose) read off the silhouette. Prioritize matching:

1. overall stance (weight distribution, contrapposto vs symmetric)
2. limb angles at shoulders/hips (these dominate perceived pose match)
3. hand/foot orientation only if clearly visible

If a joint is occluded, do not invent an angle — mark `confidence` low and default to a neutral rest angle for that joint only.

## Character Materials (stylized default)

Reuse Track A detail machinery (`grimoire/intake/detail_inventory.md`) for accessories and trims. Base recipes:

- **Skin**: warm base albedo sampled from the image, low-to-mid roughness, no true subsurface scattering — approximate with a soft rim/backlight term and a slightly desaturated shadow tint. Avoid `MeshPhysicalMaterial.transmission` unless the reference clearly shows translucency (ears, fingers backlit).
- **Hair**: the single most common failure point for single-image reconstruction. Do NOT attempt strand-level geometry from one photo. Prefer stylized clumps — hair cards or short tube-along-curve locks grouped into 5-15 major masses matching the silhouette's hair shape, layered front-to-back with alpha or hard edges. Match the read silhouette (fringe, part line, volume) over any attempt at individual strands.
- **Eyes**: a glossy sphere (high specular, low roughness) plus a separate iris disc/decal with darker outline and a small bright catchlight quad or emissive dot offset toward the key light direction. The catchlight is disproportionately important for "looks alive."
- **Cloth**: extrude or plane panels following the silhouette's fold lines; add normal-map or geometry creasing at obvious fold zones (elbow, waist cinch, knee) rather than a flat plane. Local material overrides handle prints, seams, buttons via the Track A detail inventory.

## Gate Notes

Proportion and landmark values feed `anatomy` block validation (section 5.3/5.6 of the upgrade plan) — every measured value needs an `evidenceRef` back to the source image region, same discipline as object detail inventory.
