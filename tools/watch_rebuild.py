#!/usr/bin/env python3
"""watch_rebuild: Watch sculpt-spec JSON files and auto-rebuild factory code on change.

Usage:
  python3 tools/watch_rebuild.py
      Watch all outputs/*-sculpt-spec.json, auto-rebuild on changes.

  python3 tools/watch_rebuild.py --spec outputs/my-project-sculpt-spec.json
      Watch a specific spec file only.

  python3 tools/watch_rebuild.py --spec spec.json --project-dir ~/ZCodeProjects/my-project
      Watch with an explicit project directory.

Press Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
FORGE_DIR = SKILL_DIR / "forge"
PROJECTS_ROOT = Path.home() / "Documents" / "ZCodeProjects"


def pascal(s: str) -> str:
    return s.replace("-", " ").replace("_", " ").title().replace(" ", "")


def extract_name(spec_path: Path) -> str | None:
    """Extract project name from spec filename or content."""
    stem = spec_path.stem
    # Remove trailing -sculpt-spec
    if stem.endswith("-sculpt-spec"):
        name = stem[: -len("-sculpt-spec")]
        if name:
            return name
    # Try reading targetName from JSON
    try:
        data = json.loads(spec_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "targetName" in data:
            return data["targetName"]
    except Exception:
        pass
    return None


def generate_factory(spec_path: Path, project_dir: Path, name: str) -> bool:
    """Run generate_threejs_factory.py on the spec. Returns True on success."""
    name_p = pascal(name)
    out_ts = project_dir / f"create{name_p}Model.ts"
    result = subprocess.run(
        [sys.executable, str(FORGE_DIR / "stage3_build" / "generate_threejs_factory.py"),
         str(spec_path), "--out", str(out_ts), "--force"],
        cwd=SKILL_DIR, capture_output=True, text=True,
    )
    # Print stdout (validation output)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        print(f"  ❌ Factory generation failed: {result.stderr.strip()}")
        return False
    size = out_ts.stat().st_size if out_ts.exists() else 0
    print(f"  ✅ Regenerated: {out_ts.name} ({size} bytes)")
    return True


def validate_spec(spec_path: Path) -> bool:
    """Run validate_sculpt_spec.py. Returns True on pass."""
    result = subprocess.run(
        [sys.executable, str(FORGE_DIR / "stage2_spec" / "validate_sculpt_spec.py"),
         str(spec_path)],
        cwd=SKILL_DIR, capture_output=True, text=True,
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        print(f"  ❌ Validation failed")
        return False
    return True


class FileState:
    """Track mtime and size for a watched file."""

    def __init__(self, path: Path):
        self.path = path
        self.mtime: float = 0
        self.size: int = 0
        self.update()

    def update(self):
        try:
            stat = self.path.stat()
            self.mtime = stat.st_mtime
            self.size = stat.st_size
        except OSError:
            self.mtime = 0
            self.size = 0

    def changed(self) -> bool:
        try:
            stat = self.path.stat()
            return stat.st_mtime != self.mtime or stat.st_size != self.size
        except OSError:
            return False


def find_watch_specs(paths: list[Path] | None,
                     outputs_dir: Path) -> list[Path]:
    """Collect spec files to watch."""
    if paths:
        result = []
        for p in paths:
            pp = Path(p).resolve()
            if pp.exists():
                result.append(pp)
            else:
                print(f"⚠️  Spec not found: {pp}")
        return result
    # Watch all outputs/*-sculpt-spec.json
    pattern = "*-sculpt-spec.json"
    return sorted(outputs_dir.glob(pattern))


def main():
    parser = argparse.ArgumentParser(
        description="Watch sculpt-spec JSON files and auto-rebuild factory code.")
    parser.add_argument("--spec", action="append", dest="specs", default=None,
                        help="Specific spec file(s) to watch (can repeat). "
                             "Default: watch all outputs/*-sculpt-spec.json")
    parser.add_argument("--project-dir", default=None,
                        help="Explicit project directory. "
                             "Default: ~/Documents/ZCodeProjects/<name>/")
    parser.add_argument("--poll-interval", type=float, default=2.0,
                        help="Poll interval in seconds (default: 2.0)")
    parser.add_argument("--debounce", type=float, default=0.5,
                        help="Debounce time in seconds after last change (default: 0.5)")
    args = parser.parse_args()

    outputs_dir = SKILL_DIR / "outputs"
    specs = find_watch_specs(args.specs, outputs_dir)

    if not specs:
        print("❌ No spec files found to watch.")
        sys.exit(1)

    print(f"🔍 Watching {len(specs)} spec file(s):")
    for s in specs:
        print(f"   {s}")
    if args.specs:
        print(f"   (Poll every {args.poll_interval}s, debounce {args.debounce}s)")
    print("   Press Ctrl+C to stop.\n")

    # Track file states
    states = {s: FileState(s) for s in specs}

    try:
        while True:
            changed_files = [s for s, st in states.items() if st.changed()]

            if changed_files:
                # Debounce: wait a bit to let writes settle
                time.sleep(args.debounce)
                # Re-check after debounce
                still_changed = [s for s in changed_files if states[s].changed()]

                if still_changed:
                    os.system("cls" if os.name == "nt" else "clear")
                    print(f"[{time.strftime('%H:%M:%S')}] Change detected — rebuilding...\n")

                    for spec_path in still_changed:
                        name = extract_name(spec_path)
                        if not name:
                            print(f"⚠️  Cannot determine project name from {spec_path.name}, skipping.")
                            states[spec_path].update()
                            continue

                        print(f"📄 {spec_path.name} → project: {name}")

                        # Determine project directory
                        if args.project_dir:
                            proj_dir = Path(args.project_dir).resolve()
                        else:
                            proj_dir = PROJECTS_ROOT / name

                        if not proj_dir.exists():
                            print(f"⚠️  Project dir not found: {proj_dir}")
                            print(f"   Run auto_tree first, or create the project manually.")
                            states[spec_path].update()
                            continue

                        # Validate
                        print(f"   🔍 Validating...")
                        validate_spec(spec_path)

                        # Rebuild
                        print(f"   🔧 Building factory...")
                        generate_factory(spec_path, proj_dir, name)

                        print()

                        # Update state
                        states[spec_path].update()

            time.sleep(args.poll_interval)

    except KeyboardInterrupt:
        print("\n👋 Watch stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
