#!/usr/bin/env python3
"""Normalize a generated model: orientation +Z up / facing -Y (Blender space), a target height, feet
at z=0, X/Y centred, speck islands removed, textures kept, exported as GLB.

The glTF exporter converts to Y-up, so the delivered file is +Y up facing +Z in glTF terms. Meshes are
never welded (that would break UVs and normals); islands are found on welded positions only.
Runs under the selected bpy python.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _HERE]
sys.path.append(_HERE)

import argparse  # noqa: E402
import math  # noqa: E402
from pathlib import Path  # noqa: E402

import _common as C  # noqa: E402

# Direction the model FACES after import, in Blender space (Z up), and the Z rotation that brings it
# to -Y. Tripo FBX output faces +X (measured on the vesper run); a glTF import already faces -Y.
FRONT_ROTATION_Z = {"-Y": 0.0, "+X": -90.0, "-X": 90.0, "+Y": 180.0}
DEFAULT_FRONT = {".glb": "-Y", ".gltf": "-Y", ".fbx": "+X", ".obj": "-Y"}
UPPER_BODY_FRACTION = 0.3


def build_parser():
    parser = argparse.ArgumentParser(description="Normalize a textured model and export a GLB")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--gen", type=int, help="gen-N: reads generate.json's model, writes gen-N/normalized.glb + normalize.json")
    parser.add_argument("--model", help="explicit input model; with --out-glb/--out")
    parser.add_argument("--out-glb", help="explicit output GLB path")
    parser.add_argument("--out", help="explicit receipt path")
    parser.add_argument("--height", type=float, default=1.8, help="target extent along up, metres (default 1.8)")
    parser.add_argument("--up", choices=("Z", "Y"), default="Z", help="up axis of the imported model (default Z)")
    parser.add_argument("--front", choices=sorted(FRONT_ROTATION_Z), help="direction the model faces after import "
                        "(default: -Y for glb/gltf/obj, +X for fbx)")
    parser.add_argument("--center", choices=("median", "bbox"), default="median",
                        help="XY centring: median of the upper-body vertices (default) or bounding-box centre")
    parser.add_argument("--speck-max-verts", type=int, default=100)
    parser.add_argument("--speck-max-ratio", type=float, default=0.002)
    parser.add_argument("--keep-specks", action="store_true", help="do not remove speck islands")
    parser.add_argument("--keep-shading", action="store_true", help="do not force smooth shading")
    return parser


def bounds(objects):
    low = [float("inf")] * 3
    high = [float("-inf")] * 3
    for obj in objects:
        for vert in obj.data.vertices:
            co = obj.matrix_world @ vert.co
            for axis in range(3):
                low[axis] = min(low[axis], co[axis])
                high[axis] = max(high[axis], co[axis])
    return low, high


def remove_specks(obj, tolerance, max_verts, max_ratio):
    import bmesh
    import _topology as T
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    islands = T.position_islands(bm, tolerance)
    specks = T.speck_islands(islands, max_verts, max_ratio)
    removed = []
    for island in specks:
        zs = [v.co.z for v in island]
        removed.append({"mesh": obj.name, "vertices": len(island), "z": [round(min(zs), 4), round(max(zs), 4)]})
    if specks:
        bmesh.ops.delete(bm, geom=[v for island in specks for v in island if v.is_valid], context="VERTS")
    loose = [v for v in bm.verts if not v.link_edges]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return removed, len(loose), (len(islands[0]) if islands else 0)


def run(model, out_glb, args):
    import bpy
    from mathutils import Matrix
    suffix = model.suffix.lower()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if suffix in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(model))
    elif suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(model))
    elif suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=str(model))
    else:
        raise C.TexturedError(f"unsupported model format {suffix!r}")
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not meshes:
        raise C.TexturedError("the model contains no mesh objects")
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    front = args.front or DEFAULT_FRONT[suffix]
    before_low, before_high = bounds(meshes)

    # Bake the import transform into the mesh data so everything below works in one space.
    bpy.ops.object.select_all(action="DESELECT")
    for obj in bpy.data.objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    for obj in meshes:
        if obj.parent is not None:
            matrix = obj.matrix_world.copy()
            obj.parent = None
            obj.matrix_world = matrix
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    rotation = Matrix.Identity(4)
    if args.up == "Y":
        rotation = Matrix.Rotation(math.radians(90), 4, "X") @ rotation
    rotation = Matrix.Rotation(math.radians(FRONT_ROTATION_Z[front]), 4, "Z") @ rotation
    for obj in meshes:
        obj.data.transform(rotation)
    for obj in armatures:  # a rig keeps its bind relationship: rotate it at object level
        obj.matrix_world = rotation @ obj.matrix_world

    diagonal = max(1e-9, sum((h - l) ** 2 for l, h in zip(before_low, before_high)) ** 0.5)
    tolerance = max(diagonal * 1e-6, 1e-9)
    removed, loose_removed = [], 0
    if not args.keep_specks:
        for obj in meshes:
            gone, loose, _ = remove_specks(obj, tolerance, args.speck_max_verts, args.speck_max_ratio)
            removed.extend(gone)
            loose_removed += loose

    low, high = bounds(meshes)
    extent = high[2] - low[2]
    if extent <= 0:
        raise C.TexturedError("the model has no extent along the up axis")
    scale = args.height / extent
    scaling = Matrix.Scale(scale, 4)
    for obj in meshes:
        obj.data.transform(scaling)
    for obj in armatures:
        obj.matrix_world = scaling @ obj.matrix_world
    low, high = bounds(meshes)
    if args.center == "bbox":
        cx, cy = (low[0] + high[0]) / 2, (low[1] + high[1]) / 2
    else:
        threshold = low[2] + UPPER_BODY_FRACTION * args.height
        upper = [obj.matrix_world @ v.co for obj in meshes for v in obj.data.vertices if (obj.matrix_world @ v.co).z > threshold]
        xs = sorted(p.x for p in upper) or [(low[0] + high[0]) / 2]
        ys = sorted(p.y for p in upper) or [(low[1] + high[1]) / 2]
        cx, cy = xs[len(xs) // 2], ys[len(ys) // 2]
    translation = Matrix.Translation((-cx, -cy, -low[2]))
    for obj in meshes:
        obj.data.transform(translation)
    for obj in armatures:
        obj.matrix_world = translation @ obj.matrix_world
    for obj in meshes:
        obj.data.update()
        if not args.keep_shading:
            if hasattr(obj.data, "shade_smooth"):
                obj.data.shade_smooth()
            else:
                for polygon in obj.data.polygons:
                    polygon.use_smooth = True
    final_low, final_high = bounds(meshes)

    out_glb.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=str(out_glb), export_format="GLB", export_image_format="AUTO",
                              export_yup=True, use_selection=False, export_apply=False)
    return {
        "bpy": bpy.app.version_string, "front": front, "frontAssumed": args.front is None, "up": args.up,
        "rotationZDegrees": FRONT_ROTATION_Z[front], "scale": round(scale, 6), "targetHeight": args.height,
        "center": args.center, "centerOffset": [round(-cx, 5), round(-cy, 5)],
        "speckRemoval": {"enabled": not args.keep_specks, "removedIslands": len(removed),
                         "removedVertices": sum(r["vertices"] for r in removed), "looseVerticesRemoved": loose_removed,
                         "islands": removed[:64], "thresholds": {"maxVertices": args.speck_max_verts, "maxRatio": args.speck_max_ratio}},
        "bboxBefore": {"min": [round(v, 5) for v in before_low], "max": [round(v, 5) for v in before_high]},
        "bbox": {"min": [round(v, 5) for v in final_low], "max": [round(v, 5) for v in final_high],
                 "size": [round(h - l, 5) for l, h in zip(final_low, final_high)]},
        "meshes": len(meshes), "armatures": len(armatures),
    }


def verify_export(path):
    from glb_container import parse_glb
    document, _, _ = parse_glb(path)
    images = document.get("images", [])
    textures = document.get("textures", [])
    materials = document.get("materials", [])
    return {"bytes": path.stat().st_size, "sha256": C.sha256_file(path), "images": len(images),
            "textures": len(textures), "materials": len(materials),
            "meshes": len(document.get("meshes", [])), "skins": len(document.get("skins", []))}


def main(argv):
    argv, inside = C.strip_flag(argv)
    args = build_parser().parse_args(argv)
    workspace = C.resolve_workspace(args.workspace)
    if args.height <= 0 or not math.isfinite(args.height):
        raise C.TexturedError("--height must be a positive number")
    if args.model:
        model = Path(args.model).expanduser().resolve()
        out_glb = Path(args.out_glb).expanduser().resolve() if args.out_glb else model.with_name(model.stem + "-normalized.glb")
        out = Path(args.out).expanduser().resolve() if args.out else out_glb.with_suffix(".json")
    elif args.gen is not None:
        directory = C.gen_dir(workspace, args.gen)
        generated = C.read_json(directory / "generate.json")
        model = directory / generated["asset"]["path"]
        out_glb = Path(args.out_glb).expanduser().resolve() if args.out_glb else directory / "normalized.glb"
        out = Path(args.out).expanduser().resolve() if args.out else directory / "normalize.json"
    else:
        raise C.TexturedError("pass --gen N or --model PATH")
    if not model.is_file():
        raise C.TexturedError(f"model not found: {model}")
    if not inside:
        code = C.delegate_to_bpy(os.path.abspath(__file__), argv)
        if code is not None:
            return code
    facts = run(model, out_glb, args)
    exported = verify_export(out_glb)
    if exported["textures"] == 0:
        print("normalize: WARNING the exported GLB carries no textures", file=sys.stderr)
    C.write_json(out, C.receipt("normalize", generation=args.gen, source=C.workspace_relative(workspace, model),
                                output=C.workspace_relative(workspace, out_glb), normalization=facts, export=exported))
    size = facts["bbox"]["size"]
    print(f"normalize: height {size[2]:.3f} m, specks removed {facts['speckRemoval']['removedIslands']}, "
          f"{exported['textures']} texture(s), {exported['bytes']} bytes -> {out_glb}")
    return 0


if __name__ == "__main__":
    C.run_cli(main)
