#!/usr/bin/env python3
"""Read a GLB's OWN rig -- joints, inverse binds, clips -- instead of assuming one (1.5.2 §R0, §R3).

WHY THIS MODULE EXISTS AT ALL. A reference GLB arrives carrying a real skin (`skinCount: 1`) and
real clips (`animationCount: 11`), and the pipeline then authors a skeleton *procedurally* from the
componentTree. Those are two different skeletons. The GLB's animation channels target GLB **node
indices**; the GLB's `JOINTS_0` attribute indexes the GLB's **own** `skin.joints` array. Feeding
either index space into a procedurally authored skeleton addresses a different bone at every index,
which is exactly what "the mesh comes out badly broken or disjointed" looks like from the outside.
The fast lane makes it worse by merging 69 GLB nodes into 20 components, so no 1:1 node -> component
mapping exists to fall back on.

So the rig is read out as explicit, validated data and the joint correspondence is made an artifact
you can audit, not an assumption nobody wrote down.

THE ORDERING RULE, from §R0.3, is the load-bearing one:

    bones[i] is the node for skin.joints[i] and boneInverses[i] is matrix i of the
    inverse-bind accessor. The ordering is the SKIN'S, never the traversal's.

`GlbRig.joints` is therefore in skin order and says so; nothing here sorts, re-roots or renames it.
And per §R0.1 a node is a Bone **iff it appears in `skin.joints`** -- never inferred from a name --
so `deform_vs_technical` counts, out loud, how many animation channels drive nodes that are not
joints. §R0.1 states that building those as bones corrupts the skeleton's index space; that count
is the number that makes it visible before it does.

Pure Python 3.10+ stdlib. No pip installs, no numpy, no glTF library, no three.js. GLB chunk/accessor
decoding is reused from `forge/stage1_intake/probe_glb.py` rather than reimplemented.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:  # pragma: no cover - exercised by both import routes
    from forge.stage1_intake.probe_glb import (
        _COMPONENT_FORMATS,
        _TYPE_COMPONENTS,
        _accessor_bounds,
        _buffer_view_bytes,
        parse_glb,
    )
except ImportError:  # pragma: no cover - direct script execution from any cwd
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from forge.stage1_intake.probe_glb import (
        _COMPONENT_FORMATS,
        _TYPE_COMPONENTS,
        _accessor_bounds,
        _buffer_view_bytes,
        parse_glb,
    )

try:  # pragma: no cover - package import
    from .clip_features import REQUIRED_LANDMARKS
except ImportError:  # pragma: no cover - flat sys.path import (how the stage5 tests load it)
    from clip_features import REQUIRED_LANDMARKS


# --------------------------------------------------------------------------------------------
# Named tolerances (CONTRACT_1.5.2.md: every threshold is a named constant a caller can override)
# --------------------------------------------------------------------------------------------

# Nearest-joint radius for `correspondence`, as a fraction of figure height H. Positions are
# MEASURED, so the tolerance is a length, not a string-similarity score.
CORRESPONDENCE_TOLERANCE = 0.05

# §1 samples every clip at N = 25 evenly spaced times.
DEFAULT_SAMPLE_COUNT = 25

# Below this, two quaternions are treated as the same rotation and slerp degenerates to a lerp.
SLERP_EPSILON = 1e-9

# A local scale component within this of 1.0 contributes nothing to the §1 `scaleDelta` tripwire.
SCALE_UNIT_EPSILON = 0.0

TRS_PATHS = ("translation", "rotation", "scale")
ANIMATION_PATHS = TRS_PATHS + ("weights",)
SUPPORTED_INTERPOLATIONS = ("STEP", "LINEAR")

IDENTITY_MATRIX: tuple[float, ...] = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)

INVERSE_BIND_OMITTED = "omitted-defaults-to-identity"


class UnsupportedInterpolation(ValueError):
    """A sampler uses an interpolation this module does not evaluate.

    Raised rather than approximated. CUBICSPLINE read as LINEAR does not fail loudly -- it produces
    a confident wrong feature vector, which is the failure mode §0 of the pipeline exists to stop.
    """

    def __init__(self, clip_name: str, interpolation: str, detail: str = "") -> None:
        message = (
            f"clip {clip_name!r} uses {interpolation} interpolation, which this module does not "
            f"evaluate; it is reported unsupported rather than approximated as LINEAR"
        )
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)
        self.clip_name = clip_name
        self.interpolation = interpolation


# --------------------------------------------------------------------------------------------
# Column-major 4x4 matrix and quaternion arithmetic (glTF convention throughout)
# --------------------------------------------------------------------------------------------


Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]  # x, y, z, w -- glTF and three.js order
Mat4 = tuple[float, ...]  # 16 floats, COLUMN-major: element (row i, col j) is m[j * 4 + i]


def compose_trs(translation: Vec3, rotation: Quat, scale: Vec3) -> Mat4:
    """Build the column-major local matrix glTF specifies as `M = T * R * S`."""
    x, y, z, w = rotation
    x2, y2, z2 = x + x, y + y, z + z
    xx, xy, xz = x * x2, x * y2, x * z2
    yy, yz, zz = y * y2, y * z2, z * z2
    wx, wy, wz = w * x2, w * y2, w * z2
    sx, sy, sz = scale
    return (
        (1.0 - (yy + zz)) * sx, (xy + wz) * sx, (xz - wy) * sx, 0.0,
        (xy - wz) * sy, (1.0 - (xx + zz)) * sy, (yz + wx) * sy, 0.0,
        (xz + wy) * sz, (yz - wx) * sz, (1.0 - (xx + yy)) * sz, 0.0,
        translation[0], translation[1], translation[2], 1.0,
    )


def multiply(a: Mat4, b: Mat4) -> Mat4:
    """Column-major `a * b`."""
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
    return tuple(out)


def matrix_translation(matrix: Mat4) -> Vec3:
    return (matrix[12], matrix[13], matrix[14])


def decompose(matrix: Mat4) -> tuple[Vec3, Quat, Vec3]:
    """Split a column-major matrix into T, R (xyzw), S.

    Assumes no shear, which is what glTF's `node.matrix` is required to be decomposable into. A
    sheared matrix would come back with a rotation that is not the matrix's rotation, so a node
    carrying one is reported rather than silently decomposed (see `_node_rest_trs`).
    """
    sx = math.sqrt(matrix[0] ** 2 + matrix[1] ** 2 + matrix[2] ** 2)
    sy = math.sqrt(matrix[4] ** 2 + matrix[5] ** 2 + matrix[6] ** 2)
    sz = math.sqrt(matrix[8] ** 2 + matrix[9] ** 2 + matrix[10] ** 2)
    determinant = (
        matrix[0] * (matrix[5] * matrix[10] - matrix[9] * matrix[6])
        - matrix[4] * (matrix[1] * matrix[10] - matrix[9] * matrix[2])
        + matrix[8] * (matrix[1] * matrix[6] - matrix[5] * matrix[2])
    )
    if determinant < 0.0:
        sx = -sx
    inv_x = 1.0 / sx if sx else 0.0
    inv_y = 1.0 / sy if sy else 0.0
    inv_z = 1.0 / sz if sz else 0.0
    m11, m21, m31 = matrix[0] * inv_x, matrix[1] * inv_x, matrix[2] * inv_x
    m12, m22, m32 = matrix[4] * inv_y, matrix[5] * inv_y, matrix[6] * inv_y
    m13, m23, m33 = matrix[8] * inv_z, matrix[9] * inv_z, matrix[10] * inv_z

    trace = m11 + m22 + m33
    if trace > 0.0:
        s = 0.5 / math.sqrt(trace + 1.0)
        rotation = ((m32 - m23) * s, (m13 - m31) * s, (m21 - m12) * s, 0.25 / s)
    elif m11 > m22 and m11 > m33:
        s = 2.0 * math.sqrt(1.0 + m11 - m22 - m33)
        rotation = (0.25 * s, (m12 + m21) / s, (m13 + m31) / s, (m32 - m23) / s)
    elif m22 > m33:
        s = 2.0 * math.sqrt(1.0 + m22 - m11 - m33)
        rotation = ((m12 + m21) / s, 0.25 * s, (m23 + m32) / s, (m13 - m31) / s)
    else:
        s = 2.0 * math.sqrt(1.0 + m33 - m11 - m22)
        rotation = ((m13 + m31) / s, (m23 + m32) / s, 0.25 * s, (m21 - m12) / s)
    return (matrix[12], matrix[13], matrix[14]), normalize_quat(rotation), (sx, sy, sz)


def normalize_quat(q: Quat) -> Quat:
    length = math.sqrt(q[0] ** 2 + q[1] ** 2 + q[2] ** 2 + q[3] ** 2)
    if length == 0.0:
        return (0.0, 0.0, 0.0, 1.0)
    return (q[0] / length, q[1] / length, q[2] / length, q[3] / length)


def slerp(a: Quat, b: Quat, t: float) -> Quat:
    """Spherical linear interpolation on the shorter arc.

    A normalised lerp is NOT a substitute: over a wide arc it advances at the wrong rate, so a
    landmark sampled from it is in the wrong place at every interior time. `test_..._slerp_not_lerp`
    pins a case where the two differ by degrees.
    """
    if t <= 0.0:
        return a
    if t >= 1.0:
        return b
    cos_half = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]
    if cos_half < 0.0:
        b = (-b[0], -b[1], -b[2], -b[3])
        cos_half = -cos_half
    if cos_half >= 1.0 - SLERP_EPSILON:
        # Degenerate arc: the endpoints are the same rotation, so lerp and slerp agree.
        return normalize_quat(
            (
                a[0] + (b[0] - a[0]) * t,
                a[1] + (b[1] - a[1]) * t,
                a[2] + (b[2] - a[2]) * t,
                a[3] + (b[3] - a[3]) * t,
            )
        )
    sin_half = math.sqrt(1.0 - cos_half * cos_half)
    half_theta = math.atan2(sin_half, cos_half)
    ratio_a = math.sin((1.0 - t) * half_theta) / sin_half
    ratio_b = math.sin(t * half_theta) / sin_half
    return normalize_quat(
        (
            a[0] * ratio_a + b[0] * ratio_b,
            a[1] * ratio_a + b[1] * ratio_b,
            a[2] * ratio_a + b[2] * ratio_b,
            a[3] * ratio_a + b[3] * ratio_b,
        )
    )


def quaternion_angle_degrees(a: Quat, b: Quat) -> float:
    """Absolute rotation angle between two unit quaternions, in degrees, ignoring double cover."""
    a = normalize_quat(a)
    b = normalize_quat(b)
    dot = abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3])
    dot = min(1.0, max(-1.0, dot))
    return math.degrees(2.0 * math.acos(dot))


# --------------------------------------------------------------------------------------------
# Accessor decoding (chunk/bufferView plumbing reused from probe_glb)
# --------------------------------------------------------------------------------------------


_NORMALISATION = {
    5120: lambda v: max(v / 127.0, -1.0),
    5121: lambda v: v / 255.0,
    5122: lambda v: max(v / 32767.0, -1.0),
    5123: lambda v: v / 65535.0,
}


def read_accessor(document: Mapping[str, Any], bin_payload: bytes, index: int) -> list[tuple[float, ...]]:
    """Decode accessor `index` into one tuple of floats per element.

    Handles the componentTypes and element types glTF allows for skins and animation samplers,
    including `normalized` integer quantisation. Sparse accessors are refused by name rather than
    decoded as their (wrong) base view.
    """
    accessors = document.get("accessors", [])
    if not isinstance(accessors, list) or not 0 <= index < len(accessors):
        raise ValueError(f"missing accessor {index}")
    accessor = accessors[index]
    if not isinstance(accessor, dict):
        raise ValueError(f"accessor {index} is not an object")
    if "sparse" in accessor:
        raise ValueError(
            f"accessor {index} uses sparse storage, which this reader does not decode; "
            f"reading its base bufferView alone would return values the file does not mean"
        )
    element_type = accessor.get("type")
    components = _TYPE_COMPONENTS.get(str(element_type))
    if components is None:
        raise ValueError(f"accessor {index} has unsupported type {element_type!r}")
    component_type = int(accessor.get("componentType", 5126))
    fmt_info = _COMPONENT_FORMATS.get(component_type)
    if fmt_info is None:
        raise ValueError(f"accessor {index} has unsupported componentType {component_type}")
    fmt, component_bytes = fmt_info
    count = int(accessor.get("count", 0))
    if count < 0:
        raise ValueError(f"accessor {index} has a negative count")

    view_index = accessor.get("bufferView")
    if view_index is None:
        # glTF: an accessor with no bufferView reads as zeros. Say so; do not pretend it is data.
        return [tuple([0.0] * components) for _ in range(count)]
    raw = _buffer_view_bytes(document, bin_payload, int(view_index))
    view = document["bufferViews"][int(view_index)]
    element_bytes = component_bytes * components
    stride = int(view.get("byteStride", element_bytes) or element_bytes)
    if stride < element_bytes:
        raise ValueError(f"bufferView stride {stride} is too small for accessor {index}")
    base = int(accessor.get("byteOffset", 0))
    normalise = _NORMALISATION.get(component_type) if accessor.get("normalized") else None

    out: list[tuple[float, ...]] = []
    for element in range(count):
        offset = base + element * stride
        if offset + element_bytes > len(raw):
            raise ValueError(f"accessor {index} element {element} exceeds its bufferView")
        values = struct.unpack_from(f"<{components}{fmt}", raw, offset)
        if normalise is not None:
            out.append(tuple(float(normalise(value)) for value in values))
        else:
            out.append(tuple(float(value) for value in values))
    return out


# --------------------------------------------------------------------------------------------
# The rig record
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GlbJoint:
    """One entry of `skin.joints`. `skin_joint_index` is the authority for every other index."""

    skin_joint_index: int
    node_index: int
    node_name: str
    parent_node_index: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "skinJointIndex": self.skin_joint_index,
            "nodeIndex": self.node_index,
            "nodeName": self.node_name,
            "parentNodeIndex": self.parent_node_index,
        }


@dataclass(frozen=True)
class GlbSkinRecord:
    """A single skin, read in its own joint order.

    `inverse_bind_source` is either `accessor:<n>` or `omitted-defaults-to-identity`. glTF says an
    absent `inverseBindMatrices` means identity, and that is a legal file -- but a caller that sees
    sixteen-float identities with no provenance cannot tell "the file said identity" from "the
    reader failed and filled in identity", so the two are never rendered the same way here.
    """

    skin_index: int
    name: str
    skeleton_node_index: int | None
    joints: tuple[GlbJoint, ...]
    inverse_bind_matrices: tuple[Mat4, ...]
    inverse_bind_source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "skinIndex": self.skin_index,
            "name": self.name,
            "skeletonNodeIndex": self.skeleton_node_index,
            "jointCount": len(self.joints),
            "jointOrder": "skin.joints (§R0.3: the ordering is the skin's, never the traversal's)",
            "joints": [joint.to_dict() for joint in self.joints],
            "inverseBindSource": self.inverse_bind_source,
            "inverseBindMatrices": [list(matrix) for matrix in self.inverse_bind_matrices],
        }


@dataclass(frozen=True)
class GlbChannel:
    animation_index: int
    channel_index: int
    sampler_index: int
    target_node_index: int | None
    path: str
    interpolation: str
    key_count: int
    time_min: float
    time_max: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "channelIndex": self.channel_index,
            "samplerIndex": self.sampler_index,
            "targetNodeIndex": self.target_node_index,
            "path": self.path,
            "interpolation": self.interpolation,
            "keyCount": self.key_count,
            "timeMin": self.time_min,
            "timeMax": self.time_max,
        }


@dataclass(frozen=True)
class GlbClip:
    animation_index: int
    name: str
    channels: tuple[GlbChannel, ...]

    @property
    def duration(self) -> float:
        """Clip length = the largest channel `timeMax`. Empty clips are 0.0, not `None`."""
        return max((channel.time_max for channel in self.channels), default=0.0)

    @property
    def interpolations(self) -> tuple[str, ...]:
        return tuple(sorted({channel.interpolation for channel in self.channels}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "animationIndex": self.animation_index,
            "name": self.name,
            "duration": self.duration,
            "channelCount": len(self.channels),
            "interpolations": list(self.interpolations),
            "channels": [channel.to_dict() for channel in self.channels],
        }


@dataclass
class GlbRig:
    """Everything the GLB itself says about its rig, with nothing inferred from names.

    `joints` and `inverse_bind_matrices` are the PRIMARY skin's, in skin order, and exist only when
    the file has exactly one skin. With zero or several skins they are empty and
    `structural_failure` states why -- picking one for the caller would be exactly the silent guess
    this module was written to remove.
    """

    path: str
    node_count: int
    nodes: tuple[dict[str, Any], ...]
    skins: tuple[GlbSkinRecord, ...]
    primary_skin: GlbSkinRecord | None
    skinned_mesh_nodes: tuple[dict[str, Any], ...]
    clips: tuple[GlbClip, ...]
    deform_vs_technical: dict[str, Any]
    structural_failure: str | None
    warnings: tuple[str, ...] = ()
    _document: dict[str, Any] = field(default_factory=dict, repr=False)
    _bin: bytes = field(default=b"", repr=False)
    _parents: dict[int, int | None] = field(default_factory=dict, repr=False)

    # -- skin-ordered views ------------------------------------------------------------------

    @property
    def joints(self) -> tuple[GlbJoint, ...]:
        return self.primary_skin.joints if self.primary_skin else ()

    @property
    def inverse_bind_matrices(self) -> tuple[Mat4, ...]:
        return self.primary_skin.inverse_bind_matrices if self.primary_skin else ()

    @property
    def inverse_bind_source(self) -> str | None:
        return self.primary_skin.inverse_bind_source if self.primary_skin else None

    @property
    def unsupported_interpolation_clips(self) -> tuple[str, ...]:
        return tuple(
            clip.name
            for clip in self.clips
            if any(
                channel.interpolation not in SUPPORTED_INTERPOLATIONS and channel.path in TRS_PATHS
                for channel in clip.channels
            )
        )

    def node_name(self, index: int) -> str:
        if 0 <= index < len(self.nodes):
            return str(self.nodes[index]["name"])
        return f"node-{index}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "kind": "glb-rig-reference",
            "path": self.path,
            "nodeCount": self.node_count,
            "skinCount": len(self.skins),
            "clipCount": len(self.clips),
            "primarySkinIndex": self.primary_skin.skin_index if self.primary_skin else None,
            "skins": [skin.to_dict() for skin in self.skins],
            "joints": [joint.to_dict() for joint in self.joints],
            "inverseBindSource": self.inverse_bind_source,
            "inverseBindMatrices": [list(matrix) for matrix in self.inverse_bind_matrices],
            "skinnedMeshNodes": list(self.skinned_mesh_nodes),
            "clips": [clip.to_dict() for clip in self.clips],
            "deformVsTechnical": self.deform_vs_technical,
            "unsupportedInterpolationClips": list(self.unsupported_interpolation_clips),
            "structuralFailure": self.structural_failure,
            "warnings": list(self.warnings),
            "note": (
                "Joint order is skin.joints order (§R0.3). Animation channels address GLB node "
                "indices and JOINTS_0 addresses this joint array; neither index space is valid "
                "against a procedurally authored skeleton without an explicit correspondence."
            ),
        }


# --------------------------------------------------------------------------------------------
# read_rig
# --------------------------------------------------------------------------------------------


def _node_list(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    nodes = document.get("nodes", [])
    return [node if isinstance(node, dict) else {} for node in nodes] if isinstance(nodes, list) else []


def _parent_map(nodes: Sequence[Mapping[str, Any]]) -> dict[int, int | None]:
    parents: dict[int, int | None] = {index: None for index in range(len(nodes))}
    for index, node in enumerate(nodes):
        children = node.get("children", [])
        if not isinstance(children, list):
            continue
        for child in children:
            child_index = int(child)
            if 0 <= child_index < len(nodes):
                parents[child_index] = index
    return parents


def read_rig(glb_path: str | Path) -> GlbRig:
    """Extract the GLB's rig as data. Never raises on a structurally odd rig -- it records it.

    A missing or ambiguous skin is a *finding* the caller has to act on, not an exception thrown
    from the middle of a parse. `structural_failure` carries the reason; the CLI turns it into
    exit 1.
    """
    path = Path(glb_path).expanduser().resolve()
    document, bin_payload, _binary = parse_glb(path)
    warnings: list[str] = []

    nodes = _node_list(document)
    parents = _parent_map(nodes)
    node_records = tuple(
        {
            "index": index,
            "name": node.get("name") or f"node-{index}",
            "parentNodeIndex": parents[index],
            "children": [int(child) for child in (node.get("children") or []) if isinstance(child, int)],
            "mesh": node.get("mesh"),
            "skin": node.get("skin"),
        }
        for index, node in enumerate(nodes)
    )

    # -- skins, each in its own joint order ---------------------------------------------------
    raw_skins = document.get("skins", [])
    raw_skins = raw_skins if isinstance(raw_skins, list) else []
    skins: list[GlbSkinRecord] = []
    for skin_index, skin in enumerate(raw_skins):
        if not isinstance(skin, dict):
            warnings.append(f"skin {skin_index} is not an object and was skipped")
            continue
        joint_indices = skin.get("joints", [])
        if not isinstance(joint_indices, list):
            warnings.append(f"skin {skin_index} has no joints array")
            joint_indices = []
        joints: list[GlbJoint] = []
        for slot, node_index in enumerate(joint_indices):
            node_index = int(node_index)
            if not 0 <= node_index < len(nodes):
                warnings.append(
                    f"skin {skin_index} joint slot {slot} references node {node_index}, which does not exist"
                )
                joints.append(GlbJoint(slot, node_index, f"node-{node_index}", None))
                continue
            joints.append(
                GlbJoint(
                    skin_joint_index=slot,
                    node_index=node_index,
                    node_name=str(nodes[node_index].get("name") or f"node-{node_index}"),
                    parent_node_index=parents.get(node_index),
                )
            )

        ibm_accessor = skin.get("inverseBindMatrices")
        if ibm_accessor is None:
            inverse_binds = tuple(IDENTITY_MATRIX for _ in joints)
            inverse_bind_source = INVERSE_BIND_OMITTED
        else:
            try:
                decoded = read_accessor(document, bin_payload, int(ibm_accessor))
            except ValueError as exc:
                warnings.append(f"skin {skin_index}: {exc}")
                decoded = []
            if len(decoded) != len(joints):
                warnings.append(
                    f"skin {skin_index} has {len(joints)} joints but its inverseBindMatrices accessor "
                    f"holds {len(decoded)} matrices; §R0.3 pairs them index-for-index, so the pairing "
                    f"is not trustworthy"
                )
            inverse_binds = tuple(
                tuple(decoded[slot]) if slot < len(decoded) and len(decoded[slot]) == 16 else IDENTITY_MATRIX
                for slot in range(len(joints))
            )
            inverse_bind_source = f"accessor:{int(ibm_accessor)}"

        skins.append(
            GlbSkinRecord(
                skin_index=skin_index,
                name=str(skin.get("name") or f"skin-{skin_index}"),
                skeleton_node_index=(int(skin["skeleton"]) if isinstance(skin.get("skeleton"), int) else None),
                joints=tuple(joints),
                inverse_bind_matrices=inverse_binds,
                inverse_bind_source=inverse_bind_source,
            )
        )

    primary_skin = skins[0] if len(skins) == 1 else None
    structural_failure: str | None = None
    if not skins:
        structural_failure = (
            "GLB carries no skin, so it has no rig to reference; a procedurally authored skeleton "
            "cannot be validated against this file"
        )
    elif len(skins) > 1:
        structural_failure = (
            f"GLB carries {len(skins)} skins and no primary skin was chosen; each skin owns its own "
            f"joint index space, so JOINTS_0 and inverse binds are only meaningful once the caller "
            f"names one (skin indices {[skin.skin_index for skin in skins]})"
        )

    # -- skinned mesh nodes -------------------------------------------------------------------
    skinned_mesh_nodes = tuple(
        {
            "nodeIndex": index,
            "nodeName": str(node.get("name") or f"node-{index}"),
            "meshIndex": int(node["mesh"]),
            "skinIndex": int(node["skin"]),
        }
        for index, node in enumerate(nodes)
        if isinstance(node.get("mesh"), int) and isinstance(node.get("skin"), int)
    )

    # -- clips --------------------------------------------------------------------------------
    raw_animations = document.get("animations", [])
    raw_animations = raw_animations if isinstance(raw_animations, list) else []
    clips: list[GlbClip] = []
    for animation_index, animation in enumerate(raw_animations):
        if not isinstance(animation, dict):
            warnings.append(f"animation {animation_index} is not an object and was skipped")
            continue
        samplers = animation.get("samplers", [])
        samplers = samplers if isinstance(samplers, list) else []
        raw_channels = animation.get("channels", [])
        raw_channels = raw_channels if isinstance(raw_channels, list) else []
        channels: list[GlbChannel] = []
        for channel_index, channel in enumerate(raw_channels):
            if not isinstance(channel, dict):
                continue
            sampler_index = channel.get("sampler")
            target = channel.get("target") if isinstance(channel.get("target"), dict) else {}
            path_name = str(target.get("path", "")) or "unknown"
            target_node = target.get("node")
            target_node_index = int(target_node) if isinstance(target_node, int) else None
            interpolation = "LINEAR"
            key_count = 0
            time_min = 0.0
            time_max = 0.0
            if isinstance(sampler_index, int) and 0 <= sampler_index < len(samplers):
                sampler = samplers[sampler_index]
                if isinstance(sampler, dict):
                    interpolation = str(sampler.get("interpolation", "LINEAR"))
                    input_accessor = sampler.get("input")
                    if isinstance(input_accessor, int):
                        try:
                            times = [value[0] for value in read_accessor(document, bin_payload, input_accessor)]
                        except ValueError as exc:
                            warnings.append(f"animation {animation_index} channel {channel_index}: {exc}")
                            times = []
                        key_count = len(times)
                        if times:
                            time_min = min(times)
                            time_max = max(times)
            else:
                warnings.append(
                    f"animation {animation_index} channel {channel_index} references missing sampler {sampler_index!r}"
                )
            channels.append(
                GlbChannel(
                    animation_index=animation_index,
                    channel_index=channel_index,
                    sampler_index=int(sampler_index) if isinstance(sampler_index, int) else -1,
                    target_node_index=target_node_index,
                    path=path_name,
                    interpolation=interpolation,
                    key_count=key_count,
                    time_min=time_min,
                    time_max=time_max,
                )
            )
        clips.append(
            GlbClip(
                animation_index=animation_index,
                name=str(animation.get("name") or f"animation-{animation_index}"),
                channels=tuple(channels),
            )
        )

    deform_vs_technical = _deform_vs_technical(skins, primary_skin, clips, node_records)
    if deform_vs_technical.get("channelsTargetingTechnicalNodes"):
        warnings.append(
            f"{deform_vs_technical['channelsTargetingTechnicalNodes']} animation channels target nodes "
            f"that are not in skin.joints; §R0.1 says building those as bones corrupts the skeleton's "
            f"index space"
        )

    return GlbRig(
        path=str(path),
        node_count=len(nodes),
        nodes=node_records,
        skins=tuple(skins),
        primary_skin=primary_skin,
        skinned_mesh_nodes=skinned_mesh_nodes,
        clips=tuple(clips),
        deform_vs_technical=deform_vs_technical,
        structural_failure=structural_failure,
        warnings=tuple(warnings),
        _document=dict(document),
        _bin=bin_payload,
        _parents=parents,
    )


def _deform_vs_technical(
    skins: Sequence[GlbSkinRecord],
    primary_skin: GlbSkinRecord | None,
    clips: Sequence[GlbClip],
    node_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """§R0.1: classify every animation-targeted node as deform (in skin.joints) or technical.

    With no skin at all there is no membership test, so this reports `unevaluated` with a reason
    (CONTRACT_1.5.2: a check whose input is absent is never silently a pass).
    """
    targeted: dict[int, int] = {}
    channels_without_target = 0
    for clip in clips:
        for channel in clip.channels:
            if channel.target_node_index is None:
                channels_without_target += 1
                continue
            targeted[channel.target_node_index] = targeted.get(channel.target_node_index, 0) + 1

    if not skins:
        return {
            "status": "unevaluated",
            "reason": "GLB carries no skin, so 'is this node in skin.joints' has no answer here",
            "basis": None,
            "targetedNodeCount": len(targeted),
            "channelsWithoutTargetNode": channels_without_target,
            "deformNodes": [],
            "technicalNodes": [],
            "channelsTargetingJoints": None,
            "channelsTargetingTechnicalNodes": None,
        }

    if primary_skin is not None:
        joint_nodes = {joint.node_index for joint in primary_skin.joints}
        basis = f"primarySkin:{primary_skin.skin_index}"
    else:
        joint_nodes = {joint.node_index for skin in skins for joint in skin.joints}
        basis = "unionOfAllSkins"

    def name_of(index: int) -> str:
        if 0 <= index < len(node_records):
            return str(node_records[index]["name"])
        return f"node-{index}"

    deform_nodes = []
    technical_nodes = []
    deform_channels = 0
    technical_channels = 0
    for node_index in sorted(targeted):
        record = {"nodeIndex": node_index, "nodeName": name_of(node_index), "channelCount": targeted[node_index]}
        if node_index in joint_nodes:
            deform_nodes.append(record)
            deform_channels += targeted[node_index]
        else:
            technical_nodes.append(record)
            technical_channels += targeted[node_index]

    return {
        "status": "evaluated",
        "reason": None,
        "basis": basis,
        "rule": "§R0.1: a node is a Bone IFF it appears in skin.joints; never inferred from names",
        "targetedNodeCount": len(targeted),
        "channelsWithoutTargetNode": channels_without_target,
        "deformNodes": deform_nodes,
        "technicalNodes": technical_nodes,
        "channelsTargetingJoints": deform_channels,
        "channelsTargetingTechnicalNodes": technical_channels,
    }


# --------------------------------------------------------------------------------------------
# Rest-pose world matrices and figure height
# --------------------------------------------------------------------------------------------


def _node_rest_trs(node: Mapping[str, Any]) -> tuple[Vec3, Quat, Vec3]:
    matrix = node.get("matrix")
    if isinstance(matrix, list) and len(matrix) == 16:
        return decompose(tuple(float(value) for value in matrix))
    translation = node.get("translation", [0.0, 0.0, 0.0])
    rotation = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
    scale = node.get("scale", [1.0, 1.0, 1.0])
    return (
        (float(translation[0]), float(translation[1]), float(translation[2])),
        normalize_quat((float(rotation[0]), float(rotation[1]), float(rotation[2]), float(rotation[3]))),
        (float(scale[0]), float(scale[1]), float(scale[2])),
    )


def _world_matrices(
    nodes: Sequence[Mapping[str, Any]],
    parents: Mapping[int, int | None],
    local: Sequence[Mat4],
) -> list[Mat4]:
    """Compose local matrices up the hierarchy. §R0.2: link first, then root -- any other order
    leaves world matrices stale at skeleton time."""
    world: list[Mat4 | None] = [None] * len(nodes)

    def resolve(index: int, guard: frozenset[int]) -> Mat4:
        cached = world[index]
        if cached is not None:
            return cached
        if index in guard:
            raise ValueError(f"node {index} is part of a parent cycle; the hierarchy is not a tree")
        parent = parents.get(index)
        if parent is None:
            value = local[index]
        else:
            value = multiply(resolve(parent, guard | {index}), local[index])
        world[index] = value
        return value

    return [resolve(index, frozenset()) for index in range(len(nodes))]


def rest_world_matrices(rig: GlbRig) -> list[Mat4]:
    nodes = _node_list(rig._document)
    local = [compose_trs(*_node_rest_trs(node)) for node in nodes]
    return _world_matrices(nodes, rig._parents, local)


def joint_rest_positions(rig: GlbRig) -> dict[int, Vec3]:
    """Model-space position of each PRIMARY-SKIN joint at rest, keyed by skin joint index."""
    world = rest_world_matrices(rig)
    return {
        joint.skin_joint_index: matrix_translation(world[joint.node_index])
        for joint in rig.joints
        if 0 <= joint.node_index < len(world)
    }


def derive_figure_height(rig: GlbRig) -> tuple[float, str]:
    """Figure height H from the GLB's own mesh bounds (Y extent), with its provenance.

    Everything in 1.5.2 is a fraction of H, so H is never assumed to be 1.0. If it cannot be
    measured the caller is told to supply it rather than handed a default.
    """
    meshes = rig._document.get("meshes", [])
    meshes = meshes if isinstance(meshes, list) else []
    lows: list[float] = []
    highs: list[float] = []
    for mesh in meshes:
        if not isinstance(mesh, dict):
            continue
        for primitive in mesh.get("primitives", []) or []:
            if not isinstance(primitive, dict):
                continue
            attributes = primitive.get("attributes", {})
            position = attributes.get("POSITION") if isinstance(attributes, dict) else None
            if position is None:
                continue
            try:
                minimum, maximum, _ = _accessor_bounds(rig._document, rig._bin, int(position))
            except ValueError:
                continue
            if math.isfinite(minimum[1]) and math.isfinite(maximum[1]):
                lows.append(minimum[1])
                highs.append(maximum[1])
    if not lows:
        raise ValueError(
            "figure height could not be measured from the GLB's mesh bounds; pass figure_height "
            "explicitly rather than letting a module assume H == 1.0"
        )
    height = max(highs) - min(lows)
    if height <= 0.0:
        raise ValueError("figure height measured as zero from the GLB's mesh bounds; pass it explicitly")
    return height, "meshPositionBoundsYExtent"


# --------------------------------------------------------------------------------------------
# Correspondence -- the thing the 1.5.2 pipeline says is missing
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BoneMatch:
    bone_id: str
    bone_position: Vec3
    skin_joint_index: int | None
    node_index: int | None
    node_name: str | None
    distance_h: float | None
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "boneId": self.bone_id,
            "bonePosition": list(self.bone_position),
            "skinJointIndex": self.skin_joint_index,
            "nodeIndex": self.node_index,
            "nodeName": self.node_name,
            "distanceH": self.distance_h,
            "confidence": self.confidence,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Correspondence:
    """An explicit procedural-bone -> GLB-joint map, matched by MEASURED POSITION.

    The 1.5.2 pipeline lists retargeting between skeletons under "What this pipeline does not
    solve": *"Everything here assumes one skeleton. Cross-rig retarget needs a joint correspondence
    and its own gates."* This supplies the correspondence half and states its own limit instead of
    implying the problem is closed:

      * Matching is positional, never by name. Exporters emit `node_17`, `mixamorig:LeftArm` and
        `Bone.003` for the same anatomy; a name match is a coincidence detector.
      * `usable` is False whenever ANY joint on EITHER side is unmatched. A partial map retargets
        the bones it knows and leaves the rest at bind pose, which is the disjointed mesh this
        module exists to prevent -- so a partial map is reported as unusable, not as progress.
      * Gates are still owed. This is the correspondence, not the "own gates" the spec also asks
        for.
    """

    figure_height: float
    tolerance: float
    matches: tuple[BoneMatch, ...]
    unmatched_procedural_bones: tuple[str, ...]
    unmatched_glb_joints: tuple[dict[str, Any], ...]
    usable: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "figureHeight": self.figure_height,
            "tolerance": self.tolerance,
            "toleranceUnit": "fraction of figure height H",
            "matchedBy": "measured model-space position (never node names)",
            "matches": [match.to_dict() for match in self.matches],
            "unmatchedProceduralBones": list(self.unmatched_procedural_bones),
            "unmatchedGlbJoints": [dict(joint) for joint in self.unmatched_glb_joints],
            "usable": self.usable,
            "reason": self.reason,
            "limit": (
                "1.5.2 lists cross-rig retarget as unsolved and needing 'a joint correspondence and "
                "its own gates'. This is the correspondence; the gates are not supplied here."
            ),
        }


def _bone_position(bone: Mapping[str, Any]) -> Vec3:
    for key in ("position", "jointPosition", "modelPosition", "head"):
        value = bone.get(key)
        if isinstance(value, (list, tuple)) and len(value) == 3:
            return (float(value[0]), float(value[1]), float(value[2]))
    raise ValueError(
        f"procedural bone {bone.get('id')!r} has no model-space joint position "
        f"(expected one of position/jointPosition/modelPosition/head)"
    )


def correspondence(
    glb_rig: GlbRig,
    component_bones: Sequence[Mapping[str, Any]],
    figure_height: float | None = None,
    tolerance: float = CORRESPONDENCE_TOLERANCE,
) -> Correspondence:
    """Match procedurally derived bones to the GLB's skin joints by position.

    `component_bones` is `[{"id": str, "position": [x, y, z]}, ...]` in the same model space as the
    GLB's rest pose. Assignment is globally greedy on distance and mutually exclusive, so two
    procedural bones can never claim the same GLB joint and quietly double-drive it.
    """
    if figure_height is None:
        figure_height, _source = derive_figure_height(glb_rig)
    if not math.isfinite(figure_height) or figure_height <= 0.0:
        raise ValueError(f"figure_height must be a finite positive number; got {figure_height!r}")

    joints = glb_rig.joints
    if not joints:
        return Correspondence(
            figure_height=figure_height,
            tolerance=tolerance,
            matches=(),
            unmatched_procedural_bones=tuple(str(bone.get("id")) for bone in component_bones),
            unmatched_glb_joints=(),
            usable=False,
            reason=(
                glb_rig.structural_failure
                or "the GLB exposes no primary skin, so there are no joints to correspond to"
            ),
        )

    joint_positions = joint_rest_positions(glb_rig)
    bones = list(component_bones)
    bone_positions = [_bone_position(bone) for bone in bones]

    limit = tolerance * figure_height
    candidates: list[tuple[float, int, int]] = []
    for bone_i, bone_position in enumerate(bone_positions):
        for joint in joints:
            joint_position = joint_positions.get(joint.skin_joint_index)
            if joint_position is None:
                continue
            distance = math.dist(bone_position, joint_position)
            if distance <= limit:
                candidates.append((distance, bone_i, joint.skin_joint_index))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))

    claimed_bones: dict[int, tuple[float, int]] = {}
    claimed_joints: set[int] = set()
    for distance, bone_i, joint_slot in candidates:
        if bone_i in claimed_bones or joint_slot in claimed_joints:
            continue
        claimed_bones[bone_i] = (distance, joint_slot)
        claimed_joints.add(joint_slot)

    matches: list[BoneMatch] = []
    unmatched_bones: list[str] = []
    for bone_i, bone in enumerate(bones):
        bone_id = str(bone.get("id", f"bone-{bone_i}"))
        claim = claimed_bones.get(bone_i)
        if claim is None:
            unmatched_bones.append(bone_id)
            matches.append(
                BoneMatch(
                    bone_id=bone_id,
                    bone_position=bone_positions[bone_i],
                    skin_joint_index=None,
                    node_index=None,
                    node_name=None,
                    distance_h=None,
                    confidence=0.0,
                    reason=f"no GLB joint within {tolerance}H ({limit:.6g} in model units)",
                )
            )
            continue
        distance, joint_slot = claim
        joint = joints[joint_slot]
        distance_h = distance / figure_height
        confidence = max(0.0, min(1.0, 1.0 - distance_h / tolerance)) if tolerance > 0 else 0.0
        matches.append(
            BoneMatch(
                bone_id=bone_id,
                bone_position=bone_positions[bone_i],
                skin_joint_index=joint.skin_joint_index,
                node_index=joint.node_index,
                node_name=joint.node_name,
                distance_h=distance_h,
                confidence=confidence,
                reason=f"nearest GLB joint at {distance_h:.4f}H, inside the {tolerance}H tolerance",
            )
        )

    unmatched_joints = tuple(
        joint.to_dict() for joint in joints if joint.skin_joint_index not in claimed_joints
    )

    if not unmatched_bones and not unmatched_joints:
        usable = True
        reason = (
            f"every one of {len(bones)} procedural bones and all {len(joints)} GLB joints matched "
            f"within {tolerance}H"
        )
    else:
        usable = False
        parts = []
        if unmatched_bones:
            parts.append(f"{len(unmatched_bones)} procedural bone(s) matched no GLB joint ({', '.join(unmatched_bones)})")
        if unmatched_joints:
            names = ", ".join(str(joint["nodeName"]) for joint in unmatched_joints)
            parts.append(f"{len(unmatched_joints)} GLB joint(s) matched no procedural bone ({names})")
        reason = (
            "; ".join(parts)
            + ". A partial correspondence retargets the bones it knows and leaves the rest at bind "
            "pose, which is the disjointed-mesh failure this module exists to prevent, so it is "
            "not usable for retargeting."
        )

    return Correspondence(
        figure_height=figure_height,
        tolerance=tolerance,
        matches=tuple(matches),
        unmatched_procedural_bones=tuple(unmatched_bones),
        unmatched_glb_joints=unmatched_joints,
        usable=usable,
        reason=reason,
    )


# --------------------------------------------------------------------------------------------
# Clip sampling -- TRS evaluation of the GLB's OWN channels
# --------------------------------------------------------------------------------------------


@dataclass
class _Sampler:
    times: list[float]
    values: list[tuple[float, ...]]
    interpolation: str


def _load_samplers(rig: GlbRig, clip: GlbClip) -> dict[int, _Sampler]:
    animation = rig._document["animations"][clip.animation_index]
    raw_samplers = animation.get("samplers", [])
    loaded: dict[int, _Sampler] = {}
    for index, sampler in enumerate(raw_samplers):
        if not isinstance(sampler, dict):
            continue
        input_accessor = sampler.get("input")
        output_accessor = sampler.get("output")
        if not isinstance(input_accessor, int) or not isinstance(output_accessor, int):
            continue
        times = [value[0] for value in read_accessor(rig._document, rig._bin, input_accessor)]
        values = read_accessor(rig._document, rig._bin, output_accessor)
        loaded[index] = _Sampler(times, values, str(sampler.get("interpolation", "LINEAR")))
    return loaded


def _bracket(times: Sequence[float], t: float) -> tuple[int, int, float]:
    """Return (i, j, alpha) with times[i] <= t <= times[j]. Clamps outside the key range."""
    if not times:
        raise ValueError("sampler has no key times")
    if t <= times[0]:
        return 0, 0, 0.0
    if t >= times[-1]:
        last = len(times) - 1
        return last, last, 0.0
    low, high = 0, len(times) - 1
    while high - low > 1:
        middle = (low + high) // 2
        if times[middle] <= t:
            low = middle
        else:
            high = middle
    span = times[high] - times[low]
    alpha = 0.0 if span <= 0.0 else (t - times[low]) / span
    return low, high, alpha


def _evaluate_sampler(sampler: _Sampler, path: str, t: float, clip_name: str) -> tuple[float, ...]:
    if sampler.interpolation not in SUPPORTED_INTERPOLATIONS:
        raise UnsupportedInterpolation(clip_name, sampler.interpolation, f"path {path!r}")
    low, high, alpha = _bracket(sampler.times, t)
    if low >= len(sampler.values) or high >= len(sampler.values):
        raise ValueError(
            f"clip {clip_name!r}: sampler output has {len(sampler.values)} values for "
            f"{len(sampler.times)} key times"
        )
    a = sampler.values[low]
    b = sampler.values[high]
    if sampler.interpolation == "STEP" or low == high or alpha == 0.0:
        # STEP holds the value of the key at or before t, all the way to the next key.
        return a
    if path == "rotation":
        quat = slerp(
            (a[0], a[1], a[2], a[3]),
            (b[0], b[1], b[2], b[3]),
            alpha,
        )
        return quat
    return tuple(a[i] + (b[i] - a[i]) * alpha for i in range(min(len(a), len(b))))


def _pose_at(
    rig: GlbRig,
    nodes: Sequence[Mapping[str, Any]],
    rest: Sequence[tuple[Vec3, Quat, Vec3]],
    channels: Sequence[tuple[GlbChannel, _Sampler]],
    t: float,
    clip_name: str,
) -> tuple[list[Mat4], list[tuple[Vec3, Quat, Vec3]]]:
    """Local TRS for every node at time t (rest, overridden by the clip's channels), then world."""
    trs = [(item[0], item[1], item[2]) for item in rest]
    for channel, sampler in channels:
        if channel.target_node_index is None or channel.path not in TRS_PATHS:
            continue
        index = channel.target_node_index
        if not 0 <= index < len(trs):
            continue
        value = _evaluate_sampler(sampler, channel.path, t, clip_name)
        translation, rotation, scale = trs[index]
        if channel.path == "translation" and len(value) >= 3:
            translation = (value[0], value[1], value[2])
        elif channel.path == "rotation" and len(value) >= 4:
            rotation = normalize_quat((value[0], value[1], value[2], value[3]))
        elif channel.path == "scale" and len(value) >= 3:
            scale = (value[0], value[1], value[2])
        trs[index] = (translation, rotation, scale)
    local = [compose_trs(*item) for item in trs]
    return _world_matrices(nodes, rig._parents, local), trs


def sample_clips(
    glb_rig: GlbRig,
    landmark_node_indices: Mapping[str, int],
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    figure_height: float | None = None,
) -> dict[str, Any]:
    """Evaluate the GLB's own channels and emit a CONTRACT_1.5.2 sampled-clip payload.

    The output is exactly what `clip_features.load_payload` / `measure_clip` consume, so a rig that
    ARRIVES with clips (§R3) goes through the same §1 vocabulary as a rig whose clips were authored
    (§R4) -- no second measurement path that can drift.

    What is evaluated, and what is not:

      * TRS is interpolated here, not delegated: LINEAR lerp for translation/scale, SLERP for
        rotation, STEP as a hold. Locals are composed up the hierarchy, so landmark positions are
        WORLD positions, which is what §1 measures.
      * CUBICSPLINE raises `UnsupportedInterpolation` naming the clip. Reading it as LINEAR would
        produce a feature vector that is confident and wrong.
      * `weights` (morph) channels are not evaluated at all -- they cannot move a node -- and are
        counted in `provenance.morphWeightChannelsIgnored`.
      * `stance` is omitted. This host cannot measure foot contact, and CONTRACT_1.5.2 says a host
        that cannot measure a field omits it rather than guessing.
    """
    missing = [name for name in REQUIRED_LANDMARKS if name not in landmark_node_indices]
    if missing:
        raise ValueError(
            f"landmark_node_indices is missing {', '.join(missing)}; §1 measures all six of "
            f"{', '.join(REQUIRED_LANDMARKS)} and a payload without them is rejected downstream"
        )
    if sample_count < 2:
        raise ValueError(f"sample_count must be at least 2; got {sample_count}")
    if figure_height is None:
        figure_height, height_source = derive_figure_height(glb_rig)
    else:
        height_source = "caller"
    if not math.isfinite(figure_height) or figure_height <= 0.0:
        raise ValueError(f"figure_height must be a finite positive number; got {figure_height!r}")

    nodes = _node_list(glb_rig._document)
    for name, node_index in landmark_node_indices.items():
        if not 0 <= int(node_index) < len(nodes):
            raise ValueError(f"landmark {name!r} points at node {node_index}, which does not exist")
    rest = [_node_rest_trs(node) for node in nodes]
    joint_node_indices = [joint.node_index for joint in glb_rig.joints]

    payload_clips: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    morph_channels_ignored = 0

    for clip in glb_rig.clips:
        duration = clip.duration
        if duration <= 0.0:
            skipped.append(
                {
                    "sourceName": clip.name,
                    "reason": "clip duration is zero (no channel carries a positive timeMax)",
                }
            )
            continue
        samplers = _load_samplers(glb_rig, clip)
        bound: list[tuple[GlbChannel, _Sampler]] = []
        for channel in clip.channels:
            sampler = samplers.get(channel.sampler_index)
            if sampler is None:
                continue
            if channel.path == "weights":
                morph_channels_ignored += 1
                continue
            if channel.path not in TRS_PATHS:
                continue
            if sampler.interpolation not in SUPPORTED_INTERPOLATIONS:
                raise UnsupportedInterpolation(clip.name, sampler.interpolation, f"path {channel.path!r}")
            bound.append((channel, sampler))

        times = [duration * index / (sample_count - 1) for index in range(sample_count)]
        landmark_positions: dict[str, list[list[float]]] = {name: [] for name in landmark_node_indices}
        joint_scale_delta: list[float] = []
        first_rotations: list[Quat] | None = None
        last_rotations: list[Quat] | None = None

        for sample_index, t in enumerate(times):
            world, trs = _pose_at(glb_rig, nodes, rest, bound, t, clip.name)
            for name, node_index in landmark_node_indices.items():
                position = matrix_translation(world[int(node_index)])
                landmark_positions[name].append([position[0], position[1], position[2]])
            if joint_node_indices:
                delta = 0.0
                for node_index in joint_node_indices:
                    if not 0 <= node_index < len(trs):
                        continue
                    scale = trs[node_index][2]
                    delta = max(delta, max(abs(axis - 1.0) for axis in scale))
                joint_scale_delta.append(delta if delta > SCALE_UNIT_EPSILON else 0.0)
            else:
                joint_scale_delta.append(0.0)
            rotations = [
                trs[node_index][1] for node_index in joint_node_indices if 0 <= node_index < len(trs)
            ]
            if sample_index == 0:
                first_rotations = rotations
            last_rotations = rotations

        if first_rotations and last_rotations and len(first_rotations) == len(last_rotations):
            pose_return = max(
                quaternion_angle_degrees(a, b) for a, b in zip(first_rotations, last_rotations)
            )
        else:
            # No joints to measure: omit the key rather than claim 0.0. CONTRACT_1.5.2 makes an
            # absent poseReturn mean "undecidable", which is the truth here.
            pose_return = None

        clip_payload: dict[str, Any] = {
            "sourceName": clip.name,
            "duration": duration,
            "sampleTimes": times,
            "landmarkPositions": landmark_positions,
            "jointScaleDelta": joint_scale_delta,
        }
        if pose_return is not None:
            clip_payload["poseReturn"] = pose_return
        payload_clips.append(clip_payload)

    return {
        "figureHeight": float(figure_height),
        "landmarks": list(landmark_node_indices.keys()),
        "clips": payload_clips,
        "provenance": {
            "source": glb_rig.path,
            "sampledFrom": "the GLB's own animation channels, evaluated by glb_rig_reference",
            "figureHeightSource": height_source,
            "sampleCount": sample_count,
            "landmarkNodeIndices": {name: int(index) for name, index in landmark_node_indices.items()},
            "jointCount": len(joint_node_indices),
            "morphWeightChannelsIgnored": morph_channels_ignored,
            "skippedClips": skipped,
            "stance": "omitted: this host cannot measure foot contact (CONTRACT_1.5.2 §stance)",
        },
    }


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------


def _load_landmarks(raw: str) -> dict[str, int]:
    path = Path(raw).expanduser()
    text = path.read_text(encoding="utf-8") if path.is_file() else raw
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("--landmarks must be a JSON object mapping landmark name -> node index")
    return {str(key): int(value) for key, value in parsed.items()}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Read a GLB's own rig as explicit, validated data.")
    parser.add_argument("glb", type=Path)
    parser.add_argument("--sample-clips", action="store_true", help="also emit a CONTRACT_1.5.2 sampled-clip payload")
    parser.add_argument("--landmarks", help="JSON object (path or literal) mapping the six §1 landmarks to node indices")
    parser.add_argument("--skin", type=int, help="choose one skin by index when the GLB carries several")
    parser.add_argument("--figure-height", type=float, help="figure height H; measured from mesh bounds when omitted")
    parser.add_argument("--sample-count", type=int, default=DEFAULT_SAMPLE_COUNT)
    parser.add_argument("--out", type=Path, help="also write the artifact")
    args = parser.parse_args(argv)

    errors: list[str] = []
    try:
        rig = read_rig(args.glb)
    except Exception as exc:  # noqa: BLE001 - the file itself is unreadable
        print(json.dumps({"ok": False, "kind": "glb-rig-reference", "errors": [str(exc)]}, indent=2))
        return 2

    if args.skin is not None:
        chosen = [skin for skin in rig.skins if skin.skin_index == args.skin]
        if not chosen:
            errors.append(f"--skin {args.skin} is not one of {[skin.skin_index for skin in rig.skins]}")
        else:
            rig.primary_skin = chosen[0]
            rig.structural_failure = None
            rig.deform_vs_technical = _deform_vs_technical(rig.skins, chosen[0], rig.clips, rig.nodes)

    if rig.structural_failure:
        errors.append(rig.structural_failure)

    result = rig.to_dict()

    if args.sample_clips:
        if not args.landmarks:
            errors.append("--sample-clips needs --landmarks; §1 measures six named world landmarks")
        else:
            try:
                payload = sample_clips(
                    rig,
                    _load_landmarks(args.landmarks),
                    sample_count=args.sample_count,
                    figure_height=args.figure_height,
                )
                result["sampledClips"] = payload
            except UnsupportedInterpolation as exc:
                errors.append(str(exc))
            except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
                errors.append(f"sampling failed: {exc}")

    result["ok"] = not errors
    result["errors"] = errors
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        output = args.out.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(output)
    print(rendered, end="")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
