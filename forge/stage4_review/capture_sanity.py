#!/usr/bin/env python3
"""Pre-flight check on the CAPTURE, before any render is compared to a reference.

Every other gate in stage4_review asks "is the model right?". This one asks the question that
has to be settled first: "is the picture of the model usable evidence at all?" -- because when
the harness is wrong, every downstream number is wrong in a way that reads as a model defect.

This exists because of a measured retrospective. On one reconstruction, 5 of 12 correction loops
fixed nothing about the model; they fixed the capture, and each cost a full render-and-measure
cycle because nothing surfaced them:

  * an oversized shadow-catcher plane inflated the auto-framing bbox and put the camera at
    z=54.87, rendering the subject at 8% of frame width;
  * a contact shadow counted as foreground and inflated the render's bbox height by 22% while
    the width matched to 0.6%, dragging silhouette IoU to 0.686;
  * pinned near/far planes correct for a broadside clipped the whole model once orbited, so
    two orbit frames came back empty and the degenerate-view gate read it as collapsed volume.

Each is cheap to detect from the PNG alone, and each is unambiguous once detected. The point is
not accuracy -- it is ORDER: settle the instrument before trusting what it measures.

Exit 0 usable / 1 capture defect / 2 error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage1_intake"))

from check_reference_admission import (  # noqa: E402
    build_foreground_mask,
    largest_component_fraction,
    load_image,
)

# A subject under this fraction of frame is not being measured, it is being glimpsed: the 64x64
# luma grid the fidelity signals run on gets a handful of subject cells, and small features are
# gone before any comparison happens.
MIN_SUBJECT_FRACTION = 0.02
# Above this, the "subject" is the whole frame -- a segmentation fallback or a background that
# failed to key, not a subject.
MAX_SUBJECT_FRACTION = 0.92
# Foreground that is not one connected thing means something else is in frame with the subject.
# A contact shadow is the common case and the one that silently inflates a silhouette bbox.
MIN_LARGEST_COMPONENT = 0.90
# How far the render's framing may differ from the reference's before the comparison is measuring
# framing rather than fidelity.
MAX_FRAMING_RATIO_DELTA = 0.25


def measure(path: Path) -> dict[str, Any]:
    width, height, pixels, warnings = load_image(path)
    mask, stats, mask_warnings = build_foreground_mask(width, height, pixels)
    total = width * height
    covered = sum(1 for value in mask if value)
    fraction = covered / total if total else 0.0
    largest = largest_component_fraction(mask, width, height) if covered else 0.0

    xs = [i % width for i, v in enumerate(mask) if v]
    ys = [i // width for i, v in enumerate(mask) if v]
    if xs:
        bbox = [min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1]
        bbox_fraction = [round(bbox[2] / width, 4), round(bbox[3] / height, 4)]
    else:
        bbox, bbox_fraction = [0, 0, 0, 0], [0.0, 0.0]

    return {
        "path": str(path),
        "resolution": [width, height],
        "subjectFraction": round(fraction, 4),
        "largestComponentFraction": round(largest, 4),
        "bbox": bbox,
        "bboxFraction": bbox_fraction,
        "warnings": list(warnings) + list(mask_warnings),
        "maskStats": stats,
    }


def check(render: dict[str, Any], reference: dict[str, Any] | None) -> list[str]:
    failures: list[str] = []
    frac = render["subjectFraction"]

    if frac <= 0.0:
        failures.append(
            f"{render['path']}: no subject found -- the frame is effectively empty. A pinned "
            "near/far pair solved for one camera commonly clips the model at other angles; check "
            "the frustum before reading this as a collapsed or missing volume."
        )
        return failures

    if frac < MIN_SUBJECT_FRACTION:
        failures.append(
            f"{render['path']}: subject fills {frac:.4f} of frame, below {MIN_SUBJECT_FRACTION}. "
            "This is a FRAMING failure, not a model failure -- auto-framing that measures every "
            "mesh will include an oversized shadow-catcher plane and dolly the camera off."
        )
    if frac > MAX_SUBJECT_FRACTION:
        failures.append(
            f"{render['path']}: subject fills {frac:.4f} of frame, above {MAX_SUBJECT_FRACTION}; "
            "the foreground mask has most likely fallen back to whole-frame coverage, so every "
            "downstream silhouette number would be measuring the frame, not the subject."
        )

    largest = render["largestComponentFraction"]
    if largest < MIN_LARGEST_COMPONENT:
        failures.append(
            f"{render['path']}: the largest connected foreground component is only {largest:.4f} "
            "of the foreground, so something that is not the subject shares the frame. A contact "
            "shadow is the usual cause and it inflates the silhouette bbox on ONE axis, which "
            "reads downstream as wrong proportions. Hide shadow catchers for review captures."
        )

    if reference is not None:
        for axis, index in (("width", 0), ("height", 1)):
            ref_v = reference["bboxFraction"][index]
            got_v = render["bboxFraction"][index]
            if ref_v <= 0:
                continue
            delta = abs(got_v - ref_v) / ref_v
            if delta > MAX_FRAMING_RATIO_DELTA:
                failures.append(
                    f"{render['path']}: subject {axis} is {got_v:.4f} of frame against the "
                    f"reference's {ref_v:.4f} ({delta:.1%} off). Match the capture framing to the "
                    "reference before comparing; a framing mismatch is scored as a fidelity "
                    "failure by every pixel-aligned signal."
                )
    return failures


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--render", type=Path, action="append", required=True,
                        help="render PNG to check; repeatable")
    parser.add_argument("--reference", type=Path, default=None,
                        help="optional reference plate; enables the framing-match check")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        reference = measure(args.reference) if args.reference else None
        renders = [measure(path) for path in args.render]
    except Exception as exc:  # noqa: BLE001 - an unreadable capture is an error, not a verdict
        print(f"capture_sanity: {exc}", file=sys.stderr)
        return 2

    failures: list[str] = []
    for item in renders:
        failures.extend(check(item, reference))

    report = {
        "passed": not failures,
        "failures": failures,
        "reference": reference,
        "renders": renders,
        "thresholds": {
            "minSubjectFraction": MIN_SUBJECT_FRACTION,
            "maxSubjectFraction": MAX_SUBJECT_FRACTION,
            "minLargestComponentFraction": MIN_LARGEST_COMPONENT,
            "maxFramingRatioDelta": MAX_FRAMING_RATIO_DELTA,
        },
        "note": "Capture usability only. A pass here says the picture is worth measuring; it says "
                "nothing about whether the model is right.",
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("USABLE" if not failures else "CAPTURE DEFECT")
        for item in renders:
            print(f"  {item['path']}: subject={item['subjectFraction']} "
                  f"largestComponent={item['largestComponentFraction']} bbox={item['bboxFraction']}")
        for failure in failures:
            print(f"  FAIL: {failure}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
