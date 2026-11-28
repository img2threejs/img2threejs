"""Offline structural join tests; these do not authenticate a parent or producer."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

from forge.tests import test_scene_composer as fixture


class ComposeRequestTests(unittest.TestCase):
    def setUp(self):
        self.case = fixture.SceneComposerTests("test_real_skinned_glb_is_retained_and_module_loads_parent_only")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root = self.case.root
        self.cli = Path(__file__).resolve().parents[1] / "compose.py"
        self.state = {
            "routeDecision": {"phase": "decided", "skipComposition": False,
                              "inventorySha256": self.case.request["inventory"]["sha256"],
                              "imageSha256": self.case.request["reference"]["sha256"]},
            "objects": [{"objectId": "subject", "ownerRoute": "base:generic",
                         "artifactPath": self.case.request["artifacts"][0]["path"]}],
            "checklist": [{"id": "object:subject", "status": "done"}],
        }
        self.save_state()

    def save_state(self):
        self.case.put("state.json", self.state)

    def run_request(self):
        request = self.case.request
        return subprocess.run([sys.executable, str(self.cli), "request", "--workspace", str(self.root),
                               "--state", "state.json", "--inventory", request["inventory"]["path"],
                               "--reference", request["reference"]["path"],
                               "--placement", request["placement"]["path"]],
                              capture_output=True, text=True)

    def test_request_is_consumed_by_scene_command(self):
        result = self.run_request()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        request = json.loads(result.stdout)
        self.assertEqual(request, self.case.request)
        self.case.put("request.json", request)
        scene = subprocess.run([sys.executable, str(self.cli), "scene", "--workspace", str(self.root),
                                "--request", str(self.root / "request.json"), "--output-dir", "scene"],
                               capture_output=True, text=True)
        self.assertEqual(scene.returncode, 0, scene.stderr + scene.stdout)
        self.assertFalse(json.loads(scene.stdout)["deliverable"])
        self.assertTrue((self.root / "scene/scene.mjs").is_file())

    def test_pending_child_and_wrong_inventory_are_refused(self):
        self.state["checklist"][0]["status"] = "pending"
        self.save_state()
        result = self.run_request()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["reasonCode"], "child-incomplete")
        self.state["checklist"][0]["status"] = "done"
        self.state["routeDecision"]["inventorySha256"] = "0" * 64
        self.save_state()
        result = self.run_request()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["reasonCode"], "stale-route-aggregate")

    def test_repeated_instances_use_the_single_owner_artifact(self):
        first = self.case.inventory["objects"][0]
        first["instanceOf"] = "pair"
        self.case.inventory["objects"].append(dict(first, objectId="second"))
        self.case.artifacts["subject"]["ownedObjectIds"].append("second")
        self.case.add_placement("second")
        self.case.bind()
        self.state["routeDecision"]["inventorySha256"] = self.case.request["inventory"]["sha256"]
        self.save_state()
        result = self.run_request()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        request = json.loads(result.stdout)
        self.assertEqual(len(request["artifacts"]), 1)
        self.case.put("request.json", request)
        scene = subprocess.run([sys.executable, str(self.cli), "scene", "--workspace", str(self.root),
                                "--request", str(self.root / "request.json"), "--output-dir", "scene"],
                               capture_output=True, text=True)
        self.assertEqual(scene.returncode, 0, scene.stderr + scene.stdout)
        self.assertEqual(len(json.loads(scene.stdout)["manifest"]["instances"]), 2)

    def test_unowned_artifact_is_refused(self):
        self.state["objects"][0]["ownerRoute"] = "other"
        self.save_state()
        result = self.run_request()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["reasonCode"], "ownership-mismatch")

    def test_symlinked_artifact_is_refused(self):
        path = self.root / self.state["objects"][0]["artifactPath"]
        contents = path.read_bytes()
        path.unlink()
        external = self.root / "other-artifact.json"
        external.write_bytes(contents)
        path.symlink_to(external)
        result = self.run_request()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["reasonCode"], "unsafe-path")

    def test_changed_artifact_after_request_is_refused_by_scene(self):
        request = json.loads(self.run_request().stdout)
        self.case.put("request.json", request)
        (self.root / request["artifacts"][0]["path"]).write_text("{}")
        scene = subprocess.run([sys.executable, str(self.cli), "scene", "--workspace", str(self.root),
                                "--request", str(self.root / "request.json"), "--output-dir", "scene"],
                               capture_output=True, text=True)
        self.assertEqual(scene.returncode, 3)
        self.assertEqual(json.loads(scene.stdout)["reasonCode"], "hash-mismatch")
