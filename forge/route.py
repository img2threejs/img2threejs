#!/usr/bin/env python3
"""Route decisions and authenticated child-state operations, not scene delivery.

Run ``python3 forge/route.py --workspace WORKSPACE --run-id RUN --image ref.png
--objects objects.json``. Image and object paths are workspace-relative. Real Jev
image disclosure and paid use require a separate user-run four-purpose consent
command; this CLI never enrolls, revokes, prompts for keys, or selects a fallback by itself.
A model fallback exists only when the host passes both ``--fallback-command`` (an executable it
approves) and ``--fallback-sha256`` (that file's exact digest); see ``stage0_route/fallback.py``.
The controller still consults it only after a journalled Jev outage, never after a Jev answer.
Only the base-owned reviewed Jev release lock is accepted; there is no Jev lock override.
``--create-children`` requires explicit local harness release/launcher pins. It
creates pending child checklists only after concrete aggregate authentication.
Routing may consult Jev after consent; child initialization does not execute child builders,
complete terminal gates, or produce a deliverable.
``--complete-child OBJECT_ID --decision-ref REF`` revalidates a saved decision and
child with the same explicit harness pins, without a new model consultation.
"""
from __future__ import annotations

import argparse
import ast
import copy
from dataclasses import replace
import hashlib
import json
import io
import os
from pathlib import Path
import re
import posixpath
import selectors
import signal
import shutil
import subprocess
import sys
import time
import tarfile

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "_shared"))

from decision_control import DecisionController, DecisionError, GROUPS, canonical_digest
from jev_cli_bridge import REQUIRED_PROVIDER_FILES, _file, _json
from jev_provider_factory import JevProviderFactory
from stage0_route.discovery import Discovery, JEV_EDGE
from stage0_route.route import Router
from domains import DomainRegistryError, registered_domains
from domains.character import DOMAIN as CHARACTER_DOMAIN
from route_authority import RouteAuthority, RoutePolicies
from route_parent import OwnerProfile, ParentError, RouteParent
from stage0_route.discovery import (EDGES, read_only_probes, character_bpy_probe,
                                    resolve_character_executable)
from stage0_route.fallback import HostFallback, fallback_decisions
from host_policy import pinned_policy_module


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
_SAFE_ERRORS |= {"harness-pin-required", "harness-pin-mismatch", "harness-runtime-unavailable",
    "harness-import-closure-unproven", "unapproved-owner-profile", "owner-output-mismatch",
    "owner-profile-unavailable", "profile-registry-unavailable", "owner-profile-provider-mismatch",
    "route-execution-unresolved", "existing-parent-preserved", "existing-state-conflict",
    "live-provider-unavailable-or-ambiguous", "live-provider-identity-mismatch",
    "live-provider-contract-mismatch", "live-dependency-mismatch", "live-blocking-producer-mismatch",
    "paid-or-licensed-owner-not-authorized", "stale-route-aggregate",
    "invalid-route-authority-input", "route-round-unsealed", "route-round-link-mismatch",
    "authority-command-not-authorized"}
_SAFE_ERRORS |= {"fallback-command-unsafe", "fallback-digest-mismatch", "fallback-invalid"}
_SAFE_ERRORS |= {"invalid-second-proposals", "second-proposal-context-mismatch",
                 "invalid-second-proposal-binding", "stale-second-proposal-source",
                 "second-proposals-empty"}
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
            code, value = self._probe(["consent", "decision-status", "--workspace", str(self.discovery.workspace),
                                       "--run-id", self.run_id])
            if code != 0:
                if auth != "present":
                    _fail(auth)
                reason = value.get("reasonCode")
                _fail(reason if type(reason) is str and reason in _PROBE_FAILURES else "malformed")
            if (set(value) != {"kind", "version", "status", "granted", "purposes", "sha256", "tokenBudget"}
                    or value["status"] != "consent-updated" or type(value["granted"]) is not bool
                    or type(value["purposes"]) is not list
                    or any(type(p) is not str for p in value["purposes"])
                    or (value["granted"] and (len(value["purposes"]) != len(GROUPS) or set(value["purposes"]) != set(GROUPS)))
                    or (not value["granted"] and value["purposes"] != [])
                    or type(value["sha256"]) is not str or not _HEX.fullmatch(value["sha256"])
                    or type(value["tokenBudget"]) is not int or not 0 < value["tokenBudget"] <= 2**53 - 1):
                _fail("malformed")
            if value["granted"] and auth != "present":
                _fail(auth)
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


class HarnessPin:
    """Host-approved exact local package; not third-party release authenticity.

    Pin the complete tracked package and reject untracked import material, links,
    dependencies and dynamic/external imports. Host Git, Node and OS standard
    libraries remain trust dependencies. Startup overrides are never inherited.
    """
    def __init__(self, root, commit, launcher, launcher_sha, consent):
        try:
            self.root, self.launcher = Path(root), Path(launcher)
            if (not self.root.is_absolute() or self.root.resolve(strict=True) != self.root
                    or self.root.name != "harness" or not self.root.is_dir()
                    or not self.launcher.is_absolute() or self.launcher.resolve(strict=True) != self.launcher
                    or self.launcher != self.root / "bin/img2.mjs"):
                _fail("harness-pin-required")
            if (type(commit) is not str or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) is None
                    or type(launcher_sha) is not str or not _HEX.fullmatch(launcher_sha)):
                _fail("harness-pin-required")
            node = shutil.which("node")
            if node is None:
                _fail("harness-runtime-unavailable")
            self.node = Path(node).resolve(strict=True)
        except OSError:
            _fail("harness-runtime-unavailable")
        self.commit, self.launcher_sha, self.consent = commit, launcher_sha, consent
        self.node_sha = hashlib.sha256(_file(self.node.parent, self.node.name,
                                            maximum=256 * 1024 * 1024, capture=True)).hexdigest()
        self.argv = (str(self.node), "--no-addons", str(self.launcher))
        self.runtime_provider = {"dir": str(self.root), "resolvedSha": commit}
        self.inventory = None
        self.verify()

    def _imports(self, files):
        try:
            package = _json(files["package.json"])
            if (type(package) is not dict or package.get("name") != "@img2threejs/img2"
                    or package.get("dependencies") or package.get("optionalDependencies")
                    or package.get("peerDependencies") or package.get("imports")
                    or "img2_core/__init__.py" not in files):
                _fail("harness-import-closure-unproven")
            for relative, raw in files.items():
                if relative.startswith("bin/") and relative.endswith((".js", ".mjs", ".cjs")):
                    text = raw.decode("utf-8")
                    if re.search(r"\b(?:import|require|eval|Function)\s*\(", text):
                        _fail("harness-import-closure-unproven")
                    for declaration in re.findall(r"(?m)^\s*import\b[^\n]*", text):
                        match = re.fullmatch(r"\s*import\s+(?:[^;'\"\n]+\s+from\s+)?['\"]([^'\"\n]+)['\"]\s*;?\s*", declaration)
                        if match is None:
                            _fail("harness-import-closure-unproven")
                        specifier = match[1]
                        if specifier.startswith("node:"):
                            continue
                        target = posixpath.normpath(posixpath.join(posixpath.dirname(relative), specifier))
                        if not specifier.startswith("./") or target not in files or not target.endswith(".mjs"):
                            _fail("harness-import-closure-unproven")
                elif relative.startswith("img2_core/") and relative.endswith(".py"):
                    tree = ast.parse(raw, filename=relative)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            names = [item.name.split(".")[0] for item in node.names]
                        elif isinstance(node, ast.ImportFrom):
                            names = [] if node.level else [(node.module or "").split(".")[0]]
                            if node.level > 1:
                                _fail("harness-import-closure-unproven")
                        elif isinstance(node, ast.Call):
                            target = node.func
                            if ((isinstance(target, ast.Name) and target.id in {"__import__", "eval", "exec", "compile"})
                                    or (isinstance(target, ast.Attribute) and target.attr in {"import_module", "exec_module"})):
                                _fail("harness-import-closure-unproven")
                            continue
                        else:
                            continue
                        if any(name != "img2_core" and name not in sys.stdlib_module_names for name in names):
                            _fail("harness-import-closure-unproven")
        except (KeyError, TypeError, ValueError, UnicodeError, SyntaxError):
            _fail("harness-import-closure-unproven")

    def verify(self):
        if hashlib.sha256(_file(self.consent.git.parent, self.consent.git.name,
                                maximum=256 * 1024 * 1024, capture=True)).hexdigest() != self.consent.git_sha:
            _fail("harness-pin-mismatch")
        if hashlib.sha256(_file(self.node.parent, self.node.name,
                                maximum=256 * 1024 * 1024, capture=True)).hexdigest() != self.node_sha:
            _fail("harness-pin-mismatch")
        code, raw = _run_bytes([str(self.consent.git), "--no-pager", "--no-optional-locks",
                               "-C", str(self.root), "archive", "--format=tar", self.commit],
                              self.root, maximum=64 * 1024 * 1024, env=self.consent.git_env)
        if code:
            _fail("harness-pin-mismatch")
        files = {}
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r|") as archive:
                for member in archive:
                    if member.isdir():
                        continue
                    relative = member.name
                    if (not member.isfile() or len(files) >= 4096 or member.size > 16 * 1024 * 1024
                            or relative.startswith("/") or "\\" in relative
                            or any(part in {"", ".", ".."} for part in relative.split("/"))
                            or relative in files or relative.endswith((".so", ".node", ".pyd"))):
                        _fail("harness-import-closure-unproven")
                    stream = archive.extractfile(member)
                    if stream is None:
                        _fail("harness-import-closure-unproven")
                    files[relative] = stream.read(16 * 1024 * 1024 + 1)
        except (tarfile.TarError, OSError, ValueError):
            _fail("harness-pin-mismatch")
        if "bin/img2.mjs" not in files or hashlib.sha256(files["bin/img2.mjs"]).hexdigest() != self.launcher_sha:
            _fail("harness-pin-mismatch")
        found = set()
        for directory, directories, names in os.walk(self.root, followlinks=False):
            local = Path(directory).relative_to(self.root)
            directories[:] = [name for name in directories if not (local == Path(".") and name == ".git")]
            if any((Path(directory) / name).is_symlink() or name == "node_modules" for name in directories):
                _fail("harness-import-closure-unproven")
            for name in names:
                if local == Path(".") and name == ".git":
                    continue
                path = Path(directory) / name
                relative = path.relative_to(self.root).as_posix()
                if "__pycache__" in path.relative_to(self.root).parts and name.endswith(".pyc"):
                    if path.is_symlink():
                        _fail("harness-import-closure-unproven")
                    continue
                if relative not in files:
                    _fail("harness-import-closure-unproven")
                _file(self.root, relative, maximum=16 * 1024 * 1024,
                      expected=hashlib.sha256(files[relative]).hexdigest())
                found.add(relative)
        if found != files.keys():
            _fail("harness-pin-mismatch")
        self._imports(files)
        inventory = {path: hashlib.sha256(raw).hexdigest() for path, raw in files.items()}
        if self.inventory is not None and self.inventory != inventory:
            _fail("harness-pin-mismatch")
        self.inventory = inventory

    def runner(self, argv, *, cwd=None, env=None, timeout=30, **kwargs):
        self.verify()
        if tuple(argv[:len(self.argv)]) == self.argv:
            command = list(argv[len(self.argv):])
            if command != ["--version", "--json"] and not (
                    len(command) == 6 and command[0:2] == ["capabilities", "--from-kind"]
                    and command[3] == "--to-kind" and command[5] == "--json"):
                _fail("authority-command-not-authorized")
        elif argv[0] != "git":
            allowed = [("python3", "--version"), ("node", "--version"), ("blender", "--version")]
            for probe in ("character-node", "character-chromium"):
                executable = resolve_character_executable(probe)
                if executable is not None:
                    allowed.append((executable, "--version"))
            bpy = character_bpy_probe()
            if bpy is not None:
                allowed.append(bpy)
            if tuple(argv) not in allowed:
                _fail("authority-command-not-authorized")
        environment = dict(os.environ if env is None else env)
        for key in list(environment):
            if key.startswith(("NODE_", "PYTHON")) or key == "TYPESAFE_API_KEY":
                del environment[key]
        if argv[0] == "git":
            argv = [str(self.consent.git), *argv[1:]]
            environment = self.consent.git_env
        try:
            code, raw = _run_bytes(list(argv), Path(cwd or self.root), timeout=timeout,
                                   maximum=8 * 1024 * 1024, env=environment)
            return subprocess.CompletedProcess(argv, code, raw, b"")
        finally:
            self.verify()

    def authority_runner(self, argv, **kwargs):
        """Run only argv constructed by RouteAuthority's reviewed code paths.

        Discovery's runner remains read-only/allowlisted. Terminal gate execution
        is separately constructed after provider/script pins have been verified;
        card/model commands are never forwarded here. Keep harness bytes current
        around those owned subprocesses without pretending a gate is a probe.
        """
        if argv[0] == "git" or tuple(argv[:len(self.argv)]) == self.argv:
            return self.runner(argv, **kwargs)
        self.verify()
        try:
            return subprocess.run(argv, **kwargs)
        finally:
            self.verify()


def _owner_profiles(router, result, discovered):
    decision = result["decision"]
    rows = decision.get("objects")
    if (decision.get("phase") != "decided" or type(rows) is not list or not rows
            or any(row.get("status") not in {"resolved", "dropped", "merged"} for row in rows)):
        _fail("route-execution-unresolved")
    candidates = discovered.get("candidates")
    if (type(candidates) is not list or any(type(c) is not dict for c in candidates)
            or len({c.get("id") for c in candidates}) != len(candidates)):
        _fail("unapproved-owner-profile")
    by_id = {candidate["id"]: candidate for candidate in candidates}
    try:
        domains = registered_domains()
    except DomainRegistryError:
        _fail("profile-registry-unavailable")
    expected_output = decision.get("request", {}).get("expectedOutput")
    owners = {}
    for row in rows:
        if row["status"] in {"dropped", "merged"}:
            continue
        selected = row.get("selected")
        if type(selected) is not list or len(selected) != 1 or selected[0] not in by_id:
            _fail("unapproved-owner-profile")
        candidate = by_id[selected[0]]
        provider, card = candidate.get("provider"), candidate.get("card", {})
        if (candidate.get("providerStatus") != "answered" or candidate.get("blockingGateProduced") is not True
                or any(value is not True for value in candidate.get("dependencies", {}).values())):
            _fail("live-dependency-mismatch")
        if card.get("costClass") not in {"local", "heavy"}:
            _fail("paid-or-licensed-owner-not-authorized")
        kind = card.get("outputKind")
        if provider == "base" and selected == ["base:generic"] and kind == "3d-object" and expected_output == "3d":
            owner = OwnerProfile("generic", "3d-object")
        else:
            profiles = {"character": ("character", "rigged-3d"), "cs2": ("cs2", "3d-object"),
                        "interior": ("interior", "3d-scene"), "pixi2d": ("pixi2d", "2d-scene")}
            if (provider not in profiles or candidate.get("claimants") != [provider]
                    or expected_output != ("2d" if provider == "pixi2d" else "3d")):
                _fail("unapproved-owner-profile")
            profile, output_kind = profiles[provider]
            if kind != ("spec-augmentation" if provider == "cs2" else output_kind):
                _fail("owner-output-mismatch")
            if profile not in domains:
                _fail("owner-profile-unavailable")
            if provider == "character":
                if domains[profile] != CHARACTER_DOMAIN:
                    _fail("owner-profile-provider-mismatch")
            else:
                response = router.discovery.query(EDGES[provider])
                providers = response.get("providers", []) if type(response) is dict else []
                if (type(response) is not dict or response.get("status") != "answered" or len(providers) != 1
                        or providers[0].get("plugin") != provider
                        or providers[0].get("resolvedSha") != candidate.get("resolvedSha")):
                    _fail("live-provider-unavailable-or-ambiguous")
                trusted = providers[0]
                declared = _json(router.discovery._pinned_file(trusted, "domain.json"))
                if type(declared) is not dict or declared.get("id") != profile:
                    _fail("owner-profile-provider-mismatch")
                for key in ("setupSteps", "passSteps", "rigSteps"):
                    if key in declared:
                        declared[key] = tuple((ident, command.replace("{plugin_dir}", trusted["dir"]))
                                              for ident, command in declared[key])
                if declared != domains[profile]:
                    _fail("owner-profile-provider-mismatch")
            owner = OwnerProfile(profile, output_kind)
        owners[selected[0]] = owner
    if not owners:
        _fail("unapproved-owner-profile")
    return owners


def load_route_policies(module_path, sha256, *, workspace, run_id, excluded_roots=()):
    """Load host-approved terminal policies; retain a live source-pin check."""
    with pinned_policy_module(module_path, sha256, workspace=workspace,
                              excluded_roots=(ROOT.parent, *excluded_roots)) as (module, pin):
        factory = getattr(module, "build_route_policies", None)
        if not callable(factory):
            _fail("route-policy-required")
        policies = factory(Path(workspace), run_id)
        if type(policies) is not RoutePolicies:
            _fail("route-policy-required")
        previous = policies.revalidate
        def revalidate():
            pin()
            if previous is not None:
                previous()
            pin()
        return replace(policies, revalidate=revalidate)


def route_policy_roots(discovery, harness):
    roots = [harness.root]
    for edge in EDGES.values():
        response = discovery.query(edge)
        roots.extend(Path(row["dir"]) for row in response.get("providers", [])
                     if type(row.get("dir")) is str)
    if discovery.jev is not None:
        roots.append(Path(discovery.jev["dir"]))
    return roots


def _route_parent(router, result, harness, consent, *, policies=None):
    rows = result.get("decision", {}).get("objects")
    if (result.get("decision", {}).get("phase") != "decided" or type(rows) is not list or not rows
            or any(row.get("status") not in {"resolved", "dropped", "merged"} for row in rows)):
        _fail("route-execution-unresolved")
    consent()
    discovered = router.verification_snapshot()  # Detailed discovery remains outside locks.
    owners = _owner_profiles(router, result, discovered)
    harness.verify()
    policies = RoutePolicies() if policies is None else policies
    if type(policies) is not RoutePolicies:
        _fail("route-policy-required")
    authority = RouteAuthority(router, result["decisionRef"], discovered=discovered, owners=owners,
                               harness=harness.argv, runtime_provider=harness.runtime_provider,
                               runner=harness.authority_runner,
                               pixi_quality_policies=policies.pixi_quality,
                               pixi_capture_policies=policies.pixi_capture,
                               cs2_terminal_policies=policies.cs2_terminal,
                               native_terminal_policies=policies.native_terminal,
                               policy_check=policies.revalidate)
    return RouteParent(router.controller, verify_aggregate=authority, consent=consent)


def _create_children(router, result, harness, consent):
    if (router.workspace / ".img2threejs/state.json").exists() or (router.workspace / ".img2threejs/state.json").is_symlink():
        _fail("existing-parent-preserved")
    parent = _route_parent(router, result, harness, consent)
    with read_only_probes(router.discovery):
        return parent.create()


def _complete_child(router, result, harness, consent, object_id, *, policies=None):
    parent = _route_parent(router, result, harness, consent, policies=policies)
    with read_only_probes(router.discovery):
        return parent.complete_child(object_id)


def _load_decision(workspace, reference, run_id):
    match = re.fullmatch(r"\.img2threejs/route/decision-([0-9a-f]{64})\.json", reference)
    if match is None:
        _fail("invalid-routing-result")
    raw = _file(workspace, reference, maximum=1024 * 1024, capture=True)
    if hashlib.sha256(raw).hexdigest() != match.group(1):
        _fail("invalid-routing-result")
    decision = _json(raw)
    if (type(decision) is not dict or decision.get("kind") != "img2.route-decision"
            or decision.get("version") != 1 or decision.get("runId") != run_id):
        _fail("invalid-routing-result")
    return {"decision": decision, "decisionRef": reference, "sha256": match.group(1)}


def _recorded_second_proposals(workspace, relative, approved_sha, *, image, objects):
    """Bind host-supplied additions; never attest a model or launch one implicitly."""
    raw = _file(workspace, relative, maximum=256 * 1024, expected=approved_sha, capture=True)
    value = _json(raw)
    if (type(value) is not dict or set(value) != {
            "kind", "version", "imageSha256", "firstRoundProposalSha256", "objects"}
            or value["kind"] != "img2.recorded-second-proposals"
            or type(value["version"]) is not int or value["version"] != 1
            or any(type(value[key]) is not str or _HEX.fullmatch(value[key]) is None
                   for key in ("imageSha256", "firstRoundProposalSha256"))
            or type(value["objects"]) is not list):
        _fail("invalid-second-proposals")
    if value["firstRoundProposalSha256"] != canonical_digest(objects):
        _fail("second-proposal-context-mismatch")
    _file(workspace, image, maximum=64 * 1024 * 1024, expected=value["imageSha256"])
    proposal = {"kind": "img2.object-proposals", "version": 1, "objects": value["objects"]}
    Router._objects(proposal, allow_empty=True)
    initial_ids = {obj["objectId"] for obj in objects["objects"]}
    if any(obj["objectId"] in initial_ids for obj in value["objects"]):
        _fail("invalid-second-proposals")
    source = {"role": "second-proposal-source", "path": relative, "sha256": approved_sha}

    def supply(request):
        _file(workspace, relative, maximum=256 * 1024, expected=approved_sha)
        if (type(request) is not dict or request.get("kind") != "img2.decomposition-proposal-request"
                or type(request.get("version")) is not int or request["version"] != 1
                or request.get("round") != 2
                or request.get("imageSha256") != value["imageSha256"]
                or request.get("firstRoundSha256") != value["firstRoundProposalSha256"]):
            _fail("second-proposal-context-mismatch")
        return copy.deepcopy(proposal)

    return supply, source


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--image", help="Workspace-relative PNG or baseline JPEG")
    parser.add_argument("--objects", help="Workspace-relative img2.object-proposals v1 JSON (required for 3D)")
    parser.add_argument("--second-objects",
                        help="Workspace-relative hash-approved host additions for a requested second 3D proposal round")
    parser.add_argument("--second-objects-sha256",
                        help="Exact SHA256 of --second-objects; data approval, not model or execution authority")
    parser.add_argument("--expected-output", choices=("2d", "3d"), default="3d")
    parser.add_argument("--intent", action="append", default=[])
    parser.add_argument("--explicit-route")
    parser.add_argument("--create-children", action="store_true",
                        help="Initialize pending child states after concrete route verification; not model execution or delivery")
    parser.add_argument("--complete-child", metavar="OBJECT_ID",
                        help="Revalidate and complete one existing child without rerouting")
    parser.add_argument("--decision-ref", help="Content-addressed route decision for --complete-child")
    parser.add_argument("--harness-root", type=Path, help="Host-approved canonical harness checkout (optional complete bundle for routing; required for child operations)")
    parser.add_argument("--harness-sha", help="Host-approved exact harness Git commit")
    parser.add_argument("--harness-executable", type=Path, help="Canonical harness bin/img2.mjs launcher")
    parser.add_argument("--harness-sha256", help="Host-approved launcher SHA256; never calculated from workspace input")
    parser.add_argument("--policy-module", type=Path, help="Explicit host-approved external terminal policy module")
    parser.add_argument("--policy-sha256", help="Exact SHA256 of --policy-module; not workspace authority")
    parser.add_argument("--fallback-command", type=Path, metavar="PATH",
                        help="Host-approved external model-fallback program; requires --fallback-sha256")
    parser.add_argument("--fallback-sha256", metavar="HEX",
                        help="Exact SHA256 of the --fallback-command file; re-verified before every invocation")
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
    if (args.fallback_command is None) != (args.fallback_sha256 is None):
        parser.error("--fallback-command and --fallback-sha256 must be supplied together")
    if (args.second_objects is None) != (args.second_objects_sha256 is None):
        parser.error("--second-objects and --second-objects-sha256 must be supplied together")
    if args.second_objects is not None:
        if args.complete_child or args.expected_output != "3d":
            parser.error("second-proposal data applies only to 3D routing")
        if _HEX.fullmatch(args.second_objects_sha256) is None:
            parser.error("--second-objects-sha256 must be 64 lowercase hex characters")
    if (args.policy_module is None) != (args.policy_sha256 is None):
        parser.error("--policy-module and --policy-sha256 must be supplied together")
    if args.policy_module is not None and not args.complete_child:
        parser.error("terminal policy flags require --complete-child")
    if args.policy_sha256 is not None and _HEX.fullmatch(args.policy_sha256) is None:
        parser.error("--policy-sha256 must be 64 lowercase hex characters")
    if args.fallback_command is not None:
        if args.complete_child:
            parser.error("fallback approval flags apply to routing, not --complete-child")
        if _HEX.fullmatch(args.fallback_sha256) is None:
            parser.error("--fallback-sha256 must be 64 lowercase hex characters")
    if args.complete_child:
        if args.create_children or not args.decision_ref or args.image or args.objects or args.intent or args.explicit_route:
            parser.error("--complete-child requires --decision-ref and excludes routing and creation options")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", args.complete_child) is None:
            parser.error("--complete-child must be a valid object ID")
    else:
        if args.decision_ref:
            parser.error("--decision-ref requires --complete-child")
        if args.image is None:
            parser.error("--image is required for routing")
        if args.expected_output == "3d" and args.objects is None:
            parser.error("--objects is required for --expected-output 3d")
    pins = (args.harness_root, args.harness_sha, args.harness_executable, args.harness_sha256)
    if (args.create_children or args.complete_child) and any(value is None for value in pins):
        parser.error("child operations require --harness-root, --harness-sha, --harness-executable and --harness-sha256")
    if any(value is not None for value in pins) and any(value is None for value in pins):
        parser.error("harness approval requires all four --harness-* pins")
    consent, result, harness = None, None, None
    try:
        workspace = args.workspace.resolve(strict=True)
        second_proposals, second_proposal_binding = None, None
        if args.create_children and ((workspace / ".img2threejs/state.json").exists()
                                    or (workspace / ".img2threejs/state.json").is_symlink()):
            _fail("existing-parent-preserved")
        if args.complete_child:
            result = _load_decision(workspace, args.decision_ref, args.run_id)
        else:
            _file(workspace, args.image, maximum=64 * 1024 * 1024)
            objects = _json(_file(workspace, args.objects, maximum=256 * 1024, capture=True)) if args.objects else None
            if args.second_objects is not None:
                second_proposals, second_proposal_binding = _recorded_second_proposals(
                    workspace, args.second_objects, args.second_objects_sha256,
                    image=args.image, objects=objects)
        release = _approved_lock()
        fallback = None
        if args.fallback_command is not None:
            # Refuse an unapproved or changed program before any journal or lease is touched.
            fallback = HostFallback(args.fallback_command, args.fallback_sha256)
        controller = DecisionController(workspace, args.run_id)
        discovery = PinnedDiscovery(controller)
        consent = LiveConsent(discovery, release, args.run_id, Path(sys.executable))
        discovery.consent_probe = consent
        if all(value is not None for value in pins):
            harness = HarnessPin(*pins, consent)
            discovery.harness = harness.argv
            discovery.runner = harness.runner
        factory = None if args.complete_child else JevProviderFactory(
            discovery, approved_release=release, run_id=args.run_id,
            consent=consent, python_executable=consent.executable)
        router = Router(workspace, args.run_id, controller=controller, discovery=discovery,
                        consent=consent, provider_factory=factory, fallback=fallback,
                        second_proposals=second_proposals, second_proposal_binding=second_proposal_binding)
        if args.complete_child:
            discovery.enumerate()
            policies = None if args.policy_module is None else load_route_policies(
                args.policy_module, args.policy_sha256, workspace=workspace, run_id=args.run_id,
                excluded_roots=route_policy_roots(discovery, harness))
            parent = _complete_child(router, result, harness, consent, args.complete_child, policies=policies)
            value = _output(result=result)
            value.update(mode="child-completed", status="child-completed",
                         childObjectId=args.complete_child, parentStatePath=".img2threejs/state.json",
                         parentStatus=parent["status"])
        else:
            result = router.route(image=args.image, objects=objects, expected_output=args.expected_output,
                                  intent=args.intent, explicit_route=args.explicit_route)
            value = _output(reason=consent.last_failure, result=result)
            decisions = fallback_decisions(workspace, result.get("decision"), attempted=fallback is not None)
            if decisions:
                value["fallbackDecisions"] = decisions
        if args.create_children and value["status"] == "decided":
            parent = _create_children(router, result, harness, consent)
            value.update(status="children-initialized", childrenCreated=True,
                         parentStatePath=".img2threejs/state.json", childCount=len(parent["objects"]),
                         childStatePaths=[child["statePath"] for child in parent["objects"]])
    except Exception as error:
        reported = getattr(error, "reason_code", None)
        trusted_parent_error = (isinstance(error, ParentError) and type(reported) is str
                                and re.fullmatch(r"[a-z][a-z0-9-]{0,95}", reported))
        reason = reported if trusted_parent_error or (type(reported) is str and reported in _SAFE_ERRORS) else "invalid-routing-input"
        if reason == "consent-probe-error" and consent is not None and consent.last_failure:
            reason = consent.last_failure
        value = _output(reason=reason)
    if args.create_children or args.complete_child:
        value.update(terminalStatus="incomplete", terminalAcceptance=False, deliverable=False)
        if args.create_children:
            value.setdefault("childrenCreated", False)
            if value["childrenCreated"]:
                value["mode"] = "children-initialized"
    print(json.dumps(value, sort_keys=True))
    return 0 if value["status"] in {"decided", "children-initialized", "child-completed"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
