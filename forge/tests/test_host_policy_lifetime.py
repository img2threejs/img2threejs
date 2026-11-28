"""Live host-module pin regressions at real workflow/parent mutation boundaries.

The existing fixtures use offline advisory/probe transports, but real controller
journals and consumer implementations. No terminal/visual acceptance is claimed.
Only the external module is pinned: its Python interpreter, imports, dependencies
and mutable callback configuration remain explicitly trusted host code.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

FORGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(FORGE.parent), str(FORGE), str(FORGE / "_shared"), str(FORGE / "tests")]
import test_route_authority as route_fixtures
import test_workflow_decisions as workflow_fixtures
from decision_control import DecisionError
from forge.decision import load_policy
from route import load_route_policies
from route_authority import RoutePolicies
from route_parent import RouteParent
from workflow_decisions import WorkflowPolicy


HOST_SOURCE = '''from pathlib import Path
from workflow_decisions import WorkflowPolicy
from route_authority import RoutePolicies


def approved_callback(*args):
    source = Path(__file__)
    directory = source.parent
    with (directory / "callback-calls.txt").open("a") as stream:
        stream.write("called\\n")
    if (directory / "change-during-callback").exists():
        source.write_bytes(source.read_bytes() + b"\\n# changed inside trusted callback\\n")
    with (directory / "callback-returns.txt").open("a") as stream:
        stream.write("True\\n")
    return True


def build_policy(workspace, state_path):
    return WorkflowPolicy(
        scope={"components": {"root"}, "operations": {"reposition"},
               "field_paths": {"transform.position"}, "authorized_sources": set(),
               "source_operations": set(), "available_dependencies": set(),
               "rig_required": False, "rig_available": False},
        requirements={"criticalFeatures": [], "requiredViews": []},
        authorize=approved_callback, draft_spec="draft.json", request_token_limit=128)


def build_route_policies(workspace, run_id):
    return RoutePolicies(revalidate=approved_callback)
'''


class HostPolicyLifetimeTests(unittest.TestCase):
    def fixture(self, case_type):
        # Composition avoids inheriting/discovering the fixtures' unrelated tests.
        case = case_type("runTest")
        self.addCleanup(case.doCleanups)
        case.setUp()
        return case

    def external_policy(self, case):
        temporary = tempfile.TemporaryDirectory(prefix="img2-host-policy-")
        self.addCleanup(temporary.cleanup)
        source = Path(temporary.name).resolve() / "approved-policy.py"
        # The success path must not accidentally exercise a forbidden-root refusal.
        roots = [case.workspace, FORGE.parent]
        roots.extend(getattr(case, name) for name in ("plugin", "runtime") if hasattr(case, name))
        for root in roots:
            self.assertFalse(source == root or root in source.parents)
        source.write_text(HOST_SOURCE)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        return source, digest

    @staticmethod
    def callback_log(source, name="callback-calls.txt"):
        path = source.parent / name
        return path.read_bytes() if path.exists() else b""

    @staticmethod
    def corrupt(source, mode):
        if mode == "mutate":
            source.write_bytes(source.read_bytes() + b"\n# host approval revoked\n")
            return "stale-evidence"
        if mode == "delete":
            source.unlink()
            return "unsafe-path"
        if mode == "symlink":
            replacement = source.with_name("identical-but-unapproved.py")
            replacement.write_bytes(source.read_bytes())
            source.unlink()
            source.symlink_to(replacement)
            return "unsafe-path"
        raise AssertionError(mode)

    @staticmethod
    def published_files(case):
        root = case.workspace / ".img2threejs"
        return {path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
                and (path == root / "state.json" or "objects" in path.relative_to(root).parts)}

    def selected_workflow(self):
        case = self.fixture(workflow_fixtures.WorkflowDecisionTests)
        source, digest = self.external_policy(case)
        policy = load_policy(source, digest, workspace=case.workspace, state_path=case.state_path,
                             excluded_roots=(FORGE.parent,))
        self.assertIs(type(policy), WorkflowPolicy)
        workflow = case.workflow(policy=policy)
        selected = workflow.dispatch("strategy-spec", case.request)
        self.assertEqual(selected["action"], "selected")
        with case.controller.workspace_transaction(consent=lambda: deepcopy(case.grant)) as verify:
            pending = case.state()["decisionPending"]
            self.assertEqual(verify(pending["record"], pending["context"])["status"], "answered")
        return case, workflow, source, digest

    def route_parent(self):
        case = self.fixture(route_fixtures.RouteAuthorityTests)
        source, digest = self.external_policy(case)
        policies = load_route_policies(source, digest, workspace=case.workspace,
                                       run_id=case.controller.run_id,
                                       excluded_roots=(case.plugin, case.runtime))
        self.assertIs(type(policies), RoutePolicies)
        authority = case.authority_for(
            pixi_quality_policies=policies.pixi_quality,
            pixi_capture_policies=policies.pixi_capture,
            cs2_terminal_policies=policies.cs2_terminal,
            native_terminal_policies=policies.native_terminal,
            policy_check=policies.revalidate)
        parent = RouteParent(case.controller, verify_aggregate=authority, consent=case.consent)
        return case, parent, authority, source, digest

    def test_unchanged_workflow_policy_completes_real_candidate_application(self):
        case, workflow, source, digest = self.selected_workflow()
        draft = (case.workspace / case.child_root / "draft.json").read_bytes()
        begin = dict(case.request, operation="begin", trialRevision=2)
        started = workflow.dispatch("strategy-spec", begin)
        self.assertEqual(started["application"]["status"], "pending")
        output = case.bind("spec", "candidates/rev-2/spec.json", started["spec"])
        finished = workflow.dispatch("strategy-spec", dict(begin, operation="finish", outputBinding=output))
        self.assertEqual(finished["application"]["status"], "completed")
        state = case.state()
        self.assertEqual(state["currentStep"], "spec-authoring")
        self.assertEqual(state["decisionLifecycle"]["candidateRevision"], 2)
        self.assertEqual(state["decisionLifecycle"]["incumbentRevision"], 1)
        self.assertEqual((case.workspace / case.child_root / "draft.json").read_bytes(), draft)
        self.assertFalse((case.workspace / case.child_root / "object-sculpt-spec.json").exists())
        with case.controller.workspace_transaction(consent=lambda: deepcopy(case.grant)) as verify:
            receipt = state["decisionRefs"][0]
            self.assertEqual(verify(receipt["record"], receipt["context"])["status"], "answered")
            self.assertEqual(verify.application(receipt["application"]["applicationId"])["status"], "completed")
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
        self.assertTrue(self.callback_log(source))

    def test_changed_loaded_workflow_policy_blocks_application_without_touching_incumbent(self):
        for mode in ("mutate", "delete", "symlink"):
            with self.subTest(mode=mode):
                case, workflow, source, _ = self.selected_workflow()
                before = self.published_files(case)
                journal = case.controller.journal_path.read_bytes()
                calls = self.callback_log(source)
                provider_calls = len(case.factory.calls)
                reason = self.corrupt(source, mode)
                with self.assertRaises(DecisionError) as caught:
                    workflow.dispatch("strategy-spec", dict(case.request, operation="begin", trialRevision=2))
                self.assertEqual(caught.exception.reason_code, reason)
                self.assertEqual(self.published_files(case), before)
                self.assertEqual(case.controller.journal_path.read_bytes(), journal)
                self.assertEqual(self.callback_log(source), calls)
                self.assertEqual(len(case.factory.calls), provider_calls)
                self.assertEqual(case.state()["decisionLifecycle"]["incumbentRevision"], 1)
                self.assertFalse((case.workspace / case.child_root / "candidates/rev-2").exists())

    def test_workflow_callback_returning_true_cannot_change_its_own_pin(self):
        case, workflow, source, digest = self.selected_workflow()
        before = self.published_files(case)
        journal = case.controller.journal_path.read_bytes()
        calls = self.callback_log(source)
        returns = self.callback_log(source, "callback-returns.txt")
        provider_calls = len(case.factory.calls)
        (source.parent / "change-during-callback").touch()
        with self.assertRaises(DecisionError) as caught:
            workflow.dispatch("strategy-spec", dict(case.request, operation="begin", trialRevision=2))
        self.assertEqual(caught.exception.reason_code, "stale-evidence")
        self.assertEqual(self.callback_log(source), calls + b"called\n")
        self.assertEqual(self.callback_log(source, "callback-returns.txt"), returns + b"True\n")
        self.assertNotEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
        self.assertEqual(self.published_files(case), before)
        self.assertEqual(case.controller.journal_path.read_bytes(), journal)
        self.assertEqual(len(case.factory.calls), provider_calls)
        self.assertFalse((case.workspace / case.child_root / "candidates/rev-2").exists())

    def test_unchanged_route_policy_creates_pending_children_and_revalidates(self):
        case, parent, authority, source, digest = self.route_parent()
        created = parent.create()
        self.assertEqual(len(created["objects"]), 1)
        self.assertNotEqual(created["status"], "complete")
        descriptor = created["objects"][0]
        child = json.loads((case.workspace / descriptor["statePath"]).read_bytes())
        self.assertTrue(any(step["status"] == "pending" for step in child["checklist"]))
        self.assertFalse((case.workspace / descriptor["artifactPath"]).exists())
        with case.controller.workspace_transaction(consent=case.consent) as verify:
            receipt = authority(verify)
            authority.revalidate(verify, decision=receipt.decision, inventory=receipt.inventory,
                                 owners=receipt.owners)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
        self.assertTrue(self.callback_log(source))
        self.assertEqual(case.gate_commands, [])

    def test_changed_loaded_route_policy_stops_parent_create_and_completion(self):
        for phase in ("create", "complete"):
            for mode in ("mutate", "delete", "symlink"):
                with self.subTest(phase=phase, mode=mode):
                    case, parent, _, source, _ = self.route_parent()
                    if phase == "complete":
                        created = parent.create()
                        object_id = created["objects"][0]["objectId"]
                    before = self.published_files(case)
                    calls = self.callback_log(source)
                    provider_calls = case.provider.call_count
                    reason = self.corrupt(source, mode)
                    with self.assertRaises(DecisionError) as caught:
                        if phase == "create":
                            parent.create()
                        else:
                            parent.complete_child(object_id)
                    self.assertEqual(caught.exception.reason_code, reason)
                    self.assertEqual(self.published_files(case), before)
                    self.assertEqual(self.callback_log(source), calls)
                    self.assertEqual(case.provider.call_count, provider_calls)
                    self.assertEqual(case.gate_commands, [])
                    if phase == "create":
                        self.assertFalse((case.workspace / ".img2threejs/state.json").exists())
                        self.assertFalse((case.workspace / ".img2threejs/objects").exists())

    def test_route_callback_returning_true_cannot_change_its_own_pin(self):
        for phase in ("create", "complete"):
            with self.subTest(phase=phase):
                case, parent, _, source, digest = self.route_parent()
                if phase == "complete":
                    created = parent.create()
                    object_id = created["objects"][0]["objectId"]
                before = self.published_files(case)
                calls = self.callback_log(source)
                returns = self.callback_log(source, "callback-returns.txt")
                (source.parent / "change-during-callback").touch()
                with self.assertRaises(DecisionError) as caught:
                    if phase == "create":
                        parent.create()
                    else:
                        parent.complete_child(object_id)
                self.assertEqual(caught.exception.reason_code, "stale-evidence")
                self.assertEqual(self.callback_log(source), calls + b"called\n")
                self.assertEqual(self.callback_log(source, "callback-returns.txt"), returns + b"True\n")
                self.assertNotEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
                self.assertEqual(self.published_files(case), before)
                self.assertEqual(case.gate_commands, [])
                if phase == "create":
                    self.assertFalse((case.workspace / ".img2threejs/state.json").exists())
                    self.assertFalse((case.workspace / ".img2threejs/objects").exists())


if __name__ == "__main__":
    unittest.main()
