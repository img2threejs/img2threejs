"""Does raising slice/spoke density recover REAL form, or only smooth through the same samples?

This is the question that decides whether matching the baseline's triangle count buys anything.
Triangle count is a COST metric; it becomes a FIDELITY metric only if the extra triangles carry
outline that the coarser sampling missed. There is no way to tell by inspection which it is -- hence
this measurement rather than an assumption.

METHOD. Build the radial outline of one band at rising spoke counts and compare each against the
finest, resampled onto a common dense set of rays so the comparison is like-for-like. Sampling that
has CONVERGED shows error collapsing toward zero: the extra spokes are re-describing a curve already
captured. Sampling still SHORT of the form shows error that keeps falling as density rises. The same
test along the height axis answers it for slice count.

Interpretation is the whole point: a converged axis means triangles spent there are cosmetic.

The second table measures whether the point cloud is dense enough to support each spoke count.  An
empty angular bin is later interpolated by ``radial_outline``; once many bins are empty that
interpolation bridges arcs with no measured vertices and the loft bulges.  The density ceiling is the
largest spoke count whose median empty-bin fraction across measured bands is at most 5%.

THIS SCRIPT ONLY PRINTS TABLES. Reading them and deciding the final per-node spoke count for
CHARACTER_SPOKES_JSON (min(convergence, density), raised only where a material-patch boundary needs
finer cutting) is a human/agent judgment call -- see PIPELINE.md Stage 1 -- not something this script
resolves for you.

Usage:  python3 measure_density_convergence.py <glb> [nodeIndex]
"""
from __future__ import annotations

import math
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from slice_node import cluster_slice, radial_outline, read_node_positions  # noqa: E402

RAYS = 360


def outline_on_rays(points, spokes):
    """Outline at `spokes` resolution, re-read on a fixed dense ray set so densities are comparable."""
    ring = radial_outline(points, spokes)
    if len(ring) < 3:
        return None
    cx = sum(p[0] for p in ring) / len(ring)
    cy = sum(p[1] for p in ring) / len(ring)
    polar = sorted((math.atan2(p[1] - cy, p[0] - cx) % (2 * math.pi),
                    math.hypot(p[0] - cx, p[1] - cy)) for p in ring)
    angles = [t[0] for t in polar]
    out = []
    for i in range(RAYS):
        a = 2 * math.pi * i / RAYS
        j = 0
        while j < len(angles) and angles[j] <= a:
            j += 1
        lo = polar[j - 1] if j else (polar[-1][0] - 2 * math.pi, polar[-1][1])
        hi = polar[j] if j < len(polar) else (polar[0][0] + 2 * math.pi, polar[0][1])
        gap = hi[0] - lo[0]
        out.append(lo[1] if gap <= 0 else lo[1] + (hi[1] - lo[1]) * (a - lo[0]) / gap)
    return out


def biggest_cluster(band):
    groups = cluster_slice(band)
    return max(groups, key=len) if groups else []


def empty_bin_fraction(points, spokes):
    """Return the fraction of angular bins containing no measured point."""
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    occupied = [False] * spokes
    for x, y in points:
        angle = math.atan2(y - cy, x - cx) % (2 * math.pi)
        occupied[min(spokes - 1, int(angle / (2 * math.pi) * spokes))] = True
    return (spokes - sum(occupied)) / spokes


def main() -> int:
    glb, node = Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else 0
    out_path = None
    if "--out" in sys.argv:
        out_path = Path(sys.argv[sys.argv.index("--out") + 1])
    positions, count = read_node_positions(glb, node)
    xs, ys, zs = positions[0::3], positions[1::3], positions[2::3]
    low, high = min(ys), max(ys)
    span = high - low
    print(f"node {node}: {count} vertices, height span {span:.4f}")

    def band_at(y, half):
        return [(round(xs[i], 4), round(zs[i], 4))
                for i in range(count) if abs(ys[i] - y) <= half]

    # Include a deliberately coarse floor for non-star-shaped/accessory nodes.  A new subject can
    # have no density-supported value at the 16-spoke floor used by the original character; measuring
    # 4/8/12 is preferable to silently treating 16 as supported.
    SPOKES = (4, 8, 12, 16, 32, 64, 96, 128, 192)
    density_bands = []
    for frac in (0.12, 0.32, 0.52, 0.72, 0.92):
        y = low + span * frac
        group = biggest_cluster(band_at(y, span / 80))
        # Four points per bin at the 4-spoke floor is the minimum useful occupancy sample.
        if len(group) >= 16:
            density_bands.append((y, group))

    density_rows = [[empty_bin_fraction(group, spokes) * 100 for spokes in SPOKES]
                    for _, group in density_bands]
    density_medians = ([statistics.median(row[i] for row in density_rows)
                        for i in range(len(SPOKES))] if density_rows else [])
    density_supported = [spokes for spokes, value in zip(SPOKES, density_medians) if value <= 5.0]
    density_ceiling = max(density_supported) if density_supported else None

    # Convergence is meaningful only up to the density ceiling.  Using an unsupported 320-spoke
    # reference asks interpolated, vertex-free arcs to define truth and is exactly the silent bulge
    # this tool is meant to prevent.
    convergence_spokes = tuple(spokes for spokes in SPOKES
                               if density_ceiling is not None and spokes <= density_ceiling)
    convergence_bands = density_bands if convergence_spokes else []
    print("\nSPOKE CONVERGENCE   mean |radius error| vs the density-ceiling outline")
    print(f"{'band y':>9}{'pts':>8}  " + "".join(f"{s:>8}" for s in convergence_spokes))
    convergence_rows = []
    for y, group in convergence_bands:
        ref = outline_on_rays(group, density_ceiling)
        if ref is None:
            continue
        mean_r = statistics.fmean(ref)
        errors = []
        for spokes in convergence_spokes:
            test = outline_on_rays(group, spokes)
            err = statistics.fmean(abs(a - b) for a, b in zip(test, ref)) / mean_r * 100
            errors.append(err)
        convergence_rows.append(errors)
        print(f"{y:9.3f}{len(group):8d}  " + "".join(f"{err:7.2f}%" for err in errors))
    if convergence_rows:
        convergence_medians = [statistics.median(row[i] for row in convergence_rows)
                               for i in range(len(convergence_spokes))]
        print(f"{'median':>17}  " + "".join(f"{err:7.2f}%" for err in convergence_medians))
    else:
        convergence_medians = []

    print("\nSPOKE DENSITY       empty angular bins as % of bins; median must stay <= 5%")
    print(f"{'band y':>9}{'pts':>8}  " + "".join(f"{s:>8}" for s in SPOKES))
    for (y, group), fractions in zip(density_bands, density_rows):
        print(f"{y:9.3f}{len(group):8d}  " + "".join(f"{value:7.2f}%" for value in fractions))
    if density_rows:
        print(f"{'median':>17}  " + "".join(f"{value:7.2f}%" for value in density_medians))
        print("density ceiling (largest median <= 5%): "
              + (str(density_ceiling) if density_ceiling is not None else "none"))
    else:
        density_medians = []
        print("density ceiling (largest median <= 5%): none (no band had >= 16 points)")

    print("\nSLICE CONVERGENCE   coarse band vs a 320-slice-thin band at the same height, 96 spokes")
    print(f"{'slices':>8}{'bands':>8}{'error':>9}")
    fine_half = span / 640
    slice_results = []
    for slices in (20, 40, 80, 160):
        half = span / (2 * slices)
        errs = []
        for i in range(2, slices - 2, max(1, slices // 12)):
            yc = low + span * (i + 0.5) / slices
            coarse = biggest_cluster(band_at(yc, half))
            fine = biggest_cluster(band_at(yc, fine_half))
            if len(coarse) < 400 or len(fine) < 400:
                continue
            a, b = outline_on_rays(coarse, 96), outline_on_rays(fine, 96)
            if a is None or b is None:
                continue
            errs.append(statistics.fmean(abs(x - y) for x, y in zip(a, b))
                        / statistics.fmean(b) * 100)
        if errs:
            mean_error = statistics.fmean(errs)
            slice_results.append({"slices": slices, "bands": len(errs), "meanErrorPercent": mean_error})
            print(f"{slices:8d}{len(errs):8d}{mean_error:8.2f}%")

    convergence_supported = [spokes for spokes, value in zip(convergence_spokes, convergence_medians)
                             if value <= 1.0]
    if convergence_supported:
        convergence_candidate = min(convergence_supported)
        convergence_basis = "first median at or below 1%"
    elif convergence_medians:
        convergence_candidate = max(convergence_spokes)
        convergence_basis = "density ceiling; no lower count reached 1%"
    else:
        convergence_candidate = None
        convergence_basis = "no density-supported reference outline"
    result = {
        "schemaVersion": 1,
        "glb": str(glb.resolve()),
        "node": node,
        "vertexCount": count,
        "heightSpan": span,
        "measuredBandCount": len(density_bands),
        "convergenceBandCount": len(convergence_bands),
        "spokes": list(SPOKES),
        "convergenceSpokes": list(convergence_spokes),
        "convergenceMedianErrorPercent": convergence_medians,
        "convergenceCandidateFirstMedianAtOrBelow1Percent": convergence_candidate,
        "convergenceCandidateBasis": convergence_basis,
        "densityMedianEmptyBinPercent": density_medians,
        "densityCeilingLargestMedianAtOrBelow5Percent": (
            density_ceiling
        ),
        "sliceConvergence": slice_results,
        "decisionRule": "min(convergence candidate, density ceiling); null means insufficient measured bands",
    }
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
