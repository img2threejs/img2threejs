# Contributing to img2threejs

Thanks for your interest. img2threejs turns a reference image into a code-only, procedural,
quality-gated Three.js model. Contributions that keep that identity sharp are very welcome.

## Good first areas

- New procedural material or geometry recipes in `grimoire/build/geometry_patterns.md`.
- Object-domain templates and detail-inventory taxonomy improvements.
- Generator primitives, bevels, instancing, and surface-band tuning in `forge/stage3_build/generate_threejs_factory.py`.
- More pipeline tests in `forge/tests/test_pipeline.py`.
- Documentation and worked examples.

## Where the project is strong vs honest limits

- Strong: hard-surface objects, props, stylized/low-poly assets.
- Stylized-only: characters and creatures read as game/figurine avatars, not photoreal likeness.
- Out of scope today: photoreal reconstruction of a specific person, animal, or landscape from a
  single image. That needs photo-texture projection or ML image-to-3D, which breaks the code-only
  promise. See `docs/UPGRADE_PLAN.md` for the analysis and the tiered roadmap.

Please do not add code that silently downloads meshes or art packs — the core promise is
reconstruction by code. If you want a projection or generative-assist path, propose it as an
explicit, flagged, opt-in mode.

## Development

- Scripts are pure Python 3.10+ standard library. No pip dependencies.
- Run the test suite from the skill root: `python3 forge/tests/test_pipeline.py`.
- Validate a spec before generation: `python3 forge/stage2_spec/validate_sculpt_spec.py spec.json --strict-quality`.
- Keep changes backward compatible: existing object specs must continue to validate.
- No emojis in source, docs, or generated output.

## Before you open an issue

- Search existing issues first.
- For a bug, include the reference image characteristics, the exact command, the relevant spec or generated output, expected versus actual behavior, and a render screenshot when it helps. Do not post private images, credentials, or other sensitive material.
- For a proposal, describe the problem, the observable outcome, and how it preserves the project's code-only procedural reconstruction contract.
- Use a discussion channel or another appropriate forum for usage questions when one is available; issues should be actionable bugs or focused proposals.

## Triage and contributor intent

- The issue itself is the triage discussion record. A `triage: needs-review` label means the issue
  is waiting for maintainer review; a label is not a promise that it will be implemented.
- Maintainers record a decision comment before setting priority, contributor state, or closure.
  `priority: low` keeps an issue open and must include a reason and a revisit trigger.
- To offer a fix, use the **Contribution intent** form. It records your proposed scope and does not
  reserve or assign the work. A maintainer will discuss and, if appropriate, mark the target issue
  `contribution: claimed`.
- Link implementation PRs with `Refs #<number>` or `Related to #<number>`. Do not use closing
  keywords. A maintainer manually closes an issue only after recording the merged PR, verification,
  and closure rationale in a final comment.

## Pull requests

- Keep PRs focused and describe the behavior change plus how you verified it.
- Add or update tests for new gates, schema fields, or templates.
- Update `docs/UPGRADE_PLAN.md` status when you land a roadmap item.
- Link the issue with `Refs #<number>` when applicable.
- For changes that affect visual fidelity, include the relevant render or comparison-sheet evidence and the result of its review.
- Do not include private reference images, credentials, or other sensitive material in the PR description, commits, or attachments.

## Reporting issues

Include the reference image characteristics, the command you ran, the spec or generated output,
and what you expected versus what you got. Screenshots of the render help a lot.
