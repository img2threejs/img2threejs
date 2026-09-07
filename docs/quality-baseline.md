# Quality baseline

`forge/tests/baseline.py` records what the current pipeline actually produces, so a later change to
`forge/` can be shown to have improved or degraded output rather than merely passing its tests.

## Why this exists

`forge/tests` has over a thousand green tests, but they are unit tests of the tooling. Nothing in
them proves the skill produces a good model from an image. Without a measured baseline, no refactor
of the pipeline can be shown not to have degraded quality — and the project's rule that a gate
tolerance is never widened to make something pass has no measurement to defend.

## What it measures, and what it does not

It scores **renders against references** using the deterministic ensemble in
`forge/stage4_review/divine_eye.py`.

It does **not** judge whether a spec was honest, whether a component tree is meaningful, or whether
small identity-defining detail survived. `divine_eye` records this limit itself: a feature a few
pixels wide in a 1920px reference is *absent before comparison*, not scored badly. A high baseline
number is therefore necessary but not sufficient evidence of quality.

## Determinism, honestly

Two properties, and only the first is guaranteed:

- **Scoring is deterministic.** Re-scoring a fixed reference/render pair yields identical output.
  This is asserted by `test_baseline_harness.py`.
- **Running is not.** A real reconstruction is agent-driven, so two runs of the same reference will
  differ. This is why the manifest records a *spread* rather than a single number, and why at least
  one reference must be run three times. Without that, a later comparison cannot distinguish a real
  regression from ordinary variance.

The harness writes no timestamps. A timestamp would make two scoring runs differ and destroy the one
determinism property the artifact offers.

## Attribution

Every baseline carries an environment record: skill version, resolved `three` version, browser
build, and lockfile hash, plus a content hash per reference. Any field that cannot be resolved is
recorded as `null` **and** named in `unresolved`.

The environment block is **author-supplied** in the manifest — `threeVersion` and `browserBuild` are
only knowable by whoever ran the pipeline, so the sweep does not invent them. Use
`environment_record()` to build the block correctly; it computes `unresolved` for you. A manifest
with no environment block produces a baseline that `assert_comparable` refuses outright, because an
absent record is unknown provenance rather than matching provenance.

`assert_comparable()` refuses two baselines whose environments differ, reporting **"toolchain
changed"** rather than a quality delta. That distinction is what keeps a baseline from being
abandoned the first time CI installs a newer Chromium.

## Populating it

The repository ships **no reference images**, and `fixtures/baseline-manifest.json` is empty on
purpose. To produce a real baseline:

Every `references[]` entry must carry `id`, `path`, `sha256`, `subjectClass` and `licence`. All five
are mandatory: an unattributed or unlicensed reference is refused rather than scored, duplicate ids
are refused rather than silently collapsed, and a reference whose hash no longer matches is refused
rather than re-scored. Recorded paths are relative to the manifest, so a committed artifact carries
no machine-specific absolute path.

1. Supply 6–10 references spanning the classes the pipeline claims to serve — hard-surface object,
   character, and at least one built-environment subject. Record redistribution terms per image.
2. Convert every reference to **PNG**. The decode chain is PNG, then a built-in JPEG decoder, then
   macOS `sips`; a `.webp` reference makes the baseline unreproducible off macOS.
3. Place them under `forge/tests/fixtures/baseline-references/` and add a `references[]` entry each.
4. Run the pipeline for real, once per reference and **three times for one of them**, following
   `SKILL.md` without shortcutting the loop or the local state gate. The sweep reports
   `variance.sufficient: false` when no reference reached three runs, and warns on the console.
5. Add a `runs[]` entry per run pointing at its render, then sweep.

## Usage

```bash
# Sweep the manifest and produce the baseline artifact
python3 forge/tests/baseline.py --manifest forge/tests/fixtures/baseline-manifest.json --json

# Score a single pair — the interface plugins use for their own runs
python3 forge/tests/baseline.py --reference ref.png --render render.png --json
```

Single-pair mode is deliberately the same code path the sweep uses, so a plugin's number and the
baseline's number are comparable.

## The rule that makes it worth having

If the measured number is low, or the spread is wide, report it as measured. Do not tune thresholds,
reselect references, or discard poor runs. A baseline that has been massaged to look good is worse
than no baseline, because it will be trusted.
