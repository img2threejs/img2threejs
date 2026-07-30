from __future__ import annotations

import secrets
from pathlib import Path

from .model import ItemId, JsonObject, JsonValue, StateProblem, canonical_json, hash_bytes, hash_file, parse_item_id
from .store import StateStore, now


class StateService:
    def __init__(self, root: Path) -> None:
        self.store = StateStore(root)

    def initialize(self, raw_item_id: str, primary_reference: str, from_artifacts: bool = False) -> JsonObject:
        item_id = parse_item_id(raw_item_id)
        if self.store.snapshot_path(item_id).exists():
            raise StateProblem("OPERATION_CONFLICT", "item state already exists", str(item_id))
        reference = self.store.bound_path(item_id, primary_reference)
        if not reference.is_file():
            raise StateProblem("USAGE", "primary reference must be an existing file", str(item_id))
        relative = str(reference.relative_to(self.store.root))
        snapshot: JsonObject = {
            "schemaVersion": "1.0",
            "itemId": str(item_id),
            "revision": -1,
            "phase": "initialized",
            "nextAction": "intake",
            "blockers": ["INTAKE_REQUIRED"],
            "gates": {"intakeAccepted": False, "genericHandoffReady": False, "specializedAdapterEligible": False},
            "artifacts": [{"path": relative, "role": "primary-reference", "sha256": hash_file(reference), "schemaVersion": ""}],
            "operations": [],
            "provenance": {"repositoryRoot": str(self.store.root), "initializedAt": now(), "fromArtifacts": from_artifacts},
            "ledgerWatermark": {"seq": -1, "hash": ""},
        }
        self.store.persist(item_id, snapshot, "initialized", "system")
        snapshot["revision"] = 0
        self.store.write_json(self.store.snapshot_path(item_id), snapshot)
        return self.summary(snapshot)

    def list_items(self) -> JsonObject:
        items: list[JsonValue] = []
        if self.store.items_root.exists():
            for directory in sorted(self.store.items_root.iterdir()):
                if directory.is_dir():
                    try:
                        snapshot = self.store.read_snapshot(parse_item_id(directory.name))
                    except StateProblem:
                        continue
                    items.append(self.summary(snapshot))
        return {"ok": True, "items": items}

    def read(self, raw_item_id: str) -> JsonObject:
        snapshot = self.store.read_snapshot(parse_item_id(raw_item_id))
        return {"ok": True, "snapshot": snapshot}

    def validate(self, raw_item_id: str) -> JsonObject:
        item_id = parse_item_id(raw_item_id)
        snapshot = self.store.read_snapshot(item_id)
        drift = self.store.artifact_drift(item_id, snapshot)
        if drift:
            raise StateProblem("ARTIFACT_DRIFT", f"artifact changed: {drift}", str(item_id), self.revision(snapshot))
        self.store.read_events(item_id)
        return {"ok": True, **self.summary(snapshot)}

    def resume(self, raw_item_id: str) -> JsonObject:
        item_id = parse_item_id(raw_item_id)
        self.validate(str(item_id))
        snapshot = self.store.read_snapshot(item_id)
        return {"ok": True, **self.summary(snapshot), "recovery": {"command": f"forge/state.py acknowledge {item_id} {self.revision(snapshot)}", "destructive": False, "requiresInput": False}}

    def claim(self, raw_item_id: str, actor: str) -> JsonObject:
        item_id = parse_item_id(raw_item_id)
        snapshot = self.store.read_snapshot(item_id)
        existing = self.store.read_lease(item_id)
        if existing and existing.get("actor") != actor:
            raise StateProblem("LEASE_CONFLICT", "item is leased by another actor", str(item_id), self.revision(snapshot))
        epoch = existing.get("epoch") if existing else 0
        if not isinstance(epoch, int):
            raise StateProblem("STATE_INVALID", "lease epoch must be an integer", str(item_id), self.revision(snapshot))
        lease: JsonObject = {
            "actor": actor,
            "token": secrets.token_hex(16),
            "epoch": epoch + 1,
            "createdAt": now(),
        }
        self.store.write_lease(item_id, lease)
        return {"ok": True, "itemId": str(item_id), "revision": self.revision(snapshot), "lease": lease}

    def acknowledge(self, raw_item_id: str, revision: int, actor: str) -> JsonObject:
        item_id = parse_item_id(raw_item_id)
        snapshot = self.store.read_snapshot(item_id)
        if revision != self.revision(snapshot):
            raise StateProblem("RESUME_REQUIRED", "acknowledgement revision is stale", str(item_id), self.revision(snapshot))
        lease = self.require_lease(item_id, actor, snapshot)
        acknowledgement: JsonObject = {"actor": actor, "revision": revision, "epoch": lease["epoch"], "token": lease["token"], "acknowledgedAt": now()}
        self.store.write_json(self.store.acknowledgement_path(item_id, actor), acknowledgement)
        return {"ok": True, "itemId": str(item_id), "revision": revision, "actor": actor}

    def heartbeat(self, raw_item_id: str, actor: str, token: str) -> JsonObject:
        item_id = parse_item_id(raw_item_id)
        snapshot = self.store.read_snapshot(item_id)
        lease = self.require_lease(item_id, actor, snapshot)
        if lease.get("token") != token:
            raise StateProblem("LEASE_CONFLICT", "lease token is stale", str(item_id), self.revision(snapshot))
        lease["heartbeatAt"] = now()
        self.store.write_lease(item_id, lease)
        return {"ok": True, "itemId": str(item_id), "revision": self.revision(snapshot), "lease": lease}

    def preflight(self, raw_item_id: str, action: str, actor: str) -> JsonObject:
        item_id = parse_item_id(raw_item_id)
        snapshot = self.store.read_snapshot(item_id)
        existing = self.operation_for_action(snapshot, action)
        if existing:
            raise StateProblem("OPERATION_CONFLICT", "an operation intent already exists for this action", str(item_id), self.revision(snapshot), self.text(existing, "operationId"))
        drift = self.store.artifact_drift(item_id, snapshot)
        if drift:
            raise StateProblem("ARTIFACT_DRIFT", f"artifact changed: {drift}", str(item_id), self.revision(snapshot))
        acknowledgement_path = self.store.acknowledgement_path(item_id, actor)
        if not acknowledgement_path.is_file():
            raise StateProblem("RESUME_REQUIRED", "resume acknowledgement is missing or stale", str(item_id), self.revision(snapshot), recovery_command=f"forge/state.py resume {item_id}")
        acknowledgement = self.store.read_json(acknowledgement_path, item_id)
        lease = self.require_lease(item_id, actor, snapshot)
        if acknowledgement.get("revision") != self.revision(snapshot) or acknowledgement.get("epoch") != lease.get("epoch"):
            raise StateProblem("RESUME_REQUIRED", "resume acknowledgement is missing or stale", str(item_id), self.revision(snapshot), recovery_command=f"forge/state.py resume {item_id}")
        if snapshot.get("nextAction") != action:
            raise StateProblem("INVALID_TRANSITION", f"action {action!r} is not the next safe action", str(item_id), self.revision(snapshot))
        operation_id = secrets.token_hex(16)
        operation: JsonObject = {"operationId": operation_id, "action": action, "status": "planned", "baseRevision": self.revision(snapshot), "actor": actor, "leaseEpoch": lease["epoch"], "inputHash": hash_bytes(canonical_json(snapshot.get("artifacts", [])).encode("utf-8")), "createdAt": now()}
        operations = snapshot.get("operations")
        if not isinstance(operations, list):
            raise StateProblem("STATE_INVALID", "snapshot operations must be an array", str(item_id))
        operations.append(operation)
        self.store.persist(item_id, snapshot, "operation-planned", actor, operation_id)
        return {"ok": True, "itemId": str(item_id), "action": action, "operationId": operation_id, "revision": self.revision(snapshot), "fencingEpoch": lease["epoch"]}

    def commit(self, raw_item_id: str, operation_id: str, actor: str, next_action: str) -> JsonObject:
        item_id = parse_item_id(raw_item_id)
        snapshot = self.store.read_snapshot(item_id)
        self.require_lease(item_id, actor, snapshot)
        operation = self.operation_by_id(snapshot, operation_id)
        if operation.get("status") != "planned":
            raise StateProblem("OPERATION_CONFLICT", "operation intent is unavailable", str(item_id), self.revision(snapshot), operation_id)
        operation["status"] = "completed"
        operation["completedAt"] = now()
        snapshot["phase"] = operation["action"]
        snapshot["nextAction"] = next_action
        snapshot["blockers"] = []
        self.store.persist(item_id, snapshot, "operation-completed", actor, operation_id)
        return {"ok": True, **self.summary(snapshot), "operationId": operation_id}

    def handoff(self, raw_item_id: str, actor: str, recipient: str, summary: str) -> JsonObject:
        item_id = parse_item_id(raw_item_id)
        snapshot = self.store.read_snapshot(item_id)
        self.require_lease(item_id, actor, snapshot)
        snapshot["handoff"] = {"handoffId": secrets.token_hex(16), "sender": actor, "recipient": recipient, "summary": summary, "revision": self.revision(snapshot), "createdAt": now()}
        self.store.persist(item_id, snapshot, "handoff-recorded", actor)
        return {"ok": True, **self.summary(snapshot), "handoff": snapshot["handoff"]}

    def release(self, raw_item_id: str, actor: str) -> JsonObject:
        item_id = parse_item_id(raw_item_id)
        snapshot = self.store.read_snapshot(item_id)
        self.require_lease(item_id, actor, snapshot)
        self.store.lease_path(item_id).unlink()
        return {"ok": True, "itemId": str(item_id), "revision": self.revision(snapshot)}

    def recover(self, raw_item_id: str, operation_id: str, actor: str) -> JsonObject:
        item_id = parse_item_id(raw_item_id)
        snapshot = self.store.read_snapshot(item_id)
        self.require_lease(item_id, actor, snapshot)
        operation = self.operation_by_id(snapshot, operation_id)
        if operation.get("status") != "planned":
            raise StateProblem("RECOVERY_REQUIRED", "only planned operations can be recovered", str(item_id), self.revision(snapshot), operation_id)
        operation["status"] = "unknown"
        operation["recoveredAt"] = now()
        snapshot["nextAction"] = "recover"
        snapshot["blockers"] = ["RECOVERY_REQUIRED"]
        self.store.persist(item_id, snapshot, "operation-recovered", actor, operation_id)
        return {"ok": True, **self.summary(snapshot), "operationId": operation_id}

    def summary(self, snapshot: JsonObject) -> JsonObject:
        return {"itemId": snapshot["itemId"], "revision": snapshot["revision"], "phase": snapshot["phase"], "nextAction": snapshot["nextAction"], "blockers": snapshot["blockers"], "artifacts": snapshot["artifacts"]}

    def revision(self, snapshot: JsonObject) -> int:
        revision = snapshot.get("revision")
        if not isinstance(revision, int):
            raise StateProblem("STATE_INVALID", "snapshot revision must be an integer")
        return revision

    def require_lease(self, item_id: ItemId, actor: str, snapshot: JsonObject) -> JsonObject:
        lease = self.store.read_lease(item_id)
        if not lease or lease.get("actor") != actor:
            raise StateProblem("LEASE_CONFLICT", "active lease for actor is required", str(item_id), self.revision(snapshot))
        return lease

    def operation_for_action(self, snapshot: JsonObject, action: str) -> JsonObject | None:
        operations = snapshot.get("operations")
        if not isinstance(operations, list):
            raise StateProblem("STATE_INVALID", "snapshot operations must be an array")
        for value in operations:
            if isinstance(value, dict) and value.get("action") == action and value.get("status") == "planned":
                return value
        return None

    def operation_by_id(self, snapshot: JsonObject, operation_id: str) -> JsonObject:
        operations = snapshot.get("operations")
        if not isinstance(operations, list):
            raise StateProblem("STATE_INVALID", "snapshot operations must be an array")
        for value in operations:
            if isinstance(value, dict) and value.get("operationId") == operation_id:
                return value
        raise StateProblem("OPERATION_CONFLICT", "operation intent does not exist", operation_id=operation_id)

    def text(self, value: JsonObject, key: str) -> str | None:
        field = value.get(key)
        return field if isinstance(field, str) else None
