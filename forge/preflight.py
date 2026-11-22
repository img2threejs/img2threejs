#!/usr/bin/env python3
"""Read-only installation preflight; no installs, consent grants or paid Jev calls."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
from types import SimpleNamespace

FORGE = Path(__file__).resolve().parent
sys.path.insert(0, str(FORGE))
sys.path.insert(0, str(FORGE / "_shared"))

from runtime_selectors import img2_home, installed_harness_root, read_selectors
from stage0_route.discovery import Discovery, EDGES, JEV_EDGE, _CHARACTER_GATE_PROBES, read_only_probes
from stage0_route.storage import RouteError
from route import LiveConsent, _approved_lock
from decision_control import DecisionError, GROUPS

CAPABILITIES = {"character": "rigged character", "cs2": "CS2 skin",
                "interior": "interior scene", "jev": "Jev decisions", "pixi2d": "2D scene"}


def _json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("non-finite JSON")
    return json.loads(path.read_bytes(), object_pairs_hook=pairs, parse_constant=invalid)


def _registry_and_pins(home, harness):
    registry, lock = _json(home / "plugins.json"), _json(harness / "plugins.lock.json")
    if (type(registry) is not dict or type(registry.get("version")) is not int
            or registry["version"] != 1 or type(registry.get("plugins")) is not list
            or type(lock) is not dict or type(lock.get("schema")) is not int
            or lock["schema"] != 1 or type(lock.get("plugins")) is not list):
        raise ValueError("invalid registry or lock")
    def index(rows, pin=False):
        result = {}
        for row in rows:
            if type(row) is not dict or type(row.get("id")) is not str or row["id"] in result:
                raise ValueError("invalid or duplicate plugin")
            if pin and (type(row.get("commit")) is not str
                        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", row["commit"]) is None):
                raise ValueError("invalid pin")
            result[row["id"]] = row
        return result
    return index(registry["plugins"]), index(lock["plugins"], pin=True)


def jev_status(discovery, row):
    """Authenticate only the core-approved installed provider, never a PATH alias."""
    release = _approved_lock()
    if row["resolvedSha"] != release["resolvedSha"]:
        return "core-release-pin-mismatch (release lock must be regenerated)"
    # All four decision edges must resolve to this exact provider.
    for kind, _ in GROUPS.values():
        response = discovery.query((kind, kind.removesuffix("context") + "advisory"))
        if response["status"] != "answered" or response["providers"] != [row]:
            return "capability-unavailable"
    discovery.jev, discovery.jev_status = row, "present"
    status = LiveConsent(discovery, release, "install-preflight", sys.executable).auth_status()["status"]
    if status == "credential-missing":
        return "credential-missing; run img2-jev login; no-account choice is not implemented"
    return None if status == "present" else status


def check_plugins(discovery, *, home, harness, selectors, invalid_selectors=False, check_jev=jev_status):
    """Return (plugin, capability, status, reason) rows; registry fixtures stay offline."""
    selected = set(selectors["selectedPlugins"]) if selectors is not None else set(CAPABILITIES)
    try:
        registry, pins = _registry_and_pins(home, harness)
        registry_error = None
    except (OSError, ValueError, TypeError):
        registry, pins, registry_error = {}, {}, "registry-or-plugins-lock-invalid-or-missing"
    rows = []
    with read_only_probes(discovery):
        for name, capability in CAPABILITIES.items():
            if name not in selected:
                rows.append((name, capability, "not installed by choice", "unselected"))
                continue
            reason = None
            if invalid_selectors:
                reason = "selectors-invalid (runtime selectors ignored)"
            elif registry_error:
                reason = registry_error
            elif name not in pins:
                reason = "plugin-lock-entry-missing"
            elif name not in registry:
                reason = "plugin-not-registered"
            elif registry[name].get("resolvedSha") != pins[name]["commit"]:
                reason = "plugin-pin-mismatch"
            else:
                try:
                    edge = JEV_EDGE if name == "jev" else EDGES[name]
                    response = discovery.query(edge)
                    providers = response["providers"]
                    if response["status"] != "answered" or len(providers) != 1:
                        raise RouteError("provider-" + response["status"] if providers else "provider-unavailable")
                    row = providers[0]
                    if row.get("plugin") != name or row.get("resolvedSha") != pins[name]["commit"]:
                        raise RouteError("provider-pin-mismatch")
                    manifest = json.loads(discovery._pinned_file(row, "plugin.json"))
                    if (type(manifest) is not dict or manifest.get("name") != name
                            or {"from": edge[0], "to": edge[1]} not in manifest.get("capabilities", [])):
                        raise RouteError("provider-identity-mismatch")
                    if name == "jev":
                        reason = check_jev(discovery, row)
                    else:
                        card = json.loads(discovery._pinned_file(row, "route.json"))
                        if type(card) is not dict or type(card.get("deps")) is not list:
                            raise RouteError("route-card-invalid")
                        probes = []
                        for dep in card["deps"]:
                            if type(dep) is not dict or type(dep.get("probe")) is not str:
                                raise RouteError("route-card-invalid")
                            probes.append(dep["probe"])
                        if name == "character":
                            probes.extend(p for p in _CHARACTER_GATE_PROBES if p not in probes)
                        missing = [probe for probe in dict.fromkeys(probes) if not discovery.dependency(probe)]
                        if missing:
                            reason = "dependency-missing:" + ",".join(missing)
                except (RouteError, DecisionError) as error:
                    reason = error.reason_code
                except (OSError, ValueError, TypeError, KeyError, AttributeError):
                    reason = "invalid-provider-data"
            rows.append((name, capability, "blocked" if reason else "ready", reason or "pinned; dependencies available"))
    return rows


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    home, harness = img2_home(), installed_harness_root()
    selectors = read_selectors()
    invalid = selectors is None and os.path.lexists(home / "runtime/selectors.json")
    node = shutil.which("node")
    controller = SimpleNamespace(workspace=Path.cwd(), record_probe=lambda fact: fact)
    command = ((str(Path(node).resolve()), "--no-addons", str(harness / "bin/img2.mjs"))
               if node else ("node", str(harness / "bin/img2.mjs")))
    discovery = Discovery(controller, harness=command)
    rows = check_plugins(discovery, home=home, harness=harness, selectors=selectors, invalid_selectors=invalid)
    header = ("plugin", "capability name", "status", "reason")
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(3)]
    for row in [header, *rows]:
        print("  ".join(value.ljust(widths[i]) if i < 3 else value for i, value in enumerate(row)))
    print("fix: img2threejs repair")
    return 0 if all(row[2] in {"ready", "not installed by choice"} for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
