"""A separate, fail-closed strategy boundary for native JSON specs.

The old componentTree StrategyDecision is deliberately unchanged. The integrator
supplies the exact filter_plans scope, concrete full successors, native validator,
and authenticated, program-owned native_rules (never model/provider data). Each
rule is {fieldPath, pointer, ownerId, componentId, passId}. fieldPath is a dotted
controlled alias in the unchanged plan schema; pointer is an exact RFC6901 path
kept LOCAL, since provider contexts forbid absolute-path strings. Rules grant
one exact changed field, never descendants, wildcards, commands or an owner.
Root/global fields belong to subject.objectId; nested id/componentId and ownerId
markers establish narrower ownership. Container membership/identity is immutable.

Required provenance and permissions evidence supplements the ordinary reference,
spec, source and critical-features bindings. authorize_native(context, rules) must
independently authenticate those upstream records, verify immutable ownership,
source rights, current unlocked pass and live permissions, and return literal True.
It runs before consultation and inside each controller consent check, including
resume/finish. Hashes alone never authenticate permissions. frozen_paths contains
program-owned JSON pointers for locked native passes/foreign subtrees; explicit
locked=True/unlocked=False subtrees are additionally frozen automatically.

validate_spec(spec) must run the actual native schema/provenance validator and
return literal True. A missing validator/authority fails closed; there is no
fallback to the base spec validator or arbitrary native writer. Both original and
full concrete successors are hashed before consultation. This module only grants
one durable successor and checks the external writer's actual output, never
writes a spec, imports plugins, runs Blender, or declares any native quality gate
passed. The caller owns writer integration and downstream evidence invalidation.
"""
from __future__ import annotations

import copy
import re

from forge._shared.decision_control import DecisionError, canonical_digest
from forge._shared.plan_eligibility import filter_plans
from forge.stage2_spec.strategy_decision import _read_binding, _spec

_REQUIRED_ROLES = {"reference", "spec", "source", "critical-features", "provenance", "permissions"}
_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
_PROTECTED = {
    "quality", "qualitycontract", "qualityfloor", "qualityfloors", "gate", "gates",
    "blocking", "blockinggates", "threshold", "thresholds", "policy", "policyversion",
    "criticalfeatures", "acceptance", "decision", "decisions", "decisionrefs",
    "permissions", "provenance", "id", "ownerid", "componentid", "passid", "locked",
    "unlocked", "command", "commands", "argv", "code", "script", "scripts",
    "instructions", "executable", "shell",
    "lock", "locks", "passlock", "passlocks", "lockedpass", "lockedpasses",
    "unlockedpasses", "passunlocked",
}
_RULE_KEYS = {"fieldPath", "pointer", "ownerId", "componentId", "passId"}


def _fail(reason):
    raise DecisionError(reason)


def _normal(key):
    return key.lower().replace("_", "").replace("-", "")


def _protected(key):
    key = _normal(key)
    return (key in _PROTECTED or key.startswith(("quality", "policy", "gate", "threshold", "decision"))
            or key.endswith(("policy", "policies", "gate", "gates", "threshold", "thresholds", "decision", "decisions")))


def _pointer(value, *, root=False):
    if value == "" and root:
        return ()
    if (type(value) is not str or not value.startswith("/")
            or any(char in value for char in "*?[]") or re.search(r"~(?![01])", value)):
        _fail("unsupported-native-pointer")
    parts = tuple(part.replace("~1", "/").replace("~0", "~") for part in value[1:].split("/"))
    if any(part in {"", "-"} for part in parts):
        _fail("unsupported-native-pointer")
    return parts


def _get(value, parts):
    for part in parts:
        if type(value) is dict and part in value:
            value = value[part]
        elif type(value) is list and part.isascii() and part.isdigit() and str(int(part)) == part and int(part) < len(value):
            value = value[int(part)]
        else:
            _fail("native-path-unresolvable")
    return value


def _ownership(spec, parts, object_id):
    owner, component = object_id, object_id
    value = spec
    # The changed value is not itself an ownership container.
    for depth in range(len(parts)):
        if type(value) is dict:
            owner = value.get("ownerId", owner)
            component = value.get("componentId", value.get("id", component))
        value = _get(value, (parts[depth],))
    return owner, component


def _protected_values(value, object_id, forbidden, prefix=()):
    result = []
    if type(value) is dict:
        for key in ("id", "componentId", "ownerId"):
            if key in value and (type(value[key]) is not str or not value[key]):
                _fail("invalid-native-identity")
        if (value.get("locked") is True or value.get("unlocked") is False
                or value.get("ownerId", object_id) != object_id
                or value.get("componentId", value.get("id")) in forbidden):
            return [[list(prefix), value]]
        for key, item in sorted(value.items()):
            path = prefix + (key,)
            if _protected(key):
                result.append([list(path), item])
            else:
                result.extend(_protected_values(item, object_id, forbidden, path))
    elif type(value) is list:
        for index, item in enumerate(value):
            result.extend(_protected_values(item, object_id, forbidden, prefix + (str(index),)))
    return result


def _changes(before, after, prefix=()):
    if canonical_digest(before) == canonical_digest(after):
        return set()
    if type(before) is dict and type(after) is dict:
        result = set()
        for key in before.keys() | after.keys():
            path = prefix + (key,)
            if key not in before or key not in after:
                result.add(path)
            else:
                result.update(_changes(before[key], after[key], path))
        return result
    if type(before) is list and type(after) is list and any(
            type(item) in (dict, list) for item in before + after):
        if len(before) != len(after):
            _fail("native-container-membership-change")
        result = set()
        for index, (old, new) in enumerate(zip(before, after)):
            result.update(_changes(old, new, prefix + (str(index),)))
        return result
    # Scalar arrays, e.g. camera positions, are exact atomic fields.
    return {prefix}


class NativeStrategyDecision:
    """Consult on native plans without weakening the existing base adapter."""

    def __init__(self, controller, context, *, scope, proposals, native_rules,
                 frozen_paths, forbidden_components, authorize_native, validate_spec):
        if (type(context) is not dict or context.get("group") != "build-strategy"
                or context.get("kind") != "img2.build-strategy-context"):
            _fail("group-mismatch")
        if not callable(validate_spec):
            _fail("native-validator-unavailable")
        if not callable(authorize_native):
            _fail("native-authority-unavailable")
        if (type(scope) is not dict or type(proposals) is not dict
                or type(native_rules) is not list or not native_rules
                or type(frozen_paths) not in (set, frozenset)
                or type(forbidden_components) not in (set, frozenset)
                or any(type(item) is not str for item in frozen_paths)
                or any(type(item) is not str or not item for item in forbidden_components)):
            _fail("invalid-native-strategy-input")
        self.controller = controller
        self._context = copy.deepcopy(context)
        self._scope = copy.deepcopy(scope)
        self._proposals = copy.deepcopy(proposals)
        self._rules = copy.deepcopy(native_rules)
        self._authorize = authorize_native
        self._validator = validate_spec
        self._forbidden = frozenset(forbidden_components)
        self._frozen = tuple(_pointer(path, root=True) for path in sorted(frozen_paths))
        self._applications = {}
        subject = context.get("subject")
        if (type(subject) is not dict or type(subject.get("objectId")) is not str
                or scope.get("pass_id") != subject.get("passId")):
            _fail("subject-mismatch")
        self._object_id = subject["objectId"]
        bindings = context.get("bindings")
        if type(bindings) is not list or any(type(binding) is not dict for binding in bindings):
            _fail("evidence-missing")
        roles = [binding.get("role") for binding in bindings]
        if not _REQUIRED_ROLES <= set(roles) or any(roles.count(role) != 1 for role in _REQUIRED_ROLES):
            _fail("evidence-missing")
        for binding in bindings:
            _read_binding(controller.workspace, binding)
        spec_binding = next(binding for binding in bindings if binding["role"] == "spec")
        self._source = _spec(_read_binding(controller.workspace, spec_binding))
        if type(self._source) is not dict:
            _fail("invalid-native-spec")
        for path in self._frozen:
            _get(self._source, path)
        self._by_field = {}
        pointers = set()
        for rule in self._rules:
            if (type(rule) is not dict or set(rule) != _RULE_KEYS
                    or any(type(item) is not str or not item for item in rule.values())
                    or not _FIELD.fullmatch(rule["fieldPath"])):
                _fail("invalid-native-rule")
            parts = _pointer(rule["pointer"])
            if rule["fieldPath"] in self._by_field or parts in pointers:
                _fail("duplicate-native-rule")
            if rule["ownerId"] != self._object_id:
                _fail("native-owner-mismatch")
            if rule["passId"] != scope.get("pass_id"):
                _fail("locked-or-wrong-pass")
            if rule["componentId"] in self._forbidden:
                _fail("forbidden-native-component")
            # Resolve now: unsupported native writers cannot acquire authority by
            # inventing an owner, a new component or a nonexistent field.
            _get(self._source, parts)
            if _ownership(self._source, parts, self._object_id) != (rule["ownerId"], rule["componentId"]):
                _fail("native-component-mismatch")
            pointers.add(parts)
            self._by_field[rule["fieldPath"]] = (parts, rule)
        self._verify_upstream(bindings, frozen_paths)
        self._fresh()
        self._validate(self._source)
        try:
            filtered = filter_plans(context.get("candidates"), **self._scope)
        except (TypeError, ValueError) as exc:
            raise DecisionError("invalid-strategy-scope") from exc
        self._rejected = filtered["rejected"]
        eligible = []
        hashes = {binding["sha256"] for binding in bindings}
        for plan in filtered["eligible"]:
            try:
                if not plan["fieldPaths"] or any(field not in self._by_field for field in plan["fieldPaths"]):
                    _fail("native-path-not-granted")
                if not set(plan["materialSources"]) <= hashes:
                    _fail("material-evidence-missing")
                if plan["id"] not in self._proposals:
                    _fail("proposal-missing")
                self._delta(self._proposals[plan["id"]], plan)
                self._validate(self._proposals[plan["id"]])
            except DecisionError as exc:
                self._rejected.append({"id": plan["id"], "reasons": [exc.reason_code]})
                continue
            eligible.append(plan)
        if not eligible:
            raise DecisionError("no-admissible-plans", "no-admissible-plans: " + repr(self._rejected))
        self._context["candidates"] = eligible
        observations = self._context.get("observations")
        reserved = {"nativeStrategyScopeDigest", "nativeStrategyOriginalDigest", "nativeStrategyProposalDigests"}
        if type(observations) is not dict or reserved & observations.keys():
            _fail("invalid-native-strategy-input")
        observations["nativeStrategyScopeDigest"] = canonical_digest({
            "scope": {key: sorted(value) if type(value) in (set, frozenset) else value
                      for key, value in self._scope.items()},
            "rules": sorted(self._rules, key=lambda rule: rule["fieldPath"]),
            "frozen": sorted(frozen_paths), "forbiddenComponents": sorted(forbidden_components)})
        observations["nativeStrategyOriginalDigest"] = canonical_digest(self._source)
        observations["nativeStrategyProposalDigests"] = {
            plan["id"]: canonical_digest(self._proposals[plan["id"]]) for plan in eligible}
        self._fresh()

    def _verify_upstream(self, bindings, frozen_paths):
        """Require concrete bound records, not a callback's missing-evidence claim.

        Authentication and live revocation remain the trusted authority's job;
        these exact records independently bind its permission to these inputs.
        """
        by_role = {binding["role"]: binding for binding in bindings}
        provenance = _spec(_read_binding(self.controller.workspace, by_role["provenance"]))
        expected = {
            "kind": "img2.native-strategy-provenance", "version": 1,
            "subject": self._context["subject"],
            "specSha256": by_role["spec"]["sha256"],
            "sourceSha256": by_role["source"]["sha256"],
            "owners": [{key: rule[key] for key in ("pointer", "ownerId", "componentId")}
                       for rule in sorted(self._rules, key=lambda rule: rule["fieldPath"])]}
        if canonical_digest(provenance) != canonical_digest(expected):
            _fail("native-provenance-mismatch")
        permissions = _spec(_read_binding(self.controller.workspace, by_role["permissions"]))
        expected = {
            "kind": "img2.native-strategy-permissions", "version": 1,
            "subject": self._context["subject"],
            "provenanceSha256": by_role["provenance"]["sha256"],
            "scopeDigest": canonical_digest({
                key: sorted(value) if type(value) in (set, frozenset) else value
                for key, value in self._scope.items()}),
            "rules": sorted(self._rules, key=lambda rule: rule["fieldPath"]),
            "frozenPaths": sorted(frozen_paths),
            "forbiddenComponents": sorted(self._forbidden)}
        if canonical_digest(permissions) != canonical_digest(expected):
            _fail("native-permissions-mismatch")

    def _fresh(self):
        for binding in self._context["bindings"]:
            _read_binding(self.controller.workspace, binding)
        try:
            allowed = self._authorize(copy.deepcopy(self._context), copy.deepcopy(self._rules))
        except DecisionError:
            raise
        except Exception as exc:
            raise DecisionError("native-authority-unavailable") from exc
        if allowed is not True:
            _fail("native-permission-revoked")

    def _consent(self, consent):
        # Never inherit historical consent, even in the consulting process.
        if not callable(consent):
            _fail("consent-unproven")
        def live():
            self._fresh()
            return consent()
        return live

    def _validate(self, spec):
        try:
            valid = self._validator(copy.deepcopy(spec))
        except DecisionError:
            raise
        except Exception as exc:
            raise DecisionError("native-validation-failed") from exc
        if valid is not True:
            _fail("native-validation-failed")

    def _delta(self, after, plan):
        canonical_digest(after)
        if type(after) is not dict:
            _fail("invalid-native-spec")
        if canonical_digest(_protected_values(self._source, self._object_id, self._forbidden)) != canonical_digest(
                _protected_values(after, self._object_id, self._forbidden)):
            _fail("native-protected-field-change")
        for parts in self._frozen:
            if canonical_digest(_get(self._source, parts)) != canonical_digest(_get(after, parts)):
                _fail("native-locked-path-change")
        changed = _changes(self._source, after)
        grants = {self._by_field[field][0]: self._by_field[field][1] for field in plan["fieldPaths"]}
        result = []
        for parts in sorted(changed):
            rule = grants.get(parts)
            if rule is None:
                _fail("native-path-not-granted")
            owner, component = _ownership(after, parts, self._object_id)
            if (owner != rule["ownerId"] or component != rule["componentId"]
                    or component not in plan["components"] or component in self._forbidden):
                _fail("native-component-mismatch")
            result.append({"componentId": component, "field": rule["fieldPath"]})
        if not result:
            _fail("empty-plan-delta")
        return result

    def consult(self, *, provider=None, fallback=None, availability=None, consent=None,
                request_token_limit=64000, max_trials=0):
        if type(max_trials) is not int or not 0 <= max_trials <= 3:
            _fail("invalid-trial-limit")
        live = self._consent(consent)
        self._fresh()
        record = self.controller.consult(self._context, provider=provider, fallback=fallback,
            availability=availability, consent=live, request_token_limit=request_token_limit)
        record = self.controller.consume(record, self._context, consent=live)
        ids = []
        if record["status"] == "answered":
            ids = [record["answers"]["selection"]["choice"]]
            action = "selected"
        elif record["status"] == "uncertain" and max_trials >= 2:
            probabilities = record["answers"]["selection"]["probabilities"]
            ids = sorted(probabilities, key=lambda key: (-probabilities[key], key))[:max_trials]
            action = "compare-plans"
        else:
            action = "request-input"
        return {"action": action, "planIds": ids, "record": record,
                "rejected": copy.deepcopy(self._rejected)}

    def consume(self, record, *, consent=None):
        self._fresh()
        record = self.controller.consume(record, self._context, consent=self._consent(consent))
        if record["status"] != "answered":
            _fail("application-not-authorized")
        selected = record["answers"]["selection"]["choice"]
        return copy.deepcopy(next(plan for plan in self._context["candidates"] if plan["id"] == selected))

    def begin_application(self, record, trial_revision, *, consent=None):
        plan = self.consume(record, consent=consent)
        proposed = self._proposals[plan["id"]]
        self._delta(proposed, plan)
        self._validate(proposed)
        application = self.controller.begin_application(record, trial_revision, consent=self._consent(consent))
        self._applications[application["applicationId"]] = copy.deepcopy(application)
        return {"application": application, "plan": plan, "spec": copy.deepcopy(proposed)}

    def complete_application(self, record, application, output_binding, *, consent=None):
        """Require explicit begin/resume; compare complete actual successor bytes."""
        plan = self.consume(record, consent=consent)
        if type(application) is not dict:
            _fail("application-not-authorized")
        saved = self._applications.get(application.get("applicationId"))
        if saved is None or saved != application or application["decisionRef"] != record["recordId"]:
            _fail("application-not-authorized")
        if (type(output_binding) is not dict or output_binding.get("role") != "spec"
                or output_binding.get("path") in {binding["path"] for binding in self._context["bindings"]}):
            _fail("invalid-successor-output")
        actual = _spec(_read_binding(self.controller.workspace, output_binding))
        proposed = self._proposals[plan["id"]]
        changes = self._delta(actual, plan)
        if changes != self._delta(proposed, plan) or canonical_digest(actual) != canonical_digest(proposed):
            _fail("proposal-output-mismatch")
        self._validate(actual)
        self.consume(record, consent=consent)
        _read_binding(self.controller.workspace, output_binding)
        return self.controller.finish_application(application["applicationId"], {
            "status": "completed", "planId": plan["id"], "specSha256": output_binding["sha256"],
            "specDigest": canonical_digest(actual), "changes": changes}, consent=self._consent(consent))
