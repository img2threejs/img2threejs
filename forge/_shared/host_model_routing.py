"""Local host routing request, not user attestation or paid-call consent.

The host supplies real typed answers separately. This authority only scopes their
local routing consumption; it never discovers credentials or invokes a model.
"""
import copy
import hashlib
from pathlib import Path

from decision_control import DEFAULT_REQUEST_TOKEN_LIMIT, DecisionError, _identifier, canonical_digest
from jev_cli_bridge import _file

POLICY = "host-model-routing-v1"
DEFAULT_TOKEN_BUDGET = 256000


class HostModelRouting:
    last_failure = None

    def __init__(self, workspace, run_id, image, *, request, sources=(),
                 model_id="host-agent", token_budget=DEFAULT_TOKEN_BUDGET):
        self.workspace = Path(workspace).resolve(strict=True)
        self.run_id = _identifier(run_id)
        self.model_id = _identifier(model_id)
        if (type(token_budget) is not int
                or not DEFAULT_REQUEST_TOKEN_LIMIT <= token_budget <= 2**53 - 1):
            raise DecisionError("invalid-host-model-request")
        self.token_budget = token_budget
        self.image = image
        self.sources = tuple(sources)
        self.request = copy.deepcopy(request)
        self.initial = self._read()

    @classmethod
    def from_journal(cls, workspace, run_id, data):
        """Reconstruct only an active journal request; never register or restore it."""
        if type(data) is not dict or type(data.get("request")) is not dict:
            raise DecisionError("host-model-request-unproven")
        request = data["request"]
        try:
            authority = cls(workspace, run_id, request["reference"]["path"],
                request=request["request"], sources=[source["path"] for source in request["sources"]],
                model_id=request["modelId"], token_budget=request["tokenBudget"])
        except (KeyError, TypeError):
            raise DecisionError("host-model-request-unproven") from None
        if authority.journal_data() != data:
            raise DecisionError("host-model-request-unproven")
        return authority

    def _binding(self, path):
        raw = _file(self.workspace, path, maximum=64 * 1024 * 1024, capture=True)
        return {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}

    def _read(self):
        return {"kind": "img2.host-model-routing-request", "version": 1,
                "policyVersion": POLICY, "workspace": str(self.workspace), "runId": self.run_id,
                "reference": self._binding(self.image),
                "sources": [self._binding(path) for path in self.sources],
                "request": copy.deepcopy(self.request), "modelId": self.model_id,
                "tokenBudget": self.token_budget, "purposes": ["routing"]}

    def journal_data(self):
        if self._read() != self.initial:
            raise DecisionError("host-model-request-changed")
        return {**self.metadata(), "request": copy.deepcopy(self.initial), "runId": self.run_id}

    def metadata(self):
        if self._read() != self.initial:
            raise DecisionError("host-model-request-changed")
        return {"decisionMode": "host-model", "authorizationId": canonical_digest(self.initial),
                "presentedAsJev": False, "modelId": self.model_id,
                "routingPolicyVersion": POLICY, "allowedPurposes": ["routing"]}

    def validate_context(self, context):
        self.metadata()
        reference = self.initial["reference"]
        if (context["group"] != "routing" or context["subject"]["runId"] != self.run_id
                or context["requestedModel"] != self.model_id
                or context["requirements"].get("request") != self.request
                or not any(b["role"] == "reference" and b["path"] == reference["path"]
                           and b["sha256"] == reference["sha256"] for b in context["bindings"])):
            raise DecisionError("host-model-binding-mismatch")

    def __call__(self):
        try:
            metadata = self.metadata()
            self.last_failure = None
            return {"granted": True, "purposes": ["routing"],
                    "sha256": metadata["authorizationId"], "tokenBudget": self.token_budget}
        except DecisionError as error:
            self.last_failure = error.reason_code
            raise
