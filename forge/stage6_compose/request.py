"""Construct a composition request from existing parent state and evidence.

This is a structural join only. Parent state and placement records are not
independently authenticated here, and this does not authorize delivery.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from stage6_compose.compose import CompositionError, _json, _path, _read


def build_request(workspace, *, state_path, inventory_path, reference_path, placement_path):
    """Return a request whose references bind the exact current workspace bytes."""
    try:
        root_fd = os.open(Path(workspace), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise CompositionError("unsafe-path", "workspace") from exc
    try:
        def record(path, field):
            path = str(_path(path))
            data = _read(root_fd, path)
            return _json(data, field), {"path": path, "sha256": hashlib.sha256(data).hexdigest()}

        def reference(path):
            path = str(_path(path))
            data = _read(root_fd, path)
            return {"path": path, "sha256": hashlib.sha256(data).hexdigest()}

        state, _ = record(state_path, "state")
        inventory, inventory_ref = record(inventory_path, "inventory")
        placement, placement_ref = record(placement_path, "placement")
        reference_ref = reference(reference_path)
        if type(state) is not dict or type(state.get("routeDecision")) is not dict:
            raise CompositionError("invalid-record", "state")
        decision = state["routeDecision"]
        if (decision.get("phase") != "decided"
                or decision.get("inventorySha256") != inventory_ref["sha256"]
                or decision.get("imageSha256") != reference_ref["sha256"]
                or decision.get("skipComposition") is not False):
            raise CompositionError("stale-route-aggregate", "state.routeDecision")
        if (type(inventory) is not dict or inventory.get("kind") != "img2.object-inventory"
                or inventory.get("version") != 1 or type(inventory.get("objects")) is not list
                or not inventory["objects"]):
            raise CompositionError("invalid-record", "inventory")
        if (type(placement) is not dict or placement.get("kind") != "img2.scene-placement"
                or placement.get("version") != 1
                or placement.get("inventorySha256") != inventory_ref["sha256"]
                or placement.get("referenceSha256") != reference_ref["sha256"]):
            raise CompositionError("stale-placement", "placement")
        groups = {}
        for row in inventory["objects"]:
            if (type(row) is not dict or type(row.get("objectId")) is not str
                    or type(row.get("artifactObjectId")) is not str):
                raise CompositionError("invalid-record", "inventory.objects")
            groups.setdefault(row["artifactObjectId"], []).append(row["objectId"])
        objects, checklist = state.get("objects"), state.get("checklist")
        if type(objects) is not list or type(checklist) is not list:
            raise CompositionError("invalid-record", "state.objects")
        artifact_refs = []
        seen = set()
        for child in objects:
            if type(child) is not dict or type(child.get("objectId")) is not str:
                raise CompositionError("invalid-record", "state.objects")
            oid = child["objectId"]
            if oid in seen or oid not in groups:
                raise CompositionError("inventory-mismatch", oid)
            seen.add(oid)
            steps = [item for item in checklist if type(item) is dict and item.get("id") == "object:" + oid]
            if len(steps) != 1 or steps[0].get("status") != "done":
                raise CompositionError("child-incomplete", oid)
            artifact, artifact_ref = record(child.get("artifactPath"), "artifact")
            if (type(artifact) is not dict or artifact.get("objectId") != oid
                    or artifact.get("ownerRoute") != child.get("ownerRoute")
                    or type(artifact.get("ownedObjectIds")) is not list
                    or any(type(item) is not str for item in artifact["ownedObjectIds"])
                    or sorted(artifact["ownedObjectIds"]) != sorted(groups[oid])):
                raise CompositionError("ownership-mismatch", oid)
            artifact_refs.append(artifact_ref)
        if seen != set(groups):
            raise CompositionError("inventory-mismatch", "state.objects")
        return {"kind": "img2.scene-compose-request", "version": 1, "expectedOutput": "3d",
                "inventory": inventory_ref, "reference": reference_ref,
                "placement": placement_ref, "artifacts": artifact_refs}
    finally:
        os.close(root_fd)
