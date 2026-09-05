# Local Spec Search

After image analysis and before writing or refining a spec, local evidence is a pipeline stage,
not an optional memory lookup, whenever the request needs domain-specific anatomy, PBR, wear,
geometry, runtime, or physics specifications.

The pre-spec command automatically runs BM25, chooses the collection a resolved domain contributes, or `core_3d`,
otherwise, and writes a `localSpecSearch` evidence bundle into the assessment:

```
python3 forge/stage2_spec/new_pre_spec_assessment.py "Name" --image <img> --out assessment.json
```

Add observed terms with repeatable `--spec-query "<term>"`; use `--collection <collection>` only
when the automatic collection choice is insufficient. `new_sculpt_spec.py --assessment` carries
that bundle into the final spec, including snippets, `source_refs`, and `evidence_refs`.

For extra focused retrieval, the direct CLI remains available:

```
python3 forge/stage1_intake/search_specs.py "<query>" --collection <collection> --limit 3 --snippet-chars 250 --json
```

For a domain corpus, include the anatomical and the colloquial name, for example
`--spec-query "safety ring finger ring"`, or query the collection directly with `search_specs.py`. A domain corpus ships with the plugin that serves it.
Expand queries with object names,
component names, material/finish terms, behavior terms, and known aliases; retry focused
alternatives when the first result is incomplete. Build the spec from returned evidence and do
not invent domain specs when local evidence exists. Search caches are local/generated only;
preserve JSONL records and source provenance rather than replacing them with cache output.
