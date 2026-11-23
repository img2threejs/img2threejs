"""Fixtures for the textured tests: a tiny textured GLB generated with the selected bpy python.

`bpy_python()` returns None when no bpy python is selected (tests then skip cleanly): selection is
IMG2_CHARACTER_BPY_PYTHON / IMG2_BLENDER_PYTHON from the environment, then the provisioned selectors.
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEXTURED = ROOT / "forge" / "textured"
sys.path.insert(0, str(ROOT / "forge" / "_shared"))


def bpy_python() -> str | None:
    for key in ("IMG2_CHARACTER_BPY_PYTHON", "IMG2_BLENDER_PYTHON"):
        value = os.environ.get(key)
        if value and os.path.isfile(value):
            return value
    try:
        from runtime_selectors import read_selectors
        selectors = read_selectors()
    except Exception:
        selectors = None
    for key in ("IMG2_CHARACTER_BPY_PYTHON", "IMG2_BLENDER_PYTHON"):
        value = selectors["env"].get(key) if selectors else None
        if value and os.path.isfile(value):
            return value
    return None


FIXTURE_SCRIPT = r'''
import bpy, sys, math
out = sys.argv[-1]
bpy.ops.wm.read_factory_settings(use_empty=True)
# Body: UV sphere stretched along X (so orientation is observable), 2.0 tall in Z, off-origin.
bpy.ops.mesh.primitive_uv_sphere_add(segments=96, ring_count=64, radius=1.0, location=(3.0, 4.0, 5.0))
body = bpy.context.active_object
body.scale = (0.8, 0.3, 1.0)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
# Speck: a tiny separate cube (8 verts) in the same mesh, far from the body.
bpy.ops.mesh.primitive_cube_add(size=0.02, location=(3.0, 4.0, 8.0))
speck = bpy.context.active_object
bpy.ops.object.select_all(action="DESELECT")
body.select_set(True); speck.select_set(True)
bpy.context.view_layer.objects.active = body
bpy.ops.object.join()
body.name = "Body"
# Texture: a 64x64 checker, packed.
image = bpy.data.images.new("Checker", 64, 64)
pixels = []
for y in range(64):
    for x in range(64):
        v = 0.9 if ((x // 8) + (y // 8)) % 2 else 0.15
        pixels.extend((v, 0.3, 1.0 - v, 1.0))
image.pixels = pixels
image.pack()
material = bpy.data.materials.new("Skin")
material.use_nodes = True
tree = material.node_tree
bsdf = tree.nodes["Principled BSDF"]
tex = tree.nodes.new("ShaderNodeTexImage")
tex.image = image
tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
body.data.materials.append(material)
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", export_yup=True)
'''


def build_fixture_glb(directory: Path) -> Path:
    python = bpy_python()
    assert python, "bpy python required"
    script = directory / "make_fixture.py"
    script.write_text(FIXTURE_SCRIPT, encoding="utf-8")
    target = directory / "fixture.glb"
    done = subprocess.run([python, "-I", "-B", str(script), str(target)], capture_output=True, text=True)
    if done.returncode != 0 or not target.is_file():
        raise RuntimeError(f"fixture generation failed: {done.stderr[-400:]}")
    return target


def write_png(path: Path, width: int, height: int, rgb: tuple[int, int, int]) -> Path:
    """A flat-colour PNG written with zlib/struct (no Pillow, no bpy)."""
    row = b"\x00" + bytes(rgb) * width
    raw = row * height

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return path


def png_dimensions(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    return struct.unpack(">II", header[16:24])


def run_tool(name: str, *args: str, env: dict[str, str] | None = None, python: str | None = None):
    """Run forge/textured/<name>.py under the CURRENT interpreter (exercising bpy delegation)."""
    environment = dict(os.environ)
    environment.pop("TYPESAFE_API_KEY", None)
    environment.update(env or {})
    return subprocess.run([sys.executable, str(TEXTURED / name), *args], capture_output=True, text=True, env=environment)
