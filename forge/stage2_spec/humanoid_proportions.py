#!/usr/bin/env python3
"""Canonical humanoid proportions, so a reference-free stylized figure can be specified.

`validate_sculpt_spec.py` blocks any character spec whose `anatomy.applies` is not true, and
tells the author to "fill anatomy (styleHeads, proportions, pose, faceLandmarks) from the
reference". That is the right demand when there IS a reference: a specific person's
proportions are evidence, and a canon table would be a fabrication of that evidence.

But it also made a reference-free request impossible to express. "Build me a 4-head stylized
low-poly humanoid" has no reference to measure, and the canon it should follow is public
figure-drawing convention, not an invented number. This module supplies that canon and marks
it as canon, so the two cases stay distinguishable downstream: anatomy derived here carries
`source: "canon-table"`, never `"reference"`.

## Provenance of every number below

Cited to the low-poly humanoid research corpus (NotebookLM notebook
`ab7334f3-818b-427d-a7eb-96c0581d812f`, 77 sources) or marked DEFINITIONAL where the value
follows from the head-unit system itself rather than from a source.

| Landmark            | 8-head  | 4-head  | Provenance   |
|---------------------|---------|---------|--------------|
| head height / H     | 0.125   | 0.25    | DEFINITIONAL (1/styleHeads) |
| shoulder width / H  | 0.25    | --      | research     |
| waist width / H     | 0.125   | --      | research     |
| hip width / H       | 0.1875  | --      | research     |
| hip line Y / H      | 0.50    | --      | research     |
| knee line Y / H     | 0.25    | --      | research     |
| upper arm / H       | 0.187   | 0.125   | research     |
| forearm / H         | 0.187   | 0.125   | research     |
| shin / H            | 0.25    | 0.125   | research     |

Values the research corpus did NOT supply are absent rather than guessed: chest width,
shoulder Y, waist Y, hand length, thigh length, foot length. `derive_anatomy()` reports them
in `unsourced` so a caller can see what it is NOT being told, instead of receiving a
confident-looking number nobody measured. Filling them in later is a research task, not an
editing task.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Fractions of total height H. Only entries with a stated provenance above appear here.
#
# 8 is the only COMPLETE canon. The corpus supplies stylized limb lengths but was asked
# directly for the 4-head hip line, torso span, leg span and shoulder width and answered
# "NOT IN SOURCES" for all four. Without a hip line there is no torso/legs split, which is
# precisely what the character gate checks — so a 4-head figure cannot be derived honestly
# here yet, and `derive_anatomy()` says so instead of interpolating toward the 8-head row.
CANON_BY_HEADS: dict[int, dict[str, float]] = {
    8: {
        "shoulderWidth": 0.25,
        "waistWidth": 0.125,
        "hipWidth": 0.1875,
        "hipLineY": 0.50,
        "kneeLineY": 0.25,
        "upperArmLength": 0.187,
        "forearmLength": 0.187,
        "shinLength": 0.25,
    },
}

# Sourced, but not enough on their own to satisfy the gate. Kept so the missing pieces are
# named rather than forgotten: filling in a 4-head hip line makes this row usable.
PARTIAL_CANON_BY_HEADS: dict[int, dict[str, float]] = {
    4: {
        "upperArmLength": 0.125,
        "forearmLength": 0.125,
        "shinLength": 0.125,
    },
}

REQUIRED_FOR_GATE: tuple[str, ...] = ("hipLineY",)

UNSOURCED_LANDMARKS: tuple[str, ...] = (
    "chestWidth",
    "shoulderLineY",
    "waistLineY",
    "handLength",
    "thighLength",
    "footLength",
)

# Face landmarks as a fraction of HEAD height, measured from the top of the skull down.
# DEFINITIONAL under the standard head-division convention: the eye line halves the head,
# the nose base and mouth line divide the lower half. The research corpus was not asked for
# these, so they are marked as convention rather than as measured evidence.
FACE_LANDMARKS_BY_HEAD_FRACTION: dict[str, float] = {
    "eyeLine": 0.50,
    "noseBase": 0.75,
    "mouthLine": 0.85,
}

SUPPORTED_HEADS: tuple[int, ...] = tuple(sorted(CANON_BY_HEADS))


def nearest_supported_heads(style_heads: float) -> int:
    """Snap to the nearest COMPLETE table, rather than interpolating between tables.

    Interpolating would invent numbers with the same confident shape as the sourced ones,
    which is exactly what this module exists to avoid.
    """
    return min(SUPPORTED_HEADS, key=lambda h: abs(h - style_heads))


def missing_for_heads(style_heads: float) -> list[str]:
    """Name the landmarks that stop a head count from being derivable, or return []."""
    heads = int(style_heads) if float(style_heads).is_integer() else None
    if heads in CANON_BY_HEADS:
        return []
    partial = PARTIAL_CANON_BY_HEADS.get(heads or -1)
    if partial is None:
        return []
    return [key for key in REQUIRED_FOR_GATE if key not in partial]


def derive_anatomy(style_heads: float) -> dict[str, Any]:
    """Build an `anatomy` block from canon, explicitly labelled as canon.

    The returned block satisfies `validate_sculpt_spec.py`'s character gate — `applies`,
    `styleHeads`, `proportions` with torso/legs head-unit ratios, and `faceLandmarks` — while
    `source` and `unsourced` keep it honest about where the numbers came from and what is
    still missing.
    """
    if not isinstance(style_heads, (int, float)) or isinstance(style_heads, bool):
        raise ValueError(f"styleHeads must be a number, got {style_heads!r}")
    if style_heads <= 0:
        raise ValueError(f"styleHeads must be greater than 0, got {style_heads}")

    missing = missing_for_heads(style_heads)
    if missing:
        raise ValueError(
            f"no complete canon for {style_heads} heads: the corpus supplies limb lengths but "
            f"not {', '.join(missing)}, and without a hip line there is no torso/legs split for "
            f"the character gate to check. Measure it from a reference, or extend "
            f"CANON_BY_HEADS once a source states it. Complete tables: {list(SUPPORTED_HEADS)}"
        )

    heads = nearest_supported_heads(style_heads)
    fractions = dict(CANON_BY_HEADS[heads])
    head_height = 1.0 / style_heads

    # The gate asks for head-unit ratios, so convert once here rather than at every call site.
    def in_heads(fraction: float) -> float:
        return round(fraction / head_height, 4)

    legs_fraction = fractions.get("hipLineY")
    return {
        "applies": True,
        "source": "canon-table",
        "styleHeads": style_heads,
        "snappedToHeads": heads,
        "proportions": {
            # Torso spans the hip line up to the shoulders; with no sourced shoulder Y the
            # honest statement is "everything above the hips minus the head".
            "torso": in_heads(1.0 - (legs_fraction or 0.5)) - 1.0 if legs_fraction else None,
            "legs": in_heads(legs_fraction) if legs_fraction else None,
            "headHeightFraction": round(head_height, 4),
            **{key: round(value, 4) for key, value in fractions.items()},
        },
        "faceLandmarks": dict(FACE_LANDMARKS_BY_HEAD_FRACTION),
        "pose": {"name": "bind", "description": "A-pose bind, arms lowered, palms inward"},
        "unsourced": list(UNSOURCED_LANDMARKS),
    }


def apply_to_spec(spec: dict[str, Any], style_heads: float) -> dict[str, Any]:
    """Write a canon anatomy block into a spec's `preSpecAssessment`.

    Refuses a spec that names a reference image: when a reference exists the anatomy is
    supposed to be measured from it, and silently substituting canon would turn a gate that
    demands evidence into one that accepts a default.
    """
    source_image = spec.get("sourceImage")
    if isinstance(source_image, str) and source_image.strip() and source_image != "/dev/null":
        raise ValueError(
            f"spec names a reference image ({source_image}); measure anatomy from it rather "
            "than substituting the canon table"
        )
    assessment = spec.setdefault("preSpecAssessment", {})
    assessment["anatomy"] = derive_anatomy(style_heads)
    return spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spec", type=Path, help="ObjectSculptSpec to fill")
    parser.add_argument("--style-heads", type=float, required=True,
                        help="head-unit height of the figure (8 = realistic, 4 = stylized)")
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args(argv)

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    try:
        apply_to_spec(spec, args.style_heads)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    payload = json.dumps(spec, indent=2)
    if args.in_place:
        args.spec.write_text(payload + "\n", encoding="utf-8")
        print(f"{args.spec}: anatomy filled from canon at {args.style_heads} heads")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    import sys
    from pathlib import Path

    try:
        sys.path.insert(0, str(next(
            parent / "forge" / "_shared"
            for parent in Path(__file__).resolve().parents
            if (parent / "forge" / "_shared" / "cli_run.py").is_file()
        )))
        from cli_run import run_entry
    except (ImportError, StopIteration):
        # vendored/fixture copies without the forge runtime: run bare, no pipe handling
        def run_entry(main_fn, argv=None):
            return main_fn(sys.argv[1:] if argv is None else argv)

    raise SystemExit(run_entry(main))
