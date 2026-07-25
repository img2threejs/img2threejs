from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO


def _pipeline(spec: dict[str, Any] | None) -> tuple[str, int, str, str]:
    if not isinstance(spec, dict):
        return "unknown", 0, "spec not loaded", "forge/next.py <spec>"
    pipeline = spec.get("sculptPipeline")
    if not isinstance(pipeline, dict):
        return "unknown", 0, "sculptPipeline is missing", "forge/next.py <spec>"
    current = str(pipeline.get("currentPass") or "unknown")
    completed = pipeline.get("completedPasses", [])
    count = len(completed) if isinstance(completed, list) else 0
    reason = str(pipeline.get("blockedReason") or "none")
    next_command = "none (pipeline complete)" if current == "complete" else "forge/next.py <spec>"
    return current, count, reason, next_command


def emit_status(spec: dict[str, Any] | None = None, *, next_command: str | None = None, stream: TextIO = sys.stdout) -> None:
    current, count, reason, default_next = _pipeline(spec)
    print(f"STATUS pass={current} completed={count} blocked={reason} next={next_command or default_next}", file=stream)


def load_optional_spec(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
