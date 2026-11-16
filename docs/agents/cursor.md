# Cursor (Cursor CLI + IDE)

**Tier 1 — mass-market, opinionated.** Best for developers who already
work inside the Cursor IDE and want AI inside the same surface; the
`agent` CLI is the headless-friendly entry point.

## Install

Official one-liner — macOS, Linux, and Windows under WSL. The native
Windows installer is a PowerShell `irm | iex`:

```bash
curl https://cursor.com/install -fsS | bash
# Windows (native, PowerShell):
#   irm 'https://cursor.com/install?win32=true' | iex
```

The CLI binary is `agent` and lands at `~/.local/bin/agent` (auto-added
to `PATH` by the install script). Force an update with `agent update`.

## What "working" looks like

After install, `agent --version` returns the CLI version line. Auto-update
is on by default — `agent update` is the manual refresh.

```text
$ agent --version
2026.10.16-e3cb207
```

Verified on 2026-10-17 in a clean `debian:13-slim` container: the
one-liner above installs to `~/.local/bin/agent`, the binary is named
`agent` (not `cursor`), and `agent --version` returns a date-stamped
build identifier (`2026.10.16-e3cb207` at install time). `agent update`
is the manual refresh; auto-update is on by default.

## Skill loading path

Cursor follows the open `Agent Skills` standard:

- Workspace: `.cursor/skills/<name>/SKILL.md` (VCS-shareable).
- Global: `~/.cursor/skills/<name>/SKILL.md` (applies to every project).

The built-in `/create-skill` slash command scaffolds a new skill folder
with the right `SKILL.md` frontmatter. Cursor's **rule** system (`.mdc`
files in `.cursor/rules/`) is distinct from skills — see below.

## Manual symlink

```text
# Workspace skill — visible to every Cursor project in this repo
.cursor/skills/img2threejs  ->  <your checkout>

# Global skill — visible to every Cursor project on this machine
~/.cursor/skills/img2threejs -> <your checkout>
```

A rule is only needed if you want a short always-applied pointer to the
skill or to scope it to specific globs. Cursor's rules live in
`.cursor/rules/*.mdc` with YAML frontmatter (`description`, `globs`,
`alwaysApply`). A plain `.cursor/rules/*.md` (no frontmatter) is
**ignored** — use `AGENTS.md` instead, or add the frontmatter.

## Troubleshooting

| Symptom                                                       | Cause                                                                  | Action                                                                                          |
|---------------------------------------------------------------|------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------|
| Rule dropped into `.cursor/rules/foo.md` has no effect        | Cursor's rules system requires `.mdc` + YAML frontmatter               | Rename to `.cursor/rules/foo.mdc` and add `description` / `globs` / `alwaysApply` frontmatter    |
| `agent` not found after install                               | `~/.local/bin` is not on `PATH` for the current shell                  | `export PATH="$HOME/.local/bin:$PATH"` in `~/.bashrc` / `~/.zshrc`; re-source, then `agent --version` |
| Skill link present but `SKILL.md` not picked up in chat       | Skills load on demand; the description didn't match                    | `/create-skill` to scaffold correctly, or type `/img2threejs` to invoke by name                  |

## Cross-references

- [`docs/INSTALL.md`](../INSTALL.md) — base-skill install (Cursor is not
  in the `HOSTS` table; the link above is hand-rolled).
- [`docs/PLUGINS.md`](../PLUGINS.md#host-skills) — absolute-path workaround
  for plugin `SKILL.md` under Cursor.
- Harness `HOSTS` table (`bin/img2threejs.mjs`) — `{hermes, claude,
  codex, opencode}` only; Cursor is not a row.