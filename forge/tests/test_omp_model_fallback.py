"""Offline transport/schema fixtures, not real model usage or quality evidence."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from forge.omp_model_fallback import (configuration, parse_events, prompt, run, _DISCOVERY_DENY,
                                      _attachments)
from forge import omp_model_fallback as omp_fallback
from decision_control import DecisionController, DecisionError, GROUPS, canonical_digest, _json_bytes
from stage0_route.fallback import HostFallback, IMAGE_MANIFEST_ENV, IMAGE_MANIFEST_SHA_ENV
from stage0_route.images import png


class OmpModelFallbackTests(unittest.TestCase):
    def setUp(self):
        self.context = {"kind": "img2.build-strategy-context", "version": 1, "group": "build-strategy",
            "subject": {"runId": "fixture", "objectId": "cube", "revision": 1, "passId": "blockout"},
            "bindings": [{"role": "reference", "sha256": "a" * 64}],
            "candidates": [{"id": "keep"}], "observations": {},
            "requirements": {"criticalFeatures": [], "requiredViews": []},
            "policyVersion": "fixture-v1", "questionSetVersion": "strategy-questions-v1",
            "requestedModel": "jev-1.14.0"}
        self.message = {"role": "assistant", "provider": "fixture-provider", "model": "fixture-model",
            "stopReason": "stop", "usage": {"input": 7, "output": 4, "cacheRead": 11, "cacheWrite": 3},
            "content": [{"type": "text", "text": json.dumps({"answers": {
                "selection": {"type": "choice", "choice": "keep", "probabilities": {"keep": 1}, "confidence": 1},
                "anyFits": {"type": "noul", "noul": 1}}})}]}

    def raw(self, message=None):
        return json.dumps({"type": "message_end", "message": message or self.message}).encode() + b"\n"

    def test_only_observed_provider_model_and_all_usage_categories_are_used(self):
        observations = []
        result = parse_events(self.raw(), self.context, "fixture-provider/fixture-model", observe=observations.append)
        self.assertEqual(result["usage"], {"input_tokens": 21, "output_tokens": 4})
        self.assertEqual(result["model"], "fixture-model")
        self.assertEqual(observations[0]["usage"], self.message["usage"])

    def test_missing_usage_or_model_cannot_be_inferred_from_approval(self):
        for key in ("usage", "model", "provider"):
            message = deepcopy(self.message)
            message.pop(key)
            with self.subTest(key=key), self.assertRaises(DecisionError):
                parse_events(self.raw(message), self.context, "fixture-provider/fixture-model")
        message = deepcopy(self.message)
        message["usage"].pop("cacheRead")
        with self.assertRaises(DecisionError):
            parse_events(self.raw(message), self.context, "fixture-provider/fixture-model")

    def test_tools_multiple_turns_and_incomplete_transport_are_rejected(self):
        cases = [self.raw() + self.raw(), b'{"type":"tool_execution_start"}\n' + self.raw()]
        for change in ({"stopReason": "toolUse"}, {"content": [{"type": "toolCall", "name": "read"}]}):
            message = deepcopy(self.message)
            message.update(change)
            cases.append(self.raw(message))
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(DecisionError):
                parse_events(raw, self.context, "fixture-provider/fixture-model")

    def test_out_of_set_answers_reach_controller_with_observed_usage(self):
        message = deepcopy(self.message)
        answer = json.loads(message["content"][0]["text"])
        answer["answers"]["selection"]["choice"] = "invented"
        message["content"][0]["text"] = json.dumps(answer)
        observations = []
        result = parse_events(self.raw(message), self.context, "fixture-provider/fixture-model",
                              observe=observations.append)
        self.assertEqual(result["usage"], {"input_tokens": 21, "output_tokens": 4})
        self.assertEqual(result["answers"], answer["answers"])
        self.assertEqual(observations[0]["usage"], message["usage"])
        with self.assertRaises(DecisionError) as caught:
            DecisionController._advisory(object.__new__(DecisionController), self.context, result,
                                         model_fallback=True)
        self.assertEqual(caught.exception.reason_code, "out-of-set")

    def test_malformed_answer_bodies_preserve_usage_as_invalid_advisories(self):
        for text in ("not JSON", "[]", '{"answers":{},"usage":{"input_tokens":1}}',
                     '{"answers":{},"answers":{}}', '{"answers":NaN}', None, 7):
            with self.subTest(text=text):
                message = deepcopy(self.message)
                message["content"][0]["text"] = text
                observations = []
                result = parse_events(self.raw(message), self.context, "fixture-provider/fixture-model",
                                      observe=observations.append)
                self.assertIsNone(result["answers"])
                self.assertEqual(result["usage"], {"input_tokens": 21, "output_tokens": 4})
                self.assertEqual(observations[0]["usage"], message["usage"])
                with self.assertRaises(DecisionError):
                    DecisionController._advisory(object.__new__(DecisionController), self.context, result,
                                                 model_fallback=True)

    def test_request_cannot_upload_files_or_author_its_usage(self):
        context = deepcopy(self.context)
        context["bindings"][0]["path"] = "private.png"
        with self.assertRaises(DecisionError):
            prompt(context)
        message = deepcopy(self.message)
        message["content"][0]["text"] = json.dumps({"answers": {}, "usage": {"input_tokens": 1}})
        result = parse_events(self.raw(message), self.context, "fixture-provider/fixture-model")
        self.assertIsNone(result["answers"])
        self.assertEqual(result["usage"], {"input_tokens": 21, "output_tokens": 4})

    def test_host_model_and_absolute_binary_are_required(self):
        for environment in ({}, {"IMG2_FALLBACK_OMP": "omp", "IMG2_FALLBACK_MODEL": "fixture-provider/model"},
                            {"IMG2_FALLBACK_OMP": sys.executable, "IMG2_FALLBACK_MODEL": "guess"}):
            with self.subTest(environment=environment), self.assertRaises(DecisionError):
                configuration(environment)

    def test_unknown_cli_version_refuses_before_prompt_or_model_send(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            marker = root / "model-was-started"
            executable = root / "unsupported-omp"
            executable.write_text("#!" + sys.executable + "\nimport pathlib,sys\n"
                "if sys.argv[1:]==['--version']:\n print('omp/20.0.0');sys.exit(0)\n"
                + "pathlib.Path(" + repr(str(marker)) + ").write_text('unexpected')\n")
            executable.chmod(0o700)
            with self.assertRaises(DecisionError) as caught:
                run(self.context, environment=dict(os.environ,
                    IMG2_FALLBACK_OMP=str(executable), IMG2_FALLBACK_MODEL="fixture-provider/fixture-model"))
            self.assertEqual(caught.exception.reason_code, "fallback-omp-version-unsupported")
            self.assertFalse(marker.exists())

    def test_actual_executable_receives_isolation_overlay_and_fixed_append_override(self):
        # A real disposable transport executable inspects its actual argv/cwd/env.
        # Its synthetic JSON event is explicitly not a real model measurement.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "APPEND_SYSTEM.md").write_text("PRIVATE_APPEND_SENTINEL")
            executable = root / "omp-fixture"
            checks = '''import json, os, pathlib, sys
args=sys.argv[1:]
if args==['--version']:
    print('omp/19.0.1')
    sys.exit(0)
assert os.getpgrp() == os.getpid()
assert 'IMG2_FALLBACK_SUPERVISED' not in os.environ
required=['--no-tools','--no-extensions','--no-skills','--no-rules','--no-session','--no-context-files','--no-lsp','--no-pty','-p']
assert all(flag in args for flag in required)
assert args[args.index('--append-system-prompt')+1]=='Use only the supplied projected JSON decision data and explicitly attached bound images.'
assert args[args.index('--system-prompt')+1]=='You answer bounded JSON decisions. No tools or filesystem access. Return only JSON.'
assert args[args.index('--model')+1]=='fixture-provider/fixture-model'
assert 'TYPESAFE_API_KEY' not in os.environ
assert 'PI_INTENT_PROMPT' not in os.environ
assert pathlib.Path.cwd()!=pathlib.Path(os.environ['FIXTURE_ROOT'])
config=pathlib.Path(args[args.index('--config')+1])
assert config.parent==pathlib.Path.cwd()
overlay=json.loads(config.read_text())
assert set(overlay['disabledProviders'])==set(DENY)
assert overlay['mcp']['enableProjectConfig'] is False
assert overlay['retry']=={'enabled':False,'maxRetries':0,'modelFallback':False,'usageAwareFallback':False,'waitForUsageReset':False}
payload=sys.stdin.read()
assert 'PRIVATE_APPEND_SENTINEL' not in payload
print(json.dumps(EVENT))
'''
            checks = checks.replace("DENY", repr(list(_DISCOVERY_DENY))).replace(
                "EVENT", repr({"type": "message_end", "message": self.message}))
            executable.write_text("#!" + sys.executable + "\n" + checks)
            executable.chmod(0o700)
            environment = dict(os.environ, IMG2_FALLBACK_OMP=str(executable),
                IMG2_FALLBACK_MODEL="fixture-provider/fixture-model", FIXTURE_ROOT=str(root),
                TYPESAFE_API_KEY="fixture-not-a-key", PI_INTENT_PROMPT="PRIVATE_APPEND_SENTINEL",
                IMG2_FALLBACK_SUPERVISED="1")
            # An inherited flag on a nonleader cannot join the caller's group.
            with patch("forge.omp_model_fallback.os.getpgrp", return_value=-1):
                result = run(self.context, environment=environment)
            self.assertEqual(result["usage"], {"input_tokens": 21, "output_tokens": 4})

    def test_executable_host_fallback_rejection_accounts_once_and_survives_reopen(self):
        # Run both real executable boundaries with synthetic OMP output, then
        # inspect the real controller journal and persisted rejection records.
        cases = [
            ("out-of-set", "out-of-set", 25, 0),
            ("malformed-json", "malformed", 25, 0),
            ("non-object", "malformed", 25, 0),
            ("extra-key", "malformed", 25, 0),
            ("bad-text", "malformed", 25, 0),
            ("missing-usage", "provider-error", 0, 128),
            ("unapproved-model", "provider-error", 0, 128),
            ("tool-call", "provider-error", 0, 128),
            ("incomplete", "provider-error", 0, 128),
        ]
        for mode, failure_reason, debit, reserved in cases:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                workspace = root / "workspace"
                workspace.mkdir()
                reference = workspace / "reference.png"
                reference.write_bytes(b"offline reference fixture")
                context = deepcopy(self.context)
                context["bindings"][0].update(path="reference.png",
                    sha256=hashlib.sha256(reference.read_bytes()).hexdigest())
                message = deepcopy(self.message)
                if mode == "out-of-set":
                    answer = json.loads(message["content"][0]["text"])
                    answer["answers"]["selection"]["choice"] = "invented"
                    message["content"][0]["text"] = json.dumps(answer)
                elif mode == "malformed-json":
                    message["content"][0]["text"] = "not JSON"
                elif mode == "non-object":
                    message["content"][0]["text"] = "[]"
                elif mode == "extra-key":
                    message["content"][0]["text"] = '{"answers":{},"usage":{"input_tokens":1}}'
                elif mode == "bad-text":
                    message["content"][0]["text"] = None
                elif mode == "missing-usage":
                    message.pop("usage")
                elif mode == "unapproved-model":
                    message["model"] = "unapproved"
                elif mode == "tool-call":
                    message["content"].append({"type": "toolCall", "name": "read"})
                elif mode == "incomplete":
                    message["stopReason"] = "length"
                marker = root / "transport-calls.jsonl"
                executable = root / "omp-fixture"
                executable.write_text("#!" + sys.executable + "\n"
                    "import json,pathlib,sys\n"
                    "version=sys.argv[1:]==['--version']\n"
                    "with pathlib.Path(" + repr(str(marker)) + ").open('a') as stream:\n"
                    " stream.write(json.dumps('version' if version else 'send')+'\\n')\n"
                    "if version:\n print('omp/19.0.1');sys.exit(0)\n"
                    "sys.stdin.read()\nprint(" + repr(self.raw(message).decode().strip()) + ")\n")
                executable.chmod(0o700)
                adapter = root / "fallback-adapter"
                adapter.write_text("#!" + sys.executable + "\nimport sys\n"
                    "sys.path.insert(0," + repr(str(ROOT)) + ")\n"
                    "from forge.omp_model_fallback import main\nraise SystemExit(main())\n")
                adapter.chmod(0o700)
                fallback = HostFallback(adapter, hashlib.sha256(adapter.read_bytes()).hexdigest())
                controller = DecisionController(workspace, "fixture", clock=lambda: 1000.0)
                grant = {"granted": True, "purposes": list(GROUPS), "sha256": "b" * 64,
                         "tokenBudget": 10000}
                provider = Mock(side_effect=AssertionError("No Jev provider call is authorized"))
                arguments = dict(provider=provider, fallback=fallback,
                    availability=lambda: {"status": "not-installed"},
                    consent=lambda: deepcopy(grant), request_token_limit=128)
                with patch.dict(os.environ, IMG2_FALLBACK_OMP=str(executable),
                                IMG2_FALLBACK_MODEL="fixture-provider/fixture-model"):
                    record = controller.consult(context, **arguments)
                    calls = marker.read_text()
                    self.assertEqual([json.loads(line) for line in calls.splitlines()], ["version", "send"])
                    for active in (controller, DecisionController(workspace, "fixture", clock=lambda: 1000.0)):
                        self.assertEqual((record["status"], record["reasonCode"]),
                                         ("request-input", "fallback-invalid"))
                        self.assertIsNone(record["model"])
                        self.assertIsNone(record["decider"])
                        self.assertEqual(record["answers"], {})
                        with active._locked():
                            entries, state = active._load()
                        run_state = state["runs"]["fixture"]
                        self.assertEqual((run_state["tokensUsed"], run_state["tokensReserved"]),
                                         (debit, reserved))
                        failures = [entry["data"] for entry in entries if entry["event"] == "attempt-failure"]
                        self.assertEqual(len(failures), 1)
                        self.assertEqual((failures[0]["reasonCode"], failures[0]["tokens"], failures[0]["release"]),
                                         (failure_reason, debit, 128 - reserved))
                        self.assertFalse(any(entry["event"] == "attempt-result" for entry in entries))
                        saved = json.loads((active.record_root / "build-strategy" /
                                            (record["recordId"] + ".json")).read_text())
                        self.assertEqual(saved, record)
                        record = active.consult(context, **arguments)
                        self.assertEqual(marker.read_text(), calls)
                provider.assert_not_called()

    def test_bound_pixels_reach_actual_omp_argv_and_controller_consumption(self):
        # Two executable boundaries, synthetic response: transport, NOT vision quality.
        from forge.tests.test_jpeg import FIXTURE
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            context = deepcopy(self.context)
            context.update(kind="img2.quality-review-context", group="quality-review",
                           questionSetVersion="quality-review-questions-v1")
            context["requirements"]["dimensions"] = ["geometry"]
            message = deepcopy(self.message)
            message["content"][0]["text"] = json.dumps({"answers": {"dimension:geometry": {
                "type": "score", "score": 3, "confidence": 0.9,
                "probabilities": {"0": 0, "1": 0, "2": 0, "3": 1, "4": 0},
                "legend": {
                    "0": "Absent or wrong relative to the declared requirement.",
                    "1": "Major mismatch with the declared requirement.",
                    "2": "Partially meets the declared requirement, with visible deficiencies.",
                    "3": "Meets the declared requirement.",
                    "4": "Exceeds the declared requirement without changing it."}}}})
            context["bindings"] = []
            context["observations"]["images"] = []
            sources = []
            for index, role in enumerate(("reference", "render")):
                raw = FIXTURE if index else png(2, 1, 3, bytes((index, 10, 20, 30, 40, index)))
                relative = role + "-do-not-expose.txt"
                (workspace / relative).write_bytes(raw)
                context["bindings"].append({"role": role, "path": relative,
                                             "sha256": hashlib.sha256(raw).hexdigest()})
                context["observations"]["images"].append({"role": role, "asset": role + "-asset"})
                sources.append(raw)
            projected = deepcopy(context)
            projected["bindings"] = [{key: row[key] for key in ("role", "sha256")}
                                     for row in context["bindings"]]
            marker = root / "argv.json"
            outer_marker = root / "adapter-cwd"
            executable = root / "omp-fixture"
            checks = '''import hashlib, json, os, pathlib, sys
args = sys.argv[1:]
if args == ['--version']:
    print('omp/19.0.1')
    sys.exit(0)
assert all(flag in args for flag in ['--no-tools','--no-context-files','--no-extensions','--no-skills','--no-rules','--no-lsp','--no-pty','--no-session'])
assert args[args.index('--append-system-prompt')+1] == 'Use only the supplied projected JSON decision data and explicitly attached bound images.'
assert args[args.index('--model')+1] == 'fixture-provider/fixture-model'
assert 'IMG2_FALLBACK_IMAGE_MANIFEST' not in os.environ
assert 'IMG2_FALLBACK_IMAGE_MANIFEST_SHA256' not in os.environ
images = args[args.index('--model')+2:]
assert len(images) == len(SOURCES)
root = pathlib.Path.cwd()
assert root.stat().st_mode & 0o077 == 0
for index, (argument, expected) in enumerate(zip(images, SOURCES)):
    suffix = '.jpg' if expected.startswith(b'\\xff\\xd8') else '.png'
    assert argument == '@' + str(root / ('image-' + str(index) + suffix))
    image = pathlib.Path(argument[1:])
    assert image.stat().st_mode & 0o077 == 0
    with image.open('rb') as stream:
        data = stream.read()
    assert stream.closed and data == expected
    assert hashlib.sha256(data).hexdigest() == PROJECTED['bindings'][index]['sha256']
payload = sys.stdin.read()
assert json.loads(payload.split('DATA:\\n', 1)[1]) == PROJECTED
assert 'Attachment order (1-based)' in payload
assert '"attachment":1,"role":"reference"' in payload
assert '"attachment":2,"role":"render"' in payload
assert 'do-not-expose.txt' not in payload
assert not any('do-not-expose.txt' in argument for argument in args)
overlay = json.loads(pathlib.Path(args[args.index('--config')+1]).read_text())
assert set(overlay['disabledProviders']) == set(DENY)
assert overlay['mcp']['enableProjectConfig'] is False
assert overlay['retry']['enabled'] is False and overlay['retry']['modelFallback'] is False
pathlib.Path(MARKER).write_text(json.dumps({'cwd':str(root),'images':images}))
print(json.dumps(EVENT))
'''
            checks = checks.replace("SOURCES", repr(sources)).replace("PROJECTED", repr(projected))
            checks = checks.replace("MARKER", repr(str(marker))).replace("DENY", repr(list(_DISCOVERY_DENY)))
            checks = checks.replace("EVENT", repr({"type": "message_end", "message": message}))
            executable.write_text("#!" + sys.executable + "\n" + checks)
            executable.chmod(0o700)
            adapter = root / "fallback-adapter"
            adapter.write_text("#!" + sys.executable + "\nimport pathlib,sys\n"
                "pathlib.Path(" + repr(str(outer_marker)) + ").write_text(str(pathlib.Path.cwd()))\n"
                "sys.path.insert(0," + repr(str(ROOT)) + ")\n"
                "from forge.omp_model_fallback import main\nraise SystemExit(main())\n")
            adapter.chmod(0o700)
            fallback = HostFallback(adapter, hashlib.sha256(adapter.read_bytes()).hexdigest())
            controller = DecisionController(workspace, "fixture", clock=lambda: 1000.0)
            grant = {"granted": True, "purposes": list(GROUPS), "sha256": "b" * 64, "tokenBudget": 10000}
            provider = Mock(side_effect=AssertionError("No Jev calls"))
            with patch.dict(os.environ, IMG2_FALLBACK_OMP=str(executable),
                            IMG2_FALLBACK_MODEL="fixture-provider/fixture-model"):
                record = controller.consult(context, provider=provider, fallback=fallback,
                    availability=lambda: {"status": "not-installed"}, consent=lambda: deepcopy(grant),
                    request_token_limit=128)
                self.assertEqual((record["status"], record["decider"]), ("answered", "model:fixture-model"))
                self.assertEqual(record["reasonCode"], "review-evidence-only")
                self.assertNotIn("accepted", record)
                self.assertEqual(controller.consume(record, context, consent=lambda: deepcopy(grant)), record)
            call = json.loads(marker.read_text())
            self.assertFalse(Path(call["cwd"]).exists())
            self.assertFalse(Path(outer_marker.read_text()).exists())
            self.assertTrue(all(not Path(argument[1:]).exists() for argument in call["images"]))
            provider.assert_not_called()

    def test_private_manifest_pin_roles_paths_hash_and_mime_are_rechecked(self):
        cases = ("pin", "digest", "role", "asset", "sha", "missing", "duplicate", "outside",
                 "image-changed", "mime", "text", "permissions", "image-link", "manifest-link")
        for mode in cases:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                output = root / "omp-private"
                output.mkdir(mode=0o700)
                image = root / "image-0.png"
                raw = png(1, 1, 3, b"\x01\x02\x03")
                image.write_bytes(raw)
                image.chmod(0o600)
                projected = deepcopy(self.context)
                sha = hashlib.sha256(raw).hexdigest()
                projected["bindings"][0]["sha256"] = sha
                projected["observations"]["images"] = [{"role": "reference", "asset": "ref"}]
                manifest = {"kind": "img2.host-fallback-images", "version": 1,
                    "contextDigest": canonical_digest(projected),
                    "images": [{"role": "reference", "asset": "ref", "path": str(image), "sha256": sha}]}
                row = manifest["images"][0]
                if mode == "digest":
                    manifest["contextDigest"] = "0" * 64
                elif mode in {"role", "asset"}:
                    row[mode] = "not-the-projected-" + mode
                elif mode == "sha":
                    row["sha256"] = "0" * 64
                elif mode == "missing":
                    manifest["images"] = []
                elif mode == "duplicate":
                    manifest["images"].append(deepcopy(row))
                elif mode == "outside":
                    row["path"] = str(root.parent / image.name)
                elif mode == "image-changed":
                    image.write_bytes(png(1, 1, 3, b"\x03\x02\x01"))
                elif mode == "mime":
                    target = root / "image-0.jpg"
                    image.rename(target)
                    row["path"] = str(target)
                elif mode == "text":
                    image.write_bytes(b"private local text")
                    row["sha256"] = hashlib.sha256(image.read_bytes()).hexdigest()
                    projected["bindings"][0]["sha256"] = row["sha256"]
                    manifest["contextDigest"] = canonical_digest(projected)
                elif mode == "permissions":
                    image.chmod(0o644)
                elif mode == "image-link":
                    target = root / "original.png"
                    image.rename(target)
                    image.symlink_to(target)
                path = root / "images.json"
                path.write_bytes(_json_bytes(manifest))
                path.chmod(0o600)
                manifest_sha = hashlib.sha256(path.read_bytes()).hexdigest()
                if mode == "pin":
                    manifest_sha = "0" * 64
                elif mode == "manifest-link":
                    target = root / "original.json"
                    path.rename(target)
                    path.symlink_to(target)
                environment = {IMAGE_MANIFEST_ENV: str(path), IMAGE_MANIFEST_SHA_ENV: manifest_sha}
                with patch("forge.omp_model_fallback.Path.cwd", return_value=root), \
                        patch("forge.omp_model_fallback.subprocess.Popen") as launch:
                    with self.assertRaises(DecisionError):
                        _attachments(projected, environment, output, deadline=time.monotonic() + 55)
                launch.assert_not_called()

    def test_declared_images_without_private_manifest_are_not_silently_omitted(self):
        projected = deepcopy(self.context)
        projected["observations"]["images"] = [{"role": "reference", "asset": "ref"}]
        with tempfile.TemporaryDirectory() as temporary:
            for environment in ({}, {IMAGE_MANIFEST_ENV: "/arbitrary/image.json"},
                                {IMAGE_MANIFEST_SHA_ENV: "a" * 64}):
                with self.subTest(environment=environment), self.assertRaises(DecisionError):
                    _attachments(projected, environment, Path(temporary), deadline=time.monotonic() + 55)

    def test_expired_attachment_decode_never_launches_version_or_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            image = root / "image-0.png"
            data = png(1, 1, 3, b"\x01\x02\x03")
            image.write_bytes(data)
            image.chmod(0o600)
            projected = deepcopy(self.context)
            sha = hashlib.sha256(data).hexdigest()
            projected["bindings"][0]["sha256"] = sha
            projected["observations"]["images"] = [{"role": "reference", "asset": "ref"}]
            manifest = {"kind": "img2.host-fallback-images", "version": 1,
                "contextDigest": canonical_digest(projected),
                "images": [{"role": "reference", "asset": "ref", "path": str(image), "sha256": sha}]}
            path = root / "images.json"
            raw = _json_bytes(manifest)
            path.write_bytes(raw)
            path.chmod(0o600)
            executable = root / "never-launched"
            executable.write_text("#!" + sys.executable + "\nraise AssertionError('launched after expiry')\n")
            executable.chmod(0o700)
            environment = {IMAGE_MANIFEST_ENV: str(path), IMAGE_MANIFEST_SHA_ENV: hashlib.sha256(raw).hexdigest(),
                "IMG2_FALLBACK_OMP": str(executable), "IMG2_FALLBACK_MODEL": "fixture-provider/fixture-model"}
            now, scratches = [100.0], []
            real_decode = omp_fallback._image_suffix
            real_directory = tempfile.TemporaryDirectory

            def decode_image(raw):
                suffix = real_decode(raw)
                now[0] += 2
                return suffix

            def scratch_directory(**kwargs):
                directory = real_directory(**kwargs)
                scratches.append(Path(directory.name))
                return directory

            with patch("forge.omp_model_fallback.TIMEOUT", 1), \
                    patch("forge.omp_model_fallback.time.monotonic", side_effect=lambda: now[0]), \
                    patch("forge.omp_model_fallback.Path.cwd", return_value=root), \
                    patch("forge.omp_model_fallback._image_suffix", side_effect=decode_image), \
                    patch("forge.omp_model_fallback.tempfile.TemporaryDirectory", side_effect=scratch_directory), \
                    patch("forge.omp_model_fallback.subprocess.Popen") as launch:
                with self.assertRaises(DecisionError) as caught:
                    run(projected, environment=environment)
            self.assertEqual(caught.exception.reason_code, "timeout")
            launch.assert_not_called()
            self.assertEqual(len(scratches), 1)
            self.assertFalse(scratches[0].exists())

    def test_expiry_before_version_and_after_version_pin_blocks_next_launch(self):
        for phase in ("overlay", "version-pin"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                marker = root / "calls"
                executable = root / "omp-fixture"
                executable.write_text("#!" + sys.executable + "\nimport pathlib,sys\n"
                    "with pathlib.Path(" + repr(str(marker)) + ").open('a') as stream:\n"
                    " stream.write('version\\n' if sys.argv[1:]==['--version'] else 'model\\n')\n"
                    "if sys.argv[1:]==['--version']:\n print('omp/19.0.1');sys.exit(0)\n"
                    "raise AssertionError('model launched after expiry')\n")
                executable.chmod(0o700)
                now, scratches, hashes = [100.0], [], [0]
                real_file = omp_fallback._file
                real_write = Path.write_text
                real_directory = tempfile.TemporaryDirectory

                def read_file(*args, **kwargs):
                    result = real_file(*args, **kwargs)
                    hashes[0] += 1
                    if phase == "version-pin" and hashes[0] == 2:
                        now[0] += 2
                    return result

                def write_text(target, *args, **kwargs):
                    result = real_write(target, *args, **kwargs)
                    if phase == "overlay" and target.name == "isolation.json":
                        now[0] += 2
                    return result

                def scratch_directory(**kwargs):
                    directory = real_directory(**kwargs)
                    scratches.append(Path(directory.name))
                    return directory

                with patch("forge.omp_model_fallback.TIMEOUT", 1), \
                        patch("forge.omp_model_fallback.time.monotonic", side_effect=lambda: now[0]), \
                        patch("forge.omp_model_fallback._file", side_effect=read_file), \
                        patch.object(Path, "write_text", write_text), \
                        patch("forge.omp_model_fallback.tempfile.TemporaryDirectory", side_effect=scratch_directory):
                    with self.assertRaises(DecisionError) as caught:
                        run(self.context, environment={"IMG2_FALLBACK_OMP": str(executable),
                            "IMG2_FALLBACK_MODEL": "fixture-provider/fixture-model"})
                self.assertEqual(caught.exception.reason_code, "timeout")
                if phase == "overlay":
                    self.assertFalse(marker.exists())
                else:
                    self.assertEqual(marker.read_text().splitlines(), ["version"])
                self.assertEqual(len(scratches), 1)
                self.assertFalse(scratches[0].exists())

    def test_outer_timeout_kills_supervised_version_model_and_descendants(self):
        # Benign local executables exercise real PIDs/PGIDs; no model/provider.
        for stall in ("version", "model"):
            with self.subTest(stall=stall), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                marker = root / "processes.jsonl"
                descendant = root / "descendant.py"
                descendant.write_text("import json,os,pathlib,time\n"
                    "with pathlib.Path(" + repr(str(marker)) + ").open('a') as stream:\n"
                    " stream.write(json.dumps({'kind':'descendant','pid':os.getpid(),'pgid':os.getpgrp()})+'\\n')\n"
                    "time.sleep(60)\n")
                executable = root / "omp-fixture"
                executable.write_text("#!" + sys.executable + "\n"
                    "import json,os,pathlib,subprocess,sys,time\n"
                    "kind='version' if sys.argv[1:]==['--version'] else 'model'\n"
                    "assert 'IMG2_FALLBACK_SUPERVISED' not in os.environ\n"
                    "with pathlib.Path(" + repr(str(marker)) + ").open('a') as stream:\n"
                    " stream.write(json.dumps({'kind':kind,'pid':os.getpid(),'pgid':os.getpgrp(),"
                    "'cwd':str(pathlib.Path.cwd())})+'\\n')\n"
                    "if kind=='version' and " + repr(stall) + "!='version':\n print('omp/19.0.1');sys.exit(0)\n"
                    "subprocess.Popen([sys.executable," + repr(str(descendant)) + "])\n"
                    "time.sleep(60)\n")
                executable.chmod(0o700)
                adapter = root / "fallback-adapter"
                adapter.write_text("#!" + sys.executable + "\nimport json,os,pathlib,sys\n"
                    "with pathlib.Path(" + repr(str(marker)) + ").open('a') as stream:\n"
                    " stream.write(json.dumps({'kind':'wrapper','pid':os.getpid(),'pgid':os.getpgrp(),"
                    "'cwd':str(pathlib.Path.cwd()),'supervised':os.environ.get('IMG2_FALLBACK_SUPERVISED')})+'\\n')\n"
                    "sys.path.insert(0," + repr(str(ROOT)) + ")\n"
                    "from forge.omp_model_fallback import main\nraise SystemExit(main())\n")
                adapter.chmod(0o700)
                fallback = HostFallback(adapter, hashlib.sha256(adapter.read_bytes()).hexdigest(), timeout=2)
                with patch.dict(os.environ, IMG2_FALLBACK_OMP=str(executable),
                                IMG2_FALLBACK_MODEL="fixture-provider/fixture-model",
                                IMG2_FALLBACK_SUPERVISED="stale-inherited-value"):
                    with self.assertRaises(DecisionError) as caught:
                        fallback(self.context)
                self.assertEqual(caught.exception.reason_code, "timeout")
                processes = [json.loads(line) for line in marker.read_text().splitlines()]

                def cleanup_group(pgid):
                    if pgid != os.getpgrp():
                        try:
                            os.killpg(pgid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

                for pgid in {process["pgid"] for process in processes}:
                    self.addCleanup(cleanup_group, pgid)
                wrapper = next(process for process in processes if process["kind"] == "wrapper")
                expected = {"wrapper", "version", "descendant"} | ({"model"} if stall == "model" else set())
                self.assertEqual({process["kind"] for process in processes}, expected)
                self.assertEqual(wrapper["supervised"], "1")
                self.assertEqual(wrapper["pgid"], wrapper["pid"])
                self.assertNotEqual(wrapper["pgid"], os.getpgrp())
                self.assertEqual({process["pgid"] for process in processes}, {wrapper["pid"]})
                for process in processes:
                    deadline = time.monotonic() + 5
                    while True:
                        status = subprocess.run(["ps", "-o", "stat=", "-p", str(process["pid"])],
                            capture_output=True, text=True, check=False).stdout.strip()
                        # A reparented zombie cannot execute or keep a provider alive.
                        if not status or status.startswith("Z") or time.monotonic() >= deadline:
                            break
                        time.sleep(0.01)
                    self.assertTrue(not status or status.startswith("Z"), (process, status))
                    if "cwd" in process:
                        self.assertFalse(Path(process["cwd"]).exists())


if __name__ == "__main__":
    unittest.main()
