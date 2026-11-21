"""Offline journal integration, not independent placement or visual qualification."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from forge._shared.decision_control import DecisionController, DecisionError, GROUPS, canonical_digest
from forge._shared.placement_decision import placement_context, consult_placement, consume_placement


class PlacementDecisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.controller = DecisionController(self.root, "run-placement", clock=lambda: 1000)
        self.bindings = []
        for role in ("reference", "inventory", "artifact", "size-prior", "camera", "depth", "support"):
            raw = json.dumps({"fixtureRole": role}).encode()
            path = role + ".json"
            (self.root / path).write_bytes(raw)
            self.bindings.append({"role": role, "path": path, "sha256": hashlib.sha256(raw).hexdigest()})
        self.grant = {"granted": True, "purposes": list(GROUPS), "sha256": "a" * 64, "tokenBudget": 10000}
        self.consent = lambda: deepcopy(self.grant)
        self.provider = Mock(side_effect=self.answer)
        self.fallback = Mock(side_effect=lambda ctx: self.answer(ctx, model="host-fixture"))
        self.callbacks = {"provider": self.provider, "fallback": self.fallback,
                          "availability": lambda: {"status": "present"}, "consent": self.consent,
                          "request_token_limit": 128}

    def context(self, question="size"):
        values = [[1, 1, 1], [2, 2, 2]] if question == "size" else list(range(8))
        return placement_context({"runId": "run-placement", "objectId": "chair", "revision": 1},
            self.bindings, question=question,
            options=[{"id": "option-" + str(i), "value": value} for i, value in enumerate(values)])

    @staticmethod
    def answer(context, model="jev-1.14.0"):
        ids = [candidate["id"] for candidate in context["candidates"]]
        return {"kind": "img2.decision-advisory", "version": 1, "group": context["group"],
            "subject": deepcopy(context["subject"]), "contextDigest": canonical_digest(context), "model": model,
            "answers": {"selection": {"type": "choice", "choice": ids[0], "confidence": 1,
                        "probabilities": {key: float(key == ids[0]) for key in ids}},
                        "anyFits": {"type": "noul", "noul": 1}},
            "usage": {"input_tokens": 10, "output_tokens": 2}}

    def verify(self, record, context):
        return self.controller.consume(record, context, consent=self.consent)

    def test_size_and_yaw_are_separate_journalled_shared_budget_decisions(self):
        size = consult_placement(self.controller, self.context(), **self.callbacks)
        yaw = consult_placement(self.controller, self.context("yaw"), **self.callbacks)
        self.assertEqual(size["answer"]["value"], [1, 1, 1])
        self.assertEqual(yaw["answer"]["value"], 0)
        self.assertNotEqual(size["record"]["recordId"], yaw["record"]["recordId"])
        self.assertEqual(size["answer"]["artifactSha256"], self.bindings[2]["sha256"])
        self.assertEqual(self.provider.call_count, 2)
        replay = consult_placement(self.controller, self.context(), **self.callbacks)
        self.assertEqual(replay["answer"], size["answer"])
        self.assertEqual(self.provider.call_count, 2)
        journal = [json.loads(line) for line in self.controller.journal_path.read_text().splitlines()]
        debits = [row["data"]["tokens"] for row in journal if row["event"] == "attempt-result"]
        self.assertEqual(sum(debits), 24)
        self.fallback.assert_not_called()

    def test_saved_record_revalidates_in_fresh_controller_and_rejects_modified_answer(self):
        result = consult_placement(self.controller, self.context(), **self.callbacks)
        restarted = DecisionController(self.root, "run-placement", clock=lambda: 1001)
        verify = lambda record, context: restarted.consume(record, context, consent=self.consent)
        self.assertEqual(consume_placement(verify, result["context"], result["record"]), result["answer"])
        altered = deepcopy(result["record"])
        altered["answers"]["selection"]["choice"] = "option-1"
        with self.assertRaises(DecisionError) as caught:
            consume_placement(verify, result["context"], altered)
        self.assertEqual(caught.exception.reason_code, "record-mismatch")

    def test_changed_depth_and_revoked_consent_block_consuming_prior_answer(self):
        result = consult_placement(self.controller, self.context(), **self.callbacks)
        self.grant["sha256"] = "b" * 64
        with self.assertRaises(DecisionError) as caught:
            consume_placement(self.verify, result["context"], result["record"])
        self.assertEqual(caught.exception.reason_code, "consent-changed")
        self.grant["sha256"] = "a" * 64
        (self.root / "depth.json").write_bytes(b"changed depth")
        with self.assertRaises(DecisionError) as caught:
            consume_placement(self.verify, result["context"], result["record"])
        self.assertEqual(caught.exception.reason_code, "stale-evidence")

    def test_run_outage_not_routing_decider_controls_subsequent_placement(self):
        self.callbacks["availability"] = lambda: {"status": "not-installed"}
        first = consult_placement(self.controller, self.context(), **self.callbacks)
        self.callbacks["availability"] = lambda: {"status": "present"}
        second = consult_placement(self.controller, self.context("yaw"), **self.callbacks)
        for result in (first, second):
            self.assertEqual(result["answer"]["decider"], "model:host-fixture")
            self.assertEqual(result["answer"]["reasonCode"], "not-installed")
        self.assertEqual(self.fallback.call_count, 2)
        self.provider.assert_not_called()

    def test_uncertain_jev_does_not_run_fallback_or_emit_placement_answer(self):
        def uncertain(context):
            advisory = self.answer(context)
            keys = list(advisory["answers"]["selection"]["probabilities"])
            advisory["answers"]["selection"]["probabilities"] = {key: .5 for key in keys}
            return advisory
        self.provider.side_effect = uncertain
        result = consult_placement(self.controller, self.context(), **self.callbacks)
        self.assertIsNone(result["answer"])
        self.assertEqual(result["record"]["status"], "uncertain")
        self.fallback.assert_not_called()

    def test_out_of_range_duplicate_and_wrong_group_options_are_refused_before_call(self):
        for question, value in (("size", [0, 1, 1]), ("size", [True, 1, 1]), ("yaw", 8), ("yaw", True)):
            context = self.context(question)
            context["candidates"][0]["value"] = value
            with self.subTest(question=question, value=value), self.assertRaises(DecisionError):
                consult_placement(self.controller, context, **self.callbacks)
        context = self.context()
        context["candidates"][1]["value"] = [1, 1, 1]
        with self.assertRaises(DecisionError) as caught:
            consult_placement(self.controller, context, **self.callbacks)
        self.assertEqual(caught.exception.reason_code, "duplicate-placement-value")
        context = self.context()
        context["group"] = "repair"
        with self.assertRaises(DecisionError):
            consult_placement(self.controller, context, **self.callbacks)
        self.provider.assert_not_called()
        self.fallback.assert_not_called()

    def test_missing_support_cannot_consult_even_with_a_valid_camera(self):
        context = self.context()
        context["bindings"] = [row for row in context["bindings"] if row["role"] != "support"]
        with self.assertRaises(DecisionError) as caught:
            consult_placement(self.controller, context, **self.callbacks)
        self.assertEqual(caught.exception.reason_code, "missing-placement-evidence")
        self.provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
