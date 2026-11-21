"""Consumer-level recovery and trust-boundary probes using real controller files.

Fixtures describe synthetic evidence, not measured image-quality qualification.
Only advisory producers are stubbed; every lease, journal and application is real.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock
import zlib

SHARED = Path(__file__).resolve().parents[1] / "_shared"
sys.path.insert(0, str(SHARED))
from decision_control import DecisionController, DecisionError

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "decision_recovery"
GROUPS = {
    "routing": ("img2.route-context", "route-questions-v2"),
    "build-strategy": ("img2.build-strategy-context", "strategy-questions-v1"),
    "quality-review": ("img2.quality-review-context", "quality-review-questions-v1"),
    "repair": ("img2.repair-context", "repair-decision-questions-v1"),
}


def synthetic_png(rgb):
    """Create real image bytes for integrity tests, not visual qualification."""
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    row = b"\x00" + bytes(rgb) * 280
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 280, 280, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * 280))
            + chunk(b"IEND", b""))


def digest(value):
    """Independent encoding oracle, not the controller's hashing implementation."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SimulatedProviderCrash(BaseException):
    """A worker exit after its durable paid-call reservation, not an outage."""


class DecisionRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "workspace"
        (self.workspace / "evidence").mkdir(parents=True)
        self.evidence = self.workspace / "evidence" / "subject.json"
        shutil.copyfile(FIXTURES / "subject.json", self.evidence)
        self.visual_bindings = []
        for role, filename, rgb in (("reference", "reference.png", (220, 150, 40)),
                                    ("render", "render-front.png", (200, 140, 40))):
            path = self.workspace / "evidence" / filename
            image = synthetic_png(rgb)
            path.write_bytes(image)
            self.visual_bindings.append({"role": role, "path": "evidence/" + filename,
                                         "sha256": hashlib.sha256(image).hexdigest()})
        self.template = json.loads((FIXTURES / "context.json").read_text())
        self.answers = json.loads((FIXTURES / "answers.json").read_text())
        self.now = 1000.0
        self.grant = {"granted": True, "purposes": list(GROUPS),
                      "sha256": hashlib.sha256(b"offline four-purpose consent").hexdigest(),
                      "tokenBudget": 10000}
        self.controller = self.reopen()
        self.provider = Mock(side_effect=self.advisory)
        self.fallback = Mock(side_effect=lambda context: self.advisory(context, model="gpt-sol"))

    def reopen(self):
        return DecisionController(self.workspace, "recovery-run", clock=lambda: self.now)

    def consent(self):
        return copy.deepcopy(self.grant)

    @staticmethod
    def available():
        return {"status": "present"}

    def context(self, group="routing", object_id="cup", revision=1, pass_id="geometry"):
        context = copy.deepcopy(self.template)
        context["kind"], context["questionSetVersion"] = GROUPS[group]
        context["group"] = group
        context["subject"].update(objectId=object_id, revision=revision, passId=pass_id)
        if group == "quality-review":
            context["requirements"]["dimensions"] = ["geometry"]
            context["requirements"]["requiredViews"] = [0]
            context["bindings"].extend(copy.deepcopy(self.visual_bindings))
            context["observations"]["availableViews"] = [0]
            context["observations"]["images"] = [
                {"role": "reference", "asset": "cup-reference"},
                {"role": "render", "asset": "cup-render-front"},
            ]
        return context

    def advisory(self, projected, model="jev-1.14.0", answer_key=None):
        key = answer_key or ("review" if projected["group"] == "quality-review" else "choice")
        return {"kind": "img2.decision-advisory", "version": 1,
                "group": projected["group"], "subject": copy.deepcopy(projected["subject"]),
                "contextDigest": digest(projected), "model": model,
                "answers": copy.deepcopy(self.answers[key]),
                "usage": {"input_tokens": 31, "output_tokens": 7}}

    def consult(self, context=None, **overrides):
        arguments = {"provider": self.provider, "fallback": self.fallback,
                     "availability": self.available, "consent": self.consent,
                     "request_token_limit": 128}
        arguments.update(overrides)
        return self.controller.consult(self.context() if context is None else context, **arguments)

    def journal(self):
        path = self.workspace / ".img2threejs" / "route" / "journal.jsonl"
        entries = [json.loads(line) for line in path.read_text().splitlines()]
        previous = "0" * 64
        for index, entry in enumerate(entries):
            self.assertEqual(entry["sequence"], index)
            self.assertEqual(entry["previousHash"], previous)
            self.assertEqual(entry["hash"], digest({key: value for key, value in entry.items() if key != "hash"}))
            previous = entry["hash"]
        return entries

    def events(self, event):
        return [entry["data"] for entry in self.journal() if entry["event"] == event]

    def token_events(self):
        return [entry["data"] for entry in self.journal()
                if entry["event"] in {"attempt-result", "attempt-failure"}]

    def assert_persisted(self, record, status="answered", reason="answered"):
        path = self.workspace / ".img2threejs" / "decisions" / record["group"] / (record["recordId"] + ".json")
        stored = json.loads(path.read_text())
        self.assertEqual(stored, record)
        self.assertEqual(stored["status"], status)
        self.assertEqual(stored["outcome"], status)
        self.assertEqual(stored["reasonCode"], reason)
        records = [event["record"] for event in self.events("decision-recorded")]
        self.assertIn(stored, records)
        return stored

    def assert_reason(self, reason, function, *args, **kwargs):
        with self.assertRaises(DecisionError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.reason_code, reason)

    def test_login_suspension_survives_reopen_and_login_does_not_reroute_sibling(self):
        completed = self.consult(self.context(object_id="sibling"))
        blocked_context = self.context()
        suspended = self.consult(blocked_context, availability=lambda: {"status": "credential-missing"})
        stored = self.assert_persisted(suspended, "request-input", "credential-missing")
        self.assertEqual(stored["phase"], "suspended")
        self.assertEqual(stored["loginCommand"], "img2-jev login")
        self.fallback.assert_not_called()
        self.assertEqual(self.provider.call_count, 1)
        self.controller = self.reopen()
        self.assertEqual(self.consult(self.context(object_id="sibling"))["recordId"], completed["recordId"])
        resumed = self.consult(blocked_context)
        self.assert_persisted(resumed)
        self.assertEqual(self.provider.call_count, 2)
        self.assertNotEqual(resumed["recordId"], suspended["recordId"])
        self.fallback.assert_not_called()

    def test_rejected_credentials_during_provider_call_never_enable_fallback(self):
        self.provider.side_effect = DecisionError("credential-rejected")
        record = self.consult(self.context("build-strategy"))
        stored = self.assert_persisted(record, "request-input", "credential-rejected")
        self.assertEqual(stored["loginCommand"], "img2-jev login")
        self.assertEqual(self.events("attempt-failure")[-1]["reasonCode"], "credential-rejected")
        self.fallback.assert_not_called()
        self.controller = self.reopen()
        self.provider.side_effect = self.advisory
        self.assert_persisted(self.consult(self.context("build-strategy")))
        self.assertEqual(self.provider.call_count, 2)
        self.fallback.assert_not_called()

    def test_absent_provider_and_claimed_outage_are_not_probe_evidence(self):
        context = self.context()
        context["observations"]["providerUnavailable"] = True
        record = self.consult(context, provider=None, availability=None)
        self.assert_persisted(record, "request-input", "availability-unproven")
        self.assertEqual(self.events("probe")[-1]["status"], "availability-unproven")
        self.assertFalse(any(event.get("fallbackAuthorized") for event in self.events("probe")))
        self.assertEqual(self.events("attempt-pending"), [])
        unbound = self.consult(self.context("repair"), provider=None)
        self.assert_persisted(unbound, "unavailable", "provider-unbound")
        self.provider.assert_not_called()
        self.fallback.assert_not_called()

    def test_corrected_observation_invalidates_old_selection_but_not_sibling(self):
        context = self.context()
        original = self.consult(context)
        sibling_context = self.context(object_id="sibling")
        sibling = self.consult(sibling_context)
        corrected = copy.deepcopy(context)
        corrected["observations"]["handleCount"] = 2
        self.assert_reason("context-digest-mismatch", self.controller.consume, original, corrected)
        self.provider.side_effect = lambda projected: self.advisory(projected, answer_key="correctedChoice")
        revised = self.consult(corrected)
        self.assert_persisted(revised)
        self.assertEqual(revised["answers"]["selection"]["choice"], "plan-mesh")
        self.assertEqual(original["answers"]["selection"]["choice"], "plan-lathe")
        self.assertNotEqual(original["recordId"], revised["recordId"])
        self.assert_reason("stale-decision", self.controller.consume, original, context)
        self.assertEqual(self.controller.consume(sibling, sibling_context)["recordId"], sibling["recordId"])
        self.assertEqual(self.provider.call_count, 3)
        self.assert_persisted(original)

    def test_changed_policy_or_candidate_set_requires_new_consultation(self):
        original_context = self.context()
        original = self.consult(original_context)
        policy = copy.deepcopy(original_context)
        policy["policyVersion"] = "recovery-policy-v2"
        self.assert_reason("context-digest-mismatch", self.controller.consume, original, policy)
        changed_policy = self.consult(policy)
        candidates = copy.deepcopy(policy)
        candidates["candidates"][0]["estimatedTimeMs"] = 120
        self.assert_reason("context-digest-mismatch", self.controller.consume, changed_policy, candidates)
        changed_candidates = self.consult(candidates)
        self.assert_persisted(changed_candidates)
        self.assertEqual(len({original["recordId"], changed_policy["recordId"], changed_candidates["recordId"]}), 3)
        self.assertEqual(self.provider.call_count, 3)

    def test_provider_projection_has_no_paths_and_receipt_binds_both_contexts(self):
        context = self.context()
        record = self.consult(context)
        projected = self.provider.call_args.args[0]
        self.assertEqual(projected["bindings"], [{"role": "spec", "sha256": context["bindings"][0]["sha256"]}])
        stored = self.assert_persisted(record)
        self.assertEqual(stored["contextDigest"], digest(context))
        self.assertEqual(stored["providerContextDigest"], digest(projected))
        self.assertNotEqual(stored["providerContextDigest"], stored["contextDigest"])
        self.assertEqual(self.controller.consume(record, context)["recordId"], record["recordId"])

    def test_foreign_advisories_cannot_become_decisions_or_fallback_authority(self):
        for fault, reason in (("group", "group-mismatch"), ("subject", "subject-mismatch"),
                              ("digest", "context-digest-mismatch")):
            with self.subTest(fault=fault):
                context = self.context(object_id="foreign-" + fault)

                def foreign(projected):
                    answer = self.advisory(projected)
                    if fault == "group":
                        answer["group"] = "repair"
                    elif fault == "subject":
                        answer["subject"]["objectId"] = "other-cup"
                    else:
                        answer["contextDigest"] = "f" * 64
                    return answer

                self.assert_reason(reason, self.consult, context, provider=foreign)
                self.assertFalse(any(event["record"]["subject"]["objectId"] == context["subject"]["objectId"]
                                     for event in self.events("decision-recorded")))
        self.fallback.assert_not_called()
        self.assertEqual(len(self.events("attempt-pending")), 3)

    def test_foreign_group_object_revision_pass_and_run_cannot_consume_record(self):
        record = self.consult()
        contexts = [(self.context("repair"), "group-mismatch"),
                    (self.context(object_id="other-cup"), "subject-mismatch"),
                    (self.context(revision=2), "subject-mismatch"),
                    (self.context(pass_id="material"), "subject-mismatch")]
        other_run = self.context()
        other_run["subject"]["runId"] = "foreign-run"
        contexts.append((other_run, "subject-mismatch"))
        for context, reason in contexts:
            with self.subTest(subject=context["subject"], group=context["group"]):
                self.assert_reason(reason, self.controller.consume, record, context)
        self.assertEqual(len(self.events("decision-recorded")), 1)
        self.assertEqual(self.events("application-pending"), [])

    def test_consent_revoked_before_consumption_blocks_application_and_completion(self):
        context = self.context("repair")
        record = self.consult(context)
        application = self.controller.begin_application(record, 2)
        self.grant["granted"] = False
        self.assert_reason("consent-changed", self.controller.consume, record, context)
        self.assert_reason("consent-changed", self.controller.begin_application, record, 2)
        self.assert_reason("consent-changed", self.controller.finish_application,
                           application["applicationId"], {"status": "completed"})
        self.assertEqual(self.events("application-finished"), [])
        self.assertEqual(self.events("application-pending")[0]["application"]["status"], "pending")
        self.assert_persisted(record)
        self.assertEqual(self.provider.call_count, 1)

    def test_completed_consultations_reopen_without_duplicate_calls_or_usage(self):
        records = {}
        for group in GROUPS:
            record = self.consult(self.context(group))
            reason = "review-evidence-only" if group == "quality-review" else "answered"
            self.assert_persisted(record, reason=reason)
            records[group] = record
        self.assertEqual(self.provider.call_count, 4)
        self.assertEqual(len(self.events("attempt-result")), 4)
        self.assertEqual(sum(event["tokens"] for event in self.token_events()), 152)
        for _ in range(2):
            self.controller = self.reopen()
            for group, record in records.items():
                cached = self.consult(self.context(group))
                self.assertEqual(cached["recordId"], record["recordId"])
                self.assertEqual(self.controller.consume(cached, self.context(group))["recordId"], record["recordId"])
        self.assertEqual(self.provider.call_count, 4)
        self.assertEqual(len(self.events("attempt-pending")), 4)
        self.assertEqual(len(self.events("attempt-result")), 4)
        self.assertEqual(sum(event["tokens"] for event in self.token_events()), 152)
        self.assertEqual(self.events("attempt-failure"), [])
        self.fallback.assert_not_called()

    def test_pending_application_reopens_same_trial_and_cannot_implicitly_fork(self):
        context = self.context("build-strategy")
        record = self.consult(context)
        pending = self.controller.begin_application(record, 2)
        self.assertEqual((pending["inputRevision"], pending["trialRevision"], pending["status"]), (1, 2, "pending"))
        successor = self.workspace / "evidence" / "trial-2.json"
        successor.write_text('{"objectId":"cup","revision":2,"components":["body","handle"]}\n')
        self.controller = self.reopen()
        cached = self.consult(context)
        resumed = self.controller.begin_application(cached, 2)
        self.assertEqual(resumed["applicationId"], pending["applicationId"])
        self.assertEqual(resumed["status"], "pending")
        self.assert_reason("application-conflict", self.controller.begin_application, cached, 3)
        self.assertEqual(len(self.events("application-pending")), 1)
        outcome = {"status": "completed", "reasonCode": "trial-written"}
        finished = self.controller.finish_application(pending["applicationId"], outcome)
        self.assertEqual(finished["status"], "completed")
        self.controller = self.reopen()
        self.consult(context)
        repeat = self.controller.begin_application(cached, 2)
        self.assertEqual(repeat["status"], "completed")
        self.assertEqual(self.controller.finish_application(pending["applicationId"], outcome), finished)
        self.assertEqual(len(self.events("application-pending")), 1)
        self.assertEqual(len(self.events("application-finished")), 1)
        self.assertEqual(self.events("application-finished")[0]["application"]["trialRevision"], 2)
        self.assertEqual(self.provider.call_count, 1)

    def test_distinct_decisions_cannot_apply_twice_to_same_input_subject(self):
        strategy = self.consult(self.context("build-strategy"))
        pending = self.controller.begin_application(strategy, 2)
        repair = self.consult(self.context("repair"))
        self.assert_reason("application-conflict", self.controller.begin_application, repair, 3)
        self.assertEqual(len(self.events("application-pending")), 1)
        self.assertEqual(self.events("application-pending")[0]["application"]["applicationId"], pending["applicationId"])

    def test_outside_input_edit_blocks_pending_trial_resume_and_finish(self):
        context = self.context("repair")
        record = self.consult(context)
        pending = self.controller.begin_application(record, 2)
        self.evidence.write_text('{"objectId":"cup","revision":1,"components":["body"]}\n')
        self.assert_reason("stale-evidence", self.controller.begin_application, record, 2)
        self.assert_reason("stale-evidence", self.controller.finish_application,
                           pending["applicationId"], {"status": "completed"})
        self.assertEqual(self.events("application-finished"), [])
        self.assertEqual(len(self.events("application-pending")), 1)

    def test_rejected_trial_is_durable_and_cannot_later_be_completed(self):
        context = self.context("repair")
        record = self.consult(context)
        pending = self.controller.begin_application(record, 2)
        rejected = self.controller.finish_application(pending["applicationId"],
                                                       {"status": "rejected", "reasonCode": "protected-regression"})
        self.controller = self.reopen()
        self.consult(context)
        self.assertEqual(self.controller.begin_application(record, 2)["status"], "rejected")
        self.assert_reason("application-conflict", self.controller.finish_application,
                           pending["applicationId"], {"status": "completed"})
        self.assertEqual(self.events("application-finished")[0]["application"], rejected)
        self.assertEqual(len(self.events("application-finished")), 1)

    def test_refusal_is_run_wide_after_reopen_even_when_grant_changes(self):
        self.grant["granted"] = False
        first = self.consult()
        self.assert_persisted(first)
        self.assertEqual(first["unavailableReason"], "consent-declined")
        self.controller = self.reopen()
        self.grant["granted"] = True
        second = self.consult(self.context("quality-review"))
        self.assert_persisted(second, reason="review-evidence-only")
        self.assertEqual(second["unavailableReason"], "consent-declined")
        self.assertEqual(second["decider"], "model:gpt-sol")
        self.provider.assert_not_called()
        self.assertEqual(self.fallback.call_count, 2)
        refusal = [event for event in self.events("unavailable") if event["reasonCode"] == "consent-declined"]
        self.assertEqual(refusal[0]["scope"], "run")

    def test_program_probe_not_installation_absence_alone_proves_run_wide_outage(self):
        first = self.consult(provider=None, availability=lambda: {"status": "not-installed"})
        self.assert_persisted(first)
        self.assertEqual(first["unavailableReason"], "not-installed")
        self.controller = self.reopen()
        second = self.consult(self.context("repair", object_id="other-cup"))
        self.assert_persisted(second)
        self.assertEqual(second["unavailableReason"], "not-installed")
        self.provider.assert_not_called()
        self.assertEqual(self.fallback.call_count, 2)
        self.assertEqual(self.events("unavailable")[0]["scope"], "run")

    def test_transient_provider_failures_do_not_disable_next_object(self):
        for index, reason in enumerate(("timeout", "provider-error", "malformed", "out-of-set")):
            with self.subTest(reason=reason):
                context = self.context(object_id="failed-" + str(index))
                failed = self.consult(context, provider=Mock(side_effect=DecisionError(reason)))
                self.assert_persisted(failed)
                self.assertEqual(failed["unavailableReason"], reason)
                self.assertEqual(failed["decider"], "model:gpt-sol")
                self.controller = self.reopen()
                healthy = self.consult(self.context("build-strategy", object_id="healthy-" + str(index)))
                self.assert_persisted(healthy)
                self.assertEqual(healthy["decider"], "jev:jev-1.14.0")
                self.assertEqual(self.events("attempt-failure")[-1]["reasonCode"], reason)
        self.assertEqual(self.provider.call_count, 4)
        self.assertEqual(self.fallback.call_count, 4)
        self.assertEqual(self.events("unavailable"), [])

    def test_missing_group_capability_does_not_disable_other_groups(self):
        first = self.consult(self.context("repair"), availability=lambda: {"status": "capability-unavailable"})
        self.assert_persisted(first)
        self.controller = self.reopen()
        self.assert_persisted(self.consult(self.context("build-strategy")))
        self.assert_persisted(self.consult(self.context()))
        self.assertEqual(self.provider.call_count, 2)
        self.assertEqual(self.fallback.call_count, 1)
        self.assertEqual(self.events("unavailable")[0]["scope"], "group")

    def test_valid_uncertain_advice_is_cached_and_never_model_overridden(self):
        def uncertain(projected):
            answer = self.advisory(projected)
            answer["answers"]["selection"]["probabilities"] = {"plan-lathe": 0.6, "plan-mesh": 0.4}
            return answer

        self.provider.side_effect = uncertain
        context = self.context("build-strategy")
        record = self.consult(context)
        self.assert_persisted(record, "uncertain", "choice-uncertain")
        self.controller = self.reopen()
        self.assertEqual(self.consult(context)["recordId"], record["recordId"])
        self.assert_reason("application-not-authorized", self.controller.begin_application, record, 2)
        self.assertEqual(self.provider.call_count, 1)
        self.fallback.assert_not_called()
        self.assertEqual(self.events("application-pending"), [])

    def test_all_groups_share_one_deadline_across_revisions_and_reopens(self):
        self.assert_persisted(self.consult())
        self.now += 3000
        self.controller = self.reopen()
        self.assert_persisted(self.consult(self.context("build-strategy", revision=2)))
        self.now += 599
        self.assert_persisted(self.consult(self.context("quality-review", revision=2)), reason="review-evidence-only")
        self.now += 1
        self.controller = self.reopen()
        record = self.consult(self.context("repair", revision=3, pass_id="material"))
        self.assert_persisted(record, "request-input", "deadline-exhausted")
        self.assertEqual(self.provider.call_count, 3)
        self.assertEqual(len(self.events("object-started")), 1)
        self.assertEqual(self.events("object-started")[0]["startedAt"], 1000.0)
        self.fallback.assert_not_called()

    def test_shared_token_budget_counts_fresh_calls_but_not_cached_decisions(self):
        self.grant["tokenBudget"] = 166
        route_context = self.context()
        route = self.consult(route_context)
        self.assert_persisted(route)
        self.controller = self.reopen()
        self.assertEqual(self.consult(route_context)["recordId"], route["recordId"])
        self.assert_persisted(self.consult(self.context("build-strategy")))
        self.controller = self.reopen()
        blocked = self.consult(self.context("repair"))
        self.assert_persisted(blocked, "request-input", "budget-exhausted")
        self.assertEqual(self.provider.call_count, 2)
        self.assertEqual(sum(event["tokens"] for event in self.token_events()), 76)
        self.assertEqual(len(self.events("attempt-pending")), 2)
        self.fallback.assert_not_called()

    def test_ambiguous_paid_call_retains_reservation_across_process_reopen(self):
        self.grant["tokenBudget"] = 128
        self.provider.side_effect = SimulatedProviderCrash()
        with self.assertRaises(SimulatedProviderCrash):
            self.consult()
        self.assertEqual(len(self.events("attempt-pending")), 1)
        self.assertEqual(self.events("decision-recorded"), [])
        self.controller = self.reopen()
        self.provider.side_effect = self.advisory
        blocked = self.consult(self.context("repair", object_id="other-cup"))
        self.assert_persisted(blocked, "request-input", "budget-exhausted")
        self.assertEqual(self.provider.call_count, 1)
        self.assertEqual(len(self.events("attempt-pending")), 1)
        self.assertEqual(self.token_events(), [])
        self.assertEqual(self.events("attempt-pending")[0]["reservation"], 128)
        self.fallback.assert_not_called()

    def test_missing_provider_evidence_requests_input_without_model_override(self):
        self.provider.side_effect = DecisionError("missing-evidence")
        record = self.consult(self.context("quality-review"))
        self.assert_persisted(record, "request-input", "missing-evidence")
        self.assertEqual(self.provider.call_count, 1)
        failure = self.events("attempt-failure")[-1]
        self.assertEqual(failure["reasonCode"], "missing-evidence")
        self.assertEqual(failure["tokens"], 0)
        self.assertEqual(len(self.events("attempt-pending")), 1)
        self.fallback.assert_not_called()
        self.controller = self.reopen()
        self.provider.side_effect = self.advisory
        healthy = self.consult(self.context("repair", object_id="other-cup"))
        self.assert_persisted(healthy)
        self.assertEqual(healthy["decider"], "jev:jev-1.14.0")
        self.assertEqual(self.provider.call_count, 2)
        self.fallback.assert_not_called()

    def test_missing_bound_review_evidence_never_reaches_provider(self):
        context = self.context("quality-review")
        context["bindings"] = [binding for binding in context["bindings"]
                               if binding["role"] != "render"]
        record = self.consult(context)
        self.assert_persisted(record, "request-input", "missing-evidence")
        self.provider.assert_not_called()
        self.fallback.assert_not_called()
        self.assertEqual(self.events("attempt-pending"), [])
        self.assertEqual(self.token_events(), [])

    def test_provider_cannot_exceed_reserved_token_bound_and_still_answer(self):
        def overrun(projected):
            answer = self.advisory(projected)
            answer["usage"] = {"input_tokens": 129, "output_tokens": 0}
            return answer

        record = self.consult(provider=overrun)
        self.assert_persisted(record, "request-input", "budget-overrun")
        self.assertEqual(sum(event["tokens"] for event in self.token_events()), 129)
        self.assertTrue(self.events("attempt-result")[-1]["overrun"])
        self.fallback.assert_not_called()


    def test_cached_choice_cannot_start_new_application_after_deadline(self):
        record = self.consult(self.context("repair"))
        self.now += 3600
        self.controller = self.reopen()
        self.consult(self.context("repair"))
        self.assert_reason("deadline-exhausted", self.controller.begin_application, record, 2)
        self.assertEqual(self.events("application-pending"), [])
        self.assertEqual(self.provider.call_count, 1)

    def test_correction_ceilings_survive_reopen_and_group_switch(self):
        initial = self.consult(self.context("build-strategy"))
        application = self.controller.begin_application(initial, 2)
        self.controller.finish_application(application["applicationId"], {"status": "rejected"})
        self.controller = self.reopen()
        for revision in range(2, 8):
            pass_id = "geometry" if revision <= 4 else "material"
            context = self.context("repair", revision=revision, pass_id=pass_id)
            record = self.consult(context)
            application = self.controller.begin_application(record, revision + 1)
            self.controller.finish_application(application["applicationId"], {"status": "rejected"})
            self.controller = self.reopen()
            if revision == 4:
                exhausted = self.consult(self.context("repair", revision=5))
                self.assert_persisted(exhausted, "request-input", "correction-budget-exhausted")
        continued = self.consult(self.context("build-strategy", revision=8, pass_id="rig"))
        self.assert_persisted(continued, "answered", "answered")
        exhausted = self.consult(self.context("repair", revision=8, pass_id="rig"))
        self.assert_persisted(exhausted, "request-input", "correction-budget-exhausted")
        self.assertEqual(len(self.events("application-pending")), 7)
        self.assertEqual(self.provider.call_count, 8)
        self.fallback.assert_not_called()

    def test_changed_file_hash_blocks_consult_and_consumption_without_new_call(self):
        context = self.context()
        record = self.consult(context)
        self.evidence.write_bytes(b"outside edit to bound input\n")
        self.assert_reason("stale-evidence", self.consult, context)
        self.assert_reason("stale-evidence", self.controller.consume, record, context)
        self.assertEqual(self.provider.call_count, 1)
        self.assertEqual(len(self.events("decision-recorded")), 1)
        self.fallback.assert_not_called()

    def test_relative_absolute_and_control_paths_are_rejected_before_provider(self):
        invalid_paths = ["../outside.json", str(self.evidence), "evidence/../evidence/subject.json",
                         "evidence//subject.json", "evidence\\subject.json", ".img2threejs/route/lease.json"]
        for path in invalid_paths:
            with self.subTest(path=path):
                context = self.context()
                context["bindings"][0]["path"] = path
                self.assert_reason("unsafe-path", self.consult, context)
        self.provider.assert_not_called()
        self.fallback.assert_not_called()
        self.assertEqual(self.events("attempt-pending"), [])

    def test_evidence_file_and_directory_symlinks_cannot_escape_workspace(self):
        outside = self.root / "outside"
        outside.mkdir()
        shutil.copyfile(self.evidence, outside / "subject.json")
        (self.workspace / "linked-file.json").symlink_to(outside / "subject.json")
        (self.workspace / "linked-directory").symlink_to(outside, target_is_directory=True)
        for relative in ("linked-file.json", "linked-directory/subject.json"):
            with self.subTest(relative=relative):
                context = self.context()
                context["bindings"][0]["path"] = relative
                self.assert_reason("unsafe-path", self.consult, context)
        self.provider.assert_not_called()
        self.fallback.assert_not_called()
        self.assertEqual(self.events("attempt-pending"), [])
        self.assertEqual((outside / "subject.json").read_bytes(), (FIXTURES / "subject.json").read_bytes())

    def test_symlink_replacing_bound_file_blocks_cached_record_consumption(self):
        context = self.context("repair")
        record = self.consult(context)
        outside = self.root / "same-bytes.json"
        shutil.copyfile(self.evidence, outside)
        self.evidence.unlink()
        self.evidence.symlink_to(outside)
        self.assert_reason("unsafe-path", self.controller.consume, record, context)
        self.assert_reason("unsafe-path", self.controller.begin_application, record, 2)
        self.assertEqual(self.events("application-pending"), [])
        self.assertEqual(self.provider.call_count, 1)

    def test_symlink_control_directory_cannot_write_outside_workspace(self):
        workspace = self.root / "untrusted-workspace"
        workspace.mkdir()
        outside = self.root / "control-target"
        outside.mkdir()
        (workspace / ".img2threejs").symlink_to(outside, target_is_directory=True)
        self.assert_reason("unsafe-path", DecisionController, workspace, "foreign-run")
        self.assertEqual(list(outside.iterdir()), [])

    def test_tampered_record_file_cannot_be_consumed_or_applied(self):
        context = self.context("repair")
        record = self.consult(context)
        path = self.workspace / ".img2threejs" / "decisions" / "repair" / (record["recordId"] + ".json")
        forged = json.loads(path.read_text())
        forged["answers"]["selection"]["choice"] = "plan-mesh"
        path.write_text(json.dumps(forged) + "\n")
        self.assert_reason("record-mismatch", self.controller.consume, record, context)
        self.assert_reason("record-mismatch", self.controller.begin_application, record, 2)
        self.assertEqual(self.events("application-pending"), [])
        self.assertEqual(self.provider.call_count, 1)

    def test_tampered_journal_blocks_reopen_without_repairing_chain(self):
        self.consult()
        path = self.workspace / ".img2threejs" / "route" / "journal.jsonl"
        lines = path.read_text().splitlines()
        entry = json.loads(lines[-1])
        entry["previousHash"] = "f" * 64
        lines[-1] = json.dumps(entry)
        corrupted = ("\n".join(lines) + "\n").encode()
        path.write_bytes(corrupted)
        self.assert_reason("journal-broken", self.reopen)
        self.assertEqual(path.read_bytes(), corrupted)
        self.assertEqual(self.provider.call_count, 1)

    def test_lease_survives_constructor_process_exit_and_rejects_concurrent_runs(self):
        workspace = self.root / "process-workspace"
        workspace.mkdir()
        program = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from decision_control import DecisionController, DecisionError\n"
            "try:\n"
            "    DecisionController(sys.argv[2], sys.argv[3])\n"
            "except DecisionError as error:\n"
            "    print(error.reason_code)\n"
            "else:\n"
            "    print('acquired')\n"
        )
        command = [sys.executable, "-c", program, str(SHARED), str(workspace)]
        owner = subprocess.run(command + ["process-owner"], capture_output=True, text=True, timeout=15)
        self.assertEqual(owner.returncode, 0, owner.stderr)
        self.assertEqual(owner.stdout.strip(), "acquired")
        control = workspace / ".img2threejs" / "route"
        lease_before = (control / "lease.json").read_bytes()
        journal_before = (control / "journal.jsonl").read_bytes()
        children = []
        try:
            for run_id in ("contender-one", "contender-two"):
                children.append(subprocess.Popen(command + [run_id], stdout=subprocess.PIPE,
                                                 stderr=subprocess.PIPE, text=True))
            for child in children:
                stdout, stderr = child.communicate(timeout=15)
                self.assertEqual(child.returncode, 0, stderr)
                self.assertEqual(stdout.strip(), "workspace-busy")
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.communicate()
        self.assertEqual((control / "lease.json").read_bytes(), lease_before)
        self.assertEqual((control / "journal.jsonl").read_bytes(), journal_before)
        same_run = DecisionController(workspace, "process-owner")
        same_run.release()
        replacement = DecisionController(workspace, "explicit-replacement")
        self.assertEqual(json.loads((control / "lease.json").read_text())["runId"], "explicit-replacement")
        replacement.release()

    def test_explicit_release_invalidates_old_controller_even_after_new_run_acquires(self):
        self.consult()
        self.controller.release()
        replacement = DecisionController(self.workspace, "replacement-run")
        self.assert_reason("lease-lost", self.consult)
        self.assertEqual(self.events("lease-released")[0]["runId"], "recovery-run")
        lease = json.loads((self.workspace / ".img2threejs" / "route" / "lease.json").read_text())
        self.assertEqual(lease["runId"], "replacement-run")
        replacement.release()


if __name__ == "__main__":
    unittest.main()
