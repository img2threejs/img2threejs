"""Offline consumers of real route cards from explicitly supplied isolated siblings.

Set IMG2_CROSS_ROUTE_{PIXI2D,INTERIOR,CS2,CHARACTER}_ROOT to sibling worktrees.
Missing paths skip with the named prerequisite; malformed existing data fails.
Only temporary workspaces are written. Injected probes and synthetic GLB/PNG/
classification files are protocol controls, not real provider/dependency/gate
qualification, screenshots, identity accuracy or visual-quality evidence.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

_FORGE = Path(__file__).resolve().parents[1]
_BASE = _FORGE.parent
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cross_route"
if str(_FORGE) not in sys.path:
    sys.path.insert(0, str(_FORGE))
from _shared.route_artifact import ArtifactError, load_artifact, validate_artifact
from _shared.route_eligibility import evaluate_candidate


class CrossRouteContracts(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((_FIXTURES / "contracts.json").read_text(encoding="utf-8"))
        temporary = tempfile.TemporaryDirectory(prefix="cross-route-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()

    def plugin_root(self, plugin, *required):
        variable = "IMG2_CROSS_ROUTE_" + plugin.upper() + "_ROOT"
        supplied = os.environ.get(variable)
        if not supplied:
            self.skipTest(f"missing prerequisite: {variable} (isolated sibling {plugin})")
        root = Path(supplied).expanduser().resolve()
        # Never silently import user originals or installed plugin directories.
        self.assertEqual(root, (_BASE.parent / plugin).resolve(),
                         f"{variable} must name the isolated sibling {plugin} worktree")
        for relative in ("route.json",) + required:
            if not (root / relative).is_file():
                self.skipTest(f"missing prerequisite: {plugin}/{relative} at {root}")
        return root

    def card(self, plugin):
        root = self.plugin_root(plugin)
        return json.loads((root / "route.json").read_text(encoding="utf-8"))

    def inputs(self, plugin):
        return deepcopy(self.fixture["consumerInputs"][plugin])

    def assert_rejected(self, card, inputs, reason):
        result = evaluate_candidate(card, **inputs)
        self.assertFalse(result["eligible"], result)
        self.assertIn(reason, result["reasons"])
        return result

    def put(self, relative, data):
        path = self.workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"path": relative, "sha256": hashlib.sha256(data).hexdigest()}

    def artifact(self, kind="3d-object"):
        # Only a valid GLB container with asset metadata: deliberately no geometry.
        payload = b'{"asset":{"version":"2.0"}}'
        payload += b" " * (-len(payload) % 4)
        data = (struct.pack("<4sII", b"glTF", 2, 20 + len(payload))
                + struct.pack("<II", len(payload), 0x4E4F534A) + payload)
        artifact = deepcopy(self.fixture["artifact"])
        artifact["outputKind"] = kind
        artifact["files"] = [self.put("output/protocol.glb", data)]
        if kind == "3d-scene":
            artifact["roomDimensions"] = {"width": 4, "height": 3, "depth": 5}
        return artifact

    def test_actual_pixi_card_cannot_downgrade_a_3d_request(self):
        card, inputs = self.card("pixi2d"), self.inputs("pixi2d")
        self.assertEqual(card["outputKind"], "2d-scene")
        # Positive predicate control only; no assertion that real producers qualify.
        self.assertEqual(evaluate_candidate(card, **inputs), {"eligible": True, "reasons": []})
        inputs["expected_output"] = "3d"
        self.assert_rejected(card, inputs, "output-kind-mismatch")
        artifact = self.artifact(card["outputKind"])
        with self.assertRaises(ArtifactError) as raised:
            validate_artifact(self.workspace, artifact)
        self.assertEqual(raised.exception.reason_code, "unsupported-output-kind")

    def test_actual_plugin_profiles_are_registered_for_routed_child_state(self):
        home = self.workspace / "home"
        plugins = home / "plugins"
        plugins.mkdir(parents=True)
        for profile in ("pixi2d", "interior"):
            root = self.plugin_root(profile, "domain.json")
            (plugins / profile).symlink_to(root, target_is_directory=True)
        (home / "plugins.json").write_text(json.dumps(
            {"plugins": [{"id": "pixi2d"}, {"id": "interior"}]}))
        with patch.dict(os.environ, {"IMG2_HOME": str(home)}):
            from _shared.domains import registered_domains
            from _shared.workflow_state import new_state
            domains = registered_domains()
            for profile in ("pixi2d", "interior"):
                with self.subTest(profile=profile):
                    self.assertEqual(domains[profile], {"id": profile})
                    self.assertEqual(new_state("reference.png", profile=profile)["profile"], profile)

    def test_actual_interior_card_rejects_unknown_and_multiple_rooms(self):
        card, inputs = self.card("interior"), self.inputs("interior")
        self.assertEqual(card["outputKind"], "3d-scene")
        self.assertEqual(evaluate_candidate(card, **inputs), {"eligible": True, "reasons": []})
        for value in (None, "unknown"):
            with self.subTest(roomCount=value):
                inputs["observations"]["roomCount"] = value
                self.assert_rejected(card, inputs, "unknown-observation:roomCount")
        del inputs["observations"]["roomCount"]
        self.assert_rejected(card, inputs, "unknown-observation:roomCount")
        for value in (2, 3, 2.5):
            with self.subTest(roomCount=value):
                inputs["observations"]["roomCount"] = value
                self.assert_rejected(card, inputs, "excluded-observation:roomCount")
        inputs["observations"] = {"enclosure": "unknown", "roomCount": 1}
        self.assert_rejected(card, inputs, "unknown-observation:enclosure")

    def test_actual_interior_card_requires_positive_blender_probe(self):
        card = self.card("interior")
        for value in (None, False, 1, "available"):
            with self.subTest(probe=value):
                inputs = self.inputs("interior")
                inputs["dependencies"] = {"blender>=4.3": value}
                self.assert_rejected(card, inputs, "dependency-missing:blender>=4.3")
        inputs["dependencies"] = {}
        self.assert_rejected(card, inputs, "dependency-missing:blender>=4.3")

    def test_actual_cards_require_independently_produced_blocking_gates(self):
        for plugin in self.fixture["consumerInputs"]:
            with self.subTest(plugin=plugin):
                root = self.plugin_root(plugin, "gates.json")
                card = json.loads((root / "route.json").read_text(encoding="utf-8"))
                declarations = json.loads((root / "gates.json").read_text(encoding="utf-8"))
                self.assertIsInstance(declarations, list)
                self.assertTrue(declarations, "expected actual plugin gate declarations")
                for value in (None, False, 1, "passed"):
                    inputs = self.inputs(plugin)
                    inputs["blocking_gate_produced"] = value
                    self.assert_rejected(card, inputs, "no-blocking-gate")
                # Merely reading gate commands never establishes a runnable producer.
                self.assertNotIn("blocking_gate_produced", card)

    def test_actual_character_card_requires_explicit_rig_intent(self):
        card, inputs = self.card("character"), self.inputs("character")
        self.assertEqual(card["outputKind"], "rigged-3d")
        inputs["intent"] = []
        inputs["blocking_gate_produced"] = False
        result = self.assert_rejected(card, inputs, "missing-required-intent")
        self.assertIn("no-blocking-gate", result["reasons"])

    def test_integrity_cannot_supply_missing_real_interior_gate_producers(self):
        card, inputs = self.card("interior"), self.inputs("interior")
        artifact = self.artifact(card["outputKind"])
        # Even a caller's forged quality label cannot change validator scope.
        artifact["validation"] = {"runtime": "passed", "visualQuality": "passed"}
        result = validate_artifact(self.workspace, artifact)
        self.assertEqual(result["validation"], {
            "scope": "artifact-integrity", "runtime": "unevaluated",
            "visualQuality": "unevaluated", "rigParity": "unevaluated",
        })
        self.assertNotIn("deliverable", result)
        self.assertNotIn("screenshot", result)
        inputs["blocking_gate_produced"] = False
        self.assert_rejected(card, inputs, "no-blocking-gate")

    def test_missing_manifest_metadata_and_bytes_block_artifact_consumption(self):
        with self.assertRaises(ArtifactError) as raised:
            load_artifact(self.workspace)
        self.assertEqual(raised.exception.reason_code, "missing-evidence")
        artifact = self.artifact()
        for key in ("objectId", "frame", "pivot", "scaleEstimate", "rig", "files"):
            with self.subTest(missing=key):
                missing = deepcopy(artifact)
                del missing[key]
                with self.assertRaises(ArtifactError) as raised:
                    validate_artifact(self.workspace, missing)
                self.assertEqual(raised.exception.reason_code, "missing-evidence")
        for content in (None, b""):
            with self.subTest(content=content):
                path = self.workspace / artifact["files"][0]["path"]
                if content is None:
                    path.unlink()
                else:
                    path.write_bytes(content)
                self.put("artifact.json", json.dumps(artifact).encode("utf-8"))
                with self.assertRaises(ArtifactError) as raised:
                    load_artifact(self.workspace)
                self.assertEqual(raised.exception.reason_code, "missing-evidence")

    def test_changed_bytes_and_bad_container_block_despite_matching_metadata(self):
        artifact = self.artifact()
        self.put("artifact.json", json.dumps(artifact).encode("utf-8"))
        bad_file = self.put(artifact["files"][0]["path"], b"not a GLB")
        with self.assertRaises(ArtifactError) as raised:
            load_artifact(self.workspace)
        self.assertEqual(raised.exception.reason_code, "hash-mismatch")
        artifact["files"] = [bad_file]
        self.put("artifact.json", json.dumps(artifact).encode("utf-8"))
        with self.assertRaises(ArtifactError) as raised:
            load_artifact(self.workspace)
        self.assertEqual(raised.exception.reason_code, "invalid-glb")

    def test_actual_cs2_selection_does_not_establish_independent_identity(self):
        modules = ("cs2_manifest.py", "cs2_foundation.py", "cs2_review_contract.py",
                   "detect_cs2.py", "cs2_routing.py")
        root = self.plugin_root("cs2", *("tools/" + name for name in modules))
        card, inputs = self.card("cs2"), self.inputs("cs2")
        self.assertEqual(card["outputKind"], "spec-augmentation")
        self.assertEqual(evaluate_candidate(card, **inputs), {"eligible": True, "reasons": []})
        # Cross-repo imports are test-only and isolated to copies under tempdir.
        tools = self.workspace / "tools"
        tools.mkdir()
        for name in modules:
            shutil.copyfile(root / "tools" / name, tools / name)
        completed = subprocess.run(
            [sys.executable, "-I", "-B", str(_FIXTURES / "cs2_consumer.py"),
             str(tools), str(_FIXTURES / "contracts.json"), str(self.workspace)],
            cwd=self.workspace, capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        results = json.loads(completed.stdout)
        for case in self.fixture["cs2"]["rejectedClassifications"]:
            with self.subTest(classification=case["name"]):
                manifest = results[case["name"]]
                self.assertEqual(manifest["state"], "request-input")
                self.assertNotIn("itemFamily", manifest)
                self.assertNotIn("componentAdapter", manifest)
                self.assertEqual(manifest["identity"]["provenance"], "unknown")
                self.assertEqual(manifest["extensions"]["routeSelection"]["profile"], "cs2")
        for name in ("classified-control", "routed-control"):
            self.assertEqual(results[name]["state"], "proceed")
            self.assertEqual(results[name]["itemFamily"], "knife")
        self.assertEqual(results["unadmitted"]["state"], "rejected")
        # Augmentation must still be consumed by a base child, not a terminal model.
        with self.assertRaises(ArtifactError) as raised:
            validate_artifact(self.workspace, self.artifact(card["outputKind"]))
        self.assertEqual(raised.exception.reason_code, "unsupported-output-kind")


if __name__ == "__main__":
    unittest.main()
