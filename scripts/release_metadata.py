#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Final


VERSION_PATTERN: Final = re.compile(r"^version: (\d+)\.(\d+)\.(\d+)$", re.MULTILINE)
BADGE_PATTERN: Final = re.compile(r"version-\d+\.\d+\.\d+-green\.svg")
HEADING_PATTERN: Final = re.compile(r"^## \[", re.MULTILINE)
REFERENCE_PATTERN: Final = re.compile(r"^\[", re.MULTILINE)
BREAKING_PATTERN: Final = re.compile(r"^[a-z]+(?:\([^\n]+\))?!:", re.MULTILINE)


class ReleaseLevel(Enum):
    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"


@dataclass(frozen=True, slots=True)
class Version:
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def bump(self, level: ReleaseLevel) -> "Version":
        match level:
            case ReleaseLevel.PATCH:
                return Version(self.major, self.minor, self.patch + 1)
            case ReleaseLevel.MINOR:
                return Version(self.major, self.minor + 1, 0)
            case ReleaseLevel.MAJOR:
                return Version(self.major + 1, 0, 0)
            case unreachable:
                raise AssertionError(f"unhandled release level: {unreachable}")


@dataclass(frozen=True, slots=True)
class ReleasePlan:
    current: Version
    next: Version
    level: ReleaseLevel
    commits: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReleaseRequest:
    root: Path
    commits: tuple[str, ...]
    release_date: str
    repository_url: str
    dry_run: bool


def release_level(commits: tuple[str, ...]) -> ReleaseLevel | None:
    if any("BREAKING CHANGE:" in commit or BREAKING_PATTERN.search(commit) for commit in commits):
        return ReleaseLevel.MAJOR
    if any(commit.lstrip().startswith("feat") for commit in commits):
        return ReleaseLevel.MINOR
    if any(commit.lstrip().startswith("fix") for commit in commits):
        return ReleaseLevel.PATCH
    return None


def parse_version(skill_text: str) -> Version:
    match = VERSION_PATTERN.search(skill_text)
    if match is None:
        raise ValueError("SKILL.md must contain one semantic version front-matter field")
    return Version(*(int(value) for value in match.groups()))


def parse_commits(commits_text: str) -> tuple[str, ...]:
    return tuple(commit.strip() for commit in commits_text.split("\x1e") if commit.strip())


def change_sections(commits: tuple[str, ...]) -> str:
    features = [commit.splitlines()[0].removeprefix("feat: ") for commit in commits if commit.lstrip().startswith("feat")]
    fixes = [commit.splitlines()[0].removeprefix("fix: ") for commit in commits if commit.lstrip().startswith("fix")]
    breaking = [commit.splitlines()[0] for commit in commits if "BREAKING CHANGE:" in commit or BREAKING_PATTERN.search(commit)]
    sections: list[str] = []
    if breaking:
        sections.extend(["### Breaking Changes", *(f"- {item}" for item in breaking), ""])
    if features:
        sections.extend(["### Added", *(f"- {item}" for item in features), ""])
    if fixes:
        sections.extend(["### Fixed", *(f"- {item}" for item in fixes), ""])
    return "\n".join(sections).rstrip()


def replace_once(pattern: re.Pattern[str], text: str, replacement: str, description: str) -> str:
    updated, replacements = pattern.subn(replacement, text, count=1)
    if replacements != 1:
        raise ValueError(f"{description} must contain exactly one replaceable version")
    return updated


def update_changelog(changelog: str, plan: ReleasePlan, release_date: str, repository_url: str) -> str:
    heading = f"## [{plan.next}] — {release_date}\n\n{change_sections(plan.commits)}\n\n"
    heading_match = HEADING_PATTERN.search(changelog)
    if heading_match is None:
        raise ValueError("CHANGELOG.md must contain a version heading")
    with_heading = changelog[:heading_match.start()] + heading + changelog[heading_match.start():]
    reference_match = REFERENCE_PATTERN.search(with_heading)
    if reference_match is None:
        raise ValueError("CHANGELOG.md must contain version reference links")
    reference = f"[{plan.next}]: {repository_url}/compare/v{plan.current}...v{plan.next}\n"
    return with_heading[:reference_match.start()] + reference + with_heading[reference_match.start():]


def apply_release(request: ReleaseRequest) -> ReleasePlan | None:
    level = release_level(request.commits)
    if level is None:
        return None
    skill_path = request.root / "SKILL.md"
    readme_path = request.root / "README.md"
    changelog_path = request.root / "CHANGELOG.md"
    skill_text = skill_path.read_text(encoding="utf-8")
    current = parse_version(skill_text)
    plan = ReleasePlan(current=current, next=current.bump(level), level=level, commits=request.commits)
    updated_skill = replace_once(VERSION_PATTERN, skill_text, f"version: {plan.next}", "SKILL.md")
    readme_text = readme_path.read_text(encoding="utf-8")
    updated_readme = replace_once(BADGE_PATTERN, readme_text, f"version-{plan.next}-green.svg", "README.md")
    changelog_text = changelog_path.read_text(encoding="utf-8")
    updated_changelog = update_changelog(changelog_text, plan, request.release_date, request.repository_url)
    if request.dry_run:
        return plan
    skill_path.write_text(updated_skill, encoding="utf-8")
    readme_path.write_text(updated_readme, encoding="utf-8")
    changelog_path.write_text(updated_changelog, encoding="utf-8")
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--commits-file", type=Path, required=True)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--repository-url", required=True)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    commits = parse_commits(arguments.commits_file.read_text(encoding="utf-8"))
    request = ReleaseRequest(arguments.root, commits, arguments.date, arguments.repository_url, arguments.dry_run)
    plan = apply_release(request)
    if plan is None:
        print(json.dumps({"release": False}))
        return 0
    print(json.dumps({"release": True, "version": str(plan.next), "level": plan.level.value}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
