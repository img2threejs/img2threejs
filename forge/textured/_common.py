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
from urllib.parse import unquote

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


def contained_asset(workspace: Path, generation: int, relative: Any) -> Path:
    """The real path of a receipt-named asset, which must live inside this generation's own directory.

    ``generate.json`` is data on disk, not a trusted argument: a crafted ``asset.path`` (absolute,
    ``..`` or a symlink) must not make a tool read a model from somewhere else. Both the generation
    directory and the asset are resolved with realpath; the directory must be the expected
    ``<workspace>/.img2threejs/textured/gen-N`` itself (so a symlinked gen-N cannot relocate the root)
    and the asset must resolve beneath it.
    """
    if not isinstance(relative, str) or not relative:
        raise TexturedError("generate.json asset.path must be a non-empty string")
    expected = Path(os.path.realpath(workspace)) / ".img2threejs" / "textured" / f"gen-{generation}"
    directory = gen_dir(workspace, generation)
    if Path(os.path.realpath(directory)) != expected:
        raise TexturedError(f"{directory} resolves outside the workspace's textured directory")
    resolved = Path(os.path.realpath(directory / relative))
    if resolved == expected or expected not in resolved.parents:
        raise TexturedError(f"asset path {relative!r} resolves outside {expected}; refusing to read it")
    return resolved


def _numbered(directory: Path, pattern: str) -> list[tuple[int, Path]]:
    """Entries of ``directory`` matching ``pattern`` (one numeric group), sorted by that number; gaps are fine."""
    found = []
    if directory.is_dir():
        for entry in directory.iterdir():
            match = re.fullmatch(pattern, entry.name)
            if match:
                found.append((int(match.group(1)), entry))
    return sorted(found)


def superseded_receipts(directory: Path) -> list[Path]:
    """Every ``superseded-K.json`` in a generation directory, by K, whatever numbers are missing."""
    return [path for _, path in _numbered(Path(directory), r"superseded-(\d+)\.json") if path.is_file()]


def generation_receipts(workspace: Path) -> list[dict[str, Any]]:
    """Every generate receipt ever written: for each ``gen-N`` (by N, gaps tolerated) every ``superseded-K.json``
    a replaced receipt was archived to, then its current ``generate.json``. Spending history is append-only,
    so totals sum all of it; a missing file in the middle never hides the receipts after it."""
    rows: list[dict[str, Any]] = []
    for _, directory in _numbered(textured_root(workspace), r"gen-(\d+)"):
        for archived in superseded_receipts(directory):
            rows.append(read_json(archived))
        if (directory / "generate.json").is_file():
            rows.append(read_json(directory / "generate.json"))
    return rows


# ---------------------------------------------------------------- model dependencies

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_MTL_PATH_KEYS = re.compile(r"^(map_\w+|bump|disp|decal|refl|norm)$", re.IGNORECASE)


def _gltf_document(path: Path) -> Any:
    """The glTF JSON of a .gltf/.glb. Sniffed by content like the importer does (a ``glTF`` magic is binary,
    anything else is JSON text whatever the suffix), so a renamed file cannot dodge the check."""
    data = path.read_bytes()
    if data[:4] == b"glTF":
        length = int.from_bytes(data[12:16], "little")
        if len(data) < 20 or data[16:20] != b"JSON" or 20 + length > len(data):
            raise TexturedError(f"{path.name} is not a valid GLB (no JSON chunk); refusing to import it")
        text = data[20:20 + length]
    else:
        text = data
    try:
        return json.loads(text.decode("utf-8"))
    except ValueError:
        raise TexturedError(f"{path.name} is not valid glTF JSON; refusing to import it") from None


def _references(path: Path) -> list[str]:
    """The external files a model names (data: URIs are embedded and excluded). Binary FBX is not parsed."""
    suffix = path.suffix.lower()
    found: list[str] = []
    if suffix in (".gltf", ".glb"):
        document = _gltf_document(path)
        if not isinstance(document, dict):
            raise TexturedError(f"{path.name} is not a glTF object; refusing to import it")
        for key in ("buffers", "images"):
            rows = document.get(key, [])
            if not isinstance(rows, list):
                raise TexturedError(f"{path.name}: glTF {key} must be a list; refusing to import it")
            for row in rows:
                uri = row.get("uri") if isinstance(row, dict) else None
                if uri is None:
                    continue
                if not isinstance(uri, str):
                    raise TexturedError(f"{path.name}: glTF {key} uri must be a string; refusing to import it")
                if not uri.lower().startswith("data:"):
                    found.append(uri)
    elif suffix in (".obj", ".mtl"):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            key, rest = parts[0], parts[1].strip()
            if suffix == ".obj" and key == "mtllib":
                found.append(rest)
                found.extend(rest.split())
            elif suffix == ".mtl" and _MTL_PATH_KEYS.match(key):
                found.append(rest)
                for token in rest.split():
                    if token.startswith("-"):
                        continue
                    try:
                        float(token)
                    except ValueError:
                        found.append(token)
    return found


def require_contained_model(workspace: Path, generation: int | None, model: Path) -> None:
    """The one pre-import check inspect and normalize share: a generation's model may only depend on files
    inside its own ``gen-N`` directory; an explicit ``--model`` outside a generation, on files under its own directory."""
    root = gen_dir(workspace, generation) if generation is not None else Path(model).parent
    require_contained_references(Path(model), root)


def require_contained_references(model: Path, root: Path, _seen: set[Path] | None = None) -> None:
    """Refuse a model whose external dependencies (glTF buffers/images, OBJ ``mtllib`` and the MTL maps) are not
    plain relative paths that resolve, with symlinks followed, inside ``root`` (relative to the model's own
    directory, which is how the importers resolve them).

    ``raw/model.gltf`` is data from a generator, not a trusted argument: an absolute buffer URI, ``..``, a
    ``file:`` or ``http(s):`` URL, or a symlink out of ``root`` would make the importer read or fetch something
    that is not part of the generation. Embedded (``data:``) and GLB-internal buffers are fine. Run it before
    every import of a model that can name other files; binary FBX is not parsed.
    """
    model = Path(model)
    real_root = Path(os.path.realpath(root))
    seen = _seen if _seen is not None else set()
    real_model = Path(os.path.realpath(model))
    if real_model in seen:
        return
    seen.add(real_model)
    for uri in _references(model):
        decoded = unquote(uri)
        for candidate in {uri, decoded}:
            if (not candidate or candidate.startswith(("/", "\\")) or _SCHEME.match(candidate)
                    or "\x00" in candidate or ".." in re.split(r"[\\/]", candidate)):
                raise TexturedError(f"{model.name} references {uri!r}, which is not a relative path inside "
                                    f"{real_root}; refusing to import it")
        target = Path(os.path.realpath(model.parent / decoded.replace("\\", "/")))
        if target == real_root or real_root not in target.parents:
            raise TexturedError(f"{model.name} references {uri!r}, which resolves outside {real_root}; "
                                "refusing to import it")
        if model.suffix.lower() == ".obj" and target.suffix.lower() == ".mtl" and target.is_file():
            require_contained_references(target, root, seen)


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
