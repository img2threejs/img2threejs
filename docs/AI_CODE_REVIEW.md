# AI repository-aware pull request review

This repository contains an advisory GitHub Actions review workflow. It runs
on GitHub-hosted `ubuntu-latest`, checks out the trusted base commit, indexes
the repository locally, and reads PR files through the GitHub API as untrusted
data (including from a fork's head repository). It does not clone or execute the
PR branch and does not require a VPS.

## GitHub configuration

In **Settings → Secrets and variables → Actions**:

1. Add the secret `AI_REVIEW_API_KEY`.
2. Add these repository variables:

   - `AI_REVIEW_BASE_URL`: proxy base URL, for example `https://review.example.com/v1`.
   - `AI_REVIEW_MODEL`: model name accepted by the proxy.

The workflow calls `AI_REVIEW_BASE_URL + AI_REVIEW_CHAT_PATH`. The default path
is `/chat/completions`, so the default request target is:

```text
https://review.example.com/v1/chat/completions
```

Use `AI_REVIEW_ENDPOINT` instead when the proxy needs a complete URL; it takes
precedence over `AI_REVIEW_BASE_URL`.

Optional variables:

- `AI_REVIEW_CHAT_PATH` — alternate path, such as `/api/chat/completions`.
- `AI_REVIEW_API_KEY_HEADER` — defaults to `Authorization`; set `x-api-key` for a proxy that uses that header.
- `AI_REVIEW_API_KEY_PREFIX` — defaults to `Bearer`; leave empty when the proxy expects the raw key.
- `AI_REVIEW_MAX_TOKENS` — defaults to `3500`.
- `AI_REVIEW_TIMEOUT` — request timeout in seconds; defaults to `90`.

The proxy must accept an OpenAI-compatible chat-completions JSON request and
return model text in one of these common response shapes: `choices[0].message.content`,
`output_text`, `content[].text`, or `text`. The model text must be JSON with
`summary` and `findings` fields as described in the workflow script.

## Behavior

- The first run is skipped safely if `AI_REVIEW_API_KEY` is absent.
- A single bot summary comment is created or updated on the PR.
- Findings are advisory and do not block merging.
- A result artifact is retained for seven days.
- The PR branch is never executed in this privileged `pull_request_target` job.
- PR-provided instructions are treated as untrusted content; only policy files
  from the checked-out base commit are used as review policy.

To test it, create a small PR after configuring the secret and variables, then
open the workflow run and inspect both the step summary and the updated PR
comment. Never put the API key in a repository variable or in the workflow YAML.
