# Reference Motion Measurement Contract

## Contents

1. Evidence boundary
2. Coordinate and frame contract
3. Motion-system decomposition
4. Observed and inferred records
5. Camera registration
6. Reflection and lighting inference
7. Comparison contract
8. Acceptance gate

## 1. Evidence boundary

Treat the decoded source pixels as the visual authority. Preserve the exact local source and its
SHA-256 hash. Record probe output separately from interpretation.

An every-frame claim requires all frames in the selected interval at native frame rate. A sampled
analysis must identify its sampling rule and may guide exploration, but it cannot pass an explicit
frame-complete request.

Do not commit private or copyrighted source media to a public repository. Store paths and hashes in
local evidence. Use synthetic fixtures in tests.

## 2. Coordinate and frame contract

Use `source-pixels` as the primary 2D coordinate space:

- origin: upper-left of the original decoded frame;
- x: right-positive;
- y: down-positive;
- bounds: `0 <= x < width`, `0 <= y < height`.

An ROI may accelerate analysis, but preserve its `(x, y, width, height)` in source coordinates and
emit absolute source points. Never report ROI-local positions as full-frame coordinates.

Keep timestamps in seconds from the source timeline, not from the start of an extracted clip. Keep
frame indexes monotonic. Record native width, height, frame rate, frame count, color space, transfer
function, and any tone mapping detected or assumed.

Choose the parent-pipeline bootstrap frame deterministically. Default to the temporal midpoint of
the requested interval, rounded to the nearest native frame with ties resolved toward the earlier
frame. If a minimal contact sheet shows that frame is transitional, occluded, or does not identify
the subject, use the midpoint of the first stable or hold interval with the same tie rule. Use
`manual-semantic-keyframe` only when neither midpoint is representative. Record the timestamp,
selection rule, and reason under `source.bootstrapReferenceFrame`. The frame must come from the
authoritative source, never from a generated atlas, current render, or comparison composite.

For lighting calculations, convert encoded RGB to linear RGB first. Name the exact transfer
function. Preserve encoded observations separately when they are needed for render comparison.

## 3. Motion-system decomposition

Create a separate system whenever two features can move or change independently:

| Class | Examples | Primary evidence |
| --- | --- | --- |
| `camera` | framing, projected center, apparent scale | stable landmarks, boundary fit |
| `geometry` | silhouette, rigid rotation, morph | contour, landmarks, depth cues |
| `material` | roughness, transmission, albedo response | highlight width, chroma, refraction |
| `lighting` | point caustic, rim strip, environment reflection | centroid, orientation, luminance |
| `overlay` | ticks, labels, dots, arcs | exact path, visibility, attachment |
| `silhouette` | projected boundary when treated independently | contour samples and residual |

Color does not establish system identity. A cyan rim segment and cyan specular point remain separate
when their paths, attachment, or timing differ.

## 4. Observed and inferred records

Put directly measured image evidence under `observed.features[]`:

- `systemId` and `featureId`;
- `sourcePoints` in full-frame pixels;
- `confidence`;
- optional `fitResidualPx`;
- optional measured values such as linear luminance, softness radius, orientation, ellipse axes,
  visibility, or contour samples.

Put model-dependent quantities under `inferred.properties[]`:

- `systemId` and property name;
- value and units;
- confidence;
- explicit assumptions;
- optional calibration evidence.

Depth, surface normals, light direction, camera pose, roughness, and falloff are inferred unless a
calibrated sensor or known scene makes them observed. Dark material is not automatically shadow;
bright material is not automatically emission.

For a single uncalibrated view, confidence above `0.65` requires named calibration evidence. This is
an honesty ceiling, not a statement that all values below it are correct.

## 5. Camera registration

Track a stable geometric feature before interpreting surface motion. Report:

1. raw feature displacement in source pixels;
2. camera or projected-boundary displacement;
3. camera-translation-removed displacement;
4. scale- and rotation-normalized displacement when projected size or orientation changes.

For an ellipse-like projected circular feature, keep center, radii, orientation, fit residual, and
sample count per frame. An ellipse axis ratio can support apparent tilt under weak perspective, but
only as an inference with that assumption recorded.

Separate entrance transitions from stable motion. Compute stable-interval velocity and direction
without allowing the initial registration jump to dominate the result.

## 6. Reflection and lighting inference

A highlight path can constrain a conditional lighting target when geometry and camera conventions
are stated. For a unit sphere with view vector `V` and observed surface normal `N`, a mirror-like
direction estimate may use:

```text
L = normalize(2 * dot(N, V) * N - V)
```

This is non-unique for rough, transmissive, layered, or environment-lit materials. Track point keys,
broad environment strips, and internal/refraction features separately. Record highlight centroid,
linear luminance, softness, orientation, and confidence before inferring a light vector.

Do not reconstruct reflection motion by stretching the silhouette unless the contour measurements
show real deformation. Do not bake a moving highlight into albedo when the source shows it responding
independently to view or light.

## 7. Comparison contract

Use `comparison.status: not-run` before the first render exists. It must contain only that status.
This is valid for a `preImplementationDecision.action` of `ready-for-implementation`; it is not
evidence that temporal fidelity passes. Set `rootReviewDecision.status: not-run` at the same phase,
also without stale action fields. After a live render is captured, replace both with
`status: complete` and their required fields.

Synchronize source and render timestamps. Match camera, crop, coordinate convention, and feature
identity before calculating error.

Set `featureEquivalent: true` only when both sides measure the same physical/visual feature. Then use
`classification: like-for-like`. If the render lacks an equivalent and a broad highlight is compared
with a point caustic, use `classification: proxy` and explain the limitation in
`metricInterpretation`.

Useful temporal metrics include:

- boundary residual in pixels;
- camera-registered path RMSE;
- direction angular RMSE when the inference model is shared;
- luminance and softness-curve error;
- phase/timing error;
- hold-state drift and flicker;
- radius/path residual for attached arcs, lines, and dots.

Never let a good still-frame score override a failed motion path.

The two decisions serve different gates and both remain in the manifest:

- `preImplementationDecision` records whether source analysis authorizes a first implementation;
- `rootReviewDecision` records the root img2threejs action after synchronized render comparison.

A tracking or registration defect is `refine-analysis` before the first render and `refine-spec`
when discovered during post-render review. The post-render record does not supersede or rewrite the
pre-implementation record.

## 8. Acceptance gate

`ready-for-implementation` requires:

- the exact source and interval are identified;
- requested frame coverage is satisfied;
- every frame is decoded at original resolution without resampling;
- camera motion is recorded, even when measured as static;
- observed and inferred values are separate;
- inferred properties contain assumptions and confidence;
- at least one stable or explicitly justified target interval exists;
- when a current render exists, its comparison is synchronized, camera-matched, and proxy metrics
  are labelled as proxies;
- unresolved ambiguity is listed.

The manifest validator checks structural evidence. Agent vision must still inspect annotated frames
and the source video. A valid manifest with the wrong tracked feature is still wrong.
