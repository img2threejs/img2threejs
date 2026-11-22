"""Native-character-v1 routing seam. Doubles only: no provider, network or browser.

FixtureAuthority/NativePolicy callbacks here are test-owned stand-ins for the host's
authenticated aggregate approval and pinned plugin validator; they prove the base
fails closed around them, not that any production authority exists.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "forge"), str(ROOT / "forge/_shared")]

from decision_control import DecisionController, DecisionError
from route_parent import OwnerProfile, ParentError, RouteParent, VerifiedAggregate
from workflow_decisions import NativePolicy, WorkflowDecisions, WorkflowPolicy
from workflow_state import mark_steps, new_state, validate_state
from forge.tests.test_route_parent import FixtureAuthority, canonical, sha
import forge.next as next_cli

WORKFLOW = "native-character-v1"
ROUTE = "character:native"
LEGACY_STATE_SHA = "69a678b7a5e1dc112a2d348a3cd6d45eea411f919e79877c1bd8d1bfb405c441"
LEGACY_STDOUT_SHA = "d8da0badcc407e7379f02a5e6e6437aa091031a3d1e1a13f61aecea1a2f22cd2"


class NativeAuthority(FixtureAuthority):
    def __init__(self, case, decision, inventory, owners):
        super().__init__(case, decision, inventory, owners)
        self.native_revoked = False
        self.selections = []

    def approve_native_character(self, verify, *, decision, inventory, owners, selection):
        self.selections.append(selection)
        if self.native_revoked or selection != self.case.expected_selection:
            raise ParentError("fixture-native-approval-rejected")


class NativeRouteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        home = self.workspace / "home"
        (home / "plugins/character").mkdir(parents=True)
        (home / "plugins/character/domain.json").write_bytes(canonical({"id": "animated-character"}))
        (home / "plugins.json").write_bytes(canonical({"version": 1, "plugins": [{"id": "character"}]}))
        environment = patch.dict(os.environ, {"IMG2_HOME": str(home)})
        environment.start()
        self.addCleanup(environment.stop)
        self.controller = DecisionController(self.workspace, "native-run", clock=lambda: 1000)
        self.configure(workflow=WORKFLOW)

    def put(self, relative, data):
        path = self.workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read(self, relative):
        return json.loads((self.workspace / relative).read_bytes())

    def configure(self, *, workflow, profile="animated-character", kind="rigged-3d"):
        data = b"native-crop"
        self.put("crops/hero.png", data)
        binding = {"role": "reference-crop", "path": "crops/hero.png", "sha256": sha(data)}
        inventory = {"kind": "img2.object-inventory", "version": 1, "imageSha256": "a" * 64,
                     "objects": [{"objectId": "hero", "reference": binding, "ownerRoute": ROUTE}]}
        decision = {"kind": "img2.route-decision", "version": 1, "phase": "decided",
                    "runId": self.controller.run_id, "revision": 1, "executionAuthorized": False,
                    "imageSha256": "a" * 64, "inventorySha256": sha(canonical(inventory)),
                    "objects": [{"objectId": "hero", "reference": binding, "revision": 1,
                                 "status": "resolved", "selected": [ROUTE]}],
                    "request": {"expectedOutput": "3d"}, "skipComposition": False}
        owners = {ROUTE: OwnerProfile(profile, kind, workflow)}
        self.expected_selection = {"workflow": WORKFLOW, "profile": "animated-character",
                                   "outputKind": "rigged-3d", "ownerRoute": ROUTE, "objectId": "hero",
                                   "revision": 1, "cropSha256": sha(data)}
        self.receipt = NativeAuthority(self, decision, inventory, owners)
        self.result = VerifiedAggregate(decision, inventory, owners, self.receipt)
        self.parent = RouteParent(self.controller, verify_aggregate=lambda verify: self.result,
                                  consent=lambda: None)
        self.state_path = ".img2threejs/objects/hero/rev-1/state.json"

    def snapshot(self):
        return {str(path.relative_to(self.workspace)): path.read_bytes()
                for path in sorted(self.workspace.rglob("*"))
                if path.is_file() and "home" not in path.relative_to(self.workspace).parts[:1]
                and path.relative_to(self.workspace).parts[0] == ".img2threejs"
                and not path.name.endswith(".lock")}

    def reject(self, reason, operation):
        with self.assertRaises(ParentError) as raised:
            operation()
        self.assertEqual(raised.exception.reason_code, reason)

    # -- routing -----------------------------------------------------------------

    def test_native_child_gets_native_steps_and_plan_not_sculpt_spec(self):
        self.parent.create()
        self.assertEqual(self.receipt.selections, [self.expected_selection])
        state = self.read(self.state_path)
        ids = [row["id"] for row in state["checklist"]]
        self.assertEqual(ids, ["strategy-spec", "native-plan-author", "strategy-build", "native-build",
                               "native-capture-deliver", "quality-review", "repair-decision",
                               "native-publish", "native-terminal-gate"])
        self.assertFalse({"spec-authoring", "build-current-pass", "pass-gate-check"} & set(ids))
        self.assertEqual(state["artifacts"]["spec"], "native-plan.json")
        self.assertEqual(state["routeChild"]["workflow"], WORKFLOW)
        self.assertEqual(state["currentPass"], "")
        self.assertEqual(state["loops"]["total"], 0)
        plan = self.read(".img2threejs/objects/hero/rev-1/native-plan.json")
        self.assertNotIn("buildPasses", plan)
        self.assertEqual(plan["progress"], {"nativeBuild": "pending"})
        self.assertEqual(self.read(".img2threejs/state.json")["objects"][0]["workflow"], WORKFLOW)

    def test_character_without_selector_stays_sculpt_even_with_native_authority(self):
        self.configure(workflow=None)
        self.parent.create()
        self.assertEqual(self.receipt.selections, [])
        state = self.read(self.state_path)
        self.assertEqual(state["artifacts"]["spec"], "object-sculpt-spec.json")
        self.assertNotIn("workflow", state["routeChild"])
        self.assertIn("spec-authoring", [row["id"] for row in state["checklist"]])
        self.assertFalse((self.workspace / ".img2threejs/objects/hero/rev-1/native-plan.json").exists())

    def test_forged_revoked_or_unsupported_native_selection_rejected_with_no_writes(self):
        before = self.snapshot()
        self.receipt.native_revoked = True
        self.reject("fixture-native-approval-rejected", self.parent.create)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.workspace / ".img2threejs/state.json").exists())
        for profile, kind in (("generic", "rigged-3d"), ("animated-character", "3d-object")):
            self.configure(workflow=WORKFLOW, profile=profile, kind=kind)
            self.reject("native-selection-invalid", self.parent.create)
            self.assertEqual(self.receipt.selections, [])
            self.assertFalse((self.workspace / ".img2threejs/state.json").exists())
        self.configure(workflow="native-character-v2")
        self.reject("native-selection-invalid", self.parent.create)
        self.assertEqual(self.snapshot(), before)

    def test_authority_that_predates_the_contract_cannot_authorize_native(self):
        self.receipt = FixtureAuthority(self, *self.receipt.expected)
        self.result = VerifiedAggregate(*self.receipt.expected, self.receipt)
        self.reject("native-selection-unapproved", self.parent.create)
        self.assertFalse((self.workspace / ".img2threejs/state.json").exists())

    def test_native_claim_is_reauthenticated_on_resume(self):
        self.parent.create()
        self.receipt.native_revoked = True
        before = self.snapshot()
        self.reject("fixture-native-approval-rejected", lambda: self.parent.complete_child("hero"))
        self.assertEqual(self.snapshot(), before)

    def test_native_completion_without_authenticated_evidence_is_rejected(self):
        self.parent.create()
        state = self.read(self.state_path)
        state.update(status="complete", currentStep="complete", currentPass="complete")
        for row in state["checklist"]:
            row.update(status="done", evidence=["arbitrary-checklist-text"])
        self.put(self.state_path, canonical(state))
        before = self.snapshot()
        with self.assertRaises((ParentError, DecisionError)) as raised:
            self.parent.complete_child("hero")
        self.assertEqual(raised.exception.reason_code, "workflow-decisions-missing")
        self.assertEqual(self.snapshot(), before)

    # -- next.py / pass resolution ----------------------------------------------------

    def policy(self, *, current="native-build", authorized=True, valid=True, calls=None):
        calls = calls if calls is not None else []
        def validate(spec):
            calls.append("validate")
            return valid
        def resolve(spec):
            calls.append("pass")
            return current
        authorized_calls = []
        def authorize(command, subject, bindings):
            authorized_calls.append((command, subject["passId"]))
            calls.append("authorize")
            return authorized
        policy = WorkflowPolicy(scope={"components": {"root"}, "operations": {"reposition"},
            "field_paths": {"transform.position"}, "authorized_sources": set(), "source_operations": set(),
            "available_dependencies": set(), "rig_required": False, "rig_available": False},
            requirements={"criticalFeatures": [], "requiredViews": []}, authorize=authorize,
            draft_spec="draft.json", request_token_limit=128,
            native=NativePolicy(validate_spec=validate, current_pass=resolve,
                authorize_native=lambda context, rules: True, rules=({"fieldPath": "x"},),
                frozen_paths=frozenset(), forbidden_components=frozenset()))
        self.authorized_calls = authorized_calls
        return policy

    def ready_child(self, *, setup_done=True):
        self.parent.create()
        state = self.read(self.state_path)
        plan = {"schemaVersion": "img2.character-native-plan/v1", "ownerId": "hero",
                "progress": {"nativeBuild": "pending"}}
        self.put(".img2threejs/objects/hero/rev-1/native-plan.json", canonical(plan))
        state["loops"].update(perPass={"native-build": 1}, total=1)
        if setup_done:
            for row in state["checklist"]:
                if row["scope"] == "setup":
                    row.update(status="done", evidence=["fixture setup row, not decision authority"])
        from workflow_state import recompute
        recompute(state)
        self.put(self.state_path, canonical(state))
        return state

    def workflow(self, policy):
        return WorkflowDecisions(self.controller, self.state_path, policy, consent=lambda: None,
                                 availability=lambda: {"status": "present"})

    def test_native_next_resolves_through_host_callbacks_and_keeps_budgets(self):
        original = self.ready_child()
        calls = []
        with patch.object(next_cli, "sync_from_spec", side_effect=AssertionError("sculpt sync")), \
                patch("workflow_state.sync_from_spec", side_effect=AssertionError("sculpt sync")), \
                patch("workflow_state.set_current_pass", side_effect=AssertionError("sculpt reset")):
            resolved = self.workflow(self.policy(calls=calls)).resolve_native_pass()
        self.assertEqual(calls, ["validate", "pass", "authorize"])
        updated = self.read(self.state_path)
        self.assertEqual(updated, resolved)
        self.assertEqual(updated["currentPass"], "native-build")
        self.assertEqual(updated["loops"], original["loops"])
        self.assertEqual(updated["passHistory"], original["passHistory"])
        self.assertEqual(updated["decisionRefs"], [])
        changed = {key for key in updated if updated[key] != original.get(key)}
        self.assertEqual(changed, {"currentPass"} | ({"currentStep", "status"} & changed))
        # A second resolution is idempotent and never rewrites the file.
        before = (self.workspace / self.state_path).read_bytes()
        self.workflow(self.policy()).resolve_native_pass()
        self.assertEqual((self.workspace / self.state_path).read_bytes(), before)

    def test_native_next_fails_closed_without_policy_authorization_or_valid_plan(self):
        self.ready_child()
        before = (self.workspace / self.state_path).read_bytes()
        for policy, reason in ((self.policy(authorized=False), "workflow-evidence-not-authorized"),
                               (self.policy(valid=False), "spec-validation-failed"),
                               (self.policy(current=""), "locked-or-wrong-pass")):
            with self.assertRaises(DecisionError) as raised:
                self.workflow(policy).resolve_native_pass()
            self.assertEqual(raised.exception.reason_code, reason)
        no_native = self.policy()
        from dataclasses import replace
        with self.assertRaises(DecisionError) as raised:
            self.workflow(replace(no_native, native=None)).resolve_native_pass()
        self.assertEqual(raised.exception.reason_code, "native-authority-unavailable")
        self.assertEqual((self.workspace / self.state_path).read_bytes(), before)

    def test_complete_requires_live_authorization_and_every_setup_row(self):
        self.ready_child(setup_done=False)
        before = (self.workspace / self.state_path).read_bytes()
        with self.assertRaises(DecisionError) as raised:
            self.workflow(self.policy(current="complete")).resolve_native_pass()
        self.assertEqual(raised.exception.reason_code, "native-setup-incomplete")
        self.ready_child_state_done()
        before = (self.workspace / self.state_path).read_bytes()
        denied = self.policy(current="complete", authorized=False)
        with self.assertRaises(DecisionError) as raised:
            self.workflow(denied).resolve_native_pass()
        self.assertEqual(raised.exception.reason_code, "workflow-evidence-not-authorized")
        self.assertEqual(self.authorized_calls, [("native-complete", "complete")])
        self.assertEqual((self.workspace / self.state_path).read_bytes(), before)
        granted = self.policy(current="complete")
        self.workflow(granted).resolve_native_pass()
        self.assertEqual(self.read(self.state_path)["currentPass"], "complete")

    def ready_child_state_done(self):
        state = self.read(self.state_path)
        for row in state["checklist"]:
            if row["scope"] == "setup":
                row.update(status="done", evidence=["fixture setup row, not decision authority"])
        from workflow_state import recompute
        recompute(state)
        self.put(self.state_path, canonical(state))

    def test_completed_pass_cannot_be_unlocked_or_reused_and_ordinary_decisions_reject_it(self):
        self.ready_child()
        self.workflow(self.policy(current="complete")).resolve_native_pass()
        with self.assertRaises(DecisionError) as raised:
            self.workflow(self.policy(current="native-build")).resolve_native_pass()
        self.assertEqual(raised.exception.reason_code, "locked-or-wrong-pass")
        factory_calls = []
        workflow = self.workflow(self.policy(current="complete"))
        workflow.provider_factory = type("F", (), {"provider": lambda s, c: factory_calls.append(c)})()
        request = {"subject": {"runId": "native-run", "objectId": "hero", "revision": 1, "passId": "complete"},
                   "bindings": [], "candidates": [], "observations": {}, "proposals": {}}
        with self.assertRaises(DecisionError) as raised:
            workflow.dispatch("strategy-build", request)
        self.assertIn(raised.exception.reason_code, {"evidence-mismatch", "locked-or-wrong-pass"})
        self.assertEqual(factory_calls, [])

    def test_stale_or_locked_pass_rejected_before_provider(self):
        self.ready_child()
        state = self.read(self.state_path)
        state["currentPass"] = "native-build"
        self.put(self.state_path, canonical(state))
        root = ".img2threejs/objects/hero/rev-1/"
        draft = canonical(self.read(root + "native-plan.json"))
        self.put(root + "draft.json", draft)
        crop = (self.workspace / root / "reference.png").read_bytes()
        bindings = [{"role": "reference", "path": root + "reference.png", "sha256": sha(crop)},
                    {"role": "spec", "path": root + "draft.json", "sha256": sha(draft)}]
        for current in ("other-pass", "complete"):
            factory_calls = []
            workflow = self.workflow(self.policy(current=current))
            workflow.provider_factory = type("F", (), {"provider": lambda s, c: factory_calls.append(c)})()
            request = {"subject": {"runId": "native-run", "objectId": "hero", "revision": 1,
                                   "passId": "native-build"},
                       "bindings": bindings, "candidates": [], "observations": {}, "proposals": {}}
            with self.assertRaises(DecisionError) as raised:
                workflow.dispatch("strategy-spec", request)
            self.assertEqual(raised.exception.reason_code, "locked-or-wrong-pass")
            self.assertEqual(factory_calls, [])

    def test_next_cli_native_state_is_policy_gated_and_never_synchronizes(self):
        state = self.ready_child(setup_done=False)
        path = self.workspace / self.state_path
        before = path.read_bytes()
        result = subprocess.run([sys.executable, str(ROOT / "forge/next.py"), "--state", str(path)],
                                capture_output=True, text=True, cwd=self.workspace)
        # Pre-plan rows pending: report only, no write, no spec synchronization.
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(path.read_bytes(), before)
        self.ready_child_state_done()
        before = path.read_bytes()
        result = subprocess.run([sys.executable, str(ROOT / "forge/next.py"), "--state", str(path)],
                                capture_output=True, text=True, cwd=self.workspace)
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertIn("native-authority-unavailable", result.stderr)
        self.assertEqual(path.read_bytes(), before)
        result = subprocess.run([sys.executable, str(ROOT / "forge/next.py"), "--state", str(path),
                                 str(self.workspace / "x.json")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(path.read_bytes(), before)
        forged = self.read(self.state_path)
        forged["routeChild"]["workflow"] = "native-character-v9"
        self.put(self.state_path, canonical(forged))
        result = subprocess.run([sys.executable, str(ROOT / "forge/next.py"), "--state", str(path)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("native-selection-invalid", result.stderr)

    # -- legacy ------------------------------------------------------------------------

    def test_legacy_next_state_bytes_stdout_and_exit_code_match_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "spec.json").write_bytes((ROOT / "forge/tests/fixtures/reference_target_conformance_spec.json").read_bytes())
            init = subprocess.run([sys.executable, str(ROOT / "forge/state.py"), "init", "--state", "state.json",
                                   "--reference", "ref.png", "--spec", "spec.json"],
                                  capture_output=True, text=True, cwd=work)
            self.assertEqual(init.returncode, 0, init.stderr)
            with patch.dict(os.environ, {"IMG2_HOME": str(work / "empty-home")}):
                result = subprocess.run([sys.executable, str(ROOT / "forge/next.py"), "--state", "state.json"],
                                        capture_output=True, cwd=work, env=dict(os.environ))
            self.assertEqual((result.returncode, result.stderr), (0, b""))
            self.assertEqual(hashlib.sha256(result.stdout).hexdigest(), LEGACY_STDOUT_SHA)
            self.assertEqual(hashlib.sha256((work / "state.json").read_bytes()).hexdigest(), LEGACY_STATE_SHA)


if __name__ == "__main__":
    unittest.main()
