"""Offline behavioral tests for base authority; never import a Jev plugin."""
from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))
from decision_control import DecisionController, DecisionError, canonical_digest


GROUPS = {
    "routing": ("img2.route-context", "route-questions-v2"),
    "build-strategy": ("img2.build-strategy-context", "strategy-questions-v1"),
    "quality-review": ("img2.quality-review-context", "quality-review-questions-v1"),
    "repair": ("img2.repair-context", "repair-decision-questions-v1"),
}


def try_other_run(workspace, result):
    try:
        DecisionController(workspace, "another-run")
    except DecisionError as exc:
        result.put(exc.reason_code)
    else:
        result.put("unexpected-acquisition")


class SimulatedCrash(BaseException):
    pass


class DecisionControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve()
        self.reference = self.workspace / "reference.png"
        self.reference.write_bytes(b"offline reference fixture")
        self.now = 1000.0
        self.controller = DecisionController(self.workspace, "run-1", clock=lambda: self.now)
        self.grant = {"granted": True, "purposes": list(GROUPS),
                      "sha256": "b" * 64, "tokenBudget": 10000}
        self.consent = lambda: copy.deepcopy(self.grant)
        self.availability = Mock(return_value={"status": "present"})
        self.provider = Mock(side_effect=self.advisory)
        self.fallback = Mock(side_effect=lambda ctx: self.advisory(ctx, model="gpt-sol"))

    def context(self, group="routing", object_id="object-1", revision=1):
        kind, questions = GROUPS[group]
        return {"kind": kind, "version": 1, "group": group,
                "subject": {"runId": "run-1", "objectId": object_id, "revision": revision, "passId": "geometry"},
                "bindings": [{"role": "reference", "path": "reference.png",
                              "sha256": hashlib.sha256(self.reference.read_bytes()).hexdigest()}],
                "candidates": [{"id": "a"}, {"id": "b"}], "observations": {"partCount": 2},
                "requirements": {"criticalFeatures": [], "requiredViews": []},
                "policyVersion": "policy-v1", "questionSetVersion": questions, "requestedModel": "jev-1.14.0"}

    @staticmethod
    def advisory(context, model="jev-1.14.0", probability=0.9, confidence=0.8, noul=1):
        return {"kind": "img2.decision-advisory", "version": 1, "group": context["group"],
                "subject": copy.deepcopy(context["subject"]), "contextDigest": canonical_digest(context),
                "model": model, "answers": {
                    "selection": {"type": "choice", "choice": "a", "probabilities": {"a": probability, "b": 1 - probability},
                                  "confidence": confidence}, "anyFits": {"type": "noul", "noul": noul}},
                "usage": {"input_tokens": 10, "output_tokens": 2}}

    def consult(self, context=None, **overrides):
        arguments = {"provider": self.provider, "fallback": self.fallback,
                     "availability": self.availability, "consent": self.consent, "request_token_limit": 128}
        arguments.update(overrides)
        return self.controller.consult(context or self.context(), **arguments)

    def assert_reason(self, reason, function, *args, **kwargs):
        with self.assertRaises(DecisionError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.reason_code, reason)

    def test_canonical_digest_matches_contract(self):
        value = {"z": [1, True, None], "name": "café"}
        expected = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                            allow_nan=False).encode()).hexdigest()
        self.assertEqual(canonical_digest(value), expected)
        for invalid in (float("nan"), float("inf"), {1: "wrong"}, (1, 2), object()):
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaises(DecisionError):
                    canonical_digest(invalid)

    def test_authentication_suspends_without_fallback(self):
        for status in ("credential-missing", "credential-rejected", "credential-storage-error"):
            result = self.consult(availability=lambda: {"status": status})
            self.assertEqual(result["phase"], "suspended")
            self.assertEqual(result["status"], "request-input")
            self.assertEqual(result["reasonCode"], status)
            self.assertEqual(result["loginCommand"], "img2-jev login")
        self.provider.assert_not_called()
        self.fallback.assert_not_called()

    def test_unproven_or_failed_availability_probe_never_enables_fallback(self):
        def broken_probe():
            raise OSError("probe failed")

        for probe in (lambda: {"status": "availability-unproven"}, broken_probe):
            with self.subTest(probe=probe):
                record = self.consult(availability=probe)
                self.assertEqual(record["status"], "request-input")
                self.assertEqual(record["reasonCode"], "availability-unproven")
        self.provider.assert_not_called()
        self.fallback.assert_not_called()
    def test_workspace_transaction_revalidates_consent_and_closes_verifier(self):
        context = self.context()
        record = self.consult(context)
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            self.assertEqual(verify(record, context)["recordId"], record["recordId"])
            self.grant["granted"] = False
            self.assert_reason("consent-changed", verify, record, context)
        self.assert_reason("transaction-closed", verify, record, context)
    def test_fresh_controller_consumes_with_explicit_live_consent(self):
        context = self.context()
        record = self.consult(context)
        reopened = DecisionController(self.workspace, "run-1", clock=lambda: self.now)
        self.assertEqual(reopened.consume(record, context, consent=self.consent)["recordId"], record["recordId"])
        self.grant["granted"] = False
        self.assert_reason("consent-changed", reopened.consume, record, context, consent=self.consent)
    def test_fresh_controller_resumes_and_finishes_application_with_live_consent(self):
        record = self.consult(self.context("repair"))
        application = self.controller.begin_application(record, 2)
        reopened = DecisionController(self.workspace, "run-1", clock=lambda: self.now)
        resumed = reopened.begin_application(record, 2, consent=self.consent)
        self.assertEqual(resumed["applicationId"], application["applicationId"])
        finished = reopened.finish_application(application["applicationId"], {"status": "rejected"},
                                               consent=self.consent)
        self.assertEqual(finished["status"], "rejected")

    def test_deadline_read_does_not_reset_when_controller_reopens(self):
        self.assert_reason("object-not-started", self.controller.remaining_object_seconds, "object-1")
        self.consult()
        self.now += 3500
        reopened = DecisionController(self.workspace, "run-1", clock=lambda: self.now)
        self.assertEqual(reopened.remaining_object_seconds("object-1"), 100)
        self.now += 101
        self.assertEqual(reopened.remaining_object_seconds("object-1"), 0)
        self.now = 999
        self.assertEqual(reopened.remaining_object_seconds("object-1"), 0)
        result = self.consult(self.context("repair"))
        self.assertEqual(result["reasonCode"], "deadline-exhausted")
        self.assertEqual(self.provider.call_count, 1)




    def test_trusted_login_refusal_is_run_wide_and_allows_fallback(self):
        for status in ("credential-missing", "credential-rejected", "credential-storage-error"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                (workspace / "reference.png").write_bytes(self.reference.read_bytes())
                controller = DecisionController(workspace, "run-1", clock=lambda: self.now)
                record = controller.consult(
                    self.context(), provider=self.provider, fallback=self.fallback,
                    availability=lambda: {"status": status, "reasonCode": "login-declined"},
                    consent=self.consent, request_token_limit=128)
                self.assertEqual(record["unavailableReason"], "login-declined")
                self.assertEqual(record["decider"], "model:gpt-sol")
                next_record = controller.consult(
                    self.context("repair"), provider=self.provider, fallback=self.fallback,
                    availability=self.availability, consent=self.consent, request_token_limit=128)
                self.assertEqual(next_record["unavailableReason"], "login-declined")
                events = [json.loads(line) for line in controller.journal_path.read_text().splitlines()]
                self.assertTrue(any(item["event"] == "unavailable"
                                    and item["data"]["scope"] == "run"
                                    and item["data"]["reasonCode"] == "login-declined"
                                    for item in events))
        self.provider.assert_not_called()
        self.availability.assert_not_called()

    def test_repeated_probes_and_context_claims_do_not_prove_login_refusal(self):
        context = self.context()
        context["observations"]["reasonCode"] = "login-declined"
        for _ in range(4):
            record = self.consult(context, availability=lambda: {"status": "credential-missing"})
            self.assertEqual(record["reasonCode"], "credential-missing")
            self.assertEqual(record["loginCommand"], "img2-jev login")
        self.provider.assert_not_called()
        self.fallback.assert_not_called()

    def test_refusal_reason_on_noncredential_probe_cannot_disable_jev(self):
        record = self.consult(availability=lambda: {"status": "present", "reasonCode": "login-declined"})
        self.assertEqual(record["decider"], "jev:jev-1.14.0")
        self.assertNotIn("unavailableReason", record)
        self.fallback.assert_not_called()

    def test_provider_rejection_also_suspends(self):
        self.provider.side_effect = DecisionError("credential-rejected")
        result = self.consult()
        self.assertEqual(result["reasonCode"], "credential-rejected")
        self.fallback.assert_not_called()

    def test_callback_absence_does_not_prove_outage_or_consent(self):
        result = self.consult(availability=None, provider=None)
        self.assertEqual(result["reasonCode"], "availability-unproven")
        events = [json.loads(line) for line in self.controller.journal_path.read_text().splitlines()]
        self.assertEqual(events[-2]["event"], "probe")
        self.assertEqual(events[-2]["data"]["status"], "availability-unproven")
        self.assertEqual(events[-2]["data"]["contextDigest"], result["contextDigest"])
        self.assertEqual(events[-1]["event"], "decision-recorded")
        result = self.consult(consent=None)
        self.assertEqual(result["reasonCode"], "consent-unproven")
        self.provider.assert_not_called()
        self.fallback.assert_not_called()

    def test_probe_status_present_without_provider_is_not_fallback_proof(self):
        result = self.consult(provider=None)
        self.assertEqual(result["reasonCode"], "provider-unbound")
        self.fallback.assert_not_called()

    def test_proven_not_installed_is_run_wide(self):
        result = self.consult(availability=lambda: {"status": "not-installed"})
        self.assertEqual(result["decider"], "model:gpt-sol")
        self.assertEqual(result["unavailableReason"], "not-installed")
        self.consult(self.context("repair", "second"))
        self.assertEqual(self.fallback.call_count, 2)
        self.provider.assert_not_called()

    def test_missing_capability_is_group_scoped(self):
        self.consult(self.context("repair"), availability=lambda: {"status": "capability-unavailable"})
        result = self.consult(self.context("build-strategy"))
        self.assertEqual(result["decider"], "jev:jev-1.14.0")
        self.assertEqual(self.provider.call_count, 1)
        self.assertEqual(self.fallback.call_count, 1)

    def test_timeout_is_request_scoped(self):
        def provider(context):
            if context["subject"]["objectId"] == "object-1":
                raise DecisionError("timeout")
            return self.advisory(context)
        self.provider.side_effect = provider
        first = self.consult()
        second = self.consult(self.context(object_id="second"))
        self.assertEqual(first["unavailableReason"], "timeout")
        self.assertTrue(second["decider"].startswith("jev:"))
        self.assertEqual(self.provider.call_count, 2)
        self.assertEqual(self.fallback.call_count, 1)

    def test_uncertain_and_none_fits_do_not_use_model(self):
        self.provider.side_effect = lambda context: self.advisory(context, probability=0.6)
        first = self.consult()
        self.assertEqual(first["status"], "uncertain")
        self.provider.side_effect = lambda context: self.advisory(context, noul=0.1)
        second = self.consult(self.context(object_id="second"))
        self.assertEqual(second["reasonCode"], "none-fits")
        self.assertEqual(self.consult(self.context(object_id="second")), second)
        self.assertEqual(self.provider.call_count, 2)
        self.fallback.assert_not_called()

    def test_low_probability_fallback_requests_input(self):
        self.fallback.side_effect = lambda context: self.advisory(context, model="gpt-sol", probability=0.81)
        result = self.consult(availability=lambda: {"status": "not-installed"})
        self.assertEqual(result["status"], "request-input")

    def decomposition_context(self):
        context = self.context()
        context["observations"]["images"] = [{"role": "reference", "asset": "source"}]
        context["requirements"]["decomposition"] = {
            "image": "source", "objects": [
                {"id": "cup", "label": "cup", "approxBox": [0.2, 0.1, 0.9, 0.8]}]}
        return context

    def decomposition_advisory(self, context):
        result = self.advisory(context)
        # Actual TypeSafe wire shape: Box is an edge map and may have null confidence.
        result["answers"].update({
            "object:cup:visible": {"type": "noul", "noul": 1.0},
            "object:cup:box": {"type": "box",
                               "box": {"top": 0.1, "bottom": 0.8, "left": 0.2, "right": 0.9},
                               "confidence": None},
            "missingObjects": {"type": "noul", "noul": 0.0},
        })
        return result

    def test_routing_decomposition_accepts_exact_documented_wire_answers(self):
        context = self.decomposition_context()
        provider = Mock(side_effect=self.decomposition_advisory)
        record = self.consult(context, provider=provider)
        self.assertEqual(record["status"], "answered")
        self.assertIsNone(record["answers"]["object:cup:box"]["confidence"])
        self.assertEqual(record["answers"]["object:cup:box"]["box"],
                         {"top": 0.1, "bottom": 0.8, "left": 0.2, "right": 0.9})
        self.assertEqual(self.controller.consume(record, context), record)
        self.assertEqual(self.consult(context, provider=provider), record)
        provider.assert_called_once()
        self.fallback.assert_not_called()

    def test_invalid_decomposition_context_is_rejected_before_provider(self):
        mutations = [
            lambda ctx: ctx["requirements"]["decomposition"].update(image="unlisted"),
            lambda ctx: ctx["requirements"]["decomposition"].update(objects=[]),
            lambda ctx: ctx["requirements"]["decomposition"]["objects"].append({"id": "cup", "label": "cup"}),
            lambda ctx: ctx["requirements"]["decomposition"].update(
                objects=[{"id": "object-" + str(i), "label": "cup"} for i in range(13)]),
            lambda ctx: ctx["requirements"]["decomposition"]["objects"][0].update(approxBox=[0.5, 0.1, 0.5, 0.8]),
            lambda ctx: ctx["requirements"]["decomposition"]["objects"][0].update(approxBox=[0, 0, True, 1]),
            lambda ctx: ctx["requirements"]["decomposition"]["objects"][0].update(extra="uncontrolled"),
            lambda ctx: ctx["requirements"]["decomposition"]["objects"][0].update(label="uncontrolled free text"),
            lambda ctx: ctx["observations"]["images"].append({"role": "reference", "asset": "source"}),
        ]
        for mutate in mutations:
            context = self.decomposition_context()
            mutate(context)
            with self.assertRaises(DecisionError):
                self.consult(context)
        for group in ("repair", "build-strategy", "quality-review"):
            context = self.decomposition_context()
            context.update(group=group, kind=GROUPS[group][0], questionSetVersion=GROUPS[group][1])
            self.assert_reason("invalid-requirements", self.consult, context)
        self.provider.assert_not_called()
        self.fallback.assert_not_called()

    def test_decomposition_answers_are_closed_finite_and_nondegenerate(self):
        def extra(answer):
            answer["answers"]["object:foreign:visible"] = {"type": "noul", "noul": 1}
        mutations = [
            extra,
            lambda answer: answer["answers"].pop("missingObjects"),
            lambda answer: answer["answers"]["object:cup:visible"].update(noul=1.1),
            lambda answer: answer["answers"]["missingObjects"].update(noul=True),
            lambda answer: answer["answers"]["object:cup:box"]["box"].update(left=0.9),
            lambda answer: answer["answers"]["object:cup:box"]["box"].update(top=float("nan")),
            lambda answer: answer["answers"]["object:cup:box"]["box"].update(right=1.1),
            lambda answer: answer["answers"]["object:cup:box"].update(confidence=True),
            lambda answer: answer["answers"]["object:cup:box"].update(box=[0.2, 0.1, 0.9, 0.8]),
        ]
        for index, mutate in enumerate(mutations):
            context = self.decomposition_context()
            context["subject"]["objectId"] = "invalid-" + str(index)
            def provider(projected, mutate=mutate):
                answer = self.decomposition_advisory(projected)
                mutate(answer)
                return answer
            record = self.consult(context, provider=provider, fallback=None)
            self.assertEqual(record["reasonCode"], "malformed")
            self.assertEqual(record["status"], "unavailable")
        record = self.consult(self.context(object_id="no-decomposition"),
                              provider=self.decomposition_advisory, fallback=None)
        self.assertEqual(record["reasonCode"], "malformed")

    def test_projection_preserves_distinct_local_and_provider_digests(self):
        context = self.context()
        record = self.consult(context)
        projected = self.provider.call_args.args[0]
        self.assertNotIn("path", projected["bindings"][0])
        self.assertEqual(record["contextDigest"], canonical_digest(context))
        self.assertEqual(record["providerContextDigest"], canonical_digest(projected))
        self.assertNotEqual(record["contextDigest"], record["providerContextDigest"])
        self.assertEqual(self.controller.consume(record, context), record)

    def test_cache_is_current_semantics_only_and_records_are_immutable(self):
        context = self.context()
        first = self.consult(context)
        self.assertEqual(first, self.consult(context))
        self.assertEqual(self.provider.call_count, 1)
        corrected = copy.deepcopy(context)
        corrected["observations"]["partCount"] = 3
        second = self.consult(corrected)
        self.assertNotEqual(first["recordId"], second["recordId"])
        self.assert_reason("context-digest-mismatch", self.controller.consume, first, corrected)
        self.assert_reason("stale-decision", self.controller.consume, first, context)
        self.consult(context)
        self.assertEqual(self.provider.call_count, 3)
        changed = copy.deepcopy(second)
        changed["answers"]["selection"]["choice"] = "b"
        self.assert_reason("record-mismatch", self.controller.consume, changed, corrected)

    def test_group_and_subject_are_isolated(self):
        context = self.context()
        record = self.consult(context)
        self.assert_reason("group-mismatch", self.controller.consume, record, self.context("repair"))
        self.assert_reason("subject-mismatch", self.controller.consume, record, self.context(object_id="second"))
        self.consult(self.context("repair"))
        self.assertEqual(self.provider.call_count, 2)

    def test_foreign_advisory_provenance_is_rejected(self):
        for field, value, reason in (("group", "repair", "group-mismatch"),
                                     ("subject", {"runId": "foreign"}, "subject-mismatch"),
                                     ("contextDigest", "a" * 64, "context-digest-mismatch")):
            def forged(context, field=field, value=value):
                answer = self.advisory(context)
                answer[field] = value
                return answer
            self.provider.side_effect = forged
            self.assert_reason(reason, self.consult, self.context(object_id=field))
        self.fallback.assert_not_called()

    def test_binding_hash_and_symlink_escape_are_preflight_faults(self):
        context = self.context()
        context["bindings"][0]["sha256"] = "0" * 64
        self.assert_reason("stale-evidence", self.consult, context)
        for path in ("../reference.png", "/reference.png", "./reference.png", "folder//file", "folder\\file"):
            context = self.context()
            context["bindings"][0]["path"] = path
            self.assert_reason("unsafe-path", self.consult, context)
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "external"
            target.write_bytes(self.reference.read_bytes())
            (self.workspace / "link").symlink_to(target)
            context = self.context()
            context["bindings"][0]["path"] = "link"
            self.assert_reason("unsafe-path", self.consult, context)
        self.provider.assert_not_called()

    def test_outside_edits_and_revoked_consent_block_consume(self):
        context = self.context()
        record = self.consult(context)
        self.grant["granted"] = False
        self.assert_reason("consent-changed", self.controller.consume, record, context)
        self.grant["granted"] = True
        self.reference.write_bytes(b"outside edit")
        self.assert_reason("stale-evidence", self.controller.consume, record, context)

    def test_old_grant_waits_and_explicit_refusal_is_persistent(self):
        self.grant["purposes"] = ["routing"]
        self.assertEqual(self.consult()["reasonCode"], "consent-required")
        self.provider.assert_not_called()
        self.grant["granted"] = False
        self.assertEqual(self.consult()["unavailableReason"], "consent-declined")
        self.grant["granted"] = True
        self.grant["purposes"] = list(GROUPS)
        result = self.consult(self.context("repair"))
        self.assertEqual(result["unavailableReason"], "consent-declined")
        self.provider.assert_not_called()

    def test_elapsed_budget_survives_reopen_and_group_switch(self):
        self.consult()
        self.now += 3600
        reopened = DecisionController(self.workspace, "run-1", clock=lambda: self.now)
        result = reopened.consult(self.context("repair"), provider=self.provider,
                                  availability=self.availability, consent=self.consent, request_token_limit=128)
        self.assertEqual(result["reasonCode"], "deadline-exhausted")
        self.assertEqual(self.provider.call_count, 1)

    def test_usage_budget_survives_reopen(self):
        self.grant["tokenBudget"] = 128
        def spend(context):
            result = self.advisory(context)
            result["usage"] = {"input_tokens": 120, "output_tokens": 8}
            return result
        self.provider.side_effect = spend
        self.consult()
        reopened = DecisionController(self.workspace, "run-1", clock=lambda: self.now)
        result = reopened.consult(self.context("repair"), provider=self.provider,
                                  availability=self.availability, consent=self.consent, request_token_limit=128)
        self.assertEqual(result["reasonCode"], "budget-exhausted")
        self.assertEqual(self.provider.call_count, 1)

    def test_lease_is_process_independent_until_explicit_release(self):
        context = multiprocessing.get_context("spawn")
        result = context.Queue()
        child = context.Process(target=try_other_run, args=(str(self.workspace), result))
        child.start()
        child.join(10)
        if child.is_alive():
            child.terminate()
            child.join()
            self.fail("lease contender did not finish")
        self.assertEqual(child.exitcode, 0)
        self.assertEqual(result.get(timeout=2), "workspace-busy")
        self.now += 100000
        self.assert_reason("workspace-busy", DecisionController, self.workspace, "another-run")
        self.controller.release()
        acquired = DecisionController(self.workspace, "another-run")
        self.assert_reason("lease-lost", self.controller.release)
        acquired.release()

    def test_journal_tampering_blocks_all_authority(self):
        record = self.consult()
        lines = self.controller.journal_path.read_text().splitlines()
        entry = json.loads(lines[0])
        entry["data"]["startedAt"] += 1
        lines[0] = json.dumps(entry)
        self.controller.journal_path.write_text("\n".join(lines) + "\n")
        self.assert_reason("journal-broken", self.controller.consume, record, self.context())

    def test_removed_journal_cannot_reset_budget(self):
        self.consult()
        self.controller.journal_path.unlink()
        self.assert_reason("journal-broken", DecisionController, self.workspace, "run-1")

    def test_group_record_file_tampering_blocks_consumption(self):
        record = self.consult()
        path = self.workspace / ".img2threejs" / "decisions" / "routing" / (record["recordId"] + ".json")
        changed = copy.deepcopy(record)
        changed["status"] = "answered-by-attacker"
        path.write_text(json.dumps(changed))
        self.assert_reason("record-mismatch", self.controller.consume, record, self.context())

    def test_application_pins_successor_and_completed_resume_is_idempotent(self):
        context = self.context("repair")
        record = self.consult(context)
        first = self.controller.begin_application(record, 2)
        self.assertEqual(first["status"], "pending")
        reopened = DecisionController(self.workspace, "run-1", clock=lambda: self.now)
        cached = reopened.consult(context, provider=self.provider, availability=self.availability,
                                  consent=self.consent, request_token_limit=128)
        self.assertEqual(cached, record)
        self.assertEqual(reopened.begin_application(record, 2), first)
        self.assert_reason("application-conflict", reopened.begin_application, record, 3)
        outcome = {"status": "rejected", "reasonCode": "protected-regression", "retained": False}
        finished = reopened.finish_application(first["applicationId"], outcome)
        self.assertEqual(finished["status"], "rejected")
        self.assertNotIn("deliverable", finished)
        self.assertEqual(reopened.finish_application(first["applicationId"], outcome), finished)
        self.assertEqual(reopened.begin_application(record, 2), finished)
        self.assertEqual(self.provider.call_count, 1)
        self.assert_reason("application-conflict", reopened.finish_application,
                           first["applicationId"], {"status": "completed"})

    def test_application_cannot_use_uncertainty_or_stale_input(self):
        context = self.context("repair")
        self.provider.side_effect = lambda ctx: self.advisory(ctx, probability=0.6)
        record = self.consult(context)
        self.assert_reason("application-not-authorized", self.controller.begin_application, record, 2)
        self.provider.side_effect = self.advisory
        context["observations"]["partCount"] = 3
        record = self.consult(context)
        pending = self.controller.begin_application(record, 2)
        self.reference.write_bytes(b"outside edit")
        self.assert_reason("stale-evidence", self.controller.begin_application, record, 2)
        self.assert_reason("stale-evidence", self.controller.finish_application,
                           pending["applicationId"], {"status": "completed"})

    def test_pending_paid_attempt_retries_once_after_crash(self):
        def crash(context):
            raise SimulatedCrash()
        self.provider.side_effect = crash
        with self.assertRaises(SimulatedCrash):
            self.consult()
        reopened = DecisionController(self.workspace, "run-1", clock=lambda: self.now)
        self.provider.side_effect = self.advisory
        result = reopened.consult(self.context(), provider=self.provider,
                                  availability=self.availability, consent=self.consent, request_token_limit=128)
        self.assertEqual(result["status"], "answered")
        self.assertIn("retried-after-crash", self.controller.journal_path.read_text())
        self.assertEqual(self.provider.call_count, 2)

    def test_quality_scores_do_not_use_choice_confidence_floor(self):
        context = self.context("quality-review")
        context["requirements"]["dimensions"] = ["geometry"]
        context["bindings"].append(dict(context["bindings"][0], role="render"))
        context["observations"]["images"] = [{"role": "reference", "asset": "ref"},
                                               {"role": "render", "asset": "render"}]
        def review(projected):
            result = self.advisory(projected)
            result["answers"] = {"dimension:geometry": {
                "type": "score", "score": 3, "confidence": 0.1,
                "legend": {
                    "0": "Absent or wrong relative to the declared requirement.",
                    "1": "Major mismatch with the declared requirement.",
                    "2": "Partially meets the declared requirement, with visible deficiencies.",
                    "3": "Meets the declared requirement.",
                    "4": "Exceeds the declared requirement without changing it.",
                },
                "probabilities": {"0": 0, "1": 0, "2": 0, "3": 1, "4": 0}}}
            return result
        record = self.consult(context, provider=review)
        self.assertEqual(record["status"], "answered")
        self.assertEqual(record["reasonCode"], "review-evidence-only")
        self.assertNotIn("accepted", record)
        self.fallback.assert_not_called()

    def test_missing_review_evidence_prevents_provider_call(self):
        result = self.consult(self.context("quality-review"))
        self.assertEqual(result["reasonCode"], "missing-evidence")
        self.provider.assert_not_called()
        self.fallback.assert_not_called()

    def test_global_and_feature_evidence_roles_both_gate_review(self):
        context = self.context("quality-review")
        context["requirements"].update(
            dimensions=[], evidenceRoles=["gate-report"],
            criticalFeatures=[{"id": "handle", "evidenceRoles": ["camera"]}])
        context["bindings"].append(dict(context["bindings"][0], role="render"))
        context["observations"]["images"] = [
            {"role": "reference", "asset": "ref"}, {"role": "render", "asset": "render"}]
        context["bindings"].append(dict(context["bindings"][0], role="camera"))
        def review(projected):
            result = self.advisory(projected)
            result["answers"] = {"critical:handle": {
                "type": "choice", "choice": "pass", "confidence": 0.9,
                "probabilities": {"pass": 0.9, "fail": 0.05, "uncertain": 0.05}}}
            return result
        provider = Mock(side_effect=review)
        missing_global = self.consult(context, provider=provider)
        self.assertEqual(missing_global["reasonCode"], "missing-evidence")
        provider.assert_not_called()
        context["bindings"][-1]["role"] = "gate-report"
        missing_feature = self.consult(context, provider=provider)
        self.assertEqual(missing_feature["reasonCode"], "missing-evidence")
        provider.assert_not_called()
        context["bindings"].append(dict(context["bindings"][0], role="camera"))
        complete = self.consult(context, provider=provider)
        self.assertEqual(complete["reasonCode"], "review-evidence-only")
        provider.assert_called_once()
        self.fallback.assert_not_called()

    def test_default_bound_cannot_exceed_consent_budget(self):
        result = self.consult(request_token_limit=64000)
        self.assertEqual(result["reasonCode"], "budget-exhausted")
        self.provider.assert_not_called()
        self.fallback.assert_not_called()

    def test_overrun_is_accounted_and_never_reported_as_prevented(self):
        def overrun(context):
            result = self.advisory(context)
            result["usage"] = {"input_tokens": 130, "output_tokens": 10}
            return result
        self.grant["tokenBudget"] = 140
        result = self.consult(provider=overrun)
        self.assertEqual(result["reasonCode"], "budget-overrun")
        self.assertEqual(result["usage"], {"input_tokens": 130, "output_tokens": 10})
        second = self.consult(self.context("repair"))
        self.assertEqual(second["reasonCode"], "budget-exhausted")
        self.provider.assert_not_called()
        self.fallback.assert_not_called()

    def test_ambiguous_send_keeps_reservation_across_group_switch(self):
        self.grant["tokenBudget"] = 200
        self.provider.side_effect = lambda context: (_ for _ in ()).throw(SimulatedCrash())
        with self.assertRaises(SimulatedCrash):
            self.consult()
        result = self.consult(self.context("repair"))
        self.assertEqual(result["reasonCode"], "budget-exhausted")
        self.assertEqual(self.provider.call_count, 1)

    def test_finished_advisory_is_recovered_after_publication_crash(self):
        original_record = self.controller._record
        self.controller._record = Mock(side_effect=SimulatedCrash())
        with self.assertRaises(SimulatedCrash):
            self.consult()
        self.controller._record = original_record
        result = self.consult()
        self.assertEqual(result["status"], "answered")
        self.assertEqual(self.provider.call_count, 1)

    def test_token_reservation_bound_requires_a_positive_integer(self):
        for value in (True, 0, -1, 1.5, float("inf"), "128"):
            with self.subTest(value=value):
                self.assert_reason("invalid-number", self.consult, request_token_limit=value)
        self.provider.assert_not_called()

    def test_consent_revoked_during_provider_does_not_publish_authority(self):
        def revoke(context):
            answer = self.advisory(context)
            self.grant["granted"] = False
            return answer
        self.assert_reason("consent-changed", self.consult, provider=revoke)
        self.fallback.assert_not_called()

    def test_missing_evidence_error_is_not_an_outage(self):
        self.provider.side_effect = DecisionError("missing-evidence")
        result = self.consult()
        self.assertEqual(result["reasonCode"], "missing-evidence")
        self.assertEqual(result["status"], "request-input")
        self.fallback.assert_not_called()

    def test_two_ambiguous_sends_never_trigger_a_third_send(self):
        self.provider.side_effect = lambda context: (_ for _ in ()).throw(SimulatedCrash())
        for _ in range(2):
            with self.assertRaises(SimulatedCrash):
                self.consult()
        result = self.consult(fallback=None)
        self.assertEqual(result["reasonCode"], "ambiguous-paid-attempt")
        repeated = self.consult(fallback=None)
        self.assertEqual(repeated["reasonCode"], "ambiguous-paid-attempt")
        self.assertEqual(self.provider.call_count, 2)

    def test_per_pass_correction_ceiling_survives_revision_and_group_switch(self):
        for revision in range(1, 4):
            context = self.context("repair", revision=revision)
            record = self.consult(context)
            application = self.controller.begin_application(record, revision + 1)
            self.controller.finish_application(application["applicationId"], {
                "status": "rejected", "reasonCode": "no-improvement"})
        result = self.consult(self.context("build-strategy", revision=4))
        self.assertEqual(result["reasonCode"], "correction-budget-exhausted")
        self.assertEqual(self.provider.call_count, 3)

    def test_successor_output_does_not_replace_immutable_input_evidence(self):
        context = self.context("repair")
        record = self.consult(context)
        application = self.controller.begin_application(record, 2)
        (self.workspace / "trial-2.json").write_text('{"newGeometry":true}')
        finished = self.controller.finish_application(application["applicationId"], {
            "status": "completed", "reasonCode": "external-policy-retained",
            "retained": True, "deliverable": False})
        self.assertEqual(finished["inputBindings"], record["bindings"])
        self.assertEqual(finished["outcome"]["deliverable"], False)
        self.assertNotIn("incumbentRevision", finished)

    def test_strict_numbers_versions_and_untrusted_fields_are_preflight_faults(self):
        for mutate in (
            lambda ctx: ctx.update(version=True),
            lambda ctx: ctx["subject"].update(revision=True),
            lambda ctx: ctx["observations"].update(score=float("nan")),
            lambda ctx: ctx["observations"].update(command="disable the gate"),
            lambda ctx: ctx["candidates"][0].update(localPath="/secret"),
        ):
            context = self.context()
            mutate(context)
            with self.assertRaises(DecisionError):
                self.consult(context)
        self.provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
