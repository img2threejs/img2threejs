"""D16 offline integration-seam tests, not production aggregate authorization.

FixtureAuthority is test-owned and deliberately never imported by production. Its
in-memory approvals stand for authenticated aggregate/measurement records only in
these fixtures; no test claims stage-zero currently supplies such records.
Shared-crop cases additionally consume real controller-journal object records under
the held verifier; their aggregate approval remains explicitly fixture-owned.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import pickle
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

_SHARED = str(Path(__file__).resolve().parents[1] / "_shared")
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

from decision_control import DecisionController, DecisionError
from route_artifact import ArtifactError
from route_parent import AggregateAuthority, OwnerProfile, ParentError, RouteParent, VerifiedAggregate
from workflow_state import mark_steps, new_state, validate_state


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


class FixtureAuthority(AggregateAuthority):
    def __init__(self, case, decision, inventory, owners):
        self.case = case
        self.expected = deepcopy((decision, inventory, owners))
        self.terminals = {}
        self.two_d_terminals = {}
        self.revoked = False
        self.verifiers = []

    def revalidate(self, verify, *, decision, inventory, owners):
        self.case.assertTrue(callable(verify))
        self.verifiers.append(verify)
        if self.revoked or (decision, inventory, owners) != self.expected:
            raise ParentError("fixture-aggregate-proof-rejected")

    def verify_terminal(self, verify, *, object_id, child_workspace, state, artifact):
        # Exact fixture measurements are bound to actual file hashes and the
        # complete child state, not arbitrary --evidence strings in a checklist.
        expected = self.terminals.get(object_id)
        if expected != (sha(canonical(state)), [(f["path"], f["sha256"]) for f in artifact["files"]]):
            raise ParentError("fixture-terminal-measurements-unverified")

    def verify_2d_terminal(self, verify, *, object_id, child_workspace, artifact_path, artifact_bytes, state):
        expected = self.two_d_terminals.get(object_id)
        if (not child_workspace.is_dir()
                or not artifact_path.endswith("/.img2/artifacts/pixi2d/artifact.json")
                or expected != (sha(artifact_bytes), sha(canonical(state)))):
            raise ParentError("fixture-pixi-gate-unverified")


class JournalBoundFixtureAuthority(FixtureAuthority):
    """Fixture aggregate approval plus real journal-authenticated object decisions."""

    def __init__(self, case, decision, inventory, owners, records):
        super().__init__(case, decision, inventory, owners)
        self.records = records
        self.authenticated_subjects = []

    def revalidate(self, verify, *, decision, inventory, owners):
        rows = {row["objectId"]: row for row in decision["objects"]}
        for entry in inventory["objects"]:
            object_id = entry["objectId"]
            record, context = self.records[object_id]
            authenticated = verify(record, context)
            self.authenticated_subjects.append(object_id)
            if (authenticated["group"] != "routing"
                    or authenticated["subject"]["runId"] != decision["runId"]
                    or authenticated["subject"]["objectId"] != object_id
                    or authenticated["subject"]["revision"] != rows[object_id]["revision"]
                    or authenticated["subject"]["passId"] != "routing"):
                raise ParentError("fixture-authenticated-subject-mismatch")
            crops = [binding for binding in authenticated["bindings"] if binding["role"] == "reference-crop"]
            if crops != [entry["reference"]]:
                raise ParentError("fixture-authenticated-crop-mismatch")
            proposal = authenticated["requirements"]["objectProposal"]
            proposals = [binding for binding in authenticated["bindings"] if binding["role"] == "object-proposal"]
            if len(proposals) != 1 or proposals[0]["sha256"] != sha(canonical(proposal)):
                raise ParentError("fixture-authenticated-proposal-mismatch")
            if (proposal["objectId"] != object_id or proposal["approxBox"] != entry["box"]
                    or sha(canonical(entry["box"])) != rows[object_id]["boxSha256"]):
                raise ParentError("fixture-authenticated-proposal-mismatch")
            if (authenticated["answers"]["selection"]["choice"] != entry["ownerRoute"]
                    or rows[object_id]["selected"] != [entry["ownerRoute"]]):
                raise ParentError("fixture-authenticated-owner-mismatch")
            source = [binding for binding in authenticated["bindings"] if binding["role"] == "reference"]
            if len(source) != 1 or source[0]["sha256"] != inventory["imageSha256"]:
                raise ParentError("fixture-authenticated-source-mismatch")
        super().revalidate(verify, decision=decision, inventory=inventory, owners=owners)


class RouteParentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.controller = DecisionController(self.workspace, "fixture-parent-run")
        self.parent_path = self.workspace / ".img2threejs/state.json"
        self.configure()

    def put(self, relative, data):
        path = self.workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"role": "reference-crop", "path": relative, "sha256": sha(data)}

    def configure(self, two_d=False):
        object_ids = ["whole-image"] if two_d else ["left", "right"]
        route = "fixture:pixi2d" if two_d else "base:generic"
        entries, rows = [], []
        for object_id in object_ids:
            binding = self.put("crops/" + object_id + ".png", b"fixture-crop:" + object_id.encode())
            entries.append({"objectId": object_id, "reference": binding, "ownerRoute": route})
            rows.append({"objectId": object_id, "reference": binding, "revision": 1,
                         "status": "resolved", "selected": [route]})
        self.inventory = {"kind": "img2.object-inventory", "version": 1,
                          "imageSha256": "a" * 64, "objects": entries}
        self.decision = {"kind": "img2.route-decision", "version": 1, "phase": "decided",
                         "runId": self.controller.run_id, "revision": 1,
                         "executionAuthorized": False, "imageSha256": "a" * 64,
                         "inventorySha256": sha(canonical(self.inventory)), "objects": rows,
                         "request": {"expectedOutput": "2d" if two_d else "3d"},
                         "skipComposition": two_d}
        self.owners = {route: OwnerProfile("generic", "2d-scene" if two_d else "3d-object")}
        self.receipt = FixtureAuthority(self, self.decision, self.inventory, self.owners)
        self.result = VerifiedAggregate(self.decision, self.inventory, self.owners, self.receipt)
        self.parent = RouteParent(self.controller, verify_aggregate=lambda verify: self.result,
                                  consent=lambda: None)

    def authenticate_subjects(self, *, route_card=None):
        """Exercise the held controller verifier, not a JSON-only approval."""
        reference = self.put("full-reference.png", b"fixture full source image")
        reference["role"] = "reference"
        self.inventory["imageSha256"] = reference["sha256"]
        self.decision["imageSha256"] = reference["sha256"]
        grant = {"granted": True, "purposes": ["routing", "build-strategy", "quality-review", "repair"],
                 "sha256": "b" * 64, "tokenBudget": 10000}
        self.consent = lambda: deepcopy(grant)
        records = {}
        card_binding = None
        if route_card is not None:
            card_binding = self.put("route-card.json", canonical(route_card))
            card_binding["role"] = "route-card"
            self.decision["versions"] = {
                "cards": {entry["ownerRoute"]: card_binding["sha256"] for entry in self.inventory["objects"]}}

        def advisory(context):
            route = context["candidates"][0]["id"]
            return {"kind": "img2.decision-advisory", "version": 1, "group": "routing",
                    "subject": context["subject"], "contextDigest": sha(canonical(context)),
                    "model": context["requestedModel"],
                    "answers": {"selection": {"type": "choice", "choice": route,
                        "probabilities": {route: 1.0}, "confidence": .9},
                        "anyFits": {"type": "noul", "noul": 1.0}},
                    "usage": {"input_tokens": 1, "output_tokens": 1}}

        for index, entry in enumerate(self.inventory["objects"]):
            object_id = entry["objectId"]
            entry["box"] = ([.1, .1, .4, .9] if index == 0 else [.6, .1, .9, .9])
            row = next(row for row in self.decision["objects"] if row["objectId"] == object_id)
            row["boxSha256"] = sha(canonical(entry["box"]))
            proposal = {"objectId": object_id, "label": object_id, "role": "subject", "approxBox": entry["box"]}
            proposal_binding = self.put(f"proposals/{object_id}.json", canonical(proposal))
            proposal_binding["role"] = "object-proposal"
            context = {"kind": "img2.route-context", "version": 1, "group": "routing",
                       "subject": {"runId": self.controller.run_id, "objectId": object_id,
                                   "revision": row["revision"], "passId": "routing"},
                       "bindings": [reference, proposal_binding, entry["reference"]],
                       "candidates": [{"id": entry["ownerRoute"]}], "observations": {},
                       "requirements": {"criticalFeatures": [], "requiredViews": [], "objectProposal": proposal},
                       "policyVersion": "fixture-policy-v1", "questionSetVersion": "route-questions-v2",
                       "requestedModel": "jev-1.14.0"}
            if card_binding is not None:
                context["bindings"].append(card_binding)
                context["candidates"][0]["outputKind"] = route_card["outputKind"]
            record = self.controller.consult(
                context, provider=advisory, availability=lambda: {"status": "present"},
                consent=self.consent, request_token_limit=64)
            records[object_id] = (record, context)
        self.decision["inventorySha256"] = sha(canonical(self.inventory))
        self.receipt = JournalBoundFixtureAuthority(
            self, self.decision, self.inventory, self.owners, records)
        self.result = VerifiedAggregate(self.decision, self.inventory, self.owners, self.receipt)
        self.parent = RouteParent(self.controller, verify_aggregate=lambda verify: self.result,
                                  consent=self.consent)

    def assert_independent_identical_crops(self):
        parent = self.parent.create()
        left, right = parent["objects"]
        self.assertEqual(left["cropSha256"], right["cropSha256"])
        self.assertNotEqual(left["objectId"], right["objectId"])
        self.assertNotEqual(left["statePath"], right["statePath"])
        self.assertEqual(self.receipt.authenticated_subjects, ["left", "right"])
        self.assertNotEqual(self.receipt.records["left"][0]["recordId"],
                            self.receipt.records["right"][0]["recordId"])
        left_state = self.read(left["statePath"])
        right_original = (self.workspace / right["statePath"]).read_bytes()
        left_crop = self.workspace / Path(left["statePath"]).parent / left_state["artifacts"]["reference"]
        right_crop = (self.workspace / Path(right["statePath"]).parent
                      / self.read(right["statePath"])["artifacts"]["reference"])
        self.assertEqual(left_crop.read_bytes(), right_crop.read_bytes())
        self.assertNotEqual((left_crop.stat().st_dev, left_crop.stat().st_ino),
                            (right_crop.stat().st_dev, right_crop.stat().st_ino))
        source = self.workspace / left["reference"]["path"]
        original_crop = source.read_bytes()
        self.assertNotEqual((left_crop.stat().st_dev, left_crop.stat().st_ino),
                            (source.stat().st_dev, source.stat().st_ino))
        left_crop.write_bytes(b"left child local edit")
        left_state["loops"]["total"] = 1
        self.put(left["statePath"], canonical(left_state))
        self.assertEqual(right_crop.read_bytes(), original_crop)
        self.assertEqual(source.read_bytes(), original_crop)
        self.assertEqual((self.workspace / right["statePath"]).read_bytes(), right_original)

    def configure_cs2_route(self, *, terminal_kind="3d-object"):
        """Register a declarative fixture; never execute/import plugin code."""
        home = self.workspace / "fixture-img2-home"
        plugin = home / "plugins" / "cs2"
        plugin.mkdir(parents=True)
        declaration = {
            "id": "cs2",
            "setupSteps": [
                ["cs2-contract-read", "Read {plugin_dir}/grimoire/intake/cs2_intake_contract.md completely"],
                ["cs2-authoritative-classification", "Obtain an authoritative CS2 family/subtype classification record"],
                ["cs2-manifest", "python3 {plugin_dir}/tools/cs2_manifest.py {reference}"
                 " --classification classification.json --admission admission.json --probe probe.json --out cs2-intake.json"],
                ["cs2-spec-augmentation", "python3 {plugin_dir}/tools/emit_spec_augmentation.py"
                 " --manifest cs2-intake.json --out spec-augmentation.json"],
            ],
            "setupAnchorBefore": "local-spec-search",
            "passSteps": [["cs2-review", "python3 {plugin_dir}/tools/cs2_review.py"
                           " --manifest cs2-intake.json --metrics cs2-review-inputs.json"
                           " --scene {plugin_dir}/tests/fixtures/knife_review_scene.json"
                           " --out cs2-review.json --allow-deferrals"]],
            "passAnchorBefore": "ai-review-recorded",
            "specCollection": "cs2",
        }
        (plugin / "domain.json").write_bytes(canonical(declaration))
        (home / "plugins.json").write_bytes(canonical({"version": 1, "plugins": [{"id": "cs2"}]}))
        environment = patch.dict(os.environ, {"IMG2_HOME": str(home)})
        environment.start()
        self.addCleanup(environment.stop)
        route = "profile:cs2"
        for entry, row in zip(self.inventory["objects"], self.decision["objects"]):
            entry["ownerRoute"] = route
            row["selected"] = [route]
        self.owners.clear()
        self.owners[route] = OwnerProfile("cs2", terminal_kind)
        card = {"kind": "img2.route-card", "version": 1, "outputKind": "spec-augmentation",
                "requires": {}, "notFor": {}, "costClass": "local",
                "deps": [{"probe": "python3>=3.10"}],
                "display": {"appliesTo": "CS2 profile consumes augmentation inside a base 3D child.",
                            "notFor": "Unclassified look-alikes; independent authoritative classification can veto."}}
        self.authenticate_subjects(route_card=card)
        return route, card, plugin

    def test_approved_cs2_augmentation_route_initializes_base_three_d_child(self):
        route, card, plugin = self.configure_cs2_route()
        parent = self.parent.create()
        descriptor = parent["objects"][0]
        state = self.read(descriptor["statePath"])
        routed_record = self.receipt.records[descriptor["objectId"]][0]
        self.assertEqual(card["outputKind"], "spec-augmentation")
        self.assertEqual(routed_record["candidates"][0]["outputKind"], "spec-augmentation")
        self.assertEqual(self.owners[route], OwnerProfile("cs2", "3d-object"))
        self.assertEqual(descriptor["profile"], "cs2")
        self.assertEqual(descriptor["outputKind"], "3d-object")
        self.assertEqual(state["profile"], "cs2")
        self.assertEqual(state["routeChild"]["ownerRoute"], route)
        self.assertEqual(state["routeChild"]["outputKind"], "3d-object")
        self.assertEqual(state["artifacts"]["spec"], "object-sculpt-spec.json")
        self.assertTrue(descriptor["artifactPath"].endswith("/artifact.json"))
        steps = {row["id"]: row for row in state["checklist"]}
        ids = [row["id"] for row in state["checklist"]]
        self.assertLess(ids.index("cs2-authoritative-classification"), ids.index("cs2-spec-augmentation"))
        self.assertLess(ids.index("cs2-spec-augmentation"), ids.index("spec-authoring"))
        self.assertLess(ids.index("cs2-review"), ids.index("ai-review-recorded"))
        self.assertIn(str(plugin), steps["cs2-spec-augmentation"]["command"])
        for step_id in ("cs2-authoritative-classification", "cs2-review",
                        "pass-gate-check", "part-coverage", "action-ready", "plugin-gates"):
            self.assertEqual(steps[step_id]["status"], "pending")
        self.reject("child-terminal-gates-incomplete",
                    lambda: self.parent.complete_child(descriptor["objectId"]))
        self.assertEqual(self.read(".img2threejs/state.json")["checklist"][0]["status"], "pending")

    def test_cs2_profile_or_terminal_mapping_cannot_be_changed_after_approval(self):
        route, _, _ = self.configure_cs2_route()
        for owner in (OwnerProfile("generic", "3d-object"), OwnerProfile("cs2", "rigged-3d"),
                      OwnerProfile("cs2", "spec-augmentation")):
            with self.subTest(owner=owner):
                self.owners[route] = owner
                self.reject("fixture-aggregate-proof-rejected", self.parent.create)
        self.owners.clear()
        self.reject("fixture-aggregate-proof-rejected", self.parent.create)
        self.assertFalse(self.parent_path.exists())
        self.assertFalse((self.workspace / ".img2threejs/objects").exists())

    def test_augmentation_can_never_be_an_approved_terminal_output_kind(self):
        self.configure_cs2_route(terminal_kind="spec-augmentation")
        # Even a fixture receipt approving this malformed mapping cannot expand
        # the parent's hard terminal artifact-kind set.
        self.reject("owner-output-mismatch", self.parent.create)
        self.assertFalse(self.parent_path.exists())

    def test_cs2_child_still_requires_independent_measured_terminal_gates(self):
        self.configure_cs2_route()
        parent = self.parent.create()
        descriptor = parent["objects"][0]
        self.terminal(descriptor, approve=False)
        self.reject("fixture-terminal-measurements-unverified",
                    lambda: self.parent.complete_child(descriptor["objectId"]))
        self.assertEqual(self.read(".img2threejs/state.json")["checklist"][0]["status"], "pending")

    def configure_interior_route(self, *, scene_object_ids=("left",)):
        home = self.workspace / "fixture-interior-home"
        plugin = home / "plugins" / "interior"
        plugin.mkdir(parents=True)
        (plugin / "domain.json").write_bytes(canonical({"id": "interior"}))
        (home / "plugins.json").write_bytes(canonical({"plugins": [{"id": "interior"}]}))
        environment = patch.dict(os.environ, {"IMG2_HOME": str(home)})
        environment.start()
        self.addCleanup(environment.stop)
        route = "profile:interior"
        for entry, row in zip(self.inventory["objects"], self.decision["objects"]):
            if entry["objectId"] in scene_object_ids:
                entry["ownerRoute"] = route
                row["selected"] = [route]
        self.owners[route] = OwnerProfile("interior", "3d-scene")
        self.authenticate_subjects()

    def interior_terminal(self, descriptor, *, approve=True):
        """Protocol fixture only: no native admission, geometry or visual approval."""
        state, artifact = self.terminal(descriptor, approve=False)
        directory = descriptor["statePath"].rsplit("/", 1)[0]
        excluded = state["routeChild"]["excludedObjectIds"]
        intake = {"schemaVersion": 1, "sourceKind": "photo",
                  "authorization": "fixture-owned reference",
                  "scale": {"unit": "m", "basis": "authored-estimate", "note": "fixture only"},
                  "components": ["room-shell", *excluded], "excludeComponents": excluded,
                  "unknowns": ["native geometry and visual quality unmeasured"],
                  "interactionRequired": False}
        spec = {"routing": {"objectId": descriptor["objectId"]}, "excludeComponents": excluded}
        self.put(directory + "/.img2/artifacts/interior/intake.json", canonical(intake))
        self.put(directory + "/scene-spec.json", canonical(spec))
        # Match the existing D18 container-only fixture convention, not a room.
        payload = canonical({"asset": {"version": "2.0"}})
        payload += b" " * (-len(payload) % 4)
        glb = (struct.pack("<4sII", b"glTF", 2, 20 + len(payload))
               + struct.pack("<II", len(payload), 0x4E4F534A) + payload)
        model = self.put(directory + "/protocol.glb", glb)
        artifact.update(outputKind="3d-scene", excludeComponents=excluded,
                        roomDimensions={"width": 4, "height": 3, "depth": 5},
                        files=[{"path": "protocol.glb", "sha256": model["sha256"]}])
        self.put(descriptor["artifactPath"], canonical(artifact))
        if approve:
            self.receipt.terminals[descriptor["objectId"]] = (
                sha(canonical(state)), [("protocol.glb", model["sha256"])])
        return state, artifact

    def test_interior_child_uses_native_workflow_and_requires_exclusion_mapping(self):
        self.configure_interior_route()
        parent = self.parent.create()
        scene, prop = parent["objects"]
        state = self.read(scene["statePath"])
        self.assertEqual(scene["profile"], "interior")
        self.assertEqual(scene["outputKind"], "3d-scene")
        self.assertEqual(state["artifacts"]["spec"], "scene-spec.json")
        self.assertEqual(state["currentStep"], "interior-intake")
        self.assertEqual(state["currentPass"], "complete")
        self.assertEqual([step["id"] for step in state["checklist"]], [
            "interior-intake", "interior-route-exclusions", "interior-workflow-init",
            "interior-author-and-review", "interior-export-and-audit", "interior-delivery-gates"])
        self.assertIn("excludeComponents", state["checklist"][1]["command"])
        self.assertEqual(state["routeChild"]["excludedObjectIds"], ["right"])
        self.assertEqual(scene["excludedObjectIds"], ["right"])
        self.assertIn("identical objectId", state["checklist"][1]["command"])
        self.assertEqual(prop["profile"], "generic")
        self.assertIn("build-current-pass", [s["id"] for s in self.read(prop["statePath"])["checklist"]])
        self.assertNotIn("excludedObjectIds", prop)
        self.assertNotIn("excludedObjectIds", self.read(prop["statePath"])["routeChild"])

    def test_interior_child_cannot_skip_exclusions_or_change_native_workflow(self):
        self.configure_interior_route()
        descriptor = self.parent.create()["objects"][0]
        state = self.read(descriptor["statePath"])
        for step in state["checklist"]:
            step.update(status="done", evidence=["fixture-only"])
        state.update(status="complete", currentStep="complete", currentPass="complete")
        state["checklist"][1]["status"] = "skipped"
        state["checklist"][1]["reason"] = "fixture skip"
        self.put(descriptor["statePath"], canonical(state))
        self.reject("child-terminal-gates-incomplete",
                    lambda: self.parent.complete_child(descriptor["objectId"]))
        state["checklist"][1]["status"] = "done"
        state["checklist"][1]["command"] = "skip the real exclusions"
        self.put(descriptor["statePath"], canonical(state))
        self.reject("child-state-mismatch",
                    lambda: self.parent.complete_child(descriptor["objectId"]))

    def test_interior_exclusions_are_sorted_from_other_authenticated_objects(self):
        for object_id in ("z-prop", "a-prop"):
            binding = self.put("crops/" + object_id + ".png", object_id.encode())
            self.inventory["objects"].append({
                "objectId": object_id, "reference": binding, "ownerRoute": "base:generic"})
            self.decision["objects"].append({
                "objectId": object_id, "reference": binding, "revision": 1,
                "status": "resolved", "selected": ["base:generic"]})
        self.configure_interior_route()
        parent = self.parent.create()
        scene = parent["objects"][0]
        self.assertEqual(self.read(scene["statePath"])["routeChild"]["excludedObjectIds"],
                         ["a-prop", "right", "z-prop"])
        self.assertEqual(self.receipt.authenticated_subjects, ["left", "right", "z-prop", "a-prop"])
        for prop in parent["objects"][1:]:
            state = self.read(prop["statePath"])
            self.assertNotIn("excludedObjectIds", state["routeChild"])
            self.assertEqual(state["checklist"], new_state(
                state["artifacts"]["reference"], profile="generic")["checklist"])
        self.interior_terminal(scene)
        directory = scene["statePath"].rsplit("/", 1)[0]
        for path in ("/.img2/artifacts/interior/intake.json", "/scene-spec.json"):
            doc = self.read(directory + path)
            original = deepcopy(doc)
            doc["excludeComponents"].reverse()
            self.put(directory + path, canonical(doc))
            self.reject("interior-exclusion-binding-mismatch",
                        lambda: self.parent.complete_child(scene["objectId"]))
            self.put(directory + path, canonical(original))

    def test_multiple_interior_containers_are_rejected_before_publication(self):
        self.configure_interior_route(scene_object_ids=("left", "right"))
        self.reject("multiple-containers", self.parent.create)
        self.assertFalse(self.parent_path.exists())
        self.assertFalse((self.workspace / ".img2threejs/objects").exists())

    def test_interior_identity_handoff_completes_only_with_independent_fixture_authority(self):
        self.configure_interior_route()
        scene, prop = self.parent.create()["objects"]
        state, artifact = self.interior_terminal(scene, approve=False)
        directory = scene["statePath"].rsplit("/", 1)[0]
        intake = self.read(directory + "/.img2/artifacts/interior/intake.json")
        spec = self.read(directory + "/scene-spec.json")
        self.assertEqual(intake["excludeComponents"], ["right"])
        self.assertEqual(spec["excludeComponents"], ["right"])
        self.assertIn("right", intake["components"])
        original = self.parent_path.read_bytes()
        self.reject("fixture-terminal-measurements-unverified",
                    lambda: self.parent.complete_child(scene["objectId"]))
        self.assertEqual(self.parent_path.read_bytes(), original)
        self.receipt.terminals[scene["objectId"]] = (
            sha(canonical(state)), [(row["path"], row["sha256"]) for row in artifact["files"]])
        parent = self.parent.complete_child(scene["objectId"])
        self.assertEqual(parent["currentStep"], "object:right")
        self.terminal(prop)
        parent = self.parent.complete_child(prop["objectId"])
        self.assertEqual(parent["currentStep"], "composition")
        self.assertEqual(parent["status"], "active")

    def test_interior_without_other_objects_requires_explicit_empty_bindings(self):
        self.inventory["objects"] = self.inventory["objects"][:1]
        self.decision["objects"] = self.decision["objects"][:1]
        self.configure_interior_route()
        scene = self.parent.create()["objects"][0]
        self.assertEqual(scene["excludedObjectIds"], [])
        self.interior_terminal(scene)
        directory = scene["statePath"].rsplit("/", 1)[0]
        for path in ("/.img2/artifacts/interior/intake.json", "/scene-spec.json"):
            doc = self.read(directory + path)
            original = deepcopy(doc)
            del doc["excludeComponents"]
            self.put(directory + path, canonical(doc))
            self.reject("interior-exclusion-binding-mismatch",
                        lambda: self.parent.complete_child(scene["objectId"]))
            self.put(directory + path, canonical(original))
        parent = self.parent.complete_child(scene["objectId"])
        self.assertEqual(parent["currentStep"], "composition")
        self.assertEqual(parent["status"], "active")

    def test_interior_missing_malformed_or_stale_admitted_bindings_are_rejected(self):
        self.configure_interior_route()
        scene = self.parent.create()["objects"][0]
        self.interior_terminal(scene)
        directory = scene["statePath"].rsplit("/", 1)[0]
        paths = [directory + "/.img2/artifacts/interior/intake.json",
                 directory + "/scene-spec.json"]
        originals = {path: (self.workspace / path).read_bytes() for path in paths}
        parent_bytes = self.parent_path.read_bytes()
        with patch.object(self.receipt, "verify_terminal",
                          wraps=self.receipt.verify_terminal) as terminal_gate:
            for path in paths:
                invalid = [None, b"{", canonical([])]
                doc = json.loads(originals[path])
                for value in (None, [], ["stale-object"], ["right", "right"], "right",
                              ["room-shell", "right"], [False], [{}]):
                    invalid.append(canonical({**doc, "excludeComponents": value}))
                missing = deepcopy(doc)
                del missing["excludeComponents"]
                invalid.append(canonical(missing))
                for data in invalid:
                    with self.subTest(path=path, data=data):
                        if data is None:
                            (self.workspace / path).unlink()
                        else:
                            self.put(path, data)
                        self.reject("interior-exclusion-binding-mismatch",
                                    lambda: self.parent.complete_child(scene["objectId"]))
                        self.assertEqual(self.parent_path.read_bytes(), parent_bytes)
                        terminal_gate.assert_not_called()
                        self.put(path, originals[path])
            intake = json.loads(originals[paths[0]])
            for components in (None, [], ["room-shell"], "right", ["right", "right"],
                               ["right", {}], ["right", ""]):
                with self.subTest(components=components):
                    self.put(paths[0], canonical({**intake, "components": components}))
                    self.reject("interior-exclusion-binding-mismatch",
                                lambda: self.parent.complete_child(scene["objectId"]))
                    terminal_gate.assert_not_called()
            self.put(paths[0], originals[paths[0]])
            spec = json.loads(originals[paths[1]])
            for routing in (None, {}, {"objectId": "another-room"}):
                with self.subTest(routing=routing):
                    self.put(paths[1], canonical({**spec, "routing": routing}))
                    self.reject("interior-exclusion-binding-mismatch",
                                lambda: self.parent.complete_child(scene["objectId"]))
                    terminal_gate.assert_not_called()
            self.put(paths[1], originals[paths[1]])
            artifact = self.read(scene["artifactPath"])
            for exclusions in (None, [], ["other"]):
                with self.subTest(artifact_exclusions=exclusions):
                    self.put(scene["artifactPath"], canonical({**artifact, "excludeComponents": exclusions}))
                    self.reject("interior-exclusion-binding-mismatch",
                                lambda: self.parent.complete_child(scene["objectId"]))
                    terminal_gate.assert_not_called()

    def test_interior_child_exclusion_list_is_not_an_authority(self):
        self.configure_interior_route()
        scene = self.parent.create()["objects"][0]
        original, _ = self.interior_terminal(scene)
        with patch.object(self.receipt, "verify_terminal",
                          wraps=self.receipt.verify_terminal) as terminal_gate:
            for value in (None, [], ["room-shell"], ["right", "right"], "right"):
                with self.subTest(excluded=value):
                    state = deepcopy(original)
                    state["routeChild"]["excludedObjectIds"] = value
                    self.put(scene["statePath"], canonical(state))
                    self.reject("child-state-mismatch",
                                lambda: self.parent.complete_child(scene["objectId"]))
                    terminal_gate.assert_not_called()
            state = deepcopy(original)
            del state["routeChild"]["excludedObjectIds"]
            self.put(scene["statePath"], canonical(state))
            self.reject("child-state-mismatch",
                        lambda: self.parent.complete_child(scene["objectId"]))
            terminal_gate.assert_not_called()

    def test_rewriting_all_local_exclusions_cannot_change_authenticated_plan(self):
        self.configure_interior_route()
        scene = self.parent.create()["objects"][0]
        state, _ = self.interior_terminal(scene)
        state["routeChild"]["excludedObjectIds"] = []
        self.put(scene["statePath"], canonical(state))
        parent = self.read(".img2threejs/state.json")
        parent["objects"][0]["excludedObjectIds"] = []
        self.put(".img2threejs/state.json", canonical(parent))
        directory = scene["statePath"].rsplit("/", 1)[0]
        for path in ("/.img2/artifacts/interior/intake.json", "/scene-spec.json", "/artifact.json"):
            doc = self.read(directory + path)
            doc["excludeComponents"] = []
            self.put(directory + path, canonical(doc))
        with patch.object(self.receipt, "verify_terminal",
                          wraps=self.receipt.verify_terminal) as terminal_gate:
            self.reject("stale-parent", lambda: self.parent.complete_child(scene["objectId"]))
            terminal_gate.assert_not_called()
        self.assertEqual(self.receipt.authenticated_subjects, ["left", "right", "left", "right"])

    def test_completed_interior_bindings_are_rechecked_when_another_child_completes(self):
        self.configure_interior_route()
        scene, prop = self.parent.create()["objects"]
        self.interior_terminal(scene)
        self.parent.complete_child(scene["objectId"])
        self.terminal(prop)
        path = scene["statePath"].rsplit("/", 1)[0] + "/scene-spec.json"
        spec = self.read(path)
        spec["excludeComponents"] = []
        self.put(path, canonical(spec))
        original_parent = self.parent_path.read_bytes()
        self.reject("interior-exclusion-binding-mismatch",
                    lambda: self.parent.complete_child(prop["objectId"]))
        self.assertEqual(self.parent_path.read_bytes(), original_parent)

    def test_cs2_augmentation_manifest_is_not_a_terminal_three_d_artifact(self):
        self.configure_cs2_route()
        parent = self.parent.create()
        descriptor = parent["objects"][0]
        _, artifact = self.terminal(descriptor, approve=False)
        artifact.update(kind="spec-augmentation-v1", outputKind="spec-augmentation",
                        provenance={"provider": "cs2", "version": "fixture"})
        self.put(descriptor["artifactPath"], canonical(artifact))
        with self.assertRaises(ArtifactError) as raised:
            self.parent.complete_child(descriptor["objectId"])
        self.assertEqual(raised.exception.reason_code, "unsupported-output-kind")
        self.assertEqual(self.read(".img2threejs/state.json")["checklist"][0]["status"], "pending")

    def read(self, relative):
        return json.loads((self.workspace / relative).read_bytes())

    def reject(self, reason, operation):
        with self.assertRaises(ParentError) as raised:
            operation()
        self.assertEqual(raised.exception.reason_code, reason)

    def terminal(self, descriptor, *, approve=True):
        state = self.read(descriptor["statePath"])
        state.update(status="complete", currentStep="complete", currentPass="complete")
        for step in state["checklist"]:
            step.update(status="done", evidence=["arbitrary-checklist-text"])
        self.put(descriptor["statePath"], canonical(state))
        model = self.put(descriptor["statePath"].rsplit("/", 1)[0] + "/model.ts",
                         b"export function createObjectModel() { return {}; }\n")
        artifact = {"objectId": descriptor["objectId"], "outputKind": "3d-object",
                    "files": [{"path": "model.ts", "sha256": model["sha256"]}],
                    "frame": {"units": "m", "up": "+Y", "forward": "+Z"},
                    "pivot": "bottom-centre", "scaleEstimate": 1, "rig": None}
        self.put(descriptor["artifactPath"], canonical(artifact))
        if approve:
            self.receipt.terminals[descriptor["objectId"]] = (
                sha(canonical(state)), [("model.ts", model["sha256"])])
        return state, artifact

    def test_bare_or_forged_decision_never_authorizes_parent(self):
        self.reject("aggregate-verifier-required", lambda: RouteParent(
            self.controller, verify_aggregate=self.decision, consent=lambda: None))
        self.result = self.decision
        self.reject("inventory-completeness-unverified", self.parent.create)
        self.assertFalse(self.parent_path.exists())
        self.result = VerifiedAggregate(self.decision, self.inventory, self.owners,
                                        {"authenticated": True, "sha256": "f" * 64})
        self.reject("inventory-completeness-unverified", self.parent.create)
        self.assertFalse(self.parent_path.exists())

    def test_provider_execution_flag_is_not_authority(self):
        self.decision["executionAuthorized"] = True
        self.reject("invalid-route-decision", self.parent.create)
        self.assertFalse(self.parent_path.exists())

    def test_receipt_is_not_serializable_and_transaction_verifier_expires(self):
        self.parent.create()
        with self.assertRaises(TypeError):
            pickle.dumps(self.receipt)
        with self.assertRaises(DecisionError) as raised:
            self.receipt.verifiers[-1]({}, {})
        self.assertEqual(raised.exception.reason_code, "transaction-closed")

    def test_two_same_route_children_have_independent_state_and_crops(self):
        parent = self.parent.create()
        left, right = parent["objects"]
        self.assertEqual(left["ownerRoute"], right["ownerRoute"])
        self.assertNotEqual(left["statePath"], right["statePath"])
        self.assertNotEqual(left["cropSha256"], right["cropSha256"])
        original = (self.workspace / right["statePath"]).read_bytes()
        state = self.read(left["statePath"])
        state["loops"]["total"] = 2
        state["checklist"][0]["status"] = "done"
        self.put(left["statePath"], canonical(state))
        self.assertEqual((self.workspace / right["statePath"]).read_bytes(), original)
        self.assertEqual([row["id"] for row in parent["checklist"]],
                         ["object:left", "object:right", "composition", "scene-gates"])
        self.assertEqual(parent["currentStep"], "object:left")
        self.assertEqual(self.read(right["statePath"])["profile"], "generic")

    def test_stale_crop_prevents_creation_without_partial_states(self):
        self.put("crops/right.png", b"changed-reference")
        self.reject("stale-reference-crop", self.parent.create)
        self.assertFalse(self.parent_path.exists())
        self.assertFalse((self.workspace / ".img2threejs/objects").exists())

    def test_identical_crops_at_distinct_paths_create_independent_children(self):
        right = self.inventory["objects"][1]["reference"]
        data = (self.workspace / self.inventory["objects"][0]["reference"]["path"]).read_bytes()
        self.put(right["path"], data)
        right["sha256"] = sha(data)
        self.authenticate_subjects()
        self.assert_independent_identical_crops()

    def test_shared_content_addressed_crop_creates_independent_children(self):
        shared = self.inventory["objects"][0]["reference"]
        self.inventory["objects"][1]["reference"] = deepcopy(shared)
        self.decision["objects"][1]["reference"] = deepcopy(shared)
        self.authenticate_subjects()
        self.assert_independent_identical_crops()

    def test_shared_crop_inode_is_authenticated_separately_per_subject(self):
        right = self.inventory["objects"][1]["reference"]
        right["sha256"] = self.inventory["objects"][0]["reference"]["sha256"]
        (self.workspace / right["path"]).unlink()
        os.link(self.workspace / "crops/left.png", self.workspace / right["path"])
        self.authenticate_subjects()
        self.assert_independent_identical_crops()

    def test_shared_crop_bytes_are_revalidated_under_authenticated_receipt(self):
        shared = self.inventory["objects"][0]["reference"]
        self.inventory["objects"][1]["reference"] = deepcopy(shared)
        self.decision["objects"][1]["reference"] = deepcopy(shared)
        self.authenticate_subjects()
        (self.workspace / shared["path"]).write_bytes(b"replaced shared crop")
        with self.assertRaises(DecisionError) as raised:
            self.parent.create()
        self.assertEqual(raised.exception.reason_code, "stale-evidence")
        self.assertFalse(self.parent_path.exists())
        self.assertFalse((self.workspace / ".img2threejs/objects").exists())

    def test_unrelated_crop_cannot_borrow_another_subject_approval(self):
        self.authenticate_subjects()
        shared = deepcopy(self.inventory["objects"][0]["reference"])
        self.inventory["objects"][1]["reference"] = shared
        self.decision["objects"][1]["reference"] = deepcopy(shared)
        # Repairing JSON hashes cannot change the independently authenticated
        # right-subject record consumed by the real held controller verifier.
        self.decision["inventorySha256"] = sha(canonical(self.inventory))
        self.reject("fixture-authenticated-crop-mismatch", self.parent.create)
        self.assertEqual(self.receipt.authenticated_subjects, ["left", "right"])
        self.assertFalse(self.parent_path.exists())
        self.assertFalse((self.workspace / ".img2threejs/objects").exists())

    def test_reused_authentic_record_does_not_authorize_a_different_subject(self):
        self.authenticate_subjects()
        self.receipt.records["right"] = self.receipt.records["left"]
        self.reject("fixture-authenticated-subject-mismatch", self.parent.create)
        self.assertFalse(self.parent_path.exists())

    def test_symlink_crop_and_unsafe_object_never_escape_workspace(self):
        crop = self.workspace / "crops/right.png"
        crop.unlink()
        crop.symlink_to(self.workspace / "crops/left.png")
        with self.assertRaises(OSError):
            self.parent.create()
        self.assertFalse(self.parent_path.exists())
        crop.unlink()
        self.put("crops/right.png", b"fixture-crop:right")
        self.inventory["objects"][1]["objectId"] = "../outside"
        self.decision["objects"][1]["objectId"] = "../outside"
        self.decision["inventorySha256"] = sha(canonical(self.inventory))
        self.receipt.expected = deepcopy((self.decision, self.inventory, self.owners))
        self.reject("duplicate-or-unsafe-object", self.parent.create)

    def test_unknown_owner_profile_is_rejected(self):
        self.owners.clear()
        self.receipt.expected = deepcopy((self.decision, self.inventory, self.owners))
        self.reject("unapproved-owner-profile", self.parent.create)
        self.assertFalse(self.parent_path.exists())

    def test_legacy_state_is_untouched(self):
        legacy = b'{"legacy":"preserve bytes exactly"}\n'
        self.put(".img2threejs/state.json", legacy)
        self.reject("existing-parent-preserved", self.parent.create)
        self.assertEqual(self.parent_path.read_bytes(), legacy)
        self.assertFalse((self.workspace / ".img2threejs/objects").exists())

    def test_existing_child_conflict_preserves_all_originals(self):
        original = b'{"incumbent":"not reset"}\n'
        self.put(".img2threejs/objects/right/rev-1/state.json", original)
        self.reject("existing-state-conflict", self.parent.create)
        self.assertEqual((self.workspace / ".img2threejs/objects/right/rev-1/state.json").read_bytes(), original)
        self.assertFalse((self.workspace / ".img2threejs/objects/left").exists())

    def test_terminal_state_flags_and_evidence_alone_are_insufficient(self):
        parent = self.parent.create()
        self.terminal(parent["objects"][0], approve=False)
        original = self.parent_path.read_bytes()
        self.reject("fixture-terminal-measurements-unverified", lambda: self.parent.complete_child("left"))
        self.assertEqual(self.parent_path.read_bytes(), original)

    def test_pending_or_skipped_terminal_gate_blocks_completion(self):
        parent = self.parent.create()
        descriptor = parent["objects"][0]
        state, _ = self.terminal(descriptor)
        gate = next(row for row in state["checklist"] if row["id"] == "plugin-gates")
        for status in ("pending", "skipped"):
            gate["status"] = status
            self.put(descriptor["statePath"], canonical(state))
            self.reject("child-terminal-gates-incomplete", lambda: self.parent.complete_child("left"))
        self.assertEqual(self.read(".img2threejs/state.json")["checklist"][0]["status"], "pending")

    def test_invalid_artifact_bytes_block_completion(self):
        parent = self.parent.create()
        _, artifact = self.terminal(parent["objects"][0])
        original = self.parent_path.read_bytes()
        self.put(parent["objects"][0]["statePath"].rsplit("/", 1)[0] + "/" + artifact["files"][0]["path"],
                 b"tampered model")
        with self.assertRaises(ArtifactError) as raised:
            self.parent.complete_child("left")
        self.assertEqual(raised.exception.reason_code, "hash-mismatch")
        self.assertEqual(self.parent_path.read_bytes(), original)

    def test_copied_reference_staleness_blocks_completion(self):
        parent = self.parent.create()
        state, _ = self.terminal(parent["objects"][0])
        self.put(parent["objects"][0]["statePath"].rsplit("/", 1)[0] + "/" + state["artifacts"]["reference"],
                 b"different copied reference")
        self.reject("stale-reference-crop", lambda: self.parent.complete_child("left"))

    def test_completion_revalidates_aggregate_and_each_completed_child(self):
        parent = self.parent.create()
        self.terminal(parent["objects"][0])
        self.terminal(parent["objects"][1])
        parent = self.parent.complete_child("left")
        self.assertEqual(parent["currentStep"], "object:right")
        self.receipt.revoked = True
        self.reject("fixture-aggregate-proof-rejected", lambda: self.parent.complete_child("right"))
        self.receipt.revoked = False
        parent = self.parent.complete_child("right")
        self.assertEqual(parent["currentStep"], "composition")
        self.assertEqual(parent["status"], "active")
        first, _ = self.terminal(parent["objects"][0])
        first["checklist"][0]["status"] = "pending"
        self.put(parent["objects"][0]["statePath"], canonical(first))
        self.reject("child-terminal-gates-incomplete", lambda: self.parent.complete_child("right"))

    def test_interrupted_creation_reuses_initial_children_without_overwrite(self):
        import route_parent
        write = route_parent._write
        calls = []

        def interrupted(workspace, path, data, **kwargs):
            calls.append(path)
            if len(calls) == 3:
                raise OSError("fixture interruption")
            return write(workspace, path, data, **kwargs)

        with patch("route_parent._write", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.parent.create()
        self.assertFalse(self.parent_path.exists())
        child = self.workspace / ".img2threejs/objects/left/rev-1/state.json"
        original = child.read_bytes()
        parent = self.parent.create()
        self.assertEqual(child.read_bytes(), original)
        self.assertEqual(len(parent["objects"]), 2)

    def test_lease_loss_stops_before_aggregate_callback(self):
        lease_path = ".img2threejs/route/lease.json"
        lease = self.read(lease_path)
        lease["token"] = "0" * 64
        self.put(lease_path, canonical(lease))
        with self.assertRaises(DecisionError) as raised:
            self.parent.create()
        self.assertEqual(raised.exception.reason_code, "lease-lost")
        self.assertFalse(self.receipt.verifiers)

    def complete_pixi_checklist(self, state):
        """Exercise real checklist ordering; these marks are not gate authority."""
        self.assertEqual(state["currentPass"], "complete")
        self.assertEqual(state["passHistory"], [])
        for step in state["checklist"]:
            self.assertEqual(state["currentStep"], step["id"])
            mark_steps(state, [step["id"]], status="done",
                       evidence=["fixture Pixi step progression, not native approval"])
            self.assertNotEqual(state["currentStep"], "await-pass-transition")
        self.assertEqual(state["status"], "complete")
        validate_state(state)

    def test_2d_child_has_exact_actionable_pixi_template_without_sculpt_passes(self):
        self.configure(two_d=True)
        parent = self.parent.create()
        descriptor = parent["objects"][0]
        state = self.read(descriptor["statePath"])
        self.assertEqual([(row["id"], row["scope"]) for row in state["checklist"]], [
            ("pixi2d-snapshot", "setup"),
            ("pixi2d-native-host-capture", "setup"),
            ("pixi2d-evidence-produce", "setup"),
            ("pixi2d-native-visual-review", "setup"),
            ("pixi2d-publish", "setup"),
            ("pixi2d-editable-review", "final"),
        ])
        self.assertEqual(state["currentStep"], "pixi2d-snapshot")
        self.assertEqual(state["currentPass"], "complete")
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["artifacts"]["spec"], "reconstruction.json")
        self.assertEqual(state["artifacts"]["reference"], "reference.png")
        self.assertEqual(state["routeChild"]["outputKind"], "2d-scene")
        self.assertTrue(descriptor["artifactPath"].endswith("/.img2/artifacts/pixi2d/artifact.json"))
        self.assertIn('pixi2d.py" snapshot --workspace .', state["checklist"][0]["command"])
        self.assertIn("review-capture.mjs", state["checklist"][1]["command"])
        self.assertIn('pixi2d.py" produce --workspace .', state["checklist"][2]["command"])
        self.assertIn("native host vision", state["checklist"][3]["command"])
        self.assertIn('pixi2d.py" publish --workspace .', state["checklist"][4]["command"])
        self.assertEqual(state["checklist"][-1]["command"],
                         'python3 "$SKILL_DIR/tools/pixi2d.py" gate --workspace .')
        self.complete_pixi_checklist(state)

    def test_3d_child_keeps_the_existing_generic_workflow(self):
        parent = self.parent.create()
        state = self.read(parent["objects"][0]["statePath"])
        template = new_state(state["artifacts"]["reference"], profile=state["profile"],
                             spec="object-sculpt-spec.json")
        self.assertEqual(state["checklist"], template["checklist"])
        self.assertEqual(state["currentPass"], "")
        self.assertEqual(state["currentStep"], template["currentStep"])

    def test_2d_missing_or_skipped_review_and_gate_cannot_complete(self):
        self.configure(two_d=True)
        parent = self.parent.create()
        descriptor = parent["objects"][0]
        state = self.read(descriptor["statePath"])
        self.complete_pixi_checklist(state)
        original_parent = self.parent_path.read_bytes()
        for step_id in ("pixi2d-native-visual-review", "pixi2d-editable-review"):
            for status in ("pending", "skipped"):
                with self.subTest(step=step_id, status=status):
                    forged = deepcopy(state)
                    next(row for row in forged["checklist"] if row["id"] == step_id)["status"] = status
                    # Leave complete flags forged on purpose: terminal row status
                    # must independently block before any artifact or authority call.
                    self.put(descriptor["statePath"], canonical(forged))
                    self.reject("child-terminal-gates-incomplete",
                                lambda: self.parent.complete_child("whole-image"))
        self.assertEqual(self.parent_path.read_bytes(), original_parent)

    def test_2d_template_cannot_omit_reorder_or_rewrite_native_review(self):
        self.configure(two_d=True)
        parent = self.parent.create()
        descriptor = parent["objects"][0]
        state = self.read(descriptor["statePath"])
        self.complete_pixi_checklist(state)
        omitted = deepcopy(state)
        omitted["checklist"].pop(3)
        reordered = deepcopy(state)
        reordered["checklist"][1], reordered["checklist"][2] = reordered["checklist"][2], reordered["checklist"][1]
        rewritten = deepcopy(state)
        rewritten["checklist"][3]["command"] = "Skip native visual review"
        for forged in (omitted, reordered, rewritten):
            self.put(descriptor["statePath"], canonical(forged))
            self.reject("child-state-mismatch", lambda: self.parent.complete_child("whole-image"))
        self.assertEqual(self.read(".img2threejs/state.json")["status"], "active")

    def test_2d_completion_requires_current_program_owned_pixi_gate(self):
        self.configure(two_d=True)
        parent = self.parent.create()
        descriptor = parent["objects"][0]
        state = self.read(descriptor["statePath"])
        self.complete_pixi_checklist(state)
        self.put(descriptor["statePath"], canonical(state))
        source = self.put(descriptor["statePath"].rsplit("/", 1)[0] + "/scene.ts",
                          b"export const fixtureScene = {};\n")
        artifact = {"kind": "img2.pixi2d-scene", "version": 1, "objectId": "whole-image",
                    "outputKind": "2d-scene",
                    "editableSources": [{"path": "scene.ts", "sha256": source["sha256"]}]}
        artifact_bytes = canonical(artifact)
        self.put(descriptor["artifactPath"], artifact_bytes)
        self.reject("fixture-pixi-gate-unverified", lambda: self.parent.complete_child("whole-image"))
        self.receipt.two_d_terminals["whole-image"] = (sha(artifact_bytes), sha(canonical(state)))
        # Fixture approval exercises the seam, not a real Pixi runner or image gate.
        parent = self.parent.complete_child("whole-image")
        self.assertEqual(parent["status"], "complete")
        self.assertEqual([row["id"] for row in parent["checklist"]], ["object:whole-image"])
        self.put(descriptor["artifactPath"], canonical({**artifact, "changed": True}))
        self.reject("fixture-pixi-gate-unverified", lambda: self.parent.complete_child("whole-image"))

    def test_2d_requires_matching_object_and_editable_source(self):
        self.configure(two_d=True)
        parent = self.parent.create()
        descriptor = parent["objects"][0]
        state = self.read(descriptor["statePath"])
        self.complete_pixi_checklist(state)
        self.put(descriptor["statePath"], canonical(state))
        for object_id, sources in (("foreign-object", [{"path": "scene.ts", "sha256": "a" * 64}]),
                                   ("whole-image", [])):
            self.put(descriptor["artifactPath"], canonical({
                "kind": "img2.pixi2d-scene", "version": 1, "objectId": object_id,
                "outputKind": "2d-scene", "editableSources": sources}))
            self.reject("invalid-2d-artifact", lambda: self.parent.complete_child("whole-image"))
        self.assertEqual(self.read(".img2threejs/state.json")["status"], "active")

    def test_explicit_2d_skips_composition_and_scene_gates(self):
        self.configure(two_d=True)
        parent = self.parent.create()
        self.assertEqual([row["id"] for row in parent["checklist"]], ["object:whole-image"])
        self.assertEqual(parent["currentStep"], "object:whole-image")
        self.assertTrue(parent["routeDecision"]["skipComposition"])
        self.assertEqual(parent["objects"][0]["outputKind"], "2d-scene")


if __name__ == "__main__":
    unittest.main()
