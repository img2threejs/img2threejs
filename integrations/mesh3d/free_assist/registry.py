"""Reviewed provider registry and fail-closed hosted metadata evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model import Decision


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    endpoint: str
    expected_hardware: str
    hosted: bool
    model_id: str
    license_id: str


@dataclass(frozen=True)
class MetadataDecision:
    decision: Decision
    reasons: tuple[str, ...]
    evidence: dict[str, Any]


class UnknownProvider(ValueError):
    pass


PROVIDERS = {
    "hf-zerogpu-trellis": ProviderSpec(
        "hf-zerogpu-trellis",
        "trellis-community/TRELLIS",
        "zero-a10g",
        True,
        "microsoft/TRELLIS-image-large",
        "MIT",
    ),
    "hf-zerogpu-sf3d": ProviderSpec(
        "hf-zerogpu-sf3d",
        "stabilityai/stable-fast-3d",
        "zero-a10g",
        True,
        "stabilityai/stable-fast-3d",
        "stabilityai-ai-community",
    ),
    "local-sf3d": ProviderSpec(
        "local-sf3d",
        "local",
        "local",
        False,
        "stabilityai/stable-fast-3d",
        "stabilityai-ai-community",
    ),
}


def provider_spec(provider_id: str) -> ProviderSpec:
    try:
        return PROVIDERS[provider_id]
    except KeyError as exc:
        raise UnknownProvider(
            f"provider is not in the reviewed zero-spend registry: {provider_id}"
        ) from exc


def evaluate_hosted_metadata(metadata: dict[str, Any]) -> MetadataDecision:
    runtime = metadata.get("runtime") if isinstance(metadata, dict) else None
    runtime = runtime if isinstance(runtime, dict) else {}
    hardware = (
        runtime.get("hardware") if isinstance(runtime.get("hardware"), dict) else {}
    )
    domains = runtime.get("domains") if isinstance(runtime.get("domains"), list) else []
    evidence = {
        "stage": runtime.get("stage"),
        "hardwareCurrent": hardware.get("current"),
        "hardwareRequested": hardware.get("requested"),
        "readyDomains": [
            item.get("domain")
            for item in domains
            if isinstance(item, dict)
            and item.get("stage") == "READY"
            and item.get("domain")
        ],
        "revision": runtime.get("sha"),
    }
    reasons: list[str] = []
    if evidence["stage"] != "RUNNING":
        reasons.append("Space runtime is not RUNNING")
    if (
        evidence["hardwareCurrent"] != "zero-a10g"
        or evidence["hardwareRequested"] != "zero-a10g"
    ):
        reasons.append(
            "current and requested hardware are not both the reviewed ZeroGPU class"
        )
    if not evidence["readyDomains"]:
        reasons.append("Space has no READY runtime domain")
    return MetadataDecision(
        Decision.DENY if reasons else Decision.ALLOW, tuple(reasons), evidence
    )
