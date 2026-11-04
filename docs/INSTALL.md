# Install img2threejs

One command installs the skill into every supported coding agent on your machine. Plugins are
optional and only needed for specific domains, for example CS2 skins.

You need **Node.js ≥ 18**, **Git**, and **Python ≥ 3.10**, plus one of: Claude Code, Codex,
OpenCode, Hermes. Other agents: [§7](#7-other-coding-agents). The CLI supports macOS and Linux;
Windows is not supported yet (directory symlinks need Developer Mode, and the skill expects
`python3`).

## Paste this to your agent

If you would rather let your coding agent do it, paste:

```text
Install the img2threejs skill for me, following
https://github.com/img2threejs/img2threejs/blob/main/docs/INSTALL.md
1. Run `npx --yes img2threejs@latest install`, then `npx --yes img2threejs@latest doctor`.
   Always use the npx form, never a bare `img2threejs` command.
   Exit 2 means an existing skill is in the way: show me the path and stop.
2. If ~/.hermes exists, run the Hermes step in §1.
3. Install the plugin harness only if I asked for something a plugin provides (§3; `img2 plugins` lists them).
4. Re-running any step is safe. Do not edit dotfiles by hand.
5. Tell me which agents were linked and that I must restart them.
```

## 1. Install the skill

```bash
npx --yes img2threejs@latest install   # links the skill into every agent it finds
npx --yes img2threejs@latest doctor    # one line per agent: "managed" means installed
```

Expected output of `install` (one `✓` line per agent found):

```text
→ install skill img2threejs/img2threejs @ v2.0.0 into: hermes, claude
✓ hermes: ~/.hermes/skills/img2threejs → ~/.img2threejs/releases/<sha>/img2threejs (2.0.0, <sha>)
✓ claude: ~/.claude/skills/img2threejs → ~/.img2threejs/releases/<sha>/img2threejs (2.0.0, <sha>)
```

An agent is found when its folder exists: `~/.claude`, `~/.codex`, `~/.config/opencode`
(or `$XDG_CONFIG_HOME/opencode`), `~/.hermes`. Install into one only with `--host claude`
(or `codex`, `opencode`, `hermes`). Preview without changing anything with `--dry-run`.

**Hermes only — one more line.** Hermes loads skills per profile, and the installer only writes
`~/.hermes/skills/`. Link the skill into the profile you use (default `img2threejs`):

```bash
ln -sfn ~/.hermes/skills/img2threejs ~/.hermes/profiles/${HERMES_ACTIVE_PROFILE:-img2threejs}/skills/img2threejs
```

Use `ln -sfn`, not `ln -s`: running `ln -s` a second time on an existing directory link silently
creates a stray link *inside* the skill checkout. Repeat the line for each profile that should see
the skill. Without it, Hermes reports "Unknown skill".

## 2. Restart your agent and try it

Restart the agent (or start a new session) so it loads the skill. Then, with an image in your
project:

| Agent | Type |
|---|---|
| Claude Code | `/img2threejs Rebuild the object in ./reference.png as a procedural Three.js model.` |
| Codex, OpenCode, Hermes | `Use the img2threejs skill to rebuild the object in ./reference.png as a procedural Three.js model.` |

That is all a generic object needs. More prompts: [README §4](../README.md#4-run-a-reconstruction-in-your-agent).

## 3. Add a plugin (only if you need one)

Plugins come from a second, separate CLI, the `img2` harness. Install it once, then add plugins
by id (list them with `img2 plugins`; harness 0.4.0 or newer):

```bash
npx --yes @img2threejs/img2@latest install --yes     # once per machine: creates ~/.img2
npx --yes @img2threejs/img2@latest add cs2 --yes     # example id; `img2 plugins` lists all
npx --yes @img2threejs/img2@latest list              # the plugin must be listed here
```

Then restart the agent. `add` before `install` fails with `no harness checkout at …/harness` / ``run `img2 install` first``.

What `install` changes on your machine:

- creates `~/.img2` (or `$IMG2_HOME`) with the harness checkout and plugin registry;
- if `~/.claude/` exists, adds that folder to `permissions.additionalDirectories` in
  `~/.claude/settings.json`, whether or not Claude Code is installed;
- links an `img2` command into `~/.local/bin`, `/opt/homebrew/bin` or `/usr/local/bin`, whichever
  is on your `PATH` and writable. Other `PATH` folders are not used. If none qualifies it prints
  `No writable launcher directory on PATH; use npx instead`, and you keep using the `npx` form
  (or `alias img2='npx --yes @img2threejs/img2@latest'`).

`add` links the plugin as a skill (`img2-<id>`) for Claude Code, Codex and OpenCode. **Hermes**
needs it by hand, per plugin:

```bash
ln -sfn "${IMG2_HOME:-$HOME/.img2}/plugins/cs2" ~/.hermes/profiles/${HERMES_ACTIVE_PROFILE:-img2threejs}/skills/img2-cs2
```

The `environment` plugin is private: it needs a GitHub account with access to its repository.
Why a run cannot see an installed plugin, and how plugins reach the pipeline:
[`PLUGINS.md`](PLUGINS.md).

## 4. Update an existing install

A person only needs to paste this to their agent:

```text
Update img2threejs for me: follow
https://github.com/img2threejs/img2threejs/blob/main/docs/INSTALL.md#4-update-an-existing-install
```

The rest of this section is the procedure for the agent.

### Procedure (for the agent)

Rules for every step:

- Run commands exactly as written, in the `npx --yes …@latest` form. Never run a bare
  `img2threejs` or `img2`: an old launcher on `PATH` runs old code.
- Never move, delete or re-link anything yourself, and never edit dotfiles. When a step says
  *stop*, show the user the command's output verbatim (skip `npm notice` lines) and what it asks
  them to do.
- Do not pass `--ref` unless the user gave you a tag.
- If your host blocks a command (sandbox, network or approval denied), show the user that exact
  command and stop; do not retry it another way.

**1. See what is installed.** Run both:

```bash
npx --yes img2threejs@latest --version
npx --yes img2threejs@latest doctor
```

`--version` prints `img2threejs CLI: <version>`. **0.1.x** always reinstalls v2.0.0 and cannot
replace a dangling link; tell the user no newer skill version is available through the CLI yet.

Each `doctor` agent line ends in one of these states. Match the first row that fits, and handle
each line on its own:

| State at the end of the line | Meaning | Action |
|---|---|---|
| `(no link)` | not installed for that agent | nothing to do |
| `managed → …/releases/<sha>/img2threejs (<version>)` | installed by this CLI | step 2 |
| `invalid skill: ENOENT: …, stat '<link>'` | the link points at a folder that no longer exists | CLI 0.2.0+: step 2 (it replaces the link). CLI 0.1.x: stop, tell the user to remove `<link>` and ask you again |
| `invalid skill: …`, any other message (e.g. `open '…/SKILL.md'`) | a folder without a usable `SKILL.md` | stop: the user decides whether to move it |
| `unmanaged → <path> …` and `git -C <path> remote get-url origin` ends in `/img2threejs/img2threejs` or `:img2threejs/img2threejs` (optionally `.git`) | the user's own clone | `git -C <path> pull --ff-only`; if that fails, stop |
| `unmanaged → <path> …`, anything else (including when `git remote get-url` fails) | a copied folder or someone else's link | stop: the user decides whether to move it |

If every line says `(no link)`, tell the user img2threejs is not installed and point them to
[§1](#1-install-the-skill); do not run step 2.

Hermes: `doctor` checks only `~/.hermes/skills`. A profile link made by hand (§5) shows as
`(no link)` and is not updated by this procedure; say so if the user uses Hermes.

**2. Update the managed and dangling links**, one agent at a time. `<agent>` is the first word of
its `doctor` line (`claude`, `codex`, `opencode`, `hermes`). Always pass `--host`: without it,
one unmanaged agent makes the whole command exit 2, and `update` also installs into every detected
agent that has no link.

```bash
npx --yes img2threejs@latest update --host <agent>
```

| Result | Action |
|---|---|
| exit 0, `✓ <agent>: …` or `already up to date` | next agent |
| exit 0, `… is ahead of latest stable …` | a beta install; leave it, next agent |
| exit 1, `cannot resolve the latest release` / `lookup returned HTTP …` | no access to github.com or a proxy: stop, ask the user for a tag |
| exit 2 (e.g. `refusing to replace …`, `host … not detected`) | stop for this agent; continue with the next |
| any other exit code or message | stop for this agent; continue with the next |

**3. Update the plugin harness and plugins**, only if `${IMG2_HOME:-$HOME/.img2}/plugins.json` or
`${IMG2_HOME:-$HOME/.img2}/harness` exists:

```bash
npx --yes @img2threejs/img2@latest update
```

Non-zero exit: stop. Harness 0.4.x updates plugins only. Harness 0.5.0 and later also move the
harness checkout to the newest release; when they skip it they print why (`no checkout`,
`local origin`, `dirty`, `fetch failed: …`, `no semver tag on origin`, `tag … not on origin`,
`not behind …`). Report a skip reason to the user; do not fix it yourself.

**4. Check and report.** Run `npx --yes img2threejs@latest doctor` again. Tell the user:

- the version each agent now links, and anything you stopped on;
- that they must start a new session (Claude Code) or restart the agent (Codex, OpenCode, Hermes)
  before the new version is used.

### Options (for people running the CLIs by hand)

```bash
npx --yes img2threejs@latest update                         # every detected agent, latest release
npx --yes img2threejs@latest update --ref v2.0.0            # a specific tag or full 40-char SHA
npx --yes @img2threejs/img2@latest update --check           # exits 1 when an update is pending
npx --yes @img2threejs/img2@latest update cs2
npx --yes @img2threejs/img2@latest remove cs2               # unlinks it; checkout moves to ~/.img2/backups
```

Without `--ref`, `install` and `update` use the latest GitHub release, so tags without a published
release are ignored. `update` never downgrades; a beta install newer than the latest stable release
is left as is. `--ref` takes a `vX.Y.Z` tag or a 40-character SHA; branches and short SHAs are
refused. Behind an HTTP(S) proxy the latest-release lookup fails (Node's `fetch` ignores
`HTTPS_PROXY`); pass `--ref <tag>` instead, which only uses Git.

### Uninstall

There is no uninstall command for the skill. Delete each agent's `img2threejs` link (the paths in
`doctor` output, plus any Hermes profile link), then `~/.img2threejs/`. For the harness, `img2 remove`
each plugin, then delete `~/.img2` and the `img2` launcher.

## 5. Manual install (no CLI)

Keep one checkout and symlink it into each agent, so copies cannot drift. Use either this or the
CLI for a given path, not both: the CLI refuses (exit 2) to replace a link it did not create.

```bash
git clone https://github.com/img2threejs/img2threejs.git ~/src/img2threejs
ln -sfn ~/src/img2threejs ~/.claude/skills/img2threejs
ln -sfn ~/src/img2threejs ~/.codex/skills/img2threejs
ln -sfn ~/src/img2threejs ~/.config/opencode/skills/img2threejs
ln -sfn ~/src/img2threejs ~/.hermes/profiles/${HERMES_ACTIVE_PROFILE:-img2threejs}/skills/img2threejs
ln -sfn ~/src/img2threejs ~/.hermes/skills/img2threejs   # only so `doctor` sees Hermes
```

Create the parent `skills/` folder first if it does not exist. A clone follows the default
branch; the CLI pins a release.

## 6. When something is off

| Symptom | Cause | Fix |
|---|---|---|
| `img2threejs: command not found` or `img2: command not found` | not installed globally (expected) | use the `npx --yes …@latest` form shown above |
| `img2threejs` starts something else (e.g. Hermes) | another program owns that name on your `PATH` | use the `npx` form; check `which img2threejs` |
| An agent is missing from the `install` plan | its folder does not exist yet | run that agent once, or pass `--host <name>` |
| `error (2): refusing to replace an unmanaged skill at <path>` | a manual install or other link is already there (a dangling link is replaced automatically) | keep it (for a git checkout: `git -C <path> pull --ff-only`), or move that path away and re-run |
| `error (1): cannot resolve the latest release` / `latest release lookup returned HTTP …` | no access to github.com, or an HTTP(S) proxy | pass `--ref <tag>` |
| `error (2): ref must be a vX.Y.Z tag or 40-char SHA` | `--ref` got a branch or short SHA | pass a tag or the full SHA |
| Slash command or skill not found after install | the agent was not restarted | restart it / open a new session |
| Hermes says "Unknown skill" | no link in the profile folder | the Hermes line in [§1](#1-install-the-skill) |
| `doctor` shows `hermes … (no link)` though Hermes works | `doctor` checks only `~/.hermes/skills/` | link that path too, or ignore |
| A plugin is installed but the run does not offer its profile | it is registered in a different `$IMG2_HOME` | [`PLUGINS.md`](PLUGINS.md#what-a-run-sees-depends-on-img2_home) |
| `~/.claude/settings.json` changed after `img2 install` | `~/.claude/` exists (see §3) | expected; back the file up first if you want to restore it |
| `git ls-remote … failed` / `could not read Username for 'https://github.com'` | private plugin, no GitHub access | sign in with an account that has access and configure Git credentials |

Neither `doctor` proves the result looks right; that is what the reconstruction's own render
gates check.

## 7. Other coding agents

The installer knows four agents. Others can load the skill by hand: clone it (§5) and link or
reference it where the agent reads skills or instructions. **The paths below come from each
vendor's documentation and have not been tested with img2threejs**; check the vendor's docs if one
does not work, and tell us so we can fix the row.

| Agent | Where it reads skills / instructions | Note |
|---|---|---|
| Cursor | `.cursor/skills/<name>/SKILL.md`, `~/.cursor/skills/` | rules use `.cursor/rules/*.mdc` |
| JetBrains Junie | `.junie/skills/<name>/SKILL.md`, `~/.junie/skills/` | prefers `.junie/AGENTS.md` |
| Kiro | `.kiro/skills/<name>/SKILL.md`, `~/.kiro/skills/` | steering in `.kiro/steering/*.md` |
| Pi | `~/.pi/skills/<name>/SKILL.md` | also reads root `AGENTS.md` |
| Droid (Factory) | `~/.factory/skills/` | |
| Crush | `./.crush/skills/`, `~/.config/crush/skills/` | |
| Warp | scans `.agents/`, `.warp/`, `.claude/`, `.codex/` | a Claude/Codex link may be enough |
| Aider | root `AGENTS.md` / `CONVENTIONS.md` | add the file to `read:` in `.aider.conf.yml` |
| Mistral Vibe, Amp, Auggie, Zed | root `AGENTS.md` | point it at the checkout's `SKILL.md` |
| Trae | `AGENTS.md` (enable in Settings → Rules & Memories) | |
| Windsurf | `.windsurf/rules/*.md` | |
| Continue | `.continue/rules/*.md` | CLI is `cn` |

Plugins (`img2 add`) are linked only for Claude Code, Codex and OpenCode. In any other agent, have
it read the plugin's `SKILL.md` from `~/.img2/plugins/<id>/`.

## Reference: the two CLIs

| | Skill CLI | Plugin harness CLI |
|---|---|---|
| npm package | `img2threejs` | `@img2threejs/img2` |
| Installs | this skill into agents | plugins (domain steps, gates, output targets) |
| Agents it links | hermes, claude, codex, opencode | claude, codex, opencode |
| Version check | `npx --yes img2threejs@latest --version` (plain text) | `npx --yes @img2threejs/img2@latest --version --json` (JSON) |

Installing one does not install the other, and neither generates a model: the agent does that
when you ask it to. The first `--yes` in every command belongs to `npx` (allow the package
download); a trailing `--yes` on `img2` skips its confirmation prompt. Neither overwrites anything.
Skill checkouts live in `~/.img2threejs/releases/<sha>/img2threejs`, shared by every agent on the
same release. To test the CLI from a clone: `node bin/img2threejs.mjs install --dry-run`; packaging
checks are in [`CONTRIBUTING.md`](../CONTRIBUTING.md).
