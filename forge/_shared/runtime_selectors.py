"""Strict reader for the provisioned `$IMG2_HOME/runtime/selectors.json` (never a trust root).

The harness writes this file; the core only reads it to fill a selector that is not already set
in the process environment. Any invalidity discards the whole file, so every selector is unset and
the router keeps its existing dependency-missing behavior. Nothing here executes or hashes a
selected runtime; `character_three_facts` stays the authority for the Three package bytes.
Results are never cached: each call re-reads and re-validates the files.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat

SCHEMA = 1
PLUGIN_IDS = ("character", "cs2", "interior", "jev", "pixi2d")
SHA256_KEY = "IMG2_CHARACTER_THREE_SHA256"
PATH_KEYS = ("IMG2_CHARACTER_NODE", "IMG2_CHARACTER_BROWSER", "IMG2_CHARACTER_BPY_PYTHON",
             "IMG2_CHARACTER_THREE_ROOT", "IMG2_BLENDER_PYTHON", "IMG2THREEJS_VISION_PYTHON",
             "PLAYWRIGHT_BROWSERS_PATH")
ENV_KEYS = PATH_KEYS + (SHA256_KEY,)
_FIELDS = {"schema", "lockDigest", "pluginsLockDigest", "selectedPlugins", "env", "createdAt"}
_SELECTORS_LIMIT = 1024 * 1024
_LOCK_LIMIT = 16 * 1024 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}")
_CREATED = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.[0-9]{1,9})?(?:Z|\+00:00)")


class _Invalid(ValueError):
    pass


def img2_home():
    return Path(os.environ.get("IMG2_HOME") or Path.home() / ".img2")


def installed_harness_root():
    return Path(os.environ.get("IMG2_HARNESS_DIR") or img2_home() / "harness")


def _current_uid():
    return os.geteuid()


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise _Invalid("duplicate JSON key")
        result[key] = value
    return result


def _nonfinite(value):
    raise _Invalid("non-finite JSON")


def _read_regular(directory_fd, name, *, limit, private):
    """Read one regular file by nofollow descriptor; never blocks on a FIFO."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
                 dir_fd=directory_fd)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise _Invalid("not a bounded regular file")
        if private and (info.st_uid != _current_uid() or stat.S_IMODE(info.st_mode) != 0o600):
            raise _Invalid("not a private file owned by the current user")
        data = source.read(limit + 1)
    if len(data) > limit:
        raise _Invalid("file grew past its bound")
    return data


def _lock_digest(harness_fd, name):
    return hashlib.sha256(_read_regular(harness_fd, name, limit=_LOCK_LIMIT, private=False)).hexdigest()


def _digest(value):
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise _Invalid("digest must be 64 lowercase hex")
    return value


def _created_at(value):
    match = _CREATED.fullmatch(value) if type(value) is str else None
    if match is None:
        raise _Invalid("createdAt must be ISO-8601 UTC")
    datetime.strptime(match[1], "%Y-%m-%dT%H:%M:%S")


def _runtime_path(value, lexical_roots, runtime):
    if type(value) is not str or not value or "\0" in value or not os.path.isabs(value):
        raise _Invalid("selector path must be absolute")
    if ".." in value.split("/"):
        raise _Invalid("selector path must not traverse")
    lexical = Path(value)
    if not any(lexical.is_relative_to(root) for root in lexical_roots):
        raise _Invalid("selector path outside the runtime tree")
    resolved = lexical.resolve(strict=True)
    if resolved == runtime or not resolved.is_relative_to(runtime):
        raise _Invalid("selector path escapes the runtime tree")


def _validate(data, lexical_roots, runtime):
    if type(data) is not dict or set(data) != _FIELDS:
        raise _Invalid("selectors must have exactly the six contract fields")
    if type(data["schema"]) is not int or data["schema"] != SCHEMA:
        raise _Invalid("unsupported schema")
    _digest(data["lockDigest"])
    _digest(data["pluginsLockDigest"])
    plugins = data["selectedPlugins"]
    if (type(plugins) is not list or any(type(item) is not str for item in plugins)
            or len(set(plugins)) != len(plugins) or not set(plugins) <= set(PLUGIN_IDS)):
        raise _Invalid("selectedPlugins must be a unique subset of the approved plugins")
    env = data["env"]
    if type(env) is not dict or not set(env) <= set(ENV_KEYS):
        raise _Invalid("env may only hold the contract keys")
    for key, value in env.items():
        if key == SHA256_KEY:
            _digest(value)
        else:
            _runtime_path(value, lexical_roots, runtime)
    _created_at(data["createdAt"])


def _read():
    home = img2_home()
    if not home.is_absolute():
        raise _Invalid("IMG2_HOME must be absolute")
    real_home = home.resolve(strict=True)
    runtime = real_home / "runtime"
    home_fd = os.open(real_home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        # NOFOLLOW: the runtime directory itself may not be a symlink out of the home.
        runtime_fd = os.open("runtime", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home_fd)
    finally:
        os.close(home_fd)
    try:
        raw = _read_regular(runtime_fd, "selectors.json", limit=_SELECTORS_LIMIT, private=True)
    finally:
        os.close(runtime_fd)
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite)
    _validate(data, (home / "runtime", runtime), runtime)
    harness = installed_harness_root()
    if not harness.is_absolute():
        raise _Invalid("harness root must be absolute")
    harness_fd = os.open(harness, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if (data["lockDigest"] != _lock_digest(harness_fd, "dependencies.lock.json")
                or data["pluginsLockDigest"] != _lock_digest(harness_fd, "plugins.lock.json")):
            raise _Invalid("selection is stale against the installed harness locks")
    finally:
        os.close(harness_fd)
    return data


def read_selectors():
    """Return the validated selectors dict, or None when the file is absent or invalid in any way."""
    try:
        return _read()
    except (OSError, ValueError, RuntimeError):
        return None
