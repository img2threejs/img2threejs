"""Capture-authority negatives and optional REAL Chrome/Pixi technical replay.

No fixture grants artwork-quality/terminal approval. --reproduce runs only the
explicitly authorized disposable technical scene, never the integrated suite.
Set IMG2_PIXI_PLUGIN, IMG2_TEST_HARNESS and PIXI_REPLAY_RUNTIME for real replay.
"""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

FORGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(FORGE), str(FORGE / "_shared")]
from pixi_capture_authority import (AREA, PRODUCER, PixiCaptureAuthority, ReviewedPixiPolicy, _measure)
from route_parent import ParentError, _encode
from stage0_route.images import png


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def data_url(pixels):
    import base64
    return "data:image/png;base64," + base64.b64encode(png(4, 4, 4, pixels)).decode()


def protocol_capture():
    """Authored unit pixels, NOT an observed native scene or quality evidence."""
    node = {"id": "badge", "type": "Graphics", "edits": ["visibility"]}
    requirements = {"requiredNodes": [node], "criticalFeatures": [], "behaviorChecks": []}
    baseline = data_url(b"\xff\0\0\xff" + b"\0" * 60)
    isolated = data_url(b"\0" * 64)
    snapshot = {"artboard": [4, 4], "requirements": requirements}
    capture = {"kind": "img2.pixi2d-capture", "version": 1, "producer": PRODUCER,
        "renderer": "pixi.js", "pixiVersion": "8.22.0", "nodes": [node],
        "images": {"baseline": baseline, "edit-badge-visibility": isolated, "edit-badge-visibility-reset": baseline},
        "provenance": {"contract": "img2.pixi2d-node-provenance/v1", "resolution": 1,
            "nodes": [{"id": "badge", "type": "Graphics", "parent": None, "visibleAtBaseline": True,
                "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
                "isolation": {"method": "visibility-toggle", "isolated": isolated,
                              "restored": baseline, "restoredVisible": True}}]}}
    return snapshot, capture


class CaptureAuthorityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.child = self.root / "child"
        self.child.mkdir()
        self.snapshot, self.capture = protocol_capture()
        def put(path, raw):
            target = self.child / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            return {"path": path, "sha256": sha(raw)}
        self.put = put
        self.reference = put("reference.png", png(4, 4, 4, b"\0" * 64))
        self.source = put("scene.mjs", b"export function createRoutedPixiScene() { throw new Error('unit only, never execute'); }")
        self.inputs = {"sources": ["scene.mjs"], "assets": [], "reference": "reference.png"}
        inputs = put(AREA + "inputs.json", _encode(self.inputs))
        self.snapshot.update(kind="img2.pixi2d-snapshot", version=1, objectId="badge",
                             bindings=[inputs, self.reference, self.source])
        self.snapshot_binding = put(AREA + "snapshot.json", _encode(self.snapshot))
        self.capture["snapshotSha256"] = sha(_encode(self.snapshot))
        capture = put(AREA + "capture.json", _encode(self.capture))
        evidence = {"snapshotSha256": self.capture["snapshotSha256"], "capture": capture, "files": []}
        put(AREA + "evidence.json", _encode(evidence))
        review = put(AREA + "review.json", _encode({"status": "unreviewed-unit-only"}))
        self.artifact = {"objectId": "badge", "snapshotSha256": self.capture["snapshotSha256"],
            "editableSources": [self.source], "assets": [], "bindings": self.snapshot["bindings"],
            "evidence": [], "review": review, "evidenceSha256": sha(_encode(evidence))}
        self.artifact_binding = put(AREA + "artifact.json", _encode(self.artifact))
        self.policy = ReviewedPixiPolicy(object_id="badge", revision=1,
            snapshot_binding=tuple(self.snapshot_binding.values()), reference_binding=tuple(self.reference.values()),
            requirements_json=_encode(self.snapshot["requirements"]), sources=(tuple(self.source.values()),), assets=(),
            plugin_root=str(self.root), node=str(Path(sys.executable).resolve()),
            runtime_options=tuple((key, str(self.root)) for key in ("pixi-package", "playwright-package", "chromium", "chromium-root")),
            runtime_lock_json=b"{}", review_id="unit-negative-only",
            review_notes="Unit rejection fixture; invalid runtime deliberately cannot execute or authorize anything.")
        self.policies = {"badge": self.policy}
        self.authority = PixiCaptureAuthority(self.policies)
        self.arguments = dict(object_id="badge", revision=1, artifact_binding=self.artifact_binding,
            snapshot_binding=self.snapshot_binding, reference_binding=self.reference, requirements=self.snapshot["requirements"])

    def reject(self, reason, function, *args, **kwargs):
        with self.assertRaises(ParentError) as error:
            function(*args, **kwargs)
        self.assertEqual(error.exception.reason_code, reason)

    def test_missing_host_approval_cannot_be_supplied_by_workspace(self):
        self.put("approval.json", _encode({"approved": True, "nativeNodeProvenance": "verified", "terminalAcceptance": True}))
        self.reject("terminal-native-capture-authority-unavailable", PixiCaptureAuthority().verify_capture,
                    self.child, **self.arguments)

    def test_candidate_dictionary_cannot_be_policy(self):
        self.reject("invalid-reviewed-pixi-policies", PixiCaptureAuthority, {"badge": {"approved": True}})

    def test_approval_is_deeply_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            self.policy.revision = 2
        self.reject("immutable-pixi-closure-required", replace, self.policy, sources=[tuple(self.source.values())])
        self.reject("canonical-pixi-policy-json-required", replace, self.policy, requirements_json=b'{ "requiredNodes": [] }')

    def test_live_approval_revocation_blocks_before_execution(self):
        self.policies.clear()
        with patch("pixi_capture_authority.subprocess.run") as run:
            self.reject("pixi-capture-policy-revoked", self.authority.verify_capture, self.child, **self.arguments)
            run.assert_not_called()

    def test_wrong_revision_reference_snapshot_and_requirements_block(self):
        for key, value in (("revision", 2), ("reference_binding", {"path": "reference.png", "sha256": "0" * 64}),
                           ("snapshot_binding", {"path": AREA + "snapshot.json", "sha256": "0" * 64}),
                           ("requirements", {"requiredNodes": [], "criticalFeatures": [], "behaviorChecks": []})):
            with self.subTest(key=key):
                args = dict(self.arguments, **{key: value})
                self.reject("pixi-capture-approval-subject-mismatch", self.authority.verify_capture, self.child, **args)

    def test_changed_source_is_re_read_and_refused(self):
        self.put("scene.mjs", b"changed after review")
        with patch("pixi_capture_authority.subprocess.run") as run:
            self.reject("pixi-capture-stale-binding:scene.mjs", self.authority.verify_capture, self.child, **self.arguments)
            run.assert_not_called()

    def test_provider_claimed_source_approval_does_not_broaden_closure(self):
        self.artifact["editableSources"] = [self.put("extra.mjs", b"export const approved = true;")]
        self.arguments["artifact_binding"] = self.put(AREA + "artifact.json", _encode(self.artifact))
        self.reject("pixi-unreviewed-source-or-asset", self.authority.verify_capture, self.child, **self.arguments)

    def test_unapproved_runtime_blocks_instead_of_running_submitted_js(self):
        with patch("pixi_capture_authority.subprocess.run") as run:
            self.reject("invalid-reviewed-pixi-runtime", self.authority.verify_capture, self.child, **self.arguments)
            run.assert_not_called()

    def test_source_symlink_is_not_an_approved_file(self):
        (self.child / "scene.mjs").unlink()
        (self.root / "outside.mjs").write_text("outside")
        (self.child / "scene.mjs").symlink_to(self.root / "outside.mjs")
        self.reject("pixi-reviewed-capture-invalid-or-unavailable", self.authority.verify_capture, self.child, **self.arguments)

    def test_pixel_measurement_is_derived_not_provider_reported(self):
        result = _measure(self.capture, self.snapshot)
        self.assertEqual(result[0]["pixels"], 1)
        self.assertEqual(result[0]["region"], [0, 0, 1, 1])
        self.capture["provenance"]["nodes"][0]["bounds"]["x"] = 3
        self.reject("pixi-native-pixels-outside-bounds", _measure, self.capture, self.snapshot)

    def test_legacy_diagnostic_capture_cannot_become_native(self):
        self.capture["producer"] = "pixi2d-cdp-replay-v1"
        self.capture["nativeNodeProvenance"] = "verified"
        self.capture["terminalAcceptance"] = True
        self.reject("pixi-reviewed-native-capture-required", _measure, self.capture, self.snapshot)

    def test_bad_native_ids_resets_and_parent_cycles_block(self):
        bad = deepcopy(self.capture)
        bad["provenance"]["nodes"][0]["id"] = "forged"
        self.reject("pixi-native-node-ids-mismatch", _measure, bad, self.snapshot)
        bad = deepcopy(self.capture)
        bad["provenance"]["nodes"][0]["isolation"]["restored"] = bad["images"]["edit-badge-visibility"]
        self.reject("pixi-isolation-reset-mismatch", _measure, bad, self.snapshot)
        bad = deepcopy(self.capture)
        bad["provenance"]["nodes"][0]["parent"] = "badge"
        self.reject("pixi-native-parent-cycle", _measure, bad, self.snapshot)


def real_prerequisites():
    plugin = os.environ.get("IMG2_PIXI_PLUGIN")
    if not plugin:
        return None
    root = Path(plugin).resolve(strict=True)
    spec = importlib.util.spec_from_file_location("pixi_real_replay_fixture", root / "tests/test_replay_runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    required = module.prerequisites()
    return (root, module, required) if required else None


def real_fixture(directory, prerequisite):
    """Known two-file native regression factory, reviewed as technical code only.

    The author inspected routed-scene.mjs and shape.mjs: native Graphics edits,
    deterministic motion, real destroy/recreate and shared Texture preservation.
    This does NOT approve arbitrary submitted factories or their dependencies for
    production. Runtime prerequisites are explicitly selected by the verifier.
    """
    plugin, module, required = prerequisite
    fixture = module.ReplayFixture(str(Path(directory).resolve()), *required)
    fixture.pin()
    capture_result = fixture.invoke("reviewed-capture")
    fixture.put_json(module.AREA / "capture.json", capture_result["replayCapture"])
    result = subprocess.run([sys.executable, str(plugin / "tools/pixi2d.py"), "produce", "--workspace", str(fixture.workspace)],
                            env=fixture.env, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stdout.decode() + result.stderr.decode())
    evidence = json.loads((fixture.workspace / module.AREA / "evidence.json").read_bytes())
    def binding(path):
        return {"path": path, "sha256": sha((fixture.workspace / path).read_bytes())}
    # Honest unreviewed candidate: no scores, fake native reviewer or terminal pass.
    fixture.put_json(module.AREA / "review.json", {"status": "technical-smoke-only-not-semantic-review"})
    snapshot = fixture.current_snapshot
    artifact = {"kind": "img2.pixi2d-scene", "version": 1, "objectId": snapshot["objectId"],
        "outputKind": "2d-scene", "snapshotSha256": sha(_encode(snapshot)), "bindings": snapshot["bindings"],
        "editableSources": [binding(path) for path in fixture.inputs["sources"]],
        "assets": [], "evidence": evidence["files"], "review": binding(AREA + "review.json"),
        "evidenceSha256": sha(_encode(evidence))}
    fixture.put_json(module.AREA / "artifact.json", artifact)
    policy = ReviewedPixiPolicy(object_id=snapshot["objectId"], revision=1,
        snapshot_binding=tuple(binding(AREA + "snapshot.json").values()),
        reference_binding=tuple(binding("reference.png").values()), requirements_json=_encode(snapshot["requirements"]),
        sources=tuple(tuple(value.values()) for value in artifact["editableSources"]), assets=(),
        plugin_root=str(plugin), node=str(Path(shutil.which("node")).resolve()),
        runtime_options=tuple((key, str(Path(value).resolve())) for key, value in fixture.runtime.items()),
        runtime_lock_json=_encode(json.loads(fixture.lock.read_bytes())), review_id="native-regression-technical-source-review",
        review_notes="Inspected the two regression ESM files: actual Pixi Graphics, real edits/motion/destroy-recreate/shared consumer. Explicit local dependencies selected for this technical reproduction only; no artwork-quality or production approval.")
    arguments = dict(object_id=policy.object_id, revision=1, artifact_binding=binding(AREA + "artifact.json"),
        snapshot_binding=binding(AREA + "snapshot.json"), reference_binding=binding("reference.png"), requirements=snapshot["requirements"])
    return fixture, policy, arguments


class RealCaptureAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prerequisite = real_prerequisites()
        if cls.prerequisite is None:
            raise unittest.SkipTest("Explicit real Chrome/Pixi prerequisites missing; no fake browser fallback")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pixi-authority-real-")
        self.addCleanup(temporary.cleanup)
        self.fixture, self.policy, self.arguments = real_fixture(temporary.name, self.prerequisite)
        self.policies = {self.policy.object_id: self.policy}
        self.authority = PixiCaptureAuthority(self.policies)

    def test_actual_native_replay_observes_bounds_pixels_resets_without_quality_claim(self):
        before = {path.relative_to(self.fixture.workspace).as_posix(): path.read_bytes()
                  for path in self.fixture.workspace.rglob("*") if path.is_file()}
        receipt = self.authority.verify_capture(self.fixture.workspace, **self.arguments)
        self.assertEqual(receipt["kind"], "img2.pixi2d-reviewed-capture-receipt")
        self.assertEqual(receipt["nodes"][0]["pixels"], 256)
        self.assertEqual(receipt["nodes"][0]["bounds"], {"x": 8, "y": 8, "width": 16, "height": 16})
        self.assertEqual(receipt["artworkQuality"], "not-evaluated-by-capture-authority")
        self.assertTrue(receipt["loadedResources"])
        for path, raw in before.items():
            self.assertEqual((self.fixture.workspace / path).read_bytes(), raw)

    def test_saved_capture_can_never_self_certify_match(self):
        path = self.fixture.workspace / AREA / "capture.json"
        capture = json.loads(path.read_bytes())
        capture["images"]["baseline"] = capture["images"]["edit-badge-visibility"]
        path.write_bytes(_encode(capture))
        # Rebinding candidate evidence still cannot authorize invented captures.
        evidence_path = self.fixture.workspace / AREA / "evidence.json"
        evidence = json.loads(evidence_path.read_bytes())
        evidence["capture"]["sha256"] = sha(path.read_bytes())
        evidence_path.write_bytes(_encode(evidence))
        artifact_path = self.fixture.workspace / AREA / "artifact.json"
        artifact = json.loads(artifact_path.read_bytes())
        artifact["evidenceSha256"] = sha(_encode(evidence))
        artifact_path.write_bytes(_encode(artifact))
        self.arguments["artifact_binding"]["sha256"] = sha(artifact_path.read_bytes())
        with self.assertRaisesRegex(ParentError, "pixi-current-native-capture-mismatch"):
            self.authority.verify_capture(self.fixture.workspace, **self.arguments)

    def test_changed_reviewed_native_source_needs_new_review_not_new_hash_claim(self):
        path = self.fixture.workspace / "src/shape.mjs"
        path.write_text(path.read_text() + "\n// changed after approval\n")
        with self.assertRaisesRegex(ParentError, "pixi-capture-stale-binding:src/shape.mjs"):
            self.authority.verify_capture(self.fixture.workspace, **self.arguments)

    def test_revocation_during_real_browser_replay_blocks_receipt(self):
        run = subprocess.run
        def revoke_after_replay(*args, **kwargs):
            result = run(*args, **kwargs)
            self.policies.clear()
            return result
        with patch("pixi_capture_authority.subprocess.run", side_effect=revoke_after_replay):
            with self.assertRaisesRegex(ParentError, "pixi-capture-policy-revoked"):
                self.authority.verify_capture(self.fixture.workspace, **self.arguments)


if __name__ == "__main__":
    if sys.argv[1:] == ["--reproduce"]:
        prerequisite = real_prerequisites()
        if prerequisite is None:
            raise SystemExit("Explicit real Chrome/Pixi prerequisites missing; no substitute")
        with tempfile.TemporaryDirectory(prefix="pixi-authority-authorized-reproduction-") as directory:
            fixture, policy, arguments = real_fixture(directory, prerequisite)
            receipt = PixiCaptureAuthority({policy.object_id: policy}).verify_capture(fixture.workspace, **arguments)
            print(json.dumps({"observation": "Actual reviewed technical Graphics fixture, not artwork quality or terminal acceptance",
                "browserVersion": receipt["browserVersion"], "nodes": receipt["nodes"],
                "captureSha256": receipt["capture"]["sha256"], "runtimeLockSha256": receipt["runtimeLockSha256"],
                "loadedResourceCount": len(receipt["loadedResources"]), "artworkQuality": receipt["artworkQuality"],
                "trustBoundary": receipt["trustBoundary"]}, sort_keys=True))
    else:
        unittest.main()
