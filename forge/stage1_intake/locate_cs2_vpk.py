#!/usr/bin/env python3
"""Locate a local CS2 install's paks VPK (`pak01_dir.vpk`) so the optional exact-texture upgrade
can extract Valve's authored paint textures from the user's own legal copy. Best-effort and
non-fatal: returns None / {"found": false} when nothing is located, never raises -- the pipeline
falls back to the image-only path. See grimoire/intake/cs2_texture_acquisition.md and openspec
add-cs2-item-reconstruction task 5.3.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# CS2 (Source 2) ships the paints under game/csgo relative to the install dir.
_VPK_RELATIVE = Path("steamapps/common/Counter-Strike Global Offensive/game/csgo/pak01_dir.vpk")


def default_steam_roots() -> list[Path]:
    """Per-OS default Steam library roots. Missing paths are harmless -- they just don't match."""
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library/Application Support/Steam"]
    if sys.platform.startswith("win"):
        return [Path("C:/Program Files (x86)/Steam"), Path("C:/Program Files/Steam")]
    # linux / other
    return [
        home / ".steam/steam",
        home / ".local/share/Steam",
        home / ".steam/root",
    ]


def locate_vpk(roots: list[Path] | None = None) -> Path | None:
    """Return the first existing pak01_dir.vpk across the given (or default) Steam roots.
    Dependency-injectable `roots` keeps this testable offline."""
    search_roots = list(roots) if roots else default_steam_roots()
    seen: set[Path] = set()
    for root in search_roots:
        root = Path(root)
        if root in seen:
            continue
        seen.add(root)
        vpk = root / _VPK_RELATIVE
        if vpk.is_file():
            return vpk
    return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, action="append", dest="roots",
                        help="Steam root to search (repeatable); defaults to per-OS Steam locations")
    parser.add_argument("--json", action="store_true", help="Emit a JSON result object")
    args = parser.parse_args(argv)

    vpk = locate_vpk(args.roots)
    found = vpk is not None
    if args.json:
        result = {"found": found, "path": str(vpk) if vpk else None}
        if not found:
            result["reason"] = "no local CS2 VPK found in the searched Steam roots"
        print(json.dumps(result, indent=2))
    elif found:
        print(str(vpk))
    else:
        print("no local CS2 VPK found", file=sys.stderr)
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
