# Polish — a build that renders but does not match

For a model that already exists and already passes its gates, and is still wrong to look at.

**Why polish used to stall:** told to "fix everything that does not match", an agent rewrites the
factory, and the next render is different in ways nobody can attribute. This prompt fixes **one
defect per cycle**, with the measurement before and after, so a change that made things worse is
visible immediately instead of being buried under four other changes.

````text
Polish this img2threejs build against its reference. One defect per cycle.

## Inputs
- Factory:    <PATH_TO_create<Name>Model.ts>
- Spec:       <PATH_TO_object-sculpt-spec.json>
- Reference:  <PATH_TO_REFERENCE>
- Budget:     <N> cycles          # stop when spent, and report what is left

## Cycle

1. RENDER and package one reference-vs-render sheet. One sheet, not a scattering of screenshots.

2. NAME the single worst defect in one sentence — what is wrong, where, and how you can tell.
   Rank by identity impact, not by how easy it is to fix. A wrong silhouette outranks a wrong
   gloss, always.

3. MEASURE it before touching anything. Interior difference banded by height, ΔE00 for a colour,
   silhouette IoU for a shape, per-region confidence for anything the view cannot show. Write the
   number down. "It looks off" is not a measurement and cannot be checked afterwards.

4. ATTRIBUTE it to a component id and a spec field. If you cannot name the field, you are guessing
   — say so and pick the next defect instead of editing blind.

5. FIX exactly that field. Do not touch a second component in the same cycle, however tempting.

6. RE-MEASURE the same number. Three outcomes and all three are results:
     improved   -> keep it, record both numbers
     unchanged  -> revert it. The hypothesis was wrong; say what it was.
     worse      -> revert it, and say what that rules out.

7. Re-run the gates that cover what you touched. Never lower a threshold to pass one.

## Rules

  · A cycle that reverts is a successful cycle. It bought information.
  · Never fix two defects at once. Attribution is the whole value here.
  · Never edit the generated factory by hand — edit the spec and regenerate, or the next
    regeneration silently discards your work.
  · If a defect traces to something the reference cannot show, stop and say so: no number of cycles
    will resolve what the image does not contain.
  · If the same defect survives two cycles, stop and report it. A third attempt is guessing.

## Report

Per cycle: defect · measured before · field changed · measured after · kept or reverted.
Then: which defects remain, ranked, and which of them the reference cannot resolve.
Say "improved", never "done", unless every ranked defect is closed.
````
