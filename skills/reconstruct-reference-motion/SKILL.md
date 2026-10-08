---
name: reconstruct-reference-motion
description: Measure and reproduce motion from a video or image sequence before implementing it in procedural Three.js. Use when a user asks to mirror an animation frame by frame, reconstruct camera/object/light motion, match moving reflections or caustics, compare a live Three.js animation with a reference video, or fix a 3D object that reads as a flat animated texture.
---

# Reconstruct Reference Motion

Use this companion skill with the root `img2threejs` skill. Add temporal evidence to the existing
intake, build, and review gates; do not bypass them.

## Non-negotiable rule

Measure before editing the model or shader. Keep these systems separate throughout the solve:

1. camera/framing motion;
2. object geometry and pose;
3. material response;
4. direct light and environment reflections;
5. UI, labels, arcs, dots, and other overlays.

A moving highlight is not proof that the mesh moves. A stable silhouette is not proof that the
camera is static. A texture atlas containing baked light is not a substitute for dynamic specular
response when the reference shows independent light motion.

## Required reading and artifacts

Read [references/measurement-contract.md](references/measurement-contract.md) completely before
analyzing frames. Use
[references/reference-motion-manifest.schema.json](references/reference-motion-manifest.schema.json)
as the machine-readable hand-off. Start from
[references/example-motion-manifest.json](references/example-motion-manifest.json) when useful.

Produce, at minimum:

- a source probe with dimensions, frame rate, duration, color metadata, and source hash;
- a contact sheet confirming the exact interval and all semantic transitions;
- a `reference-motion-manifest.json` that separates observed values from inferred properties;
- annotated keyframes with source-coordinate overlays;
- a synchronized reference-versus-render comparison when a render exists, otherwise an explicit
  `not-run` comparison state;
- one explicit `preImplementationDecision`: `ready-for-implementation`, `refine-analysis`,
  `request-input`, or `stop`;
- a separate `rootReviewDecision`, initially `not-run` and completed after a live render exists.

## Workflow

### 1. Resume the parent pipeline

If `.img2threejs/state.json` does not exist, perform only the minimal source probe needed to extract
one representative frame at native resolution. Use the requested interval's temporal midpoint,
rounded to the nearest native frame with ties resolved earlier. If a minimal contact sheet shows
that frame is transitional, occluded, or does not identify the subject, use the midpoint of the
first stable or hold interval instead. Use a manual semantic keyframe only when neither rule works,
and record why in `source.bootstrapReferenceFrame`. Then initialize the parent state from that
source frame:

```bash
python3 forge/state.py init \
  --state .img2threejs/state.json \
  --reference <representative-source-frame> \
  --profile <generic|cs2|character>
```

Do not initialize from the current render, generated atlas, or comparison image unless it is itself
the user's authoritative reference. Then run the root state gate before substantive analysis:

```bash
python3 forge/next.py --state .img2threejs/state.json
```

If it reports a hard stop, stop. Record the validated motion manifest as intake evidence; chat
history is not evidence.

### 2. Lock the source and interval

Download or copy the exact source locally, hash it, and probe it. Decode the selected interval at
the original dimensions and native frame rate. Do not resize, smooth, interpolate, or silently skip
frames. If performance requires sampling, label the result `sampled` and do not claim frame-complete
analysis.

Verify semantic endpoints from the pixels. Do not trust a spoken label such as “90 to 60” until the
frames show those values. Preserve absolute source coordinates even when an ROI is used for focused
measurement.

### 3. Segment independent motion systems

Create one system record per independently moving or shaded element. Typical systems are:

- silhouette or projected boundary;
- camera/gauge center and apparent scale;
- object rotation/deformation;
- point highlight or caustic;
- broad environment strip/rim;
- fixed overlay arcs, ticks, dots, numerals, and labels.

Do not merge two systems because they share a color. Do not track a dot sequence as animation unless
the frames show its position or visibility changing.

### 4. Record observed image evidence

For every decoded frame, record timestamps and feature measurements in source-pixel coordinates.
Store local ROI coordinates only as a secondary convenience. Include confidence, fit residuals, and
feature identity. Work in linear color for luminance or lighting calculations and record the transfer
function used.

Keep dense maps as external evidence files referenced by hash; do not inflate the JSON with one row
per pixel.

### 5. Infer conditional 3D quantities

Only infer camera pose, depth, normals, light direction, roughness, or falloff after observed values
exist. Put assumptions and confidence beside every inferred property. A single view of reflective or
transmissive material is non-unique; cap confidence unless calibration, known geometry, or multiple
views resolve the ambiguity.

When using a spherical specular approximation with view vector `V` and observed surface normal `N`,
record the convention and equation explicitly, for example:

```text
L = normalize(2 * dot(N, V) * N - V)
```

Treat this as a reconstruction target, not recovered ground truth.

### 6. Remove camera motion before interpreting surface motion

Register the silhouette or another stable geometric feature first. Report both raw feature motion
and camera-compensated motion. Use normalized object coordinates when apparent scale changes.

Classify intervals such as `transition`, `stable`, `morph`, and `hold`. Report stable-state metrics
separately when an entrance transition would distort velocity or direction estimates.

### 7. Validate the manifest

Run:

```bash
python3 skills/reconstruct-reference-motion/scripts/validate_motion_manifest.py \
  reference-motion-manifest.json --json
```

Fix every error. Structural validity does not grant implementation readiness; the pre-implementation
manifest decision must also be `ready-for-implementation`. A first implementation may begin with
`comparison.status: not-run` and `rootReviewDecision.status: not-run`; neither object may retain
fields from a completed comparison. Update both to `complete` after capturing the live render.

### 8. Choose the correct Three.js representation

- Use geometry/camera transforms for silhouette, pose, or parallax changes.
- Use independent lights, environment maps, normals, roughness, transmission, or a custom physical
  shader for view-dependent reflection motion.
- Use texture atlases for genuinely baked, view-independent appearance only.
- Keep UI overlays in their own geometry or render layer and bind them to the same measured path.

If a reference feature has no equivalent in the current render, mark the comparison as `proxy`.
Never publish its angular or pixel error as a like-for-like metric.

### 9. Reconstruct one system at a time

Implement in this order unless evidence requires another dependency order:

1. camera and projected boundary;
2. rigid pose or deformation;
3. material and environment response;
4. direct highlight/caustic motion;
5. overlays and labels.

After each system, capture the same timestamps from the live Three.js route and compare them against
the source. Do not tune several systems at once; that makes causality unreadable.

### 10. Review temporal fidelity

Compare synchronized frames using the same crop, coordinate convention, camera, and feature identity.
Report boundary residual, camera-registered feature-path error, angular-path error where justified,
luminance/softness curves, phase error, flicker, and hold stability. Inspect the video as motion; a
good still frame cannot pass a temporal gate.

After a render exists, update the comparison to `complete`, record exactly one action under
`rootReviewDecision`, validate again, and return that root img2threejs review action:

- `continue` only when the temporal contract and the normal img2threejs pass gates both pass;
- `refine-spec` when tracking, registration, feature identity, or the temporal contract is wrong;
- `refine-code` when the measurement contract is sound but the renderer differs;
- `request-input` when the source or interval cannot support the requested inference;
- `stop` when the requested one-to-one result is not feasible under the stated constraints.

Preserve the pre-implementation decision as phase history. Before rendering, a bad track maps to
`refine-analysis`; after rendering, discovering that the track or feature identity was wrong maps
to `refine-spec`. The later root review does not overwrite the earlier analysis decision.

## Hard stops

Stop rather than implement when any of these is true:

- the exact source or interval is unavailable;
- frames were skipped or resampled but the user requested frame-complete analysis;
- observed and inferred fields are mixed;
- camera motion has not been separated from the tracked surface feature;
- a current render lacks a feature equivalent but its completed comparison is labelled exact;
- single-view inverse-lighting output is presented without assumptions and confidence;
- the parent img2threejs state gate is blocked.

## Reporting

State what was measured, what was inferred, the coordinate spaces, the stable interval, confidence,
unresolved ambiguity, pre-implementation decision, and post-render root review when applicable.
Name what still differs. Never say “matched one-to-one” from visual impression alone.
