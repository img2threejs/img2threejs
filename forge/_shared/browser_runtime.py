"""Measure an explicitly host-approved browser bundle, not its ambient OS runtime.

Snapshots identify bytes and literal symlink targets; they do not approve them.
System libraries, the OS, interpreter and other host dependencies remain trusted
ambient code. Directory symlinks are inventoried, never recursively traversed.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat

from route_parent import ParentError


def _require(condition, reason):
    if not condition:
        raise ParentError(reason)


def snapshot_browser_bundle(browser_root: str) -> tuple[tuple[str, str], ...]:
    """Return the sorted complete regular-file/symlink inventory for host review.

    Regular files use SHA-256 of their bytes. Symlinks use SHA-256 of
    ``b"symlink\\0" + os.fsencode(literal_target)``. Neither a returned snapshot
    nor hashes computed from candidate input confer host approval.
    """
    _require(type(browser_root) is str, "cs2-invalid-browser-root")
    root = Path(browser_root)
    try:
        _require(root.is_absolute() and root.resolve(strict=True) == root and root.is_dir(),
                 "cs2-invalid-browser-root")
        inventory, edges = [], {}

        def visit(directory):
            edges.setdefault(directory, [])
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = directory / entry.name
                    relative = path.relative_to(root).as_posix()
                    mode = entry.stat(follow_symlinks=False).st_mode
                    if stat.S_ISLNK(mode):
                        target = os.readlink(path)
                        resolved = path.resolve(strict=True)
                        _require(resolved.is_relative_to(root), "cs2-browser-symlink-escape")
                        target_mode = resolved.stat().st_mode
                        _require(stat.S_ISREG(target_mode) or stat.S_ISDIR(target_mode),
                                 "cs2-unsupported-browser-entry")
                        if stat.S_ISDIR(target_mode):
                            edges[directory].append(resolved)
                        digest = hashlib.sha256(b"symlink\0" + os.fsencode(target)).hexdigest()
                        inventory.append((relative, digest))
                    elif stat.S_ISDIR(mode):
                        edges[directory].append(path)
                        visit(path)
                    elif stat.S_ISREG(mode):
                        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                        with os.fdopen(descriptor, "rb") as stream:
                            _require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode),
                                     "cs2-unsupported-browser-entry")
                            digest = hashlib.file_digest(stream, "sha256").hexdigest()
                        inventory.append((relative, digest))
                    else:
                        raise ParentError("cs2-unsupported-browser-entry")

        visit(root)
        active, complete = set(), set()

        def check_cycles(directory):
            _require(directory not in active, "cs2-browser-symlink-cycle")
            if directory in complete:
                return
            active.add(directory)
            for target in edges[directory]:
                check_cycles(target)
            active.remove(directory)
            complete.add(directory)

        check_cycles(root)
        return tuple(sorted(inventory))
    except ParentError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise ParentError("cs2-invalid-browser-bundle") from exc


def verify_browser_bundle(browser_root: str, browser_files: tuple[tuple[str, str], ...],
                          executable: str) -> None:
    """Revoke admission unless the host's immutable inventory still matches."""
    _require(type(browser_files) is tuple and browser_files, "cs2-browser-approval-required")
    for row in browser_files:
        _require(type(row) is tuple and len(row) == 2 and type(row[0]) is str
                 and row[0] and not row[0].startswith("/") and "\\" not in row[0]
                 and ":" not in row[0] and all(part not in ("", ".", "..") for part in row[0].split("/"))
                 and type(row[1]) is str and re.fullmatch(r"[0-9a-f]{64}", row[1]),
                 "cs2-invalid-browser-inventory")
    _require(browser_files == tuple(sorted(browser_files))
             and len({row[0] for row in browser_files}) == len(browser_files),
             "cs2-invalid-browser-inventory")
    _require(type(executable) is str and Path(executable).is_absolute()
             and Path(executable).is_relative_to(Path(browser_root))
             and Path(executable).relative_to(Path(browser_root)).as_posix() in dict(browser_files),
             "cs2-browser-executable-outside-bundle")
    _require(snapshot_browser_bundle(browser_root) == browser_files, "cs2-browser-bundle-changed")
