"""D18 terminal 3D artifact integrity contract (Python 3.10, standard library).

``validate_artifact(workspace, artifact)`` accepts a JSON-shaped dict; ``load_artifact``
reads a workspace-relative manifest (default ``artifact.json``) and validates it.
The required schema is::

    {"objectId": "object-1", "outputKind": "3d-object|rigged-3d|3d-scene",
     "files": [{"path": "model.glb", "sha256": "<lowercase hex SHA-256>"}],
     "frame": {"units": "m", "up": "+Y", "forward": "+Z"},
     "pivot": "bottom-centre", "scaleEstimate": 1.0,
     "rig": null}

``scaleEstimate`` is finite positive metres per authored unit. A ``3d-scene`` also
requires ``roomDimensions: {width, height, depth}``, finite positive metres. Rigged
output requires ``rig: {skeletonSha, clips: [{name, sha256}]}``; an empty clip list
is valid for a static rig. Every rig digest references a distinct, separately
listed evidence file, not the model's digest. Evidence is nonempty structured JSON,
encoded as UTF-8 with ``json.dumps(sort_keys=True, separators=(',', ':'),
ensure_ascii=False, allow_nan=False)`` (no newline). Producers must extract actual
skeleton/bind-matrix and animation data, not invent those payloads.

Paths are canonical relative POSIX paths. Files must be nonempty regular files
inside the workspace, with no symlink components or duplicate paths/inodes. Each
listed GLB is checked as a binary glTF 2 container, not just by its extension.
``3d-object`` alternatively admits nonempty UTF-8 .js/.mjs/.ts/.tsx source (not
binary or JSON disguised by a source suffix); source is never executed here.
Scenes and rigs require a GLB. Other listed files are supporting evidence/assets.
Extra strict-JSON metadata, including provenance, is retained without mutation.

The returned independent dict adds/replaces ``validation`` with integrity scope
and explicitly unevaluated runtime, visual quality and rig parity. Validation does
NOT prove source exports a factory, geometry quality, the declared metric frame or
pivot matches geometry, or rig sidecars match the GLB. Actual producers and child
terminal gates must supply those measurements. This function never marks delivery
accepted. Missing required metadata/files/references raises ``ArtifactError`` with
``reason_code == 'missing-evidence'``; malformed, unsafe and mismatching evidence
has distinct reason codes. ``field`` identifies the failing metadata/path.
"""
from __future__ import annotations

import errno
import hashlib
import json
import math
import os
from pathlib import PurePosixPath
import re
import stat
import struct

__all__ = ["ArtifactError", "validate_artifact", "load_artifact"]

_OUTPUT_KINDS = {"3d-object", "rigged-3d", "3d-scene"}
_SOURCE_SUFFIXES = {".js", ".mjs", ".ts", ".tsx"}
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
_FRAME = {"units": "m", "up": "+Y", "forward": "+Z"}


class ArtifactError(ValueError):
    """Fail-closed artifact error with a stable machine-readable reason and field."""

    def __init__(self, reason_code, field=None):
        self.reason_code = reason_code
        self.field = field
        super().__init__(reason_code if field is None else f"{reason_code}: {field}")


def _fail(reason, field):
    raise ArtifactError(reason, field)


def _json_copy(value, field="artifact", depth=0):
    if depth > 64:
        _fail("invalid-json", field)
    if value is None or type(value) in (str, bool, int):
        if type(value) is str:
            try:
                value.encode("utf-8")
            except UnicodeError:
                _fail("invalid-json", field)
        return value
    if type(value) is float:
        if not math.isfinite(value):
            _fail("non-finite-number", field)
        return value
    if type(value) is list:
        return [_json_copy(item, field, depth + 1) for item in value]
    if type(value) is dict:
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                _fail("invalid-json", field)
            _json_copy(key, field, depth + 1)
            result[key] = _json_copy(item, f"{field}.{key}", depth + 1)
        return result
    _fail("invalid-json", field)


def _parse_json(data, field):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail("invalid-json", field)
            result[key] = value
        return result

    def constant(_value):
        _fail("non-finite-number", field)

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ArtifactError):
            raise
        raise ArtifactError("invalid-json", field) from exc
    return _json_copy(value, field)


def _required(record, key, field):
    if key not in record or record[key] is None:
        _fail("missing-evidence", f"{field}.{key}")
    return record[key]


def _positive(value, field):
    if type(value) not in (int, float) or value <= 0:
        _fail("invalid-metric", field)
    if type(value) is float and not math.isfinite(value):
        _fail("non-finite-number", field)


def _sha(value, field):
    if type(value) is not str or not _SHA.fullmatch(value):
        _fail("invalid-sha256", field)
    return value


def _relative_path(value):
    if (type(value) is not str or not value or "\\" in value or ":" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        _fail("unsafe-path", value if type(value) is str and value else "path")
    path = PurePosixPath(value)
    if (not path.parts or path.is_absolute() or str(path) != value
            or any(part in (".", "..") for part in path.parts)):
        _fail("unsafe-path", value)
    return path


def _io_error(exc, field):
    if isinstance(exc, FileNotFoundError):
        raise ArtifactError("missing-evidence", field) from exc
    reason = "unsafe-path" if exc.errno in (errno.ELOOP, errno.ENOTDIR) else "artifact-io-error"
    raise ArtifactError(reason, field) from exc


def _workspace_fd(workspace):
    try:
        root = os.fspath(workspace)
        if stat.S_ISLNK(os.lstat(root).st_mode):
            _fail("unsafe-path", "workspace")
        return os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ArtifactError):
            raise
        raise ArtifactError("unsafe-path", "workspace") from exc
    except OSError as exc:
        _io_error(exc, "workspace")


def _read_file(root_fd, value):
    """Open each component relative to a pinned directory; never follow symlinks."""
    path = _relative_path(value)
    parent_fd = os.dup(root_fd)
    try:
        for index, part in enumerate(path.parts):
            mode = os.stat(part, dir_fd=parent_fd, follow_symlinks=False).st_mode
            if stat.S_ISLNK(mode):
                _fail("unsafe-path", value)
            last = index == len(path.parts) - 1
            if last and not stat.S_ISREG(mode):
                _fail("invalid-file", value)
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if not last:
                flags |= os.O_DIRECTORY
            next_fd = os.open(part, flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
        before = os.fstat(parent_fd)
        if not stat.S_ISREG(before.st_mode):
            _fail("invalid-file", value)
        with os.fdopen(os.dup(parent_fd), "rb") as stream:
            data = stream.read()
        after = os.fstat(parent_fd)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            _fail("evidence-changed", value)
        if not data:
            _fail("missing-evidence", value)
        return data, (after.st_dev, after.st_ino)
    except OSError as exc:
        _io_error(exc, value)
    finally:
        os.close(parent_fd)


def _check_glb(data, field):
    """Strict D18 byte/container check; deliberately not a geometry/rig gate."""
    if len(data) < 20:
        _fail("invalid-glb", field)
    magic, version, length = struct.unpack_from("<4sII", data)
    if magic != b"glTF" or version != 2 or length != len(data):
        _fail("invalid-glb", field)
    cursor = 12
    document = None
    bin_seen = False
    while cursor < len(data):
        if cursor + 8 > len(data):
            _fail("invalid-glb", field)
        size, kind = struct.unpack_from("<II", data, cursor)
        end = cursor + 8 + size
        if size % 4 or end > len(data) or (cursor == 12 and kind != 0x4E4F534A):
            _fail("invalid-glb", field)
        if kind == 0x4E4F534A:
            if document is not None:
                _fail("invalid-glb", field)
            document = _parse_json(data[cursor + 8:end], field)
            if type(document) is not dict:
                _fail("invalid-glb", field)
        elif kind == 0x004E4942:
            if bin_seen:
                _fail("invalid-glb", field)
            bin_seen = True
        cursor = end
    if (type(document) is not dict or type(document.get("asset")) is not dict
            or document["asset"].get("version") != "2.0"):
        _fail("invalid-glb", field)


def _check_source(data, field):
    try:
        source = data.decode("utf-8")
    except UnicodeError as exc:
        raise ArtifactError("invalid-source", field) from exc
    if not source.strip() or any(ord(char) < 32 and char not in "\t\r\n" for char in source):
        _fail("invalid-source", field)
    # JSON is valid UTF-8 too; a suffix cannot turn a manifest into a factory.
    try:
        json.loads(source)
    except (ValueError, RecursionError):
        return
    _fail("invalid-source", field)


def _check_rig(rig, files, primary):
    if type(rig) is not dict:
        _fail("invalid-rig", "rig")
    skeleton = _sha(_required(rig, "skeletonSha", "rig"), "rig.skeletonSha")
    clips = _required(rig, "clips", "rig")
    if type(clips) is not list:
        _fail("invalid-rig", "rig.clips")
    references = [(skeleton, "rig.skeletonSha")]
    names = set()
    for index, clip in enumerate(clips):
        field = f"rig.clips[{index}]"
        if type(clip) is not dict:
            _fail("invalid-rig", field)
        name = _required(clip, "name", field)
        if type(name) is not str or not name.strip():
            _fail("invalid-rig", f"{field}.name")
        if name in names:
            _fail("duplicate-reference", f"{field}.name")
        names.add(name)
        references.append((_sha(_required(clip, "sha256", field), f"{field}.sha256"), field))
    seen = set()
    for digest, field in references:
        if digest in seen:
            _fail("duplicate-reference", field)
        seen.add(digest)
        matches = [item for item in files if item[1] == digest]
        if not matches:
            _fail("missing-evidence", field)
        if len(matches) != 1 or matches[0][0] in primary:
            _fail("invalid-rig-reference", field)
        data = matches[0][2]
        payload = _parse_json(data, field)
        if type(payload) not in (dict, list) or not payload:
            _fail("invalid-rig-evidence", field)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False).encode("utf-8")
        if data != canonical:
            _fail("noncanonical-evidence", field)


def validate_artifact(workspace, artifact) -> dict:
    """Return independent validated metadata; raise ArtifactError on missing/invalid evidence."""
    result = _json_copy(artifact)
    if type(result) is not dict:
        _fail("invalid-artifact", "artifact")
    object_id = _required(result, "objectId", "artifact")
    if type(object_id) is not str or not _ID.fullmatch(object_id):
        _fail("invalid-object-id", "objectId")
    kind = _required(result, "outputKind", "artifact")
    if type(kind) is not str or kind not in _OUTPUT_KINDS:
        _fail("unsupported-output-kind", "outputKind")
    if _required(result, "frame", "artifact") != _FRAME:
        _fail("invalid-frame", "frame")
    if _required(result, "pivot", "artifact") != "bottom-centre":
        _fail("invalid-pivot", "pivot")
    _positive(_required(result, "scaleEstimate", "artifact"), "scaleEstimate")
    if kind == "3d-scene" or "roomDimensions" in result:
        dimensions = _required(result, "roomDimensions", "artifact")
        if type(dimensions) is not dict or set(dimensions) - {"width", "height", "depth"}:
            _fail("invalid-room-dimensions", "roomDimensions")
        for axis in ("width", "height", "depth"):
            _positive(_required(dimensions, axis, "roomDimensions"), f"roomDimensions.{axis}")
    if "rig" not in result:
        _fail("missing-evidence", "rig")
    rig = result["rig"]
    if kind == "rigged-3d" and rig is None:
        _fail("missing-evidence", "rig")
    entries = _required(result, "files", "artifact")
    if type(entries) is not list:
        _fail("invalid-files", "files")
    if not entries:
        _fail("missing-evidence", "files")
    paths, identities, primary = set(), set(), set()
    files = []
    glb_count = 0
    root_fd = _workspace_fd(workspace)
    try:
        for index, entry in enumerate(entries):
            field = f"files[{index}]"
            if type(entry) is not dict:
                _fail("invalid-files", field)
            path = _required(entry, "path", field)
            relative = _relative_path(path)
            if path in paths:
                _fail("duplicate-reference", path)
            paths.add(path)
            digest = _sha(_required(entry, "sha256", field), f"{field}.sha256")
            data, identity = _read_file(root_fd, path)
            if identity in identities:
                _fail("duplicate-reference", path)
            identities.add(identity)
            if hashlib.sha256(data).hexdigest() != digest:
                _fail("hash-mismatch", path)
            suffix = relative.suffix.lower()
            if suffix == ".glb":
                _check_glb(data, path)
                glb_count += 1
                primary.add(path)
            elif suffix in _SOURCE_SUFFIXES:
                _check_source(data, path)
                primary.add(path)
            files.append((path, digest, data))
        if not primary or ((kind != "3d-object" or rig is not None) and not glb_count):
            _fail("missing-evidence", "files.model")
        if rig is not None:
            _check_rig(rig, files, primary)
    finally:
        os.close(root_fd)
    result["validation"] = {
        "scope": "artifact-integrity", "runtime": "unevaluated",
        "visualQuality": "unevaluated", "rigParity": "unevaluated",
    }
    return result


def load_artifact(workspace, artifact_path="artifact.json") -> dict:
    """Read a strict JSON manifest inside workspace, then validate its actual file bytes."""
    root_fd = _workspace_fd(workspace)
    try:
        data, _identity = _read_file(root_fd, artifact_path)
    finally:
        os.close(root_fd)
    return validate_artifact(workspace, _parse_json(data, artifact_path))
