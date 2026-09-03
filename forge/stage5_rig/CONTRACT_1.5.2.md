# Stage R — 1.5.2 implementation contract

The spec is `docs/pipelines/character-rigging-animation-1.5.2.md`. This file fixes the
**module boundaries and payload shapes** so the R-stage modules compose without importing
three.js and without a renderer. Every module here is pure Python 3.10+ stdlib.

Everything is expressed as a fraction of **figure height H**. A payload carries `H`
explicitly; no module may assume `H == 1.0`.

## Sampled-clip payload (the only animation input format)

Produced by a host that can evaluate a rig (three.js, Blender, a test fixture). Positions
are **world** space, already divided by nothing — modules normalise by `H` themselves.

```json
{
  "figureHeight": 1.0,
  "landmarks": ["hip", "head", "hand.l", "hand.r", "foot.l", "foot.r"],
  "clips": [
    {
      "sourceName": "NlaTrack.003",
      "duration": 2.833,
      "sampleTimes": [0.0, "... N = 25 evenly spaced ..."],
      "landmarkPositions": {
        "hip": [[x, y, z], "... N entries ..."],
        "head": [[x, y, z], "..."]
      },
      "jointScaleDelta": [0.0, "... N entries, max |scale_j(t) - 1| over joints ..."],
      "poseReturn": 0.0,
      "stance": {"foot.l": [[t0, t1]], "foot.r": [[t0, t1]]}
    }
  ]
}
```

- `sampleTimes` length == every `landmarkPositions[*]` length == `jointScaleDelta` length.
- `poseReturn` is degrees: max per-joint rotation delta between `t = 0` and `t = duration`.
  A host that cannot measure it omits the key; modules must then report `loop: null`
  ("undecidable"), never guess `false`.
- `stance` is optional; only Gate G8 needs it. Intervals are in seconds.

## Module map — one owner per file, no cross-writes

| File | Owns | Public API |
|---|---|---|
| `clip_features.py` | §1 vocabulary, §2 classifier, §3 naming, §4 loop rule | `measure_clip`, `ClipFeatures`, `classify`, `decide_loop`, `name_clip`, `ClipName`, `load_payload` |
| `skin_conditioning.py` | §R2 proximity weight blending + Gate R2 | `blend_weights`, `SkinBinding`, `BlendReport`, `validate_binding` |
| `action_design.py` | §R4 targets, primitives, chain resolution, footSlide | `walk_targets`, `run_targets`, `TargetBand`, `accepts`, `resolve_chains`, `foot_slide` |
| `rig_gates.py` | §R6 G1–G10 runner | `run_gates`, `GateResult`, `GateReport` |
| `emit_animation_runtime.py` | §R5 controller/ticker TS emitter | `emit_animation_runtime` |

`rig_gates.py` imports from `clip_features`, `skin_conditioning` and `action_design`.
Nothing imports from `rig_gates`. No other file in `forge/` is edited by these modules.

## Shared numeric conventions

- Tolerances are module constants with the spec's name: `BINDING_EPSILON = 2 ** -23`,
  `WEIGHT_SUM_TOLERANCE = 2e-7`, `BIND_RESTORE_TOLERANCE = 1e-12`, `FOOT_SLIDE_LIMIT = 0.01`,
  `POSE_RETURN_DEGREES = 0.5`, `LOOP_HIP_TOLERANCE = 0.01`, `BLEND_RADIUS = 0.006`.
- Every threshold carrying `single-subject` in the spec is exported as a **named default
  that a caller can override**, and the dataclass records the value actually used.
- A module never prints. CLIs (`if __name__ == "__main__"`) write JSON to stdout and use
  exit code 1 on gate failure, matching `validate_rig_payload.py`.

## Honesty rules that are load-bearing

- `classify` returns **all** matching labels (a clip can be `in-place` + `planted` +
  `gesture`), plus a primary motion class. It never invents a class for an empty match.
- `name_clip` sets `inferred: true` whenever the label wording implies intent, and keeps
  `sourceName` verbatim.
- Any gate that cannot be evaluated because its input is absent reports
  `status: "unevaluated"` with a reason. It is never silently a pass.
