#!/usr/bin/env python3
"""Generate a TypeScript Three.js factory skeleton from an ObjectSculptSpec."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

_FORGE_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_FORGE_ROOT), str(_FORGE_ROOT / "_shared"), str(_FORGE_ROOT / "stage2_spec")]
from orchestrate_passes import pass_specific_gaps
from subdivision import (
    ATTACHMENT_CYLINDER_HEIGHT_SEGMENTS,
    ATTACHMENT_CYLINDER_RADIAL_SEGMENTS,
    CAPSULE_CAP_SEGMENTS,
    CAPSULE_RADIAL_SEGMENTS,
    CONE_HEIGHT_SEGMENTS,
    CONE_SUBDIVISION_TOP_RADIUS,
    CYLINDER_HEIGHT_SEGMENTS,
    CYLINDER_RADIAL_SEGMENTS,
    DEFAULT_TESSELLATION_TIER,
    MAX_SUBDIVISION_ITERATIONS,
    MAX_SUBDIVISION_QUAD_FACES,
    PLANE_HEIGHT_SEGMENTS,
    PLANE_WIDTH_SEGMENTS,
    SPHERE_HEIGHT_SEGMENTS,
    SPHERE_WIDTH_SEGMENTS,
    resolve_instanced_cluster_base,
    segments_for_spec,
    TESSELLATION_TIERS,
    TORUS_RADIAL_SEGMENTS,
    TORUS_TUBULAR_SEGMENTS,
)
from validate_sculpt_spec import ATTACHMENT_PRIMITIVES, VALID_PRIMITIVES, validate_spec
from vertex_paint import VertexPaintError, normalize_vertex_paint
from visual_hull import MAX_VISUAL_HULL_TRIANGLES

# VALID_PRIMITIVES is re-exported rather than redefined. This module used to keep its own copy,
# and the two stayed identical only because someone edited both every time. A primitive present
# in one list and absent from the other does not error: line ~2873 silently rewrites it to "box",
# so the spec validates, the factory builds, and the wrong shape ships. Importing the validator's
# set makes that class of drift impossible instead of merely unlikely.
DEFAULT_PASS_ORDER = [
    "blockout",
    "structural-pass",
    "form-refinement",
    "material-pass",
    "surface-pass",
    "lighting-pass",
    "interaction-pass",
    "optimization-pass",
]
VISUAL_PASS_IDS = set(DEFAULT_PASS_ORDER) - {"optimization-pass"}
PASS_LEVELS = {
    "blockout": {"macro"},
    "structural-pass": {"macro", "meso"},
    "form-refinement": {"macro", "meso", "micro"},
    "material-pass": {"macro", "meso", "micro"},
    "surface-pass": {"macro", "meso", "micro"},
    "lighting-pass": {"macro", "meso", "micro"},
    "interaction-pass": {"macro", "meso", "micro"},
    "optimization-pass": {"macro", "meso", "micro"},
}


def load_spec(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("spec must be a JSON object")
    return payload


def strict_quality_failures(spec: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """Return normal validation output plus the failures that block production codegen."""
    errors, warnings = validate_spec(spec)
    failures = list(errors)
    failures.extend(
        f"strict quality failure: {warning.removeprefix('quality: ').strip()}"
        for warning in warnings
        if warning.startswith("quality:")
    )
    return errors, warnings, failures


def blocked_report(
    spec_path: Path,
    failures: list[str],
    warnings: list[str],
    pass_id: str | None,
) -> dict[str, Any]:
    """Build a machine-readable fail-closed report without touching the factory output."""
    return {
        "status": "BLOCKED",
        "phase": "strict-quality",
        "gate": "strict-quality",
        "artifact": str(spec_path),
        "metric": {
            "failureCount": len(failures),
            "qualityWarningCount": sum(1 for warning in warnings if warning.startswith("quality:")),
        },
        "cause": failures,
        "warnings": warnings,
        "requestedPass": pass_id,
        "nextAction": "refine-spec: author a subject-specific ObjectSculptSpec, then rerun strict-quality",
    }


def emit_blocked(report: dict[str, Any], report_path: Path | None) -> None:
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if report_path is not None:
        report_path = report_path.expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(payload + "\n", encoding="utf-8")
    print(payload, file=sys.stderr)


def pass_order(spec: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in spec.get("buildPasses", []):
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
            ids.append(item["id"])
    return ids or DEFAULT_PASS_ORDER.copy()


def review_visual_evidence(entry: dict[str, Any]) -> dict[str, Any]:
    visual = entry.get("visualEvidence")
    return visual if isinstance(visual, dict) else {}


def review_completes_pass(entry: dict[str, Any], pass_id: str) -> bool:
    if entry.get("passId") != pass_id or entry.get("action") != "continue":
        return False
    if pass_id in VISUAL_PASS_IDS and not review_visual_evidence(entry).get("renderScreenshot"):
        return False
    return True


def completed_passes(spec: dict[str, Any], ids: list[str]) -> list[str]:
    history = spec.get("reviewHistory", [])
    if not isinstance(history, list):
        return []
    completed: list[str] = []
    for pass_id in ids:
        if any(isinstance(entry, dict) and review_completes_pass(entry, pass_id) for entry in history):
            completed.append(pass_id)
        else:
            break
    return completed


def unlocked_pass(spec: dict[str, Any]) -> str:
    ids = pass_order(spec)
    completed = completed_passes(spec, ids)
    if len(completed) >= len(ids):
        return ids[-1]
    return ids[len(completed)]


def assert_pass_unlocked(spec: dict[str, Any], requested_pass: str) -> None:
    ids = pass_order(spec)
    if requested_pass not in ids:
        raise ValueError(f"unknown build pass {requested_pass!r}; expected one of: {', '.join(ids)}")
    completed = completed_passes(spec, ids)
    current = ids[-1] if len(completed) >= len(ids) else ids[len(completed)]
    if requested_pass in completed or requested_pass == current:
        return
    previous_index = ids.index(requested_pass) - 1
    previous = ids[previous_index] if previous_index >= 0 else ""
    raise ValueError(
        f"build pass {requested_pass!r} is locked; complete {previous!r} first with "
        "stage4_review/append_review.py action=continue and browser screenshot evidence"
    )


def component_refs_for_pass(spec: dict[str, Any], pass_id: str) -> set[str]:
    ids = pass_order(spec)
    if pass_id not in ids:
        return set()
    allowed_ids = set(ids[: ids.index(pass_id) + 1])
    refs: set[str] = set()
    for item in spec.get("buildPasses", []):
        if not isinstance(item, dict) or item.get("id") not in allowed_ids:
            continue
        component_refs = item.get("componentRefs", [])
        if isinstance(component_refs, list):
            refs.update(str(value) for value in component_refs if str(value).strip())
    return refs


def filter_components_for_pass(spec: dict[str, Any], components: list[dict[str, Any]], pass_id: str) -> list[dict[str, Any]]:
    allowed_levels = PASS_LEVELS.get(pass_id, {"macro"})
    explicit_refs = component_refs_for_pass(spec, pass_id)
    included: list[dict[str, Any]] = []
    included_ids: set[str] = set()
    component_by_id = {str(item.get("id")): item for item in components if item.get("id") is not None}

    def include_component(component: dict[str, Any]) -> None:
        component_id = str(component.get("id") or "")
        if not component_id or component_id in included_ids:
            return
        parent_id = component.get("parent")
        if parent_id is not None and str(parent_id) in component_by_id:
            include_component(component_by_id[str(parent_id)])
        included.append(component)
        included_ids.add(component_id)

    for component in components:
        component_id = str(component.get("id") or "")
        level = str(component.get("level") or "macro")
        tier = str(component.get("fidelityTier") or "")
        if component_id in explicit_refs or level in allowed_levels or tier == pass_id:
            include_component(component)
    if not included and components:
        included.append(components[0])
    return included


def pascal_case(value: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", value)
    return "".join(part[:1].upper() + part[1:] for part in parts) or "Object"


def const_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
    if not name:
        return "component"
    if name[0].isdigit():
        name = "_" + name
    return name


def local_var(prefix: str, value: str, index: int) -> str:
    return f"{prefix}_{const_name(value)}_{index}"


def hex_to_number(value: Any, fallback: str = "#8A7A5F") -> str:
    color = value if isinstance(value, str) else fallback
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        return "0x" + color[1:]
    if re.fullmatch(r"#[0-9A-Fa-f]{3}", color):
        return "0x" + "".join(ch * 2 for ch in color[1:])
    return "0x" + fallback[1:]


def material_base_value(value: Any, fallback: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, dict) and isinstance(value.get("base"), (int, float)):
        return float(value["base"])
    return fallback


def json_literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def vector(values: Any, fallback: list[float]) -> str:
    if (
        isinstance(values, list)
        and len(values) == 3
        and all(isinstance(item, (int, float)) for item in values)
    ):
        return ", ".join(str(float(item)) for item in values)
    return ", ".join(str(item) for item in fallback)


def scale_triple(component: dict[str, Any], transform: dict[str, Any]) -> tuple[float, float, float]:
    """The numeric factors `scale_vector` renders, for callers that must do arithmetic with them.

    This exists because `geometry.scale(...)` bakes the factors into the VERTEX DATA and leaves the
    pivot at scale 1. Anything that describes a component's surface analytically -- a ring stack to
    stand proud of, say -- therefore has to apply the same factors itself, because no matrix in the
    scene graph carries them. Reading the surface from the unit form instead put the skull's radius
    at 0.5 where the geometry actually sits at 0.2.
    """
    if "scale" in transform:
        values = transform.get("scale")
        if isinstance(values, list) and len(values) == 3 and all(
            isinstance(item, (int, float)) for item in values
        ):
            return (float(values[0]), float(values[1]), float(values[2]))
        return (1.0, 1.0, 1.0)
    dimensions = component.get("dimensions")
    if isinstance(dimensions, dict):
        radius = dimensions.get("radius")
        fallback = radius * 2 if isinstance(radius, (int, float)) else 1
        width = dimensions.get("width", fallback)
        height = dimensions.get("height", dimensions.get("length", 1))
        depth = dimensions.get("depth", fallback)
        if all(isinstance(item, (int, float)) for item in (width, height, depth)):
            return (float(width), float(height), float(depth))
    return (1.0, 1.0, 1.0)


def scale_vector(component: dict[str, Any], transform: dict[str, Any]) -> str:
    if "scale" in transform:
        return vector(transform.get("scale"), [1, 1, 1])
    dimensions = component.get("dimensions")
    if isinstance(dimensions, dict):
        width = dimensions.get("width", dimensions.get("radius", 0.5) * 2 if isinstance(dimensions.get("radius"), (int, float)) else 1)
        height = dimensions.get("height", dimensions.get("length", 1))
        depth = dimensions.get("depth", dimensions.get("radius", 0.5) * 2 if isinstance(dimensions.get("radius"), (int, float)) else 1)
        if all(isinstance(item, (int, float)) for item in (width, height, depth)):
            return vector([width, height, depth], [1, 1, 1])
    return vector(None, [1, 1, 1])


class GeometryNotImplementedError(Exception):
    """A component's primitive is a valid VALID_PRIMITIVES member but geometry_for()
    has no codegen branch for it yet. Never silently substitute a box for this case —
    that produced structurally-wrong renders (e.g. blade/knife primitives collapsing to
    a generic box) with no signal that anything was wrong. See Plan 1.3 Workstream F."""


_DEFAULT_EXTRUDE_PROFILE = {"points": [[-0.3, -0.3], [0.3, -0.3], [0.3, 0.3], [-0.3, 0.3]], "depth": 0.1}
# Plan 1.3 — ground blade: a real knife solid with a PRIMARY BEVEL (lower body grinds from
# full thickness at a mid grind-line down to a sharp cutting edge) and a SWEDGE / false edge
# (near the tip the spine also grinds to a false edge). Lofted from stations [x, spineY, edgeY].
_DEFAULT_BLADE_SPEC = {
    "stations": [
        [0.00, 0.080, -0.090], [0.12, 0.086, -0.100], [0.30, 0.086, -0.110],
        [0.50, 0.084, -0.108], [0.63, 0.078, -0.095], [0.74, 0.055, -0.055],
        [0.82, 0.028, -0.020], [0.88, 0.000, 0.000],
    ],
    "thickness": 0.05, "grindFrac": 0.55, "swedgeFromTipFrac": 0.34,
}
_DEFAULT_LATHE_PROFILE = {"points": [[0.3, -0.5], [0.15, 0.0], [0.3, 0.5]], "segments": 24}
_DEFAULT_TUBE_PATH = {"points": [[0.0, -0.5, 0.0], [0.0, 0.5, 0.0]], "radius": 0.05, "closed": False}
# Plan 1.3 F.6 — curve-sweep: a thin 2D cross-section swept along a measured 3D spine.
# The FIX for curved forms (hooked blades, handles) that a flat extrude only renders
# correctly from the reference camera angle. Default is a gentle S-curve so a missing
# spine still produces a real swept solid, not a straight bar.
_DEFAULT_CURVE_SWEEP = {
    "spine": [[-0.5, -0.4, 0.0], [-0.1, 0.1, 0.0], [0.3, 0.2, 0.0], [0.6, -0.1, 0.0]],
    "crossSection": {"points": [[-0.04, -0.02], [0.04, -0.02], [0.04, 0.02], [-0.04, 0.02]]},
    "closed": False,
}

# A sweep whose cross-section CHANGES along the spine. Every other sweep this factory emits --
# tube, curve-sweep, extrude -- carries one constant section from root to tip, so nothing that
# comes to a point (a hair lock, a horn, a tail, a blade tip, a finger) can be expressed by them.
# The default tapers to 8% of its root, which is the profile the grimoire prescribes; the
# validator warns when a spec's stations do not actually taper, because a barely-tapering sweep
# is a `tube` written the long way and reads as a noodle.
_DEFAULT_TAPERED_SWEEP = {
    "stations": [
        {"position": [0.0, -0.5, 0.0], "rx": 0.060, "rz": 0.040, "twist": 0.0},
        {"position": [0.0, -0.1, 0.0], "rx": 0.048, "rz": 0.030, "twist": 0.0},
        {"position": [0.0, 0.25, 0.0], "rx": 0.024, "rz": 0.014, "twist": 0.0},
        # A true point, not a small ring: the tip is where a lock, a horn or a blade has to close.
        {"position": [0.0, 0.5, 0.0], "rx": 0.0, "rz": 0.0, "twist": 0.0},
    ],
    "radialSegments": 10,
    "capEnds": True,
}


_VISUAL_HULL_HELPER_SOURCE = """type VisualHullVector = readonly [number, number, number];
type VisualHullAxis = 'front' | 'side' | 'top';
type VisualHullView = {
  readonly axis: VisualHullAxis;
  readonly confidence: number;
  readonly mask: readonly string[];
};
type VisualHullDescriptor = {
  readonly projection: 'orthographic';
  readonly boundsSpace: 'component-local';
  readonly bounds: { readonly min: VisualHullVector; readonly max: VisualHullVector };
  readonly resolution: number;
  readonly triangleBudget: number;
  readonly views: readonly VisualHullView[];
  readonly hiddenRegions?: readonly string[];
};
const MAX_VISUAL_HULL_TRIANGLES = __MAX_VISUAL_HULL_TRIANGLES__;

class VisualHullGeometryBudgetError extends Error {
  readonly triangleBudget: number;
  readonly emittedTriangles: number;

  constructor(triangleBudget: number, emittedTriangles: number) {
    super(`visual hull emitted ${emittedTriangles} triangles, exceeding budget ${triangleBudget}`);
    this.name = 'VisualHullGeometryBudgetError';
    this.triangleBudget = triangleBudget;
    this.emittedTriangles = emittedTriangles;
  }
}

class VisualHullOccupancyError extends Error {
  readonly occupiedVoxelCount: number;
  readonly emittedTriangles: number;

  constructor(occupiedVoxelCount: number, emittedTriangles: number) {
    super('visual hull silhouettes produced no bounded occupied surface');
    this.name = 'VisualHullOccupancyError';
    this.occupiedVoxelCount = occupiedVoxelCount;
    this.emittedTriangles = emittedTriangles;
  }
}

function visualHullMaskContains(view: VisualHullView, horizontal: number, vertical: number): boolean {
  const height = view.mask.length;
  const width = view.mask[0].length;
  const column = Math.max(0, Math.min(width - 1, Math.floor(horizontal * width)));
  const row = Math.max(0, Math.min(height - 1, Math.floor((1 - vertical) * height)));
  return view.mask[row][column] === '1';
}

function buildVisualHullGeometry(descriptor: VisualHullDescriptor): THREE.BufferGeometry {
  const resolution = descriptor.resolution;
  const min = new THREE.Vector3(...descriptor.bounds.min);
  const max = new THREE.Vector3(...descriptor.bounds.max);
  const step = max.clone().sub(min).multiplyScalar(1 / resolution);
  const indexAt = (x: number, y: number, z: number): number => (z * resolution + y) * resolution + x;
  const occupied = new Uint8Array(resolution * resolution * resolution);
  let occupiedVoxelCount = 0;
  const contains = (x: number, y: number, z: number): boolean => (
    x >= 0 && y >= 0 && z >= 0 && x < resolution && y < resolution && z < resolution && occupied[indexAt(x, y, z)] === 1
  );
  for (let z = 0; z < resolution; z += 1) {
    for (let y = 0; y < resolution; y += 1) {
      for (let x = 0; x < resolution; x += 1) {
        const horizontalX = (x + 0.5) / resolution;
        const verticalY = (y + 0.5) / resolution;
        const horizontalZ = (z + 0.5) / resolution;
        let inside = true;
        for (const view of descriptor.views) {
          switch (view.axis) {
            case 'front':
              inside = inside && visualHullMaskContains(view, horizontalX, verticalY);
              break;
            case 'side':
              inside = inside && visualHullMaskContains(view, horizontalZ, verticalY);
              break;
            case 'top':
              inside = inside && visualHullMaskContains(view, horizontalX, horizontalZ);
              break;
          }
          if (!inside) break;
        }
        if (inside) {
          occupied[indexAt(x, y, z)] = 1;
          occupiedVoxelCount += 1;
        }
      }
    }
  }
  if (occupiedVoxelCount === 0) throw new VisualHullOccupancyError(0, 0);
  const positions: number[] = [];
  const indices: number[] = [];
  const vertices = new Map<string, number>();
  const vertexAt = (x: number, y: number, z: number): number => {
    const key = `${x},${y},${z}`;
    const existing = vertices.get(key);
    if (existing !== undefined) return existing;
    const vertex = positions.length / 3;
    positions.push(min.x + x * step.x, min.y + y * step.y, min.z + z * step.z);
    vertices.set(key, vertex);
    return vertex;
  };
  const addFace = (a: number, b: number, c: number, d: number): void => {
    indices.push(a, b, c, a, c, d);
    const emittedTriangles = indices.length / 3;
    if (emittedTriangles > descriptor.triangleBudget || emittedTriangles > MAX_VISUAL_HULL_TRIANGLES) {
      throw new VisualHullGeometryBudgetError(descriptor.triangleBudget, emittedTriangles);
    }
  };
  for (let z = 0; z < resolution; z += 1) {
    for (let y = 0; y < resolution; y += 1) {
      for (let x = 0; x < resolution; x += 1) {
        if (!contains(x, y, z)) continue;
        if (!contains(x - 1, y, z)) addFace(vertexAt(x, y, z), vertexAt(x, y, z + 1), vertexAt(x, y + 1, z + 1), vertexAt(x, y + 1, z));
        if (!contains(x + 1, y, z)) addFace(vertexAt(x + 1, y, z), vertexAt(x + 1, y + 1, z), vertexAt(x + 1, y + 1, z + 1), vertexAt(x + 1, y, z + 1));
        if (!contains(x, y - 1, z)) addFace(vertexAt(x, y, z), vertexAt(x + 1, y, z), vertexAt(x + 1, y, z + 1), vertexAt(x, y, z + 1));
        if (!contains(x, y + 1, z)) addFace(vertexAt(x, y + 1, z), vertexAt(x, y + 1, z + 1), vertexAt(x + 1, y + 1, z + 1), vertexAt(x + 1, y + 1, z));
        if (!contains(x, y, z - 1)) addFace(vertexAt(x, y, z), vertexAt(x, y + 1, z), vertexAt(x + 1, y + 1, z), vertexAt(x + 1, y, z));
        if (!contains(x, y, z + 1)) addFace(vertexAt(x, y, z + 1), vertexAt(x + 1, y, z + 1), vertexAt(x + 1, y + 1, z + 1), vertexAt(x, y + 1, z + 1));
      }
    }
  }
  if (indices.length === 0) throw new VisualHullOccupancyError(occupiedVoxelCount, 0);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(indices);
  const uv: number[] = [];
  for (let index = 0; index < positions.length; index += 3) {
    uv.push((positions[index] - min.x) / (max.x - min.x), (positions[index + 1] - min.y) / (max.y - min.y));
  }
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  geometry.computeVertexNormals();
  geometry.userData.visualHull = {
    method: 'orthographic-silhouette-intersection',
    observedAxes: descriptor.views.map((view) => view.axis),
    viewConfidence: descriptor.views.map((view) => ({ axis: view.axis, confidence: view.confidence })),
    hiddenRegions: descriptor.hiddenRegions ?? ['Unobserved depth detail remains underconstrained by silhouette intersection.'],
    lowConfidence: {
      hiddenRegions: true,
      reason: 'Silhouette intersection constrains the exterior envelope, not unseen surface detail.',
    },
  };
  return geometry;
}"""


_DECIMATE_HELPER_SOURCE = '''// Quadric-error decimation (Garland & Heckbert 1997), ported from
// forge/stage3_build/decimate.py so it can run on geometry that only exists at runtime.
//
// WHY IT RUNS HERE RATHER THAN OFFLINE: the Python version reads a mesh file, and this
// factory does not produce one -- an implicit surface is polygonised in the browser from
// its descriptor, so there is no mesh to decimate until the page is running. Baking a
// vertex array into this file instead would contradict the whole point of a code-only
// procedural model.
//
// WHY IT RUNS BEFORE BINDING: a quadric collapse merges two vertices into one, and
// skinIndex/skinWeight would have to be interpolated across that merge to stay correct.
// The generated bind pass recomputes weights from `position` afterwards, so decimating
// first means the weights are simply computed on the surviving vertices and no
// interpolation of skinning data ever has to happen.
//
// LIMITATION, stated rather than discovered later: only `position` survives. UVs are
// dropped and normals are recomputed, so this must not be applied to geometry carrying
// authored UVs. Its intended input is polygonizeSdf output, which is position + index
// + computed normals and therefore loses nothing.
//
// Two collapses are refused, exactly as in the Python original, because both produce a
// mesh that passes a triangle count while no longer being a surface:
//   * one that would FLIP a face -- the neighbouring normal reversing means the surface
//     folded through itself, which is the defect self_intersection.py exists to catch;
//   * one on a BOUNDARY edge -- an open border erodes inward and the silhouette visibly
//     retreats, the classic "the LOD is smaller than the original" artefact.
function decimateGeometry(source: THREE.BufferGeometry, targetRatio: number): THREE.BufferGeometry {
  const MIN_TRIANGLES = 4;
  const position = source.getAttribute('position');
  if (!position || !(targetRatio > 0) || targetRatio >= 1) return source;

  const vertices: number[][] = [];
  for (let i = 0; i < position.count; i++) {
    vertices.push([position.getX(i), position.getY(i), position.getZ(i)]);
  }
  const index = source.getIndex();
  const faces: number[][] = [];
  if (index) {
    for (let i = 0; i + 2 < index.count; i += 3) {
      faces.push([index.getX(i), index.getX(i + 1), index.getX(i + 2)]);
    }
  } else {
    for (let i = 0; i + 2 < position.count; i += 3) faces.push([i, i + 1, i + 2]);
  }
  if (!faces.length) return source;

  const originalCount = faces.length;
  const targetCount = Math.max(MIN_TRIANGLES, Math.floor(originalCount * targetRatio));
  if (originalCount <= targetCount) return source;

  const planeOf = (a: number[], b: number[], c: number[]): number[] | null => {
    const e1 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    const e2 = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
    const n = [
      e1[1] * e2[2] - e1[2] * e2[1],
      e1[2] * e2[0] - e1[0] * e2[2],
      e1[0] * e2[1] - e1[1] * e2[0],
    ];
    const len = Math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]);
    if (len <= 1e-12) return null;
    const u = [n[0] / len, n[1] / len, n[2] / len];
    return [u[0], u[1], u[2], -(u[0] * a[0] + u[1] * a[1] + u[2] * a[2])];
  };
  // Upper triangle of the symmetric 4x4, row-major -- same 10-element layout as the Python side.
  const quadricOf = (p: number[]): number[] => {
    const [a, b, c, d] = p;
    return [a * a, a * b, a * c, a * d, b * b, b * c, b * d, c * c, c * d, d * d];
  };
  const addQuadric = (q1: number[], q2: number[]): number[] => q1.map((x, i) => x + q2[i]);
  const quadricError = (q: number[], v: number[]): number => {
    const [x, y, z] = v;
    return (
      q[0] * x * x + 2 * q[1] * x * y + 2 * q[2] * x * z + 2 * q[3] * x +
      q[4] * y * y + 2 * q[5] * y * z + 2 * q[6] * y +
      q[7] * z * z + 2 * q[8] * z + q[9]
    );
  };

  const alive = faces.map(() => true);
  const vertexFaces: Set<number>[] = vertices.map(() => new Set<number>());
  faces.forEach((face, i) => face.forEach((v) => vertexFaces[v].add(i)));

  const quadrics: number[][] = vertices.map(() => new Array(10).fill(0));
  faces.forEach((face, i) => {
    const p = planeOf(vertices[face[0]], vertices[face[1]], vertices[face[2]]);
    if (!p) { alive[i] = false; return; }
    const q = quadricOf(p);
    face.forEach((v) => { quadrics[v] = addQuadric(quadrics[v], q); });
  });

  const edgeFaces = new Map<string, number>();
  const edgeKey = (u: number, v: number) => (u < v ? u + ':' + v : v + ':' + u);
  faces.forEach((face) => {
    const [a, b, c] = face;
    for (const [u, v] of [[a, b], [b, c], [c, a]]) {
      const k = edgeKey(u, v);
      edgeFaces.set(k, (edgeFaces.get(k) ?? 0) + 1);
    }
  });
  const boundary = new Set<string>();
  edgeFaces.forEach((count, k) => { if (count === 1) boundary.add(k); });

  const midpointOf = (u: number, v: number): number[] =>
    [0, 1, 2].map((i) => (vertices[u][i] + vertices[v][i]) / 2);
  // Midpoint rather than the quadric-optimal position: solving the 3x3 needs an inverse that
  // is singular on flat regions, and the midpoint keeps the collapse on the original surface.
  const costOf = (u: number, v: number): number =>
    quadricError(addQuadric(quadrics[u], quadrics[v]), midpointOf(u, v));

  // Binary heap keyed on collapse cost; entries are stale-checked on pop rather than updated
  // in place, the same lazy-deletion approach heapq gets in the Python original.
  const heap: { cost: number; u: number; v: number }[] = [];
  const heapPush = (entry: { cost: number; u: number; v: number }) => {
    heap.push(entry);
    let i = heap.length - 1;
    while (i > 0) {
      const parent = (i - 1) >> 1;
      if (heap[parent].cost <= heap[i].cost) break;
      [heap[parent], heap[i]] = [heap[i], heap[parent]];
      i = parent;
    }
  };
  const heapPop = () => {
    const top = heap[0];
    const last = heap.pop()!;
    if (heap.length) {
      heap[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = 2 * i + 2;
        let small = i;
        if (l < heap.length && heap[l].cost < heap[small].cost) small = l;
        if (r < heap.length && heap[r].cost < heap[small].cost) small = r;
        if (small === i) break;
        [heap[small], heap[i]] = [heap[i], heap[small]];
        i = small;
      }
    }
    return top;
  };

  edgeFaces.forEach((_count, k) => {
    if (boundary.has(k)) return;
    const [u, v] = k.split(':').map(Number);
    heapPush({ cost: costOf(u, v), u, v });
  });

  let liveFaces = alive.reduce((n, flag) => n + (flag ? 1 : 0), 0);
  while (heap.length && liveFaces > targetCount) {
    const { u, v } = heapPop();
    if (u === v || !vertexFaces[u].size || !vertexFaces[v].size) continue;
    const shared = new Set<number>();
    vertexFaces[u].forEach((i) => { if (vertexFaces[v].has(i)) shared.add(i); });
    let anyAlive = false;
    shared.forEach((i) => { if (alive[i]) anyAlive = true; });
    if (!anyAlive) continue;
    const merged = midpointOf(u, v);

    let flips = false;
    const affected = new Set<number>();
    vertexFaces[u].forEach((i) => { if (!shared.has(i)) affected.add(i); });
    vertexFaces[v].forEach((i) => { if (!shared.has(i)) affected.add(i); });
    for (const i of affected) {
      if (!alive[i] || flips) continue;
      const face = faces[i];
      const before = planeOf(vertices[face[0]], vertices[face[1]], vertices[face[2]]);
      const pts = face.map((w) => (w === u || w === v ? merged : vertices[w]));
      const after = planeOf(pts[0], pts[1], pts[2]);
      if (!before || !after) { flips = true; break; }
      if (before[0] * after[0] + before[1] * after[1] + before[2] * after[2] < 0) { flips = true; break; }
    }
    if (flips) continue;

    vertices[u] = merged;
    quadrics[u] = addQuadric(quadrics[u], quadrics[v]);
    shared.forEach((i) => { if (alive[i]) { alive[i] = false; liveFaces -= 1; } });
    Array.from(vertexFaces[v]).forEach((i) => {
      if (!alive[i]) return;
      faces[i] = faces[i].map((w) => (w === v ? u : w));
      vertexFaces[u].add(i);
    });
    vertexFaces[v] = new Set<number>();
    Array.from(vertexFaces[u]).forEach((i) => {
      if (!alive[i]) return;
      faces[i].forEach((w) => { if (w !== u) heapPush({ cost: costOf(u, w), u, v: w }); });
    });
  }

  const kept = faces.filter((_face, i) => alive[i]);
  const used = Array.from(new Set(kept.flat())).sort((a, b) => a - b);
  const remap = new Map<number, number>();
  used.forEach((old, next) => remap.set(old, next));
  const outPositions: number[] = [];
  used.forEach((old) => outPositions.push(vertices[old][0], vertices[old][1], vertices[old][2]));
  const outIndices: number[] = [];
  kept.forEach((face) => face.forEach((w) => outIndices.push(remap.get(w)!)));

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(outPositions, 3));
  geometry.setIndex(outIndices);
  geometry.computeVertexNormals();
  geometry.userData.decimation = {
    originalTriangleCount: originalCount,
    triangleCount: kept.length,
    targetTriangleCount: targetCount,
    // Reported because stopping short is not a failure but IS something the caller must know:
    // on a mesh whose remaining collapses would all flip a face, the honest outcome is fewer
    // triangles removed, not a folded mesh that hits the number.
    reachedTarget: kept.length <= targetCount,
  };
  return geometry;
}'''


_SDF_HELPER_SOURCE = """type SdfVector = readonly [number, number, number];
type SdfTransform = { position?: SdfVector; translation?: SdfVector; rotation?: SdfVector; scale?: SdfVector };
type SdfPrimitive = {
  readonly id: string;
  readonly type: 'sphere' | 'capsule' | 'box' | 'cone' | 'ellipsoid';
  readonly center?: SdfVector;
  readonly radius?: number | SdfVector;
  readonly height?: number;
  readonly size?: SdfVector;
  readonly dimensions?: SdfVector;
  readonly radii?: SdfVector;
  readonly transform?: SdfTransform;
};
type SdfOperation = {
  readonly id?: string;
  readonly output?: string;
  readonly type: 'smooth-union' | 'subtract' | 'intersect';
  readonly left: string;
  readonly right: string;
  readonly radius?: number;
};
type SdfDescriptor = {
  readonly primitives: readonly SdfPrimitive[];
  readonly operations?: readonly SdfOperation[];
  readonly resolution: number;
  readonly bounds?: { readonly min: SdfVector; readonly max: SdfVector };
};
type SdfFunction = (point: THREE.Vector3) => number;

function sdfSphere(point: THREE.Vector3, radius: number): number {
  return point.length() - radius;
}

function sdfCapsule(point: THREE.Vector3, radius: number, height: number): number {
  const halfHeight = height * 0.5;
  const y = Math.max(-halfHeight, Math.min(halfHeight, point.y));
  return point.distanceTo(new THREE.Vector3(0, y, 0)) - radius;
}

function sdfBox(point: THREE.Vector3, size: SdfVector): number {
  const q = new THREE.Vector3(Math.abs(point.x), Math.abs(point.y), Math.abs(point.z))
    .sub(new THREE.Vector3(size[0] * 0.5, size[1] * 0.5, size[2] * 0.5));
  return q.clone().max(new THREE.Vector3()).length() + Math.min(Math.max(q.x, q.y, q.z), 0);
}

function sdfCone(point: THREE.Vector3, radius: number, height: number): number {
  const halfHeight = height * 0.5;
  const taper = radius * (1 - (point.y + halfHeight) / height);
  return Math.max(Math.hypot(point.x, point.z) - Math.max(0, taper), Math.abs(point.y) - halfHeight);
}

function sdfEllipsoid(point: THREE.Vector3, radii: SdfVector): number {
  const scaled = new THREE.Vector3(point.x / radii[0], point.y / radii[1], point.z / radii[2]);
  return (scaled.length() - 1) * Math.min(radii[0], radii[1], radii[2]);
}

function sdfRadii(primitive: SdfPrimitive): SdfVector {
  const radius = primitive.radius;
  if (primitive.radii) return primitive.radii;
  if (typeof radius === 'number') return [radius, radius, radius];
  return radius ?? [0.5, 0.5, 0.5];
}

function smin(left: number, right: number, radius: number): number {
  const blend = Math.max(radius - Math.abs(left - right), 0) / radius;
  return Math.min(left, right) - blend * blend * radius * 0.25;
}

function sdfLocalPoint(point: THREE.Vector3, primitive: SdfPrimitive): { point: THREE.Vector3; scale: number } {
  const transform = primitive.transform;
  const translation = transform?.position ?? transform?.translation ?? primitive.center ?? [0, 0, 0];
  const rotation = transform?.rotation ?? [0, 0, 0];
  const scale = transform?.scale ?? [1, 1, 1];
  const local = point.clone().sub(new THREE.Vector3(translation[0], translation[1], translation[2]));
  const inverseRotation = new THREE.Quaternion()
    .setFromEuler(new THREE.Euler(rotation[0], rotation[1], rotation[2]))
    .invert();
  local.applyQuaternion(inverseRotation);
  local.set(local.x / scale[0], local.y / scale[1], local.z / scale[2]);
  return { point: local, scale: Math.min(scale[0], scale[1], scale[2]) };
}

function sdfPrimitive(point: THREE.Vector3, primitive: SdfPrimitive): number {
  const local = sdfLocalPoint(point, primitive);
  let distance: number;
  switch (primitive.type) {
    case 'sphere':
      distance = sdfSphere(local.point, typeof primitive.radius === 'number' ? primitive.radius : 0.5);
      break;
    case 'capsule':
      distance = sdfCapsule(local.point, typeof primitive.radius === 'number' ? primitive.radius : 0.25, primitive.height ?? 1);
      break;
    case 'box':
      distance = sdfBox(local.point, primitive.size ?? primitive.dimensions ?? [1, 1, 1]);
      break;
    case 'cone':
      distance = sdfCone(local.point, typeof primitive.radius === 'number' ? primitive.radius : 0.5, primitive.height ?? 1);
      break;
    case 'ellipsoid':
      distance = sdfEllipsoid(local.point, sdfRadii(primitive));
      break;
  }
  return distance * local.scale;
}

function sdfSample(descriptor: SdfDescriptor): SdfFunction {
  const nodes = new Map<string, SdfFunction>();
  for (const primitive of descriptor.primitives) nodes.set(primitive.id, (point) => sdfPrimitive(point, primitive));
  let result = descriptor.primitives.length > 0 ? nodes.get(descriptor.primitives[0].id) : undefined;
  for (let index = 0; index < (descriptor.operations?.length ?? 0); index += 1) {
    const operation = descriptor.operations?.[index];
    if (!operation) continue;
    const left = nodes.get(operation.left);
    const right = nodes.get(operation.right);
    if (!left || !right) continue;
    let combined: SdfFunction;
    switch (operation.type) {
      case 'smooth-union':
        combined = (point) => smin(left(point), right(point), operation.radius ?? 0.1);
        break;
      case 'subtract':
        combined = (point) => Math.max(left(point), -right(point));
        break;
      case 'intersect':
        combined = (point) => Math.max(left(point), right(point));
        break;
    }
    nodes.set(operation.id ?? operation.output ?? `operation-${index}`, combined);
    result = combined;
  }
  return result ?? (() => Infinity);
}

function polygonizeSdf(descriptor: SdfDescriptor): THREE.BufferGeometry {
  // SURFACE NETS, not a voxel shell.
  //
  // This used to emit one axis-aligned quad per exposed voxel face, which is a Minecraft surface:
  // every face is axis-aligned, every edge is a 90-degree step, and the result is stair-stepped at
  // exactly the scale of the sampling grid. For a subject whose whole identity is smooth blended
  // organic form -- which is the only kind of subject anyone reaches for an implicit surface to
  // build -- that is worse than the assembled primitives it was meant to replace.
  //
  // Naive surface nets places ONE vertex per sign-changing cell, at the average of the linearly
  // interpolated crossings on that cell's edges, and joins the four cells around each crossing
  // edge into a quad. It is compact, manifold, and smooth, and it is a natural fit for a field
  // that can be sampled anywhere rather than only at corners.
  //
  // Normals come from the field GRADIENT, not from face averaging: the gradient is the exact
  // surface normal of the implicit surface, so shading no longer carries the grid's imprint.
  const resolution = Math.max(4, Math.min(64, Math.floor(descriptor.resolution)));
  const defaultBounds: { readonly min: SdfVector; readonly max: SdfVector } = { min: [-2, -2, -2], max: [2, 2, 2] };
  const bounds = descriptor.bounds ?? defaultBounds;
  const min = new THREE.Vector3(bounds.min[0], bounds.min[1], bounds.min[2]);
  const step = new THREE.Vector3(
    (bounds.max[0] - bounds.min[0]) / resolution,
    (bounds.max[1] - bounds.min[1]) / resolution,
    (bounds.max[2] - bounds.min[2]) / resolution,
  );
  const sample = sdfSample(descriptor);
  const scratch = new THREE.Vector3();

  // Corner grid: one more corner than cells on each axis.
  const side = resolution + 1;
  const field = new Float32Array(side * side * side);
  const cornerAt = (x: number, y: number, z: number): number => (z * side + y) * side + x;
  for (let z = 0; z < side; z += 1) {
    for (let y = 0; y < side; y += 1) {
      for (let x = 0; x < side; x += 1) {
        scratch.set(min.x + x * step.x, min.y + y * step.y, min.z + z * step.z);
        field[cornerAt(x, y, z)] = sample(scratch);
      }
    }
  }

  // The 12 cell edges as corner-offset pairs.
  const CUBE_EDGES: readonly (readonly [number, number, number, number, number, number])[] = [
    [0, 0, 0, 1, 0, 0], [1, 0, 0, 1, 1, 0], [0, 1, 0, 1, 1, 0], [0, 0, 0, 0, 1, 0],
    [0, 0, 1, 1, 0, 1], [1, 0, 1, 1, 1, 1], [0, 1, 1, 1, 1, 1], [0, 0, 1, 0, 1, 1],
    [0, 0, 0, 0, 0, 1], [1, 0, 0, 1, 0, 1], [1, 1, 0, 1, 1, 1], [0, 1, 0, 0, 1, 1],
  ];

  const positions: number[] = [];
  const normals: number[] = [];
  const indices: number[] = [];
  const cellVertex = new Int32Array(resolution * resolution * resolution).fill(-1);
  const cellAt = (x: number, y: number, z: number): number => (z * resolution + y) * resolution + x;

  // Central-difference gradient, stepped at a fraction of a cell so it follows the field rather
  // than the grid.
  const epsilon = Math.min(step.x, step.y, step.z) * 0.25;
  const gradient = (point: THREE.Vector3): THREE.Vector3 => {
    const gx = sample(scratch.set(point.x + epsilon, point.y, point.z))
      - sample(scratch.set(point.x - epsilon, point.y, point.z));
    const gy = sample(scratch.set(point.x, point.y + epsilon, point.z))
      - sample(scratch.set(point.x, point.y - epsilon, point.z));
    const gz = sample(scratch.set(point.x, point.y, point.z + epsilon))
      - sample(scratch.set(point.x, point.y, point.z - epsilon));
    const normal = new THREE.Vector3(gx, gy, gz);
    // A point where the field is flat has no defined normal; +Y is arbitrary but finite, and
    // leaving a zero vector would poison every lighting calculation downstream.
    return normal.lengthSq() < 1e-20 ? new THREE.Vector3(0, 1, 0) : normal.normalize();
  };

  for (let z = 0; z < resolution; z += 1) {
    for (let y = 0; y < resolution; y += 1) {
      for (let x = 0; x < resolution; x += 1) {
        let crossings = 0;
        let sumX = 0;
        let sumY = 0;
        let sumZ = 0;
        for (const [ax, ay, az, bx, by, bz] of CUBE_EDGES) {
          const a = field[cornerAt(x + ax, y + ay, z + az)];
          const b = field[cornerAt(x + bx, y + by, z + bz)];
          if ((a <= 0) === (b <= 0)) continue;
          const t = a / (a - b);
          sumX += (ax + (bx - ax) * t);
          sumY += (ay + (by - ay) * t);
          sumZ += (az + (bz - az) * t);
          crossings += 1;
        }
        if (crossings === 0) continue;
        const px = min.x + (x + sumX / crossings) * step.x;
        const py = min.y + (y + sumY / crossings) * step.y;
        const pz = min.z + (z + sumZ / crossings) * step.z;
        cellVertex[cellAt(x, y, z)] = positions.length / 3;
        positions.push(px, py, pz);
        const normal = gradient(new THREE.Vector3(px, py, pz));
        normals.push(normal.x, normal.y, normal.z);
      }
    }
  }

  // One quad per sign-changing grid edge, joining the four cells that share it.
  //
  // Winding, worked out rather than guessed. For the +x edge from corner (x,y,z), the four cells
  // around it are (x, y-1, z-1), (x, y, z-1), (x, y, z), (x, y-1, z); in the (y,z) plane that
  // traversal is +y, +z, -y, whose cross product is +x. So when the corner is INSIDE and its
  // neighbour is outside, the unflipped order already faces out, and the flip belongs on the
  // opposite case. Getting this backwards is invisible in the normals -- those come from the
  // gradient and stay correct -- and shows only as back-face culling removing the front surface,
  // i.e. the model rendering as a hollow shell with its interior visible.
  const quad = (a: number, b: number, c: number, d: number, flip: boolean): void => {
    if (a < 0 || b < 0 || c < 0 || d < 0) return;
    if (flip) indices.push(a, c, b, a, d, c);
    else indices.push(a, b, c, a, c, d);
  };
  // Each quad joins the FOUR cells sharing one grid edge, so every one of those cells must exist.
  // Bounding only the edge axis and the lower end of the other two let y/z reach `resolution`, which
  // is a corner index, not a cell index: `cellAt` then strides into an unrelated slot (with
  // resolution 8, `cellAt(3, 8, 1)` is 131 -- the slot for cell (3, 0, 2)) or past the end of the
  // array, where a typed-array read yields `undefined`. `undefined < 0` is false, so the guard in
  // `quad` passed it through to `setIndex`, which coerces it to 0. Measured on a sphere reaching its
  // own bounds at resolution 8: 60 out-of-range reads and 108 aliased reads. A surface that touches
  // the sampling box is therefore left OPEN at that face rather than closed with wrong triangles --
  // pad `bounds` past the surface to get a closed mesh.
  for (let z = 0; z < side; z += 1) {
    for (let y = 0; y < side; y += 1) {
      for (let x = 0; x < side; x += 1) {
        const here = field[cornerAt(x, y, z)] <= 0;
        if (x + 1 < side && y > 0 && z > 0 && y < side - 1 && z < side - 1
          && here !== (field[cornerAt(x + 1, y, z)] <= 0)) {
          quad(
            cellVertex[cellAt(x, y - 1, z - 1)], cellVertex[cellAt(x, y, z - 1)],
            cellVertex[cellAt(x, y, z)], cellVertex[cellAt(x, y - 1, z)], !here,
          );
        }
        if (y + 1 < side && x > 0 && z > 0 && x < side - 1 && z < side - 1
          && here !== (field[cornerAt(x, y + 1, z)] <= 0)) {
          quad(
            cellVertex[cellAt(x - 1, y, z - 1)], cellVertex[cellAt(x - 1, y, z)],
            cellVertex[cellAt(x, y, z)], cellVertex[cellAt(x, y, z - 1)], !here,
          );
        }
        if (z + 1 < side && x > 0 && y > 0 && x < side - 1 && y < side - 1
          && here !== (field[cornerAt(x, y, z + 1)] <= 0)) {
          quad(
            cellVertex[cellAt(x - 1, y - 1, z)], cellVertex[cellAt(x, y - 1, z)],
            cellVertex[cellAt(x, y, z)], cellVertex[cellAt(x - 1, y, z)], !here,
          );
        }
      }
    }
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
  geometry.setIndex(indices);
  geometry.computeBoundingSphere();
  return geometry;
}"""


_SUBDIVISION_HELPER_SOURCE = """type SubdivisionFace = number[];
type SubdivisionEdge = {
  a: number;
  b: number;
  faces: number[];
};
type SubdivisionUvChannel = {
  name: string;
  faces: THREE.Vector2[][];
};
const MAX_SUBDIVISION_QUAD_FACES = __MAX_SUBDIVISION_QUAD_FACES__;
const MAX_SUBDIVISION_ITERATIONS = __MAX_SUBDIVISION_ITERATIONS__;

class SubdivisionInputError extends Error {
  readonly iterations: unknown;

  constructor(iterations: unknown) {
    super(`subdivision iterations must be an integer from 0 to ${MAX_SUBDIVISION_ITERATIONS}`);
    this.name = 'SubdivisionInputError';
    this.iterations = iterations;
  }
}

class SubdivisionGeometryBudgetError extends Error {
  readonly sourceFaceCount: number;
  readonly projectedQuadFaceCount: number;

  constructor(sourceFaceCount: number, projectedQuadFaceCount: number) {
    super(`subdivision would produce ${projectedQuadFaceCount} quad faces from ${sourceFaceCount} source faces`);
    this.name = 'SubdivisionGeometryBudgetError';
    this.sourceFaceCount = sourceFaceCount;
    this.projectedQuadFaceCount = projectedQuadFaceCount;
  }
}

class SubdivisionTopologyError extends Error {
  readonly reason: string;

  constructor(reason: string) {
    super(reason);
    this.name = 'SubdivisionTopologyError';
    this.reason = reason;
  }
}

function subdivideCatmullClark(source: THREE.BufferGeometry, iterations: number): THREE.BufferGeometry {
  if (!Number.isInteger(iterations) || iterations < 0 || iterations > MAX_SUBDIVISION_ITERATIONS) {
    throw new SubdivisionInputError(iterations);
  }
  if (iterations === 0) return source;
  const position = source.getAttribute('position');
  if (!position) {
    source.computeVertexNormals();
    return source;
  }

  const sourcePositions: THREE.Vector3[] = [];
  const sourceVertexByKey = new Map<string, number>();
  const sourceUvChannels: SubdivisionUvChannel[] = [];
  for (const name of ['uv', 'uv2']) {
    const attribute = source.getAttribute(name);
    if (attribute && attribute.itemSize >= 2) sourceUvChannels.push({ name, faces: [] });
  }
  const vertexIndexAt = (attributeIndex: number): number => {
    const x = position.getX(attributeIndex);
    const y = position.getY(attributeIndex);
    const z = position.getZ(attributeIndex);
    const key = `${Math.round(x * 1000000)},${Math.round(y * 1000000)},${Math.round(z * 1000000)}`;
    const existing = sourceVertexByKey.get(key);
    if (existing !== undefined) return existing;
    const vertexIndex = sourcePositions.length;
    sourcePositions.push(new THREE.Vector3(x, y, z));
    sourceVertexByKey.set(key, vertexIndex);
    return vertexIndex;
  };
  const sourceIndex = source.getIndex();
  const sourceVertexCount = sourceIndex?.count ?? position.count;
  const sourceTriangles: SubdivisionFace[] = [];
  const sourceTriangleAttributeIndices: number[][] = [];
  for (let index = 0; index + 2 < sourceVertexCount; index += 3) {
    const attributeA = sourceIndex ? sourceIndex.getX(index) : index;
    const attributeB = sourceIndex ? sourceIndex.getX(index + 1) : index + 1;
    const attributeC = sourceIndex ? sourceIndex.getX(index + 2) : index + 2;
    const a = vertexIndexAt(attributeA);
    const b = vertexIndexAt(attributeB);
    const c = vertexIndexAt(attributeC);
    if (a !== b && b !== c && c !== a) {
      sourceTriangles.push([a, b, c]);
      sourceTriangleAttributeIndices.push([attributeA, attributeB, attributeC]);
    }
  }

  const triangleNormal = (face: SubdivisionFace): THREE.Vector3 => {
    const a = sourcePositions[face[0]];
    const b = sourcePositions[face[1]];
    const c = sourcePositions[face[2]];
    return new THREE.Vector3().subVectors(b, a).cross(new THREE.Vector3().subVectors(c, a)).normalize();
  };
  const edgeKey = (a: number, b: number): string => (a < b ? `${a}:${b}` : `${b}:${a}`);
  const triangleEdges = new Map<string, number[]>();
  for (let triangleIndex = 0; triangleIndex < sourceTriangles.length; triangleIndex += 1) {
    const triangle = sourceTriangles[triangleIndex];
    for (let vertexIndex = 0; vertexIndex < triangle.length; vertexIndex += 1) {
      const a = triangle[vertexIndex];
      const b = triangle[(vertexIndex + 1) % triangle.length];
      const key = edgeKey(a, b);
      const entries = triangleEdges.get(key) ?? [];
      entries.push(triangleIndex);
      triangleEdges.set(key, entries);
    }
  }

  const pairedTriangles = new Set<number>();
  let faces: SubdivisionFace[] = [];
  let faceAttributeIndices: number[][] = [];
    for (const triangleIndices of triangleEdges.values()) {
      if (triangleIndices.length !== 2) continue;
      const leftIndex = triangleIndices[0];
      const rightIndex = triangleIndices[1];
      if (pairedTriangles.has(leftIndex) || pairedTriangles.has(rightIndex)) continue;
      const left = sourceTriangles[leftIndex];
      const right = sourceTriangles[rightIndex];
      if (triangleNormal(left).dot(triangleNormal(right)) < 0.999999) continue;
      const leftAttributes = sourceTriangleAttributeIndices[leftIndex];
      const rightAttributes = sourceTriangleAttributeIndices[rightIndex];
      const directedEdges: Array<{ start: number; end: number; attributeIndex: number }> = [
        { start: left[0], end: left[1], attributeIndex: leftAttributes[0] },
        { start: left[1], end: left[2], attributeIndex: leftAttributes[1] },
        { start: left[2], end: left[0], attributeIndex: leftAttributes[2] },
        { start: right[0], end: right[1], attributeIndex: rightAttributes[0] },
        { start: right[1], end: right[2], attributeIndex: rightAttributes[1] },
        { start: right[2], end: right[0], attributeIndex: rightAttributes[2] },
      ];
      const boundaryEdges = directedEdges.filter(
        (edge, edgeIndex) => !directedEdges.some(
          (candidate, candidateIndex) => (
            candidateIndex !== edgeIndex && candidate.start === edge.end && candidate.end === edge.start
          ),
        ),
      );
      if (boundaryEdges.length !== 4) continue;
      const face: number[] = [];
      const attributeIndices: number[] = [];
      let edge = boundaryEdges[0];
      let closesFace = false;
      while (face.length < 4) {
        face.push(edge.start);
        attributeIndices.push(edge.attributeIndex);
        if (face.length === 4) {
          closesFace = edge.end === face[0];
          break;
        }
        const nextEdge = boundaryEdges.find((candidate) => candidate.start === edge.end);
        if (!nextEdge) break;
        edge = nextEdge;
      }
      if (face.length === 4 && closesFace) {
        faces.push(face);
        faceAttributeIndices.push(attributeIndices);
        pairedTriangles.add(leftIndex);
        pairedTriangles.add(rightIndex);
      }
    }
  for (let triangleIndex = 0; triangleIndex < sourceTriangles.length; triangleIndex += 1) {
    if (!pairedTriangles.has(triangleIndex)) {
      faces.push(sourceTriangles[triangleIndex]);
      faceAttributeIndices.push(sourceTriangleAttributeIndices[triangleIndex]);
    }
  }
  if (faces.length === 0) {
    source.computeVertexNormals();
    return source;
  }

  for (const channel of sourceUvChannels) {
    const attribute = source.getAttribute(channel.name);
    if (!attribute) continue;
    channel.faces = faceAttributeIndices.map((indices) => (
      indices.map((attributeIndex) => new THREE.Vector2(attribute.getX(attributeIndex), attribute.getY(attributeIndex)))
    ));
  }

  const sourceFaceCount = faces.length;
  const projectedQuadFaceCount = sourceFaceCount * 4 ** iterations;
  if (projectedQuadFaceCount > MAX_SUBDIVISION_QUAD_FACES) {
    throw new SubdivisionGeometryBudgetError(sourceFaceCount, projectedQuadFaceCount);
  }
  let vertices = sourcePositions;
  let uvChannels = sourceUvChannels;
  for (let iteration = 0; iteration < iterations; iteration += 1) {
    const facePoints = faces.map((face) => {
      const point = new THREE.Vector3();
      for (const vertexIndex of face) point.add(vertices[vertexIndex]);
      return point.multiplyScalar(1 / face.length);
    });
    const vertexFaces: number[][] = Array.from({ length: vertices.length }, () => []);
    const vertexEdges: number[][] = Array.from({ length: vertices.length }, () => []);
    const faceEdges: number[][] = faces.map(() => []);
    const edges: SubdivisionEdge[] = [];
    const edgeIndices = new Map<string, number>();
    for (let faceIndex = 0; faceIndex < faces.length; faceIndex += 1) {
      const face = faces[faceIndex];
      for (const vertexIndex of face) vertexFaces[vertexIndex].push(faceIndex);
      for (let vertexIndex = 0; vertexIndex < face.length; vertexIndex += 1) {
        const a = face[vertexIndex];
        const b = face[(vertexIndex + 1) % face.length];
        const key = edgeKey(a, b);
        let edgeIndex = edgeIndices.get(key);
        if (edgeIndex === undefined) {
          edgeIndex = edges.length;
          edges.push({ a: Math.min(a, b), b: Math.max(a, b), faces: [] });
          edgeIndices.set(key, edgeIndex);
        }
        edges[edgeIndex].faces.push(faceIndex);
        vertexEdges[a].push(edgeIndex);
        vertexEdges[b].push(edgeIndex);
        faceEdges[faceIndex].push(edgeIndex);
      }
    }
    for (const edge of edges) {
      if (edge.faces.length > 2) {
        throw new SubdivisionTopologyError(`non-manifold edge ${edge.a}:${edge.b} has ${edge.faces.length} incident faces`);
      }
      if (edge.faces.length === 1) {
        throw new SubdivisionTopologyError(`open boundary edge ${edge.a}:${edge.b} has 1 incident face`);
      }
    }
    for (let vertexIndex = 0; vertexIndex < vertices.length; vertexIndex += 1) {
      const incidentFaces = vertexFaces[vertexIndex];
      if (incidentFaces.length < 2) continue;
      const connectedFaces = new Set<number>();
      const pendingFaces = [incidentFaces[0]];
      while (pendingFaces.length > 0) {
        const faceIndex = pendingFaces.pop();
        if (faceIndex === undefined || connectedFaces.has(faceIndex)) continue;
        connectedFaces.add(faceIndex);
        for (const edgeIndex of vertexEdges[vertexIndex]) {
          const edge = edges[edgeIndex];
          if (edge.faces.length !== 2 || !edge.faces.includes(faceIndex)) continue;
          const adjacentFace = edge.faces[0] === faceIndex ? edge.faces[1] : edge.faces[0];
          if (!connectedFaces.has(adjacentFace)) pendingFaces.push(adjacentFace);
        }
      }
      if (connectedFaces.size !== incidentFaces.length) {
        throw new SubdivisionTopologyError(`vertex ${vertexIndex} has a disconnected incident face fan`);
      }
    }

    const nextVertices: THREE.Vector3[] = [];
    for (let vertexIndex = 0; vertexIndex < vertices.length; vertexIndex += 1) {
      const vertex = vertices[vertexIndex];
      const incidentFaces = vertexFaces[vertexIndex];
      const incidentEdges = vertexEdges[vertexIndex];
      if (incidentFaces.length === 0 || incidentEdges.length === 0) {
        nextVertices.push(vertex.clone());
        continue;
      }
      const faceAverage = new THREE.Vector3();
      for (const faceIndex of incidentFaces) faceAverage.add(facePoints[faceIndex]);
      faceAverage.multiplyScalar(1 / incidentFaces.length);
      const edgeAverage = new THREE.Vector3();
      for (const edgeIndex of incidentEdges) {
        const edge = edges[edgeIndex];
        edgeAverage.add(vertices[edge.a].clone().add(vertices[edge.b]).multiplyScalar(0.5));
      }
      edgeAverage.multiplyScalar(1 / incidentEdges.length);
      nextVertices.push(
        vertex.clone()
          .multiplyScalar(incidentFaces.length - 3)
          .add(faceAverage)
          .addScaledVector(edgeAverage, 2)
          .multiplyScalar(1 / incidentFaces.length),
      );
    }

    const nextUvChannels = uvChannels.map((channel) => {
      const nextFaces: THREE.Vector2[][] = [];
      for (const faceValues of channel.faces) {
        const faceValue = new THREE.Vector2();
        for (const value of faceValues) faceValue.add(value);
        faceValue.multiplyScalar(1 / faceValues.length);
        for (let vertexIndex = 0; vertexIndex < faceValues.length; vertexIndex += 1) {
          const value = faceValues[vertexIndex];
          const nextValue = faceValues[(vertexIndex + 1) % faceValues.length];
          const previousValue = faceValues[(vertexIndex + faceValues.length - 1) % faceValues.length];
          nextFaces.push([
            value.clone(),
            value.clone().add(nextValue).multiplyScalar(0.5),
            faceValue.clone(),
            previousValue.clone().add(value).multiplyScalar(0.5),
          ]);
        }
      }
      return { name: channel.name, faces: nextFaces };
    });

    const edgePointIndices: number[] = [];
    for (const edge of edges) {
      const point = vertices[edge.a].clone().add(vertices[edge.b]);
      if (edge.faces.length >= 2) {
        point.add(facePoints[edge.faces[0]]).add(facePoints[edge.faces[1]]).multiplyScalar(0.25);
      } else {
        point.multiplyScalar(0.5);
      }
      edgePointIndices.push(nextVertices.length);
      nextVertices.push(point);
    }
    const facePointIndices: number[] = [];
    for (const point of facePoints) {
      facePointIndices.push(nextVertices.length);
      nextVertices.push(point);
    }
    const nextFaces: number[][] = [];
    for (let faceIndex = 0; faceIndex < faces.length; faceIndex += 1) {
      const face = faces[faceIndex];
      const faceEdgeIndices = faceEdges[faceIndex];
      for (let vertexIndex = 0; vertexIndex < face.length; vertexIndex += 1) {
        const previousEdgeIndex = (vertexIndex + faceEdgeIndices.length - 1) % faceEdgeIndices.length;
        nextFaces.push([
          face[vertexIndex],
          edgePointIndices[faceEdgeIndices[vertexIndex]],
          facePointIndices[faceIndex],
          edgePointIndices[faceEdgeIndices[previousEdgeIndex]],
        ]);
      }
    }
    vertices = nextVertices;
    uvChannels = nextUvChannels;
    faces = nextFaces;
  }

  const topologyPositions: number[] = [];
  for (const vertex of vertices) topologyPositions.push(vertex.x, vertex.y, vertex.z);
  const topologyIndices: number[] = [];
  for (const face of faces) {
    for (let vertexIndex = 1; vertexIndex + 1 < face.length; vertexIndex += 1) {
      topologyIndices.push(face[0], face[vertexIndex], face[vertexIndex + 1]);
    }
  }
  const topologyGeometry = new THREE.BufferGeometry();
  topologyGeometry.setAttribute('position', new THREE.Float32BufferAttribute(topologyPositions, 3));
  topologyGeometry.setIndex(topologyIndices);
  topologyGeometry.computeVertexNormals();

  let geometry = topologyGeometry;
  if (uvChannels.length > 0) {
    const normals = topologyGeometry.getAttribute('normal');
    const positions: number[] = [];
    const normalValues: number[] = [];
    const indices: number[] = [];
    const seamUvValues: Array<{ name: string; values: number[] }> = uvChannels.map((channel) => ({ name: channel.name, values: [] }));
    for (let faceIndex = 0; faceIndex < faces.length; faceIndex += 1) {
      const face = faces[faceIndex];
      const cornerIndices: number[] = [];
      const faceStart = positions.length / 3;
      for (let vertexIndex = 0; vertexIndex < face.length; vertexIndex += 1) {
        const topologyIndex = face[vertexIndex];
        const vertex = vertices[topologyIndex];
        positions.push(vertex.x, vertex.y, vertex.z);
        normalValues.push(normals.getX(topologyIndex), normals.getY(topologyIndex), normals.getZ(topologyIndex));
        for (let channelIndex = 0; channelIndex < uvChannels.length; channelIndex += 1) {
          const value = uvChannels[channelIndex].faces[faceIndex][vertexIndex];
          seamUvValues[channelIndex].values.push(value.x, value.y);
        }
        cornerIndices.push(faceStart + vertexIndex);
      }
      for (let vertexIndex = 1; vertexIndex + 1 < cornerIndices.length; vertexIndex += 1) {
        indices.push(cornerIndices[0], cornerIndices[vertexIndex], cornerIndices[vertexIndex + 1]);
      }
    }
    geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normalValues, 3));
    geometry.setIndex(indices);
    for (const channel of seamUvValues) {
      geometry.setAttribute(channel.name, new THREE.Float32BufferAttribute(channel.values, 2));
    }
  }
  geometry.userData.subdivision = {
    method: 'catmull-clark-style',
    sourceFaceCount,
    quadFaceCount: faces.length,
    iterations,
  };
  return geometry;
}"""


def capped_sdf(sdf: dict[str, Any], segments: dict[str, int]) -> dict[str, Any]:
    """Clamp an SDF descriptor's sampling grid to the tessellation tier's ceiling.

    Lowering the grid is the only lever an implicit surface has: it has no segment counts.
    The author's value is kept when it is already at or below the ceiling, so a deliberately
    coarse field is never made denser by the tier.
    """
    ceiling = segments.get("SDF_MAX_RESOLUTION")
    resolution = sdf.get("resolution")
    if not isinstance(ceiling, int) or not isinstance(resolution, int) or isinstance(resolution, bool):
        return sdf
    if resolution <= ceiling:
        return sdf
    return {**sdf, "resolution": ceiling}


_STAND_PROUD_HELPER_SOURCE = '''\
type ProudRingStack = { rings: [number, number, number, number][] };

// Signed distance to a stack of ellipse rings. Negative inside, positive outside.
//
// The sign is exact; the magnitude is the first-order estimate f / |grad f|, which UNDERSTATES how
// clear an outside point is and OVERSTATES how deep an inside point is. Both errors make the march
// below push slightly further than strictly necessary, which is the safe direction: the failure
// being prevented is a component sinking into the one beneath it and rendering as a bare patch.
function ringStackDistance(stack: ProudRingStack, x: number, y: number, z: number): number {
  const rings = stack.rings;
  const yMin = rings[0][0];
  const yMax = rings[rings.length - 1][0];
  let rx = rings[0][1];
  let rz = rings[0][2];
  let zc = rings[0][3];
  if (y >= yMax) {
    const last = rings[rings.length - 1];
    rx = last[1]; rz = last[2]; zc = last[3];
  } else if (y > yMin) {
    for (let i = 0; i + 1 < rings.length; i += 1) {
      const lo = rings[i];
      const hi = rings[i + 1];
      if (y >= lo[0] && y <= hi[0]) {
        const span = hi[0] - lo[0];
        const t = span > 1e-9 ? (y - lo[0]) / span : 0;
        rx = lo[1] + (hi[1] - lo[1]) * t;
        rz = lo[2] + (hi[2] - lo[2]) * t;
        zc = lo[3] + (hi[3] - lo[3]) * t;
        break;
      }
    }
  }
  const dx = x / rx;
  const dz = (z - zc) / rz;
  const f = dx * dx + dz * dz - 1;
  const gx = (2 * x) / (rx * rx);
  const gz = (2 * (z - zc)) / (rz * rz);
  const grad = Math.hypot(gx, gz);
  const radial = grad < 1e-12 ? -Math.min(rx, rz) : f / grad;
  const axial = Math.max(yMin - y, y - yMax);
  return Math.hypot(Math.max(radial, 0), Math.max(axial, 0)) + Math.min(Math.max(radial, axial), 0);
}

// Push every vertex outward until it stands `clearance` clear of the target's surface.
//
// WHY THE AUTHORED NUMBERS ARE ONLY A LOWER BOUND. A ring is an ELLIPSE, and the surface it has to
// clear generally is not. Any single ellipse that clears the widest point is loose at the narrowest
// and vice versa, so hand-widening moves the error rather than shrinking it -- measured on hair,
// where widening the side masses took closure from 42.2% to 40.9%, worse on all six views, with
// dark coverage DOWN because the widened mass had slid off the skull. Here the authored width is a
// floor and the real radius is MEASURED per vertex.
//
// Each vertex travels along its OWN radial spoke rather than along the field's gradient, so the
// ring keeps its vertex order and its seam positions and only its radius changes. `maxPush` is
// required, not a safeguard: an uncapped march walks inner vertices straight through the target and
// out the far side, closing the very gap the component exists to leave.
function applyStandProud(
  geometry: THREE.BufferGeometry,
  marcher: THREE.Object3D,
  target: THREE.Object3D,
  stack: ProudRingStack,
  clearance: number,
  maxPush: number,
): void {
  const position = geometry.getAttribute('position') as THREE.BufferAttribute;
  marcher.updateWorldMatrix(true, false);
  target.updateWorldMatrix(true, false);
  const toTarget = new THREE.Matrix4().copy(target.matrixWorld).invert().multiply(marcher.matrixWorld);
  const fromTarget = new THREE.Matrix4().copy(toTarget).invert();
  const p = new THREE.Vector3();
  // A vertex can exhaust `maxPush` and still be inside the target. That is the cap doing its job --
  // an uncapped march walks vertices out the far side -- but it means the clearance this function
  // promises was NOT achieved, and saying nothing there hides exactly the defect the caller asked
  // to be protected from. Measured on the shipped fixture: 2 of 8 sampled hair vertices sat 0.059
  // inside a skull against a 0.04 cap and could never have reached clear.
  let unresolved = 0;

  for (let i = 0; i < position.count; i += 1) {
    p.fromBufferAttribute(position, i).applyMatrix4(toTarget);
    // The spoke is the vertex's own radial direction in the target's frame; marching along it keeps
    // each ring a ring, since every vertex holds its own angle and only its radius changes.
    //
    // A vertex on the axis has no radial direction at all -- and that is precisely the crown, the
    // one place a bald patch is most visible. Skipping it leaves the exact failure this function
    // exists to prevent. So a degenerate spoke marches axially instead, out through whichever cap
    // it is nearer, which is the direction the field itself measures there.
    const spokeLength = Math.hypot(p.x, p.z);
    const onAxis = spokeLength < 1e-9;
    const midHeight = (stack.rings[0][0] + stack.rings[stack.rings.length - 1][0]) / 2;
    const sx = onAxis ? 0 : p.x / spokeLength;
    const sz = onAxis ? 0 : p.z / spokeLength;
    const sy = onAxis ? (p.y >= midHeight ? 1 : -1) : 0;

    let travelled = 0;
    for (let step = 0; step < 24; step += 1) {
      const gap = ringStackDistance(stack, p.x, p.y, p.z);
      if (gap >= clearance) break;
      const move = Math.min(Math.max(0.002, clearance - gap), maxPush - travelled);
      if (move <= 0) break;
      p.x += sx * move;
      p.y += sy * move;
      p.z += sz * move;
      travelled += move;
    }

    if (ringStackDistance(stack, p.x, p.y, p.z) < clearance) unresolved += 1;

    p.applyMatrix4(fromTarget);
    position.setXYZ(i, p.x, p.y, p.z);
  }

  position.needsUpdate = true;
  geometry.computeVertexNormals();

  geometry.userData.standProud = { clearance, maxPush, unresolved, total: position.count };
  if (unresolved > 0) {
    console.warn(
      `standProud: ${unresolved}/${position.count} vertices could not reach ${clearance} within ` +
      `maxPush ${maxPush}. They are still inside the target and will render as bare patches. ` +
      `Raise maxPush, or move the component out so it does not start that deep.`,
    );
  }
}
'''


_ROOT_TIP_GRADIENT_HELPER_SOURCE = '''\
// Darken a mass toward its root.
//
// WHY THIS IS SHADING AND NOT GEOMETRY. Measured on the reference this pipeline is calibrated
// against -- a 570,400 vertex merged scan -- the surface roughness over the hair is 0.00338 against
// a torso control of 0.00312. Its hair is a SMOOTH SHELL and every strand it appears to have lives
// in the diffuse and normal textures. This pipeline emits code and no textures, so the same
// impression has to be carried by vertex colour, faceting and sheen. Adding lock geometry does not
// substitute for it: four attempts to close the gap geometrically all failed, and the last one made
// every view worse.
//
// The gradient runs along the mass's own local axis rather than world Y, so a fringe sweeping
// sideways darkens at its root and not at whatever happens to be lowest.
function applyRootTipGradient(
  geometry: THREE.BufferGeometry,
  rootColor: THREE.ColorRepresentation,
  tipColor: THREE.ColorRepresentation,
  axis: 'x' | 'y' | 'z',
): void {
  const position = geometry.getAttribute('position') as THREE.BufferAttribute;
  const root = new THREE.Color(rootColor);
  const tip = new THREE.Color(tipColor);
  const values = new Float32Array(position.count * 3);

  let low = Infinity;
  let high = -Infinity;
  for (let i = 0; i < position.count; i += 1) {
    const v = axis === 'x' ? position.getX(i) : axis === 'z' ? position.getZ(i) : position.getY(i);
    if (v < low) low = v;
    if (v > high) high = v;
  }
  // A mass with no extent along its own axis has no root and no tip; a flat colour is the honest
  // answer, and dividing by the span would produce NaN on every vertex.
  const span = high - low;

  const mixed = new THREE.Color();
  for (let i = 0; i < position.count; i += 1) {
    const v = axis === 'x' ? position.getX(i) : axis === 'z' ? position.getZ(i) : position.getY(i);
    const t = span > 1e-9 ? (v - low) / span : 1;
    mixed.copy(root).lerp(tip, t);
    values[i * 3] = mixed.r;
    values[i * 3 + 1] = mixed.g;
    values[i * 3 + 2] = mixed.b;
  }
  geometry.setAttribute('color', new THREE.BufferAttribute(values, 3));
}
'''


_VERTEX_PAINT_HELPER_SOURCE = '''\
// Paint declared colour regions into the vertex-colour attribute.
//
// WHY VERTEX COLOUR AND NOT A TEXTURE. A subject whose identity is a set of flat colour regions
// with hard boundaries -- a blaze, a bib, a sock, a livery stripe -- needs those boundaries placed
// to a measured position. This pipeline emits code and no image assets, so a texture is not
// available to place them with; a single root-to-tip ramp cannot express a shaped region. Per-
// vertex colour driven by a declared shape is the remaining honest representation, and it is the
// one the boundary gate can measure BEFORE a browser is involved.
//
// The maths here is a transcription of forge/_shared/vertex_paint.py, and
// forge/tests/test_vertex_paint.py holds the two to the same numbers on a fixture. Editing one
// side without the other turns a gated boundary into an ungated one, which is exactly the failure
// the shared implementation exists to prevent.
//
// Regions are evaluated in the component's own local space AFTER its real dimensions have been
// applied to the vertex data, so every coordinate below is in the same units as the component's
// measured dimensions rather than in a unit cube.
type VertexPaintRegion = {
  id: string;
  kind: 'axis-band' | 'ellipsoid' | 'tapered-capsule';
  color: string;
  softness: number;
  axis?: 'x' | 'y' | 'z';
  min?: number;
  max?: number;
  center?: [number, number, number];
  radii?: [number, number, number];
  start?: [number, number, number];
  end?: [number, number, number];
  startRadius?: number;
  endRadius?: number;
};

function vertexPaintSmoothstep(edge0: number, edge1: number, value: number): number {
  if (edge1 <= edge0) return value < edge1 ? 0 : 1;
  let t = (value - edge0) / (edge1 - edge0);
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  return t * t * (3 - 2 * t);
}

function vertexPaintSignedDistance(
  region: VertexPaintRegion,
  x: number,
  y: number,
  z: number,
): number {
  if (region.kind === 'axis-band') {
    const value = region.axis === 'x' ? x : region.axis === 'z' ? z : y;
    const low = region.min as number;
    const high = region.max as number;
    if (value < low) return low - value;
    if (value > high) return value - high;
    return -Math.min(value - low, high - value);
  }
  if (region.kind === 'ellipsoid') {
    const [cx, cy, cz] = region.center as [number, number, number];
    const [rx, ry, rz] = region.radii as [number, number, number];
    const q = Math.sqrt(
      ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 + ((z - cz) / rz) ** 2,
    );
    return (q - 1) * Math.min(rx, ry, rz);
  }
  const [ax, ay, az] = region.start as [number, number, number];
  const [bx, by, bz] = region.end as [number, number, number];
  const abx = bx - ax;
  const aby = by - ay;
  const abz = bz - az;
  const denominator = abx * abx + aby * aby + abz * abz;
  let t = denominator > 0
    ? ((x - ax) * abx + (y - ay) * aby + (z - az) * abz) / denominator
    : 0;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  const distance = Math.sqrt(
    (x - (ax + abx * t)) ** 2 + (y - (ay + aby * t)) ** 2 + (z - (az + abz * t)) ** 2,
  );
  const startRadius = region.startRadius as number;
  const endRadius = region.endRadius as number;
  return distance - (startRadius + (endRadius - startRadius) * t);
}

function vertexPaintWeight(region: VertexPaintRegion, x: number, y: number, z: number): number {
  const distance = vertexPaintSignedDistance(region, x, y, z);
  if (region.softness <= 0) return distance <= 0 ? 1 : 0;
  return 1 - vertexPaintSmoothstep(-region.softness * 0.5, region.softness * 0.5, distance);
}

function applyVertexPaint(
  geometry: THREE.BufferGeometry,
  baseColor: string,
  regions: VertexPaintRegion[],
): void {
  const position = geometry.getAttribute('position') as THREE.BufferAttribute;
  const values = new Float32Array(position.count * 3);
  const base = new THREE.Color(baseColor);
  const target = new THREE.Color();
  const mixed = new THREE.Color();
  const regionColors = regions.map((region) => new THREE.Color(region.color));

  for (let i = 0; i < position.count; i += 1) {
    const x = position.getX(i);
    const y = position.getY(i);
    const z = position.getZ(i);
    mixed.copy(base);
    for (let r = 0; r < regions.length; r += 1) {
      const weight = vertexPaintWeight(regions[r], x, y, z);
      if (weight <= 0) continue;
      target.copy(regionColors[r]);
      mixed.lerp(target, weight);
    }
    values[i * 3] = mixed.r;
    values[i * 3 + 1] = mixed.g;
    values[i * 3 + 2] = mixed.b;
  }
  geometry.setAttribute('color', new THREE.BufferAttribute(values, 3));
}
'''


def vertex_paint(component: dict[str, Any]) -> dict[str, Any] | None:
    """A component's declared colour-region paint, or None.

    Raises rather than degrading to flat colour when the declaration is malformed. A boundary that
    silently vanishes is the failure mode this whole subsystem exists to remove: the render still
    looks like a cat, the gate still finds a mesh, and the identity feature is simply not there.
    """
    paint = component.get("vertexPaint")
    if paint is None:
        return None
    try:
        return normalize_vertex_paint(paint, f"component {component.get('id')!r} vertexPaint")
    except VertexPaintError as error:
        raise ValueError(str(error)) from error


def root_tip_gradient(component: dict[str, Any]) -> dict[str, Any] | None:
    """A component's root-to-tip colour ramp, or None.

    Rejected rather than defaulted when malformed: a gradient silently falling back to flat colour
    is the kind of quiet no-op that made hair hard to diagnose in the first place.
    """
    gradient = component.get("rootTipGradient")
    if not isinstance(gradient, dict):
        return None
    root = gradient.get("rootColor")
    tip = gradient.get("tipColor")
    if not isinstance(root, str) or not isinstance(tip, str):
        return None
    axis = gradient.get("axis", "y")
    if axis not in ("x", "y", "z"):
        axis = "y"
    return {"rootColor": root, "tipColor": tip, "axis": axis}


def stand_proud_ring_stack(component: dict[str, Any]) -> list[list[float]] | None:
    """The ring stack a component presents as a surface to stand proud of, in its own local space.

    Two sources, because two shapes actually occur. A component authored as a ring stack carries one
    directly. Everything else that is a closed convex blob -- an ellipsoid or a sphere, which is what
    a generated head is -- gets one synthesised from its unit geometry, since `geometry_for` authors
    those at radius 0.5 and the real dimensions arrive later via `geometry.scale`. Returning None
    means the target has no surface this march can describe, and the caller must say so rather than
    silently skip the clearance.
    """
    descriptor = component.get("geometryDescriptor")
    if isinstance(descriptor, dict):
        stack = descriptor.get("ringStack")
        if isinstance(stack, dict) and isinstance(stack.get("rings"), list) and stack["rings"]:
            offsets = stack.get("zOffsets")
            rings: list[list[float]] = []
            for index, ring in enumerate(stack["rings"]):
                values = [float(v) for v in list(ring)[:4]]
                if len(values) < 3:
                    return None
                if len(values) == 3:
                    offset = 0.0
                    if isinstance(offsets, list) and index < len(offsets):
                        offset = float(offsets[index])
                    values.append(offset)
                rings.append(values)
            rings.sort(key=lambda r: r[0])
            return rings

    if component.get("primitive") in {"ellipsoid", "sphere", "capsule"}:
        # A unit sphere of radius 0.5, sliced into rings. The march runs in the TARGET's local
        # space, and `geometry.scale` has already been applied to the target's own vertices, so the
        # stack has to describe the unit form and let the caller's matrix carry the scale.
        slices = 12
        rings = []
        for index in range(slices + 1):
            t = index / slices
            y = -0.5 + t
            radius = math.sqrt(max(0.0, 0.25 - y * y))
            rings.append([round(y, 6), round(max(radius, 1e-4), 6), round(max(radius, 1e-4), 6), 0.0])
        return rings

    return None


def geometry_for(
    primitive: str,
    component: dict[str, Any] | None = None,
    subdivision_requested: bool = False,
    segments: dict[str, int] | None = None,
) -> str:
    """Emit the geometry construction for one primitive.

    `segments` is the resolved tessellation table (see `_shared/subdivision.py`).
    It defaults to the `hero` tier, which is the module-level constants, so a
    caller that does not pass one gets exactly the output it got before tiers
    existed.
    """
    seg = segments if segments is not None else TESSELLATION_TIERS[DEFAULT_TESSELLATION_TIER]
    if primitive == "box":
        n = seg["BOX_SEGMENTS"]
        return f"new THREE.BoxGeometry(1, 1, 1, {n}, {n}, {n})"
    if primitive in {"sphere", "ellipsoid"}:
        return f"new THREE.SphereGeometry(0.5, {seg['SPHERE_WIDTH_SEGMENTS']}, {seg['SPHERE_HEIGHT_SEGMENTS']})"
    if primitive == "cylinder":
        return f"new THREE.CylinderGeometry(0.5, 0.5, 1, {seg['CYLINDER_RADIAL_SEGMENTS']}, {seg['CYLINDER_HEIGHT_SEGMENTS']})"
    if primitive == "cone":
        if subdivision_requested:
            return f"new THREE.CylinderGeometry(0.5, {CONE_SUBDIVISION_TOP_RADIUS}, 1, {seg['CYLINDER_RADIAL_SEGMENTS']}, {seg['CYLINDER_HEIGHT_SEGMENTS']})"
        return f"new THREE.ConeGeometry(0.5, 1, {seg['CYLINDER_RADIAL_SEGMENTS']}, {seg['CONE_HEIGHT_SEGMENTS']})"
    if primitive == "capsule":
        return f"buildWatertightCapsule(0.35, 0.7, {seg['CAPSULE_CAP_SEGMENTS']}, {seg['CAPSULE_RADIAL_SEGMENTS']}, 1)"
    if primitive == "torus":
        # Tube thickness is ring-relative (fraction of the 0.45 base radius) so a
        # component can be a slim bike tyre or a fat donut without changing scale.
        desc = component.get("geometryDescriptor") if isinstance(component, dict) and isinstance(component.get("geometryDescriptor"), dict) else {}
        tube_ratio = desc.get("torusTubeRatio")
        tube = 0.45 * float(tube_ratio) if isinstance(tube_ratio, (int, float)) and tube_ratio > 0 else 0.08
        return f"new THREE.TorusGeometry(0.45, {round(tube, 4)}, {seg['TORUS_TUBULAR_SEGMENTS']}, {seg['TORUS_RADIAL_SEGMENTS']})"
    if primitive == "plane-card":
        return f"new THREE.PlaneGeometry(1, 1, {seg['PLANE_WIDTH_SEGMENTS']}, {seg['PLANE_HEIGHT_SEGMENTS']})"
    descriptor = component.get("geometryDescriptor") if isinstance(component, dict) and isinstance(component.get("geometryDescriptor"), dict) else {}
    if primitive == "extrude":
        profile = descriptor.get("profile2D") if isinstance(descriptor.get("profile2D"), dict) else _DEFAULT_EXTRUDE_PROFILE
        return f"buildExtrudeGeometry({json_literal(profile)})"
    if primitive == "ground-blade":
        spec = descriptor.get("bladeSpec") if isinstance(descriptor.get("bladeSpec"), dict) else _DEFAULT_BLADE_SPEC
        return f"buildGroundBladeGeometry({json_literal(spec)})"
    if primitive == "lathe":
        profile = descriptor.get("latheProfile") if isinstance(descriptor.get("latheProfile"), dict) else _DEFAULT_LATHE_PROFILE
        return f"buildLatheGeometry({json_literal(profile)})"
    if primitive == "tube":
        path = descriptor.get("tubePath") if isinstance(descriptor.get("tubePath"), dict) else _DEFAULT_TUBE_PATH
        return f"buildTubeGeometry({json_literal(path)})"
    if primitive == "curve-sweep":
        sweep = descriptor.get("curveSweep") if isinstance(descriptor.get("curveSweep"), dict) else _DEFAULT_CURVE_SWEEP
        return f"buildCurveSweepGeometry({json_literal(sweep)})"
    if primitive == "tapered-sweep":
        tapered = (
            descriptor.get("taperedSweep")
            if isinstance(descriptor.get("taperedSweep"), dict)
            else _DEFAULT_TAPERED_SWEEP
        )
        return f"buildTaperedSweepGeometry({json_literal(tapered)})"
    if primitive == "instanced-cluster":
        # An instanced cluster's *geometry* is its base shape; the instancing itself is applied
        # by the repetition-system emitter (THREE.InstancedMesh). Resolve the base primitive from
        # the descriptor (default box); guard against self-reference so we never recurse.
        base = resolve_instanced_cluster_base(primitive, descriptor, VALID_PRIMITIVES)
        return geometry_for(base, component, subdivision_requested, seg)
    raise GeometryNotImplementedError(primitive)


def decimate_ratio(component: dict[str, Any]) -> float | None:
    """Read `geometryDescriptor.decimate.targetRatio`, or None when decimation is not asked for.

    Opt-in per component rather than derived from the tessellation tier, because the two
    solve different halves of the same problem: a tier dials the density of primitives that
    HAVE segment counts, and decimation is the only lever for geometry that does not -- an
    implicit surface's density comes from its sampling grid, which is quantised, so the tier
    can only get near a budget while a ratio can land on it. Running it on every mesh instead
    would put a quadric collapse on the page-load path for shapes whose density is already
    exactly what was asked for.
    """
    descriptor = component.get("geometryDescriptor")
    if not isinstance(descriptor, dict):
        return None
    decimate = descriptor.get("decimate")
    if not isinstance(decimate, dict):
        return None
    ratio = decimate.get("targetRatio")
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
        return None
    if not 0.0 < float(ratio) < 1.0:
        return None
    return float(ratio)


def subdivision_iterations(component: dict[str, Any]) -> int:
    descriptor = component.get("geometryDescriptor")
    if not isinstance(descriptor, dict):
        return 0
    subdivide = descriptor.get("subdivide")
    if not isinstance(subdivide, dict):
        return 0
    iterations = subdivide.get("iterations")
    if (
        not isinstance(iterations, int)
        or isinstance(iterations, bool)
        or iterations < 1
        or iterations > MAX_SUBDIVISION_ITERATIONS
    ):
        return 0
    return iterations


def component_uses_dense_height_maps(component: dict[str, Any]) -> bool:
    return (
        component.get("denseMesh") is True
        or component.get("geometryDensity") == "dense"
        or component.get("topologyClass") in {"dense", "implicit"}
        or subdivision_iterations(component) > 0
    )


BONE_TRACK_DOMAINS = {"character", "hybrid"}


def rig_is_bone_track(spec: dict[str, Any]) -> bool:
    """PLAN_1.5 §8: route on the EXISTING `objectClass.primaryDomain`, no new routing field.

    `object` stays on the pivot track, which §8 says is "not replaced" and stays the default.
    So this returns False for it and `emit_rig_hierarchy()` emits nothing whatsoever -- an
    object spec's generated source must remain byte-identical to its pre-plan output.
    """
    object_class = (spec.get("preSpecAssessment") or {}).get("objectClass") or {}
    if object_class.get("primaryDomain") not in BONE_TRACK_DOMAINS:
        return False
    rig = spec.get("rig")
    return isinstance(rig, dict) and bool(rig.get("bones"))


def emit_rig_hierarchy(spec: dict[str, Any]) -> list[str]:
    """Emit the bone hierarchy from `spec["rig"]` — PLAN_1.5 WS-C.

    Emits the bones, one shared `THREE.Skeleton`, exactly one vertex-weight helper, and binds every
    skinned component as a `THREE.SkinnedMesh`. `root.userData.rig.bound` is COMPUTED — it is true
    only when every skinned mesh actually bound — rather than asserted, so a partial bind reports
    itself instead of reading as success.

    On the pivot track this emits nothing at all, so an object spec's output is byte-identical to
    what it was before the rig track existed.

    Two things that are easy to get wrong here:

    - `jointPos` in the spec is **model space** (see `derive_character_rig`'s docstring). A
      `THREE.Bone`'s position is parent-local, so each bone's offset is its own joint minus its
      parent's joint; the root keeps its model-space joint unchanged.
    - Bones are emitted **parents first**. The spec's bone list is sorted by `(has-parent, id)`,
      so `foot-l` precedes `shin-l` alphabetically — emitting in list order would parent a bone
      to one that does not exist yet. This walks the tree rather than trusting list order.
    """
    if not rig_is_bone_track(spec):
        return []

    bones = [b for b in spec["rig"]["bones"] if isinstance(b, dict) and b.get("id")]
    by_id = {b["id"]: b for b in bones}

    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(bone: dict[str, Any]) -> None:
        parent = bone.get("parent")
        if parent and parent in by_id and parent not in seen:
            visit(by_id[parent])
        if bone["id"] not in seen:
            seen.add(bone["id"])
            ordered.append(bone)

    for bone in bones:
        visit(bone)

    def bone_var(bone_id: str) -> str:
        return f"bone_{re.sub(r'[^A-Za-z0-9]', '_', bone_id)}"

    lines = [
        "",
        "  // PLAN_1.5 WS-C slice 1: bone hierarchy from spec.rig. Model-space joints are",
        "  // converted to parent-local offsets here. Nothing is bound yet (rig.bound === false).",
        "  const bones: Record<string, THREE.Bone> = {};",
        "  const boneOrder: string[] = [];",
    ]
    for bone in ordered:
        bone_id = bone["id"]
        joint = bone.get("jointPos") or [0.0, 0.0, 0.0]
        parent = bone.get("parent")
        var = bone_var(bone_id)
        if parent and parent in by_id:
            parent_joint = by_id[parent].get("jointPos") or [0.0, 0.0, 0.0]
            offset = [float(joint[i]) - float(parent_joint[i]) for i in range(3)]
        else:
            offset = [float(joint[i]) for i in range(3)]
        lines.extend([
            f"  const {var} = new THREE.Bone();",
            f"  {var}.name = {json.dumps(bone_id)};",
            f"  {var}.position.set({vector(offset, [0.0, 0.0, 0.0])});",
            (f"  {bone_var(parent)}.add({var});" if parent and parent in by_id
             else f"  root.add({var});"),
            f"  bones[{json.dumps(bone_id)}] = {var};",
            f"  boneOrder.push({json.dumps(bone_id)});",
        ])
    lines.extend([
        "  // The bones are now in REST position. updateMatrixWorld() before constructing the",
        "  // Skeleton is load-bearing: calculateInverses() reads each bone's CURRENT world matrix,",
        "  // and those inverses are what cancel the rest pose during skinning. Constructed before",
        "  // this call it captures identity matrices, the rest pose never cancels, and every",
        "  // vertex is displaced by its bone's offset at rest. Measured, not assumed --",
        "  // scratchpad/bind_experiment.mjs read (0, 3, 0) for a vertex authored at (0, 2, 0).",
        "  root.updateMatrixWorld(true);",
        "  const skeleton = new THREE.Skeleton(boneOrder.map((id) => bones[id]));",
        "  const boneIndexOf = new Map<string, number>(boneOrder.map((id, i) => [id, i]));",
    ])
    lines.extend(_rig_weight_function_lines(spec, ordered))
    lines.extend(_rig_pose_lines(spec, ordered, bone_var))
    lines.append("  root.userData.rig = { bones, skeleton, boneOrder, boneIndexOf, "
                 "skinAttributes: skinnedMeshNames, bound: skinnedMeshNames.length > 0 "
                 "&& boundCount === skinnedMeshNames.length, "
                 "frustumCulled: false, "
                 "cullingNote: 'skinned meshes set frustumCulled = false; bone motion does not "
                 "update a SkinnedMesh boundingSphere, so a consumer that needs culling must "
                 "recompute bounds per frame' };")
    return lines


def _rig_pose_lines(
    spec: dict[str, Any],
    ordered: list[dict[str, Any]],
    bone_var: Any,
) -> list[str]:
    """Move the authored pose from the component pivots onto the BONES.

    `apply_character_pose` writes `component.transform.rotation`, which drives the component's
    pivot `Group`. Once a body segment is a `SkinnedMesh` its geometry has been baked to model
    space and reparented to `root`, so the pivot no longer reaches it — the pose has to be
    re-expressed as bone rotations or it disappears from the render.

    That substitution is exact rather than approximate because a bone's origin IS its
    component's pivot origin: `derive_character_rig` takes each `jointPos` from the component's
    proximal end, which is what the spine split in `make_character_component_tree` exists to
    guarantee, and the bone parent chain mirrors the component parent chain. Same origin, same
    parent frame, same euler order — so the same euler triple produces the same rotation.

    **Applied after `bind()`, never before.** These rotations are the pose, not the bind pose;
    setting them before the `Skeleton` is constructed would bake the pose in as rest and nothing
    would deform.
    """
    posed = _posed_bone_rotations(spec, ordered)
    if not posed:
        return []
    lines = [
        "",
        "  // The authored pose, moved from the pivots onto the bones (see _rig_pose_lines). Set",
        "  // AFTER bind() so that the rest pose -- not this one -- is what the skeleton's inverse",
        "  // bind matrices cancel.",
    ]
    for bone_id, _component_id, values in posed:
        lines.append(f"  {bone_var(bone_id)}.rotation.set({vector(values, [0.0, 0.0, 0.0])});")
    lines.extend([
        "  root.updateMatrixWorld(true);",
        "  skeleton.update();",
    ])
    return lines


def _posed_bone_rotations(
    spec: dict[str, Any],
    ordered: list[dict[str, Any]],
) -> list[tuple[str, str, list[float]]]:
    """Every bone whose component carries an authored pose, as `(bone id, component id, euler)`.

    One source for the three places that need it: resting the pivots for the bake, restoring them
    afterwards, and rotating the bones. Deriving it three times is how the pose ends up applied
    twice in one of them.
    """
    by_component = {c.get("id"): c for c in spec.get("componentTree", []) if isinstance(c, dict)}
    posed: list[tuple[str, str, list[float]]] = []
    for bone in ordered:
        component_id = str(bone.get("component") or bone["id"])
        rotation = ((by_component.get(component_id) or {}).get("transform") or {}).get("rotation")
        values = [float(v) for v in (rotation or [0.0, 0.0, 0.0])[:3]]
        if any(abs(v) > 1e-9 for v in values):
            posed.append((bone["id"], component_id, values))
    return posed


def _rig_weight_function_lines(spec: dict[str, Any], ordered: list[dict[str, Any]]) -> list[str]:
    """Emit the PLAN_1.5 §4 weight function ONCE, plus the per-mesh skin attributes it feeds.

    Ported from `forge/stage5_rig/emit_rig.py`, which measured `max |Σw − 1| = 2.98e-8` on real
    executed geometry. Ported rather than rewritten on purpose: a second implementation of a
    weight function is exactly what WS-C's "a grep must find no second weight function"
    acceptance criterion exists to prevent.

    Two things are deliberate:

    - **The envelope radius is computed in Python**, by `derive_envelope_radius` (PLAN_1.5 §4.3),
      and emitted as a literal. Doing it here reuses the one implementation instead of
      transcribing the formula into TypeScript, where it could drift.
    - **The helper is emitted once and IS called.** Both the showcase tsconfig and the test
      harness compile with `--noUnusedLocals`, so an emitted-but-uncalled helper is a hard tsc
      error. WS-C.3 consumes these attributes: each skinned component becomes a
      `THREE.SkinnedMesh` and binds against the one shared `Skeleton`.
    """
    # stage5_rig was deliberately standalone "until WS-C integration" per its own docstring.
    # This is that integration, so importing its derivation is the intended coupling rather than
    # transcribing the §4.3 formula into a second place where it could drift.
    from stage5_rig.rig_spec import derive_envelope_radius  # noqa: PLC0415

    by_component = {c.get("id"): c for c in spec.get("componentTree", []) if isinstance(c, dict)}
    envelope: dict[str, float] = {}
    for bone in ordered:
        component = by_component.get(bone.get("component") or bone["id"]) or {}
        scale = (component.get("transform") or {}).get("scale") or [0.0, 0.0, 0.0]
        width, depth = float(scale[0]), float(scale[2])
        if width <= 0 or depth <= 0:
            width = depth = 0.05
        envelope[bone["id"]] = round(derive_envelope_radius(width, depth), 6)

    joints = {b["id"]: [float(v) for v in b["jointPos"]] for b in ordered}
    tips = {b["id"]: [float(v) for v in b["tipPos"]] for b in ordered}

    # The bake below reads `mesh.matrixWorld`, and by this point the pivots already carry the
    # authored pose -- so baking as-is would freeze the POSE into the geometry while the bones sit
    # at rest, and the two would disagree by exactly the pose. The pivots are therefore returned to
    # rest for the duration of the bake and restored afterwards, so that what lands in the vertex
    # data is the rest pose the skeleton's inverse bind matrices were computed from.
    posed = _posed_bone_rotations(spec, ordered)
    if posed:
        rest_pose_comment = (
            "  // Pivots back to REST for the bake: the geometry that lands in the buffer must be\n"
            "  // the same rest pose the skeleton's inverse bind matrices cancel, not the pose."
        )
        rest_pose_lines = [
            f"  nodes[{json.dumps(component_id)}]?.rotation.set(0, 0, 0);"
            for _bone_id, component_id, _values in posed
        ]
        restore_pose_lines = [
            "",
            "  // Pose restored on the pivots. The skinned meshes no longer hang off them -- they",
            "  // were reparented to `root` -- so this drives only the non-skinned descendants",
            "  // (ear shells, eye cavities), which have no bone of their own and would otherwise",
            "  // stay at rest while the head they sit on turns. The bones get the same rotations",
            "  // applied separately, so nothing is posed twice.",
            *[
                f"  nodes[{json.dumps(component_id)}]?.rotation.set("
                f"{vector(values, [0.0, 0.0, 0.0])});"
                for _bone_id, component_id, values in posed
            ],
            "  root.updateMatrixWorld(true);",
        ]
    else:
        rest_pose_comment = "  // No component carries an authored pose, so there is nothing to rest."
        rest_pose_lines = []
        restore_pose_lines = []

    return [
        "",
        "  // ---- PLAN_1.5 §4 weight function: ONE function over the complete bone set. No",
        "  // mesh-id or vertex-index branching -- only positions, segment endpoints and the",
        "  // envelope radius derived per §4.3. Ported from forge/stage5_rig/emit_rig.py, which",
        "  // measured max |sum(w) - 1| = 2.98e-8 on executed geometry.",
        f"  const BONE_JOINT: Record<string, number[]> = {json.dumps(joints)};",
        f"  const BONE_TIP: Record<string, number[]> = {json.dumps(tips)};",
        f"  const BONE_ENVELOPE: Record<string, number> = {json.dumps(envelope)};",
        "  const _closest = new THREE.Vector3();",
        "  const distanceToSegment = (p: THREE.Vector3, s: number[], e: number[]): number => {",
        "    const ab = [e[0] - s[0], e[1] - s[1], e[2] - s[2]];",
        "    const ap = [p.x - s[0], p.y - s[1], p.z - s[2]];",
        "    const abLenSq = ab[0] * ab[0] + ab[1] * ab[1] + ab[2] * ab[2];",
        "    const t = abLenSq > 1e-12",
        "      ? THREE.MathUtils.clamp((ap[0] * ab[0] + ap[1] * ab[1] + ap[2] * ab[2]) / abLenSq, 0, 1)",
        "      : 0;",
        "    _closest.set(s[0] + ab[0] * t, s[1] + ab[1] * t, s[2] + ab[2] * t);",
        "    return p.distanceTo(_closest);",
        "  };",
        "  const computeVertexWeights = (p: THREE.Vector3) => {",
        "    const scored = boneOrder.map((id) => {",
        "      const d = distanceToSegment(p, BONE_JOINT[id], BONE_TIP[id]);",
        "      const u = d / BONE_ENVELOPE[id];",
        "      const falloff = Math.max(0, 1 - u * u);",
        "      return { id, d, w: falloff * falloff };",
        "    });",
        "    scored.sort((a, b) => b.w - a.w);",
        "    const kept = scored.slice(0, 4);",
        "    const total = kept.reduce((sum, c) => sum + c.w, 0);",
        "    const indices = [0, 0, 0, 0];",
        "    const weights = [0, 0, 0, 0];",
        "    if (total > 0) {",
        "      for (let slot = 0; slot < kept.length; slot++) {",
        "        indices[slot] = boneIndexOf.get(kept[slot].id) ?? 0;",
        "        weights[slot] = kept[slot].w / total;",
        "      }",
        "      return { indices, weights, fallback: false };",
        "    }",
        "    // Mandatory zero-sum fallback (PLAN_1.5 §4 / ADR-8). Without it three.js's own",
        "    // normalizeSkinWeights() rewrites an all-zero vertex to (1,0,0,0) against bone 0",
        "    // regardless of distance, which spikes stray vertices toward the hips. Instead:",
        "    // ignore the envelope and pin weight 1.0 to the absolutely nearest bone.",
        "    let nearest = boneOrder[0];",
        "    let nearestDistance = Infinity;",
        "    for (const id of boneOrder) {",
        "      const d = distanceToSegment(p, BONE_JOINT[id], BONE_TIP[id]);",
        "      if (d < nearestDistance) { nearestDistance = d; nearest = id; }",
        "    }",
        "    indices[0] = boneIndexOf.get(nearest) ?? 0;",
        "    weights[0] = 1;",
        "    return { indices, weights, fallback: true };",
        "  };",
        "",
        "  // ---- Bake to model space, weight, and bind.",
        "  //",
        "  // The arrangement below was chosen by measurement, not derivation, because the same",
        "  // geometry can be skinned four plausible ways and three of them are wrong. With a",
        "  // vertex authored at model-space (0, 2, 0) fully weighted to a bone at (0, 1, 0) and",
        "  // that bone rotated +90 degrees about X (correct answer: (0, 1, 1)):",
        "  //",
        "  //   pivot transform kept, bind identity     -> rest pose already wrong, no deformation",
        "  //   pivot transform kept, bind matrixWorld  -> (0, 1.5, 0.5): HALF the correct swing,",
        "  //                                              because the pivot applies on top of skinning",
        "  //   geometry baked, pivot bypassed          -> (0, 1, 1): correct",
        "  //   no pivot at all                         -> (0, 1, 1): correct, and identical",
        "  //",
        "  // The last two agreeing is the finding: what matters is that the mesh's own world",
        "  // transform is identity and its geometry lives in the skeleton's space. So each skinned",
        "  // mesh gets its world matrix folded into its vertex data and is reparented to `root`",
        "  // with an identity transform. Meshes are leaves -- components are added to their pivot",
        "  // Group, never to another mesh -- so reparenting one moves nothing else.",
        rest_pose_comment,
        *rest_pose_lines,
        "  root.updateMatrixWorld(true);",
        "  const skinnedMeshNames: string[] = [];",
        "  let boundCount = 0;",
        "  for (const boneId of boneOrder) {",
        "    const mesh = meshes[boneId];",
        "    if (!mesh) continue;",
        "    const position = mesh.geometry.getAttribute('position');",
        "    if (!position) continue;",
        "    mesh.updateWorldMatrix(true, false);",
        "    // applyMatrix4 mutates the vertex buffer in place and is NOT idempotent: running it",
        "    // twice on one geometry applies the world matrix squared, and every component lands",
        "    // somewhere it has no reason to be -- the model reads as blown apart rather than",
        "    // wrong. Throw rather than skip, because a silent skip would leave a mesh in the",
        "    // wrong space and the failure would resurface later as a subtler misplacement.",
        "    if (mesh.geometry.userData.worldBaked) {",
        "      throw new Error(",
        "        `geometry for '${boneId}' is already world-baked; baking twice squares the ` +",
        "        'world matrix and scatters the parts. Build a fresh factory instead of re-binding.'",
        "      );",
        "    }",
        "    mesh.geometry.applyMatrix4(mesh.matrixWorld);",
        "    mesh.geometry.userData.worldBaked = true;",
        "    root.add(mesh);",
        "    mesh.position.set(0, 0, 0);",
        "    mesh.quaternion.identity();",
        "    mesh.scale.set(1, 1, 1);",
        "    mesh.updateMatrixWorld(true);",
        "    // Vertices are model-space now, which is the space the weight function measures in,",
        "    // so no per-vertex matrix multiply is needed any more.",
        "    const count = position.count;",
        "    const skinIndices = new Uint16Array(count * 4);",
        "    const skinWeights = new Float32Array(count * 4);",
        "    const vertex = new THREE.Vector3();",
        "    for (let v = 0; v < count; v++) {",
        "      vertex.fromBufferAttribute(position, v);",
        "      const { indices, weights } = computeVertexWeights(vertex);",
        "      for (let slot = 0; slot < 4; slot++) {",
        "        skinIndices[v * 4 + slot] = indices[slot];",
        "        skinWeights[v * 4 + slot] = weights[slot];",
        "      }",
        "    }",
        "    mesh.geometry.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(skinIndices, 4));",
        "    mesh.geometry.setAttribute('skinWeight', new THREE.Float32BufferAttribute(skinWeights, 4));",
        "    skinnedMeshNames.push(boneId);",
        "    const skinned = mesh as THREE.SkinnedMesh;",
        "    if (!skinned.isSkinnedMesh) continue;",
        "    // bindMode is left at its default (AttachedBindMode). The bones live under `root`",
        "    // rather than under any one mesh because a single Skeleton is shared by every skinned",
        "    // mesh and cannot be parented under all of them; with root and each mesh at identity",
        "    // the bone world matrices are the same either way.",
        "    skinned.bind(skeleton, new THREE.Matrix4());",
        "    // A SkinnedMesh's boundingSphere is computed from its REST vertex data and is not",
        "    // recomputed when bones move, so a posed limb that swings outside its rest bounds gets",
        "    // culled and vanishes -- worse, it vanishes only from certain camera angles, which",
        "    // reads as a geometry bug rather than a culling one. Disabling the test outright is",
        "    // chosen over recomputing bounds every frame because these are small, always-onscreen",
        "    // character parts where the test saves nothing. Recorded in userData.rig so a consumer",
        "    // that DOES need culling knows it has to supply its own bounds.",
        "    skinned.frustumCulled = false;",
        "    boundCount += 1;",
        "  }",
        *restore_pose_lines,
    ]


def generate(spec: dict[str, Any], pass_id: str) -> str:
    errors, _warnings = validate_spec(spec)
    if errors:
        raise ValueError(f"spec validation failed: {'; '.join(errors)}")
    target = str(spec.get("targetName") or "Procedural Object")
    type_name = pascal_case(target)
    function_name = f"create{type_name}Model"
    # Resolved once per generate() so every primitive in one model agrees on density.
    seg = segments_for_spec(spec)
    reconstruction_evidence = {
        "itemFamily": spec.get("itemFamily"),
        "subtype": spec.get("subtype"),
        "componentAdapter": spec.get("componentAdapter"),
        "route": spec.get("route"),
        "exactnessTier": spec.get("exactnessTier"),
        "referenceCamera": spec.get("referenceCamera"),
        "approximationNotes": spec.get("approximationNotes", []),
    }
    materials = {
        str(material.get("id") or f"material{index}"): material
        for index, material in enumerate(spec.get("materials", []))
        if isinstance(material, dict)
    }
    all_components = [item for item in spec.get("componentTree", []) if isinstance(item, dict)]
    components = filter_components_for_pass(spec, all_components, pass_id)

    lines: list[str] = [
        "import * as THREE from 'three';",
        "import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';",
        "import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';",
        "import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';",
        "import { BokehPass } from 'three/examples/jsm/postprocessing/BokehPass.js';",
        "import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';",
        "import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';",
        "",
        "export type ProceduralModelOptions = {",
        "  wireframe?: boolean;",
        "  castShadow?: boolean;",
        "  receiveShadow?: boolean;",
        "  textureSize?: number;",
        "  textureAnisotropy?: number;",
        "  qualityPriority?: 'reference-fidelity' | 'balanced';",
        "};",
        "",
        "export type ProceduralModelRuntime = {",
        "  nodes: Record<string, THREE.Object3D>;",
        "  meshes: Record<string, THREE.Mesh>;",
        "  sockets: Record<string, THREE.Object3D>;",
        "  colliders: Record<string, unknown>;",
        "  destructionGroups: Record<string, THREE.Object3D[]>;",
        "};",
        "",
        "type SculptMaterialSpec = Record<string, any>;",
        "",
    ]

    # Plan 1.3 Workstream F.5: real geometry for the primitives that previously fell
    # through to a silent BoxGeometry fallback. Only emit the helper functions a
    # primitive that's actually used in THIS pass's components needs — TypeScript
    # projects commonly build with noUnusedLocals, and an always-emitted
    # buildLatheGeometry/buildTubeGeometry fails that build the moment a spec (or a
    # pass, since blockout only includes macro components) doesn't use them.
    def _calls_geometry_for(component: dict[str, Any]) -> bool:
        # A component whose geometry actually comes from polygonizeSdf() (implicit
        # SDF) or buildVisualHullGeometry() never calls geometry_for() for its OWN
        # `primitive` field at all -- that field is descriptive metadata only on
        # those two paths. Exclude them here so a primitive-specific helper (e.g.
        # buildWatertightCapsule) isn't emitted-but-unused merely because some OTHER
        # component happens to share that primitive label while actually building via
        # SDF/visual-hull -- an always-emitted, never-called helper fails a build with
        # `--noUnusedLocals` (found via implicit_character_torso_limb.json, whose one
        # component is `primitive: "capsule"` + `topologyClass: "implicit"`).
        descriptor = component.get("geometryDescriptor")
        geometry_descriptor = descriptor if isinstance(descriptor, dict) else {}
        is_implicit = component.get("topologyClass") == "implicit" and isinstance(geometry_descriptor.get("sdf"), dict)
        is_visual_hull = isinstance(geometry_descriptor.get("visualHull"), dict)
        return not is_implicit and not is_visual_hull

    used_primitives = {
        str(component.get("primitive")) for component in components if _calls_geometry_for(component)
    }
    implicit_components = [
        component
        for component in components
        if component.get("topologyClass") == "implicit"
        and isinstance(component.get("geometryDescriptor"), dict)
        and isinstance(component["geometryDescriptor"].get("sdf"), dict)
    ]
    visual_hull_components = [
        component
        for component in components
        if isinstance(component.get("geometryDescriptor"), dict)
        and isinstance(component["geometryDescriptor"].get("visualHull"), dict)
    ]
    # A component may require that it stay outside another's surface. Resolved here rather than at
    # each emission site so an unresolvable declaration becomes one visible comment in the output
    # instead of a clearance that silently never ran.
    components_by_id = {
        str(component.get("id")): component
        for component in components
        if isinstance(component, dict) and component.get("id")
    }
    stand_proud_jobs: list[dict[str, Any]] = []
    stand_proud_skipped: list[str] = []
    for component in components:
        proud = component.get("standProud") if isinstance(component, dict) else None
        if not isinstance(proud, dict):
            continue
        component_id = str(component.get("id"))
        target_id = str(proud.get("againstComponentId") or "")
        proud_target = components_by_id.get(target_id)
        if proud_target is None:
            stand_proud_skipped.append(
                f"{component_id}: standProud target {target_id!r} is not in this component tree"
            )
            continue
        # In the target's LOCAL frame -- which is what `toTarget` maps into -- the surface sits
        # where `geometry.scale` put it, and that scale is baked into the vertices rather than into
        # any matrix. So the analytic stack has to be scaled by hand to match. Measured on the
        # fixture: the authored rings span rx 0.12..0.20 while the scaled geometry spans
        # 0.048..0.080, and an ellipsoid target's unit stack claims 0.5 against an actual 0.2. Left
        # unscaled, the march pushed hair to two and a half times the skull's real radius.
        target_transform = proud_target.get("transform")
        sx, sy, sz = scale_triple(
            proud_target, target_transform if isinstance(target_transform, dict) else {}
        )
        rings = stand_proud_ring_stack(proud_target)
        # A ring stack is a SEPARATE description of a shape the generator also builds another way,
        # so the two can disagree and nothing else would notice. The case that occurs: a `lathe` is
        # a revolve and is therefore circular before `geometry.scale`, so its rings must have
        # rx == rz and let the dimensions supply the ellipticity. A fixture here authored rx != rz
        # on a lathe and described a surface 10-33% wider in z than the geometry it stood for.
        if rings and proud_target.get("primitive") == "lathe":
            for ring in rings:
                if abs(ring[1] - ring[2]) > 1e-9:
                    stand_proud_skipped.append(
                        f"{component_id}: standProud target {target_id!r} is a lathe, which is "
                        f"circular before scaling, but its ringStack authors rx {ring[1]} != rz "
                        f"{ring[2]}. The stack does not describe the target's geometry."
                    )
                    rings = None
                    break
        if rings:
            rings = [
                [ring[0] * sy, ring[1] * sx, ring[2] * sz, ring[3] * sz] for ring in rings
            ]
        if not rings:
            stand_proud_skipped.append(
                f"{component_id}: standProud target {target_id!r} "
                f"(primitive {proud_target.get('primitive')!r}) exposes no ring stack to stand proud of"
            )
            continue
        clearance = proud.get("clearance")
        max_push = proud.get("maxPush")
        if not isinstance(clearance, (int, float)) or not isinstance(max_push, (int, float)):
            stand_proud_skipped.append(f"{component_id}: standProud needs numeric clearance and maxPush")
            continue
        stand_proud_jobs.append({
            "componentId": component_id,
            "targetId": target_id,
            "rings": rings,
            "clearance": float(clearance),
            "maxPush": float(max_push),
        })
    if stand_proud_jobs:
        lines.extend(_STAND_PROUD_HELPER_SOURCE.splitlines())
        lines.append("")
    if any(root_tip_gradient(component) for component in components):
        lines.extend(_ROOT_TIP_GRADIENT_HELPER_SOURCE.splitlines())
        lines.append("")
    if any(vertex_paint(component) for component in components):
        lines.extend(_VERTEX_PAINT_HELPER_SOURCE.splitlines())
        lines.append("")

    has_subdivision = any(subdivision_iterations(component) > 0 for component in components)
    has_decimation = any(decimate_ratio(component) is not None for component in components)
    if visual_hull_components:
        lines.extend(
            _VISUAL_HULL_HELPER_SOURCE.replace(
                "__MAX_VISUAL_HULL_TRIANGLES__",
                str(MAX_VISUAL_HULL_TRIANGLES),
            ).splitlines()
        )
        lines.append("")
    if implicit_components:
        lines.extend(_SDF_HELPER_SOURCE.splitlines())
        lines.append("")
    if has_decimation:
        lines.extend(_DECIMATE_HELPER_SOURCE.splitlines())
        lines.append("")
    if has_subdivision:
        lines.extend(
        _SUBDIVISION_HELPER_SOURCE.replace(
            "__MAX_SUBDIVISION_QUAD_FACES__",
            str(MAX_SUBDIVISION_QUAD_FACES),
        ).replace(
            "__MAX_SUBDIVISION_ITERATIONS__",
            str(MAX_SUBDIVISION_ITERATIONS),
        ).splitlines()
        )
        lines.append("")
    if "capsule" in used_primitives:
        lines.extend(
            [
                "// THREE.CapsuleGeometry duplicates every UV-seam vertex (measured: 194 boundary",
                "// edges on the default radius/segments below) -- same benign pattern as box/",
                "// cylinder/sphere/torus, all of which weld cleanly to 0 given a CORRECT weld.",
                "// (A naive vertex-only mergeVertices() reports 64 'non-manifold' edges here, but",
                "// that is a counting artifact, not a real defect: it double-counts a handful of",
                "// near-pole triangles that become degenerate once two of their three corners",
                "// coincide -- confirmed by replicating subdivideCatmullClark's own degenerate-",
                "// triangle-aware vertex identity, which finds a perfectly ordinary 2-manifold.)",
                "// A capsule is the primary shape for skinned limbs/torso (PLAN_1.5), and skinning",
                "// weight computation is O(vertices x bones), so fewer, guaranteed-simple vertices",
                "// is worth having regardless -- authored as a deterministic, closed-by-",
                "// construction mesh instead: shared pole vertices, and",
                "// the radial index taken `% radialSegments` so the seam is never a duplicate",
                "// vertex in the first place, rather than something to weld away afterward.",
                "// Adapted from forge/stage5_rig/emit_rig.py's buildWatertightCapsule (verified",
                "// there: 0 boundary edges, 0 non-manifold edges, deterministic across repeated",
                "// runs) -- ported here rather than imported because this factory and the rig",
                "// emitter are separate generated-output surfaces with no shared runtime module;",
                "// see forge/tests/test_primitive_watertightness.py for the measured proof, and",
                "// coordinate with the rig owner before changing either copy independently.",
                "function buildWatertightCapsule(",
                "  radius: number,",
                "  cylLength: number,",
                "  capSegments: number,",
                "  radialSegments: number,",
                "  heightSegments: number,",
                "): THREE.BufferGeometry {",
                "  const positions: number[] = [];",
                "  const indices: number[] = [];",
                "  const uvs: number[] = [];",
                "  const halfCyl = cylLength / 2;",
                "  const totalSpan = 2 * (Math.PI / 2 * radius) + Math.max(0, cylLength);",
                "  const vOf = (fromBottom: number) => (totalSpan > 0 ? fromBottom / totalSpan : 0);",
                "",
                "  const bottomPoleIndex = positions.length / 3;",
                "  positions.push(0, -halfCyl - radius, 0);",
                "  uvs.push(0.5, vOf(0));",
                "",
                "  const ringStarts: number[] = [];",
                "  const ringV: number[] = [];",
                "  for (let ring = 1; ring <= capSegments; ring += 1) {",
                "    const phi = (Math.PI / 2) * (ring / capSegments);",
                "    const y = -halfCyl - radius * Math.cos(phi);",
                "    const r = radius * Math.sin(phi);",
                "    const start = positions.length / 3;",
                "    ringStarts.push(start);",
                "    ringV.push(vOf(radius * phi));",
                "    for (let radial = 0; radial < radialSegments; radial += 1) {",
                "      const theta = (radial / radialSegments) * Math.PI * 2;",
                "      positions.push(r * Math.cos(theta), y, r * Math.sin(theta));",
                "      uvs.push(radial / radialSegments, vOf(radius * phi));",
                "    }",
                "  }",
                "",
                "  const cylinderRingStarts: number[] = [];",
                "  if (cylLength > 0) {",
                "    for (let step = 1; step <= heightSegments; step += 1) {",
                "      const y = -halfCyl + (cylLength * step) / heightSegments;",
                "      const start = positions.length / 3;",
                "      cylinderRingStarts.push(start);",
                "      const v = vOf(radius * (Math.PI / 2) + halfCyl + y);",
                "      for (let radial = 0; radial < radialSegments; radial += 1) {",
                "        const theta = (radial / radialSegments) * Math.PI * 2;",
                "        positions.push(radius * Math.cos(theta), y, radius * Math.sin(theta));",
                "        uvs.push(radial / radialSegments, v);",
                "      }",
                "    }",
                "  }",
                "",
                "  const topRingStarts: number[] = [];",
                "  for (let ring = capSegments - 1; ring >= 1; ring -= 1) {",
                "    const phi = (Math.PI / 2) * (ring / capSegments);",
                "    const y = halfCyl + radius * Math.cos(phi);",
                "    const r = radius * Math.sin(phi);",
                "    const start = positions.length / 3;",
                "    topRingStarts.push(start);",
                "    const v = vOf(radius * (Math.PI / 2) + Math.max(0, cylLength) + radius * (Math.PI / 2 - phi));",
                "    for (let radial = 0; radial < radialSegments; radial += 1) {",
                "      const theta = (radial / radialSegments) * Math.PI * 2;",
                "      positions.push(r * Math.cos(theta), y, r * Math.sin(theta));",
                "      uvs.push(radial / radialSegments, v);",
                "    }",
                "  }",
                "",
                "  const topPoleIndex = positions.length / 3;",
                "  positions.push(0, halfCyl + radius, 0);",
                "  uvs.push(0.5, vOf(totalSpan));",
                "",
                "  const firstBottomRing = ringStarts[0];",
                "  for (let radial = 0; radial < radialSegments; radial += 1) {",
                "    const next = (radial + 1) % radialSegments;",
                "    indices.push(bottomPoleIndex, firstBottomRing + radial, firstBottomRing + next);",
                "  }",
                "",
                "  const allRings = [...ringStarts, ...cylinderRingStarts, ...topRingStarts];",
                "  for (let i = 0; i < allRings.length - 1; i += 1) {",
                "    const a = allRings[i];",
                "    const b = allRings[i + 1];",
                "    for (let radial = 0; radial < radialSegments; radial += 1) {",
                "      const next = (radial + 1) % radialSegments;",
                "      indices.push(a + radial, a + next, b + next);",
                "      indices.push(a + radial, b + next, b + radial);",
                "    }",
                "  }",
                "",
                "  const lastRing = allRings[allRings.length - 1];",
                "  for (let radial = 0; radial < radialSegments; radial += 1) {",
                "    const next = (radial + 1) % radialSegments;",
                "    indices.push(topPoleIndex, lastRing + next, lastRing + radial);",
                "  }",
                "",
                "  const geometry = new THREE.BufferGeometry();",
                "  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));",
                "  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));",
                "  geometry.setIndex(indices);",
                "  geometry.computeVertexNormals();",
                "  return geometry;",
                "}",
                "",
            ]
        )
    if "extrude" in used_primitives:
        lines.extend(
            [
                "// bevelEnabled defaults to true on THREE.ExtrudeGeometry and rounds every",
                "// corner — sharp/pointed profiles (blades, fork tines, spikes) need",
                "// bevelEnabled: false plus lineTo()-only path segments near the tip, since a",
                "// curve command cannot produce a true converging point.",
                "function buildExtrudeShape(points: [number, number][], holes?: [number, number][][]): THREE.Shape {",
                "  const shape = new THREE.Shape();",
                "  if (points.length > 0) {",
                "    shape.moveTo(points[0][0], points[0][1]);",
                "    for (let i = 1; i < points.length; i += 1) {",
                "      shape.lineTo(points[i][0], points[i][1]);",
                "    }",
                "  }",
                "  // Cutouts (e.g. an oval wire-cutter hole) as THREE.Path added to shape.holes —",
                "  // dep-free boolean subtraction via the tessellator, no CSG library needed.",
                "  for (const loop of holes ?? []) {",
                "    if (loop.length < 3) continue;",
                "    const path = new THREE.Path();",
                "    path.moveTo(loop[0][0], loop[0][1]);",
                "    for (let i = 1; i < loop.length; i += 1) path.lineTo(loop[i][0], loop[i][1]);",
                "    path.closePath();",
                "    shape.holes.push(path);",
                "  }",
                "  return shape;",
                "}",
                "",
                "// Build an N-gon oval loop (for hole authoring from a compact {cx,cy,rx,ry} descriptor).",
                "function ovalLoop(cx: number, cy: number, rx: number, ry: number, seg = 24): [number, number][] {",
                "  const loop: [number, number][] = [];",
                "  for (let i = 0; i < seg; i += 1) {",
                "    const a = (i / seg) * Math.PI * 2;",
                "    loop.push([cx + Math.cos(a) * rx, cy + Math.sin(a) * ry]);",
                "  }",
                "  return loop;",
                "}",
                "",
                "function buildExtrudeGeometry(profile: { points: [number, number][]; depth: number; holes?: [number, number][][]; ovalHoles?: { cx: number; cy: number; rx: number; ry: number }[] }): THREE.ExtrudeGeometry {",
                "  const holes = [...(profile.holes ?? []), ...((profile.ovalHoles ?? []).map((o) => ovalLoop(o.cx, o.cy, o.rx, o.ry)))];",
                "  const shape = buildExtrudeShape(profile.points, holes);",
                "  return new THREE.ExtrudeGeometry(shape, {",
                "    depth: profile.depth,",
                "    bevelEnabled: false,",
                "    steps: 1,",
                "  });",
                "}",
                "",
            ]
        )
    if "ground-blade" in used_primitives:
        lines.extend(
            [
                "// Ground blade: lofts a beveled cross-section along [x, spineY, edgeY] stations.",
                "// Per station the section is: sharp cutting EDGE (z=0) → PRIMARY BEVEL up to the",
                "// grind line (±T) → flat/saber body → SWEDGE near the tip (spine grinds to a false",
                "// edge, z=0) or a squared spine elsewhere. Non-indexed → flat-shaded facets. UVs map",
                "// the doppler gradient along length (u) and height (v).",
                "function buildGroundBladeGeometry(spec: { stations: [number, number, number][]; thickness?: number; grindFrac?: number; swedgeFromTipFrac?: number }): THREE.BufferGeometry {",
                "  const st = spec.stations;",
                "  const T = (spec.thickness ?? 0.05) / 2;",
                "  const grindFrac = spec.grindFrac ?? 0.55;",
                "  const swedgeFrac = spec.swedgeFromTipFrac ?? 0.34;",
                "  const xG = st[0][0];",
                "  const xT = st[st.length - 1][0];",
                "  const len = (xT - xG) || 1;",
                "  // Actual blade Y bounds (stations are [x, topY, botY]) — v must span THESE, not a",
                "  // hardcoded ±0.12: a blade positioned off-origin would otherwise clamp v→1 and make",
                "  // every face sample the bright spine-rim row (the white-tip/washed-facet bug).",
                "  let yMin = Infinity, yMax = -Infinity;",
                "  for (const s of st) { yMin = Math.min(yMin, s[2]); yMax = Math.max(yMax, s[1]); }",
                "  const yH = (yMax - yMin) || 1;",
                "  const ring = (s: [number, number, number]): [number, number, number][] => {",
                "    const [x, topY, botY] = s;",
                "    const h = Math.max(1e-4, topY - botY);",
                "    const grindY = botY + grindFrac * h;",
                "    const swedgeY = topY - 0.42 * h;",
                "    const sz = ((xT - x) / len < swedgeFrac) ? 0 : T;  // swedge → sharp false edge near tip",
                "    return [",
                "      [x, botY, 0], [x, grindY, T], [x, swedgeY, T], [x, topY, sz],",
                "      [x, topY, -sz], [x, swedgeY, -T], [x, grindY, -T],",
                "    ];",
                "  };",
                "  const pos: number[] = [];",
                "  const tri = (a: number[], b: number[], c: number[]) => { pos.push(a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2]); };",
                "  let A = ring(st[0]);",
                "  // guard-end cap (fan) so the base is closed where it meets the guard",
                "  for (let k = 1; k < 6; k++) tri(A[0], A[k], A[k + 1]);",
                "  for (let i = 1; i < st.length; i++) {",
                "    const B = ring(st[i]);",
                "    for (let k = 0; k < 7; k++) {",
                "      const k2 = (k + 1) % 7;",
                "      tri(A[k], A[k2], B[k2]);",
                "      tri(A[k], B[k2], B[k]);",
                "    }",
                "    A = B;",
                "  }",
                "  const g = new THREE.BufferGeometry();",
                "  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));",
                "  g.computeVertexNormals();",
                "  const uv: number[] = [];",
                "  for (let t = 0; t < pos.length; t += 3) uv.push((pos[t] - xG) / len, (pos[t + 1] - yMin) / yH);",
                "  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));",
                "  return g;",
                "}",
                "",
            ]
        )
    if "lathe" in used_primitives:
        lines.extend(
            [
                "function buildLatheGeometry(profile: { points: [number, number][]; segments?: number }): THREE.LatheGeometry {",
                "  const points = profile.points.map(([x, y]) => new THREE.Vector2(Math.max(0.0001, x), y));",
                "  return new THREE.LatheGeometry(points, profile.segments ?? 24);",
                "}",
                "",
            ]
        )
    if "tube" in used_primitives:
        lines.extend(
            [
                "function buildTubeGeometry(",
                "  path: { points: [number, number, number][]; radius?: number; radialSegments?: number; closed?: boolean },",
                "): THREE.TubeGeometry {",
                "  const vectors = path.points.map(([x, y, z]) => new THREE.Vector3(x, y, z));",
                "  const curve = new THREE.CatmullRomCurve3(vectors, path.closed ?? false);",
                "  const tubularSegments = Math.max(8, path.points.length * 6);",
                "  return new THREE.TubeGeometry(curve, tubularSegments, path.radius ?? 0.05, path.radialSegments ?? 8, path.closed ?? false);",
                "}",
                "",
            ]
        )
    if "tapered-sweep" in used_primitives:
        lines.extend(
            [
                "type TaperedStation = { position: [number, number, number]; rx: number; rz: number; twist?: number };",
                "",
                "// Frames come from PARALLEL TRANSPORT, not from a Frenet frame. A Frenet frame is defined by",
                "// the curve's normal, which flips sign wherever the path has an inflection or straightens out,",
                "// and every flip twists the surface 180 degrees within one segment. Carrying the previous frame",
                "// forward and removing only its along-path component keeps the twist continuous. THREE's own",
                "// extrudePath and TubeGeometry do not expose this, which is why this is hand-built.",
                "function buildTaperedSweepGeometry(",
                "  sweep: { stations: TaperedStation[]; radialSegments?: number; capEnds?: boolean },",
                "): THREE.BufferGeometry {",
                "  const stations = sweep.stations;",
                "  if (stations.length < 2) throw new Error('tapered-sweep needs at least two stations');",
                "  const radial = Math.max(3, sweep.radialSegments ?? 10);",
                "  const centres = stations.map((s) => new THREE.Vector3(...s.position));",
                "",
                "  const tangents = centres.map((_, i) => {",
                "    const prev = centres[Math.max(0, i - 1)];",
                "    const next = centres[Math.min(centres.length - 1, i + 1)];",
                "    const t = next.clone().sub(prev);",
                "    // Coincident neighbours would normalise to NaN and poison every downstream vertex.",
                "    return t.lengthSq() < 1e-12 ? new THREE.Vector3(0, 1, 0) : t.normalize();",
                "  });",
                "",
                "  // Seed a reference axis that is not parallel to the first tangent, or the first cross",
                "  // product is degenerate and the whole sweep collapses to a line.",
                "  let ref = new THREE.Vector3(0, 0, 1);",
                "  if (Math.abs(tangents[0].dot(ref)) > 0.9) ref = new THREE.Vector3(1, 0, 0);",
                "",
                "  const normals: THREE.Vector3[] = [];",
                "  const binormals: THREE.Vector3[] = [];",
                "  let carried = ref.clone().sub(tangents[0].clone().multiplyScalar(ref.dot(tangents[0]))).normalize();",
                "  for (let i = 0; i < tangents.length; i += 1) {",
                "    const t = tangents[i];",
                "    // Project the carried frame back onto the plane perpendicular to this tangent.",
                "    const n = carried.clone().sub(t.clone().multiplyScalar(carried.dot(t)));",
                "    if (n.lengthSq() < 1e-12) {",
                "      const fallback = Math.abs(t.y) > 0.9 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0);",
                "      n.copy(fallback.sub(t.clone().multiplyScalar(fallback.dot(t))));",
                "    }",
                "    n.normalize();",
                "    normals.push(n);",
                "    binormals.push(new THREE.Vector3().crossVectors(t, n).normalize());",
                "    carried = n;",
                "  }",
                "",
                "  const positions: number[] = [];",
                "  const uvs: number[] = [];",
                "  const indices: number[] = [];",
                "  const ringStart: number[] = [];",
                "  const isPoint: boolean[] = [];",
                "",
                "  for (let i = 0; i < stations.length; i += 1) {",
                "    const st = stations[i];",
                "    const v = i / (stations.length - 1);",
                "    ringStart.push(positions.length / 3);",
                "    // A station whose section has collapsed emits ONE vertex, not a ring of radius zero.",
                "    // A degenerate ring still carries `radial` coincident vertices and `radial` zero-area",
                "    // triangles, so the lock ends in a blunt cap the width of the floating-point noise",
                "    // rather than at a point -- and a hair lock, a horn or a blade tip has to reach a point.",
                "    if (st.rx <= 1e-6 && st.rz <= 1e-6) {",
                "      isPoint.push(true);",
                "      positions.push(centres[i].x, centres[i].y, centres[i].z);",
                "      uvs.push(0.5, v);",
                "      continue;",
                "    }",
                "    isPoint.push(false);",
                "    const twist = ((st.twist ?? 0) * Math.PI) / 180;",
                "    for (let j = 0; j <= radial; j += 1) {",
                "      const theta = (j / radial) * Math.PI * 2 + twist;",
                "      const offset = normals[i].clone().multiplyScalar(Math.cos(theta) * st.rx)",
                "        .add(binormals[i].clone().multiplyScalar(Math.sin(theta) * st.rz));",
                "      const p = centres[i].clone().add(offset);",
                "      positions.push(p.x, p.y, p.z);",
                "      uvs.push(j / radial, v);",
                "    }",
                "  }",
                "",
                "  for (let i = 0; i < stations.length - 1; i += 1) {",
                "    const a0 = ringStart[i];",
                "    const b0 = ringStart[i + 1];",
                "    if (isPoint[i] && isPoint[i + 1]) continue;   // two collapsed stations bound nothing",
                "    for (let j = 0; j < radial; j += 1) {",
                "      // Wound so the face normal points radially OUTWARD.",
                "      //",
                "      // Ring vertices advance from `normal` toward `binormal`, and binormal is",
                "      // tangent x normal, so increasing theta runs counter-clockwise seen from the",
                "      // far end of the segment. Taking the ring-to-ring edge first therefore puts",
                "      // the cross product on the inside. Measured as signed volume on the built",
                "      // mesh: every tapered-sweep came out negative -- a torso at -0.0674 and a",
                "      // tail at -0.0044 against a positive ellipsoid head -- so every sweep this",
                "      // generator has ever emitted rendered its back faces, with normals pointing",
                "      // into the solid and every lighting judgement made on the wrong surface.",
                "      if (isPoint[i]) indices.push(a0, b0 + j + 1, b0 + j);",
                "      else if (isPoint[i + 1]) indices.push(a0 + j, a0 + j + 1, b0);",
                "      else indices.push(a0 + j, a0 + j + 1, b0 + j, a0 + j + 1, b0 + j + 1, b0 + j);",
                "    }",
                "  }",
                "",
                "  if (sweep.capEnds ?? true) {",
                "    for (const end of [0, stations.length - 1]) {",
                "      if (isPoint[end]) continue;   // a point end is already closed",
                "      const centreIndex = positions.length / 3;",
                "      positions.push(centres[end].x, centres[end].y, centres[end].z);",
                "      uvs.push(0.5, end === 0 ? 0 : 1);",
                "      const base = ringStart[end];",
                "      for (let j = 0; j < radial; j += 1) {",
                "        if (end === 0) indices.push(centreIndex, base + j + 1, base + j);",
                "        else indices.push(centreIndex, base + j, base + j + 1);",
                "      }",
                "    }",
                "  }",
                "",
                "  const geometry = new THREE.BufferGeometry();",
                "  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));",
                "  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));",
                "  geometry.setIndex(indices);",
                "  geometry.computeVertexNormals();",
                "  return geometry;",
                "}",
                "",
            ]
        )
    if "curve-sweep" in used_primitives:
        lines.extend(
            [
                "// Plan 1.3 F.6 — sweep a thin 2D cross-section along a 3D spine so a curved",
                "// form (hooked blade, handle) reads correctly from EVERY camera angle, not just",
                "// the reference angle a flat extrude happens to match. Uses ExtrudeGeometry's",
                "// native extrudePath; bevelEnabled: false keeps sharp tips (same rule as F.5).",
                "function buildCurveSweepGeometry(",
                "  sweep: { spine: [number, number, number][]; crossSection: { points: [number, number][] }; closed?: boolean },",
                "): THREE.ExtrudeGeometry {",
                "  const shape = new THREE.Shape();",
                "  const cs = sweep.crossSection.points;",
                "  if (cs.length > 0) {",
                "    shape.moveTo(cs[0][0], cs[0][1]);",
                "    for (let i = 1; i < cs.length; i += 1) shape.lineTo(cs[i][0], cs[i][1]);",
                "    shape.closePath();",
                "  }",
                "  const spine = sweep.spine.map(([x, y, z]) => new THREE.Vector3(x, y, z));",
                "  const path = new THREE.CatmullRomCurve3(spine, sweep.closed ?? false);",
                "  return new THREE.ExtrudeGeometry(shape, {",
                "    extrudePath: path,",
                "    steps: Math.max(24, spine.length * 8),",
                "    bevelEnabled: false,",
                "  });",
                "}",
                "",
            ]
        )

    lines.extend(
        [
        "function hashString(value: string): number {",
        "  let hash = 2166136261;",
        "  for (let index = 0; index < value.length; index += 1) {",
        "    hash ^= value.charCodeAt(index);",
        "    hash = Math.imul(hash, 16777619);",
        "  }",
        "  return hash >>> 0;",
        "}",
        "",
        "function readLayerNumber(value: unknown, keys: string[], fallback: number): number {",
        "  if (typeof value === 'number') return value;",
        "  if (value && typeof value === 'object') {",
        "    const record = value as Record<string, unknown>;",
        "    for (const key of keys) {",
        "      if (typeof record[key] === 'number') return record[key] as number;",
        "    }",
        "  }",
        "  return fallback;",
        "}",
        "",
        "function hexToRgb(hex: string): [number, number, number] {",
        "  const normalized = /^#[0-9a-f]{3}$/i.test(hex)",
        "    ? '#' + hex.slice(1).split('').map((part) => part + part).join('')",
        "    : hex;",
        "  const value = /^#[0-9a-f]{6}$/i.test(normalized) ? Number.parseInt(normalized.slice(1), 16) : 0x8a7a5f;",
        "  return [clampAlbedoChannel((value >> 16) & 255), clampAlbedoChannel((value >> 8) & 255), clampAlbedoChannel(value & 255)];",
        "}",
        "",
        "function materialPalette(spec: SculptMaterialSpec): string[] {",
        "  const palette = spec.colorVariation?.palette;",
        "  if (Array.isArray(palette) && palette.length > 0) return palette.filter((value) => typeof value === 'string');",
        "  const secondary = spec.albedo?.secondary;",
        "  const colors = [spec.baseColor ?? spec.color ?? spec.albedo?.dominant, ...(Array.isArray(secondary) ? secondary : [])];",
        "  return colors.filter((value): value is string => typeof value === 'string' && value.startsWith('#'));",
        "}",
        "",
        "function clamp01(value: number): number {",
        "  return Math.max(0, Math.min(1, value));",
        "}",
        "",
        "function clampAlbedoChannel(value: number): number {",
        "  return Math.max(30, Math.min(240, Math.round(value)));",
        "}",
        "",
        "function clampPbrF0(value: number): number {",
        "  return Math.max(0.02, Math.min(1, value));",
        "}",
        "",
        "function clampPbrIor(value: number): number {",
        "  return Math.max(1, Math.min(2.5, value));",
        "}",
        "",
        "function clampPbrMetalness(value: number): number {",
        "  return value >= 0.5 ? 1 : 0;",
        "}",
        "",
        "function clampedAlbedoColor(spec: SculptMaterialSpec): THREE.Color {",
        "  const source = typeof spec.baseColor === 'string' ? spec.baseColor : '#8A7A5F';",
        "  // setStyle with an explicit SRGBColorSpace, NOT the numeric constructor.",
        "  //",
        "  // `new THREE.Color(r, g, b)` treats its arguments as LINEAR working-space components,",
        "  // while an authored `baseColor` hex is sRGB. Feeding one to the other skipped the",
        "  // transfer function and lifted every dark albedo: #2e2a28, authored as a near-black",
        "  // vinyl, rendered at roughly sRGB 0.46 — a mid grey. The error is largest exactly where",
        "  // it matters most, because the transfer curve is steepest near black.",
        "  return new THREE.Color().setStyle(source, THREE.SRGBColorSpace);",
        "}",
        "",
        "function smoothCurve(value: number): number {",
        "  return value * value * (3 - 2 * value);",
        "}",
        "",
        "function periodicHash(x: number, y: number, seed: number, periodX: number, periodY: number): number {",
        "  const wrappedX = ((x % periodX) + periodX) % periodX;",
        "  const wrappedY = ((y % periodY) + periodY) % periodY;",
        "  let value = Math.imul(wrappedX + seed * 17, 374761393) ^ Math.imul(wrappedY + seed * 31, 668265263);",
        "  value = Math.imul(value ^ (value >>> 13), 1274126177);",
        "  return ((value ^ (value >>> 16)) >>> 0) / 4294967295;",
        "}",
        "",
        "function periodicValueNoise(u: number, v: number, seed: number, periodX: number, periodY: number): number {",
        "  const x = u * periodX;",
        "  const y = v * periodY;",
        "  const x0 = Math.floor(x);",
        "  const y0 = Math.floor(y);",
        "  const tx = smoothCurve(x - x0);",
        "  const ty = smoothCurve(y - y0);",
        "  const a = periodicHash(x0, y0, seed, periodX, periodY);",
        "  const b = periodicHash(x0 + 1, y0, seed, periodX, periodY);",
        "  const c = periodicHash(x0, y0 + 1, seed, periodX, periodY);",
        "  const d = periodicHash(x0 + 1, y0 + 1, seed, periodX, periodY);",
        "  return THREE.MathUtils.lerp(THREE.MathUtils.lerp(a, b, tx), THREE.MathUtils.lerp(c, d, tx), ty);",
        "}",
        "",
        "type SurfaceBand = {",
        "  frequency: number;",
        "  amplitude: number;",
        "  stretchX: number;",
        "  stretchY: number;",
        "  ridge: boolean;",
        "};",
        "",
        "function surfaceBands(spec: SculptMaterialSpec): SurfaceBand[] {",
        "  const source = Array.isArray(spec.surfaceFrequencyBands) ? spec.surfaceFrequencyBands : [];",
        "  const parsed = source.flatMap((item: unknown) => {",
        "    if (!item || typeof item !== 'object') return [];",
        "    const band = item as Record<string, unknown>;",
        "    const frequency = typeof band.frequency === 'number' ? band.frequency : 0;",
        "    const amplitude = typeof band.amplitude === 'number' ? band.amplitude : 0;",
        "    if (frequency <= 0 || amplitude <= 0) return [];",
        "    const stretch = Array.isArray(band.stretch) ? band.stretch : [1, 1];",
        "    const description = `${String(band.pattern ?? '')} ${String(band.role ?? '')}`.toLowerCase();",
        "    return [{",
        "      frequency,",
        "      amplitude,",
        "      stretchX: typeof stretch[0] === 'number' ? Math.max(0.1, stretch[0]) : 1,",
        "      stretchY: typeof stretch[1] === 'number' ? Math.max(0.1, stretch[1]) : 1,",
        "      ridge: /(ridge|groove|grain|fiber|striated|crack)/.test(description),",
        "    }];",
        "  });",
        "  return parsed.length > 0 ? parsed : [",
        "    { frequency: 2, amplitude: 0.42, stretchX: 1, stretchY: 1, ridge: false },",
        "    { frequency: 12, amplitude: 0.22, stretchX: 1, stretchY: 1, ridge: false },",
        "    { frequency: 56, amplitude: 0.08, stretchX: 1, stretchY: 1, ridge: false },",
        "  ];",
        "}",
        "",
        "function sampleSurface(u: number, v: number, bands: SurfaceBand[], seed: number): number {",
        "  let value = 0;",
        "  let weight = 0;",
        "  for (let index = 0; index < bands.length; index += 1) {",
        "    const band = bands[index];",
        "    const periodX = Math.max(1, Math.round(band.frequency * band.stretchX));",
        "    const periodY = Math.max(1, Math.round(band.frequency * band.stretchY));",
        "    let sample = periodicValueNoise(u, v, seed + index * 1013, periodX, periodY);",
        "    if (band.ridge) sample = 1 - Math.abs(sample * 2 - 1);",
        "    value += sample * band.amplitude;",
        "    weight += band.amplitude;",
        "  }",
        "  return weight > 0 ? clamp01(value / weight) : 0.5;",
        "}",
        "",
        "function mixPalette(colors: [number, number, number][], value: number): [number, number, number] {",
        "  if (colors.length === 1) return colors[0];",
        "  const scaled = clamp01(value) * (colors.length - 1);",
        "  const index = Math.min(colors.length - 2, Math.floor(scaled));",
        "  const mix = scaled - index;",
        "  const a = colors[index];",
        "  const b = colors[index + 1];",
        "  return [",
        "    Math.round(THREE.MathUtils.lerp(a[0], b[0], mix)),",
        "    Math.round(THREE.MathUtils.lerp(a[1], b[1], mix)),",
        "    Math.round(THREE.MathUtils.lerp(a[2], b[2], mix)),",
        "  ];",
        "}",
        "",
        "type ColorGradientStop = { offset: number; color: string };",
        "type ColorGradientSpec = {",
        "  type: 'linear' | 'radial';",
        "  axis: [number, number];",
        "  stops: ColorGradientStop[];",
        "};",
        "",
        "function parseRgba(value: string): [number, number, number] {",
        "  const match = /rgba?\\(\\s*(\\d+)\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)/.exec(value);",
        "  if (!match) return [138, 122, 95];",
        "  return [clampAlbedoChannel(Number(match[1])), clampAlbedoChannel(Number(match[2])), clampAlbedoChannel(Number(match[3]))];",
        "}",
        "",
        "// Analytical per-pixel gradient sample. The extraction schema's colorGradient carries",
        "// exact rgba(...) stop colors (see extract_part_color_recipe.py), so this samples the",
        "// same trend directly in JS math rather than round-tripping through a Canvas 2D",
        "// createLinearGradient/createRadialGradient object — same visual result, and it composes",
        "// directly with the existing noise/height-correlated colorVariation blend below.",
        "function sampleColorGradient(gradient: ColorGradientSpec, u: number, v: number): [number, number, number] {",
        "  const stops = gradient.stops.length >= 2 ? gradient.stops : [{ offset: 0, color: 'rgba(138,122,95,1)' }, { offset: 1, color: 'rgba(138,122,95,1)' }];",
        "  let t: number;",
        "  if (gradient.type === 'radial') {",
        "    const [cx, cy] = gradient.axis;",
        "    const dx = u - cx;",
        "    const dy = v - cy;",
        "    const maxRadius = Math.max(0.001, Math.hypot(Math.max(cx, 1 - cx), Math.max(cy, 1 - cy)));",
        "    t = clamp01(Math.hypot(dx, dy) / maxRadius);",
        "  } else {",
        "    const [ax, ay] = gradient.axis;",
        "    const projection = (u - 0.5) * ax + (v - 0.5) * ay;",
        "    const maxProjection = 0.5 * (Math.abs(ax) + Math.abs(ay)) || 0.5;",
        "    t = clamp01(projection / maxProjection + 0.5);",
        "  }",
        "  const scaled = t * (stops.length - 1);",
        "  const index = Math.min(stops.length - 2, Math.max(0, Math.floor(scaled)));",
        "  const mix = scaled - index;",
        "  const a = parseRgba(stops[index].color);",
        "  const b = parseRgba(stops[index + 1].color);",
        "  return [",
        "    THREE.MathUtils.lerp(a[0], b[0], mix),",
        "    THREE.MathUtils.lerp(a[1], b[1], mix),",
        "    THREE.MathUtils.lerp(a[2], b[2], mix),",
        "  ];",
        "}",
        "",
        "function writePixel(data: Uint8ClampedArray, offset: number, red: number, green: number, blue: number): void {",
        "  data[offset] = Math.max(0, Math.min(255, Math.round(red)));",
        "  data[offset + 1] = Math.max(0, Math.min(255, Math.round(green)));",
        "  data[offset + 2] = Math.max(0, Math.min(255, Math.round(blue)));",
        "  data[offset + 3] = 255;",
        "}",
        "",
        "function makeCanvas(size: number): HTMLCanvasElement {",
        "  const canvas = document.createElement('canvas');",
        "  canvas.width = size;",
        "  canvas.height = size;",
        "  return canvas;",
        "}",
        "",
        "function createMapTexture(",
        "  canvas: HTMLCanvasElement,",
        "  colorSpace: THREE.ColorSpace,",
        "  spec: SculptMaterialSpec,",
        "  options: ProceduralModelOptions,",
        "): THREE.CanvasTexture {",
        "  const texture = new THREE.CanvasTexture(canvas);",
        "  const projection = spec.textureProjection && typeof spec.textureProjection === 'object' ? spec.textureProjection : {};",
        "  const repeat = Array.isArray(projection.repeat) ? projection.repeat : [2, 2];",
        "  texture.colorSpace = colorSpace;",
        "  texture.wrapS = THREE.RepeatWrapping;",
        "  texture.wrapT = THREE.RepeatWrapping;",
        "  texture.repeat.set(",
        "    typeof repeat[0] === 'number' ? repeat[0] : 2,",
        "    typeof repeat[1] === 'number' ? repeat[1] : 2,",
        "  );",
        "  texture.anisotropy = Math.max(1, Math.round(options.textureAnisotropy ?? projection.anisotropy ?? 8));",
        "  texture.needsUpdate = true;",
        "  return texture;",
        "}",
        "",
        "type ProceduralTextureSet = {",
        "  albedo: THREE.Texture;",
        "  roughness: THREE.Texture;",
        "  height: THREE.Texture;",
        "  normal: THREE.Texture;",
        "  ao: THREE.Texture;",
        "  source: 'reference-pixel-extraction' | 'procedural';",
        "};",
        "",
        "function referenceMapUrl(spec: SculptMaterialSpec, channel: string): string | null {",
        "  const reference = spec.referencePbr;",
        "  if (!reference || typeof reference !== 'object') return null;",
        "  if (reference.usable === false) return null;",
        "  const confidence = typeof reference.confidence === 'number'",
        "    ? reference.confidence",
        "    : (typeof reference.estimatedFidelity === 'number' ? reference.estimatedFidelity : 0);",
        "  const threshold = typeof reference.targetThreshold === 'number' ? reference.targetThreshold : 0.7;",
        "  if (confidence < threshold) return null;",
        "  const maps = reference.maps;",
        "  if (!maps || typeof maps !== 'object') return null;",
        "  const map = (maps as Record<string, unknown>)[channel];",
        "  if (!map || typeof map !== 'object') return null;",
        "  const record = map as Record<string, unknown>;",
        "  const url = typeof record.url === 'string' && record.url.trim() ? record.url : record.path;",
        "  return typeof url === 'string' && url.trim() ? url : null;",
        "}",
        "",
        "function createLoadedMapTexture(",
        "  url: string,",
        "  colorSpace: THREE.ColorSpace,",
        "  spec: SculptMaterialSpec,",
        "  options: ProceduralModelOptions,",
        "): THREE.Texture {",
        "  const texture = new THREE.TextureLoader().load(url);",
        "  const projection = spec.textureProjection && typeof spec.textureProjection === 'object' ? spec.textureProjection : {};",
        "  const repeat = Array.isArray(projection.repeat) ? projection.repeat : [1, 1];",
        "  texture.colorSpace = colorSpace;",
        "  texture.wrapS = THREE.RepeatWrapping;",
        "  texture.wrapT = THREE.RepeatWrapping;",
        "  texture.repeat.set(",
        "    typeof repeat[0] === 'number' ? repeat[0] : 1,",
        "    typeof repeat[1] === 'number' ? repeat[1] : 1,",
        "  );",
        "  texture.anisotropy = Math.max(1, Math.round(options.textureAnisotropy ?? projection.anisotropy ?? 8));",
        "  texture.needsUpdate = true;",
        "  return texture;",
        "}",
        "",
        "function makeReferenceTextureSet(spec: SculptMaterialSpec, options: ProceduralModelOptions): ProceduralTextureSet | null {",
        "  const albedo = referenceMapUrl(spec, 'albedo');",
        "  const roughness = referenceMapUrl(spec, 'roughness');",
        "  const height = referenceMapUrl(spec, 'height');",
        "  const normal = referenceMapUrl(spec, 'normal');",
        "  const ao = referenceMapUrl(spec, 'ao');",
        "  if (!albedo || !roughness || !height || !normal || !ao) return null;",
        "  return {",
        "    albedo: createLoadedMapTexture(albedo, THREE.SRGBColorSpace, spec, options),",
        "    roughness: createLoadedMapTexture(roughness, THREE.NoColorSpace, spec, options),",
        "    height: createLoadedMapTexture(height, THREE.NoColorSpace, spec, options),",
        "    normal: createLoadedMapTexture(normal, THREE.NoColorSpace, spec, options),",
        "    ao: createLoadedMapTexture(ao, THREE.NoColorSpace, spec, options),",
        "    source: 'reference-pixel-extraction',",
        "  };",
        "}",
        "",
        "function makeProceduralTextureSet(",
        "  id: string,",
        "  spec: SculptMaterialSpec,",
        "  options: ProceduralModelOptions,",
        "): ProceduralTextureSet | null {",
        "  if (typeof document === 'undefined') return null;",
        "  const qualityFirst = (options.qualityPriority ?? 'reference-fidelity') === 'reference-fidelity';",
        "  const requested = options.textureSize ?? spec.textureResolution;",
        "  const requestedSize = typeof requested === 'number' && Number.isFinite(requested)",
        "    ? requested",
        "    : (qualityFirst ? 1024 : 512);",
        "  const size = Math.max(256, Math.min(2048, 2 ** Math.round(Math.log2(requestedSize))));",
        "  const canvases = {",
        "    albedo: makeCanvas(size),",
        "    roughness: makeCanvas(size),",
        "    height: makeCanvas(size),",
        "    normal: makeCanvas(size),",
        "    ao: makeCanvas(size),",
        "  };",
        "  const contexts = {",
        "    albedo: canvases.albedo.getContext('2d'),",
        "    roughness: canvases.roughness.getContext('2d'),",
        "    height: canvases.height.getContext('2d'),",
        "    normal: canvases.normal.getContext('2d'),",
        "    ao: canvases.ao.getContext('2d'),",
        "  };",
        "  if (!contexts.albedo || !contexts.roughness || !contexts.height || !contexts.normal || !contexts.ao) return null;",
        "  const images = {",
        "    albedo: contexts.albedo.createImageData(size, size),",
        "    roughness: contexts.roughness.createImageData(size, size),",
        "    height: contexts.height.createImageData(size, size),",
        "    normal: contexts.normal.createImageData(size, size),",
        "    ao: contexts.ao.createImageData(size, size),",
        "  };",
        "  const seed = hashString(id);",
        "  const bands = surfaceBands(spec);",
        "  const heightField = new Float32Array(size * size);",
        "  const roughnessField = new Float32Array(size * size);",
        "  const palette = materialPalette(spec);",
        "  const fallback = typeof spec.baseColor === 'string' ? spec.baseColor : '#8A7A5F';",
        "  const colors = (palette.length >= 2 ? palette : [fallback, '#6E614B', '#A08F70']).map(hexToRgb);",
        "  const baseRoughness = clamp01(readLayerNumber(spec.roughness, ['base'], 0.76));",
        "  const roughnessVariation = clamp01(readLayerNumber(spec.roughness, ['variation'], 0.18));",
        "  const colorAmplitude = clamp01(readLayerNumber(spec.colorVariation, ['amplitude', 'variation'], 0.18));",
        "  const heightCorrelation = clamp01(readLayerNumber(spec.colorVariation, ['heightCorrelation'], 0.3));",
        "  const colorGradient: ColorGradientSpec | undefined = spec.colorGradient;",
        "  for (let y = 0; y < size; y += 1) {",
        "    const v = y / size;",
        "    for (let x = 0; x < size; x += 1) {",
        "      const u = x / size;",
        "      const index = y * size + x;",
        "      const height = sampleSurface(u, v, bands, seed + 101);",
        "      const roughNoise = sampleSurface(u, v, bands, seed + 7001);",
        "      const colorNoise = sampleSurface(u, v, bands, seed + 15013);",
        "      heightField[index] = height;",
        "      roughnessField[index] = clamp01(baseRoughness + (roughNoise - 0.5) * roughnessVariation * 2);",
        "      let color: [number, number, number];",
        "      if (colorGradient) {",
        "        // Evidence-derived spatial gradient (Plan 1.3 Workstream C) takes priority",
        "        // over the noise-based palette blend below — it is a measured trend, not a guess.",
        "        color = sampleColorGradient(colorGradient, u, v);",
        "      } else {",
        "        const paletteValue = clamp01(",
        "          0.5 + (colorNoise - 0.5) * colorAmplitude * 2 + (height - 0.5) * heightCorrelation",
        "        );",
        "        color = mixPalette(colors, paletteValue);",
        "      }",
        "      writePixel(images.albedo.data, index * 4, color[0], color[1], color[2]);",
        "    }",
        "  }",
        "  const normalStrength = Math.max(0.05, readLayerNumber(spec.normal, ['strength', 'amplitude'], 0.35));",
        "  const aoStrength = clamp01(readLayerNumber(spec.ambientOcclusion, ['cavityStrength', 'strength'], 0.35));",
        "  for (let y = 0; y < size; y += 1) {",
        "    const up = ((y - 1 + size) % size) * size;",
        "    const down = ((y + 1) % size) * size;",
        "    for (let x = 0; x < size; x += 1) {",
        "      const left = (x - 1 + size) % size;",
        "      const right = (x + 1) % size;",
        "      const index = y * size + x;",
        "      const center = heightField[index];",
        "      const dx = (heightField[y * size + right] - heightField[y * size + left]) * normalStrength * 6;",
        "      const dy = (heightField[down + x] - heightField[up + x]) * normalStrength * 6;",
        "      const inverseLength = 1 / Math.sqrt(dx * dx + dy * dy + 1);",
        "      const normalX = -dx * inverseLength;",
        "      const normalY = -dy * inverseLength;",
        "      const normalZ = inverseLength;",
        "      const neighborAverage = (",
        "        heightField[y * size + left] + heightField[y * size + right]",
        "        + heightField[up + x] + heightField[down + x]",
        "      ) * 0.25;",
        "      const cavity = Math.max(0, neighborAverage - center);",
        "      const ao = clamp01(1 - aoStrength * (cavity * 12 + (1 - center) * 0.16));",
        "      const offset = index * 4;",
        "      const heightByte = center * 255;",
        "      const roughnessByte = roughnessField[index] * 255;",
        "      writePixel(images.height.data, offset, heightByte, heightByte, heightByte);",
        "      writePixel(images.roughness.data, offset, roughnessByte, roughnessByte, roughnessByte);",
        "      writePixel(",
        "        images.normal.data, offset,",
        "        (normalX * 0.5 + 0.5) * 255,",
        "        (normalY * 0.5 + 0.5) * 255,",
        "        (normalZ * 0.5 + 0.5) * 255,",
        "      );",
        "      writePixel(images.ao.data, offset, ao * 255, ao * 255, ao * 255);",
        "    }",
        "  }",
        "  contexts.albedo.putImageData(images.albedo, 0, 0);",
        "  contexts.roughness.putImageData(images.roughness, 0, 0);",
        "  contexts.height.putImageData(images.height, 0, 0);",
        "  contexts.normal.putImageData(images.normal, 0, 0);",
        "  contexts.ao.putImageData(images.ao, 0, 0);",
        "  return {",
        "    albedo: createMapTexture(canvases.albedo, THREE.SRGBColorSpace, spec, options),",
        "    roughness: createMapTexture(canvases.roughness, THREE.NoColorSpace, spec, options),",
        "    height: createMapTexture(canvases.height, THREE.NoColorSpace, spec, options),",
        "    normal: createMapTexture(canvases.normal, THREE.NoColorSpace, spec, options),",
        "    ao: createMapTexture(canvases.ao, THREE.NoColorSpace, spec, options),",
        "    source: 'procedural',",
        "  };",
        "}",
        "",
        "function createSculptMaterial(id: string, spec: SculptMaterialSpec, options: ProceduralModelOptions, denseComponent = false): THREE.MeshPhysicalMaterial {",
        "  // A material that declares -- with evidence -- that its subject carries no texture",
        "  // detail gets NO texture set. Synthesising one anyway is not a harmless default: the",
        "  // branch below then forces color to white and roughness to 1 and reads both from the",
        "  // generated maps, so the authored albedo and the reference-derived roughness are both",
        "  // discarded, and the model gains mottling the reference does not have. Measured on the",
        "  // tuxedo cat, whose black fur rendered as speckled grey-and-white from a palette that",
        "  // only ever described two flat regions.",
        "  const textureless = (spec.textureless as { declared?: boolean } | undefined)?.declared === true;",
        "  const textures = textureless",
        "    ? null",
        "    : makeReferenceTextureSet(spec, options) ?? makeProceduralTextureSet(id, spec, options);",
        "  const material = new THREE.MeshPhysicalMaterial({",
        "    color: textures ? 0xffffff : clampedAlbedoColor(spec),",
        "    roughness: textures ? 1 : clamp01(readLayerNumber(spec.roughness, ['base'], 0.76)),",
        "    metalness: clampPbrMetalness(readLayerNumber(spec.metalness, ['base'], 0.0)),",
        "    clearcoat: clamp01(readLayerNumber(spec.clearcoat, ['base', 'amount'], 0)),",
        "    clearcoatRoughness: clamp01(readLayerNumber(spec.clearcoatRoughness, ['base'], 0.25)),",
        "    transmission: clamp01(readLayerNumber(spec.transmission, ['base', 'amount'], 0)),",
        "    ior: clampPbrIor(readLayerNumber(spec.ior, ['base', 'value'], 1.5)),",
        "    thickness: Math.max(0, readLayerNumber(spec.thickness, ['base', 'amount'], 0)),",
        "    attenuationDistance: Math.max(0.001, readLayerNumber(spec.attenuationDistance, ['base', 'value'], Infinity)),",
        "    attenuationColor: new THREE.Color(typeof spec.attenuationColor === 'string' ? spec.attenuationColor : '#ffffff'),",
        "    sheen: clamp01(readLayerNumber(spec.sheen, ['base', 'amount'], 0)),",
        "    sheenColor: new THREE.Color(typeof spec.sheenColor === 'string' ? spec.sheenColor : '#ffffff'),",
        "    sheenRoughness: clamp01(readLayerNumber(spec.sheenRoughness, ['base'], 1.0)),",
        "    iridescence: clamp01(readLayerNumber(spec.iridescence, ['base', 'amount'], 0)),",
        "    iridescenceIOR: clampPbrIor(readLayerNumber(spec.iridescenceIOR, ['base', 'value'], 1.3)),",
        "    anisotropy: clamp01(readLayerNumber(spec.anisotropy, ['base', 'amount'], 0)),",
        "    anisotropyRotation: readLayerNumber(spec.anisotropy, ['rotation'], 0),",
        "    specularIntensity: clampPbrF0(readLayerNumber(spec.specularF0 ?? spec.f0 ?? spec.specularIntensity, ['base', 'value'], 1.0)),",
        "    specularColor: new THREE.Color(typeof spec.specularColor === 'string' ? spec.specularColor : '#ffffff'),",
        "    emissive: new THREE.Color(typeof spec.emissive === 'string' ? spec.emissive : '#000000'),",
        "    emissiveIntensity: Math.max(0, readLayerNumber(spec.emissiveIntensity, ['base'], 1.0)),",
        "    opacity: clamp01(readLayerNumber(spec.opacity, ['base'], 1)),",
        "    transparent: readLayerNumber(spec.transmission, ['base', 'amount'], 0) > 0 || readLayerNumber(spec.opacity, ['base'], 1) < 1,",
        "    alphaTest: Math.max(0, readLayerNumber(spec.alpha, ['cutoff', 'alphaTest'], 0)),",
        "    wireframe: options.wireframe ?? false,",
        "    side: spec.doubleSided === true ? THREE.DoubleSide : THREE.FrontSide,",
        # Faceted shading is a look, not an optimisation: three.js implements it by giving every
        # face its own normals, which needs unshared vertices, so the geometry has to be
        # non-indexed and the vertex count triples (3 per face). It is opt-in per material for
        # that reason -- turning it on globally would silently spend the budget the
        # `performanceBudget.targetTriangles` tier just bought back.
        "    flatShading: spec.flatShading === true,",
        "  });",
        "  if (textures) {",
        "    material.map = textures.albedo;",
        "    material.roughnessMap = textures.roughness;",
        "    material.normalMap = textures.normal;",
        "    material.normalScale.setScalar(Math.max(0.05, readLayerNumber(spec.normal, ['strength', 'amplitude'], 0.35)));",
        "    material.aoMap = textures.ao;",
        "    material.aoMap.channel = 0;",
        "    material.aoMapIntensity = readLayerNumber(spec.ambientOcclusion, ['cavityStrength', 'strength'], 0.35);",
        "    const denseMesh = denseComponent || spec.denseMesh === true || spec.geometryDensity === 'dense' || spec.topologyClass === 'dense';",
        "    const bumpScale = Math.max(0, readLayerNumber(spec.bump, ['amplitude', 'strength'], 0));",
        "    const effectiveBumpScale = denseMesh ? Math.max(0.05, bumpScale) : bumpScale;",
        "    if (effectiveBumpScale > 0) {",
        "      material.bumpMap = textures.height;",
        "      material.bumpScale = effectiveBumpScale;",
        "    }",
        "    const displacementScale = Math.max(0, readLayerNumber(spec.displacement, ['amplitude', 'strength'], 0));",
        "    const effectiveDisplacementScale = denseMesh ? Math.max(0.005, displacementScale) : displacementScale;",
        "    if (effectiveDisplacementScale > 0) {",
        "      material.displacementMap = textures.height;",
        "      material.displacementScale = effectiveDisplacementScale;",
        "      material.displacementBias = -effectiveDisplacementScale * 0.5;",
        "    }",
        "  }",
        "  material.envMapIntensity = readLayerNumber(spec, ['envMapIntensity'], 0.8);",
        "  material.userData.sculptMaterial = spec;",
        "  material.userData.proceduralMapsIndependent = true;",
        "  material.userData.pbrConstraints = { albedoRange: [30, 240], binaryMetalness: true, f0Range: [0.02, 1], iorRange: [1, 2.5] };",
        "  material.userData.pbrTextureSource = textures?.source ?? 'flat-fallback';",
        "  material.userData.referencePbr = spec.referencePbr ?? null;",
        "  material.userData.referenceMaterialId = spec.referenceMaterialId ?? spec.materialReference?.profileId ?? null;",
        "  material.userData.materialEvidence = spec.materialEvidence ?? null;",
        "  material.userData.validationViews = spec.materialReference?.validationViews ?? [];",
        "  material.needsUpdate = true;",
        "  return material;",
        "}",
        "",
        "type AttachmentEndpoint = {",
        "  start: THREE.Vector3;",
        "  midpoint: THREE.Vector3;",
        "  quaternion: THREE.Quaternion;",
        "  length: number;",
        "  baseRadius: number;",
        "  endRadius: number;",
        "};",
        "",
        "function readVector3(value: unknown, fallback: [number, number, number]): THREE.Vector3 {",
        "  if (Array.isArray(value) && value.length === 3 && value.every((item) => typeof item === 'number')) {",
        "    return new THREE.Vector3(value[0], value[1], value[2]);",
        "  }",
        "  return new THREE.Vector3(fallback[0], fallback[1], fallback[2]);",
        "}",
        "",
        "function readNumber(value: unknown, fallback: number): number {",
        "  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;",
        "}",
        "",
        "function makeAttachmentEndpoint(attachment: unknown): AttachmentEndpoint | null {",
        "  if (!attachment || typeof attachment !== 'object') return null;",
        "  const record = attachment as Record<string, unknown>;",
        "  const start = readVector3(record.localStart, [0, 0, 0]);",
        "  const end = readVector3(record.localEnd, [0, 1, 0]);",
        "  const delta = end.clone().sub(start);",
        "  const length = delta.length();",
        "  if (length <= 0.0001) return null;",
        "  const direction = delta.clone().normalize();",
        "  const quaternion = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction);",
        "  const baseRadius = Math.max(0.005, readNumber(record.baseRadius, 0.06));",
        "  const endRadius = Math.max(0.003, readNumber(record.endRadius, baseRadius * 0.55));",
        "  return {",
        "    start,",
        "    midpoint: delta.multiplyScalar(0.5),",
        "    quaternion,",
        "    length,",
        "    baseRadius,",
        "    endRadius,",
        "  };",
        "}",
        "",
        f"// Generated from ObjectSculptSpec target: {target}",
        f"// Sculpt build pass: {pass_id}",
        "// This factory is intentionally pass-gated. Finish browser screenshot review before unlocking deeper passes.",
        f"export function {function_name}(options: ProceduralModelOptions = {{}}): THREE.Group {{",
        "  const root = new THREE.Group();",
        f"  root.name = {json.dumps(target)};",
        f"  root.userData.reconstructionEvidence = {json_literal(reconstruction_evidence)};",
        f"  root.userData.materialPipeline = {json_literal(spec.get('materialPipeline', {}))};",
        f"  root.userData.materialReferenceRegistry = {json_literal(spec.get('materialReference', spec.get('materialPipeline', {}).get('registry') if isinstance(spec.get('materialPipeline'), dict) else None))};",
        "",
        "  const materialMap: Record<string, THREE.Material> = {};",
        ]
    )
    for material_id, material in materials.items():
        lines.extend(
            [
                f"  materialMap[{json.dumps(material_id)}] = createSculptMaterial(",
                f"    {json.dumps(material_id)},",
                f"    {json_literal(material)},",
                "    options",
                "  );",
            ]
        )
    lines.extend(
        [
            "",
            "  const nodes: Record<string, THREE.Object3D> = { root };",
            "  const meshes: Record<string, THREE.Mesh> = {};",
            "  const sockets: Record<string, THREE.Object3D> = {};",
            "  const colliders: Record<string, unknown> = {};",
            "  const destructionGroups: Record<string, THREE.Object3D[]> = {};",
        ]
    )

    # Components that get a bone need to be SkinnedMesh from the moment they are constructed --
    # a plain THREE.Mesh cannot be promoted to one after the fact. On the pivot track this set is
    # empty, so every component stays a THREE.Mesh and the emitted output is unchanged.
    skinned_ids = (
        {str(b.get("component") or b.get("id"))
         for b in spec["rig"]["bones"] if isinstance(b, dict) and b.get("id")}
        if rig_is_bone_track(spec) else set()
    )

    for index, component in enumerate(components):
        component_id = str(component.get("id") or f"component-{index}")
        component_var = local_var("mesh", component_id, index)
        node_var = local_var("node", component_id, index)
        primitive = str(component.get("primitive") or "box")
        if primitive not in VALID_PRIMITIVES:
            primitive = "box"
        transform = component.get("transform", {}) if isinstance(component.get("transform"), dict) else {}
        action_profile = component.get("actionProfile") if isinstance(component.get("actionProfile"), dict) else {}
        sockets_spec = action_profile.get("sockets", []) if isinstance(action_profile.get("sockets"), list) else []
        destruction = action_profile.get("destruction") if isinstance(action_profile.get("destruction"), dict) else {}
        fracture_group = destruction.get("fractureGroup") if isinstance(destruction, dict) else None
        attachment = component.get("attachment") if isinstance(component.get("attachment"), dict) else None
        attachment_var = local_var("attachment", component_id, index)
        endpoint_var = local_var("endpoint", component_id, index)
        material_id = str(component.get("material") or next(iter(materials.keys()), "base"))
        component_material = materials.get(material_id)
        material_expression = (
            f"createSculptMaterial({json.dumps(material_id)}, {json_literal(component_material)}, options, true)"
            if component_uses_dense_height_maps(component) and component_material is not None
            else f"materialMap[{json.dumps(material_id)}] ?? new THREE.MeshStandardMaterial({{ color: 0x888888 }})"
        )
        parent = component.get("parent") or "root"
        name = str(component.get("name") or component_id)
        descriptor = component.get("geometryDescriptor")
        geometry_descriptor = descriptor if isinstance(descriptor, dict) else {}
        sdf = descriptor.get("sdf") if isinstance(descriptor, dict) else None
        visual_hull = descriptor.get("visualHull") if isinstance(descriptor, dict) else None
        is_implicit = component.get("topologyClass") == "implicit" and isinstance(sdf, dict)
        is_visual_hull = isinstance(visual_hull, dict)
        iterations = subdivision_iterations(component)
        effective_primitive = resolve_instanced_cluster_base(primitive, geometry_descriptor, VALID_PRIMITIVES)
        if is_visual_hull:
            base_geometry_lines = [f"    const geometry = buildVisualHullGeometry({json_literal(visual_hull)});"]
        elif is_implicit:
            # An SDF's density comes from its sampling grid, not from segment counts, so the
            # tessellation tier has to reach it separately or it stays the one uncapped source
            # of triangles in a budgeted model. Marching-cubes output grows with the SURFACE it
            # crosses, so triangles scale as O(N^2), not O(N^3) -- measured on a capsule field:
            # N 64 -> 96 -> 128 gave 3,432 -> 7,840 -> 13,960 crossing cells, matching N^2
            # (1.78, 2.25, 1.78 against predicted 1.78, 2.25, 1.78). Halving N therefore buys
            # back about 4x, which is why a cap rather than a rewrite is enough here.
            base_geometry_lines = [
                f"    const geometry = polygonizeSdf({json_literal(capped_sdf(sdf, seg))});"
            ]
        else:
            geometry_expression = (
                "new THREE.BoxGeometry(1, 1, 1)"
                if iterations > 0 and effective_primitive == "box"
                else geometry_for(primitive, component, iterations > 0, seg)
            )
            base_geometry_lines = [
                f"    const geometry = {endpoint_var}",
                f"      ? new THREE.CylinderGeometry({endpoint_var}.endRadius, {endpoint_var}.baseRadius, {endpoint_var}.length, {seg['ATTACHMENT_CYLINDER_RADIAL_SEGMENTS']}, {seg['ATTACHMENT_CYLINDER_HEIGHT_SEGMENTS']})",
                f"      : {geometry_expression};",
            ]
        if iterations > 0:
            geometry_lines = [
                f"  const {component_var}Geometry = (() => {{",
                *base_geometry_lines,
                f"    return subdivideCatmullClark(geometry, {iterations});",
                "  })();",
            ]
        elif is_visual_hull:
            geometry_lines = [f"  const {component_var}Geometry = buildVisualHullGeometry({json_literal(visual_hull)});"]
        elif is_implicit:
            geometry_lines = [
                f"  const {component_var}Geometry = polygonizeSdf({json_literal(capped_sdf(sdf, seg))});"
            ]
        else:
            geometry_lines = [
                f"  const {component_var}Geometry = {endpoint_var}",
                f"    ? new THREE.CylinderGeometry({endpoint_var}.endRadius, {endpoint_var}.baseRadius, {endpoint_var}.length, {seg['ATTACHMENT_CYLINDER_RADIAL_SEGMENTS']}, {seg['ATTACHMENT_CYLINDER_HEIGHT_SEGMENTS']})",
                f"    : {geometry_for(primitive, component, False, seg)};",
            ]
        ratio = decimate_ratio(component)
        if ratio is not None:
            # Renaming the dense result and binding the decimated one to the name everything
            # downstream already uses keeps this a single insertion: mesh construction, the
            # skin-weight pass and every gate go on reading `<component>Geometry`, and the
            # decimation lands before any of them. In particular it lands before the bind pass
            # recomputes skinIndex/skinWeight from `position`, so no skinning data is ever
            # carried across a vertex merge.
            dense_var = f"{component_var}GeometryDense"
            geometry_lines[0] = geometry_lines[0].replace(
                f"const {component_var}Geometry =", f"const {dense_var} =", 1
            )
            geometry_lines.append(
                f"  const {component_var}Geometry = decimateGeometry({dense_var}, {ratio});"
            )
        lines.extend(
            [
                "",
                *(
                    [f"  const {attachment_var} = {json.dumps(attachment, ensure_ascii=False)};"]
                    if primitive in ATTACHMENT_PRIMITIVES and not is_implicit
                    else []
                ),
                # Endpoint-DERIVED GEOMETRY only for primitives that are attachment shapes.
                #
                # An `attachment` block is a contract -- parent socket, contact type, embed depth,
                # local start and end -- and the structural pass requires one on every child
                # appendage. It is NOT a statement that the part is a tapered cylinder. Deriving
                # geometry from it unconditionally silently overrode the authored primitive: an
                # ellipsoid head, a tapered-sweep ear and a swept tail all came out as cylinders
                # between their two endpoints, the spec validated, the factory built, and the
                # wrong shape shipped. That is the same failure mode `GeometryNotImplementedError`
                # exists to prevent one step earlier.
                #
                # So the branch is gated on ATTACHMENT_PRIMITIVES. Every other primitive keeps the
                # geometry it declared and uses the attachment as what it is: a contract the
                # anchor gate reads.
                (
                    f"  const {endpoint_var} = makeAttachmentEndpoint({attachment_var});"
                    # `not is_implicit` as well as the primitive test. An implicit component's
                    # geometry comes from the SDF and its placement from its own transform, but it
                    # still has to declare SOME `primitive` -- the shipped fixture uses "capsule" --
                    # and that lands it in ATTACHMENT_PRIMITIVES. Taking the endpoint branch then
                    # moved the node to `attachment.localStart`: a head authored at y 0.705 was
                    # emitted at y -0.195, inside the body, and the model rendered with no head at
                    # all.
                    if primitive in ATTACHMENT_PRIMITIVES and not is_implicit
                    # `makeAttachmentEndpoint(null)` rather than a bare `null`, so the
                    # binding keeps its `AttachmentEndpoint | null` type. A literal null narrows to
                    # `never` and every later `endpoint.start` stops compiling.
                    else f"  const {endpoint_var} = makeAttachmentEndpoint(null);"
                ),
                f"  const {node_var} = new THREE.Group();",
                f"  {node_var}.name = {json.dumps(name + '__pivot')};",
                # PLAN_1.5 WS-E: the hierarchy transform (this pivot Group) never carries
                # non-uniform scale. A part's shape dimensions are baked directly into its
                # geometry (see `{component_var}Geometry.scale(...)` below) instead of onto
                # this node, so a non-uniform component cannot distort a child parented
                # under it. Position/rotation still cascade normally through the Group chain.
                f"  {node_var}.scale.set(1, 1, 1);",
                f"  if ({endpoint_var}) {{",
                f"    {node_var}.position.copy({endpoint_var}.start);",
                # The pivot sits AT the joint and the limb's own direction lives on the mesh
                # inside it (mesh.quaternion = endpoint.quaternion), so rotating this node
                # rotates the limb about its joint -- which is exactly what a pose is. This used
                # to be forced to (0,0,0), which silently discarded every authored rotation on
                # precisely the components that articulate: every capsule/cylinder limb carries
                # an attachment block and so takes this branch. `anatomy.pose.jointAngles` had
                # no observable effect until this line stopped zeroing it.
                f"    {node_var}.rotation.set({vector(transform.get('rotation'), [0, 0, 0])});",
                "  } else {",
                f"    {node_var}.position.set({vector(transform.get('position'), [0, 0, 0])});",
                f"    {node_var}.rotation.set({vector(transform.get('rotation'), [0, 0, 0])});",
                "  }",
                f"  {node_var}.userData.sculptComponent = {json.dumps(component, ensure_ascii=False)};",
                f"  {node_var}.userData.actionProfile = {json.dumps(action_profile, ensure_ascii=False)};",
                f"  (nodes[{json.dumps(str(parent))}] ?? root).add({node_var});",
                f"  nodes[{json.dumps(component_id)}] = {node_var};",
                *geometry_lines,
                # Attachment geometry (the endpoint branch above) already derives its exact
                # radius/length from the measured endpoints, so it is not re-scaled here.
                # Every other primitive is authored unit-sized in geometry_for(); its real
                # dimensions are applied to the vertex data now, not to the pivot's .scale.
                f"  if (!{endpoint_var}) {{",
                f"    {component_var}Geometry.scale({scale_vector(component, transform)});",
                "  }",
                # After the scale, so the ramp spans the mass's real extent rather than its unit
                # extent, and before the mesh, so the colour attribute exists when the material
                # reads it.
                *(
                    [
                        f"  applyRootTipGradient({component_var}Geometry, "
                        f"{json.dumps(gradient['rootColor'])}, {json.dumps(gradient['tipColor'])}, "
                        f"{json.dumps(gradient['axis'])});"
                    ]
                    if (gradient := root_tip_gradient(component))
                    else []
                ),
                *(
                    [
                        f"  applyVertexPaint({component_var}Geometry, "
                        f"{json.dumps(paint['baseColor'])}, "
                        f"{json.dumps(paint['regions'], ensure_ascii=False)});"
                    ]
                    if (paint := vertex_paint(component))
                    else []
                ),
                f"  const {component_var} = new THREE."
                f"{'SkinnedMesh' if component_id in skinned_ids else 'Mesh'}(",
                f"    {component_var}Geometry,",
                f"    {material_expression}",
                "  );",
                f"  {component_var}.name = {json.dumps(name)};",
                # Cloned before enabling vertex colours: materials are shared by id, and flipping
                # the flag in place would tint every other component using the same material.
                *(
                    [
                        f"  {component_var}.material = {component_var}.material.clone();",
                        f"  {component_var}.material.vertexColors = true;",
                    ]
                    if root_tip_gradient(component) or vertex_paint(component)
                    else []
                ),
                # With vertexColors on, three MULTIPLIES material.color by the vertex colour. A
                # paint block already carries the component's full albedo -- its own baseColor for
                # the unpainted surface and a region colour where a region claims it -- so leaving
                # the material's albedo in place squares it. Measured: a 0.027 linear black fur
                # against a 0.027 black material renders at 0.0007, and the white sock authored at
                # 0.937 comes out at 0.025, i.e. darker than the fur is supposed to be. The region
                # boundary is still exactly where the gate measures it, and is invisible.
                #
                # Only for `vertexPaint`. `rootTipGradient` is a SHADING ramp that is meant to
                # modulate the material's own colour, so it keeps the multiply.
                *(
                    # Cast: `Mesh.material` is typed `Material | Material[]`, and `color`
                    # lives on the concrete material rather than on the base class. `vertexColors`
                    # above needs no cast because it IS on `Material`.
                    [
                        f"  ({component_var}.material as THREE.MeshPhysicalMaterial)"
                        ".color.setRGB(1, 1, 1);"
                    ]
                    if vertex_paint(component)
                    else []
                ),
                f"  if ({endpoint_var}) {{",
                f"    {component_var}.position.copy({endpoint_var}.midpoint);",
                f"    {component_var}.quaternion.copy({endpoint_var}.quaternion);",
                "  }",
                f"  {component_var}.castShadow = options.castShadow ?? true;",
                f"  {component_var}.receiveShadow = options.receiveShadow ?? true;",
                f"  {component_var}.userData.sculptComponent = {json.dumps(component, ensure_ascii=False)};",
                f"  {node_var}.add({component_var});",
                f"  meshes[{json.dumps(component_id)}] = {component_var};",
                f"  colliders[{json.dumps(component_id)}] = {json.dumps(action_profile.get('collider', {}), ensure_ascii=False)};",
            ]
        )
        if isinstance(fracture_group, str) and fracture_group:
            lines.extend(
                [
                    f"  destructionGroups[{json.dumps(fracture_group)}] ??= [];",
                    f"  destructionGroups[{json.dumps(fracture_group)}].push({node_var});",
                ]
            )
        for socket_index, socket in enumerate(sockets_spec):
            if not isinstance(socket, dict):
                continue
            socket_id = str(socket.get("id") or f"socket-{socket_index}")
            socket_var = local_var("socket", f"{component_id}_{socket_id}", socket_index)
            local_position = socket.get("localPosition", socket.get("position"))
            local_rotation = socket.get("localRotation", socket.get("rotation"))
            socket_key = f"{component_id}:{socket_id}"
            lines.extend(
                [
                    f"  const {socket_var} = new THREE.Object3D();",
                    f"  {socket_var}.name = {json.dumps(socket_id)};",
                    f"  {socket_var}.position.set({vector(local_position, [0, 0, 0])});",
                    f"  {socket_var}.rotation.set({vector(local_rotation, [0, 0, 0])});",
                    f"  {socket_var}.userData.socket = {json.dumps(socket, ensure_ascii=False)};",
                    f"  {node_var}.add({socket_var});",
                    f"  sockets[{json.dumps(socket_key)}] = {socket_var};",
                ]
            )

    # Repetition systems (spokes, fasteners, teeth, slats): the spec models these
    # as a count + placement + instanceScale rather than N hand-authored components.
    # They are emitted here, parented to the referenced node, and pass-gated by the
    # same macro/meso/micro levels as componentTree so blockout stays clay-macro.
    allowed_levels = PASS_LEVELS.get(pass_id, {"macro"})
    known_ids = {str(c.get("id")) for c in all_components if isinstance(c, dict)}
    # Ids that already exist as built geometry: components in their own right, plus the SDF
    # primitives that make up an implicit component. A repetition system naming these is describing
    # parts that have already been authored, not asking for new ones.
    realised_ids = set(known_ids)
    for component in all_components:
        if not isinstance(component, dict):
            continue
        descriptor = component.get("geometryDescriptor")
        sdf = descriptor.get("sdf") if isinstance(descriptor, dict) else None
        for primitive in (sdf or {}).get("primitives", []) or []:
            if isinstance(primitive, dict) and primitive.get("id"):
                realised_ids.add(str(primitive["id"]))

    for rep_index, system in enumerate(spec.get("repetitionSystems", [])):
        if not isinstance(system, dict):
            continue
        # A repetition system whose members are ALL already built is documentation, not an
        # instruction, and instancing it emits a second copy of parts that exist.
        #
        # The tuxedo cat declares limb-set, whisker-set and toe-set with elementComponentIds naming
        # the four legs (SDF primitives inside the fused body), the eight whiskers and the six toes
        # (each an authored component that already emits its own mesh), and a PROSE placement —
        # "three per front paw at x offsets -0.040, 0.0, +0.040" — rather than a {mode, axis,
        # radius} object. The emitter below reads that string as an empty dict and falls through to
        # its defaults, so each system came out as an InstancedMesh of unit BOXES centred on the
        # origin, spanning -0.5..0.5 on every axis.
        #
        # That is not merely ugly. geometry.json is measured by run_geometry_gates.py, and the
        # boxes dragged the model's minimum Y from 0.0012 to -0.5 — half a unit underground — so
        # belly-clearance-over-torso-depth was computed against a ground line that does not exist
        # and "failed" for a reason that had nothing to do with the belly.
        #
        # Skipping requires ALL members to be realised, so a genuine instancing system — whose
        # members are deliberately not authored one by one — still emits.
        element_ids = [str(e) for e in (system.get("elementComponentIds") or []) if e]
        if element_ids and all(e in realised_ids for e in element_ids):
            lines.append(
                f"  // repetition system {json.dumps(str(system.get('id') or f'rep_{rep_index}'))} "
                f"describes {len(element_ids)} parts that are already built individually; "
                "not instanced."
            )
            continue
        level = str(system.get("level") or "meso")
        if level not in allowed_levels:
            continue
        parent_id = str(system.get("parent") or "root")
        if parent_id not in known_ids and parent_id != "root":
            parent_id = "root"
        placement = system.get("placement", {}) if isinstance(system.get("placement"), dict) else {}
        count = int(system.get("count") or 0)
        if count <= 0:
            continue
        primitive = str(system.get("primitive") or "box")
        if primitive not in VALID_PRIMITIVES:
            primitive = "box"
        rep_material = str(system.get("material") or next(iter(materials.keys()), "base"))
        scale = system.get("instanceScale", [0.1, 0.1, 0.1])
        axis = placement.get("axis", [0, 0, 1])
        radius = placement.get("radius", 0.0)
        start_deg = placement.get("startAngleDeg", 0)
        mode = str(placement.get("mode") or "radial")
        rep_var = f"rep_{rep_index}"
        lines.extend(
            [
                "",
                f"  // repetition system: {system.get('id') or rep_var} (InstancedMesh, {mode}, count={count}, level={level})",
                "  {",
                f"    const parent = nodes[{json.dumps(parent_id)}] ?? root;",
                f"    const geo = {geometry_for(primitive, {}, False, seg)};",
                f"    const mat = materialMap[{json.dumps(rep_material)}] ?? new THREE.MeshStandardMaterial({{ color: 0x888888 }});",
                "    // Contract (PLAN_1.5 WS-E): instanceScale is ABSOLUTE, in the parent pivot's",
                "    // local units -- it is never multiplied by the parent component's own declared",
                "    // dimensional scale. This falls out of the same fix as componentTree: the pivot",
                "    // Group this cluster is parented to always carries identity scale (dimensions are",
                "    // baked into that component's OWN geometry, not exposed on the Group), so an",
                "    // instanced fastener/tooth/spoke sized [0.05, 0.05, 0.05] renders at exactly that",
                "    // size regardless of how non-uniformly its host component is shaped, and a",
                "    // `radial` ring's placement stays circular instead of being squashed into an",
                "    // ellipse by a non-uniform host.",
                f"    const scl = [{vector(scale, [0.1, 0.1, 0.1])}];",
                f"    const axis = new THREE.Vector3({vector(axis, [0, 0, 1])}).normalize();",
                f"    const radius = {float(radius) if isinstance(radius, (int, float)) else 0.0};",
                "    const seed = Math.abs(axis.z) < 0.9 ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(1, 0, 0);",
                "    const perp = new THREE.Vector3().crossVectors(axis, seed).normalize();",
                "    // One InstancedMesh = one draw call for all repeated parts (teeth/fasteners/spokes),",
                "    // replacing the former per-instance Mesh clone loop (real-time perf principle).",
                f"    const cluster = new THREE.InstancedMesh(geo, mat, {count});",
                "    const _m = new THREE.Matrix4();",
                "    const _p = new THREE.Vector3();",
                "    const _q = new THREE.Quaternion();",
                "    const _s = new THREE.Vector3(scl[0], scl[1], scl[2]);",
                f"    for (let i = 0; i < {count}; i++) {{",
                f"      const ang = (({float(start_deg) if isinstance(start_deg,(int,float)) else 0.0}) + (i * 360) / {count}) * Math.PI / 180;",
                "      const dir = perp.clone().applyQuaternion(new THREE.Quaternion().setFromAxisAngle(axis, ang));",
                "      _p.copy(radius > 0 ? dir.clone().multiplyScalar(radius * 0.5) : new THREE.Vector3());",
                "      _q.setFromUnitVectors(new THREE.Vector3(1, 0, 0), dir);",
                "      _m.compose(_p, _q, _s);",
                "      cluster.setMatrixAt(i, _m);",
                "    }",
                "    cluster.instanceMatrix.needsUpdate = true;",
                "    cluster.castShadow = options.castShadow ?? true;",
                "    cluster.receiveShadow = options.receiveShadow ?? true;",
                f"    cluster.name = {json.dumps(str(system.get('id') or rep_var))};",
                "    parent.add(cluster);",
                "  }",
            ]
        )

    # Deferred to here, after every node is parented, because the march runs in the TARGET's frame
    # and needs both world matrices. Running it at each component's own emission site would depend
    # on the target already existing, which makes a legal forward reference silently do nothing.
    if stand_proud_jobs or stand_proud_skipped:
        lines.append("")
        lines.append("  // standProud: hold these components outside the surfaces they cover.")
        for note in stand_proud_skipped:
            lines.append(f"  // SKIPPED {note}")
        for job in stand_proud_jobs:
            marcher = json.dumps(job["componentId"])
            proud_target_key = json.dumps(job["targetId"])
            lines.extend([
                f"  if (meshes[{marcher}] && nodes[{proud_target_key}]) {{",
                "    applyStandProud(",
                f"      meshes[{marcher}].geometry,",
                f"      meshes[{marcher}],",
                f"      nodes[{proud_target_key}],",
                f"      {json_literal({'rings': job['rings']})},",
                f"      {job['clearance']},",
                f"      {job['maxPush']},",
                "    );",
                "  }",
            ])

    lines.extend(emit_rig_hierarchy(spec))

    look_dev_targets = spec.get("lookDevTargets", {})
    lighting_from_photo = spec.get("lightingFromPhoto", [])
    lines.extend(
        [
            "",
            "  root.userData.sculptRuntime = { nodes, meshes, sockets, colliders, destructionGroups } satisfies ProceduralModelRuntime;",
            f"  root.userData.lookDevTargets = {json_literal(look_dev_targets)};",
            "  root.userData.actionReadiness = {",
            "    note: 'Use root.userData.sculptRuntime.nodes for transforms, sockets for attachments, colliders for physics proxies, and destructionGroups for breakable sets.',",
            "  };",
            "  return root;",
            "}",
            "",
            f"export function create{type_name}LookDevLights(",
            "  mode: 'neutral' | 'grazing' | 'reference' = 'neutral',",
            "): THREE.Group {",
            "  const lights = new THREE.Group();",
            f"  lights.name = {json.dumps(target + ' look-dev lights')};",
            "  const hemi = new THREE.HemisphereLight(",
            "    mode === 'reference' ? 0xfff0d6 : 0xf2f4ff,",
            "    0x363b42,",
            "    mode === 'grazing' ? 0.28 : mode === 'reference' ? 0.72 : 0.85,",
            "  );",
            "  lights.add(hemi);",
            "  const key = new THREE.DirectionalLight(",
            "    mode === 'reference' ? 0xffcf8a : 0xfff4e8,",
            "    mode === 'grazing' ? 4.2 : mode === 'reference' ? 2.6 : 2.15,",
            "  );",
            "  if (mode === 'grazing') key.position.set(7.5, 1.1, 4.0);",
            "  else if (mode === 'reference') key.position.set(-4.5, 7.5, 5.0);",
            "  else key.position.set(-4.0, 6.0, 5.5);",
            "  key.castShadow = true;",
            "  key.shadow.mapSize.set(4096, 4096);",
            "  key.shadow.bias = -0.00025;",
            "  key.shadow.normalBias = 0.018;",
            "  key.shadow.radius = 7;",
            "  key.shadow.blurSamples = 24;",
            "  key.shadow.camera.near = 0.5;",
            "  key.shadow.camera.far = 30;",
            "  key.shadow.camera.left = -2.6;",
            "  key.shadow.camera.right = 2.6;",
            "  key.shadow.camera.top = 2.6;",
            "  key.shadow.camera.bottom = -2.6;",
            "  key.shadow.camera.updateProjectionMatrix();",
            "  lights.add(key);",
            "  const fill = new THREE.DirectionalLight(0xa8c4ff, mode === 'grazing' ? 0.12 : 0.42);",
            "  fill.position.set(4.0, 3.0, 3.5);",
            "  lights.add(fill);",
            "  const rim = new THREE.DirectionalLight(0xfff1c4, mode === 'grazing' ? 0.28 : 0.85);",
            "  rim.position.set(0.5, 4.5, -6.0);",
            "  lights.add(rim);",
            "  lights.userData.reviewMode = mode;",
            f"  lights.userData.lightingFromPhoto = {json_literal(lighting_from_photo)};",
            f"  lights.userData.lookDevTargets = {json_literal(look_dev_targets)};",
            "  return lights;",
            "}",
            "",
            "// PBR materials (clearcoat/iridescence/transmission/anisotropy) need an environment",
            "// map to visually behave as intended — call this once per renderer and assign the",
            "// result to scene.environment before rendering. No external HDR asset required.",
            f"export function create{type_name}Environment(renderer: THREE.WebGLRenderer): THREE.Texture {{",
            "  const pmrem = new THREE.PMREMGenerator(renderer);",
            "  const texture = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;",
            "  pmrem.dispose();",
            "  return texture;",
            "}",
            "",
            "// Plan 1.3 §3.2 — auto-framing by bounding box. The Divine Eye can only compare a",
            "// render to the reference if the object is FRAMED consistently (an object framed",
            "// differently scores as wrong even when its shape is right). This positions the camera",
            "// deterministically from the object's bounding box so it fills the frame at a stable",
            "// margin, and sets near/far to the object scale. Call after adding the model to the",
            "// scene, and again on resize (after updating camera.aspect).",
            f"export function frame{type_name}Camera(",
            "  camera: THREE.PerspectiveCamera,",
            "  object: THREE.Object3D,",
            "  options: { margin?: number; azimuthDeg?: number; elevationDeg?: number } = {},",
            "): void {",
            "  const box = new THREE.Box3().setFromObject(object);",
            "  if (box.isEmpty()) return;",
            "  const size = box.getSize(new THREE.Vector3());",
            "  const center = box.getCenter(new THREE.Vector3());",
            "  const margin = options.margin ?? 1.15;",
            "  const maxDim = Math.max(size.x, size.y, size.z) * margin;",
            "  const fov = (camera.fov * Math.PI) / 180;",
            "  // distance so the largest object dimension fits vertically in the frame",
            "  const distance = (maxDim / 2) / Math.tan(fov / 2);",
            "  const az = ((options.azimuthDeg ?? 0) * Math.PI) / 180;",
            "  const el = ((options.elevationDeg ?? 0) * Math.PI) / 180;",
            "  const dir = new THREE.Vector3(",
            "    Math.sin(az) * Math.cos(el),",
            "    Math.sin(el),",
            "    Math.cos(az) * Math.cos(el),",
            "  );",
            "  camera.position.copy(center).addScaledVector(dir, distance);",
            "  camera.near = Math.max(0.01, distance - maxDim);",
            "  camera.far = distance + maxDim * 2;",
            "  camera.lookAt(center);",
            "  camera.updateProjectionMatrix();",
            "}",
            "",
            "// Plan 1.3 §3.2c — PRESENTATION composer (DOF + bloom). CRITICAL (R-POSTFX): this is",
            "// for the showcase/hero render ONLY. The Divine Eye's EVALUATION render MUST use a",
            "// plain renderer with NO composer — bloom blows highlights and DOF blurs edges, which",
            "// would corrupt the deterministic IoU/DCD/edge/blowout signals. Enable dof/bloom ONLY",
            "// when the reference photo actually exhibits them (detect_reference_effects.py authorizes).",
            f"export function create{type_name}PresentationComposer(",
            "  renderer: THREE.WebGLRenderer,",
            "  scene: THREE.Scene,",
            "  camera: THREE.Camera,",
            "  options: { dof?: boolean; bloom?: boolean; bloomStrength?: number; dofFocus?: number; dofAperture?: number } = {},",
            "): EffectComposer {",
            "  const composer = new EffectComposer(renderer);",
            "  composer.addPass(new RenderPass(scene, camera));",
            "  if (options.dof) {",
            "    composer.addPass(new BokehPass(scene, camera, {",
            "      focus: options.dofFocus ?? 10.0,",
            "      aperture: options.dofAperture ?? 0.0002,",
            "      maxblur: 0.01,",
            "    }));",
            "  }",
            "  if (options.bloom) {",
            "    const size = new THREE.Vector2();",
            "    renderer.getSize(size);",
            "    composer.addPass(new UnrealBloomPass(size, options.bloomStrength ?? 0.4, 0.4, 0.85));",
            "  }",
            "  return composer;",
            "}",
            "",
            f"export function configure{type_name}Renderer(renderer: THREE.WebGLRenderer): void {{",
            "  // Load-bearing for view-dependent finishes (anodized / Doppler): without ACES + sRGB",
            "  // the environment reflection reads flat/washed instead of a believable metal response.",
            "  renderer.toneMapping = THREE.ACESFilmicToneMapping;",
            "  renderer.outputColorSpace = THREE.SRGBColorSpace;",
            "}",
            "",
            f"export function create{type_name}InspectControls(",
            "  camera: THREE.Camera,",
            "  domElement: HTMLElement,",
            "): OrbitControls {",
            "  // View-dependent finishes only read correctly once the user orbits — their color",
            "  // comes from the environment reflection, not albedo, so free rotation matters here.",
            "  const controls = new OrbitControls(camera, domElement);",
            "  controls.enableDamping = true;",
            "  controls.minDistance = 1.0;",
            "  controls.maxDistance = 8.0;",
            "  controls.autoRotate = false;",
            "  return controls;",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--pass-id",
        help="Build pass to generate. Defaults to the current unlocked sculptPipeline pass.",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--allow-nonstrict",
        action="store_true",
        help="Test-fixture escape hatch only; emits non-production code and cannot be used with --pass-id.",
    )
    parser.add_argument(
        "--blocked-report",
        type=Path,
        help="Write the machine-readable BLOCKED report when strict-quality prevents generation.",
    )
    args = parser.parse_args(argv)

    spec_path = args.spec.expanduser().resolve()
    spec = load_spec(spec_path)
    _errors, warnings, strict_failures = strict_quality_failures(spec)
    if strict_failures and not args.allow_nonstrict:
        emit_blocked(
            blocked_report(spec_path, strict_failures, warnings, args.pass_id),
            args.blocked_report,
        )
        return 2
    if args.allow_nonstrict and args.pass_id:
        parser.error("--allow-nonstrict is test-only and cannot be combined with --pass-id")
    if _errors:
        parser.error(f"spec validation failed: {'; '.join(_errors)}")
    if args.allow_nonstrict:
        print("WARNING: generating a non-production test-fixture factory (--allow-nonstrict)", file=sys.stderr)
    pass_id = args.pass_id or unlocked_pass(spec)
    try:
        assert_pass_unlocked(spec, pass_id)
    except ValueError as exc:
        parser.error(str(exc))
    gaps = pass_specific_gaps(spec, pass_id)
    if gaps:
        parser.error(f"build pass {pass_id!r} needs spec refinement: {'; '.join(gaps)}")
    output = args.out.expanduser().resolve()
    if output.exists() and not args.force:
        parser.error(f"{output} already exists; use --force to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        rendered = generate(spec, pass_id)
    except GeometryNotImplementedError as exc:
        parser.error(
            f"primitive {str(exc)!r} is accepted by validation but has no codegen "
            f"implementation yet in geometry_for() — refine-spec to a supported primitive "
            f"or implement it before generating this component"
        )
    output.write_text(rendered, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
