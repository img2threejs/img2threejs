#!/usr/bin/env python3
"""Adapter-provided reference mask: discovery, provenance check, decode.

The corner-model heuristic (`build_foreground_mask`) segments a uniform backdrop well — every
render, most product photos. It cannot segment a reference whose backdrop is itself structured:
banded studio lighting deviates from any corner model by more than a threshold that still admits
the subject. For those references the pipeline's own answer is the segmentation adapter
(`integrations/vision/run_vision_adapter.py segment`, SAM2), which writes a mask plus a provenance
envelope into the workspace.

This module is the single authority on that artifact, shared by every tier-1 scorer
(`diagnose_render.py`, which produces the official verdict, and `divine_eye.py`). It only ever
answers about the REFERENCE side; the render side always stays with the heuristic.

Two invariants:
  - Absent artifact ⇒ None, and the caller keeps its current behavior unchanged.
  - Present but unauthenticatable ⇒ ReferenceMaskError. Never a quiet fallback: a silent revert
    would publish a score whose ruler nobody can name afterwards.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stage1_intake"))
from extract_pbr_evidence import load_image  # noqa: E402

WORKSPACE_STATE_DIR = ".img2threejs"
REFERENCE_MASK_NAME = "reference-mask.png"
REFERENCE_MASK_SIDECAR_NAMES = (
    "reference-mask.json",           # canonical: written with `segment --json-out`
    "reference-mask.png.json",       # the adapter's default sidecar name for that output
)
REFERENCE_MASK_KIND = "segmentation-mask"
MASK_SOURCE_ARTIFACT = f"artifact:{REFERENCE_MASK_KIND}"
MASK_SOURCE_HEURISTIC = "heuristic"


class ReferenceMaskError(RuntimeError):
    """A reference-mask artifact exists but cannot be trusted to score this reference."""


class ReferenceMaskArtifact(NamedTuple):
    """A validated artifact mask at the reference's own resolution."""

    path: Path
    sidecar: Path
    width: int
    height: int
    full: list[bool]          # width*height, foreground=True
    warnings: list[str]       # decode-time notes (e.g. an external converter was used)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def find_reference_mask_artifact(reference_png: Path) -> tuple[Path, Path | None] | None:
    """Locate `<workspace>/.img2threejs/reference-mask.png` for this reference, or None.

    The workspace root is the NEAREST ancestor of the reference image that owns a state
    directory — the scoring path is handed image paths, never a workspace, and every sanctioned
    run keeps its reference inside the workspace it is scoring. The search stops at that first
    state directory so a stale mask in some outer directory can never be adopted for an inner
    workspace. No state directory above the reference ⇒ no artifact ⇒ heuristic, as before.
    """
    reference_png = reference_png.resolve()
    for candidate in (reference_png.parent, *reference_png.parent.parents):
        state_dir = candidate / WORKSPACE_STATE_DIR
        if not state_dir.is_dir():
            continue
        mask_path = state_dir / REFERENCE_MASK_NAME
        if not mask_path.exists():
            return None
        sidecar = next((state_dir / name for name in REFERENCE_MASK_SIDECAR_NAMES
                        if (state_dir / name).is_file()), None)
        return mask_path, sidecar
    return None


def _read_sidecar(mask_path: Path, sidecar: Path, reference_png: Path) -> dict[str, Any]:
    """Parse and authenticate the provenance envelope the segmentation adapter emits."""
    try:
        envelope = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReferenceMaskError(f"reference mask sidecar {sidecar} is unreadable: {exc}") from exc
    if not isinstance(envelope, dict):
        raise ReferenceMaskError(f"reference mask sidecar {sidecar} is not a JSON object")
    kind = envelope.get("kind")
    if kind != REFERENCE_MASK_KIND:
        raise ReferenceMaskError(
            f"reference mask sidecar {sidecar} declares kind {kind!r}, not {REFERENCE_MASK_KIND!r}"
        )
    source_sha = envelope.get("sourceSha256")
    if not isinstance(source_sha, str) or not source_sha:
        raise ReferenceMaskError(
            f"reference mask sidecar {sidecar} carries no sourceSha256; nothing proves the mask "
            "was cut from the reference being scored"
        )
    actual_sha = _sha256(reference_png)
    if source_sha.lower() != actual_sha.lower():
        raise ReferenceMaskError(
            f"reference mask {mask_path} was cut from a different image: sidecar sourceSha256 "
            f"{source_sha[:12]}… (sourceImage {envelope.get('sourceImage')}) but "
            f"{reference_png} hashes to {actual_sha[:12]}…"
        )
    output_sha = envelope.get("outputSha256")
    if isinstance(output_sha, str) and output_sha:
        actual_mask_sha = _sha256(mask_path)
        if output_sha.lower() != actual_mask_sha.lower():
            raise ReferenceMaskError(
                f"reference mask {mask_path} changed after its sidecar was written: sidecar "
                f"outputSha256 {output_sha[:12]}… but the file hashes to {actual_mask_sha[:12]}…"
            )
    return envelope


def _artifact_foreground(pixels: list[tuple[int, int, int, int]]) -> list[bool]:
    """Foreground rule for a mask image: alpha when the mask carries transparency, else luma.

    A mask painted as shape-on-transparent and a mask painted as white-on-black are both
    idiomatic; reading luma on the first would call the whole frame foreground, so transparency
    wins whenever the image actually uses it.
    """
    has_alpha = any(alpha < 255 for _r, _g, _b, alpha in pixels)
    if has_alpha:
        return [alpha > 0 for _r, _g, _b, alpha in pixels]
    return [r > 0 or g > 0 or b > 0 for r, g, b, _a in pixels]


def load_reference_mask_artifact(reference_png: Path) -> ReferenceMaskArtifact | None:
    """Return the validated adapter mask for this reference, or None when none is published."""
    found = find_reference_mask_artifact(reference_png)
    if found is None:
        return None
    mask_path, sidecar = found
    if sidecar is None:
        raise ReferenceMaskError(
            f"reference mask {mask_path} has no provenance sidecar; expected one of "
            f"{', '.join(REFERENCE_MASK_SIDECAR_NAMES)} beside it — an unprovenanced mask cannot "
            "be tied to the reference it claims to segment"
        )
    envelope = _read_sidecar(mask_path, sidecar, reference_png)
    ref_width, ref_height, _ref_pixels, _ref_warn = load_image(reference_png)
    try:
        width, height, pixels, warnings = load_image(mask_path)
    except Exception as exc:  # noqa: BLE001 — any decode failure is one fail-loud condition
        raise ReferenceMaskError(f"reference mask {mask_path} could not be decoded: {exc}") from exc
    if (width, height) != (ref_width, ref_height):
        raise ReferenceMaskError(
            f"reference mask {mask_path} is {width}x{height} but the reference "
            f"{reference_png} is {ref_width}x{ref_height}; a resampled mask would move the "
            "silhouette it is supposed to measure"
        )
    declared = envelope.get("imageSize")
    if isinstance(declared, list) and len(declared) == 2 and list(declared) != [ref_width, ref_height]:
        raise ReferenceMaskError(
            f"reference mask sidecar {sidecar} declares imageSize {declared} but the reference "
            f"{reference_png} is {ref_width}x{ref_height}"
        )
    full = _artifact_foreground(pixels)
    if not any(full):
        raise ReferenceMaskError(
            f"reference mask {mask_path} marks no foreground pixels; it cannot measure a silhouette"
        )
    return ReferenceMaskArtifact(
        path=mask_path,
        sidecar=sidecar,
        width=width,
        height=height,
        full=full,
        warnings=list(warnings),
    )
