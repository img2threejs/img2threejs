## 1. Contract and fixtures

- [ ] 1.1 Define the versioned item snapshot, event, lease, operation, artifact-reference, handoff, recovery, and error schemas from the two capability specs.
- [ ] 1.2 Define the transition matrix, intake predicates, forbidden transitions, invalidation causes, and recovery decision table.
- [ ] 1.2a Define item ID normalization/collision rules, artifact hashing rules, numeric exit codes, JSON envelopes, and the complete writer integration matrix.
- [ ] 1.3 Add provider-neutral fixtures for fresh, intake-blocked, generic-ready, specialized-ready, handoff, conflict, stale lease, interrupted operation, and recovery states.
- [ ] 1.4 Record the redacted NotebookLM research provenance and source-role registry without copying raw provider payloads into shared state.

## 2. Persistence and concurrency

- [ ] 2.1 Implement dedicated state persistence with canonical JSON, atomic snapshot replacement, durable JSONL append, watermarking, and validation.
- [ ] 2.2 Implement sequence/order checks, duplicate detection, truncated-final-line quarantine, malformed-middle-event blocking, replay, rebuild, compaction, and archive verification.
- [ ] 2.3 Implement revision compare-and-swap and lease acquisition, renewal, release, takeover, fencing epoch, and stale-writer rejection.
- [ ] 2.4 Enforce repository/item-root containment, symlink policy, allow-listed fields, redaction, secret/PII rejection, and Git tracking/ignore policy.

## 3. Operations and CLI

- [ ] 3.1 Implement `forge/state.py init|list|read|validate|claim|heartbeat|commit|handoff|resume|recover|release` with stable JSON output and exit codes; `list` enumerates items and `resume <itemId>` selects exactly one item.
- [ ] 3.1a Add `forge/state.py acknowledge` and `preflight`, including stable gate error codes and authorization receipts.
- [ ] 3.1b Implement `state init --from-artifacts --item-id` as the only legacy initialization path; reject implicit legacy state.
- [ ] 3.2 Implement operation idempotency keys, input/output hash verification, lifecycle transitions, no-op resume, unknown-side-effect blocking, and explicit recovery.
- [ ] 3.2a Persist single-use operation intents and bind final commit to operation ID, revision, fencing token, actor/session, input hashes, output paths, and output hashes.
- [ ] 3.3 Implement schema migration, backup, atomic promotion, rollback, future-major refusal, unknown-field preservation, and compatibility checks.

## 4. Pipeline integration

- [ ] 4.1 Update `SKILL.md` to require state read, claim, operation intent, result/reconciliation, and handoff recording across agent providers.
- [ ] 4.2 Add state-aware preflight and transition recording to intake and generic/CS2 handoff paths without duplicating domain payloads.
- [ ] 4.3 Integrate `forge/next.py` and pass orchestration with persisted phase/next-action checks while retaining domain artifacts as authority.
- [ ] 4.4 Integrate review/evidence completion and recovery boundaries, including browser/NotebookLM side-effect operations.
- [ ] 4.5 Enforce NotebookLM review continuity when intake used NotebookLM; register `QA_RENDER` and `QA_COMPARISON`, persist citations/confidence/action, and clean up only QA-role sources after review.
- [ ] 4.6 Integrate executable preflight into intake, `forge/next.py`, pass checks, generation, and review persistence; no normal bypass flag is permitted.
- [ ] 4.7 Validate NotebookLM review receipts against intake notebook/source provenance before allowing `continue`.
- [ ] 4.8 Integrate every mutation-capable writer in the command matrix; classify pure readers and reject mutation through un-gated output flags.

## 5. Verification and handoff

- [ ] 5.1 Add unit and fault-injection tests for atomic writes, replay, torn files, divergence, CAS, leases, fencing, migration, and secret rejection.
- [ ] 5.1a Add black-box no-mutation tests for every gate error, stable numeric exit/JSON contracts, stale/replayed operation intents, and item/artifact binding.
- [ ] 5.2 Add black-box conformance tests using the same fixtures for a minimal Python client and Claude/Codex/OpenCode-shaped adapters.
- [ ] 5.3 Test multiple agents sharing one item, optional Git worktrees, branch/commit drift, missing artifacts, changed artifact hashes, duplicate events, and explicit recovery.
- [ ] 5.4 Update README and workflow documentation with fresh/resume/handoff/recover examples and exact apply/rollback behavior.
