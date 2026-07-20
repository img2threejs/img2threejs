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

## Pull requests

- Keep PRs focused and describe the behavior change plus how you verified it.
- Add or update tests for new gates, schema fields, or templates.
- Update `docs/UPGRADE_PLAN.md` status when you land a roadmap item.

## Reporting issues

Include the reference image characteristics, the command you ran, the spec or generated output,
and what you expected versus what you got. Screenshots of the render help a lot.
