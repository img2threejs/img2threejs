## Problem

When reconstruction work moves between Claude Code, Codex, OpenCode, or another coding agent,
progress currently lives in several domain files plus private agent memory and transcripts. The
next agent cannot reliably know:

- which reconstruction run it is resuming;
- which intake gates and evidence have passed;
- which artifacts are authoritative and whether they changed;
- which operation was interrupted or already completed;
- who currently owns the work and whether that owner is stale;
- the exact next safe action or blocker.

As a result, agents can repeat expensive work, resume from the wrong stage, overwrite another
agent's progress, or continue before the mandatory `item-reconstruction-intake` gate is complete.

## Goal

Create a repository-local, vendor-neutral state and handoff contract that lets any compatible
agent resume the same reconstruction safely without access to another provider's transcript.
The state must be portable JSON/JSONL, auditable in Git, recoverable after interruption, and
strictly separated from domain truth.

## Proposed architecture

Use an item-scoped hybrid store. The item ID is the coordination key, not the agent or worktree:

```text
.agent-state/
└── items/<itemId>/
    ├── snapshot.json       # latest materialized resume view
    ├── events.jsonl        # append-only workflow transition/recovery ledger
    ├── lease.json          # volatile lease/fencing metadata; not committed
    └── research/           # redacted, content-addressed research records
```

Example item ID: `2026-05-01-abx`.

The ID uses the item creation date plus an item-name slug. If the same item needs another
attempt, use an explicit attempt field or suffix such as `2026-05-01-abx-attempt-02`; never
silently overwrite the prior attempt.

### Snapshot responsibility

`snapshot.json` stores orchestration metadata only:

- `schemaVersion`, immutable `itemId`, revision, and ledger watermark;
- target/source hashes and repository, branch, worktree, and commit provenance;
- current workflow phase and exact next safe action;
- blockers and recovery instructions;
- intake predicates:
  - `intakeAccepted`;
  - `genericHandoffReady`;
  - `specializedAdapterEligible`;
- references to authoritative domain artifacts, including path, role, SHA-256, schema version,
  and observed revision;
- current operation and handoff summaries.

The state file must not duplicate `cs2-intake.json`, assessment/spec JSON, sculpt pipeline state,
review history, raw transcripts, arbitrary tool output, credentials, or provider-specific session
data.

### Event ledger responsibility

`events.jsonl` records only durable orchestration events:

- initialization and state transitions;
- claims, handoffs, acknowledgements, takeovers, and recovery;
- operation lifecycle: `planned → started → completed | failed | unknown`;
- artifact invalidation and reconciliation.

Each event includes a unique event ID, monotonic sequence, run ID, actor/process identity,
operation ID when relevant, base/result revision, timestamp, fencing epoch when relevant, and
allow-listed artifact references. The ledger is the authoritative history of item orchestration
transitions, but existing domain artifacts remain authoritative for domain content.

## Concurrency and recovery contract

- Every write uses revision compare-and-swap.
- A writer acquires an atomic lease with a unique token and fencing epoch.
- Commit requires both the expected revision and current fencing token.
- Lease takeover advances the fencing epoch and records a durable takeover event.
- A late writer is rejected even if it believes its old lease is still valid.
- The event is durably appended before the snapshot is atomically replaced.
- If the ledger is ahead after a crash, valid events after the snapshot watermark are replayed.
- If the snapshot claims an event missing from the ledger, recovery blocks and quarantines it.
- Truncated final JSONL records are quarantined; malformed middle records block recovery.
- Unknown external side effects are never retried automatically; they require revalidation or
  explicit recovery.

## Portable command contract

Implement a Python stdlib reference CLI at `forge/state.py`:

```text
init       create an item state
list       list active/incomplete item IDs and their resumable status
read       print current state
validate   validate schema, hashes, ledger, and gates
claim      acquire lease/fencing ownership
heartbeat  renew lease
commit     append transition and update snapshot with CAS
handoff    create or acknowledge a provider-neutral handoff
resume     report the next safe action and recovery state for one exact item ID
recover    reconcile stale/interrupted/corrupt state explicitly
release    release the current lease
```

Every command must support stable JSON output and stable machine-readable exit codes. Provider
names are metadata only; Claude, Codex, OpenCode, and a minimal Python client must consume the
same contract.

## Pipeline integration

Before intake, assessment, spec, build, or review work, the agent must:

1. list or select the item state;
2. read and validate the item state;
3. verify referenced artifact hashes;
4. claim the item before side effects;
5. record an operation intent;
6. perform or resume the action idempotently;
7. record completion, failure, unknown result, or reconciliation;
8. produce a handoff when another agent should continue.

The state layer must enforce the mandatory `item-reconstruction-intake` gate. Generic intake
acceptance must not imply CS2 or family-adapter eligibility.

## NotebookLM review continuity

If item intake used NotebookLM, review must invoke the same NotebookLM notebook again. The review
flow must:

1. wait until the render settles;
2. register/upload the render as `QA_RENDER`;
3. register/upload the side-by-side comparison as `QA_COMPARISON`;
4. ask NotebookLM to compare reference versus render using `OBSERVED / SUPPORTED / INFERRED /
   UNKNOWN` labels;
5. persist notebook ID, source IDs/roles, prompt, citations, contradictions, confidence, and
   next action in the review evidence;
6. remove only QA-role sources after the review, preserving origin references and technical sources.

If the notebook or required source review is unavailable, review returns `request-input` or an
explicitly accepted lower-confidence fallback. It must not silently claim a normal NotebookLM
review.

## Security and Git policy

- Use an allow-listed state schema and repository/item-root path containment.
- Resolve symlinks and reject paths outside the allowed root.
- Do not store secrets, PII, raw prompts, raw transcripts, credentials, or arbitrary provider
  payloads.
- Hashes are integrity identifiers, not anonymization.
- Durable snapshots, events, and redacted research records may be committed to Git.
- Volatile leases and transient backups remain ignored.
- Branch/worktree/commit divergence requires explicit recovery; Git must not silently merge
  divergent item state. A Git worktree is optional code isolation, not a state namespace.

## Acceptance criteria

- [ ] A fresh run creates a valid deterministic snapshot and initialization event.
- [ ] A minimal client can resume work using only the repository state, with no provider transcript.
- [ ] Claude, Codex, OpenCode, and the minimal client produce equivalent state transitions.
- [ ] Concurrent claims and stale writes are rejected deterministically.
- [ ] Lease expiry and takeover are fenced and auditable.
- [ ] Completed idempotent operations resume as no-ops when outputs still match.
- [ ] Unknown external effects require explicit revalidation/recovery.
- [ ] Intake, generic handoff, and specialized adapter eligibility are distinct machine-checkable gates.
- [ ] Artifact hash drift invalidates dependent work instead of silently continuing.
- [ ] Snapshot/ledger crash windows, corruption, replay, migration, rollback, and Git worktree
      divergence are covered by tests.
- [ ] State rejects secrets, unsafe paths, raw transcripts, and arbitrary provider payloads.
- [ ] `forge/state.py` exposes the documented commands, JSON output, and exit codes.

## Research and design review

NotebookLM research was completed in notebook
`140cddad-1e06-40c6-aa7b-eefca053e455` (task
`1007493b-d1b8-4ae4-ad14-1d2626ae4a98`). It supports checkpoint persistence, atomic writes,
optimistic concurrency, idempotent resume, and explicit stale-owner handling. Repository-specific
decisions were then reviewed by two independent architecture and delivery adversarial subagents.

Debate resolution:

- Keep the ledger, but scope it to orchestration transitions and recovery rather than domain data.
- Keep existing intake/spec/review artifacts as domain authority.
- Require run identity, artifact hashes, CAS, fencing, operation lifecycle, recovery, migration,
  allow-listing, and provider-neutral conformance fixtures.

## OpenSpec artifacts

- `openspec/changes/cross-agent-resumable-state/proposal.md`
- `openspec/changes/cross-agent-resumable-state/design.md`
- `openspec/changes/cross-agent-resumable-state/specs/cross-agent-resumable-state/spec.md`
- `openspec/changes/cross-agent-resumable-state/specs/cross-agent-pipeline-integration/spec.md`
- `openspec/changes/cross-agent-resumable-state/tasks.md`

Validation result: `openspec validate cross-agent-resumable-state --strict` passes.

## Scope boundary

This issue updates the design and contract only. Implementation should follow through the
OpenSpec apply workflow after maintainer review of the storage root, Git policy, and first
integration slice.
