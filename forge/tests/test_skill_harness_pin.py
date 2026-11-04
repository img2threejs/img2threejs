from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = "@img2threejs/img2@"
PINNED = re.compile(r"@img2threejs/img2@(\d+\.\d+\.\d+)")
ALLOWED = re.compile(
    r"npx --yes @img2threejs/img2@(?:\d+\.\d+\.\d+ (?:plugins --json|ensure <id>|ensure \*|install --yes --auto-install)"
    r"|latest update)(?=[`)])"
)


def read(relative: str) -> str:
    return " ".join((ROOT / relative).read_text(encoding="utf-8").split())


def allowed_tools() -> list[str]:
    frontmatter = (ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
    block = frontmatter.split("allowed-tools:", 1)[1]
    return re.findall(r"^\s+- (.+)$", block, re.MULTILINE)


class SkillHarnessPinTest(unittest.TestCase):
    def test_pinned_harness_version_matches_across_docs(self) -> None:
        versions = {name: set(PINNED.findall(read(name))) for name in ("SKILL.md", "docs/INSTALL.md", "docs/PLUGINS.md")}
        self.assertEqual(len(versions["SKILL.md"]), 1, versions)
        self.assertEqual(versions["docs/INSTALL.md"], versions["SKILL.md"], versions)
        self.assertEqual(versions["docs/PLUGINS.md"], versions["SKILL.md"], versions)

    def test_every_harness_command_in_skill_is_an_allowed_form(self) -> None:
        text = read("SKILL.md")
        self.assertGreater(text.count(HARNESS), 0)
        self.assertEqual(len(ALLOWED.findall(text)), text.count(HARNESS), text)

    def test_allowlist_pre_approves_only_catalog_read_and_ensure(self) -> None:
        version = PINNED.search(read("SKILL.md")).group(1)
        self.assertEqual(
            allowed_tools(),
            [f"Bash(npx --yes {HARNESS}{version} plugins --json)", f"Bash(npx --yes {HARNESS}{version} ensure *)"],
        )

    def test_skill_has_no_bare_img2_command_inline_or_fenced(self) -> None:
        raw = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotRegex(raw, r"`img2 ")
        self.assertNotRegex(raw, r"(?m)^\s*img2 ")


if __name__ == "__main__":
    unittest.main()
