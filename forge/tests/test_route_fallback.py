"""Offline host-approved model fallback: transport, controller journalling and CLI wiring.

The fallback program is a tiny Python script written into a temporary directory and
pinned by its SHA-256. No network, credential, model SDK or Jev request is involved.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

FORGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORGE))
sys.path.insert(0, str(FORGE / "_shared"))
SPEC = importlib.util.spec_from_file_location("img2_route_cli_fallback", FORGE / "route.py")
route_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(route_cli)
from decision_control import DecisionController, DecisionError, GROUPS, canonical_digest
from stage0_route.fallback import (HostFallback, IMAGE_MANIFEST_ENV, IMAGE_MANIFEST_SHA_ENV,
                                  SUPERVISED_ENV)
from stage0_route import fallback as fallback_module
from stage0_route.images import png

# One advisory builder shared by the fixture program and the in-process Jev stand-in,
# so the "well-formed answer" is defined exactly once.
BUILDER = '''
def build(context, model, probability, digest):
    ids = [c["id"] for c in context["candidates"]]
    answers = {}
    if ids:
        chosen = ids[0]
        if len(ids) == 1:
            probabilities = {chosen: 1.0}
        else:
            probabilities = {key: (probability if key == chosen else (1 - probability) / (len(ids) - 1))
                             for key in ids}
        answers = {"selection": {"type": "choice", "choice": chosen, "probabilities": probabilities,
                                 "confidence": 0.9},
                   "anyFits": {"type": "noul", "noul": 1}}
    decomposition = context["requirements"].get("decomposition")
    if decomposition is not None:
        answers["missingObjects"] = {"type": "noul", "noul": 0}
        for item in decomposition["objects"]:
            left, top, right, bottom = item["approxBox"]
            answers["object:" + item["id"] + ":visible"] = {"type": "noul", "noul": 1}
            answers["object:" + item["id"] + ":box"] = {"type": "box", "confidence": 0.9,
                "box": {"left": left, "top": top, "right": right, "bottom": bottom}}
    return {"kind": "img2.decision-advisory", "version": 1, "group": context["group"],
            "subject": context["subject"], "contextDigest": digest, "model": model,
            "answers": answers, "usage": {"input_tokens": 20, "output_tokens": 10}}
'''
BODY = '''
raw = sys.stdin.buffer.read()
digest = hashlib.sha256(raw).hexdigest()
context = json.loads(raw)
with open(MARKER, "a") as stream:
    stream.write(json.dumps({"pid": os.getpid(), "cwd": os.getcwd(), "argv": sys.argv,
                             "stdinSha256": digest, "passId": context["subject"]["passId"],
                             "imageManifest": os.environ.get("IMG2_FALLBACK_IMAGE_MANIFEST"),
                             "imageManifestSha": os.environ.get("IMG2_FALLBACK_IMAGE_MANIFEST_SHA256"),
                             "supervised": os.environ.get("IMG2_FALLBACK_SUPERVISED"),
                             "pgid": os.getpgrp(),
                             "stripped": [k for k in ("NODE_OPTIONS", "PYTHONSTARTUP", "TYPESAFE_API_KEY") if k in os.environ],
                             "inherited": os.environ.get("IMG2_FALLBACK_TEST_MARKER")}) + "\\n")
advisory = build(context, MODEL, PROBABILITY, digest)
selection = advisory["answers"].get("selection")
ids = list(selection["probabilities"]) if selection is not None else []
if MODE == "bad-json" or (MODE == "bad-json-routing" and context["subject"]["passId"] == "routing"):
    sys.stdout.write("{not json")
    sys.exit(0)
if MODE == "non-object":
    sys.stdout.write("[1]")
    sys.exit(0)
if MODE == "duplicate-key":
    sys.stdout.write('{"kind": "a", "kind": "b"}')
    sys.exit(0)
if MODE == "trailing":
    sys.stdout.write(json.dumps(advisory) + " {}")
    sys.exit(0)
if MODE == "extra-option":
    selection["probabilities"]["extra"] = 0.0
if MODE == "missing-option":
    del selection["probabilities"][ids[-1]]
if MODE == "unknown-choice":
    selection["choice"] = "not-an-option"
if MODE == "bad-sum":
    selection["probabilities"] = {ids[0]: 0.9, ids[1]: 0.3}
if MODE == "missing-model":
    del advisory["model"]
if MODE == "wrong-digest":
    advisory["contextDigest"] = "0" * 64
if MODE == "timeout":
    time.sleep(60)
if MODE == "stderr-flood":
    sys.stderr.write("x" * (100 * 1024))
    sys.stderr.flush()
if MODE == "self-modify":
    with open(__file__, "ab") as stream:
        stream.write(b"\\n# edited while running\\n")
text = json.dumps(advisory)
if MODE == "oversized":
    text += " " * (300 * 1024)
sys.stdout.write(text)
if MODE == "exit-nonzero":
    sys.exit(3)
'''
_NAMESPACE = {}
exec(BUILDER, _NAMESPACE)
build_advisory = _NAMESPACE["build"]


def script_source(mode, marker, model, probability):
    header = (f"#!{sys.executable}\nimport hashlib, json, os, sys, time\n"
              f"MODE = {mode!r}\nMARKER = {str(marker)!r}\nMODEL = {model!r}\nPROBABILITY = {probability!r}\n")
    return header + BUILDER + BODY


def jev_answer(projected):
    return build_advisory(projected, "jev-1.14.0", 1.0, canonical_digest(projected))


class FixtureDiscovery:
    """Trusted injected discovery with a controllable Jev availability proof."""
    def __init__(self, workspace, status):
        self.workspace = workspace
        self.version = "fixture-harness"
        self.probes = []
        self.degraded = False
        self.availability = Mock(return_value={"status": status})

    def enumerate(self):
        return [], []


class FixtureConsent:
    executable = Path(sys.executable)
    last_failure = None

    def __init__(self, grant):
        self.grant = grant

    def __call__(self):
        return copy.deepcopy(self.grant)


class FallbackFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.programs = self.root / "programs"
        self.programs.mkdir()
        self.marker = self.root / "marker.jsonl"
        self.grant = {"granted": True, "purposes": list(GROUPS), "sha256": "b" * 64, "tokenBudget": 2_000_000}

    def program(self, mode="ok", *, model="host-model-1", probability=0.9):
        path = self.programs / f"fallback-{mode}-{len(list(self.programs.iterdir()))}.py"
        path.write_text(script_source(mode, self.marker, model, probability))
        path.chmod(0o755)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def invocations(self):
        if not self.marker.exists():
            return []
        return [json.loads(line) for line in self.marker.read_text().splitlines()]

    def assert_reason(self, reason, callback):
        with self.assertRaises(DecisionError) as caught:
            callback()
        self.assertEqual(caught.exception.reason_code, reason)


def projected_context(object_id="object-1"):
    return {"kind": "img2.route-context", "version": 1, "group": "routing",
            "subject": {"runId": "run-1", "objectId": object_id, "revision": 1, "passId": "geometry"},
            "bindings": [{"role": "reference", "sha256": "a" * 64}],
            "candidates": [{"id": "a"}, {"id": "b"}], "observations": {"partCount": 2},
            "requirements": {"criticalFeatures": [], "requiredViews": []},
            "policyVersion": "policy-v1", "questionSetVersion": "route-questions-v3",
            "requestedModel": "jev-1.14.0"}


class HostFallbackTransportTests(FallbackFixture):
    def test_constructor_refuses_unapproved_programs(self):
        path, sha = self.program()
        for bad in ("", "A" * 64, "g" * 64, "a" * 63, None):
            with self.subTest(sha=bad):
                self.assert_reason("fallback-digest-invalid", lambda: HostFallback(path, bad))
        self.assert_reason("fallback-digest-mismatch", lambda: HostFallback(path, "0" * 64))
        self.assert_reason("fallback-command-unsafe", lambda: HostFallback(self.programs / "absent.py", sha))
        self.assert_reason("fallback-command-unsafe", lambda: HostFallback(self.programs, sha))
        plain = self.programs / "not-executable.py"
        plain.write_text("#!/bin/sh\n")
        plain.chmod(0o644)
        self.assert_reason("fallback-command-unsafe",
                           lambda: HostFallback(plain, hashlib.sha256(plain.read_bytes()).hexdigest()))
        for timeout in (0, -1, 301, float("inf"), "5"):
            with self.subTest(timeout=timeout):
                self.assert_reason("fallback-timeout-invalid", lambda: HostFallback(path, sha, timeout=timeout))
        self.assertEqual(self.invocations(), [])

    def test_symlink_command_is_pinned_to_its_resolved_target(self):
        path, sha = self.program()
        link = self.programs / "link.py"
        link.symlink_to(path)
        fallback = HostFallback(link, sha)
        self.assertEqual(fallback.path, path.resolve())
        self.assertEqual(self.invocations(), [])

    def test_well_formed_answer_receives_canonical_context_in_private_scratch_cwd(self):
        path, sha = self.program()
        context = projected_context()
        before = copy.deepcopy(context)
        answer = HostFallback(path, sha)(context)
        self.assertEqual(context, before)
        self.assertEqual(answer, build_advisory(before, "host-model-1", 0.9, canonical_digest(before)))
        (invocation,) = self.invocations()
        self.assertEqual(invocation["argv"], [str(path)])
        self.assertEqual(invocation["stdinSha256"], canonical_digest(before))
        self.assertNotEqual(Path(invocation["cwd"]).resolve(), self.workspace)
        self.assertFalse(Path(invocation["cwd"]).exists())
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_environment_drops_startup_overrides_and_the_jev_credential_but_keeps_the_rest(self):
        path, sha = self.program()
        environment = {"NODE_OPTIONS": "--import unapproved", "PYTHONSTARTUP": "/tmp/unapproved.py",
                       "TYPESAFE_API_KEY": "not-a-real-key", "IMG2_FALLBACK_TEST_MARKER": "kept",
                       SUPERVISED_ENV: "inherited-not-trusted"}
        with patch.dict(os.environ, environment):
            HostFallback(path, sha)(projected_context())
        (invocation,) = self.invocations()
        self.assertEqual(invocation["stripped"], [])
        self.assertEqual(invocation["inherited"], "kept")
        self.assertEqual(invocation["supervised"], "1")
        self.assertEqual(invocation["pgid"], invocation["pid"])
        self.assertNotEqual(invocation["pgid"], os.getpgrp())

    def test_changed_file_is_refused_before_invocation_not_reapproved(self):
        path, sha = self.program()
        fallback = HostFallback(path, sha)
        path.write_text(path.read_text() + "\n# edited after approval\n")
        self.assert_reason("stale-evidence", lambda: fallback(projected_context()))
        self.assertEqual(self.invocations(), [])
        # Restoring the bytes restores the approval; nothing was silently re-pinned.
        path.write_text(path.read_text().replace("\n# edited after approval\n", ""))
        self.assertEqual(fallback(projected_context())["model"], "host-model-1")
        self.assertEqual(len(self.invocations()), 1)

    def test_deleted_file_is_refused_before_invocation(self):
        path, sha = self.program()
        fallback = HostFallback(path, sha)
        path.unlink()
        self.assert_reason("stale-evidence", lambda: fallback(projected_context()))
        self.assertEqual(self.invocations(), [])

    def test_program_that_changes_itself_while_running_is_refused(self):
        path, sha = self.program("self-modify")
        self.assert_reason("fallback-program-changed", lambda: HostFallback(path, sha)(projected_context()))
        self.assertEqual(len(self.invocations()), 1)

    def test_transport_failures_have_distinct_reason_codes(self):
        cases = {"bad-json": "malformed", "non-object": "malformed", "duplicate-key": "malformed",
                 "trailing": "malformed", "exit-nonzero": "provider-error",
                 "oversized": "fallback-output-too-large", "stderr-flood": "fallback-output-too-large"}
        for mode, reason in cases.items():
            with self.subTest(mode=mode):
                path, sha = self.program(mode)
                self.assert_reason(reason, lambda: HostFallback(path, sha)(projected_context()))

    def test_timeout_is_bounded_and_the_process_is_killed(self):
        path, sha = self.program("timeout")
        started = time.monotonic()
        self.assert_reason("timeout", lambda: HostFallback(path, sha, timeout=3)(projected_context()))
        self.assertLess(time.monotonic() - started, 20)
        (invocation,) = self.invocations()
        with self.assertRaises(ProcessLookupError):
            os.kill(invocation["pid"], 0)
        self.assertFalse(Path(invocation["cwd"]).exists())

    def test_semantic_validation_is_left_to_the_controller(self):
        # An option id the context does not offer is not a transport fault: the wrapper
        # returns the object and the controller (below) refuses it.
        path, sha = self.program("extra-option")
        answer = HostFallback(path, sha)(projected_context())
        self.assertIn("extra", answer["answers"]["selection"]["probabilities"])

    def test_initial_program_pin_is_inside_preflight_deadline(self):
        path, sha = self.program()
        fallback = HostFallback(path, sha, timeout=1)
        verify = fallback._verify
        now = [100.0]

        def slow_pin(*args):
            verify(*args)
            now[0] += 2

        with patch.object(fallback, "_verify", side_effect=slow_pin), \
                patch("stage0_route.fallback.time.monotonic", side_effect=lambda: now[0]), \
                patch("stage0_route.fallback.subprocess.Popen") as launch:
            self.assert_reason("fallback-preflight-timeout", lambda: fallback(projected_context()))
        launch.assert_not_called()
        self.assertEqual(self.invocations(), [])


class FallbackImageTransportTests(FallbackFixture):
    """Synthetic pixel transport and real controller consumption, not vision quality."""
    def setUp(self):
        super().setUp()
        self.context = projected_context()
        self.context["bindings"] = []
        self.context["observations"]["images"] = []
        self.sources = {}
        for index, role in enumerate(("reference", "render")):
            raw = png(2, 1, 3, bytes((255, 0, index, 0, 255, index)))
            relative = role + "-private-source.bin"
            (self.workspace / relative).write_bytes(raw)
            self.sources[role] = raw
            self.context["bindings"].append({"role": role, "path": relative,
                "sha256": hashlib.sha256(raw).hexdigest()})
            self.context["observations"]["images"].append({"role": role, "asset": role + "-asset"})

    @staticmethod
    def project(context):
        projected = copy.deepcopy(context)
        projected["bindings"] = [{"role": row["role"], "sha256": row["sha256"]}
                                 for row in context["bindings"]]
        return projected

    def image_program(self):
        # This executable reads actual private images, not mocked Popen arguments.
        source = f"#!{sys.executable}\n" + '''
import hashlib, json, os, pathlib, stat, sys
sys.path.insert(0, FORGE)
sys.path.insert(0, str(pathlib.Path(FORGE) / "_shared"))
from stage0_route.images import decode
raw = sys.stdin.buffer.read()
context = json.loads(raw)
root = pathlib.Path.cwd()
assert root.stat().st_mode & 0o077 == 0
assert sys.argv == [__file__]
path = pathlib.Path(os.environ["IMG2_FALLBACK_IMAGE_MANIFEST"])
assert path == root / "images.json"
with path.open("rb") as stream:
    manifest_raw = stream.read()
assert stream.closed
assert hashlib.sha256(manifest_raw).hexdigest() == os.environ["IMG2_FALLBACK_IMAGE_MANIFEST_SHA256"]
manifest = json.loads(manifest_raw)
assert set(manifest) == {"kind", "version", "contextDigest", "images"}
assert manifest["kind"] == "img2.host-fallback-images" and manifest["version"] == 1
assert manifest["contextDigest"] == hashlib.sha256(raw).hexdigest()
assert all(set(binding) == {"role", "sha256"} for binding in context["bindings"])
assert len(manifest["images"]) == len(context["observations"]["images"])
for index, (image, observation) in enumerate(zip(manifest["images"], context["observations"]["images"])):
    assert image["role"] == observation["role"] and image["asset"] == observation["asset"]
    target = pathlib.Path(image["path"])
    suffix = ".jpg" if SOURCES[image["role"]].startswith(b"\\xff\\xd8") else ".png"
    assert target == root / ("image-" + str(index) + suffix)
    assert stat.S_ISREG(target.lstat().st_mode) and target.stat().st_mode & 0o077 == 0
    # No source or materialization descriptors survive into the approved program.
    for fd in os.listdir("/dev/fd"):
        try:
            inherited = os.readlink("/dev/fd/" + fd)
        except OSError:
            continue
        assert inherited not in {str(target), str(path)}
    with target.open("rb") as stream:
        data = stream.read()
    assert stream.closed
    assert data == SOURCES[image["role"]]
    assert hashlib.sha256(data).hexdigest() == image["sha256"]
    assert image["sha256"] == next(row["sha256"] for row in context["bindings"] if row["role"] == image["role"])
    assert decode(data) == decode(SOURCES[image["role"]])
with open(MARKER, "a") as stream:
    stream.write(json.dumps({"cwd": str(root), "manifest": manifest, "stdin": context,
                             "closed": True}) + "\\n")
print(json.dumps(build(context, "host-image-model", 0.9, manifest["contextDigest"])))
'''
        source = source.replace("FORGE", repr(str(FORGE))).replace("SOURCES", repr(self.sources))
        source = source.replace("MARKER", repr(str(self.marker)))
        # The builder must be defined before the executable body calls it.
        lines = source.splitlines(keepends=True)
        source = lines[0] + BUILDER + "".join(lines[1:])
        path = self.programs / "image-fallback.py"
        path.write_text(source)
        path.chmod(0o700)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_binding_hook_is_deferred_and_projection_stays_unchanged(self):
        path, sha = self.image_program()
        fallback = HostFallback(path, sha)
        before = copy.deepcopy(self.context)
        with patch("stage0_route.fallback._file", side_effect=AssertionError("binding opened a file")), \
                patch("stage0_route.fallback.subprocess.Popen") as launch:
            bound = fallback.bind_context(self.workspace, self.context)
        launch.assert_not_called()
        projected = self.project(before)
        answer = bound(projected)
        self.assertEqual(answer["contextDigest"], canonical_digest(projected))
        self.assertEqual(self.context, before)
        self.assertEqual(projected, self.project(before))
        (call,) = self.invocations()
        self.assertEqual(call["stdin"], projected)
        self.assertTrue(call["closed"])
        self.assertFalse(Path(call["cwd"]).exists())
        for image in call["manifest"]["images"]:
            self.assertFalse(Path(image["path"]).exists())

    def test_expired_image_preflight_never_launches_and_cleans_partial_scratch(self):
        # Deterministic local work expiry: real read/decode/write/hash, no provider.
        for phase in ("source-hash", "decode", "copy", "manifest-hash"):
            with self.subTest(phase=phase):
                path, sha = self.program()
                fallback = HostFallback(path, sha, timeout=1)
                now = [100.0]
                scratch = self.root / ("expired-" + phase)
                real_file = fallback_module._file
                real_suffix = fallback_module._image_suffix
                real_open = Path.open
                real_sha = hashlib.sha256

                def make_scratch(**_):
                    scratch.mkdir(mode=0o700)
                    return str(scratch)

                def read_file(root, *args, **kwargs):
                    result = real_file(root, *args, **kwargs)
                    if phase == "source-hash" and Path(root) == self.workspace:
                        now[0] += 2
                    return result

                def decode_image(data):
                    result = real_suffix(data)
                    if phase == "decode":
                        now[0] += 2
                    return result

                class ExpiringWrite:
                    def __init__(self, stream):
                        self.stream = stream

                    def __enter__(self):
                        self.stream.__enter__()
                        return self

                    def write(self, data):
                        result = self.stream.write(data)
                        now[0] += 2
                        return result

                    def __exit__(self, *args):
                        return self.stream.__exit__(*args)

                def open_file(target, *args, **kwargs):
                    stream = real_open(target, *args, **kwargs)
                    return ExpiringWrite(stream) if phase == "copy" and target.name.startswith("image-") else stream

                def hash_bytes(data=b""):
                    result = real_sha(data)
                    if phase == "manifest-hash" and b"img2.host-fallback-images" in data:
                        now[0] += 2
                    return result

                with patch("stage0_route.fallback.time.monotonic", side_effect=lambda: now[0]), \
                        patch("stage0_route.fallback.tempfile.mkdtemp", side_effect=make_scratch), \
                        patch("stage0_route.fallback._file", side_effect=read_file), \
                        patch("stage0_route.fallback._image_suffix", side_effect=decode_image), \
                        patch.object(Path, "open", open_file), \
                        patch("stage0_route.fallback.hashlib.sha256", side_effect=hash_bytes), \
                        patch("stage0_route.fallback.subprocess.Popen") as launch:
                    bound = fallback.bind_context(self.workspace, self.context)
                    self.assert_reason("fallback-preflight-timeout", lambda: bound(self.project(self.context)))
                launch.assert_not_called()
                self.assertFalse(scratch.exists())
                self.assertEqual(self.invocations(), [])

    def test_actual_controller_uses_hook_then_consumes_image_bound_advisory(self):
        path, sha = self.image_program()
        fallback = HostFallback(path, sha)
        controller = DecisionController(self.workspace, "run-1", clock=lambda: 1000.0)
        provider = Mock(side_effect=AssertionError("No Jev calls"))
        consent = lambda: copy.deepcopy(self.grant)
        record = controller.consult(self.context, provider=provider, fallback=fallback,
            availability=lambda: {"status": "not-installed"}, consent=consent, request_token_limit=128)
        self.assertEqual((record["status"], record["decider"]), ("answered", "model:host-image-model"))
        self.assertEqual(controller.consume(record, self.context, consent=consent), record)
        self.assertEqual(len(self.invocations()), 1)
        provider.assert_not_called()

    def test_jpeg_bytes_keep_the_source_hash_and_use_signature_based_extension(self):
        from forge.tests.test_jpeg import FIXTURE
        self.sources["reference"] = FIXTURE
        binding = self.context["bindings"][0]
        (self.workspace / binding["path"]).write_bytes(FIXTURE)
        binding["sha256"] = hashlib.sha256(FIXTURE).hexdigest()
        path, sha = self.image_program()
        HostFallback(path, sha).bind_context(self.workspace, self.context)(self.project(self.context))
        (call,) = self.invocations()
        image = call["manifest"]["images"][0]
        self.assertTrue(image["path"].endswith("/image-0.jpg"))
        self.assertEqual(image["sha256"], binding["sha256"])
        self.assertFalse(Path(call["cwd"]).exists())

    def test_changed_source_or_projected_digest_refuses_before_program_call(self):
        path, sha = self.program()
        fallback = HostFallback(path, sha)
        bound = fallback.bind_context(self.workspace, self.context)
        projected = self.project(self.context)
        changed = copy.deepcopy(projected)
        changed["bindings"][0]["sha256"] = "0" * 64
        self.assert_reason("stale-evidence", lambda: bound(changed))
        source = self.workspace / self.context["bindings"][0]["path"]
        source.write_bytes(png(1, 1, 3, b"\0\0\0"))
        self.assert_reason("stale-evidence", lambda: bound(projected))
        self.assertEqual(self.invocations(), [])

    def test_missing_duplicate_unknown_bad_hash_and_nonimage_roles_are_not_dropped(self):
        path, sha = self.program()
        fallback = HostFallback(path, sha)
        cases = []
        duplicate = copy.deepcopy(self.context)
        duplicate["observations"]["images"].append({"role": "reference", "asset": "another"})
        cases.append(duplicate)
        duplicate_binding = copy.deepcopy(self.context)
        duplicate_binding["bindings"].append(dict(duplicate_binding["bindings"][0], path="other.png"))
        cases.append(duplicate_binding)
        duplicate_asset = copy.deepcopy(self.context)
        duplicate_asset["observations"]["images"][1]["asset"] = "reference-asset"
        cases.append(duplicate_asset)
        unknown = copy.deepcopy(self.context)
        unknown["observations"]["images"][0]["role"] = "unknown"
        cases.append(unknown)
        missing = copy.deepcopy(self.context)
        missing["bindings"][0]["path"] = "missing.png"
        cases.append(missing)
        bad_hash = copy.deepcopy(self.context)
        bad_hash["bindings"][0]["sha256"] = "0" * 64
        cases.append(bad_hash)
        text = copy.deepcopy(self.context)
        (self.workspace / "local.txt").write_text("private non-image data")
        text["bindings"][0].update(path="local.txt",
            sha256=hashlib.sha256((self.workspace / "local.txt").read_bytes()).hexdigest())
        cases.append(text)
        for name in ("https://example.invalid/image.png", "../outside.png", "/etc/passwd"):
            unsafe = copy.deepcopy(self.context)
            unsafe["bindings"][0]["path"] = name
            cases.append(unsafe)
        link = self.workspace / "link.png"
        link.symlink_to(self.workspace / self.context["bindings"][0]["path"])
        symlink = copy.deepcopy(self.context)
        symlink["bindings"][0]["path"] = "link.png"
        cases.append(symlink)
        for index, context in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(DecisionError):
                fallback.bind_context(self.workspace, context)(self.project(context))
        self.assertEqual(self.invocations(), [])

    def test_count_and_total_byte_limits_refuse_without_sending(self):
        path, sha = self.program()
        fallback = HostFallback(path, sha)
        context = copy.deepcopy(self.context)
        context["observations"]["images"] = [{"role": f"image-{index}", "asset": f"asset-{index}"}
                                             for index in range(65)]
        context["bindings"] = [dict(self.context["bindings"][0], role=image["role"])
                               for image in context["observations"]["images"]]
        self.assert_reason("fallback-image-invalid",
            lambda: fallback.bind_context(self.workspace, context)(self.project(context)))
        with patch("stage0_route.fallback.MAX_IMAGE_BYTES", sum(map(len, self.sources.values())) - 1):
            with self.assertRaises(DecisionError):
                fallback.bind_context(self.workspace, self.context)(self.project(self.context))
        self.assertEqual(self.invocations(), [])

    def test_unbound_images_refuse_and_no_image_calls_clear_inherited_manifest(self):
        path, sha = self.program()
        fallback = HostFallback(path, sha)
        self.assert_reason("fallback-image-context-required", lambda: fallback(self.project(self.context)))
        with patch.dict(os.environ, {IMAGE_MANIFEST_ENV: "/unapproved/stale.json",
                                    IMAGE_MANIFEST_SHA_ENV: "a" * 64}):
            fallback(projected_context())
        (call,) = self.invocations()
        self.assertIsNone(call["imageManifest"])
        self.assertIsNone(call["imageManifestSha"])

    def test_hook_never_runs_when_jev_answers_or_outage_is_unproven(self):
        path, sha = self.image_program()
        fallback = HostFallback(path, sha)
        for index, status in enumerate(("present", "availability-unproven")):
            context = copy.deepcopy(self.context)
            context["subject"]["objectId"] = f"object-{index}"
            controller = DecisionController(self.workspace, "run-1", clock=lambda: 1000.0)
            with patch.object(fallback, "bind_context", side_effect=AssertionError("unselected hook")) as hook:
                controller.consult(context, provider=jev_answer, fallback=fallback,
                    availability=lambda: {"status": status}, consent=lambda: copy.deepcopy(self.grant),
                    request_token_limit=128)
            hook.assert_not_called()
        self.assertEqual(self.invocations(), [])


class FallbackControllerTests(FallbackFixture):
    def setUp(self):
        super().setUp()
        (self.workspace / "reference.png").write_bytes(b"offline reference fixture")
        self.controller = DecisionController(self.workspace, "run-1", clock=lambda: 1000.0)
        self.grant["tokenBudget"] = 100_000
        self.consent = lambda: copy.deepcopy(self.grant)
        self.jev = Mock(side_effect=jev_answer)

    def context(self, object_id="object-1"):
        return {"kind": "img2.route-context", "version": 1, "group": "routing",
                "subject": {"runId": "run-1", "objectId": object_id, "revision": 1, "passId": "geometry"},
                "bindings": [{"role": "reference", "path": "reference.png",
                              "sha256": hashlib.sha256((self.workspace / "reference.png").read_bytes()).hexdigest()}],
                "candidates": [{"id": "a"}, {"id": "b"}], "observations": {"partCount": 2},
                "requirements": {"criticalFeatures": [], "requiredViews": []},
                "policyVersion": "policy-v1", "questionSetVersion": "route-questions-v3",
                "requestedModel": "jev-1.14.0"}

    def consult(self, fallback, *, object_id="object-1", status="not-installed"):
        return self.controller.consult(
            self.context(object_id), provider=self.jev, fallback=fallback,
            availability=lambda: {"status": status}, consent=self.consent, request_token_limit=128)

    def events(self, name):
        lines = self.controller.journal_path.read_text().splitlines()
        return [entry["data"] for entry in map(json.loads, lines) if entry["event"] == name]

    def test_well_formed_answer_is_accepted_and_journalled_with_model_decider(self):
        path, sha = self.program()
        record = self.consult(HostFallback(path, sha))
        self.assertEqual((record["status"], record["reasonCode"]), ("answered", "answered"))
        self.assertEqual(record["decider"], "model:host-model-1")
        self.assertEqual(record["model"], "host-model-1")
        self.assertEqual(record["unavailableReason"], "not-installed")
        self.assertEqual(record["answers"]["selection"]["choice"], "a")
        self.jev.assert_not_called()
        self.assertEqual(len(self.invocations()), 1)
        self.assertEqual(self.controller.consume(record, self.context(), consent=self.consent), record)
        self.assertTrue(any(item.get("fallbackAuthorized") for item in self.events("probe")))
        self.assertEqual([item["decider"] for item in self.events("attempt-pending")], ["model"])
        saved = json.loads((self.workspace / ".img2threejs/decisions/routing" / (record["recordId"] + ".json")).read_text())
        self.assertEqual(saved, record)

    def test_schema_violations_are_refused_by_the_controller_and_recorded_as_fallback_invalid(self):
        for mode in ("extra-option", "missing-option", "unknown-choice", "bad-sum", "missing-model",
                     "bad-json", "non-object", "duplicate-key", "trailing"):
            with self.subTest(mode=mode):
                path, sha = self.program(mode)
                record = self.consult(HostFallback(path, sha), object_id="object-" + mode)
                self.assertEqual((record["status"], record["reasonCode"]), ("request-input", "fallback-invalid"))
                self.assertIsNone(record["decider"])
                self.assertEqual(record["answers"], {})
                self.assertEqual(record["unavailableReason"], "not-installed")
        self.jev.assert_not_called()

    def test_probability_floor_is_enforced_by_the_controller(self):
        below, sha_below = self.program(probability=0.81)
        record = self.consult(HostFallback(below, sha_below), object_id="below")
        self.assertEqual((record["status"], record["reasonCode"]), ("request-input", "choice-uncertain"))
        self.assertEqual(record["decider"], "model:host-model-1")
        at_floor, sha_floor = self.program(probability=0.82)
        record = self.consult(HostFallback(at_floor, sha_floor), object_id="floor")
        self.assertEqual((record["status"], record["decider"]), ("answered", "model:host-model-1"))

    def test_controller_error_types_are_not_swallowed(self):
        path, sha = self.program("wrong-digest")
        self.assert_reason("context-digest-mismatch", lambda: self.consult(HostFallback(path, sha)))

    def test_changed_program_is_refused_before_invocation_and_releases_the_reservation(self):
        path, sha = self.program()
        fallback = HostFallback(path, sha)
        path.write_text(path.read_text() + "\n# edited after approval\n")
        record = self.consult(fallback)
        self.assertEqual((record["status"], record["reasonCode"]), ("request-input", "fallback-invalid"))
        self.assertEqual(self.invocations(), [])
        (failure,) = self.events("attempt-failure")
        self.assertEqual((failure["reasonCode"], failure["tokens"], failure["release"]), ("stale-evidence", 0, 128))

    def test_known_unsent_preflight_failures_release_budget_and_survive_reopen(self):
        for mode in ("invalid-image", "expired-pin"):
            with self.subTest(mode=mode):
                path, sha = self.program()
                fallback = HostFallback(path, sha, timeout=1)
                context = self.context("unsent-" + mode)
                if mode == "invalid-image":
                    context["observations"]["images"] = [{"role": "reference", "asset": "source"}]
                arguments = dict(provider=self.jev, fallback=fallback,
                    availability=lambda: {"status": "not-installed"}, consent=self.consent, request_token_limit=128)
                verify = fallback._verify
                now = [100.0]

                def pin(*args):
                    verify(*args)
                    if mode == "expired-pin":
                        now[0] += 2

                with patch.object(fallback, "_verify", side_effect=pin), \
                        patch("stage0_route.fallback.time.monotonic", side_effect=lambda: now[0]), \
                        patch("stage0_route.fallback.subprocess.Popen") as launch:
                    record = self.controller.consult(context, **arguments)
                launch.assert_not_called()
                reason = "fallback-image-invalid" if mode == "invalid-image" else "fallback-preflight-timeout"
                self.assertEqual((record["status"], record["reasonCode"]), ("request-input", "fallback-invalid"))
                for active in (self.controller, DecisionController(self.workspace, "run-1", clock=lambda: 1000.0)):
                    with active._locked():
                        entries, state = active._load()
                    run = state["runs"]["run-1"]
                    self.assertEqual((run["tokensUsed"], run["tokensReserved"]), (0, 0))
                    failures = [entry["data"] for entry in entries if entry["event"] == "attempt-failure"
                                and entry["data"]["reasonCode"] == reason]
                    self.assertEqual(len(failures), 1)
                    self.assertEqual((failures[0]["tokens"], failures[0]["release"]), (0, 128))
                    self.assertEqual(active.consult(context, **arguments), record)
                self.assertEqual(self.invocations(), [])
        self.jev.assert_not_called()

    def test_timeout_exit_and_oversize_journal_distinct_failures_without_retry(self):
        expected = {"timeout": "timeout", "exit-nonzero": "provider-error", "oversized": "malformed"}
        for mode, reason in expected.items():
            with self.subTest(mode=mode):
                path, sha = self.program(mode)
                fallback = HostFallback(path, sha, timeout=1 if mode == "timeout" else 60)
                record = self.consult(fallback, object_id="object-" + mode)
                self.assertEqual((record["status"], record["reasonCode"]), ("request-input", "fallback-invalid"))
                self.assertEqual(self.events("attempt-failure")[-1]["reasonCode"], reason)
                calls = len(self.invocations())
                again = self.consult(fallback, object_id="object-" + mode)
                self.assertEqual(again["reasonCode"], "fallback-invalid")
                self.assertEqual(len(self.invocations()), calls)

    def test_jev_answer_never_invokes_the_fallback(self):
        path, sha = self.program()
        record = self.consult(HostFallback(path, sha), status="present")
        self.assertEqual(record["decider"], "jev:jev-1.14.0")
        self.assertNotIn("unavailableReason", record)
        self.assertEqual(self.jev.call_count, 1)
        self.assertEqual(self.invocations(), [])

    def test_uncertain_jev_never_invokes_the_fallback(self):
        path, sha = self.program()
        self.jev.side_effect = lambda projected: build_advisory(projected, "jev-1.14.0", 0.6, canonical_digest(projected))
        record = self.consult(HostFallback(path, sha), status="present")
        self.assertEqual(record["status"], "uncertain")
        self.assertEqual(self.invocations(), [])

    def test_unproven_availability_never_invokes_the_fallback(self):
        path, sha = self.program()
        record = self.consult(HostFallback(path, sha), status="availability-unproven")
        self.assertEqual((record["status"], record["reasonCode"]), ("request-input", "availability-unproven"))
        self.jev.assert_not_called()
        self.assertEqual(self.invocations(), [])


class FallbackCliTests(FallbackFixture):
    def setUp(self):
        super().setUp()
        (self.workspace / "image.png").write_bytes(png(16, 16, 3, bytes(range(256)) * 3))
        (self.workspace / "objects.json").write_text(json.dumps({
            "kind": "img2.object-proposals", "version": 1, "objects": [
                {"objectId": "object-1", "label": "cube", "role": "prop", "approxBox": [0.1, 0.1, 0.6, 0.9],
                 "observations": {"flatness": "volumetric"}}]}))

    def args(self, *extra):
        return ["--decider", "jev", "--workspace", str(self.workspace), "--run-id", "run-1", "--image", "image.png",
                "--objects", "objects.json", *extra]

    def run_cli(self, extra=(), *, status="capability-unavailable"):
        stdout = io.StringIO()
        jev = Mock(side_effect=jev_answer)
        with patch.object(route_cli, "_approved_lock", return_value={"resolvedSha": "a" * 40}), \
                patch.object(route_cli, "PinnedDiscovery",
                             side_effect=lambda controller: FixtureDiscovery(controller.workspace, status)), \
                patch.object(route_cli, "LiveConsent", side_effect=lambda *a, **k: FixtureConsent(self.grant)), \
                patch.object(route_cli, "JevProviderFactory") as factory, \
                patch.object(route_cli, "DecisionController", wraps=route_cli.DecisionController) as controller, \
                patch.object(route_cli, "Router", wraps=route_cli.Router) as router, \
                redirect_stdout(stdout):
            factory.return_value.provider.return_value = jev
            code = route_cli.main(self.args(*extra))
        return code, json.loads(stdout.getvalue()), router, controller, jev

    def flags(self, mode="ok"):
        path, sha = self.program(mode)
        return ["--fallback-command", str(path), "--fallback-sha256", sha], path, sha

    def parse_error(self, argv):
        stderr = io.StringIO()
        with patch.object(route_cli, "DecisionController") as controller, redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                route_cli.main(argv)
        self.assertEqual(caught.exception.code, 2)
        controller.assert_not_called()
        return stderr.getvalue()

    def decision(self, output):
        return json.loads((self.workspace / output["decisionRef"]).read_text())

    def test_no_flags_leaves_fallback_unbound_and_the_outage_ends_as_before(self):
        code, output, router, _, jev = self.run_cli()
        self.assertIsNone(router.call_args.kwargs["fallback"])
        self.assertEqual(code, 3)
        self.assertEqual(output["status"], "request-input")
        self.assertNotIn("fallbackDecisions", output)
        self.assertFalse(output["executionAuthorized"])
        jev.assert_not_called()
        self.assertEqual(self.invocations(), [])

    def test_one_flag_alone_is_a_parser_error(self):
        _, path, sha = self.flags()
        for argv in (self.args("--fallback-command", str(path)), self.args("--fallback-sha256", sha)):
            with self.subTest(argv=argv[-2:]):
                self.assertIn("must be supplied together", self.parse_error(argv))
        self.assertEqual(self.invocations(), [])

    def test_malformed_digest_and_child_completion_combination_are_parser_errors(self):
        _, path, sha = self.flags()
        self.parse_error(self.args("--fallback-command", str(path), "--fallback-sha256", sha.upper()))
        self.parse_error(self.args("--fallback-command", str(path), "--fallback-sha256", "abc"))
        self.parse_error(["--workspace", str(self.workspace), "--run-id", "run-1", "--complete-child", "object-1",
                          "--decision-ref", ".img2threejs/route/decision-" + "c" * 64 + ".json",
                          "--fallback-command", str(path), "--fallback-sha256", sha])

    def test_digest_mismatch_is_refused_before_the_controller_or_any_invocation(self):
        _, path, _ = self.flags()
        code, output, router, controller, jev = self.run_cli(
            ["--fallback-command", str(path), "--fallback-sha256", "0" * 64])
        self.assertEqual((code, output["status"], output["reasonCode"]), (3, "request-input", "fallback-digest-mismatch"))
        controller.assert_not_called()
        router.assert_not_called()
        self.assertEqual(self.invocations(), [])

    def test_missing_or_non_executable_command_is_refused(self):
        code, output, _, controller, _ = self.run_cli(
            ["--fallback-command", str(self.programs / "absent.py"), "--fallback-sha256", "0" * 64])
        self.assertEqual((code, output["reasonCode"]), (3, "fallback-command-unsafe"))
        controller.assert_not_called()
        plain = self.programs / "plain.py"
        plain.write_text("#!/bin/sh\n")
        plain.chmod(0o644)
        code, output, _, controller, _ = self.run_cli(
            ["--fallback-command", str(plain), "--fallback-sha256", hashlib.sha256(plain.read_bytes()).hexdigest()])
        self.assertEqual((code, output["reasonCode"]), (3, "fallback-command-unsafe"))
        controller.assert_not_called()

    def test_well_formed_fallback_decides_and_the_cli_reports_the_model_decider(self):
        extra, path, sha = self.flags()
        code, output, router, _, jev = self.run_cli(extra)
        fallback = router.call_args.kwargs["fallback"]
        self.assertIsInstance(fallback, HostFallback)
        self.assertEqual((fallback.path, fallback.sha256), (path.resolve(), sha))
        self.assertEqual((code, output["status"]), (0, "decided"))
        self.assertFalse(output["executionAuthorized"])
        jev.assert_not_called()
        runs = self.invocations()
        self.assertEqual([run["passId"] for run in runs], ["decomposition", "routing"])
        self.assertTrue(all(Path(run["cwd"]).resolve() != self.workspace for run in runs))
        reported = output["fallbackDecisions"]
        self.assertEqual(len(reported), 2)
        for item in reported:
            self.assertEqual(item["decider"], "model:host-model-1")
            self.assertEqual((item["unavailableReason"], item["outcome"], item["reasonCode"]),
                             ("capability-unavailable", "answered", "answered"))
            record = json.loads((self.workspace / ".img2threejs/decisions/routing" / (item["recordId"] + ".json")).read_text())
            self.assertEqual((record["decider"], record["unavailableReason"]),
                             (item["decider"], item["unavailableReason"]))
        row = self.decision(output)["objects"][0]
        self.assertEqual(row["decider"], "model:host-model-1")
        self.assertEqual(row["status"], "resolved")
        self.assertEqual({c["decider"] for c in row["consultations"]}, {"model:host-model-1"})

    def test_failed_fallback_is_visible_and_never_decides(self):
        extra, _, _ = self.flags("bad-json-routing")
        code, output, _, _, _ = self.run_cli(extra)
        self.assertEqual((code, output["status"], output["reasonCode"]), (3, "request-input", "fallback-invalid"))
        reported = output["fallbackDecisions"]
        self.assertEqual([item["decider"] for item in reported], ["model:host-model-1", None])
        self.assertEqual(reported[1]["reasonCode"], "fallback-invalid")
        self.assertEqual(reported[1]["unavailableReason"], "capability-unavailable")
        self.assertEqual(len(self.invocations()), 2)

    def test_jev_answer_never_invokes_the_configured_fallback(self):
        extra, _, _ = self.flags()
        code, output, _, _, jev = self.run_cli(extra, status="present")
        self.assertEqual((code, output["status"]), (0, "decided"))
        self.assertNotIn("fallbackDecisions", output)
        self.assertEqual(jev.call_count, 2)
        self.assertEqual(self.invocations(), [])
        row = self.decision(output)["objects"][0]
        self.assertTrue(row["decider"].startswith("jev:"))

    def test_unproven_outage_never_invokes_the_configured_fallback(self):
        extra, _, _ = self.flags()
        code, output, _, _, jev = self.run_cli(extra, status="availability-unproven")
        self.assertEqual((code, output["status"]), (3, "request-input"))
        self.assertNotIn("fallbackDecisions", output)
        jev.assert_not_called()
        self.assertEqual(self.invocations(), [])


if __name__ == "__main__":
    unittest.main()
