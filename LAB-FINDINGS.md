# Lab experiment 1 — three intake steps as plugin rows

Branch `lab/pipeline-as-data`, worktree of `img2threejs` at v1.5.1 (`dede590`).
Harness under test: `img2` v0.2.0, contract revision 14.
Run in a throwaway `$HOME`; nothing was installed into the real skills dirs.

## What was done

`plugin.json` (`name: img2threejs-lab`, capability `image → threejs-code-lab` — a deliberately
non-product edge so it can never contend with the real skill) plus `steps.json` carrying the first
three `SETUP_STEPS` rows from `forge/_shared/workflow_state.py`, **commands copied verbatim**:

| id | command as it exists in the product today |
|---|---|
| `image-analysis` | `Read grimoire/intake/image_analysis.md and analyze {reference}` |
| `reference-suitability` | `Read grimoire/intake/validation_rubric.md and record a pass, conditional, or reject verdict for {reference}` |
| `reference-admission` | `python3 forge/stage1_intake/check_reference_admission.py {reference}` |

Nothing was adapted to make it pass. The point was to see what the harness refuses.

## Finding 1 — predicted: the placeholder set rejects every row

`img2 doctor` → **3 FAIL, exit 1**, one per row:

```
FAIL img2threejs-lab steps.json "image-analysis": command uses unrecognised
     placeholder {reference}; only {plugin_dir}, {workspace} and {image} are permitted
```

Correct and loud. This is the guard working. Resolving it is a policy decision, not a new field:
either widen the closed set (weakening a check that shipped three days ago) or rename `{reference}`
to `{image}` across the pipeline's step data.

## Finding 2 — NOT predicted, and it is a defect in the harness, not in the pipeline

`img2 capabilities --from-kind image --to-kind threejs-code-lab --json` on the **same** install, at
the same moment `doctor` was failing all three rows:

```json
{ "status": "answered", "problems": [],
  "providers": [ { "steps": [
    { "id": "image-analysis",
      "argv": ["Read", "grimoire/intake/image_analysis.md", "and", "analyze", "{reference}"] } … ] } ] }
```

**exit 0. `status: "answered"`. `problems: []`.**

So the query hands a caller an argv whose `argv[0]` is `Read`, with an unsubstituted `{reference}`
still in it, and reports no problem at all. The contract tells callers to branch on `status` and
never on stderr — a caller doing exactly that executes this.

And on macOS it does not even fail loudly. The filesystem is case-insensitive, so `Read` resolves to
a real binary:

```
$ python3 -c "import shutil; print(shutil.which('Read'))"
/usr/bin/Read                     # i.e. /usr/bin/read
$ Read grimoire/intake/image_analysis.md and analyze '{reference}'
/usr/bin/Read: line 4: read: `grimoire/...': not a valid identifier
$ echo $?
0
```

**Exit 0.** The caller concludes the step succeeded. This is a silent-wrong-success path in a system
whose first principle is "fail loud, no silent partial activation" — and it is reachable today, on
v0.2.0, by any plugin whose `steps.json` doctor rejects.

Root cause: `img2 doctor` and `img2 capabilities` apply different validation. Doctor runs
`commandFinding`; the query does not consult it. Reviewer C named exactly this gap during review
("argv[0] resolvability … belongs in problems[] rather than as a runtime surprise") and it was not
implemented — recorded then as a missing requirement, demonstrated now as a live defect.

**Required fix, in the harness, before any caller consumes the query:** `cmdCapabilities` MUST run the
same static checks doctor runs on every row it is about to return, and a row that fails them belongs
in `problems[]` — never in `providers[]`. If the queried edge has no clean provider left, the status
is `data-fault`, not `answered`.

## Finding 3 — the field count, measured

On three rows, only two of the four predicted fields are actually forced:

| Predicted field | Forced by these 3 rows? | Evidence |
|---|---|---|
| `actor: agent \| tool` | **Yes** | 2 of 3 rows are instructions to the model, not programs. Without it the harness cannot know not to exec them. |
| `mustRead: [paths]` | **Yes** | `grimoire/intake/image_analysis.md` is the actual payload of those rows; nothing else can carry it. |
| `when` / `profiles` | Not yet | The CS2/character splice lives in later steps (`workflow_state.py:110-118`). |
| `config` | Not yet | Thresholds appear in review/build steps, not intake. |

So the honest score is **2 fields forced by 3 rows, with 2 more expected once the CS2/character splice
and the threshold-bearing review steps are converted** — i.e. the four-field estimate looks right for
the full 27, and it was not an overestimate.

## Decision gate

The gate was: 0-1 new fields → proceed incrementally; all 4 plus harness changes → answer is "not
yet". The measured result is **2 fields forced on the easiest 3 of 27 rows, a placeholder policy
change, and a harness defect that must be fixed regardless**. That is the "not yet" side of the gate.

Sequence this points to:
1. **Fix Finding 2 in the harness now**, independent of any of this. It is a live silent-success path
   in a shipped release, it needs no schema decision, and it is small: reuse doctor's checks in the
   query.
2. Then the short path — the base skill consults `img2 capabilities`, `plugin-img2glb` gets an offline
   step — which delivers the original request and produces the first real consumer of the query.
3. Only then design the row schema, with `actor`/`mustRead` now measured rather than guessed, and
   `when`/`config` confirmed by converting one CS2 step and one threshold-bearing review step.

## What this experiment did not test

Whether the pipeline still produces good geometry. These are structural checks only. No reconstruction
was run, no render captured, no baseline recorded — that debt is unchanged.
