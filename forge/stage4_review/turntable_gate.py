#!/usr/bin/env python3
"""Mandatory turntable coverage gate with interior-hole (background-enclosure) detection.

``diagnose_render_multi_angle`` is reference-free and checks exactly one thing:
does the silhouette AREA collapse when the object is orbited (the billboard
test). That leaves two holes an area-only check cannot see through:

1. **Coverage was never mandatory.** Reviews were historically captured from
   the front only, so defects that exist solely off-axis survived multiple
   review rounds -- a conical hat mounted at hip height instead of behind the
   shoulders, a weapon passing through the body, a charm floating detached
   below the ground plane. None of those are visible from the front. This
   module makes a minimum set of orbit azimuths (front/right/back/left by
   default) MANDATORY: a missing required angle is a gate FAILURE, not a
   warning, because making the turntable mandatory is the entire point.

2. **A hole through the model is invisible to an area test.** A character's
   eye socket was pushed clean through the back of its skull; viewed from
   behind there is a hole through the head. Silhouette AREA barely changes
   (the head is still roughly the same size), so the existing area-collapse
   check passes. But that hole is an enclosed BACKGROUND region trapped
   INSIDE the silhouette -- trivially detectable with a flood fill, at zero
   token cost. This module flood-fills the background from the image border
   and flags any background pixel the fill never reaches: it is enclosed by
   the foreground, i.e. a hole through the object.

Important caveat: a legitimately holed subject exists (a ring, a door
handle, the negative space between an arm and a torso). "holed" here means
only "the silhouette encloses background at this angle" -- it is a defect
just when the subject is NOT supposed to have a through-hole there. Callers
that expect legitimate holes must pass ``allow_holes=True`` /
``--allow-holes`` to keep that signal from failing the gate.

Scope: like its sibling, this module only ANALYZES already-captured PNGs.
Driving a renderer to produce the orbit captures is handled elsewhere.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stage1_intake"))
from extract_pbr_evidence import load_image, build_foreground_mask  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from diagnose_render_multi_angle import analyze_angles, silhouette_area_fraction  # noqa: E402


# Front/right/back/left: the minimum orbit that can catch an off-axis-only
# defect. Overridable by callers with unusual rigs (e.g. no rear access).
REQUIRED_AZIMUTHS: tuple[float, ...] = (0.0, 90.0, 180.0, 270.0)
DEFAULT_AZIMUTH_TOLERANCE = 5.0
DEFAULT_COLLAPSE_RATIO = 0.15

# A hole must clear BOTH floors to trip the gate: an absolute pixel count
# (absorbs anti-aliasing speckle around edges -- a stray 1-2px background
# pixel next to a jagged silhouette edge is not a hole through the model)
# and a fraction of the silhouette area (so a huge, legitimate silhouette
# with a tiny enclosed pocket of background isn't over-flagged either).
INTERIOR_HOLE_PIXEL_FLOOR = 4
INTERIOR_HOLE_FRACTION_THRESHOLD = 0.01


def _normalize_azimuth(degrees: float) -> float:
    """Map an azimuth in degrees onto [0, 360). Python's ``%`` already returns
    a non-negative result against a positive divisor, so this also handles
    negative input (e.g. -10 -> 350)."""
    return degrees % 360.0


def _circular_distance(a: float, b: float) -> float:
    """Shortest distance in degrees between two azimuths around the circle,
    so 359.5 and 0 are 0.5 degrees apart, not 359.5."""
    diff = abs(_normalize_azimuth(a) - _normalize_azimuth(b)) % 360.0
    return min(diff, 360.0 - diff)


def _load_mask(png_path: Path) -> tuple[int, int, list[bool], bool]:
    """Load a capture and build its foreground mask, reusing the same stage1
    loader/segmenter ``silhouette_area_fraction`` uses, so area and hole
    numbers are computed from the identical mask.

    The fourth return value says whether the segmenter could actually isolate
    the subject. ``build_foreground_mask`` has a documented fallback: when the
    subject is not clearly separable from its background it warns "image is not
    clearly isolated from background" and returns MOST PIXELS as foreground.
    That is the right answer for its original caller, which wants a broad pixel
    sample for material evidence, and completely wrong here — a mask covering
    the whole frame makes the silhouette meaningless, the area ratio ~1.0 for
    every angle, and the enclosed-background holes pure noise.

    This is not hypothetical. Run against a real review turntable rendered on a
    dark gradient background, the gate reported ``area=0.9966`` per angle and a
    confident PASS while never once measuring the character. A gate that cannot
    see its subject must say so, not pass. Its sibling module already hardens
    the opposite fallback (a tiny mask being inverted to "all opaque"); this is
    the same bug wearing the other face.
    """
    width, height, pixels, _warnings = load_image(Path(png_path))
    mask, _diagnostics, mask_warnings = build_foreground_mask(width, height, pixels)
    segmented = not any("not clearly isolated" in str(w).lower() for w in mask_warnings)
    return width, height, mask, segmented


def _analyze_holes(width: int, height: int, mask: list[bool]) -> dict[str, Any]:
    """Flood-fill the BACKGROUND (mask == False) starting from every border
    pixel, iteratively (an explicit stack, not recursion -- these are large
    images and Python's recursion limit would blow on a recursive flood fill).
    Any background pixel the fill never reaches is enclosed by the
    foreground: an interior hole. A second iterative pass then walks the
    unreached background pixels to size each individual hole component and
    find the largest one's bounding box.
    """
    total = width * height
    foreground_count = sum(1 for value in mask if value)

    def index(x: int, y: int) -> int:
        return y * width + x

    # Pass 1: flood-fill background reachable from the border.
    reached = [False] * total
    stack: list[int] = []
    for x in range(width):
        for y in (0, height - 1):
            i = index(x, y)
            if not mask[i] and not reached[i]:
                reached[i] = True
                stack.append(i)
    for y in range(height):
        for x in (0, width - 1):
            i = index(x, y)
            if not mask[i] and not reached[i]:
                reached[i] = True
                stack.append(i)
    while stack:
        i = stack.pop()
        x, y = i % width, i // width
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height:
                ni = index(nx, ny)
                if not mask[ni] and not reached[ni]:
                    reached[ni] = True
                    stack.append(ni)

    # Pass 2: every background pixel not reached from the border is part of
    # some enclosed hole. Walk each connected component to size it and find
    # the largest one's bounding box.
    visited = [False] * total
    total_hole_pixels = 0
    largest_size = 0
    largest_bbox = {"x": 0, "y": 0, "width": 0, "height": 0}
    for start in range(total):
        if mask[start] or reached[start] or visited[start]:
            continue
        comp_stack = [start]
        visited[start] = True
        size = 0
        min_x = max_x = start % width
        min_y = max_y = start // width
        while comp_stack:
            i = comp_stack.pop()
            x, y = i % width, i // width
            size += 1
            min_x, max_x = min(min_x, x), max(max_x, x)
            min_y, max_y = min(min_y, y), max(max_y, y)
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < width and 0 <= ny < height:
                    ni = index(nx, ny)
                    if not mask[ni] and not reached[ni] and not visited[ni]:
                        visited[ni] = True
                        comp_stack.append(ni)
        total_hole_pixels += size
        if size > largest_size:
            largest_size = size
            largest_bbox = {
                "x": min_x,
                "y": min_y,
                "width": max_x - min_x + 1,
                "height": max_y - min_y + 1,
            }

    interior_fraction = (total_hole_pixels / foreground_count) if foreground_count > 0 else 0.0
    return {
        "interiorHolePixelCount": total_hole_pixels,
        "interiorHoleFraction": interior_fraction,
        "largestHolePixelCount": largest_size,
        "largestHoleBBox": largest_bbox,
    }


def analyze_turntable(
    captures: list[tuple[float, Path]],
    *,
    required_azimuths: tuple[float, ...] = REQUIRED_AZIMUTHS,
    azimuth_tolerance: float = DEFAULT_AZIMUTH_TOLERANCE,
    collapse_ratio: float = DEFAULT_COLLAPSE_RATIO,
    allow_holes: bool = False,
) -> dict[str, Any]:
    """Run the mandatory-coverage + interior-hole turntable gate.

    ``captures`` is a list of ``(azimuth_degrees, png_path)`` pairs. Azimuths
    are normalized into [0, 360) and matched to ``required_azimuths`` within
    ``azimuth_tolerance`` degrees (circular, so 359.5 matches a required 0).
    A required azimuth with no matching capture is a gate FAILURE.

    The capture closest to azimuth 0 is used as the reference frame for the
    area-collapse check (delegated to ``diagnose_render_multi_angle
    .analyze_angles``); every other capture is an orbit angle compared
    against it, mirroring that module's single-reference model.

    Every capture, including the reference, is independently checked for an
    interior hole (background enclosed by the silhouette).

    ``holed`` (per-capture and top-level) always reports the raw detection
    signal, regardless of ``allow_holes`` -- it documents what was actually
    found. ``allow_holes`` only affects the final ``passed`` gate, per the
    module docstring's caveat that an enclosed-background signal is a defect
    only when the subject is not supposed to have a through-hole there.
    """
    if not captures:
        raise ValueError("analyze_turntable requires at least one capture")

    ordered = sorted(captures, key=lambda capture: _normalize_azimuth(capture[0]))

    # Reference = capture closest to azimuth 0 ("front"). Found by index (not
    # by tuple identity/equality) so duplicate (azimuth, path) pairs can't
    # accidentally exclude the wrong entry.
    reference_index = min(
        range(len(ordered)), key=lambda i: _circular_distance(ordered[i][0], 0.0)
    )
    reference_azimuth, reference_path = ordered[reference_index]
    orbit_captures = [capture for i, capture in enumerate(ordered) if i != reference_index]
    orbit_paths = [path for _azimuth, path in orbit_captures]

    area_result = analyze_angles(reference_path, orbit_paths, collapse_ratio)

    # Stitch analyze_angles' per-orbit-angle results back onto their azimuths
    # (same order as orbit_paths, since that's exactly what was passed in).
    per_azimuth: dict[int, dict[str, Any]] = {
        reference_index: {
            "areaFraction": area_result["referenceAreaFraction"],
            "ratio": 1.0,
            "degenerate": False,
        }
    }
    for i, orbit_result in zip((i for i in range(len(ordered)) if i != reference_index), area_result["angles"]):
        per_azimuth[i] = {
            "areaFraction": orbit_result["areaFraction"],
            "ratio": orbit_result["ratio"],
            "degenerate": orbit_result["degenerate"],
        }

    angles: list[dict[str, Any]] = []
    any_holed = False
    unsegmented: list[float] = []
    for i, (azimuth, path) in enumerate(ordered):
        width, height, mask, segmented = _load_mask(path)
        hole_info = _analyze_holes(width, height, mask)
        # Hole numbers computed over a whole-frame fallback mask are noise, so they are not allowed to
        # set `holed` either way -- neither to fail the gate on garbage nor, worse, to pass it.
        holed = segmented and (
            hole_info["largestHolePixelCount"] > INTERIOR_HOLE_PIXEL_FLOOR
            and hole_info["interiorHoleFraction"] > INTERIOR_HOLE_FRACTION_THRESHOLD
        )
        if holed:
            any_holed = True
        if not segmented:
            unsegmented.append(azimuth)
        record = {
            "azimuth": azimuth,
            "path": str(path),
            "isReference": i == reference_index,
            "segmented": segmented,
            "holed": holed,
            **hole_info,
            **per_azimuth[i],
        }
        angles.append(record)

    missing_azimuths: list[float] = []
    for required in required_azimuths:
        matched = any(
            _circular_distance(azimuth, required) <= azimuth_tolerance
            for azimuth, _path in captures
        )
        if not matched:
            missing_azimuths.append(_normalize_azimuth(required))

    covered = not missing_azimuths
    degenerate = area_result["degenerate"]
    # An unsegmentable capture blocks the gate regardless of `allow_holes`. `allow_holes` says "this
    # subject is allowed to have a through-hole"; it does not say "evaluate this render blind". Passing
    # here would be the exact defect this module was written to end, one layer further down.
    passed = (
        covered
        and not degenerate
        and not unsegmented
        and (allow_holes or not any_holed)
    )

    return {
        "angles": angles,
        "referenceAzimuth": reference_azimuth,
        "requiredAzimuths": list(required_azimuths),
        "azimuthTolerance": azimuth_tolerance,
        "missingAzimuths": missing_azimuths,
        "covered": covered,
        "collapseRatio": collapse_ratio,
        "degenerate": degenerate,
        "allowHoles": allow_holes,
        "holed": any_holed,
        "unsegmentedAzimuths": unsegmented,
        "segmentationReliable": not unsegmented,
        "passed": passed,
        "note": (
            "deterministic; zero token; mandatory azimuth coverage catches "
            "off-axis-only defects, interior-hole flood fill catches "
            "silhouette-enclosed background that an area-only check misses"
        ),
    }


def _format_summary(result: dict[str, Any]) -> str:
    lines = [
        f"reference azimuth: {result['referenceAzimuth']}",
        f"required azimuths: {result['requiredAzimuths']} "
        f"(tolerance {result['azimuthTolerance']} deg)",
        f"collapse ratio threshold: {result['collapseRatio']}",
        f"allow holes: {result['allowHoles']}",
    ]
    if result["missingAzimuths"]:
        lines.append(f"MISSING required azimuths: {result['missingAzimuths']}")
    if result.get("unsegmentedAzimuths"):
        lines.append(
            f"UNSEGMENTED azimuths {result['unsegmentedAzimuths']}: the subject could not be "
            "isolated from its background, so area and hole numbers on those frames are "
            "meaningless. Re-render the turntable against a high-contrast or transparent "
            "background before reading this gate's verdict."
        )
    for angle in result["angles"]:
        flags = []
        if not angle.get("segmented", True):
            flags.append("UNSEGMENTED")
        if angle["degenerate"]:
            flags.append("DEGENERATE")
        if angle["holed"]:
            flags.append("HOLED")
        flag = ",".join(flags) if flags else "ok"
        tag = " (reference)" if angle["isReference"] else ""
        lines.append(
            f"  [{flag}] azimuth={angle['azimuth']}{tag} {angle['path']} "
            f"area={angle['areaFraction']:.4f} ratio={angle['ratio']:.4f} "
            f"holePixels={angle['interiorHolePixelCount']} "
            f"largestHole={angle['largestHolePixelCount']}"
        )
    verdict = "PASS" if result["passed"] else "GATE FAILURE"
    lines.append(f"verdict: {verdict}")
    return "\n".join(lines)


def _parse_capture(raw: str) -> tuple[float, Path]:
    if "=" not in raw:
        raise ValueError(f"--capture expects azimuth=path, got {raw!r}")
    azimuth_str, path_str = raw.split("=", 1)
    return float(azimuth_str), Path(path_str)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--capture",
        type=str,
        action="append",
        default=[],
        help="azimuth=path.png (repeat for each turntable capture)",
    )
    parser.add_argument(
        "--required",
        type=float,
        action="append",
        default=None,
        help=f"required azimuth in degrees (repeat; default {REQUIRED_AZIMUTHS})",
    )
    parser.add_argument("--azimuth-tolerance", type=float, default=DEFAULT_AZIMUTH_TOLERANCE)
    parser.add_argument("--collapse-ratio", type=float, default=DEFAULT_COLLAPSE_RATIO)
    parser.add_argument("--allow-holes", action="store_true")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a summary")
    args = parser.parse_args(argv)

    try:
        captures = [_parse_capture(raw) for raw in args.capture]
        required_azimuths = tuple(args.required) if args.required else REQUIRED_AZIMUTHS
        result = analyze_turntable(
            captures,
            required_azimuths=required_azimuths,
            azimuth_tolerance=args.azimuth_tolerance,
            collapse_ratio=args.collapse_ratio,
            allow_holes=args.allow_holes,
        )
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(_format_summary(result))
        return 0 if result["passed"] else 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


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
