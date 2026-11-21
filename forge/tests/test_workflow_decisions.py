"""Offline routed lifecycle fixtures. No real capture/provider/quality claims."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "forge"))
sys.path.insert(0, str(ROOT / "forge/_shared"))
from decision_control import DecisionController, DecisionError, canonical_digest
from workflow_state import new_state, attach_lifecycle_steps, mark_steps, WorkflowStateError, recompute
from workflow_decisions import WorkflowDecisions, WorkflowPolicy, NativePolicy, verify_lifecycle, _validate_base
from forge.stage3_build.orchestrate_passes import sync_pipeline
from forge.decision import command_output, load_policy


class FixtureFactory:
    """Synthetic provider transport, never independent scene evidence."""
    def __init__(self):
        self.calls = []

    def provider(self, context):
        def answer(projected):
            self.calls.append(deepcopy(projected))
            ids = [item["id"] for item in projected["candidates"]]
            return {"kind": "img2.decision-advisory", "version": 1, "group": projected["group"],
                "subject": projected["subject"], "contextDigest": canonical_digest(projected),
                "model": "jev-1.14.0", "usage": {"input_tokens": 10, "output_tokens": 2},
                "answers": {"selection": {"type": "choice", "choice": ids[0],
                    "probabilities": {ident: int(ident == ids[0]) for ident in ids}, "confidence": 1},
                    "anyFits": {"type": "noul", "noul": 1}}}
        return answer


class WorkflowDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve()
        self.controller = DecisionController(self.workspace, "workflow-fixture", clock=lambda: 1000)
        self.child_root = ".img2threejs/objects/case/rev-1"
        self.state_path = self.child_root + "/state.json"
        self.source = json.loads((ROOT / "forge/tests/fixtures/reference_target_conformance_spec.json").read_text())
        self.original = deepcopy(self.source)
        self.proposed = deepcopy(self.source)
        self.proposed["componentTree"][0]["transform"]["position"][0] += 0.01
        self.request = {"subject": {"runId": "workflow-fixture", "objectId": "case", "revision": 1, "passId": "blockout"},
            "bindings": [self.bind("reference", "reference.png", b"synthetic offline reference"),
                         self.bind("spec", "draft.json", self.source),
                         self.bind("source", "source.ts", b"synthetic source snapshot"),
                         self.bind("critical-features", "critical.json", {"critical": "synthetic"})],
            "candidates": [{"id": "shift", "passId": "blockout", "components": ["root"],
                "operations": ["reposition"], "fieldPaths": ["transform.position"],
                "materialSources": [], "dependencies": [], "rigHandoff": False}],
            "observations": {}, "proposals": {"shift": self.proposed}}
        reference = self.request["bindings"][0]
        child = new_state("reference.png", spec="object-sculpt-spec.json")
        child["routeChild"] = {"runId": "workflow-fixture", "objectId": "case", "revision": 1,
                               "ownerRoute": "base:generic", "outputKind": "3d-object", "cropSha256": reference["sha256"]}
        child["leaseToken"] = self.controller._token
        attach_lifecycle_steps(child)
        for row in child["checklist"]:
            if row["id"] == "strategy-spec":
                break
            row["status"] = "done"
        recompute(child)
        self.put_state(child)
        parent = new_state("reference.png")
        parent.update(runId="workflow-fixture", leaseToken=self.controller._token,
                      objects=[dict(child["routeChild"], statePath=self.state_path)])
        (self.workspace / ".img2threejs/state.json").write_text(json.dumps(parent))
        self.factory = FixtureFactory()
        self.authorized = True
        self.availability = "present"
        self.grant = {"granted": True, "purposes": ["routing", "build-strategy", "quality-review", "repair"],
                      "sha256": "c" * 64, "tokenBudget": 100000}
        self.policy = WorkflowPolicy(scope={"components": {"root"}, "operations": {"reposition"},
            "field_paths": {"transform.position"}, "authorized_sources": set(), "source_operations": set(),
            "available_dependencies": set(), "rig_required": False, "rig_available": False},
            requirements={"criticalFeatures": [], "requiredViews": []},
            authorize=lambda *args: self.authorized, draft_spec="draft.json", request_token_limit=128)

    def bind(self, role, name, value):
        raw = value if type(value) is bytes else json.dumps(value, sort_keys=True).encode()
        path = self.child_root + "/" + name
        target = self.workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return {"role": role, "path": path, "sha256": hashlib.sha256(raw).hexdigest()}

    def state(self):
        return json.loads((self.workspace / self.state_path).read_text())

    def put_state(self, state):
        path = self.workspace / self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))

    def workflow(self, *, controller=None, policy=None):
        return WorkflowDecisions(controller or self.controller, self.state_path, policy or self.policy,
            provider_factory=self.factory, consent=lambda: deepcopy(self.grant),
            availability=lambda: {"status": self.availability})

    def test_legacy_checklist_is_unchanged_and_new_steps_guard_real_boundaries(self):
        legacy = new_state("reference.png")
        self.assertNotIn("decisionLifecycle", legacy)
        ids = [row["id"] for row in self.state()["checklist"]]
        self.assertLess(ids.index("strategy-spec"), ids.index("spec-authoring"))
        self.assertLess(ids.index("strategy-build"), ids.index("build-current-pass"))
        self.assertLess(ids.index("pass-gate-check"), ids.index("quality-review"))
        self.assertLess(ids.index("quality-review"), ids.index("repair-decision"))
        with self.assertRaises(WorkflowStateError):
            mark_steps(self.state(), ["strategy-spec"], status="done", evidence=["forged"])

    def test_selection_never_completes_publication_before_validated_successor(self):
        workflow = self.workflow()
        result = workflow.dispatch("strategy-spec", self.request)
        self.assertEqual(result["action"], "selected")
        state = self.state()
        self.assertEqual(state["currentStep"], "strategy-spec")
        self.assertEqual(state["decisionRefs"], [])
        self.assertEqual(state["decisionLifecycle"]["candidateRevision"], 1)
        self.assertIn("strategyProposalDigests", self.factory.calls[0]["observations"])
        self.assertTrue(all("path" not in binding for binding in self.factory.calls[0]["bindings"]))
        begin = dict(self.request, operation="begin", trialRevision=2)
        started = workflow.dispatch("strategy-spec", begin)
        self.assertEqual(started["application"]["status"], "pending")
        output = self.bind("spec", "candidates/rev-2/spec.json", started["spec"])
        # Recreate the parent controller to exercise durable same-lease resume.
        resumed = self.workflow(controller=DecisionController(self.workspace, "workflow-fixture", clock=lambda: 1000))
        finished = resumed.dispatch("strategy-spec", dict(begin, operation="finish", outputBinding=output))
        self.assertEqual(finished["application"]["status"], "completed")
        self.assertEqual(self.state()["loops"]["total"], 0)
        self.assertEqual(resumed.controller.correction_counts("case"), {"total": 0, "perPass": {}})
        state = self.state()
        self.assertEqual(state["currentStep"], "spec-authoring")
        self.assertEqual(state["decisionLifecycle"], {"version": 1, "candidateRevision": 2, "incumbentRevision": 1})
        self.assertEqual(state["routeChild"]["revision"], 1)
        self.assertEqual(json.loads((self.workspace / self.child_root / "draft.json").read_text()), self.original)
        self.assertFalse((self.workspace / self.child_root / "object-sculpt-spec.json").exists())
        with self.controller.workspace_transaction(consent=lambda: deepcopy(self.grant)) as verify:
            receipt = state["decisionRefs"][0]
            self.assertEqual(verify(receipt["record"], receipt["context"])["status"], "answered")
            self.assertEqual(verify.application(receipt["application"]["applicationId"])["status"], "completed")

    def test_wrong_subject_pass_or_lease_rejects_before_provider(self):
        for key, value in (("runId", "other"), ("objectId", "other"), ("revision", 2), ("passId", "material-pass")):
            request = deepcopy(self.request)
            request["subject"][key] = value
            with self.subTest(key=key), self.assertRaises(DecisionError):
                self.workflow().dispatch("strategy-spec", request)
        state = self.state()
        state["leaseToken"] = "0" * 64
        self.put_state(state)
        with self.assertRaises(DecisionError):
            self.workflow().dispatch("strategy-spec", self.request)
        self.assertEqual(self.factory.calls, [])

    def test_policy_scope_and_nested_requirements_are_frozen_at_construction(self):
        scope = {"components": {"root"}, "operations": {"reposition"},
            "field_paths": {"transform.position"}, "authorized_sources": set(), "source_operations": set(),
            "available_dependencies": set(), "rig_required": False, "rig_available": False}
        requirements = {"criticalFeatures": [{"id": "detail", "threshold": 3}], "requiredViews": [0]}
        policy = WorkflowPolicy(scope=scope, requirements=requirements,
                                authorize=lambda *args: True, draft_spec="draft.json")
        scope["components"].add("foreign")
        requirements["criticalFeatures"][0]["threshold"] = 0
        self.assertEqual(policy.scope["components"], frozenset({"root"}))
        self.assertEqual(policy.requirements["criticalFeatures"][0]["threshold"], 3)
        with self.assertRaises(TypeError):
            policy.requirements["criticalFeatures"][0]["threshold"] = 0
        with self.assertRaises(TypeError):
            policy.scope["operations"] = frozenset({"disable-gate"})

    def test_native_dispatch_requires_bound_permission_records_and_live_native_authority(self):
        rule = {"fieldPath": "transform.position", "pointer": "/componentTree/0/transform/position",
                "ownerId": "case", "componentId": "root", "passId": "blockout"}
        native = NativePolicy(validate_spec=_validate_base,
            current_pass=lambda spec: sync_pipeline(spec)["currentPass"],
            authorize_native=lambda context, rules: self.authorized,
            rules=(rule,), frozen_paths=frozenset(), forbidden_components=frozenset())
        policy = replace(self.policy, native=native)
        request = deepcopy(self.request)
        inputs = {binding["role"]: binding for binding in request["bindings"]}
        provenance = {"kind": "img2.native-strategy-provenance", "version": 1,
            "subject": request["subject"], "specSha256": inputs["spec"]["sha256"],
            "sourceSha256": inputs["source"]["sha256"],
            "owners": [{key: rule[key] for key in ("pointer", "ownerId", "componentId")}]}
        provenance_binding = self.bind("provenance", "native-provenance.json", provenance)
        scope = dict(policy.scope, pass_id="blockout", pass_unlocked=True)
        permissions = {"kind": "img2.native-strategy-permissions", "version": 1,
            "subject": request["subject"], "provenanceSha256": provenance_binding["sha256"],
            "scopeDigest": canonical_digest({key: sorted(value) if isinstance(value, (set, frozenset)) else value
                                             for key, value in scope.items()}),
            "rules": [rule], "frozenPaths": [], "forbiddenComponents": []}
        request["bindings"].extend([provenance_binding, self.bind("permissions", "native-permissions.json", permissions)])
        workflow = self.workflow(policy=policy)
        result = workflow.dispatch("strategy-spec", request)
        self.assertEqual(result["action"], "selected")
        self.assertIn("nativeStrategyProposalDigests", self.factory.calls[0]["observations"])
        self.authorized = False
        with self.assertRaises(ValueError):
            workflow.dispatch("strategy-spec", dict(request, operation="begin", trialRevision=2))
        self.assertEqual(self.state()["decisionRefs"], [])

    def test_candidate_cannot_supply_policy_or_provenance_authority(self):
        for field in ("scope", "provenanceVerified", "permissions", "policy", "nativeRules"):
            request = dict(self.request, **{field: True})
            with self.subTest(field=field), self.assertRaises(DecisionError):
                self.workflow().dispatch("strategy-spec", request)
        self.authorized = False
        with self.assertRaises(DecisionError):
            self.workflow().dispatch("strategy-spec", self.request)
        self.assertEqual(self.factory.calls, [])

    def test_actual_spec_validator_and_proposal_delta_run_before_consult(self):
        request = deepcopy(self.request)
        request["proposals"]["shift"]["qualityContract"] = {}
        with self.assertRaises(Exception):
            self.workflow().dispatch("strategy-spec", request)
        self.request["bindings"][1] = self.bind("spec", "draft.json", {"componentTree": []})
        with self.assertRaises(Exception):
            self.workflow().dispatch("strategy-spec", self.request)
        self.assertEqual(self.factory.calls, [])

    def test_login_suspends_without_loop_or_state_completion(self):
        self.availability = "credential-missing"
        result = self.workflow().dispatch("strategy-spec", self.request)
        output = command_output(result)
        self.assertEqual(output["status"], "suspended")
        self.assertEqual(output["requestedCommand"], "img2-jev login")
        self.assertEqual(output["promptLimit"], 2)
        self.assertEqual(self.state()["decisionRefs"], [])
        self.assertEqual(self.factory.calls, [])
        self.availability = "present"
        self.assertEqual(self.workflow().dispatch("strategy-spec", self.request)["action"], "selected")

    def test_changed_input_or_revoked_authority_prevents_resume(self):
        workflow = self.workflow()
        workflow.dispatch("strategy-spec", self.request)
        self.authorized = False
        with self.assertRaises(DecisionError):
            workflow.dispatch("strategy-spec", dict(self.request, operation="begin", trialRevision=2))
        self.assertEqual(self.state()["decisionRefs"], [])
        self.authorized = True
        (self.workspace / self.child_root / "source.ts").write_bytes(b"outside change")
        with self.assertRaises(DecisionError):
            workflow.dispatch("strategy-spec", dict(self.request, operation="begin", trialRevision=2))

    def at_review_step(self, step):
        state = self.state()
        state["currentPass"] = "blockout"
        for row in state["checklist"]:
            if row["id"] == step:
                break
            row["status"] = "done"
        recompute(state)
        self.put_state(state)
        binding = self.bind("spec", "candidates/rev-1/spec.json", self.source)
        (self.workspace / self.child_root / "object-sculpt-spec.json").write_bytes(
            (self.workspace / binding["path"]).read_bytes())
        request = deepcopy(self.request)
        request["bindings"][1] = binding
        return request

    def test_quality_success_consumes_current_record_and_skips_only_unneeded_repair(self):
        from forge.tests.test_quality_decision import QualityDecisionTests
        quality = QualityDecisionTests()
        quality.setUp()
        self.addCleanup(quality.doCleanups)
        quality.subject = deepcopy(self.request["subject"])
        quality.transforms["spec"] = lambda unused: deepcopy(self.source)
        reference_bytes = (self.workspace / self.child_root / "reference.png").read_bytes()
        quality.transforms["reference"] = lambda unused: reference_bytes
        quality.refresh()
        self.at_review_step("quality-review")
        request = {"subject": deepcopy(quality.subject), "candidates": quality.context["candidates"],
                   "observations": quality.context["observations"], "bindings": []}
        for role, binding in quality.bindings.items():
            name = ("reference.png" if role == "reference" else
                    "candidates/rev-1/spec.json" if role == "spec" else "quality/" + role + ".fixture")
            raw = (quality.workspace / binding["path"]).read_bytes()
            request["bindings"].append(self.bind(role, name, raw))
            if role == "spec":
                (self.workspace / self.child_root / "object-sculpt-spec.json").write_bytes(raw)
        self.factory.provider = lambda context: quality.advisory
        policy = replace(self.policy, requirements=quality.requirements)
        result = self.workflow(policy=policy).dispatch("quality", request)
        self.assertEqual(result["action"], "continue")
        state = self.state()
        rows = {row["id"]: row for row in state["checklist"]}
        self.assertEqual(rows["quality-review"]["status"], "done")
        self.assertEqual(rows["repair-decision"]["status"], "skipped")
        self.assertEqual(state["decisionRefs"][-1]["record"]["group"], "quality-review")
        # A passing review cannot fabricate mandatory earlier strategy history.
        with self.controller.workspace_transaction(consent=lambda: deepcopy(self.grant)) as verify:
            with self.assertRaises(DecisionError):
                verify_lifecycle(verify, state, self.workspace / self.child_root, self.workspace)

    def test_quality_requires_matched_diagnostics_before_any_provider_call(self):
        request = self.at_review_step("quality-review")
        request.pop("proposals")
        result = self.workflow().dispatch("quality", request)
        self.assertEqual(result["action"], "request-input")
        self.assertEqual(result["reasonCode"], "evidence-missing")
        self.assertEqual(self.factory.calls, [])
        self.assertEqual(self.state()["currentStep"], "quality-review")
        self.assertEqual(self.state()["decisionRefs"], [])

    def repair_fixture(self):
        request = self.at_review_step("repair-decision")
        request.pop("proposals")
        request["bindings"].extend([
            self.bind("camera", "camera.json", {"synthetic": "camera"}),
            self.bind("view-set", "views.json", {"synthetic": "views"})])
        hashes = {binding["role"]: binding["sha256"] for binding in request["bindings"]}
        report = {"id": "synthetic-candidate", "outputKind": "threejs", "flatness": "volumetric",
            "gateResults": [{"id": "mesh", "status": "fail", "producer": "synthetic-mesh-fixture"}],
            "measurements": {"width": 2, "height": 1, "depth": 0.4, "frontArea": 2, "sideArea": 0.6,
                             "selfIntersectionCount": 4, "partCoveragePassed": True},
            "views": [0, 90, 180, 270],
            "features": [{"id": "detail", "tier": "critical", "status": "pass", "score": 3, "threshold": 3}],
            "metrics": {"silhouetteIoU": 0.9, "interiorDifference": 0.08, "orbitConsistency": 0.9},
            "evidence": {"referenceSha256": hashes["reference"], "cameraSha256": hashes["camera"],
                         "viewSetSha256": hashes["view-set"]}, "cost": 1, "timeMs": 10}
        failure = {"subject": request["subject"], "bindings": deepcopy(request["bindings"]),
                   "report": report, "faults": []}
        request["bindings"].append(self.bind("gate-report", "failed-gates.json", failure))
        request["failure"] = failure
        plan = deepcopy(request["candidates"][0])
        request["candidates"] = [{"id": plan["id"], "hypothesis": "geometry", "action": "refine-spec",
                                  "plan": plan, "permissions": ["write-spec"]}]
        criteria = {"targetMetric": "selfIntersectionCount", "direction": "min", "minImprovement": 1,
                    "tolerances": {"silhouetteIoU": 0, "interiorDifference": 0.01, "orbitConsistency": 0.01}}
        policy = replace(self.policy, permissions=frozenset({"write-spec"}), repair_criteria=criteria)
        return request, policy

    def test_repair_fresh_failure_and_authority_preflight_are_not_generic_consult(self):
        request, policy = self.repair_fixture()
        request["failure"]["subject"] = dict(request["subject"], revision=0)
        result = self.workflow(policy=policy).dispatch("repair", request)
        self.assertEqual(result["status"], "request-input")
        self.assertEqual(result["reasonCode"], "stale-evidence")
        self.assertEqual(self.factory.calls, [])
        self.assertEqual(self.state()["currentStep"], "repair-decision")

    def test_repair_application_is_candidate_only_and_rejection_preserves_incumbent(self):
        request, policy = self.repair_fixture()
        workflow = self.workflow(policy=policy)
        selected = workflow.dispatch("repair", request)
        self.assertEqual(selected["status"], "refine-spec")
        started = workflow.dispatch("repair", dict(request, operation="begin", trialRevision=2))
        self.assertEqual(started["application"]["status"], "pending")
        self.assertEqual(self.state()["loops"]["total"], 1)
        self.assertEqual(self.controller.correction_counts("case"), {"total": 1, "perPass": {"blockout": 1}})
        workflow.dispatch("repair", dict(request, operation="begin", trialRevision=2))
        self.assertEqual(self.state()["loops"]["total"], 1)
        self.assertEqual(self.state()["decisionLifecycle"]["incumbentRevision"], 1)
        before = (self.workspace / self.child_root / "object-sculpt-spec.json").read_bytes()
        after_subject = dict(request["subject"], revision=2)
        after_bindings = [deepcopy(binding) for binding in request["bindings"]
                          if binding["role"] not in {"spec", "source", "gate-report"}]
        after_bindings.extend([
            self.bind("spec", "candidates/rev-2/spec.json", self.source),
            self.bind("source", "candidates/rev-2/source.ts", b"synthetic trial source")])
        after = {"subject": after_subject, "bindings": deepcopy(after_bindings),
                 "report": deepcopy(request["failure"]["report"]), "faults": []}
        after_bindings.append(self.bind("gate-report", "candidates/rev-2/gates.json", after))
        result = workflow.dispatch("repair", dict(request, operation="finish", trialRevision=2,
            after=after, afterSubject=after_subject, afterBindings=after_bindings))
        self.assertFalse(result["retained"])
        self.assertEqual(result["application"]["status"], "rejected")
        self.assertEqual(self.state()["decisionLifecycle"]["candidateRevision"], 1)
        self.assertEqual(self.state()["decisionLifecycle"]["incumbentRevision"], 1)
        self.assertEqual(self.state()["status"], "stopped")
        self.assertEqual(self.state()["loops"]["total"], 1)
        self.assertEqual((self.workspace / self.child_root / "object-sculpt-spec.json").read_bytes(), before)

    def test_routed_lifecycle_deletion_cannot_bypass_real_journal_receipts(self):
        workflow = self.workflow()
        workflow.dispatch("strategy-spec", self.request)
        begin = dict(self.request, operation="begin", trialRevision=2)
        started = workflow.dispatch("strategy-spec", begin)
        output = self.bind("spec", "candidates/rev-2/spec.json", started["spec"])
        workflow.dispatch("strategy-spec", dict(begin, operation="finish", outputBinding=output))
        original = self.state()
        receipt = original["decisionRefs"][0]
        with self.controller.workspace_transaction(consent=lambda: deepcopy(self.grant)) as verify:
            self.assertEqual(verify(receipt["record"], receipt["context"])["status"], "answered")
            self.assertEqual(verify.application(receipt["application"]["applicationId"])["status"], "completed")
            for deleted in (("decisionLifecycle",), ("decisionRefs",), ("decisionLifecycle", "decisionRefs")):
                tampered = deepcopy(original)
                for key in deleted:
                    tampered.pop(key)
                for row in tampered["checklist"]:
                    row["status"] = "done"
                with self.subTest(deleted=deleted), self.assertRaises(DecisionError) as caught:
                    verify_lifecycle(verify, tampered, self.workspace / self.child_root, self.workspace)
                self.assertEqual(caught.exception.reason_code, "workflow-decisions-missing")

    def test_terminal_rejects_missing_receipts_and_legacy_is_not_migrated(self):
        verify_lifecycle(lambda *args: None, new_state("legacy.png"), self.workspace / self.child_root, self.workspace)
        with self.assertRaises(DecisionError):
            verify_lifecycle(lambda *args: None, self.state(), self.workspace / self.child_root, self.workspace)

    def test_policy_loader_rejects_workspace_provider_symlink_and_changed_pin(self):
        raw = b"def build_policy(workspace, state_path):\n    raise RuntimeError('must not execute')\n"
        path = self.workspace / "untrusted-policy.py"
        path.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        with self.assertRaises(DecisionError):
            load_policy(path, digest, workspace=self.workspace, state_path=self.state_path)
        with tempfile.TemporaryDirectory() as temporary:
            external = Path(temporary).resolve() / "host-policy.py"
            external.write_bytes(raw)
            with self.assertRaises(DecisionError):
                load_policy(external, "0" * 64, workspace=self.workspace, state_path=self.state_path)
            with self.assertRaises(DecisionError):
                load_policy(external, digest, workspace=self.workspace, state_path=self.state_path,
                            excluded_roots=[external.parent])
            linked = external.parent / "link.py"
            linked.symlink_to(external)
            with self.assertRaises(DecisionError):
                load_policy(linked, digest, workspace=self.workspace, state_path=self.state_path)


if __name__ == "__main__":
    unittest.main()
