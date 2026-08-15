# Adaptive harsh critic

Use this gate after the fixed turntable gate when a model must survive hostile off-axis review, not
just look plausible from a presentation camera. It inspects the **real browser Three.js scene**. It
does not review a slide, a scene-description JSON, generated prose, or a Python substitute render.

## Division of labour

- `adaptive_harsh_critic.py` is a deterministic controller. It creates view cells, hashes captures,
  validates critic identity/evidence, subdivides defective directions, and enforces stop policies.
- `render_bridge.py` schedules those views into the existing browser manifest. Chrome DevTools MCP,
  the optional Playwright adapter, or another contract-compatible browser path captures the pixels.
  Adaptive hard-gate evidence currently requires the bundled Playwright adapter receipt; the generic
  `render_bridge.py record` command is intentionally unable to mint one.
- A **separate host agent** is the critic. The controller never calls an external model. The host
  creates the critic agent and gives it the emitted request plus readable capture images.
- The scene creator applies corrections **between sessions**. One adaptive session is an immutable
  observation of one scene build; changing code or the build digest mid-session is rejected. Start
  a new render manifest + critic state after a correction. The critic must never be the creator
  under a renamed role: `critic.id == creator.id` is a validation error.

Each `init` creates a random `ahc-<20 hex>` session. Serialized view IDs and capture paths include
that session, so an identical manifest/creator/policy invocation cannot reuse names from an old run.

## View sphere

Round 0 is a six-cell cube map: front, right, rear, left, top, bottom. Those face centres are only the
root of the sphere, not enough evidence to pass. The default `minimumUniformLevel=1` subdivides **all
six** roots and requires another 24 real captures, even when every root centre was clean. Therefore a
default clean run needs at least 6 + 24 = 30 independently reviewed views before it can pass. This
fail-closed floor catches defects near cube-face edges/corners that six centre cameras miss.

Front/right/rear/left also have to pass `turntable_gate.py`; adaptive review is an escalation above
that fixed baseline, not a replacement for it.

Every view is a cube-map cell with `[uMin,uMax] × [vMin,vMax]`. Until `minimumUniformLevel` is met,
every frontier cell is split uniformly. After that floor, only cells with observable defects split
into four children and emit their centre directions as `nextViews`. Repeating defect-directed splits
has no fixed angular-resolution ceiling: a direction can be refined again and again. Actual runs are
deliberately finite.

## Bounded termination

The first applicable stop wins:

1. no findings after the complete current frontier reaches `minimumUniformLevel`: `passed`;
2. the same stable `defectKey` survives the configured consecutive rounds: `repeated-defect`;
3. worst severity and finding count fail to improve for the configured window: `plateau`;
4. completing another round would exceed `maxRounds`: `max-rounds`;
5. scheduling a complete four-child subdivision would exceed `maxViews`: `max-views`.

If either cap prevents the required uniform floor, the more explicit stop is
`max-rounds-before-minimum-coverage` or `max-views-before-minimum-coverage`. A cap is never treated as
permission to pass with incomplete coverage.

The max-view gate refuses a biased partial subdivision. It does not inspect the first two children
and pretend the other two directions never existed. An unreadable capture is `unreviewable-evidence`
and routes to `request-input`.

## Critical means AND, never average

The response has no global acceptance score. Every finding is `minor`, `major`, or `critical`; any
finding prevents a pass, and one `critical` finding is a blocking failure for the whole round. A
critical rear defect cannot be diluted by five clean sides, a high beauty score, or a large number of
minor passes. The round record persists `criticalCount` and `criticalRulePassed` explicitly.

## Pixel binding

The `request` command only succeeds after every `nextView` is recorded by the browser bridge. It
re-opens each PNG and verifies the manifest hash. The critic must echo these fields on every review:

- `viewId`
- `captureSha256`
- exact unit `direction: [x,y,z]`

Every individual finding must repeat the same three fields. The `advance` command re-opens and hashes
the PNG again. A review of JSON prose with no real capture, a stale screenshot, a swapped camera, or a
finding detached from its pixels is rejected.

The machine-readable state/request/response schema is
`docs/specs/adaptive-harsh-critic.v1.schema.json`.

### Browser provenance and collapse rejection

An adaptive capture is not `recorded` merely because a PNG exists. The Playwright adapter must load
the manifest's exact runtime URL and observe all of the following in the live page:

- `window.__IMG2THREEJS_READY__ === true` as a strict boolean;
- a non-zero WebGL canvas captured directly with the `canvas` locator;
- `window.__IMG2THREEJS_CAPTURE__.setCamera(...)`;
- `window.__IMG2THREEJS_CAPTURE__.getEvidenceSnapshot({captureId, sessionNonce})`.

`getEvidenceSnapshot` must return the same `captureId` and unpredictable adapter `sessionNonce`, a
stable 64-hex `sceneBuildSha256`, positive `objectCount`, and the actual camera `direction`,
`matrixWorld`, and `projectionMatrix`. The controller checks that the matrixWorld camera back axis
encodes the scheduled direction. The adapter also records browser/version/headless state, exact
document URL and SHA-256, ready/capture-contract results, canvas/drawing-buffer dimensions, encoded
PNG SHA-256, and a decoded-RGBA pixel SHA-256. This canonical receipt is itself hashed.

The receipt is a **trusted local-runner attestation**, not a cryptographic proof or remote
attestation. Its security boundary assumes the bundled Playwright adapter and runtime contract are
the trusted evidence-producing path. A caller that can replace the adapter/runtime/code or rewrite
all state can forge the world it reports. The ordinary `render_bridge.py record` CLI still cannot
mint this receipt from a PNG or a self-reported ready string.

Different directions in one adaptive session may not reuse the same decoded pixels, a perceptually
near-identical frame, or the same camera matrix. The near-collapse gate combines alpha-composited
low-frequency visible RGB with pHash, mean visible color, and brightness-shift-invariant demeaned
luma structure. The RGB/color guards preserve genuine isoluminant color changes while ignoring
hidden RGB under fully transparent pixels. Re-encoding one PNG, changing one pixel, or adding a
global or structured ±1/2/3-LSB noise veil does not help. A perfectly
symmetrical subject that genuinely produces near-identical pixels therefore blocks for human input
rather than silently passing; visibly different views, including substantial global color changes,
remain outside the visible-RGB collapse envelope.

### Immutable issued request

`request` canonicalizes the **complete** request except the self-referential `requestId` and
`requestDigest`, computes SHA-256, derives `requestId` from that digest, and atomically pins the
digest, manifest hash, round, and ordered view IDs in `state.pendingRequest`. It also writes the
updated state file. `advance` recomputes the digest before reading the response. Changing any scene,
baseline, capture path/hash/pixels/provenance, direction, cell, round, policy contract, or view order
is rejected even when the response is changed to agree with the forged request. Only a successful
advance consumes and clears `pendingRequest`; invalid responses leave it pending for a corrected
retry, and a second request cannot be issued over it.

The first accepted capture set also pins `sceneBuildSha256` in `state.scene`. Every later round must
match it. `state.evidenceLedger` and each completed round retain every scheduled view's capture path,
encoded/decoded hash, browser-receipt hash, scene-build hash, round, direction, and optional per-view
reference capture path/hash. Before issuing a new request the controller re-opens the full historical
ledger and rejects missing or changed evidence; adding new captures to the manifest does not erase
the previous proof trail. The original reference image/GLB file is also re-opened and checked against
the pinned `reference.sha256` at request and advance. For a GLB session, every adaptive procedural
view must have a clean, recorded browser reference capture; reference evidence is not optional.

Request construction is transactional for direct Python callers: scene/ledger/pending changes are
committed to the supplied state only after the fixed baseline and every history gate succeeds.

## End-to-end commands

Initialize after `render_bridge.py init` has created a scene manifest:

```bash
python3 forge/stage4_review/adaptive_harsh_critic.py init \
  --manifest work/render-manifest.json \
  --creator-id builder-agent \
  --minimum-uniform-level 1 \
  --out work/harsh-critic-state.json
```

Schedule the current `nextViews` into the real scene and capture only those pending IDs (the generic
Playwright adapter already iterates arbitrary manifest captures; a Chrome MCP adapter uses the same
camera records):

```bash
python3 forge/stage4_review/render_bridge.py schedule-adaptive \
  --manifest work/render-manifest.json \
  --plan work/harsh-critic-state.json

python3 scripts/capture_threejs_playwright.py \
  --manifest work/render-manifest.json \
  --capture-id ahc-<session>-harsh-front-root \
  --capture-id ahc-<session>-harsh-right-root
```

Read the complete generated IDs from `state.nextViews`; do not strip the session prefix. Omitting
`--capture-id` captures every pending manifest entry.

Scheduling first validates the complete state and canonical cube-cell grammar: the random session,
view ID, face/path/level/round, bounds, direction, angles, radius, and parent must all agree.
`--output-dir` must be a canonical relative path. Every generated capture, reference, and diagnostic
pass path is resolved and proven to remain below the render manifest's evidence root before the
manifest is atomically updated; absolute paths, drives, backslashes, empty/dot components, and `..`
are rejected.

After the first clean response, `nextViews` contains 24 level-1 IDs. Schedule and capture all of
them; six clean root views alone must not produce `passed`.

Package immutable evidence for an independent agent:

```bash
python3 forge/stage4_review/adaptive_harsh_critic.py request \
  --state work/harsh-critic-state.json \
  --out work/critic-request-r0.json
```

This command updates both files: the request is created and its canonical digest is pinned into the
existing state. Do not restore an older state after issuing the request.

The host creates a separate critic and has it inspect every image in `views[]` at native resolution.
Its response follows this shape; every finding repeats the real pixel and direction binding:

```json
{
  "kind": "img2threejs.adaptive-harsh-critic-response",
  "schemaVersion": 1,
  "requestId": "ahcr-...",
  "critic": {
    "id": "critic-agent",
    "role": "independent-harsh-critic",
    "acknowledgements": {
      "inspectedPixels": true,
      "noScoreAveraging": true,
      "criticalDefectsAreBlocking": true
    }
  },
  "views": [
    {
      "viewId": "ahc-<session>-harsh-rear-root",
      "captureSha256": "<copied from request>",
      "direction": [0, 0, -1],
      "verdict": "defect",
      "findings": [
        {
          "defectKey": "rear-skull-through-hole",
          "severity": "critical",
          "category": "geometry",
          "description": "Background is visible through the rear skull volume.",
          "viewId": "ahc-<session>-harsh-rear-root",
          "captureSha256": "<same capture hash>",
          "direction": [0, 0, -1]
        }
      ]
    }
  ]
}
```

The actual response must contain one review for **every** requested view. Advance exactly one round:

```bash
python3 forge/stage4_review/adaptive_harsh_critic.py advance \
  --state work/harsh-critic-state.json \
  --request work/critic-request-r0.json \
  --reviews work/critic-response-r0.json \
  --in-place
```

`advance` intentionally supports only `--in-place`. There is one canonical state transition and no
second `--out` target whose write could fail after the request was already consumed. Copy a completed
state only after the command succeeds if an archival snapshot is needed.

If status is `needs-render`, keep the same scene build and repeat schedule → browser capture →
request → independent critique → advance. If status is `blocked`, preserve that manifest as
evidence, correct the scene, and initialize a new manifest + critic state; do not mix old and new
scene builds or weaken the critic policy.

## What the gate proves and does not prove

It enforces that the declared creator/critic identities differ, the issued request did not change after
the state pinned it, reviewed pixels carry the trusted local Playwright/WebGL runner attestation and
are hash-bound to captured camera matrices/directions, repeated pixels did not collapse distinct
scheduled views, the fixed turntable baseline ran, defects cannot be averaged away, and recursive
work terminates. The receipt is not cryptographic proof. It cannot cryptographically prove that two
IDs are two humans or models, nor defend
against an attacker who can rewrite both code and every state/manifest file. It does not infer hidden
ground truth that no reference shows. Host orchestration must actually create a fresh agent context,
and hidden-side claims must remain labelled as self-consistency checks or inference.
