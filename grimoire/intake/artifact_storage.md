# Reconstruction artifact storage

`.img2threejs/` is the durable evidence root for one reconstruction. Keep it beside the project's
`state.json` and make every generated artifact discoverable from that root.

## Required layout

```text
.img2threejs/
├── state.json
├── renders/<pass>/
├── reviews/<pass>/
├── audit/<angle-set>/
├── manifests/
└── research/
```

Use the exact reference path as an input record; do not duplicate or overwrite the user's original
image. Generated images include captures, crops, overlays, comparison sheets, contact sheets, and
orbit views. Generated information includes specs, metrics, manifests, review records, diagnostic
reports, confidence ledgers, research summaries, and NotebookLM exports.

## State contract

State must carry the root explicitly:

```json
{
  "artifacts": {
    "root": ".img2threejs",
    "reference": "public/front.png",
    "spec": ".img2threejs/manifests/object-sculpt-spec.json"
  },
  "reconstructionLimits": {
    "maxPerPass": 20,
    "maxTotal": 20
  }
}
```

The state values are authoritative at resume time. If a command emits an output elsewhere, move or
copy the evidence into the artifact root and record the final path before marking the step done.
Do not put source implementation files in the artifact root merely to satisfy this rule; generated
evidence and source code have different ownership and review lifecycles.

## Naming and retention

Use stable names containing the pass and view, for example
`renders/scope/01-front.png`, `audit/18-angle/07-orbit-right.png`, and
`reviews/scope-loop03/comparison.png`. Never merge separate review angles into the only copy of
the evidence. A contact sheet is optional and cannot replace the individual source-resolution
images.
