#!/usr/bin/env python3
"""Regenerate or verify ``forge/_shared/jev_release_lock.json`` from a Jev commit.

The Jev commit is the ``jev`` pin in the harness ``plugins.lock.json``. File bytes are read from
git objects at that exact commit in a *local* checkout: no network, no checkout/index/worktree
read or mutation (a dirty worktree is irrelevant), no replace objects, no lazy fetch, no
repository/user/system config beyond what git needs to open the object store, and no hooks.

The script never adopts a changed required-file inventory or contract version: the existing lock
must already name exactly ``REQUIRED_PROVIDER_FILES`` under the approved contract, otherwise it
refuses. There is deliberately no flag to accept such a change; that is a reviewed code edit.

    regen_jev_release_lock.py --plugins-lock PATH --checkout PATH [--output PATH] [--check]

Exit status: 0 success/match, 1 refusal or mismatch, 2 usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "forge" / "_shared"))
from jev_cli_bridge import REQUIRED_PROVIDER_FILES  # noqa: E402

DEFAULT_OUTPUT = BASE / "forge" / "_shared" / "jev_release_lock.json"
CONTRACT_VERSION = "jev-v4-four-decisions"
_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_JSON = 1024 * 1024
_MAX_BLOB = 64 * 1024 * 1024  # same ceiling the bridge enforces when it re-hashes the files
_REGULAR_MODES = frozenset({"100644", "100755"})
_GIT_TIMEOUT = 60


class LockError(Exception):
    """A refusal that is reported as one line, never a traceback."""


def _json(raw: bytes, label: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise LockError(f"{label}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    def constant(name):
        raise LockError(f"{label}: non-finite JSON number {name}")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise LockError(f"{label}: not valid UTF-8 JSON") from None


def _read_json(path: Path, label: str, *, refuse_symlink: bool = False):
    try:
        if (refuse_symlink and path.is_symlink()) or not path.is_file():
            raise LockError(f"{label}: not a regular file: {path}")
        with open(path, "rb") as stream:
            raw = stream.read(_MAX_JSON + 1)
    except OSError as error:
        raise LockError(f"{label}: cannot read {path}: {error.strerror or error}") from None
    if len(raw) > _MAX_JSON:
        raise LockError(f"{label}: larger than {_MAX_JSON} bytes")
    return _json(raw, label)


def jev_pin(plugins_lock) -> str:
    """Validate every plugin pin and return the single ``jev`` commit."""
    if type(plugins_lock) is not dict or type(plugins_lock.get("schema")) is not int \
            or plugins_lock["schema"] != 1:
        raise LockError("plugins lock: schema must be 1")
    plugins = plugins_lock.get("plugins")
    if type(plugins) is not list or not plugins:
        raise LockError("plugins lock: 'plugins' must be a non-empty list")
    seen, jev = set(), []
    for index, plugin in enumerate(plugins):
        if type(plugin) is not dict:
            raise LockError(f"plugins lock: plugins[{index}] is not an object")
        name, commit = plugin.get("id"), plugin.get("commit")
        if type(name) is not str or not name:
            raise LockError(f"plugins lock: plugins[{index}] has no id")
        if name in seen:
            raise LockError(f"plugins lock: duplicate plugin id {name!r}")
        seen.add(name)
        if type(commit) is not str or not _COMMIT.fullmatch(commit):
            raise LockError(f"plugins lock: plugin {name!r} is unpinned or its commit is not a "
                            "full lowercase hex git object id")
        if name == "jev":
            jev.append(commit)
    if len(jev) != 1:
        raise LockError("plugins lock: exactly one 'jev' plugin pin is required")
    return jev[0]


def validate_lock(value, label: str) -> dict:
    """The existing lock must already be the approved shape over the current inventory."""
    if type(value) is not dict or set(value) != {"resolvedSha", "files", "contractVersion"}:
        raise LockError(f"{label}: must have exactly resolvedSha, files, contractVersion")
    if value["contractVersion"] != CONTRACT_VERSION:
        raise LockError(f"{label}: contractVersion is not {CONTRACT_VERSION!r}; a contract "
                        "change is a reviewed code edit, not a regeneration")
    if type(value["resolvedSha"]) is not str or not _COMMIT.fullmatch(value["resolvedSha"]):
        raise LockError(f"{label}: resolvedSha is not a full lowercase git object id")
    files = value["files"]
    if type(files) is not dict:
        raise LockError(f"{label}: files must be an object")
    required = frozenset(REQUIRED_PROVIDER_FILES)
    if set(files) != required:
        missing, extra = sorted(required - set(files)), sorted(set(files) - required)
        raise LockError(f"{label}: files differ from REQUIRED_PROVIDER_FILES "
                        f"(missing={missing}, unexpected={extra}); refusing to change inventory")
    for relative, digest in files.items():
        if type(digest) is not str or not _SHA256.fullmatch(digest):
            raise LockError(f"{label}: files[{relative!r}] is not a lowercase sha256")
    return value


def _git_env() -> dict:
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT") if name in os.environ}
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
        "HOME": os.devnull, "XDG_CONFIG_HOME": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_ALLOW_PROTOCOL": "", "LC_ALL": "C",
    })
    return env


def _git(checkout: Path, *args: str) -> bytes:
    argv = ["git", "--no-replace-objects", "--no-optional-locks",
            "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false",
            "-c", "protocol.allow=never", "-C", str(checkout), *args]
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, shell=False,
                              env=_git_env(), timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as error:
        raise LockError(f"git {args[0]} could not run: {type(error).__name__}") from None
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise LockError(f"git {args[0]} failed: {detail[0][:200] if detail else done.returncode}")
    return done.stdout


def _tree_entries(checkout: Path, commit: str, paths) -> dict:
    out = _git(checkout, "ls-tree", "-z", "--full-tree", commit, "--", *sorted(paths))
    entries = {}
    for record in out.split(b"\0"):
        if not record:
            continue
        try:
            meta, raw_path = record.split(b"\t", 1)
            mode, kind, oid = meta.decode("ascii").split(" ")
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise LockError("git ls-tree: unparseable output") from None
        if path not in paths or path in entries or not _COMMIT.fullmatch(oid):
            raise LockError("git ls-tree: unexpected entry")
        if kind != "blob" or mode not in _REGULAR_MODES:
            raise LockError(f"{path}: must be a regular file blob at the pinned commit "
                            f"(found mode {mode} type {kind}; symlinks and submodules are refused)")
        entries[path] = oid
    missing = sorted(set(paths) - set(entries))
    if missing:
        raise LockError(f"pinned commit lacks required files: {missing}")
    return entries


def compute_lock(checkout: Path, commit: str) -> dict:
    """Hash the required files as committed at ``commit``; never touches the worktree."""
    if not checkout.is_dir():
        raise LockError(f"checkout is not a directory: {checkout}")
    if _git(checkout, "rev-parse", "--show-prefix").strip():
        raise LockError("checkout must be the root of a git repository")
    resolved = _git(checkout, "rev-parse", "--verify", commit + "^{commit}").decode("ascii", "replace").strip()
    if resolved != commit:
        raise LockError("pinned commit does not resolve to itself as a commit object")
    files = {}
    for path, oid in sorted(_tree_entries(checkout, commit, frozenset(REQUIRED_PROVIDER_FILES)).items()):
        data = _git(checkout, "cat-file", "blob", oid)
        if len(data) > _MAX_BLOB:
            raise LockError(f"{path}: larger than {_MAX_BLOB} bytes")
        files[path] = hashlib.sha256(data).hexdigest()
    return {"resolvedSha": commit, "contractVersion": CONTRACT_VERSION, "files": files}


def _serialise(lock: dict) -> bytes:
    return (json.dumps(lock, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write(path: Path, data: bytes) -> None:
    temporary = None
    try:
        handle, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        temporary = None
    except OSError as error:
        raise LockError(f"cannot write {path}: {error.strerror or error}") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def run(plugins_lock: Path, checkout: Path, output: Path, check: bool) -> str:
    commit = jev_pin(_read_json(plugins_lock, "plugins lock"))
    current = validate_lock(_read_json(output, "output lock", refuse_symlink=True), "output lock")
    if check and current["resolvedSha"] != commit:
        raise LockError(f"pin mismatch: lock resolvedSha {current['resolvedSha']} != "
                        f"plugins lock jev commit {commit}")
    computed = compute_lock(checkout, commit)
    if check:
        if computed != current:
            stale = sorted(path for path in computed["files"]
                           if computed["files"][path] != current["files"].get(path))
            raise LockError(f"lock does not match commit {commit}: stale files {stale}")
        return f"ok: {output} matches jev commit {commit}"
    data = _serialise(computed)
    if output.read_bytes() == data:
        return f"unchanged: {output} (jev commit {commit})"
    _write(output, data)
    return f"wrote: {output} (jev commit {commit})"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--plugins-lock", required=True, type=Path,
                        help="harness plugins.lock.json holding the jev pin")
    parser.add_argument("--checkout", required=True, type=Path,
                        help="local checkout of the Jev plugin repository (read-only)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="lock to update/compare (default: %(default)s)")
    parser.add_argument("--check", action="store_true",
                        help="compare only; exit nonzero on any mismatch, never write")
    args = parser.parse_args(argv)
    try:
        print(run(args.plugins_lock, args.checkout, args.output, args.check))
    except LockError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
