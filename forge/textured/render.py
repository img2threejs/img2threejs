#!/usr/bin/env python3
"""Turntable renders of a normalized model: front, left34, right34, left_side, back (+ optional
closeups) under a neutral studio light, Cycles CPU with denoise (default) or EEVEE. Runs under the
selected bpy python.

The model is expected to be normalized (+Z up, facing -Y, feet at z=0): views are placed relative to
its measured bounding box, so framing follows the model rather than a fixed 1.8 m assumption.
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

# name -> camera yaw in degrees around +Z (0 = in front of a -Y facing model, positive = to its left).
VIEWS = (("front", 0.0), ("left34", 45.0), ("right34", -45.0), ("left_side", 90.0), ("back", 180.0))
# closeup name -> (target height as a fraction of model height, yaw, lateral offset in model heights)
CLOSEUP_PRESETS = {"head": (0.88, 0.0, 0.0), "torso": (0.62, 0.0, 0.0), "feet": (0.08, 0.0, 0.0)}
DEFAULT_SIZE = (1024, 1536)
MIN_SIDE, MAX_SIDE = 64, 4096
BACKGROUND = 0.42
EXPOSURE = -0.6


def build_parser():
    parser = argparse.ArgumentParser(description="Render turntable views of a normalized GLB")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--gen", type=int, help="gen-N: renders gen-N/normalized.glb into gen-N/renders/")
    parser.add_argument("--glb", help="explicit GLB path")
    parser.add_argument("--out-dir", help="explicit output directory")
    parser.add_argument("--receipt", help="explicit receipt path")
    parser.add_argument("--engine", choices=("cycles", "eevee"), default="cycles")
    parser.add_argument("--samples", type=int, default=96)
    parser.add_argument("--width", type=int, default=DEFAULT_SIZE[0])
    parser.add_argument("--height", type=int, default=DEFAULT_SIZE[1])
    parser.add_argument("--only", action="append", choices=[name for name, _ in VIEWS], help="restrict the turntable views (repeatable)")
    parser.add_argument("--closeup", action="append", choices=sorted(CLOSEUP_PRESETS),
                        help="add a closeup of a body region (repeatable); fractions of the model height")
    parser.add_argument("--distance", type=float, default=0.0,
                        help="camera distance in metres (default: fitted from the bounding box)")
    parser.add_argument("--center-x", type=float, default=0.0, help="shift the view centre along X (e.g. to frame a long prop)")
    parser.add_argument("--lens", type=float, default=85.0)
    parser.add_argument("--exposure", type=float, default=EXPOSURE)
    parser.add_argument("--background", type=float, default=BACKGROUND, help="flat grey world level 0..1")
    return parser


def validate(args):
    for name, value in (("width", args.width), ("height", args.height)):
        if not MIN_SIDE <= value <= MAX_SIDE:
            raise C.TexturedError(f"--{name} must be within {MIN_SIDE}..{MAX_SIDE}")
    if args.samples < 1:
        raise C.TexturedError("--samples must be >= 1")
    if not 0.0 <= args.background <= 1.0:
        raise C.TexturedError("--background must be within 0..1")


def setup_scene(args):
    import bpy
    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y = args.width, args.height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    if args.engine == "cycles":
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = args.samples
        scene.cycles.use_denoising = True
    else:
        scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items else "BLENDER_EEVEE"
        if hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
            scene.eevee.taa_render_samples = args.samples
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = args.exposure
    world = bpy.data.worlds.new("studio")
    world.use_nodes = True
    background = world.node_tree.nodes["Background"]
    level = args.background
    background.inputs[0].default_value = (level, level, level, 1.0)
    background.inputs[1].default_value = 0.6
    scene.world = world
    return scene


def add_area_light(name, location, energy, size, target):
    import bpy
    from mathutils import Vector
    data = bpy.data.lights.new(name, "AREA")
    data.energy, data.size = energy, size
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = (target - Vector(location)).to_track_quat("-Z", "Y").to_euler()


def three_point(yaw, center, radius, scale):
    """Key/fill/rim placed relative to the camera azimuth; energy scales with the model size squared."""
    import bpy
    from mathutils import Vector
    for obj in [o for o in bpy.data.objects if o.type == "LIGHT"]:
        bpy.data.objects.remove(obj)

    def place(azimuth, elevation):
        a, e = math.radians(yaw + azimuth), math.radians(elevation)
        return Vector((center.x + radius * math.sin(a) * math.cos(e), center.y - radius * math.cos(a) * math.cos(e),
                       center.z + radius * math.sin(e)))
    energy = scale * scale
    add_area_light("key", place(-40, 30), 350 * energy, 2.0 * scale, center)
    add_area_light("fill", place(45, 10), 120 * energy, 3.0 * scale, center)
    add_area_light("rim", place(160, 35), 250 * energy, 1.5 * scale, center)


def place_camera(yaw, center, distance, lens):
    import bpy
    from mathutils import Vector
    for obj in [o for o in bpy.data.objects if o.type == "CAMERA"]:
        bpy.data.objects.remove(obj)
    data = bpy.data.cameras.new("cam")
    data.lens, data.sensor_width, data.sensor_fit, data.clip_end = lens, 36, "HORIZONTAL", 1000
    obj = bpy.data.objects.new("cam", data)
    bpy.context.scene.collection.objects.link(obj)
    a = math.radians(yaw)
    obj.location = Vector((center.x + distance * math.sin(a), center.y - distance * math.cos(a), center.z))
    obj.rotation_euler = (center - obj.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = obj


def fit_distance(half_height, half_width, lens, aspect, margin=1.12):
    """Distance at which both the half height and half width fit with a margin (sensor width 36 mm)."""
    horizontal = 36.0 / lens
    vertical = horizontal / aspect
    return margin * max(half_height / (vertical / 2), half_width / (horizontal / 2))


def measure(meshes):
    low, high = [float("inf")] * 3, [float("-inf")] * 3
    for obj in meshes:
        for vert in obj.data.vertices:
            co = obj.matrix_world @ vert.co
            for axis in range(3):
                low[axis], high[axis] = min(low[axis], co[axis]), max(high[axis], co[axis])
    return low, high


def render_all(glb, out_dir, args):
    import bpy
    from mathutils import Vector
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(glb))
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not meshes:
        raise C.TexturedError("the GLB contains no mesh")
    low, high = measure(meshes)
    height = high[2] - low[2]
    width = math.hypot(high[0] - low[0], high[1] - low[1])  # any yaw keeps the footprint in frame
    scene = setup_scene(args)
    aspect = args.width / args.height
    center = Vector((args.center_x, 0.0, low[2] + height * 0.5))
    distance = args.distance or fit_distance(height / 2, max(width, height * 0.35) / 2, args.lens, aspect)
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = []
    views = [(n, y) for n, y in VIEWS if not args.only or n in args.only]
    for name, yaw in views:
        three_point(yaw, center, 2.6 * height / 1.8 + distance * 0.2, height / 1.8)
        place_camera(yaw, center, distance, args.lens)
        path = out_dir / f"{name}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        rendered.append({"name": name, "yaw": yaw, "kind": "turntable", "path": str(path)})
    for name in args.closeup or []:
        fraction, yaw, _ = CLOSEUP_PRESETS[name]
        focus = Vector((args.center_x, 0.0, low[2] + height * fraction))
        close = fit_distance(height * 0.14, height * 0.14, args.lens, aspect, margin=1.0)
        three_point(yaw, focus, 2.2 * height / 1.8, height / 1.8)
        place_camera(yaw, focus, close, args.lens)
        path = out_dir / f"closeup_{name}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        rendered.append({"name": f"closeup_{name}", "yaw": yaw, "kind": "closeup", "path": str(path)})
    return rendered, {"bbox": {"min": [round(v, 4) for v in low], "max": [round(v, 4) for v in high]},
                      "distance": round(distance, 4), "center": [round(v, 4) for v in center],
                      "bpy": bpy.app.version_string}


def main(argv):
    argv, inside = C.strip_flag(argv)
    args = build_parser().parse_args(argv)
    validate(args)
    workspace = C.resolve_workspace(args.workspace)
    if args.glb:
        glb = Path(args.glb).expanduser().resolve()
        out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else glb.parent / "renders"
        receipt_path = Path(args.receipt).expanduser().resolve() if args.receipt else out_dir / "render.json"
    elif args.gen is not None:
        directory = C.gen_dir(workspace, args.gen)
        glb = directory / "normalized.glb"
        out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else directory / "renders"
        receipt_path = Path(args.receipt).expanduser().resolve() if args.receipt else directory / "render.json"
    else:
        raise C.TexturedError("pass --gen N or --glb PATH")
    if not glb.is_file():
        raise C.TexturedError(f"GLB not found: {glb}")
    if not inside:
        code = C.delegate_to_bpy(os.path.abspath(__file__), argv)
        if code is not None:
            return code
    glb_sha256 = C.sha256_file(glb)
    rendered, facts = render_all(glb, out_dir, args)
    if C.sha256_file(glb) != glb_sha256:
        raise C.TexturedError("normalized GLB changed while rendering")
    for row in rendered:
        path = Path(row["path"])
        if not path.is_file() or path.stat().st_size == 0:
            raise C.TexturedError(f"render did not produce {path}")
        row["sha256"] = C.sha256_file(path)
        row["path"] = C.workspace_relative(workspace, path)
    C.write_json(receipt_path, C.receipt(
        "render", generation=args.gen, glb=C.workspace_relative(workspace, glb), glbSha256=glb_sha256, engine=args.engine,
        samples=args.samples, size=[args.width, args.height], lens=args.lens, exposure=args.exposure,
        background=args.background, views=rendered, **facts))
    print(f"render: {len(rendered)} image(s) {args.width}x{args.height} ({args.engine}, {args.samples} samples) -> {out_dir}")
    return 0


if __name__ == "__main__":
    C.run_cli(main)
