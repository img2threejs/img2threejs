"""Bounded Jev-only trials, with program-owned execution and evidence admission.

Integration API: BestOfN(controller, programs, comparison).run(record, context,
consent=..., n=2, approve_cost=...). ``programs`` is a trusted host registry, NOT
provider/model input: each opaque eligible handle maps to CandidateProgram.
Its argv_factory(trial) returns an absolute executable argv (never shell text).
Its quality_producer(trial) independently admits real captures/measurements and
returns ReportEvidence, not scores. These callbacks must do bounded local work;
all capture, review and other expensive work belongs in the timed subprocess.
The producer must verify visual readback/provenance independently: hashes alone
are not visual proof. No plugins are imported and no dependencies are installed.

comparison is the trusted frozen comparison contract: outputKind, flatness,
rubricVersion, referenceSha256, cameraSha256, viewSetSha256, contractSha256,
features (id/tier/threshold), and blockingGates (id/producer). viewSetSha256 binds
the shared comparison/view protocol, not a candidate-specific render inventory.
The producer's report envelope is {subject, decisionRef, candidateId,
rubricVersion, inputs:{role:sha256}, candidate:<decision_quality report>}.
ReportEvidence binds this file and every input, including reference, camera,
view-set, quality-contract, source, render and gate-report. Source/render/gates
and the report must be inside the new trial directory. Only measured reports
with the complete frozen gates/features can rank; nothing synthesizes a score.

Completed trials are revalidated and reused without running or charging again.
Only unstarted candidates continue, under the original controller deadline.
An interrupted pending trial requests reconciliation, never an automatic rerun.
An exclusive invocation lock and atomic, hash-chained pending/completed events
bind recovery to the decision, context, consent, subject and comparison contract.
Completed directories and content-addressed result snapshots are immutable.
This is an in-process trusted host API, not a sandbox for arbitrary executables:
commands must not daemonize or escape their POSIX process group. Unsupported
platforms fail closed before creating a trial.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import signal
import stat
import subprocess
import time
from typing import Callable, Mapping

if os.name == "posix":
    import fcntl

from forge._shared.decision_control import DecisionError, canonical_digest
from forge._shared.decision_quality import rank_candidates
from forge._shared.child_environment import child_environment


def _require(condition, reason):
    if not condition:
        raise DecisionError(reason)


@dataclass(frozen=True)
class CandidateTrial:
    candidate_id: str
    directory: Path
    subject: dict
    decision_ref: str
    remaining_seconds: float


@dataclass(frozen=True)
class ReportEvidence:
    report: dict
    bindings: tuple


@dataclass(frozen=True)
class CandidateProgram:
    cost_class: str
    argv_factory: Callable
    quality_producer: Callable


def _read(workspace, binding, *, document=False, deadline):
    _require(type(binding) is dict and set(binding) == {"role", "path", "sha256"}, "invalid-binding")
    relative = binding["path"]
    _require(type(relative) is str and relative and "\\" not in relative
             and not PurePosixPath(relative).is_absolute()
             and all(part not in ("", ".", "..") for part in relative.split("/")), "unsafe-path")
    directory = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    digest, chunks, size = hashlib.sha256(), [], 0
    try:
        parts = relative.split("/")
        for component in parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            _require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "unsafe-path")
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                _require(time.monotonic() < deadline, "deadline-exhausted")
                digest.update(chunk)
                if document:
                    size += len(chunk)
                    _require(size <= 1024 * 1024, "evidence-too-large")
                    chunks.append(chunk)
    finally:
        os.close(directory)
    _require(digest.hexdigest() == binding["sha256"], "stale-evidence")
    if document:
        value = json.loads(b"".join(chunks))
        canonical_digest(value)
        return value


def _mkdir(workspace, relative, *, exclusive=False):
    directory = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = relative.parts
        for index, component in enumerate(parts):
            try:
                os.mkdir(component, mode=0o700, dir_fd=directory)
                os.fsync(directory)
            except FileExistsError:
                _require(not (exclusive and index == len(parts) - 1), "best-of-n-already-attempted")
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
    finally:
        os.close(directory)
    return workspace / relative


def _publish(path, value):
    """Atomically publish an immutable document without replacing prior bytes."""
    temporary = path.with_name("." + path.name + "-" + os.urandom(12).hex())
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink()


def _document(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        _require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "unsafe-path")
        data = stream.read(1024 * 1024 + 1)
    _require(len(data) <= 1024 * 1024, "evidence-too-large")
    value = json.loads(data)
    canonical_digest(value)
    return value


@contextmanager
def _invocation_lock(path):
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        _require(stat.S_ISREG(os.fstat(descriptor).st_mode), "unsafe-path")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DecisionError("best-of-n-busy") from None
        yield
    finally:
        os.close(descriptor)


def _events(ledger, authority):
    events, previous = [], None
    for path in sorted(ledger.glob("event-*.json")):
        event = _document(path)
        digest = canonical_digest(event)
        _require(path.name == "event-%04d-%s.json" % (len(events), digest)
                 and event.get("sequence") == len(events)
                 and event.get("previous") == previous
                 and event.get("authority") == authority
                 and event.get("version") == 1, "recovery-journal-mismatch")
        events.append(event)
        previous = digest
    return events


def _append(ledger, events, authority, event, payload):
    entry = {"version": 1, "sequence": len(events), "previous": canonical_digest(events[-1]) if events else None,
             "authority": authority, "event": event, "payload": payload}
    _publish(ledger / ("event-%04d-%s.json" % (len(events), canonical_digest(entry))), entry)
    events.append(entry)


def _snapshot(workspace, directory, role, value):
    path = directory / (role + "-" + canonical_digest(value) + ".json")
    try:
        _publish(path, value)
    except FileExistsError:
        _require(_document(path) == value, "recovery-evidence-mismatch")
    data = (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    return {"role": role, "path": str(path.relative_to(workspace)), "sha256": hashlib.sha256(data).hexdigest()}


def _freeze(directory):
    for root, dirs, files in os.walk(directory, topdown=False, followlinks=False):
        for name in files:
            path = Path(root) / name
            _require(stat.S_ISREG(path.lstat().st_mode), "unsafe-output")
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                _require(stat.S_ISREG(os.fstat(descriptor).st_mode), "unsafe-output")
                os.fchmod(descriptor, 0o400)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        for name in dirs:
            path = Path(root) / name
            _require(stat.S_ISDIR(path.lstat().st_mode), "unsafe-output")
            path.chmod(0o500)
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fchmod(descriptor, 0o500)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class BestOfN:
    def __init__(self, controller, programs: Mapping[str, CandidateProgram], comparison: dict):
        self.controller = controller
        self.programs = dict(programs)
        self.comparison = deepcopy(comparison)
        canonical_digest(self.comparison)
        _require(set(self.comparison) == {"outputKind", "flatness", "rubricVersion", "referenceSha256",
                 "cameraSha256", "viewSetSha256", "contractSha256", "features", "blockingGates"},
                 "invalid-comparison-contract")
        _require(type(self.comparison["features"]) is list and type(self.comparison["blockingGates"]) is list
                 and self.comparison["blockingGates"], "invalid-comparison-contract")
        for program in self.programs.values():
            _require(isinstance(program, CandidateProgram) and program.cost_class in {"local", "heavy", "licensed", "paid"}
                     and callable(program.argv_factory) and callable(program.quality_producer), "untrusted-program")

    def _remaining(self, object_id, local_deadline):
        seconds = self.controller.remaining_object_seconds(object_id)
        _require(type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0,
                 "invalid-deadline")
        return min(seconds, max(0.0, local_deadline - time.monotonic()))

    def _report(self, trial, produced, elapsed_ms, deadline):
        _require(isinstance(produced, ReportEvidence), "evidence-missing")
        bindings = produced.bindings
        _require(type(bindings) is tuple and all(type(item) is dict for item in bindings), "invalid-binding")
        hashes = {}
        for binding in bindings:
            role = binding.get("role")
            _require(type(role) is str and role not in hashes, "invalid-binding")
            _read(self.controller.workspace, binding, deadline=deadline)
            hashes[role] = binding["sha256"]
            if role in {"source", "render", "gate-report"}:
                _require(trial.directory in (self.controller.workspace / binding["path"]).parents,
                         "foreign-candidate-evidence")
        _require({"reference", "camera", "view-set", "quality-contract", "source", "render", "gate-report"}
                 <= hashes.keys(), "evidence-missing")
        _require(trial.directory in (self.controller.workspace / produced.report["path"]).parents,
                 "foreign-candidate-evidence")
        envelope = _read(self.controller.workspace, produced.report, document=True, deadline=deadline)
        _require(type(envelope) is dict and envelope.get("subject") == trial.subject
                 and envelope.get("decisionRef") == trial.decision_ref
                 and envelope.get("candidateId") == trial.candidate_id, "report-subject-mismatch")
        _require(envelope.get("inputs") == hashes, "evidence-mismatch")
        contract = self.comparison
        _require(envelope.get("rubricVersion") == contract["rubricVersion"], "rubric-mismatch")
        for role, field in (("reference", "referenceSha256"), ("camera", "cameraSha256"),
                            ("view-set", "viewSetSha256"), ("quality-contract", "contractSha256")):
            _require(hashes[role] == contract[field], "comparison-mismatch")
        report = deepcopy(envelope.get("candidate"))
        _require(type(report) is dict and report.get("id") == trial.candidate_id, "report-subject-mismatch")
        _require(all(report.get(field) == contract[field] for field in ("outputKind", "flatness")),
                 "comparison-mismatch")
        _require(report.get("evidence") == {field: contract[field] for field in
                 ("referenceSha256", "cameraSha256", "viewSetSha256")}, "comparison-mismatch")
        features = report.get("features")
        _require(type(features) is list and all(type(item) is dict for item in features), "evidence-missing")
        _require(sorted((item.get("id"), item.get("tier"), item.get("threshold")) for item in features)
                 == sorted((item["id"], item["tier"], item["threshold"]) for item in contract["features"]),
                 "rubric-mismatch")
        gates = report.get("gateResults")
        _require(type(gates) is list and all(type(item) is dict for item in gates), "evidence-missing")
        _require(sorted((item.get("id"), item.get("producer")) for item in gates)
                 == sorted((item["id"], item["producer"]) for item in contract["blockingGates"]), "gate-mismatch")
        report["timeMs"] = elapsed_ms
        return report

    def run(self, record, context, *, consent, n=2, approve_cost=None):
        _require(os.name == "posix" and hasattr(os, "killpg"), "process-cancellation-unavailable")
        _require(type(n) is int and 2 <= n <= 3, "invalid-best-of-n")
        context = deepcopy(context)
        decision = self.controller.consume(record, context, consent=consent)
        _require(decision["group"] in {"routing", "build-strategy"}
                 and str(decision.get("decider", "")).startswith("jev:"), "best-of-n-not-authorized")
        answers = decision.get("answers", {})
        selection = answers.get("selection", {})
        probabilities = selection.get("probabilities", {})
        eligible = [item["id"] for item in decision["candidates"]]
        _require(len(eligible) == len(set(eligible)) and set(probabilities) == set(eligible), "invalid-candidates")
        ranked = sorted(eligible, key=lambda handle: (-probabilities[handle], handle))
        _require(len(ranked) >= n and answers.get("anyFits", {}).get("noul", 0) >= 0.5,
                 "best-of-n-not-authorized")
        _require(decision["status"] == "uncertain" or (decision["status"] == "answered"
                 and probabilities[ranked[0]] - probabilities[ranked[1]] < 0.25), "best-of-n-not-authorized")
        selected = ranked[:n]
        _require(all(handle in self.programs for handle in selected), "candidate-program-unavailable")
        subject = decision["subject"]
        object_id = subject["objectId"]
        initial = self.controller.remaining_object_seconds(object_id)
        _require(type(initial) in (int, float) and math.isfinite(initial) and initial >= 0, "invalid-deadline")
        deadline = time.monotonic() + initial
        # Hash opaque identifiers before using them as paths; no caller-supplied path.
        object_part = object_id
        revision_part = str(subject["revision"])
        _require(all(part and part not in {".", ".."} and "/" not in part and "\\" not in part
                     for part in (object_part, revision_part)), "unsafe-path")
        root = _mkdir(self.controller.workspace, Path(".img2threejs") / "objects" / object_part
                      / ("rev-" + revision_part) / "candidates")
        with _invocation_lock(root / ("best-of-n-" + decision["recordId"] + ".lock")):
            return self._resume(root, decision, context, selected, deadline, consent, approve_cost)

    def _resume(self, root, decision, context, selected, deadline, consent, approve_cost):
        subject = decision["subject"]
        object_id = subject["objectId"]
        authority = {"decisionRef": decision["recordId"], "contextDigest": canonical_digest(context),
                     "consentDigest": canonical_digest(decision["consent"]), "subject": deepcopy(subject),
                     "comparisonDigest": canonical_digest(self.comparison), "candidates": selected,
                     "costClasses": {handle: self.programs[handle].cost_class for handle in selected}}
        marker = root / ("best-of-n-" + decision["recordId"] + ".json")
        try:
            _publish(marker, authority)
        except FileExistsError:
            _require(_document(marker) == authority, "recovery-authority-mismatch")
        ledger = _mkdir(self.controller.workspace, (root / ("best-of-n-" + decision["recordId"]))
                        .relative_to(self.controller.workspace))
        events = _events(ledger, authority)
        pending, completed = {}, {}
        for event in events:
            payload = event["payload"]
            if event["event"] == "result":
                _read(self.controller.workspace, payload["binding"], deadline=float("inf"))
                continue
            _require(event["event"] in {"pending", "completed"} and type(payload) is dict
                     and payload.get("candidateId") in selected, "recovery-journal-mismatch")
            handle = payload["candidateId"]
            if event["event"] == "pending":
                _require(handle not in pending and handle not in completed, "recovery-journal-mismatch")
                pending[handle] = payload
            else:
                _require(handle in pending and handle not in completed
                         and payload["directory"] == pending[handle]["directory"], "recovery-journal-mismatch")
                completed[handle] = payload
        started = time.monotonic()
        attempts, reports = [], []
        reason = "no-passing-candidate"
        for handle in selected:
            self.controller.consume(decision, context, consent=consent)
            remaining = self._remaining(object_id, deadline)
            directory = root / hashlib.sha256(handle.encode()).hexdigest()
            relative = str(directory.relative_to(self.controller.workspace))
            trial = CandidateTrial(handle, directory, deepcopy(subject), decision["recordId"], remaining)
            if handle in pending and handle not in completed:
                attempts.append({"candidateId": handle, "directory": relative, "status": "request-input",
                                 "reasonCode": "ambiguous-in-flight", "elapsedMs": None,
                                 "inputTokens": None, "outputTokens": None, "cachedInputTokens": None})
                reason = "ambiguous-in-flight"
                break
            if handle in completed:
                completion = completed[handle]
                try:
                    _require(completion["directory"] == relative, "recovery-journal-mismatch")
                    attempt = _read(self.controller.workspace, completion["attempt"], document=True,
                                    deadline=float("inf"))
                    _require(attempt["candidateId"] == handle and attempt["directory"] == relative
                             and attempt.get("costApproval") == pending[handle]["costApproval"],
                             "recovery-evidence-mismatch")
                    if attempt["status"] == "evaluated":
                        evidence = completion["evidence"]
                        produced = ReportEvidence(evidence["report"], tuple(evidence["bindings"]))
                        report = self._report(trial, produced, attempt["reportElapsedMs"], float("inf"))
                        reports.append(report)
                    else:
                        _require(completion["evidence"] is None, "recovery-evidence-mismatch")
                    attempts.append(attempt)
                except (DecisionError, OSError, ValueError, KeyError, TypeError) as exc:
                    failure = exc.reason_code if isinstance(exc, DecisionError) else "recovery-evidence-mismatch"
                    attempts.append({"candidateId": handle, "directory": relative, "status": "unevaluated",
                                     "reasonCode": failure, "elapsedMs": None,
                                     "inputTokens": None, "outputTokens": None, "cachedInputTokens": None})
                    reason = "recovery-evidence-invalid"
                    break
                continue
            if remaining <= 0:
                reason = "deadline-exhausted"
                break
            program = self.programs[handle]
            approval = {"decisionRef": decision["recordId"], "subject": deepcopy(subject),
                        "candidateId": handle, "costClass": program.cost_class}
            approval_started = time.monotonic()
            if program.cost_class in {"licensed", "paid"}:
                if not callable(approve_cost) or approve_cost(deepcopy(approval)) is not True:
                    attempts.append({"candidateId": handle, "status": "request-input", "reasonCode": "cost-approval-required",
                                     "costApproval": dict(approval, approved=False),
                                     "elapsedMs": (time.monotonic() - approval_started) * 1000,
                                     "inputTokens": None, "outputTokens": None, "cachedInputTokens": None})
                    reason = "cost-approval-required"
                    break
            cost_approval = dict(approval, approved=True) if program.cost_class in {"licensed", "paid"} else None
            _require(not os.path.lexists(directory), "best-of-n-already-attempted")
            reservation = {"candidateId": handle, "directory": relative, "costApproval": cost_approval}
            _append(ledger, events, authority, "pending", reservation)
            _mkdir(self.controller.workspace, directory.relative_to(self.controller.workspace), exclusive=True)
            began = time.monotonic()
            attempt = {"candidateId": handle, "directory": relative, "inputTokens": None, "outputTokens": None,
                       "cachedInputTokens": None, "costApproval": cost_approval}
            stop, produced, report = False, None, None
            try:
                argv = program.argv_factory(trial)
                _require(type(argv) in (list, tuple) and argv and all(type(arg) is str and "\0" not in arg for arg in argv)
                         and Path(argv[0]).is_absolute(), "untrusted-argv")
                self.controller.consume(decision, context, consent=consent)
                remaining = self._remaining(object_id, deadline)
                if remaining <= 0:
                    raise DecisionError("deadline-exhausted")
                with (directory / "stdout.log").open("xb") as stdout, (directory / "stderr.log").open("xb") as stderr:
                    process = subprocess.Popen(argv, cwd=directory, stdin=subprocess.DEVNULL, stdout=stdout,
                                               stderr=stderr, shell=False, start_new_session=True, close_fds=True,
                                               env=child_environment())
                    try:
                        code = process.wait(timeout=remaining)
                    except subprocess.TimeoutExpired:
                        raise DecisionError("deadline-exhausted") from None
                    finally:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
                attempt["exitCode"] = code
                _require(code == 0, "candidate-command-failed")
                _require(self._remaining(object_id, deadline) > 0, "deadline-exhausted")
                self.controller.consume(decision, context, consent=consent)
                produced = program.quality_producer(trial)
                report = self._report(trial, produced, (time.monotonic() - began) * 1000, deadline)
                _require(self._remaining(object_id, deadline) > 0, "deadline-exhausted")
                attempt.update(status="evaluated", reportSha256=produced.report["sha256"],
                               reportElapsedMs=report["timeMs"])
            except (DecisionError, OSError, ValueError, KeyError, TypeError) as exc:
                failure = exc.reason_code if isinstance(exc, DecisionError) else "evidence-or-execution-error"
                attempt.update(status="unevaluated", reasonCode=failure)
                if failure == "deadline-exhausted":
                    reason, stop = failure, True
            # BaseException/crash deliberately leaves pending, never a false completion.
            attempt["elapsedMs"] = (time.monotonic() - began) * 1000
            attempt_binding = _snapshot(self.controller.workspace, directory, "attempt", attempt)
            try:
                _freeze(directory)
            except (DecisionError, OSError) as exc:
                failure = exc.reason_code if isinstance(exc, DecisionError) else "unsafe-output"
                attempt.update(status="unevaluated", reasonCode=failure)
                # Rejected output cannot prevent a durable terminal receipt in the trusted ledger.
                attempt_binding = _snapshot(self.controller.workspace, ledger, "attempt", attempt)
            evidence = {"report": produced.report, "bindings": list(produced.bindings)} if attempt["status"] == "evaluated" else None
            _append(ledger, events, authority, "completed",
                    {"candidateId": handle, "directory": relative, "attempt": attempt_binding, "evidence": evidence})
            if attempt["status"] == "evaluated":
                reports.append(report)
            attempts.append(attempt)
            if stop:
                break
        self.controller.consume(decision, context, consent=consent)
        assessments = rank_candidates(reports)
        # Missing admission must not disappear to leave a spuriously comparable winner.
        incomplete = any(item["status"] == "unevaluated" and item.get("reasonCode") != "candidate-command-failed"
                         and item.get("reasonCode") != "deadline-exhausted" for item in attempts)
        winner = next((item["id"] for item in assessments if item["passed"] and item["rank"] == 1), None)
        if incomplete or reason in {"cost-approval-required", "ambiguous-in-flight", "recovery-evidence-invalid"}:
            winner = None
        if incomplete and reason not in {"deadline-exhausted", "recovery-evidence-invalid"}:
            reason = "evidence-unevaluated"
        result = {"action": "continue" if winner else "request-input", "selected": winner,
                  "reasonCode": "best-passing-candidate" if winner else reason,
                  "decisionRef": decision["recordId"], "attempts": attempts, "assessments": assessments,
                  "elapsedMs": (time.monotonic() - started) * 1000,
                  "inputTokens": None, "outputTokens": None, "cachedInputTokens": None}
        result_binding = _snapshot(self.controller.workspace, ledger, "result", result)
        _append(ledger, events, authority, "result", {"binding": result_binding})
        if any(item.get("reasonCode") == "unsafe-output" for item in attempts):
            raise DecisionError("unsafe-output")
        return result
