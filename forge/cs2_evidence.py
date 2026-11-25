#!/usr/bin/env python3
"""Prepare or consult explicitly host-approved, non-authorizing CS2 evidence.

The policy is external SHA256-pinned trusted Python code, never workspace JSON.
Neither this command nor a mechanical gate PASS completes a routed child.
"""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "_shared"), str(ROOT), str(ROOT.parent)]

from cs2_host_evidence import CS2EvidencePolicy, CS2EvidenceReviews
from decision_control import DecisionController, DecisionError
from host_policy import pinned_policy_module
from jev_provider_factory import JevProviderFactory
from route_parent import ParentError
from route_no_account import RoutingNoAccount
from stage0_route.discovery import read_only_probes
from stage0_route.model_decider import ModelDecider
import route


def load_policy(path, digest, *, workspace, run_id, plugin_root, harness_root=None):
    excluded = [ROOT.parent, plugin_root]
    if harness_root is not None:
        excluded.append(harness_root)
    with pinned_policy_module(path, digest, workspace=workspace, excluded_roots=excluded) as (module, pin):
        factory = getattr(module, "build_cs2_evidence_policy", None)
        if not callable(factory):
            raise DecisionError("cs2-evidence-policy-required")
        policy = factory(workspace, run_id)
        if (type(policy) is not CS2EvidencePolicy or policy.workspace != str(workspace)
                or policy.admission.run_id != run_id or policy.admission.plugin_root != str(plugin_root)):
            raise DecisionError("cs2-evidence-policy-required")
        pin()
        return policy, pin


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "consult"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--object-id", required=True)
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--policy-module", type=Path, required=True)
    parser.add_argument("--policy-sha256", required=True)
    parser.add_argument("--context")
    parser.add_argument("--context-sha256")
    parser.add_argument("--harness-root", type=Path)
    parser.add_argument("--harness-sha")
    parser.add_argument("--harness-executable", type=Path)
    parser.add_argument("--harness-sha256")
    parser.add_argument("--no-jev-account", action="store_true")
    parser.add_argument("--image")
    parser.add_argument("--model-answers")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "consult" and not all((args.context, args.context_sha256,
            args.harness_root, args.harness_sha, args.harness_executable, args.harness_sha256)):
        parser.error("consult requires the prepared context binding and all four harness pins")
    if args.no_jev_account and not args.image:
        parser.error("no-account consultation requires --image")
    if not args.no_jev_account and (args.image or args.model_answers):
        parser.error("--image/--model-answers require --no-jev-account")
    try:
        workspace = args.workspace.resolve(strict=True)
        plugin_root = args.plugin_root.resolve(strict=True)
        policy, pin = load_policy(args.policy_module, args.policy_sha256, workspace=workspace,
            run_id=args.run_id, plugin_root=plugin_root, harness_root=args.harness_root)
        reviews = CS2EvidenceReviews({policy.admission.object_id: policy}, revalidate=pin)
        if args.command == "prepare":
            result = reviews.prepare(args.object_id)
        else:
            consent = RoutingNoAccount(workspace, args.run_id, args.image) if args.no_jev_account else None
            controller = DecisionController(workspace, args.run_id, routing_authority=consent)
            discovery = route.PinnedDiscovery(controller)
            release = None if args.no_jev_account else route._approved_lock()
            if not args.no_jev_account:
                consent = route.LiveConsent(discovery, release, args.run_id, Path(sys.executable))
                discovery.consent_probe = consent
            harness = route.HarnessPin(args.harness_root, args.harness_sha, args.harness_executable,
                args.harness_sha256, route.HostGit() if args.no_jev_account else consent)
            discovery.harness, discovery.runner = harness.argv, harness.runner
            discovery.enumerate()
            factory = (ModelDecider(controller, consent, args.model_answers) if args.no_jev_account else
                JevProviderFactory(discovery, approved_release=release, run_id=args.run_id,
                    consent=consent, python_executable=consent.executable))
            with read_only_probes(discovery):
                result = reviews.consult(args.object_id,
                    {"path": args.context, "sha256": args.context_sha256}, controller=controller,
                    consent=consent, provider_factory=factory, availability=discovery.availability)
            if args.no_jev_account and factory.pending is not None:
                result.update(status="questions-ready", questionsRef=factory.pending)
    except Exception as error:
        reason = getattr(error, "reason_code", "cs2-evidence-input-invalid")
        if isinstance(error, ParentError):
            reason = reason.split(":", 1)[0]
        if type(reason) is not str or re.fullmatch(r"[a-z][a-z0-9-]{0,95}", reason) is None:
            reason = "cs2-evidence-input-invalid"
        result = CS2EvidenceReviews._result("request-input", reasonCode=reason)
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["status"] in {"prepared", "reviewed", "questions-ready"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
