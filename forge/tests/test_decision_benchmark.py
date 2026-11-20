"""Offline harness mechanics only; synthetic inputs are NOT corpus qualification.

No live API, model, rendering, or paid calls. Labels and measured results are
separate frozen files; tests perturb one side without copying the other side.
"""

import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

_FORGE = Path(__file__).resolve().parents[1]
if str(_FORGE) not in sys.path:
    sys.path.insert(0, str(_FORGE))

import benchmark_decisions as benchmark

_FIXTURES = Path(__file__).parent / "fixtures" / "decision_benchmark"


class DecisionBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "protocol"
        shutil.copytree(_FIXTURES, self.root)
        self.manifest = self.load("manifest.json")
        self.results = self.load("results.good.json")

    def load(self, name):
        return json.loads((self.root / name).read_text(encoding="utf-8"))

    def save(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def update_measurement(self, identity, report):
        entry = next(row for row in self.results["results"] if row["caseId"] == identity)
        entry["measurement"] = self.save(identity + ".modified.json", report)

    def update_labels(self, labels):
        self.manifest["labels"] = self.save("labels.modified.json", labels)

    def evaluate(self):
        self.save("manifest.modified.json", self.manifest)
        self.save("results.modified.json", self.results)
        return benchmark.evaluate(self.root / "manifest.modified.json", self.root / "results.modified.json")

    def row(self, report, identity):
        return next(row for row in report["cases"] if row["id"] == identity)

    def test_known_safe_fixture_reports_denominators_not_qualification(self):
        report = self.evaluate()
        self.assertEqual([row["status"] for row in report["cases"]], ["pass"] * 4)
        self.assertFalse(report["qualification"]["qualified"])
        self.assertIn("corpus:not-real-image-corpus", report["unevaluated"])
        self.assertIn("coverage:held-out:routing:cs2-lookalike", report["unevaluated"])
        for group in benchmark.GROUPS:
            summary = report["splits"]["held-out"][group]["overall"]
            self.assertEqual(summary["cases"], 1)
            self.assertEqual(summary["evaluated"], 1)
            self.assertEqual(summary["accuracy"], 1.0)
            self.assertEqual(summary["validated"], 0)
        routing = report["splits"]["held-out"]["routing"]["overall"]
        self.assertEqual(routing["boxDenominator"], 1)
        self.assertEqual(routing["boxRecall"], 1.0)
        self.assertEqual(report["tokens"], {"fresh": 100, "cached": 20, "output": 30})
        self.assertEqual(report["objects"]["cup"]["elapsedMs"], 10000)
        self.assertEqual(report["objects"]["cup"]["corrections"], 1)
        repair = self.row(report, "repair")["quality"]
        self.assertEqual(repair["status"], "retain")
        self.assertFalse(repair["deliverable"])

    def test_known_bad_results_block_false_continue_and_passing_repair(self):
        self.results = self.load("results.bad.json")
        report = self.evaluate()
        review = self.row(report, "review")
        self.assertEqual(review["status"], "fail")
        self.assertEqual(review["criticalFalseContinue"], 1)
        self.assertEqual(review["quality"]["status"], "fail")
        repair = self.row(report, "repair")
        self.assertEqual(repair["status"], "fail")
        self.assertEqual(repair["quality"]["status"], "reject")
        self.assertEqual(repair["regressionCount"], 1)
        self.assertEqual(repair["acceptedRegressionCount"], 1)
        self.assertEqual(repair["forbiddenMutationCount"], 1)
        self.assertGreaterEqual(repair["safetyViolationCount"], 2)
        criteria = report["qualification"]["criteriaMet"]
        self.assertFalse(criteria["criticalFalseContinue"])
        self.assertFalse(criteria["forbiddenMutations"])
        self.assertFalse(criteria["protectedRegressions"])
        self.assertFalse(report["qualification"]["execution"])

    def test_result_does_not_echo_or_overwrite_expert_label(self):
        measurement = self.load("route.good.json")
        measurement["decision"] = "wrong-route"
        measurement["expectedDecision"] = "wrong-route"
        self.update_measurement("route", measurement)
        report = self.evaluate()
        row = self.row(report, "route")
        self.assertEqual(row["expectedDecision"], "base:generic")
        self.assertFalse(row["correct"])
        summary = report["splits"]["held-out"]["routing"]["overall"]
        self.assertEqual(summary["disagreements"], 1)
        self.assertEqual(summary["accuracy"], 0)
        self.assertFalse(report["qualification"]["criteriaMet"]["routeAccuracy"])

    def test_missing_usage_and_elapsed_remain_null(self):
        measurement = self.load("strategy.good.json")
        del measurement["usage"]["cached"]
        measurement["usage"]["fresh"] = True
        del measurement["elapsedMs"]
        self.update_measurement("strategy", measurement)
        report = self.evaluate()
        row = self.row(report, "strategy")
        self.assertEqual(row["status"], "unevaluated")
        self.assertIsNone(row["tokens"]["fresh"])
        self.assertIsNone(row["tokens"]["cached"])
        self.assertIsNone(report["tokens"]["cached"])
        self.assertEqual(report["knownTokenSubtotals"]["cached"], 16)
        self.assertIsNone(report["objects"]["cup"]["elapsedMs"])
        self.assertIn("tokens:cached:missing-or-invalid", row["unevaluated"])

    def test_all_missing_label_result_hash_and_class_are_named(self):
        labels = self.load("labels.json")
        labels["labels"] = [row for row in labels["labels"] if row["caseId"] != "route"]
        self.update_labels(labels)
        self.results["results"] = [row for row in self.results["results"] if row["caseId"] != "strategy"]
        del self.manifest["cases"][2]["source"]["sha256"]
        del self.manifest["cases"][3]["class"]
        report = self.evaluate()
        self.assertIn("label:missing-decision", self.row(report, "route")["unevaluated"])
        self.assertIn("result:missing", self.row(report, "strategy")["unevaluated"])
        self.assertIn("source:missing-path-or-sha256", self.row(report, "review")["unevaluated"])
        self.assertIn("class:missing-or-invalid", self.row(report, "repair")["unevaluated"])
        self.assertFalse(report["qualification"]["qualified"])

    def test_unknown_expert_and_source_authority_never_validate(self):
        self.manifest["corpusKind"] = "real"
        self.manifest["authorities"]["fixture-expert"]["status"] = "unknown"
        self.manifest["cases"][0]["source"]["authority"] = "unknown-source"
        report = self.evaluate()
        self.assertTrue(all(not row["validated"] for row in report["cases"]))
        self.assertIn("source:unknown-authority", self.row(report, "route")["unevaluated"])
        self.assertTrue(all(row["status"] == "unevaluated" for row in report["cases"]))
        self.assertFalse(report["qualification"]["qualified"])

    def test_development_is_separate_and_shared_source_is_leakage(self):
        self.manifest["cases"][0]["split"] = "development"
        report = self.evaluate()
        self.assertEqual(report["splits"]["development"]["routing"]["overall"]["cases"], 1)
        self.assertEqual(report["splits"]["held-out"]["routing"]["overall"]["cases"], 0)
        self.assertTrue(any(reason.startswith("split:source-leakage:") for reason in report["unevaluated"]))
        self.assertFalse(report["qualification"]["qualified"])

    def test_distinct_development_source_is_not_held_out_accuracy(self):
        source = self.root / "development.txt"
        source.write_text("Distinct protocol development source, not held-out.\n", encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self.manifest["cases"][0]["split"] = "development"
        self.manifest["cases"][0]["source"].update(path=source.name, sha256=digest)
        self.results["results"][0]["sourceSha256"] = digest
        report = self.evaluate()
        self.assertEqual(report["splits"]["development"]["routing"]["overall"]["accuracy"], 1.0)
        self.assertIsNone(report["splits"]["held-out"]["routing"]["overall"]["accuracy"])
        self.assertFalse(any(reason.startswith("split:source-leakage:") for reason in report["unevaluated"]))

    def test_measurement_hash_mismatch_cannot_supply_evidence(self):
        (self.root / "route.good.json").write_text('{"decision":"base:generic"}\n', encoding="utf-8")
        report = self.evaluate()
        row = self.row(report, "route")
        self.assertIn("measurement:hash-mismatch", row["unevaluated"])
        self.assertIsNone(row["decision"])
        self.assertIsNone(row["elapsedMs"])

    def test_symlink_and_path_escape_are_rejected(self):
        (self.root / "linked-source.txt").symlink_to(self.root / "source.txt")
        self.manifest["cases"][0]["source"]["path"] = "linked-source.txt"
        self.manifest["cases"][1]["source"]["path"] = "../outside.txt"
        report = self.evaluate()
        self.assertIn("source:symlink", self.row(report, "route")["unevaluated"])
        self.assertIn("source:path-escape", self.row(report, "strategy")["unevaluated"])

    def test_duplicate_results_cannot_pick_a_convenient_answer(self):
        self.results["results"].append(copy.deepcopy(self.results["results"][0]))
        report = self.evaluate()
        self.assertIn("results:duplicate:route", report["unevaluated"])
        self.assertIn("result:missing", self.row(report, "route")["unevaluated"])

    def test_shared_object_time_tokens_and_corrections_do_not_reset_by_group(self):
        for identity in ("route", "strategy", "review", "repair"):
            measurement = self.load(identity + ".good.json")
            measurement["elapsedMs"] = 1000000
            self.update_measurement(identity, measurement)
        self.manifest["budgets"] = {"tokenBudget": 149, "correctionCeiling": 0}
        report = self.evaluate()
        self.assertEqual(report["objects"]["cup"]["elapsedMs"], 4000000)
        self.assertIn("budget:object:cup:wall-clock-exceeded", report["unevaluated"])
        self.assertIn("budget:object:cup:correction-ceiling-exceeded", report["unevaluated"])
        self.assertIn("budget:run:token-budget-exceeded", report["unevaluated"])

    def test_one_box_cannot_recall_two_labelled_objects(self):
        labels = self.load("labels.json")
        labels["labels"][0]["boxes"].append(copy.deepcopy(labels["labels"][0]["boxes"][0]))
        self.update_labels(labels)
        report = self.evaluate()
        boxes = self.row(report, "route")["boxes"]
        self.assertEqual(boxes["matched"], 1)
        self.assertEqual(boxes["expected"], 2)
        self.assertFalse(report["qualification"]["criteriaMet"]["boxRecall"])

    def test_required_critical_feature_cannot_be_removed_from_report(self):
        measurement = self.load("review.good.json")
        measurement["candidate"]["features"] = []
        measurement["decision"] = "continue"
        measurement["delivered"] = True
        self.update_measurement("review", measurement)
        row = self.row(self.evaluate(), "review")
        self.assertEqual(row["status"], "unevaluated")
        self.assertIn("candidate:rubric-mismatch", row["unevaluated"])
        self.assertEqual(row["criticalFalseContinue"], 1)

    def test_high_confidence_and_report_criteria_cannot_replace_frozen_improvement(self):
        measurement = self.load("repair.good.json")
        measurement["after"]["metrics"]["silhouetteIoU"] = 0.6
        measurement["confidence"] = 1.0
        measurement["criteria"] = {"targetMetric": "cost", "direction": "min", "minImprovement": 0}
        self.update_measurement("repair", measurement)
        row = self.row(self.evaluate(), "repair")
        self.assertEqual(row["quality"]["status"], "reject")
        self.assertIn("target:insufficient-improvement", row["quality"]["reasons"])
        self.assertGreater(row["safetyViolationCount"], 0)

    def test_partial_improvement_is_not_delivery(self):
        measurement = self.load("repair.good.json")
        measurement["delivered"] = True
        self.update_measurement("repair", measurement)
        row = self.row(self.evaluate(), "repair")
        self.assertEqual(row["quality"]["status"], "retain")
        self.assertFalse(row["quality"]["deliverable"])
        self.assertEqual(row["status"], "fail")
        self.assertEqual(row["safetyViolationCount"], 1)

    def test_unbound_reference_and_missing_view_are_unevaluated(self):
        measurement = self.load("review.good.json")
        measurement["candidate"]["evidence"]["referenceSha256"] = "f" * 64
        measurement["candidate"]["views"] = [0, 90, 270]
        self.update_measurement("review", measurement)
        row = self.row(self.evaluate(), "review")
        self.assertEqual(row["status"], "unevaluated")
        self.assertIn("candidate:reference-source-mismatch", row["unevaluated"])
        self.assertIn("candidate:unbound:referenceSha256", row["unevaluated"])
        self.assertIn("candidate:views:unevaluated", row["unevaluated"])

    def make_positive_review(self):
        labels = self.load("labels.json")
        label = next(row for row in labels["labels"] if row["caseId"] == "review")
        label.update(decision="continue", criticalNegative=False)
        self.update_labels(labels)
        measurement = self.load("review.good.json")
        measurement.update(decision="continue", delivered=True)
        measurement["candidate"]["id"] = "current"
        measurement["candidate"]["features"][0].update(status="pass", score=3)
        baseline = copy.deepcopy(measurement["candidate"])
        baseline["id"] = "single-track-baseline"
        baseline["metrics"]["silhouetteIoU"] = 0.65
        baseline_binding = self.save("baseline.json", baseline)
        next(row for row in self.manifest["cases"] if row["id"] == "review")["baseline"] = baseline_binding
        return measurement

    def test_positive_review_compares_supplied_single_track_baseline(self):
        self.update_measurement("review", self.make_positive_review())
        row = self.row(self.evaluate(), "review")
        self.assertEqual(row["status"], "pass")
        self.assertTrue(row["baselineEvaluated"])
        self.assertEqual(row["regressionCount"], 0)

    def test_positive_review_detects_class_quality_regression(self):
        measurement = self.make_positive_review()
        measurement["candidate"]["metrics"]["silhouetteIoU"] = 0.5
        self.update_measurement("review", measurement)
        report = self.evaluate()
        row = self.row(report, "review")
        self.assertEqual(row["regressionCount"], 1)
        self.assertEqual(row["acceptedRegressionCount"], 1)
        self.assertFalse(report["qualification"]["criteriaMet"]["perClassQualityBaseline"])

    def test_baseline_with_unmatched_camera_is_not_a_winner(self):
        measurement = self.make_positive_review()
        measurement["candidate"]["evidence"]["cameraSha256"] = measurement["candidate"]["evidence"]["referenceSha256"]
        self.update_measurement("review", measurement)
        row = self.row(self.evaluate(), "review")
        self.assertIn("baseline:incomparable", row["unevaluated"])
        self.assertFalse(row["baselineEvaluated"])

    def test_missing_or_empty_corpus_never_qualifies(self):
        missing = benchmark.evaluate(self.root / "absent.json", self.root / "results.good.json")
        self.assertFalse(missing["qualification"]["qualified"])
        self.assertEqual(missing["unevaluated"], ["manifest-or-results:unreadable-or-invalid"])
        self.manifest["cases"] = []
        report = self.evaluate()
        self.assertFalse(report["qualification"]["qualified"])
        self.assertTrue(any(reason.startswith("coverage:held-out:") for reason in report["unevaluated"]))
        self.assertIsNone(report["tokens"]["fresh"])

    def test_cli_writes_a_real_json_report(self):
        out = self.root / "report.json"
        code = benchmark.main(["--manifest", str(self.root / "manifest.json"),
                               "--results", str(self.root / "results.bad.json"), "--out", str(out)])
        self.assertEqual(code, 0)
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertFalse(report["qualification"]["qualified"])
        self.assertEqual(self.row(report, "review")["criticalFalseContinue"], 1)

    def test_duplicate_json_keys_and_nonfinite_input_fail_closed(self):
        for content in ('{"cases":[],"cases":[]}', '{"cases":NaN}', '{"cases":1e999}'):
            with self.subTest(content=content):
                (self.root / "invalid.json").write_text(content, encoding="utf-8")
                report = benchmark.evaluate(self.root / "invalid.json", self.root / "results.good.json")
                self.assertFalse(report["qualification"]["qualified"])
                self.assertEqual(report["unevaluated"], ["manifest-or-results:unreadable-or-invalid"])

    def test_missing_repair_measurements_leave_regression_count_unknown(self):
        measurement = self.load("repair.good.json")
        del measurement["after"]["metrics"]["silhouetteIoU"]
        self.update_measurement("repair", measurement)
        row = self.row(self.evaluate(), "repair")
        self.assertEqual(row["status"], "unevaluated")
        self.assertIsNone(row["regressionCount"])

    def test_all_class_protocol_success_still_cannot_qualify_as_real_corpus(self):
        # Expand only the protocol mechanics, not an alleged real-image dataset.
        # This prevents every safety assertion from being vacuous due to missing
        # coverage, while retaining the explicit non-qualification boundary.
        self.update_measurement("review", self.make_positive_review())
        prototypes = copy.deepcopy(self.manifest["cases"])
        labels = self.load("labels.modified.json")
        labels_by_id = {row["caseId"]: row for row in labels["labels"]}
        results_by_id = {row["caseId"]: row for row in self.results["results"]}
        rubric = self.load("rubric.json")
        criteria = self.load("criteria.json")
        self.manifest["cases"] = []
        self.results["results"] = []
        expanded_labels = []
        expanded_rubric, expanded_criteria = {}, {}
        for category in benchmark.CLASSES:
            for prototype in prototypes:
                original = prototype["id"]
                identity = category + "-" + original
                case = copy.deepcopy(prototype)
                case.update(id=identity, objectId=category, **{"class": category})
                self.manifest["cases"].append(case)
                result = copy.deepcopy(results_by_id[original])
                result["caseId"] = identity
                self.results["results"].append(result)
                label = copy.deepcopy(labels_by_id[original])
                label["caseId"] = identity
                expanded_labels.append(label)
                if original in rubric["cases"]:
                    expanded_rubric[identity] = copy.deepcopy(rubric["cases"][original])
                if original in criteria["cases"]:
                    expanded_criteria[identity] = copy.deepcopy(criteria["cases"][original])
        # The required-critical negative is independent of the positive baseline
        # cases. Its correct outcome is the fixed block label from labels.json.
        negative = copy.deepcopy(prototypes[2])
        negative.update(id="critical-negative", objectId="negative")
        negative.pop("baseline", None)
        self.manifest["cases"].append(negative)
        original_label = next(row for row in self.load("labels.json")["labels"] if row["caseId"] == "review")
        original_label["caseId"] = "critical-negative"
        expanded_labels.append(original_label)
        expanded_rubric["critical-negative"] = copy.deepcopy(rubric["cases"]["review"])
        entry = copy.deepcopy(next(row for row in self.load("results.good.json")["results"] if row["caseId"] == "review"))
        entry["caseId"] = "critical-negative"
        self.results["results"].append(entry)
        self.update_labels({"labels": expanded_labels})
        self.manifest["frozen"]["rubric"] = self.save("rubric.expanded.json", {"cases": expanded_rubric})
        self.manifest["frozen"]["repairCriteria"] = self.save("criteria.expanded.json", {"cases": expanded_criteria})
        self.manifest["budgets"]["tokenBudget"] = 10000
        report = self.evaluate()
        self.assertTrue(all(report["qualification"]["criteriaMet"].values()))
        self.assertEqual(report["unevaluated"], ["corpus:not-real-image-corpus"])
        self.assertFalse(report["qualification"]["qualified"])
        self.assertTrue(all(row["status"] == "pass" for row in report["cases"]))

    def test_model_fallback_accuracy_cannot_qualify_primary_jev(self):
        # Deliberately misdeclaring these fixtures as real must still not turn
        # fallback observations into measurements of the Jev primary provider.
        self.manifest["corpusKind"] = "real"
        for identity in ("route", "strategy", "review", "repair"):
            measurement = self.load(identity + ".good.json")
            measurement["decider"] = "model:configured-gpt-sol"
            self.update_measurement(identity, measurement)
        report = self.evaluate()
        route = report["splits"]["held-out"]["routing"]
        self.assertEqual(route["overall"]["accuracy"], 1.0)
        self.assertIsNone(route["jev"]["accuracy"])
        self.assertFalse(report["qualification"]["criteriaMet"]["routeAccuracy"])
        self.assertIn("coverage:held-out:jev:routing:generic-object", report["unevaluated"])
        self.assertFalse(report["qualification"]["qualified"])

    def test_optional_supplied_candidates_use_bound_quality_ranking(self):
        measurement = self.load("strategy.good.json")
        candidate = self.load("review.good.json")["candidate"]
        candidate["id"] = "lathe-and-handle"
        candidate["features"][0].update(status="pass", score=3)
        other = copy.deepcopy(candidate)
        other["id"] = "alternative"
        other["metrics"]["silhouetteIoU"] = 0.6
        measurement["candidates"] = [other, candidate]
        rubric = self.load("rubric.json")
        rubric["cases"]["strategy"] = copy.deepcopy(rubric["cases"]["review"])
        self.manifest["frozen"]["rubric"] = self.save("rubric.strategy.json", rubric)
        self.update_measurement("strategy", measurement)
        row = self.row(self.evaluate(), "strategy")
        self.assertEqual(row["status"], "pass")
        self.assertEqual(row["quality"][0]["id"], "lathe-and-handle")
        self.assertEqual(row["quality"][0]["rank"], 1)


if __name__ == "__main__":
    unittest.main()
