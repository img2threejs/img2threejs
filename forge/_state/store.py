from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .model import ItemId, JsonObject, JsonValue, StateProblem, canonical_json, hash_bytes, hash_file, parse_json_object


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.items_root = self.root / ".agent-state" / "items"

    def item_directory(self, item_id: ItemId) -> Path:
        return self.items_root / str(item_id)

    def snapshot_path(self, item_id: ItemId) -> Path:
        return self.item_directory(item_id) / "snapshot.json"

    def events_path(self, item_id: ItemId) -> Path:
        return self.item_directory(item_id) / "events.jsonl"

    def lease_path(self, item_id: ItemId) -> Path:
        return self.item_directory(item_id) / "lease.json"

    def acknowledgement_path(self, item_id: ItemId, actor: str) -> Path:
        return self.item_directory(item_id) / "acknowledgements" / f"{actor}.json"

    def bound_path(self, item_id: ItemId, raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        resolved = candidate.resolve() if candidate.is_absolute() else (self.root / candidate).resolve()
        item_root = self.item_directory(item_id).resolve()
        if not resolved.is_relative_to(self.root) or resolved.is_relative_to(item_root / "lease.json"):
            raise StateProblem("PATH_SECURITY", "artifact path must remain inside repository", str(item_id))
        return resolved

    def write_json(self, path: Path, value: JsonObject) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        encoded = (canonical_json(value) + "\n").encode("utf-8")
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def read_json(self, path: Path, item_id: ItemId) -> JsonObject:
        if not path.is_file():
            raise StateProblem("STATE_INVALID", f"missing {path.name}", str(item_id))
        return parse_json_object(path.read_text(encoding="utf-8"), str(path))

    def read_snapshot(self, item_id: ItemId) -> JsonObject:
        snapshot = self.read_json(self.snapshot_path(item_id), item_id)
        self.validate_snapshot(snapshot, item_id)
        return snapshot

    def validate_snapshot(self, snapshot: JsonObject, item_id: ItemId) -> None:
        if snapshot.get("schemaVersion") != "1.0":
            raise StateProblem("STATE_INVALID", "unsupported state schema", str(item_id))
        if snapshot.get("itemId") != str(item_id):
            raise StateProblem("STATE_INVALID", "snapshot item ID does not match directory", str(item_id))
        if not isinstance(snapshot.get("revision"), int):
            raise StateProblem("STATE_INVALID", "snapshot revision must be an integer", str(item_id))

    def append_event(self, item_id: ItemId, event: JsonObject) -> JsonObject:
        path = self.events_path(item_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.read_events(item_id)
        event["seq"] = len(existing)
        event["hash"] = hash_bytes(canonical_json(event).encode("utf-8"))
        with path.open("ab") as handle:
            handle.write((canonical_json(event) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        return event

    def read_events(self, item_id: ItemId) -> list[JsonObject]:
        path = self.events_path(item_id)
        if not path.exists():
            return []
        events: list[JsonObject] = []
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            event = parse_json_object(line, f"{path}:{index + 1}")
            if event.get("seq") != index:
                raise StateProblem("RECOVERY_REQUIRED", "ledger sequence gap", str(item_id))
            events.append(event)
        return events

    def persist(self, item_id: ItemId, snapshot: JsonObject, event_type: str, actor: str, operation_id: str | None = None) -> JsonObject:
        revision = snapshot.get("revision")
        if not isinstance(revision, int):
            raise StateProblem("STATE_INVALID", "snapshot revision must be an integer", str(item_id))
        snapshot["revision"] = revision + 1
        event: JsonObject = {
            "eventId": secrets.token_hex(16),
            "type": event_type,
            "itemId": str(item_id),
            "actor": actor,
            "operationId": operation_id,
            "baseRevision": revision,
            "resultRevision": revision + 1,
            "timestamp": now(),
        }
        written = self.append_event(item_id, event)
        snapshot["ledgerWatermark"] = {"seq": written["seq"], "hash": written["hash"]}
        snapshot["updatedAt"] = now()
        self.write_json(self.snapshot_path(item_id), snapshot)
        return written

    def artifact_drift(self, item_id: ItemId, snapshot: JsonObject) -> str | None:
        artifacts = snapshot.get("artifacts")
        if not isinstance(artifacts, list):
            raise StateProblem("STATE_INVALID", "snapshot artifacts must be an array", str(item_id))
        for value in artifacts:
            if not isinstance(value, dict):
                raise StateProblem("STATE_INVALID", "artifact reference is invalid", str(item_id))
            path = value.get("path")
            expected = value.get("sha256")
            if not isinstance(path, str) or not isinstance(expected, str):
                raise StateProblem("STATE_INVALID", "artifact reference is incomplete", str(item_id))
            resolved = self.bound_path(item_id, path)
            if not resolved.is_file() or hash_file(resolved) != expected:
                return path
        return None

    def read_lease(self, item_id: ItemId) -> JsonObject | None:
        path = self.lease_path(item_id)
        return self.read_json(path, item_id) if path.exists() else None

    def write_lease(self, item_id: ItemId, lease: JsonObject) -> None:
        self.write_json(self.lease_path(item_id), lease)
