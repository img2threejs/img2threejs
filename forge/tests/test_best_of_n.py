"""Offline real-process tests. Synthetic numeric fixtures are NOT visual proof."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from forge._shared.decision_control import DecisionController, DecisionError, canonical_digest
from forge.stage3_build.best_of_n import BestOfN, CandidateProgram, ReportEvidence


WORKER = r'''
import hashlib, json, os, pathlib, subprocess, sys, time
config = json.loads(sys.argv[1])
root = pathlib.Path.cwd()
if config.get("descendant"):
    subprocess.Popen([sys.executable, "-c", "import pathlib,time;time.sleep(0.8);pathlib.Path(" + repr(config["descendant"]) + ").write_text('escaped')"])
if config.get("sleep"):
    time.sleep(config["sleep"])
if config.get("fail"):
    sys.exit(9)
for role in ("source", "render", "gate-report"):
    (root / role).write_text("synthetic offline fixture: " + role)
hashes = dict(config["inputs"])
for role in ("source", "render", "gate-report"):
    hashes[role] = hashlib.sha256((root / role).read_bytes()).hexdigest()
report = config["report"]
if config.get("badCamera"):
    report["evidence"]["cameraSha256"] = "0" * 64
if config.get("gateFail"):
    report["gateResults"][0]["status"] = "fail"
if config.get("criticalFail"):
    report["features"][0]["status"] = "fail"
if config.get("missingMetric"):
    del report["metrics"]["silhouetteIoU"]
report["metrics"]["interiorDifference"] = config.get("difference", 0.08)
envelope = {"subject":config["subject"], "decisionRef":config["decisionRef"],
            "candidateId":config["candidateId"], "rubricVersion":"fixture-rubric-v1",
            "inputs":hashes, "candidate":report}
(root / "quality.json").write_text(json.dumps(envelope))
(root / "literal-argument.txt").write_text(sys.argv[2])
'''


RESTART_DRIVER = r'''
import json, os, pathlib, sys, time
from forge.tests.test_best_of_n import BestOfNTests
from forge._shared.decision_control import DecisionController, DecisionError
config = json.loads(pathlib.Path(sys.argv[1]).read_text())
case = BestOfNTests()
case.workspace = pathlib.Path(config["workspace"])
case.worker = case.workspace / "fixture_worker.py"
case.shared = config["shared"]
case.contract = config["comparison"]
case.scenarios = config.get("scenarios", {})
case.costs = {"a":"paid", "b":"paid", "c":"paid"}
case.literal = "restart-fixture"
case.started_handles = []
began = time.monotonic()
case.controller = DecisionController(case.workspace, "run-1",
                                    clock=lambda: config["now"] + time.monotonic() - began)
case.consent = lambda: config["grant"]
original_factory = case.factory
def factory(trial):
    with (case.workspace / "executions.jsonl").open("a") as stream:
        stream.write(json.dumps(trial.candidate_id) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return original_factory(trial)
case.factory = factory
original_producer = case.producer
def producer(trial):
    if config["mode"] == "crash-before-completion":
        os._exit(72)
    return original_producer(trial)
case.producer = producer
def approve(request):
    if config["mode"] == "crash-before-b" and request["candidateId"] == "b":
        os._exit(71)
    with (case.workspace / "approvals.jsonl").open("a") as stream:
        stream.write(json.dumps(request["candidateId"]) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return True
try:
    result = case.executor().run(config["record"], config["context"], consent=case.consent, approve_cost=approve)
except DecisionError as exc:
    print(json.dumps({"error":exc.reason_code}))
    sys.exit(2)
print(json.dumps(result))
'''


@unittest.skipUnless(os.name == "posix", "POSIX process-group cancellation required")
class BestOfNTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name).resolve()
        self.addCleanup(self.cleanup)
        self.worker = self.workspace / "fixture_worker.py"
        self.worker.write_text(WORKER)
        self.started = time.monotonic()
        self.offset = 0
        self.clock = lambda: 1000 + self.offset + time.monotonic() - self.started
        self.controller = DecisionController(self.workspace, "run-1", clock=self.clock)
        self.consent = lambda: {"granted": True, "purposes": ["routing", "build-strategy", "quality-review", "repair"],
                                "sha256": "b" * 64, "tokenBudget": 10000}
        self.shared = {}
        for role in ("reference", "camera", "view-set", "quality-contract"):
            path = self.workspace / role
            path.write_text("offline comparison protocol: " + role)
            self.shared[role] = self.binding(role, path)
        self.contract = {"outputKind": "3d-object", "flatness": "volumetric", "rubricVersion": "fixture-rubric-v1",
                         "referenceSha256": self.shared["reference"]["sha256"],
                         "cameraSha256": self.shared["camera"]["sha256"],
                         "viewSetSha256": self.shared["view-set"]["sha256"],
                         "contractSha256": self.shared["quality-contract"]["sha256"],
                         "features": [{"id": "handle", "tier": "critical", "threshold": 3}],
                         "blockingGates": [{"id": "mesh", "producer": "fixture-mesh-v1"}]}
        self.started_handles = []
        self.scenarios = {}
        self.costs = {}
        self.literal = "; touch INJECTED; $(touch ALSO_INJECTED)"

    def cleanup(self):
        for root, dirs, files in os.walk(self.workspace):
            os.chmod(root, 0o700)
            for name in files:
                if not (Path(root) / name).is_symlink():
                    os.chmod(Path(root) / name, 0o600)
        self.temporary.cleanup()

    def binding(self, role, path):
        return {"role": role, "path": str(path.relative_to(self.workspace)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def context(self, group="routing", ids=("a", "b", "c")):
        return {"kind": "img2.route-context" if group == "routing" else "img2.build-strategy-context",
                "version": 1, "group": group,
                "subject": {"runId": "run-1", "objectId": "object-1", "revision": 1, "passId": "geometry"},
                "bindings": [self.shared["reference"]], "candidates": [{"id": handle} for handle in ids],
                "observations": {}, "requirements": {"criticalFeatures": [], "requiredViews": []},
                "policyVersion": "route-quality-policy-v1", "questionSetVersion":
                "route-questions-v1" if group == "routing" else "strategy-questions-v1", "requestedModel": "jev-1.14.0"}

    def decide(self, *, group="routing", probabilities=None, confidence=0.8):
        context = self.context(group)
        probabilities = probabilities or {"a": 0.5, "b": 0.3, "c": 0.2}

        def provider(projected):
            return {"kind": "img2.decision-advisory", "version": 1, "group": group,
                    "subject": projected["subject"], "contextDigest": canonical_digest(projected),
                    "model": "jev-1.14.0", "answers": {
                        "selection": {"type": "choice", "choice": max(probabilities, key=probabilities.get),
                                      "probabilities": probabilities, "confidence": confidence},
                        "anyFits": {"type": "noul", "noul": 1}},
                    "usage": {"input_tokens": 10, "output_tokens": 2}}
        decision = self.controller.consult(context, provider=provider, availability=lambda: {"status": "present"},
                                           consent=self.consent, request_token_limit=128)
        return decision, context

    def measured_fixture(self, handle):
        return {"id": handle, "outputKind": "3d-object", "flatness": "volumetric",
                "gateResults": [{"id": "mesh", "producer": "fixture-mesh-v1", "status": "pass"}],
                "measurements": {"width": 2, "height": 1, "depth": 0.4, "frontArea": 2, "sideArea": 0.6,
                                 "selfIntersectionCount": 0, "partCoveragePassed": True},
                "views": [0, 90, 180, 270],
                "features": [{"id": "handle", "tier": "critical", "threshold": 3, "status": "pass", "score": 3.5}],
                "metrics": {"silhouetteIoU": 0.9, "interiorDifference": 0.08, "orbitConsistency": 0.9},
                "evidence": {key: self.contract[key] for key in
                             ("referenceSha256", "cameraSha256", "viewSetSha256")}, "cost": 0, "timeMs": 123}

    def factory(self, trial):
        self.started_handles.append(trial.candidate_id)
        config = {"subject": trial.subject, "decisionRef": trial.decision_ref, "candidateId": trial.candidate_id,
                  "inputs": {role: binding["sha256"] for role, binding in self.shared.items()},
                  "report": self.measured_fixture(trial.candidate_id)}
        config.update(self.scenarios.get(trial.candidate_id, {}))
        return [sys.executable, str(self.worker), json.dumps(config), self.literal]

    def producer(self, trial):
        bindings = list(self.shared.values()) + [self.binding(role, trial.directory / role)
                                                for role in ("source", "render", "gate-report")]
        return ReportEvidence(self.binding("quality-report", trial.directory / "quality.json"), tuple(bindings))

    def executor(self, extra=None):
        programs = {handle: CandidateProgram(self.costs.get(handle, "local"), self.factory, self.producer)
                    for handle in ("a", "b", "c")}
        programs.update(extra or {})
        return BestOfN(self.controller, programs, self.contract)

    def run_trials(self, **kwargs):
        record, context = self.decide()
        return self.executor().run(record, context, consent=self.consent, **kwargs)

    def assert_reason(self, reason, callback):
        with self.assertRaises(DecisionError) as caught:
            callback()
        self.assertEqual(caught.exception.reason_code, reason)

    def test_default_top_two_real_commands_match_and_rank(self):
        self.scenarios["b"] = {"difference": 0.01}
        result = self.run_trials()
        self.assertEqual(self.started_handles, ["a", "b"])
        self.assertEqual(result["selected"], "b")
        self.assertEqual(result["action"], "continue")
        for attempt in result["attempts"]:
            self.assertGreater(attempt["elapsedMs"], 0)
            self.assertIsNone(attempt["inputTokens"])
            self.assertIsNone(attempt["outputTokens"])
            directory = self.workspace / attempt["directory"]
            self.assertTrue((directory / "quality.json").is_file())
            self.assertEqual((directory / "quality.json").stat().st_mode & 0o222, 0)
        self.assertGreater(result["elapsedMs"], 0)

    def test_three_strategy_trials_and_no_arbitrary_registry_candidate(self):
        record, context = self.decide(group="build-strategy")
        extra = {"forged": CandidateProgram("local", lambda trial: self.fail("arbitrary program ran"), self.producer)}
        result = self.executor(extra).run(record, context, consent=self.consent, n=3)
        self.assertEqual(self.started_handles, ["a", "b", "c"])
        self.assertEqual(len(result["attempts"]), 3)

    def test_duplicate_context_and_forged_record_fail_authentication(self):
        record, context = self.decide()
        forged = deepcopy(record)
        forged["candidates"].append({"id": "evil"})
        self.assert_reason("record-mismatch", lambda: self.executor().run(forged, context, consent=self.consent))
        duplicate = deepcopy(context)
        duplicate["candidates"].append({"id": "a"})
        self.assert_reason("invalid-candidates", lambda: self.executor().run(record, duplicate, consent=self.consent))
        self.assertEqual(self.started_handles, [])

    def test_n_above_three_and_boolean_refused(self):
        record, context = self.decide()
        for n in (1, 4, True):
            self.assert_reason("invalid-best-of-n", lambda: self.executor().run(record, context, consent=self.consent, n=n))

    def test_high_confidence_wide_margin_does_not_trigger_trials(self):
        record, context = self.decide(probabilities={"a": 0.9, "b": 0.08, "c": 0.02})
        self.assert_reason("best-of-n-not-authorized", lambda: self.executor().run(record, context, consent=self.consent))
        self.assertEqual(self.started_handles, [])

    def test_heavy_local_requires_no_cost_approval(self):
        self.costs = {"a": "heavy", "b": "heavy"}
        self.assertEqual(self.run_trials()["action"], "continue")

    def test_paid_and_licensed_need_separate_exact_true_approval(self):
        self.costs = {"a": "paid", "b": "licensed"}
        approvals = []
        def approve(request):
            approvals.append(request)
            return request["candidateId"] == "a"
        result = self.run_trials(approve_cost=approve)
        self.assertEqual([request["candidateId"] for request in approvals], ["a", "b"])
        self.assertEqual([request["costClass"] for request in approvals], ["paid", "licensed"])
        self.assertTrue(all(request["decisionRef"] == result["decisionRef"] for request in approvals))
        self.assertEqual(self.started_handles, ["a"])
        self.assertIsNone(result["selected"])
        self.assertEqual(result["reasonCode"], "cost-approval-required")

    def test_missing_cost_approval_starts_nothing(self):
        self.costs["a"] = "paid"
        result = self.run_trials()
        self.assertEqual(result["reasonCode"], "cost-approval-required")
        self.assertEqual(self.started_handles, [])

    def test_explicit_approval_runs_both_paid_candidates(self):
        self.costs = {"a": "paid", "b": "licensed"}
        self.assertEqual(self.run_trials(approve_cost=lambda request: True)["action"], "continue")

    def test_hard_or_critical_failure_cannot_win(self):
        self.scenarios = {"a": {"gateFail": True, "difference": 0}, "b": {"criticalFail": True}}
        result = self.run_trials()
        self.assertEqual(result["action"], "request-input")
        self.assertIsNone(result["selected"])
        self.assertTrue(all(not assessment["passed"] for assessment in result["assessments"]))

    def test_failed_hard_candidate_loses_to_gate_passing_candidate(self):
        self.scenarios = {"a": {"gateFail": True, "difference": 0}}
        self.assertEqual(self.run_trials()["selected"], "b")

    def test_unmatched_or_missing_evidence_does_not_leave_a_winner(self):
        self.scenarios["a"] = {"badCamera": True}
        result = self.run_trials()
        self.assertEqual(result["attempts"][0]["status"], "unevaluated")
        self.assertIsNone(result["selected"])

    def test_missing_measurement_remains_unevaluated(self):
        self.scenarios = {"a": {"missingMetric": True}, "b": {"missingMetric": True}}
        result = self.run_trials()
        self.assertIsNone(result["selected"])
        self.assertTrue(all(item["status"] == "unevaluated" for item in result["assessments"]))

    def test_no_shell_interpolation(self):
        result = self.run_trials()
        for attempt in result["attempts"]:
            directory = self.workspace / attempt["directory"]
            self.assertEqual((directory / "literal-argument.txt").read_text(), self.literal)
            self.assertFalse((directory / "INJECTED").exists())
            self.assertFalse((directory / "ALSO_INJECTED").exists())

    def test_completed_attempt_is_revalidated_without_overwrite_or_rerun(self):
        record, context = self.decide()
        executor = self.executor()
        result = executor.run(record, context, consent=self.consent)
        first_report = self.workspace / result["attempts"][0]["directory"] / "quality.json"
        original = first_report.read_bytes()
        resumed = executor.run(record, context, consent=self.consent)
        self.assertEqual(resumed["selected"], result["selected"])
        self.assertEqual(first_report.read_bytes(), original)
        self.assertEqual(self.started_handles, ["a", "b"])

    def test_existing_candidate_directory_is_not_reused(self):
        record, context = self.decide()
        directory = self.workspace / ".img2threejs/objects/object-1/rev-1/candidates" / hashlib.sha256(b"a").hexdigest()
        directory.mkdir(parents=True)
        (directory / "incumbent").write_text("retain")
        self.assert_reason("best-of-n-already-attempted", lambda: self.executor().run(record, context, consent=self.consent))
        self.assertEqual((directory / "incumbent").read_text(), "retain")
        self.assertEqual(self.started_handles, [])

    def test_deadline_kills_process_group_and_stops_next_candidate(self):
        record, context = self.decide()
        marker = self.workspace / "descendant-survived"
        self.scenarios["a"] = {"sleep": 30, "descendant": str(marker)}
        self.offset = 3599.6
        began = time.monotonic()
        result = self.executor().run(record, context, consent=self.consent)
        self.assertLess(time.monotonic() - began, 2)
        self.assertEqual(result["reasonCode"], "deadline-exhausted")
        self.assertEqual(self.started_handles, ["a"])
        self.assertEqual(result["attempts"][0]["status"], "unevaluated")
        time.sleep(0.9)
        self.assertFalse(marker.exists())

    def test_exhausted_shared_deadline_executes_nothing(self):
        record, context = self.decide()
        self.offset = 3601
        reopened = DecisionController(self.workspace, "run-1", clock=self.clock)
        programs = {handle: CandidateProgram("local", self.factory, self.producer) for handle in ("a", "b", "c")}
        result = BestOfN(reopened, programs, self.contract).run(record, context, consent=self.consent)
        self.assertEqual(result["reasonCode"], "deadline-exhausted")
        self.assertEqual(self.started_handles, [])

    def test_tampered_report_hash_is_unevaluated(self):
        record, context = self.decide()
        def tampered(trial):
            evidence = self.producer(trial)
            (trial.directory / "quality.json").write_text("{}")
            return evidence
        programs = {handle: CandidateProgram("local", self.factory, tampered) for handle in ("a", "b", "c")}
        result = BestOfN(self.controller, programs, self.contract).run(record, context, consent=self.consent)
        self.assertTrue(all(item["reasonCode"] == "stale-evidence" for item in result["attempts"]))
        self.assertIsNone(result["selected"])


    def test_command_failure_cannot_win(self):
        self.scenarios["a"] = {"fail": True}
        result = self.run_trials()
        self.assertEqual(result["selected"], "b")
        self.assertEqual(result["attempts"][0]["reasonCode"], "candidate-command-failed")
        self.assertEqual(result["attempts"][0]["exitCode"], 9)

    def test_prior_passing_candidate_survives_later_deadline(self):
        record, context = self.decide()
        self.scenarios["b"] = {"sleep": 30}
        def producer(trial):
            evidence = self.producer(trial)
            if trial.candidate_id == "a":
                self.offset = 3599.6
            return evidence
        programs = {handle: CandidateProgram("local", self.factory, producer) for handle in ("a", "b", "c")}
        result = BestOfN(self.controller, programs, self.contract).run(record, context, consent=self.consent)
        self.assertEqual(result["selected"], "a")
        self.assertEqual(result["attempts"][1]["reasonCode"], "deadline-exhausted")

    def test_model_fallback_record_cannot_authorize_trials(self):
        context = self.context()
        def fallback(projected):
            return {"kind": "img2.decision-advisory", "version": 1, "group": "routing",
                    "subject": projected["subject"], "contextDigest": canonical_digest(projected),
                    "model": "fixture-model", "answers": {
                        "selection": {"type": "choice", "choice": "a",
                                      "probabilities": {"a": 0.9, "b": 0.08, "c": 0.02}, "confidence": 0.9},
                        "anyFits": {"type": "noul", "noul": 1}},
                    "usage": {"input_tokens": 1, "output_tokens": 1}}
        record = self.controller.consult(context, fallback=fallback,
                                         availability=lambda: {"status": "not-installed"},
                                         consent=self.consent, request_token_limit=128)
        self.assert_reason("best-of-n-not-authorized",
                           lambda: self.executor().run(record, context, consent=self.consent))
        self.assertEqual(self.started_handles, [])

    def test_shell_string_is_not_an_argv(self):
        record, context = self.decide()
        programs = {handle: CandidateProgram("local", lambda trial: "touch INJECTED", self.producer)
                    for handle in ("a", "b", "c")}
        result = BestOfN(self.controller, programs, self.contract).run(record, context, consent=self.consent)
        self.assertTrue(all(item["reasonCode"] == "untrusted-argv" for item in result["attempts"]))
        self.assertIsNone(result["selected"])
        self.assertFalse(any(self.workspace.rglob("INJECTED")))

    def test_symlink_evidence_fails_admission(self):
        record, context = self.decide()
        def linked(trial):
            (trial.directory / "source").unlink()
            (trial.directory / "source").symlink_to(self.workspace / "reference")
            # The binding hash matches the target: path integrity must still fail.
            return self.producer(trial)
        programs = {handle: CandidateProgram("local", self.factory, linked) for handle in ("a", "b", "c")}
        # Unsafe output sealing also fails closed, rather than returning a winner.
        self.assert_reason("unsafe-output", lambda: BestOfN(self.controller, programs, self.contract)
                           .run(record, context, consent=self.consent))


    def fresh_process(self, record, context, *, mode="resume", revoked=False, now=None, scenarios=None):
        config = {"workspace": str(self.workspace), "shared": self.shared, "comparison": self.contract,
                  "record": record, "context": context, "mode": mode,
                  "now": self.clock() if now is None else now, "grant": self.consent(), "scenarios": scenarios or {}}
        if revoked:
            config["grant"]["granted"] = False
        path = self.workspace / "restart-config.json"
        path.write_text(json.dumps(config))
        process = subprocess.run([sys.executable, "-c", RESTART_DRIVER, str(path)],
                                 cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=10)
        result = json.loads(process.stdout) if process.stdout.strip() else None
        return process, result

    def executions(self):
        path = self.workspace / "executions.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_fresh_process_reuses_completed_paid_trial_and_continues_only_unstarted(self):
        record, context = self.decide()
        interrupted, _ = self.fresh_process(record, context, mode="crash-before-b")
        self.assertEqual(interrupted.returncode, 71, interrupted.stderr)
        first = self.workspace / ".img2threejs/objects/object-1/rev-1/candidates" / hashlib.sha256(b"a").hexdigest()
        original = (first / "quality.json").read_bytes()
        inode = (first / "quality.json").stat().st_ino
        resumed, result = self.fresh_process(record, context)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(result["action"], "continue")
        self.assertEqual(self.executions(), ["a", "b"])
        self.assertEqual((first / "quality.json").read_bytes(), original)
        self.assertEqual((first / "quality.json").stat().st_ino, inode)
        again, second = self.fresh_process(record, context)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(second["selected"], result["selected"])
        self.assertEqual(self.executions(), ["a", "b"])
        approvals = [json.loads(line) for line in (self.workspace / "approvals.jsonl").read_text().splitlines()]
        self.assertEqual(approvals, ["a", "b"])

    def test_fresh_process_ambiguous_in_flight_never_reexecutes(self):
        record, context = self.decide()
        interrupted, _ = self.fresh_process(record, context, mode="crash-before-completion")
        self.assertEqual(interrupted.returncode, 72, interrupted.stderr)
        resumed, result = self.fresh_process(record, context)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(result["action"], "request-input")
        self.assertEqual(result["reasonCode"], "ambiguous-in-flight")
        self.assertEqual(self.executions(), ["a"])
        again, repeated = self.fresh_process(record, context)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(repeated["reasonCode"], "ambiguous-in-flight")
        self.assertEqual(self.executions(), ["a"])

    def test_fresh_process_revoked_consent_blocks_reuse_and_unstarted_trial(self):
        record, context = self.decide()
        interrupted, _ = self.fresh_process(record, context, mode="crash-before-b")
        self.assertEqual(interrupted.returncode, 71, interrupted.stderr)
        resumed, result = self.fresh_process(record, context, revoked=True)
        self.assertEqual(resumed.returncode, 2, resumed.stderr)
        self.assertEqual(result["error"], "consent-changed")
        self.assertEqual(self.executions(), ["a"])

    def test_fresh_process_stale_output_blocks_reuse_and_unstarted_trial(self):
        record, context = self.decide()
        interrupted, _ = self.fresh_process(record, context, mode="crash-before-b")
        self.assertEqual(interrupted.returncode, 71, interrupted.stderr)
        source = self.workspace / ".img2threejs/objects/object-1/rev-1/candidates" / hashlib.sha256(b"a").hexdigest() / "source"
        source.chmod(0o600)
        source.write_text("outside edit invalidates the completed trial")
        resumed, result = self.fresh_process(record, context)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(result["action"], "request-input")
        self.assertEqual(result["reasonCode"], "recovery-evidence-invalid")
        self.assertEqual(result["attempts"][0]["reasonCode"], "stale-evidence")
        self.assertEqual(self.executions(), ["a"])

    def test_fresh_process_keeps_original_deadline_for_unstarted_trial(self):
        record, context = self.decide()
        interrupted, _ = self.fresh_process(record, context, mode="crash-before-b")
        self.assertEqual(interrupted.returncode, 71, interrupted.stderr)
        resumed, result = self.fresh_process(record, context, now=5001)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(result["selected"], "a")
        self.assertEqual(self.executions(), ["a"])


    def test_fresh_process_rechecks_gates_without_replacing_failed_reports(self):
        record, context = self.decide()
        first, initial = self.fresh_process(record, context,
                                            scenarios={"a": {"gateFail": True}, "b": {"criticalFail": True}})
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(initial["action"], "request-input")
        resumed, result = self.fresh_process(record, context)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(result["action"], "request-input")
        self.assertTrue(all(not assessment["passed"] for assessment in result["assessments"]))
        self.assertEqual(self.executions(), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
