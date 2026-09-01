"""Content-addressed cache identities for offline dense extraction."""

from __future__ import annotations

from dataclasses import dataclass

from .model import canonical_sha256


@dataclass(frozen=True)
class ExtractionCacheInput:
    glb_sha256: str
    obj_sha256: str
    source_image_sha256: tuple[str, ...]
    extractor_version: str
    alignment_profile_version: str
    component_map_sha256: str | None
    measurement_config_sha256: str


def extraction_cache_key(value: ExtractionCacheInput) -> str:
    return canonical_sha256(
        {
            "normalizedGlbSha256": value.glb_sha256,
            "normalizedObjSha256": value.obj_sha256,
            "sourceImageSha256": list(value.source_image_sha256),
            "extractorVersion": value.extractor_version,
            "alignmentProfileVersion": value.alignment_profile_version,
            "componentMapSha256": value.component_map_sha256,
            "measurementConfigSha256": value.measurement_config_sha256,
        }
    )


def base_extraction_cache_key(value: ExtractionCacheInput) -> str:
    return extraction_cache_key(
        ExtractionCacheInput(
            value.glb_sha256,
            value.obj_sha256,
            value.source_image_sha256,
            value.extractor_version,
            value.alignment_profile_version,
            None,
            value.measurement_config_sha256,
        )
    )
