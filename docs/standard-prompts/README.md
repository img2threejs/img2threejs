# Standard prompts

Four prompts, one per job. Each is copy-paste, each completes in one pass, and each states its own
exit condition. Replace every `<PLACEHOLDER>` before pasting.

| Prompt | Use when | Route |
|---|---|---|
| [`build.md`](build.md) | a reference image, no mesh | reconstruct from primitives, gated |
| [`glb-force-measured.md`](glb-force-measured.md) | a GLB is available and every parameter it measures should be forced, not approximated | measure, force, prove with parity gates |
| [`polish.md`](polish.md) | a build that renders but does not match | bounded correction, one defect at a time |
| [`vfx.md`](vfx.md) | the rig and its clips play, only the effects are missing | measure the events off a real mixer, then schedule against them |

Rigging and animation on top of a GLB: [`../GLB_ANIMATED_CHARACTER_PROMPT.md`](../GLB_ANIMATED_CHARACTER_PROMPT.md).

## Why these were rewritten

The previous prompts blocked more than they built, for three reasons that were mechanical rather
than stylistic:

1. **They pointed at a file that does not exist.** `GLB_ANIMATED_CHARACTER_PROMPT.md` delegated the
   whole geometry route to `docs/GLB_CHARACTER_PROMPT.md`, which was never written. An agent
   reaching the largest step of the run found a dead reference. `build.md` is that missing file.

2. **They ran a gate before producing the gate's input.** `validate_sculpt_spec.py --strict-quality`
   fails with ~89 causes on a freshly scaffolded spec, because the scripts only scaffold —
   `preSpecAssessment.objectClass.primaryType` is literally `"unassessed"`, all eight complexity
   scores are `0`, and `detailInventory` is empty. Told to "run the gate and do not advance", an
   agent stops on its first command, every time. **Every prompt here now fills the scaffold before
   it validates**, and says exactly which fields.

3. **They treated every stop as equally serious.** A missing input and a failed measurement need
   different responses. Each prompt below separates *produce the input and continue* from
   *stop and ask*, and lists which is which.

## The rule the four share

> A gate is a question. Answer it with evidence, or say you could not — never by lowering the bar,
> and never by stopping before you have tried to produce the evidence it asked for.
