# Issue triage

The triage workflow starts daily at 09:17 UTC and runs its helper on every second UTC epoch day, so
the cadence stays continuous across month boundaries. It is best effort, not an exact 48-hour
service level. A maintainer can use **Run workflow** in dry-run mode to see every zero-label
candidate, then use backfill only after reviewing that result. Scheduled runs consider only issues
created after `TRIAGE_ROLLOUT_AFTER` in `.github/workflows/issue-triage.yml`.

## Labels

| Axis | Labels | Owner |
| --- | --- | --- |
| Queue | `triage: needs-review`, `triage: discussed` | Bot adds only `needs-review`; maintainers transition it after discussion. |
| Priority | `priority: high`, `priority: medium`, `priority: low` | Maintainer, after a decision comment. |
| Contribution | `contribution: proposed`, `contribution: available`, `contribution: claimed` | The form adds `proposed`; maintainers control target-issue availability and claims. |

Existing labels such as `bug`, `enhancement`, and `documentation` remain independent. The bot never
removes labels, closes issues, locks them, assigns people, or applies terminal labels.

The scheduled candidate set is open non-PR issues with zero labels. The only exception is recovery
of an issue carrying exactly `triage: needs-review` when a prior bot run added that label but failed
before it could create the notice. A bot notice marker is accepted only from `github-actions[bot]`.

## Maintainer decision record

Before changing priority, contributor state, or closure, comment with the decision, rationale, next
action, and a revisit trigger if the issue becomes low priority. Low-priority issues remain open.

Implementation PRs use `Refs #<number>`. After merge, a maintainer records the merged PR,
verification, and closure rationale in the issue, then closes manually when appropriate.
