# VFX — effects on a finished model

**Read this first: img2threejs has no particle subsystem.** There is no emitter type, no VFX gate,
no effect registry. What the pipeline gives you is *anchors and a destruction model*:

    actionProfile.sockets[]     attachment / effect / grip / joint positions, in local coordinates
    destruction{}               breakable, fractureGroup, seamRefs, detachableFragments,
                                breakImpulse, debrisMaterial
    material.emissive           the glow channel
    userData.sculptRuntime      nodes, meshes, sockets, colliders, destructionGroups
    userData.tick               the per-frame hook

Everything visual is code you write, in plain `three`. Say which parts are authored rather than
measured — the same honesty rule the rest of the pipeline runs on, applied to effects.

````text
Author VFX for this img2threejs model.

## Inputs
- Factory:  <PATH_TO_create<Name>Model.ts>
- Spec:     <PATH_TO_object-sculpt-spec.json>
- Effects:  <e.g. blade trail, muzzle flash, ambient dust, impact sparks, aura>

## Anchoring — the rule that keeps effects alive through a rebuild

Every effect binds to a socket, a bone, or a destruction group **that already exists in the spec**.
No magic coordinates. If the anchor you need is not there, ADD IT TO THE SPEC and regenerate — a
`sockets[]` entry with an id and a local position — rather than hard-coding a vector into the effect
code. A hard-coded position silently detaches the moment geometry is regenerated, and nothing will
warn you.

On a rigged model an effect that must follow a limb binds to the BONE, not to a pivot: rigging
merges the mesh and the per-part pivots no longer reach the geometry.

## Budget

Read `userData.sculptRuntime.height` and scale every size, distance and velocity from it. An effect
authored in absolute units is wrong on the next subject.

Additive blending for anything emissive; disable depth WRITE but keep depth TEST, or the effect
punches a hole through the model it is attached to.

## Driving it

One `userData.tick(dt, elapsed)` per effect group, collected by the same hook the idle motion uses.
On a rigged model the mixer takes the frame DELTA while the generic contract passes elapsed — do not
feed elapsed to a mixer, and say in a comment which one each call site is passing.

Presentation offsets come off before the rig ticks and go back on after. Never restore a cached rest
pose: it overwrites the pose the animation just produced.

## Destruction, if asked

Break along `seamRefs` and `detachableFragments` that the spec already declares. Attach impact,
spark, dust and debris effects to the sockets meant for them. `debrisMaterial` is authored in the
spec — use it rather than inventing a second material that will drift from it.

## Constraints

  · plain `three` only, no new dependency
  · no runtime fetch — textures procedural or embedded, same as the geometry
  · every effect disposable: geometry, material and texture released on teardown
  · never modify the factory's geometry to make an effect fit

## Report

Per effect: anchor (socket / bone / destruction group id) · scale basis · blend mode · tick owner ·
disposal path. Then one line naming what is authored rather than measured, and the frame cost you
measured — not the one you expect.
````
