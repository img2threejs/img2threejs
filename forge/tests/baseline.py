#!/usr/bin/env python3
"""Quality baseline harness: record what the current pipeline actually produces.

Two entry points, one scorer.

    baseline.py --reference ref.png --render render.png [--json]
        Single-pair mode. Scores one render against one reference. This is the
        interface plugins call to score their own runs; it is deliberately the
        same code path the sweep uses, so a plugin's number and the baseline's
        number are comparable.

    baseline.py --manifest baseline-manifest.json [--json]
        Sweep mode. Scores every run recorded in the manifest, aggregates, and
        reports the observed spread for any reference run more than once.

What this measures, and what it does not
----------------------------------------
It scores *renders against references* with the deterministic ensemble in
`stage4_review/divine_eye.py`. It does not judge whether a spec was honest,
whether a component tree is meaningful, or whether small identity-defining
detail survived -- `divine_eye` itself records that features a few pixels wide
are absent before comparison rather than scored badly.

A real reconstruction is agent-driven, so two runs of the same reference will
differ. Re-*scoring* a fixed pair is deterministic and is asserted by the tests;
re-*running* the pipeline is not, which is why the manifest records a spread
rather than a single number.

Nothing here stamps a timestamp. A timestamp would make two scoring runs differ
and destroy the only determinism property this harness actually offers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "stage4_review"))

from divine_eye import evaluate  # noqa: E402

SCHEMA_VERSION = 1

# divine_eye emits hard-gate strings with NO colon separator -- see
# stage4_review/divine_eye.py:338-345, e.g. "silhouette IoU 0.012 < 0.85". Splitting
# on ":" therefore yielded the whole string including the measured value, so every
# run produced a unique key with count 1 and the "which gate fails most" signal was
# destroyed. Group on the known prefixes instead.
HARD_GATE_PREFIXES = (
    "silhouette IoU",
    "scale delta",
    "foreground mask",
)
SKILL_VERSION_RE = re.compile(r"^version:\s*(.+?)\s*$", re.MULTILINE)


class BaselineError(RuntimeError):
    """Raised when the harness cannot produce an attributable result."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def skill_version(skill_root: Path) -> str | None:
    """Read `version:` out of SKILL.md frontmatter. None when it cannot be read."""
    skill_md = skill_root / "SKILL.md"
    if not skill_md.is_file():
        return None
    match = SKILL_VERSION_RE.search(skill_md.read_text(encoding="utf-8")[:2048])
    return match.group(1) if match else None


def environment_record(
    *,
    skill_root: Path,
    lockfile: Path | None = None,
    browser_build: str | None = None,
    three_version: str | None = None,
) -> dict[str, Any]:
    """What a score is attributable to.

    Every field that cannot be resolved is recorded as null AND named in
    `unresolved`. A baseline carrying unresolved fields is not comparable across
    machines, and `assert_comparable` refuses to compare it.
    """
    record: dict[str, Any] = {
        "skillVersion": skill_version(skill_root),
        "threeVersion": three_version,
        "browserBuild": browser_build,
        "lockfileSha256": sha256_file(lockfile) if lockfile and lockfile.is_file() else None,
    }
    record["unresolved"] = sorted(key for key, value in record.items() if value is None)
    return record


def assert_comparable(left: dict[str, Any], right: dict[str, Any]) -> None:
    """Refuse to compare two baselines produced under different conditions.

    A toolchain change must report as a toolchain change, never as a quality
    regression -- that confusion is what makes a baseline get abandoned.
    """
    fields = ("skillVersion", "threeVersion", "browserBuild", "lockfileSha256")
    for side in (left, right):
        if "unresolved" not in side:
            raise BaselineError(
                "baseline is not comparable: no environment record (an absent record "
                "is unknown provenance, not matching provenance)"
            )
        missing = sorted(set(side["unresolved"]) | {f for f in fields if side.get(f) is None})
        if missing:
            raise BaselineError(
                f"baseline is not comparable: unresolved environment fields {missing}"
            )
    differing = [key for key in fields if left.get(key) != right.get(key)]
    if differing:
        raise BaselineError(f"toolchain changed, not comparable: {differing}")


def score_pair(reference: Path, render: Path) -> dict[str, Any]:
    """Score one render against one reference. Deterministic for fixed inputs."""
    for path in (reference, render):
        if not path.is_file():
            raise BaselineError(f"missing image: {path}")
    return evaluate(reference.resolve(), render.resolve())


def hard_gate_key(failure: Any) -> str:
    """Group a hard-gate failure string by which gate produced it.

    Matched against the literal prefixes divine_eye emits, so a message carrying a
    measured value groups with every other failure of the same gate.
    """
    text = str(failure).strip()
    for prefix in HARD_GATE_PREFIXES:
        if text.startswith(prefix):
            return prefix
    return "other"


def aggregate(scores: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise a set of scores. Refuses to summarise nothing."""
    if not scores:
        raise BaselineError(
            "no scores to aggregate -- an empty baseline reports nothing, and "
            "reporting 0.0 would read as a measured result"
        )
    fidelities = [float(score["fidelity"]) for score in scores]
    passes = sum(1 for score in scores if score["verdict"] == "pass")
    hard_failures: dict[str, int] = {}
    for score in scores:
        for failure in score.get("hardGateFailures", []):
            key = hard_gate_key(failure)
            hard_failures[key] = hard_failures.get(key, 0) + 1
    return {
        "runCount": len(scores),
        "passCount": passes,
        "fidelityMean": round(sum(fidelities) / len(fidelities), 4),
        "fidelityMin": round(min(fidelities), 4),
        "fidelityMax": round(max(fidelities), 4),
        "hardGateFailures": dict(sorted(hard_failures.items())),
    }


def variance(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Observed run-to-run spread, per reference run more than once.

    Without this a later comparison cannot tell a regression from ordinary
    agent-driven variance.
    """
    by_reference: dict[str, list[float]] = {}
    for run in runs:
        by_reference.setdefault(str(run["referenceId"]), []).append(
            float(run["score"]["fidelity"])
        )
    repeated = {
        reference_id: values
        for reference_id, values in by_reference.items()
        if len(values) > 1
    }
    return {
        "measured": bool(repeated),
        # The docs require three runs on at least one reference. Two runs give a
        # spread but not a usable sense of variance, so say which one happened.
        "sufficient": any(len(values) >= 3 for values in repeated.values()),
        "perReference": {
            reference_id: {
                "runs": len(values),
                "fidelityMin": round(min(values), 4),
                "fidelityMax": round(max(values), 4),
                "spread": round(max(values) - min(values), 4),
            }
            for reference_id, values in sorted(repeated.items())
        },
    }


def run_sweep(manifest_path: Path) -> dict[str, Any]:
    """Score every run the manifest declares and build the baseline artifact."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = manifest_path.parent
    entries = manifest.get("references", [])
    references: dict[str, Any] = {}
    for index, entry in enumerate(entries):
        for field in ("id", "path", "sha256", "subjectClass", "licence"):
            if not entry.get(field):
                raise BaselineError(
                    f"references[{index}] is missing {field!r}; an unattributed or "
                    "unlicensed reference must not enter a committed baseline"
                )
        if entry["id"] in references:
            raise BaselineError(
                f"duplicate reference id {entry['id']!r}; the later entry would "
                "silently replace the earlier one and score the wrong image"
            )
        references[entry["id"]] = dict(entry)
    if not references:
        raise BaselineError(
            f"{manifest_path.name} declares no references -- supply and licence "
            "them first (see docs/quality-baseline.md)"
        )

    for entry in references.values():
        reference_path = (base / entry["path"]).resolve()
        if not reference_path.is_file():
            raise BaselineError(
                f"reference {entry['id']}: file not found at {entry['path']}")
        actual = sha256_file(reference_path)
        if entry["sha256"] != actual:
            raise BaselineError(
                f"reference {entry['id']} changed on disk: manifest records "
                f"{entry['sha256']}, file is {actual}"
            )

    scored: list[dict[str, Any]] = []
    for run in manifest.get("runs", []):
        reference_id = run.get("referenceId")
        if reference_id not in references:
            raise BaselineError(f"run names unknown reference {reference_id!r}")
        try:
            render_relative = run["renderPath"]
        except KeyError as exc:
            raise BaselineError(f"run for {reference_id!r} has no renderPath") from exc
        score = score_pair(
            (base / references[reference_id]["path"]).resolve(),
            (base / render_relative).resolve(),
        )
        # Absolute paths would write this machine's home directory into a committed
        # artifact and stop two machines' baselines from being byte-comparable.
        score = {**score, "reference": references[reference_id]["path"],
                 "render": render_relative}
        scored.append({**run, "score": score})

    return {
        "schemaVersion": SCHEMA_VERSION,
        "environment": manifest.get("environment", {}),
        "references": [references[key] for key in sorted(references)],
        "runs": scored,
        "aggregate": aggregate([run["score"] for run in scored]),
        "variance": variance(scored),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, help="single-pair mode: the reference image")
    parser.add_argument("--render", type=Path, help="single-pair mode: the render to score")
    parser.add_argument("--manifest", type=Path, help="sweep mode: the baseline manifest")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    single = args.reference is not None or args.render is not None
    if single and args.manifest is not None:
        parser.error("choose one: --reference/--render, or --manifest")
    if single and (args.reference is None or args.render is None):
        parser.error("single-pair mode needs both --reference and --render")
    if not single and args.manifest is None:
        parser.error("nothing to do: pass --reference/--render, or --manifest")

    try:
        result = (
            score_pair(args.reference.expanduser(), args.render.expanduser())
            if single else run_sweep(args.manifest.expanduser())
        )
    except BaselineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        # A published interface another repo calls must not answer with a traceback.
        # divine_eye.py:444 does the same, and exit 2 keeps error classes separable.
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    elif single:
        print(
            f"{result['verdict'].upper()} fidelity={result['fidelity']} "
            f"(target {result['fidelityTarget']})"
        )
        for failure in result["hardGateFailures"]:
            print(f"  HARD: {failure}")
    else:
        summary = result["aggregate"]
        print(
            f"{summary['passCount']}/{summary['runCount']} pass  "
            f"fidelity mean={summary['fidelityMean']} "
            f"min={summary['fidelityMin']} max={summary['fidelityMax']}"
        )
        spread = result["variance"]
        if not spread["measured"]:
            print("  WARNING: no reference was run more than once; spread is unmeasured")
        elif not spread["sufficient"]:
            print("  WARNING: no reference reached three runs; spread is indicative only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
