#!/usr/bin/env python3
"""Probe a binary glTF (GLB) as an explicit reference-mesh artifact.

This is metadata/provenance inspection only. It does not import, render, or copy
the source mesh into a procedural factory. The browser Three.js route remains the
authority for reference and output pixels.

Pure Python 3.10+ standard library; no PIL, numpy, Blender, or glTF dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any

try:
    from .semantic_decomposition import assess_semantic_decomposition
except ImportError:  # pragma: no cover - direct script execution
    from semantic_decomposition import assess_semantic_decomposition


GLB_MAGIC = b"glTF"
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
MAX_FALLBACK_BOUND_SAMPLES = 1_000_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _chunk_type_name(chunk_type: int) -> str:
    try:
        return chunk_type.to_bytes(4, "little").decode("ascii", errors="replace")
    except OverflowError:
        return hex(chunk_type)


def parse_glb(path: Path) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    data = path.read_bytes()
    if len(data) < 12:
        raise ValueError("GLB is shorter than the 12-byte header")
    magic, version, declared_length = struct.unpack_from("<4sII", data, 0)
    if magic != GLB_MAGIC:
        raise ValueError("file does not start with the glTF binary magic")
    if version != 2:
        raise ValueError(f"unsupported GLB version {version}; expected 2")
    if declared_length != len(data):
        raise ValueError(f"header length {declared_length} does not match file size {len(data)}")

    cursor = 12
    json_payload: bytes | None = None
    bin_payload = b""
    chunks: list[dict[str, Any]] = []
    while cursor < len(data):
        if cursor + 8 > len(data):
            raise ValueError("truncated GLB chunk header")
        chunk_length, chunk_type = struct.unpack_from("<II", data, cursor)
        cursor += 8
        end = cursor + chunk_length
        if end > len(data):
            raise ValueError("GLB chunk extends beyond the declared file")
        payload = data[cursor:end]
        cursor = end
        chunks.append({"type": _chunk_type_name(chunk_type), "bytes": chunk_length})
        if chunk_type == JSON_CHUNK:
            if json_payload is not None:
                raise ValueError("GLB contains more than one JSON chunk")
            json_payload = payload.rstrip(b" \t\r\n\x00")
        elif chunk_type == BIN_CHUNK:
            if bin_payload:
                raise ValueError("GLB contains more than one BIN chunk")
            bin_payload = payload

    if json_payload is None:
        raise ValueError("GLB has no JSON chunk")
    try:
        document = json.loads(json_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"GLB JSON chunk is invalid: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("GLB JSON root must be an object")
    return document, bin_payload, {
        "version": version,
        "declaredLength": declared_length,
        "jsonChunkBytes": len(json_payload),
        "binChunkBytes": len(bin_payload),
        "chunks": chunks,
    }


def _buffer_view_bytes(document: dict[str, Any], bin_payload: bytes, view_index: int) -> bytes:
    views = document.get("bufferViews", [])
    if not isinstance(views, list) or not 0 <= view_index < len(views):
        raise ValueError(f"accessor references missing bufferView {view_index}")
    view = views[view_index]
    if not isinstance(view, dict):
        raise ValueError(f"bufferView {view_index} is not an object")
    if view.get("buffer", 0) != 0:
        raise ValueError("external/multi-buffer GLB references are not supported by the stdlib probe")
    start = int(view.get("byteOffset", 0))
    length = int(view.get("byteLength", 0))
    end = start + length
    if start < 0 or end > len(bin_payload):
        raise ValueError(f"bufferView {view_index} exceeds the GLB BIN chunk")
    return bin_payload[start:end]


_COMPONENT_FORMATS: dict[int, tuple[str, int]] = {
    5120: ("b", 1),
    5121: ("B", 1),
    5122: ("h", 2),
    5123: ("H", 2),
    5124: ("i", 4),
    5125: ("I", 4),
    5126: ("f", 4),
}
_TYPE_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


def _accessor_bounds(
    document: dict[str, Any], bin_payload: bytes, accessor_index: int
) -> tuple[list[float], list[float], list[str]]:
    accessors = document.get("accessors", [])
    warnings: list[str] = []
    if not isinstance(accessors, list) or not 0 <= accessor_index < len(accessors):
        raise ValueError(f"missing accessor {accessor_index}")
    accessor = accessors[accessor_index]
    if not isinstance(accessor, dict):
        raise ValueError(f"accessor {accessor_index} is not an object")
    if accessor.get("type") != "VEC3":
        raise ValueError(f"POSITION accessor {accessor_index} is not VEC3")
    minimum = accessor.get("min")
    maximum = accessor.get("max")
    if isinstance(minimum, list) and isinstance(maximum, list) and len(minimum) == 3 and len(maximum) == 3:
        return [float(v) for v in minimum], [float(v) for v in maximum], warnings

    count = int(accessor.get("count", 0))
    if count <= 0:
        raise ValueError(f"POSITION accessor {accessor_index} has no elements")
    if count > MAX_FALLBACK_BOUND_SAMPLES:
        warnings.append(
            f"POSITION accessor {accessor_index} has {count} vertices and no min/max; bounds were not decoded"
        )
        return [math.nan] * 3, [math.nan] * 3, warnings
    component_type = int(accessor.get("componentType", 5126))
    fmt_info = _COMPONENT_FORMATS.get(component_type)
    if fmt_info is None:
        raise ValueError(f"unsupported POSITION componentType {component_type}")
    fmt, component_bytes = fmt_info
    view_index = accessor.get("bufferView")
    if view_index is None:
        raise ValueError(f"POSITION accessor {accessor_index} has no bufferView")
    raw = _buffer_view_bytes(document, bin_payload, int(view_index))
    view = document["bufferViews"][int(view_index)]
    stride = int(view.get("byteStride", component_bytes * 3))
    base = int(accessor.get("byteOffset", 0))
    if stride < component_bytes * 3:
        raise ValueError(f"bufferView stride is too small for POSITION accessor {accessor_index}")
    mins = [float("inf")] * 3
    maxs = [float("-inf")] * 3
    for index in range(count):
        offset = base + index * stride
        if offset + component_bytes * 3 > len(raw):
            raise ValueError(f"POSITION accessor {accessor_index} exceeds its bufferView")
        values = struct.unpack_from(f"<3{fmt}", raw, offset)
        for axis, value in enumerate(values):
            mins[axis] = min(mins[axis], float(value))
            maxs[axis] = max(maxs[axis], float(value))
    return mins, maxs, warnings


def _merge_bounds(bounds: list[tuple[list[float], list[float]]]) -> dict[str, Any] | None:
    valid = [item for item in bounds if all(math.isfinite(v) for v in item[0] + item[1])]
    if not valid:
        return None
    minimum = [min(item[0][axis] for item in valid) for axis in range(3)]
    maximum = [max(item[1][axis] for item in valid) for axis in range(3)]
    size = [maximum[axis] - minimum[axis] for axis in range(3)]
    return {
        "space": "mesh-local-untransformed",
        "min": minimum,
        "max": maximum,
        "size": size,
        "center": [(minimum[axis] + maximum[axis]) / 2.0 for axis in range(3)],
    }


def probe_glb(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"GLB does not exist: {path}")
    document, bin_payload, binary = parse_glb(path)
    warnings: list[str] = []
    meshes = document.get("meshes", []) if isinstance(document.get("meshes", []), list) else []
    nodes = document.get("nodes", []) if isinstance(document.get("nodes", []), list) else []
    materials = document.get("materials", []) if isinstance(document.get("materials", []), list) else []
    skins = document.get("skins", []) if isinstance(document.get("skins", []), list) else []
    animations = document.get("animations", []) if isinstance(document.get("animations", []), list) else []
    scenes = document.get("scenes", []) if isinstance(document.get("scenes", []), list) else []
    textures = document.get("textures", []) if isinstance(document.get("textures", []), list) else []

    mesh_records: list[dict[str, Any]] = []
    all_bounds: list[tuple[list[float], list[float]]] = []
    primitive_count = 0
    for mesh_index, mesh in enumerate(meshes):
        if not isinstance(mesh, dict):
            warnings.append(f"mesh {mesh_index} is not an object")
            continue
        primitives = mesh.get("primitives", []) if isinstance(mesh.get("primitives", []), list) else []
        primitive_records: list[dict[str, Any]] = []
        mesh_bounds: list[tuple[list[float], list[float]]] = []
        for primitive_index, primitive in enumerate(primitives):
            primitive_count += 1
            if not isinstance(primitive, dict):
                warnings.append(f"mesh {mesh_index} primitive {primitive_index} is not an object")
                continue
            attributes = primitive.get("attributes", {}) if isinstance(primitive.get("attributes", {}), dict) else {}
            record: dict[str, Any] = {
                "index": primitive_index,
                "mode": primitive.get("mode", 4),
                "attributes": sorted(str(key) for key in attributes),
                "material": primitive.get("material"),
            }
            position_accessor = attributes.get("POSITION")
            if position_accessor is not None:
                try:
                    minimum, maximum, accessor_warnings = _accessor_bounds(document, bin_payload, int(position_accessor))
                    record["positionBounds"] = {"min": minimum, "max": maximum}
                    mesh_bounds.append((minimum, maximum))
                    all_bounds.append((minimum, maximum))
                    warnings.extend(accessor_warnings)
                except ValueError as exc:
                    warnings.append(f"mesh {mesh_index} primitive {primitive_index}: {exc}")
            else:
                warnings.append(f"mesh {mesh_index} primitive {primitive_index} has no POSITION attribute")
            primitive_records.append(record)
        mesh_record: dict[str, Any] = {
            "index": mesh_index,
            "name": mesh.get("name") or f"mesh-{mesh_index}",
            "primitiveCount": len(primitives),
            "primitives": primitive_records,
        }
        bounds = _merge_bounds(mesh_bounds)
        if bounds:
            mesh_record["bounds"] = bounds
        mesh_records.append(mesh_record)

    node_records = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        node_records.append(
            {
                "index": index,
                "name": node.get("name") or f"node-{index}",
                "mesh": node.get("mesh"),
                "skin": node.get("skin"),
                "children": node.get("children", []),
            }
        )
    material_records = [
        {"index": index, "name": material.get("name") or f"material-{index}", "alphaMode": material.get("alphaMode", "OPAQUE")}
        for index, material in enumerate(materials)
        if isinstance(material, dict)
    ]
    skeleton_records = [
        {"index": index, "name": skin.get("name") or f"skin-{index}", "jointCount": len(skin.get("joints", []))}
        for index, skin in enumerate(skins)
        if isinstance(skin, dict)
    ]
    bounds = _merge_bounds(all_bounds)
    if not meshes:
        warnings.append("GLB contains no meshes")
    if not bin_payload:
        warnings.append("GLB contains no BIN payload; external buffers are not accepted by this probe")
    if not materials:
        warnings.append("GLB contains no materials; visual appearance must be inferred or supplied separately")
    if not skins:
        warnings.append("GLB contains no skin; this is acceptable for static reference geometry but not animation evidence")
    semantic_decomposition = assess_semantic_decomposition(
        node_count=len(nodes),
        mesh_count=len(meshes),
        primitive_count=primitive_count,
        material_count=len(materials),
        nodes=node_records,
        meshes=mesh_records,
        materials=material_records,
        texture_count=len(textures),
    )
    if semantic_decomposition["status"] == "insufficient":
        warnings.append("GLB metadata has no reliable semantic part boundaries; request multipart GLB or capture ID masks")
    return {
        "schemaVersion": 1,
        "kind": "glb-reference",
        "path": str(path),
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
        "binary": binary,
        "asset": {
            "version": document.get("asset", {}).get("version") if isinstance(document.get("asset"), dict) else None,
            "generator": document.get("asset", {}).get("generator") if isinstance(document.get("asset"), dict) else None,
        },
        "scene": {
            "defaultScene": document.get("scene"),
            "sceneCount": len(scenes),
            "nodeCount": len(nodes),
            "meshCount": len(meshes),
            "primitiveCount": primitive_count,
            "materialCount": len(materials),
            "skinCount": len(skins),
            "animationCount": len(animations),
        },
        "nodes": node_records,
        "meshes": mesh_records,
        "materials": material_records,
        "skins": skeleton_records,
        "bounds": bounds,
        "semanticDecomposition": semantic_decomposition,
        "referenceReadiness": "pass" if meshes and bin_payload else "reject",
        "warnings": sorted(set(warnings)),
        "note": "Metadata only. Render the GLB through the target Three.js route before using it as a visual baseline; do not copy its topology into a procedural factory.",
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("glb", type=Path)
    parser.add_argument("--out", type=Path, help="also write the machine-readable probe artifact")
    args = parser.parse_args(argv)
    try:
        result = probe_glb(args.glb)
        rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        if args.out:
            output = args.out.expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_suffix(output.suffix + ".tmp")
            temporary.write_text(rendered, encoding="utf-8")
            temporary.replace(output)
        print(rendered, end="")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    import sys
    from pathlib import Path

    try:
        sys.path.insert(0, str(next(
            parent / "forge" / "_shared"
            for parent in Path(__file__).resolve().parents
            if (parent / "forge" / "_shared" / "cli_run.py").is_file()
        )))
        from cli_run import run_entry
    except (ImportError, StopIteration):
        # vendored/fixture copies without the forge runtime: run bare, no pipe handling
        def run_entry(main_fn, argv=None):
            return main_fn(sys.argv[1:] if argv is None else argv)

    raise SystemExit(run_entry(main))

