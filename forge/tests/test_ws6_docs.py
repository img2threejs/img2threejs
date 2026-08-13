#!/usr/bin/env python3
"""WS6 documentation and version metadata guard tests."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GRIMOIRE_BUILD = ROOT / "grimoire" / "build"
SKILL = ROOT / "SKILL.md"
README = ROOT / "README.md"
CHANGELOG = ROOT / "CHANGELOG.md"

# Both release paths read `metadata.version`: `scripts/release_metadata.py` with VERSION_PATTERN,
# and `.github/workflows/beta-release.yml` with an equivalent sed. Accept the stable and the beta
# spellings the beta workflow round-trips between.
SKILL_VERSION_PATTERN = re.compile(r"^  version: (\d+\.\d+\.\d+(?:-beta\.\d+)?)$", re.MULTILINE)
README_BADGE_PATTERN = re.compile(r"version-(\d+\.\d+\.\d+(?:-beta\.\d+)?)-green\.svg")


class Ws6DocsTest(unittest.TestCase):
    def test_ws6_docs_exist(self) -> None:
        for relative_path in (
            "implicit_sdf_modeling.md",
            "visual_hull_reconstruction.md",
            "subdivision_surfaces.md",
        ):
            with self.subTest(relative_path=relative_path):
                self.assertTrue((GRIMOIRE_BUILD / relative_path).exists())

    def test_fitting_doc_mentions_divine_eye_loop(self) -> None:
        text = (GRIMOIRE_BUILD / "analysis_by_synthesis_fitting.md").read_text(encoding="utf-8")
        for marker in (
            "fit_against_divine_eye()",
            "correction_loop.decide()",
            "bestRawFidelity",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)

    def test_skill_version_is_codex_compatible_and_reachable_by_release_tooling(self) -> None:
        """Assert against the real SKILL.md, never a fixture.

        Codex rejects an unrecognized top-level `version` key, so the release version lives under
        the supported `metadata` mapping. Both release paths must keep matching that exact shape.
        A fixture-only test cannot catch the real SKILL.md drifting away from the release tooling.
        """
        skill_text = SKILL.read_text(encoding="utf-8")
        self.assertIsNone(
            re.search(r"^version:", skill_text, re.MULTILINE),
            "SKILL.md must not use the unsupported top-level `version` key",
        )
        match = SKILL_VERSION_PATTERN.search(skill_text)
        self.assertIsNotNone(
            match,
            "SKILL.md needs one `metadata.version` field for the release tooling",
        )

    def test_readme_declares_one_version_badge_matching_the_skill(self) -> None:
        """A duplicated badge block let README carry two different versions at once.

        `release_metadata.py` rewrites with count=1, so a second badge is never updated and goes
        stale silently. Pin the count as well as the value.
        """
        skill_match = SKILL_VERSION_PATTERN.search(SKILL.read_text(encoding="utf-8"))
        assert skill_match is not None  # the test above reports this failure properly
        badges = README_BADGE_PATTERN.findall(README.read_text(encoding="utf-8"))

        self.assertEqual(len(badges), 1, f"README must declare exactly one version badge, got {badges}")
        self.assertEqual(badges[0], skill_match.group(1))

    def test_changelog_defines_each_link_reference_once(self) -> None:
        """A merge kept both sides' link blocks, so six versions had two definitions each.

        Markdown silently resolves the first and ignores the rest, so half the block was dead text
        pointing at a different organisation than the live one -- invisible in the rendered page.
        Deliberately not asserting heading order: `update_changelog` inserts above the first `## [`,
        so a test pinning `Unreleased` to the top would fail on the first automated release.
        """
        labels = re.findall(r"^\[([^\]]+)\]:", CHANGELOG.read_text(encoding="utf-8"), re.MULTILINE)
        duplicates = sorted({label for label in labels if labels.count(label) > 1})

        self.assertEqual(duplicates, [], f"CHANGELOG.md defines these link references twice: {duplicates}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
