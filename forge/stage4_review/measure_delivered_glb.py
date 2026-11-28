"""Measure static triangle geometry from an already-open, hash-validated GLB.

``measure_delivered_glb(file, *, expected_sha256)`` reads a regular file descriptor
without changing its offset. The authority must invoke this producer itself;
editable saved output is not proof of execution or capture provenance.

Supported: one active scene, static tree/mesh instancing, decomposable TRS matrices
or TRS transforms, embedded dense FLOAT positions, optional standard vertex attributes,
and TRIANGLES with unsigned 8/16/32-bit indices or non-indexed triangles.
All extensions, external buffers, sparse accessors, skins, animations, morphs,
non-triangle modes and disconnected scene data are explicitly rejected.

Bounds use actual referenced vertices, never accessor min/max as measurements.
Accessor min/max are checked against decoded values. Topology is reported both
by accessor indices and by the repository gate's rounded-position welding (six
decimals); these are different notions, neither is terminal acceptance. Volume
is reported only for closed consistently oriented indexed topology. This does
not measure silhouette area, self-intersections, deformation, camera fidelity,
materials, screenshots, semantic/critical quality, or final acceptance.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import os
import re
import stat
import struct

from _shared.glb_container import parse_glb
from stage4_review.geometry_integrity import mesh_edge_counts


MAX_BYTES = 128 * 1024 * 1024
MAX_VERTICES = 1_000_000
MAX_TRIANGLES = 1_000_000
_FORMATS = {5121: ("B", 1), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
_WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
_IDENTITY = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
_ATTRIBUTES = {"POSITION", "NORMAL", "TANGENT", "TEXCOORD_0", "TEXCOORD_1", "COLOR_0"}


class MeasurementError(ValueError):
    """A rejected or unsupported input; no partial geometry report is returned."""

    def __init__(self, reason_code, field):
        self.reason_code, self.field = reason_code, field
        super().__init__(f"{reason_code}: {field}")


def _require(condition, reason, field):
    if not condition:
        raise MeasurementError(reason, field)


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _item(values, index, field):
    _require(type(values) is list and _integer(index) and index < len(values), "invalid-reference", field)
    value = values[index]
    _require(type(value) is dict, "invalid-object", field)
    return value


class _Snapshot:
    """Adapt immutable descriptor bytes to the repository parser's read_bytes API."""

    def __init__(self, data):
        self.data = data

    def read_bytes(self):
        return self.data


def _snapshot(source, expected):
    _require(type(expected) is str and re.fullmatch(r"[0-9a-f]{64}", expected) is not None,
             "invalid-sha256", "expected_sha256")
    try:
        descriptor = source.fileno()
        before = os.fstat(descriptor)
        _require(stat.S_ISREG(before.st_mode), "not-regular-file", "source")
        _require(0 < before.st_size <= MAX_BYTES, "resource-limit", "source.bytes")
        chunks, position = [], 0
        while position < before.st_size:
            chunk = os.pread(descriptor, min(1024 * 1024, before.st_size - position), position)
            _require(bool(chunk), "source-changed", "source")
            chunks.append(chunk)
            position += len(chunk)
        after = os.fstat(descriptor)
    except (OSError, AttributeError, ValueError) as exc:
        if isinstance(exc, MeasurementError):
            raise
        raise MeasurementError("source-unreadable", "source") from exc
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    _require(identity(before) == identity(after), "source-changed", "source")
    data = b"".join(chunks)
    _require(hashlib.sha256(data).hexdigest() == expected, "hash-mismatch", "source")
    return data


def _no_extensions(value, field="document", depth=0):
    _require(depth <= 128, "resource-limit", field)
    if type(value) is dict:
        for key, item in value.items():
            if key in ("extensions", "extensionsUsed", "extensionsRequired"):
                _require(type(item) is (dict if key == "extensions" else list),
                         "invalid-extension-field", field + "." + key)
                _require(not item, "unsupported-extension", field + "." + key)
            _no_extensions(item, field + "." + key, depth + 1)
    elif type(value) is list:
        for index, item in enumerate(value):
            _no_extensions(item, field + "." + str(index), depth + 1)
    elif type(value) is float:
        _require(math.isfinite(value), "nonfinite-value", field)


def _parse(data):
    try:
        document, binary, metadata = parse_glb(_Snapshot(data))
        # The common parser checks container bounds; enforce GLB ordering/alignment
        # and strict JSON here rather than trusting ignored chunks or duplicate keys.
        chunks = metadata["chunks"]
        _require([chunk["type"] for chunk in chunks] == ["JSON", "BIN\x00"]
                 and all(chunk["bytes"] % 4 == 0 for chunk in chunks), "unsupported-container", "chunks")
        def pairs(items):
            result = {}
            for key, value in items:
                _require(key not in result, "duplicate-json-key", key)
                result[key] = value
            return result
        length = struct.unpack_from("<I", data, 12)[0]
        json.loads(data[20:20 + length], object_pairs_hook=pairs,
                   parse_constant=lambda value: (_ for _ in ()).throw(MeasurementError("nonfinite-value", value)))
    except (ValueError, UnicodeError, struct.error, RecursionError) as exc:
        if isinstance(exc, MeasurementError):
            raise
        raise MeasurementError("invalid-glb", "container") from exc
    _no_extensions(document)
    _require(type(document.get("asset")) is dict and document["asset"].get("version") == "2.0"
             and document["asset"].get("minVersion", "2.0") == "2.0", "unsupported-version", "asset")
    for field in ("skins", "animations"):
        _require(not document.get(field), "unsupported-deformation", field)
    buffers = document.get("buffers")
    _require(type(buffers) is list and len(buffers) == 1 and type(buffers[0]) is dict,
             "unsupported-buffer", "buffers")
    buffer = buffers[0]
    length = buffer.get("byteLength")
    _require("uri" not in buffer, "unsupported-external-buffer", "buffers.0.uri")
    _require(_integer(length, 1) and length <= len(binary) <= length + 3,
             "invalid-buffer-range", "buffers.0.byteLength")
    _require(not any(binary[length:]), "invalid-buffer-padding", "buffers.0")
    views = document.get("bufferViews")
    _require(type(views) is list and views, "missing-buffer-views", "bufferViews")
    for index, view in enumerate(views):
        field = f"bufferViews.{index}"
        _require(type(view) is dict and type(view.get("buffer")) is int and view["buffer"] == 0,
                 "invalid-buffer-view", field)
        offset, size = view.get("byteOffset", 0), view.get("byteLength")
        _require(_integer(offset) and _integer(size, 1) and offset + size <= length,
                 "invalid-buffer-range", field)
        if "byteStride" in view:
            stride = view["byteStride"]
            _require(_integer(stride, 4) and stride <= 252 and stride % 4 == 0,
                     "invalid-byte-stride", field)
        if "target" in view:
            _require(type(view["target"]) is int and view["target"] in (34962, 34963), "invalid-target", field)
    return document, binary


def _accessor(document, binary, index, *, vertex):
    field = f"accessors.{index}"
    accessor = _item(document.get("accessors"), index, field)
    _require("sparse" not in accessor, "unsupported-sparse-accessor", field)
    _require(type(accessor.get("componentType")) is int and accessor["componentType"] in _FORMATS
             and type(accessor.get("type")) is str and accessor["type"] in _WIDTHS,
             "unsupported-accessor-format", field)
    component, size = _FORMATS[accessor["componentType"]]
    width = _WIDTHS[accessor["type"]]
    count, offset = accessor.get("count"), accessor.get("byteOffset", 0)
    _require(_integer(count, 1) and count <= MAX_VERTICES * 3 and _integer(offset), "invalid-accessor", field)
    normalized = accessor.get("normalized", False)
    _require(type(normalized) is bool and not (normalized and component == "f"), "invalid-normalization", field)
    view = _item(document["bufferViews"], accessor.get("bufferView"), field + ".bufferView")
    _require(view.get("target", 34962 if vertex else 34963) == (34962 if vertex else 34963), "invalid-target", field)
    stride = view.get("byteStride", size * width)
    _require(vertex or "byteStride" not in view, "unsupported-index-stride", field)
    _require(stride >= size * width and stride % size == 0 and offset % size == 0
             and (view.get("byteOffset", 0) + offset) % size == 0, "invalid-accessor-alignment", field)
    _require(not vertex or ((view.get("byteOffset", 0) + offset) % 4 == 0 and stride % 4 == 0),
             "invalid-vertex-alignment", field)
    _require(offset + (count - 1) * stride + size * width <= view["byteLength"], "invalid-accessor-range", field)
    decoder = struct.Struct("<" + component * width)
    start = view.get("byteOffset", 0) + offset
    values = [decoder.unpack_from(binary, start + row * stride) for row in range(count)]
    _require(all(_finite(value) for row in values for value in row), "nonfinite-value", field)
    for name, operation in (("min", min), ("max", max)):
        declared = accessor.get(name)
        if declared is not None:
            _require(type(declared) is list and len(declared) == width and all(_finite(v) for v in declared),
                     "invalid-accessor-bounds", field + "." + name)
            actual = [operation(row[axis] for row in values) for axis in range(width)]
            _require(all(math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-7) for a, b in zip(actual, declared)),
                     "accessor-bounds-mismatch", field + "." + name)
    return accessor, values


def _determinant(matrix):
    return (matrix[0][0] * (matrix[1][1]*matrix[2][2] - matrix[1][2]*matrix[2][1])
            - matrix[0][1] * (matrix[1][0]*matrix[2][2] - matrix[1][2]*matrix[2][0])
            + matrix[0][2] * (matrix[1][0]*matrix[2][1] - matrix[1][1]*matrix[2][0]))


def _local_matrix(node, field):
    _require("skin" not in node and "weights" not in node, "unsupported-deformation", field)
    if "matrix" in node:
        _require(not any(key in node for key in ("translation", "rotation", "scale")), "conflicting-transforms", field)
        values = node["matrix"]
        _require(type(values) is list and len(values) == 16 and all(_finite(v) for v in values), "invalid-transform", field)
        matrix = [[values[column * 4 + row] for column in range(4)] for row in range(4)]
        _require(matrix[3] == [0, 0, 0, 1], "unsupported-projective-transform", field)
        columns = [tuple(matrix[row][column] for row in range(3)) for column in range(3)]
        lengths = [math.hypot(*column) for column in columns]
        _require(all(_finite(length) and length > 0 for length in lengths), "singular-transform", field)
        unit = [tuple(value / length for value in column) for column, length in zip(columns, lengths)]
        _require(all(abs(sum(unit[a][axis] * unit[b][axis] for axis in range(3))) <= 1e-6
                     for a, b in ((0, 1), (0, 2), (1, 2))), "unsupported-sheared-local-matrix", field)
    else:
        translation, rotation, scale = (node.get(key, default) for key, default in (
            ("translation", [0, 0, 0]), ("rotation", [0, 0, 0, 1]), ("scale", [1, 1, 1])))
        for value, width in ((translation, 3), (rotation, 4), (scale, 3)):
            _require(type(value) is list and len(value) == width and all(_finite(v) for v in value), "invalid-transform", field)
        _require(math.isclose(sum(v * v for v in rotation), 1, rel_tol=0, abs_tol=1e-6), "invalid-quaternion", field)
        x, y, z, w = rotation
        rows = [[1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w)],
                [2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w)],
                [2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y)]]
        matrix = [[rows[row][column] * scale[column] for column in range(3)] + [translation[row]] for row in range(3)]
        matrix.append([0, 0, 0, 1])
    determinant = _determinant(matrix)
    _require(_finite(determinant) and determinant != 0, "singular-transform", field)
    return matrix


def _multiply(left, right):
    result = [[sum(left[row][k] * right[k][column] for k in range(4)) for column in range(4)] for row in range(4)]
    _require(all(_finite(v) for row in result for v in row), "nonfinite-transform", "worldMatrix")
    determinant = _determinant(result)
    _require(_finite(determinant) and determinant != 0, "singular-transform", "worldMatrix")
    return result


def _topology(vertices, triangles):
    counts, directions = Counter(), Counter()
    degenerate, area, volume_terms = 0, 0.0, []
    origin = vertices[triangles[0][0]]
    for a, b, c in triangles:
        for left, right in ((a, b), (b, c), (c, a)):
            edge = (min(left, right), max(left, right))
            counts[edge] += 1
            directions[edge] += 1 if left < right else -1
        points = [vertices[index] for index in (a, b, c)]
        u = [points[1][axis] - points[0][axis] for axis in range(3)]
        v = [points[2][axis] - points[0][axis] for axis in range(3)]
        cross = (u[1]*v[2] - u[2]*v[1], u[2]*v[0] - u[0]*v[2], u[0]*v[1] - u[1]*v[0])
        magnitude = math.hypot(*cross)
        _require(_finite(magnitude), "nonfinite-measurement", "triangleArea")
        degenerate += magnitude == 0
        area += magnitude / 2
        # Closed-surface tetrahedral sum, relative to a local origin to avoid
        # introducing translation-dependent cancellation into the measurement.
        relative = [[point[axis] - origin[axis] for axis in range(3)] for point in points]
        p, q, r = relative
        term = (p[0]*(q[1]*r[2]-q[2]*r[1]) + p[1]*(q[2]*r[0]-q[0]*r[2])
                + p[2]*(q[0]*r[1]-q[1]*r[0])) / 6
        _require(_finite(term), "nonfinite-measurement", "triangleVolume")
        volume_terms.append(term)
    boundary = sum(count == 1 for count in counts.values())
    nonmanifold = sum(count > 2 for count in counts.values())
    inconsistent = sum(count == 2 and directions[edge] != 0 for edge, count in counts.items())
    closed = not (boundary or nonmanifold or inconsistent or degenerate)
    try:
        volume = math.fsum(volume_terms) if closed else None
    except OverflowError:
        raise MeasurementError("nonfinite-measurement", "volume") from None
    _require(_finite(area) and (volume is None or _finite(volume)), "nonfinite-measurement", "geometry")
    return {"edgeCount": len(counts), "boundaryEdges": boundary, "nonManifoldEdges": nonmanifold,
            "inconsistentWindingEdges": inconsistent, "degenerateTriangleCount": degenerate,
            "surfaceArea": area, "signedVolume": volume}


def _mesh(document, binary, mesh_index, matrix, node_index):
    field = f"meshes.{mesh_index}"
    mesh = _item(document.get("meshes"), mesh_index, field)
    _require("weights" not in mesh, "unsupported-deformation", field)
    primitives = mesh.get("primitives")
    _require(type(primitives) is list and primitives, "missing-primitives", field)
    vertices, triangles, attributes_report, accessor_ids = [], [], [], set()
    for primitive_index, primitive in enumerate(primitives):
        label = field + f".primitives.{primitive_index}"
        _require(type(primitive) is dict, "invalid-primitive", label)
        _require(type(primitive.get("mode", 4)) is int and primitive.get("mode", 4) == 4, "unsupported-primitive-mode", label)
        _require("targets" not in primitive, "unsupported-deformation", label)
        attributes = primitive.get("attributes")
        _require(type(attributes) is dict and "POSITION" in attributes, "missing-positions", label)
        _require(set(attributes) <= _ATTRIBUTES, "unsupported-attribute", label)
        accessor, positions = _accessor(document, binary, attributes["POSITION"], vertex=True)
        _require(accessor["componentType"] == 5126 and accessor["type"] == "VEC3"
                 and "min" in accessor and "max" in accessor, "unsupported-position-format", label)
        _require(len(vertices) + len(positions) <= MAX_VERTICES, "resource-limit", label)
        accessor_ids.add(attributes["POSITION"])
        for name, index in attributes.items():
            if name == "POSITION":
                continue
            other, values = _accessor(document, binary, index, vertex=True)
            accessor_ids.add(index)
            _require(len(values) == len(positions), "attribute-count-mismatch", label + "." + name)
            width = {"NORMAL": ["VEC3"], "TANGENT": ["VEC4"], "COLOR_0": ["VEC3", "VEC4"]}.get(name, ["VEC2"])
            floats = name in ("NORMAL", "TANGENT")
            _require(other["type"] in width and (other["componentType"] == 5126 or
                     (not floats and other["componentType"] in (5121, 5123) and other.get("normalized") is True)),
                     "unsupported-attribute-format", label + "." + name)
        if "indices" in primitive:
            indices_accessor, values = _accessor(document, binary, primitive["indices"], vertex=False)
            _require(indices_accessor["componentType"] in (5121, 5123, 5125)
                     and indices_accessor["type"] == "SCALAR" and not indices_accessor.get("normalized", False),
                     "unsupported-index-format", label)
            accessor_ids.add(primitive["indices"])
            indices = [value[0] for value in values]
            restart = (1 << (8 * _FORMATS[indices_accessor["componentType"]][1])) - 1
            _require(all(index < len(positions) and index != restart for index in indices), "index-out-of-range", label)
        else:
            indices = list(range(len(positions)))
        _require(len(indices) % 3 == 0, "invalid-triangle-count", label)
        _require(len(triangles) + len(indices) // 3 <= MAX_TRIANGLES, "resource-limit", label)
        offset = len(vertices)
        for position in positions:
            point = tuple(sum(matrix[row][axis] * position[axis] for axis in range(3)) + matrix[row][3] for row in range(3))
            _require(all(_finite(v) for v in point), "nonfinite-transform", label)
            vertices.append(point)
        triangles.extend(tuple(index + offset for index in indices[start:start+3]) for start in range(0, len(indices), 3))
        attributes_report.append({"primitive": primitive_index, "formatCheckedUnmeasuredAttributes": sorted(set(attributes) - {"POSITION"})})
    referenced = {index for triangle in triangles for index in triangle}
    bounds = [[operation(vertices[index][axis] for index in referenced) for axis in range(3)] for operation in (min, max)]
    extents = [bounds[1][axis] - bounds[0][axis] for axis in range(3)]
    _require(all(_finite(v) for v in extents), "nonfinite-measurement", field)
    return {"node": node_index, "mesh": mesh_index, "primitiveCount": len(primitives),
            "declaredVertexCount": len(vertices), "referencedVertexCount": len(referenced),
            "triangleCount": len(triangles), "bounds": {"min": bounds[0], "max": bounds[1]},
            "extents": dict(zip(("width", "height", "depth"), extents)),
            "indexedTopology": _topology(vertices, triangles),
            "positionWeldedTopology": {"decimalPrecision": 6, **mesh_edge_counts(vertices, triangles)},
            "attributes": attributes_report, "accessorIds": sorted(accessor_ids)}


def measure_delivered_glb(source, *, expected_sha256):
    """Return nonterminal measurements or raise MeasurementError; never partial-pass.

    Coordinates/areas/volumes are in authored scene units, squared/cubed. No D18
    scale or frame metadata is inferred. The caller retains its descriptor and
    must revalidate the expected hash at every later authority boundary.
    """
    data = _snapshot(source, expected_sha256)
    document, binary = _parse(data)
    scenes, nodes, meshes = (document.get(key) for key in ("scenes", "nodes", "meshes"))
    _require(type(scenes) is list and len(scenes) == 1 and type(document.get("scene")) is int
             and document["scene"] == 0, "unsupported-scene-selection", "scenes")
    _require(type(nodes) is list and nodes and type(meshes) is list and meshes, "missing-geometry", "nodes/meshes")
    _require(len(nodes) <= 10000, "resource-limit", "nodes")
    scene = _item(scenes, 0, "scenes.0")
    roots = scene.get("nodes")
    _require(type(roots) is list and roots, "missing-scene-roots", "scenes.0.nodes")
    visited, used_meshes, reports = set(), set(), []
    total_vertices, total_triangles = 0, 0
    pending = [(root, _IDENTITY, 0) for root in reversed(roots)]
    while pending:
        index, parent, depth = pending.pop()
        node = _item(nodes, index, "node")
        _require(index not in visited, "cyclic-or-shared-node", f"nodes.{index}")
        _require(depth <= 256, "resource-limit", "scene.depth")
        visited.add(index)
        matrix = _multiply(parent, _local_matrix(node, f"nodes.{index}"))
        if "mesh" in node:
            mesh_index = node["mesh"]
            report = _mesh(document, binary, mesh_index, matrix, index)
            total_vertices += report["declaredVertexCount"]
            total_triangles += report["triangleCount"]
            _require(total_vertices <= MAX_VERTICES and total_triangles <= MAX_TRIANGLES,
                     "resource-limit", "scene.instances")
            reports.append(report)
            used_meshes.add(mesh_index)
        children = node.get("children", [])
        _require(type(children) is list, "invalid-children", f"nodes.{index}")
        pending.extend((child, matrix, depth + 1) for child in reversed(children))
    _require(len(visited) == len(nodes), "unsupported-unreachable-node", "nodes")
    _require(used_meshes == set(range(len(meshes))), "unsupported-unused-mesh", "meshes")
    _require(bool(reports), "missing-geometry", "scene")
    accessors = document.get("accessors")
    _require(type(accessors) is list, "invalid-accessors", "accessors")
    used_accessors = {index for report in reports for index in report["accessorIds"]}
    _require(used_accessors == set(range(len(accessors))), "unsupported-unused-accessor", "accessors")
    bounds = {name: [operation(report["bounds"][name][axis] for report in reports) for axis in range(3)]
              for name, operation in (("min", min), ("max", max))}
    extents = [bounds["max"][axis] - bounds["min"][axis] for axis in range(3)]
    _require(all(_finite(v) for v in extents), "nonfinite-measurement", "scene.bounds")
    width, height, depth = extents
    ratio = depth / max(width, height) if max(width, height) > 0 else None
    _require(ratio is None or _finite(ratio), "nonfinite-measurement", "depthRatio")
    return {"kind": "img2.delivered-glb-geometry", "version": 1,
            "input": {"sha256": expected_sha256, "bytes": len(data)},
            "coverage": {"scope": "complete-active-static-triangle-scene", "scene": 0,
                         "nodeCount": len(visited), "meshInstanceCount": len(reports),
                         "boundsSource": "decoded-referenced-triangle-vertices", "units": "authored-scene-units",
                         "selfIntersection": "unevaluated", "skinDeformation": "unevaluated",
                         "appearance": "unevaluated", "screenshots": "unevaluated",
                         "semanticQuality": "unevaluated", "acceptance": "not-assessed"},
            "bounds": bounds, "extents": dict(zip(("width", "height", "depth"), extents)),
            "depthToMaximumWidthHeightRatio": ratio,
            "triangleCount": sum(report["triangleCount"] for report in reports), "instances": reports}
