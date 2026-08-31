# The Emission Target Socket

The pipeline's terminal export point. The default output stays byte-identical procedural Three.js
TypeScript; an exporter (GLB, FBX, …) is an installed plugin selected explicitly with `--target` —
zero base changes, proven continuously by the sculpt-echo round-trip test's clean-`git diff`
assertion. Contract text: `img2` PLUGIN_CONTRACT draft sections and plugin-wiki Scenario 12.

## 1. The flow — a target is an addition, never a replacement

The spec is the architecture's real fork point: `object-sculpt-spec.json` has 12 non-test consumers
in `forge/`, and the TypeScript emitter is one of them. The per-pass build loop is untouched; the
socket runs only after every gate has passed.

```mermaid
flowchart LR
    IMG(["reference image"]) -->|stages 1–2| SPEC["object-sculpt-spec.json<br/>hub · 12 consumers"]
    SPEC <-->|every pass| LOOP["build loop ×8 — generate → render<br/>pass progress is denominated in screenshots of the TS<br/>→ src/createObjectModel.ts"]
    SPEC --> AR(["action-ready<br/>all gates passed"])
    AR --> SOCK["emit_target.py<br/>FINAL_STEPS · the socket"]
    SOCK -->|"no --target"| NOOP["no-op that succeeds<br/>writes nothing · reads no registry"]
    SOCK -->|"--target glb"| PLUG["plugin exporter<br/>bounded subprocess"]
    PLUG -->|verify| ART["model.glb<br/>+ provenance"]
    style SOCK stroke-width:3px
    style NOOP stroke-dasharray:0,stroke:#2e7d32
```

With no `--target` the export step is a no-op that returns **before any registry read** — a
malformed `plugins.json` cannot break the default path. Selecting a target adds a second artifact
beside the TypeScript; nothing is replaced, overridden or removed.

## 2. The plugin surface — from two disjoint halves to one

The PR #106 review's central finding: everything `doctor` validated was consumed by nothing, and
everything that drove the run was validated by nothing.

**Before** — dashed edges are the missing half of each file's life:

```mermaid
flowchart LR
    DOC["img2 doctor"] --> CAP["plugin.json capabilities"] & ST["steps.json"] & GA["gates.json"]
    CAP -. no consumer .-> X1(("∅"))
    ST  -. no consumer .-> X2(("∅"))
    GA  -. "blocking: true was decorative" .-> X3(("∅"))
    X4(("∅")) -. no validation .-> DOM["domain.json"]
    X5(("∅")) -. no validation .-> SSP["spec_search_profile.json"]
    DOM --> SPL["checklist splice"]
    SSP --> SRCH["spec search"]
```

**After** — every declaration file is validated *and* has a named consumer; the two bold consumers
are the ones this change built:

```mermaid
flowchart LR
    DOC["img2 doctor"] --> CAP["plugin.json capabilities"] & ST["steps.json + provides"] & GA["gates.json"] & DOM["domain.json"] & SSP["spec_search_profile.json"]
    CAP --> T["targets.py — resolve"]
    ST  --> E["emit_target.py"]
    GA  --> R["run_gates.py"]
    DOM --> SPL["checklist splice"]
    SSP --> SRCH["spec search"]
    style T stroke-width:3px
    style E stroke-width:3px
    style R stroke-width:3px
```

Placeholder vocabularies are closed **per consumer**, matching what each renderer actually
substitutes — one uniform set was never true:

| file | vocabulary | renderer |
|---|---|---|
| `steps.json` / `gates.json` | `{plugin_dir}` `{workspace}` `{image}` `{spec}` | harness-executed argv |
| `domain.json` | `{plugin_dir}` `{reference}` `{spec}` `{pass_id}` | checklist `.format()` — `{workspace}`/`{image}` there would `KeyError` |

Shell-metacharacter hardening applies to command rows (leading token is a path or known
interpreter); prose instruction rows — which the cookbook's own examples contain, parentheses and
all — get vocabulary checks only.

## 3. Inside the socket — every failure path has a name

```mermaid
flowchart LR
    A["action-ready?"] --> B["resolve<br/>targets.py re-checks<br/>schema · coreApi · harness range"]
    B --> C["bounded run<br/>300s, cap 1800s · 1 MiB out<br/>IMG2_HOME injected"]
    C --> D["verify kind<br/>GLB magic / size+location"]
    D --> E["determinism<br/>run ×2 · cached by sha+spec-hash"]
    E --> F["provenance<br/>+ artifact"]
    A -. "refuse — names the unmet step" .-> FA["✗"]
    B -. "ambiguous — names every claimant<br/>missing — names the kind, never falls back" .-> FB["✗"]
    C -. "timeout — kill, delete the partial" .-> FC["✗"]
    D -. "wrong kind — declared kind + parse failure" .-> FD["✗"]
    E -. "byte mismatch — names the target" .-> FE["✗"]
    style F stroke:#2e7d32,stroke-width:3px
```

There is no silent fallback: *selected-and-failed* is never *nothing-selected*. Only the latter
falls back to the default TypeScript path. The base warrants the TypeScript model and its gates; a
target artifact carries provenance plus the target plugin's own gate verdicts, and no base quality
warranty beyond container/existence/size/location/determinism.

## 4. Gate execution — the two-clause participation rule

Every shipped plugin's `blocking: true` gate had never executed anywhere. `run_gates.py` runs them
behind a rule derivable from workspace state — never from name matching:

```mermaid
flowchart LR
    RG["run_gates.py<br/>FINAL_STEPS · plugin-gates"] --> C1["clause i — domain owner<br/>state.profile → plugins/&lt;id&gt;/domain.json<br/>owner derived from the registry PATH"]
    RG --> C2["clause ii — selected target<br/>targetSelection.pluginId<br/>written by emit_target"]
    C1 -->|its contributed steps ran| GATES["that plugin's gates<br/>bounded · envelope parsed, never trusted"]
    C2 --> GATES
    GATES -->|blocking fail| STOP["STOP<br/>names gate + plugin"]
    style STOP stroke:#c62828,stroke-width:3px
```

A plugin that contributed no step to the workspace's checklist runs no gates. A malformed verdict
envelope is an error, never a pass, and a verdict's declared status must agree with its exit code.

## Why the third design

| | design | why it fell / held |
|---|---|---|
| ✗ | **A — fragment splicing** at 8 anchors inside `generate()` | AST showed 7 of 8 anchors are not seams (a 711-line decomposition of a 1,753-line function); fragments are compiled-and-executed TS; substitution dragged in the reserved `overrides` mechanism |
| ✗ | **B — manifest-level target edge** | right axis, two false economic claims: resolution existed but *invocation* did not (no `{spec}` placeholder), and the base emitter does not natively conform (overloaded exit 2, envelope-less error paths) |
| ✓ | **C — `provides` on a step row + terminal socket** | uses the contract's own documented upgrade path: manifest edge = *discovery*, step row = *invocation + artifact*, targetness lives in the **user's query**; nothing is replaced, so no override machinery is needed |

Full review trail (5 adversarial reviews, 43+ recorded decisions):
`openspec/changes/establish-the-emission-target-contract/review/` in the org workspace.
