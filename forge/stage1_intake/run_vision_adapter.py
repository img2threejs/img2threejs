#!/usr/bin/env python3
"""Run an optional vision adapter without adding dependencies to forge."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PYTHON = ROOT / "integrations" / "vision" / ".venv" / "bin" / "python"
ADAPTER = ROOT / "integrations" / "vision" / "reference_vision.py"


def resolve_python(explicit: str | None) -> Path:
    configured = explicit or os.environ.get("IMG2THREEJS_VISION_PYTHON")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_PYTHON


def build_command(python: Path, forwarded: list[str]) -> list[str]:
    return [str(python), str(ADAPTER), *forwarded]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vision-python", help="Override the isolated adapter interpreter")
    parser.add_argument("adapter_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    python = resolve_python(args.vision_python)
    if not python.exists():
        parser.error(
            f"optional vision interpreter not found at {python}; "
            "run `uv sync --project integrations/vision --python 3.11`"
        )
    if not args.adapter_args:
        parser.error("provide an adapter command: health, prefetch, segment, depth, or landmarks")
    completed = subprocess.run(build_command(python, args.adapter_args), check=False)
    return int(completed.returncode)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    try:
        sys.path.insert(0, str(next(
            parent / "forge" / "_shared"
            for parent in Path(__file__).resolve().parents
            if (parent / "forge" / "_shared" / "cli_run.py").is_file()
        )))
        from cli_run import run_entry
    except (ImportError, StopIteration):
        # vendored/fixture copies without the forge runtime: run bare, no pipe handling
        def run_entry(main_fn, argv=None):
            return main_fn(sys.argv[1:] if argv is None else argv)

    raise SystemExit(run_entry(main))
