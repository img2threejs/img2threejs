# Research and evidence contract

Research supplements a 2D reference; it never overwrites what the pixels visibly establish. Use it
to infer construction, function, material behavior, terminology, and plausible hidden continuations.

## Source priority

Use sources in this order:

1. User-supplied reference images and metadata for visible appearance and identity.
2. Primary sources: manufacturer manuals, technical drawings, standards, papers, or first-party
   documentation for construction and function.
3. Specialist technical references with identifiable authorship and diagrams.
4. Secondary descriptions or marketplace pages for vocabulary and identity cross-checks only.
5. Agent inference when no source can establish the claim.

Never silently download a mesh, texture pack, or art asset as a substitute for procedural
reconstruction. If an external model is inspected for research, record that it is a reference only
and do not copy its geometry or texture into the deliverable.

## Evidence ledger

Record claims in the spec or an adjacent evidence file using this shape:

```json
{
  "id": "claim-bolt-pivot",
  "claim": "The handle rotates around the visible receiver pin.",
  "kind": "observed|researched|inferred|unknown",
  "sources": ["front-reference", "manufacturer-manual:page-12"],
  "region": "receiver-right-side",
  "confidence": 0.86,
  "affects": ["bolt-assembly", "pivot", "review:orbit-right"]
}
```

`observed` claims need an image region. `researched` claims need a source locator. `inferred` claims
must say what evidence constrained the inference. `unknown` claims must not be presented as exact.

## Research loop

1. Expand the subject name into geometry, mechanism, material, and review terms.
2. Query primary/technical sources before broad web summaries.
3. Cross-check any claim that changes component placement, proportions, motion, or material class.
4. Resolve contradictions explicitly; prefer the source closest to the actual subject and label the
   remaining alternative as uncertainty.
5. Transfer only actionable facts into the adapter: component, relationship, geometry recipe,
   material rule, pivot, or review criterion.

Research may make an object plausible from one view, but it cannot make hidden geometry observable.
Keep per-region confidence and use more reference views when a hidden decision materially affects
the visible silhouette or a requested interaction.

## LLM research safety

Do not treat a fluent research summary as evidence. Reject claims that introduce a part not visible
in the image and not supported by a source. Notebook/research assistants are advisory: verify their
claims against the cited source and the render before putting them into the adapter or review score.
