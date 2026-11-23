"""The Jev transport for the likeness loop: consent-gated, budget-accounted, key-safe.

The installed Jev plugin's typed decision edges (``decision-quality-review`` ...) build their own
question set: score axes limited to seven fixed ids, pass/fail/uncertain feature choices, no
same/similar/different verdict, no defect probabilities, no caller-authored instructions. The
likeness loop needs exactly those, so it cannot ride that edge. It therefore uses the plugin's own
building blocks the way the manual vesper run did (``jev_auth`` for the key, ``jev_decision_transport``
for the wire) but ONLY behind the plugin's per-run decision consent and its durable per-run budget
ledger (``jev_consent.decision_session``): no consent, no send; a reservation is made before I/O and
the provider usage is accounted after it. The plugin files that are imported are pinned to
``forge/_shared/jev_release_lock.json``. The API key is never read into a variable that is logged,
never serialised and never printed.

``JevBackend`` is the seam: tests pass a fake with the same methods and no network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import _common as C

LOCK = Path(__file__).resolve().parent.parent / "_shared" / "jev_release_lock.json"
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


def verify_pinned(tools: Path) -> None:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))["files"]
    for name in REQUIRED_TOOLS + ("jev",):
        relative = f"tools/{name}.py"
        path = tools / f"{name}.py"
        if not path.is_file():
            raise JevUnavailable("not-installed", f"the Jev plugin is not installed ({path} is missing)")
        if relative not in lock or C.sha256_file(path) != lock[relative]:
            raise JevUnavailable("provider-unpinned",
                                 f"{relative} does not match the pinned Jev release in jev_release_lock.json")


class PluginBackend(JevBackend):
    def __init__(self, tools: Path | None = None):
        self.tools = jev_tools_dir(str(tools) if tools else None)
        verify_pinned(self.tools)
        self._modules = {name: _load(self.tools, name) for name in REQUIRED_TOOLS}

    def auth_present(self) -> bool:
        return self._modules["jev_auth"].auth_status().get("status") == "present"

    def consent_status(self, workspace: Path, run_id: str) -> dict[str, Any]:
        """`jev.py consent decision-status --workspace W --run-id R`: the read-only grant probe."""
        completed = subprocess.run(
            [sys.executable, str(self.tools / "jev.py"), "consent", "decision-status",
             "--workspace", str(workspace), "--run-id", run_id],
            capture_output=True, text=True, timeout=60)
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
