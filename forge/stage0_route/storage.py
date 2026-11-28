"""Canonical, workspace-confined stage-zero evidence (not provider authority)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile


class RouteError(ValueError):
    def __init__(self, reason_code):
        self.reason_code = reason_code
        super().__init__(reason_code)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def safe_path(workspace, relative, *, existing=False):
    if (type(relative) is not str or not relative or "\\" in relative
            or PurePosixPath(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in relative.split("/"))):
        raise RouteError("unsafe-path")
    path = workspace / relative
    for part in (path, *path.parents):
        if part == workspace:
            break
        if part.is_symlink():
            raise RouteError("unsafe-path")
    if existing and (not path.is_file() or not stat.S_ISREG(path.stat().st_mode)):
        raise RouteError("evidence-missing")
    return path


def read_bytes(workspace, relative):
    path = safe_path(workspace, relative, existing=True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise RouteError("unsafe-path")
        return source.read()


def atomic_write(workspace, relative, data, *, immutable=False):
    path = safe_path(workspace, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_path(workspace, relative)
    if immutable and path.exists():
        if read_bytes(workspace, relative) != data:
            raise RouteError("immutable-evidence-mismatch")
        return relative
    fd, temporary = tempfile.mkstemp(prefix=".stage0-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        safe_path(workspace, relative)
        if immutable:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if read_bytes(workspace, relative) != data:
                    raise RouteError("immutable-evidence-mismatch")
        else:
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return relative


def snapshot(workspace, category, value):
    data = encode(value)
    sha = hashlib.sha256(data).hexdigest()
    relative = f".img2threejs/route-inputs/{category}/{sha}.json"
    atomic_write(workspace, relative, data, immutable=True)
    return {"role": category, "path": relative, "sha256": sha}


def binding(workspace, role, relative):
    return {"role": role, "path": relative,
            "sha256": hashlib.sha256(read_bytes(workspace, relative)).hexdigest()}
