# cross-agent-resumable-state Specification

## ADDED Requirements

### Requirement: Item-scoped portable snapshot

The system SHALL store a versioned JSON snapshot under `.agent-state/items/<itemId>/`, where
`itemId` is a stable `<yyyy-mm-dd>-<item-name-slug>` identifier independent of the agent or
provider. The snapshot SHALL include the stable `itemId`, schema version, workflow phase, next safe action,
blockers, revision, repository/worktree provenance, artifact references, intake gate predicates,
and the ledger watermark, without copying domain payloads.

#### Scenario: Fresh item is initialized

- **WHEN** a valid primary reference and item ID such as `2026-05-01-abx` are supplied
- **THEN** the system SHALL create `.agent-state/items/2026-05-01-abx/` with a deterministic
  snapshot at revision `0`, no active lease,
  explicit unresolved gates, and an initialization artifact/event reference

#### Scenario: Snapshot is read by another provider

- **WHEN** a provider-neutral client reads a valid snapshot without access to the original
  transcript
- **THEN** it SHALL recover the run identity, authoritative artifact paths/hashes, current phase,
  blocker, and next safe action

### Requirement: Append-only transition ledger

The system SHALL append typed, newline-delimited JSON events for durable workflow transitions,
claims, handoffs, operation lifecycle changes, invalidation, takeover, and recovery. Each event
MUST include an item ID, unique event ID, monotonic sequence, actor/process identity, operation ID
when applicable, base/result revisions, timestamp, fencing epoch when applicable, and allow-listed
artifact references.

#### Scenario: Transition is committed

- **WHEN** a writer commits a valid transition against the current revision and fencing token
- **THEN** the system SHALL durably append one event and advance the snapshot watermark and
  revision without duplicating domain payloads

#### Scenario: Ledger has a truncated final line

- **WHEN** recovery encounters an incomplete final JSONL record after the last complete event
- **THEN** the system SHALL quarantine the incomplete record, preserve complete prior events, and
  return a recoverable status rather than silently treating it as a valid transition

### Requirement: Atomic snapshot and replay recovery

The system SHALL write snapshots through a temporary file and atomic replacement, and SHALL
reconcile a snapshot against its ledger watermark before allowing resume.

#### Scenario: Ledger is ahead after interruption

- **WHEN** the ledger contains a complete event after the snapshot watermark
- **THEN** the system SHALL replay valid events in sequence, rebuild the materialized snapshot,
  and record the recovered watermark before resuming

#### Scenario: Snapshot claims a missing event

- **WHEN** the snapshot watermark references an event absent from the ledger
- **THEN** the system SHALL quarantine the snapshot and return a blocking recovery error without
  advancing workflow state

### Requirement: Fenced optimistic concurrency

The system SHALL reject writes whose expected snapshot revision or fencing token is stale. Lease
acquisition, renewal, takeover, and release SHALL be conditional operations with unique tokens;
takeover SHALL advance a fencing epoch and produce a durable event.

#### Scenario: Two agents claim the same run

- **WHEN** two agents attempt to claim an unleased run concurrently
- **THEN** exactly one claim SHALL succeed and the other SHALL receive a machine-readable lease
  conflict without changing the snapshot

#### Scenario: Old agent writes after takeover

- **WHEN** an expired lease is taken over and the old agent later attempts a commit
- **THEN** the commit SHALL be rejected because its fencing token/epoch is stale

### Requirement: Idempotent operation lifecycle

The system SHALL record side-effecting operations using deterministic idempotency keys, input
hashes, intended outputs, lifecycle status `planned|started|completed|failed|unknown`, and output
hashes where available.

#### Scenario: Completed operation is resumed

- **WHEN** an operation is `completed` and all referenced inputs and outputs still match
- **THEN** resume SHALL perform a deterministic no-op and return the existing result

#### Scenario: Operation outcome is unknown

- **WHEN** a process ended after an external side effect may have occurred but before completion
  was recorded
- **THEN** resume SHALL require revalidation or explicit recovery and SHALL NOT retry automatically

### Requirement: Artifact authority and invalidation

The system SHALL treat intake, assessment, spec, build, and review artifacts as domain authority;
state SHALL store only normalized path, role, SHA-256 hash, schema version, and observed revision.

#### Scenario: Referenced artifact changes

- **WHEN** an artifact hash differs from the state reference at resume
- **THEN** dependent operations SHALL become stale and the system SHALL block or require explicit
  invalidation before continuing

### Requirement: Item listing and explicit resume

The system SHALL enumerate resumable items independently of which agent created or last modified
them, and SHALL resume only the item selected by its exact `itemId`.

#### Scenario: List interrupted items

- **WHEN** a user requests active or incomplete items
- **THEN** the system SHALL list item IDs, current phase, status, blocker, last revision, and
  last update without loading provider transcripts

#### Scenario: Resume a selected item

- **WHEN** the user requests `resume 2026-05-01-abx`
- **THEN** the system SHALL open only that item directory, validate its snapshot/ledger/artifacts,
  and return its next safe action or a blocking recovery reason

### Requirement: Safe schema migration

The system SHALL validate schema versions, preserve unknown fields within a supported major
version, reject unsupported future major versions, and perform migrations through backup and
atomic promotion with rollback availability.

#### Scenario: Future major version is encountered

- **WHEN** a client reads a snapshot with a higher unsupported major schema version
- **THEN** it SHALL fail closed with a stable error and SHALL NOT overwrite the snapshot

### Requirement: Allow-listed portable state

The system SHALL reject arbitrary provider payloads, raw transcripts, credentials, unsafe paths,
and unapproved metadata from shared state. State paths SHALL remain within the repository/item root
after symlink resolution.

#### Scenario: State payload contains a credential

- **WHEN** a write includes a field or value matching the forbidden secret/credential policy
- **THEN** validation SHALL reject the write and leave the prior snapshot and ledger unchanged
