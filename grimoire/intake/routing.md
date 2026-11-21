# Routing And The Four-Group Decision Lifecycle

Read this before running `python3 forge/route.py`, consulting Jev, initializing or completing a
routed child, composing a routed scene, or reporting any routing result to the user. Every
normative statement below is derived from the code at the path named beside it. Where this page and
the code disagree, the code is right and this page is stale: say so and fix the page.

Routing decides which approved workflow should own each object in a reference image. It never
builds a model and never accepts one.

## 0. What is and is not proven

Distinguish implemented mechanisms, narrow runtime observations and qualification:

- **Real-image held-out quality remains unqualified.** `forge/benchmark_decisions.py` distinguishes
  `real` from `protocol-fixture` corpora. Fixture labels and caller-supplied authority attestations
  do not establish independent expert provenance or real-image quality.
- **Integrity is not native parity.** `route_artifact.py` checks D18 bytes/container structure and
  leaves runtime, visual quality and rig parity unevaluated. Separate host-approved native terminal
  producers now execute real geometry/capture checks; composed-scene/D12 acceptance remains separate.
- **Narrow observations are not release qualification.** A character live probe compared 925
  world-space vertex samples: maximum delta `1.21e-8 m`, below `9.97e-5 m`. Pixi's four authority
  probes and eight live replay probes passed with explicitly selected Playwright 1.64. These do not
  qualify arbitrary characters, arbitrary JavaScript, artwork quality or a held-out corpus.
- **Live Jev delivery is not demonstrated.** The release lock pins bytes, not answer quality; no
  renewed live Jev disclosure/paid-call grant or Jev network qualification is claimed here.
- **Terminal mechanisms are conditional, not blanket blockers or blanket passes.** Section 9 names
  the required host approvals, independent producers, fresh replay and authenticated semantic review.
  Scene and CS2 live verification is not claimed complete merely because those mechanisms exist.

Report routing decisions and initialized child states as progress. Never describe them as a
finished reconstruction, an accepted scene or a deliverable.

## 1. Authority: what the base owns, what an answer can do

The base owns, and a provider answer can never change:

- the candidate set and who is eligible (sections 4.2 and 4.3);
- consent, the token budget, the 60-minute object deadline and the correction ceilings (section 5);
- evidence binding: every context binding carries a SHA-256 that is re-read before and after use
  (`decision_control._binding`);
- the journal and workspace lease (`.img2threejs/route/journal.jsonl`, `.img2threejs/route/lease.json`);
- owner-profile approval, terminal gates, artifact validation, composition and acceptance.

A provider (Jev or the host fallback) can only answer typed questions about candidates the base
already filtered. The controller rejects an answer that:

- names a candidate outside the offered set (`out-of-set`);
- targets another group, subject, context digest or model (`group-mismatch`, `subject-mismatch`,
  `context-digest-mismatch`, `model-mismatch`);
- carries executable, secret or path-like fields: keys such as `prompt`, `code`, `command`,
  `instructions`, `disableGate`, `lowerThreshold`, `decisionRefs`, or any key ending in `path`, and
  strings starting with `/`, `~/`, `file:` or a drive letter (`unsafe-context`).

A provider answer never grants acceptance. Concretely:

- `forge/route.py` always prints `executionAuthorized: false`, and with a child operation always
  `terminalStatus: "incomplete"`, `terminalAcceptance: false`, `deliverable: false`.
- Repair rejects the operations `disable-gate`, `lower-threshold`, `hide-component`,
  `remove-required-component`, `unlock-pass`, `change-policy` and the fields `gates`, `thresholds`,
  `policy`, `qualityPolicy`, `criticalFeatures`, `requiredComponents`, `permissions`, `passLocks`,
  `provenance` (`stage4_review/repair_decision.py`).
- Build strategy freezes component membership, global fields and every quality, gate, threshold,
  policy, criticalFeatures, acceptance and decisionRefs field
  (`stage2_spec/strategy_decision.py`).
- Quality review returns `continue` only when the independently measured report passes AND the
  semantic answers pass; either failing returns `refine`; missing evidence returns `request-input`
  (`stage4_review/quality_decision.py`). A `continue` is a pass-level action, not terminal scene
  acceptance.
- Application/retention adapters validate actual outputs and measured or authenticated semantic
  comparison evidence before journalling `completed` or `rejected`. The shared
  `DecisionController.finish_application` records that outcome; it does not itself measure quality.

## 2. The four groups and their typed edges

`decision_control.GROUPS` is the single table. Each group has one context kind, one question-set
version, one typed Jev edge and one Jev command (`jev_cli_bridge.EDGES`):

| Group | Context kind | Advisory kind | Question set | Jev step / command |
|---|---|---|---|---|
| `routing` | `img2.route-context` | `img2.route-advisory` | `route-questions-v2` | `jev-decision-routing` / `decision-routing` |
| `build-strategy` | `img2.build-strategy-context` | `img2.build-strategy-advisory` | `strategy-questions-v1` | `jev-decision-build-strategy` |
| `quality-review` | `img2.quality-review-context` | `img2.quality-review-advisory` | `quality-review-questions-v1` | `jev-decision-quality-review` |
| `repair` | `img2.repair-context` | `img2.repair-advisory` | `repair-decision-questions-v1` | `jev-decision-repair` |

Provider capability edges that stage zero discovers (`stage0_route/discovery.EDGES`), each resolved
through `img2 capabilities --from-kind X --to-kind Y --json`:

| Provider | Edge `from` to `to` | Route card `outputKind` |
|---|---|---|
| `pixi2d` | `image` to `pixi2d-scene` | `2d-scene` |
| `interior` | `image` to `interior-scene-candidate` | `3d-scene` |
| `character` | `character-mesh` to `rigged-character` | `rigged-3d` |
| `cs2` | `image` to `cs2-skin-spec` | `spec-augmentation` |
| `base` (intrinsic) | none; not a plugin | `3d-object` |

Where each group is wired:

| Group | Base-side entry | Command-line entry |
|---|---|---|
| `routing` | `Router` in `forge/stage0_route/route.py` | `python3 forge/route.py` |
| `build-strategy` | `StrategyDecision`, `NativeStrategyDecision`, optional `BestOfN` | `python3 forge/decision.py strategy-spec` or `strategy-build` |
| `quality-review` | `review_quality`; provider-specific terminal context/consumers | `python3 forge/decision.py quality` for pass review |
| `repair` | `prepare_repair`, `begin_repair`, `finish_repair` | `python3 forge/decision.py repair` |

`workflow_decisions.WorkflowDecisions` calls these actual adapters, not a generic consult that
skips their preflight. New routed children receive the lifecycle steps through
`workflow_state.attach_lifecycle_steps`; ordinary legacy states are not implicitly migrated.
Routing alone still does not mean strategy, review or repair ran. `BestOfN` remains a separate
trusted-host API, not an automatically executed route outcome.

## 3. The entry point and its output

```bash
python3 forge/route.py --workspace WS --run-id RUN --image ref.png --objects objects.json
```

`--image` and `--objects` are workspace-relative. `--objects` is required for `--expected-output 3d`
(the default) and optional for `2d`. Other flags: `--intent ID` (repeatable), `--explicit-route ID`.
Child operations need all four harness pins; completion is separate from routing:

```bash
python3 forge/route.py --workspace WS --run-id RUN --image ref.png --objects objects.json \
  --create-children --harness-root R --harness-sha SHA --harness-executable R/bin/img2.mjs --harness-sha256 HEX
python3 forge/route.py --workspace WS --run-id RUN \
  --complete-child OBJECT_ID --decision-ref .img2threejs/route/decision-HEX.json \
  --policy-module /absolute/host/route-policy.py --policy-sha256 POLICY_SHA256 \
  --harness-root R --harness-sha SHA --harness-executable R/bin/img2.mjs --harness-sha256 HEX
```

The harness pins are host-approved: a canonical checkout named `harness`, its exact Git commit, its
`bin/img2.mjs` launcher and that launcher's SHA-256. They are never computed from workspace input.
Harness approval flags without a child operation, or a child operation without all four, are
usage errors. The pin proves an exact local package, not third-party release authenticity; Git,
Node and the OS standard libraries stay trust dependencies (`HarnessPin` docstring).

`forge/stage0_route/route.py` is a separate standalone CLI. It performs real read-only probes but
has no consent, model or paid adapter, so it requests input (exit 3) and never consults Jev. Use
`forge/route.py`.

`forge/route.py` prints one JSON object (`kind: "img2.route-command"`, `version: 1`) and exits `0`
only for status `decided`, `children-initialized` or `child-completed`, else `3`. Argument errors
exit `2`. Fields you can observe:

- `status`: `request-input`, `decided`, `children-initialized`, `child-completed`;
- `decisionRef`, `sha256`: the content-addressed decision, `.img2threejs/route/decision-<sha256>.json`;
- `reasonCode`: see section 10; `requestedCommand`, `requiredFlags`, `purposes` when the user must act;
- `childrenCreated`, `childCount`, `parentStatePath`, `childStatePaths` after `--create-children`;
- `childObjectId`, `parentStatus` after `--complete-child`;
- `executionAuthorized: false` always; `terminalStatus`, `terminalAcceptance`, `deliverable` with child
  operations.

Only the base-owned Jev release lock is accepted (`_approved_lock`: fixed path, `contractVersion`
`jev-v4-four-decisions`, exact file digests). There is no flag to override it, and `forge/route.py`
never enrolls consent, revokes consent, prompts for keys or selects a fallback on its own.

## 4. Stage zero: from image to a routing decision

### 4.1 Inputs

- Reference: an upright PNG or baseline JPEG. EXIF orientation is applied; the original SHA-256, the
  lossless upright reference and the pixel-centre transform are all retained. Progressive or
  unsupported JPEG, palette or interlaced PNG, EXIF-bearing PNG and ambiguous orientation fail
  explicitly (`reference-conversion-required`, `jpeg-conversion-required`,
  `invalid-reference-png`, `invalid-reference-jpeg`, `invalid-reference-exif`,
  `unsupported-reference-orientation`, `ambiguous-reference-orientation`).
- Object proposals (3D only): `img2.object-proposals` v1, `objects: [{objectId, label, role,
  approxBox, observations?, instanceOf?}]`. IDs and labels are lowercase hyphenated opaque
  identifiers. `role` is one of `scene-container`, `subject`, `prop`, `set-dressing`. Boxes are
  normalised `[left, top, right, bottom]` in upright space. At most 12 distinct route groups
  (`object-prioritisation-required`).
- A sealed proposal proves which objects were proposed. It does not prove the image has no omitted
  objects (`stage0_route/__init__` docstring).

### 4.2 Candidate enumeration

`Discovery.enumerate()` runs only read-only probes, never a card's commands, and never installs
anything:

1. `img2 --version --json` must report a `capabilities` command. Otherwise enumeration is
   degraded: no candidates, and Jev availability is `availability-unproven`.
2. For each edge in section 2, `capabilities` answers `answered`, `ambiguous` or `data-fault`. An
   `ambiguous` edge rejects with `provider-ambiguous`; no provider rejects with
   `provider-unavailable`; any `data-fault` degrades the whole enumeration and every candidate is
   then rejected with `enumeration-degraded`.
3. Each claimant must match by pinned bytes: `plugin.json` name, version and declared edge, the
   harness floor, and a pinned `route.json` card (`provider-identity-mismatch`,
   `harness-version-incompatible`, `route-card-missing`, `provider-pin-mismatch`,
   `provider-unpinned`, `unsafe-provider-path`, `provider-file-missing`, `invalid-provider-data`,
   `unapproved-provider`).
4. Declared dependency probes run as version checks (`blender>=`, `node>=`, `python3>=`).
   Character additionally requires actual Node, Chromium, bpy and approved Three facts; host
   selections include `IMG2_CHARACTER_NODE`, `IMG2_CHARACTER_BROWSER`, `IMG2_CHARACTER_BPY_PYTHON`,
   `IMG2_CHARACTER_THREE_ROOT` and `IMG2_CHARACTER_THREE_SHA256`. Availability alone never means
   the fixed terminal gate ran or passed.
5. `blockingGateProduced` is true only when a blocking `program` gate has a real pinned producer
   file. For `character` it is the fixed `tools/gate_routed_terminal.py` named by the card's
   `routedTerminalGate`. This is not evidence that any gate passed.
6. The base appends its own intrinsic `base:generic` candidate. It is eligible only when
   `generate_threejs_factory.py`, `run_gates.py` and `geometry_integrity.py` actually exist as regular
   files.

A candidate id is `<provider>:<sha256(resolvedSha:cardSha)>`, or `base:generic`. The model never sees
display-derived identity: it sees an opaque `candidate-<sha256(id)>` handle plus a trusted `routeId`
(`base-generic`, `interior`, `character`, `cs2`, `pixi2d`).

### 4.3 Eligibility

`route_eligibility.evaluate_candidate` runs before any consultation, with program-owned facts, never
facts from a card or a provider reply. Rejection reasons: `invalid-route-card`,
`invalid-output-request`, `output-kind-mismatch`, `provider-ambiguous`, `provider-unavailable`,
`no-blocking-gate`, `invalid-observations`, `dependency-missing:<probe>`,
`missing-required-roles`, `excluded-roles`, `missing-required-intent`, `excluded-intent`,
`unknown-observation:<key>`, `excluded-observation:<key>`, `missing-required-observation:<key>`.
Observation keys are `enclosure`, `roomCount`, `articulation`, `flatness`, `multiplicity`,
`stylization`; an unknown value never satisfies a requirement. `--explicit-route` removes every
candidate whose id or provider differs (`explicit-route-mismatch`).

The route cards shipped with the sibling plugins:

| Card | Requires | Not for | Cost | Dependencies |
|---|---|---|---|---|
| `interior` | role `scene-container`, `enclosure` `enclosed-room`, `roomCount` 1 | `roomCount` greater than 1 | heavy | `blender>=4.3` |
| `character` | intent `rig` | none declared | heavy | Python, Node, Chromium, bpy and approved Three (section 4.2) |
| `cs2` | none | none | local | `python3>=3.10` |
| `pixi2d` | none (explicit 2D enforced by the base) | none | local | `python3>=3.10` |
| `base:generic` | none | explicit 2D | local | none |

The character card's `display.notFor` still states the conservative readiness prerequisite. The
implementation now has an actual GLB/native rig oracle, frozen-world-vertex comparison and browser
sweep; the narrow observed probe in section 0 does not waive missing per-child measurements.

### 4.4 Decomposition (3D)

Before any routing question, every proposed object is localised by Jev (`passId: decomposition`).
Rounds are sealed in the journal BEFORE their first consultation. All of this is advisory; the base
reconciles it:

- `object:<id>:visible` below 0.5: the object is `dropped` (`object-not-visible`).
- Box IoU between the proposal and Jev's box of at least 0.3: Jev's box is adopted.
- Below 0.3: a second Jev choice (`passId: crop-choice`) picks between `model-crop` and `jev-crop`.
  An unresolved choice keeps Jev's box flagged low-confidence and suspends promotion
  (`decomposition-low-confidence`, or the choice's own reason). Valid uncertainty never lets a
  model preference win.
- Same label, neither an explicit instance, box IoU above 0.6: `merged` (`duplicate-object`).
  Explicit instances (`instanceOf`) stay separate placements even when boxes overlap.
- `missingObjects` at 0.7 or above in round 1 invokes a trusted host callback once
  (`Router(second_proposals=...)`) for ADDITIONS with new ids. There is no third round
  (`missing-objects-after-round-limit`). `forge/route.py` constructs `Router` without that callback,
  so a round-1 missing-objects trigger suspends with `second-proposal-adapter-required`.

### 4.5 Consultation and selection

Objects that share `instanceOf` form one route group. The group is consulted once, over the
intersection of every member's eligible candidates; non-leader members are `placementOnly`. Each
member keeps its own id, validation, box, crop and eligibility.

Controller thresholds (`DecisionController._advisory`):

- `anyFits.noul` below 0.5: `request-input`, `none-fits`.
- Jev: chosen probability below 0.80 or confidence below 0.50 is `uncertain` when more than one
  candidate exists (`choice-uncertain`), otherwise `request-input`.
- Fallback: chosen probability below 0.82 is `request-input`, `choice-uncertain`; never `uncertain`.

Router outcome per object:

| Status | When |
|---|---|
| `resolved` | answered, one selection, not paid or licensed |
| `best-of-n` | Jev decider and (`uncertain` or top-two margin below 0.25); the top two are recorded |
| `request-input` | no common route, none fits, paid or licensed selection (`route-action-consent-required`), unresolved decomposition |
| `dropped`, `merged` | section 4.4 |

A decision reaches `phase: "decided"` (CLI `status: "decided"`, exit 0) when every active object is
`resolved` or `best-of-n`. So `decided` can still hold `best-of-n` rows, and those block execution:
`--create-children` and verification refuse them with `route-execution-unresolved`. `BestOfN`
exists as a library, but `forge/route.py` does not invoke it. A paid or licensed owner is refused
with `paid-or-licensed-owner-not-authorized`; heavy local cost is not paid consent.

### 4.6 Persisted evidence and revisions

- `.img2threejs/route/decision-<sha256>.json`: immutable decision; `latest-<run>.json` is a pointer
  only, never authority.
- `.img2threejs/route-inputs/<category>/<sha256>.json`: content-addressed snapshots (reference,
  object-proposals, route-card, routing-inputs, object-inventory and others).
- `.img2threejs/decisions/<group>/<recordId>.json`: immutable decision records.
- The decision `revision` increments when the semantic inputs digest changes, including a changed
  consent digest. Each object has its own `revision` and `routingRevision`, incremented only when
  that object's own inputs change. An unchanged context with unchanged consent reuses the cached
  record and makes no new Jev call.
- `phase` is `routing`, `decided` or `suspended`; `suspendedAt: {step, reasonCode}` names why.

## 5. Consent, login and the shared budget

- **Login.** When credentials are missing or rejected, the output carries
  `requestedCommand: "img2-jev login"`. Ask the user to run it. Never request, accept or handle
  their key in chat. The CLI's own probes are local: `auth-status` (exit 0 present, 3 missing,
  1 storage error) and `consent decision-status`; neither contacts TypeSafe. A present key can still
  be rejected at the first call.
- **Consent.** Image disclosure and paid calls need a separate explicit grant per run, for all four
  purposes together. When absent or declined, the output carries
  `requestedCommand: "img2-jev consent authorize-decisions"`, `requiredFlags: ["--workspace",
  "--run-id", "--token-budget", "--accept-image-disclosure", "--accept-paid-calls"]` and
  `purposes: [routing, build-strategy, quality-review, repair]`. Show the user the exact run id and
  token budget. Never authorize for them.
- **Four purposes or an explicit refusal.** `LiveConsent` accepts an enabled grant only with exactly
  the four purposes, a SHA-256 and integer `tokenBudget` from 1 to 2^53-1. An explicit user-run
  `img2-jev consent decline-decisions --workspace WS --run-id RUN --token-budget N` records a
  private disabled entry: `decision-status` returns `granted: false`, `purposes: []` and that exact
  entry's digest/budget. It authorizes no Jev image disclosure or call. It is distinct from missing
  consent, and can support the separately host-approved fallback path (section 6). Never decline
  or authorize on the user's behalf.
- **One shared budget.** `tokenBudget` is one run-wide number across all four groups. Each send
  reserves its request limit (default 64000 tokens) before dispatch and publishes its real usage
  after; `used + reserved + limit` above the budget is `budget-exhausted`; usage above the limit is
  `budget-overrun`. The Jev provider factory refuses a budget below 64000 outright
  (`budget-exhausted`). Reservations survive crashes and group switches. After a crash, one retry is
  allowed, then `ambiguous-paid-attempt`. Raising a budget is the user's act; repeat the
  authorization with the same run id and a larger number (plugin-jev `DECISIONS.md`).
- **Image disclosure.** Jev receives only its admitted bound images after a live Jev grant check;
  its pinned helper prepares private workspace assets and its pinned CLI sends them. Context
  projections remove original local paths. Separately approved fallback programs can now receive
  the exact declared bound PNG/JPEG images through a private hash-pinned manifest (section 6);
  a Jev refusal alone never enrolls another program or approves that disclosure.
- **Changes mid-flight.** Consent is re-probed live at every consult, consume and verification. A
  grant that changed since the decision was made is `consent-changed`.
- **Ceilings.** 60 minutes per object from first consultation (`deadline-exhausted`); only started
  repair applications count toward 3 per pass and 6 per object (`correction-budget-exhausted`).
  Rejected repair starts still count; strategy applications and reviews do not consume correction slots.

## 6. Host-model fallback and the proven-outage rule

The controller alone gates the fallback (`DecisionController._consult`). A fallback may be asked
only after a journalled, proven Jev outage, once per request, never after a Jev answer.

The controller reads consent before availability. `LiveConsent` authenticates the pinned local
Jev package and its private consent store. Missing/unprovable consent, or an enabled grant without
usable credentials, suspends; neither is a synthetic refusal or fallback authorization. A valid
explicit disabled entry is returned even when credentials are absent. With a separately approved
fallback, the controller journals that run-wide `consent-declined` outcome and may consult the
fallback without disclosing to or calling Jev. Disclosure to the separately approved fallback is
its own host authorization (below). Without a verifiable local provider/consent
contract it still suspends; it never invents a grant from missing storage.

Other CLI-reachable fallback triggers are journalled Jev request failures while the provider is
present, authenticated and consented: `provider-error`, `timeout`, `malformed`, `out-of-set` or
`ambiguous-paid-attempt`. Errors collapsed to `malformed` by the controller also follow that rule.
A direct trusted host API can additionally supply independently proven `not-installed`,
`capability-unavailable` or probe-attested `login-declined`; unproven availability never suffices.

These never activate a fallback; the run suspends with `request-input`:

- `credential-missing`, `credential-rejected`, `credential-storage-error`: the user must log in;
- `availability-unproven`, including an unresolved, ambiguous or data-fault capability edge;
- valid uncertainty (`uncertain`, `choice-uncertain`, `none-fits`);
- budget, deadline, evidence or consent failures (`budget-exhausted`, `budget-overrun`,
  `deadline-exhausted`, `missing-evidence`, `stale-evidence`, `vision-forbidden`,
  `permission-denied`, `consent-changed`, `consent-required`, `consent-probe-error`).

Missing or unprovable consent suspends. An explicit authenticated refusal follows the distinct
declined-consent path above; fallback payment/model approval is still the host's responsibility.

Properties of an accepted fallback record: `decider` is `model:<id>`, `unavailableReason` records the
proven outage, the 0.82 floor applies, and a fallback answer is never `best-of-n`. It shares the same
run budget and reservation.

`forge/route.py` selects no fallback by default (`fallback=None`). A host opts in with both flags, or
neither (`--fallback-command PATH --fallback-sha256 HEX`; not accepted with `--complete-child`):

- the program is named by the host and pinned by exact SHA-256 (`stage0_route/fallback.py`). It is
  never discovered on `PATH` and never chosen from workspace or model input;
- its resolved target must be a regular executable. The target's bytes are re-verified at
  construction, before every call and after the run. The digest covers only that file: a script's
  interpreter, imports and configuration remain host trust, and the verify-to-exec window is not closed;
- no shell, no arguments, no retry. A private scratch directory is its working directory, not the
  workspace. The child inherits the host environment minus `NODE_*`, `PYTHON*` and
  `TYPESAFE_API_KEY` (the same strip `HarnessPin.runner` applies); this is not an allow-list, so
  proxy and other host variables still reach it. One process group is killed on every exit path.
  The timeout defaults to 60 s (maximum 300 s). stdout is capped at 256 KiB and stderr at 64 KiB;
- stdin remains the canonical path-stripped JSON projection, with hash-only bindings and opaque
  image placeholders. `HostFallback.bind_context` retains original validated bindings locally;
  only if the controller actually chooses this independently approved fallback does it copy the
  exact declared PNG/JPEG bytes into private scratch and produce `img2.host-fallback-images` v1.
  The private manifest and its digest travel via `IMG2_FALLBACK_IMAGE_MANIFEST` and
  `IMG2_FALLBACK_IMAGE_MANIFEST_SHA256`, never original workspace paths in stdin. Limits are 64
  images, 48 MiB total and 32 MiB per image; roles, assets, context digest, hashes and decoded image
  formats must match. A direct image-bearing call without the bound context fails closed.
  stdout must be one `img2.decision-advisory` v1 with matching `contextDigest` and exit status 0;
- this is not a sandbox. The approved program runs with the host user's privileges and can still
  reach the network or filesystem. Whether it needs its own consent for paid use is the host's
  responsibility.

The image-capable external program needs a **fresh exact program-digest approval**. An old digest
does not approve changed image-disclosure behavior. Explicit Jev decline changes only Jev's grant;
it never selects/enrolls a fallback program or grants that program independent payment/disclosure rights.

Startup refusals (exit 3): `fallback-command-unsafe`, `fallback-digest-mismatch`. A fallback that ran
and failed is journalled and emitted as `fallback-invalid`, which collapses timeout, non-zero exit,
malformed output and oversized output. When a fallback answered, the output gains `fallbackDecisions:
[{decider, unavailableReason, outcome, reasonCode, recordId}]`; the key is absent otherwise.

A model fallback has never been qualified against real images. Treat its answer as an advisory with
a stricter floor, not as equivalent evidence to Jev.

The concrete no-argv adapter is `forge/omp_model_fallback.py`. The host explicitly supplies
`IMG2_FALLBACK_OMP` (absolute canonical executable) and `IMG2_FALLBACK_MODEL` (exact provider/model);
there is no default model. The adapter requires `omp/19.0.1`, uses `--no-tools --no-extensions
--no-skills --no-rules --no-context-files --no-session --mode json -p`, fixed system/append prompts,
and a private per-run configuration disabling all twelve compiled MCP discovery sources and
retry/model substitution. `--no-tools` alone does **not** disable MCP. It shares a bounded 55-second
deadline and the transport output caps; installed configuration is untouched. The adapter rechecks
the private manifest and copies only its exact approved image bytes to program-generated scratch
filenames, supplied to OMP as positional `@images`; arbitrary requested attachments are not allowed.
Model/provider and fresh/cache-read/cache-write/output counts come from the actual OMP event,
never model-authored counters or estimates. Completed turns return observed usage even for invalid
answers so the shared controller can reject and debit truthfully; transport failures retain the
reservation. An earlier **no-image** nonprivate fixture observed 655 fresh input, zero cache
read/write and 46 output tokens in 4.56 seconds. It does not verify the new image attachment path,
image understanding, Jev delivery, held-out quality or general filesystem/network confinement.


## 7. Explicit 2D

Pixi2D is reached only by an explicit request: `--expected-output 2d`. The default is `3d`, and
nothing infers 2D from a failed or awkward 3D request. In code:

- `evaluate_candidate` rejects `pixi2d` for every 3D request (`output-kind-mismatch`), and rejects
  every non-`pixi2d` candidate for a 2D request;
- 2D ignores any `--objects`, makes one `whole-image` proposal and runs no decomposition;
- no `pixi2d` candidate for a 2D request is `explicit-2d-unavailable`; there is no fallback to 3D;
- the owner mapping demands `expectedOutput == "2d"` for `pixi2d` and `"3d"` for everything else
  (`unapproved-owner-profile`); verification and parent assembly refuse a 2D route whose inventory
  is not exactly the one `whole-image` object (`explicit-2d-mismatch`);
- 2D sets `skipComposition: true`: the parent checklist has no `composition` or `scene-gates` step;
- a 2D child is a `2d-scene` using native Pixi work plus base-owned decision lifecycle boundaries.

Never run `--expected-output 2d` to get around a failed 3D route.

## 8. Children, revisions and the parent checklist

`--create-children` runs only on a `decided` route with every row `resolved`, `dropped` or `merged`.
It refuses to run when `.img2threejs/state.json` already exists (`existing-parent-preserved`), and an
existing differing child is never reset (`existing-state-conflict`). Before it writes anything, an
aggregate authority independently re-verifies live provider pins, dependency facts, the journal's
round seals, owner-profile approval and the saved decision (`RouteAuthority`).

Owner profiles are approved by the base, never by a card or an answer:

| Selected route | Owner profile | Terminal output kind |
|---|---|---|
| `base:generic` (3D request) | `generic` | `3d-object` |
| `character` | `character` | `rigged-3d` |
| `cs2` | `cs2` | `3d-object` (the card's `spec-augmentation` is consumed inside the child) |
| `interior` | `interior` | `3d-scene` |
| `pixi2d` (2D request) | `pixi2d` | `2d-scene` |

Layout written:

- one child per artifact-owning resolved object:
  `.img2threejs/objects/<objectId>/rev-<revision>/state.json`, plus its own copy of
  the reference crop. Repeated placement-only instances share their group leader's
  child; that child's artifact must name the owner route and every grouped object
  in `ownedObjectIds`. `revision` is the leader's object revision (section 4.6),
  so a changed leader gets a new directory;
- a parent `.img2threejs/state.json` holding the decision, the child descriptors and a checklist:
  one `object:<id>` setup step per child, then, for 3D only, `composition` and `scene-gates`
  final steps. The parent's `composition` and `scene-gates` commands name
  `python3 forge/compose.py scene` and `python3 forge/compose.py gates` (section 9).

Run `python3 forge/next.py --state .img2threejs/state.json` from the parent workspace.
For a pending child it prints a command that changes into that child's workspace
and reports its current step; this reports progress, not terminal acceptance.

Child checklists: `generic`, `character` and `cs2` retain their sculpt/profile work. Interior retains
its six native intake/exclusion/workflow/authoring/export/delivery steps; Pixi retains its six
snapshot/capture/produce/visual-review/publish/editable-review steps. New routed children also
receive `strategy-spec`, `strategy-build`, `quality-review` and `repair-decision` at the relevant
spec/build/review/publication boundaries. All start pending. Creating them builds nothing.

An `interior` child receives `routeChild.excludedObjectIds`: every other resolved object, sorted. Two
interior containers are refused (`multiple-containers`).

**Run a child decision boundary from the base checkout**, naming the parent workspace:

```bash
python3 forge/decision.py strategy-spec --workspace WS --run-id RUN \
  --child-state .img2threejs/objects/ID/rev-N/state.json --request REQUEST \
  --policy-module /absolute/host/policy.py --policy-sha256 POLICY_SHA256 \
  --harness-root R --harness-sha SHA --harness-executable R/bin/img2.mjs --harness-sha256 HEX
```

Use `strategy-build`, `quality` or `repair` for the corresponding next boundary. The host policy
module exports `build_policy(workspace: Path, state_path: str) -> WorkflowPolicy`; candidate JSON
cannot supply scope, permission or provenance authority. Its canonical regular `.py` file must
be outside the workspace/provider/harness trees, with an independently approved exact digest.
The shared `host_policy.pinned_policy_module` pins that file only: Python, imports and callback
dependencies remain trusted host code, not an importer sandbox or authenticated provenance.
Missing policy never auto-approves. Native strategy requires its actual host-approved validator,
pass resolver and live native permission authority; base strategy runs the real strict spec validator.

Strategy/repair requests use `operation: select`, `begin` or `finish`. Selection leaves the step
pending. Strategy completion requires an actual separate `candidates/rev-N/` spec matching the
selected validated full proposal; original inputs and the incumbent remain intact. The lifecycle's
`candidateRevision` is not a promotion of `routeChild.revision` or the parent artifact. Pass quality
requires current matched diagnostics before consulting; a passing review skips only unnecessary
repair. A fresh deterministic failure or an authenticated failed semantic review and scoped
permissions are required for repair; retained repairs require fresh build/capture/review, and
rejected trials never replace the incumbent.

Semantic-only repair binds before `quality_review` and after `after_quality_review` as
`{context, record}` pairs consumed from the actual controller journal. After `repair` operation
`begin`, submit the successor's matched evidence through `quality` operation `trial`, using the
begun application's `trialRevision`. This records `decisionPending.afterQualityReview` without
completing quality, retaining the repair or promoting the incumbent. Repair `finish` consumes that
stored review; an optional request `afterQualityReview` must exactly match it, never supply authority.
Frozen `criteria.semantic` specifies `targetQuestion`, `minImprovement`, tolerances for every
protected question and, for critical choices, explicit `choiceScores: {pass: 4, fail: 0}`.
Matching caller-written scores are not authority.
The camera binding must be `img2.repair-camera` v1 with actual per-view `matrixWorld`,
`projectionMatrix`, `framing`, complete pose transforms, `timeMs`, dimensions and renderer
configuration. Before/after physical capture meaning and rubric must match; the host independently
admits those bytes against the real producer. Arbitrary matching JSON cannot authenticate a capture.

A nonvisual deterministic hard-gate failure can reach repair through a journalled
`workflow-quality-blocked` receipt and child `decisionBlocks`, without pretending visual review
ran or passed. Completion verifies that block and its successful repair; final acceptance still
requires current answered passing quality, never an unrepaired/skipped block.

Only current `controller.consume` plus held-lease verification may persist `decisionRefs` and mark
the matching boundary. Terminal verification revalidates the journal-backed applications, revision
chain and mandatory prior-pass receipts against immutable candidate snapshots. Deleting lifecycle
metadata or receipts from a routed child is a refusal, not a legacy-state exemption. Login
suspension returns the existing command and a two-prompt limit; no command loops over login prompts.

`--complete-child` revalidates the saved decision and the child without rerouting and without a
model call. It marks the parent's `object:<id>` row done only after all of these hold: the parent
still matches the re-derived plan (`stale-parent`, `invalid-parent-checklist`), no final row has
started (`composition-already-started`), the child state is complete with every final and rig step
done (`child-terminal-gates-incomplete`), the child's artifact loads and names this object and kind
(`child-artifact-mismatch`), and the provider's terminal authority passes. A completed child is not
an accepted composed scene.

## 9. Terminal artifact ownership and composition

**Terminal authority is conditional.** `RouteAuthority.verify_terminal` dispatches CS2 to
`CS2TerminalAuthority` and base/interior/character to `NativeTerminalAuthority`; Pixi uses its
reviewed gate, semantic consumer and `PixiCaptureAuthority`. Missing or revoked host policy,
unsupported input, stale evidence, a failed measurement or an unauthenticated review blocks that
child. A file digest, provider-authored pass flag or editable `review.json` is never authority.

| Owner | Required independent boundary |
|---|---|
| `generic`, `interior` | Host-reviewed exact source/dependency closure; actual native/static geometry and approved-camera material/part captures; matched journal semantic review; fresh native replay at completion |
| `character` | The same source/runtime approval plus actual GLB skin/animation data, frozen world-space vertex parity across pose fractions, at least twelve blocking gates and meaningful browser views |
| `cs2` | Explicit source/classification/calibration and complete browser-bundle approval; actual delivered-GLB two-phase capture and measured mechanical gates; shared semantic identity/finish/critical-feature review; independent rerender and blocking gate |
| `pixi2d` | Independently approved requirements and exact editable ESM source/asset/dependency closure; answered journal-authenticated semantic review; fresh independently owned CDP replay plus reviewed blocking gate |

Native structural/material captures do not establish semantic identity: each required part and
critical feature still needs the bound semantic judgment. Interior's actual bpy audit and four
views are structural evidence, not a substituted identity/visual pass. Pixi executes trusted,
reviewed code in the same realm; it is **not** arbitrary-JavaScript confinement or proof of
source/build equivalence. CS2 pixel/mechanical surrogates do not authenticate source truth, recover
a physical BRDF or prove asset identity. These are per-child gates, not scene acceptance.

CS2 host policy requires `browser_root` and the complete immutable `browser_files` inventory from
`browser_runtime.snapshot_browser_bundle`; a hash of only the launcher is insufficient. Hashing is
not host review. Provider cache exemptions are relative to the approved provider root; ancestor
directory names cannot exempt unapproved imports.

**Terminal prepare/consult/completion handoff.** The landed
`RouteAuthority.prepare_terminal_review(verify, object_id)` runs fixed approved local producers and
returns the exact shared quality-review context without a paid consultation or acceptance.
Consultation must occur outside the held parent transaction; final verification consumes the actual
journal record and independently reruns the capture/gates. Pixi, native and CS2 semantic consumers
accept only actual controller-authenticated `jev:` or proven-outage `model:` records with matching
subject/evidence. A decider string or saved provider-authored review alone cannot authorize one.

`forge/terminal.py` and the route CLI policy plumbing are implemented. The invocations below
describe that landed interface, **not a claim that an integrated runtime or terminal acceptance
gate passed**. Missing policy must never be replaced with an empty auto-approval or a
candidate-authored flag.

```bash
python3 forge/terminal.py prepare --workspace WS --run-id RUN --object-id ID --decision-ref REF \
  --policy-module /absolute/host/route-policy.py --policy-sha256 POLICY_SHA256 \
  --harness-root R --harness-sha SHA --harness-executable R/bin/img2.mjs --harness-sha256 HEX
python3 forge/terminal.py consult --workspace WS --run-id RUN --object-id ID --decision-ref REF \
  --policy-module /absolute/host/route-policy.py --policy-sha256 POLICY_SHA256 \
  --harness-root R --harness-sha SHA --harness-executable R/bin/img2.mjs --harness-sha256 HEX
python3 forge/route.py --workspace WS --run-id RUN --complete-child ID --decision-ref REF \
  --policy-module /absolute/host/route-policy.py --policy-sha256 POLICY_SHA256 \
  --harness-root R --harness-sha SHA --harness-executable R/bin/img2.mjs --harness-sha256 HEX
```

The route policy factory interface is
`build_route_policies(workspace: Path, run_id: str) -> route_authority.RoutePolicies`. Its four live
maps are `pixi_quality`, `pixi_capture`, `cs2_terminal` and `native_terminal`; they contain the
separately reviewed per-object policy objects described above. The shared pinned-module loader
checks the host-approved file outside workspace/provider/harness roots and the base checkout, not
the authenticity of that module's imports or the truth of its source-review claims. Both terminal
commands regenerate context from actual approved producers; `consult` never trusts a caller's
saved context, even after `prepare`. Preparation makes no paid call and writes no review link.
Consultation runs outside the parent transaction and links only an answered, reverified
`quality-review` journal record to the immutable hash-named context. Neither command accepts a child.
Only subsequent verified completion can mark the parent child row ready, never the whole scene
delivered. Paired `--fallback-command`/`--fallback-sha256` flags apply to terminal consultation, not
`route.py --complete-child`; consumer decider restrictions and shared outage/budget rules still apply.

**Artifact ownership (3D).** Each child terminal artifact is a D18 manifest
(`route_artifact.validate_artifact`: `objectId`, `outputKind`, `files` with SHA-256, metre/+Y/+Z
frame, `bottom-centre` pivot, `scaleEstimate`, `roomDimensions` for a scene, `rig` for rigged
output). Composition adds three ownership fields (`stage6_compose/compose.py`):

- `ownerRoute`: must equal the inventory row's `ownerRoute` (the candidate id);
- `ownedObjectIds`: exactly the inventory object ids this artifact owns. Repeated separately placed
  instances share `instanceOf`, `ownerRoute` and `artifactObjectId`;
- `excludeComponents`: on the single `scene-container` artifact, exactly the ids of every
  independently routed object it must NOT contain. An interior child's own completion check also
  requires `ownedObjectIds == [objectId]` and `excludeComponents == routeChild.excludedObjectIds`
  (`interior-exclusion-binding-mismatch`).

File paths in the manifest are relative to its child workspace, not the parent
workspace. The composer resolves them against the artifact manifest directory
and checks the resulting files without modifying the manifest.

**Composition.** `python3 forge/compose.py scene --workspace WS --request REQ --output-dir DIR`
writes a NEW candidate directory with `scene.mjs` and `scene.json`; it never advances state,
consults a provider or accepts delivery. `gates --workspace WS --composed scene.json --measurements M
--inventory INVENTORY` runs the scene gates over externally produced measurements, taking the
expected object ids from the inventory, never from the scene. A measurement is `{value, producer,
sceneSha256, referenceSha256}`; a missing or stale one is `unevaluated`, never a pass. Exit `0`
success or pass, `2` usage or unreadable input, `3` refusal or gate failure. Scene and gate
results carry `deliverable: false`. 2D returns a bypass without reading files.

Source-only artifacts still refuse composition (`source-consumer-unavailable`): the current consumer
needs actual supported GLBs; unsupported light/compression extensions remain blocked. The actual
placement/measurement producer commands in `forge/compose.py` are:

```bash
python3 forge/compose.py prepare-placement --workspace WS --request INPUTS --output-dir PREP \
  --host-pins HOST_PINS --host-pins-sha256 HEX
python3 forge/compose.py consult-placement --workspace WS --request PREPARATION_REQUEST --run-id RUN \
  --harness-root R --harness-sha SHA --harness-executable R/bin/img2.mjs --harness-sha256 HEX
python3 forge/compose.py place --workspace WS --request DECIDED_PLACEMENT_REQUEST --run-id RUN \
  --harness-root R --harness-sha SHA --harness-executable R/bin/img2.mjs --harness-sha256 HEX \
  --output-dir PLACEMENT
python3 forge/compose.py measure --workspace WS --request MEASUREMENT_INPUTS --output-dir MEASURED \
  --host-pins HOST_PINS --host-pins-sha256 HEX
```

`HOST_PINS` has exactly `chrome` and `modules`; its independently supplied SHA-256 is host approval,
not a value proposed by the request. Preparation uses actual GLB dimensions, a rerun camera solve,
explicit depth/support observations and an admitted metric landmark span. Non-container objects
need a known `classId` in `_shared/size_priors.json`; unknown classes block. Those frozen bands are
provisional engineering bounds, **not** independent corpus calibration. Size/yaw choices are
journal-backed shared `build-strategy` decisions, consumed under the parent lease before placement.
The producer name and digest alone never authenticate depth, support, camera landmarks or masks.

`measure` renders the exact current scene with host-pinned Chrome/modules and derives scene
geometry, object-ID foreground and image metrics. An optional `reference_mask` must bind the exact
reference digest, dimensions and Boolean pixels; the parent independently admits its producer.
No caller scores substitute for these measurements. The older `request` command still joins
`--state`, `--inventory`, `--reference` and `--placement` into a compose request; it is a structural
join, not producer authentication or permission to deliver.

## 10. Reason codes a caller can observe

Several layers carry reason codes. Report the one the caller can see, and open the decision file for
the rest.

**1. `forge/route.py` top-level `reasonCode`.** A code is printed only if it is on the CLI allow-list
or is a parent-assembly or authority error (`ParentError`); anything else prints
`invalid-routing-input`. For a suspended decision the specific cause stays in the decision file
(`suspendedAt.reasonCode`, each object's `reasonCode`) and the top level prints `routing-suspended`
unless that cause is itself on the allow-list.

- Login and consent: `credential-missing`, `credential-rejected`, `credential-storage-error`,
  `consent-required`, `consent-declined`, `consent-changed`, `consent-storage-error`,
  `consent-probe-error`, `budget-exhausted`, `budget-storage-error`.
- Provider and release pin: `provider-unpinned`, `provider-pin-mismatch`, `provider-contract-unproven`,
  `unsafe-provider-path`, `capability-unavailable`, `availability-unproven`, `stale-evidence`,
  `timeout`, `provider-error`, `malformed`, `unsafe-path`.
- Request and workspace: `invalid-routing-input`, `invalid-routing-result`, `invalid-request`,
  `invalid-object-proposals`, `objects-required`, `invalid-json`, `evidence-missing`, `context-fault`,
  `context-too-large`, `workspace-busy`, `lease-lost`, `routing-suspended`.
- Harness and owner approval: `harness-pin-required`, `harness-pin-mismatch`,
  `harness-runtime-unavailable`, `harness-import-closure-unproven`, `authority-command-not-authorized`,
  `unapproved-owner-profile`, `owner-output-mismatch`, `owner-profile-unavailable`,
  `owner-profile-provider-mismatch`, `profile-registry-unavailable`,
  `paid-or-licensed-owner-not-authorized`.
- Route aggregate: `route-execution-unresolved`, `existing-parent-preserved`, `existing-state-conflict`,
  `stale-route-aggregate`, `invalid-route-authority-input`, `route-round-unsealed`,
  `live-provider-unavailable-or-ambiguous`, `live-provider-identity-mismatch`,
  `live-provider-contract-mismatch`, `live-dependency-mismatch`, `live-blocking-producer-mismatch`.
- Fallback startup: `fallback-command-unsafe`, `fallback-digest-mismatch`.

`routing-failed` and `route-round-link-mismatch` are on the allow-list, but no code path in this
revision raises them. Everything else raised while routing or verifying prints
`invalid-routing-input`. That covers any `RouteError` not named above (the stage-zero
verification family, for example `stale-routing-inputs`, `route-selection-mismatch`,
`decision-integrity-failed`, `eligibility-mismatch`, `unlinked-routing-consultation`), any
`ArtifactError`, and any controller `DecisionError` not named above (for example `journal-broken`,
`fallback-unproven`, `route-round-required`, `route-round-order`, `stale-decision`). Open the
workspace journal and decision files to see the specific cause; the CLI does not print it.

**2. Inside the decision file** (`suspendedAt.reasonCode`, row `reasonCode`, `rejected[]`):

- Image and proposals: `invalid-reference-png`, `invalid-reference-jpeg`, `invalid-reference-exif`,
  `reference-conversion-required`, `jpeg-conversion-required`, `unsupported-reference-orientation`,
  `ambiguous-reference-orientation`, `object-proposals-required`, `objects-required`,
  `invalid-object-proposals`, `invalid-instance-group`, `invalid-object-box`,
  `object-prioritisation-required`.
- Decomposition: `object-not-visible`, `decomposition-crop-choice-required`,
  `decomposition-low-confidence`, `decomposition-incomplete`, `duplicate-object`,
  `instance-reconciliation-unresolved`, `missing-objects-after-round-limit`,
  `second-proposal-adapter-required`, `second-proposal-error`, `ambiguous-proposal-attempt`,
  `duplicate-round-object-id`, `proposal-request-mismatch`, `stale-proposal-round`,
  `proposal-seal-rejected`, `no-visible-objects`.
- Routing: `none-fits`, `choice-uncertain`, `no-common-instance-route`, `explicit-2d-unavailable`,
  `route-action-consent-required`, `provider-unbound`, `availability-unproven`, `deadline-exhausted`,
  `correction-budget-exhausted`, `budget-overrun`, `missing-evidence`, `fallback-invalid`,
  `ambiguous-paid-attempt`.
- Candidate rejection: section 4.2 and 4.3 codes, plus `explicit-route-mismatch`.

**3. Parent, authority and composition codes.** A `ParentError` carries a code that
`forge/route.py` prints verbatim, even when it is not on the allow-list. These come from
`--create-children` and `--complete-child`.

- Parent assembly and completion: `aggregate-verifier-required`, `child-artifact-mismatch`,
  `child-state-mismatch`, `child-terminal-gates-incomplete`, `composition-already-started`,
  `consent-unproven`, `duplicate-json-key`, `duplicate-object`, `duplicate-or-unsafe-object`,
  `explicit-2d-mismatch`, `interior-exclusion-binding-mismatch`, `invalid-2d-artifact`,
  `invalid-aggregate-receipt`, `invalid-crop-digest`, `invalid-object-inventory`,
  `invalid-output-branch`, `invalid-parent-checklist`, `invalid-route-decision`,
  `inventory-completeness-unverified`, `inventory-mismatch`, `inventory-object-mismatch`,
  `multiple-containers`, `provider-file-missing`, `reference-mismatch`, `stale-parent`,
  `stale-reference-crop`, `unapproved-provider`, `unknown-child`, `unsafe-authority-path`,
  `unsafe-provider-import-path`.
- Authority and harness: `actual-router-required`, `aggregate-authority-mismatch`,
  `authority-command-timeout`, `authority-command-unavailable`, `base-provider-identity-mismatch`,
  `character-host-binding-changed`, `character-three-binding-changed`, `gate-interpreter-changed`,
  `gate-interpreter-unavailable`, `gate-runtime-changed`, `held-route-transaction-required`,
  `invalid-authority-command-output`, `invalid-discovery-snapshot`, `live-harness-incompatible`,
  `live-harness-unavailable`, `owner-profile-approval-changed`, `owner-profile-definition-changed`,
  `pinned-gate-runtime-required`, `provider-python-sources-missing`,
  `provider-sources-changed-during-gate`, `stale-discovery-snapshot`, `unsupported-gate-runtime-layout`,
  `unsupported-provider-import`.
- Terminal gate and artifact binding: `blocking-terminal-gate-failed`,
  `child-owner-binding-mismatch`, `child-workspace-mismatch`, `conflicting-terminal-evidence-binding`,
  `invalid-blocking-terminal-gate-contract`, `invalid-gate-contract`,
  `invalid-terminal-evidence-binding`, `invalid-terminal-receipt`,
  `native-terminal-contract-amendment-required`, `stale-terminal-capture`, `stale-terminal-evidence`,
  `terminal-artifact-bytes-mismatch`, `terminal-artifact-changed-during-gate`,
  `terminal-artifact-contract-mismatch`, `terminal-artifact-path-mismatch`,
  `terminal-capture-binding-missing`, `terminal-evidence-changed-during-gate`,
  `terminal-evidence-missing`, `terminal-gate-artifact-binding-mismatch`,
  `terminal-produced-evidence-mismatch`, `terminal-reference-not-bound`,
  `unreviewed-terminal-gate-command`, `unsupported-blocking-terminal-gate`,
  `unsupported-terminal-gate-prerequisite`.
- Missing/revoked terminal approval (section 9): `native-policy-unavailable`,
  `native-policy-revoked-or-changed`, `host-native-policies-required`, `cs2-source-approval-required`,
  `cs2-independent-critical-checks-required`, `pixi-capture-policy-revoked`,
  `terminal-native-capture-authority-unavailable`, `terminal-authenticated-quality-review-unavailable`.
  These name absent or invalid prerequisites, not an unconditional ban on an entire owner.
- Pixi quality review binding: `invalid-pixi-quality-link`, `invalid-pixi-quality-policy`,
  `pixi-quality-ambiguous-image`, `pixi-quality-baseline-missing`,
  `pixi-quality-behavior-captures-missing`, `pixi-quality-critical-crops-missing`,
  `pixi-quality-edit-captures-missing`, `pixi-quality-evidence-mismatch`,
  `pixi-quality-required-checks-mismatch`, `pixi-quality-requirements-mismatch`,
  `pixi-quality-review-not-authorized`, `pixi-quality-subject-mismatch`,
  `pixi-semantic-quality-failed`, `pixi2d-quality-policy-changed`, `stale-pixi-quality-link`,
  `stale-pixi-user-requirements`.

**4. `forge/compose.py` output** (a separate command; it prints `reasonCode` and `field`, and every
result carries `deliverable: false`). Composition refusals (`CompositionError`, exit 3):
`ambiguous-entrypoint`, `child-light-rig`, `container-kind-mismatch`, `decoder-unavailable`,
`duplicate-artifact`, `duplicate-container`, `duplicate-object`, `duplicate-routed-artifact`,
`duplicate-routed-object`, `evidence-changed`, `external-glb-resource`, `hash-mismatch`,
`invalid-camera`, `invalid-decider`, `invalid-environment`, `invalid-fallback`, `invalid-glb`,
`invalid-inventory`, `invalid-json`, `invalid-metric-anchor`, `invalid-record`, `invalid-reference`,
`invalid-session`, `invalid-transform`, `inventory-mismatch`, `missing-evidence`,
`missing-gltf-scene`, `multiple-containers`, `output-exists`, `output-overlaps-input`,
`ownership-mismatch`, `scale-outside-prior`, `source-consumer-unavailable`, `stale-placement`,
`stale-placement-answer`, `stale-placement-evidence`, `unsafe-output-path`, `unsafe-path`,
`unsupported-camera`, `unsupported-environment`, `unsupported-output-kind`. Input and gate
refusals added by the command include `unreadable-input`, `input-too-large`, `invalid-record`,
`stale-scene-module`, `stale-scene-input`, `stale-scene-measurement`, and
`stale-reference-measurement`. Hash checks do not authenticate the supplied manifest or producer.
Invalid D18 artifacts are reported as JSON refusals using their `ArtifactError` reason code.

## 11. Known gaps and contract mismatches

State these when they matter; do not paper over them.

- Terminal completion requires the explicit per-owner approvals and real evidence in section 9;
  missing approval blocks instead of auto-approving. Implemented producers are not themselves live
  qualification, held-out quality or a release verdict.
- Strategy/review/repair commands and lifecycle persistence now exist (sections 2 and 8). The host
  must still authenticate producer, provenance and permission facts separately from candidate data.
- The router-to-composer structural join and instance ownership checks do not establish end-to-end
  scene delivery. Depth/support/mask provenance and independent composed-scene acceptance remain
  parent responsibilities even when the placement/measurement commands run.
- Composition treats `capability-unavailable` as a group-scoped fallback reason, not a run-wide
  outage. Run-wide outage names follow the controller; receipts still require independent authority.
- `base:generic` children may emit a TypeScript factory; composition needs a GLB and refuses
  source-only artifacts.
- `best-of-n` and `compare-plans` outcomes are recorded but never executed by `forge/route.py`.
- One reference image cannot prove hidden sides or that no object was omitted.
- Interior routing covers one enclosed room; the pinned 0.3.1 adapter excludes the unreleased 0.4
  house pipeline.

## 12. Where to check a claim

- Routing and decomposition: `forge/tests/test_stage0_route.py`, `forge/tests/test_route_cli.py`,
  `forge/tests/test_route_eligibility.py`, `forge/tests/test_route_round_journal.py`.
- Controller, consent, budgets, outage and fallback gating: `forge/tests/test_decision_control.py`,
  `forge/tests/test_decision_recovery.py`.
- Parent, children and terminal authority: `forge/tests/test_route_parent.py`,
  `forge/tests/test_route_authority.py`, `forge/tests/test_cross_route_contracts.py`.
- Artifacts and composition: `forge/tests/test_route_artifact.py`, `forge/tests/test_scene_composer.py`,
  `forge/tests/test_compose_cli.py`.
- Groups 2 to 4: `forge/tests/test_strategy_decision.py`, `forge/tests/test_native_strategy_decision.py`,
  `forge/tests/test_quality_decision.py`, `forge/tests/test_repair_decision.py`,
  `forge/tests/test_workflow_decisions.py`, `forge/tests/test_omp_model_fallback.py`,
  `forge/tests/test_best_of_n.py`.

These are fixture tests. A passing run proves the protocol and the fail-closed paths, not real-image
quality, live provider behaviour, Blender export parity or a deliverable.
