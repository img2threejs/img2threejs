"""Host-approved external model fallback for the standalone routing CLI.

``HostFallback`` is the callable ``DecisionController.consult(fallback=...)`` expects.
It wraps ONE program the host named and pinned by exact SHA-256; it is never
discovered, searched for on PATH, or chosen from workspace/model input. The
controller alone decides whether a fallback may run: only after a journalled Jev
outage, never after a Jev answer and never for unproven availability. Nothing here
duplicates or weakens that gating, the advisory schema checks, the option and
probability checks or the 0.82 floor, which stay in ``decision_control``.

Protocol. The program is run with no arguments and no shell. Its stdin is the exact
canonical JSON bytes of the path-stripped projected context the controller passed;
the SHA-256 of those bytes is the context digest. Its stdout must be exactly one
JSON object: an ``img2.decision-advisory`` v1 whose ``contextDigest`` is that digest
and whose ``model`` names the model that answered. Exit status must be 0. This
module checks only the transport (approval, exit, time, size, one strict JSON
object); the controller then validates that object as a fallback advisory.

Approval. The command path is resolved strictly to a regular executable file. Its
bytes are re-read and verified against the approved digest at construction, before
every invocation, and again after the run; a changed file is a refusal, never a
re-approval. The digest covers only that file: a script's interpreter, its imports
and any config it reads are the host's trust decision. The verify-to-exec window is
not closed by this module; the post-run check only refuses an answer from a program
that changed.

Containment this module does provide: no shell, a private scratch directory as the
working directory (never the workspace), one process group killed on every exit
path, a wall-clock deadline covering pin verification and image preflight, and
bounded stdout/stderr. The environment is the host's,
as for ``jev_cli_bridge``'s program-owned caller, minus what ``HarnessPin.runner``
also removes: interpreter/runtime startup overrides (``NODE_*``, ``PYTHON*``) and the
Jev credential ``TYPESAFE_API_KEY``, which an outage fallback must not receive.
Bound image observations alone are copied, byte-for-byte, to private
scratch and passed through a pinned manifest; binding paths never enter stdin.
Approving this program authorizes it to receive those images, independently of
Jev consent. It is NOT a sandbox: the program runs with the host user's privileges
and can reach the network or filesystem if the approved program itself does.
It never retries and never mutates the context.
``IMG2_FALLBACK_SUPERVISED=1`` identifies this isolated wrapper group to the OMP
adapter; its children join the group so the outer timeout also stops them.
Program output is never logged or surfaced; only the advisory the controller
validates is returned.

Reason codes. Local ``fallback-preflight-timeout``, ``fallback-image-invalid`` and
``fallback-image-context-required`` failures prove no approved program launched;
the controller recognizes these unsent failures and releases the reservation.
``stale-evidence`` also releases an unsent reservation. After launch, ``timeout``,
``provider-error`` (non-zero exit or I/O failure), and ``malformed`` (not one strict
JSON object) retain conservative accounting unless actual usage was returned.
The controller records every failed fallback as ``fallback-invalid``.
"""
from __future__ import annotations

import copy
import hashlib
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time

_SHARED = str(Path(__file__).resolve().parents[1] / "_shared")
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

from decision_control import DecisionError, _json_bytes, canonical_digest
from jev_cli_bridge import _file, _json
from .images import decode
from .storage import RouteError

DEFAULT_TIMEOUT = 60.0
MAX_TIMEOUT = 300.0
MAX_STDOUT = 256 * 1024
MAX_STDERR = 64 * 1024
MAX_PROGRAM = 64 * 1024 * 1024
_MAX_RECORD = 2 * 1024 * 1024
MAX_IMAGES = 64
MAX_IMAGE_BYTES = 48 * 1024 * 1024
MAX_IMAGE_FILE = 32 * 1024 * 1024
IMAGE_MANIFEST_ENV = "IMG2_FALLBACK_IMAGE_MANIFEST"
IMAGE_MANIFEST_SHA_ENV = "IMG2_FALLBACK_IMAGE_MANIFEST_SHA256"
SUPERVISED_ENV = "IMG2_FALLBACK_SUPERVISED"
_IMAGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_RECORD_REF = re.compile(r"\.img2threejs/decisions/routing/([0-9a-f]{64})\.json\Z")


def _fail(code):
    raise DecisionError(code) from None


def _check_deadline(deadline, reason="timeout"):
    if time.monotonic() >= deadline:
        _fail(reason)


def _reap(process, *, own_group=True):
    if process is None:
        return
    try:
        if own_group:
            # The program may have forked: kill its isolated group as well.
            os.killpg(process.pid, signal.SIGKILL)
        else:
            # Nested supervised children share our group. Never kill the wrapper
            # (or a standalone caller's terminal) while reaping one direct child.
            process.kill()
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    for stream in (process.stdin, process.stdout, process.stderr):
        try:
            if stream is not None:
                stream.close()
        except OSError:
            pass

def _image_rows(projected):
    """Resolve each declared image to exactly one projected role, in declared order."""
    if type(projected) is not dict or type(projected.get("observations")) is not dict:
        _fail("fallback-image-invalid")
    images = projected.get("observations", {}).get("images", [])
    bindings = projected.get("bindings", [])
    if type(images) is not list or len(images) > MAX_IMAGES or type(bindings) is not list:
        _fail("fallback-image-invalid")
    roles, assets, result = set(), set(), []
    for image in images:
        if (type(image) is not dict or set(image) != {"role", "asset"}
                or any(type(image.get(key)) is not str or not _IMAGE_ID.fullmatch(image[key])
                       for key in ("role", "asset"))
                or image["role"] in roles or image["asset"] in assets):
            _fail("fallback-image-invalid")
        matching = [row for row in bindings if type(row) is dict and row.get("role") == image["role"]]
        if (len(matching) != 1 or type(matching[0].get("sha256")) is not str
                or not _HEX.fullmatch(matching[0]["sha256"])):
            _fail("fallback-image-invalid")
        roles.add(image["role"])
        assets.add(image["asset"])
        result.append(dict(image, sha256=matching[0]["sha256"]))
    return result


def _image_suffix(data):
    """Validate actual bounded PNG/JPEG pixels, never a filename or declared MIME."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        suffix = ".png"
    elif data.startswith(b"\xff\xd8"):
        suffix = ".jpg"
    else:
        _fail("fallback-image-invalid")
    try:
        decode(data)
    except (RouteError, ValueError, IndexError, KeyError, OverflowError):
        _fail("fallback-image-invalid")
    return suffix



class HostFallback:
    def __init__(self, command, sha256, *, timeout=DEFAULT_TIMEOUT):
        if type(sha256) is not str or not _HEX.fullmatch(sha256):
            _fail("fallback-digest-invalid")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT:
            _fail("fallback-timeout-invalid")
        try:
            self.path = Path(command).resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError):
            _fail("fallback-command-unsafe")
        self.sha256, self.timeout = sha256, float(timeout)
        self._verify("fallback-command-unsafe", "fallback-digest-mismatch")

    def _verify(self, unsafe, mismatch):
        """Re-hash the approved file (descriptor-relative, no-follow); any deviation refuses."""
        try:
            if not os.access(self.path, os.X_OK):
                _fail("unsafe-path")
            _file(self.path.parent, self.path.name, maximum=MAX_PROGRAM, expected=self.sha256)
        except DecisionError as error:
            _fail(mismatch if error.reason_code == "stale-evidence" else unsafe)

    def bind_context(self, workspace, context):
        """Defer all image I/O until the controller selects this approved fallback.

        This trusted optional controller hook receives the original validated
        context. It neither enrolls another program nor opens any file itself.
        """
        original = copy.deepcopy(context)
        expected = copy.deepcopy(original)
        expected["bindings"] = [{"role": row["role"], "sha256": row["sha256"]}
                                for row in original["bindings"]]
        workspace = Path(workspace)

        def bound(projected):
            if canonical_digest(projected) != canonical_digest(expected):
                _fail("stale-evidence")
            return self._invoke(projected, workspace=workspace, context=original)

        return bound

    def __call__(self, projected):
        if _image_rows(projected):
            _fail("fallback-image-context-required")
        return self._invoke(projected)

    def _invoke(self, projected, *, workspace=None, context=None):
        deadline = time.monotonic() + self.timeout
        payload = _json_bytes(projected)
        _check_deadline(deadline, "fallback-preflight-timeout")
        # Nothing has been sent yet, so the controller may release its reservation.
        self._verify("stale-evidence", "stale-evidence")
        _check_deadline(deadline, "fallback-preflight-timeout")
        stdout = self._run(payload, deadline=deadline, projected=projected,
                           workspace=workspace, context=context)
        # The program has run and may have spent tokens: a changed program is a
        # distinct refusal that keeps the reservation instead of releasing it.
        _check_deadline(deadline)
        self._verify("fallback-program-changed", "fallback-program-changed")
        _check_deadline(deadline)
        # The controller's _advisory validates shape, digest, option ids,
        # probabilities, model identity and the fallback floor on this object and
        # debits real usage even when it rejects the answer. Never pre-empt it here.
        try:
            text = stdout.decode("utf-8")
        except UnicodeError:
            _fail("malformed")
        value = _json(text)
        if type(value) is not dict:
            _fail("malformed")
        return value

    @staticmethod
    def _materialize(scratch, projected, workspace, context, *, deadline):
        images, total = [], 0
        _check_deadline(deadline, "fallback-preflight-timeout")
        for index, image in enumerate(_image_rows(projected)):
            _check_deadline(deadline, "fallback-preflight-timeout")
            matching = [row for row in context["bindings"] if row["role"] == image["role"]]
            if (len(matching) != 1 or set(matching[0]) != {"role", "path", "sha256"}
                    or matching[0]["sha256"] != image["sha256"]):
                _fail("fallback-image-invalid")
            _check_deadline(deadline, "fallback-preflight-timeout")
            data = _file(workspace, matching[0]["path"],
                         maximum=min(MAX_IMAGE_FILE, MAX_IMAGE_BYTES - total),
                         expected=image["sha256"], capture=True)
            _check_deadline(deadline, "fallback-preflight-timeout")
            total += len(data)
            _check_deadline(deadline, "fallback-preflight-timeout")
            suffix = _image_suffix(data)
            _check_deadline(deadline, "fallback-preflight-timeout")
            target = scratch / f"image-{index}{suffix}"
            _check_deadline(deadline, "fallback-preflight-timeout")
            with target.open("xb") as stream:
                os.chmod(target, 0o600)
                stream.write(data)
            _check_deadline(deadline, "fallback-preflight-timeout")
            images.append(dict(image, path=str(target)))
        if not images:
            return {}
        _check_deadline(deadline, "fallback-preflight-timeout")
        manifest = {"kind": "img2.host-fallback-images", "version": 1,
                    "contextDigest": canonical_digest(projected), "images": images}
        raw = _json_bytes(manifest)
        _check_deadline(deadline, "fallback-preflight-timeout")
        path = scratch / "images.json"
        with path.open("xb") as stream:
            os.chmod(path, 0o600)
            stream.write(raw)
        _check_deadline(deadline, "fallback-preflight-timeout")
        sha = hashlib.sha256(raw).hexdigest()
        _check_deadline(deadline, "fallback-preflight-timeout")
        return {IMAGE_MANIFEST_ENV: str(path), IMAGE_MANIFEST_SHA_ENV: sha}

    def _run(self, payload, *, deadline, projected, workspace=None, context=None):
        scratch = process = None
        try:
            try:
                _check_deadline(deadline, "fallback-preflight-timeout")
                scratch = str(Path(tempfile.mkdtemp(prefix="img2-fallback-")).resolve(strict=True))
                environment = {key: value for key, value in os.environ.items()
                               if not key.startswith(("NODE_", "PYTHON"))
                               and key not in {"TYPESAFE_API_KEY", IMAGE_MANIFEST_ENV,
                                               IMAGE_MANIFEST_SHA_ENV, SUPERVISED_ENV}}
                environment[SUPERVISED_ENV] = "1"
                if context is not None:
                    environment.update(self._materialize(Path(scratch), projected, workspace, context,
                                                         deadline=deadline))
                _check_deadline(deadline, "fallback-preflight-timeout")
                process = subprocess.Popen(
                    [str(self.path)], cwd=scratch, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, env=environment, shell=False, start_new_session=True)
            except OSError:
                _fail("fallback-launch-failed")
            try:
                stdout = self._exchange(process, payload, deadline)
            except subprocess.TimeoutExpired:
                _fail("timeout")
            except OSError:
                _fail("provider-error")
            if process.returncode != 0:
                _fail("provider-error")
            return stdout
        finally:
            _reap(process)
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)

    @staticmethod
    def _exchange(process, payload, deadline):
        stdin, stdout, stderr = process.stdin, process.stdout, process.stderr
        os.set_blocking(stdin.fileno(), False)
        view, sent, output, noise = memoryview(payload), 0, bytearray(), 0
        with selectors.DefaultSelector() as selector:
            selector.register(stdout, selectors.EVENT_READ)
            selector.register(stderr, selectors.EVENT_READ)
            selector.register(stdin, selectors.EVENT_WRITE)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _fail("timeout")
                for key, _ in selector.select(remaining):
                    if key.fileobj is stdin:
                        try:
                            sent += os.write(stdin.fileno(), view[sent:sent + 65536])
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            # The program stopped reading; its exit status decides the outcome.
                            sent = len(payload)
                        if sent == len(payload):
                            selector.unregister(stdin)
                            stdin.close()
                        continue
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif key.fileobj is stdout:
                        output.extend(chunk)
                        if len(output) > MAX_STDOUT:
                            _fail("fallback-output-too-large")
                    else:
                        noise += len(chunk)
                        if noise > MAX_STDERR:
                            _fail("fallback-output-too-large")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail("timeout")
            process.wait(timeout=remaining)
        return bytes(output)


def fallback_decisions(workspace, decision, *, attempted):
    """Report every consultation in ``decision`` that went through the fallback path.

    The controller stamps ``unavailableReason`` on a decision record only when it
    actually invoked the fallback callable, so that key (not a row field and never
    fallback program output) identifies fallback consultations. ``decider`` is
    ``model:<id>`` when the fallback answered and ``None`` when it was invoked but
    its answer was refused. Records are read only when a fallback was configured
    (``attempted``) or a consultation already names a model decider; each is
    re-read from disk and must hash to the receipt the decision names, else the
    result fails closed.
    """
    objects = decision.get("objects") if type(decision) is dict else None
    if type(objects) is not list:
        return []
    reported, seen = [], set()
    for row in objects:
        consultations = row.get("consultations") if type(row) is dict else None
        for consultation in consultations if type(consultations) is list else []:
            if type(consultation) is not dict:
                continue
            decider = consultation.get("decider")
            if not attempted and not (type(decider) is str and decider.startswith("model:")):
                continue
            receipt, reference = consultation.get("receiptSha256"), consultation.get("decisionRef")
            match = _RECORD_REF.fullmatch(reference) if type(reference) is str else None
            if match is None or match.group(1) != receipt:
                _fail("invalid-routing-result")
            if receipt in seen:
                continue
            seen.add(receipt)
            record = _json(_file(Path(workspace), reference, maximum=_MAX_RECORD, capture=True))
            if (type(record) is not dict or record.get("recordId") != receipt
                    or canonical_digest({k: v for k, v in record.items() if k != "recordId"}) != receipt
                    or record.get("decider") != decider):
                _fail("invalid-routing-result")
            if "unavailableReason" in record:
                reported.append({"decider": record["decider"], "unavailableReason": record["unavailableReason"],
                                 "outcome": record["outcome"], "reasonCode": record["reasonCode"],
                                 "recordId": receipt})
    return reported
