"""The Jev transport for the likeness loop: consent-gated, budget-accounted, key-safe.

The installed Jev plugin's typed decision edges (``decision-quality-review`` ...) build their own
question set: score axes limited to seven fixed ids, pass/fail/uncertain feature choices, no
same/similar/different verdict, no defect probabilities, no caller-authored instructions. The
likeness loop needs exactly those, so it cannot ride that edge. It therefore uses the plugin's own
building blocks the way the manual vesper run did (``jev_auth`` for the key, ``jev_decision_transport``
for the wire) but ONLY behind the plugin's per-run decision consent and its durable per-run budget
ledger (``jev_consent.decision_session``): no consent, no send; a reservation is made before I/O and
the provider usage is accounted after it.

EVERY file the plugin executes or imports on this path (``REQUIRED_PROVIDER_FILES``, the same set the
core's provider bridge pins) is verified against ``forge/_shared/jev_release_lock.json`` with no-follow
reads BEFORE anything is launched. The verified bytes are then copied to a private staging directory and
both the in-process modules and the ``jev.py`` subprocess run from that copy, so a swap after the check,
a sibling module, a stale ``__pycache__`` or a shadowing file next to the originals cannot execute. The
subprocess gets a scrubbed environment (an allowlist, no inherited credentials, no ``PYTHON*``). The API
key is read only by the in-process transport, never logged, serialised or printed.

``JevBackend`` is the seam: tests pass a fake with the same methods and no network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import _common as C
from decision_control import DecisionError
from jev_cli_bridge import REQUIRED_PROVIDER_FILES, _file

LOCK = Path(__file__).resolve().parent.parent / "_shared" / "jev_release_lock.json"
MAX_PLUGIN_FILE = 8 * 1024 * 1024
# Variables the plugin's CLI needs to find its harness, temp space and locale. Nothing else is inherited:
# no TYPESAFE_API_KEY, no cloud/provider credentials, no PYTHONPATH/PYTHONSTARTUP.
CHILD_ENV_ALLOWLIST = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR",
                       "IMG2_HOME", "IMG2_HARNESS_DIR")
REQUIRED_TOOLS = ("jev_auth", "jev_consent", "jev_decision_transport")
PURPOSE = "quality-review"
MODEL = "jev-1.14.0"
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")


class JevUnavailable(C.TexturedError):
    """No usable Jev: not installed, no account, or no consent. Carries a stable reason code."""

    def __init__(self, reason: str, message: str, code: int = 3):
        super().__init__(message, code)
        self.reason = reason


def check_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise C.TexturedError("--run-id must match [A-Za-z0-9][A-Za-z0-9_-]{0,95}")
    return run_id


def request_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class JevBackend:
    """Interface. ``consent_status`` and ``session`` are the gate; ``post`` is the only network call."""

    def auth_present(self) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def consent_status(self, workspace: Path, run_id: str) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def session(self, workspace: Path, run_id: str):  # pragma: no cover
        raise NotImplementedError

    def post(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError


def jev_tools_dir(explicit: str | None = None) -> Path:
    from runtime_selectors import img2_home
    return Path(explicit).expanduser() if explicit else img2_home() / "plugins" / "jev" / "tools"


def _load(tools: Path, name: str):
    spec = importlib.util.spec_from_file_location(f"_img2_textured_{name}", tools / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def verified_plugin_files(tools: Path) -> dict[str, bytes]:
    """Every pinned plugin file's bytes, each checked against the release lock; nothing is executed.

    ``tools`` is ``<plugin>/tools``; ``plugin.json``/``steps.json`` live one level up. The file list is
    ``REQUIRED_PROVIDER_FILES`` (the core's list, not a second one) and every entry must be pinned.
    """
    lock = json.loads(LOCK.read_text(encoding="utf-8"))["files"]
    root = Path(os.path.realpath(tools.parent))
    unpinned = sorted(REQUIRED_PROVIDER_FILES - lock.keys())
    if unpinned:
        raise JevUnavailable("provider-unpinned", f"jev_release_lock.json does not pin {', '.join(unpinned)}")
    if not os.path.lexists(tools / "jev.py"):
        raise JevUnavailable("not-installed", f"the Jev plugin is not installed ({tools / 'jev.py'} is missing)")
    files: dict[str, bytes] = {}
    for relative in sorted(REQUIRED_PROVIDER_FILES):
        # An installed plugin that lacks a pinned file is not the pinned release.
        try:
            files[relative] = _file(root, relative, maximum=MAX_PLUGIN_FILE, expected=lock[relative], capture=True)
        except DecisionError:
            raise JevUnavailable("provider-unpinned",
                                 f"{relative} does not match the pinned Jev release in jev_release_lock.json") from None
    return files


def stage_verified(files: dict[str, bytes], stage: Path) -> Path:
    """Write the verified bytes under ``stage`` with the plugin's layout; returns ``stage/tools``."""
    for relative, data in files.items():
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return stage / "tools"


def scrubbed_environment(pycache: Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if environ is None else environ
    from runtime_selectors import img2_home
    env = {key: source[key] for key in CHILD_ENV_ALLOWLIST if key in source}
    env["IMG2_HOME"] = str(img2_home())  # the plugin CLI locates the harness core and user consent through it
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = str(pycache)
    return env


class PluginBackend(JevBackend):
    def __init__(self, tools: Path | None = None):
        self.source_tools = jev_tools_dir(str(tools) if tools else None)
        files = verified_plugin_files(self.source_tools)
        self._stage = tempfile.TemporaryDirectory(prefix="img2-jev-")
        os.chmod(self._stage.name, 0o700)
        self.tools = stage_verified(files, Path(self._stage.name) / "plugin")
        self._pycache = Path(self._stage.name) / "pycache"
        self._pycache.mkdir()
        self._modules = {name: _load(self.tools, name) for name in REQUIRED_TOOLS}

    def close(self) -> None:
        self._stage.cleanup()

    def auth_present(self) -> bool:
        return self._modules["jev_auth"].auth_status().get("status") == "present"

    def consent_status(self, workspace: Path, run_id: str) -> dict[str, Any]:
        """`jev.py consent decision-status --workspace W --run-id R`: the read-only grant probe."""
        completed = subprocess.run(
            [sys.executable, str(self.tools / "jev.py"), "consent", "decision-status",
             "--workspace", str(workspace), "--run-id", run_id],
            capture_output=True, text=True, timeout=60, env=scrubbed_environment(self._pycache), stdin=subprocess.DEVNULL)
        try:
            result = json.loads(completed.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise JevUnavailable("consent-required", "could not read the Jev decision consent status") from None
        if completed.returncode != 0 or result.get("status") == "request-input":
            raise JevUnavailable(str(result.get("reasonCode") or "consent-required"),
                                 f"Jev decision consent is not active for run {run_id} ({result.get('reasonCode', 'consent-required')})")
        return result

    @contextmanager
    def session(self, workspace: Path, run_id: str):
        with self._modules["jev_consent"].decision_session(workspace, run_id) as session:
            yield session

    def post(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        transport, auth = self._modules["jev_decision_transport"], self._modules["jev_auth"]
        key = auth.resolve_api_key()
        body, content_type, images = transport._encode_request(payload)
        return transport._post_https(body, key, content_type, images, time.monotonic() + timeout)


def assert_grant(grant: dict[str, Any]) -> dict[str, Any]:
    """The grant must be live, cover the quality-review purpose and carry a usable token budget."""
    if (not isinstance(grant, dict) or grant.get("granted") is not True
            or PURPOSE not in (grant.get("purposes") or [])):
        raise JevUnavailable("consent-required", f"Jev decision consent must be granted for purpose {PURPOSE!r}")
    budget = grant.get("tokenBudget")
    if type(budget) is not int or budget <= 0:
        raise JevUnavailable("budget-exhausted", "the Jev consent carries no token budget")
    return grant


def consult(backend: JevBackend, workspace: Path, run_id: str, payload: dict[str, Any], timeout: float = 240.0):
    """Send ``payload`` once, under consent and budget. Returns (response, usage, grant).

    Order is the contract: auth present -> live consent -> reserve (budget) -> post -> account ->
    complete. Any failure before ``post`` sends nothing. A failed or unknown send keeps the plugin's
    full reservation charged (the plugin's rule, not ours to relax).
    """
    check_run_id(run_id)
    if not backend.auth_present():
        raise JevUnavailable("credential-missing",
                             "no Jev account: run `img2-jev login`, or use the no-account mode "
                             "(likeness.py questions, then record-model-answers)")
    grant = assert_grant(backend.consent_status(workspace, run_id))
    digest = request_digest(payload)
    with backend.session(workspace, run_id) as session:
        if session.previous(digest) is not None:
            raise C.TexturedError("this exact request was already sent for this run; reuse its recorded likeness "
                                  "or change the render/sheet before asking again")
        session.reserve(digest)
        response = backend.post(payload, timeout)
        usage = response.get("usage") if isinstance(response, dict) else None
        session.account(digest, usage)
        result = hashlib.sha256(json.dumps(response, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        session.complete(digest, result)
    return response, usage, grant
