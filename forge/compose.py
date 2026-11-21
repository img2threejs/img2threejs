#!/usr/bin/env python3
"""Executable entry point for base-owned D11/D18 composition and D12 scene gates.

Three subcommands, all fail-closed and offline:

    python3 forge/compose.py request --workspace WS --state STATE --inventory INVENTORY --reference IMAGE --placement PLACEMENT
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
import re

ROOT = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(ROOT / "_shared")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from scene_quality import assess_scene  # noqa: E402
from _shared.route_artifact import ArtifactError  # noqa: E402
from stage6_compose.compose import CompositionError, _read, compose_scene  # noqa: E402
from stage6_compose.request import build_request  # noqa: E402
from stage0_route.storage import snapshot
from _shared.placement_decision import placement_context, consult_placement, consume_placement

_MAX_INPUT = 64 * 1024 * 1024


def _emit(value: dict) -> None:
    print(json.dumps(value, sort_keys=True))


def _refusal(reason: str, field: str) -> dict:
    return {"kind": "img2.compose-command", "version": 1, "status": "refused",
            "reasonCode": reason, "field": field, "deliverable": False}


def _read_json(path: Path, field: str) -> tuple[dict, bytes]:
    """Read one bounded JSON object; an unreadable or oversized input refuses."""
    try:
        with path.open("rb") as source:
            data = source.read(_MAX_INPUT + 1)
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
    except (CompositionError, ArtifactError) as error:
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


def _workspace_ref(workspace, path):
    descriptor = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        raw = _read(descriptor, path)
    finally:
        os.close(descriptor)
    return {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}, raw


def _bound_json(workspace, reference):
    if type(reference) is not dict or set(reference) != {"path", "sha256"}:
        raise _InputError("invalid-reference", "request")
    current, raw = _workspace_ref(workspace, reference["path"])
    if current != reference:
        raise _InputError("stale-evidence", reference["path"])
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise _InputError("invalid-json", reference["path"]) from error
    if type(value) is not dict:
        raise _InputError("invalid-record", reference["path"])
    return value


def _host_pins(args):
    pins, raw = _read_json(args.host_pins, "host-pins")
    if (not re.fullmatch(r"[0-9a-f]{64}", args.host_pins_sha256)
            or hashlib.sha256(raw).hexdigest() != args.host_pins_sha256
            or set(pins) != {"chrome", "modules"}):
        raise _InputError("host-pin-mismatch", "host-pins")
    return pins


def _produce(args):
    request, _ = _read_json(args.request, "request")
    pins = _host_pins(args)
    if args.command == "prepare-placement":
        from stage6_compose.placement import prepare_placement
        required = {"inventory", "reference", "artifacts", "camera_solve", "depth", "support", "environment"}
        if not required <= set(request) or set(request) - required - {"base_container"}:
            raise _InputError("invalid-record", "placement-inputs")
        request.setdefault("base_container", None)
        manifest = prepare_placement(args.workspace, **request, chrome=pins["chrome"],
                                     modules=pins["modules"], output_dir=args.output_dir)
    else:
        from stage6_compose.measure import measure_scene
        required = {"scene", "inventory", "reference", "placement", "viewport"}
        if not required <= set(request) or set(request) - required - {"background", "reference_mask"}:
            raise _InputError("invalid-record", "measurement-inputs")
        manifest = measure_scene(args.workspace, **request, chrome=pins["chrome"],
                                 modules=pins["modules"], output_dir=args.output_dir)
    _emit({"kind": "img2.compose-command", "version": 1, "status": "produced",
           "manifest": manifest, "deliverable": False})
    return 0


def _placement_runtime(args):
    # Reuse routing's real approved harness, credentials, consent and provider.
    # No saved consent object or model-authored availability enters this path.
    from route import (DecisionController, PinnedDiscovery, LiveConsent, HarnessPin,
                       JevProviderFactory, Router, HostFallback, _approved_lock,
                       _load_decision, _route_parent)
    workspace = args.workspace.resolve(strict=True)
    controller = DecisionController(workspace, args.run_id)
    discovery = PinnedDiscovery(controller)
    release = _approved_lock()
    consent = LiveConsent(discovery, release, args.run_id, Path(sys.executable))
    discovery.consent_probe = consent
    harness = HarnessPin(args.harness_root, args.harness_sha, args.harness_executable,
                         args.harness_sha256, consent)
    discovery.harness, discovery.runner = harness.argv, harness.runner
    factory = JevProviderFactory(discovery, approved_release=release, run_id=args.run_id,
                                 consent=consent, python_executable=consent.executable)
    fallback = HostFallback(args.fallback_command, args.fallback_sha256) if args.fallback_command else None
    router = Router(workspace, args.run_id, controller=controller, discovery=discovery, consent=consent)
    latest_ref, _ = _workspace_ref(workspace, router.latest)
    latest = _bound_json(workspace, latest_ref)
    result = _load_decision(workspace, latest["path"], args.run_id)
    discovery.enumerate()
    parent = _route_parent(router, result, harness, consent)
    return workspace, controller, discovery, consent, factory, fallback, parent


def _placement(args):
    from stage0_route.discovery import read_only_probes
    from stage6_compose.placement import produce_placement
    request, _ = _read_json(args.request, "request")
    workspace, controller, discovery, consent, factory, fallback, parent = _placement_runtime(args)
    preparation = request.get("preparation")
    plan = _bound_json(workspace, preparation)
    with read_only_probes(discovery), controller.workspace_transaction(consent=consent) as verify:
        decision, inventory, _, _ = parent._verified(verify)
        expected = {"path": decision["inventoryRef"], "sha256": decision["inventorySha256"]}
        if (plan.get("arguments", {}).get("inventory") != expected
                or plan.get("inventorySha256") != expected["sha256"]):
            raise _InputError("inventory-mismatch", "preparation")
        containers = {row["objectId"] for row in inventory["objects"] if row["role"] == "scene-container"}
        routed_ids = {row["objectId"] for row in inventory["objects"]
                      if row["artifactObjectId"] not in containers or row["objectId"] in containers}
        rows = plan.get("objects")
        if (type(rows) is not list or len(rows) != len(routed_ids)
                or {row.get("objectId") for row in rows} != routed_ids):
            raise _InputError("inventory-mismatch", "preparation.objects")
    if args.command == "place":
        if request.get("kind") != "img2.scene-placement-request" or request.get("version") != 1:
            raise _InputError("invalid-record", "request")
        with read_only_probes(discovery), controller.workspace_transaction(consent=consent) as verify:
            parent._verified(verify)
            manifest = produce_placement(
                workspace, preparation=preparation, decisions=request["decisions"],
                verify_answer=lambda context, record: consume_placement(verify, context, record),
                output_dir=args.output_dir)
        _emit({"kind": "img2.compose-command", "version": 1, "status": "produced",
               "manifest": manifest, "deliverable": False})
        return 0
    state = _bound_json(workspace, _workspace_ref(workspace, ".img2threejs/state.json")[0])
    revisions = {row["objectId"]: row["revision"] for row in state["objects"]}
    revisions = {row["objectId"]: revisions[row["artifactObjectId"]] for row in inventory["objects"]}
    decisions, suspended = {}, []
    for row in plan["objects"]:
        object_id = row["objectId"]
        decisions[object_id] = {}
        for question in ("size", "yaw"):
            context = placement_context(
                {"runId": args.run_id, "objectId": object_id, "revision": revisions[object_id]},
                row["decisionBindings"], question=question, options=row[question + "Options"],
                observations={"images": [{"role": "reference", "asset": "reference-image"}]})
            context_ref = snapshot(workspace, "placement-context", context)
            context_ref = {key: context_ref[key] for key in ("path", "sha256")}
            with read_only_probes(discovery):
                result = consult_placement(
                    controller, context, provider=factory.provider(context), fallback=fallback,
                    availability=discovery.availability, consent=consent)
            record = result["record"]
            record_path = ".img2threejs/decisions/build-strategy/" + record["recordId"] + ".json"
            record_ref, _ = _workspace_ref(workspace, record_path)
            decisions[object_id][question] = {"context": context_ref, "record": record_ref}
            if result["answer"] is None:
                suspended.append({"objectId": object_id, "question": question, "reasonCode": record["reasonCode"]})
    _emit({"kind": "img2.scene-placement-request", "version": 1, "preparation": preparation,
           "decisions": decisions, "status": "request-input" if suspended else "decided",
           "suspended": suspended, "deliverable": False})
    return 3 if suspended else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    request = commands.add_parser("request", help="Join existing parent and placement evidence into a request")
    request.add_argument("--workspace", type=Path, default=Path("."))
    request.add_argument("--state", required=True, help="workspace-relative parent state")
    request.add_argument("--inventory", required=True, help="workspace-relative routed inventory")
    request.add_argument("--reference", required=True, help="workspace-relative source image")
    request.add_argument("--placement", required=True, help="workspace-relative existing placement record")

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
    for name in ("prepare-placement", "measure"):
        producer = commands.add_parser(name, help="Produce actual geometry/capture evidence; never accept delivery")
        producer.add_argument("--workspace", type=Path, required=True)
        producer.add_argument("--request", type=Path, required=True)
        producer.add_argument("--output-dir", required=True)
        producer.add_argument("--host-pins", type=Path, required=True,
                              help="Host-selected Chrome/module bindings, not model input")
        producer.add_argument("--host-pins-sha256", required=True,
                              help="Explicit host approval of exact host-pins JSON bytes")
    for name in ("consult-placement", "place"):
        placement = commands.add_parser(name, help="Use shared journalled size/yaw decisions")
        placement.add_argument("--workspace", type=Path, required=True)
        placement.add_argument("--request", type=Path, required=True)
        placement.add_argument("--run-id", required=True)
        placement.add_argument("--harness-root", type=Path, required=True)
        placement.add_argument("--harness-sha", required=True)
        placement.add_argument("--harness-executable", type=Path, required=True)
        placement.add_argument("--harness-sha256", required=True)
        placement.add_argument("--fallback-command", type=Path)
        placement.add_argument("--fallback-sha256")
        if name == "place":
            placement.add_argument("--output-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in {"place", "consult-placement"} and (
            (args.fallback_command is None) != (args.fallback_sha256 is None)):
        build_parser().error("--fallback-command and --fallback-sha256 must be supplied together")
    try:
        if args.command in {"prepare-placement", "measure"}:
            return _produce(args)
        if args.command in {"consult-placement", "place"}:
            return _placement(args)
        if args.command == "request":
            try:
                _emit(build_request(args.workspace, state_path=args.state, inventory_path=args.inventory,
                                    reference_path=args.reference, placement_path=args.placement))
            except CompositionError as error:
                _emit(_refusal(error.reason_code, error.field))
                return 3
            return 0
        return _scene(args) if args.command == "scene" else _gates(args)
    except _InputError as error:
        _emit(_refusal(error.reason_code, error.field))
        return 2
    except (CompositionError, ArtifactError) as error:
        _emit(_refusal(error.reason_code, getattr(error, "field", "request")))
        return 3
    except (ValueError, OSError, KeyError, TypeError) as error:
        reason = getattr(error, "reason_code", None)
        if type(reason) is not str or re.fullmatch(r"[a-z][a-z0-9-]{0,95}", reason) is None:
            reason = "invalid-producer-input"
        _emit(_refusal(reason, "request"))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
