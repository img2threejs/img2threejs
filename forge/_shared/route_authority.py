"""Concrete RouteParent authority: real router seals, live pins, real terminal gates.

Prepare router.verification_snapshot() with real Discovery OUTSIDE the controller
transaction. Construct RouteAuthority with that trusted snapshot, the program's
live approved OwnerProfile mapping, and explicit trusted harness argv. Pass this
object as RouteParent(..., verify_aggregate=authority, consent=live_consent).
Calling it/revalidate/terminal methods requires RouteParent's already-held
workspace_transaction verifier. No Discovery methods or locking controller calls
are used here; direct read-only capability/git/version probes do not journal.
The approved owner mapping is retained live to detect revocation; all other
inputs are detached. Candidate/provider/model JSON cannot grant an owner profile.

Only Pixi's reviewed deterministic gate + artifact-binding contract is enabled
by default. Every declared blocking gate must have an exact reviewed command,
run successfully, and return the exact current artifact binding. Each child-local
source, asset, snapshot binding, produced evidence and review is independently
rehashed before AND after execution. This proves integrity, NOT review authority:
Pixi's editable review.json does not authenticate a Jev or human visual review.
The dedicated 2D projection/consumer binds the exact child subject, artifact,
snapshot, reference, render, capture and critical crops to an answered controller
quality-review record. Independently approved requirements cannot be shrunk by
workspace JSON. This semantic evidence still does NOT authenticate native capture:
terminal promotion remains blocked until a reviewed real-host replay/attestation
producer proves the pinned helper ran with these source bytes loaded.

Pixi's import bootstrap must also be pinned: runtime_provider is a trusted
{dir, resolvedSha} for the harness checkout named 'harness'. Its Python package
and the plugin's Python sources are checked against that commit before/after
execution. IMG2_HOME is explicit; site startup and Python environment overrides
are disabled, and an isolated disposable bytecode cache prevents stale pyc use.

GateContract/GateSpec describe other program-reviewed 2D runners implementing
this same strict artifact-binding/evidence contract, never pass callbacks.
D18 is a different schema: no Pixi kind/version/bindings fields are imposed on
3D. Native producer/reference authentication requires a reviewed code amendment,
not registration of another GateContract. Base has no complete measured terminal
producer contract; interior's
workflow is explicitly attestation-only; CS2 accepts unbound metrics; character's
legacy rig gate is nonblocking. These defaults fail with named reasons. None of
this establishes missing visual provenance or grants paid/licensed consent; such
routes remain blocked by the router and this boundary.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

from route_parent import (AggregateAuthority, OwnerProfile, ParentError, VerifiedAggregate,
                          _decode, _encode, _read)
from workflow_state import new_state
from stage0_route.images import decode
from stage0_route.discovery import EDGES, read_only_probes
from stage0_route.route import Router

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PIN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_DEPENDENCY = re.compile(r"(blender|node|python3)>=(\d+(?:\.\d+)*)\Z")
_ARTIFACT = ".img2/artifacts/pixi2d/artifact.json"
_UNSUPPORTED = {
    "base": "base-terminal-measurement-contract-unavailable",
    "interior": "interior-terminal-quality-producer-unavailable",
    "cs2": "cs2-terminal-artifact-provenance-unavailable",
    "character": "character-blocking-terminal-gates-unavailable",
}


def _fail(reason):
    raise ParentError(reason)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _relative(path):
    if (type(path) is not str or not path or "\\" in path or ":" in path
            or path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/"))):
        _fail("unsafe-authority-path")
    return path


@dataclass(frozen=True)
class GateSpec:
    gate_id: str
    script: str
    arguments: tuple[str, ...]


@dataclass(frozen=True)
class GateContract:
    provider: str
    output_kind: str
    artifact_kind: str
    artifact_relative: str
    gates: tuple[GateSpec, ...]
    collections: tuple[str, ...]
    singleton_bindings: tuple[str, ...]
    requires_core: bool = True


@dataclass(frozen=True)
class PixiQualityPolicy:
    """Program-approved 2D requirements; never deserialize authority from a model.

    requirements is exactly {requiredNodes, criticalFeatures, behaviorChecks};
    request_binding binds the independently retained user requirements in the
    parent workspace. Keep the caller's policy mapping live for revocation.
    """
    revision: int
    requirements: dict
    request_binding: dict
    requested_model: str = "jev-1.14.0"


PIXI_CONTRACT = GateContract(
    provider="pixi2d", output_kind="2d-scene", artifact_kind="img2.pixi2d-scene",
    artifact_relative=_ARTIFACT,
    gates=(GateSpec("pixi2d-editable-review", "tools/pixi2d.py", ("gate", "--workspace", "{workspace}")),),
    collections=("editableSources", "assets", "bindings", "evidence"), singleton_bindings=("review",))


class RouteAuthority(AggregateAuthority):
    """A callable aggregate verifier and opaque terminal-verification receipt."""

    def __init__(self, router, decision_ref, *, discovered, owners, harness,
                 runtime_provider=None, contracts=(PIXI_CONTRACT,), timeout=30,
                 runner=subprocess.run, pixi_quality_policies=None):
        if type(router) is not Router:
            _fail("actual-router-required")
        if (type(discovered) is not dict or set(discovered) != {"candidates", "rejected"}
                or type(discovered["candidates"]) is not list or type(owners) is not dict
                or not owners or any(type(owner) is not OwnerProfile for owner in owners.values())
                or type(harness) not in (list, tuple) or not harness
                or any(type(arg) is not str or not arg for arg in harness)
                or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300 or not callable(runner)):
            _fail("invalid-route-authority-input")
        self.router = router
        self.workspace = router.workspace
        self.decision_ref = _relative(decision_ref)
        self._discovered = deepcopy(discovered)
        self._live_owners = owners
        self._owners = deepcopy(owners)
        self._harness = tuple(harness)
        self._runtime = deepcopy(runtime_provider)
        self._timeout = timeout
        self._runner = runner
        if pixi_quality_policies is None:
            pixi_quality_policies = {}
        if (type(pixi_quality_policies) is not dict
                or any(type(policy) is not PixiQualityPolicy for policy in pixi_quality_policies.values())):
            _fail("invalid-pixi-quality-policy")
        self._live_pixi_policies = pixi_quality_policies
        self._pixi_policies = deepcopy(pixi_quality_policies)
        for policy in self._pixi_policies.values():
            if (type(policy.revision) is not int or policy.revision < 1
                    or type(policy.requirements) is not dict
                    or set(policy.requirements) != {"requiredNodes", "criticalFeatures", "behaviorChecks"}
                    or any(type(values) is not list for values in policy.requirements.values())
                    or type(policy.request_binding) is not dict
                    or set(policy.request_binding) != {"role", "path", "sha256"}
                    or policy.request_binding["role"] != "user-requirements"
                    or type(policy.requested_model) is not str or not policy.requested_model):
                _fail("invalid-pixi-quality-policy")
        if type(contracts) is not tuple or any(type(contract) is not GateContract for contract in contracts):
            _fail("invalid-gate-contract")
        self._contracts = {}
        for contract in contracts:
            if contract.output_kind != "2d-scene":
                _fail("native-terminal-contract-amendment-required")
            key = (contract.provider, contract.output_kind)
            if (key in self._contracts or not contract.gates or not contract.collections
                    or type(contract.requires_core) is not bool
                    or any(type(gate) is not GateSpec for gate in contract.gates)
                    or not any(key != "assets" for key in contract.collections)
                    or any(type(gate.gate_id) is not str or not gate.gate_id for gate in contract.gates)
                    or any(type(gate.arguments) is not tuple for gate in contract.gates)
                    or len({gate.gate_id for gate in contract.gates}) != len(contract.gates)):
                _fail("invalid-gate-contract")
            _relative(contract.artifact_relative)
            for gate in contract.gates:
                _relative(gate.script)
                if any(type(arg) is not str or ("{" in arg and arg != "{workspace}") for arg in gate.arguments):
                    _fail("invalid-gate-contract")
            self._contracts[key] = contract
        candidates = self._discovered["candidates"]
        if (any(type(candidate) is not dict or type(candidate.get("id")) is not str for candidate in candidates)
                or len({candidate["id"] for candidate in candidates}) != len(candidates)):
            _fail("invalid-discovery-snapshot")
        self._candidates = {candidate["id"]: candidate for candidate in candidates}
        if not set(owners) <= self._candidates.keys():
            _fail("unapproved-owner-profile")
        self._profile_templates = {}
        for ident, owner in owners.items():
            candidate = self._candidates[ident]
            augmentation_owner = (
                candidate.get("provider") == "cs2"
                and type(candidate.get("resolvedSha")) is str
                and _PIN.fullmatch(candidate["resolvedSha"]) is not None
                and candidate.get("card", {}).get("outputKind") == "spec-augmentation"
                and owner == OwnerProfile("cs2", "3d-object"))
            if (owner.output_kind not in {"2d-scene", "3d-object", "3d-scene", "rigged-3d"}
                    or (not augmentation_owner and owner.output_kind != candidate.get("card", {}).get("outputKind"))):
                _fail("owner-output-mismatch")
            self._profile_templates[ident] = _encode(new_state("reference.png", profile=owner.profile))

    def _run(self, argv, *, cwd=None, env=None):
        try:
            result = self._runner(list(argv), capture_output=True, timeout=self._timeout,
                                  check=False, shell=False, cwd=str(cwd or self.workspace), env=env)
        except subprocess.TimeoutExpired as exc:
            raise ParentError("authority-command-timeout") from exc
        except OSError as exc:
            raise ParentError("authority-command-unavailable") from exc
        raw = result.stdout.encode() if type(result.stdout) is str else result.stdout
        if type(raw) is not bytes or len(raw) > 8 * 1024 * 1024:
            _fail("invalid-authority-command-output")
        return result.returncode, raw

    def _pinned(self, provider, relative):
        if (type(provider) is not dict or type(provider.get("dir")) is not str
                or type(provider.get("resolvedSha")) is not str or not _PIN.fullmatch(provider["resolvedSha"])):
            _fail("provider-unpinned")
        directory = Path(provider["dir"])
        if not directory.is_absolute() or directory.is_symlink() or directory.resolve() != directory:
            _fail("unsafe-provider-path")
        relative = _relative(relative)
        try:
            data, _ = _read(directory, relative)
        except FileNotFoundError as exc:
            raise ParentError("provider-file-missing") from exc
        except OSError as exc:
            raise ParentError("unsafe-provider-path") from exc
        code, committed = self._run(("git", "-C", str(directory), "show", provider["resolvedSha"] + ":" + relative))
        if code != 0 or data != committed:
            _fail("provider-pin-mismatch")
        return data

    def _python_sources(self, provider, relative):
        root = Path(provider["dir"]) / relative
        files = {}
        for directory, dirs, names in os.walk(root, followlinks=False):
            if any((Path(directory) / name).is_symlink() for name in dirs):
                _fail("unsafe-provider-import-path")
            for name in names:
                path = Path(directory) / name
                if path.suffix in {".so", ".pyd", ".pyc"} and "__pycache__" not in path.parts:
                    _fail("unsupported-provider-import")
                if path.suffix == ".py":
                    local = path.relative_to(Path(provider["dir"])).as_posix()
                    files[local] = _sha(self._pinned(provider, local))
        if not files:
            _fail("provider-python-sources-missing")
        return files

    def _query(self, provider):
        edge = EDGES.get(provider)
        if edge is None:
            _fail("unapproved-provider")
        code, raw = self._run((*self._harness, "capabilities", "--from-kind", edge[0], "--to-kind", edge[1], "--json"))
        response = _decode(raw)
        if (code != 0 or type(response) is not dict or response.get("version") != 1
                or response.get("query") != {"from": edge[0], "to": edge[1]}
                or response.get("status") != "answered" or type(response.get("providers")) is not list
                or len(response["providers"]) != 1):
            _fail("live-provider-unavailable-or-ambiguous")
        return response["providers"][0]

    def _dependency(self, name):
        match = _DEPENDENCY.fullmatch(name)
        if match is None:
            return False
        code, raw = self._run((match[1], "--version"))
        version = re.search(rb"\b(\d+)\.(\d+)(?:\.(\d+))?", raw)
        if code or version is None:
            return False
        actual = tuple(int(part or b"0") for part in version.groups())
        required = tuple(int(part) for part in match[2].split("."))
        return actual >= required + (0,) * (3 - len(required))

    def _provider(self, candidate):
        name = candidate["provider"]
        row = self._query(name)
        if (type(row) is not dict or row.get("plugin") != name
                or row.get("resolvedSha") != candidate["resolvedSha"]):
            _fail("live-provider-identity-mismatch")
        manifest = _decode(self._pinned(row, "plugin.json"))
        card = _decode(self._pinned(row, "route.json"))
        try:
            gates = _decode(self._pinned(row, "gates.json"))
        except ParentError as exc:
            if exc.reason_code != "provider-file-missing" or candidate.get("blockingGateProduced") is not False:
                raise
            gates = []
        if (type(manifest) is not dict or manifest.get("name") != name or manifest.get("version") != row.get("version")
                or {"from": EDGES[name][0], "to": EDGES[name][1]} not in manifest.get("capabilities", [])
                or card != candidate["card"] or type(gates) is not list):
            _fail("live-provider-contract-mismatch")
        code, version_raw = self._run((*self._harness, "--version", "--json"))
        version = _decode(version_raw)
        if code or type(version) is not dict or type(version.get("harness")) is not str:
            _fail("live-harness-unavailable")
        requirement = manifest.get("requires", {}).get("harness")
        if requirement is not None:
            minimum = re.fullmatch(r">=(\d+)\.(\d+)\.(\d+)", requirement)
            current = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version["harness"])
            if (minimum is None or current is None
                    or tuple(map(int, current.groups())) < tuple(map(int, minimum.groups()))):
                _fail("live-harness-incompatible")
        binding = candidate["binding"]
        card_raw, _ = _read(self.workspace, binding["path"])
        expected_card = {"provider": name, "resolvedSha": row["resolvedSha"], "card": card}
        expected_id = name + ":" + _sha((row["resolvedSha"] + ":" + binding["sha256"]).encode())
        if (_sha(card_raw) != binding["sha256"] or _decode(card_raw) != expected_card
                or candidate["id"] != expected_id or candidate.get("providerStatus") != "answered"
                or candidate.get("claimants") != [name]):
            _fail("stale-discovery-snapshot")
        dependency_facts = {item["probe"]: self._dependency(item["probe"]) for item in card.get("deps", [])}
        if candidate.get("dependencies") != dependency_facts:
            _fail("live-dependency-mismatch")
        gate_runner = row.get("gateRunner")
        produced = False
        if type(gate_runner) is dict and type(gate_runner.get("argv")) is list and gate_runner["argv"]:
            for gate in gates:
                if type(gate) is not dict or gate.get("blocking") is not True or gate.get("actor", "program") != "program":
                    continue
                argv = shlex.split(gate.get("command", ""))
                if (len(argv) >= 2 and argv[0] in {"python3", "node"} and shutil.which(argv[0])
                        and argv[1].startswith("{plugin_dir}/")):
                    self._pinned(row, argv[1][len("{plugin_dir}/"):])
                    produced = True
        if candidate.get("blockingGateProduced") is not produced:
            _fail("live-blocking-producer-mismatch")
        return row, gates

    def _verify(self, verify):
        if not callable(verify) or not callable(getattr(verify, "route_rounds", None)) or not callable(getattr(verify, "route_round_links", None)):
            _fail("held-route-transaction-required")
        if self._live_owners != self._owners:
            _fail("owner-profile-approval-changed")
        for ident, owner in self._owners.items():
            if _encode(new_state("reference.png", profile=owner.profile)) != self._profile_templates[ident]:
                _fail("owner-profile-definition-changed")
        for candidate in self._candidates.values():
            if candidate.get("provider") == "base":
                if candidate != self.router._generic():
                    _fail("base-provider-identity-mismatch")
            else:
                self._provider(candidate)
        latest = _decode(_read(self.workspace, self.router.latest)[0])
        raw, _ = _read(self.workspace, self.decision_ref)
        if latest != {"path": self.decision_ref, "sha256": _sha(raw)}:
            _fail("stale-route-aggregate")
        with read_only_probes(self.router.discovery):
            decision = self.router.verify_decision(self.decision_ref, verify=verify, discovered=deepcopy(self._discovered))
        inventory_raw, _ = _read(self.workspace, decision["inventoryRef"])
        if _sha(inventory_raw) != decision["inventorySha256"]:
            _fail("inventory-mismatch")
        inventory = _decode(inventory_raw)
        selected = {entry["ownerRoute"] for entry in inventory["objects"]}
        if not selected <= self._owners.keys():
            _fail("unapproved-owner-profile")
        for ident in selected:
            if self._candidates[ident]["card"].get("costClass") != "local":
                _fail("paid-or-licensed-owner-not-authorized")
        owners = {ident: self._owners[ident] for ident in selected}
        return decision, inventory, owners

    def __call__(self, verify):
        decision, inventory, owners = self._verify(verify)
        return VerifiedAggregate(decision, inventory, owners, self)

    def revalidate(self, verify, *, decision, inventory, owners):
        actual_decision, actual_inventory, actual_owners = self._verify(verify)
        if (decision != actual_decision or inventory != actual_inventory or owners != actual_owners):
            _fail("aggregate-authority-mismatch")

    def _terminal(self, verify, object_id, child_workspace, state, artifact_bytes, artifact_relative):
        decision, inventory, owners = self._verify(verify)
        entries = [entry for entry in inventory["objects"] if entry["objectId"] == object_id]
        if len(entries) != 1:
            _fail("unknown-child")
        ident = entries[0]["ownerRoute"]
        candidate = self._candidates[ident]
        owner = owners[ident]
        row = next(row for row in decision["objects"] if row["objectId"] == object_id)
        expected_workspace = self.workspace / f".img2threejs/objects/{object_id}/rev-{row['revision']}"
        if Path(child_workspace) != expected_workspace or expected_workspace.resolve() != expected_workspace:
            _fail("child-workspace-mismatch")
        route_child = state.get("routeChild", {})
        if (state.get("profile") != owner.profile or route_child.get("runId") != self.router.run_id
                or route_child.get("objectId") != object_id or route_child.get("revision") != row["revision"]
                or route_child.get("ownerRoute") != ident or route_child.get("outputKind") != owner.output_kind
                or route_child.get("cropSha256") != entries[0]["reference"]["sha256"]):
            _fail("child-owner-binding-mismatch")
        contract = self._contracts.get((candidate["provider"], owner.output_kind))
        if contract is None:
            _fail(_UNSUPPORTED.get(candidate["provider"], "unsupported-terminal-gate-contract"))
        if artifact_relative != contract.artifact_relative:
            _fail("terminal-artifact-path-mismatch")
        actual_bytes, _ = _read(child_workspace, artifact_relative)
        if actual_bytes != artifact_bytes:
            _fail("terminal-artifact-bytes-mismatch")
        artifact = _decode(actual_bytes)
        if (type(artifact) is not dict or artifact.get("kind") != contract.artifact_kind
                or artifact.get("version") != 1 or artifact.get("objectId") != object_id
                or artifact.get("outputKind") != owner.output_kind):
            _fail("terminal-artifact-contract-mismatch")
        evidence_before = self._evidence(child_workspace, artifact, contract)
        reference_path = state.get("artifacts", {}).get("reference")
        expected_reference = {"path": reference_path, "sha256": entries[0]["reference"]["sha256"]}
        if expected_reference not in artifact.get("bindings", []):
            _fail("terminal-reference-not-bound")
        if contract.provider == "pixi2d":
            inputs = _decode(_read(child_workspace, ".img2/artifacts/pixi2d/inputs.json")[0])
            if inputs.get("reference") != reference_path or inputs.get("objectId") != object_id:
                _fail("terminal-reference-not-bound")
        provider, gates = self._provider(candidate)
        self._gate_contract(gates, contract)
        provider_sources = self._python_sources(provider, "tools")
        env = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
        if contract.requires_core:
            if type(self._runtime) is not dict:
                _fail("pinned-gate-runtime-required")
            runtime = Path(self._runtime.get("dir", ""))
            if runtime.name != "harness":
                _fail("unsupported-gate-runtime-layout")
            self._pinned(self._runtime, "img2_core/__init__.py")
            runtime_sources = self._python_sources(self._runtime, "img2_core")
            env["IMG2_HOME"] = str(runtime.parent)
        interpreter = shutil.which("python3")
        if interpreter is None:
            _fail("gate-interpreter-unavailable")
        interpreter = str(Path(interpreter).resolve(strict=True))
        interpreter_sha = _sha(Path(interpreter).read_bytes())
        with tempfile.TemporaryDirectory(prefix="img2-gate-pycache-") as cache:
            for gate in contract.gates:
                script = str(Path(provider["dir"]) / gate.script)
                argv = [interpreter, "-E", "-s", "-S", "-B", "-X", "pycache_prefix=" + cache, script,
                        *(str(child_workspace) if arg == "{workspace}" else arg for arg in gate.arguments)]
                code, raw = self._run(argv, cwd=child_workspace, env=env)
                if code != 0:
                    _fail("blocking-terminal-gate-failed")
                verdict = _decode(raw)
                if (type(verdict) is not dict or verdict.get("kind") != "img2.gate-verdict"
                        or verdict.get("version") != 1 or verdict.get("plugin") != contract.provider
                        or verdict.get("gate") != gate.gate_id or verdict.get("status") != "pass"
                        or verdict.get("reasons") != []):
                    _fail("blocking-terminal-gate-failed")
                expected_binding = {"path": artifact_relative, "sha256": _sha(artifact_bytes)}
                if type(verdict.get("evidence")) is not dict or verdict["evidence"].get("artifact") != expected_binding:
                    _fail("terminal-gate-artifact-binding-mismatch")
        if _read(child_workspace, artifact_relative)[0] != artifact_bytes:
            _fail("terminal-artifact-changed-during-gate")
        if self._evidence(child_workspace, artifact, contract) != evidence_before:
            _fail("terminal-evidence-changed-during-gate")
        if self._python_sources(provider, "tools") != provider_sources:
            _fail("provider-sources-changed-during-gate")
        self._provider(candidate)
        if contract.requires_core:
            if self._python_sources(self._runtime, "img2_core") != runtime_sources:
                _fail("gate-runtime-changed")
        if _sha(Path(interpreter).read_bytes()) != interpreter_sha:
            _fail("gate-interpreter-changed")
        self._verify(verify)
        self.consume_pixi_quality(verify, object_id, child_workspace)
        # An authenticated semantic decision is necessary, not sufficient:
        # capture.json's producer/renderer strings still do not prove that the
        # pinned helper ran inside the real host with these source bytes loaded.
        # No reviewed host replay/attestation contract currently exists.
        _fail("terminal-native-capture-authority-unavailable")

    def pixi_quality_context(self, object_id, child_workspace):
        """Project an unpromoted candidate for the shared quality-review edge.

        Publication order: produce/publish candidate -> build this context ->
        consult the shared controller OUTSIDE the parent transaction -> persist
        canonical context at .img2/artifacts/pixi2d/quality-review-context.json
        and a quality-review-link.json containing {context:{path,sha256},
        record:{path,sha256}}. Paths in the link are parent-workspace-relative;
        the record path is the controller's quality-review/<recordId>.json.
        Do not rewrite native review.json/artifact from the decision afterwards.
        The projection never claims native capture provenance or emits authority.
        """
        if self._live_pixi_policies != self._pixi_policies:
            _fail("pixi2d-quality-policy-changed")
        policy = self._pixi_policies.get(object_id)
        if policy is None:
            _fail("terminal-authenticated-quality-review-unavailable")
        child = Path(child_workspace)
        expected = self.workspace / f".img2threejs/objects/{object_id}/rev-{policy.revision}"
        if child != expected or child.resolve() != expected:
            _fail("child-workspace-mismatch")
        relative = child.relative_to(self.workspace).as_posix()
        state = _decode(_read(child, "state.json")[0])
        route_child = state.get("routeChild", {})
        if (route_child.get("runId") != self.router.run_id or route_child.get("objectId") != object_id
                or route_child.get("revision") != policy.revision or route_child.get("outputKind") != "2d-scene"):
            _fail("pixi-quality-subject-mismatch")
        area = ".img2/artifacts/pixi2d/"
        artifact = _decode(_read(child, _ARTIFACT)[0])
        self._evidence(child, artifact, PIXI_CONTRACT)
        snapshot = _decode(_read(child, area + "snapshot.json")[0])
        evidence = _decode(_read(child, area + "evidence.json")[0])
        inputs = _decode(_read(child, area + "inputs.json")[0])
        if (snapshot.get("objectId") != object_id or artifact.get("objectId") != object_id
                or _encode(snapshot.get("requirements")) != _encode(policy.requirements)):
            _fail("pixi-quality-requirements-mismatch")
        checks = ["requirements", "layout", "identity", "ownership", "motion-coverage", "provenance", "lifecycle"]
        for node in policy.requirements["requiredNodes"]:
            checks.extend("edit:" + node["id"] + ":" + edit for edit in node["edits"])
        checks.extend("feature:" + feature["id"] for feature in policy.requirements["criticalFeatures"])
        checks.extend("behavior:" + behavior["id"] for behavior in policy.requirements["behaviorChecks"])
        if snapshot.get("checks") != checks or len(set(checks)) != len(checks):
            _fail("pixi-quality-required-checks-mismatch")
        bindings, images, views, names = [], [], [], {}

        def bound(role, path, *, image=False, parent=False):
            path = _relative(path)
            root = self.workspace if parent else child
            raw, _ = _read(root, path)
            bindings.append({"role": role, "path": path if parent else relative + "/" + path, "sha256": _sha(raw)})
            if image:
                decode(raw)  # Admission only; a readable PNG is not host provenance.
                images.append({"role": role, "asset": role})
            return raw

        route_digest = _sha(_read(self.workspace, self.decision_ref)[0])
        request = policy.request_binding
        if _sha(bound("user-requirements", request["path"], parent=True)) != request["sha256"]:
            _fail("stale-pixi-user-requirements")
        for role, path in (("artifact", _ARTIFACT), ("native-snapshot", area + "snapshot.json"),
                           ("native-evidence", area + "evidence.json"), ("native-capture", evidence["capture"]["path"]),
                           ("native-inputs", area + "inputs.json"), ("native-review", artifact["review"]["path"])):
            bound(role, path)
        reference = state.get("artifacts", {}).get("reference")
        if inputs.get("reference") != reference:
            _fail("terminal-reference-not-bound")
        reference_raw = bound("reference", reference, image=True)
        if _sha(reference_raw) != route_child.get("cropSha256"):
            _fail("stale-reference-crop")
        for index, binding in enumerate(snapshot["bindings"]):
            bound("native-binding-" + str(index), binding["path"])
        for index, binding in enumerate(artifact["editableSources"]):
            bound("source-" + str(index), binding["path"])
        for index, binding in enumerate(artifact["assets"]):
            bound("asset-" + str(index), binding["path"])
        for index, binding in enumerate(evidence["files"]):
            name = Path(binding["path"]).name.split(".", 1)[0]
            if name in names:
                _fail("pixi-quality-ambiguous-image")
            role = "render" if name == "baseline" else "capture-" + str(index)
            bound(role, binding["path"], image=True)
            names[name] = role
            views.append("view-" + str(index))
        if "baseline" not in names:
            _fail("pixi-quality-baseline-missing")
        feature_roles = {}
        for feature in policy.requirements["criticalFeatures"]:
            pair = [names.get("feature-" + feature["id"] + "-" + side) for side in ("reference", "render")]
            if None in pair:
                _fail("pixi-quality-critical-crops-missing")
            feature_roles["feature:" + feature["id"]] = pair
        for node in policy.requirements["requiredNodes"]:
            for edit in node["edits"]:
                prefix = "edit-" + node["id"] + "-" + edit
                if prefix not in names or prefix + "-reset" not in names:
                    _fail("pixi-quality-edit-captures-missing")
        for behavior in policy.requirements["behaviorChecks"]:
            prefix = "behavior-" + behavior["id"]
            if prefix not in names or prefix + "-reset" not in names:
                _fail("pixi-quality-behavior-captures-missing")
        image_roles = [image["role"] for image in images]
        dimensions = [{"id": name, "threshold": 3, "evidenceRoles": image_roles} for name in (
            "silhouette-proportions", "part-relationships", "geometry", "material-appearance", "identity-features")]
        critical = [{"id": check, "threshold": 3, "evidenceRoles": feature_roles.get(check, image_roles)}
                    for check in checks]
        return {"kind": "img2.quality-review-context", "version": 1, "group": "quality-review",
            "subject": {"runId": self.router.run_id, "objectId": object_id, "revision": policy.revision,
                        "passId": "pixi2d-terminal-review"},
            "bindings": bindings, "candidates": [],
            "observations": {"images": images, "availableViews": views, "nativeRequirements": deepcopy(policy.requirements),
                "routeDecisionSha256": route_digest,
                "reviewScope": "Editable 2D artwork only; no 3D orbit, volume, rig or capture-authenticity claim."},
            "requirements": {"rubricVersion": "quality-review-rubric-v1", "dimensions": dimensions,
                "criticalFeatures": critical, "requiredViews": views,
                "evidenceRoles": [binding["role"] for binding in bindings]},
            "policyVersion": "pixi2d-quality-policy-v1", "questionSetVersion": "quality-review-questions-v1",
            "requestedModel": policy.requested_model}

    def consume_pixi_quality(self, verify, object_id, child_workspace):
        """Consume journal-authenticated semantic evidence, NOT terminal authority."""
        expected = self.pixi_quality_context(object_id, child_workspace)
        child = Path(child_workspace)
        relative = child.relative_to(self.workspace).as_posix()
        area = ".img2/artifacts/pixi2d/"
        try:
            link = _decode(_read(child, area + "quality-review-link.json")[0])
        except FileNotFoundError as exc:
            raise ParentError("terminal-authenticated-quality-review-unavailable") from exc
        if type(link) is not dict or set(link) != {"context", "record"}:
            _fail("invalid-pixi-quality-link")
        values = {}
        for name in ("context", "record"):
            binding = link[name]
            if type(binding) is not dict or set(binding) != {"path", "sha256"}:
                _fail("invalid-pixi-quality-link")
            raw, _ = _read(self.workspace, _relative(binding["path"]))
            if _sha(raw) != binding["sha256"]:
                _fail("stale-pixi-quality-link")
            values[name] = _decode(raw)
        context, record = values["context"], values["record"]
        context_path = relative + "/" + area + "quality-review-context.json"
        if (link["context"]["path"] != context_path or _encode(context) != _encode(expected)
                or type(record) is not dict
                or link["record"]["path"] != ".img2threejs/decisions/quality-review/" + str(record.get("recordId")) + ".json"):
            _fail("pixi-quality-evidence-mismatch")
        with read_only_probes(self.router.discovery):
            record = verify(record, context)
        if (record.get("group") != "quality-review" or record.get("subject") != expected["subject"]
                or record.get("status") != "answered" or not record.get("decider", "").startswith("jev:")):
            _fail("pixi-quality-review-not-authorized")
        for dimension in expected["requirements"]["dimensions"]:
            answer = record["answers"].get("dimension:" + dimension["id"], {})
            if answer.get("type") != "score" or answer.get("score", -1) < dimension["threshold"]:
                _fail("pixi-semantic-quality-failed")
        for feature in expected["requirements"]["criticalFeatures"]:
            answer = record["answers"].get("critical:" + feature["id"], {})
            if answer.get("type") != "choice" or answer.get("choice") != "pass":
                _fail("pixi-semantic-quality-failed")
        return record

    @staticmethod
    def _gate_contract(gates, contract):
        if any(type(gate) is not dict or type(gate.get("blocking")) is not bool
               or type(gate.get("id")) is not str or type(gate.get("command")) is not str
               for gate in gates):
            _fail("invalid-blocking-terminal-gate-contract")
        blocking = [gate for gate in gates if type(gate) is dict and gate.get("blocking") is True]
        by_id = {gate.get("id"): gate for gate in blocking}
        if len(by_id) != len(blocking) or set(by_id) != {gate.gate_id for gate in contract.gates}:
            _fail("unsupported-blocking-terminal-gate")
        completed = set()
        for spec in contract.gates:
            gate = by_id[spec.gate_id]
            expected = ["python3", "{plugin_dir}/" + spec.script, *spec.arguments]
            if gate.get("actor", "program") != "program" or shlex.split(gate.get("command", "")) != expected:
                _fail("unreviewed-terminal-gate-command")
            prerequisites = gate.get("after", [])
            if type(prerequisites) is not list or not set(prerequisites) <= completed:
                _fail("unsupported-terminal-gate-prerequisite")
            completed.add(spec.gate_id)

    @staticmethod
    def _evidence(workspace, artifact, contract):
        bindings = []
        for key in contract.collections:
            rows = artifact.get(key)
            if type(rows) is not list or (key != "assets" and not rows):
                _fail("terminal-evidence-missing")
            bindings.extend(rows)
        for key in contract.singleton_bindings:
            bindings.append(artifact.get(key))
        seen = {}
        for binding in bindings:
            if (type(binding) is not dict or set(binding) != {"path", "sha256"}
                    or type(binding["sha256"]) is not str or not _SHA.fullmatch(binding["sha256"])):
                _fail("invalid-terminal-evidence-binding")
            path = _relative(binding["path"])
            if path in seen and seen[path] != binding["sha256"]:
                _fail("conflicting-terminal-evidence-binding")
            seen[path] = binding["sha256"]
            data, _ = _read(workspace, path)
            if _sha(data) != binding["sha256"]:
                _fail("stale-terminal-evidence")
        if contract.provider == "pixi2d":
            area = ".img2/artifacts/pixi2d/"
            snapshot_raw, _ = _read(workspace, area + "snapshot.json")
            evidence_raw, _ = _read(workspace, area + "evidence.json")
            snapshot, evidence = _decode(snapshot_raw), _decode(evidence_raw)
            if type(snapshot) is not dict or type(evidence) is not dict:
                _fail("terminal-produced-evidence-mismatch")
            if (_sha(_encode(snapshot)) != artifact.get("snapshotSha256")
                    or _sha(_encode(evidence)) != artifact.get("evidenceSha256")
                    or snapshot.get("bindings") != artifact.get("bindings")
                    or evidence.get("files") != artifact.get("evidence")):
                _fail("terminal-produced-evidence-mismatch")
            capture = evidence.get("capture")
            if type(capture) is not dict or set(capture) != {"path", "sha256"}:
                _fail("terminal-capture-binding-missing")
            capture_raw, _ = _read(workspace, _relative(capture["path"]))
            if _sha(capture_raw) != capture["sha256"]:
                _fail("stale-terminal-capture")
            for path, data in ((area + "snapshot.json", snapshot_raw), (area + "evidence.json", evidence_raw),
                               (capture["path"], capture_raw)):
                seen[path] = _sha(data)
        return seen

    def verify_2d_terminal(self, verify, *, object_id, child_workspace, artifact_path, artifact_bytes, state):
        expected = str(Path(child_workspace).relative_to(self.workspace) / _ARTIFACT)
        if artifact_path != expected:
            _fail("terminal-artifact-path-mismatch")
        self._terminal(verify, object_id, child_workspace, state, artifact_bytes, _ARTIFACT)

    def verify_terminal(self, verify, *, object_id, child_workspace, state, artifact):
        # D18 has no required kind/version/bindings envelope. Never reinterpret
        # it as Pixi or authorize native output through a data-only registration.
        _, inventory, _ = self._verify(verify)
        entries = [entry for entry in inventory["objects"] if entry["objectId"] == object_id]
        if len(entries) != 1:
            _fail("unknown-child")
        candidate = self._candidates[entries[0]["ownerRoute"]]
        _fail(_UNSUPPORTED.get(candidate["provider"], "unsupported-terminal-gate-contract"))
