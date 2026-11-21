#!/usr/bin/env python3
"""Executable entry point for base-owned D11/D18 composition and D12 scene gates.

Two subcommands, both fail-closed and offline:

    python3 forge/compose.py scene --workspace WS --request REQ --output-dir DIR
    python3 forge/compose.py gates --workspace WS --composed scene.json \
        --measurements M.json --inventory INVENTORY.json

`scene` writes a NEW candidate directory through ``compose_scene``; it never
advances workflow state, consults a provider, or accepts delivery. `gates`
assesses caller-supplied measurements through ``assess_scene`` against a routed
inventory and the current composed module and input bytes. Hash checks do not
authenticate the manifest, inventory or measurement producers. A pass here is
not canonical workflow acceptance; the parent still owns that decision.

Exit codes: 0 success/pass, 2 usage or unreadable input, 3 refusal or gate
failure. Every refusal prints one JSON object carrying its reason code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(ROOT / "_shared")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from scene_quality import assess_scene  # noqa: E402
from stage6_compose.compose import CompositionError, _read, compose_scene  # noqa: E402

_MAX_INPUT = 64 * 1024 * 1024


def _emit(value: dict) -> None:
    print(json.dumps(value, sort_keys=True))


def _refusal(reason: str, field: str) -> dict:
    return {"kind": "img2.compose-command", "version": 1, "status": "refused",
            "reasonCode": reason, "field": field, "deliverable": False}


def _read_json(path: Path, field: str) -> tuple[dict, bytes]:
    """Read one bounded JSON object; an unreadable or oversized input refuses."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise _InputError("unreadable-input", field) from exc
    if len(data) > _MAX_INPUT:
        raise _InputError("input-too-large", field)
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _InputError("invalid-json", field) from exc
    if type(value) is not dict:
        raise _InputError("invalid-record", field)
    return value, data


class _InputError(Exception):
    def __init__(self, reason_code: str, field: str) -> None:
        self.reason_code = reason_code
        self.field = field
        super().__init__(f"{reason_code}: {field}")


def _scene(args: argparse.Namespace) -> int:
    request, _ = _read_json(args.request, "request")
    try:
        workspace = args.workspace.resolve(strict=True)
    except OSError as exc:
        raise _InputError("unreadable-input", "workspace") from exc
    try:
        manifest = compose_scene(workspace, request, output_dir=args.output_dir)
    except CompositionError as error:
        _emit(_refusal(error.reason_code, getattr(error, "field", "request")))
        return 3
    _emit({"kind": "img2.compose-command", "version": 1, "status": manifest.get("status", "composed"),
           "manifest": manifest, "deliverable": False})
    return 0 if manifest.get("status") != "refused" else 3


def _inventory_ids(inventory: dict) -> list[str]:
    """Read object identities from the supplied inventory without authenticating it."""
    if inventory.get("kind") != "img2.object-inventory" or inventory.get("version") != 1:
        raise _InputError("invalid-record", "inventory")
    rows = inventory.get("objects")
    if type(rows) is not list or not rows:
        raise _InputError("missing-evidence", "inventory.objects")
    ids = []
    for row in rows:
        if type(row) is not dict or type(row.get("objectId")) is not str or not row["objectId"]:
            raise _InputError("invalid-record", "inventory.objects")
        ids.append(row["objectId"])
    if len(set(ids)) != len(ids):
        raise _InputError("duplicate-object", "inventory.objects")
    return ids


def _gates(args: argparse.Namespace) -> int:
    composed, _ = _read_json(args.composed, "composed")
    measurements, _ = _read_json(args.measurements, "measurements")
    inventory, inventory_bytes = _read_json(args.inventory, "inventory")
    if composed.get("kind") != "img2.composed-scene" or composed.get("version") != 1:
        raise _InputError("invalid-record", "composed")
    module = composed.get("module")
    if (type(module) is not dict or type(module.get("path")) is not str
            or type(module.get("sha256")) is not str):
        raise _InputError("missing-evidence", "composed.module")
    inputs = composed.get("inputs")
    if (type(inputs) is not list or not inputs
            or any(type(ref) is not dict or set(ref) != {"path", "sha256"}
                   or type(ref["path"]) is not str or type(ref["sha256"]) is not str
                   for ref in inputs)):
        raise _InputError("missing-evidence", "composed.inputs")
    if composed.get("referenceSha256") not in {ref["sha256"] for ref in inputs}:
        raise _InputError("missing-evidence", "composed.reference")
    try:
        workspace_fd = os.open(args.workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if hashlib.sha256(_read(workspace_fd, module["path"])).hexdigest() != module["sha256"]:
                _emit(_refusal("stale-scene-module", "composed.module"))
                return 3
            for ref in inputs:
                if hashlib.sha256(_read(workspace_fd, ref["path"])).hexdigest() != ref["sha256"]:
                    _emit(_refusal("stale-scene-input", ref["path"]))
                    return 3
        finally:
            os.close(workspace_fd)
    except CompositionError as error:
        _emit(_refusal(error.reason_code, error.field))
        return 3
    except OSError:
        _emit(_refusal("unsafe-path", "workspace"))
        return 3
    # The measurements must describe the exact composed module, reference and
    # inventory bytes under review. Any other scene is stale evidence.
    if measurements.get("sceneSha256") != module["sha256"]:
        _emit(_refusal("stale-scene-measurement", "measurements.sceneSha256"))
        return 3
    if measurements.get("referenceSha256") != composed.get("referenceSha256"):
        _emit(_refusal("stale-reference-measurement", "measurements.referenceSha256"))
        return 3
    if composed.get("inventorySha256") != hashlib.sha256(inventory_bytes).hexdigest():
        _emit(_refusal("inventory-mismatch", "inventory"))
        return 3
    result = assess_scene(measurements, expected_object_ids=_inventory_ids(inventory))
    _emit({"kind": "img2.compose-command", "version": 1, "status": result["status"],
           "result": result, "deliverable": False})
    return 0 if result["status"] == "pass" else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    scene = commands.add_parser("scene", help="Compose validated child artifacts into a candidate scene")
    scene.add_argument("--workspace", type=Path, default=Path("."))
    scene.add_argument("--request", type=Path, required=True,
                       help="img2.scene-compose-request v1 JSON")
    scene.add_argument("--output-dir", required=True,
                       help="workspace-relative directory that must not already exist")

    gates = commands.add_parser("gates", help="Measure D12 scene gates over produced measurements")
    gates.add_argument("--workspace", type=Path, default=Path("."))
    gates.add_argument("--composed", type=Path, required=True, help="scene.json written by `scene`")
    gates.add_argument("--measurements", type=Path, required=True,
                       help="externally produced scene measurements")
    gates.add_argument("--inventory", type=Path, required=True,
                       help="routed img2.object-inventory for the run")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _scene(args) if args.command == "scene" else _gates(args)
    except _InputError as error:
        _emit(_refusal(error.reason_code, error.field))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
