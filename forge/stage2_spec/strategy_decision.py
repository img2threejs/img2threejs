"""Select bounded construction plans; never edit an incumbent or accept an artifact.

The integrator supplies program-owned context/scope and full proposed specs keyed
by candidate id. Only plan metadata and proposal digests reach the controller.
``scope`` is exactly the keyword contract of ``filter_plans``. Field paths are
exact dotted paths relative to existing ``componentTree`` entries, identified by
``id``; lists are atomic fields. Global fields and component membership are frozen.
The base independently computes ``source_operations`` (a subset of allowed
operations requiring external asset provenance); neither candidates nor providers
may decide that classification. Such operations require nonempty independently
authorized source hashes, each bound to actual evidence before consultation.
``validate_spec(spec)`` must run the existing spec/provenance validators and return
literal True. This hook is trusted code, never supplied by a provider.

This application boundary supports only base ``componentTree`` specs. Interior
0.3.1 native authoring/runtime fields and global CS2 route/material/camera edits
remain blocked. They require a separately authorized typed native-spec application
adapter; this module must not authorize them by widening its field/component checks.

Resume uses the SAME successor via begin_application and passes a program-owned
live ``consent=`` probe to consume, begin_application and complete_application;
another consultation/provider call is not required. Omitting it retains the
controller's same-process consultation callback, never manufactures a grant.
Uncertain results describe up to three plans for comparison, not worker authority:
the shared controller currently authorizes only answered decisions.
State/CLI integration, trial comparison and downstream evidence invalidation are
owned by the caller. Completion proves a scoped spec application, not quality or
deliverability. No plugin imports, provider discovery, or source writes occur here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import stat

from forge._shared.decision_control import DecisionError, canonical_digest
from forge._shared.plan_eligibility import filter_plans

_REQUIRED_ROLES = {"reference", "spec", "source", "critical-features"}
_PROTECTED = {"quality", "qualitycontract", "qualityfloor", "qualityfloors", "gate",
              "gates", "blocking", "blockinggates", "threshold", "thresholds",
              "policy", "policyversion", "criticalfeatures", "acceptance", "decisionrefs"}
_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")


def _fail(reason):
    raise DecisionError(reason)


def _protected(path):
    return any(part.lower().replace("_", "").replace("-", "") in _PROTECTED
               for part in path.split("."))


def _read_binding(workspace, binding):
    """Read the same verified regular file, without following directory symlinks."""
    if type(binding) is not dict or set(binding) != {"role", "path", "sha256"}:
        _fail("invalid-binding")
    relative = binding["path"]
    if (type(relative) is not str or not relative or "\\" in relative
            or PurePosixPath(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in relative.split("/"))):
        _fail("unsafe-path")
    directory = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = relative.split("/")
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                _fail("unsafe-path")
            data = source.read()
    except OSError as exc:
        raise DecisionError("evidence-missing") from exc
    finally:
        os.close(directory)
    if hashlib.sha256(data).hexdigest() != binding["sha256"]:
        _fail("stale-evidence")
    return data


def _spec(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("invalid-spec")
            result[key] = value
        return result
    try:
        value = json.loads(data, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise DecisionError("invalid-spec") from exc
    canonical_digest(value)
    return value


def _components(spec):
    if type(spec) is not dict or type(spec.get("componentTree")) is not list:
        _fail("invalid-spec")
    result = {}
    for component in spec["componentTree"]:
        if (type(component) is not dict or type(component.get("id")) is not str
                or not component["id"] or component["id"] in result):
            _fail("invalid-spec")
        result[component["id"]] = component
    return result


def _changes(before, after, prefix=""):
    """Report all changed fields, including additions/deletions and type changes."""
    if canonical_digest(before) == canonical_digest(after):
        return set()
    if type(before) is dict and type(after) is dict:
        changed = set()
        for key in before.keys() | after.keys():
            if "." in key:
                _fail("field-scope-violation")
            path = prefix + "." + key if prefix else key
            if key not in before or key not in after:
                changed.add(path)
            else:
                changed.update(_changes(before[key], after[key], path))
        return changed
    return {prefix}


def _protected_values(value, prefix=""):
    result = {}
    if type(value) is dict:
        for key, item in value.items():
            path = prefix + "." + key if prefix else key
            if _protected(key):
                result[path] = item
            else:
                result.update(_protected_values(item, path))
    elif type(value) is list:
        for index, item in enumerate(value):
            result.update(_protected_values(item, prefix + "." + str(index)))
    return result


def _delta(before, after, plan):
    canonical_digest(after)
    old, new = _components(before), _components(after)
    if canonical_digest(_protected_values(before)) != canonical_digest(_protected_values(after)):
        _fail("quality-boundary-violation")
    if old.keys() != new.keys():
        _fail("component-scope-violation")
    if _changes({k: v for k, v in before.items() if k != "componentTree"},
                {k: v for k, v in after.items() if k != "componentTree"}):
        _fail("field-scope-violation")
    # Ordering/hierarchy is not implicitly authorized by editing another field.
    if [c["id"] for c in before["componentTree"]] != [c["id"] for c in after["componentTree"]]:
        _fail("component-scope-violation")
    result = []
    for component_id in old:
        fields = _changes(old[component_id], new[component_id])
        if fields and component_id not in plan["components"]:
            _fail("component-scope-violation")
        if any(_protected(field) or field not in plan["fieldPaths"] for field in fields):
            _fail("field-scope-violation")
        result.extend({"componentId": component_id, "field": field} for field in sorted(fields))
    if not result:
        _fail("empty-plan-delta")
    return result


class StrategyDecision:
    """Program-owned strategy boundary around one shared decision snapshot."""

    def __init__(self, controller, context, *, scope, proposals, validate_spec):
        self.controller = controller
        self._context = copy.deepcopy(context)
        self._scope = copy.deepcopy(scope)
        self._proposals = copy.deepcopy(proposals)
        self._validate_spec = validate_spec
        self._applications = {}
        if (type(context) is not dict or context.get("group") != "build-strategy"
                or context.get("kind") != "img2.build-strategy-context"):
            _fail("group-mismatch")
        if not callable(validate_spec) or type(proposals) is not dict:
            _fail("invalid-strategy-input")
        bindings = context.get("bindings")
        if type(bindings) is not list or any(type(b) is not dict for b in bindings):
            _fail("evidence-missing")
        roles = [b.get("role") for b in bindings]
        if not _REQUIRED_ROLES <= set(roles) or any(roles.count(role) != 1 for role in _REQUIRED_ROLES):
            _fail("evidence-missing")
        self._spec_binding = copy.deepcopy(next(b for b in bindings if b["role"] == "spec"))
        self._source = _spec(_read_binding(controller.workspace, self._spec_binding))
        if type(context.get("subject")) is not dict or scope.get("pass_id") != context["subject"].get("passId"):
            _fail("subject-mismatch")
        try:
            filtered = filter_plans(context.get("candidates"), **self._scope)
        except (TypeError, ValueError) as exc:
            raise DecisionError("invalid-strategy-scope") from exc
        self._rejected = filtered["rejected"]
        eligible = []
        binding_hashes = {binding.get("sha256") for binding in bindings}
        for plan in filtered["eligible"]:
            if (not plan["fieldPaths"] or any(not _FIELD.fullmatch(field) or _protected(field)
                                             for field in plan["fieldPaths"])):
                self._rejected.append({"id": plan["id"], "reasons": ["field-scope-violation"]})
                continue
            if not set(plan["materialSources"]) <= binding_hashes:
                self._rejected.append({"id": plan["id"], "reasons": ["material-evidence-missing"]})
                continue
            if plan["id"] not in self._proposals:
                _fail("proposal-missing")
            try:
                _delta(self._source, self._proposals[plan["id"]], plan)
                self._validate(self._proposals[plan["id"]])
            except DecisionError as exc:
                self._rejected.append({"id": plan["id"], "reasons": [exc.reason_code]})
                continue
            eligible.append(plan)
        if not eligible:
            _fail("no-admissible-plans")
        self._context["candidates"] = eligible
        observations = self._context.get("observations")
        if type(observations) is not dict:
            _fail("invalid-context")
        if "strategyScopeDigest" in observations or "strategyProposalDigests" in observations:
            _fail("invalid-strategy-input")
        observations["strategyScopeDigest"] = canonical_digest({
            key: sorted(value) if type(value) in (set, frozenset) else value
            for key, value in self._scope.items()})
        observations["strategyProposalDigests"] = {
            plan["id"]: canonical_digest(self._proposals[plan["id"]]) for plan in eligible}

    def _validate(self, spec):
        if self._validate_spec(copy.deepcopy(spec)) is not True:
            _fail("spec-validation-failed")

    def consult(self, *, provider=None, fallback=None, availability=None, consent=None,
                request_token_limit=64000, max_trials=0):
        """Consult Jev first; max_trials is trusted feasibility, never model data."""
        if type(max_trials) is not int or not 0 <= max_trials <= 3:
            _fail("invalid-trial-limit")
        record = self.controller.consult(
            self._context, provider=provider, fallback=fallback, availability=availability,
            consent=consent, request_token_limit=request_token_limit)
        record = self.controller.consume(record, self._context, consent=consent)
        status = record["status"]
        ids = []
        if status == "answered":
            ids = [record["answers"]["selection"]["choice"]]
            action = "selected"
        elif status == "uncertain" and max_trials >= 2:
            probabilities = record["answers"]["selection"]["probabilities"]
            ids = sorted(probabilities, key=lambda key: (-probabilities[key], key))[:max_trials]
            action = "compare-plans"
        else:
            action = "request-input"
        return {"action": action, "planIds": ids, "record": record,
                "rejected": copy.deepcopy(self._rejected)}

    def consume(self, record, *, consent=None):
        """Recheck this snapshot and live consent; fresh processes supply a probe."""
        record = self.controller.consume(record, self._context, consent=consent)
        if record["status"] != "answered":
            _fail("application-not-authorized")
        selected = record["answers"]["selection"]["choice"]
        return copy.deepcopy(next(plan for plan in self._context["candidates"] if plan["id"] == selected))

    def begin_application(self, record, trial_revision, *, consent=None):
        """Persist selection before handing the worker its sole successor/spec."""
        plan = self.consume(record, consent=consent)
        proposed = self._proposals[plan["id"]]
        _delta(self._source, proposed, plan)
        self._validate(proposed)
        application = self.controller.begin_application(record, trial_revision, consent=consent)
        self._applications[application["applicationId"]] = copy.deepcopy(application)
        return {"application": application, "plan": plan, "spec": copy.deepcopy(proposed)}

    def complete_application(self, record, application, output_binding, *, consent=None):
        """Compare actual bytes/fields/components before persisting completion.

        A fresh process must explicitly resume begin_application first, supplying
        the program-owned live consent probe to both actions. The output file
        must be separate from every input. No source is ever written here.
        """
        plan = self.consume(record, consent=consent)
        if type(application) is not dict:
            _fail("application-not-authorized")
        saved = self._applications.get(application.get("applicationId"))
        if saved is None or saved != application or application["decisionRef"] != record["recordId"]:
            _fail("application-not-authorized")
        if (type(output_binding) is not dict or output_binding.get("role") != "spec"
                or output_binding.get("path") in {b["path"] for b in self._context["bindings"]}):
            _fail("invalid-successor-output")
        actual = _spec(_read_binding(self.controller.workspace, output_binding))
        proposed = self._proposals[plan["id"]]
        actual_delta = _delta(self._source, actual, plan)
        proposed_delta = _delta(self._source, proposed, plan)
        if actual_delta != proposed_delta or canonical_digest(actual) != canonical_digest(proposed):
            _fail("proposal-output-mismatch")
        self._validate(actual)
        # Recheck input hashes and consent after the external validator returns.
        self.consume(record, consent=consent)
        _read_binding(self.controller.workspace, output_binding)
        return self.controller.finish_application(application["applicationId"], {
            "status": "completed", "planId": plan["id"], "specSha256": output_binding["sha256"],
            "specDigest": canonical_digest(actual), "changes": actual_delta}, consent=consent)
