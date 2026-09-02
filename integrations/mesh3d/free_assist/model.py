"""Shared immutable records for the free generative-assist integration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Decision(str, Enum):
    ALLOW = "ALLOW"
    NEEDS_USER_ACTION = "NEEDS_USER_ACTION"
    DENY = "DENY"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class ZeroSpendPolicy:
    max_cost_usd: int = field(default=0, init=False)
    allow_paid_fallback: bool = field(default=False, init=False)
    allow_credit_purchase: bool = field(default=False, init=False)
    allow_automatic_retry: bool = field(default=False, init=False)
    allow_automatic_provider_switch: bool = field(default=False, init=False)


@dataclass(frozen=True)
class GenerationRequest:
    images: tuple[Path, ...]
    provider_id: str
    out_dir: Path
    endpoint_revision: str = "live"
    parameters: dict[str, Any] = field(default_factory=dict)
    policy: ZeroSpendPolicy = field(default_factory=ZeroSpendPolicy)


@dataclass(frozen=True)
class PreflightReport:
    decision: Decision
    provider_id: str
    endpoint: str
    cache_key: str
    reasons: tuple[str, ...]
    evidence: dict[str, Any] = field(default_factory=dict)
    upload_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class RawGeneration:
    source_path: Path
    provider_task_id: str | None = None
    model_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AssistFailure(RuntimeError):
    def __init__(
        self,
        category: str,
        message: str,
        *,
        resumable: bool = False,
        last_artifact: str | None = None,
    ):
        super().__init__(message)
        self.category = category
        self.resumable = resumable
        self.last_artifact = last_artifact


def json_record(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return {key: json_record(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): json_record(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_record(item) for item in value]
    return value
