"""Shared CLI exit handling for every forge entry point.

Why this exists: `probe_image.py ref.png | head -5` used to blow up — head reads
five lines, closes the pipe, and Python's write/shutdown flush then raised with
a full traceback plus an "Exception ignored" line. A human reads that as the
false error it is; an agent reads a non-zero exit and treats the command as
failed, so piped diagnostics had to be hand-verified every time.

Every command-line entry point routes its main() through run_entry(), which
keeps real bugs loud and makes the environment-driven exits honest:
  * pipe closed by the consumer — exit 141 (128+SIGPIPE), silent. On POSIX this
    surfaces as BrokenPipeError; on Windows (notably under MSYS/Git-Bash pipes)
    the same condition surfaces as OSError EINVAL/EPIPE/EINVAL, so all three are
    recognised (the same mapping pip adopted for its CLI).
  * KeyboardInterrupt — Ctrl-C: exit 130 (128+SIGINT), silent
  * FileNotFoundError — one human-readable line on stderr, exit 66 (EX_NOINPUT)
  * anything else — re-raised unchanged; a genuine traceback is the feature

The flush happens INSIDE the guarded region (and again after main returns) so
the consumer-closed-the-pipe case is caught whether it surfaces mid-main or at
the final flush, instead of as the interpreter's "Exception ignored" noise.
Pure stdlib, matching the rest of forge/.
"""
from __future__ import annotations

import errno
import os
import sys
from collections.abc import Callable

EXIT_BROKEN_PIPE = 141  # 128 + SIGPIPE(13), the convention `head` users expect
EXIT_INTERRUPTED = 130  # 128 + SIGINT(2)
EXIT_NO_INPUT = 66  # EX_NOINPUT, BSD sysexits

# Errnos that mean "the reader is gone" (POSIX EPIPE plus the Windows shapes of
# the same condition, including the EINVAL MSYS pipes raise).
_BROKEN_PIPE_ERRNOS = frozenset({errno.EPIPE, errno.EINVAL, errno.ESHUTDOWN})


def _is_broken_pipe(error: BaseException) -> bool:
    if isinstance(error, BrokenPipeError):
        return True
    return isinstance(error, OSError) and error.errno in _BROKEN_PIPE_ERRNOS


def _silence_stdout() -> None:
    """Point stdout at devnull so the final flush cannot re-raise on shutdown."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError):
        pass


def _hard_exit(code: int) -> None:
    try:
        sys.stderr.flush()
    except Exception:  # noqa: BLE001 — stderr may itself be the closed pipe
        pass
    os._exit(code)  # bypass the interpreter-shutdown flush entirely


def run_entry(main_fn: Callable[..., int], argv: list[str] | None = None) -> int:
    """Run a main(argv) -> int entry point with pipeline-friendly exit handling."""
    try:
        result = main_fn(sys.argv[1:] if argv is None else argv)
        try:
            sys.stdout.flush()
        except OSError as flush_error:
            if not _is_broken_pipe(flush_error):
                raise
            _silence_stdout()
            return EXIT_BROKEN_PIPE
        return result
    except KeyboardInterrupt:
        _silence_stdout()
        _hard_exit(EXIT_INTERRUPTED)
    except FileNotFoundError as error:  # before OSError: FileNotFoundError is one
        missing = error.filename or str(error)
        print(f"error: file not found: {missing}", file=sys.stderr)
        return EXIT_NO_INPUT
    except OSError as error:
        if not _is_broken_pipe(error):
            raise
        _silence_stdout()
        _hard_exit(EXIT_BROKEN_PIPE)
