"""Executable composition/gate entry point: refusals, bindings and exit codes.

These exercise the real CLI as a subprocess over the composer's own offline
fixtures. A passing gate here is a measured result over fixture measurements,
not evidence of visual reconstruction quality or canonical acceptance.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest

_FORGE = Path(__file__).resolve().parents[1]
if str(_FORGE) not in sys.path:
    sys.path.insert(0, str(_FORGE))
if str(_FORGE / "tests") not in sys.path:
    sys.path.insert(0, str(_FORGE / "tests"))

# Import the module, not its TestCase: a direct class import would make unittest
# discovery re-collect the whole composer suite under this file's name.
import test_scene_composer as _composer  # noqa: E402

canonical = _composer.canonical
_CLI = _FORGE / "compose.py"


def measurement(value, scene_sha, reference_sha):
    return {"value": value, "producer": "offline-fixture-not-production-proof",
            "sceneSha256": scene_sha, "referenceSha256": reference_sha}


class ComposeCliTests(unittest.TestCase):
    def setUp(self):
        self.case = _composer.SceneComposerTests(
            "test_real_skinned_glb_is_retained_and_module_loads_parent_only")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root = Path(self.case.root)
        self.request_path = self.root / "compose-request.json"
        self.request_path.write_bytes(canonical(self.case.request))

    def run_cli(self, *argv):
        result = subprocess.run([sys.executable, str(_CLI), *argv],
                                capture_output=True, text=True, cwd=str(_FORGE.parent))
        return result.returncode, json.loads(result.stdout)

    def compose(self, output="composed/rev-1"):
        return self.run_cli("scene", "--workspace", str(self.root),
                            "--request", str(self.request_path), "--output-dir", output)

    def composed(self):
        code, _ = self.compose()
        self.assertEqual(code, 0)
        manifest = json.loads((self.root / "composed/rev-1/scene.json").read_text())
        return manifest, manifest["module"]["sha256"], manifest["referenceSha256"]

    def measurements(self, scene_sha, reference_sha):
        def value(number):
            return measurement(number, scene_sha, reference_sha)
        return {"sceneSha256": scene_sha, "referenceSha256": reference_sha,
                "objects": [{"id": "subject", "supported": True, "boxIoU": value(0.95),
                             "height": value(1.0), "scale": value(1.0),
                             "scalePrior": {"min": 0.5, "max": 2.0}, "contactGap": value(0.001)}],
                "intersections": [], "silhouetteIoU": value(0.95),
                "interiorDifference": value(0.05), "lightRigCount": value(1)}

    def gates(self, payload, inventory=None):
        path = self.root / "measurements.json"
        path.write_text(json.dumps(payload))
        return self.run_cli("gates", "--workspace", str(self.root),
                            "--composed", str(self.root / "composed/rev-1/scene.json"),
                            "--measurements", str(path),
                            "--inventory", str(inventory or self.root / self.case.request["inventory"]["path"]))

    def test_composition_writes_a_candidate_scene_that_is_never_deliverable(self):
        code, value = self.compose()
        self.assertEqual(code, 0)
        self.assertFalse(value["deliverable"])
        self.assertFalse(value["manifest"]["deliverable"])
        self.assertEqual(value["manifest"]["kind"], "img2.composed-scene")
        self.assertTrue((self.root / "composed/rev-1/scene.mjs").is_file())
        for field in value["manifest"]["validation"].values():
            self.assertEqual(field, "unevaluated")

    def test_explicit_2d_request_bypasses_composition(self):
        self.request_path.write_bytes(canonical({"kind": "img2.scene-compose-request",
                                                 "version": 1, "expectedOutput": "2d"}))
        code, value = self.compose(output="unused")
        self.assertEqual(code, 0)
        self.assertEqual(value["manifest"]["status"], "bypassed")
        self.assertEqual(value["manifest"]["reasonCode"], "explicit-2d")
        self.assertFalse((self.root / "unused").exists())

    def test_composition_refuses_an_existing_output_directory(self):
        self.assertEqual(self.compose()[0], 0)
        code, value = self.compose()
        self.assertEqual(code, 3)
        self.assertEqual(value["reasonCode"], "output-exists")

    def test_composition_refuses_a_tampered_input_digest(self):
        request = deepcopy(self.case.request)
        request["reference"]["sha256"] = "0" * 64
        self.request_path.write_bytes(canonical(request))
        code, value = self.compose()
        self.assertEqual(code, 3)
        self.assertEqual(value["reasonCode"], "hash-mismatch")

    def test_invalid_artifact_reports_json_refusal(self):
        request = deepcopy(self.case.request)
        artifact_ref = request["artifacts"][0]
        artifact_path = self.root / artifact_ref["path"]
        artifact = json.loads(artifact_path.read_bytes())
        del artifact["frame"]
        artifact_ref["sha256"] = self.case.put(artifact_ref["path"], artifact)["sha256"]
        self.request_path.write_bytes(canonical(request))
        code, value = self.compose()
        self.assertEqual((code, value["reasonCode"]), (3, "missing-evidence"))

    def test_unreadable_and_malformed_requests_are_usage_refusals(self):
        code, value = self.run_cli("scene", "--workspace", str(self.root),
                                   "--request", str(self.root / "absent.json"), "--output-dir", "x")
        self.assertEqual((code, value["reasonCode"]), (2, "unreadable-input"))
        self.request_path.write_bytes(b"{not json")
        code, value = self.compose()
        self.assertEqual((code, value["reasonCode"]), (2, "invalid-json"))
        self.request_path.write_bytes(b"[]")
        code, value = self.compose()
        self.assertEqual((code, value["reasonCode"]), (2, "invalid-record"))

    def test_measured_gates_pass_without_granting_delivery(self):
        _, scene_sha, reference_sha = self.composed()
        code, value = self.gates(self.measurements(scene_sha, reference_sha))
        self.assertEqual(code, 0)
        self.assertEqual(value["status"], "pass")
        self.assertFalse(value["deliverable"])
        self.assertTrue(value["result"]["deliverable"])
        self.assertIn("object:subject:box-iou", [check["id"] for check in value["result"]["checks"]])

    def test_a_failing_measurement_fails_the_gate(self):
        _, scene_sha, reference_sha = self.composed()
        payload = self.measurements(scene_sha, reference_sha)
        payload["objects"][0]["boxIoU"]["value"] = 0.1
        code, value = self.gates(payload)
        self.assertEqual(code, 3)
        self.assertEqual(value["status"], "fail")
        self.assertFalse(value["result"]["deliverable"])

    def test_changed_scene_module_is_refused(self):
        manifest, scene_sha, reference_sha = self.composed()
        (self.root / manifest["module"]["path"]).write_bytes(b"changed module")
        code, value = self.gates(self.measurements(scene_sha, reference_sha))
        self.assertEqual((code, value["reasonCode"]), (3, "stale-scene-module"))

    def test_changed_reference_bytes_are_refused(self):
        _, scene_sha, reference_sha = self.composed()
        (self.root / self.case.request["reference"]["path"]).write_bytes(b"changed reference")
        code, value = self.gates(self.measurements(scene_sha, reference_sha))
        self.assertEqual((code, value["reasonCode"]), (3, "stale-scene-input"))

    def test_measurements_bound_to_another_scene_are_refused(self):
        _, scene_sha, reference_sha = self.composed()
        payload = self.measurements(scene_sha, reference_sha)
        payload["sceneSha256"] = "0" * 64
        code, value = self.gates(payload)
        self.assertEqual(code, 3)
        self.assertEqual(value["reasonCode"], "stale-scene-measurement")

    def test_measurements_bound_to_another_reference_are_refused(self):
        _, scene_sha, reference_sha = self.composed()
        payload = self.measurements(scene_sha, reference_sha)
        payload["referenceSha256"] = "1" * 64
        code, value = self.gates(payload)
        self.assertEqual(code, 3)
        self.assertEqual(value["reasonCode"], "stale-reference-measurement")

    def test_a_foreign_inventory_cannot_define_the_expected_objects(self):
        _, scene_sha, reference_sha = self.composed()
        foreign = self.root / "foreign-inventory.json"
        foreign.write_bytes(canonical({"kind": "img2.object-inventory", "version": 1,
                                       "objects": [{"objectId": "subject", "role": "subject",
                                                    "ownerRoute": "base:generic",
                                                    "artifactObjectId": "subject"},
                                                   {"objectId": "ghost", "role": "prop",
                                                    "ownerRoute": "base:generic",
                                                    "artifactObjectId": "ghost"}]}))
        code, value = self.gates(self.measurements(scene_sha, reference_sha), inventory=foreign)
        self.assertEqual(code, 3)
        self.assertEqual(value["reasonCode"], "inventory-mismatch")

    def test_an_unevaluated_measurement_is_never_a_pass(self):
        _, scene_sha, reference_sha = self.composed()
        payload = self.measurements(scene_sha, reference_sha)
        del payload["objects"][0]["contactGap"]
        code, value = self.gates(payload)
        self.assertEqual(code, 3)
        self.assertEqual(value["status"], "unevaluated")
        self.assertFalse(value["result"]["deliverable"])

    def test_a_measurement_from_another_producer_run_is_not_accepted(self):
        _, scene_sha, reference_sha = self.composed()
        payload = self.measurements(scene_sha, reference_sha)
        payload["objects"][0]["boxIoU"]["sceneSha256"] = "2" * 64
        code, value = self.gates(payload)
        self.assertEqual(code, 3)
        self.assertEqual(value["status"], "unevaluated")
        reasons = {check["id"]: check["reason"] for check in value["result"]["checks"]}
        self.assertEqual(reasons["object:subject:box-iou"], "missing-or-stale-measurement")


if __name__ == "__main__":
    unittest.main()
