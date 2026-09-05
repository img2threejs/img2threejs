#!/usr/bin/env python3
"""
Compare a candidate mesh against a reference mesh, band by band, and say WHERE it is wrong.

This exists because every silhouette comparison in this project so far has measured something other
than what it claimed. The ground plane sat inside the foreground mask; the capture clipped both
subjects at the frame edge so their aspect ratios agreed by construction; and three separate attempts
sliced both models into height bands measured down from the top of the bounding box -- where this
model's top is a single ear tip and the reference's is two ears side by side, so every band below was
offset and the numbers described the misalignment rather than the shapes.

Three decisions follow from that history:

FEET, NOT BBOX TOP. Both meshes are translated so their lowest point is 0 and scaled so their height
is 1. The ground is a landmark both subjects genuinely share; the top of the bounding box is whatever
happens to poke up highest, which is exactly the thing that differs.

PERCENTILES, NOT EXTREMES. A staff is long, thin and contributes very few vertices, but it dominates
any min/max width. The 5th-95th percentile of each band reports the BODY, which is what proportion
work needs; the raw extent is still reported alongside so a genuine prop difference stays visible.

CENTROID OFFSET, NOT ONLY WIDTH. Two bands can be the same width and still be in different places.
Reporting the lateral and depth centroid of each band catches a limb that is the right size and the
wrong side -- a defect a width-only comparison passes silently.

Reads .glb (uncompressed) with the standard library only, per this package's no-dependency rule.

Usage:
    mesh_reference_compare.py REFERENCE.glb CANDIDATE.glb [--bands N] [--json]
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942
FLOAT32 = 5126
COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def read_glb_positions(path: Path) -> list[tuple[float, float, float]]:
    """Every POSITION vertex in the file, in node-transformed world space.

    Node transforms are applied because a mesh parked under a translated or scaled node would
    otherwise be compared in the wrong place — and that failure looks like a proportion error.
    """
    data = path.read_bytes()
    magic, _version, _length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC:
        sys.exit(f"{path} is not a GLB")

    offset, gltf, binary = 12, None, None
    while offset < len(data):
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        body = data[offset + 8: offset + 8 + chunk_len]
        if chunk_type == CHUNK_JSON:
            gltf = json.loads(body)
        elif chunk_type == CHUNK_BIN:
            binary = body
        offset += 8 + chunk_len + (-chunk_len % 4)

    if gltf is None or binary is None:
        sys.exit(f"{path}: missing JSON or BIN chunk")
    required = gltf.get("extensionsRequired", [])
    if any("draco" in e.lower() or "meshopt" in e.lower() for e in required):
        sys.exit(f"{path}: compressed ({required}); this reader handles uncompressed GLB only")

    views, accessors = gltf.get("bufferViews", []), gltf.get("accessors", [])

    def read_accessor(index: int) -> list[tuple[float, float, float]]:
        acc = accessors[index]
        if acc.get("componentType") != FLOAT32 or acc.get("type") != "VEC3":
            sys.exit(f"{path}: POSITION accessor {index} is not float32 VEC3")
        view = views[acc["bufferView"]]
        start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = view.get("byteStride") or 12
        out = []
        for i in range(acc["count"]):
            out.append(struct.unpack_from("<3f", binary, start + i * stride))
        return out

    # Full 4x4 composition, because the two shortcuts here were both wrong.
    #
    # The first version handled only translation and uniform scale and IGNORED node rotation
    # entirely -- a rotated limb would have been measured in the wrong place with no complaint. The
    # second refused any node carrying a `matrix`, which is exactly how three.js's GLTFExporter
    # writes this project's own model, so the candidate could not be read at all. Composing real
    # matrices removes both special cases.
    nodes = gltf.get("nodes", [])
    meshes = gltf.get("meshes", [])
    points: list[tuple[float, float, float]] = []

    def local_matrix(node: dict) -> list[float]:
        if "matrix" in node:
            return list(node["matrix"])  # glTF stores column-major
        tx, ty, tz = node.get("translation", [0.0, 0.0, 0.0])
        qx, qy, qz, qw = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
        sx, sy, sz = node.get("scale", [1.0, 1.0, 1.0])
        xx, yy, zz = qx * qx, qy * qy, qz * qz
        xy, xz, yz = qx * qy, qx * qz, qy * qz
        wx, wy, wz = qw * qx, qw * qy, qw * qz
        return [
            (1 - 2 * (yy + zz)) * sx, (2 * (xy + wz)) * sx, (2 * (xz - wy)) * sx, 0.0,
            (2 * (xy - wz)) * sy, (1 - 2 * (xx + zz)) * sy, (2 * (yz + wx)) * sy, 0.0,
            (2 * (xz + wy)) * sz, (2 * (yz - wx)) * sz, (1 - 2 * (xx + yy)) * sz, 0.0,
            tx, ty, tz, 1.0,
        ]

    def multiply(a: list[float], b: list[float]) -> list[float]:
        out = [0.0] * 16
        for col in range(4):
            for row in range(4):
                out[col * 4 + row] = sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
        return out

    def apply(m: list[float], x: float, y: float, z: float) -> tuple[float, float, float]:
        return (
            m[0] * x + m[4] * y + m[8] * z + m[12],
            m[1] * x + m[5] * y + m[9] * z + m[13],
            m[2] * x + m[6] * y + m[10] * z + m[14],
        )

    IDENTITY = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]

    def walk(node_index: int, parent: list[float]) -> None:
        node = nodes[node_index]
        world = multiply(parent, local_matrix(node))
        if "mesh" in node:
            for prim in meshes[node["mesh"]]["primitives"]:
                pos = prim.get("attributes", {}).get("POSITION")
                if pos is None:
                    continue
                for x, y, z in read_accessor(pos):
                    points.append(apply(world, x, y, z))
        for child in node.get("children", []):
            walk(child, world)

    scene = gltf.get("scenes", [{}])[gltf.get("scene", 0)]
    for root in scene.get("nodes", range(len(nodes))):
        walk(root, IDENTITY)
    if not points:
        sys.exit(f"{path}: no POSITION data found")
    return points


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def normalise(points: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """Feet to 0, height to 1, and centred laterally on the body's own median.

    Centring on the MEDIAN x rather than the bbox centre matters for the same reason percentiles
    matter for width: one long horizontal prop drags a bbox centre sideways, and every centroid
    offset below would then be measured against a datum the body is not standing on.
    """
    ys = [p[1] for p in points]
    y0, y1 = min(ys), max(ys)
    height = y1 - y0
    if height <= 0:
        sys.exit("degenerate mesh: zero height")
    mx = percentile([p[0] for p in points], 0.5)
    mz = percentile([p[2] for p in points], 0.5)
    return [((p[0] - mx) / height, (p[1] - y0) / height, (p[2] - mz) / height) for p in points]


def width_curve(points: list[tuple[float, float, float]], slices: int = 160) -> list[float]:
    """Percentile width at every height, smoothed — the curve landmarks are read off."""
    buckets: list[list[float]] = [[] for _ in range(slices)]
    for x, y, _z in points:
        buckets[min(slices - 1, int(y * slices))].append(x)
    raw = [percentile(b, 0.95) - percentile(b, 0.05) if len(b) >= 20 else 0.0 for b in buckets]
    smoothed = []
    for i in range(slices):
        window = raw[max(0, i - 2): min(slices, i + 3)]
        smoothed.append(sum(window) / len(window))
    return smoothed


def find_landmarks(points: list[tuple[float, float, float]]) -> dict[str, float]:
    """Locate the waist and the neck as the narrowest points of the body's width curve.

    Equal-percentage bands only correspond between two subjects when the subjects share proportions.
    These two do not -- one is far more compact than the other -- so band 75 is the head on one and
    the upper chest on the other, and a deviation table built that way describes the mismatch rather
    than the shapes. Three earlier comparisons in this project failed exactly there.

    A standing figure's width curve has two reliable minima: the waist between hips and ribs, and the
    neck between shoulders and skull. Both are found as the narrowest slice inside a generous search
    window rather than at a fixed height, so they track the subject instead of assuming it.
    """
    curve = width_curve(points)
    n = len(curve)

    def narrowest(lo: float, hi: float) -> float:
        segment = [(curve[i], i) for i in range(int(n * lo), int(n * hi)) if curve[i] > 0]
        if not segment:
            return (lo + hi) / 2
        return min(segment)[1] / n

    waist = narrowest(0.35, 0.68)

    def neck_above(waist_h: float) -> float:
        """The first true pinch ABOVE the waist, not merely the smallest slice in a fixed window.

        A fixed 0.62-0.90 window picked the waist twice on this project's own model: its narrowest
        slice sits at 0.61, inside that window, so waist and neck were reported 0.006 apart and every
        band between them collapsed. Requiring a clear margin above the waist and an actual local
        minimum -- narrower than the curve a few slices either side -- finds the pinch below the skull
        instead of re-finding the one below the ribs.
        """
        start = int(n * min(0.92, waist_h + 0.08))
        best, best_width = None, None
        for i in range(start, int(n * 0.94)):
            if curve[i] <= 0:
                continue
            lo_i, hi_i = max(0, i - 3), min(n - 1, i + 3)
            if curve[i] <= curve[lo_i] and curve[i] <= curve[hi_i]:
                if best_width is None or curve[i] < best_width:
                    best, best_width = i, curve[i]
        return (best / n) if best is not None else min(0.92, waist_h + 0.15)

    return {"ground": 0.0, "waist": waist, "neck": neck_above(waist), "top": 1.0}


def landmark_bands(points: list[tuple[float, float, float]], per_segment: int) -> list[dict]:
    """Band between anatomical landmarks, so band k means the same body region in both meshes."""
    marks = find_landmarks(points)
    edges: list[float] = []
    for lo, hi in (("ground", "waist"), ("waist", "neck"), ("neck", "top")):
        a, b = marks[lo], marks[hi]
        edges.extend(a + (b - a) * i / per_segment for i in range(per_segment))
    edges.append(1.0)

    profile = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        bucket = [p for p in points if lo <= p[1] < hi]
        if len(bucket) < 20:
            profile.append({"band": i, "points": len(bucket), "lo": lo, "hi": hi})
            continue
        xs = [p[0] for p in bucket]
        zs = [p[2] for p in bucket]
        profile.append({
            "band": i, "points": len(bucket), "lo": lo, "hi": hi,
            "width": percentile(xs, 0.95) - percentile(xs, 0.05),
            "depth": percentile(zs, 0.95) - percentile(zs, 0.05),
            "centroidX": percentile(xs, 0.5), "centroidZ": percentile(zs, 0.5),
        })
    return profile, marks


def band_profile(points: list[tuple[float, float, float]], bands: int) -> list[dict]:
    buckets: list[list[tuple[float, float, float]]] = [[] for _ in range(bands)]
    for p in points:
        idx = min(bands - 1, int(p[1] * bands))
        buckets[idx].append(p)
    profile = []
    for i, bucket in enumerate(buckets):
        if len(bucket) < 20:
            profile.append({"band": i, "points": len(bucket)})
            continue
        xs = [p[0] for p in bucket]
        zs = [p[2] for p in bucket]
        profile.append({
            "band": i,
            "points": len(bucket),
            "width": percentile(xs, 0.95) - percentile(xs, 0.05),
            "depth": percentile(zs, 0.95) - percentile(zs, 0.05),
            "widthFull": max(xs) - min(xs),
            "centroidX": percentile(xs, 0.5),
            "centroidZ": percentile(zs, 0.5),
        })
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--bands", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--align", choices=("height", "landmarks"), default="landmarks",
                        help="landmarks (default) bands between the waist and neck it "
                             "finds in each mesh; height bands by raw percentage and "
                             "only corresponds when both subjects share proportions")
    args = parser.parse_args()

    ref_pts = normalise(read_glb_positions(args.reference))
    cand_pts = normalise(read_glb_positions(args.candidate))
    if args.align == "landmarks":
        ref, ref_marks = landmark_bands(ref_pts, max(2, args.bands // 3))
        cand, cand_marks = landmark_bands(cand_pts, max(2, args.bands // 3))
    else:
        ref, cand = band_profile(ref_pts, args.bands), band_profile(cand_pts, args.bands)
        ref_marks = cand_marks = None

    rows, errors = [], []
    for r, c in zip(ref, cand):
        if "width" not in r or "width" not in c:
            continue
        row = {
            "band": r["band"],
            "lowPct": round(r["band"] / args.bands * 100),
            "highPct": round((r["band"] + 1) / args.bands * 100),
            "refWidth": round(r["width"], 4), "candWidth": round(c["width"], 4),
            "refDepth": round(r["depth"], 4), "candDepth": round(c["depth"], 4),
            "widthDelta": round(c["width"] - r["width"], 4),
            "depthDelta": round(c["depth"] - r["depth"], 4),
            "centroidXDelta": round(c["centroidX"] - r["centroidX"], 4),
            "centroidZDelta": round(c["centroidZ"] - r["centroidZ"], 4),
        }
        rows.append(row)
        errors.append(abs(row["widthDelta"]) + abs(row["depthDelta"]))

    score = sum(errors) / (2 * len(errors)) if errors else 0.0
    report = {
        "reference": str(args.reference), "candidate": str(args.candidate),
        "bands": args.bands, "meanBandError": round(score, 5), "rows": rows,
        "note": "0 = feet, 100 = top. Widths are 5-95 percentile, normalised by subject height, "
                "so a thin prop cannot dominate. Both meshes are aligned on the ground plane, not "
                "on the bounding-box top.",
    }

    if args.json:
        print(json.dumps(report, indent=2))
        return

    if ref_marks:
        print(f"landmarks  reference: waist {ref_marks['waist']:.3f}  neck {ref_marks['neck']:.3f}")
        print(f"landmarks  candidate: waist {cand_marks['waist']:.3f}  neck {cand_marks['neck']:.3f}")
    print(f"reference: {args.reference.name}")
    print(f"candidate: {args.candidate.name}")
    print(f"\n{'band':>10}{'ref w':>9}{'cand w':>9}{'Δw':>8}{'ref d':>9}{'cand d':>9}{'Δd':>8}"
          f"{'Δcx':>8}{'Δcz':>8}")
    for row in rows:
        print(f"{row['lowPct']:>4}-{row['highPct']:<5}"
              f"{row['refWidth']:>9.3f}{row['candWidth']:>9.3f}{row['widthDelta']:>+8.3f}"
              f"{row['refDepth']:>9.3f}{row['candDepth']:>9.3f}{row['depthDelta']:>+8.3f}"
              f"{row['centroidXDelta']:>+8.3f}{row['centroidZDelta']:>+8.3f}")
    print(f"\nmean band error = {score:.5f}")
    worst = sorted(rows, key=lambda r: -(abs(r["widthDelta"]) + abs(r["depthDelta"])))[:5]
    print("\nworst bands:")
    for row in worst:
        print(f"  {row['lowPct']:>3}-{row['highPct']:<4}  Δw {row['widthDelta']:+.3f}   "
              f"Δd {row['depthDelta']:+.3f}   Δcx {row['centroidXDelta']:+.3f}")


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

    raise SystemExit(run_entry(lambda _argv: main()))
