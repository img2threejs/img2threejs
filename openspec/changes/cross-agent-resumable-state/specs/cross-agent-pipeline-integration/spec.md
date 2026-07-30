# cross-agent-pipeline-integration Specification

## ADDED Requirements

### Requirement: Mandatory state-aware preflight

The reconstruction workflow SHALL read and validate the selected item state before executing intake,
assessment, spec, build, or review actions. Missing, corrupt, stale, blocked, or incompatible
state SHALL stop the action with a stable machine-readable reason and exact recovery/next-input
guidance.

#### Scenario: Intake has not passed

- **WHEN** a client attempts assessment or spec work without a valid intake record reference and
  accepted intake predicate
- **THEN** preflight SHALL block the action and identify the missing intake artifact/status

#### Scenario: State is ready for the next action

- **WHEN** the state and all referenced artifact hashes are valid and the requested action matches
  the persisted next safe action
- **THEN** preflight SHALL allow the action and record an operation intent before side effects

### Requirement: Explicit intake predicates

The workflow SHALL distinguish `intakeAccepted`, `genericHandoffReady`, and
`specializedAdapterEligible`. A generic accepted intake SHALL NOT imply eligibility for a CS2 or
other family-specific adapter.

#### Scenario: Generic intake has no identity

- **WHEN** the primary reference is technically admitted but identity enrichment is unavailable
- **THEN** the state SHALL allow generic handoff when its evidence contract passes and SHALL keep
  specialized adapter eligibility false

#### Scenario: CS2 adapter evidence is incomplete

- **WHEN** a CS2 manifest lacks required authoritative classification or supported family evidence
- **THEN** adapter work SHALL remain blocked even if generic intake is accepted

### Requirement: Cross-agent resume and handoff

The workflow SHALL expose provider-neutral resume and handoff summaries containing item ID, phase,
artifact references/hashes, gates, blocker, next safe action, operation status, and state revision.
Handoff acceptance SHALL be recorded separately from workflow advancement.

#### Scenario: Codex resumes work handed off by Claude

- **WHEN** Codex reads a valid handoff without Claude's transcript
- **THEN** Codex SHALL identify the same item, phase, next action, evidence, and blocker and SHALL
  claim the item before writing

#### Scenario: Handoff is acknowledged

- **WHEN** a recipient accepts a handoff at the current revision
- **THEN** the system SHALL record sender, recipient role, handoff ID, acceptance revision, and
  acknowledgement without falsely marking the downstream build/review complete

### Requirement: Transition recording around domain artifacts

The workflow SHALL use an intent/result/reconciliation protocol around domain writes because
ordinary repository files do not provide a cross-file transaction.

#### Scenario: Domain write succeeds before state commit

- **WHEN** a domain artifact is written but the state commit fails
- **THEN** resume SHALL detect the artifact by hash, record reconciliation evidence, and either
  complete the matching operation or block for explicit recovery without repeating blindly

### Requirement: NotebookLM continuity review

When the item intake record contains NotebookLM analysis, the workflow SHALL invoke the same
NotebookLM notebook for review comparison. It SHALL register the settled render as `QA_RENDER`
and the comparison sheet as `QA_COMPARISON` before asking comparison questions, and SHALL persist
the review prompt, citations, contradictions, confidence, and action with the review evidence.

#### Scenario: NotebookLM-backed review compares a render

- **WHEN** intake NotebookLM provenance is valid and a settled render and comparison sheet exist
- **THEN** review SHALL upload/register both QA sources, ask a reference-versus-render comparison
  using `OBSERVED|SUPPORTED|INFERRED|UNKNOWN` labels, and persist the answer provenance before
  allowing a pass decision

#### Scenario: NotebookLM review is unavailable

- **WHEN** the intake used NotebookLM but the notebook or required source review cannot be reached
- **THEN** review SHALL return `request-input` or record an explicitly accepted lower-confidence
  fallback and SHALL NOT claim a normal NotebookLM-backed review

### Requirement: Provider-neutral conformance

The workflow SHALL define stable JSON output and exit behavior for `list`, `read`, `validate`, `claim`,
`heartbeat`, `commit`, `handoff`, `resume`, `recover`, and `release`, and SHALL provide fixtures
that a minimal client and provider adapters can consume equivalently.

#### Scenario: Minimal client consumes a checkpoint

- **WHEN** a client with no vendor transcript invokes `resume 2026-05-01-abx --json`
- **THEN** it SHALL receive the current phase, safe next action, gates, blocker, revision, and
  artifact hashes using only the portable contract
