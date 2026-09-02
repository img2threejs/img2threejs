"""Bounded offline extraction from normalized GLB geometry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from . import EXTRACTOR_VERSION
from .alignment import ALIGNMENT_PROFILE_VERSION, ValidatedAlignment, validate_alignment
from .cache import ExtractionCacheInput, base_extraction_cache_key
from .model import (
    DenseEvidenceError,
    canonical_sha256,
    validate_provider_run,
    write_json_atomic,
)
from .regions import inventory_boundaries


MAX_OCCUPANCY_RESOLUTION = 32
MAX_CROSS_SECTIONS = 32
MAX_CROSS_SECTION_POINTS = 64


@dataclass(frozen=True)
class ExtractionConfig:
    resolution: int = 24
    sections: int = 16
    max_section_points: int = 64

    def validate(self) -> None:
        if not 1 <= self.resolution <= MAX_OCCUPANCY_RESOLUTION:
            raise DenseEvidenceError(
                "measurement_limit_exceeded", "occupancy resolution exceeds 32"
            )
        if not 1 <= self.sections <= MAX_CROSS_SECTIONS:
            raise DenseEvidenceError(
                "measurement_limit_exceeded", "cross-section count exceeds 32"
            )
        if not 3 <= self.max_section_points <= MAX_CROSS_SECTION_POINTS:
            raise DenseEvidenceError(
                "measurement_limit_exceeded", "cross-section points exceed 64"
            )


def _load_scene(path: Path) -> trimesh.Scene:
    try:
        loaded = trimesh.load(path, force="scene", process=False)
    except Exception as error:  # noqa: BLE001 - optional parser failures are normalized
        raise DenseEvidenceError("mesh_load_failed", str(error)) from error
    if not isinstance(loaded, trimesh.Scene) or not loaded.geometry:
        raise DenseEvidenceError("mesh_load_failed", "GLB contains no scene geometry")
    return loaded


def _world_points(scene: trimesh.Scene, alignment_transform: np.ndarray) -> np.ndarray:
    groups: list[np.ndarray] = []
    for node in scene.graph.nodes_geometry:
        node_transform, geometry_name = scene.graph[node]
        vertices = np.asarray(scene.geometry[geometry_name].vertices, dtype=float)
        if vertices.size:
            groups.append(
                trimesh.transform_points(vertices, alignment_transform @ node_transform)
            )
    if not groups:
        raise DenseEvidenceError("mesh_load_failed", "GLB contains no vertices")
    points = np.concatenate(groups, axis=0)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise DenseEvidenceError("non_finite_geometry", "mesh vertices must be finite xyz points")
    return points


def _principal_axes(points: np.ndarray) -> list[dict[str, object]]:
    centered = points - points.mean(axis=0)
    covariance = np.cov(centered, rowvar=False)
    values, vectors = np.linalg.eigh(covariance)
    order = np.argsort(values)[::-1]
    return [
        {
            "axis": vectors[:, index].tolist(),
            "variance": float(max(values[index], 0.0)),
        }
        for index in order
    ]


def _sparse_occupancy(
    points: np.ndarray, minimum: np.ndarray, size: np.ndarray, resolution: int
) -> dict[str, object]:
    scaled = np.floor(((points - minimum) / size) * resolution).astype(int)
    indices = np.clip(scaled, 0, resolution - 1)
    occupied = sorted({tuple(int(value) for value in row) for row in indices})
    return {
        "resolution": resolution,
        "occupiedCells": [list(item) for item in occupied],
        "occupiedCellCount": len(occupied),
    }


def _resample_profile(points: np.ndarray, limit: int) -> list[list[float]]:
    if len(points) <= limit:
        chosen = points
    else:
        indices = np.linspace(0, len(points) - 1, limit, dtype=int)
        chosen = points[indices]
    return [[float(row[0]), float(row[2])] for row in chosen]


def _cross_sections(
    points: np.ndarray,
    minimum: np.ndarray,
    maximum: np.ndarray,
    count: int,
    limit: int,
) -> list[dict[str, object]]:
    centers = np.linspace(minimum[1], maximum[1], count)
    half_band = max((maximum[1] - minimum[1]) / max(count * 2, 1), 1e-9)
    records: list[dict[str, object]] = []
    for center in centers:
        selected = points[np.abs(points[:, 1] - center) <= half_band]
        if len(selected) == 0:
            nearest = np.argsort(np.abs(points[:, 1] - center))[: min(limit, len(points))]
            selected = points[nearest]
        records.append(
            {
                "axis": "y",
                "position": float(center),
                "profile": _resample_profile(selected, limit),
            }
        )
    return records


def _geometry_payload(
    points: np.ndarray, alignment: ValidatedAlignment, config: ExtractionConfig
) -> dict[str, object]:
    minimum = points.min(axis=0)
    maximum = points.max(axis=0)
    size = maximum - minimum
    if np.any(size <= 1e-9):
        raise DenseEvidenceError("degenerate_geometry", "mesh bounds have zero volume")
    return {
        "bounds": {
            "min": minimum.tolist(),
            "max": maximum.tolist(),
            "size": size.tolist(),
        },
        "principalAxes": _principal_axes(points),
        "occupancyGrid": _sparse_occupancy(points, minimum, size, config.resolution),
        "crossSections": _cross_sections(
            points, minimum, maximum, config.sections, config.max_section_points
        ),
        "silhouetteViews": [
            {
                "view": capture["view"],
                "path": capture["path"],
                "sha256": capture["sha256"],
                "silhouetteIou": alignment.payload["sourceViewSilhouetteIou"],
                "projectedAspectRatioError": alignment.payload[
                    "projectedAspectRatioError"
                ],
            }
            for capture in alignment.payload["browserCaptures"]
        ],
    }


def extract_run(
    run: Path,
    source_images: tuple[Path, ...],
    alignment_payload: dict[str, object],
    out_dir: Path,
    config: ExtractionConfig | None = None,
) -> dict[str, Any]:
    selected = config or ExtractionConfig()
    selected.validate()
    provider_run = validate_provider_run(run, source_images)
    alignment = validate_alignment(alignment_payload, provider_run.semantic_status)
    scene = _load_scene(provider_run.root / "normalized" / "reference.glb")
    transform = np.asarray(alignment.transform, dtype=float).reshape((4, 4))
    points = _world_points(scene, transform)
    measurement_config = {
        "resolution": selected.resolution,
        "sections": selected.sections,
        "maxSectionPoints": selected.max_section_points,
    }
    cache_input = ExtractionCacheInput(
        provider_run.glb_sha256,
        provider_run.obj_sha256,
        provider_run.source_image_sha256,
        EXTRACTOR_VERSION,
        ALIGNMENT_PROFILE_VERSION,
        None,
        canonical_sha256(measurement_config),
    )
    evidence: dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "dense-evidence",
        "extractorVersion": EXTRACTOR_VERSION,
        "createdAt": datetime.now(UTC).isoformat(),
        "provenance": {
            "providerId": provider_run.provider_id,
            "runPath": str(provider_run.root),
            "glbPath": str(provider_run.root / "normalized" / "reference.glb"),
            "glbSha256": provider_run.glb_sha256,
            "objSha256": provider_run.obj_sha256,
            "sourceImageSha256": list(provider_run.source_image_sha256),
            "visualReviewStatus": provider_run.visual_review_status,
            "alignmentProfileVersion": ALIGNMENT_PROFILE_VERSION,
            "alignmentSha256": canonical_sha256(alignment_payload),
        },
        "cache": {
            "baseExtractionKey": base_extraction_cache_key(cache_input),
            "measurementConfigSha256": cache_input.measurement_config_sha256,
        },
        "admission": {
            "structuralStatus": provider_run.structural_status,
            "semanticStatus": provider_run.semantic_status,
            "maximumInfluenceScope": alignment.maximum_scope.value,
            "approvedInfluenceScope": "none",
        },
        "alignment": dict(alignment_payload),
        "globalGeometry": _geometry_payload(points, alignment, selected),
        "regions": inventory_boundaries(scene, transform),
        "uncertainty": {
            "sourceViewCount": len(source_images),
            "hiddenSurfacePolicy": "non-authoritative",
            "rearConfidence": "low",
            "singleViewLimitations": [
                "rear and occluded geometry are model inventions, not source observations"
            ],
        },
        "extensions": {},
    }
    target = out_dir.expanduser().resolve()
    write_json_atomic(target / "dense-evidence.v1.json", evidence)
    return evidence
