"""Generated materials must be able to carry the environment as their own envMap (Refs #193).

three.js r163+ lights a MeshStandardMaterial / MeshPhysicalMaterial whose `material.envMap` is null with
`scene.environmentIntensity`, and copies `material.envMapIntensity` into the shader only when
`material.envMap` is set (WebGLRenderer / WebGLMaterials). The generator used to hand the PMREM environment
to `scene.environment` only, so every per-material `envMapIntensity` (the default, and recipe values such
as candy-coat's 0.7) had no effect on the render.

These tests check the emitted source. The rendered effect (0.7 versus 1.0 differing once the environment
is bound) needs WebGL and is recorded in the pull request rather than in this dependency-free suite.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "forge" / "_shared"))
sys.path.insert(0, str(ROOT / "forge" / "stage2_spec"))
sys.path.insert(0, str(ROOT / "forge" / "stage3_build"))

import generate_threejs_factory as generator  # noqa: E402

SPEC = ROOT / "forge" / "tests" / "fixtures" / "reference_target_conformance_spec.json"


def _source() -> str:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    return generator.generate(spec, "blockout")


def _function_body(source: str, signature_pattern: str) -> str:
    """The text of one emitted top-level function, from its signature to the closing brace at column 0."""
    match = re.search(signature_pattern, source)
    if match is None:
        raise AssertionError(f"no emitted function matches {signature_pattern!r}")
    end = re.compile(r"\n\};?\n").search(source, match.end())
    if end is None:
        raise AssertionError(f"emitted block {signature_pattern!r} has no closing brace")
    return source[match.start():end.start()]


class EnvMapBinding(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = _source()
        found = re.search(r"export function create(\w+)Environment\(", cls.source)
        if found is None:
            raise AssertionError("the generator no longer emits an environment helper")
        cls.type_name = found.group(1)

    def test_the_model_options_accept_the_environment(self) -> None:
        options = _function_body(self.source, r"export type ProceduralModelOptions = \{")
        self.assertRegex(options, r"\n  environment\?: THREE\.Texture \| null;")

    def test_every_sculpt_material_binds_the_environment_it_is_given(self) -> None:
        body = _function_body(self.source, r"function createSculptMaterial\(")
        intensity = body.find("material.envMapIntensity = ")
        binding = body.find("if (options.environment) material.envMap = options.environment;")
        self.assertGreaterEqual(intensity, 0, "createSculptMaterial no longer sets envMapIntensity")
        self.assertGreaterEqual(binding, 0, "createSculptMaterial never sets material.envMap")
        # Bound on the same material the intensity was written to, before the material is returned.
        self.assertGreater(binding, intensity)
        self.assertLess(binding, body.find("return material;"))

    def test_a_model_built_without_the_option_can_be_bound_afterwards(self) -> None:
        helper = _function_body(
            self.source,
            rf"export function bind{self.type_name}Environment\(root: THREE\.Object3D, environment: THREE\.Texture \| null\): void \{{",
        )
        self.assertIn("root.traverse(", helper)
        # Only the generator's own materials, so a caller's hand-made materials keep their own envMap.
        self.assertIn("userData?.sculptMaterial", helper)
        self.assertIn(".envMap = environment;", helper)
        # A material that already compiled without an envMap needs a new program to sample one.
        self.assertIn("needsUpdate = true;", helper)

    def test_the_environment_helper_says_to_pass_it_to_the_model(self) -> None:
        start = self.source.find(f"export function create{self.type_name}Environment(")
        comment = self.source[self.source.rfind("// PBR materials", 0, start):start]
        self.assertIn("options.environment", comment)
        self.assertIn(f"bind{self.type_name}Environment", comment)


if __name__ == "__main__":
    unittest.main()
