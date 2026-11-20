"""Real binary GLB geometry fixtures, not screenshot or terminal-quality tests."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

_FORGE = str(Path(__file__).resolve().parents[1])
if _FORGE not in sys.path:
    sys.path.insert(0, _FORGE)

from stage4_review.measure_delivered_glb import MeasurementError, measure_delivered_glb


TETRA = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
FACES = [0, 2, 1, 0, 1, 3, 1, 2, 3, 2, 0, 3]


def mesh_fixture(vertices=None, indices=FACES, component=5123, stride=12):
    vertices = TETRA if vertices is None else vertices
    positions = b"".join(struct.pack("<3f", *vertex) + b"\x00" * (stride - 12) for vertex in vertices)
    document = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}], "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "buffers": [{"byteLength": len(positions)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions), "target": 34962}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "type": "VEC3", "count": len(vertices),
                       "min": [min(vertex[axis] for vertex in vertices) for axis in range(3)],
                       "max": [max(vertex[axis] for vertex in vertices) for axis in range(3)]}],
    }
    if stride != 12:
        document["bufferViews"][0]["byteStride"] = stride
    binary = positions
    if indices is not None:
        format_code = {5121: "B", 5123: "H", 5125: "I"}[component]
        encoded = struct.pack("<" + format_code * len(indices), *indices)
        offset = len(binary)
        binary += encoded
        document["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": len(encoded), "target": 34963})
        document["accessors"].append({"bufferView": 1, "componentType": component, "type": "SCALAR", "count": len(indices)})
        document["meshes"][0]["primitives"][0]["indices"] = 1
    document["buffers"][0]["byteLength"] = len(binary)
    return document, binary


def glb(document, binary):
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=True).encode()
    payload += b" " * (-len(payload) % 4)
    binary += b"\x00" * (-len(binary) % 4)
    chunks = struct.pack("<II", len(payload), 0x4E4F534A) + payload
    chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<4sII", b"glTF", 2, len(chunks) + 12) + chunks


class DeliveredGlbMeasurementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "delivered.glb"

    def measure(self, document=None, binary=None, data=None):
        if document is None:
            document, binary = mesh_fixture()
        data = glb(document, binary) if data is None else data
        self.path.write_bytes(data)
        with self.path.open("rb") as source:
            return measure_delivered_glb(source, expected_sha256=hashlib.sha256(data).hexdigest())

    def reject(self, reason, document=None, binary=None, data=None):
        with self.assertRaises(MeasurementError) as caught:
            self.measure(document, binary, data)
        self.assertEqual(caught.exception.reason_code, reason)
        self.assertTrue(caught.exception.field)

    def test_real_tetrahedron_positions_triangles_topology_area_and_volume(self):
        result = self.measure()
        self.assertEqual(result["bounds"], {"min": [0, 0, 0], "max": [1, 1, 1]})
        self.assertEqual(result["extents"], {"width": 1, "height": 1, "depth": 1})
        self.assertEqual(result["depthToMaximumWidthHeightRatio"], 1)
        self.assertEqual(result["triangleCount"], 4)
        report = result["instances"][0]
        self.assertEqual(report["referencedVertexCount"], 4)
        topology = report["indexedTopology"]
        self.assertEqual(topology["edgeCount"], 6)
        self.assertEqual(topology["boundaryEdges"], 0)
        self.assertEqual(topology["nonManifoldEdges"], 0)
        self.assertEqual(topology["inconsistentWindingEdges"], 0)
        self.assertAlmostEqual(topology["signedVolume"], 1 / 6)
        self.assertAlmostEqual(topology["surfaceArea"], 1.5 + math.sqrt(3) / 2)
        self.assertEqual(result["input"]["sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(result["coverage"]["acceptance"], "not-assessed")
        self.assertEqual(result["coverage"]["selfIntersection"], "unevaluated")
        self.assertEqual(result["coverage"]["screenshots"], "unevaluated")
        self.assertNotIn("passed", result)
        self.assertNotIn("deliverable", result)

    def test_all_three_unsigned_index_encodings_are_measured(self):
        for component in (5121, 5123, 5125):
            with self.subTest(component=component):
                result = self.measure(*mesh_fixture(component=component))
                self.assertEqual(result["triangleCount"], 4)
                self.assertAlmostEqual(result["instances"][0]["indexedTopology"]["signedVolume"], 1 / 6)

    def test_interleaved_positions_use_actual_byte_stride(self):
        result = self.measure(*mesh_fixture(stride=20))
        self.assertEqual(result["extents"], {"width": 1, "height": 1, "depth": 1})

    def test_nonindexed_seam_vertices_are_not_falsely_index_watertight(self):
        vertices = [TETRA[index] for index in FACES]
        result = self.measure(*mesh_fixture(vertices, indices=None))
        report = result["instances"][0]
        self.assertEqual(report["indexedTopology"]["boundaryEdges"], 12)
        self.assertIsNone(report["indexedTopology"]["signedVolume"])
        self.assertEqual(report["positionWeldedTopology"]["boundaryEdges"], 0)
        self.assertEqual(report["positionWeldedTopology"]["decimalPrecision"], 6)

    def test_parent_child_trs_composition_measures_world_coordinates(self):
        document, binary = mesh_fixture()
        document["nodes"] = [{"children": [1], "translation": [10, 20, 30], "scale": [2, 3, 4]},
                             {"mesh": 0, "translation": [1, 2, 3]}]
        result = self.measure(document, binary)
        self.assertEqual(result["bounds"], {"min": [12, 26, 42], "max": [14, 29, 46]})
        self.assertEqual(result["extents"], {"width": 2, "height": 3, "depth": 4})
        self.assertAlmostEqual(result["instances"][0]["indexedTopology"]["signedVolume"], 4)

    def test_column_major_matrix_and_reflection_are_applied(self):
        document, binary = mesh_fixture()
        document["nodes"][0]["matrix"] = [-2, 0, 0, 0, 0, 3, 0, 0, 0, 0, 4, 0, 10, 20, 30, 1]
        result = self.measure(document, binary)
        self.assertEqual(result["bounds"], {"min": [8, 20, 30], "max": [10, 23, 34]})
        self.assertAlmostEqual(result["instances"][0]["indexedTopology"]["signedVolume"], -4)

    def test_quaternion_rotation_is_applied_without_inventing_camera_evidence(self):
        document, binary = mesh_fixture()
        document["nodes"][0]["rotation"] = [0, 0, math.sqrt(0.5), math.sqrt(0.5)]
        result = self.measure(document, binary)
        for actual, expected in zip(result["bounds"]["min"], [-1, 0, 0]):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(result["bounds"]["max"], [0, 1, 1]):
            self.assertAlmostEqual(actual, expected)

    def test_two_instances_of_one_mesh_are_both_measured(self):
        document, binary = mesh_fixture()
        document["nodes"].append({"mesh": 0, "translation": [3, 0, 0]})
        document["scenes"][0]["nodes"].append(1)
        result = self.measure(document, binary)
        self.assertEqual(result["triangleCount"], 8)
        self.assertEqual(result["coverage"]["meshInstanceCount"], 2)
        self.assertEqual(result["bounds"]["max"], [4, 1, 1])

    def test_accessor_unused_outlier_is_not_in_rendered_triangle_bounds(self):
        document, binary = mesh_fixture(TETRA + [(1000, 1000, 1000)])
        result = self.measure(document, binary)
        self.assertEqual(result["bounds"]["max"], [1, 1, 1])
        self.assertEqual(result["instances"][0]["declaredVertexCount"], 5)
        self.assertEqual(result["instances"][0]["referencedVertexCount"], 4)

    def test_open_and_degenerate_geometry_are_reported_not_accepted(self):
        result = self.measure(*mesh_fixture([(0, 0, 0), (1, 0, 0), (2, 0, 0)], indices=[0, 1, 2]))
        report = result["instances"][0]["indexedTopology"]
        self.assertEqual(report["boundaryEdges"], 3)
        self.assertEqual(report["degenerateTriangleCount"], 1)
        self.assertEqual(report["surfaceArea"], 0)
        self.assertIsNone(report["signedVolume"])

    def test_nonmanifold_duplicate_face_and_winding_are_measured(self):
        result = self.measure(*mesh_fixture(indices=FACES + FACES[:3]))
        self.assertEqual(result["instances"][0]["indexedTopology"]["nonManifoldEdges"], 3)
        self.assertIsNone(result["instances"][0]["indexedTopology"]["signedVolume"])
        reversed_face = [0, 1, 2] + FACES[3:]
        result = self.measure(*mesh_fixture(indices=reversed_face))
        self.assertEqual(result["instances"][0]["indexedTopology"]["inconsistentWindingEdges"], 3)

    def test_open_descriptor_not_replaced_path_is_authoritative_and_offset_is_preserved(self):
        document, binary = mesh_fixture()
        data = glb(document, binary)
        self.path.write_bytes(data)
        with self.path.open("rb") as source:
            source.seek(9)
            replacement = self.path.with_suffix(".replacement")
            replacement.write_bytes(b"foreign artifact")
            replacement.replace(self.path)
            result = measure_delivered_glb(source, expected_sha256=hashlib.sha256(data).hexdigest())
            self.assertEqual(source.tell(), 9)
            self.assertFalse(source.closed)
        self.assertEqual(result["triangleCount"], 4)
        self.assertEqual(self.path.read_bytes(), b"foreign artifact")

    def test_foreign_or_stale_glb_hash_is_rejected(self):
        data = glb(*mesh_fixture())
        self.path.write_bytes(data)
        with self.path.open("rb") as source:
            with self.assertRaises(MeasurementError) as caught:
                measure_delivered_glb(source, expected_sha256="a" * 64)
        self.assertEqual(caught.exception.reason_code, "hash-mismatch")

    def test_arbitrary_mesh_json_or_memory_objects_are_not_a_file_producer(self):
        with self.assertRaises(MeasurementError) as caught:
            measure_delivered_glb(io.BytesIO(b"{}"), expected_sha256="a" * 64)
        self.assertEqual(caught.exception.reason_code, "source-unreadable")
        self.reject("invalid-glb", data=b'{"meshes":[{"passed":true}]}')

    def test_corrupted_container_length_is_rejected(self):
        data = bytearray(glb(*mesh_fixture()))
        struct.pack_into("<I", data, 8, len(data) + 4)
        self.reject("invalid-glb", data=bytes(data))

    def test_truncated_position_bytes_and_invalid_view_ranges_are_rejected(self):
        document, binary = mesh_fixture()
        document["bufferViews"][0]["byteLength"] = 12
        self.reject("invalid-accessor-range", document, binary)
        document, binary = mesh_fixture()
        document["bufferViews"][0]["byteOffset"] = len(binary)
        self.reject("invalid-buffer-range", document, binary)

    def test_forged_accessor_minmax_cannot_replace_decoded_bounds(self):
        document, binary = mesh_fixture()
        document["accessors"][0]["max"] = [100, 100, 100]
        self.reject("accessor-bounds-mismatch", document, binary)

    def test_out_of_range_index_and_incomplete_triangle_are_rejected(self):
        self.reject("index-out-of-range", *mesh_fixture(indices=[0, 1, 10]))
        self.reject("invalid-triangle-count", *mesh_fixture(indices=[0, 1]))

    def test_nonfinite_position_bytes_are_rejected(self):
        document, binary = mesh_fixture()
        binary = struct.pack("<f", float("nan")) + binary[4:]
        self.reject("nonfinite-value", document, binary)

    def test_all_extensions_including_optional_compression_are_explicitly_unsupported(self):
        for extension in ("KHR_draco_mesh_compression", "EXT_meshopt_compression", "KHR_mesh_quantization",
                          "EXT_mesh_gpu_instancing", "KHR_materials_unlit"):
            with self.subTest(extension=extension):
                document, binary = mesh_fixture()
                document["extensionsUsed"] = [extension]
                self.reject("unsupported-extension", document, binary)
        document, binary = mesh_fixture()
        document["meshes"][0]["primitives"][0]["extensions"] = {"unlisted-compression": {}}
        self.reject("unsupported-extension", document, binary)

    def test_external_buffers_and_sparse_positions_are_rejected(self):
        document, binary = mesh_fixture()
        document["buffers"][0]["uri"] = "https://example.invalid/mesh.bin"
        self.reject("unsupported-external-buffer", document, binary)
        document, binary = mesh_fixture()
        document["accessors"][0]["sparse"] = {"count": 1}
        self.reject("unsupported-sparse-accessor", document, binary)

    def test_skins_animations_morph_targets_and_joint_attributes_are_not_silently_ignored(self):
        for key in ("skins", "animations"):
            with self.subTest(key=key):
                document, binary = mesh_fixture()
                document[key] = [{}]
                self.reject("unsupported-deformation", document, binary)
        document, binary = mesh_fixture()
        document["nodes"][0]["skin"] = 0
        self.reject("unsupported-deformation", document, binary)
        document, binary = mesh_fixture()
        document["meshes"][0]["primitives"][0]["targets"] = []
        self.reject("unsupported-deformation", document, binary)
        document, binary = mesh_fixture()
        document["meshes"][0]["primitives"][0]["attributes"]["JOINTS_0"] = 0
        self.reject("unsupported-attribute", document, binary)

    def test_points_lines_strips_and_fans_are_rejected_without_skipping(self):
        for mode in (0, 1, 2, 3, 5, 6):
            with self.subTest(mode=mode):
                document, binary = mesh_fixture()
                document["meshes"][0]["primitives"][0]["mode"] = mode
                self.reject("unsupported-primitive-mode", document, binary)

    def test_cycles_shared_children_and_unreachable_nodes_are_rejected(self):
        document, binary = mesh_fixture()
        document["nodes"][0]["children"] = [0]
        self.reject("cyclic-or-shared-node", document, binary)
        document, binary = mesh_fixture()
        document["nodes"][0]["children"] = [1, 1]
        document["nodes"].append({})
        self.reject("cyclic-or-shared-node", document, binary)
        document, binary = mesh_fixture()
        document["nodes"].append({"mesh": 0})
        self.reject("unsupported-unreachable-node", document, binary)

    def test_unused_mesh_and_accessor_are_explicitly_rejected(self):
        document, binary = mesh_fixture()
        document["meshes"].append(deepcopy(document["meshes"][0]))
        self.reject("unsupported-unused-mesh", document, binary)
        document, binary = mesh_fixture()
        document["accessors"].append(deepcopy(document["accessors"][0]))
        self.reject("unsupported-unused-accessor", document, binary)

    def test_multiple_scenes_or_absent_default_scene_are_rejected(self):
        document, binary = mesh_fixture()
        document["scenes"].append({"nodes": [0]})
        self.reject("unsupported-scene-selection", document, binary)
        document, binary = mesh_fixture()
        del document["scene"]
        self.reject("unsupported-scene-selection", document, binary)

    def test_invalid_transforms_do_not_create_fake_measurements(self):
        for transform, reason in (({"scale": [1, 0, 1]}, "singular-transform"),
                                  ({"rotation": [0, 0, 0, 2]}, "invalid-quaternion"),
                                  ({"translation": [float("inf"), 0, 0]}, "nonfinite-value")):
            with self.subTest(transform=transform):
                document, binary = mesh_fixture()
                document["nodes"][0].update(transform)
                self.reject(reason, document, binary)
        document, binary = mesh_fixture()
        document["nodes"][0].update(matrix=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], scale=[1, 1, 1])
        self.reject("conflicting-transforms", document, binary)

    def test_resource_limit_is_explicit_not_partial_geometry(self):
        with patch("stage4_review.measure_delivered_glb.MAX_TRIANGLES", 3):
            self.reject("resource-limit")

    def test_standard_normals_are_format_checked_but_not_claimed_as_deformation_or_quality(self):
        document, binary = mesh_fixture()
        normals = struct.pack("<12f", *([0, 0, 1] * 4))
        document["bufferViews"].append({"buffer": 0, "byteOffset": len(binary),
                                       "byteLength": len(normals), "target": 34962})
        document["accessors"].append({"bufferView": 2, "componentType": 5126, "type": "VEC3", "count": 4})
        document["meshes"][0]["primitives"][0]["attributes"]["NORMAL"] = 2
        binary += normals
        document["buffers"][0]["byteLength"] = len(binary)
        result = self.measure(document, binary)
        self.assertEqual(result["instances"][0]["attributes"][0]["formatCheckedUnmeasuredAttributes"], ["NORMAL"])
        self.assertEqual(result["coverage"]["skinDeformation"], "unevaluated")

    def test_every_primitive_is_measured_and_later_unsupported_primitive_cannot_be_skipped(self):
        document, binary = mesh_fixture()
        primitive = deepcopy(document["meshes"][0]["primitives"][0])
        document["meshes"][0]["primitives"].append(primitive)
        result = self.measure(document, binary)
        self.assertEqual(result["triangleCount"], 8)
        self.assertEqual(result["instances"][0]["primitiveCount"], 2)
        primitive["mode"] = 1
        self.reject("unsupported-primitive-mode", document, binary)

    def test_misaligned_accessor_and_undersized_vertex_stride_are_rejected(self):
        document, binary = mesh_fixture()
        document["accessors"][0]["byteOffset"] = 1
        self.reject("invalid-accessor-alignment", document, binary)
        document, binary = mesh_fixture()
        document["bufferViews"][0]["byteStride"] = 8
        self.reject("invalid-accessor-alignment", document, binary)

    def test_non_affine_matrix_is_explicitly_unsupported(self):
        document, binary = mesh_fixture()
        document["nodes"][0]["matrix"] = [1, 0, 0, 1, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        self.reject("unsupported-projective-transform", document, binary)

    def test_degenerate_world_transform_from_underflow_is_not_an_approximated_mesh(self):
        document, binary = mesh_fixture()
        document["nodes"] = [{"scale": [1e-80, 1e-80, 1e-80], "children": [1]},
                             {"mesh": 0, "scale": [1e-80, 1e-80, 1e-80]}]
        self.reject("singular-transform", document, binary)

    def test_local_matrix_shear_is_rejected_but_hierarchical_trs_shear_is_measured(self):
        document, binary = mesh_fixture()
        document["nodes"][0]["matrix"] = [1, 0, 0, 0, 1, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        self.reject("unsupported-sheared-local-matrix", document, binary)
        document, binary = mesh_fixture()
        document["nodes"] = [{"scale": [2, 1, 1], "children": [1]},
                             {"mesh": 0, "rotation": [0, 0, math.sin(math.pi / 8), math.cos(math.pi / 8)]}]
        result = self.measure(document, binary)
        self.assertAlmostEqual(result["extents"]["width"], 2 * math.sqrt(2))
        self.assertAlmostEqual(result["extents"]["height"], math.sqrt(0.5))


if __name__ == "__main__":
    unittest.main()
