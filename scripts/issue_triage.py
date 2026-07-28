#!/usr/bin/env python3
"""Queue newly opened, unlabeled GitHub issues for maintainer triage."""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Final, Protocol


API_ROOT: Final = "https://api.github.com"
NOTICE_MARKER: Final = "<!-- img2threejs-triage-notice:v1 -->"
QUEUE_LABEL: Final = "triage: needs-review"
TRIAGE_BOT_LOGIN: Final = "github-actions[bot]"
NOTICE: Final = (
    f"{NOTICE_MARKER}\n"
    "Thanks for opening this issue. It is in the maintainer triage queue. "
    "Please add any missing reproduction steps, expected outcome, or scope here; "
    "a label is not a promise of implementation."
)
TRANSIENT_STATUSES: Final = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class Issue:
    number: int
    is_open: bool
    is_pull_request: bool
    labels: tuple[str, ...]
    created_at: str = ""


@dataclass(frozen=True, slots=True)
class TriageOptions:
    dry_run: bool
    rollout_after: str | None = None


@dataclass(frozen=True, slots=True)
class TriageResult:
    noticed: tuple[int, ...]
    reconciled: tuple[int, ...]
    would_notice: tuple[int, ...]
    skipped: tuple[int, ...]
    failed: tuple[int, ...]


class IssueApi(Protocol):
    def queue_label_exists(self) -> bool: ...

    def list_open_issues(self) -> tuple[Issue, ...]: ...

    def get_issue(self, number: int) -> Issue | None: ...

    def has_notice_marker(self, number: int) -> bool: ...

    def add_label(self, number: int, label: str) -> None: ...

    def create_notice(self, number: int, notice: str) -> None: ...


class MissingQueueLabelError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(f"Required label is missing: {QUEUE_LABEL}")


class GitHubIssueApi:
    def __init__(self, repository: str, token: str) -> None:
        self._repository = repository
        self._token = token

    def list_open_issues(self) -> tuple[Issue, ...]:
        issues: list[Issue] = []
        page = 1
        while True:
            payload = self._request(f"/repos/{self._repository}/issues?state=open&per_page=100&page={page}")
            if not payload:
                return tuple(issues)
            issues.extend(parse_issue(item) for item in payload)
            page += 1

    def queue_label_exists(self) -> bool:
        label = urllib.parse.quote(QUEUE_LABEL, safe="")
        try:
            self._request(f"/repos/{self._repository}/labels/{label}")
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return False
            raise
        return True

    def get_issue(self, number: int) -> Issue | None:
        try:
            return parse_issue(self._request(f"/repos/{self._repository}/issues/{number}"))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise

    def has_notice_marker(self, number: int) -> bool:
        page = 1
        while True:
            comments = self._request(f"/repos/{self._repository}/issues/{number}/comments?per_page=100&page={page}")
            if any(
                NOTICE_MARKER in comment.get("body", "")
                and comment.get("user", {}).get("login") == TRIAGE_BOT_LOGIN
                and comment.get("user", {}).get("type") == "Bot"
                for comment in comments
            ):
                return True
            if not comments:
                return False
            page += 1

    def add_label(self, number: int, label: str) -> None:
        self._request(f"/repos/{self._repository}/issues/{number}/labels", {"labels": [label]})

    def create_notice(self, number: int, notice: str) -> None:
        path = f"/repos/{self._repository}/issues/{number}/comments"
        try:
            self._request(path, {"body": notice}, retry=False)
        except (urllib.error.HTTPError, urllib.error.URLError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code not in TRANSIENT_STATUSES:
                raise
            if self.has_notice_marker(number):
                return
            self._request(path, {"body": notice}, retry=False)

    def _request(
        self,
        path: str,
        payload: dict[str, list[str]] | dict[str, str] | None = None,
        retry: bool = True,
    ):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{API_ROOT}{path}",
            data=data,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        for attempt in range(3 if retry else 1):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                if error.code not in TRANSIENT_STATUSES or attempt == (2 if retry else 0):
                    raise
            except urllib.error.URLError:
                if attempt == (2 if retry else 0):
                    raise
            time.sleep(2**attempt)
        raise AssertionError("unreachable retry loop")


def parse_issue(payload) -> Issue:
    return Issue(
        number=payload["number"],
        is_open=payload["state"] == "open",
        is_pull_request="pull_request" in payload,
        labels=tuple(label["name"] for label in payload["labels"]),
        created_at=payload["created_at"],
    )


def is_rollout_candidate(issue: Issue, options: TriageOptions) -> bool:
    return options.rollout_after is None or issue.created_at >= options.rollout_after


def run_triage(api: IssueApi, options: TriageOptions) -> TriageResult:
    noticed: list[int] = []
    reconciled: list[int] = []
    would_notice: list[int] = []
    skipped: list[int] = []
    failed: list[int] = []
    if not api.queue_label_exists():
        raise MissingQueueLabelError
    for listed in api.list_open_issues():
        if listed.is_pull_request or not listed.is_open:
            skipped.append(listed.number)
            continue
        try:
            current = api.get_issue(listed.number)
            if current is None or current.is_pull_request or not current.is_open:
                skipped.append(listed.number)
                continue
            is_recovery = current.labels == (QUEUE_LABEL,)
            if not is_recovery and (current.labels or not is_rollout_candidate(current, options)):
                skipped.append(current.number)
                continue
            if api.has_notice_marker(current.number):
                skipped.append(current.number)
                continue
            if options.dry_run:
                would_notice.append(current.number)
                continue
            if is_recovery:
                api.create_notice(current.number, NOTICE)
                reconciled.append(current.number)
            else:
                api.add_label(current.number, QUEUE_LABEL)
                api.create_notice(current.number, NOTICE)
                noticed.append(current.number)
        except (urllib.error.HTTPError, urllib.error.URLError):
            failed.append(listed.number)
    return TriageResult(tuple(noticed), tuple(reconciled), tuple(would_notice), tuple(skipped), tuple(failed))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rollout-after")
    arguments = parser.parse_args()
    if not arguments.repository or not arguments.token:
        parser.error("--repository and --token (or GITHUB_REPOSITORY and GITHUB_TOKEN) are required")
    result = run_triage(
        GitHubIssueApi(arguments.repository, arguments.token),
        TriageOptions(dry_run=arguments.dry_run, rollout_after=arguments.rollout_after),
    )
    print(json.dumps(asdict(result), sort_keys=True))
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
