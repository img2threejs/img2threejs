#!/usr/bin/env python3
"""Find bald patches before anything is rendered.

WHY THIS GATE EXISTS. Three review signals already look at hair and none of them can see this
failure:

  silhouette_iou        blind by construction -- only about 11% of a figure's cells lie on the
                        outline, and a bald crown is interior
  interior_difference   sees it, but only AFTER a browser capture, and only as a number that could
                        equally mean a colour shift
  the human eye         sees it instantly, and was the only thing that caught it the last three times

The failure is specific and repeatable. Widening the hair side masses by hand took closure from
42.2% to 40.9%, worse on all six views, and dark coverage went DOWN. The widened mass had slid off
the skull: the loft's spine is straight while the skull is convex, so thickening the section moved
the surface sideways rather than outward, the skull ended up proud of the hair, and the render grew
a bare strip. The invariant it broke was already written down in the demo:

    EVERY piece must stand proud of the skull at its own height. Where the skull is proud of the
    hair, the head renders bald there.

This gate is that sentence as arithmetic. It runs on points, so it works on any hair
representation -- shell, masses or locks -- and it needs no browser, no GPU and no capture.

HAIR INSIDE THE SKULL IS NOT COVERAGE. That is the whole point. A naive "is there a hair vertex near
this patch of scalp" test would have passed the widened build, because the vertices were still
nearby -- they had merely sunk below the surface. Only points on the outside of the skull count.

Pure Python 3.10+ standard library.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))

from scalp_field import ScalpField  # noqa: E402

Point = tuple[float, float, float]

# A patch of scalp counts as covered when hair sits within this far along its outward normal,
# measured as a fraction of the skull's own height. Hair further out than this is a separate mass
# floating clear of the head rather than the layer that covers it.
DEFAULT_REACH_FRACTION = 0.22
# ... and within this far off that normal, laterally, again as a fraction of skull height. This is
# the sampling radius, not a physical property: it must be a little wider than the gap between
# adjacent scalp samples or the test reports stripes of exposure between the samples themselves.
DEFAULT_LATERAL_FRACTION = 0.075
# Azimuth and height sampling of the scalp. 32x16 is 512 patches over a head, which resolves a strip
# roughly a finger wide -- finer than the failure being caught and coarse enough to stay instant.
DEFAULT_U_SAMPLES = 32
DEFAULT_V_SAMPLES = 16
# Anything above this fraction of scalp area uncovered is a hard failure rather than a soft signal.
# Not calibrated against a reference: no multipart hair reference exists yet, so this is a
# deliberately loose bound chosen to catch the recorded failure and flagged as uncalibrated in the
# report rather than presented as a measurement.
DEFAULT_HARD_MAX = 0.05


class _PointGrid:
    """Uniform bucket grid, so the gate stays linear in hair points rather than quadratic."""

    def __init__(self, points: Sequence[Point], cell: float) -> None:
        self.cell = max(cell, 1e-6)
        self.buckets: dict[tuple[int, int, int], list[Point]] = {}
        for point in points:
            self.buckets.setdefault(self._key(point), []).append(point)

    def _key(self, point: Point) -> tuple[int, int, int]:
        return (
            int(math.floor(point[0] / self.cell)),
            int(math.floor(point[1] / self.cell)),
            int(math.floor(point[2] / self.cell)),
        )

    def near(self, point: Point) -> Iterable[Point]:
        cx, cy, cz = self._key(point)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    bucket = self.buckets.get((cx + dx, cy + dy, cz + dz))
                    if bucket:
                        yield from bucket


def scalp_exposure(
    field: ScalpField,
    hair_points: Sequence[Point],
    *,
    reach: float | None = None,
    lateral: float | None = None,
    u_samples: int = DEFAULT_U_SAMPLES,
    v_samples: int = DEFAULT_V_SAMPLES,
    hard_max: float = DEFAULT_HARD_MAX,
    v_range: tuple[float, float] = (0.0, 1.0),
) -> dict[str, Any]:
    """What fraction of the scalp has no hair standing over it.

    `v_range` restricts the test to a band of the skull. The face is scalp by geometry and not by
    anatomy, so a full-head sweep would report the forehead, nose and chin as bald and drown the
    signal; callers pass the band above the hairline.

    The returned fraction is AREA-weighted, not sample-weighted: rings near the crown are far
    shorter in circumference than rings at the temples, and counting samples would let a small bare
    crown hide behind a large well-covered band.
    """
    height = field.y_max - field.y_min
    if height <= 0.0:
        raise ValueError("the scalp field has no height")
    reach_distance = reach if reach is not None else DEFAULT_REACH_FRACTION * height
    lateral_distance = lateral if lateral is not None else DEFAULT_LATERAL_FRACTION * height
    if reach_distance <= 0.0 or lateral_distance <= 0.0:
        raise ValueError("reach and lateral must both be positive")
    low, high = v_range
    if not 0.0 <= low < high <= 1.0:
        raise ValueError(f"v_range must be an ascending band inside [0,1], got {v_range}")

    # Only hair OUTSIDE the skull can cover it. This single filter is the difference between a gate
    # that catches the recorded failure and one that waves it through.
    outside = [p for p in hair_points if field.distance(*p) > 0.0]
    sunk = len(hair_points) - len(outside)
    grid = _PointGrid(outside, lateral_distance)

    march_steps = max(2, int(math.ceil(reach_distance / lateral_distance)) + 1)

    total_area = 0.0
    exposed_area = 0.0
    exposed: list[dict[str, float]] = []
    walked = 0

    # The band and the cap disc used to be two near-identical loops here. They are one now, because
    # `ScalpField.surface_samples` already knows how to walk both -- and the duplicate was worse
    # than untidy: the cap loop was added later to close a defect, and having a second copy of the
    # march is precisely how the two would drift apart the next time one of them is fixed.
    for sample in field.surface_samples(u_samples, v_samples, v_range=(low, high)):
        px, py, pz = sample["point"]
        nx, ny, nz = sample["normal"]
        weight = sample["weight"]
        total_area += weight
        walked += 1

        covered = False
        for step in range(march_steps + 1):
            t = reach_distance * step / march_steps
            qx, qy, qz = px + nx * t, py + ny * t, pz + nz * t
            for hx, hy, hz in grid.near((qx, qy, qz)):
                if (hx - qx) ** 2 + (hy - qy) ** 2 + (hz - qz) ** 2 <= lateral_distance ** 2:
                    covered = True
                    break
            if covered:
                break

        if not covered:
            exposed_area += weight
            entry = {"u": round(sample["u"], 4), "v": round(sample["v"], 4),
                     "x": round(px, 5), "y": round(py, 5), "z": round(pz, 5)}
            if sample["cap"]:
                entry["cap"] = True
                entry["capRing"] = sample["capRing"]
            exposed.append(entry)

    fraction = exposed_area / total_area if total_area > 0.0 else 0.0
    return {
        "schemaVersion": 1,
        "kind": "scalp-exposure",
        "exposedFraction": round(fraction, 6),
        "exposedSamples": exposed,
        # What was actually walked, not u*v: the cap disc adds its own annuli, so the product
        # under-reported by 128 of 640 and a caller sizing anything off it would be wrong.
        "sampleCount": walked,
        "hairPointCount": len(hair_points),
        "hairPointsInsideSkull": sunk,
        "reach": round(reach_distance, 6),
        "lateral": round(lateral_distance, 6),
        "vRange": [low, high],
        "hardMax": hard_max,
        "hardMaxUncalibrated": True,
        "verdict": "fail" if fraction > hard_max else "pass",
        "note": (
            "Area-weighted fraction of scalp with no hair standing outside the skull above it. "
            "Hair points inside the skull are excluded from coverage by design: the recorded "
            "failure was a mass that slid off the skull while staying nearby."
        ),
    }


def largest_exposed_run(result: dict[str, Any], u_samples: int = DEFAULT_U_SAMPLES) -> int:
    """Longest unbroken azimuth run of exposed samples at any one height.

    A scatter of isolated exposed samples is sampling noise. A RUN is a strip, and a strip is what
    a viewer reads as a parting or a bald line -- the recorded failure showed exactly that, a bare
    band down the centre of the crown.
    """
    # Keyed by (v, capRing), not by v. Every cap annulus reports v = 1.0, so keying on v alone
    # merged the whole disc into a single row and let one bare annulus read as a full-circle run.
    by_row: dict[tuple[float, int], set[int]] = {}
    for sample in result.get("exposedSamples", []):
        column = int(round(sample["u"] * u_samples)) % u_samples
        by_row.setdefault((sample["v"], sample.get("capRing", -1)), set()).add(column)

    longest = 0
    for columns in by_row.values():
        # A fully exposed ring has NO first column -- every column's predecessor is also exposed --
        # so the run-start scan below finds no start and falls through reporting zero. That is the
        # worst case reporting as the best, on a head with no hair on it at all.
        if len(columns) >= u_samples:
            return u_samples
        for start in columns:
            if (start - 1) % u_samples in columns:
                continue  # not the beginning of a run
            run = 1
            while (start + run) % u_samples in columns and run < u_samples:
                run += 1
            longest = max(longest, run)
    return longest


def _load_points(path: Path) -> list[Point]:
    payload = json.loads(path.read_text())
    raw = payload.get("points", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        raise ValueError("expected a JSON array of [x,y,z] points, or {\"points\": [...]}")
    return [(float(p[0]), float(p[1]), float(p[2])) for p in raw]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--rings", required=True, help="JSON file: [[y,rx,rz,(zc)], ...]")
    parser.add_argument("--hair-points", required=True, help="JSON file: [[x,y,z], ...]")
    parser.add_argument("--v-low", type=float, default=0.0)
    parser.add_argument("--v-high", type=float, default=1.0)
    parser.add_argument("--hard-max", type=float, default=DEFAULT_HARD_MAX)
    parser.add_argument("--out", help="also write the report here")
    args = parser.parse_args(argv)

    field = ScalpField(json.loads(Path(args.rings).read_text()))
    report = scalp_exposure(
        field,
        _load_points(Path(args.hair_points)),
        hard_max=args.hard_max,
        v_range=(args.v_low, args.v_high),
    )
    report["largestExposedRun"] = largest_exposed_run(report)
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
