#!/usr/bin/env python3
"""Hair reads as hair through shading, because in this pipeline it cannot read as hair through
geometry.

THE MEASUREMENT THAT REORDERED THIS. The reference these gates are calibrated against is a merged
570,400-vertex scan. Its hair surface roughness, measured as the radial step between adjacent
azimuth bins, is 0.00338 -- against 0.00312 for a torso with no hair on it at all. The hair is a
smooth shell and its entire strand appearance lives in the diffuse and normal textures.

img2threejs emits code and no textures. So the strand impression has to come from sheen, anisotropy
and a root-to-tip ramp. That is the opposite of the earlier judgement in this session, which was
that shading should be deferred because "a shader cannot move a geometry score" -- four attempts to
close the gap with geometry all failed, and the last one made every view worse.

Run: python3 forge/tests/test_hair_material.py
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage3_build"))

from generate_threejs_factory import generate, root_tip_gradient  # noqa: E402
from materials.reference import load_reference, validate_reference  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "stand_proud_hair_on_head.json"


def fixture_spec() -> dict:
    return json.loads(FIXTURE.read_text())


class TheMaterialReference(unittest.TestCase):
    def setUp(self) -> None:
        self.reference = load_reference()
        self.by_id = {m["id"]: m for m in self.reference["materials"]}

    def test_the_registry_still_validates(self) -> None:
        self.assertEqual(validate_reference(self.reference), [])

    def test_hair_human_now_carries_sheen(self) -> None:
        """It described anisotropy and no sheen, which leaves the broad highlight band with nothing
        to come from once there are no maps."""
        prior = self.by_id["hair.human"]["renderPrior"]
        self.assertIn("sheen", prior)
        self.assertIn("sheenRoughness", prior)

    def test_a_code_only_hair_profile_exists_and_needs_no_maps(self) -> None:
        profile = self.by_id["hair.human.code-only"]
        self.assertEqual(profile["requiredMaps"], [])
        self.assertEqual(profile["optionalMaps"], [])

    def test_the_code_only_profile_leans_harder_on_shading_than_the_textured_one(self) -> None:
        """With no anisotropyMap and no normalMap, what is left has to work harder."""
        textured = self.by_id["hair.human"]["renderPrior"]
        code_only = self.by_id["hair.human.code-only"]["renderPrior"]
        self.assertGreater(code_only["anisotropy"]["default"], textured["anisotropy"]["default"])
        self.assertGreater(code_only["sheen"]["default"], textured["sheen"]["default"])

    def test_the_code_only_profile_admits_it_is_not_calibrated(self) -> None:
        limitations = " ".join(self.by_id["hair.human.code-only"]["limitations"])
        self.assertIn("Not calibrated", limitations)
        self.assertIn("0.00338", limitations)

    def test_both_hair_profiles_label_their_basis_as_a_prior(self) -> None:
        for material_id in ("hair.human", "hair.human.code-only"):
            with self.subTest(material_id=material_id):
                self.assertEqual(self.by_id[material_id]["recipeBasis"], "inferred-starting-prior")


class GradientDescriptor(unittest.TestCase):
    def test_a_well_formed_gradient_is_read(self) -> None:
        parsed = root_tip_gradient(
            {"rootTipGradient": {"rootColor": "#111", "tipColor": "#eee", "axis": "z"}}
        )
        self.assertEqual(parsed, {"rootColor": "#111", "tipColor": "#eee", "axis": "z"})

    def test_the_axis_defaults_to_y(self) -> None:
        parsed = root_tip_gradient({"rootTipGradient": {"rootColor": "#111", "tipColor": "#eee"}})
        self.assertEqual(parsed["axis"], "y")

    def test_an_unknown_axis_falls_back_to_y_rather_than_emitting_nonsense(self) -> None:
        parsed = root_tip_gradient(
            {"rootTipGradient": {"rootColor": "#111", "tipColor": "#eee", "axis": "w"}}
        )
        self.assertEqual(parsed["axis"], "y")

    def test_a_malformed_gradient_is_none_not_a_default(self) -> None:
        for bad in ({}, {"rootTipGradient": "dark"}, {"rootTipGradient": {"rootColor": "#111"}},
                    {"rootTipGradient": {"rootColor": 1, "tipColor": 2}}):
            with self.subTest(bad=bad):
                self.assertIsNone(root_tip_gradient(bad))


class GradientEmission(unittest.TestCase):
    def test_nothing_is_emitted_when_no_component_asks_for_one(self) -> None:
        spec = fixture_spec()
        for component in spec["componentTree"]:
            component.pop("rootTipGradient", None)
        source = generate(spec, "structural-pass")
        self.assertNotIn("applyRootTipGradient", source)

    def test_the_helper_and_the_call_are_emitted(self) -> None:
        source = generate(fixture_spec(), "structural-pass")
        self.assertIn("function applyRootTipGradient(", source)
        self.assertIn('applyRootTipGradient(', source)
        self.assertIn('"#1a1310"', source)
        self.assertIn('"#4a3a2c"', source)

    def test_the_ramp_runs_along_the_mass_own_axis(self) -> None:
        """World Y would darken a sideways-sweeping fringe at whatever end happens to be lowest."""
        source = generate(fixture_spec(), "structural-pass")
        self.assertIn("axis === 'x' ? position.getX(i) : axis === 'z' ? position.getZ(i)", source)

    def test_a_mass_with_no_extent_does_not_divide_by_zero(self) -> None:
        source = generate(fixture_spec(), "structural-pass")
        self.assertIn("span > 1e-9", source)

    def test_the_material_is_cloned_before_vertex_colours_are_enabled(self) -> None:
        """Materials are shared by id; flipping the flag in place would tint unrelated components."""
        source = generate(fixture_spec(), "structural-pass")
        self.assertIn(".material.clone();", source)
        self.assertIn(".material.vertexColors = true;", source)
        self.assertLess(
            source.index(".material.clone();"),
            source.index(".material.vertexColors = true;"),
        )

    def test_the_gradient_runs_after_the_scale(self) -> None:
        """Before it, the ramp would span the unit form rather than the mass's real extent.

        Anchored on the LAST call site rather than the first match: the first match is the helper's
        own signature, which sits near the top of the file and would make this assertion pass or
        fail for reasons unrelated to the ordering it is checking.
        """
        source = generate(fixture_spec(), "structural-pass")
        call_at = source.rindex("  applyRootTipGradient(")
        scale_before = source.rindex("Geometry.scale(", 0, call_at)
        self.assertLess(scale_before, call_at)
        # ... and the two belong to the same component block, not to different ones.
        self.assertNotIn("new THREE.Mesh(", source[scale_before:call_at])


class EmittedSourceTypechecks(unittest.TestCase):
    def test_a_factory_with_hair_shading_typechecks(self) -> None:
        import subprocess  # noqa: PLC0415

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from showcase_test_support import showcase_root  # noqa: PLC0415

        root = showcase_root()
        destination = root / "src" / "__hair_material_smoke__.ts"
        self.assertFalse(destination.exists(), "a previous run left its smoke source behind")

        source = generate(fixture_spec(), "structural-pass")
        self.assertIn("function applyRootTipGradient(", source)
        try:
            destination.write_text(source, encoding="utf-8")
            result = subprocess.run(
                ["npx", "tsc", "--noEmit"], cwd=root, capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        finally:
            if destination.exists():
                destination.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
