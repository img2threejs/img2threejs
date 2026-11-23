#!/usr/bin/env python3
"""Facts about a 3D model: triangles, vertices, meshes, materials, textures and their sizes, bbox,
connectivity islands, non-manifold edges and skeleton. Runs under the selected bpy python.

Islands and non-manifold edges are counted on POSITION-welded topology as well as raw: glTF import
splits vertices at UV seams, which inflates the raw non-manifold count (the vesper report measured
43,462 raw vs 245 real). Both figures are recorded; the welded one is the geometry fact.
"""
import os
import sys

# This file is named like the stdlib module `inspect`: keep its directory off the front of sys.path
# (the script directory is sys.path[0] when run directly) so no import can resolve to it.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _HERE]
sys.path.append(_HERE)

import argparse  # noqa: E402
from pathlib import Path  # noqa: E402

import _common as C  # noqa: E402

SUPPORTED = (".glb", ".gltf", ".fbx", ".obj")
DEFAULT_SPECK_MAX_VERTS = 100
DEFAULT_SPECK_MAX_RATIO = 0.002


def import_model(path):
    import bpy
    bpy.ops.wm.read_factory_settings(use_empty=True)
    suffix = Path(path).suffix.lower()
    if suffix in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path))
    else:
        raise C.TexturedError(f"unsupported model format {suffix!r}; expected one of {SUPPORTED}")
    return [o for o in bpy.data.objects if o.type == "MESH"]


def world_bbox(objects):
    from mathutils import Vector
    low = [float("inf")] * 3
    high = [float("-inf")] * 3
    for obj in objects:
        for corner in obj.bound_box:
            point = obj.matrix_world @ Vector(corner)
            for axis in range(3):
                low[axis] = min(low[axis], point[axis])
                high[axis] = max(high[axis], point[axis])
    if low[0] == float("inf"):
        return None
    return low, high


def mesh_topology(mesh, tolerance, speck_max_verts, speck_max_ratio):
    import bmesh
    import _topology as T
    raw, welded, loose = T.nonmanifold_counts(mesh, tolerance)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    islands = T.position_islands(bm, tolerance)
    sizes = [len(island) for island in islands]
    specks = [len(island) for island in T.speck_islands(islands, speck_max_verts, speck_max_ratio)]
    bm.free()
    return raw, welded, loose, sizes, specks


def material_facts(material):
    facts = {"name": material.name, "textures": {}, "doubleSided": not getattr(material, "use_backface_culling", True)}
    if material.node_tree:
        for node in material.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image:
                for output in node.outputs:
                    for link in output.links:
                        facts["textures"][f"{link.to_node.name}.{link.to_socket.name}"] = node.image.name
            elif node.type == "BSDF_PRINCIPLED":
                facts["metallic"] = round(float(node.inputs["Metallic"].default_value), 4)
                facts["roughness"] = round(float(node.inputs["Roughness"].default_value), 4)
    return facts


def collect(path, speck_max_verts=DEFAULT_SPECK_MAX_VERTS, speck_max_ratio=DEFAULT_SPECK_MAX_RATIO):
    import bpy
    path = Path(path)
    meshes = import_model(path)
    box = world_bbox(meshes)
    diagonal = (sum((h - l) ** 2 for l, h in zip(*box)) ** 0.5) if box else 1.0
    tolerance = max(diagonal * 1e-6, 1e-9)
    objects, island_sizes, speck_sizes = [], [], []
    total_verts = total_tris = raw_nm = welded_nm = loose_total = 0
    uv_sets = set()
    for obj in meshes:
        mesh = obj.data
        mesh.calc_loop_triangles()
        raw, welded, loose, sizes, specks_here = mesh_topology(mesh, tolerance, speck_max_verts, speck_max_ratio)
        speck_sizes.extend(specks_here)
        total_verts += len(mesh.vertices)
        total_tris += len(mesh.loop_triangles)
        raw_nm += raw
        welded_nm += welded
        loose_total += loose
        island_sizes.extend(sizes)
        uv_sets.update(layer.name for layer in mesh.uv_layers)
        objects.append({
            "name": obj.name, "vertices": len(mesh.vertices), "triangles": len(mesh.loop_triangles),
            "materials": [m.name for m in mesh.materials if m], "uvLayers": [l.name for l in mesh.uv_layers],
            "dimensions": [round(v, 5) for v in obj.dimensions], "islands": len(sizes),
            "nonManifoldEdgesRaw": raw, "nonManifoldEdgesWelded": welded, "looseVertices": loose,
            "parent": obj.parent.name if obj.parent else None,
        })
    island_sizes.sort(reverse=True)
    largest = island_sizes[0] if island_sizes else 0
    specks = speck_sizes
    users = {}
    for material in bpy.data.materials:
        for socket, image in material_facts(material)["textures"].items():
            users.setdefault(image, []).append(f"{material.name}:{socket}")
    textures = []
    for image in bpy.data.images:
        if image.name in ("Render Result", "Viewer Node"):
            continue
        textures.append({"name": image.name, "width": image.size[0], "height": image.size[1],
                         "colorspace": image.colorspace_settings.name, "packed": image.packed_file is not None,
                         "usedBy": users.get(image.name, [])})
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    materials = [material_facts(m) for m in bpy.data.materials if m.users]
    has_base = any(any(key.endswith("Base Color") for key in m["textures"]) for m in materials)
    return {
        "source": str(path), "format": path.suffix.lower().lstrip("."), "bytes": path.stat().st_size,
        "sha256": C.sha256_file(path), "bpy": bpy.app.version_string,
        "triangles": total_tris, "vertices": total_verts, "meshes": len(meshes),
        "materialCount": len(materials), "textureCount": len(textures), "uvSets": sorted(uv_sets),
        "bbox": None if box is None else {"min": [round(v, 5) for v in box[0]], "max": [round(v, 5) for v in box[1]],
                                          "size": [round(h - l, 5) for l, h in zip(*box)]},
        "islands": {"count": len(island_sizes), "largest": largest, "specks": len(specks),
                    "speckVertices": sum(specks), "thresholds": {"maxVertices": speck_max_verts, "maxRatio": speck_max_ratio}},
        "nonManifoldEdges": {"raw": raw_nm, "welded": welded_nm,
                             "note": "raw is inflated by glTF UV-seam vertex splits; welded is the geometry fact"},
        "looseVertices": loose_total,
        "skeleton": {"armatures": len(armatures), "bones": sum(len(a.data.bones) for a in armatures),
                     "actions": len(bpy.data.actions)},
        "textured": {"hasBaseColorTexture": has_base, "loadedTextures": sum(1 for t in textures if t["width"] > 0)},
        "meshObjects": objects, "materials": materials, "textures": textures,
    }


def build_parser():
    parser = argparse.ArgumentParser(description="Inspect a 3D model and write a facts receipt")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--gen", type=int, help="generation number: inspects gen-N/raw/<model> and writes gen-N/inspect.json")
    parser.add_argument("--model", help="explicit model path (glb, gltf, fbx, obj); required without --gen")
    parser.add_argument("--out", help="receipt path (default: gen-N/inspect.json)")
    parser.add_argument("--speck-max-verts", type=int, default=DEFAULT_SPECK_MAX_VERTS)
    parser.add_argument("--speck-max-ratio", type=float, default=DEFAULT_SPECK_MAX_RATIO)
    return parser


def resolve_input(args, workspace):
    if args.model:
        return Path(args.model).expanduser().resolve(), (Path(args.out) if args.out else None)
    if args.gen is None:
        raise C.TexturedError("pass --gen N or --model PATH")
    directory = C.gen_dir(workspace, args.gen)
    generated = C.read_json(directory / "generate.json")
    return (directory / generated["asset"]["path"]), (Path(args.out) if args.out else directory / "inspect.json")


def main(argv):
    argv, inside = C.strip_flag(argv)
    args = build_parser().parse_args(argv)
    workspace = C.resolve_workspace(args.workspace)
    model, out = resolve_input(args, workspace)
    if not model.is_file():
        raise C.TexturedError(f"model not found: {model}")
    if model.suffix.lower() not in SUPPORTED:
        raise C.TexturedError(f"unsupported model format {model.suffix!r}; expected one of {SUPPORTED}")
    if out is None:
        out = Path.cwd() / "inspect.json"
    if not inside:
        code = C.delegate_to_bpy(os.path.abspath(__file__), argv)
        if code is not None:
            return code
    facts = collect(model, args.speck_max_verts, args.speck_max_ratio)
    facts["source"] = C.workspace_relative(workspace, model)
    C.write_json(out, C.receipt("inspect", generation=args.gen, facts=facts))
    print(f"inspect: {facts['triangles']} tris, {facts['vertices']} verts, {facts['meshes']} mesh(es), "
          f"{facts['materialCount']} material(s), {facts['textureCount']} texture(s), "
          f"islands {facts['islands']['count']} (specks {facts['islands']['specks']}), "
          f"skeleton bones {facts['skeleton']['bones']} -> {out}")
    return 0


if __name__ == "__main__":
    C.run_cli(main)
