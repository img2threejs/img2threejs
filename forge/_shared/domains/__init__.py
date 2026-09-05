"""Domain profiles the base pipeline can run.

The base pipeline names no domain. It asks this registry what is available, and a domain contributes
its own steps, its own evidence collection and its own track. Each module in this package declares a
single `DOMAIN` mapping; registration is by presence, not by the base holding a list of names.

This is the extension point the CS2 extraction needs. Today both entries are in-repo. When a domain
moves out to a plugin, its module leaves this directory and the harness supplies the same mapping
instead -- the base pipeline does not change either way, which is the property being bought here.

Required keys:
    id                  the profile identifier a run is started with
Optional keys, each defaulting to "contributes nothing":
    setupSteps          ((step_id, command), ...) spliced into the setup phase
    setupAnchorBefore   base step id to splice before; required when setupSteps is non-empty
    passSteps           ((step_id, command), ...) spliced into every correction pass
    passAnchorBefore    base step id to splice before; required when passSteps is non-empty
    specCollection      an extra local-evidence collection to search
    rigSteps            ((step_id, command), ...) appended after the FINAL steps as the rig track;
                        no anchor -- the rig phase always follows the terminal steps (v1.5.2)
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any, Final

_PACKAGE_DIR: Final = Path(__file__).resolve().parent

_REQUIRED: Final = ("id",)
_ALLOWED: Final = {
    "id",
    "setupSteps",
    "setupAnchorBefore",
    "passSteps",
    "passAnchorBefore",
    "specCollection",
    "rigSteps",
}


class DomainRegistryError(ValueError):
    pass


def _load_module(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(f"_img2_domain_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise DomainRegistryError(f"cannot load domain module {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate(entry: Any, source: Path) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise DomainRegistryError(f"{source.name}: DOMAIN must be a mapping")
    for key in _REQUIRED:
        if not entry.get(key):
            raise DomainRegistryError(f"{source.name}: DOMAIN is missing required key {key!r}")
    unknown = sorted(set(entry) - _ALLOWED)
    if unknown:
        # Refused rather than ignored: a typo'd key would otherwise silently contribute nothing.
        raise DomainRegistryError(f"{source.name}: DOMAIN has unknown key(s) {unknown}")
    for steps_key, anchor_key in (("setupSteps", "setupAnchorBefore"), ("passSteps", "passAnchorBefore")):
        if entry.get(steps_key) and not entry.get(anchor_key):
            raise DomainRegistryError(f"{source.name}: {steps_key} needs {anchor_key}")
    return entry


def registered_domains() -> dict[str, dict[str, Any]]:
    """Every domain profile available to this run, keyed by id.

    Two sources, treated identically once loaded: modules in this package (in-repo domains) and
    `domain.json` in each installed plugin. A domain that moves from the first to the second changes
    nothing for the base pipeline -- that equivalence is the point of the registry.
    """
    found: dict[str, dict[str, Any]] = {}

    def claim(entry: Any, source: Path) -> None:
        entry = _validate(entry, source)
        if entry["id"] in found:
            # Two providers claiming one id is ambiguous; the base must not pick one.
            raise DomainRegistryError(f"domain id {entry['id']!r} is declared twice")
        found[entry["id"]] = entry

    for path in sorted(_PACKAGE_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        module = _load_module(path)
        entry = getattr(module, "DOMAIN", None)
        if entry is None:
            raise DomainRegistryError(f"{path.name}: a domain module must declare DOMAIN")
        claim(entry, path)

    for path, entry in _installed_plugin_domains():
        claim(entry, path)

    return found


def _img2_home() -> Path:
    return Path(os.environ.get("IMG2_HOME") or Path.home() / ".img2")


def _installed_plugin_domains() -> list[tuple[Path, Any]]:
    """Domain declarations from installed plugins.

    Reads the harness registry rather than globbing the plugins directory, so a checkout left behind
    by a removed plugin does not silently contribute steps. A plugin that ships no `domain.json`
    simply contributes no domain -- it may still provide capabilities.
    """
    registry = _img2_home() / "plugins.json"
    if not registry.is_file():
        return []
    try:
        rows = json.loads(registry.read_text(encoding="utf-8")).get("plugins") or []
    except (OSError, json.JSONDecodeError) as exc:
        raise DomainRegistryError(f"cannot read the plugin registry at {registry}: {exc}") from exc

    out: list[tuple[Path, Any]] = []
    for row in rows:
        plugin_id = (row or {}).get("id")
        if not plugin_id:
            continue
        declaration = _img2_home() / "plugins" / plugin_id / "domain.json"
        if not declaration.is_file():
            continue
        try:
            entry = json.loads(declaration.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DomainRegistryError(f"cannot read {declaration}: {exc}") from exc
        # Steps arrive as JSON arrays; the splice unpacks them as (id, command) pairs. {plugin_dir}
        # is resolved here, the same substitution the harness performs, so a checklist command a
        # plugin supplied is runnable from the workspace without the caller knowing where it lives.
        plugin_dir = str(declaration.parent)
        for key in ("setupSteps", "passSteps", "rigSteps"):
            if key in entry:
                try:
                    entry[key] = tuple(
                        (step_id, command.replace("{plugin_dir}", plugin_dir)) for step_id, command in entry[key]
                    )
                except (TypeError, ValueError, AttributeError) as exc:
                    # Unpacking used to run before validation, so a malformed row surfaced as a bare
                    # ValueError/AttributeError instead of an error naming the file at fault.
                    raise DomainRegistryError(
                        f"{declaration}: {key!r} rows must be [id, command] pairs of strings"
                    ) from exc
        out.append((declaration, entry))
    return out


def domain_profile(profile: str) -> dict[str, Any] | None:
    """The registered domain for `profile`, or None for the generic pipeline."""
    if profile == "generic":
        return None
    domains = registered_domains()
    if profile not in domains:
        known = ", ".join(sorted(["generic", *domains]))
        raise DomainRegistryError(
            f"no installed provider serves profile {profile!r}; available: {known}. "
            "Install the domain plugin that provides it, or start the run as generic."
        )
    return domains[profile]
