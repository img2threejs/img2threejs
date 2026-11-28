# Textured build path

img2threejs turns a reference image into a **textured, realistic 3D model (GLB)**. An image-to-3D
generator makes the mesh and baked PBR textures; Blender (bpy) inspects, normalizes and renders it;
a Jev likeness loop measures the render against the reference and decides accept or regenerate. The
model is generated, not code-built. The procedural pipeline remains available as an explicit opt-in
(`--profile procedural-generic`, `procedural-character`, `procedural-<domain>`).

Manual Vesper asset inspection/normalization/rendering observations motivate these tools; they do not
qualify automatic generation, routing, native child completion or the full self-correction loop.

## Requirements

| Need | Why | Without it |
|---|---|---|
| A Tripo MCP tool in the host (`mcp__tripo_make`), account and credits | the generator; paid per generation (65 to 120 credits in the vesper run) | `state.py init` falls back to procedural `generic` and says so; `IMG2_TEXTURED_GENERATOR=off` forces that |
| A bpy runtime: `img2 provision`, or `IMG2_CHARACTER_BPY_PYTHON` / `IMG2_BLENDER_PYTHON` | inspect, normalize, render, sheet | the tool exits 2 naming both variables; nothing is installed |
| Jev login (`img2-jev login`) and per-run consent | likeness scores, defect checks, candidate pick | no-account mode below |

Presence of a Tripo MCP server is detected from host configuration (`~/.omp/agent/mcp.json`,
`~/.claude.json`, `~/.codex/config.toml`, ...) or `IMG2_TEXTURED_GENERATOR`. It is not proof of an
account or credits.

## Checklist rows (profile `textured`)

`reference-admission` → `generate` → `inspect` → `normalize` → `render` → `likeness` → `decide` → `deliver`

| Row | Who | What |
|---|---|---|
| reference-admission | script | `check_reference_admission.py` on the reference |
| generate | **agent** | call the generator MCP tool (image path, scenario `film`/`game-pc`/..., model `tripo-v3.1`/`tripo-p1`/`tripo-p2`, optional chain), then `forge/textured/generate.py record` stores the raw model under `.img2threejs/textured/gen-N/raw/` with task id, credits, model, scenario, prompt and the input sha256 |
| inspect | script (bpy) | facts: triangles, vertices, meshes, materials, textures and sizes, bbox, islands, non-manifold edges (raw and welded), skeleton |
| normalize | script (bpy) | +Z up, facing -Y, target height (`--height`, default 1.8), feet at the origin, XY centred, speck islands removed, textures kept, GLB export |
| render | script (bpy) | front, left34, right34, left_side, back and optional `--closeup head|torso|feet`; Cycles CPU with denoise (or `--engine eevee`); default 1024x1536; neutral studio light |
| likeness | script + Jev | `sheet.py` builds `[reference | render]` within the Jev image limits (each side 280..1568 px); `likeness.py run` asks Jev |
| decide | script | `decide.py`: SAME accepts; DIFFERENT regenerates; SIMILAR accepts at mean >= 2.4 with no flagged defect, else regenerates; cap reached (`textured.maxGenerations`, 1..8, default 4) accepts the best generation, Jev scores outranking model-answered ones |
| deliver | script | `.img2threejs/textured/final/`: `model.glb`, renders, `comparison.png`, receipts, `REPORT.md` |

A regeneration must change model, scenario, prompt or input image. Every attempt, including every
superseded overwrite, consumes the original cap bound to canonical `.img2threejs/state.json`.
Historical signatures cannot be reused and changing the state cannot raise the original cap.
`decide` is recorded only by `decide.py`, never `state.py mark`; explicit `deliver --gen` must match
its accepted generation and fresh source/receipt bindings. Legacy textured receipts lacking cap
or hash authority fail closed for owner reconciliation, not silent promotion.

Receipts live under `.img2threejs/textured/gen-N/`. Likeness is immutable and binds the reference,
raw asset tree, inspection, normalization, normalized GLB, render bytes and comparison sheet.
Both decision and delivery revalidate that chain. Delivery stages all confined, collision-free
copies and its report before activating the replacement; preflight/copy/report/activation failure
preserves the previous final. The loop retains `journal.jsonl`, `jev-tokens.jsonl` and sanitized
`jev-transport.jsonl` facts under `.img2threejs/textured/`.

## Likeness

Question set (`likeness.py`), by object kind (`character`, `creature`, `vehicle`, `prop`, `furniture`,
`building`, `generic`; add features with `--feature id="what to rate"`):

- one `choice` `overall`: `same` / `similar` / `different`;
- one 0..4 `score:<feature>` per feature (for `character`: silhouette and proportions, costume colours
  and layers, face/head, hair, hands, held props and accessories, footwear/legwear, texture realism);
- `noul:<defect>` P(defect) checks (extra or missing parts, melted geometry, wrong hands, floating
  parts, unintended text).

### Jev path

The installed Jev plugin's typed `decision-quality-review` edge cannot carry this set: it builds its own
fixed questions (seven fixed score axes, pass/fail/uncertain feature choices) and accepts neither a
same/similar/different verdict, nor defect probabilities, nor caller-authored instructions. So
`likeness.py run` uses the same two plugin helpers the manual run used (`jev_auth` for the key and
`jev_decision_transport` for the wire), but **only behind the plugin's own per-run decision consent
and durable budget ledger**:

1. the Jev plugin files imported are verified against `forge/_shared/jev_release_lock.json`;
2. a credential must be present (`img2-jev login`); the key is read only inside the plugin call and is never
   stored, logged or put in a receipt;
3. `jev.py consent decision-status --workspace W --run-id R` must report a live grant that covers the
   `quality-review` purpose, with a token budget (grant it yourself:
   `jev.py consent authorize-decisions --workspace W --run-id R --token-budget N --accept-image-disclosure --accept-paid-calls`);
4. `decision_session` reserves 64,000 tokens before sending; invalid or missing usage retains that
   full reservation and is never retried automatically. Identical requests are never resent.
5. valid usage is accounted and logged before semantic answer validation; malformed answers cannot
   erase known cost or create a likeness. `jev-transport.jsonl` retains hashes/outcomes, never raw replies or keys.

`likeness.py pick --gens 1,2,3` is the head-to-head candidate pick through the same gate.

### No-account mode

Only a user with **no Jev account** may enable this path; declined consent, uncertainty or a valid Jev
verdict never authorize model replacement. Personally attest the `quality-review` purpose under a
live v2 grant, then inspect the typed questions and bound sheet:

```bash
python3 forge/route.py attest-no-jev-account --workspace WS --run-id RUN --image ref.png \
  --model-id provider:model --token-budget 64000 --purpose quality-review --accept-image-disclosure
python3 forge/textured/likeness.py questions --workspace WS --gen 1 --kind generic
python3 forge/textured/likeness.py record-model-answers --workspace WS --gen 1 --kind generic \
  --answers answers.json --model-id provider:model --run-id RUN --reference ref.png
```

`--answers` and `--reference` are workspace-relative (or absolute within it). Record raw answers to exactly those questions. Run, generation, original reference, complete pipeline
hashes, live grant and named model must agree; record/decide/delivery recheck authority. V1 grants
require personal re-attestation and are retained. No-account status must be proven by pinned auth
tooling; changing `IMG2_HOME` or omitting credentials cannot grant authority.
Receipts say `model:<id>` / `presentedAsJev: false`; scores are host-model opinion, not independent
measurement or comparable Jev scores. Core makes no model call and cannot measure host token usage
or authenticate the user's model-identity claim. Existing judged receipts cannot be overwritten,
including by model answers after Jev. `REPORT.md` states the decider and these limits.

## Not done

No rig, skin weights or animation (a domain's rig track runs afterwards: `state.py init --profile textured --domain animated-character` appends the installed character plugin's nine rig rows after `deliver`; the in-repo `character` domain has no rig rows, so `--domain character` adds none and says so); no
retopology or LOD; back and sides are invented by the generator; Jev is noisy (differences below
about 0.15 are noise) and advisory, never acceptance authority. Selecting a profile does not waive
repository screenshot/visual gates; the code-only versus textured-default conflict remains an owner release decision.
