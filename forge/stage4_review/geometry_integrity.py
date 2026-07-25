from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import Any, TypeAlias

SEAM_MIN_OVERLAP = 0.02
SEAM_MAX_OVERLAP = 0.05
SEAM_SOURCE = "grimoire/build/geometry_patterns.md"
EdgeKey: TypeAlias = tuple[tuple[float, ...], tuple[float, ...]]


def _vertex_key(vertex: Any, precision: int = 6) -> tuple[float, ...]:
    if not isinstance(vertex, (list, tuple)) or len(vertex) < 3:
        raise ValueError("vertices must contain 3D positions")
    return tuple(round(float(value), precision) for value in vertex[:3])


def mesh_edge_counts(vertices: list[Any], indices: list[Any], *, precision: int = 6) -> dict[str, int]:
    keys = [_vertex_key(vertex, precision) for vertex in vertices]
    edges: Counter[EdgeKey] = Counter()
    triangles: list[Any] = indices if indices and isinstance(indices[0], (list, tuple)) else [indices[i:i + 3] for i in range(0, len(indices), 3)]
    if any(len(triangle) != 3 for triangle in triangles):
        raise ValueError("triangle indices must contain groups of three")
    for triangle in triangles:
        points = [keys[int(index)] for index in triangle]
        for left, right in ((points[0], points[1]), (points[1], points[2]), (points[2], points[0])):
            edge: EdgeKey = (left, right) if left <= right else (right, left)
            edges[edge] += 1
    return {"boundaryEdges": sum(count == 1 for count in edges.values()),
            "nonManifoldEdges": sum(count > 2 for count in edges.values()), "edgeCount": len(edges)}


def _bounds(mesh: dict[str, Any]) -> list[list[float]] | None:
    bounds = mesh.get("bounds") or mesh.get("boundingBox")
    if isinstance(bounds, dict):
        bounds = [bounds.get("min"), bounds.get("max")]
    if isinstance(bounds, (list, tuple)) and len(bounds) == 2 and all(isinstance(point, (list, tuple)) and len(point) >= 3 for point in bounds):
        points = [point for point in bounds if isinstance(point, (list, tuple))]
        if len(points) == 2:
            return [[float(value) for value in points[index][:3]] for index in range(2)]
    vertices = mesh.get("vertices")
    if not isinstance(vertices, list) or not vertices:
        return None
    points = [_vertex_key(point, 12) for point in vertices]
    return [[min(point[axis] for point in points) for axis in range(3)], [max(point[axis] for point in points) for axis in range(3)]]


def _axis_index(value: Any) -> int:
    if isinstance(value, int) and value in (0, 1, 2):
        return value
    return {"x": 0, "y": 1, "z": 2, "long": 0, "length": 0, "thickness": 2}.get(str(value or "").lower(), 0)


def _seam_overlap(first: dict[str, Any], second: dict[str, Any]) -> tuple[float, int] | None:
    raw_attachment = second.get("attachment")
    attachment: dict[str, Any] = raw_attachment if isinstance(raw_attachment, dict) else {}
    axis = attachment.get("seamAxis", second.get("seamAxis", first.get("seamAxis")))
    a, b = _bounds(first), _bounds(second)
    if axis is None or a is None or b is None:
        return None
    index = _axis_index(axis)
    return min(a[1][index], b[1][index]) - max(a[0][index], b[0][index]), index


def _blade_thickness(mesh: dict[str, Any]) -> dict[str, Any]:
    samples = mesh.get("thicknessSamples") or mesh.get("crossSections")
    grind_samples = mesh.get("grindSamples") or samples
    distal_samples = mesh.get("distalThicknessSamples") or samples
    values = []
    def numeric_values(source: Any) -> list[float]:
        result: list[float] = []
        if isinstance(source, list):
            for sample in source:
                value = sample.get("thickness") if isinstance(sample, dict) else sample
                if isinstance(value, (int, float)):
                    result.append(float(value))
        return result
    if isinstance(samples, list):
        for sample in samples:
            value = sample.get("thickness") if isinstance(sample, dict) else sample
            if isinstance(value, (int, float)):
                values.append(float(value))
    grind_values = numeric_values(grind_samples)
    distal_values = numeric_values(distal_samples)
    grind_variation = max(grind_values) - min(grind_values) if grind_values else 0.0
    distal_variation = max(distal_values) - min(distal_values) if distal_values else 0.0
    return {"grindVariation": grind_variation, "distalVariation": distal_variation,
            "constantGrind": grind_variation <= 1e-6, "missingDistalTaper": distal_variation <= 1e-6,
            "reported": bool(values)}


def measure_geometry_integrity(payload: dict[str, Any]) -> dict[str, Any]:
    meshes = payload.get("meshes") or payload.get("components") or []
    if isinstance(meshes, dict):
        meshes = [dict(value, id=key) if isinstance(value, dict) else {"id": key} for key, value in meshes.items()]
    if not isinstance(meshes, list):
        raise ValueError("built geometry meshes/components must be an array or object")
    reports: list[dict[str, Any]] = []
    failures: list[str] = []
    for index, mesh in enumerate(meshes):
        if not isinstance(mesh, dict):
            continue
        item: dict[str, Any] = {"id": mesh.get("id") or mesh.get("name") or f"mesh-{index}", "realization": mesh.get("realization", "separate-geometry")}
        if isinstance(mesh.get("vertices"), list) and isinstance(mesh.get("indices"), list):
            item.update(mesh_edge_counts(mesh["vertices"], mesh["indices"]))
        else:
            item.update({"boundaryEdges": None, "nonManifoldEdges": None, "note": "mesh topology not supplied"})
        role = str(mesh.get("role") or "").lower()
        if role == "blade" or "blade" in str(item["id"]).lower():
            item["thickness"] = _blade_thickness(mesh)
            if item["thickness"]["constantGrind"] or item["thickness"]["missingDistalTaper"]:
                failures.append(f"{item['id']}: blade has constant thickness or missing distal taper")
        if item["realization"] == "separate-geometry" and item.get("boundaryEdges", 0) not in (0, None):
            failures.append(f"{item['id']}: openEdges={item['boundaryEdges']} (separate geometry)")
        reports.append(item)
    seams: list[dict[str, Any]] = []
    for first, second in combinations([item for item in meshes if isinstance(item, dict)], 2):
        relation = second.get("adjacentTo") or first.get("adjacentTo")
        attached = isinstance(second.get("attachment"), dict) and second["attachment"].get("parentId") == first.get("id")
        if relation not in (None, first.get("id"), second.get("id")) and not attached:
            continue
        result = _seam_overlap(first, second)
        if result is None:
            continue
        overlap, axis = result
        seam = {"a": first.get("id"), "b": second.get("id"), "axis": axis, "overlap": round(overlap, 6), "minimum": SEAM_MIN_OVERLAP, "source": SEAM_SOURCE}
        seams.append(seam)
        if overlap < SEAM_MIN_OVERLAP:
            failures.append(f"seam {seam['a']}↔{seam['b']}: overlap={overlap:.3f} < {SEAM_MIN_OVERLAP:.2f}")
    return {"passed": not failures, "source": SEAM_SOURCE, "minimumSeamOverlap": SEAM_MIN_OVERLAP, "maximumDocumentedSeamOverlap": SEAM_MAX_OVERLAP, "meshes": reports, "seams": seams, "failures": failures}
