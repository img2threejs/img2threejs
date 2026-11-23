"""Offline user-attested primary-model routing; real stores, controller and router."""
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import pty
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

FORGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORGE))
sys.path.insert(0, str(FORGE / "_shared"))
sys.path.insert(0, str(Path(__file__).parent))
import test_stage0_route as fixtures
from decision_control import DecisionController, DecisionError, canonical_digest
import route_no_account as authority
from stage0_route.images import png
from stage0_route.route import Router

SPEC = importlib.util.spec_from_file_location("no_account_route_cli", FORGE / "route.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)
MODEL = "test-provider:test-model"


class NoAccountRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.home = self.root / "user-home"
        self.image = self.workspace / "ref.png"
        self.image.write_bytes(png(16, 16, 3, bytes(range(256)) * 3))
        self.objects = fixtures.Stage0RouteTests.proposals()
        (self.workspace / "objects.json").write_text(json.dumps(self.objects))
        self.candidates = []
        self.credential = patch.object(authority, "credential_status", return_value="credential-missing").start()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"IMG2_HOME": str(self.home)}).start()
        self.attestation = None
        self.answers = []

    def typed(self, text):
        """Point the confirmation device at a real pty with ``text`` already typed on it."""
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        if text:
            os.write(master, text.encode())
        patch.object(authority, "TTY_PATH", os.ttyname(slave)).start()

    @staticmethod
    def phrase(revoke=False):
        return "revoke run-1" if revoke else "attest run-1 " + MODEL

    def mint(self, **kwargs):
        output = io.StringIO()
        revoke = kwargs.get("revoke", False)
        self.typed(self.phrase(revoke) + "\n")
        # Neither stdin nor input() may satisfy the confirmation; only the tty device does.
        with patch("builtins.input", side_effect=AssertionError("confirmation must not use input()")), \
                redirect_stdout(output):
            value = authority.attest(self.workspace, "run-1", model_id=MODEL, images=["ref.png"],
                                     token_budget=kwargs.pop("token_budget", 256000), **kwargs)
        if not revoke:
            self.attestation = value
            self.assertIn(MODEL, output.getvalue())
            self.assertIn(hashlib.sha256(self.image.read_bytes()).hexdigest(), output.getvalue())
            self.assertIn("derived crops", output.getvalue())
            self.assertIn("NOT Jev", output.getvalue())
        return value

    def grant(self):
        return authority.RoutingNoAccount(self.workspace, "run-1", "ref.png")

    def journal(self):
        path = self.workspace / ".img2threejs/route/journal.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def invoke(self, *, questions=False, extra=(), no_account=True):
        argv = (["questions"] if questions else []) + ["--workspace", str(self.workspace), "--run-id", "run-1",
                "--image", "ref.png", "--objects", "objects.json"]
        if no_account:
            argv.append("--no-jev-account")
        if self.answers:
            data = {"kind": "img2.route-model-answers", "version": 1,
                    "attestationId": self.attestation["attestationId"], "answers": self.answers}
            (self.workspace / "answers.json").write_text(json.dumps(data))
            argv.extend(["--model-answers", "answers.json"])
        output = io.StringIO()
        with patch.object(cli, "PinnedDiscovery", side_effect=lambda _: fixtures.FixtureDiscovery(
                self.workspace, self.candidates)), patch.object(cli, "LiveConsent") as jev_consent, \
                patch.object(cli, "JevProviderFactory") as jev, patch.object(cli, "_approved_lock") as lock, \
                redirect_stdout(output):
            code = cli.main([*argv, *extra])
        if no_account:
            jev.assert_not_called()
            jev_consent.assert_not_called()
            lock.assert_not_called()
        return code, json.loads(output.getvalue())

    def question(self):
        code, output = self.invoke(questions=True)
        self.assertEqual((code, output["status"]), (0, "questions-ready"), output)
        ref = output["questionsRef"]
        data = (self.workspace / ref["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), ref["sha256"])
        request = json.loads(data)
        self.assertEqual(request["contextDigest"], canonical_digest(request["state"]))
        self.assertFalse(request["presentedAsJev"])
        return request

    def answer(self, request, **kwargs):
        answer = fixtures.Stage0RouteTests.answer(request["state"], model=MODEL, **kwargs)
        self.answers.append(answer)
        return answer

    def complete(self, **selection):
        self.answer(self.question())
        self.answer(self.question(), **selection)
        code, output = self.invoke()
        self.assertEqual(code, 0, output)
        decision = json.loads((self.workspace / output["decisionRef"]).read_bytes())
        return output, decision

    def test_attestation_roundtrip_private_external_store_and_journal(self):
        value = self.mint()
        grant = self.grant()
        self.assertEqual(grant()["sha256"], value["attestationId"])
        self.assertEqual(grant()["purposes"], ["routing"])
        path = authority._path(self.workspace, "run-1")
        self.assertNotIn(self.workspace, path.parents)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        attested = [e["data"] for e in self.journal() if e["event"] == "no-account-attested"]
        self.assertEqual(attested[0]["attestationId"], value["attestationId"])
        self.assertFalse(attested[0]["presentedAsJev"])

    def attempt(self, **kwargs):
        with redirect_stdout(io.StringIO()), self.assertRaises(DecisionError) as caught:
            authority.attest(self.workspace, "run-1", model_id=MODEL, images=["ref.png"], token_budget=128000, **kwargs)
        self.assertEqual(caught.exception.reason_code, "user-attestation-required")
        self.assertFalse(self.home.exists())
        return caught.exception

    def test_confirmation_needs_the_tty_device_not_stdin_or_a_blind_attest(self):
        piped = io.StringIO("ATTEST\n" + self.phrase() + "\n")
        # "\x04\x04" is the pty EOF character: a user (or host) closing the terminal without a line.
        for typed in ("ATTEST\n", "attest run-1\n", self.phrase() + " \n", self.phrase() + "\x04\x04", "\x04\x04", "\n",
                      "attest run-2 " + MODEL + "\n", "attest run-1 other:model\n", self.phrase(revoke=True) + "\n"):
            with self.subTest(typed=typed):
                self.typed(typed)
                with patch.object(sys, "stdin", piped), \
                        patch("builtins.input", side_effect=AssertionError("must not read input()")):
                    self.attempt()

    def test_no_terminal_or_unreadable_terminal_is_a_clean_refusal(self):
        with patch.object(authority, "TTY_PATH", str(self.root / "no-such-tty")):
            self.attempt()
        regular = self.root / "not-a-tty"
        regular.write_text(self.phrase() + "\n")
        with patch.object(authority, "TTY_PATH", str(regular)):
            self.attempt()
        with patch.object(authority.os, "read", side_effect=EOFError), patch.object(authority.os, "isatty", return_value=True):
            self.typed(self.phrase() + "\n")
            self.attempt()
        with patch.object(authority.os, "read", side_effect=OSError("tty closed")):
            self.typed(self.phrase() + "\n")
            self.attempt()

    def test_cli_reports_refused_confirmation_without_a_traceback(self):
        argv = ["attest-no-jev-account", "--workspace", str(self.workspace), "--run-id", "run-1", "--image", "ref.png",
                "--model-id", MODEL, "--token-budget", "128000", "--accept-image-disclosure"]
        output = io.StringIO()
        with patch.object(authority, "TTY_PATH", str(self.root / "no-such-tty")), redirect_stdout(output):
            code = cli.main(argv)
        self.assertEqual(code, 3)
        last = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(last["reasonCode"], "user-attestation-required")
        self.assertIn("/dev/tty", last["message"])
        self.assertNotIn("outside the agent control path", last["message"])

    def test_revoke_and_expiry_invalidate_already_loaded_authority(self):
        value = self.mint(ttl=60)
        grant = self.grant()
        with patch.object(authority.time, "time", return_value=value["expiresAt"]), self.assertRaises(DecisionError) as caught:
            grant.metadata()
        self.assertEqual(caught.exception.reason_code, "attestation-expired")
        self.mint(revoke=True)
        with self.assertRaises(DecisionError) as caught:
            grant()
        self.assertEqual(caught.exception.reason_code, "attestation-revoked")
        revoked = [e["data"] for e in self.journal() if e["event"] == "no-account-revoked"]
        self.assertEqual(revoked[0]["revokedAttestationId"], value["attestationId"])

    def test_flag_without_attestation_refuses_and_names_user_command(self):
        code, output = self.invoke()
        self.assertEqual(code, 3)
        self.assertEqual(output["reasonCode"], "no-jev-account-attestation-required")
        self.assertIn("attest-no-jev-account", output["requestedCommand"])
        self.assertFalse((self.workspace / ".img2threejs").exists())

    def test_flag_with_present_credential_refuses_even_with_attestation(self):
        self.mint()
        self.credential.return_value = "present"
        code, output = self.invoke()
        self.assertEqual((code, output["reasonCode"]), (3, "jev-account-present"))
        self.assertIn("Use Jev", output["message"])
        self.assertFalse(any(e["event"] == "attempt-pending" for e in self.journal()))

    def test_images_workspace_run_and_store_privacy_are_bound(self):
        self.mint()
        for mode in ("image", "permissions", "symlink", "workspace-home", "run"):
            with self.subTest(mode=mode):
                if mode == "image":
                    old = self.image.read_bytes()
                    self.image.write_bytes(png(4, 4, 3, bytes(48)))
                    with self.assertRaisesRegex(DecisionError, "attestation-binding-mismatch"):
                        self.grant()
                    self.image.write_bytes(old)
                elif mode == "permissions":
                    path = authority._path(self.workspace, "run-1")
                    path.chmod(0o644)
                    with self.assertRaisesRegex(DecisionError, "attestation-storage-error"):
                        self.grant()
                    path.chmod(0o600)
                elif mode == "symlink":
                    link = self.root / "home-link"
                    link.symlink_to(self.home, target_is_directory=True)
                    with patch.dict(os.environ, {"IMG2_HOME": str(link)}), self.assertRaises(DecisionError):
                        self.grant()
                elif mode == "workspace-home":
                    with patch.dict(os.environ, {"IMG2_HOME": str(self.workspace / "authority")}), self.assertRaises(DecisionError):
                        self.grant()
                else:
                    with self.assertRaises(DecisionError):
                        authority.RoutingNoAccount(self.workspace, "other-run", "ref.png")

    def test_model_answers_complete_route_and_record_honest_receipts(self):
        self.mint()
        output, decision = self.complete()
        for value in (output, decision):
            self.assertEqual(value["decider"], "model:" + MODEL)
            self.assertFalse(value["presentedAsJev"])
            self.assertEqual(value["attestationId"], self.attestation["attestationId"])
        self.assertEqual(decision["objects"][0]["selected"], ["base:generic"])
        self.assertEqual(decision["jevSession"], "not-used:no-account")
        events = self.journal()
        self.assertEqual(len([e for e in events if e["event"] == "attempt-pending"]), 2)
        self.assertFalse(any(e["event"] == "probe" for e in events))
        for event in events:
            if event["event"] in {"attempt-pending", "no-account-selected"}:
                self.assertEqual(event["data"]["decider"], "model:" + MODEL)
                self.assertFalse(event["data"]["presentedAsJev"])
                self.assertEqual(event["data"]["attestationId"], self.attestation["attestationId"])
            if event["event"] == "decision-recorded":
                record = event["data"]["record"]
                self.assertFalse(record["presentedAsJev"])
                self.assertNotIn("unavailableReason", record)
        grant = self.grant()
        controller = DecisionController(self.workspace, "run-1", routing_authority=grant)
        router = Router(self.workspace, "run-1", controller=controller,
                        discovery=fixtures.FixtureDiscovery(self.workspace), consent=grant, requested_model=MODEL)
        self.assertEqual(router.verify_decision(output["decisionRef"]), decision)
        self.mint(revoke=True)
        with self.assertRaises(DecisionError):
            router.verify_decision(output["decisionRef"])

    def test_missing_answers_does_not_reserve_or_dispatch(self):
        self.mint()
        code, output = self.invoke()
        self.assertEqual((code, output["reasonCode"]), (3, "model-answers-required"))
        self.assertIn("questionsRef", output)
        self.assertFalse(any(e["event"] in {"attempt-pending", "route-round-dispatch"} for e in self.journal()))

    def test_forged_advisory_identity_and_schema_rejected_before_dispatch(self):
        self.mint()
        request = self.question()
        valid = fixtures.Stage0RouteTests.answer(request["state"], model=MODEL)
        for field, value in (("model", "not-the-authorized-model"), ("subject", {}),
                             ("answers", {"selection": {"type": "choice", "choice": "forged"}}),
                             ("usage", {"input_tokens": -1, "output_tokens": 0})):
            with self.subTest(field=field):
                self.answers = [dict(valid, **{field: value})]
                code, output = self.invoke()
                self.assertEqual(code, 3, output)
                self.assertNotIn("decisionRef", output)
        self.assertFalse(any(e["event"] == "attempt-pending" for e in self.journal()))

    def test_answers_cannot_borrow_another_attestation(self):
        self.mint()
        value = {"kind": "img2.route-model-answers", "version": 1, "attestationId": "0" * 64, "answers": []}
        (self.workspace / "forged.json").write_text(json.dumps(value))
        code, output = self.invoke(extra=("--model-answers", "forged.json"))
        self.assertEqual((code, output["reasonCode"]), (3, "invalid-model-answers"))

    def test_ineligible_dependency_candidate_cannot_be_chosen(self):
        self.mint()
        discovery = fixtures.FixtureDiscovery(self.workspace)
        candidate = discovery.candidate("interior", deps={"blender>=4.3": False})
        self.candidates.append(candidate)
        self.answer(self.question())
        request = self.question()
        self.assertEqual([c["routeId"] for c in request["state"]["candidates"]], ["base-generic"])
        answer = self.answer(request)
        forged = "candidate-" + hashlib.sha256(candidate["id"].encode()).hexdigest()
        answer["answers"]["selection"].update(choice=forged, probabilities={forged: 1})
        code, output = self.invoke()
        self.assertEqual((code, output["reasonCode"]), (3, "out-of-set"))
        self.assertEqual(len([e for e in self.journal() if e["event"] == "attempt-pending"]), 1)

    def test_primary_model_uncertainty_uses_same_best_of_n_as_jev(self):
        self.mint()
        self.candidates.append(fixtures.FixtureDiscovery(self.workspace).candidate())
        _, decision = self.complete(probability=.65, confidence=.9)
        self.assertEqual(decision["objects"][0]["status"], "best-of-n")
        self.assertEqual(len(decision["objects"][0]["selected"]), 2)
        self.assertEqual(decision["objects"][0]["reasonCode"], "choice-uncertain")

    def test_recomputed_forged_attestation_without_user_journal_cannot_decide(self):
        self.mint()
        path = authority._path(self.workspace, "run-1")
        value = json.loads(path.read_bytes())
        value["generation"] = "forged-generation"
        value.pop("attestationId")
        value["attestationId"] = canonical_digest(value)
        path.write_text(json.dumps(value))
        self.attestation = value
        code, output = self.invoke()
        self.assertEqual((code, output["reasonCode"]), (3, "attestation-unproven"))
        self.assertFalse(any(e["event"] == "attempt-pending" for e in self.journal()))

    def test_explicit_2d_never_uses_generic_3d_even_in_model_mode(self):
        self.mint()
        code, output = self.invoke(extra=("--expected-output", "2d"))
        self.assertEqual((code, output["reasonCode"]), (3, "routing-suspended"))
        self.assertFalse(any(e["event"] == "attempt-pending" for e in self.journal()))
        decision = json.loads((self.workspace / output["decisionRef"]).read_bytes())
        self.assertEqual(decision["objects"][0]["reasonCode"], "explicit-2d-unavailable")
        self.assertEqual(decision["objects"][0]["selected"], [])

    def test_attestation_replacement_invalidates_cached_records(self):
        self.mint()
        _, decision = self.complete()
        grant = self.grant()
        controller = DecisionController(self.workspace, "run-1", routing_authority=grant)
        consultation = decision["objects"][0]["consultations"][-1]
        record = json.loads((self.workspace / consultation["decisionRef"]).read_bytes())
        context = json.loads((self.workspace / consultation["contextRef"]["path"]).read_bytes())
        self.mint()
        with self.assertRaisesRegex(DecisionError, "attestation-changed"):
            controller.consume(record, context, consent=grant)

    def test_token_budget_remains_a_veto(self):
        self.mint(token_budget=100)
        self.answer(self.question())
        code, output = self.invoke()
        self.assertEqual(code, 3)
        self.assertEqual(output["reasonCode"], "budget-exhausted")
        self.assertFalse(any(e["event"] == "attempt-pending" for e in self.journal()))


    DECIDER_FIELDS = {"decider", "presentedAsJev", "attestationId"}

    def test_pending_and_error_outputs_carry_no_decider_claims(self):
        code, output = self.invoke()  # no attestation
        self.assertEqual((code, output["reasonCode"]), (3, "no-jev-account-attestation-required"))
        self.assertEqual(output["decisionMode"], "no-jev-account-pending")
        self.assertFalse(self.DECIDER_FIELDS & set(output))
        self.mint()
        for label, call in (("questions", lambda: self.invoke(questions=True)), ("answers-required", self.invoke)):
            with self.subTest(label):
                code, output = call()
                self.assertEqual(output["decisionMode"], "no-jev-account-pending")
                self.assertFalse(self.DECIDER_FIELDS & set(output), output)
        self.assertEqual(output["reasonCode"], "model-answers-required")

    def test_decided_output_names_the_model_only_after_it_decided(self):
        self.mint()
        output, _ = self.complete()
        self.assertEqual(output["decisionMode"], "no-account")
        self.assertEqual(output["decider"], "model:" + MODEL)
        self.assertIs(output["presentedAsJev"], False)
        self.assertEqual(output["attestationId"], self.attestation["attestationId"])

    def test_output_rereads_attestation_so_a_mid_run_revoke_leaves_no_stale_id(self):
        self.mint()
        self.answer(self.question())
        self.answer(self.question())
        revoke = lambda *args, **kwargs: (self.mint(revoke=True), [])[1]
        with patch.object(cli, "fallback_decisions", side_effect=revoke):
            code, output = self.invoke()
        self.assertEqual((code, output["reasonCode"]), (3, "attestation-revoked"))
        self.assertEqual(output["decisionMode"], "no-jev-account-pending")
        self.assertFalse(self.DECIDER_FIELDS & set(output), output)

    def test_busy_workspace_leaves_no_enabled_store_behind(self):
        other = DecisionController(self.workspace, "someone-else")
        self.addCleanup(other.release)
        self.typed(self.phrase() + "\n")
        with redirect_stdout(io.StringIO()), self.assertRaises(DecisionError) as caught:
            authority.attest(self.workspace, "run-1", model_id=MODEL, images=["ref.png"], token_budget=256000)
        self.assertEqual(caught.exception.reason_code, "workspace-busy")
        self.assertFalse(authority._path(self.workspace, "run-1").exists())
        with self.assertRaisesRegex(DecisionError, "no-jev-account-attestation-required"):
            self.grant()

    def test_journal_failure_after_revoke_keeps_the_store_revoked(self):
        self.mint()
        other_journal = patch.object(DecisionController, "_commit", side_effect=DecisionError("workspace-busy"))
        self.typed(self.phrase(revoke=True) + "\n")
        with other_journal, redirect_stdout(io.StringIO()), self.assertRaises(DecisionError):
            authority.attest(self.workspace, "run-1", model_id=MODEL, images=["ref.png"], token_budget=1, revoke=True)
        with self.assertRaisesRegex(DecisionError, "attestation-revoked"):
            self.grant()


class CredentialProbeTests(unittest.TestCase):
    """The veto reuses the pinned Jev plugin's own ``jev_auth.auth_status``; fakes only, no Keychain."""
    PLUGIN = (
        "import os\n"
        "RESULT = {behavior!r}\n"
        "def auth_status(environ=None):\n"
        "    if RESULT == 'raise':\n"
        "        raise RuntimeError('keychain unavailable')\n"
        "    return RESULT\n")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve() / "home"
        patch.dict(os.environ, {"IMG2_HOME": str(self.home)}).start()
        self.addCleanup(patch.stopall)
        self.lock = Path(self.tmp.name) / "lock" / "jev_release_lock.json"
        self.lock.parent.mkdir()
        patch.object(authority, "_LOCK", self.lock).start()

    def install(self, behavior, *, pinned=True):
        tools = self.home / "plugins/jev/tools"
        tools.mkdir(parents=True, exist_ok=True)
        source = self.PLUGIN.format(behavior=behavior).encode()
        (tools / "jev_auth.py").write_bytes(source)
        digest = hashlib.sha256(source if pinned else b"other").hexdigest()
        self.lock.write_text(json.dumps({"files": {"tools/jev_auth.py": digest}}))

    def test_plugin_answers_decide_and_inconclusive_answers_mean_account_present(self):
        present = {"status": "present", "reasonCode": "credential-present"}
        missing = {"status": "missing", "reasonCode": "credential-missing"}
        for behavior, expected in ((present, "present"), (missing, "credential-missing")):
            with self.subTest(behavior=behavior["status"]):
                self.install(behavior)
                self.assertEqual(authority.credential_status(), expected)
        for behavior in ({"status": "storage-error", "reasonCode": "credential-storage-error"},
                         {"status": "missing", "reasonCode": "credential-present"}, {"status": "present"},
                         {"status": "weird", "reasonCode": "x"}, "raise", ["not", "a", "dict"]):
            with self.subTest(behavior=behavior):
                self.install(behavior)
                with self.assertRaisesRegex(DecisionError, "credential-storage-error"):
                    authority.credential_status()
                with self.assertRaisesRegex(DecisionError, "jev-account-present"):
                    authority.require_no_credential()

    def test_tampered_or_unreadable_plugin_is_never_executed_and_never_means_no_account(self):
        marker = self.home / "executed"
        self.install({"status": "missing", "reasonCode": "credential-missing"}, pinned=False)
        (self.home / "plugins/jev/tools/jev_auth.py").write_text(f"open({str(marker)!r}, 'w')\n")
        with self.assertRaisesRegex(DecisionError, "jev-account-present"):
            authority.require_no_credential()
        self.assertFalse(marker.exists())
        self.install({"status": "missing", "reasonCode": "credential-missing"})
        self.lock.write_text("{not json")
        with self.assertRaisesRegex(DecisionError, "jev-account-present"):
            authority.require_no_credential()

    def test_environment_alone_cannot_hide_an_installed_jev_account(self):
        # An env-only Jev account on a non-macOS host: the plugin reports present from the environment,
        # and clearing the variable afterwards must not be able to turn that into "no account"
        # while the plugin cannot say "missing".
        self.install({"status": "storage-error", "reasonCode": "credential-storage-error"})
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}), self.assertRaisesRegex(DecisionError, "jev-account-present"):
            authority.require_no_credential()

    def test_without_an_installed_plugin_only_the_automation_override_counts(self):
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}):
            self.assertEqual(authority.credential_status(), "credential-missing")
            authority.require_no_credential()
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "fake-no-network"}):
            self.assertEqual(authority.credential_status(), "present")
            with self.assertRaisesRegex(DecisionError, "jev-account-present"):
                authority.require_no_credential()


if __name__ == "__main__":
    unittest.main()
