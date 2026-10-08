# Installing and using plugins

Domain knowledge does not live in this checkout. It lives in **plugins**, installed and managed by
the [`img2` harness](https://github.com/img2threejs/img2). This page is the pipeline-facing view:
what a run sees, how a plugin's declarations reach this pipeline's checklist, and what to check when
a profile you expect is missing.

The normative rules for plugins themselves are the harness repo's
[`docs/PLUGIN_CONTRACT.md`](https://github.com/img2threejs/img2/blob/main/docs/PLUGIN_CONTRACT.md) —
where anything here disagrees with it, that file wins. To *write* a plugin, start from its
[`docs/WRITING_A_PLUGIN.md`](https://github.com/img2threejs/img2/blob/main/docs/WRITING_A_PLUGIN.md).

## Three layers, and why they do not couple

| Layer | Owns |
|---|---|
| harness `img2` | install/link plumbing, the plugin registry, the workspace state envelope, the gate runner, the contract. Ships **no** domain capability |
| this pipeline | `forge/`, the staged build, the deterministic gates, the checklist authority (`.img2threejs/state.json`) |
| a plugin | its own checkout steps, gates, reference material, spec augmentation |

Neither side imports the other's code. The harness never executes plugin code during
`add`/`doctor`/`sync`, and this pipeline consumes a plugin only as **declared data**
(`plugins.json`, `domain.json`, `spec-augmentation.json`). That is why installing or removing a
plugin can never change what this pipeline emits: it may *add* steps and *raise* quality floors
(the merge clamps — a plugin can raise a floor, never lower one), and nothing else.

## Install

Nothing here is required for a `generic` or `character` run: those are satisfied by this checkout
alone. Reach for the harness when a **domain plugin** serves the item — and note that **every
command below works before you install anything**, as
`npx --yes @img2threejs/img2@latest <command>`. Measured on an empty, throwaway `$IMG2_HOME`:
`--version --json` and `list` exit 0 and write nothing (`list` reports `Registered 0` and points at
`img2 plugins`), `capabilities` answers `providers: []`, and only `doctor` exits 1 — saying exactly
`no harness checkout at <home>; run img2 install`.

```bash
npx --yes @img2threejs/img2@latest install   # once per machine: ~/.img2 + an `img2` launcher on PATH
img2 plugins                              # the official live catalog (harness CLI >= 0.4.0)
img2 update [<id>] [--check]              # update managed plugins, or check for newer versions (0.4.0)
img2 add cs2 --yes                        # short id; `img2 add img2threejs/plugin-cs2` is the portable form
img2 list                                 # id, version, ref, sha
img2 doctor                               # fail-loud static audit of every registered row
img2 sync --check                         # generated index == manifests (CI-able)
img2 remove cs2                           # unlink every host, move clone to backups, drop the row
```

- Short ids and `img2 plugins` require harness CLI **0.4.0 or newer**; the `img2threejs/plugin-<id>`
  form works on any version.
- `--ref <branch>` installs a moving branch explicitly and records it as such; `--link <path>`
  symlinks a local checkout for plugin development (no clone); anything outside the `img2threejs`
  org needs `--allow-any-source`. Some plugins are distributed from **private** repositories and
  need authorized Git access — see the README's plugin table for each plugin's access level.
- A plugin whose `requires.harness` / `requires.coreApi` the installed harness does not satisfy
  **blocks** the install. It is not a warning: a mismatch that got through could emit wrong geometry
  that passes visual gates by luck.
- Keep the plugin's own `SKILL.md` reachable in whatever agent host you use. The harness links each
  plugin as its own skill (`<host-skills-dir>/img2-<id>`) **for the hosts it knows about** — see
  [Host skills](#host-skills) if yours is not one of them.

### Where `img2` itself lives

The npm package is only an entry point. `install` clones the harness into `$IMG2_HOME/harness` and
links that checkout's own CLI (`$IMG2_HOME/harness/bin/img2.mjs`, a zero-dependency Node script) as
`img2` in a writable directory on `PATH`, so `which img2` resolves **into the checkout**, not into a
global npm prefix — and the CLI's version is the checkout's tag, not the npm dist-tag. That makes the
CLI version a machine property worth checking (`img2 --version --json`) rather than an assumption:
this block's `plugins`, `update`, `npm:<name>` and short-id forms are 0.4.0 additions, while an older
checkout accepts only the `img2threejs/plugin-<id>` form.

### What `install` actually does

Run against a throwaway `$IMG2_HOME` (nothing of an existing setup touched), in three steps:

1. **Installation home** — creates `$IMG2_HOME/{plugins,generated,backups}` plus `plugins.json`
   (`{"version":1,"plugins":[]}`) and `receipts.json` (`[]`).
2. **Harness checkout** — clones the harness into `$IMG2_HOME/harness`, following the repository's
   **default branch rather than the newest tag** (measured: `v0.4.0-1-g06634bd`), so the checkout can
   sit a commit past the tag it reports.
3. **Agent access** — links the launcher into a writable directory on `PATH`. When no such directory
   qualifies it links **nothing**: it warns `No writable launcher directory on PATH; use npx
   instead` and prints the `npx` form. That is why the block above is written to work whether or not
   an `img2` launcher exists. Host settings are touched only for hosts it detects (`Claude Code not
   detected; settings left unchanged`).

## What a run sees depends on `$IMG2_HOME`

Composition is **data**: the set of active plugins is the registry at `$IMG2_HOME/plugins.json`
(`IMG2_HOME` defaults to `~/.img2`). Nothing else is consulted — in particular this pipeline reads
the registry, never a glob of the `plugins/` directory, so a checkout left behind by a removed
plugin contributes nothing.

That makes "I installed it" and "this run sees it" two different statements whenever more than one
`$IMG2_HOME` exists on a machine (a per-project home next to the default one is the common case).

```bash
img2 --version --json      # harness, maxPluginSchema, coreApi, contract, commands[] — a positive probe
img2 list                  # what the ACTIVE home has registered
python3 forge/state.py init --help | grep profile   # profiles this pipeline can actually start
```

`--profile`'s choices are generated from the registry, so that list is the authoritative answer for
the environment the command runs in. If a profile is missing:

```bash
export IMG2_HOME=<the home you installed into>       # for this shell
# or register the plugin into the default home:
img2 add img2threejs/plugin-character && img2 doctor && img2 sync --check
```

## What a domain plugin contributes

A domain plugin ships an optional `domain.json`. This pipeline reads it from
`$IMG2_HOME/plugins/<id>/domain.json` (registered rows only) at
`forge/state.py init --profile <id>`, and splices:

- `setupSteps` **before** the base step in `setupAnchorBefore`;
- `passSteps` **before** the base step in `passAnchorBefore` (every correction pass);
- `rigSteps` **after** the terminal steps (no anchor by design — the rig track always follows them);
- `specCollection` as an extra local-evidence collection for Local Spec Search.

`{plugin_dir}` is substituted when the profile is spliced in, so a command in the checklist is
runnable from the workspace without the caller knowing where the plugin lives. Rows are
`[stepId, command]` **pairs** (not `{id, command}` objects), and `domain.json` has **no `actor`
field** — a different shape from `steps.json`/`gates.json`, and a **different closed placeholder
set**: `{plugin_dir}`, `{reference}`, `{spec}`, `{pass_id}`. The pipeline renders it with Python
`str.format()`, which raises on any other name, so `{workspace}`/`{image}` are legal in
`steps.json` and a run-time crash here — `img2 doctor` refuses the wrong set up front, naming the
placeholder, the file, and that file's own valid set.

Failure modes are loud, by construction:

- a missing profile raises naming **what is installed** (`available: generic, character`);
- an unknown key in `domain.json` is refused rather than ignored (a typo would otherwise silently
  contribute nothing);
- two plugins declaring the same domain `id` raise `declared twice` — the pipeline refuses to pick
  one.

In-repo profiles are `generic` and `character`. Installed plugins add their own
(`cs2`, `animated-character`, `environment`, …); see the README's official-plugin table.
`steps.json` and `gates.json` are the harness-side surfaces (contributed steps ordered among
themselves, and gates); `domain.json` is the one this pipeline consumes.

## Plugin gates

A gate is a CLI that prints **one** verdict envelope and exits `0` (pass) / `1` (fail) / `2`
(error/refused):

```json
{ "kind": "img2.gate-verdict", "version": 1, "gate": "cs2-review", "plugin": "cs2",
  "status": "pass", "reasons": [], "evidence": {} }
```

A malformed envelope is an `error`, never a pass, and a gate must be `actor: "program"` — a gate has
to produce a verdict. `img2_core.gate_runner` aggregates them and stops the workflow on a blocking
fail (later gates are recorded `skipped`, never run). In this pipeline a plugin gate is attached to
the review record with `--domain-review-json <report>.json --review-scene-json <scene.json>` — the
checklist step carries the resolved paths, and a failed domain gate blocks `continue` even when the
global fidelity score passes.

## Host skills

The harness links each plugin into the skill directories of the hosts it knows about
(`~/.claude/skills/img2-<id>`, the Codex and OpenCode equivalents), using a harness-derived name —
a plugin never chooses it. If your agent host is **not** in that table, nothing is linked for it,
and the plugin's own `SKILL.md` (intake contracts, finish rulebooks, review gates) is simply not in
your model's context. Two workarounds, in order of preference:

1. Link it yourself with the same name convention, from your host's skills directory to
   `$IMG2_HOME/plugins/<id>` — the same target the harness would have used.
2. Read the files directly: every step a profile splices in prints an **absolute** path, so the
   contract a step names can be read without a skill link at all.

Adding your host to the harness's `HOSTS` table is the real fix, and it belongs upstream in the
harness, not in a local patch.

## Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| `no installed provider serves profile 'X'; available: …` | the plugin is not in the registry the run is reading | `img2 list`, then `img2 add …` or fix `$IMG2_HOME` |
| `domain id 'X' is declared twice` | two providers claim one id | remove one; the pipeline must not choose |
| `doctor` names a row and two versions | `requires.harness` / `coreApi` mismatch | update the harness, or the plugin's floor |
| `capabilities` → `ambiguous`, exit 3 | two plugins claim one edge | `--plugin <id>` disambiguates |
| `capabilities` → `data-fault`, exit 1 | a manifest failed to validate, or `argv[0]` does not resolve | read `problems[]`; `doctor` and `capabilities` must agree |
| A domain step's command crashes the checklist | a placeholder from the wrong file's vocabulary | fix the plugin; `doctor` catches it first |
| A prose row executed as a command | missing `actor: agent\|human` | declare `actor` on that row |

## Where to read next

- This pipeline's README — the plugin table (what each adds, its access level, its install command)
  and the harness setup steps.
- Harness `docs/PLUGIN_CONTRACT.md` — every normative rule (manifest schema, steps, gates,
  `domain.json`, the gate runner envelope, the emission-target socket).
- Harness `docs/WRITING_A_PLUGIN.md` and `docs/plugin-wiki/` — authoring, worked scenarios, and the
  field-by-field reference.
- Harness `SECURITY.md` — the threat model: a plugin is arbitrary code your agent will later run.
  `add` pins, attributes and names it; it cannot make it safe.
