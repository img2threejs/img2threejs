#!/usr/bin/env python3
"""Compare a render's hair against the reference's, and say which differences are which kind.

WHY THE HARD/SOFT SPLIT IS THE POINT. Every hair failure recorded in this pipeline falls into one of
two classes that want opposite responses:

  a bald patch          the skull is showing through. Always wrong, at any coverage number, and no
                        amount of matching elsewhere compensates. HARD.
  a coverage shortfall   there is less hair than the reference has. Often true and often the best
                        available compromise at a given triangle budget. SOFT.

Conflating them is what produced four wrong fixes in one session. A coverage shortfall was read as
"add more hair", the masses were widened, and the widening pushed them off the skull -- turning a
soft signal into a hard failure while the coverage number barely moved. The measured result: closure
42.2% to 40.9%, worse on all six views, with crown scalp exposure UP 14.9 points on the worst view.

So a shortfall never authorises widening on its own. `scalpExposure` runs first, geometrically, and
its verdict dominates.

Pure Python 3.10+ standard library.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stage1_intake"))

from extract_hair_evidence import extract_hair_evidence  # noqa: E402

# A band whose coverage is this far below the reference is worth reporting. Not calibrated: chosen
# to sit just under the smallest shortfall that proved visible by eye (the profile mid band, which
# read 41.5% against 7.8%).
BAND_SHORTFALL_SOFT = 0.08
# Hairline offset, as a fraction of figure height, beyond which the face has visibly the wrong
# amount of forehead. Derived from the head being roughly 0.13 of figure height: a tenth of a head.
HAIRLINE_OFFSET_MAX = 0.013
# Specular band position difference, in scalp v units, beyond which the highlight reads as sitting
# on the wrong part of the head. Uncalibrated.
SPECULAR_OFFSET_MAX = 0.15


def compare_views(reference: dict[str, Any], render: dict[str, Any]) -> dict[str, Any]:
    """Per-view band, hairline and shading deltas between two hair-evidence reports."""
    views: dict[str, Any] = {}
    for name, reference_view in reference.get("views", {}).items():
        render_view = render.get("views", {}).get(name)
        if not isinstance(render_view, dict):
            views[name] = {"status": "missing-from-render"}
            continue
        if reference_view.get("status") != "measured" or render_view.get("status") != "measured":
            views[name] = {
                "status": "not-measurable",
                "referenceStatus": reference_view.get("status"),
                "renderStatus": render_view.get("status"),
            }
            continue

        bands = {}
        for band, reference_band in reference_view.get("bands", {}).items():
            render_band = render_view.get("bands", {}).get(band, {})
            delta = round(render_band.get("coverage", 0.0) - reference_band.get("coverage", 0.0), 4)
            bands[band] = {
                "reference": reference_band.get("coverage"),
                "render": render_band.get("coverage"),
                "delta": delta,
                "shortfall": delta <= -BAND_SHORTFALL_SOFT,
            }

        reference_hairline = reference_view.get("hairline")
        render_hairline = render_view.get("hairline")
        hairline_offset = (
            round(render_hairline - reference_hairline, 4)
            if reference_hairline is not None and render_hairline is not None
            else None
        )

        reference_specular = reference_view.get("shading", {}).get("specularBandV")
        render_specular = render_view.get("shading", {}).get("specularBandV")
        specular_offset = (
            round(render_specular - reference_specular, 4)
            if reference_specular is not None and render_specular is not None
            else None
        )

        views[name] = {
            "status": "compared",
            "bands": bands,
            "hairlineOffset": hairline_offset,
            "specularOffset": specular_offset,
            "hairFractionDelta": round(
                render_view.get("hairFraction", 0.0) - reference_view.get("hairFraction", 0.0), 4
            ),
        }
    return views


def hair_gate(
    reference_views: dict[str, Path],
    render_views: dict[str, Path],
    scalp_exposure_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The whole hair verdict: one hard channel, several soft ones.

    `scalp_exposure_report` is the output of `scalp_exposure.py`. It is optional only so this module
    can be used on captures alone, and its absence is reported as a gap rather than treated as a
    pass -- an image-only comparison cannot see a bald patch reliably, which is the entire reason
    the geometric gate exists.
    """
    reference = extract_hair_evidence(reference_views)
    render = extract_hair_evidence(render_views)
    views = compare_views(reference, render)

    hard: list[str] = []
    soft: list[str] = []

    if scalp_exposure_report is None:
        soft.append(
            "scalpExposure was not supplied; a pixel comparison cannot reliably see a bald patch, "
            "so this verdict is incomplete. Run forge/stage4_review/scalp_exposure.py."
        )
    elif scalp_exposure_report.get("verdict") == "fail":
        hard.append(
            f"scalpExposure {scalp_exposure_report.get('exposedFraction')} exceeds "
            f"{scalp_exposure_report.get('hardMax')}: the skull is uncovered and will render bald. "
            f"No coverage improvement elsewhere compensates for this."
        )

    for name, view in views.items():
        if view.get("status") != "compared":
            soft.append(f"{name}: {view.get('status')}")
            continue
        for band, data in view.get("bands", {}).items():
            if data.get("shortfall"):
                soft.append(
                    f"{name}/{band}: coverage {data['render']} against reference {data['reference']} "
                    f"(delta {data['delta']})"
                )
        offset = view.get("hairlineOffset")
        if offset is not None and abs(offset) > HAIRLINE_OFFSET_MAX:
            soft.append(f"{name}: hairline off by {offset} of figure height")
        specular = view.get("specularOffset")
        if specular is not None and abs(specular) > SPECULAR_OFFSET_MAX:
            soft.append(
                f"{name}: highlight band off by {specular} in scalp v; this is a MATERIAL "
                f"difference, not a geometry one, and adding hair will not move it"
            )

    return {
        "schemaVersion": 1,
        "kind": "hair-gate",
        "verdict": "fail" if hard else ("review" if soft else "pass"),
        # Whether the HARD channel actually ran, kept separate from the verdict. "review" with the
        # geometric gate present means "checked, here are notes"; "review" with it absent means "I
        # could not check the one thing that is always a failure". Collapsing those two into one
        # word let a run with no bald-patch check at all exit 0.
        "hardChannelPresent": scalp_exposure_report is not None,
        "hardFailures": hard,
        "softSignals": soft,
        "views": views,
        "referenceNotObserved": reference.get("notObserved", []),
        "renderNotObserved": render.get("notObserved", []),
        "thresholds": {
            "bandShortfallSoft": BAND_SHORTFALL_SOFT,
            "hairlineOffsetMax": HAIRLINE_OFFSET_MAX,
            "specularOffsetMax": SPECULAR_OFFSET_MAX,
            "uncalibrated": True,
        },
        "note": (
            "A band shortfall is a soft signal and never on its own authorises widening the masses. "
            "That exact response was measured: widening took closure from 42.2% to 40.9%, worse on "
            "all six views, because the widened mass slid off the skull rather than growing on it."
        ),
    }


def _pairs(entries: Sequence[str], parser: argparse.ArgumentParser) -> dict[str, Path]:
    views: dict[str, Path] = {}
    for entry in entries:
        if "=" not in entry:
            parser.error(f"expected view=path, got {entry!r}")
        name, _, raw = entry.partition("=")
        views[name] = Path(raw)
    return views


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--reference", nargs="+", required=True, help="view=path pairs")
    parser.add_argument("--render", nargs="+", required=True, help="view=path pairs")
    parser.add_argument("--scalp-exposure", help="JSON report from scalp_exposure.py")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    exposure = json.loads(Path(args.scalp_exposure).read_text()) if args.scalp_exposure else None
    report = hair_gate(_pairs(args.reference, parser), _pairs(args.render, parser), exposure)
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)

    # 1 = a hard failure. 2 = the hard channel never ran, so this verdict is incomplete and must not
    # read as approval to a caller that only checks for zero. 0 = the hard gate ran and passed;
    # soft signals alone do not fail a build, by design.
    if report["verdict"] == "fail":
        return 1
    if not report["hardChannelPresent"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
