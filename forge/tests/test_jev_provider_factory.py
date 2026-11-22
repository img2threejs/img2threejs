"""Offline temporary Git releases and isolated preparation fixtures; never network."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

FORGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORGE / "_shared"))
sys.path.insert(0, str(FORGE))
from decision_control import DecisionController, DecisionError, canonical_digest
from jev_cli_bridge import EDGES, JevCliBridge, REQUIRED_PROVIDER_FILES
from jev_provider_factory import JevProviderFactory, _image_bytes
from stage0_route.discovery import Discovery


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 280, 280, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff" * 840) * 280)) + chunk(b"IEND", b""))


CLI = r'''
import hashlib, json, pathlib, sys
args = sys.argv[1:]
workspace = pathlib.Path(args[args.index("--workspace") + 1])
with (workspace / "cli-spawns.jsonl").open("a") as marker:
    marker.write(json.dumps(args) + "\n")
grant = json.loads((workspace / "grant.json").read_text())
result = {"kind":"img2.jev-command", "version":1}
if args[0] == "consent":
    result.update(status="consent-updated", **grant)
else:
    context = json.loads((workspace / args[args.index("--input") + 1]).read_text())
    context["bindings"] = [{"role": b["role"], "sha256": b["sha256"]} for b in context["bindings"]]
    digest = hashlib.sha256(json.dumps(context, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode()).hexdigest()
    advisory = {"kind":"img2.decision-advisory", "version":1, "group":context["group"],
                "subject":context["subject"], "contextDigest":digest, "model":context["requestedModel"],
                "answers":{"selection":{"type":"choice", "choice":"a", "probabilities":{"a":.9,"b":.1},"confidence":.8},
                           "anyFits":{"type":"noul","noul":1}},
                "usage":{"input_tokens":10,"output_tokens":2}}
    result.update(status="answered", reasonCode="answered", consentSha256=grant["sha256"], advisory=advisory)
print(json.dumps(result))
'''

# This fixture is deliberately NOT real image normalization. It lets the tests
# exercise the actual isolated child and bounded envelope checks without Pillow.
HELPER = r'''
import base64, hashlib, pathlib

def prepare_image(path):
    data = pathlib.Path(path).read_bytes()
    return {"format":"png", "data_base64":base64.b64encode(data).decode("ascii"),
            "sha256":hashlib.sha256(data).hexdigest(),"width":280,"height":280,
            "transform":{"sourceWidth":280,"sourceHeight":280,"crop":[0,0,280,280],
                         "scaleX":1,"scaleY":1,"padX":0,"padY":0}}
'''


class FixtureDiscovery(Discovery):
    """Actual _pinned_file implementation; only offline discovery/auth are fixtures."""
    def __init__(self, controller, row, git_env):
        super().__init__(controller)
        self.jev, self.jev_status, self.git_env = row, "present", git_env
        self.status = "present"
        self.seen = []

    def availability(self):
        return {"status": self.status}

    def query(self, edge):
        return {"status": "answered", "providers": [copy.deepcopy(self.jev)]}

    def run(self, argv):
        self.seen.append(tuple(argv))
        if argv[0] != "git" or argv[3] != "show":
            raise AssertionError("fixture permits only read-only git show")
        result = subprocess.run(argv, capture_output=True, timeout=5, check=False,
                                shell=False, env=self.git_env)
        return result.returncode, result.stdout


@unittest.skipUnless(shutil.which("git"), "offline Git fixture requires git")
class JevProviderFactoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.provider_root = self.root / "reviewed provider"
        (self.provider_root / "tools").mkdir(parents=True)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.git_env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                            GIT_AUTHOR_NAME="Offline Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                            GIT_COMMITTER_NAME="Offline Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
        for key in list(self.git_env):
            if key.startswith("GIT_") and key not in {
                    "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                    "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"}:
                del self.git_env[key]
        for relative in REQUIRED_PROVIDER_FILES:
            (self.provider_root / relative).write_text("# explicitly reviewed fixture dependency\n")
        (self.provider_root / "tools/jev.py").write_text(CLI)
        (self.provider_root / "tools/jev_decision_images.py").write_text(HELPER)
        (self.provider_root / "plugin.json").write_text(json.dumps({
            "name": "jev", "capabilities": [{"from": edge[0], "to": edge[1]} for edge in EDGES.values()]}))
        declared, actual = [], []
        for group, edge in EDGES.items():
            template = ["python3", "{plugin_dir}/tools/jev.py", "decision-" + group,
                        "--workspace", "{workspace}", "--input", ".img2/artifacts/jev/contexts/" + group + ".json",
                        "--assets", ".img2/artifacts/jev/contexts/" + group + "-assets.json"]
            # Quote the provider placeholder because this fixture root has spaces.
            command = shlex.join(template)
            declared.append({"id": edge[2], "actor": "program", "command": command})
            actual.append({"id": edge[2], "actor": "program",
                           "argv": [part.replace("{plugin_dir}", str(self.provider_root)) for part in template]})
        (self.provider_root / "steps.json").write_text(json.dumps(declared))
        self.git("init", "--quiet")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "Explicit offline fixture release")
        sha = self.git("rev-parse", "HEAD").decode().strip()
        self.lock = {"resolvedSha": sha, "files": {
            relative: self.sha(self.provider_root / relative) for relative in REQUIRED_PROVIDER_FILES},
                     "contractVersion": "jev-v4-four-decisions"}
        self.controller = DecisionController(self.workspace, "run-1")
        row = {"plugin": "jev", "dir": str(self.provider_root), "resolvedSha": sha, "steps": actual}
        self.discovery = FixtureDiscovery(self.controller, row, self.git_env)
        self.grant = {"granted": True, "purposes": list(EDGES), "sha256": "b" * 64, "tokenBudget": 128000}
        (self.workspace / "grant.json").write_text(json.dumps(self.grant))
        (self.workspace / "reference.png").write_bytes(png())
        self.context = {"kind": "img2.route-context", "version": 1, "group": "routing",
                        "subject": {"runId": "run-1", "objectId": "object-1", "revision": 1, "passId": "geometry"},
                        "bindings": [{"role": "reference", "path": "reference.png",
                                      "sha256": self.sha(self.workspace / "reference.png")}],
                        "candidates": [{"id": "a"}, {"id": "b"}],
                        "observations": {"images": [{"role": "reference", "asset": "reference-image"}]},
                        "requirements": {"criticalFeatures": [], "requiredViews": []},
                        "policyVersion": "policy-v1", "questionSetVersion": "route-questions-v3",
                        "requestedModel": "jev-1.14.0"}

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.provider_root), "-c", "core.hooksPath=" + os.devnull,
                                 "-c", "commit.gpgsign=false", *args], env=self.git_env,
                                check=True, capture_output=True, timeout=10, shell=False)
        return result.stdout

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def factory(self, **overrides):
        args = {"approved_release": self.lock, "run_id": "run-1",
                "consent": lambda: copy.deepcopy(self.grant), "timeout": 5}
        args.update(overrides)
        return JevProviderFactory(self.discovery, **args)

    def projected(self, context=None):
        projected = copy.deepcopy(context or self.context)
        projected["bindings"] = [{"role": b["role"], "sha256": b["sha256"]} for b in projected["bindings"]]
        return projected

    def assert_reason(self, reason, callback):
        with self.assertRaises(DecisionError) as caught:
            callback()
        self.assertEqual(caught.exception.reason_code, reason)
        self.assertNotIn("data_base64", str(caught.exception))

    def test_prepares_immutable_bound_context_and_png_without_paid_spawn(self):
        bridge = self.factory()(self.context)
        self.assertIsInstance(bridge, JevCliBridge)
        self.assertEqual(json.loads((self.workspace / bridge.context_path).read_text()), self.context)
        assets = json.loads((self.workspace / bridge.assets_path).read_text())
        self.assertEqual(set(assets), {"reference-image"})
        self.assertEqual(_image_bytes(assets["reference-image"]), len(png()))
        self.assertEqual(bridge.assets_sha256, self.sha(self.workspace / bridge.assets_path))
        self.assertEqual(bridge.context_sha256, self.sha(self.workspace / bridge.context_path))
        self.assertEqual(set(bridge.provider_files), set(self.lock["files"]))
        self.assertFalse((self.workspace / "cli-spawns.jsonl").exists())
        self.assertTrue(self.discovery.seen)
        self.assertEqual({argv[-1].split(":", 1)[1] for argv in self.discovery.seen}, set(self.lock["files"]))
        self.assertEqual((self.workspace / bridge.assets_path).stat().st_mode & 0o777, 0o400)

    def test_real_callback_returns_advisory_not_bridge_and_keeps_original_binding(self):
        original = copy.deepcopy(self.context)
        callback = self.factory().provider(original)
        original["candidates"] = [{"id": "foreign"}]
        answer = callback(self.projected())
        self.assertEqual(answer["kind"], "img2.decision-advisory")
        self.assertEqual(answer["contextDigest"], canonical_digest(self.projected()))
        commands = [json.loads(line) for line in (self.workspace / "cli-spawns.jsonl").read_text().splitlines()]
        self.assertEqual([args[0] for args in commands], ["consent", "decision-routing"])

    def test_swapped_projected_context_fails_before_preparation(self):
        callback = self.factory().provider(self.context)
        projected = self.projected()
        projected["subject"]["revision"] += 1
        with patch("jev_provider_factory._prepare_image") as prepare:
            self.assert_reason("context-fault", lambda: callback(projected))
            prepare.assert_not_called()

    def test_contexts_are_not_reused_across_objects(self):
        factory = self.factory()
        first = factory(self.context)
        other = copy.deepcopy(self.context)
        other["subject"]["objectId"] = "object-2"
        second = factory(other)
        self.assertNotEqual(first.context_path, second.context_path)
        self.assertEqual(json.loads((self.workspace / first.context_path).read_text()), self.context)
        self.assertEqual(json.loads((self.workspace / second.context_path).read_text()), other)

    def test_explicit_approved_lock_is_mandatory(self):
        for release in (None, {}, {**self.lock, "contractVersion": "v3"}):
            with self.subTest(release=release):
                self.assert_reason("provider-contract-unproven", lambda: self.factory(approved_release=release))
        missing = copy.deepcopy(self.lock)
        del missing["files"]["tools/jev_consent.py"]
        self.assert_reason("provider-contract-unproven", lambda: self.factory(approved_release=missing))

    def test_actual_bytes_must_match_reviewed_lock_and_git_release(self):
        path = self.provider_root / "tools/jev_consent.py"
        path.write_text("# changed worktree, not reviewed\n")
        self.assert_reason("stale-evidence", lambda: self.factory()(self.context))
        # Even a caller lock matching dirty bytes cannot replace the Git release.
        dirty_lock = copy.deepcopy(self.lock)
        dirty_lock["files"]["tools/jev_consent.py"] = self.sha(path)
        self.assert_reason("provider-pin-mismatch", lambda: self.factory(approved_release=dirty_lock)(self.context))

    def test_approval_record_is_copied_not_mutated_or_live_widened(self):
        factory = self.factory()
        self.lock["contractVersion"] = "unsupported"
        self.lock["files"]["tools/jev_auth.py"] = "d" * 64
        self.assertIsInstance(factory(self.context), JevCliBridge)

    def test_missing_consent_auth_or_reference_never_prepares(self):
        with patch("jev_provider_factory._prepare_image") as prepare:
            self.grant["granted"] = False
            self.assert_reason("consent-required", lambda: self.factory()(self.context))
            self.grant["granted"] = True
            self.discovery.status = "credential-missing"
            self.assert_reason("credential-missing", lambda: self.factory()(self.context))
            self.discovery.status = "present"
            (self.workspace / "reference.png").unlink()
            self.assert_reason("evidence-missing", lambda: self.factory()(self.context))
            prepare.assert_not_called()

    def test_image_assets_require_unique_roles_and_bindings(self):
        with patch("jev_provider_factory._prepare_image") as prepare:
            duplicate = copy.deepcopy(self.context)
            duplicate["observations"]["images"].append({"role": "reference", "asset": "other-image"})
            self.assert_reason("ambiguous-image-binding", lambda: self.factory()(duplicate))
            duplicate = copy.deepcopy(self.context)
            (self.workspace / "other.png").write_bytes(png())
            duplicate["bindings"].append({"role": "reference", "path": "other.png",
                                           "sha256": self.sha(self.workspace / "other.png")})
            self.assert_reason("ambiguous-image-binding", lambda: self.factory()(duplicate))
            missing = copy.deepcopy(self.context)
            missing["observations"]["images"] = [{"role": "render", "asset": "render-image"}]
            self.assert_reason("missing-evidence", lambda: self.factory()(missing))
            prepare.assert_not_called()

    def test_symlink_source_and_output_parent_are_rejected(self):
        (self.workspace / "reference.png").unlink()
        outside = self.root / "outside.png"
        outside.write_bytes(png())
        (self.workspace / "reference.png").symlink_to(outside)
        self.assert_reason("unsafe-path", lambda: self.factory()(self.context))
        (self.workspace / "reference.png").unlink()
        (self.workspace / "reference.png").write_bytes(png())
        inputs = self.workspace / ".img2threejs" / "route-inputs"
        inputs.symlink_to(self.root)
        self.assert_reason("unsafe-path", lambda: self.factory()(self.context))

    def test_preparation_subprocess_is_isolated_no_shell_or_secret_environment(self):
        real_popen = subprocess.Popen
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "fixture-secret-not-real", "PYTHONPATH": "injected"}), \
                patch("jev_provider_factory.subprocess.Popen", wraps=real_popen) as spawn:
            self.factory()(self.context)
        preparation = [call for call in spawn.call_args_list if "-c" in call.args[0]]
        self.assertEqual(len(preparation), 1)
        call = preparation[0]
        self.assertEqual(call.args[0][1:4], ["-I", "-B", "-c"])
        self.assertFalse(call.kwargs["shell"])
        self.assertTrue(call.kwargs["start_new_session"])
        self.assertNotIn("TYPESAFE_API_KEY", call.kwargs["env"])
        self.assertNotIn("PYTHONPATH", call.kwargs["env"])
        self.assertNotIn("fixture-secret-not-real", repr(call.args))
        self.assertNotIn("data_base64", repr(call.args))

    def test_missing_interpreter_is_reported_by_real_preparation_runner(self):
        import jev_provider_factory as factory_module
        real_prepare = factory_module._prepare_image
        def unavailable(*args):
            with patch("jev_provider_factory.subprocess.Popen", side_effect=FileNotFoundError()):
                return real_prepare(*args)
        with patch("jev_provider_factory._prepare_image", side_effect=unavailable):
            self.assert_reason("image-dependency-unavailable", lambda: self.factory()(self.context))
        self.assertFalse((self.workspace / "cli-spawns.jsonl").exists())

    def test_actual_preparation_child_has_a_deadline(self):
        from jev_provider_factory import _prepare_image
        self.assert_reason("timeout", lambda: _prepare_image(
            Path(sys.executable).resolve(), self.provider_root / "tools/jev_decision_images.py",
            self.workspace / "reference.png", self.workspace, 0.000001))

    def test_preparation_dependency_failure_is_explicit_no_fallback(self):
        with patch("jev_provider_factory._prepare_image", side_effect=DecisionError("image-dependency-unavailable")):
            self.assert_reason("image-dependency-unavailable", lambda: self.factory()(self.context))
        self.assertFalse((self.workspace / "cli-spawns.jsonl").exists())

    def test_invalid_prepared_image_is_rejected_without_paid_spawn(self):
        with patch("jev_provider_factory._prepare_image", return_value={"data_base64": "untrusted"}):
            self.assert_reason("invalid-assets", lambda: self.factory()(self.context))
        self.assertFalse((self.workspace / "cli-spawns.jsonl").exists())

    def test_provider_changes_after_preparation_are_rechecked_on_bridge_use(self):
        bridge = self.factory()(self.context)
        (self.provider_root / "tools/jev_auth.py").write_text("# stale release bytes")
        self.assert_reason("provider-contract-unproven", lambda: bridge(self.projected()))
        self.assertFalse((self.workspace / "cli-spawns.jsonl").exists())

    def test_consent_revocation_after_factory_creation_blocks_cli_spawn(self):
        bridge = self.factory()(self.context)
        self.grant["granted"] = False
        self.assert_reason("consent-required", lambda: bridge(self.projected()))
        self.assertFalse((self.workspace / "cli-spawns.jsonl").exists())

    def test_no_images_is_a_valid_local_context_without_preparation(self):
        context = copy.deepcopy(self.context)
        context["observations"]["images"] = []
        with patch("jev_provider_factory._prepare_image") as prepare:
            bridge = self.factory()(context)
            prepare.assert_not_called()
        self.assertEqual(json.loads((self.workspace / bridge.assets_path).read_text()), {})

    def test_exact_declared_step_cannot_be_replaced_by_provider_metadata(self):
        self.discovery.jev["steps"][0]["argv"][2] = "route"
        self.assert_reason("capability-unavailable", lambda: self.factory()(self.context))

    def test_source_hash_change_after_preparation_blocks_bridge_use(self):
        bridge = self.factory()(self.context)
        (self.workspace / "reference.png").write_bytes(b"changed source")
        self.assert_reason("stale-evidence", lambda: bridge(self.projected()))
        self.assertFalse((self.workspace / "cli-spawns.jsonl").exists())

    def test_model_free_text_and_foreign_run_are_not_preparation_inputs(self):
        foreign = copy.deepcopy(self.context)
        foreign["subject"]["runId"] = "other-run"
        self.assert_reason("subject-mismatch", lambda: self.factory()(foreign))
        unsafe = copy.deepcopy(self.context)
        unsafe["observations"]["prompt"] = "send arbitrary user prose"
        self.assert_reason("unsafe-context", lambda: self.factory()(unsafe))


if __name__ == "__main__":
    unittest.main()
