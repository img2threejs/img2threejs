# Stage R test plan — does the animation pipeline still break meshes?

A runbook for img2threejs 1.5.2, written to **falsify** the fix rather than confirm it.

**Status of what it tests:** the Stage R modules are unit-tested (1343 tests, `OK (skipped=30)`) and
several were exercised on real data — `glb_rig_reference.py` on a real GLB, `mesh_parity` through a
live browser round-trip. But **the pipeline has never been run end-to-end on a real character, and
nothing has been rendered.** The three mesh-damage mechanisms behind this work were inferred by
reading code, not by reproducing the failure.

That is why Phase 0 exists, and why it is not optional.

---

## The one rule

> **Phase 0 is the phase that makes every other phase mean something.**

Nobody has yet seen the broken mesh, and nobody has seen it fixed. If you skip to Phase 3 and
everything passes, that result is unfalsifiable — a green run proves the gates agree with each
other, not that your mesh stopped tearing. Capture the damage first.

---

## How to read a result

| Status | Meaning |
|---|---|
| `pass` | The check ran, on real data, and the value was inside tolerance. |
| `fail` | The check ran and the value was outside tolerance. A real defect, or a real change in the input. |
| `unevaluated` | **Nothing measured it. This is not a pass.** It makes the whole report not-ok by design. |

---

## Before you start — four gates have no producer

`rig_gates.py` **will report not-ok on any run today.** That is the system telling the truth, not a
test failure. Judge the run on the other eight gates plus the mesh-parity verdict.

| Gate | Catches | Input producer |
|---|---|---|
| `G1` | clips that play silently | **none yet** — needs `runGateR1()` exposed via a CLI |
| `G2` | bad indices, NaN weights | host harness |
| `G3` | pose bleed between clips | **none yet** |
| `G4` `G5` | weight sums, index range | `export_mesh_buffers.mjs` |
| `G6` | a part left in bind pose | **none yet** |
| `G7` | mirrored rig | computed in Python |
| `G8` `G9` | skating gait, joint scale | `glb_rig_reference.py --sample-clips` |
| `G10` | tearing under motion | **none yet** — needs a 176-frame sweep |
| `G11` | **rigging that rewrites geometry** | `export_mesh_buffers.mjs` |
| `G12` | clips addressing a different skeleton | `glb_rig_reference.py` |

Set `I=~/Documents/personal/img2threejs` and run everything from that directory. **Keep every JSON
and every recording** — the artifacts are the result, not the terminal output.

---

## Phase 0 — Reproduce the damage

**~30 min. The phase everyone skips. Do not skip it.**

**Goal:** a saved, dated record of a mesh actually breaking. Without it, "fixed" cannot be tested,
only asserted.

### 0.1 — Name the broken subject

Pick the demo where you saw the tearing. Write down which one, and what you saw.

> **Two opposite symptoms, two different causes.** "Nothing moves" is a binding failure (`G1`:
> the clip exists, holds an action, reports a duration, and drives nothing). "Parts fly apart" is
> geometry being rewritten (`G11`). If you saw both, say so — the 1.5.1 build had two separate bugs
> and each produced a plausible-looking scene.

### 0.2 — Capture it on video, at three angles

Play the clip that breaks it. Record front, 40°, and a grazing angle.

Metrics hide structural failure: a surface can score 2.49 mm median accuracy and still read as a
lumpy, hole-riddled shell. **If the render and the metric disagree, the render is right.**

### 0.3 — Freeze the broken geometry as evidence

```bash
node $I/runtime/scripts/export_mesh_buffers.mjs \
  --url <preview-url> --out baseline-broken.json

python3 $I/forge/stage5_rig/mesh_parity.py freeze baseline-broken.json \
  --out baseline-manifest.json
```

**Keep:** `baseline-broken.json`, `baseline-manifest.json`, the three recordings. This is the
"before" that everything else is measured against.

---

## Phase 1 — Smoke the modules alone

**~10 min. No browser. Catches wiring errors before they cost you an hour.**

**Goal:** prove each module runs on real input *in your environment*. All of these were run during
development; re-running them proves your checkout matches.

### 1.1 — The suite

```bash
cd $I && python3 -m unittest discover -s forge/tests -p 'test_*.py'
```

- **Expect:** `Ran 1343 tests` · `OK (skipped=30)`
- **If not:** stop. Something in the checkout differs from what was verified. Report the failure
  names before going further.

### 1.2 — The honesty rule, on an empty payload

```bash
echo '{"figureHeight":1.0}' > empty.json
python3 $I/forge/stage5_rig/rig_gates.py empty.json; echo "exit=$?"
```

- **Expect:** 12 gates, every one `unevaluated`, `ok: false`, `exit=1`.
- No gate may say `pass` — nothing was measured.

### 1.3 — Read a real rig out of a real GLB

```bash
python3 $I/forge/stage5_rig/glb_rig_reference.py \
  ~/Documents/personal/img2threejs-showcase/public/mesh/tripo.glb \
  --out glb-rig.json
```

- **Expect:** `skinCount: 1`, `clipCount: 11`, 42 joints, and `joints[0]` pointing at
  `nodeIndex 40` — **not** 0. That gap is the whole point: the ordering is the skin's, never the
  traversal's.
- **Check `deformVsTechnical.channelsTargetingTechnicalNodes`.** On this asset it is 0. A non-zero
  number on *your* asset means clips drive nodes that are not joints — read it before binding
  anything, because building those as bones corrupts the skeleton's index space.

### 1.4 — The mesh-parity gate, all three outcomes

This is the gate that answers the original complaint. Prove it can say yes **and** prove it can say
no — a gate that only ever passes is worthless.

```bash
# a: unchanged → pass
python3 $I/forge/stage5_rig/mesh_parity.py verify m.json before.json; echo "exit=$?"

# b: skinIndex/skinWeight added by rigging → must ALSO pass
python3 $I/forge/stage5_rig/mesh_parity.py verify m.json after-rig.json; echo "exit=$?"

# c: one vertex nudged by hand → must fail, and say where
python3 $I/forge/stage5_rig/mesh_parity.py verify m.json damaged.json; echo "exit=$?"
```

- **Expect:** a → `exit=0` · b → `exit=0` with skin attributes listed as *added (legal)* ·
  c → `exit=1` naming the mesh, the attribute and the first differing element.
- **If b fails:** that is a design bug, not a data problem. The gate would fail every successful rig
  and be switched off within a day. Report it immediately.

---

## Phase 2 — Does the workflow actually drive to Stage R?

**~5 min. This exact wiring was broken once and shipped green.**

**Goal:** confirm `next.py` asks for the rig steps. The first version of this work put nine steps on
the checklist that no dispatcher ever returned — the build reported `complete` with all nine still
`pending`, and the tests passed because they asserted the *list* rather than the *behaviour*.

### 2.1 — Initialise on the animated profile

```bash
python3 $I/forge/state.py init --state .img2threejs/state.json \
  --reference <your>.glb --profile animated-character \
  --spec object-sculpt-spec.json
```

- **Expect:** 32 checklist steps, 9 of them scope `rig`.
- On `--profile character` you get 0 rig steps and the gates never run. That is the difference the
  profile makes, and picking the wrong one is how animation used to ship broken.

### 2.2 — Walk it

```bash
python3 $I/forge/next.py --state .img2threejs/state.json
```

- **Expect:** the nine rig steps appear in `pending`. Once the static build finishes, `currentStep`
  becomes `rig-contract-read` and status stays `active`.
- **Red flag:** if status goes `complete` while any rig step is `pending`, the dispatch regression is
  back. This is the single most important thing this phase looks for.

---

## Phase 3 — Run the real character through

**The long one. Order is load-bearing.**

**Goal:** take the Phase 0 subject through the full flow. Follow `next.py` at every step; a hard stop
(exit 3) is the system working — answer it rather than pushing past it.

Use `docs/GLB_ANIMATED_CHARACTER_PROMPT.md` as the driver. The order below cannot be rearranged:
**repair before freeze, freeze before bind, verify after bind.**

### 3.1 — Repair, then freeze

Fix whatever is broken in the mesh **now**, and write down what you fixed. Then freeze. Freezing a
broken mesh certifies the breakage instead of catching it.

```bash
node $I/runtime/scripts/export_mesh_buffers.mjs \
  --url <preview> --out meshes.json

python3 $I/forge/stage5_rig/mesh_parity.py freeze meshes.json \
  --out mesh-manifest.json
```

> Use `export_mesh_buffers.mjs`, **not** `export_mesh_geometry.mjs`. The latter multiplies every
> vertex by `matrixWorld` — correct for self-intersection, wrong here in both directions: posing
> changes `matrixWorld` without touching the buffer (false positive), and a real buffer edit
> cancelled by a compensating transform reads as unchanged (false negative).

### 3.2 — Bind, additively only

Rigging may add `skeleton`, `skinIndex` and `skinWeight`. Positions, normals, uvs and indices are
evidence from here on. Three rules, each of which caused a real failure:

- `mesh.bind(skeleton, new THREE.Matrix4())` — identity, always, in attached bind mode. Attached
  mode recomputes `bindMatrixInverse` from `matrixWorld` every frame, so `matrixWorld` cancels out
  entirely. Binding with the armature's matrix rendered a blank frame.
- Display offset comes from the **mesh bounds alone**: `(−centre.x, −min.y, −centre.z)`. Adding the
  armature translation double-counts it — the figure lands offset by exactly that translation.
- `updateMatrixWorld(true)` **before** `new THREE.Skeleton(...)`. `calculateInverses()` reads each
  bone's current world matrix; constructed first it captures identity, the rest pose never cancels,
  and every vertex sits displaced by its bone's offset. That failure compiles, binds, reports
  `bound: true`, and renders a corpse.

### 3.3 — The verdict on the original complaint

```bash
node $I/runtime/scripts/export_mesh_buffers.mjs \
  --url <preview> --out meshes-after.json

python3 $I/forge/stage5_rig/mesh_parity.py verify \
  mesh-manifest.json meshes-after.json; echo "exit=$?"
```

- **Pass:** rigging touched no geometry. Skin attributes appear as *added (legal)*.
- **Fail: the rigging is wrong.** **Never re-freeze to make it pass** — that would certify the
  damage instead of catching it. The failure block names the mesh, the attribute and the vertex;
  keep it verbatim.

### 3.4 — Measure the clips, then run the gates

```bash
python3 $I/forge/stage5_rig/glb_rig_reference.py <glb> \
  --sample-clips --landmarks landmarks.json --out sampled-clips.json

python3 $I/forge/stage5_rig/clip_features.py sampled-clips.json

python3 $I/forge/stage5_rig/rig_gates.py rig-gate-payload.json
```

- **Expect:** not-ok, with `G1 G3 G6 G10` unevaluated. Judge the run on the other eight. **If any of
  those eight *fails*, that is a real finding.**
- **Watch:** `scaleDelta ≠ 0` on any clip is a tripwire, not a descriptor — the source rig scales
  joints, which changes what skin conditioning may legally do. Surface it before anything else
  proceeds.
- Clip names: a name that describes motion is provable; one that implies intent is not. Anything
  carrying `inferred: true` is a guess, and marking it that way is the honest answer rather than a
  placeholder for a better classifier.

---

## Phase 4 — Compare against Phase 0 and decide

**The only phase that answers the question you actually asked.**

Record the same three angles as Phase 0, playing the same clip. Then answer three questions **in
writing**:

1. **Does the mesh still tear?** Yes / no / differently — and if differently, how.
2. **Did a gate catch it before you saw it?** If your eyes found something no gate flagged, that is
   the most valuable result in the whole run: it names a missing gate.
3. **Did a gate flag something your eyes cannot see?** Equally valuable in the other direction —
   either a real defect below the visual threshold, or a gate that is too strict.

> **"Improved" is not "fixed."** If it tears less, say it tears less. Naming what still does not
> match is worth more than a clean verdict.

---

## What to send back

These make the result actionable rather than a guess:

- Phase 0 and Phase 4 recordings — same clip, same three angles.
- `glb-rig.json`, especially `deformVsTechnical` and the joint ordering.
- The `mesh_parity verify` output, pass or fail, verbatim.
- The full `rig_gates` report — the unevaluated gates matter as much as the failures.
- Whatever you repaired in the `mesh-repair` step, one line each.
- The point where you had to deviate from this plan, and why.

---

## Known gaps, so they don't read as bugs

- **Four gates have no producer** (`G1` `G3` `G6` `G10`). They report `unevaluated`, which makes the
  report not-ok by design.
- **Nothing writes `rig-payload.json`** in the shape `validate_rig_payload.py` expects.
  `new_sculpt_spec.py` produces a differently-shaped `spec["rig"]`; no converter exists.
- **`mesh-repair` and `rig-bind` are prose steps** with no script and no verifiable artifact.
  `rig-bind` creates the skeleton; its only downstream check is `mesh-parity-verify`, which proves
  buffers were untouched, **not** that the bind is correct.
- **The GLB fast lane worktree is still on 1.5.1** and has none of the 1.5.2 animation stack. The two
  branches cannot be combined until it is rebased.
- **`single-subject` thresholds** (blend radius `R = 0.006H`, the §2 classifier boundaries) still
  come from one rig. Treat them as starting values, not constants.

**Cheapest next fix** if you want `G1` — the gate that catches clips playing silently, and the only
check that distinguishes a clip that plays from one that exists, loads, holds an action and reports a
duration while driving nothing — is a CLI on `emit_animation_runtime.py`. It already contains the
`runGateR1()` harness that produces the input; nothing exposes it to a shell.

---

## Related

- `docs/GLB_ANIMATED_CHARACTER_PROMPT.md` — the one-pass prompt this plan tests.
- `docs/pipelines/character-rigging-animation-1.5.2.md` — the derivation and the failure log.
- `grimoire/readiness/animation_contract.md` — the routing file read at Stage R.
- `forge/stage5_rig/CONTRACT_1.5.2.md` — module map and payload shapes.
