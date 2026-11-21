"""D16 parent/child assembly, with an explicit program-owned authority boundary.

``RouteParent(controller, verify_aggregate=..., consent=...)`` exposes ``create()``
and ``complete_child(object_id)``. Both call the aggregate verifier and its opaque
receipt under ``controller.workspace_transaction``. The verifier must independently
authenticate the complete current inventory and approved owner profiles; a routing
JSON document, hash, provider answer, or callable alone is not that authority.

There is intentionally no concrete AggregateAuthority here. Stage-zero currently
emits executionAuthorized=false and has no journal-backed aggregate receipt.
Production route-to-child integration therefore remains blocked until the caller
supplies that verifier. Test fixtures are not production authority. Likewise, the
receipt must independently verify terminal measured gates, not checklist evidence
strings. D18 validates artifact bytes, not visual quality.

Parent publication is last; every individual write is atomic and fsynced. Existing
parents/children are never replaced by create(). An interrupted creation may leave
identical initial children, which a retry reuses without resetting their cursors.
Authenticated subjects may share an immutable crop blob; each child receives its
own local copy and independent state, never shared mutable execution files.
Legacy state is never migrated or overwritten. No alternate workspace lease exists.
Explicit 2D children use only the Pixi snapshot/capture/evidence/review/publish/gate
checklist. Their complete pass sentinel bypasses sculpt passes, not required 2D work.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import secrets
import stat

from route_artifact import load_artifact
from workflow_state import new_state, recompute, validate_state, attach_lifecycle_steps


class ParentError(ValueError):
    def __init__(self, reason_code):
        self.reason_code = reason_code
        super().__init__(reason_code)


class AggregateAuthority(ABC):
    """Opaque, program-owned proof; never load or construct this from JSON.

    Implementations must authenticate current aggregate coverage, route selections,
    owner profiles and user approval of paid/licensed operations, under ``verify``.
    They must reject unbound/stale records and independently verify all measured
    blocking/hard/critical terminal gates on the supplied state/artifact bytes.
    Neither method may invoke another locking controller operation.
    """

    def __reduce_ex__(self, protocol):
        raise TypeError("aggregate authority cannot be serialized")

    @abstractmethod
    def revalidate(self, verify, *, decision, inventory, owners):
        """Raise unless these exact current inputs have authenticated completeness."""

    @abstractmethod
    def verify_terminal(self, verify, *, object_id, child_workspace, state, artifact):
        """Raise unless actual, current, artifact-bound terminal measurements pass."""

    @abstractmethod
    def verify_2d_terminal(self, verify, *, object_id, child_workspace, artifact_path, artifact_bytes, state):
        """Resolve/pin Pixi, run/recheck its blocking gate, and bind these bytes.

        This is a separate contract, never a D18 artifact or a JSON-only pass.
        Missing provider, producer evidence, or gate execution must raise.
        Independently hash every editable source, asset, binding, evidence and
        review file within the child; a matching manifest digest alone is not proof.
        """


@dataclass(frozen=True)
class OwnerProfile:
    """Independently approved execution profile and TERMINAL artifact kind.

    ``output_kind`` is not the selected route card's advisory ``outputKind``.
    A spec-augmentation route must have an explicitly approved consuming profile
    and terminal 3D output; this value never performs or authorizes that mapping.
    """

    profile: str
    output_kind: str


@dataclass(frozen=True)
class VerifiedAggregate:
    decision: dict
    inventory: dict
    owners: dict[str, OwnerProfile]
    authority_receipt: AggregateAuthority


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PARENT = ".img2threejs/state.json"
_PIXI2D_STEPS = (
    ("pixi2d-snapshot", "setup",
     "Resolve this child's approved pinned Pixi provider as SKILL_DIR. Author "
     ".img2/artifacts/pixi2d/inputs.json using {reference}, {spec}, the actual host profile, "
     "editable sources/assets, required nodes, critical features and behavior checks; then run "
     "python3 \"$SKILL_DIR/tools/pixi2d.py\" snapshot --workspace ."),
    ("pixi2d-native-host-capture", "setup",
     "Use $SKILL_DIR/skills/img2pixi2d/runtime/review-capture.mjs in the actual existing Pixi host "
     "with its Application, Pixi namespace, editable scene handle, bound snapshot and real "
     "behavior/reset callbacks. Capture every declared edit/behavior/reset after source, fonts "
     "and assets are ready; save .img2/artifacts/pixi2d/capture.json through the host's real "
     "browser/download channel. Never substitute a renderer, demo or reference pixels."),
    ("pixi2d-evidence-produce", "setup",
     "python3 \"$SKILL_DIR/tools/pixi2d.py\" produce --workspace ."),
    ("pixi2d-native-visual-review", "setup",
     "Read $SKILL_DIR/skills/img2pixi2d/references/harness-review.md. Inspect the saved reference, "
     "baseline, every edit/behavior/reset, critical-feature crop pairs, source/spec/profile and "
     "resource provenance with native host vision. Author .img2/artifacts/pixi2d/review.json "
     "with the actual reviewer identity, exact inspected bindings, every required judgment, "
     "score, reason and evidence, and unresolved defects. Missing or uncertain review blocks publication."),
    ("pixi2d-publish", "setup",
     "python3 \"$SKILL_DIR/tools/pixi2d.py\" publish --workspace ."),
    ("pixi2d-editable-review", "final",
     "python3 \"$SKILL_DIR/tools/pixi2d.py\" gate --workspace ."),
)

_INTERIOR_STEPS = (
    ("interior-intake", "setup",
     "Resolve this child's approved pinned interior provider as SKILL_DIR. Read its "
     "reference/production-runbook.md, reference/reference-analysis.md and reference/intake-camera.md. "
     "Run interior_project.py start and preflight for {reference}; author interior-intake.json and "
     "{spec} from the actual image, preserving source provenance and unknowns."),
    ("interior-route-exclusions", "setup",
     "Read $SKILL_DIR/reference/scene-contract.md. Use routeChild.excludedObjectIds from this "
     "child's state.json as the exact excludeComponents list in interior-intake.json and {spec}. "
     "Declare every excluded objectId unchanged in intake.components and use that identical "
     "objectId as the Blender component tag; never infer or manually guess a label mapping. "
     "The admitted .img2/artifacts/interior/intake.json must preserve this exact binding."),
    ("interior-workflow-init", "setup",
     "python3 \"$SKILL_DIR/tools/interior_workflow.py\" init --workspace . --image {reference} "
     "--intake ./interior-intake.json; use its next command on every resume."),
    ("interior-author-and-review", "setup",
     "Author and iteratively inspect the actual native Blender room, camera, geometry and materials "
     "using the interior workflow and preserved routed exclusions. Record each required workflow "
     "stage against actual renders; do not substitute a bundled scene or invented visual evidence."),
    ("interior-export-and-audit", "setup",
     "Run the interior bake, probe and export chain with routing.objectId and actual shell component "
     "identities in {spec}; run audit_routed_artifact.py against the exact produced artifact.json. "
     "A mechanical audit does not establish visual acceptance."),
    ("interior-delivery-gates", "final",
     "Run the actual interior material, delivery and workflow gates for this child. Independent "
     "artifact-bound visual and composition gates are still required before terminal acceptance."),
)



def _fail(reason):
    raise ParentError(reason)


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _decode(data):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                _fail("duplicate-json-key")
            result[key] = value
        return result

    def constant(value):
        _fail("invalid-json")

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, ParentError):
            raise
        raise ParentError("invalid-json") from exc


@contextmanager
def _directory(workspace, relative, *, create=False):
    """Pin each directory component; no symlink or traversal can escape workspace."""
    if (type(relative) is not str or not relative or "\\" in relative or ":" in relative
            or PurePosixPath(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in relative.split("/"))):
        _fail("unsafe-path")
    fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = relative.split("/")
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, parts[-1]
    finally:
        os.close(fd)


def _read(workspace, relative):
    with _directory(workspace, relative) as (directory, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                _fail("unsafe-path")
            return source.read(), (info.st_dev, info.st_ino)


def _existing(workspace, relative):
    try:
        return _read(workspace, relative)[0]
    except FileNotFoundError:
        return None


def _write(workspace, relative, data, *, replace=False):
    with _directory(workspace, relative, create=True) as (directory, name):
        temporary = ".parent-" + secrets.token_hex(16)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as target:
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
            if replace:
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            else:
                try:
                    os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory,
                            follow_symlinks=False)
                except FileExistsError:
                    if _read(workspace, relative)[0] != data:
                        _fail("existing-state-conflict")
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass


class RouteParent:
    def __init__(self, controller, *, verify_aggregate, consent):
        if not callable(verify_aggregate):
            _fail("aggregate-verifier-required")
        if not callable(consent):
            _fail("consent-unproven")
        self.controller = controller
        self.workspace = controller.workspace
        self.verify_aggregate = verify_aggregate
        self.consent = consent

    def _verified(self, verify):
        result = self.verify_aggregate(verify)
        if (type(result) is not VerifiedAggregate
                or not isinstance(result.authority_receipt, AggregateAuthority)):
            _fail("inventory-completeness-unverified")
        decision, inventory, owners = deepcopy((result.decision, result.inventory, result.owners))
        # A serialized flag must never become authority, even beside a real receipt.
        if (type(decision) is not dict or decision.get("kind") != "img2.route-decision"
                or decision.get("version") != 1 or decision.get("phase") != "decided"
                or decision.get("runId") != self.controller.run_id
                or decision.get("executionAuthorized") is not False):
            _fail("invalid-route-decision")
        if (type(inventory) is not dict or inventory.get("kind") != "img2.object-inventory"
                or inventory.get("version") != 1
                or inventory.get("imageSha256") != decision.get("imageSha256")
                or hashlib.sha256(_encode(inventory)).hexdigest() != decision.get("inventorySha256")):
            _fail("inventory-mismatch")
        if result.authority_receipt.revalidate(
                verify, decision=decision, inventory=inventory, owners=owners) is not None:
            _fail("invalid-aggregate-receipt")
        return decision, inventory, owners, result.authority_receipt

    def _assemble(self, decision, inventory, owners):
        lease = _decode(_read(self.workspace, ".img2threejs/route/lease.json")[0])
        if (lease.get("runId") != self.controller.run_id
                or type(lease.get("token")) is not str or not _SHA.fullmatch(lease["token"])):
            _fail("lease-lost")
        expected = decision.get("request", {}).get("expectedOutput")
        if expected not in {"2d", "3d"} or decision.get("skipComposition") != (expected == "2d"):
            _fail("invalid-output-branch")
        rows = decision.get("objects")
        entries = inventory.get("objects")
        if type(rows) is not list or type(entries) is not list or not entries or len(entries) > 12:
            _fail("invalid-object-inventory")
        by_id = {}
        for row in rows:
            if type(row) is not dict or row.get("objectId") in by_id:
                _fail("duplicate-object")
            by_id[row["objectId"]] = row
        resolved = {key for key, row in by_id.items() if row.get("status") == "resolved"}
        if any(row.get("status") not in {"resolved", "dropped", "merged"} for row in rows):
            _fail("route-execution-unresolved")
        groups, group_ids = {}, set()
        for entry in entries:
            if type(entry) is not dict or type(entry.get("objectId")) is not str:
                _fail("invalid-object-inventory")
            if not _ID.fullmatch(entry["objectId"]) or entry["objectId"] in group_ids:
                _fail("duplicate-or-unsafe-object")
            group_ids.add(entry["objectId"])
            artifact_id = entry.get("artifactObjectId", entry["objectId"])
            if type(artifact_id) is not str or not _ID.fullmatch(artifact_id):
                _fail("invalid-object-inventory")
            groups.setdefault(artifact_id, []).append(entry)
        for artifact_id, members in groups.items():
            if (artifact_id not in {item["objectId"] for item in members}
                    or type(members[0].get("ownerRoute")) is not str
                    or any(item.get("ownerRoute") != members[0]["ownerRoute"] for item in members)):
                _fail("instance-group-mismatch")
            if len(members) > 1:
                instance = members[0].get("instanceOf")
                if (type(instance) is not str or not instance
                        or any(item.get("instanceOf") != instance
                               or item.get("role") == "scene-container" for item in members)):
                    _fail("instance-group-mismatch")
        seen = set()
        children, writes = [], []
        for entry in entries:
            object_id = entry.get("objectId")
            if type(object_id) is not str or not _ID.fullmatch(object_id) or object_id in seen:
                _fail("duplicate-or-unsafe-object")
            seen.add(object_id)
            row = by_id.get(object_id, {})
            owner_route = entry.get("ownerRoute")
            owner = owners.get(owner_route)
            revision = row.get("revision")
            if (row.get("status") != "resolved" or row.get("selected") != [owner_route]
                    or type(revision) is not int or revision < 1
                    or type(owner) is not OwnerProfile):
                _fail("unapproved-owner-profile")
            # The receipt approves the profile/terminal-kind pair. A route card
            # can emit augmentation consumed inside this child, never a terminal
            # spec-augmentation artifact or an implicit generic-profile fallback.
            allowed_kinds = {"2d-scene"} if expected == "2d" else {"3d-object", "3d-scene", "rigged-3d"}
            if owner.output_kind not in allowed_kinds:
                _fail("owner-output-mismatch")
            binding = entry.get("reference")
            if type(binding) is not dict or row.get("reference") != binding:
                _fail("reference-mismatch")
            sha, path = binding.get("sha256"), binding.get("path")
            if type(sha) is not str or not _SHA.fullmatch(sha):
                _fail("invalid-crop-digest")
            data, _ = _read(self.workspace, path)
            if not data or hashlib.sha256(data).hexdigest() != sha:
                _fail("stale-reference-crop")
            artifact_id = entry.get("artifactObjectId", object_id)
            if "artifactObjectId" in entry and (
                    row.get("routeObjectId") != artifact_id
                    or entry.get("routeObjectId") != artifact_id
                    or type(row.get("execution")) is not dict
                    or row["execution"].get("placementOnly") is not (object_id != artifact_id)):
                _fail("instance-group-mismatch")
            if object_id != artifact_id:
                continue
            directory = f".img2threejs/objects/{object_id}/rev-{revision}"
            child_path = directory + "/state.json"
            crop_path = directory + "/reference" + PurePosixPath(path).suffix
            interior = owner.profile == "interior" and owner.output_kind == "3d-scene"
            spec = "reconstruction.json" if expected == "2d" else (
                "scene-spec.json" if interior else "object-sculpt-spec.json")
            child = new_state("reference" + PurePosixPath(path).suffix, profile=owner.profile, spec=spec)
            if expected == "2d" or interior:
                steps = _PIXI2D_STEPS if expected == "2d" else _INTERIOR_STEPS
                child["checklist"] = [self._step(step_id, scope, command)
                                      for step_id, scope, command in steps]
                # These plugin workflows own their progression, not sculpt passes.
                # Their required steps and independent terminal gates remain pending.
                child["currentPass"] = "complete"
                recompute(child)
                validate_state(child)
            child["leaseToken"] = lease["token"]
            child["routeChild"] = {"runId": self.controller.run_id, "objectId": object_id,
                                   "revision": revision, "ownerRoute": owner_route,
                                   "outputKind": owner.output_kind, "cropSha256": sha}
            if len(groups[object_id]) > 1:
                child["routeChild"]["ownedObjectIds"] = sorted(
                    item["objectId"] for item in groups[object_id])
            if interior:
                child["routeChild"]["excludedObjectIds"] = sorted(resolved - {object_id})
            attach_lifecycle_steps(child)
            writes.extend([(crop_path, data), (child_path, _encode(child))])
            children.append({**child["routeChild"], "statePath": child_path,
                             "artifactPath": directory + ("/.img2/artifacts/pixi2d/artifact.json"
                                                          if expected == "2d" else "/artifact.json"),
                             "reference": deepcopy(binding), "profile": owner.profile})
        if seen != resolved:
            _fail("inventory-object-mismatch")
        if sum(row["profile"] == "interior" and row["outputKind"] == "3d-scene"
               for row in children) > 1:
            _fail("multiple-containers")
        if expected == "2d" and (seen != {"whole-image"} or len(children) != 1):
            _fail("explicit-2d-mismatch")
        parent = new_state(children[0]["reference"]["path"])
        parent["routeDecision"] = deepcopy(decision)
        parent["objects"] = children
        parent["runId"] = self.controller.run_id
        parent["leaseToken"] = lease["token"]
        parent["currentPass"] = "complete"
        parent["checklist"] = [self._step("object:" + row["objectId"], "setup", row["statePath"])
                               for row in children]
        if expected == "3d":
            # The executable base-owned entry points, not prose: composition writes a
            # new candidate scene and the gate measures it. Neither grants acceptance.
            # The inventory path is content-addressed from the digest _verified already
            # bound to this decision, so the gate cannot read a different inventory.
            inventory_path = (".img2threejs/route-inputs/object-inventory/"
                              + decision["inventorySha256"] + ".json")
            parent["checklist"].extend([
                self._step("composition", "final",
                           "python3 forge/compose.py scene --workspace . "
                           "--request .img2threejs/route/compose-request.json "
                           "--output-dir .img2threejs/scene"),
                self._step("scene-gates", "final",
                           "python3 forge/compose.py gates --workspace . "
                           "--composed .img2threejs/scene/scene.json "
                           "--measurements .img2threejs/scene/measurements.json "
                           "--inventory " + inventory_path)])
        recompute(parent)
        validate_state(parent)
        return parent, writes

    @staticmethod
    def _step(step_id, scope, command):
        return {"id": step_id, "scope": scope, "status": "pending", "evidence": [],
                "reason": "", "command": command}

    def create(self):
        """Authenticate and publish separate ordinary child states, then the parent."""
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            decision, inventory, owners, _ = self._verified(verify)
            if _existing(self.workspace, _PARENT) is not None:
                _fail("existing-parent-preserved")
            parent, writes = self._assemble(decision, inventory, owners)
            # Validate all collisions before publishing any child. Never reset an
            # existing child, even if it was left by an interrupted prior create.
            for path, data in writes:
                previous = _existing(self.workspace, path)
                if previous is not None and previous != data:
                    _fail("existing-state-conflict")
            for path, data in writes:
                _write(self.workspace, path, data)
            _write(self.workspace, _PARENT, _encode(parent))
            return deepcopy(parent)

    def _validate_child(self, verify, receipt, descriptor, template):
        child = _decode(_read(self.workspace, descriptor["statePath"])[0])
        validate_state(child)
        if (child.get("routeChild") != template["routeChild"]
                or child.get("leaseToken") != template["leaseToken"]
                or child.get("profile") != template["profile"]
                or child.get("artifacts", {}).get("reference") != template["artifacts"]["reference"]
                or child.get("artifacts", {}).get("spec") != template["artifacts"]["spec"]
                or [(r["id"], r["scope"]) for r in child["checklist"]] != [
                    (r["id"], r["scope"]) for r in template["checklist"]]):
            _fail("child-state-mismatch")
        if (descriptor["outputKind"] == "2d-scene" or descriptor["profile"] == "interior") and [
                (row["id"], row["scope"], row.get("command")) for row in child["checklist"]] != [
                    (row["id"], row["scope"], row["command"]) for row in template["checklist"]]:
            _fail("child-state-mismatch")
        child_workspace = self.workspace / descriptor["statePath"].rsplit("/", 1)[0]
        crop, _ = _read(child_workspace, child["artifacts"]["reference"])
        if hashlib.sha256(crop).hexdigest() != descriptor["cropSha256"]:
            _fail("stale-reference-crop")
        if (child.get("status") != "complete" or child.get("currentPass") != "complete"
                or child.get("currentStep") != "complete"
                or any(row["status"] == "pending" for row in child["checklist"])
                or any(row["status"] != "done" for row in child["checklist"]
                       if row["scope"] in {"final", "rig"} or row["id"] == "pass-gate-check")
                or ((descriptor["outputKind"] == "2d-scene" or descriptor["profile"] == "interior")
                    and any(row["status"] != "done" for row in child["checklist"]))):
            _fail("child-terminal-gates-incomplete")
        if descriptor["outputKind"] == "2d-scene":
            artifact_bytes = _read(self.workspace, descriptor["artifactPath"])[0]
            artifact = _decode(artifact_bytes)
            if (type(artifact) is not dict or artifact.get("kind") != "img2.pixi2d-scene"
                    or artifact.get("version") != 1 or artifact.get("outputKind") != "2d-scene"
                    or artifact.get("objectId") != descriptor["objectId"]
                    or type(artifact.get("editableSources")) is not list or not artifact["editableSources"]):
                _fail("invalid-2d-artifact")
            if receipt.verify_2d_terminal(
                    verify, object_id=descriptor["objectId"],
                    child_workspace=self.workspace / descriptor["statePath"].rsplit("/", 1)[0],
                    artifact_path=descriptor["artifactPath"], artifact_bytes=artifact_bytes,
                    state=deepcopy(child)) is not None:
                _fail("invalid-terminal-receipt")
            return
        if descriptor["profile"] == "interior" and descriptor["outputKind"] == "3d-scene":
            # The template is rederived from the revalidated aggregate, not an
            # editable child exclusion list or a guessed component-label mapping.
            excluded = template["routeChild"]["excludedObjectIds"]
            try:
                intake = _decode(_read(child_workspace, ".img2/artifacts/interior/intake.json")[0])
                spec = _decode(_read(child_workspace, template["artifacts"]["spec"])[0])
            except (OSError, ParentError) as exc:
                raise ParentError("interior-exclusion-binding-mismatch") from exc
            if (type(intake) is not dict or type(spec) is not dict
                    or type(spec.get("routing")) is not dict
                    or spec["routing"].get("objectId") != descriptor["objectId"]
                    or type(intake.get("excludeComponents")) is not list
                    or intake["excludeComponents"] != excluded
                    or type(spec.get("excludeComponents")) is not list
                    or spec["excludeComponents"] != excluded):
                _fail("interior-exclusion-binding-mismatch")
            components = intake.get("components")
            if (type(components) is not list or not components
                    or any(type(item) is not str or not item.strip() for item in components)
                    or len({item.strip() for item in components}) != len(components)
                    or not set(excluded) <= set(components)):
                _fail("interior-exclusion-binding-mismatch")
        artifact = load_artifact(child_workspace)
        if (artifact["objectId"] != descriptor["objectId"]
                or artifact["outputKind"] != descriptor["outputKind"]):
            _fail("child-artifact-mismatch")
        owned = descriptor.get("ownedObjectIds")
        if owned is not None and (artifact.get("ownerRoute") != descriptor["ownerRoute"]
                                  or type(artifact.get("ownedObjectIds")) is not list
                                  or any(type(item) is not str for item in artifact["ownedObjectIds"])
                                  or sorted(artifact["ownedObjectIds"]) != owned):
            _fail("child-artifact-mismatch")
        if (descriptor["profile"] == "interior" and descriptor["outputKind"] == "3d-scene"
                and (artifact.get("excludeComponents") != template["routeChild"]["excludedObjectIds"]
                     or artifact.get("ownerRoute") != descriptor["ownerRoute"]
                     or artifact.get("ownedObjectIds") != [descriptor["objectId"]])):
            _fail("interior-exclusion-binding-mismatch")
        if receipt.verify_terminal(verify, object_id=descriptor["objectId"],
                                   child_workspace=child_workspace,
                                   state=deepcopy(child), artifact=deepcopy(artifact)) is not None:
            _fail("invalid-terminal-receipt")

    def complete_child(self, object_id):
        """Complete after current state, measured gates and the route's artifact pass."""
        with self.controller.workspace_transaction(consent=self.consent) as verify:
            decision, inventory, owners, receipt = self._verified(verify)
            expected, writes = self._assemble(decision, inventory, owners)
            parent = _decode(_read(self.workspace, _PARENT)[0])
            validate_state(parent)
            if (parent.get("routeDecision") != decision or parent.get("objects") != expected["objects"]
                    or parent.get("runId") != self.controller.run_id
                    or parent.get("leaseToken") != expected["leaseToken"]):
                _fail("stale-parent")
            # Parent statuses are mutable, its structural plan is not.
            if [(r["id"], r["scope"]) for r in parent["checklist"]] != [
                    (r["id"], r["scope"]) for r in expected["checklist"]]:
                _fail("invalid-parent-checklist")
            if any(row["status"] != "pending" for row in parent["checklist"] if row["scope"] == "final"):
                _fail("composition-already-started")
            if any(row["status"] == "skipped" for row in parent["checklist"]):
                _fail("invalid-parent-checklist")
            if object_id not in {row["objectId"] for row in parent["objects"]}:
                _fail("unknown-child")
            completed = {row["id"][7:] for row in parent["checklist"]
                         if row["scope"] == "setup" and row["status"] == "done"} | {object_id}
            initial_files = dict(writes)
            for descriptor in parent["objects"]:
                if descriptor["objectId"] in completed:
                    self._validate_child(verify, receipt, descriptor,
                                         _decode(initial_files[descriptor["statePath"]]))
            for row in parent["checklist"]:
                if row["id"] == "object:" + object_id:
                    descriptor = next(item for item in parent["objects"] if item["objectId"] == object_id)
                    row.update(status="done", evidence=[descriptor["statePath"], descriptor["artifactPath"]], reason="")
            recompute(parent)
            validate_state(parent)
            _write(self.workspace, _PARENT, _encode(parent), replace=True)
            return deepcopy(parent)
