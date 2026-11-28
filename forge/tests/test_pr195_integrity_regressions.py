"""Small defensive PR195 counterexamples using only owned synthetic files/processes."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parent / "_shared")]
import test_best_of_n as trial_fixtures
import test_native_terminal_authority as native_fixtures
import test_route_no_account as account_fixtures
import test_textured_workflow as textured_fixtures
from decision_control import DecisionController, DecisionError


class NativeProjectionRegressionTests(unittest.TestCase):
    def test_safe_projection_retains_local_freshness_without_disclosing_nested_paths(self):
        case = native_fixtures.NativeTerminalAuthorityTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.snapshot["deterministic"]["bindings"] = {"source": case.binding("source.py")}
        case.snapshot["captures"][0]["view"].update(camera={"path": "/owned/camera.json", "fov": 45})
        context = case.authority._context(case.policy, case.child, case.snapshot)
        controller = DecisionController(case.parent, "run-1")
        projected = controller._context(context)
        evidence = projected["observations"]["nativeEvidence"]
        self.assertNotIn('"path"', json.dumps(evidence))
        self.assertNotIn("/owned/camera.json", json.dumps(evidence))
        self.assertEqual(evidence["captures"][0]["view"]["camera"]["fov"], 45)
        (case.child / "source.py").write_text("# Changed owned fixture\n")
        with self.assertRaises(DecisionError):
            controller._context(context)


class GenerationIntegrityRegressionTests(unittest.TestCase):
    def setUp(self):
        self.case = textured_fixtures.GenerationBudgetTest()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_overwritten_attempts_consume_the_same_immutable_cap(self):
        self.case.write_state(1)
        self.assertEqual(self.case.record(1, scenario="first").returncode, 0)
        before = (textured_fixtures.C.gen_dir(self.case.ws, 1) / "generate.json").read_bytes()
        refused = self.case.record(1, scenario="second", extra=("--overwrite",))
        self.assertEqual(refused.returncode, 2, refused.stdout)
        self.assertEqual((textured_fixtures.C.gen_dir(self.case.ws, 1) / "generate.json").read_bytes(), before)

    def test_superseded_signature_cannot_be_repeated_and_state_cannot_raise_cap(self):
        self.case.write_state(3)
        self.assertEqual(self.case.record(1, scenario="first").returncode, 0)
        self.assertEqual(self.case.record(1, scenario="second", extra=("--overwrite",)).returncode, 0)
        repeated = self.case.record(1, scenario="first", extra=("--overwrite",))
        self.assertEqual(repeated.returncode, 2, repeated.stdout)
        self.case.edit_cap(8)
        raised = self.case.record(2, scenario="third")
        self.assertEqual(raised.returncode, 2, raised.stdout)

    def test_foreign_state_does_not_authorize_workspace_generations(self):
        self.case.write_state(1)
        foreign = self.case.ws / "foreign-state.json"
        foreign.write_text(json.dumps(textured_fixtures.new_state("r.png", profile="textured", max_generations=8)))
        refused = self.case.record(1, extra=("--state", str(foreign)))
        self.assertEqual(refused.returncode, 2, refused.stdout)
        self.assertFalse(textured_fixtures.C.gen_dir(self.case.ws, 1).exists())

    def test_obj_alternate_material_extension_still_checks_owned_texture_references(self):
        raw = self.case.ws / "raw"
        raw.mkdir()
        (raw / "model.obj").write_text("mtllib material.txt\nv 0 0 0\n")
        (self.case.ws / "outside-fixture.png").write_bytes(b"owned synthetic texture")
        (raw / "material.txt").write_text("newmtl a\nmap_Kd ../outside-fixture.png\n")
        with self.assertRaises(textured_fixtures.C.TexturedError):
            textured_fixtures.C.require_contained_references(raw / "model.obj", raw)
        (raw / "material.txt").write_text("newmtl a\nmap_Kd texture.png\n")
        (raw / "texture.png").write_bytes(b"owned synthetic texture")
        textured_fixtures.C.require_contained_references(raw / "model.obj", raw)


class TrialIntegrityRegressionTests(unittest.TestCase):
    def setUp(self):
        self.case = trial_fixtures.BestOfNTests()
        self.case.setUp()
        self.addCleanup(self.case.cleanup)
        self.case.literal = "benign owned fixture argument"

    def test_trial_process_receives_no_ambient_secret_or_startup_canaries(self):
        self.case.worker.write_text(self.case.worker.read_text() +
            '\n(root / "environment.json").write_text(json.dumps({k:os.environ.get(k) for k in '
            '("TYPESAFE_API_KEY","GITHUB_TOKEN","NODE_OPTIONS","PYTHONPATH")}))\n')
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "benign-canary", "GITHUB_TOKEN": "benign-canary"}):
            result = self.case.run_trials()
        for attempt in result["attempts"]:
            observed = json.loads((self.case.workspace / attempt["directory"] / "environment.json").read_text())
            self.assertTrue(all(value is None for value in observed.values()), observed)

    def test_rejected_symlink_is_a_durable_failure_not_an_ambiguous_retry(self):
        producer = self.case.producer
        def with_owned_link(trial):
            result = producer(trial)
            (trial.directory / "owned-link").symlink_to(self.case.workspace / "reference")
            return result
        self.case.producer = with_owned_link
        record, context = self.case.decide()
        executor = self.case.executor()
        self.case.assert_reason("unsafe-output", lambda: executor.run(
            record, context, consent=self.case.consent))
        before = list(self.case.started_handles)
        self.case.assert_reason("unsafe-output", lambda: executor.run(
            record, context, consent=self.case.consent))
        self.assertEqual(self.case.started_handles, before)


class AttestationBudgetRegressionTests(unittest.TestCase):
    def test_insufficient_budget_is_rejected_before_confirmation_or_persistence(self):
        case = account_fixtures.NoAccountRoutingTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        with patch.object(account_fixtures.authority, "_confirm",
                side_effect=AssertionError("an unusable grant must not reach confirmation")):
            with self.assertRaises(DecisionError) as caught:
                account_fixtures.authority.attest(case.workspace, "run-1", model_id=account_fixtures.MODEL,
                    images=["ref.png"], token_budget=63999)
        self.assertEqual(caught.exception.reason_code, "invalid-attestation")
        self.assertFalse(case.home.exists())
        self.assertEqual(case.mint(token_budget=64000)["tokenBudget"], 64000)


class CredentialHomeRegressionTests(unittest.TestCase):
    def setUp(self):
        self.case = account_fixtures.CredentialProbeTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.user_home = Path(self.case.tmp.name) / "personal-user"
        self.home_patch = patch.object(account_fixtures.authority.Path, "home", return_value=self.user_home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

    def test_alternate_empty_home_cannot_hide_pinned_per_user_credential_status(self):
        self.case.install({"status": "present", "reasonCode": "credential-present"})
        source = self.case.home / "plugins/jev/tools/jev_auth.py"
        canonical = self.user_home / ".img2/plugins/jev/tools/jev_auth.py"
        canonical.parent.mkdir(parents=True)
        canonical.write_bytes(source.read_bytes())
        with patch.dict(os.environ, {"IMG2_HOME": str(self.user_home / "empty"), "TYPESAFE_API_KEY": ""}):
            self.assertEqual(account_fixtures.authority.credential_status(), "present")
            with self.assertRaisesRegex(DecisionError, "jev-account-present"):
                account_fixtures.authority.require_no_credential()

    def test_absent_trusted_auth_bytes_are_unknown_not_missing_credentials(self):
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}):
            with self.assertRaisesRegex(DecisionError, "credential-storage-error"):
                account_fixtures.authority.credential_status()


if __name__ == "__main__":
    unittest.main()
