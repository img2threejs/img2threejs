from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "forge" / "_shared"))
sys.path.insert(0, str(ROOT / "forge" / "stage2_spec"))
sys.path.insert(0, str(ROOT / "forge" / "stage3_build"))

import generate_threejs_factory as generator  # noqa: E402


def spec(base_color: str) -> dict:
    return {
        "targetName": "Albedo",
        "targetId": "albedo",
        "schemaVersion": "2.1",
        "suitability": "pass",
        "coordinateFrame": {"front": "+Z", "up": "+Y", "scaleReference": "unit"},
        "silhouette": {"boundingShape": "test", "symmetry": "bilateral"},
        "proceduralStrategy": ["blockout"],
        "materials": [{
            "id": "vinyl",
            "name": "Vinyl",
            "baseColor": base_color,
            "roughness": {"base": 0.52},
            "metalness": {"base": 0.0},
            "textureless": {"declared": True, "evidence": ["no texture in the reference"]},
        }],
        "buildPasses": [{"id": "blockout", "acceptance": []}],
        "componentTree": [{
            "id": "body",
            "name": "Body",
            "level": "macro",
            "role": "body",
            "primitive": "ellipsoid",
            "topologyClass": "continuous-sculpt",
            "topologyRationale": "test",
            "parent": None,
            "material": "vinyl",
            "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative"},
            "transform": {"position": [0, 0.5, 0], "rotation": [0, 0, 0]},
        }],
    }


class AlbedoColorSpace(unittest.TestCase):
    """An authored `baseColor` hex is sRGB and must be read as sRGB.

    `new THREE.Color(r, g, b)` treats its arguments as LINEAR components. Feeding sRGB channel
    values to it skips the transfer function, and the error is largest near black because that is
    where the curve is steepest — which is exactly where a dark subject lives.
    """

    def test_the_emitted_helper_names_the_colour_space(self):
        source = generator.generate(spec("#2e2a28"), "blockout")
        self.assertIn("setStyle(source, THREE.SRGBColorSpace)", source)
        self.assertNotIn("new THREE.Color(red / 255, green / 255, blue / 255)", source)

    def test_a_dark_albedo_survives_the_round_trip_at_the_right_value(self):
        """Executed, not grepped: the emitted expression is evaluated and compared to sRGB→linear.

        A near-black vinyl authored at #2e2a28 is sRGB 0.180; its linear value is about 0.0273.
        Reading it as linear instead would leave 0.180 in the working space and render it at
        roughly sRGB 0.46 — a mid grey where the reference has near-black.
        """
        node = shutil.which("node")
        if node is None:
            self.fail("node is required to evaluate the emitted colour conversion")
        harness = """
        import * as THREE from 'three';
        const wrong = new THREE.Color(0x2e / 255, 0x2a / 255, 0x28 / 255);
        const right = new THREE.Color().setStyle('#2e2a28', THREE.SRGBColorSpace);
        console.log(JSON.stringify({
          wrong: [wrong.r, wrong.g, wrong.b],
          right: [right.r, right.g, right.b],
        }));
        """
        showcase = ROOT.parent / "img2threejs-showcase"
        if not (showcase / "node_modules" / "three").is_dir():
            self.skipTest(
                "three is not installed at "
                f"{showcase / 'node_modules' / 'three'}; the numeric half of this check needs it. "
                "The emitted-source assertion above still runs."
            )
        work = showcase / "node_modules" / ".cache" / "albedo-color-space"
        work.mkdir(parents=True, exist_ok=True)
        script = work / "check.mjs"
        script.write_text(harness, encoding="utf-8")
        result = subprocess.run([node, str(script)], capture_output=True, text=True)
        script.unlink(missing_ok=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        # sRGB 0.1804 -> linear 0.0273. The two readings differ by more than 6x.
        self.assertAlmostEqual(values["right"][0], 0.0273, places=3)
        self.assertAlmostEqual(values["wrong"][0], 0.1804, places=3)
        self.assertGreater(values["wrong"][0] / values["right"][0], 6.0)

    def test_a_mid_grey_is_also_affected_so_this_is_not_a_black_only_quirk(self):
        source = generator.generate(spec("#808080"), "blockout")
        self.assertIn("setStyle(source, THREE.SRGBColorSpace)", source)


if __name__ == "__main__":
    unittest.main()
