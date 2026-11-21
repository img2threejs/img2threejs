"""Base-owned routed-child strategy, quality and repair command boundaries.

WorkflowPolicy is executable host authority, never deserialized request data.
Its authorize callback must authenticate current producer/capture evidence,
source provenance and permissions independently (hashes alone are insufficient).
Native validators/resolvers may call fixed host-approved plugin CLIs; this module
never imports plugin implementations. No method publishes/promotes an artifact.
Quality operation="trial" reviews only a journal-begun pending repair successor;
it freezes afterQualityReview in decisionPending without publishing canonical
quality, completing the repair, or promoting the candidate/incumbent.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from types import MappingProxyType
from types import SimpleNamespace
from typing import Callable

from decision_control import DecisionError, GROUPS, canonical_digest
from route_parent import _read, _write, _decode, _encode
from workflow_state import validate_state, next_entry, recompute
from forge.stage2_spec.strategy_decision import StrategyDecision, _read_binding, _spec
from forge.stage2_spec.native_strategy_decision import NativeStrategyDecision
from forge.stage4_review.quality_decision import review_quality, _prepare, _read_bindings
from _shared.decision_quality import assess_candidate
from _shared.decision_control import DecisionError as GateDecisionError
from forge.stage4_review.repair_decision import (
    prepare_repair, begin_repair, finish_repair, _quality_evidence, _semantic_pair)
from forge.stage3_build.orchestrate_passes import sync_pipeline
from forge.stage2_spec.validate_sculpt_spec import validate_spec

_STATE = re.compile(r"\.img2threejs/objects/([A-Za-z0-9][A-Za-z0-9_-]{0,159})/rev-([1-9][0-9]*)/state\.json\Z")
_SCOPE = {"components", "operations", "field_paths", "authorized_sources", "source_operations",
          "available_dependencies", "rig_required", "rig_available"}
_STEPS = {"strategy-spec", "strategy-build", "quality-review", "repair-decision"}


def _fail(reason):
    raise DecisionError(reason)


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(value)
    return value


@dataclass(frozen=True)
class NativePolicy:
    validate_spec: Callable
    current_pass: Callable
    authorize_native: Callable
    rules: tuple
    frozen_paths: frozenset
    forbidden_components: frozenset

    def __post_init__(self):
        object.__setattr__(self, "rules", _freeze(self.rules))
        object.__setattr__(self, "frozen_paths", frozenset(self.frozen_paths))
        object.__setattr__(self, "forbidden_components", frozenset(self.forbidden_components))


@dataclass(frozen=True)
class WorkflowPolicy:
    scope: dict
    requirements: dict
    authorize: Callable
    draft_spec: str
    permissions: frozenset = frozenset()
    repair_criteria: dict | None = None
    native: NativePolicy | None = None
    requested_model: str = "jev-1.14.0"
    request_token_limit: int = 64000
    strategy_policy_version: str = "workflow-strategy-v1"

    def __post_init__(self):
        for name in ("scope", "requirements", "repair_criteria"):
            object.__setattr__(self, name, _freeze(getattr(self, name)))
        object.__setattr__(self, "permissions", frozenset(self.permissions))


def _validate_base(spec):
    errors, warnings = validate_spec(spec)
    return not errors and not any(warning.startswith("quality:") for warning in warnings)


def _relative(value):
    if (type(value) is not str or not value or value.startswith("/") or "\\" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        _fail("unsafe-path")
    return value


def _quality_continues(context, record, assessment):
    if record["status"] != "answered" or not assessment["passed"]:
        return False
    for dimension in context["requirements"]["dimensions"]:
        item = {"id": dimension} if type(dimension) is str else dimension
        if record["answers"]["dimension:" + item["id"]]["score"] < item.get("threshold", 3):
            return False
    return all(record["answers"]["critical:" + feature["id"]]["choice"] == "pass"
               for feature in context["requirements"]["criticalFeatures"])


def _blocked_quality(controller, context):
    """Recognize a measured failure, never an admission/permission outage."""
    try:
        _prepare(controller, context)
    except (DecisionError, GateDecisionError) as exc:
        if exc.reason_code != "vision-forbidden":
            raise
    else:
        _fail("workflow-quality-block-invalid")
    hashes, documents = _read_bindings(controller, context)
    gate, report, views = (documents[role] for role in ("gate-report", "quality-report", "view-set"))
    candidate = report["candidate"]
    assessment = assess_candidate(candidate)
    if (assessment["hardStatus"] != "fail"
            or any(check["status"] == "unevaluated" for check in assessment["checks"])
            or not any(item.get("status") == "fail" for item in gate["gateResults"])
            or any(type(reason) is not str or any(
                word in reason.lower() for word in ("permission", "provenance", "evidence", "consent"))
                for reason in gate["hardGateFailures"])
            or any(document.get("faults") for document in (gate, report, candidate))
            or any(item.get("status") == "fail" and any(
                word in item["id"].lower() for word in ("permission", "provenance", "evidence", "consent"))
                for item in gate["gateResults"])):
        _fail("workflow-quality-block-invalid")
    captures = views.get("views")
    if (type(captures) is not list or not captures
            or any(type(item) is not dict or set(item) != {"id", "role", "sha256"}
                   or hashes.get(item["role"]) != item["sha256"] for item in captures)
            or context["observations"].get("availableViews") != [item["id"] for item in captures]
            or candidate["views"] != [item["id"] for item in captures]
            or not set(context["requirements"]["requiredViews"]) <= {item["id"] for item in captures}):
        _fail("missing-views")
    crops = views.get("features")
    features = context["requirements"]["criticalFeatures"]
    if (type(crops) is not list or any(type(item) is not dict or set(item) != {
            "id", "referenceRole", "referenceSha256", "renderRole", "renderSha256"} for item in crops)
            or len(crops) != len(features) or {item["id"] for item in crops} != {item["id"] for item in features}
            or any(hashes.get(crop[prefix + "Role"]) != crop[prefix + "Sha256"]
                   for crop in crops for prefix in ("reference", "render"))):
        _fail("missing-critical-evidence")
    required_images = {"reference", "render"} | {item["role"] for item in captures} | {
        crop[prefix + "Role"] for crop in crops for prefix in ("reference", "render")}
    images = context["observations"].get("images")
    if (type(images) is not list or any(type(item) is not dict or set(item) != {"role", "asset"} for item in images)
            or not required_images <= {item["role"] for item in images}):
        _fail("missing-evidence")
    return assessment


class WorkflowDecisions:
    def __init__(self, controller, state_path, policy, *, consent, availability,
                 provider_factory=None, fallback=None):
        if (type(policy) is not WorkflowPolicy or not callable(policy.authorize)
                or not isinstance(policy.scope, Mapping) or set(policy.scope) != _SCOPE
                or not isinstance(policy.requirements, Mapping)
                or type(policy.permissions) is not frozenset
                or type(policy.request_token_limit) is not int or not 0 < policy.request_token_limit <= 64000
                or not callable(consent) or not callable(availability)):
            _fail("workflow-policy-required")
        if policy.native is not None and (type(policy.native) is not NativePolicy or any(
                not callable(callback) for callback in (policy.native.validate_spec,
                    policy.native.current_pass, policy.native.authorize_native))):
            _fail("native-authority-unavailable")
        if not _STATE.fullmatch(state_path):
            _fail("invalid-child-state-path")
        _relative(policy.draft_spec)
        self.controller, self.workspace, self.state_path = controller, controller.workspace, state_path
        self.child_root = str(Path(state_path).parent)
        self.policy = policy
        self.consent, self.availability = consent, availability
        self.provider_factory, self.fallback = provider_factory, fallback

    def _load(self):
        state = validate_state(_decode(_read(self.workspace, self.state_path)[0]))
        parent = validate_state(_decode(_read(self.workspace, ".img2threejs/state.json")[0]))
        child = state.get("routeChild", {})
        match = _STATE.fullmatch(self.state_path)
        descriptors = [item for item in parent.get("objects", []) if item.get("statePath") == self.state_path]
        lifecycle = state.get("decisionLifecycle")
        if (len(descriptors) != 1 or child.get("runId") != self.controller.run_id
                or parent.get("runId") != self.controller.run_id
                or child.get("objectId") != match[1] or child.get("revision") != int(match[2])
                or any(child.get(key) != descriptors[0].get(key) for key in child)
                or state.get("leaseToken") != parent.get("leaseToken")
                or state.get("leaseToken") != self.controller._token
                or type(lifecycle) is not dict or lifecycle.get("version") != 1
                or type(lifecycle.get("candidateRevision")) is not int
                or lifecycle["candidateRevision"] < child["revision"]
                or lifecycle.get("incumbentRevision") != child["revision"]
                or type(state.get("decisionRefs")) is not list or state.get("status") == "stopped"):
            _fail("child-state-mismatch")
        return state

    def _live(self, command, request, *, verify=None):
        trial_review = command == "quality" and request.get("operation") == "trial"
        if trial_review and verify is None:
            with self.controller.workspace_transaction(consent=self.consent) as held:
                return self._live(command, request, verify=held)
        state = self._load()
        subject, bindings = request.get("subject"), request.get("bindings")
        if type(subject) is not dict or type(bindings) is not list:
            _fail("invalid-request")
        application = self._trial_application(state, verify) if trial_review else None
        expected = {"runId": self.controller.run_id, "objectId": state["routeChild"]["objectId"],
                    "revision": application["trialRevision"] if trial_review else state["decisionLifecycle"]["candidateRevision"],
                    "passId": application["subject"]["passId"] if trial_review else subject.get("passId")}
        if subject != expected:
            _fail("subject-mismatch")
        spec_path = self.child_root + "/" + _relative(self.policy.draft_spec if command == "strategy-spec"
                                                      else state["artifacts"].get("spec"))
        roles = [item.get("role") for item in bindings if type(item) is dict]
        if len(roles) != len(bindings) or len(set(roles)) != len(roles):
            _fail("invalid-binding")
        by_role = {item["role"]: item for item in bindings}
        spec_binding = by_role.get("spec", {})
        if (not spec_binding
                or (command == "strategy-spec" and spec_binding.get("path") != spec_path)
                or by_role.get("reference", {}).get("path") != self.child_root + "/" + state["artifacts"]["reference"]
                or by_role["reference"].get("sha256") != state["routeChild"]["cropSha256"]):
            _fail("evidence-mismatch")
        for binding in bindings:
            self.controller._binding(binding)
        if command != "strategy-spec":
            self._candidate_output(spec_binding, subject["revision"])
            if trial_review:
                for role in ("source", "gate-report"):
                    self._candidate_output(by_role.get(role), subject["revision"])
            else:
                # Canonical publication is separate; ordinary quality still
                # requires the immutable snapshot to match canonical bytes.
                live_spec = _read(self.workspace, spec_path)[0]
                if hashlib.sha256(live_spec).hexdigest() != spec_binding["sha256"]:
                    _fail("stale-evidence")
        spec = _spec(_read_binding(self.workspace, by_role["spec"]))
        validator = _validate_base if self.policy.native is None else self.policy.native.validate_spec
        if validator(deepcopy(spec)) is not True:
            _fail("spec-validation-failed")
        current_pass = (sync_pipeline(deepcopy(spec))["currentPass"] if self.policy.native is None
                        else self.policy.native.current_pass(deepcopy(spec)))
        if (type(current_pass) is not str or not current_pass or current_pass == "complete"
                or subject["passId"] != current_pass
                or (state.get("currentPass") not in {"", "complete", current_pass})):
            _fail("locked-or-wrong-pass")
        if self.policy.authorize(command, deepcopy(subject), deepcopy(bindings)) is not True:
            _fail("workflow-evidence-not-authorized")
        return state, spec

    def _trial_application(self, state, verify):
        pending = state.get("decisionPending")
        if (type(pending) is not dict or pending.get("step") != "repair-decision"
                or type(pending.get("application")) is not dict
                or next_entry(state) is None or next_entry(state)["id"] != "repair-decision"
                or not callable(getattr(verify, "application", None))):
            _fail("application-not-authorized")
        saved = pending["application"]
        application = _plain(verify.application(saved.get("applicationId")))
        if (application != saved or application["status"] != "pending" or application["group"] != "repair"
                or application["decisionRef"] != pending["record"]["recordId"]
                or application["subject"] != pending["context"]["subject"]
                or application["inputRevision"] != state["decisionLifecycle"]["candidateRevision"]
                or application["inputRevision"] != pending["context"]["subject"]["revision"]
                or (state.get("currentPass") != application["subject"]["passId"]
                    and not (self.policy.native is not None and state.get("currentPass") == "complete"))
                or type(application["trialRevision"]) is not int
                or application["trialRevision"] <= application["inputRevision"]):
            _fail("application-not-authorized")
        frozen_scope = pending["context"]["requirements"]["repairScope"]
        scope = {key: sorted(value) if isinstance(value, (set, frozenset)) else _plain(value)
                 for key, value in self.policy.scope.items()}
        if (any(frozen_scope.get(key) != value for key, value in scope.items())
                or frozen_scope.get("permissions") != sorted(self.policy.permissions)):
            _fail("workflow-evidence-not-authorized")
        return application

    def _callbacks(self, bindings):
        def provider(projected):
            if self.provider_factory is None:
                _fail("provider-unbound")
            context = deepcopy(projected)
            context["bindings"] = deepcopy(bindings)
            return self.provider_factory.provider(context)(projected)
        return {"provider": provider, "fallback": self.fallback, "availability": self.availability,
                "consent": self.consent, "request_token_limit": self.policy.request_token_limit}

    def _context(self, group, request):
        return {"kind": GROUPS[group][0], "version": 1, "group": group,
                "subject": deepcopy(request["subject"]), "bindings": deepcopy(request["bindings"]),
                "candidates": deepcopy(request["candidates"]),
                "observations": deepcopy(request.get("observations", {})),
                "requirements": _plain(self.policy.requirements),
                "policyVersion": "route-quality-policy-v1" if group == "quality-review" else self.policy.strategy_policy_version,
                "questionSetVersion": GROUPS[group][1], "requestedModel": self.policy.requested_model}

    def _strategy(self, context, request):
        scope = dict(_plain(self.policy.scope), pass_id=context["subject"]["passId"], pass_unlocked=True)
        options = {"scope": scope, "proposals": request["proposals"], "validate_spec": _validate_base}
        if self.policy.native is None:
            return StrategyDecision(self.controller, context, **options)
        native = self.policy.native
        options.update(validate_spec=native.validate_spec, native_rules=_plain(native.rules),
                       frozen_paths=native.frozen_paths, forbidden_components=native.forbidden_components,
                       authorize_native=native.authorize_native)
        return NativeStrategyDecision(self.controller, context, **options)

    @staticmethod
    def _complete(state, step, evidence, *, skipped=False):
        entry = next_entry(state)
        if entry is None or entry["id"] != step:
            _fail("workflow-step-mismatch")
        entry.update(status="skipped" if skipped else "done", evidence=[evidence],
                     reason="quality-passed-no-repair" if skipped else "")
        recompute(state)

    def _persist(self, original, updated, *, record, context, command, request):
        # Consume immediately before the transaction; verify again while holding
        # the sole parent controller lease, then replace only this child state.
        self.controller.consume(record, context, consent=self.consent)
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            verify(record, context)
            live, _ = self._live(command, request, verify=verify)
            if canonical_digest(live) != canonical_digest(original):
                _fail("child-state-changed")
            if command == "repair" and request.get("operation") == "begin":
                self._trial_application(updated, verify)
            counts = _plain(verify.correction_counts(live["routeChild"]["objectId"]))
            updated["loops"].update(counts)
            if command == "repair" and "qualityReview" in context["observations"]:
                if (request.get("operation") == "finish"
                        and updated["decisionRefs"][-1]["application"]["status"] == "completed"):
                    _verify_semantic_receipt(verify, self.controller, updated["decisionRefs"][-1])
                else:
                    selection = live["decisionPending"]["selection"] if request.get("operation") == "finish" else updated["decisionPending"]["selection"]
                    _quality_evidence(self.controller, selection.get("qualityReview"), context["subject"],
                        context["bindings"], context["observations"]["before"],
                        context["observations"]["criteria"]["semantic"], verify=verify)
            validate_state(updated)
            _write(self.workspace, self.state_path, _encode(updated), replace=True)

    def _persist_block(self, original, context, request):
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            live, _ = self._live("quality", request)
            if canonical_digest(live) != canonical_digest(original):
                _fail("child-state-changed")
            assessment = _blocked_quality(self.controller, context)
            block = {"kind": "img2.workflow-quality-block", "version": 1,
                     "step": "quality-review", "context": deepcopy(context),
                     "subject": deepcopy(context["subject"]), "reasonCode": "deterministic-hard-gate-failed",
                     "assessment": assessment, "receiptIndex": len(live["decisionRefs"])}
            block["blockId"] = canonical_digest(block)
            updated = deepcopy(live)
            self._complete(updated, "quality-review", block["blockId"])
            row = next(row for row in updated["checklist"] if row["id"] == "quality-review")
            row.update(status="skipped", reason=block["reasonCode"])
            updated.setdefault("decisionBlocks", []).append(block)
            validate_state(updated)
            verify.decision_block(block, publish=True)
            _write(self.workspace, self.state_path, _encode(updated), replace=True)
        return {"action": "refine", "status": "fail", "deliverable": False,
                "reasonCode": block["reasonCode"], "assessment": assessment,
                "decision": None, "block": block}

    def dispatch(self, command, request):
        if command not in {"strategy-spec", "strategy-build", "quality", "repair"} or type(request) is not dict:
            _fail("invalid-request")
        request = deepcopy(request)
        allowed = {"subject", "bindings", "candidates", "observations", "proposals", "operation",
                   "trialRevision", "outputBinding", "failure", "after", "afterSubject", "afterBindings",
                   "afterQualityReview"}
        if set(request) - allowed:
            _fail("request-authority-forbidden")
        state, _ = self._live(command, request)
        operation = request.get("operation", "select")
        step = ("repair-decision" if command == "quality" and operation == "trial" else
                {"quality": "quality-review", "repair": "repair-decision"}.get(command, command))
        if next_entry(state) is None or next_entry(state)["id"] != step:
            _fail("workflow-step-mismatch")
        callbacks = self._callbacks(request["bindings"])
        updated = deepcopy(state)
        pending = state.get("decisionPending")
        if command.startswith("strategy-"):
            context = self._context("build-strategy", request)
            stage = self._strategy(context, request)
            # Strategy preflight enriches and filters the exact controller context.
            context = deepcopy(stage._context)
            if operation == "select":
                result = stage.consult(**callbacks)
                record = result["record"]
                self.controller.consume(record, context, consent=self.consent)
                if result["action"] != "selected":
                    return result
                updated["decisionPending"] = {"step": step, "context": context, "record": record}
            else:
                if (type(pending) is not dict or pending.get("step") != step
                        or pending.get("context") != context):
                    _fail("application-not-authorized")
                record = self.controller.consume(pending["record"], context, consent=self.consent)
                trial = self._trial(request, state)
                started = stage.begin_application(record, trial, consent=self.consent)
                if operation == "begin":
                    result = started
                elif operation == "finish":
                    output = request.get("outputBinding")
                    self._candidate_output(output, trial)
                    application = stage.complete_application(record, started["application"], output, consent=self.consent)
                    result = {"application": application, "action": "candidate-completed"}
                    updated["decisionLifecycle"]["candidateRevision"] = trial
                    if not updated.get("currentPass"):
                        updated["currentPass"] = context["subject"]["passId"]
                    self._complete(updated, step, record["recordId"])
                    updated["decisionRefs"].append({"step": step, "context": context, "record": record,
                                                    "application": application, "outputBinding": output})
                    updated.pop("decisionPending", None)
                else:
                    _fail("invalid-operation")
        elif command == "quality":
            if operation not in {"select", "trial"}:
                _fail("invalid-operation")
            context = self._context("quality-review", request)
            result = review_quality(self.controller, context, **callbacks)
            record = result.get("decision")
            if operation == "trial":
                if record is None or record["status"] != "answered":
                    return dict(result, deliverable=False)
                record = self.controller.consume(record, context, consent=self.consent)
                envelope = {"context": context, "record": record}
                updated["decisionPending"]["afterQualityReview"] = envelope
                result = dict(result, action="trial-reviewed", reviewAction=result["action"],
                              deliverable=False, qualityReview=envelope)
                self._persist(state, updated, record=record, context=context, command=command, request=request)
                return result
            if record is None:
                if result["reasonCode"] == "vision-forbidden":
                    try:
                        _blocked_quality(self.controller, context)
                    except (DecisionError, GateDecisionError):
                        return result
                    return self._persist_block(state, context, request)
                return result
            record = self.controller.consume(record, context, consent=self.consent)
            if record["status"] != "answered":
                return result
            self._complete(updated, step, record["recordId"])
            updated["decisionRefs"].append({"step": step, "context": context, "record": record,
                                            "action": result["action"]})
            if result["action"] == "continue":
                self._complete(updated, "repair-decision", record["recordId"], skipped=True)
        else:
            if operation == "select":
                if not self.policy.repair_criteria:
                    _fail("repair-policy-required")
                counts = self.controller.correction_counts(request["subject"]["objectId"])
                scope = dict(_plain(self.policy.scope), pass_id=request["subject"]["passId"], pass_unlocked=True,
                             permissions=self.policy.permissions, remainingTrials=min(
                                 state["loops"]["maxTotal"] - counts["total"],
                                 state["loops"]["maxPerPass"] - counts["perPass"].get(request["subject"]["passId"], 0)),
                             evidenceAdmitted=True, provenanceVerified=True, permissionGranted=True)
                quality_receipts = [receipt for receipt in state["decisionRefs"]
                                    if receipt["step"] == "quality-review" and receipt.get("action") == "refine"
                                    and receipt["context"]["subject"] == request["subject"]]
                quality_review = ({"context": quality_receipts[-1]["context"], "record": quality_receipts[-1]["record"]}
                                  if quality_receipts else None)
                result = prepare_repair(self.controller, subject=request["subject"], bindings=request["bindings"],
                    failure=request["failure"], candidates=request["candidates"], scope=scope,
                    criteria=_plain(self.policy.repair_criteria), requirements=_plain(self.policy.requirements),
                    requested_model=self.policy.requested_model, quality_review=quality_review, **callbacks)
                record, context = result.get("record"), result.get("context")
                if record is None or result["status"] not in {"refine-spec", "refine-code"}:
                    return result
                self.controller.consume(record, context, consent=self.consent)
                updated["decisionPending"] = {"step": step, "context": context, "record": record,
                                               "selection": result}
            else:
                if type(pending) is not dict or pending.get("step") != step:
                    _fail("application-not-authorized")
                context, record, selection = pending["context"], pending["record"], pending["selection"]
                if context["subject"] != request["subject"] or context["bindings"] != request["bindings"]:
                    _fail("subject-mismatch")
                self.controller.consume(record, context, consent=self.consent)
                trial = self._trial(request, state)
                if operation == "begin":
                    result = {"application": begin_repair(self.controller, selection, trial, consent=self.consent)}
                    updated["decisionPending"]["application"] = result["application"]
                elif operation == "finish":
                    after_quality_review = pending.get("afterQualityReview")
                    if "qualityReview" in context["observations"]:
                        if after_quality_review is None:
                            _fail("semantic-quality-review-required")
                    if ("afterQualityReview" in request
                            and request["afterQualityReview"] != after_quality_review):
                        _fail("semantic-quality-review-mismatch")
                    for binding in request["afterBindings"]:
                        if binding["role"] in {"spec", "source", "gate-report"}:
                            self._candidate_output(binding, trial)
                    spec_bindings = [binding for binding in request["afterBindings"] if binding["role"] == "spec"]
                    if len(spec_bindings) != 1:
                        _fail("evidence-missing")
                    successor = _spec(_read_binding(self.workspace, spec_bindings[0]))
                    validator = _validate_base if self.policy.native is None else self.policy.native.validate_spec
                    if (validator(deepcopy(successor)) is not True
                            or self.policy.authorize("repair-finish", deepcopy(request["afterSubject"]),
                                                     deepcopy(request["afterBindings"])) is not True):
                        _fail("workflow-evidence-not-authorized")
                    result = finish_repair(self.controller, selection, trial_revision=trial, after=request["after"],
                        after_subject=request["afterSubject"], after_bindings=request["afterBindings"], consent=self.consent,
                        after_quality_review=after_quality_review)
                    application = result["application"]
                    updated["decisionRefs"].append({"step": step, "context": context, "record": record,
                                                    "application": application})
                    if "qualityReview" in context["observations"]:
                        updated["decisionRefs"][-1]["qualityReviews"] = {
                            "before": selection["qualityReview"], "after": after_quality_review}
                    self._complete(updated, step, record["recordId"])
                    if result["retained"]:
                        updated["decisionLifecycle"]["candidateRevision"] = trial
                        # Repair comparison never replaces fresh build/review.
                        rows = updated["checklist"]
                        start = next(index for index, row in enumerate(rows) if row["id"] == "strategy-build")
                        end = next(index for index, row in enumerate(rows) if row["id"] == "repair-decision")
                        updated.setdefault("passHistory", []).append({
                            "passId": context["subject"]["passId"], "iteration": "repair",
                            "checklist": deepcopy(rows[start + 1:end + 1])})
                        for row in rows[start + 1:end + 1]:
                            row.update(status="pending", evidence=[], reason="")
                        recompute(updated)
                    else:
                        updated.update(status="stopped", currentStep="stopped",
                                       stopReason="repair-candidate-not-retained")
                    updated.pop("decisionPending", None)
                else:
                    _fail("invalid-operation")
        # Recheck real validator, producer admission and live state after callbacks.
        self._live(command, request)
        self._persist(state, updated, record=record, context=context, command=command, request=request)
        return result

    @staticmethod
    def _trial(request, state):
        revision = request.get("trialRevision")
        if type(revision) is not int or revision <= state["decisionLifecycle"]["candidateRevision"]:
            _fail("invalid-successor")
        return revision

    def _candidate_output(self, binding, revision):
        prefix = self.child_root + "/candidates/rev-" + str(revision) + "/"
        if type(binding) is not dict or not binding.get("path", "").startswith(prefix):
            _fail("invalid-successor-output")
        self.controller._binding(binding)


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _verify_semantic_receipt(verify, controller, receipt):
    context, application = receipt["context"], receipt["application"]
    reviews = receipt.get("qualityReviews")
    if type(reviews) is not dict or set(reviews) != {"before", "after"}:
        _fail("semantic-quality-review-required")
    outcome = application["outcome"]
    if application["status"] != "completed" or "after" not in outcome["comparison"]:
        _fail("application-not-authorized")
    after_report = outcome["comparison"]["after"]["report"]
    after_subject = dict(context["subject"], revision=application["trialRevision"])
    historical = getattr(verify, "historical_quality", verify)
    comparison, proofs = _semantic_pair(controller, reviews["before"], reviews["after"], context,
        after_subject, outcome["afterBindings"], after_report, verify=historical)
    if (canonical_digest(comparison) != canonical_digest(outcome["comparison"])
            or proofs != outcome.get("qualityReviews") or not comparison["retained"]):
        _fail("semantic-quality-comparison-mismatch")


def verify_lifecycle(verify, state, child_root, parent_root):
    """Revalidate terminal lifecycle receipts under the caller's HELD transaction.

    ``verify.application`` must be the controller's journal-backed application
    verifier; persisted application-shaped JSON cannot prove completion.
    """
    lifecycle = state.get("decisionLifecycle")
    if lifecycle is None and type(state.get("routeChild")) is not dict:
        return
    if (type(lifecycle) is not dict
            or set(lifecycle) != {"version", "candidateRevision", "incumbentRevision"}
            or type(lifecycle["version"]) is not int or lifecycle["version"] != 1
            or any(type(lifecycle[key]) is not int or lifecycle[key] < 1
                   for key in ("candidateRevision", "incumbentRevision"))
            or lifecycle["candidateRevision"] < lifecycle["incumbentRevision"]):
        _fail("workflow-decisions-missing")
    child_root, parent_root = Path(child_root), Path(parent_root)
    relative = child_root.relative_to(parent_root).as_posix()
    match = _STATE.fullmatch(relative + "/state.json")
    if match is None or lifecycle.get("version") != 1:
        _fail("invalid-child-state-path")
    child = state["routeChild"]
    revision = child["revision"]
    receipts = state.get("decisionRefs")
    if type(receipts) is not list or not receipts:
        _fail("workflow-decisions-missing")
    completed = set()
    quality_passed = False
    quality_success = set()
    blocks = state.get("decisionBlocks", [])
    if type(blocks) is not list:
        _fail("workflow-quality-block-invalid")
    block_at, block_repairs, used_blocks = {}, {}, set()
    evidence_controller = SimpleNamespace(
        workspace=parent_root, control_dir=parent_root / ".img2threejs/route",
        record_root=parent_root / ".img2threejs/decisions")
    for block in blocks:
        if not callable(getattr(verify, "decision_block", None)):
            _fail("workflow-quality-block-verifier-required")
        verify.decision_block(block)
        if (block["receiptIndex"] in block_at or block["receiptIndex"] >= len(receipts)
                or canonical_digest(_blocked_quality(evidence_controller, block["context"]))
                != canonical_digest(block["assessment"])):
            _fail("workflow-quality-block-invalid")
        block_at[block["receiptIndex"]] = block
    for index, receipt in enumerate(receipts):
        if type(receipt) is not dict or receipt.get("step") not in _STEPS:
            _fail("workflow-receipt-invalid")
        context, record = receipt["context"], receipt["record"]
        subject = context["subject"]
        if (subject.get("runId") != child["runId"] or subject.get("objectId") != child["objectId"]
                or subject.get("revision") != revision):
            _fail("subject-mismatch")
        block = block_at.get(index)
        if block is not None:
            if (block["subject"] != subject or receipt["step"] != "repair-decision"
                    or canonical_digest(context["observations"].get("before"))
                    != canonical_digest(block["assessment"]["report"])):
                _fail("workflow-quality-block-unrepaired")
            original_inputs = {binding["role"]: binding["sha256"] for binding in block["context"]["bindings"]}
            repair_inputs = {binding["role"]: binding["sha256"] for binding in context["bindings"]}
            if any(original_inputs.get(role) != repair_inputs.get(role)
                   for role in ("reference", "spec", "source", "camera", "view-set")):
                _fail("workflow-quality-block-unrepaired")
            application = receipt.get("application", {})
            if application.get("status") != "completed" or application.get("outcome", {}).get("retained") is not True:
                _fail("workflow-quality-block-unrepaired")
            block_repairs[block["blockId"]] = record["recordId"]
        current = verify(record, context)
        if current["status"] != "answered":
            _fail("application-not-authorized")
        step = receipt["step"]
        expected_group = ("build-strategy" if step.startswith("strategy-") else
                          "quality-review" if step == "quality-review" else "repair")
        if context["group"] != expected_group:
            _fail("group-mismatch")
        if step.startswith("strategy-") or step == "repair-decision":
            application = receipt.get("application")
            if not callable(getattr(verify, "application", None)) or type(application) is not dict:
                _fail("application-verifier-required")
            if canonical_digest(_plain(verify.application(application["applicationId"]))) != canonical_digest(application):
                _fail("application-mismatch")
            if application["decisionRef"] != record["recordId"] or application["status"] not in {"completed", "rejected"}:
                _fail("application-not-authorized")
            if step == "repair-decision" and "qualityReview" in context["observations"]:
                _verify_semantic_receipt(verify, evidence_controller, receipt)
            if application["status"] == "completed":
                revision = application["trialRevision"]
                quality_passed = False
            if step.startswith("strategy-"):
                if application["status"] != "completed":
                    _fail("application-not-authorized")
                binding = receipt["outputBinding"]
                actual = _spec(_read_binding(parent_root, binding))
                selected = record["answers"]["selection"]["choice"]
                observations = context["observations"]
                proposals = observations.get("strategyProposalDigests", observations.get("nativeStrategyProposalDigests", {}))
                if proposals.get(selected) != canonical_digest(actual):
                    _fail("proposal-output-mismatch")
        else:
            assessment = _prepare(SimpleNamespace(
                workspace=parent_root, control_dir=parent_root / ".img2threejs/route",
                record_root=parent_root / ".img2threejs/decisions"), context)
            quality_passed = _quality_continues(context, current, assessment)
            if receipt.get("action") != ("continue" if quality_passed else "refine"):
                _fail("workflow-quality-mismatch")
            if quality_passed:
                quality_success.add(record["recordId"])
        completed.add(step)
    if (revision != lifecycle.get("candidateRevision") or lifecycle.get("incumbentRevision") != child["revision"]
            or not quality_passed or state.get("decisionPending") is not None):
        _fail("workflow-terminal-incomplete")
    if not {"strategy-spec", "strategy-build", "quality-review"} <= completed:
        _fail("workflow-decisions-missing")
    def check_rows(rows, pass_id=None, *, repaired_history=False):
        for row in rows:
            if row["id"] not in _STEPS:
                continue
            evidence = row.get("evidence", [])
            if row["id"] == "quality-review" and row["status"] == "skipped":
                matching = [block for block in blocks if block["blockId"] in evidence
                            and block["subject"]["passId"] == pass_id
                            and block["blockId"] in block_repairs]
                if (not repaired_history or len(matching) != 1
                        or row.get("reason") != matching[0]["reasonCode"]
                        or not any(other["id"] == "repair-decision" and other["status"] == "done"
                                   and block_repairs[matching[0]["blockId"]] in other.get("evidence", [])
                                   for other in rows)
                        or not any(receipt["step"] == "quality-review"
                                   and receipt["record"]["recordId"] in quality_success
                                   and receipt["context"]["subject"]["passId"] == pass_id
                                   and receipt["context"]["subject"]["revision"] > matching[0]["subject"]["revision"]
                                   for receipt in receipts)):
                    _fail("workflow-quality-block-unrepaired")
                used_blocks.add(matching[0]["blockId"])
                continue
            if row["id"] == "quality-review" and not repaired_history and pass_id is None:
                if not any(receipt["step"] == "quality-review"
                           and receipt["record"]["recordId"] in quality_success
                           and receipt["record"]["recordId"] in evidence
                           and receipt["context"]["subject"]["revision"] == revision for receipt in receipts):
                    _fail("workflow-terminal-incomplete")
            if row["id"] == "repair-decision" and row["status"] == "skipped":
                if not any(reference in quality_success for reference in evidence):
                    _fail("workflow-repair-skip-unproven")
                continue
            if row["status"] != "done" or not any(
                    receipt["step"] == row["id"] and receipt["record"]["recordId"] in evidence
                    and (pass_id is None or receipt["context"]["subject"]["passId"] == pass_id)
                    for receipt in receipts):
                _fail("workflow-decisions-missing")
    current_rows = [row for row in state["checklist"]
                    if row["scope"] != "pass" or state.get("currentPass") != "complete"]
    check_rows(current_rows)
    for history in state.get("passHistory", []):
        check_rows(history.get("checklist", []), history.get("passId"),
                   repaired_history=history.get("iteration") == "repair")
    if used_blocks != {block["blockId"] for block in blocks}:
        _fail("workflow-quality-block-history-missing")
    if state.get("currentPass") == "complete" and any(
            row["scope"] == "pass" and row["id"] == "strategy-build" for row in state["checklist"]):
        # A deleted history must not erase mandatory earlier sculpt passes.
        spec_path = relative + "/" + _relative(state["artifacts"]["spec"])
        spec = _spec(_read(parent_root, spec_path)[0])
        if _validate_base(spec) is not True:
            _fail("spec-validation-failed")
        for build_pass in spec["buildPasses"]:
            pass_id = build_pass["id"]
            if (not any(receipt["step"] == "strategy-build"
                        and receipt["context"]["subject"]["passId"] == pass_id for receipt in receipts)
                    or not any(receipt["record"]["recordId"] in quality_success
                               and receipt["context"]["subject"]["passId"] == pass_id for receipt in receipts)):
                _fail("workflow-pass-history-missing")
