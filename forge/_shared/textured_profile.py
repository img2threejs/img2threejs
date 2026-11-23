"""The base-owned ``textured`` build profile: reference -> generated textured GLB -> Blender cleanup
-> Jev likeness loop -> delivery.

This is a base profile, not a registered domain: a plugin never splices into it and no plugin can
change what its rows are. A domain id may be attached (``state["domain"]``) so that domain's own
rig-track checks still run on the delivered GLB; its procedural setup/pass steps do not apply.

Everything here is stdlib and deterministic. The generator itself is an AGENT step (the host's MCP
tool); this module only decides whether such a tool is configured and names the rows.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Final

TEXTURED_PROFILE: Final = "textured"
PROCEDURAL_PREFIX: Final = "procedural-"
BASE_PROFILES: Final = ("generic", TEXTURED_PROFILE)
DEFAULT_MAX_GENERATIONS: Final = 4
MIN_GENERATIONS: Final = 1  # the range `state.py init --max-generations` accepts, and every state must stay within
MAX_GENERATIONS: Final = 8

# Rows that are re-opened for every new generation. `reference-admission` is per run, `deliver` is
# terminal; everything between them belongs to one generation attempt.
GENERATION_ROWS: Final = ("generate", "inspect", "normalize", "render", "likeness", "decide")

TEXTURED_STEPS: Final = (
    (
        "reference-admission",
        "python3 forge/stage1_intake/check_reference_admission.py {reference}"
        " --out admission.json --probe-out probe.json",
    ),
    (
        "generate",
        "AGENT STEP (generation {gen}): call the host MCP generator (mcp__tripo_make: input={reference},"
        " scenario film|game-pc|..., model tripo-v3.1|tripo-p1|tripo-p2; a regeneration MUST change model,"
        " scenario, prompt or the input image), download the result, then record it:"
        " python3 forge/textured/generate.py record --workspace . --gen {gen} --input {reference}"
        " --task-id <id> --model <model> --scenario <scenario> --credits <n> --model-file <downloaded model>"
        " [--model-dir <dir with textures>]",
    ),
    ("inspect", "python3 forge/textured/inspect.py --workspace . --gen {gen}"),
    ("normalize", "python3 forge/textured/normalize.py --workspace . --gen {gen} --height 1.8"),
    ("render", "python3 forge/textured/render.py --workspace . --gen {gen}"),
    (
        "likeness",
        "python3 forge/textured/sheet.py --workspace . --gen {gen} --reference {reference}"
        " && python3 forge/textured/likeness.py run --workspace . --gen {gen} --kind <character|creature|vehicle|"
        "prop|furniture|building|generic> --run-id <run-id>   (Jev; no account: likeness.py questions, then"
        " record-model-answers --decider model)",
    ),
    (
        "decide",
        "python3 forge/textured/decide.py --workspace . --state .img2threejs/state.json"
        " (SAME accepts; DIFFERENT regenerates with a changed input, up to {max_gen} generations; best by Jev)",
    ),
)

DELIVER_STEPS: Final = (
    (
        "deliver",
        "python3 forge/textured/deliver.py --workspace . --reference {reference}"
        " (final GLB + renders + comparison sheet + REPORT.md under .img2threejs/textured/final/)",
    ),
)

_MCP_SERVER_NAME = re.compile(r"tripo", re.IGNORECASE)


def normalize_profile(profile: str) -> str:
    """``procedural-generic`` / ``procedural-<domain>`` name the procedural pipelines explicitly.

    The stored profile stays the underlying id, so every consumer that reads ``state["profile"]``
    (domain registry, plugin gate participation, persisted workspaces) is unaffected.
    """
    if isinstance(profile, str) and profile.startswith(PROCEDURAL_PREFIX) and len(profile) > len(PROCEDURAL_PREFIX):
        return profile[len(PROCEDURAL_PREFIX):]
    return profile


def _host_config_paths(env: dict[str, str]) -> list[Path]:
    home = Path(env.get("HOME") or Path.home())
    return [
        home / ".omp" / "agent" / "mcp.json",
        home / ".claude.json",
        home / ".claude" / "mcp.json",
        home / ".codex" / "config.toml",
        home / ".config" / "opencode" / "opencode.json",
    ]


def generator_available(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Is an image->textured-3D generator MCP tool configured for a host on this machine?

    ``IMG2_TEXTURED_GENERATOR`` is the explicit override: ``off``/``0``/``none`` disables, ``tripo``
    (or any other non-empty value) asserts availability. Otherwise the host MCP configuration files
    are scanned for a server whose NAME contains ``tripo``. Presence of a configuration is the only
    thing checked: it is not proof of an account or credits, and no network call is made.
    """
    env = os.environ if environ is None else environ
    forced = (env.get("IMG2_TEXTURED_GENERATOR") or "").strip()
    if forced:
        if forced.lower() in {"off", "0", "none", "false", "no"}:
            return {"available": False, "via": "env", "detail": "IMG2_TEXTURED_GENERATOR disables the generator"}
        return {"available": True, "via": "env", "detail": f"IMG2_TEXTURED_GENERATOR={forced}"}
    for path in _host_config_paths(env):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if path.suffix == ".toml":
            found = re.search(r"^\s*\[mcp_servers\.[\"']?([^\]\"']*tripo[^\]\"']*)", text, re.IGNORECASE | re.MULTILINE)
            server = found.group(1) if found else None
        else:
            try:
                data = json.loads(text)
            except ValueError:
                continue
            servers: dict[str, Any] = {}
            if isinstance(data, dict):
                for key in ("mcpServers", "mcp", "servers"):
                    if isinstance(data.get(key), dict):
                        servers.update(data[key])
            server = next((name for name in servers if _MCP_SERVER_NAME.search(str(name))), None)
        if server:
            return {"available": True, "via": str(path), "detail": f"MCP server {server!r}"}
    return {"available": False, "via": None, "detail": "no MCP server named like 'tripo' in the known host configs"}


def default_profile(environ: dict[str, str] | None = None) -> str:
    """The build profile for 3D outputs when the caller names none."""
    return TEXTURED_PROFILE if generator_available(environ)["available"] else "generic"


def textured_rows(max_generations: int) -> list[tuple[str, str]]:
    rows = [(step_id, command.replace("{max_gen}", str(max_generations))) for step_id, command in TEXTURED_STEPS]
    return rows


def textured_block(generation: int, max_generations: int) -> dict[str, Any]:
    return {"generation": generation, "maxGenerations": max_generations, "history": []}
