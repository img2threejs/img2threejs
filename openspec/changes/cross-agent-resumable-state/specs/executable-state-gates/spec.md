# executable-state-gates Specification

## ADDED Requirements

### Requirement: Executable preflight authorization

The system SHALL provide `forge/state.py preflight <itemId> <action>` as the command-level
authorization boundary. Every intake, assessment, spec, build, generation, and review persistence
command SHALL invoke equivalent preflight before side effects. Skill text, agent prompts, and
transcript claims SHALL NOT satisfy preflight.

#### Scenario: Agent skips the skill text

- **WHEN** an agent invokes a downstream command without a valid item snapshot and persisted
  resume acknowledgement
- **THEN** the command SHALL refuse the action with exit code `RESUME_REQUIRED` and machine-readable
  JSON identifying the item and required recovery command

#### Scenario: Preflight succeeds

- **WHEN** the item snapshot, acknowledgement revision, artifact hashes, lease, and requested
  transition are valid
- **THEN** preflight SHALL return an authorization receipt containing item ID, action, revision,
  fencing epoch, and operation ID before the command performs side effects

### Requirement: Persisted resume acknowledgement

The system SHALL require an explicit acknowledgement of the exact snapshot revision returned by
`resume <itemId>`. Acknowledgement SHALL be invalidated when the item revision, artifact hashes,
lease fencing epoch, or recovery status changes.

#### Scenario: Context compacts after resume

- **WHEN** a new agent runs `resume 2026-05-01-abx --json` and acknowledges revision `17`
- **THEN** downstream commands SHALL be authorized using the persisted acknowledgement without
  requiring the old transcript

#### Scenario: Stale acknowledgement is used

- **WHEN** an agent attempts build or review using acknowledgement revision `17` after the item
  has advanced to revision `18`
- **THEN** preflight SHALL reject with `RESUME_REQUIRED` and require a new resume/acknowledge pair

### Requirement: Stable machine-readable gate failures

The gate SHALL return stable JSON errors and non-zero exit codes for missing or invalid state,
including `RESUME_REQUIRED`, `STATE_INVALID`, `ARTIFACT_DRIFT`, `LEASE_CONFLICT`,
`NOTEBOOKLM_REVIEW_REQUIRED`, and `RECOVERY_REQUIRED`.

#### Scenario: Artifact drift is detected

- **WHEN** a referenced intake, spec, render, or review artifact hash differs from the item snapshot
- **THEN** preflight SHALL return `ARTIFACT_DRIFT` and SHALL NOT allow the next action

### Requirement: NotebookLM review receipt enforcement

When intake provenance says NotebookLM was used, a review continuation SHALL require a validated
receipt containing the same notebook ID, source-role registry, `QA_RENDER` source ID,
`QA_COMPARISON` source ID, prompt/answer hashes, citations, confidence, and review action.

#### Scenario: Review has no NotebookLM receipt

- **WHEN** `append_review.py` requests `continue` for an item whose intake used NotebookLM but no
  valid review receipt is linked
- **THEN** the command SHALL reject with `NOTEBOOKLM_REVIEW_REQUIRED`

#### Scenario: Receipt belongs to another notebook

- **WHEN** a review receipt references a different notebook or missing QA source role
- **THEN** validation SHALL reject the receipt and preserve the prior review state

### Requirement: Gate integration cannot be advisory-only

The canonical pipeline commands SHALL call the executable gate internally and SHALL NOT accept an
agent-provided flag that disables, bypasses, or downgrades preflight in normal operation.

#### Scenario: Bypass flag is supplied

- **WHEN** a caller supplies an undocumented or explicit bypass option to a gated command
- **THEN** the command SHALL reject the option and retain the normal gate requirements

### Requirement: Single-use operation authorization

The gate SHALL persist a single-use operation intent before a gated side effect. The intent SHALL
bind item ID, action, base revision, fencing epoch/token, actor/session identity, input hashes,
registered output paths, and operation ID. Final commit SHALL consume the intent and reject reuse,
stale revision, changed fencing, changed paths, or changed inputs.

#### Scenario: Operation intent is replayed

- **WHEN** a caller submits a previously consumed authorization receipt
- **THEN** commit SHALL return `OPERATION_CONFLICT` and SHALL NOT mutate the snapshot, ledger, or
  domain artifact

#### Scenario: Another agent advances the item

- **WHEN** another agent advances the item after preflight but before the side effect commits
- **THEN** the original operation SHALL become unknown/recovery-required and SHALL NOT silently
  commit against the newer revision

### Requirement: Item and artifact binding

Every gated mutation command SHALL require `--item-id` or a state-owned manifest that resolves one
item. Preflight SHALL bind all input and output paths to the item, reject unsafe/symlink escapes,
and record canonical SHA-256 hashes over the declared artifact bytes and metadata.

#### Scenario: Spec belongs to another item

- **WHEN** a command supplies item A with a spec or output registered to item B
- **THEN** preflight SHALL return `PATH_SECURITY` or `ARTIFACT_DRIFT` and SHALL not write either item

### Requirement: Trusted NotebookLM receipt

The NotebookLM adapter SHALL issue review receipts only after authenticated notebook interaction
and QA source registration. A receipt SHALL bind notebook/session ID, source IDs and content
hashes, `QA_RENDER`/`QA_COMPARISON` hashes, prompt/answer hashes, response ID, citations,
confidence, action, and timestamp. A manually authored receipt SHALL NOT satisfy the review gate.

#### Scenario: Adapter receipt is valid

- **WHEN** the designated authenticated adapter issues a receipt matching the intake notebook and
  the current render/comparison hashes
- **THEN** review preflight SHALL accept the receipt and bind it to the operation

#### Scenario: Receipt is manually fabricated

- **WHEN** a local JSON file contains plausible NotebookLM fields but lacks adapter verification
- **THEN** review SHALL return `NOTEBOOKLM_REVIEW_REQUIRED` and SHALL preserve prior state

### Requirement: Stable command error contract

The CLI SHALL use numeric exits `0` success, `2` usage, `10` state invalid, `11` resume required,
`12` artifact drift, `13` lease conflict, `14` NotebookLM review required, `15` recovery required,
`16` invalid transition, `17` operation conflict, and `18` path/security violation. With `--json`,
errors SHALL use `{ok:false,code,itemId,revision,operationId,message,recovery}`.

#### Scenario: Missing resume acknowledgement

- **WHEN** a gated command is called with `--json` without a current acknowledgement
- **THEN** it SHALL exit `11`, emit `RESUME_REQUIRED`, and include an idempotent recovery command
