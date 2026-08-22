# Scene traps — each one cost a correction cycle before it was named

Every entry below was hit for real during the profile's motivating
reconstruction (a 1536x691 film-still interior). They share a nasty property:
**each looks like a modelling error and is not one.**

## 1. The principal point is not the centre of the frame

Film stills, published screenshots, and social-media crops are almost never the
full sensor frame. The motivating still carried its principal point 90px below
centre. Two consequences:

- Symptom: EVERY reprojected landmark is offset by the same vector. The reflex
  is to move the furniture; the cause is one number. The reprojection check in
  `scene_backproject.py` names this ("uniform shift") precisely so nobody spends
  a cycle chasing placements.
- Three.js cannot express it with a plain `PerspectiveCamera`: build the camera
  with the FOV of a larger virtual frame whose centre IS the principal point,
  then cut the real frame out of it with `camera.setViewOffset(fullW, fullH,
  offsetX, offsetY, viewW, viewH)`.

## 2. World axes go in the COLUMNS of world-to-camera

Building R with the world basis vectors as rows silently transposes the frame.
The evil part is the error signature: near-field points stay almost correct and
far points stretch (~1.7x in the motivating case), so the room "comes out too
long" and it reads as a proportion mistake. If distances measured on the floor
grow with depth, check the rotation layout before touching any geometry.

## 3. "The verticals converge" is a claim that needs a stability test

Two nearly vertical edges always intersect SOMEWHERE. Whether that intersection
means anything is a noise question, and the eye cannot answer it. Measured on a
synthetic fixture: with 3 degrees of camera pitch and 0.8px of endpoint noise,
the 3-VP orthocentre collapsed the focal length from 772 to 259 — a 3x error
from sub-pixel noise. The solver therefore gates the 3-VP route on a
leave-one-out spread of the DERIVED FOCAL LENGTH (not of the vanishing point,
whose distance is unbounded and meaningless as a scale) and falls back to the
horizon-constrained route when unstable. The fallback absorbs true pitch into
the principal point — fine for back-projection, but it means the reported
"pitch" is not the physical camera pitch; do not quote it as one.

## 4. Wall orientation must come from the interior normal

Deriving a wall's rotation from its edge direction alone flips half the walls
of any non-convex or many-sided plan; with backface culling they vanish into
black. Compute the normal, dot it with (room centre - wall midpoint), and
negate if needed. The symptom (black wall) misleads toward lighting/material
debugging.

## 5. Frames and door surrounds need real holes

A frame drawn as a solid slab in front of a canvas/doorway reads as a blank
plate from any oblique angle. Extrude a ring (outer shape with the inner shape
as a `THREE.Path` hole). Same rule for archway surrounds.

## 6. The light source may BE the scene

Interior identity often lives in nonstandard lighting — the motivating room is
lit by its own floor. The evidence is directional: undersides of furniture
bright, cornice shadow soft and upward. Importing a default studio rig destroys
the likeness even when every measurement is right. State the light hypothesis
in the spec with its evidence, and light the model that way (emissive floor +
upward area light, in that case).

## 7. Contacts near the horizon are not measurements

Depth sensitivity scales like the SQUARE of the inverse distance to the
horizon line: at the motivating frame's resolution, a reading 60px under the
horizon moved ~0.2 floor units per pixel. The gate tiers these as
`horizon-limited` — record them, model from them if nothing better exists, but
never report them as measured, and never "fix" a 30px reprojection error there
by dragging furniture.

## 8. A scene fails the object rubric — route it, don't force it

`grimoire/intake/validation_rubric.md` rejects "photo is a scene, not an
object reference" for the OBJECT pipeline, and that stays correct. The scene
profile is the routing answer: shell/openings/fixtures/props decomposition,
solved camera, reprojection gates. Do not push a scene through the object
spec; do not reject it either.
