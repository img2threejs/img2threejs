# NotebookLM Research Record

## Provenance

- Notebook: `140cddad-1e06-40c6-aa7b-eefca053e455`
- Research task: `1007493b-d1b8-4ae4-ad14-1d2626ae4a98`
- Research query: portable repository-local state for resumable work across Claude Code, Codex,
  OpenCode, and other coding agents, including checkpointing, concurrency, leases, provenance,
  idempotent resume, and Git/OpenSpec compatibility.
- Sources: ten web sources imported by NotebookLM; source IDs and URLs are preserved in the
  NotebookLM task output and should be registered by the eventual intake/research implementation.

## Findings used

- `SUPPORTED`: checkpointing persists volatile agent state so a process can resume after failure.
- `SUPPORTED`: atomic replacement and optimistic concurrency reduce corruption and lost updates.
- `SUPPORTED`: idempotent resume must distinguish intended, started, completed, failed, and
  unknown side effects; completed tool effects must not be blindly repeated.
- `SUPPORTED`: durable event history and a materialized snapshot address different recovery and
  read-performance needs.
- `SUPPORTED`: multi-agent writers need explicit ownership, stale-owner recovery, and fencing or
  equivalent protection against late writes.
- `IMPLEMENTATION`: this repository should use a hybrid snapshot plus append-only transition
  ledger, with existing domain artifacts referenced by hash rather than copied into the ledger.

## Qualification

NotebookLM research is supporting evidence, not authority over this repository. Repository
inspection and adversarial review overruled generic suggestions that would duplicate domain state,
store raw provider payloads, or assume a live OpenCode-style server. The design below is portable
filesystem/JSON/JSONL behavior with no provider-specific runtime dependency.
