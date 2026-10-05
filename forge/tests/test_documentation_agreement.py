#!/usr/bin/env python3
"""Documentation checked by the suite, because a command someone remembers to run is not a check.

Two classes of rot, and they are different problems that a single check keeps conflating:

  (a) A path a document names does not exist. Catches a move like the character extraction's, where
      four `grimoire/character/` pages left the repo and nine files still pointed at them.
  (b) A withdrawn IDENTIFIER survives in prose. `--profile animated-character` is not a path, so (a)
      cannot see it. The plan's one mechanical doc gate was scoped to paths and passed green while
      seven documents still taught the withdrawn profile -- including two carrying runnable
      commands that fail.

Python docstrings are in scope for (a) deliberately. `extract_landmarks.py:6` named a page that
moved while the module stayed, which is the whole class: the code is correct, the prose beside it is
not, and nothing that looks at code or at `docs/` alone can see it.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SEARCH_ROOTS = ("SKILL.md", "README.md", "ROADMAP.md", "CHANGELOG.md", "docs", "grimoire", "forge")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".cache", "openspec", "graphify-out", "dist"}

# LIVE GUIDANCE -- what a reader is expected to follow today. Only these are checked for dead paths.
#
# A historical record naming a file that no longer exists is CORRECT: `CHANGELOG.md` says v2.0
# deleted `forge/_shared/domains/animated_character.py`, and a dead-link rule applied there would
# demand the history be falsified. `docs/UPGRADE_PLAN.md` is the same shape, a record of what 1.5
# added. Measured before scoping: 36 dead paths repo-wide, all but a handful in exactly those two.
LIVE_GUIDANCE_ROOTS = ("SKILL.md", "README.md", "grimoire", "docs/standard-prompts", "forge")
HISTORICAL_RECORDS = {"CHANGELOG.md", "docs/UPGRADE_PLAN.md", "ROADMAP.md"}

# A repo-relative path only counts when its first segment is a directory this repo ships. Without
# that, prose like `family/subtype` and a plugin's `{plugin_dir}/...` rows read as dead links.
TOP_LEVEL = {p.name for p in ROOT.iterdir() if p.is_dir() and p.name not in SKIP_DIRS}

PATH_IN_PROSE = re.compile(r"`([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.{}-]+)+\.[A-Za-z0-9]{1,5})`")

WITHDRAWN_IDENTIFIER = "animated-character"

# Where the withdrawn identifier is still CORRECT. Each entry is a historical record: rewriting it
# would make the history wrong, which is a worse failure than the staleness this test prevents.
WITHDRAWN_ALLOWED = {
    # Records what v1.5.2 and v2.0 actually shipped. A changelog that edits its own past is useless.
    "CHANGELOG.md",
    # The withdrawal table itself, and its message, must name the identifier to answer for it.
    "forge/_shared/domains/__init__.py",
    # Tests that assert the withdrawal, and comments naming the OpenSpec change that introduced the
    # profile -- a change name is a permanent identifier, not a live profile reference.
    "forge/tests/test_domain_registry.py",
    "forge/tests/test_rig_workflow_steps.py",
    "forge/tests/test_run_gates.py",
    "forge/stage3_build/run_gates.py",
    "forge/tests/test_documentation_agreement.py",
    # Records its own rename in its header note.
    "docs/GLB_CHARACTER_RIG_PROMPT.md",
}


def _files(roots: tuple[str, ...] = SEARCH_ROOTS) -> list[Path]:
    out: list[Path] = []
    for entry in roots:
        target = ROOT / entry
        if target.is_file():
            out.append(target)
            continue
        for path in target.rglob("*"):
            if not path.is_file() or path.suffix not in {".md", ".py"}:
                continue
            if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
                continue
            out.append(path)
    return sorted(out)


class EveryPathNamedInProseExists(unittest.TestCase):
    def test_no_document_or_docstring_names_a_file_this_repo_does_not_have(self) -> None:
        dead: list[str] = []
        checked = 0
        for path in _files(LIVE_GUIDANCE_ROOTS):
            if path.name == Path(__file__).name:
                continue   # its own prose names deleted files as examples of the rule it states
            text = path.read_text(encoding="utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), 1):
                # A path ATTRIBUTED to another repo, or marked as gone, is not this one's to have.
                # The two rules in this file compose here: attribution exempts a line from the
                # dead-path check, and `test_the_moved_grimoire_pages_are_named_as_the_plugin_s`
                # is what REQUIRES the attribution -- so a moved page must be attributed and an
                # unmoved one must exist. The exemption is deliberately a whole phrase rather than
                # a bare marker: "since deleted" has to be written as a statement to the reader,
                # not as a pragma that silences the check.
                if any(token in line for token in ("plugin-", "{plugin_dir}", "since deleted")):
                    continue
                for match in PATH_IN_PROSE.findall(line):
                    if "{" in match or match.split("/", 1)[0] not in TOP_LEVEL:
                        continue
                    checked += 1
                    if not (ROOT / match).exists():
                        dead.append(f"{path.relative_to(ROOT)}:{line_number} -> {match}")
        self.assertGreater(checked, 100, "the path pattern matched almost nothing; it is broken")
        self.assertEqual(dead, [], "documents naming paths this repo does not have")

    def test_the_moved_grimoire_pages_are_named_as_the_plugin_s(self) -> None:
        """The pages left; a bare `grimoire/character/<page>.md` would now be a base-relative lie.

        `test_no_document_or_docstring_names_a_file_this_repo_does_not_have` above already fails on
        a backticked one. This catches the unbackticked prose form it cannot see, which is how the
        reference in `extract_landmarks.py`'s docstring was written.
        """
        moved = ("reconstruction", "likeness_maximization", "structure_decomposition",
                 "head_construction")
        offenders: list[str] = []
        for path in _files():
            relative = path.relative_to(ROOT).as_posix()
            # Test code and historical records NAME the pages as subjects rather than pointing a
            # reader at them -- `test_rig_workflow_steps` asserts they are ABSENT, which this rule
            # would otherwise read as the very rot it exists to catch.
            if (path.name == Path(__file__).name or relative in HISTORICAL_RECORDS
                    or relative.startswith("forge/tests/")):
                continue
            for line_number, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                for page in moved:
                    if f"grimoire/character/{page}.md" not in line:
                        continue
                    if "plugin-character" in line or "{plugin_dir}" in line:
                        continue
                    offenders.append(f"{path.relative_to(ROOT)}:{line_number}")
        self.assertEqual(sorted(set(offenders)), [],
                         "these name a moved page without saying whose it is")

    def test_the_hair_pages_are_still_named_base_relative(self) -> None:
        # The other half of the same partition, and the one a blanket rewrite would have broken:
        # the hair pages did NOT move, so their base-relative links must stay base-relative.
        for page in ("stylized_hair_threejs.md", "threejs_hair_parameter_contract.json"):
            self.assertTrue((ROOT / "grimoire" / "character" / page).is_file(), page)


class TheWithdrawnProfileIsNotStillTaught(unittest.TestCase):
    def test_only_historical_records_still_name_it(self) -> None:
        offenders: list[str] = []
        for path in _files():
            relative = path.relative_to(ROOT).as_posix()
            if relative in WITHDRAWN_ALLOWED:
                continue
            for line_number, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if WITHDRAWN_IDENTIFIER in line:
                    offenders.append(f"{relative}:{line_number}: {line.strip()[:90]}")
        self.assertEqual(offenders, [],
                         f"{WITHDRAWN_IDENTIFIER!r} is withdrawn; these still teach it")

    def test_no_runnable_example_invokes_the_withdrawn_profile(self) -> None:
        # The sharper half: two documents carried `--profile animated-character` in copy-paste
        # blocks. An allowlisted historical record must not contain one either, so this check is
        # NOT allowlisted -- a changelog may name the identifier, never hand out a broken command.
        pattern = re.compile(rf"--profile\s+{re.escape(WITHDRAWN_IDENTIFIER)}")
        offenders = [
            f"{path.relative_to(ROOT)}:{n}"
            for path in _files()
            if path.name != Path(__file__).name
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
            if pattern.search(line)
        ]
        self.assertEqual(offenders, [], "these hand out a command that now fails")

    def test_the_allowlist_is_not_carrying_dead_entries(self) -> None:
        # An allowlist nobody prunes eventually permits everything. Each entry must still contain
        # the identifier it was added for.
        stale = [name for name in sorted(WITHDRAWN_ALLOWED)
                 if WITHDRAWN_IDENTIFIER not in (ROOT / name).read_text(encoding="utf-8",
                                                                        errors="replace")]
        self.assertEqual(stale, [], "remove these from WITHDRAWN_ALLOWED; they no longer name it")


if __name__ == "__main__":
    unittest.main()


class TheAuthorityRulingCoversWhatTheMergeAdmits(unittest.TestCase):
    """`SECTION_AUTHORITY`'s plugin half is prose, and prose beside a constant drifts.

    Its BASE half cannot drift -- it is derived from `BASE_OWNED` by comprehension. Its PLUGIN half
    is hand-written, and until this class the only thing checking it lived in ANOTHER repo
    (plugin-character's `test_spec_augmentation_artifact.py`) and skipped whenever that repo could
    not see this one. A cleanup review called that out: a structure that looks like enforced code
    but is only cross-checked by an external, skippable test is exactly the shape that rots.

    So the subject here is the base's OWN frozen artifact. `fixtures/oracle-character/spec.json` is
    what this repo produced for a character before the extraction; every section in it that is not
    base-owned is a section a provider must now author, and therefore one the base has to have
    ruled on. It needs no plugin installed and no second checkout.
    """

    @classmethod
    def setUpClass(cls) -> None:
        sys.path[:0] = [str(ROOT / "forge"), str(ROOT / "forge" / "_shared")]
        from spec_augmentation import BASE_OWNED, SECTION_AUTHORITY  # noqa: PLC0415
        cls.authority, cls.base_owned = SECTION_AUTHORITY, BASE_OWNED
        cls.frozen = json.loads(
            (ROOT / "forge/tests/fixtures/oracle-character/spec.json").read_text(encoding="utf-8"))

    def test_every_ruled_section_is_a_real_section(self) -> None:
        """A ruling for a key no spec carries is a decision about nothing.

        This is the check that has a subject on THIS side. The first version of this test asserted
        the opposite -- that every non-base-owned section in the frozen spec has a ruling -- and it
        was wrong on a false premise: `BASE_OWNED` is a deny-list of what a plugin may not WRITE,
        not a list of what the base does not author. The frozen spec carries 24 sections
        (`actionReadiness`, `animationAnchors`, `performanceBudget`, …) that the base authors
        itself and no provider ever touches; demanding a ruling for each would have been demanding
        the base rule on questions nobody asked.
        """
        self.assertEqual(sorted(set(self.authority) - set(self.frozen) - set(self.base_owned)), [])

    def test_every_base_owned_key_is_ruled_base(self) -> None:
        for key in sorted(self.base_owned):
            self.assertTrue(self.authority[key].startswith("BASE."), key)

    def test_no_ruling_contradicts_the_deny_list(self) -> None:
        # A key ruled writable by a plugin while sitting in BASE_OWNED would be a ruling the merge
        # refuses to honour -- documentation that the code disagrees with, which is worse than none.
        contradictory = sorted(k for k, v in self.authority.items()
                               if k in self.base_owned and not v.startswith("BASE."))
        self.assertEqual(contradictory, [])

    def test_the_plugin_half_is_checked_from_the_provider_side_and_says_so(self) -> None:
        """What this repo CANNOT check, asserted as a pointer rather than left implicit.

        Whether the PLUGIN half is complete depends on what a provider contributes, which the base
        does not know and must not assume -- that is the deny-list design. The check that a
        provider's sections are all ruled on lives in the provider
        (plugin-character `tests/test_spec_augmentation_artifact.py::RuledOnByTheBase`). This
        asserts the ruling still tells the next reader where that check is, so the split is a
        documented division of labour rather than a gap either side assumes the other covers.
        """
        source = (ROOT / "forge/_shared/spec_augmentation.py").read_text(encoding="utf-8")
        self.assertIn("NOT AN ALLOW-LIST", source.upper())
        self.assertIn("test_spec_augmentation_artifact.py", source)
        plugin_ruled = {k for k, v in self.authority.items() if v.startswith("PLUGIN")}
        self.assertEqual(plugin_ruled & set(self.base_owned), set())
        self.assertTrue(plugin_ruled, "the ruling has no plugin half left to check")
