"""Offline seal/link ordering tests; seals never attest image completeness."""
from copy import deepcopy
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


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def reload_rounds(workspace, grant, result):
    try:
        controller = DecisionController(workspace, "round-run")
        with controller.workspace_transaction(consent=lambda: grant) as verify:
            rounds = verify.route_rounds(1)
            links = verify.route_round_links(1)
            result.put({"rounds": [row["roundNumber"] for row in rounds],
                        "links": [row["recordId"] for row in links]})
    except Exception as exc:
        result.put({"error": str(exc)})


class SimulatedCrash(BaseException):
    pass


class RouteRoundJournalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.controller = DecisionController(self.workspace, "round-run", clock=lambda: 1000)
        self.grant = {"granted": True, "purposes": ["routing", "build-strategy", "quality-review", "repair"],
                      "sha256": "a" * 64, "tokenBudget": 10000}
        self.consent = lambda: deepcopy(self.grant)
        self.reference = self.put("reference", "reference.bin", b"offline source bytes")
        self.request_value = {"expectedOutput": "3d", "intent": [], "explicitRoute": None}
        self.request = self.json_binding("route-request", self.request_value)
        self.cup = {"objectId": "cup", "label": "cup", "role": "subject", "approxBox": [.1, .1, .6, .9]}
        self.lamp = {"objectId": "lamp", "label": "lamp", "role": "subject", "approxBox": [.6, .1, .9, .9]}
        self.initial, self.first = self.inputs([self.cup])
        self.missing = .8
        self.provider = Mock(side_effect=self.advisory)

    def put(self, role, path, data):
        target = self.workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"role": role, "path": path, "sha256": hashlib.sha256(data).hexdigest()}

    def json_binding(self, role, value):
        digest = canonical_digest(value)
        return self.put(role, f".img2threejs/route-inputs/{role}/{digest}.json", encode(value))

    def inputs(self, objects, **changes):
        initial = {"reference": self.reference["sha256"], "request": deepcopy(self.request_value),
                   "proposals": deepcopy(objects), "candidates": [{"id": "base:generic", "resolvedSha": "b" * 64}],
                   "policyVersion": "route-policy-v1", "questionSetVersion": "route-questions-v3",
                   "requestedModel": "jev-1.14.0", "consentSnapshotSha256": canonical_digest(self.grant)}
        initial.update(changes)
        return self.json_binding("routing-inputs", initial), self.proposal(objects)

    def proposal(self, objects):
        return self.json_binding("object-proposals", {"kind": "img2.object-proposals", "version": 1,
                                                       "objects": deepcopy(objects)})

    def seal(self, number=1, *, revision=1, proposal=None, initial=None, parent=None, triggers=None):
        return self.controller.seal_route_round(
            route_revision=revision, round_number=number, proposal=proposal or self.first,
            initial_inputs_digest=(initial or self.initial)["sha256"],
            parent_round_sha256=parent, trigger_decision_ids=[] if triggers is None else triggers)

    def context(self, obj=None, *, pass_id="decomposition", objects=None):
        obj = obj or self.cup
        bindings = [self.reference, self.request]
        bindings.extend(self.json_binding("object-proposal", item) for item in (objects or [obj]))
        requirements = {"criticalFeatures": [], "requiredViews": []}
        observations = {"images": [{"role": "reference", "asset": "source"}]}
        candidates = [{"id": "keep"}]
        if pass_id == "decomposition":
            candidates = []
            requirements["decomposition"] = {"image": "source", "objects": [{
                "id": obj["objectId"], "label": obj["label"], "approxBox": obj["approxBox"]}],
                "inventory": [{"id": item["objectId"], "label": item["label"], "role": item["role"],
                               "approxBox": item["approxBox"]} for item in (objects or [obj])]}
        if pass_id == "routing":
            handles = {"keep": "base:generic"}
            bindings.append(self.json_binding("candidate-handles", handles))
        return {"kind": "img2.route-context", "version": 1, "group": "routing",
                "subject": {"runId": "round-run", "objectId": obj["objectId"], "revision": 1, "passId": pass_id},
                "bindings": bindings, "candidates": candidates, "observations": observations,
                "requirements": requirements, "policyVersion": "route-policy-v1",
                "questionSetVersion": "route-questions-v3", "requestedModel": "jev-1.14.0"}

    def advisory(self, context):
        self.assertNotIn("routeRound", context["requirements"])
        self.assertNotIn("route_round", context)
        answers = {"selection": {"type": "choice", "choice": "keep", "probabilities": {"keep": 1.0},
                                  "confidence": .9}, "anyFits": {"type": "noul", "noul": 1.0}} if context["candidates"] else {}
        for obj in context["requirements"].get("decomposition", {}).get("objects", []):
            left, top, right, bottom = obj["approxBox"]
            answers.update({f"object:{obj['id']}:visible": {"type": "noul", "noul": 1.0},
                            f"object:{obj['id']}:box": {"type": "box", "confidence": .9,
                                "box": {"left": left, "top": top, "right": right, "bottom": bottom}},
                            "missingObjects": {"type": "noul", "noul": self.missing}})
        return {"kind": "img2.decision-advisory", "version": 1, "group": context["group"],
                "subject": context["subject"], "contextDigest": canonical_digest(context),
                "model": context["requestedModel"], "answers": answers,
                "usage": {"input_tokens": 4, "output_tokens": 2}}

    def consult(self, context=None, *, revision=1, number=1, scoped=True, **overrides):
        kwargs = {"provider": self.provider, "availability": lambda: {"status": "present"},
                  "consent": self.consent, "request_token_limit": 100}
        if scoped:
            kwargs["route_round"] = {"routeRevision": revision, "roundNumber": number}
        kwargs.update(overrides)
        return self.controller.consult(context or self.context(), **kwargs)

    def reject(self, reason, function, *args, **kwargs):
        with self.assertRaises(DecisionError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.reason_code, reason)

    def events(self):
        return [json.loads(row) for row in self.controller.journal_path.read_bytes().splitlines()]

    def rewrite_events(self, events):
        previous = "0" * 64
        rewritten = []
        for index, entry in enumerate(events):
            body = {"sequence": index, "previousHash": previous, "event": entry["event"], "data": entry["data"]}
            entry = dict(body, hash=canonical_digest(body))
            rewritten.append(entry)
            previous = entry["hash"]
        self.controller.journal_path.write_bytes(b"".join(encode(entry) + b"\n" for entry in rewritten))

    def test_seal_precedes_dispatch_and_round_metadata_does_not_change_context(self):
        context = self.context()
        digest = canonical_digest(context)
        self.reject("route-round-unsealed", self.consult, context)
        self.provider.assert_not_called()
        first = self.seal()
        record = self.consult(context)
        self.assertEqual(record["contextDigest"], digest)
        events = self.events()
        names = [entry["event"] for entry in events]
        self.assertLess(names.index("route-round-sealed"), names.index("attempt-pending"))
        self.assertLess(names.index("decision-recorded"), names.index("route-round-linked"))
        self.assertEqual(first["proposal"], self.first)
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            self.assertEqual(verify.route_round_links(1)[0]["recordId"], record["recordId"])

    def test_first_seal_cannot_follow_unmarked_legacy_model_consultation(self):
        context = self.context()
        record = self.consult(context, scoped=False)
        original = self.controller.journal_path.read_bytes()
        self.reject("route-round-order", self.seal)
        self.assertEqual(self.controller.journal_path.read_bytes(), original)
        self.assertEqual(self.controller.consume(record, context, consent=self.consent), record)
        reopened = DecisionController(self.workspace, "round-run", clock=lambda: 1000)
        with reopened.workspace_transaction(consent=self.consent) as verify:
            self.assertEqual(verify.route_rounds(1), ())

    def test_first_seal_cannot_follow_unmarked_inflight_attempt(self):
        def crash(context):
            raise SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            self.consult(scoped=False, provider=crash)
        self.reject("route-round-order", self.seal)

    def test_repeat_is_immutable_idempotent_and_rechecks_actual_bytes(self):
        first = self.seal()
        original = self.controller.journal_path.read_bytes()
        self.assertEqual(self.seal(), first)
        self.assertEqual(self.controller.journal_path.read_bytes(), original)
        self.reject("route-round-conflict", self.seal, proposal=self.proposal([self.lamp]))
        (self.workspace / self.first["path"]).write_bytes(b"tampered")
        self.reject("stale-evidence", self.seal)
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            self.reject("stale-evidence", verify.route_rounds, 1)

    def test_first_snapshot_and_proposals_must_match_and_inputs_stay_current(self):
        self.reject("route-round-proposal-mismatch", self.seal, proposal=self.proposal([self.lamp]))
        self.seal()
        (self.workspace / self.initial["path"]).write_bytes(b"changed initial inputs")
        self.reject("stale-evidence", self.consult)
        self.provider.assert_not_called()

    def test_unscoped_consult_after_seal_is_refused(self):
        self.seal()
        self.reject("route-round-required", self.consult, scoped=False)
        self.provider.assert_not_called()

    def test_second_round_requires_exact_first_hash_and_authenticated_trigger(self):
        self.seal()
        record = self.consult()
        second = self.proposal([self.lamp])
        self.reject("route-round-order", self.seal, 2, proposal=second, parent="0" * 64,
                    triggers=[record["recordId"]])
        self.reject("invalid-route-round-trigger", self.seal, 2, proposal=second,
                    parent=self.first["sha256"], triggers=["f" * 64])
        sealed = self.seal(2, proposal=second, parent=self.first["sha256"], triggers=[record["recordId"]])
        self.assertEqual(sealed["roundNumber"], 2)
        self.consult(self.context(self.lamp), number=2)
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            self.assertEqual(len(verify.route_rounds(1)), 2)
            self.assertEqual(len(verify.route_round_links(1)), 2)

    def test_wrong_pass_low_missing_and_other_revision_triggers_fail(self):
        self.seal()
        self.missing = .69
        low = self.consult()
        second = self.proposal([self.lamp])
        self.reject("invalid-route-round-trigger", self.seal, 2, proposal=second,
                    parent=self.first["sha256"], triggers=[low["recordId"]])
        choice = self.consult(self.context(pass_id="crop-choice"))
        self.reject("invalid-route-round-trigger", self.seal, 2, proposal=second,
                    parent=self.first["sha256"], triggers=[choice["recordId"]])
        initial2, proposal2 = self.inputs([self.cup, self.lamp])
        self.seal(revision=2, initial=initial2, proposal=proposal2)
        extra = self.proposal([{**self.lamp, "objectId": "other"}])
        self.reject("invalid-route-round-trigger", self.seal, 2, revision=2, proposal=extra,
                    initial=initial2, parent=proposal2["sha256"], triggers=[low["recordId"]])

    def test_second_before_first_third_round_or_bad_first_parent_fail(self):
        self.reject("route-round-order", self.seal, 2)
        self.reject("route-round-order", self.seal, parent="a" * 64)
        self.seal()
        self.reject("route-round-order", self.seal, 3)

    def test_foreign_subject_nonmember_and_changed_policy_fail_before_provider(self):
        self.seal()
        foreign = self.context()
        foreign["subject"]["runId"] = "foreign-run"
        self.reject("route-round-subject-mismatch", self.consult, foreign)
        self.reject("route-round-proposal-mismatch", self.consult, self.context(self.lamp))
        changed = self.context()
        changed["policyVersion"] = "unapproved-policy"
        self.reject("route-round-context-mismatch", self.consult, changed)
        self.provider.assert_not_called()

    def test_missing_or_failed_consent_suspends_without_dispatch_or_link(self):
        self.seal()

        def failed_probe():
            raise OSError("fixture consent probe failure")

        for callback, reason in ((None, "consent-unproven"), (failed_probe, "consent-probe-error")):
            with self.subTest(reason=reason):
                record = self.consult(consent=callback)
                self.assertEqual(record["status"], "request-input")
                self.assertEqual(record["reasonCode"], reason)
        names = [entry["event"] for entry in self.events()]
        self.assertNotIn("route-round-dispatch", names)
        self.assertNotIn("route-round-linked", names)
        self.provider.assert_not_called()

    def test_new_inflight_attempt_cannot_borrow_older_context_link(self):
        self.seal()
        self.consult()
        self.grant["sha256"] = "d" * 64
        # Deliberately bypass the scoped API to emulate an untrusted legacy
        # writer with the same context but a new consent/attempt identity.
        def crash(context):
            raise SimulatedCrash()

        with self.assertRaises(SimulatedCrash):
            self.controller._consult(self.context(), provider=crash,
                                     availability=lambda: {"status": "present"},
                                     consent=self.consent, request_token_limit=100)
        initial, proposal = self.inputs([self.cup, self.lamp])
        self.reject("route-round-order", self.seal, revision=2, initial=initial, proposal=proposal)

    def test_failed_jev_then_authenticated_model_fallback_can_trigger_round_two(self):
        self.seal()

        def failing_provider(context):
            raise TimeoutError("fixture timeout")

        def fallback(context):
            result = self.advisory(context)
            result["model"] = "fixture-model"
            return result

        record = self.consult(provider=failing_provider, fallback=fallback)
        self.assertEqual(record["decider"], "model:fixture-model")
        self.seal(2, proposal=self.proposal([self.lamp]), parent=self.first["sha256"],
                  triggers=[record["recordId"]])

    def test_round_views_are_immutable_and_expire_with_the_transaction(self):
        self.seal()
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            rounds = verify.route_rounds(1)
            with self.assertRaises(TypeError):
                rounds[0]["roundNumber"] = 2
            with self.assertRaises(TypeError):
                rounds[0]["proposal"]["path"] = "elsewhere"
            get_rounds, get_links = verify.route_rounds, verify.route_round_links
        self.reject("transaction-closed", get_rounds, 1)
        self.reject("transaction-closed", get_links, 1)

    def test_unchanged_object_rebinds_to_new_aggregate_without_provider_repeat(self):
        self.seal()
        context = self.context()
        record = self.consult(context)
        second_inputs, second_proposal = self.inputs([self.cup, self.lamp])
        self.seal(revision=2, initial=second_inputs, proposal=second_proposal)
        reused = self.consult(context, revision=2)
        self.assertEqual(record, reused)
        self.assertEqual(self.provider.call_count, 1)
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            self.assertEqual(verify.route_round_links(2)[0]["recordId"], record["recordId"])
            self.assertEqual(verify.route_round_links(2)[0]["proposalShas"],
                             (canonical_digest(self.cup),))

    def test_changed_consent_cannot_rebind_cached_decision(self):
        self.seal()
        record = self.consult()
        self.grant["sha256"] = "c" * 64
        self.reject("consent-changed", self.consult)
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            self.reject("consent-changed", verify.route_round_links, 1)
        self.assertEqual(self.provider.call_count, 1)
        self.assertTrue(record["recordId"])

    def test_unlinked_result_created_by_bypassing_scoped_api_is_not_upgraded(self):
        initial, first = self.inputs([self.cup, self.lamp])
        self.seal(initial=initial, proposal=first)
        context = self.context(self.lamp)
        self.controller._consult(context, provider=self.provider, availability=lambda: {"status": "present"},
                                 consent=self.consent, request_token_limit=100)
        self.reject("route-round-unlinked-record", self.consult, context)

    def test_group_routing_can_link_proposals_from_both_rounds(self):
        self.seal()
        trigger = self.consult()
        self.seal(2, proposal=self.proposal([self.lamp]), parent=self.first["sha256"],
                  triggers=[trigger["recordId"]])
        context = self.context(pass_id="routing", objects=[self.cup, self.lamp])
        self.consult(context, number=2)
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            link = verify.route_round_links(1)[-1]
            self.assertEqual(link["objectIds"], ("cup", "lamp"))

    def test_rehashed_forged_link_and_broken_round_sequence_are_rejected_on_load(self):
        self.seal()
        self.consult()
        original = self.events()
        forged = deepcopy(original)
        forged[-1]["data"]["proposalShas"] = ["f" * 64]
        self.rewrite_events(forged)
        self.reject("journal-broken", DecisionController, self.workspace, "round-run")
        broken = deepcopy(original)
        seal = next(entry for entry in broken if entry["event"] == "route-round-sealed")
        seal["data"]["roundNumber"] = 2
        self.rewrite_events(broken)
        self.reject("journal-broken", DecisionController, self.workspace, "round-run")

    def test_hash_chain_tamper_and_trigger_sidecar_tamper_are_rejected(self):
        self.seal()
        record = self.consult()
        path = self.workspace / f".img2threejs/decisions/routing/{record['recordId']}.json"
        path.write_bytes(encode({**record, "answers": {}}))
        self.reject("record-mismatch", self.seal, 2, proposal=self.proposal([self.lamp]),
                    parent=self.first["sha256"], triggers=[record["recordId"]])
        events = self.events()
        events[-1]["data"]["objectIds"] = ["fake"]
        self.controller.journal_path.write_bytes(b"".join(encode(entry) + b"\n" for entry in events))
        self.reject("journal-broken", DecisionController, self.workspace, "round-run")

    def test_fresh_process_reloads_seals_and_authenticated_links(self):
        self.seal()
        record = self.consult()
        context = multiprocessing.get_context("spawn")
        result = context.Queue()
        self.addCleanup(result.close)
        process = context.Process(target=reload_rounds, args=(str(self.workspace), self.grant, result))
        process.start()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
            self.fail("fixture child process did not finish")
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(result.get(timeout=2), {"rounds": [1], "links": [record["recordId"]]})


if __name__ == "__main__":
    unittest.main()
