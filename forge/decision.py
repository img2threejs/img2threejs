#!/usr/bin/env python3
"""Dispatch a routed child's strategy-spec, strategy-build, quality or repair.

Run from the base checkout with --workspace PARENT --run-id RUN --child-state
.img2threejs/objects/ID/rev-N/state.json --request REQUEST.json and the same four
explicit --harness-* pins used for routing. Request paths are parent-relative.

Host policy approval is separate: --policy-module /canonical/host/policy.py and
--policy-sha256 EXACT_SHA256 approve executable host code, never candidate JSON.
The module exports build_policy(workspace: Path, state_path: str)->WorkflowPolicy.
It must live outside the workspace and provider/harness trees. The file pin covers
ONLY that module; its interpreter, imports, callbacks and dependencies are host
trust, not sandboxed code. Native callbacks must invoke fixed approved validator
CLIs rather than import plugin implementations. No built-in policy guesses source
rights, real screenshot admission or native authority.

Strategy/repair request operation is select, begin or finish. Selection persists
pending authority only; strategy completion requires a separately written
candidates/rev-N spec matching the selected full proposal. Quality operation
trial reviews only the begun repair application's successor and freezes its
answered receipt; repair finish consumes that stored review. No command promotes
an incumbent or declares a deliverable. Login/consent suspension returns the
existing Jev commands; it never invokes login, enrolls or loops over prompts.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import re
import sys
import json

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "_shared"))
from decision_control import DecisionController, DecisionError
from workflow_decisions import WorkflowDecisions, WorkflowPolicy
from jev_cli_bridge import _file, _json
from jev_provider_factory import JevProviderFactory
from route import LiveConsent, PinnedDiscovery, HarnessPin, HostGit, _approved_lock
from stage0_route.discovery import EDGES, read_only_probes
from stage0_route.fallback import HostFallback
from host_policy import pinned_policy_module
from route_no_account import RoutingNoAccount
from stage0_route.model_decider import ModelDecider


def load_policy(module_path, sha256, *, workspace, state_path, excluded_roots=()):
    """Load host-approved workflow policy and recheck its pin on authorization."""
    with pinned_policy_module(module_path, sha256, workspace=workspace,
                              excluded_roots=excluded_roots) as (module, pin):
        factory = getattr(module, "build_policy", None)
        if not callable(factory):
            raise DecisionError("workflow-policy-required")
        policy = factory(Path(workspace), state_path)
        if type(policy) is not WorkflowPolicy:
            raise DecisionError("workflow-policy-required")
        authorize = policy.authorize
        def live_authorize(*args):
            pin()
            result = authorize(*args)
            pin()
            return result
        return replace(policy, authorize=live_authorize)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("strategy-spec", "strategy-build", "quality", "repair"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--child-state", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--policy-module", type=Path, required=True)
    parser.add_argument("--policy-sha256", required=True)
    parser.add_argument("--harness-root", type=Path, required=True)
    parser.add_argument("--harness-sha", required=True)
    parser.add_argument("--harness-executable", type=Path, required=True)
    parser.add_argument("--harness-sha256", required=True)
    parser.add_argument("--fallback-command", type=Path)
    parser.add_argument("--fallback-sha256")
    parser.add_argument("--no-jev-account", action="store_true")
    parser.add_argument("--image", help="Attested workspace-relative reference; required in no-account mode")
    parser.add_argument("--model-answers", help="Workspace-relative attestation-bound model answers")
    return parser


def command_output(result, *, consent=None):
    record = result.get("record", result.get("decision"))
    reason = result.get("reasonCode") or (record.get("reasonCode") if type(record) is dict else None)
    if consent is not None and consent.last_failure:
        reason = consent.last_failure
    value = {"kind": "img2.workflow-decision-command", "version": 1,
             "deliverable": False, "terminalAcceptance": False, "result": result}
    if type(reason) is str:
        value["reasonCode"] = reason
    if reason in {"credential-missing", "credential-rejected", "credential-storage-error"}:
        value.update(status="suspended", requestedCommand="img2-jev login", promptLimit=2)
    elif reason in {"consent-required", "consent-declined", "consent-unproven", "consent-probe-error"}:
        value.update(status="suspended", requestedCommand="img2-jev consent authorize-decisions",
                     requiredFlags=["--workspace", "--run-id", "--token-budget", "--accept-image-disclosure", "--accept-paid-calls"])
    elif result.get("action") == "request-input" or result.get("status") in {"request-input", "stop"}:
        value["status"] = "request-input"
    else:
        value["status"] = "decision-recorded"
    return value


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if (args.fallback_command is None) != (args.fallback_sha256 is None):
        parser.error("fallback command and digest must be supplied together")
    if args.no_jev_account and (not args.image or args.fallback_command):
        parser.error("no-account mode requires --image and excludes outage fallback")
    if not args.no_jev_account and (args.image or args.model_answers):
        parser.error("--image/--model-answers require --no-jev-account")
    consent = None
    try:
        workspace = args.workspace.resolve(strict=True)
        request = _json(_file(workspace, args.request, maximum=256 * 1024, capture=True))
        release = None if args.no_jev_account else _approved_lock()
        fallback = None if args.fallback_command is None else HostFallback(args.fallback_command, args.fallback_sha256)
        consent = RoutingNoAccount(workspace, args.run_id, args.image) if args.no_jev_account else None
        controller = DecisionController(workspace, args.run_id, routing_authority=consent)
        discovery = PinnedDiscovery(controller)
        if not args.no_jev_account:
            consent = LiveConsent(discovery, release, args.run_id, Path(sys.executable))
            discovery.consent_probe = consent
        harness = HarnessPin(args.harness_root, args.harness_sha, args.harness_executable,
                             args.harness_sha256, HostGit() if args.no_jev_account else consent)
        discovery.harness, discovery.runner = harness.argv, harness.runner
        discovery.enumerate()
        excluded = [harness.root]
        for edge in EDGES.values():
            response = discovery.query(edge)
            excluded.extend(Path(row["dir"]) for row in response.get("providers", []) if type(row.get("dir")) is str)
        if discovery.jev is not None:
            excluded.append(Path(discovery.jev["dir"]))
        policy = load_policy(args.policy_module, args.policy_sha256, workspace=workspace,
                             state_path=args.child_state, excluded_roots=excluded)
        if args.no_jev_account:
            policy = replace(policy, requested_model=consent.model_id)
            factory = ModelDecider(controller, consent, args.model_answers)
        else:
            factory = JevProviderFactory(discovery, approved_release=release, run_id=args.run_id,
                                         consent=consent, python_executable=consent.executable)
        workflow = WorkflowDecisions(controller, args.child_state, policy, consent=consent,
            availability=discovery.availability, provider_factory=factory, fallback=fallback)
        with read_only_probes(discovery):
            result = workflow.dispatch(args.command, request)
        value = command_output(result, consent=consent)
        if args.no_jev_account:
            if value["status"] == "decision-recorded":
                value.update(consent.metadata())
            else:
                value["decisionMode"] = "no-jev-account-pending"
            if factory.pending is not None:
                value.update(status="questions-ready", questionsRef=factory.pending)
    except Exception as error:
        reason = getattr(error, "reason_code", "workflow-input-invalid")
        if type(reason) is not str or re.fullmatch(r"[a-z][a-z0-9-]{0,95}", reason) is None:
            reason = "workflow-input-invalid"
        value = command_output({"action": "request-input", "reasonCode": reason}, consent=consent)
    print(json.dumps(value, sort_keys=True, allow_nan=False))
    return 0 if value["status"] in {"decision-recorded", "questions-ready"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
