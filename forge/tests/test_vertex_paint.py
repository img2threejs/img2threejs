from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "forge" / "_shared"))
sys.path.insert(0, str(ROOT / "forge" / "stage2_spec"))
sys.path.insert(0, str(ROOT / "forge" / "stage3_build"))

from vertex_paint import (  # noqa: E402
    VertexPaintError,
    dominant_region,
    normalize_vertex_paint,
    paint_point,
    region_weight,
    signed_distance,
)

PAINT = {
    "baseColor": "#1d1b1a",
    "regions": [
        {
            "id": "sock",
            "kind": "axis-band",
            "axis": "y",
            "min": -0.50,
            "max": -0.32,
            "color": "#f2efe9",
            "softness": 0.0,
        },
        {
            "id": "nose",
            "kind": "ellipsoid",
            "center": [0.0, 0.1, 0.4],
            "radii": [0.06, 0.04, 0.05],
            "color": "#141212",
            "softness": 0.01,
        },
        {
            "id": "blaze",
            "kind": "tapered-capsule",
            "start": [0.0, 0.30, 0.20],
            "end": [0.0, -0.05, 0.36],
            "startRadius": 0.03,
            "endRadius": 0.12,
            "color": "#f2efe9",
            "softness": 0.02,
        },
    ],
}

SAMPLE_POINTS = [
    [0.0, -0.40, 0.0],
    [0.0, -0.20, 0.0],
    [0.0, 0.10, 0.40],
    [0.0, 0.00, 0.34],
    [0.0, 0.30, 0.20],
    [0.25, 0.00, 0.34],
    [0.0, -0.325, 0.0],
    [0.02, 0.12, 0.42],
]


class ShapePredicates(unittest.TestCase):
    def test_axis_band_is_inside_between_its_planes_and_outside_beyond_them(self):
        region = normalize_vertex_paint(PAINT)["regions"][0]
        self.assertLess(signed_distance(region, [0, -0.40, 0]), 0)
        self.assertGreater(signed_distance(region, [0, -0.20, 0]), 0)
        self.assertGreater(signed_distance(region, [0, -0.60, 0]), 0)

    def test_a_hard_boundary_gives_only_zero_or_one_weight(self):
        region = normalize_vertex_paint(PAINT)["regions"][0]
        for point in ([0, -0.40, 0], [0, -0.20, 0], [0, -0.32, 0], [0, -0.3199, 0]):
            self.assertIn(region_weight(region, point), (0.0, 1.0))

    def test_softness_produces_intermediate_weights_only_inside_its_band(self):
        region = normalize_vertex_paint(PAINT)["regions"][1]
        self.assertEqual(region_weight(region, [0.0, 0.1, 0.4]), 1.0)
        self.assertEqual(region_weight(region, [0.0, 0.1, 0.9]), 0.0)
        edge = region_weight(region, [0.0, 0.1, 0.45])
        self.assertGreater(edge, 0.0)
        self.assertLess(edge, 1.0)

    def test_tapered_capsule_radius_grows_from_start_to_end(self):
        region = normalize_vertex_paint(PAINT)["regions"][2]
        near_start = signed_distance(region, [0.05, 0.30, 0.20])
        near_end = signed_distance(region, [0.05, -0.05, 0.36])
        self.assertGreater(near_start, 0.0, "0.05 out is beyond the 0.03 start radius")
        self.assertLess(near_end, 0.0, "0.05 out is inside the 0.12 end radius")

    def test_later_regions_win_where_they_overlap(self):
        paint = normalize_vertex_paint(PAINT)
        # The blaze's wide end covers the nose pad's centre; the blaze is declared later.
        overlap = [0.0, -0.02, 0.35]
        self.assertEqual(dominant_region(paint, overlap), "blaze")

    def test_a_point_claimed_by_nothing_keeps_the_base_colour(self):
        paint = normalize_vertex_paint(PAINT)
        self.assertEqual(dominant_region(paint, [0.9, 0.9, 0.9]), "base")
        self.assertEqual(
            [round(c, 6) for c in paint_point(paint, [0.9, 0.9, 0.9])],
            [round(int("1d", 16) / 255, 6), round(int("1b", 16) / 255, 6), round(int("1a", 16) / 255, 6)],
        )


class Rejection(unittest.TestCase):
    def test_an_unknown_kind_is_rejected_rather_than_ignored(self):
        with self.assertRaises(VertexPaintError):
            normalize_vertex_paint({"baseColor": "#000000", "regions": [{"id": "x", "kind": "blob", "color": "#ffffff"}]})

    def test_a_zero_radius_ellipsoid_is_rejected(self):
        with self.assertRaises(VertexPaintError):
            normalize_vertex_paint(
                {
                    "baseColor": "#000000",
                    "regions": [
                        {"id": "x", "kind": "ellipsoid", "center": [0, 0, 0], "radii": [0.1, 0, 0.1], "color": "#fff000"}
                    ],
                }
            )

    def test_a_capsule_with_identical_endpoints_is_rejected(self):
        with self.assertRaises(VertexPaintError):
            normalize_vertex_paint(
                {
                    "baseColor": "#000000",
                    "regions": [
                        {
                            "id": "x",
                            "kind": "tapered-capsule",
                            "start": [0, 0, 0],
                            "end": [0, 0, 0],
                            "startRadius": 0.1,
                            "endRadius": 0.2,
                            "color": "#ffffff",
                        }
                    ],
                }
            )

    def test_duplicate_region_ids_are_rejected(self):
        with self.assertRaises(VertexPaintError):
            normalize_vertex_paint(
                {
                    "baseColor": "#000000",
                    "regions": [
                        {"id": "a", "kind": "axis-band", "axis": "y", "min": 0, "max": 1, "color": "#ffffff"},
                        {"id": "a", "kind": "axis-band", "axis": "y", "min": 2, "max": 3, "color": "#ffffff"},
                    ],
                }
            )

    def test_a_bad_colour_string_is_rejected(self):
        with self.assertRaises(VertexPaintError):
            normalize_vertex_paint({"baseColor": "black", "regions": PAINT["regions"]})


class SpecValidation(unittest.TestCase):
    def _spec_with(self, component_extra: dict) -> dict:
        return {
            "targetName": "T",
            "schemaVersion": "2.1",
            "componentTree": [
                {
                    "id": "root",
                    "name": "root",
                    "level": "macro",
                    "role": "body",
                    "primitive": "ellipsoid",
                    "topologyClass": "continuous-sculpt",
                    "topologyRationale": "test",
                    "parent": None,
                    "dimensions": {"width": 1, "height": 1, "depth": 1, "units": "relative"},
                    **component_extra,
                }
            ],
        }

    def test_validator_rejects_a_malformed_paint_block(self):
        from validate_sculpt_spec import validate_components

        errors: list[str] = []
        warnings: list[str] = []
        spec = self._spec_with({"vertexPaint": {"baseColor": "#000000", "regions": []}})
        validate_components(spec, set(), set(), errors, warnings)
        self.assertTrue(any("vertexPaint.regions" in error for error in errors), errors)

    def test_validator_rejects_paint_and_gradient_on_the_same_component(self):
        from validate_sculpt_spec import validate_components

        errors: list[str] = []
        warnings: list[str] = []
        spec = self._spec_with(
            {
                "vertexPaint": PAINT,
                "rootTipGradient": {"rootColor": "#000000", "tipColor": "#ffffff", "axis": "y"},
            }
        )
        validate_components(spec, set(), set(), errors, warnings)
        self.assertTrue(
            any("both write the vertex colour attribute" in error for error in errors), errors
        )

    def test_validator_accepts_a_well_formed_paint_block(self):
        from validate_sculpt_spec import validate_components

        errors: list[str] = []
        warnings: list[str] = []
        validate_components(self._spec_with({"vertexPaint": PAINT}), set(), set(), errors, warnings)
        self.assertFalse([error for error in errors if "vertexPaint" in error], errors)


class TypeScriptParity(unittest.TestCase):
    """The emitted TS and the Python predicates must agree to floating-point noise.

    They are two transcriptions of the same maths, and the gate reads the Python one while the
    render reads the TS one. If they drift, the gate passes a boundary the viewer never sees.
    """

    def test_python_and_typescript_agree_on_every_sample_point(self):
        node = shutil.which("node")
        if node is None:
            self.fail(
                "node is required for the vertex-paint parity check; without it the Python gate "
                "and the emitted TypeScript are never compared and may silently diverge"
            )
        from generate_threejs_factory import _VERTEX_PAINT_HELPER_SOURCE

        paint = normalize_vertex_paint(PAINT)
        # The helper's only THREE dependency is Color/BufferAttribute; a tiny stub keeps this a
        # pure maths comparison and avoids needing three installed to check the arithmetic.
        harness = (
            "const THREE = {\n"
            "  Color: class { r = 0; g = 0; b = 0; constructor(hex?: string){ if (hex) this.set(hex); }\n"
            "    set(hex: string){ const n = parseInt(hex.slice(1), 16);\n"
            "      this.r = ((n >> 16) & 255) / 255; this.g = ((n >> 8) & 255) / 255;"
            " this.b = (n & 255) / 255; return this; }\n"
            "    copy(o: any){ this.r = o.r; this.g = o.g; this.b = o.b; return this; }\n"
            "    lerp(o: any, t: number){ this.r += (o.r - this.r) * t;"
            " this.g += (o.g - this.g) * t; this.b += (o.b - this.b) * t; return this; }\n"
            "  },\n"
            "  BufferAttribute: class { array: any; itemSize: number; count: number;\n"
            "    constructor(array: any, size: number){ this.array = array; this.itemSize = size;"
            " this.count = array.length / size; }\n"
            "    getX(i: number){ return this.array[i * this.itemSize]; }\n"
            "    getY(i: number){ return this.array[i * this.itemSize + 1]; }\n"
            "    getZ(i: number){ return this.array[i * this.itemSize + 2]; } },\n"
            "} as any;\n"
            + _VERTEX_PAINT_HELPER_SOURCE
            + "\n"
            "const points = " + json.dumps(SAMPLE_POINTS) + ";\n"
            "const flat = new Float64Array(points.length * 3);\n"
            "points.forEach((p, i) => { flat[i*3] = p[0]; flat[i*3+1] = p[1]; flat[i*3+2] = p[2]; });\n"
            "const geometry: any = { attributes: {} as any,\n"
            "  getAttribute(){ return new THREE.BufferAttribute(flat, 3); },\n"
            "  setAttribute(name: string, attribute: any){ this.attributes[name] = attribute; } };\n"
            "applyVertexPaint(geometry, " + json.dumps(paint["baseColor"]) + ", "
            + json.dumps(paint["regions"]) + " as any);\n"
            "console.log(JSON.stringify(Array.from(geometry.attributes.color.array)));\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "parity.ts"
            script.write_text(harness, encoding="utf-8")
            result = subprocess.run(
                [node, "--experimental-strip-types", "--no-warnings", str(script)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            ts_colors = json.loads(result.stdout)

        py_colors: list[float] = []
        for point in SAMPLE_POINTS:
            py_colors.extend(paint_point(paint, point))

        self.assertEqual(len(ts_colors), len(py_colors))
        for index, (ts_value, py_value) in enumerate(zip(ts_colors, py_colors)):
            # The TS side stores into a Float32Array, so the only admissible difference is that
            # single rounding. Comparing against the float32 image of the Python value makes the
            # assertion exact rather than tolerance-based, which is what keeps it able to catch a
            # small genuine divergence.
            expected = struct.unpack("f", struct.pack("f", py_value))[0]
            self.assertEqual(ts_value, expected, msg=f"channel {index}")

    def test_the_parity_harness_would_catch_a_divergence(self):
        """Negative control: perturbing the Python side must break the comparison.

        Without this, a parity test that silently compared nothing would still pass.
        """
        paint = normalize_vertex_paint(PAINT)
        baseline = paint_point(paint, SAMPLE_POINTS[0])
        mutated = dict(paint)
        mutated["regions"] = [dict(region) for region in paint["regions"]]
        mutated["regions"][0]["min"] = -0.10
        self.assertNotEqual(baseline, paint_point(mutated, SAMPLE_POINTS[0]))


class GeneratorEmission(unittest.TestCase):
    def test_the_helper_is_emitted_only_when_a_component_declares_paint(self):
        from generate_threejs_factory import vertex_paint

        self.assertIsNone(vertex_paint({"id": "a"}))
        self.assertIsNotNone(vertex_paint({"id": "a", "vertexPaint": PAINT}))

    def test_a_painted_component_gets_a_white_material_albedo(self):
        """Otherwise the authored albedo is applied twice.

        `vertexColors` MULTIPLIES material.color by the vertex colour, and a paint block already
        carries the component's full albedo. Leaving the material's own colour in place squares it:
        a 0.027 linear black against a 0.027 black material renders at 0.0007, and a 0.937 white
        sock comes out at 0.025 — darker than the fur it is supposed to stand out from.
        """
        from generate_threejs_factory import generate

        source = generate(_painted_spec(), "blockout")
        self.assertIn("material.vertexColors = true;", source)
        self.assertIn("as THREE.MeshPhysicalMaterial).color.setRGB(1, 1, 1);", source)

    def test_a_gradient_component_keeps_its_material_albedo(self):
        """Negative control. A root-to-tip gradient is SHADING and is meant to multiply."""
        from generate_threejs_factory import generate

        spec = _painted_spec()
        component = spec["componentTree"][0]
        del component["vertexPaint"]
        component["rootTipGradient"] = {"rootColor": "#000000", "tipColor": "#ffffff", "axis": "y"}
        source = generate(spec, "blockout")
        self.assertIn("material.vertexColors = true;", source)
        self.assertNotIn("color.setRGB(1, 1, 1);", source)

    def test_an_unpainted_component_does_not_enable_vertex_colours_at_all(self):
        from generate_threejs_factory import generate

        spec = _painted_spec()
        del spec["componentTree"][0]["vertexPaint"]
        source = generate(spec, "blockout")
        self.assertNotIn("material.vertexColors = true;", source)


def _painted_spec():
    return {
        "targetName": "Painted",
        "targetId": "painted",
        "schemaVersion": "2.1",
        "suitability": "pass",
        "coordinateFrame": {"front": "+Z", "up": "+Y", "scaleReference": "unit"},
        "silhouette": {"boundingShape": "test", "symmetry": "bilateral"},
        "proceduralStrategy": ["blockout"],
        "materials": [{"id": "fur", "name": "Fur", "baseColor": "#2e2a28"}],
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
            "material": "fur",
            "dimensions": {"width": 1.0, "height": 1.0, "depth": 1.0, "units": "relative"},
            "transform": {"position": [0, 0.5, 0], "rotation": [0, 0, 0]},
            "vertexPaint": PAINT,
        }],
    }


class MalformedDeclaration(unittest.TestCase):
    def test_a_malformed_declaration_raises_instead_of_degrading_to_flat_colour(self):
        from generate_threejs_factory import vertex_paint

        with self.assertRaises(ValueError):
            vertex_paint({"id": "a", "vertexPaint": {"baseColor": "#000000", "regions": []}})


if __name__ == "__main__":
    unittest.main()
