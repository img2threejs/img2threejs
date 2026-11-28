"""Required CI coverage must fail, not pass or skip, when its fixture is absent."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

RUNNER = Path(__file__).resolve().with_name("run_ci.py")


class CIProfileFailureTests(unittest.TestCase):
    def invoke(self, runner, root):
        return subprocess.run([sys.executable, str(runner), "cs2-contract"], cwd=root,
            env={"PATH": os.defpath, "HOME": str(root), "TMPDIR": str(root),
                 "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                 "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
            capture_output=True, text=True, timeout=30)

    def test_missing_required_fixture_is_a_failing_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.invoke(RUNNER, Path(directory))
        self.assertEqual(result.returncode, 1)

    def test_empty_required_profile_is_not_a_successful_suite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relocated = root / "forge/tests/run_ci.py"
            relocated.parent.mkdir(parents=True)
            shutil.copyfile(RUNNER, relocated)
            result = self.invoke(relocated, root)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
