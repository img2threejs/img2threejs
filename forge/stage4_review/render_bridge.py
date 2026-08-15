#!/usr/bin/env python3
"""Deterministic manifest and evidence controller for browser Three.js renders.

This module does not render a model. It creates a camera batch, validates saved
screenshots, records provenance, and delegates deterministic image checks to the
existing review gates. A browser adapter (Chrome MCP or Playwright) must produce
the actual Three.js pixels.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

STAGE1 = Path(__file__).resolve().parents[1] / "stage1_intake"
sys.path.insert(0, str(STAGE1))
from probe_image import probe  # noqa: E402
from probe_glb import probe_glb  # noqa: E402
from extract_pbr_evidence import load_image  # noqa: E402

SHARED = Path(__file__).resolve().parents[1] / "_shared"
sys.path.insert(0, str(SHARED))
from image_hash import hamming, phash, to_grayscale_downsampled  # noqa: E402

REVIEW_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REVIEW_DIR))
from multi_pass import PASS_IDS, default_pass_records, record_pass, validate_pass_records  # noqa: E402


CAPTURE_PLAN: tuple[dict[str, Any], ...] = (
    {"id": "hero", "role": "reference-match", "azimuthDegrees": 0, "elevationDegrees": 0},
    {"id": "orbit-plus35", "role": "orbit", "azimuthDegrees": 35, "elevationDegrees": 0},
    {"id": "orbit-minus35", "role": "orbit", "azimuthDegrees": -35, "elevationDegrees": 0},
    {"id": "profile", "role": "orbit", "azimuthDegrees": 78, "elevationDegrees": 0},
    {"id": "rear", "role": "orbit", "azimuthDegrees": 180, "elevationDegrees": 0},
    {"id": "head-hero", "role": "head-closeup", "azimuthDegrees": 0, "elevationDegrees": 0},
    {"id": "head-threequarter", "role": "head-closeup", "azimuthDegrees": 35, "elevationDegrees": 0},
)

ADAPTIVE_BROWSER_RECEIPT_KIND = "img2threejs.playwright-capture-receipt"
ADAPTIVE_BROWSER_RECEIPT_VERSION = 1
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
NONCE_PATTERN = re.compile(r"^[a-f0-9]{32,64}$")
ADAPTIVE_SESSION_PATTERN = re.compile(r"^ahc-[a-f0-9]{20}$")
ADAPTIVE_VIEW_ID_PATTERN = re.compile(
    r"^ahc-[a-f0-9]{20}-harsh-(front|right|rear|left|top|bottom)-(root|[0-3]+)$"
)
# Whole-frame +/-3 LSB noise has MAE ~= 3/255 (0.0118).  A strict
# visible-color floor rejects that tiny perturbation without relying on pHash,
# while the structure envelope below catches slightly wider uniform
# brightness/color shifts whose scene layout did not change.
NEAR_DUPLICATE_VISIBLE_RGB_MAE = 0.02
NEAR_DUPLICATE_STRUCTURE_MAE = 0.025
NEAR_DUPLICATE_MEAN_COLOR_DISTANCE = 0.10
NEAR_DUPLICATE_PHASH_DISTANCE = 2


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    """Hash one JSON value with the canonical encoding used by evidence receipts."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def decoded_pixel_sha256(path: Path) -> str:
    """Hash decoded RGBA pixels, not PNG container bytes.

    This catches the same frame re-encoded with different PNG compression or
    metadata, which a file SHA alone cannot detect.
    """
    return str(_decoded_image_signature(path)["pixelSha256"])


@lru_cache(maxsize=512)
def _decoded_image_signature_cached(path_value: str, file_hash: str) -> dict[str, Any]:
    # ``file_hash`` is deliberately part of the key: overwriting a capture at
    # the same path cannot reuse the old perceptual signature.
    del file_hash
    width, height, pixels, _warnings = load_image(Path(path_value))
    pixel_digest = hashlib.sha256()
    pixel_digest.update(f"rgba8:{width}x{height}\n".encode("ascii"))
    for pixel in pixels:
        pixel_digest.update(bytes(pixel))
    # Browser screenshots are normally opaque, but treat transparent RGB as
    # invisible by compositing over white before pHash/luma comparison. This
    # prevents hidden RGB noise under alpha=0 from masquerading as a new view.
    composited: list[tuple[int, int, int, int]] = []
    for red, green, blue, alpha in pixels:
        opacity = alpha / 255.0
        composited.append(
            (
                round(red * opacity + 255 * (1.0 - opacity)),
                round(green * opacity + 255 * (1.0 - opacity)),
                round(blue * opacity + 255 * (1.0 - opacity)),
                255,
            )
        )
    gray = to_grayscale_downsampled(width, height, composited)
    grid_size = 32
    rgb_sums = [[[0.0, 0.0, 0.0, 0] for _x in range(grid_size)] for _y in range(grid_size)]
    for index, (red, green, blue, _alpha) in enumerate(composited):
        x = index % width
        y = index // width
        cell_x = min(grid_size - 1, x * grid_size // width)
        cell_y = min(grid_size - 1, y * grid_size // height)
        cell = rgb_sums[cell_y][cell_x]
        cell[0] += red
        cell[1] += green
        cell[2] += blue
        cell[3] += 1
    rgb: list[list[tuple[float, float, float]]] = []
    for row in rgb_sums:
        normalized_row: list[tuple[float, float, float]] = []
        for red_sum, green_sum, blue_sum, count in row:
            divisor = float(count) if count else 1.0
            normalized_row.append(
                (red_sum / divisor, green_sum / divisor, blue_sum / divisor)
            )
        rgb.append(normalized_row)
    return {
        "width": width,
        "height": height,
        "gray": gray,
        "rgb": rgb,
        "meanRgb": tuple(
            sum(pixel[channel] for pixel in composited) / len(composited)
            for channel in range(3)
        ) if composited else (0.0, 0.0, 0.0),
        "phash": phash(gray),
        "pixelSha256": pixel_digest.hexdigest(),
    }


def _decoded_image_signature(path: Path) -> dict[str, Any]:
    """Return a compact low-frequency + pHash signature for replay detection."""
    resolved = path.expanduser().resolve()
    return _decoded_image_signature_cached(str(resolved), sha256(resolved))


def _normalized_visible_rgb_mae(left: dict[str, Any], right: dict[str, Any]) -> float:
    """Mean alpha-composited RGB error in [0, 1], or 1 for size mismatch.

    Luma alone aliases visibly different isoluminant colors (for example red
    versus dark green). RGB preserves that chroma evidence. Compositing before
    downsampling makes hidden RGB beneath alpha=0 irrelevant.
    """
    if left["width"] != right["width"] or left["height"] != right["height"]:
        return 1.0
    left_rgb = left["rgb"]
    right_rgb = right["rgb"]
    if len(left_rgb) != len(right_rgb) or not left_rgb:
        return 1.0
    total = 0.0
    samples = 0
    for left_row, right_row in zip(left_rgb, right_rgb):
        if len(left_row) != len(right_row):
            return 1.0
        for left_pixel, right_pixel in zip(left_row, right_row):
            total += sum(
                abs(float(left_value) - float(right_value))
                for left_value, right_value in zip(left_pixel, right_pixel)
            )
            samples += 3
    return total / (samples * 255.0) if samples else 1.0


def _normalized_demeaned_luma_mae(left: dict[str, Any], right: dict[str, Any]) -> float:
    """Brightness-shift-invariant low-frequency structure error in [0, 1]."""
    if left["width"] != right["width"] or left["height"] != right["height"]:
        return 1.0
    left_gray = left.get("gray")
    right_gray = right.get("gray")
    if not isinstance(left_gray, list) or not isinstance(right_gray, list):
        return 1.0
    left_values = [float(value) for row in left_gray for value in row]
    right_values = [float(value) for row in right_gray for value in row]
    if not left_values or len(left_values) != len(right_values):
        return 1.0
    left_mean = sum(left_values) / len(left_values)
    right_mean = sum(right_values) / len(right_values)
    return sum(
        abs((left_value - left_mean) - (right_value - right_mean))
        for left_value, right_value in zip(left_values, right_values)
    ) / (len(left_values) * 255.0)


def _normalized_mean_color_distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    """Mean alpha-composited RGB difference in [0, 1]."""
    left_mean = left.get("meanRgb")
    right_mean = right.get("meanRgb")
    if (
        not isinstance(left_mean, tuple)
        or not isinstance(right_mean, tuple)
        or len(left_mean) != 3
        or len(right_mean) != 3
    ):
        return 1.0
    return sum(
        abs(float(left_value) - float(right_value))
        for left_value, right_value in zip(left_mean, right_mean)
    ) / (3.0 * 255.0)


def _near_identical_image_signatures(
    left: dict[str, Any], right: dict[str, Any]
) -> tuple[bool, int, float]:
    distance = hamming(int(left["phash"]), int(right["phash"]))
    visible_rgb_mae = _normalized_visible_rgb_mae(left, right)
    structure_mae = _normalized_demeaned_luma_mae(left, right)
    mean_color_distance = _normalized_mean_color_distance(left, right)
    # The extreme critic is intentionally fail closed.  Tiny visible-color
    # changes never prove a new camera view, even when adversarial structured
    # noise destabilizes pHash.  A second, brightness-shift-invariant envelope
    # catches pHash-stable frames with the same spatial structure, but its mean
    # color guard leaves visibly different isoluminant colors admissible.
    collapsed = visible_rgb_mae <= NEAR_DUPLICATE_VISIBLE_RGB_MAE or (
        distance <= NEAR_DUPLICATE_PHASH_DISTANCE
        and structure_mae <= NEAR_DUPLICATE_STRUCTURE_MAE
        and mean_color_distance <= NEAR_DUPLICATE_MEAN_COLOR_DISTANCE
    )
    return collapsed, distance, visible_rgb_mae


def _reject_extra_fields(value: dict[str, Any], allowed: set[str], label: str) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        raise ValueError(f"{label} contains schema-forbidden fields: {extra}")


def _normalized_document_url(value: Any) -> str:
    """Normalize only browser-safe semantic URL differences.

    Chromium adds ``/`` to an origin-only HTTP(S) URL and may remove a default
    port.  Everything else (path, query, fragment, origin) remains part of the
    equality check so a redirect cannot silently become accepted evidence.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("browser URL must be a non-empty string")
    parsed = urlsplit(value)
    if not parsed.scheme:
        raise ValueError("browser URL must be absolute")
    scheme = parsed.scheme.lower()
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("browser URL credentials are not allowed")
    hostname = parsed.hostname
    if hostname is None:
        # Keep non-network schemes (for example file:) deterministic without
        # manufacturing an origin.
        return urlunsplit(parsed)
    host = hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = parsed.path or ("/" if scheme in {"http", "https"} else "")
    normalized = SplitResult(scheme, host, path, parsed.query, parsed.fragment)
    return urlunsplit(normalized)


def _finite_vector(value: Any, length: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == length
        and all(
            not isinstance(item, bool)
            and isinstance(item, (int, float))
            and math.isfinite(float(item))
            for item in value
        )
    )


def _directions_equal(left: Any, right: Any, tolerance: float = 1e-6) -> bool:
    return _finite_vector(left, 3) and _finite_vector(right, 3) and all(
        abs(float(a) - float(b)) <= tolerance for a, b in zip(left, right)
    )


def _camera_back_axis(matrix_world: list[Any]) -> list[float]:
    axis = [float(matrix_world[8]), float(matrix_world[9]), float(matrix_world[10])]
    length = math.sqrt(sum(item * item for item in axis))
    if length <= 1e-12:
        raise ValueError("browser receipt camera.matrixWorld has a zero back axis")
    return [item / length for item in axis]


def validate_adaptive_browser_receipt(
    manifest: dict[str, Any],
    capture: dict[str, Any],
    screenshot: Path,
    image: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Validate a Playwright-minted adaptive capture receipt fail closed.

    The generic ``record`` CLI deliberately cannot supply this object.  It is
    assembled by ``scripts/capture_threejs_playwright.py`` from live browser
    observations after the runtime camera has settled.
    """
    capture_id = str(capture.get("id", ""))
    binding = capture.get("adaptiveCritic")
    if capture.get("role") != "adaptive-critic" or not isinstance(binding, dict):
        raise ValueError(f"capture is not an adaptive critic view: {capture_id}")
    if not isinstance(receipt, dict):
        raise ValueError(f"adaptive capture requires a Playwright browser receipt: {capture_id}")
    _reject_extra_fields(
        receipt,
        {
            "kind",
            "schemaVersion",
            "adapter",
            "sessionNonce",
            "captureId",
            "runtime",
            "browser",
            "camera",
            "canvas",
            "screenshot",
        },
        "adaptive browser receipt",
    )
    if receipt.get("kind") != ADAPTIVE_BROWSER_RECEIPT_KIND:
        raise ValueError(f"adaptive capture receipt kind is invalid: {capture_id}")
    if receipt.get("schemaVersion") != ADAPTIVE_BROWSER_RECEIPT_VERSION:
        raise ValueError(f"adaptive capture receipt schemaVersion is invalid: {capture_id}")
    if receipt.get("captureId") != capture_id:
        raise ValueError(f"adaptive browser receipt captureId mismatch: {capture_id}")
    nonce = receipt.get("sessionNonce")
    if not isinstance(nonce, str) or not NONCE_PATTERN.fullmatch(nonce):
        raise ValueError(f"adaptive browser receipt sessionNonce is invalid: {capture_id}")

    adapter = receipt.get("adapter")
    if not isinstance(adapter, dict) or adapter.get("name") != "capture_threejs_playwright":
        raise ValueError(f"adaptive capture was not minted by the Playwright adapter: {capture_id}")
    _reject_extra_fields(adapter, {"name", "version"}, "adaptive browser receipt.adapter")
    if adapter.get("version") != ADAPTIVE_BROWSER_RECEIPT_VERSION:
        raise ValueError(f"adaptive capture adapter version is invalid: {capture_id}")
    browser = receipt.get("browser")
    if (
        not isinstance(browser, dict)
        or browser.get("name") != "chromium"
        or not isinstance(browser.get("version"), str)
        or not browser["version"].strip()
        or not isinstance(browser.get("headless"), bool)
    ):
        raise ValueError(f"adaptive browser identity is incomplete: {capture_id}")
    _reject_extra_fields(
        browser, {"name", "version", "headless"}, "adaptive browser receipt.browser"
    )

    runtime = receipt.get("runtime")
    manifest_runtime = manifest.get("runtime")
    if not isinstance(runtime, dict) or not isinstance(manifest_runtime, dict):
        raise ValueError(f"adaptive browser runtime snapshot is missing: {capture_id}")
    _reject_extra_fields(
        runtime,
        {
            "requestedUrl",
            "documentUrl",
            "documentSha256",
            "readySignal",
            "captureContract",
            "snapshotEcho",
            "sceneBuildSha256",
            "objectCount",
        },
        "adaptive browser receipt.runtime",
    )
    expected_url = manifest_runtime.get("url")
    if runtime.get("requestedUrl") != expected_url:
        raise ValueError(f"adaptive browser runtime URL request does not match manifest: {capture_id}")
    try:
        document_matches = _normalized_document_url(
            runtime.get("documentUrl")
        ) == _normalized_document_url(expected_url)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"adaptive browser document URL is invalid: {capture_id}") from exc
    if not document_matches:
        raise ValueError(f"adaptive browser runtime URL does not match manifest: {capture_id}")
    document_hash = runtime.get("documentSha256")
    if not isinstance(document_hash, str) or not SHA256_PATTERN.fullmatch(document_hash):
        raise ValueError(f"adaptive browser documentSha256 is invalid: {capture_id}")
    ready = runtime.get("readySignal")
    if (
        not isinstance(ready, dict)
        or ready.get("expression") != manifest_runtime.get("readySignal")
        or ready.get("value") is not True
        or ready.get("valueType") != "boolean"
    ):
        raise ValueError(f"adaptive browser ready contract was not strictly true: {capture_id}")
    _reject_extra_fields(
        ready,
        {"expression", "value", "valueType"},
        "adaptive browser receipt.runtime.readySignal",
    )
    contract = runtime.get("captureContract")
    if (
        not isinstance(contract, dict)
        or contract.get("name") != manifest_runtime.get("captureContract")
        or contract.get("setCamera") is not True
        or contract.get("getEvidenceSnapshot") is not True
    ):
        raise ValueError(f"adaptive runtime capture contract is incomplete: {capture_id}")
    _reject_extra_fields(
        contract,
        {"name", "setCamera", "getEvidenceSnapshot"},
        "adaptive browser receipt.runtime.captureContract",
    )
    snapshot_echo = runtime.get("snapshotEcho")
    if (
        not isinstance(snapshot_echo, dict)
        or snapshot_echo.get("captureId") != capture_id
        or snapshot_echo.get("sessionNonce") != nonce
    ):
        raise ValueError(f"adaptive runtime snapshot challenge was not echoed: {capture_id}")
    _reject_extra_fields(
        snapshot_echo,
        {"captureId", "sessionNonce"},
        "adaptive browser receipt.runtime.snapshotEcho",
    )
    scene_hash = runtime.get("sceneBuildSha256")
    if not isinstance(scene_hash, str) or not SHA256_PATTERN.fullmatch(scene_hash):
        raise ValueError(f"adaptive runtime sceneBuildSha256 is invalid: {capture_id}")
    object_count = runtime.get("objectCount")
    if isinstance(object_count, bool) or not isinstance(object_count, int) or object_count <= 0:
        raise ValueError(f"adaptive runtime objectCount must be positive: {capture_id}")

    canvas = receipt.get("canvas")
    if (
        not isinstance(canvas, dict)
        or canvas.get("selector") != "canvas"
        or canvas.get("webgl") is not True
    ):
        raise ValueError(f"adaptive capture is not bound to a WebGL canvas: {capture_id}")
    _reject_extra_fields(
        canvas,
        {
            "selector",
            "cssWidth",
            "cssHeight",
            "width",
            "height",
            "drawingBufferWidth",
            "drawingBufferHeight",
            "devicePixelRatio",
            "webgl",
        },
        "adaptive browser receipt.canvas",
    )
    for field in (
        "cssWidth",
        "cssHeight",
        "width",
        "height",
        "drawingBufferWidth",
        "drawingBufferHeight",
    ):
        value = canvas.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"adaptive browser canvas.{field} must be positive: {capture_id}")
    manifest_viewport = manifest_runtime.get("viewport")
    manifest_dpr = manifest_runtime.get("devicePixelRatio")
    if (
        not isinstance(manifest_viewport, list)
        or len(manifest_viewport) != 2
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in manifest_viewport
        )
        or isinstance(manifest_dpr, bool)
        or not isinstance(manifest_dpr, (int, float))
        or not math.isfinite(float(manifest_dpr))
        or not 0.25 <= float(manifest_dpr) <= 8.0
    ):
        raise ValueError(f"adaptive manifest viewport/DPR is invalid: {capture_id}")
    receipt_dpr = canvas.get("devicePixelRatio")
    if (
        isinstance(receipt_dpr, bool)
        or not isinstance(receipt_dpr, (int, float))
        or not math.isfinite(float(receipt_dpr))
        or abs(float(receipt_dpr) - float(manifest_dpr)) > 1e-9
    ):
        raise ValueError(f"adaptive browser devicePixelRatio differs from manifest: {capture_id}")
    if [canvas["cssWidth"], canvas["cssHeight"]] != manifest_viewport:
        raise ValueError(f"adaptive browser canvas CSS size differs from viewport: {capture_id}")
    expected_width = round(canvas["cssWidth"] * float(receipt_dpr))
    expected_height = round(canvas["cssHeight"] * float(receipt_dpr))
    if canvas["width"] != expected_width or canvas["height"] != expected_height:
        raise ValueError(f"adaptive browser canvas backing size differs from CSS size/DPR: {capture_id}")
    if (
        canvas["drawingBufferWidth"] != canvas["width"]
        or canvas["drawingBufferHeight"] != canvas["height"]
    ):
        raise ValueError(f"adaptive WebGL drawing buffer differs from canvas backing size: {capture_id}")

    camera = receipt.get("camera")
    if not isinstance(camera, dict):
        raise ValueError(f"adaptive browser camera snapshot is missing: {capture_id}")
    _reject_extra_fields(
        camera,
        {"direction", "matrixWorld", "projectionMatrix"},
        "adaptive browser receipt.camera",
    )
    expected_direction = binding.get("direction")
    if not _directions_equal(camera.get("direction"), expected_direction):
        raise ValueError(f"adaptive browser camera direction mismatch: {capture_id}")
    matrix_world = camera.get("matrixWorld")
    projection_matrix = camera.get("projectionMatrix")
    if not _finite_vector(matrix_world, 16) or not _finite_vector(projection_matrix, 16):
        raise ValueError(f"adaptive browser camera matrices are invalid: {capture_id}")
    if not _directions_equal(_camera_back_axis(matrix_world), expected_direction, tolerance=1e-5):
        raise ValueError(f"adaptive camera matrix does not encode planned direction: {capture_id}")

    screenshot_record = receipt.get("screenshot")
    file_hash = sha256(screenshot)
    pixel_hash = decoded_pixel_sha256(screenshot)
    if not isinstance(screenshot_record, dict):
        raise ValueError(f"adaptive browser screenshot receipt is missing: {capture_id}")
    _reject_extra_fields(
        screenshot_record,
        {"sha256", "pixelSha256", "width", "height"},
        "adaptive browser receipt.screenshot",
    )
    if screenshot_record.get("sha256") != file_hash:
        raise ValueError(f"adaptive browser screenshot hash mismatch: {capture_id}")
    if screenshot_record.get("pixelSha256") != pixel_hash:
        raise ValueError(f"adaptive browser decoded-pixel hash mismatch: {capture_id}")
    if screenshot_record.get("width") != image.get("width") or screenshot_record.get("height") != image.get("height"):
        raise ValueError(f"adaptive browser screenshot dimensions mismatch: {capture_id}")
    if (
        screenshot_record.get("width") != canvas["width"]
        or screenshot_record.get("height") != canvas["height"]
    ):
        raise ValueError(f"adaptive browser screenshot is not the full bound canvas: {capture_id}")
    return copy.deepcopy(receipt)


def validate_adaptive_capture_set(
    manifest_path_value: Path,
    manifest: dict[str, Any],
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Validate provenance and reject replay/collapse across camera directions."""
    recorded: list[dict[str, Any]] = []
    for capture in manifest.get("captures", []):
        if not isinstance(capture, dict) or capture.get("role") != "adaptive-critic":
            continue
        binding = capture.get("adaptiveCritic")
        if not isinstance(binding, dict) or (session_id is not None and binding.get("sessionId") != session_id):
            continue
        if capture.get("status") != "recorded":
            continue
        screenshot_value = capture.get("path")
        if not isinstance(screenshot_value, str) or not screenshot_value:
            raise ValueError(f"adaptive capture has no screenshot path: {capture.get('id')}")
        screenshot = manifest_path(manifest_path_value, screenshot_value)
        if not screenshot.is_file():
            raise ValueError(f"adaptive capture screenshot is missing: {screenshot}")
        image = probe(screenshot)
        receipt = capture.get("browserEvidence")
        normalized = validate_adaptive_browser_receipt(manifest, capture, screenshot, image, receipt)
        receipt_hash = canonical_sha256(normalized)
        if capture.get("browserEvidenceSha256") != receipt_hash:
            raise ValueError(f"adaptive browser receipt hash changed: {capture.get('id')}")
        pixel_hash = decoded_pixel_sha256(screenshot)
        if capture.get("pixelSha256") != pixel_hash:
            raise ValueError(f"adaptive decoded-pixel hash changed: {capture.get('id')}")
        perceptual_signature = _decoded_image_signature(screenshot)
        recorded.append(
            {
                "captureId": str(capture["id"]),
                "sessionId": str(binding.get("sessionId", "")),
                "direction": binding.get("direction"),
                "pixelSha256": pixel_hash,
                "fileSha256": sha256(screenshot),
                "cameraMatrixSha256": canonical_sha256(normalized["camera"]["matrixWorld"]),
                "sceneBuildSha256": normalized["runtime"]["sceneBuildSha256"],
                "browserEvidenceSha256": receipt_hash,
                "_imageSignature": perceptual_signature,
            }
        )
    for index, left in enumerate(recorded):
        for right in recorded[index + 1 :]:
            # A corrected scene starts a new adaptive session.  Preserve and
            # validate old evidence, but never compare its pixels, cameras, or
            # scene-build digest against a later session.
            if left["sessionId"] != right["sessionId"]:
                continue
            if _directions_equal(left["direction"], right["direction"]):
                continue
            if left["pixelSha256"] == right["pixelSha256"]:
                raise ValueError(
                    "duplicate/collapsed adaptive capture pixels across distinct directions: "
                    f"{left['captureId']} and {right['captureId']}"
                )
            collapsed, phash_distance, visible_rgb_mae = _near_identical_image_signatures(
                left["_imageSignature"], right["_imageSignature"]
            )
            if collapsed:
                raise ValueError(
                    "near-identical/collapsed adaptive capture pixels across distinct directions: "
                    f"{left['captureId']} and {right['captureId']} "
                    f"(pHash Hamming={phash_distance}, "
                    f"normalized visible-RGB MAE={visible_rgb_mae:.8f})"
                )
            if left["cameraMatrixSha256"] == right["cameraMatrixSha256"]:
                raise ValueError(
                    "duplicate adaptive camera matrix across distinct directions: "
                    f"{left['captureId']} and {right['captureId']}"
                )
    session_scene_hashes: dict[str, set[str]] = {}
    for item in recorded:
        session_scene_hashes.setdefault(item["sessionId"], set()).add(item["sceneBuildSha256"])
    inconsistent_sessions = sorted(
        key for key, hashes in session_scene_hashes.items() if len(hashes) > 1
    )
    if inconsistent_sessions:
        raise ValueError(
            "adaptive captures were recorded from different scene build digests "
            f"inside session(s): {', '.join(inconsistent_sessions)}"
        )
    scene_hashes = {item["sceneBuildSha256"] for item in recorded}
    return {
        "recordedCaptureCount": len(recorded),
        "uniquePixelCount": len({item["pixelSha256"] for item in recorded}),
        "uniqueCameraMatrixCount": len({item["cameraMatrixSha256"] for item in recorded}),
        "sceneBuildSha256": next(iter(scene_hashes), None) if len(scene_hashes) <= 1 else None,
    }


def read_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("manifest root must be an object")
    return value


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def manifest_path(manifest_path_value: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else (manifest_path_value.parent / candidate).resolve()


def portable_path(manifest_path_value: Path, value: Path) -> str:
    resolved = value.expanduser().resolve()
    try:
        return resolved.relative_to(manifest_path_value.parent.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def init_manifest(
    reference: Path | None,
    runtime_url: str,
    output: Path,
    viewport: tuple[int, int],
    device_pixel_ratio: float,
    output_dir: str,
    reference_glb: Path | None = None,
    reference_browser_url: str | None = None,
    render_profile: Path | None = None,
) -> dict[str, Any]:
    if (reference is None) == (reference_glb is None):
        raise ValueError("provide exactly one of reference image or reference GLB")

    if reference_glb is not None:
        reference_glb = reference_glb.expanduser().resolve()
        reference_probe: dict[str, Any] = probe_glb(reference_glb)
        if reference_probe.get("referenceReadiness") != "pass":
            raise ValueError(f"reference GLB is not usable: {reference_glb}")
        reference_record: dict[str, Any] = {
            "kind": "glb",
            "path": portable_path(output, reference_glb),
            "sha256": reference_probe["sha256"],
            "probe": reference_probe,
            "comparisonBasis": "browser-rendered-glb",
            "renderRequired": True,
        }
        if reference_browser_url:
            reference_record["browserUrl"] = reference_browser_url
    else:
        assert reference is not None
        reference = reference.expanduser().resolve()
        if not reference.is_file():
            raise ValueError(f"reference does not exist: {reference}")
        reference_probe = probe(reference)
        if not reference_probe.get("type") or not reference_probe.get("width"):
            raise ValueError(f"reference is not a readable image: {reference}")
        reference_record = {
            "kind": "image",
            "path": portable_path(output, reference),
            "sha256": sha256(reference),
            "image": reference_probe,
            "comparisonBasis": "source-image",
            "renderRequired": False,
        }

    captures = []
    for item in CAPTURE_PLAN:
        captures.append(
            {
                **item,
                "target": [0, 0, 0],
                "near": 0.01,
                "far": 100,
                "path": f"{output_dir.rstrip('/')}/{item['id']}.png",
                "status": "pending",
                "passes": default_pass_records(f"{output_dir.rstrip('/')}/{item['id']}"),
            }
        )
    if reference_record["kind"] == "glb":
        for item in captures:
            item["reference"] = {
                "path": f"reference/{item['id']}.png",
                "status": "pending",
                "passes": default_pass_records(f"reference/{item['id']}"),
            }
    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "createdAt": now_utc(),
        "runtime": {
            "url": runtime_url,
            "route": runtime_url.split("#", 1)[-1] if "#" in runtime_url else runtime_url,
            "viewport": list(viewport),
            "devicePixelRatio": device_pixel_ratio,
            "renderer": "WebGLRenderer",
            "threeVersion": "project-pinned",
            "readySignal": "window.__IMG2THREEJS_READY__",
            "captureContract": "window.__IMG2THREEJS_CAPTURE__",
        },
        "reference": reference_record,
        "captures": captures,
        "evidence": {
            "browser": None,
            "diagnostics": [],
            "comparisonSheet": None,
        },
    }
    if render_profile is not None:
        profile_path = render_profile.expanduser().resolve()
        if not profile_path.is_file():
            raise ValueError(f"render profile does not exist: {profile_path}")
        from validate_render_profile import validate_file  # noqa: PLC0415

        profile_validation = validate_file(profile_path)
        if not profile_validation["passed"]:
            raise ValueError(f"render profile is invalid: {profile_validation['errors']}")
        manifest["fidelityTrack"] = "glb-mediated-v2"
        manifest["renderProfile"] = {
            "path": portable_path(output, profile_path),
            "sha256": sha256(profile_path),
            "schemaVersion": "render-profile.v2",
            "sharedBy": ["glb-reference", "procedural"],
        }
    return manifest


def find_capture(manifest: dict[str, Any], capture_id: str) -> dict[str, Any]:
    for capture in manifest.get("captures", []):
        if isinstance(capture, dict) and capture.get("id") == capture_id:
            return capture
    raise ValueError(f"capture id not found: {capture_id}")


def _validate_adaptive_schedule_plan(plan: dict[str, Any]) -> None:
    """Reuse the controller's canonical state/view grammar before path creation."""
    try:
        from .adaptive_harsh_critic import _validate_state  # type: ignore[import-not-found]
    except ImportError:  # direct ``python forge/stage4_review/render_bridge.py`` execution
        from adaptive_harsh_critic import _validate_state  # type: ignore[no-redef]

    _validate_state(plan)


def _safe_relative_evidence_path(
    value: str,
    *,
    manifest_path_value: Path | None,
    label: str,
) -> str:
    """Return one canonical POSIX-relative path confined to the manifest root."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty relative path")
    if "\\" in value or "\x00" in value:
        raise ValueError(f"{label} contains a forbidden path separator or NUL")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError(f"{label} contains an unsafe relative path component")
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or bool(windows_path.root)
    ):
        raise ValueError(f"{label} must be relative to the manifest evidence root")
    canonical = posix_path.as_posix()
    if canonical != value:
        raise ValueError(f"{label} is not a canonical relative path")
    if manifest_path_value is not None:
        evidence_root = manifest_path_value.expanduser().resolve().parent
        candidate = (evidence_root / Path(*posix_path.parts)).resolve()
        try:
            candidate.relative_to(evidence_root)
        except ValueError as exc:
            raise ValueError(f"{label} escapes the manifest evidence root") from exc
    return canonical


def _validate_scheduled_evidence_paths(
    capture: dict[str, Any],
    *,
    manifest_path_value: Path | None,
    label: str,
) -> None:
    _safe_relative_evidence_path(
        capture.get("path"),
        manifest_path_value=manifest_path_value,
        label=f"{label}.path",
    )
    passes = capture.get("passes")
    if not isinstance(passes, dict):
        raise ValueError(f"{label}.passes must be an object")
    for pass_id in PASS_IDS:
        record = passes.get(pass_id)
        if not isinstance(record, dict):
            raise ValueError(f"{label}.passes.{pass_id} is required")
        _safe_relative_evidence_path(
            record.get("path"),
            manifest_path_value=manifest_path_value,
            label=f"{label}.passes.{pass_id}.path",
        )
    reference = capture.get("reference")
    if reference is not None:
        if not isinstance(reference, dict):
            raise ValueError(f"{label}.reference must be an object")
        _safe_relative_evidence_path(
            reference.get("path"),
            manifest_path_value=manifest_path_value,
            label=f"{label}.reference.path",
        )
        reference_passes = reference.get("passes")
        if not isinstance(reference_passes, dict):
            raise ValueError(f"{label}.reference.passes must be an object")
        for pass_id in PASS_IDS:
            record = reference_passes.get(pass_id)
            if not isinstance(record, dict):
                raise ValueError(f"{label}.reference.passes.{pass_id} is required")
            _safe_relative_evidence_path(
                record.get("path"),
                manifest_path_value=manifest_path_value,
                label=f"{label}.reference.passes.{pass_id}.path",
            )


def schedule_adaptive_views(
    manifest: dict[str, Any],
    plan: dict[str, Any],
    *,
    manifest_path: Path | None = None,
    output_dir: str = "captures/adaptive",
) -> dict[str, Any]:
    """Add ``adaptive_harsh_critic.py`` nextViews to the real scene manifest.

    This is the bridge between a deterministic view plan and actual Three.js
    pixels.  It does not invent screenshots: every new capture starts pending
    and must go through the same browser adapter + ``record_capture`` hash path
    as the fixed camera batch.  Re-importing an identical plan is idempotent;
    reusing a view ID with a different direction/session is rejected.
    """
    _validate_adaptive_schedule_plan(plan)
    if plan.get("status") != "needs-render":
        raise ValueError("adaptive plan status must be needs-render")
    session_id = plan.get("sessionId")
    round_index = plan.get("currentRound")
    views = plan.get("nextViews")
    if not isinstance(session_id, str) or ADAPTIVE_SESSION_PATTERN.fullmatch(session_id) is None:
        raise ValueError("adaptive plan sessionId must be ahc- plus 20 lowercase hex characters")
    if isinstance(round_index, bool) or not isinstance(round_index, int) or round_index < 0:
        raise ValueError("adaptive plan currentRound must be a non-negative integer")
    if not isinstance(views, list) or not views:
        raise ValueError("adaptive plan nextViews must be a non-empty list")
    manifest_path_value = manifest_path.expanduser().resolve() if manifest_path is not None else None
    if manifest_path_value is not None:
        plan_manifest_path = Path(str(plan["scene"]["manifestPath"])).expanduser().resolve()
        if plan_manifest_path != manifest_path_value:
            raise ValueError("adaptive plan scene.manifestPath differs from target manifest")
    base_dir_value = output_dir.rstrip("/\\")
    base_dir = _safe_relative_evidence_path(
        base_dir_value,
        manifest_path_value=manifest_path_value,
        label="adaptive output_dir",
    )
    # Work transactionally so a later collision/path failure cannot partially
    # append earlier views to an in-memory manifest used by API callers.
    working_manifest = copy.deepcopy(manifest)
    captures = working_manifest.setdefault("captures", [])
    if not isinstance(captures, list):
        raise ValueError("manifest captures must be a list")
    by_id = {
        str(item.get("id")): item
        for item in captures
        if isinstance(item, dict) and item.get("id")
    }
    scheduled: list[str] = []
    already_present: list[str] = []
    reference = working_manifest.get("reference", {})
    if not isinstance(reference, dict) or reference.get("kind") not in {"image", "glb"}:
        raise ValueError("manifest reference.kind must be image or glb")
    if reference.get("kind") != plan["scene"]["referenceKind"]:
        raise ValueError("adaptive plan referenceKind differs from target manifest")
    reference_is_glb = reference.get("kind") == "glb"

    for index, view in enumerate(views):
        if not isinstance(view, dict):
            raise ValueError(f"adaptive nextViews[{index}] must be an object")
        view_id = view.get("id")
        direction = view.get("direction")
        azimuth = view.get("azimuthDegrees")
        elevation = view.get("elevationDegrees")
        cell = view.get("cell")
        if not isinstance(view_id, str) or ADAPTIVE_VIEW_ID_PATTERN.fullmatch(view_id) is None:
            raise ValueError(f"adaptive nextViews[{index}].id is not canonical")
        if (
            not isinstance(direction, list)
            or len(direction) != 3
            or not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in direction)
        ):
            raise ValueError(f"adaptive view {view_id} direction must be a 3-vector")
        if not isinstance(azimuth, (int, float)) or isinstance(azimuth, bool):
            raise ValueError(f"adaptive view {view_id} azimuthDegrees must be numeric")
        if not isinstance(elevation, (int, float)) or isinstance(elevation, bool):
            raise ValueError(f"adaptive view {view_id} elevationDegrees must be numeric")
        if not isinstance(cell, dict):
            raise ValueError(f"adaptive view {view_id} cell is required")
        binding = {
            "sessionId": session_id,
            "round": round_index,
            "direction": direction,
            "angularRadiusDegrees": view.get("angularRadiusDegrees"),
            "cell": cell,
            "parentId": view.get("parentId"),
        }
        if view_id in by_id:
            existing = by_id[view_id]
            if (
                existing.get("role") != "adaptive-critic"
                or existing.get("azimuthDegrees") != azimuth
                or existing.get("elevationDegrees") != elevation
                or existing.get("adaptiveCritic") != binding
            ):
                raise ValueError(f"capture id collision with different adaptive view: {view_id}")
            _validate_scheduled_evidence_paths(
                existing,
                manifest_path_value=manifest_path_value,
                label=f"adaptive capture {view_id}",
            )
            already_present.append(view_id)
            continue

        # Every adaptive run receives a random session ID.  Keep both capture
        # IDs and filesystem paths session-scoped so stale evidence from an
        # earlier run cannot satisfy a fresh plan by name.
        if not view_id.startswith(f"{session_id}-harsh-"):
            raise ValueError(
                f"adaptive view id is not scoped to session {session_id}: {view_id}"
            )
        path_prefix = f"{base_dir}/{session_id}/round-{round_index:02d}/{view_id}"
        capture: dict[str, Any] = {
            "id": view_id,
            "role": "adaptive-critic",
            "azimuthDegrees": azimuth,
            "elevationDegrees": elevation,
            "target": [0, 0, 0],
            "near": 0.01,
            "far": 100,
            "path": f"{path_prefix}.png",
            "status": "pending",
            "passes": default_pass_records(path_prefix),
            "adaptiveCritic": binding,
        }
        if reference_is_glb:
            reference_prefix = (
                f"reference/adaptive/{session_id}/round-{round_index:02d}/{view_id}"
            )
            capture["reference"] = {
                "path": f"{reference_prefix}.png",
                "status": "pending",
                "passes": default_pass_records(reference_prefix),
            }
        _validate_scheduled_evidence_paths(
            capture,
            manifest_path_value=manifest_path_value,
            label=f"adaptive capture {view_id}",
        )
        captures.append(capture)
        by_id[view_id] = capture
        scheduled.append(view_id)

    evidence = working_manifest.setdefault("evidence", {})
    if not isinstance(evidence, dict):
        raise ValueError("manifest evidence must be an object")
    plans = evidence.setdefault("adaptiveCriticPlans", [])
    if not isinstance(plans, list):
        raise ValueError("manifest evidence.adaptiveCriticPlans must be a list")
    plan_record = next(
        (
            item
            for item in plans
            if isinstance(item, dict)
            and item.get("sessionId") == session_id
            and item.get("round") == round_index
        ),
        None,
    )
    expected_view_ids = [str(view["id"]) for view in views]
    if plan_record is None:
        plans.append(
            {
                "sessionId": session_id,
                "round": round_index,
                "viewIds": expected_view_ids,
            }
        )
    elif plan_record.get("viewIds") != expected_view_ids:
        raise ValueError("adaptive plan round was already registered with different view IDs")
    manifest.clear()
    manifest.update(working_manifest)
    return {
        "sessionId": session_id,
        "round": round_index,
        "scheduled": scheduled,
        "alreadyPresent": already_present,
        "captureCount": len(captures),
    }


def record_capture(
    manifest_path_value: Path,
    manifest: dict[str, Any],
    capture_id: str,
    screenshot: Path,
    ready_signal: Any = True,
    console_errors: list[str] | None = None,
    browser_snapshot: dict[str, Any] | None = None,
    browser_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    screenshot = screenshot.expanduser().resolve()
    if not screenshot.is_file():
        raise ValueError(f"screenshot does not exist: {screenshot}")
    image = probe(screenshot)
    if not image.get("type") or not image.get("width") or not image.get("height"):
        raise ValueError(f"screenshot is not readable: {screenshot}")
    capture = find_capture(manifest, capture_id)
    errors = list(console_errors or [])
    normalized_receipt: dict[str, Any] | None = None
    pixel_hash: str | None = None
    if capture.get("role") == "adaptive-critic":
        if ready_signal is not True:
            raise ValueError(
                f"adaptive ready signal must be the strict browser boolean true: {capture_id}"
            )
        if errors:
            raise ValueError(f"adaptive browser capture has console/page errors: {capture_id}")
        if browser_receipt is None:
            raise ValueError(
                "adaptive captures cannot be registered from an arbitrary PNG; "
                "use scripts/capture_threejs_playwright.py"
            )
        normalized_receipt = validate_adaptive_browser_receipt(
            manifest,
            capture,
            screenshot,
            image,
            browser_receipt,
        )
        pixel_hash = decoded_pixel_sha256(screenshot)
        perceptual_signature = _decoded_image_signature(screenshot)
        binding = capture.get("adaptiveCritic", {})
        direction = binding.get("direction") if isinstance(binding, dict) else None
        matrix_hash = canonical_sha256(normalized_receipt["camera"]["matrixWorld"])
        scene_hash = normalized_receipt["runtime"]["sceneBuildSha256"]
        for existing in manifest.get("captures", []):
            if (
                not isinstance(existing, dict)
                or existing is capture
                or existing.get("role") != "adaptive-critic"
                or existing.get("status") != "recorded"
            ):
                continue
            existing_binding = existing.get("adaptiveCritic")
            if (
                not isinstance(existing_binding, dict)
                or not isinstance(binding, dict)
                or existing_binding.get("sessionId") != binding.get("sessionId")
                or _directions_equal(existing_binding.get("direction"), direction)
            ):
                continue
            if existing.get("pixelSha256") == pixel_hash:
                raise ValueError(
                    "duplicate/collapsed adaptive capture pixels across distinct directions: "
                    f"{existing.get('id')} and {capture_id}"
                )
            existing_path_value = existing.get("path")
            if not isinstance(existing_path_value, str) or not existing_path_value:
                raise ValueError(f"recorded adaptive capture has no path: {existing.get('id')}")
            existing_path = manifest_path(manifest_path_value, existing_path_value)
            if not existing_path.is_file():
                raise ValueError(f"recorded adaptive capture screenshot is missing: {existing_path}")
            collapsed, phash_distance, visible_rgb_mae = _near_identical_image_signatures(
                _decoded_image_signature(existing_path), perceptual_signature
            )
            if collapsed:
                raise ValueError(
                    "near-identical/collapsed adaptive capture pixels across distinct directions: "
                    f"{existing.get('id')} and {capture_id} "
                    f"(pHash Hamming={phash_distance}, "
                    f"normalized visible-RGB MAE={visible_rgb_mae:.8f})"
                )
            existing_receipt = existing.get("browserEvidence")
            if isinstance(existing_receipt, dict):
                if canonical_sha256(existing_receipt.get("camera", {}).get("matrixWorld")) == matrix_hash:
                    raise ValueError(
                        "duplicate adaptive camera matrix across distinct directions: "
                        f"{existing.get('id')} and {capture_id}"
                    )
                if existing_receipt.get("runtime", {}).get("sceneBuildSha256") != scene_hash:
                    raise ValueError(
                        "adaptive captures were recorded from different scene build digests"
                    )
    capture["path"] = portable_path(manifest_path_value, screenshot)
    capture["status"] = "recorded"
    capture["recordedAt"] = now_utc()
    capture["readySignal"] = ready_signal
    capture["screenshotSha256"] = sha256(screenshot)
    capture["image"] = image
    capture["consoleErrors"] = errors
    if normalized_receipt is not None and pixel_hash is not None:
        capture["pixelSha256"] = pixel_hash
        capture["browserEvidence"] = normalized_receipt
        capture["browserEvidenceSha256"] = canonical_sha256(normalized_receipt)
    if browser_snapshot is not None:
        capture["browserSnapshot"] = browser_snapshot
    return capture


def record_reference_capture(
    manifest_path_value: Path,
    manifest: dict[str, Any],
    capture_id: str,
    screenshot: Path,
    ready_signal: Any = True,
    console_errors: list[str] | None = None,
) -> dict[str, Any]:
    """Record a browser screenshot of the GLB reference baseline.

    The GLB itself is never treated as pixel evidence. The reference must first
    be loaded by the same Three.js route (or an explicit reference mode of it).
    """
    reference = manifest.get("reference")
    if not isinstance(reference, dict) or reference.get("kind") != "glb":
        raise ValueError("reference captures are only valid for a GLB reference manifest")
    screenshot = screenshot.expanduser().resolve()
    if not screenshot.is_file():
        raise ValueError(f"screenshot does not exist: {screenshot}")
    image = probe(screenshot)
    if not image.get("type") or not image.get("width") or not image.get("height"):
        raise ValueError(f"screenshot is not readable: {screenshot}")
    capture = find_capture(manifest, capture_id)
    record = capture.setdefault("reference", {})
    record.update(
        {
            "path": portable_path(manifest_path_value, screenshot),
            "status": "recorded",
            "recordedAt": now_utc(),
            "readySignal": ready_signal,
            "screenshotSha256": sha256(screenshot),
            "image": image,
            "consoleErrors": list(console_errors or []),
        }
    )
    return record


def record_capture_pass(
    manifest_path_value: Path,
    manifest: dict[str, Any],
    capture_id: str,
    pass_id: str,
    image_path: Path,
    *,
    reference: bool = False,
) -> dict[str, Any]:
    if pass_id not in PASS_IDS:
        raise ValueError(f"unknown render pass: {pass_id}")
    capture = find_capture(manifest, capture_id)
    target: dict[str, Any]
    if reference:
        target = capture.setdefault("reference", {})
    else:
        target = capture
    records = target.setdefault("passes", {})
    record = records.setdefault(pass_id, {})
    result = record_pass(manifest_path_value, record, image_path, probe)
    result["recordedAt"] = now_utc()
    return result


def validate_manifest(path: Path, require_complete: bool = False) -> dict[str, Any]:
    manifest = read_manifest(path)
    errors: list[str] = []
    warnings: list[str] = []
    if manifest.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1")
    v2 = manifest.get("fidelityTrack") == "glb-mediated-v2"
    if v2:
        profile = manifest.get("renderProfile")
        if not isinstance(profile, dict) or not profile.get("path"):
            errors.append("GLB-mediated-v2 manifest requires renderProfile.path")
        else:
            profile_path = manifest_path(path, str(profile["path"]))
            if not profile_path.is_file():
                errors.append(f"render profile is missing: {profile_path}")
            else:
                if profile.get("sha256") and profile["sha256"] != sha256(profile_path):
                    errors.append(f"render profile hash changed: {profile_path}")
                from validate_render_profile import validate_file  # noqa: PLC0415

                profile_result = validate_file(profile_path)
                errors.extend(f"render profile: {error}" for error in profile_result["errors"])
                warnings.extend(f"render profile: {warning}" for warning in profile_result["warnings"])
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict) or not runtime.get("url"):
        errors.append("runtime.url is required")
    reference = manifest.get("reference")
    if not isinstance(reference, dict) or not reference.get("path") or reference.get("kind") not in {"image", "glb"}:
        errors.append("reference.path is required")
    else:
        reference_path = manifest_path(path, str(reference["path"]))
        if not reference_path.is_file():
            errors.append(f"reference file is missing: {reference_path}")
        elif reference.get("sha256") and reference["sha256"] != sha256(reference_path):
            errors.append(f"reference hash changed: {reference_path}")
        if reference.get("kind") == "glb":
            try:
                current_probe = probe_glb(reference_path)
                if current_probe.get("referenceReadiness") != "pass":
                    errors.append("reference GLB is no longer usable")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"reference GLB probe failed: {exc}")

    captures = manifest.get("captures")
    if not isinstance(captures, list) or not captures:
        errors.append("captures must be a non-empty list")
        captures = []
    ids: set[str] = set()
    recorded = 0
    for capture in captures:
        if not isinstance(capture, dict) or not capture.get("id"):
            errors.append("every capture needs an id")
            continue
        capture_id = str(capture["id"])
        if capture_id in ids:
            errors.append(f"duplicate capture id: {capture_id}")
        ids.add(capture_id)
        if reference.get("kind") == "glb":
            baseline = capture.get("reference")
            if not isinstance(baseline, dict) or baseline.get("status") != "recorded":
                if require_complete:
                    errors.append(f"GLB reference baseline is not recorded: {capture_id}")
            elif baseline.get("path"):
                baseline_path = manifest_path(path, str(baseline["path"]))
                if not baseline_path.is_file():
                    errors.append(f"GLB reference baseline is missing: {baseline_path}")
                elif baseline.get("screenshotSha256") and baseline["screenshotSha256"] != sha256(baseline_path):
                    errors.append(f"GLB reference baseline hash changed: {baseline_path}")
                if baseline.get("consoleErrors"):
                    errors.append(f"browser console errors recorded for GLB baseline: {capture_id}")
                errors.extend(
                    validate_pass_records(
                        path,
                        baseline.get("passes"),
                        probe,
                        require_complete=require_complete and v2,
                        label=f"GLB reference {capture_id}",
                    )
                )
        if capture.get("status") != "recorded":
            if require_complete:
                errors.append(f"capture is not recorded: {capture_id}")
            continue
        recorded += 1
        screenshot_value = capture.get("path")
        if not screenshot_value:
            errors.append(f"recorded capture has no path: {capture_id}")
            continue
        screenshot = manifest_path(path, str(screenshot_value))
        if not screenshot.is_file():
            errors.append(f"screenshot file is missing: {screenshot}")
            continue
        image = probe(screenshot)
        if not image.get("type") or not image.get("width"):
            errors.append(f"screenshot is unreadable: {screenshot}")
        if capture.get("screenshotSha256") and capture["screenshotSha256"] != sha256(screenshot):
            errors.append(f"screenshot hash changed: {screenshot}")
        if capture.get("consoleErrors"):
            errors.append(f"browser console errors recorded for: {capture_id}")
        if capture.get("role") == "adaptive-critic":
            try:
                validate_adaptive_browser_receipt(
                    manifest,
                    capture,
                    screenshot,
                    image,
                    capture.get("browserEvidence"),
                )
                if capture.get("browserEvidenceSha256") != canonical_sha256(capture["browserEvidence"]):
                    errors.append(f"adaptive browser receipt hash changed: {capture_id}")
                if capture.get("pixelSha256") != decoded_pixel_sha256(screenshot):
                    errors.append(f"adaptive decoded-pixel hash changed: {capture_id}")
            except Exception as exc:  # noqa: BLE001 - validation aggregates all failures
                errors.append(f"adaptive browser provenance invalid for {capture_id}: {exc}")
        errors.extend(
            validate_pass_records(
                path,
                capture.get("passes"),
                probe,
                require_complete=require_complete and v2,
                label=f"procedural {capture_id}",
            )
        )
    if recorded == 0:
        warnings.append("no browser screenshots recorded yet")
    try:
        validate_adaptive_capture_set(path, manifest)
    except Exception as exc:  # noqa: BLE001 - validation aggregates all failures
        errors.append(f"adaptive capture set invalid: {exc}")
    result = {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "recordedCaptures": recorded,
        "fidelityTrack": manifest.get("fidelityTrack", "legacy"),
        "requiredPasses": list(PASS_IDS) if v2 else [],
    }
    return result


def diagnose(path: Path, output: Path) -> dict[str, Any]:
    manifest = read_manifest(path)
    hero = find_capture(manifest, "hero")
    reference_record = manifest.get("reference", {})
    reference_path = manifest_path(path, str(reference_record["path"]))
    hero_path = manifest_path(path, str(hero["path"]))
    if hero.get("status") != "recorded":
        raise ValueError("hero capture must be recorded before diagnostics")

    review_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(review_dir))
    from diagnose_render import run_tier1  # noqa: PLC0415
    from diagnose_render_multi_angle import analyze_angles  # noqa: PLC0415

    orbit_paths = []
    for capture in manifest.get("captures", []):
        if isinstance(capture, dict) and capture.get("role") == "orbit" and capture.get("status") == "recorded":
            orbit_paths.append(manifest_path(path, str(capture["path"])))
    if reference_record.get("kind") == "glb":
        hero_reference = hero.get("reference")
        if not isinstance(hero_reference, dict) or hero_reference.get("status") != "recorded":
            raise ValueError("GLB reference hero baseline must be recorded before diagnostics")
        comparison_reference_path = manifest_path(path, str(hero_reference["path"]))
    else:
        comparison_reference_path = reference_path
    result = {
        "manifest": str(path.resolve()),
        "reference": str(reference_path),
        "comparisonReference": str(comparison_reference_path),
        "comparisonBasis": reference_record.get("comparisonBasis"),
        "hero": run_tier1(comparison_reference_path, hero_path),
        "multiAngle": analyze_angles(hero_path, orbit_paths),
        "generatedAt": now_utc(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    evidence = manifest.setdefault("evidence", {})
    evidence.setdefault("diagnostics", []).append(portable_path(path, output))
    write_manifest(path, manifest)
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create a deterministic character camera-batch manifest")
    reference_group = init.add_mutually_exclusive_group(required=True)
    reference_group.add_argument("--reference", type=Path, help="source image reference")
    reference_group.add_argument("--reference-glb", type=Path, help="GLB reference mesh; it must be browser-rendered before comparison")
    init.add_argument("--runtime-url", required=True)
    init.add_argument("--out", type=Path, required=True)
    init.add_argument("--viewport", default="620x1000", help="CSS viewport, e.g. 620x1000")
    init.add_argument("--device-pixel-ratio", type=float, default=1.0)
    init.add_argument("--output-dir", default="captures")
    init.add_argument("--reference-browser-url", help="browser-visible URL/path for the GLB reference mode")
    init.add_argument("--render-profile", type=Path, help="validated render-profile.v2 shared by GLB and procedural routes")

    record = commands.add_parser(
        "record",
        help=(
            "record a legacy/fixed browser screenshot; adaptive-critic captures are "
            "accepted only from scripts/capture_threejs_playwright.py"
        ),
    )
    record.add_argument("--manifest", type=Path, required=True)
    record.add_argument("--capture-id", required=True)
    record.add_argument("--screenshot", type=Path, required=True)
    record.add_argument("--ready-signal", default=True)
    record.add_argument("--console-error", action="append", default=[])

    record_reference = commands.add_parser("record-reference", help="record a browser-rendered GLB baseline screenshot")
    record_reference.add_argument("--manifest", type=Path, required=True)
    record_reference.add_argument("--capture-id", required=True)
    record_reference.add_argument("--screenshot", type=Path, required=True)
    record_reference.add_argument("--ready-signal", default=True)
    record_reference.add_argument("--console-error", action="append", default=[])

    record_pass_parser = commands.add_parser("record-pass", help="record one browser-produced diagnostic pass")
    record_pass_parser.add_argument("--manifest", type=Path, required=True)
    record_pass_parser.add_argument("--capture-id", required=True)
    record_pass_parser.add_argument("--pass-id", choices=PASS_IDS, required=True)
    record_pass_parser.add_argument("--image", type=Path, required=True)
    record_pass_parser.add_argument("--reference", action="store_true", help="record the GLB baseline pass")

    validate = commands.add_parser("validate", help="validate files and hashes in a manifest")
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--require-complete", action="store_true")

    diagnostic = commands.add_parser("diagnose", help="run Tier-1 and multi-angle diagnostics")
    diagnostic.add_argument("--manifest", type=Path, required=True)
    diagnostic.add_argument("--out", type=Path, required=True)

    adaptive = commands.add_parser(
        "schedule-adaptive",
        help="append adaptive harsh-critic nextViews as pending real-scene captures",
    )
    adaptive.add_argument("--manifest", type=Path, required=True)
    adaptive.add_argument("--plan", type=Path, required=True)
    adaptive.add_argument("--output-dir", default="captures/adaptive")

    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            try:
                width_text, height_text = args.viewport.lower().split("x", 1)
                viewport = (int(width_text), int(height_text))
            except ValueError as exc:
                raise ValueError("--viewport must look like WIDTHxHEIGHT") from exc
            output = args.out.expanduser().resolve()
            write_manifest(
                output,
                init_manifest(
                    args.reference,
                    args.runtime_url,
                    output,
                    viewport,
                    args.device_pixel_ratio,
                    args.output_dir,
                    reference_glb=args.reference_glb,
                    reference_browser_url=args.reference_browser_url,
                    render_profile=args.render_profile,
                ),
            )
            print(json.dumps({"manifest": str(output), "captures": len(CAPTURE_PLAN)}, indent=2))
            return 0
        manifest_path_value = args.manifest.expanduser().resolve()
        manifest = read_manifest(manifest_path_value)
        if args.command == "record":
            record_capture(manifest_path_value, manifest, args.capture_id, Path(args.screenshot), args.ready_signal, args.console_error)
            write_manifest(manifest_path_value, manifest)
            print(json.dumps(find_capture(manifest, args.capture_id), indent=2, ensure_ascii=False))
            return 0
        if args.command == "record-reference":
            record_reference_capture(manifest_path_value, manifest, args.capture_id, Path(args.screenshot), args.ready_signal, args.console_error)
            write_manifest(manifest_path_value, manifest)
            print(json.dumps(find_capture(manifest, args.capture_id).get("reference"), indent=2, ensure_ascii=False))
            return 0
        if args.command == "record-pass":
            result = record_capture_pass(
                manifest_path_value,
                manifest,
                args.capture_id,
                args.pass_id,
                args.image,
                reference=args.reference,
            )
            write_manifest(manifest_path_value, manifest)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.command == "validate":
            result = validate_manifest(manifest_path_value, args.require_complete)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result["passed"] else 1
        if args.command == "diagnose":
            result = diagnose(manifest_path_value, args.out.expanduser().resolve())
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result["hero"]["passed"] and not result["multiAngle"]["degenerate"] else 1
        if args.command == "schedule-adaptive":
            plan = read_manifest(args.plan.expanduser().resolve())
            result = schedule_adaptive_views(
                manifest,
                plan,
                manifest_path=manifest_path_value,
                output_dir=args.output_dir,
            )
            write_manifest(manifest_path_value, manifest)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        raise ValueError(f"unknown command: {args.command}")
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
