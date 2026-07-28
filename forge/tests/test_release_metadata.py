from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release_metadata.py"


class ReleaseMetadataTests(unittest.TestCase):
    def write_project(self, directory: Path) -> tuple[Path, Path]:
        (directory / "SKILL.md").write_text("---\nversion: 1.4.1\n---\n", encoding="utf-8")
        (directory / "README.md").write_text(
            "[![Version](https://img.shields.io/badge/version-1.4.1-green.svg)](CHANGELOG.md)\n",
            encoding="utf-8",
        )
        (directory / "CHANGELOG.md").write_text(
            "# Changelog\n\nIntro.\n\n## [1.4.1] — 2026-07-26\n\n- Existing.\n\n"
            "[1.4.1]: https://github.com/img2threejs/img2threejs/releases/tag/v1.4.1\n",
            encoding="utf-8",
        )
        commits = directory / "commits.txt"
        return directory, commits

    def run_release(self, directory: Path, commits: Path, dry_run: bool = False) -> subprocess.CompletedProcess[str]:
        command = [
                "python3",
                str(SCRIPT),
                "--root",
                str(directory),
                "--commits-file",
                str(commits),
                "--date",
                "2026-07-26",
                "--repository-url",
                "https://github.com/img2threejs/img2threejs",
            ]
        if dry_run:
            command.append("--dry-run")
        return subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
        )

    def test_minor_release_synchronizes_all_version_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project, commits = self.write_project(Path(temporary_directory))
            commits.write_text("feat: generic image intake\x1efix: image hash stability\x1e", encoding="utf-8")

            result = self.run_release(project, commits)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["version"], "1.5.0")
            self.assertIn("version: 1.5.0", (project / "SKILL.md").read_text(encoding="utf-8"))
            self.assertIn("version-1.5.0-green.svg", (project / "README.md").read_text(encoding="utf-8"))
            changelog = (project / "CHANGELOG.md").read_text(encoding="utf-8")
            self.assertIn("## [1.5.0] — 2026-07-26", changelog)
            self.assertIn("[1.5.0]: https://github.com/img2threejs/img2threejs/compare/v1.4.1...v1.5.0", changelog)

    def test_non_releasable_commits_leave_metadata_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project, commits = self.write_project(Path(temporary_directory))
            commits.write_text("docs: clarify usage\x1echore: refresh metadata\x1e", encoding="utf-8")
            before = (project / "SKILL.md").read_text(encoding="utf-8")

            result = self.run_release(project, commits)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)["release"])
            self.assertEqual((project / "SKILL.md").read_text(encoding="utf-8"), before)

    def test_dry_run_reports_the_next_version_without_writing_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project, commits = self.write_project(Path(temporary_directory))
            commits.write_text("fix: image hash stability\x1e", encoding="utf-8")
            before = (project / "CHANGELOG.md").read_text(encoding="utf-8")

            result = self.run_release(project, commits, dry_run=True)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["version"], "1.4.2")
            self.assertEqual((project / "CHANGELOG.md").read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
