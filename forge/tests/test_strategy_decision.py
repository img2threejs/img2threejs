"""Offline strategy boundary probes; no provider/plugin/network imports or calls."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from forge._shared.decision_control import DecisionController, DecisionError, canonical_digest
from forge.stage2_spec.strategy_decision import StrategyDecision


class StrategyDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve()
        self.now = 1000
        self.controller = DecisionController(self.workspace, "run-1", clock=lambda: self.now)
        self.source = {"qualityContract": {"threshold": 0.8, "blockingGates": ["volume"]},
                       "componentTree": [
                           {"id": "body", "geometry": {"profile": [1, 2], "segments": 24}},
                           {"id": "handle", "geometry": {"profile": [1, 1], "segments": 24}}]}
        self.context = {"kind": "img2.build-strategy-context", "version": 1,
                        "group": "build-strategy", "subject": {"runId": "run-1", "objectId": "cup",
                        "revision": 1, "passId": "geometry"}, "bindings": [
                            self.binding("reference", "reference.png", b"offline reference"),
                            self.binding("source", "source.ts", b"offline source"),
                            self.binding("spec", "spec.json", json.dumps(self.source).encode()),
                            self.binding("critical-features", "critical.json", b'{"handle":true}')],
                        "candidates": [self.plan("a"), self.plan("b")],
                        "observations": {}, "requirements": {"criticalFeatures": [{"id": "handle"}],
                        "requiredViews": []}, "policyVersion": "policy-v1",
                        "questionSetVersion": "strategy-questions-v1", "requestedModel": "jev-1.14.0"}
        self.scope = {"pass_id": "geometry", "pass_unlocked": True, "components": {"body"},
                      "operations": {"lathe"}, "field_paths": {"geometry.profile"},
                      "authorized_sources": set(), "source_operations": set(),
                      "available_dependencies": {"mesh-engine"},
                      "rig_required": False, "rig_available": False}
        self.proposals = {"a": self.changed([1, 3]), "b": self.changed([2, 3])}
        self.validator = Mock(return_value=True)
        self.grant = {"granted": True, "purposes": ["routing", "build-strategy", "quality-review", "repair"],
                      "sha256": "c" * 64, "tokenBudget": 10000}
        self.provider = Mock(side_effect=self.advisory)
        self.fallback = Mock(side_effect=lambda ctx: self.advisory(ctx, model="host-model"))
        self.options = {"provider": self.provider, "fallback": self.fallback,
                        "availability": lambda: {"status": "present"},
                        "consent": lambda: copy.deepcopy(self.grant), "request_token_limit": 128}

    @staticmethod
    def plan(ident):
        return {"id": ident, "passId": "geometry", "components": ["body"],
                "operations": ["lathe"], "fieldPaths": ["geometry.profile"],
                "materialSources": [], "dependencies": ["mesh-engine"], "rigHandoff": False}

    def binding(self, role, path, data):
        target = self.workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"role": role, "path": path, "sha256": hashlib.sha256(data).hexdigest()}

    def changed(self, profile):
        spec = copy.deepcopy(self.source)
        spec["componentTree"][0]["geometry"]["profile"] = profile
        return spec

    def stage(self):
        return StrategyDecision(self.controller, self.context, scope=self.scope,
                                proposals=self.proposals, validate_spec=self.validator)

    @staticmethod
    def advisory(context, *, probability=0.9, confidence=0.8, noul=1, model="jev-1.14.0"):
        ids = [plan["id"] for plan in context["candidates"]]
        probabilities = {ident: (1 - probability) / (len(ids) - 1) if len(ids) > 1 else 1 for ident in ids}
        probabilities[ids[0]] = probability if len(ids) > 1 else 1
        return {"kind": "img2.decision-advisory", "version": 1, "group": context["group"],
                "subject": copy.deepcopy(context["subject"]), "contextDigest": canonical_digest(context),
                "model": model, "answers": {"selection": {"type": "choice", "choice": ids[0],
                "probabilities": probabilities, "confidence": confidence},
                "anyFits": {"type": "noul", "noul": noul}},
                "usage": {"input_tokens": 10, "output_tokens": 2}}

    def assert_reason(self, reason, function, *args, **kwargs):
        with self.assertRaises(DecisionError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.reason_code, reason)

    def select(self):
        stage = self.stage()
        result = stage.consult(**self.options)
        self.assertEqual(result["action"], "selected")
        self.assertEqual(result["planIds"], ["a"])
        return stage, result["record"]

    def output(self, spec):
        return self.binding("spec", "trial-2/spec.json", json.dumps(spec).encode())

    def test_immutable_selection_precedes_one_successor_without_source_mutation(self):
        source_bytes = (self.workspace / "spec.json").read_bytes()
        stage, record = self.select()
        request = self.provider.call_args.args[0]
        self.assertEqual(request["observations"]["strategyProposalDigests"]["a"], canonical_digest(self.proposals["a"]))
        self.assertTrue(all("path" not in binding for binding in request["bindings"]))
        started = stage.begin_application(record, 2)
        events = [json.loads(line)["event"] for line in self.controller.journal_path.read_text().splitlines()]
        self.assertLess(events.index("decision-recorded"), events.index("application-pending"))
        self.assertEqual(started["application"], stage.begin_application(record, 2)["application"])
        self.assert_reason("application-conflict", stage.begin_application, record, 3)
        finished = stage.complete_application(record, started["application"], self.output(started["spec"]))
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["outcome"]["changes"], [{"componentId": "body", "field": "geometry.profile"}])
        self.assertEqual((self.workspace / "spec.json").read_bytes(), source_bytes)
        self.fallback.assert_not_called()

    def test_forged_threshold_or_gate_mutation_is_rejected_before_provider(self):
        for field, operation in (("quality.threshold", "lathe"), ("geometry.profile", "disable-gate")):
            with self.subTest(field=field, operation=operation):
                for plan in self.context["candidates"]:
                    plan["fieldPaths"] = [field]
                    plan["operations"] = [operation]
                self.assert_reason("no-admissible-plans", self.stage)
        self.provider.assert_not_called()
        self.fallback.assert_not_called()

    def test_quality_reduction_hidden_in_proposal_is_rejected_before_provider(self):
        for proposal in self.proposals.values():
            proposal["qualityContract"]["threshold"] = 0
        self.assert_reason("no-admissible-plans", self.stage)
        self.provider.assert_not_called()

    def test_protected_nested_field_cannot_hide_inside_new_allowed_container(self):
        self.scope["field_paths"] = {"geometry.newDetails"}
        for plan in self.context["candidates"]:
            plan["fieldPaths"] = ["geometry.newDetails"]
        for proposal in self.proposals.values():
            proposal["componentTree"][0]["geometry"]["newDetails"] = {"gate": False}
        self.assert_reason("no-admissible-plans", self.stage)
        self.provider.assert_not_called()

    def test_inadmissible_candidate_is_not_shown_to_provider(self):
        self.context["candidates"][1]["materialSources"] = ["a" * 64]
        stage, record = self.select()
        self.assertEqual([p["id"] for p in record["candidates"]], ["a"])
        self.assertEqual(stage.consume(record)["id"], "a")

    def test_locked_tools_sources_rig_and_arbitrary_fields_fail_closed(self):
        changes = [lambda: self.scope.update(pass_unlocked=False),
                   lambda: self.scope.update(available_dependencies=set()),
                   lambda: self.scope.update(rig_required=True),
                   lambda: [p.update(materialSources=["a" * 64]) for p in self.context["candidates"]],
                   lambda: [p.update(argv=["execute"]) for p in self.context["candidates"]]]
        initial_scope, initial_plans = copy.deepcopy(self.scope), copy.deepcopy(self.context["candidates"])
        for change in changes:
            with self.subTest(change=change):
                self.scope = copy.deepcopy(initial_scope)
                self.context["candidates"] = copy.deepcopy(initial_plans)
                change()
                self.assert_reason("no-admissible-plans", self.stage)
        self.provider.assert_not_called()

    def test_empty_set_missing_proposal_and_missing_evidence_fail_closed(self):
        plans = self.context["candidates"]
        self.context["candidates"] = []
        self.assert_reason("no-admissible-plans", self.stage)
        self.context["candidates"] = plans
        proposal = self.proposals.pop("a")
        self.assert_reason("proposal-missing", self.stage)
        self.proposals["a"] = proposal
        self.context["bindings"] = [b for b in self.context["bindings"] if b["role"] != "source"]
        self.assert_reason("evidence-missing", self.stage)
        self.provider.assert_not_called()

    def test_validator_rejection_is_preconsult_and_literal_true_required(self):
        for invalid in (False, None, {"valid": True}):
            with self.subTest(invalid=invalid):
                self.validator.return_value = invalid
                self.assert_reason("no-admissible-plans", self.stage)
        self.provider.assert_not_called()

    def test_stale_reference_is_rejected_before_provider(self):
        stage = self.stage()
        (self.workspace / "reference.png").write_bytes(b"outside edit")
        self.assert_reason("stale-evidence", stage.consult, **self.options)
        self.provider.assert_not_called()
        self.fallback.assert_not_called()

    def test_stale_source_prevents_application_after_selection(self):
        stage, record = self.select()
        (self.workspace / "source.ts").write_bytes(b"outside edit")
        self.assert_reason("stale-evidence", stage.begin_application, record, 2)

    def test_stale_spec_snapshot_is_rejected_on_construction(self):
        (self.workspace / "spec.json").write_text("{}")
        self.assert_reason("stale-evidence", self.stage)
        self.provider.assert_not_called()

    def test_proposal_mutation_after_snapshot_cannot_change_authorized_spec(self):
        stage = self.stage()
        expected = copy.deepcopy(self.proposals["a"])
        self.proposals["a"]["componentTree"][0]["geometry"]["profile"] = [99]
        record = stage.consult(**self.options)["record"]
        self.assertEqual(stage.begin_application(record, 2)["spec"], expected)

    def test_changed_proposal_invalidates_old_record_even_with_same_subject(self):
        stage, old = self.select()
        self.proposals["a"] = self.changed([3, 4])
        fresh = self.stage()
        fresh.consult(**self.options)
        self.assert_reason("stale-decision", stage.begin_application, old, 2)

    def test_actual_component_field_and_value_must_match_proposal(self):
        stage, record = self.select()
        application = stage.begin_application(record, 2)["application"]
        for name, mutate, reason in (
                ("component", lambda doc: doc["componentTree"][1]["geometry"].update(profile=[9]), "component-scope-violation"),
                ("field", lambda doc: doc["componentTree"][0]["geometry"].update(segments=4), "field-scope-violation"),
                ("value", lambda doc: doc["componentTree"][0]["geometry"].update(profile=[9]), "proposal-output-mismatch"),
                ("quality", lambda doc: doc["qualityContract"].update(threshold=0), "quality-boundary-violation")):
            with self.subTest(name=name):
                doc = copy.deepcopy(self.proposals["a"])
                mutate(doc)
                self.assert_reason(reason, stage.complete_application, record, application, self.output(doc))
        self.assertEqual(stage.complete_application(record, application, self.output(self.proposals["a"]))["status"], "completed")

    def test_output_binding_cannot_overwrite_or_alias_source(self):
        stage, record = self.select()
        application = stage.begin_application(record, 2)["application"]
        self.assert_reason("invalid-successor-output", stage.complete_application, record, application,
                           next(b for b in self.context["bindings"] if b["role"] == "spec"))
        (self.workspace / "trial-link").symlink_to(self.workspace, target_is_directory=True)
        binding = {"role": "spec", "path": "trial-link/spec.json",
                   "sha256": next(b for b in self.context["bindings"] if b["role"] == "spec")["sha256"]}
        self.assert_reason("evidence-missing", stage.complete_application, record, application, binding)

    def test_complete_requires_explicit_begin_or_resume(self):
        stage, record = self.select()
        application = self.controller.begin_application(record, 2)
        self.assert_reason("application-not-authorized", stage.complete_application, record, application,
                           self.output(self.proposals["a"]))
        stage.begin_application(record, 2)
        self.assertEqual(stage.complete_application(record, application, self.output(self.proposals["a"]))["status"], "completed")

    def test_uncertainty_returns_bounded_plans_never_model_or_application(self):
        self.context["candidates"] += [self.plan("c"), self.plan("d")]
        self.proposals.update(c=self.changed([3]), d=self.changed([4]))
        self.provider.side_effect = lambda ctx: self.advisory(ctx, probability=0.55)
        stage = self.stage()
        result = stage.consult(**self.options, max_trials=3)
        self.assertEqual(result["action"], "compare-plans")
        self.assertEqual(result["planIds"], ["a", "b", "c"])
        self.assert_reason("application-not-authorized", stage.begin_application, result["record"], 2)
        self.assertEqual(stage.consult(**self.options, max_trials=0)["action"], "request-input")
        self.fallback.assert_not_called()

    def test_none_fits_requests_input_not_new_plan_or_fallback(self):
        self.provider.side_effect = lambda ctx: self.advisory(ctx, noul=0.2)
        result = self.stage().consult(**self.options, max_trials=3)
        self.assertEqual(result["action"], "request-input")
        self.assertEqual(result["planIds"], [])
        self.fallback.assert_not_called()

    def test_missing_and_rejected_credentials_request_login_without_fallback(self):
        stage = self.stage()
        for status in ("credential-missing", "credential-rejected"):
            with self.subTest(status=status):
                options = dict(self.options, availability=lambda: {"status": status})
                result = stage.consult(**options)
                self.assertEqual(result["record"]["loginCommand"], "img2-jev login")
                self.assertEqual(result["action"], "request-input")
        self.provider.assert_not_called()
        self.fallback.assert_not_called()

    def test_model_fallback_uses_same_filtered_context_only_after_proven_outage(self):
        self.context["candidates"][1]["operations"] = ["unknown"]
        result = self.stage().consult(**dict(self.options, availability=lambda: {"status": "capability-unavailable"}))
        self.assertEqual(result["record"]["decider"], "model:host-model")
        self.assertEqual([p["id"] for p in self.fallback.call_args.args[0]["candidates"]], ["a"])
        self.provider.assert_not_called()

    def test_shared_object_deadline_is_not_reset_by_strategy_revision(self):
        self.select()
        self.now += 3600
        self.context["subject"]["revision"] = 2
        result = self.stage().consult(**self.options)
        self.assertEqual(result["record"]["reasonCode"], "deadline-exhausted")
        self.assertEqual(self.provider.call_count, 1)
        self.fallback.assert_not_called()

    def test_shared_token_budget_is_not_reset_by_strategy_revision(self):
        self.select()
        self.context["subject"]["revision"] = 2
        self.grant["tokenBudget"] = 128
        result = self.stage().consult(**self.options)
        self.assertEqual(result["record"]["reasonCode"], "budget-exhausted")
        self.assertEqual(self.provider.call_count, 1)
        self.fallback.assert_not_called()

    def test_source_operation_without_material_evidence_is_rejected_before_provider(self):
        self.scope["operations"].add("reference-texture")
        self.scope["source_operations"] = {"reference-texture"}
        for plan in self.context["candidates"]:
            plan["operations"] = ["reference-texture"]
        self.assert_reason("no-admissible-plans", self.stage)
        self.provider.assert_not_called()
        self.fallback.assert_not_called()
        binding = self.binding("material-evidence", "texture-evidence.json", b"authorized texture")
        self.context["bindings"].append(binding)
        self.scope["authorized_sources"] = {binding["sha256"]}
        for plan in self.context["candidates"]:
            plan["materialSources"] = [binding["sha256"]]
        self.select()

    def test_authorized_material_still_requires_bound_actual_evidence(self):
        self.scope["authorized_sources"] = {"a" * 64}
        for plan in self.context["candidates"]:
            plan["materialSources"] = ["a" * 64]
        self.assert_reason("no-admissible-plans", self.stage)
        self.provider.assert_not_called()
        binding = self.binding("material-evidence", "texture-evidence.json", b"authorized evidence")
        self.context["bindings"].append(binding)
        self.scope["authorized_sources"] = {binding["sha256"]}
        for plan in self.context["candidates"]:
            plan["materialSources"] = [binding["sha256"]]
        self.select()

    def test_outside_source_edit_invalidates_pending_completion(self):
        stage, record = self.select()
        application = stage.begin_application(record, 2)["application"]
        output = self.output(self.proposals["a"])
        (self.workspace / "source.ts").write_bytes(b"outside edit")
        self.assert_reason("stale-evidence", stage.complete_application, record, application, output)

    def test_validator_cannot_change_output_after_delta_check(self):
        stage, record = self.select()
        application = stage.begin_application(record, 2)["application"]
        output = self.output(self.proposals["a"])
        def alter_output(spec):
            (self.workspace / output["path"]).write_text("{}")
            return True
        self.validator.side_effect = alter_output
        self.assert_reason("stale-evidence", stage.complete_application, record, application, output)

    def test_new_controller_resumes_same_pending_successor_without_provider_call(self):
        stage, record = self.select()
        original = stage.begin_application(record, 2)["application"]
        controller = DecisionController(self.workspace, "run-1", clock=lambda: self.now)
        resumed = StrategyDecision(controller, self.context, scope=self.scope,
                                   proposals=self.proposals, validate_spec=self.validator)
        consent = Mock(side_effect=lambda: copy.deepcopy(self.grant))
        self.assertEqual(resumed.consume(record, consent=consent)["id"], "a")
        trial = resumed.begin_application(record, 2, consent=consent)
        self.assertEqual(trial["application"], original)
        self.assert_reason("application-conflict", resumed.begin_application, record, 3, consent=consent)
        finished = resumed.complete_application(
            record, trial["application"], self.output(trial["spec"]), consent=consent)
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["applicationId"], original["applicationId"])
        self.assertEqual(self.provider.call_count, 1)
        self.fallback.assert_not_called()
        self.assertGreaterEqual(consent.call_count, 7)

    def test_new_controller_never_silently_reuses_historical_consent(self):
        stage, record = self.select()
        stage.begin_application(record, 2)
        controller = DecisionController(self.workspace, "run-1", clock=lambda: self.now)
        resumed = StrategyDecision(controller, self.context, scope=self.scope,
                                   proposals=self.proposals, validate_spec=self.validator)
        self.assert_reason("consent-unproven", resumed.consume, record)
        self.assert_reason("consent-unproven", resumed.begin_application, record, 2)
        self.grant["granted"] = False
        consent = lambda: copy.deepcopy(self.grant)
        self.assert_reason("consent-changed", resumed.consume, record, consent=consent)
        self.assert_reason("consent-changed", resumed.begin_application, record, 2, consent=consent)
        self.assertEqual(self.provider.call_count, 1)
        self.fallback.assert_not_called()

    def test_resume_rechecks_live_consent_at_completion(self):
        stage, record = self.select()
        original = stage.begin_application(record, 2)["application"]
        controller = DecisionController(self.workspace, "run-1", clock=lambda: self.now)
        resumed = StrategyDecision(controller, self.context, scope=self.scope,
                                   proposals=self.proposals, validate_spec=self.validator)
        consent = lambda: copy.deepcopy(self.grant)
        resumed.begin_application(record, 2, consent=consent)
        self.grant["granted"] = False
        self.assert_reason("consent-changed", resumed.complete_application, record, original,
                           self.output(self.proposals["a"]), consent=consent)
        self.assertEqual(self.provider.call_count, 1)
        self.fallback.assert_not_called()

    def test_resume_rechecks_consent_inside_durable_finish(self):
        stage, record = self.select()
        original = stage.begin_application(record, 2)["application"]
        controller = DecisionController(self.workspace, "run-1", clock=lambda: self.now)
        resumed = StrategyDecision(controller, self.context, scope=self.scope,
                                   proposals=self.proposals, validate_spec=self.validator)
        resumed.begin_application(record, 2, consent=lambda: copy.deepcopy(self.grant))
        revoked = dict(copy.deepcopy(self.grant), granted=False)
        consent = Mock(side_effect=[copy.deepcopy(self.grant), copy.deepcopy(self.grant), revoked])
        self.assert_reason("consent-changed", resumed.complete_application, record, original,
                           self.output(self.proposals["a"]), consent=consent)
        self.assertEqual(consent.call_count, 3)
        self.assertEqual(self.provider.call_count, 1)
        self.fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
