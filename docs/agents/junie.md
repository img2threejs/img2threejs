# JetBrains Junie (CLI + IDE)

**Tier 1 — mass-market, opinionated.** Best for developers already in
JetBrains IDEs (IntelliJ, PyCharm, GoLand, …) — install the Junie
plugin once, both surfaces share the same guidelines/skills.

## Install

Two paths depending on which surface you want:

```bash
# CLI (the standalone `junie` binary in ~/.local/bin/junie)
curl -fsSL https://junie.jetbrains.com/install.sh | bash
# Homebrew (macOS):  brew tap jetbrains-junie/junie && brew install junie
# npm (cross-platform, EAP):  npm install -g @jetbrains/junie
# Windows PowerShell:
#   powershell -NoProfile -ExecutionPolicy Bypass -Command "iex (irm 'https://junie.jetbrains.com/install.ps1')"
```

Verify with `junie --version`. Auth options: JetBrains account, Junie
API key, or BYOK (Anthropic, OpenAI, Google, xAI, OpenRouter, GitHub
Copilot).

## What "working" looks like

```text
$ junie --version
Junie version: 26.10.12 (3659.7)
```

Verified on 2026-10-17 in a clean `debian:13-slim` container: the
`curl | bash` one-liner above downloads `junie-release-<build>-linux-amd64.zip`
from the JetBrains releases, verifies its checksum, and installs to
`~/.local/bin/junie`. `junie --version` prints the marketing version
(`26.10.12`) and the build number (`3659.7`). Running `junie` without
auth produces an explicit "Cannot find authorization" error and points
at `junie --auth <api-key>` or `JUNIE_API_KEY` — by design, the
install does not require any auth.

## Skill loading path

Junie reads guidelines first, then skills. First-match wins:

1. **`.junie/AGENTS.md`** (project root) — preferred; standalone, no
   combination with other files.
2. **`AGENTS.md`** (project root) combined with
   **`.junie/playbook.md`** + **`.junie/rules/*.md`** if present.
3. **`.junie/guidelines.md`** or **`.junie/guidelines/`** — legacy,
   deprecated.

Global guidelines: **`~/.junie/AGENTS.md`** — combined with project
guidelines, project wins on conflict. Override the filename with
`JUNIE_GUIDELINES_FILENAME` or `--guidelines-filename`.

On first project open, Junie CLI scans `CLAUDE.md`, `AGENTS.md`,
`GEMINI.md`, … and offers to **import** them into `.junie/AGENTS.md` —
it does not read them directly for prompt context.

Skills — open `Agent Skills` standard, discovery order:

1. `.junie/skills/<name>/SKILL.md` — workspace, VCS-shareable.
2. `~/.junie/skills/<name>/SKILL.md` — user scope.
3. `.agents/skills/<name>/SKILL.md` and `~/.agents/skills/<name>/SKILL.md`
   — cross-agent (shared with Crush, Pi, Warp).
4. Extension-provided skills via `.junie/extensions.json`.
5. Custom locations via `--skill-location` or `config.json`'s
   `skill-locations`.

## Manual symlink

```text
.junie/skills/img2threejs  ->  <your checkout>      # workspace, VCS-shareable
~/.junie/skills/img2threejs -> <your checkout>      # global, applies everywhere
```

## Troubleshooting

| Symptom                                                              | Cause                                                                  | Action                                                                                  |
|----------------------------------------------------------------------|------------------------------------------------------------------------|-----------------------------------------------------------------------------------------|
| `.junie/guidelines.md` ignored after a JetBrains release              | Legacy format is deprecated                                            | Move content to `.junie/AGENTS.md` or to `AGENTS.md` + `.junie/rules/*.md`               |
| Junie ignores the repo's `CLAUDE.md` / `AGENTS.md`                   | Junie reads only `.junie/AGENTS.md` (or `.junie/rules/*.md`) directly  | On first open, accept the import prompt; or symlink `.junie/AGENTS.md -> ../AGENTS.md`   |
| Skill path created but Junie doesn't see it                          | Discovery looks under `.junie/skills/` (not `.junie/agents/skills/`)    | Verify the symlink lives at `.junie/skills/img2threejs/SKILL.md` (or check perms)         |

## Cross-references

- [`docs/INSTALL.md`](../INSTALL.md) — base-skill install (Junie is not
  in the `HOSTS` table).
- [`docs/PLUGINS.md`](../PLUGINS.md#host-skills) — absolute-path
  workaround for plugin `SKILL.md` under Junie.
- Harness `HOSTS` table (`bin/img2threejs.mjs`) — `{hermes, claude,
  codex, opencode}` only; Junie is not a row.