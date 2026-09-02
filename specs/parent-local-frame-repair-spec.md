> Last updated: 2026-09-01 22:30

# Parent-local frame repair — whimsical-hearth-house spec + frame-sanity gate

## Problem

The generated factory for `whimsical-hearth-house` renders with floating parts
(chimney, roof, finial, door, dormer). Root cause, verified in this session:

- The ObjectSculptSpec contract authors `transform.position` and
  `attachment.localStart/localEnd` in **parent-local coordinates**
  (`grimoire/readiness/joint_attachment.md`; the canonical
  `electric-mouse-mascot` spec follows it — e.g. `head` at `[0, 0.238, 0]`
  relative to `neck`).
- The whimsical-hearth-house spec violates the contract: children of
  `house-main` (chimney `[-2.2, 6.35, -0.3]`, roof-main `[0, 5.45, 0.25]`,
  front-door, front-dormer, chimney-smoke, …) are authored in **object-frame
  absolute** coordinates.
- `generate_threejs_factory.py` is correct: it parents each node under
  `nodes[parent]` and applies the position as local
  (`forge/stage3_build/generate_threejs_factory.py:3630-3635`), so every
  child of `house-main` is displaced by house-main's own offset
  `(0, 2.65, 0.3)` — chimney lands at world y 9.0 instead of 6.35.
- `validate_sculpt_spec.py --strict-quality` passes the malformed spec: no
  gate relates a child's parent-local position to its parent's bounds, so an
  object-frame spec sails through and the defect only appears at render time.

## Goals

1. Repair the whimsical-hearth-house spec to the parent-local contract and
   prove the regenerated factory assembles (browser render, no floating
   parts).
2. Close the validation gap with a deterministic frame-sanity check so the
   next object-frame spec is caught at validation time, not at render time.
3. Re-run the dense-evidence A/B on the repaired baseline. The existing
   hash-bound admission dies with the spec change **by design** (it binds
   `targetSpecSha256`); a new admission requires a fresh explicit user
   approval.

## Non-goals

- Changing the generator's parenting or transform semantics (it follows the
  contract; changing it would break every conforming spec).
- Migrating or re-authoring any other shipped spec (mascot and knife specs
  already conform).
- Touching the bespoke shipped demo factory.

## Task A — repair the spec

New one-shot converter `forge/stage2_spec/rebase_component_frames.py`:

- Input: a spec whose component positions are object-frame; output: a new
  spec file (never in place) with, for every component whose parent is not
  `root`: `position_local = position_object - parent_position_object`
  (recursive down the tree, rotations are all zero in this spec — the script
  refuses non-zero parent rotations rather than guessing).
- Applies the same rebase to `attachment.localStart` / `localEnd`.
- Emits a JSON report of every changed field (old → new) for review.
- Run it on `src/demos/whimsical-hearth-house/object-sculpt-spec.json` in the
  showcase worktree, validate with `--strict-quality`, regenerate the
  factory, and capture browser renders (front + three-quarter) proving the
  assembly — the existing mandatory screenshot gate applies.

## Task B — frame-sanity gate in validate_sculpt_spec

New deterministic check (warning by default, failure under
`--strict-quality`): for each component with a non-root parent that declares
numeric dimensions on both sides, flag when the child's parent-local
position magnitude on any axis exceeds `(P + child_half_extent) * 1.5`,
where `P` is the parent's **full** extent when the parent's primitive is in
`ATTACHMENT_PRIMITIVES` (its pivot then sits at `attachment.localStart`,
typically the part's base, so children legitimately reach the far end) and
the parent's **half** extent for center-pivoted primitives — a child that
far outside its parent's volume is almost certainly authored in the wrong
frame. The message names both frames and points at
`rebase_component_frames.py`.

- Threshold rationale (revised during implementation — the drafted
  `parent_half * 2.5 + child_half` formula did NOT flag the observed chimney
  case: 6.35 < 2.4·2.5 + 1.5): with `(P + child_half) * 1.5`, the pre-repair
  house spec produces 7 findings (roof-main, roof-wing, chimney,
  front-dormer, front-door, gutter-system, chimney-smoke) while the repaired
  house spec and every conforming shipped showcase spec stay at zero — the
  test suite proves both directions.
- Focused tests in `forge/tests/`: object-frame spec fails strict, conforming
  specs stay green, warning text names the converter.

## Task A addendum — degenerate attachment segments (found during repair)

The frame rebase alone still rendered five components as ~0.06-radius stubs:
`garden-base`, `turret`, `turret-roof`, `gutter-system`, `garden-tree`. Their
primitives are in `ATTACHMENT_PRIMITIVES`, so the generator derives their
geometry as a strut from `attachment.localStart` to `localEnd` — and the
machine-authored spec gave every attachment a degenerate 0.04-length marker
segment. Second repair applied by hand to the same spec:

- Author real segments spanning each part's extent plus `baseRadius` /
  `endRadius` (island disc r 4.5, tower r 1.175, cone 1.5→0.05, gutter run
  r 0.08 along x, trunk 0.35→0.2).
- Because the endpoint branch moves the node pivot to `localStart`, children
  of `garden-base` needed +0.225 y and `turret-roof` / `garden-tree` needed
  re-authoring relative to the base pivot.

## Task C — re-run the dense-evidence chain on the repaired baseline

Order (each step is the existing tooling, no new code):

1. `check_dense_evidence.py` against the repaired spec.
2. New `admit_dense_influence.py` at `global-massing` — **pauses for the
   user's explicit approval** (hash-bound to the new spec).
3. `apply_dense_evidence.py` → new proposal/delta/fit-plan.
4. Regenerate both factories, browser A/B with the same harness method
   (archived in the run's `dense-evidence/ab/harness/`), metrics, gates,
   `compare_dense_evidence.py` decision.

Expected: with parts assembled, the turntable/intersection failure mode from
the first A/B disappears or shrinks to real findings; the source-IoU/massing
trade-off gets a clean read.

## Acceptance

- Regenerated whimsical factory renders assembled (screenshot evidence read
  back, no floating parts).
- `--strict-quality` fails the pre-repair spec with the new frame-sanity
  category and passes the repaired one.
- Full forge suite green locally and on GitLab CI.
- A/B decision recorded; whatever it is, reported honestly.

## Files

- Create: `forge/stage2_spec/rebase_component_frames.py`, tests in
  `forge/tests/test_rebase_component_frames.py`.
- Modify: `forge/stage2_spec/validate_sculpt_spec.py`,
  `forge/tests/test_validate_sculpt_spec.py` (or the existing validator test
  module), `CHANGELOG.md`.
- Modify in showcase worktree: `whimsical-hearth-house/object-sculpt-spec.json`
  (repaired output replaces it only after review of the change report).

## Execution record — 2026-09-01

All three tasks completed; the user explicitly approved the new hash-bound
admission ("ok decidi te, approvo tutto").

- Task A: 69 fields rebased + 5 degenerate attachment segments re-authored
  (see addendum). Assembly verified by browser captures, world-space mesh
  bounds, comparison sheet and tier-1 diagnostics — evidence under the run's
  `dense-evidence/repair/`.
- Task B: frame-sanity gate live with the revised `(P + child_half) * 1.5`
  threshold; 12 focused tests in `forge/tests/test_rebase_component_frames.py`;
  full forge suite green locally (OK, 4 skips, showcase root set).
- Task C: second A/B under `dense-evidence/ab2/` → **ALLOW**
  (sourceRegression −0.024976, massingSimilarity +0.218512, all 5 gates
  pass). Two first-run gate artifacts were root-caused and fixed in the
  harness/method, not by relaxing rules: specular bands read as enclosed
  background (matte override on both variants) and contract seams read as
  penetrations (differential criterion: baseline authored contacts allowed,
  candidate-only pairs individually reviewed). The proposal replaced the
  accepted demo spec; the pre-ALLOW spec and the reversible 131-change delta
  are preserved in the run.
