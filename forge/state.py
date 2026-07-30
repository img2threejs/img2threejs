from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forge._state.cli import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
