"""Immutable dense-evidence records and local provider-run validation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class InfluenceScope(str, Enum):
    NONE = "none"
    GLOBAL_MASSING = "global-massing"
    COMPONENT_MEASUREMENTS = "component-measurements"


SCOPE_RANK = {
    InfluenceScope.NONE: 0,
    InfluenceScope.GLOBAL_MASSING: 1,
    InfluenceScope.COMPONENT_MEASUREMENTS: 2,
}


class DenseEvidenceError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(f"{category}: {message}")
        self.category = category


@dataclass(frozen=True)
class ProviderRun:
    root: Path
    source_image_sha256: tuple[str, ...]
    glb_sha256: str
    obj_sha256: str
    provider_id: str
    structural_status: str
    visual_review_status: str
    semantic_status: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_json_atomic(path: Path, value: object) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_json_object(path: Path, category: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise DenseEvidenceError(category, f"missing artifact: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise DenseEvidenceError(category, f"unreadable artifact: {path}: {error}") from error
    if not isinstance(value, dict):
        raise DenseEvidenceError(category, f"artifact must contain a JSON object: {path}")
    return value


def _receipt_source_hashes(receipt: dict[str, Any], request: dict[str, Any]) -> tuple[str, ...]:
    direct = receipt.get("sourceImageSha256")
    if isinstance(direct, list) and all(isinstance(item, str) for item in direct):
        return tuple(direct)
    images = request.get("images")
    if isinstance(images, list):
        hashes = tuple(
            str(item.get("sha256"))
            for item in images
            if isinstance(item, dict) and isinstance(item.get("sha256"), str)
        )
        if len(hashes) == len(images):
            return hashes
    return ()


def _semantic_status(admission: dict[str, Any]) -> str:
    probe = admission.get("probe")
    if not isinstance(probe, dict):
        return "insufficient"
    scene = probe.get("scene") if isinstance(probe.get("scene"), dict) else probe
    mesh_count = scene.get("meshCount")
    primitive_count = scene.get("primitiveCount")
    explicit = probe.get("semanticStatus")
    if mesh_count == 1 and primitive_count == 1:
        return "insufficient"
    if explicit in {"sufficient", "insufficient"}:
        return str(explicit)
    decomposition = probe.get("semanticDecomposition")
    if isinstance(decomposition, dict) and decomposition.get("status") in {"rich", "sufficient"}:
        return "sufficient"
    return "insufficient"


def validate_provider_run(run: Path, source_images: tuple[Path, ...]) -> ProviderRun:
    root = run.expanduser().resolve()
    receipt = load_json_object(root / "provider-receipt.json", "provider_receipt_missing")
    admission = load_json_object(root / "review" / "admission.json", "mesh_not_structurally_admitted")
    visual = load_json_object(root / "review" / "visual-review.json", "visual_review_missing")
    request_path = root / "request.json"
    request = load_json_object(request_path, "provider_receipt_missing") if request_path.exists() else {}

    glb = root / "normalized" / "reference.glb"
    obj = root / "normalized" / "reference.obj"
    if not glb.is_file() or not obj.is_file():
        raise DenseEvidenceError("mesh_not_structurally_admitted", "normalized GLB and OBJ are required")
    glb_hash = sha256_file(glb)
    obj_hash = sha256_file(obj)
    source_hashes = tuple(sha256_file(item.expanduser().resolve()) for item in source_images)

    expected_glb = receipt.get("normalizedGlbSha256") or admission.get("glbSha256") or receipt.get("rawSha256")
    expected_obj = receipt.get("normalizedObjSha256") or admission.get("objSha256")
    expected_sources = _receipt_source_hashes(receipt, request)
    visual_glb = visual.get("glbSha256")
    if expected_glb != glb_hash or (expected_obj is not None and expected_obj != obj_hash):
        raise DenseEvidenceError("evidence_hash_mismatch", "normalized mesh hash drift")
    if expected_sources and expected_sources != source_hashes:
        raise DenseEvidenceError("evidence_hash_mismatch", "source image hash drift")
    if visual_glb is not None and visual_glb != glb_hash:
        raise DenseEvidenceError("evidence_hash_mismatch", "visual review is bound to another GLB")

    structural = str(admission.get("status", ""))
    if structural not in {"pass", "structural-pass-visual-review-required"}:
        raise DenseEvidenceError("mesh_not_structurally_admitted", f"structural status is {structural!r}")
    reviewed = visual.get("status") == "reviewed" or bool(visual.get("reviewedAt"))
    decision = visual.get("decision") or visual.get("verdict")
    if not reviewed or not isinstance(decision, str):
        raise DenseEvidenceError("visual_review_missing", "completed visual review is required")
    for value in (glb_hash, obj_hash, *source_hashes):
        if len(value) != 64 or not all(character in "0123456789abcdef" for character in value):
            raise DenseEvidenceError("evidence_hash_mismatch", "invalid SHA-256 value")
    return ProviderRun(
        root=root,
        source_image_sha256=source_hashes,
        glb_sha256=glb_hash,
        obj_sha256=obj_hash,
        provider_id=str(receipt.get("providerId", "unknown")),
        structural_status=structural,
        visual_review_status=str(decision),
        semantic_status=_semantic_status(admission),
    )


def finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DenseEvidenceError("malformed_input", f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise DenseEvidenceError("malformed_input", f"{field} must be finite")
    return result
