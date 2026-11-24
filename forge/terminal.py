#!/usr/bin/env python3
"""Prepare or consult a routed child's authenticated terminal quality review.

Both commands reproduce context from approved actual local producers. No context
or authority JSON is accepted. prepare never calls a decision provider or writes
a quality-review link. consult publishes only a journal-verified answered record;
neither command completes a child or grants terminal acceptance.

The four explicit harness pins and an external, SHA256-pinned host policy module
are required, exactly as for routing. Policy code and its dependencies are trusted
host code, not sandboxed candidate code. Only route --complete-child can perform
the independent terminal verification and lifecycle completion afterwards.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "_shared"))

from decision_control import DecisionController, DecisionError, canonical_digest
from jev_provider_factory import JevProviderFactory
from route_authority import RouteAuthority
from route_parent import ParentError, _decode, _encode, _read, _write
from stage0_route.discovery import read_only_probes
from stage0_route.fallback import HostFallback
import route
from route_no_account import RoutingNoAccount
from stage0_route.model_decider import ModelDecider


_AUTH = {"credential-missing", "credential-rejected", "credential-storage-error"}
_CONSENT = {"consent-required", "consent-declined", "consent-unproven", "consent-probe-error",
            "consent-changed", "consent-storage-error"}
_SAFE_ERRORS = route._SAFE_ERRORS | _AUTH | _CONSENT | {
    "terminal-input-invalid", "policy-module-unsafe", "policy-pin-required", "route-policy-required",
    "terminal-native-capture-authority-unavailable", "terminal-authenticated-quality-review-unavailable",
    "terminal-route-authority-required", "terminal-context-subject-mismatch", "terminal-context-changed",
    "record-mismatch", "stale-decision", "context-digest-mismatch", "subject-mismatch",
    "review-uncertain", "missing-evidence", "provider-unbound", "deadline-exhausted",
    "correction-budget-exhausted", "budget-overrun", "login-declined",
}


class TerminalReviews:
    """Orchestrate local preparation and shared controller review, never acceptance."""

    def __init__(self, parent, *, provider_factory=None, availability=None, fallback=None):
        self.parent = parent
        self.controller = parent.controller
        self.workspace = parent.workspace
        self.consent = parent.consent
        self.provider_factory = provider_factory
        self.availability = availability
        self.fallback = fallback

    def _prepare(self, verify, object_id):
        _, _, _, authority = self.parent._verified(verify)
        if not isinstance(authority, RouteAuthority):
            raise DecisionError("terminal-route-authority-required")
        child, area, link, context = RouteAuthority.prepare_terminal_review(authority, verify, object_id)
        prefix = child.relative_to(self.workspace).as_posix() + "/"
        subject = context["subject"]
        if (context.get("group") != "quality-review" or subject.get("runId") != self.controller.run_id
                or subject.get("objectId") != object_id
                or prefix != f".img2threejs/objects/{object_id}/rev-{subject.get('revision')}/"):
            raise DecisionError("terminal-context-subject-mismatch")
        path = prefix + area + "quality-review-context-" + canonical_digest(context) + ".json"
        raw = _encode(context)
        _write(self.workspace, path, raw)  # Immutable; identical retries only.
        return context, {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}, prefix + link

    def prepare(self, object_id):
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            _, binding, _ = self._prepare(verify, object_id)
        return {"status": "prepared", "context": binding}

    def consult(self, object_id):
        # Do not read a caller's saved context, even after an earlier prepare.
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            context, context_binding, link_path = self._prepare(verify, object_id)
        bindings = deepcopy(context["bindings"])

        def provider(projected):
            if self.provider_factory is None:
                raise DecisionError("provider-unbound")
            # The controller projection deliberately strips paths. Restore the
            # original bindings only for the trusted factory, not the provider.
            original = deepcopy(projected)
            original["bindings"] = deepcopy(bindings)
            return self.provider_factory.provider(original)(projected)

        # consult/consume acquire their own lease; never nest them in a held one.
        record = self.controller.consult(context, provider=provider, fallback=self.fallback,
            availability=self.availability, consent=self.consent, request_token_limit=64000)
        record = self.controller.consume(record, context, consent=self.consent)
        if record["status"] != "answered":
            return {"status": "request-input", "reasonCode": record["reasonCode"], "context": context_binding}
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            record = verify(record, context)
            self.parent._verified(verify)
            # Verify the immutable context again after the external consultation.
            if _read(self.workspace, context_binding["path"])[0] != _encode(context):
                raise DecisionError("terminal-context-changed")
            record_path = ".img2threejs/decisions/quality-review/" + record["recordId"] + ".json"
            record_raw = _read(self.workspace, record_path)[0]
            if _decode(record_raw) != record:
                raise DecisionError("record-mismatch")
            record_binding = {"path": record_path, "sha256": hashlib.sha256(record_raw).hexdigest()}
            _write(self.workspace, link_path,
                   _encode({"context": context_binding, "record": record_binding}), replace=True)
        return {"status": "decision-recorded", "context": context_binding,
                "record": record_binding, "linkPath": link_path}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "consult"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--object-id", required=True)
    parser.add_argument("--decision-ref", required=True)
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


def command_output(result, *, consent=None, trusted_parent_error=False):
    value = {**result, "kind": "img2.terminal-review-command", "version": 1,
             "deliverable": False, "terminalAcceptance": False}
    reason = result.get("reasonCode")
    if consent is not None and consent.last_failure:
        reason = consent.last_failure
    if reason is not None:
        trusted = (trusted_parent_error and type(reason) is str
                   and re.fullmatch(r"[a-z][a-z0-9-]{0,95}", reason) is not None)
        if type(reason) is not str or (reason not in _SAFE_ERRORS and not trusted):
            reason = "terminal-input-invalid"
        value["reasonCode"] = reason
    if reason in _AUTH:
        value.update(status="suspended", requestedCommand="img2-jev login", promptLimit=2)
    elif reason in _CONSENT:
        value.update(status="suspended", requestedCommand="img2-jev consent authorize-decisions", promptLimit=2,
                     requiredFlags=["--workspace", "--run-id", "--token-budget",
                                    "--accept-image-disclosure", "--accept-paid-calls"])
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
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", args.object_id) is None:
        parser.error("--object-id must be a valid object ID")
    consent = None
    try:
        workspace = args.workspace.resolve(strict=True)
        result = route._load_decision(workspace, args.decision_ref, args.run_id)
        release = None if args.no_jev_account else route._approved_lock()
        fallback = None if args.fallback_command is None else HostFallback(args.fallback_command, args.fallback_sha256)
        consent = RoutingNoAccount(workspace, args.run_id, args.image) if args.no_jev_account else None
        controller = DecisionController(workspace, args.run_id, routing_authority=consent)
        discovery = route.PinnedDiscovery(controller)
        if not args.no_jev_account:
            consent = route.LiveConsent(discovery, release, args.run_id, Path(sys.executable))
            discovery.consent_probe = consent
        harness = route.HarnessPin(args.harness_root, args.harness_sha, args.harness_executable,
                                   args.harness_sha256, route.HostGit() if args.no_jev_account else consent)
        discovery.harness, discovery.runner = harness.argv, harness.runner
        discovery.enumerate()
        policies = route.load_route_policies(args.policy_module, args.policy_sha256,
            workspace=workspace, run_id=args.run_id, excluded_roots=route.route_policy_roots(discovery, harness))
        factory = (ModelDecider(controller, consent, args.model_answers) if args.no_jev_account else
                   JevProviderFactory(discovery, approved_release=release, run_id=args.run_id,
                                      consent=consent, python_executable=consent.executable))
        router = route.Router(workspace, args.run_id, controller=controller, discovery=discovery,
                              consent=consent, provider_factory=factory, fallback=fallback,
                              **({"requested_model": consent.model_id} if args.no_jev_account else {}))
        parent = route._route_parent(router, result, harness, consent, policies=policies)
        reviews = TerminalReviews(parent, provider_factory=factory, availability=discovery.availability,
                                  fallback=fallback)
        with read_only_probes(discovery):
            result = getattr(reviews, args.command)(args.object_id)
        value = command_output(result, consent=consent)
        if args.no_jev_account:
            if value["status"] == "decision-recorded":
                value.update(consent.metadata())
            else:
                value["decisionMode"] = "no-jev-account-pending"
            if factory.pending is not None:
                value.update(status="questions-ready", questionsRef=factory.pending)
    except Exception as error:
        reason = getattr(error, "reason_code", "terminal-input-invalid")
        if isinstance(error, ParentError) and type(reason) is str:
            reason = reason.split(":", 1)[0]
        value = command_output({"status": "request-input", "reasonCode": reason}, consent=consent,
                               trusted_parent_error=isinstance(error, ParentError))
    print(json.dumps(value, sort_keys=True, allow_nan=False))
    return 0 if value["status"] in {"prepared", "decision-recorded", "questions-ready"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
