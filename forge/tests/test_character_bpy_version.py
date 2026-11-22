"""The character bpy probe accepts the version strings real bpy builds print."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

FORGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(FORGE), str(FORGE / "_shared")]
from decision_control import DecisionController
from stage0_route.discovery import Discovery, character_bpy_version

EXE = "/opt/bpy/bin/python"


def probe(version, *, executable=EXE):
    return json.dumps({"kind": "img2.character-bpy-probe", "version": 1,
                       "executable": executable, "bpyVersion": version,
                       "bpyModule": "/opt/bpy/__init__.py"})


class CharacterBpyVersionTest(unittest.TestCase):
    def test_version_strings(self):
        cases = [
            ("4.3.0", (4, 3, 0)), ("4.3", (4, 3, 0)), ("4.5.15 LTS", (4, 5, 15)),
            ("4.5 LTS", (4, 5, 0)), ("5.0.0", (5, 0, 0)),
            ("4.5.15 alpha", None), ("4.5.15-LTS", None), ("4.5.15 LTS extra", None),
            ("4.5.15  LTS", None), ("4.5.15 lts", None), ("4.5.15 LTS LTS", None),
            ("4.5.15LTS", None), ("", None), ("LTS", None), ("abc", None), ("-4.5.15", None),
            ("4", None), ("4.5.15.1", None), (" 4.5.15", None), ("4.5.15 ", None),
            ("4.5.15 LTS ", None), ("4.5.15\n", None), ("4.5.15 LTS\n", None),
            ("4.5.15\nLTS", None), (4.5, None), (None, None), (["4.5.15"], None),
            ("\uff14.\uff15.\uff11\uff15", None), ("\uff14.\uff15.\uff11\uff15 LTS", None),
            ("4.\u0665.15", None), ("4.5.\u0661\u0665 LTS", None),
        ]
        for text, expected in cases:
            with self.subTest(version=text):
                self.assertEqual(character_bpy_version(0, probe(text), EXE), expected)

    def test_other_probe_checks_still_hold(self):
        self.assertIsNone(character_bpy_version(1, probe("4.5.15 LTS"), EXE))
        self.assertIsNone(character_bpy_version(0, probe("4.5.15 LTS"), "/other/python"))
        self.assertIsNone(character_bpy_version(0, "not json", EXE))
        for key, bad in (("kind", "x"), ("version", 2), ("bpyModule", 1)):
            value = json.loads(probe("4.5.15 LTS"))
            value[key] = bad
            self.assertIsNone(character_bpy_version(0, json.dumps(value), EXE))


class CharacterBpyDependencyTest(unittest.TestCase):
    def dependency(self, version):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            stub = root / "bin" / "python"
            stub.parent.mkdir()
            stub.write_text("#!" + sys.executable + "\nimport json\nprint(json.dumps(" + repr({
                "kind": "img2.character-bpy-probe", "version": 1, "executable": str(stub),
                "bpyVersion": version, "bpyModule": "/stub/bpy/__init__.py"}) + "))\n")
            stub.chmod(0o755)
            with patch.dict(os.environ, {"IMG2_CHARACTER_BPY_PYTHON": str(stub)}):
                controller = DecisionController(root, "bpy-run", clock=lambda: 1000)
                return Discovery(controller).dependency("character-bpy>=4.3")

    def test_stub_probe_versions(self):
        for version, expected in (("4.5.15 LTS", True), ("4.3.0", True), ("4.2.9 LTS", False),
                                  ("5.0.0", True), ("4.5.15 alpha", False)):
            with self.subTest(version=version):
                self.assertIs(self.dependency(version), expected)


if __name__ == "__main__":
    unittest.main()
