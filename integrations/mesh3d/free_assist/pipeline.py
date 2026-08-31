"""Fail-closed orchestration, cache, normalization, and admission."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .model import (
    AssistFailure,
    Decision,
    GenerationRequest,
    PreflightReport,
    json_record,
)
from .registry import MetadataDecision, evaluate_hosted_metadata, provider_spec
from .security import redact, write_json_atomic


NORMALIZER_VERSION = "free-assist-normalizer-v1"
MIN_LOCAL_DISK_BYTES = 12 << 30
MIN_LOCAL_MEMORY_BYTES = 16 << 30
MPS_RECOMMENDED_MEMORY_BYTES = 32 << 30
MetadataProbe = Callable[[str], dict[str, Any]]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def compute_cache_key(request: GenerationRequest) -> str:
    payload = {
        "imageHashes": [
            sha256_file(path.expanduser().resolve()) for path in request.images
        ],
        "providerId": request.provider_id,
        "endpointRevision": request.endpoint_revision,
        "parameters": request.parameters,
        "normalizerVersion": NORMALIZER_VERSION,
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def compute_request_fingerprint(request: GenerationRequest) -> str:
    payload = {
        "imageHashes": [
            sha256_file(path.expanduser().resolve()) for path in request.images
        ],
        "providerId": request.provider_id,
        "parameters": request.parameters,
        "normalizerVersion": NORMALIZER_VERSION,
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def default_metadata_probe(endpoint: str) -> dict[str, Any]:
    try:
        from huggingface_hub import HfApi

        info = HfApi().space_info(endpoint)
        runtime_value = info.runtime
        runtime = (
            runtime_value
            if isinstance(runtime_value, dict)
            else getattr(runtime_value, "raw", {})
        )
        card_value = info.card_data
        if card_value is None:
            card_data = {}
        elif hasattr(card_value, "to_dict"):
            card_data = card_value.to_dict()
        elif isinstance(card_value, dict):
            card_data = card_value
        else:
            card_data = {}
        return {"runtime": runtime, "cardData": card_data, "id": info.id}
    except Exception as exc:  # noqa: BLE001 - uncertainty must fail closed
        raise AssistFailure(
            "free_status_unverified", f"could not verify hosted free status: {exc}"
        ) from exc


def _read_cache_index(out_dir: Path) -> dict[str, Any]:
    path = out_dir / "cache-index.json"
    if not path.exists():
        return {"schemaVersion": 1, "entries": {}, "aliases": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schemaVersion": 1, "entries": {}, "aliases": {}}
    if not isinstance(value, dict) or not isinstance(value.get("entries"), dict):
        return {"schemaVersion": 1, "entries": {}, "aliases": {}}
    if not isinstance(value.get("aliases"), dict):
        value["aliases"] = {}
    return value


def _cached_run(out_dir: Path, cache_key: str) -> str | None:
    entry = _read_cache_index(out_dir)["entries"].get(cache_key)
    if not isinstance(entry, dict) or entry.get("status") != "complete":
        return None
    run = out_dir / str(entry.get("run", ""))
    raw = run / "raw" / "reference.glb"
    normalized = run / "normalized" / "reference.glb"
    receipt_path = run / "provider-receipt.json"
    if raw.is_file() and normalized.is_file() and receipt_path.is_file():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("rawSha256") == sha256_file(raw):
                return str(run)
        except (OSError, json.JSONDecodeError):
            pass
    return None


def _cached_run_for_request(request: GenerationRequest) -> tuple[str, str] | None:
    index = _read_cache_index(request.out_dir)
    cache_key = index["aliases"].get(compute_request_fingerprint(request))
    if not isinstance(cache_key, str):
        return None
    run = _cached_run(request.out_dir, cache_key)
    return (run, cache_key) if run else None


def _physical_memory_bytes() -> int | None:
    if platform.system() == "Darwin":
        completed = subprocess.run(
            ["sysctl", "-n", "hw.memsize"], text=True, capture_output=True, check=False
        )
        if completed.returncode == 0 and completed.stdout.strip().isdigit():
            return int(completed.stdout.strip())
    try:
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        return pages * page_size
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def local_capability(
    root: Path,
    *,
    system: str | None = None,
    machine: str | None = None,
    disk_free_bytes: int | None = None,
    memory_bytes: int | None = None,
) -> MetadataDecision:
    system = system or platform.system()
    machine = machine or platform.machine()
    if disk_free_bytes is None:
        try:
            disk_free_bytes = shutil.disk_usage(root).free
        except OSError:
            disk_free_bytes = 0
    if memory_bytes is None:
        memory_bytes = _physical_memory_bytes() or 0
    apple_silicon = system == "Darwin" and machine in {"arm64", "aarch64"}
    backend = (
        "mps-experimental"
        if apple_silicon and memory_bytes >= MPS_RECOMMENDED_MEMORY_BYTES
        else "cpu"
    )
    evidence = {
        "system": system,
        "machine": machine,
        "appleSilicon": apple_silicon,
        "diskFreeBytes": disk_free_bytes,
        "memoryBytes": memory_bytes,
        "minimumDiskBytes": MIN_LOCAL_DISK_BYTES,
        "minimumMemoryBytes": MIN_LOCAL_MEMORY_BYTES,
        "recommendedBackend": backend,
    }
    reasons: list[str] = []
    if disk_free_bytes < MIN_LOCAL_DISK_BYTES:
        reasons.append(
            "less than 12 GiB free disk is available for the local environment and model"
        )
    if memory_bytes < MIN_LOCAL_MEMORY_BYTES:
        reasons.append("less than 16 GiB physical/unified memory is available")
    return MetadataDecision(
        Decision.UNAVAILABLE if reasons else Decision.ALLOW, tuple(reasons), evidence
    )


def local_install_revision(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    revision = completed.stdout.strip()
    if (
        completed.returncode == 0
        and len(revision) == 40
        and all(character in "0123456789abcdef" for character in revision.lower())
    ):
        return f"git:{revision}"
    script = root / "run.py"
    return f"run-py-sha256:{sha256_file(script)}"


def _local_preflight(
    request: GenerationRequest, approve_local_run: bool
) -> PreflightReport:
    spec = provider_spec(request.provider_id)
    reasons: list[str] = []
    root_value = os.environ.get("SF3D_ROOT", "")
    root = Path(root_value).expanduser().resolve() if root_value else None
    capability: MetadataDecision | None = None
    failure_category: str | None = None
    if root is None or not (root / "run.py").is_file():
        reasons.append(
            "SF3D_ROOT does not point to an installed Stable Fast 3D checkout"
        )
        failure_category = "local_capability_missing"
    else:
        capability = local_capability(root)
    if os.environ.get("SF3D_MODEL_ACCESS_APPROVED") != "1":
        reasons.append("model license/access acceptance must be completed by the user")
        if failure_category is None:
            failure_category = "license_acceptance_required"
    if not approve_local_run:
        reasons.append("local execution approval is missing")
        if failure_category is None:
            failure_category = "local_capability_missing"
    decision = Decision.ALLOW if not reasons else Decision.NEEDS_USER_ACTION
    if capability is not None and capability.decision == Decision.UNAVAILABLE:
        decision = Decision.UNAVAILABLE
        reasons.extend(capability.reasons)
        failure_category = "local_capability_missing"
    effective_parameters = dict(request.parameters)
    if (
        capability is not None
        and capability.evidence.get("recommendedBackend") == "cpu"
    ):
        effective_parameters["forceCpu"] = True
    revision = (
        local_install_revision(root)
        if root is not None and (root / "run.py").is_file()
        else "local-install-unverified"
    )
    request_for_key = replace(
        request, endpoint_revision=revision, parameters=effective_parameters
    )
    return PreflightReport(
        decision,
        request.provider_id,
        spec.endpoint,
        compute_cache_key(request_for_key),
        tuple(reasons),
        {
            "platform": platform.platform(),
            "sf3dRoot": str(root) if root else None,
            "revision": revision,
            "monetaryCostUsd": 0,
            "capability": capability.evidence if capability else None,
            "effectiveParameters": effective_parameters,
            "failureCategory": failure_category,
        },
        (),
    )


def preflight(
    request: GenerationRequest,
    *,
    metadata_probe: MetadataProbe = default_metadata_probe,
    approve_upload: bool = False,
    approve_local_run: bool = False,
) -> PreflightReport:
    spec = provider_spec(request.provider_id)
    if not request.images:
        raise AssistFailure(
            "invalid_provider_response", "at least one reference image is required"
        )
    missing = [str(path) for path in request.images if not path.expanduser().is_file()]
    if missing:
        raise AssistFailure(
            "invalid_provider_response", f"reference image not found: {missing[0]}"
        )
    if request.policy.max_cost_usd != 0 or any(
        (
            request.policy.allow_paid_fallback,
            request.policy.allow_credit_purchase,
            request.policy.allow_automatic_retry,
            request.policy.allow_automatic_provider_switch,
        )
    ):
        raise AssistFailure("free_status_unverified", "zero-spend policy is not exact")

    cached_before_probe = _cached_run_for_request(request)
    if cached_before_probe:
        cached, cache_key = cached_before_probe
        report = PreflightReport(
            Decision.ALLOW,
            request.provider_id,
            spec.endpoint,
            cache_key,
            (
                "matching completed generation is cached; no capability probe, upload, or provider call is needed",
            ),
            {
                "cacheHit": True,
                "cachedRun": cached,
                "monetaryCostUsd": 0,
                "policy": json_record(request.policy),
            },
            tuple(str(path.expanduser().resolve()) for path in request.images)
            if spec.hosted
            else (),
        )
        write_json_atomic(request.out_dir / "preflight.json", json_record(report))
        return report

    if not spec.hosted:
        report = _local_preflight(request, approve_local_run)
    else:
        try:
            metadata = metadata_probe(spec.endpoint)
        except Exception as exc:  # noqa: BLE001 - unverified live state is a durable DENY report
            report = PreflightReport(
                Decision.DENY,
                request.provider_id,
                spec.endpoint,
                compute_cache_key(replace(request, endpoint_revision="unverified")),
                (
                    f"live free-status metadata could not be verified: {redact(str(exc))}",
                ),
                {
                    "cacheHit": False,
                    "cachedRun": None,
                    "monetaryCostUsd": 0,
                    "policy": json_record(request.policy),
                    "failureCategory": "free_status_unverified",
                },
                tuple(str(path.expanduser().resolve()) for path in request.images),
            )
            write_json_atomic(request.out_dir / "preflight.json", json_record(report))
            return report
        evaluated = evaluate_hosted_metadata(metadata)
        revision = str(evaluated.evidence.get("revision") or "unverified")
        keyed_request = replace(request, endpoint_revision=revision)
        cache_key = compute_cache_key(keyed_request)
        cached = _cached_run(request.out_dir, cache_key)
        if evaluated.decision != Decision.ALLOW:
            decision = Decision.DENY
            reasons = evaluated.reasons
        elif cached:
            decision = Decision.ALLOW
            reasons = (
                "matching completed generation is cached; no upload or provider call is needed",
            )
        elif not approve_upload:
            decision = Decision.NEEDS_USER_ACTION
            reasons = (
                "exact upload approval is required for the listed files and provider",
            )
        else:
            decision = Decision.ALLOW
            reasons = (
                "reviewed ZeroGPU metadata and exact upload approval are present",
            )
        evidence = {
            **evaluated.evidence,
            "cacheHit": bool(cached),
            "cachedRun": cached,
            "monetaryCostUsd": 0,
            "policy": json_record(request.policy),
            "failureCategory": (
                "free_status_unverified"
                if evaluated.decision != Decision.ALLOW
                else "upload_not_approved"
                if not cached and not approve_upload
                else None
            ),
        }
        report = PreflightReport(
            decision,
            request.provider_id,
            spec.endpoint,
            cache_key,
            reasons,
            evidence,
            tuple(str(path.expanduser().resolve()) for path in request.images),
        )
    write_json_atomic(request.out_dir / "preflight.json", json_record(report))
    return report


def _run_id(cache_key: str, out_dir: Path) -> str:
    stem = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + cache_key[:10]
    candidate = stem
    suffix = 1
    while (out_dir / "runs" / candidate).exists():
        suffix += 1
        candidate = f"{stem}-{suffix}"
    return candidate


def _failure_status(
    category: str, message: str, *, resumable: bool, last_artifact: str | None = None
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "status": "failed",
        "failureCategory": category,
        "message": message,
        "resumable": resumable,
        "lastDurableArtifact": last_artifact,
        "updatedAt": datetime.now(UTC).isoformat(),
    }


def normalize_provider_failure(exc: Exception) -> str:
    message = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timed out" in message or "timeout" in message:
        return "queue_timeout"
    if "quota" in message and (
        "exhaust" in message or "exceed" in message or "limit" in message
    ):
        return "quota_exhausted"
    return "provider_unavailable"


def generate(
    request: GenerationRequest,
    *,
    approve_upload: bool = False,
    approve_local_run: bool = False,
    metadata_probe: MetadataProbe = default_metadata_probe,
    adapter: Any | None = None,
) -> Path:
    report = preflight(
        request,
        metadata_probe=metadata_probe,
        approve_upload=approve_upload,
        approve_local_run=approve_local_run,
    )
    spec = provider_spec(request.provider_id)
    cached = report.evidence.get("cachedRun")
    if cached:
        return Path(str(cached))
    if report.decision != Decision.ALLOW:
        category = str(
            report.evidence.get("failureCategory") or "free_status_unverified"
        )
        raise AssistFailure(category, "; ".join(report.reasons))

    effective_parameters = report.evidence.get("effectiveParameters")
    provider_request = (
        replace(request, parameters=dict(effective_parameters))
        if isinstance(effective_parameters, dict)
        else request
    )

    run_dir = request.out_dir / "runs" / _run_id(report.cache_key, request.out_dir)
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=False)
    write_json_atomic(
        run_dir / "request.json",
        {
            "schemaVersion": 1,
            "providerId": request.provider_id,
            "endpoint": spec.endpoint,
            "cacheKey": report.cache_key,
            "requestFingerprint": compute_request_fingerprint(request),
            "images": [
                {"name": path.name, "sha256": sha256_file(path)}
                for path in request.images
            ],
            "parameters": provider_request.parameters,
            "policy": json_record(request.policy),
        },
    )
    write_json_atomic(
        run_dir / "status.json",
        {
            "schemaVersion": 1,
            "status": "provider-running",
            "resumable": False,
            "updatedAt": datetime.now(UTC).isoformat(),
        },
    )
    if adapter is None:
        from .providers import provider_adapter

        adapter = provider_adapter(request.provider_id)
    try:
        raw_generation = adapter.generate(provider_request, run_dir)
        source = Path(raw_generation.source_path)
        if not source.is_file():
            raise AssistFailure(
                "invalid_provider_response", "provider returned a missing GLB path"
            )
        temporary = raw_dir / "reference.glb.tmp"
        shutil.copyfile(source, temporary)
        if temporary.read_bytes()[:4] != b"glTF":
            temporary.unlink(missing_ok=True)
            raise AssistFailure(
                "invalid_glb", "provider output does not start with GLB magic"
            )
        raw = raw_dir / "reference.glb"
        temporary.replace(raw)
        raw.chmod(0o444)
        receipt = {
            "schemaVersion": 1,
            "providerId": request.provider_id,
            "endpoint": spec.endpoint,
            "endpointRevision": report.evidence.get("revision"),
            "modelId": raw_generation.model_id or spec.model_id,
            "licenseId": spec.license_id,
            "providerTaskId": raw_generation.provider_task_id,
            "parameters": provider_request.parameters,
            "quotaClass": spec.expected_hardware,
            "declaredMonetaryCostUsd": 0,
            "rawSha256": sha256_file(raw),
            "providerMetadata": raw_generation.metadata,
            "completedAt": datetime.now(UTC).isoformat(),
        }
        write_json_atomic(run_dir / "provider-receipt.json", receipt)
        write_json_atomic(
            run_dir / "status.json",
            {
                "schemaVersion": 1,
                "status": "raw-persisted",
                "resumable": True,
                "lastDurableArtifact": "raw/reference.glb",
                "updatedAt": datetime.now(UTC).isoformat(),
            },
        )
        return run_dir
    except AssistFailure as exc:
        message = str(redact(str(exc)))
        status = _failure_status(
            exc.category,
            message,
            resumable=exc.resumable,
            last_artifact=exc.last_artifact,
        )
        write_json_atomic(run_dir / "status.json", status)
        raise AssistFailure(
            exc.category,
            message,
            resumable=exc.resumable,
            last_artifact=exc.last_artifact,
        ) from exc
    except Exception as exc:  # noqa: BLE001 - provider errors are normalized and never retried
        message = str(redact(str(exc)))
        category = normalize_provider_failure(exc)
        write_json_atomic(
            run_dir / "status.json", _failure_status(category, message, resumable=False)
        )
        raise AssistFailure(category, message) from exc


def admit_glb(
    glb_path: Path,
    *,
    obj_info: dict[str, Any] | None = None,
    max_vertices: int = 2_000_000,
    max_triangles: int = 4_000_000,
) -> dict[str, Any]:
    try:
        from forge.stage1_intake.probe_glb import parse_glb, probe_glb

        document, _binary, _binary_info = parse_glb(glb_path)
        probe = probe_glb(glb_path)
    except Exception as exc:  # noqa: BLE001 - invalid input is a report, not a crash
        return {
            "schemaVersion": 1,
            "status": "reject",
            "failureCategory": "invalid_glb",
            "reasons": [str(redact(str(exc)))],
            "admittedForProceduralInfluence": False,
        }
    reasons: list[str] = []
    scene = probe.get("scene", {})
    if probe.get("referenceReadiness") != "pass" or int(scene.get("meshCount", 0)) < 1:
        reasons.append("GLB has no readable mesh and BIN inventory")
    if int(scene.get("materialCount", 0)) < 1:
        reasons.append("GLB has no material inventory")
    bounds = probe.get("bounds")
    size = bounds.get("size") if isinstance(bounds, dict) else None
    if (
        not isinstance(size, list)
        or len(size) != 3
        or not all(math.isfinite(float(value)) for value in size)
    ):
        reasons.append("GLB bounds are missing or non-finite")
    elif sum(float(value) > 1e-9 for value in size) < 2:
        reasons.append("GLB bounds are degenerate")
    if obj_info:
        vertices = int(obj_info.get("vertices", 0))
        triangles = int(obj_info.get("triangles", 0))
        if vertices <= 0 or vertices > max_vertices:
            reasons.append(f"vertex count {vertices} is outside 1..{max_vertices}")
        if triangles <= 0 or triangles > max_triangles:
            reasons.append(f"triangle count {triangles} is outside 1..{max_triangles}")
        obj_size = obj_info.get("size")
        if isinstance(size, list) and isinstance(obj_size, list) and len(obj_size) == 3:
            glb_scale = max(float(value) for value in size)
            mismatch = max(abs(float(a) - float(b)) for a, b in zip(size, obj_size))
            if mismatch > max(1e-4, glb_scale * 0.01):
                reasons.append("GLB and OBJ bounds disagree by more than one percent")
    required = (
        document.get("extensionsRequired", []) if isinstance(document, dict) else []
    )
    compressed = any(
        "draco" in str(item).lower() or "meshopt" in str(item).lower()
        for item in required
    )
    return {
        "schemaVersion": 1,
        "status": "reject" if reasons else "structural-pass-visual-review-required",
        "failureCategory": "mesh_admission_failed" if reasons else None,
        "reasons": reasons,
        "probe": probe,
        "obj": obj_info,
        "compressed": compressed,
        "visualReview": {
            "status": "required",
            "requirements": [
                "preview render",
                "silhouette",
                "aspect",
                "scale",
                "invented hidden surfaces",
            ],
        },
        "admittedForProceduralInfluence": False,
        "note": "Structural admission is not visual acceptance. The mesh remains a proxy until the review gates pass.",
    }


def _default_obj_writer(glb_path: Path, obj_path: Path) -> dict[str, Any]:
    from integrations.mesh3d.generate_reference_mesh import write_obj

    return write_obj(glb_path, obj_path)


def _write_cache_entry(run_dir: Path, cache_key: str, request_fingerprint: str) -> None:
    out_dir = run_dir.parent.parent
    index = _read_cache_index(out_dir)
    relative = run_dir.relative_to(out_dir)
    index["entries"][cache_key] = {
        "status": "complete",
        "run": str(relative),
        "updatedAt": datetime.now(UTC).isoformat(),
    }
    index["aliases"][request_fingerprint] = cache_key
    write_json_atomic(out_dir / "cache-index.json", index)


def resume(
    run_dir: Path,
    *,
    obj_writer: Callable[[Path, Path], dict[str, Any]] = _default_obj_writer,
) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    raw = run_dir / "raw" / "reference.glb"
    if not raw.is_file():
        status = _failure_status(
            "invalid_glb", "raw/reference.glb is missing", resumable=False
        )
        write_json_atomic(run_dir / "status.json", status)
        return status
    try:
        receipt = json.loads(
            (run_dir / "provider-receipt.json").read_text(encoding="utf-8")
        )
        expected_hash = receipt.get("rawSha256")
        if not expected_hash or sha256_file(raw) != expected_hash:
            raise AssistFailure(
                "invalid_glb", "raw GLB hash no longer matches its provider receipt"
            )
        normalized = run_dir / "normalized"
        normalized.mkdir(parents=True, exist_ok=True)
        normalized_glb = normalized / "reference.glb"
        temporary_glb = normalized / "reference.glb.tmp"
        shutil.copyfile(raw, temporary_glb)
        temporary_glb.replace(normalized_glb)
        obj_path = normalized / "reference.obj"
        obj_info = obj_writer(normalized_glb, obj_path)
        admission = admit_glb(normalized_glb, obj_info=obj_info)
        write_json_atomic(
            normalized / "reference-mesh.json",
            {
                "schemaVersion": 1,
                "glb": "reference.glb",
                "obj": "reference.obj",
                "glbSha256": sha256_file(normalized_glb),
                **obj_info,
            },
        )
        write_json_atomic(run_dir / "review" / "admission.json", admission)
        if admission["status"] == "reject":
            status = _failure_status(
                "mesh_admission_failed",
                "; ".join(admission["reasons"]),
                resumable=True,
                last_artifact="raw/reference.glb",
            )
            write_json_atomic(run_dir / "status.json", status)
            return status
        request_record = json.loads(
            (run_dir / "request.json").read_text(encoding="utf-8")
        )
        status = {
            "schemaVersion": 1,
            "status": "complete",
            "resumable": True,
            "lastDurableArtifact": "raw/reference.glb",
            "normalizedArtifacts": [
                "normalized/reference.glb",
                "normalized/reference.obj",
                "normalized/reference-mesh.json",
            ],
            "visualReviewStatus": "required",
            "admittedForProceduralInfluence": False,
            "updatedAt": datetime.now(UTC).isoformat(),
        }
        write_json_atomic(run_dir / "status.json", status)
        _write_cache_entry(
            run_dir,
            str(request_record["cacheKey"]),
            str(request_record["requestFingerprint"]),
        )
        return status
    except AssistFailure as exc:
        status = _failure_status(
            exc.category,
            str(exc),
            resumable=exc.resumable,
            last_artifact=exc.last_artifact,
        )
    except Exception as exc:  # noqa: BLE001 - local conversion remains resumable from raw
        status = _failure_status(
            "normalization_failed",
            str(redact(str(exc))),
            resumable=True,
            last_artifact="raw/reference.glb",
        )
    write_json_atomic(run_dir / "status.json", status)
    return status
