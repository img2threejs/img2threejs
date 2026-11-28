"""Real offline journals and synthetic bound producer reports; no visual claims."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import Mock

from forge.tests import test_workflow_decisions as fixtures
from forge.tests import test_quality_decision as quality_fixtures
from decision_control import DecisionController, DecisionError, canonical_digest
from workflow_decisions import NativePolicy, _validate_base, verify_lifecycle
from workflow_state import recompute


class WorkflowRepairBudgetTests(unittest.TestCase):
    setUp = fixtures.WorkflowDecisionTests.setUp
    bind = fixtures.WorkflowDecisionTests.bind
    state = fixtures.WorkflowDecisionTests.state
    put_state = fixtures.WorkflowDecisionTests.put_state
    workflow = fixtures.WorkflowDecisionTests.workflow

    def advance_to(self, step):
        state = self.state()
        for row in state["checklist"]:
            if row["id"] == step:
                break
            if row["status"] == "pending":
                self.assertNotIn(row["id"], {"strategy-spec", "strategy-build", "quality-review", "repair-decision"})
                row.update(status="done", evidence=["synthetic-producer"], reason="")
        recompute(state)
        self.put_state(state)

    def publish_spec(self, binding):
        raw = (self.workspace / binding["path"]).read_bytes()
        (self.workspace / self.child_root / "object-sculpt-spec.json").write_bytes(raw)
        self.current_spec = json.loads(raw)
        self.current_binding = binding

    def initial_build(self):
        workflow = self.workflow()
        request = deepcopy(self.request)
        for command, revision in (("strategy-spec", 2), ("strategy-build", 3)):
            if command == "strategy-build":
                self.advance_to(command)
                request["subject"]["revision"] = 2
                request["bindings"][1] = self.current_binding
                proposal = deepcopy(self.current_spec)
                proposal["componentTree"][0]["transform"]["position"][0] += 0.01
                request["proposals"] = {"shift": proposal}
            self.assertEqual(workflow.dispatch(command, request)["action"], "selected")
            begin = dict(request, operation="begin", trialRevision=revision)
            started = workflow.dispatch(command, begin)
            binding = self.bind("spec", f"candidates/rev-{revision}/spec.json", started["spec"])
            workflow.dispatch(command, dict(begin, operation="finish", outputBinding=binding))
            self.publish_spec(binding)
        self.assertEqual(self.controller.correction_counts("case"), {"total": 0, "perPass": {}})
        self.advance_to("quality-review")

    def quality_request(self, intersections=0, *, forbidden=False, revision=None, spec_binding=None,
                        source_binding=None, semantic=False, score=4, feature="pass",
                        mutate_camera=None, prefix=None, view_ids=None, overrides=None, candidate_change=None,
                        target_question="dimension:geometry", hard_failures=None):
        quality = quality_fixtures.QualityDecisionTests()
        quality.setUp()
        self.addCleanup(quality.doCleanups)
        revision = revision or self.state()["decisionLifecycle"]["candidateRevision"]
        prefix = prefix or f"quality/rev-{revision}"
        spec_binding = spec_binding or self.current_binding
        spec = json.loads((self.workspace / spec_binding["path"]).read_bytes())
        quality.subject = dict(self.request["subject"], revision=revision)
        quality.transforms["spec"] = lambda unused: deepcopy(spec)
        if source_binding:
            source = (self.workspace / source_binding["path"]).read_bytes()
            quality.transforms["source"] = lambda unused: source
        if semantic:
            camera = self.semantic_camera(quality.subject, view_ids or quality.view_ids)
            if mutate_camera:
                mutate_camera(camera)
            quality.transforms["camera"] = lambda unused: deepcopy(camera)
        if view_ids is not None:
            quality.view_ids = view_ids
        quality.transforms["render"] = lambda unused: ("synthetic render " + prefix).encode()
        reference = (self.workspace / self.child_root / "reference.png").read_bytes()
        quality.transforms["reference"] = lambda unused: reference
        quality.candidate["measurements"]["selfIntersectionCount"] = intersections
        quality.candidate["gateResults"][0]["status"] = "fail" if intersections else "pass"
        quality.vision_allowed = not forbidden
        if candidate_change:
            candidate_change(quality.candidate)
        quality.hard_failures = hard_failures if hard_failures is not None else ["mesh"] if intersections else []
        quality.refresh()
        request = {key: deepcopy(quality.context[key]) for key in ("subject", "candidates", "observations")}
        request["bindings"] = []
        for role, binding in quality.bindings.items():
            if role == "spec":
                copied = deepcopy(spec_binding)
            elif role == "gate-report" and semantic:
                copied = self.bind(role, f"candidates/rev-{revision}/{prefix}/gate-report.json",
                                   (quality.workspace / binding["path"]).read_bytes())
            elif role == "reference":
                copied = deepcopy(self.request["bindings"][0])
            elif role == "source" and source_binding:
                copied = deepcopy(source_binding)
            else:
                copied = self.bind(role, f"{prefix}/{role}.json",
                                   (quality.workspace / binding["path"]).read_bytes())
            request["bindings"].append(copied)
        self.policy = replace(self.policy, requirements=quality.requirements,
            permissions=frozenset({"write-spec"}), repair_criteria={
                "targetMetric": "selfIntersectionCount", "direction": "min", "minImprovement": 1,
                "tolerances": {"silhouetteIoU": 0, "interiorDifference": 0.01, "orbitConsistency": 0.01}})
        if semantic:
            criteria = dict(self.policy.repair_criteria)
            criteria["tolerances"] = dict(criteria["tolerances"])
            questions = ["dimension:" + item for item in quality.requirements["dimensions"]] + ["critical:handle"]
            criteria["semantic"] = {"targetQuestion": target_question, "minImprovement": 1,
                "choiceScores": {"pass": 4, "fail": 0},
                "tolerances": {key: 0 for key in questions if key != target_question}}
            self.policy = replace(self.policy, repair_criteria=criteria)
        ordinary = fixtures.FixtureFactory.provider
        def provider(context):
            if context["group"] != "quality-review":
                return ordinary(self.factory, context)
            def answer(projected):
                self.factory.calls.append(deepcopy(projected))
                result = quality.advisory(projected, score=score, feature=feature)
                for key, value in (overrides or {}).items():
                    answer = result["answers"][key]
                    answer.update(score=value, probabilities={str(number): int(number == value) for number in range(5)})
                return result
            return answer
        self.factory.provider = provider
        return request, deepcopy(json.loads((quality.workspace / quality.bindings["quality-report"]["path"]).read_bytes())["candidate"])

    @staticmethod
    def semantic_camera(subject, views):
        identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        return {"kind": "img2.repair-camera", "version": 1, "subject": deepcopy(subject),
            "views": [{"id": angle, "matrixWorld": list(identity), "projectionMatrix": list(identity),
                "framing": [0, 0, 192, 192], "timeMs": 0, "width": 192, "height": 192,
                "pose": {"id": "static", "transforms": [{"id": "root", "matrixWorld": list(identity)}]}}
                for angle in views],
            "renderer": {"engine": "synthetic", "version": "fixture-v1", "width": 192, "height": 192,
                "samples": 1, "exposure": 1, "worldColor": [0, 0, 0], "worldStrength": 1, "lights": []}}

    def repair_request(self, quality_request, report):
        request = deepcopy(quality_request)
        request["bindings"] = [binding for binding in request["bindings"] if binding["role"] != "gate-report"]
        failure = {"subject": deepcopy(request["subject"]), "bindings": deepcopy(request["bindings"]),
                   "report": deepcopy(report), "faults": []}
        revision = request["subject"]["revision"]
        request["bindings"].append(self.bind("gate-report", f"repair/failed-{revision}.json", failure))
        request["failure"] = failure
        plan = deepcopy(self.request["candidates"][0])
        request["candidates"] = [{"id": "shift", "hypothesis": "geometry", "action": "refine-spec",
                                  "plan": plan, "permissions": ["write-spec"]}]
        return request

    def repair(self, request, intersections=0):
        workflow = self.workflow()
        selected = workflow.dispatch("repair", request)
        self.assertEqual(selected["status"], "refine-spec")
        trial = request["subject"]["revision"] + 1
        begun = workflow.dispatch("repair", dict(request, operation="begin", trialRevision=trial))
        count = self.controller.correction_counts("case")
        self.assertEqual(self.state()["loops"]["total"], count["total"])
        reopened = DecisionController(self.workspace, "workflow-fixture", clock=lambda: 1000)
        self.assertEqual(reopened.correction_counts("case"), count)
        workflow = self.workflow(controller=reopened)
        resumed = workflow.dispatch("repair", dict(request, operation="begin", trialRevision=trial))
        self.assertEqual(resumed["application"], begun["application"])
        self.assertEqual(reopened.correction_counts("case"), count)
        after_bindings = [deepcopy(binding) for binding in request["bindings"]
                          if binding["role"] not in {"spec", "source", "gate-report"}]
        successor = deepcopy(self.current_spec)
        successor["componentTree"][0]["transform"]["position"][0] += 0.01
        output = self.bind("spec", f"candidates/rev-{trial}/spec.json", successor)
        after_bindings.extend([output, self.bind("source", f"candidates/rev-{trial}/source.ts", b"synthetic repaired source")])
        report = deepcopy(request["failure"]["report"])
        report["measurements"]["selfIntersectionCount"] = intersections
        report["gateResults"][0]["status"] = "fail" if intersections else "pass"
        subject = dict(request["subject"], revision=trial)
        after = {"subject": subject, "bindings": deepcopy(after_bindings), "report": report, "faults": []}
        after_bindings.append(self.bind("gate-report", f"candidates/rev-{trial}/gates.json", after))
        result = workflow.dispatch("repair", dict(request, operation="finish", trialRevision=trial,
            after=after, afterSubject=subject, afterBindings=after_bindings))
        self.assertTrue(result["retained"])
        self.assertEqual(reopened.correction_counts("case"), count)
        self.assertEqual(self.state()["loops"]["total"], count["total"])
        self.assertEqual(self.state()["decisionLifecycle"]["incumbentRevision"], 1)
        self.assertEqual(self.state()["routeChild"]["revision"], 1)
        self.publish_spec(output)
        self.assertEqual(self.state()["currentStep"], "build-current-pass")
        self.advance_to("quality-review")
        return result

    def assert_lifecycle(self, state=None):
        with self.controller.workspace_transaction(consent=lambda: deepcopy(self.grant)) as verify:
            verify_lifecycle(verify, state or self.state(), self.workspace / self.child_root, self.workspace)

    def passing_review(self):
        request, unused = self.quality_request()
        self.assertEqual(self.workflow().dispatch("quality", request)["action"], "continue")
        self.assert_lifecycle()

    def test_hard_failure_skips_without_paid_visual_then_repairs_and_reopens_terminal(self):
        self.initial_build()
        request, report = self.quality_request(4, forbidden=True)
        calls = len(self.factory.calls)
        result = self.workflow().dispatch("quality", request)
        self.assertEqual(result["reasonCode"], "deterministic-hard-gate-failed")
        self.assertIsNone(result["decision"])
        self.assertEqual(len(self.factory.calls), calls)
        state = self.state()
        self.assertEqual(state["currentStep"], "repair-decision")
        self.assertEqual(len(state["decisionRefs"]), 2)
        self.assertEqual(state["decisionBlocks"][0]["context"]["subject"], request["subject"])
        with self.assertRaises(DecisionError):
            self.assert_lifecycle()
        self.repair(self.repair_request(request, report))
        self.passing_review()
        self.controller = DecisionController(self.workspace, "workflow-fixture", clock=lambda: 1000)
        self.assertEqual(self.controller.correction_counts("case"), {"total": 1, "perPass": {"blockout": 1}})
        self.assert_lifecycle()
        self.completed = self.state()
        for mutation in ("missing-block", "forged-assessment", "missing-history", "final-skip"):
            state = deepcopy(self.completed)
            if mutation == "missing-block":
                state.pop("decisionBlocks")
            elif mutation == "forged-assessment":
                block = state["decisionBlocks"][0]
                block["assessment"]["report"]["measurements"]["selfIntersectionCount"] = 99
                block["blockId"] = canonical_digest({key: value for key, value in block.items() if key != "blockId"})
            elif mutation == "missing-history":
                state.pop("passHistory")
            else:
                row = next(row for row in state["checklist"] if row["id"] == "quality-review")
                row.update(status="skipped", evidence=[state["decisionBlocks"][0]["blockId"]],
                           reason="deterministic-hard-gate-failed")
            with self.subTest(mutation=mutation), self.assertRaises(DecisionError):
                self.assert_lifecycle(state)

    def test_declined_jev_can_record_nonvisual_failure_and_repair_with_explicit_host(self):
        self.grant.update(granted=False, purposes=[])
        factory = self.factory
        host_calls = []
        class Host:
            def __call__(self, projected):
                raise AssertionError("fallback must bind its admitted context")
            def bind_context(self, workspace, context):
                def answer(projected):
                    result = factory.provider(context)(projected)
                    result["model"] = "offline-host-fixture"
                    host_calls.append(projected["group"])
                    return result
                return answer
        ordinary_workflow = self.workflow
        def workflow(**kwargs):
            value = ordinary_workflow(**kwargs)
            value.provider_factory = None
            value.fallback = Host()
            return value
        self.workflow = workflow
        self.initial_build()
        request, report = self.quality_request(4, forbidden=True)
        before = list(host_calls)
        blocked = self.workflow().dispatch("quality", request)
        self.assertEqual(blocked["reasonCode"], "deterministic-hard-gate-failed")
        self.assertEqual(host_calls, before)
        self.assertEqual(self.state()["currentStep"], "repair-decision")
        self.repair(self.repair_request(request, report))
        self.passing_review()
        self.assertEqual(self.controller.correction_counts("case"), {"total": 1, "perPass": {"blockout": 1}})
        self.assertTrue(all(item["record"]["decider"] == "model:offline-host-fixture"
                            for item in self.state()["decisionRefs"]))
        self.assert_lifecycle()

    def test_third_repair_can_be_reviewed_but_fourth_cannot_start(self):
        self.initial_build()
        for before, after in ((6, 4), (4, 2), (2, 0)):
            request, report = self.quality_request(before, forbidden=True)
            self.assertEqual(self.workflow().dispatch("quality", request)["action"], "refine")
            self.repair(self.repair_request(request, report), after)
        self.assertEqual(self.controller.correction_counts("case"), {"total": 3, "perPass": {"blockout": 3}})
        self.passing_review()
        # A newly required repair is refused before provider or application start.
        request, report = self.quality_request(1, forbidden=True)
        state = self.state()
        for row in state["checklist"]:
            if row["id"] in {"quality-review", "repair-decision"}:
                row.update(status="pending", evidence=[], reason="")
        recompute(state)
        self.put_state(state)
        self.workflow().dispatch("quality", request)
        calls = len(self.factory.calls)
        result = self.workflow().dispatch("repair", self.repair_request(request, report))
        self.assertEqual(result["reasonCode"], "correction-budget-exhausted")
        self.assertEqual(len(self.factory.calls), calls)
        self.assertEqual(self.controller.correction_counts("case")["total"], 3)

    def test_controller_counts_repair_identity_once_and_enforces_total_six(self):
        future = None
        for number in range(6):
            request = deepcopy(self.request)
            request["subject"].update(revision=10 + number * 2,
                                      passId="blockout" if number < 3 else "detail")
            context = self.workflow()._context("repair", request)
            record = self.controller.consult(context, provider=self.factory.provider(context),
                consent=lambda: deepcopy(self.grant), availability=lambda: {"status": "present"},
                request_token_limit=128)
            self.assertEqual(record["status"], "answered")
            if number == 2:
                later = deepcopy(context)
                later["subject"]["revision"] = 100
                future = self.controller.consult(later, provider=self.factory.provider(later),
                    consent=lambda: deepcopy(self.grant), availability=lambda: {"status": "present"},
                    request_token_limit=128)
                self.assertEqual(future["status"], "answered")
            application = self.controller.begin_application(record, 11 + number * 2,
                consent=lambda: deepcopy(self.grant))
            self.assertEqual(self.controller.begin_application(record, 11 + number * 2,
                consent=lambda: deepcopy(self.grant)), application)
            self.controller.finish_application(application["applicationId"], {"status": "completed"},
                consent=lambda: deepcopy(self.grant))
            if number == 2:
                with self.assertRaises(DecisionError) as caught:
                    self.controller.begin_application(future, 101, consent=lambda: deepcopy(self.grant))
                self.assertEqual(caught.exception.reason_code, "correction-budget-exhausted")
        reopened = DecisionController(self.workspace, "workflow-fixture", clock=lambda: 1000)
        self.assertEqual(reopened.correction_counts("case"),
                         {"total": 6, "perPass": {"blockout": 3, "detail": 3}})
        request = deepcopy(self.request)
        request["subject"].update(revision=200, passId="new-pass")
        context = self.workflow()._context("repair", request)
        refused = reopened.consult(context, provider=self.factory.provider(context),
            consent=lambda: deepcopy(self.grant), availability=lambda: {"status": "present"},
            request_token_limit=128)
        self.assertEqual(refused["reasonCode"], "correction-budget-exhausted")
        # The final allowed completed application and mandatory new strategy
        # remain usable after allowance exhaustion.
        self.assertEqual(reopened.begin_application(record, 21, consent=lambda: deepcopy(self.grant))["status"],
                         "completed")
        context = self.workflow()._context("build-strategy", request)
        selected = reopened.consult(context, provider=self.factory.provider(context),
            consent=lambda: deepcopy(self.grant), availability=lambda: {"status": "present"},
            request_token_limit=128)
        self.assertEqual(selected["status"], "answered")
        self.assertEqual(reopened.begin_application(selected, 201,
            consent=lambda: deepcopy(self.grant))["status"], "pending")
        self.assertEqual(reopened.correction_counts("case")["total"], 6)

    def test_permission_missing_evidence_and_nonfailure_admission_do_not_skip(self):
        self.initial_build()
        request, report = self.quality_request(4, forbidden=True, hard_failures=["permission-denied"])
        self.assertEqual(self.workflow().dispatch("quality", request)["action"], "request-input")
        self.assertNotIn("decisionBlocks", self.state())
        request, report = self.quality_request(forbidden=True)
        self.assertEqual(self.workflow().dispatch("quality", request)["action"], "request-input")
        self.assertEqual(self.state()["currentStep"], "quality-review")
        request, report = self.quality_request(4, forbidden=True)
        request["bindings"] = [binding for binding in request["bindings"] if binding["role"] != "render-crop-handle"]
        self.assertEqual(self.workflow().dispatch("quality", request)["action"], "request-input")
        self.assertNotIn("decisionBlocks", self.state())
        self.authorized = False
        with self.assertRaises(DecisionError):
            self.workflow().dispatch("quality", request)

    def test_block_publication_rechecks_live_authority_under_lease(self):
        self.initial_build()
        request, unused = self.quality_request(4, forbidden=True)
        calls = []
        def authorize(*args):
            calls.append(args[0])
            return len(calls) == 1
        policy = replace(self.policy, authorize=authorize)
        with self.assertRaises(DecisionError):
            self.workflow(policy=policy).dispatch("quality", request)
        self.assertEqual(self.state()["currentStep"], "quality-review")
        self.assertNotIn("decisionBlocks", self.state())


    def test_native_pass_sentinel_allows_begun_trial_review_and_retention(self):
        result, unused_spec, unused_source, unused_review = self.semantic_trial(native=True)
        self.assertTrue(result["retained"])
        self.assertEqual(self.state()["currentPass"], "complete")
        self.assertEqual(self.state()["currentStep"], "quality-review")

    def test_native_pass_sentinel_does_not_bypass_live_pass_resolution(self):
        with self.assertRaises(DecisionError) as caught:
            self.semantic_trial(native=True, fault="wrong-native-pass")
        self.assertEqual(caught.exception.reason_code, "locked-or-wrong-pass")
        self.assertEqual(self.controller.correction_counts("case")["total"], 0)

    def semantic_trial(self, fault=None, *, before_score=2, after_score=4, before_overrides=None,
                       after_overrides=None, before_feature="pass", target_question="dimension:geometry",
                       native=False):
        self.initial_build()
        if native:
            self.policy = replace(self.policy, native=NativePolicy(
                validate_spec=_validate_base, current_pass=lambda spec: "blockout",
                authorize_native=lambda context, rules: self.authorized,
                rules=(), frozen_paths=frozenset(), forbidden_components=frozenset()))
            state = self.state()
            # Native child lifecycle rows are setup-scoped, not sculpt-pass rows.
            for row in state["checklist"]:
                if row["scope"] == "pass":
                    row["scope"] = "setup"
            state["currentPass"] = "complete"
            self.put_state(state)
        request, report = self.quality_request(semantic=True, score=before_score, overrides=before_overrides,
            feature=before_feature, target_question=target_question)
        self.assertTrue(all(gate["status"] == "pass" for gate in report["gateResults"]))
        reviewed = self.workflow().dispatch("quality", request)
        self.assertEqual(reviewed["action"], "refine")
        self.assertEqual(reviewed["reasonCode"], "semantic-quality-failed")
        repair_request = self.repair_request(request, report)
        selected = self.workflow().dispatch("repair", repair_request)
        self.assertEqual(selected["status"], "refine-spec")
        self.assertTrue(all(gate["status"] == "pass" for gate in selected["context"]["observations"]["before"]["gateResults"]))
        if fault == "wrong-native-pass":
            self.policy = replace(self.policy, native=replace(
                self.policy.native, current_pass=lambda spec: "detail"))
        if fault != "no-begin":
            begun = self.workflow().dispatch("repair", dict(repair_request, operation="begin", trialRevision=4))
            self.assertEqual(self.state()["decisionPending"]["application"], begun["application"])
        successor = deepcopy(self.current_spec)
        successor["componentTree"][0]["transform"]["position"][0] += 0.01
        output = self.bind("spec", "candidates/rev-4/semantic-spec.json", successor)
        source = self.bind("source", "candidates/rev-4/semantic-source.ts", b"synthetic semantic repair source")
        def change_camera(camera):
            if fault == "camera":
                camera["views"][0]["matrixWorld"][12] = 1
            elif fault == "pose":
                camera["views"][0]["pose"]["transforms"][0]["matrixWorld"][12] = 1
            elif fault == "unknown-camera-field":
                camera["unaccountedFraming"] = True
        def change_candidate(candidate):
            if fault == "metric-regression":
                candidate["metrics"]["silhouetteIoU"] = 0.1
            elif fault == "gate-producer":
                candidate["gateResults"][0]["producer"] = "different-producer"
        after_request, after_report = self.quality_request(1 if fault == "trial-hard-gate" else 0,
            forbidden=fault == "trial-hard-gate", semantic=True, score=after_score,
            feature="uncertain" if fault == "unevaluated" else "pass", revision=4,
            spec_binding=output, source_binding=source, prefix="quality/rev-4/post-trial",
            mutate_camera=change_camera, candidate_change=change_candidate,
            view_ids=[0, 90, 270] if fault == "views" else None, overrides=after_overrides,
            target_question=target_question)
        if fault == "wrong-trial":
            after_request["subject"]["revision"] = 5
        elif fault == "closed-application":
            self.controller.finish_application(begun["application"]["applicationId"], {"status": "rejected"},
                                               consent=lambda: deepcopy(self.grant))
        elif fault == "revoked-policy":
            self.authorized = False
        elif fault == "unsafe-path":
            next(item for item in after_request["bindings"] if item["role"] == "source")["path"] = "../source.ts"
        elif fault == "scope-revoked":
            scope = dict(self.policy.scope)
            scope["components"] = {"foreign"}
            self.policy = replace(self.policy, scope=scope)
        before_trial_review = self.state()
        calls = len(self.factory.calls)
        counts = self.controller.correction_counts("case")
        canonical = (self.workspace / self.child_root / "object-sculpt-spec.json").read_bytes()
        parent = (self.workspace / ".img2threejs/state.json").read_bytes()
        try:
            post = (self.workflow().dispatch("quality", dict(after_request, operation="trial"))
                    if fault != "missing-review" else {})
        except DecisionError:
            self.assertEqual(len(self.factory.calls), calls)
            self.assertEqual(self.state(), before_trial_review)
            self.assertEqual(self.controller.correction_counts("case"), counts)
            raise
        after_review = post.get("qualityReview")
        retained_review = deepcopy(after_review)
        self.assertEqual(self.state()["currentStep"], "repair-decision")
        self.assertEqual(self.state()["decisionLifecycle"], before_trial_review["decisionLifecycle"])
        self.assertEqual(self.state()["decisionRefs"], before_trial_review["decisionRefs"])
        self.assertEqual((self.workspace / self.child_root / "object-sculpt-spec.json").read_bytes(), canonical)
        self.assertEqual((self.workspace / ".img2threejs/state.json").read_bytes(), parent)
        if fault == "trial-hard-gate":
            self.assertEqual(len(self.factory.calls), calls)
            self.assertNotIn("afterQualityReview", self.state()["decisionPending"])
        if after_review is not None:
            self.assertEqual(post["action"], "trial-reviewed")
            self.assertFalse(post["deliverable"])
            self.assertEqual(self.state()["decisionPending"]["afterQualityReview"], after_review)
        if fault == "forged-record":
            after_review["record"]["answers"]["dimension:geometry"]["score"] = 4
            after_review["record"]["decider"] = "forged"
        after_bindings = [deepcopy(binding) for binding in after_request["bindings"] if binding["role"] != "gate-report"]
        if fault == "source":
            after_bindings = [binding for binding in after_bindings if binding["role"] != "source"]
            after_bindings.append(self.bind("source", "candidates/rev-4/unreviewed-source.ts", b"unreviewed source"))
        after_subject = dict(request["subject"], revision=4)
        after = {"subject": after_subject, "bindings": deepcopy(after_bindings), "report": after_report, "faults": []}
        after_bindings.append(self.bind("gate-report", "candidates/rev-4/semantic-gates.json", after))
        finish = dict(repair_request, operation="finish", trialRevision=4,
                      after=after, afterSubject=after_subject, afterBindings=after_bindings)
        if fault in {"forged-record", "matching-envelope"}:
            finish["afterQualityReview"] = after_review
        result = self.workflow().dispatch("repair", finish)
        self.assertEqual(self.controller.correction_counts("case")["total"], 1)
        self.assertEqual(self.state()["decisionLifecycle"]["incumbentRevision"], 1)
        if result["retained"]:
            self.publish_spec(output)
            self.advance_to("quality-review")
        else:
            self.assertEqual(self.state()["decisionLifecycle"]["candidateRevision"], 3)
            self.assertEqual(self.state()["status"], "stopped")
        return result, output, source, retained_review

    def test_semantic_only_failure_repairs_with_real_reviews_and_fresh_terminal(self):
        result, output, source, old_after = self.semantic_trial()
        self.assertTrue(result["retained"])
        self.assertTrue(result["deliverable"])
        self.assertIn("qualityReviews", result["application"]["outcome"])
        request, unused = self.quality_request(semantic=True, spec_binding=output, source_binding=source,
                                              prefix="quality/rev-4/canonical-fresh")
        self.assertEqual(self.workflow().dispatch("quality", request)["action"], "continue")
        self.assert_lifecycle()
        self.controller = DecisionController(self.workspace, "workflow-fixture", clock=lambda: 1000)
        self.assert_lifecycle()
        with self.controller.workspace_transaction(consent=lambda: deepcopy(self.grant)) as verify:
            self.assertEqual(verify.historical_quality(old_after["record"], old_after["context"])["status"], "answered")
            with self.assertRaises(DecisionError) as caught:
                verify(old_after["record"], old_after["context"])
            self.assertEqual(caught.exception.reason_code, "stale-decision")
            wrong_group = self.state()["decisionRefs"][0]
            with self.assertRaises(DecisionError):
                verify.historical_quality(wrong_group["record"], wrong_group["context"])
            forged = deepcopy(old_after["record"])
            forged["decider"] = "forged"
            with self.assertRaises(DecisionError):
                verify.historical_quality(forged, old_after["context"])
            self.grant["granted"] = False
            with self.assertRaises(DecisionError):
                verify.historical_quality(old_after["record"], old_after["context"])
            self.grant["granted"] = True
            binding = next(item for item in old_after["context"]["bindings"] if item["role"] == "render")
            (self.workspace / binding["path"]).write_bytes(b"changed historical render")
            with self.assertRaises(DecisionError):
                verify.historical_quality(old_after["record"], old_after["context"])
        with self.assertRaises(DecisionError) as caught:
            verify.historical_quality(old_after["record"], old_after["context"])
        self.assertEqual(caught.exception.reason_code, "transaction-closed")

    def test_semantic_partial_improvement_is_not_terminal(self):
        result, unused, unused_source, unused_review = self.semantic_trial(
            before_score=4, after_score=4,
            before_overrides={"dimension:geometry": 1}, after_overrides={"dimension:geometry": 2})
        self.assertTrue(result["retained"])
        self.assertFalse(result["deliverable"])
        with self.assertRaises(DecisionError):
            self.assert_lifecycle()

    def test_semantic_no_improvement_rejects(self):
        self.assertFalse(self.semantic_trial(after_score=2)[0]["retained"])

    def test_semantic_changed_camera_rejects(self):
        self.assertFalse(self.semantic_trial("camera")[0]["retained"])

    def test_semantic_changed_pose_rejects(self):
        self.assertFalse(self.semantic_trial("pose")[0]["retained"])

    def test_semantic_unknown_capture_metadata_rejects(self):
        self.assertFalse(self.semantic_trial("unknown-camera-field")[0]["retained"])

    def test_semantic_changed_view_inventory_rejects(self):
        with self.assertRaises(DecisionError):
            self.semantic_trial("views")

    def test_semantic_forged_record_rejects(self):
        with self.assertRaises(DecisionError) as caught:
            self.semantic_trial("forged-record")
        self.assertEqual(caught.exception.reason_code, "semantic-quality-review-mismatch")

    def test_semantic_unreviewed_source_rejects(self):
        self.assertFalse(self.semantic_trial("source")[0]["retained"])

    def test_semantic_unevaluated_review_rejects(self):
        with self.assertRaises(DecisionError):
            self.semantic_trial("unevaluated")

    def test_semantic_missing_posttrial_review_rejects(self):
        with self.assertRaises(DecisionError):
            self.semantic_trial("missing-review")

    def test_semantic_metric_regression_rejects(self):
        self.assertFalse(self.semantic_trial("metric-regression")[0]["retained"])

    def test_semantic_gate_producer_change_rejects(self):
        self.assertFalse(self.semantic_trial("gate-producer")[0]["retained"])

    def test_semantic_protected_rubric_regression_rejects(self):
        self.assertFalse(self.semantic_trial(after_overrides={"dimension:material-appearance": 1})[0]["retained"])


    def test_trial_quality_requires_actual_begun_application(self):
        with self.assertRaises(DecisionError):
            self.semantic_trial("no-begin")

    def test_trial_quality_rejects_wrong_successor(self):
        with self.assertRaises(DecisionError) as caught:
            self.semantic_trial("wrong-trial")
        self.assertEqual(caught.exception.reason_code, "subject-mismatch")

    def test_trial_quality_rejects_closed_application(self):
        with self.assertRaises(DecisionError):
            self.semantic_trial("closed-application")

    def test_trial_quality_rechecks_live_policy(self):
        with self.assertRaises(DecisionError):
            self.semantic_trial("revoked-policy")

    def test_trial_quality_rechecks_scope(self):
        with self.assertRaises(DecisionError):
            self.semantic_trial("scope-revoked")

    def test_trial_quality_rejects_unsafe_candidate_paths(self):
        with self.assertRaises(DecisionError):
            self.semantic_trial("unsafe-path")

    def test_trial_quality_hard_gate_does_not_consult_or_promote(self):
        with self.assertRaises(DecisionError):
            self.semantic_trial("trial-hard-gate")

    def test_repair_finish_accepts_only_matching_optional_review_envelope(self):
        self.assertTrue(self.semantic_trial("matching-envelope")[0]["retained"])

    def test_semantic_critical_choice_uses_frozen_pass_fail_mapping(self):
        result, unused, unused_source, unused_review = self.semantic_trial(
            before_score=4, before_feature="fail", target_question="critical:handle")
        self.assertTrue(result["retained"])
        self.assertEqual(result["comparison"]["improvement"], 4)

    def test_fallback_binds_full_context_only_when_selected(self):
        bound = []
        factory = self.factory
        class BoundFallback:
            def __call__(self, projected):
                raise AssertionError("unbound fallback dispatched")
            def bind_context(self, workspace, context):
                bound.append((workspace, deepcopy(context)))
                def answer(projected):
                    self_test.assertTrue(all("path" not in item for item in projected["bindings"]))
                    result = fixtures.FixtureFactory.provider(factory, context)(projected)
                    result["model"] = "host-model"
                    return result
                return answer
        self_test = self
        context = self.workflow()._context("build-strategy", self.request)
        callbacks = {"consent": lambda: deepcopy(self.grant), "request_token_limit": 128,
                     "fallback": BoundFallback()}
        healthy = self.controller.consult(context, provider=self.factory.provider(context),
            availability=lambda: {"status": "present"}, **callbacks)
        self.assertEqual(healthy["status"], "answered")
        self.assertEqual(bound, [])
        context["subject"]["revision"] = 2
        outage = self.controller.consult(context, availability=lambda: {"status": "not-installed"}, **callbacks)
        self.assertEqual(outage["status"], "answered")
        self.assertEqual(len(bound), 1)
        self.assertEqual(bound[0], (self.workspace, context))
        self.assertTrue(all("path" in item for item in bound[0][1]["bindings"]))

    def test_plain_mock_fallback_remains_callable_without_synthetic_binder(self):
        context = self.workflow()._context("build-strategy", self.request)
        def answer(projected):
            result = fixtures.FixtureFactory.provider(self.factory, context)(projected)
            result["model"] = "host-model"
            return result
        fallback = Mock(side_effect=answer)
        result = self.controller.consult(context, fallback=fallback,
            consent=lambda: deepcopy(self.grant), availability=lambda: {"status": "not-installed"},
            request_token_limit=128)
        self.assertEqual(result["status"], "answered")
        fallback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
