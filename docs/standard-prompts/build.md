# Build — reference image to a gated procedural model

The reconstruction route. This is the file `GLB_ANIMATED_CHARACTER_PROMPT.md` delegates its
Stages 1–3 to.

**The one thing that makes this prompt not block:** the forge scripts *scaffold*, they do not
*assess*. A freshly generated spec carries `primaryType: "unassessed"`, eight complexity scores of
`0`, an empty `detailInventory` and `anatomy.applies` unset — so `--strict-quality` fails it with
around 89 causes. Those are not defects to report; they are **your fields to fill**. Fill them, then
validate.

Replace every `<PLACEHOLDER>`, paste as one prompt.

````text
Rebuild the subject in this reference as a procedural Three.js model, using img2threejs.

## Inputs
- Reference:      <ABSOLUTE_PATH_TO_IMAGE>
- Subject name:   <SubjectName>
- Demo id:        <subject-id>
- Profile:        <character | animated-character | object>
- Real longest dimension: <e.g. 1.70 m>   # sanity-checks scale; never used to scale

## Step 0 — Can this subject be built at all?

Segment the figure from the background and report as numbers:
  · width/height at the hip band (48-54% of figure height), knee (68-74%), ankle (92-99%)
  · silhouette area / height²
  · whether the silhouette WIDENS below the hip, and by how much

The character template is 61 anatomy components and ZERO garments — clothing is not shipped in
v1.5. If a meaningful share of the silhouette is cloth standing away from the body (flared skirt,
hanging sleeve, cape, trailing panel), STOP HERE and tell me: what fraction, and what will be lost.
A body-shaped figure delivered under the subject's name is the failure this step exists to prevent.
For a subject like that the GLB route is the honest one — say so and stop.

## Step 1 — Intake

    python3 forge/state.py init --state .img2threejs/state.json \
      --reference <image> --profile <profile> --spec object-sculpt-spec.json
    python3 forge/stage1_intake/probe_image.py <image>
    python3 forge/stage1_intake/extract_landmarks.py <image> \
      --out anatomy.json --overlay overlay.png
    python3 forge/stage1_intake/build_detail_inventory.py <image> \
      --mode component-zones --out detail-inventory.json

Two things about the overlay, both measured, both worth knowing before you trust it:
  · it divides the IMAGE height, not the figure's bounding box, so every guide is offset by
    whatever margin sits above the head. Re-read the guides against the figure top.
  · `styleHeads` defaults to 6.0. Measure the head against the figure and set the real value;
    on a stylized 8-head figure the default puts the "nose base" guide on the eyes.

## Step 2 — Assess. This is YOUR work, not the script's.

Open the assessment and fill it from looking at the reference. Nothing downstream can succeed
until these hold real values:

    preSpecAssessment.objectClass.primaryType      not "unassessed"
    preSpecAssessment.objectClass.primaryDomain    object | character | hybrid
    objectClass.formLanguage / structureKind / motionPotential / materialFamilies   non-empty
    complexity.scores       all eight, from what you see, not defaults
    complexity.estimatedCounts                     real counts
    detailInventory                                >= targetMinDetails entries
    anatomy.applies = true, with styleHeads, proportions, pose, faceLandmarks

Enumerate the identity-defining details first — bevels and rounding, panel seams, fasteners,
engraved or painted linework, gloss vs matte zones, wear. Drop any detail you cannot place on a
real component rather than faking it.

## Step 3 — Spec, then gate

    python3 forge/stage2_spec/new_sculpt_spec.py "<SubjectName>" --image <image> \
      --assessment assessment.json --out object-sculpt-spec.json
    python3 forge/stage2_spec/validate_sculpt_spec.py object-sculpt-spec.json --strict-quality

Expect failures on the first run. Read each cause and answer it — a missing
`colorMaterialRecipe` means derive that component's colour from the reference pixels, not that the
gate is wrong. Iterate until it exits 0.

Only then:

    python3 forge/stage3_build/generate_threejs_factory.py object-sculpt-spec.json \
      --out src/create<SubjectName>Model.ts

The generator repeats the gate and is fail-closed: on failure it exits 2 with
`{"status":"BLOCKED", ...}` and writes NO file. If you see that, the spec is not ready — go back to
Step 2. Never pass `--allow-nonstrict`.

## Step 4 — Render and review, one pass at a time

Render, package one reference-vs-render sheet, and judge it. Do not advance a pass until its
review passes. Record per-region confidence for anything the single view cannot show.

## When to stop, and when not to

STOP and ask me:
  · Step 0 says the subject is mostly cloth
  · a gate fails for a reason you cannot resolve from the reference
  · `next.py` exits 3 / `status=stopped`

DO NOT stop for:
  · a scaffolded field being empty — fill it, that is the job
  · strict-quality failing on the first run — read the causes and answer them
  · a detail you cannot place — drop it, note it, continue

## Report

Components · strict-quality exit and cause count · passes cleared · details inventoried vs target ·
per-region confidence · and one line naming what still does not match. Never say "done" when it is
"improved".
````
