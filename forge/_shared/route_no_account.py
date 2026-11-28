"""User-owned, purpose-scoped run authority; never inferred from missing login.

Storage follows jev_consent's IMG2_HOME/config, uid/private-mode, flock and atomic
fsync/rename boundary. This is a user-consent boundary, not isolation from an agent
running as the same OS user: such a process can impersonate its user, exactly as it can
for `jev consent authorize-decisions`. The interactive CLI is the only code path here
that mints/revokes grants and it confirms on /dev/tty; lifecycle consumers only read them.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import math
import os
from pathlib import Path
import secrets
import stat
import tempfile
import time
import types

from decision_control import (DEFAULT_REQUEST_TOKEN_LIMIT, GROUPS, DecisionError, _identifier,
                              _json_bytes, canonical_digest)
from jev_cli_bridge import _file, _json
from runtime_selectors import img2_home

POLICY = "routing-no-jev-account-v2"
LEGACY_POLICY = "routing-no-jev-account-v1"
TTY_PATH = "/dev/tty"
MAX_TTL = 3600


_LOCK = Path(__file__).resolve().parent / "jev_release_lock.json"
_AUTH_MODULE = "tools/jev_auth.py"


def _plugin_auth_status():
    """Use the selected installation's pinned auth implementation.

    Fall back to the default installation only when no selected plugin exists.
    An unreadable or stale selected plugin fails closed, never silently falls back.
    The admitted implementation probes the same per-user Keychain/environment as
    ``LiveConsent.auth_status``; installation selection does not scope credentials.
    """
    roots = (img2_home() / "plugins" / "jev", Path.home() / ".img2" / "plugins" / "jev")
    root = next((candidate for candidate in roots if os.path.lexists(candidate)), None)
    if root is None:
        raise DecisionError("credential-storage-error")
    try:
        pinned = _json(_file(_LOCK.parent, _LOCK.name, maximum=128 * 1024, capture=True))["files"][_AUTH_MODULE]
        source = _file(root, _AUTH_MODULE, maximum=1024 * 1024, expected=pinned, capture=True)
        module = types.ModuleType("_img2_route_jev_auth")
        exec(compile(source, str(root / _AUTH_MODULE), "exec"), module.__dict__)
        status = module.auth_status(os.environ)
        if type(status) is not dict:
            raise ValueError
        return status["status"], status["reasonCode"]
    except Exception:
        raise DecisionError("credential-storage-error") from None


def credential_status():
    """"present" or "credential-missing"; every inconclusive outcome raises credential-storage-error."""
    if os.environ.get("TYPESAFE_API_KEY", "").strip():
        return "present"
    status = _plugin_auth_status()
    if status == ("present", "credential-present"):
        return "present"
    if status == ("missing", "credential-missing"):
        return "credential-missing"
    raise DecisionError("credential-storage-error")


def require_no_credential():
    """Fail closed: a present, rejected or unreadable credential is treated as an existing account."""
    status = credential_status()
    if status != "credential-missing":
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


def _purposes(value):
    return (type(value) is list and bool(value) and all(type(item) is str and item in GROUPS for item in value)
            and len(value) == len(set(value)))


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
    legacy = type(value) is dict and value.get("version") == 1 and value.get("policyVersion") == LEGACY_POLICY
    if type(value) is not dict or set(value) != (fields if legacy else fields | {"purposes"}):
        raise DecisionError("attestation-storage-error")
    body = {key: item for key, item in value.items() if key != "attestationId"}
    if (value["kind"] != "img2.route-no-jev-account" or type(value["version"]) is not int
            or (not legacy and (value["version"] != 2 or value["policyVersion"] != POLICY or not _purposes(value["purposes"])))
            or canonical_digest(body) != value["attestationId"] or type(value["enabled"]) is not bool
            or type(value["tokenBudget"]) is not int or not 0 < value["tokenBudget"] <= 2**53 - 1
            or (not legacy and value["tokenBudget"] < DEFAULT_REQUEST_TOKEN_LIMIT)
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


def _preserve(path, value):
    """Replacing/revoking a grant never erases its original bytes or ledger."""
    archive = path.with_name(path.name + "." + value["attestationId"] + ".history")
    try:
        os.link(path, archive, follow_symlinks=False)
    except FileExistsError:
        if _load(archive) != value:
            raise DecisionError("attestation-storage-error") from None


def _confirm(phrase):
    """Read the user's typed confirmation from the controlling terminal itself.

    stdin/stdout may be pipes or a pty owned by a host process, so neither is consulted: the
    terminal is opened explicitly and refusal covers no tty, EOF and read errors alike.
    """
    descriptor = None
    try:
        descriptor = os.open(TTY_PATH, os.O_RDWR | os.O_NOCTTY)
        if not os.isatty(descriptor):
            raise DecisionError("user-attestation-required", "the confirmation device is not a terminal")
        os.write(descriptor, ("Type exactly '" + phrase + "' to confirm: ").encode())
        line = b""
        while not line.endswith(b"\n"):
            chunk = os.read(descriptor, 1024)
            if not chunk or len(line) > 4096:
                raise EOFError
            line += chunk
        line = line.decode()
    except (EOFError, OSError, UnicodeError):
        raise DecisionError("user-attestation-required", "the terminal confirmation could not be read") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not line.endswith("\n") or line.rstrip("\r\n") != phrase:
        raise DecisionError("user-attestation-required", "the typed confirmation did not match")


def attest(workspace, run_id, *, model_id, images, token_budget, purposes=None, ttl=1800, revoke=False):
    """Interactive user action; images are the original reference bytes.

    The confirmation is a fixed phrase naming this run and model, typed on /dev/tty. That is a
    user-consent step, not isolation from a process running as the same OS user.
    """
    from decision_control import DecisionController
    workspace = Path(workspace).resolve(strict=True)
    path = _path(workspace, run_id)
    if not revoke:
        require_no_credential()
        _identifier(model_id)
        if (type(token_budget) is not int or not DEFAULT_REQUEST_TOKEN_LIMIT <= token_budget <= 2**53 - 1
                or type(ttl) is not int or not 0 < ttl <= MAX_TTL or not images or not _purposes(purposes)):
            raise DecisionError("invalid-attestation")
        hashes = sorted({hashlib.sha256(_file(workspace, image, maximum=64 * 1024 * 1024, capture=True)).hexdigest()
                         for image in images})
        disclosure = (f"I have no Jev account. Purposes {', '.join(purposes)} will be decided by model provider/model {model_id}, NOT Jev. "
                      f"The reference images with SHA256 {', '.join(hashes)}, their upright versions, derived crops and bound lifecycle evidence "
                      f"go to that named model provider through the host agent. Workspace {workspace}; run {run_id}; "
                      f"token budget {token_budget}; expires in {ttl} seconds. No core network call. "
                      "Revoke with route.py revoke-no-jev-account --workspace WS --run-id RUN.")
        phrase = f"attest {run_id} {model_id}"
    else:
        disclosure = f"Revoke no-Jev-account routing authority for workspace {workspace}; run {run_id}."
        phrase = f"revoke {run_id}"
    print(disclosure)
    _confirm(phrase)
    if revoke:
        # Store first: a revoked store stays revoked even if journal publication then fails.
        with _locked(path, create=True):
            value = _load(path)
            _preserve(path, value)
            revoked_id = value["attestationId"]
            value["enabled"] = False
            value.pop("attestationId", None)
            value["attestationId"] = canonical_digest(value)
            _store(path, value)
        data = {"attestationId": value["attestationId"], "presentedAsJev": False,
                "decider": "model:" + value["modelId"], "expiresAt": value["expiresAt"],
                "revokedAttestationId": revoked_id}
        controller = DecisionController(workspace, run_id)
        with controller._locked():
            entries, _ = controller._load()
            controller._commit(entries, "no-account-revoked", data)
        return value
    now = time.time()
    value = {"kind": "img2.route-no-jev-account", "version": 2, "policyVersion": POLICY,
             **_identity(workspace, run_id), "modelId": model_id, "imageSha256s": hashes, "purposes": list(purposes),
             "tokenBudget": token_budget, "issuedAt": now, "expiresAt": now + ttl,
             "generation": secrets.token_hex(32), "enabled": True}
    value["attestationId"] = canonical_digest(value)
    # Journal first, store second. Routing needs both, so a failure at either step (a busy
    # workspace lease, a full disk) can never leave an enabled store without its journal entry;
    # an orphan journal entry names no store and authorizes nothing. The store lock is taken
    # only after the controller lock is released (routing nests them the other way round).
    controller = DecisionController(workspace, run_id)
    with controller._locked():
        entries, _ = controller._load()
        controller._commit(entries, "no-account-attested", {
            "attestationId": value["attestationId"], "presentedAsJev": False,
            "decider": "model:" + value["modelId"], "expiresAt": value["expiresAt"],
            "attestationPolicyVersion": POLICY, "attestedPurposes": list(purposes)})
    with _locked(path, create=True):
        if path.exists():
            _preserve(path, _load(path))
        _store(path, value)
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
        if value["version"] != 2:
            raise DecisionError("no-jev-account-reattest-required")
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
                "decider": "model:" + value["modelId"], "presentedAsJev": False,
                "attestationPolicyVersion": POLICY, "attestedPurposes": list(value["purposes"])}

    def __call__(self):
        try:
            metadata = self.metadata()
            self.last_failure = None
            return {"granted": True, "purposes": list(self.initial["purposes"]), "sha256": metadata["attestationId"],
                    "tokenBudget": self.initial["tokenBudget"]}
        except DecisionError as error:
            self.last_failure = error.reason_code
            raise
