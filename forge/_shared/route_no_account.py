"""User-owned, run-scoped routing authority; never inferred from missing login.

Storage follows jev_consent's IMG2_HOME/config, uid/private-mode, flock and atomic
fsync/rename boundary. This is a user-action boundary, not a same-uid sandbox:
an unrestricted host process can impersonate its user, just as for Jev consent.
Only the interactive CLI mints/revokes grants; routing only reads them.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import math
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tempfile
import time

from decision_control import DecisionError, _identifier, _json_bytes, canonical_digest
from jev_cli_bridge import _file, _json

POLICY = "routing-no-jev-account-v1"
MAX_TTL = 3600


def credential_status():
    """Inspect enrollment without retrieving a key or running the Jev plugin."""
    if os.environ.get("TYPESAFE_API_KEY", "").strip():
        return "present"
    if sys.platform != "darwin":
        return "credential-missing"  # Jev's only enrolled store is macOS Keychain.
    try:
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-a", "jev", "-s", "ai.typesafe.api-key"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        raise DecisionError("credential-storage-error") from None
    if result.returncode == 0:
        return "present"
    if result.returncode == 44:
        return "credential-missing"
    raise DecisionError("credential-storage-error")


def require_no_credential():
    if credential_status() != "credential-missing":
        raise DecisionError("jev-account-present")


def _identity(workspace, run_id):
    return {"user": hashlib.sha256((str(os.getuid()) + "\0" + str(Path.home().resolve())).encode()).hexdigest(),
            "workspace": hashlib.sha256(str(workspace).encode()).hexdigest(), "runId": _identifier(run_id)}


def _path(workspace, run_id):
    home = os.environ.get("IMG2_HOME")
    if not home:
        raise DecisionError("attestation-store-required")
    root = Path(home).expanduser().absolute()
    # Do not resolve away symlinks before checking them. No authority in the
    # request workspace or the skill checkout, even with an IMG2_HOME override.
    path = root / "config" / ("route-no-jev-account-" + canonical_digest(_identity(workspace, run_id)) + ".json")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise DecisionError("attestation-storage-error")
    root = root.resolve()
    for excluded in (workspace, Path(__file__).resolve().parents[2]):
        if root == excluded or excluded in root.parents:
            raise DecisionError("attestation-storage-error")
    return path


def _private(info, *, directory=False):
    return (info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o077
            and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)))


@contextmanager
def _locked(path, *, create=False):
    if create:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.parent.exists():
        raise DecisionError("no-jev-account-attestation-required")
    if path.parent.is_symlink() or not _private(path.parent.stat(), directory=True):
        raise DecisionError("attestation-storage-error")
    fd = None
    try:
        fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        if not _private(os.fstat(fd)):
            raise DecisionError("attestation-storage-error")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    except OSError:
        raise DecisionError("attestation-storage-error") from None
    finally:
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def _load(path):
    if not path.exists():
        raise DecisionError("no-jev-account-attestation-required")
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not _private(os.fstat(fd)):
            raise DecisionError("attestation-storage-error")
        raw = os.read(fd, 32769)
        if len(raw) > 32768:
            raise DecisionError("attestation-storage-error")
        value = _json(raw)
    except OSError:
        raise DecisionError("attestation-storage-error") from None
    finally:
        if fd is not None:
            os.close(fd)
    fields = {"kind", "version", "policyVersion", "user", "workspace", "runId", "modelId",
              "imageSha256s", "tokenBudget", "issuedAt", "expiresAt", "generation", "enabled", "attestationId"}
    if type(value) is not dict or set(value) != fields:
        raise DecisionError("attestation-storage-error")
    body = {key: item for key, item in value.items() if key != "attestationId"}
    if (value["kind"] != "img2.route-no-jev-account" or type(value["version"]) is not int
            or value["version"] != 1 or value["policyVersion"] != POLICY
            or canonical_digest(body) != value["attestationId"] or type(value["enabled"]) is not bool
            or type(value["tokenBudget"]) is not int or not 0 < value["tokenBudget"] <= 2**53 - 1
            or type(value["imageSha256s"]) is not list or not value["imageSha256s"]
            or any(type(sha) is not str or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha)
                   for sha in value["imageSha256s"])
            or any(type(value[k]) not in (int, float) or not math.isfinite(value[k]) for k in ("issuedAt", "expiresAt"))
            or not 0 < value["expiresAt"] - value["issuedAt"] <= MAX_TTL):
        raise DecisionError("attestation-storage-error")
    _identifier(value["modelId"])
    _identifier(value["generation"])
    return value


def _store(path, value):
    fd, temporary = tempfile.mkstemp(prefix=".route-attestation-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_json_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def attest(workspace, run_id, *, model_id, images, token_budget, ttl=1800, revoke=False):
    """Interactive user action only; images are the original reference bytes."""
    workspace = Path(workspace).resolve(strict=True)
    path = _path(workspace, run_id)
    if not sys.stdin.isatty():
        raise DecisionError("user-attestation-required")
    if not revoke:
        require_no_credential()
        _identifier(model_id)
        if (type(token_budget) is not int or not 0 < token_budget <= 2**53 - 1
                or type(ttl) is not int or not 0 < ttl <= MAX_TTL or not images):
            raise DecisionError("invalid-attestation")
        hashes = sorted({hashlib.sha256(_file(workspace, image, maximum=64 * 1024 * 1024, capture=True)).hexdigest()
                         for image in images})
        disclosure = (f"I have no Jev account. Routing will be decided by model provider/model {model_id}, NOT Jev. "
                      f"The reference images with SHA256 {', '.join(hashes)}, their upright versions and derived crops "
                      f"go to that named model provider through the host agent. Workspace {workspace}; run {run_id}; "
                      f"token budget {token_budget}; expires in {ttl} seconds. No core network call. "
                      "Revoke with route.py revoke-no-jev-account --workspace WS --run-id RUN.")
    else:
        disclosure = f"Revoke no-Jev-account routing authority for workspace {workspace}; run {run_id}."
    print(disclosure)
    if input("Type ATTEST to confirm: ") != "ATTEST":
        raise DecisionError("user-attestation-required")
    with _locked(path, create=True):
        if revoke:
            value = _load(path)
            revoked_id = value["attestationId"]
            value["enabled"] = False
        else:
            now = time.time()
            value = {"kind": "img2.route-no-jev-account", "version": 1, "policyVersion": POLICY,
                     **_identity(workspace, run_id), "modelId": model_id, "imageSha256s": hashes,
                     "tokenBudget": token_budget, "issuedAt": now, "expiresAt": now + ttl,
                     "generation": secrets.token_hex(32), "enabled": True}
        value.pop("attestationId", None)
        value["attestationId"] = canonical_digest(value)
        _store(path, value)
    # Same lease/hash-chain publication path as routing; no workspace file can
    # mint authority. A revoked store remains revoked if journal publication fails.
    from decision_control import DecisionController
    controller = DecisionController(workspace, run_id)
    with controller._locked():
        entries, _ = controller._load()
        data = {"attestationId": value["attestationId"], "presentedAsJev": False,
                "decider": "model:" + value["modelId"], "expiresAt": value["expiresAt"]}
        if revoke:
            data["revokedAttestationId"] = revoked_id
        controller._commit(entries, "no-account-revoked" if revoke else "no-account-attested", data)
    return value


class RoutingNoAccount:
    """Live read-only grant, including credential, image, expiry and revocation checks."""
    last_failure = None

    def __init__(self, workspace, run_id, image):
        self.workspace = Path(workspace).resolve(strict=True)
        self.run_id, self.image = run_id, image
        self.initial = self._read()
        self.model_id = self.initial["modelId"]

    def _read(self):
        require_no_credential()
        path = _path(self.workspace, self.run_id)
        with _locked(path):
            value = _load(path)
        if any(value[key] != item for key, item in _identity(self.workspace, self.run_id).items()):
            raise DecisionError("attestation-binding-mismatch")
        if not value["enabled"]:
            raise DecisionError("attestation-revoked")
        if not value["issuedAt"] <= time.time() < value["expiresAt"]:
            raise DecisionError("attestation-expired")
        sha = hashlib.sha256(_file(self.workspace, self.image, maximum=64 * 1024 * 1024, capture=True)).hexdigest()
        if sha not in value["imageSha256s"]:
            raise DecisionError("attestation-binding-mismatch")
        return value

    def metadata(self):
        value = self._read()
        if value != self.initial:
            raise DecisionError("attestation-changed")
        return {"decisionMode": "no-account", "attestationId": value["attestationId"],
                "decider": "model:" + value["modelId"], "presentedAsJev": False}

    def __call__(self):
        try:
            metadata = self.metadata()
            self.last_failure = None
            return {"granted": True, "purposes": ["routing"], "sha256": metadata["attestationId"],
                    "tokenBudget": self.initial["tokenBudget"]}
        except DecisionError as error:
            self.last_failure = error.reason_code
            raise
