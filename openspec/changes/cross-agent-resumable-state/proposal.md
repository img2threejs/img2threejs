# Cross-Agent Resumable Reconstruction State

## Why

The reconstruction pipeline currently stores progress across separate manifests, specs, review
history, agent transcripts, and implicit agent memory. When work moves between Claude Code,
Codex, OpenCode, or another compatible agent, the next agent cannot reliably determine the
authoritative stage, completed evidence, active blocker, or safe next action. This causes loops
to stop early, repeat completed work, or continue past an intake/review gate.

The project needs a repository-local, vendor-neutral checkpoint and handoff contract that can
be read and updated by every supported agent, survives process interruption, and remains
auditable in Git.

## What Changes

- Add a versioned repository-local state contract keyed by a stable item ID such as
  `2026-05-01-abx`, containing the current workflow state, artifact references, evidence ledger,
  blocker, next action, and resumability metadata.
- Use a hybrid persistence model: an atomic latest snapshot for fast resume plus an append-only
  event ledger for audit and recovery.
- Define a portable agent identity and handoff record independent of Claude, Codex, OpenCode, or
  any provider-specific transcript format. Agents are actors on an item, not state namespaces.
- Enforce optimistic concurrency with a revision/compare-and-swap check and a short-lived lease
  for active writers; stale leases become recoverable rather than silently overwritten.
- Make every state transition explicit and gate downstream commands on the persisted state,
  especially the mandatory `item-reconstruction-intake` result.
- Define idempotent resume behavior: completed evidence-producing actions are not rerun unless
  explicitly invalidated, and interrupted actions are resumed or marked unknown.
- Keep secrets, credentials, raw transcripts, and PII out of the shared state by default; store
  redacted references and hashes instead.
- Add migration/versioning rules, validation commands, recovery guidance, and cross-agent
  contract tests.
- Treat OpenSpec completion as design readiness only; executable enforcement is complete only
  after `forge/state.py`, shared writer integration, and black-box rejection tests exist.

## Capabilities

### New Capabilities

- `cross-agent-resumable-state`: Portable checkpoint, handoff, concurrency, resume, validation,
  and recovery behavior for multi-agent reconstruction work.
- `cross-agent-pipeline-integration`: State-aware preflight and transition recording across
  intake, assessment, spec, build, review, and agent-facing instructions.
- `executable-state-gates`: Command-level preflight, resume acknowledgement, and NotebookLM
  review-receipt enforcement that cannot be bypassed by forgetting or ignoring skill text.

### Modified Capabilities

- None. The repository has no base capability specs under `openspec/specs/`; integration changes
  are therefore specified as the second new capability. Existing intake, spec, build, and review
  artifacts remain authoritative for their own domain data; the new state contract records their
  linkage and workflow position.

## Impact

- New state schema and validation/persistence code under `forge/`.
- New repository-local item state artifacts under `.agent-state/items/<itemId>/`.
- `SKILL.md` and agent-facing workflow instructions gain a mandatory state read/write and
  handoff protocol.
- `forge/next.py`, intake, pass orchestration, and review commands gain state-aware preflight
  and transition recording without duplicating their existing domain schemas.
- New tests cover atomic writes, schema validation, stale-agent recovery, concurrent update
  rejection, interrupted-action resume, and interoperability using fixtures representing Claude,
  Codex, and OpenCode clients.
- All persistence-capable commands must receive or resolve an unambiguous `--item-id`; read-only
  commands may remain ungated only when they cannot write through an output flag.
- NotebookLM research notebook: `140cddad-1e06-40c6-aa7b-eefca053e455`; research task:
  `1007493b-d1b8-4ae4-ad14-1d2626ae4a98`. Research was used as input to the design and will be
  preserved with source URLs and confidence notes in the change artifacts.
