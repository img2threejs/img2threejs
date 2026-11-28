"""The router's inventory must satisfy the composer's structural contract.

Stage zero publishes `img2.object-inventory`; `stage6_compose.compose_scene`
consumes it. These are written by different modules against the same schema, so
nothing but an actual join proves they agree. The composer is expected to stop
at the absent artifacts here: that is the first check PAST inventory structure,
and reaching it is the evidence. Structural agreement is not scene quality.
"""
import hashlib
import json
from pathlib import Path
import sys
import unittest

_FORGE = Path(__file__).resolve().parents[1]
for _entry in (str(_FORGE), str(_FORGE / "_shared"), str(_FORGE / "tests")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import test_stage0_route as _stage0  # noqa: E402
from test_route_parent import FixtureAuthority  # noqa: E402
from route_parent import OwnerProfile, RouteParent, VerifiedAggregate  # noqa: E402
from stage0_route.storage import snapshot  # noqa: E402
from stage6_compose.compose import CompositionError, compose_scene  # noqa: E402


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


class RouteComposeJoinTests(unittest.TestCase):
    def setUp(self):
        self.case = _stage0.Stage0RouteTests(
            "test_instances_route_once_and_keep_independent_overlapping_placements")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.workspace = self.case.workspace

    def routed_inventory(self):
        """Route two instances of one object and return the published inventory."""
        proposals = self.case.proposals(2)
        for obj in proposals["objects"]:
            obj.update(instanceOf="chairs", label="chair")
        decision = self.case.route(objects=proposals)["decision"]
        inventory = json.loads((self.workspace / decision["inventoryRef"]).read_bytes())
        return decision, inventory

    def request(self, inventory_ref):
        reference = self.workspace / "reference.png"
        reference_ref = {"path": "reference.png",
                         "sha256": hashlib.sha256(reference.read_bytes()).hexdigest()}
        placement = {"kind": "img2.scene-placement", "version": 1, "jevSession": "healthy",
                     "objects": [], "inventorySha256": inventory_ref["sha256"],
                     "referenceSha256": reference_ref["sha256"],
                     "camera": {"path": "camera.json", "sha256": "0" * 64},
                     "environment": {"path": "environment.json", "sha256": "0" * 64},
                     "baseContainer": None}
        encoded = canonical(placement)
        (self.workspace / "placement.json").write_bytes(encoded)
        return {"kind": "img2.scene-compose-request", "version": 1, "expectedOutput": "3d",
                "inventory": inventory_ref, "artifacts": [],
                "placement": {"path": "placement.json", "sha256": hashlib.sha256(encoded).hexdigest()},
                "reference": reference_ref}

    def compose(self, inventory_ref, output):
        with self.assertRaises(CompositionError) as raised:
            compose_scene(self.workspace, self.request(inventory_ref), output_dir=output)
        return raised.exception

    def test_routed_inventory_rows_name_the_child_whose_artifact_owns_them(self):
        _, inventory = self.routed_inventory()
        rows = inventory["objects"]
        self.assertEqual([row["objectId"] for row in rows], ["object-0", "object-1"])
        # Only the group leader is built; the second instance is placementOnly and
        # therefore shares the leader's artifact rather than owning its own.
        self.assertEqual([row["artifactObjectId"] for row in rows], ["object-0", "object-0"])
        self.assertEqual(len({row["ownerRoute"] for row in rows}), 1)

    def test_routed_repeated_instances_create_only_the_artifact_owner_child(self):
        decision, inventory = self.routed_inventory()
        owners = {"base:generic": OwnerProfile("generic", "3d-object")}
        receipt = FixtureAuthority(self, decision, inventory, owners)
        aggregate = VerifiedAggregate(decision, inventory, owners, receipt)
        parent = RouteParent(self.case.controller, verify_aggregate=lambda verify: aggregate,
                             consent=self.case.consent).create()
        self.assertEqual([row["objectId"] for row in parent["objects"]], ["object-0"])
        self.assertEqual(parent["objects"][0]["ownedObjectIds"], ["object-0", "object-1"])
        self.assertFalse((self.workspace / ".img2threejs/objects/object-1").exists())

    def test_a_routed_inventory_passes_every_composer_structural_check(self):
        decision, _ = self.routed_inventory()
        reference = {"path": decision["inventoryRef"], "sha256": decision["inventorySha256"]}
        error = self.compose(reference, "composed/routed")
        # Past objectId/role/ownerRoute/artifactObjectId, group ownership, the
        # single-container rule and the shared-instanceOf rule: the only thing
        # left is the artifacts this fixture deliberately does not supply.
        self.assertEqual(error.reason_code, "inventory-mismatch")
        self.assertEqual(error.field, "artifacts")

    def test_an_inventory_without_artifact_ownership_is_refused_as_invalid(self):
        _, inventory = self.routed_inventory()
        for row in inventory["objects"]:
            del row["artifactObjectId"]
        reference = snapshot(self.workspace, "object-inventory", inventory)
        error = self.compose({"path": reference["path"], "sha256": reference["sha256"]},
                             "composed/stripped")
        self.assertEqual(error.reason_code, "invalid-inventory")
        self.assertEqual(error.field, "object-0")

    def test_instances_claiming_separate_artifacts_are_refused(self):
        _, inventory = self.routed_inventory()
        inventory["objects"][1]["artifactObjectId"] = "object-1"
        reference = snapshot(self.workspace, "object-inventory", inventory)
        error = self.compose({"path": reference["path"], "sha256": reference["sha256"]},
                             "composed/forked")
        # object-1 names itself as its own artifact owner, but no such child was
        # built; the composer must not silently accept the second claim.
        self.assertEqual(error.reason_code, "inventory-mismatch")
        self.assertEqual(error.field, "artifacts")

    def test_a_row_owned_by_an_unbuilt_child_is_refused(self):
        _, inventory = self.routed_inventory()
        for row in inventory["objects"]:
            row["artifactObjectId"] = "never-built"
        reference = snapshot(self.workspace, "object-inventory", inventory)
        error = self.compose({"path": reference["path"], "sha256": reference["sha256"]},
                             "composed/ghost")
        self.assertEqual(error.reason_code, "inventory-mismatch")
        self.assertEqual(error.field, "never-built")

    def test_verification_rejects_an_inventory_with_rewritten_ownership(self):
        decision, inventory = self.routed_inventory()
        inventory["objects"][1]["artifactObjectId"] = "object-1"
        reference = snapshot(self.workspace, "object-inventory", inventory)
        forged = json.loads(json.dumps(decision))
        forged["inventoryRef"] = reference["path"]
        forged["inventorySha256"] = reference["sha256"]
        path = ".img2threejs/route/forged.json"
        (self.workspace / path).parent.mkdir(parents=True, exist_ok=True)
        (self.workspace / path).write_bytes(canonical(forged))
        from stage0_route.storage import RouteError
        with self.assertRaises(RouteError) as raised:
            self.case.router().verify_decision(path)
        # Repointing a decision at a rewritten inventory breaks the hash-chained
        # journal first, so ownership never has to be re-argued field by field.
        self.assertEqual(raised.exception.reason_code, "decision-integrity-failed")


if __name__ == "__main__":
    unittest.main()
