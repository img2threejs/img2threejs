"""Offline CLI protocols with the real controller journal and route aggregate.

The existing tiny route/provider fixture is NOT native capture or scene quality
evidence. Tests that reach publication stub only the native capture transport;
the independent terminal completion remains unavailable, never mocked as passed.
No live provider requests are made.
"""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

FORGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(FORGE), str(FORGE / "_shared"), str(FORGE / "tests")]
import terminal
import route
import test_route_authority as route_fixtures
from decision_control import DecisionError, canonical_digest
from route_parent import ParentError, RouteParent, _encode, _read


class TerminalReviewTests(unittest.TestCase):
    def setUp(self):
        self.case = route_fixtures.RouteAuthorityTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.fixture, self.authority = self.case.quality_fixture()
        self.child = self.fixture[1]
        self.workspace = self.case.workspace
        self.controller = self.case.controller
        self.parent = RouteParent(self.controller, verify_aggregate=self.authority, consent=self.case.consent)
        self.parent_bytes = (self.workspace / ".img2threejs/state.json").read_bytes()
        self.child_bytes = (self.child / "state.json").read_bytes()
        self.in_transaction = False
        real_transaction = self.controller.workspace_transaction

        @contextmanager
        def transaction(**kwargs):
            with real_transaction(**kwargs) as verify:
                self.assertFalse(self.in_transaction)
                self.in_transaction = True
                try:
                    yield verify
                finally:
                    self.in_transaction = False

        self.transaction_patch = patch.object(self.controller, "workspace_transaction", transaction)
        self.transaction_patch.start()
        self.addCleanup(self.transaction_patch.stop)
        self.factory_contexts = []
        self.projected_contexts = []
        self.after_answer = None
        self.critical_choice = "pass"
        self.factory = SimpleNamespace(provider=self.provider)
        self.availability = Mock(return_value={"status": "present"})
        self.fallback = Mock(side_effect=AssertionError("fallback must not run"))
        self.reviews = terminal.TerminalReviews(self.parent, provider_factory=self.factory,
            availability=self.availability, fallback=self.fallback)
        self.link = self.child / (route_fixtures.AREA + "quality-review-link.json")

    def provider(self, context):
        self.assertFalse(self.in_transaction, "factory preparation must be outside the parent transaction")
        self.factory_contexts.append(deepcopy(context))

        def answer(projected):
            self.assertFalse(self.in_transaction, "provider must be outside the parent transaction")
            self.projected_contexts.append(deepcopy(projected))
            result = self.case.quality_answer(projected, critical_choice=self.critical_choice)
            if self.after_answer is not None:
                self.after_answer()
            return result

        return answer

    def capture_transport(self):
        # Only transport is synthetic. The real aggregate, context projection,
        # journal, consume, held verifier, path writer and completion stay real.
        return patch.object(self.authority._pixi_capture_authority, "verify_capture", return_value=None)

    def assert_uncompleted(self):
        self.assertEqual((self.workspace / ".img2threejs/state.json").read_bytes(), self.parent_bytes)
        self.assertEqual((self.child / "state.json").read_bytes(), self.child_bytes)

    def assert_rejected(self, callback, *args):
        with self.assertRaises((DecisionError, ParentError, OSError)):
            callback(*args)

    def test_real_prepare_refuses_unapproved_capture_before_any_provider(self):
        with self.assertRaises(ParentError) as caught:
            self.reviews.prepare("whole-image")
        self.assertEqual(caught.exception.reason_code, "terminal-native-capture-authority-unavailable")
        self.assertEqual(self.factory_contexts, [])
        self.assertFalse(self.link.exists())
        self.assert_uncompleted()

    def test_prepare_is_immutable_hash_named_and_never_paid_or_linked(self):
        with self.capture_transport():
            result = self.reviews.prepare("whole-image")
            repeated = self.reviews.prepare("whole-image")
        self.assertEqual(result, repeated)
        raw = _read(self.workspace, result["context"]["path"])[0]
        context = json.loads(raw)
        self.assertTrue(result["context"]["path"].endswith(
            route_fixtures.AREA + "quality-review-context-" + canonical_digest(context) + ".json"))
        self.assertEqual(result["context"]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(self.factory_contexts, [])
        self.availability.assert_not_called()
        self.fallback.assert_not_called()
        self.assertFalse(self.link.exists())
        self.assertFalse(terminal.command_output(result)["terminalAcceptance"])
        self.assert_uncompleted()

    def test_consult_reproduces_context_and_uses_real_record_bytes(self):
        self.link.write_bytes(b'{"previous":"answered reviews may replace the pointer"}')
        # A caller-controlled old context is irrelevant, not an input API.
        stale = self.child / (route_fixtures.AREA + "quality-review-context.json")
        stale.write_bytes(b'{"forged":"caller authority"}')
        with self.capture_transport(), patch.object(self.parent, "_verified", wraps=self.parent._verified) as verified:
            result = self.reviews.consult("whole-image")
        self.assertEqual(result["status"], "decision-recorded")
        self.assertEqual(verified.call_count, 2)
        link = json.loads(self.link.read_bytes())
        self.assertEqual(link, {"context": result["context"], "record": result["record"]})
        record_raw = _read(self.workspace, link["record"]["path"])[0]
        record = json.loads(record_raw)
        self.assertEqual(link["record"]["sha256"], hashlib.sha256(record_raw).hexdigest())
        self.assertNotEqual(link["record"]["sha256"], record["recordId"])
        self.assertEqual(self.controller.consume(record, json.loads(
            _read(self.workspace, link["context"]["path"])[0]), consent=self.case.consent), record)
        original = self.factory_contexts[0]
        self.assertTrue(all("path" in item for item in original["bindings"]))
        self.assertTrue(all("path" not in item for item in self.projected_contexts[0]["bindings"]))
        self.assertEqual(stale.read_bytes(), b'{"forged":"caller authority"}')
        self.fallback.assert_not_called()
        self.assert_uncompleted()
        # Leaving the transport fixture cannot turn the record into acceptance.
        self.assert_rejected(self.parent.complete_child, "whole-image")

    def test_uncertain_journal_record_never_creates_or_replaces_link(self):
        self.critical_choice = "uncertain"
        with self.capture_transport():
            result = self.reviews.consult("whole-image")
        self.assertEqual(result["reasonCode"], "review-uncertain")
        self.assertFalse(self.link.exists())
        self.link.write_bytes(b'{"previous":"do not replace on uncertainty"}')
        with self.capture_transport():
            result = self.reviews.consult("whole-image")
        self.assertEqual(result["status"], "request-input")
        self.assertEqual(self.link.read_bytes(), b'{"previous":"do not replace on uncertainty"}')
        self.fallback.assert_not_called()
        self.assert_uncompleted()

    def test_consent_change_invalidates_the_previous_route_without_credential_fallback(self):
        self.case.grant["granted"] = False
        self.case.grant["purposes"] = []
        with self.assertRaises(ValueError) as caught:
            self.reviews.prepare("whole-image")
        self.assertEqual(caught.exception.reason_code, "consent-changed")
        self.assertEqual(self.factory_contexts, [])
        self.fallback.assert_not_called()
        self.assertFalse(self.link.exists())
        self.assert_uncompleted()

    def test_credential_missing_suspends_without_provider_fallback_or_link(self):
        self.availability.return_value = {"status": "credential-missing"}
        with self.capture_transport():
            result = self.reviews.consult("whole-image")
        output = terminal.command_output(result)
        self.assertEqual(output["status"], "suspended")
        self.assertEqual(output["requestedCommand"], "img2-jev login")
        self.assertEqual(output["promptLimit"], 2)
        self.assertEqual(self.factory_contexts, [])
        self.fallback.assert_not_called()
        self.assertFalse(self.link.exists())
        self.assert_uncompleted()

    def test_stale_bound_bytes_during_consult_prevent_link(self):
        self.after_answer = lambda: (self.child / "scene.ts").write_bytes(b"changed during consultation")
        with self.capture_transport():
            self.assert_rejected(self.reviews.consult, "whole-image")
        self.assertFalse(self.link.exists())
        self.assert_uncompleted()

    def test_revoked_aggregate_after_provider_prevents_link(self):
        self.after_answer = self.case.owners.clear
        with self.capture_transport():
            self.assert_rejected(self.reviews.consult, "whole-image")
        self.assertFalse(self.link.exists())
        self.assert_uncompleted()

    def test_changed_immutable_context_after_provider_prevents_link(self):
        def mutate():
            paths = list(self.child.glob(route_fixtures.AREA + "quality-review-context-*.json"))
            self.assertEqual(len(paths), 1)
            paths[0].write_bytes(b"{}")
        self.after_answer = mutate
        with self.capture_transport():
            self.assert_rejected(self.reviews.consult, "whole-image")
        self.assertFalse(self.link.exists())

    def test_caller_cannot_replace_existing_digest_named_context(self):
        with self.capture_transport():
            prepared = self.reviews.prepare("whole-image")
        (self.workspace / prepared["context"]["path"]).write_bytes(b"{}")
        with self.capture_transport():
            self.assert_rejected(self.reviews.consult, "whole-image")
        self.assertEqual(self.factory_contexts, [])
        self.assertFalse(self.link.exists())

    def test_forged_record_id_or_answers_rejected_by_real_journal(self):
        with self.capture_transport():
            result = self.reviews.consult("whole-image")
        raw = _read(self.workspace, result["record"]["path"])[0]
        record = json.loads(raw)
        context = json.loads(_read(self.workspace, result["context"]["path"])[0])
        for forged_id in ("../outside", "0" * 64):
            forged = dict(record, recordId=forged_id)
            self.assert_rejected(self.controller.consume, forged, context)
        forged = deepcopy(record)
        forged["answers"] = {}
        self.assert_rejected(self.controller.consume, forged, context)
        (self.workspace / result["record"]["path"]).write_bytes(_encode(forged))
        self.assert_rejected(self.controller.consume, record, context)
        previous = self.link.read_bytes()
        with self.capture_transport():
            self.assert_rejected(self.reviews.consult, "whole-image")
        self.assertEqual(self.link.read_bytes(), previous)

    def test_symlinked_context_directory_cannot_escape_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            area = self.child / route_fixtures.AREA
            area.rename(self.child / "original-pixi2d")
            area.symlink_to(Path(temporary).resolve(), target_is_directory=True)
            self.assert_rejected(self.reviews.prepare, "whole-image")
            self.assertEqual(list(Path(temporary).iterdir()), [])
        self.assertEqual(self.factory_contexts, [])
        self.assertFalse(self.link.exists())


class TerminalCLIContractTests(unittest.TestCase):
    def arguments(self):
        return ["prepare", "--workspace", "/workspace", "--run-id", "run", "--object-id", "object",
                "--decision-ref", ".img2threejs/route/decision-" + "d" * 64 + ".json",
                "--policy-module", "/host/policy.py", "--policy-sha256", "a" * 64,
                "--harness-root", "/host/harness", "--harness-sha", "b" * 40,
                "--harness-executable", "/host/harness/bin/img2.mjs", "--harness-sha256", "c" * 64]

    def test_parser_requires_all_explicit_pins_and_no_context_input(self):
        parser = terminal.build_parser()
        args = self.arguments()
        self.assertEqual(parser.parse_args(args).command, "prepare")
        for flag in ("--workspace", "--run-id", "--object-id", "--decision-ref", "--policy-module",
                     "--policy-sha256", "--harness-root", "--harness-sha", "--harness-executable", "--harness-sha256"):
            shortened = args[:]
            index = shortened.index(flag)
            del shortened[index:index + 2]
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(shortened)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(args + ["--context", "caller-authority.json"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            terminal.main(args + ["--fallback-command", "/host/fallback"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            terminal.main(args + ["--fallback-sha256", "f" * 64])

    def test_actionable_login_and_consent_remain_distinct_and_never_accept(self):
        for reason, command in (("credential-missing", "img2-jev login"),
                                ("consent-declined", "img2-jev consent authorize-decisions")):
            output = terminal.command_output({"status": "request-input", "reasonCode": reason})
            self.assertEqual(output["reasonCode"], reason)
            self.assertEqual(output["requestedCommand"], command)
            self.assertEqual(output["promptLimit"], 2)
            self.assertFalse(output["deliverable"])
            self.assertFalse(output["terminalAcceptance"])

    def test_errors_do_not_echo_arbitrary_exception_or_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = self.arguments()
            args[args.index("--workspace") + 1] = temporary
            output = io.StringIO()
            error = DecisionError("private-secret-key")
            with patch.object(route, "_load_decision", side_effect=error), redirect_stdout(output):
                code = terminal.main(args)
        value = json.loads(output.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(value["reasonCode"], "terminal-input-invalid")
        self.assertNotIn("private-secret", output.getvalue())

    def test_actual_cli_preserves_named_native_policy_refusal_without_raw_details(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = self.arguments()
            args[args.index("--workspace") + 1] = temporary
            output = io.StringIO()
            error = terminal.ParentError("native-policy-unavailable:private-detail")
            with patch.object(route, "_load_decision", side_effect=error), redirect_stdout(output):
                code = terminal.main(args)
        value = json.loads(output.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(value["reasonCode"], "native-policy-unavailable")
        self.assertNotIn("private-detail", output.getvalue())
        self.assertFalse(value["terminalAcceptance"])

    def test_policy_excludes_workspace_base_harness_and_every_provider_including_jev(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            harness = root / "harness"
            provider = root / "provider"
            jev = root / "jev"
            for path in (harness, provider, jev):
                path.mkdir()
            discovery = SimpleNamespace(jev={"dir": str(jev)},
                query=lambda edge: {"providers": [{"dir": str(provider)}]})
            roots = route.route_policy_roots(discovery, SimpleNamespace(root=harness))
            self.assertEqual(set(roots), {harness, provider, jev})
            raw = b"raise RuntimeError('candidate policy must never execute')\n"
            for directory in (workspace, harness, provider, jev):
                policy = directory / "policy.py"
                policy.write_bytes(raw)
                with self.assertRaises(DecisionError) as caught:
                    route.load_route_policies(policy, hashlib.sha256(raw).hexdigest(), workspace=workspace,
                        run_id="run", excluded_roots=roots)
                self.assertEqual(caught.exception.reason_code, "policy-module-unsafe")
            # Existing checkout code is also unsafe, without writing any repo file.
            with self.assertRaises(DecisionError) as caught:
                route.load_route_policies(Path(terminal.__file__).resolve(), "0" * 64,
                    workspace=workspace, run_id="run", excluded_roots=roots)
            self.assertEqual(caught.exception.reason_code, "policy-module-unsafe")
            host = root / "host-policy.py"
            host.write_bytes(raw)
            linked = root / "linked-policy.py"
            linked.symlink_to(host)
            with self.assertRaises(DecisionError):
                route.load_route_policies(linked, hashlib.sha256(raw).hexdigest(), workspace=workspace, run_id="run")
            with self.assertRaises(DecisionError):
                route.load_route_policies(host, "0" * 64, workspace=workspace, run_id="run")


class TerminalDeclinedFallbackTests(unittest.TestCase):
    def test_fresh_declined_route_and_review_use_only_the_explicit_host_callback(self):
        case = route_fixtures.RouteAuthorityTests()
        self.addCleanup(case.doCleanups)
        router_class = route_fixtures.Router
        def declined_router(*args, **kwargs):
            case.grant.update(granted=False, purposes=[])
            def answer(projected):
                result = case.answer(projected)
                result["model"] = "offline-terminal-fixture"
                return result
            kwargs["fallback"] = answer
            return router_class(*args, **kwargs)
        with patch.object(route_fixtures, "Router", side_effect=declined_router):
            case.setUp()
        fixture, authority = case.quality_fixture()
        child = fixture[1]
        parent = RouteParent(case.controller, verify_aggregate=authority, consent=case.consent)
        parent_before = (case.workspace / ".img2threejs/state.json").read_bytes()
        child_before = (child / "state.json").read_bytes()
        factory = Mock()
        factory.provider.side_effect = AssertionError("declined Jev must not receive a review")
        def review(projected):
            result = case.quality_answer(projected)
            result["model"] = "offline-terminal-fixture"
            return result
        reviews = terminal.TerminalReviews(parent, provider_factory=factory,
            availability=lambda: {"status": "credential-missing"}, fallback=review)
        # Only capture transport is synthetic; route, journal and semantic consumer are real.
        with patch.object(authority._pixi_capture_authority, "verify_capture", return_value=None):
            result = reviews.consult("whole-image")
        self.assertEqual(result["status"], "decision-recorded")
        record = terminal._decode(terminal._read(case.workspace, result["record"]["path"])[0])
        self.assertEqual(record["decider"], "model:offline-terminal-fixture")
        self.assertEqual(record["unavailableReason"], "consent-declined")
        with case.controller.workspace_transaction(consent=case.consent) as verify:
            authenticated = authority.consume_pixi_quality(verify, "whole-image", child)
            self.assertEqual(authenticated["recordId"], record["recordId"])
        case.provider.assert_not_called()
        factory.provider.assert_not_called()
        self.assertEqual((case.workspace / ".img2threejs/state.json").read_bytes(), parent_before)
        self.assertEqual((child / "state.json").read_bytes(), child_before)


if __name__ == "__main__":
    unittest.main()
