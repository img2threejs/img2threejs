# Installing the skill — agent runbook

What *you* (an agent) should run, in what order, and what "working" looks like. This page is the
procedure and the expected output; the normative detail stays where it already is — the README's
[§1 Install the base skill](../README.md#1-install-the-base-skill) and
[§5 Audit and maintain the installation](../README.md#5-audit-and-maintain-the-installation) — and
the plugin surface in [`docs/PLUGINS.md`](PLUGINS.md). Where this page and those disagree, they win.

## 0. Two CLIs, one skill — do not conflate them

| | Base skill CLI | Plugin harness CLI |
|---|---|---|
| Package | `img2threejs` | `@img2threejs/img2` |
| Installs | **this skill** into agent hosts | **plugins** (domain steps, gates, targets) |
| Hosts it writes skill links for | `hermes`, `claude`, `codex`, `opencode` | `claude`, `codex`, `opencode` |
| Run as | `npx --yes img2threejs@latest <cmd>` | `npx --yes @img2threejs/img2@latest <cmd>` |

Installing one does **not** install the other, and adding a plugin does not generate a model.
Reach for the harness only when a domain plugin serves the item (CS2 skins, animated characters);
a `generic` or `character` run is satisfied by this checkout alone.

Both work **before** anything is installed — every command below is also valid as
`npx --yes <package>@latest <cmd>` — so "the CLI is missing" is never the blocker it looks like.

## 1. Install the skill into this host

```bash
npx --yes img2threejs@latest install --dry-run     # plan only: no downloads, no filesystem changes
npx --yes img2threejs@latest install               # detect and install into every present host
npx --yes img2threejs@latest install --host hermes # or one host: hermes|claude|codex|opencode|all
npx --yes img2threejs@latest doctor                # base-skill host/link report
```

Expected dry-run line, and how detection works — a host is "present" when its directory exists, so
the plan names exactly those:

```
→ install skill img2threejs/img2threejs @ v2.0.0 into: hermes, claude
```

- `--ref` takes a **semantic tag or a 40-char commit SHA**; branches and short SHAs are refused
  (the installer requires Git and network for a real run).
- Checkouts live under `~/.img2threejs/releases/<sha>/img2threejs`; hosts installing the same commit
  share one checkout.
- The first `--yes` belongs to `npx` (package download) — it is not an overwrite flag.
- The installer refuses to overwrite a manual install or an unrelated link rather than clobbering it.

Manual alternative — one checkout, every host entering it through a symlink, so copies cannot drift
(`~/.config/opencode` may be `$XDG_CONFIG_HOME/opencode`):

```text
~/.hermes/skills/img2threejs          -> <your checkout>
~/.claude/skills/img2threejs          -> <your checkout>
~/.codex/skills/img2threejs           -> <your checkout>
~/.config/opencode/skills/img2threejs -> <your checkout>
```

Choose the CLI **or** the manual clone for a given path, never both. A manual clone follows the
repository's default branch; the CLI pins a release.

## 2. Install the harness, only if a domain plugin serves the item

```bash
npx --yes @img2threejs/img2@latest --version --json   # works with nothing installed
npx --yes @img2threejs/img2@latest doctor             # exits 1 until a harness checkout exists
npx --yes @img2threejs/img2@latest install --yes      # clones the harness, links a launcher
npx --yes @img2threejs/img2@latest add cs2 --yes      # a plugin by catalog id
```

On a machine with no `$IMG2_HOME`, `doctor` exits **1** and says exactly
`no harness checkout at <home>/.img2/harness; run 'img2 install'`, while `--version` and `list`
still answer. If no writable directory on `PATH` qualifies, `install` links **no** launcher and
warns `No writable launcher directory on PATH; use npx instead`. Full behaviour, host-skill linking
and the `$IMG2_HOME` trap: [`docs/PLUGINS.md`](PLUGINS.md).

## 3. Verify, and know what each check proves

```bash
npx --yes img2threejs@latest doctor   # this skill's links per host
img2 doctor                           # registered plugins: static audit, fail-loud
img2 list                             # what the ACTIVE $IMG2_HOME has registered
```

Neither `doctor` proves visual likeness or animation quality — that needs the reconstruction's own
render and domain gates. `img2 list` answering a profile the pipeline does not offer is the
signature of two `$IMG2_HOME`s with the plugin registered in the inactive one.

## 4. When something is off

| Symptom | Cause | Action |
|---|---|---|
| `img2threejs: command not found` (or `img2`) | the CLI is not on `PATH` | run the `npx --yes <package>@latest …` form — no install required |
| Host missing from the dry-run plan | that host's directory does not exist | create/point it, or install with an explicit `--host` |
| `install` refuses a ref | branch or short SHA | pass a semantic tag or the full 40-char SHA |
| Unmanaged base skill reported | a manual clone occupies that skill path | keep it, or move that entry deliberately before using the CLI |
| A profile your run needs is missing | the plugin is not registered in the **active** `$IMG2_HOME` | `img2 list`, then `img2 add …` or fix `$IMG2_HOME` |
| `FAIL … opencode link missing` | a host skill link is absent | inspect the reported path; `sync` regenerates harness data, not host links |
| Private plugin source 404 / access denied | no Git credentials for that repository | sign in with an authorized account; no installer can bypass source permissions |

## 5. Where to read next

- README [§1](../README.md#1-install-the-base-skill) and [§5](../README.md#5-audit-and-maintain-the-installation) — the authoritative install and maintenance text for this skill.
- [`docs/PLUGINS.md`](PLUGINS.md) — what a run actually sees: registry, `domain.json` splice, gates, host skills.
- Harness [`PLUGIN_CONTRACT.md`](https://github.com/img2threejs/img2/blob/main/docs/PLUGIN_CONTRACT.md) — every plugin rule, including the gate runner and the emission-target socket.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — packaging and publication checks for this repository's own CLI.
