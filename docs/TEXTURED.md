# Textured build path

img2threejs turns a reference image into a **textured, realistic 3D model (GLB)**. An image-to-3D
generator makes the mesh and baked PBR textures; Blender (bpy) inspects, normalizes and renders it;
a Jev likeness loop measures the render against the reference and decides accept or regenerate. The
model is generated, not code-built. The procedural pipeline remains available as an explicit opt-in
(`--profile procedural-generic`, `procedural-character`, `procedural-<domain>`).

This path is implemented from the manual vesper run (final mean Jev score 2.45/4 against 1.31 for
the game's own model). That run is evidence for the loop, not a guarantee for other references.

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
| decide | script | `decide.py`: SAME accepts; DIFFERENT regenerates; SIMILAR accepts at mean >= 2.4 with no flagged defect, else regenerates; cap reached (default 4 generations) accepts the best generation, Jev scores outranking model-answered ones |
| deliver | script | `.img2threejs/textured/final/`: `model.glb`, renders, `comparison.png`, receipts, `REPORT.md` |

A regeneration must change model, scenario, prompt or input image: `generate.py` refuses a request
identical to an earlier generation. `decide` can only be recorded by `decide.py`, never `state.py mark`.
`next.py --state` prints the checklist, the generation counter and the exact next command; a textured
state has no sculpt spec and `next.py` refuses one.

Every tool writes a JSON receipt (`inspect.json`, `normalize.json`, `render.json`, `sheet-*.json`,
`likeness.json`, `generate.json`) under `.img2threejs/textured/gen-N/`, plus a run journal
(`journal.jsonl`) and Jev token log (`jev-tokens.jsonl`) under `.img2threejs/textured/`.

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
4. `decision_session` reserves 64,000 tokens before the send and accounts the provider's usage after it;
   an exhausted budget sends nothing; an identical request is never paid for twice;
5. usage is appended to `jev-tokens.jsonl`; `likeness.json` records `decider: jev-1.14.0`.

`likeness.py pick --gens 1,2,3` is the head-to-head candidate pick through the same gate.

### No-account mode

With no Jev account or no consent, the host model answers the same typed questions:
`likeness.py questions` writes the set; the model looks at `sheet-front.png`;
`likeness.py record-model-answers --answers a.json --model-id <id>` validates and records them as
`decider: model:<id>`, `presentedAsJev: false`, with a note. These scores are the model's own opinion,
not independent measurement, are not comparable with Jev scores, and `REPORT.md` says so.

## Not done

No rig, skin weights or animation (a domain's rig track runs afterwards: `state.py init --profile textured --domain animated-character` appends the installed character plugin's nine rig rows after `deliver`; the in-repo `character` domain has no rig rows, so `--domain character` adds none and says so); no
retopology or LOD; back and sides are invented by the generator; Jev is noisy (differences below
about 0.15 are noise) and advisory, never acceptance authority; the procedural visual gate in
`CLAUDE.md` does not apply to the textured path.
