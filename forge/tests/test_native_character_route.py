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
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "forge"), str(ROOT / "forge/_shared")]

from decision_control import DecisionController, DecisionError
from route_parent import OwnerProfile, ParentError, RouteParent, VerifiedAggregate
from workflow_decisions import NativePolicy, WorkflowDecisions, WorkflowPolicy
from workflow_state import mark_steps, new_state, recompute, validate_state
from forge.tests.test_route_parent import FixtureAuthority, canonical, sha
import forge.next as next_cli
import route_parent

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

    # -- security review regressions ---------------------------------------------------

    POLICY_SOURCE = '''from workflow_decisions import NativePolicy, WorkflowPolicy


def build_policy(workspace, state_path):
    return WorkflowPolicy(scope={"components": {"root"}, "operations": {"reposition"},
        "field_paths": {"transform.position"}, "authorized_sources": set(), "source_operations": set(),
        "available_dependencies": set(), "rig_required": False, "rig_available": False},
        requirements={"criticalFeatures": [], "requiredViews": []},
        authorize=lambda command, subject, bindings: True,
        draft_spec="draft.json", request_token_limit=128,
        native=NativePolicy(validate_spec=lambda spec: True, current_pass=lambda spec: "native-build",
            authorize_native=lambda context, rules: True, rules=({"fieldPath": "x"},),
            frozen_paths=frozenset(), forbidden_components=frozenset()))
'''

    def external_policy(self):
        temporary = tempfile.TemporaryDirectory(prefix="img2-native-policy-")
        self.addCleanup(temporary.cleanup)
        source = Path(temporary.name).resolve() / "policy.py"
        source.write_text(self.POLICY_SOURCE)
        return source, hashlib.sha256(source.read_bytes()).hexdigest()

    def next_cli(self, *extra, cwd=None):
        return subprocess.run([sys.executable, str(ROOT / "forge/next.py"), "--state",
                               str(self.workspace / self.state_path), *extra],
                              capture_output=True, text=True, cwd=cwd or self.workspace)

    def journal(self):
        return (self.workspace / ".img2threejs/route/journal.jsonl").read_bytes()

    def policy_args(self, source, digest):
        return ["--policy-module", str(source), "--policy-sha256", digest,
                "--excluded-root", str(ROOT)]

    def test_real_pinned_policy_cli_success_path_writes_only_the_resolved_pass(self):
        original = self.ready_child()
        source, digest = self.external_policy()
        journal = self.journal()
        result = self.next_cli(*self.policy_args(source, digest))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pass=native-build", result.stdout)
        updated = self.read(self.state_path)
        self.assertEqual(updated["currentPass"], "native-build")
        self.assertEqual(updated["loops"], original["loops"])
        self.assertEqual(self.journal(), journal)
        stale = self.next_cli(*self.policy_args(source, "0" * 64))
        self.assertEqual(stale.returncode, 2)

    def test_deleted_null_or_altered_workflow_marker_fails_closed_never_sculpt(self):
        self.ready_child()
        plan_path = ".img2threejs/objects/hero/rev-1/native-plan.json"
        # The adversarial plan a sculpt downgrade would have consumed.
        plan = self.read(plan_path)
        plan.update(buildPasses=[{"id": "native-build"}],
                    reviewHistory=[{"passId": "native-build", "action": "continue"}])
        self.put(plan_path, canonical(plan))
        pristine = self.read(self.state_path)
        child = self.workspace / ".img2threejs/objects/hero/rev-1"
        source, digest = self.external_policy()
        for name, change in (("deleted", lambda row: row.pop("workflow")),
                             ("null", lambda row: row.__setitem__("workflow", None)),
                             ("altered", lambda row: row.__setitem__("workflow", "native-character-v2"))):
            forged = deepcopy(pristine)
            change(forged["routeChild"])
            self.put(self.state_path, canonical(forged))
            before = self.snapshot()
            for extra, cwd in (((), self.workspace), ((), child),
                               (tuple(self.policy_args(source, digest)), child)):
                with self.subTest(marker=name, extra=bool(extra), cwd=cwd.name):
                    result = self.next_cli(*extra, cwd=cwd)
                    self.assertEqual(result.returncode, 2, result.stdout)
                    self.assertIn("native-selection-invalid", result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(self.snapshot(), before)
            with self.subTest(marker=name, path="dispatch"):
                with self.assertRaises(DecisionError):
                    self.workflow(self.policy()).resolve_native_pass()
                self.assertEqual(self.snapshot(), before)
        # A marker deleted from a state that never carried a native signal stays legacy: the
        # descriptor in the parent is the independent source for native-ness.
        parent = self.read(".img2threejs/state.json")
        parent["objects"][0].pop("workflow")
        self.put(".img2threejs/state.json", canonical(parent))
        self.put(self.state_path, canonical(pristine))
        self.assertEqual(self.next_cli().returncode, 2)

    def test_relative_excluded_root_is_rejected_like_an_absolute_one(self):
        from host_policy import pinned_policy_module
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            (home / "provider").mkdir()
            module = home / "provider/policy.py"
            module.write_text("x = 1\n")
            digest = hashlib.sha256(module.read_bytes()).hexdigest()
            workspace = home / "ws"
            workspace.mkdir()
            previous = Path.cwd()
            os.chdir(home)
            try:
                for root in (str(home / "provider"), "provider", "./provider", "provider/../provider"):
                    with self.subTest(root=root):
                        with self.assertRaises(DecisionError) as raised:
                            with pinned_policy_module(module, digest, workspace=workspace,
                                                      excluded_roots=[Path(root)]):
                                pass
                        self.assertEqual(raised.exception.reason_code, "policy-module-unsafe")
                with pinned_policy_module(module, digest, workspace=workspace, excluded_roots=[Path("other")]):
                    pass
                with self.assertRaises(DecisionError):
                    with pinned_policy_module("provider/policy.py", digest, workspace=workspace):
                        pass
            finally:
                os.chdir(previous)

    def test_binding_replaced_between_verification_and_publish_publishes_nothing(self):
        self.ready_child()
        plan_path = ".img2threejs/objects/hero/rev-1/native-plan.json"
        for attack in ("plan", "state"):
            with self.subTest(attack=attack):
                self.ready_child_state_done()
                before = self.snapshot()
                policy = self.policy(current="complete")
                approved = policy.authorize
                def swap(command, subject, bindings, approved=approved, attack=attack):
                    result = approved(command, subject, bindings)
                    if attack == "plan":
                        self.put(plan_path, canonical({"unapproved": True}))
                    else:
                        state = self.read(self.state_path)
                        state["loops"]["total"] = 7
                        self.put(self.state_path, canonical(state))
                    return result
                with self.assertRaises(DecisionError) as raised:
                    self.workflow(replace(policy, authorize=swap)).resolve_native_pass()
                self.assertEqual(raised.exception.reason_code, "child-state-changed")
                after = self.snapshot()
                changed = {key for key in after if after[key] != before.get(key)}
                self.assertEqual(changed, {plan_path if attack == "plan" else self.state_path})
                if attack == "plan":
                    self.assertNotEqual(self.read(self.state_path)["currentPass"], "complete")
                self.put(plan_path, before[plan_path])
                self.put(self.state_path, before[self.state_path])

    def test_rejection_before_policy_and_run_verification_leaves_every_byte_unchanged(self):
        self.ready_child()
        source, digest = self.external_policy()
        before, journal = self.snapshot(), self.journal()
        result = self.next_cli(*self.policy_args(source, "0" * 64))
        self.assertEqual(result.returncode, 2)
        self.assertEqual((self.snapshot(), self.journal()), (before, journal))
        # A run the journal has never started, whose lease and child agree on it: the
        # controller used to append run-started before the policy pin was checked.
        lease = self.read(".img2threejs/route/lease.json")
        lease["runId"] = "unseen-run"
        self.put(".img2threejs/route/lease.json", canonical(lease))
        child = self.read(self.state_path)
        child["routeChild"]["runId"] = "unseen-run"
        self.put(self.state_path, canonical(child))
        before = self.snapshot()
        for pin in ("0" * 64, digest):
            with self.subTest(pin=pin[:4]):
                result = self.next_cli(*self.policy_args(source, pin))
                self.assertEqual(result.returncode, 2)
                self.assertEqual((self.snapshot(), self.journal()), (before, journal))

    def test_skipped_setup_row_cannot_complete(self):
        self.ready_child()
        state = self.read(self.state_path)
        row = next(row for row in state["checklist"] if row["id"] == "native-publish")
        row.update(status="skipped", reason="operator waived", evidence=["claimed"])
        recompute(state)
        self.put(self.state_path, canonical(state))
        before = (self.workspace / self.state_path).read_bytes()
        with self.assertRaises(DecisionError) as raised:
            self.workflow(self.policy(current="complete")).resolve_native_pass()
        self.assertEqual(raised.exception.reason_code, "native-setup-incomplete")
        self.assertEqual(self.authorized_calls, [])
        self.assertEqual((self.workspace / self.state_path).read_bytes(), before)

    def test_populated_journal_correction_counts_survive_native_resolution(self):
        from decision_control import GROUPS
        self.ready_child()
        grant = {"granted": True, "purposes": list(GROUPS), "sha256": "b" * 64, "tokenBudget": 10000}
        crop = (self.workspace / ".img2threejs/objects/hero/rev-1/reference.png").read_bytes()
        kind, questions = GROUPS["repair"]
        context = {"kind": kind, "version": 1, "group": "repair",
                   "subject": {"runId": "native-run", "objectId": "hero", "revision": 1, "passId": "native-build"},
                   "bindings": [{"role": "reference", "path": ".img2threejs/objects/hero/rev-1/reference.png",
                                 "sha256": sha(crop)}],
                   "candidates": [{"id": "a"}, {"id": "b"}], "observations": {"partCount": 2},
                   "requirements": {"criticalFeatures": [], "requiredViews": []},
                   "policyVersion": "policy-v1", "questionSetVersion": questions, "requestedModel": "jev-1.14.0"}
        def advisory(ctx):
            return {"kind": "img2.decision-advisory", "version": 1, "group": ctx["group"],
                    "subject": deepcopy(ctx["subject"]), "model": "jev-1.14.0",
                    "contextDigest": __import__("decision_control").canonical_digest(ctx),
                    "answers": {"selection": {"type": "choice", "choice": "a",
                                              "probabilities": {"a": 0.9, "b": 0.1}, "confidence": 0.8},
                                "anyFits": {"type": "noul", "noul": 1}},
                    "usage": {"input_tokens": 10, "output_tokens": 2}}
        record = self.controller.consult(context, provider=advisory, fallback=None,
                                         availability=lambda: {"status": "present"},
                                         consent=lambda: deepcopy(grant), request_token_limit=128)
        self.controller.begin_application(record, 2, consent=lambda: deepcopy(grant))
        counts = self.controller.correction_counts("hero")
        self.assertEqual(counts, {"total": 1, "perPass": {"native-build": 1}})
        state = self.read(self.state_path)
        state["loops"].update(counts)
        self.put(self.state_path, canonical(state))
        journal = self.journal()
        self.workflow(self.policy()).resolve_native_pass()
        updated = self.read(self.state_path)
        self.assertEqual({key: updated["loops"][key] for key in counts}, counts)
        self.assertEqual(updated["loops"], state["loops"])
        self.assertEqual(self.journal(), journal)
        self.assertEqual(self.controller.correction_counts("hero"), counts)

    def test_native_completion_then_independent_terminal_rejection_without_evidence(self):
        self.ready_child()
        self.workflow(self.policy(current="complete")).resolve_native_pass()
        done = self.read(self.state_path)
        self.assertEqual(done["currentPass"], "complete")
        self.assertEqual(self.authorized_calls, [("native-complete", "complete")])
        before = self.snapshot()
        with self.assertRaises((ParentError, DecisionError)) as raised:
            self.parent.complete_child("hero")
        self.assertEqual(raised.exception.reason_code, "workflow-decisions-missing")
        self.assertEqual(self.snapshot(), before)

    # -- review round g2b-a3 ------------------------------------------------------------

    def backed_no_repair(self):
        """Child whose repair-decision was skipped on the strength of a REAL journal quality pass."""
        from forge.tests.test_quality_decision import QualityDecisionTests
        from stage4_review.quality_decision import review_quality
        self.ready_child()
        quality = QualityDecisionTests()
        quality.setUp()
        self.addCleanup(quality.doCleanups)
        quality.workspace = self.workspace
        quality.subject = {"runId": "native-run", "objectId": "hero", "revision": 1, "passId": "native-build"}
        quality.refresh()
        grant = deepcopy(quality.grant)
        result = review_quality(self.controller, quality.context, provider=quality.advisory, fallback=None,
                                availability=lambda: {"status": "present"}, consent=lambda: deepcopy(grant),
                                request_token_limit=128)
        self.assertEqual(result["action"], "continue")
        record = self.controller.consume(result["decision"], quality.context, consent=lambda: deepcopy(grant))
        state = self.read(self.state_path)
        state["decisionRefs"].append({"step": "quality-review", "context": quality.context,
                                      "record": record, "action": "continue"})
        for row in state["checklist"]:
            if row["id"] == "quality-review":
                row.update(status="done", evidence=[record["recordId"]], reason="")
            elif row["id"] == "repair-decision":
                row.update(status="skipped", evidence=[record["recordId"]], reason="quality-passed-no-repair")
        recompute(state)
        self.put(self.state_path, canonical(state))
        return state, record

    def test_skipped_repair_counts_only_with_a_verified_journal_decision(self):
        state, record = self.backed_no_repair()
        # Real, verified quality pass: the no-repair skip completes.
        self.workflow(self.policy(current="complete")).resolve_native_pass()
        self.assertEqual(self.read(self.state_path)["currentPass"], "complete")
        forgeries = {
            "arbitrary-evidence-no-refs": lambda s: (s["decisionRefs"].clear(),
                next(r for r in s["checklist"] if r["id"] == "repair-decision").update(evidence=["forged"])),
            "refs-removed-real-id": lambda s: s["decisionRefs"].clear(),
            "evidence-not-the-reviewed-record": lambda s: next(
                r for r in s["checklist"] if r["id"] == "repair-decision").update(evidence=["0" * 64]),
            "tampered-record": lambda s: s["decisionRefs"][0]["record"].update(model="forged-model"),
            "review-refined-not-continued": lambda s: s["decisionRefs"][0].update(action="refine"),
            "reason-rewritten": lambda s: next(
                r for r in s["checklist"] if r["id"] == "repair-decision").update(reason="operator waived"),
        }
        for name, forge in forgeries.items():
            with self.subTest(forgery=name):
                forged = deepcopy(state)
                forged["currentPass"] = ""
                forge(forged)
                self.put(self.state_path, canonical(forged))
                before = self.snapshot()
                with self.assertRaises(DecisionError) as raised:
                    self.workflow(self.policy(current="complete")).resolve_native_pass()
                self.assertEqual(raised.exception.reason_code, "native-setup-incomplete")
                self.assertEqual(self.snapshot(), before)
        # Skipped SETUP rows without that backing stay rejected (unchanged behaviour).
        for row_id in ("native-publish", "quality-review"):
            with self.subTest(skipped=row_id):
                waived = deepcopy(state)
                waived["currentPass"] = ""
                next(r for r in waived["checklist"] if r["id"] == row_id).update(
                    status="skipped", reason="quality-passed-no-repair", evidence=[record["recordId"]])
                self.put(self.state_path, canonical(waived))
                with self.assertRaises(DecisionError) as raised:
                    self.workflow(self.policy(current="complete")).resolve_native_pass()
                self.assertEqual(raised.exception.reason_code, "native-setup-incomplete")

    def assert_controller_lock_free(self):
        with (self.controller.control_dir / "lock").open("r+b") as stream:
            route_parent.fcntl.flock(stream, route_parent.fcntl.LOCK_EX | route_parent.fcntl.LOCK_NB)

    def run_native_lock_cli(self, source, pin, *, lock_timeout=None):
        args = [sys.executable, str(ROOT / "forge/next.py")]
        if lock_timeout is not None:
            script = (
                "import sys, runpy; "
                f"sys.path.insert(0, {str(ROOT / 'forge/_shared')!r}); "
                f"import route_parent; route_parent.STATE_LOCK_TIMEOUT = {lock_timeout!r}; "
                "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
            )
            args = [sys.executable, "-c", script, str(ROOT / "forge/next.py")]
        return subprocess.run(
            [*args, "--state", str(self.workspace / self.state_path), *self.policy_args(source, pin)],
            cwd=self.workspace, capture_output=True, text=True, timeout=3)

    @unittest.skipIf(route_parent.fcntl is None, "requires POSIX flock")
    def test_unsafe_state_locks_fail_closed_and_release_controller(self):
        self.ready_child()
        source, pin = self.external_policy()
        lock = self.workspace / (self.state_path + ".lock")
        other = self.workspace / "other-lock"
        other.write_bytes(b"unrelated file")
        controller_lock = self.controller.control_dir / "lock"
        for kind in ("controller-hardlink", "other-hardlink", "symlink", "fifo", "directory"):
            with self.subTest(kind=kind):
                if kind == "controller-hardlink":
                    lock.hardlink_to(controller_lock)
                elif kind == "other-hardlink":
                    lock.hardlink_to(other)
                elif kind == "symlink":
                    lock.symlink_to(other)
                elif kind == "fifo":
                    os.mkfifo(lock)
                else:
                    lock.mkdir()
                try:
                    before = self.snapshot()
                    result = self.run_native_lock_cli(source, pin)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("state-lock-unsafe", result.stdout + result.stderr)
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(other.read_bytes(), b"unrelated file")
                    self.assert_controller_lock_free()
                finally:
                    if kind == "directory":
                        lock.rmdir()
                    else:
                        lock.unlink()

    @unittest.skipIf(route_parent.fcntl is None, "requires POSIX flock")
    def test_state_lock_contention_times_out_without_publication(self):
        import select
        self.ready_child()
        source, pin = self.external_policy()
        lock = self.workspace / (self.state_path + ".lock")
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import fcntl, sys; f = open(sys.argv[1], 'w'); "
             "fcntl.flock(f, fcntl.LOCK_EX); print('locked', flush=True); sys.stdin.read(1)",
             str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True)
        try:
            self.assertTrue(select.select([holder.stdout], [], [], 5)[0], "holder did not start")
            self.assertEqual(holder.stdout.readline(), "locked\n")
            before = self.snapshot()
            started = time.monotonic()
            result = self.run_native_lock_cli(source, pin, lock_timeout=0.2)
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.2)
            self.assertLess(elapsed, 3)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("state-lock-timeout", result.stdout + result.stderr)
            self.assertEqual(self.snapshot(), before)
            self.assert_controller_lock_free()
            # Exercise context-manager cleanup, not just OS cleanup on CLI exit.
            with patch.object(route_parent, "STATE_LOCK_TIMEOUT", 0.05):
                with self.assertRaisesRegex(ParentError, "state-lock-timeout"):
                    self.workflow(self.policy()).resolve_native_pass()
            self.assertEqual(self.snapshot(), before)
            self.assert_controller_lock_free()
        finally:
            holder.communicate(input="x", timeout=5)
        # Once the contender releases, the same normal CLI resolution succeeds.
        result = self.run_native_lock_cli(source, pin)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(self.read(self.state_path)["currentPass"], "native-build")

    def test_state_lock_rejects_controller_identity_even_with_single_link_metadata(self):
        self.ready_child()
        lock = self.workspace / (self.state_path + ".lock")
        lock.hardlink_to(self.controller.control_dir / "lock")
        info = lock.stat()
        from types import SimpleNamespace
        single_link = SimpleNamespace(st_mode=info.st_mode, st_nlink=1,
                                      st_dev=info.st_dev, st_ino=info.st_ino)
        before = self.snapshot()
        with patch.object(route_parent.os, "fstat", return_value=single_link):
            with self.assertRaisesRegex(ParentError, "state-lock-unsafe"):
                with route_parent.state_lock(self.workspace, self.state_path):
                    self.fail("controller inode admitted")
        self.assertEqual(self.snapshot(), before)

    def test_state_lock_permissions_and_descriptors_on_success_and_failure(self):
        import errno
        import stat
        self.ready_child()
        lock = self.workspace / (self.state_path + ".lock")
        opened = []
        real_open = os.open
        def track_open(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            opened.append(fd)
            return fd
        with patch.object(route_parent.os, "open", side_effect=track_open):
            for _ in range(100):
                with route_parent.state_lock(self.workspace, self.state_path):
                    self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)
                with self.assertRaisesRegex(RuntimeError, "inside-lock"):
                    with route_parent.state_lock(self.workspace, self.state_path):
                        raise RuntimeError("inside-lock")
            lock.unlink()
            lock.hardlink_to(self.controller.control_dir / "lock")
            for _ in range(100):
                with self.assertRaisesRegex(ParentError, "state-lock-unsafe"):
                    with route_parent.state_lock(self.workspace, self.state_path):
                        self.fail("unsafe lock acquired")
        for fd in set(opened):
            with self.assertRaises(OSError) as raised:
                os.fstat(fd)
            self.assertEqual(raised.exception.errno, errno.EBADF)

    def test_concurrent_writer_holding_the_state_lock_is_serialised_not_lost(self):
        import threading
        from route_parent import state_lock
        self.ready_child()
        acquired, order, errors = threading.Event(), [], []
        started = threading.Event()
        def editor():
            try:
                started.wait(5)
                with state_lock(self.workspace, self.state_path):
                    acquired.set()
                    state = self.read(self.state_path)
                    order.append(("editor-saw", state["currentPass"]))
                    state["loops"]["total"] = 5
                    self.put(self.state_path, canonical(state))
            except Exception as error:  # pragma: no cover - reported below
                errors.append(repr(error))
        thread = threading.Thread(target=editor)
        thread.start()
        policy = self.policy(current="complete")
        approved = policy.authorize
        def authorize(command, subject, bindings):
            started.set()
            # The editor must be unable to enter while the critical section is open.
            order.append(("editor-entered-during-section", acquired.wait(0.5)))
            return approved(command, subject, bindings)
        self.workflow(replace(policy, authorize=authorize)).resolve_native_pass()
        thread.join(10)
        self.assertEqual(errors, [])
        self.assertIn(("editor-entered-during-section", False), order)
        self.assertEqual(order[-1], ("editor-saw", "complete"))
        final = self.read(self.state_path)
        self.assertEqual((final["currentPass"], final["loops"]["total"]), ("complete", 5))

    def test_editor_ignoring_the_lock_during_publication_is_never_overwritten(self):
        import threading
        import route_parent
        self.ready_child()
        for target in ("state", "plan"):
            with self.subTest(target=target):
                state = self.read(self.state_path)
                state.update(currentPass="")
                state["loops"]["total"] = 1
                self.put(self.state_path, canonical(state))
                plan = ".img2threejs/objects/hero/rev-1/native-plan.json"
                before_plan = (self.workspace / plan).read_bytes()
                at_fsync, edited = threading.Event(), threading.Event()
                def editor():
                    at_fsync.wait(5)
                    if target == "state":
                        changed = self.read(self.state_path)
                        changed["loops"]["total"] = 5
                        self.put(self.state_path, canonical(changed))
                    else:
                        self.put(plan, canonical({"unapproved": True}))
                    edited.set()
                thread = threading.Thread(target=editor)
                thread.start()
                real = route_parent.os.fsync
                def delayed(fd):
                    if not at_fsync.is_set():
                        at_fsync.set()
                        edited.wait(5)
                    return real(fd)
                with patch.object(route_parent.os, "fsync", side_effect=delayed):
                    with self.assertRaises(DecisionError) as raised:
                        self.workflow(self.policy(current="complete")).resolve_native_pass()
                thread.join(10)
                self.assertEqual(raised.exception.reason_code, "child-state-changed")
                after = self.read(self.state_path)
                self.assertNotEqual(after["currentPass"], "complete")
                self.assertEqual(after["loops"]["total"], 5 if target == "state" else 1)
                self.put(plan, before_plan)

    def test_standalone_sculpt_spec_named_native_plan_json_is_not_native(self):
        fixture = (ROOT / "forge/tests/fixtures/reference_target_conformance_spec.json").read_bytes()
        runs = {}
        for name in ("spec.json", "native-plan.json"):
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                (work / name).write_bytes(fixture)
                init = subprocess.run([sys.executable, str(ROOT / "forge/state.py"), "init", "--state", "state.json",
                                       "--reference", "ref.png", "--spec", name, "--profile", "generic"],
                                      capture_output=True, text=True, cwd=work)
                self.assertEqual(init.returncode, 0, init.stderr)
                with patch.dict(os.environ, {"IMG2_HOME": str(work / "empty-home")}):
                    result = subprocess.run([sys.executable, str(ROOT / "forge/next.py"), "--state", "state.json"],
                                            capture_output=True, text=True, cwd=work, env=dict(os.environ))
                state = json.loads((work / "state.json").read_text())
                runs[name] = (result.returncode, result.stdout, result.stderr, state["currentPass"],
                              state["loops"], [row["id"] for row in state["checklist"]])
        self.assertEqual(runs["native-plan.json"][:3], (0, runs["spec.json"][1], ""))
        self.assertEqual(runs["native-plan.json"][3:], runs["spec.json"][3:])

    @unittest.expectedFailure
    def test_known_limit_erasing_every_native_claim_downgrades_to_sculpt(self):
        """KNOWN, UNFIXED (review rv-g2b-a2, finding P1-1): native-ness is stored only in
        child state and the parent descriptor, both editable by whoever can write the
        workspace. Deleting BOTH claims leaves nothing to fail closed on, so next.py takes
        the sculpt path and rewrites currentPass/loops. Fixing it needs an authority outside
        the candidate-writable tree: an owner design decision, deliberately not made here.
        This asserts the DESIRED behaviour and is expected to fail until then."""
        self.ready_child()
        child = self.read(self.state_path)
        child["routeChild"].pop("workflow")
        child["artifacts"]["spec"] = "plan.json"
        self.put(self.state_path, canonical(child))
        parent = self.read(".img2threejs/state.json")
        parent["objects"][0].pop("workflow")
        self.put(".img2threejs/state.json", canonical(parent))
        root = self.workspace / ".img2threejs/objects/hero/rev-1"
        plan = json.loads((root / "native-plan.json").read_bytes())
        plan.update(buildPasses=[{"id": "native-build"}],
                    reviewHistory=[{"passId": "native-build", "action": "continue"}])
        (root / "native-plan.json").unlink()
        (root / "plan.json").write_bytes(canonical(plan))
        result = self.next_cli(cwd=root)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(self.read(self.state_path)["loops"]["total"], 1)

    # -- legacy ------------------------------------------------------------------------

    def test_legacy_next_state_bytes_stdout_and_exit_code_match_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "spec.json").write_bytes((ROOT / "forge/tests/fixtures/reference_target_conformance_spec.json").read_bytes())
            init = subprocess.run([sys.executable, str(ROOT / "forge/state.py"), "init", "--state", "state.json",
                                   "--reference", "ref.png", "--spec", "spec.json", "--profile", "generic"],
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
