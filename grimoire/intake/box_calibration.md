# Box-object self-calibration — measured proportions for the object pipeline

`forge/stage1_intake/box_calibration.py`. The scene profile's central
observation — orthogonal straight-line families calibrate the camera — spills
over to one class of OBJECT: box-dominant subjects (tank hulls, cabinets,
containers, buildings, machine housings). Their own edges provide the two or
three orthogonal families that a lone organic object cannot, so the camera
becomes solvable and the payoff lands exactly on the object pipeline's weakest
step: **proportions stop being eyeballed into the spec and become measured
ratios with reprojection residuals.**

## When to reach for it

- The subject's silhouette is dominated by straight, parallel edge pairs on at
  least TWO of its principal axes (>= 2 clean edges per family).
- You are about to write width/height/depth ratios into an ObjectSculptSpec by
  eye. Don't — harvest the edges, run the solver, and copy the measured ratios.

## When NOT to

- One usable family only (a slab seen face-on, a mostly-organic subject with a
  single straight feature). The solver refuses with routing advice rather than
  guessing — that case stays with the floor-grid scene route (if a patterned
  ground is visible) or the honest eyeball route (`solve_camera_pose.py`).
- Curved-hull subjects (cars): the "edges" you can harvest are not straight
  world lines; the residual gate will tell you, but don't fight it.

## What you get

```json
{"calibration": {"route": "three-family orthocentre", "focalPx": ...,
                 "fPairwiseSpread": ..., "verdict": "pass|warn|fail"},
 "box": {"gauge": "dimension along x = 1",
         "dimensions": {"x": 1.0, "y": 0.4603, "z": 0.5873},
         "perCorner": [...], "uniformShiftPx": ..., "diagnosis": [...]},
 "scale": {"dimensionsMetres": ...}}
```

- With 3 families: f AND the principal point are both solved (orthocentre).
  The three pairwise f estimates must agree (`fPairwiseSpread` gate) — that is
  the same honesty device as the scene solver's pitch-agreement gate.
- With 2 families: the third axis is ASSUMED image-parallel and the output says
  so (same constraint as the scene solver's horizon route).
- Corner residuals are decomposed into a uniform shift (suspect the
  calibration) and local residuals (that corner reading or lattice tag is
  wrong) — the same one-cause-vs-local diagnosis as `scene_backproject.py`.
- Absolute scale needs one known dimension (a published hull length, a track
  pitch x link count). Without it, dimensions are ratios and are labelled so.

## Relationship to the rest of intake

This is an opt-in stage-1 measurement, not a new profile: run it during the
object pipeline's intake (before spec authoring), and feed `box.dimensions`
into the spec's component sizes. Contracts shared with the scene route live in
`grimoire/scene/reconstruction.md` and the trap catalogue in
`grimoire/scene/traps.md` (principal point, column-major rotation, stability
tests — all apply verbatim).
