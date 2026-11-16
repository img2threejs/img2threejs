# AWS Kiro (IDE + CLI)

**Tier 1 — mass-market, opinionated.** Best for AWS-leaning teams that
already use Kiro's steering-files convention; the `kiro-cli` TUI shares
auth and skills with the IDE.

## Install

Kiro's official installer (Linux, Windows); macOS and Linux also have a
Homebrew-published package; the IDE form downloads a standalone
installer from <https://kiro.dev>:

```bash
curl -fsSL https://cli.kiro.dev/install | bash
# Verify:  kiro-cli --version
# Update:  kiro-cli update
```

The IDE and CLI share the same `.kiro/` configuration — sign in once,
the same auth state and skills apply to both. `kiro-cli chat` is the
headless-friendly entry point; bare `kiro-cli` launches the interactive
TUI.

## What "working" looks like

```text
$ kiro-cli --version
kiro-cli 2.32.0
```

Verified on 2026-10-17 in a clean `debian:13-slim` container: the
`curl | bash` one-liner downloads the `kiro-cli` package from
<https://cli.kiro.dev>, extracts it, and installs to
`~/.local/bin/kiro-cli`. `kiro-cli --version`, `-V`, and `version` all
return `kiro-cli 2.32.0`. `kiro-cli doctor` is the on-host diagnostic
for sign-in / settings / agent issues.

## Skill loading path

Kiro's primary instruction mechanism is **steering** (always loaded),
with skills loading on demand:

- `.kiro/steering/<file>.md` — workspace, VCS-shareable. CLI ships
  three defaults: `product.md`, `structure.md`, `tech.md`.
- `~/.kiro/steering/<file>.md` — global, applies to every workspace.
  Workspace wins on conflict.
- `AGENTS.md` (project root) — also supported; same role as in Tier-3
  CLI hosts.

Skills — open `Agent Skills` standard:

- `.kiro/skills/<name>/SKILL.md` — workspace, VCS-shareable.
- `~/.kiro/skills/<name>/SKILL.md` — global.

Skills load on demand: only the skill name and description load at
startup; the full `SKILL.md` loads only when the request matches.
Match-on-slash works — type `/<skill-name>` in chat to invoke directly.

## Manual symlink

```text
.kiro/skills/img2threejs  ->  <your checkout>        # workspace, VCS-shareable
~/.kiro/skills/img2threejs -> <your checkout>        # global, applies everywhere
```

A workspace-level steering file that always points at the skill is also
valid:

```text
.kiro/steering/img2threejs.md -> <your checkout>/SKILL.md
# Or symlink the whole skills folder, same as above.
```

`.kiro/` and `~/.kiro/` are Kiro-only — the link never conflicts with
another host.

## Troubleshooting

| Symptom                                                       | Cause                                                                | Action                                                                                  |
|---------------------------------------------------------------|----------------------------------------------------------------------|-----------------------------------------------------------------------------------------|
| Steering file ignored                                         | Filename is `<file>.md` but lives outside `.kiro/steering/`           | Move into `.kiro/steering/`, or symlink it in                                            |
| Skill name typed in chat (`/img2threejs`) does not invoke      | Skill folder is at the wrong path or `SKILL.md` frontmatter is missing | Verify `.kiro/skills/img2threejs/SKILL.md` exists with `name` and `description` frontmatter |
| Both IDE and CLI load the same skill twice                    | Skill is linked at both workspace and global; not a bug               | Expected behaviour; pick one scope per host                                                |

## Cross-references

- [`docs/INSTALL.md`](../INSTALL.md) — base-skill install (Kiro is not
  in the `HOSTS` table).
- [`docs/PLUGINS.md`](../PLUGINS.md#host-skills) — absolute-path
  workaround for plugin `SKILL.md` under Kiro.
- Harness `HOSTS` table (`bin/img2threejs.mjs`) — `{hermes, claude,
  codex, opencode}` only; Kiro is not a row.