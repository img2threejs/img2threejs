#!/usr/bin/env python3
"""Optional exactness upgrade: extract a CS2 skin's authored paint textures from the user's own
legal install using Source2Viewer-CLI, then classify the output maps by convention. Degrades
gracefully -- every failure (no VPK, no extractor binary, subprocess error, non-zero exit) returns
a {"status": "fallback", "reason": ...} result so the pipeline can fall back to the image-only
path, never a wrong render. Extracted pixels are Valve IP: they stay in the gitignored cs2_textures/
workspace and are never committed. See grimoire/intake/cs2_texture_acquisition.md and openspec
add-cs2-item-reconstruction tasks 5.4/5.5.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import locate_cs2_vpk  # noqa: E402  (sibling module, resolved via the path insert above)
from cs2_foundation import classify_map_path  # noqa: E402

SOURCE2VIEWER_BINARY = "Source2Viewer-CLI"

# Filename-convention buckets for Source 2 material maps. A map that matches nothing lands in
# "other" rather than being dropped, so an unexpected naming convention is visible, not silent.
_MAP_CLASSIFIERS = (
    ("color", ("_color", "_albedo", "_basecolor", "_diffuse")),
    ("normal", ("_normal", "_bump", "_nrm")),
    ("roughness_metalness", ("_rough", "_metal", "_orm", "_mrao", "_packed")),
    ("mask", ("_mask", "_ao", "_occlusion", "_pattern", "_wear")),
)


def source2viewer_available() -> bool:
    """True when the Source2Viewer-CLI binary is on PATH (it needs the .NET runtime to run)."""
    return shutil.which(SOURCE2VIEWER_BINARY) is not None


def run_source2viewer(vpk: Path, out_dir: Path, filter_glob: str | None = None) -> subprocess.CompletedProcess:
    """Wrap the Source2Viewer-CLI extraction call. Raises FileNotFoundError if the binary is
    absent; returns the CompletedProcess otherwise (caller inspects returncode)."""
    cmd = [SOURCE2VIEWER_BINARY, "-i", str(vpk), "-o", str(out_dir), "-d", "-e", "vtex_c"]
    if filter_glob:
        cmd += ["-f", filter_glob]
    return subprocess.run(cmd, capture_output=True, text=True)


def classify_extracted_maps(out_dir: Path) -> dict[str, list[str]]:
    """Bucket the files under out_dir into color / normal / roughness_metalness / mask / other by
    filename convention."""
    buckets: dict[str, list[str]] = {name: [] for name, _ in _MAP_CLASSIFIERS}
    buckets["other"] = []
    if not out_dir.is_dir():
        return buckets
    for path in sorted(out_dir.rglob("*")):
        if not path.is_file():
            continue
        lower = path.name.lower()
        for name, suffixes in _MAP_CLASSIFIERS:
            if any(token in lower for token in suffixes):
                buckets[name].append(str(path))
                break
        else:
            buckets["other"].append(str(path))
    return buckets


def build_asset_records(out_dir: Path) -> list[dict]:
    records = []
    for path in sorted(out_dir.rglob("*")):
        if path.is_file():
            record = classify_map_path(path)
            record.update({"source": "local-vpk", "ipBoundary": "local-user-install-not-redistributable"})
            records.append(record)
    return records


def extract(out_dir: Path, vpk: Path | None, roots: list[Path] | None, filter_glob: str | None) -> dict:
    if vpk is None:
        vpk = locate_cs2_vpk.locate_vpk(roots)
    if vpk is None:
        return {"status": "fallback",
                "reason": "no local CS2 VPK found; falling back to image-only reconstruction "
                          "(exact texture likeness unavailable)"}
    if not source2viewer_available():
        return {"status": "fallback",
                "reason": f"{SOURCE2VIEWER_BINARY} not found on PATH; falling back to image-only "
                          "reconstruction (exact texture likeness unavailable)"}
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = run_source2viewer(vpk, out_dir, filter_glob)
    except OSError as exc:
        return {"status": "fallback", "reason": f"{SOURCE2VIEWER_BINARY} failed to run ({exc}); "
                "falling back to image-only reconstruction"}
    if result.returncode != 0:
        return {"status": "fallback",
                "reason": f"{SOURCE2VIEWER_BINARY} exited {result.returncode}: "
                          f"{(result.stderr or '').strip()[:200]}; falling back to image-only"}
    return {"status": "ok", "vpk": str(vpk), "outDir": str(out_dir),
            "maps": classify_extracted_maps(out_dir), "assets": build_asset_records(out_dir)}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="Output directory (gitignored cs2_textures/)")
    parser.add_argument("--vpk", type=Path, help="Explicit pak01_dir.vpk path (skips locating)")
    parser.add_argument("--root", type=Path, action="append", dest="roots",
                        help="Steam root to search when --vpk is omitted (repeatable)")
    parser.add_argument("--filter", dest="filter_glob", help="Source2Viewer -f filter, e.g. a paint kit name")
    parser.add_argument("--json", action="store_true", help="Emit a JSON result object")
    args = parser.parse_args(argv)

    result = extract(args.out.expanduser(), args.vpk, args.roots, args.filter_glob)
    if args.json:
        print(json.dumps(result, indent=2))
    elif result["status"] == "ok":
        print(f"extracted to {result['outDir']}")
    else:
        print(result["reason"], file=sys.stderr)
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
