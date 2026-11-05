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
explicit, flagged, opt-in mode. `integrations/glb_character_pipeline/` is one such mode: a GLB-baseline
character reconstruction pipeline (SDF point-cloud splat + Surface Nets) that runs against a companion
showcase checkout via `IMG2THREEJS_SHOWCASE_ROOT`, isolated behind its own `pyproject.toml`/`uv.lock`
and `node/package.json` so the `forge` core stays dependency-free. It only applies when a character
build actually has a GLB reference to measure — skip it entirely otherwise. See its `README.md`.

## Development

- Scripts under `forge/` are pure Python 3.10+ standard library. No pip dependencies there. Optional
  integrations under `integrations/<name>/` may declare their own pip/npm dependencies in their own
  `pyproject.toml`/`package.json`, isolated from the core.
- Run the test suite from the skill root: `python3 -m unittest discover -s forge/tests -p 'test_*.py'`.
  Set `IMG2THREEJS_SHOWCASE_ROOT` to a showcase checkout to include the TypeScript typecheck gates;
  without it they skip, so a green run has not proven the emitted Three.js compiles.
  The vertex-paint Python/TypeScript parity test invokes Node's
  `--experimental-strip-types`: use a Node 22 release supporting that flag for the full suite.
  This test prerequisite does not change the CLI's Node >= 18 support.
- Validate a spec before generation: `python3 forge/stage2_spec/validate_sculpt_spec.py spec.json --strict-quality`.
- Keep changes backward compatible: existing object specs must continue to validate.
- No emojis in source, docs, or generated output.

### npm CLI verification and publishing

The CLI version in `package.json` is independent of the skill version in `SKILL.md`.
Without `--ref`, `bin/img2threejs.mjs` installs the latest GitHub Release of the skill
(the `releases/latest` redirect), so a skill release needs no CLI republish. The tests
point that lookup at a local server through `IMG2THREEJS_RELEASES_LATEST_URL`; this env
var is a test hook only, not a supported user setting. The npm package ships the
installer only; it fetches the complete skill from GitHub when explicitly invoked.
No install lifecycle hook runs.

Before publishing, use Node.js >= 18 and Git:

```bash
npm test                       # isolated local Git fixtures; no network
npm run package:check
npm publish --dry-run --json    # packaging only, not proof of npm credentials
npm pack --pack-destination /tmp
```

Install the resulting tarball in a disposable project with
`npm install /tmp/img2threejs-<version>.tgz`, then invoke its
`node_modules/.bin/img2threejs` with `--version`, `install --dry-run`, `install`,
`update`, and `doctor`. Set `HOME` and `XDG_CONFIG_HOME` to disposable directories
containing the host config roots so the smoke run cannot touch your real host skills.
A real install requires network access to GitHub.

### Automatic CLI releases

`.github/workflows/cli-publish.yml` publishes npm CLI releases through the reviewed,
full-SHA-pinned `img2threejs/ci-workflows` reusable workflow and npm trusted publishing.
No npm write token is passed to Actions. The GitHub-hosted publish job uses Node 24
and npm >=11.5.1; the installer itself still supports Node >=18.

Configure the npm package's **Trusted Publisher** as GitHub Actions:

- Organization: `img2threejs`
- Repository: `img2threejs`
- Workflow filename: `cli-publish.yml` (the caller, not `npm-publish.yml`)
- Allowed action: direct **`npm publish`**, not only `npm stage publish`

With npm >=11.15 and account-level 2FA, configure the same binding from the CLI:

```bash
npm exec --yes --package=npm@11 -- npm trust github img2threejs \
  --repo img2threejs/img2threejs --file cli-publish.yml --allow-publish --yes
```

The package must already exist on npm; the first publication is manual.

CLI tags are **`cli-vX.Y.Z`**, matching `package.json`. Skill tags remain `vX.Y.Z`,
matching `SKILL.md`; pushing a skill tag does not publish the CLI.

```bash
# Bump package.json and update CLI release notes, then commit reviewed files.
npm test
npm run package:check
git tag -a cli-v0.1.1 -m 'img2threejs CLI 0.1.1'
git push origin cli-v0.1.1
```

The workflow tests without publish permissions, publishes the tested tarball with
install/publish scripts disabled, and validates tag/version agreement. Stable releases
use `latest`; prereleases use their prerelease channel. A registry outage is an error,
not permission to publish; an existing version must match the tested artifact.

Trusted publishers need their first successful CI publish within npm's activation
window. Verify the workflow run and registry version after every release. A successful
local dry-run is not proof of authentication or registry publication.

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
