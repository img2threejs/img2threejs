"""Bounded host-Git reads, independent of the executable harness trust boundary."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import tarfile
import time

from decision_control import DecisionError
from jev_cli_bridge import _file, _relative


def _fail(reason):
    raise DecisionError(reason) from None


def run_bytes(argv, workspace, *, timeout=15, maximum=64 * 1024, env=None,
              allow_missing_executable=False):
    """Read-only bounded runner; raw child output never reaches terminal or logs."""
    process = None
    try:
        try:
            process = subprocess.Popen(argv, cwd=workspace, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                       shell=False, start_new_session=True)
        except FileNotFoundError as error:
            if allow_missing_executable and error.filename == argv[0]:
                return 127, b""
            raise
        deadline, output, size = time.monotonic() + timeout, bytearray(), 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _fail("timeout")
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    size += len(chunk)
                    if size > maximum:
                        _fail("malformed")
                    if key.fileobj is process.stdout:
                        output.extend(chunk)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail("timeout")
            process.wait(timeout=remaining)
        return process.returncode, bytes(output)
    except subprocess.TimeoutExpired:
        _fail("timeout")
    except OSError:
        _fail("provider-error")
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            process.stdout.close()
            process.stderr.close()


def pin_git():
    git = shutil.which("git")
    if git is None:
        _fail("provider-contract-unproven")
    try:
        git = Path(git).resolve(strict=True)
    except OSError:
        _fail("provider-contract-unproven")
    sha = hashlib.sha256(_file(git.parent, git.name,
                              maximum=256 * 1024 * 1024, capture=True)).hexdigest()
    environment = {"PATH": os.defpath, "LANG": "C.UTF-8",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                   "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
                   "GIT_NO_LAZY_FETCH": "1"}
    return git, sha, environment


def committed_inventory(git, git_sha, environment, root, commit, files, workspace, *, runner=run_bytes):
    """Check every live hash and exact regular committed member, with one archive.

    No cached trust: both Git bytes and every live provider file are read on each
    call. The harness runner is deliberately never involved in these Git reads.
    """
    if (type(commit) is not str or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) is None
            or type(files) is not dict or not files or len(files) > 4096):
        _fail("provider-pin-mismatch")
    directories = set()
    for relative, sha in files.items():
        parts = _relative(relative)
        if type(sha) is not str or re.fullmatch(r"[0-9a-f]{64}", sha) is None:
            _fail("provider-pin-mismatch")
        directories.update("/".join(parts[:index]) for index in range(1, len(parts)))
        _file(root, relative, maximum=64 * 1024 * 1024, expected=sha)

    def verify_git():
        if hashlib.sha256(_file(git.parent, git.name, maximum=256 * 1024 * 1024,
                                capture=True)).hexdigest() != git_sha:
            _fail("provider-pin-mismatch")

    verify_git()
    try:
        code, raw = runner([str(git), "--no-pager", "--no-optional-locks", "--literal-pathspecs",
                            "-c", "core.fsmonitor=false", "-C", str(root),
                            "archive", "--format=tar", commit, "--", *sorted(files)],
                           workspace, maximum=64 * 1024 * 1024, env=environment)
    finally:
        verify_git()
    if code != 0 or type(raw) is not bytes or len(raw) > 64 * 1024 * 1024:
        _fail("provider-pin-mismatch")
    found, seen, selected, total = set(), set(), {}, 0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r|", ignore_zeros=True) as archive:
            for member in archive:
                relative = member.name.rstrip("/") if member.isdir() else member.name
                _relative(relative)
                if relative in seen or len(seen) >= 8192:
                    _fail("provider-pin-mismatch")
                seen.add(relative)
                if member.sparse is not None:
                    _fail("provider-pin-mismatch")
                if member.isdir():
                    if relative not in directories:
                        _fail("provider-pin-mismatch")
                    continue
                if (member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE)
                        or relative not in files
                        or not 0 <= member.size <= 64 * 1024 * 1024):
                    _fail("provider-pin-mismatch")
                total += member.size
                if total > 64 * 1024 * 1024:
                    _fail("provider-pin-mismatch")
                stream = archive.extractfile(member)
                if stream is None:
                    _fail("provider-pin-mismatch")
                data = stream.read(member.size + 1)
                if len(data) != member.size or hashlib.sha256(data).hexdigest() != files[relative]:
                    _fail("provider-pin-mismatch")
                found.add(relative)
                if relative in {"plugin.json", "steps.json"}:
                    selected[relative] = data
    except (tarfile.TarError, OSError, ValueError, DecisionError):
        _fail("provider-pin-mismatch")
    if found != files.keys():
        _fail("provider-pin-mismatch")
    for relative, sha in files.items():
        _file(root, relative, maximum=64 * 1024 * 1024, expected=sha)
    return selected
