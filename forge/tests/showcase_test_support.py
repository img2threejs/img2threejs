from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path

# On Windows npx is npx.cmd and CreateProcess does not apply PATHEXT, so a bare
# "npx" in a subprocess argv list fails with FileNotFoundError. Resolve the real
# executable once; tests that shell out to tsc must use this instead of "npx".
NPX = shutil.which("npx") or "npx"


def showcase_root() -> Path:
    configured_root = os.environ.get("IMG2THREEJS_SHOWCASE_ROOT")
    root = Path(configured_root).expanduser().resolve() if configured_root else None
    if root is not None and (root / "package.json").is_file():
        return root
    message = (
        "showcase TypeScript checks require IMG2THREEJS_SHOWCASE_ROOT pointing to an "
        f"img2threejs-showcase checkout; received {root if root is not None else 'no configuration'}"
    )
    if os.environ.get("IMG2THREEJS_REQUIRE_SHOWCASE") == "1":
        raise RuntimeError(message)
    raise unittest.SkipTest(message)
