"""Disposable fixtures and opt-in real-provider HTTP stubs; never live calls."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import random
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))
from decision_control import DecisionController, DecisionError, canonical_digest
from jev_cli_bridge import EDGES, JevCliBridge, REQUIRED_PROVIDER_FILES
from jev_provider_factory import JevProviderCallback, JevProviderFactory
from placement_fixtures import placement_records_fixture


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
        value.update(status="request-input", reasonCode=mode.split(":", 1)[1], reason=secret,
                     sendState="not-sent", chargedTokens=0)
        print(json.dumps(value))
        sys.exit(2)
    context = json.loads((workspace / args[args.index("--input") + 1]).read_text())
    def sha(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    full_digest = sha(context)
    context["bindings"] = [{"role": b["role"], "sha256": b["sha256"]} for b in context["bindings"]]
    digest = hashlib.sha256(json.dumps(context, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    answer = {"kind": "img2.decision-advisory", "version": 1, "group": context["group"],
              "subject": context["subject"], "contextDigest": digest,
              "model": context["requestedModel"], "answers": {
                  "selection": {"type": "choice", "choice": "a", "probabilities": {"a": .9, "b": .1}, "confidence": .8},
                  "anyFits": {"type": "noul", "noul": 1}},
              "usage": {"input_tokens": 10, "output_tokens": 2}}
    assets = json.loads((workspace / args[args.index("--assets") + 1]).read_text()) if "--assets" in args else {}
    request_digest = sha({"contextDigest":full_digest, "providerContextDigest":digest,
                          "consentSha256":grant["sha256"],
                          "assets":{name:image["sha256"] for name,image in assets.items()}})
    attempt = {"contextDigest":digest, "requestDigest":request_digest, "consentSha256":grant["sha256"],
               "invocationDigest":hashlib.sha256(args[args.index("--attempt-id") + 1].encode("ascii")).hexdigest()}
    if mode == "stale-nonce":
        attempt["invocationDigest"] = json.loads((workspace / "last-attempt.json").read_text())["invocationDigest"]
    else:
        (workspace / "last-attempt.json").write_text(json.dumps(attempt))
    value.update(sendState="charged", chargedTokens=12, attempt=attempt)
    if mode.startswith("proof-error:"):
        _, state, charged, reason = mode.split(":")
        value.update(status="request-input", reasonCode=reason, sendState=state, chargedTokens=int(charged))
        print(json.dumps(value))
        sys.exit(2)
    if mode.startswith("forged:"):
        field = mode.split(":", 1)[1]
        if field == "missing":
            del value["attempt"]
        elif field == "extra":
            value["attempt"]["extra"] = digest
        elif field == "bool":
            value["chargedTokens"] = True
        elif field == "accounting":
            value["chargedTokens"] = 11
        elif field == "state":
            value.update(sendState="not-sent", chargedTokens=12)
        elif field == "attempt-null":
            value["attempt"] = None
        elif field == "charged-missing":
            del value["chargedTokens"]
        elif field == "charged-float":
            value["chargedTokens"] = 12.0
        elif field == "charged-negative":
            value["chargedTokens"] = -1
        elif field == "unknown-state":
            value["sendState"] = "unknown"
        elif field == "possibly-sent":
            value.update(sendState="possibly-sent", chargedTokens=64000)
        elif field == "rejected":
            value.update(sendState="rejected", chargedTokens=0)
        else:
            value["attempt"][field] = "0" * 64
    if mode in {"charged-malformed", "cache-malformed"}:
        answer["answers"] = {}
    if mode in {"cache", "cache-malformed"}:
        value.update(sendState="not-sent", chargedTokens=0)
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
    if mode in {"cache", "cache-malformed"}:
        value["cached"] = True
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
                        "policyVersion": "policy-v1", "questionSetVersion": "route-questions-v3",
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
        questions = {"routing": "route-questions-v3", "build-strategy": "strategy-questions-v1",
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
        for relative in ("tools/jev_decision_transport.py", "tools/jev_character_material.py"):
            with self.subTest(relative=relative):
                pins = copy.deepcopy(self.pins)
                del pins[relative]
                self.assert_reason("provider-unpinned", lambda: self.bridge(provider_files=pins))
        self.assert_reason("provider-contract-unproven",
                           lambda: self.bridge(attest_contract=lambda: {"durableSharedReservationTokens": 64000})(self.projection()))
        self.assertEqual(self.commands(), [])

    def test_pinned_dependency_changes_fail_before_spawn(self):
        bridge = self.bridge()
        (self.provider / "tools/jev_consent.py").write_text("# dirty worktree")
        self.assert_reason("stale-evidence", lambda: bridge(self.projection()))
        self.assertEqual(self.commands(), [])

    def test_character_material_validator_tampering_blocks_cli_before_spawn(self):
        bridge = self.bridge()
        (self.provider / "tools/jev_character_material.py").write_text("# unreviewed validator\n")
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

    def placement(self):
        records = placement_records_fixture()
        facts = {("sizePrior" if role == "size-prior" else role): copy.deepcopy(record)
                 for role, record in records.items()}
        self.context.update(kind=EDGES["build-strategy"][0], group="build-strategy",
                            questionSetVersion="strategy-questions-v1", policyVersion="placement-policy-v1")
        self.context["subject"]["passId"] = "placement-size"
        self.context["requirements"]["placement"] = {"question": "size"}
        self.context["observations"] = {"placementFacts": facts}
        for candidate, scale in zip(self.context["candidates"], (1, 2)):
            candidate["value"] = [scale]*3
        records["camera"]["source"] = {"path": "/owned-fixture/local-only.json"}
        for role in ("inventory", "artifact", "camera", "depth", "support", "size-prior"):
            path = role + ".json"
            (self.workspace / path).write_text(json.dumps(records.get(role, {"fixture": role})))
            self.context["bindings"].append({"role": role, "path": path, "sha256": self.sha(self.workspace/path)})
        (self.workspace / "context.json").write_text(json.dumps(self.context))
        return {"edge": EDGES["build-strategy"][:2], "step_id": EDGES["build-strategy"][2]}

    def test_placement_facts_must_match_bound_records_before_any_cli_spawn(self):
        options = self.placement()
        self.context["observations"]["placementFacts"]["depth"]["cameraDepth"] = 4
        (self.workspace / "context.json").write_text(json.dumps(self.context))
        self.assert_reason("missing-placement-evidence", lambda: self.bridge(**options)(self.projection()))
        self.assertEqual(self.commands(), [])

    def test_incomplete_placement_record_refuses_before_cli_consent(self):
        options = self.placement()
        (self.workspace / "support.json").write_text('{"supportObjectId":null}')
        next(row for row in self.context["bindings"] if row["role"] == "support")["sha256"] = self.sha(self.workspace/"support.json")
        (self.workspace / "context.json").write_text(json.dumps(self.context))
        self.assert_reason("missing-placement-evidence", lambda: self.bridge(**options)(self.projection()))
        self.assertEqual(self.commands(), [])

    def test_complete_placement_records_allow_only_projected_facts(self):
        options = self.placement()
        result = self.bridge(**options)(self.projection())
        self.assertEqual(result["answers"]["selection"]["choice"], "a")
        self.assertNotIn("/owned-fixture", json.dumps(self.projection()))
        self.assertEqual([args[0] for args in self.commands()], ["consent", "decision-build-strategy"])

    def test_local_and_consent_errors_cannot_enable_model_fallback_or_release_by_reason(self):
        self.grant["tokenBudget"] = 640000
        (self.workspace / "grant.json").write_text(json.dumps(self.grant))
        controller = DecisionController(self.workspace, "run-1")
        fallback_calls = []
        def fallback(context):
            fallback_calls.append(context)
            raise AssertionError("local/consent failure is not a model outage")
        reasons = ("invalid-credential", "request-too-large", "credential-in-context",
                   "missing-placement-evidence", "consent-busy")
        for index, reason in enumerate(reasons):
            self.context["subject"]["objectId"] = "object-" + str(index)
            (self.workspace / "context.json").write_text(json.dumps(self.context))
            self.mode("error:" + reason)
            record = controller.consult(self.context, provider=self.bridge(), fallback=fallback,
                availability=lambda: {"status": "present"}, consent=lambda: copy.deepcopy(self.grant))
            self.assertEqual((record["status"], record["reasonCode"]), ("request-input", reason))
        self.assertEqual(fallback_calls, [])
        failures = [json.loads(line)["data"] for line in controller.journal_path.read_text().splitlines()
                    if json.loads(line)["event"] == "attempt-failure"]
        self.assertEqual(len(failures), len(reasons))
        self.assertTrue(all(row["tokens"] == 0 and row["release"] == 0 for row in failures))

    def trusted_callback(self, controller):
        class Factory:
            def __call__(inner, context):
                return self.bridge()
        factory = Factory()
        factory.discovery = type("Discovery", (), {"controller": controller})()
        return JevProviderCallback(factory, self.context)

    def consult_trusted(self, controller, *, fallback=None, callback=None):
        return controller.consult(self.context, provider=callback or self.trusted_callback(controller),
            fallback=fallback, availability=lambda: {"status": "present"},
            consent=lambda: copy.deepcopy(self.grant))

    def journal_events(self, controller, event):
        return [row["data"] for line in controller.journal_path.read_text().splitlines()
                if (row := json.loads(line))["event"] == event]

    def test_bound_unsent_releases_budget_and_definite_rejection_allows_fresh_auth_retry(self):
        controller = DecisionController(self.workspace, "run-1")
        self.grant["tokenBudget"] = 64012
        (self.workspace / "grant.json").write_text(json.dumps(self.grant))
        self.consult_trusted(controller)
        self.context["subject"]["objectId"] = "object-2"
        (self.workspace / "context.json").write_text(json.dumps(self.context))
        for mode, expected in (
                ("proof-error:not-sent:0:invalid-credential", "invalid-credential"),
                ("proof-error:rejected:0:credential-rejected", "credential-rejected")):
            self.mode(mode)
            # The second refusal has a new identity; both share the run's actual spend.
            if expected == "credential-rejected":
                self.context["subject"]["objectId"] = "object-3"
                (self.workspace / "context.json").write_text(json.dumps(self.context))
            record = self.consult_trusted(controller)
            self.assertEqual(record["reasonCode"], expected)
        self.mode("success")
        record = self.consult_trusted(controller)
        self.assertEqual(record["status"], "answered")
        failures = self.journal_events(controller, "attempt-failure")
        self.assertEqual([(row["tokens"], row["release"]) for row in failures], [(0, 64000)] * 2)
        self.assertEqual(sum(row["tokens"] for row in self.journal_events(controller, "attempt-result")), 24)

    def test_billed_malformed_advisory_records_debit_and_releases_unused_reservation(self):
        controller = DecisionController(self.workspace, "run-1")
        self.grant["tokenBudget"] = 64012
        (self.workspace / "grant.json").write_text(json.dumps(self.grant))
        self.mode("charged-malformed")
        record = self.consult_trusted(controller)
        self.assertEqual(record["reasonCode"], "malformed")
        failure = self.journal_events(controller, "attempt-failure")[-1]
        self.assertEqual((failure["tokens"], failure["release"]), (12, 64000))
        self.context["subject"]["objectId"] = "object-2"
        (self.workspace / "context.json").write_text(json.dumps(self.context))
        self.mode("success")
        self.assertEqual(self.consult_trusted(controller)["status"], "answered")

    def test_bound_ambiguous_and_unbound_errors_retain_reservation_without_fallback(self):
        for mode in ("proof-error:possibly-sent:64000:timeout", "error:credential-rejected"):
            with self.subTest(mode=mode):
                controller = DecisionController(self.workspace, "run-1")
                self.context["subject"]["objectId"] = mode.split(":")[0]
                (self.workspace / "context.json").write_text(json.dumps(self.context))
                self.mode(mode)
                calls = []
                record = self.consult_trusted(controller, fallback=lambda ctx: calls.append(ctx))
                self.assertEqual(record["status"], "request-input")
                failure = self.journal_events(controller, "attempt-failure")[-1]
                self.assertEqual((failure["tokens"], failure["release"]), (0, 0))
                self.assertIs(failure["fallbackAuthorized"], False)
                self.assertEqual(calls, [])
                if mode.startswith("proof-error"):
                    commands = len(self.commands())
                    self.consult_trusted(controller, fallback=lambda ctx: calls.append(ctx))
                    self.assertEqual(len(self.commands()), commands)
                    self.assertEqual(calls, [])

    def test_forged_or_missing_proof_never_refunds_or_authorizes_fallback(self):
        self.grant["tokenBudget"] = 1280000
        (self.workspace / "grant.json").write_text(json.dumps(self.grant))
        controller = DecisionController(self.workspace, "run-1")
        fields = ("contextDigest", "requestDigest", "consentSha256", "invocationDigest",
                  "missing", "extra", "bool", "accounting", "state", "attempt-null",
                  "charged-missing", "charged-float", "charged-negative", "unknown-state",
                  "possibly-sent", "rejected")
        for field in fields:
            with self.subTest(field=field):
                self.context["subject"]["objectId"] = field
                (self.workspace / "context.json").write_text(json.dumps(self.context))
                self.mode("forged:" + field)
                calls = []
                record = self.consult_trusted(controller, fallback=lambda ctx: calls.append(ctx))
                self.assertEqual((record["status"], record["reasonCode"]), ("request-input", "malformed"))
                failure = self.journal_events(controller, "attempt-failure")[-1]
                self.assertEqual((failure["tokens"], failure["release"]), (0, 0))
                self.assertEqual(calls, [])

    def test_verified_overrun_keeps_actual_debit_and_never_falls_back(self):
        controller = DecisionController(self.workspace, "run-1")
        self.mode("proof-error:charged:64001:provider-token-bound-exceeded")
        calls = []
        record = self.consult_trusted(controller, fallback=lambda ctx: calls.append(ctx))
        self.assertEqual((record["status"], record["reasonCode"]), ("request-input", "budget-overrun"))
        failure = self.journal_events(controller, "attempt-failure")[-1]
        self.assertEqual((failure["tokens"], failure["release"]), (64001, 64000))
        self.assertEqual(calls, [])

    def test_cache_hit_has_zero_new_debit_without_rewriting_advisory_usage(self):
        controller = DecisionController(self.workspace, "run-1")
        self.consult_trusted(controller)
        self.context["observations"]["partCount"] = 3
        (self.workspace / "context.json").write_text(json.dumps(self.context))
        self.consult_trusted(controller)
        controller = DecisionController(self.workspace, "run-1")  # Rebuild settlement authority from the journal.
        self.context["observations"]["partCount"] = 2
        (self.workspace / "context.json").write_text(json.dumps(self.context))
        self.mode("cache")
        self.assertEqual(len([args for args in self.commands() if args[0] == "decision-routing"]), 2)
        record = self.consult_trusted(controller)
        self.assertEqual(record["status"], "answered")
        self.assertEqual(record["usage"], {"input_tokens": 10, "output_tokens": 2})
        results = self.journal_events(controller, "attempt-result")
        self.assertEqual([(row["tokens"], row["release"]) for row in results],
                         [(12, 64000), (12, 64000), (0, 64000)])
        self.assertEqual(len([args for args in self.commands() if args[0] == "decision-routing"]), 3)
        self.assertIs(results[-1]["sendEvidence"]["cached"], True)
        commands = len(self.commands())
        self.consult_trusted(controller)
        self.assertEqual(len(self.commands()), commands)

    def test_cold_cache_refuses_without_debit_or_fallback_and_stays_conservative_after_restart(self):
        self.grant["tokenBudget"] = 64000
        (self.workspace / "grant.json").write_text(json.dumps(self.grant))
        controller = DecisionController(self.workspace, "run-1")
        self.mode("cache")
        calls = []
        record = self.consult_trusted(controller, fallback=lambda ctx: calls.append(ctx))
        self.assertEqual((record["status"], record["reasonCode"]), ("request-input", "ambiguous-paid-attempt"))
        failure = self.journal_events(controller, "attempt-failure")[-1]
        self.assertEqual((failure["tokens"], failure["release"]), (0, 0))
        self.assertEqual(calls, [])
        controller = DecisionController(self.workspace, "run-1")
        commands = len(self.commands())
        self.consult_trusted(controller, fallback=lambda ctx: calls.append(ctx))
        self.assertEqual(len(self.commands()), commands)
        self.context["subject"]["objectId"] = "other-object"
        (self.workspace / "context.json").write_text(json.dumps(self.context))
        self.mode("success")
        self.assertEqual(self.consult_trusted(controller)["reasonCode"], "budget-exhausted")
        self.assertEqual(len(self.commands()), commands)
        self.assertEqual(calls, [])

    def test_resumed_pending_attempt_cannot_consume_unsettled_cache_or_erase_old_reservation(self):
        controller = DecisionController(self.workspace, "run-1")
        class Interrupted(BaseException):
            pass
        real_call = JevCliBridge.__call__
        def interrupt(bridge, projected):
            real_call(bridge, projected)
            raise Interrupted()
        with patch.object(JevCliBridge, "__call__", new=interrupt), self.assertRaises(Interrupted):
            self.consult_trusted(controller)
        controller = DecisionController(self.workspace, "run-1")
        self.mode("cache")
        calls = []
        record = self.consult_trusted(controller, fallback=lambda ctx: calls.append(ctx))
        self.assertEqual((record["status"], record["reasonCode"]), ("request-input", "ambiguous-paid-attempt"))
        failures = self.journal_events(controller, "attempt-failure")
        self.assertEqual([(row["tokens"], row["release"]) for row in failures], [(0, 0)])
        self.context["subject"]["objectId"] = "next-object"
        (self.workspace / "context.json").write_text(json.dumps(self.context))
        self.mode("success")
        commands = len(self.commands())
        self.assertEqual(self.consult_trusted(controller)["reasonCode"], "budget-exhausted")
        self.assertEqual(len(self.commands()), commands)
        self.assertEqual(calls, [])

    def test_two_interrupted_bridge_calls_cannot_authorize_fallback_after_restart(self):
        self.grant["tokenBudget"] = 256000
        (self.workspace / "grant.json").write_text(json.dumps(self.grant))
        class Interrupted(BaseException):
            pass
        real_call = JevCliBridge.__call__
        def interrupt(bridge, projected):
            real_call(bridge, projected)
            raise Interrupted()
        for _ in range(2):
            controller = DecisionController(self.workspace, "run-1")
            with patch.object(JevCliBridge, "__call__", new=interrupt), self.assertRaises(Interrupted):
                self.consult_trusted(controller)
        controller = DecisionController(self.workspace, "run-1")
        calls = []
        commands = len(self.commands())
        record = self.consult_trusted(controller, fallback=lambda ctx: calls.append(ctx))
        self.assertEqual((record["status"], record["reasonCode"]), ("request-input", "ambiguous-paid-attempt"))
        self.assertEqual(calls, [])
        self.assertEqual(len(self.commands()), commands)
        self.assertEqual(len([args for args in self.commands() if args[0].startswith("decision-")]), 2)
        self.assertEqual(len(self.journal_events(controller, "attempt-pending")), 2)
        self.assertEqual(self.journal_events(controller, "attempt-result"), [])
        self.assertEqual(self.journal_events(controller, "attempt-failure"), [])
        self.assertFalse(any(row.get("fallbackAuthorized") is True
                             for row in self.journal_events(controller, "probe")))
        controller = DecisionController(self.workspace, "run-1")
        self.consult_trusted(controller, fallback=lambda ctx: calls.append(ctx))
        self.assertEqual(calls, [])
        self.assertEqual(len(self.commands()), commands)

    def test_recovered_pending_attempt_blocks_availability_outage_fallback(self):
        self.grant["tokenBudget"] = 192000
        (self.workspace / "grant.json").write_text(json.dumps(self.grant))
        controller = DecisionController(self.workspace, "run-1")
        class Interrupted(BaseException):
            pass
        real_call = JevCliBridge.__call__
        def interrupt(bridge, projected):
            real_call(bridge, projected)
            raise Interrupted()
        with patch.object(JevCliBridge, "__call__", new=interrupt), self.assertRaises(Interrupted):
            self.consult_trusted(controller)
        calls = []
        commands = len(self.commands())
        for _ in range(2):
            controller = DecisionController(self.workspace, "run-1")
            record = controller.consult(self.context, provider=self.trusted_callback(controller),
                fallback=lambda ctx: calls.append(ctx), availability=lambda: {"status": "not-installed"},
                consent=lambda: copy.deepcopy(self.grant))
            self.assertEqual((record["status"], record["reasonCode"]), ("request-input", "ambiguous-paid-attempt"))
            self.assertEqual(calls, [])
            self.assertEqual(len(self.commands()), commands)
        self.assertEqual(len(self.journal_events(controller, "attempt-pending")), 1)
        self.assertEqual(self.journal_events(controller, "attempt-result"), [])
        self.assertEqual(self.journal_events(controller, "attempt-failure"), [])
        self.assertFalse(any(row.get("fallbackAuthorized") is True
                             for row in self.journal_events(controller, "probe")))

    def test_verified_charged_malformed_outcome_still_allows_existing_model_fallback(self):
        controller = DecisionController(self.workspace, "run-1")
        self.mode("charged-malformed")
        calls = []
        def fallback(projected):
            calls.append(projected)
            return {"kind": "img2.decision-advisory", "version": 1, "group": projected["group"],
                    "subject": copy.deepcopy(projected["subject"]), "contextDigest": canonical_digest(projected),
                    "model": "host-model", "answers": {
                        "selection": {"type": "choice", "choice": "a", "probabilities": {"a": .9, "b": .1},
                                      "confidence": .8}, "anyFits": {"type": "noul", "noul": 1}},
                    "usage": {"input_tokens": 3, "output_tokens": 1}}
        record = self.consult_trusted(controller, fallback=fallback)
        self.assertEqual((record["status"], record["decider"]), ("answered", "model:host-model"))
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(row.get("fallbackAuthorized") is True
                            for row in self.journal_events(controller, "probe")))

    def test_legacy_unbound_result_is_not_a_verified_cache_settlement(self):
        self.grant["tokenBudget"] = 64012
        (self.workspace / "grant.json").write_text(json.dumps(self.grant))
        controller = DecisionController(self.workspace, "run-1")
        record = controller.consult(self.context, provider=lambda projected: self.bridge()(projected),
            availability=lambda: {"status": "present"}, consent=lambda: copy.deepcopy(self.grant))
        self.assertEqual(record["status"], "answered")
        controller = DecisionController(self.workspace, "run-1")
        self.mode("cache")
        calls = []
        record = self.consult_trusted(controller, fallback=lambda ctx: calls.append(ctx))
        self.assertEqual((record["status"], record["reasonCode"]), ("request-input", "ambiguous-paid-attempt"))
        self.assertEqual(sum(row["tokens"] for row in self.journal_events(controller, "attempt-result")), 12)
        failure = self.journal_events(controller, "attempt-failure")[-1]
        self.assertEqual((failure["tokens"], failure["release"]), (0, 0))
        self.assertEqual(calls, [])

    def test_cache_settlement_cannot_cross_request_subject_or_live_consent(self):
        self.grant["tokenBudget"] = 256000
        (self.workspace / "grant.json").write_text(json.dumps(self.grant))
        controller = DecisionController(self.workspace, "run-1")
        self.consult_trusted(controller)
        original = copy.deepcopy(self.context)
        self.mode("cache")
        for change in ("request", "subject", "consent"):
            with self.subTest(change=change):
                self.context = copy.deepcopy(original)
                if change == "request":
                    self.context["observations"]["partCount"] = 3
                elif change == "subject":
                    self.context["subject"]["objectId"] = "other-subject"
                else:
                    self.grant["sha256"] = "c" * 64
                    (self.workspace / "grant.json").write_text(json.dumps(self.grant))
                (self.workspace / "context.json").write_text(json.dumps(self.context))
                calls = []
                record = self.consult_trusted(controller, fallback=lambda ctx: calls.append(ctx))
                self.assertEqual((record["status"], record["reasonCode"]), ("request-input", "ambiguous-paid-attempt"))
                failure = self.journal_events(controller, "attempt-failure")[-1]
                self.assertEqual((failure["tokens"], failure["release"]), (0, 0))
                self.assertEqual(calls, [])

    def test_trusted_jev_refuses_nonmatching_reservation_before_dispatch(self):
        controller = DecisionController(self.workspace, "run-1")
        for limit in (1, 128, 63999, 64001):
            with self.subTest(limit=limit):
                calls = []
                record = controller.consult(self.context, provider=self.trusted_callback(controller),
                    request_token_limit=limit, fallback=lambda ctx: calls.append(ctx),
                    availability=lambda: {"status": "present"}, consent=lambda: copy.deepcopy(self.grant))
                self.assertEqual((record["status"], record["reasonCode"]), ("request-input", "provider-contract-unproven"))
                self.assertEqual(calls, [])
        self.assertEqual(self.commands(), [])
        self.assertEqual(self.journal_events(controller, "attempt-pending"), [])

    def test_malformed_cold_cache_cannot_refund_its_unknown_historical_liability(self):
        controller = DecisionController(self.workspace, "run-1")
        self.mode("cache-malformed")
        calls = []
        record = self.consult_trusted(controller, fallback=lambda ctx: calls.append(ctx))
        self.assertEqual((record["status"], record["reasonCode"]), ("request-input", "ambiguous-paid-attempt"))
        failure = self.journal_events(controller, "attempt-failure")[-1]
        self.assertEqual((failure["tokens"], failure["release"]), (0, 0))
        self.assertEqual(calls, [])

    def test_evidence_is_detached_nonce_is_fresh_and_local_failure_clears_prior_proof(self):
        controller = DecisionController(self.workspace, "run-1")
        callback = self.trusted_callback(controller)
        callback(self.projection())
        evidence = callback.send_evidence(self.context, self.grant)
        self.assertEqual(evidence["chargedTokens"], 12)
        evidence["attempt"]["invocationDigest"] = "f" * 64
        self.assertNotEqual(callback.send_evidence(self.context, self.grant), evidence)
        self.mode("stale-nonce")
        self.assert_reason("malformed", lambda: callback(self.projection()))
        self.assertIsNone(callback.send_evidence(self.context, self.grant))
        commands = [args for args in self.commands() if args[0].startswith("decision-")]
        nonces = [args[args.index("--attempt-id") + 1] for args in commands]
        self.assertEqual(len(set(nonces)), 2)
        self.assertTrue(all(len(nonce) == 32 for nonce in nonces))
        self.mode("success")
        callback(self.projection())
        (self.workspace / "reference.png").write_bytes(b"changed")
        self.assert_reason("stale-evidence", lambda: callback(self.projection()))
        self.assertIsNone(callback.send_evidence(self.context, self.grant))

    def test_post_dispatch_package_or_source_tampering_never_publishes_proof(self):
        controller = DecisionController(self.workspace, "run-1")
        for index, mode in enumerate(("mutate-input", "mutate-provider")):
            with self.subTest(mode=mode):
                (self.workspace / "reference.png").write_bytes(b"offline reference")
                self.context["subject"]["objectId"] = "tamper-" + str(index)
                (self.workspace / "context.json").write_text(json.dumps(self.context))
                self.mode(mode)
                calls = []
                record = self.consult_trusted(controller, fallback=lambda ctx: calls.append(ctx))
                self.assertEqual(record["reasonCode"], "stale-evidence")
                failure = self.journal_events(controller, "attempt-failure")[-1]
                self.assertEqual((failure["tokens"], failure["release"]), (0, 0))
                self.assertEqual(calls, [])

    def test_generic_callable_cannot_forge_typed_refund_evidence(self):
        controller = DecisionController(self.workspace, "run-1")
        class Forged:
            def __call__(self, context):
                raise DecisionError("malformed")
            def send_evidence(self, context, grant):
                return {"sendState": "not-sent", "chargedTokens": 0}
        class ForgedSubclass(JevProviderCallback):
            __call__ = Forged.__call__
            send_evidence = Forged.send_evidence
        for index, callback in enumerate((Forged(), object.__new__(ForgedSubclass))):
            with self.subTest(callback=type(callback).__name__):
                self.context["subject"]["objectId"] = "forged-" + str(index)
                (self.workspace / "context.json").write_text(json.dumps(self.context))
                record = controller.consult(self.context, provider=callback,
                    availability=lambda: {"status": "present"}, consent=lambda: copy.deepcopy(self.grant))
                self.assertEqual(record["reasonCode"], "malformed")
                failure = self.journal_events(controller, "attempt-failure")[-1]
                self.assertEqual((failure["tokens"], failure["release"]), (0, 0))

    def test_image_manifest_exception_does_not_relax_context_or_metadata_limits(self):
        from jev_cli_bridge import _json
        encoded = "A" * 20000
        image = {"format": "png", "sha256": "a" * 64, "data_base64": encoded}
        raw = json.dumps({"reference-image": image})
        self.assert_reason("malformed", lambda: _json(raw))
        self.assertEqual(_json(raw, asset_manifest=True)["reference-image"]["data_base64"], encoded)
        image["metadata"] = "x" * 16385
        self.assert_reason("malformed", lambda: _json(json.dumps({"reference-image": image}), asset_manifest=True))
        image.pop("metadata")
        image["width"] = float("nan")
        self.assert_reason("malformed", lambda: _json(json.dumps({"reference-image": image}), asset_manifest=True))
        image.pop("width")
        image["data_base64"] = ["not-a-string"]
        self.assert_reason("malformed", lambda: _json(json.dumps({"reference-image": image}), asset_manifest=True))
        image["data_base64"] = encoded
        duplicate = '{"reference-image":' + json.dumps(image) + ',"reference-image":{}}'
        self.assert_reason("malformed", lambda: _json(duplicate, asset_manifest=True))


@unittest.skipUnless(os.environ.get("IMG2_JEV_DIR") and os.environ.get("IMG2_HARNESS_DIR")
                     and shutil.which("git"),
                     "real-provider contract requires IMG2_JEV_DIR, IMG2_HARNESS_DIR and Git")
class JevCliBridgeRealProviderTests(unittest.TestCase):
    """Run unchanged Jev CLI/provider/worker code against synthetic HTTPS bytes.

    Only the socket boundary is stubbed. An isolated worker bootstrap installs
    the same socket stub because Python -I deliberately ignores sitecustomize.
    All grants, credentials, images and journals belong to a temporary fixture.
    """

    def setUp(self):
        source = Path(os.environ["IMG2_JEV_DIR"]).resolve(strict=True)
        harness = Path(os.environ["IMG2_HARNESS_DIR"]).resolve(strict=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="core-jev-contract-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.provider = self.root / "provider"
        self.provider.mkdir()
        for relative in REQUIRED_PROVIDER_FILES:
            target = self.provider / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)
        home, support = self.root / "home", self.root / "support"
        home.mkdir()
        support.mkdir()
        (home / "harness").symlink_to(harness, target_is_directory=True)
        (support / "offline_http.py").write_text(self.HTTP_STUB)
        (support / "sitecustomize.py").write_text("from offline_http import install\ninstall()\n")
        self.posts = self.root / "http-posts.jsonl"
        self.response = self.root / "http-response.json"
        environment = {
            "PATH": os.defpath, "HOME": str(home), "IMG2_HOME": str(home),
            "PYTHONPATH": str(support), "PYTHONDONTWRITEBYTECODE": "1",
            "TYPESAFE_API_KEY": "offline-fixture-credential-not-a-real-key",
            "CORE_JEV_HTTP_RESPONSE": str(self.response), "CORE_JEV_HTTP_POSTS": str(self.posts),
            "CORE_JEV_HTTP_SUPPORT": str(support),
            "CORE_JEV_HTTP_WORKER": str(self.provider / "tools/jev_decision_transport.py"),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Offline Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Offline Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        }
        self.environment = patch.dict(os.environ, environment, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        for arguments in (["init", "--quiet"], ["add", "."],
                          ["commit", "--quiet", "-m", "Offline real-provider fixture"]):
            self.git(*arguments)
        release = {"resolvedSha": self.git("rev-parse", "HEAD").decode().strip(),
                   "contractVersion": "jev-v4-four-decisions",
                   "files": {relative: hashlib.sha256((self.provider / relative).read_bytes()).hexdigest()
                             for relative in REQUIRED_PROVIDER_FILES}}
        self.controller = DecisionController(self.workspace, "run-1")
        steps = json.loads((self.provider / "steps.json").read_text())
        row = {"plugin": "jev", "dir": str(self.provider), "resolvedSha": release["resolvedSha"],
               "steps": [{"id": step["id"], "actor": step["actor"],
                          "argv": shlex.split(step["command"].replace("{plugin_dir}", str(self.provider)))}
                         for step in steps if step["id"] in {edge[2] for edge in EDGES.values()}]}
        discovery = SimpleNamespace(workspace=self.workspace, controller=self.controller, jev=row,
            availability=lambda: {"status": "present"},
            query=lambda edge: {"status": "answered", "providers": [copy.deepcopy(row)]})
        grant = self.command("consent", "authorize-decisions", "--run-id", "run-1",
                             "--token-budget", "128000", "--accept-image-disclosure", "--accept-paid-calls")
        self.grant = {key: grant[key] for key in ("granted", "purposes", "sha256", "tokenBudget")}
        self.factory = JevProviderFactory(discovery, approved_release=release, run_id="run-1",
                                         consent=lambda: copy.deepcopy(self.grant), timeout=15)
        # Metadata-free deterministic noisy RGB PNG: its prepared base64 exceeds
        # both the 16KiB context-string cap and the 2MiB context-document cap.
        def chunk(kind, data):
            return (struct.pack(">I", len(data)) + kind + data
                    + struct.pack(">I", zlib.crc32(kind + data)))
        pixels = random.Random(7).randbytes(800 * 800 * 3)
        rows = b"".join(b"\0" + pixels[index:index + 2400] for index in range(0, len(pixels), 2400))
        image = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 800, 800, 8, 2, 0, 0, 0))
                 + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
        (self.workspace / "reference.png").write_bytes(image)
        self.context = {
            "kind": "img2.route-context", "version": 1, "group": "routing",
            "subject": {"runId": "run-1", "objectId": "object-1", "revision": 1, "passId": "geometry"},
            "bindings": [{"role": "reference", "path": "reference.png",
                          "sha256": hashlib.sha256(image).hexdigest()}],
            "candidates": [{"id": "a"}, {"id": "b"}],
            "observations": {"images": [{"role": "reference", "asset": "reference-image"}]},
            "requirements": {"criticalFeatures": [], "requiredViews": []},
            "policyVersion": "policy-v1", "questionSetVersion": "route-questions-v3",
            "requestedModel": "jev-1.14.0"}

    def git(self, *arguments):
        return subprocess.run(["git", "-C", str(self.provider), "-c", "core.hooksPath=" + os.devnull,
                               "-c", "commit.gpgsign=false", *arguments], check=True,
                              capture_output=True, timeout=10).stdout

    def command(self, *arguments):
        result = subprocess.run([sys.executable, "-B", str(self.provider / "tools/jev.py"), *arguments,
                                 "--workspace", str(self.workspace)],
                                capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
        return json.loads(result.stdout)

    def consult(self, response):
        self.response.write_text(json.dumps(response))
        fallback_calls = []
        record = self.controller.consult(self.context, provider=self.factory.provider(self.context),
            fallback=lambda context: fallback_calls.append(context),
            availability=lambda: {"status": "present"}, consent=lambda: copy.deepcopy(self.grant))
        self.assertEqual(fallback_calls, [])
        self.assertEqual(self.posts.read_text().splitlines(), ['"POST"'])
        events = [json.loads(line) for line in self.controller.journal_path.read_text().splitlines()]
        results = [event["data"] for event in events if event["event"] == "attempt-result"]
        self.assertEqual(len(results), 1)
        settlement = results[0]
        self.assertEqual((settlement["tokens"], settlement["release"]), (2441, 64000))
        proof = settlement["sendEvidence"]
        self.assertEqual((proof["sendState"], proof["chargedTokens"], proof["cached"]), ("charged", 2441, False))
        self.assertIn("jevSettlement", settlement)
        ledgers = list((self.root / "home/config").glob("jev-decision-run-*.json"))
        self.assertEqual(len(ledgers), 1)
        attempt = json.loads(ledgers[0].read_text())["attempts"][proof["attempt"]["requestDigest"]]
        self.assertEqual((attempt["status"], attempt["chargedTokens"]), ("completed", 2441))
        manifests = list((self.workspace / ".img2threejs/route-inputs/jev-assets").glob("*.json"))
        prepared = json.loads(manifests[0].read_text())["reference-image"]
        self.assertGreater(len(prepared["data_base64"]), 2 * 1024 * 1024)
        self.assertEqual(record["usage"], response["usage"])
        self.assertEqual(record["answers"], response["answers"])
        return record

    def test_real_cli_large_prepared_image_accepts_decomposition_and_settles_both_journals(self):
        self.context["subject"]["passId"] = "decomposition"
        self.context["candidates"] = []
        item = {"id": "object-1", "label": "object", "approxBox": [0, 0, 1, 1]}
        self.context["requirements"]["decomposition"] = {
            "image": "reference-image", "objects": [item],
            "inventory": [{**item, "role": "subject"}]}
        # Same HTTP answer shape as the real paid localization; the real CLI,
        # not this test, emits advisory/attempt/send-state/charge envelope fields.
        record = self.consult({
            "model": "jev-1.14.0", "usage": {"input_tokens": 2370, "output_tokens": 71},
            "answers": {"missingObjects": {"type": "noul", "noul": .05},
                        "object:object-1:visible": {"type": "noul", "noul": .96},
                        "object:object-1:box": {"type": "box", "confidence": .78,
                                              "box": {"top": .33, "bottom": .78, "left": .01, "right": .82}}}})
        self.assertEqual((record["status"], record["reasonCode"]), ("answered", "answered"))

    def test_real_cli_large_prepared_image_preserves_uncertainty_without_fallback(self):
        record = self.consult({
            "model": "jev-1.14.0", "usage": {"input_tokens": 2370, "output_tokens": 71},
            "answers": {"selection": {"type": "choice", "choice": "a",
                                      "probabilities": {"a": .6, "b": .4}, "confidence": .4},
                        "anyFits": {"type": "noul", "noul": 1}}})
        self.assertEqual((record["status"], record["reasonCode"]), ("uncertain", "choice-uncertain"))

    HTTP_STUB = r'''
import io, json, os, socket, ssl, subprocess, sys
from pathlib import Path

class StubSocket:
    def __init__(self):
        body = Path(os.environ["CORE_JEV_HTTP_RESPONSE"]).read_bytes()
        self.response = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    def setsockopt(self, *args): pass
    def settimeout(self, *args): pass
    def close(self): pass
    def makefile(self, *args, **kwargs): return io.BytesIO(self.response)
    def sendall(self, data):
        if bytes(data).startswith(b"POST "):
            with open(os.environ["CORE_JEV_HTTP_POSTS"], "a") as stream:
                stream.write(json.dumps("POST") + "\n")

def install():
    def connect(address, *args, **kwargs):
        if address != ("api.typesafe.ai", 443):
            raise AssertionError("unexpected external socket")
        return StubSocket()
    def tls(self, connection, *args, **kwargs):
        if kwargs.get("server_hostname") != "api.typesafe.ai":
            raise AssertionError("unexpected external TLS host")
        return connection
    def deny(*args, **kwargs):
        raise AssertionError("unmocked socket is forbidden")
    socket.create_connection = connect
    socket.socket.connect = deny
    ssl.SSLContext.wrap_socket = tls
    original = subprocess.Popen
    def worker(argv, *args, **kwargs):
        target = os.environ["CORE_JEV_HTTP_WORKER"]
        if list(argv) == [sys.executable, "-I", target, "--worker"]:
            bootstrap = ("import runpy,sys;sys.path.insert(0," + repr(os.environ["CORE_JEV_HTTP_SUPPORT"])
                         + ");from offline_http import install;install();"
                         + "sys.argv=[sys.argv[1],'--worker'];runpy.run_path(sys.argv[0],run_name='__main__')")
            argv = [sys.executable, "-I", "-c", bootstrap, target]
        return original(argv, *args, **kwargs)
    subprocess.Popen = worker
'''


if __name__ == "__main__":
    unittest.main()
