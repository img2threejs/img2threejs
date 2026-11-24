"""Base-owned, offline-by-default authority for the four Jev decision groups.

Only ``consult`` invokes explicitly supplied program-owned callbacks. There is no
provider discovery, import, network request or credential handling in this module.
The journal is authoritative: every transaction is an fsynced temp/rename of its
hash chain, under a process lock. The workspace lease outlives that lock/process.
A reopened controller must consult with current probes before consuming advice.
Application completion records the caller's retention assessment, never a claim
that this controller measured quality. CLI/state integration belongs to callers.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import tempfile
import time
from contextlib import contextmanager
from types import MappingProxyType


GROUPS = {
    "routing": ("img2.route-context", "route-questions-v3"),
    "build-strategy": ("img2.build-strategy-context", "strategy-questions-v1"),
    "quality-review": ("img2.quality-review-context", "quality-review-questions-v1"),
    "repair": ("img2.repair-context", "repair-decision-questions-v1"),
}
DEFAULT_REQUEST_TOKEN_LIMIT = 64000
QUALITY_SCORE_LEGEND = {
    "0": "Absent or wrong relative to the declared requirement.",
    "1": "Major mismatch with the declared requirement.",
    "2": "Partially meets the declared requirement, with visible deficiencies.",
    "3": "Meets the declared requirement.",
    "4": "Exceeds the declared requirement without changing it.",
}
_AUTH = {"credential-missing", "credential-rejected", "credential-storage-error"}
_UNAVAILABLE = {"not-installed", "capability-unavailable", "consent-declined", "login-declined"}
_REQUEST_FAILURES = {"provider-error", "timeout", "malformed", "out-of-set", "ambiguous-paid-attempt"}
_INPUT_FAILURES = {"invalid-credential", "request-too-large", "credential-in-context",
                   "missing-placement-evidence", "consent-busy"}
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
_SEMANTIC_LABEL = re.compile(r"[a-z][a-z0-9-]{0,95}\Z")
_FORBIDDEN = {
    "apikey", "api_key", "authorization", "credential", "credentials", "secret",
    "password", "token", "data_base64", "instructions", "prompt", "usertext",
    "code", "command", "commands", "disablegate", "lowerthreshold", "decisionrefs",
}


class DecisionError(ValueError):
    def __init__(self, reason_code, message=None):
        self.reason_code = reason_code
        super().__init__(message or reason_code)


def _fail(reason):
    raise DecisionError(reason)


def _json_bytes(value):
    _validate_json(value)
    try:
        result = json.dumps(value, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("invalid-json")
    if len(result) > 2 * 1024 * 1024:
        _fail("context-too-large")
    return result


def canonical_digest(value):
    """SHA256 of strict canonical JSON; rejects non-JSON and non-finite values."""
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _validate_json(value, depth=0, count=None):
    if count is None:
        count = [0]
    count[0] += 1
    if depth > 32 or count[0] > 50000:
        _fail("context-too-large")
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            _fail("non-finite-number")
        return
    if type(value) is str:
        if len(value) > 16384:
            _fail("context-too-large")
        return
    if type(value) is list:
        for item in value:
            _validate_json(item, depth + 1, count)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                _fail("invalid-json")
            _validate_json(key, depth + 1, count)
            _validate_json(item, depth + 1, count)
        return
    _fail("invalid-json")


def _identifier(value):
    if type(value) is not str or not _ID.fullmatch(value):
        _fail("invalid-identifier")
    return value


def _revision(value):
    if type(value) is int and value >= 0:
        return value
    return _identifier(value)


def _sha(value):
    if type(value) is not str or not _HEX.fullmatch(value):
        _fail("invalid-sha256")
    return value


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        _fail("invalid-number")
    return value


def _number(value, low=0, high=1):
    if (type(value) not in (int, float) or (type(value) is float and not math.isfinite(value))
            or not low <= value <= high):
        _fail("invalid-number")
    return value


_PLACEMENT_FACT_FIELDS = {
    "camera": ("type", "position", "target", "up", "fov", "aspect", "near", "far"),
    "depth": ("cameraDepth", "relativeDepth", "metricLandmark"),
    "support": ("mode", "supportObjectId"),
    "sizePrior": ("minScale", "maxScale", "authoredMetricHeight", "heightBand"),
}


def _placement_facts(facts):
    def require(condition):
        if not condition:
            _fail("missing-placement-evidence")
    def number(value, positive=False):
        return (type(value) in (int, float) and -1e12 <= value <= 1e12
                and (not positive or value > 0))
    def vector(value, positive=False):
        return type(value) is list and len(value) == 3 and all(number(v, positive) for v in value)
    require(type(facts) is dict and set(facts) == set(_PLACEMENT_FACT_FIELDS))
    for role, fields in _PLACEMENT_FACT_FIELDS.items():
        require(type(facts[role]) is dict and set(facts[role]) == set(fields))
    camera, depth, support, prior = (facts[role] for role in _PLACEMENT_FACT_FIELDS)
    require(camera["type"] == "perspective"
            and all(vector(camera[key]) for key in ("position", "target", "up"))
            and camera["position"] != camera["target"] and any(camera["up"])
            and number(camera["fov"], True) and camera["fov"] < 180
            and all(number(camera[key], True) for key in ("aspect", "near", "far"))
            and camera["near"] < camera["far"])
    require(all(number(depth[key], True) for key in ("cameraDepth", "relativeDepth"))
            and camera["near"] < depth["cameraDepth"] < camera["far"]
            and vector(depth["metricLandmark"]))
    require((support["mode"] == "free" and support["supportObjectId"] is None)
            or (support["mode"] == "surface" and type(support["supportObjectId"]) is str
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", support["supportObjectId"]) is not None))
    require(vector(prior["minScale"], True) and vector(prior["maxScale"], True)
            and all(low <= high for low, high in zip(prior["minScale"], prior["maxScale"]))
            and number(prior["authoredMetricHeight"], True))
    band = prior["heightBand"]
    require(type(band) is dict and set(band) == {"min", "max"}
            and all(number(band[key], True) for key in band) and band["min"] <= band["max"])
    return facts


def placement_record_facts(records):
    """Project typed facts from caller-admitted, hash-checked producer records."""
    if type(records) is not dict:
        _fail("missing-placement-evidence")
    facts = {}
    for role, fields in _PLACEMENT_FACT_FIELDS.items():
        record = records.get("size-prior" if role == "sizePrior" else role)
        if type(record) is not dict or not set(fields) <= set(record):
            _fail("missing-placement-evidence")
        facts[role] = {key: copy.deepcopy(record[key]) for key in fields}
    band = facts["sizePrior"]["heightBand"]
    if type(band) is not dict or not {"min", "max"} <= set(band):
        _fail("missing-placement-evidence")
    facts["sizePrior"]["heightBand"] = {key: band[key] for key in ("min", "max")}
    return _placement_facts(facts)


def _safe_data(value):
    """Reject executable/secret fields and local path literals in provider data."""
    if type(value) is dict:
        for key, item in value.items():
            if key.lower().replace("-", "") in _FORBIDDEN or key.lower().endswith("path"):
                _fail("unsafe-context")
            _safe_data(item)
    elif type(value) is list:
        for item in value:
            _safe_data(item)
    elif type(value) is str:
        if value.startswith(("/", "~/", "file:", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
            _fail("unsafe-context")


def _immutable(value):
    if type(value) is dict:
        return MappingProxyType({key: _immutable(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_immutable(item) for item in value)
    return value


def _proposal_members(value):
    if (type(value) is not dict or value.get("kind") != "img2.object-proposals"
            or value.get("version") != 1 or type(value.get("objects")) is not list
            or not value["objects"]):
        _fail("invalid-route-proposal")
    members = {}
    for obj in value["objects"]:
        if type(obj) is not dict:
            _fail("invalid-route-proposal")
        object_id = _identifier(obj.get("objectId"))
        if object_id in members:
            _fail("invalid-route-proposal")
        members[object_id] = canonical_digest(obj)
    return members


class DecisionController:
    def __init__(self, workspace, run_id, *, clock=None, routing_authority=None):
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            _fail("invalid-workspace")
        self.run_id = _identifier(run_id)
        self.clock = time.time if clock is None else clock
        if not callable(self.clock):
            _fail("invalid-clock")
        self._consent = None
        self.routing_authority = routing_authority
        if routing_authority is not None:
            from route_no_account import RoutingNoAccount
            if (type(routing_authority) is not RoutingNoAccount
                    or routing_authority.workspace != self.workspace or routing_authority.run_id != self.run_id):
                _fail("invalid-attestation")
            routing_authority.metadata()
        self._released = False
        self.control_dir = self.workspace / ".img2threejs" / "route"
        self.record_root = self.workspace / ".img2threejs" / "decisions"
        self._mkdir(self.workspace / ".img2threejs")
        self._mkdir(self.control_dir)
        self._mkdir(self.record_root)
        for group in GROUPS:
            self._mkdir(self.record_root / group)
        self.lease_path = self.control_dir / "lease.json"
        self.journal_path = self.control_dir / "journal.jsonl"
        self._token = None
        with self._locked(check_lease=False):
            entries, state = self._load()
            if self.routing_authority is not None:
                self._routing_metadata(state)
            if self.lease_path.exists():
                lease = self._read_json(self.lease_path)
                if lease.get("runId") != self.run_id:
                    _fail("workspace-busy")
                self._token = _sha(lease.get("token"))
            else:
                self._token = secrets.token_hex(32)
                lease = {"runId": self.run_id, "token": self._token, "version": 1}
                self._exclusive_json(self.lease_path, lease)
            if self.run_id not in state["runs"]:
                self._commit(entries, "run-started", {"runId": self.run_id, "startedAt": self._now()})

    def _now(self):
        value = self.clock()
        if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)) or value < 0:
            _fail("invalid-clock")
        return value

    def _routing_metadata(self, state):
        metadata = self.routing_authority.metadata()
        attested = state["noAccountAttestations"].get(metadata["attestationId"])
        if (not attested or attested["runId"] != self.run_id
                or attested["decider"] != metadata["decider"]
                or attested.get("attestationPolicyVersion") != metadata["attestationPolicyVersion"]
                or attested.get("attestedPurposes") != metadata["attestedPurposes"]):
            _fail("attestation-unproven")
        return metadata

    def _mkdir(self, path):
        if path.is_symlink():
            _fail("unsafe-path")
        path.mkdir(exist_ok=True)
        if not path.is_dir() or path.resolve() != path:
            _fail("unsafe-path")

    def _check_control_path(self, path):
        allowed = {self.control_dir} | {self.record_root / group for group in GROUPS}
        if path.parent not in allowed or path.is_symlink():
            _fail("unsafe-path")
        for parent in (path.parent, self.record_root, self.control_dir, self.control_dir.parent):
            if parent.is_symlink() or parent.resolve() != parent:
                _fail("unsafe-path")
        if path.exists() and not path.is_file():
            _fail("unsafe-path")

    @contextmanager
    def _locked(self, *, check_lease=True):
        path = self.control_dir / "lock"
        self._check_control_path(path)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if check_lease:
                if self._released:
                    _fail("lease-lost")
                lease = self._read_json(self.lease_path)
                if lease != {"runId": self.run_id, "token": self._token, "version": 1}:
                    _fail("lease-lost")
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read_bytes(self, path):
        self._check_control_path(path)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as source:
                return source.read()
        except OSError:
            _fail("control-storage-error")

    def _read_json(self, path):
        try:
            value = json.loads(self._read_bytes(path))
            _validate_json(value)
            if type(value) is not dict:
                _fail("control-storage-error")
            return value
        except (json.JSONDecodeError, UnicodeError):
            _fail("control-storage-error")

    def _sync_directory(self):
        fd = os.open(self.control_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _exclusive_json(self, path, value):
        self._check_control_path(path)
        fd, temporary = tempfile.mkstemp(prefix=".lease-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as target:
                target.write(_json_bytes(value) + b"\n")
                target.flush()
                os.fsync(target.fileno())
            self._check_control_path(path)
            # link publishes a complete file atomically and refuses replacement.
            os.link(temporary, path)
            self._sync_directory()
        finally:
            os.unlink(temporary)

    def _atomic_bytes(self, path, value):
        self._check_control_path(path)
        fd, temporary = tempfile.mkstemp(prefix=".decision-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as target:
                target.write(value)
                target.flush()
                os.fsync(target.fileno())
            self._check_control_path(path)
            os.replace(temporary, path)
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _load(self):
        entries = []
        state = {"runs": {}, "records": {}, "contexts": {}, "refs": {}, "epochs": {},
                 "attempts": {}, "applications": {}, "routeRounds": {}, "routeLinks": [],
                 "recordSequences": {}, "recordAttempts": {}, "routeDispatches": [], "decisionBlocks": {},
                 "noAccountAttestations": {}}
        if not self.journal_path.exists():
            if self.lease_path.exists():
                _fail("journal-broken")
            return entries, state
        raw = self._read_bytes(self.journal_path)
        if not raw or not raw.endswith(b"\n"):
            _fail("journal-broken")
        previous = "0" * 64
        try:
            for line in raw.splitlines():
                entry = json.loads(line)
                if type(entry) is not dict or set(entry) != {"sequence", "previousHash", "event", "data", "hash"}:
                    _fail("journal-broken")
                body = {key: value for key, value in entry.items() if key != "hash"}
                if (type(entry["sequence"]) is not int or entry["sequence"] != len(entries)
                        or entry["previousHash"] != previous or canonical_digest(body) != entry["hash"]):
                    _fail("journal-broken")
                self._reduce(state, entry["event"], entry["data"], sequence=entry["sequence"])
                entries.append(entry)
                previous = entry["hash"]
        except (ValueError, TypeError, KeyError, UnicodeError):
            _fail("journal-broken")
        return entries, state

    @staticmethod
    def _reduce(state, event, data, *, sequence=None):
        run_id = data["runId"]
        if event == "run-started":
            state["runs"][run_id] = {"objects": {}, "tokensUsed": 0, "tokensReserved": 0,
                                      "tokenBudget": None, "unavailable": None, "groupUnavailable": {}}
        elif event == "object-started":
            state["runs"][run_id]["objects"][data["objectId"]] = {
                "startedAt": data["startedAt"], "total": 0, "perPass": {}}
        elif event == "no-account-attested":
            state["noAccountAttestations"][data["attestationId"]] = copy.deepcopy(data)
        elif event == "no-account-revoked":
            state["noAccountAttestations"].pop(data["revokedAttestationId"], None)
        elif event == "no-account-selected":
            attested = state["noAccountAttestations"].get(data["attestationId"])
            if (not attested or attested["runId"] != run_id or attested["decider"] != data["decider"]
                    or data["presentedAsJev"] is not False):
                _fail("attestation-unproven")
        elif event == "consent-observed":
            state["runs"][run_id]["tokenBudget"] = data["grant"]["tokenBudget"]
        elif event == "unavailable":
            run = state["runs"][run_id]
            if data["scope"] == "run":
                run["unavailable"] = data["reasonCode"]
            elif data["scope"] == "group":
                run["groupUnavailable"][data["group"]] = data["reasonCode"]
        elif event == "attempt-pending":
            state["runs"][run_id]["tokensReserved"] += data["reservation"]
            state["attempts"][data["attemptId"]] = dict(data, pendingAtSequence=sequence)
        elif event in {"attempt-result", "attempt-failure"}:
            run = state["runs"][run_id]
            run["tokensUsed"] += data["tokens"]
            run["tokensReserved"] -= data["release"]
            attempt = state["attempts"][data["attemptId"]]
            attempt.update(data)
            attempt["finishedAtSequence"] = sequence
            attempt["status"] = "result-ready" if event == "attempt-result" else "failed"
        elif event == "decision-recorded":
            record = data["record"]
            state["records"][record["recordId"]] = record
            state["contexts"][record["recordId"]] = data["context"]
            state["refs"][data["refKey"]] = record["recordId"]
            state["recordSequences"][record["recordId"]] = sequence
            state["recordAttempts"][record["recordId"]] = data.get("attemptId")
            previous = state["epochs"].get(data["refKey"])
            epoch = previous["epoch"] if previous else 0
            if previous and previous["contextDigest"] != record["contextDigest"]:
                epoch += 1
            state["epochs"][data["refKey"]] = {"epoch": epoch, "contextDigest": record["contextDigest"]}
            if data.get("attemptId"):
                state["attempts"][data["attemptId"]]["status"] = "completed"
        elif event == "route-round-dispatch":
            DecisionController._reduce_route_dispatch(state, data, sequence)
        elif event == "route-round-sealed":
            DecisionController._reduce_route_round(state, data, sequence)
        elif event == "route-round-linked":
            DecisionController._reduce_route_link(state, data)
        elif event == "application-pending":
            application = data["application"]
            previous = state["applications"].get(application["applicationId"])
            record = state["records"][application["decisionRef"]]
            if (application["group"] != record["group"] or application["subject"] != record["subject"]
                    or application["applicationId"] != canonical_digest({
                        "decisionRef": application["decisionRef"], "trialRevision": application["trialRevision"]})
                    or application["inputRevision"] != record["subject"]["revision"]
                    or application["inputBindings"] != record["bindings"]
                    or (previous is not None and previous != application)):
                _fail("journal-broken")
            state["applications"][application["applicationId"]] = application
            if previous is None and record["group"] == "repair":
                subject = record["subject"]
                obj = state["runs"][run_id]["objects"][subject["objectId"]]
                obj["total"] += 1
                obj["perPass"][subject["passId"]] = obj["perPass"].get(subject["passId"], 0) + 1
        elif event == "application-finished":
            state["applications"][data["applicationId"]] = data["application"]
        elif event == "workflow-quality-blocked":
            state["decisionBlocks"][data["block"]["blockId"]] = data
        elif event not in {"probe", "lease-released", "retried-after-crash"}:
            _fail("journal-broken")

    def _commit(self, entries, event, data):
        data = dict(data, runId=self.run_id)
        body = {"sequence": len(entries), "previousHash": entries[-1]["hash"] if entries else "0" * 64,
                "event": event, "data": data}
        entry = dict(body, hash=canonical_digest(body))
        entries.append(entry)
        self._atomic_bytes(self.journal_path, b"".join(_json_bytes(item) + b"\n" for item in entries))
        return entry

    @staticmethod
    def _rounds(state, run_id, route_revision):
        return state["routeRounds"].get(run_id, {}).get(canonical_digest(route_revision), [])

    @staticmethod
    def _route_members(state, run_id, route_revision, round_number, context):
        rounds = DecisionController._rounds(state, run_id, route_revision)
        if type(round_number) is not int or round_number not in {1, 2} or len(rounds) < round_number:
            _fail("route-round-unsealed")
        if context["group"] != "routing" or context["subject"]["runId"] != run_id:
            _fail("route-round-subject-mismatch")
        selected = rounds[round_number - 1]
        initial = selected["initialInputs"]
        if (context["policyVersion"] != initial["policyVersion"]
                or context["questionSetVersion"] != initial["questionSetVersion"]
                or context["requestedModel"] != initial["requestedModel"]):
            _fail("route-round-context-mismatch")
        references = [b for b in context["bindings"] if b["role"] == "reference"]
        requests = [b for b in context["bindings"] if b["role"] == "route-request"]
        if (len(references) != 1 or references[0]["sha256"] != initial["reference"]
                or len(requests) != 1 or requests[0]["sha256"] != canonical_digest(initial["request"])):
            _fail("route-round-context-mismatch")
        # Group routing can cover instances introduced in different rounds.
        allowed = {}
        relevant = rounds[:round_number] if context["subject"]["passId"] == "routing" else [selected]
        for round_data in relevant:
            allowed.update(_proposal_members(round_data["proposalValue"]))
        proposals = [b for b in context["bindings"] if b["role"] == "object-proposal"]
        if not proposals or len({b["sha256"] for b in proposals}) != len(proposals):
            _fail("route-round-proposal-mismatch")
        inverse = {sha: object_id for object_id, sha in allowed.items()}
        if any(b["sha256"] not in inverse for b in proposals):
            _fail("route-round-proposal-mismatch")
        object_ids = [inverse[b["sha256"]] for b in proposals]
        if context["subject"]["objectId"] not in object_ids:
            _fail("route-round-subject-mismatch")
        if context["subject"]["passId"] != "routing" and object_ids != [context["subject"]["objectId"]]:
            _fail("route-round-subject-mismatch")
        return object_ids, [b["sha256"] for b in proposals]

    @staticmethod
    def _reduce_route_round(state, data, sequence):
        required = {"runId", "routeRevision", "roundNumber", "proposal", "initialInputsDigest",
                    "parentRoundSha256", "triggerDecisionIds", "proposalValue", "initialInputs",
                    "sealedAtSequence"}
        if type(data) is not dict or set(data) != required:
            _fail("invalid-route-round")
        run_id = _identifier(data["runId"])
        if run_id not in state["runs"]:
            _fail("route-round-subject-mismatch")
        _revision(data["routeRevision"])
        _sha(data["initialInputsDigest"])
        binding = data["proposal"]
        if (type(binding) is not dict or set(binding) != {"role", "path", "sha256"}
                or binding["role"] != "object-proposals" or type(binding["path"]) is not str):
            _fail("invalid-binding")
        _sha(binding["sha256"])
        if (canonical_digest(data["proposalValue"]) != binding["sha256"]
                or canonical_digest(data["initialInputs"]) != data["initialInputsDigest"]):
            _fail("route-round-binding-mismatch")
        _proposal_members(data["proposalValue"])
        initial = data["initialInputs"]
        if (type(initial) is not dict or not {"reference", "request", "candidates", "policyVersion",
                "questionSetVersion", "requestedModel", "proposals"} <= set(initial)
                or type(initial["candidates"]) is not list):
            _fail("route-round-context-mismatch")
        _sha(initial["reference"])
        for key in ("policyVersion", "questionSetVersion", "requestedModel"):
            _identifier(initial[key])
        candidate_ids = []
        for candidate in initial["candidates"]:
            if type(candidate) is not dict:
                _fail("route-round-context-mismatch")
            candidate_ids.append(_identifier(candidate.get("id")))
        if len(set(candidate_ids)) != len(candidate_ids):
            _fail("route-round-context-mismatch")
        if (type(data["roundNumber"]) is not int or data["roundNumber"] not in {1, 2}
                or type(data["sealedAtSequence"]) is not int or data["sealedAtSequence"] != sequence):
            _fail("route-round-order")
        rounds = DecisionController._rounds(state, run_id, data["routeRevision"])
        if len(rounds) != data["roundNumber"] - 1:
            _fail("route-round-order")
        linked_ids = {link["recordId"] for link in state["routeLinks"] if link["runId"] == run_id}
        linked_attempts = {state["recordAttempts"].get(record_id) for record_id in linked_ids}
        for attempt in state["attempts"].values():
            if attempt["runId"] != run_id or attempt["group"] != "routing":
                continue
            if attempt["attemptId"] in linked_attempts:
                continue
            # A failed Jev attempt can belong to a later authenticated model
            # fallback for the same request. A newer in-flight/unlinked call
            # cannot borrow an older link merely because its context is equal.
            covered_failure = attempt["status"] == "failed" and any(
                state["records"][record_id]["contextDigest"] == attempt["contextDigest"]
                and attempt["finishedAtSequence"] < state["recordSequences"][record_id]
                for record_id in linked_ids)
            if not covered_failure:
                _fail("route-round-order")
        if any(record["group"] == "routing" and record["subject"]["runId"] == run_id
               and record.get("model") is not None and record["recordId"] not in linked_ids
               for record in state["records"].values()):
            _fail("route-round-order")
        triggers = data["triggerDecisionIds"]
        if type(triggers) is not list or len(set(triggers)) != len(triggers):
            _fail("invalid-route-round-triggers")
        for record_id in triggers:
            _sha(record_id)
        if data["roundNumber"] == 1:
            if initial["proposals"] != data["proposalValue"]["objects"]:
                _fail("route-round-proposal-mismatch")
            if data["parentRoundSha256"] is not None or triggers:
                _fail("route-round-order")
        else:
            first = rounds[0]
            if (data["parentRoundSha256"] != first["proposal"]["sha256"]
                    or data["initialInputsDigest"] != first["initialInputsDigest"] or not triggers):
                _fail("route-round-order")
            if set(_proposal_members(first["proposalValue"])) & set(_proposal_members(data["proposalValue"])):
                _fail("duplicate-route-object")
            for record_id in triggers:
                record = state["records"].get(record_id)
                linked = any(link["recordId"] == record_id and link["runId"] == run_id
                             and link["routeRevision"] == data["routeRevision"] and link["roundNumber"] == 1
                             for link in state["routeLinks"])
                if (record is None or not linked or record["group"] != "routing"
                        or record["subject"]["runId"] != run_id or record["subject"]["passId"] != "decomposition"
                        or record["status"] != "answered" or record.get("model") is None
                        or "decomposition" not in record["requirements"]):
                    _fail("invalid-route-round-trigger")
                missing = record["answers"].get("missingObjects", {})
                if missing.get("type") != "noul" or _number(missing.get("noul")) < .7:
                    _fail("invalid-route-round-trigger")
        state["routeRounds"].setdefault(run_id, {}).setdefault(
            canonical_digest(data["routeRevision"]), []).append(copy.deepcopy(data))

    @staticmethod
    def _reduce_route_dispatch(state, data, sequence):
        if type(data) is not dict or set(data) != {"runId", "routeRevision", "roundNumber",
                "contextDigest", "context", "objectIds", "proposalShas"}:
            _fail("invalid-route-round-dispatch")
        if canonical_digest(data["context"]) != data["contextDigest"]:
            _fail("invalid-route-round-dispatch")
        ids, shas = DecisionController._route_members(
            state, data["runId"], data["routeRevision"], data["roundNumber"], data["context"])
        if ids != data["objectIds"] or shas != data["proposalShas"]:
            _fail("invalid-route-round-dispatch")
        previous = any(link["runId"] == data["runId"] and link["contextDigest"] == data["contextDigest"]
                       for link in state["routeLinks"])
        dispatched = any(item["runId"] == data["runId"] and item["contextDigest"] == data["contextDigest"]
                         for item in state["routeDispatches"])
        if not previous and not dispatched:
            if (any(a["runId"] == data["runId"] and a["group"] == "routing"
                    and a["contextDigest"] == data["contextDigest"] for a in state["attempts"].values())
                    or any(r["group"] == "routing" and r["subject"]["runId"] == data["runId"]
                           and r.get("model") is not None and r["contextDigest"] == data["contextDigest"]
                           for r in state["records"].values())):
                _fail("route-round-unlinked-record")
        state["routeDispatches"].append(dict(copy.deepcopy(data), sequence=sequence))

    @staticmethod
    def _reduce_route_link(state, data):
        if type(data) is not dict or set(data) != {"runId", "routeRevision", "roundNumber",
                "recordId", "contextDigest", "objectIds", "proposalShas"}:
            _fail("invalid-route-round-link")
        run_id = _identifier(data["runId"])
        _revision(data["routeRevision"])
        record = state["records"].get(_sha(data["recordId"]))
        context = state["contexts"].get(data["recordId"])
        if (record is None or context is None or record.get("model") is None
                or record["contextDigest"] != data["contextDigest"]
                or canonical_digest(context) != data["contextDigest"]
                or record["subject"] != context["subject"] or record["bindings"] != context["bindings"]
                or canonical_digest({k: v for k, v in record.items() if k != "recordId"}) != record["recordId"]):
            _fail("invalid-route-round-link")
        attempt = state["attempts"].get(state["recordAttempts"].get(data["recordId"]))
        if (attempt is None or attempt.get("status") != "completed" or "answer" not in attempt
                or attempt["runId"] != run_id or attempt.get("contextDigest") != data["contextDigest"]
                or attempt["answer"].get("answers") != record["answers"]
                or attempt["answer"].get("model") != record["model"]):
            _fail("invalid-route-round-link")
        ids, shas = DecisionController._route_members(
            state, run_id, data["routeRevision"], data["roundNumber"], context)
        if ids != data["objectIds"] or shas != data["proposalShas"]:
            _fail("invalid-route-round-link")
        rounds = DecisionController._rounds(state, run_id, data["routeRevision"])
        previous_links = [link for link in state["routeLinks"] if link["runId"] == run_id
                          and link["recordId"] == data["recordId"]
                          and link["contextDigest"] == data["contextDigest"] and link["proposalShas"] == shas]
        previous = bool(previous_links)
        if previous:
            candidate_facts = rounds[data["roundNumber"] - 1]["initialInputs"]["candidates"]
            if not any(DecisionController._rounds(state, run_id, link["routeRevision"])[
                    link["roundNumber"] - 1]["initialInputs"]["candidates"] == candidate_facts
                    for link in previous_links):
                _fail("route-round-context-mismatch")
        dispatched = any(item["runId"] == run_id and item["routeRevision"] == data["routeRevision"]
                         and item["roundNumber"] == data["roundNumber"]
                         and item["contextDigest"] == data["contextDigest"]
                         and item["sequence"] < state["recordSequences"][data["recordId"]]
                         for item in state["routeDispatches"])
        if not previous and not dispatched:
            _fail("route-round-unlinked-record")
        if state["recordSequences"][data["recordId"]] <= rounds[data["roundNumber"] - 1]["sealedAtSequence"] and not previous:
            _fail("route-round-unlinked-record")
        if data in state["routeLinks"]:
            _fail("duplicate-route-round-link")
        state["routeLinks"].append(copy.deepcopy(data))

    def _initial_route_binding(self, digest):
        return {"role": "routing-inputs", "path": f".img2threejs/route-inputs/routing-inputs/{digest}.json",
                "sha256": digest}

    def seal_route_round(self, *, route_revision, round_number, proposal, initial_inputs_digest,
                         parent_round_sha256, trigger_decision_ids):
        """Seal proposed inventory BEFORE consultation, never image completeness.

        The initial inputs use stage-zero's existing content-addressed snapshot.
        Legacy unlinked model calls cannot be retroactively assigned to a round.
        """
        _revision(route_revision)
        _sha(initial_inputs_digest)
        proposal = copy.deepcopy(proposal)
        with self._locked():
            entries, state = self._load()
            data = {"runId": self.run_id, "routeRevision": route_revision, "roundNumber": round_number,
                    "proposal": proposal, "initialInputsDigest": initial_inputs_digest,
                    "parentRoundSha256": parent_round_sha256,
                    "triggerDecisionIds": copy.deepcopy(trigger_decision_ids),
                    "proposalValue": self._binding(proposal, json_value=True),
                    "initialInputs": self._binding(self._initial_route_binding(initial_inputs_digest), json_value=True),
                    "sealedAtSequence": len(entries)}
            rounds = self._rounds(state, self.run_id, route_revision)
            for prior in rounds:
                self._binding(prior["proposal"])
            repeated = type(round_number) is int and 1 <= round_number <= len(rounds)
            if repeated:
                existing = rounds[round_number - 1]
                data["sealedAtSequence"] = existing["sealedAtSequence"]
                if data != existing:
                    _fail("route-round-conflict")
            else:
                self._reduce_route_round(state, data, len(entries))
            # Trigger records must still match their immutable local records and
            # input files; replay validation alone is not a freshness check.
            for record_id in data["triggerDecisionIds"]:
                record = state["records"][record_id]
                if self._read_json(self.record_root / "routing" / (record_id + ".json")) != record:
                    _fail("record-mismatch")
                self._context(state["contexts"][record_id])
            if not repeated:
                self._commit(entries, "route-round-sealed", data)
            return copy.deepcopy(data)

    def _binding(self, binding, *, json_value=False):
        if type(binding) is not dict or set(binding) != {"role", "path", "sha256"}:
            _fail("invalid-binding")
        _identifier(binding["role"])
        _sha(binding["sha256"])
        relative = binding["path"]
        if (type(relative) is not str or not relative or "\\" in relative
                or PurePosixPath(relative).is_absolute()
                or any(part in {"", ".", ".."} for part in relative.split("/"))):
            _fail("unsafe-path")
        path = self.workspace / relative
        if any(path == root or root in path.parents for root in (self.control_dir, self.record_root)):
            _fail("unsafe-path")
        directory = os.open(self.workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = relative.split("/")
            for component in parts[:-1]:
                try:
                    child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                except OSError:
                    _fail("unsafe-path")
                os.close(directory)
                directory = child
            try:
                fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            except OSError:
                _fail("evidence-missing" if not path.exists() else "unsafe-path")
            with os.fdopen(fd, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    _fail("unsafe-path")
                digest = hashlib.sha256()
                parts_json = [] if json_value else None
                json_size = 0
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                    if json_value:
                        json_size += len(chunk)
                        if json_size > 2 * 1024 * 1024:
                            _fail("context-too-large")
                        parts_json.append(chunk)
        finally:
            os.close(directory)
        if digest.hexdigest() != binding["sha256"]:
            _fail("stale-evidence")
        if json_value:
            try:
                value = json.loads(b"".join(parts_json))
            except (ValueError, UnicodeError):
                _fail("invalid-json")
            if canonical_digest(value) != binding["sha256"]:
                _fail("noncanonical-evidence")
            return value

    def _context(self, context):
        if len(_json_bytes(context)) > 256 * 1024:
            _fail("context-too-large")
        required = {"kind", "version", "group", "subject", "bindings", "candidates", "observations",
                    "requirements", "policyVersion", "questionSetVersion", "requestedModel"}
        if type(context) is not dict or set(context) != required:
            _fail("invalid-context")
        group = context["group"]
        if type(group) is not str or group not in GROUPS:
            _fail("group-mismatch")
        if context["kind"] != GROUPS[group][0] or type(context["version"]) is not int or context["version"] != 1:
            _fail("invalid-context-version")
        if context["questionSetVersion"] != GROUPS[group][1]:
            _fail("question-set-mismatch")
        _identifier(context["policyVersion"])
        _identifier(context["requestedModel"])
        subject = context["subject"]
        if type(subject) is not dict or set(subject) != {"runId", "objectId", "revision", "passId"}:
            _fail("invalid-subject")
        for key in ("runId", "objectId", "passId"):
            _identifier(subject[key])
        _revision(subject["revision"])
        if subject["runId"] != self.run_id:
            _fail("subject-mismatch")
        if type(context["bindings"]) is not list or not context["bindings"]:
            _fail("evidence-missing")
        seen = set()
        for binding in context["bindings"]:
            self._binding(binding)
            key = (binding["role"], binding["path"])
            if key in seen:
                _fail("invalid-binding")
            seen.add(key)
        candidates = context["candidates"]
        localization = (group == "routing" and type(context["requirements"]) is dict
                        and "decomposition" in context["requirements"])
        if (type(candidates) is not list or (localization and candidates)
                or (not candidates and group != "quality-review" and not localization)):
            _fail("invalid-candidates")
        ids = []
        for candidate in candidates:
            if type(candidate) is not dict:
                _fail("invalid-candidates")
            ids.append(_identifier(candidate.get("id")))
        if len(set(ids)) != len(ids) or len(ids) > 100:
            _fail("invalid-candidates")
        if type(context["observations"]) is not dict or type(context["requirements"]) is not dict:
            _fail("invalid-context")
        requirements = context["requirements"]
        if type(requirements.get("criticalFeatures")) is not list or type(requirements.get("requiredViews")) is not list:
            _fail("invalid-requirements")
        feature_ids = []
        for feature in requirements["criticalFeatures"]:
            if type(feature) is not dict:
                _fail("invalid-requirements")
            feature_ids.append(_identifier(feature.get("id")))
        if len(set(feature_ids)) != len(feature_ids) or len(feature_ids) > 100:
            _fail("invalid-requirements")
        projected = copy.deepcopy(context)
        projected["bindings"] = [{"role": b["role"], "sha256": b["sha256"]} for b in context["bindings"]]
        _safe_data(projected)
        self._decomposition(projected)
        self._placement(projected)
        if group == "quality-review":
            self._quality_items(projected)
        return projected

    @staticmethod
    def _placement(context):
        placement = context["requirements"].get("placement")
        placement_pass = context["subject"]["passId"] in {"placement-size", "placement-yaw"}
        if placement is None and not placement_pass:
            return
        if (context["group"] != "build-strategy" or type(placement) is not dict
                or set(placement) != {"question"} or placement.get("question") not in {"size", "yaw"}
                or context["subject"]["passId"] != "placement-" + placement["question"]
                or context["policyVersion"] != "placement-policy-v1"
                or "decomposition" in context["requirements"]):
            _fail("invalid-placement-context")
        roles = [binding["role"] for binding in context["bindings"]]
        if any(roles.count(role) != 1 for role in
               ("reference", "inventory", "artifact", "size-prior", "camera", "depth", "support")):
            _fail("missing-placement-evidence")
        observations = context.get("observations")
        if type(observations) is not dict:
            _fail("missing-placement-evidence")
        _placement_facts(observations.get("placementFacts"))
        values = set()
        for candidate in context["candidates"]:
            if set(candidate) != {"id", "value"}:
                _fail("invalid-placement-candidate")
            value = candidate["value"]
            if placement["question"] == "yaw":
                if type(value) is not int or not 0 <= value <= 7:
                    _fail("invalid-placement-candidate")
            else:
                if type(value) is not list or len(value) != 3:
                    _fail("invalid-placement-candidate")
                for axis in value:
                    if type(axis) not in (int, float) or not math.isfinite(axis) or axis <= 0:
                        _fail("invalid-placement-candidate")
            digest = canonical_digest(value)
            if digest in values:
                _fail("duplicate-placement-value")
            values.add(digest)

    @staticmethod
    def _decomposition(context):
        decomposition = context["requirements"].get("decomposition")
        if decomposition is None:
            return None
        if (context["group"] != "routing" or type(decomposition) is not dict
                or set(decomposition) != {"image", "objects", "inventory"}
                or context["candidates"]):
            _fail("invalid-requirements")
        image_id = _identifier(decomposition["image"])
        images = context["observations"].get("images", [])
        if type(images) is not list or len(images) > 32:
            _fail("invalid-requirements")
        asset_ids = set()
        for image in images:
            if type(image) is not dict or set(image) != {"role", "asset"}:
                _fail("invalid-requirements")
            _identifier(image["role"])
            asset_id = _identifier(image["asset"])
            if asset_id in asset_ids:
                _fail("invalid-requirements")
            asset_ids.add(asset_id)
        if image_id not in asset_ids:
            _fail("invalid-requirements")
        objects = decomposition["objects"]
        if type(objects) is not list or not 1 <= len(objects) <= 12:
            _fail("invalid-requirements")
        inventory = decomposition["inventory"]
        if type(inventory) is not list or not 1 <= len(inventory) <= 256:
            _fail("invalid-requirements")
        inventory_by_id = {}
        for obj in inventory:
            if (type(obj) is not dict or set(obj) != {"id", "label", "role", "approxBox"}
                    or type(obj["role"]) is not str
                    or obj["role"] not in {"scene-container", "subject", "prop", "set-dressing"}):
                _fail("invalid-requirements")
            object_id = _identifier(obj["id"])
            if type(obj["label"]) is not str or not _SEMANTIC_LABEL.fullmatch(obj["label"]):
                _fail("invalid-requirements")
            if object_id in inventory_by_id:
                _fail("invalid-requirements")
            bounds = obj["approxBox"]
            if type(bounds) is not list or len(bounds) != 4:
                _fail("invalid-requirements")
            for edge in bounds:
                _number(edge)
            if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
                _fail("invalid-requirements")
            inventory_by_id[object_id] = obj
        object_ids = set()
        for obj in objects:
            if type(obj) is not dict or set(obj) != {"id", "label", "approxBox"}:
                _fail("invalid-requirements")
            object_id = _identifier(obj["id"])
            if type(obj["label"]) is not str or not _SEMANTIC_LABEL.fullmatch(obj["label"]):
                _fail("invalid-requirements")
            bounds = obj["approxBox"]
            if type(bounds) is not list or len(bounds) != 4:
                _fail("invalid-requirements")
            for edge in bounds:
                _number(edge)
            if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
                _fail("invalid-requirements")
            if object_id in object_ids:
                _fail("invalid-requirements")
            object_ids.add(object_id)
            entry = inventory_by_id.get(object_id)
            if entry is None or obj != {key: entry[key] for key in ("id", "label", "approxBox")}:
                _fail("invalid-requirements")
        return objects

    @staticmethod
    def _ref_key(context):
        return canonical_digest({"group": context["group"], "subject": context["subject"]})

    def _grant(self, callback):
        if not callable(callback):
            _fail("consent-unproven")
        try:
            grant = callback()
        except Exception:
            _fail("consent-probe-error")
        _json_bytes(grant)
        if type(grant) is not dict or set(grant) != {"granted", "purposes", "sha256", "tokenBudget"}:
            _fail("invalid-consent")
        if type(grant["granted"]) is not bool or type(grant["purposes"]) is not list:
            _fail("invalid-consent")
        if any(type(p) is not str or p not in GROUPS for p in grant["purposes"]):
            _fail("invalid-consent")
        if len(set(grant["purposes"])) != len(grant["purposes"]):
            _fail("invalid-consent")
        _sha(grant["sha256"])
        _integer(grant["tokenBudget"])
        return copy.deepcopy(grant)

    def _probe(self, callback):
        if not callable(callback):
            return {"status": "availability-unproven"}
        try:
            result = callback()
        except Exception:
            return {"status": "availability-unproven"}
        if type(result) is not dict or set(result) - {"status", "reasonCode"}:
            _fail("invalid-availability")
        status = result.get("status")
        if type(status) is not str or status not in {"present", "not-installed", "capability-unavailable", "availability-unproven"} | _AUTH:
            _fail("invalid-availability")
        # Never persist arbitrary provider text, exceptions or secret-bearing messages.
        # Only the trusted CLI probe can attest to an actual refusal/prompt limit.
        # Repeated probes themselves never count as user login prompts.
        if status in _AUTH and result.get("reasonCode") == "login-declined":
            return {"status": status, "reasonCode": "login-declined"}
        return {"status": status}

    def _record(self, entries, context, projected, grant, status, reason, *, advisory=None,
                decider=None, attempt_id=None, fallback_reason=None):
        record = {"kind": "img2.decision-record", "version": 1, "group": context["group"],
                  "subject": copy.deepcopy(context["subject"]), "contextDigest": canonical_digest(context),
                  "providerContextDigest": canonical_digest(projected), "bindings": copy.deepcopy(context["bindings"]),
                  "candidates": copy.deepcopy(context["candidates"]), "requirements": copy.deepcopy(context["requirements"]),
                  "policyVersion": context["policyVersion"], "questionSetVersion": context["questionSetVersion"],
                  "requestedModel": context["requestedModel"], "status": status, "outcome": status,
                  "reasonCode": reason, "decider": decider, "model": advisory["model"] if advisory else None,
                  "answers": copy.deepcopy(advisory["answers"]) if advisory else {},
                  "usage": copy.deepcopy(advisory["usage"]) if advisory else None,
                  "consent": copy.deepcopy(grant), "createdAt": self._now()}
        if self.routing_authority is not None:
            record.update(self.routing_authority.metadata())
        if fallback_reason:
            record["unavailableReason"] = fallback_reason
        if reason in _AUTH:
            record["loginCommand"] = "img2-jev login"
            record["phase"] = "suspended"
        elif status in {"unavailable", "request-input"}:
            record["phase"] = "suspended"
        else:
            record["phase"] = "decided"
        record["recordId"] = canonical_digest(record)
        record_path = self.record_root / context["group"] / (record["recordId"] + ".json")
        if record_path.exists():
            if self._read_json(record_path) != record:
                _fail("record-mismatch")
        else:
            self._atomic_bytes(record_path, _json_bytes(record) + b"\n")
        self._commit(entries, "decision-recorded", {"record": record, "context": context,
                     "refKey": self._ref_key(context), "attemptId": attempt_id})
        return copy.deepcopy(record)

    def _advisory(self, projected, result, *, model_fallback=False):
        _json_bytes(result)
        required = {"kind", "version", "group", "subject", "contextDigest", "model", "answers", "usage"}
        if type(result) is not dict or set(result) != required:
            _fail("malformed")
        if (result["kind"] != "img2.decision-advisory" or type(result["version"]) is not int
                or result["version"] != 1):
            _fail("malformed")
        if result["group"] != projected["group"]:
            _fail("group-mismatch")
        if canonical_digest(result["subject"]) != canonical_digest(projected["subject"]):
            _fail("subject-mismatch")
        if result["contextDigest"] != canonical_digest(projected):
            _fail("context-digest-mismatch")
        _identifier(result["model"])
        if not model_fallback and result["model"] != projected["requestedModel"]:
            _fail("model-mismatch")
        answers = result["answers"]
        if type(answers) is not dict or not answers:
            _fail("malformed")
        _safe_data(answers)
        tokens = self._usage(result["usage"])
        if projected["group"] == "quality-review":
            status, reason = self._quality_answers(projected, answers)
        else:
            objects = self._decomposition(projected)
            expected = {"selection", "anyFits"} if objects is None else {"missingObjects"}
            if objects is not None:
                for obj in objects:
                    expected.update({"object:" + obj["id"] + ":visible", "object:" + obj["id"] + ":box"})
            if set(answers) != expected:
                _fail("malformed")
            if objects is None:
                choice, fits = answers["selection"], answers["anyFits"]
                if (type(choice) is not dict or set(choice) != {"type", "choice", "probabilities", "confidence"}
                        or choice["type"] != "choice" or type(fits) is not dict
                        or set(fits) != {"type", "noul"} or fits["type"] != "noul"):
                    _fail("malformed")
                ids = {candidate["id"] for candidate in projected["candidates"]}
                if type(choice["choice"]) is not str or choice["choice"] not in ids:
                    _fail("out-of-set")
                probabilities = choice["probabilities"]
                if type(probabilities) is not dict or set(probabilities) != ids:
                    _fail("out-of-set")
                for probability in probabilities.values():
                    _number(probability)
                if abs(sum(probabilities.values()) - 1) > 1e-6:
                    _fail("malformed")
                probability = probabilities[choice["choice"]]
                if probability < max(probabilities.values()):
                    _fail("malformed")
                confidence = _number(choice["confidence"])
                noul = _number(fits["noul"])
            for key in expected - {"selection", "anyFits"}:
                answer = answers[key]
                if key.endswith(":box"):
                    if (type(answer) is not dict or set(answer) != {"type", "box", "confidence"}
                            or answer["type"] != "box"):
                        _fail("malformed")
                    box = answer["box"]
                    if type(box) is not dict or set(box) != {"top", "bottom", "left", "right"}:
                        _fail("malformed")
                    for edge in box.values():
                        _number(edge)
                    if box["left"] >= box["right"] or box["top"] >= box["bottom"]:
                        _fail("malformed")
                    if answer["confidence"] is not None:
                        _number(answer["confidence"])
                else:
                    if type(answer) is not dict or set(answer) != {"type", "noul"} or answer["type"] != "noul":
                        _fail("malformed")
                    _number(answer["noul"])
            if objects is not None:
                status, reason = "answered", "answered"
            elif noul < 0.5:
                status, reason = "request-input", "none-fits"
            elif probability < (0.82 if model_fallback else 0.80) or (not model_fallback and confidence < 0.50):
                status = "uncertain" if len(ids) > 1 and not model_fallback else "request-input"
                reason = "choice-uncertain"
            else:
                status, reason = "answered", "answered"
        return copy.deepcopy(result), status, reason, tokens

    @staticmethod
    def _usage(usage):
        if type(usage) is not dict or set(usage) != {"input_tokens", "output_tokens"}:
            _fail("usage-missing")
        return _integer(usage["input_tokens"]) + _integer(usage["output_tokens"])

    @staticmethod
    def _quality_items(context):
        axes = {"silhouette-proportions", "part-relationships", "geometry",
                "material-appearance", "identity-features", "rig", "placement"}
        defaults = ["silhouette-proportions", "part-relationships", "geometry",
                    "material-appearance", "identity-features"]
        requirements = context["requirements"]
        dimensions = requirements.get("dimensions", defaults)
        if type(dimensions) is not list:
            _fail("invalid-requirements")
        items = {}
        for item in dimensions:
            item = {"id": item} if type(item) is str else item
            if type(item) is not dict or type(item.get("id")) is not str or item["id"] not in axes:
                _fail("invalid-requirements")
            key = "dimension:" + item["id"]
            if key in items:
                _fail("invalid-requirements")
            items[key] = item
        for item in requirements["criticalFeatures"]:
            if "threshold" in item:
                _number(item["threshold"], 0, 4)
            items["critical:" + item["id"]] = item
        available = context["observations"].get("availableViews", [])
        if type(available) is not list:
            _fail("invalid-requirements")
        def views(values):
            if type(values) is not list:
                _fail("invalid-requirements")
            for value in values:
                if type(value) is str:
                    _identifier(value)
                elif type(value) is int:
                    _number(value, 0, 360)
                else:
                    _fail("invalid-requirements")
            return set(values)
        available = views(available)
        global_views = views(requirements["requiredViews"])
        global_roles = requirements.get("evidenceRoles", [])
        if type(global_roles) is not list:
            _fail("invalid-requirements")
        for role in global_roles:
            _identifier(role)
        roles = {item["role"] for item in context["bindings"]}
        images = context["observations"].get("images", [])
        if type(images) is not list:
            _fail("invalid-requirements")
        image_roles = set()
        for image in images:
            if type(image) is not dict or set(image) != {"role", "asset"}:
                _fail("invalid-requirements")
            image_roles.add(_identifier(image["role"]))
            _identifier(image["asset"])
        evaluable = {}
        for key, item in items.items():
            required_roles = item.get("evidenceRoles", [])
            if type(required_roles) is not list:
                _fail("invalid-requirements")
            for role in required_roles:
                _identifier(role)
            needed = global_views | views(item.get("requiredViews", []))
            evaluable[key] = ({"reference", "render"} <= roles & image_roles
                              and (set(global_roles) | set(required_roles)) <= roles and needed <= available)
        return evaluable

    @staticmethod
    def _quality_answers(context, answers):
        evaluable = DecisionController._quality_items(context)
        if not evaluable or set(answers) != set(evaluable):
            _fail("malformed")
        uncertain = False
        for key, answer in answers.items():
            if type(answer) is not dict:
                _fail("malformed")
            if answer == {"type": "unevaluated"}:
                uncertain = True
                continue
            if not evaluable[key]:
                _fail("missing-evidence")
            if key.startswith("critical:"):
                if set(answer) != {"type", "choice", "probabilities", "confidence"} or answer["type"] != "choice":
                    _fail("malformed")
                choices = {"pass", "fail", "uncertain"}
                if type(answer["choice"]) is not str or answer["choice"] not in choices:
                    _fail("out-of-set")
                DecisionController._probabilities(answer["probabilities"], choices)
                _number(answer["confidence"])
                uncertain |= answer["choice"] == "uncertain"
            else:
                if (set(answer) != {"type", "score", "confidence", "legend", "probabilities"}
                        or answer["type"] != "score"):
                    _fail("malformed")
                _number(answer["score"], 0, 4)
                _number(answer["confidence"])
                legend = QUALITY_SCORE_LEGEND
                if answer["legend"] != legend:
                    _fail("malformed")
                DecisionController._probabilities(answer["probabilities"], set(legend))
        return ("uncertain", "review-uncertain") if uncertain else ("answered", "review-evidence-only")

    @staticmethod
    def _probabilities(probabilities, ids):
        if type(probabilities) is not dict or set(probabilities) != ids:
            _fail("out-of-set")
        for value in probabilities.values():
            _number(value)
        if abs(sum(probabilities.values()) - 1) > 1e-6:
            _fail("malformed")

    def _attempt(self, entries, state, attempt_id, context, projected, grant, callback,
                 token_limit, *, model_fallback=False):
        """Reserve before dispatch; atomically publish a result with its real debit."""
        self._context(context)
        if self._grant(self._consent) != grant:
            _fail("consent-changed")
        run = state["runs"][self.run_id]
        prior = state["attempts"].get(attempt_id)
        if prior and "answer" in prior:
            answer, status, reason, _ = self._advisory(projected, prior["answer"], model_fallback=model_fallback)
            if prior["overrun"]:
                status, reason = "request-input", "budget-overrun"
            return answer, status, reason
        if prior and "reasonCode" in prior:
            if prior["reasonCode"] in _AUTH or (self.routing_authority is not None
                                                and prior["reasonCode"] == "model-answers-required"):
                # A fresh trusted auth probe may permit a new login attempt.
                prior = None
            else:
                _fail(prior["reasonCode"])
        retries = prior["retries"] if prior else 0
        if prior:
            if retries >= 1:
                _fail("ambiguous-paid-attempt")
            retries += 1
        if run["tokensUsed"] + run["tokensReserved"] + token_limit > grant["tokenBudget"]:
            _fail("budget-exhausted")
        if prior:
            self._commit(entries, "retried-after-crash", {"attemptId": attempt_id,
                         "reasonCode": "ambiguous-paid-attempt"})
        authority = self.routing_authority.metadata() if self.routing_authority is not None else {}
        pending = self._commit(entries, "attempt-pending", {
            "attemptId": attempt_id, "contextDigest": canonical_digest(context),
            "group": context["group"], "status": "pending", "retries": retries,
            "reservation": token_limit, "decider": "model" if model_fallback else "jev",
            **authority,
        })
        self._reduce(state, "attempt-pending", pending["data"])
        response = None
        try:
            response = callback(copy.deepcopy(projected))
            answer, status, reason, tokens = self._advisory(projected, response, model_fallback=model_fallback)
        except Exception as exc:
            code = getattr(exc, "reason_code", "timeout" if isinstance(exc, TimeoutError) else "provider-error")
            unsent = {"missing-evidence", "vision-forbidden", "permission-denied", "stale-evidence",
                      "fallback-image-invalid", "fallback-image-context-required", "fallback-preflight-timeout"}
            if self.routing_authority is not None:
                unsent.add("model-answers-required")
            known = (_AUTH | _REQUEST_FAILURES | _INPUT_FAILURES | unsent | {"group-mismatch", "subject-mismatch",
                     "context-digest-mismatch", "model-mismatch"})
            code = code if type(code) is str and code in known else (
                "malformed" if isinstance(exc, DecisionError) else "provider-error")
            tokens, release = 0, 0
            if code in _AUTH | unsent:
                release = token_limit
            elif type(response) is dict:
                try:
                    tokens = self._usage(response.get("usage"))
                    release = token_limit
                except DecisionError:
                    pass
            if tokens > token_limit:
                code = "budget-overrun"
            failure = self._commit(entries, "attempt-failure", {
                "attemptId": attempt_id, "reasonCode": code, "tokens": tokens, "release": release,
            })
            self._reduce(state, "attempt-failure", failure["data"])
            _fail(code)
        overrun = tokens > token_limit
        result = self._commit(entries, "attempt-result", {
            "attemptId": attempt_id, "answer": answer, "tokens": tokens,
            "release": token_limit, "overrun": overrun,
        })
        self._reduce(state, "attempt-result", result["data"])
        if overrun:
            status, reason = "request-input", "budget-overrun"
        return answer, status, reason

    def _route_live(self, state, route_round, context, consent):
        if type(route_round) is not dict or set(route_round) != {"routeRevision", "roundNumber"}:
            _fail("invalid-route-round")
        _revision(route_round["routeRevision"])
        ids, shas = self._route_members(
            state, self.run_id, route_round["routeRevision"], route_round["roundNumber"], context)
        rounds = self._rounds(state, self.run_id, route_round["routeRevision"])
        for data in rounds[:route_round["roundNumber"]]:
            self._binding(data["proposal"])
            self._binding(self._initial_route_binding(data["initialInputsDigest"]))
            for record_id in data["triggerDecisionIds"]:
                self._current(state, state["records"][record_id], state["contexts"][record_id], consent=consent)
        self._context(context)
        grant = self._grant(consent)
        initial = rounds[0]["initialInputs"]
        if "consentSnapshotSha256" in initial and canonical_digest(grant) != initial["consentSnapshotSha256"]:
            _fail("consent-changed")
        if context["subject"]["passId"] == "routing":
            handles = [b for b in context["bindings"] if b["role"] == "candidate-handles"]
            if len(handles) != 1:
                _fail("route-round-context-mismatch")
            mapping = self._binding(handles[0], json_value=True)
            admitted = {item["id"] for item in initial["candidates"]}
            if (type(mapping) is not dict or not set(mapping.values()) <= admitted
                    or set(mapping) != {candidate["id"] for candidate in context["candidates"]}):
                _fail("route-round-context-mismatch")
        return ids, shas

    def consult(self, context, *, provider=None, fallback=None, availability=None, consent=None,
                request_token_limit=DEFAULT_REQUEST_TOKEN_LIMIT, route_round=None):
        """Consult without changing per-object context/cache identity.

        ``route_round={routeRevision, roundNumber}`` is trusted host-only metadata.
        It seals dispatch ordering and links live verified results in the journal;
        it is never projected to providers or included in their request digest.
        """
        arguments = {"provider": provider, "fallback": fallback, "availability": availability,
                     "consent": consent, "request_token_limit": request_token_limit}
        if route_round is None:
            if type(context) is dict and context.get("group") == "routing":
                with self._locked():
                    _, state = self._load()
                    if state["routeRounds"].get(self.run_id):
                        _fail("route-round-required")
            return self._consult(context, **arguments)
        context, route_round = copy.deepcopy((context, route_round))
        try:
            self._grant(consent)
        except DecisionError as exc:
            if exc.reason_code not in {"consent-unproven", "consent-probe-error"}:
                raise
            # Preserve structured suspension without a dispatch/link or a
            # second, potentially successful consent probe enabling a send.
            if exc.reason_code == "consent-unproven":
                arguments["consent"] = None
            else:
                def failed_consent():
                    raise DecisionError("consent-probe-error")
                arguments["consent"] = failed_consent
            return self._consult(context, **arguments)
        with self._locked():
            entries, state = self._load()
            ids, shas = self._route_live(state, route_round, context, consent)
            dispatch = {"runId": self.run_id, **route_round, "contextDigest": canonical_digest(context),
                        "context": context, "objectIds": ids, "proposalShas": shas}
            self._reduce_route_dispatch(state, dispatch, len(entries))
            self._commit(entries, "route-round-dispatch", dispatch)
        record = self._consult(context, **arguments)
        if record.get("model") is None:
            return record
        with self._locked():
            entries, state = self._load()
            ids, shas = self._route_live(state, route_round, context, consent)
            self._current(state, record, context, consent=consent)
            link = {"runId": self.run_id, **route_round, "recordId": record["recordId"],
                    "contextDigest": canonical_digest(context), "objectIds": ids, "proposalShas": shas}
            if link not in state["routeLinks"]:
                self._reduce_route_link(state, link)
                self._commit(entries, "route-round-linked", link)
        return record

    def _consult(self, context, *, provider=None, fallback=None, availability=None, consent=None,
                request_token_limit=DEFAULT_REQUEST_TOKEN_LIMIT):
        """Consult Jev first, journal proven outages, and constrain optional fallback.

        Callbacks receive a detached projection with no local paths. Login and
        missing probes suspend; valid uncertainty never activates fallback.
        ``request_token_limit`` is a trusted adapter's verified positive upper
        bound, never a field read from model-supplied observations. Unaccounted
        sends retain their reservations across crashes and group switches.
        """
        _integer(request_token_limit, 1)
        context = copy.deepcopy(context)
        projected = self._context(context)
        self._consent = consent
        mode = self.routing_authority
        if mode is not None and (consent is not mode or context["requestedModel"] != mode.model_id):
            _fail("attestation-binding-mismatch")
        with self._locked():
            self._context(context)
            entries, state = self._load()
            if mode is not None:
                self._routing_metadata(state)
            run = state["runs"][self.run_id]
            subject = context["subject"]
            if subject["objectId"] not in run["objects"]:
                started_at = self._now()
                self._commit(entries, "object-started", {"objectId": subject["objectId"], "startedAt": started_at})
                run["objects"][subject["objectId"]] = {"startedAt": started_at, "total": 0, "perPass": {}}
            obj = run["objects"][subject["objectId"]]
            try:
                grant = self._grant(consent)
            except DecisionError as exc:
                if exc.reason_code not in {"consent-unproven", "consent-probe-error"}:
                    raise
                return self._record(entries, context, projected, None, "request-input", exc.reason_code)
            self._commit(entries, "consent-observed", {"grant": grant})
            run["tokenBudget"] = grant["tokenBudget"]
            if mode is not None:
                self._commit(entries, "no-account-selected", mode.metadata())
            if context["group"] == "quality-review" and not any(self._quality_items(projected).values()):
                return self._record(entries, context, projected, grant, "request-input", "missing-evidence")
            current_id = state["refs"].get(self._ref_key(context))
            current = state["records"].get(current_id)
            if current:
                saved = self._read_json(self.record_root / current["group"] / (current["recordId"] + ".json"))
                if saved != current:
                    _fail("record-mismatch")
            if (mode is None and not callable(availability) and grant["granted"]
                    and not run["unavailable"] and context["group"] not in run["groupUnavailable"]):
                self._commit(entries, "probe", {
                    "group": context["group"], "contextDigest": canonical_digest(context),
                    "status": "availability-unproven",
                })
                return self._record(entries, context, projected, grant, "request-input", "availability-unproven")
            same_request = (current and current["contextDigest"] == canonical_digest(context)
                            and current["consent"] == grant)
            if same_request and current["model"] is not None:
                return copy.deepcopy(current)
            if not 0 <= self._now() - obj["startedAt"] < 3600:
                return self._record(entries, context, projected, grant, "request-input", "deadline-exhausted")
            if context["group"] == "repair" and (
                    obj["total"] >= 6 or obj["perPass"].get(subject["passId"], 0) >= 3):
                return self._record(entries, context, projected, grant, "request-input", "correction-budget-exhausted")
            reason = None if mode is not None else run["unavailable"] or run["groupUnavailable"].get(context["group"])
            if same_request and current["reasonCode"] in _REQUEST_FAILURES:
                reason = reason or current["reasonCode"]
            if not grant["granted"]:
                reason = "consent-declined"
                self._commit(entries, "unavailable", {"scope": "run", "group": context["group"], "reasonCode": reason})
            elif (context["group"] not in grant["purposes"] if mode is not None else set(grant["purposes"]) != set(GROUPS)):
                return self._record(entries, context, projected, grant, "request-input", "consent-required")
            if reason is None and mode is None:
                probe = self._probe(availability)
                self._commit(entries, "probe", {"group": context["group"], "contextDigest": canonical_digest(context), **probe})
                reason = "login-declined" if probe.get("reasonCode") == "login-declined" else probe["status"]
                if reason == "login-declined":
                    self._commit(entries, "unavailable", {
                        "scope": "run", "group": context["group"], "reasonCode": reason})
                if reason in _AUTH or reason == "availability-unproven":
                    return self._record(entries, context, projected, grant, "request-input", reason)
                if reason in {"not-installed", "capability-unavailable"}:
                    self._commit(entries, "unavailable", {"scope": "run" if reason == "not-installed" else "group",
                                 "group": context["group"], "reasonCode": reason})
                elif reason == "present":
                    reason = None
                    if not callable(provider):
                        return self._record(entries, context, projected, grant, "unavailable", "provider-unbound")
            previous_epoch = state["epochs"].get(self._ref_key(context))
            epoch = previous_epoch["epoch"] if previous_epoch else 0
            if previous_epoch and previous_epoch["contextDigest"] != canonical_digest(context):
                epoch += 1
            identity = {"contextDigest": canonical_digest(context), "consent": grant, "epoch": epoch}
            attempt_id = canonical_digest(dict(identity, decider="model:" + mode.model_id if mode is not None else "jev"))
            active_attempt = None
            if reason is None:
                try:
                    answer, status, answer_reason = self._attempt(
                        entries, state, attempt_id, context, projected, grant, provider, request_token_limit)
                except DecisionError as exc:
                    reason = exc.reason_code
                    active_attempt = attempt_id if attempt_id in state["attempts"] else None
                    if reason in {"group-mismatch", "subject-mismatch", "context-digest-mismatch", "model-mismatch"}:
                        raise
                    if mode is not None:
                        return self._record(entries, context, projected, grant, "request-input", reason,
                                            attempt_id=active_attempt)
                    if reason in _AUTH | _INPUT_FAILURES | {"missing-evidence", "vision-forbidden", "permission-denied",
                                          "stale-evidence", "budget-exhausted", "budget-overrun"}:
                        return self._record(entries, context, projected, grant, "request-input", reason,
                                            attempt_id=active_attempt)
                else:
                    self._context(context)
                    if self._grant(consent) != grant:
                        _fail("consent-changed")
                    return self._record(entries, context, projected, grant, status, answer_reason,
                                        advisory=answer, decider=("model:" if mode is not None else "jev:") + answer["model"],
                                        attempt_id=attempt_id)
            if mode is not None:
                return self._record(entries, context, projected, grant, "request-input", reason,
                                    attempt_id=active_attempt)
            if reason not in _UNAVAILABLE | _REQUEST_FAILURES:
                _fail("fallback-unproven")
            if not callable(fallback):
                return self._record(entries, context, projected, grant, "unavailable", reason, attempt_id=active_attempt)
            self._commit(entries, "probe", {"group": context["group"], "contextDigest": canonical_digest(context),
                         "status": "unavailable", "reasonCode": reason, "fallbackAuthorized": True})
            fallback_id = canonical_digest(dict(identity, decider="model", unavailableReason=reason))
            try:
                bind_context = getattr(type(fallback), "bind_context", None)
                callback = bind_context(fallback, self.workspace, copy.deepcopy(context)) if callable(bind_context) else fallback
                if not callable(callback):
                    _fail("fallback-invalid")
                answer, status, answer_reason = self._attempt(
                    entries, state, fallback_id, context, projected, grant, callback, request_token_limit,
                    model_fallback=True)
            except DecisionError as exc:
                if exc.reason_code in {"group-mismatch", "subject-mismatch", "context-digest-mismatch", "model-mismatch"}:
                    raise
                answer_reason = exc.reason_code if exc.reason_code in {
                    "budget-exhausted", "budget-overrun", "missing-evidence", "ambiguous-paid-attempt"} else "fallback-invalid"
                return self._record(entries, context, projected, grant, "request-input", answer_reason,
                                    fallback_reason=reason, attempt_id=active_attempt)
            self._context(context)
            if self._grant(consent) != grant:
                _fail("consent-changed")
            return self._record(entries, context, projected, grant, status, answer_reason, advisory=answer,
                                decider="model:" + answer["model"], fallback_reason=reason,
                                attempt_id=fallback_id)

    def _current(self, state, record, context, *, consent=None, historical=False, check_consent=True):
        _json_bytes(record)
        if type(record) is not dict:
            _fail("invalid-record")
        if type(context) is not dict or "group" not in context or "subject" not in context:
            _fail("invalid-context")
        record_id = _sha(record.get("recordId"))
        stored = state["records"].get(record_id)
        if stored is None or stored != record:
            _fail("record-mismatch")
        record_path = self.record_root / record["group"] / (record["recordId"] + ".json")
        if self._read_json(record_path) != stored:
            _fail("record-mismatch")
        if record["group"] != context["group"]:
            _fail("group-mismatch")
        if canonical_digest(record["subject"]) != canonical_digest(context["subject"]):
            _fail("subject-mismatch")
        self._context(context)
        if record["contextDigest"] != canonical_digest(context):
            _fail("context-digest-mismatch")
        if not historical and state["refs"].get(self._ref_key(context)) != record["recordId"]:
            _fail("stale-decision")
        if record.get("decisionMode") == "no-account":
            if self.routing_authority is None:
                _fail("no-jev-account-attestation-required")
            metadata = self._routing_metadata(state)
            if any(record.get(key) != value for key, value in metadata.items()):
                _fail("attestation-changed")
        if check_consent and self._grant(consent if consent is not None else self._consent) != record["consent"]:
            _fail("consent-changed")
        return stored

    def remaining_object_seconds(self, object_id):
        """Read the durable 60-minute object deadline; never create or reset it."""
        _identifier(object_id)
        with self._locked():
            _, state = self._load()
            obj = state["runs"][self.run_id]["objects"].get(object_id)
            if obj is None:
                _fail("object-not-started")
            elapsed = self._now() - obj["startedAt"]
            return max(0.0, 3600.0 - elapsed) if elapsed >= 0 else 0.0

    def consume(self, record, context, *, consent=None):
        """Revalidate immutable authority against live local evidence and consent."""
        context = copy.deepcopy(context)
        with self._locked():
            _, state = self._load()
            return copy.deepcopy(self._current(state, record, context, consent=consent))

    def correction_counts(self, object_id):
        """Return actual repair starts, replayed from application identities."""
        with self._locked():
            _, state = self._load()
            obj = state["runs"][self.run_id]["objects"].get(_identifier(object_id))
            return copy.deepcopy({"total": obj["total"], "perPass": obj["perPass"]}
                                 if obj else {"total": 0, "perPass": {}})

    @contextmanager
    def workspace_transaction(self, *, consent):
        """Hold the lease while verifying records and publishing base-owned state.

        Yields ``verify(record, context)``; it becomes unusable when the context
        exits. ``verify.route_rounds(revision)`` and
        ``verify.route_round_links(revision)`` return recursively immutable
        journal-backed snapshots with the same transaction lifetime.
        The caller may atomically write its own files while locked, but
        must not call consult/consume/other locking controller methods inside
        this context: they take the same non-reentrant OS lock.
        """
        if not callable(consent):
            _fail("consent-unproven")
        active = [True]
        with self._locked():
            entries, state = self._load()

            def verify(record, context):
                if not active[0]:
                    _fail("transaction-closed")
                return copy.deepcopy(self._current(state, record, copy.deepcopy(context), consent=consent))

            def recorded(record, context):
                """Prove record integrity and currency against the journal, NOT live consent.

                For callers that hold no consent probe (native pass resolution). Whether
                consent still matches is re-proven by the consenting terminal verifier.
                """
                if not active[0]:
                    _fail("transaction-closed")
                return copy.deepcopy(self._current(state, record, copy.deepcopy(context),
                                                   consent=consent, check_consent=False))

            def historical_quality(record, context):
                """Replay only quality proof already pinned by completed repair."""
                if not active[0]:
                    _fail("transaction-closed")
                if (type(record) is not dict or record.get("group") != "quality-review"
                        or record.get("status") != "answered"):
                    _fail("historical-quality-proof-required")
                proof = {"recordId": record.get("recordId"), "recordDigest": canonical_digest(record),
                         "contextDigest": canonical_digest(context)}
                if not any(value["group"] == "repair" and value["status"] == "completed"
                           and proof in value["outcome"].get("qualityReviews", {}).values()
                           for value in state["applications"].values()):
                    _fail("historical-quality-proof-required")
                return copy.deepcopy(self._current(state, record, copy.deepcopy(context),
                                                   consent=consent, historical=True))

            def route_rounds(route_revision):
                if not active[0]:
                    _fail("transaction-closed")
                _revision(route_revision)
                rounds = self._rounds(state, self.run_id, route_revision)
                for data in rounds:
                    self._binding(data["proposal"])
                    self._binding(self._initial_route_binding(data["initialInputsDigest"]))
                    for record_id in data["triggerDecisionIds"]:
                        self._current(state, state["records"][record_id], state["contexts"][record_id], consent=consent)
                return _immutable(rounds)

            def route_round_links(route_revision):
                route_rounds(route_revision)
                links = [link for link in state["routeLinks"] if link["runId"] == self.run_id
                         and link["routeRevision"] == route_revision]
                for link in links:
                    record_id = link["recordId"]
                    self._current(state, state["records"][record_id], state["contexts"][record_id], consent=consent)
                return _immutable(links)

            def application(application_id):
                if not active[0]:
                    _fail("transaction-closed")
                value = state["applications"].get(_sha(application_id))
                if value is None:
                    _fail("application-not-authorized")
                record_id = value["decisionRef"]
                self._current(state, state["records"][record_id], state["contexts"][record_id], consent=consent)
                return _immutable(value)

            def correction_counts(object_id):
                if not active[0]:
                    _fail("transaction-closed")
                obj = state["runs"][self.run_id]["objects"].get(_identifier(object_id))
                return _immutable({"total": obj["total"], "perPass": obj["perPass"]}
                                  if obj else {"total": 0, "perPass": {}})

            def decision_block(block, *, publish=False):
                if not active[0]:
                    _fail("transaction-closed")
                if (type(block) is not dict or set(block) != {
                        "kind", "version", "blockId", "step", "context", "subject",
                        "reasonCode", "assessment", "receiptIndex"}
                        or block["kind"] != "img2.workflow-quality-block" or block["version"] != 1
                        or block["step"] != "quality-review"
                        or block["subject"] != block["context"]["subject"]
                        or block["context"]["group"] != "quality-review"
                        or block["reasonCode"] != "deterministic-hard-gate-failed"
                        or type(block["receiptIndex"]) is not int or block["receiptIndex"] < 0
                        or block["blockId"] != canonical_digest({
                            key: value for key, value in block.items() if key != "blockId"})):
                    _fail("workflow-quality-block-invalid")
                self._context(block["context"])
                grant = self._grant(consent)
                if grant["granted"] and ("quality-review" not in grant["purposes"] if self.routing_authority is not None
                                         else set(grant["purposes"]) != set(GROUPS)):
                    _fail("consent-required")
                saved = state["decisionBlocks"].get(block["blockId"])
                if saved is None:
                    if not publish:
                        _fail("workflow-quality-block-missing")
                    saved = {"runId": self.run_id, "block": copy.deepcopy(block), "grant": grant}
                    self._commit(entries, "workflow-quality-blocked", saved)
                    state["decisionBlocks"][block["blockId"]] = saved
                if saved["block"] != block or saved["grant"] != grant:
                    _fail("workflow-quality-block-invalid")
                return _immutable(block)

            verify.route_rounds = route_rounds
            verify.route_round_links = route_round_links
            verify.application = application
            verify.correction_counts = correction_counts
            verify.decision_block = decision_block
            verify.historical_quality = historical_quality
            verify.recorded = recorded

            try:
                yield verify
            finally:
                active[0] = False

    def begin_application(self, record, trial_revision, *, consent=None):
        """Pin a strategy/repair decision to exactly one successor, durably."""
        _revision(trial_revision)
        with self._locked():
            entries, state = self._load()
            record_id = _sha(record.get("recordId")) if type(record) is dict else None
            context = state["contexts"].get(record_id)
            if context is None:
                _fail("record-mismatch")
            self._current(state, record, context, consent=consent)
            if record["group"] not in {"build-strategy", "repair"} or record["status"] != "answered":
                _fail("application-not-authorized")
            if trial_revision == record["subject"]["revision"]:
                _fail("invalid-successor")
            if (type(trial_revision) is not type(record["subject"]["revision"])
                    or (type(trial_revision) is int and trial_revision < record["subject"]["revision"])):
                _fail("invalid-successor")
            for application in state["applications"].values():
                if application["decisionRef"] == record_id:
                    if application["trialRevision"] != trial_revision:
                        _fail("application-conflict")
                    return copy.deepcopy(application)
                if (application["subject"]["runId"] == record["subject"]["runId"]
                        and application["subject"]["objectId"] == record["subject"]["objectId"]
                        and (application["inputRevision"] == record["subject"]["revision"]
                             or application["trialRevision"] == trial_revision)):
                    _fail("application-conflict")
            run = state["runs"][self.run_id]
            obj = run["objects"][record["subject"]["objectId"]]
            if not 0 <= self._now() - obj["startedAt"] < 3600:
                _fail("deadline-exhausted")
            if record["group"] == "repair" and (
                    obj["total"] >= 6 or obj["perPass"].get(record["subject"]["passId"], 0) >= 3):
                _fail("correction-budget-exhausted")
            application = {"applicationId": canonical_digest({"decisionRef": record_id, "trialRevision": trial_revision}),
                           "decisionRef": record_id, "group": record["group"], "subject": copy.deepcopy(record["subject"]),
                           "inputRevision": record["subject"]["revision"], "trialRevision": trial_revision,
                           "inputBindings": copy.deepcopy(record["bindings"]), "status": "pending", "outcome": None}
            self._commit(entries, "application-pending", {"application": application})
            return copy.deepcopy(application)

    def finish_application(self, application_id, outcome, *, consent=None):
        """Persist an external retention assessment without manufacturing metrics.

        ``outcome`` is bounded JSON with status ``completed`` or ``rejected``;
        any measurements/retention reasons remain explicitly caller supplied.
        No incumbent, deliverability or measured-quality field is written here.
        """
        _sha(application_id)
        _json_bytes(outcome)
        if (type(outcome) is not dict or type(outcome.get("status")) is not str
                or outcome["status"] not in {"completed", "rejected"}):
            _fail("invalid-application-outcome")
        _safe_data(outcome)
        with self._locked():
            entries, state = self._load()
            application = state["applications"].get(application_id)
            if application is None or application["subject"]["runId"] != self.run_id:
                _fail("application-missing")
            record = state["records"][application["decisionRef"]]
            self._current(state, record, state["contexts"][application["decisionRef"]], consent=consent)
            if application["status"] != "pending":
                if application["outcome"] != outcome:
                    _fail("application-conflict")
                return copy.deepcopy(application)
            application = copy.deepcopy(application)
            application["status"] = outcome["status"]
            application["outcome"] = copy.deepcopy(outcome)
            self._commit(entries, "application-finished", {"applicationId": application_id, "application": application})
            return application

    def record_probe(self, probe):
        """Record program-observed discovery metadata; never authorize fallback.

        The caller executes the probe and must omit credentials from argv. Only
        a stdout digest is retained. Availability for fallback remains governed
        by the independent program-owned callback in consult().
        """
        if (type(probe) is not dict or set(probe) != {"argv", "exitCode", "stdoutSha256", "at"}
                or type(probe["argv"]) is not list or not 1 <= len(probe["argv"]) <= 64
                or any(type(arg) is not str or not arg or len(arg) > 1024 or "\x00" in arg
                       for arg in probe["argv"])
                or type(probe["exitCode"]) is not int
                or type(probe["at"]) not in (int, float)
                or (type(probe["at"]) is float and not math.isfinite(probe["at"]))
                or probe["at"] < 0):
            _fail("invalid-probe")
        _sha(probe["stdoutSha256"])
        with self._locked():
            entries, _ = self._load()
            return self._commit(entries, "probe", copy.deepcopy(probe))["hash"]

    def release(self):
        """Explicitly relinquish the durable workspace lease (never PID based)."""
        with self._locked():
            entries, _ = self._load()
            self._commit(entries, "lease-released", {})
            self.lease_path.unlink()
            self._sync_directory()
            self._released = True
