# Self-Correction Loop Reference

Use this reference when a model construction pass has just finished.

## Review Order

1. Capture or collect a rendered screenshot for the current browser view.
2. Select at most five critical semantic systems for the current pass and only the suspicious important systems.
3. Create one full reference/render comparison sheet with `stage4_review/make_comparison_sheet.py`.
4. Inspect the sheet once with your agent's vision and score the global image, relevant visual layers, and each selected semantic feature visible in that pair.
5. Compare the rendered result to current `ObjectSculptSpec`.
6. Decide whether the mismatch is caused by the spec, the implementation, lighting/camera, missing evidence, or performance tradeoff.
7. Choose exactly one action:
   - `continue`
   - `refine-spec`
   - `refine-code`
   - `request-input`
   - `stop`
8. Record the screenshot paths, comparison image, overall score, layer scores, feature scores, and AI critique in `reviewHistory`.

For visual passes, `continue` requires a rendered screenshot, a comparison image, a global AI vision score at or above threshold, and every critical feature at or above its own threshold. Without them, the review is not evidence-backed enough. Pixel comparison code is never the acceptance authority.

## Root Cause Guide

Use `refine-spec` when:

- a component is missing or invented incorrectly
- the primitive family is wrong
- proportions or coordinate frame are wrong
- material layer is under-specified
- local features are missing from the spec
- evidence refs are absent or contradict the image
- user expectation cannot be represented by current build passes

Use `refine-code` when:

- the spec is clear but generated geometry is wrong
- material parameters were not implemented
- local masks/noise/wear are missing in code
- hierarchy/pivots do not match the spec
- browser render has obvious artifacts
- performance can be improved without changing the spec

Use `request-input` when:

- the image hides essential geometry
- material cannot be inferred from the provided view
- exact branding/text/ornament is required
- the requested fidelity is incompatible with a single image

Use `stop` when:

- target fidelity is reached
- user accepted current approximation
- remaining issues require new references, manual modeling, or non-procedural assets

## Fidelity Estimate

Use a practical 0-1 scale:

- `0.2`: only rough primitive placeholder
- `0.4`: silhouette recognizable, structure incomplete
- `0.6`: macro and meso forms mostly correct, material/detail weak
- `0.75`: object reads correctly, local details approximate
- `0.85`: strong procedural match for real-time use
- `0.95`: near-reference, usually requires multiple views or manual art

Do not claim `0.9+` from a single ambiguous image unless the object is simple and symmetrical.
