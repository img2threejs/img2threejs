"""Offline bridge from reviewed generative meshes to bounded evidence JSON."""

from .model import DenseEvidenceError, InfluenceScope, ProviderRun

EXTRACTOR_VERSION = "dense-evidence-extractor-v1"

__all__ = ["DenseEvidenceError", "EXTRACTOR_VERSION", "InfluenceScope", "ProviderRun"]
