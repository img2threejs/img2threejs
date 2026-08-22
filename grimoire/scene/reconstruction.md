# Scene reconstruction (img2threejsScene profile)

Rebuild an INTERIOR OR BUILT SPACE seen in one photograph as a procedural
Three.js scene. This inverts the object pipeline's economics:

|                | object                     | scene                                  |
| -------------- | -------------------------- | -------------------------------------- |
| decomposition  | easy — one part tree       | hard — shell + openings + fixtures + props + lights |
| camera         | NOT recoverable → fit by eye | recoverable → solved by arithmetic   |
| verification   | side-by-side sheet, agent vision | REPROJECTION — pixels, not opinions |

The whole profile follows from one observation: **a built space is its own
calibration target.** Floors, walls and openings supply bundles of straight
lines in two or three mutually orthogonal directions, and a repeated floor
pattern (tiles, boards, paving) supplies scale. So "fit the camera until it
looks right" — the only option for a lone object — is replaced by "solve the
camera, then never argue with it".

## Order of work

1. **Line harvest** (agent vision). Collect pixel segments for the two floor
   directions, and for vertical architectural edges if any are crisp. The agent
   sees; the scripts do arithmetic. Segments from furniture silhouettes and
   reflections are the main contamination — prefer grout lines, skirting, and
   wall/floor junctions.
2. **Camera solve** — `forge/stage1_intake/scene_camera.py`. Two orthogonal
   floor vanishing points give the horizon; the vertical direction decides
   whether the principal point is the 3-VP orthocentre or is constrained to the
   horizon (see `traps.md` on which route you are actually in). Scale comes from
   the floor repeat. The solve carries its own gates: VP residuals, the
   **pitch-agreement gate** (square tiles must report the same repeat in both
   directions — a disagreement IS your scale uncertainty, report it, do not
   average it away silently), and a leave-one-out stability gate on the 3-VP
   focal length.
3. **Back-projection** — `scene_backproject.py`. Every prop is placed by its
   FLOOR CONTACT pixel, back-projected to the plan. Heights come from vertical
   lines standing on those contacts. Each placement carries a
   **depth-sensitivity** number (floor units per pixel of reading error) and a
   confidence tier; near the horizon the tier says `horizon-limited` and the
   placement is a recorded guess, not a measurement.
4. **Unit sanity** — `scene_unit_gate.py`. Absolute scale is NOT recoverable
   from one image. Propose metres-per-repeat and test every independently
   measured height against architectural bands **conjunctively** (door head AND
   worktop AND cornice AND camera height). One hard failure kills the proposal.
5. **Build** the shell (floor, walls with the mouldings toolkit, ceiling), the
   openings, then props at the measured plan positions.
6. **Verify by reprojection.** Project the built geometry through the solved
   camera back into reference pixels. The reprojection check decomposes error
   into a UNIFORM shift (one cause — almost always the camera) and per-landmark
   residuals (that placement is wrong). Do not eyeball a comparison sheet for
   placement; pixels decide.

## What a scene spec needs that ObjectSculptSpec lacks

- `shell` — floor polygon, wall bands (plinth / dado / panel field / frieze /
  cornice heights), ceiling treatment.
- `openings` — doorways and windows: which wall, position along it, head
  height, and what lies beyond (a glowing cell reads better than a void).
- `fixtures` — things attached to walls (panelling, paintings, sconces).
- `props` — free-standing furniture: floor contact (x, z), yaw, and overall
  height, each with the confidence tier from back-projection.
- `lights` — with EVIDENCE. Interior light placement is an identity feature:
  state which surfaces are bright (undersides bright = the floor is the
  emitter) rather than importing a studio rig.

## Honesty requirements

- The camera solve is measured; SAY SO, and keep the solved values apart from
  assumed ones (principal-point x, the room behind the camera, hidden faces).
- Everything behind the camera is invention. Record the assumed plan shape.
- Absolute scale is a proposal that passed a plausibility gate, not a fact.
- Do not reproduce identifiable artwork (paintings, posters) from the
  reference; place procedural stand-ins with the same compositional statistics
  and say that is what they are.
