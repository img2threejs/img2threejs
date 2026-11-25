"""Explicit trusted-host CS2 evidence workflow, never routed terminal authority.

Policy objects are constructed by approved external host code, not workspace JSON.
The existing routed workspace/held-verifier rules are not used or relaxed here.
"""
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import re

from cs2_terminal_authority import (AREA, CS2TerminalAuthority, CS2TerminalPolicy,
                                    _bound, _ref, _binding, _require, _sha)
from decision_control import DecisionController, canonical_digest
from route_parent import _decode, _encode, _read, _write

_POLICY = "cs2-host-evidence-policy-v1"


@dataclass(frozen=True)
class CS2EvidencePolicy:
    workspace: str
    admission: CS2TerminalPolicy

    def __post_init__(self):
        _require(type(self.admission) is CS2TerminalPolicy
                 and type(self.workspace) is str
                 and Path(self.workspace).is_absolute()
                 and str(Path(self.workspace).resolve(strict=True)) == self.workspace
                 and self.admission.parent_workspace == self.workspace,
                 "invalid-cs2-evidence-policy")


class CS2EvidenceReviews:
    """Capture/review evidence with a real controller journal, not a completion API."""

    def __init__(self, live_policies, *, revalidate=None):
        _require(type(live_policies) is dict and all(
            type(value) is CS2EvidencePolicy and key == value.admission.object_id
            for key, value in live_policies.items()), "invalid-cs2-evidence-policies")
        _require(revalidate is None or callable(revalidate), "invalid-cs2-evidence-policy")
        self._live = live_policies
        self._policies = deepcopy(live_policies)
        self._revalidate = revalidate
        # Composition deliberately does not expose/inherit verify_terminal.
        self._producer = CS2TerminalAuthority({})
        source = Path(__file__).resolve()
        self._producer._base_files[source] = _sha(source.read_bytes())

    def _policy(self, object_id):
        _require(self._live == self._policies, "cs2-evidence-policy-changed")
        if self._revalidate is not None:
            self._revalidate()
        wrapper = self._policies.get(object_id)
        _require(wrapper is not None, "cs2-evidence-policy-unavailable")
        policy = wrapper.admission
        self._producer._check_host(policy)
        return policy, Path(wrapper.workspace)

    @staticmethod
    def _result(status, **values):
        return {"kind": "img2.cs2-host-evidence", "version": 1, "status": status,
                "scope": "mechanical-replay-only", "terminalAuthority": False,
                "terminalAcceptance": False, "deliverable": False, **values}

    def prepare(self, object_id):
        policy, workspace = self._policy(object_id)
        inputs = _binding(policy.inputs_binding)
        artifact = _ref(_decode(_bound(workspace, inputs))["artifact"])
        context = self._producer._prepare_production(policy, workspace, artifact, inputs,
            revalidate=lambda: self._policy(object_id), policy_version=_POLICY)
        request = next(row for row in context["bindings"] if row["role"] == "produced-gate-request")
        area = request["path"].removesuffix("gate-request.json")
        path = area + "quality-review-context-" + canonical_digest(context) + ".json"
        binding = {"path": path, "sha256": _sha(_read(workspace, path)[0])}
        return self._result("prepared", context=binding,
                            request={key: request[key] for key in ("path", "sha256")})

    def _checked(self, object_id, context_ref):
        policy, workspace = self._policy(object_id)
        context_ref = _ref(context_ref)
        match = re.fullmatch(re.escape(AREA) + r"(run-[a-f0-9]{32}/)quality-review-context-([a-f0-9]{64})\.json",
                             context_ref["path"])
        _require(match is not None, "cs2-quality-context-path-mismatch")
        area = AREA + match.group(1)
        context = _decode(_bound(workspace, context_ref))
        _require(canonical_digest(context) == match.group(2), "cs2-quality-review-context-mismatch")
        inputs = _binding(policy.inputs_binding)
        artifact = _ref(_decode(_bound(workspace, inputs))["artifact"])
        values, _ = self._producer._admit(policy, workspace, artifact, inputs)
        path = area + "inputs.json"
        replay = self._producer._replay(policy, workspace,
            {"path": path, "sha256": _sha(_read(workspace, path)[0])})
        _require({k: v for k, v in replay["inputs"].items() if k not in {"captures", "materialCaptures"}} == values,
                 "cs2-produced-inputs-not-approved")
        _require(context == self._producer._context(policy, workspace, replay, area, policy_version=_POLICY),
                 "cs2-quality-review-context-mismatch")
        return policy, workspace, artifact, context, area, replay

    def consult(self, object_id, context_ref, *, controller, consent, provider_factory,
                availability=None, fallback=None):
        policy, workspace = self._policy(object_id)
        _require(type(controller) is DecisionController and controller.workspace == workspace
                 and controller.run_id == policy.run_id, "cs2-review-controller-mismatch")
        with controller.workspace_transaction(consent=consent):
            checked = self._checked(object_id, context_ref)
        context = checked[3]
        bindings = deepcopy(context["bindings"])

        def provider(projected):
            original = deepcopy(projected)
            original["bindings"] = deepcopy(bindings)
            return provider_factory.provider(original)(projected)

        # The model consult is outside the held transaction. No implicit extra
        # attempt/capture is made on a pending review; the same immutable ref resumes.
        record = controller.consult(context, provider=provider, availability=availability,
            fallback=fallback, consent=consent, request_token_limit=64000)
        record = controller.consume(record, context, consent=consent)
        if record["status"] != "answered":
            return self._result("request-input", reasonCode=record["reasonCode"], context=_ref(context_ref))
        with controller.workspace_transaction(consent=consent) as verify:
            policy, workspace, artifact, fresh_context, area, replay = self._checked(object_id, context_ref)
            _require(fresh_context == context, "cs2-quality-review-context-mismatch")
            record = self._producer._quality_record(verify, record, context, policy)
            gate = self._producer._gate(policy, workspace, area, replay, artifact)
            # Revalidate source/runtime, request and all current evidence after gate execution.
            _require(self._checked(object_id, context_ref)[3] == context, "cs2-terminal-evidence-changed")
            record = verify(record, context)
            record_path = ".img2threejs/decisions/quality-review/" + record["recordId"] + ".json"
            record_raw = _read(workspace, record_path)[0]
            _require(_decode(record_raw) == record, "cs2-quality-review-not-authenticated")
            receipt = self._result("reviewed" if gate["verdict"] == "pass" else "rejected",
                context=_ref(context_ref), record={"path": record_path, "sha256": _sha(record_raw)},
                evidence=gate)
            path = area + "host-review-receipt-" + record["recordId"] + ".json"
            _write(workspace, path, _encode(receipt))
            return {**receipt, "receipt": {"path": path, "sha256": _sha(_read(workspace, path)[0])}}
