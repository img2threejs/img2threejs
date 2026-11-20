"""Offline D18 integrity cases with actual temporary GLB/container/evidence bytes.

The triangle is a format fixture, not a rendered model or visual-quality result.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

_FORGE = str(Path(__file__).resolve().parents[1])
if _FORGE not in sys.path:
    sys.path.insert(0, _FORGE)

from _shared.route_artifact import ArtifactError, load_artifact, validate_artifact


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def glb_bytes(document=None, binary=None):
    """Build a real glTF 2 binary container, including a triangle by default."""
    if document is None:
        document = {
            "asset": {"version": "2.0"}, "scene": 0,
            "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
            "buffers": [{"byteLength": 36}],
            "bufferViews": [{"buffer": 0, "byteLength": 36}],
            "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3,
                           "type": "VEC3", "min": [0, 0, 0], "max": [1, 1, 0]}],
        }
        binary = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    payload = canonical(document)
    payload += b" " * (-len(payload) % 4)
    chunks = struct.pack("<II", len(payload), 0x4E4F534A) + payload
    if binary is not None:
        binary += b"\x00" * (-len(binary) % 4)
        chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


class ArtifactContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.artifact = {
            "objectId": "object-1", "outputKind": "3d-object",
            "files": [self.put("output/model.glb", glb_bytes())],
            "frame": {"units": "m", "up": "+Y", "forward": "+Z"},
            "pivot": "bottom-centre", "scaleEstimate": 1.0, "rig": None,
        }

    def put(self, path, data):
        file = self.workspace / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        return {"path": path, "sha256": hashlib.sha256(data).hexdigest()}

    def reject(self, reason, artifact=None, workspace=None):
        with self.assertRaises(ArtifactError) as raised:
            validate_artifact(self.workspace if workspace is None else workspace,
                              self.artifact if artifact is None else artifact)
        self.assertEqual(raised.exception.reason_code, reason)
        self.assertTrue(raised.exception.field)
        return raised.exception

    def rigged(self, clips=True):
        artifact = deepcopy(self.artifact)
        artifact["outputKind"] = "rigged-3d"
        # Synthetic sidecar payloads are intentionally NOT asserted to match the GLB.
        skeleton = self.put("evidence/skeleton.json", canonical({"joints": [0]}))
        artifact["files"].append(skeleton)
        artifact["rig"] = {"skeletonSha": skeleton["sha256"], "clips": []}
        if clips:
            clip = self.put("evidence/walk.json", canonical({"channels": [{"node": 0}]}))
            artifact["files"].append(clip)
            artifact["rig"]["clips"].append({"name": "walk", "sha256": clip["sha256"]})
        return artifact

    def test_valid_glb_metadata_is_independent_and_has_no_quality_claim(self):
        self.artifact["provenance"] = {"provider": "fixture", "observations": [1, "recorded"]}
        original = deepcopy(self.artifact)
        result = validate_artifact(self.workspace, self.artifact)
        self.assertEqual(self.artifact, original)
        for key in original:
            self.assertEqual(result[key], original[key])
        self.assertEqual(result["validation"], {
            "scope": "artifact-integrity", "runtime": "unevaluated",
            "visualQuality": "unevaluated", "rigParity": "unevaluated",
        })
        self.assertNotIn("deliverable", result)
        self.assertNotIn("passed", result)
        result["provenance"]["observations"].append("changed")
        result["files"][0]["path"] = "changed"
        self.assertEqual(self.artifact, original)

    def test_input_cannot_claim_validator_measured_quality(self):
        self.artifact["validation"] = {"visualQuality": "passed", "runtime": "passed"}
        result = validate_artifact(self.workspace, self.artifact)
        self.assertEqual(result["validation"]["visualQuality"], "unevaluated")
        self.assertEqual(result["validation"]["runtime"], "unevaluated")

    def test_loader_reads_manifest_and_actual_bytes(self):
        self.put("artifact.json", canonical(self.artifact))
        self.assertEqual(load_artifact(self.workspace)["objectId"], "object-1")
        self.put("manifests/child.json", canonical(self.artifact))
        self.assertEqual(load_artifact(str(self.workspace), "manifests/child.json")["files"],
                         self.artifact["files"])
        (self.workspace / "output/model.glb").write_bytes(b"changed")
        with self.assertRaises(ArtifactError) as raised:
            load_artifact(self.workspace)
        self.assertEqual(raised.exception.reason_code, "hash-mismatch")

    def test_loader_rejects_missing_invalid_and_nonobject_json(self):
        for data, reason in ((b"{broken", "invalid-json"),
                             (b'{"a":1,"a":2}', "invalid-json"),
                             (b'{"a":NaN}', "non-finite-number"),
                             (b'{"a":1e9999}', "non-finite-number"),
                             (b'[]', "invalid-artifact"),
                             (b"\xff", "invalid-json")):
            with self.subTest(data=data):
                self.put("artifact.json", data)
                with self.assertRaises(ArtifactError) as raised:
                    load_artifact(self.workspace)
                self.assertEqual(raised.exception.reason_code, reason)
        with self.assertRaises(ArtifactError) as raised:
            load_artifact(self.workspace, "missing.json")
        self.assertEqual(raised.exception.reason_code, "missing-evidence")

    def test_loader_rejects_symlink_and_escaping_manifest(self):
        self.put("real.json", canonical(self.artifact))
        (self.workspace / "artifact.json").symlink_to("real.json")
        for path in ("artifact.json", "../outside.json", str(self.workspace / "real.json")):
            with self.subTest(path=path), self.assertRaises(ArtifactError) as raised:
                load_artifact(self.workspace, path)
            self.assertEqual(raised.exception.reason_code, "unsafe-path")

    def test_source_only_factories_are_integrity_not_runtime_evidence(self):
        for suffix in ("js", "mjs", "ts", "tsx"):
            with self.subTest(suffix=suffix):
                artifact = deepcopy(self.artifact)
                artifact["files"] = [self.put(f"createObjectModel.{suffix}",
                    b"export function createObjectModel() { return new THREE.Group(); }\n")]
                result = validate_artifact(self.workspace, artifact)
                self.assertEqual(result["validation"]["runtime"], "unevaluated")

    def test_source_suffix_does_not_accept_binary_empty_or_json(self):
        for data, reason in ((b"\xff\x00", "invalid-source"), (b"a\x00b", "invalid-source"),
                             (b"  \n", "invalid-source"), (b"{}", "invalid-source"),
                             (b'"a manifest"', "invalid-source"),
                             (glb_bytes(), "invalid-source"), (b"", "missing-evidence")):
            with self.subTest(data=data[:20]):
                artifact = deepcopy(self.artifact)
                artifact["files"] = [self.put("model.js", data)]
                self.reject(reason, artifact)

    def test_only_approved_terminal_3d_kinds_are_accepted(self):
        for kind in ("spec-augmentation", "2d-scene", "threejs", "GLB", "", [], True):
            with self.subTest(kind=kind):
                artifact = deepcopy(self.artifact)
                artifact["outputKind"] = kind
                self.reject("unsupported-output-kind", artifact)

    def test_required_metadata_is_missing_evidence(self):
        for key in self.artifact:
            with self.subTest(key=key):
                artifact = deepcopy(self.artifact)
                del artifact[key]
                self.reject("missing-evidence", artifact)
        for key in ("objectId", "outputKind", "frame", "pivot", "scaleEstimate", "files"):
            with self.subTest(null=key):
                artifact = deepcopy(self.artifact)
                artifact[key] = None
                self.reject("missing-evidence", artifact)

    def test_malformed_metadata_and_nonfinite_extra_fields_fail(self):
        for artifact in ([], "artifact.json", 1, None):
            with self.subTest(artifact=artifact), self.assertRaises(ArtifactError) as raised:
                validate_artifact(self.workspace, artifact)
            self.assertEqual(raised.exception.reason_code, "invalid-artifact")
        for value in ("", "../foreign", "object 1", 12, True):
            artifact = deepcopy(self.artifact)
            artifact["objectId"] = value
            self.reject("invalid-object-id", artifact)
        for value in (float("nan"), float("inf"), float("-inf")):
            artifact = deepcopy(self.artifact)
            artifact["provenance"] = {"nested": [value]}
            self.reject("non-finite-number", artifact)
        for value in (object(), {1: "nonstring key"}, (1, 2), "\ud800"):
            artifact = deepcopy(self.artifact)
            artifact["provenance"] = value
            self.reject("invalid-json", artifact)
        artifact = deepcopy(self.artifact)
        artifact["cycle"] = artifact
        self.reject("invalid-json", artifact)

    def test_exact_frame_and_bottom_centre_are_required(self):
        for field, value in (("units", "cm"), ("up", "+Z"), ("forward", "-Z")):
            artifact = deepcopy(self.artifact)
            artifact["frame"][field] = value
            self.reject("invalid-frame", artifact)
        for frame in ({}, [], "m +Y +Z", dict(self.artifact["frame"], handedness="left")):
            artifact = deepcopy(self.artifact)
            artifact["frame"] = frame
            self.reject("invalid-frame", artifact)
        for pivot in ("center", "bottom-center", [], 0):
            artifact = deepcopy(self.artifact)
            artifact["pivot"] = pivot
            self.reject("invalid-pivot", artifact)

    def test_scale_is_positive_finite_numeric_not_boolean(self):
        for value in (0, -1, True, False, "1", {}, []):
            artifact = deepcopy(self.artifact)
            artifact["scaleEstimate"] = value
            self.reject("invalid-metric", artifact)
        for value in (float("nan"), float("inf"), float("-inf")):
            artifact = deepcopy(self.artifact)
            artifact["scaleEstimate"] = value
            self.reject("non-finite-number", artifact)
        self.artifact["scaleEstimate"] = 0.001
        self.assertEqual(validate_artifact(self.workspace, self.artifact)["scaleEstimate"], 0.001)

    def test_scene_requires_glb_and_positive_metric_room_dimensions(self):
        artifact = deepcopy(self.artifact)
        artifact["outputKind"] = "3d-scene"
        self.reject("missing-evidence", artifact)
        artifact["roomDimensions"] = {"width": 4, "height": 2.5, "depth": 3}
        self.assertEqual(validate_artifact(self.workspace, artifact)["roomDimensions"],
                         artifact["roomDimensions"])
        for axis in artifact["roomDimensions"]:
            for value in (0, -1, True, "2"):
                trial = deepcopy(artifact)
                trial["roomDimensions"][axis] = value
                self.reject("invalid-metric", trial)
            trial = deepcopy(artifact)
            del trial["roomDimensions"][axis]
            self.reject("missing-evidence", trial)
        artifact["files"] = [self.put("scene.ts", b"export const scene = new THREE.Scene();")]
        self.reject("missing-evidence", artifact)

    def test_missing_empty_and_malformed_files_are_distinguished(self):
        for files, reason in (([], "missing-evidence"), ({}, "invalid-files"),
                              (["model.glb"], "invalid-files"), ([{}], "missing-evidence")):
            artifact = deepcopy(self.artifact)
            artifact["files"] = files
            self.reject(reason, artifact)
        for field in ("path", "sha256"):
            artifact = deepcopy(self.artifact)
            del artifact["files"][0][field]
            self.reject("missing-evidence", artifact)
        (self.workspace / "output/model.glb").unlink()
        self.reject("missing-evidence")
        self.put("output/model.glb", b"")
        self.reject("missing-evidence")
        (self.workspace / "output/model.glb").unlink()
        (self.workspace / "output/model.glb").mkdir()
        self.reject("invalid-file")

    def test_hash_must_be_exact_lowercase_sha256_of_actual_bytes(self):
        for digest in ("a" * 63, "A" * 64, "g" * 64, [], 123):
            artifact = deepcopy(self.artifact)
            artifact["files"][0]["sha256"] = digest
            self.reject("invalid-sha256", artifact)
        self.artifact["files"][0]["sha256"] = "0" * 64
        self.reject("hash-mismatch")

    def test_paths_cannot_escape_or_use_ambiguous_spellings(self):
        for path in ("../model.glb", "/tmp/model.glb", "a/../model.glb", "./output/model.glb",
                     "output//model.glb", "output/model.glb/", "C:/model.glb", "file:model.glb",
                     "output\\model.glb", "output/\x00model.glb", "output/\nmodel.glb", ".", ""):
            with self.subTest(path=path):
                artifact = deepcopy(self.artifact)
                artifact["files"][0]["path"] = path
                self.reject("unsafe-path", artifact)

    def test_symlinks_are_rejected_even_when_target_is_inside_workspace(self):
        target = self.workspace / "output/model.glb"
        for link_target in (target, self.workspace / "missing.glb"):
            link = self.workspace / "linked.glb"
            link.symlink_to(link_target)
            artifact = deepcopy(self.artifact)
            artifact["files"][0]["path"] = "linked.glb"
            self.reject("unsafe-path", artifact)
            link.unlink()
        (self.workspace / "linked-dir").symlink_to(self.workspace / "output", target_is_directory=True)
        artifact = deepcopy(self.artifact)
        artifact["files"][0]["path"] = "linked-dir/model.glb"
        self.reject("unsafe-path", artifact)
        (self.workspace / "workspace-link").symlink_to(self.workspace, target_is_directory=True)
        self.reject("unsafe-path", workspace=self.workspace / "workspace-link")

    def test_duplicate_paths_and_hardlink_aliases_are_rejected(self):
        artifact = deepcopy(self.artifact)
        artifact["files"].append(deepcopy(artifact["files"][0]))
        self.reject("duplicate-reference", artifact)
        os.link(self.workspace / "output/model.glb", self.workspace / "alias.glb")
        artifact["files"][1]["path"] = "alias.glb"
        self.reject("duplicate-reference", artifact)

    def test_extension_alone_does_not_prove_glb_or_model_presence(self):
        for data in (b"this is not a GLB", b"{}", b"glTF" + bytes(16)):
            artifact = deepcopy(self.artifact)
            artifact["files"] = [self.put("fake.glb", data)]
            self.reject("invalid-glb", artifact)
        artifact = deepcopy(self.artifact)
        artifact["files"] = [self.put("model.dat", glb_bytes())]
        self.reject("missing-evidence", artifact)
        artifact["files"] = [self.put("texture.png", b"nonempty supporting bytes")]
        self.reject("missing-evidence", artifact)

    def test_corrupt_glb_container_and_malformed_json_fail(self):
        good = glb_bytes()
        wrong_version = bytearray(good)
        struct.pack_into("<I", wrong_version, 4, 1)
        duplicate_json = good + good[12:12 + 8 + struct.unpack_from("<I", good, 12)[0]]
        duplicate_json = duplicate_json[:8] + struct.pack("<I", len(duplicate_json)) + duplicate_json[12:]
        bad_first = bytearray(good)
        struct.pack_into("<I", bad_first, 16, 0x004E4942)
        unaligned = struct.pack("<4sIIII", b"glTF", 2, 23, 3, 0x4E4F534A) + b"{} "
        for data in (good[:-1], bytes(wrong_version), duplicate_json, bytes(bad_first),
                     unaligned, glb_bytes({}), glb_bytes({"asset": {"version": "1.0"}})):
            with self.subTest(size=len(data)):
                artifact = deepcopy(self.artifact)
                artifact["files"] = [self.put("bad.glb", data)]
                self.reject("invalid-glb", artifact)
        bad_json = b'{"asset":{"version":"2.0"},"bad":NaN}'
        bad_json += b" " * (-len(bad_json) % 4)
        data = struct.pack("<4sIIII", b"glTF", 2, 20 + len(bad_json), len(bad_json), 0x4E4F534A) + bad_json
        artifact = deepcopy(self.artifact)
        artifact["files"] = [self.put("bad.glb", data)]
        self.reject("non-finite-number", artifact)

    def test_static_and_animated_rig_references_are_integrity_only(self):
        for clips in (False, True):
            artifact = self.rigged(clips)
            result = validate_artifact(self.workspace, artifact)
            self.assertEqual(result["rig"], artifact["rig"])
            self.assertEqual(result["validation"]["rigParity"], "unevaluated")

    def test_rig_requires_separate_actual_skeleton_and_clip_evidence(self):
        self.artifact["outputKind"] = "rigged-3d"
        self.reject("missing-evidence")
        artifact = self.rigged()
        for field in ("skeletonSha", "clips"):
            trial = deepcopy(artifact)
            del trial["rig"][field]
            self.reject("missing-evidence", trial)
        trial = deepcopy(artifact)
        trial["rig"]["skeletonSha"] = "a" * 64
        self.reject("missing-evidence", trial)
        trial = deepcopy(artifact)
        trial["rig"]["clips"][0]["sha256"] = "b" * 64
        self.reject("missing-evidence", trial)
        trial = deepcopy(artifact)
        trial["rig"]["skeletonSha"] = trial["files"][0]["sha256"]
        self.reject("invalid-rig-reference", trial)
        trial = deepcopy(artifact)
        trial["rig"]["clips"][0]["sha256"] = trial["files"][0]["sha256"]
        self.reject("invalid-rig-reference", trial)
        trial = deepcopy(artifact)
        trial["files"] = trial["files"][:1]
        self.reject("missing-evidence", trial)

    def test_rig_rejects_malformed_duplicate_and_noncanonical_evidence(self):
        artifact = self.rigged()
        for rig in ([], "skeleton", {"skeletonSha": "not-sha", "clips": []},
                    {"skeletonSha": artifact["rig"]["skeletonSha"], "clips": {}}):
            trial = deepcopy(artifact)
            trial["rig"] = rig
            self.reject("invalid-sha256" if type(rig) is dict and rig["skeletonSha"] == "not-sha"
                        else "invalid-rig", trial)
        for clip in ({"name": "", "sha256": "a" * 64}, "walk"):
            trial = deepcopy(artifact)
            trial["rig"]["clips"] = [clip]
            self.reject("invalid-rig", trial)
        trial = deepcopy(artifact)
        trial["rig"]["clips"].append(deepcopy(trial["rig"]["clips"][0]))
        self.reject("duplicate-reference", trial)
        trial = deepcopy(artifact)
        trial["rig"]["clips"][0]["sha256"] = trial["rig"]["skeletonSha"]
        self.reject("duplicate-reference", trial)
        for payload, reason in ((b"{}", "invalid-rig-evidence"),
                                (b"1", "invalid-rig-evidence"),
                                (b'{ "joints": [0] }\n', "noncanonical-evidence"),
                                (b"not json", "invalid-json")):
            trial = deepcopy(artifact)
            sidecar = self.put("evidence/skeleton.json", payload)
            trial["files"][1] = sidecar
            trial["rig"]["skeletonSha"] = sidecar["sha256"]
            self.reject(reason, trial)


if __name__ == "__main__":
    unittest.main()
