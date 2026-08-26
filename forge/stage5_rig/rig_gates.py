#!/usr/bin/env python3
"""Stage R6 — the G1..G10 gate runner (1.5.2 §"Stage R6 — Gates").

WHY THIS MODULE EXISTS. The 1.5.1 rigging failures were not "slightly wrong"; they were total and
silent. Eleven clips held actions, the mixer held state, buttons dispatched, and nothing moved. Two
separate bugs each produced a plausible-looking scene with zero motion, and neither was visible in
code review. Every gate below is one of those failures, written down as a number. The R6 table's
"Catches" column is carried on every result for exactly that reason: a failing report has to explain
itself without the reader opening the spec.

THE HONESTY RULE IS THE POINT OF THIS MODULE. A gate whose input is absent reports
`status: "unevaluated"` with a human-readable reason. It is NEVER silently a pass, and
`GateReport.ok` is True only when every one of the ten reports `pass`. An unevaluated gate makes the
whole report not-ok, because "we did not check" and "we checked and it was fine" are different
claims and only one of them ships.

UNDER-COVERAGE IS A FAILURE, NOT A PASS. G1, G2, G3, G6 and G10 consume measurements a browser/host
harness produces — Python cannot evaluate a three.js interpolant or count background pixels. So this
module is the arbiter, not the measurer, and its job is as much to audit the harness's coverage as
its numbers. A harness that reports `maxSampledBindingDelta = 1e-12` over one clip at one time has
not proved anything: the clip that plays silently is precisely the clip nobody sampled. G1 therefore
fails a tiny delta with thin coverage, and G2 and G10 do the same for their own axes.

DELEGATION. This module owns no measurement it can borrow:
  G4/G5 -> `skin_conditioning.validate_binding` (Gate R2 lives there)
  G7    -> `action_design.check_medial_lateral`
  G8    -> `action_design.foot_slide` / `action_design.FOOT_SLIDE_LIMIT`
  G9    -> `clip_features.measure_clip(...).scale_delta`
Re-implementing any of them here would let the gate and the stage drift apart, which is how a gate
stops being evidence.

---------------------------------------------------------------------------------------------
SPEC AMBIGUITIES AND THE READING CHOSEN
---------------------------------------------------------------------------------------------
1. "over >= 5 times x all clips" (G1). The spec states the delta bound but never says which side
   under-coverage falls on. READING: coverage is part of the criterion, not a side note — a clip
   missing from the harness's roster and a clip sampled 3 times both FAIL G1, with a reason naming
   the clips. The roster is taken from the sampled-clip payload's `clips` when present (that is the
   authoritative clip set); when there is no sampled-clip payload the payload must carry a top-level
   `clipNames`. The roster is never taken from the harness's own report, which would make
   "covered every clip" true by construction; a payload carrying neither leaves G1 `unevaluated`.

2. ">= 64 vertices/mesh/frame" (G2). Same reading as G1: a mesh sampled at 8 vertices FAILS. The
   per-mesh entry may give `verticesPerFrame` as one number (interpreted as the MINIMUM across that
   mesh's frames, the only reading under which a single number can support a per-frame claim) or as
   a list of per-frame counts, which is checked entry by entry.

3. "materially thinner than its declared clip set allows" (G10). The spec fixes one sweep shape —
   11 clips x 4 times x 2 sides x 2 azimuths = 176 frames — without saying what a *different* clip
   set requires. READING: the per-axis minima (4 times, 2 sides, 2 azimuths) are the constants and
   the clip axis scales with the payload, so the required frame count is
   `clips x 4 x 2 x 2`. The declared `frames` must also equal the product of the declared axes: a
   sweep claiming 176 frames from axes that multiply to 20 is a bookkeeping error, and the spec's
   whole point is that the declared number is not taken on trust.

4. "background-through-split <= measured baseline" (G10). "Baseline" is the control run with the
   blend disabled (§R2's `blend off` row), so it must be a measured number carried in the payload.
   READING: an absent baseline leaves G10 `unevaluated` rather than defaulting to the Lee Sin
   numbers — a threshold borrowed from another subject is not a measurement of this one. Holes and
   creases are always reported as TWO numbers; §R2 trades one against the other and a single
   combined score hides that trade. Creases are reported and NEVER gated: R2 accepts a ~16% crease
   increase deliberately, so failing on crease count would ask a later stage to undo R2.

5. "unless the source has it" (G9). READING: the exemption must be DECLARED, by
   `sourceScalesJoints: true` on the payload, and it produces a pass carrying a warning that names
   every clip with a non-zero scaleDelta. It never produces a silent pass, because the reason the
   tripwire exists is that joint scale changes what Stage R2 may legally do to skin weights and
   somebody downstream has to be told.

6. G6's `visibleMeshCount == visibleSkinnedMeshCount`. Equality is the criterion as written, so a
   payload reporting MORE skinned meshes than visible meshes also fails — the two numbers disagree
   and one of them is wrong, which is a finding either way.

7. `GateResult.measured` is deliberately typed loose (a number, a dict, or None). Several gates are
   irreducibly multi-valued — G10 reports holes and creases, G1 reports a delta and a coverage
   table — and flattening them to one number is the exact mistake the spec calls out for G10.

8. G7 and the tautological pass. `action_design.Chains.gate_is_independent` records something the
   R6 table does not: when left/right were assigned by "left = +X" rather than claimed from outside
   the geometry, the gate compares the geometry against itself and the mirrored-rig case is
   unreachable. READING: an errorless run of a non-independent gate reports `unevaluated`, not
   `pass`. A pass that could not have failed is the silent pass this module exists to prevent. A
   non-empty error list is still a `fail` -- a degenerate pair is a real finding either way.

9. G8 and clips with no stance. `stance` is optional in the payload and nothing here can tell a
   gait clip from a gesture clip without re-classifying. READING: a clip carrying no stance is
   recorded and WARNED about, not failed -- failing every non-gait clip would make the gate
   unusable -- but a payload where NO clip carries measurable stance leaves G8 `unevaluated`,
   because a gate with no stance data is an unasked question, not a clean answer.

10. G11 and a manifest/payload disagreement about which meshes exist. `mesh_parity.verify`
    already decides this — a frozen mesh absent from the payload is `mesh missing`, a payload mesh
    absent from the manifest is `mesh added`, and both are FAILURES ("implementation may not remove
    visible geometry" / "...may not create new visible geometry either"). READING: this module
    DEFERS to that decision rather than re-deciding it. G11 is a thin arbiter over
    `mesh_parity.verify` and adds no parity rule of its own; re-deriving one here is how a gate and
    the stage it gates start disagreeing about what "byte-identical" meant.

11. G11 and legally-added attributes. `skinIndex` and `skinWeight` are deliberately outside
    FROZEN_BUFFERS — adding them is the entire legal purpose of rigging. READING: the additions
    `mesh_parity` reports are carried into `measured.addedLegal` for the reader and NEVER
    contribute to the verdict. A gate that failed every successful rig would simply be switched
    off, which is worse than not having it.

12. G12 and the GLB report's `ok` field. `GlbRig.to_dict()` does not emit `ok`; the field is added
    by `glb_rig_reference`'s CLI (`result["ok"] = not errors`), so an in-process caller's report
    legitimately lacks it. READING: an explicit `ok: false` is honoured as a failure, and an absent
    `ok` is DERIVED as `structuralFailure is None and not errors` rather than treated as missing
    input. Requiring a field the library itself does not produce would leave G12 permanently
    unevaluated for every caller that does not shell out.

13. G12 and unsupported interpolation. The R6 brief groups "no skin, multiple skins without a
    choice, unsupported interpolation" under `structuralFailure`, but `glb_rig_reference` sets
    `structural_failure` only for the two skin cases and surfaces unsupported interpolation
    separately as `unsupportedInterpolationClips`. READING: both are FAILURES and the reason names
    which one fired, because a clip whose interpolation cannot be evaluated addresses the skeleton
    no more usefully than one addressing the wrong skeleton. Neither is ever `unevaluated`: a
    structural failure is a known-bad answer, not a missing one.

14. G12 with a procedural source and no correspondence. READING: FAIL, not unevaluated. A payload
    declaring a procedural skeleton and supplying no mapping to the GLB's joints is not a gate
    with absent input — it is the exact defect G12 exists to catch, stated in the payload.

Pure Python 3.10+ stdlib. No pip installs, no numpy, no three.js.
"""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

# The three sibling modules below are imported flat, the way they import each other. This file
# is the first with HARD sibling dependencies (the others import defensively or not at all), so
# it puts its own directory on the path rather than relying on every caller to bootstrap it.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from action_design import (  # noqa: E402
    FOOT_SLIDE_LIMIT,
    MEDIAL_AXIS_TOLERANCE,
    ChainResolutionError,
    Chains,
    check_medial_lateral,
    foot_slide,
    resolve_chains,
)
from clip_features import measure_clip  # noqa: E402

# These two are imported as NAMESPACES rather than flat. Both define their own STATUS_PASS /
# STATUS_FAIL / STATUS_UNEVALUATED with the same string values as this module's, and
# mesh_parity also exports a `load_payload`; importing them flat would shadow ours with
# constants that happen to agree today and might not tomorrow.
import glb_rig_reference  # noqa: E402
import mesh_parity  # noqa: E402
from skin_conditioning import SkinBinding, WEIGHT_SUM_TOLERANCE, validate_binding  # noqa: E402

# ---------------------------------------------------------------------------------------------
# Tolerances. The names are the spec's and the values come from CONTRACT_1.5.2.md; the two that
# skin_conditioning and action_design own are IMPORTED, never restated, so a change there cannot
# leave this file quietly disagreeing with the stage it gates.
# ---------------------------------------------------------------------------------------------
BINDING_EPSILON = 2**-23          # float32 epsilon: G1
BIND_RESTORE_TOLERANCE = 1e-12    # G3
MINIMUM_GATE_R1_SAMPLES = 5       # G1 coverage: ">= 5 times", per clip
MINIMUM_G2_VERTICES = 64          # G2 coverage: ">= 64 vertices/mesh/frame"

# G10 sweep coverage. `single-subject`: the sweep that found the real defects was 11 clips x 4 times
# x 2 sides x 2 azimuths = 176 frames, and the clip axis is the only one that follows the payload.
# Callers may override; the report records the values actually used.
MINIMUM_SWEEP_TIMES = 4
MINIMUM_SWEEP_SIDES = 2
MINIMUM_SWEEP_AZIMUTHS = 2

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_UNEVALUATED = "unevaluated"


@dataclass(frozen=True)
class SweepCoverage:
    """The G10 axis minima, as data a caller can replace (see ambiguity 3)."""

    times: int = MINIMUM_SWEEP_TIMES
    sides: int = MINIMUM_SWEEP_SIDES
    azimuths: int = MINIMUM_SWEEP_AZIMUTHS
    provenance: str = "single-subject (11 clips x 4 times x 2 sides x 2 azimuths = 176 frames)"

    def required_frames(self, clip_count: int) -> int:
        return max(0, int(clip_count)) * self.times * self.sides * self.azimuths

    def to_dict(self) -> dict[str, Any]:
        return {
            "times": self.times,
            "sides": self.sides,
            "azimuths": self.azimuths,
            "provenance": self.provenance,
        }


DEFAULT_SWEEP_COVERAGE = SweepCoverage()


@dataclass(frozen=True)
class GateSpec:
    """One row of the R6 table, verbatim. `catches` is not decoration — it is the failure the gate
    exists to catch, and it rides on every result so a failing report explains itself."""

    id: str
    name: str
    criterion: str
    catches: str
    input_key: str


GATE_SPECS: tuple[GateSpec, ...] = (
    GateSpec(
        "G1",
        "binding reaches node",
        "maxSampledBindingDelta <= 2^-23 over >= 5 times x all clips",
        "clips that play silently",
        "bindingSamples",
    ),
    GateSpec(
        "G2",
        "deformation finite",
        "applyBoneTransform on >= 64 vertices/mesh/frame, all finite",
        "bad indices, NaN weights",
        "deformation",
    ),
    GateSpec(
        "G3",
        "bind restore",
        "maxBindRestoreDelta <= 1e-12 after stop()",
        "pose bleed between clips",
        "bindRestore",
    ),
    GateSpec(
        "G4",
        "weights normalised",
        "|1 - sum(w)| <= 2e-7 every vertex",
        "shrinking/inflating limbs",
        "binding",
    ),
    GateSpec(
        "G5",
        "indices in range",
        "maxSkinIndex <= bones - 1",
        "one vertex to infinity",
        "binding",
    ),
    GateSpec(
        "G6",
        "every visible mesh bound",
        "visibleMeshCount == visibleSkinnedMeshCount",
        "a part left behind in bind pose",
        "meshVisibility",
    ),
    GateSpec(
        "G7",
        "medial/lateral",
        "leftAnchor.x > 0 > rightAnchor.x",
        "mirrored rig",
        "chainAnchors",
    ),
    GateSpec(
        "G8",
        "foot contact",
        "footSlide <= 0.01H in stance",
        "skating gaits",
        "clips[].stance",
    ),
    GateSpec(
        "G9",
        "no joint scale",
        "scaleDelta == 0 unless the source has it",
        "weight-blend invalidation",
        "clips",
    ),
    GateSpec(
        "G10",
        "skin integrity sweep",
        "background-through-split <= measured baseline",
        "tearing under motion",
        "skinIntegritySweep",
    ),
    GateSpec(
        "G11",
        "mesh parity",
        "every frozen buffer byte-identical after binding",
        "rigging that rewrites mesh geometry",
        "meshParity",
    ),
    GateSpec(
        "G12",
        "rig reference",
        "animation refers to the GLB's own skeleton, or an explicitly usable correspondence",
        "clip channels and skinIndex values addressing a different skeleton",
        "rigReference",
    ),
)

GATE_SPEC_BY_ID: dict[str, GateSpec] = {spec.id: spec for spec in GATE_SPECS}


@dataclass(frozen=True)
class GateResult:
    """One gate's verdict.

    `measured` is loose on purpose (ambiguity 7): several gates are irreducibly multi-valued and
    flattening them to one number is the exact mistake the spec calls out for G10.

    `warnings` carries a pass that a reader still has to be told about — G9's declared source joint
    scaling is the case the spec names. A warning never changes `status`, and a warning is never a
    substitute for a failure.
    """

    id: str
    name: str
    status: str
    criterion: str
    catches: str
    threshold: Any
    measured: Any = None
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status == STATUS_PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "criterion": self.criterion,
            "catches": self.catches,
            "threshold": self.threshold,
            "measured": self.measured,
            "reason": self.reason,
            "details": dict(self.details),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class GateReport:
    """All ten verdicts. `ok` is True only when every gate is `pass`."""

    results: tuple[GateResult, ...]
    figure_height: float | None = None
    sweep_coverage: SweepCoverage = DEFAULT_SWEEP_COVERAGE

    @property
    def ok(self) -> bool:
        return all(result.passed for result in self.results)

    def by_id(self, gate_id: str) -> GateResult:
        for result in self.results:
            if result.id == gate_id:
                return result
        raise KeyError(f"no such gate: {gate_id!r}")

    @property
    def failed(self) -> tuple[GateResult, ...]:
        return tuple(r for r in self.results if r.status == STATUS_FAIL)

    @property
    def unevaluated(self) -> tuple[GateResult, ...]:
        return tuple(r for r in self.results if r.status == STATUS_UNEVALUATED)

    def summary(self) -> dict[str, Any]:
        """Table-shaped: one row per gate, in R6 order, plus the counts a reader reads first."""
        rows = [
            {
                "id": r.id,
                "gate": r.name,
                "status": r.status,
                "measured": r.measured,
                "threshold": r.threshold,
                "catches": r.catches,
                "reason": r.reason,
                "warnings": list(r.warnings),
            }
            for r in self.results
        ]
        return {
            "ok": self.ok,
            "figureHeight": self.figure_height,
            "counts": {
                "total": len(self.results),
                "pass": sum(1 for r in self.results if r.status == STATUS_PASS),
                "fail": len(self.failed),
                "unevaluated": len(self.unevaluated),
            },
            "failed": [r.id for r in self.failed],
            "unevaluated": [r.id for r in self.unevaluated],
            "sweepCoverage": self.sweep_coverage.to_dict(),
            "rows": rows,
            "note": (
                "ok is True only when every gate is `pass`. An unevaluated gate is not a pass: "
                "\"we did not check\" and \"we checked and it was fine\" are different claims."
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "kind": "rig-gates",
            "ok": self.ok,
            "figureHeight": self.figure_height,
            "sweepCoverage": self.sweep_coverage.to_dict(),
            "gates": [r.to_dict() for r in self.results],
            "summary": self.summary(),
        }


# ---------------------------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------------------------


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _unevaluated(spec: GateSpec, reason: str, threshold: Any, **details: Any) -> GateResult:
    """The honesty rule, in one place. Absent input is never a pass."""
    return GateResult(
        id=spec.id,
        name=spec.name,
        status=STATUS_UNEVALUATED,
        criterion=spec.criterion,
        catches=spec.catches,
        threshold=threshold,
        measured=None,
        reason=reason,
        details=details,
    )


def _verdict(
    spec: GateSpec,
    ok: bool,
    threshold: Any,
    measured: Any,
    reason: str,
    warnings: Sequence[str] = (),
    **details: Any,
) -> GateResult:
    return GateResult(
        id=spec.id,
        name=spec.name,
        status=STATUS_PASS if ok else STATUS_FAIL,
        criterion=spec.criterion,
        catches=spec.catches,
        threshold=threshold,
        measured=measured,
        reason=reason,
        details=details,
        warnings=tuple(warnings),
    )


def _clip_roster(payload: Mapping[str, Any]) -> list[str] | None:
    """The authoritative list of clip names the harness was supposed to cover, or None.

    Taken from the sampled-clip payload's `clips` (CONTRACT_1.5.2.md) when present, else from a
    top-level `clipNames`. It is NEVER taken from the harness's own report: a roster derived from
    what the harness sampled makes "covered every clip" true by construction, which is precisely the
    hole G1 exists to close.
    """
    clips = payload.get("clips")
    if isinstance(clips, (list, tuple)) and clips:
        names: list[str] = []
        for index, clip in enumerate(clips):
            if isinstance(clip, Mapping) and isinstance(clip.get("sourceName"), str):
                names.append(clip["sourceName"])
            else:
                names.append(f"<clips[{index}] has no sourceName>")
        return names
    declared = payload.get("clipNames")
    if isinstance(declared, (list, tuple)) and declared and all(isinstance(n, str) for n in declared):
        return [str(n) for n in declared]
    return None


# ---------------------------------------------------------------------------------------------
# G1 — binding reaches node
# ---------------------------------------------------------------------------------------------


def _normalise_sampled_clips(raw: Any) -> dict[str, dict[str, Any]] | None:
    """Accept the three shapes a harness plausibly emits, or None if it is not one of them.

      {"walk": 5}                                   name -> sample count
      {"walk": {"sampleCount": 5, "maxSampledBindingDelta": 1e-9}}
      [{"sourceName": "walk", "sampleCount": 5, ...}]
    """
    entries: dict[str, dict[str, Any]] = {}
    if isinstance(raw, Mapping):
        for name, value in raw.items():
            if not isinstance(name, str):
                return None
            if isinstance(value, bool):
                return None
            if isinstance(value, int):
                entries[name] = {"sampleCount": value}
            elif isinstance(value, Mapping):
                entry = dict(value)
                entry.setdefault("sampleCount", value.get("sampleCount"))
                entries[name] = entry
            else:
                return None
        return entries
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if not isinstance(item, Mapping) or not isinstance(item.get("sourceName"), str):
                return None
            entries[item["sourceName"]] = dict(item)
        return entries
    return None


def gate_g1(payload: Mapping[str, Any]) -> GateResult:
    """maxSampledBindingDelta <= 2^-23 over >= 5 times x all clips.

    THIS IS THE GATE THAT CATCHES SILENT DEATH. A clip can exist, be loaded, hold an action and
    report a duration while driving nothing; comparing the node's actual transform against
    `track.createInterpolant().evaluate(t)` is the only check that tells the two apart.

    Which means the delta on its own proves nothing. A harness that sampled one clip at one time and
    reported 1e-12 has measured the one clip that was working. Coverage is therefore part of the
    criterion (ambiguity 1), and a thin sweep FAILS.
    """
    spec = GATE_SPEC_BY_ID["G1"]
    threshold = {
        "maxSampledBindingDelta": BINDING_EPSILON,
        "minimumSamplesPerClip": MINIMUM_GATE_R1_SAMPLES,
        "clipCoverage": "every clip in the roster",
    }
    block = payload.get(spec.input_key)
    if not isinstance(block, Mapping):
        return _unevaluated(
            spec,
            f"payload has no {spec.input_key!r} block; a three.js interpolant can only be evaluated "
            f"by the host harness, and an unrun harness is not a pass",
            threshold,
        )

    delta = block.get("maxSampledBindingDelta")
    if not _finite(delta):
        return _unevaluated(
            spec,
            f"{spec.input_key}.maxSampledBindingDelta is missing or not a finite number "
            f"(got {delta!r})",
            threshold,
        )
    delta = float(delta)

    roster = _clip_roster(payload)
    if roster is None:
        return _unevaluated(
            spec,
            "no independent clip roster: the payload carries neither a sampled-clip `clips` list "
            "nor a top-level `clipNames`, so \"covered every clip\" cannot be checked against "
            "anything the harness did not choose itself",
            threshold,
            maxSampledBindingDelta=delta,
        )

    sampled = _normalise_sampled_clips(block.get("clips"))
    if sampled is None:
        return _unevaluated(
            spec,
            f"{spec.input_key}.clips must be a per-clip sample-count map or list; without it the "
            f"reported delta has no coverage behind it",
            threshold,
            maxSampledBindingDelta=delta,
            clipsInRoster=len(roster),
        )

    missing = [name for name in roster if name not in sampled]
    thin: dict[str, int] = {}
    unreadable: list[str] = []
    for name in roster:
        entry = sampled.get(name)
        if entry is None:
            continue
        count = _positive_int(entry.get("sampleCount"))
        if count is None:
            unreadable.append(name)
        elif count < MINIMUM_GATE_R1_SAMPLES:
            thin[name] = count
    extra = [name for name in sampled if name not in roster]

    coverage = {
        "clipsInRoster": len(roster),
        "clipsSampled": len([n for n in roster if n in sampled]),
        "minimumSamplesPerClip": MINIMUM_GATE_R1_SAMPLES,
        "clipsMissing": missing,
        "clipsUnderSampled": thin,
        "clipsWithUnreadableSampleCount": unreadable,
        "clipsNotInRoster": extra,
    }
    measured = {"maxSampledBindingDelta": delta, "coverage": coverage}

    problems: list[str] = []
    if not (delta <= BINDING_EPSILON):  # not(<=) also catches NaN
        problems.append(
            f"maxSampledBindingDelta = {delta:.6e} exceeds float32 epsilon {BINDING_EPSILON:.6e}; "
            f"the binding path does not reach the node"
        )
    if missing:
        problems.append(
            f"the harness skipped {len(missing)} of {len(roster)} clips ({', '.join(missing)}); "
            f"a clip nobody sampled is exactly the clip that plays silently"
        )
    if thin:
        listed = ", ".join(f"{name} sampled {count}x" for name, count in sorted(thin.items()))
        problems.append(
            f"{len(thin)} clip(s) sampled fewer than {MINIMUM_GATE_R1_SAMPLES} times ({listed}); "
            f"under-coverage is a failure, not a pass"
        )
    if unreadable:
        problems.append(
            f"{len(unreadable)} clip(s) report no readable sampleCount ({', '.join(unreadable)})"
        )

    warnings = []
    if extra:
        warnings.append(
            f"harness sampled {len(extra)} clip(s) absent from the roster: {', '.join(extra)}"
        )

    if problems:
        return _verdict(spec, False, threshold, measured, "; ".join(problems), warnings)
    return _verdict(
        spec,
        True,
        threshold,
        measured,
        f"maxSampledBindingDelta = {delta:.3e} <= {BINDING_EPSILON:.3e} over "
        f"{len(roster)} clip(s), each sampled at least {MINIMUM_GATE_R1_SAMPLES} times",
        warnings,
    )


# ---------------------------------------------------------------------------------------------
# G2 — deformation finite
# ---------------------------------------------------------------------------------------------


def _per_frame_counts(entry: Any) -> tuple[int | None, list[int] | None]:
    """Return (frames, per-frame vertex counts) from a mesh entry, or (None, None) if unreadable.

    `verticesPerFrame` given as one number is read as the MINIMUM across that mesh's frames -- the
    only reading under which a single number can support a per-frame claim (ambiguity 2).
    """
    if not isinstance(entry, Mapping):
        return None, None
    frames = _positive_int(entry.get("frames"))
    vertices = entry.get("verticesPerFrame")
    if isinstance(vertices, (list, tuple)):
        counts = [_positive_int(v) for v in vertices]
        if any(c is None for c in counts):
            return frames, None
        resolved = [int(c) for c in counts if c is not None]
        if frames is None:
            frames = len(resolved)
        return frames, resolved
    single = _positive_int(vertices)
    if single is None or frames is None:
        return frames, None
    return frames, [single] * frames


def gate_g2(payload: Mapping[str, Any]) -> GateResult:
    """applyBoneTransform on >= 64 vertices/mesh/frame, all finite.

    Two separate claims, and the gate needs both: a NaN weight or an out-of-range index sends one
    vertex to infinity, and a harness that only transformed 8 vertices of a 20k mesh has not looked
    where that vertex is. Under-sampled coverage FAILS (ambiguity 2).
    """
    spec = GATE_SPEC_BY_ID["G2"]
    threshold = {
        "allFinite": True,
        "minimumVerticesPerMeshPerFrame": MINIMUM_G2_VERTICES,
        "minimumFramesPerMesh": 1,
    }
    block = payload.get(spec.input_key)
    if not isinstance(block, Mapping):
        return _unevaluated(
            spec,
            f"payload has no {spec.input_key!r} block; applyBoneTransform runs in the host, and an "
            f"unrun harness is not a pass",
            threshold,
        )

    non_finite = _positive_int(block.get("nonFiniteCount"))
    all_finite = block.get("allFinite")
    if non_finite is None and not isinstance(all_finite, bool):
        return _unevaluated(
            spec,
            f"{spec.input_key} reports neither `nonFiniteCount` nor a boolean `allFinite`; there is "
            f"no finiteness claim to check",
            threshold,
        )

    meshes = block.get("meshes")
    if not isinstance(meshes, Mapping) or not meshes:
        return _unevaluated(
            spec,
            f"{spec.input_key}.meshes must be a non-empty per-mesh coverage map; a finiteness claim "
            f"with no coverage behind it says nothing about the mesh nobody transformed",
            threshold,
            nonFiniteCount=non_finite,
        )

    per_mesh: dict[str, Any] = {}
    thin: list[str] = []
    unreadable: list[str] = []
    for name, entry in meshes.items():
        frames, counts = _per_frame_counts(entry)
        if counts is None or frames is None:
            unreadable.append(str(name))
            per_mesh[str(name)] = {"frames": frames, "minVerticesPerFrame": None}
            continue
        smallest = min(counts) if counts else 0
        per_mesh[str(name)] = {"frames": frames, "minVerticesPerFrame": smallest}
        if frames < 1:
            thin.append(f"{name} transformed over 0 frames")
        elif smallest < MINIMUM_G2_VERTICES:
            thin.append(
                f"{name} transformed {smallest} vertices in its thinnest frame "
                f"(< {MINIMUM_G2_VERTICES})"
            )

    measured = {
        "nonFiniteCount": non_finite,
        "allFinite": all_finite,
        "meshes": per_mesh,
        "meshCount": len(meshes),
    }

    problems: list[str] = []
    if non_finite is not None and non_finite > 0:
        problems.append(
            f"{non_finite} non-finite deformed vertex value(s); a bad skin index reads a garbage "
            f"matrix and a NaN weight propagates to every vertex it touches"
        )
    if all_finite is False:
        problems.append("harness reports allFinite = false")
    if thin:
        problems.append(
            f"vertex coverage below {MINIMUM_G2_VERTICES}/mesh/frame: " + "; ".join(thin)
        )
    if unreadable:
        problems.append(
            f"{len(unreadable)} mesh entr(ies) report no readable frames/verticesPerFrame: "
            + ", ".join(unreadable)
        )

    if problems:
        return _verdict(spec, False, threshold, measured, "; ".join(problems))
    return _verdict(
        spec,
        True,
        threshold,
        measured,
        f"all deformed values finite over {len(meshes)} mesh(es), each at least "
        f"{MINIMUM_G2_VERTICES} vertices per frame",
    )


# ---------------------------------------------------------------------------------------------
# G3 — bind restore
# ---------------------------------------------------------------------------------------------


def gate_g3(payload: Mapping[str, Any]) -> GateResult:
    """maxBindRestoreDelta <= 1e-12 after stop().

    Restoring the bind pose before each play is what stops clips bleeding into one another: a clip
    that ends mid-pose leaves joints displaced, and the next clip's tracks only overwrite the joints
    they address. The residue is invisible until a clip that never touches the shoulder plays after
    one that did.
    """
    spec = GATE_SPEC_BY_ID["G3"]
    threshold = BIND_RESTORE_TOLERANCE
    block = payload.get(spec.input_key)
    if isinstance(block, Mapping):
        delta = block.get("maxBindRestoreDelta")
    elif _finite(block):
        delta = block
    else:
        delta = None
    if not _finite(delta):
        return _unevaluated(
            spec,
            f"payload has no finite {spec.input_key}.maxBindRestoreDelta; the delta is measured in "
            f"the host after stop(), and an unrun harness is not a pass",
            threshold,
        )
    delta = float(delta)
    if not (delta <= BIND_RESTORE_TOLERANCE):  # not(<=) also catches NaN
        return _verdict(
            spec,
            False,
            threshold,
            delta,
            f"maxBindRestoreDelta = {delta:.6e} exceeds {BIND_RESTORE_TOLERANCE:.1e}; joints are "
            f"still displaced after stop(), so the next clip starts from the last one's pose",
        )
    return _verdict(
        spec,
        True,
        threshold,
        delta,
        f"maxBindRestoreDelta = {delta:.3e} <= {BIND_RESTORE_TOLERANCE:.1e}; the bind pose is "
        f"restored exactly",
    )


# ---------------------------------------------------------------------------------------------
# G4 / G5 — delegated to skin_conditioning.validate_binding (Gate R2)
# ---------------------------------------------------------------------------------------------

_SUMMARY_PATTERN = re.compile(
    r"\.\.\.\s+(\d+)\s+weight-sum failures \(worst ([^)]+)\) and (\d+) index-range failures"
)


@dataclass(frozen=True)
class _BindingVerdict:
    """One `validate_binding` run, split into the two gates that consume it.

    Structural failures (wrong array lengths, joint_count < 1) belong to BOTH gates: on a malformed
    binding neither the weight sums nor the index range has been checked, so neither may be called
    a pass.
    """

    binding: SkinBinding | None
    error: str | None
    weight_failures: tuple[str, ...] = ()
    index_failures: tuple[str, ...] = ()
    structural_failures: tuple[str, ...] = ()
    weight_failure_count: int = 0
    index_failure_count: int = 0
    worst_sum_error: float | None = None


def _evaluate_binding(payload: Mapping[str, Any]) -> _BindingVerdict | None:
    """Run Gate R2 once and partition its messages. Returns None when there is no binding at all."""
    raw = payload.get("binding")
    if raw is None:
        return None
    if isinstance(raw, SkinBinding):
        binding = raw
    elif isinstance(raw, Mapping):
        try:
            binding = SkinBinding.from_dict(dict(raw))
        except Exception as exc:
            return _BindingVerdict(binding=None, error=f"binding payload is unreadable: {exc}")
    else:
        return _BindingVerdict(
            binding=None,
            error=f"binding must be an object or a SkinBinding; got {type(raw).__name__}",
        )

    failures = validate_binding(binding)

    weight: list[str] = []
    index: list[str] = []
    structural: list[str] = []
    weight_count = 0
    index_count = 0
    worst: float | None = None

    for message in failures:
        summary = _SUMMARY_PATTERN.search(message)
        if summary:
            weight_count = max(weight_count, int(summary.group(1)))
            index_count = max(index_count, int(summary.group(3)))
            try:
                worst = float(summary.group(2))
            except ValueError:
                worst = None
            # The summary line carries both counts, so both gates get to quote it.
            weight.append(message)
            index.append(message)
        elif "sum(w)" in message:
            weight.append(message)
        elif "skin index" in message:
            index.append(message)
        else:
            structural.append(message)

    weight_count = max(weight_count, sum(1 for m in weight if "sum(w)" in m))
    index_count = max(index_count, sum(1 for m in index if "skin index" in m))

    return _BindingVerdict(
        binding=binding,
        error=None,
        weight_failures=tuple(weight),
        index_failures=tuple(index),
        structural_failures=tuple(structural),
        weight_failure_count=weight_count,
        index_failure_count=index_count,
        worst_sum_error=worst,
    )


def gate_g4(payload: Mapping[str, Any], verdict: _BindingVerdict | None = None) -> GateResult:
    """|1 - sum(w)| <= 2e-7 every vertex. Delegated in full to `skin_conditioning.validate_binding`.

    The check is Gate R2's and stays there. Duplicating it here would let the gate and the stage
    that produces the weights drift apart, and the first symptom of that drift would be a limb that
    shrinks slightly every frame while both files claim to be correct.
    """
    spec = GATE_SPEC_BY_ID["G4"]
    threshold = WEIGHT_SUM_TOLERANCE
    if verdict is None:
        verdict = _evaluate_binding(payload)
    if verdict is None:
        return _unevaluated(
            spec,
            "payload has no `binding`; weight normalisation cannot be checked without the weights",
            threshold,
        )
    if verdict.error is not None:
        return _unevaluated(spec, verdict.error, threshold)

    messages = list(verdict.structural_failures) + list(verdict.weight_failures)
    measured = {
        "verticesOutOfTolerance": verdict.weight_failure_count,
        "worstAbsSumError": verdict.worst_sum_error,
        "vertexCount": verdict.binding.vertex_count if verdict.binding else None,
    }
    if messages:
        return _verdict(
            spec,
            False,
            threshold,
            measured,
            "skin_conditioning.validate_binding rejected the binding: " + messages[0],
            delegatedFailures=messages,
            delegatedTo="skin_conditioning.validate_binding",
        )
    return _verdict(
        spec,
        True,
        threshold,
        measured,
        f"skin_conditioning.validate_binding found no weight-sum failure across "
        f"{measured['vertexCount']} vertices",
        delegatedTo="skin_conditioning.validate_binding",
    )


def gate_g5(payload: Mapping[str, Any], verdict: _BindingVerdict | None = None) -> GateResult:
    """maxSkinIndex <= bones - 1. Delegated in full to `skin_conditioning.validate_binding`.

    The index check lives after the top-4 reduction because the reduction rewrites the indices:
    being in range before it says nothing about being in range after.
    """
    spec = GATE_SPEC_BY_ID["G5"]
    if verdict is None:
        verdict = _evaluate_binding(payload)
    if verdict is None:
        return _unevaluated(
            spec,
            "payload has no `binding`; skin indices cannot be range-checked without them",
            {"maxSkinIndex": "bones - 1"},
        )
    if verdict.error is not None:
        return _unevaluated(spec, verdict.error, {"maxSkinIndex": "bones - 1"})

    binding = verdict.binding
    bones = binding.joint_count if binding else None
    threshold = {"maxSkinIndex": (bones - 1) if isinstance(bones, int) else "bones - 1"}
    max_index = max(binding.skin_indices) if binding and binding.skin_indices else None
    measured = {
        "maxSkinIndex": max_index,
        "boneCount": bones,
        "slotsOutOfRange": verdict.index_failure_count,
    }

    messages = list(verdict.structural_failures) + list(verdict.index_failures)
    if messages:
        return _verdict(
            spec,
            False,
            threshold,
            measured,
            "skin_conditioning.validate_binding rejected the binding: " + messages[0],
            delegatedFailures=messages,
            delegatedTo="skin_conditioning.validate_binding",
        )
    return _verdict(
        spec,
        True,
        threshold,
        measured,
        f"maxSkinIndex = {max_index} <= {bones - 1 if isinstance(bones, int) else '?'}",
        delegatedTo="skin_conditioning.validate_binding",
    )


# ---------------------------------------------------------------------------------------------
# G6 — every visible mesh bound
# ---------------------------------------------------------------------------------------------


def gate_g6(payload: Mapping[str, Any]) -> GateResult:
    """visibleMeshCount == visibleSkinnedMeshCount.

    Catches a part left behind in bind pose: the figure animates, one shoulder pad does not, and
    nothing about the frame looks broken until the arm moves far enough for the pad to be obviously
    detached. Equality is the criterion as written, so more skinned than visible fails too
    (ambiguity 6) -- the two counts disagree and one of them is wrong either way.
    """
    spec = GATE_SPEC_BY_ID["G6"]
    threshold = "visibleMeshCount == visibleSkinnedMeshCount"
    block = payload.get(spec.input_key)
    if not isinstance(block, Mapping):
        return _unevaluated(
            spec,
            f"payload has no {spec.input_key!r} block; the scene graph is walked in the host, and "
            f"an unwalked scene is not a pass",
            threshold,
        )
    visible = _positive_int(block.get("visibleMeshCount"))
    skinned = _positive_int(block.get("visibleSkinnedMeshCount"))
    if visible is None or skinned is None:
        return _unevaluated(
            spec,
            f"{spec.input_key} needs both visibleMeshCount and visibleSkinnedMeshCount as "
            f"non-negative integers; got {block.get('visibleMeshCount')!r} and "
            f"{block.get('visibleSkinnedMeshCount')!r}",
            threshold,
        )
    unbound = block.get("unboundMeshes")
    named = [str(n) for n in unbound] if isinstance(unbound, (list, tuple)) else []
    measured = {
        "visibleMeshCount": visible,
        "visibleSkinnedMeshCount": skinned,
        "unboundMeshes": named,
    }
    if visible != skinned:
        detail = f" ({', '.join(named)})" if named else ""
        return _verdict(
            spec,
            False,
            threshold,
            measured,
            f"{visible} visible mesh(es) but {skinned} skinned{detail}; "
            f"{abs(visible - skinned)} mesh(es) unaccounted for",
        )
    if named:
        return _verdict(
            spec,
            False,
            threshold,
            measured,
            f"counts agree at {visible} but the harness also names unbound meshes "
            f"({', '.join(named)}); the counts and the list contradict each other",
        )
    return _verdict(
        spec, True, threshold, measured, f"all {visible} visible mesh(es) are skinned"
    )


# ---------------------------------------------------------------------------------------------
# G9 — no joint scale (delegated measurement: clip_features.measure_clip)
# ---------------------------------------------------------------------------------------------


def gate_g9(payload: Mapping[str, Any]) -> GateResult:
    """scaleDelta == 0 unless the source has it.

    scaleDelta is a tripwire, not a descriptor. A non-zero value means the source rig scales joints,
    which changes what Stage R2 may legally do to skin weights -- proximity blending averages two
    parts' bindings, and averaging across a scaled joint invalidates the blend rather than softening
    it. The "unless" clause must be DECLARED (`sourceScalesJoints: true`) and then produces a pass
    carrying a warning that names the clips (ambiguity 5). It is never a silent pass.
    """
    spec = GATE_SPEC_BY_ID["G9"]
    declared = payload.get("sourceScalesJoints") is True
    threshold = {"scaleDelta": 0.0, "sourceScalesJointsDeclared": declared}

    clips = payload.get("clips")
    if not isinstance(clips, (list, tuple)) or not clips:
        return _unevaluated(
            spec,
            "payload has no sampled-clip `clips` list; scaleDelta is measured from the clips and "
            "an absent one cannot be assumed to be zero",
            threshold,
        )
    height = payload.get("figureHeight")
    if not _finite(height) or float(height) <= 0.0:
        return _unevaluated(
            spec,
            f"payload.figureHeight must be a finite positive number to measure clips; got {height!r}",
            threshold,
        )

    per_clip: dict[str, float] = {}
    try:
        for clip in clips:
            features = measure_clip(clip, float(height))
            per_clip[features.source_name] = features.scale_delta
    except Exception as exc:
        return _unevaluated(
            spec,
            f"clip_features.measure_clip could not measure the payload: {exc}",
            threshold,
        )

    scaling = {name: value for name, value in per_clip.items() if value > 0.0}
    worst = max(per_clip.values()) if per_clip else 0.0
    measured = {"maxScaleDelta": worst, "scaleDeltaByClip": per_clip, "clipsScaling": list(scaling)}

    if not scaling:
        return _verdict(
            spec,
            True,
            threshold,
            measured,
            f"scaleDelta == 0 on all {len(per_clip)} clip(s); no joint is scaled",
        )
    listed = ", ".join(f"{name} ({value:.6g})" for name, value in sorted(scaling.items()))
    if declared:
        return _verdict(
            spec,
            True,
            threshold,
            measured,
            f"pass with warning: {len(scaling)} clip(s) scale joints and the payload declares "
            f"sourceScalesJoints = true",
            warnings=[
                f"source rig scales joints on {len(scaling)} clip(s): {listed}. This is admitted, "
                f"not measured away -- Stage R2's proximity blend averages bindings across parts "
                f"and that averaging is invalidated by a scaled joint. Whoever runs R2 must be told."
            ],
        )
    return _verdict(
        spec,
        False,
        threshold,
        measured,
        f"scaleDelta > 0 on {len(scaling)} clip(s): {listed}. Nothing declares "
        f"sourceScalesJoints, so this is an undeclared joint scale and Stage R2's weight blend "
        f"cannot be trusted on this rig",
    )


# ---------------------------------------------------------------------------------------------
# G7 — medial/lateral (delegated to action_design.check_medial_lateral)
# ---------------------------------------------------------------------------------------------


def gate_g7(payload: Mapping[str, Any]) -> GateResult:
    """leftAnchor.x > 0 > rightAnchor.x. Delegated to `action_design.check_medial_lateral`.

    One comparison catches a mirrored rig, the failure where every left-hand action plays on the
    right and nothing about it looks wrong in isolation.

    AMBIGUITY 8 -- THE TAUTOLOGY. `action_design.Chains.gate_is_independent` records something the
    R6 table does not: when the side labels were assigned by "left = +X" rather than supplied from
    outside the geometry, the gate is comparing the geometry against a claim derived from that same
    geometry, and the mirrored-rig case is unreachable. READING: an errorless run of a
    non-independent gate is reported `unevaluated`, not `pass`, because a pass that could not have
    failed is exactly the silent pass this module exists to prevent. A non-empty error list is still
    a `fail` -- a degenerate pair is a real finding either way.
    """
    spec = GATE_SPEC_BY_ID["G7"]
    threshold = "leftAnchor.x > 0 > rightAnchor.x"
    block = payload.get(spec.input_key)
    if isinstance(block, Chains):
        chains: Chains | None = block
        medial_tolerance = None
    elif isinstance(block, Mapping):
        joints = block.get("joints")
        if not isinstance(joints, (list, tuple)) or not joints:
            return _unevaluated(
                spec,
                f"{spec.input_key}.joints must be a non-empty joint hierarchy; chains are resolved "
                f"from topology (R4) and there is nothing here to resolve",
                threshold,
            )
        side_labels = block.get("sideLabels")
        medial_tolerance = block.get("medialTolerance")
        kwargs: dict[str, Any] = {}
        if isinstance(side_labels, Mapping):
            kwargs["side_labels"] = {str(k): str(v) for k, v in side_labels.items()}
        if _finite(block.get("symmetryTolerance")):
            kwargs["symmetry_tolerance"] = float(block["symmetryTolerance"])
        try:
            chains = resolve_chains(joints, **kwargs)
        except ChainResolutionError as exc:
            return _verdict(
                spec,
                False,
                threshold,
                None,
                f"action_design.resolve_chains could not resolve the chains: {exc}. R4 fails closed "
                f"rather than guessing, and an unresolvable hierarchy cannot be gated",
                delegatedTo="action_design.resolve_chains",
            )
    else:
        return _unevaluated(
            spec,
            f"payload has no {spec.input_key!r} block (a joint hierarchy, optionally with "
            f"sideLabels); the medial/lateral convention cannot be checked without anchors",
            threshold,
        )

    tolerance = float(medial_tolerance) if _finite(medial_tolerance) else MEDIAL_AXIS_TOLERANCE
    errors = check_medial_lateral(chains, medial_tolerance=tolerance)
    measured = {
        "armLeftAnchorX": chains.arm["l"].anchor_position[0],
        "armRightAnchorX": chains.arm["r"].anchor_position[0],
        "legLeftAnchorX": chains.leg["l"].anchor_position[0],
        "legRightAnchorX": chains.leg["r"].anchor_position[0],
        "sideSource": chains.side_source,
        "gateIsIndependent": chains.gate_is_independent,
    }
    if errors:
        return _verdict(
            spec,
            False,
            threshold,
            measured,
            "action_design.check_medial_lateral rejected the rig: " + errors[0],
            delegatedFailures=errors,
            delegatedTo="action_design.check_medial_lateral",
        )
    if not chains.gate_is_independent:
        return _unevaluated(
            spec,
            f"chains resolved with side_source={chains.side_source!r}: the left/right labels were "
            f"assigned from model X, the same geometry the gate compares them against, so a "
            f"mirrored rig could not have been caught. Supply `sideLabels` (the source rig's own "
            f"naming, which is the claim a mirrored rig gets wrong) to give the gate teeth",
            threshold,
            **measured,
        )
    return _verdict(
        spec,
        True,
        threshold,
        measured,
        f"left anchors are +X and right anchors are -X, checked against an independent side-label "
        f"claim (side_source={chains.side_source!r})",
        delegatedTo="action_design.check_medial_lateral",
    )


# ---------------------------------------------------------------------------------------------
# G8 — foot contact (delegated to action_design.foot_slide)
# ---------------------------------------------------------------------------------------------


def gate_g8(payload: Mapping[str, Any]) -> GateResult:
    """footSlide <= 0.01H in stance. Delegated per clip to `action_design.foot_slide`.

    "The single most valuable gate in the stage": a skating gait reads as floaty to every viewer
    and almost nobody can name what is wrong with it, so it survives review and playtest notes and
    is fixed only when somebody finally puts a number on the stance frames.

    Positions are the payload's WORLD landmark positions, which is the frame `foot_slide` requires.
    Handing it a hip-relative clip inverts the measurement -- with the root's planar motion removed,
    a correctly planted foot slides backwards by exactly `travel` per cycle and the gate rejects the
    one gait that was right.

    Clips carrying no `stance` are recorded and warned about rather than failed: this module cannot
    tell a gait clip from a gesture clip without re-classifying, and failing every non-gait clip for
    lacking stance would make the gate unusable. The warning is there so the omission is visible.
    """
    spec = GATE_SPEC_BY_ID["G8"]
    threshold = {"footSlide": FOOT_SLIDE_LIMIT, "units": "fraction of figure height H"}

    clips = payload.get("clips")
    if not isinstance(clips, (list, tuple)) or not clips:
        return _unevaluated(
            spec,
            "payload has no sampled-clip `clips` list; footSlide is measured on stance frames and "
            "there are none",
            threshold,
        )
    height = payload.get("figureHeight")
    if not _finite(height) or float(height) <= 0.0:
        return _unevaluated(
            spec,
            f"payload.figureHeight must be a finite positive number; got {height!r}",
            threshold,
        )
    height = float(height)

    per_clip: dict[str, Any] = {}
    without_stance: list[str] = []
    failing: list[str] = []
    unmeasured: list[str] = []
    worst_value: float | None = None
    worst_clip: str | None = None

    for index, clip in enumerate(clips):
        if not isinstance(clip, Mapping):
            unmeasured.append(f"clips[{index}] is not an object")
            continue
        name = str(clip.get("sourceName", f"clips[{index}]"))
        stance = clip.get("stance")
        if not isinstance(stance, Mapping) or not stance:
            without_stance.append(name)
            continue
        positions = clip.get("landmarkPositions")
        times = clip.get("sampleTimes")
        if not isinstance(positions, Mapping) or not isinstance(times, (list, tuple)):
            unmeasured.append(f"{name}: stance declared but landmarkPositions/sampleTimes missing")
            continue
        try:
            report = foot_slide(positions, times, stance, height, limit=FOOT_SLIDE_LIMIT)
        except Exception as exc:
            unmeasured.append(f"{name}: action_design.foot_slide could not measure it: {exc}")
            continue
        per_clip[name] = report.to_dict()
        if report.max_slide_fraction is not None:
            if worst_value is None or report.max_slide_fraction > worst_value:
                worst_value = report.max_slide_fraction
                worst_clip = name
        if report.status == STATUS_FAIL:
            failing.append(f"{name}: {report.reason}")
        elif report.status == STATUS_UNEVALUATED:
            unmeasured.append(f"{name}: {report.reason}")

    measured = {
        "maxFootSlideFraction": worst_value,
        "worstClip": worst_clip,
        "clipsMeasured": len(per_clip),
        "perClip": per_clip,
    }
    warnings: list[str] = []
    if without_stance:
        warnings.append(
            f"{len(without_stance)} clip(s) declare no stance intervals and were not gated: "
            + ", ".join(without_stance)
            + ". A gait among them would escape G8 entirely"
        )
    if unmeasured:
        warnings.append("unmeasurable stance data: " + "; ".join(unmeasured))

    if not per_clip:
        return _unevaluated(
            spec,
            "no clip carried a measurable stance interval; Gate G8 with no stance data is not a "
            "pass, it is an unasked question"
            + (f" ({'; '.join(unmeasured)})" if unmeasured else "")
            + (f" [clips without stance: {', '.join(without_stance)}]" if without_stance else ""),
            threshold,
            **{"perClip": per_clip, "clipsWithoutStance": without_stance},
        )
    if failing:
        return _verdict(
            spec,
            False,
            threshold,
            measured,
            f"{len(failing)} clip(s) skate: " + "; ".join(failing),
            warnings,
            clipsWithoutStance=without_stance,
            delegatedTo="action_design.foot_slide",
        )
    return _verdict(
        spec,
        True,
        threshold,
        measured,
        f"footSlide {worst_value:.6g}H <= {FOOT_SLIDE_LIMIT:.6g}H across {len(per_clip)} clip(s)"
        if worst_value is not None
        else f"footSlide within {FOOT_SLIDE_LIMIT:.6g}H across {len(per_clip)} clip(s)",
        warnings,
        clipsWithoutStance=without_stance,
        delegatedTo="action_design.foot_slide",
    )


# ---------------------------------------------------------------------------------------------
# G10 — skin integrity sweep
# ---------------------------------------------------------------------------------------------

_SWEEP_AXES = ("clips", "times", "sides", "azimuths")


def gate_g10(
    payload: Mapping[str, Any], coverage: SweepCoverage = DEFAULT_SWEEP_COVERAGE
) -> GateResult:
    """background-through-split <= measured baseline, over a sweep that is actually a sweep.

    FIVE POSES IS NOT COVERAGE. The spec says so in as many words, and the number behind it is
    concrete: coincidence-welding looked correct until a sweep of 11 clips x 4 times x 3 azimuths =
    132 frames found cracks in 28 of them. A sweep is a claim about the frames nobody looked at, so
    the per-axis breakdown is required and a thin axis FAILS however clean its hole count is
    (ambiguity 3).

    HOLES AND CREASES ARE TWO NUMBERS, ALWAYS. Stage R2 trades one against the other on purpose --
    proximity blending took holes from 974 px to 287 px and pushed creases from 31,316 px to 36,470,
    accepted because a hole shows the background and a crease shows skin. A single combined score
    hides that trade and invites a later stage to "fix" the crease count by disabling the blend.
    Creases are therefore REPORTED and never gated here; gating them would ask for exactly that.
    """
    spec = GATE_SPEC_BY_ID["G10"]
    block = payload.get(spec.input_key)
    roster = _clip_roster(payload)
    roster_count = len(roster) if roster is not None else None
    threshold: dict[str, Any] = {
        "backgroundThroughSplitPx": "<= measured baseline",
        "creasePx": "reported, never gated (see the R2 trade)",
        "minimumAxes": coverage.to_dict(),
        "clipsInRoster": roster_count,
    }
    if not isinstance(block, Mapping):
        return _unevaluated(
            spec,
            f"payload has no {spec.input_key!r} block; G10 is a visual gate and needs a render "
            f"harness. An unrun sweep is not a clean sweep",
            threshold,
        )

    axes: dict[str, int] = {}
    missing_axes: list[str] = []
    for axis in _SWEEP_AXES:
        value = _positive_int(block.get(axis))
        if value is None:
            missing_axes.append(axis)
        else:
            axes[axis] = value
    frames = _positive_int(block.get("frames"))

    if missing_axes or frames is None:
        return _unevaluated(
            spec,
            f"the sweep declares no usable "
            + (f"frame count; " if frames is None else "")
            + (f"per-axis breakdown (missing {', '.join(missing_axes)}); " if missing_axes else "")
            + "a bare total cannot show which axis was skipped, and five poses is not coverage",
            threshold,
            declaredFrames=frames,
            declaredAxes=axes,
        )

    holes = block.get("backgroundThroughSplitPx")
    creases = block.get("creasePx")
    baseline = block.get("baseline")
    baseline_holes = (
        baseline.get("backgroundThroughSplitPx") if isinstance(baseline, Mapping) else None
    )
    baseline_creases = baseline.get("creasePx") if isinstance(baseline, Mapping) else None

    if not _finite(holes):
        return _unevaluated(
            spec,
            f"{spec.input_key}.backgroundThroughSplitPx is missing or not a finite number "
            f"(got {holes!r}); there is no measurement to compare",
            threshold,
            declaredFrames=frames,
            declaredAxes=axes,
        )
    if not _finite(baseline_holes):
        return _unevaluated(
            spec,
            f"{spec.input_key}.baseline.backgroundThroughSplitPx is missing; the criterion is "
            f"\"<= measured baseline\" and the baseline is the control run with the blend disabled. "
            f"Borrowing another subject's number is not a measurement of this one",
            threshold,
            declaredFrames=frames,
            declaredAxes=axes,
            backgroundThroughSplitPx=float(holes),
        )

    product = axes["clips"] * axes["times"] * axes["sides"] * axes["azimuths"]
    clip_axis_target = roster_count if roster_count is not None else axes["clips"]
    required_frames = coverage.required_frames(clip_axis_target)

    measured = {
        "backgroundThroughSplitPx": float(holes),
        "backgroundThroughSplitBlobs": block.get("backgroundThroughSplitBlobs"),
        "creasePx": float(creases) if _finite(creases) else None,
        "baselineBackgroundThroughSplitPx": float(baseline_holes),
        "baselineCreasePx": float(baseline_creases) if _finite(baseline_creases) else None,
        "frames": frames,
        "axes": dict(axes),
        "requiredFrames": required_frames,
        "note": (
            "holes and creases are two numbers on purpose; Stage R2 trades one against the other "
            "and a single combined score hides that"
        ),
    }

    problems: list[str] = []
    if frames != product:
        problems.append(
            f"the sweep declares {frames} frames but its axes multiply to {product} "
            f"({axes['clips']} clips x {axes['times']} times x {axes['sides']} sides x "
            f"{axes['azimuths']} azimuths); one of the two numbers is wrong"
        )
    thin_axes = []
    if axes["times"] < coverage.times:
        thin_axes.append(f"{axes['times']} times (< {coverage.times})")
    if axes["sides"] < coverage.sides:
        thin_axes.append(f"{axes['sides']} sides (< {coverage.sides})")
    if axes["azimuths"] < coverage.azimuths:
        thin_axes.append(f"{axes['azimuths']} azimuths (< {coverage.azimuths})")
    if thin_axes:
        problems.append(
            "sweep axes too thin: " + ", ".join(thin_axes) + " — five poses is not coverage"
        )
    if roster_count is not None and axes["clips"] < roster_count:
        problems.append(
            f"the sweep covered {axes['clips']} of {roster_count} clip(s); the frames that tear are "
            f"in the clips nobody rendered"
        )
    if frames < required_frames:
        problems.append(
            f"{frames} frames is materially thinner than the {required_frames} its declared clip "
            f"set allows ({clip_axis_target} clips x {coverage.times} times x {coverage.sides} "
            f"sides x {coverage.azimuths} azimuths)"
        )
    if not (float(holes) <= float(baseline_holes)):  # not(<=) also catches NaN
        problems.append(
            f"background through splits {float(holes):.6g} px exceeds the measured baseline "
            f"{float(baseline_holes):.6g} px; the skin tears under motion"
        )

    warnings: list[str] = []
    if not _finite(creases):
        warnings.append(
            "no creasePx reported. R2 trades holes against creases deliberately and both numbers "
            "have to be visible, or a later stage will 'fix' one by undoing the other"
        )
    elif _finite(baseline_creases) and float(creases) > float(baseline_creases):
        warnings.append(
            f"creases rose from {float(baseline_creases):.6g} px to {float(creases):.6g} px "
            f"(+{100.0 * (float(creases) / float(baseline_creases) - 1.0):.1f}%). This is the R2 "
            f"trade, accepted on purpose — a hole shows the background, a crease shows skin — and "
            f"it is reported, not gated. Do not 'fix' it by disabling the blend"
        )

    if problems:
        return _verdict(spec, False, threshold, measured, "; ".join(problems), warnings)
    return _verdict(
        spec,
        True,
        threshold,
        measured,
        f"background through splits {float(holes):.6g} px <= baseline "
        f"{float(baseline_holes):.6g} px over {frames} frames "
        f"({axes['clips']}x{axes['times']}x{axes['sides']}x{axes['azimuths']})",
        warnings,
    )


# ---------------------------------------------------------------------------------------------
# G11 — mesh parity (delegated to mesh_parity.verify)
# ---------------------------------------------------------------------------------------------


def gate_g11(payload: Mapping[str, Any]) -> GateResult:
    """Every frozen buffer byte-identical after binding. Delegated to `mesh_parity.verify`.

    Rigging an already-built model is meant to be ADDITIVE: a skeleton appears, two vertex
    attributes appear, some clips appear. Nothing about it requires touching a position, a normal,
    a uv or an index. The animation pass is nevertheless exactly where meshes come back disjointed,
    and by the time the damage shows it is buried under a rig built on top of it — so it is no
    longer clear whether the mesh or the skinning is at fault. This gate answers that question
    before it has to be asked.

    THIS GATE ADDS NO PARITY RULE OF ITS OWN (ambiguity 10). `mesh_parity` decides what counts as a
    change, including the manifest/payload disagreements about which meshes exist; re-deriving any
    of it here is how a gate and the stage it gates start disagreeing about what "byte-identical"
    meant. What this function owns is the verdict mapping and carrying the detail through.

    AND IT MUST NOT FAIL A SUCCESSFUL RIG (ambiguity 11). `skinIndex` and `skinWeight` sit outside
    FROZEN_BUFFERS on purpose — adding them is the whole legal purpose of the implementation phase.
    They are reported in `measured.addedLegal` and never contribute to the verdict.
    """
    spec = GATE_SPEC_BY_ID["G11"]
    threshold = {
        "frozenBuffers": list(mesh_parity.FROZEN_BUFFERS),
        "criterion": "byte-identical over packed float64/int64",
        "notFrozen": ["skinIndex", "skinWeight"],
    }
    block = payload.get(spec.input_key)
    if not isinstance(block, Mapping):
        return _unevaluated(
            spec,
            f"payload has no {spec.input_key!r} block; geometry parity needs both a freeze manifest "
            f"and the post-bind buffers, and unproven is not the same as unbroken",
            threshold,
        )

    raw_manifest = block.get("manifest")
    after = block.get("after")
    missing: list[str] = []
    if not isinstance(raw_manifest, Mapping):
        missing.append("`manifest` (the freeze record taken before rigging)")
    if not isinstance(after, Mapping):
        missing.append("`after` (the post-bind buffer payload)")
    if missing:
        return _unevaluated(
            spec,
            f"{spec.input_key} is missing {' and '.join(missing)}; parity is a comparison and needs "
            f"both halves. Geometry parity is unproven, which is not the same as unbroken",
            threshold,
            manifestPresent=isinstance(raw_manifest, Mapping),
            afterPresent=isinstance(after, Mapping),
        )

    try:
        manifest = mesh_parity.Manifest.from_dict(dict(raw_manifest))
    except Exception as exc:
        return _unevaluated(
            spec,
            f"the freeze manifest is unreadable: {exc}. A manifest that cannot be parsed proves "
            f"nothing about the geometry either way",
            threshold,
        )
    try:
        report = mesh_parity.verify(manifest, dict(after))
    except Exception as exc:
        return _unevaluated(
            spec,
            f"the post-bind buffer payload is unreadable: {exc}",
            threshold,
        )

    measured = {
        "meshesFrozen": report.meshes_frozen,
        "meshesCompared": report.meshes_compared,
        "failureCount": report.failure_count,
        "failures": [failure.to_dict() for failure in report.failures],
        "addedLegalCount": report.addition_count,
        "addedLegal": list(report.additions),
    }
    warnings: list[str] = []
    if report.additions:
        warnings.append(
            f"{report.addition_count} attribute(s) added and legal: "
            + ", ".join(report.additions)
            + ". Adding skinIndex/skinWeight is the purpose of rigging, not a violation — reported "
            "so the addition is visible, never gated"
        )

    if report.status == mesh_parity.STATUS_UNEVALUATED:
        return _unevaluated(
            spec,
            f"mesh_parity.verify could not evaluate parity: {report.reason}",
            threshold,
            **measured,
        )
    if report.status == mesh_parity.STATUS_FAIL:
        first = report.failures[0] if report.failures else None
        headline = str(first) if first is not None else "parity failed with no failure detail"
        if first is not None and first.differences:
            difference = first.differences[0]
            headline += (
                f" (first differing element {difference.index}: {difference.frozen} -> "
                f"{difference.current}, {first.differing_elements} element(s) differ)"
            )
        return _verdict(
            spec,
            False,
            threshold,
            measured,
            f"{report.failure_count} parity failure(s); rigging rewrote frozen geometry. {headline}",
            warnings,
            delegatedTo="mesh_parity.verify",
            summary=report.summary(),
        )
    return _verdict(
        spec,
        True,
        threshold,
        measured,
        f"every frozen buffer byte-identical across {report.meshes_compared} of "
        f"{report.meshes_frozen} frozen mesh(es)",
        warnings,
        delegatedTo="mesh_parity.verify",
    )


# ---------------------------------------------------------------------------------------------
# G12 — rig reference
# ---------------------------------------------------------------------------------------------


def gate_g12(payload: Mapping[str, Any]) -> GateResult:
    """The animation must address the GLB's own skeleton, or an explicitly usable correspondence.

    Animation channels address GLB NODE indices and `JOINTS_0` addresses the SKIN's joint array.
    Neither index space is valid against a procedurally authored skeleton, and neither fails loudly
    when it is wrong: the channels bind, the mixer runs, and the vertices go somewhere. That is the
    same silent class of failure as §0's — a plausible scene driven by the wrong numbers.

    So there are exactly two acceptable answers, and the distinction between them is the whole
    point of the gate:

      * the rig came straight from the GLB, in which case the index spaces are the file's own; or
      * a procedural skeleton coexists AND an explicit, USABLE correspondence maps it to the GLB's
        joints. `Correspondence.usable` is False whenever ANY joint on EITHER side is unmatched,
        because a partial map retargets the bones it knows and leaves the rest at bind pose. An
        unmatched joint means the mapping was invented, and inventing it is what shreds a mesh.

    A structural failure is a known-bad answer, not a missing one, so it FAILS rather than reporting
    unevaluated (ambiguities 13 and 14).
    """
    spec = GATE_SPEC_BY_ID["G12"]
    threshold = {
        "source": "glb, or a correspondence with usable == true",
        "structuralFailure": None,
        "unsupportedInterpolationClips": [],
        "supportedInterpolations": list(glb_rig_reference.SUPPORTED_INTERPOLATIONS),
    }
    block = payload.get(spec.input_key)
    if not isinstance(block, Mapping):
        return _unevaluated(
            spec,
            f"payload has no {spec.input_key!r} block; nothing states which skeleton the clip "
            f"channels and skinIndex values address, and that is not something to assume",
            threshold,
        )

    glb = block.get("glb")
    if not isinstance(glb, Mapping):
        return _unevaluated(
            spec,
            f"{spec.input_key} carries no `glb` report (from glb_rig_reference); there is no "
            f"skeleton of record to check the animation against",
            threshold,
        )

    source = block.get("source")
    structural = glb.get("structuralFailure")
    unsupported = glb.get("unsupportedInterpolationClips")
    unsupported = list(unsupported) if isinstance(unsupported, (list, tuple)) else []
    errors = glb.get("errors")
    errors = list(errors) if isinstance(errors, (list, tuple)) else []
    declared_ok = glb.get("ok")
    # Ambiguity 12: `ok` is added by the CLI, not by GlbRig.to_dict(), so derive it when absent.
    ok = bool(declared_ok) if isinstance(declared_ok, bool) else (not structural and not errors)
    primary_skin = glb.get("primarySkinIndex")
    correspondence = block.get("correspondence")

    measured = {
        "source": source,
        "glbOk": ok,
        "okWasDeclared": isinstance(declared_ok, bool),
        "primarySkinIndex": primary_skin,
        "structuralFailure": structural,
        "unsupportedInterpolationClips": unsupported,
        "errors": errors,
        "correspondenceUsable": (
            correspondence.get("usable") if isinstance(correspondence, Mapping) else None
        ),
    }

    # A structural failure is a known-bad answer, not a missing one — it fails, never unevaluated.
    if isinstance(structural, str) and structural.strip():
        return _verdict(
            spec,
            False,
            threshold,
            measured,
            f"the GLB has a structural failure, so it has no usable skeleton of record: {structural}",
        )
    if unsupported:
        return _verdict(
            spec,
            False,
            threshold,
            measured,
            f"{len(unsupported)} clip(s) use an interpolation outside "
            f"{list(glb_rig_reference.SUPPORTED_INTERPOLATIONS)} "
            f"({', '.join(str(name) for name in unsupported)}); a channel that cannot be evaluated "
            f"addresses the skeleton no more usefully than one addressing the wrong skeleton",
        )

    if isinstance(correspondence, Mapping):
        usable = correspondence.get("usable")
        unmatched_bones = correspondence.get("unmatchedProceduralBones")
        unmatched_bones = list(unmatched_bones) if isinstance(unmatched_bones, (list, tuple)) else []
        unmatched_joints = correspondence.get("unmatchedGlbJoints")
        unmatched_joints = (
            list(unmatched_joints) if isinstance(unmatched_joints, (list, tuple)) else []
        )
        measured["unmatchedProceduralBones"] = unmatched_bones
        measured["unmatchedGlbJoints"] = unmatched_joints
        measured["correspondenceReason"] = correspondence.get("reason")
        if usable is True:
            return _verdict(
                spec,
                True,
                threshold,
                measured,
                "a procedural skeleton coexists and an explicit correspondence maps it to the GLB's "
                "joints with nothing unmatched on either side: "
                f"{correspondence.get('reason')}",
            )
        detail = ""
        if unmatched_bones:
            detail += f" unmatched procedural bones: {', '.join(str(b) for b in unmatched_bones)}."
        if unmatched_joints:
            named = ", ".join(
                str(joint.get("name", joint.get("nodeName", joint)))
                if isinstance(joint, Mapping)
                else str(joint)
                for joint in unmatched_joints
            )
            detail += f" unmatched GLB joints: {named}."
        return _verdict(
            spec,
            False,
            threshold,
            measured,
            f"the procedural-to-GLB correspondence is not usable: "
            f"{correspondence.get('reason')}.{detail} An unmatched joint on either side means the "
            f"mapping was invented, and inventing it is what shreds a mesh",
        )

    if source == "glb":
        problems: list[str] = []
        if not ok:
            problems.append(
                "the GLB report is not ok"
                + (f" ({'; '.join(str(e) for e in errors)})" if errors else "")
            )
        if primary_skin is None:
            problems.append(
                "the report names no primarySkin, so JOINTS_0 and the inverse binds have no "
                "index space of record"
            )
        if problems:
            return _verdict(spec, False, threshold, measured, "; ".join(problems))
        return _verdict(
            spec,
            True,
            threshold,
            measured,
            f"the rig came straight from the GLB (primary skin {primary_skin}), so the clip "
            f"channels and skinIndex values address the file's own index spaces",
        )

    # Ambiguity 14: a procedural skeleton with no mapping is the defect, not absent input.
    return _verdict(
        spec,
        False,
        threshold,
        measured,
        f"rigReference declares source {source!r} and carries no `correspondence` block. A "
        f"skeleton that is not the GLB's own needs an explicit mapping to it; without one the clip "
        f"channels and skinIndex values address a different skeleton",
    )


# ---------------------------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------------------------


def run_gates(
    payload: Mapping[str, Any], sweep_coverage: SweepCoverage = DEFAULT_SWEEP_COVERAGE
) -> GateReport:
    """Run all ten R6 gates and return the report.

    Nothing ships until all ten pass, and "pass" means `pass` -- an unevaluated gate makes
    `GateReport.ok` False. Gates are independent: one failing never short-circuits another, because
    a report that stops at the first failure hides the other nine answers a reader needs to decide
    whether this is one bug or a broken harness.
    """
    if not isinstance(payload, Mapping):
        raise TypeError(f"payload must be a mapping; got {type(payload).__name__}")

    binding_verdict = _evaluate_binding(payload)
    height = payload.get("figureHeight")

    results = (
        gate_g1(payload),
        gate_g2(payload),
        gate_g3(payload),
        gate_g4(payload, binding_verdict),
        gate_g5(payload, binding_verdict),
        gate_g6(payload),
        gate_g7(payload),
        gate_g8(payload),
        gate_g9(payload),
        gate_g10(payload, sweep_coverage),
        gate_g11(payload),
        gate_g12(payload),
    )
    return GateReport(
        results=results,
        figure_height=float(height) if _finite(height) else None,
        sweep_coverage=sweep_coverage,
    )


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] in {"-h", "--help"}:
        print("usage: rig_gates.py <payload.json>", file=sys.stderr)
        return 2
    try:
        raw = json.loads(Path(argv[0]).expanduser().read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("payload root must be an object")
        report = run_gates(raw)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
