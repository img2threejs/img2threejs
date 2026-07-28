from __future__ import annotations

import unittest
import urllib.error
from unittest.mock import patch

from scripts.issue_triage import (
    MissingQueueLabelError,
    NOTICE_MARKER,
    TRIAGE_BOT_LOGIN,
    GitHubIssueApi,
    QUEUE_LABEL,
    Issue,
    TriageOptions,
    run_triage,
)


class FakeIssueApi:
    def __init__(self, issues: dict[int, Issue], markers: set[int] | None = None) -> None:
        self.issues = issues
        self.markers = set() if markers is None else markers
        self.operations: list[tuple[str, int]] = []

    def list_open_issues(self) -> tuple[Issue, ...]:
        return tuple(self.issues.values())

    def queue_label_exists(self) -> bool:
        return True

    def get_issue(self, number: int) -> Issue | None:
        return self.issues.get(number)

    def has_notice_marker(self, number: int) -> bool:
        return number in self.markers

    def add_label(self, number: int, label: str) -> None:
        self.operations.append((f"label:{label}", number))
        issue = self.issues[number]
        self.issues[number] = Issue(issue.number, issue.is_open, issue.is_pull_request, issue.labels + (label,))

    def create_notice(self, number: int, notice: str) -> None:
        self.operations.append(("comment", number))
        if NOTICE_MARKER in notice:
            self.markers.add(number)


class MarkerFailingApi(FakeIssueApi):
    def has_notice_marker(self, number: int) -> bool:
        raise urllib.error.URLError("temporary GitHub failure")


class NoticeFailingApi(FakeIssueApi):
    def create_notice(self, number: int, notice: str) -> None:
        self.operations.append(("comment", number))
        raise urllib.error.URLError("temporary GitHub failure")


class MissingQueueLabelApi(FakeIssueApi):
    def queue_label_exists(self) -> bool:
        return False


class FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exception_type, exception, traceback) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


class IssueTriageTests(unittest.TestCase):
    def test_notices_unlabeled_open_issue_once(self) -> None:
        # Given: one unlabeled open issue.
        api = FakeIssueApi({7: Issue(7, True, False, ())})

        # When: the scheduled triage runs.
        result = run_triage(api, TriageOptions(dry_run=False))

        # Then: the queue label and one notice are created.
        self.assertEqual(result.noticed, (7,))
        self.assertEqual(api.operations, [(f"label:{QUEUE_LABEL}", 7), ("comment", 7)])

    def test_recovers_labelled_issue_when_notice_is_missing(self) -> None:
        # Given: a previous run added the queue label but did not create its notice.
        api = FakeIssueApi({8: Issue(8, True, False, (QUEUE_LABEL,))})

        # When: triage runs again.
        result = run_triage(api, TriageOptions(dry_run=False))

        # Then: it creates only the missing notice.
        self.assertEqual(result.reconciled, (8,))
        self.assertEqual(api.operations, [("comment", 8)])

    def test_skips_pull_requests_and_type_labelled_issues(self) -> None:
        # Given: a pull request and an issue already classified by its form.
        api = FakeIssueApi(
            {
                9: Issue(9, True, True, ()),
                10: Issue(10, True, False, ("bug",)),
            }
        )

        # When: triage runs.
        result = run_triage(api, TriageOptions(dry_run=False))

        # Then: neither record is mutated.
        self.assertEqual(result.noticed, ())
        self.assertEqual(api.operations, [])

    def test_skips_human_labelled_queue_issue_without_notice(self) -> None:
        # Given: a human has classified an issue after a previous partial bot write.
        api = FakeIssueApi({10: Issue(10, True, False, (QUEUE_LABEL, "bug"))})

        # When: triage runs again.
        result = run_triage(api, TriageOptions(dry_run=False))

        # Then: it leaves human-classified issues for maintainer discussion.
        self.assertEqual(result.skipped, (10,))
        self.assertEqual(api.operations, [])

    def test_dry_run_reports_candidates_without_mutation(self) -> None:
        # Given: an unlabeled open issue.
        api = FakeIssueApi({11: Issue(11, True, False, ())})

        # When: a maintainer requests dry-run mode.
        result = run_triage(api, TriageOptions(dry_run=True))

        # Then: the candidate is reported but no issue write occurs.
        self.assertEqual(result.would_notice, (11,))
        self.assertEqual(api.operations, [])

    def test_marker_prevents_duplicate_notice(self) -> None:
        # Given: an issue already has the signed notice marker.
        api = FakeIssueApi({12: Issue(12, True, False, (QUEUE_LABEL,))}, markers={12})

        # When: triage runs again.
        result = run_triage(api, TriageOptions(dry_run=False))

        # Then: it leaves the issue unchanged.
        self.assertEqual(result.skipped, (12,))
        self.assertEqual(api.operations, [])

    def test_ignores_notice_marker_from_untrusted_commenter(self) -> None:
        # Given: a user copies the marker into an issue comment.
        api = GitHubIssueApi("img2threejs/img2threejs", "token")
        comments = FakeResponse(
            '[{"body":"' + NOTICE_MARKER + '","user":{"login":"someone","type":"User"}}]'
        )

        # When: the helper checks whether it previously wrote the notice.
        with patch("scripts.issue_triage.urllib.request.urlopen", side_effect=[comments, FakeResponse("[]")]):
            marked = api.has_notice_marker(12)

        # Then: an untrusted commenter cannot suppress triage.
        self.assertFalse(marked)

    def test_trusted_bot_marker_prevents_duplicate_notice(self) -> None:
        # Given: GitHub Actions previously wrote the marker.
        api = GitHubIssueApi("img2threejs/img2threejs", "token")
        comments = FakeResponse(
            '[{"body":"' + NOTICE_MARKER + '","user":{"login":"' + TRIAGE_BOT_LOGIN + '","type":"Bot"}}]'
        )

        # When: the helper checks the issue comments.
        with patch("scripts.issue_triage.urllib.request.urlopen", return_value=comments):
            marked = api.has_notice_marker(12)

        # Then: it recognizes only the bot's own marker.
        self.assertTrue(marked)

    def test_rechecks_for_notice_before_retrying_ambiguous_comment_write(self) -> None:
        # Given: a comment POST gets a transient response after GitHub accepted it.
        transient = urllib.error.HTTPError("https://api.github.test", 503, "unavailable", None, None)
        api = GitHubIssueApi("img2threejs/img2threejs", "token")
        comments = FakeResponse(
            '[{"body":"' + NOTICE_MARKER + '","user":{"login":"' + TRIAGE_BOT_LOGIN + '","type":"Bot"}}]'
        )

        # When: the helper handles the ambiguous write result.
        with patch("scripts.issue_triage.urllib.request.urlopen", side_effect=[transient, comments]) as request:
            api.create_notice(12, NOTICE_MARKER)
        transient.close()

        # Then: it reads the marker instead of repeating the POST.
        self.assertEqual(request.call_count, 2)
        self.assertIn("/issues/12/comments?", request.call_args_list[1].args[0].full_url)

    def test_reads_every_page_of_issue_comments_for_a_trusted_marker(self) -> None:
        # Given: the trusted notice is beyond the first GitHub comments page.
        api = GitHubIssueApi("img2threejs/img2threejs", "token")
        first_page = FakeResponse('[{"body":"discussion","user":{"login":"someone","type":"User"}}]')
        second_page = FakeResponse(
            '[{"body":"' + NOTICE_MARKER + '","user":{"login":"' + TRIAGE_BOT_LOGIN + '","type":"Bot"}}]'
        )

        # When: it searches the issue comments.
        with patch("scripts.issue_triage.urllib.request.urlopen", side_effect=[first_page, second_page]) as request:
            marked = api.has_notice_marker(12)

        # Then: the later trusted marker is found.
        self.assertTrue(marked)
        self.assertIn("page=2", request.call_args_list[1].args[0].full_url)

    def test_reads_every_page_of_open_issues(self) -> None:
        # Given: GitHub has more than one page of open records.
        api = GitHubIssueApi("img2threejs/img2threejs", "token")
        first_page = FakeResponse('[{"number":18,"state":"open","labels":[],"created_at":"2026-07-28T00:00:00Z"}]')
        second_page = FakeResponse('[{"number":19,"state":"open","labels":[],"created_at":"2026-07-28T00:00:00Z"}]')

        # When: it lists open issues.
        with patch(
            "scripts.issue_triage.urllib.request.urlopen",
            side_effect=[first_page, second_page, FakeResponse("[]")],
        ):
            issues = api.list_open_issues()

        # Then: both pages are included for triage filtering.
        self.assertEqual(tuple(issue.number for issue in issues), (18, 19))

    def test_isolates_marker_lookup_failure(self) -> None:
        # Given: a GitHub request fails for one candidate.
        api = MarkerFailingApi({13: Issue(13, True, False, ())})

        # When: triage processes the issue.
        result = run_triage(api, TriageOptions(dry_run=False))

        # Then: it reports the issue as failed without mutation.
        self.assertEqual(result.failed, (13,))
        self.assertEqual(api.operations, [])

    def test_scheduled_rollout_excludes_historical_issue(self) -> None:
        # Given: an unlabeled issue created before the rollout cutoff.
        api = FakeIssueApi({14: Issue(14, True, False, (), "2026-07-27T00:00:00Z")})

        # When: the scheduled mode uses the rollout cutoff.
        result = run_triage(api, TriageOptions(dry_run=False, rollout_after="2026-07-28T00:00:00Z"))

        # Then: the historical issue is not changed until a maintainer backfill.
        self.assertEqual(result.skipped, (14,))
        self.assertEqual(api.operations, [])

    def test_partial_write_is_failed_and_recoverable(self) -> None:
        # Given: adding the queue label succeeds but creating the notice fails.
        api = NoticeFailingApi({15: Issue(15, True, False, ())})

        # When: triage attempts the candidate.
        result = run_triage(api, TriageOptions(dry_run=False))

        # Then: it reports failure instead of a successful notice.
        self.assertEqual(result.noticed, ())
        self.assertEqual(result.failed, (15,))
        self.assertEqual(api.operations, [(f"label:{QUEUE_LABEL}", 15), ("comment", 15)])

    def test_missing_queue_label_fails_before_issue_mutation(self) -> None:
        # Given: the required queue label has not been provisioned.
        api = MissingQueueLabelApi({16: Issue(16, True, False, ())})

        # When: triage starts.
        with self.assertRaises(MissingQueueLabelError):
            run_triage(api, TriageOptions(dry_run=False))

        # Then: the candidate remains untouched.
        self.assertEqual(api.operations, [])

    def test_non_transient_github_errors_are_not_retried(self) -> None:
        # Given: GitHub rejects the request with an authorization or validation error.
        for status in (401, 403, 422):
            with self.subTest(status=status):
                error = urllib.error.HTTPError("https://api.github.test", status, "failure", None, None)
                api = GitHubIssueApi("img2threejs/img2threejs", "token")

                # When: the helper validates the queue label.
                with patch("scripts.issue_triage.urllib.request.urlopen", side_effect=error) as request:
                    with self.assertRaises(urllib.error.HTTPError):
                        api.queue_label_exists()
                error.close()

                # Then: it makes one request and leaves retry to no non-transient path.
                self.assertEqual(request.call_count, 1)

    def test_rate_limit_is_retried_before_success(self) -> None:
        # Given: GitHub returns a transient rate limit once, then the issue response.
        rate_limit = urllib.error.HTTPError("https://api.github.test", 429, "rate limit", None, None)
        response = FakeResponse('{"number":17,"state":"open","labels":[],"created_at":"2026-07-28T00:00:00Z"}')
        api = GitHubIssueApi("img2threejs/img2threejs", "token")

        # When: the helper fetches the issue.
        with patch("scripts.issue_triage.urllib.request.urlopen", side_effect=[rate_limit, response]):
            with patch("scripts.issue_triage.time.sleep") as sleep:
                issue = api.get_issue(17)
        rate_limit.close()

        # Then: it retries and returns the parsed issue.
        self.assertEqual(issue, Issue(17, True, False, (), "2026-07-28T00:00:00Z"))
        sleep.assert_called_once_with(1)

    def test_empty_issue_list_has_an_empty_summary(self) -> None:
        # Given: no open issues returned by GitHub.
        api = FakeIssueApi({})

        # When: triage runs.
        result = run_triage(api, TriageOptions(dry_run=False))

        # Then: every result bucket is empty.
        self.assertEqual(result.noticed, ())
        self.assertEqual(result.reconciled, ())
        self.assertEqual(result.would_notice, ())
        self.assertEqual(result.skipped, ())
        self.assertEqual(result.failed, ())


if __name__ == "__main__":
    unittest.main()
