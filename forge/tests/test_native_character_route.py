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
