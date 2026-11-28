"""Pinned, no-shell Jev CLI callback for DecisionController (Python 3.10+).

The trusted discovery caller supplies the canonical provider root, resolved Git
SHA, and a complete file->SHA256 inventory established against that release.
``attest_contract()`` must independently attest that exact inventory as supporting
v4 four-purpose consent and a durable shared 64k reservation before every send.
Neither a model, a CLI's self-description, nor this module can establish package
provenance. External interpreter/core/dependency integrity remains the installer's
responsibility. No plugin Python is imported here.

``consent()`` is the same live program-owned callback passed to the controller.
The CLI's read-only decision-status must agree with it before a paid command.
The bridge never grants consent, retries, falls back, or changes a returned digest.
CLI and base retain distinct attempt journals; this is not a unified transaction.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import stat
import subprocess
import time

from decision_control import DecisionController, DecisionError, GROUPS, canonical_digest, placement_record_facts


EDGES = {
    group: (kind, kind.removesuffix("context") + "advisory", "jev-decision-" + group)
    for group, (kind, _) in GROUPS.items()
}
# All plugin modules imported by the supported CLI, including its legacy imports.
REQUIRED_PROVIDER_FILES = frozenset({"plugin.json", "steps.json"} | {
    "tools/" + name + ".py" for name in (
        "jev", "jev_auth", "jev_consent", "jev_contracts", "jev_client",
        "jev_policy", "jev_transport", "jev_outcome_report", "jev_checkpoint",
        "jev_checkpoint_client", "jev_projection", "jev_decision_questions",
        "jev_decision_provider", "jev_decision_transport", "jev_decision_images",
        "jev_character_material",
    )
})
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_MAX_CONTEXT = 256 * 1024
_MAX_ASSETS = 48 * 1024 * 1024
_MAX_OUTPUT = 512 * 1024
_ERROR_CODES = frozenset({
    "credential-missing", "credential-rejected", "credential-storage-error",
    "consent-required", "consent-declined", "consent-changed", "consent-storage-error",
    "budget-exhausted", "budget-storage-error", "ambiguous-paid-attempt",
    "timeout", "provider-error", "malformed", "out-of-set", "context-fault",
    "missing-evidence", "vision-forbidden", "permission-denied", "stale-evidence",
    "invalid-assets", "image-dependency-unavailable", "cache-fault",
    "provider-token-bound-exceeded",
    "invalid-credential", "request-too-large", "credential-in-context",
    "missing-placement-evidence", "consent-busy",
})


def _fail(reason):
    raise DecisionError(reason) from None


def _sha(value):
    if type(value) is not str or not _HEX.fullmatch(value):
        _fail("context-fault")
    return value


def _relative(value):
    if (type(value) is not str or not value or "\\" in value
            or any(ord(ch) < 32 for ch in value)
            or any(part in ("", ".", "..") for part in value.split("/"))):
        _fail("unsafe-path")
    return value.split("/")


def _file(root, relative, *, maximum, expected=None, capture=False):
    """Read through descriptor-relative, no-follow opens; refuse FIFOs/devices."""
    parts = _relative(relative)
    directory = descriptor = None
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            _fail("context-fault")
        digest, chunks, size = hashlib.sha256(), [], 0
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            while chunk := stream.read(65536):
                size += len(chunk)
                if size > maximum:
                    _fail("context-fault")
                digest.update(chunk)
                if capture:
                    chunks.append(chunk)
        if expected is not None and digest.hexdigest() != _sha(expected):
            _fail("stale-evidence")
        return b"".join(chunks) if capture else None
    except OSError:
        _fail("unsafe-path")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def _json(raw, *, asset_manifest=False):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail("malformed")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        checked = value
        if asset_manifest:
            # Image manifests have a separate 48MiB byte envelope. Only their
            # base64 image bodies are exempt from the context JSON string/byte
            # limits; metadata still uses the ordinary strict validator.
            if type(value) is not dict or len(value) > 32:
                _fail("malformed")
            checked = {}
            for name, image in value.items():
                if (type(image) is not dict or type(image.get("data_base64")) is not str
                        or len(image["data_base64"]) > 4 * ((16 * 1024 * 1024 + 2) // 3)):
                    _fail("malformed")
                checked[name] = {key: item for key, item in image.items() if key != "data_base64"}
        canonical_digest(checked)
        return value
    except (ValueError, UnicodeError, RecursionError):
        _fail("malformed")


class JevCliBridge:
    """Callable provider; arguments are trusted configuration, never model output.

    ``entrypoint`` is relative to ``provider_root`` and included in ``provider_files``.
    With no ``executable`` it is executed directly; otherwise the absolute trusted
    interpreter runs it. Paths must be canonical (resolve interpreter symlinks at
    discovery). ``context_sha256``/``assets_sha256`` bind exact persisted bytes.
    """

    def __init__(self, workspace, *, provider_root, resolved_sha, provider_files,
                 entrypoint, executable=None, edge, step_id, context, context_path,
                 context_sha256, consent, attest_contract, assets_path=None,
                 assets_sha256=None, timeout=30.0):
        try:
            self.workspace = Path(workspace).resolve(strict=True)
            self.provider_root = Path(provider_root)
            if (not self.workspace.is_dir() or not self.provider_root.is_absolute()
                    or self.provider_root.resolve(strict=True) != self.provider_root
                    or not self.provider_root.is_dir()
                    or self.provider_root == self.workspace
                    or self.provider_root in self.workspace.parents
                    or self.workspace in self.provider_root.parents):
                _fail("unsafe-provider-path")
            if executable is not None:
                executable = Path(executable)
                if (not executable.is_absolute() or executable.resolve(strict=True) != executable
                        or not executable.is_file() or not os.access(executable, os.X_OK)):
                    _fail("unsafe-provider-path")
        except OSError:
            _fail("unsafe-provider-path")
        if (type(resolved_sha) is not str
                or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", resolved_sha) is None
                or type(provider_files) is not dict
                or not REQUIRED_PROVIDER_FILES <= provider_files.keys()):
            _fail("provider-unpinned")
        self.resolved_sha = resolved_sha
        self.provider_files = copy.deepcopy(provider_files)
        for relative, sha in self.provider_files.items():
            _relative(relative)
            _sha(sha)
        _relative(entrypoint)
        if entrypoint not in self.provider_files:
            _fail("provider-unpinned")
        self.entrypoint, self.executable = entrypoint, executable
        self.inventory_digest = canonical_digest({"root": str(self.provider_root),
                                                  "resolvedSha": resolved_sha,
                                                  "files": self.provider_files})
        self.context = copy.deepcopy(context)
        canonical_digest(self.context)
        if type(self.context) is not dict or self.context.get("group") not in EDGES:
            _fail("context-fault")
        self.group = self.context["group"]
        expected = EDGES[self.group]
        if (type(edge) not in (list, tuple) or tuple(edge) != expected[:2]
                or step_id != expected[2] or self.context.get("kind") != expected[0]):
            _fail("capability-unavailable")
        self.context_path, self.context_sha256 = context_path, _sha(context_sha256)
        _relative(context_path)
        if (assets_path is None) != (assets_sha256 is None):
            _fail("context-fault")
        self.assets_path, self.assets_sha256 = assets_path, assets_sha256
        if assets_path is not None:
            _relative(assets_path)
            _sha(assets_sha256)
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 < timeout <= 30):
            _fail("context-fault")
        self.timeout = timeout
        self.consent, self.attest_contract = consent, attest_contract
        self._send_evidence = None

    @property
    def send_evidence(self):
        """Detached transport facts, published only after post-send trust checks."""
        return copy.deepcopy(self._send_evidence)

    def _grant(self):
        if not callable(self.consent):
            _fail("consent-required")
        try:
            grant = self.consent()
        except Exception:
            _fail("consent-required")
        if (type(grant) is not dict
                or set(grant) != {"granted", "purposes", "sha256", "tokenBudget"}
                or grant["granted"] is not True
                or type(grant["purposes"]) is not list
                or len(grant["purposes"]) != len(GROUPS)
                or any(type(p) is not str for p in grant["purposes"])
                or set(grant["purposes"]) != set(GROUPS)):
            _fail("consent-required")
        _sha(grant["sha256"])
        if type(grant["tokenBudget"]) is not int or grant["tokenBudget"] < 64000:
            _fail("budget-exhausted")
        return copy.deepcopy(grant)

    def _package(self):
        if not callable(self.attest_contract):
            _fail("provider-contract-unproven")
        try:
            attestation = self.attest_contract()
        except Exception:
            _fail("provider-contract-unproven")
        if attestation != {"inventoryDigest": self.inventory_digest,
                           "consentPolicyVersion": "jev-disclosure-v4",
                           "durableSharedReservationTokens": 64000}:
            _fail("provider-contract-unproven")
        for relative, sha in self.provider_files.items():
            _file(self.provider_root, relative, maximum=64 * 1024 * 1024, expected=sha)
        manifest = _json(_file(self.provider_root, "plugin.json", maximum=_MAX_CONTEXT,
                               expected=self.provider_files["plugin.json"], capture=True))
        steps = _json(_file(self.provider_root, "steps.json", maximum=_MAX_CONTEXT,
                            expected=self.provider_files["steps.json"], capture=True))
        edge = EDGES[self.group]
        if (type(manifest) is not dict or manifest.get("name") != "jev"
                or type(manifest.get("capabilities")) is not list
                or {"from": edge[0], "to": edge[1]} not in manifest["capabilities"]
                or type(steps) is not list
                or sum(type(s) is dict and s.get("id") == edge[2]
                       and s.get("actor") == "program" for s in steps) != 1):
            _fail("capability-unavailable")

    def _snapshot(self, projected):
        raw = _file(self.workspace, self.context_path, maximum=_MAX_CONTEXT,
                    expected=self.context_sha256, capture=True)
        if canonical_digest(_json(raw)) != canonical_digest(self.context):
            _fail("context-fault")
        expected = copy.deepcopy(self.context)
        bindings = expected.get("bindings")
        if type(bindings) is not list or not bindings:
            _fail("context-fault")
        placement = (type(self.context.get("subject")) is dict
                     and self.context["subject"].get("passId") in {"placement-size", "placement-yaw"})
        records = {}
        for binding in bindings:
            if type(binding) is not dict or set(binding) != {"role", "path", "sha256"}:
                _fail("context-fault")
            record = placement and binding["role"] in {"camera", "depth", "support", "size-prior"}
            raw = _file(self.workspace, binding["path"], maximum=64 * 1024 * 1024,
                        expected=binding["sha256"], capture=record)
            if record:
                records[binding["role"]] = _json(raw)
        expected["bindings"] = [{"role": b["role"], "sha256": b["sha256"]} for b in bindings]
        if canonical_digest(projected) != canonical_digest(expected):
            _fail("context-fault")
        # Same provider-safe contract as the controller, checked independently of
        # who invoked this callback. Never strip any field other than binding paths.
        from decision_control import _safe_data
        _safe_data(projected)
        if placement:
            DecisionController._placement(self.context)
            if placement_record_facts(records) != self.context["observations"]["placementFacts"]:
                _fail("missing-placement-evidence")
        if self.assets_path is not None:
            _file(self.workspace, self.assets_path, maximum=_MAX_ASSETS,
                  expected=self.assets_sha256)
        elif self.context.get("observations", {}).get("images"):
            _fail("missing-evidence")

    def _run(self, arguments, deadline, *, decision=False):
        argv = [str(self.provider_root / self.entrypoint), *arguments]
        if self.executable is not None:
            argv.insert(0, str(self.executable))
        process = None
        try:
            # Credentials may be inherited from the host secret manager via env;
            # never add them to argv, input, exceptions, diagnostics or artifacts.
            process = subprocess.Popen(argv, cwd=self.workspace, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       shell=False, start_new_session=True)
            output, size = bytearray(), 0
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
                        if size > _MAX_OUTPUT:
                            _fail("malformed")
                        if key.fileobj is process.stdout:
                            output.extend(chunk)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _fail("timeout")
                process.wait(timeout=remaining)
            value = _json(bytes(output))
            if (type(value) is not dict or value.get("kind") != "img2.jev-command"
                    or type(value.get("version")) is not int or value["version"] != 1):
                _fail("malformed")
            if decision:
                return process.returncode, value
            if process.returncode != 0:
                code = value.get("reasonCode")
                _fail(code if type(code) is str and code in _ERROR_CODES else "provider-error")
            return value
        except subprocess.TimeoutExpired:
            _fail("timeout")
        except OSError:
            _fail("provider-error")
        finally:
            if process is not None:
                # Kill the isolated process group even if the direct child exited,
                # so a fork cannot retain pipes or outlive a bounded invocation.
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

    def _evidence(self, result, projected, grant, nonce, code):
        state, charged = result.get("sendState"), result.get("chargedTokens")
        if (type(state) is not str or state not in {"not-sent", "possibly-sent", "rejected", "charged"}
                or type(charged) is not int or not 0 <= charged <= 2**54 - 2
                or (state in {"not-sent", "rejected"} and charged != 0)
                or (state == "possibly-sent" and charged != 64000)):
            _fail("malformed")
        assets = {}
        if self.assets_path is not None:
            assets = _json(_file(self.workspace, self.assets_path, maximum=_MAX_ASSETS,
                                 expected=self.assets_sha256, capture=True), asset_manifest=True)
        if (type(assets) is not dict
                or any(type(image) is not dict or type(image.get("sha256")) is not str
                       or not _HEX.fullmatch(image["sha256"]) for image in assets.values())):
            _fail("malformed")
        expected = {
            "contextDigest": canonical_digest(projected),
            "requestDigest": canonical_digest({
                "contextDigest": canonical_digest(self.context),
                "providerContextDigest": canonical_digest(projected),
                "consentSha256": grant["sha256"],
                "assets": {name: image["sha256"] for name, image in assets.items()}}),
            "consentSha256": grant["sha256"],
            "invocationDigest": hashlib.sha256(nonce.encode("ascii")).hexdigest(),
        }
        attempt = result.get("attempt")
        if "attempt" not in result and code != 0 and state == "not-sent":
            # A pre-context local error has no bound attempt: not refund authority.
            return None
        if type(attempt) is not dict or attempt != expected:
            _fail("malformed")
        if code == 0:
            cached = result.get("cached")
            if (type(cached) is not bool
                    or (cached and (state != "not-sent" or charged != 0))
                    or (not cached and state != "charged")):
                _fail("malformed")
            advisory = result.get("advisory")
            if not cached and type(advisory) is dict:
                try:
                    usage = DecisionController._usage(advisory.get("usage"))
                except DecisionError:
                    pass  # A billed but malformed advisory still carries a real debit.
                else:
                    if usage != charged:
                        _fail("malformed")
        return {"sendState": state, "chargedTokens": charged, "attempt": copy.deepcopy(attempt),
                "cached": code == 0 and result["cached"]}

    def __call__(self, projected):
        self._send_evidence = None
        grant = self._grant()  # No subprocess at all without a live local grant.
        projected = copy.deepcopy(projected)
        self._snapshot(projected)
        self._package()
        deadline = time.monotonic() + self.timeout
        try:
            status = self._run(["consent", "decision-status", "--workspace", str(self.workspace),
                                "--run-id", self.context["subject"]["runId"]], deadline)
            cli_grant = {key: status.get(key) for key in grant}
            if status.get("status") != "consent-updated" or cli_grant != grant:
                _fail("consent-changed")
            if self._grant() != grant:
                _fail("consent-changed")
            self._snapshot(projected)
            self._package()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail("timeout")
            nonce = secrets.token_hex(16)
            args = ["decision-" + self.group, "--workspace", str(self.workspace),
                    "--input", self.context_path, "--timeout", str(min(30.0, remaining)),
                    "--attempt-id", nonce]
            if self.assets_path is not None:
                args.extend(["--assets", self.assets_path])
            if self._grant() != grant:
                _fail("consent-changed")
            code, result = self._run(args, deadline, decision=True)
        finally:
            self._package()
        if self._grant() != grant:
            _fail("consent-changed")
        self._snapshot(projected)
        evidence = self._evidence(result, projected, grant, nonce, code)
        self._send_evidence = evidence
        if code != 0:
            reason = result.get("reasonCode")
            _fail(reason if type(reason) is str and reason in _ERROR_CODES else "provider-error")
        if (result.get("status") != "answered" or result.get("reasonCode") != "answered"
                or result.get("consentSha256") != grant["sha256"]):
            _fail("malformed")
        advisory = result.get("advisory")
        # Reuse the controller's pure schema validator without creating another
        # controller/lease/journal. It accesses no instance state. Only the strict
        # advisory is returned; CLI artifacts, diagnostic text and extras are dropped.
        validator = object.__new__(DecisionController)
        try:
            answer, _, _, _ = validator._advisory(projected, advisory)
        except DecisionError as exc:
            code = exc.reason_code
            _fail(code if code in {"group-mismatch", "subject-mismatch", "context-digest-mismatch",
                                  "model-mismatch", "out-of-set", "missing-evidence"} else "malformed")
        return answer
