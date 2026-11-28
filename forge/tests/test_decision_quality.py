"""Offline measured-report examples only; these tests do not render geometry."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

_FORGE = str(Path(__file__).resolve().parents[1])
if _FORGE not in sys.path:
    sys.path.insert(0, _FORGE)

from _shared.decision_quality import assess_candidate, compare_repair, rank_candidates


def candidate(identifier="solid"):
    return {
        "id": identifier, "outputKind": "threejs", "flatness": "volumetric",
        "gateResults": [{"id": "mesh", "status": "pass", "producer": "mesh-check-v1"},
                        {"id": "rig", "status": "pass", "producer": "rig-check-v1"}],
        "measurements": {"width": 2, "height": 1, "depth": 0.4,
                         "frontArea": 2, "sideArea": 0.6, "closed": True,
                         "twoSided": True, "selfIntersectionCount": 0,
                         "partCoveragePassed": True},
        "views": [0, 90, 180, 270],
        "features": [{"id": "handle", "tier": "critical", "status": "pass", "score": 3.5, "threshold": 3},
                     {"id": "face", "tier": "critical", "status": "pass", "score": 4, "threshold": 4}],
        "metrics": {"silhouetteIoU": 0.9, "interiorDifference": 0.08, "orbitConsistency": 0.9},
        "evidence": {"referenceSha256": "a" * 64, "cameraSha256": "b" * 64, "viewSetSha256": "c" * 64},
        "cost": 2, "timeMs": 100,
    }


def criteria(target="silhouetteIoU", direction="max", minimum=0.01):
    return {"targetMetric": target, "direction": direction, "minImprovement": minimum,
            "tolerances": {"silhouetteIoU": 0, "interiorDifference": 0.01, "orbitConsistency": 0.01}}


class CandidateAssessmentTests(unittest.TestCase):
    def test_complete_measured_report_passes_without_mutation(self):
        report = candidate()
        original = deepcopy(report)
        result = assess_candidate(report)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["passed"])
        self.assertTrue(result["deliverable"])
        self.assertEqual(result["hardStatus"], "pass")
        self.assertEqual(result["semanticStatus"], "pass")
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["report"], original)
        self.assertEqual(report, original)

    def test_volumetric_billboard_cannot_be_saved_by_perfect_scores(self):
        for field, value in (("depth", 0.04), ("sideArea", 0.49)):
            with self.subTest(field=field):
                report = candidate()
                report["measurements"][field] = value
                report["metrics"] = {"silhouetteIoU": 1, "interiorDifference": 0, "orbitConsistency": 1}
                report["cost"] = 0
                result = assess_candidate(report)
                self.assertEqual(result["status"], "fail")
                self.assertFalse(result["deliverable"])
                self.assertEqual(result["hardStatus"], "fail")

    def test_policy_boundary_is_inclusive(self):
        report = candidate()
        report["measurements"].update(depth=0.3, sideArea=0.5)
        self.assertTrue(assess_candidate(report)["passed"])

    def test_flat_closed_two_sided_solid_uses_only_thickness_floor(self):
        report = candidate()
        report["flatness"] = "flat"
        report["measurements"].update(depth=0.01, sideArea=0)
        self.assertTrue(assess_candidate(report)["passed"])
        for field, value in (("depth", 0.009), ("depth", 0), ("closed", False), ("twoSided", False)):
            with self.subTest(field=field, value=value):
                trial = deepcopy(report)
                trial["measurements"][field] = value
                self.assertFalse(assess_candidate(trial)["passed"])

    def test_intersection_or_coverage_failure_is_hard(self):
        for field, value in (("selfIntersectionCount", 1), ("partCoveragePassed", False)):
            with self.subTest(field=field):
                report = candidate()
                report["measurements"][field] = value
                self.assertEqual(assess_candidate(report)["hardStatus"], "fail")

    def test_each_azimuth_is_required(self):
        for angle in (0, 90, 180, 270):
            with self.subTest(angle=angle):
                report = candidate()
                report["views"].remove(angle)
                result = assess_candidate(report)
                self.assertEqual(result["status"], "unevaluated")
                self.assertFalse(result["deliverable"])

    def test_missing_sections_are_not_success(self):
        for field in candidate():
            with self.subTest(field=field):
                report = candidate()
                del report[field]
                result = assess_candidate(report)
                self.assertEqual(result["status"], "unevaluated")
                self.assertFalse(result["passed"])
                self.assertTrue(result["reasons"])

    def test_partial_evidence_and_missing_producer_are_unevaluated(self):
        for field in ("referenceSha256", "cameraSha256", "viewSetSha256"):
            report = candidate()
            del report["evidence"][field]
            self.assertEqual(assess_candidate(report)["status"], "unevaluated")
        for field in ("producer", "status", "id"):
            report = candidate()
            del report["gateResults"][0][field]
            self.assertEqual(assess_candidate(report)["hardStatus"], "unevaluated")
        report = candidate()
        report["gateResults"] = []
        self.assertFalse(assess_candidate(report)["passed"])

    def test_required_measurement_missing_is_unevaluated(self):
        for field in ("width", "height", "depth", "frontArea", "sideArea", "selfIntersectionCount", "partCoveragePassed"):
            with self.subTest(field=field):
                report = candidate()
                del report["measurements"][field]
                self.assertEqual(assess_candidate(report)["status"], "unevaluated")

    def test_numeric_false_success_values_are_rejected(self):
        for bad in (True, False, float("nan"), float("inf"), -float("inf"), "1", None, 10 ** 400):
            for section, field in (("measurements", "width"), ("metrics", "silhouetteIoU"), ("features", "score")):
                with self.subTest(value=bad, section=section):
                    report = candidate()
                    if section == "features":
                        report[section][0][field] = bad
                    else:
                        report[section][field] = bad
                    self.assertFalse(assess_candidate(report)["passed"])
            report = candidate()
            report["cost"] = bad
            self.assertFalse(assess_candidate(report)["passed"])

    def test_bool_is_not_a_gate_status_or_number(self):
        for field in ("status", "producer"):
            report = candidate()
            report["gateResults"][0][field] = True
            self.assertFalse(assess_candidate(report)["passed"])
        for field in ("closed", "twoSided"):
            report = candidate()
            report["flatness"] = "flat"
            report["measurements"][field] = 1
            self.assertFalse(assess_candidate(report)["passed"])
        report = candidate()
        report["views"][0] = False
        self.assertFalse(assess_candidate(report)["passed"])

    def test_nonfinite_derived_geometry_cannot_pass(self):
        report = candidate()
        report["measurements"].update(width=1e-308, height=1e-308, depth=1e308)
        self.assertFalse(assess_candidate(report)["passed"])

    def test_zero_dimensions_and_invalid_count_never_pass(self):
        for field in ("width", "height", "depth", "frontArea"):
            report = candidate()
            report["measurements"][field] = 0
            self.assertFalse(assess_candidate(report)["passed"])
        for value in (-1, 0.5, True):
            report = candidate()
            report["measurements"]["selfIntersectionCount"] = value
            self.assertFalse(assess_candidate(report)["passed"])

    def test_critical_failure_is_not_averaged(self):
        for status, score in (("fail", 4), ("pass", 2), ("uncertain", 4), ("unevaluated", 4)):
            with self.subTest(status=status, score=score):
                report = candidate()
                report["features"][0].update(status=status, score=score)
                self.assertFalse(assess_candidate(report)["passed"])

    def test_initial_floor_does_not_lower_authored_contract(self):
        report = candidate()
        report["features"][1]["score"] = 3.9
        self.assertEqual(assess_candidate(report)["semanticStatus"], "fail")
        report = candidate()
        report["features"][0].update(threshold=1, score=2.9)
        self.assertEqual(assess_candidate(report)["semanticStatus"], "fail")

    def test_invalid_threshold_and_duplicate_features_are_unevaluated(self):
        for value in (True, float("nan"), 5, -1, None):
            report = candidate()
            report["features"][0]["threshold"] = value
            self.assertEqual(assess_candidate(report)["semanticStatus"], "unevaluated")
        report = candidate()
        report["features"].append(deepcopy(report["features"][0]))
        self.assertFalse(assess_candidate(report)["passed"])

    def test_policy_file_is_used_and_online_override_is_refused(self):
        path = Path(_FORGE) / "_shared" / "route_quality_policy_v1.json"
        with path.open(encoding="utf-8") as stream:
            policy = json.load(stream)
        self.assertTrue(assess_candidate(candidate(), policy)["passed"])
        self.assertEqual(assess_candidate(candidate())["policyVersion"], policy["version"])
        policy["volumetric"]["minimumDepthRatio"] = 0.01
        result = assess_candidate(candidate(), policy)
        self.assertFalse(result["passed"])
        self.assertIn("policy-mismatch", result["reasons"])


class CandidateRankingTests(unittest.TestCase):
    def test_geometry_failure_cannot_win_through_cost_or_metrics(self):
        good, billboard = candidate("good"), candidate("billboard")
        billboard["measurements"]["depth"] = 0.01
        billboard["metrics"]["silhouetteIoU"] = 1
        billboard["cost"] = 0
        ranked = rank_candidates([billboard, good])
        self.assertEqual(ranked[0]["id"], "good")
        self.assertEqual(ranked[0]["rank"], 1)
        self.assertIsNone(ranked[1]["rank"])
        self.assertFalse(ranked[1]["deliverable"])
        self.assertEqual(ranked[1]["report"], billboard)

    def test_each_dimension_precedes_all_later_dimensions(self):
        for dimension in ("silhouetteIoU", "interiorDifference", "criticalFeatures", "orbitConsistency", "cost", "timeMs"):
            with self.subTest(dimension=dimension):
                better, worse = candidate("better"), candidate("worse")
                if dimension == "criticalFeatures":
                    better["features"][0]["score"] = 4
                elif dimension == "cost":
                    better["cost"] = 1
                    better["timeMs"] = 10000
                elif dimension == "timeMs":
                    better["timeMs"] = 99
                else:
                    better["metrics"][dimension] += -0.01 if dimension == "interiorDifference" else 0.01
                if dimension not in ("cost", "timeMs"):
                    better["cost"] = 100000
                    better["timeMs"] = 100000
                self.assertEqual(rank_candidates([worse, better])[0]["id"], "better")

    def test_unmatched_bindings_output_or_rubric_have_no_winner(self):
        for field in ("outputKind", "referenceSha256", "cameraSha256", "viewSetSha256", "threshold"):
            with self.subTest(field=field):
                first, second = candidate("a"), candidate("b")
                if field == "outputKind":
                    second[field] = "glb"
                elif field == "threshold":
                    second["features"][0][field] = 3.5
                else:
                    second["evidence"][field] = "d" * 64
                ranked = rank_candidates([first, second])
                self.assertEqual(len(ranked), 2)
                for result in ranked:
                    self.assertEqual(result["status"], "unevaluated")
                    self.assertIsNone(result["rank"])
                    self.assertFalse(result["deliverable"])

    def test_failed_candidate_cannot_conceal_an_unmatched_comparison(self):
        for field in ("outputKind", "cameraSha256", "threshold", "missing-evidence"):
            with self.subTest(field=field):
                first, second = candidate("a"), candidate("b")
                second["gateResults"][0]["status"] = "fail"
                if field == "outputKind":
                    second[field] = "glb"
                elif field == "cameraSha256":
                    second["evidence"][field] = "d" * 64
                elif field == "threshold":
                    second["features"][0][field] = 3.5
                else:
                    del second["evidence"]
                ranked = rank_candidates([first, second])
                for result in ranked:
                    self.assertEqual(result["status"], "unevaluated")
                    self.assertIsNone(result["rank"])
                    self.assertFalse(result["deliverable"])
                self.assertEqual(ranked[1]["hardStatus"], "fail")

    def test_tie_order_is_stable_and_duplicate_ids_do_not_win(self):
        self.assertEqual([entry["id"] for entry in rank_candidates([candidate("b"), candidate("a")])], ["a", "b"])
        self.assertTrue(all(entry["rank"] is None for entry in rank_candidates([candidate(), candidate()])))

    def test_failed_critical_candidate_cannot_be_ranked(self):
        report = candidate()
        report["features"][0]["status"] = "fail"
        result = rank_candidates([report])[0]
        self.assertFalse(result["passed"])
        self.assertIsNone(result["rank"])


    def test_silhouette_precedes_interior_and_critical_precedes_orbit(self):
        first, second = candidate("first"), candidate("second")
        first["metrics"].update(silhouetteIoU=0.91, interiorDifference=1, orbitConsistency=0)
        second["metrics"].update(interiorDifference=0, orbitConsistency=1)
        self.assertEqual(rank_candidates([second, first])[0]["id"], "first")
        first, second = candidate("first"), candidate("second")
        first["features"][0]["score"] = 4
        first["metrics"]["orbitConsistency"] = 0
        second["metrics"]["orbitConsistency"] = 1
        self.assertEqual(rank_candidates([second, first])[0]["id"], "first")


class RepairComparisonTests(unittest.TestCase):
    def test_improvement_can_be_retained_and_deliverable(self):
        before, after = candidate("old"), candidate("new")
        after["metrics"]["silhouetteIoU"] = 0.95
        result = compare_repair(before, after, criteria())
        self.assertEqual(result["status"], "retain")
        self.assertTrue(result["retained"])
        self.assertTrue(result["passed"])
        self.assertTrue(result["deliverable"])

    def test_partial_numeric_repair_is_not_deliverable(self):
        before, after = candidate("old"), candidate("new")
        before["measurements"]["selfIntersectionCount"] = 4
        after["measurements"]["selfIntersectionCount"] = 2
        result = compare_repair(before, after, criteria("selfIntersectionCount", "min", 1))
        self.assertEqual(result["status"], "retain")
        self.assertTrue(result["retained"])
        self.assertFalse(result["passed"])
        self.assertFalse(result["deliverable"])
        self.assertEqual(result["after"]["hardStatus"], "fail")

    def test_partial_semantic_repair_is_not_deliverable(self):
        before, after = candidate("old"), candidate("new")
        before["features"][0].update(status="fail", score=1)
        after["features"][0].update(status="fail", score=2)
        after["metrics"]["silhouetteIoU"] = 0.95
        result = compare_repair(before, after, criteria())
        self.assertTrue(result["retained"])
        self.assertFalse(result["deliverable"])

    def test_new_hard_critical_or_structural_failure_rejects(self):
        for defect in ("rig", "feature", "geometry", "coverage"):
            with self.subTest(defect=defect):
                before, after = candidate("old"), candidate("new")
                after["metrics"]["silhouetteIoU"] = 0.99
                if defect == "rig":
                    after["gateResults"][1]["status"] = "fail"
                elif defect == "feature":
                    after["features"][0]["status"] = "fail"
                elif defect == "geometry":
                    after["measurements"]["depth"] = 0.01
                else:
                    after["measurements"]["partCoveragePassed"] = False
                result = compare_repair(before, after, criteria())
                self.assertEqual(result["status"], "reject")
                self.assertFalse(result["retained"])
                self.assertFalse(result["deliverable"])

    def test_protected_metric_regression_obeys_tolerance(self):
        before, after = candidate("old"), candidate("new")
        after["metrics"].update(silhouetteIoU=0.95, interiorDifference=0.091)
        self.assertEqual(compare_repair(before, after, criteria())["status"], "reject")
        after["metrics"]["interiorDifference"] = 0.085
        self.assertEqual(compare_repair(before, after, criteria())["status"], "retain")

    def test_no_improvement_or_subminimum_is_not_retained(self):
        for score in (0.9, 0.8, 0.905):
            before, after = candidate("old"), candidate("new")
            after["metrics"]["silhouetteIoU"] = score
            self.assertEqual(compare_repair(before, after, criteria())["status"], "reject")

    def test_uncertain_or_missing_evidence_cannot_be_retained(self):
        for defect in ("feature", "producer", "view", "hash", "metric"):
            before, after = candidate("old"), candidate("new")
            after["metrics"]["silhouetteIoU"] = 0.95
            if defect == "feature":
                after["features"][0]["status"] = "uncertain"
            elif defect == "producer":
                del after["gateResults"][0]["producer"]
            elif defect == "view":
                after["views"].remove(180)
            elif defect == "hash":
                after["evidence"]["cameraSha256"] = "d" * 64
            else:
                after["metrics"]["orbitConsistency"] = float("nan")
            result = compare_repair(before, after, criteria())
            self.assertEqual(result["status"], "unevaluated")
            self.assertFalse(result["retained"])
            self.assertFalse(result["deliverable"])

    def test_lowered_threshold_or_removed_check_cannot_hide_failure(self):
        for defect in ("threshold", "feature", "gate"):
            before, after = candidate("old"), candidate("new")
            after["metrics"]["silhouetteIoU"] = 0.95
            if defect == "threshold":
                after["features"][1]["threshold"] = 3
            elif defect == "feature":
                after["features"].pop()
            else:
                after["gateResults"].pop()
            self.assertEqual(compare_repair(before, after, criteria())["status"], "unevaluated")

    def test_criteria_must_be_complete_finite_and_measured(self):
        bad_criteria = [None, {}, criteria(), criteria(), criteria(), criteria(), criteria()]
        bad_criteria[2]["minImprovement"] = True
        bad_criteria[3]["tolerances"] = {}
        bad_criteria[4]["tolerances"]["orbitConsistency"] = float("nan")
        bad_criteria[5]["targetMetric"] = "confidence"
        bad_criteria[6]["minImprovement"] = float("inf")
        before, after = candidate("old"), candidate("new")
        after["metrics"]["silhouetteIoU"] = 0.95
        for settings in bad_criteria:
            with self.subTest(settings=settings):
                result = compare_repair(before, after, settings)
                self.assertEqual(result["status"], "unevaluated")
                self.assertFalse(result["retained"])


    def test_tolerance_cannot_shadow_a_protected_metric(self):
        before, after = candidate("old"), candidate("new")
        after["metrics"].update(silhouetteIoU=0.95, orbitConsistency=0)
        before["measurements"]["orbitConsistency"] = 1
        after["measurements"]["orbitConsistency"] = 1
        settings = criteria()
        del settings["tolerances"]["orbitConsistency"]
        settings["tolerances"]["measurements.orbitConsistency"] = 0
        result = compare_repair(before, after, settings)
        self.assertEqual(result["status"], "unevaluated")
        self.assertFalse(result["retained"])


if __name__ == "__main__":
    unittest.main()
