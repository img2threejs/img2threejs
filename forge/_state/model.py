from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NewType, TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
ItemId = NewType("ItemId", str)

SCHEMA_VERSION: Final = "1.0"
ITEM_ID_PATTERN: Final = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")
EXIT_CODES: Final = {
    "OK": 0,
    "USAGE": 2,
    "STATE_INVALID": 10,
    "RESUME_REQUIRED": 11,
    "ARTIFACT_DRIFT": 12,
    "LEASE_CONFLICT": 13,
    "NOTEBOOKLM_REVIEW_REQUIRED": 14,
    "RECOVERY_REQUIRED": 15,
    "INVALID_TRANSITION": 16,
    "OPERATION_CONFLICT": 17,
    "PATH_SECURITY": 18,
}


@dataclass(frozen=True, slots=True)
class StateProblem(Exception):
    code: str
    message: str
    item_id: str | None = None
    revision: int | None = None
    operation_id: str | None = None
    recovery_command: str | None = None

    def __str__(self) -> str:
        return self.message


def canonical_json(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_item_id(value: str) -> ItemId:
    normalized = value.strip().lower()
    if not ITEM_ID_PATTERN.fullmatch(normalized):
        raise StateProblem("USAGE", "item ID must match yyyy-mm-dd-item-name-slug")
    return ItemId(normalized)


def parse_json_object(text: str, source: str) -> JsonObject:
    try:
        value: JsonValue = json.loads(text)
    except json.JSONDecodeError as error:
        raise StateProblem("STATE_INVALID", f"invalid JSON in {source}") from error
    if not isinstance(value, dict):
        raise StateProblem("STATE_INVALID", f"{source} must contain a JSON object")
    return value


def error_payload(problem: StateProblem) -> JsonObject:
    recovery: JsonObject = {
        "command": problem.recovery_command or "",
        "destructive": False,
        "requiresInput": False,
    }
    return {
        "ok": False,
        "code": problem.code,
        "itemId": problem.item_id,
        "revision": problem.revision,
        "operationId": problem.operation_id,
        "message": problem.message,
        "recovery": recovery,
    }
