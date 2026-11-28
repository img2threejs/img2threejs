"""Offline native JSON authority probes, not a native/Blender quality gate."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from forge._shared.decision_control import DecisionController, DecisionError, canonical_digest
from forge.stage2_spec.native_strategy_decision import NativeStrategyDecision


def resume_probe(payload):
    """Run only by the disposable fresh-process fixture; never consult a provider."""
    scope = payload["scope"]
    for key in ("components", "operations", "field_paths", "authorized_sources", "source_operations", "available_dependencies"):
        scope[key] = set(scope[key])
    controller = DecisionController(payload["workspace"], "run-1", clock=lambda: 1000)
    stage = NativeStrategyDecision(controller, payload["context"], scope=scope,
        proposals=payload["proposals"], native_rules=payload["rules"],
        frozen_paths=set(payload["frozen"]), forbidden_components=set(payload["forbidden"]),
        authorize_native=lambda context, rules: True, validate_spec=lambda spec: True)
    record = payload["record"]
    try:
        stage.begin_application(record, 2)
    except DecisionError as exc:
        missing_consent = exc.reason_code
    else:
        raise AssertionError("resume inherited historical consent")
    consent = lambda: copy.deepcopy(payload["grant"])
    trial = stage.begin_application(record, 2, consent=consent)
    try:
        stage.begin_application(record, 3, consent=consent)
    except DecisionError as exc:
        conflict = exc.reason_code
    else:
        raise AssertionError("resume authorized a second successor")
    completed = stage.complete_application(record, trial["application"], payload["output"], consent=consent)
    return {"missingConsent": missing_consent, "conflict": conflict, "completed": completed,
            "sameApplication": trial["application"] == payload["application"]}


class NativeStrategyDecisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.now = 1000
        self.controller = DecisionController(self.workspace, "run-1", clock=lambda: self.now)
        self.source = {
            "schemaVersion": "scene-spec1", "ownerId": "room",
            "camera": {"fov": 45, "position": [1, 2, 3]},
            "authoring": {"runtime": {"samples": 32}, "geometry": {"locked": True, "segments": 64}},
            "qualityContract": {"threshold": 0.8, "blockingGates": ["volume"]},
            "parts": [{"id": "chair", "width": 1}, {"id": "foreign", "width": 2}],
            "external": {"ownerId": "other-room", "width": 1},
        }
        self.context = {
            "kind": "img2.build-strategy-context", "version": 1, "group": "build-strategy",
            "subject": {"runId": "run-1", "objectId": "room", "revision": 1, "passId": "camera"},
            "bindings": [self.binding("reference", "reference.png", b"offline reference"),
                self.binding("spec", "scene.json", json.dumps(self.source).encode()),
                self.binding("source", "source.py", b"offline native source"),
                self.binding("critical-features", "critical.json", b'{"chair":true}')],
            "candidates": [self.plan("a"), self.plan("b")], "observations": {},
            "requirements": {"criticalFeatures": [{"id": "chair"}], "requiredViews": []},
            "policyVersion": "policy-v1", "questionSetVersion": "strategy-questions-v1",
            "requestedModel": "jev-1.14.0"}
        self.scope = {"pass_id": "camera", "pass_unlocked": True, "components": {"room"},
            "operations": {"set-camera", "set-runtime"}, "field_paths": {"camera.fov", "authoring.runtime.samples"},
            "authorized_sources": set(), "source_operations": set(), "available_dependencies": {"native-writer"},
            "rig_required": False, "rig_available": False}
        self.rules = [self.rule("camera.fov", "/camera/fov"),
                      self.rule("authoring.runtime.samples", "/authoring/runtime/samples")]
        self.frozen = {"/authoring/geometry"}
        self.forbidden = {"foreign"}
        self.proposals = {"a": self.changed(50, 64), "b": self.changed(55, 128)}
        self.validator = Mock(return_value=True)
        self.permission_live = True
        self.authority = Mock(side_effect=lambda context, rules: self.permission_live)
        self.grant = {"granted": True, "purposes": ["routing", "build-strategy", "quality-review", "repair"],
                      "sha256": "c" * 64, "tokenBudget": 10000}
        self.consent = lambda: copy.deepcopy(self.grant)
        self.provider = Mock(side_effect=self.advisory)
        self.fallback = Mock(side_effect=lambda context: self.advisory(context, model="host-model"))
        self.options = {"provider": self.provider, "fallback": self.fallback,
                        "availability": lambda: {"status": "present"}, "consent": self.consent,
                        "request_token_limit": 128}
        self.bind_authority()

    @staticmethod
    def rule(field, pointer, component="room", owner="room"):
        return {"fieldPath": field, "pointer": pointer, "ownerId": owner,
                "componentId": component, "passId": "camera"}

    @staticmethod
    def plan(ident):
        return {"id": ident, "passId": "camera", "components": ["room"],
                "operations": ["set-camera", "set-runtime"],
                "fieldPaths": ["camera.fov", "authoring.runtime.samples"],
                "materialSources": [], "dependencies": ["native-writer"], "rigHandoff": False}

    @staticmethod
    def advisory(context, *, probability=0.9, model="jev-1.14.0"):
        ids = [candidate["id"] for candidate in context["candidates"]]
        probabilities = {ident: (1 - probability) / (len(ids) - 1) if len(ids) > 1 else 1 for ident in ids}
        probabilities[ids[0]] = probability if len(ids) > 1 else 1
        return {"kind": "img2.decision-advisory", "version": 1, "group": context["group"],
                "subject": copy.deepcopy(context["subject"]), "contextDigest": canonical_digest(context),
                "model": model, "answers": {"selection": {"type": "choice", "choice": ids[0],
                    "probabilities": probabilities, "confidence": 0.9}, "anyFits": {"type": "noul", "noul": 1}},
                "usage": {"input_tokens": 10, "output_tokens": 2}}

    def binding(self, role, path, data):
        target = self.workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"role": role, "path": path, "sha256": hashlib.sha256(data).hexdigest()}

    def bind_authority(self):
        self.context["bindings"] = [binding for binding in self.context["bindings"]
                                    if binding["role"] not in {"provenance", "permissions"}]
        by_role = {binding["role"]: binding for binding in self.context["bindings"]}
        rules = sorted(self.rules, key=lambda rule: rule["fieldPath"])
        provenance = {"kind": "img2.native-strategy-provenance", "version": 1,
            "subject": self.context["subject"], "specSha256": by_role["spec"]["sha256"],
            "sourceSha256": by_role["source"]["sha256"],
            "owners": [{key: rule[key] for key in ("pointer", "ownerId", "componentId")} for rule in rules]}
        binding = self.binding("provenance", "provenance.json", json.dumps(provenance).encode())
        self.context["bindings"].append(binding)
        permissions = {"kind": "img2.native-strategy-permissions", "version": 1,
            "subject": self.context["subject"], "provenanceSha256": binding["sha256"],
            "scopeDigest": canonical_digest({key: sorted(value) if type(value) is set else value
                                             for key, value in self.scope.items()}),
            "rules": rules, "frozenPaths": sorted(self.frozen), "forbiddenComponents": sorted(self.forbidden)}
        self.context["bindings"].append(self.binding("permissions", "permissions.json", json.dumps(permissions).encode()))

    def changed(self, fov, samples):
        spec = copy.deepcopy(self.source)
        spec["camera"]["fov"] = fov
        spec["authoring"]["runtime"]["samples"] = samples
        return spec

    def stage(self, **overrides):
        options = {"scope": self.scope, "proposals": self.proposals, "native_rules": self.rules,
            "frozen_paths": self.frozen, "forbidden_components": self.forbidden,
            "authorize_native": self.authority, "validate_spec": self.validator}
        options.update(overrides)
        return NativeStrategyDecision(self.controller, self.context, **options)

    def assert_reason(self, reason, function, *args, **kwargs):
        with self.assertRaises(DecisionError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.reason_code, reason)
        return caught.exception

    def selected(self):
        stage = self.stage()
        result = stage.consult(**self.options)
        self.assertEqual(result["action"], "selected")
        return stage, result["record"]

    def output(self, spec):
        return self.binding("spec", "trial-2/scene.json", json.dumps(spec).encode())

    def test_native_camera_authoring_exact_grants_prehashed_before_provider(self):
        original = (self.workspace / "scene.json").read_bytes()
        stage, record = self.selected()
        request = self.provider.call_args.args[0]
        self.assertEqual(request["observations"]["nativeStrategyOriginalDigest"], canonical_digest(self.source))
        self.assertEqual(request["observations"]["nativeStrategyProposalDigests"],
                         {key: canonical_digest(value) for key, value in self.proposals.items()})
        self.assertNotIn("/camera/fov", json.dumps(request))
        self.assertTrue(all("path" not in binding for binding in request["bindings"]))
        started = stage.begin_application(record, 2, consent=self.consent)
        self.assertEqual(started["spec"], self.proposals["a"])
        finished = stage.complete_application(record, started["application"], self.output(started["spec"]), consent=self.consent)
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["outcome"]["specDigest"], canonical_digest(self.proposals["a"]))
        self.assertEqual(finished["outcome"]["changes"], [
            {"componentId": "room", "field": "authoring.runtime.samples"},
            {"componentId": "room", "field": "camera.fov"}])
        self.assertEqual((self.workspace / "scene.json").read_bytes(), original)
        self.fallback.assert_not_called()

    def test_allowlist_alone_cannot_grant_missing_exact_path(self):
        self.rules = self.rules[:1]
        self.bind_authority()
        error = self.assert_reason("no-admissible-plans", self.stage)
        self.assertIn("native-path-not-granted", str(error))
        self.provider.assert_not_called()

    def test_global_quality_remains_protected_even_when_explicitly_granted(self):
        for field, pointer, mutate in (
            ("qualityContract.threshold", "/qualityContract/threshold", lambda spec: spec["qualityContract"].update(threshold=0)),
            ("qualityContract.blockingGates", "/qualityContract/blockingGates", lambda spec: spec["qualityContract"].update(blockingGates=[]))):
            with self.subTest(field=field):
                self.rules = [self.rule(field, pointer)]
                self.scope["field_paths"] = {field}
                for plan in self.context["candidates"]:
                    plan["fieldPaths"] = [field]
                self.proposals = {"a": copy.deepcopy(self.source), "b": copy.deepcopy(self.source)}
                for proposal in self.proposals.values():
                    mutate(proposal)
                self.bind_authority()
                error = self.assert_reason("no-admissible-plans", self.stage)
                self.assertIn("native-protected-field-change", str(error))
        self.provider.assert_not_called()

    def test_recursive_protected_fields_cannot_hide_under_granted_scalar_or_array(self):
        for hidden in ({"qualityContract": {"threshold": 0}}, [{"policy": "disabled"}],
                       {"decisions": []}, {"command": "run-arbitrary-code"}, {"gate": False},
                       {"renderThreshold": 0}, {"subtree": [{"decisionRefs": []}]}):
            with self.subTest(hidden=hidden):
                for proposal in self.proposals.values():
                    proposal["camera"]["fov"] = copy.deepcopy(hidden)
                error = self.assert_reason("no-admissible-plans", self.stage)
                self.assertIn("native-protected-field-change", str(error))
        self.provider.assert_not_called()

    def test_wrong_owner_component_and_forbidden_component_grants_fail_precisely(self):
        for rule, reason in (
                (self.rule("camera.fov", "/camera/fov", owner="other-room"), "native-owner-mismatch"),
                (self.rule("camera.fov", "/camera/fov", component="chair"), "native-component-mismatch"),
                (self.rule("part.width", "/parts/1/width", component="foreign"), "forbidden-native-component"),
                (self.rule("external.width", "/external/width"), "native-component-mismatch")):
            with self.subTest(reason=reason):
                self.rules = [rule]
                self.bind_authority()
                self.assert_reason(reason, self.stage)
        self.provider.assert_not_called()

    def test_component_local_exact_rule_and_plan_component_are_both_required(self):
        self.rules = [self.rule("chair.width", "/parts/0/width", component="chair")]
        self.scope.update(components={"chair"}, field_paths={"chair.width"})
        for plan in self.context["candidates"]:
            plan.update(components=["chair"], fieldPaths=["chair.width"])
        for proposal in self.proposals.values():
            proposal.update(camera=copy.deepcopy(self.source["camera"]), authoring=copy.deepcopy(self.source["authoring"]))
            proposal["parts"][0]["width"] = 3
        self.bind_authority()
        self.selected()
        for plan in self.context["candidates"]:
            plan["components"] = ["room"]
        self.scope["components"].add("room")
        self.bind_authority()
        self.assert_reason("no-admissible-plans", self.stage)

    def test_unrelated_global_component_or_container_change_is_rejected(self):
        for mutate in (lambda spec: spec["camera"].update(position=[9, 9, 9]),
                       lambda spec: spec["parts"][0].update(width=10),
                       lambda spec: spec["parts"][1].update(width=10),
                       lambda spec: spec["external"].update(width=10),
                       lambda spec: spec["parts"].reverse(),
                       lambda spec: spec["parts"].append({"id": "new", "width": 2})):
            with self.subTest(mutate=mutate):
                self.proposals = {"a": self.changed(50, 64), "b": self.changed(55, 128)}
                for proposal in self.proposals.values():
                    mutate(proposal)
                self.assert_reason("no-admissible-plans", self.stage)
        self.provider.assert_not_called()

    def test_explicit_and_intrinsic_locked_passes_cannot_be_changed(self):
        self.rules.append(self.rule("authoring.geometry.segments", "/authoring/geometry/segments"))
        self.scope["field_paths"].add("authoring.geometry.segments")
        for plan in self.context["candidates"]:
            plan["fieldPaths"].append("authoring.geometry.segments")
        for proposal in self.proposals.values():
            proposal["authoring"]["geometry"]["segments"] = 1
        self.bind_authority()
        error = self.assert_reason("no-admissible-plans", self.stage)
        self.assertIn("native-protected-field-change", str(error))
        self.frozen.add("/camera")
        self.proposals = {"a": self.changed(50, 64), "b": self.changed(55, 128)}
        self.bind_authority()
        error = self.assert_reason("no-admissible-plans", self.stage)
        self.assertIn("native-locked-path-change", str(error))

    def test_current_pass_lock_and_wrong_rule_pass_block_before_provider(self):
        self.scope["pass_unlocked"] = False
        self.bind_authority()
        self.assert_reason("no-admissible-plans", self.stage)
        self.scope["pass_unlocked"] = True
        self.rules[0]["passId"] = "geometry"
        self.bind_authority()
        self.assert_reason("locked-or-wrong-pass", self.stage)
        self.provider.assert_not_called()

    def test_wildcard_append_alias_duplicate_and_unresolvable_paths_are_unsupported(self):
        for pointer in ("/camera/*", "/parts/-", "/camera/fov?", "/camera/~2fov", "/camera/missing", "camera.fov"):
            with self.subTest(pointer=pointer):
                self.rules[0]["pointer"] = pointer
                self.bind_authority()
                reason = "native-path-unresolvable" if pointer.endswith("missing") else "unsupported-native-pointer"
                self.assert_reason(reason, self.stage)
        self.rules[0]["pointer"] = "/camera/fov"
        self.rules.append(copy.deepcopy(self.rules[0]))
        self.bind_authority()
        self.assert_reason("duplicate-native-rule", self.stage)

    def test_rfc6901_exact_escaped_field_and_atomic_scalar_array(self):
        self.source["camera"]["a/b~c"] = 4
        self.context["bindings"] = [binding for binding in self.context["bindings"] if binding["role"] != "spec"]
        self.context["bindings"].append(self.binding("spec", "scene.json", json.dumps(self.source).encode()))
        self.rules = [self.rule("camera.escaped", "/camera/a~1b~0c"), self.rule("camera.position", "/camera/position")]
        self.scope["field_paths"] = {"camera.escaped", "camera.position"}
        self.proposals = {"a": copy.deepcopy(self.source), "b": copy.deepcopy(self.source)}
        for proposal in self.proposals.values():
            proposal["camera"]["a/b~c"] = 7
            proposal["camera"]["position"] = [2, 3, 4]
        for plan in self.context["candidates"]:
            plan["fieldPaths"] = ["camera.escaped", "camera.position"]
        self.bind_authority()
        self.selected()

    def test_arbitrary_candidate_command_or_operation_never_reaches_provider(self):
        for plan in self.context["candidates"]:
            plan["argv"] = ["execute"]
        self.assert_reason("no-admissible-plans", self.stage)
        for plan in self.context["candidates"]:
            del plan["argv"]
            plan["operations"] = ["shell-command"]
        self.assert_reason("no-admissible-plans", self.stage)
        self.provider.assert_not_called()

    def test_source_required_operation_needs_authorized_and_bound_evidence(self):
        self.scope["operations"].add("reference-texture")
        self.scope["source_operations"] = {"reference-texture"}
        for plan in self.context["candidates"]:
            plan["operations"] = ["reference-texture"]
        self.bind_authority()
        error = self.assert_reason("no-admissible-plans", self.stage)
        self.assertIn("material-source-required", str(error))
        self.scope["authorized_sources"] = {"a" * 64}
        for plan in self.context["candidates"]:
            plan["materialSources"] = ["a" * 64]
        self.bind_authority()
        error = self.assert_reason("no-admissible-plans", self.stage)
        self.assertIn("material-evidence-missing", str(error))
        evidence = self.binding("material-evidence", "material.json", b"independently licensed material evidence")
        self.context["bindings"].append(evidence)
        self.scope["authorized_sources"] = {evidence["sha256"]}
        for plan in self.context["candidates"]:
            plan["materialSources"] = [evidence["sha256"]]
        self.bind_authority()
        self.selected()
        self.fallback.assert_not_called()

    def test_missing_provenance_or_permissions_cannot_be_replaced_by_callback(self):
        initial = copy.deepcopy(self.context["bindings"])
        for role in ("provenance", "permissions", "source", "critical-features"):
            with self.subTest(role=role):
                self.context["bindings"] = [binding for binding in initial if binding["role"] != role]
                self.assert_reason("evidence-missing", self.stage)
        self.authority.assert_not_called()
        self.provider.assert_not_called()

    def test_provenance_and_permissions_require_full_exact_immutable_binding(self):
        for role, reason in (("provenance", "native-provenance-mismatch"), ("permissions", "native-permissions-mismatch")):
            with self.subTest(role=role):
                self.bind_authority()
                old = next(binding for binding in self.context["bindings"] if binding["role"] == role)
                bad = self.binding(role, old["path"], b'{"granted":true}')
                self.context["bindings"] = [bad if binding["role"] == role else binding for binding in self.context["bindings"]]
                self.assert_reason(reason, self.stage)
        self.authority.assert_not_called()
        self.provider.assert_not_called()

    def test_rule_or_scope_cannot_widen_independently_of_permission_record(self):
        self.rules[0]["fieldPath"] = "camera.changedAlias"
        self.assert_reason("native-permissions-mismatch", self.stage)
        self.rules[0]["fieldPath"] = "camera.fov"
        self.scope["operations"].add("new-operation")
        self.assert_reason("native-permissions-mismatch", self.stage)

    def test_missing_validator_or_unavailable_authority_has_precise_reason(self):
        self.assert_reason("native-validator-unavailable", self.stage, validate_spec=None)
        self.assert_reason("native-authority-unavailable", self.stage, authorize_native=None)
        self.authority.side_effect = RuntimeError("native writer not safely adaptable")
        self.assert_reason("native-authority-unavailable", self.stage)
        self.provider.assert_not_called()

    def test_validator_literal_true_and_exceptions_fail_before_provider(self):
        for invalid in (False, None, {"valid": True}):
            self.validator.return_value = invalid
            self.assert_reason("native-validation-failed", self.stage)
        self.validator.side_effect = RuntimeError("unsupported native writer schema")
        self.assert_reason("native-validation-failed", self.stage)
        self.provider.assert_not_called()

    def test_live_permission_revocation_blocks_consult_begin_and_finish(self):
        stage = self.stage()
        self.permission_live = False
        self.assert_reason("native-permission-revoked", stage.consult, **self.options)
        self.provider.assert_not_called()
        self.permission_live = True
        record = stage.consult(**self.options)["record"]
        self.permission_live = False
        self.assert_reason("native-permission-revoked", stage.begin_application, record, 2, consent=self.consent)
        self.permission_live = True
        application = stage.begin_application(record, 2, consent=self.consent)["application"]
        self.permission_live = False
        self.assert_reason("native-permission-revoked", stage.complete_application, record, application,
                           self.output(self.proposals["a"]), consent=self.consent)

    def test_stale_reference_source_permission_and_spec_are_rechecked(self):
        stage = self.stage()
        for role in ("reference", "source", "permissions", "provenance", "spec"):
            with self.subTest(role=role):
                binding = next(binding for binding in self.context["bindings"] if binding["role"] == role)
                path = self.workspace / binding["path"]
                original = path.read_bytes()
                path.write_bytes(b"outside edit")
                self.assert_reason("stale-evidence", stage.consult, **self.options)
                path.write_bytes(original)
        self.provider.assert_not_called()

    def test_actual_paths_values_quality_and_output_alias_must_match_proposal(self):
        stage, record = self.selected()
        application = stage.begin_application(record, 2, consent=self.consent)["application"]
        for mutate, reason in (
                (lambda spec: spec["camera"].update(fov=99), "proposal-output-mismatch"),
                (lambda spec: spec["camera"].update(position=[9, 9, 9]), "native-path-not-granted"),
                (lambda spec: spec["parts"][0].update(width=9), "native-path-not-granted"),
                (lambda spec: spec["qualityContract"].update(threshold=0), "native-protected-field-change")):
            with self.subTest(reason=reason):
                spec = copy.deepcopy(self.proposals["a"])
                mutate(spec)
                self.assert_reason(reason, stage.complete_application, record, application,
                                   self.output(spec), consent=self.consent)
        original_binding = next(binding for binding in self.context["bindings"] if binding["role"] == "spec")
        self.assert_reason("invalid-successor-output", stage.complete_application, record, application,
                           original_binding, consent=self.consent)
        self.assertEqual(stage.complete_application(record, application, self.output(self.proposals["a"]),
                                                   consent=self.consent)["status"], "completed")

    def test_validator_cannot_edit_output_between_comparison_and_completion(self):
        stage, record = self.selected()
        application = stage.begin_application(record, 2, consent=self.consent)["application"]
        output = self.output(self.proposals["a"])
        def mutate(spec):
            (self.workspace / output["path"]).write_text("{}")
            return True
        self.validator.side_effect = mutate
        self.assert_reason("stale-evidence", stage.complete_application, record, application, output, consent=self.consent)

    def test_copying_proposals_and_rules_prevents_post_snapshot_mutation(self):
        stage = self.stage()
        expected = copy.deepcopy(self.proposals["a"])
        self.proposals["a"]["camera"]["fov"] = 99
        self.rules[0]["pointer"] = "/qualityContract/threshold"
        record = stage.consult(**self.options)["record"]
        self.assertEqual(stage.begin_application(record, 2, consent=self.consent)["spec"], expected)

    def test_unique_durable_successor_and_explicit_begin_are_required(self):
        stage, record = self.selected()
        application = self.controller.begin_application(record, 2, consent=self.consent)
        self.assert_reason("application-not-authorized", stage.complete_application, record, application,
                           self.output(self.proposals["a"]), consent=self.consent)
        trial = stage.begin_application(record, 2, consent=self.consent)
        self.assertEqual(trial["application"], application)
        self.assert_reason("application-conflict", stage.begin_application, record, 3, consent=self.consent)
        self.assertEqual(stage.begin_application(record, 2, consent=self.consent)["application"], application)

    def test_explicit_consent_required_even_in_same_process_and_revocation_blocks_finish(self):
        stage, record = self.selected()
        self.assert_reason("consent-unproven", stage.consume, record)
        self.assert_reason("consent-unproven", stage.begin_application, record, 2)
        application = stage.begin_application(record, 2, consent=self.consent)["application"]
        self.grant["granted"] = False
        self.assert_reason("consent-changed", stage.complete_application, record, application,
                           self.output(self.proposals["a"]), consent=self.consent)

    def test_consent_rechecked_inside_durable_finish(self):
        stage, record = self.selected()
        application = stage.begin_application(record, 2, consent=self.consent)["application"]
        revoked = dict(copy.deepcopy(self.grant), granted=False)
        consent = Mock(side_effect=[copy.deepcopy(self.grant), copy.deepcopy(self.grant), revoked])
        self.assert_reason("consent-changed", stage.complete_application, record, application,
                           self.output(self.proposals["a"]), consent=consent)
        self.assertEqual(consent.call_count, 3)

    def test_native_permission_rechecked_inside_durable_finish(self):
        stage, record = self.selected()
        application = stage.begin_application(record, 2, consent=self.consent)["application"]
        calls = []
        def revoke_after_validation():
            calls.append(True)
            if len(calls) == 2:
                self.permission_live = False
            return copy.deepcopy(self.grant)
        # The controller deliberately wraps callback failures as consent-probe-error.
        self.assert_reason("consent-probe-error", stage.complete_application, record, application,
                           self.output(self.proposals["a"]), consent=revoke_after_validation)
        self.permission_live = True
        pending = stage.begin_application(record, 2, consent=self.consent)["application"]
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["applicationId"], application["applicationId"])

    def test_true_fresh_process_resumes_same_trial_without_consultation(self):
        stage, record = self.selected()
        application = stage.begin_application(record, 2, consent=self.consent)["application"]
        payload = {"workspace": str(self.workspace), "context": self.context,
            "scope": {key: sorted(value) if type(value) is set else value for key, value in self.scope.items()},
            "rules": self.rules, "frozen": sorted(self.frozen), "forbidden": sorted(self.forbidden),
            "proposals": self.proposals, "record": record, "application": application,
            "grant": self.grant, "output": self.output(self.proposals["a"])}
        code = ("import json, sys; from forge.tests.test_native_strategy_decision import resume_probe; "
                "print(json.dumps(resume_probe(json.load(sys.stdin))))")
        result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[2],
                                input=json.dumps(payload), text=True, capture_output=True, check=True)
        actual = json.loads(result.stdout)
        self.assertEqual(actual["missingConsent"], "consent-unproven")
        self.assertEqual(actual["conflict"], "application-conflict")
        self.assertTrue(actual["sameApplication"])
        self.assertEqual(actual["completed"]["status"], "completed")
        self.assertEqual(actual["completed"]["applicationId"], application["applicationId"])
        self.assertEqual(self.provider.call_count, 1)

    def test_jev_uncertainty_never_grants_application_or_model_override(self):
        self.provider.side_effect = lambda context: self.advisory(context, probability=0.55)
        stage = self.stage()
        result = stage.consult(**self.options, max_trials=2)
        self.assertEqual(result["action"], "compare-plans")
        self.assert_reason("application-not-authorized", stage.begin_application, result["record"], 2, consent=self.consent)
        self.fallback.assert_not_called()

    def test_object_deadline_not_reset_by_native_strategy_revision(self):
        self.selected()
        self.now += 3600
        self.context["subject"]["revision"] = 2
        self.bind_authority()
        result = self.stage().consult(**self.options)
        self.assertEqual(result["record"]["reasonCode"], "deadline-exhausted")
        self.assertEqual(self.provider.call_count, 1)
        self.fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
