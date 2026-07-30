# Design: Cross-Agent Resumable Reconstruction State

## Context

The project already has domain artifacts with their own authority and schemas: intake manifests,
assessment/spec JSON, sculpt pipeline state, and review history. Agents currently infer the next
step from those artifacts and their private transcript memory. That is insufficient when a task
moves between providers or a process exits between side effects and checkpoint writes.

The design adds a thin orchestration layer. It does not replace or copy domain truth.

## Goals / Non-Goals

**Goals:**

- Let any filesystem-capable agent read the same run checkpoint and produce the same safe next
  action without access to another agent's transcript.
- Preserve an auditable transition history and recover after crashes, stale writers, and partial
  writes.
- Prevent concurrent agents from silently overwriting each other's state.
- Make intake gating and handoff machine-checkable.
- Keep state portable, JSON-based, provider-neutral, bounded, and safe to commit to Git.

**Non-Goals:**

- Persisting full conversations, tool output, credentials, or provider sessions.
- Replacing `cs2-intake.json`, assessment/spec JSON, `reviewHistory`, or other domain artifacts.
- Providing a distributed database or cross-machine lock service.
- Making every external side effect exactly-once; unknown external effects require reconciliation.

## Decisions

### 1. Item-scoped storage

Use `.agent-state/items/<itemId>/` with an item ID in the form
`<yyyy-mm-dd>-<item-name-slug>`, for example `2026-05-01-abx`, with:

- `snapshot.json`: latest materialized orchestration state, committed to Git when durable.
- `events.jsonl`: append-only transition/recovery ledger, committed with durable transitions.
- `lease.json`: volatile lease and fencing metadata, ignored by Git.
- `research/`: redacted, content-addressed research records referenced by hash.

`itemId` is generated once for the reconstruction item and is not tied to an agent, provider,
branch, or worktree. If the same item needs a separate attempt, use an explicit attempt field or
an intentional suffix such as `2026-05-01-abx-attempt-02`; do not silently overwrite the prior
attempt. Agents are recorded only as logical actor and process/lease metadata. The snapshot
records normalized repository root, optional worktree ID, branch, commit, target source hashes,
and artifact references. Symlinks resolving outside the repository/item root are rejected.

`git worktree` is optional code isolation and is never the state namespace. Resuming an item from
another worktree is allowed when the referenced artifacts and commit policy are compatible;
branch/worktree drift is reported and may require explicit recovery, but it does not create a new
item automatically.

### 2. Snapshot plus ledger boundary

The snapshot is the fast read model. The ledger is the authoritative history of orchestration
transitions, not a second domain database. Event records contain typed allow-listed metadata,
operation IDs, artifact references, and hashes; they never contain raw transcripts or arbitrary
provider payloads.

Each snapshot stores `ledgerWatermark.seq` and `ledgerWatermark.hash`. A normal commit appends and
fsyncs the event first, then atomically replaces the snapshot with a temp file and `os.replace`.
If the ledger is ahead after a crash, replay events after the watermark and rebuild the snapshot.
If the snapshot claims an event not present in the ledger, quarantine the snapshot and block.
Only complete newline-delimited JSON events are replayed; a truncated final line is quarantined,
while a malformed middle event blocks recovery. Duplicate event IDs or sequence gaps block unless
an explicit recovery event proves the correction. Compaction may remove events only after a
snapshot watermark and an integrity-checked archive are recorded.

### 3. Concurrency and leases

Every snapshot write uses revision compare-and-swap. A writer first atomically acquires
`lease.json` with a random lease token and monotonically increasing fencing epoch. Commit requires
both the expected snapshot revision and current fencing epoch/token. Renewal is conditional on
the same token. Takeover requires an expired lease plus a new epoch and emits a durable takeover
event. A late writer fails even if its wall-clock view says the old lease is valid.

The lease is a coordination aid, not proof of progress. Durable state survives lease cleanup.
Wall-clock timestamps are informational; expiry uses a monotonic local deadline plus a bounded
clock-skew policy recorded in the lease.

### 4. Operations and idempotent resume

Every side-effecting action has an `operationId` and deterministic idempotency key derived from
item, action type, input hashes, and intended output paths. Its lifecycle is:
`planned → started → completed | failed | unknown`.

Completed operations are no-ops when inputs and output hashes still match. Failed operations may
retry only when their action declares retry-safe behavior. Unknown operations never retry
automatically; the resume command requests revalidation or explicit recovery. Output references
include path, role, schema version, and SHA-256 hash.

### 5. Domain reconciliation

Domain artifacts remain authoritative for domain fields. The state layer stores only a reference,
content hash, schema version, and observed revision. On resume, missing or changed artifacts mark
dependent operations stale and block the next action until rebuilt or explicitly invalidated.
The state layer is authoritative only for orchestration transitions and lease/operation metadata.

### 6. NotebookLM review continuity

When the item intake used NotebookLM, every review pass SHALL use the same notebook and preserve
the intake source-role registry. After the render settles, upload the render as `QA_RENDER` and
the comparison sheet as `QA_COMPARISON` before asking NotebookLM comparison questions. The review
record stores the notebook ID, source IDs/roles, prompt or prompt hash, answer/citations,
contradictions, confidence, and action. QA sources are cleaned up by role after the review while
origin references and technical sources remain. NotebookLM review unavailability is a
`request-input` condition unless an explicitly recorded lower-confidence fallback is accepted.

### 7. Portable command contract

Implement a Python stdlib reference CLI at `forge/state.py` with stable JSON output and exit codes:
`init`, `list`, `read`, `validate`, `claim`, `heartbeat`, `commit`, `handoff`, `resume`,
`acknowledge`, `preflight`, `recover`, and `release`. `list` enumerates item IDs and their
resumable status; `resume <itemId>` opens the selected item state and reports its next safe action;
`acknowledge <itemId> <revision>` records that the current context loaded the persisted handoff;
`preflight <itemId> <action>` is called by every downstream pipeline command and rejects missing or
stale acknowledgement. Agents may invoke the CLI or implement the same JSON contract. Provider
names are opaque metadata, not branching behavior.

### 8. Executable enforcement boundary

`SKILL.md`, `AGENTS.md`, and provider prompts document the workflow but are not authorization
boundaries. `forge/state.py preflight` is the authorization boundary. `forge/next.py`, intake/spec
commands, pass orchestration, generation, and review persistence must call it before side effects.
The gate rejects `RESUME_REQUIRED`, `STATE_INVALID`, `ARTIFACT_DRIFT`, `LEASE_CONFLICT`, or
`NOTEBOOKLM_REVIEW_REQUIRED` with stable exit codes and JSON output. A successful continuation
must leave a persisted operation intent and the current item revision.

When intake records NotebookLM provenance, review continuation additionally requires a validated
NotebookLM review receipt containing the same notebook ID, registered `QA_RENDER` and
`QA_COMPARISON` source IDs, prompt/answer hashes, citations, confidence, and action. A text claim
that NotebookLM was used is never sufficient.

The receipt is trusted only when issued by the authenticated NotebookLM adapter or verified by a
designated signature/MAC verifier. A manually authored JSON file is not evidence of an actual
NotebookLM interaction. The receipt binds notebook/session ID, source IDs and content hashes,
QA render/comparison hashes, prompt/answer hashes, response ID, citations, action, confidence,
and timestamp. Semantic confidence remains a review judgment, not a cryptographic fact.

The enforcement threat model is explicit: canonical persistence commands cannot advance without
preflight, and direct edits are detected by artifact/hash drift and block the next gated action.
An actor with unrestricted filesystem/root access can still edit local files; preventing that
requires a separate trusted service or external authorization boundary and is out of scope.

### 9. Command integration matrix

Every command that persists or mutates data is gated and requires `--item-id` or a state-owned
manifest that resolves exactly one item. Pure readers/validators are ungated only when they have
no mutation mode.

| Command family | Mutation examples | Required preflight action | Result/commit |
| --- | --- | --- | --- |
| Intake/manifests | `cs2_manifest.py`, admission reports | `intake` | artifact reference + operation result |
| Assessment/spec | `new_pre_spec_assessment.py`, `new_sculpt_spec.py` | `assessment` / `spec` | output hash + domain reconciliation |
| Build/generation | `generate_threejs_factory.py`, generated files | `build:<pass>` | registered output hashes |
| Diagnostics/evidence | `diagnose_render.py --in-place`, texture/detail/camera outputs, comparison sheets | `evidence` | evidence artifact hashes |
| Review/pipeline | `append_review.py`, `orchestrate_passes.py sync --in-place` | `review` / `sync` | review receipt, reviewHistory reconciliation |
| NotebookLM | source registration and review receipt | `notebooklm-review` | trusted receipt + QA source roles |

Each gated command executes `preflight → persisted single-use operation intent → side effect →
commit/reconcile`. The authorization binds item ID, action, base revision, fencing epoch/token,
input hashes, registered output paths, actor/session, and operation ID. `--force-out-of-order` is
not a state bypass; if retained it becomes an explicit `reopen-review` recovery transition with
reason, actor, lease, and durable audit event.

### 10. CLI and error contract

All commands support `--json`; successful JSON goes to stdout and errors go to stderr with the
same envelope on stdout only when `--json` is requested. Numeric exits are stable:

| Exit | Code | Meaning |
| --- | --- | --- |
| 0 | `OK` | success |
| 2 | `USAGE` | invalid CLI input |
| 10 | `STATE_INVALID` | malformed/unsupported state |
| 11 | `RESUME_REQUIRED` | missing/stale acknowledgement |
| 12 | `ARTIFACT_DRIFT` | bound artifact changed |
| 13 | `LEASE_CONFLICT` | ownership/fencing conflict |
| 14 | `NOTEBOOKLM_REVIEW_REQUIRED` | missing/untrusted receipt |
| 15 | `RECOVERY_REQUIRED` | explicit recovery needed |
| 16 | `INVALID_TRANSITION` | action not allowed by state |
| 17 | `OPERATION_CONFLICT` | intent reused/stale |
| 18 | `PATH_SECURITY` | unsafe or unbound path |

Error JSON is `{ok:false, code, itemId, revision, operationId, message, recovery:{command,
destructive, requiresInput}}`.

### 11. Safety, migration, and Git

The schema has a major/minor version. Unknown major versions fail closed; unknown fields in the
same major are preserved. Migrations validate into a backup, atomically promote the new snapshot,
and retain rollback data. Snapshot, durable events, and redacted research records are tracked by
Git; leases and transient backups are ignored. Secret/PII protection is an allow-list, normalized
path policy, and testable rejection of raw prompts, credentials, arbitrary tool payloads, and
unsafe paths. Hashes are integrity identifiers, not anonymization.

## Risks / Trade-offs

- [Ledger growth] → Compact only at a verified watermark and keep a bounded archive record.
- [Cross-file crash window] → Use event intent/result lifecycle and reconciliation; do not claim
  distributed atomicity.
- [Git merge conflict] → Record branch/worktree identity and require explicit recovery/merge; do
  not auto-merge divergent run snapshots.
- [Provider divergence] → Conformance fixtures exercise only the portable CLI/schema.
- [Sensitive metadata leakage] → Allow-list state fields and run secret/path fixture tests.
- [Stale lease after clock changes] → Use fencing epochs and conditional commit, not expiry alone.

## Migration Plan

1. Add the schema, validator, and read-only `list`/`resume`/`validate` commands.
2. Require explicit `state init --from-artifacts --item-id <itemId>` for existing artifacts;
   never implicitly treat legacy files as initialized state. Ambiguous or drifted artifacts block.
3. Add the shared preflight library, operation intents, and writer integration matrix entries.
4. Enable claim/commit integration in intake, pass orchestration, generation, and review.
5. Add handoff/recovery, trusted NotebookLM adapter receipts, and conformance fixtures.
6. Roll back by removing integration calls; preserve domain artifacts and leave state artifacts
   readable. Never downgrade or overwrite a newer major state schema.

## Open Questions

- Durable snapshots, events, and redacted research records are committed by default; volatile
  leases/backups are ignored.
- All listed mutation-capable command families are in scope; pure readers remain ungated only
  when they cannot write through an output option.
- Same-host writers use an OS/process lock where supported, with CAS and fencing as the portable
  correctness mechanism; unsupported network filesystems return `LEASE_CONFLICT` or `RECOVERY_REQUIRED`.
