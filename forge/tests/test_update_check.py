from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts import update_check
from scripts.update_check import check

SCRIPT = Path(update_check.__file__).resolve()


CONNECTION = "http.client.HTTPSConnection"


def connection(tag: str) -> MagicMock:
    factory = MagicMock()
    factory.return_value.getresponse.return_value.getheader.return_value = (
        f"https://github.com/img2threejs/img2threejs/releases/tag/{tag}"
    )
    return factory


class UpdateCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.stamp = self.home / ".img2threejs" / "update-check.stamp"
        self.checkout = Path(self.tmp.name) / "checkout"
        self.write_skill(self.checkout, "2.0.0")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_skill(self, root: Path, version: str) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "SKILL.md").write_text(f"---\nname: img2threejs\nversion: {version}\n---\n", encoding="utf-8")

    def set_stamp_age(self, seconds: float) -> None:
        self.stamp.parent.mkdir(parents=True, exist_ok=True)
        self.stamp.touch()
        os.utime(self.stamp, (time.time() - seconds,) * 2)

    def test_fresh_stamp_makes_no_request(self) -> None:
        self.set_stamp_age(0)
        with patch(CONNECTION) as factory:
            self.assertIsNone(check(self.home, self.checkout))
        factory.assert_not_called()

    def test_stale_stamp_makes_one_head_request(self) -> None:
        self.set_stamp_age(2 * 86400)
        factory = connection("v2.0.0")
        with patch(CONNECTION, factory):
            self.assertIsNone(check(self.home, self.checkout))
        factory.assert_called_once_with("github.com", timeout=3)
        factory.return_value.request.assert_called_once_with("HEAD", "/img2threejs/img2threejs/releases/latest")
        factory.return_value.close.assert_called_once()

    def test_failed_request_still_leaves_a_fresh_stamp(self) -> None:
        factory = MagicMock()
        factory.return_value.request.side_effect = TimeoutError("offline")
        with patch(CONNECTION, factory):
            with self.assertRaises(TimeoutError):
                check(self.home, self.checkout)
        factory.return_value.close.assert_called_once()
        self.assertLess(time.time() - self.stamp.stat().st_mtime, 60)
        with patch(CONNECTION) as again:
            self.assertIsNone(check(self.home, self.checkout))
        again.assert_not_called()

    def test_release_dir_with_git_gets_npx_advice(self) -> None:
        release = self.home / ".img2threejs" / "releases" / ("a" * 40) / "img2threejs"
        self.write_skill(release, "2.0.0")
        (release / ".git").mkdir()
        with patch(CONNECTION, connection("v2.1.0")):
            message = check(self.home, release)
        self.assertIn("v2.1.0 is available (installed 2.0.0)", message)
        self.assertIn("npx --yes img2threejs@latest update", message)
        self.assertNotIn("git -C", message)

    def test_plain_checkout_gets_git_pull_advice(self) -> None:
        with patch(CONNECTION, connection("v2.1.0")):
            message = check(self.home, self.checkout)
        self.assertIn(f"git -C {self.checkout.resolve()} pull --ff-only", message)

    def test_older_release_is_silent(self) -> None:
        with patch(CONNECTION, connection("v1.9.9")):
            self.assertIsNone(check(self.home, self.checkout))

    def test_beta_install_sees_its_stable_release(self) -> None:
        self.write_skill(self.checkout, "2.1.0-beta.2")
        with patch(CONNECTION, connection("v2.1.0")):
            self.assertIn("v2.1.0 is available", check(self.home, self.checkout))

    def test_bad_location_is_an_error_the_cli_skips(self) -> None:
        with patch(CONNECTION, connection("abc123")):
            with self.assertRaises((AttributeError, ValueError)):
                check(self.home, self.checkout)

    def test_cli_prints_skipped_and_exits_zero_on_failure(self) -> None:
        env = {**os.environ, "HOME": str(self.home)}
        self.home.mkdir(parents=True)
        (self.home / ".img2threejs").write_text("not a directory", encoding="utf-8")
        result = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "update check skipped")


if __name__ == "__main__":
    unittest.main()
