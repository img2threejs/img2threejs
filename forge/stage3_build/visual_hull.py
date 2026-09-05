"""Visual-hull descriptor validation, and the space carving it describes.

Until now this module only VALIDATED a `visualHull` descriptor; nothing carved one. A schema without
an implementation reads downstream as a shipped capability, and the pipeline's own docs cite visual
hull as a way to constrain hidden geometry it never actually constrained. `carve_visual_hull` closes
that: it consumes exactly the descriptor `validate_visual_hull_descriptor` accepts and returns a real
mesh.

What a visual hull can and cannot do, stated here because the caller must not over-read the result:
it is the INTERSECTION OF SILHOUETTE CONES, so it is an upper bound on the true shape. It can never
represent a concavity that does not break the silhouette from some supplied view -- an eye socket, the
inside of a cupped ear, the hollow of a bowl. With two views it is the intersection of two prisms,
which is loose; the third axis is unconstrained and the result extrudes along it. `carve_visual_hull`
reports `unconstrainedAxes` and `limitations` for exactly this reason: a hull is evidence about the
silhouette, not about the surface.

Image conventions, fixed here so a caller can match them:
  front  columns follow +x left-to-right, rows follow -y top-to-bottom
  side   columns follow +z left-to-right, rows follow -y top-to-bottom
  top    columns follow +x left-to-right, rows follow +z top-to-bottom
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


MAX_VISUAL_HULL_RESOLUTION = 32
MAX_VISUAL_HULL_TRIANGLES = 400_000
MIN_VISUAL_HULL_MASK_DIMENSION = 4
MAX_VISUAL_HULL_MASK_DIMENSION = 256
VALID_VISUAL_HULL_AXES = {"front", "side", "top"}
_DESCRIPTOR_FIELDS = {"projection", "boundsSpace", "bounds", "resolution", "triangleBudget", "views", "hiddenRegions"}
_VIEW_FIELDS = {"axis", "confidence", "mask"}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_vector(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, list) or len(value) != 3:
        errors.append(f"{label} must be [number, number, number]")
        return
    for index, item in enumerate(value):
        if not _is_number(item):
            errors.append(f"{label}[{index}] must be numeric")
        elif not math.isfinite(float(item)):
            errors.append(f"{label}[{index}] must be finite")


def _validate_mask(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, list) or not value:
        errors.append(f"{label} must be a non-empty array of binary strings")
        return
    if len(value) < MIN_VISUAL_HULL_MASK_DIMENSION or len(value) > MAX_VISUAL_HULL_MASK_DIMENSION:
        errors.append(
            f"{label} height must be from {MIN_VISUAL_HULL_MASK_DIMENSION} to {MAX_VISUAL_HULL_MASK_DIMENSION}"
        )
    width: int | None = None
    foreground = 0
    for index, row in enumerate(value):
        row_label = f"{label}[{index}]"
        if not isinstance(row, str) or not row:
            errors.append(f"{row_label} must be a non-empty binary string")
            continue
        if any(bit not in {"0", "1"} for bit in row):
            errors.append(f"{row_label} must contain only '0' and '1'")
        if width is None:
            width = len(row)
        elif len(row) != width:
            errors.append(f"{label} rows must all have the same width")
        foreground += row.count("1")
    if width is not None and (width < MIN_VISUAL_HULL_MASK_DIMENSION or width > MAX_VISUAL_HULL_MASK_DIMENSION):
        errors.append(
            f"{label} width must be from {MIN_VISUAL_HULL_MASK_DIMENSION} to {MAX_VISUAL_HULL_MASK_DIMENSION}"
        )
    if foreground == 0:
        errors.append(f"{label} must contain at least one foreground pixel")


def validate_visual_hull_descriptor(component_id: str, descriptor: Any, errors: list[str]) -> None:
    label = f"component {component_id!r} geometryDescriptor.visualHull"
    if not isinstance(descriptor, dict):
        errors.append(f"{label} must be an object")
        return
    for field in descriptor:
        if field not in _DESCRIPTOR_FIELDS:
            errors.append(f"{label}.{field} is not supported")
    if descriptor.get("projection") != "orthographic":
        errors.append(f"{label}.projection must be 'orthographic'")
    if descriptor.get("boundsSpace") != "component-local":
        errors.append(f"{label}.boundsSpace must be 'component-local'")
    bounds = descriptor.get("bounds")
    if not isinstance(bounds, dict):
        errors.append(f"{label}.bounds must be an object")
    else:
        minimum = bounds.get("min")
        maximum = bounds.get("max")
        _validate_vector(minimum, f"{label}.bounds.min", errors)
        _validate_vector(maximum, f"{label}.bounds.max", errors)
        if isinstance(minimum, list) and isinstance(maximum, list) and len(minimum) == len(maximum) == 3:
            for index, (minimum_value, maximum_value) in enumerate(zip(minimum, maximum)):
                if _is_number(minimum_value) and _is_number(maximum_value) and minimum_value >= maximum_value:
                    errors.append(f"{label}.bounds.min[{index}] must be less than bounds.max[{index}]")
    resolution = descriptor.get("resolution")
    if not isinstance(resolution, int) or isinstance(resolution, bool) or resolution < MIN_VISUAL_HULL_MASK_DIMENSION:
        errors.append(f"{label}.resolution must be an integer from {MIN_VISUAL_HULL_MASK_DIMENSION} to {MAX_VISUAL_HULL_RESOLUTION}")
    elif resolution > MAX_VISUAL_HULL_RESOLUTION:
        errors.append(f"{label}.resolution must not exceed {MAX_VISUAL_HULL_RESOLUTION}")
    triangle_budget = descriptor.get("triangleBudget")
    if not isinstance(triangle_budget, int) or isinstance(triangle_budget, bool) or triangle_budget < 1:
        errors.append(f"{label}.triangleBudget must be a positive integer")
    elif triangle_budget > MAX_VISUAL_HULL_TRIANGLES:
        errors.append(f"{label}.triangleBudget must not exceed {MAX_VISUAL_HULL_TRIANGLES}")
    elif isinstance(resolution, int) and not isinstance(resolution, bool) and resolution >= MIN_VISUAL_HULL_MASK_DIMENSION:
        worst_case_triangles = resolution**3 * 12
        if triangle_budget < worst_case_triangles:
            errors.append(
                f"{label}.triangleBudget requires at least {worst_case_triangles} triangles for resolution {resolution}"
            )
    views = descriptor.get("views")
    if not isinstance(views, list) or len(views) < 2:
        errors.append(f"{label}.views must contain at least two views")
    elif len(views) > len(VALID_VISUAL_HULL_AXES):
        errors.append(f"{label}.views must not contain more than {len(VALID_VISUAL_HULL_AXES)} views")
    axes: set[str] = set()
    if isinstance(views, list):
        for index, view in enumerate(views):
            view_label = f"{label}.views[{index}]"
            if not isinstance(view, dict):
                errors.append(f"{view_label} must be an object")
                continue
            for field in view:
                if field not in _VIEW_FIELDS:
                    errors.append(f"{view_label}.{field} is not supported")
            axis = view.get("axis")
            if axis not in VALID_VISUAL_HULL_AXES:
                errors.append(f"{view_label}.axis must be one of: {', '.join(sorted(VALID_VISUAL_HULL_AXES))}")
            elif axis in axes:
                errors.append(f"{label}.views must use distinct axes")
            else:
                axes.add(axis)
            confidence = view.get("confidence")
            if not _is_number(confidence) or not 0 <= float(confidence) <= 1:
                errors.append(f"{view_label}.confidence must be a number from 0 to 1")
            _validate_mask(view.get("mask"), f"{view_label}.mask", errors)
    hidden_regions = descriptor.get("hiddenRegions")
    if hidden_regions is not None and (
        not isinstance(hidden_regions, list)
        or not all(isinstance(region, str) and region.strip() for region in hidden_regions)
    ):
        errors.append(f"{label}.hiddenRegions must be an array of non-empty strings")


# ---------------------------------------------------------------------------
# Space carving
# ---------------------------------------------------------------------------

# Which world axis each view's image column and row follow, and whether that axis runs backwards.
# Keeping this as data rather than branches means a new view axis is one table row, and means the
# convention documented in the module docstring is the convention the code actually uses.
_VIEW_AXES: dict[str, tuple[int, bool, int, bool]] = {
    # axis: (column world axis, column reversed, row world axis, row reversed)
    "front": (0, False, 1, True),
    "side": (2, False, 1, True),
    "top": (0, False, 2, False),
}
# The world axis each view leaves unconstrained -- the direction its camera looks along.
_VIEW_FREE_AXIS = {"front": 2, "side": 0, "top": 1}
_AXIS_NAMES = ("x", "y", "z")


def _mask_lookup(mask: list[str]) -> tuple[int, int, list[str]]:
    return len(mask[0]), len(mask), mask


def _sample_mask(mask: list[str], column: float, row: float) -> bool:
    """Nearest-texel test. Returns False outside the mask rather than raising.

    Out-of-range means the voxel projects off the edge of that view's image, which is evidence the
    voxel is NOT in the silhouette -- treating it as background is the conservative reading and keeps
    the hull an upper bound rather than letting unseen space leak into the result.
    """
    width, height, rows = _mask_lookup(mask)
    x = int(column * width)
    y = int(row * height)
    if x < 0 or y < 0 or x >= width or y >= height:
        return False
    return rows[y][x] == "1"


def carve_visual_hull(descriptor: dict[str, Any]) -> dict[str, Any]:
    """Intersect the supplied silhouette cones on a voxel grid and emit the boundary surface.

    A voxel survives only if it lands on foreground in EVERY view. That is the definition of the
    visual hull, and it is why one bad mask erases the model rather than degrading it -- the caller
    should check `occupiedVoxelCount` before trusting a mesh, which is why it is reported.
    """
    errors: list[str] = []
    validate_visual_hull_descriptor("carve", descriptor, errors)
    if errors:
        raise ValueError("; ".join(errors))

    bounds = descriptor["bounds"]
    low = [float(v) for v in bounds["min"]]
    high = [float(v) for v in bounds["max"]]
    resolution = int(descriptor["resolution"])
    views = descriptor["views"]
    step = [(high[axis] - low[axis]) / resolution for axis in range(3)]

    def centre(index: int, axis: int) -> float:
        return low[axis] + (index + 0.5) * step[axis]

    occupied: set[tuple[int, int, int]] = set()
    for ix in range(resolution):
        cx = centre(ix, 0)
        for iy in range(resolution):
            cy = centre(iy, 1)
            for iz in range(resolution):
                cz = centre(iz, 2)
                point = (cx, cy, cz)
                inside = True
                for view in views:
                    column_axis, column_flip, row_axis, row_flip = _VIEW_AXES[view["axis"]]
                    span_c = high[column_axis] - low[column_axis]
                    span_r = high[row_axis] - low[row_axis]
                    u = (point[column_axis] - low[column_axis]) / span_c
                    v = (point[row_axis] - low[row_axis]) / span_r
                    if column_flip:
                        u = 1.0 - u
                    if row_flip:
                        v = 1.0 - v
                    if not _sample_mask(view["mask"], u, v):
                        inside = False
                        break
                if inside:
                    occupied.add((ix, iy, iz))

    vertices, indices = _boundary_surface(occupied, low, step, resolution)

    covered_axes = {_VIEW_FREE_AXIS[view["axis"]] for view in views}
    unconstrained = [_AXIS_NAMES[axis] for axis in range(3) if axis not in covered_axes]
    triangle_count = len(indices) // 3
    budget = int(descriptor["triangleBudget"])

    limitations = [
        "a visual hull is the intersection of silhouette cones: it is an upper bound on the true "
        "shape and can never contain a concavity that no supplied view sees as background",
    ]
    if len(views) < 3:
        limitations.append(
            f"only {len(views)} views supplied, so the hull extrudes along "
            f"{', '.join(unconstrained) or 'no'} and is loose in that direction"
        )

    return {
        "vertices": vertices,
        "indices": indices,
        "triangleCount": triangle_count,
        # Reported for context only. There is deliberately no budget-overrun verdict here: the
        # descriptor validator already requires triangleBudget >= resolution**3 * 12, the worst case
        # for this grid, so a carve cannot exceed it and a boolean saying so could never be False.
        # A check that cannot fail is worse than no check -- it reads as coverage that isn't there.
        "triangleBudget": budget,
        "resolution": resolution,
        "occupiedVoxelCount": len(occupied),
        "totalVoxelCount": resolution**3,
        "occupiedFraction": round(len(occupied) / max(1, resolution**3), 6),
        "viewAxes": [view["axis"] for view in views],
        "unconstrainedAxes": unconstrained,
        "limitations": limitations,
    }


def _boundary_surface(
    occupied: set[tuple[int, int, int]],
    low: list[float],
    step: list[float],
    resolution: int,
) -> tuple[list[list[float]], list[int]]:
    """Emit only the faces between an occupied voxel and empty space.

    Emitting all six faces of every voxel would produce a mesh with interior walls, which is not a
    surface: every shared face would be a duplicated, oppositely-wound pair and the result would fail
    the watertightness gate for reasons that have nothing to do with the carve. Corners are welded
    through a cache so the output is a closed 2-manifold.
    """
    vertex_ids: dict[tuple[int, int, int], int] = {}
    vertices: list[list[float]] = []

    def corner(gx: int, gy: int, gz: int) -> int:
        key = (gx, gy, gz)
        found = vertex_ids.get(key)
        if found is not None:
            return found
        index = len(vertices)
        vertex_ids[key] = index
        vertices.append([low[0] + gx * step[0], low[1] + gy * step[1], low[2] + gz * step[2]])
        return index

    indices: list[int] = []
    # (axis, direction) -> the four grid corners of that face, wound so the normal points outward.
    faces = {
        (0, 1): ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)),
        (0, -1): ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)),
        (1, 1): ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)),
        (1, -1): ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)),
        (2, 1): ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)),
        (2, -1): ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)),
    }

    for (ix, iy, iz) in sorted(occupied):
        for (axis, direction), offsets in faces.items():
            neighbour = [ix, iy, iz]
            neighbour[axis] += direction
            if tuple(neighbour) in occupied:
                continue
            quad = [corner(ix + dx, iy + dy, iz + dz) for dx, dy, dz in offsets]
            indices.extend([quad[0], quad[1], quad[2], quad[0], quad[2], quad[3]])
    return vertices, indices


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Carve a visual hull from a descriptor JSON.")
    parser.add_argument("descriptor", type=Path, help="JSON file holding the visualHull descriptor")
    parser.add_argument("--out", type=Path, help="write the carved mesh JSON here")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args(argv)
    try:
        descriptor = json.loads(args.descriptor.read_text())
        result = carve_visual_hull(descriptor)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.out:
        args.out.write_text(json.dumps(result))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"carved {result['occupiedVoxelCount']}/{result['totalVoxelCount']} voxels "
            f"({result['occupiedFraction']:.4f}) into {result['triangleCount']} triangles "
            f"from views {result['viewAxes']}"
        )
        for line in result["limitations"]:
            print(f"  note: {line}")
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
