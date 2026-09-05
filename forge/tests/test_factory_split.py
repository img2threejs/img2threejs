#!/usr/bin/env python3
"""The generated factory used to hard-bind the whole presentation stack
(EffectComposer / BokehPass / UnrealBloomPass / OrbitControls / RoomEnvironment)
into every file — even a single-component blockout that only wanted a model.
These tests pin the split layout: model file imports only 'three', the
presentation harness lives in its own file, every moved block is byte-identical
to the single-file rendering, and --single-file still reproduces the
historical one-file layout exactly.

Run: python forge/tests/test_factory_split.py
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = SKILL_ROOT / "forge" / "stage3_build" / "generate_threejs_factory.py"
IMPLICIT_FIXTURE = SKILL_ROOT / "forge" / "tests" / "fixtures" / "implicit_character_torso_limb.json"

sys.path.insert(0, str(SKILL_ROOT / "forge" / "stage3_build"))
from generate_threejs_factory import generate, split_factory_source  # noqa: E402

JSM_NAMES = ("RoomEnvironment", "EffectComposer", "RenderPass", "BokehPass", "UnrealBloomPass", "OrbitControls")
TYPE_NAME = "ImplicitCharacterTorsoLimb"
SUFFIXES = ("Environment", "PresentationComposer", "InspectControls")


def run_cli(*args: object) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(GENERATOR), *map(str, args)]
    # Same fixture escape hatch the other CLI-level tests use: these specs are
    # deliberately shallow, strict-quality would block them before generation.
    command.append("--allow-nonstrict")
    return subprocess.run(command, capture_output=True, text=True)


class FactorySplitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rendered = generate(__import__("json").loads(IMPLICIT_FIXTURE.read_text(encoding="utf-8")), "blockout")
        cls.model, cls.harness = split_factory_source(cls.rendered, TYPE_NAME)

    def test_model_imports_only_three(self) -> None:
        imports = [line for line in self.model.splitlines() if line.startswith("import ")]
        self.assertEqual(imports, ["import * as THREE from 'three';"])

    def test_model_has_no_jsm_references(self) -> None:
        for name in JSM_NAMES:
            self.assertNotIn(name, self.model, f"{name} leaked into the model file")

    def test_harness_carries_the_presentation_functions(self) -> None:
        for suffix in SUFFIXES:
            self.assertIn(f"export function create{TYPE_NAME}{suffix}(", self.harness, suffix)
        self.assertNotIn("createModel", self.harness, "harness must not carry the model factory")

    def test_moved_blocks_are_verbatim_from_the_single_file(self) -> None:
        for suffix in SUFFIXES:
            pattern = rf"(?://[^\n]*\n)*export function create{TYPE_NAME}{suffix}\(.*?^\}}\n"
            match = re.search(pattern, self.rendered, re.S | re.M)
            self.assertIsNotNone(match, suffix)
            block = match.group(0)
            self.assertIn(block, self.harness, f"{suffix} block changed in transit")
            self.assertNotIn(block, self.model, f"{suffix} block duplicated in the model file")

    def test_no_source_line_lost_or_duplicated(self) -> None:
        original = Counter(self.rendered.splitlines())
        combined = Counter(self.model.splitlines()) + Counter(self.harness.splitlines())
        # The split relocates every line verbatim with exactly one deliberate
        # addition: the harness re-imports 'three' alongside the jsm modules.
        expected = original + Counter({"import * as THREE from 'three';": 1})
        self.assertEqual(combined, expected, "split must relocate lines verbatim, not rewrite them")

    def test_single_file_flag_reproduces_historical_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "createImplicitCharacterTorsoLimbModel.ts"
            result = run_cli(IMPLICIT_FIXTURE, "--out", out, "--single-file")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(out.read_text(encoding="utf-8"), self.rendered)
            self.assertEqual(len(list(Path(directory).glob("*"))), 1, "single-file mode must write exactly one file")

    def test_default_split_writes_two_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "createImplicitCharacterTorsoLimbModel.ts"
            result = run_cli(IMPLICIT_FIXTURE, "--out", out)
            self.assertEqual(result.returncode, 0, result.stderr)
            harness_path = Path(directory) / "createImplicitCharacterTorsoLimbModel.harness.ts"
            self.assertTrue(out.is_file(), result.stdout)
            self.assertTrue(harness_path.is_file(), result.stdout)
            self.assertEqual(out.read_text(encoding="utf-8"), self.model)
            self.assertEqual(harness_path.read_text(encoding="utf-8"), self.harness)
            self.assertIn(str(out), result.stdout)
            self.assertIn(str(harness_path), result.stdout)

    def test_harness_identifier_closure(self) -> None:
        """Every non-THREE symbol the harness uses is imported there."""
        imported = set(re.findall(r"import \{ (\w+) \} from", self.harness))
        for name in JSM_NAMES:
            self.assertIn(name, imported, f"{name} used but not imported by the harness")


if __name__ == "__main__":
    unittest.main(verbosity=2)
