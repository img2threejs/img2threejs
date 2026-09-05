"""The GLB binary-glTF container parse, shared between intake and export.

Extracted from `forge/stage1_intake/probe_glb.py` (task 3.6, `establish-the-emission-target-contract`
slice 3): the magic/version/chunk parse is intake-domain-independent, but `probe_glb.py` itself is
not a drop-in for the export side -- it also builds the richer semantic-decomposition report via
`semantic_decomposition.assess_semantic_decomposition`, which the export tier-1 verifier has no use
for and should not have to import. This module holds only the container parse: is this a well-formed
GLB, and (incidentally) what chunks does it carry. `probe_glb.py` now imports `GLB_MAGIC` and
`parse_glb` from here instead of defining its own copy, so both sides read one implementation.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

GLB_MAGIC = b"glTF"
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


def _chunk_type_name(chunk_type: int) -> str:
    try:
        return chunk_type.to_bytes(4, "little").decode("ascii", errors="replace")
    except OverflowError:
        return hex(chunk_type)


def parse_glb(path: Path) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    """Parse the GLB container: header, chunk table, and the JSON/BIN chunk payloads.

    Raises ValueError naming the specific structural defect on anything malformed -- short header,
    wrong magic, unsupported version, a length mismatch, a truncated or overrunning chunk, more than
    one JSON or BIN chunk, a missing JSON chunk, or JSON that isn't a UTF-8 object. This is the
    complete container check; it does not look inside the document beyond confirming it parses as an
    object.
    """
    data = path.read_bytes()
    if len(data) < 12:
        raise ValueError("GLB is shorter than the 12-byte header")
    magic, version, declared_length = struct.unpack_from("<4sII", data, 0)
    if magic != GLB_MAGIC:
        raise ValueError("file does not start with the glTF binary magic")
    if version != 2:
        raise ValueError(f"unsupported GLB version {version}; expected 2")
    if declared_length != len(data):
        raise ValueError(f"header length {declared_length} does not match file size {len(data)}")

    cursor = 12
    json_payload: bytes | None = None
    bin_payload = b""
    bin_seen = False
    chunks: list[dict[str, Any]] = []
    while cursor < len(data):
        if cursor + 8 > len(data):
            raise ValueError("truncated GLB chunk header")
        chunk_length, chunk_type = struct.unpack_from("<II", data, cursor)
        cursor += 8
        end = cursor + chunk_length
        if end > len(data):
            raise ValueError("GLB chunk extends beyond the declared file")
        payload = data[cursor:end]
        cursor = end
        chunks.append({"type": _chunk_type_name(chunk_type), "bytes": chunk_length})
        if chunk_type == JSON_CHUNK:
            if json_payload is not None:
                raise ValueError("GLB contains more than one JSON chunk")
            json_payload = payload.rstrip(b" \t\r\n\x00")
        elif chunk_type == BIN_CHUNK:
            # A seen-flag, not truthiness: a zero-length first BIN chunk is falsy, and truthiness
            # let a second BIN chunk slide through the duplicate check.
            if bin_seen:
                raise ValueError("GLB contains more than one BIN chunk")
            bin_seen = True
            bin_payload = payload

    if json_payload is None:
        raise ValueError("GLB has no JSON chunk")
    try:
        document = json.loads(json_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"GLB JSON chunk is invalid: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("GLB JSON root must be an object")
    return document, bin_payload, {
        "version": version,
        "declaredLength": declared_length,
        "jsonChunkBytes": len(json_payload),
        "binChunkBytes": len(bin_payload),
        "chunks": chunks,
    }
