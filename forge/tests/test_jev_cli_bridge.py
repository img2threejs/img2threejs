"""Disposable executable fixtures only: no plugin imports, credentials or live calls."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))
from decision_control import DecisionController, DecisionError, canonical_digest
from jev_cli_bridge import EDGES, JevCliBridge, REQUIRED_PROVIDER_FILES


# The fixture deliberately implements no network code. Its observable marker
# distinguishes a refusal before spawn from a child that was spawned and refused.
STUB = r'''
import hashlib, json, os, pathlib, sys, time
args = sys.argv[1:]
workspace = pathlib.Path(args[args.index("--workspace") + 1])
with (workspace / "spawned.jsonl").open("a") as marker:
    marker.write(json.dumps(args) + "\n")
mode = json.loads((workspace / "mode.json").read_text())
secret = os.environ.get("TYPESAFE_API_KEY", "no-key")
if mode == "timeout":
    time.sleep(5)
if mode == "large":
    print("x" * (600 * 1024))
    sys.exit(0)
if mode == "bad-stdout":
    print(secret)
    print(secret, file=sys.stderr)
    sys.exit(2)
if mode == "duplicate":
    print('{"kind":"img2.jev-command","kind":"other","version":1}')
    sys.exit(0)
grant = json.loads((workspace / "grant.json").read_text())
value = {"kind": "img2.jev-command", "version": 1}
if args[0] == "consent":
    value.update(status="consent-updated", **grant)
    if mode == "cli-no-consent":
        value.update(status="request-input", reasonCode="consent-required")
        print(json.dumps(value))
        sys.exit(2)
    if mode == "cli-stale-consent":
        value["sha256"] = "c" * 64
else:
    if mode.startswith("error:"):
        value.update(status="request-input", reasonCode=mode.split(":", 1)[1], reason=secret)
        print(json.dumps(value))
        sys.exit(2)
    context = json.loads((workspace / args[args.index("--input") + 1]).read_text())
    context["bindings"] = [{"role": b["role"], "sha256": b["sha256"]} for b in context["bindings"]]
    digest = hashlib.sha256(json.dumps(context, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    answer = {"kind": "img2.decision-advisory", "version": 1, "group": context["group"],
              "subject": context["subject"], "contextDigest": digest,
              "model": context["requestedModel"], "answers": {
                  "selection": {"type": "choice", "choice": "a", "probabilities": {"a": .9, "b": .1}, "confidence": .8},
                  "anyFits": {"type": "noul", "noul": 1}},
              "usage": {"input_tokens": 10, "output_tokens": 2}}
    if context["group"] == "quality-review":
        answer["answers"] = {"dimension:" + dimension: {"type": "unevaluated"} for dimension in (
            "silhouette-proportions", "part-relationships", "geometry",
            "material-appearance", "identity-features")}
    if mode == "foreign-group":
        answer["group"] = "repair"
    if mode == "foreign-subject":
        answer["subject"]["revision"] += 1
    if mode == "foreign-digest":
        answer["contextDigest"] = "d" * 64
    if mode == "wrong-model":
        answer["model"] = "other-model"
    if mode == "uncertain":
        answer["answers"]["selection"].update(probabilities={"a": .6, "b": .4}, confidence=.4)
    if mode == "secret-answer":
        answer["answers"]["secret"] = secret
    if mode == "secret-model":
        answer["model"] = secret
    if mode == "mutate-input":
        (workspace / "reference.png").write_bytes(b"outside edit")
    if mode == "mutate-provider":
        pathlib.Path(__file__).with_name("jev_auth.py").write_text("changed")
    value.update(status="answered", reasonCode="answered", consentSha256=grant["sha256"],
                 cached=False, artifact="not-returned", advisory=answer, diagnostics=secret)
    print(secret, file=sys.stderr)
print(json.dumps(value))
'''


class JevCliBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.provider = self.root / "provider"
        (self.provider / "tools").mkdir(parents=True)
        for relative in REQUIRED_PROVIDER_FILES:
            (self.provider / relative).write_text("# unused pinned fixture\n")
        (self.provider / "tools/jev.py").write_text(STUB)
        (self.provider / "plugin.json").write_text(json.dumps({
            "name": "jev", "capabilities": [{"from": edge[0], "to": edge[1]} for edge in EDGES.values()]}))
        (self.provider / "steps.json").write_text(json.dumps([
            {"id": edge[2], "actor": "program"} for edge in EDGES.values()]))
        self.pins = {relative: self.sha(self.provider / relative) for relative in REQUIRED_PROVIDER_FILES}
        self.grant = {"granted": True, "purposes": list(EDGES), "sha256": "b" * 64, "tokenBudget": 128000}
        (self.workspace / "grant.json").write_text(json.dumps(self.grant))
        self.mode("success")
        (self.workspace / "reference.png").write_bytes(b"offline reference")
        self.context = {"kind": "img2.route-context", "version": 1, "group": "routing",
                        "subject": {"runId": "run-1", "objectId": "object-1", "revision": 1, "passId": "geometry"},
                        "bindings": [{"role": "reference", "path": "reference.png",
                                      "sha256": self.sha(self.workspace / "reference.png")}],
                        "candidates": [{"id": "a"}, {"id": "b"}], "observations": {"partCount": 2},
                        "requirements": {"criticalFeatures": [], "requiredViews": []},
                        "policyVersion": "policy-v1", "questionSetVersion": "route-questions-v1",
                        "requestedModel": "jev-1.14.0"}
        (self.workspace / "context.json").write_text(json.dumps(self.context))
        (self.workspace / "assets.json").write_text("{}")
        self.secret = "fixture-private-api-key-not-real"
        self.env = patch.dict(os.environ, {"TYPESAFE_API_KEY": self.secret})
        self.env.start()
        self.addCleanup(self.env.stop)

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def mode(self, mode):
        (self.workspace / "mode.json").write_text(json.dumps(mode))

    def commands(self):
        path = self.workspace / "spawned.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def projection(self):
        result = copy.deepcopy(self.context)
        result["bindings"] = [{"role": b["role"], "sha256": b["sha256"]} for b in result["bindings"]]
        return result

    def bridge(self, **overrides):
        inventory_digest = canonical_digest({"root": str(self.provider), "resolvedSha": "a" * 40, "files": self.pins})
        args = {"provider_root": self.provider, "resolved_sha": "a" * 40, "provider_files": self.pins,
                "entrypoint": "tools/jev.py", "executable": Path(sys.executable).resolve(),
                "edge": EDGES["routing"][:2], "step_id": EDGES["routing"][2],
                "context": self.context, "context_path": "context.json",
                "context_sha256": self.sha(self.workspace / "context.json"),
                "assets_path": "assets.json", "assets_sha256": self.sha(self.workspace / "assets.json"),
                "consent": lambda: copy.deepcopy(self.grant), "timeout": 3,
                "attest_contract": lambda: {"inventoryDigest": inventory_digest,
                                             "consentPolicyVersion": "jev-disclosure-v4",
                                             "durableSharedReservationTokens": 64000}}
        args.update(overrides)
        return JevCliBridge(self.workspace, **args)

    def assert_reason(self, reason, callback):
        with self.assertRaises(DecisionError) as caught:
            callback()
        self.assertEqual(reason, caught.exception.reason_code)
        self.assertNotIn(self.secret, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_success_preserves_provider_digest_and_only_returns_advisory(self):
        projection = self.projection()
        response = self.bridge()(projection)
        self.assertEqual(response["contextDigest"], canonical_digest(projection))
        self.assertNotEqual(response["contextDigest"], canonical_digest(self.context))
        self.assertEqual(response["group"], "routing")
        self.assertNotIn(self.secret, json.dumps(response))
        self.assertNotIn("diagnostics", response)
        commands = self.commands()
        self.assertEqual([args[0] for args in commands], ["consent", "decision-routing"])
        self.assertNotIn(self.secret, json.dumps(commands))
        self.assertEqual(projection, self.projection())

    def test_exact_command_mapping_for_all_four_groups(self):
        questions = {"routing": "route-questions-v1", "build-strategy": "strategy-questions-v1",
                     "quality-review": "quality-review-questions-v1", "repair": "repair-decision-questions-v1"}
        for group, edge in EDGES.items():
            with self.subTest(group=group):
                self.context.update(kind=edge[0], group=group, questionSetVersion=questions[group])
                (self.workspace / "context.json").write_text(json.dumps(self.context))
                answer = self.bridge(edge=edge[:2], step_id=edge[2])(self.projection())
                self.assertEqual(answer["group"], group)
                self.assertEqual(self.commands()[-1][0], "decision-" + group)

    def test_direct_pinned_executable_and_shell_metacharacters_in_path(self):
        script = self.provider / "tools/jev.py"
        script.write_text("#!" + str(Path(sys.executable).resolve()) + "\n" + STUB)
        script.chmod(0o700)
        self.pins["tools/jev.py"] = self.sha(script)
        name = "context;not-a-command.json"
        (self.workspace / name).write_text(json.dumps(self.context))
        answer = self.bridge(executable=None, context_path=name,
                             context_sha256=self.sha(self.workspace / name))(self.projection())
        self.assertEqual(answer["group"], "routing")
        self.assertIn(name, self.commands()[-1])
        self.assertFalse((self.workspace / "not-a-command.json").exists())

    def test_revocation_after_read_only_probe_prevents_paid_spawn(self):
        calls = []
        def consent():
            calls.append(None)
            grant = copy.deepcopy(self.grant)
            if len(calls) > 1:
                grant["granted"] = False
            return grant
        self.assert_reason("consent-required", lambda: self.bridge(consent=consent)(self.projection()))
        self.assertEqual([args[0] for args in self.commands()], ["consent"])

    def test_controller_callback_integration_keeps_jev_primary(self):
        controller = DecisionController(self.workspace, "run-1")
        def forbidden_fallback(_):
            self.fail("answering Jev must never invoke fallback")
        record = controller.consult(self.context, provider=self.bridge(), fallback=forbidden_fallback,
                                    availability=lambda: {"status": "present"},
                                    consent=lambda: copy.deepcopy(self.grant))
        self.assertEqual(record["status"], "answered")
        self.assertEqual(record["decider"], "jev:jev-1.14.0")

    def test_uncertainty_is_returned_without_model_override(self):
        self.mode("uncertain")
        response = self.bridge()(self.projection())
        self.assertEqual(response["answers"]["selection"]["confidence"], .4)
        self.assertEqual(response["model"], "jev-1.14.0")

    def test_no_live_grant_means_no_spawn(self):
        bridge = self.bridge()
        self.grant["granted"] = False
        self.assert_reason("consent-required", lambda: bridge(self.projection()))
        self.assertEqual(self.commands(), [])
        self.grant["granted"] = True
        self.grant["purposes"] = ["routing"]
        self.assert_reason("consent-required", lambda: bridge(self.projection()))
        self.assertEqual(self.commands(), [])

    def test_missing_contract_attestation_and_budget_fail_before_spawn(self):
        self.assert_reason("provider-contract-unproven", lambda: self.bridge(attest_contract=None)(self.projection()))
        self.grant["tokenBudget"] = 63999
        self.assert_reason("budget-exhausted", lambda: self.bridge()(self.projection()))
        self.assertEqual(self.commands(), [])

    def test_cli_grant_must_match_before_paid_spawn(self):
        for mode, reason in (("cli-no-consent", "consent-required"), ("cli-stale-consent", "consent-changed")):
            with self.subTest(mode=mode):
                self.mode(mode)
                self.assert_reason(reason, lambda: self.bridge()(self.projection()))
        self.assertTrue(all(args[0] == "consent" for args in self.commands()))

    def test_projection_must_equal_original_with_only_binding_paths_removed(self):
        for field, value in (("requestedModel", "other"), ("observations", {}), ("candidates", [{"id": "x"}])):
            with self.subTest(field=field):
                projected = self.projection()
                projected[field] = value
                self.assert_reason("context-fault", lambda: self.bridge()(projected))
        self.assert_reason("context-fault", lambda: self.bridge()(self.context))
        self.assertEqual(self.commands(), [])

    def test_typed_edge_step_mapping_cannot_be_substituted(self):
        self.assert_reason("capability-unavailable", lambda: self.bridge(step_id="jev-recommend"))
        self.assert_reason("capability-unavailable", lambda: self.bridge(edge=EDGES["repair"][:2]))
        self.assertEqual(self.commands(), [])

    def test_foreign_advisory_fields_and_digest_are_rejected_without_rewriting(self):
        for mode, reason in (("foreign-group", "group-mismatch"), ("foreign-subject", "subject-mismatch"),
                             ("foreign-digest", "context-digest-mismatch"), ("wrong-model", "model-mismatch")):
            with self.subTest(mode=mode):
                self.mode(mode)
                self.assert_reason(reason, lambda: self.bridge()(self.projection()))

    def test_bounded_timeout_and_stdout(self):
        self.mode("timeout")
        self.assert_reason("timeout", lambda: self.bridge(timeout=.1)(self.projection()))
        self.mode("large")
        self.assert_reason("malformed", lambda: self.bridge()(self.projection()))

    def test_auth_and_transport_reasons_are_explicit_and_secret_safe(self):
        for reason in ("credential-missing", "credential-rejected", "credential-storage-error", "timeout"):
            with self.subTest(reason=reason):
                self.mode("error:" + reason)
                self.assert_reason(reason, lambda: self.bridge()(self.projection()))
        self.mode("error:" + self.secret)
        self.assert_reason("provider-error", lambda: self.bridge()(self.projection()))

    def test_bad_stdout_duplicates_and_secret_answers_never_escape(self):
        for mode in ("bad-stdout", "duplicate", "secret-answer"):
            with self.subTest(mode=mode):
                self.mode(mode)
                self.assert_reason("malformed", lambda: self.bridge()(self.projection()))
        self.mode("secret-model")
        self.assert_reason("model-mismatch", lambda: self.bridge()(self.projection()))

    def test_context_assets_and_bound_evidence_need_current_hashes(self):
        for relative in ("context.json", "assets.json", "reference.png"):
            with self.subTest(relative=relative):
                bridge = self.bridge()
                path = self.workspace / relative
                original = path.read_bytes()
                path.write_bytes(b"changed")
                self.assert_reason("stale-evidence", lambda: bridge(self.projection()))
                path.write_bytes(original)
        self.assertEqual(self.commands(), [])

    def test_symlinks_and_escaping_context_or_assets_fail_without_spawn(self):
        for key in ("context_path", "assets_path"):
            with self.subTest(key=key):
                self.assert_reason("unsafe-path", lambda: self.bridge(**{key: "../escape.json"}))
        outside = self.root / "outside.json"
        outside.write_text("{}")
        (self.workspace / "link.json").symlink_to(outside)
        self.assert_reason("unsafe-path", lambda: self.bridge(assets_path="link.json", assets_sha256=self.sha(outside))(self.projection()))
        self.assertEqual(self.commands(), [])

    def test_incomplete_or_unattested_inventory_cannot_enable_cli(self):
        pins = copy.deepcopy(self.pins)
        del pins["tools/jev_decision_transport.py"]
        self.assert_reason("provider-unpinned", lambda: self.bridge(provider_files=pins))
        self.assert_reason("provider-contract-unproven",
                           lambda: self.bridge(attest_contract=lambda: {"durableSharedReservationTokens": 64000})(self.projection()))
        self.assertEqual(self.commands(), [])

    def test_pinned_dependency_changes_fail_before_spawn(self):
        bridge = self.bridge()
        (self.provider / "tools/jev_consent.py").write_text("# dirty worktree")
        self.assert_reason("stale-evidence", lambda: bridge(self.projection()))
        self.assertEqual(self.commands(), [])

    def test_post_dispatch_provider_and_evidence_changes_invalidate_response(self):
        self.mode("mutate-input")
        self.assert_reason("stale-evidence", lambda: self.bridge()(self.projection()))
        (self.workspace / "reference.png").write_bytes(b"offline reference")
        self.mode("mutate-provider")
        self.assert_reason("stale-evidence", lambda: self.bridge()(self.projection()))

    def test_no_plugin_imports_or_global_configuration_writes(self):
        config = self.root / "img2-home" / "config"
        config.mkdir(parents=True)
        sentinel = config / "jev-decision-consent.json"
        sentinel.write_text("user owned sentinel")
        before = set(sys.modules)
        with patch.dict(os.environ, {"IMG2_HOME": str(config.parent)}):
            self.bridge()(self.projection())
        self.assertEqual(sentinel.read_text(), "user owned sentinel")
        self.assertFalse(any(name.startswith("jev_") for name in set(sys.modules) - before))
        self.assertEqual(self.pins, {relative: self.sha(self.provider / relative) for relative in self.pins})


if __name__ == "__main__":
    unittest.main()
