"""Credential-safe serialization helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SECRET_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "hf_token",
    "hf-token",
    "token",
    "access_token",
}
_SIGNED_QUERY_KEYS = {
    "token",
    "access_token",
    "signature",
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
}
_TOKEN_PATTERN = re.compile(r"(?i)(?:Bearer\s+)?hf_[A-Za-z0-9_-]{12,}")
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[^\s,;]+")


def _redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if parsed.scheme not in {"http", "https"}:
        return value
    safe_query = [
        (key, "[REDACTED]" if key.lower() in _SIGNED_QUERY_KEYS else item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _SIGNED_QUERY_KEYS
    ]
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(safe_query), "")
    )


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).lower() in _SECRET_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _TOKEN_PATTERN.sub(
            "[REDACTED]", _BEARER_PATTERN.sub("[REDACTED]", _redact_url(value))
        )
    return value


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(redact(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
