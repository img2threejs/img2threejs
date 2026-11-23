"""Shared plumbing for the textured tools: workspace layout, receipts, bpy selection and delegation.

Stdlib only. The bpy-dependent tools (inspect, normalize, render, sheet) run under the bpy python
selected by ``IMG2_CHARACTER_BPY_PYTHON`` or ``IMG2_BLENDER_PYTHON`` (process environment first,
then ``$IMG2_HOME/runtime/selectors.json`` through ``runtime_selectors``). A tool started under any
other interpreter re-executes itself under that bpy python; nothing is ever installed or downloaded.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_SHARED = str(_HERE.parent / "_shared")
if _SHARED not in sys.path:
    sys.path.append(_SHARED)

BPY_ENV_KEYS = ("IMG2_CHARACTER_BPY_PYTHON", "IMG2_BLENDER_PYTHON")
IN_BPY_FLAG = "--in-bpy"
RECEIPT_VERSION = 1


class TexturedError(Exception):
    """A user-facing failure; the CLI prints the message and exits 2."""

    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- workspace layout

def textured_root(workspace: Path) -> Path:
    return Path(workspace) / ".img2threejs" / "textured"


def gen_dir(workspace: Path, generation: int) -> Path:
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise TexturedError("--gen must be a positive integer")
    return textured_root(workspace) / f"gen-{generation}"


def resolve_workspace(value: str | os.PathLike[str] | None) -> Path:
    path = Path(value or ".").expanduser().resolve()
    if not path.is_dir():
        raise TexturedError(f"workspace is not a directory: {path}")
    return path


# ---------------------------------------------------------------- json + hashes

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise TexturedError(f"missing file: {path}") from None
    except ValueError:
        raise TexturedError(f"not valid JSON: {path}") from None


def write_json(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, sort_keys=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def receipt(tool: str, **fields: Any) -> dict[str, Any]:
    return {"kind": f"img2.textured-{tool}", "version": RECEIPT_VERSION, "tool": tool,
            "createdAt": now_iso(), **fields}


def workspace_relative(workspace: Path, path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(workspace).resolve()))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------- bpy selection

def select_bpy_python(environ: dict[str, str] | None = None) -> str | None:
    """The selected bpy interpreter path, or None. A process variable (even set) wins over the file."""
    env = os.environ if environ is None else environ
    for key in BPY_ENV_KEYS:
        value = env.get(key)
        if value:
            return value
    try:
        from runtime_selectors import read_selectors
        selectors = read_selectors()
    except Exception:  # selectors are optional; an unreadable file is simply "not selected"
        selectors = None
    if selectors:
        for key in BPY_ENV_KEYS:
            value = selectors["env"].get(key)
            if value:
                return value
    return None


def bpy_importable() -> bool:
    try:
        import bpy  # noqa: F401
        return True
    except ImportError:
        return False


def delegate_to_bpy(script: str, argv: list[str]) -> int | None:
    """Return None when bpy is importable here (run in-process); else run ``script`` under the
    selected bpy python and return its exit code."""
    if bpy_importable():
        return None
    python = select_bpy_python()
    if not python:
        raise TexturedError(
            "no bpy python selected: set IMG2_CHARACTER_BPY_PYTHON or IMG2_BLENDER_PYTHON "
            "(or run `img2 provision` so $IMG2_HOME/runtime/selectors.json names one)")
    if not os.path.isfile(python) or not os.access(python, os.X_OK):
        raise TexturedError("the selected bpy python is not an executable file")
    env = {key: value for key, value in os.environ.items() if key != "TYPESAFE_API_KEY"}
    completed = subprocess.run([python, "-I", "-B", script, IN_BPY_FLAG, *argv], env=env)
    return completed.returncode


def strip_flag(argv: list[str]) -> tuple[list[str], bool]:
    return [item for item in argv if item != IN_BPY_FLAG], IN_BPY_FLAG in argv


def parse_vec3(text: str) -> tuple[float, float, float]:
    parts = text.split(",")
    if len(parts) != 3:
        raise TexturedError(f"expected x,y,z but got {text!r}")
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        raise TexturedError(f"expected numbers in {text!r}") from None


_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def safe_name(value: str, what: str) -> str:
    if not _SAFE_NAME.fullmatch(value):
        raise TexturedError(f"{what} must match [A-Za-z0-9][A-Za-z0-9_.-]{{0,63}}: {value!r}")
    return value


def run_cli(main) -> None:
    try:
        code = main(sys.argv[1:])
    except TexturedError as error:
        print(f"textured error: {error}", file=sys.stderr)
        code = error.code
    raise SystemExit(code)
