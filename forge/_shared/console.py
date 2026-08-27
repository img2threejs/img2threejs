"""Make a script's stdout/stderr able to carry the text this repo actually prints.

WHY THIS EXISTS. The vocabulary packs under `docs/specs/vocabulary/` are bilingual --
`core_3d.jsonl` and `cs2.jsonl` carry Vietnamese and Chinese recognition cues alongside the
English ones -- and the scripts that surface them print with `ensure_ascii=False`, which is the
right call: escaping every non-ASCII character to `\\uXXXX` would make the snippets unreadable
for the reader they were written for.

That combination is safe only when the sink can encode the text. `print()` encodes through
`sys.stdout.encoding`, which Python takes from the environment, and on a default Windows console
that is cp1252 -- a codec with no mapping for `ạ` (U+1EA1) or `ấ` (U+1EA5). The result is not a
garbled line, it is `UnicodeEncodeError` raised mid-write, a non-zero exit, and a partially
written stream. `search_specs.py` is the executable half of the mandatory local-spec-search step,
so that crash stops the pipeline at stage 1 rather than degrading to a worse-but-working search.

The same fault is reachable anywhere, not just on Windows: `PYTHONIOENCODING=cp1252` reproduces it
on Linux and in CI, which is how the regression test for it is written.

WHY RECONFIGURE RATHER THAN ESCAPE. Switching these scripts to `ensure_ascii=True` would fix the
crash by deleting the information -- the bilingual cue becomes unreadable escapes for every
reader, everywhere, to satisfy one legacy console. Forcing the stream to UTF-8 instead keeps the
output correct and makes it identical on every platform, which is also what the deterministic
fingerprints and cached artifacts elsewhere in `forge/` already assume.

`errors="backslashreplace"` is a second belt: if a stream cannot be reconfigured at all (a wrapped
or already-detached stdout), any character it still cannot represent degrades to a visible escape
instead of killing the process. A gate should fail on its own findings, never on its own output.
"""

from __future__ import annotations

import sys

__all__ = ["enable_utf8_output"]


def enable_utf8_output() -> None:
    """Force UTF-8 on stdout/stderr for the current process.

    Idempotent, never raises, and a no-op on streams that do not support reconfiguration
    (`io.StringIO` under test capture, a detached stream, a non-text sink).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):
            # A detached or already-closed stream cannot be reconfigured. Printing is then
            # someone else's problem; refusing to start over it would be worse.
            continue
