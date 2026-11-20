#!/usr/bin/env python3
"""Decision-only Router CLI; never initializes children or authorizes execution.

Run ``python3 forge/route.py --workspace WORKSPACE --run-id RUN --image ref.png
--objects objects.json``. Image and object paths are workspace-relative. Real Jev
image disclosure and paid use require a separate user-run four-purpose consent
command; this CLI never enrolls, revokes, prompts for keys, or selects a fallback.
Only the base-owned reviewed release lock is accepted; there is no lock override.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "_shared"))

from decision_control import DecisionController, DecisionError, GROUPS
from jev_cli_bridge import REQUIRED_PROVIDER_FILES, _file, _json
from jev_provider_factory import JevProviderFactory
from stage0_route.discovery import Discovery, JEV_EDGE
from stage0_route.route import Router


_AUTH = {"credential-missing", "credential-rejected", "credential-storage-error"}
_PROBE_FAILURES = _AUTH | {"consent-required", "consent-declined", "consent-changed",
    "consent-storage-error", "budget-exhausted", "budget-storage-error", "timeout",
    "provider-error", "malformed", "provider-unpinned", "provider-pin-mismatch",
    "provider-contract-unproven", "unsafe-provider-path", "stale-evidence",
    "capability-unavailable", "availability-unproven", "unsafe-path"}
_SAFE_ERRORS = _PROBE_FAILURES | {"invalid-routing-input", "invalid-routing-result",
    "workspace-busy", "lease-lost", "invalid-request", "invalid-object-proposals",
    "objects-required", "context-fault", "evidence-missing", "invalid-json",
    "context-too-large", "consent-probe-error", "routing-suspended", "routing-failed"}
_HEX = re.compile(r"[0-9a-f]{64}\Z")


def _fail(code):
    raise DecisionError(code) from None


def _approved_lock():
    # Fixed base path, never the workspace/plugin/environment or a CLI argument.
    value = _json(_file(ROOT / "_shared", "jev_release_lock.json", maximum=128 * 1024, capture=True))
    if (type(value) is not dict or set(value) != {"resolvedSha", "files", "contractVersion"}
            or value["contractVersion"] != "jev-v4-four-decisions"
            or type(value["resolvedSha"]) is not str
            or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value["resolvedSha"]) is None
            or type(value["files"]) is not dict or not REQUIRED_PROVIDER_FILES <= value["files"].keys()):
        _fail("provider-contract-unproven")
    for relative, sha in value["files"].items():
        if (type(relative) is not str or "\\" in relative
                or any(part in ("", ".", "..") for part in relative.split("/"))
                or type(sha) is not str or not _HEX.fullmatch(sha)):
            _fail("provider-contract-unproven")
    return value


def _run_bytes(argv, workspace, *, timeout=15, maximum=64 * 1024, env=None):
    """Read-only bounded runner; raw child output never reaches terminal or logs."""
    process = None
    try:
        process = subprocess.Popen(argv, cwd=workspace, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                   shell=False, start_new_session=True)
        deadline, output, size = time.monotonic() + timeout, bytearray(), 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _fail("timeout")
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    size += len(chunk)
                    if size > maximum:
                        _fail("malformed")
                    if key.fileobj is process.stdout:
                        output.extend(chunk)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail("timeout")
            process.wait(timeout=remaining)
        return process.returncode, bytes(output)
    except subprocess.TimeoutExpired:
        _fail("timeout")
    except OSError:
        _fail("provider-error")
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            process.stdout.close()
            process.stderr.close()


def _run_cli(argv, workspace, *, timeout=15):
    code, raw = _run_bytes(argv, workspace, timeout=timeout)
    return code, _json(raw)


class LiveConsent:
    """Live pinned local auth/consent probes; no synthetic or enrolled grants."""
    def __init__(self, discovery, approved_release, run_id, executable):
        self.discovery = discovery
        self.release = copy.deepcopy(approved_release)
        self.run_id = run_id
        self.executable = Path(executable).resolve(strict=True)
        self.executable_sha = self._interpreter_sha()
        self.last_failure = None
        self.row = None
        git = shutil.which("git")
        if git is None:
            _fail("provider-contract-unproven")
        self.git = Path(git).resolve(strict=True)
        self.git_sha = hashlib.sha256(_file(self.git.parent, self.git.name,
                                            maximum=256 * 1024 * 1024, capture=True)).hexdigest()
        self.git_env = {"PATH": os.defpath, "LANG": "C.UTF-8"}
        self.git_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                            GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1")

    def _interpreter_sha(self):
        return hashlib.sha256(_file(self.executable.parent, self.executable.name,
                                    maximum=256 * 1024 * 1024, capture=True)).hexdigest()

    def verify(self):
        row = copy.deepcopy(self.discovery.jev)
        if self.discovery.jev_status != "present":
            _fail("capability-unavailable" if self.discovery.jev_status == "capability-unavailable"
                  else "availability-unproven")
        if (type(row) is not dict or row.get("plugin") != "jev"
                or row.get("resolvedSha") != self.release["resolvedSha"] or type(row.get("dir")) is not str):
            _fail("provider-unpinned")
        root = Path(row["dir"])
        try:
            if (not root.is_absolute() or root.resolve(strict=True) != root or not root.is_dir()
                    or root == self.discovery.workspace or root in self.discovery.workspace.parents
                    or self.discovery.workspace in root.parents):
                _fail("unsafe-provider-path")
        except OSError:
            _fail("unsafe-provider-path")
        # This callback also runs inside controller workspace transactions. Never
        # call Discovery.run/query/_pinned_file here: those can lock its journal.
        # The row comes from Router's earlier trusted discovery, not provider text.
        if self.row is None:
            self.row = copy.deepcopy(row)
        elif self.row != row:
            _fail("provider-pin-mismatch")
        if hashlib.sha256(_file(self.git.parent, self.git.name,
                                maximum=256 * 1024 * 1024, capture=True)).hexdigest() != self.git_sha:
            _fail("provider-pin-mismatch")
        if self._interpreter_sha() != self.executable_sha:
            _fail("provider-pin-mismatch")
        for relative, sha in self.release["files"].items():
            _file(root, relative, maximum=64 * 1024 * 1024, expected=sha)
            code, raw = _run_bytes(
                [str(self.git), "--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false",
                 "-C", str(root), "show", self.release["resolvedSha"] + ":" + relative],
                self.discovery.workspace, maximum=64 * 1024 * 1024, env=self.git_env)
            if code != 0 or hashlib.sha256(raw).hexdigest() != sha:
                _fail("provider-pin-mismatch")
        return row, root

    def _probe(self, arguments):
        row, root = self.verify()
        try:
            result = _run_cli([str(self.executable), str(root / "tools/jev.py"), *arguments],
                              self.discovery.workspace)
        finally:
            current, _ = self.verify()
            if current != row:
                _fail("provider-pin-mismatch")
        code, value = result
        if (type(value) is not dict or value.get("kind") != "img2.jev-command"
                or type(value.get("version")) is not int or value["version"] != 1):
            _fail("malformed")
        return code, value

    def auth_status(self):
        code, value = self._probe(["auth-status"])
        expected = {0: ("present", "credential-present"), 3: ("missing", "credential-missing"),
                    1: ("storage-error", "credential-storage-error")}.get(code)
        if (expected is None or set(value) != {"kind", "version", "status", "reasonCode"}
                or (value["status"], value["reasonCode"]) != expected):
            _fail("malformed")
        return {"status": "present" if code == 0 else expected[1]}

    def __call__(self):
        try:
            auth = self.auth_status()["status"]
            if auth != "present":
                _fail(auth)
            code, value = self._probe(["consent", "decision-status", "--workspace", str(self.discovery.workspace),
                                       "--run-id", self.run_id])
            if code != 0:
                reason = value.get("reasonCode")
                _fail(reason if type(reason) is str and reason in _PROBE_FAILURES else "malformed")
            if (set(value) != {"kind", "version", "status", "granted", "purposes", "sha256", "tokenBudget"}
                    or value["status"] != "consent-updated" or value["granted"] is not True
                    or type(value["purposes"]) is not list
                    or any(type(p) is not str for p in value["purposes"])
                    or len(value["purposes"]) != len(GROUPS) or set(value["purposes"]) != set(GROUPS)
                    or type(value["sha256"]) is not str or not _HEX.fullmatch(value["sha256"])
                    or type(value["tokenBudget"]) is not int or not 0 < value["tokenBudget"] <= 2**53 - 1):
                _fail("malformed")
            self.last_failure = None
            return {key: copy.deepcopy(value[key]) for key in ("granted", "purposes", "sha256", "tokenBudget")}
        except DecisionError as error:
            self.last_failure = error.reason_code if error.reason_code in _PROBE_FAILURES else "provider-error"
            raise DecisionError(self.last_failure) from None


class PinnedDiscovery(Discovery):
    """Use the same verified installed CLI for auth, not an unrelated PATH entry."""
    consent_probe = None

    def availability(self):
        if self.jev_status != "present":
            return {"status": self.jev_status}
        if self.consent_probe is None:
            return {"status": "availability-unproven"}
        try:
            response = self.query(JEV_EDGE)
            if (type(response) is not dict or response.get("status") != "answered"
                    or response.get("providers") != [self.jev]):
                return {"status": "availability-unproven"}
            return self.consent_probe.auth_status()
        except DecisionError as error:
            return {"status": error.reason_code if error.reason_code in _AUTH else "availability-unproven"}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--image", required=True, help="Workspace-relative PNG or baseline JPEG")
    parser.add_argument("--objects", help="Workspace-relative img2.object-proposals v1 JSON (required for 3D)")
    parser.add_argument("--expected-output", choices=("2d", "3d"), default="3d")
    parser.add_argument("--intent", action="append", default=[])
    parser.add_argument("--explicit-route")
    return parser


def _output(*, reason=None, result=None):
    value = {"kind": "img2.route-command", "version": 1, "mode": "decision-only",
             "executionAuthorized": False, "status": "request-input"}
    if result is not None:
        ref, sha = result.get("decisionRef"), result.get("sha256")
        if (type(ref) is not str or re.fullmatch(r"\.img2threejs/route/decision-[0-9a-f]{64}\.json", ref) is None
                or type(sha) is not str or not _HEX.fullmatch(sha)):
            _fail("invalid-routing-result")
        value.update(decisionRef=ref, sha256=sha)
        phase = result.get("decision", {}).get("phase")
        if phase == "decided" and reason is None:
            value["status"] = "decided"
        else:
            suspended = result.get("decision", {}).get("suspendedAt")
            detail = suspended.get("reasonCode") if type(suspended) is dict else None
            reason = reason or (detail if type(detail) is str and detail in _SAFE_ERRORS else "routing-suspended")
    if reason:
        value["reasonCode"] = reason
    if reason in _AUTH:
        value["requestedCommand"] = "img2-jev login"
    elif reason in {"consent-required", "consent-declined"}:
        value["requestedCommand"] = "img2-jev consent authorize-decisions"
        value["requiredFlags"] = ["--workspace", "--run-id", "--token-budget",
                                  "--accept-image-disclosure", "--accept-paid-calls"]
        value["purposes"] = list(GROUPS)
    return value


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.expected_output == "3d" and args.objects is None:
        parser.error("--objects is required for --expected-output 3d")
    consent = None
    try:
        workspace = args.workspace.resolve(strict=True)
        _file(workspace, args.image, maximum=64 * 1024 * 1024)
        objects = _json(_file(workspace, args.objects, maximum=256 * 1024, capture=True)) if args.objects else None
        release = _approved_lock()
        controller = DecisionController(workspace, args.run_id)
        discovery = PinnedDiscovery(controller)
        consent = LiveConsent(discovery, release, args.run_id, Path(sys.executable))
        discovery.consent_probe = consent
        factory = JevProviderFactory(discovery, approved_release=release, run_id=args.run_id,
                                     consent=consent, python_executable=consent.executable)
        router = Router(workspace, args.run_id, controller=controller, discovery=discovery,
                        consent=consent, provider_factory=factory, fallback=None)
        result = router.route(image=args.image, objects=objects, expected_output=args.expected_output,
                              intent=args.intent, explicit_route=args.explicit_route)
        value = _output(reason=consent.last_failure, result=result)
    except Exception as error:
        reported = getattr(error, "reason_code", None)
        reason = reported if type(reported) is str and reported in _SAFE_ERRORS else "invalid-routing-input"
        if reason == "consent-probe-error" and consent is not None and consent.last_failure:
            reason = consent.last_failure
        value = _output(reason=reason)
    print(json.dumps(value, sort_keys=True))
    return 0 if value["status"] == "decided" else 3


if __name__ == "__main__":
    raise SystemExit(main())
