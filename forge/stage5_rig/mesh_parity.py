#!/usr/bin/env python3
"""Stage R — mesh geometry freeze and the parity gate that proves the freeze held.

WHY THIS EXISTS
---------------
Rigging a model that is already built is meant to be an ADDITIVE operation: a skeleton appears, two
new vertex attributes appear, some clips appear. Nothing about it requires touching a single
position, normal, uv or index. In practice the animation pass is exactly where meshes come back
disjointed -- a limb regenerated at a different resolution, an index buffer rewired by a "helpful"
weld, one vertex nudged so a joint looked better in one frame -- and by the time the damage is
visible it is buried under a rig that was built on top of it, so it is no longer clear whether the
mesh or the skinning is at fault.

So the geometry is FROZEN. A mesh-repair phase may fix broken meshes first; then `freeze` records
every geometry buffer and its hash, and from that point the implementation phase may only ADD.
`verify` re-hashes the same buffers afterwards and proves, per mesh and per attribute, that nothing
moved.

WHAT IS FROZEN, AND THE ONE THING THAT IS DELIBERATELY NOT
-----------------------------------------------------------
FROZEN_ATTRIBUTES = ("position", "normal", "uv"), plus "index" -- together FROZEN_BUFFERS.

`skinIndex` and `skinWeight` are DELIBERATELY NOT FROZEN. Adding them is the entire legal purpose of
the implementation phase: a mesh that carried no skinning attributes before rigging and carries them
afterwards is the pass condition, not a violation. Freezing them would make every successful rig
fail this gate, and leaving position out of the frozen set would make the gate prove nothing. That
split is the load-bearing design decision of this module. Any attribute outside FROZEN_BUFFERS that
turns up after the freeze is therefore reported as "added (legal)" rather than as a change -- and,
symmetrically, a FROZEN attribute that appears only after the freeze is a failure, because adding a
uv set post-freeze changes what the surface looks like just as surely as moving it does.

WHY THE HASH IS OVER PACKED BYTES AND NOT OVER THE JSON TEXT
-------------------------------------------------------------
The payload arrives as JSON from a browser harness and is written back out as JSON at least once
more. JSON float formatting is not canonical: `1.0`, `1`, `1e0` and `1.0000000000000002` are choices
a serialiser makes, and two serialisers -- or two versions of one -- may spell the same double
differently. Hashing the text would therefore report a change every time a formatter changed its
mind, which is the fastest way to teach everyone to ignore the gate.

So the hash is taken over the PARSED numbers, re-serialised here into a byte layout this module
defines and controls: little-endian IEEE-754 float64 for the float buffers, little-endian
two's-complement int64 for the index buffer. Two payloads that parse to the same numbers hash
identically however they were spelled, and two payloads that parse to different numbers cannot be
made to collide by formatting.

The layout is bit-exact, which is the point and worth stating: `0.0` and `-0.0` are different bytes
and therefore a reported change, and two NaNs sharing a bit pattern are the same bytes and therefore
not one.

WHY THE MANIFEST KEEPS THE BUFFERS AS WELL AS THEIR HASHES
-----------------------------------------------------------
A hash answers "did it change?" and nothing else. Fired on a 200k-element position buffer, "the hash
differs" hands a human two megabytes of numbers and a diff tool. So the manifest retains the frozen
buffers next to their hashes, and a hash mismatch is followed by a linear scan -- run ONLY after a
mismatch -- that names the first differing element and both of its values. "element 37:
0.5 -> 0.5001, 1 of 72,000 elements differ" is a completely different bug report from "71,998 of
72,000 elements differ", and only the scan can tell them apart.

The scan compares the same packed bytes the hash consumed, eight bytes per element, so the scan and
the hash cannot disagree about what counts as a difference. When the scan finds NO differing element
after the hash said there was one, that is not a contradiction to hide: it means the manifest's
recorded hash disagrees with the manifest's own retained buffer, i.e. the manifest was edited, and
it is reported as exactly that.

Pure Python 3.10+ stdlib. No pip installs, no numpy, no three.js.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# The vertex attributes whose bytes may not change once frozen.
FROZEN_ATTRIBUTES: tuple[str, ...] = ("position", "normal", "uv")
# The index buffer is frozen on the same terms; it lives beside `attributes` in the payload rather
# than inside it, so it is named separately and then folded into one ordered tuple.
INDEX_KEY = "index"
FROZEN_BUFFERS: tuple[str, ...] = FROZEN_ATTRIBUTES + (INDEX_KEY,)

# Every frozen buffer is packed to 8 bytes per element -- float64 or int64 -- so the mismatch scan
# can walk the packed bytes at a fixed stride instead of re-deriving each element's encoding.
ELEMENT_BYTES = 8

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_UNEVALUATED = "unevaluated"

# The kinds of change a failure can report. Naming the kind (not just the mesh) is what lets a
# reader tell "someone moved a vertex" from "someone rebuilt the mesh" without opening the payload.
KIND_HASH = "hash mismatch"
KIND_MESH_MISSING = "mesh missing"
KIND_MESH_ADDED = "mesh added"
KIND_COUNT = "count changed"
KIND_ATTRIBUTE_MISSING = "attribute missing"
KIND_ATTRIBUTE_ADDED = "frozen attribute added"
KIND_DUPLICATE = "duplicate mesh name"
KIND_MALFORMED = "malformed buffer"

KINDS: tuple[str, ...] = (
    KIND_HASH,
    KIND_MESH_MISSING,
    KIND_MESH_ADDED,
    KIND_COUNT,
    KIND_ATTRIBUTE_MISSING,
    KIND_ATTRIBUTE_ADDED,
    KIND_DUPLICATE,
    KIND_MALFORMED,
)

# A failure list is for a human to read. Past this many the list stops being readable and the total
# count is the useful part.
_MAX_REPORTED_FAILURES = 20
# Per mismatched buffer: the first differing element is the diagnosis, the next few show whether it
# is a nudge or a rebuild. Everything past that is the same information repeated.
_MAX_REPORTED_DIFFS = 8
# Legal additions are informational, not evidence; a handful is enough to see what the rig added.
_MAX_REPORTED_ADDITIONS = 20

_ORDER = {name: position for position, name in enumerate(FROZEN_BUFFERS)}

FREEZE_NOTE = (
    "Geometry frozen. Implementation may add a skeleton, skinIndex/skinWeight and clips; it may not "
    "change position, normal, uv or index on any mesh, and it may not add or remove meshes."
)
_NO_MANIFEST_REASON = (
    "no freeze manifest was supplied, so there is nothing to compare the payload against; geometry "
    "parity is unproven, which is not the same as unbroken"
)
_EMPTY_MANIFEST_REASON = (
    "the freeze manifest records no meshes, so no geometry was ever frozen; geometry parity is "
    "unproven, which is not the same as unbroken"
)


# ---------------------------------------------------------------------------------------------
# Byte layout
# ---------------------------------------------------------------------------------------------


def _as_floats(values: Any, label: str) -> list[float]:
    if not isinstance(values, list):
        raise ValueError(f"{label} must be a list of numbers, got {type(values).__name__}")
    out: list[float] = []
    for position, value in enumerate(values):
        # bool is an int subclass; `True` in a position buffer is a producer bug, not a 1.0.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{label}[{position}] is {value!r}; a float buffer holds numbers")
        out.append(float(value))
    return out


def _as_ints(values: Any, label: str) -> list[int]:
    if not isinstance(values, list):
        raise ValueError(f"{label} must be a list of integers, got {type(values).__name__}")
    out: list[int] = []
    for position, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{label}[{position}] is {value!r}; an index buffer holds integers")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError(
                f"{label}[{position}] is {value!r}; an index buffer holds whole numbers. A "
                "fractional index means the buffer was resampled, not re-serialised."
            )
        out.append(int(value))
    return out


def _packed(name: str, values: list[float] | list[int]) -> bytes:
    """The canonical bytes of one frozen buffer: little-endian float64, or int64 for the index.

    `array` is used rather than `struct.pack("<%dd")` because the latter needs every element as a
    separate call argument, which stops being reasonable somewhere below a real mesh's vertex count.
    `array` is native-endian, so a big-endian host byteswaps -- without that, the same mesh would
    hash differently on different machines and the gate would fire on the CI box alone.
    """
    code = "q" if name == INDEX_KEY else "d"
    try:
        buffer = array(code, values)
    except OverflowError as exc:
        raise ValueError(f"{name} does not fit a 64-bit integer buffer: {exc}") from exc
    if sys.byteorder != "little":
        buffer.byteswap()
    return buffer.tobytes()


def _hash_buffer(name: str, values: list[float] | list[int]) -> str:
    """SHA-256 over the packed bytes, prefixed with the buffer's identity and length.

    The prefix is domain separation: a normal buffer that happens to hold the same numbers as a
    position buffer must not produce the same digest, so a hash can never be quietly moved from one
    slot to another in a hand-edited manifest.
    """
    tag = "i64le" if name == INDEX_KEY else "f64le"
    digest = hashlib.sha256()
    digest.update(f"{name}|{len(values)}|{tag}|".encode("utf-8"))
    digest.update(_packed(name, values))
    return digest.hexdigest()


# ---------------------------------------------------------------------------------------------
# Payload reading
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _PayloadMesh:
    """One mesh as it arrives: the frozen buffers converted, everything else recorded by name only.

    Non-frozen attributes are never converted or hashed. Their VALUES are none of this gate's
    business -- skinWeight is supposed to change -- so only their existence is recorded.
    """

    name: str
    buffers: dict[str, list[float] | list[int]]
    extra_attributes: tuple[str, ...]


def _read_meshes(payload: Any) -> list[_PayloadMesh]:
    if not isinstance(payload, dict):
        raise ValueError("payload root must be an object")
    raw = payload.get("meshes")
    if not isinstance(raw, list):
        raise ValueError("payload must carry a `meshes` array")

    meshes: list[_PayloadMesh] = []
    for position, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"meshes[{position}] must be an object")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"meshes[{position}].name must be a non-empty string")
        attributes = entry.get("attributes", {})
        if not isinstance(attributes, dict):
            raise ValueError(f"mesh {name!r}: `attributes` must be an object")
        if INDEX_KEY in attributes:
            raise ValueError(
                f"mesh {name!r}: `index` must sit beside `attributes`, not inside it. Reading both "
                "spellings would let a payload carry two different index buffers."
            )

        buffers: dict[str, list[float] | list[int]] = {}
        extras: list[str] = []
        for key, values in attributes.items():
            if key in FROZEN_ATTRIBUTES:
                buffers[key] = _as_floats(values, f"mesh {name!r} attribute {key!r}")
            else:
                if not isinstance(values, list):
                    raise ValueError(f"mesh {name!r} attribute {key!r} must be a list")
                extras.append(key)
        if entry.get(INDEX_KEY) is not None:
            buffers[INDEX_KEY] = _as_ints(entry[INDEX_KEY], f"mesh {name!r} index")

        meshes.append(
            _PayloadMesh(name=name, buffers=buffers, extra_attributes=tuple(sorted(extras)))
        )
    return meshes


def _duplicates(names: list[str]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return sorted((name, count) for name, count in counts.items() if count > 1)


def load_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("payload root must be an object")
    return payload


# ---------------------------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MeshFingerprint:
    """What was frozen for one mesh: the counts, the hashes, and the buffers themselves."""

    name: str
    vertex_count: int
    index_count: int
    attributes_present: tuple[str, ...]
    attribute_hashes: dict[str, str]
    buffers: dict[str, list[float] | list[int]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "vertexCount": self.vertex_count,
            "indexCount": self.index_count,
            "attributes": list(self.attributes_present),
            "hashes": dict(self.attribute_hashes),
            "buffers": {name: list(values) for name, values in self.buffers.items()},
        }

    @staticmethod
    def from_dict(entry: dict[str, Any]) -> "MeshFingerprint":
        try:
            name = entry["name"]
            hashes = dict(entry["hashes"])
            attributes = tuple(entry["attributes"])
            raw_buffers = entry["buffers"]
        except KeyError as exc:
            raise ValueError(f"manifest mesh is missing {exc.args[0]!r}") from exc
        if set(attributes) != set(hashes) or set(attributes) != set(raw_buffers):
            raise ValueError(
                f"manifest mesh {name!r} disagrees with itself: attributes {sorted(attributes)}, "
                f"hashes {sorted(hashes)}, buffers {sorted(raw_buffers)}"
            )
        buffers: dict[str, list[float] | list[int]] = {}
        for key, values in raw_buffers.items():
            label = f"manifest mesh {name!r} buffer {key!r}"
            buffers[key] = (
                _as_ints(values, label) if key == INDEX_KEY else _as_floats(values, label)
            )
        return MeshFingerprint(
            name=name,
            vertex_count=int(entry["vertexCount"]),
            index_count=int(entry["indexCount"]),
            attributes_present=attributes,
            attribute_hashes=hashes,
            buffers=buffers,
        )


@dataclass(frozen=True)
class Manifest:
    """The frozen record. Everything downstream of a freeze is checked against this and only this."""

    schema_version: int
    frozen_attributes: tuple[str, ...]
    meshes: tuple[MeshFingerprint, ...]

    def mesh(self, name: str) -> MeshFingerprint | None:
        for fingerprint in self.meshes:
            if fingerprint.name == name:
                return fingerprint
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "kind": "mesh-freeze-manifest",
            "frozenAttributes": list(self.frozen_attributes),
            "meshes": [fingerprint.to_dict() for fingerprint in self.meshes],
            "note": FREEZE_NOTE,
        }

    def to_summary_dict(self) -> dict[str, Any]:
        """The manifest without the retained buffers -- what a human reads, not what verify eats."""
        return {
            "schemaVersion": self.schema_version,
            "kind": "mesh-freeze-summary",
            "frozenAttributes": list(self.frozen_attributes),
            "meshes": [
                {
                    "name": fingerprint.name,
                    "vertexCount": fingerprint.vertex_count,
                    "indexCount": fingerprint.index_count,
                    "attributes": list(fingerprint.attributes_present),
                    "hashes": dict(fingerprint.attribute_hashes),
                }
                for fingerprint in self.meshes
            ],
            "note": FREEZE_NOTE,
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "Manifest":
        if not isinstance(payload, dict):
            raise ValueError("manifest root must be an object")
        meshes = payload.get("meshes")
        if not isinstance(meshes, list):
            raise ValueError("manifest must carry a `meshes` array")
        return Manifest(
            schema_version=int(payload.get("schemaVersion", -1)),
            frozen_attributes=tuple(payload.get("frozenAttributes", ())),
            meshes=tuple(MeshFingerprint.from_dict(entry) for entry in meshes),
        )


def freeze(payload: dict[str, Any]) -> Manifest:
    """Record every frozen buffer of every mesh. The payload is read, never modified.

    Duplicate mesh names are refused here rather than recorded, because a manifest is keyed by name:
    two meshes called "arm" could not be told apart afterwards, and a gate that cannot tell two
    things apart will eventually pass the wrong one. `verify` reports duplicates instead of raising
    -- it is a gate, and a gate that crashes on bad input reports nothing at all.
    """
    meshes = _read_meshes(payload)
    duplicates = _duplicates([mesh.name for mesh in meshes])
    if duplicates:
        listed = ", ".join(f"{name!r} x{count}" for name, count in duplicates)
        raise ValueError(
            f"cannot freeze: duplicate mesh names ({listed}). A manifest is keyed by name, so these "
            "meshes could not be told apart at verify time."
        )

    fingerprints: list[MeshFingerprint] = []
    for mesh in meshes:
        buffers = {name: mesh.buffers[name] for name in FROZEN_BUFFERS if name in mesh.buffers}
        position = buffers.get("position")
        if position is None:
            raise ValueError(
                f"cannot freeze mesh {mesh.name!r}: it has no `position` attribute, so there is no "
                "geometry to freeze"
            )
        if len(position) % 3 != 0:
            raise ValueError(
                f"cannot freeze mesh {mesh.name!r}: position holds {len(position)} floats, which is "
                "not a whole number of xyz vertices"
            )
        fingerprints.append(
            MeshFingerprint(
                name=mesh.name,
                vertex_count=len(position) // 3,
                index_count=len(buffers.get(INDEX_KEY, ())),
                attributes_present=tuple(sorted(buffers, key=lambda key: _ORDER[key])),
                attribute_hashes={
                    name: _hash_buffer(name, values) for name, values in buffers.items()
                },
                buffers={name: list(values) for name, values in buffers.items()},
            )
        )

    return Manifest(
        schema_version=SCHEMA_VERSION,
        frozen_attributes=FROZEN_BUFFERS,
        meshes=tuple(fingerprints),
    )


# ---------------------------------------------------------------------------------------------
# Parity report
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ElementDifference:
    """One element that moved: where it is, what it was, what it became."""

    index: int
    frozen: float | int
    current: float | int

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "frozen": self.frozen, "current": self.current}


@dataclass(frozen=True)
class ParityFailure:
    """A single violation, named down to the mesh, the buffer and the kind of change."""

    mesh: str
    attribute: str | None
    kind: str
    detail: str
    differing_elements: int = 0
    differences: tuple[ElementDifference, ...] = ()

    def __str__(self) -> str:
        where = f"{self.mesh}.{self.attribute}" if self.attribute else self.mesh
        return f"{where}: {self.kind} -- {self.detail}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mesh": self.mesh,
            "attribute": self.attribute,
            "kind": self.kind,
            "detail": self.detail,
            "differingElements": self.differing_elements,
            "differences": [difference.to_dict() for difference in self.differences],
        }


@dataclass(frozen=True)
class ParityReport:
    """The gate's answer. `status` is one of pass / fail / unevaluated; only pass is `ok`."""

    status: str
    meshes_frozen: int
    meshes_compared: int
    failure_count: int
    failures: tuple[ParityFailure, ...]
    addition_count: int
    additions: tuple[str, ...]
    reason: str | None = None

    @property
    def ok(self) -> bool:
        # `unevaluated` is deliberately not ok. A gate whose input never arrived has not passed; it
        # has not run.
        return self.status == STATUS_PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "mesh-parity-report",
            "status": self.status,
            "ok": self.ok,
            "reason": self.reason,
            "frozenAttributes": list(FROZEN_BUFFERS),
            "meshesFrozen": self.meshes_frozen,
            "meshesCompared": self.meshes_compared,
            "failureCount": self.failure_count,
            "failures": [failure.to_dict() for failure in self.failures],
            "addedLegalCount": self.addition_count,
            "addedLegal": list(self.additions),
            "summary": self.summary(),
        }

    def summary(self) -> str:
        headers = ("KIND", "MESH", "ATTRIBUTE")
        lines = [
            f"mesh parity: {self.status.upper()}   (schema {SCHEMA_VERSION}, frozen: "
            + ", ".join(FROZEN_BUFFERS)
            + ")",
            f"  meshes frozen {self.meshes_frozen}   compared {self.meshes_compared}   "
            f"failures {self.failure_count}   added (legal) {self.addition_count}",
        ]
        if self.reason:
            lines.append(f"  reason: {self.reason}")

        if self.failures:
            rows = [
                (failure.kind, failure.mesh, failure.attribute or "-", failure.detail)
                for failure in self.failures
            ]
            widths = [
                max(len(header), max(len(row[column]) for row in rows))
                for column, header in enumerate(headers)
            ]
            lines.append(
                "  "
                + "  ".join(header.ljust(widths[column]) for column, header in enumerate(headers))
                + "  DETAIL"
            )
            for row in rows:
                lines.append(
                    "  "
                    + "  ".join(row[column].ljust(widths[column]) for column in range(3))
                    + "  "
                    + row[3]
                )
            if self.failure_count > len(self.failures):
                lines.append(
                    f"  ... {self.failure_count - len(self.failures)} further failures not listed"
                )

        if self.additions:
            lines.append("  added (legal): " + ", ".join(self.additions))
            if self.addition_count > len(self.additions):
                lines.append(
                    f"  ... {self.addition_count - len(self.additions)} further additions not listed"
                )
        return "\n".join(lines)


def _first_differences(
    name: str, frozen: list[float] | list[int], current: list[float] | int | list[int]
) -> tuple[int, tuple[ElementDifference, ...]]:
    """Locate the differing elements of two equal-length buffers, after a hash already said so.

    Compared as PACKED BYTES, not with `==`, so the scan agrees with the hash by construction: the
    same -0.0 / 0.0 pair that the hash calls a difference is a difference here, and the same NaN bit
    pattern that the hash calls equal is equal here. A float comparison would disagree with the hash
    on both, and a gate whose diagnosis contradicts its own verdict is worse than no diagnosis.
    """
    left = _packed(name, frozen)
    right = _packed(name, current)  # type: ignore[arg-type]
    differing = 0
    found: list[ElementDifference] = []
    for position in range(len(frozen)):
        start = position * ELEMENT_BYTES
        if left[start : start + ELEMENT_BYTES] != right[start : start + ELEMENT_BYTES]:
            differing += 1
            if len(found) < _MAX_REPORTED_DIFFS:
                found.append(
                    ElementDifference(
                        index=position,
                        frozen=frozen[position],
                        current=current[position],  # type: ignore[index]
                    )
                )
    return differing, tuple(found)


def _compare_mesh(
    fingerprint: MeshFingerprint, mesh: _PayloadMesh
) -> tuple[list[ParityFailure], list[str]]:
    failures: list[ParityFailure] = []
    additions: list[str] = []

    frozen_names = set(fingerprint.attribute_hashes)
    present = set(mesh.buffers)

    for name in sorted(frozen_names - present, key=lambda key: _ORDER[key]):
        failures.append(
            ParityFailure(
                mesh=mesh.name,
                attribute=name,
                kind=KIND_ATTRIBUTE_MISSING,
                detail=f"{name} was frozen with {len(fingerprint.buffers[name])} values and is "
                "absent from the payload",
            )
        )
    for name in sorted(present - frozen_names, key=lambda key: _ORDER[key]):
        failures.append(
            ParityFailure(
                mesh=mesh.name,
                attribute=name,
                kind=KIND_ATTRIBUTE_ADDED,
                detail=f"{name} is a frozen-class buffer that did not exist at freeze time; adding "
                "one changes the surface just as moving it does",
            )
        )
    for name in mesh.extra_attributes:
        additions.append(f"{mesh.name}.{name}")

    common = frozen_names & present

    # Counts are checked against the manifest's RECORDED counts, not against the length of its
    # retained buffers -- so a manifest whose hashes were recomputed to match a shrunken mesh still
    # fails here, on the count it forgot to update.
    counted: set[str] = set()
    if "position" in common:
        position = mesh.buffers["position"]
        if len(position) % 3 != 0:
            failures.append(
                ParityFailure(
                    mesh=mesh.name,
                    attribute="position",
                    kind=KIND_MALFORMED,
                    detail=f"position holds {len(position)} floats, which is not a whole number of "
                    "xyz vertices",
                )
            )
            counted.add("position")
        elif len(position) // 3 != fingerprint.vertex_count:
            failures.append(
                ParityFailure(
                    mesh=mesh.name,
                    attribute=None,
                    kind=KIND_COUNT,
                    detail=f"vertexCount {fingerprint.vertex_count} -> {len(position) // 3}",
                )
            )
            counted.add("position")
    if INDEX_KEY in common and len(mesh.buffers[INDEX_KEY]) != fingerprint.index_count:
        failures.append(
            ParityFailure(
                mesh=mesh.name,
                attribute=INDEX_KEY,
                kind=KIND_COUNT,
                detail=f"indexCount {fingerprint.index_count} -> {len(mesh.buffers[INDEX_KEY])}",
            )
        )
        counted.add(INDEX_KEY)

    for name in sorted(common, key=lambda key: _ORDER[key]):
        frozen_values = fingerprint.buffers[name]
        current_values = mesh.buffers[name]
        if len(frozen_values) != len(current_values):
            # A length change is a count change, and the hash mismatch it also causes says nothing
            # extra. Reporting both would double every truncation.
            if name not in counted:
                failures.append(
                    ParityFailure(
                        mesh=mesh.name,
                        attribute=name,
                        kind=KIND_COUNT,
                        detail=f"{name} holds {len(current_values)} values, frozen with "
                        f"{len(frozen_values)}",
                    )
                )
            continue
        if _hash_buffer(name, current_values) == fingerprint.attribute_hashes[name]:
            continue
        differing, differences = _first_differences(name, frozen_values, current_values)
        if differing == 0:
            detail = (
                "the payload buffer matches the manifest's own retained buffer byte for byte but "
                "not its recorded hash: the manifest has been edited and cannot be trusted"
            )
        else:
            first = differences[0]
            detail = (
                f"first difference at element {first.index}: {first.frozen!r} -> "
                f"{first.current!r} ({differing} of {len(frozen_values)} elements differ)"
            )
        failures.append(
            ParityFailure(
                mesh=mesh.name,
                attribute=name,
                kind=KIND_HASH,
                detail=detail,
                differing_elements=differing,
                differences=differences,
            )
        )

    return failures, additions


def verify(manifest: Manifest | None, payload: dict[str, Any]) -> ParityReport:
    """Re-hash the payload against the freeze manifest. Meshes are matched BY NAME, never by order.

    Order matching would be a silent trap: an implementation that reorders a scene graph -- which is
    legal and common -- would be compared limb against torso and would fail for the wrong reason,
    while an implementation that swapped two meshes' geometry would pass. Names are the only stable
    identity, which is also why duplicate names are a structural failure rather than something to
    zip through.
    """
    if manifest is None:
        return ParityReport(
            status=STATUS_UNEVALUATED,
            meshes_frozen=0,
            meshes_compared=0,
            failure_count=0,
            failures=(),
            addition_count=0,
            additions=(),
            reason=_NO_MANIFEST_REASON,
        )
    if manifest.schema_version != SCHEMA_VERSION:
        return ParityReport(
            status=STATUS_UNEVALUATED,
            meshes_frozen=len(manifest.meshes),
            meshes_compared=0,
            failure_count=0,
            failures=(),
            addition_count=0,
            additions=(),
            reason=f"manifest schemaVersion {manifest.schema_version} is not {SCHEMA_VERSION}; this "
            "module cannot say what its hashes cover",
        )
    if tuple(manifest.frozen_attributes) != FROZEN_BUFFERS:
        return ParityReport(
            status=STATUS_UNEVALUATED,
            meshes_frozen=len(manifest.meshes),
            meshes_compared=0,
            failure_count=0,
            failures=(),
            addition_count=0,
            additions=(),
            reason="manifest was frozen over "
            f"{list(manifest.frozen_attributes)}, this module freezes {list(FROZEN_BUFFERS)}; a "
            "comparison would prove something other than what it claims",
        )
    if not manifest.meshes:
        return ParityReport(
            status=STATUS_UNEVALUATED,
            meshes_frozen=0,
            meshes_compared=0,
            failure_count=0,
            failures=(),
            addition_count=0,
            additions=(),
            reason=_EMPTY_MANIFEST_REASON,
        )

    current = _read_meshes(payload)
    failures: list[ParityFailure] = []
    additions: list[str] = []

    manifest_duplicates = _duplicates([fingerprint.name for fingerprint in manifest.meshes])
    payload_duplicates = _duplicates([mesh.name for mesh in current])
    for name, count in manifest_duplicates:
        failures.append(
            ParityFailure(
                mesh=name,
                attribute=None,
                kind=KIND_DUPLICATE,
                detail=f"the freeze manifest records {count} meshes named {name!r}; meshes are "
                "matched by name and these cannot be told apart",
            )
        )
    for name, count in payload_duplicates:
        failures.append(
            ParityFailure(
                mesh=name,
                attribute=None,
                kind=KIND_DUPLICATE,
                detail=f"the payload carries {count} meshes named {name!r}; meshes are matched by "
                "name and these cannot be told apart",
            )
        )
    ambiguous = {name for name, _ in manifest_duplicates} | {name for name, _ in payload_duplicates}

    by_name = {mesh.name: mesh for mesh in current if mesh.name not in ambiguous}
    frozen_names = {fingerprint.name for fingerprint in manifest.meshes}
    compared = 0

    for fingerprint in manifest.meshes:
        if fingerprint.name in ambiguous:
            continue
        mesh = by_name.get(fingerprint.name)
        if mesh is None:
            failures.append(
                ParityFailure(
                    mesh=fingerprint.name,
                    attribute=None,
                    kind=KIND_MESH_MISSING,
                    detail=f"frozen with {fingerprint.vertex_count} vertices and absent from the "
                    "payload; implementation may not remove visible geometry",
                )
            )
            continue
        mesh_failures, mesh_additions = _compare_mesh(fingerprint, mesh)
        failures.extend(mesh_failures)
        additions.extend(mesh_additions)
        compared += 1

    for mesh in current:
        if mesh.name in ambiguous or mesh.name in frozen_names:
            continue
        vertices = len(mesh.buffers.get("position", ())) // 3
        failures.append(
            ParityFailure(
                mesh=mesh.name,
                attribute=None,
                kind=KIND_MESH_ADDED,
                detail=f"present in the payload with {vertices} vertices and not in the manifest; "
                "implementation may not create new visible geometry either",
            )
        )

    return ParityReport(
        status=STATUS_FAIL if failures else STATUS_PASS,
        meshes_frozen=len(manifest.meshes),
        meshes_compared=compared,
        failure_count=len(failures),
        failures=tuple(failures[:_MAX_REPORTED_FAILURES]),
        addition_count=len(additions),
        additions=tuple(additions[:_MAX_REPORTED_ADDITIONS]),
    )


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def _run_freeze(args: argparse.Namespace) -> int:
    manifest = freeze(load_payload(args.payload))
    document = json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.expanduser().parent.mkdir(parents=True, exist_ok=True)
        args.out.expanduser().write_text(document, encoding="utf-8")
        # stdout gets the manifest WITHOUT its retained buffers. The buffers are a copy of the whole
        # mesh; printing them would bury the hashes a human came to read.
        print(json.dumps(manifest.to_summary_dict(), indent=2, ensure_ascii=False))
    else:
        print(document, end="")
    return 0


def _run_verify(args: argparse.Namespace) -> int:
    manifest = Manifest.from_dict(
        json.loads(args.manifest.expanduser().read_text(encoding="utf-8"))
    )
    report = verify(manifest, load_payload(args.payload))
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    return 0 if report.ok else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Freeze mesh geometry before rigging and prove afterwards that it never moved."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    freezer = subcommands.add_parser("freeze", help="record the frozen buffers and their hashes")
    freezer.add_argument("payload", type=Path, help="JSON mesh payload")
    freezer.add_argument("--out", type=Path, help="write the full manifest here")
    freezer.set_defaults(handler=_run_freeze)

    verifier = subcommands.add_parser("verify", help="re-hash a payload against a freeze manifest")
    verifier.add_argument("manifest", type=Path, help="manifest written by `freeze --out`")
    verifier.add_argument("payload", type=Path, help="JSON mesh payload to check")
    verifier.set_defaults(handler=_run_verify)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
