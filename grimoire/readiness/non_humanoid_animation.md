# Non-humanoid articulated animation

Use this contract for insects, quadrupeds, mechanical creatures, vehicles with articulated limbs,
and other moving subjects whose topology is not a humanoid skeleton. Do not force a humanoid bone
template onto a subject merely because the output needs `idle` and `walk` clips.

## Declare model space before rigging

The skill's internal procedural convention is right-handed, Y-up, forward `+Z`. That is an
authoring convention, not a promise that the target application uses the same one. Record both the
authoring and target frames, then name exactly one conversion owner: export or load-root adapter.
Never distribute the same correction across geometry, camera, animation tracks, and gameplay.

Create `model-space-contract.json` from measured scene data and run:

```bash
python3 forge/stage5_rig/model_space_gate.py --contract model-space-contract.json
```

The contract must contain an identity semantic root, a forward marker, one visible front feature,
and one rear feature in target-root local coordinates. This prevents a correct-looking `forward`
label or marker from hiding geometry that actually faces sideways. Then render front, side, rear,
and quarter views from the declared target frame; the structural gate cannot replace those views.

## Choose rigid pivots before skinning

Rigid shells and mechanical segments belong under nested transform pivots. A limb hierarchy follows
the actual motion chain, for example `hip -> knee -> ankle -> foot`; do not place the tibia and foot
beside the hip and attempt to fake downstream motion from one rotation. Prefer identity rotations at
rest and place geometry relative to its owning pivot.

Use a skin only where a visible surface must deform across joints. A rigid insect or machine can be
fully animation-ready with no `THREE.Skeleton`, skin indices, or weights. The humanoid rig payload
gate is not applicable to that path; unique semantic pivots, resolvable tracks, pose stress, and
bounds are the relevant evidence.

## Define gait topology, not just keyframes

Name limbs by side and longitudinal position. Define the contact groups before authoring a walk. A
stable six-leg default is alternating tripods:

- group A: left-front, right-middle, left-rear
- group B: right-front, left-middle, right-rear

Each group alternates stance and swing. Hip motion advances the limb, knee/ankle motion provides
clearance, and the foot returns to a plausible contact pose. Body bob, head, antenna, tail, shell,
or wind-up motion may follow the phase as secondary motion but must not own world movement.

## Clip authority

- locomotion clips are in-place unless the consumer explicitly owns a root-motion contract
- the semantic root has no position or quaternion track
- clip names are semantic (`idle`, `walk`, actor-specific actions), unique, and mapped once
- every track target resolves after the production loader parses the exported asset
- loop start and end poses agree; duration and loop policy are explicit
- gameplay or another consumer owns actual position, facing, collision, and velocity selection

## Required evidence

Sample every looping locomotion clip at normalized phases `0`, `0.25`, `0.5`, `0.75`, and `1`.
Record that:

- opposing gait groups reverse lift phase
- all transforms and animated bounds remain finite
- feet and appendages do not materially intersect the body or detach at motion extremes
- the phase-1 pose closes the phase-0 seam
- the semantic root remains identity and does not drift
- target-forward agrees with visible front in all cardinal views

A single attractive animation frame is not animation evidence. A successful humanoid rig validator
is not evidence for a rigid non-humanoid hierarchy.
