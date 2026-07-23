#!/usr/bin/env python3
"""Generate a TypeScript Three.js factory skeleton from an ObjectSculptSpec."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from orchestrate_passes import pass_specific_gaps


VALID_PRIMITIVES = {
    "box",
    "sphere",
    "ellipsoid",
    "cylinder",
    "cone",
    "capsule",
    "torus",
    "tube",
    "lathe",
    "extrude",
    "ground-blade",
    "curve-sweep",
    "plane-card",
    "instanced-cluster",
}
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
    "thickness": 0.05, "grindFrac": 0.55, "swedgeFromTipFrac": 0.34, "spineFlat": 0.30,
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


def geometry_for(primitive: str, component: dict[str, Any] | None = None) -> str:
    if primitive == "box":
        return "new THREE.BoxGeometry(1, 1, 1, 12, 12, 12)"
    if primitive in {"sphere", "ellipsoid"}:
        return "new THREE.SphereGeometry(0.5, 64, 40)"
    if primitive == "cylinder":
        return "new THREE.CylinderGeometry(0.5, 0.5, 1, 48, 16)"
    if primitive == "cone":
        return "new THREE.ConeGeometry(0.5, 1, 48, 16)"
    if primitive == "capsule":
        return "new THREE.CapsuleGeometry(0.35, 0.7, 16, 32)"
    if primitive == "torus":
        # Tube thickness is ring-relative (fraction of the 0.45 base radius) so a
        # component can be a slim bike tyre or a fat donut without changing scale.
        desc = component.get("geometryDescriptor") if isinstance(component, dict) and isinstance(component.get("geometryDescriptor"), dict) else {}
        tube_ratio = desc.get("torusTubeRatio")
        tube = 0.45 * float(tube_ratio) if isinstance(tube_ratio, (int, float)) and tube_ratio > 0 else 0.08
        return f"new THREE.TorusGeometry(0.45, {round(tube, 4)}, 24, 96)"
    if primitive == "plane-card":
        return "new THREE.PlaneGeometry(1, 1, 24, 24)"
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
    if primitive == "instanced-cluster":
        # An instanced cluster's *geometry* is its base shape; the instancing itself is applied
        # by the repetition-system emitter (THREE.InstancedMesh). Resolve the base primitive from
        # the descriptor (default box); guard against self-reference so we never recurse.
        base = descriptor.get("baseGeometry") if isinstance(descriptor.get("baseGeometry"), str) else "box"
        if base in ("instanced-cluster", "") or base not in VALID_PRIMITIVES:
            base = "box"
        return geometry_for(base, component)
    raise GeometryNotImplementedError(primitive)


def generate(spec: dict[str, Any], pass_id: str) -> str:
    target = str(spec.get("targetName") or "Procedural Object")
    type_name = pascal_case(target)
    function_name = f"create{type_name}Model"
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
    used_primitives = {str(component.get("primitive")) for component in components}
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
        "  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];",
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
        "  return [Number(match[1]), Number(match[2]), Number(match[3])];",
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
        "function createSculptMaterial(id: string, spec: SculptMaterialSpec, options: ProceduralModelOptions): THREE.MeshPhysicalMaterial {",
        "  const textures = makeReferenceTextureSet(spec, options) ?? makeProceduralTextureSet(id, spec, options);",
        "  const material = new THREE.MeshPhysicalMaterial({",
        "    color: textures ? 0xffffff : new THREE.Color(typeof spec.baseColor === 'string' ? spec.baseColor : '#8A7A5F'),",
        "    roughness: textures ? 1 : clamp01(readLayerNumber(spec.roughness, ['base'], 0.76)),",
        "    metalness: clamp01(readLayerNumber(spec.metalness, ['base'], 0.0)),",
        "    clearcoat: clamp01(readLayerNumber(spec.clearcoat, ['base', 'amount'], 0)),",
        "    clearcoatRoughness: clamp01(readLayerNumber(spec.clearcoatRoughness, ['base'], 0.25)),",
        "    transmission: clamp01(readLayerNumber(spec.transmission, ['base', 'amount'], 0)),",
        "    ior: Math.max(1, readLayerNumber(spec.ior, ['base', 'value'], 1.5)),",
        "    thickness: Math.max(0, readLayerNumber(spec.thickness, ['base', 'amount'], 0)),",
        "    attenuationDistance: Math.max(0.001, readLayerNumber(spec.attenuationDistance, ['base', 'value'], Infinity)),",
        "    attenuationColor: new THREE.Color(typeof spec.attenuationColor === 'string' ? spec.attenuationColor : '#ffffff'),",
        "    sheen: clamp01(readLayerNumber(spec.sheen, ['base', 'amount'], 0)),",
        "    sheenColor: new THREE.Color(typeof spec.sheenColor === 'string' ? spec.sheenColor : '#ffffff'),",
        "    sheenRoughness: clamp01(readLayerNumber(spec.sheenRoughness, ['base'], 1.0)),",
        "    iridescence: clamp01(readLayerNumber(spec.iridescence, ['base', 'amount'], 0)),",
        "    iridescenceIOR: Math.max(1, readLayerNumber(spec.iridescenceIOR, ['base', 'value'], 1.3)),",
        "    anisotropy: clamp01(readLayerNumber(spec.anisotropy, ['base', 'amount'], 0)),",
        "    anisotropyRotation: readLayerNumber(spec.anisotropy, ['rotation'], 0),",
        "    specularIntensity: clamp01(readLayerNumber(spec.specularIntensity, ['base'], 1.0)),",
        "    specularColor: new THREE.Color(typeof spec.specularColor === 'string' ? spec.specularColor : '#ffffff'),",
        "    emissive: new THREE.Color(typeof spec.emissive === 'string' ? spec.emissive : '#000000'),",
        "    emissiveIntensity: Math.max(0, readLayerNumber(spec.emissiveIntensity, ['base'], 1.0)),",
        "    opacity: clamp01(readLayerNumber(spec.opacity, ['base'], 1)),",
        "    transparent: readLayerNumber(spec.transmission, ['base', 'amount'], 0) > 0 || readLayerNumber(spec.opacity, ['base'], 1) < 1,",
        "    alphaTest: Math.max(0, readLayerNumber(spec.alpha, ['cutoff', 'alphaTest'], 0)),",
        "    wireframe: options.wireframe ?? false,",
        "    side: spec.doubleSided === true ? THREE.DoubleSide : THREE.FrontSide,",
        "  });",
        "  if (textures) {",
        "    material.map = textures.albedo;",
        "    material.roughnessMap = textures.roughness;",
        "    material.normalMap = textures.normal;",
        "    material.normalScale.setScalar(Math.max(0.05, readLayerNumber(spec.normal, ['strength', 'amplitude'], 0.35)));",
        "    material.aoMap = textures.ao;",
        "    material.aoMap.channel = 0;",
        "    material.aoMapIntensity = readLayerNumber(spec.ambientOcclusion, ['cavityStrength', 'strength'], 0.35);",
        "    const bumpScale = Math.max(0, readLayerNumber(spec.bump, ['amplitude', 'strength'], 0));",
        "    if (bumpScale > 0) {",
        "      material.bumpMap = textures.height;",
        "      material.bumpScale = bumpScale;",
        "    }",
        "    const displacementScale = Math.max(0, readLayerNumber(spec.displacement, ['amplitude', 'strength'], 0));",
        "    if (displacementScale > 0) {",
        "      material.displacementMap = textures.height;",
        "      material.displacementScale = displacementScale;",
        "      material.displacementBias = -displacementScale * 0.5;",
        "    }",
        "  }",
        "  material.envMapIntensity = readLayerNumber(spec, ['envMapIntensity'], 0.8);",
        "  material.userData.sculptMaterial = spec;",
        "  material.userData.proceduralMapsIndependent = true;",
        "  material.userData.pbrTextureSource = textures?.source ?? 'flat-fallback';",
        "  material.userData.referencePbr = spec.referencePbr ?? null;",
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
        parent = component.get("parent") or "root"
        name = str(component.get("name") or component_id)
        lines.extend(
            [
                "",
                f"  const {attachment_var} = {json.dumps(attachment, ensure_ascii=False)};",
                f"  const {endpoint_var} = makeAttachmentEndpoint({attachment_var});",
                f"  const {node_var} = new THREE.Group();",
                f"  {node_var}.name = {json.dumps(name + '__pivot')};",
                f"  if ({endpoint_var}) {{",
                f"    {node_var}.position.copy({endpoint_var}.start);",
                f"    {node_var}.rotation.set(0, 0, 0);",
                f"    {node_var}.scale.set(1, 1, 1);",
                "  } else {",
                f"    {node_var}.position.set({vector(transform.get('position'), [0, 0, 0])});",
                f"    {node_var}.rotation.set({vector(transform.get('rotation'), [0, 0, 0])});",
                f"    {node_var}.scale.set({scale_vector(component, transform)});",
                "  }",
                f"  {node_var}.userData.sculptComponent = {json.dumps(component, ensure_ascii=False)};",
                f"  {node_var}.userData.actionProfile = {json.dumps(action_profile, ensure_ascii=False)};",
                f"  (nodes[{json.dumps(str(parent))}] ?? root).add({node_var});",
                f"  nodes[{json.dumps(component_id)}] = {node_var};",
                f"  const {component_var}Geometry = {endpoint_var}",
                f"    ? new THREE.CylinderGeometry({endpoint_var}.endRadius, {endpoint_var}.baseRadius, {endpoint_var}.length, 32, 12)",
                f"    : {geometry_for(primitive, component)};",
                f"  const {component_var} = new THREE.Mesh(",
                f"    {component_var}Geometry,",
                f"    materialMap[{json.dumps(material_id)}] ?? new THREE.MeshStandardMaterial({{ color: 0x888888 }})",
                "  );",
                f"  {component_var}.name = {json.dumps(name)};",
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
    for rep_index, system in enumerate(spec.get("repetitionSystems", [])):
        if not isinstance(system, dict):
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
                f"    const geo = {geometry_for(primitive, {})};",
                f"    const mat = materialMap[{json.dumps(rep_material)}] ?? new THREE.MeshStandardMaterial({{ color: 0x888888 }});",
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
    args = parser.parse_args(argv)

    spec = load_spec(args.spec.expanduser().resolve())
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
