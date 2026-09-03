# VFX — the effects layer for a character that already moves

Use this once the rig and its clips exist and play. It is the effects pass, not a rigging pass.

**Read this first: img2threejs has no particle subsystem.** There is no emitter type, no VFX gate,
no effect registry. What the pipeline gives you is anchors, a destruction model, and — the part that
matters most here — **a way to measure the clips instead of eyeballing them**:

    actionProfile.sockets[]   attachment / effect / grip / joint positions, in local coordinates
    destruction{}             breakable, fractureGroup, seamRefs, detachableFragments,
                              breakImpulse, debrisMaterial
    material.emissive         the glow channel
    userData.sculptRuntime    nodes, meshes, sockets, colliders, destructionGroups, height
    userData.tick             the per-frame hook

Everything visual is code you write, in plain `three`. Say which parts are authored rather than
measured — the same honesty rule the rest of the pipeline runs on, applied to effects.

## The measuring machinery already exists — call it, don't rebuild it

The prompt below asks you to sweep every clip through a real `AnimationMixer` rather than reading
timings off a scrub bar. That harness is already emitted:

- `emit_animation_runtime.py` emits `seek(name, t)` — play, pause, set time, `mixer.update(0)`,
  `updateMatrixWorld(true)`. That is a real evaluation at a real time, which is exactly what Gate R1
  uses to tell a clip that plays from one that merely exists.
- `clip_features.py` already measures `handRange`, `footRange`, `stance` intervals and `poseReturn`
  at N = 25 samples per clip.
- Everything in this project is expressed as a fraction of figure height `H`, and
  `userData.sculptRuntime.height` carries it. The prompt's "normalise to figure heights" is that
  same convention, not a new one.

## Anchoring — the rule that keeps effects alive through a rebuild

Every effect binds to a socket, a bone, or a destruction group **that already exists in the spec**.
No magic coordinates. If the anchor you need is not there, add it to the spec and regenerate — a
`sockets[]` entry with an id and a local position — rather than hard-coding a vector into the effect
code. A hard-coded position silently detaches the moment geometry is regenerated, and nothing warns
you.

On a rigged model an effect that must follow a limb binds to the **bone**, not to a pivot: rigging
merges the mesh and the per-part pivots no longer reach the geometry.

Additive blending for anything emissive; disable depth WRITE but keep depth TEST, or the effect
punches a hole through the model it is attached to. One `userData.tick(dt, elapsed)` per effect
group. The mixer takes the frame **delta** while the generic tick contract passes elapsed — say in a
comment which one each call site is passing. Plain `three` only, no new dependency, no runtime
fetch, and every effect disposable: geometry, material and texture released on teardown.

## The prompt

````text
Build the effects layer for the character already in this showcase — the rig and its clips exist,
only the VFX is missing.

Start from the subject. Study what this character is and how its actions read in the world it
comes from; the effects should belong to it, not to a generic effect kit. Then find the events —
don't eyeball timings off a scrub bar. Sweep every embedded clip through a real AnimationMixer and
measure where a strike actually stops at extension, where weight actually meets the ground, and
where the body is driven by a force arriving from outside the clip. Normalise to figure heights so
the table survives a change of scale.

Build around the stop, not the travel — the interesting part of any impact is the instant it
arrests. Schedule the effects against that measured table rather than a live "it just decelerated"
test: scheduling fires on the exact frame, and it's the only way a windup can exist at all,
because nothing else knows a strike is coming. Add hitstop; the clip itself slowing for a few tens
of milliseconds is most of the felt impact.

Give the character a small vocabulary of impact kinds and make them differ in motion first, colour
second — a light hit that is only a paler heavy hit is still a heavy hit. Take the palette from
the world the character lives in, not from an element wheel, and keep one contrasting accent so
something in frame reads against the rest. A blow the character takes is not a strike played
backwards: rings turn inward, debris comes off the body, no flash at the hand.

Calibrate continuous layers — motion trails, breath, ambient drift — per clip against that clip's
own measured speed; one global threshold smears the fast clips and leaves the slow ones bare. Pool
everything, allocate nothing after construction, and start every object invisible so the viewer's
framing pass never measures an effect. Then show me the loudest action, the quietest one, and a
ground contact.
````

## Report

The measured event table — clip, time, event kind, position in figure heights — before any effect
exists, because everything downstream is scheduled against it. Then per effect: anchor
(socket / bone / destruction group id), scale basis, blend mode, tick owner, disposal path. Finally
one line naming what is authored rather than measured, and the frame cost you measured — not the one
you expect.

## For an object rather than a character

An object has no clips to sweep, so there is no event table to measure and the whole scheduling
argument does not apply. Anchor on the same sockets and destruction groups, drive from
`userData.tick`, and break along the `seamRefs` and `detachableFragments` the spec already declares —
`debrisMaterial` is authored there, so use it rather than inventing a second material that will
drift from it.
