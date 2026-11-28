"""inspect / normalize / render / sheet on a tiny textured GLB generated with bpy.

Skipped cleanly when no bpy python is selected (IMG2_CHARACTER_BPY_PYTHON / IMG2_BLENDER_PYTHON or the
provisioned selectors). The tools are run as CLIs under THIS interpreter so the bpy delegation is
exercised too.
"""
import json
import os
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from textured_support import bpy_python, build_fixture_glb, png_dimensions, run_tool, write_png

BPY = bpy_python()


@unittest.skipUnless(BPY, "no bpy python selected (set IMG2_BLENDER_PYTHON)")
class TexturedToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = Path(tempfile.mkdtemp(prefix="tex-tools-"))
        cls.glb = build_fixture_glb(cls.directory)
        cls.env = {"IMG2_BLENDER_PYTHON": BPY, "IMG2_CHARACTER_BPY_PYTHON": ""}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.directory, ignore_errors=True)

    def run_ok(self, name, *args):
        done = run_tool(name, *args, env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr[-800:] + done.stdout[-400:])
        return done

    def test_inspect_reports_geometry_texture_and_speck_facts(self):
        out = self.directory / "inspect.json"
        self.run_ok("inspect.py", "--workspace", str(self.directory), "--model", str(self.glb), "--out", str(out))
        facts = json.loads(out.read_text())["facts"]
        self.assertEqual(facts["meshes"], 1)
        self.assertEqual(facts["materialCount"], 1)
        self.assertEqual(facts["textureCount"], 1)
        self.assertEqual((facts["textures"][0]["width"], facts["textures"][0]["height"]), (64, 64))
        self.assertTrue(facts["textured"]["hasBaseColorTexture"])
        self.assertEqual(facts["islands"]["count"], 2)       # body + the detached cube
        self.assertEqual(facts["islands"]["specks"], 1)
        self.assertGreater(facts["triangles"], 5000)
        self.assertEqual(facts["skeleton"]["bones"], 0)
        # The detached cube lifts the extent to ~4.01 along Z; width follows the 0.8 x-scale.
        self.assertAlmostEqual(facts["bbox"]["size"][2], 4.01, delta=0.02)
        self.assertAlmostEqual(facts["bbox"]["size"][0], 1.6, delta=0.02)

    def test_normalize_orients_scales_grounds_removes_specks_and_keeps_textures(self):
        out_glb, out = self.directory / "n.glb", self.directory / "n.json"
        # Declare the fixture as facing +X so the -90 degree turn is observable: the body is long along X.
        self.run_ok("normalize.py", "--workspace", str(self.directory), "--model", str(self.glb), "--out-glb", str(out_glb),
                    "--out", str(out), "--height", "1.0", "--front", "+X", "--center", "bbox")
        receipt = json.loads(out.read_text())
        norm = receipt["normalization"]
        self.assertEqual(norm["speckRemoval"]["removedIslands"], 1)
        low, high = norm["bbox"]["min"], norm["bbox"]["max"]
        self.assertAlmostEqual(low[2], 0.0, places=4)                  # feet at the origin
        self.assertAlmostEqual(high[2] - low[2], 1.0, places=3)        # target height, speck excluded
        self.assertAlmostEqual((low[0] + high[0]) / 2, 0.0, places=3)  # centred
        self.assertAlmostEqual((low[1] + high[1]) / 2, 0.0, places=3)
        size = norm["bbox"]["size"]
        self.assertAlmostEqual(size[1], 0.8, delta=0.02)               # x extent became y after the turn
        self.assertAlmostEqual(size[0], 0.3, delta=0.02)
        self.assertEqual(receipt["export"]["textures"], 1)
        self.assertGreater(receipt["export"]["bytes"], 0)
        again = self.directory / "again.json"
        self.run_ok("inspect.py", "--workspace", str(self.directory), "--model", str(out_glb), "--out", str(again))
        facts = json.loads(again.read_text())["facts"]
        self.assertEqual((facts["islands"]["count"], facts["textureCount"]), (1, 1))

    def test_render_writes_requested_views_at_the_requested_size_and_a_receipt(self):
        out_dir, receipt = self.directory / "renders", self.directory / "render.json"
        self.run_ok("render.py", "--workspace", str(self.directory), "--glb", str(self.glb), "--out-dir", str(out_dir),
                    "--receipt", str(receipt), "--width", "160", "--height", "240", "--samples", "4",
                    "--only", "front", "--only", "back", "--closeup", "head")
        rows = json.loads(receipt.read_text())["views"]
        self.assertEqual([r["name"] for r in rows], ["front", "back", "closeup_head"])
        for row in rows:
            image = Path(row["path"]) if os.path.isabs(row["path"]) else self.directory / row["path"]
            self.assertEqual(png_dimensions(image), (160, 240))
            self.assertEqual(len(row["sha256"]), 64)
        front = out_dir / "front.png"
        # a model is actually in frame: the PNG is not a flat background
        self.assertGreater(len(set(front.read_bytes()[100:4000])), 40)

    def test_render_rejects_sizes_outside_the_allowed_range(self):
        done = run_tool("render.py", "--workspace", str(self.directory), "--glb", str(self.glb), "--width", "8", env=self.env)
        self.assertEqual(done.returncode, 2)
        self.assertIn("--width must be within", done.stderr)

    def test_sheet_obeys_the_jev_side_limits_and_keeps_aspect(self):
        left = write_png(self.directory / "ref.png", 1024, 1536, (200, 40, 40))
        right = write_png(self.directory / "ren.png", 1024, 1536, (40, 40, 200))
        out = self.directory / "sheet.png"
        self.run_ok("sheet.py", "--workspace", str(self.directory), "--reference", str(left), "--panel", str(right), "--out", str(out))
        width, height = png_dimensions(out)
        self.assertEqual((width, height), (1568, 1176))
        self.assertTrue(280 <= width <= 1568 and 280 <= height <= 1568)
        receipt = json.loads(out.with_suffix(".json").read_text())
        self.assertEqual(receipt["panelSize"], [784, 1176])
        self.assertEqual(len(receipt["panels"]), 2)

    def test_sheet_four_panel_pick_layout_and_refusals(self):
        panels = [write_png(self.directory / f"p{i}.png", 400, 600, (60 * i, 30, 30)) for i in range(4)]
        out = self.directory / "pick.png"
        args = ["--workspace", str(self.directory), "--reference", str(panels[0]), "--out", str(out)]
        for p in panels[1:]:
            args += ["--panel", str(p)]
        self.run_ok("sheet.py", *args)
        self.assertEqual(png_dimensions(out), (1568, 588))
        single = run_tool("sheet.py", "--workspace", str(self.directory), "--reference", str(panels[0]),
                          "--out", str(self.directory / "x.png"), env=self.env)
        self.assertEqual(single.returncode, 2)
        self.assertIn("need a reference and at least one render", single.stderr)


class BpySelectionTest(unittest.TestCase):
    def test_tools_fail_clearly_without_a_selected_bpy_python(self):
        with tempfile.TemporaryDirectory() as home:
            env = {"IMG2_BLENDER_PYTHON": "", "IMG2_CHARACTER_BPY_PYTHON": "", "IMG2_HOME": home}
            body = b'{"asset":{"version":"2.0"}}  '
            (Path(home) / "m.glb").write_bytes(b"glTF" + struct.pack("<III", 2, 20 + len(body), len(body)) + b"JSON" + body)
            done = run_tool("inspect.py", "--workspace", home, "--model", str(Path(home) / "m.glb"), env=env)
        self.assertEqual(done.returncode, 2)
        self.assertIn("no bpy python selected", done.stderr)
        self.assertIn("IMG2_CHARACTER_BPY_PYTHON", done.stderr)


if __name__ == "__main__":
    unittest.main()
